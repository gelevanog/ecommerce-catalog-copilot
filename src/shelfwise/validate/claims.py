"""No invented specs: every claim in a listing must trace to a feed fact or to what the photo shows.

The checker reads generated text the way a careful editor would and pulls out the statements that can be wrong:
numbers with units (dimensions, weights, capacities, percentages, hours), pack sizes, materials, colors, patterns
and claim phrases ("dishwasher safe", "organic", "made in Portugal"). Each one is looked up in the evidence of this
product; a statement with no evidence is an `unsupported_*` error. Banned claims (superlatives, guarantees, medical
and generic green claims) are errors whatever the evidence says. The same pass records which facts each field
states, which is the per-field provenance the review screen shows.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from shelfwise.catalog.models import AttributeDecision, Fact, ImageAttributes, Issue
from shelfwise.catalog.normalize import parse_number
from shelfwise.config import StoreConfig
from shelfwise.enrich.crosscheck import usable
from shelfwise.vocab import canonical, canonical_all, colors_match

COLOR_CLAIM_WORDS = (
    "black",
    "white",
    "gray",
    "grey",
    "charcoal",
    "navy",
    "blue",
    "light blue",
    "sky blue",
    "teal",
    "turquoise",
    "green",
    "olive",
    "red",
    "burgundy",
    "maroon",
    "pink",
    "orange",
    "yellow",
    "mustard",
    "beige",
    "cream",
    "ivory",
    "tan",
    "khaki",
    "brown",
    "purple",
    "lavender",
    "indigo",
    "off-white",
    "dark blue",
    "navy blue",
    "dark grey",
    "dark gray",
    "light grey",
    "light gray",
)
_COLOR_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in COLOR_CLAIM_WORDS), key=len, reverse=True)) + r")\b", re.IGNORECASE
)
PATTERN_CLAIM = re.compile(
    r"\b(striped|stripes?|pinstripes?|polka[- ]dots?|dotted|spotted|checked|checks|checkered|plaid|gingham|tartan|"
    r"floral|flower(?:ed| print| pattern)|botanical print|geometric|chevron|zig-?zag)\b",
    re.IGNORECASE,
)
_UNITS: dict[str, tuple[str, float]] = {
    "mm": ("length", 0.1),
    "cm": ("length", 1.0),
    "m": ("length", 100.0),
    "in": ("length", 2.54),
    "inch": ("length", 2.54),
    "inches": ("length", 2.54),
    '"': ("length", 2.54),
    "g": ("weight", 1.0),
    "kg": ("weight", 1000.0),
    "lb": ("weight", 453.592),
    "lbs": ("weight", 453.592),
    "oz": ("weight", 28.3495),
    "ml": ("volume", 1.0),
    "cl": ("volume", 10.0),
    "l": ("volume", 1000.0),
    "litre": ("volume", 1000.0),
    "liter": ("volume", 1000.0),
    "fl oz": ("volume", 29.5735),
    "%": ("percent", 1.0),
    "hours": ("hours", 1.0),
    "hour": ("hours", 1.0),
    "h": ("hours", 1.0),
}
_NUM = r"\d+(?:[.,]\d+)?"
_UNIT_ALT = r"fl\.? ?oz|mm|cm|kg|lbs|lb|ml|cl|litres?|liters?|inches|inch|in|oz|hours?|g|m|l|h|%|\""
_DIM_RE = re.compile(rf"({_NUM})((?:\s*(?:x|×|by)\s*{_NUM})+)\s*({_UNIT_ALT})(?![a-z])", re.IGNORECASE)
_QTY_RE = re.compile(rf"(?<![\w.])({_NUM})\s?({_UNIT_ALT})(?![a-z0-9])", re.IGNORECASE)
_COUNT_RE = re.compile(
    r"\b(?:set|pack|box|bundle) of (\d{1,2}|two|three|four|five|six|eight|ten|twelve)\b|"
    r"\b(\d{1,2}|two|three|four|five|six)[- ](?:pack|piece|pc)s?\b|"
    r"\b(\d{1,2}|two|three|four|five|six) (?:pairs?|mugs|plates|candles|towels|glasses|tumblers|pieces)\b",
    re.IGNORECASE,
)
_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10, "twelve": 12}
_TAGS = re.compile(r"<[^>]+>")


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text))).strip()


@dataclass
class EvidenceSet:
    quantities: list[tuple[str, float, str]] = field(default_factory=list)
    """(kind, value in base unit, fact id)"""
    materials: dict[str, str] = field(default_factory=dict)
    colors: dict[str, str] = field(default_factory=dict)
    patterns: dict[str, str] = field(default_factory=dict)
    counts: dict[int, str] = field(default_factory=dict)
    texts: list[tuple[str, str]] = field(default_factory=list)
    """(fact id, lowercase text) of every fact, for claim phrases"""
    origin: tuple[str, str] | None = None
    name_words: set[str] = field(default_factory=set)
    """Words of the product name and brand, which are not color or material claims ("Moss Tote")."""

    def text_has(self, phrase: str) -> str | None:
        for fid, text in self.texts:
            if phrase in text:
                return fid
        return None


def _add_quantities(ev: EvidenceSet, text: str, fid: str) -> None:
    for match in _DIM_RE.finditer(text):
        unit = match.group(3).lower()
        kind, factor = _UNITS.get(_unit_key(unit), ("", 1.0))
        for raw in re.findall(_NUM, match.group(0)):
            n = parse_number(raw)
            if n is not None and kind:
                ev.quantities.append((kind, n * factor, fid))
    for match in _QTY_RE.finditer(text):
        n = parse_number(match.group(1))
        kind, factor = _UNITS.get(_unit_key(match.group(2)), ("", 1.0))
        if n is not None and kind:
            ev.quantities.append((kind, n * factor, fid))


def _unit_key(unit: str) -> str:
    u = unit.lower().replace(".", "")
    if u.startswith("fl"):
        return "fl oz"
    if u in {"litres", "liters"}:
        return "litre"
    return u


def build_evidence(
    facts: list[Fact], decisions: list[AttributeDecision], images: list[ImageAttributes], name: str = ""
) -> EvidenceSet:
    ev = EvidenceSet()
    ev.name_words = {w.lower() for w in re.findall(r"[A-Za-z]+", name)}
    for f in facts:
        text = f.display.lower()
        ev.texts.append((f.id, text))
        if f.key == "dimensions" and isinstance(f.value, list):
            ev.quantities += [("length", float(v), f.id) for v in f.value]
        elif f.key == "weight" and isinstance(f.value, int | float):
            ev.quantities.append(("weight", float(f.value), f.id))
        elif f.key == "capacity" and isinstance(f.value, int | float):
            ev.quantities.append(("volume", float(f.value), f.id))
        else:
            _add_quantities(ev, f.display, f.id)
        if f.key in {"material", "composition", "feature", "care"}:
            for m in canonical_all("material", f.display):
                ev.materials.setdefault(m, f.id)
        if f.key == "origin":
            ev.origin = (str(f.value).lower(), f.id)
        if f.key == "count" and str(f.value).split()[0].isdigit():
            ev.counts.setdefault(int(str(f.value).split()[0]), f.id)
    for d in decisions:
        if not usable(d):
            continue
        ref = f"attr:{d.attribute}"
        if d.attribute == "color":
            ev.colors[str(d.value)] = ref
            if d.feed_value:
                ev.colors.setdefault(str(d.feed_value), ref)
        elif d.attribute == "pattern":
            ev.patterns[str(d.value)] = ref
        elif d.attribute == "material":
            ev.materials[str(d.value)] = ref
        elif d.attribute == "count":
            ev.counts[int(d.value)] = ref
    for img in images:
        if img.secondary_color:
            ev.colors.setdefault(img.secondary_color, "attr:color")
    ev.counts.setdefault(1, "default")
    return ev


@dataclass
class ClaimResult:
    issues: list[Issue]
    facts: list[str]
    """ids of the facts the text states (provenance)"""


def check_text(field_name: str, text: str, ev: EvidenceSet, store: StoreConfig, *, english: bool = True) -> ClaimResult:
    body = plain(text)
    lowered = body.lower()
    issues: list[Issue] = []
    used: list[str] = []

    def use(fid: str | None) -> None:
        if fid and fid not in used:
            used.append(fid)

    for banned in store.banned:
        for match in banned.pattern.finditer(body):
            issues.append(
                Issue(
                    code="banned_claim",
                    severity="error",
                    field=field_name,
                    span=match.group(0),
                    message=f"Banned {banned.reason}: “{match.group(0)}”",
                )
            )
    if not english:
        return ClaimResult(issues, used)
    for word in store.avoid_words:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            issues.append(
                Issue(
                    code="style_word",
                    severity="warning",
                    field=field_name,
                    span=word,
                    message=f"Style guide: avoid “{word}”",
                )
            )
    # claim phrases that need a fact
    for claim, phrases in store.needs_evidence.items():
        hit = next((p for p in phrases if re.search(rf"(?<![\w-]){re.escape(p)}(?![\w-])", lowered)), None)
        if hit is None:
            continue
        if claim == "made in":
            country = re.search(r"made in ([a-z ]+?)(?:[.,;]|$| and | with )", lowered)
            if ev.origin and country and ev.origin[0] in country.group(1):
                use(ev.origin[1])
            else:
                issues.append(
                    Issue(
                        code="unsupported_claim",
                        severity="error",
                        field=field_name,
                        span=hit,
                        message=f"“{country.group(0).strip() if country else hit}” is not in the feed",
                    )
                )
            continue
        support = next((fid for p in phrases if (fid := ev.text_has(p))), None)
        if support:
            use(support)
        else:
            issues.append(
                Issue(
                    code="unsupported_claim",
                    severity="error",
                    field=field_name,
                    span=hit,
                    message=f"“{hit}” is not backed by the feed",
                )
            )
    # quantities with units
    spans: list[tuple[int, int]] = []
    for match in _DIM_RE.finditer(body):
        spans.append(match.span())
        kind, factor = _UNITS.get(_unit_key(match.group(3)), ("", 1.0))
        for raw in re.findall(_NUM, match.group(0)):
            n = parse_number(raw)
            if n is not None and kind:
                _check_quantity(field_name, kind, n * factor, f"{raw} {match.group(3)}", ev, issues, use)
    for match in _QTY_RE.finditer(body):
        if any(a <= match.start() < b for a, b in spans):
            continue
        n = parse_number(match.group(1))
        unit = _unit_key(match.group(2))
        kind, factor = _UNITS.get(unit, ("", 1.0))
        if n is None or not kind:
            continue
        if kind == "length" and unit == "m" and n > 5:
            continue  # "m" after a large number is usually not metres in copy
        _check_quantity(field_name, kind, n * factor, match.group(0), ev, issues, use)
    # pack sizes
    for match in _COUNT_RE.finditer(body):
        token = next(g for g in match.groups() if g)
        n = int(token) if token.isdigit() else _WORD_NUMBERS.get(token.lower(), 0)
        if n in ev.counts:
            use(ev.counts[n])
        else:
            issues.append(
                Issue(
                    code="unsupported_count",
                    severity="error",
                    field=field_name,
                    span=match.group(0),
                    message=f"“{match.group(0)}”, but the evidence says {sorted(ev.counts)[-1]} per pack",
                )
            )
    # materials
    for material in canonical_all("material", _without_words(body, ev.name_words)):
        if material in ev.materials:
            use(ev.materials[material])
        else:
            issues.append(
                Issue(
                    code="unsupported_material",
                    severity="error",
                    field=field_name,
                    span=material,
                    message=f"Material “{material}” is not in the feed",
                )
            )
    # colors
    for match in _COLOR_RE.finditer(_without_words(body, ev.name_words)):
        color = canonical("color", match.group(1))
        if color is None:
            continue
        support = next((ref for c, ref in ev.colors.items() if colors_match(color, c, lenient=True)), None)
        if support:
            use(support)
        else:
            issues.append(
                Issue(
                    code="unsupported_color",
                    severity="error",
                    field=field_name,
                    span=match.group(1),
                    message=f"Color “{match.group(1)}” matches neither the feed nor the photo",
                )
            )
    # patterns
    # "flowers" in a vase's copy or "Flower Pot" in a product name are not pattern claims
    for match in PATTERN_CLAIM.finditer(_without_words(body, ev.name_words)):
        pattern = canonical("pattern", match.group(1))
        if pattern is None or pattern == "solid":
            continue
        if pattern in ev.patterns:
            use(ev.patterns[pattern])
        else:
            issues.append(
                Issue(
                    code="unsupported_pattern",
                    severity="error",
                    field=field_name,
                    span=match.group(1),
                    message=f"Pattern “{match.group(1)}” matches neither the feed nor the photo",
                )
            )
    # provenance of plain features (no number, no claim phrase): word overlap with a feature or care fact
    words = set(re.findall(r"[a-z]{4,}", lowered))
    for fid, text in ev.texts:
        fact_words = set(re.findall(r"[a-z]{4,}", text))
        if len(fact_words) >= 2 and len(fact_words & words) / len(fact_words) >= 0.6:
            use(fid)
    return ClaimResult(issues, used)


def _without_words(text: str, words: set[str]) -> str:
    if not words:
        return text
    return re.sub(r"[A-Za-z]+", lambda m: " " if m.group(0).lower() in words else m.group(0), text)


def _check_quantity(
    field_name: str, kind: str, value: float, span: str, ev: EvidenceSet, issues: list[Issue], use: object
) -> None:
    assert callable(use)
    for k, v, fid in ev.quantities:
        if k != kind:
            continue
        tolerance = max(abs(v) * 0.03, 0.6 if kind == "length" else 1.0)
        if abs(v - value) <= tolerance:
            use(fid)
            return
    if kind == "percent" and value == 100:
        return  # "100%" next to a material is covered by the material check
    issues.append(
        Issue(
            code="unsupported_spec",
            severity="error",
            field=field_name,
            span=span,
            message=f"“{span}” does not match any {kind} in the feed",
        )
    )
