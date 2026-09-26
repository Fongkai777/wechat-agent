"""Check a source checkout with temporary fictional data and no cloud requests."""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="wechat-smoke-") as directory, \
            patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected external request")):
        root = Path(directory)
        check(demo.initialize(root) == 30, "Initial import failed")
        state = demo.demo_state(root)
        web.update_qa_search_db_incremental(state)
        check(demo.import_messages(root, demo.fixture()["increment"]) == 10, "Append failed")
        state = demo.demo_state(root)
        update = web.update_qa_search_db_incremental(state)
        check(update["update_mode"] == "incremental" and update["inserted"] == 10, "Incremental index failed")
        check(web.update_qa_search_db_incremental(state)["inserted"] == 0, "Index is not idempotent")

        startup_threads = []
        start_thread = threading.Thread.start

        def track_start(thread):
            startup_threads.append(thread)
            start_thread(thread)

        with patch.object(threading.Thread, "start", track_start):
            handler = web.make_handler(state)
        for worker in startup_threads:
            worker.join(timeout=20)
            check(not worker.is_alive(), "Startup worker failed to finish")

        class QuietHandler(handler):
            def log_message(self, *args):
                pass

        # Port 0 is OS-assigned and released below; never touch the user's server.
        with ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                QuietHandler.goal_scheduler.start()
                QuietHandler.rag_scheduler.start()

                def get(path, as_json=True):
                    connection = HTTPConnection(*server.server_address, timeout=10)
                    try:
                        connection.request("GET", path)
                        response = connection.getresponse()
                        body = response.read()
                        check(response.status == 200, f"HTTP {response.status}: {path}")
                        check(bool(body), f"Empty response: {path}")
                        return json.loads(body) if as_json else body
                    finally:
                        connection.close()

                check(b"WeChat Agent" in get("/", False), "Missing application page")
                for asset in sorted(web.STATIC_DIR.rglob("*")):
                    if asset.is_file():
                        get("/static/" + asset.relative_to(web.STATIC_DIR).as_posix(), False)
                chats = get("/api/chats")["chats"]
                check(len(chats) == 6, "Chat list incomplete")
                messages = [message for chat in chats for message in
                            get("/api/messages?" + urlencode({"chat": chat["chat"]}))["messages"]]
                check(len(messages) == 40, "Message endpoints did not return all fixture messages")
                check(any(message["mine"] for message in messages), "Sender identity missing")
                check(any(not message["mine"] for message in messages), "Contact identity missing")
                for path in ("/api/status", "/api/rag/status", "/api/rag/schedule",
                             "/api/voice/status", "/api/llm/config", "/api/qa/conversations", "/api/goals"):
                    get(path)
            finally:
                QuietHandler.goal_scheduler.stop()
                QuietHandler.rag_scheduler.stop()
                state.sync_stop.set()
                server.shutdown()
                thread.join(timeout=10)
                check(not thread.is_alive(), "Test server failed to stop")
    print("PASS: import 30 + append 10, incremental index, page/assets, 6 chats/40 messages and workspace APIs.")
    print("No private data or cloud APIs used. Temporary server and data removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
