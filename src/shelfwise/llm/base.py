"""Chat-model interface shared by the OpenRouter, OpenAI, OpenAI-compatible and offline providers.

Messages follow the OpenAI chat format; a user message may carry images as content parts
(``{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}``), which is how the vision step
sends product photos.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NotRequired, Protocol, TypedDict


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]
    name: NotRequired[str]


def image_part(path: Path) -> dict[str, Any]:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def text_of(message: Message) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return "\n".join(str(p.get("text", "")) for p in content if p.get("type") == "text")


def images_of(message: Message) -> list[bytes]:
    content = message["content"]
    if isinstance(content, str):
        return []
    out = []
    for part in content:
        if part.get("type") == "image_url":
            url = str(part["image_url"]["url"])
            if url.startswith("data:") and "," in url:
                out.append(base64.b64decode(url.split(",", 1)[1]))
    return out


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    """The model that actually answered (OpenRouter may route to a fallback)."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    latency_s: float = 0.0
    extra: dict[str, object] = field(default_factory=dict)


class LLMError(RuntimeError):
    """The model request failed and should not be retried."""


class RetryableLLMError(LLMError):
    """Rate limit, overload, timeout or an empty answer: worth retrying with backoff."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PolicyViolationError(LLMError):
    """A guard refused the request, e.g. a non-free OpenRouter model while the free-only guard is on."""


class BudgetExceededError(LLMError):
    pass


class ChatModel(Protocol):
    @property
    def label(self) -> str:
        """Provider and model, e.g. "openrouter/google/gemma-4-31b-it:free"."""
        ...

    @property
    def is_local(self) -> bool:
        """True when no request leaves the process (the offline model)."""
        ...

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0, json_mode: bool = False
    ) -> Completion: ...
