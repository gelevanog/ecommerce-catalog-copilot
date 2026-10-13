"""Human review rules, the Shopify CSV export and pushes to the mock Shopify and WooCommerce APIs."""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from shelfwise.catalog.models import ProductRecord
from shelfwise.export import shopify_csv
from shelfwise.export.push import ShopifyClient, WooClient
from shelfwise.mocks.app import create_mock_app
from shelfwise.mocks.shopify import ShopifyMock
from shelfwise.services import Copilot, ReviewError


def ready(enriched: list[ProductRecord]) -> ProductRecord:
    return next(r for r in enriched if r.status == "ready")


def test_approval_needs_no_open_errors(copilot: Copilot, enriched: list[ProductRecord]) -> None:
    blocked = next(r for r in enriched if r.status == "blocked")
    with pytest.raises(ReviewError, match="open error"):
        copilot.approve(blocked.id)
    approved = copilot.approve(ready(enriched).id, actor="ana")
    assert approved.status == "approved"
    assert all(v.state == "approved" and v.by == "ana" for v in approved.review.values())
    assert copilot.db.events(approved.id)[0]["action"] == "approve"


def test_editing_revalidates_and_resets_approval(copilot: Copilot, enriched: list[ProductRecord]) -> None:
    record = copilot.approve(ready(enriched).id)
    edited = copilot.edit_field(
        record.id, "bullets", ["Holds 2 litres", "Dishwasher safe", "The best mug"], actor="ana"
    )
    assert edited.status == "needs_review"
    codes = {i.code for i in edited.issues}
    assert "banned_claim" in codes
    assert edited.review["bullets"].state == "edited"
    reverted = copilot.revert_field(record.id, "bullets")
    assert "banned_claim" not in {i.code for i in reverted.issues}


def test_unknown_fields_and_bad_categories_are_refused(copilot: Copilot, enriched: list[ProductRecord]) -> None:
    pid = ready(enriched).id
    with pytest.raises(ReviewError):
        copilot.edit_field(pid, "price", "1.00")
    with pytest.raises(ReviewError):
        copilot.edit_field(pid, "category_id", 123456789)
    record = copilot.edit_field(pid, "category_id", 2169)
    assert record.listing is not None and record.listing.category_path.endswith("Mugs")


def test_waivers_cannot_hide_unsupported_claims(copilot: Copilot, enriched: list[ProductRecord]) -> None:
    pid = ready(enriched).id
    copilot.edit_field(pid, "meta_description", "Holds 3 litres.")
    with pytest.raises(ReviewError, match="cannot be waived"):
        copilot.waive(pid, "unsupported_spec", "meta_description", "looks fine")
    blocked = next(r for r in enriched if r.status == "blocked")
    issue = next(i for i in blocked.issues if i.code.startswith("variant_"))
    waived = copilot.waive(blocked.id, issue.code, issue.field, "price confirmed with the supplier", actor="ana")
    assert all(i.waived for i in waived.issues if i.code == issue.code)


def test_resolving_a_conflict(copilot: Copilot, enriched: list[ProductRecord]) -> None:
    record = enriched[0]
    resolved = copilot.resolve_attribute(record.id, "color", "red", actor="ana")
    d = resolved.decision("color")
    assert d is not None and d.value == "red" and d.status == "agree"
    assert d.evidence[-1].source == "editor"


def test_bulk_approve_only_takes_ready_products(
    copilot: Copilot, enriched: list[ProductRecord], demo_batch: str
) -> None:
    result = copilot.bulk_approve(batch_id=demo_batch)
    statuses = {r.id: r.status for r in enriched}
    assert set(result["approved"]) == {pid for pid, s in statuses.items() if s == "ready"}
    assert all(statuses[s["id"]] != "ready" for s in result["skipped"])


def test_export_contains_only_approved_products(
    copilot: Copilot, enriched: list[ProductRecord], demo_batch: str
) -> None:
    with pytest.raises(shopify_csv.NotApprovedError):
        shopify_csv.to_csv([ready(enriched)], image_base_url="http://x")
    copilot.bulk_approve(batch_id=demo_batch)
    text, translations, n = copilot.export_csv(demo_batch)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0].keys() >= {
        "Handle",
        "Title",
        "Body (HTML)",
        "Variant SKU",
        "SEO Title",
        "Google Shopping / Google Product Category",
    }
    handles = {r["Handle"] for r in rows}
    assert len(handles) == n
    sized = [r for r in rows if r["Option1 Name"] == "Size"]
    if sized:
        handle = sized[0]["Handle"]
        assert sum(1 for r in rows if r["Handle"] == handle) > 1
    assert all(r["Title"] for r in rows if r["Option1 Name"])
    trows = list(csv.reader(io.StringIO(translations)))
    assert trows[0] == shopify_csv.TRANSLATION_COLUMNS
    assert {r[3] for r in trows[1:]} == {"de", "es", "fr"}


def test_push_to_mock_shopify_and_woocommerce(copilot: Copilot, enriched: list[ProductRecord], demo_batch: str) -> None:
    copilot.bulk_approve(batch_id=demo_batch)
    app = create_mock_app()
    client = TestClient(app)
    copilot.settings.shopify_url = "http://testserver/shopify"
    copilot.settings.woocommerce_url = "http://testserver/woocommerce"
    shop = copilot.push("shopify", demo_batch, client=client)
    assert shop and all(r.ok for r in shop), [r.errors for r in shop if not r.ok]
    assert all(r.translations == 12 for r in shop)
    assert len(app.state.shopify.products) == len(shop)
    woo = copilot.push("woocommerce", demo_batch, client=client)
    assert all(r.ok for r in woo)
    assert len(app.state.woo.products) == len(woo)
    assert {
        r.status for r in copilot.db.products(demo_batch) if r.id in {x.id for x in enriched if x.status == "ready"}
    } == {"exported"}


def test_shopify_mock_auth_and_user_errors() -> None:
    mock = ShopifyMock()
    client = TestClient(create_mock_app(shopify=mock))
    bad = ShopifyClient("http://testserver/shopify", "wrong-token", client=client)
    with pytest.raises(RuntimeError, match="401"):
        bad.call("{ products { nodes { id } } }", {})
    good = ShopifyClient("http://testserver/shopify", mock.token, client=client)
    data = good.call(
        "mutation($i: ProductSetInput!) { productSet(input: $i) { product { id } userErrors { message code } } }",
        {"i": {"title": "", "variants": []}},
    )
    assert data["productSet"]["userErrors"][0]["code"] == "BLANK"
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        good.call("{ nope }", {})


def test_shopify_translation_digests_are_checked() -> None:
    mock = ShopifyMock()
    product = mock.product_set({"title": "A Mug", "descriptionHtml": "<p>x</p>", "variants": []})["product"]
    result = mock.register(
        product["id"], [{"locale": "de", "key": "title", "value": "Ein Becher", "translatableContentDigest": "stale"}]
    )
    assert result["userErrors"][0]["code"] == "INVALID_TRANSLATABLE_CONTENT"


def test_woocommerce_mock_rejects_bad_credentials_and_duplicate_skus() -> None:
    app = create_mock_app()
    client = TestClient(app)
    woo = app.state.woo
    response = client.get("/woocommerce/wp-json/wc/v3/products", auth=("ck_wrong", "cs_wrong"))
    assert response.status_code == 401
    good = WooClient("http://testserver/woocommerce", woo.consumer_key, woo.consumer_secret, client=client)
    assert good.client.post(f"{good.base}/products", json={"name": "A", "sku": "S1"}, auth=good.auth).status_code == 201
    dup = good.client.post(f"{good.base}/products", json={"name": "B", "sku": "S1"}, auth=good.auth)
    assert dup.status_code == 400 and dup.json()["code"] == "product_invalid_sku"


def test_mock_credentials_are_local_only() -> None:
    client = TestClient(create_mock_app())
    assert set(client.get("/credentials").json()) == {"shopify_token", "woocommerce_key", "woocommerce_secret"}
