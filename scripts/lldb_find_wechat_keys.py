#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import lldb
except Exception as exc:  # pragma: no cover - this runs under Xcode/CLT Python
    print(f"Could not import lldb: {exc}", file=sys.stderr)
    print('Run with: PYTHONPATH="$(lldb -P)" /Library/Developer/CommandLineTools/usr/bin/python3 scripts/lldb_find_wechat_keys.py', file=sys.stderr)
    raise


HEX_RE = re.compile(rb"(?:x')?([0-9a-fA-F]{64})([0-9a-fA-F]{32})'?")
SQLITE_HEADER = b"SQLite format 3\x00"
SALT_SIZE = 16


def is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(c in "0123456789abcdef" for c in value)


def collect_db_salts(db_storage: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    salts: dict[str, list[str]] = {}
    paths: dict[str, str] = {}
    if not db_storage.exists():
        return salts, paths

    for path in sorted(db_storage.rglob("*.db")):
        if path.name.endswith(("-wal", "-shm")):
            continue
        try:
            head = path.read_bytes()[:SALT_SIZE]
        except OSError:
            continue
        if len(head) != SALT_SIZE or head.startswith(SQLITE_HEADER[:SALT_SIZE]):
            continue
        rel = path.relative_to(db_storage).as_posix()
        salt = head.hex()
        salts.setdefault(salt, []).append(rel)
        paths[str(path.resolve())] = rel
    return salts, paths


def load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_keys(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def reg_u64(frame, name: str) -> int:
    reg = frame.FindRegister(name)
    if not reg.IsValid():
        return 0
    value = reg.GetValue()
    if not value:
        return 0
    try:
        return int(value, 0)
    except ValueError:
        return 0


def read_memory(process, addr: int, size: int) -> bytes:
    if addr == 0 or size <= 0 or size > 4096:
        return b""
    err = lldb.SBError()
    data = process.ReadMemory(addr, size, err)
    return bytes(data) if err.Success() else b""


def find_symbol_addr(target, names: tuple[str, ...]) -> int:
    for name in names:
        matches = target.FindSymbols(name)
        for sym_ctx in matches:
            sym = sym_ctx.GetSymbol()
            if sym.IsValid():
                addr = sym.GetStartAddress().GetLoadAddress(target)
                if addr != lldb.LLDB_INVALID_ADDRESS:
                    return int(addr)
    return 0


def read_db_path_via_sqlite(frame, target, process, db_ptr: int) -> str:
    fn_addr = find_symbol_addr(target, ("sqlite3_db_filename",))
    if not fn_addr or not db_ptr:
        return ""

    opts = lldb.SBExpressionOptions()
    opts.SetLanguage(lldb.eLanguageTypeC_plus_plus)
    opts.SetIgnoreBreakpoints(True)
    opts.SetTimeoutInMicroSeconds(500000)
    expr = f'((const char *(*)(void *, const char *))0x{fn_addr:x})((void*)0x{db_ptr:x}, "main")'
    value = frame.EvaluateExpression(expr, opts)
    ptr = value.GetValueAsUnsigned(0) if value.IsValid() else 0
    if not ptr:
        return ""
    err = lldb.SBError()
    path = process.ReadCStringFromMemory(ptr, 4096, err)
    return path if err.Success() else ""


def parse_key_payload(payload: bytes) -> tuple[str, str]:
    if not payload:
        return "", ""

    match = HEX_RE.search(payload)
    if match:
        return match.group(1).decode("ascii").lower(), match.group(2).decode("ascii").lower()

    if len(payload) >= 48:
        return payload[:32].hex(), payload[32:48].hex()
    if len(payload) == 32:
        return payload.hex(), ""
    return "", ""


def add_key(
    keys: dict,
    rel: str,
    enc_key: str,
    salt: str,
    source: str,
    db_path: str = "",
) -> bool:
    if not is_hex(enc_key, 64):
        return False
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    previous = keys.get(rel)
    if isinstance(previous, dict) and previous.get("enc_key") == enc_key:
        return False
    keys[rel] = {
        "enc_key": enc_key,
        "salt": salt,
        "source": source,
        "captured_at": now,
    }
    if db_path:
        keys[rel]["db_path"] = db_path
    return True


def handle_sqlite_key(frame, target, process, args, keys, salts_by_hex, rel_by_abs_path, variant: str) -> bool:
    db_ptr = reg_u64(frame, "x0")
    if variant == "sqlite3_key_v2":
        key_ptr = reg_u64(frame, "x2")
        key_len = reg_u64(frame, "x3")
    else:
        key_ptr = reg_u64(frame, "x1")
        key_len = reg_u64(frame, "x2")
    payload = read_memory(process, key_ptr, key_len)
    enc_key, salt = parse_key_payload(payload)
    if not enc_key:
        return False

    db_path = read_db_path_via_sqlite(frame, target, process, db_ptr)
    rel = rel_by_abs_path.get(str(Path(db_path).resolve())) if db_path else None
    if rel:
        if not salt:
            try:
                salt = (Path(db_path).read_bytes()[:SALT_SIZE]).hex()
            except OSError:
                salt = ""
        return add_key(keys, rel, enc_key, salt, variant, db_path)

    if salt and salt in salts_by_hex:
        changed = False
        for matched_rel in salts_by_hex[salt]:
            changed = add_key(keys, matched_rel, enc_key, salt, variant, db_path) or changed
        return changed

    unmatched = keys.setdefault("_unmatched", [])
    item = {
        "enc_key": enc_key,
        "salt": salt,
        "source": variant,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if db_path:
        item["db_path"] = db_path
    if item not in unmatched:
        unmatched.append(item)
        return True
    return False


def create_breakpoint(target, name: str):
    bp = target.BreakpointCreateByName(name)
    bp.SetAutoContinue(False)
    return bp if bp.IsValid() else None


def breakpoint_ids(*bps) -> set[int]:
    return {bp.GetID() for bp in bps if bp and bp.IsValid()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture macOS WeChat SQLCipher keys with LLDB.")
    parser.add_argument("--db-storage", type=Path, default=Path("db_storage"))
    parser.add_argument("--out", type=Path, default=Path("all_keys.json"))
    parser.add_argument("--process-name", default="WeChat")
    args = parser.parse_args()

    db_storage = args.db_storage.resolve()
    out = args.out.resolve()
    salts_by_hex, rel_by_abs_path = collect_db_salts(db_storage)
    print(f"db_storage: {db_storage}")
    print(f"known encrypted DB salts: {sum(len(v) for v in salts_by_hex.values())}")
    print(f"output: {out}")
    print()
    print("Waiting for WeChat launch. Quit WeChat with Cmd+Q, then relaunch it.")

    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(False)
    target = debugger.CreateTarget("")
    error = lldb.SBError()
    process = target.AttachToProcessWithName(debugger.GetListener(), args.process_name, True, error)
    if not error.Success() or not process.IsValid():
        print(f"attach failed: {error.GetCString()}", file=sys.stderr)
        return 2

    print(f"attached to pid {process.GetProcessID()}")

    sqlite_key_bp = create_breakpoint(target, "sqlite3_key")
    sqlite_key_v2_bp = create_breakpoint(target, "sqlite3_key_v2")
    key_bp_ids = breakpoint_ids(sqlite_key_bp, sqlite_key_v2_bp)
    bp_variants = {}
    if sqlite_key_bp and sqlite_key_bp.IsValid():
        bp_variants[sqlite_key_bp.GetID()] = "sqlite3_key"
    if sqlite_key_v2_bp and sqlite_key_v2_bp.IsValid():
        bp_variants[sqlite_key_v2_bp.GetID()] = "sqlite3_key_v2"
    if not key_bp_ids:
        print("warning: could not create sqlite3_key breakpoints yet; LLDB may resolve them after libraries load")

    keys = load_existing(out)
    keys["_meta"] = {
        "tool": "scripts/lldb_find_wechat_keys.py",
        "db_storage": str(db_storage),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    save_keys(out, keys)

    print("capturing... click chats, contacts, search, favorites/moments as needed. Press Ctrl+C to stop.")
    try:
        while True:
            state = process.GetState()
            if state != lldb.eStateStopped:
                process.Continue()
                continue

            hit_key = False
            for thread in process:
                if thread.GetStopReason() != lldb.eStopReasonBreakpoint:
                    continue
                bp_id = thread.GetStopReasonDataAtIndex(0)
                if bp_id not in key_bp_ids:
                    continue
                frame = thread.GetFrameAtIndex(0)
                variant = bp_variants.get(bp_id, "sqlite3_key")
                if handle_sqlite_key(frame, target, process, args, keys, salts_by_hex, rel_by_abs_path, variant):
                    save_keys(out, keys)
                    printable = [k for k in keys.keys() if not k.startswith("_")]
                    last = printable[-1] if printable else "unmatched"
                    print(f"[+] captured {len(printable)} matched DB key(s); latest: {last}")
                hit_key = True

            if not hit_key:
                pass
            process.Continue()
    except KeyboardInterrupt:
        print()
        print("stopping and detaching...")
    finally:
        keys["_meta"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save_keys(out, keys)
        process.Detach()
        lldb.SBDebugger.Destroy(debugger)

    matched = len([k for k in keys.keys() if not k.startswith("_")])
    unmatched = len(keys.get("_unmatched", [])) if isinstance(keys.get("_unmatched"), list) else 0
    print(f"saved {matched} matched key(s), {unmatched} unmatched candidate(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
