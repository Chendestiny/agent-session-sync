---
name: session-sync
description: 跨 Agent 会话同步（codex/hermes/dsh/zcode/workbuddy 等 agent → dsh 单向 + Markdown 归档）。当用户要"同步会话 / 导入会话 / 迁移会话 / 把 X 的会话搬到 Y / 归档会话 / 在 dsh 里继续另一家的会话"时使用。本目录即完整工具包：sync.py 为 CLI，AGENTS.md 为完整操作手册。
---

# 跨 Agent 会话同步（session-sync）

本 skill 目录是一个自洽工具包：读取 codex / hermes / dsh / zcode / workbuddy / claude / opencode / qoder / openclaw / cursor / trae 十一家会话，
写入 dsh（可续聊），并支持 Markdown 归档。
**详细操作手册见同目录 `AGENTS.md`**（cookbook、安全铁律、故障排查、格式文档地图），
格式深度规格见 `docs/FORMATS.md`。以下是要点。

## 安装

本目录整体就是一个 skill bundle。把本目录链接（或复制）到 agent 的 skills 目录即可：

```bat
mklink /J "%USERPROFILE%\.agents\skills\session-sync" "<项目目录>"
```

（junction 不需要管理员权限；dsh/zcode/hermes 均从 `~/.agents/skills/` 发现。）

## 执行纪律（必须遵守）

1. **第一条命令跑自检**：`python sync.py selftest`，全绿才继续（沙箱运行，不碰真实数据）。
2. **先 status 再动作**：`python sync.py status` 确认各 agent 源路径与数量。
3. **人在回路两道确认（to-dsh / sync-finish）**：同步前必须确认 ①来源区 ②数据量。
   用户在终端跑 → 自动弹两道菜单（回车有默认，q 取消）；agent 代跑（非交互）→
   **必须显式给 `--source`（all / zcode,workbuddy 等）和 `--scope`（inc/7d/30d/N天/all），
   参数即确认，缺任一命令会拒绝执行**——绝不替用户默认范围。
   **历史全量（`--scope all` 或 inc 首跑）额外拦截**：交互弹 y/N（默认取消）；
   非交互必须由用户显式给 `--confirm-history`——agent 绝不自行拍板全量历史。
   **大批量（候选 >15）第三道勾选**：交互弹会话清单（回车=全部 / 编号多选如 1,3-5,8 / q 取消）；
   非交互必须 `--confirm-batch` 或先缩小范围（--session / --limit / 有界 --scope）——绝不一股脑写入。
4. **默认 dry-run**：所有写命令先不带 `--apply` 跑一遍，把计划给用户看过再落盘。
5. 出错就停下报告，不要猜测性重试。
6. **attach-dsh / prune 的硬前提：dsh 必须完全退出**（含托盘/后台 node 进程）。
   若检测到 dsh 在运行：**停下来告知用户「请先完全退出 dsh，退出后告诉我，我再执行 attach」**，
   绝不绕过、不用任何方式强行写入——运行中写入会被 dsh 退出时的内存回写覆盖，
   造成「执行成功了但侧边栏看不到」的假象。用户重启 dsh 后才能看到结果。
7. **同步完必须二次验证**：`verify` 通过 + 抽查落盘文件三要素（imported 标记含 ignorable、
   [来源]前缀标题、分区目录=cwd编码）+ `attach-dsh`（dsh 已退出时）后提醒用户重启 dsh 目视复核。
8. **需要跑 node 的校验前先 `nvm use 22`**（node:zlib 的 zstd API 要求 Node 22+；
   `tools/verify-dsh-backend.cmd` 已内置自动选择 nvm 22.x，可直接调用）。

## 常用命令

```bash
cd "<本目录>"                     # 路径含空格，务必带引号
python sync.py status                                  # 各 agent 源概览
python sync.py web                                    # 可视化 dashboard：浏览器自动开 127.0.0.1:8321（给人看的；装过 install 后任意目录可用全局命令，快捷 ass web）
python sync.py to-dsh                                  # 交互终端：弹两道确认菜单（来源区→数据量）后 dry-run
python sync.py to-dsh --source all --scope inc --apply --budget 550000   # 参数即确认（agent/脚本必给两参）
python sync.py to-dsh --source zcode,workbuddy --scope 7d          # 组合来源 + 最近 7 天
python sync.py to-dsh --source dsh --session <id> --scope all --apply   # 指定会话
python sync.py to-dsh --source all --scope all --apply --force --confirm-history [--budget 550000]    # 修复旧导入（整体重写；历史全量需确认）
python sync.py to-dsh --source all --scope all --apply --force --confirm-history --titles titles.json # 批量重命名（配合 titles.json）
# dsh 完整闭环 = to-dsh --apply → 完全退出 dsh → attach-dsh --apply（分组+标题） → 启动 dsh
python sync.py to-codex --source all --scope inc --apply   # 反向写入 codex（可 resume）
python sync.py to-claude --source all --scope inc --apply  # 反向写入 claude code
python sync.py to-hermes --source all --scope inc --apply  # 反向写入 hermes
python sync.py to-opencode --source all --scope inc --apply   # 反向写入 opencode（桌面/CLI 共库）
python sync.py to-workbuddy --source all --scope inc --apply  # 反向写入 workbuddy（db+jsonl 双写）
# 非 dsh 目标的『全部』默认含 dsh 自身（dsh 会话反向流出）；zcode 只读不可写（实证渲染 bug）
python sync.py pull --source all --scope inc      # A→C：各源 → 规范库 ~/.session-sync（安全，免退出任何应用）
python sync.py push --target codex --source all --scope inc --apply   # C→B：断点续推（中途换 agent 可继续）
# 架构 A→C→B：7 reader + 4 writer = 7×2 个适配器（而不是 7×6=42 条直连）；
# C 是本地纯一份规范副本（IR 全保真，工具往返不丢），pull 只读源、push 才写 agent
python sync.py attach-dsh                              # 挂分组+回填侧栏标题缓存（--apply 前必须退出 dsh）
python sync.py archive --source all --apply            # Markdown 归档到 ./archive
python sync.py prune                                   # 清理孤儿/打招呼会话（--apply 前退出 dsh，移入 ~/.trash-dsh 可恢复）
python sync.py verify                                  # 校验已导入 dsh 会话的事件纪律
python sync.py selftest                                # 沙箱端到端自检
python sync.py regtest                                 # 真库矩阵回归 dry-run（源×目标逐格探针计划）
python sync.py regtest --apply                         # 同上执行（退出全部目标应用；写1条+幂等+读回+防回环拦截验证）
tools/verify-dsh-backend.cmd                           # dsh 原生后端强校验（Node 22）
```

两道确认参数（to-dsh 必备；sync-finish 对应 `--sources`/`--scope`）：
`--source all|zcode,hermes,codex,workbuddy,claude,opencode`（确认1 来源区）、
`--scope inc|7d|30d|<N>d|all`（确认2 数据量；inc=仅增量，基准存于
`~/.dsh/sessions/.agentsync-state.json`，`--apply` 成功后推进，回看 15 分钟重叠；
all 或 inc 首跑=历史全量，`--apply` 需交互 y/N 或非交互 `--confirm-history`）。
大批量：候选 >15 时交互弹会话勾选清单（确认 3/3，回车=全部 / 编号多选）；
非交互需 `--confirm-batch` 或缩小范围（--session / --limit / 有界 --scope）。
过滤参数（to-dsh / archive 通用）：`--session <源ID子串,逗号分隔>`、`--cwd <路径子串>`、
`--since <天数>`、`--limit <每源数量>`。预算参数：`to-dsh --budget 200000` 超限时三层裁剪，
默认不裁。

## 同步语义

- **幂等**：dsh 侧会话 id = `import-<源ID slug>`，重复执行自动去重。
- **增量**：源会话新增了轮次再执行 to-dsh，只 append 新增轮次的事件（seq 自动续接）。
- **人在回路**：to-dsh / sync-finish 同步前两道确认——① 来源区（全部/单源/组合）
  ② 数据量（仅增量/最近 N 天/全部历史）。交互弹菜单，非交互参数即确认，缺参拒绝执行。
  **历史全量二次拦截**：`--scope all` 或 inc 首跑（无基准）在 `--apply` 时，交互弹 y/N
  （默认取消），非交互必须由人显式给 `--confirm-history`，否则拒绝；inc（有基准）与天数窗口不拦。
- **方向**：只写入 dsh；默认源 = zcode,hermes,codex,workbuddy,claude,opencode（不含 dsh 自身）。zcode 只出不进（写入方向已移除）。
- **工作区分区**：导入会话按源 cwd 落入 dsh 对应工作区分组（attach-dsh 挂载）。
  两条编码规则已对全量数据核对（zcode 5/5 工作区、dsh 349/349 会话）。hermes 无 cwd 的旧会话：
  dsh → `_no-cwd` 分组（dsh 原生语义），zcode → 用户主目录工作区。
- **可见性**：dsh 完整闭环 = 导入(to-dsh --apply) → 退出 dsh → 挂分组+标题缓存(attach-dsh --apply) → 启动 dsh；只导入不做第②步会堆在「未分组」且列表无标题。
  预期留在 dsh 未分组的：无 cwd 的源会话、cwd 已删、临时目录、cwd 嵌套在已有工作区下。

## 已知边界

- hermes 5 月的旧会话 cwd 为空，导入 dsh 会落在 `_no-cwd` 分组。
- codex 的 custom_tool_call 自由参数若无法解析为 JSON，会以原始文本保存（模型可读，格式稍异）。
- 极长会话建议 `--budget`（如 200000），否则 dsh resume 时可能超上下文。
- 环境依赖：Python 3.10+ 与 `zstandard`；Node 22（`nvm use 22`）仅 dsh 原生后端校验需要。
- dsh 源默认排除 origin=subagent 子代理会话（每次委派各落一个目录，侧栏隐藏；对齐 zcode/codex
  过滤口径）；openclaw 子代理靠首问 [Subagent Context] 标记同样默认排除；dashboard 展示口径
  含它们并带 🤖 徽章。
- 归档会话默认排除同步：zcode/hermes/workbuddy 的 UI 归档 + dsh workspace.json 的
  archivedSessionIds 软删名单，reader 层默认不返回（归档=已不要，不再外流）；
  webui 展示与 prune --pick 用 include_archived=True 仍可见（🗑 徽章，卡片/行级/名单同口径）。
- 导入会话不回流（防环）：codex/claude/hermes/workbuddy 铸的 sessionId 是 uuid5（原生非 v5），
  reader 层按版本位默认跳过；opencode 桌面版原生也是 uuidv5，改旁路清单 .agentsync-imports.json；
  dsh 默认排除 import-*（副本不当源，防二次成环）；审计/展示口径 include_imports=True（webui 📥 徽章）。
- zcode 永不写入（两连败定论：全量=超上下文黑屏；裁剪版也黑屏且无上下文——v2 注册表
  协同所致）。要带上下文进 zcode：`archive --source dsh --session <id>` 导出 Markdown
  让用户贴进新会话（项目级用 local/zcode-交接摘要.md 模式）。
