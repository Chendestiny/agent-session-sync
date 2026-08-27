# 🔄 agent-session-sync

[简体中文](./README.md) | **English**

Consolidate chat sessions from **codex / hermes / dsh (DeepSeek Harness) / zcode / workbuddy**
into **dsh** — any historical session can be imported into dsh and **continued**, plus a unified
**Markdown archive**. One-way by design: zcode acts as a read-only source (the write direction was
removed — mirrored conversations on both sides get messy, and live-db writes showed rendering issues).

```
codex CLI ─┐
hermes    ─┤
dsh       ─┼─→ Normalized IR (turns) ─→ dsh        (resumable, idempotent + incremental)
zcode     ─┤                          ─→ Markdown archive (browse & search)
workbuddy ─┘
```

> Only dsh is written to. Writing hermes risks cross-process lock contention; codex has been
> superseded by dsh. The WorkBuddy write recipe is documented in `docs/agents/workbuddy.md` §5
> should you ever need it.

Design references [Nwflower/dsh-chat-import](https://github.com/Nwflower/dsh-chat-import) (MIT, a dsh
plugin — see `reference/`). Write-side formats were reverse-engineered field-by-field against the locally
installed dsh 0.1.1-rc / zcode 0.16.x.

## 🚀 Quick Start

Requirements: Python 3.10+ with `zstandard`. The native-backend verification needs **Node 22+**
(`nvm use 22`; `tools/verify-dsh-backend.cmd` auto-picks the nvm 22.x).

```bash
cd "<project dir>"
pip install zstandard        # the only third-party Python dependency
nvm use 22                   # optional, for node-based verification

python sync.py selftest                        # sandbox self-test: must be green first
python sync.py status                          # five-source overview
python sync.py to-dsh   --source zcode         # dry-run plan
python sync.py to-dsh   --source zcode --apply # ① import into dsh
# ② fully quit dsh, then: attach to workspace groups + backfill sidebar title cache
python sync.py attach-dsh --apply
# ③ start dsh: imported sessions appear under their workspace groups, resumable

python sync.py archive  --source all --apply   # Markdown archive → ./archive
python sync.py verify                          # validate imported sessions
tools\verify-dsh-backend.cmd                   # strong check via dsh's own backend (Node 22)
```

> **The full dsh loop = import (to-dsh) + group & titles (attach-dsh) + restart dsh.** Skipping step ②
> leaves sessions in "ungrouped" with no list titles until opened. Bulk-rename titles: edit `titles.json`,
> then `python sync.py to-dsh --apply --force --titles titles.json --budget 550000`.
>
> Agent-facing manual: **AGENTS.md** is the complete entry point (cookbook / safety rules /
> troubleshooting / upgrade adaptation) — hand it to any agent.

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

- Five-source reading: codex / hermes / dsh / zcode / workbuddy all parse correctly
  (tool calls incl. args/results/failures, reasoning, image placeholders)
- dsh writing: 100% read-back pass via dsh's own JsonlSessionPersistence backend
  (`tools/verify-dsh-backend.cmd`)
- Workspace partition encodings: zero mismatches in full-corpus comparisons
- Idempotent + incremental; three-layer budget trimming keeps huge sessions resumable;
  zcode writer passed round-trip regression on a db copy

### ⚠️ Pitfalls (all fixed in code + docs)

| Pitfall | Symptom | Fix |
|---|---|---|
| `session/imported` missing top-level `ignorable:true` | whole log rejected, titles fall back, everything ungrouped | writer emits the flag; validator embeds the host event vocabulary |
| workspace record missing `createdAt/updatedAt` | dsh fails to boot (Zod validation) | apply_attach writes all 5 keys; schema captured in docs/agents/dsh.md |
| nested-path workspaces | dsh startup prunes those records | attach mirrors dsh: nested cwds don't create groups |
| writing sessions into zcode | wrong timestamps / blank renders | direction removed entirely (see README top); write kept as archived reference only |
| projcache missing title row | no list title until opened | sidebar reads the projection cache; attach-dsh now backfills title rows |
| bidirectional sync pollutes both lists | duplicated forks on both sides | product decision: one-way to dsh |

## 📂 Layout

See the Chinese README's 目录结构 section, or explore the tree directly:
`AGENTS.md` (manual) · `SKILL.md` (skill bundle) · `sync.py` (CLI) · `agentsync/` (library) ·
`docs/FORMATS.md` + `docs/agents/*.md` (deep format specs per agent) · `examples/` · `tools/`.

## 🔒 Safety Boundaries

- Reads are always read-only (sqlite `mode=ro` URI).
- Writes to dsh only create new `import-*` session directories; native sessions are never touched.
- Never writes zcode/hermes/codex/workbuddy stores.
- Restore: use the auto backups (`*.agentsync-bak-*`) created before every mutation.
