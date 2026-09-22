"""Conservative pre-publication checks; report locations, never matched secrets."""
import argparse
from pathlib import Path
import re
import subprocess
import sys


PATTERNS = {
    "provider-key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "local-home-path": re.compile(rb"/Users/(?!YOUR_USER|username|example|<)[A-Za-z0-9_.-]+/"),
    "personal-wechat-id": re.compile(rb"wxid_[a-z0-9]{12,}"),
    "personal-chatroom-id": re.compile(rb"(?<![0-9])(?!(?:1234567890|0+)@chatroom)[0-9]{10,}@chatroom"),
}
PRIVATE_DIRS = {"web_cache", "decrypted", "db_storage", "exports", "msg", ".demo", "logs", "artifacts", ".venv", "work"}
PRIVATE_NAMES = {"all_keys.json", "voice_transcriptions.json", "runtime_probe.txt", ".env"}
PRIVATE_EXT = {".db", ".sqlite", ".sqlite3", ".dat", ".pem", ".key", ".pkl"}


def findings(name, content):
    path = Path(name)
    issues = []
    if set(path.parts) & PRIVATE_DIRS or path.name in PRIVATE_NAMES or path.suffix in PRIVATE_EXT:
        issues.append("private-data-path")
    if b"\x00" not in content[:4096]:
        issues.extend(label for label, pattern in PATTERNS.items() if pattern.search(content))
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Inspect local branch and remote-tracking history (not editor checkpoints)")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]).split(b"\0")
    failures = []
    for raw in sorted(set(paths)):
        if not raw:
            continue
        path = Path(raw.decode())
        if path.is_file():
            failures.extend((str(path), label) for label in findings(str(path), path.read_bytes()))
    for path, label in failures:
        print(f"WORKTREE {label}: {path}")
    historical = []
    if args.history:
        objects = subprocess.check_output(["git", "rev-list", "--objects", "--branches", "--remotes"], text=True)
        for row in objects.splitlines():
            sha, _, name = row.partition(" ")
            if not name:
                continue
            kind = subprocess.check_output(["git", "cat-file", "-t", sha], text=True).strip()
            if kind != "blob":
                continue
            content = subprocess.check_output(["git", "cat-file", "blob", sha])
            historical.extend((name, label) for label in findings(name, content))
        for name, label in sorted(set(historical)):
            print(f"HISTORY {label}: {name}")
    print(f"Worktree findings: {len(failures)}; historical locations: {len(set(historical))}")
    print("Heuristic check only; manually review staged changes and screenshots before publishing.")
    return int(bool(failures or historical))


if __name__ == "__main__":
    sys.exit(main())
