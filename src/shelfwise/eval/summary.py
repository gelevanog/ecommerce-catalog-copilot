"""results/summary.json: the headline numbers and tables of the README and the dashboard's evaluation page,
computed from the result files and the call ledger (never typed by hand)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from shelfwise.config import Settings

PRICE_MODELS = (
    "nvidia/nemotron-3-super-120b-a12b",
    "google/gemma-4-31b-it",
    "google/gemini-3.5-flash-lite",
    "anthropic/claude-haiku-5.5",
    "openai/gpt-5.4-mini",
    "anthropic/claude-sonnet-5.5",
)


def snapshot_prices(settings: Settings) -> dict[str, Any]:
    """Paid list prices from OpenRouter's public /models endpoint (no key), for the cost-per-1,000 estimate."""
    response = httpx.get(f"{settings.openrouter_base_url}/models", timeout=30)
    response.raise_for_status()
    prices = {}
    for m in response.json().get("data", []):
        if m["id"] in PRICE_MODELS:
            p = m.get("pricing") or {}
            prices[m["id"]] = {
                "input_per_million": round(float(p.get("prompt", 0)) * 1e6, 4),
                "output_per_million": round(float(p.get("completion", 0)) * 1e6, 4),
            }
    out = {"source": f"{settings.openrouter_base_url}/models", "prices": prices}
    (settings.results_dir / "prices.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def ledger_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    requested = Counter(r["requested_model"] for r in rows)
    served = Counter(r["served_model"] for r in rows if r.get("served_model"))
    status = Counter(r["status"] for r in rows)
    tags = Counter(
        str(r.get("tag", "")).split(":")[0] if not str(r.get("tag", "")).startswith("eval:") else r["tag"] for r in rows
    )
    return {
        "requests": len(rows),
        "first": rows[0]["ts"] if rows else None,
        "last": rows[-1]["ts"] if rows else None,
        "status": dict(status),
        "by_tag": dict(tags),
        "requested_models": dict(requested),
        "served_models": dict(served),
        "all_free": all(m.endswith(":free") for m in list(requested) + list(served)),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }


def cost_per_thousand(main: dict[str, Any], prices: dict[str, Any] | None) -> dict[str, Any]:
    tp = main.get("throughput", {})
    tin, tout = tp.get("input_tokens_per_product", 0), tp.get("output_tokens_per_product", 0)
    out: dict[str, Any] = {"free_models_usd": 0.0, "tokens_per_product": {"input": tin, "output": tout}}
    for model, p in (prices or {}).get("prices", {}).items():
        usd = 1000 * (tin * p["input_per_million"] + tout * p["output_per_million"]) / 1e6
        out[model] = round(usd, 2)
    return out


def build_summary(settings: Settings) -> dict[str, Any]:
    results = settings.results_dir
    main = _load(results / "main.json") or {}
    prices = _load(results / "prices.json")
    if prices is None:
        try:
            prices = snapshot_prices(settings)
        except httpx.HTTPError:
            prices = None
    attrs = main.get("attributes", {})
    listings = main.get("listings", {})
    ledger = ledger_stats(results / "calls.jsonl")
    summary: dict[str, Any] = {
        "measured_on": (ledger_stats(results / "calls.jsonl").get("last") or "")[:10],
        "main": main,
        "offline": _load(results / "offline.json"),
        "vision_alt": _load(results / "vision_alt.json"),
        "judge": _strip_rows(_load(results / "main_judge.json")),
        "backtranslation": _strip_rows(_load(results / "main_backtranslation.json")),
        "smoke_vision": _strip_rows(_load(results / "smoke_vision.json"), key="models"),
        "ledger": ledger_stats(results / "calls.jsonl"),
        "cost_per_1000_products": cost_per_thousand(main, prices) if main else {},
        "prices": prices,
    }
    summary["headline"] = {
        "products": main.get("import", {}).get("products_matched"),
        "color_accuracy_image": attrs.get("color", {}).get("image_accuracy"),
        "pattern_accuracy_image": attrs.get("pattern", {}).get("image_accuracy"),
        "count_accuracy_image": attrs.get("count", {}).get("image_accuracy"),
        "material_accuracy_image": attrs.get("material", {}).get("image_accuracy"),
        "conflicts_caught": attrs.get("conflicts", {}),
        "category_exact": listings.get("category", {}).get("exact_pct"),
        "hallucination_rate_final": listings.get("claims", {}).get("hallucination_rate_pct"),
        "seo_all_three": listings.get("seo_length", {}).get("all_three", {}).get("pct"),
        "requests": ledger.get("requests"),
        "all_free": ledger.get("all_free"),
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary


def _strip_rows(data: dict[str, Any] | None, key: str = "rows") -> dict[str, Any] | None:
    if data is None:
        return None
    if key == "models":
        return {
            **data,
            "models": {m: {k: v for k, v in row.items() if k != "rows"} for m, row in data.get("models", {}).items()},
            "free_models": data.get("free_models"),
        }
    return {k: v for k, v in data.items() if k != key} | {
        "examples": [r for r in data.get(key, []) if r.get("judge") or r.get("back")][:8]
    }
