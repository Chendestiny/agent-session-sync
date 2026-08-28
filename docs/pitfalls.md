# 踩坑总录（都已修进代码）

按家分组。每条含：坑 → 现象 → 修复。新坑照此格式追加，README 不再维护此表。

## dsh（DeepSeek Harness）

| 坑 | 现象 | 修复 |
|---|---|---|
| `session/imported` 缺顶层 `ignorable:true` | 整条日志被拒、标题回退、全进未分组 | 写入器补标记；校验器内置宿主事件词汇表规则 |
| workspace 记录缺 `createdAt/updatedAt` | dsh 启动直接失败（Zod 校验） | apply_attach 写全 5 键；schema 记入 docs/agents/dsh.md |
| 嵌套路径工作区 | dsh 启动时清理嵌套记录 | attach 与 dsh 行为一致：嵌套 cwd 不建组 |
| projcache 无 title 行 | 列表不显示标题（显示工作区名），点开才有 | 侧栏标题读投影缓存而非日志；attach-dsh 同时回填 title 行 |
| projcache identity 失配但 title 一致 | cachedSnapshot 整条拒绝 → 侧栏无标题且无法自愈 | plan_title_backfill：title 与 identity 双一致才跳过，失配即重建（回归测试覆盖） |
| 会话 id 命中归档列表 | 数据四层全对但侧栏不渲染（删除→同 id 复活即隐身） | prune/删除同步清 archivedSessionIds；复活历史归档需手工移除 id |
| 运行中写 workspace/projcache | dsh 退出时内存回写覆盖，造成「成功但看不见」假象 | attach/prune 硬前提：dsh 完全退出后执行 |

## codex CLI

| 坑 | 现象 | 修复 |
|---|---|---|
| resume 列表不认外来 rollout | 文件落位但 picker 只显示原生会话 | 0.137 的列表读 `~/.codex/state_N.sqlite` 的 **threads 索引**而非扫描文件；写入器落盘时同步登记 threads 行（模板取自真库显示行：`\\?\` 前缀 cwd、字符串秒时间戳） |
| 极简 session_meta | 同上（即使登记了索引，续聊解析也可能缺上下文） | meta 对齐原生字段集：originator/cli_version/source/thread_source/base_instructions（从本机原生 rollout 动态抄） |

## claude code

| 坑 | 现象 | 修复 |
|---|---|---|
| thinking 块带 signature 校验 | 外来 thinking 块有被拒风险 | 写入器跳过 thinking（文本与工具往返完整保留） |
| 坏 shim | `claude` 报 claude.exe 不存在 | 本机可用入口 `~\bin\claude.exe`；根治 = 删 `C:\Program Files\nodejs` 下三个 stale shim（需管理员） |

## hermes

| 坑 | 现象 | 修复 |
|---|---|---|
| sessions 计数列为空 | 列表显示「0条消息，未展开」（消息行在库、格式也对） | 列表 UI 读 `message_count/tool_call_count/source` 计数列；写入器创建时填、追加后按 messages 实测值刷新 |
| `sessions.source` NOT NULL 无默认 | INSERT 直接 IntegrityError | 写入 `source='cli'`（agentctxsync 同款坑） |

## opencode

| 坑 | 现象 | 修复 |
|---|---|---|
| 缺 `path` 派生列 | db 三表全对但桌面列表不显示 | `path = directory 去盘符`（`C:/Users/x` → `Users/x`，真库实证） |
| directory 与当前项目不匹配 | 补了 path 仍看不到 / 全堆在 Default Project | 分区规则：cwd 真实存在 → 会话落自己的分区（找/建 project；桌面自建的分区自动复用）；缺失 → 兜底默认上下文（global）。默认上下文取 global 分区 time_created 最新会话的 directory（不能用 time_updated——force 重写会推高它造成漂移） |
| session.model 裸字符串 | opencode JSON 解析 model 列会炸 | 必须写 JSON：`{"id","providerID":"opencode","variant":"default"}` |
| force 重写不清旧消息 | 确定性 uuid5 id 撞 message 主键 | force=create 时先 DELETE 旧 message/part 再写 |

## workbuddy

| 坑 | 现象 | 修复 |
|---|---|---|
| cwd 缺失的会话 | WorkBuddy 拒开 | 写入前 `os.path.isdir` 校验，缺失兜底主目录（agentctxsync 同款） |
| edge-sync-mapping-v2.db | 误碰可能破坏云同步映射 | 绝对不碰（读取器/写入器都不打开它） |

## zcode（只读，教训存档）

| 坑 | 现象 | 修复 |
|---|---|---|
| 向 zcode 写入会话 | 时间显示异常（旧会话显示 1 分钟前）、部分会话渲染空白 | **方向整体移除**（zcode 只出不进）；已导入 246 会话已清理（识别规则：sess_+uuid5 版本位=5，备份 db.sqlite.cleanup-bak-*） |
| 写入每会话备份一次 | 一次导入百余个全量备份（23GB） | 历史教训：改每运行一次；现已随方向移除作废 |

## 跨家通用

| 坑 | 现象 | 修复 |
|---|---|---|
| 双向同步两边列表污染 | 同一会话两边各一份，续聊即分叉 | 产品决策：单向归一（A→C→B）；目标侧 id=uuid5 幂等，同源重推不重复 |
| 各家 CLI 的运行中写入 | SQLite 内存回写/缓存导致看不见或被覆盖 | to-hermes/to-opencode/to-workbuddy 建议目标应用退出后执行；文件型目标（codex/claude/dsh 会话文件）随时可写 |
