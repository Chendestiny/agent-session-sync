"""dsh 写入器：IR → dsh 会话事件流 → session.jsonl.zstd（多帧、带校验和）。

落盘规格按本机 dsh 0.1.1-rc 系的 dsh-session-persistence-jsonl 逆向：
  路径   <DSH_HOME>/sessions/projectKey(cwd)/encodeSegment(id)/session.jsonl.zstd
  文件   = header 帧 + 若干事件帧（每帧独立可解压、ZSTD_c_checksumFlag=1）
  header = {"type":"session","version":0,"id","createdAt"(ms),"cwd","delegationDepth":0}
事件合成纪律移植自 dsh-chat-import 的 synthesizeSession（MIT）。
"""
from __future__ import annotations

import json
import os
import re
import time
from array import array

from .model import Session, apply_budget_trim, estimate_tokens
from .readers import _ms, _parse_jsonl, _zstd_decode_all
from .validate import validate_session_events

_SAFE = re.compile(r"^[A-Za-z0-9._-]$")


def _units(s: str) -> array:
    """按 UTF-16 码元迭代（对齐 JS charCodeAt，含代理对逐码元转义）。"""
    return array("H", s.encode("utf-16-le", errors="surrogatepass"))


def encode_segment(raw: str) -> str:
    if raw == ".":
        return "~002E"
    if raw == "..":
        return "~002E~002E"
    out = []
    for u in _units(raw):
        ch = chr(u)
        if ch != "~" and _SAFE.match(ch):
            out.append(ch)
        else:
            out.append("~%04X" % u)
    return "".join(out)


def project_key(cwd: str) -> str:
    readable: list[str] = []
    sep_run = False
    for u in _units(cwd):
        ch = chr(u)
        if ch in "/\\:":
            if not sep_run:
                readable.append("-")
            sep_run = True
        elif ch != "~" and _SAFE.match(ch):
            readable.append(ch)
            sep_run = False
        else:
            readable.append("~%04X" % u)
            sep_run = False
    s = "".join(readable).lstrip("-") or "root"
    return "--" + s[:251] + "--"


def mint_session_id(source_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", str(source_id or ""))[:64]
    return "import-" + (slug or str(int(time.time() * 1000)))


# ── 事件合成 ─────────────────────────────────────────────────────────────


def synthesize(
    sess: Session,
    budget: int | None = None,
    include_env_note: bool = True,
    include_marker: bool = True,
    title_override: str | None = None,
) -> tuple[dict, list[dict], dict]:
    """返回 (meta, events, stats)。meta/事件形状见模块 docstring。"""
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    now = int(time.time() * 1000)
    sid = mint_session_id(sess.source_id)
    meta = {
        "version": 0,
        "id": sid,
        "sourceId": sess.source_id,
        "createdAt": sess.created_at or now,
        "delegationDepth": 0,
    }
    if sess.cwd:
        meta["cwd"] = sess.cwd

    events: list[dict] = []
    seq = 0

    def push(etype: str, data: dict, surface: bool = False, source_seqs=None) -> dict:
        nonlocal seq
        ev = {"type": etype, "seq": seq, "time": meta["createdAt"], "data": data}
        if surface:
            ev["surfaceOp"] = "append"
        if source_seqs is not None:
            ev["sourceEventSeqs"] = source_seqs
        seq += 1
        events.append(ev)
        return ev

    provider = sess.source
    mname = sess.model or provider

    if turns:
        if include_marker:
            marker = push(
                "session/imported",
                {
                    "tool": provider,
                    "sourceId": sess.source_id,
                    "sourcePath": sess.source_path,
                    "importedAt": now,
                },
            )
            # 宿主 KNOWN_SESSION_EVENT_TYPES 不含 session/imported：必须带顶层
            # ignorable=true 才能通过 assertEventsSupported，否则整条日志被判
            # SessionFormatUnsupportedError（dsh-session-persistence §envelope 契约）。
            marker["ignorable"] = True
        if include_env_note:
            note = (
                f"【环境变更提示】本会话已从 {provider} 迁移到 DeepSeek Harness（DSH）。"
                "当前运行环境、可用工具列表、权限与执行指令均以 DSH 当前会话为准，"
                "请勿沿用源环境中的旧工具名、旧命令或旧环境约定。"
            )
            if sess.system_prompt:
                note += "\n\n--- 原始系统提示词（仅供参考）---\n" + sess.system_prompt
            push(
                "user/message",
                {
                    "id": f"import:{sid}:env",
                    "role": "user",
                    "content": [{"type": "text", "text": note}],
                    "source": {"kind": "plugin", "plugin": "chat-import"},
                },
                surface=True,
            )

    call_seq_by_id: dict[str, int] = {}
    covered: set[str] = set()
    for t in turns:
        for s in t.steps:
            for tr in s.tool_results:
                covered.add(tr.tool_call_id)

    for i, t in enumerate(turns, start=1):
        push("turn/start", {"turn": i})
        if not t.steps:
            push(
                "user/message",
                {
                    "id": f"import:{sid}:u{i}",
                    "role": "user",
                    "content": [{"type": "text", "text": t.prompt}],
                    "source": {"kind": "user"},
                },
                surface=True,
            )
        for j, step in enumerate(t.steps, start=1):
            push("step/start", {"turn": i, "step": j})
            if j == 1:
                push(
                    "user/message",
                    {
                        "id": f"import:{sid}:u{i}",
                        "role": "user",
                        "content": [{"type": "text", "text": t.prompt}],
                        "source": {"kind": "user"},
                    },
                    surface=True,
                )
            push(
                "assistant/message",
                {
                    "turn": i,
                    "step": j,
                    "message": {
                        "id": f"import:{sid}:a{i}:{j}",
                        "role": "assistant",
                        "content": step.content,
                        "source": {"kind": "model", "provider": provider, "model": step.model or mname},
                    },
                },
                surface=True,
            )
            for tc in step.tool_calls:
                ev = push(
                    "tool/call",
                    {"turn": i, "step": j, "callId": tc["id"], "name": tc["name"], "arguments": tc["arguments"]},
                )
                call_seq_by_id[tc["id"]] = ev["seq"]
            for tr in step.tool_results:
                push(
                    "tool/result",
                    {
                        "turn": i,
                        "step": j,
                        "message": {
                            "id": f"import:{sid}:t{i}:{j}:{tr.tool_call_id}",
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool-result",
                                    "toolCallId": tr.tool_call_id,
                                    "content": tr.content,
                                    **({"isError": True} if tr.is_error else {}),
                                }
                            ],
                            "source": {"kind": "tool", "callId": tr.tool_call_id},
                        },
                    },
                    surface=True,
                    source_seqs=[call_seq_by_id[tr.tool_call_id]] if tr.tool_call_id in call_seq_by_id else None,
                )
            # 兜底配对：无结果的调用补空 result（resume 时 API 拒绝缺 tool 消息）
            for tc in step.tool_calls:
                if tc["id"] in covered:
                    continue
                push(
                    "tool/result",
                    {
                        "turn": i,
                        "step": j,
                        "message": {
                            "id": f"import:{sid}:t{i}:{j}:{tc['id']}",
                            "role": "user",
                            "content": [{"type": "tool-result", "toolCallId": tc["id"], "content": []}],
                            "source": {"kind": "tool", "callId": tc["id"]},
                        },
                    },
                    surface=True,
                    source_seqs=[call_seq_by_id[tc["id"]]] if tc["id"] in call_seq_by_id else None,
                )
            push("step/end", {"turn": i, "step": j})
        push("turn/end", {"turn": i, "reason": {"kind": "completed"}})

    title = (title_override or sess.title or "").strip()
    if title:
        push("session/title", {"title": title, "messageSeqs": [], "source": {"kind": "user"}})

    stats = {
        "messages": sum(
            1
            for e in events
            if e["type"] in ("user/message", "assistant/message", "tool/result")
            and not (isinstance(e["data"].get("source"), dict) and e["data"]["source"].get("kind") == "plugin")
        ),
        "toolCalls": sum(1 for e in events if e["type"] == "tool/call"),
        "estimatedTokens": estimate_tokens(""),
        "trimmed": trimmed,
    }
    return meta, events, stats


def tail_events(events: list[dict], from_turn: int, from_seq: int) -> list[dict]:
    """截取 from_turn（含）之后的事件尾部，seq 从 from_seq 重排（增量续写用）。

    session/imported 与 session/title 不进尾部（标记只写一次、标题只钉一次）。
    """
    keep: list[dict] = []
    old_to_new: dict[int, int] = {}
    current_turn = None
    for ev in events:
        if ev.get("type") == "turn/start" and isinstance(ev.get("data"), dict) and isinstance(ev["data"].get("turn"), int):
            current_turn = ev["data"]["turn"]
        if ev.get("type") == "session/title":
            continue
        if current_turn is not None and current_turn >= from_turn:
            old_to_new[ev["seq"]] = from_seq + len(keep)
            keep.append(ev)
    return [
        {**ev, "seq": old_to_new[ev["seq"]], **({"sourceEventSeqs": [old_to_new.get(s, s) for s in ev["sourceEventSeqs"]]} if isinstance(ev.get("sourceEventSeqs"), list) else {})}
        for ev in keep
    ]


# ── 标题预投影（侧栏列表标题的数据源回填）───────────────────────────────


def _projcache_path(dsh_root: str) -> str:
    return os.path.join(os.path.dirname(str(dsh_root)), "storages", "session_projcache.json")


TITLE_ROW_VER = 1  # title 行的当前 schema 版本（实测本机 dsh 写入值）


def plan_title_backfill(dsh_root: str, only_imports: bool = True) -> dict:
    """为缺少投影缓存的导入会话准备 title 行回填计划。

    机制（dsh-session-projection-cache cachedSnapshot）：侧栏列表标题读自
    session_projcache.json 的 title 行（零 IO 列表读），该缓存正常只在会话被
    打开/adopt 时回填——直接落盘的导入会话因此全部回退显示工作区名。
    行 schema 宽松（rows 任意子集合法），最小回填 = identity{createdAt,cwd} +
    title 行 {ver:1, seq:<title事件seq>, val:<标题>}。
    """
    import glob as _glob

    pc_path = _projcache_path(dsh_root)
    if not os.path.exists(pc_path):
        return {"backfill": {}, "reason": f"未找到 {pc_path}"}
    pc = json.load(open(pc_path, encoding="utf-8"))
    existing = pc.get("tables", {}).get("sessions", {})
    backfill: dict[str, dict] = {}

    def title_of(p: str, sid: str):
        try:
            _, evs = read_log_events(p)
            ev = next((e for e in reversed(evs) if e.get("type") == "session/title"), None)
            return ev["data"].get("title") if ev else None
        except Exception:
            return None

    for path in sorted(_glob.glob(os.path.join(str(dsh_root), "*", "import-*", "session.jsonl*"))):
        sid = os.path.basename(os.path.dirname(path))
        old_row = (existing.get(sid, {}).get("rows") or {}).get("title")
        if old_row is not None and old_row.get("val") == title_of(path, sid):
            continue  # 已有 title 投影且与日志一致（点开过/已回填/已刷新）
        try:
            header, events = read_log_events(path)
        except Exception:
            continue
        if header is None:
            continue
        title_ev = next((e for e in reversed(events) if e.get("type") == "session/title"), None)
        if not title_ev:
            continue
        title = title_ev.get("data", {}).get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        identity = {"createdAt": header.get("createdAt", 0)}
        if header.get("cwd") is not None:
            identity["cwd"] = header["cwd"]
        backfill[sid] = {
            "identity": identity,
            "rows": {"title": {"ver": TITLE_ROW_VER, "seq": title_ev.get("seq", 0), "val": title}},
        }
    return {"backfill": backfill, "projcache": pc, "pc_path": pc_path}


def apply_title_backfill(plan: dict, dsh_running: bool = False) -> str:
    """把回填计划写进 session_projcache.json（原子替换 + 备份）。dsh 运行中拒绝。"""
    import shutil as _shutil
    import time as _time

    if dsh_running:
        raise RuntimeError("检测到 dsh 正在运行：projcache 回填必须在 dsh 完全退出后执行")
    if not plan.get("backfill"):
        return "noop（无待回填）"
    pc = plan["projcache"]
    pc_path = plan["pc_path"]
    sessions = pc.setdefault("tables", {}).setdefault("sessions", {})
    for sid, entry in plan["backfill"].items():
        sessions[sid] = entry
    bak = f"{pc_path}.agentsync-bak-{_time.strftime('%Y%m%d-%H%M%S')}"
    _shutil.copy2(pc_path, bak)
    tmp = pc_path + ".agentsync-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, pc_path)
    return f"backfilled title rows for {len(plan['backfill'])} sessions (backup: {bak})"


# ── 会话清理（prune：孤儿 + 打招呼/测试会话 → 回收站，可恢复）──────────

# 打招呼/冒烟测试的判定词（标题或首轮提问为这些词本身，而非包含）
JUNK_TOKENS = {
    "你好", "hello", "hi", "在吗", "测试", "test", "ping", "冒烟", "smoke",
    "code-ok", "codex-ok", "ok", "正常", "测试模型",
}


def _is_junk(title: str, first_prompt: str, n_turns: int) -> bool:
    """判定纯打招呼/冒烟测试会话：轮次极少且（标题或首问）就是测试词本身。

    注意是「等于」而非「包含」——「测试环境注释鉴权」这类真实工作标题不会误伤。
    """
    if n_turns > 2:
        return False
    for text in (title.strip(), first_prompt.strip().lower()):
        t = text.lower()
        if t and (t in JUNK_TOKENS or t.rstrip("!！.。?？") in JUNK_TOKENS):
            return True
        if t.startswith(("codex-ok", "code-ok", "reply with exactly")):
            return True
    return False


def plan_prune(dsh_root: str, sources: dict[str, set[str]] | None = None) -> dict:
    """扫描导入会话 → 清理计划。

    sources: {源名: 当前源库仍存在的 source_id 集合}；不在集合中的导入 = 孤儿。
    返回 {orphans, junk, detail: {sid: {reason,source,title,turns,prompt}}, paths}
    """
    import glob as _glob

    detail: dict[str, dict] = {}
    paths: dict[str, str] = {}
    for path in sorted(_glob.glob(os.path.join(str(dsh_root), "*", "import-*", "session.jsonl*"))):
        sid = os.path.basename(os.path.dirname(path))
        try:
            header, events = read_log_events(path)
        except Exception:
            continue
        marker = next((e for e in events if e.get("type") == "session/imported"), {})
        src = marker.get("data", {}).get("tool", "?")
        title_ev = next((e for e in reversed(events) if e.get("type") == "session/title"), None)
        title = (title_ev or {}).get("data", {}).get("title", "")
        for pref in ("[hermes] ", "[codex] ", "[workbuddy] ", "[zcode] "):
            if title.startswith(pref):
                title = title[len(pref):]
        first_user = next((e for e in events if e.get("type") == "user/message"
                           and (e.get("data", {}).get("source") or {}).get("kind") == "user"), None)
        prompt = ""
        if first_user:
            prompt = chr(10).join(
                b.get("text", "") for b in first_user["data"].get("content", []) if isinstance(b, dict)
            )
        n_turns = sum(1 for e in events if e.get("type") == "turn/start")
        paths[sid] = path
        detail[sid] = {"source": src, "title": title, "turns": n_turns, "prompt": prompt[:60]}
    orphans, junk = [], []
    for sid, d in detail.items():
        alive = (sources or {}).get(d["source"])
        if alive is not None and sid.replace("import-", "", 1) not in alive:
            d["reason"] = "孤儿（源会话已删除）"
            orphans.append(sid)
        elif _is_junk(d["title"], d["prompt"], d["turns"]):
            d["reason"] = "打招呼/冒烟测试会话"
            junk.append(sid)
    return {"orphans": orphans, "junk": junk, "detail": detail, "paths": paths}


def apply_prune(dsh_root: str, plan: dict, do_orphans: bool, do_junk: bool, dsh_running: bool = False) -> dict:
    """执行清理：会话目录移入回收站 + 清 workspace.json/projcache 引用（均先备份）。"""
    import shutil as _shutil
    import time as _time

    if dsh_running:
        raise RuntimeError("检测到 dsh 正在运行：prune 需在 dsh 完全退出后执行")
    targets = list(plan["orphans"] if do_orphans else []) + list(plan["junk"] if do_junk else [])
    if not targets:
        return {"moved": 0}
    trash = os.path.normpath(os.path.join(str(dsh_root), "..", "..", ".trash-dsh"))
    os.makedirs(trash, exist_ok=True)
    with open(os.path.join(trash, "manifest.jsonl"), "a", encoding="utf-8") as manifest:
        moved = 0
        for sid in targets:
            session_dir = os.path.dirname(plan["paths"][sid])
            dest = os.path.join(trash, sid)
            if os.path.exists(dest):
                dest = dest + "-" + _time.strftime("%H%M%S")
            _shutil.move(session_dir, dest)
            manifest.write(json.dumps(
                {"id": sid, **plan["detail"][sid], "movedAt": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "from": session_dir}, ensure_ascii=False) + chr(10))
            moved += 1

    # 清引用：workspace.json 的 sessionIds + projcache 条目
    for store_file, kind in (("workspace.json", "ws"), ("session_projcache.json", "pc")):
        sp = os.path.normpath(os.path.join(str(dsh_root), "..", "storages", store_file))
        if not os.path.exists(sp):
            continue
        data = json.load(open(sp, encoding="utf-8"))
        changed = False
        if kind == "ws":
            for rec in data.get("tables", {}).get("workspaces", {}).values():
                before = rec.get("sessionIds", [])
                after = [s for s in before if s not in targets]
                if len(after) != len(before):
                    rec["sessionIds"] = after
                    changed = True
        else:
            tabs = data.get("tables", {}).get("sessions", {})
            for sid in targets:
                if sid in tabs:
                    del tabs[sid]
                    changed = True
        if changed:
            _shutil.copy2(sp, f"{sp}.agentsync-bak-{_time.strftime('%Y%m%d-%H%M%S')}")
            tmp = sp + ".agentsync-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, sp)
    return {"moved": moved, "trash": trash}


# ── 工作区挂载（分组）───────────────────────────────────────────────────


def _norm_path(p: str) -> str:
    """Windows 路径归一：去尾部分隔符、大小写折叠，用于 cwd ↔ workspace 精确匹配。"""
    return str(p).rstrip("/\\").replace("/", "\\").casefold()


def plan_attach(dsh_root: str, only_imports: bool = True) -> dict:
    """扫描导入会话 → workspace.json 挂载计划。

    原生语义（dsh-workspace attachSession）：会话按 header.cwd 挂到 path 匹配的
    工作区记录；cwd 不存在的目录不挂（留在未分组）；无记录的 cwd 新建工作区记录
    （title 取 basename，id 进 global.workspaceIds）。
    """
    import glob as _glob
    import uuid as _uuid

    ws_path = os.path.join(os.path.dirname(str(dsh_root)), "storages", "workspace.json")
    if not os.path.exists(ws_path):
        return {"action": "missing", "reason": f"未找到 {ws_path}", "attach": {}, "create": {}, "skip": {}}
    ws = json.load(open(ws_path, encoding="utf-8"))
    records = ws.get("tables", {}).get("workspaces", {})
    by_path: dict[str, str] = {}
    for wid, rec in records.items():
        by_path[_norm_path(rec["path"])] = wid

    attach: dict[str, list[str]] = {}   # wid -> [session_id]
    create: dict[str, list[str]] = {}   # 新 path -> [session_id]（path 不存在目录的剔除）
    skip: dict[str, list[str]] = {}     # 原因 -> [session_id]

    for path in sorted(_glob.glob(os.path.join(str(dsh_root), "*", "import-*", "session.jsonl*"))):
        sid = os.path.basename(os.path.dirname(path))
        try:
            header, _ = read_log_events(path)
        except Exception as e:
            skip.setdefault("读取失败", []).append(f"{sid}: {e}")
            continue
        if header is None:
            skip.setdefault("无会话头", []).append(sid)
            continue
        cwd = header.get("cwd")
        if not cwd:
            skip.setdefault("无 cwd（未分组，dsh 原生语义）", []).append(sid)
            continue
        if not os.path.isdir(cwd):
            skip.setdefault(f"cwd 目录不存在：{cwd}", []).append(sid)
            continue
        temp_root = os.environ.get("TEMP", "")
        if temp_root and os.path.normcase(os.path.abspath(cwd)).startswith(os.path.normcase(os.path.abspath(temp_root))):
            skip.setdefault("临时目录不建工作区（留在未分组）", []).append(sid)
            continue
        key = _norm_path(cwd)
        wid = by_path.get(key)
        if wid:
            attach.setdefault(wid, []).append(sid)
        else:
            # dsh 启动会清理「嵌套在已有工作区路径之下」的记录（实测：父路径已有
            # 记录时子路径记录被丢弃）——与其建了被删，不如与 dsh 行为保持一致跳过。
            nested = any(
                key.startswith(existing + "\\") and key != existing
                for existing in by_path
            )
            if nested:
                skip.setdefault("cwd 嵌套在已有工作区内（dsh 会清理该记录）", []).append(sid)
            else:
                create.setdefault(cwd, []).append(sid)
    return {"action": "plan", "ws_path": ws_path, "workspace": ws, "attach": attach, "create": create, "skip": skip}


def apply_attach(plan: dict, dsh_running: bool = False) -> str:
    """按计划改写 workspace.json（原子替换 + 先备份）。dsh 运行中拒绝。"""
    import shutil as _shutil
    import time as _time
    import uuid as _uuid
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    if dsh_running:
        raise RuntimeError("检测到 dsh 正在运行：attach 必须在 dsh 完全退出后执行（否则退出时会覆盖本次修改）")
    ws = plan["workspace"]
    ws_path = plan["ws_path"]
    records = ws["tables"]["workspaces"]
    by_path = {_norm_path(r["path"]): w for w, r in records.items()}

    n_attached = 0

    def _iso_now() -> str:
        t = _dt.now(_tz.utc)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    _iso = _iso_now()
    for wid, sids in plan["attach"].items():
        existing = set(records[wid]["sessionIds"])
        add = [s for s in sids if s not in existing]
        if add:
            records[wid]["sessionIds"] = records[wid]["sessionIds"] + add  # 追加到尾部（原生 attach 前插，这里不扰动原顺序）
            records[wid]["updatedAt"] = _iso
        n_attached += len(add)

    n_new_ws = 0
    n_new_attached = 0

    for cwd, sids in plan["create"].items():
        wid = str(_uuid.uuid4())
        # 记录 schema（dsh-workspace workspaceRecord）必填 5 键：缺 createdAt/updatedAt
        # 会导致 dsh 启动时 Zod 校验失败、整个插件树拒绝加载。
        records[wid] = {
            "path": cwd,
            "title": os.path.basename(cwd.rstrip("/\\")) or cwd,
            "sessionIds": list(sids),
            "createdAt": _iso_now(),
            "updatedAt": _iso_now(),
        }
        ws["global"].setdefault("workspaceIds", []).append(wid)
        n_new_ws += 1
        n_new_attached += len(sids)

    if n_attached == 0 and n_new_ws == 0:
        return "noop（无新挂载）"
    import shutil as _shutil
    import time as _time

    bak = f"{ws_path}.agentsync-bak-{_time.strftime('%Y%m%d-%H%M%S')}"
    _shutil.copy2(ws_path, bak)
    tmp = ws_path + ".agentsync-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ws, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ws_path)
    return f"attached {n_attached} sessions to {len(plan['attach'])} workspaces; created {n_new_ws} workspaces for {n_new_attached} sessions (backup: {bak})"


def dsh_process_running() -> bool:
    """尽力检测 dsh 进程（node 命令行含 dsh / dsh.exe）。"""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'dsh' } | Select-Object -ExpandProperty Name"],
            capture_output=True, timeout=30,
        ).stdout.decode("utf-8", errors="replace").lower()
        return "node.exe" in out or "dsh.exe" in out
    except Exception:
        return False


# ── 落盘 ─────────────────────────────────────────────────────────────────


def session_log_path(dsh_root: str, cwd: str | None, session_id: str) -> str:
    proj = "_no-cwd" if not cwd else project_key(cwd)
    return os.path.join(str(dsh_root), proj, encode_segment(session_id), "session.jsonl.zstd")


def _compress_frame(payload: str) -> bytes:
    import zstandard as zstd

    c = zstd.ZstdCompressor(write_checksum=True, write_content_size=True)
    return c.compress(payload.encode("utf-8"))


def read_log_events(path: str) -> tuple[dict | None, list[dict]]:
    """读回一个 dsh 会话文件：返回 (header, events)。"""
    raw = open(path, "rb").read()
    text = _zstd_decode_all(raw).decode("utf-8", errors="replace") if path.endswith(".zstd") else raw.decode("utf-8", errors="replace")
    lines = _parse_jsonl(text)
    header = next((o for o in lines if o.get("type") == "session"), None)
    events = [o for o in lines if o.get("type") != "session"]
    return header, events


def plan_write(dsh_root: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """计算目标路径与动作（create / append / up-to-date / skip-empty）。

    force=True：已存在的导入会话整体重写（用于修复格式损坏的旧导入）。
    titles：{source_id: 新标题} 覆盖（配合 force 重写生效）。
    """
    meta, events, stats = synthesize(sess, budget=budget, title_override=(titles or {}).get(sess.source_id))
    path = session_log_path(dsh_root, sess.cwd, meta["id"])
    if not sess.turns:
        return {"action": "skip", "reason": "无可导入轮次", "path": path, "meta": meta, "events": [], "stats": stats}
    if not os.path.exists(path) or force:
        return {"action": "create", "path": path, "meta": meta, "events": events, "stats": stats}
    header, existing = read_log_events(path)
    max_turn = 0
    for ev in existing:
        if ev.get("type") == "turn/start" and isinstance(ev.get("data"), dict):
            max_turn = max(max_turn, ev["data"].get("turn") or 0)
    src_turns = len(sess.turns)
    if src_turns <= max_turn:
        return {"action": "up-to-date", "path": path, "meta": meta, "events": [], "stats": stats,
                "existingTurns": max_turn, "sourceTurns": src_turns}
    tail = tail_events(events, max_turn + 1, len(existing))
    if not tail:
        return {"action": "up-to-date", "path": path, "meta": meta, "events": [], "stats": stats,
                "existingTurns": max_turn, "sourceTurns": src_turns}
    return {"action": "append", "path": path, "meta": meta, "events": tail, "stats": stats,
            "existingTurns": max_turn, "sourceTurns": src_turns}


def apply_write(plan: dict) -> str:
    """按 plan 写盘。create：头帧+事件帧原子落盘；append：追加一个事件帧。"""
    path = plan["path"]
    meta = plan["meta"]
    events = plan["events"]
    ok, problems = validate_session_events(events)
    if not ok:
        raise ValueError("事件校验失败：" + "; ".join(problems[:5]))
    if plan["action"] == "create":
        header_line = json.dumps(
            {
                "type": "session",
                "version": meta.get("version", 0),
                "id": meta["id"],
                "createdAt": meta["createdAt"],
                **({"cwd": meta["cwd"]} if meta.get("cwd") else {}),
                "delegationDepth": meta.get("delegationDepth", 0),
            },
            ensure_ascii=False,
        )
        body = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
        blob = _compress_frame(header_line + "\n") + _compress_frame(body)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".agentsync-tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
        return f"created {len(events)} events"
    if plan["action"] == "append":
        body = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
        with open(path, "ab") as f:
            f.write(_compress_frame(body))
        return f"appended {len(events)} events"
    return "noop"
