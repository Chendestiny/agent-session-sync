#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键收尾：退出 dsh 后运行一次，自动完成全部待办（无需再找 agent）。

用法（退出 dsh 后）：
    python sync-finish.py            # 全部收尾（先弹两道确认：来源区 + 数据量）
    python sync-finish.py --check    # 只看当前状态，不动任何东西（不弹确认）
    python sync-finish.py --sources zcode,workbuddy --scope 7d   # 参数即确认（非交互）

流程：
  0. 人在回路两道确认（来源区 / 数据量；显式参数即确认）→ 检查 dsh 已退出
     （未退出则每 10s 等待，可 Ctrl+C 取消后重来）
  1. prune   ：清理孤儿导入（源已删/被过滤）+ 打招呼冒烟会话 → 回收站+墓碑
  2. to-dsh  ：按确认的来源区+数据量增量导入（幂等；workbuddy 已排除 playground）
  3. attach  ：挂工作区分组 + 回填侧栏标题投影（含 identity 失配刷新）
  4. verify  ：事件纪律校验 + 汇总报告
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentsync import confirm, dshwrite, paths, readers


def _arg_value(flag: str) -> str | None:
    """从 sys.argv 取 `--flag value`；返回 None 表示未给。"""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def wait_dsh_exit(check_only: bool = False) -> bool:
    if not dshwrite.dsh_process_running():
        return True
    if check_only:
        return False
    print("dsh 正在运行，等待退出（每 10 秒检测，Ctrl+C 取消）…")
    print(">>> 现在可以完全退出 dsh（含托盘），脚本会自动继续 <<<")
    while dshwrite.dsh_process_running():
        time.sleep(10)
    print("dsh 已退出，等 3 秒让文件句柄释放…")
    time.sleep(3)
    return True


def main():
    check_only = "--check" in sys.argv
    p = paths.detect()
    if not p.dsh_sessions:
        print("未找到 dsh sessions 目录")
        return 1
    root = str(p.dsh_sessions)

    import sync as S
    from types import SimpleNamespace as NS

    # ── 人在回路两道确认（在等待 dsh 退出之前问完）─────────────────
    # --check 只读预览不弹确认（按全源全部历史展示待办全貌）
    if check_only:
        source_spec = _arg_value("--sources") or "zcode,hermes,codex,workbuddy"
        scope_spec = _arg_value("--scope") or "all"
        print("CHECK 模式（只读）：实跑时会先确认 来源区 + 数据量 两道再动手")
    else:
        gates = NS(source=_arg_value("--sources") or "", scope=_arg_value("--scope") or "")
        which = S._resolve_sources(gates)      # 确认 1/2（参数即确认/交互菜单）
        scope = S._resolve_scope(gates)        # 确认 2/2
        source_spec = ",".join(which)
        scope_spec = confirm.scope_spec(scope)

    # --check 全程只读，不需要 dsh 退出；实做模式才等待退出
    if not check_only and not wait_dsh_exit(False):
        return 1

    print("=" * 56)
    print("== [1/4] prune：清理孤儿与测试会话")
    print("=" * 56)
    loaded = S.load_sources(["zcode", "hermes", "codex", "workbuddy"], p)
    sources = {k: {s.source_id for s in v} for k, v in loaded.items() if v}
    plan = dshwrite.plan_prune(root, sources)
    for cat, label in (("orphans", "孤儿"), ("junk", "测试会话")):
        print(f"  {label}: {len(plan[cat])} 个")
    if plan["orphans"] or plan["junk"]:
        if check_only:
            for sid in plan["orphans"][:6]:
                d = plan["detail"][sid]
                print(f"    [孤儿] [{d['source']:9}] {d['title'][:30]}")
        else:
            res = dshwrite.apply_prune(root, plan, True, True)
            print(f"  -> {res}")

    print("=" * 56)
    print(f"== [2/4] to-dsh：导入（来源区={source_spec} 数据量={scope_spec}）")
    print("=" * 56)

    a = NS(source=source_spec, scope=scope_spec, apply=not check_only, root=root,
           budget=550000, force=False, titles=None, session=None, cwd=None, since=None, limit=None)
    S.cmd_to_dsh(a)

    print("=" * 56)
    print("== [3/4] attach：挂分组 + 标题投影回填")
    print("=" * 56)
    ap = dshwrite.plan_attach(root)
    total_attach = sum(len(v) for v in ap["attach"].values()) + sum(len(v) for v in ap["create"].values())
    tb = dshwrite.plan_title_backfill(root)
    print(f"  待挂载 {total_attach} | 待回填标题 {len(tb.get('backfill', {}))}")
    if not check_only and (total_attach or tb.get("backfill")):
        msg1 = dshwrite.apply_attach(ap)
        print(f"  -> {msg1}")
        msg2 = dshwrite.apply_title_backfill(tb)
        print(f"  -> {msg2}")

    print("=" * 56)
    print("== [4/4] verify：校验与汇总")
    print("=" * 56)

    S.cmd_verify(NS(root=root))

    print()
    print("=" * 56)
    if check_only:
        print("CHECK 模式完成（未做任何修改）——以上为当前待办全貌")
    else:
        print("全部收尾完成！现在可以启动 dsh 验收。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
