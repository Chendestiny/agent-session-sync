"""只读 Web dashboard：python sync.py serve → http://127.0.0.1:8321

- 全只读：零写端点（POST 一律 405），实时读源无缓存
- 仅绑定 127.0.0.1；页面为包内 index.html（单文件、无构建、离线可用）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import paths, readers, store, syncstate

DEFAULT_PORT = 8321
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

SOURCES = ["zcode", "hermes", "dsh", "codex", "workbuddy", "claude", "opencode"]
_ROOT_ATTR = {
    "zcode": "zcode_db", "hermes": "hermes_db", "dsh": "dsh_sessions",
    "codex": "codex_sessions", "workbuddy": "workbuddy_home",
    "claude": "claude_projects", "opencode": "opencode_db",
}


def _meta(s) -> dict:
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
    }


def _trash_count(name: str, p) -> int | None:
    """各源回收站/归档计数（轻量 SQL/目录数，不做全量解析）。None=该源无此概念。"""
    try:
        if name == "zcode" and p.zcode_db:
            con = sqlite3.connect(f"file:{str(p.zcode_db).replace(chr(92), '/')}?mode=ro", uri=True)
            n = con.execute(
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
            trash = os.path.normpath(os.path.join(str(p.dsh_sessions), "..", "..", ".trash-dsh"))
            if os.path.isdir(trash):
                return sum(1 for d in os.listdir(trash) if os.path.isdir(os.path.join(trash, d)))
            return 0
    except Exception:
        return None
    return None


def api_overview() -> dict:
    p = paths.detect()
    sources = []
    for name in SOURCES:
        root = getattr(p, _ROOT_ATTR[name])
        sources.append({
            "name": name, "ok": root is not None,
            "path": str(root) if root else None,
            "trash": _trash_count(name, p) if root is not None else None,
        })
    res: dict = {"sources": sources}
    if store.store_exists():
        ov = store.overview()
        res["store"] = {"dir": ov["dir"], "counts": ov["counts"], "state": ov["state"], "push": ov["push"]}
    else:
        res["store"] = None
    res["state"] = syncstate.load(p.dsh_sessions) if p.dsh_sessions else {}
    return res


def api_sessions(source: str, q: str = "", t_from: int = 0, t_to: int = 0) -> list[dict]:
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    p = paths.detect()
    if getattr(p, _ROOT_ATTR[source]) is None:
        return []
    sessions = readers.load_sources([source], p).get(source, [])
    metas = [_meta(s) for s in sessions]
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
    for s in readers.load_sources([source], p).get(source, []):
        if s.source_id == sid:
            return store.session_to_dict(s)
    return None


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
            self._json({"error": "not found"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # 单端点读失败不崩服务
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self) -> None:
        self._json({"error": "read-only dashboard (v1 has no write endpoints)"}, 405)

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
