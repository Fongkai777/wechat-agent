import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.message_pages import select_message_page, target_filter


class MessagePageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted", keys=root / "keys.json",
            media_root=root / "msg", voice_cache=root / "voice.json", llm_config=root / "config.json",
            qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl", qa_search_db=root / "search.db",
        )
        (self.state.decrypted / "message").mkdir(parents=True)
        self.rec = {"id": "friend", "chat": "friend", "type": "private", "shards": [], "total_messages": 0}

    def make_shard(self, number, timestamps):
        db = f"message/message_{number}.db"
        with sqlite3.connect(self.state.decrypted / db) as conn:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            conn.execute("INSERT INTO Name2Id VALUES ('friend')")
            conn.execute("CREATE TABLE Msg_test(local_id INTEGER, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)")
            conn.execute("CREATE INDEX msg_time ON Msg_test(create_time)")
            conn.executemany("INSERT INTO Msg_test VALUES (?, ?, 1, 1, ?, 'same content')",
                             [(i + 1, number * 100000 + i + 1, t) for i, t in enumerate(timestamps)])
        self.rec["shards"].append({"db": db, "table": "Msg_test", "count": len(timestamps)})
        self.rec["total_messages"] += len(timestamps)
        return self.state.decrypted / db

    def page(self, **kwargs):
        return web.collect_message_page(self.state, self.rec, **kwargs)

    def test_large_chat_decodes_only_one_page_and_skips_media_for_text(self):
        self.make_shard(0, list(range(10000)))
        with patch.object(web, "decode_content", wraps=web.decode_content) as decode, \
                patch.object(web, "build_resource_map") as resources, patch.object(web, "build_voice_map") as voices:
            page = self.page()
        self.assertEqual(decode.call_count, 100)
        resources.assert_not_called()
        voices.assert_not_called()
        self.assertEqual([msg["timestamp"] for msg in page["messages"]], list(range(9900, 10000)))
        self.assertEqual(page["total"], 10000)
        self.assertTrue(page["has_more_before"])
        self.assertFalse(page["has_more_after"])

    def test_cross_shard_same_second_pages_never_repeat_or_skip_real_messages(self):
        for n in range(3):
            self.make_shard(n, [100 + i // 7 for i in range(103)])
        expected = self.page(limit=200)
        before, found = "", []
        while True:
            page = self.page(limit=17, before=before)
            found[0:0] = page["messages"]
            if not page["has_more_before"]:
                break
            before = page["before_cursor"]
        self.assertEqual(len(found), 309)
        self.assertEqual(len({msg["id"] for msg in found}), 309)
        self.assertEqual([msg["timestamp"] for msg in found], sorted(msg["timestamp"] for msg in found))
        self.assertEqual([msg["id"] for msg in found[-200:]], [msg["id"] for msg in expected["messages"]])
        after, forwards = found[0]["id"], [found[0]["id"]]
        while True:
            page = self.page(limit=19, after=after)
            forwards.extend(msg["id"] for msg in page["messages"])
            if not page["has_more_after"]:
                break
            after = page["after_cursor"]
        self.assertEqual(forwards, [msg["id"] for msg in found])

    def test_insertions_and_deleted_cursor_do_not_shift_older_pages(self):
        path = self.make_shard(0, list(range(300)))
        first = self.page()
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM Msg_test WHERE create_time=200")
            conn.execute("INSERT INTO Msg_test VALUES (301, 301, 1, 1, 300, 'new')")
        older = self.page(before=first["before_cursor"])
        self.assertEqual([m["timestamp"] for m in older["messages"]], list(range(100, 200)))
        newer = self.page(after=first["after_cursor"])
        self.assertEqual([m["content"] for m in newer["messages"]], ["new"])

    def test_empty_delta_preserves_no_phantom_messages(self):
        self.make_shard(0, [100])
        page = self.page(after=self.page()["after_cursor"])
        self.assertEqual(page["messages"], [])
        self.assertFalse(page["has_more_after"])

    def test_same_count_edit_is_not_hidden_by_whole_chat_cache(self):
        path = self.make_shard(0, [100])
        first = self.page()["messages"][0]
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE Msg_test SET message_content='edited'")
        changed = self.page()["messages"][0]
        self.assertEqual(first["id"], changed["id"])
        self.assertEqual(changed["content"], "edited")

    def test_since_filter_and_read_only_source(self):
        path = self.make_shard(0, list(range(200)))
        original = path.read_bytes()
        self.state.since_ts = 175
        self.assertEqual(len(self.page()["messages"]), 25)
        self.assertEqual(path.read_bytes(), original)

    def test_bad_and_cross_chat_cursors_are_rejected(self):
        self.make_shard(0, [100])
        cursor = self.page()["before_cursor"]
        for args in ({"before": "broken!"}, {"before": cursor, "after": cursor}, {"limit": 201}):
            with self.assertRaises(ValueError):
                self.page(**args)
        self.rec["id"] = "other"
        with self.assertRaises(ValueError):
            self.page(before=cursor)

    def test_empty_chat(self):
        self.make_shard(0, [])
        page = self.page()
        self.assertEqual(page["messages"], [])
        self.assertIsNone(page["before_cursor"])
        self.assertFalse(page["has_more_before"])

    def test_media_filters_stay_bounded_and_ignore_zero_server_id(self):
        clause, params = target_filter([{"create_time": 100, "local_id": 1, "server_id": 0}], ("t", "l", "s"))
        self.assertEqual(params, [100, 1])
        self.assertNotIn("s IN", clause)
        self.assertEqual(target_filter(None, ("t", "l", "s")), ("", []))
        self.assertEqual(target_filter([], ("t", "l", "s")), (" AND (0)", []))

    def test_voice_lookup_preserves_server_id_fallback_with_different_timestamp(self):
        self.make_shard(0, [100])
        media_path = self.state.decrypted / "message/media_0.db"
        with sqlite3.connect(media_path) as conn:
            conn.executescript("CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES ('friend'); "
                               "CREATE TABLE VoiceInfo(chat_name_id,create_time,local_id,svr_id,voice_data,data_index);")
            conn.executemany("INSERT INTO VoiceInfo VALUES (1,?,?,?,?,0)",
                             [(99, 12, 42, b"voice"), (50, 7, 8, b"unrelated")])
        targets = [{"create_time": 100, "local_id": 1, "server_id": 42}]
        voices = web.build_voice_map(self.state, self.rec, targets)
        self.assertEqual(web.match_voice_info(voices, 100, 1, 42)["local_id"], 12)
        self.assertNotIn(("local", 7), voices)

    def test_voice_cache_loaded_once_for_whole_page(self):
        path = self.make_shard(0, list(range(100)))
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE Msg_test SET local_type=34")
        with patch.object(web, "load_voice_cache", return_value={}) as cache, \
                patch.object(web, "build_voice_map", return_value={}):
            self.page()
        cache.assert_called_once()

    def test_resource_lookup_only_parses_page_matches(self):
        self.make_shard(0, [100])
        path = self.state.decrypted / "message/message_resource.db"
        with sqlite3.connect(path) as conn:
            conn.executescript("CREATE TABLE ChatName2Id(user_name TEXT); INSERT INTO ChatName2Id VALUES ('friend'); "
                               "CREATE TABLE MessageResourceInfo(chat_id,message_local_type,message_create_time,message_local_id,message_svr_id,packed_info);")
            conn.executemany("INSERT INTO MessageResourceInfo VALUES (1,3,?,?,?,?)",
                             [(99, 12, 42, b"0123456789abcdef0123456789abcdef"),
                              (50, 7, 8, b"11111111111111111111111111111111")])
        with patch.object(web, "extract_media_hashes", wraps=web.extract_media_hashes) as extract:
            resources = web.build_resource_map(self.state, self.rec, [{"create_time":100,"local_id":1,"server_id":42}])
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(resources[("server",42)]["local_id"], 12)

    def test_handler_uses_bounded_page_and_validates_legacy_offset(self):
        self.make_shard(0, list(range(300)))
        self.state.chats = [self.rec]
        with patch.object(web.threading.Thread, "start"), patch.object(web, "collect_messages_for_chat") as bulk:
            cls = web.make_handler(self.state)
            handler = cls.__new__(cls)
            handler.json_response = Mock()
            handler.handle_messages({"chat": ["friend"]})
            self.assertEqual(len(handler.json_response.call_args.args[0]["messages"]), 100)
            handler.handle_messages({"chat": ["friend"], "offset": ["10"]})
            self.assertEqual(handler.json_response.call_args.kwargs["status"], 400)
            bulk.assert_not_called()

    def test_preview_selects_last_row_across_shards_without_loading_media(self):
        self.make_shard(0, list(range(10000)))
        newest = self.make_shard(2, [10001, 10001])
        with sqlite3.connect(newest) as conn:
            conn.execute("UPDATE Msg_test SET local_type=47 WHERE rowid=2")
        with patch.object(web, "select_message_page", wraps=select_message_page) as select, \
                patch.object(web, "collect_messages_for_chat") as bulk, \
                patch.object(web, "resolve_sticker_media") as media, \
                patch.object(web, "decode_content") as decode:
            self.assertEqual(web.latest_message_preview(self.state, self.rec), "[表情包]")
        self.assertEqual(select.call_args.kwargs["limit"], 1)
        bulk.assert_not_called()
        media.assert_not_called()
        decode.assert_not_called()

    def test_previews_for_media_links_system_messages_and_group_text(self):
        path = self.make_shard(0, [100])
        cases = [
            (3, "<msg><img/></msg>", "[图片]"), (43, "<msg><video/></msg>", "[视频]"),
            (34, "<msg><voicemsg/></msg>", "[语音]"), (47, "<msg><emoji/></msg>", "[表情包]"),
            (49, "<msg><appmsg><type>6</type><title>report.pdf</title></appmsg></msg>", "[文件] report.pdf"),
            (49, "<msg><appmsg><type>5</type><title>Example</title><url>https://example.com</url></appmsg></msg>", "[外部链接] Example"),
            (10000, "Joined the group", "Joined the group"),
            (1, "friend:\nhello\nworld", "hello world"),
        ]
        self.rec["type"] = "group"
        for kind, content, expected in cases:
            with self.subTest(kind=kind, content=content):
                with sqlite3.connect(path) as conn:
                    conn.execute("UPDATE Msg_test SET local_type=?,message_content=?", (kind, content))
                self.assertEqual(web.latest_message_preview(self.state, self.rec), expected)

    def test_preview_does_not_use_messages_outside_date_filter(self):
        self.make_shard(0, [100])
        self.state.since_ts = 101
        self.assertEqual(web.latest_message_preview(self.state, self.rec), "[暂无预览]")

    def test_chat_index_fills_only_missing_previews_and_updates_after_sync(self):
        import hashlib
        path = self.make_shard(0, [100])
        username = "friend"
        table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
        with sqlite3.connect(path) as conn:
            conn.execute(f"ALTER TABLE Msg_test RENAME TO {table}")
            conn.execute(f"UPDATE {table} SET local_type=47")
        session = {username:{"username":username,"display_name":"Friend","summary":""}}
        with patch.object(web, "load_sessions", return_value=session), \
                patch.object(web, "latest_message_preview", wraps=web.latest_message_preview) as preview:
            self.assertEqual(web.build_chat_index(self.state)[0]["summary"], "[表情包]")
            with sqlite3.connect(path) as conn:
                conn.execute(f"INSERT INTO {table} VALUES (2,2,1,1,101,'new text')")
            self.assertEqual(web.build_chat_index(self.state)[0]["summary"], "new text")
            session[username]["summary"] = " \n "
            self.assertEqual(web.build_chat_index(self.state)[0]["summary"], "new text")
            session[username]["summary"] = "Keep existing preview"
            preview.reset_mock()
            self.assertEqual(web.build_chat_index(self.state)[0]["summary"], "Keep existing preview")
            preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
