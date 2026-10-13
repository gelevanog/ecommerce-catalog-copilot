"""Command line: `shelfwise seed | import | enrich | review | export | push | serve | mock-store | eval | mcp`."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from shelfwise.config import Settings, get_settings

app = typer.Typer(help="Shelfwise: an AI catalog copilot for Shopify and WooCommerce.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation on the synthetic catalog (writes results/).", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@app.callback()
def main(log_level: Annotated[str, typer.Option(help="Python log level")] = "WARNING") -> None:
    logging.basicConfig(level=log_level.upper(), format="%(levelname)s %(name)s: %(message)s")


def _copilot() -> object:
    from shelfwise.services import Copilot

    return Copilot(get_settings())


@app.command()
def seed(
    out: Annotated[Path | None, typer.Option(help="Output directory (default: data/demo)")] = None,
    seed_value: Annotated[int, typer.Option("--seed")] = 11,
    products: Annotated[int, typer.Option()] = 64,
) -> None:
    """Generate the synthetic catalog: two messy supplier feeds, product renders and gold labels."""
    from shelfwise.synth.catalog import seed_catalog

    target = out or get_settings().demo_dir
    gold = seed_catalog(target, seed=seed_value, n=products)
    typer.echo(f"{len(gold['products'])} products written to {target}")


@app.command("import")
def import_cmd(
    feeds: Annotated[list[Path] | None, typer.Argument(help="CSV or XLSX supplier feeds")] = None,
    images: Annotated[Path | None, typer.Option(help="Directory with the product images")] = None,
    vendor: Annotated[list[str] | None, typer.Option(help="Brand per feed, in the same order")] = None,
    demo: Annotated[bool, typer.Option(help="Import the bundled demo feeds instead")] = False,
) -> None:
    """Import supplier feeds (column mapping, units, variants, duplicates)."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    if demo:
        summary = copilot.import_demo()
    else:
        vendors = list(vendor or [])
        data: list[tuple[str, bytes, str | None, dict[str, str | None]]] = [
            (f.name, f.read_bytes(), vendors[i] if i < len(vendors) else None, {}) for i, f in enumerate(feeds or [])
        ]
        summary = copilot.import_files(data, images_dir=images.resolve() if images else None)
    typer.echo(
        json.dumps(
            {
                "batch_id": summary.batch_id,
                "products": summary.products,
                "duplicates_merged": summary.duplicates_merged,
                "feeds": summary.feeds,
            },
            indent=1,
        )
    )


@app.command()
def enrich(
    batch: Annotated[str | None, typer.Option(help="Batch id (default: latest)")] = None,
    limit: Annotated[int | None, typer.Option()] = None,
    translate: Annotated[bool, typer.Option(help="Also translate")] = True,
) -> None:
    """Enrich the products of a batch: vision attributes, copy, category, translations, validation."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    batch_id = batch or copilot.db.latest_batch_id()
    if batch_id is None:
        raise typer.BadParameter("no batch: run `shelfwise import` first")
    ids = [r.id for r in copilot.db.products(batch_id)][:limit]
    with typer.progressbar(length=len(ids), label="enriching") as bar:
        last = [0]

        def progress(done: int, total: int) -> None:
            bar.update(done - last[0])
            last[0] = done

        copilot.enrich_many(ids, translate=translate, progress=progress)
    typer.echo(json.dumps(copilot.summary(batch_id), indent=1))


@app.command()
def status(batch: Annotated[str | None, typer.Option()] = None) -> None:
    """Products per status and the open errors of a batch."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    typer.echo(json.dumps(copilot.summary(batch or copilot.db.latest_batch_id()), indent=1))


@app.command()
def approve(
    batch: Annotated[str | None, typer.Option()] = None,
    actor: Annotated[str, typer.Option(help="Who approves (recorded in the audit log)")] = "cli",
) -> None:
    """Bulk-approve the products that are `ready` (no open errors, no feed/photo conflicts)."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    result = copilot.bulk_approve(batch_id=batch or copilot.db.latest_batch_id(), actor=actor)
    typer.echo(f"approved {len(result['approved'])}, skipped {len(result['skipped'])}")


@app.command()
def export(
    out: Annotated[Path, typer.Option(help="Shopify products CSV")] = Path("exports/shopify_products.csv"),
    batch: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Export approved products as a Shopify CSV (+ a translations CSV next to it)."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    products, translations, n = copilot.export_csv(batch or copilot.db.latest_batch_id(), actor="cli")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(products, encoding="utf-8")
    out.with_name(out.stem + "_translations.csv").write_text(translations, encoding="utf-8")
    typer.echo(f"{n} approved products -> {out}")


@app.command()
def push(
    target: Annotated[str, typer.Argument(help="shopify or woocommerce (the local mocks)")],
    batch: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Push approved products to the mock Shopify Admin GraphQL API or the mock WooCommerce REST API."""
    from shelfwise.services import Copilot

    copilot = Copilot(get_settings())
    results = copilot.push(target, batch or copilot.db.latest_batch_id(), actor="cli")
    for r in results:
        typer.echo(
            f"{'ok ' if r.ok else 'ERR'} {r.handle} {r.remote_id} variants={r.variants} translations={r.translations} {'; '.join(r.errors)}"
        )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API (the dashboard talks to it)."""
    import uvicorn

    uvicorn.run("shelfwise.api.app:create_default_app", factory=True, host=host, port=port, reload=reload)


@app.command("mock-store")
def mock_store(host: str = "127.0.0.1", port: int = 8070) -> None:
    """Run the local Shopify GraphQL and WooCommerce REST mocks."""
    import uvicorn

    from shelfwise.mocks.app import create_mock_app

    uvicorn.run(create_mock_app(), host=host, port=port)


@app.command()
def mcp(http: bool = False, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the MCP server (stdio by default; --http for Streamable HTTP on 127.0.0.1)."""
    from shelfwise.mcp_server import serve as serve_mcp

    serve_mcp(http=http, host=host, port=port)


# ---------------------------------------------------------------- evaluation


def _eval_settings() -> Settings:
    settings = get_settings()
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    return settings


@eval_app.command("run")
def eval_run(
    name: Annotated[str, typer.Option()] = "main",
    limit: Annotated[int | None, typer.Option()] = None,
    translate: Annotated[bool, typer.Option()] = True,
    workers: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Import and enrich the 64 demo products with the configured provider and score everything."""
    from shelfwise.eval.runner import run_main

    report = run_main(_eval_settings(), name=name, limit=limit, translate=translate, workers=workers)
    typer.echo(json.dumps({k: report[k] for k in ("attributes", "listings", "throughput")}, indent=1)[:6000])


@eval_app.command("vision")
def eval_vision(
    model: Annotated[str, typer.Option()],
    name: Annotated[str, typer.Option()] = "vision_alt",
    provider: Annotated[str, typer.Option()] = "openrouter",
) -> None:
    """The vision step alone with another model."""
    from shelfwise.eval.runner import run_vision

    report = run_vision(_eval_settings(), name=name, model=model, provider=provider)
    typer.echo(json.dumps(report["attributes"], indent=1))


@eval_app.command("judge")
def eval_judge(name: str = "main", sample: int = 20, model: str | None = None) -> None:
    """An LLM judge re-checks a sample of listings for unsupported claims."""
    from shelfwise.eval.judge import run_judge

    report = run_judge(_eval_settings(), name=name, k=sample, model=model)
    typer.echo(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


@eval_app.command("backtranslate")
def eval_backtranslate(name: str = "main", sample: int = 10) -> None:
    """Back-translate a sample of translations into English and score them with chrF."""
    from shelfwise.eval.judge import run_backtranslation

    report = run_backtranslation(_eval_settings(), name=name, k=sample)
    typer.echo(json.dumps(report["by_language"], indent=1))


@eval_app.command("smoke")
def eval_smoke(models: Annotated[list[str], typer.Argument(help="Free vision model ids to try")]) -> None:
    """Try free vision models on six demo products (results/smoke_vision.json)."""
    from shelfwise.eval.smoke import list_free_models, vision_smoke, write

    settings = _eval_settings()
    report = vision_smoke(settings, models, settings.demo_dir)
    report["free_models"] = list_free_models(settings)
    write(report, settings.results_dir / "smoke_vision.json")
    for model, row in report["models"].items():
        typer.echo(
            f"{model}: {json.dumps({k: row[k] for k in ('answered', 'color', 'pattern', 'count', 'material', 'median_seconds')})}"
        )


@eval_app.command("summary")
def eval_summary() -> None:
    """Combine the result files into results/summary.json (read by the dashboard and the README)."""
    from shelfwise.eval.summary import build_summary

    summary = build_summary(_eval_settings())
    typer.echo(json.dumps(summary["headline"], indent=1))


if __name__ == "__main__":
    app()
