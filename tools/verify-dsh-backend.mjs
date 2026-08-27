// tools/verify-dsh-backend.mjs — 用 dsh 自带的 JsonlSessionPersistence 读回校验 agentsync 导入的会话。
// 这是比 sync.py verify 更强的一层校验：直接用 dsh 的原生读取路径（多帧 zstd 解码、
// header 身份校验、事件扫描）确认写出的文件 dsh 一定能读。
//
// 需要 Node >= 22（node:zlib 的 zstd API）。Windows 下先 `nvm use 22`，
// 或直接用同目录的 verify-dsh-backend.cmd 包装器（会自动优先选 nvm 的 22.x）。
//
// 用法： node verify-dsh-backend.mjs [sessions-root]     （缺省 ~/.dsh/sessions）
import { readdirSync, existsSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

const root = process.argv[2] || join(homedir(), ".dsh", "sessions")

const candidates = [
  process.env.AGENTSYNC_DSH_JSONL_PKG,
  join(process.env.APPDATA || "", "npm", "node_modules", "@deepseek-ai", "dsh", "node_modules", "@deepseek-ai", "dsh-session-persistence-jsonl"),
  join(homedir(), ".dsh", "node_modules", "@deepseek-ai", "dsh-session-persistence-jsonl"),
].filter(Boolean)
const pkgDir = candidates.find((p) => existsSync(join(p, "lib", "index.js")))
if (!pkgDir) {
  console.error("找不到 @deepseek-ai/dsh-session-persistence-jsonl；可用环境变量 AGENTSYNC_DSH_JSONL_PKG 指定其目录")
  process.exit(2)
}
const { JsonlSessionPersistence } = await import(pathToFileURL(join(pkgDir, "lib", "index.js")))

// dsh 的持久化类是 cordis Service：这里注入只读路径所需的最小 stub（不写盘，
// 不触达 koffi/Win32 写入原语）。
const noop = () => {}
const ctx = {
  reflect: { provide: noop, revoke: noop },
  sessions: { list: () => [] },
  on: () => noop,
  off: noop,
  get: () => undefined,
  effect: noop,
}
const backend = new JsonlSessionPersistence(ctx, { root, compression: "zstd" })

let checked = 0, ok = 0
const problems = []
for (const proj of readdirSync(root)) {
  const projDir = join(root, proj)
  if (!statSync(projDir).isDirectory()) continue
  for (const sid of readdirSync(projDir)) {
    if (!sid.startsWith("import-")) continue
    checked += 1
    try {
      const rec = await backend.loadStored(sid)
      if (!rec || !Array.isArray(rec.events) || rec.events.length === 0) throw new Error("no events returned")
      console.log(`OK  ${proj}/${sid}: ${rec.events.length} events, header=${JSON.stringify(rec.meta)}`)
      ok += 1
    } catch (e) {
      console.log(`ERR ${proj}/${sid}: ${e.message}`)
      problems.push(`${proj}/${sid}: ${e.message}`)
    }
  }
}
console.log(`\ndsh 原生后端校验：${ok}/${checked} 通过（root=${root}）`)
process.exit(problems.length ? 1 : 0)
