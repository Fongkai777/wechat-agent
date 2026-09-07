#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LLDB_PYTHON_PATH="$(lldb -P)"
PYTHON_BIN="/Library/Developer/CommandLineTools/usr/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Command Line Tools Python at $PYTHON_BIN" >&2
  exit 1
fi

echo "This attaches to the currently running WeChat process and reads the runtime DB key object."
echo "Keep WeChat open and logged in. The full key will be written only to all_keys.json."
echo

sudo env PYTHONPATH="$LLDB_PYTHON_PATH" "$PYTHON_BIN" \
  scripts/lldb_extract_runtime_key.py \
  --db-storage "$PWD/db_storage" \
  --out "$PWD/all_keys.json"
