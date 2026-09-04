# Gemini CLI 会话结构详解

核实基线：源码（D:\Project_github\gemini-cli-main，chatRecordingService/sessionUtils）
+ 本机实测（2026-09-03，~/.gemini/tmp/*/chats 四条会话）；读取器
`agentsync.readers.read_gemini`（第 14 家读取源）。

## 1. 存储布局

```
~/.gemini\                                    ← $GEMINI_CLI_HOME 可覆盖
├── tmp\<项目标识>\chats\session-<时间戳>-<id8>.jsonl   ← ★ 会话记录
├── history\<项目标识>\                          = 输入历史（不同步）
├── settings.json / projects.json / state.json  = 配置
└── tmp\<项目标识>\logs / memory / checkpoints   = 日志/记忆/检查点（不同步）
```

项目标识 = 工作目录名（如 D:\BI_x → bi-x；主目录 → 用户名）。

## 2. 行形状（源码 chatRecordingService）

```jsonl
{"sessionId":"<uuid>","kind":"main","startTime":"ISO","lastUpdated":"ISO"}
{"$set":{"messages":[ 初始消息快照 ]}}            ← 首个 $set 带全量初始消息
{"id":"…","timestamp":"ISO","type":"user","content":[{"text":"你好"}]}   ← 裸消息行=追加
{"$set":{"lastUpdated":"ISO"}}                   ← 其间穿插元数据更新（忽略）
```

- 模型回复 = `type:"gemini"`：`content:[{text}]` 正文、`thoughts:[{subject,text}]`
  思维链、`model`/`tokens` 元数据（本机 key 503 未采到真实回复，形状取自源码）
- 初始快照里首条 user 消息是 `<session_context>` 注入块（含 **Workspace Directories**
  列表——cwd 从这里反解）

## 3. 读取策略（read_gemini）

1. glob `tmp/*/chats/session-*.json(l)`（.json 整文件 / .jsonl 逐行都认）
2. `$set` 带 messages=列表重置；裸消息行（type=user/gemini）追加
3. user：跳过 `<session_context>` 等注入块（顺带反解 cwd），其余开 Turn
4. gemini：thoughts→reasoning 块在前、content text→text 块在后，成 Step
5. 标题=首问 [:40]；created=startTime；updated=末条消息时间

## 4. 写入配方（geminiwrite，2026-09-03 实机落盘）

- 文件：`tmp/<项目slug>/chats/session-<UTC分钟>-<id8>.jsonl`——slug=cwd basename
  小写（对齐原生项目目录，/resume 按工作区过滤）；projectHash=sha256(cwd 反斜杠原串)
- 行：元数据头（sessionId=uuid5，原生 v4 判别导入）+ $set{session_context} +
  每轮 user/gemini 裸行（gemini 的思维链进 thoughts[] 字段、正文进 content）
- 增量：按已有 user 裸行数整轮追加；无标题存储 → 标题由首问推导

## 5. 边界与待核验

- **content 块两种形态（实测坑）**：源码形状是 `[{text}]` 字典 part；但走中转站
  （第三方中转站实测）落盘为**纯字符串碎片数组**（流式分片，"是"/"的"/"，"…）。
  reader 用 `_gem_block_text` 统一拼接，两种形态都过（真回复已验证：gemini-3.7-flash
  经中转站的一句话自我介绍完整读回）
- thoughts 思维链在中转站路径未出现（thoughts=0）；映射保留（源码形状）
- 工具调用 parts 形态（functionCall 等）待真实编码会话核验
- 旧版 checkpoint.sqlite 机制与本 chats 记录并存过，现版本以 chats 为准
