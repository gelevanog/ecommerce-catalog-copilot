"""Validation of one listing (lengths, HTML, store rules, claims, translations) and of the catalog as a whole
(duplicate titles and handles, near-duplicate descriptions)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from shelfwise.catalog.models import AttributeDecision, Fact, ImageAttributes, Issue, Listing, ProductRecord
from shelfwise.config import StoreConfig
from shelfwise.taxonomy import get as get_category
from shelfwise.validate.claims import EvidenceSet, build_evidence, check_text, plain
from shelfwise.validate.translation import check_translation

ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "b", "i", "br"}
_TAG = re.compile(r"</?\s*([a-zA-Z0-9]+)[^>]*>")
_SIZE_IN_TITLE = re.compile(r"\b(?:XXS|XS|XL|XXL|S/M|L/XL|US \d{1,2}|size \w+)\b")


def _length(issues: list[Issue], field: str, text: str, lo: int, hi: int, unit: str = "characters") -> None:
    n = len(text) if unit == "characters" else len(text.split())
    if n > hi:
        issues.append(Issue(code="too_long", severity="error", field=field, message=f"{n} {unit}, the limit is {hi}"))
    elif n < lo:
        issues.append(Issue(code="too_short", severity="error" if n == 0 else "warning", field=field,
                            message=f"{n} {unit}, at least {lo} expected"))


def check_structure(listing: Listing, store: StoreConfig, *, vendor: str | None, image_count: int) -> list[Issue]:
    issues: list[Issue] = []
    _length(issues, "title", listing.title, store.title.min, store.title.max)
    _length(issues, "meta_title", listing.meta_title, store.meta_title.min, store.meta_title.max)
    _length(issues, "meta_description", listing.meta_description, store.meta_description.min, store.meta_description.max)
    _length(issues, "body_html", plain(listing.body_html), store.body_words.min, store.body_words.max, unit="words")
    n = len(listing.bullets)
    if not store.bullets_count.min <= n <= store.bullets_count.max:
        issues.append(Issue(code="bullet_count", severity="error", field="bullets",
                            message=f"{n} bullets, {store.bullets_count.min} to {store.bullets_count.max} expected"))
    for i, bullet in enumerate(listing.bullets):
        if len(bullet) > store.bullet_max_chars:
            issues.append(Issue(code="too_long", severity="error", field=f"bullets[{i}]",
                                message=f"{len(bullet)} characters, the limit is {store.bullet_max_chars}"))
    if len(listing.alt_texts) < image_count:
        issues.append(Issue(code="alt_text_missing", severity="error", field="alt_texts", message="An image has no alt text"))
    for i, alt in enumerate(listing.alt_texts):
        if len(alt) > store.alt_text_max:
            issues.append(Issue(code="too_long", severity="error", field=f"alt_texts[{i}]",
                                message=f"{len(alt)} characters, the limit is {store.alt_text_max}"))
        if re.match(r"\s*(image|picture|photo) of\b", alt, re.IGNORECASE):
            issues.append(Issue(code="alt_text_style", severity="warning", field=f"alt_texts[{i}]",
                                message="Alt text should not start with “image of”"))
    if not store.tags_count.min <= len(listing.tags) <= store.tags_count.max:
        issues.append(Issue(code="tag_count", severity="warning", field="tags",
                            message=f"{len(listing.tags)} tags, {store.tags_count.min} to {store.tags_count.max} expected"))
    for tag in _TAG.findall(listing.body_html):
        if tag.lower() not in ALLOWED_TAGS:
            issues.append(Issue(code="html_tag", severity="error", field="body_html", span=tag,
                                message=f"HTML tag <{tag}> is not allowed in a product description"))
            break
    if re.search(r"<script|javascript:|on\w+\s*=", listing.body_html, re.IGNORECASE):
        issues.append(Issue(code="html_unsafe", severity="error", field="body_html", message="Script or event handler in HTML"))
    if listing.product_type not in store.product_types:
        issues.append(Issue(code="product_type", severity="error", field="product_type",
                            message=f"“{listing.product_type}” is not one of the store's product types"))
    if get_category(listing.category_id) is None:
        issues.append(Issue(code="category_invalid", severity="error", field="category_id",
                            message=f"Category id {listing.category_id} is not in the taxonomy"))
    if vendor and not listing.title.lower().startswith(vendor.lower()):
        issues.append(Issue(code="title_pattern", severity="warning", field="title",
                            message=f"Style guide: titles start with the brand ({vendor})"))
    if _SIZE_IN_TITLE.search(listing.title):
        issues.append(Issue(code="title_size", severity="warning", field="title", message="Sizes belong in variants, not the title"))
    if "!" in listing.title + listing.body_html + " ".join(listing.bullets):
        issues.append(Issue(code="style_exclamation", severity="warning", field="body_html", message="Style guide: no exclamation marks"))
    return issues


def evidence_for(record: ProductRecord) -> EvidenceSet:
    from shelfwise.catalog.ingest import display_title

    p = record.product
    name = f"{p.vendor or ''} {display_title(p)}"
    return build_evidence(p.facts, record.decisions, record.image_attributes, name)


def check_claims(listing: Listing, ev: EvidenceSet, store: StoreConfig) -> tuple[list[Issue], dict[str, list[str]]]:
    issues: list[Issue] = []
    provenance: dict[str, list[str]] = {}
    for field, text in listing.text_fields():
        result = check_text(field, text, ev, store)
        issues += result.issues
        provenance[field] = result.facts
    return issues, provenance


def validate_listing(
    listing: Listing,
    store: StoreConfig,
    *,
    facts: list[Fact],
    decisions: list[AttributeDecision],
    images: list[ImageAttributes],
    vendor: str | None,
    name: str = "",
    image_count: int = 1,
) -> tuple[list[Issue], dict[str, list[str]]]:
    ev = build_evidence(facts, decisions, images, f"{vendor or ''} {name}")
    issues = check_structure(listing, store, vendor=vendor, image_count=image_count)
    claim_issues, provenance = check_claims(listing, ev, store)
    issues += claim_issues
    for lang in store.languages:
        translated = listing.translations.get(lang)
        if translated is None:
            issues.append(Issue(code="translation_missing", severity="warning", field=f"translations.{lang}",
                                message=f"No {lang.upper()} translation"))
            continue
        issues += check_translation(listing, translated, lang, store)
    return issues, provenance


def review_issues(record: ProductRecord) -> list[Issue]:
    """Issues from the attribute cross-check that a person must resolve."""
    out = []
    for d in record.decisions:
        if d.status == "conflict":
            out.append(Issue(code="attribute_conflict", severity="warning", field=d.attribute, message=d.note))
        elif d.needs_review and d.note:
            out.append(Issue(code="attribute_unconfirmed", severity="info", field=d.attribute, message=d.note))
    for img in record.image_attributes:
        if img.error:
            out.append(Issue(code="vision_failed", severity="warning", field="images",
                             message=f"No attributes from {img.image}: {img.error[:120]}"))
    return out


# ---------------------------------------------------------------- catalog-level checks


def _shingles(text: str, k: int = 4) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", plain(text).lower())
    return {tuple(words[i : i + k]) for i in range(max(0, len(words) - k + 1))}


def catalog_issues(records: Iterable[ProductRecord], threshold: float = 0.6) -> dict[str, list[Issue]]:
    """Duplicate titles and handles, and descriptions so alike that search engines treat them as duplicates."""
    records = [r for r in records if r.listing is not None]
    out: dict[str, list[Issue]] = defaultdict(list)
    by_title: dict[str, list[ProductRecord]] = defaultdict(list)
    by_handle: dict[str, list[ProductRecord]] = defaultdict(list)
    for r in records:
        assert r.listing is not None
        by_title[r.listing.title.strip().lower()].append(r)
        by_handle[r.product.handle].append(r)
    for title, group in by_title.items():
        if len(group) > 1 and title:
            for r in group:
                others = ", ".join(o.product.handle for o in group if o is not r)
                out[r.id].append(Issue(code="duplicate_title", severity="error", field="title",
                                       message=f"Same title as {others}"))
    for handle, group in by_handle.items():
        if len(group) > 1:
            for r in group:
                out[r.id].append(Issue(code="duplicate_handle", severity="error", field="handle",
                                       message=f"Handle “{handle}” is used by {len(group)} products"))
    shingles = {r.id: _shingles(r.listing.body_html) for r in records if r.listing}
    ids = list(shingles)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            sa, sb = shingles[a], shingles[b]
            if not sa or not sb:
                continue
            jaccard = len(sa & sb) / len(sa | sb)
            if jaccard >= threshold:
                for x, y in ((a, b), (b, a)):
                    out[x].append(Issue(code="near_duplicate_description", severity="warning", field="body_html",
                                        message=f"Description {jaccard:.0%} the same as product {y}"))
    return out
