/* 「让 agent 代跑」写入提示词弹窗 */
import { SRC_CN } from "../sources.js";
import { esc } from "../format.js";
import { openModal, copyText } from "../modal.js";
export function writePrompt(src){
  const head = "使用 session-sync skill（~/.agents/skills/session-sync/，一句话触发，手册在里面）：";
  if(src === "dsh"){
    return [
      head + "把其他 agent 的最新会话同步进 dsh，并完成挂载分组。",
      "要求：",
      "1. 先 selftest 自检，必须全绿再动真实数据；",
      "2. dry-run 看写入计划，确认后再落盘（增量 + 预算控制，防超上下文）；",
      "3. 写入后完全退出 dsh 再挂载（attach），否则会话堆在「未分组」；",
      "4. 如实报告：写了多少、跳过多少、有无裁剪。"
    ].join("\n");
  }
  const needExit = ["hermes","opencode","workbuddy","minimax","pi","gemini","cline"].includes(src) ? "；目标应用需先完全退出再写入" : "";
  return [
    head + "把其他 agent 的最新会话同步进 " + (SRC_CN[src]||src) + needExit + "。",
    "要求：",
    "1. 先 selftest 自检，必须全绿再动真实数据；",
    "2. dry-run 看写入计划，确认后再落盘（增量 scope=inc）；",
    "3. 如实报告：写了多少、跳过多少。"
    ].join("\n");
}
export function openWriteModal(src){
  const p = writePrompt(src);
  openModal(
    '<h3 style="margin:0 0 10px">写入 ' + esc(SRC_CN[src]||src) + '：把提示词交给 agent 代跑</h3>'
    + '<div style="display:flex;gap:10px;align-items:flex-start">'
    + '<pre style="flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px">' + esc(p) + '</pre>'
    + '<button class="mbtn" id="wcopy" style="flex:none">复制</button></div>'
    + '<div class="hint" style="margin:8px 0">复制后粘给任意 AI agent（zcode / codex / dsh…）即可代执行；写入命令默认 dry-run，agent 会先看计划再加 --apply。</div>'
  );
  document.getElementById("wcopy").onclick = function(){ copyText(p, this); };
}
