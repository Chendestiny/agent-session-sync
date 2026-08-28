# 🔄 跨 Agent 同步会话（agentsync）

📌 简体中文 | [🇬🇧 English](./README_EN.md)

把 **codex CLI / hermes / dsh(DeepSeek Harness) / zcode / workbuddy** 五家的会话记录归一到 **dsh**：
任何一家的历史会话都可以导入 dsh **继续对话**，并可导出统一的 **Markdown 归档**。
**其他agent 只出不进**（仅作为读取源；写入方向已移除——双端同对话易混乱，且实测活库写入
存在时间/渲染兼容问题）。

```
codex CLI ─┐
hermes    ─┤
dsh       ─┼─→ 归一化 IR（turns）─→ dsh   （可续聊，幂等+增量）
zcode     ─┤                    ─→ Markdown 归档（浏览/搜索）
workbuddy ─┘
```

设计参考 [Nwflower/dsh-chat-import](https://github.com/Nwflower/dsh-chat-import)（MIT，dsh 插件，
见 `reference/`），写入规格按本机安装的 dsh 0.1.1-rc / zcode 0.16.x 逐字段逆向核实。

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

### 🐌 GitHub 访问慢 / 不能翻墙？

**办法一：手动下载 zip（最稳，安装过程零联网）**

1. 任何能访问的时刻下载仓库 zip：仓库页 `Code` → `Download ZIP`，
   或用加速链（镜像站不保证长期可用，任选当时能下载的）：
   `https://ghfast.top/https://github.com/Chendestiny/agent-session-sync/archive/refs/heads/main.zip`
2. 解压 zip，进入 `agent-session-sync-main` 目录执行（脚本检测到旁边的 `sync.py` 就地安装，不再联网）：
   - Windows：`powershell -ExecutionPolicy Bypass -File .\install.ps1`
   - Linux / macOS / WSL：`bash install.sh`
3. 也可以完全不用脚本：把解压目录整个复制为 `~/.agents/skills/session-sync`
   （Windows 即 `%USERPROFILE%\.agents\skills\session-sync`），效果等价。

**办法二：镜像前缀在线装**（脚本支持 `ASS_GH_PREFIX` 环境变量拼在下载地址前；脚本本体仍需能访问 raw.githubusercontent.com，不行就用办法一）

```powershell
$env:ASS_GH_PREFIX = 'https://ghfast.top/'; irm https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.ps1 | iex
```

```bash
curl -fsSL https://raw.githubusercontent.com/Chendestiny/agent-session-sync/main/install.sh | ASS_GH_PREFIX=https://ghfast.top/ bash
```

安装脚本会把整个工具包落到 `~/.agents/skills/session-sync` 并注册为 skill——装完对它说以下任意一句（**建议带主语与意图的完整句**；纯「同步会话」四字在 skill 多、会话多的环境下可能检索慢或理解偏差）：

```text
用 session-sync skill 同步会话到 dsh，按它的纪律跑完闭环
```
或者
```text
同步会话：把各 agent 的会话全量增量导入 dsh，先跑 selftest 和 verify 自检
```

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
# ② 完全退出 dsh 后：挂工作区分组 + 回填侧栏标题缓存（新会话进分组且列表直接带标题）
python sync.py attach-dsh --apply
# ③ 启动 dsh：import-* 会话出现在对应工作区分组，可 resume 续聊

python sync.py archive  --source all --apply   # Markdown 归档 → ./archive
python sync.py verify                          # 校验已导入 dsh 会话
python sync-finish.py --check                   # 一键收尾·只读预览（prune/导入/挂载待办全貌）
python sync-finish.py                           # 一键收尾：先弹两道确认（来源区/数据量）→ prune+导入+挂载+校验
python sync-finish.py --sources zcode --scope 7d           # 参数即确认（非交互场景）
tools\verify-dsh-backend.cmd                   # 用 dsh 原生后端做强校验（Node 22）
```

> **dsh 侧完整闭环 = 导入(to-dsh) + 挂分组与标题缓存(attach-dsh) + 重启 dsh。** 只导入不做第②步，
> 会话会堆在「未分组」且列表不显示标题（点开才有）。批量改标题：编辑 `titles.json` 后
> `python sync.py to-dsh --source all --scope all --apply --force --titles titles.json --budget 550000`。
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
### ⚠️ 踩坑记录（都已修进代码 + 文档）

| 坑                                         | 现象                                                  | 修复                                                                                                                          |
| ------------------------------------------ | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `session/imported` 缺顶层 `ignorable:true` | 整条日志被拒、标题回退、全进未分组                    | 写入器补标记；校验器内置宿主事件词汇表规则                                                                                    |
| workspace 记录缺 `createdAt/updatedAt`     | dsh 启动直接失败（Zod 校验）                          | apply_attach 写全 5 键；schema 记入 docs/agents/dsh.md                                                                        |
| 嵌套路径工作区                             | dsh 启动时清理嵌套记录                                | attach 与 dsh 行为一致：嵌套 cwd 不建组                                                                                       |
| 向 zcode 写入会话                          | 时间显示异常（旧会话显示 1 分钟前）、部分会话渲染空白 | **方向整体移除**（zcode 只出不进）；已导入的 246 个会话已清理（识别规则：sess_+uuid5 版本位=5，备份 db.sqlite.cleanup-bak-*） |
| projcache 无 title 行                      | 列表不显示标题（显示工作区名），点开才有              | 侧栏标题读投影缓存而非日志；attach-dsh 现在同时回填 title 行                                                                  |
| 会话 id 命中归档列表                       | 数据四层全对但侧栏不渲染（删除→同 id 复活即隐身）     | prune/删除同步清 archivedSessionIds；复活历史归档需手工移除 id                                                                |
| 双向同步导致两边列表污染                   | 同一会话两边各一份，续聊即分叉                        | 产品决策改为单向：跨 agent → dsh；to-zcode 移除，写入器存档                                                                   |
| zcode 写入每会话备份一次                   | 一次导入产生百余个全量备份（23GB）                    | 已改为每次运行备份一次；存量备份已清理                                                                                        |

## 📂 目录结构

```
📖 AGENTS.md            AI agent 操作手册（交给 agent 读的入口）
🧩 SKILL.md             skill 封装（整目录即 skill bundle，junction 到 skills 目录）
⌨️ sync.py              CLI 入口（status/to-dsh/attach-dsh/archive/verify/selftest）
titles.json             会话标题覆盖表（{源ID: 新标题}，配合 to-dsh --force --titles 重写）
📐 docs/FORMATS.md      格式总览 + 归一化 IR + 索引
🔬 docs/agents/         各家会话结构详解（深度规格分册）
  dsh.md  zcode.md  hermes.md  codex.md  workbuddy.md
📑 examples/            真实示例：命令输出转录 + 转换实例（含再生成方法）
🛠️ tools/
  verify-dsh-backend.mjs   dsh 原生后端读回校验（Node 22+，先 nvm use 22）
  verify-dsh-backend.cmd   上者的 Windows 包装器（自动选 nvm 22.x）
📦 agentsync/
  paths.py              五家存储定位 + zcode project_id 规则
  model.py              归一化 IR + token 估算 + 三层预算裁剪
  readers.py            五家读取器（全部只读）
  dshwrite.py           dsh 事件合成 + 多帧 zstd 落盘（幂等+增量）+ 工作区挂载
  zcodewrite.py         [已废弃] zcode 写入历史实现，保留供参考（勿调用）
  archive.py            Markdown 归档
  validate.py           dsh 事件纪律校验
🗂️ archive/             归档输出
📚 reference/dsh-chat-import/  参考仓库源码
.test-dsh-root/ .test-zcode-db.sqlite   测试产物（可删）
```

## 🔒 安全边界

- 🔍 读取永远只读（sqlite `mode=ro` URI）。
- ✏️ 写 dsh：只新增 `import-*` 会话目录，不触碰原生会话文件。
- 🚫 **不写 zcode/hermes/codex/workbuddy 的存储**（zcode 写入方向已于 2026-08-26 移除）。
- ♻️ 恢复方法：zcode db 异常时，用 `db.sqlite.cleanup-bak-*` / `db.sqlite.agentsync-bak-*`
  覆盖回原文件（需退出 zcode）。
