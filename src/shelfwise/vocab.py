"""Controlled vocabularies for product attributes, with the synonyms suppliers and models actually use.

Free text from a feed ("Charcoal", "Breton stripe", "100% Organic Cotton") and from a vision model ("dark grey",
"pinstripes", "ceramic stoneware") is mapped onto one canonical value per attribute, so a feed value and an image
value can be compared in code and a listing can only state values from these lists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ATTRIBUTES = ("color", "pattern", "material", "style", "count")

COLORS: dict[str, tuple[int, int, int]] = {
    "black": (32, 32, 36),
    "white": (242, 241, 236),
    "gray": (134, 137, 142),
    "navy": (30, 44, 92),
    "blue": (44, 96, 196),
    "light blue": (146, 190, 228),
    "teal": (18, 128, 132),
    "green": (46, 132, 72),
    "olive": (112, 116, 52),
    "red": (204, 42, 42),
    "burgundy": (118, 26, 46),
    "pink": (236, 152, 178),
    "orange": (236, 122, 42),
    "yellow": (242, 206, 54),
    "mustard": (202, 160, 40),
    "beige": (222, 202, 168),
    "brown": (122, 82, 52),
    "purple": (112, 62, 152),
}

COLOR_FAMILIES: dict[str, frozenset[str]] = {
    "black": frozenset({"black"}),
    "white": frozenset({"white"}),
    "gray": frozenset({"gray"}),
    "navy": frozenset({"blue"}),
    "blue": frozenset({"blue"}),
    "light blue": frozenset({"blue"}),
    "teal": frozenset({"blue", "green"}),
    "green": frozenset({"green"}),
    "olive": frozenset({"green", "brown"}),
    "red": frozenset({"red"}),
    "burgundy": frozenset({"red", "purple"}),
    "pink": frozenset({"pink", "red"}),
    "orange": frozenset({"orange"}),
    "yellow": frozenset({"yellow"}),
    "mustard": frozenset({"yellow", "orange", "brown"}),
    "beige": frozenset({"brown", "white"}),
    "brown": frozenset({"brown"}),
    "purple": frozenset({"purple"}),
}

COLOR_SYNONYMS: dict[str, str] = {
    "grey": "gray",
    "charcoal": "gray",
    "slate": "gray",
    "silver": "gray",
    "graphite": "gray",
    "ash": "gray",
    "dark gray": "gray",
    "dark grey": "gray",
    "light gray": "gray",
    "light grey": "gray",
    "jet": "black",
    "onyx": "black",
    "ebony": "black",
    "off-white": "white",
    "off white": "white",
    "ivory": "white",
    "snow": "white",
    "chalk": "white",
    "cream": "beige",
    "sand": "beige",
    "oatmeal": "beige",
    "natural": "beige",
    "tan": "beige",
    "khaki": "beige",
    "camel": "brown",
    "chocolate": "brown",
    "walnut": "brown",
    "chestnut": "brown",
    "cognac": "brown",
    "dark blue": "navy",
    "midnight": "navy",
    "midnight blue": "navy",
    "navy blue": "navy",
    "indigo": "navy",
    "cobalt": "blue",
    "royal blue": "blue",
    "royal": "blue",
    "azure": "blue",
    "sky blue": "light blue",
    "sky": "light blue",
    "powder blue": "light blue",
    "baby blue": "light blue",
    "pale blue": "light blue",
    "turquoise": "teal",
    "petrol": "teal",
    "aqua": "teal",
    "emerald": "green",
    "forest": "green",
    "forest green": "green",
    "sage": "green",
    "kelly green": "green",
    "khaki green": "olive",
    "army green": "olive",
    "moss": "olive",
    "crimson": "red",
    "scarlet": "red",
    "cherry": "red",
    "tomato": "red",
    "maroon": "burgundy",
    "wine": "burgundy",
    "oxblood": "burgundy",
    "bordeaux": "burgundy",
    "rose": "pink",
    "blush": "pink",
    "salmon": "pink",
    "coral": "orange",
    "rust": "orange",
    "tangerine": "orange",
    "amber": "orange",
    "lemon": "yellow",
    "sunflower": "yellow",
    "ochre": "mustard",
    "gold": "mustard",
    "golden": "mustard",
    "violet": "purple",
    "plum": "purple",
    "lavender": "purple",
    "lilac": "purple",
}

PATTERNS = ("solid", "striped", "polka dot", "checked", "floral", "geometric")
PATTERN_SYNONYMS: dict[str, str] = {
    "plain": "solid",
    "none": "solid",
    "solid color": "solid",
    "solid colour": "solid",
    "monochrome": "solid",
    "uni": "solid",
    "stripe": "striped",
    "stripes": "striped",
    "pinstripe": "striped",
    "breton": "striped",
    "breton stripe": "striped",
    "horizontal stripes": "striped",
    "vertical stripes": "striped",
    "banded": "striped",
    "dots": "polka dot",
    "dotted": "polka dot",
    "spotted": "polka dot",
    "spots": "polka dot",
    "polka": "polka dot",
    "polka dots": "polka dot",
    "plaid": "checked",
    "check": "checked",
    "checks": "checked",
    "checkered": "checked",
    "checkerboard": "checked",
    "gingham": "checked",
    "tartan": "checked",
    "grid": "checked",
    "flowers": "floral",
    "flower": "floral",
    "botanical": "floral",
    "chevron": "geometric",
    "zigzag": "geometric",
    "zig-zag": "geometric",
    "triangles": "geometric",
    "triangle": "geometric",
    "abstract": "geometric",
    "diamond": "geometric",
    "herringbone": "geometric",
}

MATERIALS = (
    "cotton",
    "wool",
    "denim",
    "leather",
    "canvas",
    "linen",
    "polyester",
    "ceramic",
    "glass",
    "stainless steel",
    "wood",
    "bamboo",
    "terracotta",
)
MATERIAL_SYNONYMS: dict[str, str] = {
    "organic cotton": "cotton",
    "cotton jersey": "cotton",
    "jersey": "cotton",
    "merino": "wool",
    "merino wool": "wool",
    "lambswool": "wool",
    "knit": "wool",
    "wool blend": "wool",
    "cashmere": "wool",
    "jean": "denim",
    "genuine leather": "leather",
    "full-grain leather": "leather",
    "full grain leather": "leather",
    "suede": "leather",
    "cotton canvas": "canvas",
    "flax": "linen",
    "poly": "polyester",
    "recycled polyester": "polyester",
    "nylon": "polyester",
    "fleece": "polyester",
    "stoneware": "ceramic",
    "porcelain": "ceramic",
    "earthenware": "ceramic",
    "borosilicate": "glass",
    "borosilicate glass": "glass",
    "steel": "stainless steel",
    "metal": "stainless steel",
    "stainless": "stainless steel",
    "18/8 stainless steel": "stainless steel",
    "aluminium": "stainless steel",
    "aluminum": "stainless steel",
    "oak": "wood",
    "walnut wood": "wood",
    "acacia": "wood",
    "beech": "wood",
    "teak": "wood",
    "timber": "wood",
    "clay": "terracotta",
    "fired clay": "terracotta",
}

STYLES = ("minimalist", "rustic", "modern", "vintage", "sporty", "bohemian")
STYLE_SYNONYMS: dict[str, str] = {
    "minimal": "minimalist",
    "scandinavian": "minimalist",
    "scandi": "minimalist",
    "clean": "minimalist",
    "simple": "minimalist",
    "farmhouse": "rustic",
    "country": "rustic",
    "natural": "rustic",
    "cottage": "rustic",
    "contemporary": "modern",
    "sleek": "modern",
    "mid-century": "vintage",
    "mid century": "vintage",
    "retro": "vintage",
    "classic": "vintage",
    "athletic": "sporty",
    "sport": "sporty",
    "sports": "sporty",
    "athleisure": "sporty",
    "boho": "bohemian",
    "eclectic": "bohemian",
}

_VOCAB: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "color": (tuple(COLORS), COLOR_SYNONYMS),
    "pattern": (PATTERNS, PATTERN_SYNONYMS),
    "material": (MATERIALS, MATERIAL_SYNONYMS),
    "style": (STYLES, STYLE_SYNONYMS),
}

_WORDS = re.compile(r"[a-z0-9/]+(?:[-'][a-z0-9]+)*")


def _clean(text: str) -> str:
    text = text.lower().replace("colour", "color").replace("_", " ")
    text = re.sub(r"\d+\s*%", " ", text)  # "100% cotton" -> "cotton"
    return " ".join(_WORDS.findall(text))


def canonical(attribute: str, value: object) -> str | None:
    """The canonical vocabulary value for free text, or None when nothing in the text maps onto the vocabulary.

    The earliest match in the text wins ("navy with white stripes" is navy), and at the same position the longer
    phrase wins ("light blue" before "blue", "stainless steel" before "steel")."""
    if value is None:
        return None
    if attribute == "count":
        number = parse_count(str(value))
        return str(number) if number is not None else None
    values, synonyms = _VOCAB[attribute]
    if attribute == "material":
        shares = re.findall(r"(\d{1,3})\s*%\s*([a-zA-Z][a-zA-Z /-]*)", str(value))
        if shares:
            ranked = sorted(((int(pct), canonical("material", name)) for pct, name in shares), key=lambda x: -x[0])
            for _, material in ranked:
                if material:
                    return material
    text = _clean(str(value))
    if not text:
        return None
    if text in values:
        return text
    if text in synonyms:
        return synonyms[text]
    phrases = [(p, p) for p in values] + list(synonyms.items())
    best: tuple[int, int, str] | None = None  # (position, -length, value)
    padded = f" {text} "
    for phrase, target in phrases:
        index = padded.find(f" {phrase} ")
        if index < 0:
            continue
        rank = (index, -len(phrase), target)
        if best is None or rank < best:
            best = rank
    return best[2] if best else None


def canonical_all(attribute: str, text: str) -> list[str]:
    """Every distinct vocabulary value mentioned in a text, in order of appearance."""
    values, synonyms = _VOCAB[attribute]
    padded = f" {_clean(text)} "
    hits: list[tuple[int, int, str]] = []
    for phrase, target in [(p, p) for p in values] + list(synonyms.items()):
        start = 0
        while (index := padded.find(f" {phrase} ", start)) >= 0:
            hits.append((index, -len(phrase), target))
            start = index + 1
    hits.sort()
    taken: list[tuple[int, int]] = []
    out: list[str] = []
    for index, neg_len, target in hits:
        end = index + (-neg_len)
        if any(index < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((index, end))
        if target not in out:
            out.append(target)
    return out


_COUNT_WORDS = {
    "single": 1,
    "one": 1,
    "pair": 2,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "eight": 8,
    "ten": 10,
    "twelve": 12,
}
_COUNT_PATTERNS = [
    re.compile(r"\b(?:pack|set|box|bundle)\s+of\s+(\d{1,2}|[a-z]+)\b"),
    re.compile(r"\b(\d{1,2})\s*[- ]?\s*(?:pack|pk|pcs|pieces|piece|count|ct|set|x)\b"),
    re.compile(r"\b(\d{1,2})\s*[- ]?\s*(?:pairs?)\b"),
    re.compile(r"\b(single|pair|two|three|four|five|six|eight|ten|twelve)[- ](?:pack|set|piece)\b"),
    re.compile(r"\bx\s?(\d{1,2})\b"),
]


def parse_count(text: str) -> int | None:
    """Units in a pack: "3-Pack", "Set of 4", "pack of six", "2 pairs", "x6", or a bare number."""
    lowered = text.lower().strip()
    if re.fullmatch(r"\d{1,3}", lowered):
        return int(lowered)
    if lowered in _COUNT_WORDS:
        return _COUNT_WORDS[lowered]
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(lowered)
        if match:
            token = match.group(1)
            if token.isdigit():
                return int(token)
            if token in _COUNT_WORDS:
                return _COUNT_WORDS[token]
    return None


def colors_match(a: str | None, b: str | None, *, lenient: bool = False) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    if not lenient:
        return False
    return bool(COLOR_FAMILIES.get(a, frozenset()) & COLOR_FAMILIES.get(b, frozenset()))


def nearest_color(rgb: tuple[int, int, int], palette: Iterable[str] = COLORS) -> str:
    """Closest palette color by a weighted RGB distance (the offline model's 'vision')."""
    r, g, b = rgb

    def distance(name: str) -> float:
        pr, pg, pb = COLORS[name]
        mean_r = (r + pr) / 2
        dr, dg, db = r - pr, g - pg, b - pb
        return float((2 + mean_r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - mean_r) / 256) * db * db)

    return min(palette, key=distance)
