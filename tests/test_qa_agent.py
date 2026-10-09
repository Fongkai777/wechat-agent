import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wechat_agent.qa_planning import validate_plan, make_plan, calendar_window, PLAN_FORMAT
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_tools import ConversationTools
from wechat_agent import web


NOW = datetime(2026, 9, 30, 16, 30, tzinfo=timezone(timedelta(hours=8)))
PEOPLE = [{"id": "alice", "name": "小林", "aliases": ["小林"]}, {"id": "bob", "name": "小王", "aliases": ["小王"]}]


def raw(**changes):
    return dict({"query": "小林今天下午对实习的态度", "intent": "analysis", "people": [{"name": "小林", "user_turn": 0}],
                 "time_expression": "今天下午", "time_user_turn": 1, "since": "2025-08-25T00:00:00", "until": "2025-08-25T23:59:59",
                 "need_context": True, "background_query": "", "clarification": ""}, **changes)


def completion(data):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data, ensure_ascii=False)}}]}


class PlanningTests(unittest.TestCase):
    def test_pronoun_and_today_do_not_follow_wrong_model_dates(self):
        p = validate_plan(raw(), ["小林想找实习吗", "她今天下午什么态度"], PEOPLE, {}, NOW)
        self.assertEqual(p["people"][0]["id"], "alice")
        self.assertEqual(p["since_ts"], int(NOW.replace(hour=12, minute=0).timestamp()))
        self.assertEqual(p["until_ts"], int(NOW.timestamp()))

    def test_same_nickname_requires_clarification(self):
        p = validate_plan(raw(), ["小林想找实习吗", "她今天下午什么态度"], PEOPLE + [{"id": "other", "name": "小林"}], {}, NOW)
        self.assertTrue(p["clarification"])
        self.assertFalse(p["people"])

    def test_person_cannot_be_invented_from_assistant_answer(self):
        p = validate_plan(raw(people=[{"name": "小王", "user_turn": 0}]), ["小林想找实习吗", "她今天下午什么态度"], PEOPLE, {}, NOW)
        self.assertTrue(p["clarification"])

    def test_explicit_new_person_overrides_previous(self):
        p = validate_plan(raw(people=[{"name": "小王", "user_turn": 1}]), ["小林想找实习吗", "小王今天下午说什么"], PEOPLE, {}, NOW)
        self.assertEqual(p["people"][0]["id"], "bob")
        wrong = validate_plan(raw(), ["小林想找实习吗", "小王今天下午说什么"], PEOPLE, {}, NOW)
        self.assertTrue(wrong["clarification"])

    def test_location_query_is_global_and_no_invented_time(self):
        p = validate_plan(raw(people=[], query="新加坡实习", intent="search", time_expression="", since="", until=""), ["新加坡有哪些实习"], PEOPLE, {}, NOW)
        self.assertEqual(p["people"], [])
        self.assertIsNone(p["since_ts"])
        with self.assertRaises(ValueError):
            validate_plan(raw(people=[], time_expression=""), ["实习信息"], PEOPLE, {}, NOW)

    def test_today_is_recomputed_across_days(self):
        self.assertNotEqual(calendar_window("今天下午", NOW)[0], calendar_window("今天下午", NOW + timedelta(days=1))[0])

    def test_yesterday_has_upper_bound(self):
        start, end = calendar_window("昨天下午", NOW)
        self.assertEqual((start.day, start.hour, end.hour, end.minute, end.second), (29, 12, 17, 59, 59))

    def test_planner_receives_users_not_untrusted_assistant_guesses(self):
        complete = Mock(return_value=completion(raw()))
        p = make_plan("她今天下午什么态度", [{"role": "user", "content": "小林想找实习吗"},
                                            {"role": "assistant", "content": "UNTRUSTED_GUESS"}], PEOPLE, NOW, complete)
        self.assertNotIn("UNTRUSTED_GUESS", str(complete.call_args))
        self.assertEqual(p["people"][0]["id"], "alice")


class ToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "messages.db"
        self.state = SimpleNamespace(qa_search_db=path, since_ts=None, account="me")
        self.conn = sqlite3.connect(path); self.addCleanup(self.conn.close)
        self.conn.executescript("""CREATE TABLE messages(id INTEGER PRIMARY KEY, source_db TEXT, source_table TEXT,
          local_id TEXT, server_id TEXT, chat_id TEXT, chat_title TEXT, chat_type TEXT, person_id TEXT,
          person_name TEXT, sender TEXT, sender_username TEXT, timestamp INTEGER, time TEXT, type TEXT, text TEXT, search_text TEXT);
          CREATE TABLE semantic_message_map(chunk_id INTEGER, message_id INTEGER);""")
        self.plan = validate_plan(raw(), ["小林想找实习吗", "她今天下午什么态度"], PEOPLE, {}, NOW)
        self.hybrid = Mock(return_value=([], {}))
        self.tools = ConversationTools(self.state, self.hybrid, web.qa_item_from_search_row, lambda: None)

    def insert(self, id, person="alice", hour=13, days=0, chat="group@chatroom"):
        dt = NOW.replace(hour=hour) - timedelta(days=days)
        self.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            id, "message/message_0.db", "Msg", str(id), str(id), chat, chat, "group" if "@" in chat else "private",
            person, person, person, person, int(dt.timestamp()), dt.isoformat(), "text", "internship", "internship"))
        self.conn.commit()

    def test_time_and_person_filters_and_pagination(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400); self.insert(4, hour=10); self.insert(5, hour=15)
        first = self.tools.timeline(self.plan, size=1)
        self.assertEqual(first["matched"], 2)
        self.assertFalse(first["complete"])
        second = self.tools.timeline(self.plan, first["next_offset"], 1)
        self.assertNotEqual(first["items"][0]["evidence_id"], second["items"][0]["evidence_id"])
        self.assertTrue(second["complete"])

    def test_context_preserves_other_senders_without_relabeling(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, hour=10)
        seeds = self.tools.timeline(self.plan)["items"]
        result = self.tools.surrounding(self.plan, seeds)
        self.assertEqual([i["sender"] for i in result["items"]], ["bob"])
        self.assertEqual(result["items"][0]["evidence_role"], "surrounding")

    def test_semantic_chunk_is_rehydrated_and_clipped(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400)
        self.conn.executemany("INSERT INTO semantic_message_map VALUES(1, ?)", [(1,), (2,), (3,)]); self.conn.commit()
        self.hybrid.return_value = ([{"semantic_chunk_id": 1}], {})
        result = self.tools.search(self.plan)
        self.assertEqual([i["local_id"] for i in result["items"]], ["1"])

    def test_empty_window_does_not_fall_back_to_history(self):
        self.insert(1, days=400)
        self.assertEqual(self.tools.timeline(self.plan)["items"], [])

    def test_statistics_counts_all_rows_not_top_k(self):
        for i in range(8): self.insert(i)
        result = self.tools.statistics(self.plan)
        self.assertEqual(json.loads(result["items"][0]["text"])["messages"], 8)

    def test_unanswered_excludes_conversations_already_replied(self):
        self.insert(1, chat="alice")
        self.insert(2, person="me", hour=14, chat="alice")
        self.insert(3, person="bob", chat="bob")
        result = self.tools.unanswered(dict(self.plan, people=[]))
        self.assertEqual([x["chat_id"] for x in result["items"]], ["bob"])

    def test_graph_saves_plan_trace_and_only_current_evidence(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400)
        complete = Mock(side_effect=[completion(raw()), completion({"action": "finish", "query": ""})])
        answer = Mock(return_value={"paragraphs": [{"kind": "answer", "text": "在讨论实习", "source_refs": [1]}]})
        checkpoint = Mock()
        result, sources, diag = run_conversation_agent("她今天下午什么态度", [{"role": "user", "content": "小林想找实习吗"}],
            PEOPLE, self.tools, complete, answer, Mock(), checkpoint, lambda: None, now=NOW)
        self.assertEqual(diag["conversation_state"]["people"][0]["id"], "alice")
        self.assertGreaterEqual(checkpoint.call_count, 3)
        self.assertEqual({s["local_id"] for s in sources}, {"1", "2"})
        self.assertNotIn("2025", answer.call_args.args[0])
        self.assertTrue(diag["trace"])

    def test_graph_no_evidence_never_calls_answer_or_hybrid(self):
        answer = Mock()
        result, sources, diag = run_conversation_agent("她今天下午什么态度", [{"role": "user", "content": "小林想找实习吗"}],
            PEOPLE, self.tools, Mock(return_value=completion(raw())), answer, Mock(), Mock(), lambda: None, now=NOW)
        self.assertFalse(sources); answer.assert_not_called(); self.hybrid.assert_not_called()
        self.assertEqual(result["paragraphs"][0]["kind"], "limitation")


if __name__ == "__main__":
    unittest.main()
