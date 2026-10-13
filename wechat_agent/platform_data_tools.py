"""Native tool views of the same data API used by Python Skills."""
import math

from .model_output import STRING


PAGE = {'type': 'integer', 'minimum': 1, 'maximum': 60}
OFFSET = {'type': 'integer', 'minimum': 0, 'maximum': 1000000}
TIME = {'type': ['number', 'null'], 'description': 'Unix 秒；null 使用本次执行边界，只能缩小范围。'}
PARAM = {'anyOf': [{'type': t} for t in ('string', 'number', 'boolean', 'null')]}

# Tool name -> API method, permissions, description, native argument schema.
DATA_TOOLS = {
    'read_chats': ('chats', {'messages.read'}, '列出已同步的原始会话目录，不依赖索引；按 offset/size 分页。',
                   {'offset': OFFSET, 'size': PAGE}),
    'read_messages': ('messages', {'messages.read'},
        '读取已取得会话ID的原始消息，保留表情和无正文消息。latest=null为全部，latest=N为最后N条；结果按时间顺序分页。',
        {'chat_id': STRING, 'since': TIME, 'until': TIME, 'latest': {'type':['integer','null'],'minimum':1,'maximum':100000},
         'offset': OFFSET, 'size': PAGE}),
    'read_messages_batch': ('messages_batch', {'messages.read'},
        '批量读已取得会话ID的最近消息；latest=1适合判断最后发送者。返回的是选定尾部，不代表完整历史。',
        {'chat_ids': {'type':'array','items':STRING,'maxItems':100}, 'since':TIME, 'until':TIME,
         'latest': {'type':'integer','minimum':1,'maximum':100}, 'offset':OFFSET, 'size':PAGE}),
    'query_messages': ('query', {'index.query'},
        '在限定时间窗快照上运行只读SQLite SELECT/CTE，支持聚合、窗口函数。'
        'messages列：id,chat_id,chat_title,chat_type,timestamp,time,sender,sender_username,text,type,source_db,source_table,local_id,server_id,person_id,person_name；'
        'chats列：id,title,type,last_ts。原文引用需SELECT完整消息字段；聚合结果标为计算摘要。禁止写入和宿主文件访问。结果按offset/size分页。',
        {'sql':STRING,'params':{'type':'array','items':PARAM}, 'since':TIME,'until':TIME,'offset':OFFSET,'size':PAGE}),
    'semantic_search': ('semantic_search', {'embedding'},
        '按相似度阈值召回语义块并回查消息，不做混合重排。阈值不是置信度，返回页不代表穷尽主题。',
        {'query':STRING,'min_similarity':{'type':'number','minimum':0,'maximum':1},'offset':OFFSET,'size':PAGE}),
    'message_context': ('surrounding', {'messages.read'},
        '展开实际取得的消息证据前后文；可配置条数和间隔，支持原始消息，不要求一定有索引行ID。',
        {'source_ids':{'type':'array','items':STRING,'maxItems':60},
         'before':{'type':'integer','minimum':0,'maximum':50},'after':{'type':'integer','minimum':0,'maximum':50},
         'max_gap_minutes':{'type':'integer','minimum':1,'maximum':1440},'offset':OFFSET,'size':PAGE}),
}


def validate_data_arguments(name, values, sources, observations, boundary):
    schema = DATA_TOOLS[name][3]
    if not isinstance(values, dict) or set(values) != set(schema):
        raise ValueError('工具参数与声明不一致')
    for key, spec in schema.items():
        value = values[key]
        kinds = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
        kind = ('null' if value is None else 'boolean' if type(value) is bool else
                'integer' if type(value) is int else 'number' if type(value) is float else
                'string' if isinstance(value,str) else 'array' if isinstance(value,list) else 'unknown')
        if kind not in kinds and not (kind=='integer' and 'number' in kinds):
            raise ValueError('工具参数类型无效：'+key)
        if kind in ('integer','number') and (not math.isfinite(value) or value<spec.get('minimum',-math.inf) or value>spec.get('maximum',math.inf)):
            raise ValueError('工具参数范围无效：'+key)
        if kind=='string' and len(value) > (30000 if key=='sql' else 4000 if key=='query' else 200):
            raise ValueError('工具参数过长：'+key)
        if kind=='array':
            if len(value)>spec.get('maxItems',1000): raise ValueError('数组参数过长')
            if key=='params':
                if any(v is not None and type(v) not in (str,int,float,bool) or isinstance(v,float) and not math.isfinite(v) for v in value):
                    raise ValueError('SQL 参数必须为有限标量')
            elif any(not isinstance(v,str) for v in value) or len(set(value))!=len(value):
                raise ValueError('来源ID必须为不重复字符串')
    known_chats = {s.get('chat_id') for s in sources}
    for observation in observations:
        known_chats.update(c['chat_id'] for c in observation.get('chats', []))
    requested = [values['chat_id']] if 'chat_id' in values else values.get('chat_ids', [])
    if not set(requested)<=known_chats:
        raise ValueError('只能查询已返回的会话ID，请先调用read_chats或list_chats')
    if 'source_ids' in values and (not values['source_ids'] or not set(values['source_ids'])<={s['evidence_id'] for s in sources if s.get('evidence_role')!='statistics'}):
        raise ValueError('上下文只能使用实际取得的消息证据ID')
    low, high = boundary.get('since_ts') or 0, boundary['until_ts']
    start = low if values.get('since') is None else values['since']
    end = high if values.get('until') is None else values['until']
    if not low<=start<=end<=high:
        raise ValueError('不能扩大平台时间范围')
    return {'tool':name, 'arguments':dict(values)}
