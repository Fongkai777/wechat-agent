import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_agent import web
from wechat_agent.cli import load_contacts, load_sessions


class GroupTitleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted", keys=root / "keys.json",
            media_root=root / "msg", voice_cache=root / "voice.json", llm_config=root / "config.json",
            qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl", qa_search_db=root / "search.db",
            account="self",
        )
        self.contact_db = self.state.decrypted / "contact" / "contact.db"
        self.session_db = self.state.decrypted / "session" / "session.db"
        self.contact_db.parent.mkdir(parents=True)
        self.session_db.parent.mkdir(parents=True)
        self.group = "1234567890@chatroom"
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("CREATE TABLE contact(username TEXT, remark TEXT, nick_name TEXT, alias TEXT)")
            conn.execute("CREATE TABLE name2id(username TEXT)")
            conn.execute("CREATE TABLE chatroom_member(room_id INTEGER, member_id INTEGER)")
            conn.executemany("INSERT INTO name2id(rowid,username) VALUES (?,?)",
                             [(10,"liu"),(20,"li"),(30,"self"),(100,self.group)])
            conn.executemany("INSERT INTO contact VALUES (?,?,?,?)", [
                ("liu","示例甲","Nickname Liu",""), ("li","示例乙","Nickname Li",""),
                ("self","","Self Nickname",""), (self.group,"","",""),
            ])
            conn.executemany("INSERT INTO chatroom_member VALUES (100,?)", [(10,),(20,),(30,)])
        with sqlite3.connect(self.session_db) as conn:
            conn.execute("CREATE TABLE SessionTable(username TEXT, summary TEXT)")
            conn.execute("INSERT INTO SessionTable VALUES (?, 'preview')", (self.group,))
            conn.execute("CREATE TABLE SessionNoContactInfoTable(username TEXT, session_title TEXT)")

    def titles(self):
        contacts = load_contacts(self.state.decrypted)
        sessions = load_sessions(self.state.decrypted, contacts)
        return web.unnamed_group_titles(self.state.decrypted, contacts, sessions, self.state.account)

    def test_three_member_group_excludes_self_but_counts_everyone(self):
        self.assertEqual(self.titles()[self.group], "示例甲、示例乙（3）")

    def test_named_groups_and_private_contacts_keep_their_existing_titles(self):
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET remark='Group Remark',nick_name='Group Name' WHERE username=?", (self.group,))
        self.assertEqual(self.titles(), {})
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET remark='' WHERE username=?", (self.group,))
        self.assertEqual(self.titles(), {})
        self.assertEqual(web.unnamed_group_titles(self.state.decrypted, {}, {"friend":{"display_name":"friend"}}, "self"), {})

    def test_whitespace_remark_uses_nickname_and_whitespace_group_name_is_missing(self):
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET remark='   ' WHERE username='liu'")
            conn.execute("UPDATE contact SET remark=' ',nick_name=' ' WHERE username=?", (self.group,))
        self.assertEqual(self.titles()[self.group], "Nickname Liu、示例乙（3）")

    def test_extra_members_add_ellipsis_and_duplicates_do_not_inflate_count(self):
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("INSERT INTO name2id(rowid,username) VALUES (40,'other')")
            conn.execute("INSERT INTO contact VALUES ('other','','Other','')")
            conn.executemany("INSERT INTO chatroom_member VALUES (100,?)", [(40,),(10,),(30,)])
        self.assertEqual(self.titles()[self.group], "示例甲、示例乙…（4）")

    def test_missing_member_names_do_not_leak_ids_or_invent_a_title(self):
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET remark='',nick_name='' WHERE username='liu'")
            conn.execute("DELETE FROM name2id WHERE rowid=20")
        self.assertEqual(self.titles()[self.group], "未命名群（3）")

    def test_missing_member_schema_leaves_other_names_usable(self):
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("DROP TABLE chatroom_member")
        self.assertEqual(self.titles()[self.group], "未命名群")
        self.assertEqual(load_contacts(self.state.decrypted)["liu"], "示例甲")

    def test_cached_session_title_takes_priority_over_member_fallback(self):
        with sqlite3.connect(self.session_db) as conn:
            conn.execute("INSERT INTO SessionNoContactInfoTable VALUES (?, 'Cached Group')", (self.group,))
        self.assertEqual(self.titles()[self.group], "Cached Group")

    def test_display_names_refresh_without_changing_chat_ids_or_source_data(self):
        path = self.state.decrypted / "message" / "message_0.db"
        path.parent.mkdir(parents=True)
        table = "Msg_" + hashlib.md5(self.group.encode()).hexdigest()
        with sqlite3.connect(path) as conn:
            conn.execute(f"CREATE TABLE {table}(create_time INTEGER)")
            conn.execute(f"INSERT INTO {table} VALUES (100)")
        original = self.contact_db.read_bytes()
        first = web.build_chat_index(self.state)[0]
        self.assertEqual(first["title"], "示例甲、示例乙（3）")
        self.assertEqual(self.state.contacts[self.group], first["title"])
        self.assertEqual(self.state.sessions[self.group]["display_name"], first["title"])
        self.assertEqual(self.contact_db.read_bytes(), original)
        self.assertEqual(first["id"], self.group)
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET remark='New Remark' WHERE username='liu'")
        changed = web.build_chat_index(self.state)[0]
        self.assertEqual(changed["title"], "New Remark、示例乙（3）")
        self.assertEqual(changed["id"], first["id"])
        with sqlite3.connect(self.contact_db) as conn:
            conn.execute("UPDATE contact SET nick_name='正式群名' WHERE username=?", (self.group,))
        self.assertEqual(web.build_chat_index(self.state)[0]["title"], "正式群名")

    def test_existing_retrieval_rows_use_the_display_name_without_rebuilding_or_mutating_the_index(self):
        self.state.contacts[self.group] = "示例甲、示例乙（3）"
        original = {"chat_id": self.group, "chat_title": self.group, "text": "message"}
        with patch.object(web, "search_qa_search_db", return_value=([original], {})):
            items, _ = web.select_qa_context_with_diagnostics(self.state, {}, "test", 10)
        self.assertEqual(items[0]["chat_title"], self.state.contacts[self.group])
        self.assertEqual(items[0]["chat_id"], self.group)
        self.assertEqual(original["chat_title"], self.group)
        private = {"chat_id": "friend", "chat_title": "Friend"}
        self.assertEqual(web.with_current_group_titles(self.state, [private]), [private])


if __name__ == "__main__":
    unittest.main()
