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

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "corpus_v1.json"
MARKER = ".wechat-agent-synthetic-demo"
MANIFEST = 'demo_manifest.json'


def fixture():
    """Fixed historical evaluation corpus; never apply the showcase clock here."""
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def workspace_manifest(root):
    root = Path(root).resolve()
    if not (root / MARKER).is_file():
        raise ValueError('Not a demo directory. Run init first.')
    path = root / MANIFEST
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    # Existing pre-showcase demos keep their dates, IDs and message history.
    return {'dataset':'evaluation', 'corpus':fixture()}


def demo_state(root):
    root = Path(root).resolve()
    if not (root / MARKER).is_file():
        raise ValueError("Not a demo directory. Run init first; private data is never used as fallback.")
    metadata = workspace_manifest(root)
    state = web.AppState(
        db_storage=root / "empty-source", decrypted=root / "decrypted",
        keys=root / "unused-keys.json", media_root=root / "media",
        voice_cache=root / "voice.json", llm_config=root / "models.json",
        qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl",
        qa_search_db=root / "search.db", account=metadata['corpus'].get('account', 'demo_me'), sync_interval=0, demo_mode=True,
    )
    state.chats = web.build_chat_index(state)
    state.last_synced_at = datetime.fromtimestamp((root/'decrypted/message/message_0.db').stat().st_mtime).astimezone().isoformat(timespec='seconds')
    state.demo_source_stamp = _source_stamp(state)
    return state


def _source_stamp(state):
    stat = (state.decrypted/'message/message_0.db').stat()
    return stat.st_mtime_ns, stat.st_size


def refresh_workspace(state):
    """Observe only the marked synthetic workspace, never the real WeChat source."""
    workspace_manifest(state.decrypted.parent)
    stamp = _source_stamp(state)
    if stamp == getattr(state, 'demo_source_stamp', None):
        return False
    state.chats = web.build_chat_index(state)
    state.last_synced_at = datetime.now().astimezone().isoformat(timespec='seconds')
    state.demo_source_stamp = stamp
    state.sync_revision += 1
    return True


def initialize(root, dataset=None, anchor=None):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()) and not (root / MARKER).is_file():
        raise ValueError("Refusing a nonempty non-demo directory.")
    if (root / MARKER).is_file():
        metadata = workspace_manifest(root)
        if dataset is not None and dataset != metadata['dataset']:
            raise ValueError('Dataset is frozen for this directory; use a new demo root.')
        if anchor is not None:
            from .demo_scenario import build_showcase
            if build_showcase(anchor)['anchor'] != metadata['corpus'].get('anchor'):
                raise ValueError('Demo clock is frozen; use a new root to select another date.')
        data = metadata['corpus']
    else:
        dataset = dataset or 'showcase'
        if dataset not in ('showcase', 'evaluation'):
            raise ValueError('Unknown demo dataset')
        if dataset == 'evaluation':
            if anchor is not None:
                raise ValueError('Evaluation dates are fixed')
            data = fixture()
        else:
            from .demo_scenario import build_showcase
            data = build_showcase(anchor)
        metadata = {'dataset':dataset, 'corpus':data}
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("Synthetic data only\n", encoding="utf-8")
    # Persist the rendered corpus so later append/restart never shifts the clock
    # or picks up changed templates halfway through a demonstration.
    if not (root / MANIFEST).exists():
        (root / MANIFEST).write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
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
        config.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        config.chmod(0o600)
    return import_messages(root, data["messages"])


def import_messages(root, messages):
    state = demo_state(root)
    with sqlite3.connect(state.decrypted / "message/message_0.db") as conn:
        return _insert_messages(conn, messages)


def _insert_messages(conn, messages):
    names = {name: rowid for rowid, name in conn.execute("SELECT rowid, user_name FROM Name2Id")}
    before = conn.total_changes
    for mid, chat, sender, timestamp, text in messages:
        table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
        conn.execute(f'INSERT OR IGNORE INTO "{table}" VALUES (?, ?, 1, ?, ?, ?)',
                     (mid, mid, names[sender], int(datetime.fromisoformat(timestamp).timestamp()), text))
    return conn.total_changes - before


def append_phase(root, phase='next'):
    metadata = workspace_manifest(root)
    data = metadata['corpus']
    phases = data.get('phases', [])[1:] or [{'name':'increment', 'messages':data['increment']}]
    if phase != 'next' and phase not in {p['name'] for p in phases}:
        raise ValueError('Unknown phase for this demo')
    state = demo_state(root)
    with sqlite3.connect(state.decrypted/'message/message_0.db') as conn:
        # Stage selection, inserts and receipt are one transaction. Repeated or
        # concurrent clicks cannot duplicate messages or skip an unfinished phase.
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS demo_phases(name TEXT PRIMARY KEY)')
        done = {row[0] for row in conn.execute('SELECT name FROM demo_phases')}
        selected = next((p for p in phases if p['name'] not in done), None)
        if phase in done:
            return {'phase':phase, 'inserted':0, 'complete':len(done)==len(phases)}
        if selected is None:
            return {'phase':None, 'inserted':0, 'complete':True}
        if phase != 'next' and selected['name'] != phase:
            raise ValueError('Import '+selected['name']+' before '+phase)
        inserted = _insert_messages(conn, selected['messages'])
        conn.execute('INSERT INTO demo_phases VALUES (?)', (selected['name'],))
        return {'phase':selected['name'], 'inserted':inserted, 'complete':len(done)+1==len(phases)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "append", "index", "serve"])
    parser.add_argument("--root", type=Path, default=Path(".demo"))
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument('--dataset', choices=['showcase', 'evaluation'], help='init only; new roots default to showcase')
    parser.add_argument('--anchor', help='init only; ISO datetime with timezone; default is the current time')
    parser.add_argument('--phase', default='next', help='append only: next, updates, replies (or increment for evaluation)')
    args = parser.parse_args(argv)
    if args.command != 'init' and (args.dataset is not None or args.anchor is not None):
        parser.error('--dataset/--anchor are only allowed for init')
    if args.command != 'append' and args.phase != 'next':
        parser.error('--phase is only allowed for append')
    if args.command == "init":
        print(f"Imported {initialize(args.root, args.dataset, args.anchor)} fictional messages into {args.root}.")
        data = workspace_manifest(args.root)
        print('Dataset: '+data['dataset']+' | clock: '+str(data['corpus'].get('anchor', 'fixed evaluation dates')))
        return 0
    if args.command == "append":
        print(json.dumps(append_phase(args.root, args.phase), ensure_ascii=False))
        return 0
    state = demo_state(args.root)
    if args.command == "index":
        result = web.update_qa_search_db_incremental(state)
        print(json.dumps({key: result.get(key) for key in ("update_mode", "message_count", "inserted", "updated_voices")}, ensure_ascii=False))
        return 0
    state.sync_interval = 2
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
        if state.sync_thread is not None:
            state.sync_thread.join(timeout=30)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
