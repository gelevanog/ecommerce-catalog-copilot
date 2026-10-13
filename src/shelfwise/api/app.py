"""The HTTP API behind the review dashboard. The mock Shopify and WooCommerce APIs are mounted under /mock."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from shelfwise import __version__
from shelfwise.catalog.ingest import display_title
from shelfwise.catalog.models import ProductRecord
from shelfwise.config import Settings, get_settings
from shelfwise.mocks.app import create_mock_app
from shelfwise.services import Copilot, ReviewError
from shelfwise.taxonomy import candidates, get, taxonomy_source

log = logging.getLogger(__name__)


class FieldEdit(BaseModel):
    field: str
    value: Any
    actor: str = "reviewer"


class FieldRef(BaseModel):
    field: str
    actor: str = "reviewer"


class AttributeChoice(BaseModel):
    attribute: str
    value: Any
    actor: str = "reviewer"


class Waiver(BaseModel):
    code: str
    field: str
    reason: str
    actor: str = "reviewer"


class Actor(BaseModel):
    actor: str = "reviewer"


class EnrichRequest(BaseModel):
    product_ids: list[str] | None = None
    batch_id: str | None = None
    translate: bool = True


class BulkApprove(BaseModel):
    product_ids: list[str] | None = None
    batch_id: str | None = None
    actor: str = "reviewer"


def seed(copilot: Copilot) -> None:
    """First start: import the demo feeds; load the listings of the real-model evaluation run when present."""
    if copilot.db.batches():
        return
    summary = copilot.import_demo()
    records_file = copilot.settings.results_dir / "main_records.jsonl"
    loaded = 0
    if records_file.exists():
        stored = {r.id: r for r in (ProductRecord.model_validate_json(line) for line in records_file.read_text(encoding="utf-8").splitlines() if line.strip())}
        for record in copilot.db.products(summary.batch_id):
            source = stored.get(record.id)
            if source is None or source.listing is None:
                continue
            source.batch_id = summary.batch_id
            copilot.db.save(source)
            loaded += 1
        copilot.db.log("seed", {"loaded_from": str(records_file.name), "products": loaded})
    log.info("demo seeded: %d products, %d listings from the evaluation run", summary.products, loaded)


def create_app(settings: Settings | None = None, copilot: Copilot | None = None) -> FastAPI:
    settings = settings or get_settings()
    cp = copilot or Copilot(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        if settings.seed_demo:
            seed(cp)
        yield

    app = FastAPI(title="Shelfwise API", version=__version__, lifespan=lifespan)
    app.state.copilot = cp
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.mount("/mock", create_mock_app())

    def guard(fn: Any) -> Any:
        try:
            return fn()
        except KeyError as exc:
            raise HTTPException(404, f"not found: {exc}") from exc
        except ReviewError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        s = cp.store_config
        return {
            "store": s.name, "currency": s.currency, "languages": s.languages, "product_types": s.product_types,
            "limits": {"title": [s.title.min, s.title.max], "meta_title": [s.meta_title.min, s.meta_title.max],
                       "meta_description": [s.meta_description.min, s.meta_description.max],
                       "bullets": [s.bullets_count.min, s.bullets_count.max, s.bullet_max_chars],
                       "body_words": [s.body_words.min, s.body_words.max], "alt_text": s.alt_text_max},
            "models": cp.provider_label(), "taxonomy": taxonomy_source(),
            "voice": s.style.get("voice", ""), "banned": sorted({b.reason for b in s.banned}),
        }

    @app.get("/api/batches")
    def batches() -> list[dict[str, Any]]:
        return [{k: v for k, v in b.items() if k != "feeds"} | {"feeds": [{k: f[k] for k in ("file", "rows", "header_row")} for f in b.get("feeds", [])]}
                for b in cp.db.batches()]

    @app.get("/api/batches/{batch_id}")
    def batch(batch_id: str) -> dict[str, Any]:
        b = cp.db.batch(batch_id)
        if b is None:
            raise HTTPException(404, "batch not found")
        return b | {"summary": cp.summary(batch_id)}

    @app.post("/api/import/preview")
    async def preview(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        data = await file.read()
        return guard(lambda: cp.preview(file.filename or "feed.csv", data))

    @app.post("/api/import")
    async def import_feeds(
        feeds: Annotated[list[UploadFile], File()],
        images: Annotated[list[UploadFile] | None, File()] = None,
        vendors: Annotated[str, Form()] = "{}",
        overrides: Annotated[str, Form()] = "{}",
        name: Annotated[str, Form()] = "",
    ) -> dict[str, Any]:
        vendor_map: dict[str, str] = json.loads(vendors or "{}")
        override_map: dict[str, dict[str, str | None]] = json.loads(overrides or "{}")
        files = [(f.filename or "feed.csv", await f.read(), vendor_map.get(f.filename or "") or None, override_map.get(f.filename or "", {}))
                 for f in feeds]
        imgs = [(i.filename or "image.jpg", await i.read()) for i in images or []]
        summary = guard(lambda: cp.import_files(files, imgs, name=name, actor="reviewer"))
        return {"batch_id": summary.batch_id, "products": summary.products, "duplicates_merged": summary.duplicates_merged,
                "feeds": summary.feeds, "issues": [i.model_dump() for i in summary.issues]}

    @app.post("/api/import/demo")
    def import_demo() -> dict[str, Any]:
        summary = cp.import_demo()
        return {"batch_id": summary.batch_id, "products": summary.products, "duplicates_merged": summary.duplicates_merged}

    @app.get("/api/products")
    def products(batch_id: str | None = None, status: str | None = None, q: str | None = None) -> dict[str, Any]:
        bid = batch_id or cp.db.latest_batch_id()
        records = cp.db.products(bid)
        counts: dict[str, int] = {}
        for r in records:
            counts[r.status] = counts.get(r.status, 0) + 1
        if status:
            records = [r for r in records if r.status == status]
        if q:
            needle = q.lower()
            records = [r for r in records if needle in (r.listing.title.lower() if r.listing else "") or needle in r.product.title_raw.lower()
                       or needle in r.product.handle]
        return {"batch_id": bid, "counts": counts, "total": sum(counts.values()), "products": [cp.card(r) for r in records]}

    @app.get("/api/products/{product_id}")
    def product(product_id: str) -> dict[str, Any]:
        record = guard(lambda: cp._get(product_id))
        name = display_title(record.product)
        noun = record.image_attributes[0].product_noun if record.image_attributes else ""
        cands = [{"id": c.id, "path": c.path} for c in candidates(f"{name} {record.product.supplier_category} {noun}")]
        category = get(record.listing.category_id) if record.listing else None
        return {"record": record.model_dump(), "name": name, "category_candidates": cands,
                "category": {"id": category.id, "path": category.path} if category else None,
                "events": cp.db.events(product_id, limit=50)}

    @app.post("/api/products/{product_id}/enrich")
    def enrich_one(product_id: str, translate: bool = True) -> dict[str, Any]:
        record = guard(lambda: cp.enrich_one(product_id, translate=translate))
        cp.catalog_checks(record.batch_id)
        return cp._get(product_id).model_dump()

    @app.post("/api/enrich")
    def enrich_many(request: EnrichRequest) -> dict[str, Any]:
        ids = request.product_ids or [r.id for r in cp.db.products(request.batch_id or cp.db.latest_batch_id())]
        return {"job_id": cp.start_enrich_job(ids, translate=request.translate), "products": len(ids)}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        j = cp.db.job(job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return j

    @app.patch("/api/products/{product_id}/fields")
    def edit(product_id: str, edit: FieldEdit) -> dict[str, Any]:
        return guard(lambda: cp.edit_field(product_id, edit.field, edit.value, actor=edit.actor)).model_dump()

    @app.post("/api/products/{product_id}/revert")
    def revert(product_id: str, ref: FieldRef) -> dict[str, Any]:
        return guard(lambda: cp.revert_field(product_id, ref.field, actor=ref.actor)).model_dump()

    @app.post("/api/products/{product_id}/attributes")
    def attribute(product_id: str, choice: AttributeChoice) -> dict[str, Any]:
        return guard(lambda: cp.resolve_attribute(product_id, choice.attribute, choice.value, actor=choice.actor)).model_dump()

    @app.post("/api/products/{product_id}/waive")
    def waive(product_id: str, waiver: Waiver) -> dict[str, Any]:
        return guard(lambda: cp.waive(product_id, waiver.code, waiver.field, waiver.reason, actor=waiver.actor)).model_dump()

    @app.post("/api/products/{product_id}/approve")
    def approve(product_id: str, actor: Annotated[Actor, Body()] = Actor()) -> dict[str, Any]:
        return guard(lambda: cp.approve(product_id, actor=actor.actor)).model_dump()

    @app.post("/api/approve/bulk")
    def bulk(request: BulkApprove) -> dict[str, Any]:
        return cp.bulk_approve(request.product_ids, batch_id=request.batch_id or cp.db.latest_batch_id(), actor=request.actor)

    @app.get("/api/export/shopify.csv")
    def export_csv(batch_id: str | None = None) -> PlainTextResponse:
        products_csv, _, _ = cp.export_csv(batch_id or cp.db.latest_batch_id())
        return PlainTextResponse(products_csv, media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=shopify_products.csv"})

    @app.get("/api/export/translations.csv")
    def export_translations(batch_id: str | None = None) -> PlainTextResponse:
        _, translations, _ = cp.export_csv(batch_id or cp.db.latest_batch_id())
        return PlainTextResponse(translations, media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=shopify_translations.csv"})

    @app.get("/api/export/preview")
    def export_preview(batch_id: str | None = None) -> dict[str, Any]:
        bid = batch_id or cp.db.latest_batch_id()
        records = cp.approved(bid)
        text, translations, n = cp.export_csv(bid) if records else ("", "", 0)
        return {"approved": n, "rows": max(0, text.count("\n") - 1), "csv_head": "\n".join(text.splitlines()[:6]),
                "translation_rows": max(0, translations.count("\n") - 1)}

    @app.post("/api/push/{target}")
    def push(target: str, batch_id: str | None = None) -> dict[str, Any]:
        results = guard(lambda: cp.push(target, batch_id or cp.db.latest_batch_id()))
        return {"target": target, "ok": sum(r.ok for r in results), "failed": sum(not r.ok for r in results),
                "results": [r.__dict__ for r in results]}

    @app.get("/api/events")
    def events(product_id: str | None = None, limit: int = Query(200, le=1000)) -> list[dict[str, Any]]:
        return cp.db.events(product_id, limit=limit)

    @app.get("/api/taxonomy/search")
    def taxonomy(q: str, k: int = 12) -> list[dict[str, Any]]:
        return [{"id": c.id, "path": c.path} for c in candidates(q, k=k)]

    @app.get("/api/images/{name}")
    def image(name: str) -> FileResponse:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(400, "bad image name")
        dirs = [Path(b["images_dir"]) for b in cp.db.batches() if b.get("images_dir")] + [settings.demo_dir / "images"]
        for directory in dirs:
            path = directory / name
            if path.is_file():
                return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
        raise HTTPException(404, "image not found")

    @app.get("/api/evaluation")
    def evaluation() -> dict[str, Any]:
        path = settings.results_dir / "summary.json"
        if not path.exists():
            raise HTTPException(404, "no evaluation summary: run `shelfwise eval summary`")
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    return app


def create_default_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return create_app()
