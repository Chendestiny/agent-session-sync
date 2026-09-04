/* 总览视图：源健康跑马灯 + C 库卡 + 会话时间轴（位置=创建 · 宽度=首末轮跨度） */
import { SOURCES, SRC_CN, COLORS, swHtml, WRITABLE, BLOCKED } from "../sources.js";
import { esc, fmt, fmtShort, errBox } from "../format.js";
import { jget, cacheGet } from "../api.js";
import { filt } from "./sessions.js";
import { openExportModal } from "../modals/export.js";
import { openWriteModal } from "../modals/write.js";
import { openBackupModal, openCBackupModal } from "../modals/backup.js";
import { openBindModal } from "../modals/bind.js";
const app = document.getElementById("app");
export async function renderOverview(){
  app.classList.add("fit");   // 整页零滚动：本视图内时间轴吃剩余高度
  app.innerHTML = '<div class="srcHead"><h2>源健康（' + SOURCES.length + ' 家 · ←→ 滑动）</h2>'
    + '<input id="srcSearch" class="srcSearch" placeholder="检索源：名称 / 路径…" autocomplete="off">'
    + '<span id="srcSearchN" class="hint"></span></div>'
    + '<div class="railWrap"><button class="railArrow" id="railL" title="向左滑动">‹</button>'
    + '<div class="rail" id="srcCards"></div>'
    + '<button class="railArrow" id="railR" title="向右滑动">›</button></div>'
    + '<div class="cards crow" id="cRow"></div>'
    + '<h2>会话时间轴（位置=创建时间 · 宽度=首末轮跨度 · 点击下钻）</h2>'
    + '<div class="tl" id="tl"><div class="hint">加载中…</div></div>';
  let ov;
  try{ ov = await jget("/api/overview"); }
  catch(e){ document.getElementById("tl").innerHTML = errBox(e.message); return; }

  /* 源卡：先画健康，再并行补会话数 */
  const cards = document.getElementById("srcCards");
  cards.innerHTML = ov.sources.map(s =>
    '<div class="card click" id="card-'+s.name+'" data-src="'+s.name+'" title="点击查看该源会话列表">'
    + '<div class="src"><span class="srcline"><span class="dot '+(s.ok?"on":"off")+'"></span>'
    + swHtml(s.name)
    + '<span class="srcname">'+esc(SRC_CN[s.name]||s.name)+'</span></span>'
    + '<span class="tags">'
    + (BLOCKED.has(s.name)
        ? '<span class="tag r" title="读取被阻断：CN 版正文库 ModularData/ai-agent/database.db 自加密（无 SQLite 头，WAL 亦密文）——读取/写入均不可，仅支持原始库文件快照，详见 docs/agents/trae.md">🔒 加密阻断</span>'
        : '<span class="tag e" title="读取器已接，可导出 Markdown / IR JSON">可导出</span>')
    + '<span class="tag k" title="' + (BLOCKED.has(s.name)
        ? '整库原始文件快照留档（加密库本体拷贝，解密攻破后可用）'
        : '会话快照备份到 C 库 backups/（口径/日期可选，幂等还原）') + '">备份</span>'
    + '<span class="tag '+(WRITABLE.has(s.name)?"w":"r")+'" title="'
    + (WRITABLE.has(s.name) ? "to-X 写入器已接（会话可写入此目标）" : "只读源（无写入器；zcode 曾因兼容性问题禁写）")
    + '">'+(WRITABLE.has(s.name)?"可写入":"只读")+'</span>'
    + '<span class="tag b" title="绑定/解绑数据目录（存 ~/.session-sync/paths.json，优先于自动探测）">⚙</span></span></div>'
    + '<div class="path"><span class="pt" title="'+esc(s.path || "")+'">'+(s.path ? esc(s.path) : '<span class="bindlnk">未找到 · 点击绑定</span>')+'</span>'
    + (s.bound ? '<span class="bl" style="flex:none" title="已手动绑定（~/.session-sync/paths.json）">🔗已绑定</span>' : "") + '</div>'
    + '<div class="stat">…</div>'
    + '<div class="kv trash" title="'+(s.trash ? '回收站 '+s.trash+' 条（已排除同步）' : '')+'">'
    + (s.trash ? '🗑 回收站 <b>'+s.trash+'</b> 条（已排除同步）' : '&nbsp;')+'</div>'
    + '</div>').join("");
  cards.querySelectorAll(".card.click").forEach(c => c.onclick = (e) => {
    if(e.target.closest && e.target.closest(".srcname")) return;  // 选中源名复制，不跳转
    filt.sources = new Set([c.dataset.src]);
    filt.q = ""; filt.from = ""; filt.to = "";
    location.hash = "#/sessions";
  });
  cards.querySelectorAll(".tag").forEach(t => t.onclick = e => {
    e.stopPropagation();
    const src = t.closest(".card").dataset.src;
    if(t.classList.contains("e")) openExportModal(src);
    else if(t.classList.contains("k")) openBackupModal(src);
    else if(t.classList.contains("w")) openWriteModal(src);
    else if(t.classList.contains("b")) openBindModal(src);
  });
  cards.querySelectorAll(".bindlnk").forEach(l => l.onclick = e => {
    e.stopPropagation();
    openBindModal(l.closest(".card").dataset.src);
  });
  document.querySelectorAll("#srcCards .stat").forEach(el => el.textContent = "统计加载中…");

  const cEl = document.createElement("div");
  cEl.className = "card";
  cEl.dataset.src = "";
  document.getElementById("cRow").appendChild(cEl);
  if(ov.store){
    const st = ov.store;
    const counts = Object.entries(st.counts||{}).map(([k,v])=>k+" "+v).join(" · ")||"（空）";
    const pull = Object.entries(st.state||{}).map(([k,v])=>k+" "+fmtShort(v)).join(" · ");
    const push = Object.entries(st.push||{}).map(([t,m])=>t+"（"+Object.entries(m).map(([k,v])=>k+" "+fmtShort(v)).join(" · ")+"）").join("；");
    cEl.innerHTML = '<div class="src"><span class="srcline"><span class="srcname">📦 C 规范库</span></span>'
      + '<span class="tags"><span class="tag k" title="查看/还原/删除全部备份快照（~/.session-sync/backups/）">备份</span></span></div>'
      + '<div class="path"><span class="pt" title="'+esc(st.dir)+'">'+esc(st.dir)+'</span></div>'
      + '<div class="kv">会话：<b>'+esc(counts)+'</b></div>'
      + (pull?'<div class="kv">pull 基准：'+esc(pull)+'</div>':"")
      + (push?'<div class="kv">push 水位：'+esc(push)+'</div>':"");
  }else{
    cEl.innerHTML = '<div class="src"><span class="srcline"><span class="srcname">📦 C 规范库</span></span>'
      + '<span class="tags"><span class="tag k" title="查看/还原/删除全部备份快照（~/.session-sync/backups/）">备份</span></span></div>'
      + '<div class="path">尚未创建（先跑 pull）</div>';
  }
  const cbk = cEl.querySelector(".tag.k");
  if(cbk) cbk.onclick = e => { e.stopPropagation(); openCBackupModal(); };

  /* 跑马灯箭头：按可视宽度 80% 平滑滑动；到边禁用 */
  const rail = document.getElementById("srcCards");
  const aL = document.getElementById("railL"), aR = document.getElementById("railR");
  const railStep = () => Math.max(rail.clientWidth * 0.8, 200);
  aL.onclick = () => rail.scrollBy({left: -railStep(), behavior: "smooth"});
  aR.onclick = () => rail.scrollBy({left: railStep(), behavior: "smooth"});
  const railUpd = () => {
    aL.disabled = rail.scrollLeft <= 2;
    aR.disabled = rail.scrollLeft >= rail.scrollWidth - rail.clientWidth - 2;
  };
  rail.addEventListener("scroll", railUpd, {passive: true});
  window.addEventListener("resize", railUpd);
  railUpd();

  /* 前端检索：按 名称/中文名/路径 即时过滤源卡与 C 库卡（纯前端，不发请求） */
  const si = document.getElementById("srcSearch");
  si.addEventListener("input", () => {
    const q = si.value.trim().toLowerCase();
    let n = 0;
    document.querySelectorAll("#srcCards .card").forEach(c => {
      const src = c.dataset.src || "";
      const ptext = ((c.querySelector(".path .pt") || {}).textContent || "").toLowerCase();
      const hit = !q || src.toLowerCase().includes(q) || (SRC_CN[src] || "").toLowerCase().includes(q)
        || ptext.includes(q);
      c.style.display = hit ? "" : "none";
      if(hit) n++;
    });
    const cp = ((cEl.querySelector(".path") || {}).textContent || "").toLowerCase();
    const cHit = !q || "c 规范库 session-sync store c库".includes(q) || cp.includes(q);
    cEl.style.display = cHit ? "" : "none";
    if(cHit) n++;
    document.getElementById("srcSearchN").textContent = q ? "命中 " + n : "";
    railUpd();
  });

  /* 每源并行拉 metas（走缓存：返回不再重新加载）；单源慢不阻塞别的源 */
  const all = {};
  await Promise.all(SOURCES.map(async src => {
    const card = document.getElementById("card-"+src);
    try{
      const ms = await cacheGet(src);
      all[src] = ms;
      if(card){
        const last = ms[0];
        const n = ms.filter(m => !m.subagent).length;  // 🤖子代理默认不同步，不进统计
        const im = ms.filter(m => !m.subagent && m.imported).length;  // 📥导入副本计入但单列（正主在原生源）
        const tail = last ? ' · 最近 '+esc(fmtShort(last.updated_at))+"「"+esc((last.title||"").slice(0,16))+'」' : "";
        const el = card.querySelector(".stat");
        el.innerHTML = '<b>'+n+'</b> = 导入 '+im+' + 原生 '+(n-im)+tail;
        el.title = n + ' = 导入 ' + im + ' + 原生 ' + (n-im)
          + (last ? ' · 最近 '+fmtShort(last.updated_at)+'「'+(last.title||"").slice(0,16)+'」' : '');
      }
    }catch(e){
      if(card) card.querySelector(".stat").innerHTML = '<span class="err">'+esc(e.message)+"</span>";
    }
  }));
  renderTimeline(all);
}
function renderTimeline(all){
  const tl = document.getElementById("tl");
  const entries = Object.values(all).flat();
  if(!entries.length){ tl.innerHTML = '<div class="hint">暂无会话数据</div>'; return; }
  const t0 = Math.min(...entries.map(m => m.span_first||m.created_at||Infinity).filter(Number.isFinite));
  const t1raw = Math.max(...entries.map(m => Math.max(m.span_last||0, m.updated_at||0)));
  let t1 = t1raw;
  if(!t0 || !t1 || t1 - t0 < 3600e3){ const now = Date.now(); t1 = Math.max(t1, now); }  // 退化为「截至现在」
  const dom = Math.max(t1 - t0, 1);
  const x = ms => ((ms - t0) / dom * 100).toFixed(3);

  let html = '<div class="tl-axis">';
  for(let i = 0; i <= 4; i++){
    const ms = t0 + dom * i / 4;
    html += '<span style="left:'+(i*25)+'%">' + fmtShort(ms) + "</span>";
  }
  html += "</div>";
  for(const src of SOURCES){
    const ms = (all[src]||[]).slice().sort((a,b)=>a.span_first-b.span_first);
    let bars = "";
    for(const m of ms){
      const w = Math.max((Math.max(m.span_last||0, m.updated_at||0) - (m.span_first||m.created_at||t0)) / dom * 100, .4);
      // 右缘钳制：末尾会话（如最新一条）不越出轨道被裁——整条左移到 100% 内
      const l = Math.min(parseFloat(x(m.span_first||m.created_at||t0)), 100 - w).toFixed(3);
      const tip = (SRC_CN[src]||src)+" · "+(m.title||m.id)+(m.trashed?" · 🗑回收站":"")+(m.subagent?" · 🤖子代理":"")+"\n"+fmt(m.span_first||m.created_at)+" ~ "+fmt(Math.max(m.span_last||0,m.updated_at||0))+" · "+m.turns+" 轮";
      bars += '<div class="bar" style="left:'+l+'%;width:'+w+'%;background:'+(m.trashed?"#4b5563":COLORS[src])+';opacity:'+(m.subagent?".35":".85")+'" title="'+esc(tip)+'" data-href="#/session/'+src+"/"+encodeURIComponent(m.id)+'"></div>';
    }
    const shown = (all[src]||[]).filter(m => !m.subagent).length;  // 泳道计数与源卡同口径（不含🤖子代理）
    html += '<div class="lane"><div class="lb">'+esc(SRC_CN[src]||src)+" "+shown+'</div><div class="track">'+bars+"</div></div>";
  }
  tl.innerHTML = html;
  tl.querySelectorAll(".bar").forEach(b => b.onclick = () => { location.hash = b.dataset.href; });
}
