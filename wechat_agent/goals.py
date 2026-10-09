from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


INTERVAL_UNITS = {"hours": 3600, "days": 86400}
GOAL_MODEL_TIMEOUT_SECONDS = 300
RANGE_LIMITS = {"days": 90, "weeks": 12, "months": 3}
RESULT_LIMITS = {"summary": 80, "title": 60, "detail": 180, "suggestion": 160, "notice": 200}
GOAL_RESULT_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "goal_result_v1", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "required": ["summary", "items", "notice"],
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": RESULT_LIMITS["summary"]},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["title", "detail", "suggestion", "source_refs"],
                        "properties": {
                            "title": {"type": "string", "minLength": 1, "maxLength": RESULT_LIMITS["title"]},
                            "detail": {"type": "string", "minLength": 1, "maxLength": RESULT_LIMITS["detail"]},
                            "suggestion": {"type": ["string", "null"], "minLength": 1, "maxLength": RESULT_LIMITS["suggestion"]},
                            "source_refs": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1}},
                        },
                    },
                },
                "notice": {"type": ["string", "null"], "minLength": 1, "maxLength": RESULT_LIMITS["notice"]},
            },
        },
    },
}


class GoalReferenceError(ValueError):
    def __init__(self, invalid_refs):
        self.invalid_refs = sorted(set(invalid_refs))
        super().__init__("任务结果引用了未检索到的来源，未保存为成功结果")


class GoalCoverageError(RuntimeError):
    pass


def validate_goal_result(text, source_refs=None):
    """Validate both provider output and stored JSON before exposing structured fields."""
    message = "任务结果格式无效，请确认模型支持 Structured Outputs（JSON Schema）；未保存为成功结果"
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError(message) from None
    if not isinstance(data, dict) or set(data) != {"summary", "items", "notice"}:
        raise ValueError(message)

    def valid_text(value, field, nullable=False):
        return (nullable and value is None) or (isinstance(value, str) and bool(value.strip()) and len(value) <= RESULT_LIMITS[field])

    if not valid_text(data["summary"], "summary") or not valid_text(data["notice"], "notice", True):
        raise ValueError(message)
    if not isinstance(data["items"], list):
        raise ValueError(message)
    invalid_refs = []
    for item in data["items"]:
        if not isinstance(item, dict) or set(item) != {"title", "detail", "suggestion", "source_refs"}:
            raise ValueError(message)
        if not all(valid_text(item[field], field, field == "suggestion") for field in ("title", "detail", "suggestion")):
            raise ValueError(message)
        refs = item["source_refs"]
        if not isinstance(refs, list) or not refs or any(type(ref) is not int or ref < 1 for ref in refs):
            raise ValueError(message)
        if source_refs is not None:
            invalid_refs.extend(ref for ref in refs if ref not in source_refs)
    if invalid_refs:
        raise GoalReferenceError(invalid_refs)
    return data


def stored_goal_result(text):
    try:
        return validate_goal_result(text)
    except ValueError:
        return None


def schedule_config(data):
    if "interval_value" in data or "interval_unit" in data:
        value, unit = data.get("interval_value", 1), data.get("interval_unit", "hours")
    else:
        cadence = data.get("cadence", "hourly")
        if cadence not in ("hourly", "daily"):
            raise ValueError("执行周期须为每 N 小时或每 N 天")
        value, unit = 1, "days" if cadence == "daily" else "hours"
    if unit not in INTERVAL_UNITS:
        raise ValueError("执行周期单位仅支持小时或天")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
        raise ValueError("周期数量须为 1 到 365 的整数")
    return value, unit


def interval_seconds(goal):
    value, unit = schedule_config(goal)
    return value * INTERVAL_UNITS[unit]


def search_range(data):
    kind = data.get("range_type", "today")
    value = data.get("range_value", data.get("range_days", 7))
    unit = data.get("range_unit", "days")
    if kind not in ("today", "recent_days"):
        raise ValueError("检索范围仅支持当日或最近一段时间")
    if kind == "today":
        return kind, 1, "days"
    if unit not in RANGE_LIMITS:
        raise ValueError("检索时间单位仅支持天、周或月")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= RANGE_LIMITS[unit]:
        raise ValueError(f"检索数量须为 1 到 {RANGE_LIMITS[unit]} 的整数")
    return kind, value, unit


def observation_window(goal):
    kind, value, unit = search_range(goal)
    until = goal["started_at"]
    if kind == "today":
        since = datetime.fromtimestamp(until).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    elif unit == "months":
        current = datetime.fromtimestamp(until)
        year, month = divmod(current.year * 12 + current.month - 1 - value, 12)
        month += 1
        since = current.replace(year=year, month=month, day=min(current.day, monthrange(year, month)[1])).timestamp()
    else:
        since = until - value * (7 if unit == "weeks" else 1) * 86400
    return since, until


class GoalConflict(ValueError):
    pass


class GoalCancelled(RuntimeError):
    pass


class GoalStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
                    cadence TEXT NOT NULL, enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    next_run_at REAL, last_success_at REAL
                );
                CREATE TABLE IF NOT EXISTS goal_runs (
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                    started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL,
                    title TEXT NOT NULL, prompt TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                    progress TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '', usage TEXT NOT NULL DEFAULT '{}',
                    sources TEXT NOT NULL DEFAULT '[]', steps TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS goal_runs_time ON goal_runs(goal_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS goal_skill_versions (
                    goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL, prompt TEXT NOT NULL, skill TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(goal_id,version)
                );
                CREATE TABLE IF NOT EXISTS goal_skill_drafts (
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, prompt TEXT NOT NULL,
                    skill TEXT NOT NULL, model TEXT NOT NULL, usage TEXT NOT NULL, created_at REAL NOT NULL
                );
            """)
            # Additive migration keeps existing goals and execution history intact.
            conn.execute("BEGIN IMMEDIATE")
            for table, columns in {
                "goals": {"range_type": "TEXT NOT NULL DEFAULT 'today'", "range_days": "INTEGER NOT NULL DEFAULT 1",
                          "range_value": "INTEGER NOT NULL DEFAULT 1", "range_unit": "TEXT NOT NULL DEFAULT 'days'",
                          "interval_value": "INTEGER NOT NULL DEFAULT 1", "interval_unit": "TEXT NOT NULL DEFAULT 'hours'",
                          "skill": "TEXT NOT NULL DEFAULT '{}'", "skill_version": "INTEGER NOT NULL DEFAULT 0"},
                "goal_runs": {"range_type": "TEXT NOT NULL DEFAULT ''", "range_days": "INTEGER NOT NULL DEFAULT 1",
                              "goal_snapshot": "TEXT NOT NULL DEFAULT '{}'",
                              "range_value": "INTEGER NOT NULL DEFAULT 1", "range_unit": "TEXT NOT NULL DEFAULT 'days'",
                              "window_start": "REAL", "window_end": "REAL", "trigger": "TEXT NOT NULL DEFAULT 'scheduled'",
                              "cancel_requested": "INTEGER NOT NULL DEFAULT 0"},
            }.items():
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                        if name == "range_value":
                            conn.execute(f"UPDATE {table} SET range_value=range_days")
                        if name == "interval_unit":
                            conn.execute("UPDATE goals SET interval_unit='days' WHERE cadence='daily'")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def run_data(row):
        if row is None:
            return None
        result = dict(row)
        for key in ("usage", "sources", "steps"):
            result[key] = json.loads(result[key])
        result["result_data"] = stored_goal_result(result["result"])
        snapshot = json.loads(result.pop("goal_snapshot", '{}'))
        result['skill_version'] = snapshot.get('skill_version', 0)
        result['skill'] = snapshot.get('skill') or {}
        if isinstance(result['skill'], str):
            result['skill'] = json.loads(result['skill'])
        return result

    def checkpoint_path(self, run_id):
        if not run_id or any(c not in '0123456789abcdef' for c in run_id):
            raise ValueError("执行记录编号无效")
        return self.path.parent / "goal_checkpoints" / (run_id + ".sqlite3")

    def resume_info(self, run):
        from .goal_checkpoints import checkpoint_info
        if run["status"] not in ("failed", "interrupted"):
            return {"resumable": False, "retry_confirmation_required": False}
        # Older runs did not save graph state and cannot be resumed.
        if len(run["id"]) != 32 or any(c not in '0123456789abcdef' for c in run["id"]):
            return {"resumable": False, "retry_confirmation_required": False}
        return checkpoint_info(self.checkpoint_path(run["id"]))

    def prune_checkpoints(self):
        from .goal_checkpoints import remove_checkpoint
        with self.connect() as conn:
            keep = {row[0] for row in conn.execute("SELECT id FROM goal_runs WHERE status IN ('running','failed','interrupted')")}
        for path in (self.path.parent / "goal_checkpoints").glob("*.sqlite3"):
            if path.stem not in keep:
                try:
                    remove_checkpoint(path)
                except OSError:
                    pass  # Retry cleanup at startup; never turn a saved success into failure.

    def save_snapshot(self, goal):
        snapshot = {k: v for k, v in goal.items() if k not in ('checkpoint_path', 'resume', 'confirm_retry')}
        with self.connect() as conn:
            conn.execute("UPDATE goal_runs SET goal_snapshot=? WHERE id=? AND status='running'",
                         (json.dumps(snapshot, ensure_ascii=False), goal['run_id']))

    def list(self):
        with self.connect() as conn:
            goals = []
            for row in conn.execute("SELECT * FROM goals ORDER BY created_at DESC"):
                goal = dict(row)
                goal['skill'] = json.loads(goal['skill'])
                goal["enabled"] = bool(goal["enabled"])
                latest = conn.execute("SELECT * FROM goal_runs WHERE goal_id=? ORDER BY (status='running') DESC, started_at DESC LIMIT 1", (goal["id"],)).fetchone()
                goal["latest_run"] = None
                if latest:
                    goal["latest_run"] = {key: latest[key] for key in ("id", "status", "started_at", "finished_at", "progress", "error", "model", "trigger", "cancel_requested")}
                    structured = stored_goal_result(latest["result"])
                    goal["latest_run"]["result_data"] = structured
                    goal["latest_run"]["result"] = structured["summary"] if structured else latest["result"][:1500]
                    goal["latest_run"].update(self.resume_info(latest))
                goals.append(goal)
            return goals

    def history(self, goal_id):
        with self.connect() as conn:
            return [{**self.run_data(row), **self.resume_info(row)} for row in conn.execute(
                "SELECT * FROM goal_runs WHERE goal_id=? ORDER BY started_at DESC LIMIT 20", (goal_id,))]

    def save(self, data, now=None):
        now = time.time() if now is None else now
        title, prompt = str(data.get("title") or "").strip(), str(data.get("prompt") or "").strip()
        if not title or len(title) > 80 or not prompt or len(prompt) > 3000:
            raise ValueError("请填写任务名称（最多 80 字）和任务内容（最多 3000 字）")
        if not isinstance(data.get("enabled", True), bool):
            raise ValueError("启用状态无效")
        enabled = data.get("enabled", True)
        goal_id = str(data.get("id") or uuid.uuid4().hex)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if data.get("id") and old is None:
                raise ValueError("任务不存在")
            old_skill = old['skill'] if old else '{}'
            skill_version = old['skill_version'] if old else 0
            skill_json = old_skill
            if 'skill' in data:
                from .task_skills import validate_skill
                if data.get('skill_prompt') != prompt:
                    raise ValueError('任务内容已变化，请重新生成或确认编辑后的 Skill')
                if data.get('expected_skill_version', skill_version) != skill_version:
                    raise GoalConflict('Skill 已被其他窗口修改，请刷新后重试')
                validated = validate_skill(data['skill'])
                if validated.get('schema_version') == 3:
                    from .code_skill_service import require_validated
                    require_validated(conn, validated, prompt)
                skill_json = json.dumps(validated, ensure_ascii=False, sort_keys=True)
            elif old and skill_version and prompt != old['prompt']:
                raise ValueError('修改任务时必须同时确认对应 Skill')
            skill_changed = json.loads(skill_json) != json.loads(old_skill) or bool(skill_version and old and old['prompt'] != prompt)
            if skill_changed:
                skill_version += 1
            schedule_data = {**(dict(old) if old else {}), **data}
            if "cadence" in data and not {"interval_value", "interval_unit"}.intersection(data):
                schedule_data = {"cadence": data["cadence"]}
            interval_value, interval_unit = schedule_config(schedule_data)
            interval = interval_value * INTERVAL_UNITS[interval_unit]
            cadence = ("hourly" if interval_unit == "hours" else "daily") if interval_value == 1 else "custom"
            range_data = {**(dict(old) if old else {}), **data}
            if "range_days" in data and "range_value" not in data:
                range_data.update(range_value=data["range_days"], range_unit="days")
            range_type, range_value, range_unit = search_range(range_data)
            if old is None:
                if conn.execute("SELECT count(*) FROM goals").fetchone()[0] >= 50:
                    raise ValueError("最多保留 50 个任务")
                conn.execute("""INSERT INTO goals(id,title,prompt,cadence,enabled,created_at,updated_at,next_run_at,range_type,range_value,range_unit,interval_value,interval_unit)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    goal_id, title, prompt, cadence, enabled, now, now,
                    now + interval if enabled else None, range_type, range_value, range_unit, interval_value, interval_unit,
                ))
            else:
                if conn.execute("SELECT 1 FROM goal_runs WHERE goal_id=? AND status='running'", (goal_id,)).fetchone():
                    raise GoalConflict("任务正在执行，请先暂停并等待当前请求结束后再编辑")
                scope_changed = old["range_type"] != range_type or old["range_value"] != range_value or old["range_unit"] != range_unit
                changed = old["interval_value"] != interval_value or old["interval_unit"] != interval_unit or old["prompt"] != prompt or bool(old["enabled"]) != enabled or scope_changed or skill_changed
                next_at = now + interval if changed else old["next_run_at"]
                conn.execute("UPDATE goals SET title=?, prompt=?, cadence=?, enabled=?, updated_at=?, next_run_at=?, last_success_at=?, range_type=?, range_value=?, range_unit=?, interval_value=?, interval_unit=? WHERE id=?", (
                    title, prompt, cadence, enabled, now, next_at if enabled else None,
                    None if old["prompt"] != prompt or scope_changed else old["last_success_at"], range_type, range_value, range_unit, interval_value, interval_unit, goal_id,
                ))
            conn.execute('UPDATE goals SET skill=?,skill_version=? WHERE id=?', (skill_json, skill_version, goal_id))
            if skill_changed:
                conn.execute('INSERT INTO goal_skill_versions VALUES(?,?,?,?,?)', (goal_id, skill_version, prompt, skill_json, now))
        return goal_id

    def save_skill_draft(self, goal_id, prompt, skill, model, usage):
        from .task_skills import validate_skill
        draft_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute('INSERT INTO goal_skill_drafts VALUES(?,?,?,?,?,?,?)',
                         (draft_id, goal_id, prompt, json.dumps(validate_skill(skill), ensure_ascii=False), model, json.dumps(usage), time.time()))
            conn.execute('DELETE FROM goal_skill_drafts WHERE id NOT IN (SELECT id FROM goal_skill_drafts ORDER BY created_at DESC LIMIT 20)')
        return draft_id

    def skill_drafts(self):
        with self.connect() as conn:
            return [{**dict(row), 'skill': json.loads(row['skill']), 'usage': json.loads(row['usage'])}
                    for row in conn.execute('SELECT * FROM goal_skill_drafts ORDER BY created_at DESC')]

    def skill_versions(self, goal_id):
        with self.connect() as conn:
            return [{**dict(row), 'skill': json.loads(row['skill'])} for row in conn.execute(
                'SELECT * FROM goal_skill_versions WHERE goal_id=? ORDER BY version DESC', (goal_id,))]

    def toggle(self, goal_id, enabled, now=None):
        if not isinstance(enabled, bool):
            raise ValueError("启用状态无效")
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if row is None:
                raise ValueError("任务不存在")
            if bool(row["enabled"]) == enabled:
                return
            if enabled and conn.execute("SELECT 1 FROM goal_runs WHERE goal_id=? AND status='running'", (goal_id,)).fetchone():
                raise GoalConflict("请等待当前请求暂停完成后再启用")
            next_at = now + interval_seconds(dict(row)) if enabled else None
            conn.execute("UPDATE goals SET enabled=?, next_run_at=?, updated_at=? WHERE id=?", (enabled, next_at, now, goal_id))
            if not enabled:
                conn.execute("UPDATE goal_runs SET cancel_requested=1, progress='暂停中，等待当前请求结束' WHERE goal_id=? AND status='running'", (goal_id,))

    def delete(self, goal_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM goal_runs WHERE goal_id=? AND status='running'", (goal_id,)).fetchone():
                raise GoalConflict("请先暂停任务，等待执行结束后再删除")
            conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_skill_state'").fetchone():
                conn.execute('DELETE FROM code_skill_state WHERE goal_id=?',(goal_id,))
        self.prune_checkpoints()

    def recover(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("UPDATE goal_runs SET status='interrupted', finished_at=?, progress='', error='上次执行因服务退出而中断' WHERE status='running'", (now,))
        self.prune_checkpoints()

    def claim_due(self, now=None):
        return self._claim(now=now)

    def claim_manual(self, goal_id, now=None):
        if not goal_id:
            raise ValueError("任务不存在")
        return self._claim(goal_id=goal_id, now=now)

    def _claim(self, goal_id=None, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM goal_runs WHERE status='running'").fetchone():
                if goal_id is not None:
                    raise GoalConflict("已有任务正在执行，请等待完成或停止后再试")
                return None
            if goal_id is None:
                row = conn.execute("SELECT * FROM goals WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at LIMIT 1", (now,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if row is None:
                if goal_id is not None:
                    raise ValueError("任务不存在")
                return None
            goal = dict(row)
            goal["started_at"] = now
            goal["trigger"] = "manual" if goal_id is not None else "scheduled"
            window_start, window_end = observation_window(goal)
            run_id = uuid.uuid4().hex
            # Missed ticks coalesce into one run, never an unbounded catch-up queue.
            goal["next_run_at"] = now + interval_seconds(goal) if goal["enabled"] else None
            conn.execute("UPDATE goals SET next_run_at=? WHERE id=?", (goal["next_run_at"], goal["id"]))
            conn.execute("""INSERT INTO goal_runs(id,goal_id,started_at,status,title,prompt,progress,range_type,range_value,range_unit,window_start,window_end,trigger)
                VALUES(?,?,?,'running',?,?,'准备检索',?,?,?,?,?,?)""", (
                run_id, goal["id"], now, goal["title"], goal["prompt"], goal["range_type"], goal["range_value"], goal["range_unit"], window_start, window_end, goal["trigger"],
            ))
            previous = conn.execute("SELECT result FROM goal_runs WHERE goal_id=? AND status='completed' ORDER BY started_at DESC LIMIT 1", (goal["id"],)).fetchone()
            goal.update(run_id=run_id, started_at=now, previous_result=previous[0][:5000] if previous else "")
            conn.execute("UPDATE goal_runs SET goal_snapshot=? WHERE id=?", (json.dumps(goal, ensure_ascii=False), run_id))
            return goal

    def claim_resume(self, goal_id, run_id, confirm_retry=False):
        if type(confirm_retry) is not bool:
            raise ValueError("重试确认必须为布尔值")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM goal_runs WHERE status='running'").fetchone():
                raise GoalConflict("已有任务正在执行，请等待完成或停止后再试")
            row = conn.execute("SELECT * FROM goal_runs WHERE id=? AND goal_id=?", (run_id, goal_id)).fetchone()
            if row is None or not self.resume_info(row)['resumable']:
                raise GoalConflict("这次执行没有可恢复的检查点，请重新执行")
            if self.resume_info(row)['retry_confirmation_required'] and not confirm_retry:
                raise GoalConflict("上次模型请求结果未知，可能已计费；请确认后重试该请求")
            goal = json.loads(row['goal_snapshot'])
            if not goal or goal.get('run_id') != run_id:
                raise GoalConflict("缺少原任务快照，无法恢复")
            # Explicit recovery is allowed even if its periodic schedule is paused.
            goal.update(resume=True, confirm_retry=confirm_retry, checkpoint_path=str(self.checkpoint_path(run_id)))
            current = conn.execute("SELECT next_run_at FROM goals WHERE id=?", (goal_id,)).fetchone()
            goal['next_run_at'] = current[0]
            conn.execute("UPDATE goal_runs SET status='running',cancel_requested=0,finished_at=NULL,error='',progress='正在恢复执行' WHERE id=?", (run_id,))
            return goal

    def cancel_run(self, goal_id, run_id):
        with self.connect() as conn:
            changed = conn.execute("""UPDATE goal_runs SET cancel_requested=1, progress='停止中，等待当前请求结束'
                WHERE id=? AND goal_id=? AND status='running'""", (run_id, goal_id)).rowcount
            if not changed:
                raise GoalConflict("这次执行已结束，请刷新状态")

    def run_cancelled(self, goal):
        with self.connect() as conn:
            row = conn.execute("""SELECT r.status, r.cancel_requested, g.enabled FROM goal_runs r
                JOIN goals g ON g.id=r.goal_id WHERE r.id=?""", (goal["run_id"],)).fetchone()
            return not row or row["status"] != "running" or bool(row["cancel_requested"]) or (not goal.get('resume') and goal["trigger"] != "manual" and not row["enabled"])

    def enabled(self, goal_id):
        with self.connect() as conn:
            row = conn.execute("SELECT enabled FROM goals WHERE id=?", (goal_id,)).fetchone()
            return bool(row and row[0])

    def progress(self, run_id, data):
        with self.connect() as conn:
            conn.execute("UPDATE goal_runs SET progress=CASE WHEN cancel_requested=1 THEN progress ELSE ? END, usage=?, steps=?, sources=?, model=? WHERE id=? AND status='running'", (
                data.get("progress", ""), json.dumps(data.get("usage", {})), json.dumps(data.get("steps", []), ensure_ascii=False),
                json.dumps(data.get("sources", []), ensure_ascii=False), data.get("model", ""), run_id,
            ))

    def finish(self, goal, status, result="", error="", now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT status FROM goal_runs WHERE id=?", (goal['run_id'],)).fetchone()
            if not existing or existing[0] != 'running':
                return
            if status == "completed":
                row = conn.execute("SELECT cancel_requested FROM goal_runs WHERE id=?", (goal["run_id"],)).fetchone()
                if row and row[0]:
                    status, result, error = "cancelled", "", "已停止，未启动后续模型请求"
            conn.execute("UPDATE goal_runs SET status=?, result=?, error=?, progress='', finished_at=? WHERE id=?", (
                status, result, error, now, goal["run_id"],
            ))
            checked_until = goal.get("data_until", goal["started_at"])
            if status == "completed" and checked_until is not None:
                conn.execute("UPDATE goals SET last_success_at=CASE WHEN last_success_at IS NULL OR last_success_at<? THEN ? ELSE last_success_at END WHERE id=?", (checked_until, checked_until, goal["id"]))
            if status == 'completed' and 'code_skill_state' in goal:
                from .code_skill_service import setup
                setup(conn)
                conn.execute('INSERT OR REPLACE INTO code_skill_state VALUES(?,?)', (goal['id'],json.dumps(goal['code_skill_state'],ensure_ascii=False)))
            conn.execute("DELETE FROM goal_runs WHERE goal_id=? AND id NOT IN (SELECT id FROM goal_runs WHERE goal_id=? ORDER BY started_at DESC LIMIT 20)", (goal["id"], goal["id"]))
        self.prune_checkpoints()


class GoalScheduler:
    def __init__(self, store, execute):
        self.store, self.execute = store, execute
        self.stop_event = threading.Event()
        self.thread = None
        self.manual_thread = None
        self.error = ""

    def tick(self, now=None):
        goal = self.store.claim_due(now)
        if goal is None:
            return False
        self._execute(goal)
        return True

    def run_now(self, goal_id):
        if self.stop_event.is_set() or not self.thread or not self.thread.is_alive():
            raise GoalConflict("定时服务未运行，请先启动本地服务")
        goal = self.store.claim_manual(goal_id)
        return self._start_manual(goal)

    def resume(self, goal_id, run_id, confirm_retry=False):
        if self.stop_event.is_set() or not self.thread or not self.thread.is_alive():
            raise GoalConflict("定时服务未运行，请先启动本地服务")
        goal = self.store.claim_resume(goal_id, run_id, confirm_retry)
        return self._start_manual(goal)

    def _start_manual(self, goal):
        worker = threading.Thread(target=self._execute, args=(goal,), daemon=True, name="wechat-goal-manual")
        try:
            worker.start()
        except Exception:
            self.store.finish(goal, "failed", error="无法启动执行，请重试")
            raise GoalConflict("无法启动执行，请重试") from None
        self.manual_thread = worker
        return goal

    def _execute(self, goal):
        def cancelled():
            return self.stop_event.is_set() or self.store.run_cancelled(goal)

        try:
            goal['checkpoint_path'] = str(self.store.checkpoint_path(goal['run_id']))
            result = self.execute(goal, lambda data: self.store.progress(goal["run_id"], data), cancelled)
            if cancelled():
                raise GoalCancelled()
            self.store.finish(goal, "completed", result=result)
        except GoalCancelled:
            if self.stop_event.is_set():
                self.store.finish(goal, "interrupted", error="服务已停止，可从已保存的检查点恢复")
            else:
                self.store.finish(goal, "cancelled", error="已停止，未启动后续模型请求")
        except Exception as exc:
            self.store.finish(goal, "failed", error=str(exc)[:600])

    def start(self):
        self.store.recover()

        def work():
            while not self.stop_event.is_set():
                try:
                    ran = self.tick()
                    self.error = ""
                except Exception:
                    ran = False
                    self.error = "任务调度暂时异常，请检查本地存储"
                self.stop_event.wait(1 if ran else 5)

        self.thread = threading.Thread(target=work, daemon=True, name="wechat-goals")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.manual_thread:
            self.manual_thread.join(timeout=2)


def tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TIME_ARG = {"type": "string", "description": "Local date/time YYYY-MM-DD or YYYY-MM-DD HH:MM:SS. Empty uses the supplied observation window. May narrow but never expand that window."}
TOOLS = [
    tool("search_messages", "Search all synced messages in the observation window by keywords (any keyword can match). Includes groups and private chats. Returns ALL matching messages, not a top-k subset. Keyword matching is not proof that all relevant information was found.", {
        "query": {"type": "string"}, "since": TIME_ARG, "until": TIME_ARG,
    }, ["query"]),
    tool("list_private_chats", "Read ALL private conversations and their messages in the observation window, including who sent them. No conversation or message count limit. This is NOT an unread/needs-reply judgment; use the context to decide.", {
        "since": TIME_ARG,
    }, []),
    tool("read_chat", "Read ALL chronological messages in a known chat within the observation window, including mine flags and voice transcriptions. No message count limit.", {
        "chat_id": {"type": "string"}, "since": TIME_ARG, "until": TIME_ARG,
    }, ["chat_id"]),
]


def run_goal_agent(goal, completion, invoke, report, cancelled, model):
    from .goal_agent import run_goal_graph
    return run_goal_graph(goal, completion, invoke, report, cancelled, model)
