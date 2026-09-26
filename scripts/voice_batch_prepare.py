from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_agent.voice_transcribe import cached_voice_transcription, decode_silk_to_wav, voice_cache_path
from wechat_agent.web import DEFAULT_DB_STORAGE, AppState, fetch_voice_data, infer_account, iter_voice_items, load_llm_config, parse_since, resolve_llm_api_key
from wechat_agent.web import build_chat_index, find_chat_by_query, iter_chat_voice_items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-storage", type=Path, default=DEFAULT_DB_STORAGE)
    parser.add_argument("--decrypted", type=Path, default=Path("decrypted"))
    parser.add_argument("--cache", type=Path, default=voice_cache_path())
    parser.add_argument("--llm-config", type=Path, default=Path("web_cache/llm_config.json"))
    parser.add_argument("--media-root", type=Path, default=None)
    parser.add_argument("--account", default="")
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--jobs-dir", type=Path, default=Path("web_cache/voice_jobs"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    since_ts, since_label = parse_since(args.since)
    db_storage = args.db_storage.resolve()
    state = AppState(
        db_storage=db_storage,
        decrypted=args.decrypted.resolve(),
        keys=Path("all_keys.json").resolve(),
        media_root=(args.media_root or (db_storage.parent / "msg")).resolve(),
        voice_cache=args.cache.resolve(),
        llm_config=args.llm_config.resolve(),
        qa_store=Path("web_cache/qa_conversations.json").resolve(),
        account=args.account or infer_account(db_storage),
        since_ts=since_ts,
        since_label=since_label,
    )
    config = load_llm_config(state.llm_config)
    profile = config["voice"]
    api_key = resolve_llm_api_key(profile)
    if not api_key:
        print(json.dumps({"event": "error", "error": "missing voice API key"}, ensure_ascii=False), flush=True)
        return 1

    if args.chat_id or args.query:
        state.chats = build_chat_index(state)
        rec = state.chat_by_id(args.chat_id) if args.chat_id else find_chat_by_query(state, args.query)
        if not rec:
            print(json.dumps({"event": "error", "error": "chat not found or ambiguous", "query": args.query}, ensure_ascii=False), flush=True)
            return 1
        all_items = iter_chat_voice_items(state, rec)
        chat_payload = {"id": rec.get("id"), "title": rec.get("title"), "chat": rec.get("chat")}
    else:
        all_items = iter_voice_items(state)
        chat_payload = {}
    pending = all_items if args.force else [
        item
        for item in all_items
        if not cached_voice_transcription(item["db"], item["local_id"], item["create_time"], state.voice_cache)
    ]
    if args.offset > 0:
        pending = pending[args.offset :]
    if args.limit > 0:
        pending = pending[: args.limit]
    skipped = len(all_items) - len(pending) if not args.force else 0
    args.jobs_dir.mkdir(parents=True, exist_ok=True)
    print(
        json.dumps(
            {
                "event": "summary",
                "total": len(all_items),
                "pending": len(pending),
                "skipped": skipped,
                "force": bool(args.force),
                "cache": str(state.voice_cache),
                "chat": chat_payload,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for index, item in enumerate(pending, start=1):
        data = fetch_voice_data(state, item["db"], item["local_id"], item["create_time"])
        if not data:
            print(json.dumps({"event": "item_error", "index": index, "item": item, "error": "voice not found"}, ensure_ascii=False), flush=True)
            continue
        wav = decode_silk_to_wav(data)
        if wav is None:
            print(json.dumps({"event": "item_error", "index": index, "item": item, "error": "decode failed"}, ensure_ascii=False), flush=True)
            continue
        digest = hashlib.sha256(data).hexdigest()
        stem = f"{index:05d}-{item['create_time']}-{item['local_id']}"
        wav_path = (args.jobs_dir / f"{stem}.wav").resolve()
        config_path = (args.jobs_dir / f"{stem}.curl.conf").resolve()
        response_path = (args.jobs_dir / f"{stem}.response.json").resolve()
        wav_path.write_bytes(wav)
        config_path.write_text(
            "\n".join(
                [
                    f"url = {json.dumps(profile['base_url'] + '/audio/transcriptions')}",
                    "request = POST",
                    "silent",
                    "show-error",
                    "fail-with-body",
                    f"output = {json.dumps(str(response_path))}",
                    f"header = {json.dumps('Authorization: Bearer ' + api_key)}",
                    f"form = {json.dumps('model=' + profile['model'])}",
                    "form = \"response_format=json\"",
                    f"form = {json.dumps('file=@' + str(wav_path) + ';type=audio/wav')}",
                ]
            ),
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        print(
            json.dumps(
                {
                    "event": "job",
                    "index": index,
                    "pending": len(pending),
                    "item": item,
                    "wav": str(wav_path),
                    "curl_config": str(config_path),
                    "response": str(response_path),
                    "model": profile["model"],
                    "audio_sha256": digest,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
