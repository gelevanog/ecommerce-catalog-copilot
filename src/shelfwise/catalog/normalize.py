"""Parsing the formats suppliers really send: prices, dimensions, weights, capacities, titles, SKUs."""

from __future__ import annotations

import re
import unicodedata

CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP", "us$": "USD"}
CURRENCY_CODES = ("EUR", "USD", "GBP")

_NUMBER = r"\d+(?:[.,'\s]\d{3})*(?:[.,]\d+)?|\d+"


def parse_number(text: str) -> float | None:
    """'1.299,00' -> 1299.0, '1,299.00' -> 1299.0, '24,90' -> 24.9, '0,45' -> 0.45, '12' -> 12.0."""
    raw = text.strip().replace(" ", " ").replace("'", "").replace(" ", "")
    if not raw:
        return None
    match = re.search(r"-?\d[\d.,]*", raw)
    if not match:
        return None
    token = match.group(0).rstrip(".,")
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        if len(tail) == 3 and head not in {"", "0", "-0"}:
            token = token.replace(",", "")  # "1,299" is a thousands separator
        else:
            token = head.replace(",", "") + "." + tail
    elif token.count(".") > 1:
        parts = token.split(".")
        token = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(token)
    except ValueError:
        return None


def parse_price(text: object, default_currency: str = "USD") -> tuple[float | None, str]:
    """'€24,90' -> (24.9, 'EUR'); '24,90 EUR' -> (24.9, 'EUR'); '12.50' -> (12.5, default)."""
    if text is None:
        return None, default_currency
    if isinstance(text, int | float):
        return float(text), default_currency
    s = str(text).strip()
    currency = default_currency
    lowered = s.lower()
    for sign, code in CURRENCY_SIGNS.items():
        if sign in lowered:
            currency = code
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", s, re.IGNORECASE):
            currency = code
    return parse_number(s), currency


_LENGTH_UNITS = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "inch": 2.54, "inches": 2.54, '"': 2.54}
_WEIGHT_UNITS = {"g": 1.0, "gr": 1.0, "grams": 1.0, "kg": 1000.0, "lb": 453.592, "lbs": 453.592, "oz": 28.3495}
_VOLUME_UNITS = {"ml": 1.0, "cl": 10.0, "l": 1000.0, "litre": 1000.0, "liter": 1000.0, "fl oz": 29.5735, "oz": 29.5735}


def _unit_in(text: str, units: dict[str, float]) -> str | None:
    lowered = text.lower()
    for unit in sorted(units, key=len, reverse=True):
        if unit == '"':
            if '"' in lowered:
                return unit
            continue
        if re.search(rf"(?<=[\d\s.]){re.escape(unit)}\b", lowered):
            return unit
    return None


def parse_dimensions(text: object, unit_hint: str | None = None) -> list[float] | None:
    """'30 x 20 x 10 cm', '300x200mm', '11.8 x 7.9 in', '45' (with a cm hint) -> centimetres, rounded to 0.1."""
    if text is None or str(text).strip() == "":
        return None
    s = str(text).replace("×", "x").replace("X", "x")
    unit = _unit_in(s, _LENGTH_UNITS) or unit_hint or "cm"
    numbers = [parse_number(n) for n in re.findall(r"\d+(?:[.,]\d+)?", s)]
    values = [n for n in numbers if n is not None]
    if not values:
        return None
    factor = _LENGTH_UNITS.get(unit, 1.0)
    return [round(v * factor, 1) for v in values]


def parse_weight(text: object, unit_hint: str | None = None) -> float | None:
    """'1.2 lb', '500 g', '0,45kg', '0.285' (with a kg hint) -> grams, rounded to 1 g."""
    if text is None or str(text).strip() == "":
        return None
    if isinstance(text, int | float):
        value = float(text)
        unit = unit_hint or "g"
    else:
        s = str(text)
        value_or_none = parse_number(s)
        if value_or_none is None:
            return None
        value = value_or_none
        unit = _unit_in(s, _WEIGHT_UNITS) or unit_hint or "g"
    return round(value * _WEIGHT_UNITS.get(unit, 1.0))


def parse_capacity(text: object) -> float | None:
    """'350 ml', '0.75 l', '12 fl oz' -> millilitres."""
    if text is None or str(text).strip() == "":
        return None
    s = str(text)
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(fl\.? ?oz|ml|cl|litres?|liters?|l|oz)\b", s, re.IGNORECASE)
    if not match:
        return None
    value = parse_number(match.group(1))
    if value is None:
        return None
    unit = match.group(2).lower().replace(".", "").replace("litres", "litre").replace("liters", "liter")
    unit = "fl oz" if unit.startswith("fl") else unit
    return round(value * _VOLUME_UNITS.get(unit, 1.0))


def unit_hint_from_header(header: str) -> str | None:
    lowered = header.lower()
    for unit in ("mm", "cm", "kg", "lb", "ml", "in", "g"):
        if re.search(rf"(?:^|[\s_(\[]){unit}(?:$|[\s_)\]])", lowered):
            return unit
    return None


def currency_from_header(header: str) -> str | None:
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", header, re.IGNORECASE) or code.lower() in header.lower().split("_"):
            return code
    for sign, code in CURRENCY_SIGNS.items():
        if sign in header:
            return code
    return None


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_sku(sku: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", sku.upper())


def normalize_gtin(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        value = f"{value:.0f}"
    digits = re.sub(r"\D", "", str(value))
    if len(digits) not in (8, 12, 13, 14):
        return None
    return digits.zfill(14) if len(digits) != 13 else "0" + digits


def gtin_valid(gtin14: str) -> bool:
    digits = [int(d) for d in gtin14]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == check


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


SMALL_WORDS = {"a", "an", "and", "of", "the", "with", "for", "in", "on", "to", "or"}


def smart_title(text: str) -> str:
    """Title case that leaves small words, sizes and codes alone: 'HARBOR TEE' -> 'Harbor Tee'."""
    words = text.split()
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i and lower in SMALL_WORDS:
            out.append(lower)
        elif re.fullmatch(r"(?:XXS|XS|S|M|L|XL|XXL|US|UK|EU|\d+[A-Za-z]*|[A-Z]+/[A-Z]+)", word):
            out.append(word)
        else:
            out.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
    return " ".join(out)


def fmt_cm(values: list[float]) -> str:
    return " x ".join(f"{v:g}" for v in values) + " cm"


def fmt_weight(grams: float) -> str:
    return f"{grams / 1000:g} kg" if grams >= 1000 else f"{grams:g} g"


def fmt_ml(ml: float) -> str:
    return f"{ml / 1000:g} l" if ml >= 1000 else f"{ml:g} ml"
