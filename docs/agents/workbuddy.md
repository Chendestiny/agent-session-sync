# WorkBuddy 会话结构详解

核实基线：WorkBuddy 5.3.x（Windows 本机 `~/.workbuddy`，34 会话实测）；
写入配方来自 agentctxsync 的 workbuddy 适配器（其对 5.3.13 实机验证，MIT）。
**当前状态：读取器已实现**（`readers.read_workbuddy`，作为第五家源接入 to-dsh/archive；
写入 WorkBuddy 未实现——如需可按 §5 配方实现）。

## 1. 存储布局

```
~/.workbuddy/                     ← 国内版
~/.workbuddy-ai/                  ← 国际版（WorkBuddy AI；D:\workbuddyai 仅为程序安装目录。
                                     两目录同 layout，本工具为两个独立源 workbuddy / workbuddy-ai，
                                     独立探测互不占位；读取器 read_workbuddy(home, source=…) 与
                                     workbuddywrite 通用；CLI to-workbuddy / to-workbuddy-ai；
                                     push --target 同名；防环按 uuid5 版本位各自生效）
├── workbuddy.db                  ← 会话元数据库（SQLite，sessions 表）
├── edge-sync-mapping-v2.db       ← WorkBuddy 云同步映射（⚠️ 绝对不要碰，
│                                    启动 MIGRATE 会自行登记）
├── projects/                     ← 消息内容（按工作区分目录）
│   └── <slug>/<conversationId>.jsonl
├── settings.json                 ← claw.legacyOwnerUid = 所有者 user id
└── blobs/ audit-log/ clipboard-images/ automation-backups/ …
```

**slug 规则**（WorkBuddy 自有）：cwd 盘符小写 + 其余原样 + `\`→`-`：
`C:\Users\demo\WorkBuddy\2026-08-24-15-58-47` → `c-Users-demo-WorkBuddy-2026-08-24-15-58-47`；
盘根 `E:\` → `e`（无尾横线，错了会把同一会话劈成两个文件）。

## 2. workbuddy.db sessions 表（本机实测列）

```
id, cwd, user_id, title, custom_title, status, created_at, updated_at(以上毫秒),
deleted_at(软删), is_playground, source_mode, is_background_automation, model,
expert_id, expert_locale, expert_runtime_identity, expert_marketplace,
permission_mode, last_activity_at, use_sandbox_cli, project_id,
plugin_context_json, last_user_prompt_expert_selection, mode
```

实测样例：`{id: <uuid>, cwd: C:\Users\demo\WorkBuddy\2026-08-24-15-58-47,
title: 测试模型, status: completed, created_at: 1787558330631, mode: craft,
model: custom-local:qwen3.6-35b}`。查询排空 `deleted_at IS NULL`，按
`COALESCE(updated_at, created_at)` 倒序。

## 3. JSONL 事件格式（每行一事件，毫秒时间戳）

| type | 关键字段 | 说明 |
|---|---|---|
| `message` | `{id,timestamp,type,role:user\|assistant,status,content:[{type:"input_text"\|"output_text",text}],providerData,sessionId,cwd}` | 对话消息；assistant 带 parentId 指向 user 消息 |
| `reasoning` | `{id,timestamp,rawContent:[{type:"reasoning_text",text}]}` | 思维链 |
| `function_call` | `{id,timestamp,callId,name,arguments,providerData}` | 工具调用 |
| `function_call_result` | `{id,timestamp,callId,name,status,output:{type:"text",text}}` | 工具结果 |
| `ai-title` | `{timestamp,aiTitle,sessionId,cwd}` | AI 生成标题（可多条，取首条） |
| `file-history-snapshot` | `{snapshot:{…}}` | 文件快照，读取跳过、永不写 |

## 4. 读取规则（本工具 reader，已实现并实测）

- 会话清单来自 workbuddy.db；消息文件 `projects/<slug(cwd)>/<id>.jsonl`；
- **同一会话可能存在多份文件**（项目移动/同步写入）：扫全 projects/ 取并集，
  按 (type, role, timestamp) 去重合并、时间排序（agentctxsync 踩过的坑：只读 db 指向的那份会丢新消息）；
- 典型事件序列：`message(user) → (reasoning → function_call → function_call_result)* → reasoning → message(assistant)`；
  user 开新轮；reasoning 缓冲后前置到对应 assistant 步；call/result 按 callId 归位；
- 跳过：`file-history-snapshot`、`resend-fork-notice`、role=system；
- **注入剥离（关键）**：user message 会内嵌 `<system-reminder data-role="user-context">`
  块（OS/IDE/skills 列表，实测可达 15K+ 字符），真实提问在其 `<user_query>` 标签里——
  读取时剥离注入块只留 user_query 正文（实测案例：15232 字符 → 2 字符「你好」）；
- title 回退链：sessions.title → custom_title → ai-title 事件；
- 实测：34 会话 / 2658 工具调用全部正确解析。

**已知分组边界**：WorkBuddy 默认项目目录是 `~/WorkBuddy/<时间戳>`（嵌套在主目录下），
这类 cwd 按嵌套规则不建 dsh 工作区（dsh 启动会清理嵌套记录）→ 留在未分组，属预期。

**跨版本迁移边界（workbuddy ↔ workbuddy-ai 双向实测 2026-09-11，国际版 5.5.2）**：
**两版的「任务」栏 = `is_playground` 标记位视图**（国际版：原生 pg「简单问候」pg=1 在任务栏、
pg=0 的不进，21 条导入翻转 pg=1 后任务栏即时认账；国内版：反向写入的 `[workbuddy-ai] 简单问候`
翻转 pg=1 后同样进任务列表——**两版同义，只是一个标记位，非独立存储**）。
会话正典仍是 db+JSONL 双层，两版 layout 相同全可读写；国内任务(19)列表是 UI 收敛视图。
两栏归属由 cwd 结构决定：pg 会话保持**原始单会话时间戳目录**（`~/WorkBuddy/<ts>`、
国际版 `~/WorkBuddy AI/<ts>`），任务栏可见且不产生空间分区；非 pg 会话由
workbuddywrite._import_cwd() 折叠到父目录聚合成单一空间（否则每条会话劈成独立分区；
16 条 automation 保留在 WorkBuddy 空间是正常归属，若要分区清零可整体翻 pg=1）。
（writer 按 sess.is_playground 自动区分：read_workbuddy 把 is_playground 带进 IR。）
注意 UI 空间列表可能在翻转 pg 后残留旧口径——彻底重启（MIGRATE 重跑）刷新。
playground（国内版「未分区」）会话读取需 --include-playground 显式纳入。

## 5. 写入配方（agentctxsync 实机验证过的约束）

1. JSONL：append-only；新文件首行写 `ai-title` 事件（占位标题，保证文件非空）；
   按 (role, timestamp) 去重后追加 `message`/`function_call_result` 事件；
2. db：upsert sessions 行（INSERT：status='completed'、mode='craft'、is_playground=0）；
   本机会话 UPDATE 时保留本地 cwd（防外部 cwd 把读路径指到旧副本）；
3. **可见性**：运行中写入 UI 看不到，**重启 WorkBuddy** 后其启动 MIGRATE 扫 db+projects/、
   自行登记 edge-sync-mapping-v2.db 并显示；桌面端可正常打开并续聊；
4. **cwd 必须存在**，否则打开报「工作目录可能已被重命名或删除」——写入方负责 mkdir；
5. 云同步只上传 WorkBuddy 自己产生的消息（元数据同步、内容不上云），外来会话安全；
6. 时间戳存储毫秒、规范化模型秒（/1000 读、×1000 写），保证 (session, role, timestamp) 去重键往返一致。

## 6. user id 获取（写入需要）

环境变量 `WORKBUDDY_USER_ID` → `settings.json` 的 `claw.legacyOwnerUid` → 最新一条会话的 user_id。
