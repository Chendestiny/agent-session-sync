"""各 agent 会话存储的定位与探测。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def home() -> Path:
    return Path(os.path.expanduser("~"))


@dataclass
class StorePaths:
    zcode_db: Path | None = None      # ~/.zcode/cli/db/db.sqlite
    dsh_sessions: Path | None = None   # $DSH_HOME/sessions（~/.dsh/sessions）
    hermes_db: Path | None = None      # $HERMES_HOME/state.db（Windows %LOCALAPPDATA%/hermes）
    codex_sessions: Path | None = None  # ~/.codex/sessions
    workbuddy_home: Path | None = None  # ~/.workbuddy-ai 或 ~/.workbuddy
    claude_projects: Path | None = None  # ~/.claude/projects/<cwd转义>/<sessionId>.jsonl
    opencode_db: Path | None = None    # %LOCALAPPDATA%/opencode/opencode.db（回退 ~/.local/share）
    qoder_home: Path | None = None     # ~/.qoder（正文 cache/projects/*/conversation-history）
    qoder_vscdb: Path | None = None    # %APPDATA%/Qoder/User/globalStorage/state.vscdb（任务索引）
    openclaw_home: Path | None = None  # ~/.openclaw（agents/main/sessions/<uuid>.jsonl）
    cursor_global_db: Path | None = None  # %APPDATA%/Cursor/User/globalStorage/state.vscdb（cursorDiskKV）
    trae_global_db: Path | None = None    # %APPDATA%/Trae/User/globalStorage/state.vscdb（同 VS Code 系布局）
    mimo_home: Path | None = None         # %LOCALAPPDATA%/mimocode 或 ~/.local/share/mimocode（OpenCode fork，待实机核验）
    kimi_home: Path | None = None         # ~/.kimi-code（Kimi Code CLI，minidb 自有格式待核）
    minimax_home: Path | None = None      # ~/.minimax（v2/sqlite/runtime-state.sqlite 注册表+消息行）
    grok_home: Path | None = None         # ~/.grok（Grok Build：sessions/<cwd编码>/<uuid7>/ JSONL 三层，仓库已核）
    copilot_home: Path | None = None      # %APPDATA%/Code/User（GitHub Copilot=VS Code 本体 chatSessions，待核验）
    gemini_home: Path | None = None       # ~/.gemini（Gemini CLI：tmp/<项目标识>/chats/session-*.json(l)，仓库已核）
    cline_home: Path | None = None        # %APPDATA%/Code/User/globalStorage/saoudrizwan.claude-dev（tasks/<id>/api_conversation_history.json，仓库已核）
    pi_home: Path | None = None           # ~/.pi（Pi Agent Harness：agent/sessions/*.jsonl；minimax 的 pi-agent 同源运行时）


def detect() -> StorePaths:
    s = StorePaths()

    zdb = home() / ".zcode" / "cli" / "db" / "db.sqlite"
    s.zcode_db = zdb if zdb.exists() else None

    dsh_home = Path(os.environ.get("DSH_HOME") or (home() / ".dsh"))
    ds = dsh_home / "sessions"
    s.dsh_sessions = ds if ds.is_dir() else None

    # hermes: HERMES_HOME 显式指定；否则 Windows 用 %LOCALAPPDATA%\hermes，
    # 其余平台按 hermes_constants 的平台默认（~/.hermes）。当前机器为 Windows 布局。
    if os.environ.get("HERMES_HOME"):
        hdb = Path(os.environ["HERMES_HOME"]) / "state.db"
    elif os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or (home() / "AppData" / "Local"))
        hdb = local / "hermes" / "state.db"
    else:
        hdb = home() / ".hermes" / "state.db"
    s.hermes_db = hdb if hdb.exists() else None

    cs = home() / ".codex" / "sessions"
    s.codex_sessions = cs if cs.is_dir() else None

    for wb in (home() / ".workbuddy-ai", home() / ".workbuddy"):  # 5.3.x 优先 -ai
        if wb.is_dir() and (wb / "workbuddy.db").exists():
            s.workbuddy_home = wb
            break

    cp = home() / ".claude" / "projects"
    s.claude_projects = cp if cp.is_dir() else None

    # opencode: CLI 与桌面版共享同一个 SQLite（agentctxsync 实证 `opencode db path`
    # 与桌面版一致）。候选：XDG_DATA_HOME → %LOCALAPPDATA% → ~/.local/share。
    for oc in (
        Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else None,
        Path(os.environ.get("LOCALAPPDATA") or "") / "opencode" / "opencode.db" if os.environ.get("LOCALAPPDATA") else None,
        home() / ".local" / "share" / "opencode" / "opencode.db",
    ):
        if oc and oc.is_file():
            s.opencode_db = oc
            break

    # qoder（阿里 AI IDE，VS Code 系）：任务索引在 globalStorage/state.vscdb，
    # 正文在 ~/.qoder/cache/projects——两者都在才算装了
    qdb = None
    for base in (os.environ.get("APPDATA"), str(home() / ".config")):
        if not base:
            continue
        cand = Path(base) / "Qoder" / "User" / "globalStorage" / "state.vscdb"
        if cand.is_file():
            qdb = cand
            break
    if qdb is not None:
        s.qoder_vscdb = qdb
        qh = home() / ".qoder"
        if (qh / "cache" / "projects").is_dir():
            s.qoder_home = qh

    # openclaw（开源个人 AI 助手）：~/.openclaw/agents/main/sessions/
    ocl = home() / ".openclaw"
    if (ocl / "agents" / "main" / "sessions").is_dir():
        s.openclaw_home = ocl

    # cursor / trae（VS Code 系 AI IDE）：cursor 会话在 globalStorage/state.vscdb 的
    # cursorDiskKV 表（composerData:* 会话头 + bubbleId:* 消息）。trae CN 实测无此表，
    # 会话正典在 ModularData/ai-agent/database.db 且整库自加密（读取阻断，读取器返回 0，
    # 详见 docs/agents/trae.md）；目录名带 " CN" 后缀，两个名字都认。
    base = os.environ.get("APPDATA")
    if base:
        cdb = Path(base) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
        if cdb.is_file():
            s.cursor_global_db = cdb
        for tdir in ("Trae", "Trae CN"):
            tdb = Path(base) / tdir / "User" / "globalStorage" / "state.vscdb"
            if tdb.is_file():
                s.trae_global_db = tdb
                break

    # minimax（MiniMax Code）：~/.minimax 下 v2/sqlite/runtime-state.sqlite 才算装了
    mm = home() / ".minimax"
    if (mm / "v2" / "sqlite" / "runtime-state.sqlite").is_file():
        s.minimax_home = mm

    # mimo（小米 MiMo-Code，OpenCode fork；仓库 packages/shared/src/global.ts 已核验）：
    # MIMOCODE_HOME=<root>（data 在 <root>/data）或 XDG 数据目录（Win=%LOCALAPPDATA%\mimocode）。
    # 库名 mimocode.db（storage/db.ts），schema 同构 opencode 的 session/message/part。
    for mc in (
        Path(os.environ["MIMOCODE_HOME"]) / "data" if os.environ.get("MIMOCODE_HOME") else None,
        Path(os.environ.get("XDG_DATA_HOME") or "") / "mimocode" if os.environ.get("XDG_DATA_HOME") else None,
        Path(os.environ.get("LOCALAPPDATA") or "") / "mimocode" if os.environ.get("LOCALAPPDATA") else None,
        home() / ".local" / "share" / "mimocode",
    ):
        if mc and mc.is_dir():
            s.mimo_home = mc
            break

    # kimi（Kimi Code CLI，仓库 apps/kimi-code/src/constant/app.ts 已核验）：
    # 数据目录 = $KIMI_CODE_HOME 或 ~/.kimi-code（注意不是 ~/.kimi）；会话在其自研
    # minidb 里（packages/minidb，非 sqlite），格式细节待实装后核验
    for kc in (
        Path(os.environ["KIMI_CODE_HOME"]) if os.environ.get("KIMI_CODE_HOME") else None,
        home() / ".kimi-code",
    ):
        if kc and kc.is_dir():
            s.kimi_home = kc
            break

    # grok（xAI Grok Build CLI，仓库 xai-dirs + pager/docs/17-sessions.md 已核验）：
    # $GROK_HOME 或 ~/.grok，会话在 sessions/<cwd编码>/<uuid7>/（summary.json +
    # updates.jsonl 正典 + chat_history.jsonl 原始层，纯 JSONL 明文）
    gh = Path(os.environ["GROK_HOME"]) if os.environ.get("GROK_HOME") else (home() / ".grok")
    if gh.is_dir():
        s.grok_home = gh

    # copilot（GitHub Copilot = VS Code 本体的 agent chat）：会话在
    # workspaceStorage/<hash>/chatSessions/*.json；仅在探测到 chatSessions 时才算装了
    if os.environ.get("APPDATA"):
        cu = Path(os.environ["APPDATA"]) / "Code" / "User"
        if cu.is_dir():
            for ws in (cu / "workspaceStorage").glob("*/chatSessions"):
                if ws.is_dir():
                    s.copilot_home = cu
                    break

    # gemini（Google Gemini CLI，仓库已核验）：$GEMINI_CLI_HOME 或 ~/.gemini，
    # 会话在 tmp/<项目标识>/chats/session-<时间戳>-<id8>.json(.jsonl)
    gdir = Path(os.environ["GEMINI_CLI_HOME"]) if os.environ.get("GEMINI_CLI_HOME") else (home() / ".gemini")
    if gdir.is_dir():
        s.gemini_home = gdir

    # cline（Cline VS Code 扩展，仓库已核验）：任务 JSON 在
    # globalStorage/saoudrizwan.claude-dev/tasks/<taskId>/api_conversation_history.json
    if os.environ.get("APPDATA"):
        cd = Path(os.environ["APPDATA"]) / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
        if cd.is_dir():
            s.cline_home = cd

    # pi（Pi Agent Harness @earendil-works/pi-coding-agent，仓库已核验）：
    # $PI_CODING_AGENT_SESSION_DIR 或 ~/.pi/agent/sessions/<时间戳>_<id>.jsonl；
    # minimax 的 pi-agent 运行时同源，但存储各自独立（~/.pi 与 ~/.minimax）
    pidir = (Path(os.environ["PI_CODING_AGENT_SESSION_DIR"])
             if os.environ.get("PI_CODING_AGENT_SESSION_DIR")
             else home() / ".pi")
    if pidir.is_dir():
        s.pi_home = pidir
    return apply_overrides(s)


# ── 手动绑定（webui「绑定目录」按钮落点）─────────────────────────────
# 持久化在 C 库根（$SESSION_SYNC_HOME 或 ~/.session-sync）/paths.json，
# 机器本地配置、优先于自动探测；不写进仓库代码（仓库是公共的）。

def _store_root() -> Path:
    return Path(os.environ.get("SESSION_SYNC_HOME") or (home() / ".session-sync"))


def overrides_path() -> Path:
    return _store_root() / "paths.json"


def load_overrides() -> dict:
    try:
        data = json.load(open(overrides_path(), encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_overrides(s: StorePaths) -> StorePaths:
    for _src, fields in load_overrides().items():
        if not isinstance(fields, dict):
            continue
        for k, v in fields.items():
            if hasattr(s, k) and isinstance(v, str) and v:
                setattr(s, k, Path(v))
    return s


def bind_override(source: str, raw: str, save: bool = True) -> dict:
    """把用户粘贴的目录/文件路径解析成对应 StorePaths 字段并持久化。

    返回 {ok, field?, value?, detail}；raw 为空 = 解绑该源。解析规则：接受应用
    根目录或直接目标（如 state.db / sessions 目录），逐层尝试已知结构。
    """
    rules = {
        # source: (字段, 文件型?, 目录候选补全路径列表, 目录有效性判断, 子目录命中时绑根目录?)
        "zcode": ("zcode_db", True, ["cli/db/db.sqlite", "db/db.sqlite", "db.sqlite"], None, False),
        "dsh": ("dsh_sessions", False, ["sessions"], lambda d: d.name == "sessions", False),
        "hermes": ("hermes_db", True, ["state.db"], None, False),
        "codex": ("codex_sessions", False, ["sessions"], lambda d: d.name == "sessions", False),
        "workbuddy": ("workbuddy_home", False, ["."], lambda d: (d / "workbuddy.db").exists(), True),
        "claude": ("claude_projects", False, ["projects"], lambda d: d.name == "projects", False),
        "opencode": ("opencode_db", True, ["opencode.db"], None, False),
        "qoder": ("qoder_home", False, ["cache/projects"], lambda d: (d / "cache" / "projects").is_dir(), True),
        "openclaw": ("openclaw_home", False, ["agents/main/sessions"], lambda d: (d / "agents" / "main" / "sessions").is_dir(), True),
        "cursor": ("cursor_global_db", True,
                   ["User/globalStorage/state.vscdb", "globalStorage/state.vscdb", "state.vscdb"], None, False),
        "trae": ("trae_global_db", True,
                 ["User/globalStorage/state.vscdb", "globalStorage/state.vscdb", "state.vscdb"], None, False),
        "minimax": ("minimax_home", False, ["v2/sqlite/runtime-state.sqlite"],
                    lambda d: (d / "v2" / "sqlite" / "runtime-state.sqlite").is_file(), True),
        "mimo": ("mimo_home", False, ["."], lambda d: d.is_dir(), True),
        "kimi": ("kimi_home", False, ["."], lambda d: d.is_dir(), True),
        "grok": ("grok_home", False, ["sessions"], lambda d: d.name == ".grok" or (d / "sessions").is_dir(), True),
        "copilot": ("copilot_home", False, ["."], lambda d: d.is_dir(), True),
        "gemini": ("gemini_home", False, ["."], lambda d: d.is_dir(), True),
        "cline": ("cline_home", False, ["."], lambda d: d.is_dir(), True),
        "pi": ("pi_home", False, ["agent/sessions"], lambda d: (d / "agent" / "sessions").is_dir() or d.name == ".pi", True),
    }
    if source not in rules:
        return {"ok": False, "detail": f"未知源 {source}"}
    field, file_like, subdirs, dir_ok, bind_root = rules[source]
    raw = (raw or "").strip().strip('"')
    ov = load_overrides()
    if not raw:  # 解绑
        ov.pop(source, None)
        if save:
            _store_root().mkdir(parents=True, exist_ok=True)
            json.dump(ov, open(overrides_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return {"ok": True, "unbound": True, "detail": f"已解除 {source} 的手动绑定（恢复自动探测）"}

    p = Path(raw)
    if not p.exists():
        return {"ok": False, "detail": f"路径不存在：{raw}"}
    if p.is_file():
        if file_like:
            value = p
        else:
            return {"ok": False, "detail": f"{source} 需要目录，不是文件"}
    else:
        value = None
        # 目录直接命中（如粘的就是 sessions 目录本身）
        if dir_ok and dir_ok(p):
            value = p
        for sub in subdirs:
            cand = p / sub if sub != "." else p
            if cand.exists():
                value = p if bind_root else cand  # home 型字段绑根目录，sessions 型绑目录本身
                break
        if value is None:
            return {"ok": False, "detail": f"目录结构不像 {source} 的存储（找不到 {' / '.join(subdirs)}）"}
    ov[source] = {field: str(value)}
    if save:
        _store_root().mkdir(parents=True, exist_ok=True)
        json.dump(ov, open(overrides_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"ok": True, "field": field, "value": str(value),
            "detail": f"已绑定 {field} = {value}（优先于自动探测）"}


def zcode_project_id(directory: str) -> str:
    """zcode session.project_id：路径小写、':' 丢弃、'\\'/' 变 '-'。

    样本：C:\\Users\\alice -> proj_c-users-alice
          C:\\Users\\alice\\.zcode\\workspace\\default -> proj_c-users-alice-.zcode-workspace-default
    """
    slug = []
    for ch in directory:
        if ch == ":":
            continue
        if ch in "\\/":
            slug.append("-")
        else:
            slug.append(ch.lower())
    return "proj_" + "".join(slug)
