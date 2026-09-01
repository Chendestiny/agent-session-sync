# 🔄 跨 Agent 同步会话（agentsync）

📌 简体中文 | [🇬🇧 English](./README_EN.md)

七家会话互通：**codex CLI / hermes / dsh(DeepSeek Harness) / zcode / workbuddy / claude code / opencode**。
任何一家的历史会话都可以导入其余各家**继续对话**，并可导出统一的 **Markdown 归档**。
单向归一（A→C→B：7 读 + 6 写，而非 7×6 条直连）。

| A · 读取源（7 家） | C · 归一化 | B · 写入目标（6 家 + 归档） |
|---|---|---|
| codex CLI · hermes · dsh · zcode（只出不进） · workbuddy · claude code · opencode | IR（turns）＋ 规范库 `~/.session-sync`（pull/push 断点续推） | dsh（可续聊，幂等+增量） · codex · claude code · hermes · opencode · workbuddy ＋ Markdown 归档（浏览/搜索） |

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

### ▶️ 执行同步（装完对 agent 说一句）
安装脚本（含下面的离线办法）会把整个工具包落到 `~/.agents/skills/session-sync` 并注册为 skill——装完对它说以下任意一句（**建议带主语与意图的完整句**；纯「同步会话」四字在 skill 多、会话多的环境下可能检索慢或理解偏差）：

```text
用 session-sync skill 同步会话到 dsh，按它的纪律跑完闭环
```
或者
```text
同步会话：把各 agent 的会话全量增量导入 dsh，先跑 selftest 和 verify 自检
```

## 🖥️ 只读可视化（Web Dashboard）

**方式一 · 全局命令**：

```bash
session-sync serve              # 自动开浏览器 127.0.0.1:8321（--port 可改，Ctrl+C 停）
```

**方式二 · 从源码跑**（没装 skill / 刚 clone）：

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard           # 唯一第三方依赖（读 dsh 源用；其余源纯标准库）
python sync.py serve
```

| 视图 | 看什么 |
|---|---|
| 总览 | 7 家源健康灯/实时会话数 + C 库水位线 + 7 泳道会话时间轴（位置=创建时间，宽度=跨度） |
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

**同步完的二次验证**（粘贴给 agent 即可让它自证）：

```text
按 SKILL.md 做发布后二次验证：
1. python sync.py verify                     # 全部导入会话事件纪律通过
2. 抽查刚导入会话的落盘文件三要素：session/imported 标记(ignorable=true)、
   [来源] 前缀标题、分区目录名与头部 cwd 编码一致
3. 退出 dsh 后 python sync.py attach-dsh --apply 回填分组+projcache 标题行，
   重启 dsh 后确认：出现在对应工作区分组、列表带标题、点开可续聊
```

也可以直接克隆使用：

```bash
git clone https://github.com/Chendestiny/agent-session-sync && cd agent-session-sync
pip install zstandard && python sync.py selftest
```

> AI agent 操作手册：**AGENTS.md** 是给 agent 看的完整入口（cookbook / 安全铁律 /
> 故障排查 / 升级适配），交给任何 agent 读它即可。

## ⌨️ 具体脚本
```bash
cd "<项目目录>"
pip install zstandard        # Python 侧唯一第三方依赖
nvm use 22                   # node 相关校验用（可选）

python sync.py selftest                        # 沙箱自检：全绿再动真数据
python sync.py status                          # 各 agent 源概览
python sync.py to-dsh                          # 交互：弹两道确认（来源区→数据量）后 dry-run
python sync.py to-dsh   --source zcode --scope 7d          # 参数即确认：zcode + 最近 7 天
python sync.py to-dsh   --source all --scope inc --apply   # ① 导入到 dsh（agent/脚本必给两参）
python sync.py to-codex --source zcode --scope inc --apply  # 反向写入 codex（to-claude/to-hermes/to-opencode/to-workbuddy 同款）
python sync.py pull --source all --scope inc                # A→C：各源 → 规范库 ~/.session-sync（安全免退出）
python sync.py push --target codex --apply --scope inc      # C→B：断点续推（中途换 agent 可继续）
# ② 完全退出 dsh 后：挂工作区分组 + 回填侧栏标题缓存（新会话进分组且列表直接带标题）
python sync.py attach-dsh --apply
# ③ 启动 dsh：import-* 会话出现在对应工作区分组，可 resume 续聊

python sync.py archive  --source all --apply   # Markdown 归档 → ./archive
python sync.py verify                          # 校验已导入 dsh 会话
python sync.py serve                           # 只读可视化 dashboard：总览时间轴/会话列表/轮次时间条（浏览器自动开 127.0.0.1:8321）
python sync-finish.py --check                   # 一键收尾·只读预览（prune/导入/挂载待办全貌）
python sync-finish.py                           # 一键收尾：先弹两道确认（来源区/数据量）→ prune+导入+挂载+校验
python sync-finish.py --sources zcode --scope 7d           # 参数即确认（非交互场景）
python sync-finish.py --sources all --scope all --confirm-history   # 历史全量需显式确认
python sync.py prune --session "标题或id子串" --hard --apply          # dsh 瘦身：点名直接删除（先跑 dry-run 看命中；--older-than N 限天数；--native 连原生会话点名）
tools\verify-dsh-backend.cmd                   # 用 dsh 原生后端做强校验（Node 22）
```

> **dsh 侧完整闭环 = 导入(to-dsh) + 挂分组与标题缓存(attach-dsh) + 重启 dsh。** 只导入不做第②步，
> 会话会堆在「未分组」且列表不显示标题（点开才有）。批量改标题：编辑 `titles.json` 后
> `python sync.py to-dsh --source all --scope all --apply --force --confirm-history --titles titles.json --budget 550000`。
>
> AI agent 操作手册：**AGENTS.md** 是给 agent 看的完整入口（cookbook / 安全铁律 /
> 故障排查 / 升级适配），交给任何 agent 读它即可。

## 🧩 作为 skill 使用（整目录即 skill bundle）

本目录本身就是 skill 包（`SKILL.md` + `sync.py` + `📦 agentsync/` + `🛠️ tools/` + `docs/`），
用 junction 链接到 skills 目录（免管理员权限）：

```bat
mklink /J "%USERPROFILE%\.agents\skills\session-sync" "<项目目录>"
```

之后在 zcode / dsh / hermes 里说一句"同步一下会话"，agent 按 SKILL.md 的纪律执行
（selftest → dry-run → 确认 → apply）。

通用过滤：`--session <源ID子串>` `--cwd <路径子串>` `--since <天数>` `--limit <N>`；
`to-dsh --budget <tokens>` 对超长会话做三层裁剪保续聊。

## 🧪 发布前验证概览

以下能力均在真实数据上验证通过后发布（方法见各文档，可在你机器复跑）：

- ✅ 跨 agent 读取：codex / hermes / dsh / zcode / workbuddy 全部解析正常（含工具调用、reasoning、失败态、图片占位）
- ✅ dsh 写入：使用 dsh 自带 JsonlSessionPersistence 后端读回校验 100% 通过（🛠️ tools/verify-dsh-backend.cmd 可复跑）
- ✅ 工作区分区编码：projectKey / project_id 规则与各家原生行为全量比对零偏差
- ✅ 幂等与增量：重复导入自动去重；源会话增长后仅追加新轮次且 seq 连续
- ✅ 超长会话三层预算裁剪保续聊；zcode 写入器已在 db 副本上完成 round-trip 回归
### ⚠️ 踩坑记录

12+ 条实测坑（dsh 投影缓存 / codex threads 索引 / hermes 计数列 / opencode 项目上下文等）
全部修进代码，明细见 **[docs/pitfalls.md](docs/pitfalls.md)**（按家分组，含修复方案）。

## 📂 目录结构

```
📖 AGENTS.md            AI agent 操作手册（交给 agent 读的入口）
🧩 SKILL.md             skill 封装（整目录即 skill bundle，junction 到 skills 目录）
⌨️ sync.py              CLI 入口（status/serve/to-dsh/attach-dsh/archive/verify/selftest）
titles.json             会话标题覆盖表（{源ID: 新标题}，配合 to-dsh --force --titles 重写）
📐 docs/FORMATS.md      格式总览 + 归一化 IR + 索引
🔬 docs/agents/         各家会话结构详解（深度规格分册）
  dsh.md  zcode.md  hermes.md  codex.md  workbuddy.md
📑 examples/            真实示例：命令输出转录 + 转换实例（含再生成方法）
🛠️ tools/
  verify-dsh-backend.mjs   dsh 原生后端读回校验（Node 22+，先 nvm use 22）
  verify-dsh-backend.cmd   上者的 Windows 包装器（自动选 nvm 22.x）
📦 agentsync/
  paths.py              各家存储定位 + zcode project_id 规则
  model.py              归一化 IR + token 估算 + 三层预算裁剪
  readers.py            七家读取器（全部只读）
  dshwrite.py           dsh 事件合成 + 多帧 zstd 落盘（幂等+增量）+ 工作区挂载
  zcodewrite.py         [已废弃] zcode 写入历史实现，保留供参考（勿调用）
  archive.py            Markdown 归档
  validate.py           dsh 事件纪律校验
  webui/                只读 Web dashboard（serve → 127.0.0.1:8321；零写端点，页面随包离线可用）
🗂️ archive/             归档输出
📚 reference/dsh-chat-import/  参考仓库源码
.test-dsh-root/ .test-zcode-db.sqlite   测试产物（可删）
```

## 🔒 安全边界

- 🔍 读取永远只读（sqlite `mode=ro` URI）。
- ✏️ 写入目标（dsh / codex / claude code / hermes / opencode / workbuddy）：只新增幂等导入会话
  （`import-*` / uuid5 id），不触碰原生会话，每次写入前自动备份。
- 🚫 **zcode 暂不写入**（写入方向已于 2026-08-26 移除，写入器存档于 `zcodewrite.py`）。
- ♻️ 恢复方法：存储异常时，用 `*.agentsync-bak-*` 自动备份覆盖回原文件（需退出对应应用）。

---

设计参考 [Nwflower/dsh-chat-import](https://github.com/Nwflower/dsh-chat-import)（MIT，dsh 插件，
见 `reference/`），写入规格按本机安装的 dsh 0.1.1-rc / zcode 0.16.x 逐字段逆向核实。
