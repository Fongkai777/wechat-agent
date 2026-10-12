# -*- coding: utf-8 -*-
"""
检查任务时间窗内全部私聊的最后一条物理消息，判断是否可能“待回”。

设计要点（用户已确认）：
- 仅处理 chat.type == 'private' 的会话（任务时间窗由平台的 api.chats / api.messages 限定）。
- 分批调用 api.messages_batch(chat_ids, latest=1) 读取最后的物理消息；不要用全文索引或语义检索。
- 用 message['sender_username'] 与 api.context['account'] 精确比较：
  - 如果 sender_username == account 且消息没有 order_uncertain，则认定自己最后发言，跳过该会话（不列出）。
  - 如果 sender_username != account，则把该会话列出（对方发言）。
  - 如果 sender_username 为空或仅数字（无法证明为某个账号），视为未知身份并标注需人工确认。
  - 如果消息包含 order_uncertain == True，则视为顺序不确定，标注需人工确认；即便 sender_username == account 也要列出并标注。
- 保留原始消息字段（text、type、未渲染占位、order_uncertain 等），不要擅自删除表情、通知或通话元信息。
- 不调用 LLM、Embedding、index.query 或任何外部能力；全部本地判断。
- 如 api.context 缺失或无法识别当前账户（account 为空、缺失或 None），明确返回错误（避免把自己误判为对方）。
- 使用 api.cite(messages=...) 返回平台校验的引用编号作为每个 item 的 source_refs。

输出契约：返回 {summary, items, notice}，每个 item 包含 title, detail, suggestion (或 null), source_refs（整数引用）。

注意：该检查基于每个会话的最后一条物理消息，仅供参考，可能存在误判/漏判，请人工复核是否需要回复。
"""

from typing import List, Tuple


def _is_numeric_only(s) -> bool:
    return isinstance(s, str) and s.isdigit()


def _shorten(s: str, limit: int) -> str:
    if s is None:
        return ''
    s = str(s)
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + '...'


def run(api):
    """主入口。遵循平台输出契约。

    返回格式：{summary, items, notice}
    items: [{title, detail, suggestion or None, source_refs: [int,...]}]
    """
    ctx = getattr(api, 'context', None)
    if not isinstance(ctx, dict):
        raise ValueError('无法识别运行上下文，未完成检查')

    account = ctx.get('account')
    # account 必须明确可用（非空、非 None）以避免把自己误判为对方
    if not isinstance(account, str) or not account.strip() or account.isdigit():
        raise ValueError('无法识别当前账户，未完成检查')

    api.log('检查私聊：每批最多 100 个会话，读取各自最后一条物理消息')

    chats = api.chats()
    if not isinstance(chats, list):
        raise ValueError('会话列表格式无效，未完成检查')

    chats = [chat for chat in chats if chat.get('type') == 'private']
    latest_by_chat = {}
    for start in range(0, len(chats), 100):
        ids = [chat['id'] for chat in chats[start:start + 100]]
        batch = api.messages_batch(chat_ids=ids, latest=1)
        if not isinstance(batch, dict) or set(batch) != set(ids):
            raise ValueError('批量末条消息接口缺失会话，未完成检查')
        latest_by_chat.update(batch)

    candidates: List[Tuple[dict, dict, bool]] = []  # (chat, message, ambiguous)
    msgs_for_cite: List[dict] = []

    # 遍历所有会话，严格只处理私聊
    for chat in chats:
        if chat.get('type') != 'private':
            continue
        msgs = latest_by_chat[chat['id']]
        if not isinstance(msgs, list) or len(msgs)>1:
            raise ValueError('末条消息接口格式无效，未完成检查')
        if not msgs:
            continue
        msg = msgs[0]
        sender_username = msg.get('sender_username')
        order_uncertain = bool(msg.get('order_uncertain'))
        unknown_sender = not isinstance(sender_username,str) or not sender_username.strip() or _is_numeric_only(sender_username)
        is_self = not unknown_sender and sender_username == account
        if is_self and not order_uncertain:
            continue
        candidates.append((chat, msg, unknown_sender or order_uncertain))
        msgs_for_cite.append(msg)

    # 获取平台证据引用（整数），用于可验证的 source_refs
    refs: List[int] = []
    if msgs_for_cite:
        cited = api.cite(messages=msgs_for_cite)
        if not isinstance(cited,list) or len(cited)!=len(msgs_for_cite):
            raise ValueError('引用数量与消息不一致')
        refs = [c.get('reference') for c in cited]
        if any(type(ref) is not int or ref<=0 for ref in refs):
            raise ValueError('引用编号无效')

    items = []
    for idx, (chat, msg, ambiguous) in enumerate(candidates):
        title = _shorten(chat.get('title') or '私聊（无标题）', 60)

        # 保留原始显示名和文本/类型信息
        sender_display = msg.get('sender') or ''
        sender_username = msg.get('sender_username')

        # 准备内容摘要，保留原消息类型与文本占位
        text = msg.get('text')
        if text is None:
            # 非文本消息保留类型说明（例如通知、表情、通话等）以及原始 type 字段
            msg_type = msg.get('type') or 'unknown'
            content_snippet = f'<{msg_type} 消息>'
        else:
            content_snippet = _shorten(text, 120)

        time_str = msg.get('time') or ''

        detail = f"{time_str} · {sender_display} ({sender_username or '未知ID'})：{content_snippet}"
        detail = _shorten(detail, 180)

        suggestion = None
        if ambiguous:
            suggestion = '发送者身份未知或时间顺序不确定，请人工确认是否需要回复。'

        src_ref = []
        if refs:
            ref = refs[idx]
            if isinstance(ref, int):
                src_ref = [ref]

        items.append({
            'title': title,
            'detail': detail,
            'suggestion': suggestion,
            'source_refs': src_ref
        })

    summary = (f"发现 {len(items)} 个可能待回或需人工确认的私聊" if items else "未发现可能待回的私聊")
    notice = ('按每个私聊的最后一条物理消息筛选；仅作为提示，表情/通知/通话等原始消息被保留，请人工复核是否需要回复。')

    return {'summary': summary, 'items': items, 'notice': notice}
