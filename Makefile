.PHONY: setup extract enrich load explain serve web test api-types e2e-setup e2e test-web demo

# Usage examples:
#   make extract REPO=repos/jsbsim
#   make extract REPO=repos/ADL EXTRACTOR=ada ARGS="--parser treesitter"
#   make explain GRAPH=data/jsbsim.graph.json
REPO ?=
GRAPH ?=
EXTRACTOR ?= cpp

setup:
	uv sync
	cd web && pnpm install

extract:
	@test -n "$(REPO)" || { echo "usage: make extract REPO=repos/<name>"; exit 1; }
	uv run python -m extractor.$(EXTRACTOR) $(REPO) --out data/$(notdir $(REPO)).graph.json $(ARGS)

enrich:
	@test -n "$(REPO)" || { echo "usage: make enrich REPO=repos/<name>"; exit 1; }
	uv run python -m extractor.common.enrich data/$(notdir $(REPO)).graph.json

load:
	@test -n "$(REPO)" || { echo "usage: make load REPO=repos/<name>"; exit 1; }
	uv run python -m server.store load data/$(notdir $(REPO)).graph.json

explain:
	@test -n "$(REPO)" || { echo "usage: make explain REPO=repos/<name> [ARGS=\"--max 30\"]"; exit 1; }
	uv run python -m extractor.common.explain $(notdir $(REPO)) $(ARGS)

serve:
	uv run uvicorn server.main:app --reload --port 8000

web:
	cd web && pnpm dev

test:
	uv run pytest

# Regenerate web/src/api-types.ts from the server's OpenAPI schema (run after changing a route).
api-types:
	cd web && pnpm gen:api

# One-time: download the browser Playwright drives.
e2e-setup:
	cd web && pnpm exec playwright install chromium

# Browser test of the web app. Needs data/tinyxml2.db (make extract/enrich/load REPO=repos/tinyxml2).
e2e:
	cd web && pnpm exec playwright test

# Unit tests for the web app's tree logic (no browser needed).
test-web:
	cd web && pnpm test

# The whole pipeline, then both servers: make demo REPO=jsbsim   (add ARGS="--yes" to pay for explanations without asking)
demo:
	@test -n "$(REPO)" || { echo "usage: make demo REPO=jsbsim"; exit 1; }
	uv run python -m scripts.demo $(notdir $(REPO)) $(ARGS)
