"""Loading the gold labels of the synthetic catalog (data/demo/gold.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_gold(demo_dir: Path) -> dict[str, Any]:
    path = demo_dir / "gold.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `shelfwise seed` first")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def gold_by_image(gold: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["image"]: p for p in gold["products"]}
