# syntax=docker/dockerfile:1

# ---- build: resolve dependencies with uv into a self-contained virtualenv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime: the API (with the mock stores mounted under /mock), the CLI and the MCP server ----
FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app configs ./configs
COPY --chown=app:app data/demo ./data/demo
COPY --chown=app:app results ./results
ENV SHELFWISE_DATABASE=/home/app/data/shelfwise.db \
    SHELFWISE_WORKSPACE_DIR=/home/app/data/workspace \
    SHELFWISE_LLM_CACHE_DIR=/home/app/data/llm-cache \
    SHELFWISE_LLM_LEDGER=/home/app/data/calls.jsonl
RUN mkdir -p /home/app/data && chown -R app:app /home/app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "shelfwise.api.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
