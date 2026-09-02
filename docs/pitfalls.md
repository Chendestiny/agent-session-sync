# 踩坑总录（都已修进代码）

按家分组。每条含：坑 → 现象 → 修复。新坑照此格式追加，README 不再维护此表。

## dsh（DeepSeek Harness）

| 坑 | 现象 | 修复 |
|---|---|---|
| `session/imported` 缺顶层 `ignorable:true` | 整条日志被拒、标题回退、全进未分组 | 写入器补标记；校验器内置宿主事件词汇表规则 |
| workspace 记录缺 `createdAt/updatedAt` | dsh 启动直接失败（Zod 校验） | apply_attach 写全 5 键；schema 记入 docs/agents/dsh.md |
| 嵌套路径工作区 | dsh 启动时清理嵌套记录 | attach 与 dsh 行为一致：嵌套 cwd 不建组 |
| projcache 无 title 行 | 列表不显示标题（显示工作区名），点开才有 | 侧栏标题读投影缓存而非日志；attach-dsh 同时回填 title 行 |
| projcache identity 失配但 title 一致 | cachedSnapshot 整条拒绝 → 侧栏无标题且无法自愈 | plan_title_backfill：title 与 identity 双一致才跳过，失配即重建（回归测试覆盖） |
| 会话 id 命中归档列表 | 数据四层全对但侧栏不渲染（删除→同 id 复活即隐身） | prune/删除同步清 archivedSessionIds；复活历史归档需手工移除 id |
| webui 回收站三口径不一致 | 卡片数 `~/.trash-dsh` 目录（prune 移入 ≠ 归档）≠ 行级 🗑（归档∩磁盘）≠ workspace 名单（含死 id）——同一概念三个数 | 统一归档名单口径：卡片=名单数、行级=名单标记、`read_dsh` 补归档排除（对齐 zcode/hermes「归档不同步」）；历史死 id 退出 dsh 后清（先备份，2026-09-01 清 30 条） |
| 运行中写 workspace/projcache | dsh 退出时内存回写覆盖，造成「成功但看不见」假象 | attach/prune 硬前提：dsh 完全退出后执行 |

## codex CLI

| 坑 | 现象 | 修复 |
|---|---|---|
| resume 列表不认外来 rollout | 文件落位但 picker 只显示原生会话 | 0.137 的列表读 `~/.codex/state_N.sqlite` 的 **threads 索引**而非扫描文件；写入器落盘时同步登记 threads 行（模板取自真库显示行：`\\?\` 前缀 cwd、字符串秒时间戳） |
| 极简 session_meta | 同上（即使登记了索引，续聊解析也可能缺上下文） | meta 对齐原生字段集：originator/cli_version/source/thread_source/base_instructions（从本机原生 rollout 动态抄） |
| **`<` 开头提问被当注入过滤（已知未修）** | 以 `<el-button`、`<template` 等开头的真实代码提问整轮丢失（本机实测已丢 3 轮） | 触发条件：用户消息以 `<` 开头且非已知注入标签。修法：改为已知注入标签白名单（`<user_instructions>` `<environment_context>` `<permissions>` `<project_layout>` `<turn_aborted>`），其余 `<` 开头照收 |
| rollout 无标题，首问多为贴入路径 | 同项目多会话显示标题撞车（实测 9×「D:\BI_frontend\src\views\…」开头完全一致；threads 索引标题也是首问截断，救不了） | `read_codex` 标题剥盘符路径前缀取真问题；webui 显示层再叠加仓库根 titles.json 人工标题（`SESSION_SYNC_TITLES` 可改指；实测 86/86 覆盖后重复组 7→0） |

## claude code

| 坑 | 现象 | 修复 |
|---|---|---|
| thinking 块带 signature 校验 | 外来 thinking 块有被拒风险 | 写入器跳过 thinking（文本与工具往返完整保留） |
| 坏 shim | `claude` 报 claude.exe 不存在 | 本机可用入口 `~\bin\claude.exe`；根治 = 删 `C:\Program Files\nodejs` 下三个 stale shim（需管理员） |
| 导入会话无标记可辨（五家+中转全版本） | 反向写入器落的会话与原生同形状，reader 照读 → A→B→A 环形复制（实测：opencode 会话经 claude 中转在 dsh 出现第二副本；2026-09-02 复查发现仅 claude 有防护） | 四家写入器 sessionId 是 uuid5 且原生全不是 v5（claude/v4、codex/v7、workbuddy/v4、hermes/时间戳串）——`_is_agentsync_uuid5` 统一判别默认跳过；**opencode 例外**（见下行）；**dsh 中转**：read_dsh 默认排除 `import-*`（副本当源外流=二次成环），webui/prune --pick 显式放开。selftest 防回归断言 |
| **opencode 桌面版原生 id 也是 uuidv5（形状判别翻车）** | 2026-09-02 按 uuid5 版本位排除导入后，桌面版（ai.opencode.desktop）9 条原生会话全被误杀（webui 只剩 1 条）——桌面原生 id 就是 uuidv5 形状，与写入器铸的同形 | opencode 改**旁路清单**：写入器 apply 后登记数据根 `.agentsync-imports.json`，读取器按清单排除（`_oc_import_ids`）；历史测试导入已种子登记。selftest 回归：「桌面版 uuidv5 原生会话不被误杀」 |

## hermes

| 坑 | 现象 | 修复 |
|---|---|---|
| sessions 计数列为空 | 列表显示「0条消息，未展开」（消息行在库、格式也对） | 列表 UI 读 `message_count/tool_call_count/source` 计数列；写入器创建时填、追加后按 messages 实测值刷新 |
| `sessions.source` NOT NULL 无默认 | INSERT 直接 IntegrityError | 写入 `source='cli'`（agentctxsync 同款坑） |

## opencode

| 坑 | 现象 | 修复 |
|---|---|---|
| 缺 `path` 派生列 | db 三表全对但桌面列表不显示 | `path = directory 去盘符`（`C:/Users/x` → `Users/x`，真库实证） |
| directory 与当前项目不匹配 | 补了 path 仍看不到 / 全堆在 Default Project | 分区规则：cwd 真实存在 → 会话落自己的分区（找/建 project；桌面自建的分区自动复用）；缺失 → 兜底默认上下文（global）。默认上下文取 global 分区 time_created 最新会话的 directory（不能用 time_updated——force 重写会推高它造成漂移） |
| session.model 裸字符串 | opencode JSON 解析 model 列会炸 | 必须写 JSON：`{"id","providerID":"opencode","variant":"default"}` |
| force 重写不清旧消息 | 确定性 uuid5 id 撞 message 主键 | force=create 时先 DELETE 旧 message/part 再写 |
| **缺事件流（1.18 桌面事件溯源）** | 点开会话报 `Expected a string starting with "msg", got "{messageID}"` | 桌面渲染读 `event`/`event_sequence` 表而非直查 message；写入器补最小事件流（session.created → message.updated× → message.part.updated× → session.updated，directory 用反斜杠）；agentctxsync 配方（1.17 时代）无此表 |
| **消息形状不完整** | 点开会话报 `Missing key at [0]["info"]["agent"]` | message.data 与事件 info 必须是完整原生形状：user 带 agent/model/summary；assistant 带 parentID/mode/path/cost/tokens/modelID/finish——写入器按原生模板补齐 |
| part 缺 time / tool state 不全 | `Missing parts[0]["time"]` → `state["title"]` → `Expected ToolState` 层层报错 | part 一律带 `time{start,end}`；tool state 六键齐（status/input(对象)/output/metadata/title/time）；**ToolState 是按 status 的可辨识联合**，failed 分支形状不同——失败调用统一按 completed 形状写（空输出标 `(failed)`） |
| **桌面"只有 N 条" vs 全局 M 条（2026-09-01 惨案）** | webui/reader 报 45，用户桌面只见 12；误把"某分区恰好 12 条"当作用户的那 12 条 → 删错了集合，用户可见项目全空 | 桌面按**项目分区**显示（当前上下文），其他 cwd 的会话（CLI/自动化产生）在库但不在视野——数量差是视野差不是丢数据。**判定"哪批是用户的"必须以用户念出的标题为准，数字巧合（12=12）不能当证据**；正确流程：让用户报项目路径+标题 → 反查 id 集合 → dry-run 打印保留清单核对 → 才动手 |
| **行级删除的安全性（还原实验证实）** | 删 33 条后桌面"全没了"，疑似缓存坏 | 其实桌面**如实反映库**：当时删的恰好是用户两个可见项目（Default Project + BI_frontend）的全部会话；还原备份后立即恢复。行级删除配方：message/part 按 session_id、event/event_sequence 按 **aggregate_id**（不是 session_id）、session 按 id，事务内 + 全库备份 + 退出应用 |

## workbuddy

| 坑 | 现象 | 修复 |
|---|---|---|
| cwd 缺失的会话 | WorkBuddy 拒开 | 写入前 `os.path.isdir` 校验，缺失兜底主目录（agentctxsync 同款） |
| edge-sync-mapping-v2.db | 误碰可能破坏云同步映射 | 绝对不碰（读取器/写入器都不打开它） |
| **force 重写仍追加（已知未修）** | `--force` 后 jsonl 内容翻倍（沙箱实测 1→2 条提问） | 触发条件：人工加 `--force`；create/append 路径无恙。修法：force 路径截断重写（同 opencode 先清后写） |

## zcode（只读，教训存档）

| 坑 | 现象 | 修复 |
|---|---|---|
| 向 zcode 写入会话 | 时间显示异常（旧会话显示 1 分钟前）、部分会话渲染空白 | **方向整体移除**（zcode 只出不进）；已导入 246 会话已清理（识别规则：sess_+uuid5 版本位=5，备份 db.sqlite.cleanup-bak-*） |
| 写入每会话备份一次 | 一次导入百余个全量备份（23GB） | 历史教训：改每运行一次；现已随方向移除作废 |
| **UI「删除」≠ 库里删除（回收站泄漏根因）** | 用户在 zcode 删掉的会话（如「任重道远」）仍被同步到 dsh | 真相（0.16.5 实测）：删除按钮调 RPC `zcode-task.archiveTask`，标记打在 `~/.zcode/v2/tasks-index.sqlite` 的 tasks 表（`archived=1`/`deleted=1`），db.sqlite 的 session/message **完全不动**，`time_archived` 列形同虚设。修复：read_zcode 双机制排除（time_archived + tasks-index 联查，读不到则不排除），include_archived=True 全放出审计；历史已导入的成了孤儿 → prune 清理 |
| **大会话全量写入 = 超上下文黑屏报废（2026-09-01 实测）** | 124 轮会话（**估算 163 万 tokens**）追加进 zcode 后点开即黑屏；手动压缩卡死、发消息卡在"压缩上下文"、重开仍黑屏——会话报废 | resume 时 zcode 试图全量装载/压缩。正确姿势（已验证）：`trim_turns` 三层裁剪（锚点+尾部+中段摘要，对齐 to-dsh --budget 哲学）→ **新建会话**（87k tokens / 8 轮可续聊），旧会话回滚原状。形状注意（追加/新建都适用）：assistant `parentID` 必须指向轮内 user 消息、assistant `anchor` 必须为 null（对齐原生，旧 zcodewrite 正是栽在这两处） |
| **裁剪版也黑屏：v2 注册表协同才是真根因（二连败定论）** | 87k tokens/8 轮的裁剪版新会话仍黑屏；发消息后模型"重新思考"——历史完全没进上下文 | zcode v2 的渲染与上下文装配靠一排注册表协同：`session_entry`（runtime 事件/checkpoint）、`session_input`（输入晋升链→promoted_message_id）、`turn_usage`、`tool_usage`、`input_history`、`model_usage`、v2 `tasks-index`——外部只写 session/message/part **进不了读取路径**（列表见标题、打开黑屏、上下文为空三者同源）。**定论：外部写 zcode db 通道彻底关闭**；带上下文进 zcode 的正确姿势 = 交接摘要 markdown（`local/zcode-交接摘要.md` 模式）。清残留坑：session 表主键是 `id` 而非 `session_id`，按列名扫描会漏删主行——空壳会话仍列表可见且点开黑屏，必须连主行和 tasks-index 一起删 |

## openclaw

| 坑 | 现象 | 修复 |
|---|---|---|
| **首问注入前缀污染标题** | webui 标题全是 `Sender (untrusted metadata):\n```json…`、`[Tue … GMT+8]`、`A new session was started…`（OpenClaw 无标题字段，Control UI 把用户输入包在元数据块后） | `_openclaw_title` 逐轮剥前缀（Sender 块/日期戳/[Subagent Task] 包装/盘符路径），控制条/System 回显/内部事件行当空问跳过看下一轮，整场无有效提问给占位「（无有效提问）」（2026-09-01 实测 84 条全治） |
| **子代理标记误伤主会话** | 用 `source: subagent` 判子代理会把主会话误判——转发的子代理完成事件（`OpenClaw runtime context (internal)`）出现在主会话轮里 | 判定只用首问 `[Subagent Context] You are running as a subagent`（84 中 16 条）；完成事件行在标题迭代里按控制行跳过 |
| **doctor 迁移丢活跃会话** | 2026.3→2026.8 升级 `doctor --session-sqlite import` 后 27 条活跃 jsonl 只迁入 25 条（`d178eca1`/`6188846d` 被丢，且原文件已移走） | 读取器双容器天然兜底：把备份里的 jsonl 拷回 sessions/ 即可读回（升级前备份在 `local/openclaw-pre-upgrade-bak/`） |

## cursor / trae

| 坑 | 现象 | 修复 |
|---|---|---|
| **conversationMap 是空壳** | composerData 的 conversationMap 字段实测 19/19 全空，按它组对话全丢 | 关联只靠 `bubbleId:<cid>:` 键前缀分桶，字段不信 |
| **标题/时间字段全空** | composer 的 text/name 为空、bubble 无 model | 标题=首问剥 `@文件` 引用与盘符路径前缀；model=None |
| **Trae 国内版目录名带 CN 后缀** | 探测 `%APPDATA%\Trae` 找不到（实为 `Trae CN`） | detect 两个名字都认；webui ⚙ 绑定规则同步 |
| **浏览器选不了绝对路径** | 网页 file/webkitdirectory 选择器出于安全不返回绝对路径，"选完自动绑定"做不到 | 本地服务弹原生对话框：`POST /api/pick-folder`（tkinter askdirectory）→ 选完自动校验保存；网页选择器降级为预填文件夹名 |

## 跨家通用

| 坑 | 现象 | 修复 |
|---|---|---|
| 双向同步两边列表污染 | 同一会话两边各一份，续聊即分叉 | 产品决策：单向归一（A→C→B）；目标侧 id=uuid5 幂等，同源重推不重复 |
| **事件时间压平到会话创建时间** | 跨多天的会话导入后所有事件/轮都标创建日（如 56 轮全显示 08-18），侧栏日期与真实活跃时间不符 | 根因三连：IR 的 Turn 无时间字段 + zcode 读取器没 SELECT time_created + dsh 写入器统一用 createdAt。修复：Turn.time（ms，0=未知回退）+ 七家 reader 传轮开始时间 + 六写入器优先用 turn.time；force 重写即还原真实分布 |
| **C 库往返丢 Turn.time（压平复发链）** | pull 进 C 再 push 出的会话，轮次时间又退回 fallback（Turn.time 修复只穿了 readers/writers，漏了序列化层） | store.py 的 session_to_dict/from_dict 补 `time` 字段（旧 C 文件缺 key 回退 0）；selftest 9.1 往返断言防回归；webui 的 /api/session 走同一序列化 |
| **dsh 子代理会话虚增对账/外流** | 六源总数对 dsh 数差一百多；pull/to-X 带 dsh 源时把内部委派会话搬出家族 | dsh 每次 agent 委派单开一个会话目录（header `origin=subagent`+`parentSession`；侧栏隐藏但磁盘全在）。read_dsh 从 header 捕获并默认排除（对齐 zcode parent_id / codex 过滤口径），`include_subagents=True` 为审计口径；webui 卡片拆分「导入/原生/子代理」、列表 🤖 徽章 |
| 各家 CLI 的运行中写入 | SQLite 内存回写/缓存导致看不见或被覆盖 | to-hermes/to-opencode/to-workbuddy 建议目标应用退出后执行；文件型目标（codex/claude/dsh 会话文件）随时可写 |
| **webui 导入徽章对 claude/opencode 失明** | 写进这两家的导入会话在 serve 卡片/列表上看不到（导入数恒 0），dsh/codex/hermes/workbuddy 却正常 | 根因：`_display_sessions` 展示口径只给四家传了 `include_imports=True`，claude 走默认 reader 且 `read_claude` 连开关都没有（无条件跳 uuid5）。修复：`read_claude(dir, include_imports=False)` 加参 + webui 补 claude/opencode 分支 |
| **缺增量基准的「源×目标」格首跑=全量** | 对从未同步过的组合跑 `--scope inc` 按全部历史处理：弹 `--confirm-history` 闸且整源灌进目标 | 该格所在目标根 `.agentsync-state.json` 无该源水位即首跑。regtest 对缺基准格自动预置到探针时刻-1ms（15 分钟回看内通常只剩探针自身）；手工场景要么先跑一次全量确认，要么 `--session` 点名 |
| **回归探针误选活跃会话** | 拿「该源最新会话」当探针会选中仍在更新的对话（如正在跑测试的会话，几十轮且持续变化），写进目标的只是即时快照 | regtest 探针只取「最新且已稳定」的会话：最后更新距今 ≤10 分钟视为活跃跳过（全源都活跃才回退取最新） |
