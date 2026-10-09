#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' \
    'Usage: ./run.sh' \
    'Restart WeChat Agent in the background on http://127.0.0.1:8787/' \
    'Running tasks will be interrupted. Saved data is preserved.' \
    'Logs: logs/web.agent.log'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo 'Usage: ./run.sh (no arguments)' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo 'Missing .venv/bin/python. Complete the project setup first.' >&2
  exit 1
fi
for tool in lsof ps curl; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done

export WECHAT_AGENT_HOST=127.0.0.1
export WECHAT_AGENT_PORT=8787
url='http://127.0.0.1:8787'
pids="$(lsof -tiTCP:8787 -sTCP:LISTEN || true)"

# Never stop an unrelated application that happens to occupy this port.
for pid in $pids; do
  command_line="$(ps -p "$pid" -o command=)"
  case "$command_line" in
    *' -m wechat_agent.web '*) ;;
    *) echo "Port 8787 belongs to another application (PID $pid); leaving it running." >&2; exit 1 ;;
  esac
done
if [[ -n "$pids" ]]; then
  echo 'Stopping WeChat Agent. Any running tasks will be interrupted.'
  for pid in $pids; do kill -TERM "$pid"; done
  for ((i=0; i<50; i++)); do
    [[ -z "$(lsof -tiTCP:8787 -sTCP:LISTEN || true)" ]] && break
    sleep 0.2
  done
fi
if [[ -n "$(lsof -tiTCP:8787 -sTCP:LISTEN || true)" ]]; then
  echo 'Port 8787 is still occupied; no additional server was started.' >&2
  exit 1
fi

mkdir -p logs
pid="$(.venv/bin/python - <<'PY'
import subprocess
with open('logs/web.agent.log', 'ab') as log:
    process = subprocess.Popen(
        ['bash', 'scripts/start_web.sh'], stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
print(process.pid)
PY
)"
echo "Starting WeChat Agent (PID $pid)..."
for ((i=0; i<45; i++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo 'Startup failed. Check logs/web.agent.log.' >&2
    exit 1
  fi
  if curl --noproxy '*' --fail --silent --output /dev/null --max-time 2 "$url/api/status"; then
    echo "Ready: $url/"
    echo 'Running in the background. Logs: logs/web.agent.log'
    exit 0
  fi
  sleep 1
done
echo 'Startup is taking longer than expected. Check logs/web.agent.log before retrying.' >&2
exit 1
