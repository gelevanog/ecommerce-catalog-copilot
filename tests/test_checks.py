"""Feed-vs-photo decisions, the claims checker, listing and translation validation."""

from __future__ import annotations

from typing import Any

import pytest

from shelfwise.catalog.models import Evidence, Fact, ImageAttributes, ImportedProduct, Listing, TranslatedListing
from shelfwise.config import StoreConfig
from shelfwise.enrich.crosscheck import decide, usable
from shelfwise.validate.claims import build_evidence, check_text
from shelfwise.validate.listing import catalog_issues, check_structure, validate_listing
from shelfwise.validate.translation import check_translation, detect_language, glossary_hits


def fact(key: str, value: Any, display: str | None = None, fid: str = "") -> Fact:
    return Fact(
        key=key,
        value=value,
        display=display or str(value),
        id=fid or f"F_{key}",
        evidence=[Evidence(source="feed", ref=f"feed.csv · row 2 · {key}", quote=str(value))],
    )


def product(*facts: Fact) -> ImportedProduct:
    return ImportedProduct(
        id="p1",
        handle="larkspur-harbor-mug",
        supplier_file="feed.csv",
        title_raw="Harbor Mug",
        vendor="Larkspur",
        facts=list(facts),
        images=["a.jpg"],
    )


def seen(**kw: Any) -> ImageAttributes:
    conf = kw.pop("confidence", {"color": 0.9, "pattern": 0.9, "material": 0.6, "count": 0.9, "style": 0.5})
    return ImageAttributes(image="a.jpg", model="m", confidence=conf, raw=dict(kw), **kw)


# ---------------------------------------------------------------- cross-check


def test_agreeing_feed_and_photo() -> None:
    d = {x.attribute: x for x in decide(product(fact("color", "navy")), seen(color="navy"))}
    assert d["color"].status == "agree"
    assert not d["color"].needs_review
    assert d["color"].evidence[0].source == "feed" and d["color"].evidence[-1].source == "image"


def test_close_colors_agree_with_a_note() -> None:
    d = {x.attribute: x for x in decide(product(fact("color", "navy")), seen(color="blue"))}
    assert d["color"].status == "agree"
    assert "close colors" in d["color"].note
    assert d["color"].value == "navy"


def test_wrong_feed_color_is_a_conflict_and_photo_is_suggested() -> None:
    d = {x.attribute: x for x in decide(product(fact("color", "green")), seen(color="red"))}
    assert d["color"].status == "conflict"
    assert d["color"].needs_review
    assert d["color"].value == "red"


def test_low_confidence_photo_keeps_feed_value() -> None:
    d = {x.attribute: x for x in decide(product(fact("count", 4)), seen(count=3, confidence={"count": 0.4}))}
    assert d["count"].status == "conflict"
    assert d["count"].value == 4


def test_material_from_feed_wins_and_photo_only_material_is_not_usable() -> None:
    conflict = {x.attribute: x for x in decide(product(fact("material", "linen")), seen(material="cotton"))}
    assert conflict["material"].status == "conflict"
    assert conflict["material"].value == "linen"
    photo_only = {x.attribute: x for x in decide(product(), seen(material="wool", pattern="striped"))}
    assert photo_only["material"].status == "image_only"
    assert not usable(photo_only["material"])
    assert usable(photo_only["pattern"])


def test_feed_naming_the_secondary_color_is_not_a_conflict() -> None:
    d = {x.attribute: x for x in decide(product(fact("color", "white")), seen(color="navy", secondary_color="white"))}
    assert d["color"].status == "agree"
    assert d["color"].value == "navy"


def test_vision_failure_leaves_feed_values() -> None:
    attrs = ImageAttributes(image="a.jpg", model="m", error="rate limited")
    d = {x.attribute: x for x in decide(product(fact("color", "red")), attrs)}
    assert d["color"].status == "feed_only"
    assert d["pattern"].status == "missing"


# ---------------------------------------------------------------- claims


@pytest.fixture()
def evidence():  # type: ignore[no-untyped-def]
    facts = [
        fact("composition", "Glazed stoneware", fid="F1"),
        fact("material", "ceramic", fid="F2"),
        fact("dimensions", [8.0, 11.0], "8 x 11 cm", fid="F3"),
        fact("capacity", 350, "350 ml", fid="F4"),
        fact("care", "Dishwasher safe", fid="F5"),
        fact("origin", "Portugal", "Made in Portugal", fid="F6"),
    ]
    p = product(*facts, fact("count", 2, "2 units", fid="F7"), fact("color", "blue", fid="F8"))
    decisions = decide(p, seen(color="blue", pattern="polka dot", count=2, secondary_color="white"))
    return build_evidence(p.facts, decisions, [seen(color="blue", secondary_color="white")], "Larkspur Moss Mug")


def issues(text: str, ev: Any, store: StoreConfig) -> list[str]:
    return [i.code for i in check_text("body_html", text, ev, store).issues]


def test_supported_copy_has_no_issues_and_records_provenance(evidence: Any, store: StoreConfig) -> None:
    text = "<p>A set of 2 blue polka dot mugs in glazed stoneware, 350 ml, 8 x 11 cm. Dishwasher safe. Made in Portugal.</p>"
    result = check_text("body_html", text, evidence, store)
    assert result.issues == []
    assert {"F4", "F3", "F5", "F6", "attr:count", "attr:color", "attr:pattern"} <= set(result.facts)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Holds 450 ml of coffee.", "unsupported_spec"),
        ("Measures 10 x 12 cm.", "unsupported_spec"),
        ("Weighs 2 kg.", "unsupported_spec"),
        ("Microwave safe.", "unsupported_claim"),
        ("Made in Italy.", "unsupported_claim"),
        ("A set of 4 mugs.", "unsupported_count"),
        ("Crafted from porcelain and bamboo.", "unsupported_material"),
        ("A cheerful red mug.", "unsupported_color"),
        ("With bold stripes.", "unsupported_pattern"),
        ("The best mug you will ever own.", "banned_claim"),
        ("Antibacterial glaze.", "banned_claim"),
        ("An eco-friendly choice.", "banned_claim"),
        ("Relieves stress every morning.", "banned_claim"),
    ],
)
def test_unsupported_and_banned_claims(text: str, code: str, evidence: Any, store: StoreConfig) -> None:
    assert code in issues(text, evidence, store)


def test_unit_conversions_are_accepted(evidence: Any, store: StoreConfig) -> None:
    assert issues("About 3.1 x 4.3 in and 11.8 fl oz.", evidence, store) == []


def test_name_words_are_not_color_claims(evidence: Any, store: StoreConfig) -> None:
    assert issues("The Moss Mug in blue.", evidence, store) == []


def test_secondary_color_from_photo_is_supported(evidence: Any, store: StoreConfig) -> None:
    assert issues("Blue with white dots.", evidence, store) == []


# ---------------------------------------------------------------- listings


def good_listing() -> Listing:
    return Listing(
        title="Larkspur Moss Mug – Set of 2, Blue Polka Dot",
        body_html="<p>The Moss Mug comes as a set of 2 blue polka dot mugs made of glazed stoneware. Each holds 350 ml "
        "and measures 8 x 11 cm, a comfortable size for coffee or tea at home or at work.</p><p>Dishwasher safe, so "
        "cleaning up after breakfast takes a moment. Made in Portugal for the Larkspur range at our store.</p>",
        bullets=["Glazed stoneware, 350 ml", "8 x 11 cm", "Dishwasher safe", "Set of 2 mugs"],
        meta_title="Moss Mug – Set of 2, Blue | Fernhill & Co.",
        meta_description="Two blue polka dot mugs in glazed stoneware, 350 ml each and dishwasher safe. Made in Portugal.",
        tags=["mugs", "blue", "polka dot", "stoneware"],
        product_type="Mugs",
        category_id=2169,
        alt_texts=["Two blue polka dot mugs on a light background"],
    )


def test_structure_rules(store: StoreConfig) -> None:
    assert [
        i for i in check_structure(good_listing(), store, vendor="Larkspur", image_count=1) if i.severity == "error"
    ] == []
    bad = good_listing().model_copy(
        update={
            "title": "x" * 90,
            "bullets": ["one"],
            "product_type": "Gadgets",
            "category_id": 999999,
            "body_html": "<div onclick='x()'>Hi</div>",
            "alt_texts": [],
        }
    )
    codes = {i.code for i in check_structure(bad, store, vendor="Larkspur", image_count=1)}
    assert {
        "too_long",
        "bullet_count",
        "product_type",
        "category_invalid",
        "html_tag",
        "html_unsafe",
        "alt_text_missing",
        "title_pattern",
    } <= codes


def test_validate_listing_combines_claims_and_translations(evidence: Any, store: StoreConfig) -> None:
    p = product(
        fact("composition", "Glazed stoneware", fid="F1"),
        fact("dimensions", [8.0, 11.0], "8 x 11 cm", fid="F2"),
        fact("capacity", 350, "350 ml", fid="F3"),
        fact("care", "Dishwasher safe", fid="F4"),
        fact("origin", "Portugal", "Made in Portugal", fid="F5"),
        fact("count", 2, "2 units", fid="F6"),
        fact("color", "blue", fid="F7"),
    )
    decisions = decide(p, seen(color="blue", pattern="polka dot", count=2))
    found, provenance = validate_listing(
        good_listing(), store, facts=p.facts, decisions=decisions, images=[], vendor="Larkspur", name="Moss Mug"
    )
    assert [i for i in found if i.severity == "error"] == []
    assert {i.code for i in found} >= {"translation_missing"}
    assert "F3" in provenance["body_html"]


def test_catalog_duplicates() -> None:
    from shelfwise.catalog.models import ProductRecord

    def rec(i: int, title: str, body: str) -> ProductRecord:
        p = product()
        p.id, p.handle = f"p{i}", f"h{i}"
        return ProductRecord(
            id=p.id,
            batch_id="b",
            product=p,
            listing=good_listing().model_copy(update={"title": title, "body_html": body}),
        )

    body = good_listing().body_html
    out = catalog_issues(
        [
            rec(1, "Same title here", body),
            rec(2, "Same title here", body),
            rec(3, "Other", "<p>completely different words in this one</p>"),
        ]
    )
    assert {i.code for i in out["p1"]} == {"duplicate_title", "near_duplicate_description"}
    assert "p3" not in out


# ---------------------------------------------------------------- translations


def test_language_detection() -> None:
    assert detect_language("Die Tasse ist aus Steinzeug und für den Alltag")[0] == "de"
    assert detect_language("La taza es de gres y para el uso diario con tu café")[0] == "es"
    assert detect_language("La tasse est en grès et pour votre café du matin")[0] == "fr"
    assert detect_language("The mug is made of stoneware for your coffee")[0] == "en"


def test_translation_checks(store: StoreConfig) -> None:
    source = good_listing()
    good = TranslatedListing(
        title="Larkspur Moss Becher – 2er-Set, Blau gepunktet",
        body_html="<p>Das Set mit 2 Bechern aus glasiertem Steinzeug, je 350 ml und 8 x 11 cm groß.</p><p>Spülmaschinenfest. "
        "Hergestellt in Portugal für die Larkspur Kollektion.</p>",
        bullets=["Glasiertes Steinzeug, 350 ml", "8 x 11 cm", "Spülmaschinenfest", "Set mit 2 Bechern"],
        meta_title="Moss Becher – 2er-Set | Fernhill & Co.",
        meta_description="Zwei blaue Becher aus Steinzeug, je 350 ml, spülmaschinenfest und in Portugal hergestellt.",
    )
    assert [i for i in check_translation(source, good, "de", store) if i.severity == "error"] == []
    bad = good.model_copy(
        update={"body_html": "<p>The mug holds 300 ml.</p>", "bullets": ["x"], "title": "Moss Becher"}
    )
    codes = {i.code for i in check_translation(source, bad, "de", store)}
    assert {"translation_numbers", "translation_bullets", "translation_keep_term", "translation_html"} <= codes


def test_glossary_hits_allow_german_compounds() -> None:
    terms = {"cotton": {"de": "Baumwolle"}, "dishwasher safe": {"de": "spülmaschinen"}}
    assert glossary_hits("Cotton tee, dishwasher safe", "Baumwollshirt, spülmaschinenfest", "de", terms)[:2] == (2, 2)
    assert glossary_hits("Cotton tee", "Hemd aus Leinen", "de", terms)[2] == ["cotton → Baumwolle"]


def test_fields_left_in_english_are_flagged(store: StoreConfig) -> None:
    source = good_listing()
    target = TranslatedListing(title="Larkspur Moss Becher", body_html="<p>Der Becher ist aus Steinzeug.</p>",
                               bullets=["Glazed stoneware, 350 ml", "8 x 11 cm", "Spülmaschinenfest", "Set mit 2"],
                               meta_title="Moss Becher", meta_description="Zwei Becher aus Steinzeug.")
    flagged = [i.field for i in check_translation(source, target, "de", store) if i.code == "translation_untranslated"]
    assert flagged == ["translations.de.bullets[0]"]
