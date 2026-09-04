/* 会话详情视图：头部元信息 + 轮次时间条（压平 bug 一眼可见）+ 轮次列表（点击展开步骤） */
import { COLORS } from "../sources.js";
import { esc, fmt, fmtShort, badge, errBox } from "../format.js";
import { jget } from "../api.js";
import { t } from "../i18n/index.js";
const app = document.getElementById("app");
const expanded = new Set();
export async function renderDetail(src, id){
  expanded.clear();
  app.innerHTML = '<a class="back" href="#/sessions">'+t("det.back")+'</a><div id="dbody"><div class="hint">'+t("common.loading")+'</div></div>';
  const box = document.getElementById("dbody");
  let d;
  try{
    d = await jget("/api/session?source="+encodeURIComponent(src)+"&id="+encodeURIComponent(id));
  }catch(e){ box.innerHTML = errBox(e.message); return; }
  const times = (d.turns||[]).map(tt => tt.time||0).filter(Boolean);
  const tmin = times.length ? Math.min(...times) : 0;
  const tmax = times.length ? Math.max(...times) : 0;
  const xu = "/api/export?source="+encodeURIComponent(src)+"&id="+encodeURIComponent(id)+"&fmt=";
  let html = '<div class="hd">'+badge(d.source)
    + (d.subagent?' <span class="badge" style="background:#8b5cf6" title="'+t("badge.sub.tip")+'">'+t("det.subbadge")+'</span>':"")
    + (d.imported?' <span class="badge" style="background:#f59e0b" title="'+t("badge.imp.tip")+'">'+t("det.impbadge")+'</span>':"")
    + '<div class="t"'+(d.imported?' style="color:#f59e0b" title="'+t("det.imptitle")+'"':"")+'>'+esc(d.title||t("common.notitle"))+"</div>"
    + '<div class="kv">'+t("det.cwd")+'<b>'+esc(d.cwd||"-")+'</b></div>'
    + '<div class="kv">'+t("det.span", {a: fmt(d.created_at), b: fmt(d.updated_at||tmax), n: (d.turns||[]).length})+'</div>'
    + '<div class="kv mono">'+t("det.id")+esc(d.source_id)+'</div>'
    + (d.source_path?'<div class="kv mono">'+t("det.srcfile")+esc(d.source_path)+"</div>":"")
    + '<div class="kv">'+t("det.export")+'<a class="xbtn" style="padding:1px 8px" href="'+xu+'md">'+t("det.md.btn")+'</a><a class="xbtn" style="padding:1px 8px" href="'+xu+'ir">'+t("det.ir.btn")+'</a></div>'
    + "</div>";

  /* 轮次时间条：压平 bug 一眼可见 */
  html += '<h2>'+t("det.turnsTimeline")+'</h2><div class="tl">';
  if(!times.length){
    html += '<div class="hint">'+t("det.notime")+'</div>';
  }else{
    let a = tmin, b = Math.max(tmax, tmin + 3600e3);
    const W = 1000, dom = b - a;
    let svg = '<svg viewBox="0 0 '+W+' 64" style="width:100%;height:64px" preserveAspectRatio="none">';
    for(let i = 0; i <= 4; i++){
      const xx = W * i / 4;
      svg += '<line class="tick" x1="'+xx+'" y1="50" x2="'+xx+'" y2="58"/>'
           + '<text x="'+xx+'" y="63" text-anchor="middle">'+fmtShort(a + dom*i/4)+"</text>";
    }
    (d.turns||[]).forEach((tt, i) => {
      if(!tt.time) return;
      const xx = ((tt.time - a) / dom * W).toFixed(1);
      svg += '<line x1="'+xx+'" y1="6" x2="'+xx+'" y2="46" stroke="'+COLORS[d.source]
           + '" stroke-width="2"><title>#'+(i+1)+" "+fmt(tt.time)+"</title></line>";
    });
    svg += "</svg>";
    html += svg;
  }
  html += "</div>";

  /* 轮次列表 */
  html += '<h2>'+t("det.turns.h")+'</h2>';
  (d.turns||[]).forEach((tt, i) => {
    const tools = (tt.steps||[]).reduce((n,s)=>n+(s.tool_calls||[]).length,0);
    const msgs = 1 + (tt.steps||[]).length + (tt.steps||[]).reduce((n,s)=>n+(s.tool_results||[]).length,0);
    html += '<div class="turn" data-i="'+i+'"><div class="meta">'
      + '<span class="idx">#'+(i+1)+"</span>"
      + '<span class="tm">'+fmt(tt.time)+'</span>'
      + '<span class="dim">'+t("det.meta", {msgs, tools})+'</span></div>'
      + '<div class="pv">'+esc((tt.prompt||"").slice(0,200))+((tt.prompt||"").length>200?"…":"")+"</div>"
      + '<div class="steps" style="display:'+(expanded.has(i)?"block":"none")+'">'+stepsHtml(tt)+"</div></div>";
  });
  if(!(d.turns||[]).length) html += '<div class="hint">'+t("det.noturns")+'</div>';
  box.innerHTML = html;
  box.querySelectorAll(".turn").forEach(el => el.onclick = () => {
    const i = +el.dataset.i;
    expanded.has(i) ? expanded.delete(i) : expanded.add(i);
    el.querySelector(".steps").style.display = expanded.has(i) ? "block" : "none";
  });
}
function stepsHtml(tt){
  let html = "";
  (tt.steps||[]).forEach((s, si) => {
    (s.content||[]).forEach(b => {
      if(b.type === "text") html += '<div class="blk"><pre>'+esc(b.text||"")+"</pre></div>";
      else if(b.type === "reasoning") html += '<div class="blk reasoning"><pre>'+esc(b.text||"")+"</pre></div>";
      else if(b.type === "tool-call") html += '<div class="blk tc">🔧 '+esc(b.name)+" <pre>"+esc(b.arguments||"")+"</pre></div>";
    });
    (s.tool_calls||[]).forEach(c => {
      html += '<div class="blk tc">▶ '+esc(c.name)+" <pre>"+esc(c.arguments||"")+"</pre></div>";
    });
    (s.tool_results||[]).forEach(r => {
      const text = (r.content||[]).filter(x=>x.type==="text").map(x=>x.text||"").join("\n").slice(0,2000);
      html += '<div class="blk tr'+(r.is_error?" err-block":"")+'">⬅ '+(r.is_error?t("det.step.err"):t("det.step.result"))
            + "<pre>"+esc(text)+((r.content||[]).some(x=>(x.text||"").length>2000)?"…":"")+"</pre></div>";
    });
  });
  return html || '<div class="hint">'+t("det.emptysteps")+'</div>';
}
