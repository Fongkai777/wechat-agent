from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wechat_agent.voice_transcribe import cached_voice_transcription, voice_cache_path
from wechat_agent.web import (
    DEFAULT_DB_STORAGE,
    AppState,
    fetch_voice_data,
    infer_account,
    iter_voice_items,
    merge_usage_totals,
    parse_since,
    transcribe_voice_data_with_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcribe WeChat voice messages into the web cache")
    parser.add_argument("--db-storage", type=Path, default=DEFAULT_DB_STORAGE)
    parser.add_argument("--decrypted", type=Path, default=Path("decrypted"))
    parser.add_argument("--cache", type=Path, default=voice_cache_path())
    parser.add_argument("--llm-config", type=Path, default=Path("web_cache/llm_config.json"))
    parser.add_argument("--media-root", type=Path, default=None)
    parser.add_argument("--account", default="")
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
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

    all_items = iter_voice_items(state)
    pending = all_items if args.force else [
        item
        for item in all_items
        if not cached_voice_transcription(item["db"], item["local_id"], item["create_time"], state.voice_cache)
    ]
    if args.limit > 0:
        pending = pending[: args.limit]
    skipped = len(all_items) - len(pending) if not args.force else 0
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    usage_seen = False
    done = 0
    failed = 0

    print(
        json.dumps(
            {
                "event": "summary",
                "total": len(all_items),
                "pending": len(pending),
                "skipped": skipped,
                "force": bool(args.force),
                "cache": str(state.voice_cache),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for index, item in enumerate(pending, start=1):
        print(
            json.dumps(
                {
                    "event": "progress",
                    "index": index,
                    "pending": len(pending),
                    "time": item["time"],
                    "db": item["db"],
                    "local_id": item["local_id"],
                    "size": item["size"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        data = fetch_voice_data(state, item["db"], item["local_id"], item["create_time"])
        if not data:
            failed += 1
            print(json.dumps({"event": "item_error", "item": item, "error": "voice not found"}, ensure_ascii=False), flush=True)
            continue
        try:
            result = transcribe_voice_data_with_config(
                state,
                data,
                item["db"],
                item["local_id"],
                item["create_time"],
                force=args.force,
            )
        except RuntimeError as exc:
            failed += 1
            print(json.dumps({"event": "item_error", "item": item, "error": str(exc)}, ensure_ascii=False), flush=True)
            return 1
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage_seen = merge_usage_totals(usage_totals, usage) or usage_seen
        done += 1
        print(
            json.dumps(
                {
                    "event": "item_done",
                    "index": index,
                    "pending": len(pending),
                    "time": item["time"],
                    "text": str(result.get("text") or ""),
                    "usage": usage,
                    "usage_totals": usage_totals if usage_seen else {},
                    "done": done,
                    "failed": failed,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "event": "done",
                "total": len(all_items),
                "transcribed": done,
                "skipped": skipped,
                "failed": failed,
                "usage_totals": usage_totals if usage_seen else {},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
