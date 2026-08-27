# examples/ — 使用与转换示例

| 文件 | 内容 |
|---|---|
| `命令输出示例.md` | 命令的真实运行转录（selftest/status/dry-run/apply/幂等/双层校验/归档/attach-dsh 挂分组+标题回填），各段附要点 |
| `转换示例1-zcode到dsh.md` | 一条真实 zcode 会话导入 dsh 后的**完整事件日志**（解压后逐行 + 注释） |

配合阅读：`../AGENTS.md`（操作手册）、`../docs/FORMATS.md`（格式规格）。

## 如何重新生成这些示例

示例来自真实数据，全部可复现（在项目根目录执行）：

```bash
# 转换示例 1 的素材：导入 zcode 最小会话到测试根
python sync.py to-dsh --session sess_e5ffd0df --root .test-dsh-root --apply

# 命令转录：照 `命令输出示例.md` 里的命令原样跑即可
python sync.py selftest && python sync.py status && python sync.py to-dsh --limit 2
```

> 注意：`.test-dsh-root/`、`.test-zcode-db.sqlite` 是测试产物（已被 .gitignore 忽略）。
