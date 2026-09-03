r"""会话级备份/还原：按口径（原生 or 原生+导入）与日期范围筛选会话，快照到 C 库
`~/.session-sync/backups/<source>/<时间戳>/`，每会话一份 IR JSON（与 C 库规范文件
同构）+ manifest（口径/范围/清单）。还原 = 读快照 IR → 走 to-X 同一写入器幂等写回
（默认目标=源本身，只读源或跨家场景用 --target 指定 6 个可写目标之一）。

与 pull 的区别：pull 是持续推进水位的同步语义；backup 是不推进任何水位、不影响
增量基准的时间戳快照，随时可列可还原。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

from . import paths, readers, store


def backup_root() -> str:
    home = os.environ.get("SESSION_SYNC_HOME") or os.path.join(os.path.expanduser("~"), ".session-sync")
    return os.path.join(home, "backups")


def _collect(source: str, p, with_imports: bool):
    """按口径取会话：原生=reader 默认（排除导入/子代理/归档）；+导入=展示口径。"""
    if source == "zcode":
        return readers.read_zcode(p.zcode_db)
    if source == "hermes":
        return readers.read_hermes(p.hermes_db, include_imports=with_imports)
    if source == "dsh":
        return readers.read_dsh(str(p.dsh_sessions), include_imports=with_imports)
    if source == "codex":
        return readers.read_codex(p.codex_sessions, include_imports=with_imports)
    if source == "workbuddy":
        return readers.read_workbuddy(p.workbuddy_home, include_imports=with_imports)
    if source == "claude":
        return readers.read_claude(p.claude_projects, include_imports=with_imports)
    if source == "opencode":
        return readers.read_opencode(p.opencode_db, include_imports=with_imports)
    if source == "qoder":
        return readers.read_qoder(p.qoder_home)
    if source == "openclaw":
        return readers.read_openclaw(p.openclaw_home)
    if source == "cursor":
        return readers.read_cursor(p.cursor_global_db)
    if source == "trae":
        return readers.read_trae(p.trae_global_db)
    return []


def _writers():
    from . import claudewrite, codexwrite, dshwrite, hermeswrite, opencodewrite, workbuddywrite

    return {
        "dsh": (lambda p: p.dsh_sessions, dshwrite),
        "codex": (lambda p: p.codex_sessions, codexwrite),
        "claude": (lambda p: p.claude_projects, claudewrite),
        "hermes": (lambda p: p.hermes_db, hermeswrite),
        "opencode": (lambda p: p.opencode_db, opencodewrite),
        "workbuddy": (lambda p: p.workbuddy_home, workbuddywrite),
    }


def expand_ids(sources: list[str], p, wanted: set[str]) -> set[str]:
    """把 --session 的子串集合展开成完整会话 id 集合（与 CLI 各处 --session 同语义）。"""
    out: set[str] = set()
    for src in sources:
        for s in _collect(src, p, with_imports=True):
            if any(w in s.source_id or w in (s.title or "") for w in wanted):
                out.add(s.source_id)
    return out


def do_backup(sources: list[str], p, days: int | None = None, with_imports: bool = False,
              ids: set[str] | None = None) -> list[dict]:
    """打快照：每源一个时间戳目录，返回摘要。days=None 不限日期；ids 非空时只备这些会话
    （webui 勾选/CLI --session 精确点名）。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = []
    for src in sources:
        sessions = _collect(src, p, with_imports)
        if days is not None:
            cutoff = (time.time() - days * 86400) * 1000
            sessions = [s for s in sessions if (s.updated_at or s.created_at or 0) >= cutoff]
        if ids is not None:
            sessions = [s for s in sessions if s.source_id in ids]
        snap = os.path.join(backup_root(), src, ts)
        sess_dir = os.path.join(snap, "sessions")
        os.makedirs(sess_dir, exist_ok=True)
        size = 0
        for s in sessions:
            data = store.session_to_dict(s)
            path = os.path.join(sess_dir, f"{store._safe_name(s.source_id)}.json")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                size += f.tell()
        json.dump({
            "source": src, "ts": ts, "with_imports": with_imports, "days": days,
            "count": len(sessions),
            "sessions": [{"id": s.source_id, "title": (s.title or "")[:60],
                          "turns": len(s.turns), "cwd": s.cwd} for s in sessions],
        }, open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8", newline="\n"),
            ensure_ascii=False, indent=1)
        out.append({"source": src, "ts": ts, "count": len(sessions),
                    "size_kb": round(size / 1024, 1), "dir": snap})
    return out


def list_snapshots(source: str | None = None) -> list[dict]:
    """已有快照清单（新→旧）：ts/count/口径/范围/体积。"""
    root = backup_root()
    out = []
    for src in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if source and src != source:
            continue
        sdir = os.path.join(root, src)
        for ts in sorted(os.listdir(sdir), reverse=True):
            mpath = os.path.join(sdir, ts, "manifest.json")
            if not os.path.exists(mpath):
                continue
            try:
                m = json.load(open(mpath, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            try:
                size = sum(os.path.getsize(os.path.join(sdir, ts, "sessions", f))
                           for f in os.listdir(os.path.join(sdir, ts, "sessions")))
            except OSError:
                size = 0
            out.append({"source": src, "ts": ts, "count": m.get("count", 0),
                        "with_imports": m.get("with_imports"), "days": m.get("days"),
                        "size_kb": round(size / 1024, 1),
                        "dir": os.path.abspath(os.path.join(sdir, ts))})
    return out


def delete_snapshot(source: str, ts: str) -> dict:
    """删除一个快照目录（只动 C 库 backups/，目录级不可恢复）。"""
    if not re.match(r"^[0-9A-Za-z_-]+$", ts or "") or not re.match(r"^[0-9A-Za-z_-]+$", source or ""):
        return {"ok": False, "error": "非法 source/ts"}
    path = os.path.join(backup_root(), source, ts)
    if not os.path.isdir(path):
        return {"ok": False, "error": f"快照不存在：{path}"}
    shutil.rmtree(path)
    return {"ok": True, "removed": path}


def plan_restore(source: str, ts: str, p, target: str | None = None, limit: int = 5) -> dict:
    """还原计划（dry-run 同款只读）：列将写回的会话。"""
    writers = _writers()
    tgt = target or source
    if tgt not in writers:
        return {"ok": False, "error": f"还原目标不可写：{tgt}（只读源请用 --target 指定：{','.join(writers)}）"}
    snap = os.path.join(backup_root(), source, ts)
    sess_dir = os.path.join(snap, "sessions")
    if not os.path.isdir(sess_dir):
        return {"ok": False, "error": f"快照不存在：{snap}"}
    files = sorted(os.listdir(sess_dir))
    m = {}
    try:
        m = json.load(open(os.path.join(snap, "manifest.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    titles = {s.get("id"): s.get("title") for s in m.get("sessions", [])}
    return {"ok": True, "target": tgt, "count": len(files),
            "sessions": [{"id": f[:-5], "title": titles.get(f[:-5], "")} for f in files[:limit]]}


def do_restore(source: str, ts: str, p, target: str | None = None) -> dict:
    """执行还原：快照 IR → 目标写入器（与 to-X 同一代码路径，幂等）。"""
    writers = _writers()
    tgt = target or source
    if tgt not in writers:
        return {"ok": False, "error": f"还原目标不可写：{tgt}"}
    get_store, writer = writers[tgt]
    store_root = get_store(p)
    if not store_root:
        return {"ok": False, "error": f"未找到目标 {tgt} 存储"}
    sess_dir = os.path.join(backup_root(), source, ts, "sessions")
    if not os.path.isdir(sess_dir):
        return {"ok": False, "error": f"快照不存在：{sess_dir}"}
    root = str(store_root)
    s_root = root if os.path.isdir(root) else os.path.dirname(root)
    written = skipped = failed = 0
    errors = []
    for f in sorted(os.listdir(sess_dir)):
        if not f.endswith(".json"):
            continue
        try:
            sess = store.session_from_dict(json.load(open(os.path.join(sess_dir, f), encoding="utf-8")))
            plan = writer.plan_write(root, sess, budget=550000, force=False, titles={})
            if plan.get("action") in ("create", "append"):
                writer.apply_write(plan)
                written += 1
            else:
                skipped += 1
        except Exception as e:  # 单条失败不拖垮整批
            failed += 1
            errors.append(f"{f[:-5]}: {e}")
    # 还原推进目标侧该源水位（下次增量从还原后状态起算）
    try:
        from . import syncstate
        syncstate.mark(s_root, [source])
    except Exception:
        pass
    return {"ok": not errors, "target": tgt, "written": written, "skipped": skipped,
            "failed": failed, "errors": errors[:3]}
