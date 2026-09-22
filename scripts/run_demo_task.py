"""Run real Q&A or one read-only task against the isolated synthetic demo corpus."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web
from scripts.evaluate_retrieval import UsageRecorder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".demo/showcase"))
    parser.add_argument("--mode", choices=("qa", "task"), default="task")
    parser.add_argument("--live-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("eval/results/task"))
    args = parser.parse_args()
    demo.initialize(args.root)
    demo.import_messages(args.root, demo.fixture()["increment"])
    state = demo.demo_state(args.root)
    web.update_qa_search_db_incremental(state)
    state.llm_config = args.live_config.resolve()
    handler = web.make_handler(state)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "qa":
        events = []

        def emit(event, **data):
            events.append({"event": event, **data})
            print(event + ": " + str(data.get("message") or data.get("error") or ""), flush=True)

        with UsageRecorder(args.output) as recorder:
            recorder.stage = "demo-index"
            if web.load_llm_config(state.llm_config)["embedding"].get("enabled"):
                web.build_or_update_qa_semantic_index(state)
            recorder.stage = "demo-qa"
            handler.__new__(handler).run_qa_stream({
                "question": "新加坡有哪些搜索或 RAG 算法实习？列出要求、截止时间和申请方式。",
            }, emit)
        (args.output / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return int(not any(event["event"] == "done" for event in events))
    scheduler = handler.goal_scheduler
    goal_id = scheduler.store.save({
        "title": "新加坡搜索与 RAG 实习",
        "prompt": "追踪新加坡的搜索与 RAG 实习，合并重复岗位，列出要求、截止时间和申请方式，结合聊天中的建议列出准备事项。",
        "enabled": False, "interval_value": 1, "interval_unit": "days",
        "range_type": "recent_days", "range_value": 30, "range_unit": "days",
    })
    goal = scheduler.store.claim_manual(goal_id)
    with UsageRecorder(args.output) as recorder:
        recorder.stage = "demo-task"
        scheduler._execute(goal)
    run = scheduler.store.history(goal_id)[0]
    run["task_config"] = {"range_type": "recent_days", "range_value": 30, "range_unit": "days", "interval_value": 1, "interval_unit": "days"}
    (args.output / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": run["status"], "model": run["model"], "source_count": len(run["sources"]),
                      "tokens": run["usage"].get("total_tokens"), "error": run["error"]}, ensure_ascii=False))
    return int(run["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
