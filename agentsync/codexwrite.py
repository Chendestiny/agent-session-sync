"""写入 codex CLI（~/.codex/sessions）：IR 会话 → rollout-*.jsonl，可被 codex resume 续聊。

写入配方参考 agentctxsync 的 deepseek-harness 适配器（其实机验证）：
- 目录 YYYY/MM/DD + 文件名 rollout-<ISO时间>-<uuid>.jsonl；**id 必须是 UUID 形状**
  才会被 codex 索引 → 本地 id = uuid5(固定命名空间, "<source>:<source_id>")，天然幂等
- 首行 session_meta{id,timestamp,model_provider,cwd}；行形状与真实 rollout 对齐：
  turn_context / message(input_text|output_text) / reasoning(summary_text) /
  function_call / function_call_output
- 增量：按已有文件的实际轮数整轮追加（时间戳从会话起点确定性合成，重跑稳定）
- 墓碑：独立于 dsh（同文件名 .agentsync-deleted.json，放在 codex sessions 根）
- 边界：空 prompt 轮（如 claude 助手开头的会话）无法用 input_text 表达，跳过该轮
"""
from __future__ import annotations

import glob
import json
import os
import uuid
from datetime import datetime, timezone

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

# agentsync 固定命名空间：任意值，但一经使用永不再变（幂等 id 的根基）
_NS = uuid.UUID("6f1b9a3e-2c4d-5e87-9a0b-3d6c7f8e9a01")


def local_id(sess: Session) -> str:
    """源会话 → 稳定 UUID（codex 只索引 UUID 形状的 id）。"""
    return str(uuid.uuid5(_NS, f"{sess.source}:{sess.source_id}"))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _line(ev_type: str, ms: int, payload: dict) -> str:
    return json.dumps({"timestamp": _iso(ms), "type": ev_type, "payload": payload}, ensure_ascii=False)


def _find_existing(sessions_root: str, lid: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(sessions_root, "**", f"rollout-*-{lid}.jsonl"), recursive=True))
    return hits[0] if hits else None


def _count_turns(path: str) -> int:
    """数已有文件的对话轮（与写入口径一致：非空且不以 < 开头的 input_text 用户消息）。"""
    n = 0
    try:
        for ln in open(path, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "response_item":
                continue
            p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if p.get("type") == "message" and p.get("role") == "user":
                for b in p.get("content") or []:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "input_text"
                        and isinstance(b.get("text"), str)
                        and b["text"].strip()
                        and not b["text"].startswith("<")
                    ):
                        n += 1
                        break
    except OSError:
        pass
    return n


def _turn_lines(sess: Session, turn, idx: int, base_ms: int) -> list[str]:
    """一个 IR 轮 → rollout 行（turn_context + user 消息 + 各响应项）。"""
    lines: list[str] = []
    ts = base_ms + idx * 1000  # 轮级时间戳：确定性毫秒步进
    tc: dict = {}
    if sess.cwd:
        tc["cwd"] = sess.cwd
    if sess.model:
        tc["model"] = sess.model
    lines.append(_line("turn_context", ts, tc))
    lines.append(
        _line("response_item", ts, {"type": "message", "role": "user",
                                    "content": [{"type": "input_text", "text": turn.prompt}]})
    )
    k = 0
    for step in turn.steps:
        for b in step.content:
            if not isinstance(b, dict):
                continue
            k += 1
            bt = b.get("type")
            if bt == "reasoning" and isinstance(b.get("text"), str) and b["text"].strip():
                lines.append(_line("response_item", ts + k,
                                   {"type": "reasoning", "summary": [{"type": "summary_text", "text": b["text"]}]}))
            elif bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                lines.append(_line("response_item", ts + k,
                                   {"type": "message", "role": "assistant",
                                    "content": [{"type": "output_text", "text": b["text"]}]}))
            elif bt == "tool-call":
                args = b.get("arguments")
                args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                lines.append(_line("response_item", ts + k,
                                   {"type": "function_call", "name": b.get("name") or "unknown",
                                    "arguments": args_text, "call_id": str(b.get("id") or "")}))
        for tr in step.tool_results:
            k += 1
            text = "\n".join(
                x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
            )
            lines.append(_line("response_item", ts + k,
                               {"type": "function_call_output", "call_id": tr.tool_call_id,
                                "output": json.dumps({"output": text}, ensure_ascii=False)}))
    return lines


def plan_write(sessions_root: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """codex 版写入计划：create / append / up-to-date / skip / skip-deleted。

    titles 参数为对齐 to-dsh 调用面保留（codex 无会话标题机制，忽略）。
    """
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    turns = [t for t in turns if (t.prompt or "").strip()]
    lid = local_id(sess)
    created = sess.created_at or int(datetime.now(timezone.utc).timestamp() * 1000)
    dt = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
    path = os.path.join(sessions_root, dt.strftime("%Y/%m/%d"), f"rollout-{dt.strftime('%Y-%m-%dT%H-%M-%S')}-{lid}.jsonl")
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    meta_line = _line("session_meta", created, {
        "id": lid, "session_id": lid, "timestamp": _iso(created),
        "model_provider": sess.model or "unknown", **({"cwd": sess.cwd} if sess.cwd else {}),
    })
    plan = {"path": path, "meta_line": meta_line, "lines": [], "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns)}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(sessions_root):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截；如确要恢复，先从墓碑文件移除该 id）"}
    existing = _find_existing(sessions_root, lid)
    if existing:
        path = existing
        plan["path"] = existing
        have = _count_turns(existing)
        plan["existingTurns"] = have
        if have >= len(turns) and not force:
            return {**plan, "action": "up-to-date"}
        base = 0 if force else have
        tail = turns if force else turns[have:]
        lines: list[str] = []
        for i, t in enumerate(tail):
            lines += _turn_lines(sess, t, base + i, created)
        return {**plan, "action": "create" if force else "append", "lines": lines}
    lines = []
    for i, t in enumerate(turns):
        lines += _turn_lines(sess, t, i, created)
    return {**plan, "action": "create", "lines": lines}


def apply_write(plan: dict) -> str:
    """落盘：create 整文件（含 session_meta 首行），append 仅追加行。"""
    if plan["action"] == "create":
        os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
        with open(plan["path"], "w", encoding="utf-8") as f:
            f.write(plan["meta_line"] + "\n")
            for ln in plan["lines"]:
                f.write(ln + "\n")
        return f"created {len(plan['lines'])} lines -> {plan['path']}"
    if plan["action"] == "append":
        with open(plan["path"], "a", encoding="utf-8") as f:
            for ln in plan["lines"]:
                f.write(ln + "\n")
        return f"appended {len(plan['lines'])} lines -> {plan['path']}"
    raise ValueError(f"unexpected action: {plan['action']}")
