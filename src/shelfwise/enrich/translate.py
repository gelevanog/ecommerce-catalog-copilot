"""Translations of the approved English copy into the store's languages, with the glossary in the prompt."""

from __future__ import annotations

import json
from typing import Any

from shelfwise.catalog.models import Listing, TranslatedListing
from shelfwise.config import StoreConfig
from shelfwise.llm.base import ChatModel, Completion, Message
from shelfwise.llm.jsonparse import parse_json_object

TASK = "translate_listing"
BACK_TASK = "back_translate"
LANGUAGE_NAMES = {"de": "German", "es": "Spanish (Spain)", "fr": "French (France)", "it": "Italian", "nl": "Dutch", "en": "English"}


def source_fields(listing: Listing) -> dict[str, Any]:
    return {
        "title": listing.title,
        "description_html": listing.body_html,
        "bullets": listing.bullets,
        "meta_title": listing.meta_title,
        "meta_description": listing.meta_description,
    }


def messages(store: StoreConfig, listing: Listing, languages: list[str]) -> list[Message]:
    glossary = {en: {lang: v for lang, v in t.items() if lang in languages} for en, t in store.glossary_terms.items()}
    names = ", ".join(f"{code} ({LANGUAGE_NAMES.get(code, code)})" for code in languages)
    system = f"""Shelfwise task: {TASK}
You are a professional e-commerce translator. Translate the product listing into {names}.
- Keep the meaning exactly: add nothing, drop nothing. Keep every number and unit as written.
- Never translate these names: {", ".join(store.glossary_keep)}.
- Use these translations for product terms (German compounds may join them): {json.dumps(glossary, ensure_ascii=False)}
- Keep the HTML tags of the description as they are.
- Keep titles under {store.title.max} characters, meta titles under {store.meta_title.max} and meta descriptions under
  {store.meta_description.max} where the language allows.
Answer with one JSON object and nothing else, one key per language code:
{{"<code>": {{"title": "...", "description_html": "...", "bullets": ["..."], "meta_title": "...", "meta_description": "..."}}}}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "<input>\n" + json.dumps(source_fields(listing), ensure_ascii=False, indent=1) + "\n</input>"},
    ]


def parse_translations(text: str, languages: list[str]) -> dict[str, TranslatedListing]:
    data = parse_json_object(text)
    out: dict[str, TranslatedListing] = {}
    for lang in languages:
        item = data.get(lang)
        if not isinstance(item, dict):
            continue
        bullets = item.get("bullets") or []
        out[lang] = TranslatedListing(
            title=str(item.get("title") or "").strip(),
            body_html=str(item.get("description_html") or item.get("body_html") or "").strip(),
            bullets=[str(b).strip() for b in bullets if str(b).strip()] if isinstance(bullets, list) else [],
            meta_title=str(item.get("meta_title") or "").strip(),
            meta_description=str(item.get("meta_description") or "").strip(),
        )
    return out


def translate(model: ChatModel, store: StoreConfig, listing: Listing, languages: list[str], *, max_tokens: int) -> tuple[dict[str, TranslatedListing], Completion]:
    completion = model.complete(messages(store, listing, languages), max_tokens=max_tokens, temperature=0.0, json_mode=True)
    return parse_translations(completion.text, languages), completion


def back_messages(translated: TranslatedListing, lang: str) -> list[Message]:
    system = f"""Shelfwise task: {BACK_TASK}
Translate this {LANGUAGE_NAMES.get(lang, lang)} product listing into English, literally, keeping numbers and HTML.
Answer with one JSON object: {{"title": "...", "description_html": "...", "bullets": ["..."], "meta_title": "...", "meta_description": "..."}}"""
    payload = {"language": lang, "title": translated.title, "description_html": translated.body_html,
               "bullets": translated.bullets, "meta_title": translated.meta_title,
               "meta_description": translated.meta_description}
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "<input>\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</input>"},
    ]
