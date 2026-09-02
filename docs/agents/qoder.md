# Qoder（阿里 AI IDE）会话结构详解

核实基线：本机 Qoder 2026-08/09 版实测（`%APPDATA%/Qoder` + `~/.qoder`）；读取器
`agentsync.readers.read_qoder`（只读源，2026-09-02 接入；无写入方向）。

## 1. 存储总览（两跳，索引与正文分离）

```
%APPDATA%/Qoder/User/globalStorage/state.vscdb   ← ① 任务索引（VS Code 系键值库）
  ItemTable['aicoding.questTaskListSnapshot']
    = {version, ownerAccountId, folders: {<cwd>: {updatedAt, tasks: [...]}}}
    task = {id: "task-<hex20>", name, status, createTime, updatedAtTimestamp,
            lastUserQueryAt, sourceBranch/targetBranch, machineId, ...}

~/.qoder/cache/projects/<项目名>-<hash8>/          ← ② 对话正文
  conversation-history/<id前8>/<id前8>.jsonl
```

**文件名是 id 截前 8 位**（实测坑）：`task-63023fd180d845e3acc4` →
`conversation-history/task-630/task-630.jsonl`；按全 id 找文件必然落空。
读取器先试 `<前8>/<前8>.jsonl`，兼容回退 `**/<全id>.jsonl`。

## 2. 正文行形态

```json
{"role":"user","timestamp":"2026-08-26T02:45:00.000Z",
 "message":{"role":"user","content":[{"type":"text","text":"<user_query>\n…\n</user_query>"}]}}
{"role":"assistant","message":{"content":[{"type":"text","text":"…"}]}}
```

- user 行开轮；首问常被 `<user_query>…</user_query>` 传输包装（整段被包才剥，读取器处理）
- assistant 行成 step；工具调用块按 `type: tool_call|tool_use` 防御性映射（本机样本纯 text）
- 时间戳：索引里是毫秒 int；行内是 RFC3339

## 3. 周边与边界

- **索引/正文可脱钩**：任务完成后执行现场（`~/.qoder/cache/experts/<id>.session.execution/`
  的 agents/inboxes）会被清空；索引在、正文无的任务读取器跳过（"幽灵任务"）
- **旧版布局已弃用**：`~/.qwen/projects/<cwd转义>/chats/<uuid>.jsonl`（2026-03 前，
  uuid/parentUuid 形态）——按用户确认不再读取
- 周边：`SharedClientCache/cache/ai_tracker/`（AI 改动行账本，非对话）、
  `knowledges/`、`memories/`
- 回收站/子代理概念：快照里仅 status 字段（Completed 等），无归档名单；子代理现场在
  experts 下，随任务清理
