from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover - reported at runtime
    AES = None

try:
    import zstandard as zstd
except Exception:  # pragma: no cover - optional dependency
    zstd = None


PROJECT_ROOT = Path.cwd()
SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = IV_SIZE + HMAC_SIZE
WAL_HEADER_SIZE = 32
WAL_FRAME_HEADER_SIZE = 24
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


@dataclass(frozen=True)
class Paths:
    db_storage: Path
    decrypted: Path
    exports: Path


def rel_display(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def iter_db_files(db_storage: Path) -> Iterable[Path]:
    for path in sorted(db_storage.rglob("*.db")):
        name = path.name
        if name.endswith("-wal") or name.endswith("-shm"):
            continue
        yield path


def read_head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as f:
        return f.read(size)


def db_state(path: Path) -> tuple[str, str]:
    head = read_head(path, 64)
    if head.startswith(SQLITE_HEADER):
        return "plain-sqlite", ""
    if len(head) >= SALT_SIZE:
        return "encrypted-sqlcipher", head[:SALT_SIZE].hex()
    return "unknown", ""


def cmd_inspect(args: argparse.Namespace) -> int:
    base = args.db_storage.resolve()
    if not base.exists():
        print(f"db_storage not found: {base}", file=sys.stderr)
        return 2

    rows: list[tuple[str, int, str, str]] = []
    for path in iter_db_files(base):
        state, salt = db_state(path)
        rows.append((rel_display(path, base), path.stat().st_size, state, salt))

    print(f"db_storage: {base}")
    print(f"databases: {len(rows)}")
    print()
    print(f"{'relative path':48} {'size':>12} {'state':20} salt")
    print("-" * 112)
    for rel, size, state, salt in rows:
        print(f"{rel:48} {size:12} {state:20} {salt}")
    return 0


def run_text(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=10)
        return result.returncode, (result.stdout or result.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def cmd_doctor(args: argparse.Namespace) -> int:
    base = args.db_storage.resolve()
    print("WeChat Agent doctor")
    print(f"project:    {Path.cwd()}")
    print(f"db_storage: {base} {'OK' if base.exists() else 'MISSING'}")
    print(f"pycryptodome: {'OK' if AES is not None else 'MISSING'}")
    print(f"zstandard:   {'OK' if zstd is not None else 'MISSING'}")

    lldb_code, lldb_out = run_text(["/usr/bin/which", "lldb"])
    print(f"lldb:        {lldb_out if lldb_code == 0 else 'MISSING'}")

    sip_code, sip_out = run_text(["/usr/bin/csrutil", "status"])
    print(f"SIP:         {sip_out if sip_code == 0 else 'unknown'}")

    sqlcipher_code, sqlcipher_out = run_text(["/usr/bin/which", "sqlcipher"])
    print(f"sqlcipher:   {sqlcipher_out if sqlcipher_code == 0 else 'MISSING'}")

    if base.exists():
        encrypted = plain = 0
        for path in iter_db_files(base):
            state, _salt = db_state(path)
            if state == "plain-sqlite":
                plain += 1
            elif state == "encrypted-sqlcipher":
                encrypted += 1
        print(f"databases:   {encrypted} encrypted, {plain} plain")

    key_path = args.keys.resolve()
    print(f"keys:        {key_path} {'OK' if key_path.exists() else 'MISSING'}")
    if key_path.exists():
        try:
            keys = load_keys(key_path)
            print(f"key entries: {len(keys)}")
        except Exception as exc:
            print(f"key entries: unreadable ({exc})")
    return 0


def load_keys(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)

    keys: dict[str, str] = {}
    for rel, info in raw.items():
        if rel.startswith("_"):
            continue
        if isinstance(info, str):
            key_hex = info
        elif isinstance(info, dict):
            key_hex = info.get("enc_key") or info.get("key") or info.get("key_hex")
        else:
            key_hex = None
        if not key_hex:
            continue
        key_hex = key_hex.strip().lower()
        if len(key_hex) == 64 and all(c in "0123456789abcdef" for c in key_hex):
            keys[rel.replace("\\", "/")] = key_hex
    return keys


def key_for_rel(keys: dict[str, str], rel: str) -> str | None:
    norm = rel.replace("\\", "/")
    candidates = {
        norm,
        norm.replace("/", os.sep),
        f"db_storage/{norm}",
        f"./{norm}",
    }
    for candidate in candidates:
        if candidate in keys:
            return keys[candidate]
    suffix = "/" + norm
    for candidate, key in keys.items():
        if candidate.endswith(suffix):
            return key
    return None


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)


def verify_page1(data: bytes, enc_key: bytes) -> bool:
    if len(data) < PAGE_SIZE:
        return False
    salt = data[:SALT_SIZE]
    mac_key = derive_mac_key(enc_key, salt)
    signed = data[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    stored = data[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, signed, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), stored)


def decrypt_page(page: bytes, enc_key: bytes, page_no: int) -> bytes:
    if AES is None:
        raise RuntimeError("pycryptodome is required: python3 -m pip install -r requirements.txt")

    iv = page[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    if page_no == 1:
        encrypted = page[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE]
        decrypted = cipher.decrypt(encrypted)
        return SQLITE_HEADER + decrypted + (b"\x00" * RESERVE_SIZE)

    encrypted = page[: PAGE_SIZE - RESERVE_SIZE]
    decrypted = cipher.decrypt(encrypted)
    return decrypted + (b"\x00" * RESERVE_SIZE)


def decrypt_db(src: Path, dst: Path, key_hex: str) -> bool:
    head = read_head(src, PAGE_SIZE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    if head.startswith(SQLITE_HEADER):
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        return True

    if AES is None:
        return False

    enc_key = bytes.fromhex(key_hex)
    if not verify_page1(head, enc_key):
        return False

    try:
        with src.open("rb") as fin, tmp.open("wb") as fout:
            page_no = 1
            while True:
                page = fin.read(PAGE_SIZE)
                if not page:
                    break
                if len(page) < PAGE_SIZE:
                    page += b"\x00" * (PAGE_SIZE - len(page))
                fout.write(decrypt_page(page, enc_key, page_no))
                page_no += 1
        decrypt_wal(src.with_name(src.name + "-wal"), tmp, enc_key)
        os.replace(tmp, dst)
        return True
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def decrypt_wal(wal_path: Path, dst: Path, enc_key: bytes) -> int:
    if not wal_path.exists() or wal_path.stat().st_size <= WAL_HEADER_SIZE:
        return 0

    frame_size = WAL_FRAME_HEADER_SIZE + PAGE_SIZE
    patched = 0
    with wal_path.open("rb") as wal, dst.open("r+b") as out:
        header = wal.read(WAL_HEADER_SIZE)
        wal_salt1 = struct.unpack(">I", header[16:20])[0]
        wal_salt2 = struct.unpack(">I", header[20:24])[0]
        while wal.tell() + frame_size <= wal_path.stat().st_size:
            frame_header = wal.read(WAL_FRAME_HEADER_SIZE)
            if len(frame_header) < WAL_FRAME_HEADER_SIZE:
                break
            page_no = struct.unpack(">I", frame_header[0:4])[0]
            frame_salt1 = struct.unpack(">I", frame_header[8:12])[0]
            frame_salt2 = struct.unpack(">I", frame_header[12:16])[0]
            page = wal.read(PAGE_SIZE)
            if len(page) < PAGE_SIZE:
                break
            if page_no == 0 or page_no > 1_000_000:
                continue
            if frame_salt1 != wal_salt1 or frame_salt2 != wal_salt2:
                continue
            out.seek((page_no - 1) * PAGE_SIZE)
            out.write(decrypt_page(page, enc_key, page_no))
            patched += 1
    return patched


def cmd_decrypt(args: argparse.Namespace) -> int:
    if AES is None:
        print("Missing dependency: pycryptodome. Run: python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    base = args.db_storage.resolve()
    out = args.out.resolve()
    key_path = args.keys.resolve()
    if not key_path.exists():
        print(f"Key file not found: {key_path}", file=sys.stderr)
        print("Create it first with a local WeChat key extractor, then rerun decrypt.", file=sys.stderr)
        return 2

    keys = load_keys(key_path)
    if not keys:
        print(f"No usable keys in {args.keys}", file=sys.stderr)
        return 2

    success = failed = skipped = 0
    for src in iter_db_files(base):
        rel = rel_display(src, base)
        if args.only and args.only not in rel:
            continue
        key_hex = key_for_rel(keys, rel)
        if not key_hex:
            print(f"SKIP {rel}: no key")
            skipped += 1
            continue
        dst = out / rel
        if args.dry_run:
            print(f"WOULD decrypt {rel}")
            continue
        ok = decrypt_db(src, dst, key_hex)
        if ok:
            print(f"OK   {rel}")
            cleanup_sqlite_sidecars(dst)
            success += 1
        else:
            print(f"FAIL {rel}: key did not verify page-1 HMAC")
            failed += 1

    print(f"done: {success} ok, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


def cleanup_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass


def sqlite_rows(path: Path, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def table_names(conn: sqlite3.Connection, like: str | None = None) -> list[str]:
    if like:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?", (like,))
    else:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [r[0] for r in rows.fetchall()]


def find_message_dbs(decrypted: Path, include_biz: bool = False) -> list[Path]:
    msg_dir = decrypted / "message"
    patterns = ["message_*.db"]
    if include_biz:
        patterns.append("biz_message_*.db")
    result: list[Path] = []
    for pattern in patterns:
        for path in sorted(msg_dir.glob(pattern)):
            if "-wal" not in path.name and "-shm" not in path.name:
                result.append(path)
    return result


def load_contacts(decrypted: Path) -> dict[str, str]:
    contact_db = decrypted / "contact" / "contact.db"
    if not contact_db.exists():
        return {}
    conn = sqlite3.connect(contact_db)
    conn.row_factory = sqlite3.Row
    contacts: dict[str, str] = {}
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(contact)").fetchall()}
        if "username" not in cols:
            return {}
        display_cols = [c for c in ("remark", "nick_name", "nickname", "alias") if c in cols]
        select = ", ".join(["username"] + display_cols)
        for row in conn.execute(f"SELECT {select} FROM contact"):
            name = ""
            for col in display_cols:
                if row[col]:
                    name = str(row[col])
                    break
            contacts[row["username"]] = name or row["username"]
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return contacts


def load_sessions(decrypted: Path, contacts: dict[str, str]) -> dict[str, dict[str, Any]]:
    session_db = decrypted / "session" / "session.db"
    if not session_db.exists():
        return {}
    conn = sqlite3.connect(session_db)
    conn.row_factory = sqlite3.Row
    sessions: dict[str, dict[str, Any]] = {}
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(SessionTable)").fetchall()}
        if "username" not in cols:
            return {}
        optional = [c for c in ("type", "last_timestamp", "sort_timestamp", "summary", "last_sender_display_name") if c in cols]
        select = ", ".join(["username"] + optional)
        for row in conn.execute(f"SELECT {select} FROM SessionTable"):
            username = row["username"]
            item = {c: row[c] for c in optional}
            item["username"] = username
            item["display_name"] = contacts.get(username, username)
            sessions[username] = item
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return sessions


def cmd_index(args: argparse.Namespace) -> int:
    decrypted = args.decrypted.resolve()
    out_dir = args.out.resolve()
    if not decrypted.exists():
        print(f"decrypted directory not found: {decrypted}", file=sys.stderr)
        print("Run decrypt after creating all_keys.json.", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    contacts = load_contacts(decrypted)
    sessions = load_sessions(decrypted, contacts)
    session_by_hash = {hashlib.md5(k.encode("utf-8")).hexdigest().lower(): v for k, v in sessions.items()}

    chats: dict[str, dict[str, Any]] = {}
    for db_path in find_message_dbs(decrypted, include_biz=args.include_biz):
        conn = sqlite3.connect(db_path)
        try:
            msg_tables = [t for t in table_names(conn) if t.startswith(("Msg_", "Chat_", "msg_", "chat_"))]
            for table in msg_tables:
                row = conn.execute(f'SELECT COUNT(*), MIN(create_time), MAX(create_time) FROM "{table}"').fetchone()
                count, first_ts, last_ts = row
                if not count:
                    continue
                table_hash = table.split("_", 1)[1].lower()
                rec = chats.setdefault(table_hash, {
                    "table_hash": table_hash,
                    "chat": None,
                    "display_name": None,
                    "type": "unknown",
                    "total_messages": 0,
                    "first_ts": None,
                    "last_ts": None,
                    "shards": [],
                })
                sess = session_by_hash.get(table_hash)
                if sess:
                    username = sess["username"]
                    rec["chat"] = username
                    rec["display_name"] = sess.get("display_name") or username
                    rec["type"] = "group" if "@chatroom" in username else "private"
                    rec["session"] = sess
                rec["total_messages"] += count
                rec["shards"].append({"db": rel_display(db_path, decrypted), "table": table, "count": count})
                rec["first_ts"] = min_non_null(rec["first_ts"], first_ts)
                rec["last_ts"] = max_non_null(rec["last_ts"], last_ts)
        finally:
            conn.close()

    records = sorted(chats.values(), key=lambda r: r["last_ts"] or 0, reverse=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decrypted": str(decrypted),
        "total_chats": len(records),
        "total_messages": sum(r["total_messages"] for r in records),
        "chats": records,
    }

    json_path = out_dir / "all_chats_index.json"
    txt_path = out_dir / "all_chats_index.txt"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with txt_path.open("w", encoding="utf-8") as f:
        f.write(f"# WeChat chat index\n# {payload['generated_at']}\n")
        f.write(f"# {payload['total_chats']} chats, {payload['total_messages']} messages\n\n")
        f.write(f"{'type':8} {'messages':>9} {'first':19} {'last':19} chat\n")
        f.write("-" * 96 + "\n")
        for rec in records:
            first = fmt_ts(rec["first_ts"])
            last = fmt_ts(rec["last_ts"])
            chat = rec["display_name"] or rec["chat"] or f"[unknown:{rec['table_hash']}]"
            f.write(f"{rec['type']:8} {rec['total_messages']:9} {first:19} {last:19} {chat}\n")

    print(f"indexed {len(records)} chats")
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    return 0


def min_non_null(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def max_non_null(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def fmt_ts(ts: Any) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def load_name2id(conn: sqlite3.Connection) -> dict[int, str]:
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT rowid, user_name FROM Name2Id")}
    except sqlite3.Error:
        return {}


def decode_content(row: sqlite3.Row) -> str:
    for key in ("compress_content", "message_content", "content"):
        if key not in row.keys():
            continue
        value = row[key]
        if value is None:
            continue
        if isinstance(value, bytes):
            decoded = try_zstd(value)
            if decoded is not None:
                return decoded
            return value.decode("utf-8", errors="replace")
        text = str(value)
        if text:
            return text
    return ""


def try_zstd(data: bytes) -> str | None:
    if not data.startswith(ZSTD_MAGIC) or zstd is None:
        return None
    try:
        return zstd.ZstdDecompressor().decompress(data).decode("utf-8", errors="replace")
    except Exception:
        return None


def classify_type(local_type: int) -> str:
    base_type = local_type & 0xFFFFFFFF
    if base_type == 1:
        return "text"
    if base_type == 3:
        return "image"
    if base_type == 34:
        return "voice"
    if base_type == 43:
        return "video"
    if base_type == 47:
        return "sticker"
    if base_type == 49:
        return "app"
    if base_type == 50:
        return "voip"
    if base_type == 10000:
        return "system"
    return f"type_{base_type}"


def collect_chat_messages(decrypted: Path, chat: str, include_biz: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_hash = hashlib.md5(chat.encode("utf-8")).hexdigest().lower()
    table_candidates = [f"Msg_{target_hash}", f"Chat_{target_hash}", f"msg_{target_hash}", f"chat_{target_hash}"]
    contacts = load_contacts(decrypted)
    messages: list[dict[str, Any]] = []
    shard_hits: list[dict[str, Any]] = []

    for db_path in find_message_dbs(decrypted, include_biz=include_biz):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            existing = set(table_names(conn))
            table = next((t for t in table_candidates if t in existing), None)
            if not table:
                continue
            cols = {r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
            needed = [c for c in ("local_id", "local_type", "real_sender_id", "create_time", "message_content", "compress_content") if c in cols]
            if "create_time" not in needed:
                continue
            name2id = load_name2id(conn)
            rows = conn.execute(f'SELECT {", ".join(needed)} FROM "{table}" ORDER BY create_time ASC').fetchall()
            shard_hits.append({"db": rel_display(db_path, decrypted), "table": table, "count": len(rows)})
            for row in rows:
                local_type = int(row["local_type"]) if "local_type" in row.keys() and row["local_type"] is not None else 0
                sender_id = row["real_sender_id"] if "real_sender_id" in row.keys() else None
                sender_username = name2id.get(sender_id, str(sender_id) if sender_id is not None else "")
                messages.append({
                    "timestamp": row["create_time"],
                    "time": fmt_ts(row["create_time"]),
                    "chat": chat,
                    "sender": contacts.get(sender_username, sender_username),
                    "sender_username": sender_username,
                    "type": classify_type(local_type),
                    "local_type": local_type,
                    "content": decode_content(row),
                })
        finally:
            conn.close()
    messages.sort(key=lambda m: m["timestamp"] or 0)
    return messages, shard_hits


def cmd_export(args: argparse.Namespace) -> int:
    decrypted = args.decrypted.resolve()
    out_dir = args.out.resolve()
    if not decrypted.exists():
        print(f"decrypted directory not found: {decrypted}", file=sys.stderr)
        print("Run decrypt after creating all_keys.json.", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or args.chat
    basename = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "wechat_export"

    messages, hits = collect_chat_messages(decrypted, args.chat, include_biz=args.include_biz)
    if not messages:
        print("No messages found. Check the chat username/@chatroom id, or run index first.", file=sys.stderr)
        return 1

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "chat": args.chat,
        "name": name,
        "total": len(messages),
        "shards": hits,
        "messages": messages,
    }

    json_path = out_dir / f"{basename}.json"
    txt_path = out_dir / f"{basename}.txt"
    csv_path = out_dir / f"{basename}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with txt_path.open("w", encoding="utf-8") as f:
        f.write(f"# {name}\n# {args.chat}\n# {len(messages)} messages\n\n")
        for msg in messages:
            content = msg["content"] or f"[{msg['type']}]"
            f.write(f"[{msg['time']}] {msg['sender']}: {content}\n")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "timestamp", "chat", "sender", "sender_username", "type", "local_type", "content"])
        writer.writeheader()
        for msg in messages:
            writer.writerow({k: msg.get(k, "") for k in writer.fieldnames})

    print(f"exported {len(messages)} messages")
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    print(f"wrote {csv_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-agent")
    parser.add_argument("--db-storage", type=Path, default=Path("db_storage"), help="Copied WeChat db_storage directory")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="Check local environment and project state")
    doctor_p.add_argument("--keys", type=Path, default=Path("all_keys.json"), help="Key JSON from a key extractor")
    doctor_p.set_defaults(func=cmd_doctor)

    inspect_p = sub.add_parser("inspect", help="Inspect copied encrypted/plain databases")
    inspect_p.set_defaults(func=cmd_inspect)

    decrypt_p = sub.add_parser("decrypt", help="Decrypt SQLCipher 4 databases with all_keys.json")
    decrypt_p.add_argument("--keys", type=Path, default=Path("all_keys.json"), help="Key JSON from a key extractor")
    decrypt_p.add_argument("--out", type=Path, default=Path("decrypted"), help="Output directory for decrypted DBs")
    decrypt_p.add_argument("--only", help="Only decrypt DBs whose relative path contains this text")
    decrypt_p.add_argument("--dry-run", action="store_true")
    decrypt_p.set_defaults(func=cmd_decrypt)

    index_p = sub.add_parser("index", help="Index chats from decrypted DBs")
    index_p.add_argument("--decrypted", type=Path, default=Path("decrypted"))
    index_p.add_argument("--out", type=Path, default=Path("exports/index"))
    index_p.add_argument("--include-biz", action="store_true", help="Include biz_message_*.db shards")
    index_p.set_defaults(func=cmd_index)

    export_p = sub.add_parser("export", help="Export one chat from decrypted DBs")
    export_p.add_argument("--decrypted", type=Path, default=Path("decrypted"))
    export_p.add_argument("--out", type=Path, default=Path("exports/chats"))
    export_p.add_argument("--chat", required=True, help="Chat username or NNN@chatroom id")
    export_p.add_argument("--name", help="Human-readable output name")
    export_p.add_argument("--include-biz", action="store_true", help="Include biz_message_*.db shards")
    export_p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
