#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${WECHAT_AGENT_HOST:-127.0.0.1}"
PORT="${WECHAT_AGENT_PORT:-8787}"
SINCE="${WECHAT_AGENT_SINCE:-2023-01-01}"

exec .venv/bin/python -m wechat_agent.web \
  --host "$HOST" \
  --port "$PORT" \
  --since "$SINCE" \
  "$@"
