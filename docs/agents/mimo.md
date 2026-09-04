# MiMo-Code 会话结构详解（占位：待实装核验）

核实基线：源码核验（D:\Project_github\MiMo-Code-main，OpenCode fork monorepo）。
本机未装，reader/writer 待实装。

## 1. 存储布局（源码实锤）

```
MIMOCODE_HOME=<根>          → <根>\data\mimocode.db      ← 四分区制（data/cache/config/state）
XDG（Windows=%LOCALAPPDATA%）→ %LOCALAPPDATA%\mimocode\mimocode.db
（profile 模式：mimocode-<safe>.db）
```

- 库：`mimocode.db`（storage/db.ts，Bun/node sqlite + drizzle）
- schema 与 opencode 同族（packages/opencode/src/session/session.sql.ts）：
  **SessionTable / MessageTable / PartTable** + Todo/Permission/Share/Workspace/
  Account/Project/HistoryFts
- 相对 opencode 的列差异：`workspace_id / context_from / context_watermark /
  slug(version 必填) / prompt(JSON) / summary_diffs / revert / auto_worktree_hint_sent`
  等列；保留 `parent_id / time_archived / directory / title`

## 2. 接入计划

实装后：读取器直接试 opencode 引擎（表同构）；写入器从 opencodewrite 改造
（补 NOT NULL 新列 + slug/version）。防环：opencode 桌面版原生 id 也是 uuidv5
的坑在此家同样可能存在——接入时先采样原生 id 形状再定判别方案。
