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
npm --prefix ui ci

eval "$(
  uv run python -c '
from porter_forces_ai.settings import Settings
settings = Settings()
print(f"API_HOST={settings.api_host}")
print(f"API_PORT={settings.api_port}")
print(f"DATABASE_PATH={settings.database_path.expanduser().resolve()}")
'
)"
export PFA_UI_API_PROXY="${PFA_UI_API_PROXY:-http://${API_HOST}:${API_PORT}}"

_unique_pids() {
  if [[ $# -eq 0 ]]; then
    return 0
  fi
  printf '%s\n' "$@" | awk 'NF && !seen[$0]++'
}

_print_pids() {
  local pids="$1"
  # shellcheck disable=SC2086
  ps -o pid,ppid,command -p ${pids} >&2 || true
}

_lock_holders() {
  local lock_path="${DATABASE_PATH}.writer.lock"
  if [[ ! -e "${lock_path}" ]]; then
    return 0
  fi
  lsof -t "${lock_path}" 2>/dev/null || true
}

_port_holders() {
  lsof -nP -iTCP:"${API_PORT}" -sTCP:LISTEN -t 2>/dev/null || true
}

_preflight_exclusive_writer() {
  local lock_pids port_pids
  lock_pids="$(_unique_pids $(_lock_holders))"
  if [[ -n "${lock_pids}" ]]; then
    echo "FAILED: another process owns the AnalysisService writer lease for ${DATABASE_PATH}" >&2
    echo "Owning process:" >&2
    _print_pids "${lock_pids}"
    echo "Stop it, then re-run this script:" >&2
    echo "  kill ${lock_pids}" >&2
    echo "Do not delete ${DATABASE_PATH}.writer.lock; it is not a busy-state signal." >&2
    exit 1
  fi

  port_pids="$(_unique_pids $(_port_holders))"
  if [[ -n "${port_pids}" ]]; then
    echo "FAILED: ${API_HOST}:${API_PORT} is already in use" >&2
    echo "Listening process:" >&2
    _print_pids "${port_pids}"
    echo "Stop it, then re-run this script:" >&2
    echo "  kill ${port_pids}" >&2
    exit 1
  fi
}

_kill_tree() {
  local pid="${1:-}"
  local child
  [[ -z "${pid}" ]] && return 0
  for child in $(pgrep -P "${pid}" 2>/dev/null || true); do
    _kill_tree "${child}"
  done
  kill "${pid}" 2>/dev/null || true
}

_api_healthy() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf "http://${API_HOST}:${API_PORT}/api/health" >/dev/null
    return
  fi
  uv run python -c "import urllib.request; urllib.request.urlopen('http://${API_HOST}:${API_PORT}/api/health', timeout=1).read()" >/dev/null
}

_wait_for_api() {
  local i
  for i in $(seq 1 50); do
    if ! kill -0 "${API_PID}" 2>/dev/null; then
      echo "FAILED: porter-forces serve exited before becoming healthy." >&2
      wait "${API_PID}" || true
      exit 1
    fi
    if _api_healthy; then
      return 0
    fi
    sleep 0.1
  done
  echo "FAILED: porter-forces serve did not become healthy on http://${API_HOST}:${API_PORT}/api/health" >&2
  exit 1
}

_preflight_exclusive_writer

API_PID=""
UI_PID=""

cleanup() {
  if [[ -n "${UI_PID}" ]]; then _kill_tree "${UI_PID}"; fi
  if [[ -n "${API_PID}" ]]; then _kill_tree "${API_PID}"; fi
}
trap cleanup EXIT INT TERM HUP

uv run porter-forces serve &
API_PID=$!
_wait_for_api

npm --prefix ui run dev -- --host 127.0.0.1 --port 3000 &
UI_PID=$!

echo "PorterForcesAI API: http://${API_HOST}:${API_PORT}/api/docs"
echo "PorterForcesAI UI:  http://127.0.0.1:3000"
echo "Press Ctrl-C to stop both local services."

while kill -0 "${API_PID}" 2>/dev/null && kill -0 "${UI_PID}" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "${API_PID}" 2>/dev/null; then
  echo "FAILED: porter-forces serve exited." >&2
  wait "${API_PID}" || true
  exit 1
fi
echo "FAILED: UI dev server exited." >&2
wait "${UI_PID}" || true
exit 1
