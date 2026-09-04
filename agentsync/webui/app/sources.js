/* 19 家源元数据：清单 / 配色 / 中文名 / 官方图标 / 能力位（自 index.html 原样迁出） */
export const SOURCES = ["claude","codex","hermes","openclaw","zcode","dsh","workbuddy","opencode","qoder","cursor","trae","mimo","kimi","minimax","grok","copilot","gemini","cline","pi"];
export const COLORS = {zcode:"#84cc16",hermes:"#eab308",dsh:"#4f8cff",codex:"#a78bfa",
                workbuddy:"#f472b6",claude:"#f97316",opencode:"#22d3ee",qoder:"#14b8a6",openclaw:"#ef4444",
                cursor:"#6366f1",trae:"#0ea5e9",mimo:"#22c55e",kimi:"#d946ef",minimax:"#7c3aed",
                grok:"#64748b",copilot:"#38bdf8",gemini:"#e879f9",cline:"#2dd4bf",pi:"#fbbf24"};
export const SRC_CN  = {zcode:"ZCode",hermes:"Hermes Agent",dsh:"DeepSeek Harness(DSH)",codex:"Codex",
                 workbuddy:"WorkBuddy",claude:"Claude Code",opencode:"OpenCode",qoder:"Qoder",openclaw:"OpenClaw",
                 cursor:"Cursor",trae:"Trae",mimo:"MiMo-Code",kimi:"Kimi Code",minimax:"MiniMax Code",
                 grok:"Grok Build",copilot:"GitHub Copilot",gemini:"Gemini CLI",cline:"Cline",pi:"Pi Agent"};
export const ICONS = {zcode:"zcode.png",hermes:"hermes.svg",dsh:"dsh.svg",codex:"codex.png",
               workbuddy:"workbuddy.png",claude:"claude.png",opencode:"opencode.png",qoder:"qoder.png",
               openclaw:"openclaw.svg",cursor:"cursor.ico",trae:"trae.png",mimo:"mimo.png",
               kimi:"kimi.svg",minimax:"minimax.png",grok:"grok.png",copilot:"copilot.png",
               gemini:"gemini.svg",cline:"cline.png",pi:"pi.svg"};  // /icons/ 官方图标（无图标的源回退色块）
export const ICONS_DARK = new Set(["dsh","kimi","grok"]);  // 黑色图标：垫浅灰底才看得清
export function swHtml(src){
  if(!ICONS[src]) return '<span class="sw" style="background:'+COLORS[src]+'"></span>';
  return '<img class="ic'+(ICONS_DARK.has(src)?" icbg":"")+'" src="/icons/'+ICONS[src]+'" alt="" width="16" height="16">';
}
export const WRITABLE = new Set(["dsh","codex","claude","hermes","opencode","workbuddy","minimax","pi","gemini","cline"]); // 10 写入目标；zcode/qoder/openclaw 等只读
export const BLOCKED  = new Set(["trae"]); // 读取被阻断（CN 版正文库自加密）：读写均不可，仅原始库文件备份
