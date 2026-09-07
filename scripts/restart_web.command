#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Restarting WeChat Agent web server on http://127.0.0.1:8787"

pids="$(lsof -tiTCP:8787 -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
  echo "Stopping existing server: $pids"
  kill $pids || true
  sleep 1
fi

echo "Starting server..."
exec bash scripts/start_web.sh
