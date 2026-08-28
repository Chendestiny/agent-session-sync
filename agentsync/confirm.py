"""人在回路（HITL）两道确认：① 来源区 ② 数据量。

原则：
- 交互终端（TTY）：弹菜单，人当场选定，回车有默认值，q/Ctrl+C 取消。
- 非交互（agent/脚本）：显式 CLI 参数即确认（参数=人的决定）；
  参数缺失时由调用方拒绝执行，绝不默默替人做决定。
"""
from __future__ import annotations

import re
import sys

# 默认同步源（dsh 是目标不是源）
SYNC_SOURCES = ["zcode", "hermes", "codex", "workbuddy", "claude", "opencode"]

# 非交互缺参时的教学文案（sync.py / sync-finish.py 共用）
NONINTERACTIVE_HELP = (
    "非交互环境无法弹确认菜单：请把两道确认写成显式参数后重跑（参数即确认）——\n"
    "  python sync.py to-dsh --apply --source all --scope inc\n"
    "  python sync-finish.py --sources zcode,workbuddy --scope 7d\n"
    "  --source/--sources：all 或逗号组合（zcode,hermes,codex,workbuddy,claude,opencode）\n"
    "  --scope          ：inc(仅增量) | 7d | 30d | 任意N天(如 14 或 14d) | all(全部历史)"
)

_QUIT = ("q", "quit", "exit", "取消")


def interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


# ── 数据量 scope ─────────────────────────────────────────────────────────

def parse_scope(raw: str) -> dict:
    """'inc' | 'all' | '7d' | '14' → {'kind': 'inc'|'days'|'all', 'days': int|None}。

    数字（可带 d 后缀）= 最近 N 天。无法识别即退出，绝不猜默认。
    """
    s = (raw or "").strip().lower()
    if s in _QUIT:
        raise SystemExit("已取消：未做任何修改。")
    if s in ("inc", "increment", "增量"):
        return {"kind": "inc", "days": None}
    if s in ("all", "*", "全部", "全量", "历史"):
        return {"kind": "all", "days": None}
    if s.endswith("d"):
        s = s[:-1]
    if s.isdigit() and int(s) > 0:
        return {"kind": "days", "days": int(s)}
    raise SystemExit(f"无法识别的数据量：{raw!r}（可选 inc | 7d | 30d | 任意N天 | all）")


def scope_label(scope: dict) -> str:
    if scope["kind"] == "inc":
        return "仅增量"
    if scope["kind"] == "all":
        return "全部历史"
    return f"最近 {scope['days']} 天"


def scope_spec(scope: dict) -> str:
    """scope dict → CLI 参数写法（反向转换，供透传/回显）。"""
    if scope["kind"] == "inc":
        return "inc"
    if scope["kind"] == "all":
        return "all"
    return f"{scope['days']}d"


def prompt_scope() -> dict:
    print("── 确认 2/2 · 同步多少数据 " + "─" * 24)
    print("  1) 仅增量（上次同步后有新内容的会话；首次=全部）  ← 回车默认")
    print("  2) 最近 7 天")
    print("  3) 最近 30 天")
    print("  4) 全部历史")
    print("  也可直接输入天数（如 14 = 最近 14 天）；q 取消")
    try:
        ans = input("选择 [1]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("已取消：未做任何修改。")
    if not ans or ans == "1":
        return parse_scope("inc")
    if ans == "2":
        return parse_scope("7d")
    if ans == "3":
        return parse_scope("30d")
    if ans == "4":
        return parse_scope("all")
    return parse_scope(ans)


# ── 来源区 sources ───────────────────────────────────────────────────────

_NUM = {str(i + 2): name for i, name in enumerate(SYNC_SOURCES)}  # 菜单编号→来源


def parse_sources_answer(ans: str) -> list[str]:
    s = (ans or "").strip().lower()
    if s in _QUIT:
        raise SystemExit("已取消：未做任何修改。")
    if not s or s in ("1", "all", "全部"):
        return list(SYNC_SOURCES)
    out: list[str] = []
    for tok in re.split(r"[,，\s]+", s):
        if not tok:
            continue
        name = _NUM.get(tok, tok)
        if name not in SYNC_SOURCES:
            raise SystemExit(f"未知来源：{tok}（可选 1-{len(SYNC_SOURCES) + 1} 或 {'/'.join(SYNC_SOURCES)}）")
        if name not in out:
            out.append(name)
    return sorted(out, key=SYNC_SOURCES.index)


def prompt_sources() -> list[str]:
    print("── 确认 1/2 · 同步哪些来源区 " + "─" * 24)
    print(f"  1) 全部（{' + '.join(SYNC_SOURCES)}）  ← 回车默认")
    print("  " + "   ".join(f"{i + 2}) {name}" for i, name in enumerate(SYNC_SOURCES)))
    print("  组合输入编号或名称：如 2,5 或 zcode,workbuddy；q 取消")
    try:
        ans = input("选择 [1]: ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("已取消：未做任何修改。")
    return parse_sources_answer(ans)
