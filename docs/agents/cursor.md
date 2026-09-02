# Cursor 会话结构详解

核实基线：本机 Cursor 实测（`%APPDATA%\Cursor\User\globalStorage\state.vscdb`，2026-09-02；
743 行 cursorDiskKV → 5 会话）；读取器 `agentsync.readers.read_cursor`（只读源；第十家）。

## 1. 存储布局

```
%APPDATA%\Cursor\User\
├── globalStorage\state.vscdb      ← ★ 会话正典（sqlite，表 cursorDiskKV）
│     composerData:<cid>            = 会话头：createdAt(毫秒)、isArchived
│     bubbleId:<cid>:<bid>          = 消息行（关联只靠键前缀）
│     checkpointId / codeBlockDiff / messageRequestContext … = 上下文件（不同步）
└── workspaceStorage\<hash>\        = workspace.json(folder URI) + 每工作区杂项
```

## 2. 关键形状（实测）

- **conversationMap 恒空壳**（19/19 实测全空）——composer 与 bubble 的关联只能靠
  `bubbleId:<cid>:` 键前缀，别信 conversationMap
- bubble：`type` **1=user / 2=assistant**（26/408 实测文本对照）、`text`、
  `createdAt`（ISO 字符串）、`workspaceUris: ["file:///d%3A/BI_frontend"]`（URL 编码，
  反解出 cwd）、`toolFormerData`（dict：`name/tool`、`params/rawArgs`、`result`、
  `toolCallId`——一个 bubble 一次调用，调用与结果同体）
- composer 头：`createdAt` 毫秒、`isArchived`（归档位，**默认排除**，对齐
  zcode/hermes 口径）；`text`/`name` 实测为空——标题只能从首问推导
- `allThinkingBlocks` 实测 0 命中（有字段无数据），暂不映射 reasoning

## 3. 读取策略（read_cursor）

1. cursorDiskKV 全表 → composerData/bubbleId 按前缀分桶
2. bubble 按 createdAt 排序组轮：type1 开轮，type2 成 step（text + toolFormerData
   → tool_calls/tool_results 配对）
3. cwd 取首个带 workspaceUris 的 bubble 反解（`file:///` 前缀 + unquote）
4. 标题 = 首问剥 `@文件` 引用与盘符路径前缀后 [:40]（Cursor 习惯 `@src/xx.vue 提问`）

## 4. 边界

- workspaceStorage 里另有旧版 `chat.ChatSessionStore.index`（legacy chat），实测当前
  会话全在 globalStorage，v1 不读 legacy
- 无 model 字段可映射（IR model=None）
- 会话删除 = 行消失，无回收站概念
