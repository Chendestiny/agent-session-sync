# hermes 会话结构详解

核实基线：Hermes Agent 0.19.0（Windows）；db 位于 `%LOCALAPPDATA%\hermes\state.db`（387 MB + WAL）。
注意：`%LOCALAPPDATA%\hermes\sessions\` 下的 885 个 `request_dump_*.json` 是 LLM 请求调试转储，**不是**会话库。

## 1. 存储总览

```
%LOCALAPPDATA%\hermes\          ← HERMES_HOME（Linux/macOS 为 ~/.hermes）
├── state.db                    ← 会话权威库（sessions + messages 两表）
├── state.db-wal / -shm
├── sessions/request_dump_*.json  ← 调试转储（忽略）
├── config.yaml / auth.json
└── profiles/<name>/state.db    ← 独立 profile 库（本机为空）
```

hermes 是**单库不分区**（无工作区分目录），工作区信息只作为 `sessions.cwd` 列存在。

## 2. 表结构

```sql
sessions(
  id TEXT,               -- 如 20260824_112431_348591（日期_时间_随机）
  source TEXT,           -- 'cli' / …
  model TEXT,            -- 如 glm-5.3
  model_config TEXT,     -- JSON：max_iterations/reasoning_config/max_tokens…
  system_prompt TEXT,    -- 会话系统提示词（完整文本）
  parent_session_id TEXT,
  started_at REAL,       -- Unix 秒（浮点）
  ended_at REAL, end_reason TEXT,
  message_count INTEGER, tool_call_count INTEGER,
  input_tokens/output_tokens/cache_read_tokens/cache_write_tokens/reasoning_tokens INTEGER,
  estimated_cost_usd REAL, cost_status TEXT,
  title TEXT,
  cwd TEXT,              -- 工作区路径（2026-05 旧会话为 NULL）
  git_branch TEXT, git_repo_root TEXT,
  handoff_state/handoff_platform/handoff_error TEXT,   -- 跨端交接状态
  profile_name TEXT, rewind_count INTEGER, archived INTEGER, …)

messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- 自增 = 消息顺序
  session_id TEXT,
  role TEXT,             -- 'user' | 'assistant' | 'tool'
  content TEXT,          -- 纯文本（assistant 正文）
  tool_call_id TEXT,     -- role='tool' 时关联调用
  tool_calls TEXT,       -- JSON：OpenAI 风格调用数组（assistant）
  tool_name TEXT,
  timestamp REAL,        -- Unix 秒
  token_count INTEGER, finish_reason TEXT,
  reasoning TEXT,        -- 思维链文本（assistant）
  reasoning_content/reasoning_details/codex_reasoning_items/codex_message_items TEXT,
  observed INTEGER, active INTEGER, compacted INTEGER,
  api_content TEXT, …)
```

本机基线：217 会话 / 31892 消息（user 2219、assistant 15579、tool 14094）。

## 3. 消息形态（OpenAI chat 风格 + Claude 血统）

**user**：`content` 纯文本字符串（提问）。

**assistant**：

```json
content   = "回复正文（纯文本）"
reasoning = "思维链文本"
tool_calls = [{"id":"tooluse_xxx","call_id":"tooluse_xxx","type":"function",
               "function":{"name":"skill_view","arguments":"{\"name\":…}"}}]
```

（arguments 是 JSON 字符串；id/call_id 二选一存在）

**tool（结果行）**：`tool_call_id` 关联调用，`content` 即结果文本（常为 JSON 文本的字符串）。

## 4. 读取规则（本工具 reader）

- 按 `messages.id` 升序 = 消息顺序；时间戳秒 → 毫秒（×1000）；
- user 文本开新轮；assistant = 新 step（reasoning 列 → reasoning 块，content → text 块，
  tool_calls 解析为 ToolCall）；role='tool' 按 tool_call_id 挂回所属 step；
- 无前驱 user 的孤儿 assistant 丢弃（回合平衡）；空 step 不入轮。

## 5. 写入侧注意（若未来实现 hermes 写入）

- agentctxsync 的 hermes 适配器经验：跨进程写 state.db 有锁竞争，需要 WAL 超时重试；
- `handoff_state/handoff_platform` 列是 hermes 自带的跨端交接机制（同产品内同步优先用它）；
- 387 MB 大库，写入务必先备份 + 单事务。

## 6. 已知边界

- 2026-05 的旧会话 `cwd` 为 NULL（导入 dsh → `_no-cwd`，导入 zcode → 主目录工作区）；
- `archived=1` 会话默认仍读取（可用参数过滤）；
- `system_prompt` 列完整保存了系统提示词（导入时按需作为上下文注入）。
