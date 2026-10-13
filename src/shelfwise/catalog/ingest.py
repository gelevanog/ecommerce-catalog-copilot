"""From mapped supplier rows to products: normalization, variant grouping, duplicate merging, feed facts.

Each row becomes one variant. Rows are grouped into a product by their base title (the title without size, color,
pack and pattern words); duplicate rows (same GTIN or SKU, also across suppliers) are merged and recorded, never
silently dropped. Every fact keeps the file, row and column it came from, so the review screen can show it and the
claims check can trace a listing's statements back to a cell.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from shelfwise.catalog.mapping import find_header_row, propose_mapping, read_table
from shelfwise.catalog.models import ColumnMapping, Evidence, Fact, ImportedProduct, Issue, SourceRef, Variant
from shelfwise.catalog.normalize import (
    currency_from_header,
    fmt_cm,
    fmt_ml,
    fmt_weight,
    normalize_gtin,
    normalize_sku,
    parse_capacity,
    parse_dimensions,
    parse_number,
    parse_price,
    parse_weight,
    slugify,
    smart_title,
    unit_hint_from_header,
)
from shelfwise.config import StoreConfig
from shelfwise.vocab import canonical, canonical_all, parse_count

COLOR_ABBREVIATIONS = {
    "WHT": "white",
    "BLK": "black",
    "NVY": "navy",
    "GRY": "gray",
    "GRN": "green",
    "OLV": "olive",
    "BLU": "blue",
    "BRN": "brown",
    "BGE": "beige",
    "PNK": "pink",
    "RD": "red",
}
_PACK_PHRASES = re.compile(
    r",?\s*\b(?:(?:pack|set|box) of \w+|\d{1,2}\s*-?\s*(?:pack|pk|pcs|pieces|pairs?)|x\d{1,2})\b", re.IGNORECASE
)
_SIZE_TAIL = re.compile(r"\s+(?:XXS|XS|S|M|L|XL|XXL|S/M|M/L|L/XL|US \d{1,2}|EU \d{2}|UK \d{1,2})$", re.IGNORECASE)
_CARE = re.compile(r"\b(wash|clean|dry|iron|bleach|unattended|oil occasionally|dishwasher|wipe)\b", re.IGNORECASE)
_ORIGIN = re.compile(r"\bmade in ([A-Z][a-zA-Z ]+)", re.IGNORECASE)


@dataclass
class FeedInput:
    path: Path
    data: bytes | None = None
    vendor: str | None = None
    """Brand to use when the feed has no brand column (suppliers that sell one brand)."""
    overrides: dict[str, str | None] = field(default_factory=dict)
    mapping: list[ColumnMapping] | None = None


@dataclass
class FeedReport:
    file: str
    header_row: int
    rows: int
    mapping: list[ColumnMapping]


@dataclass
class ImportResult:
    products: list[ImportedProduct]
    feeds: list[FeedReport]
    duplicates_merged: int
    issues: list[Issue]


@dataclass
class _Row:
    file: str
    number: int
    cells: dict[str, str]
    """store field -> raw value"""
    columns: dict[str, str]
    """store field -> the supplier's column name"""
    hints: dict[str, str]
    """store field -> unit or currency taken from the column name"""


def preview_feed(path: Path, data: bytes | None = None) -> tuple[int, list[str], list[list[str]], list[ColumnMapping]]:
    rows = read_table(path, data)
    header_index = find_header_row(rows)
    header = rows[header_index] if rows else []
    body = [r for r in rows[header_index + 1 :] if any(c for c in r)]
    return header_index, header, body, propose_mapping(header, body)


def _rows(feed: FeedInput) -> tuple[FeedReport, list[_Row]]:
    from shelfwise.catalog.mapping import apply_overrides

    header_index, header, _, proposed = preview_feed(feed.path, feed.data)
    rows = read_table(feed.path, feed.data)
    mapping = feed.mapping or apply_overrides(proposed, feed.overrides)
    by_column = {m.column: m for m in mapping}
    out: list[_Row] = []
    for offset, raw in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(c for c in raw):
            continue
        cells: dict[str, str] = {}
        columns: dict[str, str] = {}
        hints: dict[str, str] = {}
        for i, name in enumerate(header):
            m = by_column.get(name)
            if m is None or m.target is None or i >= len(raw):
                continue
            if raw[i] == "":
                continue
            cells[m.target] = raw[i]
            columns[m.target] = name
            hint = unit_hint_from_header(name) or currency_from_header(name)
            if hint:
                hints[m.target] = hint
        out.append(_Row(feed.path.name, offset, cells, columns, hints))
    return FeedReport(feed.path.name, header_index + 1, len(out), mapping), out


def _ref(row: _Row, target: str) -> str:
    return f"{row.file} · row {row.number} · {row.columns.get(target, target)}"


def _ev(row: _Row, target: str) -> Evidence:
    return Evidence(source="feed", ref=_ref(row, target), quote=row.cells.get(target, ""))


def base_title(title: str, *, size: str | None, vendors: list[str]) -> str:
    text = re.sub(r"\([^)]*\)", " ", title)
    text = _PACK_PHRASES.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    if size and text.lower().endswith(" " + size.lower()):
        text = text[: -len(size)].strip()
    text = _SIZE_TAIL.sub("", text)
    words = text.split()
    # color words trail the name ("Harbor Tee NVY", "Harbor Tee Light Blue"); pattern words lead it ("Striped ...")
    while words:
        last = words[-1].strip(",.")
        two = " ".join(w.strip(",.") for w in words[-2:]).lower()
        if len(words) > 2 and canonical("color", two) and two in _plain_color_words() | {"light blue"}:
            words = words[:-2]
        elif last.upper() in COLOR_ABBREVIATIONS or last.lower() in _plain_color_words():
            words = words[:-1]
        else:
            break
    while words and words[0].lower().strip(",.") in {
        "striped",
        "stripe",
        "polka",
        "dot",
        "checked",
        "plaid",
        "floral",
        "geometric",
        "solid",
    }:
        words = words[1:]
    kept = words
    text = " ".join(kept)
    for vendor in vendors:
        text = re.sub(rf"^{re.escape(vendor)}\s+", "", text, flags=re.IGNORECASE)
    return smart_title(text.strip(" ,-"))


def _plain_color_words() -> set[str]:
    from shelfwise.vocab import COLOR_SYNONYMS, COLORS

    return {c for c in COLORS if " " not in c} | {c for c in COLOR_SYNONYMS if " " not in c}


def _statements(text: str) -> list[str]:
    parts = re.split(r"\s*[;|]\s*|(?<=[.!?])\s+", text)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def _feed_facts(row: _Row, store: StoreConfig, issues: list[Issue]) -> tuple[list[Fact], str]:
    facts: list[Fact] = []
    title = row.cells.get("title", "")
    # color: the color column, else color words or abbreviations in the title
    color_raw = row.cells.get("color", "")
    if color_raw and (color := canonical("color", color_raw)):
        facts.append(Fact(key="color", value=color, display=color, evidence=[_ev(row, "color")]))
    else:
        found = [
            COLOR_ABBREVIATIONS[w.strip(",.").upper()]
            for w in title.split()
            if w.strip(",.").upper() in COLOR_ABBREVIATIONS
        ]
        found += [c for c in canonical_all("color", title) if c not in found]
        if found:
            facts.append(
                Fact(
                    key="color",
                    value=found[0],
                    display=found[0],
                    evidence=[Evidence(source="feed", ref=_ref(row, "title"), quote=title)],
                )
            )
    patterns = canonical_all("pattern", re.sub(r"\bsolid\b", "", title, flags=re.IGNORECASE))
    if patterns:
        facts.append(
            Fact(
                key="pattern",
                value=patterns[0],
                display=patterns[0],
                evidence=[Evidence(source="feed", ref=_ref(row, "title"), quote=title)],
            )
        )
    pack = row.cells.get("pack")
    count = parse_count(pack) if pack else parse_count(title)
    if count:
        target = "pack" if pack else "title"
        facts.append(
            Fact(
                key="count",
                value=count,
                display=f"{count} units",
                evidence=[Evidence(source="feed", ref=_ref(row, target), quote=row.cells.get(target, ""))],
            )
        )
    material_raw = row.cells.get("material", "")
    if material_raw:
        material = canonical("material", material_raw)
        if material:
            facts.append(Fact(key="material", value=material, display=material, evidence=[_ev(row, "material")]))
        facts.append(Fact(key="composition", value=material_raw, display=material_raw, evidence=[_ev(row, "material")]))
    dims: list[float] | None = None
    if "dimensions" in row.cells:
        dims = parse_dimensions(row.cells["dimensions"], row.hints.get("dimensions"))
        dims_ev = [_ev(row, "dimensions")]
    else:
        parts = [
            (t, parse_dimensions(row.cells[t], row.hints.get(t)))
            for t in ("length", "width", "height")
            if t in row.cells
        ]
        dims = [v[0] for _, v in parts if v] or None
        dims_ev = [_ev(row, t) for t, _ in parts]
    if dims:
        facts.append(Fact(key="dimensions", value=dims, display=fmt_cm(dims), evidence=dims_ev))
    if "weight" in row.cells and (grams := parse_weight(row.cells["weight"], row.hints.get("weight"))):
        facts.append(Fact(key="weight", value=grams, display=fmt_weight(grams), evidence=[_ev(row, "weight")]))
    if "capacity" in row.cells and (ml := parse_capacity(row.cells["capacity"])):
        facts.append(Fact(key="capacity", value=ml, display=fmt_ml(ml), evidence=[_ev(row, "capacity")]))
    if "care" in row.cells:
        facts.append(Fact(key="care", value=row.cells["care"], display=row.cells["care"], evidence=[_ev(row, "care")]))
    notes = []
    for target in ("features", "notes"):
        text = row.cells.get(target, "")
        if not text:
            continue
        notes.append(text)
        for statement in _statements(text):
            ev = [Evidence(source="feed", ref=_ref(row, target), quote=statement)]
            banned = next((b for b in store.banned if b.pattern.search(statement)), None)
            if banned:
                issues.append(
                    Issue(
                        code="supplier_claim_ignored",
                        severity="info",
                        field=target,
                        span=statement,
                        message=f"Supplier text not used ({banned.reason}): “{statement}”",
                    )
                )
                continue
            if (ml := parse_capacity(statement)) and not any(f.key == "capacity" for f in facts):
                facts.append(Fact(key="capacity", value=ml, display=fmt_ml(ml), evidence=ev))
            elif m := _ORIGIN.search(statement):
                facts.append(
                    Fact(key="origin", value=m.group(1).strip(), display=f"Made in {m.group(1).strip()}", evidence=ev)
                )
            elif _CARE.search(statement) and "care" not in row.cells:
                facts.append(Fact(key="care", value=statement, display=statement, evidence=ev))
            elif not any(f.key == "care" and f.value == statement for f in facts):
                facts.append(Fact(key="feature", value=statement, display=statement, evidence=ev))
    return facts, " | ".join(notes)


def _price(row: _Row, store: StoreConfig) -> tuple[float | None, str]:
    raw = row.cells.get("price", "")
    if not raw:
        return None, ""
    amount, currency = parse_price(raw, row.hints.get("price") or store.currency)
    if amount is None:
        return None, raw
    rate = store.fx.get(currency.upper(), 1.0) / store.fx.get(store.currency, 1.0)
    return round(amount * rate, 2), raw


def import_feeds(feeds: list[FeedInput], store: StoreConfig, *, known_vendors: list[str] | None = None) -> ImportResult:
    vendors = [v for v in (known_vendors or []) + [f.vendor for f in feeds if f.vendor] if v]
    reports: list[FeedReport] = []
    products: dict[str, ImportedProduct] = {}
    by_gtin: dict[str, tuple[str, int]] = {}
    by_sku: dict[str, tuple[str, int]] = {}
    issues: list[Issue] = []
    merged = 0
    group_facts: dict[str, list[tuple[_Row, list[Fact]]]] = defaultdict(list)
    for feed in feeds:
        report, rows = _rows(feed)
        reports.append(report)
        for row in rows:
            title = row.cells.get("title", "")
            if not title:
                issues.append(
                    Issue(
                        code="row_without_title",
                        severity="warning",
                        field="title",
                        message=f"{row.file} row {row.number}: no title, row skipped",
                    )
                )
                continue
            size = row.cells.get("size") or None
            gtin = normalize_gtin(row.cells.get("gtin"))
            sku = row.cells.get("sku", "") or f"{slugify(title)[:20]}-{row.number}"
            source = SourceRef(file=row.file, row=row.number)
            existing = (gtin and by_gtin.get(gtin)) or by_sku.get(normalize_sku(sku))
            if existing:
                pid, vindex = existing
                product = products[pid]
                product.merged_duplicates.append(source)
                kept = product.variants[vindex]
                price, _ = _price(row, store)
                diff = []
                if price is not None and kept.price is not None and abs(price - kept.price) > 0.01:
                    diff.append(f"price {price:.2f} vs {kept.price:.2f}")
                where = "another supplier" if row.file != kept.source.file else f"row {kept.source.row}"
                product.issues.append(
                    Issue(
                        code="duplicate_merged",
                        severity="warning" if diff else "info",
                        field="variants",
                        message=f"{row.file} row {row.number} duplicates {where} (SKU {kept.sku}); merged"
                        + (f", {', '.join(diff)}: kept the first" if diff else ""),
                    )
                )
                merged += 1
                continue
            vendor = row.cells.get("vendor") or feed.vendor
            base = base_title(title, size=size, vendors=[*vendors, *([vendor] if vendor else [])])
            pid = "p_" + hashlib.sha1(f"{row.file}|{base.lower()}".encode()).hexdigest()[:10]
            row_issues: list[Issue] = []
            facts, notes = _feed_facts(row, store, row_issues)
            if pid not in products:
                image = Path(row.cells.get("image", "")).name if row.cells.get("image") else ""
                products[pid] = ImportedProduct(
                    id=pid,
                    handle=slugify(f"{vendor or ''} {base}"),
                    supplier_file=row.file,
                    title_raw=title,
                    vendor=vendor,
                    supplier_category=row.cells.get("category", ""),
                    images=[image] if image else [],
                    notes_raw=notes,
                )
                if vendor:
                    facts.append(
                        Fact(
                            key="brand",
                            value=vendor,
                            display=vendor,
                            evidence=[
                                _ev(row, "vendor")
                                if "vendor" in row.cells
                                else Evidence(source="rule", ref=f"vendor set for {row.file}", quote=vendor)
                            ],
                        )
                    )
                products[pid].facts = facts
                products[pid].issues.extend(row_issues)
            product = products[pid]
            group_facts[pid].append((row, facts))
            price, raw_price = _price(row, store)
            inventory = parse_number(row.cells.get("inventory", "")) if row.cells.get("inventory") else None
            variant = Variant(
                sku=sku.strip().upper(),
                options={"Size": size} if size else {},
                price=price,
                currency=store.currency,
                source_price=raw_price,
                inventory=int(inventory) if inventory is not None else None,
                weight_g=next((f.value for f in facts if f.key == "weight"), None),
                gtin=gtin,
                source=source,
            )
            product.variants.append(variant)
            vindex = len(product.variants) - 1
            if gtin:
                by_gtin[gtin] = (pid, vindex)
            by_sku[normalize_sku(sku)] = (pid, vindex)
    for pid, entries in group_facts.items():
        _variant_checks(products[pid], entries)
        _assign_ids(products[pid])
    return ImportResult(products=list(products.values()), feeds=reports, duplicates_merged=merged, issues=issues)


def _variant_checks(product: ImportedProduct, entries: list[tuple[_Row, list[Fact]]]) -> None:
    """Rows of one product must agree on what the product is; prices must be plausible against each other."""
    first_row, first = entries[0]
    for key in ("material", "composition", "color"):
        base = next((f for f in first if f.key == key), None)
        for row, facts in entries[1:]:
            other = next((f for f in facts if f.key == key), None)
            if base and other and str(other.value).lower() != str(base.value).lower():
                product.issues.append(
                    Issue(
                        code="variant_inconsistent",
                        severity="error",
                        field=key,
                        message=f"{key} differs between variants: row {first_row.number} says “{base.display}”, "
                        f"row {row.number} says “{other.display}”",
                    )
                )
    prices = [v.price for v in product.variants if v.price]
    if len(prices) >= 2:
        mid = median(prices)
        for v in product.variants:
            if v.price and (v.price > 3 * mid or v.price < mid / 3):
                product.issues.append(
                    Issue(
                        code="variant_price_outlier",
                        severity="error",
                        field="variants",
                        message=f"SKU {v.sku} costs {v.price:.2f}, the other variants about {mid:.2f} (from “{v.source_price}”)",
                    )
                )
    options = [tuple(sorted(v.options.items())) for v in product.variants]
    if len(options) != len(set(options)):
        product.issues.append(
            Issue(
                code="variant_duplicate_option",
                severity="error",
                field="variants",
                message="Two variants have the same option values",
            )
        )
    if not product.images:
        product.issues.append(
            Issue(code="no_image", severity="warning", field="images", message="No product image in the feed")
        )
    if not product.vendor:
        product.issues.append(Issue(code="no_vendor", severity="warning", field="vendor", message="No brand/vendor"))


def _assign_ids(product: ImportedProduct) -> None:
    for i, fact in enumerate(product.facts, start=1):
        fact.id = f"F{i}"


def display_title(product: ImportedProduct, vendors: list[str] | None = None) -> str:
    size = next(iter(product.variants[0].options.values()), None) if product.variants else None
    return base_title(product.title_raw, size=size, vendors=vendors or ([product.vendor] if product.vendor else []))
