"""A local mock of the WooCommerce REST API v3 (products and variations), with consumer-key auth and the
validation errors WooCommerce returns. Keys are generated when the mock starts; there is no real shop."""

from __future__ import annotations

import secrets
import threading
from typing import Any


class WooError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "data": {"status": self.status}}


class WooMock:
    def __init__(self) -> None:
        self.consumer_key = "ck_local_" + secrets.token_hex(12)
        self.consumer_secret = "cs_local_" + secrets.token_hex(12)
        self.products: dict[int, dict[str, Any]] = {}
        self.variations: dict[int, list[dict[str, Any]]] = {}
        self._next = 100
        self._lock = threading.Lock()
        self.requests = 0

    def authorize(self, key: str | None, secret: str | None) -> None:
        self.requests += 1
        if key != self.consumer_key or secret != self.consumer_secret:
            raise WooError(401, "woocommerce_rest_cannot_view", "Sorry, you cannot list resources.")

    def _id(self) -> int:
        self._next += 1
        return self._next

    def create_product(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise WooError(400, "rest_missing_callback_param", "Missing parameter(s): name")
        kind = data.get("type", "simple")
        if kind not in {"simple", "variable"}:
            raise WooError(400, "rest_invalid_param", "Invalid parameter(s): type")
        sku = data.get("sku")
        with self._lock:
            if sku and any(p.get("sku") == sku for p in self.products.values()):
                raise WooError(400, "product_invalid_sku", "Invalid or duplicated SKU.")
            slug = data.get("slug") or name.lower().replace(" ", "-")
            existing = next((p for p in self.products.values() if p["slug"] == slug), None)
            pid = existing["id"] if existing else self._id()
            product = {
                "id": pid,
                "name": name,
                "slug": slug,
                "type": kind,
                "status": data.get("status", "draft"),
                "description": data.get("description", ""),
                "short_description": data.get("short_description", ""),
                "sku": sku or "",
                "regular_price": str(data.get("regular_price", "")),
                "categories": data.get("categories", []),
                "tags": data.get("tags", []),
                "images": data.get("images", []),
                "attributes": data.get("attributes", []),
                "meta_data": data.get("meta_data", []),
                "permalink": f"http://localhost/product/{slug}/",
            }
            self.products[pid] = product
            if not existing:
                self.variations[pid] = []
        return product

    def batch_variations(self, product_id: int, data: dict[str, Any]) -> dict[str, Any]:
        product = self.products.get(product_id)
        if product is None:
            raise WooError(404, "woocommerce_rest_product_invalid_id", "Invalid ID.")
        if product["type"] != "variable":
            raise WooError(400, "woocommerce_rest_invalid_product_type", "Variations need a variable product.")
        attribute_names = {a["name"] for a in product["attributes"] if a.get("variation")}
        created = []
        for item in data.get("create", []):
            names = {a["name"] for a in item.get("attributes", [])}
            if names - attribute_names:
                created.append({"id": 0, "error": {"code": "woocommerce_rest_invalid_attribute",
                                                   "message": f"Unknown attribute(s): {', '.join(sorted(names - attribute_names))}"}})
                continue
            try:
                float(item.get("regular_price", "0"))
            except ValueError:
                created.append({"id": 0, "error": {"code": "rest_invalid_param", "message": "regular_price is not a number"}})
                continue
            with self._lock:
                variation = {"id": self._id(), **item}
                self.variations[product_id].append(variation)
            created.append(variation)
        return {"create": created}

    def list_products(self, per_page: int = 100) -> list[dict[str, Any]]:
        return list(self.products.values())[:per_page]

    def get_product(self, product_id: int) -> dict[str, Any]:
        product = self.products.get(product_id)
        if product is None:
            raise WooError(404, "woocommerce_rest_product_invalid_id", "Invalid ID.")
        return {**product, "variations": [v["id"] for v in self.variations.get(product_id, [])]}
