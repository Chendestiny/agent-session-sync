# Claude Code CLI 会话结构详解

核实基线：Claude Code 2.1.x（Windows 本机 `~/.claude/projects`，121 个 jsonl 实测、
过滤后 13 个真实会话）。**当前状态：读取器已实现**（`readers.read_claude`，第六家源；
写入方向不做——单向设计）。

## 1. 存储布局

```
~/.claude/
├── projects/                        ← 每个工作区(cwd)一个目录
│   ├── C--Users-neware/             ← 目录名 = cwd 转义（\: 与 . → -）
│   │   ├── <sessionId(uuid)>.jsonl  ← 一个会话一个文件（追加写）
│   │   └── …
│   └── C--Users-neware-AppData-Local-Temp-claude-ping/   ← TEMP 冒烟，读取跳过
├── history.jsonl                    ← 命令历史（不用）
└── …（settings/todos/stats 等，不用）
```

注意：**cwd 不要从目录名反推**（转义有歧义，如 `Temp-claude-ping` 可能是
`Temp\claude-ping` 也可能是 `Temp-claude-ping`）——每条对话行自带 `cwd` 字段，直接用。

## 2. JSONL 行类型（本机 121 文件实测分布）

| type | 处理 | 说明 |
|---|---|---|
| `user` / `assistant` | **解析** | 唯二的对话行；Anthropic API message 信封 |
| `ai-title` | 标题 | `{aiTitle, sessionId}`，AI 生成标题（同 WorkBuddy 字段名） |
| `summary` | 摘要 | `{summary, leafUuid}`，压缩摘要 → Session.summary |
| `queue-operation` `progress` `attachment` `permission-mode` `mode` `file-history-snapshot` `file-history-delta` `last-prompt` `system` | 跳过 | 运行时事件，非对话内容 |

对话行公共字段：`parentUuid, isSidechain, isMeta, uuid, timestamp(ISO), cwd,
sessionId, version, gitBranch, userType, entrypoint`。

**语义过滤**（同 zcode 思路）：
- `isSidechain: true` → 子代理链，整行跳过（等价 codex 的 subagent rollout 跳过）
- `isMeta: true` → 框架注入行，跳过

## 3. message.content 形态

`message = {role, model(assistant), content, stop_reason, usage…}`，content 两态：

- **字符串**：纯文本（user 直问最常见）
- **块数组**：
  - `{"type":"text","text"}` → 文本
  - `{"type":"thinking","thinking"}` → reasoning
  - `{"type":"tool_use","id","name","input"(对象)}` → 工具调用（assistant 侧）
  - `{"type":"tool_result","tool_use_id","content"(str|块数组),"is_error"}` → 工具结果（**在 user 行里**）

**关键结构规则**：Claude 把 tool_result 放在下一条 user 消息里。读取器用
`call_steps[tool_use_id]` 把结果挂回发起调用的 assistant step，该 user 行**不单独成轮**；
剥离注入后无真实文本的 user 行一律不成轮。

## 4. 注入剥离（user 文本）

`<system-reminder>…</system-reminder>`、`<command-name>/<command-message>/<command-args>/
<command-contents>`（斜杠命令包装）、`<local-command-stdout>…</local-command-stdout>`
（本地命令回显）整体删除；`Caveat: The messages below were generated…` 开头的包装行
整行丢弃。剥离后为空 = 非真实提问。

## 5. 冒烟会话处理

cwd 落在 `%TEMP%` 下的会话（claude-ping / claude-test / tmp-* 等 sdk 冒烟）**整文件跳过**；
不在 TEMP 下的 "PONG" 类冒烟（如在用户目录跑的）会导入，由 prune 的打招呼规则兜底。

## 6. 时间与增量

- `created_at` = 首条对话行 `timestamp`（ISO → ms）
- `updated_at` = 文件 mtime（会话文件只追加，mtime 即最后活跃）
- 标题优先级：`ai-title` > 首轮提问回退（synthesize 统一加 `[claude]` 前缀）
