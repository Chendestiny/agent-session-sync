# 🔄 agent-session-sync

[简体中文](./README.md) | **English**

Cross-agent sessions across **codex / hermes / dsh (DeepSeek Harness) / zcode / workbuddy / claude code / opencode** —
any historical session can be imported into any of the others and **continued**, plus a unified
**Markdown archive**. One-way consolidation (A→C→B: 7 readers + 6 writers, not 7×6 direct pairs).

| A · Sources (7) | C · Normalization | B · Targets (6 + archive) |
|---|---|---|
| codex CLI · hermes · dsh · zcode *(read-only)* · workbuddy · claude code · opencode | IR (turns) + canonical store `~/.session-sync` (resumable pull/push) | dsh *(resumable, idempotent + incremental)* · codex · claude code · hermes · opencode · workbuddy + Markdown archive |

## 🚀 Quick Start

Requirements: Python 3.10+ with `zstandard`. The native-backend verification needs **Node 22+**
(`nvm use 22`; `tools/verify-dsh-backend.cmd` auto-picks the nvm 22.x).

```bash
cd "<project dir>"
pip install zstandard        # the only third-party Python dependency
nvm use 22                   # optional, for node-based verification

python sync.py selftest                        # sandbox self-test: must be green first
python sync.py status                          # seven-source overview
python sync.py to-dsh                          # interactive: two confirm menus (sources → scope), then dry-run
python sync.py to-dsh   --source zcode --scope 7d          # flags = the confirmation (zcode, last 7 days)
python sync.py to-dsh   --source all --scope inc --apply   # ① import into dsh (agents must pass both flags)
python sync.py to-codex --source zcode --scope inc --apply  # reverse write into codex (to-claude / to-hermes / to-opencode / to-workbuddy alike)
python sync.py pull --source all --scope inc                # A→C: sources → canonical store ~/.session-sync (safe, no app exits)
python sync.py push --target codex --apply --scope inc      # C→B: resumable push (any agent can continue mid-way)
# ② fully quit dsh, then: attach to workspace groups + backfill sidebar title cache
python sync.py attach-dsh --apply
# ③ start dsh: imported sessions appear under their workspace groups, resumable

python sync.py archive  --source all --apply   # Markdown archive → ./archive
python sync.py verify                          # validate imported sessions
python sync.py serve                           # read-only web dashboard: timeline / session list / per-turn times (auto-opens 127.0.0.1:8321)
python sync.py prune --pick --hard                           # interactive slim-down: list all dsh sessions (keyword filter), pick by number, delete outright (quit dsh first)
python sync.py prune --session "title-or-id-substring" --hard --apply   # delete named sessions outright (dry-run first; --older-than N; --native to also pick native ones)
tools\verify-dsh-backend.cmd                   # strong check via dsh's own backend (Node 22)
```

> **The full dsh loop = import (to-dsh) + group & titles (attach-dsh) + restart dsh.** Skipping step ②
> leaves sessions in "ungrouped" with no list titles until opened. Bulk-rename titles: edit `titles.json`,
> then `python sync.py to-dsh --source all --scope all --apply --force --confirm-history --titles titles.json --budget 550000`.
>
> Human-in-the-loop: every sync (to-dsh / sync-finish.py) asks two confirmations —
> ① source scope (all / zcode,workbuddy / ...) ② data scope (inc / 7d / 30d / N days / all).
> Interactive terminals get menus; non-interactive runs must pass `--source` + `--scope`
> explicitly (flags = the confirmation; missing flags abort). Full-history sync
> (`--scope all` or first-run inc) additionally requires an explicit y/N (interactive)
> or the human-granted `--confirm-history` flag (non-interactive).
>
> Agent-facing manual: **AGENTS.md** is the complete entry point (cookbook / safety rules /
> troubleshooting / upgrade adaptation) — hand it to any agent.

## 🖥️ Read-only Web Dashboard

**Option 1 · global command:**

```bash
session-sync serve              # auto-opens the browser at 127.0.0.1:8321 (--port to change, Ctrl+C to stop)
```

**Option 2 · from source** (no skill install / fresh clone):

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard           # the only third-party dep (for reading the dsh source)
python sync.py serve
```

| View | What you see |
|---|---|
| Overview | per-source health/count + C-store watermarks + 7-lane session timeline (position = created, width = span) |
| Sessions | filter by source/date/keyword, click a row to drill down |
| Detail | per-turn time bars (flattened timestamps instantly visible) + turns & tool-call details |

Fully read-only: zero write endpoints (POST → 405), binds 127.0.0.1 only, offline page (no CDN),
no new dependencies (stdlib HTTP server); fresh reads on every request, no cache.

## 💬 One Sentence to Any Agent (zero config)

Send this to any agent that can access the web and run commands (dsh / zcode / hermes / Claude):

```text
Install agent-session-sync for me: irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
```

The installer drops the toolkit into `~/.agents/skills/session-sync` and registers it as a skill.
Afterwards just say:

```text
sync my sessions
sync the hermes sessions of the demo project into dsh
archive my sessions
clean up orphans and test sessions in dsh
```

The agent follows the discipline in `SKILL.md`: selftest → dry-run → confirm → apply → **post-sync verification**.

**Post-sync verification** (paste this so the agent proves its work):

```text
Run the post-sync verification per SKILL.md:
1. python sync.py verify                     # all imported sessions pass event discipline
2. spot-check one imported log file for the three essentials:
   session/imported marker (ignorable=true), [source]-prefixed title,
   partition folder name matching the header cwd encoding
3. quit dsh, then python sync.py attach-dsh --apply to backfill groups + projcache titles;
   after restarting dsh confirm: correct workspace group, titled in the sidebar, resumable
```

Or simply clone:

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard && python sync.py selftest
```

## 🧩 Use as a Skill (the whole directory IS the skill bundle)

```bat
mklink /J "%USERPROFILE%\.agents\skills\session-sync" "<project dir>"
```

Then say “sync my sessions” inside zcode / dsh / hermes.

Filters: `--session <id substring>` · `--cwd <path substring>` · `--since <days>` · `--limit <N>`;
`to-dsh --budget <tokens>` trims oversized sessions while keeping them resumable.

## 🧪 Pre-release Verification

Validated against real data before publishing (methods documented, re-runnable on your machine):

- Seven-source reading: codex / hermes / dsh / zcode / workbuddy / claude / opencode all parse correctly
  (tool calls incl. args/results/failures, reasoning, image placeholders)
- dsh writing: 100% read-back pass via dsh's own JsonlSessionPersistence backend
  (`tools/verify-dsh-backend.cmd`)
- Workspace partition encodings: zero mismatches in full-corpus comparisons
- Idempotent + incremental; three-layer budget trimming keeps huge sessions resumable;
  zcode writer passed round-trip regression on a db copy

### ⚠️ Pitfalls

All measured pitfalls (dsh projection cache / codex threads index / hermes count columns /
opencode project context …) are fixed in code — full breakdown in
**[docs/pitfalls.md](docs/pitfalls.md)** (grouped per agent, with fixes).

## 📂 Layout

See the Chinese README's 目录结构 section, or explore the tree directly:
`AGENTS.md` (manual) · `SKILL.md` (skill bundle) · `sync.py` (CLI) · `agentsync/` (library, incl.
`webui/` read-only dashboard served by `sync.py serve`) ·
`docs/FORMATS.md` + `docs/agents/*.md` (deep format specs per agent) · `examples/` · `tools/`.

## 🔒 Safety Boundaries

- Reads are always read-only (sqlite `mode=ro` URI).
- Write targets (dsh / codex / claude code / hermes / opencode / workbuddy) only add idempotent
  imported sessions (`import-*` / uuid5 ids); native sessions are never touched, and every
  mutation is preceded by an automatic backup.
- zcode is not written for now (that direction was removed 2026-08-26; the writer is archived in `zcodewrite.py`).
- Restore: use the auto backups (`*.agentsync-bak-*`) created before every mutation.

---

Design references [Nwflower/dsh-chat-import](https://github.com/Nwflower/dsh-chat-import) (MIT, a dsh
plugin — see `reference/`). Write-side formats were reverse-engineered field-by-field against the locally
installed dsh 0.1.1-rc / zcode 0.16.x.
