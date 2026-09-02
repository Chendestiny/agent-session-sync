# OpenClaw（开源个人 AI 助手）会话结构详解

核实基线：本机 OpenClaw 实测（`~/.openclaw`，2026-09-02 从 2026.3.23-2 升级到 2026.8.2
后核实）；读取器 `agentsync.readers.read_openclaw`（只读源；个人助理型 agent）。

## 0. 版本兼容（一个读取器吃两种机器形态）

**json→sqlite 的分水岭是 v2026.7.2**（PR #98236，agent schema 4 "Sessions and
transcripts moved into SQLite"，官方 [Database schemas](
https://docs.openclaw.ai/refactor/database-first) 的 Agent schema history 表）；
迁移单向，老版本拒绝打开新 schema 库，不可降级。

| 机器形态 | 存储 | read_openclaw 数据源 |
|---|---|---|
| 旧版 ≤2026.7.1（如 2026.3.x） | 无 agent sqlite，全量 jsonl | sessions/ 活跃 jsonl + reset 快照 |
| 新版 ≥2026.7.2 | openclaw-agent.sqlite 正典 | transcript_events 为主 |
| 新版 + 迁移残留（本机现况） | sqlite + 磁盘残留快照 | 正典有的 id 以正典为准，快照只补孤儿 |

实测（2026-09-01，同一函数两个根目录对照）：旧版树（升级前备份
`local/openclaw-pre-upgrade-bak/`）85 会话/688 轮全部从 jsonl+快照读出；
新版树（live）84 会话 = 26 正典 + 58 快照。**doctor 迁移实测丢数据**：旧版 27 条
活跃 jsonl 迁入 sqlite 只有 25 条，`d178eca1`/`6188846d` 两条被丢弃且活跃文件
已移走——这两条现存仅备份；如需找回，把备份里的这两个 jsonl 拷回
sessions/ 即可被兜底路径读回（读取器层面可恢复，OpenClaw 自身是否识别未验证）。

## 1. 存储布局（新版形态；旧版只是 agent/ 下没有 sqlite，其余相同）

```
~/.openclaw/
├── agents/main/agent/openclaw-agent.sqlite   ← ★ 正典（2026.8 新）：transcript_events
│                                               (session_id, seq, event_json)；
│                                               session_windows 存会话链/展示 key
├── agents/main/sessions/                     ← 旧容器：升级迁移后活跃 jsonl 已移走，
│   ├── <uuid>.jsonl                          ←   仅剩 .reset 快照与 .bak/.deleted
│   └── <uuid>.jsonl.reset.<ISO时间戳>        ← reset 轮转快照（大量旧对话只在这）
├── memory/  workspace/(git)  skills/  extensions/  cron/  devices/  identity/
```

**升级迁移（2026.3→2026.8 实测路径）**：新版把 provider 内置拆成插件（deepseek 需
`plugins install @openclaw/deepseek-provider --accept-capabilities`，EPEM 符号链接用
mklink /J junction 手工补）；workspace 旧状态与凭证迁移要 `doctor --fix`（被计划任务
管理员权限卡住时可手工摘旧配置键/移走 workspace-state.json）；会话迁移用
`doctor --session-sqlite import`（不走服务门禁，26 会话/460 事件迁入 SQLite，
event_json 与 jsonl 行同形）。

## 2. 事件形态（jsonl 行与 SQLite event_json 同形）

```json
{"type":"session","version":3,"id":"<uuid>","timestamp":"…","cwd":"…"}
{"type":"model_change","id":"…","timestamp":"…","provider":"…","modelId":"…"}
{"type":"message","id":"b89a80bb","parentId":"…","timestamp":"…",
 "message":{"role":"user|assistant","content":[
    {"type":"text","text":"…"},
    {"type":"thinking","thinking":"…"},
    {"type":"toolCall","id":"call_x","name":"exec","arguments":{…}}]}}
{"type":"message","message":{"role":"toolResult","toolCallId":"call_x","toolName":"exec",
 "content":[{"type":"text","text":"…"}],"details":{"status":"completed","exitCode":0,…}}}
```

- user 行开轮、assistant 行成 step（thinking→reasoning、toolCall→tool_calls）
- **工具结果是独立 role=toolResult 行**，按 toolCallId 挂回发起 step；
  `details.status` 非 completed 记为错误
- 首问常带注入前缀（`[Tue … GMT+8]` 时间戳、`Sender (untrusted metadata)` 元数据块、
  `[Subagent Context]` 包装、`System:` 执行回显、`/new` 控制条、`OpenClaw runtime
  context` 内部事件行）——prompt 原样保留，标题推导时剥离（见 §3）

## 3. 读取策略（read_openclaw，双版本兼容）

1. 正典：`openclaw-agent.sqlite` 的 transcript_events 按 (session_id, seq) 分组解析
   （sqlite 缺失/不可读时静默跳过——纯旧版机器走第 2 步）
2. 兜底：sessions/ 活跃 jsonl + reset 快照——只补正典没有的 uuid（孤儿），同 uuid
   多份取字典序最新；与 SQLite 已有 id 去重（实测升级后 84 = 26 正典 + 58 快照；
   纯旧版树 85 = 27 活跃 + 58 快照，无 sqlite 依赖）
3. selftest 双形态全覆盖：jsonl-only（旧版）与 sqlite+jsonl 共存（新版，含同 id
   去重断言）

## 3.5 标题推导与子代理排除

- **标题**：OpenClaw 无标题字段（session_windows.session_key 是展示键非标题），
  `_openclaw_title` 逐轮剥注入前缀后取首个非空行 [:40]；/new 控制条、System 回显、
  内部事件行当空问跳过看下一轮；整场无有效提问给占位「（无有效提问）」
- **子代理默认排除**（include_subagents=False，对齐 zcode/codex/dsh）：判定标记 =
  首问含 `[Subagent Context] You are running as a subagent`（实测 84 条中 16 条）。
  **坑**：转发的子代理完成事件（`source: subagent`、`OpenClaw runtime context
  (internal)`）会出现在主会话里，不能当子代理标记，否则误伤主会话
- webui 展示口径传 include_subagents=True，子代理带 🤖 徽章可筛选（2026-09-01 起）

## 4. 边界

- 标题靠首问推导（见 §3.5），同模板任务仍可能同题（如多条「你好」）——如需定制用
  webui titles.json 叠加
- 无归档/回收站概念（reset/删除即文件改名留存）
- 子代理会话与主会话同库混存，靠首问标记区分（见 §3.5）
- gateway 是 Windows 计划任务服务（schtasks 管理需管理员）；`gateway run` 可前台跑
