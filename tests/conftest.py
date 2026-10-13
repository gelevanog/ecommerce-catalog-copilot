"""Shared fixtures: settings that never touch the network, the repository's results or the user's cache."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from shelfwise.catalog.models import ProductRecord
from shelfwise.config import REPO_ROOT, Settings, get_store
from shelfwise.services import Copilot
from shelfwise.store import Store

DEMO = REPO_ROOT / "data" / "demo"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database=tmp_path / "test.db",
        workspace_dir=tmp_path / "workspace",
        demo_dir=DEMO,
        results_dir=tmp_path / "results",
        llm_provider="fake",
        llm_ledger=None,
        llm_cache=False,
        llm_cache_dir=tmp_path / "llm-cache",
        seed_demo=False,
        llm_concurrency=2,
        openrouter_api_key=None,
    )


@pytest.fixture()
def store(settings: Settings):  # type: ignore[no-untyped-def]
    return get_store(settings)


@pytest.fixture()
def copilot(settings: Settings) -> Iterator[Copilot]:
    yield Copilot(settings, store=Store(":memory:"))


@pytest.fixture()
def demo_batch(copilot: Copilot) -> str:
    return copilot.import_demo().batch_id


@pytest.fixture()
def enriched(copilot: Copilot, demo_batch: str) -> list[ProductRecord]:
    ids = [r.id for r in copilot.db.products(demo_batch)]
    return copilot.enrich_many(ids)
