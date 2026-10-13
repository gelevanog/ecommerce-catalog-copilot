"""The data model: what a product is at each step, and where every value came from."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SourceKind = Literal["feed", "image", "style_guide", "taxonomy", "model", "editor", "rule"]
Severity = Literal["error", "warning", "info"]
DecisionStatus = Literal["agree", "conflict", "feed_only", "image_only", "missing"]
ProductStatus = Literal["imported", "enriched", "needs_review", "ready", "approved", "exported", "blocked"]


class Evidence(BaseModel):
    """One piece of support for a value: a feed cell, an image, a rule or a person."""

    source: SourceKind
    ref: str
    """Human-readable location: "northbay_feed.csv · row 7 · colour_name", "images/nb-2201.jpg"."""
    quote: str = ""
    """The raw text before normalization, or the model's own words."""


class Fact(BaseModel):
    """A normalized fact about a product, with the evidence it came from. Listings may only state facts."""

    key: str
    """color, secondary_color, pattern, material, composition, style, count, dimensions, weight, capacity, care,
    feature, origin, size, brand."""
    value: Any
    display: str
    evidence: list[Evidence] = Field(default_factory=list)
    id: str = ""
    """F1, F2, ... assigned per product, so prompts and the review UI can point at a fact."""


class Issue(BaseModel):
    code: str
    severity: Severity
    message: str
    field: str = ""
    """listing field ("title", "bullets[2]", "translations.de.title") or attribute ("color")."""
    span: str = ""
    """The offending text, when there is one."""
    waived: str = ""
    """Set when a reviewer accepted the issue on purpose: who and why ("ivan: price confirmed with supplier")."""


class SourceRef(BaseModel):
    file: str
    row: int
    """1-based row number as a person would see it in the spreadsheet (header rows included)."""


class Variant(BaseModel):
    sku: str
    options: dict[str, str] = Field(default_factory=dict)
    price: float | None = None
    currency: str = "USD"
    source_price: str = ""
    inventory: int | None = None
    weight_g: float | None = None
    gtin: str | None = None
    source: SourceRef


class ColumnMapping(BaseModel):
    """Supplier column -> store field, with how the mapping was decided."""

    column: str
    target: str | None
    method: Literal["synonym", "values", "model", "user", "unmapped"]
    confidence: float = 1.0
    samples: list[str] = Field(default_factory=list)


class ImportedProduct(BaseModel):
    id: str
    handle: str
    supplier_file: str
    title_raw: str
    vendor: str | None = None
    supplier_category: str = ""
    facts: list[Fact] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    notes_raw: str = ""
    """Supplier free text (features, notes). Kept for the reviewer; not trusted for claims beyond the facts parsed
    from it."""
    merged_duplicates: list[SourceRef] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    def fact(self, key: str) -> Fact | None:
        return next((f for f in self.facts if f.key == key), None)

    def facts_of(self, key: str) -> list[Fact]:
        return [f for f in self.facts if f.key == key]


class ImageAttributes(BaseModel):
    """What the vision model saw, normalized onto the vocabularies (raw words kept)."""

    image: str
    model: str
    color: str | None = None
    secondary_color: str | None = None
    pattern: str | None = None
    material: str | None = None
    style: str | None = None
    count: int | None = None
    product_noun: str = ""
    visible_text: str = ""
    confidence: dict[str, float] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class AttributeDecision(BaseModel):
    attribute: str
    value: Any
    status: DecisionStatus
    feed_value: Any = None
    image_value: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    needs_review: bool = False
    note: str = ""


class TranslatedListing(BaseModel):
    title: str = ""
    body_html: str = ""
    bullets: list[str] = Field(default_factory=list)
    meta_title: str = ""
    meta_description: str = ""


class Listing(BaseModel):
    title: str = ""
    body_html: str = ""
    bullets: list[str] = Field(default_factory=list)
    meta_title: str = ""
    meta_description: str = ""
    tags: list[str] = Field(default_factory=list)
    product_type: str = ""
    category_id: int | None = None
    category_path: str = ""
    alt_texts: list[str] = Field(default_factory=list)
    translations: dict[str, TranslatedListing] = Field(default_factory=dict)

    def text_fields(self, *, translations: bool = False) -> list[tuple[str, str]]:
        """(field name, text) pairs for validation, in display order."""
        out: list[tuple[str, str]] = [("title", self.title), ("body_html", self.body_html)]
        out += [(f"bullets[{i}]", b) for i, b in enumerate(self.bullets)]
        out += [("meta_title", self.meta_title), ("meta_description", self.meta_description)]
        out += [(f"alt_texts[{i}]", a) for i, a in enumerate(self.alt_texts)]
        if translations:
            for lang, t in self.translations.items():
                out += [(f"translations.{lang}.title", t.title), (f"translations.{lang}.body_html", t.body_html)]
                out += [(f"translations.{lang}.bullets[{i}]", b) for i, b in enumerate(t.bullets)]
                out += [(f"translations.{lang}.meta_title", t.meta_title)]
                out += [(f"translations.{lang}.meta_description", t.meta_description)]
        return out


class FieldReview(BaseModel):
    state: Literal["pending", "approved", "edited"] = "pending"
    by: str = ""
    at: str = ""


class CallStats(BaseModel):
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    models: list[str] = Field(default_factory=list)


class ProductRecord(BaseModel):
    """Everything the review screen shows for one product."""

    id: str
    batch_id: str
    status: ProductStatus = "imported"
    product: ImportedProduct
    image_attributes: list[ImageAttributes] = Field(default_factory=list)
    decisions: list[AttributeDecision] = Field(default_factory=list)
    generated: Listing | None = None
    """The listing exactly as the pipeline produced it (kept for the before/after diff)."""
    listing: Listing | None = None
    """The current listing: generated, then edited by a reviewer."""
    provenance: dict[str, list[str]] = Field(default_factory=dict)
    """listing field -> ids of the facts it states ("F3")."""
    issues: list[Issue] = Field(default_factory=list)
    review: dict[str, FieldReview] = Field(default_factory=dict)
    stats: CallStats = Field(default_factory=CallStats)
    error: str = ""

    def errors(self) -> list[Issue]:
        """Open errors: the listing cannot be approved while there are any."""
        return [i for i in self.issues if i.severity == "error" and not i.waived]

    def decision(self, attribute: str) -> AttributeDecision | None:
        return next((d for d in self.decisions if d.attribute == attribute), None)
