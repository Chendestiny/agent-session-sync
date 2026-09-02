# zcode 会话结构详解

核实基线：zcode 0.16.3（Windows）；db 位于 `~/.zcode/cli/db/db.sqlite`（WAL 模式）。
zcode 的三表结构与 opencode 同族（agentctxsync 的 opencode 适配器即同款 schema，id 前缀差异 `sess_` vs `ses_`）。

## 1. 存储总览

```
~/.zcode/cli/
├── db/db.sqlite          ← 会话权威库（核心三表 + 注册表族，见 §1.1）
├── rollout/model-io-sess_*.jsonl   ← 模型 IO 原始流（每会话一个，删会话要连带）
├── agents/sess_*/        ← 子代理转写（独立文件，不在 db 主表）
├── artifacts/sess_*/     ← 会话产物
├── exec/sess_*/          ← 执行现场
├── exec/bash-startup/sess_*/      ← bash 启动快照（同 id 关联）
├── image-cache/sess_*/   ← 图片缓存
└── memories/projects/    ← 项目级记忆（不随会话删除）

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

### 1.1 完整关联地图（2026-09-01 参考两个第三方清理工具补全）

参考 [klopoikkm/zcode-session-manager](https://github.com/klopoikkm/zcode-session-manager)
与 [aapplle/zcode-session-manager-fix](https://github.com/aapplle/zcode-session-manager-fix)
（两者均为浏览/删除工具，**都不敢创建会话**——侧证外部写入此路不通），本机核实 15 张表全部在位：

- **db.sqlite 里按会话 id 级联的表**（删除/对账时一个都不能漏）：核心三表
  `session`/`message`/`part` + 注册表族 `session_entry`/`session_input`/`session_target`
  + 计量族 `tool_usage`/`turn_usage`/`model_usage`/`input_history` + 任务链
  `todo`/`session_task_link` + `workflow_run`/`workflow_activity`/`workflow_event`
- **磁盘关联**（目录/文件名即会话 id）：`agents/`、`artifacts/`、`exec/`、
  `exec/bash-startup/`、`image-cache/`、`rollout/model-io-<id>.jsonl`
- **tasks-index.sqlite 四表**：`tasks`（主索引）+ `task_group_view_node_orders`
  （`node_key` 是 JSON 数组 `[cwd, 会话id]`，清理须解析后按 id 精确匹配，防前缀相近误删）
  + `automation_runs` + `off_peak_tasks`；索引库被占用（zcode 运行中）时清理会失败，需退出后补做
- 只清 db 不清磁盘会留死目录残留（本机实测逮到 1 例：exec×2 + model-io 各一份）

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

## 8. 写入方向（已废弃，2026-08-26；2026-09-01 二次实验定论）

曾实现过 IR → zcode 行写入（uuid5 幂等、进程检测、备份、单事务），实测暴露两类问题后
**整体移除（zcode 只出不进）**：

- 导入会话 time_updated 异常（旧会话显示「1 分钟前」）——客户端对时间字段有额外处理；
- 部分会话渲染空白——客户端读取行时有本工具未完全复刻的字段/状态依赖。

### 2026-09-01 二次实验（两连败，根因落定）

带着 Turn.time 修复与原生形状对齐（assistant `parentID`→轮内 user 消息、`anchor`=null）重试了两种姿势：

1. **全量追加**（124 轮，估算 163 万 tokens）→ resume 超上下文，压缩卡死、黑屏、会话报废；
2. **裁剪新建**（trim_turns 87k tokens / 8 轮）→ 仍黑屏，且发消息后模型"重新思考"（历史未进上下文）。

**根因（v2 注册表协同架构）**：zcode 打开会话时的渲染与上下文装配不止读 session/message/part，
还依赖一排注册表：`session_entry`（runtime 事件/checkpoint）、`session_input`（输入晋升链，
`promoted_message_id`）、`turn_usage`、`tool_usage`、`input_history`、`model_usage`、
v2 `tasks-index`。客户端自己的每次写入都会过这排表；外部写入进不了读取路径——
列表见标题、打开黑屏、上下文为空，三者同源。逆完整套注册表成本极高且随版本升级即碎，
**定论：外部写 zcode db 通道彻底关闭**。

### 把会话带进 zcode 的正确姿势：交接摘要

上下文靠文档传递，不靠数据库灌注（与 CLAUDE.md/AGENTS.md 同一哲学）：

```bash
python sync.py archive --source dsh --session <id子串> --apply   # 单会话导出 Markdown
```

导出的 Markdown（或项目级 `local/zcode-交接摘要.md` 式摘要）贴进 zcode 新会话即可续聊。

### 清理残留（如重蹈覆辙）

**必须清两个库 + 主行**（历史识别规则：`sess_` 前缀 + 总长 41 + uuid 版本位=uuid5 派生）：
1. `cli/db.sqlite`：按 `session_id` 列扫表清 message/part/session_entry/... 之外，
   **别忘了 session 表本身（主键是 `id` 不是 `session_id`，按列名扫描会漏）**——漏删主行
   会留"列表可见、点开黑屏"的空壳；zcode 重开时还会从主行重新注册 tasks-index；
2. `v2/tasks-index.sqlite`：连 `tasks` 行一起删。
两库操作前都先备份；zcode 必须完全退出。2026-08-26 曾按旧规则清理（246 会话 + 199 僵尸 task）。
