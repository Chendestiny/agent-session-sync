"""Web dashboard：python sync.py web → http://127.0.0.1:8321

- 浏览/导出下载（GET）；写操作走 sync.py CLI，页面写端点仅目录绑定族（POST /api/bind-path、
  /api/pick-folder），其余 405；实时读源无缓存
- 仅绑定 127.0.0.1；页面为包内 index.html（单文件、无构建、离线可用）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .. import paths, readers, store, syncstate

DEFAULT_PORT = 8321
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

SOURCES = ["zcode", "hermes", "dsh", "codex", "workbuddy", "claude", "opencode", "qoder", "openclaw",
           "cursor", "trae"]
_ROOT_ATTR = {
    "zcode": "zcode_db", "hermes": "hermes_db", "dsh": "dsh_sessions",
    "codex": "codex_sessions", "workbuddy": "workbuddy_home",
    "claude": "claude_projects", "opencode": "opencode_db", "qoder": "qoder_home",
    "openclaw": "openclaw_home", "cursor": "cursor_global_db", "trae": "trae_global_db",
}


def _imported_flag(source: str, sid: str, p) -> bool:
    """该会话是否 agentsync 导入（其他 agent 会话的副本；同步口径默认排除）。"""
    if source == "dsh":
        return sid.startswith("import-")
    if source == "opencode":
        return bool(p.opencode_db) and sid in readers._oc_import_ids(p.opencode_db)
    if source in ("hermes", "codex", "claude", "workbuddy"):
        return readers._is_agentsync_uuid5(sid)
    return False  # zcode/qoder/openclaw 从不是写入目标


def _meta(s, imported: bool = False) -> dict:
    times = [t.time for t in s.turns if t.time]
    created = s.created_at or (min(times) if times else 0)
    last = s.updated_at or (max(times) if times else 0) or created
    title = s.title or (s.turns[0].prompt.strip()[:40] if s.turns and s.turns[0].prompt else "")
    return {
        "source": s.source, "id": s.source_id, "title": title,
        "cwd": s.cwd, "created_at": created, "updated_at": last,
        "turns": len(s.turns), "messages": s.message_count, "tools": s.tool_call_count,
        "span_first": min(times) if times else created,
        "span_last": max(times) if times else last,
        "path": s.source_path,
        "subagent": bool(getattr(s, "subagent", False)),
        "imported": imported,
    }


def _trash_count(name: str, p) -> int | None:
    """各源回收站/归档计数（轻量 SQL/目录数，不做全量解析）。None=该源无此概念。"""
    try:
        if name == "zcode" and p.zcode_db:
            # UI「删除」的真实落点是 tasks-index.sqlite（archived/deleted 标记），
            # db.session.time_archived 是第二机制；两个都数上
            ti = readers._zcode_tasks_index_path(p.zcode_db)
            n = 0
            if os.path.exists(ti):
                try:
                    con = sqlite3.connect(f"file:{ti.replace(chr(92), '/')}?mode=ro", uri=True)
                    n += con.execute("SELECT COUNT(*) FROM tasks WHERE archived=1 OR deleted=1").fetchone()[0]
                    con.close()
                except Exception:
                    pass
            con = sqlite3.connect(f"file:{str(p.zcode_db).replace(chr(92), '/')}?mode=ro", uri=True)
            n += con.execute(
                "SELECT COUNT(*) FROM session WHERE parent_id IS NULL AND time_archived IS NOT NULL"
            ).fetchone()[0]
            con.close()
            return int(n)
        if name == "hermes" and p.hermes_db:
            con = sqlite3.connect(f"file:{str(p.hermes_db).replace(chr(92), '/')}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM sessions WHERE archived=1").fetchone()[0]
            con.close()
            return int(n)
        if name == "workbuddy" and p.workbuddy_home:
            wdb = os.path.join(str(p.workbuddy_home), "workbuddy.db")
            con = sqlite3.connect(f"file:{wdb.replace(chr(92), '/')}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM sessions WHERE deleted_at IS NOT NULL").fetchone()[0]
            con.close()
            return int(n)
        if name == "dsh" and p.dsh_sessions:
            # 与 zcode/hermes 同口径：数源自身归档（dsh UI 软删名单），
            # 不数 agentsync 自己的 ~/.trash-dsh（那是 prune 的可恢复区，另一个概念）
            return len(readers._dsh_archived_ids(str(p.dsh_sessions)))
    except Exception:
        return None
    return None


def api_overview() -> dict:
    p = paths.detect()
    bound = paths.load_overrides()
    sources = []
    for name in SOURCES:
        root = getattr(p, _ROOT_ATTR[name])
        sources.append({
            "name": name, "ok": root is not None,
            "path": str(root) if root else None,
            "trash": _trash_count(name, p) if root is not None else None,
            "bound": name in bound,   # 手动绑定（~/.session-sync/paths.json）
        })
    res: dict = {"sources": sources}
    if store.store_exists():
        ov = store.overview()
        res["store"] = {"dir": ov["dir"], "counts": ov["counts"], "state": ov["state"], "push": ov["push"]}
    else:
        res["store"] = None
    res["state"] = syncstate.load(p.dsh_sessions) if p.dsh_sessions else {}
    return res


def _hidden_ids(source: str, p) -> set[str]:
    """该源「回收站/归档」会话 id 集合（dashboard 展示标记用；同步路径已在 reader 层排除）。"""
    try:
        if source == "zcode" and p.zcode_db:
            ids = readers._zcode_hidden_ids(readers._zcode_tasks_index_path(p.zcode_db))
            con = sqlite3.connect(f"file:{str(p.zcode_db).replace(chr(92), '/')}?mode=ro", uri=True)
            ids |= {r[0] for r in con.execute("SELECT id FROM session WHERE time_archived IS NOT NULL")}
            con.close()
            return ids
        if source == "hermes" and p.hermes_db:
            con = sqlite3.connect(f"file:{str(p.hermes_db).replace(chr(92), '/')}?mode=ro", uri=True)
            ids = {r[0] for r in con.execute("SELECT id FROM sessions WHERE archived=1")}
            con.close()
            return ids
        if source == "workbuddy" and p.workbuddy_home:
            wdb = os.path.join(str(p.workbuddy_home), "workbuddy.db")
            con = sqlite3.connect(f"file:{wdb.replace(chr(92), '/')}?mode=ro", uri=True)
            ids = {r[0] for r in con.execute("SELECT id FROM sessions WHERE deleted_at IS NOT NULL")}
            con.close()
            return ids
        if source == "dsh" and p.dsh_sessions:
            return readers._dsh_archived_ids(str(p.dsh_sessions))
    except Exception:
        return set()
    return set()


def _titles_override() -> dict[str, str]:
    """titles.json 人工标题叠加（仅 webui 显示层；同步侧仍走 to-dsh --titles 显式覆盖）。
    默认读包根的 titles.json（skill junction 安装指向同一文件），SESSION_SYNC_TITLES 可改指（selftest 用）。"""
    path = os.environ.get("SESSION_SYNC_TITLES") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "titles.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _display_sessions(source: str, p):
    """展示口径加载（含导入/子代理/归档，与同步口径的 reader 层排除互补）。
    api_sessions 与 api_session 共用，保证列表与详情同一口径。"""
    if source == "zcode" and p.zcode_db:
        return readers.read_zcode(p.zcode_db, include_archived=True)
    if source == "hermes" and p.hermes_db:
        return readers.read_hermes(p.hermes_db, include_archived=True, include_imports=True)
    if source == "workbuddy" and p.workbuddy_home:
        return readers.read_workbuddy(p.workbuddy_home, include_deleted=True, include_imports=True)
    if source == "dsh" and p.dsh_sessions:
        return readers.read_dsh(str(p.dsh_sessions), include_subagents=True, include_archived=True,
                                include_imports=True)
    if source == "openclaw" and p.openclaw_home:
        return readers.read_openclaw(str(p.openclaw_home), include_subagents=True)
    if source == "codex" and p.codex_sessions:
        return readers.read_codex(p.codex_sessions, include_imports=True)
    if source == "claude" and p.claude_projects:
        return readers.read_claude(p.claude_projects, include_imports=True)
    if source == "opencode" and p.opencode_db:
        return readers.read_opencode(p.opencode_db, include_imports=True)
    return readers.load_sources([source], p).get(source, [])


def api_sessions(source: str, q: str = "", t_from: int = 0, t_to: int = 0) -> list[dict]:
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    p = paths.detect()
    if getattr(p, _ROOT_ATTR[source]) is None:
        return []
    # 展示口径含回收站/归档/导入（trashed 标记）；同步口径的排除发生在 reader 层
    sessions = _display_sessions(source, p)
    hidden = _hidden_ids(source, p)
    ov = _titles_override()
    metas = []
    for s in sessions:
        m = _meta(s, imported=_imported_flag(source, s.source_id, p))
        m["trashed"] = s.source_id in hidden
        if s.source_id in ov:
            m["title"] = ov[s.source_id]
        metas.append(m)
    if q:
        ql = q.lower()
        metas = [m for m in metas if ql in (m["title"] or "").lower() or ql in (m["id"] or "").lower()]
    if t_from:
        metas = [m for m in metas if m["updated_at"] >= t_from]
    if t_to:
        metas = [m for m in metas if m["created_at"] <= t_to]
    metas.sort(key=lambda m: m["updated_at"], reverse=True)
    return metas


def api_session(source: str, sid: str) -> dict | None:
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    p = paths.detect()
    ov = _titles_override()
    for s in _display_sessions(source, p):
        if s.source_id == sid:
            d = store.session_to_dict(s)
            d["imported"] = _imported_flag(source, s.source_id, p)
            d["subagent"] = bool(getattr(s, "subagent", False))
            if s.source_id in ov:
                d["title"] = ov[s.source_id]
            return d
    return None


def api_export(source: str, sid: str, fmt: str = "md"):
    """单会话导出下载：fmt=md（人读 Markdown）/ ir（C 库同构 IR JSON，可回写 push）。"""
    from .. import archive as _archive

    p = paths.detect()
    for s in _display_sessions(source, p):
        if s.source_id == sid:
            ov = _titles_override()
            if s.source_id in ov:
                s.title = ov[s.source_id]
            safe = sid.replace("/", "_").replace("\\", "_")[:40] or "session"
            if fmt == "ir":
                body = json.dumps(store.session_to_dict(s), ensure_ascii=False, indent=1).encode("utf-8")
                return f"{safe}.ir.json", "application/json; charset=utf-8", body
            return f"{safe}.md", "text/markdown; charset=utf-8", _archive.render_markdown(s).encode("utf-8")
    return None


def api_export_source(source: str, fmt: str = "md", days: int = 0, ids: str = ""):
    """整源批量导出：fmt=md（合并单文件 Markdown）/ jsonl（一行一会话，C 库同构）。
    口径=卡片「原生」：排除 🤖子代理、🗑回收站、📥导入副本（正主在原生源）。
    ids=逗号分隔的显式会话清单（webui 列表框勾选后传入），优先于 days 过滤。"""
    from .. import archive as _archive
    from datetime import datetime, timedelta

    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    p = paths.detect()
    if getattr(p, _ROOT_ATTR[source]) is None:
        return None
    hidden = _hidden_ids(source, p)
    ov = _titles_override()
    floor = (datetime.now() - timedelta(days=days)).timestamp() * 1000 if days else 0
    want = {i for i in ids.split(",") if i} if ids else None
    picked = [s for s in _display_sessions(source, p)
              if not getattr(s, "subagent", False)
              and s.source_id not in hidden
              and not _imported_flag(source, s.source_id, p)
              and (not floor or (s.updated_at or s.created_at or 0) >= floor)
              and (want is None or s.source_id in want)]
    stamp = datetime.now().strftime("%Y%m%d")
    if fmt == "jsonl":
        body = "\n".join(json.dumps(store.session_to_dict(s), ensure_ascii=False) for s in picked).encode("utf-8")
        return f"{source}-sessions-{stamp}.jsonl", "application/x-ndjson; charset=utf-8", body
    parts = [f"# {source} 会话导出（{len(picked)} 条 · {stamp}）\n"]
    for s in picked:
        if s.source_id in ov:
            s.title = ov[s.source_id]
        parts.append(_archive.render_markdown(s))
    body = "\n\n---\n\n".join(parts).encode("utf-8")
    return f"{source}-sessions-{stamp}.md", "text/markdown; charset=utf-8", body


class Handler(BaseHTTPRequestHandler):
    server_version = "agentsync-webui/0.4.0"

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                try:
                    body = open(PAGE, "rb").read()
                except OSError:
                    return self._json({"error": "index.html missing"}, 500)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/overview":
                return self._json(api_overview())
            if u.path == "/api/sessions":
                return self._json(api_sessions(
                    qs.get("source", ""), q=qs.get("q", ""),
                    t_from=int(qs.get("from") or 0), t_to=int(qs.get("to") or 0)))
            if u.path == "/api/session":
                data = api_session(qs.get("source", ""), qs.get("id", ""))
                if data is None:
                    return self._json({"error": "not found"}, 404)
                return self._json(data)
            if u.path == "/api/export":
                out = api_export(qs.get("source", ""), qs.get("id", ""), qs.get("fmt", "md"))
                if out is None:
                    return self._json({"error": "not found"}, 404)
                fname, mime, body = out
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(fname)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/export-source":
                out = api_export_source(qs.get("source", ""), qs.get("fmt", "md"),
                                        int(qs.get("days") or 0), qs.get("ids", ""))
                if out is None:
                    return self._json({"error": "not found"}, 404)
                fname, mime, body = out
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(fname)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/backups":
                from .. import backup as backup_mod
                return self._json(backup_mod.list_snapshots(qs.get("source") or None))
            self._json({"error": "not found"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # 单端点读失败不崩服务
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path == "/api/pick-folder":
            # 原生目录选择：浏览器拿不到绝对路径，由本地服务弹系统对话框（tkinter 标准库）
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                path = filedialog.askdirectory(title="选择 agent 数据目录")
                root.destroy()
                return self._json({"ok": bool(path), "path": path or ""})
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
        if u.path == "/api/bind-path":
            # 唯一 POST 例外：目录绑定。只写 ~/.session-sync/paths.json（本地配置），
            # 不碰任何 agent 数据；校验/解析/解绑都在 paths.bind_override
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                out = paths.bind_override(str(body.get("source", "")), str(body.get("path", "")))
                return self._json(out, 200 if out.get("ok") else 400)
            except Exception as e:  # 参数坏不崩服务
                return self._json({"ok": False, "detail": f"{type(e).__name__}: {e}"}, 400)
        if u.path == "/api/backup":
            # 备份快照：只写 C 库 backups/（不碰任何 agent 数据）；口径/勾选清单可选
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                from .. import backup as backup_mod
                src = str(body.get("source", ""))
                if src not in SOURCES:
                    return self._json({"ok": False, "detail": f"unknown source: {src}"}, 400)
                raw_ids = body.get("ids") or ""
                ids = {i for i in str(raw_ids).split(",") if i} or None
                rows = backup_mod.do_backup([src], paths.detect(),
                                            with_imports=bool(body.get("with_imports")), ids=ids)
                return self._json({"ok": True, "snapshots": rows})
            except Exception as e:
                return self._json({"ok": False, "detail": f"{type(e).__name__}: {e}"}, 500)
        if u.path == "/api/restore":
            # 还原：快照 IR → 目标写入器（同 to-X 代码路径，幂等）。要求目标应用已退出。
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                from .. import backup as backup_mod
                plan = backup_mod.plan_restore(str(body.get("source", "")), str(body.get("ts", "")),
                                               paths.detect(), target=body.get("target") or None)
                if not plan.get("ok"):
                    return self._json(plan, 400)
                r = backup_mod.do_restore(str(body.get("source", "")), str(body.get("ts", "")),
                                          paths.detect(), target=body.get("target") or None)
                return self._json(r, 200 if r.get("ok") else 500)
            except Exception as e:
                return self._json({"ok": False, "detail": f"{type(e).__name__}: {e}"}, 500)
        self._json({"error": "POST 不支持（例外端点：目录绑定 /api/bind-path、备份 /api/backup、还原 /api/restore；其余写操作走 sync.py CLI）"}, 405)

    def log_message(self, fmt, *args) -> None:
        print(f"  [webui] {self.address_string()} {fmt % args}")


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    httpd = make_server(port)
    print(f"agent-session-sync dashboard（只读）: {httpd.url}   Ctrl+C 退出")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(httpd.url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        httpd.server_close()
