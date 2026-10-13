"""Vocabularies and the parsers for the formats suppliers send."""

from __future__ import annotations

import pytest

from shelfwise.catalog.normalize import (
    gtin_valid,
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
from shelfwise.vocab import canonical, canonical_all, colors_match, nearest_color, parse_count


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Charcoal", "gray"),
        ("Navy Blue", "navy"),
        ("light blue", "light blue"),
        ("sky blue", "light blue"),
        ("Off-White", "white"),
        ("Bordeaux", "burgundy"),
        ("navy with white stripes", "navy"),
        ("chartreuse", None),
    ],
)
def test_color_synonyms(text: str, expected: str | None) -> None:
    assert canonical("color", text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100% organic cotton", "cotton"),
        ("80% wool, 20% nylon", "wool"),
        ("55% linen, 45% cotton", "linen"),
        ("Glazed stoneware", "ceramic"),
        ("18/8 stainless steel", "stainless steel"),
        ("Acacia wood", "wood"),
        ("Recycled cotton canvas", "canvas"),
        ("Borosilicate glass", "glass"),
        ("mystery fibre", None),
    ],
)
def test_material_from_compositions(text: str, expected: str | None) -> None:
    assert canonical("material", text) == expected


def test_patterns_and_styles() -> None:
    assert canonical("pattern", "Breton stripe") == "striped"
    assert canonical("pattern", "gingham") == "checked"
    assert canonical("pattern", "zig-zag") == "geometric"
    assert canonical("style", "Scandinavian") == "minimalist"
    assert canonical("style", "boho") == "bohemian"


def test_canonical_all_keeps_order_and_longest_match() -> None:
    assert canonical_all("color", "light blue and white stripes on navy") == ["light blue", "white", "navy"]
    assert canonical_all("material", "stainless steel lid, glass body") == ["stainless steel", "glass"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3-Pack", 3), ("Set of 4", 4), ("pack of six", 6), ("2 pairs", 2), ("x6", 6), ("12", 12), ("Harbor Tee", None)],
)
def test_pack_counts(text: str, expected: int | None) -> None:
    assert parse_count(text) == expected


def test_color_matching_strict_and_lenient() -> None:
    assert colors_match("navy", "navy")
    assert not colors_match("navy", "blue")
    assert colors_match("navy", "blue", lenient=True)
    assert not colors_match("red", "green", lenient=True)
    assert nearest_color((30, 44, 92)) == "navy"
    assert nearest_color((240, 240, 236)) == "white"


@pytest.mark.parametrize(
    ("text", "default", "expected"),
    [
        ("€24,90", "USD", (24.9, "EUR")),
        ("24,90 EUR", "USD", (24.9, "EUR")),
        ("1.299,00 €", "USD", (1299.0, "EUR")),
        ("$1,299.00", "EUR", (1299.0, "USD")),
        ("12.50", "EUR", (12.5, "EUR")),
        ("£45", "USD", (45.0, "GBP")),
    ],
)
def test_prices(text: str, default: str, expected: tuple[float, str]) -> None:
    assert parse_price(text, default) == expected


def test_number_edge_cases() -> None:
    assert parse_number("0,45") == 0.45
    assert parse_number("1,299") == 1299.0
    assert parse_number("abc") is None


def test_dimensions_in_any_unit_become_cm() -> None:
    assert parse_dimensions("30 x 20 x 10 cm") == [30.0, 20.0, 10.0]
    assert parse_dimensions("300x200mm") == [30.0, 20.0]
    assert parse_dimensions("11.8 x 7.9 in") == [30.0, 20.1]
    assert parse_dimensions("45", "cm") == [45.0]
    assert parse_dimensions("") is None


def test_weights_and_capacities() -> None:
    assert parse_weight("1.2 lb") == 544
    assert parse_weight("0,45kg") == 450
    assert parse_weight("0.285", "kg") == 285
    assert parse_weight("500 g") == 500
    assert parse_capacity("Capacity 350 ml") == 350
    assert parse_capacity("0.75 l") == 750
    assert parse_capacity("12 fl oz") == 355
    assert parse_capacity("no volume here") is None


def test_header_unit_hints() -> None:
    assert unit_hint_from_header("weight_kg") == "kg"
    assert unit_hint_from_header("length_cm") == "cm"
    assert unit_hint_from_header("Price (EUR)") is None


def test_codes_and_titles() -> None:
    assert normalize_sku(" lk-1001 ") == "LK1001"
    gtin = normalize_gtin("2916011084367")
    assert gtin == "02916011084367"
    assert gtin_valid(gtin)
    assert normalize_gtin("12345") is None
    assert normalize_gtin(2916011084367.0) == "02916011084367"
    assert slugify("Larkspur Fjord Mug – Set of 2") == "larkspur-fjord-mug-set-of-2"
    assert smart_title("HARBOR CREW TEE L/XL US 9") == "Harbor Crew Tee L/XL US 9"
    assert smart_title("set of 4 dinner plates") == "Set of 4 Dinner Plates"
