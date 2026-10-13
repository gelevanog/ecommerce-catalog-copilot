"""Builds the configured text and vision models, applying the free-only guard first, and wraps cloud models in the
budget wrapper (throttle, retries with backoff, hard call budget, ledger, disk cache)."""

from __future__ import annotations

from typing import Literal

from shelfwise.config import LLMProviderKind, Settings
from shelfwise.llm.base import ChatModel, LLMError
from shelfwise.llm.budget import BudgetedModel, DiskCache, shared_ledger, shared_throttle
from shelfwise.llm.guards import ensure_free_models
from shelfwise.llm.openai_compat import GUARDED_KINDS, OpenAICompatibleModel

Role = Literal["text", "vision"]


def build_model(
    settings: Settings,
    role: Role,
    *,
    provider: LLMProviderKind | None = None,
    model: str | None = None,
    fallback_models: list[str] | None = None,
    reasoning: str | None = None,
) -> ChatModel:
    kind: LLMProviderKind = provider or settings.llm_provider
    if model:
        name = model
    elif provider is None or provider == settings.llm_provider:
        name = settings.vision_model_name() if role == "vision" else settings.text_model_name()
    else:
        from shelfwise.config import DEFAULT_TEXT_MODELS, DEFAULT_VISION_MODELS

        name = (DEFAULT_VISION_MODELS if role == "vision" else DEFAULT_TEXT_MODELS)[kind]
    if fallback_models is not None:
        fallbacks = fallback_models
    elif model:
        fallbacks = []
    else:
        fallbacks = settings.vision_fallbacks() if role == "vision" else settings.text_fallbacks()
    # Guards first: nothing is constructed (no client, no key read) for a refused model.
    if kind in GUARDED_KINDS and settings.free_only:
        ensure_free_models([name, *(fallbacks if kind == "openrouter" else [])])
    if kind == "fake":
        from shelfwise.llm.fake import FakeModel

        return FakeModel(role)
    base_url = {
        "openrouter": settings.openrouter_base_url,
        "openai": settings.openai_base_url,
        "compat": settings.llm_base_url,
    }[kind]
    api_key = {
        "openrouter": settings.openrouter_api_key,
        "openai": settings.openai_api_key,
        "compat": settings.llm_api_key,
    }[kind]
    if not name:
        raise LLMError(f"no {role} model configured for the {kind} provider (SHELFWISE_{role.upper()}_MODEL)")
    return OpenAICompatibleModel(
        kind=kind,
        base_url=base_url,
        model=name,
        api_key=api_key,
        fallback_models=fallbacks if kind == "openrouter" else (),
        require_free=settings.free_only,
        timeout_seconds=settings.llm_timeout_seconds,
        reasoning_effort=(settings.llm_reasoning_effort if reasoning is None else reasoning)
        if kind == "openrouter"
        else "",
        send_temperature=settings.llm_send_temperature,
    )


def budgeted(model: ChatModel, settings: Settings, *, tag: str) -> ChatModel:
    """Cloud models get the throttle, retries, call budget and ledger; the offline model passes through."""
    if model.is_local:
        return model
    return BudgetedModel(
        model,
        ledger=shared_ledger(settings.llm_ledger, settings.llm_max_calls),
        cache=DiskCache(settings.llm_cache_dir) if settings.llm_cache else None,
        throttle=shared_throttle(settings.llm_min_seconds_between_requests),
        max_retries=settings.llm_max_retries,
        tag=tag,
    )


def model_for(settings: Settings, role: Role, *, tag: str, **kwargs: object) -> ChatModel:
    return budgeted(build_model(settings, role, **kwargs), settings, tag=tag)  # type: ignore[arg-type]
