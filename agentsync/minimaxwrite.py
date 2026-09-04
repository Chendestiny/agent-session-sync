r"""写入 MiniMax Code（~/.minimax/v2/sqlite/runtime-state.sqlite）。

2026-09-03 实机验证通过的配方（真库逆向 + dsh 会话写入、UI 验收通过）：
- local_runtime_sessions：session_id='mvs_'+uuid5hex（原生为 v4，版本位可判别导入）；
  columnar_version 必须=3（项目计数触发器只认 v3 行）；record_json 冗余同步一份
- 项目分区：INSERT 触发器按 project_workspace_dir 自动找/建 local_runtime_projects
  行并维护计数，但不会回填 sessions.project_id —— 须手动查 workspace_dir 回填
- local_runtime_message_rows：data_json 内 msg_content/thinking_content；
  轮分组 turn_id（确定性 uuid5 → 幂等追加按 turn_id 判缺失）
- 轮次簿记：turn_ingress/turn_ingress_sequences/query_view_states 每轮一行
  （status=completed）；session_agent_state 每会话一行（UNIQUE，末轮覆盖）
- local_runtime_sessions_fts：无触发器维护，手动补行（搜索用）
- 安全：MiniMax Code 进程在跑则拒绝写（写库须独占）；每次 apply 自动备份 db 三件套
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd43018")  # 与 2026-09-03 实机试验同源（已验收会话保持幂等同 id）
_PROCESS = "MiniMax Code"


def local_id(sess: Session) -> str:
    # 命名串带 minimax 前缀：与 2026-09-03 实机试验一致（该会话已在真库并被 UI 验收，
    # 保持同 id 才能幂等；NS 本就每家独立，命名串格式无跨家耦合）
    return "mvs_" + uuid.uuid5(_NS, f"minimax:{sess.source}:{sess.source_id}").hex


def _turn_id(sid: str, idx: int) -> str:
    """轮 id 确定性铸造：同一会话重复计划/追加得到相同 turn_id。"""
    return str(uuid.uuid5(_NS, f"{sid}:turn:{idx}"))


def _prefixed_title(source: str, title: str) -> str:
    """导入标记（对齐 dshwrite 的 [source] 前缀）；已有前缀不重复加。"""
    if title and not (title.startswith("[") and "] " in title[:14]):
        title = f"[{source}] {title}"
    return title


def _app_running() -> bool:
    try:
        out = subprocess.run(["tasklist"], capture_output=True).stdout
    except OSError:
        return False
    return _PROCESS.encode() in out


def _db_path(home) -> str:
    return os.path.join(str(home), "v2", "sqlite", "runtime-state.sqlite")


def _backup(db: str) -> None:
    """apply 前备份三件套（同秒幂等——批量写入只备一次）。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = db + suffix
        dst = src + f".agentsync-bak-{ts}"
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)


def _count_turns(cur, sid: str) -> int:
    """已有轮数 = user 消息数（每轮恰一条 user 行）。"""
    n = 0
    for (dj,) in cur.execute(
            "SELECT data_json FROM local_runtime_message_rows WHERE session_id=?", (sid,)):
        try:
            if (json.loads(dj or "{}") or {}).get("role") == "user":
                n += 1
        except (ValueError, TypeError):
            continue
    return n


def _turn_payloads(sess: Session, turns, base_ms: int, base_idx: int = 0) -> list[dict]:
    """IR 轮 → 消息行载荷（turn_id/各行 data_json）。base_idx=已有轮数，
    turn_id 用全局轮号铸造——追加轮与首轮不撞 id。"""
    out = []
    for i, t in enumerate(turns):
        tid = _turn_id(local_id(sess), base_idx + i)
        ms = t.time or (base_ms + i * 1000)
        rows = [{"role": "user", "msg_id": f"msg-user-v1-agentsync-{tid[:8]}",
                 "msg_content": t.prompt, "msg_type": 1,
                 "query_key": f"turn:{tid}", "turnId": tid, "source": "api", "timestamp": ms}]
        k = 0
        for st in t.steps:
            k += 1
            think = "\n".join(b.get("text", "") for b in st.content if b.get("type") == "reasoning")
            texts = [b.get("text", "") for b in st.content if b.get("type") == "text"]
            tools = "\n".join(f"[工具调用 {tc.get('name')}]" for tc in st.tool_calls)
            for tr in st.tool_results:
                tools += "\n[工具结果] " + "\n".join(
                    b.get("text", "") for b in tr.content if b.get("type") == "text")[:2000]
            body = "\n\n".join(x for x in (think, tools, "\n".join(texts)) if x)
            if not body:
                continue
            mid = str(uuid.uuid5(_NS, f"{tid}:step:{k}"))
            d = {"msg_id": mid, "turn_id": tid, "turnId": tid, "timestamp": ms + k,
                 "role": "assistant", "msg_type": 1, "msg_content": body,
                 "finish_reason": "stopped", "canonical_message_id": mid}
            if think:
                d["thinking_content"] = think
            rows.append(d)
        out.append({"turn_id": tid, "ms": ms, "rows": rows})
    return out


def plan_write(home, sess: Session, budget: int | None, force: bool = False,
               titles: dict | None = None) -> dict:
    """minimax 版写入计划：create / append / up-to-date / skip / skip-deleted。"""
    home = str(home)
    db = _db_path(home)
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    title = (sess.title or "").strip() or (turns[0].prompt[:40] if turns else "")
    if titles and sess.source_id in titles:
        title = titles[sess.source_id]
    created = sess.created_at or 0
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    plan = {"path": db, "sid": sid, "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "session_row": None, "turns": []}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(os.path.dirname(db)):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    con = sqlite3.connect(f"file:{db.replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        exists = cur.execute(
            "SELECT 1 FROM local_runtime_sessions WHERE session_id=?", (sid,)).fetchone() is not None
        have = _count_turns(cur, sid) if exists else 0
        plan["existingTurns"] = have
        if exists and have >= len(turns) and not force:
            return {**plan, "action": "up-to-date"}
        cwd = sess.cwd or os.path.expanduser("~")
        last_ms = max((t.time for t in turns if t.time), default=0) or created
        plan["session_row"] = {
            "session_id": sid,
            "title": _prefixed_title(sess.source, title),
            "workspace_dir": cwd,
            "created_at_ms": created,
            "updated_at_ms": last_ms,
        }
        tail = turns if (force or not exists) else turns[have:]
        base = 0 if (force or not exists) else have
        plan["turns"] = _turn_payloads(sess, tail, created or 1787000000000, base)
        return {**plan, "action": "append" if (exists and not force) else "create"}
    finally:
        con.close()


def _delete_session_rows(cur, sid: str) -> None:
    for sql in (
        "DELETE FROM local_runtime_message_rows WHERE session_id=?",
        "DELETE FROM local_runtime_turn_ingress WHERE session_id=?",
        "DELETE FROM local_runtime_query_view_states WHERE session_id=?",
        "DELETE FROM local_runtime_session_agent_state WHERE session_id=?",
        "DELETE FROM local_runtime_sessions_fts WHERE session_id=?",
        "DELETE FROM local_runtime_sessions WHERE session_id=?",
    ):
        try:
            cur.execute(sql, (sid,))
        except sqlite3.DatabaseError:
            pass


def apply_write(plan: dict) -> str:
    # 进程守卫只对真库生效（沙箱/自检路径放行）
    from . import paths as _paths

    try:
        real_db = _db_path(_paths.detect().minimax_home) if _paths.detect().minimax_home else None
    except Exception:
        real_db = None
    if real_db and os.path.realpath(plan["path"]) == os.path.realpath(real_db) and _app_running():
        raise SystemExit(f"{_PROCESS} 正在运行——请完全退出后重试（写库须独占）")
    db = plan["path"]
    _backup(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        s = plan["session_row"]
        sid = s["session_id"]
        exists = cur.execute(
            "SELECT 1 FROM local_runtime_sessions WHERE session_id=?", (sid,)).fetchone() is not None
        now_note = time.strftime("%Y/%m/%d/%H-%M-%S-000-") + "session_" + sid
        if exists and plan["action"] == "create":   # force 整体重写
            _delete_session_rows(cur, sid)
            exists = False
        if exists:
            cur.execute(
                "UPDATE local_runtime_sessions SET title=?, updated_at_ms=?, record_json="
                "json_set(record_json,'$.updatedAtMs',?) WHERE session_id=?",
                (s["title"], s["updated_at_ms"], s["updated_at_ms"], sid))
        else:
            cur.execute(
                "INSERT INTO local_runtime_sessions (session_id, record_json, updated_at_ms,"
                " columnar_version, agent_name, runtime, session_type, status, archived, visibility,"
                " session_kind, purpose, purpose_kind, origin_cron_id, parent_session_id,"
                " workspace_dir, project_workspace_dir, is_default_workspace, title, created_at_ms,"
                " error_message, error_code, extra_data_json, project_id, history_relative_dir)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid,
                 json.dumps({"sessionId": sid, "agentName": "__local_runtime_v2__",
                             "workspaceDir": s["workspace_dir"], "runtime": "pi-agent",
                             "sessionType": "branch", "archived": False, "visibility": "visible",
                             "status": "idle", "createdAtMs": s["created_at_ms"],
                             "updatedAtMs": s["updated_at_ms"]}, ensure_ascii=False),
                 s["updated_at_ms"], 3, "mavis", "pi-agent", "branch", "idle", 0, "visible",
                 "conversation", None, "", None, None,
                 s["workspace_dir"], s["workspace_dir"], 0, s["title"], s["created_at_ms"],
                 None, None, "{}", None, now_note))
            # 触发器已按 project_workspace_dir 找/建项目行 → 回填 project_id（对齐应用行为）
            proj = cur.execute(
                "SELECT project_id FROM local_runtime_projects WHERE workspace_dir=?"
                " ORDER BY project_id DESC", (s["workspace_dir"],)).fetchone()
            if proj:
                cur.execute("UPDATE local_runtime_sessions SET project_id=? WHERE session_id=?",
                            (proj["project_id"], sid))

        # 追加缺失轮（turn_id 确定性 → 已在库的轮自动跳过）
        acc = cur.execute(
            "SELECT MAX(accepted_sequence) FROM local_runtime_turn_ingress").fetchone()[0] or 0
        last_tid = last_ms = None
        for tp in plan["turns"]:
            tid, ms = tp["turn_id"], tp["ms"]
            gone = cur.execute(
                "SELECT 1 FROM local_runtime_message_rows WHERE session_id=? AND turn_id=?",
                (sid, tid)).fetchone()
            if gone:
                continue
            for d in tp["rows"]:
                cur.execute(
                    "INSERT INTO local_runtime_message_rows (session_id,msg_id,role,turn_id,"
                    "created_at_ms,data_json) VALUES (?,?,?,?,?,?)",
                    (sid, d["msg_id"], d["role"], tid, d["timestamp"],
                     json.dumps(d, ensure_ascii=False)))
            acc += 1
            cur.execute("INSERT OR REPLACE INTO local_runtime_turn_ingress_sequences (sequence, turn_id)"
                        " VALUES (?,?)", (acc, tid))
            cur.execute(
                "INSERT INTO local_runtime_turn_ingress (turn_id,session_id,source,input_json,status,"
                "accepted_at_ms,accepted_sequence,completed_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                (tid, sid, "turn", "{}", "completed", ms, acc, ms + 1))
            cur.execute(
                "INSERT OR REPLACE INTO local_runtime_query_view_states (session_id,query_key,"
                "current_turn_id,force_expanded,processing_started_at_ms,processing_finished_at_ms,"
                "updated_at_ms) VALUES (?,?,?,?,?,?,?)",
                (sid, f"turn:{tid}", tid, 0, ms, ms + 1, ms + 1))
            last_tid, last_ms = tid, ms + 1
        if last_tid:
            cur.execute(
                "INSERT OR REPLACE INTO local_runtime_session_agent_state (session_id,turn_id,"
                "turn_sequence,event_id,terminal_outcome,updated_at_ms) VALUES (?,?,?,?,?,?)",
                (sid, last_tid, acc, f"evt_{last_tid}_coding_idle_20", "completed", last_ms))
        # FTS（无触发器，手动维护）
        try:
            cur.execute("DELETE FROM local_runtime_sessions_fts WHERE session_id=?", (sid,))
            cur.execute(
                "INSERT INTO local_runtime_sessions_fts (session_id,session_id_terms,agent_name_terms,"
                "title_terms,workspace_dir_terms,purpose_terms,status_terms,session_type_terms)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sid, sid, "mavis", s["title"], s["workspace_dir"], "", "idle", "branch"))
        except sqlite3.DatabaseError:
            pass
        con.commit()
        n = sum(len(tp["rows"]) for tp in plan["turns"])
        return f"{plan['action']} {n} messages -> {db} ({sid[:16]})"
    finally:
        con.close()
