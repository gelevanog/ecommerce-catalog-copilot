"""Shopify product CSV (the import format of Shopify admin > Products > Import) and a translations CSV in the
layout of Shopify's Translate & Adapt export. Only approved products are exported."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from shelfwise.catalog.models import ProductRecord

COLUMNS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Variant SKU", "Variant Grams", "Variant Inventory Tracker",
    "Variant Inventory Qty", "Variant Inventory Policy", "Variant Fulfillment Service", "Variant Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode", "Image Src", "Image Position",
    "Image Alt Text", "SEO Title", "SEO Description", "Google Shopping / Google Product Category",
    "Variant Weight Unit", "Status",
]
TRANSLATION_COLUMNS = ["Type", "Identification", "Field", "Locale", "Market", "Status", "Default content", "Translated content"]


class NotApprovedError(ValueError):
    pass


def _gtin(value: str | None) -> str:
    if not value:
        return ""
    return value[1:] if len(value) == 14 and value.startswith("0") else value


def rows(records: Iterable[ProductRecord], *, image_base_url: str, require_approval: bool = True) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for record in records:
        if require_approval and record.status not in {"approved", "exported"}:
            raise NotApprovedError(f"{record.product.handle} is {record.status}; only approved products are exported")
        listing = record.listing
        if listing is None:
            raise NotApprovedError(f"{record.product.handle} has no listing")
        p = record.product
        option_name = next(iter(p.variants[0].options), "Title") if p.variants else "Title"
        images = p.images or [""]
        for i, variant in enumerate(p.variants or []):
            row = dict.fromkeys(COLUMNS, "")
            row["Handle"] = p.handle
            row.update({
                "Option1 Value": variant.options.get(option_name, "Default Title"),
                "Variant SKU": variant.sku,
                "Variant Grams": f"{variant.weight_g:.0f}" if variant.weight_g else "",
                "Variant Inventory Tracker": "shopify",
                "Variant Inventory Qty": str(variant.inventory or 0),
                "Variant Inventory Policy": "deny",
                "Variant Fulfillment Service": "manual",
                "Variant Price": f"{variant.price:.2f}" if variant.price is not None else "",
                "Variant Requires Shipping": "TRUE",
                "Variant Taxable": "TRUE",
                "Variant Barcode": _gtin(variant.gtin),
                "Variant Weight Unit": "g",
            })
            if i == 0:
                row.update({
                    "Title": listing.title,
                    "Body (HTML)": listing.body_html,
                    "Vendor": p.vendor or "",
                    "Type": listing.product_type,
                    "Tags": ", ".join(listing.tags),
                    "Published": "TRUE",
                    "Option1 Name": option_name,
                    "Image Src": image_base_url.rstrip("/") + "/" + images[0] if images[0] else "",
                    "Image Position": "1" if images[0] else "",
                    "Image Alt Text": listing.alt_texts[0] if listing.alt_texts else "",
                    "SEO Title": listing.meta_title,
                    "SEO Description": listing.meta_description,
                    "Google Shopping / Google Product Category": listing.category_path,
                    "Status": "active",
                })
            out.append(row)
        for position, image in enumerate(images[1:], start=2):
            row = dict.fromkeys(COLUMNS, "")
            row.update({"Handle": p.handle, "Image Src": image_base_url.rstrip("/") + "/" + image,
                        "Image Position": str(position),
                        "Image Alt Text": listing.alt_texts[position - 1] if len(listing.alt_texts) >= position else ""})
            out.append(row)
    return out


def to_csv(records: Iterable[ProductRecord], *, image_base_url: str, require_approval: bool = True) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows(records, image_base_url=image_base_url, require_approval=require_approval))
    return buffer.getvalue()


def translations_csv(records: Iterable[ProductRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TRANSLATION_COLUMNS)
    for record in records:
        listing = record.listing
        if listing is None:
            continue
        source = {"title": listing.title, "body_html": listing.body_html, "meta_title": listing.meta_title,
                  "meta_description": listing.meta_description}
        for locale, t in sorted(listing.translations.items()):
            target = {"title": t.title, "body_html": t.body_html, "meta_title": t.meta_title, "meta_description": t.meta_description}
            for field, text in source.items():
                writer.writerow(["PRODUCT", record.product.handle, field, locale, "", "", text, target[field]])
    return buffer.getvalue()
