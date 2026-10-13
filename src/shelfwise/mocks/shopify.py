"""A local mock of the Shopify Admin GraphQL API: the subset a catalog push needs, with real GraphQL parsing and
validation (graphql-core), Shopify-style `userErrors`, access-token auth and the query-cost extension.

Implemented: `productSet` (create or update by handle, options, variants, media, SEO), `products` and `product`
queries, `translatableResource` (content digests) and `translationsRegister`. Nothing here talks to Shopify; there
is no real store and no account. The token is generated when the mock starts.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from typing import Any

from graphql import GraphQLSchema, build_schema, graphql_sync

SDL = """
type Query {
  products(first: Int = 50, query: String): ProductConnection!
  product(id: ID!): Product
  translatableResource(resourceId: ID!): TranslatableResource
}
type Mutation {
  productSet(input: ProductSetInput!, synchronous: Boolean = true): ProductSetPayload
  translationsRegister(resourceId: ID!, translations: [TranslationInput!]!): TranslationsRegisterPayload
}
enum ProductStatus { ACTIVE ARCHIVED DRAFT }
enum WeightUnit { GRAMS KILOGRAMS OUNCES POUNDS }
enum FileContentType { IMAGE FILE VIDEO }
input SEOInput { title: String, description: String }
input OptionValueSetInput { name: String! }
input OptionSetInput { name: String!, values: [OptionValueSetInput!]! }
input VariantOptionValueInput { optionName: String!, name: String! }
input WeightInput { value: Float!, unit: WeightUnit! }
input InventoryItemMeasurementInput { weight: WeightInput }
input InventoryItemInput { sku: String, tracked: Boolean, measurement: InventoryItemMeasurementInput }
input ProductVariantSetInput {
  optionValues: [VariantOptionValueInput!]!
  price: String
  barcode: String
  inventoryItem: InventoryItemInput
}
input FileSetInput { originalSource: String!, alt: String, contentType: FileContentType }
input ProductSetInput {
  id: ID
  handle: String
  title: String!
  descriptionHtml: String
  vendor: String
  productType: String
  category: ID
  tags: [String!]
  status: ProductStatus
  seo: SEOInput
  productOptions: [OptionSetInput!]
  variants: [ProductVariantSetInput!]
  files: [FileSetInput!]
}
input TranslationInput { locale: String!, key: String!, value: String!, translatableContentDigest: String! }
type UserError { field: [String!], message: String!, code: String }
type SEO { title: String, description: String }
type Image { url: String!, altText: String }
type InventoryItem { sku: String }
type ProductVariant { id: ID!, title: String!, sku: String, price: String, barcode: String, inventoryItem: InventoryItem }
type ProductVariantConnection { nodes: [ProductVariant!]! }
type ProductOption { name: String!, values: [String!]! }
type MediaImage { image: Image }
type MediaConnection { nodes: [MediaImage!]! }
type Translation { locale: String!, key: String!, value: String }
type Product {
  id: ID!
  handle: String!
  title: String!
  descriptionHtml: String
  vendor: String
  productType: String
  tags: [String!]!
  status: ProductStatus!
  seo: SEO
  options: [ProductOption!]!
  variants(first: Int = 100): ProductVariantConnection!
  media(first: Int = 20): MediaConnection!
  translations(locale: String!): [Translation!]!
  updatedAt: String
}
type ProductConnection { nodes: [Product!]! }
type ProductSetPayload { product: Product, userErrors: [UserError!]! }
type TranslatableContent { key: String!, value: String, digest: String!, locale: String! }
type TranslatableResource { resourceId: ID!, translatableContent: [TranslatableContent!]! }
type TranslationsRegisterPayload { translations: [Translation!], userErrors: [UserError!]! }
"""

TRANSLATABLE_KEYS = {"title": "title", "body_html": "descriptionHtml", "meta_title": "seo.title", "meta_description": "seo.description"}


def _digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


class ShopifyMock:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or "local-dev-" + secrets.token_hex(16)
        self.schema: GraphQLSchema = build_schema(SDL)
        self.products: dict[str, dict[str, Any]] = {}
        self.translations: dict[str, dict[tuple[str, str], str]] = {}
        self._lock = threading.Lock()
        self._next = 1001
        self.requests = 0

    # ---- resolvers
    def _gid(self, kind: str) -> str:
        self._next += 1
        return f"gid://shopify/{kind}/{self._next}"

    def product_set(self, data: dict[str, Any]) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        title = (data.get("title") or "").strip()
        if not title:
            errors.append({"field": ["input", "title"], "message": "Title can't be blank", "code": "BLANK"})
        if len(title) > 255:
            errors.append({"field": ["input", "title"], "message": "Title is too long (maximum is 255 characters)", "code": "TOO_LONG"})
        options = data.get("productOptions") or []
        variants = data.get("variants") or []
        option_names = [o["name"] for o in options]
        seen: set[tuple[str, ...]] = set()
        for i, v in enumerate(variants):
            names = [ov["optionName"] for ov in v.get("optionValues", [])]
            if options and sorted(names) != sorted(option_names):
                errors.append({"field": ["input", "variants", str(i)], "message": "Variant option values do not match the product options", "code": "INVALID"})
            key = tuple(sorted(f"{ov['optionName']}={ov['name']}" for ov in v.get("optionValues", [])))
            if key in seen:
                errors.append({"field": ["input", "variants", str(i)], "message": "The variant already exists", "code": "TAKEN"})
            seen.add(key)
            try:
                if v.get("price") is not None and float(v["price"]) < 0:
                    raise ValueError
            except ValueError:
                errors.append({"field": ["input", "variants", str(i), "price"], "message": "Price is invalid", "code": "INVALID"})
        if len(variants) > 2048:
            errors.append({"field": ["input", "variants"], "message": "Too many variants", "code": "TOO_MANY"})
        if errors:
            return {"product": None, "userErrors": errors}
        with self._lock:
            handle = data.get("handle") or title.lower().replace(" ", "-")
            existing = next((p for p in self.products.values() if p["handle"] == handle), None)
            if data.get("id"):
                existing = self.products.get(data["id"])
                if existing is None:
                    return {"product": None, "userErrors": [{"field": ["input", "id"], "message": "Product does not exist", "code": "NOT_FOUND"}]}
            pid = existing["id"] if existing else self._gid("Product")
            seo = data.get("seo") or {}
            product = {
                "id": pid,
                "handle": handle,
                "title": title,
                "descriptionHtml": data.get("descriptionHtml") or "",
                "vendor": data.get("vendor") or "",
                "productType": data.get("productType") or "",
                "tags": list(data.get("tags") or []),
                "status": data.get("status") or "DRAFT",
                "seo": {"title": seo.get("title"), "description": seo.get("description")},
                "options": [{"name": o["name"], "values": [v["name"] for v in o["values"]]} for o in options]
                or [{"name": "Title", "values": ["Default Title"]}],
                "variants": [
                    {
                        "id": self._gid("ProductVariant"),
                        "title": " / ".join(ov["name"] for ov in v.get("optionValues", [])) or "Default Title",
                        "sku": (v.get("inventoryItem") or {}).get("sku"),
                        "price": v.get("price"),
                        "barcode": v.get("barcode"),
                        "inventoryItem": {"sku": (v.get("inventoryItem") or {}).get("sku")},
                    }
                    for v in variants
                ],
                "media": [{"image": {"url": f["originalSource"], "altText": f.get("alt")}} for f in data.get("files") or []],
                "updatedAt": "2026-10-13T00:00:00Z",
            }
            self.products[pid] = product
        return {"product": product, "userErrors": []}

    def translatable(self, resource_id: str) -> dict[str, Any] | None:
        p = self.products.get(resource_id)
        if p is None:
            return None
        content = [
            {"key": "title", "value": p["title"]},
            {"key": "body_html", "value": p["descriptionHtml"]},
            {"key": "meta_title", "value": p["seo"]["title"]},
            {"key": "meta_description", "value": p["seo"]["description"]},
        ]
        return {"resourceId": resource_id,
                "translatableContent": [{**c, "digest": _digest(c["value"]), "locale": "en"} for c in content]}

    def register(self, resource_id: str, translations: list[dict[str, Any]]) -> dict[str, Any]:
        resource = self.translatable(resource_id)
        if resource is None:
            return {"translations": None, "userErrors": [{"field": ["resourceId"], "message": "Resource does not exist", "code": "RESOURCE_NOT_FOUND"}]}
        digests = {c["key"]: c["digest"] for c in resource["translatableContent"]}
        errors, saved = [], []
        for i, t in enumerate(translations):
            if t["key"] not in digests:
                errors.append({"field": ["translations", str(i), "key"], "message": f"Key {t['key']} is not translatable", "code": "INVALID_KEY_FOR_MODEL"})
            elif t["translatableContentDigest"] != digests[t["key"]]:
                errors.append({"field": ["translations", str(i), "translatableContentDigest"], "message": "Translatable content hash is invalid", "code": "INVALID_TRANSLATABLE_CONTENT"})
            elif not t["value"].strip():
                errors.append({"field": ["translations", str(i), "value"], "message": "Value can't be blank", "code": "BLANK"})
            else:
                self.translations.setdefault(resource_id, {})[(t["locale"], t["key"])] = t["value"]
                saved.append({"locale": t["locale"], "key": t["key"], "value": t["value"]})
        return {"translations": saved, "userErrors": errors}

    # ---- GraphQL execution
    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests += 1
        mock = self

        class Root:
            @staticmethod
            def products(info: Any, first: int = 50, query: str | None = None) -> dict[str, Any]:
                nodes = list(mock.products.values())
                if query:
                    nodes = [p for p in nodes if query.lower().replace("handle:", "") in p["handle"]]
                return {"nodes": [mock._shape(p) for p in nodes[:first]]}

            @staticmethod
            def product(info: Any, id: str) -> dict[str, Any] | None:  # noqa: A002
                p = mock.products.get(id)
                return mock._shape(p) if p else None

            @staticmethod
            def translatableResource(info: Any, resourceId: str) -> dict[str, Any] | None:  # noqa: N802, N803
                return mock.translatable(resourceId)

            @staticmethod
            def productSet(info: Any, input: dict[str, Any], synchronous: bool = True) -> dict[str, Any]:  # noqa: A002, N802
                result = mock.product_set(input)
                if result["product"]:
                    result["product"] = mock._shape(result["product"])
                return result

            @staticmethod
            def translationsRegister(info: Any, resourceId: str, translations: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: N802, N803
                return mock.register(resourceId, translations)

        def field_resolver(source: Any, info: Any, **args: Any) -> Any:
            name = info.field_name
            if source is Root:
                return getattr(Root, name)(info, **args)
            if isinstance(source, dict):
                value = source.get(name)
                return value(**args) if callable(value) else value
            return getattr(source, name, None)

        result = graphql_sync(self.schema, query, root_value=Root, variable_values=variables or {}, field_resolver=field_resolver)
        body: dict[str, Any] = {}
        if result.errors:
            body["errors"] = [{"message": e.message} for e in result.errors]
        if result.data is not None:
            body["data"] = result.data
        cost = 10 + 10 * query.count("productSet") + query.count("{")
        body["extensions"] = {"cost": {"requestedQueryCost": cost, "actualQueryCost": cost,
                                       "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": 2000.0 - cost, "restoreRate": 100.0}}}
        return body

    def _shape(self, p: dict[str, Any]) -> dict[str, Any]:
        translations = self.translations.get(p["id"], {})
        return {
            **p,
            "variants": lambda first=100: {"nodes": p["variants"][:first]},
            "media": lambda first=20: {"nodes": p["media"][:first]},
            "translations": lambda locale: [{"locale": loc, "key": key, "value": value}
                                            for (loc, key), value in translations.items() if loc == locale],
        }
