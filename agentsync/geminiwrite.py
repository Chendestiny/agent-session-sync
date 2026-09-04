r"""写入 Gemini CLI（~/.gemini）。

配方（源码 chatRecordingService + 中转站真数据核验）：
- 会话文件：tmp/<项目slug>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl，
  项目 slug = cwd basename 小写，.project_root 文件 = cwd 小写落盘标记
- projectHash = sha256(cwd 原样反斜杠字符串)（实测命中真库样本）
- 行形状：首行元数据（sessionId=uuid5——原生是 uuid4，版本位判别导入），
  初始 $set{messages:[session_context]}，此后每条消息一行裸对象
  （type=user / gemini，content=[{text}]；gemini 带该轮回复与 model）
- 增量：按已有 user 裸行数整轮追加（幂等，sessionId → 文件名确定性映射）
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd43018")


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"gemini:{sess.source}:{sess.source_id}"))


def _prefixed_title(source: str, title: str) -> str:
    if title and not (title.startswith("[") and "] " in title[:14]):
        title = f"[{source}] {title}"
    return title


def session_path(home, sid: str, created_ms: int, cwd: str | None) -> str:
    """确定性文件名：tmp/<项目slug>/chats/session-<UTC 分钟>-<id8>.jsonl。
    slug = cwd basename 小写（对齐 gemini 原生项目目录；无 cwd 落 _agentsync 桶）。"""
    slug = os.path.basename((cwd or "").rstrip("/\\")).lower() or "_agentsync"
    stamp = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H-%M")
    return os.path.join(str(home), "tmp", slug, "chats", f"session-{stamp}-{sid.replace('-', '')[:8]}.jsonl")


def _count_user_turns(path: str) -> int:
    n = 0
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return 0
    for l in lines:
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "user" and "sessionId" not in d:
            n += 1
    return n


def _turn_lines(sess: Session, turns, start_ts: int) -> list[str]:
    """IR 轮 → 裸消息行（user + gemini 一问一答）。"""
    out = []
    ms = start_ts
    for i, t in enumerate(turns):
        ms = t.time or (start_ts + i * 1000)
        out.append(json.dumps({
            "id": str(uuid.uuid5(_NS, f"{sess.source_id}:u:{i}")),
            "timestamp": datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": "user", "content": [{"text": t.prompt}],
        }, ensure_ascii=False))
        reply, think = [], []
        for st in t.steps:
            for b in st.content:
                if b.get("type") == "text" and b.get("text"):
                    reply.append(b["text"])
                elif b.get("type") == "reasoning" and b.get("text"):
                    think.append(b["text"])
            for tc in st.tool_calls:
                reply.append(f"[工具调用 {tc.get('name')}] {tc.get('arguments', '')[:200]}")
            for tr in st.tool_results:
                reply.append("[工具结果] " + "\n".join(
                    b.get("text", "") for b in tr.content if b.get("type") == "text")[:2000])
        parts = []
        if think:
            parts.append("\n".join(think))
        if reply:
            parts.append("\n\n".join(reply))
        body = "\n\n".join(parts) or "（无回复）"
        gem = {
            "id": str(uuid.uuid5(_NS, f"{sess.source_id}:g:{i}")),
            "timestamp": datetime.fromtimestamp((ms + 1) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": "gemini", "model": sess.model or "imported",
            "content": [{"text": "\n\n".join(reply)}],
            "thoughts": ([{"subject": "", "text": "\n".join(think)}] if think else []),
            "tokens": {"input": 0, "output": 0, "cached": 0, "thoughts": 0, "tool": 0, "total": 0},
        }
        out.append(json.dumps(gem, ensure_ascii=False))
    return out


def plan_write(home, sess: Session, budget: int | None, force: bool = False,
               titles: dict | None = None) -> dict:
    home = str(home)
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    title = (sess.title or "").strip() or (turns[0].prompt[:40] if turns else "")
    if titles and sess.source_id in titles:
        title = titles[sess.source_id]
    created = sess.created_at or int(time.time() * 1000)
    stats = {"messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
             "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps)}
    path = session_path(home, sid, created, sess.cwd)
    plan = {"path": path, "sid": sid, "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "lines": [], "title": _prefixed_title(sess.source, title),
            "cwd": sess.cwd, "created": created}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(os.path.join(home, "tmp")):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    have = _count_user_turns(path)
    plan["existingTurns"] = have
    if have >= len(turns) and not force:
        return {**plan, "action": "up-to-date"}
    tail = turns if (force or not os.path.exists(path)) else turns[have:]
    base = 0 if (force or not os.path.exists(path)) else have
    plan["lines"] = _turn_lines(sess, tail, created or 1787000000000)
    plan["create"] = not os.path.exists(path) or force
    return {**plan, "action": "append" if (os.path.exists(path) and not force) else "create"}


def apply_write(plan: dict) -> str:
    path = plan["path"]
    home = os.path.dirname(os.path.dirname(os.path.dirname(path)))  # …/tmp/_agentsync/chats → home
    cwd = plan.get("cwd")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    created = plan.get("created") or int(time.time() * 1000)
    from datetime import datetime as _dt, timezone as _tz
    iso = lambda ms: _dt.fromtimestamp(ms / 1000, tz=_tz.utc).isoformat().replace("+00:00", "Z")  # noqa: E731

    # 元数据时间用「现在」：gemini 启动会按 retention（默认 1d）清理过期会话文件，
    # 照搬源会话的旧时间戳会被当过期删除（实测坑）；真实时间保留在消息行里。
    now_ms = int(time.time() * 1000)
    if plan.get("create"):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            ctx = ("<session_context>\n导入会话（agentsync）。\n- **Workspace Directories:**\n"
                   f"  - {cwd or '(未知)'}\n</session_context>") if cwd else \
                  "<session_context>\n导入会话（agentsync）。</session_context>"
            f.write(json.dumps({
                "sessionId": plan["sid"], "projectHash": hashlib.sha256((cwd or "").encode()).hexdigest() if cwd else "",
                "startTime": iso(now_ms), "lastUpdated": iso(now_ms), "kind": "main",
            }, ensure_ascii=False) + "\n")
            f.write(json.dumps({"$set": {"messages": [{
                "id": str(uuid.uuid5(_NS, plan["sid"] + ":ctx")),
                "timestamp": iso(now_ms), "type": "user", "content": [{"text": ctx}],
            }]}}, ensure_ascii=False) + "\n")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for line in plan["lines"]:
            f.write(line + "\n")
        f.write(json.dumps({"$set": {"lastUpdated": iso(int(time.time() * 1000))}},
                           ensure_ascii=False) + "\n")
    return f"{plan['action']} {len(plan['lines'])} lines -> {path} ({plan['sid'][:8]})"
