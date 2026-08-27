"""归一化会话模型（canonical turns IR）+ token 预算三层裁剪。

IR 与 dsh-chat-import 的 convert/core.mjs 对齐：
  Session{ source, source_id, title, cwd, created_at(ms), model, system_prompt,
           summary, turns }
  Turn{ prompt, steps }
  Step{ content: [block], tool_calls, tool_results, model }
block = {"type": "text"|"reasoning", "text": str}
        | {"type": "tool-call", "id", "name", "arguments"(JSON 字符串)}
ToolResult{ tool_call_id, content: [block], is_error }
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── token 估算（CJK 1/字，ASCII 1/4字符）────────────────────────────────


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x3400 <= cp <= 0x4DBF
            or 0x4E00 <= cp <= 0x9FFF
            or 0xF900 <= cp <= 0xFAFF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
            or 0x20000 <= cp <= 0x2A6DF
        ):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


def _estimate_blocks(blocks: list[dict]) -> int:
    total = 0
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") in ("text", "reasoning"):
            total += estimate_tokens(b.get("text") or "")
        elif b.get("type") == "tool-call":
            total += estimate_tokens(b.get("arguments") or "")
        elif b.get("type") == "tool-result" and isinstance(b.get("content"), list):
            total += _estimate_blocks(b["content"])
    return total


def estimate_turns(turns: list["Turn"]) -> int:
    total = 0
    for t in turns or []:
        total += estimate_tokens(t.prompt)
        for s in t.steps:
            total += _estimate_blocks(s.content)
            for tr in s.tool_results:
                total += _estimate_blocks(tr.content)
    return total


# ── IR 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    tool_call_id: str
    content: list[dict] = field(default_factory=list)
    is_error: bool = False


@dataclass
class Step:
    content: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)       # {id,name,arguments}
    tool_results: list[ToolResult] = field(default_factory=list)
    model: str | None = None


@dataclass
class Turn:
    prompt: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class Session:
    source: str                    # 'zcode' | 'hermes' | 'dsh' | 'codex'
    source_id: str
    title: str = ""
    cwd: str | None = None
    created_at: int = 0            # 毫秒
    model: str | None = None
    system_prompt: str | None = None
    summary: str | None = None     # 源侧压缩摘要（zcode compaction）
    turns: list[Turn] = field(default_factory=list)
    source_path: str | None = None

    @property
    def message_count(self) -> int:
        n = 0
        for t in self.turns:
            n += 1  # user prompt
            n += len(t.steps)
            n += sum(len(s.tool_results) for s in t.steps)
        return n

    @property
    def tool_call_count(self) -> int:
        return sum(len(s.tool_calls) for t in self.turns for s in t.steps)


# ── 三层预算裁剪（L1 单条裁剪 / L2 锚点+尾部截断 / L3 单条兜底）──────────

TEXT_BLOCK_CHAR_LIMIT = 16000
TOOL_RESULT_CHAR_LIMIT = 40000
_CROP_MARKER = "\n…（已裁剪）…\n"


def _crop_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    room = max(1, limit - len(_CROP_MARKER))
    head = room * 3 // 4
    tail = room - head
    return text[:head] + _CROP_MARKER + text[-tail:]


def _crop_blocks(blocks: list[dict], text_limit: int, tool_limit: int) -> tuple[list[dict], int]:
    out, cropped = [], 0
    for b in blocks or []:
        if not isinstance(b, dict):
            out.append(b)
            continue
        if b.get("type") in ("text", "reasoning") and isinstance(b.get("text"), str):
            t = _crop_text(b["text"], text_limit)
            if t != b["text"]:
                cropped += 1
                out.append({**b, "text": t})
            else:
                out.append(b)
        elif b.get("type") == "tool-result" and isinstance(b.get("content"), list):
            inner, n = _crop_blocks(b["content"], tool_limit, tool_limit)
            cropped += n
            out.append({**b, "content": inner})
        else:
            out.append(b)
    return out, cropped


def trim_turns(turns: list[Turn], budget: int, anchor_turns: int = 3) -> tuple[list[Turn], dict]:
    """返回 (裁剪后 turns, trimmed 统计)。预算内只做 L1 单条裁剪。"""
    trimmed = {
        "budget": budget,
        "originalTokens": estimate_turns(turns),
        "estimatedTokens": 0,
        "croppedBlocks": 0,
        "droppedTurns": 0,
        "droppedMessages": 0,
        "summaryInserted": False,
    }
    if not turns:
        return [], trimmed

    # L1：克隆 + 单条内容裁剪
    import copy
    l1: list[Turn] = []
    for t in turns:
        nt = Turn(prompt=t.prompt)
        for s in t.steps:
            cb, n1 = _crop_blocks(s.content, TEXT_BLOCK_CHAR_LIMIT, TOOL_RESULT_CHAR_LIMIT)
            results = []
            for tr in s.tool_results:
                rb, n2 = _crop_blocks(tr.content, TOOL_RESULT_CHAR_LIMIT, TOOL_RESULT_CHAR_LIMIT)
                trimmed["croppedBlocks"] += n2
                results.append(ToolResult(tr.tool_call_id, rb, tr.is_error))
            trimmed["croppedBlocks"] += n1
            nt.steps.append(Step(cb, copy.deepcopy(s.tool_calls), results, s.model))
        l1.append(nt)

    l1_est = estimate_turns(l1)
    if l1_est <= budget:
        trimmed["estimatedTokens"] = l1_est
        return l1, trimmed

    # L2：保留开头锚点 + 尾部贪心，中间丢弃并插入压缩摘要
    anchor = l1[: min(anchor_turns, len(l1))]
    rest = l1[len(anchor):]
    anchor_tokens = estimate_turns(anchor)
    summary_allowance = 512
    while len(anchor) > 1 and anchor_tokens + summary_allowance > budget:
        anchor = anchor[:-1]
        anchor_tokens = estimate_turns(anchor)
    tail: list[Turn] = []
    tail_tokens = 0
    for i in range(len(rest) - 1, -1, -1):
        add = estimate_turns([rest[i]])
        if anchor_tokens + summary_allowance + tail_tokens + add > budget:
            break
        tail.insert(0, rest[i])
        tail_tokens += add
    middle = l1[len(anchor): len(l1) - len(tail)] if tail else l1[len(anchor):]

    for t in middle:
        trimmed["droppedTurns"] += 1
        for s in t.steps:
            trimmed["droppedMessages"] += 1 + len(s.tool_results)
        trimmed["droppedMessages"] += 1

    kept = anchor + tail
    if trimmed["droppedTurns"] > 0 and kept:
        attach = tail[0] if tail else kept[-1]
        summary_text = (
            f"…[导入预算裁剪] 原对话约 {trimmed['originalTokens']} tokens，超出上下文预算 {budget} tokens。"
            f"为保持可续聊，已保留开头锚点与最近对话，裁剪中间 {trimmed['droppedTurns']} 轮"
            f"（{trimmed['droppedMessages']} 条消息）。完整历史见源文件。"
        )
        if attach.steps:
            attach.steps[0].content.insert(0, {"type": "reasoning", "text": summary_text})
        else:
            attach.steps.append(Step([{"type": "reasoning", "text": summary_text}]))
        trimmed["summaryInserted"] = True

    # L3：裁剪后单条仍超预算一半 → 丢弃（首轮 prompt 不丢）
    half = budget / 2
    kept2 = []
    for i, t in enumerate(kept):
        if i > 0 and estimate_tokens(t.prompt) > half:
            trimmed["droppedTurns"] += 1
            continue
        steps = []
        for s in t.steps:
            if _estimate_blocks(s.content) > half:
                trimmed["droppedMessages"] += 1
                continue
            results = [tr for tr in s.tool_results if _estimate_blocks(tr.content) <= half]
            steps.append(Step(s.content, s.tool_calls, results, s.model))
        kept2.append(Turn(t.prompt, steps))

    trimmed["estimatedTokens"] = estimate_turns(kept2)
    return kept2, trimmed


def apply_budget_trim(turns: list[Turn], budget: int | None) -> tuple[list[Turn], dict | None]:
    if not budget or budget <= 0:
        return turns, None
    out, trimmed = trim_turns(turns, budget)
    engaged = (
        trimmed["croppedBlocks"] > 0
        or trimmed["droppedTurns"] > 0
        or trimmed["droppedMessages"] > 0
        or trimmed["summaryInserted"]
    )
    return out, (trimmed if engaged else None)
