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
LANGUAGE_NAMES = {
    "de": "German",
    "es": "Spanish (Spain)",
    "fr": "French (France)",
    "it": "Italian",
    "nl": "Dutch",
    "en": "English",
}


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
- Length limits are hard: title at most {store.title.max} characters, meta_title at most {store.meta_title.max},
  meta_description at most {store.meta_description.max}. Shorten the wording (never the facts) when a translation
  runs longer.
Answer with one JSON object and nothing else, one key per language code:
{{"<code>": {{"title": "...", "description_html": "...", "bullets": ["..."], "meta_title": "...", "meta_description": "..."}}}}"""
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "<input>\n" + json.dumps(source_fields(listing), ensure_ascii=False, indent=1) + "\n</input>",
        },
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


def translate(
    model: ChatModel, store: StoreConfig, listing: Listing, languages: list[str], *, max_tokens: int
) -> tuple[dict[str, TranslatedListing], Completion]:
    completion = model.complete(
        messages(store, listing, languages), max_tokens=max_tokens, temperature=0.0, json_mode=True
    )
    return parse_translations(completion.text, languages), completion


def length_problems(store: StoreConfig, translations: dict[str, TranslatedListing]) -> dict[str, list[str]]:
    """Fields over the hard limit (with the translation slack), per language."""
    limits = {
        "title": store.title.max,
        "meta_title": store.meta_title.max,
        "meta_description": store.meta_description.max,
    }
    out: dict[str, list[str]] = {}
    for lang, t in translations.items():
        for name, limit in limits.items():
            if len(getattr(t, name)) > limit * store.translation_slack:
                out.setdefault(lang, []).append(f"{name} has {len(getattr(t, name))} characters, the limit is {limit}")
    return out


def shorten(
    model: ChatModel,
    store: StoreConfig,
    listing: Listing,
    translations: dict[str, TranslatedListing],
    problems: dict[str, list[str]],
    *,
    max_tokens: int,
) -> tuple[dict[str, TranslatedListing], Completion]:
    langs = sorted(problems)
    previous = json.dumps(
        {
            lang: {
                "title": translations[lang].title,
                "description_html": translations[lang].body_html,
                "bullets": translations[lang].bullets,
                "meta_title": translations[lang].meta_title,
                "meta_description": translations[lang].meta_description,
            }
            for lang in langs
        },
        ensure_ascii=False,
    )
    note = "\n".join(f"- {lang}: {p}" for lang in langs for p in problems[lang])
    msgs: list[Message] = [
        *messages(store, listing, langs),
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": "Some fields are too long:\n" + note + "\nReturn the same JSON with those fields shortened.",
        },
    ]
    completion = model.complete(msgs, max_tokens=max_tokens, temperature=0.0, json_mode=True)
    return parse_translations(completion.text, langs), completion


def back_messages(translated: TranslatedListing, lang: str) -> list[Message]:
    system = f"""Shelfwise task: {BACK_TASK}
Translate this {LANGUAGE_NAMES.get(lang, lang)} product listing into English, literally, keeping numbers and HTML.
Answer with one JSON object: {{"title": "...", "description_html": "...", "bullets": ["..."], "meta_title": "...", "meta_description": "..."}}"""
    payload = {
        "language": lang,
        "title": translated.title,
        "description_html": translated.body_html,
        "bullets": translated.bullets,
        "meta_title": translated.meta_title,
        "meta_description": translated.meta_description,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "<input>\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</input>"},
    ]
