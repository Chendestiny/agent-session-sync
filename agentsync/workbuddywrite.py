r"""写入 WorkBuddy（~/.workbuddy 或 ~/.workbuddy-ai）：workbuddy.db + projects/<slug>/<id>.jsonl 双写。

配方对齐 agentctxsync 的 workbuddy 适配器（对 5.3.13 实机验证）：
- jsonl：首行 ai-title（含转义反斜杠的 cwd），之后每消息一事件——
  user=input_text / assistant=output_text / reasoning=rawContent /
  function_call+function_call_result（我们的 IR 比 agentctxsync 的有损配方多保真
  一档：调用与回传都写，读取器按 callId 配对）
- db：sessions 行 INSERT（id,cwd,user_id,title,status,created_at,updated_at,
  is_playground=0,mode='craft',model,last_activity_at）——cwd 必须真实存在，
  WorkBuddy 拒开 cwd 缺失的会话（agentctxsync 同款兜底：不存在则落主目录）
- slug 规则复用读取器 _workbuddy_slug（盘符小写、\→-、盘根无尾横线）
- id = uuid5（合法文件名，确定性幂等）；增量按已有 user 消息事件数整轮追加
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim
from .readers import _workbuddy_slug

_NS = uuid.UUID("9c2e4f6a-8d1b-4e7a-b3c5-1f9d6b8e0a2c")


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"{sess.source}:{sess.source_id}"))


def _user_id(wb_home: str) -> str:
    try:
        cfg = json.load(open(os.path.join(wb_home, "settings.json"), encoding="utf-8"))
        uid = ((cfg.get("claw") or {}).get("legacyOwnerUid")) if isinstance(cfg, dict) else None
        if isinstance(uid, str) and uid:
            return uid
    except (OSError, ValueError):
        pass
    return "agentsync"


def _count_turns(path: str) -> int:
    """数已有 jsonl 的 user 消息事件数（=轮数）。"""
    n = 0
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "message" and e.get("role") == "user":
                n += 1
    except OSError:
        pass
    return n


def _turn_events(sid: str, cwd: str, turn, idx: int, base_ms: int) -> list[dict]:
    """一个 IR 轮 → WorkBuddy jsonl 事件列表。"""
    ms = turn.time or (base_ms + idx * 1000)  # 轮级真实时间优先；未知回退确定性合成
    ev: list[dict] = []

    def E(fields: dict, k: int) -> dict:
        d = {"id": str(uuid.uuid5(_NS, f"{sid}:{idx}:{k}:{fields.get('type')}:{len(ev)}")),
             "timestamp": ms + k, "sessionId": sid}
        d.update(fields)
        return d

    k = 0
    ev.append(E({"type": "message", "role": "user", "status": "completed",
                 "content": [{"type": "input_text", "text": turn.prompt}]}, k))
    for step in turn.steps:
        texts: list[str] = []
        reasonings: list[str] = []
        calls: list[tuple[str, str, str]] = []
        results = {tr.tool_call_id: tr for tr in step.tool_results}
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
                calls.append((str(b.get("id") or ""), b.get("name") or "unknown",
                              args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)))
        if reasonings:
            k += 1
            ev.append(E({"type": "reasoning",
                         "rawContent": [{"type": "reasoning_text", "text": "\n".join(reasonings)}]}, k))
        for cid, name, args_text in calls:
            k += 1
            ev.append(E({"type": "function_call", "callId": cid, "name": name,
                         "arguments": args_text}, k))
            tr = results.pop(cid, None)
            if tr is not None:
                k += 1
                out_text = "\n".join(
                    x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
                )
                ev.append(E({"type": "function_call_result", "callId": cid, "name": name,
                             "status": "failed" if tr.is_error else "completed",
                             "output": {"type": "text", "text": out_text}}, k))
        for tr in results.values():
            k += 1
            out_text = "\n".join(
                x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
            )
            ev.append(E({"type": "function_call_result", "callId": tr.tool_call_id, "name": "tool",
                         "status": "failed" if tr.is_error else "completed",
                         "output": {"type": "text", "text": out_text}}, k))
        if texts:
            k += 1
            ev.append(E({"type": "message", "role": "assistant", "status": "completed",
                         "content": [{"type": "output_text", "text": "\n".join(texts)}]}, k))
    return ev


def _prefixed_title(source: str, title: str) -> str:
    """导入标记（对齐 dshwrite 的 [source] 前缀）：在 WorkBuddy 自家 UI 一眼区分来源；已有前缀不重复加。"""
    if title and not (title.startswith("[") and "] " in title[:14]):
        title = f"[{source}] {title}"
    return title


def plan_write(wb_home: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """workbuddy 版写入计划：create / append / up-to-date / skip / skip-deleted。"""
    wb_home = str(wb_home)
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    cwd = sess.cwd or os.path.expanduser("~")
    if not os.path.isdir(cwd):  # WorkBuddy 拒开 cwd 缺失的会话（agentctxsync 同款兜底）
        cwd = os.path.expanduser("~")
    path = os.path.join(wb_home, "projects", _workbuddy_slug(cwd), f"{sid}.jsonl")
    created = sess.created_at or 0
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    plan = {"path": path, "db": os.path.join(wb_home, "workbuddy.db"), "sid": sid,
            "cwd": cwd, "events": [], "stats": stats, "trimmed": trimmed, "sourceTurns": len(turns),
            "title": _prefixed_title(sess.source,
                                     (sess.title or "").strip() or (turns[0].prompt[:40] if turns else "")),
            "model": sess.model, "created": created, "updated": sess.updated_at or created}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(wb_home):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    exists = os.path.exists(path)
    have = _count_turns(path) if exists else 0
    plan["existingTurns"] = have
    if exists and have >= len(turns) and not force:
        return {**plan, "action": "up-to-date"}
    base = 0 if (force or not exists) else have
    tail = turns if (force or not exists) else turns[have:]
    events: list[dict] = []
    if not exists:
        events.append({"timestamp": created or 1787000000000, "type": "ai-title",
                       "aiTitle": plan["title"] or "Imported session", "sessionId": sid,
                       "cwd": cwd.replace("\\", "\\\\")})
    for i, t in enumerate(tail):
        events += _turn_events(sid, cwd, t, base + i, created or 1787000000000)
    plan["events"] = events
    return {**plan, "action": "append" if (exists and not force) else "create"}


def apply_write(plan: dict) -> str:
    # ① jsonl（追加；首行 ai-title 只在新建时写）
    os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
    with open(plan["path"], "a", encoding="utf-8") as f:
        for e in plan["events"]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # ② workbuddy.db sessions 行（INSERT OR REPLACE，列集对齐 agentctxsync 配方）
    con = sqlite3.connect(plan["db"])
    try:
        cur = con.cursor()
        now_ms = plan["updated"] or plan["created"] or 0
        cur.execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, cwd, user_id, title, status, created_at, updated_at, "
            " is_playground, mode, model, last_activity_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (plan["sid"], plan["cwd"], _user_id(os.path.dirname(plan["db"])),
             plan["title"] or "Imported session", "completed",
             plan["created"] or now_ms, plan["updated"] or now_ms, 0, "craft",
             plan["model"], now_ms),
        )
        con.commit()
    finally:
        con.close()
    return f"{plan['action']} {len(plan['events'])} events -> {plan['path']}"
