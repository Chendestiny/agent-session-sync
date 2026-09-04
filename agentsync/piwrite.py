r"""写入 Pi Agent（~/.pi）。

配方（源码 jsonl/repo.ts + 真数据核验）：
- 会话文件：agent/sessions/<目录编码>/<ISO时间戳>_<id>.jsonl，
  目录编码 = '--' + cwd 去首分隔符后 [/\\:]→'-' + '--'（源码 sessionDirectoryName）
- 事件流（v3）：首行 {type:session,version:3,id,timestamp,cwd}，随后
  model_change + 每轮 message 事件（user/assistant/toolResult；
  assistant.content=[text/thinking/toolCall]，thinking 块字段=thinking、
  toolCall.arguments=对象）
- id = uuid5（原生 uuidv7，版本位判别导入）；文件名由 id+createdAt 确定 → 幂等
- 增量：按已有 user message 事件数整轮追加
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd43018")


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"pi:{sess.source}:{sess.source_id}"))


def _prefixed_title(source: str, title: str) -> str:
    if title and not (title.startswith("[") and "] " in title[:14]):
        title = f"[{source}] {title}"
    return title


def _dir_name(cwd: str) -> str:
    """源码 sessionDirectoryName：'--' + 去首分隔符 + [/\\:]→'-' + '--'。"""
    c = cwd.replace("/", "\\")
    if c[:1] in ("/", "\\"):
        c = c[1:]
    return "--" + c.replace("\\", "-").replace(":", "-") + "--"


def session_path(home, sid: str, created_ms: int, cwd: str) -> str:
    root = os.path.join(str(home), "agent", "sessions")
    if not os.path.isdir(root):
        root = str(home)  # 直接绑了 sessions 目录的形态
    stamp = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat().replace(":", "-").replace(".", "-")
    return os.path.join(root, _dir_name(cwd), f"{stamp}_{sid}.jsonl")


def _count_user_turns(path: str) -> int:
    n = 0
    try:
        for l in open(path, encoding="utf-8"):
            if not l.strip():
                continue
            try:
                d = json.loads(l)
            except ValueError:
                continue
            if d.get("type") == "message" and (d.get("message") or {}).get("role") == "user":
                n += 1
    except OSError:
        pass
    return n


def _last_event_id(path: str) -> str | None:
    """追加模式的链头：文件末行事件的 id（pi 是 append-only 树，靠 parentId 链解析上下文）。"""
    last = None
    try:
        for l in open(path, encoding="utf-8"):
            if l.strip():
                last = l
    except OSError:
        return None
    if not last:
        return None
    try:
        return json.loads(last).get("id")
    except ValueError:
        return None


def _turn_events(sess: Session, turns, start_ms: int, base_idx: int, parent_id: str | None) -> list[str]:
    out = []
    ms = start_ms
    seq = base_idx * 100

    def ev(payload: dict) -> None:
        nonlocal parent_id
        payload["parentId"] = parent_id
        parent_id = payload["id"]
        out.append(json.dumps(payload, ensure_ascii=False))

    for i, t in enumerate(turns):
        ms = t.time or (start_ms + i * 1000)
        ev({
            "type": "message",
            "id": uuid.uuid5(_NS, f"{sess.source_id}:{base_idx + i}:u").hex[:8],
            "timestamp": datetime.fromtimestamp(
                ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "message": {"role": "user", "content": [{"type": "text", "text": t.prompt}],
                        "timestamp": ms},
        })
        for st in t.steps:
            seq += 1
            blocks = []
            for b in st.content:
                if b.get("type") == "reasoning" and b.get("text"):
                    blocks.append({"type": "thinking", "thinking": b["text"]})
                elif b.get("type") == "text" and b.get("text"):
                    blocks.append({"type": "text", "text": b["text"]})
            for tc in st.tool_calls:
                try:
                    args = json.loads(tc.get("arguments") or "{}")
                except ValueError:
                    args = {"raw": tc.get("arguments") or ""}
                blocks.append({"type": "toolCall", "id": tc.get("id") or "",
                               "name": tc.get("name") or "unknown", "arguments": args})
            if not blocks:
                continue
            a_ms = ms + seq
            ev({
                "type": "message",
                "id": uuid.uuid5(_NS, f"{sess.source_id}:{base_idx + i}:a:{seq}").hex[:8],
                "timestamp": datetime.fromtimestamp(
                    a_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "message": {"role": "assistant", "model": sess.model or "imported",
                            "content": blocks, "stopReason": "stop", "timestamp": a_ms,
                            "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                                      "totalTokens": 0,
                                      "cost": {"input": 0, "output": 0, "cacheRead": 0,
                                               "cacheWrite": 0, "total": 0}}},
            })
            for tr in st.tool_results:
                seq += 1
                r_ms = a_ms + 1
                ev({
                    "type": "message",
                    "id": uuid.uuid5(_NS, f"{sess.source_id}:{base_idx + i}:r:{seq}").hex[:8],
                    "timestamp": datetime.fromtimestamp(
                        r_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "message": {"role": "toolResult", "toolCallId": tr.tool_call_id,
                                "toolName": "tool",
                                "content": [{"type": "text", "text": "\n".join(
                                    b.get("text", "") for b in tr.content
                                    if b.get("type") == "text")[:2000]}]},
                })
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
    cwd = sess.cwd or os.path.expanduser("~")
    stats = {"messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
             "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps)}
    path = session_path(home, sid, created, cwd)
    plan = {"path": path, "sid": sid, "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "lines": [], "cwd": cwd, "created": created,
            "title": _prefixed_title(sess.source, title), "model": sess.model}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    root = os.path.dirname(os.path.dirname(path))
    if sess.source_id in load_tombstones(root):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    have = _count_user_turns(path)
    plan["existingTurns"] = have
    if have >= len(turns) and not force:
        return {**plan, "action": "up-to-date"}
    tail = turns if (force or not os.path.exists(path)) else turns[have:]
    base = 0 if (force or not os.path.exists(path)) else have
    # 链头：create 从 model_change 起链；append 接文件末行
    parent0 = "agentsync" if (force or not os.path.exists(path)) else _last_event_id(path)
    plan["lines"] = _turn_events(sess, tail, created or 1787000000000, base, parent0)
    plan["create"] = not os.path.exists(path) or force
    return {**plan, "action": "append" if (os.path.exists(path) and not force) else "create"}


def apply_write(plan: dict) -> str:
    path = plan["path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if plan.get("create"):
        from datetime import datetime as _dt, timezone as _tz
        iso = _dt.fromtimestamp(plan["created"] / 1000, tz=_tz.utc).isoformat().replace("+00:00", "Z")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "session", "version": 3, "id": plan["sid"],
                                "timestamp": iso, "cwd": plan["cwd"]}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"type": "model_change", "id": "agentsync", "parentId": None,
                                "timestamp": iso, "provider": "imported",
                                "modelId": plan.get("model") or "imported"},
                               ensure_ascii=False) + "\n")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for line in plan["lines"]:
            f.write(line + "\n")
    return f"{plan['action']} {len(plan['lines'])} events -> {path} ({plan['sid'][:8]})"
