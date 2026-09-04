/* 会话列表视图：源 chips + 日期/关键词筛选 + 表格（行点下钻、md/ir 导出） */
import { SOURCES, COLORS, SRC_CN } from "../sources.js";
import { esc, fmt, badge } from "../format.js";
import { metasCache, cacheGet } from "../api.js";
import { t } from "../i18n/index.js";
export const filt = { sources:new Set(SOURCES), q:"", from:"", to:"", hideSub:true };
const app = document.getElementById("app");
export async function renderSessions(){
  app.innerHTML = '<h2>'+t("sess.h")+'</h2><div class="filters" id="fbar"></div><div id="ltable">'
    + '<div class="hint">'+t("common.loading")+'</div></div>';
  /* 缺的源并行补齐（走缓存） */
  await Promise.all(SOURCES.filter(s => metasCache[s] === undefined).map(async src => {
    try{ await cacheGet(src); }
    catch(e){ metasCache[src] = []; }
  }));
  drawFilterBar(); drawTable();
}
function drawFilterBar(){
  const bar = document.getElementById("fbar");
  const nSub = Object.values(metasCache).flat().filter(m => m.subagent).length;
  bar.innerHTML = SOURCES.map(s =>
    '<span class="chip'+(filt.sources.has(s)?" on":"")+'" data-s="'+s+'" style="'+(filt.sources.has(s)?"color:"+COLORS[s]+" !important;border-color:"+COLORS[s]:"")+'">'+esc(SRC_CN[s]||s)+"</span>").join("")
    + ' <span class="chip'+(filt.hideSub?"":" on")+'" id="fSub" title="'+t("sess.sub.tip")+'">'+t("sess.sub")+(nSub?"×"+nSub:"")+(filt.hideSub?t("sess.sub.hidden"):"")+'</span>'
    + ' <input type="date" id="fFrom" value="'+esc(filt.from)+'" title="'+t("sess.from.tip")+'"> ~ '
    + '<input type="date" id="fTo" value="'+esc(filt.to)+'" title="'+t("sess.to.tip")+'">'
    + ' <input type="text" id="fQ" placeholder="'+t("sess.q.ph")+'" value="'+esc(filt.q)+'">'
    + ' <button class="chip on" id="fGo">'+t("sess.go")+'</button>'
    + ' <span class="hint" id="fCount"></span>';
  bar.querySelectorAll(".chip[data-s]").forEach(c => c.onclick = () => {
    const s = c.dataset.s;
    filt.sources.has(s) ? filt.sources.delete(s) : filt.sources.add(s);
    drawFilterBar(); drawTable();
  });
  const subChip = document.getElementById("fSub");
  if(subChip) subChip.onclick = () => { filt.hideSub = !filt.hideSub; drawFilterBar(); drawTable(); };
  document.getElementById("fGo").onclick = () => {
    filt.from = document.getElementById("fFrom").value;
    filt.to = document.getElementById("fTo").value;
    filt.q = document.getElementById("fQ").value.trim();
    drawFilterBar(); drawTable();
  };
  document.getElementById("fQ").addEventListener("keydown", e => { if(e.key==="Enter") document.getElementById("fGo").click(); });
}
function drawTable(){
  const box = document.getElementById("ltable");
  const fromMs = filt.from ? new Date(filt.from+"T00:00:00").getTime() : 0;
  const toMs = filt.to ? new Date(filt.to+"T23:59:59").getTime() : 0;
  const ql = filt.q.toLowerCase();
  let rows = Object.entries(metasCache).flatMap(([src, ms]) => ms.map(m => ({...m, source:src})));
  rows = rows.filter(m =>
    filt.sources.has(m.source)
    && !(filt.hideSub && m.subagent)
    && (!fromMs || m.updated_at >= fromMs)
    && (!toMs || m.created_at <= toMs)
    && (!ql || (m.title||"").toLowerCase().includes(ql) || (m.id||"").toLowerCase().includes(ql)));
  rows.sort((a,b) => b.updated_at - a.updated_at);
  const nTrashed = rows.filter(m => m.trashed).length;
  const nEl = document.getElementById("fCount");
  if(nEl) nEl.innerHTML = t("sess.count", {n: rows.length,
    extra: nTrashed ? t("sess.count.trash", {n: nTrashed}) : ""});
  if(!rows.length){ box.innerHTML = '<div class="hint">'+t("sess.empty")+'</div>'; return; }
  box.innerHTML = '<table><thead><tr><th>'+t("th.source")+'</th><th>'+t("th.title")+'</th><th>'+t("th.created")+'</th><th>'+t("th.updated")+'</th>'
    + '<th>'+t("th.turns")+'</th><th>'+t("th.messages")+'</th><th>'+t("th.tools")+'</th><th>'+t("th.export")+'</th></tr></thead><tbody>'
    + rows.map(m => {
      const xu = "/api/export?source="+encodeURIComponent(m.source)+"&id="+encodeURIComponent(m.id)+"&fmt=";
      return '<tr class="row'+(m.trashed?" trashed":"")+(m.subagent?" subrow":"")+(m.imported?" improw":"")+'" data-href="#/session/'+m.source+"/"+encodeURIComponent(m.id)+'">'
      + "<td>"+(m.trashed?'<span class="badge" style="background:#6b7280" title="'+t("badge.trash.tip")+'">🗑</span> ':"")+(m.subagent?'<span class="badge" style="background:#8b5cf6" title="'+t("badge.sub.tip")+'">🤖</span> ':"")+(m.imported?'<span class="badge" style="background:#f59e0b" title="'+t("badge.imp.tip")+'">📥</span> ':"")+badge(m.source)+"</td>"
      + "<td>"+esc((m.title||t("common.notitle")).slice(0,60))+"</td>"
      + "<td>"+fmt(m.created_at)+"</td><td>"+fmt(m.updated_at)+"</td>"
      + "<td>"+m.turns+"</td><td>"+m.messages+"</td><td>"+m.tools+"</td>"
      + '<td><a class="xbtn" href="'+xu+'md" onclick="event.stopPropagation()" title="'+t("export.md.tip")+'">md</a><a class="xbtn" href="'+xu+'ir" onclick="event.stopPropagation()" title="'+t("export.ir.tip")+'">ir</a></td></tr>';}).join("")
    + "</tbody></table>";
  box.querySelectorAll("tr.row").forEach(r => r.onclick = () => { location.hash = r.dataset.href; });
}
