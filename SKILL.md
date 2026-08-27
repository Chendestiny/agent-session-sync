---
name: session-sync
description: 跨 Agent 会话同步（codex/hermes/dsh/zcode/workbuddy 等 agent → dsh 单向 + Markdown 归档）。当用户要"同步会话 / 导入会话 / 迁移会话 / 把 X 的会话搬到 Y / 归档会话 / 在 dsh 里继续另一家的会话"时使用。本目录即完整工具包：sync.py 为 CLI，AGENTS.md 为完整操作手册。
---

# 跨 Agent 会话同步（session-sync）

本 skill 目录是一个自洽工具包：读取 codex / hermes / dsh / zcode / workbuddy 五家会话，
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
3. **默认 dry-run**：所有写命令先不带 `--apply` 跑一遍，把计划给用户看过再落盘。
4. 出错就停下报告，不要猜测性重试。
6. **同步完必须二次验证**：`verify` 通过 + 抽查落盘文件三要素（imported 标记含 ignorable、
   [来源]前缀标题、分区目录=cwd编码）+ `attach-dsh` 后重启 dsh 目视复核；
5. **需要跑 node 的校验前先 `nvm use 22`**（node:zlib 的 zstd API 要求 Node 22+；
   `tools/verify-dsh-backend.cmd` 已内置自动选择 nvm 22.x，可直接调用）。

## 常用命令

```bash
cd "<本目录>"                     # 路径含空格，务必带引号
python sync.py status                                  # 各 agent 源概览
python sync.py to-dsh                                  # 计划（dry-run，默认全部已探测源）
python sync.py to-dsh --source zcode --apply --budget 550000   # 落盘：导入到 dsh
python sync.py to-dsh --source dsh --session <id> --apply   # 指定会话
python sync.py to-dsh --apply --force [--budget 550000]    # 修复损坏的旧导入（整体重写）
python sync.py to-dsh --apply --force --titles titles.json # 批量重命名（配合 titles.json）
# dsh 完整闭环 = to-dsh --apply → 完全退出 dsh → attach-dsh --apply（分组+标题） → 启动 dsh
python sync.py attach-dsh                              # 挂分组+回填侧栏标题缓存（--apply 前必须退出 dsh）
python sync.py archive --source all --apply            # Markdown 归档到 ./archive
python sync.py prune                                   # 清理孤儿/打招呼会话（--apply 前退出 dsh，移入 ~/.trash-dsh 可恢复）
python sync.py verify                                  # 校验已导入 dsh 会话的事件纪律
python sync.py selftest                                # 沙箱端到端自检
tools/verify-dsh-backend.cmd                           # dsh 原生后端强校验（Node 22）
```

过滤参数（to-dsh / archive 通用）：`--session <源ID子串,逗号分隔>`、`--cwd <路径子串>`、
`--since <天数>`、`--limit <每源数量>`。预算参数：`to-dsh --budget 200000` 超限时三层裁剪，
默认不裁。

## 同步语义

- **幂等**：dsh 侧会话 id = `import-<源ID slug>`，重复执行自动去重。
- **增量**：源会话新增了轮次再执行 to-dsh，只 append 新增轮次的事件（seq 自动续接）。
- **方向**：只写入 dsh；默认源 = zcode,hermes,codex,workbuddy（不含 dsh 自身）。zcode 只出不进（写入方向已移除）。
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
