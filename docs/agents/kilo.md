# Kilo CLI 会话结构详解

核实基线：本机 Kilo CLI 实测（npm `@kilocode/cli` v7.6.2，2026-09-11；探针会话=BI_frontend
「销售需求单AI推荐」327 消息 / 332 工具调用 → kilo.db `ses_3e7b666d`）。
**当前状态：读写源已实装**（读取器 `readers.read_kilo` = read_mimo 泛化；写入器
复用 `opencodewrite`（三表同构零改动），CLI `to-kilo` / `push --target kilo` 已接，
探针真库写入 + 双口径读回已验收）。

## 1. 存储布局

```
~/.config/kilo/                    ← 配置根（fork 了 opencode 的配置层）
├── kilo.jsonc                     ← config.jsonc 同款：provider 注册表（ai-sdk 形状）
└── .gitignore
~/.local/share/kilo/               ← 数据根
├── kilo.db (+ wal/shm)            ← ★ 正典（SQLite，opencode/mimo 同构）
└── storage/session_diff/<sid>.json
```

## 2. schema（三表同构，mimo 判别法验证）

- session：id=`ses_`+nanoid、project_id、directory=**cwd（直接给绝对路径）**、title、
  `model` 列（JSON：`{"id","providerID","variant"}`——比 mimo 多，比 message.data 优先）、
  time_created/time_updated/time_archived（归档默认排除）、slug/version/cost/tokens_*
- message：id=`msg_`、session_id、data JSON（role/agent/model 等）
- part：id=`prt_`、message_id、data JSON（text/reasoning/tool/step-finish）
- 注册表族：session_input/session_context_epoch/columnar 等——reader 不用
- 防环：`ses_` nanoid 非 uuid5，嵌入旁路清单判别（同 mimo 剧本）

## 3. 写入（opencodewrite 直接复用）

`kilo.db` 与 opencode.db 三表同构到**写入器零改动可吃**的程度：`plan_write(db_path=…)`
签名一致、墓碑/旁路清单放 db 同目录、`s[U]IMPORTS` 旁路登记同步生效。实机验收：
dsh「销售需求单AI推荐」（10 轮 / 532 events）写入后默认口径正确排除（防 dsh→kilo→dsh），
审计口径含导入可见。注意：写入前确认 kilo/daemon 已退出；kilo 侧消息模型列为 opencode
同款 JSON（`providerID`/`modelID`）。

## 4. 模型层（与阿里云 Token Plan）

Kilo 进了百炼 Token Plan 官方「接入模型」列表；provider 配置照 `~/.minimax/config.yaml`
同款 ai-sdk 形状写入 ~/.config/kilo/kilo.jsonc——2026-09-11 本机已配 `bailian-token-plan`
（Anthropic 兼容端点 + sk-sp Key，18 模型注册全过），`kilo run` 连通验证通过
（BI_frontend 发「你好」自动识别项目上下文）。Key 是组织维度，泄露需在 Token Plan
控制台重置（on-board 脚本在 `local/kilo-onboard.py`）。

## 5. 读取策略（read_kilo = read_mimo 泛化）

`read_mimo(mimo_home, source="kilo", dbname="kilo.db")` 直调；注入剥离沿用
`_claude_strip_injection`（kilo 的 user message 也内嵌上下文注入块），TEMP 冒烟排除同款。
