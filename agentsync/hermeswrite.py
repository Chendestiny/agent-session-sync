"""写入 hermes（%LOCALAPPDATA%/hermes/state.db）：sessions + messages 两表。

写入配方对齐 agentctxsync 的 hermes 适配器（其主场 agent，实机验证）：
- 会话行：id（uuid5 幂等）/ source=''（NOT NULL 无默认，agentctxsync 同款补法）/
  title / cwd / started_at·ended_at（秒）/ model / archived=0
- 消息行：user=content 纯文本；assistant=content(+reasoning)；工具=assistant 行带
  tool_calls(JSON) + role='tool' 行带 tool_call_id/content
- 去重：会话 id 幂等；消息按 (session_id, role, timestamp) 唯一
- 增量：按已有 user 行数（=轮数）整轮追加，时间戳从会话起点确定性合成
- 墓碑：state.db 同目录 .agentsync-deleted.json（独立于其他目标）
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("3d5a7c9e-8b1d-4f6a-2e8c-7b0d9f1a3c5e")


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"{sess.source}:{sess.source_id}"))


def _conn(db_path: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _count_turns(con, sid: str) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'", (sid,)
    ).fetchone()
    return row[0] if row else 0


def _session_exists(con, sid: str) -> bool:
    return con.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone() is not None


def _turn_rows(sess: Session, turn, idx: int, base_s: float) -> list[dict]:
    """一个 IR 轮 → messages 行（user 提问 + assistant 内容/工具 + tool 结果）。"""
    ts = base_s + idx * 1.0
    rows: list[dict] = [{"role": "user", "content": turn.prompt, "timestamp": ts}]
    k = 0
    for step in turn.steps:
        texts: list[str] = []
        reasonings: list[str] = []
        calls: list[dict] = []
        for b in step.content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                texts.append(b["text"])
            elif bt == "reasoning" and isinstance(b.get("text"), str) and b["text"].strip():
                reasonings.append(b["text"])
            elif bt == "tool-call":
                args = b.get("arguments")
                args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                calls.append({"id": str(b.get("id") or ""), "type": "function",
                              "function": {"name": b.get("name") or "unknown", "arguments": args_text}})
        k += 1
        if texts or reasonings or calls:
            row: dict = {"role": "assistant", "content": "\n".join(texts), "timestamp": ts + k * 0.01}
            if reasonings:
                row["reasoning"] = "\n".join(reasonings)
            if calls:
                row["tool_calls"] = json.dumps(calls, ensure_ascii=False)
                row["tool_name"] = calls[0]["function"]["name"]
            rows.append(row)
        for tr in step.tool_results:
            k += 1
            text = "\n".join(
                x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
            )
            rows.append({"role": "tool", "content": text, "tool_call_id": tr.tool_call_id,
                         "timestamp": ts + k * 0.01})
    return rows


def plan_write(db_path: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """hermes 版写入计划：create / append / up-to-date / skip / skip-deleted。

    titles 参数为对齐调用面保留（hermes 会话标题随行写入，无需覆盖机制）。
    """
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    title = (sess.title or "").strip()
    created_s = (sess.created_at or int(datetime.now(timezone.utc).timestamp() * 1000)) / 1000
    state_dir = os.path.dirname(str(db_path))
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    plan = {"path": str(db_path), "sid": sid, "rows": [], "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "session_row": None}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(state_dir):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截；如确要恢复，先从墓碑文件移除该 id）"}
    con = _conn(db_path)
    try:
        if _session_exists(con, sid) and not force:
            have = _count_turns(con, sid)
            plan["existingTurns"] = have
            if have >= len(turns):
                return {**plan, "action": "up-to-date"}
            rows: list[dict] = []
            for i, t in enumerate(turns[have:]):
                rows += _turn_rows(sess, t, have + i, created_s)
            return {**plan, "action": "append", "rows": rows}
        rows = []
        for i, t in enumerate(turns):
            rows += _turn_rows(sess, t, i, created_s)
        plan["session_row"] = {
            "id": sid, "source": "",  # hermes NOT NULL 无默认列（agentctxsync 同款补法）
            "title": title or (turns[0].prompt[:40] if turns else ""),
            "cwd": sess.cwd or os.path.expanduser("~"),
            "started_at": created_s, "ended_at": created_s + len(rows) * 0.01,
            "model": sess.model, "archived": 0,
        }
        return {**plan, "action": "create", "rows": rows}
    finally:
        con.close()


def apply_write(plan: dict) -> str:
    con = _conn(plan["path"])
    try:
        cur = con.cursor()
        if plan["action"] == "create":
            s = plan["session_row"]
            cur.execute("SELECT 1 FROM sessions WHERE id = ?", (s["id"],))
            if cur.fetchone():
                cur.execute("DELETE FROM messages WHERE session_id = ?", (s["id"],))
                sets = ", ".join(f"{k} = ?" for k in s)
                cur.execute(f"UPDATE sessions SET {sets} WHERE id = ?", list(s.values()) + [s["id"]])
            else:
                cols = ", ".join(s)
                ph = ", ".join(["?"] * len(s))
                cur.execute(f"INSERT INTO sessions ({cols}) VALUES ({ph})", list(s.values()))
        elif plan["action"] != "append":
            raise ValueError(f"unexpected action: {plan['action']}")
        sid = plan["sid"]
        for m in plan["rows"]:
            cur.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND role = ? AND timestamp = ?",
                (sid, m["role"], m["timestamp"]),
            )
            if cur.fetchone():
                continue
            m2 = dict(m)
            m2["session_id"] = sid
            cols = ", ".join(m2)
            ph = ", ".join(["?"] * len(m2))
            cur.execute(f"INSERT INTO messages ({cols}) VALUES ({ph})", list(m2.values()))
        con.commit()
        return f"{plan['action']} {len(plan['rows'])} message rows -> {plan['path']} ({sid[:8]})"
    finally:
        con.close()
