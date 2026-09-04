# Cline 会话结构详解

核实基线：源码（D:\Project_github\cline-main，apps/vscode 的
openDiskConversationHistory.ts/disk.ts）+ 本机实测（2026-09-03，VS Code 1.103.2
+ Cline 4.1.17 一条真实对话）；读取器 `agentsync.readers.read_cline`（第 15 家读取源）。

## 1. 存储布局

```
%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\
├── tasks\<taskTs>\
│     ui_messages.json                 ← ★ UI 事件流（读取主源）
│     api_conversation_history.json    = API 层消息（cwd 反解用）
│     task_metadata.json               = model_usage / environment_history
│     focus_chain_taskid_*.md          = 焦点链（不同步）
├── checkpoints\                        = 文件快照（不同步）
├── cache\ settings\ state\             = 杂项
```

## 2. ui_messages 事件形状（实测）

```jsonl
{"ts":1788427457156,"say":"task","text":"hellp"}                  ← 首问开轮
{"ts":…,"say":"user_feedback","text":"我vscode已经…"}             ← 追问开轮
{"ts":…,"say":"checkpoint_created","text":""}                     ← 噪音（忽略）
{"ts":…,"say":"api_req_started","text":"{\"request\":…}"}         ← 噪音
{"ts":…,"say":"reasoning","text":"The user is asking…","partial":false}
{"ts":…,"say":"reasoning","text":"流式碎块","partial":true}       ← partial 跳过
{"ts":…,"say":"completion_result","text":"Cline 是一个…"}         ← 终答
```

api 历史首条 user 消息的环境块里有 `Working Directory (d:/<工作区>)`
——cwd 从这里反解；model 取 task_metadata.model_usage[0].model_id。

## 3. 读取策略（read_cline）

1. glob `tasks/*/ui_messages.json`
2. say=task/user_feedback 开 Turn；reasoning（非 partial）→ 当前轮首 Step 的
   reasoning 块；completion_result → 新 Step 的 text 块
3. checkpoint/api_req/resume_task 等噪音忽略；标题=首问 [:40]

## 4. 写入配方（clinewrite，2026-09-03 实机落盘）

- 任务目录：`tasks/<created_at毫秒>/`（确定性幂等映射）；**任务 id 无形状可判别
  导入 → 旁路清单 `.agentsync-imports.json`**（home 根，同 opencode 先例）
- 三件 JSON：ui_messages（task/user_feedback/reasoning/completion_result +
  conversationHistoryIndex）、api_conversation_history（首条 user 含
  `Working Directory (cwd)` 环境块——读取器反解 cwd）、task_metadata（model_usage）
- 增量：按已有开轮行数整轮追加

## 5. 边界与待核验

- 工具类 say（tool / command_execution / use_mcp_tool / file_edited…）待真实
  编码任务出现后核验映射（本机样本为纯问答）
- 归档/删除：Cline UI 删任务=删 tasks/<ts> 目录，无软删标记
- api_conversation_history 里的 `<attempt_completion>`/`[TASK RESUMPTION]`
  噪音块不进 IR（读取走 ui_messages，天然规避）
