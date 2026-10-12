# -*- coding: utf-8 -*-
"""
本地测试覆盖：
- 空数据（无私聊）
- 自发（自己最后发言且顺序确定） -> 不入选
- 对方最后发言 -> 入选
- 未知身份（空 sender_username 或 纯数字） -> 入选并标注需人工确认
- order_uncertain -> 入选且标注需人工确认（即便为自己发送）
- 多会话覆盖
- 缺少 account 场景 -> 明确报错返回

使用 FakeAPI 模拟 api.chats / api.messages / api.cite / api.log。
"""

from workflow import run


class FakeAPI:
    def __init__(self, chats, messages_map, account='self'):
        self._chats = chats
        self._messages = messages_map
        # context must exist and include account unless testing missing-account
        self.context = {'task': 'test', 'since': 0, 'until': 9999999999, 'account': account, 'dry_run': False}
        self._cite_counter = 0
        self.messages_calls = []
        self.batch_calls = []

    def messages_batch(self, chat_ids, since=None, until=None, latest=1):
        self.batch_calls.append(list(chat_ids))
        assert len(chat_ids) <= 100
        return {chat: self.messages(chat, since, until, latest) for chat in chat_ids}

    def chats(self):
        return list(self._chats)

    def messages(self, chat_id, since=None, until=None, latest=None):
        # record call for assertions
        self.messages_calls.append({'chat_id': chat_id, 'since': since, 'until': until, 'latest': latest})
        msgs = self._messages.get(chat_id, [])
        if latest is None:
            return list(msgs)
        if latest <= 0:
            return []
        # return newest N messages in chronological order per API contract
        return list(msgs[-latest:])

    def cite(self, messages):
        # emulate platform behavior: attach increasing integer 'reference' to each returned original message
        cited = []
        for m in messages:
            self._cite_counter += 1
            # attach reference to the original dict (platform does this for verification)
            m['reference'] = self._cite_counter
            cited.append(m)
        return cited

    def log(self, message):
        # no-op for tests
        pass


def test():
    many = [{'id':str(i), 'title':'Synthetic', 'type':'private'} for i in range(1200)]
    rows = {chat['id']:[{'chat_id':chat['id'], 'sender_username':'other', 'text':'Hi'}] for chat in many}
    bulk = FakeAPI(many, rows)
    assert len(run(bulk)['items']) == 1200
    assert len(bulk.batch_calls) == 12

    class Incomplete(FakeAPI):
        def messages_batch(self, *args, **kwargs): return {}
    try:
        run(Incomplete(many, rows))
    except ValueError as exc:
        assert '缺失会话' in str(exc)
    else:
        raise AssertionError('Missing chats must not look like an empty result')

    # prepare chats: several private chats and a group
    chats = [
        {'id': 'c1', 'title': 'SelfChat', 'type': 'private', 'last_ts': 100},
        {'id': 'c2', 'title': 'Bob', 'type': 'private', 'last_ts': 101},
        {'id': 'c3', 'title': 'Empty', 'type': 'private', 'last_ts': 102},
        {'id': 'c4', 'title': 'NumericID', 'type': 'private', 'last_ts': 103},
        {'id': 'c5', 'title': 'UnknownEmpty', 'type': 'private', 'last_ts': 104},
        {'id': 'c6', 'title': 'OrderUncertain', 'type': 'private', 'last_ts': 105},
        {'id': 'g1', 'title': 'Group', 'type': 'group', 'last_ts': 106},
    ]

    # messages chronological per chat; sender != sender_username distinction
    messages_map = {
        # c1: last from self, order certain -> should NOT be included
        'c1': [
            {'chat_id': 'c1', 'chat_title': 'SelfChat', 'chat_type': 'private', 'timestamp': 10, 'time': '2021-01-01 10:00',
             'sender': 'Me', 'sender_username': 'self', 'mine': True, 'type': 'text', 'text': 'I wrote this.'}
        ],
        # c2: last from Bob (other) -> included
        'c2': [
            {'chat_id': 'c2', 'chat_title': 'Bob', 'chat_type': 'private', 'timestamp': 11, 'time': '2021-01-01 10:01',
             'sender': 'Bob Display', 'sender_username': 'bob_123', 'mine': False, 'type': 'text', 'text': 'Hi, are you there?'}
        ],
        # c3: empty -> nothing
        'c3': [],
        # c4: numeric-only sender_username -> unknown identity, included and ambiguous
        'c4': [
            {'chat_id': 'c4', 'chat_title': 'NumericID', 'chat_type': 'private', 'timestamp': 12, 'time': '2021-01-01 10:02',
             'sender': 'Sys', 'sender_username': '12345678', 'mine': False, 'type': 'notification', 'text': None}
        ],
        # c5: empty sender_username -> unknown identity
        'c5': [
            {'chat_id': 'c5', 'chat_title': 'UnknownEmpty', 'chat_type': 'private', 'timestamp': 13, 'time': '2021-01-01 10:03',
             'sender': 'Unknown', 'sender_username': '', 'mine': False, 'type': 'text', 'text': 'Ping'}
        ],
        # c6: order_uncertain True even though sender_username == account -> should be included and ambiguous
        'c6': [
            {'chat_id': 'c6', 'chat_title': 'OrderUncertain', 'chat_type': 'private', 'timestamp': 14, 'time': '2021-01-01 10:04',
             'sender': 'Me', 'sender_username': 'self', 'mine': True, 'type': 'text', 'text': 'Maybe last', 'order_uncertain': True}
        ],
        # group chat present but should be ignored
        'g1': [
            {'chat_id': 'g1', 'chat_title': 'Group', 'chat_type': 'group', 'timestamp': 15, 'time': '2021-01-01 10:05',
             'sender': 'Someone', 'sender_username': 'someone', 'mine': False, 'type': 'text', 'text': 'Group message'}
        ],
    }

    api = FakeAPI(chats=chats, messages_map=messages_map, account='self')
    result = run(api)

    assert isinstance(result, dict)
    assert 'summary' in result and 'items' in result and 'notice' in result

    items = result['items']
    # Expect c2 (Bob), c4 (NumericID), c5 (UnknownEmpty), c6 (OrderUncertain) -> total 4
    assert len(items) == 4, f"expected 4 items, got {len(items)}"

    titles = set(i['title'] for i in items)
    assert 'Bob' in titles and 'NumericID' in titles and 'UnknownEmpty' in titles and 'OrderUncertain' in titles

    # ambiguous ones should have suggestion text
    amb_titles = {'NumericID', 'UnknownEmpty', 'OrderUncertain'}
    for it in items:
        if it['title'] in amb_titles:
            assert it['suggestion'] is not None
            assert '人工确认' in it['suggestion'] or '确认' in it['suggestion']
        else:
            # Bob is a clear other-person last sender -> suggestion may be None
            assert it['suggestion'] is None

    # ensure messages() was called with latest=1 for each private chat
    private_chat_ids = [c['id'] for c in chats if c['type'] == 'private']
    called_ids = [c['chat_id'] for c in api.messages_calls]
    assert set(called_ids) == set(private_chat_ids)
    for call in api.messages_calls:
        assert call['latest'] == 1, f"messages called without latest=1 for {call['chat_id']}"

    # source_refs should be integers and >0
    for it in items:
        refs = it['source_refs']
        assert isinstance(refs, list) and len(refs) == 1
        assert isinstance(refs[0], int) and refs[0] > 0

    # No-match case: only one private chat and last message from self and order certain -> no items
    chats2 = [{'id': 'd1', 'title': 'Solo', 'type': 'private', 'last_ts': 200}]
    messages_map2 = {
        'd1': [
            {'chat_id': 'd1', 'chat_title': 'Solo', 'chat_type': 'private', 'timestamp': 20, 'time': '2021-01-02 09:00',
             'sender': 'Me', 'sender_username': 'self', 'mine': True, 'type': 'text', 'text': 'I sent this.'}
        ]
    }
    api2 = FakeAPI(chats=chats2, messages_map=messages_map2, account='self')
    result2 = run(api2)
    assert isinstance(result2, dict)
    assert result2['items'] == []

    # Missing account should return explicit error (summary starts with 错误)
    chats3 = [{'id': 'e1', 'title': 'Someone', 'type': 'private', 'last_ts': 300}]
    messages_map3 = {
        'e1': [
            {'chat_id': 'e1', 'chat_title': 'Someone', 'chat_type': 'private', 'timestamp': 30, 'time': '2021-01-03 08:00',
             'sender': 'Alice', 'sender_username': 'alice', 'mine': False, 'type': 'text', 'text': 'Hello'}
        ]
    }
    api3 = FakeAPI(chats=chats3, messages_map=messages_map3, account=None)
    # deliberately remove account to simulate missing-account; the FakeAPI sets context.account to None above
    def fails(api):
        try:
            run(api)
        except (ValueError,RuntimeError):
            return
        raise AssertionError('Failure must not become a successful empty result')
    fails(api3)
    for invalid in ('', '123', 123):
        fails(FakeAPI(chats3,messages_map3,account=invalid))
    def broken(*args, **kwargs):
        raise RuntimeError('unavailable')
    for method in ('chats','messages','cite'):
        api4=FakeAPI(chats3,messages_map3)
        setattr(api4,method,broken)
        fails(api4)
    api5=FakeAPI(chats3,messages_map3)
    api5.cite=lambda **kwargs: []
    fails(api5)

    print('All tests passed.')


if __name__ == '__main__':
    test()
