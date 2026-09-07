#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PIP_CACHE_DIR="${PIP_CACHE_DIR:-/private/tmp/wechat-agent-pip-cache}" \
  .venv/bin/python -m pip install 'pilk>=0.2' 'openai-whisper>=20231117'
