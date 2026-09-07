from __future__ import annotations

import argparse
import json
from pathlib import Path

from wechat_agent.voice_transcribe import load_voice_cache, save_voice_cache, voice_cache_key, voice_cache_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=voice_cache_path())
    parser.add_argument("--db", required=True)
    parser.add_argument("--local-id", type=int, required=True)
    parser.add_argument("--create-time", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio-sha256", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = json.loads(args.response.read_text(encoding="utf-8"))
    text = str(payload.get("text") or "").strip()
    if not text:
        text = "（无可识别文字）"
        payload["empty_transcription"] = True
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    item = {
        "text": text,
        "language": payload.get("language") or "unknown",
        "backend": "openai-compatible-audio-curl",
        "model": args.model,
        "create_time": int(args.create_time),
        "source_db": args.db,
        "local_id": int(args.local_id),
        "audio_sha256": args.audio_sha256,
        "usage": usage,
        "empty_transcription": bool(payload.get("empty_transcription")),
    }
    cache = load_voice_cache(args.cache)
    cache[voice_cache_key(args.db, args.local_id, args.create_time)] = item
    save_voice_cache(cache, args.cache)
    print(json.dumps({"event": "item_done", "text": text, "usage": usage}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
