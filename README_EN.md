# agent-session-sync

Unify session histories from **21 agents on the dashboard** (19 with real readers — workbuddy ships CN & intl as two cards, 2 placeholders grok/copilot), continue any conversation in dsh, export to Markdown. Idempotent, incremental, ring-safe — imported sessions are marked and never flow back.

**Read (21 sources)**: zcode · hermes · dsh (DeepSeek Harness) · codex · workbuddy · workbuddy-ai (WorkBuddy AI intl) · Kilo CLI · Claude Code · opencode · qoder · OpenClaw · Cursor · Trae · MiniMax Code · Pi Agent · Gemini CLI · Cline · grok · mimo · kimi · copilot
**Write (12 targets)**: dsh · codex · Claude Code · hermes · opencode · Kilo CLI · workbuddy · workbuddy-ai · MiniMax Code · Pi Agent · Gemini CLI · Cline

## Install

Windows (PowerShell) — just tell any agent this sentence, or run it yourself:

```text
帮我安装 agent-session-sync：irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
```

Linux / macOS / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.sh | bash
```

The installer drops the toolkit at `~/.agents/skills/session-sync`, registers global commands (`session-sync`, alias `ass`), and bridges the skill into each detected agent's own skills dir — single source, upgrade once.

Requirements: Python 3.10+ · `pip install zstandard` (only third-party dep). No Python on Windows? An embedded CPython (~12 MB, no admin) is auto-downloaded.

## Quick commands

```bash
ass web        # dashboard at 127.0.0.1:8321: 19-card icon carousel, timeline, session list, export, backup, path binding
ass doctor     # one-click health check + self-repair (deps, selftest, stores, baselines, skill bridges, shims)
ass selftest   # sandboxed end-to-end test (never touches real data)
ass status     # detect which agent stores exist on this machine
```

Or just tell any agent: `用 session-sync skill 同步会话到 dsh，按它的纪律跑完闭环`

## Docs (Chinese, canonical)

| File | Content |
|---|---|
| `AGENTS.md` | Operator manual for AI agents: cookbook, safety rules, troubleshooting |
| `README.md` | Full README (Chinese, most detailed) |
| `docs/FORMATS.md` | Format overview, normalized IR, C-store, web endpoints |
| `docs/agents/*.md` | Deep storage specs — one file per agent |
| `docs/pitfalls.md` | Field-tested pitfalls (all fixed in code) |
