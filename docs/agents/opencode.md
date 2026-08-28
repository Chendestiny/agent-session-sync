# OpenCode 会话结构详解

核实基线：opencode CLI（Windows 本机 `~/.local/share/opencode/opencode.db`，实测 1 会话
「简单问候」读取通过）；表结构来自 agentctxsync 适配器（其实机验证，MIT）——CLI 与
桌面版**共享同一个 SQLite**（`opencode db path` == 桌面版 opencode.db）。
**当前状态：读取器已实现**（`readers.read_opencode`，第七家源；写入方向不做）。

## 1. 存储定位（探测顺序）

```
$XDG_DATA_HOME/opencode/opencode.db
%LOCALAPPDATA%/opencode/opencode.db      ← Windows 常规位
~/.local/share/opencode/opencode.db      ← 本机实际命中位
```

任一命中即用；全都没有 → 该源为空（status 显示未找到，不报错）。

## 2. 三表结构（读取只用以下列）

```
session(id TEXT PK, project_id, directory, title, model,
        time_created INTEGER ms, time_updated INTEGER ms, …)
message(id TEXT PK, session_id, time_created ms, data TEXT JSON)
part   (id TEXT PK, message_id, session_id, time_created ms, data TEXT JSON)
```

- `session.directory` = 工作区 cwd（空 → 兜底用户主目录，同 hermes 处理）
- `session.model` 可能是 `"claude-sonnet-4"`，也可能是含 `modelID` 的 JSON 字符串
- 时间全为毫秒，`time_updated` 直接作 Session.updated_at（增量基准）

## 3. message.data 的 role（读取过滤）

`data = {"role": …}`；跳过 `agent-switched` / `model-switched` / `compaction` / `step`
（运行时切换与压缩事件，非对话）。`shell` 视作工具类消息。

## 4. part.data 的 type

| type | 关键字段 | 映射 |
|---|---|---|
| `text` / `input_text` / `output_text` | `{text}` | user 取 input_text，assistant 取 text/output_text |
| `reasoning` | `{text}` | reasoning 块 |
| `tool` | `{tool, state:{input, output, status}}` | 调用+回传同挂一个 step（同 zcode：state 里 input/output 都在） |

tool 的调用 id 用 part 自带 id（无则合成 `oc-<sid尾>-<轮>-<步>`）；
`state.status in (failed, error)` → is_error。

## 5. 已知边界

- 本机样本尚少（1 会话）；若新版 opencode 改 schema（列名/role 集合变化），
  以 `python sync.py status` 的 opencode 行为第一道报警，对照本篇更新。
- opencode 的会话标题就在 session.title（无 ai-title 事件），为空时走首轮提问回退。
