from __future__ import annotations

from datetime import datetime

from .goals import GoalCancelled


class GoalChatTools:
    """Read all matching messages in the task window from current snapshots."""

    def __init__(self, state, reader, tokenizer, cancelled, voice_cache=None):
        self.state, self.reader, self.tokenizer, self.cancelled = state, reader, tokenizer, cancelled
        self.voice_cache = voice_cache

    def check(self):
        if self.cancelled():
            raise GoalCancelled()

    @staticmethod
    def date(value, default):
        if not value:
            return int(default)
        if not isinstance(value, str):
            raise ValueError("日期格式无效")
        try:
            return int(datetime.fromisoformat(value).timestamp())
        except (ValueError, OverflowError):
            raise ValueError("日期请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")

    def read(self, chat, since, until):
        self.check()
        return self.reader(self.state, chat, since_ts=since, until_ts=until, max_items=None,
                           voice_cache=self.voice_cache, max_text_chars=None, check_cancelled=self.check)

    @staticmethod
    def compact(item):
        keys = ("chat_id", "chat_title", "chat_type", "timestamp", "time", "sender", "sender_username",
                "mine", "type", "source_db", "source_table", "local_id", "server_id")
        return {**{key: item.get(key) for key in keys}, "text": str(item.get("text") or "")}

    def __call__(self, name, args, default_since, now):
        self.check()
        if not self.state.chats:
            raise ValueError("尚无可读取的聊天数据，请先同步消息")
        requested_since = self.date(args.get("since"), default_since)
        requested_until = self.date(args.get("until"), now)
        since = max(requested_since, int(default_since))
        until = min(requested_until, int(now))
        if since > until:
            raise ValueError("时间范围无效")
        warnings = []
        if requested_since < since or requested_until > until:
            warnings.append("工具请求超出目标检索范围，已限制在设定范围内")
        if self.state.since_ts and since < self.state.since_ts:
            since = self.state.since_ts
            warnings.append("早于应用数据起始日期的消息不可见")
        chats = [rec for rec in self.state.chats if (rec.get("last_ts") or 0) >= since]
        messages = []
        extra = {}
        if name == "read_chat":
            chat = self.state.chat_by_id(str(args.get("chat_id") or ""))
            if not chat:
                raise ValueError("未找到会话，请使用检索返回的 chat_id")
            messages = self.read(chat, since, until)
        elif name == "list_private_chats":
            private = sorted((c for c in chats if c.get("type") == "private"), key=lambda c: c.get("last_ts") or 0, reverse=True)
            for chat in private:
                messages.extend(self.read(chat, since, until))
            extra = {"total_chats": len(private), "next_offset": None}
            if not self.state.account:
                warnings.append("未识别当前账户，不能可靠判断谁发了最后一条消息")
        elif name == "search_messages":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > 500:
                raise ValueError("请提供最多 500 字的检索关键词")
            terms = self.tokenizer(query)
            if not terms:
                raise ValueError("没有可用检索词，请更换关键词")
            candidates = []
            scanned = 0
            for chat in sorted(chats, key=lambda c: c.get("last_ts") or 0, reverse=True):
                items = self.read(chat, since, until)
                for item in items:
                    self.check()
                    text = str(item.get("text") or "").casefold()
                    score = sum(1 for term in terms if term.casefold() in text)
                    if score:
                        candidates.append((score, item.get("timestamp") or 0, item))
                scanned += len(items)
            candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
            messages = [value[2] for value in candidates]
            extra = {"matched": len(candidates), "scanned": scanned}
        else:
            raise ValueError("不支持的检索工具")
        if self.state.sync_error:
            warnings.append("最近一次消息同步失败，结果可能缺少最新消息")
        if not self.state.last_synced_at:
            warnings.append("尚未确认消息同步成功，仅能检查现有快照")
        return {"messages": [self.compact(item) for item in messages], "returned": len(messages),
                "complete": True, "since": since, "until": until,
                "synced_at": self.state.last_synced_at, "warning": "；".join(warnings), **extra}
