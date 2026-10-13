"""Pushing approved products to a store: Shopify Admin GraphQL (`productSet`, then `translationsRegister`) and
WooCommerce REST (variable product + variations batch). In this repository both point at the local mocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from shelfwise.catalog.models import ProductRecord

PRODUCT_SET = """
mutation productSet($input: ProductSetInput!) {
  productSet(input: $input, synchronous: true) {
    product { id handle title status variants(first: 100) { nodes { id sku price } } }
    userErrors { field message code }
  }
}"""
TRANSLATABLE = """
query translatable($id: ID!) {
  translatableResource(resourceId: $id) { resourceId translatableContent { key value digest locale } }
}"""
REGISTER = """
mutation register($id: ID!, $translations: [TranslationInput!]!) {
  translationsRegister(resourceId: $id, translations: $translations) {
    translations { locale key }
    userErrors { field message code }
  }
}"""


@dataclass
class PushResult:
    handle: str
    ok: bool
    remote_id: str = ""
    variants: int = 0
    translations: int = 0
    errors: list[str] = field(default_factory=list)


def product_set_input(record: ProductRecord, *, image_base_url: str) -> dict[str, Any]:
    p, listing = record.product, record.listing
    assert listing is not None
    option = next(iter(p.variants[0].options), None) if p.variants else None
    variants = []
    for v in p.variants:
        values = (
            [{"optionName": option, "name": v.options[option]}]
            if option
            else [{"optionName": "Title", "name": "Default Title"}]
        )
        variants.append(
            {
                "optionValues": values,
                "price": f"{v.price:.2f}" if v.price is not None else None,
                "barcode": (v.gtin[1:] if v.gtin and len(v.gtin) == 14 and v.gtin.startswith("0") else v.gtin),
                "inventoryItem": {
                    "sku": v.sku,
                    "tracked": True,
                    "measurement": {"weight": {"value": float(v.weight_g), "unit": "GRAMS"}} if v.weight_g else None,
                },
            }
        )
    options = (
        [{"name": option, "values": [{"name": v.options[option]} for v in p.variants]}]
        if option
        else [{"name": "Title", "values": [{"name": "Default Title"}]}]
    )
    return {
        "handle": p.handle,
        "title": listing.title,
        "descriptionHtml": listing.body_html,
        "vendor": p.vendor or "",
        "productType": listing.product_type,
        "tags": listing.tags,
        "status": "ACTIVE",
        "seo": {"title": listing.meta_title, "description": listing.meta_description},
        "productOptions": options,
        "variants": variants,
        "files": [
            {
                "originalSource": image_base_url.rstrip("/") + "/" + img,
                "alt": listing.alt_texts[i] if i < len(listing.alt_texts) else "",
                "contentType": "IMAGE",
            }
            for i, img in enumerate(p.images)
        ],
    }


class ShopifyClient:
    def __init__(
        self, base_url: str, token: str, api_version: str = "2026-07", *, client: httpx.Client | None = None
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/admin/api/{api_version}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        self.client = client or httpx.Client(timeout=30)

    def call(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(self.url, json={"query": query, "variables": variables}, headers=self.headers)
        if response.status_code >= 400:
            raise RuntimeError(f"Shopify {response.status_code}: {response.text[:200]}")
        data: dict[str, Any] = response.json()
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        result: dict[str, Any] = data["data"]
        return result

    def push(self, record: ProductRecord, *, image_base_url: str) -> PushResult:
        handle = record.product.handle
        try:
            data = self.call(PRODUCT_SET, {"input": product_set_input(record, image_base_url=image_base_url)})[
                "productSet"
            ]
        except RuntimeError as exc:
            return PushResult(handle, False, errors=[str(exc)])
        if data["userErrors"]:
            return PushResult(handle, False, errors=[e["message"] for e in data["userErrors"]])
        product = data["product"]
        result = PushResult(handle, True, remote_id=product["id"], variants=len(product["variants"]["nodes"]))
        listing = record.listing
        assert listing is not None
        if listing.translations:
            resource = self.call(TRANSLATABLE, {"id": product["id"]})["translatableResource"]
            digests = {c["key"]: c["digest"] for c in resource["translatableContent"]}
            translations = []
            for locale, t in listing.translations.items():
                for key, value in (
                    ("title", t.title),
                    ("body_html", t.body_html),
                    ("meta_title", t.meta_title),
                    ("meta_description", t.meta_description),
                ):
                    if value:
                        translations.append(
                            {"locale": locale, "key": key, "value": value, "translatableContentDigest": digests[key]}
                        )
            registered = self.call(REGISTER, {"id": product["id"], "translations": translations})[
                "translationsRegister"
            ]
            result.translations = len(registered["translations"] or [])
            result.errors += [e["message"] for e in registered["userErrors"]]
        return result


class WooClient:
    def __init__(self, base_url: str, key: str, secret: str, *, client: httpx.Client | None = None) -> None:
        self.base = f"{base_url.rstrip('/')}/wp-json/wc/v3"
        self.auth = (key, secret)
        self.client = client or httpx.Client(timeout=30)

    def push(self, record: ProductRecord, *, image_base_url: str) -> PushResult:
        p, listing = record.product, record.listing
        assert listing is not None
        option = next(iter(p.variants[0].options), None) if p.variants else None
        body: dict[str, Any] = {
            "name": listing.title,
            "slug": p.handle,
            "type": "variable" if option else "simple",
            "status": "publish",
            "description": listing.body_html,
            "short_description": "<ul>" + "".join(f"<li>{b}</li>" for b in listing.bullets) + "</ul>",
            "categories": [{"name": listing.product_type}],
            "tags": [{"name": t} for t in listing.tags],
            "images": [
                {
                    "src": image_base_url.rstrip("/") + "/" + img,
                    "alt": listing.alt_texts[i] if i < len(listing.alt_texts) else "",
                }
                for i, img in enumerate(p.images)
            ],
            "meta_data": [
                {"key": "_yoast_wpseo_title", "value": listing.meta_title},
                {"key": "_yoast_wpseo_metadesc", "value": listing.meta_description},
                {"key": "_google_product_category", "value": listing.category_path},
            ],
        }
        if option:
            body["attributes"] = [
                {"name": option, "options": [v.options[option] for v in p.variants], "variation": True, "visible": True}
            ]
        else:
            v = p.variants[0]
            body.update({"sku": v.sku, "regular_price": f"{v.price:.2f}" if v.price is not None else ""})
        response = self.client.post(f"{self.base}/products", json=body, auth=self.auth)
        if response.status_code >= 400:
            return PushResult(p.handle, False, errors=[response.json().get("message", response.text[:200])])
        product = response.json()
        result = PushResult(p.handle, True, remote_id=str(product["id"]), variants=1 if not option else 0)
        if option:
            create = [
                {
                    "sku": v.sku,
                    "regular_price": f"{v.price:.2f}" if v.price is not None else "0",
                    "attributes": [{"name": option, "option": v.options[option]}],
                    "weight": f"{(v.weight_g or 0) / 1000:g}",
                }
                for v in p.variants
            ]
            batch = self.client.post(
                f"{self.base}/products/{product['id']}/variations/batch", json={"create": create}, auth=self.auth
            )
            created = batch.json().get("create", []) if batch.status_code < 400 else []
            result.variants = sum(1 for c in created if c.get("id"))
            result.errors += [c["error"]["message"] for c in created if c.get("error")]
            result.ok = not result.errors
        return result
