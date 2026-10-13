"""Attributes from the product photo, seen by a vision model that is never shown the feed.

The model is blind to the supplier's data on purpose: an answer that only repeats the feed is no evidence. Its words
are mapped onto the store vocabularies in code (the raw words are kept), and anything outside them is dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shelfwise.catalog.models import ImageAttributes
from shelfwise.llm.base import ChatModel, LLMError, Message, image_part
from shelfwise.llm.jsonparse import JSONReplyError, parse_json_object
from shelfwise.vocab import COLORS, MATERIALS, PATTERNS, STYLES, canonical, parse_count

TASK = "vision_attributes"

SYSTEM = f"""Shelfwise task: {TASK}
You are a product data specialist. You look at one catalog photo and report only what you can see. Do not guess a
brand, size, price, weight or anything that is not visible. The background is not part of the product.

Answer with one JSON object and nothing else:
{{"product": "<what the product is, 1 to 4 words>",
 "color": "<main color of the product: one of {", ".join(COLORS)}>",
 "secondary_color": "<second color of the product if it has a clear one (stripes, dots, trim), else null>",
 "pattern": "<one of {", ".join(PATTERNS)}>",
 "material": "<the material if it is visible: one of {", ".join(MATERIALS)}, wax; else null>",
 "style": "<one of {", ".join(STYLES)}>",
 "count": <how many separate items are shown, an integer>,
 "visible_text": "<text printed on the product, else an empty string>",
 "confidence": {{"color": 0.0-1.0, "pattern": 0.0-1.0, "material": 0.0-1.0, "style": 0.0-1.0, "count": 0.0-1.0}}}}"""

USER = "Catalog photo of one product listing (a pack may show several identical items). Report its attributes as JSON."


def vision_messages(image: Path) -> list[Message]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": USER}, image_part(image)]},
    ]


def _confidence(raw: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                out[str(key)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
    return out


def normalize_answer(data: dict[str, Any], *, image: str, model: str) -> ImageAttributes:
    def text(key: str) -> str:
        value = data.get(key)
        return "" if value is None or str(value).lower() in {"null", "none", "n/a", ""} else str(value)

    count_raw = data.get("count")
    count: int | None
    if isinstance(count_raw, int | float) and not isinstance(count_raw, bool):
        count = int(count_raw) if count_raw >= 1 else None
    else:
        count = parse_count(str(count_raw)) if count_raw is not None else None
    material_text = text("material")
    material = canonical("material", material_text) if material_text else None
    if material is None and "wax" in material_text.lower():
        material = "wax"
    secondary = canonical("color", text("secondary_color")) if text("secondary_color") else None
    color = canonical("color", text("color")) if text("color") else None
    return ImageAttributes(
        image=image,
        model=model,
        color=color,
        secondary_color=secondary if secondary != color else None,
        pattern=canonical("pattern", text("pattern")) if text("pattern") else None,
        material=material,
        style=canonical("style", text("style")) if text("style") else None,
        count=count,
        product_noun=text("product")[:60],
        visible_text=text("visible_text")[:200],
        confidence=_confidence(data.get("confidence")),
        raw={k: data.get(k) for k in ("product", "color", "secondary_color", "pattern", "material", "style", "count")},
    )


def extract_image_attributes(model: ChatModel, image: Path, *, max_tokens: int = 4000) -> tuple[ImageAttributes, Any]:
    """Returns the attributes and the completion (for call statistics); errors become an `error` on the result."""
    try:
        completion = model.complete(vision_messages(image), max_tokens=max_tokens, temperature=0.0, json_mode=True)
    except LLMError as exc:
        return ImageAttributes(image=image.name, model=model.label, error=str(exc)[:300]), None
    try:
        data = parse_json_object(completion.text)
    except JSONReplyError as exc:
        return ImageAttributes(image=image.name, model=completion.model, error=f"no JSON: {exc}"[:300]), completion
    attrs = normalize_answer(data, image=image.name, model=completion.model)
    return attrs, completion


def describe(attrs: ImageAttributes) -> str:
    return json.dumps({k: v for k, v in attrs.raw.items() if v not in (None, "")}, ensure_ascii=False)
