"""Bind saved Skill packages to the shared platform runtime."""
from __future__ import annotations

import hashlib
import json

from .code_skill_packages import validate_package, manifest
from .code_skill_service import require_validated, run_code_skill, setup
from .goals import observation_window, GoalCancelled
from .platform_runtime import create_platform_runtime, prepare_platform_indexes

# Existing application/test imports keep using this name; implementation is shared.
prepare_code_skill_indexes = prepare_platform_indexes


def execute_code_goal(state, store, goal, report, cancelled, maintenance_lock):
    # 应用执行入口：把当前账户的数据、模型配置及任务状态绑定为受控 API 后交给 service。
    from . import web as app
    # Database claims and persisted run snapshots contain serialized packages.
    package = goal['skill']
    goal['skill'] = validate_package(json.loads(package) if isinstance(package, str) else package)
    config = app.load_llm_config(state.llm_config)
    profile = config['task']
    embedding_profile = app.effective_llm_profile(config,'embedding')
    # 恢复签名绑定模型配置和数据来源，但不包含密钥，换密钥本身不会使检查点失效。
    goal['profile_signature'] = hashlib.sha256(json.dumps(
        {name:{k:v for k,v in value.items() if k!='api_key'} for name,value in
         [('task',profile),('embedding',embedding_profile),('rerank',app.effective_llm_profile(config,'rerank')),
          ('source',{'account':state.account,'storage':str(state.db_storage)})]},sort_keys=True).encode()).hexdigest()
    since, until = observation_window(goal)
    # 拒绝超出已导入历史的任务，避免把数据缺失误判为没有匹配消息。
    if state.since_ts and since < state.since_ts:
        raise ValueError('任务范围早于已导入数据的起始日期，请调整范围或导入历史数据')
    if not state.account: raise ValueError('无法识别当前账户，请先同步聊天')
    with store.connect() as conn:
        # 真实执行前检查测试凭据，并按任务 ID 读取跨轮状态，避免任务之间共享状态。
        require_validated(conn,goal['skill'],goal['prompt'])
        row = conn.execute('SELECT value FROM code_skill_state WHERE goal_id=?',(goal['id'],)).fetchone()
        local_state = json.loads(row[0]) if row else {}
    def check():
        if cancelled():
            raise GoalCancelled()
    runtime = create_platform_runtime(state,
        {'task':goal['prompt'],'since':since,'until':until,'account':state.account,
         'dry_run':False,'synced_at':state.last_synced_at,'sync_error':state.sync_error or ''},
        manifest(goal['skill']), config, 'task', check, maintenance_lock,
        local_state=local_state, prepare=prepare_code_skill_indexes)
    return run_code_skill(goal, runtime.api, report, cancelled, str(profile.get('model') or '代码 Skill'),
                          preparation_time=runtime.preparation_time)
