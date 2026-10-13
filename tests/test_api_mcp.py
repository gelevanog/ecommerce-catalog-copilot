"""The HTTP API (with the demo seeded on start) and the MCP server through the SDK's in-memory client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from shelfwise.api.app import create_app
from shelfwise.config import Settings
from shelfwise.mcp_server import create_server
from shelfwise.services import Copilot
from shelfwise.store import Store

from .conftest import DEMO


@pytest.fixture()
def api(settings: Settings) -> Iterator[TestClient]:
    s = settings.model_copy(
        update={
            "seed_demo": True,
            "shopify_url": "http://testserver/mock/shopify",
            "woocommerce_url": "http://testserver/mock/woocommerce",
        }
    )
    copilot = Copilot(s, store=Store(":memory:"))
    with TestClient(create_app(s, copilot)) as client:
        yield client


def test_seeded_catalog_and_filters(api: TestClient) -> None:
    data = api.get("/api/products").json()
    assert data["total"] == 64
    assert data["counts"] == {"imported": 60, "blocked": 4}
    blocked = api.get("/api/products", params={"status": "blocked"}).json()["products"]
    assert len(blocked) == 4 and all(p["errors"] for p in blocked)
    assert api.get("/api/products", params={"q": "mug"}).json()["products"]


def test_enrich_review_approve_export(api: TestClient) -> None:
    products = api.get("/api/products").json()["products"]
    pid = next(p["id"] for p in products if p["status"] == "imported")
    record = api.post(f"/api/products/{pid}/enrich").json()
    assert record["listing"]["title"]
    detail = api.get(f"/api/products/{pid}").json()
    assert detail["category_candidates"] and detail["events"]
    if record["status"] == "ready":
        assert api.post(f"/api/products/{pid}/approve", json={"actor": "ana"}).json()["status"] == "approved"
        csv_text = api.get("/api/export/shopify.csv").text
        assert record["product"]["handle"] in csv_text
        push = api.post("/api/push/shopify").json()
        assert push["ok"] == 1
    edit = api.patch(f"/api/products/{pid}/fields", json={"field": "title", "value": "The best mug ever"})
    assert edit.status_code == 200
    assert any(i["code"] == "banned_claim" for i in edit.json()["issues"])
    assert api.post(f"/api/products/{pid}/approve", json={}).status_code == 409


def test_import_preview_and_upload(api: TestClient) -> None:
    feed = (DEMO / "feeds" / "northbay_feed.csv").read_bytes()
    preview = api.post("/api/import/preview", files={"file": ("northbay_feed.csv", feed, "text/csv")}).json()
    assert preview["rows"] == 46 and {m["target"] for m in preview["mapping"]} >= {"title", "price", "gtin"}
    result = api.post(
        "/api/import",
        files=[("feeds", ("northbay_feed.csv", feed, "text/csv"))],
        data={"vendors": '{"northbay_feed.csv": "Northbay"}', "name": "upload"},
    ).json()
    assert result["products"] == 35  # 32 Northbay products + 3 rows that repeat Larkspur products


def test_images_config_and_errors(api: TestClient) -> None:
    card = api.get("/api/products").json()["products"][0]
    assert api.get(f"/api/images/{card['image']}").headers["content-type"] == "image/jpeg"
    assert api.get("/api/images/..%2Fsecrets").status_code in {400, 404}
    assert api.get("/api/products/nope").status_code == 404
    config = api.get("/api/config").json()
    assert config["languages"] == ["de", "es", "fr"] and config["models"]["provider"] == "fake"
    assert api.get("/api/taxonomy/search", params={"q": "mug"}).json()[0]["id"] == 2169
    assert api.get("/mock/health").json()["ok"]


# ---------------------------------------------------------------- MCP


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
async def mcp_client(settings: Settings) -> AsyncIterator[tuple[Client, Copilot]]:
    copilot = Copilot(settings, store=Store(":memory:"))
    async with Client(create_server(copilot=copilot), raise_exceptions=True) as client:
        yield client, copilot


async def call(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@pytest.mark.anyio
async def test_mcp_tools_listed(mcp_client: tuple[Client, Copilot]) -> None:
    client, _ = mcp_client
    names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "import_feed",
        "list_products",
        "get_product",
        "enrich_product",
        "validate_listing",
        "check_claims",
        "save_listing",
        "export_shopify_csv",
    }


@pytest.mark.anyio
async def test_mcp_claude_as_copywriter(mcp_client: tuple[Client, Copilot]) -> None:
    client, _ = mcp_client
    imported = await call(client, "import_feed", {"demo": True})
    assert imported["products"] == 64 and imported["duplicates_merged"] == 6
    listing = await call(client, "list_products", {"status": "imported"})
    pid = listing["products"][0]["id"]
    product = await call(client, "get_product", {"product_id": pid})
    assert product["facts"] and product["category_candidates"]
    draft = {
        "title": f"{product['vendor']} {product['name']} – Best Seller",
        "body_html": "<p>Made in Narnia, 9000 ml.</p>",
        "bullets": ["a", "b", "c"],
        "meta_title": "x",
        "meta_description": "y",
        "product_type": product["product_types"][0],
        "category_id": product["category_candidates"][0]["id"],
        "alt_texts": ["photo"],
    }
    checked = await call(client, "validate_listing", {"product_id": pid, "listing": draft})
    codes = {i["code"] for i in checked["issues"]}
    assert not checked["ok"] and {"banned_claim", "unsupported_claim", "unsupported_spec", "too_short"} <= codes
    refused = await client.call_tool("save_listing", {"product_id": pid, "listing": draft})
    assert refused.is_error
    claims = await call(client, "check_claims", {"product_id": pid, "text": "Guaranteed for life."})
    assert not claims["ok"]


@pytest.mark.anyio
async def test_mcp_enrich_and_export_only_approved(mcp_client: tuple[Client, Copilot]) -> None:
    client, copilot = mcp_client
    await call(client, "import_feed", {"demo": True})
    pid = (await call(client, "list_products", {"status": "imported"}))["products"][0]["id"]
    enriched = await call(client, "enrich_product", {"product_id": pid, "translate": False})
    assert enriched["listing"]["title"]
    exported = await call(client, "export_shopify_csv", {})
    assert exported["approved_exported"] == 0 and exported["not_approved_skipped"] == 64
    if enriched["status"] == "ready":
        copilot.approve(pid)
        exported = await call(client, "export_shopify_csv", {})
        assert exported["approved_exported"] == 1
