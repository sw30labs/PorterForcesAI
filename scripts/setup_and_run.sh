#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js/npm is required for the local dashboard." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example; review the local oMLX model and key."
fi

uv sync --extra dev
npm --prefix ui install

API_PID=""
UI_PID=""

cleanup() {
  if [[ -n "${UI_PID}" ]]; then kill "${UI_PID}" 2>/dev/null || true; fi
  if [[ -n "${API_PID}" ]]; then kill "${API_PID}" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

uv run porter-forces serve &
API_PID=$!
npm --prefix ui run dev -- --host 127.0.0.1 --port 3000 &
UI_PID=$!

echo "PorterForcesAI API: http://127.0.0.1:8765/api/docs"
echo "PorterForcesAI UI:  http://127.0.0.1:3000"
echo "Press Ctrl-C to stop both local services."

wait -n "${API_PID}" "${UI_PID}"
