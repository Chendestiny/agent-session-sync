# MiniMax Code 会话结构详解

核实基线：本机 MiniMax Code 实测（`~/.minimax`，2026-09-03；v2 迁移后 runtime-state.sqlite，
内置 4 agent 引导壳 + 1 条真实会话）；读取器 `agentsync.readers.read_minimax`、
写入器 `agentsync.minimaxwrite`（第 12 家读取源 + 第 7 家写入目标，**读写均已实机验收**）。

## 1. 存储布局

```
~/.minimax\
├── v2\sqlite\runtime-state.sqlite   ← ★ v2 正典（注册表 + 列存投影 + 消息行）
│     local_runtime_sessions         = 会话头（session_id/title/workspace_dir/
│                                       created_at_ms/archived/parent_session_id …）
│     local_runtime_message_rows     = 消息（data_json 内 msg_content/thinking_content）
│     local_runtime_*_fts / turn_*   = 全文索引与轮次事件（不同步）
│     agents                         = 内置 agent 定义（mavis/explore/worker/verifier）
├── sessions\mvs_<hex>\workspace\    ← 旧版残留（实测只剩空 workspace 壳，不读）
└── config.yaml / plugins / memory … = 配置（无关）
```

## 2. 关键形状（实测）

- **session_id 形如 `mvs_<32hex>`**；真实用户会话 `session_type='branch'`，
  内置 agent（mavis/explore/worker/verifier）各有一个 `session_type='root'`、
  title='Main' 的引导壳——greeting turn 全部 failed 且**没有消息行**，reader 按
  「无消息即不产出」自然滤掉
- 消息行：`role`（user/assistant）、`turn_id`（轮分组 uuid）、`created_at_ms`、
  `data_json`：`msg_content`（正文；user 问题与 assistant 回答同字段）、
  `thinking_content`（assistant 思维链）、`msg_type`（1=文本，其余待核验）
- 会话头列存字段即真相（`record_json` 冗余且可能滞后，不读）；
  `archived=1` 归档、`parent_session_id` 非空 = 子代理分支（均默认排除）
- `workspace_dir` 即 cwd（列存直接给绝对路径，无需反解）

## 3. 读取策略（read_minimax）

1. sessions 头全表读入 → 按 archived / parent 过滤
2. 消息行按自增 `id`（插入序）读入、按 session_id 分桶
3. 组轮：user 开 Turn（prompt=msg_content），assistant 在当前 Turn 追加 Step
   （thinking_content → reasoning 块在前、msg_content → text 块在后）
4. updated_at 取列存 `updated_at_ms`，缺省回退消息最大时间

## 4. 写入配方（minimaxwrite，2026-09-03 实机验收）

dsh 会话「修复文件详情接口字段」经 `local/mm-import-test.py` 配方写入后，MiniMax Code
UI 验收通过（会话可见、可打开）。固化要点：

- **id**：`mvs_ + uuid5(NS, "minimax:<source>:<source_id>").hex`（原生 v4，版本位判别导入）；
  命名串带 minimax 前缀是对实机试验的历史对齐
- **sessions 行**：`columnar_version=3` 必须——项目计数触发器只认 v3；
  `record_json` 同步冗余一份；title 加 `[source]` 前缀
- **项目分区**：INSERT 触发器按 `project_workspace_dir` 自动找/建
  `local_runtime_projects` 行，但不回填 `sessions.project_id`——须手动查回填
- **消息**：`local_runtime_message_rows`，turn_id = uuid5(NS, "<sid>:turn:<全局轮号>")
  ——全局轮号保证追加轮不与首轮撞 id（幂等追加按 turn_id 判缺失）
- **簿记**：turn_ingress / turn_ingress_sequences / query_view_states 每轮一行
  （completed）；session_agent_state 每会话一行（UNIQUE，末轮覆盖）；
  sessions_fts 无触发器，手动补行
- **安全**：真库写入前守卫 MiniMax Code 进程（在跑即拒绝）；每次 apply 自动备份
  db 三件套（`.agentsync-bak-<ts>`）
- **已知限制**：工具调用落为文本摘要（`[工具调用 X]…[工具结果]…`）——原生工具消息
  形状待实机出现带工具的会话后核验升级

## 5. 边界与待核验

- 工具调用原生形态未核验（见上）；msg_content 理论上可能是分块数组（已做兜底），实测均为字符串
- 非 Windows 平台的存储根待核验（推测同为 `~/.minimax`）
- 另有 4 张占位卡（mimo/kimi/grok/copilot）：探测与 ⚙ 手绑已留；
  pi/gemini/cline 已升读取源（见各自分册），
  grok 仓库已核（~/.grok/sessions/<cwd编码>/<uuid7>/ 的 summary.json + updates.jsonl
  正典 + chat_history.jsonl，JSONL 明文读写双高可行），gemini/cline 仓库到位后核验
