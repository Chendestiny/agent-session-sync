"""规范库 C：~/.session-sync —— 全机唯一的会话规范副本（A→C→B 的 C）。

- 物理：$SESSION_SYNC_HOME 或 ~/.session-sync（跨平台：Windows=C:\\Users\\<u>\\.session-sync，
  Linux=/home/<u>/.session-sync；与 ~/.claude、~/.codex 同套路）
- 格式：IR（Session/Turn/Step）全保真序列化，7 家源统一为 1 种 JSON
- 语义：C 只镜像各源当前状态（源是唯一事实源，pull 整文件覆盖更新）；
  工具自产的 import-* 会话不回流（防 A→B→A 环形复制）
- 墓碑：C 目录 .agentsync-deleted.json（从 C 删除过的源 id 不再回流）
- push 水位：push-<target>-state.json（每目标独立断点，续推的根基）
"""
from __future__ import annotations

import json
import os
import re

from .model import Session, Step, ToolResult, Turn


def store_dir() -> str:
    return os.environ.get("SESSION_SYNC_HOME") or os.path.join(os.path.expanduser("~"), ".session-sync")


def store_exists() -> bool:
    return os.path.isdir(store_dir())


def sessions_dir() -> str:
    return os.path.join(store_dir(), "sessions")


def _safe_name(source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", source_id)[:120]


def session_path(source: str, source_id: str) -> str:
    return os.path.join(sessions_dir(), source, _safe_name(source_id) + ".json")


# ── IR 序列化 ────────────────────────────────────────────────────────────


def session_to_dict(s: Session) -> dict:
    return {
        "source": s.source,
        "source_id": s.source_id,
        "title": s.title,
        "cwd": s.cwd,
        "created_at": s.created_at,
        "updated_at": s.updated_at or 0,
        "model": s.model,
        "system_prompt": s.system_prompt,
        "summary": s.summary,
        "source_path": s.source_path,
        "turns": [
            {
                "prompt": t.prompt,
                "steps": [
                    {
                        "content": st.content,
                        "tool_calls": st.tool_calls,
                        "tool_results": [
                            {"tool_call_id": tr.tool_call_id, "content": tr.content, "is_error": tr.is_error}
                            for tr in st.tool_results
                        ],
                        "model": st.model,
                    }
                    for st in t.steps
                ],
            }
            for t in s.turns
        ],
    }


def session_from_dict(d: dict) -> Session:
    turns = []
    for t in d.get("turns") or []:
        steps = []
        for st in t.get("steps") or []:
            steps.append(
                Step(
                    content=list(st.get("content") or []),
                    tool_calls=list(st.get("tool_calls") or []),
                    tool_results=[
                        ToolResult(tr.get("tool_call_id") or "", list(tr.get("content") or []), bool(tr.get("is_error")))
                        for tr in st.get("tool_results") or []
                    ],
                    model=st.get("model"),
                )
            )
        turns.append(Turn(prompt=t.get("prompt") or "", steps=steps))
    return Session(
        source=d.get("source") or "",
        source_id=d.get("source_id") or "",
        title=d.get("title") or "",
        cwd=d.get("cwd"),
        created_at=int(d.get("created_at") or 0),
        updated_at=int(d.get("updated_at") or 0),
        model=d.get("model"),
        system_prompt=d.get("system_prompt"),
        summary=d.get("summary"),
        turns=turns,
        source_path=d.get("source_path"),
    )


# ── pull（源 → C）与读取 ─────────────────────────────────────────────────


def native_only(sessions: list[Session]) -> list[Session]:
    """pull 的 dsh 源过滤：工具自产的 import-* 会话不进 C（防环形复制）。"""
    return [s for s in sessions if not s.source_id.startswith("import-")]


def write_session(sess: Session) -> str:
    """写入/更新一个会话到 C。返回 create / update / up-to-date。

    C 镜像源状态：新版本（轮数更多或 updated_at 更新）整体覆盖。
    """
    path = session_path(sess.source, sess.source_id)
    if os.path.exists(path):
        try:
            old = session_from_dict(json.load(open(path, encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            old = None
        if old is not None:
            if len(old.turns) >= len(sess.turns) and (old.updated_at or 0) >= (sess.updated_at or 0):
                return "up-to-date"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(session_to_dict(sess), f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
        return "update"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(session_to_dict(sess), f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    return "create"


def read_store(sources: list[str] | None = None) -> dict[str, list[Session]]:
    """读 C：{source: [Session]}（按 source 过滤可选）。"""
    import glob as _glob

    out: dict[str, list[Session]] = {}
    root = sessions_dir()
    if not os.path.isdir(root):
        return out
    wanted = set(sources) if sources is not None else None
    for src_dir in sorted(os.listdir(root)):
        if not os.path.isdir(os.path.join(root, src_dir)):
            continue
        if wanted is not None and src_dir not in wanted:
            continue
        for path in sorted(_glob.glob(os.path.join(root, src_dir, "*.json"))):
            try:
                d = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.setdefault(src_dir, []).append(session_from_dict(d))
    return out


def overview() -> dict:
    """C 总览：{counts: {source: n}, state: {...}, push: {target: {...}}}。"""
    from . import syncstate

    counts = {src: len(ss) for src, ss in read_store().items()}
    res = {"dir": store_dir(), "counts": counts, "state": syncstate.load(store_dir())}
    push: dict[str, dict] = {}
    if os.path.isdir(store_dir()):
        for fn in os.listdir(store_dir()):
            m = re.match(r"push-(.+)-state\.json$", fn)
            if m:
                try:
                    push[m.group(1)] = json.load(open(os.path.join(store_dir(), fn), encoding="utf-8"))
                except (OSError, ValueError):
                    pass
    res["push"] = push
    return res
