/* 「让 agent 代跑」写入提示词弹窗（提示词文本跟随 UI 语言） */
import { SRC_CN } from "../sources.js";
import { esc } from "../format.js";
import { openModal, copyText } from "../modal.js";
import { t } from "../i18n/index.js";
export function writePrompt(src){
  const head = t("prompt.head");
  if(src === "dsh") return head + t("prompt.dsh");
  const needExit = ["hermes","opencode","workbuddy","minimax","pi","gemini","cline"].includes(src) ? t("prompt.exit") : "";
  return head + t("prompt.generic", {target: SRC_CN[src]||src, exit: needExit});
}
export function openWriteModal(src){
  const p = writePrompt(src);
  openModal(
    '<h3 style="margin:0 0 10px">' + t("wr.h", {name: esc(SRC_CN[src]||src)}) + '</h3>'
    + '<div style="display:flex;gap:10px;align-items:flex-start">'
    + '<pre style="flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px">' + esc(p) + '</pre>'
    + '<button class="mbtn" id="wcopy" style="flex:none">'+t("common.copy")+'</button></div>'
    + '<div class="hint" style="margin:8px 0">'+t("wr.hint")+'</div>'
  );
  document.getElementById("wcopy").onclick = function(){ copyText(p, this); };
}
