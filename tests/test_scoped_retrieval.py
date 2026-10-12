"""Scope must constrain recall, not merely filter global top-k results."""
import copy
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_qa_agent
from wechat_agent import web


class ScopedRecallTests(unittest.TestCase):
    setUp = test_qa_agent.ToolTests.setUp
    insert = test_qa_agent.ToolTests.insert

    def populate(self):
        self.insert(1,chat='target@chatroom')
        for i in range(2,27): self.insert(i,chat='noise@chatroom',hour=15)
        self.conn.execute('CREATE VIRTUAL TABLE messages_fts USING fts5(search_text)')
        self.conn.execute('INSERT INTO messages_fts(rowid,search_text) SELECT id,search_text FROM messages')
        self.conn.commit()
        return dict(self.plan,people=[],chat_ids=['target@chatroom'],chat_scope={'type':'group','keyword':''})

    def test_lexical_scope_applies_before_top_k_for_ids_kind_and_title(self):
        plan=self.populate()
        for query in (web.query_qa_search_fts,web.query_qa_search_like):
            global_rows=query(self.conn,'internship',[],None,20)
            self.assertNotIn('target@chatroom',{r[5] for r in global_rows})
            self.conn.row_factory=sqlite3.Row
            for scope in (plan,dict(plan,chat_ids=[],chat_scope={'type':'group','keyword':'target'})):
                rows=query(self.conn,'internship',[],None,20,scope=scope)
                self.assertEqual([r['id'] for r in rows],[1])

    def test_end_to_end_hybrid_scopes_before_rerank(self):
        plan=self.populate()
        self.state.llm_config=self.state.qa_search_db.parent/'models.json'
        def hybrid(q,people,since,until,size,scope=None):
            return web.search_qa_search_db(self.state,q,people,since,size,
                {**scope,'verified_people':True,'time_until':until,'queries':[{'text':q}]})
        self.tools.hybrid=hybrid
        def rerank(state,q,items,limit):
            self.assertEqual({r['chat_id'] for r in items},{'target@chatroom'})
            return items[:limit],{}
        with patch.object(web,'qa_search_db_status',return_value={'ready':True,'usable':True}), \
             patch.object(web,'query_qa_semantic_index',return_value=([],{})), \
             patch.object(web,'rerank_qa_items_with_config',side_effect=rerank):
            for limit in (20,60):
                result=self.tools.search(plan,'internship',limit)
                self.assertEqual([r['chat_id'] for r in result['items']],['target@chatroom'])

    def test_semantic_scope_matches_same_message_before_vector_ranking(self):
        plan=self.populate()
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('CREATE TABLE semantic_chunks(id INTEGER PRIMARY KEY, embedding BLOB)')
        for i in range(1,27):
            self.conn.execute('INSERT INTO semantic_chunks VALUES (?,?)',(i,web.pack_float_vector([1,0])))
            self.conn.execute('INSERT INTO semantic_message_map VALUES (?,?)',(i,i))
        self.conn.commit()
        rows=web.select_semantic_candidate_rows(self.conn,[],None,scope=plan)
        self.assertEqual([r['id'] for r in rows],[1])
        # A target's sender may differ from person_id; group filtering uses sender_username.
        self.conn.execute("UPDATE messages SET person_id='someone-else' WHERE id=1")
        self.assertEqual(len(web.select_semantic_candidate_rows(self.conn,[{'id':'alice'}],None,scope=plan)),1)
        self.assertEqual(web.select_semantic_candidate_rows(self.conn,[{'id':'bob'}],None,scope=plan),[])
        self.assertEqual(web.select_semantic_candidate_rows(self.conn,[],self.plan['until_ts']+1,scope=plan),[])

    def test_vector_top_twenty_cannot_be_filled_by_other_groups(self):
        plan=self.populate()
        self.conn.execute('CREATE TABLE semantic_chunks(id INTEGER PRIMARY KEY, end_ts INTEGER, embedding BLOB)')
        for i in range(1,27):
            # Distractors have higher cosine similarity than the single target.
            self.conn.execute('INSERT INTO semantic_chunks VALUES (?,?,?)',
                (i,i,web.pack_float_vector([1,1] if i==1 else [1,0])))
            self.conn.execute('INSERT INTO semantic_message_map VALUES (?,?)',(i,i))
        self.conn.commit()
        self.state.llm_config=self.state.qa_search_db.parent/'models.json'
        config=copy.deepcopy(web.DEFAULT_LLM_CONFIG)
        config['embedding']['enabled']=True
        with patch.object(web,'load_llm_config',return_value=config), \
             patch.object(web,'qa_embedding_index_status',return_value={'ready':True}), \
             patch.object(web,'resolve_llm_api_key',return_value='synthetic-not-a-key'), \
             patch.object(web,'qa_item_from_semantic_row',side_effect=lambda r,*args: {'id':r['id']}):
            args=(self.state,'internship',[],None,20)
            global_hits,_=web.query_qa_semantic_index(*args,query_embedding={'vectors':[[1,0]]})
            self.assertNotIn(1,[r['id'] for r in global_hits])
            hits,_=web.query_qa_semantic_index(*args,plan=plan,query_embedding={'vectors':[[1,0]]})
            self.assertEqual([r['id'] for r in hits],[1])

    def test_index_floor_is_applied_before_recall(self):
        self.state.since_ts=self.plan['since_ts']+1
        self.tools.search(self.plan)
        self.assertEqual(self.hybrid.call_args.args[2],self.state.since_ts)


if __name__=='__main__':
    import unittest
    unittest.main()
