"""Feed vs photo: one decision per attribute, with both values, the evidence and whether a person must look.

Policy (stated in the review screen):
- color, pattern and count are visual: when feed and photo disagree, the photo's value is suggested if the model
  was confident, and the product goes to review either way;
- material is a spec: the feed wins a disagreement, and a material seen only in the photo is a suggestion for the
  reviewer, never stated in the listing;
- style comes only from the photo and is used for tags, never as a spec.
"""

from __future__ import annotations

from typing import Any

from shelfwise.catalog.models import AttributeDecision, Evidence, ImageAttributes, ImportedProduct
from shelfwise.vocab import COLOR_FAMILIES, colors_match

CONFIDENT = 0.7
USABLE_IMAGE_ONLY = {"color", "pattern", "count", "style"}
"""Attributes a listing may state on photo evidence alone (visible facts). Material is not one of them."""


def _image_evidence(attrs: ImageAttributes, attribute: str, value: Any) -> Evidence:
    raw = attrs.raw.get(attribute)
    conf = attrs.confidence.get(attribute)
    quote = f"{raw}" + (f" (confidence {conf:.2f})" if conf is not None else "")
    return Evidence(source="image", ref=f"images/{attrs.image} · {attrs.model}", quote=quote if raw is not None else str(value))


def decide(product: ImportedProduct, attrs: ImageAttributes | None) -> list[AttributeDecision]:
    decisions = []
    for attribute in ("color", "pattern", "material", "count", "style"):
        fact = product.fact(attribute)
        feed_value = fact.value if fact else None
        image_value = getattr(attrs, attribute) if attrs and not attrs.error else None
        if attribute == "pattern" and feed_value is None and image_value == "solid":
            pass  # a plain product: feeds rarely say "solid"; the photo decides
        evidence: list[Evidence] = list(fact.evidence) if fact else []
        if image_value is not None and attrs is not None:
            evidence.append(_image_evidence(attrs, attribute, image_value))
        confidence = attrs.confidence.get(attribute, 0.5) if attrs else 0.0
        decisions.append(_decide_one(attribute, feed_value, image_value, confidence, attrs, evidence))
    return decisions


def _same(attribute: str, a: Any, b: Any, attrs: ImageAttributes | None) -> tuple[bool, str]:
    if attribute == "color":
        if colors_match(a, b):
            return True, ""
        if colors_match(a, b, lenient=True):
            return True, f"close colors: feed “{a}”, photo “{b}”"
        if attrs is not None and attrs.secondary_color and colors_match(a, attrs.secondary_color, lenient=True):
            return True, f"the feed names the product's second color ({a}); main color in the photo: {b}"
        return False, ""
    return a == b, ""


def _decide_one(
    attribute: str, feed: Any, image: Any, confidence: float, attrs: ImageAttributes | None, evidence: list[Evidence]
) -> AttributeDecision:
    if feed is None and image is None:
        return AttributeDecision(attribute=attribute, value=None, status="missing", evidence=evidence)
    if feed is not None and image is None:
        return AttributeDecision(attribute=attribute, value=feed, status="feed_only", feed_value=feed, evidence=evidence)
    if feed is None:
        review = attribute == "material" or confidence < 0.5
        note = "seen in the photo only: a suggestion, not stated in the listing" if attribute == "material" else ""
        return AttributeDecision(attribute=attribute, value=image, status="image_only", image_value=image,
                                 evidence=evidence, needs_review=review, note=note)
    same, note = _same(attribute, feed, image, attrs)
    if same:
        value = feed
        if attribute == "color" and note.startswith("the feed names"):
            value = image
        return AttributeDecision(attribute=attribute, value=value, status="agree", feed_value=feed, image_value=image,
                                 evidence=evidence, note=note)
    if attribute == "material":
        value, note = feed, f"photo suggests {image}; the feed's material is kept (a spec), please check"
    elif confidence >= CONFIDENT:
        value, note = image, f"feed says {feed}, photo shows {image}: photo value suggested"
    else:
        value, note = feed, f"feed says {feed}, photo maybe {image} (low confidence): feed value kept"
    return AttributeDecision(attribute=attribute, value=value, status="conflict", feed_value=feed, image_value=image,
                             evidence=evidence, needs_review=True, note=note)


def usable(decision: AttributeDecision) -> bool:
    """May the listing state this value?"""
    if decision.value is None:
        return False
    if decision.status == "image_only":
        return decision.attribute in USABLE_IMAGE_ONLY
    return True


def color_family(color: str | None) -> frozenset[str]:
    return COLOR_FAMILIES.get(color or "", frozenset())
