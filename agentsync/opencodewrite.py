r"""写入 opencode（%LOCALAPPDATA%\opencode\opencode.db 或 ~/.local/share/opencode/opencode.db）。

配方对齐 agentctxsync 的 opencode 适配器（实机验证）+ 本机真库 ground truth：
- session 表：id='ses_'+uuid5hex（确定性幂等）、version/agent='build'、
  model 列必须是 JSON（{"id","providerID":"opencode","variant":"default"}，裸字符串会炸）、
  directory 用正斜杠、slug 小写连字符且全库唯一
- project_id：按 directory 匹配 project/project_directory（桌面版列表按 project 圈会话），
  匹配不到落 'global'（永远存在的桶）
- message.data：{"role","time":{"created"}(,model:{modelID})}；
  part.data：text / reasoning / tool（一个 tool part 同时带 state.input+state.output，
  读取器天然配对回调用与回传——比 agentctxsync 的有损配方多保真一档）
- 增量：按已有 user 消息数整轮追加；墓碑放 opencode.db 同目录
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("7e9b1d3f-6a2c-4d8e-b1f0-5c7d9e2a4b6c")
_VERSION = "1.18.23"  # 对齐本机真库


def local_id(sess: Session) -> str:
    return "ses_" + uuid.uuid5(_NS, f"{sess.source}:{sess.source_id}").hex


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (title or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:60] or "imported"


def _norm_dir(d: str) -> str:
    return (d or "").replace("\\", "/").rstrip("/").lower()


def _default_context(cur) -> str:
    """桌面默认项目上下文：取 global 分区里 time_created 最新的会话 directory
    （用 time_created 而非 time_updated——force 重写会推高 time_updated 造成漂移）；
    无则 ~/Documents/Default Project；再退 ~。"""
    try:
        r = cur.execute(
            "SELECT directory FROM session WHERE project_id='global' ORDER BY time_created DESC LIMIT 1"
        ).fetchone()
        if r and r[0]:
            return r[0]
    except sqlite3.OperationalError:
        pass
    dp = os.path.expanduser("~/Documents/Default Project")
    return dp if os.path.isdir(dp) else os.path.expanduser("~")


def _ensure_project(cur, directory: str) -> str:
    """为 directory 找/建 project 分区（桌面按 project 圈会话列表）。

    规则（实测）：cwd 真实存在 → 会话落自己的分区（无对应 project 则建一个
    worktree=directory 的行）；cwd 缺失或等于默认上下文 → global（Default Project）。
    """
    pid = _resolve_project(cur, directory)
    if pid not in ("global", None):
        return pid
    now_ms = int(time.time() * 1000)
    new_id = "prj_" + uuid.uuid4().hex[:20]
    cur.execute(
        "INSERT INTO project (id, name, worktree, vcs, time_created, time_updated, sandboxes) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_id, None, directory, None, now_ms, now_ms, "[]"),
    )
    return new_id


def _derived_path(directory: str) -> str:
    """原生规律（真库实证）：path = directory 去掉盘符前缀（'C:/Users/x' → 'Users/x'）。
    桌面版按 path/project 圈会话列表，缺它外来会话不显示。"""
    d = directory.replace("\\", "/")
    if len(d) > 2 and d[1] == ":" and d[2] == "/":
        return d[3:]
    return d.lstrip("/")


def _resolve_project(cur, directory: str) -> str:
    """按 directory 匹配 project（桌面列表按 project_id 圈会话）；失配落 global。"""
    want = _norm_dir(directory)
    try:
        for pid, pdir in cur.execute("SELECT project_id, directory FROM project_directory"):
            if _norm_dir(str(pdir or "")) == want:
                return pid
    except sqlite3.OperationalError:
        pass
    try:
        for pid, wt in cur.execute("SELECT id, worktree FROM project WHERE worktree IS NOT NULL"):
            if _norm_dir(str(wt)) == want:
                return pid
    except sqlite3.OperationalError:
        pass
    return "global"


def _conn(db_path: str, ro: bool = False):
    p = str(db_path).replace(chr(92), "/")
    uri = f"file:{p}?mode=ro" if ro else f"file:{p}"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _count_user_turns(cur, sid: str) -> int:
    n = 0
    for (data,) in cur.execute("SELECT data FROM message WHERE session_id=?", (sid,)):
        try:
            role = (json.loads(data or "{}") or {}).get("role")
        except (ValueError, TypeError):
            continue
        if role == "user":
            n += 1
    return n


def _tool_input_obj(args_text: str):
    """state.input 必须是对象（服务端 zod：Expected object）——JSON 字符串解析为 dict，
    解析不了退 {"raw": 原文}。"""
    if isinstance(args_text, dict):
        return args_text
    try:
        v = json.loads(args_text) if isinstance(args_text, str) and args_text.strip() else {}
    except (json.JSONDecodeError, TypeError):
        v = None
    if isinstance(v, dict):
        return v
    return {"raw": str(args_text or "")}


def _turn_messages(sess: Session, turn, idx: int, base_ms: int) -> list[tuple[dict, list[dict]]]:
    """一个 IR 轮 → [(message.data, [part.data...]), ...]。"""
    ms = turn.time or (base_ms + idx * 1000)  # 轮级真实时间优先；未知回退确定性合成
    out: list[tuple[dict, list[dict]]] = []
    out.append(({"role": "user", "time": {"created": ms}},
                [{"type": "text", "text": turn.prompt}]))
    k = 0
    for step in turn.steps:
        texts: list[str] = []
        reasonings: list[str] = []
        calls: list[tuple[str, str, str]] = []  # (call_id, name, args_text)
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
            out.append(({"role": "assistant", "time": {"created": ms + k}},
                        [{"type": "reasoning", "text": "\n".join(reasonings)}]))
        for cid, name, args_text in calls:
            k += 1
            tr = results.pop(cid, None)
            out_text = ""
            status = "completed"
            if tr is not None:
                out_text = "\n".join(
                    x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
                )
                status = "failed" if tr.is_error else "completed"
            t_ms = ms + k
            # 原生 state 完整键：status/input/output/metadata/title/time（缺一过不了 zod）。
            # ToolState 是按 status 的可辨识联合——failed 分支要求 error 键等其他形状；
            # 务实映射：失败也按 completed 形状写（输出文本保留，空则标注）。
            if tr is not None and tr.is_error and not out_text:
                out_text = "(failed)"
            state = {
                "status": "completed",
                "input": _tool_input_obj(args_text),
                "output": out_text,
                "metadata": {"truncated": False},
                "title": name,
                "time": {"start": t_ms, "end": t_ms},
            }
            out.append(({"role": "assistant", "time": {"created": t_ms}},
                        [{"type": "tool", "tool": name, "callID": cid, "state": state}]))
        for tr in results.values():  # 没有对应调用的孤儿结果
            k += 1
            out_text = "\n".join(
                x.get("text", "") for x in tr.content if isinstance(x, dict) and isinstance(x.get("text"), str)
            )
            t_ms = ms + k
            out_txt = out_text if (out_text or not tr.is_error) else "(failed)"
            out.append(({"role": "assistant", "time": {"created": t_ms}},
                        [{"type": "tool", "tool": "tool", "callID": tr.tool_call_id,
                          "state": {"status": "completed", "input": {}, "output": out_txt,
                                    "metadata": {"truncated": False}, "title": "tool",
                                    "time": {"start": t_ms, "end": t_ms}}}]))
        if texts:
            k += 1
            data: dict = {"role": "assistant", "time": {"created": ms + k}}
            if sess.model:
                data["model"] = {"providerID": "opencode", "modelID": sess.model}
            out.append((data, [{"type": "text", "text": "\n".join(texts)}]))
    return out


def plan_write(db_path: str, sess: Session, budget: int | None, force: bool = False, titles: dict | None = None) -> dict:
    """opencode 版写入计划：create / append / up-to-date / skip / skip-deleted。"""
    turns, trimmed = apply_budget_trim(sess.turns, budget)
    sid = local_id(sess)
    state_dir = os.path.dirname(str(db_path))
    created = sess.created_at or 0
    stats = {
        "messages": 1 + sum(1 + len(s.tool_results) for t in turns for s in t.steps),
        "toolCalls": sum(len(s.tool_calls) for t in turns for s in t.steps),
    }
    plan = {"path": str(db_path), "sid": sid, "messages": [], "stats": stats, "trimmed": trimmed,
            "sourceTurns": len(turns), "session_row": None, "model": sess.model}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(state_dir):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    con = _conn(db_path, ro=True)
    try:
        cur = con.cursor()
        exists = cur.execute("SELECT 1 FROM session WHERE id=?", (sid,)).fetchone() is not None
        have = _count_user_turns(cur, sid) if exists else 0
        plan["existingTurns"] = have
        if exists and have >= len(turns) and not force:
            return {**plan, "action": "up-to-date"}
        # 分区规则：cwd 真实存在 → 落自己的分区（找不到 project 就建）；缺失/无效 → 兜底默认上下文
        if sess.cwd and os.path.isdir(sess.cwd):
            directory = sess.cwd.replace("\\", "/")
        else:
            directory = _default_context(cur)
        plan["session_row"] = {
            "id": sid,
            "project_id": _resolve_project(cur, directory),
            "slug": None,  # apply 时做唯一性处理
            "directory": directory,
            "title": (sess.title or "").strip() or (turns[0].prompt[:40] if turns else ""),
            "time_created": created or None,
            "time_updated": (sess.updated_at or created or None),
        }
        base = 0 if (force or not exists) else have
        tail = turns if (force or not exists) else turns[have:]
        msgs: list[tuple[dict, list[dict]]] = []
        for i, t in enumerate(tail):
            msgs += _turn_messages(sess, t, base + i, created or 1787000000000)
        plan["messages"] = msgs
        return {**plan, "action": "append" if (exists and not force) else "create"}
    finally:
        con.close()


def _write_events(cur, s: dict, slug: str, project_id: str, feed: list, now_ms: int,
                  emit_created: bool = True) -> None:
    """opencode 1.18 桌面按事件溯源渲染（event/event_sequence 表）——不写事件，
    会话点开即报 `Expected a string starting with "msg", got "{messageID}"`。

    事件流：session.created（仅 create——append 重复发会破坏一次性语义，实测坑）
    → 每 message.updated → 每 message.part.updated → session.updated。
    注意：事件里的 directory 用反斜杠（与 session 表的正斜杠相反）。
    """
    sid = s["id"]
    sess_info = {
        "id": sid, "slug": slug, "projectID": project_id,
        "directory": s["directory"].replace("/", "\\"), "path": _derived_path(s["directory"]),
        "cost": 0,
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        "title": s["title"], "version": _VERSION,
        "time": {"created": s["time_created"] or now_ms, "updated": s["time_updated"] or now_ms},
    }
    row = cur.execute("SELECT seq FROM event_sequence WHERE aggregate_id=?", (sid,)).fetchone()
    seq = int(row[0]) if row else -1
    if row is None:
        cur.execute("INSERT INTO event_sequence (aggregate_id, seq, owner_id) VALUES (?,?,NULL)", (sid, seq))

    def emit(ev_type: str, data: dict) -> None:
        nonlocal seq
        seq += 1
        cur.execute(
            "INSERT INTO event (id, aggregate_id, seq, type, data) VALUES (?,?,?,?,?)",
            ("evt_" + uuid.uuid5(_NS, f"{sid}:{seq}:{ev_type}").hex[:26], sid, seq, ev_type,
             json.dumps(data, ensure_ascii=False)),
        )

    if emit_created:
        emit("session.created.1", {"sessionID": sid, "info": dict(sess_info)})
    for mid, data, ms, plist in feed:
        minfo = dict(data)  # 完整消息形状（agent/model/parentID 等已由 apply 补齐）
        minfo["id"] = mid
        minfo["sessionID"] = sid
        emit("message.updated.1", {"sessionID": sid, "info": minfo})
        for pid, pt, pms in plist:
            part = {"id": pid, "sessionID": sid, "messageID": mid}
            part.update(pt)  # pt 已含 time{start,end}
            emit("message.part.updated.1", {"sessionID": sid, "part": part, "time": pms})
    final = dict(sess_info)
    final["agent"] = "build"
    final["time"] = {"created": sess_info["time"]["created"], "updated": now_ms}
    emit("session.updated.1", {"sessionID": sid, "info": final})
    cur.execute("UPDATE event_sequence SET seq=? WHERE aggregate_id=?", (seq, sid))


def apply_write(plan: dict) -> str:
    con = _conn(plan["path"])
    try:
        cur = con.cursor()
        s = plan["session_row"]
        exists = cur.execute("SELECT 1 FROM session WHERE id=?", (s["id"],)).fetchone() is not None
        import time as _time

        now_ms = int(_time.time() * 1000)
        slug = _slugify(s["title"])
        n = 2
        taken = {r[0] for r in cur.execute("SELECT slug FROM session WHERE id != ?", (s["id"],))}
        while slug in taken:
            slug = f"{_slugify(s['title'])[:56]}-{n}"
            n += 1
        # 找/建分区 project：默认上下文与根工作台保持 global，其余目录自建分区
        default_ctx = _default_context(cur)
        project_id = _resolve_project(cur, s["directory"])
        if project_id == "global" and _norm_dir(s["directory"]) not in ("/", _norm_dir(default_ctx)):
            project_id = _ensure_project(cur, s["directory"])
        if exists:
            if plan["action"] == "create":
                # force 整体重写：清旧消息/部件/事件，否则确定性 uuid5 id 会撞主键
                cur.execute("DELETE FROM part WHERE session_id=?", (s["id"],))
                cur.execute("DELETE FROM message WHERE session_id=?", (s["id"],))
                cur.execute("DELETE FROM event WHERE aggregate_id=?", (s["id"],))
                cur.execute("DELETE FROM event_sequence WHERE aggregate_id=?", (s["id"],))
            cur.execute(
                "UPDATE session SET title=?, directory=?, path=?, project_id=?, slug=?, time_updated=? WHERE id=?",
                (s["title"], s["directory"], _derived_path(s["directory"]), project_id, slug, now_ms, s["id"]),
            )
        else:
            cur.execute(
                "INSERT INTO session (id, project_id, parent_id, slug, directory, path, title, version, "
                "time_created, time_updated, cost, tokens_input, tokens_output, tokens_reasoning, "
                "tokens_cache_read, tokens_cache_write, agent, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (s["id"], project_id, None, slug, s["directory"], _derived_path(s["directory"]),
                 s["title"], _VERSION,
                 s["time_created"] or now_ms, s["time_updated"] or now_ms,
                 0.0, 0, 0, 0, 0, 0, "build",
                 json.dumps({"id": plan.get("model") or "imported", "providerID": "opencode",
                             "variant": "default"}, ensure_ascii=False)),
            )
        sid = s["id"]
        model_id = plan.get("model") or "imported"
        directory_bs = s["directory"].replace("/", "\\")
        last_user_mid: str | None = None
        feed: list = []  # (mid, message.data, ms, [(pid, part.data, ms), ...])
        for mi, (data, parts) in enumerate(plan["messages"]):
            data = dict(data)
            if data.get("role") == "user":
                # 原生 user 形状（缺 agent/model/summary 会过不了服务端 zod 校验）
                data.setdefault("agent", "build")
                data.setdefault("model", {"providerID": "opencode", "modelID": model_id})
                data.setdefault("summary", {"diffs": []})
            else:
                data.setdefault("mode", "build")
                data.setdefault("agent", "build")
                data.setdefault("path", {"cwd": directory_bs, "root": "/"})
                data.setdefault("cost", 0)
                data.setdefault("tokens", {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                                           "cache": {"write": 0, "read": 0}})
                data.setdefault("modelID", model_id)
                data.setdefault("providerID", "opencode")
                data.setdefault("finish", "tool-calls" if any(p.get("type") == "tool" for p in parts) else "stop")
            ms = (data.get("time") or {}).get("created") or now_ms
            mid = "msg_" + uuid.uuid5(_NS, f"{sid}:{ms}:{mi}").hex[:26]
            if data.get("role") == "user":
                data.setdefault("time", {"created": ms})
                last_user_mid = mid
            else:
                if last_user_mid:
                    data.setdefault("parentID", last_user_mid)
                t = data.setdefault("time", {"created": ms})
                t.setdefault("completed", ms)
            cur.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
                (mid, sid, ms, ms, json.dumps(data, ensure_ascii=False)),
            )
            plist = []
            for pi, pt in enumerate(parts or [{"type": "text", "text": ""}]):
                pt = dict(pt)
                # 服务端 zod 要求 part 带 time（原生 reasoning 有 {start,end}，text 实测
                # 表里可缺但 API 必需）——统一补 {start,end}，事件对象同源共享
                if "time" not in pt:
                    pt["time"] = {"start": ms, "end": ms}
                pid = "prt_" + uuid.uuid5(_NS, f"{mid}:{pi}:{pt.get('type')}").hex[:26]
                cur.execute(
                    "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
                    "VALUES (?,?,?,?,?,?)",
                    (pid, mid, sid, ms, ms, json.dumps(pt, ensure_ascii=False)),
                )
                plist.append((pid, pt, ms))
            feed.append((mid, data, ms, plist))
        _write_events(cur, s, slug, project_id, feed, now_ms, emit_created=(plan["action"] == "create"))
        con.commit()
        return f"{plan['action']} {len(plan['messages'])} messages -> {plan['path']} ({sid[:12]})"
    finally:
        con.close()
