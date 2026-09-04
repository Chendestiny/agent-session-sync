/* 展示层小工具：转义 / 时间格式 / 徽章 */
import { COLORS, SRC_CN } from "./sources.js";
export function esc(s){
  return String(s??"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
const p2 = n => String(n).padStart(2,"0");
export function fmt(ms){
  if(!ms) return "-";
  const d = new Date(ms);
  return d.getFullYear()+"-"+p2(d.getMonth()+1)+"-"+p2(d.getDate())+" "+p2(d.getHours())+":"+p2(d.getMinutes());
}
export function fmtShort(ms){
  if(!ms) return "-";
  const d = new Date(ms);
  return p2(d.getMonth()+1)+"-"+p2(d.getDate())+" "+p2(d.getHours())+":"+p2(d.getMinutes());
}
export function errBox(msg){ return '<div class="err">加载失败：'+esc(msg)+"</div>"; }
export function badge(src){ return '<span class="badge" style="background:'+COLORS[src]+'">'+esc(SRC_CN[src]||src)+"</span>"; }
