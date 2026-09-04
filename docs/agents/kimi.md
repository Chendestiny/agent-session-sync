# Kimi Code 会话结构详解（占位：待实装核验）

核实基线：源码核验（D:\Project_github\kimi-code-main，@moonshot-ai/kimi-code
monorepo）。本机未装，reader/writer 待实装。

## 1. 存储布局（源码实锤）

```
~\.kimi-code\                 ← $KIMI_CODE_HOME 可覆盖（注意不是 ~/.kimi）
├── logs\ cache\ updates\ bin\
└── （会话在自研 minidb 里，非 sqlite 非 jsonl）
```

- 持久层：`packages/minidb`——自研嵌入式库（内存层 TranscriptStore + 分段外部
  归并，worker 线程），格式细节源码体量大，实装后按真实落盘逆向更划算
- 事件层：`packages/transcript`（contract/events schema + store/history 视图）
- `packages/migration-legacy` 处理旧版 `~/.kimi`（md5 桶目录 + kimi.json）迁移
  ——旧格式的存在意味着曾有 jsonl 形态，实装时可对照

## 2. 接入计划

实装后发一条测试会话 → 直接看 `~/.kimi-code` 真实落盘（minidb 的文件名/编码
从数据反推）→ read_kimi；写入可行性取决于 minidb 是否有外部可写的追加接口。
