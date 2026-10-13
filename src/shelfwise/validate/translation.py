"""Translation sanity checks that need no second model: right language, every number and unit kept, brand names
untouched, glossary terms used, HTML structure kept, length limits with a little slack."""

from __future__ import annotations

import re
from collections import Counter

from shelfwise.catalog.models import Issue, Listing, TranslatedListing
from shelfwise.config import StoreConfig
from shelfwise.validate.claims import plain

STOPWORDS: dict[str, set[str]] = {
    "en": {"the", "and", "with", "for", "your", "you", "this", "made", "from", "of", "in", "is", "a", "to", "it", "on"},
    "de": {
        "der",
        "die",
        "das",
        "und",
        "mit",
        "für",
        "ihr",
        "ihre",
        "aus",
        "ist",
        "ein",
        "eine",
        "zu",
        "den",
        "im",
        "sie",
        "von",
    },
    "es": {
        "el",
        "la",
        "los",
        "las",
        "y",
        "con",
        "para",
        "tu",
        "de",
        "en",
        "es",
        "un",
        "una",
        "del",
        "su",
        "que",
        "por",
    },
    "fr": {"le", "la", "les", "et", "avec", "pour", "votre", "vous", "de", "en", "est", "un", "une", "des", "du", "au"},
}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_TAG = re.compile(r"<\s*([a-zA-Z0-9]+)")


def detect_language(text: str) -> tuple[str, dict[str, int]]:
    words = re.findall(r"[a-zäöüßàâçéèêëîïôûùüÿñáíóú]+", text.lower())
    scores = {lang: sum(1 for w in words if w in stop) for lang, stop in STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best, scores


def numbers(text: str) -> Counter[str]:
    return Counter(n.replace(",", ".") for n in _NUMBER.findall(text))


def _fields(t: Listing | TranslatedListing) -> list[tuple[str, str]]:
    out = [("title", t.title), ("body_html", t.body_html)]
    out += [(f"bullets[{i}]", b) for i, b in enumerate(t.bullets)]
    out += [("meta_title", t.meta_title), ("meta_description", t.meta_description)]
    return out


def glossary_hits(source: str, target: str, lang: str, terms: dict[str, dict[str, str]]) -> tuple[int, int, list[str]]:
    """(expected, found, missing terms) for glossary terms present in the English source."""
    expected = found = 0
    missing: list[str] = []
    src, tgt = source.lower(), target.lower()
    for en, translations in terms.items():
        if lang not in translations or not re.search(rf"\b{re.escape(en)}\b", src):
            continue
        expected += 1
        options = [o.strip().lower() for o in translations[lang].split("|")]
        if any(o[: max(4, len(o) - 1)] in tgt for o in options):
            found += 1
        else:
            missing.append(f"{en} → {translations[lang]}")
    return expected, found, missing


def untranslated(source: Listing, target: TranslatedListing, keep: list[str]) -> list[str]:
    """Fields (bullets included) that came back word for word in English. Brand names alone do not count."""
    out = []
    src = dict(_fields(source))
    for name, text in _fields(target):
        original = src.get(name)
        if original is None or plain(text).strip().lower() != plain(original).strip().lower():
            continue
        words = plain(text)
        for term in keep:
            words = words.replace(term, " ")
        if len(re.findall(r"[A-Za-z]{3,}", words)) >= 2:
            out.append(name)
    return out


def check_translation(source: Listing, target: TranslatedListing, lang: str, store: StoreConfig) -> list[Issue]:
    issues: list[Issue] = []
    prefix = f"translations.{lang}"
    for name, text in _fields(target):
        if not text.strip():
            issues.append(Issue(code="translation_empty", severity="error", field=f"{prefix}.{name}", message="Empty"))
    if len(target.bullets) != len(source.bullets):
        issues.append(
            Issue(
                code="translation_bullets",
                severity="error",
                field=f"{prefix}.bullets",
                message=f"{len(target.bullets)} bullets, the source has {len(source.bullets)}",
            )
        )
    src_text = " ".join(plain(t) for _, t in _fields(source))
    tgt_text = " ".join(plain(t) for _, t in _fields(target))
    detected, scores = detect_language(tgt_text)
    if detected != lang:
        issues.append(
            Issue(
                code="translation_language",
                severity="error",
                field=prefix,
                message=f"Reads as {detected.upper()} (stopword counts {scores}), expected {lang.upper()}",
            )
        )
    for name in untranslated(source, target, store.glossary_keep):
        issues.append(
            Issue(
                code="translation_untranslated", severity="warning", field=f"{prefix}.{name}", message="Left in English"
            )
        )
    missing_numbers = numbers(src_text) - numbers(tgt_text)
    if missing_numbers:
        issues.append(
            Issue(
                code="translation_numbers",
                severity="error",
                field=prefix,
                span=", ".join(sorted(missing_numbers)),
                message=f"Numbers missing in the translation: {', '.join(sorted(missing_numbers))}",
            )
        )
    for term in store.glossary_keep:
        if term.lower() in src_text.lower() and term.lower() not in tgt_text.lower():
            issues.append(
                Issue(
                    code="translation_keep_term",
                    severity="error",
                    field=prefix,
                    span=term,
                    message=f"“{term}” must stay as it is",
                )
            )
    _, _, missing = glossary_hits(src_text, tgt_text, lang, store.glossary_terms)
    for m in missing:
        issues.append(
            Issue(
                code="translation_glossary",
                severity="warning",
                field=prefix,
                span=m,
                message=f"Glossary term not used: {m}",
            )
        )
    if sorted(_TAG.findall(source.body_html)) != sorted(_TAG.findall(target.body_html)):
        issues.append(
            Issue(
                code="translation_html",
                severity="warning",
                field=f"{prefix}.body_html",
                message="HTML structure differs from the English description",
            )
        )
    slack = store.translation_slack
    for name, text, limit in (
        ("title", target.title, store.title.max),
        ("meta_title", target.meta_title, store.meta_title.max),
        ("meta_description", target.meta_description, store.meta_description.max),
    ):
        if len(text) > limit * slack:
            issues.append(
                Issue(
                    code="too_long",
                    severity="error",
                    field=f"{prefix}.{name}",
                    message=f"{len(text)} characters, the limit is {int(limit * slack)} for translations",
                )
            )
        elif len(text) > limit:
            issues.append(
                Issue(
                    code="too_long",
                    severity="warning",
                    field=f"{prefix}.{name}",
                    message=f"{len(text)} characters, over the English limit of {limit}",
                )
            )
    return issues
