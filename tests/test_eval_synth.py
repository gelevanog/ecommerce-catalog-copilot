"""The synthetic catalog generator, the renders and the evaluation harness (offline model)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from shelfwise.config import Settings
from shelfwise.eval.judge import chrf, run_backtranslation, run_judge
from shelfwise.eval.runner import run_main
from shelfwise.eval.summary import build_summary, cost_per_thousand
from shelfwise.synth.catalog import KINDS, ean13, seed_catalog, style_for
from shelfwise.synth.render import RenderSpec, render
from shelfwise.taxonomy import candidates, get, load_taxonomy

from .conftest import DEMO


def test_seed_is_deterministic(tmp_path: Path) -> None:
    a = seed_catalog(tmp_path / "a", images=False)
    b = seed_catalog(tmp_path / "b", images=False)
    assert a == b
    assert (tmp_path / "a" / "feeds" / "northbay_feed.csv").read_bytes() == (
        tmp_path / "b" / "feeds" / "northbay_feed.csv"
    ).read_bytes()


def test_bundled_demo_matches_the_generator(tmp_path: Path) -> None:
    fresh = seed_catalog(tmp_path / "c", images=False)
    bundled = json.loads((DEMO / "gold.json").read_text())
    assert fresh == bundled


def test_eans_have_valid_check_digits() -> None:
    import random

    from shelfwise.catalog.normalize import gtin_valid, normalize_gtin

    rng = random.Random(1)
    for _ in range(50):
        code = ean13(rng)
        assert code.startswith("2") and len(code) == 13
        norm = normalize_gtin(code)
        assert norm is not None and gtin_valid(norm)


def test_render_size_and_count() -> None:
    img = render(RenderSpec("candles", "red", "white", "solid", "wax", count=3, seed=1))
    assert img.size == (600, 600) and img.mode == "RGB"
    assert img.getpixel((5, 5)) != img.getpixel((300, 420))


def test_demo_images_are_small() -> None:
    files = list((DEMO / "images").glob("*.jpg"))
    assert len(files) == 64
    assert sum(f.stat().st_size for f in files) < 5_000_000
    with Image.open(files[0]) as im:
        assert im.size == (600, 600)


def test_style_rule_and_taxonomy_labels() -> None:
    assert style_for("plates", "blue", "floral", "ceramic") == "bohemian"
    assert style_for("board", "brown", "solid", "wood") == "rustic"
    assert style_for("mug", "white", "solid", "ceramic") == "minimalist"
    taxonomy = load_taxonomy()
    for kind in KINDS.values():
        assert kind.category_id in taxonomy
        assert kind.category_id in {c.id for c in candidates(f"{kind.nouns[0]} {kind.supplier_category}")}
    category = get(2169)
    assert category is not None and category.leaf == "Mugs"


def test_offline_eval_run_and_spot_checks(settings: Settings) -> None:
    report = run_main(settings, name="t", limit=12)
    assert report["import"]["products_matched"] == 64
    assert report["import"]["duplicates"] == {"expected": 6, "merged_rows": 6, "found": 6}
    assert report["import"]["column_mapping"]["correct"] == report["import"]["column_mapping"]["total"] == 29
    assert report["throughput"]["products"] == 12
    assert report["listings"]["claims"]["hallucination_rate_pct"] == 0.0
    judge = run_judge(settings, name="t", k=5)
    assert judge["answered"] == 5 and judge["products_checker_only"] == 0
    back = run_backtranslation(settings, name="t", k=2)
    assert set(back["by_language"]) == {"de", "es", "fr"}
    (settings.results_dir / "prices.json").write_text(
        json.dumps({"prices": {"x/y": {"input_per_million": 1.0, "output_per_million": 2.0}}})
    )
    (settings.results_dir / "t.json").rename(settings.results_dir / "main.json")
    summary = build_summary(settings)
    assert summary["headline"]["products"] == 64


def test_chrf_and_costs() -> None:
    assert chrf("the blue mug", "the blue mug") == 100.0
    assert chrf("a red plate", "the blue mug") < 40
    costs = cost_per_thousand(
        {"throughput": {"input_tokens_per_product": 1000, "output_tokens_per_product": 500}},
        {"prices": {"m": {"input_per_million": 1.0, "output_per_million": 4.0}}},
    )
    assert costs["m"] == 3.0 and costs["free_models_usd"] == 0.0
