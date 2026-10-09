"""Run untrusted task packages in a deny-by-default macOS subprocess."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

from .code_skill_packages import manifest, validate_package


def run_package(package, context, dispatch, mode='run', cancelled=lambda: False, preparation_time=lambda: 0):
    package = validate_package(package)
    limits = manifest(package)
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        raise RuntimeError('代码 Skill 需要 macOS 系统隔离执行环境；未启用不受限执行')
    with tempfile.TemporaryDirectory(prefix='wechat-skill-') as directory:
        root = Path(directory).resolve()
        for file in package['files']:
            path = root / file['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file['content'], encoding='utf-8')
        worker = root / '_platform_worker.py'
        worker.write_bytes(Path(__file__).with_name('code_skill_worker.py').read_bytes())
        python = Path(sys._base_executable).resolve()
        runtime = Path(sys.base_prefix).resolve()
        profile = '''(version 1)(deny default)
        (allow process-exec)(allow process-info*)(allow signal (target self))
        (allow file-read-metadata)(allow sysctl-read)
        (allow file-read* (literal "/") (subpath "/System/Library")
          (subpath "/usr/lib") (literal "/dev/null") (literal "/dev/urandom")
          (subpath %s) (subpath %s))''' % (json.dumps(str(runtime)), json.dumps(str(root)))
        process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', profile, str(python),
                                    '-I', '-S', '-B', str(worker)], cwd=root,
                                   env={'LANG':'en_US.UTF-8','PYTHONIOENCODING':'utf-8'},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + (min(30, limits['timeout_seconds']) if mode == 'test' else limits['timeout_seconds'])
        last_memory_check = 0
        def write(value):
            data = (json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n').encode()
            if len(data) > 32*1024*1024:
                raise ValueError('接口结果超过 32 MB 安全预算；请在 Skill 内分批查询，未静默截断')
            # Nonblocking writes keep cancellation and deadlines effective even if user code stops reading.
            offset = 0
            while offset < len(data):
                check()
                try:
                    offset += os.write(process.stdin.fileno(), data[offset:offset+65536])
                except BlockingIOError:
                    time.sleep(.01)
        def check():
            nonlocal last_memory_check
            if cancelled(): raise RuntimeError('任务已停止')
            # Only trusted host-side index preparation can pause the code budget.
            if time.monotonic() > deadline + preparation_time(): raise TimeoutError('代码 Skill 超过执行时间预算')
            if time.monotonic()-last_memory_check > .5 and process.poll() is None:
                last_memory_check = time.monotonic()
                rss = subprocess.run(['/bin/ps','-o','rss=','-p',str(process.pid)],capture_output=True,text=True,timeout=2)
                if rss.stdout.strip() and int(rss.stdout.strip()) > 512*1024:
                    raise MemoryError('Skill 超过 512 MB 驻留内存预算')
        os.set_blocking(process.stdin.fileno(), False)
        selector = selectors.DefaultSelector()
        buffers = {'out':b'', 'err':b''}
        calls = 0
        try:
            for pipe, name in [(process.stdout,'out'),(process.stderr,'err')]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            write({'mode':mode, 'context':context})
            while selector.get_map():
                check()
                for key, _ in selector.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    if name == 'err':
                        buffers['err'] = (buffers['err']+chunk)[-8192:]
                        continue
                    buffers['out'] += chunk
                    if len(buffers['out']) > 8*1024*1024: raise ValueError('Skill 输出超过安全预算')
                    while b'\n' in buffers['out']:
                        line, buffers['out'] = buffers['out'].split(b'\n',1)
                        event = json.loads(line)
                        if event.get('type') == 'result': return event.get('value')
                        if event.get('type') == 'error': raise ValueError(str(event.get('error'))[:1500])
                        if event.get('type') != 'call' or mode == 'test': raise ValueError('非法 Skill 协议')
                        calls += 1
                        if calls > limits['max_api_calls']: raise ValueError('Skill 超过接口调用预算，未保存成功结果')
                        check()
                        # Exceptions terminate the run, rather than allowing a program to hide incomplete retrieval.
                        value = dispatch(event.get('method'), event.get('arguments'))
                        check()
                        write({'ok':True,'value':value})
            raise RuntimeError('Skill 子进程未返回结果：'+buffers['err'].decode(errors='replace')[-1500:])
        finally:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
            selector.close()
            for pipe in (process.stdin,process.stdout,process.stderr): pipe.close()
