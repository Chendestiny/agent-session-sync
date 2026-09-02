# Trae 会话结构详解（布局对齐 Cursor，待实机核验）

核实基线：本机 **Trae CN 残留**实测（`%APPDATA%\Trae CN\`，2026-09-02——已卸载，
chat index 空、无 cursorDiskKV 表、仅输入历史面包屑）；读取器
`agentsync.readers.read_trae`（只读源；第十一家，**未在本机验证过真实正文**）。

## 1. 已实测的事实（残留）

```
%APPDATA%\Trae CN\                    ← 国内版目录名带 " CN" 后缀（探测认 Trae / Trae CN 两种）
├── User\globalStorage\state.vscdb    ← ItemTable 93 键；chat.ChatSessionStore.index = {"version":…,"entries":{}}（空）
├── User\workspaceStorage\<hash>\     ← chat index 同样 entries 空
~/.trae-cn\                           ← 空壳
%LOCALAPPDATA%\Programs\Trae CN\      ← 程序本体
```

结论：卸载清空了会话数据，**真实正文格式无法从残留反推**。

## 2. 读取策略（read_trae，v1 探针）

- 复用 Cursor 的 cursorDiskKV 引擎（Trae 是 VS Code 系 fork，合理兜底）；
  表不存在时静默返回空——本机残留即此形态（0 会话，卡片显示真实状态）
- 重装 Trae 后需实机核验：真实 chat 存储是 cursorDiskKV 同形状还是
  `chat.ChatSessionStore.index` + icube 自有键（`icubeAiChat/…`），对照本篇更新
- 目录绑定（webui ⚙）规则已备：粘 `%APPDATA%\Trae CN` 或直接 state.vscdb 均可

## 3. 边界

- 本机 0 会话不是 bug——残留就这么多；有数据后以实测为准修订本文档
