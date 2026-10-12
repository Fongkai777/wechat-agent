"""Native model messages for Skills; execution remains explicit in the package."""
import json


def validate_messages(messages, native=False):
    if not isinstance(messages,list) or not messages:
        raise ValueError('模型 messages 无效')
    pending, seen = set(), set()
    for message in messages:
        if not isinstance(message,dict): raise ValueError('模型消息必须为对象')
        role = message.get('role')
        if native and role=='assistant' and message.get('tool_calls'):
            if pending or set(message)-{'role','content','tool_calls'}:
                raise ValueError('上一轮工具结果尚未回传或消息字段无效')
            if message.get('content') is not None and not isinstance(message['content'],str):
                raise ValueError('模型消息正文无效')
            calls=message['tool_calls']
            if not isinstance(calls,list) or len(calls)!=1: raise ValueError('每轮仅支持一个工具调用')
            call=calls[0]
            if not isinstance(call,dict) or call.get('type')!='function': raise ValueError('工具调用格式无效')
            call_id=call.get('id'); function=call.get('function')
            if not isinstance(call_id,str) or not call_id or call_id in seen: raise ValueError('工具调用 ID 无效或重复')
            if not isinstance(function,dict) or not isinstance(function.get('name'),str) or not isinstance(function.get('arguments'),str):
                raise ValueError('工具函数参数无效')
            pending.add(call_id); seen.add(call_id)
        elif native and role=='tool':
            if set(message)!={'role','tool_call_id','content'} or message['tool_call_id'] not in pending or not isinstance(message['content'],str):
                raise ValueError('工具结果必须对应尚未回传的 tool_call_id')
            pending.remove(message['tool_call_id'])
        elif pending or set(message)!={'role','content'} or role not in ('system','user','assistant') or not isinstance(message['content'],str):
            raise ValueError('模型消息无效或缺少工具结果')
    if pending: raise ValueError('工具结果尚未回传，不能开始下一轮模型请求')
    if len(json.dumps(messages).encode())>1024*1024: raise ValueError('单次模型输入超过 1 MB 安全预算；请分批处理')
