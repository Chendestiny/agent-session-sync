# agent-session-sync

Unify session histories from **15 AI coding agents**, continue any conversation in dsh, export to Markdown. Idempotent, incremental, ring-safe — imported sessions are marked and never flow back.

**Read (15 sources)**: zcode · hermes · dsh (DeepSeek Harness) · codex · workbuddy · Claude Code · opencode · qoder · OpenClaw · Cursor · Trae · MiniMax Code · Pi Agent · Gemini CLI · Cline
**Write (10 targets)**: dsh · codex · Claude Code · hermes · opencode · workbuddy · MiniMax Code · Pi Agent · Gemini CLI · Cline

## Install

Windows (PowerShell) — just tell any agent this sentence, or run it yourself:

```text
帮我安装 agent-session-sync：irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
```

Linux / macOS / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.sh | bash
```

The installer drops the toolkit at `~/.agents/skills/session-sync`, registers global commands (`session-sync` and the short alias `ass`), and bridges the skill into each detected agent's own skills dir (WorkBuddy / Claude Code / codex / hermes / dsh each keep their own — they don't read the common `~/.agents` location).

Requirements: Python 3.10+ · `pip install zstandard` (the only third-party dependency). **No Python at all?** On Windows the installer auto-downloads an embedded CPython to `~/.agents/py-runtime` (no admin, ~12 MB) and points everything at it.

## Quick commands

```bash
ass web        # dashboard at 127.0.0.1:8321: 15-source timeline, session list, per-turn bars, export, path binding
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
