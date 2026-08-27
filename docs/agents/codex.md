# codex 会话结构详解

核实基线：Codex CLI 0.137.0（Windows）；会话目录 `~/.codex/sessions/`。
注意：本机 dsh 早期版本也写过此目录（codex 血统），8-17 后 dsh 已迁移到 `~/.dsh/sessions`。

## 1. 存储布局

```
~/.codex/
├── sessions/
│   └── YYYY/MM/DD/rollout-<YYYY-MM-DDTHH-MM-SS>-<uuid>.jsonl   ← 按天分区
│       （旧版本为 sessions/ 平铺，两种布局都要扫）
├── session_index.jsonl     ← 标题索引（append-only：{"id","thread_name","updated_at"}）
├── config.toml / instructions.md / AGENTS.md
└── skills/
```

一个会话一个文件；会话 ID = 文件名里的 UUID。

## 2. 文件格式（JSONL，事件 envelope）

每行 `{"timestamp":"RFC3339","type","payload"}`，三种 type：

### session_meta（首行）

```json
{"type":"session_meta","payload":{"id":"<uuid>","timestamp":"…",
 "cwd":"C:\\Users\\demo","originator":"codex-tui","cli_version":"0.137.0",
 "source":"cli","thread_source":"user","model_provider":"custom",…}}
```

`thread_source=="subagent"` 或 `payload.source.subagent` 为子代理线程 → 不是独立会话，跳过。
（`forked_from_id`/`parent_thread_id` 是 fork 出的主会话，保留。）

### turn_context（每轮一条）

`payload`：`{turn_id, cwd, workspace_roots, current_date, timezone, approval_policy, sandbox, model…}`。
模型名从这里取。

### response_item（对话内容唯一来源）

payload 为 OpenAI Responses API item：

| payload.type | 字段 | 映射 |
|---|---|---|
| `message` role=user | content `[{type:"input_text",text}]` | 提问（`<` 开头的块是 harness 注入，过滤） |
| `message` role=assistant | content `[{type:"output_text",text}]` | 回复 |
| `message` role=developer | 同上 | 系统注入（默认忽略） |
| `function_call` | `{call_id,name,arguments(JSON字符串)}` | 工具调用 |
| `custom_tool_call` | `{call_id,name,input(自由文本/JS)}` | 自定义工具（apply_patch 等）；input 尝试提取 `{…}` 转 JSON，失败原样 |
| `function_call_output` | `{call_id,output}` | 结果；output 可能是纯文本或 `{"output":"…"}` JSON 字符串 |
| `custom_tool_call_output` | 同上 | |
| `reasoning` | summary（常加密） | 忽略 |

### event_msg（UI 事件，**不要用**）

`user_message`/`agent_message` 等是 response_item 的重复（官方 schema 注释明确警告会重复计数）。

## 3. 读取规则（本工具 reader）

- glob `**/*.jsonl` 递归；session_meta 取 id/cwd/时间/子代理判定；turn_context 取模型；
- user 消息（滤 `<` 开头块）开轮；assistant 开 step；function_call 挂最近 step（无则新开）；
  output 按 call_id 挂回调用所属 step；
- 无显式标题源：标题回退首问（或读 session_index.jsonl，本工具暂不读）。

## 4. 写入侧（若需要 codex→codex 回写，参考 agentctxsync）

- 文件 append-only，绝不改已有行；
- 新文件命名 `rollout-<ts>-<uuid>.jsonl` 放对应日期分区；
- 外来 ID 需映射成 UUID 形态（codex 桌面端只对 UUID 形态的 id 做索引回填；
  agentctxsync 用 `.hermes-sync-idmap.json` 持久化映射）；
- 标题写 `~/.codex/session_index.jsonl`（append）；codex 重扫后可见。
