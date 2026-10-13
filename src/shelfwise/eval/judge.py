"""Independent checks of the main run: an LLM judge for unsupported claims, and back-translation for the
translations. Both are spot checks on a seeded sample."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from shelfwise.catalog.models import ProductRecord
from shelfwise.config import Settings, get_store
from shelfwise.enrich.copy import facts_for_prompt
from shelfwise.enrich.translate import back_messages
from shelfwise.eval.runner import load_records, sample
from shelfwise.llm.base import LLMError, Message
from shelfwise.llm.factory import model_for
from shelfwise.llm.jsonparse import JSONReplyError, parse_json_object
from shelfwise.validate.claims import plain

JUDGE_TASK = "judge_listing"
JUDGE_SYSTEM = f"""Shelfwise task: {JUDGE_TASK}
You audit e-commerce listings for claims that are not supported by the product data. You get the facts (from the
supplier feed and the product photo) and the listing. List every statement in the listing that states a fact NOT
given in the facts: an invented dimension, weight, capacity, material, color, pattern, pack size, feature, care
instruction, certification, origin or benefit. Ignore general wording that states no fact ("a cozy addition to your
home"). Also list superlatives, guarantees and health or environmental claims.
Answer with JSON only: {{"unsupported": [{{"field": "<field name>", "claim": "<the words>", "why": "<short>"}}]}}
Use [] when everything is supported."""


def judge_messages(record: ProductRecord) -> list[Message]:
    assert record.listing is not None
    from shelfwise.catalog.ingest import display_title

    listing = record.listing
    payload: dict[str, Any] = {
        "product_name": display_title(record.product),
        "facts": facts_for_prompt(record.product.facts, record.decisions),
        "listing": {
            "title": listing.title,
            "body_html": listing.body_html,
            "bullets": listing.bullets,
            "meta_title": listing.meta_title,
            "meta_description": listing.meta_description,
            "alt_texts": listing.alt_texts,
        },
    }
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": "<input>\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</input>"},
    ]


def _field_key(field: str) -> str:
    field = field.strip().lower().replace("description_html", "body_html").replace("description", "body_html")
    m = re.match(r"(bullets|alt_texts)\s*\[?\s*(\d+)", field)
    if m:
        return f"{m.group(1)}[{m.group(2)}]"
    return field.split("[")[0]


def run_judge(settings: Settings, *, name: str, k: int, model: str | None = None) -> dict[str, Any]:
    records = sample(load_records(settings, name), k)
    judge = model_for(settings, "text", tag="eval:judge", **({"model": model, "fallback_models": []} if model else {}))
    rows: list[dict[str, Any]] = []
    agree_products = judge_only_products = checker_only_products = 0
    for r in records:
        assert r.listing is not None
        checker = {
            i.field
            for i in r.issues
            if (i.code.startswith("unsupported_") or i.code == "banned_claim")
            and not i.field.startswith("translations")
        }
        try:
            completion = judge.complete(
                judge_messages(r), max_tokens=settings.llm_max_tokens, temperature=0.0, json_mode=True
            )
            found = parse_json_object(completion.text).get("unsupported") or []
        except (LLMError, JSONReplyError) as exc:
            rows.append({"id": r.id, "error": str(exc)[:200]})
            continue
        judged = {_field_key(str(f.get("field", ""))) for f in found if isinstance(f, dict)}
        checker_keys = {_field_key(f) for f in checker}
        if bool(judged) == bool(checker_keys):
            agree_products += 1
        elif judged:
            judge_only_products += 1
        else:
            checker_only_products += 1
        rows.append(
            {
                "id": r.id,
                "title": r.listing.title,
                "checker_fields": sorted(checker_keys),
                "judge": found,
                "judge_fields": sorted(judged),
            }
        )
    answered = [x for x in rows if "error" not in x]
    claims = [c for x in answered for c in x["judge"] if isinstance(c, dict)]
    report = {
        "name": f"{name}_judge",
        "judge_model": judge.label,
        "sample": len(records),
        "answered": len(answered),
        "products_agree": agree_products,
        "products_judge_only": judge_only_products,
        "products_checker_only": checker_only_products,
        "judge_claims": len(claims),
        "judge_claims_by_field": dict(Counter(_field_key(str(c.get("field", ""))) for c in claims)),
        "rows": rows,
    }
    (settings.results_dir / f"{name}_judge.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return report


def chrf(hypothesis: str, reference: str, n: int = 6, beta: float = 2.0) -> float:
    """Character n-gram F-score (chrF, Popović 2015), 0-100, whitespace removed."""
    hyp, ref = re.sub(r"\s+", "", hypothesis.lower()), re.sub(r"\s+", "", reference.lower())
    precisions, recalls = [], []
    for k in range(1, n + 1):
        h = Counter(hyp[i : i + k] for i in range(len(hyp) - k + 1))
        r = Counter(ref[i : i + k] for i in range(len(ref) - k + 1))
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / sum(h.values()))
        recalls.append(overlap / sum(r.values()))
    if not precisions:
        return 0.0
    p, rc = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    if p + rc == 0:
        return 0.0
    return round(100 * (1 + beta**2) * p * rc / (beta**2 * p + rc), 1)


def run_backtranslation(settings: Settings, *, name: str, k: int) -> dict[str, Any]:
    from shelfwise.validate.translation import numbers

    store = get_store(settings)
    records = sample(load_records(settings, name), k, seed=11)
    model = model_for(settings, "text", tag="eval:backtranslate", reasoning=settings.translate_reasoning_effort)
    rows: list[dict[str, Any]] = []
    for r in records:
        assert r.listing is not None
        source = " ".join(
            plain(x) for x in [r.listing.title, r.listing.body_html, *r.listing.bullets, r.listing.meta_description]
        )
        for lang in store.languages:
            t = r.listing.translations.get(lang)
            if t is None:
                continue
            try:
                completion = model.complete(
                    back_messages(t, lang), max_tokens=settings.llm_max_tokens, temperature=0.0, json_mode=True
                )
                data = parse_json_object(completion.text)
            except (LLMError, JSONReplyError) as exc:
                rows.append({"id": r.id, "lang": lang, "error": str(exc)[:200]})
                continue
            bullets = data.get("bullets") or []
            back = " ".join(
                plain(str(x))
                for x in [
                    data.get("title", ""),
                    data.get("description_html", ""),
                    *(bullets if isinstance(bullets, list) else []),
                    data.get("meta_description", ""),
                ]
            )
            rows.append(
                {
                    "id": r.id,
                    "lang": lang,
                    "chrf": chrf(back, source),
                    "numbers_kept": not (numbers(source) - numbers(back)),
                    "back": back[:400],
                }
            )
    by_lang: dict[str, Any] = {}
    for lang in store.languages:
        ok = [x for x in rows if x["lang"] == lang and "chrf" in x]
        if ok:
            by_lang[lang] = {
                "n": len(ok),
                "chrf_mean": round(sum(x["chrf"] for x in ok) / len(ok), 1),
                "chrf_min": min(x["chrf"] for x in ok),
                "numbers_kept_pct": round(100 * sum(x["numbers_kept"] for x in ok) / len(ok), 1),
            }
    report = {
        "name": f"{name}_backtranslation",
        "model": model.label,
        "products": len(records),
        "by_language": by_lang,
        "rows": rows,
    }
    (settings.results_dir / f"{name}_backtranslation.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return report
