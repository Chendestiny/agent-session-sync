"""各 agent 会话存储的读取器：zcode(db.sqlite) / hermes(state.db) / dsh(session.jsonl.zstd) /
codex(rollout jsonl) / workbuddy(db+jsonl) / claude(projects jsonl) / opencode(opencode.db)。

全部只读打开（sqlite 走 mode=ro URI），输出统一的 model.Session IR。
解析规则移植自 dsh-chat-import 的 convert/{zcode,hermes,dsh,codex}.mjs；
claude / opencode 参考 agentctxsync 的适配器与本机真实数据核对。
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote

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


def _zcode_tasks_index_path(db_path) -> str:
    """db.sqlite（~/.zcode/cli/db/）→ tasks-index.sqlite（~/.zcode/v2/）。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(str(db_path))))),
        "v2", "tasks-index.sqlite")


def _zcode_hidden_ids(tasks_index: str) -> set[str]:
    """zcode UI「删除/归档」的会话 id 集合。

    真相（0.16.5 实测）：UI 删除按钮调 RPC zcode-task.archiveTask，标记打在
    ~/.zcode/v2/tasks-index.sqlite 的 tasks 表（archived=1 / deleted=1），
    db.sqlite 的 session/message 完全不动——只读 db 会把「已删」当活会话同步出去。
    读不到/表不存在返回空集（行为不回退）。
    """
    try:
        con = sqlite3.connect(f"file:{str(tasks_index).replace(chr(92), '/')}?mode=ro", uri=True)
        rows = con.execute("SELECT task_id FROM tasks WHERE archived=1 OR deleted=1").fetchall()
        con.close()
        return {str(r[0]) for r in rows}
    except Exception:
        return set()


def read_zcode(db_path, include_subagents: bool = False, include_archived: bool = False,
               tasks_index: str | None = None) -> list[Session]:
    """include_archived=False：归档/已删会话不进同步——回收站不同步。

    双机制排除：① db.session.time_archived（库级归档列）② tasks-index.sqlite
    的 archived/deleted 标记（UI 删除的真实落点）。include_archived=True 全放出（审计用）。
    """
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        sessions = []
        conds = []
        if not include_subagents:
            conds.append("parent_id IS NULL")
        if not include_archived:
            conds.append("time_archived IS NULL")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        session_rows = cur.execute(
            f"SELECT id, directory, title, time_created, time_updated FROM session {where} ORDER BY time_created"
        ).fetchall()
        hidden: set[str] = set()
        if not include_archived:
            hidden = _zcode_hidden_ids(tasks_index or _zcode_tasks_index_path(db_path))
        for sid, directory, title, created, updated in session_rows:
            if sid in hidden:
                continue
            msgs = list(
                cur.execute(
                    "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY sequence", (sid,)
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
            for msg_id, mtime, mdata in msgs:
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
                        cur_turn = Turn(prompt=prompt, time=_ms(mtime))
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


def _is_agentsync_uuid5(sid) -> bool:
    """agentsync 反向写入器的幂等会话 id = uuid5(命名空间, "source:source_id")；
    各家原生 id 都不是 v5（hermes=时间戳串、codex=v7、workbuddy=v4、claude=v4、
    opencode=nanoid），v5 即导入——读取层跳过防 A→B→A 环形回流。"""
    try:
        return uuid.UUID(str(sid)).version == 5
    except (ValueError, AttributeError, TypeError):
        return False


def read_hermes(db_path, include_archived: bool = True, include_imports: bool = False) -> list[Session]:
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        where = "" if include_archived else "WHERE archived=0"
        sessions = []
        session_rows = cur.execute(
            f"SELECT id, title, cwd, started_at, model, archived FROM sessions {where}"
        ).fetchall()
        for sid, title, cwd, started, model, archived in session_rows:
            if not include_imports and _is_agentsync_uuid5(sid):
                continue  # agentsync 导入不回流（原生 id=时间戳串，非 v5）
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
                        cur_turn = Turn(prompt=text, time=_ms(ts) if ts else 0)
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


def _read_dsh_file(path: str) -> Session | None:
    """解析单个 dsh session.jsonl[.zstd] → Session（模块级：进程池 worker 需可 pickle）。"""
    try:
        raw = open(path, "rb").read()
        if path.endswith(".zstd"):
            raw = _zstd_decode_all(raw)
        lines = _parse_jsonl(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    header = next((o for o in lines if o.get("type") == "session"), None)
    if not header:
        return None
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
            cur_turn = Turn(prompt="", time=o.get("time") or 0)
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
                cur_turn = Turn(prompt="", time=o.get("time") or 0)
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
        return None
    return Session(
        source="dsh",
        source_id=str(header.get("id") or os.path.basename(os.path.dirname(path))),
        title=title.strip(),
        cwd=header.get("cwd"),
        created_at=_ms(header.get("createdAt")),
        model=None,
        turns=turns,
        source_path=path,
        subagent=(header.get("origin") == "subagent"),
    )


def _dsh_archived_ids(sessions_root) -> set[str]:
    """dsh UI 归档名单：storages/workspace.json 的 global.archivedSessionIds（软删，目录还在）。"""
    sp = os.path.normpath(os.path.join(str(sessions_root), "..", "storages", "workspace.json"))
    try:
        data = json.load(open(sp, encoding="utf-8"))
        arch = data.get("global", {}).get("archivedSessionIds")
        return set(arch) if isinstance(arch, list) else set()
    except (OSError, ValueError):
        return set()


def read_dsh(sessions_root, include_subagents: bool = False, include_archived: bool = False,
             include_imports: bool = False) -> list[Session]:
    """读全部 dsh 会话。文件级并行（进程池）：98 万行 JSONL 全量解析 22s → ~5s。

    worker 必须是模块级函数（Windows spawn pickle）；主入口无 __main__ 守卫等
    受限环境下自动回退串行，行为不变仅慢。
    include_subagents=False（默认）：origin=subagent 的子代理会话不返回——每次 agent
    委派都会新开一个会话目录（侧栏隐藏但磁盘全在），不排除会让对账虚高、外流污染
    其他目标；True = 审计/展示口径全放出。
    include_archived=False（默认）：workspace.json 归档名单里的会话不返回（对齐
    zcode/hermes「归档默认不同步」口径；dsh UI 归档=软删、目录仍在）；True = 展示/
    手术刀口径（webui 行级 🗑、prune --pick 可见可删）。
    include_imports=False（默认）：import-* 会话不返回——那是其他 agent 会话在 dsh
    里的副本，当源外流会造成 A→dsh→B 二次成环（正主在原生源里）；True = 展示/
    prune 口径（webui 卡片「导入/原生」拆分、prune --pick 浏览删除）。
    """
    root = str(sessions_root)
    paths = sorted(glob.glob(os.path.join(root, "*", "*", "session.jsonl*")))
    if not paths:
        return []
    workers = min(6, (os.cpu_count() or 4), len(paths))
    results = None
    if workers > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_read_dsh_file, paths, chunksize=8))
        except Exception:
            results = None  # 进程池不可用 → 串行兜底
    if results is None:
        results = [s for s in map(_read_dsh_file, paths) if s is not None]
    out = [s for s in results if s is not None]
    if not include_imports:
        out = [s for s in out if not s.source_id.startswith("import-")]
    if not include_subagents:
        out = [s for s in out if not s.subagent]
    if not include_archived:
        arch = _dsh_archived_ids(root)
        if arch:
            out = [s for s in out if s.source_id not in arch]
    return out


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


def read_workbuddy(home, include_deleted: bool = False, include_imports: bool = False) -> list[Session]:
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
        if not include_imports and _is_agentsync_uuid5(sid):
            continue  # agentsync 导入不回流（原生 id=v4）
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
                    cur_turn = Turn(prompt=text, time=int(e.get("timestamp") or 0))
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


def read_codex(sessions_dir, include_imports: bool = False) -> list[Session]:
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
                        cur_turn = Turn(prompt=prompt, time=_ms(rec.get("timestamp")))
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
        if subagent or not turns or (not include_imports and _is_agentsync_uuid5(source_id)):
            continue  # uuid5 = agentsync 导入不回流（原生 session_meta.id=v7）
        out.append(
            Session(
                source="codex",
                source_id=source_id or os.path.basename(path),
                title=_codex_title(turns),
                cwd=cwd,
                created_at=created,
                updated_at=_ms(os.path.getmtime(path)),
                model=model,
                turns=turns,
                source_path=path,
            )
        )
    return out


def _codex_title(turns) -> str:
    """codex 会话标题：用户习惯把文件路径贴在首问开头当上下文，同项目多会话
    截断后显示撞车（如 9×「D:\\BI_frontend\\src\\views\\aiagent\\ruleman…」）——
    剥掉盘符路径前缀（含末段分隔符）取真问题；剥完为空回退原始首问截断。"""
    raw = ""
    for t in turns:
        if (t.prompt or "").strip():
            raw = t.prompt.strip()
            break
    if not raw:
        return ""
    stripped = re.sub(r"^[A-Za-z]:[\\/](?:[^\s，。,.\n]*[\\/])+", "", raw).strip()
    return (stripped or raw)[:40]


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


# ── claude 读取器（~/.claude/projects/<cwd转义>/<sessionId>.jsonl）───────

_CLAUDE_STRIP_TAGS = (
    "system-reminder",
    "command-name",
    "command-message",
    "command-args",
    "command-contents",
    "local-command-stdout",
)


def _claude_strip_injection(text: str) -> str:
    """剥掉 Claude Code 注入的包装（本地命令回显 / 系统提醒）。"""
    for tag in _CLAUDE_STRIP_TAGS:
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL)
    return text.strip()


def _claude_user_text(content) -> str:
    """user 消息里的真实提问文本（注入剥离后为空 = 不是真实提问）。"""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    else:
        return ""
    text = _claude_strip_injection(text)
    # 本地命令包装行（其后紧跟的 local-command-stdout 已被剥掉）不是真实提问
    if text.startswith("Caveat: The messages below were generated"):
        return ""
    return text.strip()


def read_claude(projects_dir, include_imports: bool = False) -> list[Session]:
    """Claude Code CLI 会话（projects/<munged-cwd>/<sessionId>.jsonl）。

    - 只认 user/assistant 对话行；queue-operation/progress/attachment 等事件行跳过
    - isSidechain（子代理）与 isMeta 行跳过（同 zcode 语义过滤思路）
    - agentsync 导入不回流：sessionId 为 uuid5 形状整文件跳过（原生实测全 uuid4）；
      include_imports=True 时保留（展示/读回口径）
    - user 行里的 tool_result 块挂回发起它的 assistant step，不单独成轮
    - ai-title 行 → 会话标题；summary 行 → 压缩摘要
    - %TEMP% 下的冒烟会话（claude-ping/test、tmp-*）整文件跳过
    """
    temp_prefix = os.path.normpath(tempfile.gettempdir()).lower().rstrip("\\/") + os.sep
    out = []
    for path in sorted(glob.glob(os.path.join(str(projects_dir), "**", "*.jsonl"), recursive=True)):
        try:
            lines = _parse_jsonl(open(path, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        sid = os.path.splitext(os.path.basename(path))[0]
        # agentsync 导入不回流：claudewrite 铸的 sessionId 是 uuid5（claude 原生为 uuid4，
        # 本机 22 个原生实测零例外）；不过滤会把 A→claude→A 的副本再当原生源读回成环
        if _is_agentsync_uuid5(sid) and not include_imports:
            continue
        ai_title = ""
        summaries: list[str] = []
        cwd = None
        created = 0
        model = None
        turns: list[Turn] = []
        cur_turn: Turn | None = None
        last_step: Step | None = None
        call_steps: dict[str, Step] = {}

        for rec in lines:
            t = rec.get("type")
            if t == "ai-title":
                if isinstance(rec.get("aiTitle"), str) and rec["aiTitle"].strip():
                    ai_title = rec["aiTitle"].strip()
                continue
            if t == "summary":
                if isinstance(rec.get("summary"), str) and rec["summary"].strip():
                    summaries.append(rec["summary"].strip())
                continue
            if t not in ("user", "assistant"):
                continue
            if rec.get("isSidechain") or rec.get("isMeta"):
                continue
            if not cwd and isinstance(rec.get("cwd"), str):
                cwd = rec["cwd"]
            ts = _ms(rec.get("timestamp"))
            if not created and ts:
                created = ts
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            content = msg.get("content")
            role = msg.get("role") or t

            if role == "user":
                blocks = content if isinstance(content, list) else []
                # tool_result 块：挂回发起它的 assistant step，不单独成轮
                for b in blocks:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    call_id = str(b.get("tool_use_id") or "")
                    step = call_steps.get(call_id) or last_step
                    if step is None:
                        continue
                    inner = b.get("content")
                    if isinstance(inner, str):
                        rb = [{"type": "text", "text": inner}]
                    elif isinstance(inner, list):
                        rb = [
                            {"type": "text", "text": x["text"]}
                            for x in inner
                            if isinstance(x, dict) and isinstance(x.get("text"), str)
                        ]
                    else:
                        rb = []
                    step.tool_results.append(
                        ToolResult(call_id, rb or [{"type": "text", "text": ""}], bool(b.get("is_error")))
                    )
                text = _claude_user_text(content)
                if not text:
                    continue  # 纯工具回传 / 注入行：不成轮
                cur_turn = Turn(prompt=text, time=_ms(rec.get("timestamp")))
                turns.append(cur_turn)
                last_step = None
                call_steps = {}
            else:  # assistant
                if cur_turn is None:
                    cur_turn = Turn(prompt="")  # 罕见：会话以助手消息开头
                    turns.append(cur_turn)
                blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
                step = Step()
                m = msg.get("model")
                if isinstance(m, str) and m:
                    step.model = m
                    model = model or m
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                        step.content.append({"type": "text", "text": b["text"]})
                    elif bt == "thinking" and isinstance(b.get("thinking"), str) and b["thinking"].strip():
                        step.content.append({"type": "reasoning", "text": b["thinking"]})
                    elif bt == "tool_use":
                        call_id = str(b.get("id") or f"cl-{sid[-8:]}-{len(turns)}-{len(cur_turn.steps) + 1}")
                        mapped = {
                            "id": call_id,
                            "name": b.get("name") or "unknown",
                            "arguments": json.dumps(b.get("input") if b.get("input") is not None else {}, ensure_ascii=False),
                        }
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                        if call_id:
                            call_steps[call_id] = step
                if step.content or step.tool_calls:
                    cur_turn.steps.append(step)
                    last_step = step

        turns = [tu for tu in turns if tu.steps or tu.prompt]
        if not turns:
            continue
        cwd = cwd or os.path.expanduser("~")
        if os.path.normpath(cwd).lower().startswith(temp_prefix):
            continue  # %TEMP% 下的冒烟会话
        out.append(
            Session(
                source="claude",
                source_id=sid,
                title=ai_title,
                cwd=cwd,
                created_at=created,
                updated_at=_ms(os.path.getmtime(path)),
                model=model,
                summary="\n\n".join(summaries) or None,
                turns=turns,
                source_path=path,
            )
        )
    return out


# ── opencode 读取器（%LOCALAPPDATA%/opencode/opencode.db）───────────────


def _oc_model(raw) -> str | None:
    """opencode session.model 可能是 'claude-sonnet-4' 或含 modelID 的 JSON。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(d, dict):
            mid = d.get("modelID") or (d.get("model") or {}).get("modelID") if isinstance(d.get("model"), dict) else d.get("modelID")
            if isinstance(mid, str) and mid:
                return mid
        return None
    return raw


def _oc_import_ids(db_path) -> set[str]:
    """opencode 防回流旁路清单：写入器在数据根维护 .agentsync-imports.json。
    opencode 不能用 uuid5 版本位判别——桌面版（ai.opencode.desktop）原生 id 也是
    uuidv5 形状（2026-09-02 实测 9/9 误杀），导入标记只能靠清单。"""
    mf = os.path.join(os.path.dirname(os.path.abspath(str(db_path))), ".agentsync-imports.json")
    try:
        data = json.load(open(mf, encoding="utf-8"))
        ids = data.get("ids") if isinstance(data, dict) else data
        return {str(x) for x in ids or []}
    except (OSError, ValueError):
        return set()


def read_opencode(db_path, include_imports: bool = False) -> list[Session]:
    """opencode（CLI 与桌面版共享同一 SQLite）：session / message / part 三表。

    解析规则参考 agentctxsync 的 opencode 适配器（读取方向）：
    - message.data JSON 的 role；agent-switched/model-switched/compaction/step 跳过
    - part.data：text/input_text/output_text → 文本；reasoning → 思考；
      tool{tool, state.input/state.output} → 调用+回传同挂一个 step（同 zcode 思路）
    """
    p = str(db_path)
    con = sqlite3.connect(f"file:{p.replace(chr(92), '/')}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        import_ids: set[str] = set()
        if not include_imports:
            import_ids = _oc_import_ids(db_path)
        rows = con.execute(
            "SELECT id, directory, title, model, time_created, time_updated FROM session ORDER BY time_created"
        ).fetchall()
        sessions: list[Session] = []
        for row in rows:
            sid = str(row["id"] or "")
            if not sid:
                continue
            if sid in import_ids:
                continue  # agentsync 导入不回流（旁路清单；桌面版原生 id 也是 v5，形状判别不可用）
            msg_rows = con.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created",
                (sid,),
            ).fetchall()
            turns: list[Turn] = []
            cur_turn: Turn | None = None
            model = _oc_model(row["model"])
            for mrow in msg_rows:
                try:
                    data = json.loads(mrow["data"]) if mrow["data"] else {}
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                role = data.get("role") or data.get("type") or "assistant"
                if role in ("agent-switched", "model-switched", "compaction", "step"):
                    continue
                m_model = data.get("model")
                if isinstance(m_model, dict):
                    mid = m_model.get("modelID")
                    if isinstance(mid, str) and mid:
                        model = model or mid
                part_rows = con.execute(
                    "SELECT data FROM part WHERE message_id=? ORDER BY time_created",
                    (mrow["id"],),
                ).fetchall()
                if role == "user":
                    texts = []
                    for prow in part_rows:
                        try:
                            part = json.loads(prow["data"]) if prow["data"] else {}
                        except json.JSONDecodeError:
                            continue
                        if part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
                            texts.append(part["text"])
                    text = _claude_strip_injection("\n".join(texts)).strip()
                    if not text:
                        continue
                    cur_turn = Turn(prompt=text, time=mrow["time_created"] or 0)
                    turns.append(cur_turn)
                    continue
                if cur_turn is None:
                    cur_turn = Turn(prompt="")
                    turns.append(cur_turn)
                step = Step()
                for prow in part_rows:
                    try:
                        part = json.loads(prow["data"]) if prow["data"] else {}
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(part, dict):
                        continue
                    ptype = part.get("type")
                    if ptype in ("text", "output_text") and isinstance(part.get("text"), str) and part["text"].strip():
                        step.content.append({"type": "text", "text": part["text"]})
                    elif ptype == "reasoning" and isinstance(part.get("text"), str) and part["text"].strip():
                        step.content.append({"type": "reasoning", "text": part["text"]})
                    elif ptype == "tool":
                        tname = str(part.get("tool") or "unknown")
                        state = part.get("state") if isinstance(part.get("state"), dict) else {}
                        inp = state.get("input")
                        args_text = inp if isinstance(inp, str) else json.dumps(inp if inp is not None else {}, ensure_ascii=False)
                        call_id = str(part.get("id") or f"oc-{sid[-8:]}-{len(turns)}-{len(cur_turn.steps) + 1}")
                        mapped = {"id": call_id, "name": tname, "arguments": args_text}
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                        out = state.get("output")
                        out_text = out if isinstance(out, str) else ("" if out is None else json.dumps(out, ensure_ascii=False))
                        step.tool_results.append(
                            ToolResult(call_id, [{"type": "text", "text": out_text}], state.get("status") in ("failed", "error"))
                        )
                if step.content or step.tool_calls:
                    cur_turn.steps.append(step)
            turns = [tu for tu in turns if tu.steps or tu.prompt]
            if not turns:
                continue
            sessions.append(
                Session(
                    source="opencode",
                    source_id=sid,
                    title=(row["title"] or "").strip(),
                    cwd=(row["directory"] or "").strip() or os.path.expanduser("~"),
                    created_at=_ms(row["time_created"]),
                    updated_at=_ms(row["time_updated"]),
                    model=model,
                    turns=turns,
                    source_path=p,
                )
            )
        return sessions
    finally:
        con.close()


def _qoder_strip_wrapper(text: str) -> str:
    """剥 Qoder 首问的 <user_query> 传输包装（整段被包才剥，内嵌不动）。"""
    t = text.strip()
    if t.startswith("<user_query>") and t.endswith("</user_query>"):
        return t[len("<user_query>"): -len("</user_query>")].strip()
    return text


def read_qoder(qoder_home, vscdb_path=None) -> list[Session]:
    """Qoder（阿里 AI IDE，VS Code 系）会话。两跳（2026-09 实测布局）：

    - 索引：globalStorage/state.vscdb 的 ItemTable['aicoding.questTaskListSnapshot']
      = {folders: {cwd: {tasks: [{id, name, status, createTime, updatedAtTimestamp…}]}}}
    - 正文：~/.qoder/cache/projects/<proj>-<hash>/conversation-history/<id前缀>/<id>.jsonl
      行形态 {"role","message":{"content":[{type,text},…]}}（旧版 ~/.qwen/projects 已弃用不读）
    """
    home = str(qoder_home)
    if vscdb_path is None:
        vscdb_path = os.path.join(os.environ.get("APPDATA", ""), "Qoder",
                                  "User", "globalStorage", "state.vscdb")
    try:
        con = sqlite3.connect(f"file:{str(vscdb_path).replace(chr(92), '/')}?mode=ro", uri=True)
        row = con.execute("SELECT value FROM ItemTable WHERE key = 'aicoding.questTaskListSnapshot'").fetchone()
        con.close()
        snap = json.loads(row[0]) if row else {}
    except (sqlite3.Error, OSError, ValueError):
        return []
    out: list[Session] = []
    for folder, info in (snap.get("folders") or {}).items():
        for t in info.get("tasks") or []:
            tid = str(t.get("id") or "")
            if not tid:
                continue
            # 文件名 = id 截前 8 位（实测 task-63023fd1… → task-630/task-630.jsonl）；
            # 兼容全 id 形态以防版本差异
            pref = tid[:8]
            hits = (glob.glob(os.path.join(home, "cache", "projects", "*", "conversation-history", pref, f"{pref}.jsonl"))
                    or glob.glob(os.path.join(home, "cache", "projects", "*", "conversation-history", "**", f"{tid}.jsonl"), recursive=True))
            if not hits:
                continue  # 索引在、正文无（如执行现场已清）——跳过
            turns: list[Turn] = []
            cur: Turn | None = None
            last_step: Step | None = None
            try:
                lines = _parse_jsonl(open(hits[0], encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            for rec in lines:
                role = rec.get("role")
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
                blocks = [b for b in (msg.get("content") or []) if isinstance(b, dict)]
                if role == "user":
                    texts = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                    cur = Turn(prompt=_qoder_strip_wrapper(texts), steps=[], time=_ms(rec.get("timestamp")))
                    turns.append(cur)
                    last_step = None
                elif role == "assistant" and cur is not None:
                    step = Step(content=[], tool_calls=[], tool_results=[])
                    for b in blocks:
                        if b.get("type") == "text" and b.get("text"):
                            step.content.append({"type": "text", "text": b["text"]})
                        elif b.get("type") in ("tool_call", "tool_use") and b.get("name"):
                            mapped = {"id": str(b.get("id") or ""), "name": b["name"],
                                      "arguments": b.get("arguments") if isinstance(b.get("arguments"), str)
                                      else json.dumps(b.get("arguments") or {}, ensure_ascii=False)}
                            step.content.append({"type": "tool-call", **mapped})
                            step.tool_calls.append(mapped)
                    if step.content or step.tool_calls:
                        cur.steps.append(step)
                        last_step = step
            if not turns:
                continue
            out.append(
                Session(
                    source="qoder",
                    source_id=tid,
                    title=str(t.get("name") or "").strip(),
                    cwd=str(folder).replace("\\", "/") or None,
                    created_at=int(t.get("createTime") or 0),
                    updated_at=int(t.get("updatedAtTimestamp") or t.get("lastUserQueryAt") or 0),
                    model=None,
                    turns=turns,
                    source_path=hits[0],
                )
            )
    return out


# OpenClaw 首问注入前缀（官方无标题机制，标题只能从首问推导）：
# Control UI 把用户输入包在 Sender 元数据块 + [星期 日期] 戳后面；
# 子代理会话首问带 [Subagent Context] 包装、任务在 [Subagent Task]: 后
_OC_SENDER_RE = re.compile(r"^Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*", re.S)
_OC_STAMP_RE = re.compile(r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[^\]]*\]\s*")
_OC_PATH_RE = re.compile(r"^[A-Za-z]:[\\/](?:[^\s，。,.\n]*[\\/])+")


def _openclaw_title(turns) -> str:
    """标题推导：逐轮剥离注入前缀后取首个非空行 [:40]。

    - 剥离：Sender 元数据块、[星期 日期 GMT+8] 戳、[Subagent Task] 包装、盘符路径前缀
    - 跳过（当空问看下一轮）：/new 控制条、System: 执行回显、OpenClaw runtime
      context 内部事件行（转发进主会话的子代理完成事件也是这种形态）
    """
    for t in turns:
        raw = (t.prompt or "").strip()
        if not raw:
            continue
        raw = _OC_SENDER_RE.sub("", raw, count=1)
        raw = _OC_STAMP_RE.sub("", raw, count=1)
        if raw.startswith("[Subagent Context]"):
            m = re.search(r"\[Subagent Task\]:\s*(.+)", raw, re.S)
            raw = (m.group(1) if m else "").strip()
        if not raw or raw.startswith(("A new session was started", "System:", "OpenClaw runtime context")):
            continue
        line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        line = _OC_PATH_RE.sub("", re.sub(r"\s+", " ", line))
        if line:
            return line[:40]
    return "（无有效提问）"  # 整场只有 /new 控制条/内部事件行（实测 4 条）


def read_openclaw(openclaw_home, include_subagents: bool = False) -> list[Session]:
    """OpenClaw（开源个人 AI 助手）会话。**新旧双版本兼容**（2026-09-01 实测：
    旧版树 85 会话 / 新版树 84 会话均读通）：

    - 新版（≥2026.7.2，官方 PR #98236 起 json→sqlite）：正典 =
      `agents/main/agent/openclaw-agent.sqlite` 的 `transcript_events`
      (session_id, seq, event_json)，event_json 与 jsonl 行同形；升级用
      `doctor --session-sqlite import` 迁移，迁完活跃 jsonl 被移走
    - 旧版（≤2026.7.1）：无 agent sqlite，全量 = `agents/main/sessions/` 下
      活跃 `<uuid>.jsonl` + `.jsonl.reset.<ISO>` 轮转快照（大量旧对话只在
      快照里；同 uuid 多份取字典序最新一份）
    - 共存规则：sqlite 有的 id 以正典为准，jsonl/快照只补正典缺的——同一
      函数覆盖纯旧版机器、新版机器、迁移残留三种形态
    - 子代理会话默认排除（include_subagents=False）：判定=首问含
      `[Subagent Context] You are running as a subagent`（转发的完成事件
      `source: subagent` 会出现在主会话里，不能当标记，对齐 zcode/codex/dsh）
    - 行形态：`session` 头 {version,id,timestamp,cwd} → `message` {id,parentId,
      timestamp, message{role, content[{type:text|thinking|toolCall}]}}；工具结果
      是独立 `role=toolResult` 行（toolCallId 配对挂回发起 step）。
    """
    base = str(openclaw_home)

    def _parse_events(events, sid, path, out):
        turns: list[Turn] = []
        cur: Turn | None = None
        last_step: Step | None = None
        cwd = None
        created = 0
        model = None
        call_steps: dict[str, Step] = {}
        for rec in events:
            t = rec.get("type")
            if t == "session":
                if isinstance(rec.get("cwd"), str):
                    cwd = rec["cwd"]
                created = _ms(rec.get("timestamp")) or created
                if isinstance(rec.get("id"), str) and rec["id"]:
                    sid = rec["id"]
            elif t == "model_change":
                model = rec.get("modelId") or rec.get("model") or model
            elif t != "message":
                continue
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            role = msg.get("role")
            blocks = [b for b in (msg.get("content") or []) if isinstance(b, dict)]
            ts = _ms(rec.get("timestamp"))
            if role == "user":
                texts = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                cur = Turn(prompt=texts, steps=[], time=ts)
                turns.append(cur)
                last_step = None
            elif role == "assistant" and cur is not None:
                step = Step(content=[], tool_calls=[], tool_results=[])
                for b in blocks:
                    if b.get("type") == "text" and b.get("text"):
                        step.content.append({"type": "text", "text": b["text"]})
                    elif b.get("type") == "thinking" and (b.get("thinking") or b.get("text")):
                        step.content.append({"type": "reasoning", "text": b.get("thinking") or b.get("text")})
                    elif b.get("type") == "toolCall" and b.get("name"):
                        args = b.get("arguments")
                        args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                        mapped = {"id": str(b.get("id") or ""), "name": b["name"], "arguments": args_text}
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                        if mapped["id"]:
                            call_steps[mapped["id"]] = step
                if step.content or step.tool_calls:
                    cur.steps.append(step)
                    last_step = step
            elif role == "toolResult" and cur is not None:
                texts = [{"type": "text", "text": b.get("text", "")}
                         for b in blocks if b.get("type") == "text" and b.get("text")]
                if not texts:
                    continue
                details = msg.get("details") if isinstance(msg.get("details"), dict) else {}
                is_err = details.get("status") not in (None, "completed", "success")
                target = call_steps.get(str(msg.get("toolCallId") or "")) or last_step
                if target is not None:
                    target.tool_results.append(ToolResult(str(msg.get("toolCallId") or ""), texts, is_err))
        if turns:
            is_sub = "[Subagent Context] You are running as a subagent" in (turns[0].prompt or "")
            if is_sub and not include_subagents:
                return
            last_ts = max((t.time for t in turns if t.time), default=0) or created
            out.append(
                Session(
                    source="openclaw",
                    source_id=sid,
                    title=_openclaw_title(turns),
                    cwd=cwd,
                    created_at=created,
                    updated_at=last_ts,
                    model=model,
                    turns=turns,
                    source_path=path,
                    subagent=is_sub,
                )
            )

    out: list[Session] = []
    seen_ids: set[str] = set()

    # ① 正典：agent SQLite 的 transcript_events
    db = os.path.join(base, "agents", "main", "agent", "openclaw-agent.sqlite")
    if os.path.isfile(db):
        try:
            con = sqlite3.connect(f"file:{db.replace(chr(92), '/')}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT session_id, seq, event_json FROM transcript_events ORDER BY session_id, seq"
            ).fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        by_sid: dict[str, list] = {}
        for sid, _seq, ej in rows:
            try:
                by_sid.setdefault(str(sid), []).append(json.loads(ej))
            except (ValueError, TypeError):
                continue
        for sid, events in by_sid.items():
            before = len(out)
            _parse_events(events, sid, db, out)
            if len(out) > before:
                seen_ids.add(out[-1].source_id)

    # ② 兜底：sessions/ 残留 jsonl（活跃 + reset 孤儿快照）
    sdir = os.path.join(base, "agents", "main", "sessions")
    files: dict[str, str] = {}
    latest_reset: dict[str, str] = {}
    if os.path.isdir(sdir):
        for fn in sorted(os.listdir(sdir)):
            if fn.endswith(".jsonl"):
                files[fn[:-6]] = os.path.join(sdir, fn)
            else:
                m = re.match(r"^(.+)\.jsonl\.reset\.\d{4}", fn)
                if m and m.group(1) not in files:
                    old = latest_reset.get(m.group(1))
                    if old is None or fn > os.path.basename(old):
                        latest_reset[m.group(1)] = os.path.join(sdir, fn)
        for k, v in latest_reset.items():
            files.setdefault(k, v)
        for sid, path in files.items():
            if sid in seen_ids:
                continue  # 正典已有，快照不重复
            try:
                events = _parse_jsonl(open(path, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            _parse_events(events, sid, path, out)
    return out


# ── cursor / trae（VS Code 系 AI IDE）─────────────────────────────────
# globalStorage/state.vscdb 的 cursorDiskKV 表（实测 Cursor 743 行）：
#   composerData:<cid>  = 会话头（createdAt 毫秒、isArchived；text/conversationMap 恒空壳）
#   bubbleId:<cid>:<bid> = 消息（type 1=user / 2=assistant；text；toolFormerData=
#     {name/tool, params/rawArgs, result, toolCallId}；workspaceUris 反解 cwd）
# 关联只靠键前缀（conversationMap 实测 19/19 全空，不能信）。
_AT_PATH_RE = re.compile(r"^(@\S+\s+)+")
_DRIVE_PATH2_RE = re.compile(r"^[A-Za-z]:[\\/](?:[^\s，。,.\n]*[\\/])+")


def _cursor_title(turns) -> str:
    """首问剥 @文件引用 与盘符路径前缀后 [:40]（Cursor 习惯 `@src/xx.vue 提问`）。"""
    for t in turns:
        raw = (t.prompt or "").strip()
        if not raw:
            continue
        raw = _AT_PATH_RE.sub("", raw)
        raw = _DRIVE_PATH2_RE.sub("", raw).strip()
        if raw:
            return raw[:40]
    return ""


def _read_vscode_kv_chat(db_path, source: str, include_archived: bool = False) -> list[Session]:
    con = sqlite3.connect(f"file:{str(db_path).replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        try:
            rows = con.execute("SELECT key, value FROM cursorDiskKV").fetchall()
        except sqlite3.Error:
            return []  # 表不存在（Trae 旧版/他布局）——空结果，不炸
        composers: dict[str, dict] = {}
        bubbles: dict[str, list] = {}
        for k, v in rows:
            k = str(k or "")
            if not k or not v:
                continue
            try:
                d = json.loads(v if isinstance(v, str) else v.decode("utf-8", "replace"))
            except (ValueError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            if k.startswith("composerData:"):
                composers[k.split(":", 1)[1]] = d
            elif k.startswith("bubbleId:"):
                parts = k.split(":", 2)
                if len(parts) == 3:
                    bubbles.setdefault(parts[1], []).append(d)
        out: list[Session] = []
        for cid, comp in composers.items():
            if not include_archived and comp.get("isArchived"):
                continue
            bs = sorted(bubbles.get(cid, []), key=lambda b: _ms(b.get("createdAt")) or 0)
            turns: list[Turn] = []
            cur: Turn | None = None
            cwd = None
            for b in bs:
                text = (b.get("text") or "").strip()
                ts = _ms(b.get("createdAt"))
                if not cwd and isinstance(b.get("workspaceUris"), list) and b["workspaceUris"]:
                    u = str(b["workspaceUris"][0])
                    if u.startswith("file:///"):
                        cwd = unquote(u[len("file:///"):]) or None
                if b.get("type") == 1:  # user
                    if not text:
                        continue
                    cur = Turn(prompt=text, steps=[], time=ts)
                    turns.append(cur)
                elif b.get("type") == 2 and cur is not None:  # assistant
                    step = Step(content=[], tool_calls=[], tool_results=[])
                    if text:
                        step.content.append({"type": "text", "text": text})
                    tf = b.get("toolFormerData")
                    for call in (tf if isinstance(tf, list) else ([tf] if isinstance(tf, dict) else [])):
                        if not isinstance(call, dict):
                            continue
                        name = str(call.get("name") or call.get("tool") or "tool")
                        args = call.get("params") if call.get("params") is not None else call.get("rawArgs")
                        args_text = args if isinstance(args, str) else json.dumps(args if args is not None else {}, ensure_ascii=False)
                        mapped = {"id": str(call.get("toolCallId") or ""), "name": name, "arguments": args_text}
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                        res = call.get("result")
                        res_text = res if isinstance(res, str) else ("" if res is None else json.dumps(res, ensure_ascii=False))
                        step.tool_results.append(ToolResult(str(call.get("toolCallId") or ""),
                                                            [{"type": "text", "text": res_text}], False))
                    if step.content or step.tool_calls:
                        cur.steps.append(step)
            if not turns:
                continue
            created = int(comp.get("createdAt") or 0)
            out.append(
                Session(
                    source=source,
                    source_id=cid,
                    title=_cursor_title(turns),
                    cwd=cwd,
                    created_at=created,
                    updated_at=max((t.time for t in turns if t.time), default=0) or created,
                    model=None,
                    turns=turns,
                    source_path=str(db_path),
                )
            )
        return out
    finally:
        con.close()


def read_cursor(db_path, include_archived: bool = False) -> list[Session]:
    """Cursor（AI IDE）会话：globalStorage state.vscdb 的 cursorDiskKV。"""
    return _read_vscode_kv_chat(db_path, "cursor", include_archived)


def read_trae(db_path, include_archived: bool = False) -> list[Session]:
    """Trae（字节 AI IDE，VS Code 系 fork）会话。2026-09-03 重装实测：当前 Trae CN
    的 globalStorage state.vscdb 里**没有** cursorDiskKV 表（旧「布局对齐 Cursor」
    是空壳年代的误判），会话正典在 ModularData/ai-agent/database.db 且整库自加密
    （无 SQLite 头，WAL 帧亦密文，非标准 SQLCipher）——离线读取被加密阻断。
    本引擎仅对旧版/国际版 Cursor 布局有效，读当前 CN 版返回空，详见
    docs/agents/trae.md。"""
    return _read_vscode_kv_chat(db_path, "trae", include_archived)


def _mm_is_import(sid: str) -> bool:
    """minimax 导入判别：id=mvs_<32hex>，原生是 uuidv4，agentsync 铸的是 uuidv5。"""
    h = sid[4:] if sid.startswith("mvs_") else ""
    if len(h) != 32:
        return False
    try:
        return uuid.UUID(h).version == 5
    except ValueError:
        return False


def read_minimax(minimax_home, include_archived: bool = False,
                 include_imports: bool = False,
                 include_subagents: bool = False) -> list[Session]:
    """MiniMax Code（Xiaomi MiniMax 的 agentic coding workspace，内置 agent Mavis）。

    ~/.minimax/v2/sqlite/runtime-state.sqlite：v2 为注册表+列存投影布局，
    local_runtime_sessions 存会话头（title/workspace_dir/archived/parent_session_id），
    local_runtime_message_rows 存消息（data_json 内 msg_content / thinking_content，
    按 turn_id 分轮）。内置 agent（mavis/explore/worker/verifier）的 Main 引导壳
    没有消息行，天然不出现；子代理 = parent_session_id 非空，默认排除。
    导入判别 = mvs_id 的 uuid 版本位（原生 v4 / agentsync v5）。
    工具调用形态待实机出现后核验（本机首条会话纯文本）。
    """
    db = os.path.join(str(minimax_home), "v2", "sqlite", "runtime-state.sqlite")
    if not os.path.isfile(db):
        return []
    con = sqlite3.connect(f"file:{db.replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        heads: dict[str, dict] = {}
        for sid, title, cwd, created, updated, arch, parent in con.execute(
            "SELECT session_id, title, workspace_dir, created_at_ms, updated_at_ms,"
            " archived, parent_session_id FROM local_runtime_sessions"
        ):
            heads[sid] = {"title": title, "cwd": cwd, "created": int(created or 0),
                          "updated": int(updated or 0), "archived": bool(arch),
                          "parent": parent}
        # 消息按自增 id 即插入序读入，按会话分桶
        msgs: dict[str, list] = {sid: [] for sid in heads}
        for sid, role, turn_id, ts, data_json in con.execute(
            "SELECT session_id, role, turn_id, created_at_ms, data_json"
            " FROM local_runtime_message_rows ORDER BY id"
        ):
            if sid in msgs:
                msgs[sid].append((role, turn_id, int(ts or 0), data_json))
    finally:
        con.close()

    out: list[Session] = []
    for sid, rows in msgs.items():
        h = heads.get(sid, {})
        if not rows:
            continue  # 引导壳/空会话
        if h.get("archived") and not include_archived:
            continue
        if h.get("parent") and not include_subagents:
            continue
        if not include_imports and _mm_is_import(sid):
            continue  # agentsync 导入不回流（原生 mvs id 是 uuidv4）
        turns: list[Turn] = []
        cur: Turn | None = None
        for role, turn_id, ts, data_json in rows:
            try:
                d = json.loads(data_json) if data_json else {}
            except json.JSONDecodeError:
                d = {}
            if role == "user":
                text = d.get("msg_content")
                text = text if isinstance(text, str) else ""
                if text:
                    cur = Turn(prompt=text, time=ts)
                    turns.append(cur)
            elif role == "assistant" and cur is not None:
                step = Step()
                think = d.get("thinking_content")
                if isinstance(think, str) and think.strip():
                    step.content.append({"type": "reasoning", "text": think})
                body = d.get("msg_content")
                if isinstance(body, str) and body.strip():
                    step.content.append({"type": "text", "text": body})
                elif isinstance(body, list):
                    for b in body:
                        if isinstance(b, dict) and b.get("type") in ("text", "output_text") \
                                and isinstance(b.get("text"), str):
                            step.content.append({"type": "text", "text": b["text"]})
                if step.content:
                    cur.steps.append(step)
        if not turns:
            continue
        created = h.get("created") or (turns[0].time if turns else 0)
        out.append(
            Session(
                source="minimax",
                source_id=sid,
                title=h.get("title") or "",
                cwd=h.get("cwd"),
                created_at=created,
                updated_at=h.get("updated") or max((t.time for t in turns), default=0) or created,
                model=None,
                turns=turns,
                source_path=db,
                subagent=bool(h.get("parent")),
            )
        )
    return out


def _pi_sessions_root(pi_home) -> str:
    """pi 会话根：<home>/agent/sessions；兼容直接绑了 sessions 目录本身的情况
    （PI_CODING_AGENT_SESSION_DIR 探测路径）。"""
    h = str(pi_home)
    cand = os.path.join(h, "agent", "sessions")
    if os.path.isdir(cand):
        return cand
    return h


def read_pi(pi_home, include_imports: bool = False) -> list[Session]:
    """Pi Agent Harness（@earendil-works/pi-coding-agent；minimax 的 pi-agent 同源运行时）。

    ~/.pi/agent/sessions/<cwd编码>/<时间戳>_<uuid>.jsonl：事件流 JSONL（v3）——
    首行 type=session（id/cwd/timestamp），后续 model_change/thinking_level_change/
    message 事件。message.message 形状（源码 packages/ai/src/types.ts）：
    user={role,content:[{type:text,text}]}；assistant={content:[text/thinking/toolCall]，
    thinking 块字段是 thinking，toolCall.arguments 是对象}；toolResult={toolCallId,
    toolName,content:[text]}。assistant stopReason=error 且无内容时跳过。
    """
    root = _pi_sessions_root(pi_home)
    out: list[Session] = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        try:
            lines = _parse_jsonl(open(path, encoding="utf-8").read())
        except OSError:
            continue
        head = next((o for o in lines if o.get("type") == "session"), None)
        if head is None:
            continue
        turns: list[Turn] = []
        cur: Turn | None = None
        call_steps: dict[str, Step] = {}
        last_ms = 0
        for ev in lines:
            if ev.get("type") != "message":
                continue
            m = ev.get("message") or {}
            role = m.get("role")
            ts = _ms(m.get("timestamp"), _ms(ev.get("timestamp")))
            if ts > last_ms:
                last_ms = ts
            if role == "user":
                texts = [b.get("text", "") for b in (m.get("content") or [])
                         if isinstance(b, dict) and b.get("type") == "text"]
                text = "\n".join(t for t in texts if t)
                if not text:
                    continue
                cur = Turn(prompt=text, time=ts)
                turns.append(cur)
            elif role == "assistant" and cur is not None:
                step = Step(model=m.get("model"))
                for b in m.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                        step.content.append({"type": "text", "text": b["text"]})
                    elif bt == "thinking" and isinstance(b.get("thinking"), str) and b["thinking"].strip():
                        step.content.append({"type": "reasoning", "text": b["thinking"]})
                    elif bt == "toolCall":
                        args = b.get("arguments")
                        args_text = args if isinstance(args, str) else json.dumps(
                            args if args is not None else {}, ensure_ascii=False)
                        mapped = {"id": str(b.get("id") or ""), "name": str(b.get("name") or "unknown"),
                                  "arguments": args_text}
                        step.content.append({"type": "tool-call", **mapped})
                        step.tool_calls.append(mapped)
                if not step.content and not step.tool_calls:
                    continue  # error/空回复
                cur.steps.append(step)
                for tc in step.tool_calls:
                    call_steps[tc["id"]] = step
            elif role == "toolResult":
                step = call_steps.get(str(m.get("toolCallId") or ""))
                if step is None:
                    continue
                texts = [b.get("text", "") for b in (m.get("content") or [])
                         if isinstance(b, dict) and b.get("type") == "text"]
                step.tool_results.append(ToolResult(
                    str(m.get("toolCallId") or ""),
                    [{"type": "text", "text": "\n".join(t for t in texts if t)}],
                    False))
        if not turns:
            continue
        if not include_imports and _is_agentsync_uuid5(head.get("id")):
            continue  # agentsync 导入不回流（原生 id 是 uuidv7）
        created = _ms(head.get("timestamp")) or turns[0].time
        cwd = head.get("cwd")
        title = (turns[0].prompt or "").strip()[:40]
        out.append(
            Session(
                source="pi",
                source_id=str(head.get("id") or os.path.basename(path)),
                title=title,
                cwd=cwd,
                created_at=created,
                updated_at=last_ms or created,
                model=None,
                turns=turns,
                source_path=path,
            )
        )
    return out


_GEMINI_CTX_SKIP = ("<session_context>", "<environment_context>", "<system_reminder>")


def _gem_block_text(blocks) -> str:
    """gemini content 块取文本：实测有 {text} 字典 part（源码形状）与**纯字符串碎片**
    （流式分片，中转站/不同版本路径）两种形态——统一拼出完整文本。"""
    parts = []
    for b in blocks or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            parts.append(b["text"])
        elif isinstance(b, str):
            parts.append(b)
    return "".join(parts)


def _gemini_cwd_from_context(text: str) -> str | None:
    m = re.search(r"Workspace Directories:\*\*\s*\r?\n\s*-\s*(.+)", text or "")
    return m.group(1).strip() if m else None


def read_gemini(gemini_home, include_imports: bool = False) -> list[Session]:
    """Gemini CLI（Google）：~/.gemini/tmp/<项目标识>/chats/session-*.json(.jsonl)。

    行形状（源码 chatRecordingService）：首行会话头（kind=main，sessionId/startTime），
    初始 `$set:{messages}` 快照，此后每条消息是**裸对象行**（id/timestamp/type/content），
    其间穿插 `$set:{lastUpdated|memoryScratchpad}`。消息 type=user（含 <session_context>
    注入块，读时跳过并从中反解 cwd）/ gemini（模型回复：content=[{text}]，
    thoughts=[{subject,text}] 思维链，model/tokens 元数据）。
    """
    root = os.path.join(str(gemini_home), "tmp")
    out: list[Session] = []
    for path in sorted(glob.glob(os.path.join(root, "*", "chats", "session-*.json*"))):
        try:
            if path.endswith(".jsonl"):
                lines = _parse_jsonl(open(path, encoding="utf-8").read())
            else:
                lines = [json.load(open(path, encoding="utf-8"))]
        except (OSError, ValueError):
            continue
        head = next((o for o in lines if isinstance(o, dict) and o.get("kind") == "main"), {})
        msgs: list = []
        cwd = None
        for o in lines:
            if not isinstance(o, dict):
                continue
            if "$set" in o:
                s = o["$set"] or {}
                if isinstance(s.get("messages"), list):
                    msgs = list(s["messages"])
                continue
            if "kind" in o or "messages" in o or "lastUpdated" in o:
                continue
            if o.get("type") in ("user", "gemini") and ("content" in o or "thoughts" in o):
                msgs.append(o)
        turns: list[Turn] = []
        cur: Turn | None = None
        last_ms = 0
        for m in msgs:
            if not isinstance(m, dict):
                continue
            ts = _ms(m.get("timestamp"))
            if ts > last_ms:
                last_ms = ts
            role = m.get("type")
            if role == "user":
                text = _gem_block_text(m.get("content"))
                if not text.strip():
                    continue
                if text.lstrip().startswith(_GEMINI_CTX_SKIP):
                    if cwd is None:
                        cwd = _gemini_cwd_from_context(text)
                    continue
                cur = Turn(prompt=text, time=ts)
                turns.append(cur)
            elif role == "gemini" and cur is not None:
                step = Step(model=m.get("model"))
                for th in m.get("thoughts") or []:
                    if isinstance(th, dict) and isinstance(th.get("text"), str) and th["text"].strip():
                        step.content.append({"type": "reasoning", "text": th["text"]})
                body = _gem_block_text(m.get("content"))
                if body.strip():
                    step.content.append({"type": "text", "text": body})
                if step.content:
                    cur.steps.append(step)
        if not turns:
            continue
        if not include_imports and _is_agentsync_uuid5(head.get("sessionId")):
            continue  # agentsync 导入不回流（原生 sessionId 是 uuidv4）
        created = _ms(head.get("startTime")) or turns[0].time
        sid = str(head.get("sessionId") or os.path.basename(path))
        out.append(
            Session(
                source="gemini",
                source_id=sid,
                title=(turns[0].prompt or "").strip()[:40],
                cwd=cwd,
                created_at=created,
                updated_at=last_ms or created,
                model=None,
                turns=turns,
                source_path=path,
            )
        )
    return out


def read_cline(cline_home, include_imports: bool = False) -> list[Session]:
    """Cline（VS Code 扩展）：globalStorage/saoudrizwan.claude-dev/tasks/<ts>/。

    ui_messages.json = UI 事件流（say=task 首问 / user_feedback 追问开轮；
    reasoning 思维链；completion_result 终答；checkpoint/api_req 等噪音忽略，
    partial=true 的流式碎块跳过）。cwd 从 api_conversation_history.json 的
    `Working Directory (d:\\…)` 反解；model 取 task_metadata.model_usage。
    工具类 say（tool/command_execution 等）待真实编码任务出现后核验升级。
    """
    root = os.path.join(str(cline_home), "tasks")
    if not include_imports:
        from .clinewrite import import_ids as _cl_import_ids
        imports = _cl_import_ids(cline_home)
    else:
        imports = set()
    out: list[Session] = []
    for tdir in sorted(glob.glob(os.path.join(root, "*"))):
        if os.path.basename(tdir) in imports:
            continue  # agentsync 导入不回流（任务 id=时间戳无形状，靠旁路清单）
        ui_path = os.path.join(tdir, "ui_messages.json")
        if not os.path.isfile(ui_path):
            continue
        try:
            ui = json.load(open(ui_path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(ui, list):
            continue
        cwd = None
        model = None
        api_path = os.path.join(tdir, "api_conversation_history.json")
        if os.path.isfile(api_path):
            try:
                api = json.load(open(api_path, encoding="utf-8"))
                for am in api if isinstance(api, list) else []:
                    for b in (am.get("content") or []):
                        if isinstance(b, dict) and isinstance(b.get("text"), str):
                            m = re.search(r"Working Directory \(([^)]+)\)", b["text"])
                            if m:
                                cwd = m.group(1).strip().replace("/", "\\")
                                break
                    if cwd:
                        break
            except (OSError, ValueError):
                pass
        try:
            meta = json.load(open(os.path.join(tdir, "task_metadata.json"), encoding="utf-8"))
            mu = (meta.get("model_usage") or [{}])[0]
            model = mu.get("model_id")
        except (OSError, ValueError, IndexError):
            pass
        turns: list[Turn] = []
        cur: Turn | None = None
        last_ms = 0
        for ev in ui:
            if not isinstance(ev, dict) or ev.get("partial"):
                continue
            ts = _ms(ev.get("ts"))
            if ts > last_ms:
                last_ms = ts
            say = ev.get("say")
            text = ev.get("text")
            if not isinstance(text, str):
                text = ""
            if say in ("task", "user_feedback") and text.strip():
                cur = Turn(prompt=text, time=ts)
                turns.append(cur)
            elif say == "reasoning" and cur is not None and text.strip():
                if not cur.steps:
                    cur.steps.append(Step(model=model))
                cur.steps[0].content.append({"type": "reasoning", "text": text})
            elif say == "completion_result" and cur is not None and text.strip():
                step = Step(model=model)
                step.content.append({"type": "text", "text": text})
                cur.steps.append(step)
        if not turns:
            continue
        created = _ms(ui[0].get("ts")) if isinstance(ui[0], dict) else turns[0].time
        out.append(
            Session(
                source="cline",
                source_id=os.path.basename(tdir),
                title=(turns[0].prompt or "").strip()[:40],
                cwd=cwd,
                created_at=created or turns[0].time,
                updated_at=last_ms or created,
                model=model,
                turns=turns,
                source_path=ui_path,
            )
        )
    return out


def load_sources(which, p):
    """统一 fan-out：{source: [Session]}（存储缺失的源跳过）。"""
    loaded: dict[str, list[Session]] = {}
    if "zcode" in which and p.zcode_db:
        loaded["zcode"] = read_zcode(p.zcode_db)
    if "hermes" in which and p.hermes_db:
        loaded["hermes"] = read_hermes(p.hermes_db, include_archived=False)
    if "dsh" in which and p.dsh_sessions:
        loaded["dsh"] = read_dsh(p.dsh_sessions)
    if "codex" in which and p.codex_sessions:
        loaded["codex"] = read_codex(p.codex_sessions)
    if "workbuddy" in which and p.workbuddy_home:
        loaded["workbuddy"] = read_workbuddy(p.workbuddy_home)
    if "claude" in which and p.claude_projects:
        loaded["claude"] = read_claude(p.claude_projects)
    if "opencode" in which and p.opencode_db:
        loaded["opencode"] = read_opencode(p.opencode_db)
    if "qoder" in which and p.qoder_home and p.qoder_vscdb:
        loaded["qoder"] = read_qoder(p.qoder_home, p.qoder_vscdb)
    if "openclaw" in which and p.openclaw_home:
        loaded["openclaw"] = read_openclaw(p.openclaw_home)
    if "cursor" in which and p.cursor_global_db:
        loaded["cursor"] = read_cursor(p.cursor_global_db)
    if "trae" in which and p.trae_global_db:
        loaded["trae"] = read_trae(p.trae_global_db)
    if "minimax" in which and p.minimax_home:
        loaded["minimax"] = read_minimax(p.minimax_home)
    if "pi" in which and p.pi_home:
        loaded["pi"] = read_pi(p.pi_home)
    if "gemini" in which and p.gemini_home:
        loaded["gemini"] = read_gemini(p.gemini_home)
    if "cline" in which and p.cline_home:
        loaded["cline"] = read_cline(p.cline_home)
    return loaded
