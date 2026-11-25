.PHONY: demo setup test run verify web-data web-build web-dev record

# One command: install, replay the committed recordings offline, build and serve the page.
demo: setup web-data web-build
	pnpm --dir web preview --open

setup:
	uv sync --locked
	pnpm --dir web install --frozen-lockfile

test: web-data
	uv run pytest -q
	pnpm --dir web test

# Offline replay of the committed recordings (no network, no API key). --mode replay is pinned,
# so SRG_LLM_MODE in the environment cannot turn these into live calls.
run:
	uv run srg run --mode replay --out runs/latest.json

# Contract validation, internal consistency, and provenance: every LLM call in the artifact must
# rebuild to its request hash and replay to the same decision from the committed recordings.
verify: run
	uv run srg validate runs/latest.json
	uv run srg verify runs/latest.json --recordings recordings

web-data:
	uv run srg run --mode replay --out web/public/data/run.json --json

web-build:
	pnpm --dir web build

web-dev: web-data
	pnpm --dir web dev

# Optional live recording; never used by demo, run or verify.
record:
	uv run python -m srg.record_session
