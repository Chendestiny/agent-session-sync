# Grok Build 会话结构详解（占位：已装未认证，待实装核验）

核实基线：源码核验（D:\Project_github\grok-build-main，Rust monorepo；xai-dirs +
xai-grok-pager/docs/user-guide/17-sessions.md）。本机已装（~/.grok 有二进制+配置），
**无 xAI 账户未认证 → 无会话数据**，reader/writer 待实装。

## 1. 存储布局（源码实锤）

```
~/.grok\                                        ← $GROK_HOME 可覆盖
├── sessions\<cwd编码>\<session-id>\
│     summary.json        ← 索引：标题(自动生成/手改)/model/时间戳/消息数/parent
│     updates.jsonl       ← ★ 正典会话日志（驱动 /resume 与恢复）
│     chat_history.jsonl  = 发给模型的原始消息层
│     subagents\          = 子代理元数据（子会话本体在正常 sessions 树里）
├── active_sessions.json + .lock               = 活跃会话注册表
└── bin\grok.exe 等
```

## 2. 关键形状（源码自述）

- **session id = UUIDv7**（客户端也可 `-s` 自带 id）→ uuid5 导入可判别
- 标题自动生成（首问后生成、早几轮再生成后冻结），`/rename` 手改优先
- 目录编码与 dsh/pi 同思路（cwd 转义进路径）
- 值得注意：源码含 `xai-grok-foreign-sessions` 模块——只读列出 Claude/Codex/
  Cursor 的会话（SQLite 只读事务）——**同类竞品**，其 claude.rs/codex.rs 可作交叉参考

## 3. 接入计划

认证后发一条测试会话 → 照 minimax 打法逆向三文件真实形状 → read_grok +
grokwrite（JSONL 明文，读写双高可行）
