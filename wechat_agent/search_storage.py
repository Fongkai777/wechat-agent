"""Space-efficient FTS storage and an offline, verified compaction command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sqlite3
import tempfile
from pathlib import Path


FTS_SCHEMA = """CREATE VIRTUAL TABLE messages_fts USING fts5(
    search_text, content='messages', content_rowid='id', tokenize='unicode61'
)"""


def has_private_fts_content(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='messages_fts_content'"
    ).fetchone() is not None


def delete_fts_rows(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Remove tokens before changing/deleting their backing message text."""
    if has_private_fts_content(conn):
        conn.executemany("DELETE FROM messages_fts WHERE rowid=?", ((i,) for i in ids))
        return
    for message_id in ids:
        row = conn.execute("SELECT search_text FROM messages WHERE id=?", (message_id,)).fetchone()
        if row is not None:
            conn.execute(
                "INSERT INTO messages_fts(messages_fts, rowid, search_text) VALUES ('delete', ?, ?)",
                (message_id, row[0]),
            )


def migrate_storage(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("Storage migration requires a separate transaction")
    # Explicit BEGIN covers DDL too; failure must restore the original FTS table.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if has_private_fts_content(conn):
            conn.execute("DROP TABLE messages_fts")
            conn.execute(FTS_SCHEMA)
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_chunks'").fetchone():
            # Keep the legacy column for old schema readers; never duplicate the
            # embedding input here. The vector and its source text remain intact.
            conn.execute("UPDATE semantic_chunks SET search_text='' WHERE search_text != ''")
        conn.execute("INSERT INTO messages_fts(messages_fts, rank) VALUES ('integrity-check', 1)")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def preserved_data(conn: sqlite3.Connection) -> dict:
    """Hash every retained value, including IDs, vectors, metadata and mappings."""
    result = {}
    for table, order in (("messages", "id"), ("semantic_chunks", "id"),
                         ("semantic_message_map", "message_id"), ("meta", "key"),
                         ("sqlite_sequence", "name")):
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        if not columns:
            continue
        if table == "semantic_chunks":
            columns.remove("search_text")
        selection = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(f'SELECT {selection} FROM "{table}" ORDER BY "{order}"'):
            digest.update(pickle.dumps(tuple(row), protocol=4))
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def compact_offline(path: Path) -> dict:
    """Caller must stop all services using this DB until this function returns."""
    path = path.resolve(strict=True)
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(p.exists() for p in sidecars):
        raise RuntimeError("SQLite sidecars exist; close database users/checkpoint before compaction")
    before_stat = path.stat()
    signature = (before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns)
    fd, name = tempfile.mkstemp(prefix=path.name + ".compact-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(temporary)
        try:
            before = preserved_data(source)
            source.backup(target)
            migrate_storage(target)
            target.execute("VACUUM")
            integrity = [row[0] for row in target.execute("PRAGMA integrity_check")]
            if integrity != ["ok"] or preserved_data(target) != before:
                raise RuntimeError("Compaction validation failed; original file is unchanged")
            target.execute("INSERT INTO messages_fts(messages_fts, rank) VALUES ('integrity-check', 1)")
            target.commit()
        finally:
            target.close()
            source.close()
        current = path.stat()
        if signature != (current.st_ino, current.st_size, current.st_mtime_ns) or any(p.exists() for p in sidecars):
            raise RuntimeError("Source database changed during compaction; original file is unchanged")
        after_size = temporary.stat().st_size
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return {"before_bytes": before_stat.st_size, "after_bytes": after_size,
                "freed_bytes": before_stat.st_size - after_size, "verified": before}
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact an offline QA index without regenerating embeddings")
    parser.add_argument("path", type=Path)
    parser.add_argument("--offline", action="store_true", help="Confirm all services using this database are stopped")
    args = parser.parse_args()
    if not args.offline:
        parser.error("Stop the web server and other database users, then pass --offline")
    print(json.dumps(compact_offline(args.path), indent=2))


if __name__ == "__main__":
    main()
