"""The application layer shared by the API, the CLI and the MCP server: import, enrich, review, export, push."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from shelfwise.catalog.ingest import FeedInput, display_title, import_feeds, preview_feed
from shelfwise.catalog.models import AttributeDecision, Evidence, FieldReview, Issue, ProductRecord
from shelfwise.config import Settings, StoreConfig, get_settings, get_store
from shelfwise.enrich.pipeline import Models, enrich, revalidate
from shelfwise.export import shopify_csv
from shelfwise.export.push import PushResult, ShopifyClient, WooClient
from shelfwise.llm.factory import model_for
from shelfwise.store import Store, now
from shelfwise.validate.listing import catalog_issues

log = logging.getLogger(__name__)

EDITABLE = {"title", "body_html", "meta_title", "meta_description", "product_type", "category_id", "tags", "bullets", "alt_texts"}
DEMO_FEEDS = (("larkspur_pricelist.xlsx", "Larkspur"), ("northbay_feed.csv", "Northbay"))


class ReviewError(ValueError):
    """An action the review rules do not allow (approving with open errors, editing an unknown field)."""


@dataclass
class ImportSummary:
    batch_id: str
    products: int
    duplicates_merged: int
    feeds: list[dict[str, Any]]
    issues: list[Issue]


class Copilot:
    def __init__(self, settings: Settings | None = None, store: Store | None = None, models: Models | None = None) -> None:
        self.settings = settings or get_settings()
        self.db = store or Store(self.settings.database)
        self.store_config: StoreConfig = get_store(self.settings)
        self._models = models
        self._jobs: dict[str, threading.Thread] = {}

    # ---------------------------------------------------------------- models
    @property
    def models(self) -> Models:
        if self._models is None:
            self._models = Models(
                vision=model_for(self.settings, "vision", tag="vision"),
                text=model_for(self.settings, "text", tag="copy"),
            )
        return self._models

    def provider_label(self) -> dict[str, str]:
        return {"provider": self.settings.llm_provider, "vision": self.models.vision.label, "text": self.models.text.label,
                "free_only": str(self.settings.free_only).lower()}

    # ---------------------------------------------------------------- import
    def preview(self, name: str, data: bytes) -> dict[str, Any]:
        header_row, header, body, mapping = preview_feed(Path(name), data)
        return {"file": name, "header_row": header_row + 1, "columns": header, "rows": len(body),
                "sample": body[:5], "mapping": [m.model_dump() for m in mapping]}

    def import_files(
        self,
        feeds: list[tuple[str, bytes, str | None, dict[str, str | None]]],
        images: list[tuple[str, bytes]] | None = None,
        *,
        name: str = "",
        images_dir: Path | None = None,
        actor: str = "system",
    ) -> ImportSummary:
        """feeds: (file name, bytes, vendor or None, column overrides)."""
        batch_dir = self.settings.workspace_dir / ("import-" + uuid.uuid4().hex[:8])
        (batch_dir / "feeds").mkdir(parents=True, exist_ok=True)
        inputs = []
        for fname, data, vendor, overrides in feeds:
            safe = _safe_name(fname)
            (batch_dir / "feeds" / safe).write_bytes(data)
            inputs.append(FeedInput(path=batch_dir / "feeds" / safe, data=data, vendor=vendor, overrides=overrides))
        if images:
            (batch_dir / "images").mkdir(exist_ok=True)
            for iname, data in images:
                (batch_dir / "images" / _safe_name(Path(iname).name)).write_bytes(data)
        result = import_feeds(inputs, self.store_config)
        img_dir = images_dir or (batch_dir / "images")
        batch_id = self.db.create_batch(
            name or ", ".join(f[0] for f in feeds),
            {"images_dir": str(img_dir), "feeds": [{"file": r.file, "header_row": r.header_row, "rows": r.rows,
                                                    "mapping": [m.model_dump() for m in r.mapping]} for r in result.feeds],
             "duplicates_merged": result.duplicates_merged},
        )
        for position, product in enumerate(result.products):
            record = ProductRecord(id=product.id, batch_id=batch_id, product=product)
            missing = [i for i in product.images if not (img_dir / i).exists()]
            if missing:
                product.issues.append(Issue(code="image_missing", severity="warning", field="images",
                                            message=f"Image file not uploaded: {', '.join(missing)}"))
            revalidate(record, self.store_config)
            self.db.save(record, position=position)
        self.db.log("import", {"batch_id": batch_id, "products": len(result.products), "duplicates_merged": result.duplicates_merged,
                               "files": [f[0] for f in feeds]}, actor=actor)
        return ImportSummary(batch_id, len(result.products), result.duplicates_merged,
                             [{"file": r.file, "header_row": r.header_row, "rows": r.rows} for r in result.feeds], result.issues)

    def import_demo(self) -> ImportSummary:
        demo = self.settings.demo_dir
        if not (demo / "feeds").exists():
            from shelfwise.synth.catalog import seed_catalog

            seed_catalog(demo)
        feeds = [(name, (demo / "feeds" / name).read_bytes(), vendor, {}) for name, vendor in DEMO_FEEDS]
        return self.import_files(feeds, name="Demo: Larkspur + Northbay supplier feeds", images_dir=demo / "images")

    def images_dir(self, batch_id: str) -> Path:
        batch = self.db.batch(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        return Path(batch["images_dir"])

    # ---------------------------------------------------------------- enrichment
    def enrich_one(self, product_id: str, *, translate: bool = True) -> ProductRecord:
        record = self._get(product_id)
        record = enrich(record, store=self.store_config, settings=self.settings, models=self.models,
                        images_dir=self.images_dir(record.batch_id), translate=translate)
        record.review = {}
        self.db.save(record)
        self.db.log("enrich", {"status": record.status, "calls": record.stats.calls, "errors": len(record.errors())},
                    product_id=product_id)
        return record

    def enrich_many(
        self, product_ids: list[str], *, translate: bool = True, progress: Callable[[int, int], None] | None = None
    ) -> list[ProductRecord]:
        done: list[ProductRecord] = []
        lock = threading.Lock()

        def work(pid: str) -> ProductRecord:
            try:
                record = self.enrich_one(pid, translate=translate)
            except Exception as exc:  # one broken product must not stop a batch
                log.exception("enrichment failed for %s", pid)
                record = self._get(pid)
                record.error = f"enrichment failed: {exc}"[:300]
                record.status = "needs_review"
                self.db.save(record)
            with lock:
                done.append(record)
                if progress:
                    progress(len(done), len(product_ids))
            return record

        with ThreadPoolExecutor(max_workers=max(1, self.settings.llm_concurrency)) as pool:
            list(pool.map(work, product_ids))
        batches = {r.batch_id for r in done}
        for batch_id in batches:
            self.catalog_checks(batch_id)
        return [self._get(pid) for pid in product_ids]

    def start_enrich_job(self, product_ids: list[str], *, translate: bool = True) -> str:
        job_id = "job_" + uuid.uuid4().hex[:8]
        state: dict[str, Any] = {"total": len(product_ids), "done": 0, "started_at": now(), "product_ids": product_ids[:500]}
        self.db.put_job(job_id, "enrich", "running", state)

        def progress(done: int, total: int) -> None:
            state["done"] = done
            self.db.put_job(job_id, "enrich", "running", state)

        def run() -> None:
            started = time.monotonic()
            try:
                self.enrich_many(product_ids, translate=translate, progress=progress)
                state["seconds"] = round(time.monotonic() - started, 1)
                self.db.put_job(job_id, "enrich", "done", state)
            except Exception as exc:
                state["error"] = str(exc)[:300]
                self.db.put_job(job_id, "enrich", "failed", state)

        thread = threading.Thread(target=run, daemon=True, name=job_id)
        self._jobs[job_id] = thread
        thread.start()
        return job_id

    def catalog_checks(self, batch_id: str) -> None:
        records = self.db.products(batch_id)
        for r in records:
            r.issues = [i for i in r.issues if i.code not in {"duplicate_title", "duplicate_handle", "near_duplicate_description"}]
        extra = catalog_issues(records)
        for r in records:
            if r.listing is None:
                continue
            r.issues += extra.get(r.id, [])
            if any(i.severity == "error" and not i.waived for i in extra.get(r.id, [])) and r.status in {"ready", "approved"}:
                r.status = "needs_review"
            self.db.save(r)

    # ---------------------------------------------------------------- review
    def _get(self, product_id: str) -> ProductRecord:
        record = self.db.get(product_id)
        if record is None:
            raise KeyError(product_id)
        return record

    def edit_field(self, product_id: str, field: str, value: Any, *, actor: str = "reviewer") -> ProductRecord:
        record = self._get(product_id)
        if record.listing is None:
            raise ReviewError("enrich the product before editing its listing")
        lang = None
        name = field
        if field.startswith("translations."):
            _, lang, name = field.split(".", 2)
        if name not in EDITABLE:
            raise ReviewError(f"field {field!r} cannot be edited")
        target: Any = record.listing.translations.get(lang) if lang else record.listing
        if target is None:
            raise ReviewError(f"no {lang} translation")
        old = getattr(target, name)
        if name in {"tags", "bullets", "alt_texts"}:
            value = [str(v).strip() for v in (value if isinstance(value, list) else str(value).split("\n")) if str(v).strip()]
        elif name == "category_id":
            value = int(value)
            from shelfwise.taxonomy import get

            category = get(value)
            if category is None:
                raise ReviewError(f"category {value} is not in the taxonomy")
            record.listing.category_path = category.path
        else:
            value = str(value)
        setattr(target, name, value)
        record.review[field] = FieldReview(state="edited", by=actor, at=now())
        if record.status in {"approved", "exported"}:
            record.status = "needs_review"
        revalidate(record, self.store_config, languages=list(record.listing.translations))
        self.db.save(record)
        self.db.log("edit", {"field": field, "old": _short(old), "new": _short(value)}, actor=actor, product_id=product_id)
        return record

    def resolve_attribute(self, product_id: str, attribute: str, value: Any, *, actor: str = "reviewer") -> ProductRecord:
        record = self._get(product_id)
        decision = record.decision(attribute)
        if decision is None:
            raise ReviewError(f"unknown attribute {attribute!r}")
        decision.value = int(value) if attribute == "count" and value is not None else value
        decision.status = "agree"
        decision.needs_review = False
        decision.note = f"set by {actor}"
        decision.evidence.append(Evidence(source="editor", ref=actor, quote=str(value)))
        record.decisions = [decision if d.attribute == attribute else d for d in record.decisions]
        revalidate(record, self.store_config, languages=list(record.listing.translations) if record.listing else [])
        self.db.save(record)
        self.db.log("resolve_attribute", {"attribute": attribute, "value": value}, actor=actor, product_id=product_id)
        return record

    def waive(self, product_id: str, code: str, field: str, reason: str, *, actor: str = "reviewer") -> ProductRecord:
        record = self._get(product_id)
        hits = [i for i in record.issues if i.code == code and i.field == field]
        if not hits:
            raise ReviewError(f"no issue {code} on {field!r}")
        if code.startswith(("banned_claim", "unsupported_")):
            raise ReviewError("claims without evidence cannot be waived: edit the text instead")
        for issue in hits:
            issue.waived = f"{actor}: {reason}"
        revalidate(record, self.store_config, languages=list(record.listing.translations) if record.listing else [])
        self.db.save(record)
        self.db.log("waive", {"code": code, "field": field, "reason": reason}, actor=actor, product_id=product_id)
        return record

    def approve(self, product_id: str, *, actor: str = "reviewer") -> ProductRecord:
        record = self._get(product_id)
        if record.listing is None:
            raise ReviewError("nothing to approve: the product has no listing yet")
        errors = record.errors()
        if errors:
            raise ReviewError(f"{len(errors)} open error(s): " + "; ".join(f"{e.field}: {e.message}" for e in errors[:3]))
        conflicts = [d.attribute for d in record.decisions if d.status == "conflict"]
        if conflicts:
            raise ReviewError(f"resolve the feed/photo conflicts first: {', '.join(conflicts)}")
        for field, _ in record.listing.text_fields(translations=True):
            if record.review.get(field, FieldReview()).state != "edited":
                record.review[field] = FieldReview(state="approved", by=actor, at=now())
        record.status = "approved"
        self.db.save(record)
        self.db.log("approve", {"title": record.listing.title}, actor=actor, product_id=product_id)
        return record

    def bulk_approve(self, product_ids: list[str] | None = None, *, batch_id: str | None = None, actor: str = "reviewer") -> dict[str, Any]:
        """Approves only products in `ready` state (no open errors, no conflicts); everything else is skipped."""
        records = [self._get(pid) for pid in product_ids] if product_ids else self.db.products(batch_id)
        approved, skipped = [], []
        for r in records:
            if r.status != "ready":
                skipped.append({"id": r.id, "status": r.status})
                continue
            try:
                self.approve(r.id, actor=actor)
                approved.append(r.id)
            except ReviewError as exc:
                skipped.append({"id": r.id, "status": r.status, "reason": str(exc)})
        self.db.log("bulk_approve", {"approved": len(approved), "skipped": len(skipped)}, actor=actor)
        return {"approved": approved, "skipped": skipped}

    def revert_field(self, product_id: str, field: str, *, actor: str = "reviewer") -> ProductRecord:
        record = self._get(product_id)
        if record.generated is None or record.listing is None:
            raise ReviewError("nothing to revert")
        lang, name = (field.split(".", 2)[1:] if field.startswith("translations.") else (None, field))
        source: Any = record.generated.translations.get(lang) if lang else record.generated
        target: Any = record.listing.translations.get(lang) if lang else record.listing
        setattr(target, name, getattr(source, name))
        record.review.pop(field, None)
        revalidate(record, self.store_config, languages=list(record.listing.translations))
        self.db.save(record)
        self.db.log("revert", {"field": field}, actor=actor, product_id=product_id)
        return record

    # ---------------------------------------------------------------- export and push
    def approved(self, batch_id: str | None = None) -> list[ProductRecord]:
        return [r for r in self.db.products(batch_id) if r.status in {"approved", "exported"}]

    def image_base_url(self) -> str:
        return "http://localhost:8000/api/images"

    def export_csv(self, batch_id: str | None = None, *, actor: str = "reviewer") -> tuple[str, str, int]:
        records = self.approved(batch_id)
        products_csv = shopify_csv.to_csv(records, image_base_url=self.image_base_url())
        translations = shopify_csv.translations_csv(records)
        self.db.log("export_csv", {"products": len(records)}, actor=actor)
        return products_csv, translations, len(records)

    def _mock_credentials(self) -> dict[str, str]:
        base = self.settings.shopify_url.rsplit("/shopify", 1)[0]
        response = httpx.get(f"{base}/credentials", timeout=10)
        response.raise_for_status()
        data: dict[str, str] = response.json()
        return data

    def push(self, target: str, batch_id: str | None = None, *, client: httpx.Client | None = None,
             credentials: dict[str, str] | None = None, actor: str = "reviewer") -> list[PushResult]:
        records = self.approved(batch_id)
        creds = credentials or self._mock_credentials()
        results: list[PushResult] = []
        if target == "shopify":
            shop = ShopifyClient(self.settings.shopify_url, self.settings.shopify_token or creds["shopify_token"],
                                 self.settings.shopify_api_version, client=client)
            results = [shop.push(r, image_base_url=self.image_base_url()) for r in records]
        elif target == "woocommerce":
            woo = WooClient(self.settings.woocommerce_url, creds["woocommerce_key"], creds["woocommerce_secret"], client=client)
            results = [woo.push(r, image_base_url=self.image_base_url()) for r in records]
        else:
            raise ValueError(f"unknown target {target!r}")
        for r, res in zip(records, results, strict=True):
            if res.ok:
                r.status = "exported"
                self.db.save(r)
        self.db.log("push", {"target": target, "ok": sum(r.ok for r in results), "failed": sum(not r.ok for r in results)}, actor=actor)
        return results

    # ---------------------------------------------------------------- views
    def summary(self, batch_id: str | None = None) -> dict[str, Any]:
        records = self.db.products(batch_id)
        counts: dict[str, int] = {}
        for r in records:
            counts[r.status] = counts.get(r.status, 0) + 1
        issues: dict[str, int] = {}
        for r in records:
            for i in r.issues:
                if i.severity == "error" and not i.waived:
                    issues[i.code] = issues.get(i.code, 0) + 1
        return {"products": len(records), "status": counts, "open_errors": issues,
                "conflicts": sum(1 for r in records for d in r.decisions if d.status == "conflict"),
                "calls": sum(r.stats.calls for r in records)}

    def card(self, r: ProductRecord) -> dict[str, Any]:
        listing = r.listing
        return {
            "id": r.id,
            "status": r.status,
            "handle": r.product.handle,
            "name": display_title(r.product),
            "title": listing.title if listing else "",
            "title_raw": r.product.title_raw,
            "vendor": r.product.vendor,
            "image": r.product.images[0] if r.product.images else None,
            "product_type": listing.product_type if listing else "",
            "variants": len(r.product.variants),
            "errors": len(r.errors()),
            "warnings": sum(1 for i in r.issues if i.severity == "warning" and not i.waived),
            "conflicts": [d.attribute for d in r.decisions if d.status == "conflict"],
            "attributes": {d.attribute: d.value for d in r.decisions if d.value is not None},
            "languages": sorted(listing.translations) if listing else [],
            "supplier": r.product.supplier_file,
        }


def _safe_name(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120] or "file"


def _short(value: Any) -> Any:
    text = str(value)
    return text if len(text) <= 300 else text[:297] + "..."


def decision_dict(d: AttributeDecision) -> dict[str, Any]:
    return d.model_dump()
