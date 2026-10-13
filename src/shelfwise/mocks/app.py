"""The mock store server (`shelfwise mock-store`): Shopify Admin GraphQL under /shopify, WooCommerce REST under
/woocommerce, and /credentials for the generated local development credentials (localhost only)."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from shelfwise.mocks.shopify import ShopifyMock
from shelfwise.mocks.woocommerce import WooError, WooMock

API_VERSIONS = {"2025-10", "2026-01", "2026-04", "2026-07"}


def create_mock_app(shopify: ShopifyMock | None = None, woo: WooMock | None = None) -> FastAPI:
    shop = shopify or ShopifyMock()
    wc = woo or WooMock()
    app = FastAPI(
        title="Shelfwise mock stores",
        description="Local mocks of the Shopify Admin GraphQL API and the WooCommerce REST API.",
    )
    app.state.shopify = shop
    app.state.woo = wc

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "shopify_products": len(shop.products), "woocommerce_products": len(wc.products)}

    @app.get("/credentials")
    def credentials(request: Request) -> dict[str, str]:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"} and not host.startswith(
            ("172.", "10.", "192.168.")
        ):
            raise HTTPException(403, "credentials are only served to local clients")
        return {
            "shopify_token": shop.token,
            "woocommerce_key": wc.consumer_key,
            "woocommerce_secret": wc.consumer_secret,
        }

    @app.post("/shopify/admin/api/{version}/graphql.json")
    async def graphql(
        version: str, request: Request, x_shopify_access_token: str | None = Header(default=None)
    ) -> JSONResponse:
        if x_shopify_access_token != shop.token:
            return JSONResponse(
                {"errors": "[API] Invalid API key or access token (unrecognized login or wrong password)"},
                status_code=401,
            )
        if version not in API_VERSIONS:
            return JSONResponse({"errors": f"API version {version} is not supported by this mock"}, status_code=404)
        body = await request.json()
        return JSONResponse(shop.execute(str(body.get("query", "")), body.get("variables")))

    wc_router = APIRouter(prefix="/woocommerce/wp-json/wc/v3")

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        key = secret = None
        if header.lower().startswith("basic "):
            try:
                key, _, secret = base64.b64decode(header[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                key = secret = None
        else:
            key, secret = request.query_params.get("consumer_key"), request.query_params.get("consumer_secret")
        wc.authorize(key, secret)

    @wc_router.get("/products")
    def list_products(request: Request, per_page: int = 100) -> list[dict[str, Any]]:
        auth(request)
        return wc.list_products(per_page)

    @wc_router.get("/products/{product_id}")
    def get_product(request: Request, product_id: int) -> dict[str, Any]:
        auth(request)
        return wc.get_product(product_id)

    @wc_router.post("/products", status_code=201)
    async def create_product(request: Request) -> dict[str, Any]:
        auth(request)
        return wc.create_product(await request.json())

    @wc_router.post("/products/{product_id}/variations/batch")
    async def variations(request: Request, product_id: int) -> dict[str, Any]:
        auth(request)
        return wc.batch_variations(product_id, await request.json())

    app.include_router(wc_router)

    @app.exception_handler(WooError)
    async def woo_error(_: Request, exc: WooError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    return app
