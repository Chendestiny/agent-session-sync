#!/usr/bin/env python3
"""跨 Agent 会话同步 CLI：codex / hermes / dsh / zcode / workbuddy / claude / opencode → dsh + Markdown 归档。

zcode 只作为读取源（只出不进：向 zcode 写入会话已移除——双端同对话易混乱，
且活库写入验证成本高；历史实现保留在 agentsync/zcodewrite.py 供参考）。

用法：
  python sync.py status                          # 五源概览（含各源上次同步时间）
  python sync.py to-dsh   [--source ...] [--scope inc|7d|30d|Nd|all] [--apply] ...
      # 人在回路两道确认：交互终端未指定时弹菜单（来源区 → 数据量）；
      # 非交互（agent/脚本）必须显式给 --source/--scope（参数即确认），缺失拒绝执行
  python sync.py pull  [--source ...] [--scope ...]        # 各源 → 规范库 ~/.session-sync（安全，免退出）
  python sync.py push --target codex [--source] [--scope] [--apply]   # 规范库 → 目标（断点续推）
  python sync.py attach-dsh [--apply]            # 挂工作区分组（需退出 dsh）
  python sync.py archive  [--source all] [--apply]
  python sync.py verify   [--root PATH]

所有写操作默认 dry-run，加 --apply 才落盘。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentsync import archive as archive_mod
from agentsync import confirm, dshwrite, paths, readers, syncstate

ALL_SOURCES = ["zcode", "hermes", "dsh", "codex", "workbuddy", "claude", "opencode"]


def _fmt_ts(ms: int) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def load_sources(which: list[str], p: paths.StorePaths):
    loaded: dict[str, list] = {}
    if "zcode" in which:
        if p.zcode_db:
            loaded["zcode"] = readers.read_zcode(p.zcode_db)
    if "hermes" in which:
        if p.hermes_db:
            loaded["hermes"] = readers.read_hermes(p.hermes_db)
    if "dsh" in which:
        if p.dsh_sessions:
            loaded["dsh"] = readers.read_dsh(p.dsh_sessions)
    if "codex" in which:
        if p.codex_sessions:
            loaded["codex"] = readers.read_codex(p.codex_sessions)
    if "workbuddy" in which:
        if p.workbuddy_home:
            loaded["workbuddy"] = readers.read_workbuddy(p.workbuddy_home)
    if "claude" in which:
        if p.claude_projects:
            loaded["claude"] = readers.read_claude(p.claude_projects)
    if "opencode" in which:
        if p.opencode_db:
            loaded["opencode"] = readers.read_opencode(p.opencode_db)
    return loaded


def _filter(sessions: list, args) -> list:
    out = sessions
    if args.session:
        wanted = {w.strip() for w in args.session.split(",") if w.strip()}
        out = [s for s in out if s.source_id in wanted or any(w in s.source_id for w in wanted)]
    if args.cwd:
        out = [s for s in out if s.cwd and args.cwd.lower() in s.cwd.lower()]
    if args.since:
        cutoff = (datetime.now().timestamp() - args.since * 86400) * 1000
        out = [s for s in out if s.created_at >= cutoff]
    if args.limit:
        out = out[: args.limit]
    return out


def _parse_sources(raw: str, defaults: list[str]) -> list[str]:
    raw = (raw or "").strip()
    if raw in ("all", "*", "全部"):
        return list(defaults)
    which = [s.strip() for s in raw.split(",") if s.strip()]
    for s in which:
        if s not in ALL_SOURCES:
            sys.exit(f"未知来源：{s}（可选 {ALL_SOURCES}）")
    return which or defaults


def _resolve_sources(args, include_dsh: bool = False) -> list[str]:
    """确认 1/2 · 来源区：显式 --source 即确认；交互终端弹菜单；非交互缺参拒绝。

    非 dsh 目标默认全集含 dsh 自身（dsh 会话反向流出）；dsh 目标不含 dsh。
    """
    defaults = list(confirm.SYNC_SOURCES) + (["dsh"] if include_dsh else [])
    raw = (getattr(args, "source", "") or "").strip()
    if raw:
        which = _parse_sources(raw, defaults)
        print(f"✔ 确认 1/2 来源区：{' + '.join(which)}（--source）")
        return which
    if confirm.interactive():
        which = confirm.prompt_sources()
        print(f"✔ 确认 1/2 来源区：{' + '.join(which)}")
        return which
    sys.exit(confirm.NONINTERACTIVE_HELP)


def _resolve_scope(args) -> dict:
    """确认 2/2 · 数据量：显式 --scope 即确认；交互终端弹菜单；非交互缺参拒绝。"""
    raw = (getattr(args, "scope", "") or "").strip()
    if raw:
        scope = confirm.parse_scope(raw)
        print(f"✔ 确认 2/2 数据量：{confirm.scope_label(scope)}（--scope {confirm.scope_spec(scope)}）")
        return scope
    if confirm.interactive():
        scope = confirm.prompt_scope()
        print(f"✔ 确认 2/2 数据量：{confirm.scope_label(scope)}")
        return scope
    sys.exit(confirm.NONINTERACTIVE_HELP)


def cmd_status(args):
    p = paths.detect()
    print("== 存储探测 ==")
    print(f"  zcode  db      : {p.zcode_db or '未找到'}")
    print(f"  hermes db      : {p.hermes_db or '未找到'}")
    print(f"  dsh sessions   : {p.dsh_sessions or '未找到'}")
    print(f"  codex sessions : {p.codex_sessions or '未找到'}")
    print(f"  workbuddy home : {p.workbuddy_home or '未找到'}")
    print(f"  claude projects: {p.claude_projects or '未找到'}")
    print(f"  opencode db    : {p.opencode_db or '未找到'}")
    if args.verbose:
        return
    loaded = load_sources(ALL_SOURCES, p)
    print()
    print("== 会话统计 ==")
    for src in ALL_SOURCES:
        ss = loaded.get(src, [])
        if not ss:
            print(f"  {src:7}: 0")
            continue
        newest = max(ss, key=lambda s: s.created_at or 0)
        label = (newest.title or (newest.turns[0].prompt[:30] if newest.turns else ""))[:30]
        print(f"  {src:7}: {len(ss):3} 个会话，最近：{_fmt_ts(newest.created_at)} 「{label}」")
    # 已导入到 dsh 的会话
    if p.dsh_sessions:
        imported = [s for s in loaded.get("dsh", []) if s.source_id.startswith("import-")]
        print(f"\n  dsh 中 import-* 会话：{len(imported)} 个（由同步工具/插件导入）")
        st = syncstate.load(p.dsh_sessions)
        if st:
            parts = [f"{k} {_fmt_ts(v)}" for k, v in sorted(st.items())]
            print("  上次同步（增量基准）：" + " · ".join(parts))
    # 规范库 C 总览（存在才显示）
    from agentsync import store

    if store.store_exists():
        ov = store.overview()
        counts = " · ".join(f"{k} {v}" for k, v in sorted(ov["counts"].items())) or "（空）"
        print(f"\n== 规范库 C：{ov['dir']} ==")
        print(f"  会话：{counts}")
        if ov["state"]:
            parts = [f"{k} {_fmt_ts(v)}" for k, v in sorted(ov["state"].items())]
            print("  pull 基准：" + " · ".join(parts))
        for tgt, stt in sorted(ov["push"].items()):
            parts = [f"{k} {_fmt_ts(v)}" for k, v in sorted(stt.items())]
            print(f"  push 水位[{tgt}]：" + " · ".join(parts))


def _compute_cutoffs(scope: dict, which: list[str], state: dict) -> tuple[dict[str, int | None], list[str]]:
    """数据量确认 → 每源增量下界；返回 (cutoffs, 首次无基准源列表)。"""
    cutoffs: dict[str, int | None] = {}
    if scope["kind"] == "days":
        c = int(time.time() * 1000) - scope["days"] * 86400000
        cutoffs = {src: c for src in which}
        print(f"数据量过滤：{confirm.scope_label(scope)}（活跃时间 ≥ {datetime.fromtimestamp(c / 1000).strftime('%m-%d %H:%M')}）")
    elif scope["kind"] == "inc":
        for src in which:
            cutoffs[src] = syncstate.cutoff_for(state, src)
    first = [src for src in which if cutoffs.get(src) is None] if scope["kind"] == "inc" else []
    return cutoffs, first


def _run_sink(args, name: str, get_store, writer, loader=None, state_dir=None, state_file=None, include_dsh=None):
    """通用写入目标执行器：两道确认 → 历史拦截 → 增量过滤 → plan/apply → 推进基准。

    dsh/codex/claude 的 root 是目录；hermes 的 root 是 state.db 文件
    （增量基准与墓碑落其所在目录）。push 模式：loader 读规范库 C，
    state_dir/state_file 指向 C 内每目标水位文件（断点续推的根基）。
    """
    p = paths.detect()
    store = get_store(p)
    if not store:
        sys.exit(f"未找到 {name} 会话存储")
    if include_dsh is None:
        include_dsh = name != "dsh"
    which = _resolve_sources(args, include_dsh=include_dsh)
    scope = _resolve_scope(args)
    root = args.root or str(store)
    s_root = state_dir or (root if os.path.isdir(root) else os.path.dirname(root))
    fname = state_file or ".agentsync-state.json"
    titles = {}
    tfile = getattr(args, "titles", None)
    if tfile:
        titles = json.load(open(tfile, encoding="utf-8"))
        print(f"标题覆盖：{len(titles)} 条（来自 {tfile}）")
    loaded = (loader or load_sources)(which, p)
    # 数据量确认 → 每源增量下界（None = 不过滤：全部历史 / 首次增量）
    state = syncstate.load(s_root, fname)
    # 人工拦截：历史全量（scope=all，或 inc 首跑无基准）在 --apply 时必须显式确认——
    # 交互弹 y/N（默认 N=取消）；非交互必须由人显式给 --confirm-history，否则拒绝。
    full_sources = confirm.history_full_sources(scope, which, state, available=set(loaded))
    if full_sources and args.apply and not getattr(args, "confirm_history", False):
        if confirm.interactive():
            if not confirm.prompt_history_confirm("'/'".join(full_sources)):
                sys.exit("已取消：未做任何修改。")
        else:
            sys.exit(confirm.NONINTERACTIVE_HISTORY_HELP)
    elif full_sources and getattr(args, "confirm_history", False):
        print(f"⚠ 历史全量：{'+'.join(full_sources)} 已由 --confirm-history 显式确认")
    cutoffs, first = _compute_cutoffs(scope, which, state)
    if first:
        print(f"仅增量：{'/'.join(first)} 首次运行无基准，按全部历史处理")
    total = planned = applied = 0
    for src in which:
        sessions = _filter(loaded.get(src, []), args)
        c = cutoffs.get(src)
        if c is not None:
            sessions = syncstate.apply_cutoff(sessions, c)
        for sess in sessions:
            total += 1
            plan = writer.plan_write(root, sess, budget=args.budget, force=args.force, titles=titles)
            tag = plan["action"]
            n_units = len(plan.get("events") or plan.get("lines") or plan.get("rows") or [])
            extra = ""
            if tag == "append":
                extra = f"（源 {plan['sourceTurns']} 轮 > 已有 {plan['existingTurns']} 轮，追加 {n_units} 单元）"
            elif tag == "create":
                extra = f"（{plan['stats']['messages']} 消息 / {plan['stats']['toolCalls']} 工具调用）"
            elif tag == "skip-deleted":
                extra = f"（{plan['reason']}）"
            print(f"  [{tag:13}] {src}:{sess.source_id} 「{(sess.title or sess.turns[0].prompt[:24] if sess.turns else '')[:32]}」 {extra}")
            if tag in ("create", "append"):
                planned += 1
                if args.apply:
                    msg = writer.apply_write(plan)
                    print(f"      └─ applied: {msg}")
                    applied += 1
    mode = "APPLY" if args.apply else "DRY-RUN（--apply 落盘）"
    print(f"\n{mode}：共 {total} 个候选，{planned} 个待写入，{applied} 个已写入（target={name} root={root}）")
    if args.apply:
        done = [src for src in which if src in loaded]
        syncstate.mark(s_root, done, fname)
        print(f"增量基准已推进：{'/'.join(done)}（下次『仅增量』从这里起算）")


def cmd_pull(args):
    """pull：各源 → 规范库 C（~/.session-sync）。只读各 agent、只写 C——
    无需退出任何应用，历史全量也不拦截（C 是内部规范库，不触碰 agent 存储）。
    """
    from agentsync import store

    p = paths.detect()
    which = _resolve_sources(args, include_dsh=True)
    scope = _resolve_scope(args)
    c_dir = store.store_dir()
    loaded = load_sources(which, p)
    if "dsh" in loaded:
        loaded["dsh"] = store.native_only(loaded["dsh"])  # import-* 不回流（防环形复制）
    state = syncstate.load(c_dir)
    cutoffs, first = _compute_cutoffs(scope, which, state)
    if first:
        print(f"仅增量：{'/'.join(first)} 首次运行无基准，按全部历史处理")
    tomb = dshwrite.load_tombstones(c_dir)
    total = created = updated = ok = skipped = 0
    for src in which:
        sessions = _filter(loaded.get(src, []), args)
        c = cutoffs.get(src)
        if c is not None:
            sessions = syncstate.apply_cutoff(sessions, c)
        for sess in sessions:
            total += 1
            label = (sess.title or sess.turns[0].prompt[:24] if sess.turns else "")[:32]
            if sess.source_id in tomb:
                skipped += 1
                continue
            r = store.write_session(sess)
            if r == "create":
                created += 1
            elif r == "update":
                updated += 1
            else:
                ok += 1
            if r != "up-to-date":
                print(f"  [{r:13}] {src}:{sess.source_id} 「{label}」")
    print(f"\nPULL：共 {total} 个候选，新增 {created}，更新 {updated}，无变化 {ok}，墓碑拦 {skipped}（C={c_dir}）")
    syncstate.mark(c_dir, [s for s in which if s in loaded])
    print("pull 基准已推进（下次『仅增量』从这里起算）")


def cmd_push(args):
    """push：规范库 C → 目标 agent。幂等断点续推（C 内每目标水位文件）；
    写 agent 存储照旧两道确认 + 历史全量拦截；换任何 agent 重跑自动从断点继续。
    """
    from agentsync import claudewrite, codexwrite, hermeswrite, opencodewrite, store, workbuddywrite

    if not store.store_exists():
        sys.exit("规范库 C 为空：先跑 python sync.py pull")
    writers = {"dsh": dshwrite, "codex": codexwrite, "claude": claudewrite, "hermes": hermeswrite,
               "opencode": opencodewrite, "workbuddy": workbuddywrite}
    getters = {
        "dsh": lambda p: p.dsh_sessions,
        "codex": lambda p: p.codex_sessions,
        "claude": lambda p: p.claude_projects,
        "hermes": lambda p: p.hermes_db,
        "opencode": lambda p: p.opencode_db,
        "workbuddy": lambda p: p.workbuddy_home,
    }
    target = args.target
    _run_sink(
        args, f"push:{target}", getters[target], writers[target],
        loader=lambda which, p: store.read_store(which),
        state_dir=store.store_dir(),
        state_file=f"push-{target}-state.json",
        include_dsh=(target != "dsh"),
    )


def cmd_to_dsh(args):
    from agentsync import dshwrite as _w

    _run_sink(args, "dsh", lambda p: p.dsh_sessions, _w)


def cmd_to_codex(args):
    from agentsync import codexwrite as _w

    _run_sink(args, "codex", lambda p: p.codex_sessions, _w)


def cmd_to_claude(args):
    from agentsync import claudewrite as _w

    _run_sink(args, "claude", lambda p: p.claude_projects, _w)


def cmd_to_hermes(args):
    from agentsync import hermeswrite as _w

    _run_sink(args, "hermes", lambda p: p.hermes_db, _w)


def cmd_to_opencode(args):
    from agentsync import opencodewrite as _w

    _run_sink(args, "opencode", lambda p: p.opencode_db, _w)


def cmd_to_workbuddy(args):
    from agentsync import workbuddywrite as _w

    _run_sink(args, "workbuddy", lambda p: p.workbuddy_home, _w)


def cmd_archive(args):
    p = paths.detect()
    which = _parse_sources(args.source, ALL_SOURCES)
    loaded = load_sources(which, p)
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive")
    n = 0
    for src in which:
        sessions = _filter(loaded.get(src, []), args)
        n += len(sessions)
        if args.apply:
            written = archive_mod.write_archive(sessions, out_dir)
            print(f"  {src:7}: {len(written)} 篇已写入 {out_dir}{os.sep}{src}")
        else:
            print(f"  {src:7}: {len(sessions)} 个会话待归档（--apply 落盘 → {out_dir}）")
    if not args.apply:
        print(f"\nDRY-RUN：共 {n} 个会话，加 --apply 写出 Markdown")


def cmd_verify(args):
    p = paths.detect()
    root = args.root or str(p.dsh_sessions)
    from agentsync.readers import _zstd_decode_all, _parse_jsonl

    import glob
    n = ok_n = 0
    for path in sorted(glob.glob(os.path.join(root, "*", "import-*", "session.jsonl*"))):
        n += 1
        raw = open(path, "rb").read()
        text = _zstd_decode_all(raw).decode("utf-8", errors="replace") if path.endswith(".zstd") else raw.decode("utf-8", errors="replace")
        lines = _parse_jsonl(text)
        events = [o for o in lines if o.get("type") != "session"]
        ok, problems = dshwrite.validate_session_events(events)
        ok_n += ok
        status = "OK " if ok else "BAD"
        print(f"  [{status}] {os.path.basename(os.path.dirname(path))}: {len(events)} events")
        for pr in problems[:3]:
            print(f"         - {pr}")
    print(f"\n校验 {n} 个导入会话：{ok_n} 通过")


def cmd_selftest(args):
    """沙箱端到端自检：不动任何真实数据，全部在 .selftest/ 里完成。

    覆盖：dsh 写入→读回→增量追加→校验；归档渲染。
    （zcode 写入方向已移除——只出不进；zcode 读取在 status/归档路径中隐式覆盖。）
    任何一步失败即退出码 1。换机器/升级 dsh 后先跑这个。
    """
    import shutil
    import tempfile

    from agentsync.model import Session, Step, ToolResult, Turn
    from agentsync.readers import read_dsh, read_zcode

    here = os.path.dirname(os.path.abspath(__file__))
    box = os.path.join(here, ".selftest")
    shutil.rmtree(box, ignore_errors=True)
    os.makedirs(box)
    failures = []

    def check(name: bool | tuple[bool, str], label: str):
        ok, detail = (name, "") if isinstance(name, bool) else name
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(label)

    def fake(v2=False) -> Session:
        turns = [
            Turn("第一问：你好", [Step([{"type": "reasoning", "text": "想想"}, {"type": "text", "text": "第一答"}])]),
        ]
        if v2:
            turns.append(
                Turn(
                    "第二问：跑个命令",
                    [
                        Step(
                            [{"type": "text", "text": "我来执行"}, {"type": "tool-call", "id": "call_t1", "name": "Bash", "arguments": '{"command": "echo hi"}'}],
                            [{"id": "call_t1", "name": "Bash", "arguments": '{"command": "echo hi"}'}],
                            [ToolResult("call_t1", [{"type": "text", "text": "hi"}], is_error=False)],
                        ),
                        Step([{"type": "text", "text": "命令输出 hi"}]),
                    ],
                )
            )
        return Session(
            source="codex",
            source_id="selftest-0001",
            title="自检会话",
            cwd="D:\\SelfTest",
            created_at=1787000000000,
            model="test-model",
            turns=turns,
        )

    print("== 1/8 dsh 写入与读回（沙箱根）==")
    dsh_root = os.path.join(box, "dsh-root")
    plan = dshwrite.plan_write(dsh_root, fake(), None)
    check(plan["action"] == "create", "plan 首次为 create")
    dshwrite.apply_write(plan)
    header, events = dshwrite.read_log_events(plan["path"])
    ok, problems = dshwrite.validate_session_events(events)
    check(ok and len(events) > 0, f"事件校验通过（{len(events)} 事件）")
    back = read_dsh(dsh_root)
    check(len(back) == 1 and len(back[0].turns) == 1 and back[0].title == "[codex] 自检会话", "读回 1 会话 1 轮带标题（自动来源前缀）")

    print("== 2/8 dsh 增量追加 ==")
    plan2 = dshwrite.plan_write(dsh_root, fake(v2=True), None)
    check(plan2["action"] == "append" and len(plan2["events"]) > 0, f"plan 二次为 append（+{len(plan2['events'])} 事件）")
    dshwrite.apply_write(plan2)
    _, events2 = dshwrite.read_log_events(plan2["path"])
    seqs = [e["seq"] for e in events2]
    ok2, _ = dshwrite.validate_session_events(events2)
    check(ok2 and seqs == list(range(len(seqs))), "追加后 seq 连续且校验通过")
    plan3 = dshwrite.plan_write(dsh_root, fake(v2=True), None)
    check(plan3["action"] == "up-to-date", "三次执行为 up-to-date（幂等）")

    # 标题投影回填（projcache title 行 + identity 校验）
    pc_dir = os.path.join(box, "storages")
    os.makedirs(pc_dir, exist_ok=True)
    pc_path = os.path.join(pc_dir, "session_projcache.json")
    with open(pc_path, "w", encoding="utf-8") as f:
        json.dump({"ver": 1, "tables": {"sessions": {}}}, f)
    tb = dshwrite.plan_title_backfill(dsh_root)
    check(len(tb["backfill"]) == 1, "title 回填计划覆盖新导入")
    dshwrite.apply_title_backfill(tb)
    sid_key = next(iter(tb["backfill"]))
    pc1 = json.load(open(pc_path, encoding="utf-8"))
    check(pc1["tables"]["sessions"][sid_key]["rows"]["title"]["val"] == "[codex] 自检会话", "回填 title 值带来源前缀")
    tb2 = dshwrite.plan_title_backfill(dsh_root)
    check(not tb2["backfill"], "二次回填为空（幂等）")
    # 回归：identity 失配但 title 一致 → 仍须重建条目（早期版本被 continue 跳过 → 侧栏无标题）
    pc1["tables"]["sessions"][sid_key]["identity"] = {"createdAt": 1, "cwd": "X:\\old"}
    with open(pc_path, "w", encoding="utf-8") as f:
        json.dump(pc1, f)
    tb3 = dshwrite.plan_title_backfill(dsh_root)
    check(sid_key in tb3["backfill"], "identity 失配且 title 一致时仍重建条目（回归）")

    print("== 3/8 归档渲染 ==")
    out = archive_mod.write_archive([fake(v2=True)], os.path.join(box, "archive"))
    check(len(out) == 1 and os.path.getsize(out[0]) > 0, f"Markdown 已生成（{os.path.basename(out[0]) if out else '-'}）")

    print("== 4/8 确认与增量（HITL 纯函数）==")
    from agentsync import confirm as cf
    from agentsync import syncstate

    check(cf.parse_scope("inc")["kind"] == "inc", "scope: inc")
    check(cf.parse_scope("all") == {"kind": "all", "days": None}, "scope: all")
    check(cf.parse_scope("14d") == {"kind": "days", "days": 14}, "scope: 14d")
    check(cf.parse_scope("14")["days"] == 14, "scope: 14（无 d 后缀）")
    try:
        cf.parse_scope("abc")
        check(False, "scope: 非法值应退出")
    except SystemExit:
        check(True, "scope: 非法值退出")
    check(cf.parse_sources_answer("2,5") == ["zcode", "workbuddy"], "来源组合 2,5 → zcode+workbuddy")
    check(cf.parse_sources_answer("") == cf.SYNC_SOURCES, "来源空输入 → 全部（默认同步源全集）")
    check(cf.parse_sources_answer("zcode,workbuddy") == ["zcode", "workbuddy"], "来源名称组合")

    base_ms = 1787000000000
    s_old = fake()
    s_old.updated_at = base_ms - 10 * 86400000
    s_new = fake()
    s_new.updated_at = base_ms - 1000
    kept = syncstate.apply_cutoff([s_old, s_new], base_ms - 7 * 86400000)
    check(len(kept) == 1 and kept[0] is s_new, "cutoff 过滤：只留 7 天内活跃")
    st_dir = os.path.join(box, "state-root")
    os.makedirs(st_dir, exist_ok=True)
    syncstate.mark(st_dir, ["zcode"])
    st = syncstate.load(st_dir)
    check("zcode" in st and st["zcode"] > 0, "状态文件 mark/load 往返")
    check(
        syncstate.cutoff_for(st, "zcode") is not None and syncstate.cutoff_for(st, "hermes") is None,
        "cutoff_for：有基准取基准-重叠，无基准 None（首次=全部）",
    )
    st_full = {"zcode": 1, "hermes": 1}
    check(cf.history_full_sources({"kind": "all", "days": None}, ["zcode", "claude"], {}) == ["zcode", "claude"],
          "历史拦截：scope=all 全部所选源命中")
    check(cf.history_full_sources({"kind": "inc", "days": None}, ["zcode", "claude"], st_full) == ["claude"],
          "历史拦截：inc 首跑无基准的源命中")
    check(cf.history_full_sources({"kind": "inc", "days": None}, ["zcode"], st_full) == [],
          "历史拦截：inc 有基准不命中")
    check(cf.history_full_sources({"kind": "days", "days": 7}, ["zcode"], {}) == [],
          "历史拦截：天数窗口（有界）不命中")

    print("== 5/8 claude / opencode 读取器（沙箱样本）==")
    import tempfile

    from agentsync.readers import read_claude, read_opencode

    claude_root = os.path.join(box, "claude", "projects")
    proj_dir = os.path.join(claude_root, "D--Proj")
    os.makedirs(proj_dir)
    c_sid = "11111111-2222-3333-4444-555555555555"
    c_recs = [
        {"type": "ai-title", "aiTitle": "配置 Codex 命令启动", "sessionId": c_sid},
        {"type": "user", "isSidechain": False, "cwd": "D:\\Proj", "timestamp": "2026-08-01T00:00:00.000Z",
         "message": {"role": "user", "content": "跑一下<system-reminder>别理我</system-reminder>测试"}},
        {"type": "assistant", "timestamp": "2026-08-01T00:00:01.000Z",
         "message": {"role": "assistant", "model": "claude-test", "content": [
             {"type": "thinking", "thinking": "想一想"},
             {"type": "tool_use", "id": "call_x1", "name": "Bash", "input": {"command": "echo hi"}},
         ]}},
        {"type": "user", "timestamp": "2026-08-01T00:00:02.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "call_x1", "content": "hi", "is_error": False}]}},
        {"type": "assistant", "timestamp": "2026-08-01T00:00:03.000Z",
         "message": {"role": "assistant", "model": "claude-test", "content": [{"type": "text", "text": "结果 hi"}]}},
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-08-01T00:00:04.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "子代理输出"}]}},
        {"type": "queue-operation", "operation": "dequeue", "timestamp": "2026-08-01T00:00:05.000Z"},
    ]
    with open(os.path.join(proj_dir, c_sid + ".jsonl"), "w", encoding="utf-8") as f:
        for r in c_recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    smoke_dir = os.path.join(claude_root, "T--tmp-smoke")
    os.makedirs(smoke_dir)
    with open(os.path.join(smoke_dir, "99999999-8888-7777-6666-555555555555.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "cwd": os.path.join(tempfile.gettempdir(), "x"),
                            "timestamp": "2026-08-01T00:00:00.000Z",
                            "message": {"role": "user", "content": "PONG"}}, ensure_ascii=False) + "\n")
    cl = read_claude(claude_root)
    check(len(cl) == 1 and cl[0].source_id == c_sid, "claude：读回 1 会话（TEMP 冒烟整文件跳过）")
    check(cl[0].title == "配置 Codex 命令启动" and cl[0].cwd == "D:\\Proj", "claude：ai-title + 行内 cwd")
    check(cl[0].turns[0].prompt == "跑一下测试", "claude：system-reminder 注入已剥")
    st_c = cl[0].turns[0].steps[0]
    check(any(b["type"] == "reasoning" for b in st_c.content) and st_c.tool_calls[0]["name"] == "Bash",
          "claude：thinking + tool_use")
    check(st_c.tool_results and st_c.tool_results[0].content[0]["text"] == "hi", "claude：tool_result 挂回原 step")
    check(len(cl[0].turns[0].steps) == 2 and cl[0].turns[0].steps[1].content[0]["text"] == "结果 hi",
          "claude：文本步在位，子代理/事件行被跳过")

    oc_db = os.path.join(box, "opencode.db")
    con = sqlite3.connect(oc_db)
    con.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT, model TEXT, time_created INTEGER, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
    )
    con.execute("INSERT INTO session VALUES ('ses_test0001', 'D:/oc', 'OC 测试', 'gpt-test', 1787000000000, 1787000009000)")
    con.execute("INSERT INTO message VALUES ('msg_u1', 'ses_test0001', 1787000001000, '{\"role\":\"user\"}')")
    con.execute("INSERT INTO part VALUES ('prt_u1', 'msg_u1', 'ses_test0001', 1787000001000, '{\"type\":\"text\",\"text\":\"你好 opencode\"}')")
    con.execute("INSERT INTO message VALUES ('msg_a1', 'ses_test0001', 1787000002000, '{\"role\":\"assistant\"}')")
    con.execute("INSERT INTO part VALUES ('prt_a1', 'msg_a1', 'ses_test0001', 1787000002000, '{\"type\":\"text\",\"text\":\"收到\"}')")
    con.execute("INSERT INTO message VALUES ('msg_s1', 'ses_test0001', 1787000003000, '{\"role\":\"compaction\"}')")
    con.commit()
    con.close()
    oc = read_opencode(oc_db)
    check(len(oc) == 1 and oc[0].title == "OC 测试", "opencode：读回 1 会话")
    check(oc[0].turns[0].prompt == "你好 opencode" and oc[0].turns[0].steps[0].content[0]["text"] == "收到",
          "opencode：user→assistant 轮（compaction 消息跳过）")
    check(oc[0].updated_at == 1787000009000 and oc[0].cwd == "D:/oc" and oc[0].model == "gpt-test",
          "opencode：updated_at / cwd / model")

    print("== 6/8 反向写入器（codex / claude / hermes 沙箱回路）==")
    from agentsync import claudewrite, codexwrite, hermeswrite
    from agentsync.readers import read_claude, read_codex, read_hermes

    cx_root = os.path.join(box, "codex-root")
    plan = codexwrite.plan_write(cx_root, fake(), None)
    check(plan["action"] == "create" and "codex-tui" in plan["meta_line"] and '"source": "cli"' in plan["meta_line"],
          "codex：计划 create 且 meta 含原生字段集")
    codexwrite.apply_write(codexwrite.plan_write(cx_root, fake(), None))
    back = read_codex(cx_root)
    check(len(back) == 1 and back[0].turns[0].prompt == "第一问：你好"
          and any(b.get("type") == "text" and b.get("text") == "第一答" for b in back[0].turns[0].steps[0].content),
          "codex：读回提问与回答")
    plan2 = codexwrite.plan_write(cx_root, fake(v2=True), None)
    check(plan2["action"] == "append" and bool(plan2["lines"]), f"codex：二次 append（+{len(plan2['lines'])} 行）")
    codexwrite.apply_write(plan2)
    back2 = read_codex(cx_root)
    check(len(back2[0].turns) == 2 and bool(back2[0].turns[1].steps[0].tool_calls), "codex：追加后含工具调用")
    check(codexwrite.plan_write(cx_root, fake(v2=True), None)["action"] == "up-to-date", "codex：三次 up-to-date")

    cl_root = os.path.join(box, "claude-root")
    check(claudewrite.plan_write(cl_root, fake(), None)["action"] == "create", "claude：计划 create")
    claudewrite.apply_write(claudewrite.plan_write(cl_root, fake(), None))
    back = read_claude(cl_root)
    check(len(back) == 1 and back[0].turns[0].prompt == "第一问：你好", "claude：读回提问")
    plan2 = claudewrite.plan_write(cl_root, fake(v2=True), None)
    claudewrite.apply_write(plan2)
    back2 = read_claude(cl_root)
    check(len(back2[0].turns) == 2 and bool(back2[0].turns[1].steps[0].tool_calls and back2[0].turns[1].steps[0].tool_results),
          "claude：工具调用+回传往返")
    check(claudewrite.plan_write(cl_root, fake(v2=True), None)["action"] == "up-to-date", "claude：三次 up-to-date")

    hm_db = os.path.join(box, "hermes", "state.db")
    os.makedirs(os.path.dirname(hm_db), exist_ok=True)
    con = sqlite3.connect(hm_db)
    con.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT DEFAULT 'cli', title TEXT, cwd TEXT, started_at REAL, ended_at REAL, model TEXT, archived INTEGER DEFAULT 0, "
        "message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0, api_call_count INTEGER DEFAULT 0);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, reasoning TEXT);"
    )
    con.commit()
    con.close()
    check(hermeswrite.plan_write(hm_db, fake(), None)["action"] == "create", "hermes：计划 create")
    hermeswrite.apply_write(hermeswrite.plan_write(hm_db, fake(), None))
    back = read_hermes(hm_db)
    check(len(back) == 1 and back[0].turns[0].prompt == "第一问：你好", "hermes：读回提问")
    con = sqlite3.connect(hm_db)
    mc = con.execute("SELECT message_count, tool_call_count, source FROM sessions").fetchone()
    con.close()
    check(mc[0] >= 2 and mc[2] == "cli", "hermes：计数列已填（列表 UI 的「N条消息」数据源）")
    hermeswrite.apply_write(hermeswrite.plan_write(hm_db, fake(v2=True), None))
    back2 = read_hermes(hm_db)
    check(len(back2[0].turns) == 2 and bool(back2[0].turns[1].steps[0].tool_calls), "hermes：追加后含工具调用")
    check(hermeswrite.plan_write(hm_db, fake(v2=True), None)["action"] == "up-to-date", "hermes：三次 up-to-date")

    print("== 7/8 规范库 C（存储层 + push 全链路）==")
    from agentsync import store as st_mod

    os.environ["SESSION_SYNC_HOME"] = os.path.join(box, "session-sync")
    try:
        check(st_mod.write_session(fake()) == "create", "C：首次写入 create")
        check(st_mod.write_session(fake()) == "up-to-date", "C：重复写入 up-to-date")
        st_mod.write_session(fake(v2=True))
        check(st_mod.write_session(fake()) == "up-to-date", "C：旧版本不回退（已有 2 轮 >= 源 1 轮）")
        back_c = st_mod.read_store(["codex"])
        check(set(back_c) == {"codex"} and len(back_c["codex"]) == 1, "C：按源读取")
        s_c = back_c["codex"][0]
        check(len(s_c.turns) == 2 and bool(s_c.turns[1].steps[0].tool_calls and s_c.turns[1].steps[0].tool_results),
              "C：IR 全保真（工具调用+回传往返）")
        cx_push = os.path.join(box, "codex-push")
        plan_p = codexwrite.plan_write(cx_push, s_c, None)
        check(plan_p["action"] == "create", "push：C→codex 计划 create")
        codexwrite.apply_write(plan_p)
        back_p = read_codex(cx_push)
        check(len(back_p) == 1 and len(back_p[0].turns) == 2 and bool(back_p[0].turns[1].steps[0].tool_calls),
              "push：C→codex→读回 全链路")
        fake_dsh = fake()
        fake_dsh.source = "dsh"
        fake_dsh.source_id = "import-abc"
        check(len(st_mod.native_only([fake_dsh])) == 0 and len(st_mod.native_only([fake_dsh, fake()])) == 1,
              "C：dsh import-* 过滤（防环形复制）")
    finally:
        del os.environ["SESSION_SYNC_HOME"]

    print("== 8/8 桌面版写入器（opencode / workbuddy 沙箱回路）==")
    from agentsync import opencodewrite, workbuddywrite
    from agentsync.readers import read_opencode, read_workbuddy

    ocw_db = os.path.join(box, "ocw", "opencode.db")
    os.makedirs(os.path.dirname(ocw_db), exist_ok=True)
    con = sqlite3.connect(ocw_db)
    con.executescript(
        "CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT, worktree TEXT);"
        "INSERT INTO project(id,name,worktree) VALUES('global',NULL,'/');"
        "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT, slug TEXT NOT NULL, "
        "directory TEXT NOT NULL, title TEXT NOT NULL, version TEXT NOT NULL, share_url TEXT, "
        "summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER, summary_diffs TEXT, "
        "revert TEXT, permission TEXT, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, "
        "time_compacting INTEGER, time_archived INTEGER, workspace_id TEXT, path TEXT, agent TEXT, model TEXT, "
        "cost REAL NOT NULL DEFAULT 0, tokens_input INTEGER NOT NULL DEFAULT 0, tokens_output INTEGER NOT NULL DEFAULT 0, "
        "tokens_reasoning INTEGER NOT NULL DEFAULT 0, tokens_cache_read INTEGER NOT NULL DEFAULT 0, "
        "tokens_cache_write INTEGER NOT NULL DEFAULT 0, metadata TEXT);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, data TEXT NOT NULL);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);"
        "CREATE TABLE project_directory (project_id TEXT NOT NULL, directory TEXT NOT NULL, type TEXT, "
        "strategy TEXT, time_created INTEGER);"
    )
    con.commit()
    con.close()
    p1 = opencodewrite.plan_write(ocw_db, fake(), None)
    check(p1["action"] == "create", "opencode：计划 create")
    opencodewrite.apply_write(p1)
    con = sqlite3.connect(ocw_db)
    oc_path_col = con.execute("SELECT path FROM session").fetchone()[0]
    con.close()
    check(oc_path_col == "SelfTest", "opencode：path 派生列已填（桌面列表可见性）")
    back = read_opencode(ocw_db)
    check(len(back) == 1 and back[0].turns[0].prompt == "第一问：你好", "opencode：读回提问")
    p2 = opencodewrite.plan_write(ocw_db, fake(v2=True), None)
    opencodewrite.apply_write(p2)
    back2 = read_opencode(ocw_db)
    check(len(back2[0].turns) == 2 and bool(back2[0].turns[1].steps[0].tool_calls and back2[0].turns[1].steps[0].tool_results),
          "opencode：工具调用+回传往返（input/output 同 part）")
    check(opencodewrite.plan_write(ocw_db, fake(v2=True), None)["action"] == "up-to-date", "opencode：三次 up-to-date")
    pf = opencodewrite.plan_write(ocw_db, fake(v2=True), None, force=True)
    opencodewrite.apply_write(pf)
    backf = read_opencode(ocw_db)
    check(len(backf[0].turns) == 2 and len(backf[0].turns) == 2, "opencode：force 重写不重复（清旧消息）")

    wb_home = os.path.join(box, "wb-home")
    os.makedirs(os.path.join(wb_home, "projects"), exist_ok=True)
    con = sqlite3.connect(os.path.join(wb_home, "workbuddy.db"))
    con.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, user_id TEXT NOT NULL, title TEXT, "
        "custom_title TEXT, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
        "deleted_at INTEGER, is_playground INTEGER NOT NULL, source_mode TEXT, is_background_automation INTEGER, "
        "mode TEXT, model TEXT, last_activity_at INTEGER);"
    )
    con.commit()
    con.close()
    w1 = workbuddywrite.plan_write(wb_home, fake(), None)
    check(w1["action"] == "create", "workbuddy：计划 create")
    workbuddywrite.apply_write(w1)
    back = read_workbuddy(wb_home)
    check(len(back) == 1 and back[0].turns[0].prompt == "第一问：你好", "workbuddy：读回提问")
    w2 = workbuddywrite.plan_write(wb_home, fake(v2=True), None)
    workbuddywrite.apply_write(w2)
    back2 = read_workbuddy(wb_home)
    check(len(back2[0].turns) == 2 and bool(back2[0].turns[1].steps[0].tool_calls), "workbuddy：追加后含工具调用")
    check(workbuddywrite.plan_write(wb_home, fake(v2=True), None)["action"] == "up-to-date", "workbuddy：三次 up-to-date")

    if args.keep:
        print(f"\n沙箱保留在：{box}")
    else:
        shutil.rmtree(box, ignore_errors=True)
    if failures:
        print(f"\nSELFTEST FAILED：{len(failures)} 项未过 -> {failures}")
        sys.exit(1)
    print("\nSELFTEST PASSED：工具链完好，可对真实数据执行（记得先 dry-run）。")


def main():
    ap = argparse.ArgumentParser(description="跨 Agent 会话同步（codex/hermes/dsh/zcode/workbuddy/claude/opencode 七源 → dsh，单向）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="四源存储概览")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    for sink, fn, root_help in (
        ("to-dsh", cmd_to_dsh, "覆盖 dsh sessions 根目录"),
        ("to-codex", cmd_to_codex, "覆盖 codex sessions 根目录"),
        ("to-claude", cmd_to_claude, "覆盖 claude projects 根目录"),
        ("to-hermes", cmd_to_hermes, "覆盖 hermes state.db 路径"),
        ("to-opencode", cmd_to_opencode, "覆盖 opencode opencode.db 路径"),
        ("to-workbuddy", cmd_to_workbuddy, "覆盖 workbuddy home 目录"),
    ):
        s = sub.add_parser(sink, help=f"导入到 {sink[3:]}（可续聊）")
        s.add_argument("--source", default="", help="来源区（确认1/2）：all 或逗号组合 zcode,hermes,codex,workbuddy,claude,opencode[,dsh]；交互缺省时弹菜单")
        s.add_argument("--scope", default="", help="数据量（确认2/2）：inc(仅增量,默认)|7d|30d|任意N天|all(全部历史,需二次确认)；交互缺省时弹菜单")
        s.add_argument("--confirm-history", action="store_true", help="历史全量的人工确认 token：--scope all 或 inc 首跑时，交互弹 y/N、非交互必给本参数")
        s.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
        s.add_argument("--root", default=None, help=f"{root_help}（测试用）")
        s.add_argument("--budget", type=int, default=None, help="上下文 token 预算（超限裁剪，默认不裁）")
        s.add_argument("--force", action="store_true", help="已存在的导入会话整体重写（修复损坏导入用）")
        if sink == "to-dsh":
            s.add_argument("--titles", default=None, help="标题覆盖 JSON 文件：{源会话ID: 新标题}（配合 --force 重写生效）")
        _filter_args(s)
        s.set_defaults(fn=fn)

    s = sub.add_parser("pull", help="各源 → 规范库 ~/.session-sync（只读源、只写 C，无需退出任何应用）")
    s.add_argument("--source", default="", help="来源区（确认1/2）：all 或逗号组合（pull 的 all 含 dsh 原生会话）")
    s.add_argument("--scope", default="", help="数据量（确认2/2）：inc|7d|30d|Nd|all（C 为内部库，全量不拦截）")
    _filter_args(s)
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("push", help="规范库 C → 目标 agent（幂等断点续推，中途换 agent 可继续）")
    s.add_argument("--target", required=True, choices=["dsh", "codex", "claude", "hermes", "opencode", "workbuddy"], help="推送目标")
    s.add_argument("--source", default="", help="来源区（确认1/2）：C 里哪些源推过去")
    s.add_argument("--scope", default="", help="数据量（确认2/2）：inc|7d|30d|Nd|all（写 agent 存储，全量需确认）")
    s.add_argument("--confirm-history", action="store_true", help="历史全量的人工确认 token：--scope all 或 inc 首跑时，交互弹 y/N、非交互必给本参数")
    s.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    s.add_argument("--root", default=None, help="覆盖目标存储路径（测试用）")
    s.add_argument("--budget", type=int, default=None, help="上下文 token 预算（超限裁剪，默认不裁）")
    s.add_argument("--force", action="store_true", help="已存在的导入会话整体重写（修复损坏导入用）")
    _filter_args(s)
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("archive", help="导出 Markdown 归档")
    s.add_argument("--source", default="all", help="逗号分隔或 all")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--out", default=None)
    _filter_args(s)
    s.set_defaults(fn=cmd_archive)

    s = sub.add_parser("verify", help="校验已导入 dsh 会话的事件纪律")
    s.add_argument("--root", default=None)
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("selftest", help="沙箱端到端自检（不碰真实数据；换机/升级后先跑这个）")
    s.add_argument("--keep", action="store_true", help="保留沙箱目录 .selftest/ 供检查")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("attach-dsh", help="把 import-* 会话挂载到 dsh 工作区分组（改 workspace.json；需先退出 dsh）")
    s.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    s.add_argument("--root", default=None, help="覆盖 dsh sessions 根目录（测试用）")
    s.set_defaults(fn=cmd_attach_dsh)

    s = sub.add_parser("prune", help="清理 dsh 中的孤儿导入与打招呼/测试会话（移入回收站，可恢复）")
    s.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    s.add_argument("--only", default=None, help="只清某类：orphans,junk 逗号分隔（默认两类都清）")
    s.add_argument("--root", default=None, help="覆盖 dsh sessions 根目录（测试用）")
    s.set_defaults(fn=cmd_prune)

    ns = ap.parse_args()
    ns.fn(ns)


def cmd_prune(args):
    p = paths.detect()
    if not p.dsh_sessions:
        sys.exit("未找到 dsh sessions 目录")
    root = args.root or str(p.dsh_sessions)
    loaded = load_sources(list(confirm.SYNC_SOURCES), p)
    sources = {k: {s.source_id for s in v} for k, v in loaded.items() if v}
    plan = dshwrite.plan_prune(root, sources)
    for cat, label in (("orphans", "孤儿（源会话已删除）"), ("junk", "打招呼/冒烟测试")):
        sids = plan[cat]
        print(f"== {label}：{len(sids)} 个 ==")
        for sid in sids[:6]:
            d = plan["detail"][sid]
            print(f"  [{d['source']:9}] 「{d['title'][:28]}」 {d['turns']}轮")
        if len(sids) > 6:
            print(f"  …（其余 {len(sids)-6} 个略）")
    if not args.apply:
        print()
        print("DRY-RUN（--apply 落盘：移入 ~/.trash-dsh 回收站 + 清引用，可恢复；--only orphans,junk 可只清一类）")
        return
    do_orphans = not args.only or "orphans" in args.only
    do_junk = not args.only or "junk" in args.only
    running = dshwrite.dsh_process_running()
    res = dshwrite.apply_prune(root, plan, do_orphans, do_junk, dsh_running=running)
    print()
    print(f"applied: moved {res.get('moved', 0)} sessions -> {res.get('trash', '~/.trash-dsh')} (manifest.jsonl 有明细)")


def cmd_attach_dsh(args):
    p = paths.detect()
    if not p.dsh_sessions:
        sys.exit("未找到 dsh sessions 目录")
    root = args.root or str(p.dsh_sessions)
    plan = dshwrite.plan_attach(root)
    if plan["action"] == "missing":
        sys.exit(plan["reason"])
    ws = plan["workspace"]
    records = ws["tables"]["workspaces"]
    print(f"workspace.json：{len(records)} 个工作区记录")
    total = sum(len(v) for v in plan["attach"].values()) + sum(len(v) for v in plan["create"].values())
    print(f"待挂载：{total} 个导入会话")
    for wid, sids in plan["attach"].items():
        rec = records[wid]
        print(f"  挂到已有  「{rec['title']}」({rec['path']})：{len(sids)} 个")
    for cwd, sids in plan["create"].items():
        print(f"  新建工作区 {cwd}：{len(sids)} 个")
    for reason, sids in plan["skip"].items():
        print(f"  [跳过] {reason}：{len(sids)} 个")
    tb = dshwrite.plan_title_backfill(root)
    n_tb = len(tb.get("backfill", {}))
    print(f"  标题预投影：{n_tb} 个导入会话待回填 title 行（侧栏列表标题的数据源）")
    if not args.apply:
        print("\nDRY-RUN（--apply 落盘；执行前必须完全退出 dsh，否则退出时会被覆盖）")
        return
    running = dshwrite.dsh_process_running()
    msg = dshwrite.apply_attach(plan, dsh_running=running)
    print(f"\napplied: {msg}")
    if n_tb:
        msg2 = dshwrite.apply_title_backfill(tb, dsh_running=running)
        print(f"applied: {msg2}")


def _filter_args(s):
    s.add_argument("--session", default=None, help="按源会话 ID 过滤（子串匹配，可逗号分隔多个）")
    s.add_argument("--cwd", default=None, help="按工作区路径子串过滤")
    s.add_argument("--since", type=float, default=None, help="只看最近 N 天")
    s.add_argument("--limit", type=int, default=None, help="每个来源最多处理 N 个")


if __name__ == "__main__":
    main()
