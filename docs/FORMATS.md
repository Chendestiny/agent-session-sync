# 会话格式总览与归一化 IR（索引页）

各 agent 的**深度格式规格**已按家拆分，见 `agents/` 子目录：

| 文档 | 家 | 一句话 |
|---|---|---|
| [agents/dsh.md](agents/dsh.md) | DeepSeek Harness | 多帧 zstd 事件日志 + workspace.json 分组挂载 |
| [agents/zcode.md](agents/zcode.md) | zcode | db.sqlite 三表（session/message/part），opencode 同族（读取源；写入器已弃用存档） |
| [agents/qoder.md](agents/qoder.md) | qoder | 任务索引 vscdb + conversation-history JSONL 两跳（读取源；文件名=id 截 8 位） |
| [agents/openclaw.md](agents/openclaw.md) | openclaw | sessions JSONL + reset 孤儿快照（读取源；toolResult 独立行配对） |
| [agents/hermes.md](agents/hermes.md) | hermes | state.db 两表，OpenAI chat 风格，单库不分区 |
| [agents/codex.md](agents/codex.md) | codex CLI | rollout JSONL（session_meta/response_item/turn_context） |
| [agents/workbuddy.md](agents/workbuddy.md) | WorkBuddy | 元数据 db + projects/<slug>/JSONL 双层（读写源） |
| [agents/claude.md](agents/claude.md) | Claude Code | ~/.claude/projects/<cwd转义>/<id>.jsonl |
| [agents/opencode.md](agents/opencode.md) | OpenCode | opencode.db 三表 + 事件溯源（uuid5 旁路清单防环） |
| [agents/cursor.md](agents/cursor.md) | Cursor | globalStorage cursorDiskKV（composer+bubble 键前缀关联） |
| [agents/trae.md](agents/trae.md) | Trae | CN 版正文库自加密读取阻断（仅原始库快照备份） |
| [agents/minimax.md](agents/minimax.md) | MiniMax Code | v2 runtime-state.sqlite 注册表+消息行（读写源） |
| [agents/pi.md](agents/pi.md) | Pi Agent | ~/.pi 事件流 JSONL（append-only 树，parentId 链；读写源） |
| [agents/gemini.md](agents/gemini.md) | Gemini CLI | tmp/*/chats $set 快照+裸消息行（读写源；流式碎片坑） |
| [agents/cline.md](agents/cline.md) | Cline | 扩展 globalStorage tasks 三件 JSON（读写源；旁路清单） |
| [agents/grok.md](agents/grok.md) | Grok Build | 占位：sessions 三层 JSONL（仓库已核，待实装） |
| [agents/mimo.md](agents/mimo.md) | MiMo-Code | 占位：mimocode.db 同构 opencode（待实装） |
| [agents/kimi.md](agents/kimi.md) | Kimi Code | 占位：~/.kimi-code 自研 minidb（待实装） |
| [agents/copilot.md](agents/copilot.md) | GitHub Copilot | 占位：VS Code chatSessions（待逆向） |

本页保留跨家通用的内容：归一化 IR、ID 铸造、时间戳约定、预算裁剪。

## 归一化 IR（agentsync/model.py）

```
Session{source, source_id, title, cwd, created_at(毫秒), model, system_prompt, summary, turns[]}
Turn{prompt, steps[], time(毫秒, 0=未知→写入器回退会话创建时间)}
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

## Web dashboard（agentsync/webui/，`sync.py web`）

`python sync.py web` → 127.0.0.1:8321（`--port` 可改）自动开浏览器；**写端点仅目录绑定族**
（POST /api/bind-path、/api/pick-folder，其余 POST 405）、
实时读源无缓存、页面单文件离线可用。端点契约：

| 端点 | 返回 |
|---|---|
| `GET /` | 单页面板（总览时间轴 / 会话列表 / 会话详情三视图） |
| `GET /api/overview` | `{sources:[{name,ok,path}], store:{dir,counts,state,push}|null, state:{源:水位ms}}` |
| `GET /api/sessions?source=&q=&from=&to=` | 会话 meta 列表（updated_at 降序）：`source,id,title,cwd,created_at,updated_at,turns,messages,tools,span_first,span_last,path` |
| `GET /api/session?source=&id=` | 全量 IR JSON（`store.session_to_dict`，含 `turns[].time`） |

## 溯源

- dsh 写入纪律与 WorkBuddy 写入约束：本机逆向 + `reference/dsh-chat-import`（MIT）
- WorkBuddy / codex 写入配方与多副本合并坑：`本地克隆的 agentctxsync 仓库`（MIT，mcp/adapters/）
- 核实基线版本见各文档头部（2026-08-25）
