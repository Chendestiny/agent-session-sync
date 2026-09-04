# GitHub Copilot 会话结构详解（占位：待逆向）

核实基线：路径探测已命中本机（VS Code workspaceStorage 存在 chatSessions 目录）。
GitHub Copilot 的 agent 会话 = **VS Code 本体的 chat 会话存储**，非独立应用。

## 1. 存储布局（已知）

```
%APPDATA%\Code\User\
├── workspaceStorage\<hash>\
│     workspace.json                     = 该存储区对应的工作区 URI
│     chatSessions\<sessionId>.json       ← ★ chat 会话（含 Copilot agent 对话）
└── globalStorage\state.vscdb             = 杂项状态（chat 索引等键）
```

- 探测规则（paths.py）：仅在 workspaceStorage 下扫到 `*/chatSessions` 目录才算
  装了——避免把"装了 VS Code"误判成"用了 Copilot chat"
- 边界注意：与 Cline 共用 VS Code 布局但目录不同（Cline 在 globalStorage/
  saoudrizwan.claude-dev）；Cursor/Trae 等 fork 的 chatSessions 不在探测范围

## 2. 接入计划

用本机已有的 chatSessions 样本逆向 JSON 形状（VS Code chat session 格式，
含 requester/响应 parts）→ read_copilot；写入可行性待形状确认后评估。
