"""The offline model: a rule-based stand-in for every model task, so tests, CI and the zero-key demo run without
any API key or network.

- vision: reads the pixels (background removal, nearest palette color, stripe/check detection from color
  transitions, connected components for the item count). It cannot see materials or style and says so (null).
- copy: fills fixed house-style templates from the facts list only.
- translation: the same templates in German, Spanish and French, with a small dictionary for the slot values;
  sentences that are not from the templates stay in English and fail the language check, as they should.
- judge and back-translation: the deterministic claims check and the reverse templates.

It is deliberately simple: the README's evaluation runs real free models, and reports this model as the baseline.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from shelfwise.llm.base import Completion, LLMError, Message, images_of, text_of

_TASK = re.compile(r"Shelfwise task: (\w+)")
_INPUT = re.compile(r"<input>\s*(.*?)\s*</input>", re.DOTALL)


class FakeModel:
    def __init__(self, role: str = "text") -> None:
        self.role = role

    @property
    def label(self) -> str:
        return f"fake/{'fake-pixels' if self.role == 'vision' else 'fake-rules'}"

    @property
    def is_local(self) -> bool:
        return True

    def complete(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0, json_mode: bool = False
    ) -> Completion:
        system = text_of(messages[0]) if messages else ""
        match = _TASK.search(system)
        task = match.group(1) if match else ""
        user = messages[-1]
        if task == "vision_attributes":
            images = images_of(user)
            if not images:
                raise LLMError("fake vision: no image in the request")
            answer: dict[str, Any] = see(images[0])
        else:
            payload = _payload(messages)
            from shelfwise.llm import fake_text

            handler = fake_text.HANDLERS.get(task)
            if handler is None:
                raise LLMError(f"fake model: unknown task {task!r}")
            answer = handler(payload, system)
        text = json.dumps(answer, ensure_ascii=False)
        return Completion(text=text, model=self.label.split("/", 1)[1], input_tokens=0, output_tokens=0)


def _payload(messages: Sequence[Message]) -> dict[str, Any]:
    for message in messages:
        if message["role"] != "user":
            continue
        match = _INPUT.search(text_of(message))
        if match:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
    return {}


# ---------------------------------------------------------------- "vision" from pixels


def see(image: bytes) -> dict[str, Any]:
    from PIL import Image

    from shelfwise.vocab import nearest_color

    img = Image.open(io.BytesIO(image)).convert("RGB").resize((120, 120))
    px = img.load()
    assert px is not None
    w, h = img.size

    def bg(y: int) -> tuple[int, int, int]:
        left, right = px[1, y], px[w - 2, y]
        assert isinstance(left, tuple) and isinstance(right, tuple)
        return tuple((a + b) // 2 for a, b in zip(left, right, strict=True))  # type: ignore[return-value]

    mask = [[False] * w for _ in range(h)]
    labels: list[list[str | None]] = [[None] * w for _ in range(h)]
    counts: Counter[str] = Counter()
    for y in range(h):
        b = bg(y)
        for x in range(w):
            p = px[x, y]
            assert isinstance(p, tuple)
            d = sum(abs(p[i] - b[i]) for i in range(3))
            sat = max(p) - min(p)
            # shadows are soft, low-saturation darkenings of the background: skip them
            if d > 70 or (d > 40 and sat > 40):
                mask[y][x] = True
                name = nearest_color(p[:3])
                labels[y][x] = name
                counts[name] += 1
    total = sum(counts.values())
    if total < w * h * 0.02:
        return {"product": "product", "color": "white", "secondary_color": None, "pattern": "solid", "material": None,
                "style": None, "count": 1, "visible_text": "", "confidence": {"color": 0.3, "pattern": 0.3, "count": 0.3}}
    ranked = [c for c, _ in counts.most_common()]
    primary = ranked[0]
    secondary = next((c for c in ranked[1:] if counts[c] >= 0.15 * total), None)
    pattern = "solid"
    if secondary:
        def transitions(rows: bool) -> int:
            n = 0
            outer, inner = (h, w) if rows else (w, h)
            for i in range(outer):
                prev = None
                for j in range(inner):
                    lab = labels[i][j] if rows else labels[j][i]
                    if lab in (primary, secondary):
                        if prev is not None and lab != prev:
                            n += 1
                        prev = lab
            return n

        along_rows, along_cols = transitions(True), transitions(False)
        if along_cols > 2.5 * max(1, along_rows):
            pattern = "striped"
        elif along_rows > 2.5 * max(1, along_cols):
            pattern = "striped"
        elif along_rows > 120 and along_cols > 120:
            pattern = "checked" if abs(along_rows - along_cols) < 0.25 * max(along_rows, along_cols) else "polka dot"
        else:
            pattern = "geometric"
    return {
        "product": "product",
        "color": primary,
        "secondary_color": secondary,
        "pattern": pattern,
        "material": None,
        "style": None,
        "count": _components(mask, w, h),
        "visible_text": "",
        "confidence": {"color": 0.8, "pattern": 0.5, "count": 0.6, "material": 0.0, "style": 0.0},
    }


def _components(mask: list[list[bool]], w: int, h: int) -> int:
    seen = [[False] * w for _ in range(h)]
    sizes = []
    for y in range(h):
        for x in range(w):
            if not mask[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            size = 0
            while stack:
                cx, cy = stack.pop()
                size += 1
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            sizes.append(size)
    big = [s for s in sizes if s >= 0.25 * max(sizes)] if sizes else []
    return max(1, len(big))
