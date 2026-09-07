#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path

try:
    import lldb
except Exception as exc:  # pragma: no cover - this runs under Xcode/CLT Python
    print(f"Could not import lldb: {exc}", file=sys.stderr)
    print('Run with: PYTHONPATH="$(lldb -P)" /Library/Developer/CommandLineTools/usr/bin/python3 scripts/lldb_extract_runtime_key.py', file=sys.stderr)
    raise


PAGE_SIZE = 4096
SALT_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = 80
SQLITE_HEADER = b"SQLite format 3\x00"


def iter_db_files(db_storage: Path):
    for path in sorted(db_storage.rglob("*.db")):
        if path.name.endswith(("-wal", "-shm")):
            continue
        yield path


def verify_page1(db_path: Path, enc_key: bytes) -> bool:
    try:
        data = db_path.read_bytes()[:PAGE_SIZE]
    except OSError:
        return False
    if len(data) < PAGE_SIZE or data.startswith(SQLITE_HEADER):
        return False
    salt = data[:SALT_SIZE]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)
    signed = data[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE + 16]
    stored = data[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, signed, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), stored)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def eval_u64(frame, expr: str) -> tuple[int, str]:
    opts = lldb.SBExpressionOptions()
    opts.SetLanguage(lldb.eLanguageTypeObjC_plus_plus)
    opts.SetIgnoreBreakpoints(True)
    opts.SetTimeoutInMicroSeconds(2000000)
    value = frame.EvaluateExpression(expr, opts)
    err = value.GetError()
    if not value.IsValid() or not err.Success():
        return 0, err.GetCString() or "invalid expression result"
    return value.GetValueAsUnsigned(0), ""


def read_memory(process, addr: int, size: int) -> bytes:
    if addr == 0 or size <= 0 or size > 4096:
        return b""
    err = lldb.SBError()
    data = process.ReadMemory(addr, size, err)
    return bytes(data) if err.Success() else b""


def runtime_expr(selector: str, field: str) -> str:
    field_call = (
        f'((unsigned long long (*)(void *, void *))objc_msgSend)(data, sel_registerName("{field}"))'
        if field == "length"
        else f'((void *(*)(void *, void *))objc_msgSend)(data, sel_registerName("{field}"))'
    )
    call = f"""
    extern void *objc_getClass(const char *);
    extern void *sel_registerName(const char *);
    extern void *objc_msgSend(void *, void *, ...);
    void *wx_mm_service_center_obj = ((void *(*)(void *, void *))objc_msgSend)(objc_getClass("MMServiceCenter"), sel_registerName("defaultCenter"));
    void *wx_account_storage_obj = ((void *(*)(void *, void *, void *))objc_msgSend)(wx_mm_service_center_obj, sel_registerName("getService:"), objc_getClass("AccountStorage"));
    void *wx_db_encrypt_info_obj = ((void *(*)(void *, void *))objc_msgSend)(wx_account_storage_obj, sel_registerName("GetDBEncryptInfo"));
    void *data = ((void *(*)(void *, void *))objc_msgSend)(wx_db_encrypt_info_obj, sel_registerName("{selector}"));
    (unsigned long long){field_call}
    """
    return call


def extract_runtime_key(process, target) -> bytes:
    thread = process.GetSelectedThread()
    if not thread.IsValid() or thread.GetNumFrames() == 0:
        for candidate in process:
            if candidate.GetNumFrames() > 0:
                thread = candidate
                break
    if not thread.IsValid() or thread.GetNumFrames() == 0:
        print("no usable thread frame for expression evaluation", file=sys.stderr)
        return b""

    frame = thread.GetFrameAtIndex(0)
    selectors = ("m_dbEncryptKey", "dbEncryptKey", "DBEncryptKey")
    errors: list[str] = []
    for selector in selectors:
        length, len_err = eval_u64(frame, runtime_expr(selector, "length"))
        if len_err or length <= 0 or length > 256:
            errors.append(f"{selector}.length: {len_err or length}")
            continue
        ptr, ptr_err = eval_u64(frame, runtime_expr(selector, "bytes"))
        if ptr_err or not ptr:
            errors.append(f"{selector}.bytes: {ptr_err or ptr}")
            continue
        payload = read_memory(process, ptr, int(length))
        if len(payload) == length:
            print(f"runtime key object: DBEncryptInfo.{selector}, length={length}")
            return payload
        errors.append(f"{selector}: failed to read {length} bytes at 0x{ptr:x}")

    print("runtime key expression attempts failed:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return b""


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract WeChat DB runtime key through LLDB Objective-C expressions.")
    parser.add_argument("--db-storage", type=Path, default=Path("db_storage"))
    parser.add_argument("--out", type=Path, default=Path("all_keys.json"))
    parser.add_argument("--process-name", default="WeChat")
    args = parser.parse_args()

    db_storage = args.db_storage.resolve()
    out = args.out.resolve()
    dbs = list(iter_db_files(db_storage))
    print(f"db_storage: {db_storage}")
    print(f"encrypted DB candidates: {len(dbs)}")
    print(f"output: {out}")

    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(False)
    target = debugger.CreateTarget("")
    error = lldb.SBError()
    print(f"attaching to running {args.process_name}...")
    process = target.AttachToProcessWithName(debugger.GetListener(), args.process_name, False, error)
    if not error.Success() or not process.IsValid():
        print(f"attach failed: {error.GetCString()}", file=sys.stderr)
        return 2

    try:
        enc_key = extract_runtime_key(process, target)
        if len(enc_key) != 32:
            print(f"did not get a 32-byte key, got {len(enc_key)} byte(s)", file=sys.stderr)
            return 3

        result = {
            "_meta": {
                "tool": "scripts/lldb_extract_runtime_key.py",
                "db_storage": str(db_storage),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "verified_by": "SQLCipher page-1 HMAC",
            }
        }
        verified = 0
        for db_path in dbs:
            rel = db_path.relative_to(db_storage).as_posix()
            try:
                salt = db_path.read_bytes()[:SALT_SIZE].hex()
            except OSError:
                salt = ""
            if verify_page1(db_path, enc_key):
                result[rel] = {"enc_key": enc_key.hex(), "salt": salt, "source": "DBEncryptInfo.m_dbEncryptKey"}
                verified += 1

        save_json(out, result)
        print(f"verified {verified}/{len(dbs)} database key entries")
        print(f"saved: {out}")
        if verified == 0:
            print("the runtime key was read, but it did not validate against these DB files", file=sys.stderr)
            return 4
        return 0
    finally:
        process.Detach()
        lldb.SBDebugger.Destroy(debugger)


if __name__ == "__main__":
    raise SystemExit(main())
