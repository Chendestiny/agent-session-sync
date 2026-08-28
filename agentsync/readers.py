"""四家会话存储的读取器：zcode(db.sqlite) / hermes(state.db) / dsh(session.jsonl.zstd) / codex(rollout jsonl)。

全部只读打开（sqlite 走 mode=ro URI），输出统一的 model.Session IR。
解析规则移植自 dsh-chat-import 的 convert/{zcode,hermes,dsh,codex}.mjs。
"""
from __future__ import annotations

import glob
import io
import json
import os
import sqlite3
from datetime import datetime, timezone

from .model import Session, Step, ToolResult, Turn

# ── 通用工具 ─────────────────────────────────────────────────────────────


def _ms(value, default: int = 0) -> int:
    """时间戳归一为毫秒：数字按 秒(<1e11)/毫秒 判别，字符串尝试解析。"""
    if isinstance(value, (int, float)):
        v = float(value)
        if v < 1e11:
            v *= 1000
        return int(v)
    if isinstance(value, str) and value:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return default


def _parse_jsonl(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _zstd_decode_all(data: bytes) -> bytes:
    """解压多帧 zstd（dsh 每次落盘一帧，文件是多帧拼接）。"""
    import zstandard as zstd

    try:
        reader = zstd.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True)
        return reader.read()
    except TypeError:  # 旧版 zstandard 无 read_across_frames
        out = b""
        while data:
            dobj = zstd.ZstdDecompressor().decompressobj()
            out += dobj.decompress(data)
            if getattr(dobj, "unused_data", None):
                data = dobj.unused_data
            else:
                break
        return out


# ── zcode 读取器（~/.zcode/cli/db/db.sqlite）────────────────────────────


def read_zcode(db_path, include_subagents: bool = False) -> list[Session]:
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        sessions = []
        parent_filter = "WHERE parent_id IS NULL" if not include_subagents else ""
        session_rows = cur.execute(
            f"SELECT id, directory, title, time_created, time_updated FROM session {parent_filter} ORDER BY time_created"
        ).fetchall()
        for sid, directory, title, created, updated in session_rows:
            msgs = list(
                cur.execute(
                    "SELECT id, data FROM message WHERE session_id=? ORDER BY sequence", (sid,)
                )
            )
            if not msgs:
                continue
            # 一次取齐本会话全部 parts，按 message 分组、组内按 sequence 排序
            parts_by_msg: dict[str, list[dict]] = {}
            for msg_id, pseq, pdata in cur.execute(
                "SELECT message_id, sequence, data FROM part WHERE session_id=?",
                (sid,),
            ):
                try:
                    parts_by_msg.setdefault(msg_id, []).append((pseq, json.loads(pdata)))
                except json.JSONDecodeError:
                    continue
            for mid in parts_by_msg:
                parts_by_msg[mid] = [d for _, d in sorted(parts_by_msg[mid], key=lambda x: x[0])]

            turns: list[Turn] = []
            compaction_summaries: list[str] = []
            cur_turn: Turn | None = None
            for msg_id, mdata in msgs:
                try:
                    d = json.loads(mdata)
                except json.JSONDecodeError:
                    continue
                role = d.get("role")
                sem = d.get("semantics") if isinstance(d.get("semantics"), dict) else {}
                sem_kind = sem.get("kind")
                parts = parts_by_msg.get(msg_id, [])
                if role == "user":
                    # 只认真实用户提问：agent_runtime 注入类（todo_reminder 227 /
                    # background_notification / system_reminder 等，data 带 synthetic 标记）
                    # 一律不成为对话轮
                    if sem_kind == "compact_summary":
                        s = d.get("summary")
                        if isinstance(s, dict) and s.get("body"):
                            compaction_summaries.append(str(s["body"]))
                        elif isinstance(s, str) and s.strip():
                            compaction_summaries.append(s)
                        continue
                    if sem.get("origin") != "real_user" or sem_kind != "user_prompt":
                        continue
                    texts = [
                        p["text"].strip()
                        for p in parts
                        if p.get("type") == "text" and isinstance(p.get("text"), str) and p["text"].strip()
                    ]
                    prompt = "\n".join(texts)
                    if prompt and "<system-reminder>" not in prompt:
                        cur_turn = Turn(prompt=prompt)
                        turns.append(cur_turn)
                elif role == "assistant" and cur_turn is not None:
                    # timeline_event（模型切换分隔）等非 assistant_response 消息跳过；
                    # hidden 的 assistant_response 是 UI 折叠的真实输出（纯工具步骤），保留
                    if sem_kind not in (None, "assistant_response"):
                        continue
                    step = Step()
                    for p in parts:
                        pt = p.get("type")
                        if pt == "text" and isinstance(p.get("text"), str):
                            step.content.append({"type": "text", "text": p["text"]})
                        elif pt == "reasoning" and isinstance(p.get("text"), str):
                            step.content.append({"type": "reasoning", "text": p["text"]})
                        elif pt == "tool":
                            call_id = str(p.get("callID") or f"zc-{sid[-8:]}-{len(turns)}-{len(cur_turn.steps) + 1}")
                            state = p.get("state") if isinstance(p.get("state"), dict) else {}
                            inp = state.get("input")
                            mapped = {
                                "id": call_id,
                                "name": p.get("tool") or "unknown",
                                "arguments": inp if isinstance(inp, str) else json.dumps(inp if inp is not None else {}, ensure_ascii=False),
                            }
                            step.content.append({"type": "tool-call", **mapped})
                            step.tool_calls.append(mapped)
                            out = state.get("output")
                            out_text = out if isinstance(out, str) else ("" if out is None else json.dumps(out, ensure_ascii=False))
                            step.tool_results.append(
                                ToolResult(
                                    call_id,
                                    [{"type": "text", "text": out_text}],
                                    state.get("status") in ("failed", "error"),
                                )
                            )
                        elif pt == "file":
                            step.content.append({"type": "text", "text": f"[image: {p.get('filename') or 'unknown'}]"})
                        elif pt == "compaction":
                            s = p.get("summary")
                            if isinstance(s, dict) and s.get("body"):
                                compaction_summaries.append(str(s["body"]))
                    mid = d.get("modelID") or (d.get("model") or {}).get("modelID")
                    if mid:
                        step.model = str(mid)
                    cur_turn.steps.append(step)
            if not turns:
                continue
            sessions.append(
                Session(
                    source="zcode",
                    source_id=sid,
                    title=(title or "").strip(),
                    cwd=directory,
                    created_at=_ms(created),
                    updated_at=_ms(updated),
                    model=None,
                    summary="\n\n".join(compaction_summaries) or None,
                    turns=turns,
                    source_path=str(db_path),
                )
            )
        return sessions
    finally:
        con.close()


# ── hermes 读取器（%LOCALAPPDATA%/hermes/state.db）──────────────────────


def read_hermes(db_path, include_archived: bool = True) -> list[Session]:
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        where = "" if include_archived else "WHERE archived=0"
        sessions = []
        session_rows = cur.execute(
            f"SELECT id, title, cwd, started_at, model, archived FROM sessions {where}"
        ).fetchall()
        for sid, title, cwd, started, model, archived in session_rows:
            # cwd 兜底：hermes headless(-z) 模式不记录 cwd（交互模式正常）。
            # dsh 侧栏不渲染 _no-cwd 分区会话（实测），无 cwd 的会话必须兜底到
            # 用户主目录（≈ cmd 启动时的默认目录）才能可见——与 workbuddy 一致。
            cwd = cwd or os.path.expanduser("~")
            msgs = list(
                cur.execute(
                    "SELECT role, content, tool_call_id, tool_calls, reasoning, timestamp "
                    "FROM messages WHERE session_id=? ORDER BY id",
                    (sid,),
                )
            )
            if not msgs:
                continue
            turns: list[Turn] = []
            call_steps: dict[str, Step] = {}
            cur_turn: Turn | None = None
            last_active = 0
            for role, content, tool_call_id, tool_calls, reasoning, ts in msgs:
                t_ms = _ms(ts) if ts else 0
                if t_ms > last_active:
                    last_active = t_ms
                if role == "user":
                    text = _hermes_user_text(content)
                    if text:
                        cur_turn = Turn(prompt=text)
                        turns.append(cur_turn)
                elif role == "assistant" and cur_turn is not None:
                    step = Step()
                    if isinstance(reasoning, str) and reasoning.strip():
                        step.content.append({"type": "reasoning", "text": reasoning})
                    if isinstance(content, str) and content.strip():
                        step.content.append({"type": "text", "text": content})
                    elif isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") in ("text", "output_text") and isinstance(b.get("text"), str):
                                step.content.append({"type": "text", "text": b["text"]})
                    if isinstance(tool_calls, str) and tool_calls.strip():
                        try:
                            tc_list = json.loads(tool_calls)
                        except json.JSONDecodeError:
                            tc_list = []
                    else:
                        tc_list = tool_calls if isinstance(tool_calls, list) else []
                    for tc in tc_list or []:
                        if not isinstance(tc, dict):
                            continue
                        tc_id = tc.get("id") or tc.get("call_id")
                        fn = tc.get("function") or {}
                        name = fn.get("name") or tc.get("name")
                        if not isinstance(tc_id, str) or not isinstance(name, str):
                            continue
                        args = fn.get("arguments")
                        if not isinstance(args, str):
                            args = json.dumps(args if args is not None else {}, ensure_ascii=False)
                        mapped = {"id": tc_id, "name": name, "arguments": args}
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                    if not step.content and not step.tool_calls:
                        continue
                    cur_turn.steps.append(step)
                    for tc in step.tool_calls:
                        call_steps[tc["id"]] = step
                elif role == "tool":
                    tc_id = tool_call_id or ""
                    step = call_steps.get(tc_id)
                    if step is None:
                        continue
                    out_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                    step.tool_results.append(ToolResult(tc_id, [{"type": "text", "text": out_text or ""}]))
            if not turns:
                continue
            sessions.append(
                Session(
                    source="hermes",
                    source_id=str(sid),
                    title=(title or "").strip(),
                    cwd=cwd,
                    created_at=_ms(started),
                    updated_at=last_active or _ms(started),
                    model=model,
                    turns=turns,
                    source_path=str(db_path),
                )
            )
        return sessions
    finally:
        con.close()


def _hermes_user_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts).strip()
    return ""


# ── dsh 读取器（~/.dsh/sessions/--proj--/<id>/session.jsonl(.zstd)）─────


def read_dsh(sessions_root) -> list[Session]:
    root = str(sessions_root)
    sessions = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*", "session.jsonl*"))):
        try:
            raw = open(path, "rb").read()
            if path.endswith(".zstd"):
                raw = _zstd_decode_all(raw)
            lines = _parse_jsonl(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
        header = next((o for o in lines if o.get("type") == "session"), None)
        if not header:
            continue
        title = ""
        for o in reversed(lines):
            if o.get("type") == "session/title" and isinstance(o.get("data"), dict):
                title = str(o["data"].get("title") or "")
                break
        turns: list[Turn] = []
        cur_turn: Turn | None = None
        cur_step: Step | None = None
        call_steps: dict[str, Step] = {}
        for o in lines:
            t = o.get("type")
            data = o.get("data") if isinstance(o.get("data"), dict) else {}
            if t == "turn/start":
                cur_turn = Turn(prompt="")
                turns.append(cur_turn)
                cur_step = None
            elif t == "user/message":
                if cur_turn is not None and not cur_turn.prompt:
                    src = data.get("source") or {}
                    if src.get("kind") == "plugin":
                        continue  # 导入时注入的环境声明，重导出时跳过
                    blocks = data.get("content") if isinstance(data.get("content"), list) else []
                    text = "\n".join(
                        b.get("text", "")
                        for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ).strip()
                    if text:
                        cur_turn.prompt = text
            elif t == "assistant/message":
                msg = data.get("message") if isinstance(data.get("message"), dict) else {}
                if cur_turn is None:
                    cur_turn = Turn(prompt="")
                    turns.append(cur_turn)
                cur_step = Step()
                src = msg.get("source") or {}
                if isinstance(src.get("model"), str):
                    cur_step.model = src["model"]
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") in ("text", "reasoning") and isinstance(b.get("text"), str):
                        cur_step.content.append({"type": b["type"], "text": b["text"]})
                    elif b.get("type") == "tool-call":
                        mapped = {"id": b.get("id") or "", "name": b.get("name") or "unknown", "arguments": b.get("arguments") or "{}"}
                        cur_step.content.append({"type": "tool-call", **mapped})
                        cur_step.tool_calls.append(mapped)
                cur_turn.steps.append(cur_step)
            elif t == "tool/call":
                if cur_turn is None:
                    continue
                if cur_step is None:
                    cur_step = Step()
                    cur_turn.steps.append(cur_step)
                mapped = {"id": data.get("callId") or "", "name": data.get("name") or "unknown", "arguments": data.get("arguments") or "{}"}
                cur_step.content.append({"type": "tool-call", **mapped})
                cur_step.tool_calls.append(mapped)
                if mapped["id"]:
                    call_steps[mapped["id"]] = cur_step
            elif t == "tool/result":
                msg = data.get("message") if isinstance(data.get("message"), dict) else {}
                call_id = ""
                inner: list[dict] = []
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool-result":
                        call_id = b.get("toolCallId") or call_id
                        inner = b.get("content") or []
                step = call_steps.get(call_id)
                if step is None:
                    continue
                step.tool_results.append(ToolResult(call_id, inner, bool(msg.get("isError"))))
        turns = [t for t in turns if t.prompt or t.steps]
        if not turns:
            continue
        sessions.append(
            Session(
                source="dsh",
                source_id=str(header.get("id") or os.path.basename(os.path.dirname(path))),
                title=title.strip(),
                cwd=header.get("cwd"),
                created_at=_ms(header.get("createdAt")),
                model=None,
                turns=turns,
                source_path=path,
            )
        )
    return sessions


# ── WorkBuddy 读取器（~/.workbuddy[-ai]/：workbuddy.db + projects/<slug>/<id>.jsonl）──


def _workbuddy_slug(cwd: str) -> str:
    """cwd → projects 子目录 slug（WorkBuddy 自有方案，agentctxsync 核实）。

    盘符小写、其余原样、`\\`→`-`；盘根 `E:\\` → `e`（无尾横线）。
    """
    cwd = (cwd or "").replace("/", "\\")
    import re as _re

    m = _re.match(r"^([a-zA-Z]):[\\]?(.*)$", cwd)
    if m:
        drive, rest = m.group(1).lower(), m.group(2)
    else:
        drive, rest = "", cwd.lstrip("\\")
    slug = _re.sub(r"[\\]+", "-", rest)
    if not drive:
        return slug
    return drive if not slug else f"{drive}-{slug}"


def _wb_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _wb_parse_jsonl(path: str, session_id: str) -> tuple[list[dict], str | None]:
    """一个 WorkBuddy 会话文件 → (事件列表, ai-title)。"""
    events: list[dict] = []
    title = None
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return events, title
    for ln in lines:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "ai-title":
            if e.get("aiTitle") and not title:
                title = str(e["aiTitle"])
        elif t in ("message", "reasoning", "function_call", "function_call_result"):
            if isinstance(e.get("timestamp"), (int, float)):
                events.append(e)
    return events, title


def _strip_wb_injection(text: str) -> str:
    """剥离 WorkBuddy 注入的 user-context 块，只留真实用户输入。

    WorkBuddy 的 user message 会内嵌 <system-reminder data-role="user-context">
    块（OS/Shell/IDE/skills 列表，可达上万字符），真实提问在其中的
    <user_query>…</user_query> 标签里。策略：
      1) 无 system-reminder → 原样返回；
      2) 有 → 提取全部 <user_query> 内容为正文；没有该标签则丢弃整条（纯注入）。
    """
    if "<system-reminder" not in text:
        return text
    import re as _re

    queries = _re.findall(r"<user_query>(.*?)</user_query>", text, _re.S)
    if queries:
        return "\n".join(q.strip() for q in queries if q.strip())
    return ""


def read_workbuddy(home, include_deleted: bool = False) -> list[Session]:
    """WorkBuddy → IR。消息文件可能因项目移动存在多副本：扫全 projects/ 取并集，
    按 (type, role, timestamp) 去重后按时间排序（agentctxsync 2026-08-25 踩坑结论）。"""
    home = str(home)
    db = os.path.join(home, "workbuddy.db")
    projects = os.path.join(home, "projects")
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db.replace(chr(92), '/')}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        conds = [] if include_deleted else ["deleted_at IS NULL"]
        # playground 会话是 WorkBuddy 的"试验场"，其 UI 正式列表不显示——
        # 同步默认排除（对齐源侧可见集；实测 36 未删中 16 个 playground）
        if not include_deleted:
            conds.append("is_playground = 0")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = con.execute(
            f"SELECT id, cwd, title, custom_title, created_at, updated_at, model FROM sessions {where}"
        ).fetchall()
    finally:
        con.close()

    sessions: list[Session] = []
    for row in rows:
        sid = str(row["id"] or "")
        if not sid:
            continue
        cwd = row["cwd"] or os.path.expanduser("~")
        # 多副本并集：db 指向的副本优先，再扫全部 project 目录
        copies: list[str] = []
        primary = os.path.join(projects, _workbuddy_slug(str(cwd)), f"{sid}.jsonl")
        if os.path.exists(primary):
            copies.append(primary)
        try:
            for d in sorted(os.listdir(projects)):
                p = os.path.join(projects, d, f"{sid}.jsonl")
                if os.path.exists(p) and p not in copies:
                    copies.append(p)
        except OSError:
            pass

        raw_events: list[dict] = []
        seen: set[tuple] = set()
        title_from_events = None
        for cp in copies:
            evs, t = _wb_parse_jsonl(cp, sid)
            if not title_from_events and t:
                title_from_events = t
            for e in evs:
                key = (e.get("type"), e.get("role"), e.get("timestamp"))
                if key in seen:
                    continue
                seen.add(key)
                raw_events.append(e)
        raw_events.sort(key=lambda e: e.get("timestamp", 0))
        if not raw_events:
            continue

        turns: list[Turn] = []
        cur_turn: Turn | None = None
        pending: Step | None = None          # 当前 assistant 步（未遇 message(assistant) 前）
        reasoning_buf: list[str] = []
        call_steps: dict[str, Step] = {}

        def flush_pending():
            nonlocal pending, reasoning_buf
            if pending is not None and (pending.content or pending.tool_calls):
                for i, r in enumerate(reasoning_buf):
                    pending.content.insert(i, {"type": "reasoning", "text": r})
                if cur_turn is not None:
                    cur_turn.steps.append(pending)
            pending = None
            reasoning_buf = []

        for e in raw_events:
            t = e.get("type")
            if t == "message":
                role = e.get("role")
                if role == "user":
                    text = _wb_text(e.get("content")).strip()
                    text = _strip_wb_injection(text)
                    if not text:
                        continue
                    flush_pending()
                    cur_turn = Turn(prompt=text)
                    turns.append(cur_turn)
                elif role == "assistant" and cur_turn is not None:
                    step = pending or Step()
                    for r in reasoning_buf:
                        step.content.append({"type": "reasoning", "text": r})
                    reasoning_buf = []
                    text = _wb_text(e.get("content"))
                    if text:
                        step.content.append({"type": "text", "text": text})
                    pending = step
                    flush_pending()
            elif t == "reasoning":
                txt = _wb_text(e.get("rawContent") or e.get("content"))
                if txt.strip():
                    reasoning_buf.append(txt)
            elif t == "function_call" and cur_turn is not None:
                if pending is None:
                    pending = Step()
                call_id = str(e.get("callId") or "")
                args = e.get("arguments")
                args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                mapped = {"id": call_id, "name": e.get("name") or "unknown", "arguments": args_text}
                pending.content.append({"type": "tool-call", **mapped})
                pending.tool_calls.append(mapped)
                if call_id:
                    call_steps[call_id] = pending
            elif t == "function_call_result":
                call_id = str(e.get("callId") or "")
                step = call_steps.get(call_id) or pending
                if step is None:
                    continue
                out = e.get("output")
                text = out.get("text") if isinstance(out, dict) else _wb_text(out)
                step.tool_results.append(
                    ToolResult(call_id, [{"type": "text", "text": text or ""}], e.get("status") == "failed")
                )
        flush_pending()

        turns = [tu for tu in turns if tu.steps or tu.prompt]
        if not turns:
            continue
        title = row["title"] or row["custom_title"] or title_from_events or ""
        sessions.append(
            Session(
                source="workbuddy",
                source_id=sid,
                title=str(title).strip(),
                cwd=str(cwd),
                created_at=_ms(row["created_at"]),
                updated_at=_ms(row["updated_at"]),
                model=row["model"],
                turns=turns,
                source_path=primary if copies else db,
            )
        )
    return sessions


# ── codex 读取器（~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl）─────────


def read_codex(sessions_dir) -> list[Session]:
    out = []
    for path in sorted(glob.glob(os.path.join(str(sessions_dir), "**", "*.jsonl"), recursive=True)):
        try:
            lines = _parse_jsonl(open(path, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        source_id = ""
        cwd = None
        created = 0
        model = None
        subagent = False
        turns: list[Turn] = []
        cur_turn: Turn | None = None
        last_step: Step | None = None
        call_steps: dict[str, Step] = {}

        for rec in lines:
            env = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if env == "session_meta":
                if not source_id and isinstance(payload.get("id"), str):
                    source_id = payload["id"]
                if not cwd and isinstance(payload.get("cwd"), str):
                    cwd = payload["cwd"]
                if not created:
                    created = _ms(payload.get("timestamp") or rec.get("timestamp"))
                if payload.get("thread_source") == "subagent" or isinstance(payload.get("source"), dict) and payload["source"].get("subagent"):
                    subagent = True
            elif env == "turn_context":
                if not model and isinstance(payload.get("model"), str):
                    model = payload["model"]
            elif env == "response_item":
                pt = payload.get("type")
                if pt == "message" and payload.get("role") == "user":
                    parts = []
                    for b in payload.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "input_text" and isinstance(b.get("text"), str):
                            if not b["text"].startswith("<"):
                                parts.append(b["text"])
                    prompt = "\n".join(parts).strip()
                    if prompt:
                        cur_turn = Turn(prompt=prompt)
                        turns.append(cur_turn)
                        last_step = None
                elif pt == "message" and payload.get("role") == "assistant" and cur_turn is not None:
                    last_step = Step()
                    for b in payload.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "output_text" and isinstance(b.get("text"), str):
                            last_step.content.append({"type": "text", "text": b["text"]})
                    cur_turn.steps.append(last_step)
                elif pt in ("function_call", "custom_tool_call") and cur_turn is not None:
                    step = last_step or Step()
                    if step not in cur_turn.steps:
                        cur_turn.steps.append(step)
                    last_step = step
                    call_id = str(payload.get("call_id") or "")
                    if pt == "function_call":
                        args = payload.get("arguments")
                        args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                    else:
                        args_text = _codex_custom_args(payload.get("input"))
                    mapped = {"id": call_id, "name": payload.get("name") or "unknown", "arguments": args_text}
                    step.content.append({"type": "tool-call", **mapped})
                    step.tool_calls.append(mapped)
                    if call_id:
                        call_steps[call_id] = step
                elif pt in ("function_call_output", "custom_tool_call_output") and cur_turn is not None:
                    call_id = str(payload.get("call_id") or "")
                    step = call_steps.get(call_id)
                    if step is None:
                        continue
                    o = payload.get("output")
                    text = ""
                    if isinstance(o, str):
                        try:
                            p = json.loads(o)
                            text = p["output"] if isinstance(p, dict) and isinstance(p.get("output"), str) else o
                        except json.JSONDecodeError:
                            text = o
                    elif isinstance(o, dict) and isinstance(o.get("output"), str):
                        text = o["output"]
                    else:
                        text = json.dumps(o or "", ensure_ascii=False)
                    step.tool_results.append(ToolResult(call_id, [{"type": "text", "text": text}]))
        if subagent or not turns:
            continue
        out.append(
            Session(
                source="codex",
                source_id=source_id or os.path.basename(path),
                title="",
                cwd=cwd,
                created_at=created,
                updated_at=_ms(os.path.getmtime(path)),
                model=model,
                turns=turns,
                source_path=path,
            )
        )
    return out


def _codex_custom_args(inp) -> str:
    """custom_tool_call 的自由格式 input：尝试提取 JS 对象字面量转 JSON，失败原样。"""
    if not isinstance(inp, str):
        return json.dumps(inp if inp is not None else {}, ensure_ascii=False)
    text = inp.strip()
    start = text.find("{")
    if start == -1:
        return json.dumps(inp, ensure_ascii=False)
    depth = 0
    in_str = ""
    end = -1
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = ""
        elif ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end == -1:
        return json.dumps(inp, ensure_ascii=False)
    literal = text[start: end + 1]
    try:
        return json.dumps(json.loads(literal), ensure_ascii=False)
    except json.JSONDecodeError:
        return json.dumps(literal, ensure_ascii=False)
