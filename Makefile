.PHONY: setup test check demo api ui run

setup:
	uv sync --extra dev
	npm --prefix ui ci

test:
	uv run pytest -q
	npm --prefix ui test

check:
	uv run ruff check .
	uv run mypy src
	npm --prefix ui run lint
	cd ui && npm exec tsc -- --noEmit
	npm --prefix ui run build

demo:
	uv run porter-forces run examples/global-bank-ai-adoption.demo.json

api:
	uv run porter-forces serve

ui:
	npm --prefix ui run dev -- --host 127.0.0.1 --port 3000

run:
	./scripts/setup_and_run.sh
