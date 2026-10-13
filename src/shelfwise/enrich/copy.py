"""SEO copy in the store's voice, written only from facts, with the category picked from retrieved candidates.

The model gets a numbered facts list (feed cells and visual attributes, each with its source), the style guide and
the limits, and returns a JSON listing. Code then validates it; when it fails, the model gets one corrective
round with the exact problems ("“dishwasher safe” is not backed by the feed", "title 84 characters, limit 70").
"""

from __future__ import annotations

import json
from typing import Any

from shelfwise.catalog.models import AttributeDecision, Fact, Issue, Listing
from shelfwise.config import StoreConfig
from shelfwise.enrich.crosscheck import usable
from shelfwise.llm.base import ChatModel, Completion, Message
from shelfwise.llm.jsonparse import parse_json_object
from shelfwise.taxonomy import Category

TASK = "write_listing"


def facts_for_prompt(facts: list[Fact], decisions: list[AttributeDecision]) -> list[dict[str, str]]:
    """The facts the copy may use. Visual attributes come from the cross-check (photo-only material is left out)."""
    out: list[dict[str, str]] = []
    decided = {d.attribute for d in decisions if usable(d)}
    for f in facts:
        if f.key in decided or f.key in {"style"}:
            continue  # replaced by the decision below
        source = "feed" if any(e.source == "feed" for e in f.evidence) else (f.evidence[0].source if f.evidence else "feed")
        out.append({"id": f.id, "fact": f.key, "value": f.display, "source": source})
    for d in decisions:
        if not usable(d):
            continue
        src = {"agree": "feed + photo", "feed_only": "feed", "image_only": "photo", "conflict": "photo (feed differs)"}.get(d.status, d.status)
        if d.status == "conflict" and d.attribute == "material":
            src = "feed (photo differs)"
        value = f"{d.value} units" if d.attribute == "count" else str(d.value)
        if d.attribute == "count" and int(d.value) == 1:
            continue
        out.append({"id": f"attr:{d.attribute}", "fact": d.attribute, "value": value, "source": src})
    return out


def system_prompt(store: StoreConfig) -> str:
    style = store.style
    rules = "\n".join(f"- {r}" for key in ("title_rules", "description_rules", "bullets_rules", "seo_rules", "alt_text_rules")
                      for r in style.get(key, []))
    examples = json.dumps(style.get("examples", []), ensure_ascii=False)
    banned = "superlatives (best, #1, unbeatable), guarantees, medical or health claims (relieves pain, antibacterial, hypoallergenic), FDA, and generic green claims (eco-friendly, sustainable)"
    return f"""Shelfwise task: {TASK}
You write product listings for the online store {store.name}.
Voice: {style.get("voice", "")}
Title pattern: {style.get("title_pattern", "")}
Rules:
{rules}
- Use ONLY the facts provided. Every number, material, color, pattern, pack size, care instruction, feature and
  origin you mention must be in the facts. If a fact is missing, leave it out; never guess dimensions or materials.
- Never use {banned}, even if a supplier wrote them. Avoid these words: {", ".join(store.avoid_words)}.
- Limits: title {store.title.min}-{store.title.max} characters; meta_title {store.meta_title.min}-{store.meta_title.max};
  meta_description {store.meta_description.min}-{store.meta_description.max}; {store.bullets_count.min}-{store.bullets_count.max}
  bullets of at most {store.bullet_max_chars} characters; description {store.body_words.min}-{store.body_words.max} words in
  <p> paragraphs (allowed tags: p, ul, li, strong, em); {store.tags_count.min}-{store.tags_count.max} lowercase tags;
  one alt text per image, under {store.alt_text_max} characters.
- product_type must be one of the store's types; category_id must be one of the candidate ids.
Examples of the house style: {examples}

Answer with one JSON object and nothing else:
{{"title": "...", "description_html": "<p>...</p><p>...</p>", "bullets": ["...", "..."], "meta_title": "...",
 "meta_description": "...", "tags": ["..."], "product_type": "...", "category_id": 0, "alt_texts": ["..."]}}"""


def user_payload(
    *, name: str, vendor: str | None, facts: list[dict[str, str]], variants: list[str], candidates: list[Category],
    product_types: list[str], image_count: int, supplier_category: str,
) -> dict[str, Any]:
    return {
        "product_name": name,
        "brand": vendor or "",
        "supplier_category": supplier_category,
        "variants": variants,
        "facts": facts,
        "images": image_count,
        "product_types": product_types,
        "category_candidates": [{"id": c.id, "path": c.path} for c in candidates],
    }


def messages(store: StoreConfig, payload: dict[str, Any]) -> list[Message]:
    return [
        {"role": "system", "content": system_prompt(store)},
        {"role": "user", "content": "Write the listing for this product.\n<input>\n"
         + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</input>"},
    ]


def correction_messages(base: list[Message], previous: str, issues: list[Issue]) -> list[Message]:
    problems = "\n".join(f"- {i.field}: {i.message}" for i in issues)
    return [
        *base,
        {"role": "assistant", "content": previous},
        {"role": "user", "content": "Your listing failed the store's checks:\n" + problems
         + "\nReturn the corrected JSON listing. Fix only these problems: remove any statement that is not in the "
         "facts, keep everything else as it was."},
    ]


def parse_listing(text: str, candidates: list[Category]) -> Listing:
    data = parse_json_object(text)

    def s(key: str) -> str:
        value = data.get(key)
        return "" if value is None else str(value).strip()

    def strings(key: str) -> list[str]:
        value = data.get(key) or []
        if isinstance(value, str):
            value = [value]
        return [str(v).strip() for v in value if str(v).strip()]

    category_id: int | None
    try:
        category_id = int(data.get("category_id")) if data.get("category_id") is not None else None
    except (TypeError, ValueError):
        category_id = None
    path = next((c.path for c in candidates if c.id == category_id), "")
    return Listing(
        title=s("title"),
        body_html=s("description_html") or s("body_html") or s("description"),
        bullets=strings("bullets"),
        meta_title=s("meta_title"),
        meta_description=s("meta_description"),
        tags=[t.lower() for t in strings("tags")],
        product_type=s("product_type"),
        category_id=category_id,
        category_path=path,
        alt_texts=strings("alt_texts"),
    )


def write_listing(model: ChatModel, msgs: list[Message], *, max_tokens: int) -> Completion:
    return model.complete(msgs, max_tokens=max_tokens, temperature=0.2, json_mode=True)
