"""Small synthetic retrieval benchmark. Generation/relevance reviews are separate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web


def evaluate(output, live_config=None, top_k=8):
    cases = json.loads(Path("eval/cases.json").read_text(encoding="utf-8"))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wechat-eval-") as directory:
        root = Path(directory)
        demo.initialize(root)
        state = demo.demo_state(root)
        web.update_qa_search_db_incremental(state)
        demo.import_messages(root, demo.fixture()["increment"])
        state = demo.demo_state(root)
        incremental = web.update_qa_search_db_incremental(state)
        if live_config:
            state.llm_config = Path(live_config).resolve()
        config = web.load_llm_config(state.llm_config)
        if live_config and config["embedding"].get("enabled"):
            web.build_or_update_qa_semantic_index(state)
        index = web.load_or_build_qa_index(state)
        results = []
        for case in cases:
            started = time.perf_counter()
            context, diagnostics = web.select_qa_context_with_diagnostics(state, index, case["question"], top_k)
            retrieved = [int(item["local_id"]) for item in context]
            expected = set(case["evidence"])
            expected_chats = {row[1] for row in demo.fixture()["messages"] + demo.fixture()["increment"] if row[0] in expected}
            item = {
                **case, "retrieved_message_ids": retrieved,
                "retrieved": [{key: row.get(key) for key in ("chat_id", "chat_title", "local_id", "sender", "text")} for row in context],
                "conversation_hit": bool(expected_chats & {row["chat_id"] for row in context}) if expected else None,
                "evidence_recall": len(expected & set(retrieved)) / len(expected) if expected else None,
                "all_evidence_found": expected.issubset(retrieved) if expected else None,
                "retrieval_ms": round((time.perf_counter() - started) * 1000, 2),
                "retrieval_mode": diagnostics.get("mode"),
                "answer": None, "citation_validity": "not_run", "answer_completeness": "not_reviewed",
                "manual_review": {"correct_context_cited": None, "answer_complete": None, "notes": ""},
            }
            if live_config:
                profile = config["qa"]
                key = web.resolve_llm_api_key(profile)
                if not key:
                    raise ValueError("Live evaluation requires a configured QA key.")
                response = web.call_chat_payload(profile, key, web.build_qa_messages(case["question"], context, case.get("history")), timeout=180)
                answer = response["choices"][0]["message"].get("content") or ""
                item["answer"] = answer
                item["usage"] = response.get("usage", {})
                refs = [int(number) for number in re.findall(r"\[(\d+)\]", answer)]
                item["citation_validity"] = "no_numeric_citations" if not refs else "in_range" if all(1 <= number <= len(context) for number in refs) else "out_of_range"
            results.append(item)
            print(f"{case['id']}: evidence_recall={item['evidence_recall']} mode={item['retrieval_mode']}", flush=True)
    positive = [item for item in results if not item.get("unanswerable")]
    report = {
        "dataset": "40 wholly fictional messages / 6 chats / 16 authored questions; not a production benchmark",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live-config" if live_config else "offline-local-only", "top_k": top_k,
        "incremental_inserted": incremental.get("inserted"),
        "conversation_hits": sum(item["conversation_hit"] for item in positive),
        "all_evidence_hits": sum(item["all_evidence_found"] for item in positive),
        "answerable_cases": len(positive), "results": results,
        "limits": ["Small authored synthetic dataset; not representative of private history.",
                   "Conversation hit is lenient; all-evidence hit requires every annotated message in top-k.",
                   "Citation range checks do not establish semantic support. Human review is required.",
                   "Multi-turn history is passed to generation, but current retrieval sees the latest question only."],
    }
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Retrieval Evaluation", "", f"Mode: **{report['mode']}**. Top-k: **{top_k}**.", "", report["dataset"], "",
             f"Correct-chat hit: **{report['conversation_hits']}/{len(positive)}**. All annotated evidence present: **{report['all_evidence_hits']}/{len(positive)}**.",
             f"Incremental import: **{report['incremental_inserted']}** new messages indexed; initial corpus 30 messages.", "",
             "| Case | Scenario | Correct chat | All evidence | Recall | Answer completeness |", "|---|---|---|---|---|---|"]
    for item in results:
        value = lambda v: "n/a" if v is None else "yes" if v else "no"
        recall = "n/a" if item["evidence_recall"] is None else f"{item['evidence_recall']:.0%}"
        lines.append(f"| {item['id']} | {item['category']} | {value(item['conversation_hit'])} | {value(item['all_evidence_found'])} | {recall} | not reviewed |")
    lines += ["", "## Interpretation", "", "These are retrieval measurements, not LLM answer-quality scores. Offline mode makes no model requests. Answers, citation support and completeness remain unevaluated until a live run and human review.", "", "## Failed Retrieval Cases", ""]
    for item in positive:
        if not item["all_evidence_found"]:
            lines.append(f"- **{item['id']}** ({item['question']}): missing annotated messages {sorted(set(item['evidence']) - set(item['retrieved_message_ids']))}.")
    lines += ["", "## Limits", ""] + [f"- {limit}" for limit in report["limits"]]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("eval/results/local"))
    parser.add_argument("--live-config", type=Path, help="Opt in to billable requests using ONLY synthetic messages; config is read, not copied")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 160:
        parser.error("top-k must be 1..160")
    evaluate(args.output, args.live_config, args.top_k)
