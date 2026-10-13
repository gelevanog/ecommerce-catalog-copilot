"""The model layer: the free-only guard, the budget wrapper (cache, retries, ledger, cap), the OpenAI-compatible
request shape with images, JSON parsing, and the offline model."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from shelfwise.config import Settings
from shelfwise.llm.base import (
    BudgetExceededError,
    Completion,
    LLMError,
    Message,
    PolicyViolationError,
    RetryableLLMError,
    image_part,
)
from shelfwise.llm.budget import BudgetedModel, CallLedger, DiskCache, Throttle
from shelfwise.llm.factory import build_model
from shelfwise.llm.guards import ensure_free_body, ensure_free_models, ensure_served_free
from shelfwise.llm.jsonparse import JSONReplyError, parse_json_object
from shelfwise.llm.openai_compat import OpenAICompatibleModel

from .conftest import DEMO

FAKE_KEY = "test-" + "k" * 8  # built at runtime, never a real key


def test_guard_refuses_paid_and_online_models() -> None:
    ensure_free_models(["nvidia/nemotron-3-super-120b-a12b:free"])
    for bad in ("openai/gpt-5.4-mini", "x/y:free:online", ""):
        with pytest.raises(PolicyViolationError):
            ensure_free_models([bad])
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a:free", "models": ["a:free", "b"]})
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a:free", "plugins": [{"id": "web"}]})
    with pytest.raises(PolicyViolationError):
        ensure_served_free("openai/gpt-5.4-mini")
    ensure_served_free(None)


def test_factory_refuses_before_building(settings: Settings) -> None:
    s = settings.model_copy(
        update={"llm_provider": "openrouter", "text_model": "openai/gpt-5.4-mini", "openrouter_api_key": FAKE_KEY}
    )
    with pytest.raises(PolicyViolationError):
        build_model(s, "text")
    s = settings.model_copy(
        update={
            "llm_provider": "compat",
            "text_model": "claude-haiku-5-5",
            "llm_api_key": FAKE_KEY,
            "llm_base_url": "http://127.0.0.1:9/v1",
        }
    )
    with pytest.raises(PolicyViolationError):
        build_model(s, "text")


def test_paid_models_need_the_explicit_opt_in(settings: Settings) -> None:
    s = settings.model_copy(
        update={
            "llm_provider": "compat",
            "text_model": "claude-haiku-5-5",
            "llm_api_key": FAKE_KEY,
            "llm_base_url": "http://127.0.0.1:9/v1",
            "allow_paid_models": True,
        }
    )
    model = build_model(s, "text")
    assert model.label == "compat/claude-haiku-5-5"


def test_default_free_models(settings: Settings) -> None:
    s = settings.model_copy(update={"llm_provider": "openrouter", "openrouter_api_key": FAKE_KEY})
    vision, text = build_model(s, "vision"), build_model(s, "text")
    assert vision.label.endswith(":free") and text.label.endswith(":free")
    assert all(m.endswith(":free") for m in text.fallback_models)


def _transport(answers: list[httpx.Response], seen: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"body": json.loads(request.content), "headers": dict(request.headers)})
        return answers.pop(0)

    return httpx.MockTransport(handler)


def _ok(text: str, model: str = "dots-studio/dots-3-note-preview:free") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


def test_request_shape_with_an_image() -> None:
    seen: list[dict[str, Any]] = []
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://openrouter.test/api/v1",
        model="dots-studio/dots-3-note-preview:free",
        api_key=FAKE_KEY,
        fallback_models=["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"],
        transport=_transport([_ok('{"color": "red"}')], seen),
    )
    image = next((DEMO / "images").glob("*.jpg"))
    messages: list[Message] = [{"role": "user", "content": [{"type": "text", "text": "hi"}, image_part(image)]}]
    completion = model.complete(messages, max_tokens=100)
    body = seen[0]["body"]
    assert body["models"] == [
        "dots-studio/dots-3-note-preview:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    ]
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "reasoning" not in body  # no effort configured on this instance
    assert seen[0]["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    assert completion.input_tokens == 11 and completion.output_tokens == 7


def test_reasoning_off_and_paid_answer_rejected() -> None:
    seen: list[dict[str, Any]] = []
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://openrouter.test/api/v1",
        model="a/b:free",
        api_key=FAKE_KEY,
        reasoning_effort="off",
        transport=_transport([_ok("{}", model="a/b-paid")], seen),
    )
    with pytest.raises(PolicyViolationError):
        model.complete([{"role": "user", "content": "x"}], max_tokens=10)
    assert seen[0]["body"]["reasoning"] == {"enabled": False}
    assert model.cache_salt == "reasoning=off"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "3"}),
            RetryableLLMError,
        ),
        (httpx.Response(503, json={"error": {"message": "overloaded"}}), RetryableLLMError),
        (httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}}), RetryableLLMError),
        (httpx.Response(403, json={"error": {"message": "only agentic harnesses"}}), LLMError),
        (httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}), LLMError),
    ],
)
def test_error_classification(response: httpx.Response, error: type[Exception]) -> None:
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://x.test/v1",
        model="a:free",
        api_key=FAKE_KEY,
        transport=_transport([response], []),
    )
    with pytest.raises(error):
        model.complete([{"role": "user", "content": "x"}], max_tokens=10)


class Scripted:
    label = "openrouter/test:free"
    is_local = False

    def __init__(self, outcomes: list[Any]) -> None:
        self.fallback_models: list[str] = []
        self.outcomes = outcomes
        self.calls = 0

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0, json_mode: bool = False
    ) -> Completion:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Completion(text=outcome, model="test:free", input_tokens=5, output_tokens=3)


def test_budget_retries_caches_and_ledgers(tmp_path: Path) -> None:
    ledger = CallLedger(tmp_path / "calls.jsonl", max_calls=10)
    inner = Scripted([RetryableLLMError("rate limited", retry_after=0.01), '{"ok": true}'])
    model = BudgetedModel(
        inner,
        ledger=ledger,
        cache=DiskCache(tmp_path / "cache"),
        throttle=Throttle(0),
        retry_base_seconds=0.01,
        tag="copy",
    )
    msgs: list[Message] = [{"role": "user", "content": "secret prompt text"}]
    assert model.complete(msgs, max_tokens=10).text == '{"ok": true}'
    again = model.complete(msgs, max_tokens=10)
    assert again.cached and inner.calls == 2
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == ["retryable_error", "ok"]
    assert rows[1]["tag"] == "copy" and rows[1]["input_tokens"] == 5
    assert "secret prompt text" not in (tmp_path / "calls.jsonl").read_text()


def test_budget_cap(tmp_path: Path) -> None:
    ledger = CallLedger(tmp_path / "calls.jsonl", max_calls=1)
    model = BudgetedModel(Scripted(['{"a": 1}', '{"b": 2}']), ledger=ledger, cache=None, throttle=None)
    model.complete([{"role": "user", "content": "one"}], max_tokens=5)
    with pytest.raises(BudgetExceededError):
        model.complete([{"role": "user", "content": "two"}], max_tokens=5)


def test_cache_salt_separates_settings() -> None:
    msgs: list[Message] = [{"role": "user", "content": "x"}]
    assert DiskCache.key("m", msgs, 10, 0.0, True) != DiskCache.key("m", msgs, 10, 0.0, True, "reasoning=off")


def test_torn_cache_file_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    key = "ab" + "0" * 62
    (tmp_path / "ab").mkdir()
    (tmp_path / "ab" / f"{key}.json").write_text("")
    assert cache.get(key) is None


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        'Sure! ```json\n{"a": 1}\n```',
        '<think>hmm {"no": 0}</think>{"a": 1,}',
        "Here: {“a”: 1}",
    ],
)
def test_json_replies(text: str) -> None:
    assert parse_json_object(text)["a"] == 1


def test_json_reply_without_object() -> None:
    with pytest.raises(JSONReplyError):
        parse_json_object("no json here")


def test_offline_vision_reads_pixels() -> None:
    from shelfwise.llm.fake import FakeModel

    gold = json.loads((DEMO / "gold.json").read_text())
    model = FakeModel("vision")
    hits = 0
    for g in gold["products"][:12]:
        messages: list[Message] = [
            {"role": "system", "content": "Shelfwise task: vision_attributes"},
            {"role": "user", "content": [image_part(DEMO / "images" / g["image"])]},
        ]
        answer = json.loads(model.complete(messages, max_tokens=10).text)
        hits += answer["count"] == g["count"]
        assert answer["material"] is None and answer["style"] is None
    assert hits >= 10


def test_offline_model_refuses_unknown_tasks() -> None:
    from shelfwise.llm.fake import FakeModel

    with pytest.raises(LLMError):
        FakeModel().complete(
            [{"role": "system", "content": "Shelfwise task: nothing"}, {"role": "user", "content": "x"}], max_tokens=5
        )
