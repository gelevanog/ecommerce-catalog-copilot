"""The enrichment pipeline end to end with the offline model, and with scripted models for the corrective round
and the translation fallbacks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from shelfwise.catalog.models import ProductRecord
from shelfwise.config import Settings, StoreConfig
from shelfwise.enrich.pipeline import Models, enrich
from shelfwise.llm.base import Completion, LLMError, Message, text_of
from shelfwise.llm.fake import FakeModel
from shelfwise.services import Copilot

from .conftest import DEMO


def test_offline_enrichment_of_the_demo(enriched: list[ProductRecord]) -> None:
    assert len(enriched) == 64
    assert all(r.listing is not None for r in enriched)
    statuses = {r.status for r in enriched}
    assert statuses <= {"ready", "needs_review", "blocked"}
    blocked = [r for r in enriched if r.status == "blocked"]
    assert len(blocked) == 4  # the planted variant problems
    for r in enriched:
        assert r.listing is not None
        assert set(r.listing.translations) == {"de", "es", "fr"}
        assert not [i for i in r.issues if i.code.startswith("unsupported_") or i.code == "banned_claim"]
        assert r.stats.calls == 3


def test_provenance_points_at_facts(enriched: list[ProductRecord]) -> None:
    record = next(r for r in enriched if r.product.fact("capacity"))
    capacity = record.product.fact("capacity")
    assert capacity is not None
    assert any(capacity.id in ids for ids in record.provenance.values())


class Hallucinator:
    """First answer invents a spec and a banned claim; the corrective round fixes them."""

    label = "test/hallucinator"
    is_local = True

    def __init__(self) -> None:
        self.fake = FakeModel("text")
        self.copy_calls = 0

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0, json_mode: bool = False
    ) -> Completion:
        system = text_of(messages[0])
        if "write_listing" not in system:
            return self.fake.complete(messages, max_tokens=max_tokens)
        self.copy_calls += 1
        good = json.loads(self.fake.complete(messages[:2], max_tokens=max_tokens).text)
        if self.copy_calls == 1:
            good["bullets"] = [*good["bullets"][:3], "Holds 999 ml", "The best choice"]
        return Completion(text=json.dumps(good), model="hallucinator")


def _record(copilot: Copilot, batch: str, index: int = 5) -> ProductRecord:
    return copilot.db.products(batch)[index]


def test_corrective_round_removes_unsupported_claims(
    copilot: Copilot, demo_batch: str, settings: Settings, store: StoreConfig
) -> None:
    model = Hallucinator()
    record = enrich(
        _record(copilot, demo_batch),
        store=store,
        settings=settings,
        models=Models(vision=FakeModel("vision"), text=model),
        images_dir=DEMO / "images",
    )
    assert model.copy_calls == 2
    assert any(c.startswith("unsupported_spec") for c in record.copy_attempts[0])
    assert any(c.startswith("banned_claim") for c in record.copy_attempts[0])
    assert record.copy_attempts[1] == []
    assert record.listing is not None and "999" not in " ".join(record.listing.bullets)


class FlakyTranslator:
    label = "test/flaky"
    is_local = True

    def __init__(self) -> None:
        self.fake = FakeModel("text")
        self.calls: list[str] = []

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0, json_mode: bool = False
    ) -> Completion:
        system = text_of(messages[0])
        if "translate_listing" in system and "fr (" in system and "de (" in system:
            self.calls.append("all")
            raise LLMError("empty answer: max_tokens reached before any output")
        if "translate_listing" in system:
            self.calls.append("one")
        return self.fake.complete(messages, max_tokens=max_tokens)


def test_translation_falls_back_to_one_call_per_language(
    copilot: Copilot, demo_batch: str, settings: Settings, store: StoreConfig
) -> None:
    flaky = FlakyTranslator()
    record = enrich(
        _record(copilot, demo_batch),
        store=store,
        settings=settings,
        models=Models(vision=FakeModel("vision"), text=FakeModel("text"), translate=flaky),
        images_dir=DEMO / "images",
    )
    assert flaky.calls == ["all", "one", "one", "one"]
    assert record.listing is not None and set(record.listing.translations) == {"de", "es", "fr"}


def test_missing_image_is_reported(
    copilot: Copilot, demo_batch: str, settings: Settings, store: StoreConfig, tmp_path: Any
) -> None:
    record = enrich(
        _record(copilot, demo_batch),
        store=store,
        settings=settings,
        models=Models(vision=FakeModel("vision"), text=FakeModel("text")),
        images_dir=tmp_path,
        translate=False,
    )
    assert any(i.code == "vision_failed" for i in record.issues)
    assert record.listing is not None
