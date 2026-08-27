# 转换示例 1：zcode 会话 → dsh 可续聊会话

**源**：zcode 会话 `sess_e5ffd0df`（标题「你好」，工作区 `C:\Users\demo\.zcode\workspace\default`，2 轮对话）

**命令**：`python sync.py to-dsh --session sess_e5ffd0df --apply`

**产物**：`~/.dsh/sessions/--C-Users-demo-.zcode-workspace-default--/import-sess_e5ffd0df-…/session.jsonl.zstd`

下面是产物解压后的完整事件日志（共 19 行）。行尾 `#` 注释说明每类事件的作用。
物理文件是两个独立 zstd 帧拼接：第 1 帧只有头行，第 2 帧是全部事件（每帧带校验和）。

```jsonl
{"type": "session", "version": 0, "id": "import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a", "createdAt": 1786949755813, "cwd": "C:\\Users\\demo\\.zcode\\workspace\\default", "delegationDepth": 0}   # 会话头：version/id/createdAt(毫秒)/cwd(决定工作区分区)/delegationDepth。写入帧 1。
{"type": "session/imported", "seq": 0, "time": 1786949755813, "data": {"tool": "zcode", "sourceId": "sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a", "sourcePath": "C:\\Users\\demo\\.zcode\\cli\\db\\db.sqlite", "importedAt": 1787654811958}}   # 导入标记(ignorable)：记录从哪来、何时导入。只在首次导入写一次。
{"type": "user/message", "seq": 1, "time": 1786949755813, "data": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:env", "role": "user", "content": [{"type": "text", "text": "【环境变更提示】本会话已从 zcode 迁移到 DeepSeek Harness（DSH）。当前运行…   # ① 环境变更声明(source.kind=plugin，UI 折叠)：告知模型已迁移、以 DSH 当前环境为准。
{"type": "turn/start", "seq": 2, "time": 1786949755813, "data": {"turn": 1}}   # 轮次开始（turn 从 1 计）。
{"type": "step/start", "seq": 3, "time": 1786949755813, "data": {"turn": 1, "step": 1}}   # 步骤开始。
{"type": "user/message", "seq": 4, "time": 1786949755813, "data": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:u1", "role": "user", "content": [{"type": "text", "text": "你好"}], "source": {"kind": "user"}}, "surfaceOp": "a…
{"type": "assistant/message", "seq": 5, "time": 1786949755813, "data": {"turn": 1, "step": 1, "message": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:a1:1", "role": "assistant", "content": [{"type": "text", "text": "你好！有什…   # ② 模型回复正文。同一提问的回复可能分多 step。
{"type": "step/end", "seq": 6, "time": 1786949755813, "data": {"turn": 1, "step": 1}}   # 步骤结束。
{"type": "step/start", "seq": 7, "time": 1786949755813, "data": {"turn": 1, "step": 2}}
{"type": "assistant/message", "seq": 8, "time": 1786949755813, "data": {"turn": 1, "step": 2, "message": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:a1:2", "role": "assistant", "content": [], "source": {"kind": "model", …
{"type": "step/end", "seq": 9, "time": 1786949755813, "data": {"turn": 1, "step": 2}}
{"type": "turn/end", "seq": 10, "time": 1786949755813, "data": {"turn": 1, "reason": {"kind": "completed"}}}   # 轮次结束（reason=completed）。
{"type": "turn/start", "seq": 11, "time": 1786949755813, "data": {"turn": 2}}
{"type": "step/start", "seq": 12, "time": 1786949755813, "data": {"turn": 2, "step": 1}}
{"type": "user/message", "seq": 13, "time": 1786949755813, "data": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:u2", "role": "user", "content": [{"type": "text", "text": "你好"}], "source": {"kind": "user"}}, "surfaceOp": "…
{"type": "assistant/message", "seq": 14, "time": 1786949755813, "data": {"turn": 2, "step": 1, "message": {"id": "import:import-sess_e5ffd0df-d5fd-4ee8-ac21-30872c7d791a:a2:1", "role": "assistant", "content": [{"type": "reasoning", "text": …
{"type": "step/end", "seq": 15, "time": 1786949755813, "data": {"turn": 2, "step": 1}}
{"type": "turn/end", "seq": 16, "time": 1786949755813, "data": {"turn": 2, "reason": {"kind": "completed"}}}
{"type": "session/title", "seq": 17, "time": 1786949755813, "data": {"title": "你好", "messageSeqs": [], "source": {"kind": "user"}}}   # 标题钉住事件：防止 dsh 自动改名覆盖原标题。只在首次导入写。
```

> 换成有工具调用的会话时，`assistant/message` 与 `step/end` 之间还会出现成对的
> `tool/call`（callId/name/arguments）与 `tool/result`（配对不变量：每个 call 必有 result，
> result 通过 sourceEventSeqs 指回对应 call）。完整规格见 `docs/FORMATS.md` §1.5。
