#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LLDB_PYTHON_PATH="$(lldb -P)"
PYTHON_BIN="/Library/Developer/CommandLineTools/usr/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Command Line Tools Python at $PYTHON_BIN" >&2
  exit 1
fi

echo "This will wait for WeChat to launch."
echo "After it starts: quit WeChat with Cmd+Q, relaunch WeChat, then click the chats/contacts you need."
echo

sudo env PYTHONPATH="$LLDB_PYTHON_PATH" "$PYTHON_BIN" \
  scripts/lldb_find_wechat_keys.py \
  --db-storage "$PWD/db_storage" \
  --out "$PWD/all_keys.json"
