# zcode 会话结构详解

核实基线：zcode 0.16.3（Windows）；db 位于 `~/.zcode/cli/db/db.sqlite`（WAL 模式）。
zcode 的三表结构与 opencode 同族（agentctxsync 的 opencode 适配器即同款 schema，id 前缀差异 `sess_` vs `ses_`）。

## 1. 存储总览

```
~/.zcode/cli/
├── db/db.sqlite          ← 会话权威索引（session/message/part 三表）
├── rollout/model-io-sess_*.jsonl   ← 模型 IO 原始流（参考，不读写）
├── agents/sess_*/        ← 子代理转写（独立文件，不在 db 主表）
├── artifacts/sess_*/     ← 会话产物
└── exec/bash-startup/sess_*/

~/.zcode/v2/
├── tasks-index.sqlite    ← ★ 桌面端 UI 任务索引（tasks.task_id = sess_<uuid>）
│                            历史列表的数据源之一；与 cli/db 不是同一库！
├── bot-state.v2.json     ← UI 状态（当前会话指针）
└── checkpoints/ crash/ logs/
```

**关键教训（2026-08-26 清理实录）**：只清 `cli/db.sqlite` 不够——桌面端 UI 从
`v2/tasks-index.sqlite` 的 `tasks` 表列会话，僵尸行会让历史列表残留条目、点开报
`fault.subscribe.sessionNotFound`。清理会话必须同步清两个库（按 `task_id NOT IN
(SELECT id FROM session)` 对齐）。当时状态：211 个 task 中 199 个僵尸，清理后 12 个
（与 db 存活会话对齐），备份 `tasks-index.sqlite.cleanup-bak-*`。

## 2. 三张核心表

```sql
session(id, project_id, workspace_id, parent_id, slug, directory, path, title,
        version, share_url, summary_additions, summary_deletions, summary_files,
        summary_diffs, revert, permission, time_created, time_updated,
        time_compacting, time_archived, task_type, title_source,
        title_message_id, time_title_updated, trace_id)
message(id, session_id, time_created, time_updated, data, sequence)
part(id, message_id, session_id, time_created, time_updated, data, sequence)
```

`session_entry` 表是运行时日志（model_selection、workspace_checkpoint 等），导入无需写。

## 3. ID 与分区

| 项 | 规则 | 例 |
|---|---|---|
| session.id | `sess_<uuid>`（导入用 `sess_<uuid5(source|source_id)>` 幂等） | `sess_07c406f6-…` |
| slug | 同 id | |
| project_id | `proj_` + 路径小写、`:` 丢弃、`\`/`/`→`-`（点保留） | `D:\code\agent-svc`→`proj_d-bi_agent` |
| workspace_id | 现网全 NULL | |
| parent_id | 非空=子代理线程（读取时默认排除；本机 18 会话中 6 个） | |

历史列表按 `directory`（=工作区路径）分组，project_id 是其派生索引键。

## 4. 关键列语义（真实样本）

- `directory` 与 `path` 同值 = 工作区路径；
- `permission` = `{"mode":"build"}`；`task_type` = `"interactive"`；
- `title_source` ∈ `first_input | generated | custom`（导入用 first_input）；
- `version` = 应用版本（如 `0.16.3`，导入取库内最新值）；
- 时间全部毫秒；`message.sequence` 会话内 0 起连续；`part.sequence` 消息内 0 起连续。

## 5. message.data（JSON）

**user：**

```json
{"role":"user","time":{"created":<ms>},"agent":"zcode-agent",
 "semantics":{"origin":"real_user","kind":"user_prompt","uiVisibility":"visible",
              "providerVisibility":"visible","transcriptVisibility":"visible"},
 "anchor":{"turnId":"turn_<uuid>","origin":"realUser"}}
```

（原生行还带 `model`、`contextSnapshot.envInfo`、`tools`、`metadata` 等，导入可省）

**assistant：**

```json
{"role":"assistant","time":{"created":<ms>,"completed":<ms>},"parentID":"<本轮user消息id>",
 "modelID":"glm-5.3","mode":"build","agent":"zcode-agent",
 "path":{"cwd":"…","root":"…"},"cost":0,
 "tokens":{"total":0,"input":0,"output":0,"reasoning":0,"cache":{"read":0,"write":0}},
 "finish":"tool-calls|stop",
 "semantics":{"origin":"agent_runtime","kind":"assistant_response",…},
 "anchor":{"turnId":"turn_<uuid>"}}
```

`anchor.turnId` 把同一轮的 user/assistant 消息绑成一组。

## 6. part.data（JSON，按消息内 sequence 排序）

**user 消息**：仅 `{"type":"text","text":"提问","time":{"start","end"}}`。

**assistant 消息**（固定骨架）：

```
{"type":"step-start"}
{"type":"text","text":"…","time":{"start","end"}}                 # 可多条
{"type":"reasoning","text":"…","time":{…},"metadata":{…可选}}      # 可选可多条
{"type":"tool","callID":"call_xxx","tool":"Bash",
 "state":{"status":"completed|error","input":{…参数对象…},
          "output":"结果文本","title":"Bash",
          "metadata":{"schemaVersion":1,…可选}}}
{"type":"step-finish","reason":"stop|tool-calls","cost":0,"tokens":{…}}
```

工具调用的参数与结果**内嵌在同一个 tool part** 的 state 里（没有独立的 tool-result 消息）。
其余结构块：`timeline`（模型切换分隔）、`compaction`（压缩摘要，`summary.body`）、
`file`（图片，读侧转 `[image: 名]` 占位）。

## 7. 读取规则（本工具 reader）

- 会话按 `parent_id IS NULL` 过滤；消息按 sequence；parts 按 (message, sequence)；
- **语义过滤（关键）**：`semantics.origin/kind` 是 zcode 自带的注入标记——
  只认 `origin=="real_user" && kind=="user_prompt"` 的 user 消息开轮；
  注入类一律跳过（实测分布：`todo_reminder` 227、`background_notification` 32、
  `system_reminder` 10，均 origin=`agent_runtime` 且 data 带 `synthetic` 标记、UI hidden）。
  只靠 `<system-reminder>` 文本过滤会漏掉它们；
- `kind=="compact_summary"` 的 user 消息：消息级 `data.summary`（`{body}` 或字符串）
  → 会话压缩摘要，不成为对话轮；
- assistant：`kind=="assistant_response"`（hidden 也保留——是 UI 折叠的真实输出，
  56 个 hidden 全部带 parts）；`timeline_event`（模型切换分隔）跳过；
- assistant 一条消息 = 一个 step（text/reasoning/tool 映射；tool 同步产出 call+result 对，
  `state.status ∈ {failed,error}` → isError）。

## 8. 写入方向（已废弃，2026-08-26）

曾实现过 IR → zcode 行写入（uuid5 幂等、进程检测、备份、单事务），实测暴露两类问题后
**整体移除（zcode 只出不进）**：

- 导入会话 time_updated 异常（旧会话显示「1 分钟前」）——客户端对时间字段有额外处理；
- 部分会话渲染空白——客户端读取行时有本工具未完全复刻的字段/状态依赖。

历史实现保留在 `agentsync/zcodewrite.py`（标注废弃，勿调用）。
如需清理历史导入会话，**必须清两个库**：
1. `cli/db.sqlite`：删 part/message/session（识别规则：`sess_` 前缀 + 总长 41 +
   uuid 版本位（第三段首位）= '5'，uuid5 派生与原生 uuid4 可靠区分）；
2. `v2/tasks-index.sqlite`：删 `tasks` 表中 `task_id NOT IN (SELECT id FROM session)`
   的僵尸行——否则 UI 历史列表残留、点开报 `fault.subscribe.sessionNotFound`。
两库操作前都先备份。2026-08-26 已按此清理（246 会话 + 199 个僵尸 task；
备份 `db.sqlite.cleanup-bak-*` / `tasks-index.sqlite.cleanup-bak-*`）。
