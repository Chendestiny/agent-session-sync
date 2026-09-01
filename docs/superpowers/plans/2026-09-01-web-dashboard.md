# Web Dashboard（v0.4.0 只读可视化）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sync.py serve` 起一个仅绑定 127.0.0.1 的只读本地 Web dashboard，三视图可视化 7 家源 + C 库 + 水位线。

**Architecture:** 复用 `agentsync` 现有 readers/store/syncstate，新增 `agentsync/webui/` 包（`__init__.py` 服务与 API + `index.html` 单文件页面），`sync.py` 加 `serve` 子命令。零第三方依赖（stdlib `http.server`）。v1 零写端点。

**Tech Stack:** Python 3.10+ stdlib only；前端单 HTML（内联 CSS/JS，无构建、无 CDN）。

**Spec:** `docs/superpowers/specs/2026-09-01-web-dashboard-design.md`

## Global Constraints

- 零新第三方依赖：只用 stdlib（http.server/json/urllib 等）+ 现有 `agentsync` 模块
- v1 全只读：无 POST/PUT/DELETE 端点，POST 一律 405
- 只绑定 `127.0.0.1`；默认端口 8321（`--port` 可改）
- `index.html` 单文件、内联 CSS/JS、无外部 CDN、离线可用；UI 文案中文
- selftest 不碰真实数据：环境变量重定向（DSH_HOME / SESSION_SYNC_HOME）到 `.selftest/` 沙箱
- install.ps1 不改动（bundle 里 `agentsync` 整目录递归复制，webui/ 自动随包；且该文件必须保持 ASCII/BOM-less）
- 提交用仓库级身份（Chendestiny）；每个 Task 一个提交
- 对规格的两处无害偏差（实施取优）：`agentsync/webui.py` → `agentsync/webui/__init__.py` 包目录（避免同名模块/目录冲突，代码与页面同目录）；开浏览器用 stdlib `webbrowser.open`（跨平台）而非 Windows `start`

## 顺手修复（本轮发现的真实 bug）

`store.py` 的 `session_to_dict`/`session_from_dict` **没有序列化 `Turn.time`**——上次时间戳修复只穿了 readers/writers，C 库漏了；push 走 C 的会话时间会退化为写入器 fallback。Task 1 一并修复并加回归断言。

---

### Task 1: store 往返保住 Turn.time + readers.load_sources 统一 fan-out

**Files:**
- Modify: `agentsync/store.py:55-73`（session_to_dict turns 字典）、`agentsync/store.py:92`（session_from_dict Turn 构造）
- Modify: `agentsync/readers.py`（文件末尾追加 load_sources）
- Modify: `sync.py:44-70`（load_sources 改为委托）
- Test: 临时脚本 `.tmp-store-roundtrip.py` + `python sync.py selftest`（83 项不回归）

**Interfaces:**
- Produces: `readers.load_sources(which: list[str], p: paths.StorePaths) -> dict[str, list[Session]]`（路径缺失的源跳过；Task 2 的 webui 消费）
- Produces: store 往返字典 turn 含 `"time": int`（ms；旧文件缺 key → 0）

- [ ] **Step 1: 写失败验证（临时脚本，避免 PS 引号坑）**

`.tmp-store-roundtrip.py`：
```python
import sys
sys.path.insert(0, r"D:\Project\agent-session-sync")
from agentsync import store

d = {"source": "t", "source_id": "t1", "created_at": 1,
     "turns": [{"prompt": "p", "time": 1735689600123, "steps": []}]}
out = store.session_to_dict(store.session_from_dict(d))
assert out["turns"][0].get("time") == 1735689600123, out["turns"][0]
print("roundtrip ok")
```
Run: `python .tmp-store-roundtrip.py` → Expected: AssertionError（time 丢失）

- [ ] **Step 2: 实现**

`session_to_dict` turn 字典改为：
```python
            {
                "prompt": t.prompt,
                "time": t.time,
                "steps": [
```
`session_from_dict` Turn 构造改为：
```python
        turns.append(Turn(prompt=t.get("prompt") or "", steps=steps, time=int(t.get("time") or 0)))
```
`readers.py` 末尾追加：
```python
def load_sources(which, p):
    """统一 fan-out：{source: [Session]}（存储缺失的源跳过）。"""
    loaded: dict[str, list[Session]] = {}
    if "zcode" in which and p.zcode_db:
        loaded["zcode"] = read_zcode(p.zcode_db)
    if "hermes" in which and p.hermes_db:
        loaded["hermes"] = read_hermes(p.hermes_db)
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
    return loaded
```
`sync.py` 的 `load_sources` 函数体替换为：
```python
def load_sources(which: list[str], p: paths.StorePaths):
    return readers.load_sources(which, p)
```

- [ ] **Step 3: 验证**

Run: `python .tmp-store-roundtrip.py` → `roundtrip ok`；
Run: `python sync.py selftest` → SELFTEST PASSED（83 项）；删除 `.tmp-store-roundtrip.py`

- [ ] **Step 4: Commit**
```bash
git add agentsync/store.py agentsync/readers.py sync.py
git commit -m "store 往返保住 Turn.time（C 库漏序列化修复，push 走 C 不再压平）+ readers.load_sources 统一 fan-out"
```

---

### Task 2: webui 包——只读服务四端点 + serve 子命令

**Files:**
- Create: `agentsync/webui/__init__.py`
- Modify: `sync.py`（cmd_serve 函数 + main() 里 serve 子解析器）
- Test: `.tmp-webui-api.py`（后台线程起 make_server(0)，urllib 打端点断言）

**Interfaces:**
- Consumes: `readers.load_sources`（Task 1）、`store.session_to_dict/overview/store_exists`、`syncstate.load`、`paths.detect`
- Produces:
  - `webui.SOURCES = ["zcode","hermes","dsh","codex","workbuddy","claude","opencode"]`
  - `webui.make_server(port=8321) -> ThreadingHTTPServer`（实例带 `.url` 属性；port=0 由内核分配）
  - `webui.serve(port=8321, open_browser=True) -> None`（阻塞；Ctrl+C 退出）
  - `webui.api_overview() -> dict`、`webui.api_sessions(source, q="", t_from=0, t_to=0) -> list[dict]`、`webui.api_session(source, sid) -> dict | None`
  - 端点契约（Task 3 前端消费）：
    - `GET /` → index.html 字节（缺失时 500 JSON）
    - `GET /api/overview` → `{"sources":[{name,ok,path}], "store":{dir,counts,state,push}|null, "state":{源:水位ms}}`
    - `GET /api/sessions?source=&q=&from=&to=` → `[{source,id,title,cwd,created_at,updated_at,turns,messages,tools,span_first,span_last,path}]`（按 updated_at 降序）
    - `GET /api/session?source=&id=` → `store.session_to_dict` 全量 IR（turns[].time 含毫秒时间）
    - 其余 404；任何 POST → 405

- [ ] **Step 1: 创建 `agentsync/webui/__init__.py`（完整代码）**

```python
"""只读 Web dashboard：python sync.py serve → http://127.0.0.1:8321

- 全只读：零写端点（POST 一律 405），实时读源无缓存
- 仅绑定 127.0.0.1；页面为包内 index.html（单文件、无构建、离线可用）
"""
from __future__ import annotations

import json
import os
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


def api_overview() -> dict:
    p = paths.detect()
    sources = []
    for name in SOURCES:
        root = getattr(p, _ROOT_ATTR[name])
        sources.append({"name": name, "ok": root is not None, "path": str(root) if root else None})
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
```

- [ ] **Step 2: sync.py 接 serve 子命令**

`cmd_*` 函数区追加：
```python
def cmd_serve(args):
    from agentsync import webui

    webui.serve(port=args.port, open_browser=not args.no_open)
```
main() 里 `status` 子解析器后追加：
```python
    s = sub.add_parser("serve", help="只读 Web dashboard（127.0.0.1，浏览器可视化 7 家源 + C 库）")
    s.add_argument("--port", type=int, default=8321, help="监听端口（默认 8321）")
    s.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    s.set_defaults(fn=cmd_serve)
```

- [ ] **Step 3: 端点冒烟（临时脚本，DSH_HOME/SESSION_SYNC_HOME 重定向防碰真数据）**

`.tmp-webui-api.py`：
```python
import json, os, sys, tempfile, threading, urllib.request, urllib.error
sys.path.insert(0, r"D:\Project\agent-session-sync")
box = tempfile.mkdtemp(prefix="webui-smoke-")
os.environ["DSH_HOME"] = box          # dsh sessions 指向空沙箱
os.environ["SESSION_SYNC_HOME"] = box # C 库指向空沙箱
from agentsync import webui

httpd = webui.make_server(0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

st, ov = None, None
with opener.open(httpd.url + "/api/overview") as r:
    st, ov = r.status, json.loads(r.read().decode("utf-8"))
assert st == 200 and {s["name"] for s in ov["sources"]} == set(webui.SOURCES), ov
with opener.open(httpd.url + "/api/sessions?source=dsh") as r:
    assert r.status == 200 and json.loads(r.read().decode()) == []
try:
    opener.open(httpd.url + "/api/overview"); code = 200
except urllib.error.HTTPError as e:
    code = e.code
assert code == 405, code
try:
    opener.open(httpd.url + "/nope"); code = 200
except urllib.error.HTTPError as e:
    code = e.code
assert code == 404, code
httpd.shutdown(); httpd.server_close()
print("webui api smoke ok")
```
Run: `python .tmp-webui-api.py` → `webui api smoke ok`（注：GET / 此刻 500 属预期，index.html 是 Task 3）

- [ ] **Step 4: Commit**
```bash
git add agentsync/webui/__init__.py sync.py
git commit -m "webui 只读服务：serve 子命令 + overview/sessions/session/页面四端点，仅绑 127.0.0.1，POST 一律 405"
```

---

### Task 3: index.html 单文件页面（三视图）

**Files:**
- Create: `agentsync/webui/index.html`

**Interfaces:**
- Consumes: Task 2 的四端点契约（字段名逐一对应，勿改）
- Produces: 无（叶子产物）

**页面规格（无口味自由度，照此实现）：**

- 主题：深色（`#0f1115` 背景 / `#e6e6e6` 文字 / 强调 `#4f8cff`），system-ui 字体，中文 UI
- 布局：顶栏（标题 + 视图切换 + 刷新按钮）+ 主区；hash 路由 `#/`（总览）、`#/sessions`（列表）、`#/session/<source>/<id>`（详情，id 用 encodeURIComponent）
- 源配色（时间轴/标签统一）：zcode `#f59e0b`、hermes `#10b981`、dsh `#4f8cff`、codex `#a78bfa`、workbuddy `#f472b6`、claude `#fb923c`、opencode `#22d3ee`
- **总览 `#/`**：
  - 先取 `/api/overview` 渲染源卡（名称/健康灯：绿=ok 红=missing/path 灰字）；C 库卡（dir、counts、state/push 水位线格式化为 `MM-dd HH:mm`，空则「-」）
  - 再**每源并行** fetch `/api/sessions?source=X` 填卡上的「N 会话 · 最近 <title>」并把条目并入时间轴（单源慢不阻塞其他源）
  - 时间轴：7 条泳道（每源一行，行高 30px，左标签列 90px）；x 域 = 全部 metas 的 span_first..span_last（无数据退 now-30d..now）；会话条 `position:absolute`，`left/width` 按毫秒线性映射（min-width 3px），`title` 属性原生 tooltip（源/标题/时间/轮数），点击跳详情
- **列表 `#/sessions`**：
  - 筛选栏：源 chip 多选（全亮=全选）、日期起止 `<input type="date">`、关键词输入框、过滤按钮（前端本地过滤已拉取的全量 metas，不打服务端）
  - 表格列：源 | 标题 | 创建时间 | 最后活动 | 轮数 | 消息数 | 工具调用；行点击跳详情；空态文案「该筛选下无会话」
- **详情 `#/session/<source>/<id>`**：
  - 取 `/api/session?source=&id=`；头部卡：源徽章/标题/cwd/创建-最后活动跨度/轮数/id（等宽字体）
  - **轮次时间条**：横向 SVG（高 64px），x 域 = turns[].time 的 min..max（全部为 0 时显示提示「该会话无轮次时间（旧数据）」），每轮一根 2px 竖条，hover 显示 `#i 时间`；时间全挤一点 = 压平 bug 一眼可见（这正是本视图的存在意义）
  - 轮次列表：每轮一行卡片（序号/时间 `MM-dd HH:mm`/prompt 预览 200 字截断/工具调用数徽章/消息数），点击展开该轮 steps（text/reasoning 块 + tool-call 名称与参数 + tool-result 摘要，长文本 `<pre>` 滚动）
- 通用：所有 fetch 包 try/catch，失败在对应区域红字显示错误不白屏；时间戳格式化函数 `fmt(ms)`；XSS 防护——所有动态文本经 `esc()`（textContent 或转义 `&<>"`
- [ ] **Step 1: 实现 index.html（按上述规格，单文件，预计 500-700 行）**
- [ ] **Step 2: 冒烟**
```bash
python sync.py serve --no-open   # 后台起
curl -s http://127.0.0.1:8321/ | Select-String "agentsync"   # 页面可达
curl -s "http://127.0.0.1:8321/api/overview" | python -m json.tool | Select-Object -First 20
# 人工：浏览器开 http://127.0.0.1:8321 走三视图（用户桌面验证）
```
- [ ] **Step 3: Commit**
```bash
git add agentsync/webui/index.html
git commit -m "webui 前端单页：总览泳道时间轴/会话列表筛选/轮次时间条三视图（无构建无CDN离线可用）"
```

---

### Task 4: selftest 第 9 节——webui 端到端断言

**Files:**
- Modify: `sync.py` cmd_selftest（第 889 行 `SELFTEST FAILED` 汇总之前插入第 9 节）

**Interfaces:**
- Consumes: `webui.make_server`、Task 1 的 store 往返、既有 `fake()` 会话工厂与 `dshwrite.plan_write/apply_write`

- [ ] **Step 1: 在 cmd_selftest 末段（`# 汇总` 打印之前）插入**

```python
    # ── 9. webui（只读 dashboard）───────────────────────────────────────
    print("\n== 9. webui 只读服务 ==")
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request

    from agentsync import store as _store
    from agentsync import webui

    # 9.1 store 往返保住 Turn.time（C 库时间戳修复回归）
    d = _store.session_to_dict(_store.session_from_dict(
        {"source": "t", "source_id": "t1", "created_at": 1,
         "turns": [{"prompt": "p", "time": 1735689600123, "steps": []}]}))
    check(d["turns"][0].get("time") == 1735689600123, "store 往返保留 Turn.time")

    # 9.2 沙箱起服务（DSH_HOME/SESSION_SYNC_HOME 重定向，不碰真实数据）
    old_env = {k: os.environ.get(k) for k in ("DSH_HOME", "SESSION_SYNC_HOME")}
    sandbox_dsh_parent = os.path.join(box, "webui-dsh-home")
    os.makedirs(sandbox_dsh_parent, exist_ok=True)
    os.environ["DSH_HOME"] = sandbox_dsh_parent
    os.environ["SESSION_SYNC_HOME"] = os.path.join(box, "webui-cstore")
    try:
        fake_sess = fake(v2=True)
        base_ms = 1735689600000  # 2025-01-01 00:00 UTC
        for i, t in enumerate(fake_sess.turns):
            t.time = base_ms + i * 3600_000
        plan = dshwrite.plan_write(os.path.join(sandbox_dsh_parent, "sessions"), fake_sess, None)
        dshwrite.apply_write(plan)

        httpd = webui.make_server(0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def get(path: str):
            with opener.open(httpd.url + path) as r:
                return r.status, json.loads(r.read().decode("utf-8"))

        st, home = get("/")
        check(st == 200 and "agentsync" in home, "GET / 返回页面")
        st, ov = get("/api/overview")
        names = {s["name"] for s in ov.get("sources", [])}
        check(st == 200 and names == set(webui.SOURCES), "overview 七源卡齐全")
        st, metas = get("/api/sessions?source=dsh")
        check(st == 200 and any(m["id"].startswith("import-") for m in metas), "sessions 读到沙箱导入会话")
        sid = next((m["id"] for m in metas if m["id"].startswith("import-")), "")
        st, detail = get("/api/session?source=dsh&id=" + urllib.parse.quote(sid))
        tt = (detail or {}).get("turns") or []
        check(st == 200 and bool(tt) and all(t.get("time") for t in tt), "detail 轮次时间穿透（Turn.time 全链路）")
        req = urllib.request.Request(httpd.url + "/api/overview", data=b"{}", method="POST")
        try:
            opener.open(req)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check(code == 405, "POST 被拒（405：v1 零写端点）")
        httpd.shutdown()
        httpd.server_close()
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
```

- [ ] **Step 2: 跑 selftest**
Run: `python sync.py selftest` → SELFTEST PASSED，新增 6 项（合计 89）
- [ ] **Step 3: Commit**
```bash
git add sync.py
git commit -m "selftest 第9节：webui 端点端到端 + Turn.time 全链路穿透 + 405 零写断言（83→89 项）"
```

---

### Task 5: README 双语 + 本地实跑 + 发布三连

**Files:**
- Modify: `README.md`、`README_EN.md`（具体脚本/Quick Start 区加 serve；目录结构 agentsync 行提 webui/）

- [ ] **Step 1: README.md 「⌨️ 具体脚本」bash 块末尾加**
```bash
# ④ 只读可视化 dashboard（本地浏览器，127.0.0.1:8321，零写端点）
python sync.py serve
```
「目录结构」agentsync 说明补 `webui/`；README_EN.md 对应 Quick Start/Layout 同步
- [ ] **Step 2: 本地实跑**：`python sync.py serve --no-open` 后台 + `curl` 三端点（overview 真数据形状、sessions?source=hermes 条数、session 详情含 turns[].time）；停服
- [ ] **Step 3: 发布三连**：commit → push（`git rev-parse HEAD` vs `origin/main` 核验）→ `.\install.ps1`（本地源模式，自动 .bak 备份旧 skill 副本，webui/ 随 agentsync 递归进副本）→ 副本内 `python sync.py selftest` 复验

## Self-Review

- 规格覆盖：三视图（Task 3）、四端点（Task 2）、零依赖/127.0.0.1/405（Task 2+4 断言）、selftest 第 9 节（Task 4）、README（Task 5）、install.ps1 复制范围（已核实 bundle 含 `agentsync` 递归，无需改）✔
- 顺手修复 store Turn.time（Task 1）超出规格但为真 bug，已标注 ✔
- 类型一致：`_meta` 字段名与 Task 3 前端规格一致；`make_server().url` 在 Task 2/4 一致 ✔
- 无占位：Task 3 为结构规格（视图/字段/配色/交互逐项锁定），实现即翻译 ✔
