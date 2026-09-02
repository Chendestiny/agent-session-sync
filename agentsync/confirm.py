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
SYNC_SOURCES = ["zcode", "hermes", "codex", "workbuddy", "claude", "opencode", "qoder", "openclaw",
                "cursor", "trae"]

# 非交互缺参时的教学文案（sync.py / sync-finish.py 共用）
NONINTERACTIVE_HELP = (
    "非交互环境无法弹确认菜单：请把两道确认写成显式参数后重跑（参数即确认）——\n"
    "  python sync.py to-dsh --apply --source all --scope inc\n"
    "  python sync-finish.py --sources zcode,workbuddy --scope 7d\n"
    "  --source/--sources：all 或逗号组合（zcode,hermes,codex,workbuddy,claude,opencode,qoder,openclaw,cursor,trae）\n"
    "  --scope          ：inc(仅增量) | 7d | 30d | 任意N天(如 14 或 14d) | all(全部历史)"
)

# 历史全量的人工拦截（非交互必须显式同意 token）
NONINTERACTIVE_HISTORY_HELP = (
    "⚠ 历史全量同步需要人工确认，非交互环境无法弹 y/N：\n"
    "  · 只想同步增量/近期数据 → --scope inc（默认安全）或 --scope 7d/30d/N天\n"
    "  · 确实要全量历史（人已拍板）→ 由人显式追加 --confirm-history，例如：\n"
    "      python sync.py to-dsh --apply --source all --scope all --confirm-history"
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
    print("  1) 仅增量（上次同步后有新内容的会话）  ← 回车默认")
    print("  2) 最近 7 天")
    print("  3) 最近 30 天")
    print("  4) 全部历史（历史全量，需二次确认）")
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


def history_full_sources(scope: dict, selected: list[str], state: dict, available=None) -> list[str]:
    """哪些源本次将按『全部历史』处理（触发人工拦截）：

    - scope=all：全部所选源
    - scope=inc 且该源无增量基准（首次运行）：等同全量
    - 天数窗口（7d/30d/N天）：有界，不拦截
    """
    avail = set(available) if available is not None else set(selected)
    if scope.get("kind") == "all":
        return [s for s in selected if s in avail]
    if scope.get("kind") == "inc":
        return [s for s in selected if s in avail and not state.get(s)]
    return []


def prompt_history_confirm(detail: str) -> bool:
    """历史全量二次确认（y/N，默认 N=取消）。"""
    print(f"⚠ 人工拦截：{detail}，将扫描/导入该源全部历史会话。")
    try:
        ans = input("确认执行？[y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes", "是")


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


# ── 大批量勾选（确认 3/3）────────────────────────────────────────────────

BATCH_THRESHOLD = 15  # 候选超过此数：交互弹会话勾选；非交互需 --confirm-batch

NONINTERACTIVE_BATCH_HELP = (
    "⚠ 本次候选 {n} 条，超过批量阈值 {t}——非交互环境不会一股脑写入，三选一：\n"
    "  · 缩小范围：--session <id子串> / --limit <每源N条> / --scope 7d 等有界窗口\n"
    "  · 显式放行全量（人已拍板）：追加 --confirm-batch\n"
    "  · 在交互终端跑同一条命令：会弹会话清单逐条勾选"
)


def parse_pick_answer(ans: str, n: int) -> list[int] | None:
    """'all'/空 → None（全选）；'1,3-5,8' → 0 基索引列表；'q' 取消。"""
    s = (ans or "").strip().lower()
    if s in _QUIT:
        raise SystemExit("已取消：未做任何修改。")
    if not s or s in ("all", "a", "全部"):
        return None
    out: list[int] = []
    for tok in re.split(r"[,，\s]+", s):
        if not tok:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", tok)
        if not m:
            raise SystemExit(f"无法识别的选择：{tok}（可用：编号 / 范围 3-5 / all / q）")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a or b > n:
            raise SystemExit(f"选择越界：{tok}（共 {n} 条，范围 1-{n}）")
        out.extend(range(a - 1, b))
    return sorted(set(out))


def prompt_session_pick(labels: list[str]) -> list[int] | None:
    """确认 3/3 · 大批量会话勾选。返回选中的 0 基索引；None=全部。"""
    print(f"── 确认 3/3 · 本次实际同步哪些（共 {len(labels)} 条）" + "─" * 8)
    MAX_SHOW = 50
    for i, lab in enumerate(labels[:MAX_SHOW], start=1):
        print(f"  {i:>3}) {lab}")
    if len(labels) > MAX_SHOW:
        print(f"  …（其余 {len(labels) - MAX_SHOW} 条未显示；回车=all，或用编号/范围选择）")
    print("  回车=全部同步 | 编号如 1,3-5,8 | q 取消")
    try:
        ans = input("选择 [all]: ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("已取消：未做任何修改。")
    return parse_pick_answer(ans, len(labels))


def batch_gate(candidates: list, gated: bool, confirm_batch: bool, label_of) -> list | None:
    """大批量闸门：候选超阈值时——交互弹逐条勾选（返回保留子集）；非交互要求
    --confirm-batch（token 即确认）否则拒绝。未超阈值 / 未启用（dry-run）→ None=全保留。
    """
    if len(candidates) <= BATCH_THRESHOLD or not gated:
        return None
    if confirm_batch:
        print(f"⚠ 大批量：{len(candidates)} 条已由 --confirm-batch 显式确认")
        return None
    if interactive():
        picked = prompt_session_pick([label_of(c) for c in candidates])
        if picked is None:
            return None
        print(f"已选 {len(picked)} / {len(candidates)} 条")
        return [candidates[i] for i in picked]
    raise SystemExit(NONINTERACTIVE_BATCH_HELP.format(n=len(candidates), t=BATCH_THRESHOLD))
