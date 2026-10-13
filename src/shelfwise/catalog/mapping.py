"""Reading supplier spreadsheets and mapping their columns onto the store's fields.

Header rows are found (spreadsheets often start with a title and a note), the delimiter of a CSV is sniffed, and
each column is mapped by its name (a synonym table) or, failing that, by what its values look like (13-digit
numbers are GTINs, "30 x 20 cm" is a dimension). The mapping is a proposal: the import screen shows it with sample
values and a person confirms or changes it before anything is imported.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from pathlib import Path

from shelfwise.catalog.models import ColumnMapping
from shelfwise.catalog.normalize import clean_text, parse_capacity, parse_dimensions, parse_price, parse_weight
from shelfwise.vocab import canonical

STORE_FIELDS: dict[str, str] = {
    "title": "Product title",
    "sku": "Variant SKU",
    "gtin": "Barcode (EAN/UPC/GTIN)",
    "vendor": "Vendor / brand",
    "color": "Color",
    "material": "Material / composition",
    "size": "Size (variant option)",
    "pack": "Units per pack",
    "dimensions": "Dimensions (one cell)",
    "length": "Length",
    "width": "Width",
    "height": "Height",
    "weight": "Weight",
    "capacity": "Capacity",
    "price": "Price",
    "inventory": "Inventory quantity",
    "image": "Image file",
    "category": "Supplier category",
    "notes": "Notes / description",
    "features": "Features",
    "care": "Care instructions",
}

SYNONYMS: dict[str, tuple[str, ...]] = {
    "title": ("item name", "product title", "title", "name", "product name", "item", "article name", "product"),
    "sku": ("sku", "item code", "article", "art no", "article number", "item number", "product code", "variant sku", "code", "ref"),
    "gtin": ("ean", "gtin", "upc", "barcode", "ean13", "ean code", "gtin13"),
    "vendor": ("brand", "vendor", "manufacturer", "make", "label"),
    "color": ("colour", "color", "colour name", "color name", "farbe", "couleur", "shade"),
    "material": ("material", "mat", "material composition", "composition", "fabric", "materials"),
    "size": ("size", "variant size", "size option", "sizes", "groesse"),
    "pack": ("pack", "pack size", "units per pack", "pcs", "pieces", "count"),
    "dimensions": ("dims", "dimensions", "measurements", "size cm", "dim"),
    "length": ("length", "length cm", "l cm", "depth", "length mm", "length in"),
    "width": ("width", "width cm", "w cm", "width mm", "width in"),
    "height": ("height", "height cm", "h cm", "height mm", "height in", "diameter"),
    "weight": ("wt", "weight", "weight kg", "weight g", "gross weight", "net weight", "weight lb"),
    "capacity": ("capacity", "volume", "capacity ml"),
    "price": ("price", "price eur", "price usd", "rrp", "rrp usd", "rrp eur", "msrp", "retail price", "unit price", "price gbp", "cost"),
    "inventory": ("qty", "stock", "inventory", "quantity", "on hand", "available", "stock qty"),
    "image": ("image", "picture", "photo", "image url", "img", "image file", "main image", "image filename"),
    "category": ("category", "supplier category", "product category", "group", "department"),
    "notes": ("notes", "remarks", "comments", "description", "desc", "long description"),
    "features": ("features", "bullet points", "highlights", "key features"),
    "care": ("care", "care instructions", "washing", "care label"),
}

_HEADER_CLEAN = re.compile(r"[^a-z0-9]+")


def normalize_header(header: str) -> str:
    text = header.lower().replace("#", " ").replace("colour", "colour")
    return " ".join(_HEADER_CLEAN.sub(" ", text).split())


def read_table(path: Path, data: bytes | None = None) -> list[list[str]]:
    """All rows of the first sheet (XLSX) or of a CSV (delimiter sniffed), as cleaned strings."""
    raw = data if data is not None else path.read_bytes()
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = max(",;\t|", key=sample.count)
    return [[clean_text(c) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer() and abs(value) > 1e6:
        return f"{value:.0f}"  # barcodes stored as numbers
    if isinstance(value, float):
        return f"{value:g}"
    return clean_text(value)


def synonym_target(header: str) -> tuple[str | None, float]:
    norm = normalize_header(header)
    if not norm:
        return None, 0.0
    for target, names in SYNONYMS.items():
        if norm in names:
            return target, 1.0
    tokens = norm.split()
    for target, names in SYNONYMS.items():
        for name in names:
            if " " not in name and name in tokens and len(name) > 2:
                return target, 0.8
    return None, 0.0


def find_header_row(rows: Sequence[Sequence[str]], scan: int = 15) -> int:
    """Index of the header row: the first row with 3+ filled cells of which 2+ are known column names."""
    best, best_hits = 0, -1
    for index, row in enumerate(rows[:scan]):
        filled = [c for c in row if c]
        if len(filled) < 3:
            continue
        hits = sum(1 for c in filled if synonym_target(c)[0])
        if hits >= 2 and hits > best_hits:
            best, best_hits = index, hits
            if hits >= max(3, len(filled) // 2):
                return index
    return best


def _share(values: list[str], test: object) -> float:
    filled = [v for v in values if v]
    if not filled:
        return 0.0
    assert callable(test)
    return sum(1 for v in filled if test(v)) / len(filled)


def sniff_target(values: list[str]) -> tuple[str | None, float]:
    """What a column holds, judged from its values only."""
    checks: list[tuple[str, object]] = [
        ("gtin", lambda v: re.fullmatch(r"\d{12,14}", v) is not None),
        ("image", lambda v: re.search(r"\.(jpe?g|png|webp|gif)$", v, re.IGNORECASE) is not None),
        ("dimensions", lambda v: re.search(r"\d\s*[x×]\s*\d", v) is not None and parse_dimensions(v) is not None),
        ("weight", lambda v: re.search(r"\d\s*(kg|g|lb|lbs)\b", v, re.IGNORECASE) is not None and parse_weight(v) is not None),
        ("capacity", lambda v: parse_capacity(v) is not None),
        ("price", lambda v: re.search(r"[€$£]|\b(EUR|USD|GBP)\b", v) is not None and parse_price(v)[0] is not None),
        ("color", lambda v: canonical("color", v) is not None and len(v) < 25),
        ("material", lambda v: canonical("material", v) is not None and len(v) < 40),
    ]
    for target, test in checks:
        share = _share(values, test)
        if share >= 0.8:
            return target, round(share * 0.9, 2)
    return None, 0.0


def propose_mapping(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[ColumnMapping]:
    mappings: list[ColumnMapping] = []
    taken: set[str] = set()
    for i, name in enumerate(header):
        values = [r[i] for r in rows if i < len(r)]
        samples = [v for v in values if v][:3]
        target, confidence = synonym_target(name)
        method = "synonym"
        if target is None or target in taken:
            sniffed, conf = sniff_target(values)
            if sniffed and sniffed not in taken:
                target, confidence, method = sniffed, conf, "values"
            else:
                target, confidence, method = None, 0.0, "unmapped"
        if target:
            taken.add(target)
        mappings.append(
            ColumnMapping(column=name, target=target, method=method, confidence=confidence, samples=samples)  # type: ignore[arg-type]
        )
    return mappings


def apply_overrides(mappings: list[ColumnMapping], overrides: dict[str, str | None]) -> list[ColumnMapping]:
    """A person's corrections from the import screen: {"column name": "target field" or None to ignore it}."""
    out = []
    for m in mappings:
        if m.column in overrides:
            target = overrides[m.column]
            if target is not None and target not in STORE_FIELDS:
                raise ValueError(f"unknown store field {target!r} for column {m.column!r}")
            out.append(m.model_copy(update={"target": target, "method": "user", "confidence": 1.0}))
        else:
            out.append(m)
    return out
