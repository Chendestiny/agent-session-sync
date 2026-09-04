/* 会话详情视图：头部元信息 + 轮次时间条（压平 bug 一眼可见）+ 轮次列表（点击展开步骤） */
import { COLORS } from "../sources.js";
import { esc, fmt, fmtShort, badge, errBox } from "../format.js";
import { jget } from "../api.js";
const app = document.getElementById("app");
const expanded = new Set();
export async function renderDetail(src, id){
  expanded.clear();
  app.innerHTML = '<a class="back" href="#/sessions">← 返回列表</a><div id="dbody"><div class="hint">加载中…</div></div>';
  const box = document.getElementById("dbody");
  let d;
  try{
    d = await jget("/api/session?source="+encodeURIComponent(src)+"&id="+encodeURIComponent(id));
  }catch(e){ box.innerHTML = errBox(e.message); return; }
  const times = (d.turns||[]).map(t => t.time||0).filter(Boolean);
  const tmin = times.length ? Math.min(...times) : 0;
  const tmax = times.length ? Math.max(...times) : 0;
  const xu = "/api/export?source="+encodeURIComponent(src)+"&id="+encodeURIComponent(id)+"&fmt=";
  let html = '<div class="hd">'+badge(d.source)
    + (d.subagent?' <span class="badge" style="background:#8b5cf6" title="origin=subagent 委派会话（默认不同步）">🤖 子代理</span>':"")
    + (d.imported?' <span class="badge" style="background:#f59e0b" title="agentsync 导入（其他 agent 会话副本，同步默认排除）">📥 导入</span>':"")
    + '<div class="t"'+(d.imported?' style="color:#f59e0b" title="📥 agentsync 导入会话"':"")+'>'+esc(d.title||"(无标题)")+"</div>"
    + '<div class="kv">cwd：<b>'+esc(d.cwd||"-")+'</b></div>'
    + '<div class="kv">跨度：<b>'+fmt(d.created_at)+" ~ "+fmt(d.updated_at||tmax)+'</b>'
    + " · <b>"+(d.turns||[]).length+"</b> 轮</div>"
    + '<div class="kv mono">id：'+esc(d.source_id)+'</div>'
    + (d.source_path?'<div class="kv mono">源文件：'+esc(d.source_path)+"</div>":"")
    + '<div class="kv">导出：<a class="xbtn" style="padding:1px 8px" href="'+xu+'md">⬇ Markdown（人读）</a><a class="xbtn" style="padding:1px 8px" href="'+xu+'ir">⬇ IR JSON（C 库同构，可 push 回写）</a></div>'
    + "</div>";

  /* 轮次时间条：压平 bug 一眼可见 */
  html += '<h2>轮次时间分布</h2><div class="tl">';
  if(!times.length){
    html += '<div class="hint">该会话无轮次时间（旧数据，Turn.time 修复前导入）</div>';
  }else{
    let a = tmin, b = Math.max(tmax, tmin + 3600e3);
    const W = 1000, dom = b - a;
    let svg = '<svg viewBox="0 0 '+W+' 64" style="width:100%;height:64px" preserveAspectRatio="none">';
    for(let i = 0; i <= 4; i++){
      const xx = W * i / 4;
      svg += '<line class="tick" x1="'+xx+'" y1="50" x2="'+xx+'" y2="58"/>'
           + '<text x="'+xx+'" y="63" text-anchor="middle">'+fmtShort(a + dom*i/4)+"</text>";
    }
    (d.turns||[]).forEach((t, i) => {
      if(!t.time) return;
      const xx = ((t.time - a) / dom * W).toFixed(1);
      svg += '<line x1="'+xx+'" y1="6" x2="'+xx+'" y2="46" stroke="'+COLORS[d.source]
           + '" stroke-width="2"><title>#'+(i+1)+" "+fmt(t.time)+"</title></line>";
    });
    svg += "</svg>";
    html += svg;
  }
  html += "</div>";

  /* 轮次列表 */
  html += '<h2>轮次（点击展开步骤）</h2>';
  (d.turns||[]).forEach((t, i) => {
    const tools = (t.steps||[]).reduce((n,s)=>n+(s.tool_calls||[]).length,0);
    const msgs = 1 + (t.steps||[]).length + (t.steps||[]).reduce((n,s)=>n+(s.tool_results||[]).length,0);
    html += '<div class="turn" data-i="'+i+'"><div class="meta">'
      + '<span class="idx">#'+(i+1)+"</span>"
      + '<span class="tm">'+fmt(t.time)+'</span>'
      + '<span class="dim">'+msgs+' 消息 · '+tools+' 工具</span></div>'
      + '<div class="pv">'+esc((t.prompt||"").slice(0,200))+((t.prompt||"").length>200?"…":"")+"</div>"
      + '<div class="steps" style="display:'+(expanded.has(i)?"block":"none")+'">'+stepsHtml(t)+"</div></div>";
  });
  if(!(d.turns||[]).length) html += '<div class="hint">（无轮次）</div>';
  box.innerHTML = html;
  box.querySelectorAll(".turn").forEach(el => el.onclick = () => {
    const i = +el.dataset.i;
    expanded.has(i) ? expanded.delete(i) : expanded.add(i);
    el.querySelector(".steps").style.display = expanded.has(i) ? "block" : "none";
  });
}
function stepsHtml(t){
  let html = "";
  (t.steps||[]).forEach((s, si) => {
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
      html += '<div class="blk tr'+(r.is_error?" err-block":"")+'">⬅ '+(r.is_error?"(错误) ":"结果 ")
            + "<pre>"+esc(text)+((r.content||[]).some(x=>(x.text||"").length>2000)?"…":"")+"</pre></div>";
    });
  });
  return html || '<div class="hint">（空步骤）</div>';
}
