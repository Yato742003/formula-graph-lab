SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; else command -v python3 || command -v python; fi)

.PHONY: dev-sandbox help setup db-up db-down db-migrate api web verify build build-wasm test-integration test-sandbox benchmark replay

help: ## Show available cross-platform targets (macOS / Linux / Git Bash)
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install Node + Python dependencies and apply local Cloudflare D1 migrations
	npm install
	uv sync --project services/graph-api --frozen --extra dev
	npx wrangler d1 migrations apply site-creator-d1 --local

db-up: ## Start local Neo4j container via Docker Compose
	docker compose up -d neo4j

db-down: ## Stop Docker Compose services
	docker compose down

db-migrate: ## Apply Drizzle SQLite migrations to local Cloudflare D1 (Miniflare)
	npx wrangler d1 migrations apply site-creator-d1 --local

api: ## Run FastAPI Graph API locally on 127.0.0.1:8000
	$(PYTHON) -m uvicorn app.main:app --app-dir services/graph-api --reload --host 127.0.0.1 --port 8000

API_PORT ?= 8000
dev-sandbox: db-up ## Build a pinned local worker and run the research API
	$(PYTHON) scripts/dev_sandbox.py --port $(API_PORT)

web: ## Run Vinext (Next.js App Router on Vite + Cloudflare Workers) dev server
	npm run dev

verify: ## Run full offline verification gate (Ruff, Pytest, Oxlint, TypeScript, Vitest, Rust proptest)
	cd services/graph-api && $(abspath $(PYTHON)) -m ruff check app tests
	cd services/graph-api && $(abspath $(PYTHON)) -m pytest -m "not integration and not live and not sandbox" -q
	npm run lint
	npx tsc --noEmit --incremental false
	npm test
	cargo test --manifest-path crates/formula-layout-wasm/Cargo.toml --quiet

build: ## Build production Cloudflare Worker / Vinext bundle
	npm run build

build-wasm: ## Compile Rust layout engine crate to public/wasm/formula-layout.wasm
	cd crates/formula-layout-wasm && cargo test --quiet && cargo build --target wasm32-unknown-unknown --release
	mkdir -p public/wasm
	cp crates/formula-layout-wasm/target/wasm32-unknown-unknown/release/formula_layout_wasm.wasm public/wasm/formula-layout.wasm

test-integration: ## Run Neo4j integration tests against an isolated tmpfs container
	@set -euo pipefail; \
	RUN_PROJECT="fgl-test-$$(openssl rand -hex 6)"; \
	export TEST_NEO4J_PASSWORD="$$(openssl rand -hex 32)"; \
	trap 'docker compose -p "$$RUN_PROJECT" -f docker-compose.test.yml down >/dev/null 2>&1 || true' EXIT; \
	docker compose -p "$$RUN_PROJECT" -f docker-compose.test.yml up -d --wait --wait-timeout 120; \
	ADDR="$$(docker compose -p "$$RUN_PROJECT" -f docker-compose.test.yml port neo4j 7687)"; \
	export TEST_NEO4J_URI="bolt://$$ADDR"; \
	cd services/graph-api && $(abspath $(PYTHON)) -m pytest -m integration --tb=short

test-sandbox: ## Build pinned Docker sandbox + probe images and run isolation + clean-replay tests
	@set -euo pipefail; \
	TAG="fgl-sandbox-test-$$(openssl rand -hex 6)"; \
	trap 'docker image rm "$${TAG}-probe" "$$TAG" >/dev/null 2>&1 || true' EXIT; \
	docker build -f services/graph-api/Dockerfile.sandbox -t "$$TAG" services/graph-api; \
	export TEST_SANDBOX_IMAGE="$$(docker image inspect --format '{{.Id}}' "$$TAG")"; \
	docker build -f services/graph-api/tests/Dockerfile.sandbox-probe --build-arg "WORKER_IMAGE=$$TAG" -t "$${TAG}-probe" services/graph-api; \
	export TEST_SANDBOX_PROBE_IMAGE="$$(docker image inspect --format '{{.Id}}' "$${TAG}-probe")"; \
	cd services/graph-api && $(abspath $(PYTHON)) -m pytest -m sandbox --tb=short

benchmark: ## Run arXiv corpus + live ephemeral Docker sandbox benchmark -> reports/benchmark-summary.json
	@set -euo pipefail; \
	TAG="fgl-bench-$$(openssl rand -hex 5)"; \
	trap 'docker image rm "$$TAG" >/dev/null 2>&1 || true' EXIT; \
	docker build -q -f services/graph-api/Dockerfile.sandbox -t "$$TAG" services/graph-api >/dev/null; \
	IMG="$$(docker image inspect --format '{{.Id}}' "$$TAG")"; \
	$(PYTHON) scripts/benchmark_evidence.py --sandbox-image "$$IMG" --symbolic-runs 5 --numerical-runs 3

replay: ## Replay an exported bundle offline in a locked-down container (usage: make replay BUNDLE=path/to/bundle.json [EVOLUTION=1])
	@set -euo pipefail; \
	test -n "$(BUNDLE)" || { echo "Usage: make replay BUNDLE=path/to/bundle.json [EVOLUTION=1]"; exit 1; }; \
	ABS_BUNDLE="$$(cd "$$(dirname "$(BUNDLE)")" && pwd)/$$(basename "$(BUNDLE)")"; \
	TAG="fgl-replay-$$(openssl rand -hex 6)"; \
	MOD="$$( [ "$(EVOLUTION)" = "1" ] && echo app.evolution_replay_cli || echo app.replay_bundle_cli )"; \
	trap 'docker image rm "$$TAG" >/dev/null 2>&1 || true' EXIT; \
	docker build -q -f services/graph-api/Dockerfile -t "$$TAG" services/graph-api >/dev/null; \
	docker run --rm --network none --read-only --cap-drop ALL \
		--security-opt no-new-privileges --memory 256m --cpus 1 --pids-limit 64 \
		--user 65534:65534 --mount "type=bind,source=$$ABS_BUNDLE,target=/bundle.json,readonly" \
		"$$TAG" python -m "$$MOD" /bundle.json
