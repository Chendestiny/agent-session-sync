r"""写入 Cline（VS Code 扩展 globalStorage）。

配方（源码 openDiskConversationHistory.ts + 真数据核验）：
- 任务目录：tasks/<created_at 毫秒>/（原生任务 id 即创建时间戳，无形状可判别
  导入 → 旁路清单 .agentsync-imports.json，同 opencode 先例）
- 三件 JSON：
  - ui_messages.json：say=task 首问 / user_feedback 追问 / reasoning / completion_result，
    每行带 ts 与 conversationHistoryIndex
  - api_conversation_history.json：[{role,content:[{type:text,text}]}]，首条 user
    含环境块（Working Directory (cwd) —— 读取器从这里反解 cwd）
  - task_metadata.json：model_usage / environment_history 最小行
- 增量：按已有 user_feedback+task 行数整轮追加（幂等，目录名确定性映射）
"""
from __future__ import annotations

import json
import os
import time
import uuid

from .dshwrite import load_tombstones
from .model import Session, apply_budget_trim

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd43018")
_MANIFEST = ".agentsync-imports.json"


def local_id(sess: Session) -> str:
    return str(uuid.uuid5(_NS, f"cline:{sess.source}:{sess.source_id}"))


def _prefixed_title(source: str, title: str) -> str:
    if title and not (title.startswith("[") and "] " in title[:14]):
        title = f"[{source}] {title}"
    return title


def task_dir(home, sess: Session) -> str:
    return os.path.join(str(home), "tasks", str(sess.created_at or int(time.time() * 1000)))


def import_ids(home) -> set[str]:
    """旁路清单：agentsync 铸的任务目录名集合（防 A→cline→A 回流）。"""
    try:
        d = json.load(open(os.path.join(str(home), _MANIFEST), encoding="utf-8"))
        return set(d.get("tasks", []))
    except (OSError, ValueError):
        return set()


def _register(home, tdir: str) -> None:
    ids = import_ids(home)
    name = os.path.basename(tdir)
    if name in ids:
        return
    ids.add(name)
    with open(os.path.join(str(home), _MANIFEST), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"tasks": sorted(ids)}, f, ensure_ascii=False, indent=1)


def _count_turns(ui_path: str) -> int:
    try:
        ui = json.load(open(ui_path, encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return sum(1 for ev in ui if isinstance(ev, dict) and ev.get("say") in ("task", "user_feedback"))


def _turn_rows(sess: Session, turns, start_ms: int) -> tuple[list, list]:
    """IR 轮 → (ui 行, api 消息行)。"""
    ui, api = [], []
    ms = start_ms
    idx = 1
    for i, t in enumerate(turns):
        ms = t.time or (start_ms + i * 1000)
        ui.append({"ts": ms, "type": "say", "say": "task" if not ui else "user_feedback",
                   "text": t.prompt, "conversationHistoryIndex": idx, "partial": False})
        api.append({"role": "user", "content": [{"type": "text", "text": t.prompt}]})
        idx += 1
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
        if think:
            ui.append({"ts": ms + 1, "type": "say", "say": "reasoning",
                       "text": "\n".join(think), "conversationHistoryIndex": idx, "partial": False})
        body = "\n\n".join(reply) or "（无回复）"
        ui.append({"ts": ms + 2, "type": "say", "say": "completion_result",
                   "text": body, "conversationHistoryIndex": idx, "partial": False})
        api.append({"role": "assistant", "content": [{"type": "text", "text": body}]})
        idx += 1
    return ui, api


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
    tdir = task_dir(home, sess)
    plan = {"path": os.path.join(tdir, "ui_messages.json"), "dir": tdir, "sid": sid,
            "stats": stats, "trimmed": trimmed, "sourceTurns": len(turns),
            "cwd": cwd, "created": created, "title": _prefixed_title(sess.source, title),
            "model": sess.model, "ui": [], "api": []}
    if not turns:
        return {**plan, "action": "skip", "reason": "无可导入轮次"}
    if sess.source_id in load_tombstones(os.path.dirname(str(home))):
        return {**plan, "action": "skip-deleted", "reason": "曾被删除（墓碑拦截）"}
    have = _count_turns(plan["path"])
    plan["existingTurns"] = have
    if have >= len(turns) and not force:
        return {**plan, "action": "up-to-date"}
    tail = turns if (force or not os.path.exists(plan["path"])) else turns[have:]
    base_ms = created or 1787000000000
    ui, api = _turn_rows(sess, tail, base_ms + have * 2000)
    plan["ui"], plan["api"] = ui, api
    plan["create"] = not os.path.exists(plan["path"]) or force
    return {**plan, "action": "append" if (os.path.exists(plan["path"]) and not force) else "create"}


def apply_write(plan: dict) -> str:
    tdir = plan["dir"]
    os.makedirs(tdir, exist_ok=True)
    ui_path = os.path.join(tdir, "ui_messages.json")
    api_path = os.path.join(tdir, "api_conversation_history.json")
    if plan.get("create"):
        with open(ui_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump([], f)
        env = (f"<environment_details>\nWorking Directory ({plan['cwd']})\n"
               "# Imported by agentsync\n</environment_details>")
        with open(api_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump([{"role": "user", "content": [{"type": "text", "text": env}]}], f,
                      ensure_ascii=False, indent=1)
        json.dump({"model_usage": [{"ts": plan["created"], "model_id": plan.get("model") or "imported",
                                    "model_provider_id": "agentsync", "mode": "act"}],
                   "environment_history": [{"ts": plan["created"], "os_name": os.name,
                                            "host_name": "agentsync"}]},
                  open(os.path.join(tdir, "task_metadata.json"), "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)
    try:
        ui = json.load(open(ui_path, encoding="utf-8"))
    except (OSError, ValueError):
        ui = []
    ui.extend(plan["ui"])
    with open(ui_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ui, f, ensure_ascii=False, indent=1)
    try:
        api = json.load(open(api_path, encoding="utf-8"))
    except (OSError, ValueError):
        api = []
    api.extend(plan["api"])
    with open(api_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(api, f, ensure_ascii=False, indent=1)
    _register(os.path.dirname(os.path.dirname(tdir)), tdir)
    return f"{plan['action']} {len(plan['ui'])} ui rows -> {tdir} ({os.path.basename(tdir)})"
