# Kimi Work（桌面版 Kimi 的 coding agent）会话结构详解（实机已核验）

核实基线：源码核验（@moonshot-ai/kimi-code
monorepo）+ **2026-09-09 本机实装核验**（Kimi 桌面版 3.2.6 的 "Kimi Work" 功能）。

## 0. 正名与形态（实机实锤）

- 产品名是 **Kimi Work**——Kimi 桌面版（Electron）里的 coding agent 功能，不是独立
  npm 包"kimi code"。桌面未装独立 CLI 时 **`~/.kimi-code` 不存在**，npm 源码期
  布局在本机全部不落地。
- 但内部引擎仍叫 Kimi Code：system prompt 自述 "You are Kimi Code CLI"，
  日志反复出现 "Kimi Code sessions"。
- 架构链（main.log 实测）：Kimi 桌面(Electron, `%APPDATA%\kimi-desktop`)
  → DaimonHost 守护 → **daimon runtime**（OpenClaw 族布局，目录里有
  `openclaw-shim`）→ 内嵌 kimi-code 引擎，引擎 HOME 在 daimon 数据区内。
- 数据根：`<数据盘>:\KimiData\`（`daimon-bundle\`=运行时本体，`daimon-share\`=用户数据，
  `kimi\`=下载等杂项）。

## 1. 存储布局（实机实锤）

```
<数据盘>:\KimiData\daimon-share\daimon\
├── runtime\kimi-code\home\                ← 引擎 HOME（会话正体）
│   ├── workspaces.json                    ← 工作区注册表：wd_<目录名>_<hash> → {root,name,created_at,last_opened_at}
│   ├── session_index.jsonl                ← 每会话一行 {sessionId, sessionDir, workDir} ← reader 入口
│   ├── device_id / plugins\ / migrations-effort.json
│   └── sessions\<wd slug>\<sessionId>\
│       ├── state.json                     ← 会话元数据（见 §3）
│       └── agents\main\wire.jsonl         ← 原始线协议转录（一次问答实测 32 行/249KB）
├── agents\main\
│   ├── sessions\hosted-logical\
│   │   ├── conversations.sqlite           ← 会话注册表（见 §2）
│   │   └── turn-traces.v1.json
│   └── memory\transcripts\days\<YYYY-MM-DD>\conv-*.jsonl
│                                          ← 日转录：每消息一行（见 §4）
└── skills\ tools\ plugin-packages\ openclaw-shim\ …
%APPDATA%\kimi-desktop\kimi-agent\          ← 桌面侧状态（conversation-statuses /
  context-usage / segment-id-map / created-workspaces…），只有元数据无正文
```

sessionId 形状：`conv-` + 20 位 hex（nanoid 风格）；另有 **`ctitle-` 前缀的标题
生成副作用会话**（同目录同结构，为生成标题专门跑一轮 LLM）——reader 必须按前缀排除。
conversationKey：`agent:main:main:conversation:<uuid4>`。

## 2. conversations.sqlite（注册表，读的最优入口）

三表：`conversations`（每会话一行）/ `sessions`（agent 会话指针）/ `schema_metadata`。

conversations 关键列：`agent_id, conversation_key, conversation_id(uuid4),
kernel_type='kimi-code', kernel_session_id(=conv-*), kernel_session_dir,
kernel_records_path(→wire.jsonl), workspace_path, title, title_status,
first_user_text, first_assistant_text, created_at_ms, updated_at_ms,
origin, work_tag`——**首问/首答现成**，标题、时间、kernel 路径一应俱全，
比扫 wire.jsonl 省事；正文再下钻 wire.jsonl 或日转录。

## 3. wire.jsonl（协议 1.4）与 state.json

实测行类型：`metadata(protocol_version,created_at)` / `config.update(profileName,
systemPrompt)` / `turn.prompt`（**用户输入**）/ `context.append_message`（**模型输出**）
/ `context.append_loop_event` / `llm.request` / `llm.tools_snapshot` /
`tools.register_user_tool` / `tools.set_active_tools` / `permission.set_mode` /
`usage.record`。一轮问答的膨胀主要来自 config.update 的 systemPrompt 与工具注册
（故 249KB）——reader 应只取 turn.prompt / context.append_message，
跳过 config/tools/permission 行。

state.json：`createdAt/updatedAt/title（带 <meta …> 注入前缀，读时剥离）/
isCustomTitle/agents.main.homedir/workDir` + `custom.{owner:'daimon',
sessionKind:'conversation', createdBy:'daimon-kernel-adapter',
conversationKey, workspacePath}`。

## 4. 日转录（最省事的正文口径）

`agents\main\memory\transcripts\days\<日期>\conv-*.jsonl`：**每消息一行**
`{ts, role, content, meta}`，meta 自带 `sessionId / conversationKey /
sourcePath(指回 wire.jsonl) / sourceType('kimi-code:user:text' 等) /
messageId / turnId`——daimon 的 transcript sync 每日首活跃时从 wire.jsonl 汇出。
reader 可直接吃这里（现成 role/content/time），wire.jsonl 留作兜底；
注意它归 daimon 所有，后续若混入非 kimi-work 来源需按 meta.source 过滤。

## 5. 本机实证

2026-09-09 一条测试会话（工作区=某本地项目目录）：问"你是kimi吗？"→
答"是的，我是 Kimi，由 Moonshot AI（月之暗面）开发……作为 Kimi Work 协助你"。
模型 `k2d6-agent`，maxContextTokens 262144（context-usage 状态实测）。

## 6. npm 源码期结论的修正与保留

- 「自研 minidb 持久层」结论**仅适用于独立 npm CLI 形态**；桌面 Kimi Work 内嵌
  引擎实测落盘是 **JSONL（wire.jsonl）+ sqlite（注册表）**，无 minidb 痕迹。
- `~/.kimi` 旧格式迁移（migration-legacy）在本机未出现（无该目录）。
- 写入可行性：wire.jsonl 为追加式明文 JSONL，理论上可外部追加；但 conversations.sqlite
  注册表与 daimon 内存态联动，**写入方向风险高**，先只做读取源（与 zcode 同判）。

## 7. 实装状态

**read_kimi 已实装（2026-09-09）**：入口 `session_index.jsonl`（缺失时退化扫
sessions/*/*/state.json）→ 排除 `ctitle-*` → wire.jsonl 解析（turn.prompt=用户输入
剥 `<meta>`；content.part think/text 按 stepUuid 归并 step；llm.request 取模型名；
config/tools/permission/usage 跳过；append_message role=assistant 仅在无 loop 事件时
兜底）→ cwd 取 state.json 的 custom.workspacePath。探测：paths.py 扫各盘
`<盘>:\KimiData\daimon-share\daimon\runtime\kimi-code\home`（session_index.jsonl 或
sessions\ 存在即命中），webui 支持手动绑定。未采样形态（工具调用 part、多 turn、
agents/<非 main> 子代理）以原始 JSON 保底或暂不读，实机出现后按需精化。
