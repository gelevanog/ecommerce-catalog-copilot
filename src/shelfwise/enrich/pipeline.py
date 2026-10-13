"""One product through the copilot: photo -> attributes -> cross-check -> copy -> validation -> translations.

Model calls per product: one vision call, one copy call (plus at most `copy_retries` corrections when validation
fails) and one translation call for all languages (plus one per language that came back missing). Everything a
model returns is validated in code; the record ends as `ready` (no errors, no conflicts: can be bulk-approved),
`needs_review` (a person must look) or `blocked` (the feed itself is broken, e.g. a variant price 10x the others).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from shelfwise.catalog.ingest import display_title
from shelfwise.catalog.models import CallStats, ImageAttributes, Issue, Listing, ProductRecord
from shelfwise.config import Settings, StoreConfig
from shelfwise.enrich import copy as copy_step
from shelfwise.enrich import translate as translate_step
from shelfwise.enrich.crosscheck import decide
from shelfwise.enrich.vision import extract_image_attributes
from shelfwise.llm.base import ChatModel, Completion, LLMError
from shelfwise.llm.jsonparse import JSONReplyError
from shelfwise.taxonomy import candidates as category_candidates
from shelfwise.validate.listing import check_claims, check_structure, evidence_for, review_issues, validate_listing

log = logging.getLogger(__name__)

BLOCKING_CODES = {"variant_inconsistent", "variant_price_outlier", "variant_duplicate_option"}


@dataclass
class Models:
    vision: ChatModel
    text: ChatModel
    translate: ChatModel | None = None
    """Defaults to the text model (a separate instance only so the ledger tags translation calls)."""


def _track(stats: CallStats, completion: Completion | None, started: float) -> None:
    stats.seconds = round(stats.seconds + (time.monotonic() - started), 2)
    if completion is None:
        return
    stats.calls += 1
    stats.cached += int(completion.cached)
    stats.input_tokens += completion.input_tokens
    stats.output_tokens += completion.output_tokens
    if completion.model not in stats.models:
        stats.models.append(completion.model)


def _copy_errors(listing: Listing, record: ProductRecord, store: StoreConfig) -> list[Issue]:
    p = record.product
    issues = check_structure(listing, store, vendor=p.vendor, image_count=max(1, len(p.images)))
    claim_issues, _ = check_claims(listing, evidence_for(record), store)
    return [i for i in issues + claim_issues if i.severity == "error"]


def enrich(
    record: ProductRecord,
    *,
    store: StoreConfig,
    settings: Settings,
    models: Models,
    images_dir: Path,
    translate: bool = True,
    languages: list[str] | None = None,
) -> ProductRecord:
    p = record.product
    stats = CallStats()
    record.error = ""
    record.copy_attempts = []
    # 1. the photo, seen blind
    attrs: list[ImageAttributes] = []
    for name in p.images[:1]:
        path = images_dir / name
        started = time.monotonic()
        if not path.exists():
            attrs.append(ImageAttributes(image=name, model=models.vision.label, error="image file not found"))
            continue
        result, completion = extract_image_attributes(models.vision, path, max_tokens=settings.llm_max_tokens)
        _track(stats, completion, started)
        attrs.append(result)
    record.image_attributes = attrs
    # 2. feed vs photo
    record.decisions = decide(p, attrs[0] if attrs else None)
    # 3. copy + category, with one corrective round
    name = display_title(p)
    noun = attrs[0].product_noun if attrs and not attrs[0].error else ""
    cands = category_candidates(f"{name} {p.supplier_category} {noun}")
    variants = [" / ".join(v.options.values()) for v in p.variants if v.options]
    payload = copy_step.user_payload(
        name=name, vendor=p.vendor, facts=copy_step.facts_for_prompt(p.facts, record.decisions), variants=variants,
        candidates=cands, product_types=store.product_types, image_count=max(1, len(p.images)),
        supplier_category=p.supplier_category,
    )
    base = copy_step.messages(store, payload)
    msgs = base
    best: tuple[int, Listing] | None = None
    for attempt in range(settings.copy_retries + 1):
        started = time.monotonic()
        try:
            completion = copy_step.write_listing(models.text, msgs, max_tokens=settings.llm_max_tokens)
        except LLMError as exc:
            _track(stats, None, started)
            record.error = f"copy: {exc}"[:300]
            break
        _track(stats, completion, started)
        try:
            listing = copy_step.parse_listing(completion.text, cands)
        except JSONReplyError as exc:
            record.error = f"copy: {exc}"[:300]
            msgs = [*base, {"role": "assistant", "content": completion.text[:2000]},
                    {"role": "user", "content": "That was not a JSON object. Return only the JSON listing."}]
            continue
        record.error = ""
        errors = _copy_errors(listing, record, store)
        record.copy_attempts.append([f"{i.code}:{i.field}" for i in errors])
        if best is None or len(errors) < best[0]:
            best = (len(errors), listing)
        if not errors:
            break
        msgs = copy_step.correction_messages(base, completion.text, errors)
    if best is None:
        record.stats = stats
        record.status = "needs_review"
        record.issues = [*p.issues, Issue(code="copy_failed", severity="error", message=record.error or "no listing")]
        return record
    listing = best[1]
    # 4. translations
    langs = languages if languages is not None else store.languages
    if translate and langs:
        started = time.monotonic()
        try:
            translations, completion = translate_step.translate(models.translate or models.text, store, listing, langs, max_tokens=settings.llm_max_tokens)
            _track(stats, completion, started)
            for lang in [x for x in langs if x not in translations]:
                started = time.monotonic()
                extra, completion = translate_step.translate(models.translate or models.text, store, listing, [lang], max_tokens=settings.llm_max_tokens)
                _track(stats, completion, started)
                translations.update(extra)
            record.translation_attempts = 1 + len([x for x in langs if x not in translations])
            too_long = translate_step.length_problems(store, translations)
            if too_long:
                started = time.monotonic()
                try:
                    fixed, completion = translate_step.shorten(models.translate or models.text, store, listing, translations, too_long,
                                                               max_tokens=settings.llm_max_tokens)
                    _track(stats, completion, started)
                    translations.update({k: v for k, v in fixed.items() if v.title and v.body_html})
                except (LLMError, JSONReplyError) as exc:
                    log.warning("shortening translations failed for %s: %s", p.id, exc)
                record.translation_attempts += 1
            listing.translations = translations
        except (LLMError, JSONReplyError) as exc:
            _track(stats, None, started)
            log.warning("translation failed for %s: %s", p.id, exc)
            record.error = f"translation: {exc}"[:300]
    record.generated = listing.model_copy(deep=True)
    record.listing = listing
    record.stats = stats
    revalidate(record, store, languages=langs if translate else [])
    return record


def revalidate(record: ProductRecord, store: StoreConfig, *, languages: list[str] | None = None) -> ProductRecord:
    """Recompute issues, provenance and status (after enrichment and after every edit)."""
    p = record.product
    waived = {(i.code, i.field, i.span): i.waived for i in record.issues if i.waived}
    issues: list[Issue] = list(p.issues)
    issues += review_issues(record)
    if record.listing is not None:
        check_store = store
        if languages is not None:
            check_store = _with_languages(store, languages)
        listing_issues, provenance = validate_listing(
            record.listing, check_store, facts=p.facts, decisions=record.decisions, images=record.image_attributes,
            vendor=p.vendor, name=display_title(p), image_count=max(1, len(p.images)),
        )
        issues += listing_issues
        record.provenance = provenance
    if record.error:
        issues.append(Issue(code="model_error", severity="warning", message=record.error))
    for issue in issues:
        issue.waived = waived.get((issue.code, issue.field, issue.span), "")
    record.issues = issues
    open_errors = [i for i in issues if i.severity == "error" and not i.waived]
    if record.listing is None:
        record.status = "imported" if not open_errors else "blocked"
    elif record.status in {"approved", "exported"} and not open_errors:
        pass
    elif any(i.code in BLOCKING_CODES for i in open_errors):
        record.status = "blocked"
    elif open_errors or any(d.status == "conflict" for d in record.decisions):
        record.status = "needs_review"
    else:
        record.status = "ready"
    return record


def _with_languages(store: StoreConfig, languages: list[str]) -> StoreConfig:
    from dataclasses import replace

    return replace(store, languages=list(languages))
