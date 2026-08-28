# dsh（DeepSeek Harness）会话结构详解

核实基线：dsh 0.1.1-rc.1（Windows，DSH_HOME=`~/.dsh`）；2026-08-25 全量核对 349 会话。

## 1. 存储总览

```
~/.dsh/
├── sessions/                        ← 会话日志（本工具读写区）
│   ├── --D-agent-svc--/              ← 工作区分区目录（projectKey 编码，§2）
│   │   ├── session-<uuid>/          ← 原生会话目录（encodeSegment 编码）
│   │   │   └── session.jsonl.zstd   ← 事件日志（多帧 zstd，§4）
│   │   └── import-<slug>/           ← 外部导入会话（本工具/插件创建）
│   ├── _no-cwd/                     ← 无 cwd 会话的分区
│   └── ...
├── storages/
│   ├── workspace.json               ← 工作区注册表 + 分组挂载（§6，分组的关键）
│   ├── session_projcache.json       ← 会话投影缓存（侧栏标题数据源 + 统计/token，attach-dsh 回填 title 行）
│   └── cost_tracker.json
└── settings.yaml / .credentials.yaml / profiles/ ...
```

## 2. 目录名编码（源码级：dsh-session-persistence-jsonl）

**projectKey(cwd)** → 工作区分区目录名：

| 输入字符 | 输出 |
|---|---|
| `/` `\` `:` | 合并为单个 `-`（连续分隔符去重） |
| `A-Za-z0-9._-`（非 `~`） | 原样 |
| 其它（含中文、空格） | `~XXXX`（UTF-16 码元 4 位大写十六进制，代理对逐码元） |

整体 `--` 包裹，去头部 `-`，截 251 字符，空则 `root`。
例：`C:\Users\demo` → `--C-Users-demo--`；`D:\study\中文项目…` → `--D-Study-AI_Agent-~6768~…--`。

**encodeSegment(sessionId)** → 会话目录名：同上转义但无分隔符合并特判；`.`→`~002E`、`..`→`~002E~002E`。`import-xxx`、`session-<uuid>` 全安全字符，原样。

## 3. 会话 ID

- 原生：`session-<uuid7>`（时间有序 UUID）。
- 导入：`import-<源ID去[^a-zA-Z0-9_-]截64>`（dsh-chat-import 约定；幂等）。

## 4. session.jsonl.zstd 物理格式

**文件 = 多个独立 zstd 帧拼接**，每帧带校验和（ZSTD_c_checksumFlag=1）、帧头含 content size：

- 第 1 帧：会话头行 + `\n`；
- 之后每次逻辑 append 一帧：该批事件 JSONL（`\n` 连接 + 尾 `\n`）；
- 读取端按帧边界扫描逐帧解码，与帧数无关。

Python 写法：`zstandard.ZstdCompressor(write_checksum=True, write_content_size=True).compress(bytes)` 逐帧压缩后拼接；读法：`stream_reader(..., read_across_frames=True)`。

## 5. 事件日志（JSONL）

### 5.1 会话头（第一行，独立帧）

```json
{"type":"session","version":0,"id":"<sessionId>","createdAt":<毫秒>,
 "cwd":"D:\\code\\agent-svc","delegationDepth":0}
```

可选：`parentSession`、`seedLength`、`origin`（仅 `"subagent"`）、`agentPreset`。
守卫：createdAt 必须安全整数毫秒；禁止已废弃的 `sandboxMode`/`approvalPolicy`。

### 5.2 事件类型（宿主 KNOWN_SESSION_EVENT_TYPES，0.1.1-rc 共 48 种）

对话核心 9 种（导入只写这些 + 标记）：

| 事件 | data | 说明 |
|---|---|---|
| `turn/start` | `{turn}` | 1 起计 |
| `step/start` | `{turn,step}` | |
| `user/message` | `{id,role:"user",content:[{type:"text",text}],source:{kind:"user"}}` | surface 事件 |
| `assistant/message` | `{turn,step,message:{id,role,content:[block],source:{kind:"model",provider,model}}}` | block：text/reasoning/tool-call |
| `tool/call` | `{turn,step,callId,name,arguments}` | arguments 为 JSON 字符串 |
| `tool/result` | `{turn,step,message:{content:[{type:"tool-result",toolCallId,content,isError?}],source:{kind:"tool",callId}}}` | 与 call 一一配对 |
| `step/end` | `{turn,step}` | |
| `turn/end` | `{turn,reason:{kind:"completed"}}` | |
| `session/title` | `{title,messageSeqs:[],source:{kind:"user"}}` | 钉标题防自动覆盖 |

其余为运行时/状态事件（`request/header`、`permission/preset`、`sandbox/mode`、`assistant/chunk` 流式块、`compaction/*`、`todo/write`、`agent-preset/selected`…），导入时不写。

### 5.3 硬纪律（违反即 SessionFormatUnsupportedError / resume 失败）

1. **词汇表**：类型必须在 KNOWN_SESSION_EVENT_TYPES 内，**否则该事件必须带顶层 `ignorable:true`**（`session/imported` 标记即靠此放行——漏写会导致整条日志被拒、标题回退、未分组）。
2. **seq**：从 0 连续递增，append 续写接着排。
3. **surfaceOp**：`user/message`/`assistant/message`/`tool/result` 必须带 `surfaceOp:"append"`。
4. **call↔result 配对**：每个 `tool/call` 必须有 `tool/result`（模型 API 硬性要求），result 用 `sourceEventSeqs:[call的seq]` 指回。
5. 增量续写不重复写 `session/imported`、环境声明、`session/title`。

### 5.4 导入会话的合成顺序（本工具 synthesize）

```
session/imported(ignorable) → user/message 环境声明(source.kind=plugin，UI 折叠)
→ [turn/start → step/start → (step1 前插 user 提问) → assistant/message
   → tool/call* → tool/result*(含空结果兜底) → step/end]* → turn/end
→ session/title
```

## 6. 工作区分组机制（sidebar「未分组」的根源）

分组**不在会话文件里**，在 `~/.dsh/storages/workspace.json`：

```json
{"unit":{"name":"workspace","version":2},
 "global":{"initialized":true,"workspaceIds":["<uuid>",…],"archivedSessionIds":[…]},
 "tables":{"workspaces":{"<uuid>":{"path":"D:\\code\\agent-svc","title":"agent-svc",
                                   "sessionIds":["session-xxx","import-yyy",…],
                                   "createdAt":"2026-08-14T01:49:03.432Z",
                                   "updatedAt":"2026-08-25T10:19:44.174Z"}}}}
```

**记录 schema 硬约束**（dsh-workspace 的 workspaceRecord，Zod）：必填 5 键
`path/title/sessionIds/createdAt/updatedAt`，后两者为 ISO-8601 字符串——
新建记录缺任一字段会导致 dsh 启动时整个插件树加载失败（boot 报
`does not match its schema`）。

- 原生流程：创建会话时宿主 `attachSession`（校验 header.cwd 实路径 === 记录 path 后，把 id 前插进 sessionIds）。
- 直接写文件绕过 attach → 会话不进任何 sessionIds → 侧栏「未分组」。
- 修复：本工具 `attach-dsh` 命令改写该文件（挂已有记录或新建记录：新 uuid + title=basename + 进 workspaceIds）。
- **必须 dsh 完全退出时改**（运行中 dsh 退出时会把内存状态写回覆盖）。
- **嵌套路径规则（实测）**：cwd 嵌套在已有工作区路径之下的记录（如已有 `C:\Users\demo`
  记录时再建 `C:\Users\demo\.zcode\workspace\default`）会被 dsh 启动时清理——
  attach 工具与之间保持一致，这类 cwd 不建组、留在未分组。
- 无 cwd（`_no-cwd`）、cwd 目录不存在、临时目录的会话按设计留在未分组。
- **归档列表（第四种状态，实测踩坑）**：`global.archivedSessionIds` 按 session id 记忆
  归档，被归档的会话侧栏不渲染——与挂载/投影完全独立。若删除后以**相同 id 复活**
  （幂等导入的天然行为），id 仍在归档列表 → 「复活即隐身」（数据四层全对但 UI 不显示）。
  prune/手动删除已同步清理归档记录；复活历史归档会话需手工从该列表移除 id。
- **`session_projcache.json` 是侧栏标题的数据源**（dsh-session-projection-cache 的
  cachedSnapshot 零 IO 列表读）：正常只在会话被打开/adopt 时回填，直接落盘的导入会话
  没有条目 → 列表回退显示工作区名，点开后才有标题。`attach-dsh` 现在会同步回填
  title 行（行 schema 宽松：`rows` 任意子集合法，最小回填 = identity{createdAt,cwd} +
  `title: {ver:1, seq:<title事件seq>, val:<标题>}`；ver 需与本机 dsh 写入值一致）。
- **identity-check 是缓存的生死门（实测踩坑）**：cachedSnapshot 按
  `identity{createdAt, cwd}` 与**当前会话头**做严格比对，失配则**拒绝整条记录**
  （不只是 title——整个 projections 块都无）。典型触发：force 重写/兜底后 header 的
  cwd 变了（如 `_no-cwd` → home），而 projcache 旧条目还是 dsh 在旧文件时代写的
  identity（`createdAt=None, cwd=None`）→ 数据四层全对但侧栏无标题。
  `attach-dsh` 的回填已同步校验并刷新失配 identity；存量修复用 `scripts/fix_identity.py`。
- 验证侧栏真实数据可直接调 dsh web 的 RPC（实测可用）：
  `POST http://127.0.0.1:3080/api/session.list`，
  body `{"type":"client-request","rpcId":"r1","method":"session.list","payload":{"args":{}}}`，
  检查 items[].projections.values.title。

## 7. 读取规则（本工具 reader）

- 遍历 `sessions/*/*/session.jsonl(.zstd)`；`turn/start` 开轮，`assistant/message` 开 step，
  `user/message`（kind≠plugin）为轮提问，`tool/call`/`tool/result` 按 callId 归位；
- 跳过流式块（`assistant/chunk` 等）；`session/title` 取标题。
