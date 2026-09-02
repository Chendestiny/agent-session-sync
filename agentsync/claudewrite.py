r"""写入 Claude Code CLI（~/.claude/projects/<转义目录>/<sessionId>.jsonl）。

目录转义规则（本机 16 个真实目录实证）：cwd 中每个不属于 [A-Za-z0-9-] 的字符
都替换为 '-'（`C:\Users\alice` → `C--Users-alice`，`tmp.xxx` → `tmp-xxx`，
盘符大小写保留）。会话行内自带 cwd 字段，目录只是分桶。

行形状与真实会话对齐（读取器 `read_claude` 同口径）：
- user：message.content = 纯字符串（真实提问）
- assistant：content 块数组（text / tool_use）
- tool_result：放在下一条 user 行的 content 块里（tool_use_id 配对挂回）
- parentUuid 链完整（首行 null），sessionId/每行 uuid 均 UUID 形状（uuid5 幂等）

边界：thinking/reasoning 块不写入（claude 自家的 thinking 带 signature 校验，
外来块有被拒风险；文本与工具往返完整保留）。
"""
from __future__ import annotations

import glob
import json
import os
import re
import uuid
from datetime import datetime, timezone

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("a2c4e6d8-1b3f-4a5c-8e7d-9f0a1b2c3d4e")
_VERSION = "2.1.117"  # 写入行携带的版本号（对齐本机实测）


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"{sess.source}:{sess.source_id}"))


def munge_dir(cwd: str) -> str:
    """cwd → claude projects 子目录名（实证规则：非 [A-Za-z0-9-] 一律 '-'）。"""
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd or "")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _find_existing(projects_root: str, munged: str, sid: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(projects_root, "**", f"{sid}.jsonl"), recursive=True))
    return hits[0] if hits else None


def _count_turns(path: str) -> int:
    """数已有文件的真实提问轮（content 为非空字符串的 user 行）。"""
    n = 0
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user":
                continue
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            if msg.get("role") == "user" and isinstance(msg.get("content"), str) and msg["content"].strip():
                n += 1
    except OSError:
        pass
    return n


def _base(sid: str, cwd: str) -> dict:
    return {"cwd": cwd, "sessionId": sid, "version": _VERSION}


def _turn_records(sid: str, cwd: str, turn, idx: int, base_ms: int, parent: str | None) -> tuple[list[str], str | None]:
    """一个 IR 轮 → jsonl 行列表；返回 (行列表, 最后一个 uuid)。"""
    lines: list[str] = []
    ts = turn.time or (base_ms + idx * 1000)  # 轮级真实时间优先；未知回退确定性合成

    def emit(rec: dict, t: int) -> str:
        u = str(uuid.uuid5(_NS, f"{sid}:{idx}:{len(lines)}"))
        rec.update({"uuid": u, "parentUuid": parent_ref[0], "timestamp": _iso(t), "isSidechain": False})
        rec.update(_base(sid, cwd))
        parent_ref[0] = u
        lines.append(json.dumps(rec, ensure_ascii=False))
        return u

    parent_ref = [parent]
    # 用户提问
    emit({"type": "user", "message": {"role": "user", "content": turn.prompt}}, ts)
    # assistant 块 + 工具回传
    for step in turn.steps:
        content: list[dict] = []
        calls: list[tuple[str, str]] = []  # (call_id, name)
        for b in step.content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                content.append({"type": "text", "text": b["text"]})
            elif bt == "tool-call":
                try:
                    inp = json.loads(b["arguments"]) if isinstance(b.get("arguments"), str) else (b.get("arguments") or {})
                except json.JSONDecodeError:
                    inp = {"raw": str(b.get("arguments") or "")}
                cid = str(b.get("id") or f"call_{len(content)}")
                content.append({"type": "tool_use", "id": cid, "name": b.get("name") or "unknown", "input": inp})
                calls.append((cid, b.get("name") or "unknown"))
        if content:
            mid = f"msg_{uuid.uuid5(_NS, f'{sid}:{idx}:msg:{len(lines)}')}"
            emit({"type": "assistant", "message": {"id": mid, "type": "message", "role": "assistant",
                                                   "model": None, "content": content, "stop_reason": None}}, ts + 1)
        for tr in step.tool_results:
            text = "\n".join(
                x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
            )
            emit({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tr.tool_call_id, "content": text, "is_error": bool(tr.is_error)}
            ]}}, ts + 2)
    return lines, parent_ref[0]


def plan_write(projects_root: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """claude 版写入计划：create / append / up-to-date / skip / skip-deleted。

    titles 参数为对齐调用面保留（标题由 claude 自身 ai-title 机制管理，忽略）。
    """
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    cwd = sess.cwd or os.path.expanduser("~")
    munged = munge_dir(cwd)
    created = sess.created_at or int(datetime.now(timezone.utc).timestamp() * 1000)
    path = os.path.join(projects_root, munged, f"{sid}.jsonl")
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    plan = {"path": path, "lines": [], "stats": stats, "trimmed": trimmed, "sourceTurns": len(turns)}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(projects_root):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截；如确要恢复，先从墓碑文件移除该 id）"}
    existing = _find_existing(projects_root, munged, sid)
    if existing and not force:
        plan["path"] = existing
        have = _count_turns(existing)
        plan["existingTurns"] = have
        if have >= len(turns):
            return {**plan, "action": "up-to-date"}
        tail = turns[have:]
        # 追加：parentUuid 链接不上已有文件的末行（其 uuid 未知）→ 用 null 开新链，
        # claude 按 timestamp 排序展示，链断裂不影响读取器/续聊
        lines: list[str] = []
        parent: str | None = None
        for i, t in enumerate(tail):
            seg, parent = _turn_records(sid, cwd, t, have + i, created, parent)
            lines += seg
        return {**plan, "action": "append", "lines": lines}
    lines = []
    parent: str | None = None
    start = 0 if not existing else 0  # force 重写也从头生成
    for i, t in enumerate(turns):
        seg, parent = _turn_records(sid, cwd, t, start + i, created, parent)
        lines += seg
    return {**plan, "action": "create", "lines": lines}


def apply_write(plan: dict) -> str:
    if plan["action"] == "create":
        os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
        with open(plan["path"], "w", encoding="utf-8") as f:
            for ln in plan["lines"]:
                f.write(ln + "\n")
        return f"created {len(plan['lines'])} lines -> {plan['path']}"
    if plan["action"] == "append":
        with open(plan["path"], "a", encoding="utf-8") as f:
            for ln in plan["lines"]:
                f.write(ln + "\n")
        return f"appended {len(plan['lines'])} lines -> {plan['path']}"
    raise ValueError(f"unexpected action: {plan['action']}")
