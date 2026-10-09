"""Trusted child bootstrap. Launched with -I -S inside the OS sandbox."""
import importlib
import json
import os
import resource
import sys
import traceback

resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(__file__))
transport = sys.stdout
sys.stdout = sys.stderr


def send(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(data.encode()) > 8*1024*1024: raise ValueError('Skill 输出过大')
    transport.write(data+'\n'); transport.flush()


class API:
    def __init__(self, context): self.context = context
    def __getattr__(self, method):
        def call(*args, **kwargs):
            parameters = {'chats':(), 'messages':('chat_id','since','until','latest'),
                          'query':('sql','params'), 'semantic_search':('query','min_similarity'),
                          'surrounding':('messages','before','after','max_gap_minutes'),
                          'cite':('messages',), 'llm':('messages','schema'), 'state_get':(),
                          'state_set':('value',), 'log':('message',)}
            names = parameters.get(method)
            if names is None or len(args)>len(names): raise TypeError('未知方法或参数过多')
            for name,value in zip(names,args):
                if name in kwargs: raise TypeError('重复参数：'+name)
                kwargs[name]=value
            send({'type':'call','method':method,'arguments':kwargs})
            line = sys.stdin.buffer.readline(32*1024*1024+1)
            if len(line) > 32*1024*1024: raise ValueError('平台响应过大')
            response = json.loads(line)
            if not response.get('ok'): raise RuntimeError(response.get('error','平台请求失败'))
            return response.get('value')
        return call


try:
    initial = json.loads(sys.stdin.readline())
    if initial['mode'] == 'test':
        importlib.import_module('test_skill').test()
        result = {'tests_passed': True}
    else:
        result = importlib.import_module('workflow').run(API(initial['context']))
    send({'type':'result','value':result})
except BaseException as exc:
    frames = [f'{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}'
              for frame in traceback.extract_tb(exc.__traceback__) if os.path.dirname(frame.filename)==os.path.dirname(__file__)]
    send({'type':'error','error':type(exc).__name__+': '+str(exc)[:1000]+'; '+ ' -> '.join(frames)[-500:]})
    sys.exit(1)
