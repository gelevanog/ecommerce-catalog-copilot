.DEFAULT_GOAL := help
.PHONY: help install seed serve web mcp test lint format typecheck web-lint web-build eval-offline eval-real summary docker-up clean

FREE = SHELFWISE_LLM_PROVIDER=openrouter

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Python (uv) and dashboard (npm) dependencies
	uv sync
	cd web && npm ci --no-audit --no-fund

seed:  ## Regenerate the synthetic catalog in data/demo (deterministic)
	uv run shelfwise seed

serve:  ## API on :8000 (mock stores under /mock), demo seeded on first start
	uv run shelfwise serve --reload

web:  ## Dashboard on :3000
	cd web && npm run dev

mcp:  ## MCP server on stdio
	uv run shelfwise mcp

test:  ## Python tests (offline, no keys)
	uv run pytest

lint:  ## ruff check + format check
	uv run ruff check src tests
	uv run ruff format --check src tests

format:  ## ruff format
	uv run ruff format src tests

typecheck:  ## mypy --strict
	uv run mypy

web-lint:  ## ESLint + tsc
	cd web && npm run lint && npm run typecheck

web-build:  ## Production build of the dashboard
	cd web && npm run build

eval-offline:  ## Evaluation with the offline model (seconds)
	uv run shelfwise eval run --name offline

eval-real:  ## Evaluation with free OpenRouter models (needs OPENROUTER_API_KEY; about an hour)
	$(FREE) uv run shelfwise eval run --name main --workers 3
	$(FREE) uv run shelfwise eval vision --model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free --name vision_alt
	$(FREE) uv run shelfwise eval judge --sample 20 --model nvidia/nemotron-3-ultra-550b-a55b:free
	$(FREE) uv run shelfwise eval backtranslate --sample 10
	uv run shelfwise eval summary

summary:  ## results/summary.json from the result files and the ledger
	uv run shelfwise eval summary

docker-up:  ## API + dashboard in Docker
	docker compose up --build

clean:  ## Remove caches and the local database
	rm -rf .cache .mypy_cache .ruff_cache .pytest_cache web/.next
