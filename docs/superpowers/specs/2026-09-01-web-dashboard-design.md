# Web Dashboard 设计（v0.4.0 · 只读可视化）

- 日期：2026-09-01
- 状态：设计已与用户逐节确认，待实施
- 范围：只读可视化 + 轮次下钻；写操作（apply 进页面）明确留给 v2

## 1. 背景与动机

CLI 的三道人工确认已稳定，但"看"的成本高：会话分布、时间戳健康、同步水位都要靠命令行输出脑补。
Turn.time 压平 bug（56 轮挤在同一时刻）若有时间轴视图一眼可见。
目标：一个本地 web dashboard，把 7 家源 + C store + 水位线画出来——人能看、能下钻、能验证。

## 2. 选型决策（已定）

| 决策点 | 结论 | 理由 |
|---|---|---|
| TUI vs Web | Web（浏览器） | 本工具是管理式交互而非对话式；时间轴/勾选清单/同步拓扑是 web 强项；绕开 Windows 终端编码渲染坑 |
| Node vs Python | Python | 7 readers / 6 writers / 三道门 / 水位线全是现成 Python；换 Node 等于整体重写、零收益 |
| HTTP 层 | stdlib `http.server` | 零新依赖，保持 clone 即用；7 家 store 中 5 家是 SQLite，stdlib `sqlite3` 已覆盖 |
| 前端 | 单 HTML 文件，内联 CSS/JS | 无构建步骤、无 CDN、离线可用 |
| v1 写操作 | 无（零 POST 端点） | 物理上不可写，安全即设计 |

## 3. 形态与入口

- `sync.py serve [--port N]`（默认 8321），仅绑定 `127.0.0.1`
- 启动成功后（Windows）自动 `start http://127.0.0.1:N` 打开浏览器；Ctrl+C 退出

### 与 skill / CLI 的关系（一份代码、两个入口）

- skill 皮：SKILL.md 教 agent 调 `sync.py` CLI（机器用）
- web 皮：`sync.py serve` → 浏览器 dashboard（人用）
- 两者互不依赖、可单独使用；都只共享底层 `agentsync` 包与磁盘数据
- 分发上没有"基础版/全量版"：install.ps1 / git clone 本来就是完整工具包，web 随包附带，
  skill 目录复制时自动带上（新增 `agentsync/webui/` 需确认被 install.ps1 的复制范围覆盖）
- web 不绑定任何 agent：本地 HTTP 服务直接读文件与 SQLite，不需要任何 agent 在线，也不需要安装 skill

## 4. 视图（三个）

### 4.1 总览
- 每源一张卡：名称、会话数、最近活跃、根路径健康灯（绿=可读 / 红=根缺失）
- C store 卡：会话条数、最近 pull/push 水位线
- 底部 7 泳道时间轴：每源一行；会话条的位置=createdAt、宽度=首末轮跨度、颜色=源

### 4.2 会话列表
- 统一大表：源 / 标题 / 创建时间 / 最后活动 / 轮数
- 筛选：源多选、时间范围、关键词
- 点击行下钻

### 4.3 会话详情
- 头部：源、标题、时间跨度、轮数、id
- 轮次时间条：每轮一根横条，位置=`Turn.time`（时间戳压平类 bug 的一眼验证点）
- 轮次列表：角色 / 时间 / 文本预览 / 工具调用摘要

## 5. API（v1 全只读）

```
GET /                单页应用（index.html）
GET /api/overview    {sources:[{name,count,last_active,ok,path}], store:{...}, state:{水位线}}
GET /api/sessions    ?source=&q=&from=&to=  → 会话 meta 数组
GET /api/session     ?id=&source=           → 完整 IR JSON（复用 store.session_to_dict）
```

## 6. 数据流

- 每次请求实时跑 readers（与 `sync.py status` 同路径），本地磁盘几百条量级已验证够快
- 无缓存层、无失效策略：数据永远新鲜
- 并发：`ThreadingHTTPServer`；v1 只读，与 CLI 同跑天然无冲突

## 7. 安全

- 仅绑定 127.0.0.1；零写端点；无外部 CDN、无遥测；完全离线可用

## 8. 测试

- selftest 新增第 9 节：随机端口起服务 → 依次请求 3 个 API 端点 → 断言 JSON 形状
  （源卡字段齐全、会话数组可空但结构对、详情含 turns）→ 关闭服务

## 9. 文件落点

- `agentsync/webui.py`：server + 路由
- `agentsync/webui/index.html`：单文件页面（随包分发）
- `sync.py`：新增 `serve` 子命令
- README（双语）与 docs：新增 web dashboard 一节
- SKILL.md 主线不动（skill 面向 agent 用 CLI；serve 是给人用的）

## 10. 明确不做（v1）

- 任何写操作 / apply 进页面（v2 连同三道确认 GUI 化一起做）
- CLI 与 web 的写互斥锁（v1 只读不需要，v2 引入写时补）
- 账号体系、多主题、历史回放、Textual 终端版
