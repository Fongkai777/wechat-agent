#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/work/vendor/wechat-suite/wechat-decrypt/find_all_keys_macos.c"
BIN="$ROOT/tools/find_all_keys_macos"

if [[ ! -f "$SRC" ]]; then
  echo "Missing reference source:"
  echo "  $SRC"
  echo
  echo "Clone the reference repo first:"
  echo "  mkdir -p work/vendor"
  echo "  git clone --depth 1 https://github.com/raclen/wechat-suite.git work/vendor/wechat-suite"
  exit 2
fi

mkdir -p "$ROOT/tools"
cc -O2 -o "$BIN" "$SRC" -framework Foundation

cat <<EOF
Built:
  $BIN

Next steps:
  1. Open WeChat and log in to the account whose db_storage you copied here.
  2. Click several chats, Contacts, search, Favorites, and Moments if you need those DB keys.
  3. Run this from a local macOS Terminal, not from a sandboxed agent:

     cd "$ROOT"
     sudo "$BIN"

The scanner writes:
  $ROOT/all_keys.json

Then decrypt:
  cd "$ROOT"
  .venv/bin/python -m wechat_agent decrypt --keys all_keys.json
  .venv/bin/python -m wechat_agent index

If sudo scanning says task_for_pid failed, see docs/KEY_EXTRACTION.md.
EOF
