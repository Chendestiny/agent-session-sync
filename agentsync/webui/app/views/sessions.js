/* 会话列表视图：源 chips + 日期/关键词筛选 + 表格（行点下钻、md/ir 导出） */
import { SOURCES, COLORS, SRC_CN } from "../sources.js";
import { esc, fmt, badge } from "../format.js";
import { metasCache, cacheGet } from "../api.js";
export const filt = { sources:new Set(SOURCES), q:"", from:"", to:"", hideSub:true };
const app = document.getElementById("app");
export async function renderSessions(){
  app.innerHTML = '<h2>会话列表</h2><div class="filters" id="fbar"></div><div id="ltable">'
    + '<div class="hint">加载中…</div></div>';
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
    + ' <span class="chip'+(filt.hideSub?"":" on")+'" id="fSub" title="委派子代理会话（dsh 每次委派各落一个目录、openclaw 首问带 [Subagent Context]；侧栏默认隐藏；同步默认排除）">🤖 子代理'+(nSub?"×"+nSub:"")+(filt.hideSub?"（隐藏）":"")+'</span>'
    + ' <input type="date" id="fFrom" value="'+esc(filt.from)+'" title="最后活跃 ≥"> ~ '
    + '<input type="date" id="fTo" value="'+esc(filt.to)+'" title="创建 ≤">'
    + ' <input type="text" id="fQ" placeholder="标题 / id 关键词" value="'+esc(filt.q)+'">'
    + ' <button class="chip on" id="fGo">筛选</button>'
    + ' <span class="hint">共 <b id="fN">0</b> 条<span id="fT"></span></span>';
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
  const nEl = document.getElementById("fN"); if(nEl) nEl.textContent = rows.length;
  const tEl = document.getElementById("fT"); if(tEl) tEl.textContent = nTrashed ? "（含回收站 "+nTrashed+" 条，已排除同步）" : "";
  if(!rows.length){ box.innerHTML = '<div class="hint">该筛选下无会话</div>'; return; }
  box.innerHTML = '<table><thead><tr><th>源</th><th>标题</th><th>创建时间</th><th>最后活动</th>'
    + '<th>轮数</th><th>消息</th><th>工具调用</th><th>导出</th></tr></thead><tbody>'
    + rows.map(m => {
      const xu = "/api/export?source="+encodeURIComponent(m.source)+"&id="+encodeURIComponent(m.id)+"&fmt=";
      return '<tr class="row'+(m.trashed?" trashed":"")+(m.subagent?" subrow":"")+(m.imported?" improw":"")+'" data-href="#/session/'+m.source+"/"+encodeURIComponent(m.id)+'">'
      + "<td>"+(m.trashed?'<span class="badge" style="background:#6b7280" title="源侧已归档/删除，已排除同步">🗑</span> ':"")+(m.subagent?'<span class="badge" style="background:#8b5cf6" title="origin=subagent 委派会话（默认不同步）">🤖</span> ':"")+(m.imported?'<span class="badge" style="background:#f59e0b" title="agentsync 导入（其他 agent 会话副本，同步默认排除）">📥</span> ':"")+badge(m.source)+"</td>"
      + "<td>"+esc((m.title||"(无标题)").slice(0,60))+"</td>"
      + "<td>"+fmt(m.created_at)+"</td><td>"+fmt(m.updated_at)+"</td>"
      + "<td>"+m.turns+"</td><td>"+m.messages+"</td><td>"+m.tools+"</td>"
      + '<td><a class="xbtn" href="'+xu+'md" onclick="event.stopPropagation()" title="导出 Markdown（人读）">md</a><a class="xbtn" href="'+xu+'ir" onclick="event.stopPropagation()" title="导出 IR JSON（C 库同构，可 push 回写）">ir</a></td></tr>';}).join("")
    + "</tbody></table>";
  box.querySelectorAll("tr.row").forEach(r => r.onclick = () => { location.hash = r.dataset.href; });
}
