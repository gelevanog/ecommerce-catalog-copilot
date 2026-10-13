"""The Google product taxonomy subset bundled with Shelfwise, and candidate retrieval for the category step.

The model never invents a category: code retrieves the most likely leaves by word overlap with the product's
name, the supplier's category and what the photo shows, and the model picks one id from that short list. The id is
then checked against the taxonomy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Category:
    id: int
    path: str

    @property
    def leaf(self) -> str:
        return self.path.rsplit(" > ", 1)[-1]


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[int, Category]:
    raw = resources.files("shelfwise").joinpath("data/google_taxonomy.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    return {int(c["id"]): Category(int(c["id"]), c["path"]) for c in data["categories"]}


def taxonomy_source() -> str:
    raw = resources.files("shelfwise").joinpath("data/google_taxonomy.json").read_text(encoding="utf-8")
    return str(json.loads(raw)["source"])


_SYNONYMS = {
    "tee": "shirts",
    "t-shirt": "shirts",
    "tshirt": "shirts",
    "top": "tops",
    "sweater": "shirts",
    "jumper": "shirts",
    "pullover": "shirts",
    "knit": "",
    "cap": "hats",
    "beanie": "hats",
    "scarf": "scarves",
    "wrap": "scarves",
    "tote": "totes",
    "shopper": "totes",
    "daypack": "backpacks",
    "rucksack": "backpacks",
    "backpack": "backpacks",
    "sneakers": "shoes",
    "sneaker": "shoes",
    "trainers": "shoes",
    "mug": "mugs",
    "cup": "mugs",
    "plate": "plates",
    "vase": "vases",
    "cushion": "pillows",
    "pillow": "pillows",
    "candle": "candles",
    "lamp": "lamps",
    "bottle": "bottles",
    "towel": "towels",
    "planter": "planters",
    "pot": "pots",
    "board": "boards",
    "chopping": "cutting",
    "blanket": "blankets",
    "throw": "blankets",
    "glasses": "tumblers",
    "glass": "tumblers",
    "tumbler": "tumblers",
    "socks": "socks",
    "sock": "socks",
    "headwear": "hats",
    "footwear": "shoes",
    "drinkware": "drinkware",
    "knitwear": "shirts",
}
_STOP = {"and", "of", "the", "set", "with", "for", "accessories", "home", "garden", "&", "s"}


def _tokens(text: str) -> list[str]:
    out = []
    for word in re.findall(r"[a-z][a-z-]*", text.lower()):
        mapped = _SYNONYMS.get(word, word)
        for w in mapped.split():
            if w and w not in _STOP:
                out.append(w)
                if w.endswith("s") and len(w) > 3:
                    out.append(w[:-1])
    return out


def candidates(query: str, k: int = 12) -> list[Category]:
    """The k categories whose path shares the most (leaf-weighted) words with the query."""
    q = set(_tokens(query))
    scored = []
    for cat in load_taxonomy().values():
        leaf = set(_tokens(cat.leaf))
        path = set(_tokens(cat.path))
        score = 3 * len(q & leaf) + len(q & path) - 0.05 * cat.path.count(">")
        if score > 0:
            scored.append((score, cat.id, cat))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored[:k]]


def get(category_id: int | None) -> Category | None:
    if category_id is None:
        return None
    return load_taxonomy().get(int(category_id))
