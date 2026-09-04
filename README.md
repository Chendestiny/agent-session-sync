# 🔄 跨 Agent 同步会话（agentsync）

📌 简体中文 | [🇬🇧 English](./README_EN.md)

十九家 agent 一张网：**15 家会话全互通**——codex CLI / hermes / dsh(DeepSeek Harness) / zcode / workbuddy / claude code / opencode / qoder / openclaw / cursor / trae / MiniMax Code / Pi Agent / Gemini CLI / Cline（trae CN 版正文库自加密暂不可读）；**4 张占位卡**——grok / mimo / kimi / copilot（路径与格式已源码核验，装好即接）。
任何一家的历史会话都可以导入其余各家**继续对话**，并可导出统一的 **Markdown 归档**。
单向归一（A→C→B：15 读 + 10 写 + 4 占位 = 19 卡，而非两两直连）。

| A · 读取源（15 家 + 4 占位） | C · 归一化 | B · 写入目标（10 家 + 归档） |
|---|---|---|
| codex CLI · hermes · dsh · zcode（只出不进） · workbuddy · claude code · opencode · qoder · openclaw · cursor · trae · MiniMax Code · Pi Agent · Gemini CLI · Cline（grok/mimo/kimi/copilot 占位待实装） | IR（turns）＋ 规范库 `~/.session-sync`（pull/push 断点续推） | dsh（可续聊，幂等+增量） · codex · claude code · hermes · opencode · workbuddy · MiniMax Code · Pi Agent · Gemini CLI · Cline ＋ Markdown 归档（浏览/搜索） |

## 📋 前置条件
环境要求：Python 3.10+ 与 `zstandard`；dsh 原生后端校验需要 **Node 22+**（`nvm use 22`，
`🛠️ tools/verify-dsh-backend.cmd` 会自动优先选 nvm 的 22.x，无需手动切换）。

## 🚀 快速开始
### 🗣️ 对任意 agent 一句话开始（零手动配置）
按你的平台，把下面这句发给任何一个能联网 + 能执行命令的 agent（dsh / zcode / hermes / Claude 都行）：

Windows（PowerShell）：

```text
帮我安装 agent-session-sync：irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
```

Linux / macOS / WSL：

```text
帮我安装 agent-session-sync：curl -fsSL https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.sh | bash
```

> WSL/Linux 下自动只发现**该系统内**安装的 agent（如 `~/.codex`、`~/.dsh`）；
> zcode / hermes / workbuddy 装在 Windows 侧的，请在 Windows 上跑同步。
>
> **skills 目录自动桥接**：各 agent 只扫自家 skills 目录（如 `~/.workbuddy/skills`、
> `~/.claude/skills`、`~/.codex/skills`、`~/.hermes/skills`、`~/.dsh/skills`），大多不认通用位
> `~/.agents/skills`。安装脚本会在每个检测到的自家 skills 目录里放一个指向唯一源
> `~/.agents/skills/session-sync` 的 junction/symlink——单一源、全家电齐、升级改一处生效
> （桥接后需重启对应 agent 才会重新扫描）。
>
> **没有 Python 环境也能装**（Windows）：安装器检测不到可用的 `python` 时，自动下载官方
> 嵌入式 CPython 到 `~/.agents/py-runtime`（免管理员、不改系统、约 12 MB），装好 pip 与
> zstandard，shim 直接指向它，并把它加进用户 PATH——agent 里的 `python sync.py` 也能跑。

### ▶️ 执行同步（装完对 agent 说一句）
安装脚本（含下面的离线办法）会把整个工具包落到 `~/.agents/skills/session-sync` 并注册为 skill——装完对它说以下任意一句（**建议带主语与意图的完整句**；纯「同步会话」四字在 skill 多、会话多的环境下可能检索慢或理解偏差）：

```text
用 session-sync skill 同步会话到 dsh，按它的纪律跑完闭环
```
或者
```text
同步会话：把各 agent 的会话全量增量导入 dsh，先跑 selftest 和 verify 自检
```

## 🖥️ 可视化（Web Dashboard）

**方式一 · 全局命令**：

```bash
ass web                        # 快捷命令（= session-sync web）自动开浏览器 127.0.0.1:8321（--port 可改，Ctrl+C 停）
```

**方式二 · 从源码跑**（没装 skill / 刚 clone）：

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard           # 唯一第三方依赖（读 dsh 源用；其余源纯标准库）
python sync.py web
```

| 视图 | 看什么 |
|---|---|
| 总览 | 19 张源卡官方图标两行跑马灯（健康灯/实时会话数/🔒加密阻断标记）+ 源名检索框 + C 库水位线独行 + 会话时间轴盒内竖滚（整页零滚动条） |
| 会话列表 | 按源/日期/关键词筛选，点行下钻 |
| 会话详情 | 轮次时间条（时间戳是否压平一眼可见）+ 轮次与工具调用明细 |

全只读：零写端点（POST 一律 405）、仅绑定 127.0.0.1、页面离线可用（无 CDN）、零新依赖（标准库 HTTP 服务）；
数据实时读源、无缓存。

## 💬 更多触发语句

```text
把 demo 项目工作区里的 hermes 会话也同步进 dsh
```

```text
把 dsh 里已导入的会话导出一份 Markdown 归档到 archive 目录
```

```text
清理 dsh 里的导入会话：来源已删除的孤儿、纯打招呼和冒烟测试的都清掉
```

agent 会按 `SKILL.md` 的纪律执行：selftest → dry-run → 确认 → apply → **二次验证**。

**同步完的二次验证**：`python sync.py verify` 全过 → 退出 dsh 后 `attach-dsh --apply` → 重启 dsh，会话出现在对应分组、带 `[来源]` 标题、可续聊。

也可以直接克隆使用：

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard && python sync.py selftest
```

## ⌨️ 具体脚本
```bash
cd "<项目目录>" && pip install zstandard

python sync.py selftest && python sync.py status   # 沙箱自检全绿 → 各源概览
python sync.py doctor          # 一键体检+自修复（依赖/基准/桥接/防环清单审计）
python sync.py to-dsh          # 交互弹两道确认后 dry-run；非交互必给 --source/--scope（参数即确认）
python sync.py to-dsh   --source all --scope inc --apply   # ① 导入（agent/脚本必给两参）
python sync.py attach-dsh --apply                          # ② 完全退出 dsh 后：挂分组+回填标题
python sync.py to-codex --source zcode --scope inc --apply # 反向写入；to-claude/-hermes/-opencode/-workbuddy/-minimax/-pi/-gemini/-cline 同款
python sync.py pull --source all --scope inc               # A→C：→ ~/.session-sync（安全免退出）
python sync.py push --target codex --apply --scope inc     # C→B：断点续推（中断重跑即续）
python sync.py backup --source all          # 会话快照 → C 库 backups/（--with-imports/--scope/--list）
python sync.py restore --source claude --ts <快照> --target dsh --apply   # 幂等还原（默认 dry-run）
python sync.py regtest --apply              # 真库矩阵回归：每格写1条+幂等复跑+读回+防环拦截（退出目标应用）
python sync.py web                          # 可视化 dashboard（127.0.0.1:8321）
python sync-finish.py                       # 一键收尾：确认 → prune+导入+挂载+校验（--check 只读预览）
python sync.py prune --session "标题或id子串" --hard --apply   # dsh 瘦身点名删除（--pick 交互勾选）
```

> **dsh 完整闭环 = ①导入 → ②退出 dsh 后 attach-dsh → ③重启**，少第②步会堆「未分组」且无标题。
> 批量改标题：`titles.json` + `to-dsh --force --titles titles.json`。
>
> AI agent 操作手册：**AGENTS.md** 是给 agent 看的完整入口（cookbook / 安全铁律 / 故障排查 / 升级适配）。

## 🧩 作为 skill 使用（整目录即 skill bundle）

整目录即 skill 包。装好后在任何 agent 里说一句“同步一下会话”，agent 按 SKILL.md 纪律执行
（selftest → dry-run → 确认 → apply）；手动挂载：`mklink /J "%USERPROFILE%\.agents\skills\session-sync" "<项目目录>"`。
通用过滤：`--session/--cwd/--since/--limit`；`to-dsh --budget <tokens>` 超长会话三层裁剪保续聊。

## 🧪 发布前验证概览

以下能力均在真实数据上验证通过（方法见各文档，可在你机器复跑）：

- ✅ **15 家读取**全部真库验证：工具调用往返 / reasoning / 失败态 / 分区编码全量比对零偏差
- ✅ **10 家写入**真库落盘：minimax 经 UI 验收；dsh 用原生后端读回 100%（tools/verify-dsh-backend.cmd 可复跑）
- ✅ **回归双保险**：194 项沙箱自检 + 48 格真库矩阵回归（每格写 1 条 + 幂等复跑 + 读回 + 防环拦截）
- ✅ **幂等增量**：重复导入去重；源会话增长只追加新轮次
- ✅ **防环三件套**：uuid5 版本位 / 旁路清单（doctor 反推法自动审计）/ import-* 前缀；导入一律带 `[来源]` 标题
- ✅ **备份还原 + 超长裁剪**：IR 快照跨家幂等还原、加密源（trae）转原始库快照、三层预算保续聊

### ⚠️ 踩坑记录

62 条实测坑（dsh 投影缓存 / codex threads 索引 / opencode 事件溯源 / gemini 流式碎片等）
全部修进代码，明细见 **[docs/pitfalls.md](docs/pitfalls.md)**（按家分组，含修复方案）。

## 📂 目录结构

```
📖 AGENTS.md            AI agent 操作手册（交给 agent 读的入口）
🧩 SKILL.md             skill 封装（整目录即 skill bundle，junction 到 skills 目录）
⌨️ sync.py              CLI 入口（status/web/doctor/to-*/attach-dsh/backup/restore/regtest/prune/archive/verify/selftest）
titles.json             会话标题覆盖表（{源ID: 新标题}，配合 to-dsh --force --titles 重写）
📐 docs/FORMATS.md      格式总览 + 归一化 IR + 索引
🔬 docs/agents/         各家会话结构详解（一家一册，15 读写 + 4 占位）
📑 examples/            真实示例：命令输出转录 + 转换实例（含再生成方法）
🛠️ tools/
  verify-dsh-backend.mjs   dsh 原生后端读回校验（Node 22+，先 nvm use 22）
  verify-dsh-backend.cmd   上者的 Windows 包装器（自动选 nvm 22.x）
📦 agentsync/
  paths.py              各家存储定位 + zcode project_id 规则
  model.py              归一化 IR + token 估算 + 三层预算裁剪
  readers.py            十五家读取器 + 4 张占位源（19 卡全景，全部只读）
  dshwrite.py           dsh 事件合成 + 多帧 zstd 落盘（幂等+增量）+ 工作区挂载
  zcodewrite.py         [已废弃] zcode 写入历史实现，保留供参考（勿调用）
  archive.py            Markdown 归档
  validate.py           dsh 事件纪律校验
  webui/                Web dashboard（web 子命令 → 127.0.0.1:8321；POST 仅目录绑定族例外，页面随包离线可用）
🗂️ archive/             归档输出
📚 reference/dsh-chat-import/  参考仓库源码
.test-dsh-root/ .test-zcode-db.sqlite   测试产物（可删）
```

## 🔒 安全边界

- 🔍 读取永远只读（sqlite `mode=ro` URI）。
- ✏️ 写入目标（dsh / codex / claude code / hermes / opencode / workbuddy / minimax / pi / gemini / cline）：只新增幂等导入会话
  （`import-*` / uuid5 id），不触碰原生会话，每次写入前自动备份。
- 🚫 **zcode 暂不写入**（写入方向已于 2026-08-26 移除，写入器存档于 `zcodewrite.py`）。
- ♻️ 恢复方法：存储异常时，用 `*.agentsync-bak-*` 自动备份覆盖回原文件（需退出对应应用）。

---

设计参考 [Nwflower/dsh-chat-import](https://github.com/Nwflower/dsh-chat-import)（MIT，dsh 插件，
见 `reference/`），写入规格按本机安装的 dsh 0.1.1-rc / zcode 0.16.x 逐字段逆向核实。
