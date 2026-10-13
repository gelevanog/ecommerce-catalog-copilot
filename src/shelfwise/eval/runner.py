"""The evaluation on the synthetic catalog (data/demo): import, enrichment and every metric against gold labels.

    shelfwise eval run --name main            import + enrich all 64 products with the configured provider
    shelfwise eval vision --model <id>        the vision step alone with another model (model comparison)
    shelfwise eval judge --sample 20          an LLM judge re-checks listings for unsupported claims
    shelfwise eval backtranslate --sample 10  back-translation of DE/ES/FR into English, scored with chrF
    shelfwise eval summary                    results/summary.json from the files above

Each run writes results/<name>_products.jsonl (one compact line per product) and results/<name>.json (metrics).
"""

from __future__ import annotations

import json
import random
import statistics
import time
from typing import Any

from shelfwise.catalog.models import ProductRecord
from shelfwise.config import Settings
from shelfwise.eval.gold import load_gold
from shelfwise.services import Copilot
from shelfwise.store import Store
from shelfwise.validate.claims import plain
from shelfwise.vocab import colors_match

EXPECTED_MAPPING = {
    "Item Name": "title",
    "SKU#": "sku",
    "EAN": "gtin",
    "Colour": "color",
    "Mat.": "material",
    "Size": "size",
    "Dims": "dimensions",
    "Wt": "weight",
    "Price (EUR)": "price",
    "Qty": "inventory",
    "Image": "image",
    "Category": "category",
    "Notes": "notes",
    "product_title": "title",
    "variant_size": "size",
    "colour_name": "color",
    "material_composition": "material",
    "length_cm": "length",
    "width_cm": "width",
    "height_cm": "height",
    "weight_kg": "weight",
    "capacity": "capacity",
    "rrp_usd": "price",
    "stock": "inventory",
    "picture": "image",
    "features": "features",
    "care": "care",
    "gtin": "gtin",
    "item_code": "sku",
}
EN_FIELDS = ("title", "body_html", "bullets", "meta_title", "meta_description", "alt_texts")


def _pct(n: float, d: float) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def match_products(
    records: list[ProductRecord], gold: dict[str, Any]
) -> dict[str, tuple[dict[str, Any], ProductRecord]]:
    """gold id -> (gold product, record), matched by GTIN of the first variant."""
    by_gtin: dict[str, ProductRecord] = {}
    for r in records:
        for v in r.product.variants:
            if v.gtin:
                by_gtin[v.gtin.lstrip("0")] = r
    out = {}
    for g in gold["products"]:
        hit = by_gtin.get(g["gtins"][0].lstrip("0"))
        if hit is not None:
            out[g["id"]] = (g, hit)
    return out


# ---------------------------------------------------------------- import metrics


def import_metrics(copilot: Copilot, batch_id: str, gold: dict[str, Any]) -> dict[str, Any]:
    records = copilot.db.products(batch_id)
    matched = match_products(records, gold)
    batch = copilot.db.batch(batch_id) or {}
    mapping_total = mapping_ok = 0
    wrong_mappings = []
    for feed in batch.get("feeds", []):
        for m in feed["mapping"]:
            if m["column"] in EXPECTED_MAPPING:
                mapping_total += 1
                if m["target"] == EXPECTED_MAPPING[m["column"]]:
                    mapping_ok += 1
                else:
                    wrong_mappings.append(f"{m['column']} -> {m['target']}")
    grouped = sum(1 for g, r in matched.values() if len(r.product.variants) == max(1, len(g["sizes"])))
    expected_dups = len(gold["duplicates"]["within_supplier_a"]) + len(gold["duplicates"]["across_suppliers"])
    merged = sum(len(r.product.merged_duplicates) for r in records)
    dup_hits = sum(
        1
        for gid in gold["duplicates"]["within_supplier_a"] + gold["duplicates"]["across_suppliers"]
        if gid in matched and matched[gid][1].product.merged_duplicates
    )
    planted = {g["id"] for g in gold["products"] if g["variant_problems"]}
    flagged = {
        gid
        for gid, (g, r) in matched.items()
        if any(i.code in {"variant_inconsistent", "variant_price_outlier"} for i in r.product.issues)
    }
    units = {"dimensions": [0, 0], "weight": [0, 0], "capacity": [0, 0], "price": [0, 0]}
    for g, r in matched.values():
        p = r.product
        dims = p.fact("dimensions")
        if g["dims_cm"]:
            units["dimensions"][1] += 1
            if (
                dims
                and len(dims.value) == len(g["dims_cm"])
                and all(abs(a - b) <= 0.2 for a, b in zip(dims.value, g["dims_cm"], strict=True))
            ):
                units["dimensions"][0] += 1
        weight = p.fact("weight")
        units["weight"][1] += 1
        # the feeds round weights (2 decimals of kg or lb), so allow 1% or 5 g
        if weight and abs(float(weight.value) - g["weight_g"]) <= max(5, 0.01 * g["weight_g"]):
            units["weight"][0] += 1
        if g["capacity_ml"]:
            units["capacity"][1] += 1
            cap = p.fact("capacity")
            if cap and abs(float(cap.value) - g["capacity_ml"]) <= 1:
                units["capacity"][0] += 1
        prices = [v.price for v in p.variants if v.price is not None]
        units["price"][1] += 1
        if prices and abs(statistics.median(prices) - g["price_usd"]) <= 0.02:
            units["price"][0] += 1
    leaked_fluff = 0
    fluff_total = 0
    for g, r in matched.values():
        for phrase in g["fluff"]:
            fluff_total += 1
            if any(f.display.strip(" .").lower() in phrase.lower() and len(f.display) > 8 for f in r.product.facts):
                leaked_fluff += 1
    return {
        "rows": sum(f["rows"] for f in batch.get("feeds", [])),
        "products_expected": len(gold["products"]),
        "products_imported": len(records),
        "products_matched": len(matched),
        "column_mapping": {"correct": mapping_ok, "total": mapping_total, "wrong": wrong_mappings},
        "variants_grouped": {"correct": grouped, "total": len(matched)},
        "duplicates": {"expected": expected_dups, "merged_rows": merged, "found": dup_hits},
        "variant_problems": {
            "planted": len(planted),
            "flagged": len(planted & flagged),
            "false_flags": len(flagged - planted),
        },
        "units": {k: {"correct": v[0], "total": v[1]} for k, v in units.items()},
        "supplier_claims_kept_as_facts": {"leaked": leaked_fluff, "total": fluff_total},
    }


# ---------------------------------------------------------------- attribute and listing metrics


def attribute_metrics(matched: dict[str, tuple[dict[str, Any], ProductRecord]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attribute in ("color", "pattern", "material", "count", "style"):
        image = feed = merged = 0
        image_n = feed_n = merged_n = 0
        lenient = 0
        for g, r in matched.values():
            truth = g[attribute]
            attrs = r.image_attributes[0] if r.image_attributes else None
            seen = getattr(attrs, attribute) if attrs and not attrs.error else None
            fact = r.product.fact(attribute)
            d = r.decision(attribute)

            def ok(value: Any, truth: Any = truth, attribute: str = attribute) -> bool:
                if attribute == "color":
                    return colors_match(value, truth)
                return bool(value == truth)

            if truth is None:
                continue
            image_n += 1
            if seen is not None and ok(seen):
                image += 1
            if attribute == "color" and seen is not None and colors_match(seen, truth, lenient=True):
                lenient += 1
            if fact is not None:
                feed_n += 1
                feed += ok(fact.value)
            if d is not None and d.value is not None:
                merged_n += 1
                merged += ok(d.value)
        out[attribute] = {
            "image_accuracy": _pct(image, image_n),
            "image_correct": image,
            "n": image_n,
            "feed_coverage": _pct(feed_n, image_n),
            "feed_accuracy": _pct(feed, feed_n),
            "merged_coverage": _pct(merged_n, image_n),
            "merged_accuracy": _pct(merged, merged_n),
        }
        if attribute == "color":
            out[attribute]["image_accuracy_lenient"] = _pct(lenient, image_n)
    # conflicts: planted feed errors should be caught; correct feed values should not raise one
    planted = caught = false_alarms = feed_right = 0
    for g, r in matched.values():
        for attribute, error_key in (("color", "feed_color_error"), ("count", "feed_count_error")):
            d = r.decision(attribute)
            fact = r.product.fact(attribute)
            if fact is None or d is None:
                continue
            if g[error_key]:
                planted += 1
                caught += d.status == "conflict"
            else:
                feed_right += 1
                false_alarms += d.status == "conflict"
    out["conflicts"] = {
        "planted_feed_errors": planted,
        "caught": caught,
        "recall": _pct(caught, planted),
        "feed_values_right": feed_right,
        "false_alarms": false_alarms,
    }
    return out


def listing_metrics(
    matched: dict[str, tuple[dict[str, Any], ProductRecord]], store_limits: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    from shelfwise.taxonomy import get

    records = [r for _, r in matched.values() if r.listing is not None]
    n = len(records)
    cat_exact = cat_branch = ptype = 0
    for g, r in matched.values():
        if r.listing is None:
            continue
        cat_exact += r.listing.category_id == g["category_id"]
        chosen = get(r.listing.category_id)
        truth = get(g["category_id"])
        if chosen and truth:
            cat_branch += chosen.path.split(" > ")[:2] == truth.path.split(" > ")[:2]
        ptype += r.listing.product_type == g["product_type"]
    # claims: fields with at least one unsupported claim (final listing, English)
    fields = flagged_fields = banned = 0
    by_code: dict[str, int] = {}
    for r in records:
        assert r.listing is not None
        names = [f for f, _ in r.listing.text_fields()]
        fields += len(names)
        bad = {
            i.field for i in r.issues if i.code.startswith("unsupported_") and not i.field.startswith("translations")
        }
        flagged_fields += len(bad & set(names))
        for i in r.issues:
            if i.code.startswith("unsupported_") or i.code == "banned_claim":
                by_code[i.code] = by_code.get(i.code, 0) + 1
            banned += i.code == "banned_claim"
    first_draft = [r.copy_attempts[0] for r in records if r.copy_attempts]
    first_unsupported = sum(1 for a in first_draft for c in a if c.startswith(("unsupported_", "banned_claim")))
    first_products = sum(1 for a in first_draft if any(c.startswith(("unsupported_", "banned_claim")) for c in a))
    final_products = sum(
        1
        for r in records
        if any(
            i.code.startswith("unsupported_") or i.code == "banned_claim"
            for i in r.issues
            if not i.field.startswith("translations")
        )
    )
    corrected = sum(1 for r in records if len(r.copy_attempts) > 1)
    # SEO lengths
    seo: dict[str, list[int]] = {k: [0, 0] for k in store_limits}
    for r in records:
        assert r.listing is not None
        for key, (lo, hi) in store_limits.items():
            text = getattr(r.listing, key)
            seo[key][1] += 1
            seo[key][0] += lo <= len(text) <= hi
    all_ok = sum(
        1
        for r in records
        if r.listing and all(lo <= len(getattr(r.listing, k)) <= hi for k, (lo, hi) in store_limits.items())
    )
    status: dict[str, int] = {}
    for _, r in matched.values():
        status[r.status] = status.get(r.status, 0) + 1
    return {
        "listings": n,
        "category": {
            "exact": cat_exact,
            "exact_pct": _pct(cat_exact, n),
            "same_branch_pct": _pct(cat_branch, n),
            "product_type_pct": _pct(ptype, n),
        },
        "claims": {
            "english_fields": fields,
            "fields_with_unsupported_claim": flagged_fields,
            "hallucination_rate_pct": _pct(flagged_fields, fields),
            "products_with_unsupported_claim_first_draft": first_products,
            "unsupported_claims_first_draft": first_unsupported,
            "products_with_unsupported_claim_final": final_products,
            "products_corrected": corrected,
            "banned_claims_final": banned,
            "by_code_final": by_code,
        },
        "seo_length": {k: {"ok": v[0], "total": v[1], "pct": _pct(v[0], v[1])} for k, v in seo.items()}
        | {"all_three": {"ok": all_ok, "total": n, "pct": _pct(all_ok, n)}},
        "against_gold": against_gold(matched),
        "status": status,
    }


def against_gold(matched: dict[str, tuple[dict[str, Any], ProductRecord]]) -> dict[str, Any]:
    """Statements in the final English listings checked against the truth (the gold labels), not the evidence:
    a wrong color, pack size or material that reached the copy, e.g. a supplier error that was not caught."""
    import re as _re

    from shelfwise.validate.claims import _COLOR_RE, _COUNT_RE, _WORD_NUMBERS, _without_words
    from shelfwise.vocab import canonical, canonical_all

    wrong = {"color": 0, "count": 0, "material": 0}
    products_wrong = 0
    examples: list[str] = []
    for g, r in matched.values():
        if r.listing is None:
            continue
        text = " ".join(plain(t) for _, t in r.listing.text_fields())
        name_words = {w.lower() for w in _re.findall(r"[A-Za-z]+", f"{g['brand']} {g['title']}")}
        clean = _without_words(text, name_words)
        bad = False
        for m in _COLOR_RE.finditer(clean):
            c = canonical("color", m.group(1))
            if c and not (
                colors_match(c, g["color"], lenient=True) or colors_match(c, g["secondary_color"], lenient=True)
            ):
                wrong["color"] += 1
                bad = True
                examples.append(f"{g['id']}: color '{m.group(1)}' (true {g['color']})")
                break
        for m in _COUNT_RE.finditer(text):
            token = next(x for x in m.groups() if x)
            n = int(token) if token.isdigit() else _WORD_NUMBERS.get(token.lower(), 0)
            if n != g["count"]:
                wrong["count"] += 1
                bad = True
                examples.append(f"{g['id']}: '{m.group(0)}' (true {g['count']})")
                break
        true_material = g["material"]
        allowed = set(canonical_all("material", " ".join([g["composition"] or "", *g["features"], g["care"]])))
        for mat in canonical_all("material", clean):
            if mat != true_material and mat not in allowed:
                wrong["material"] += 1
                bad = True
                examples.append(f"{g['id']}: material '{mat}' (true {true_material})")
                break
        products_wrong += bad
    return {
        "listings_with_wrong_color": wrong["color"],
        "listings_with_wrong_count": wrong["count"],
        "listings_with_wrong_material": wrong["material"],
        "listings_with_any": products_wrong,
        "examples": examples[:12],
    }


def translation_metrics(records: list[ProductRecord], languages: list[str], store: Any) -> dict[str, Any]:
    from shelfwise.validate.translation import _fields, detect_language, glossary_hits, numbers, untranslated

    out: dict[str, Any] = {}
    for lang in languages:
        present = lang_ok = numbers_ok = length_ok = 0
        fields_total = fields_english = 0
        g_expected = g_found = 0
        keep_total = keep_ok = 0
        for r in records:
            if r.listing is None:
                continue
            t = r.listing.translations.get(lang)
            if t is None:
                continue
            present += 1
            fields_total += len(_fields(t))
            fields_english += len(untranslated(r.listing, t, store.glossary_keep))
            src = " ".join(
                plain(x)
                for x in [
                    r.listing.title,
                    r.listing.body_html,
                    *r.listing.bullets,
                    r.listing.meta_title,
                    r.listing.meta_description,
                ]
            )
            tgt = " ".join(plain(x) for x in [t.title, t.body_html, *t.bullets, t.meta_title, t.meta_description])
            lang_ok += detect_language(tgt)[0] == lang
            numbers_ok += not (numbers(src) - numbers(tgt))
            e, f, _ = glossary_hits(src, tgt, lang, store.glossary_terms)
            g_expected += e
            g_found += f
            for term in store.glossary_keep:
                if term.lower() in src.lower():
                    keep_total += 1
                    keep_ok += term.lower() in tgt.lower()
            length_ok += (
                len(t.meta_description) <= store.meta_description.max * store.translation_slack
                and len(t.title) <= store.title.max * store.translation_slack
            )
        out[lang] = {
            "translated": present,
            "language_ok_pct": _pct(lang_ok, present),
            "numbers_kept_pct": _pct(numbers_ok, present),
            "glossary_pct": _pct(g_found, g_expected),
            "glossary_terms": g_expected,
            "brand_kept_pct": _pct(keep_ok, keep_total),
            "length_ok_pct": _pct(length_ok, present),
            "fields_left_in_english_pct": _pct(fields_english, fields_total),
        }
    return out


def throughput(records: list[ProductRecord], wall_seconds: float, workers: int) -> dict[str, Any]:
    stats = [r.stats for r in records]
    calls = sum(s.calls for s in stats)
    tokens_in = sum(s.input_tokens for s in stats)
    tokens_out = sum(s.output_tokens for s in stats)
    per = [s.seconds for s in stats if s.seconds]
    n = max(1, len(records))
    return {
        "products": len(records),
        "wall_seconds": round(wall_seconds, 1),
        "workers": workers,
        "products_per_hour": round(3600 * len(records) / wall_seconds, 1) if wall_seconds else None,
        "seconds_per_product_p50": round(statistics.median(per), 1) if per else None,
        "seconds_per_product_p90": round(sorted(per)[int(0.9 * (len(per) - 1))], 1) if per else None,
        "model_calls": calls,
        "calls_per_product": round(calls / n, 2),
        "cached_calls": sum(s.cached for s in stats),
        "input_tokens_per_product": round(tokens_in / n),
        "output_tokens_per_product": round(tokens_out / n),
        "models": sorted({m for s in stats for m in s.models}),
    }


def compact(g: dict[str, Any] | None, r: ProductRecord) -> dict[str, Any]:
    attrs = r.image_attributes[0] if r.image_attributes else None
    return {
        "gold_id": g["id"] if g else None,
        "id": r.id,
        "status": r.status,
        "title": r.listing.title if r.listing else None,
        "category_id": r.listing.category_id if r.listing else None,
        "gold_category_id": g["category_id"] if g else None,
        "image": attrs.model_dump(
            include={"color", "secondary_color", "pattern", "material", "style", "count", "error", "model"}
        )
        if attrs
        else None,
        "gold": {k: g[k] for k in ("color", "pattern", "material", "style", "count")} if g else None,
        "decisions": {d.attribute: [d.value, d.status] for d in r.decisions},
        "errors": [f"{i.code}:{i.field}" for i in r.issues if i.severity == "error" and not i.waived],
        "copy_attempts": r.copy_attempts,
        "stats": r.stats.model_dump(),
    }


def run_main(
    settings: Settings, *, name: str, limit: int | None = None, translate: bool = True, workers: int | None = None
) -> dict[str, Any]:
    gold = load_gold(settings.demo_dir)
    db_path = settings.results_dir / f".{name}.db"
    if db_path.exists():
        db_path.unlink()
    copilot = Copilot(settings, store=Store(":memory:"))
    summary = copilot.import_demo()
    imported = import_metrics(copilot, summary.batch_id, gold)
    records = copilot.db.products(summary.batch_id)
    ids = [r.id for r in records][:limit] if limit else [r.id for r in records]
    if workers:
        copilot.settings.llm_concurrency = workers
    started = time.monotonic()
    copilot.enrich_many(ids, translate=translate)
    wall = time.monotonic() - started
    records = [r for r in copilot.db.products(summary.batch_id) if r.id in set(ids)]
    matched = match_products(records, gold)
    store = copilot.store_config
    limits = {
        "title": (store.title.min, store.title.max),
        "meta_title": (store.meta_title.min, store.meta_title.max),
        "meta_description": (store.meta_description.min, store.meta_description.max),
    }
    by_record = {r.id: g for g, r in matched.values()}
    report: dict[str, Any] = {
        "name": name,
        "provider": copilot.provider_label(),
        "import": imported,
        "attributes": attribute_metrics(matched),
        "listings": listing_metrics(matched, limits),
        "translations": translation_metrics(records, store.languages, store) if translate else {},
        "throughput": throughput(records, wall, copilot.settings.llm_concurrency),
    }
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    previous = settings.results_dir / f"{name}.json"
    tp = report["throughput"]
    if previous.exists() and tp["model_calls"] and tp["cached_calls"] > tp["model_calls"] / 2:
        # a re-run answered from the disk cache measures nothing: keep the cold run's timing and say so
        old = json.loads(previous.read_text(encoding="utf-8")).get("throughput", {})
        if old and old.get("cached_calls", 0) <= old.get("model_calls", 0) / 2:
            report["throughput"] = old
            report["throughput_rerun_from_cache"] = tp
    with (settings.results_dir / f"{name}_products.jsonl").open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(compact(by_record.get(r.id), r), ensure_ascii=False) + "\n")
    with (settings.results_dir / f"{name}_records.jsonl").open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(r.model_dump_json() + "\n")
    (settings.results_dir / f"{name}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return report


def load_records(settings: Settings, name: str) -> list[ProductRecord]:
    path = settings.results_dir / f"{name}_records.jsonl"
    return [
        ProductRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_vision(settings: Settings, *, name: str, model: str, provider: str = "openrouter") -> dict[str, Any]:
    """The vision step alone on every demo image with another model, scored like the main run."""
    from shelfwise.catalog.ingest import FeedInput, import_feeds
    from shelfwise.config import get_store
    from shelfwise.enrich.crosscheck import decide
    from shelfwise.enrich.vision import extract_image_attributes
    from shelfwise.llm.factory import model_for

    gold = load_gold(settings.demo_dir)
    store = get_store(settings)
    demo = settings.demo_dir
    result = import_feeds(
        [
            FeedInput(demo / "feeds" / f, vendor=v)
            for f, v in (("larkspur_pricelist.xlsx", "Larkspur"), ("northbay_feed.csv", "Northbay"))
        ],
        store,
    )
    vision = model_for(settings, "vision", tag=f"eval:{name}", provider=provider, model=model, fallback_models=[])
    records: list[ProductRecord] = []
    seconds = []
    for p in result.products:
        r = ProductRecord(id=p.id, batch_id="eval", product=p)
        started = time.monotonic()
        attrs, completion = extract_image_attributes(
            vision, demo / "images" / p.images[0], max_tokens=settings.llm_max_tokens
        )
        seconds.append(time.monotonic() - started)
        r.image_attributes = [attrs]
        r.decisions = decide(p, attrs)
        if completion is not None:
            r.stats.calls = 1
            r.stats.input_tokens, r.stats.output_tokens = completion.input_tokens, completion.output_tokens
        records.append(r)
    matched = match_products(records, gold)
    report = {
        "name": name,
        "model": model,
        "attributes": attribute_metrics(matched),
        "errors": sum(1 for r in records if r.image_attributes[0].error),
        "seconds_p50": round(statistics.median(seconds), 1),
    }
    with (settings.results_dir / f"{name}_products.jsonl").open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(compact(None, r) | {"gold": None}, ensure_ascii=False) + "\n")
    (settings.results_dir / f"{name}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return report


def sample(records: list[ProductRecord], k: int, seed: int = 7) -> list[ProductRecord]:
    usable = [r for r in records if r.listing is not None]
    return random.Random(seed).sample(usable, k=min(k, len(usable)))
