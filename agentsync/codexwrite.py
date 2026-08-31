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
import sqlite3
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
    ts = turn.time or (base_ms + idx * 1000)  # 轮级真实时间优先；未知回退确定性合成
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


_META_CACHE: dict[str, dict | None] = {}


def _native_base_instructions(sessions_root: str) -> dict | None:
    """从本机原生 rollout 抄 base_instructions（与 CLI 版本一致的自述词，实测缺它 resume
    选择器不认外来会话）。"""
    if sessions_root in _META_CACHE:
        return _META_CACHE[sessions_root]
    found = None
    for p in sorted(glob.glob(os.path.join(sessions_root, "**", "*.jsonl"), recursive=True), key=os.path.getmtime, reverse=True):
        try:
            payload = (json.loads(open(p, encoding="utf-8", errors="replace").readline()) or {}).get("payload") or {}
        except (OSError, ValueError):
            continue
        if isinstance(payload.get("base_instructions"), dict):
            found = payload["base_instructions"]
            break
    _META_CACHE[sessions_root] = found
    return found


def _meta_line(sessions_root: str, lid: str, sess: Session, created: int) -> str:
    """session_meta 对齐原生字段集（originator/cli_version/source/thread_source/
    base_instructions 缺一不可，极简 meta 会被 resume 选择器无视）。"""
    payload = {
        "id": lid,
        "timestamp": _iso(created),
        "cwd": sess.cwd or os.path.expanduser("~"),
        "originator": "codex-tui",
        "cli_version": "0.137.0",  # 对齐本机 codex-cli；CLI 升级后建议同步
        "source": "cli",
        "thread_source": "user",
        "model_provider": sess.model or "custom",
    }
    bi = _native_base_instructions(sessions_root)
    if bi:
        payload["base_instructions"] = bi
    return json.dumps({"timestamp": _iso(created), "type": "session_meta", "payload": payload}, ensure_ascii=False)


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
    meta_line = _meta_line(sessions_root, lid, sess, created)
    plan = {"path": path, "meta_line": meta_line, "lines": [], "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "lid": lid, "created": created,
            "updated": sess.updated_at or created, "title": sess.title or "",
            "first_prompt": turns[0].prompt if turns else "", "cwd": sess.cwd,
            "model_provider": sess.model or "custom", "state_root": sessions_root}
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


def _state_db(sessions_root: str) -> str | None:
    """codex 状态库（resume 列表的真正数据源）：~/.codex/state_N.sqlite 取最大 N。"""
    import glob as _glob

    hits = _glob.glob(os.path.join(os.path.dirname(str(sessions_root)), "state_*.sqlite"))
    return max(hits) if hits else None


def _upsert_thread(sessions_root: str, plan: dict) -> str | None:
    """把导入会话登记进 state db 的 threads 表——resume picker 只读这里，不扫文件。"""
    db = _state_db(sessions_root)
    if not db:
        return None
    con = sqlite3.connect(db)
    try:
        cur = con.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(threads)")}
        if "rollout_path" not in cols:
            return None
        lid = plan["lid"]
        created_s = plan["created"] // 1000
        updated_s = (plan.get("updated") or plan["created"]) // 1000
        title = (plan.get("title") or plan.get("first_prompt") or "imported session")[:200]
        cwd = plan.get("cwd") or os.path.expanduser("~")
        cwd_win = "\\\\?\\" + cwd.replace("/", "\\") if os.name == "nt" else cwd
        # 抄一条现有行的 sandbox_policy/approval_mode（保持本机策略形态）
        pol = cur.execute("SELECT sandbox_policy, approval_mode FROM threads LIMIT 1").fetchone()
        sandbox_policy, approval_mode = (pol if pol else ('{"type":"read-only"}', "on-request"))
        row = {
            "id": lid,
            "rollout_path": plan["path"],
            "created_at": str(created_s),
            "updated_at": str(updated_s),
            "source": "cli",
            "model_provider": plan.get("model_provider") or "custom",
            "cwd": cwd_win,
            "title": title,
            "sandbox_policy": sandbox_policy,
            "approval_mode": approval_mode,
            "tokens_used": 0,
            "has_user_event": 1,
            "archived": 0,
            "cli_version": "0.137.0",
            "first_user_message": title,
            "memory_mode": "enabled",
            "thread_source": "user",
            "preview": title,
            "recency_at": str(updated_s),
            "recency_at_ms": str((plan.get("updated") or plan["created"])),
            "created_at_ms": str(plan["created"]),
            "updated_at_ms": str(plan.get("updated") or plan["created"]),
            "history_mode": "legacy",
        }
        row = {k: v for k, v in row.items() if k in cols}
        cur.execute("DELETE FROM threads WHERE id = ?", (lid,))
        keys = ", ".join(row)
        ph = ", ".join(["?"] * len(row))
        cur.execute(f"INSERT INTO threads ({keys}) VALUES ({ph})", list(row.values()))
        con.commit()
        return f"thread indexed -> {os.path.basename(db)}"
    finally:
        con.close()


def apply_write(plan: dict) -> str:
    """落盘：create 整文件（含 session_meta 首行），append 仅追加行；并登记 threads 索引。"""
    if plan["action"] == "create":
        os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
        with open(plan["path"], "w", encoding="utf-8") as f:
            f.write(plan["meta_line"] + "\n")
            for ln in plan["lines"]:
                f.write(ln + "\n")
        msg = f"created {len(plan['lines'])} lines -> {plan['path']}"
    elif plan["action"] == "append":
        with open(plan["path"], "a", encoding="utf-8") as f:
            for ln in plan["lines"]:
                f.write(ln + "\n")
        msg = f"appended {len(plan['lines'])} lines -> {plan['path']}"
    else:
        raise ValueError(f"unexpected action: {plan['action']}")
    if plan.get("state_root"):
        t = _upsert_thread(plan["state_root"], plan)
        if t:
            msg += f"; {t}"
    return msg
