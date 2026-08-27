# 会话格式总览与归一化 IR（索引页）

各 agent 的**深度格式规格**已按家拆分，见 `agents/` 子目录：

| 文档 | 家 | 一句话 |
|---|---|---|
| [agents/dsh.md](agents/dsh.md) | DeepSeek Harness | 多帧 zstd 事件日志 + workspace.json 分组挂载 |
| [agents/zcode.md](agents/zcode.md) | zcode | db.sqlite 三表（session/message/part），opencode 同族（读取源；写入器已弃用存档） |
| [agents/hermes.md](agents/hermes.md) | hermes | state.db 两表，OpenAI chat 风格，单库不分区 |
| [agents/codex.md](agents/codex.md) | codex CLI | rollout JSONL（session_meta/response_item/turn_context） |
| [agents/workbuddy.md](agents/workbuddy.md) | WorkBuddy | 元数据 db + projects/<slug>/JSONL 双层（reader 已实现；写入配方备而未用） |

本页保留跨家通用的内容：归一化 IR、ID 铸造、时间戳约定、预算裁剪。

## 归一化 IR（agentsync/model.py）

```
Session{source, source_id, title, cwd, created_at(毫秒), model, system_prompt, summary, turns[]}
Turn{prompt, steps[]}
Step{content[block], tool_calls[{id,name,arguments}], tool_results[{tool_call_id,content[block],is_error}], model}
block = {"type":"text"|"reasoning","text"} | {"type":"tool-call","id","name","arguments"}
```

## ID 铸造（幂等）

- dsh 导入：`import-<源ID去[^a-zA-Z0-9_-]截64>`
- zcode 导入：`sess_<uuid5("agentsync/zcode", source|source_id)>`

## 时间戳约定

| 家 | 存储单位 | IR（毫秒） |
|---|---|---|
| dsh / zcode / workbuddy | 毫秒 | 原样 |
| hermes / codex(RFC3339) | 秒 | ×1000 / 解析 |

## 预算三层裁剪（超长会话保续聊，对齐 dsh-chat-import）

- **L1 单条**：text ≤16K 字符、工具结果 ≤40K（保头 75% + 尾 25%，中置裁剪标记）；
- **L2 整体**：保留开头锚点（默认 3 轮）+ 压缩摘要（reasoning 块）+ 尾部贪心装填；
- **L3 兜底**：裁剪后单条仍超预算一半即丢弃（首轮 prompt 永不丢）。

## 溯源

- dsh 写入纪律与 WorkBuddy 写入约束：本机逆向 + `reference/dsh-chat-import`（MIT）
- WorkBuddy / codex 写入配方与多副本合并坑：`本地克隆的 agentctxsync 仓库`（MIT，mcp/adapters/）
- 核实基线版本见各文档头部（2026-08-25）
