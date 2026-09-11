# MiMo-Code（mimo CLI）会话结构详解（实机已核验）

核实基线：源码核验（MiMo-Code 开源仓库，OpenCode fork monorepo）
+ **2026-09-09 本机实装核验**（MiMoCode CLI 0.1.14；桌面版未公测，现役形态是 CLI，
`~/.mimocode/bin/mimo.exe`，Bun 单文件 ~129MB）。reader/writer 仍待实装，
但存储布局与 schema 已实机落盘核验，下文均实测值。

## 1. 存储布局（实机实锤，修正源码期推断）

```
~\.mimocode\bin\mimo.exe            ← CLI 本体；package.json + node_modules(@mimo-ai/plugin+sdk)
~\.config\mimocode\mimocode.jsonc   ← 配置（含同名 package.json/node_modules 镜像）
~\.local\share\mimocode\            ← 数据根（重点）
  ├── mimocode.db / -shm / -wal     ← 会话库（sqlite，WAL 模式，实测写后 wal >2MB）
  ├── auth.json / installation_id
  ├── trusted-workspaces.json
  └── builtin_skills\<ver>\skills\… ← 内置技能目录（含 claude-code/codex 等）
~\.local\state\mimocode\            ← model.json + prompt-history.jsonl（输入历史）
~\.cache\mimocode\                  ← models.json + version + bin\rg.exe
```

**关键修正**：源码期推断 Windows 走 `%LOCALAPPDATA%\mimocode\`——实测**不存在**该目录，
XDG 四分区在 Windows 上字面生效（`~/.local/share` `~/.local/state` `~/.cache` `~/.config`）。
reader 探测顺序：`~/.local/share/mimocode/mimocode.db`（唯一实锤位置）。

## 2. 库 schema（0.1.14 实测 35 表）

核心三表与 opencode 同构（`data` 列存 JSON）：

- **session**：`id, project_id, parent_id, slug, directory, title, version,
  share_url, summary_*, revert, permission, time_created/updated/compacting/archived
  (ms 时间戳), workspace_id, context_from, context_watermark,
  last_checkpoint_message_id, prompt(JSON), auto_worktree_hint_sent`
  ——源码期推断的列差异全部坐实，另有 `time_compacting`/`last_checkpoint_message_id`。
- **message**：`id, session_id, agent_id, time_created, time_updated, data`
  （data.role=user/assistant）。
- **part**：`id, message_id, session_id, time_created, time_updated, data`。
  实测 part 类型：`text` / `reasoning` / `step-start` / `step-finish`
  （step-finish.data 带 reason/snapshot/tokens{total,input,output,reasoning,cache}）
  ——与 opencode 引擎一致，`read_opencode` 的解析思路可直接套。
- **project**：`id(uuid4 或 'global'), worktree(=cwd), vcs, name, …`；
  TEMP 目录会话挂到 `'global'` 兜底行。

其余表（读取侧关注 / 均实测有行或空）：`actor_registry`（子代理/后台 actor，
含 parent_actor_id/background/mode——**子代理判别可用**）、`external_import`、
`claude_import`、`event`+`event_sequence`、`inbox`、`task`+`task_event`、
`workflow_run`、`session_prefix_snapshot`（system prompt 快照）、
`session_share`、`history_fts*`（搜索索引，131 行）、`memory_fts*`、
`permission`+`permission_grant`、`workspace`、`account*`、`todo`。

## 3. id 形状（防环判别）

`ses_` / `msg_` / `prt_` 前缀 + 22 位 nanoid（含大小写字母数字），
**不是 uuid**——源码期担心的「opencode 桌面版 uuidv5 误杀」在此家不存在，
按前缀判别即可，天然与工具铸的任何 id 不撞。

## 4. 大坑：首跑自动导入 Claude Code 会话（实测踩中）

安装后首跑，mimo **自动扫描并导入了本机 Claude Code 会话**：`external_import` 表
22 行，`source='cc'`，`source_path` 指向 `~/.claude/projects/...jsonl`，
被导入会话的 directory 即原 claude 会话的 cwd（含 TEMP 冒烟）。后果：
**直接全量读 mimo 会与 claude 源重复外流**。reader 实装必须排除：
`session.id IN (SELECT session_id FROM external_import UNION SELECT session_id
FROM claude_import)`，并照常排除 TEMP cwd 冒烟（mimo 自测也产生 calculator.py
等 TEMP 会话）。另有 `external_import` 的反向含义：mimo 自身是个「导入器」，
后续版本可能再导入别家——接入时按 source 列白名单放行 native 会话。

## 5. 实装状态

- **reader 已实装（2026-09-09）**：`readers.read_mimo`——套 opencode 引擎（表同构），
  §4 排除（external_import + claude_import + 旁路清单）+ TEMP/无 cwd/归档过滤；
  标题直接用 session.title；selftest 沙箱样本覆盖三类排除。
  子代理（actor_registry）暂未接，实机出现真实样本后按需补。
- writer 未实装：从 opencodewrite 改造方向不变（补 slug/version 必填、prompt JSON
  等新列；WAL 三件套处理），regtest 试点通过后再开。
