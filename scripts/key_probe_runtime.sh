#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LLDB_PYTHON_PATH="$(lldb -P)"
PYTHON_BIN="/Library/Developer/CommandLineTools/usr/bin/python3"

sudo env PYTHONPATH="$LLDB_PYTHON_PATH" "$PYTHON_BIN" \
  scripts/lldb_probe_wechat_runtime.py \
  --out "$PWD/runtime_probe.txt"
