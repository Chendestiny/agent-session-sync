#!/usr/bin/env python3
"""跨 Agent 会话同步 CLI：codex / hermes / dsh / zcode / workbuddy / claude / opencode → dsh + Markdown 归档。

zcode 只作为读取源（只出不进：向 zcode 写入会话已移除——双端同对话易混乱，
且活库写入验证成本高；历史实现保留在 agentsync/zcodewrite.py 供参考）。

用法：
  python sync.py status                          # 五源概览（含各源上次同步时间）
  python sync.py to-dsh   [--source ...] [--scope inc|7d|30d|Nd|all] [--apply] ...
      # 人在回路两道确认：交互终端未指定时弹菜单（来源区 → 数据量）；
      # 非交互（agent/脚本）必须显式给 --source/--scope（参数即确认），缺失拒绝执行
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


def _resolve_sources(args) -> list[str]:
    """确认 1/2 · 来源区：显式 --source 即确认；交互终端弹菜单；非交互缺参拒绝。"""
    raw = (getattr(args, "source", "") or "").strip()
    if raw:
        which = _parse_sources(raw, list(confirm.SYNC_SOURCES))
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


def cmd_to_dsh(args):
    p = paths.detect()
    if not p.dsh_sessions:
        sys.exit("未找到 dsh sessions 目录")
    which = _resolve_sources(args)
    scope = _resolve_scope(args)
    root = args.root or str(p.dsh_sessions)
    titles = {}
    if args.titles:
        titles = json.load(open(args.titles, encoding="utf-8"))
        print(f"标题覆盖：{len(titles)} 条（来自 {args.titles}）")
    loaded = load_sources(which, p)
    # 数据量确认 → 每源增量下界（None = 不过滤：全部历史 / 首次增量）
    state = syncstate.load(root)
    cutoffs: dict[str, int | None] = {}
    if scope["kind"] == "days":
        c = int(time.time() * 1000) - scope["days"] * 86400000
        cutoffs = {src: c for src in which}
        print(f"数据量过滤：{confirm.scope_label(scope)}（活跃时间 ≥ {datetime.fromtimestamp(c / 1000).strftime('%m-%d %H:%M')}）")
    elif scope["kind"] == "inc":
        for src in which:
            cutoffs[src] = syncstate.cutoff_for(state, src)
        first = [src for src in which if cutoffs[src] is None]
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
            plan = dshwrite.plan_write(root, sess, budget=args.budget, force=args.force, titles=titles)
            tag = plan["action"]
            extra = ""
            if tag == "append":
                extra = f"（源 {plan['sourceTurns']} 轮 > 已有 {plan['existingTurns']} 轮，追加 {len(plan['events'])} 事件）"
            elif tag == "create":
                extra = f"（{plan['stats']['messages']} 消息 / {plan['stats']['toolCalls']} 工具调用）"
            elif tag == "skip-deleted":
                extra = f"（{plan['reason']}）"
            print(f"  [{tag:13}] {src}:{sess.source_id} 「{(sess.title or sess.turns[0].prompt[:24] if sess.turns else '')[:32]}」 {extra}")
            if tag in ("create", "append"):
                planned += 1
                if args.apply:
                    msg = dshwrite.apply_write(plan)
                    print(f"      └─ applied: {msg} -> {plan['path']}")
                    applied += 1
    mode = "APPLY" if args.apply else "DRY-RUN（--apply 落盘）"
    print(f"\n{mode}：共 {total} 个候选，{planned} 个待写入，{applied} 个已写入（root={root}）")
    if args.apply:
        done = [src for src in which if src in loaded]
        syncstate.mark(root, done)
        print(f"增量基准已推进：{'/'.join(done)}（下次『仅增量』从这里起算）")


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

    print("== 1/5 dsh 写入与读回（沙箱根）==")
    dsh_root = os.path.join(box, "dsh-root")
    plan = dshwrite.plan_write(dsh_root, fake(), None)
    check(plan["action"] == "create", "plan 首次为 create")
    dshwrite.apply_write(plan)
    header, events = dshwrite.read_log_events(plan["path"])
    ok, problems = dshwrite.validate_session_events(events)
    check(ok and len(events) > 0, f"事件校验通过（{len(events)} 事件）")
    back = read_dsh(dsh_root)
    check(len(back) == 1 and len(back[0].turns) == 1 and back[0].title == "[codex] 自检会话", "读回 1 会话 1 轮带标题（自动来源前缀）")

    print("== 2/5 dsh 增量追加 ==")
    plan2 = dshwrite.plan_write(dsh_root, fake(v2=True), None)
    check(plan2["action"] == "append" and len(plan2["events"]) > 0, f"plan 二次为 append（+{len(plan2['events'])} 事件）")
    dshwrite.apply_write(plan2)
    _, events2 = dshwrite.read_log_events(plan2["path"])
    seqs = [e["seq"] for e in events2]
    ok2, _ = dshwrite.validate_session_events(events2)
    check(ok2 and seqs == list(range(len(seqs))), "追加后 seq 连续且校验通过")
    plan3 = dshwrite.plan_write(dsh_root, fake(v2=True), None)
    check(plan3["action"] == "up-to-date", "三次执行为 up-to-date（幂等）")

    print("== 3/5 归档渲染 ==")
    out = archive_mod.write_archive([fake(v2=True)], os.path.join(box, "archive"))
    check(len(out) == 1 and os.path.getsize(out[0]) > 0, f"Markdown 已生成（{os.path.basename(out[0]) if out else '-'}）")

    print("== 4/5 确认与增量（HITL 纯函数）==")
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

    print("== 5/5 claude / opencode 读取器（沙箱样本）==")
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

    s = sub.add_parser("to-dsh", help="导入到 dsh（可续聊）")
    s.add_argument("--source", default="", help="来源区（确认1/2）：all 或逗号组合 zcode,hermes,codex,workbuddy,claude,opencode；交互缺省时弹菜单")
    s.add_argument("--scope", default="", help="数据量（确认2/2）：inc(仅增量)|7d|30d|任意N天|all(全部历史)；交互缺省时弹菜单")
    s.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    s.add_argument("--root", default=None, help="覆盖 dsh sessions 根目录（测试用）")
    s.add_argument("--budget", type=int, default=None, help="上下文 token 预算（超限裁剪，默认不裁）")
    s.add_argument("--force", action="store_true", help="已存在的导入会话整体重写（修复损坏导入用）")
    s.add_argument("--titles", default=None, help="标题覆盖 JSON 文件：{源会话ID: 新标题}（配合 --force 重写生效）")
    _filter_args(s)
    s.set_defaults(fn=cmd_to_dsh)

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
