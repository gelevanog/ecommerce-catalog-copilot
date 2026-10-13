"""Reading feeds, mapping columns, grouping variants, merging duplicates, flagging broken variants."""

from __future__ import annotations

import json
from pathlib import Path

from shelfwise.catalog.ingest import FeedInput, base_title, import_feeds, preview_feed
from shelfwise.catalog.mapping import apply_overrides, find_header_row, propose_mapping, read_table, sniff_target
from shelfwise.config import StoreConfig

from .conftest import DEMO

FEEDS = DEMO / "feeds"


def _demo(store: StoreConfig):  # type: ignore[no-untyped-def]
    return import_feeds(
        [FeedInput(FEEDS / "larkspur_pricelist.xlsx", vendor="Larkspur"), FeedInput(FEEDS / "northbay_feed.csv", vendor="Northbay")],
        store,
    )


def test_xlsx_header_row_is_found_below_title_rows() -> None:
    rows = read_table(FEEDS / "larkspur_pricelist.xlsx")
    assert rows[0][0].startswith("Larkspur Wholesale")
    assert find_header_row(rows) == 3


def test_csv_delimiter_is_sniffed() -> None:
    rows = read_table(FEEDS / "northbay_feed.csv")
    assert rows[0][:3] == ["product_title", "variant_size", "colour_name"]


def test_mapping_by_column_names() -> None:
    _, header, body, mapping = preview_feed(FEEDS / "northbay_feed.csv")
    targets = {m.column: m.target for m in mapping}
    assert targets["rrp_usd"] == "price"
    assert targets["material_composition"] == "material"
    assert targets["item_code"] == "sku"
    assert all(m.method == "synonym" for m in mapping)


def test_mapping_by_values_when_names_are_unknown() -> None:
    header = ["Bezeichnung", "Code13", "Foto", "Abm."]
    rows = [["Tee", "2916011084367", "a.jpg", "30 x 20 cm"], ["Mug", "2569777818808", "b.png", "8 x 11 cm"]]
    mapping = {m.column: (m.target, m.method) for m in propose_mapping(header, rows)}
    assert mapping["Code13"] == ("gtin", "values")
    assert mapping["Foto"] == ("image", "values")
    assert mapping["Abm."] == ("dimensions", "values")
    assert mapping["Bezeichnung"] == (None, "unmapped")
    assert sniff_target(["€12,00", "€4,50"])[0] == "price"


def test_user_overrides_win() -> None:
    mapping = propose_mapping(["Bezeichnung", "EAN"], [["Tee", "2916011084367"]])
    fixed = apply_overrides(mapping, {"Bezeichnung": "title", "EAN": None})
    assert [(m.target, m.method) for m in fixed] == [("title", "user"), (None, "user")]


def test_demo_import_counts(store: StoreConfig) -> None:
    result = _demo(store)
    gold = json.loads((DEMO / "gold.json").read_text())
    assert len(result.products) == len(gold["products"]) == 64
    assert result.duplicates_merged == 6
    variants = sum(len(p.variants) for p in result.products)
    assert variants == sum(max(1, len(g["sizes"])) for g in gold["products"])


def test_duplicates_across_suppliers_are_merged_with_a_note(store: StoreConfig) -> None:
    result = _demo(store)
    cross = [p for p in result.products if any(r.file == "northbay_feed.csv" for r in p.merged_duplicates)]
    assert len(cross) == 3
    assert all(p.supplier_file == "larkspur_pricelist.xlsx" for p in cross)
    assert all(any(i.code == "duplicate_merged" and "another supplier" in i.message for i in p.issues) for p in cross)


def test_planted_variant_problems_are_errors(store: StoreConfig) -> None:
    result = _demo(store)
    flagged = [p for p in result.products if any(i.code in {"variant_inconsistent", "variant_price_outlier"} for i in p.issues)]
    assert len(flagged) == 4
    outlier = next(i for p in flagged for i in p.issues if i.code == "variant_price_outlier")
    assert "the other variants about" in outlier.message


def test_facts_keep_their_cell(store: StoreConfig) -> None:
    result = _demo(store)
    product = next(p for p in result.products if p.fact("capacity") and p.supplier_file == "northbay_feed.csv")
    capacity = product.fact("capacity")
    assert capacity is not None
    assert capacity.evidence[0].ref.startswith("northbay_feed.csv · row ")
    assert capacity.evidence[0].ref.endswith("capacity")
    assert [f.id for f in product.facts] == [f"F{i}" for i in range(1, len(product.facts) + 1)]


def test_prices_are_converted_to_store_currency(store: StoreConfig) -> None:
    result = _demo(store)
    gold = {g["gtins"][0]: g for g in json.loads((DEMO / "gold.json").read_text())["products"]}
    checked = 0
    for p in result.products:
        g = gold.get((p.variants[0].gtin or "").lstrip("0"))
        if g and not g["variant_problems"] and p.supplier_file.endswith(".xlsx"):
            assert abs((p.variants[0].price or 0) - g["price_usd"]) <= 0.02
            assert p.variants[0].currency == "USD"
            checked += 1
    assert checked > 10


def test_supplier_marketing_claims_are_not_facts(store: StoreConfig) -> None:
    result = _demo(store)
    ignored = [i for p in result.products for i in p.issues if i.code == "supplier_claim_ignored"]
    assert ignored
    texts = " ".join(f.display.lower() for p in result.products for f in p.facts)
    for phrase in ("eco-friendly", "relieves back pain", "fda approved", "lifetime", "best seller"):
        assert phrase not in texts


def test_base_title_strips_size_color_pack_and_pattern() -> None:
    assert base_title("STRIPED FJORD MUG SET OF 2 NVY", size=None, vendors=[]) == "Fjord Mug"
    assert base_title("Harbor Tee Light Blue M", size="M", vendors=[]) == "Harbor Tee"
    assert base_title("Larkspur Moss Tote", size=None, vendors=["Larkspur"]) == "Moss Tote"
    assert base_title("aster cushion (floral), pack of 2", size=None, vendors=[]) == "Aster Cushion"


def test_import_from_bytes_and_unknown_vendor(store: StoreConfig, tmp_path: Path) -> None:
    csv = "title;sku;ean;colour;price\nPlain Mug;M-1;2916011084367;white;9.99\n"
    result = import_feeds([FeedInput(tmp_path / "x.csv", data=csv.encode())], store)
    assert len(result.products) == 1
    product = result.products[0]
    assert product.fact("color") is not None
    assert any(i.code == "no_vendor" for i in product.issues)
    assert any(i.code == "no_image" for i in product.issues)
