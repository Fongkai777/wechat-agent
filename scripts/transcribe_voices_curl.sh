#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
JOBS_DIR="web_cache/voice_jobs"
JOBS_FILE="$JOBS_DIR/jobs.jsonl"
mkdir -p "$JOBS_DIR"

"$PY" scripts/voice_batch_prepare.py "$@" > "$JOBS_FILE"

input_tokens=0
output_tokens=0
total_tokens=0
usage_seen=0
done_count=0
failed_count=0

json_get() {
  "$PY" - "$1" "$2" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
value = data
for part in sys.argv[2].split("."):
    value = value.get(part, "") if isinstance(value, dict) else ""
print(value if value is not None else "")
PY
}

json_int() {
  local value
  value="$(json_get "$1" "$2")"
  [[ "$value" =~ ^[0-9]+$ ]] && printf '%s\n' "$value" || printf '0\n'
}

while IFS= read -r line; do
  event="$(json_get "$line" event)"
  case "$event" in
    summary)
      printf '%s\n' "$line"
      ;;
    item_error)
      failed_count=$((failed_count + 1))
      printf '%s\n' "$line"
      ;;
    job)
      idx="$(json_get "$line" index)"
      pending="$(json_get "$line" pending)"
      item_time="$(json_get "$line" item.time)"
      item_db="$(json_get "$line" item.db)"
      item_local_id="$(json_get "$line" item.local_id)"
      item_create_time="$(json_get "$line" item.create_time)"
      config_path="$(json_get "$line" curl_config)"
      response_path="$(json_get "$line" response)"
      wav_path="$(json_get "$line" wav)"
      model="$(json_get "$line" model)"
      audio_sha="$(json_get "$line" audio_sha256)"

      printf '{"event":"progress","index":%s,"pending":%s,"time":%s}\n' \
        "$idx" "$pending" "$(printf '%s' "$item_time" | "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"

      if curl --max-time 180 --connect-timeout 20 --retry 1 --config "$config_path"; then
        save_output="$("$PY" scripts/voice_batch_save.py \
          --response "$response_path" \
          --db "$item_db" \
          --local-id "$item_local_id" \
          --create-time "$item_create_time" \
          --model "$model" \
          --audio-sha256 "$audio_sha")"
        done_count=$((done_count + 1))
        usage="$(json_get "$save_output" usage)"
        in_tokens="$(json_int "$save_output" usage.input_tokens)"
        out_tokens="$(json_int "$save_output" usage.output_tokens)"
        tot_tokens="$(json_int "$save_output" usage.total_tokens)"
        if [[ "$in_tokens" -gt 0 || "$out_tokens" -gt 0 || "$tot_tokens" -gt 0 ]]; then
          usage_seen=1
          input_tokens=$((input_tokens + in_tokens))
          output_tokens=$((output_tokens + out_tokens))
          total_tokens=$((total_tokens + tot_tokens))
        fi
        text="$(json_get "$save_output" text)"
        "$PY" - "$idx" "$pending" "$item_time" "$text" "$usage_seen" "$input_tokens" "$output_tokens" "$total_tokens" <<'PY'
import json, sys
usage_seen = sys.argv[5] == "1"
payload = {
    "event": "item_done",
    "index": int(sys.argv[1]),
    "pending": int(sys.argv[2]),
    "time": sys.argv[3],
    "text": sys.argv[4],
}
if usage_seen:
    payload["usage_totals"] = {
        "input_tokens": int(sys.argv[6]),
        "output_tokens": int(sys.argv[7]),
        "total_tokens": int(sys.argv[8]),
    }
print(json.dumps(payload, ensure_ascii=False))
PY
      else
        failed_count=$((failed_count + 1))
        "$PY" - "$idx" "$pending" "$item_time" "$response_path" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[4])
detail = path.read_text(encoding="utf-8", errors="ignore")[:500] if path.exists() else ""
print(json.dumps({
    "event": "item_error",
    "index": int(sys.argv[1]),
    "pending": int(sys.argv[2]),
    "time": sys.argv[3],
    "error": detail or "curl failed",
}, ensure_ascii=False))
PY
        rm -f "$config_path" "$response_path" "$wav_path"
        exit 1
      fi
      rm -f "$config_path" "$response_path" "$wav_path"
      ;;
  esac
done < "$JOBS_FILE"

"$PY" - "$done_count" "$failed_count" "$usage_seen" "$input_tokens" "$output_tokens" "$total_tokens" <<'PY'
import json, sys
payload = {
    "event": "done",
    "transcribed": int(sys.argv[1]),
    "failed": int(sys.argv[2]),
}
if sys.argv[3] == "1":
    payload["usage_totals"] = {
        "input_tokens": int(sys.argv[4]),
        "output_tokens": int(sys.argv[5]),
        "total_tokens": int(sys.argv[6]),
    }
print(json.dumps(payload, ensure_ascii=False))
PY
