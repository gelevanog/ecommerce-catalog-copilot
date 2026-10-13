"""Text tasks of the offline model: template copy from facts, template translations, a rule-based judge."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from shelfwise.taxonomy import _tokens

Handler = Callable[[dict[str, Any], str], dict[str, Any]]


def _facts(payload: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in payload.get("facts", []):
        out.setdefault(str(f.get("fact")), []).append(str(f.get("value")))
    return out


def _first(facts: dict[str, list[str]], key: str) -> str:
    return facts.get(key, [""])[0]


def _store_name(system: str) -> str:
    m = re.search(r"online store (.+?)\.\n", system)
    return m.group(1) if m else "our store"


def _limit(system: str, key: str, default: tuple[int, int]) -> tuple[int, int]:
    m = re.search(rf"{key} (\d+)-(\d+)", system)
    return (int(m.group(1)), int(m.group(2))) if m else default


def _product_type(payload: dict[str, Any]) -> tuple[str, int | None]:
    cands = payload.get("category_candidates") or []
    name_tokens = set(_tokens(str(payload.get("product_name", ""))))
    best_cat = max(cands, key=lambda c: len(name_tokens & set(_tokens(c["path"].rsplit(" > ", 1)[-1]))), default=None)
    types = payload.get("product_types") or [""]
    raw_name = str(payload.get("product_name", "")).lower()
    for t in types:
        stem = t.lower().rstrip("s")
        if stem and re.search(rf"\b{re.escape(stem)}", raw_name):
            return t, (best_cat["id"] if best_cat else None)
    leaf_tokens = set(_tokens(best_cat["path"].rsplit(" > ", 1)[-1])) if best_cat else set()
    best_type = max(types, key=lambda t: (len(set(_tokens(t)) & (name_tokens | leaf_tokens)), -types.index(t)))
    return best_type, (best_cat["id"] if best_cat else None)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:–- ")


def write_listing(payload: dict[str, Any], system: str) -> dict[str, Any]:
    facts = _facts(payload)
    store = _store_name(system)
    name = str(payload.get("product_name", "Product"))
    brand = str(payload.get("brand", "")).strip()
    color = _first(facts, "color")
    pattern = _first(facts, "pattern")
    material = _first(facts, "material")
    composition = _first(facts, "composition") or material
    count_raw = _first(facts, "count")
    count = int(count_raw.split()[0]) if count_raw[:1].isdigit() else 1
    noun = name.split()[-1].lower() if name.split() else "product"
    pattern_word = "" if pattern in ("", "solid") else pattern
    desc = " ".join(w for w in (color, pattern_word, noun) if w)
    title_bits = [w.title() for w in (color, pattern_word) if w]
    if count > 1:
        title_bits.insert(0, f"Set of {count},")
    key = " ".join(title_bits).rstrip(",")
    if material and len(f"{brand} {name} – {key} {material.title()}") <= 70:
        key = f"{key} {material.title()}".strip()
    title = f"{brand} {name} – {key}".strip() if key else f"{brand} {name}".strip()
    _, hi = _limit(system, "title", (20, 70))
    title = _trim(title, hi)
    article = "an" if desc[:1].lower() in "aeiou" else "a"
    first = f"The {name} is {article} {desc} from {brand}" + (
        f", made of {composition.lower()}." if composition else "."
    )
    sentences = [first]
    if count > 1:
        sentences.append(f"The pack holds {count} pieces.")
    for fact, verb in (("dimensions", "measures"), ("weight", "weighs"), ("capacity", "holds")):
        if facts.get(fact):
            sentences.append(f"It {verb} {facts[fact][0]}.")
    features = facts.get("feature", [])
    second = [f"{f.rstrip('.')}." for f in features]
    if facts.get("care"):
        second.append(f"Care: {facts['care'][0].rstrip('.')}.")
    if facts.get("origin"):
        second.append(f"{facts['origin'][0].rstrip('.')}.")
    sentences.append(f"It is part of the {brand} range at {store.rstrip('.')}.")
    body = f"<p>{' '.join(sentences)}</p>" + (f"<p>{' '.join(second)}</p>" if second else "")
    bullets: list[str] = []
    if composition:
        bullets.append(composition[:1].upper() + composition[1:])
    for fact in ("dimensions", "capacity"):
        if facts.get(fact):
            bullets.append(f"{fact.title()}: {facts[fact][0]}")
    bullets += [f.rstrip(".") for f in features]
    if facts.get("care"):
        bullets.append(facts["care"][0].rstrip("."))
    if count > 1:
        bullets.append(f"Set of {count}")
    if color and len(bullets) < 3:
        bullets.append(f"Color: {color}")
    if len(bullets) < 3 and facts.get("weight"):
        bullets.append(f"Weight: {facts['weight'][0]}")
    bullets = [b[:160] for b in bullets[:5]]
    meta_title = f"{name} – {color.title()} | {store}" if color else f"{name} | {store}"
    if len(meta_title) > 60:
        meta_title = _trim(f"{name} – {color.title()}" if color else name, 60)
    meta = (
        f"{brand} {name}"
        + (f" in {color}" if color else "")
        + (f", {composition.lower()}" if composition else "")
        + "."
    )
    if features:
        meta += f" {features[0].rstrip('.')}."
    if len(meta) < 70:
        meta += f" Shop it at {store}".rstrip(".") + "."
    meta = _trim(meta, 160)
    product_type, category_id = _product_type(payload)
    tags = [t for t in dict.fromkeys([color, pattern_word, material, product_type.lower(), brand.lower()]) if t]
    alt = " ".join(w for w in (color.title() if color else "", pattern_word, noun) if w)
    alt += f", {count} pieces" if count > 1 else ""
    alt += " on a light background"
    images = int(payload.get("images", 1) or 1)
    return {
        "title": title,
        "description_html": body,
        "bullets": bullets,
        "meta_title": meta_title,
        "meta_description": meta,
        "tags": tags,
        "product_type": product_type,
        "category_id": category_id,
        "alt_texts": [alt[:125]] * images,
    }


# ---------------------------------------------------------------- template translations

WORDS: dict[str, dict[str, str]] = {
    "black": {"de": "schwarz", "es": "negro", "fr": "noir"},
    "white": {"de": "weiß", "es": "blanco", "fr": "blanc"},
    "gray": {"de": "grau", "es": "gris", "fr": "gris"},
    "navy": {"de": "marineblau", "es": "azul marino", "fr": "bleu marine"},
    "blue": {"de": "blau", "es": "azul", "fr": "bleu"},
    "light blue": {"de": "hellblau", "es": "azul claro", "fr": "bleu clair"},
    "teal": {"de": "petrol", "es": "verde azulado", "fr": "bleu canard"},
    "green": {"de": "grün", "es": "verde", "fr": "vert"},
    "olive": {"de": "oliv", "es": "oliva", "fr": "olive"},
    "red": {"de": "rot", "es": "rojo", "fr": "rouge"},
    "burgundy": {"de": "bordeaux", "es": "burdeos", "fr": "bordeaux"},
    "pink": {"de": "rosa", "es": "rosa", "fr": "rose"},
    "orange": {"de": "orange", "es": "naranja", "fr": "orange"},
    "yellow": {"de": "gelb", "es": "amarillo", "fr": "jaune"},
    "mustard": {"de": "senfgelb", "es": "mostaza", "fr": "moutarde"},
    "beige": {"de": "beige", "es": "beige", "fr": "beige"},
    "brown": {"de": "braun", "es": "marrón", "fr": "marron"},
    "purple": {"de": "lila", "es": "morado", "fr": "violet"},
    "striped": {"de": "gestreift", "es": "a rayas", "fr": "rayé"},
    "polka dot": {"de": "gepunktet", "es": "de lunares", "fr": "à pois"},
    "checked": {"de": "kariert", "es": "de cuadros", "fr": "à carreaux"},
    "floral": {"de": "geblümt", "es": "floral", "fr": "fleuri"},
    "geometric": {"de": "geometrisch", "es": "geométrico", "fr": "géométrique"},
    "organic cotton": {"de": "Bio-Baumwolle", "es": "algodón orgánico", "fr": "coton biologique"},
    "combed cotton": {"de": "gekämmter Baumwolle", "es": "algodón peinado", "fr": "coton peigné"},
    "cotton canvas": {"de": "Baumwoll-Canvas", "es": "lona de algodón", "fr": "toile de coton"},
    "cotton denim": {"de": "Baumwoll-Denim", "es": "denim de algodón", "fr": "denim de coton"},
    "recycled": {"de": "recyceltem", "es": "reciclado", "fr": "recyclé"},
    "cotton": {"de": "Baumwolle", "es": "algodón", "fr": "coton"},
    "wool": {"de": "Wolle", "es": "lana", "fr": "laine"},
    "merino": {"de": "Merino", "es": "merino", "fr": "mérinos"},
    "linen": {"de": "Leinen", "es": "lino", "fr": "lin"},
    "polyester": {"de": "Polyester", "es": "poliéster", "fr": "polyester"},
    "nylon": {"de": "Nylon", "es": "nailon", "fr": "nylon"},
    "leather": {"de": "Leder", "es": "cuero", "fr": "cuir"},
    "canvas": {"de": "Canvas", "es": "lona", "fr": "toile"},
    "stoneware": {"de": "Steinzeug", "es": "gres", "fr": "grès"},
    "glazed": {"de": "glasiertem", "es": "esmaltado", "fr": "émaillé"},
    "porcelain": {"de": "Porzellan", "es": "porcelana", "fr": "porcelaine"},
    "ceramic": {"de": "Keramik", "es": "cerámica", "fr": "céramique"},
    "glass": {"de": "Glas", "es": "vidrio", "fr": "verre"},
    "stainless steel": {"de": "Edelstahl", "es": "acero inoxidable", "fr": "acier inoxydable"},
    "wood": {"de": "Holz", "es": "madera", "fr": "bois"},
    "bamboo": {"de": "Bambus", "es": "bambú", "fr": "bambou"},
    "terracotta": {"de": "Terrakotta", "es": "terracota", "fr": "terre cuite"},
    "wax": {"de": "Wachs", "es": "cera", "fr": "cire"},
    "dishwasher safe": {"de": "spülmaschinenfest", "es": "apto para lavavajillas", "fr": "compatible lave-vaisselle"},
    "microwave safe": {"de": "mikrowellengeeignet", "es": "apto para microondas", "fr": "compatible micro-ondes"},
    "machine wash cold": {
        "de": "Maschinenwäsche kalt",
        "es": "lavar a máquina en frío",
        "fr": "lavage en machine à froid",
    },
    "hand wash": {"de": "Handwäsche", "es": "lavar a mano", "fr": "lavage à la main"},
    "tumble dry low": {
        "de": "Trockner niedrige Stufe",
        "es": "secadora a baja temperatura",
        "fr": "sèche-linge basse température",
    },
    "made in": {"de": "Hergestellt in", "es": "Fabricado en", "fr": "Fabriqué au"},
    "set of": {"de": "Set mit", "es": "set de", "fr": "lot de"},
    "pieces": {"de": "Stück", "es": "piezas", "fr": "pièces"},
    "and": {"de": "und", "es": "y", "fr": "et"},
    "with": {"de": "mit", "es": "con", "fr": "avec"},
    "dimensions": {"de": "Maße", "es": "medidas", "fr": "dimensions"},
    "capacity": {"de": "Fassungsvermögen", "es": "capacidad", "fr": "contenance"},
    "weight": {"de": "Gewicht", "es": "peso", "fr": "poids"},
    "color": {"de": "Farbe", "es": "color", "fr": "couleur"},
    "on a light background": {"de": "auf hellem Hintergrund", "es": "sobre fondo claro", "fr": "sur fond clair"},
    "shop it at": {"de": "Erhältlich bei", "es": "Cómpralo en", "fr": "Disponible chez"},
}
TEMPLATES: list[tuple[str, dict[str, str]]] = [
    (
        r"The (?P<name>.+?) is an? (?P<desc>.+?) from (?P<brand>[\w&.]+), made of (?P<comp>.+?)\.",
        {
            "de": "{name} von {brand} ist ein {desc} aus {comp}.",
            "es": "El {name} de {brand} es un {desc} de {comp}.",
            "fr": "Le {name} de {brand} est un {desc} en {comp}.",
            "en": "The {name} is a {desc} from {brand}, made of {comp}.",
        },
    ),
    (
        r"The (?P<name>.+?) is an? (?P<desc>.+?) from (?P<brand>[\w&.]+)\.",
        {
            "de": "{name} von {brand} ist ein {desc}.",
            "es": "El {name} de {brand} es un {desc}.",
            "fr": "Le {name} de {brand} est un {desc}.",
            "en": "The {name} is a {desc} from {brand}.",
        },
    ),
    (
        r"The pack holds (?P<n>\d+) pieces\.",
        {
            "de": "Die Packung enthält {n} Stück.",
            "es": "El paquete contiene {n} piezas.",
            "fr": "Le lot contient {n} pièces.",
            "en": "The pack holds {n} pieces.",
        },
    ),
    (
        r"It measures (?P<x>.+?)\.",
        {"de": "Die Maße sind {x}.", "es": "Mide {x}.", "fr": "Il mesure {x}.", "en": "It measures {x}."},
    ),
    (
        r"It is part of the (?P<brand>[\w&.]+) range at (?P<store>.+?)\.",
        {
            "de": "Es gehört zum Sortiment von {brand} bei {store}.",
            "es": "Forma parte de la gama {brand} de {store}.",
            "fr": "Il fait partie de la gamme {brand} chez {store}.",
            "en": "It is part of the {brand} range at {store}.",
        },
    ),
    (
        r"It weighs (?P<x>.+?)\.",
        {"de": "Das Gewicht ist {x}.", "es": "Pesa {x}.", "fr": "Il pèse {x}.", "en": "It weighs {x}."},
    ),
    (
        r"It holds (?P<x>.+?)\.",
        {
            "de": "Das Fassungsvermögen ist {x}.",
            "es": "Tiene una capacidad de {x}.",
            "fr": "Il a une contenance de {x}.",
            "en": "It holds {x}.",
        },
    ),
    (
        r"Care: (?P<x>.+?)\.",
        {"de": "Pflege: {x}.", "es": "Cuidado: {x}.", "fr": "Entretien : {x}.", "en": "Care: {x}."},
    ),
]
_KEEP = ("Fernhill & Co.", "Larkspur", "Northbay")


def _words(text: str, lang: str) -> str:
    out = text
    for en in sorted(WORDS, key=len, reverse=True):
        target = WORDS[en].get(lang)
        if target:
            out = re.sub(rf"(?<![\w-]){re.escape(en)}(?![\w-])", target, out, flags=re.IGNORECASE)
    return out


def translate_text(text: str, lang: str) -> str:
    def sentence(s: str) -> str:
        for pattern, forms in TEMPLATES:
            m = re.fullmatch(pattern, s.strip())
            if m:
                parts = {
                    k: (v if k in {"name", "brand", "n", "store"} else _words(v, lang))
                    for k, v in m.groupdict().items()
                }
                return forms[lang].format(**parts)
        return _words(s, lang)

    pieces = re.split(r"(<[^>]+>)", text)
    out = []
    for piece in pieces:
        if piece.startswith("<"):
            out.append(piece)
            continue
        sentences = re.split(r"(?<=\.)\s+", piece)
        out.append(" ".join(sentence(s) for s in sentences if s))
    return "".join(out)


def translate_listing(payload: dict[str, Any], system: str) -> dict[str, Any]:
    langs = re.findall(r"\b(de|es|fr|it|nl) \(", system) or ["de", "es", "fr"]
    out = {}
    for lang in langs:
        out[lang] = {
            "title": translate_text(str(payload.get("title", "")), lang),
            "description_html": translate_text(str(payload.get("description_html", "")), lang),
            "bullets": [translate_text(str(b), lang) for b in payload.get("bullets", [])],
            "meta_title": translate_text(str(payload.get("meta_title", "")), lang),
            "meta_description": translate_text(str(payload.get("meta_description", "")), lang),
        }
    return out


def _formatter(form: str) -> Callable[[re.Match[str]], str]:
    def render(m: re.Match[str]) -> str:
        return form.format(**m.groupdict())

    return render


def back_translate(payload: dict[str, Any], system: str) -> dict[str, Any]:
    lang = str(payload.get("language", "de"))
    reverse = {forms[lang]: forms["en"] for _, forms in TEMPLATES}

    def back(text: str) -> str:
        out = text
        for target_form, en_form in reverse.items():
            pattern = re.escape(target_form)
            for slot in re.findall(r"\\\{(\w+)\\\}", pattern):
                pattern = pattern.replace(rf"\{{{slot}\}}", rf"(?P<{slot}>.+?)")
            out = re.sub(pattern, _formatter(en_form), out)
        for en in sorted(WORDS, key=lambda w: len(WORDS[w].get(lang, "")), reverse=True):
            target = WORDS[en].get(lang)
            if target:
                out = re.sub(rf"(?<![\w-]){re.escape(target)}(?![\w-])", en, out)
        return out

    return {
        "title": back(str(payload.get("title", ""))),
        "description_html": back(str(payload.get("description_html", ""))),
        "bullets": [back(str(b)) for b in payload.get("bullets", [])],
        "meta_title": back(str(payload.get("meta_title", ""))),
        "meta_description": back(str(payload.get("meta_description", ""))),
    }


def judge_listing(payload: dict[str, Any], system: str) -> dict[str, Any]:
    """The offline judge is the deterministic claims check itself (so its agreement with the check is 100% by
    construction; the evaluation runs a real model as the judge)."""
    from shelfwise.catalog.models import Fact, Listing
    from shelfwise.config import get_store
    from shelfwise.validate.claims import build_evidence, check_text

    facts = [Fact(key=f["fact"], value=f["value"], display=f["value"], id=f["id"]) for f in payload.get("facts", [])]
    ev = build_evidence(facts, [], [], str(payload.get("product_name", "")))
    for f in payload.get("facts", []):
        if f["fact"] == "color":
            ev.colors[f["value"]] = f["id"]
        if f["fact"] == "pattern":
            ev.patterns[f["value"]] = f["id"]
        if f["fact"] == "count":
            ev.counts[int(str(f["value"]).split()[0])] = f["id"]
    listing = Listing.model_validate(payload.get("listing", {}))
    store = get_store()
    found = []
    for field, text in listing.text_fields():
        for issue in check_text(field, text, ev, store).issues:
            if issue.severity == "error":
                found.append({"field": field, "claim": issue.span or issue.message})
    return {"unsupported": found}


HANDLERS: dict[str, Handler] = {
    "write_listing": write_listing,
    "translate_listing": translate_listing,
    "back_translate": back_translate,
    "judge_listing": judge_listing,
}

__all__ = ["HANDLERS", "translate_text"]
_ = _KEEP
