"""dsh 会话事件流校验（对齐 dsh 宿主 dsh-session-persistence 的两条硬纪律）。

1. assertEventsSupported：事件类型必须在宿主 KNOWN_SESSION_EVENT_TYPES 内，
   否则必须带顶层 ignorable === true（session/imported 即靠此放行）。
2. 结构纪律：seq 连续无重复；surface 事件带 surfaceOp；tool/result 的
   sourceEventSeqs 指向 tool/call。
"""
from __future__ import annotations

SURFACE_EVENT_TYPES = {"user/message", "assistant/message", "tool/result"}

# 逐字对齐宿主 @deepseek-ai/dsh-session 的 KNOWN_SESSION_EVENT_TYPES（0.1.1-rc）。
KNOWN_SESSION_EVENT_TYPES = {
    "agent-preset/selected",
    "agent/inbox/spliced",
    "approval/asked",
    "approval/decided",
    "approval/policy",
    "assistant/chunk",
    "assistant/message",
    "command/done",
    "command/run",
    "compaction/end",
    "compaction/prune",
    "compaction/start",
    "compaction/summary",
    "feedback/record",
    "goal/change",
    "hook/invoked",
    "hook/result",
    "llm/retry",
    "llm/retry-started",
    "permission/preset",
    "plan/mode",
    "request/context",
    "request/header",
    "sandbox/mode",
    "schedule/change",
    "session/end-seed",
    "session/title",
    "session/title-llm-request",
    "step/end",
    "step/start",
    "subagent/descriptor",
    "team/member",
    "team/message/delivered",
    "team/message/queued",
    "team/task",
    "todo/write",
    "tool-workflow/agent-end",
    "tool-workflow/agent-start",
    "tool-workflow/run-end",
    "tool-workflow/run-start",
    "tool/call",
    "tool/code-dispatch",
    "tool/code-dispatch-start",
    "tool/result",
    "turn/end",
    "turn/start",
    "user/message",
    "web/deepseek-search-llm-request",
}


def validate_session_events(events: list[dict]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    by_seq: dict[int, dict] = {}
    for ev in events:
        if not isinstance(ev, dict):
            problems.append("事件不是对象")
            continue
        seq = ev.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            problems.append(f"事件缺整数 seq：{ev.get('type')}")
            continue
        if seq in by_seq:
            problems.append(f"seq 重复：{seq}")
        by_seq[seq] = ev
    sorted_seqs = sorted(by_seq)
    for i in range(1, len(sorted_seqs)):
        if sorted_seqs[i] != sorted_seqs[i - 1] + 1:
            problems.append(f"seq 不连续：{sorted_seqs[i - 1]} → {sorted_seqs[i]}")
    for seq in sorted_seqs:
        ev = by_seq[seq]
        t = ev.get("type")
        if t not in KNOWN_SESSION_EVENT_TYPES and ev.get("ignorable") is not True:
            problems.append(f"事件类型 {t} 不在宿主已知表内且未标 ignorable（seq={seq}）——dsh 将拒绝整条日志")
        if t in SURFACE_EVENT_TYPES and "surfaceOp" not in ev:
            problems.append(f"surface 事件缺 surfaceOp（seq={seq}, {t}）")
        if t == "tool/result" and isinstance(ev.get("sourceEventSeqs"), list):
            for ref in ev["sourceEventSeqs"]:
                target = by_seq.get(ref)
                if target is not None and target.get("type") != "tool/call":
                    problems.append(f"sourceEventSeqs 指向非 tool/call（seq={seq} → {ref}）")
    return (len(problems) == 0), problems
