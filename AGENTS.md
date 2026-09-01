# AGENTS.md — 跨 Agent 会话同步（给 AI agent 的操作手册）

本文件夹是一个自洽工具包：把 **codex / hermes / dsh(DeepSeek Harness) / zcode / workbuddy / claude code / opencode** 七家 AI agent
的会话记录归一到 **dsh** 继续对话，并可导出 Markdown 归档。
**zcode 只出不进**（仅读取源；写入方向已移除——双端同对话易混乱，实测亦有兼容问题）。
你（AI agent）读完本文件即可安全操作，不需要其它上下文。

## 0. 环境准备（一次性）

```bash
cd "<项目目录>"        # 注意：路径含空格，必须带引号
python --version                 # 需要 3.10+
pip install zstandard            # 唯一第三方依赖（import zstandard 不报错即已装好）
```

Node 22 仅在运行 `tools/verify-dsh-backend.cmd`（dsh 原生后端强校验）时需要：先 `nvm use 22`，
或直接调用该 .cmd（它自动优先选 nvm 的 22.x）。

## 1. 第一条命令永远是自检

```bash
python sync.py selftest
```

它在沙箱（`.selftest/`）里完整跑一遍 dsh 写入→读回→增量→校验、归档渲染、webui 只读端点，
**不碰任何真实数据**。全绿（`SELFTEST PASSED`）才继续；有 FAIL 就停下排查（见 §6），
不要带病操作真实数据。

## 2. 日常操作（cookbook）

所有写命令**默认 dry-run**：先跑一遍看计划，确认后加 `--apply`。

**dsh 侧完整闭环（顺序不能乱）**：

```bash
python sync.py to-dsh --source all --scope inc --apply --budget 550000   # ① 导入（两道确认=参数；终端交互会弹菜单）
# ② 完全退出 dsh（含后台进程），然后（挂分组 + 回填侧栏标题缓存）：
python sync.py attach-dsh --apply                                           # ② 挂分组 + 回填侧栏标题缓存
# ③ 启动 dsh → import-* 会话出现在对应分组，可 resume 续聊
```

> 只做 ① 不做 ②，会话会堆在侧栏「未分组」。以后增量导入新会话后，同样要补一次 ②。

| 任务 | 命令 |
|---|---|
| 看各 agent 源概览 | `python sync.py status` |
| 只读可视化 dashboard（给人看） | `python sync.py serve`（浏览器自动开 127.0.0.1:8321，`--port` 可改；三视图：总览 7 泳道时间轴/会话列表筛选/轮次时间条下钻；零写端点 POST 一律 405、仅绑 127.0.0.1、实时读源无缓存。用户想看会话全景/排查时间分布时起给他；agent 自己分析数据不需要它。install 脚本装过后任意目录可直接 `session-sync serve`） |
| 导入到 dsh（计划→落盘） | `python sync.py to-dsh --source all --scope inc` 然后 `--apply --budget 550000`（终端跑自动弹两道确认；非交互必须显式两参，缺参拒绝） |
| 反向写入 codex / claude code / hermes / opencode / workbuddy | `python sync.py to-codex\|to-claude\|to-hermes\|to-opencode\|to-workbuddy --source all --scope inc --apply`（同款两道确认+历史拦截；非 dsh 目标 all 含 dsh 源；写入器在 agentsync/{codex,claude,hermes,opencode,workbuddy}write.py；zcode 不可写）。**六目标已实测全通**（每家的可见性坑都已固化修复：codex 需登记 state_N.sqlite threads 索引、hermes 需计数列、opencode 需 path 列+对齐默认项目上下文） |
| 规范库（A→C→B 架构） | `python sync.py pull --source all --scope inc`（各源→~/.session-sync，只读源安全免退出）→ `python sync.py push --target dsh\|codex\|claude\|hermes --source all --scope inc --apply`（C→目标，幂等断点续推，中途换 agent 重跑即续；与直通 to-X 共享幂等 id，混用不重复） |
| 挂工作区分组 + 标题预投影 | **退出 dsh 后** `python sync.py attach-dsh --apply`（改 workspace.json + 回填 projcache title 行，均先备份） |
| 批量改标题 | 编辑 `titles.json`（{源ID: 新标题}）→ `python sync.py to-dsh --source all --scope all --apply --force --confirm-history --titles titles.json --budget 550000` → 重启 dsh |
| 只同步某个会话 | 加 `--session <源ID子串>`（如 `--session sess_07c4`） |
| 只同步某个工作区 | 加 `--cwd frontend`（子串匹配） |
| 只要最近数据 | 确认2 用 `--scope 7d`（按最后活跃时间，推荐）；旧参数 `--since 7` 按创建时间过滤，仍可用 |
| 超长会话防超上下文 | `to-dsh` 加 `--budget 200000`（三层裁剪保续聊） |
| Markdown 归档 | `python sync.py archive --source all --apply` → `archive/` |
| 清理孤儿/测试会话 | **退出 dsh 后** `python sync.py prune --apply`（dry-run 先看；孤儿=源已删的导入，junk=纯打招呼/冒烟；移入 `~/.trash-dsh` 可恢复，manifest.jsonl 有明细） |
| 校验已导入的 dsh 会话 | `python sync.py verify` |
| 一键收尾（不想逐条跑） | 退出 dsh 后 `python sync-finish.py`（先弹两道确认→prune+导入+挂载+校验；`--sources zcode --scope 7d` 参数即确认）；`--check` 只读预览 |
| dsh 原生后端强校验 | `tools\verify-dsh-backend.cmd`（Node 22） |

**同步语义**：幂等（重复跑自动去重）；增量（源会话长了再跑 `to-dsh` 只追加新轮次；
`--scope inc` 基准存于 `~/.dsh/sessions/.agentsync-state.json`，`--apply` 成功后推进，回看 15 分钟重叠）；
**人在回路**（to-dsh / sync-finish 先确认 ①来源区 ②数据量：交互弹菜单、非交互参数即确认缺参拒绝；
**历史全量二次拦截**：`--scope all` 或 inc 首跑在 `--apply` 时需交互 y/N 或人给的 `--confirm-history`，agent 不得自行拍板全量；
**大批量第三道**：候选 >15 交互弹会话勾选清单，非交互需 `--confirm-batch` 或缩小范围——绝不一股脑写入）；
导入会话按源工作区自动落分区。落盘后可见性：dsh 需 attach + 重启。
预期留在 dsh「未分组」的：源会话无 cwd（hermes 旧库）、cwd 目录已删除、临时目录、
cwd 嵌套在已有工作区路径下（dsh 启动会清理这类嵌套记录）。
**子代理会话默认排除**：dsh 每次 agent 委派各落一个会话目录（header `origin=subagent`，
侧栏隐藏但磁盘全在）——read_dsh 默认不返回它们（对齐 zcode parent_id / codex 过滤口径），
审计全量用 `read_dsh(root, include_subagents=True)` 或 webui 展示口径（🤖 徽章）。

## 3. 安全铁律

1. **zcode 写入仅限「裁剪预算内新建/追加」，全量大会话写入=报废**（2026-09-01 实测：124 轮
   163 万 tokens 全量追加 → resume 超上下文黑屏、压缩卡死，会话报废；裁剪到 ~100k tokens
   新建则可续聊）。任何 zcode 写入必须：先估算 tokens、`trim_turns` 裁剪、确认 zcode 完全退出、
   全库备份；行形状对齐原生（assistant parentID→轮内 user 消息、anchor=null）。
   `agentsync/zcodewrite.py` 仅作历史参考。
2. 读取侧永远只读（sqlite `mode=ro`）；写 dsh 只新增 `import-*` 会话目录，不动原生会话。
3. 改 dsh 的 workspace.json/projcache（attach-dsh）必须在其完全退出后进行。
3. 对用户报结果时如实说明：写了多少、跳过多少、有无裁剪、哪些步骤被阻塞待用户配合。
5. **本机个人操作一律放 `local/`（已 gitignore）**：一次性执行脚本、含真实会话 id/标题/本机路径的
   清单与产物，绝不提交、绝不放进公共目录。只有对所有用户有用且无隐私的才进正式目录。
   临时验证脚本用 `.tmp-*` 前缀（同样被忽略，用完即删）。

## 4. 文档地图

| 文件 | 内容 |
|---|---|
| `AGENTS.md` | 本文件：操作手册（你是 agent 就看这个） |
| `SKILL.md` | 同样内容的 skill 封装（装到 `~/.agents/skills/session-sync/` 后可一句话触发） |
| `README.md` | 人类视角的项目说明、验证记录 |
| `docs/FORMATS.md` | 格式总览 + 归一化 IR + 索引（先看这个再进分册） |
| `docs/pitfalls.md` | **踩坑总录**（按家分组，全部已修进代码；排障先查这里） |
| `docs/agents/dsh.md` | dsh 深度规格：多帧 zstd、事件纪律、目录编码、workspace.json 分组挂载 |
| `docs/agents/zcode.md` | zcode 深度规格：三表结构、message/part 模板、project_id（读取源；写入器已弃用存档） |
| `docs/agents/hermes.md` | hermes 深度规格：state.db 两表、三种 role 形态、已知边界 |
| `docs/agents/codex.md` | codex 深度规格：rollout JSONL、response_item 映射、subagent 过滤 |
| `docs/agents/workbuddy.md` | WorkBuddy 深度规格：db+JSONL 双层、读取规则（已实现）、写入配方（未实现） |
| `examples/` | 示例：真实命令输出转录 + 两条完整转换实例（源→dsh 事件日志 / 源→zcode 数据行） |
| `agentsync/` | Python 源码（readers 七家读取 / dshwrite 写入+挂载 / confirm 人工确认 / syncstate 增量基准 / model IR / archive / validate / store C 库 / webui 只读 dashboard；zcodewrite 已废弃保留） |
| `tools/` | Node 22 的 dsh 原生后端校验脚本 |
| `reference/` | 参考仓库（dsh-chat-import 等；agentctxsync 在 `本地克隆的 agentctxsync 仓库`） |
| `archive/` | Markdown 归档输出目录 |

## 5. 维护：dsh / zcode 升级后怎么确认还兼容

1. `python sync.py selftest` —— 快速回归。
2. dsh 侧分区编码全量核对（应输出 mismatches 0）：
   ```bash
   python - <<'EOF'
   import sys, glob, os; sys.path.insert(0, '.')
   from agentsync.dshwrite import project_key
   from agentsync.readers import _zstd_decode_all, _parse_jsonl
   bad = total = 0
   for path in glob.glob(os.path.expanduser('~/.dsh/sessions/*/*/session.jsonl*')):
       folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
       try:
           raw = open(path, 'rb').read()
           text = _zstd_decode_all(raw).decode('utf-8', 'replace') if path.endswith('.zstd') else raw.decode('utf-8', 'replace')
           h = next(o for o in _parse_jsonl(text) if o.get('type') == 'session')
       except Exception: continue
       total += 1
       if (project_key(h['cwd']) if h.get('cwd') else '_no-cwd') != folder: bad += 1
   print(f'checked {total}, mismatches {bad}')
   EOF
   ```
3. zcode 侧 project_id 全量核对（应输出 mismatches 0）：
   ```bash
   python - <<'EOF'
   import sys, sqlite3, os; sys.path.insert(0, '.')
   from agentsync.paths import zcode_project_id
   db = os.path.expanduser('~/.zcode/cli/db/db.sqlite').replace('\\', '/')
   con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
   bad = sum(1 for d, p in con.execute('SELECT DISTINCT directory, project_id FROM session') if zcode_project_id(d) != p)
   print('mismatches:', bad)
   EOF
   ```
4. 不兼容时的适配点：`agentsync/dshwrite.py`（事件/schema/编码）。规格基线见 `docs/FORMATS.md`
   （zcode 只读，其读取器 `readers.read_zcode` 若因结构变化失败，对照 `docs/agents/zcode.md` 修）。

## 6. 故障排查

| 症状 | 原因与处理 |
|---|---|
| `ModuleNotFoundError: zstandard` | `pip install zstandard` |
| 想清理历史导入进 zcode 的会话 | **必须清两个库**：① `cli/db.sqlite`（识别：`sess_` 前缀 + 总长 41 + uuid 版本位= '5'，删 part/message/session）；② `v2/tasks-index.sqlite` 的 `tasks` 表（删 `task_id NOT IN (SELECT id FROM session)` 的僵尸行，否则 UI 残留 + `fault.subscribe.sessionNotFound`）。两库先备份（2026-08-26 已清理 246 会话 + 199 僵尸 task，备份 `*.cleanup-bak-*`） |
| to-dsh 写出后 dsh 里看不到会话 | 重启 dsh / 新开会话列表；确认会话在 `~/.dsh/sessions/<工作区>/import-*/` 下 |
| `verify-dsh-backend` 报找不到包 | dsh 安装布局变化：`set AGENTSYNC_DSH_JSONL_PKG=<dsh-session-persistence-jsonl 目录>` 再跑 |
| hermes 旧会话落在 `_no-cwd` / 主目录 | 源数据本身没有 cwd，属预期行为 |
| 路径含空格导致命令报错 | Git Bash 下始终 `cd "<项目目录>"` 带引号 |
| dsh resume 超上下文 | 重导时加 `--budget 200000`（裁剪中间轮次保锚点+尾部） |
| 点开导入会话报 `SessionFormatUnsupportedError: ... session/imported ... not marked ignorable` | 旧版导入缺顶层 `ignorable:true` 标记：`python sync.py to-dsh --apply --force [--budget 550000]` 整体重写后重启 dsh（校验器已内置该规则，`verify` 不通过即未修复） |
| 导入会话标题显示成工作区名/全在「未分组」 | 两种原因：① 日志被判不可解析（ignorable 缺失）→ `to-dsh --force` 重写；② projcache 无 title 行（点开才有标题）→ 退出 dsh 后 `attach-dsh --apply` 回填 |
| 重写后仍在「未分组」 | 会话没挂进 workspace.json 的工作区记录：退出 dsh → `python sync.py attach-dsh --apply` → 重启。无 cwd 的源会话（hermes 旧库）按原生语义留在未分组 |
| attach 报「检测到 dsh 正在运行」 | 完全退出 dsh（含后台 node 进程）后重试；dsh 退出时会把内存中的 workspace 状态写回磁盘，运行中改必被覆盖 |
| dsh 启动报 `stored record … does not match its schema`（createdAt/updatedAt） | workspace.json 有记录缺 `createdAt/updatedAt`（ISO 字符串）——用 `storages/workspace.json.agentsync-bak-*` 恢复，或给缺字段记录补上两键；新代码已不会再产生这种记录 |
| attach 后个别会话仍在未分组 | 看 attach 输出的跳过原因：无 cwd / 目录已删 / 临时目录 / 嵌套在已有工作区路径下（dsh 会清理嵌套记录，工具保持一致不建） |
