"""四家会话存储的定位与探测。"""
from __future__ import annotations

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
    return s


def zcode_project_id(directory: str) -> str:
    """zcode session.project_id：路径小写、':' 丢弃、'\\'/' 变 '-'。

    样本：C:\\Users\\neware -> proj_c-users-neware
          C:\\Users\\neware\\.zcode\\workspace\\default -> proj_c-users-neware-.zcode-workspace-default
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
