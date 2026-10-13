"""Model smoke test: which free OpenRouter models can do the vision and the copy task at all, and how fast.

Lists the free models OpenRouter offers today (the public /models endpoint, no key needed), then sends the same
few products to each candidate. The result (results/smoke.json) is how the defaults in config.py were chosen.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from shelfwise.config import Settings
from shelfwise.eval.gold import load_gold
from shelfwise.llm.factory import model_for
from shelfwise.vocab import colors_match

SMOKE_PRODUCTS = 6


def list_free_models(settings: Settings) -> list[dict[str, Any]]:
    response = httpx.get(f"{settings.openrouter_base_url}/models", timeout=30)
    response.raise_for_status()
    out = []
    for m in response.json().get("data", []):
        if str(m.get("id", "")).endswith(":free"):
            arch = m.get("architecture") or {}
            out.append({"id": m["id"], "input_modalities": arch.get("input_modalities"),
                        "context_length": m.get("context_length")})
    return out


def pick_smoke_products(gold: dict[str, Any]) -> list[dict[str, Any]]:
    """A fixed, varied subset: a pattern, a multi-pack, a material that shows, a plain one."""
    wanted = ["striped", "polka dot", "checked", "floral", "geometric"]
    chosen: list[dict[str, Any]] = []
    for pattern in wanted:
        p = next((p for p in gold["products"] if p["pattern"] == pattern and p not in chosen), None)
        if p:
            chosen.append(p)
    for p in gold["products"]:
        if len(chosen) >= SMOKE_PRODUCTS:
            break
        if p["count"] > 2 and p not in chosen:
            chosen.append(p)
    return chosen[:SMOKE_PRODUCTS]


def vision_smoke(settings: Settings, models: list[str], demo_dir: Path) -> dict[str, Any]:
    from shelfwise.enrich.vision import extract_image_attributes

    gold = load_gold(demo_dir)
    products = pick_smoke_products(gold)
    report: dict[str, Any] = {"products": [p["id"] for p in products], "models": {}}
    for name in models:
        model = model_for(settings, "vision", tag="smoke:vision", provider="openrouter", model=name, fallback_models=[])
        rows = []
        for p in products:
            started = time.monotonic()
            attrs, completion = extract_image_attributes(model, demo_dir / "images" / p["image"])
            rows.append({
                "product": p["id"],
                "seconds": round(time.monotonic() - started, 1),
                "error": attrs.error,
                "color_ok": colors_match(attrs.color, p["color"], lenient=True),
                "pattern_ok": attrs.pattern == p["pattern"],
                "count_ok": attrs.count == p["count"],
                "material_ok": attrs.material == p["material"],
                "answer": attrs.raw,
                "output_tokens": completion.output_tokens if completion else 0,
            })
        ok = [r for r in rows if not r["error"]]
        report["models"][name] = {
            "answered": len(ok),
            "color": sum(r["color_ok"] for r in rows),
            "pattern": sum(r["pattern_ok"] for r in rows),
            "count": sum(r["count_ok"] for r in rows),
            "material": sum(r["material_ok"] for r in rows),
            "median_seconds": sorted(r["seconds"] for r in ok)[len(ok) // 2] if ok else None,
            "rows": rows,
        }
    return report


def write(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
