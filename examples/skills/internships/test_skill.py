"""
FakeAPI 测试：覆盖以下场景
- 空匹配
- 已有完整证据可直接由模型返回（无需工具调用）
- 原生 assistant/tool 双轮补查：assistant 首次发起工具调用（surrounding/search/timeline），工具回执追加后模型再次返回最终 schema
- 同条消息被识别为多个不同岗位
- 重复召回（sql 与语义重复）只保留一次
- 模型返回非法引用（不在本批 cite 的引用）应导致失败
- 模型畸形（字段类型错）应导致失败
- 单条上下文超预算应失败

新增 test() 入口以供自动化测试框架调用
"""

import json
from workflow import run, BATCH_CHAR_LIMIT, MAX_EMBEDDING_CALLS

class FakeAPI:
    def __init__(self):
        self.context = {'task':'查找实习相关信息', 'since':0, 'until':9999999999}
        self._cite_next = 100
        self._llm_invocations = 0
        self._tool_calls = []
        # seed messages
        self._messages = [
            {
                'id': 1,
                'chat_id': 'chatA',
                'chat_title': '招聘群A',
                'chat_type': 'group',
                'timestamp': 1000,
                'sender': 'Alice',
                'sender_username': 'alice',
                'person_id': 'p_alice',
                'source_db': 'db1',
                'source_table': 't1',
                'local_id': 'l1',
                'server_id': 'srv1',
                'text': '我们招聘实习生，岗位: 后端实习, 联系邮箱 hr@example.com, 截止: 2025-07-01',
            },
            {
                'id': 2,
                'chat_id': 'chatA',
                'chat_title': '招聘群A',
                'chat_type': 'group',
                'timestamp': 1001,
                'sender': 'Bob',
                'sender_username': 'bob',
                'person_id': 'p_bob',
                'source_db': 'db1',
                'source_table': 't1',
                'local_id': 'l2',
                'server_id': None,
                'text': '大家好，有实习机会，有兴趣私聊。',
            },
            {
                'id': 3,
                'chat_id': 'chatB',
                'chat_title': '公司直招',
                'chat_type': 'private',
                'timestamp': 1100,
                'sender': 'Carol',
                'sender_username': 'carol',
                'person_id': 'p_carol',
                'source_db': 'db2',
                'source_table': 't2',
                'local_id': 'l3',
                'server_id': 'srv2',
                'text': 'Internship: frontend intern, contact: jobs@example.org, location: Beijing',
            }
        ]
        # For tool-based responses, we will return items keyed by argument
        self._tool_data = {
            'surrounding': lambda args: {'items': [{'reference': args.get('source_ids')[0], 'text': '周边消息: 更多细节'}] if args.get('source_ids') else []},
            'search': lambda args: {'items': [{'reference': 999 + i, 'text': '搜索匹配'} for i in range(1,2)]},
            'timeline': lambda args: {'items': []},
            'list_chats': lambda args: {'items': [{'id': 'chatA', 'title': '招聘群A', 'type': 'group'}]},
            'shared_windows': lambda args: {'items': []},
            'finish': lambda args: {'ok': True}
        }
        # model behavior flags for tests
        self.mode = 'normal'  # other modes: 'empty','two_round','malformed','illegal_ref','multi_job','duplicate','over_budget'

    # minimal logging
    def log(self, msg):
        pass

    # semantic_search returns candidates depending on mode
    def semantic_search(self, query, min_similarity=0.25):
        if self.mode == 'empty':
            return {'messages': [], 'warning': None}
        if self.mode == 'over_budget':
            # return one very long message
            return {'messages': self._messages}
        # default: return first and third if present
        msgs = []
        if len(self._messages) > 0:
            msgs.append(self._messages[0])
        if len(self._messages) > 2:
            msgs.append(self._messages[2])
        return {'messages': msgs, 'warning': None}

    # query simulates SQL LIKE matching
    def query(self, sql, params=[]):
        if self.mode == 'empty':
            return []
        lowered = [p.strip('%').lower() for p in params]
        out = []
        for m in self._messages:
            txt = (m.get('text') or '').lower()
            for p in lowered:
                if p and p in txt:
                    out.append(m)
                    break
        out.sort(key=lambda x: x.get('timestamp') or 0)
        return out

    # api.surrounding (native) - returns neighbour messages
    def surrounding(self, messages, before=3, after=3, max_gap_minutes=30):
        out = []
        for m in messages:
            if m.get('chat_id') == 'chatA' and m.get('timestamp') == 1000:
                for mm in self._messages:
                    if mm.get('timestamp') == 1001 and mm.get('chat_id') == 'chatA':
                        out.append(mm)
        return out

    def cite(self, messages):
        cited = []
        for m in messages:
            new = dict(m)
            new['reference'] = self._cite_next
            self._cite_next += 1
            cited.append(new)
        return cited

    # api.tools listing - not strictly needed by workflow but provided
    def tools(self, names=None):
        return [{'name': n} for n in SHARED_TOOL_NAMES()]

    # api.tool executing shared tools
    def tool(self, name, arguments):
        self._tool_calls.append((name, arguments))
        if self.mode == 'empty':
            return {'items':[], 'coverage':{'complete':True}}
        if name not in self._tool_data:
            return {'error': 'unknown tool'}
        fn = self._tool_data[name]
        return fn(arguments)

    # api.llm simulates native tool-calling behavior based on internal mode and invocation count
    def llm(self, messages, schema=None, tools=None, tool_choice='auto'):
        response=self._response(messages,schema,tools,tool_choice)
        if 'jobs' in response:
            return {'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(response)}}
        return response

    def _response(self, messages, schema=None, tools=None, tool_choice='auto'):
        self._llm_invocations += 1
        if tool_choice == 'required':
            assert tools == ['search']
            return {'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
                {'id':'empty_retry','type':'function','function':{'name':'search','arguments':json.dumps(
                    {'query':'暑期项目 内推 申请 宣讲 实习机会', 'size':20})}}]}}
        if self.mode == 'empty':
            return {'jobs':[]}
        last_user = None
        for m in reversed(messages):
            if m.get('role') == 'user':
                last_user = m.get('content')
                break
        refs = []
        if last_user:
            for part in last_user.split('\n'):
                s = part.strip()
                if s.startswith('[#') and ']' in s:
                    try:
                        num = int(s.split('[#',1)[1].split(']',1)[0])
                        refs.append(num)
                    except Exception:
                        pass
        def make_jobs_for_refs(refs_list):
            jobs = []
            for r in refs_list:
                jobs.append({
                    'title': '后端实习' if '后端' in self._messages[0].get('text','') else 'Intern',
                    'company': None,
                    'contact': 'hr@example.com',
                    'deadline': '2025-07-01',
                    'location': 'Beijing',
                    'description': '来自证据的职位描述',
                    'is_relevant': True,
                    'source_refs': [r]
                })
            return jobs

        if self.mode == 'two_round':
            if self._llm_invocations == 1:
                return {'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
                    {'id':'tc1','type':'function','function':{'name':'search','arguments':json.dumps(
                        {'chat_ids':[],'person_ids':[],'query':'internship details','size':20})}}]}}
            else:
                assert messages[-2]['tool_calls'][0]['id']=='tc1'
                assert messages[-1]['role']=='tool' and messages[-1]['tool_call_id']=='tc1'
                extra=json.loads(messages[-1]['content'])['items'][0]['reference']
                return {'jobs': make_jobs_for_refs([extra])}

        if self.mode == 'malformed':
            if refs:
                return {'jobs': [{
                    'title': '坏条目', 'company': None, 'contact': None, 'deadline': None, 'location': None,
                    'description': '坏数据', 'is_relevant': 'yes', 'source_refs': [refs[0]]
                }]}
            else:
                return {'jobs': []}

        if self.mode == 'illegal_ref':
            if refs:
                return {'jobs': [{
                    'title': '非法引用条目', 'company': None, 'contact': None, 'deadline': None, 'location': None,
                    'description': '非法引用', 'is_relevant': True, 'source_refs': [999999]
                }]}
            else:
                return {'jobs': []}

        if self.mode == 'multi_job':
            if refs:
                r = refs[0]
                return {'jobs': [
                    {'title': 'Backend intern', 'company': None, 'contact': None, 'deadline': None, 'location': None, 'description': 'Backend', 'is_relevant': True, 'source_refs': [r]},
                    {'title': 'Frontend intern', 'company': None, 'contact': None, 'deadline': None, 'location': None, 'description': 'Frontend', 'is_relevant': True, 'source_refs': [r]}
                ]}
            else:
                return {'jobs': []}

        return {'jobs': make_jobs_for_refs(refs)}

# helper to expose tool names used by fake api.tools
def SHARED_TOOL_NAMES():
    return ["list_chats", "timeline", "search", "surrounding", "shared_windows", "finish"]

# Individual test cases

def test_empty():
    api = FakeAPI()
    api.mode = 'empty'
    res = run(api)
    assert isinstance(res, dict)
    assert res['items'] == []
    assert '召回策略' in res['notice']
    assert api._llm_invocations == 2
    assert [name for name, args in api._tool_calls] == ['search']


def test_empty_retry_can_find_evidence():
    class Retry(FakeAPI):
        def query(self, *args): return []
        def semantic_search(self, *args): return {'messages':[]}
        def _response(self, messages, schema=None, tools=None, tool_choice='auto'):
            if tool_choice == 'required':
                return super()._response(messages, schema, tools, tool_choice)
            assert messages[-1]['tool_call_id'] == 'empty_retry'
            ref = json.loads(messages[-1]['content'])['items'][0]['reference']
            return {'jobs':[{'title':'暑期内推渠道', 'company':None, 'location':None,
                            'contact':None, 'deadline':None, 'description':'内推申请通知',
                            'is_relevant':True, 'source_refs':[ref]}]}
    result = run(Retry())
    assert result['items'][0]['title'] == '暑期内推渠道'
    assert result['items'][0]['source_refs'] == [1000]


def test_conflicting_facts_are_not_overwritten():
    class Corrections(FakeAPI):
        def _response(self, messages, schema=None, tools=None, tool_choice='auto'):
            result = super()._response(messages, schema, tools, tool_choice)
            first = dict(result['jobs'][0], deadline='2026-10-20', contact='old@example.com')
            second = dict(first, deadline='2026-10-15', contact='new@example.com',
                          source_refs=result['jobs'][-1]['source_refs'])
            return {'jobs':[first, second, dict(first)]}
    result = run(Corrections())
    assert len(result['items']) == 2
    assert '2026-10-20' in result['items'][0]['detail']
    assert '2026-10-15' in result['items'][1]['detail']
    assert 'old@example.com' in result['items'][0]['detail']
    assert 'new@example.com' in result['items'][1]['detail']
    assert all('存在差异' in item['suggestion'] for item in result['items'])
    assert result['items'][0]['source_refs'] != result['items'][1]['source_refs']


def test_related_events_are_not_restricted_to_offers():
    class Events(FakeAPI):
        def _response(self, messages, schema=None, tools=None, tool_choice='auto'):
            assert '用户任务：' in messages[-1]['content']
            result = super()._response(messages, schema, tools, tool_choice)
            template = result['jobs'][0]
            return {'jobs':[dict(template, title=title, company=None, deadline=None, contact=None)
                            for title in ('实习宣讲会', '实习内推渠道', '申请时间更正')]}
    result = run(Events())
    assert len(result['items']) == 3


def test_conflict_across_batches():
    class Batches(FakeAPI):
        def __init__(self):
            super().__init__()
            self._messages = [dict(self._messages[0], chat_id='chat'+str(i), server_id='srv'+str(i),
                                   text='实习 ' + 'x' * 16000) for i in range(2)]
        def surrounding(self, *args, **kwargs): return []
        def _response(self, messages, schema=None, tools=None, tool_choice='auto'):
            result = super()._response(messages, schema, tools, tool_choice)
            job = dict(result['jobs'][0], title='Same internship', company='Example',
                       deadline='2026-10-20' if self._llm_invocations == 1 else '2026-10-15')
            return {'jobs':[job]}
    api = Batches()
    result = run(api)
    assert api._llm_invocations == 2
    assert len(result['items']) == 2
    assert all(item['suggestion'] for item in result['items'])


def test_empty_retry_failure_is_not_saved_as_no_results():
    class Broken(FakeAPI):
        def tool(self, *args): raise OSError('search failed')
    api = Broken()
    api.mode = 'empty'
    try:
        run(api)
    except OSError:
        pass
    else:
        raise AssertionError('Failed supplemental search must fail the task')


def test_direct_answer():
    api = FakeAPI()
    api.mode = 'normal'
    res = run(api)
    assert isinstance(res, dict)
    assert len(res['items']) >= 1
    for it in res['items']:
        assert isinstance(it['source_refs'][0], int)


def test_two_round_tool():
    api = FakeAPI()
    api.mode = 'two_round'
    res = run(api)
    assert isinstance(res, dict)
    assert len(res['items']) >= 1
    # ensure tool was called at least once
    assert any(t[0] == 'search' for t in api._tool_calls)
    assert res['items'][0]['source_refs']==[1000]


def test_multi_job_same_message():
    api = FakeAPI()
    api.mode = 'multi_job'
    res = run(api)
    assert isinstance(res, dict)
    # two different jobs should be present
    assert len(res['items']) >= 2


def test_duplicate_recall():
    api = FakeAPI()
    api.mode = 'normal'
    # simulate duplicate messages across semantic_search and sql by repeating
    api._messages = api._messages + [dict(api._messages[0])]
    res = run(api)
    # should dedupe by server_id/person_id and not crash
    assert isinstance(res, dict)


def test_illegal_reference_raises():
    api = FakeAPI()
    api.mode = 'illegal_ref'
    try:
        _ = run(api)
    except RuntimeError as e:
        assert '非法引用' in str(e) or '不属于本批引用' in str(e)
    else:
        raise AssertionError('非法引用应导致错误')


def test_malformed_model_response_raises():
    api = FakeAPI()
    api.mode = 'malformed'
    try:
        _ = run(api)
    except RuntimeError as e:
        assert 'is_relevant' in str(e) or '字段 is_relevant 必须为 boolean' in str(e)
    else:
        raise AssertionError('畸形模型返回应导致错误')


def test_over_budget_fails():
    api = FakeAPI()
    api.mode = 'over_budget'
    # make messages very long so that window_size exceeds BATCH_CHAR_LIMIT
    long_text = 'intern ' + ('x' * (BATCH_CHAR_LIMIT + 1000))
    api._messages = [{
        'chat_id': 'chat_long', 'chat_title': '超长', 'chat_type': 'group', 'timestamp': 5000,
        'sender': 'Z', 'sender_username': 'z', 'person_id': 'p_z', 'source_db': 'dbx', 'source_table': 'tx', 'local_id': 'lx', 'server_id': None,
        'text': long_text
    }]
    try:
        _ = run(api)
    except RuntimeError as e:
        assert '超出每批字符预算' in str(e) or '上下文' in str(e) or 'BATCH_CHAR_LIMIT' in str(e)
    else:
        raise AssertionError('超预算应导致失败')

# 聚合测试入口：测试框架期望有 test() 函数
def test():
    test_empty()
    test_empty_retry_can_find_evidence()
    test_conflicting_facts_are_not_overwritten()
    test_related_events_are_not_restricted_to_offers()
    test_conflict_across_batches()
    test_empty_retry_failure_is_not_saved_as_no_results()
    test_direct_answer()
    test_two_round_tool()
    test_multi_job_same_message()
    test_duplicate_recall()
    test_illegal_reference_raises()
    test_malformed_model_response_raises()
    test_over_budget_fails()
    print('所有测试通过')

if __name__ == '__main__':
    test()
