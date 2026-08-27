"""Markdown 归档：任意来源 IR → 可读转录（纯输出、无风险）。"""
from __future__ import annotations

import os
import re
from datetime import datetime

from .model import Session


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n]+", " ", text).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:limit] or "untitled"


def render_markdown(sess: Session) -> str:
    dt = datetime.fromtimestamp((sess.created_at or 0) / 1000).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {sess.title or sess.turns[0].prompt[:60] if sess.turns else '(无标题)'}",
        "",
        f"- 来源：`{sess.source}`  ·  源ID：`{sess.source_id}`",
        f"- 工作区：`{sess.cwd or '-'}`",
        f"- 时间：{dt}  ·  轮次：{len(sess.turns)}  ·  消息：{sess.message_count}  ·  工具调用：{sess.tool_call_count}",
        "",
        "---",
        "",
    ]
    if sess.summary:
        lines += ["> [源会话压缩摘要]", "", "```", sess.summary, "```", ""]
    for i, turn in enumerate(sess.turns, start=1):
        lines += [f"## Turn {i}", "", "**用户：**", "", turn.prompt, ""]
        for j, step in enumerate(turn.steps, start=1):
            result_by_call = {tr.tool_call_id: tr for tr in step.tool_results}
            for b in step.content:
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    lines += [f"**助手：**", "", b["text"], ""]
                elif bt == "reasoning" and b.get("text"):
                    lines += ["<details><summary>思考过程</summary>", "", "```", b["text"], "```", "", "</details>", ""]
                elif bt == "tool-call":
                    tr = result_by_call.get(b.get("id"))
                    out = ""
                    if tr:
                        out = "\n".join(x.get("text", "") for x in tr.content if isinstance(x, dict))
                    lines += [
                        "<details><summary>🔧 " + (b.get("name") or "tool") + "</summary>",
                        "",
                        "**参数：**",
                        "",
                        "```json",
                        b.get("arguments") or "{}",
                        "```",
                        "",
                        "**结果：**" + ("（失败）" if tr and tr.is_error else ""),
                        "",
                        "```",
                        out[:8000],
                        "```",
                        "",
                        "</details>",
                        "",
                    ]
    return "\n".join(lines)


def write_archive(sessions: list[Session], archive_dir: str) -> list[str]:
    os.makedirs(archive_dir, exist_ok=True)
    written = []
    index = ["# 会话归档索引", "", "| 来源 | 日期 | 标题 | 轮次 | 文件 |", "|---|---|---|---|---|"]
    for sess in sessions:
        dt = datetime.fromtimestamp((sess.created_at or 0) / 1000)
        display_title = sess.title or (sess.turns[0].prompt[:30] if sess.turns else "")
        name = f"{dt.strftime('%Y%m%d-%H%M')}-{_slug(display_title)}-{sess.source}-{sess.source_id[-8:].replace(':', '')}.md"
        sub = os.path.join(archive_dir, sess.source)
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_markdown(sess))
        written.append(path)
        index.append(
            f"| {sess.source} | {dt.strftime('%Y-%m-%d %H:%M')} | {display_title or '(无标题)'} | {len(sess.turns)} | [{os.path.basename(name)}]({sess.source}/{os.path.basename(name)}) |"
        )
    with open(os.path.join(archive_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index) + "\n")
    return written
