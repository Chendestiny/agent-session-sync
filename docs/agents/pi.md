# Pi Agent 会话结构详解

核实基线：源码（D:\Project_github\pi-main，@earendil-works/pi-coding-agent 0.84）+
本机源码构建实跑（2026-09-03，`./pi-test.sh -p` 三条会话）；读取器
`agentsync.readers.read_pi`（第 13 家读取源）。Pi 是 minimax 的 pi-agent
运行时同源项目，但存储各自独立（~/.pi 与 ~/.minimax 互不相干）。

## 1. 存储布局

```
~/.pi\
├── agent\
│     auth.json / models-store.json     = provider 配置
│     sessions\<cwd编码>\<时间戳>_<uuid>.jsonl   ← ★ 事件流会话（v3）
└── （$PI_CODING_AGENT_SESSION_DIR 可整体覆盖会话目录）
```

- 目录编码与 dsh 同思路：`D:\t` → `--D--t--`
- 文件名：`2026-09-03T09-31-48-717Z_01a0669c-….jsonl`（时间戳_会话 uuid）

## 2. 事件流形状（实测 + 源码 types.ts）

```jsonl
{"type":"session","version":3,"id":"<uuid>","timestamp":"ISO","cwd":"D:\\t"}
{"type":"model_change","provider":"…","modelId":"…"}
{"type":"thinking_level_change","thinkingLevel":"medium"}
{"type":"message","message":{"role":"user","content":[{"type":"text","text":"…"}],"timestamp":ms}}
{"type":"message","message":{"role":"assistant","model":"…","stopReason":"stop",
  "content":[{"type":"thinking","thinking":"…"},      ← 字段是 thinking 不是 text
             {"type":"text","text":"…"},
             {"type":"toolCall","id":"tc1","name":"Bash","arguments":{…对象}}],  ← 对象非 JSON 串
  "usage":{…}}}
{"type":"message","message":{"role":"toolResult","toolCallId":"tc1","toolName":"Bash",
  "content":[{"type":"text","text":"…"}]}}
```

事件靠 parentId 串链；message.timestamp 是毫秒，事件顶层 timestamp 是 ISO。

## 3. 读取策略（read_pi）

1. glob `sessions/*/*.jsonl` → 首行 type=session 取 id/cwd/created
2. message 事件组轮：user 开 Turn；assistant 成 Step
   （thinking→reasoning 块、text→text 块、toolCall→tool_calls+tool-call 块）；
   toolResult 按 toolCallId 回挂对应 Step 的 tool_results
3. **stopReason=error 且无内容的 assistant 跳过**（实测：headless 跑 LLM 403 会落
   一条空 error 消息，不能算轮次内容）
4. 标题 = 首问 [:40]（会话文件无标题字段）；model 取 assistant 消息的 model

## 4. 写入配方（piwrite，2026-09-03 实机落盘）

- 文件：`agent/sessions/<目录编码>/<ISO时间戳>_<uuid5>.jsonl`——目录编码 = `'--' +
  cwd 去首分隔符 + [/\:]→'-' + '--'`（源码 sessionDirectoryName）；id=uuid5
  （原生 v7，版本位判别导入）
- 事件：session 头（v3）+ model_change + 每轮 user/assistant/toolResult message 事件
  （thinking 块字段=thinking、toolCall.arguments=对象——与读取器互逆）
- 增量：按已有 user 事件数整轮追加；pi 无标题存储 → 标题由首问推导
  （[source] 前缀在 pi 侧不可见，属格式限制）

## 5. 边界与待核验

- assistant 成功回复的真实数据未采到（本机 headless 走 zcode 注入的
  ANTHROPIC_BASE_URL=DeepSeek 网关被 403——网关 token 是 zcode 内部作用域，
  不能给外部工具直连）；assistant/toolCall 映射按源码类型实现，沙箱夹具覆盖，
  待用户用自己的 provider key 跑一次真对话后实机复核
- 无归档/子代理概念字段（未见于 v3 事件流）；cwd 编码规则与 dsh 的 project_key
  同族但独立实现（读取器按目录直读，不依赖编码还原）
