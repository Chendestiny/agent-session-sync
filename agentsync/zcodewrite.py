"""[已废弃，不再接线] zcode 写入器：IR → ~/.zcode/cli/db/db.sqlite。

**2026-08-26 起停用**：决策为「zcode 只出不进」——双端同对话易混乱、活库写入
验证成本高，且实测存在日期/渲染兼容问题（导入会话 time_updated 异常、部分客户端
空白）。sync.py 已移除 to-zcode 命令；本文件保留供参考，勿再调用。

曾按本机 zcode 0.16.x db 逆向实现（真实样本逐字段核对）：
  session: id='sess_<uuid>'、project_id=proj_+路径slug、slug=id、directory=path=cwd、
           permission={"mode":"build"}、task_type='interactive'、title_source='first_input'
  message(user):     data={role,time.created,agent,semantics(origin=real_user),anchor.turnId}
  message(assistant):data={role,time.created/completed,parentID,modelID,mode,agent,path,
                          cost,tokens,finish,semantics(origin=agent_runtime),anchor.turnId}
  part: step-start → text/reasoning/tool → step-finish（与 assistant 消息同序）

清理工具：如需删除历史导入会话，识别规则 = id 为 sess_ 前缀 + 总长 41 +
uuid 版本位（第三段首位）= '5'（uuid5 派生），与原生 uuid4 区分。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime

from .model import Session
from .paths import zcode_project_id

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "agentsync/zcode")


def _u5(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def zcode_session_id(source: str, source_id: str) -> str:
    return "sess_" + _u5(f"{source}|{source_id}")


def _zcode_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq zcode.exe"], capture_output=True, timeout=15
        ).stdout.decode("utf-8", errors="replace").lower()
        if "zcode.exe" in out:
            return True
        out2 = subprocess.run(["tasklist"], capture_output=True, timeout=30).stdout.decode("utf-8", errors="replace").lower()
        for ln in out2.splitlines():
            name = ln.split()[0] if ln.split() else ""
            if name.startswith("zcode") and name.endswith(".exe"):
                return True
        return False
    except Exception:
        return False


def build_rows(sess: Session, app_version: str = "0.16.3"):
    """IR → (session_row, [(msg_id, seq, data, t)], [(part_id, msg_id, seq, data, t)])。"""
    zid = zcode_session_id(sess.source, sess.source_id)
    cwd = sess.cwd or os.path.expanduser("~")
    base_t = sess.created_at or int(time.time() * 1000)
    n_msgs = 0

    messages: list[tuple[str, int, str, int]] = []   # (id, seq, data_json, time)
    parts: list[tuple[str, str, int, str, int]] = []  # (id, msg_id, seq, data_json, time)
    part_seq: dict[str, int] = {}

    def add_part(msg_id: str, data: dict, t: int):
        seq = part_seq.get(msg_id, 0)
        part_seq[msg_id] = seq + 1
        parts.append(
            (
                "part_" + _u5(f"{zid}|{msg_id}|p{seq}"),
                msg_id,
                seq,
                json.dumps(data, ensure_ascii=False),
                t,
            )
        )

    for i, turn in enumerate(sess.turns, start=1):
        turn_id = "turn_" + _u5(f"{zid}|turn{i}")
        t_user = base_t + n_msgs * 1000
        umsg_id = "msg_" + _u5(f"{zid}|u{i}")
        messages.append(
            (
                umsg_id,
                n_msgs,
                json.dumps(
                    {
                        "role": "user",
                        "time": {"created": t_user},
                        "agent": "zcode-agent",
                        "semantics": {
                            "origin": "real_user",
                            "kind": "user_prompt",
                            "uiVisibility": "visible",
                            "providerVisibility": "visible",
                            "transcriptVisibility": "visible",
                        },
                        "anchor": {"turnId": turn_id, "origin": "realUser"},
                    },
                    ensure_ascii=False,
                ),
                t_user,
            )
        )
        n_msgs += 1
        add_part(umsg_id, {"type": "text", "text": turn.prompt, "time": {"start": t_user, "end": t_user}}, t_user)

        for j, step in enumerate(turn.steps, start=1):
            t_a = base_t + n_msgs * 1000
            amid = "msg_" + _u5(f"{zid}|a{i}:{j}")
            has_tools = bool(step.tool_calls)
            messages.append(
                (
                    amid,
                    n_msgs,
                    json.dumps(
                        {
                            "role": "assistant",
                            "time": {"created": t_a, "completed": t_a + 1000},
                            "parentID": umsg_id,
                            **({"modelID": step.model or sess.model} if (step.model or sess.model) else {}),
                            "mode": "build",
                            "agent": "zcode-agent",
                            "path": {"cwd": cwd, "root": cwd},
                            "cost": 0,
                            "tokens": {
                                "total": 0, "input": 0, "output": 0, "reasoning": 0,
                                "cache": {"read": 0, "write": 0},
                            },
                            "finish": "tool-calls" if has_tools else "stop",
                            "semantics": {
                                "origin": "agent_runtime",
                                "kind": "assistant_response",
                                "uiVisibility": "visible",
                                "providerVisibility": "visible",
                                "transcriptVisibility": "visible",
                            },
                            "anchor": {"turnId": turn_id},
                        },
                        ensure_ascii=False,
                    ),
                    t_a,
                )
            )
            n_msgs += 1
            add_part(amid, {"type": "step-start"}, t_a)
            result_by_call = {tr.tool_call_id: tr for tr in step.tool_results}
            for b in step.content:
                bt = b.get("type")
                if bt in ("text", "reasoning"):
                    add_part(amid, {"type": bt, "text": b.get("text") or "", "time": {"start": t_a, "end": t_a + 1000}}, t_a)
                elif bt == "tool-call":
                    tr = result_by_call.get(b.get("id"))
                    try:
                        args_obj = json.loads(b.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args_obj = {"raw": b.get("arguments") or ""}
                    add_part(
                        amid,
                        {
                            "type": "tool",
                            "callID": b.get("id"),
                            "tool": b.get("name") or "unknown",
                            "state": {
                                "status": "error" if (tr and tr.is_error) else "completed",
                                "input": args_obj,
                                "output": _result_text(tr),
                                "title": b.get("name") or "unknown",
                            },
                        },
                        t_a,
                    )
            add_part(
                amid,
                {
                    "type": "step-finish",
                    "reason": "tool-calls" if has_tools else "stop",
                    "cost": 0,
                    "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
                t_a + 1000,
            )

    now = int(time.time() * 1000)
    session_row = (
        zid,
        zcode_project_id(cwd),
        None,            # workspace_id
        None,            # parent_id
        zid,             # slug
        cwd,             # directory
        cwd,             # path
        sess.title or (sess.turns[0].prompt[:40] if sess.turns else "imported"),
        app_version,
        None, None, None, None, None, None,   # share_url, summary_*, revert
        '{"mode":"build"}',
        base_t,
        base_t + max(0, n_msgs - 1) * 1000,
        None, None,      # time_compacting, time_archived
        "interactive",
        "first_input",
        None,
        base_t,
        None,            # trace_id
    )
    return session_row, messages, parts


def _result_text(tr) -> str:
    if tr is None:
        return ""
    parts_ = []
    for b in tr.content or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            parts_.append(b["text"])
    return "\n".join(parts_)


def plan_write(db_path: str, sess: Session, force: bool = False) -> dict:
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        zid = zcode_session_id(sess.source, sess.source_id)
        exists = cur.execute("SELECT COUNT(*) FROM session WHERE id=?", (zid,)).fetchone()[0]
        app_version = cur.execute("SELECT version FROM session ORDER BY time_created DESC LIMIT 1").fetchone()
        row, messages, parts = build_rows(sess, app_version[0] if app_version else "0.16.3")
        if exists and not force:
            return {"action": "exists", "session_id": zid, "reason": "已存在（--force 重建）"}
        if not messages:
            return {"action": "skip", "session_id": zid, "reason": "无可导入消息"}
        return {
            "action": "create" if not exists else "rebuild",
            "session_id": zid,
            "session_row": row,
            "messages": messages,
            "parts": parts,
        }
    finally:
        con.close()


_BACKED_UP: set[str] = set()


def apply_write(db_path: str, plan: dict, backup: bool = True, allow_live: bool = False) -> str:
    if plan["action"] in ("skip", "exists"):
        return "noop"
    if _zcode_running() and not allow_live:
        raise RuntimeError("检测到 zcode 正在运行：请先退出 zcode 客户端再写入（或 --allow-live 自担风险）")
    if backup and db_path not in _BACKED_UP:
        _BACKED_UP.add(db_path)  # 每次运行只备份一次（每会话一备份曾产生 23GB）
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{db_path}.agentsync-bak-{stamp}"
        src = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
        dst = sqlite3.connect(bak)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        zid = plan["session_id"]
        cur.execute("DELETE FROM part WHERE session_id=?", (zid,))
        cur.execute("DELETE FROM message WHERE session_id=?", (zid,))
        cur.execute("DELETE FROM session WHERE id=?", (zid,))
        session_cols = [
            "id", "project_id", "workspace_id", "parent_id", "slug", "directory", "path", "title",
            "version", "share_url", "summary_additions", "summary_deletions", "summary_files",
            "summary_diffs", "revert", "permission", "time_created", "time_updated",
            "time_compacting", "time_archived", "task_type", "title_source", "title_message_id",
            "time_title_updated", "trace_id",
        ]
        cur.execute(
            f"INSERT INTO session ({','.join(session_cols)}) VALUES ({','.join(['?'] * len(session_cols))})",
            plan["session_row"],
        )
        cur.executemany(
            "INSERT INTO message (id, session_id, time_created, time_updated, data, sequence) VALUES (?,?,?,?,?,?)",
            [(m[0], zid, m[3], m[3], m[2], m[1]) for m in plan["messages"]],
        )
        cur.executemany(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data, sequence) VALUES (?,?,?,?,?,?,?)",
            [(p[0], p[1], zid, p[4], p[4], p[3], p[2]) for p in plan["parts"]],
        )
        cur.execute("UPDATE session SET time_updated=? WHERE id=?", (int(time.time() * 1000), zid))
        con.commit()
        return f"{plan['action']}d: {len(plan['messages'])} messages, {len(plan['parts'])} parts"
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
