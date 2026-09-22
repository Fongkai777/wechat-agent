"""Run the existing application against fictional, isolated SQLite snapshots."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from http.server import ThreadingHTTPServer

from . import web

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "chats.json"
MARKER = ".wechat-agent-synthetic-demo"


def fixture():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def demo_state(root):
    root = Path(root).resolve()
    if not (root / MARKER).is_file():
        raise ValueError("Not a demo directory. Run init first; private data is never used as fallback.")
    state = web.AppState(
        db_storage=root / "empty-source", decrypted=root / "decrypted",
        keys=root / "unused-keys.json", media_root=root / "media",
        voice_cache=root / "voice.json", llm_config=root / "models.json",
        qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl",
        qa_search_db=root / "search.db", account="demo_me", sync_interval=0, demo_mode=True,
    )
    state.chats = web.build_chat_index(state)
    state.last_synced_at = "2026-09-22T18:00:00"
    return state


def initialize(root):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()) and not (root / MARKER).is_file():
        raise ValueError("Refusing a nonempty non-demo directory.")
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("Synthetic data only\n", encoding="utf-8")
    data = fixture()
    for directory in ("message", "contact", "session"):
        (root / "decrypted" / directory).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "decrypted/contact/contact.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS contact(username TEXT PRIMARY KEY, nick_name TEXT)")
        conn.executemany("INSERT OR IGNORE INTO contact VALUES (?, ?)", data["contacts"].items())
    with sqlite3.connect(root / "decrypted/session/session.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS SessionTable(username TEXT PRIMARY KEY)")
        chats = sorted({row[1] for row in data["messages"] + data["increment"]})
        conn.executemany("INSERT OR IGNORE INTO SessionTable VALUES (?)", [(chat,) for chat in chats])
    with sqlite3.connect(root / "decrypted/message/message_0.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS Name2Id(user_name TEXT UNIQUE)")
        conn.executemany("INSERT OR IGNORE INTO Name2Id VALUES (?)", [(name,) for name in data["contacts"]])
        for chat in chats:
            table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)')
            conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}" ON "{table}"(create_time)')
    config = root / "models.json"
    if not config.exists():
        profiles = json.loads(json.dumps(web.DEFAULT_LLM_CONFIG))
        for profile in profiles.values():
            profile["api_key_env"] = "WECHAT_DEMO_API_KEY"
        profiles["embedding"]["enabled"] = False
        profiles["rerank"]["enabled"] = False
        config.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        config.chmod(0o600)
    return import_messages(root, data["messages"])


def import_messages(root, messages):
    state = demo_state(root)
    with sqlite3.connect(state.decrypted / "message/message_0.db") as conn:
        names = {name: rowid for rowid, name in conn.execute("SELECT rowid, user_name FROM Name2Id")}
        before = conn.total_changes
        for mid, chat, sender, timestamp, text in messages:
            table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
            conn.execute(f'INSERT OR IGNORE INTO "{table}" VALUES (?, ?, 1, ?, ?, ?)',
                         (mid, mid, names[sender], int(datetime.fromisoformat(timestamp).timestamp()), text))
        return conn.total_changes - before


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "append", "index", "serve"])
    parser.add_argument("--root", type=Path, default=Path(".demo"))
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if args.command == "init":
        print(f"Imported {initialize(args.root)} fictional messages into {args.root}.")
        return 0
    if args.command == "append":
        print(f"Imported {import_messages(args.root, fixture()['increment'])} new fictional messages.")
        return 0
    state = demo_state(args.root)
    if args.command == "index":
        result = web.update_qa_search_db_incremental(state)
        print(json.dumps({key: result.get(key) for key in ("update_mode", "message_count", "inserted", "updated_voices")}, ensure_ascii=False))
        return 0
    handler = web.make_handler(state)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as exc:
        raise SystemExit(f"Cannot bind port {args.port}; no alternate port was opened. Stop an existing server first. {exc}")
    handler.goal_scheduler.start()
    handler.rag_scheduler.start()
    print(f"SYNTHETIC DEMO: http://127.0.0.1:{args.port} | {len(state.chats)} fictional chats", flush=True)
    print("No private source/config is read. Cloud features require demo model configuration.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        handler.goal_scheduler.stop()
        handler.rag_scheduler.stop()
        state.sync_stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
