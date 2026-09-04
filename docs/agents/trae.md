# Trae 会话结构详解（CN 版实机核验：正文库自加密，读取被阻断）

核实基线：本机 **Trae CN 重装后实测**（`%APPDATA%\Trae CN\`，2026-09-03，真实
工作区对话一条）；读取器 `agentsync.readers.read_trae`（只读源；第十一家，
当前对本机返回 0 会话——原因见下，不是 bug）。

## 1. 实测存储布局（重装后有真实对话）

```
%APPDATA%\Trae CN\
├── ModularData\ai-agent\
│     database.db (+ -wal/-shm)     ← ★ 会话正典：整库自加密（6.7MB 全密文，
│                                     无 "SQLite format 3" 头 → 非标准 SQLCipher，
│                                     WAL 帧亦密文；你好/会话 id 均搜不到明文）
│     snapshot\<sessionId>\v2\.git\  = Agent 文件快照（checkpoint，非聊天）
│     sandbox / hooks_env / …        = 执行环境
├── User\globalStorage\state.vscdb   ← ItemTable：draft:session:<id>:code（输入草稿）、
│                                     ai-chat-v2.lastActiveSessionId、agent 配置
├── User\workspaceStorage\<hash>\    ← workspace.json（folder URI）+ ItemTable：
│       memento/icube-ai-agent-storage（会话清单）、icube-ai-agent-storage-input-history
│       （输入历史，明文可见「你好」）、ai-chat.chatQueryCompletion.v2.<id>
└── logs\<ts>\window1\renderer.log   ← 请求载荷 + 流式元数据（sessionId/turnId/
                                       messageId/模型配置），但**回复正文不落日志**
```

## 2. 关键结论

- **「布局对齐 Cursor（cursorDiskKV）」是空壳年代的误判**：重装后的 CN 版
  globalStorage state.vscdb 连 cursorDiskKV 表都没有，只有 ItemTable
- 会话正典 = `ModularData/ai-agent/database.db`，自定义加密（疑似应用层密钥，
  头部 16 字节非 SQLite 魔数，排除标准 SQLCipher），**离线读取被加密阻断**
- 明文可见的只有元数据面包屑：输入历史、会话 id、agent 映射、草稿——够定位
  「有这条会话」，不够还原正文
- 会话 id 形如 24 位 hex（`6a99199b3b9da18a60ac68ce`），消息 id 同族 +1 递增

## 3. 读取策略（read_trae，现状）

- cursorDiskKV 引擎保留（对旧版/国际版布局仍可能有效），当前 CN 版表不存在
  → 静默 0 会话，卡片显示 0 是真实状态
- 探测/绑定不变：`%APPDATA%\Trae CN`（认 Trae / Trae CN 两种目录名）
- 卡片显示 **🔒 加密阻断**（读/写 tag 均不提供）；「备份」tag 是唯一可用动作 =
  **原始库整份快照**（`sync.py backup --source trae` 或 webui 同名弹窗）：加密库
  database.db+wal/shm 不解密直接拷进 `~/.session-sync/backups/trae/<ts>/raw/`，
  还原=原位覆盖写回（需 Trae 完全退出）。日后解密攻破，快照即可回补读取

## 4. 边界与后续路径

- 解除阻断需逆向加密（Electron 主进程/随包 native 服务里的密钥派生），成本高，
  优先级由需求定
- 备选：国际版 Trae（不带 CN 后缀）存储是否加密未测；Trae UI 若有导出功能，
  导出件可走归档通道
- snapshot 的 git 快照只含工作区文件状态，不含对话
