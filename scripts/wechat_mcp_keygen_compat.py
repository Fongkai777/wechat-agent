#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import struct
import sys
from pathlib import Path

try:
    import lldb
except Exception as exc:  # pragma: no cover
    print(f"Could not import lldb: {exc}", file=sys.stderr)
    print('Run with: PYTHONPATH="$(lldb -P)" /Library/Developer/CommandLineTools/usr/bin/python3 scripts/wechat_mcp_keygen_compat.py', file=sys.stderr)
    raise


PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
RESERVE_SIZE = 80
HMAC_SIZE = 64
SQLITE_HEADER = b"SQLite format 3\x00"

HEX_PATTERN = re.compile(rb"x'([0-9a-fA-F]{64,192})'")


def iter_db_files(db_storage: Path):
    for path in sorted(db_storage.rglob("*.db")):
        if path.name.endswith(("-wal", "-shm")):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < PAGE_SIZE:
            continue
        with path.open("rb") as f:
            page1 = f.read(PAGE_SIZE)
        if page1.startswith(SQLITE_HEADER):
            continue
        rel = path.relative_to(db_storage).as_posix()
        salt = page1[:SALT_SIZE].hex()
        yield rel, path, size, salt, page1


def verify_key_for_page(enc_key: bytes, page1: bytes) -> bool:
    salt = page1[:SALT_SIZE]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)
    signed = page1[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE + 16]
    stored = page1[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, signed, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), stored)


def parse_candidate(hex_str: str):
    if len(hex_str) == 96:
        return hex_str[:64].lower(), hex_str[64:].lower()
    if len(hex_str) == 64:
        return hex_str.lower(), None
    if len(hex_str) > 96 and len(hex_str) % 2 == 0:
        return hex_str[:64].lower(), hex_str[-32:].lower()
    return None, None


def collect_regions(process):
    regions = []
    region_info = lldb.SBMemoryRegionInfo()
    addr = 0
    while True:
        err = process.GetMemoryRegionInfo(addr, region_info)
        if err.Fail():
            break
        base = region_info.GetRegionBase()
        end = region_info.GetRegionEnd()
        if end <= base:
            break
        if region_info.IsReadable() and not region_info.IsExecutable():
            size = end - base
            if 0 < size < 500 * 1024 * 1024:
                regions.append((base, size))
        addr = end
        if addr == 0:
            break
    return regions


def load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Python 3.9 compatible macOS WeChat memory key scanner.")
    parser.add_argument("--db-storage", type=Path, default=Path("db_storage"))
    parser.add_argument("--out", type=Path, default=Path("all_keys.json"))
    parser.add_argument("--process-name", default="WeChat")
    args = parser.parse_args()

    db_storage = args.db_storage.resolve()
    out = args.out.resolve()
    db_files = list(iter_db_files(db_storage))
    salt_to_dbs = {}
    for rel, _path, _size, salt, _page1 in db_files:
        salt_to_dbs.setdefault(salt, []).append(rel)

    print("=" * 60)
    print("  WeChat MCP keygen compatible scanner")
    print("=" * 60)
    print(f"db_storage: {db_storage}")
    print(f"databases: {len(db_files)}, salts: {len(salt_to_dbs)}")
    print(f"output: {out}")

    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(False)
    target = debugger.CreateTarget("")
    error = lldb.SBError()
    print("\nAttaching to WeChat...")
    process = target.AttachToProcessWithName(debugger.GetListener(), args.process_name, False, error)
    if not error.Success() or not process.IsValid():
        print(f"attach failed: {error.GetCString()}", file=sys.stderr)
        return 2
    print(f"attached to pid {process.GetProcessID()}")

    try:
        key_by_salt = {}
        remaining = set(salt_to_dbs)
        all_hex_matches = 0
        total_scanned = 0

        regions = collect_regions(process)
        total_bytes = sum(size for _base, size in regions)
        print(f"scanning {len(regions)} readable non-executable regions ({total_bytes / 1024 / 1024:.0f} MB)")

        err = lldb.SBError()
        chunk_size = 8 * 1024 * 1024
        overlap = 256
        for idx, (base, size) in enumerate(regions, start=1):
            offset = 0
            tail = b""
            while offset < size:
                read_size = min(chunk_size, size - offset)
                data = process.ReadMemory(base + offset, read_size, err)
                offset += read_size
                total_scanned += read_size
                if not err.Success() or not data:
                    tail = b""
                    continue

                blob = tail + bytes(data)
                tail = blob[-overlap:]
                for match in HEX_PATTERN.finditer(blob):
                    candidate = match.group(1).decode("ascii")
                    enc_key_hex, salt_hex = parse_candidate(candidate)
                    if not enc_key_hex:
                        continue
                    all_hex_matches += 1

                    try:
                        enc_key = bytes.fromhex(enc_key_hex)
                    except ValueError:
                        continue

                    if salt_hex and salt_hex in remaining:
                        for rel, _path, _size, db_salt, page1 in db_files:
                            if db_salt == salt_hex and verify_key_for_page(enc_key, page1):
                                key_by_salt[salt_hex] = enc_key_hex
                                remaining.discard(salt_hex)
                                print(f"FOUND {len(key_by_salt)}/{len(salt_to_dbs)}: {', '.join(salt_to_dbs[salt_hex])}")
                                break
                    elif not salt_hex and remaining:
                        for rel, _path, _size, db_salt, page1 in db_files:
                            if db_salt in remaining and verify_key_for_page(enc_key, page1):
                                key_by_salt[db_salt] = enc_key_hex
                                remaining.discard(db_salt)
                                print(f"FOUND {len(key_by_salt)}/{len(salt_to_dbs)}: {', '.join(salt_to_dbs[db_salt])}")
                                break

                if not remaining:
                    break

            if idx % 50 == 0 or idx == len(regions) or not remaining:
                progress = total_scanned / total_bytes * 100 if total_bytes else 100
                print(f"{progress:.1f}% scanned, {len(key_by_salt)}/{len(salt_to_dbs)} salts, {all_hex_matches} key-shaped patterns")

            if not remaining:
                break

        if remaining and key_by_salt:
            print("\ncross-verifying known keys against remaining DBs...")
            for rel, _path, _size, db_salt, page1 in db_files:
                if db_salt not in remaining:
                    continue
                for known_key in set(key_by_salt.values()):
                    if verify_key_for_page(bytes.fromhex(known_key), page1):
                        key_by_salt[db_salt] = known_key
                        remaining.discard(db_salt)
                        print(f"CROSS {len(key_by_salt)}/{len(salt_to_dbs)}: {', '.join(salt_to_dbs[db_salt])}")
                        break

        result = load_existing(out)
        result["_meta"] = {
            "tool": "scripts/wechat_mcp_keygen_compat.py",
            "db_storage": str(db_storage),
            "verified_by": "SQLCipher page-1 HMAC",
        }
        for rel, _path, _size, salt, _page1 in db_files:
            if salt in key_by_salt:
                result[rel] = {
                    "enc_key": key_by_salt[salt],
                    "salt": salt,
                    "source": "wechat-mcp compatible memory scan",
                }

        save_json(out, result)
        print("\n" + "=" * 60)
        print(f"result: {len(key_by_salt)}/{len(salt_to_dbs)} salts found")
        print(f"saved: {out}")
        if remaining:
            print("missing:")
            for salt in sorted(remaining):
                print(f"  {salt}: {', '.join(salt_to_dbs[salt])}")
            return 1
        return 0
    finally:
        process.Detach()
        lldb.SBDebugger.Destroy(debugger)


if __name__ == "__main__":
    raise SystemExit(main())
