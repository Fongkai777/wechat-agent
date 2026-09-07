#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="/Library/Developer/CommandLineTools/usr/bin/python3"
LLDB_PYTHON_PATH="$(lldb -P)"

sudo env PYTHONPATH="$LLDB_PYTHON_PATH" "$PYTHON_BIN" \
  scripts/wechat_mcp_keygen_compat.py \
  --db-storage "$PWD/db_storage" \
  --out "$PWD/all_keys.json"
