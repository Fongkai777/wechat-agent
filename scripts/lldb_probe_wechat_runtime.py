#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import lldb
except Exception as exc:  # pragma: no cover
    print(f"Could not import lldb: {exc}", file=sys.stderr)
    print('Run with: PYTHONPATH="$(lldb -P)" /Library/Developer/CommandLineTools/usr/bin/python3 scripts/lldb_probe_wechat_runtime.py', file=sys.stderr)
    raise


def read_c_string(process, addr: int, size: int = 1024 * 1024) -> str:
    if not addr:
        return ""
    err = lldb.SBError()
    data = process.ReadMemory(addr, size, err)
    if not err.Success():
        return ""
    raw = bytes(data).split(b"\x00", 1)[0]
    return raw.decode("utf-8", errors="replace")


def eval_probe(frame, limit: int, image_filter: str, show_all_methods: bool) -> tuple[int, str]:
    all_methods = 1 if show_all_methods else 0
    expr = f"""
    typedef unsigned int uint32_t;
    extern void **objc_copyClassList(uint32_t *);
    extern const char *class_getName(void *);
    extern const char *class_getImageName(void *);
    extern void **class_copyMethodList(void *, uint32_t *);
    extern void *object_getClass(void *);
    extern void *method_getName(void *);
    extern const char *sel_getName(void *);
    extern void free(void *);
    extern void *malloc(unsigned long);
    extern int snprintf(char *, unsigned long, const char *, ...);
    extern char *strstr(const char *, const char *);

    uint32_t class_count = 0;
    void **classes = objc_copyClassList(&class_count);
    unsigned long cap = {limit};
    char *buf = (char *)malloc(cap);
    unsigned long off = 0;
    if (buf) {{
      off += snprintf(buf + off, cap - off, "objc classes: %u\\n", class_count);
      for (uint32_t i = 0; classes && i < class_count && off + 256 < cap; i++) {{
        const char *cname = class_getName(classes[i]);
        const char *image = class_getImageName(classes[i]);
        if (!cname) continue;
        if (!image || !strstr(image, "{image_filter}")) continue;
        int class_match = {all_methods} ||
          strstr(cname, "Encrypt") || strstr(cname, "encrypt") ||
          strstr(cname, "Cipher") || strstr(cname, "cipher") ||
          strstr(cname, "WCDB") || strstr(cname, "WCT") ||
          strstr(cname, "Account") || strstr(cname, "Storage") ||
          strstr(cname, "MessageDB") || strstr(cname, "DBEncrypt");
        if (!class_match) continue;
        off += snprintf(buf + off, cap - off, "\\nCLASS %s\\n  image: %s\\n", cname, image);

        uint32_t method_count = 0;
        void **methods = class_copyMethodList(classes[i], &method_count);
        for (uint32_t j = 0; methods && j < method_count && off + 256 < cap; j++) {{
          const char *mname = sel_getName(method_getName(methods[j]));
          if (!mname) continue;
          int method_match = {all_methods} ||
            strstr(mname, "Encrypt") || strstr(mname, "encrypt") ||
            strstr(mname, "Cipher") || strstr(mname, "cipher") ||
            strstr(mname, "Key") || strstr(mname, "key") ||
            strstr(mname, "DB") || strstr(mname, "db") ||
            strstr(mname, "Storage") || strstr(mname, "storage");
          if (method_match) {{
            off += snprintf(buf + off, cap - off, "  - %s\\n", mname);
          }}
        }}
        if (methods) free(methods);

        uint32_t class_method_count = 0;
        void *meta_class = object_getClass(classes[i]);
        void **class_methods = class_copyMethodList(meta_class, &class_method_count);
        for (uint32_t j = 0; class_methods && j < class_method_count && off + 256 < cap; j++) {{
          const char *mname = sel_getName(method_getName(class_methods[j]));
          if (!mname) continue;
          int method_match = {all_methods} ||
            strstr(mname, "Encrypt") || strstr(mname, "encrypt") ||
            strstr(mname, "Cipher") || strstr(mname, "cipher") ||
            strstr(mname, "Key") || strstr(mname, "key") ||
            strstr(mname, "DB") || strstr(mname, "db") ||
            strstr(mname, "Storage") || strstr(mname, "storage") ||
            strstr(mname, "default") || strstr(mname, "shared") ||
            strstr(mname, "Service") || strstr(mname, "service");
          if (method_match) {{
            off += snprintf(buf + off, cap - off, "  + %s\\n", mname);
          }}
        }}
        if (class_methods) free(class_methods);
      }}
      if (off < cap) buf[off] = 0;
      else buf[cap - 1] = 0;
    }}
    if (classes) free(classes);
    (unsigned long long)buf
    """

    opts = lldb.SBExpressionOptions()
    opts.SetLanguage(lldb.eLanguageTypeObjC_plus_plus)
    opts.SetIgnoreBreakpoints(True)
    opts.SetTimeoutInMicroSeconds(5_000_000)
    value = frame.EvaluateExpression(expr, opts)
    err = value.GetError()
    if not value.IsValid() or not err.Success():
        return 0, err.GetCString() or "invalid expression result"
    return value.GetValueAsUnsigned(0), ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Print WeChat Objective-C runtime classes/methods relevant to DB encryption.")
    parser.add_argument("--process-name", default="WeChat")
    parser.add_argument("--out", type=Path, default=Path("runtime_probe.txt"))
    parser.add_argument("--limit", type=int, default=1024 * 1024)
    parser.add_argument("--image-filter", default="WeChat.app")
    parser.add_argument("--all-methods", action="store_true")
    args = parser.parse_args()

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
        thread = process.GetSelectedThread()
        if not thread.IsValid() or thread.GetNumFrames() == 0:
            for candidate in process:
                if candidate.GetNumFrames() > 0:
                    thread = candidate
                    break
        if not thread.IsValid() or thread.GetNumFrames() == 0:
            print("no usable thread frame", file=sys.stderr)
            return 3

        ptr, expr_err = eval_probe(thread.GetFrameAtIndex(0), args.limit, args.image_filter, args.all_methods)
        if expr_err:
            print(expr_err, file=sys.stderr)
            return 4
        text = read_c_string(process, ptr, args.limit)
        args.out.write_text(text, encoding="utf-8")
        print(text[:12000])
        if len(text) > 12000:
            print(f"\n... truncated in terminal; full output saved to {args.out.resolve()}")
        else:
            print(f"\nsaved to {args.out.resolve()}")
        return 0
    finally:
        process.Detach()
        lldb.SBDebugger.Destroy(debugger)


if __name__ == "__main__":
    raise SystemExit(main())
