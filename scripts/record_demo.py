"""Record the real app with fictional data and explicitly approved model credentials."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web
from scripts.evaluate_retrieval import UsageRecorder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New, empty synthetic workspace")
    parser.add_argument("--live-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/demo-video"))
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    if args.root.exists() and any(args.root.iterdir()):
        parser.error("Recording requires a fresh directory; existing history is never reset.")
    if not args.live_config.is_file():
        parser.error("The explicitly selected model configuration must exist.")
    real_config = web.load_llm_config(args.live_config)
    qa, task = real_config["qa"], real_config["task"]
    if not web.resolve_llm_api_key(qa) or not web.resolve_llm_api_key(task):
        parser.error("Q&A and task credentials must be configured.")
    args.output.mkdir(parents=True, exist_ok=True)
    demo.initialize(args.root)
    state = demo.demo_state(args.root)
    config = web.load_llm_config(state.llm_config)
    config["qa"] = dict(qa, max_context_messages=30)
    config["task"] = dict(task)
    original_config_loader = web.load_llm_config

    def in_memory_config(path):
        if Path(path).resolve() == state.llm_config.resolve():
            return config
        return original_config_loader(path)

    with ExitStack() as stack:
        stack.enter_context(patch.object(web, "load_llm_config", side_effect=in_memory_config))
        usage = stack.enter_context(UsageRecorder(args.output))
        usage.stage = "recording"
        web.update_qa_search_db_incremental(state)

        class RecordingHandler(web.make_handler(state)):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path == "/api/demo/append":
                    inserted = demo.import_messages(args.root, demo.fixture()["increment"])
                    state.chats = web.build_chat_index(state)
                    state.last_synced_at = datetime.now().isoformat(timespec="seconds")
                    return self.json_response({"ok": True, "inserted": inserted})
                return super().do_POST()

        with ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            RecordingHandler.goal_scheduler.start()
            RecordingHandler.rag_scheduler.start()
            try:
                result = subprocess.run([
                    args.node, "scripts/record_demo.cjs", "--url",
                    f"http://127.0.0.1:{server.server_port}", "--output", str(args.output.resolve()),
                ], env=os.environ.copy(), timeout=1200)
                (args.output / "provenance.json").write_text(json.dumps({
                    "synthetic_only": True, "qa_model": qa["model"], "task_model": task["model"],
                    "retrieval": "local lexical; embedding and reranking disabled",
                    "initial_messages": 30, "final_messages": sum(c["total_messages"] for c in state.chats),
                    "recorded_at": datetime.now().isoformat(timespec="seconds"),
                    "waits_edited": True, "api_calls": len(usage.calls), "exit_code": result.returncode,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return result.returncode
            finally:
                RecordingHandler.goal_scheduler.stop()
                RecordingHandler.rag_scheduler.stop()
                state.sync_stop.set()
                server.shutdown()
                thread.join(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
