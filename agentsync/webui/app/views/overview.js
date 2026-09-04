/* 总览视图：源健康跑马灯 + C 库卡 + 会话时间轴（位置=创建 · 宽度=首末轮跨度） */
import { SOURCES, SRC_CN, COLORS, swHtml, WRITABLE, BLOCKED } from "../sources.js";
import { esc, fmt, fmtShort, errBox } from "../format.js";
import { jget, cacheGet } from "../api.js";
import { t } from "../i18n/index.js";
import { filt } from "./sessions.js";
import { openExportModal } from "../modals/export.js";
import { openWriteModal } from "../modals/write.js";
import { openBackupModal, openCBackupModal } from "../modals/backup.js";
import { openBindModal } from "../modals/bind.js";
const app = document.getElementById("app");
export async function renderOverview(){
  app.classList.add("fit");   // 整页零滚动：本视图内时间轴吃剩余高度
  app.innerHTML = '<div class="srcHead"><h2>' + t("ov.srcHealth", {n: SOURCES.length}) + '</h2>'
    + '<input id="srcSearch" class="srcSearch" placeholder="'+t("ov.search.ph")+'" autocomplete="off">'
    + '<span id="srcSearchN" class="hint"></span></div>'
    + '<div class="railWrap"><button class="railArrow" id="railL" title="'+t("ov.slideL")+'">‹</button>'
    + '<div class="rail" id="srcCards"></div>'
    + '<button class="railArrow" id="railR" title="'+t("ov.slideR")+'">›</button></div>'
    + '<div class="cards crow" id="cRow"></div>'
    + '<h2>'+t("ov.timeline.h")+'</h2>'
    + '<div class="tl" id="tl"><div class="hint">'+t("common.loading")+'</div></div>';
  let ov;
  try{ ov = await jget("/api/overview"); }
  catch(e){ document.getElementById("tl").innerHTML = errBox(e.message); return; }

  /* 源卡：先画健康，再并行补会话数 */
  const cards = document.getElementById("srcCards");
  cards.innerHTML = ov.sources.map(s =>
    '<div class="card click" id="card-'+s.name+'" data-src="'+s.name+'" title="'+t("ov.card.title")+'">'
    + '<div class="src"><span class="srcline"><span class="dot '+(s.ok?"on":"off")+'"></span>'
    + swHtml(s.name)
    + '<span class="srcname">'+esc(SRC_CN[s.name]||s.name)+'</span></span>'
    + '<span class="tags">'
    + (BLOCKED.has(s.name)
        ? '<span class="tag r" title="'+t("ov.tag.blocked.tip")+'">'+t("ov.tag.blocked")+'</span>'
        : '<span class="tag e" title="'+t("ov.tag.export.tip")+'">'+t("ov.tag.export")+'</span>')
    + '<span class="tag k" title="' + (BLOCKED.has(s.name)
        ? t("ov.tag.backup.tip.blocked") : t("ov.tag.backup.tip")) + '">'+t("ov.tag.backup")+'</span>'
    + '<span class="tag '+(WRITABLE.has(s.name)?"w":"r")+'" title="'
    + (WRITABLE.has(s.name) ? t("ov.tag.writable.tip") : t("ov.tag.readonly.tip"))
    + '">'+(WRITABLE.has(s.name)?t("ov.tag.writable"):t("ov.tag.readonly"))+'</span>'
    + '<span class="tag b" title="'+t("ov.tag.bind.tip")+'">⚙</span></span></div>'
    + '<div class="path"><span class="pt" title="'+esc(s.path || "")+'">'+(s.path ? esc(s.path) : '<span class="bindlnk">'+t("ov.notfound")+'</span>')+'</span>'
    + (s.bound ? '<span class="bl" style="flex:none" title="'+t("ov.bound.tip")+'">'+t("ov.bound")+'</span>' : "") + '</div>'
    + '<div class="stat">…</div>'
    + '<div class="kv trash" title="'+(s.trash ? t("ov.trash.tip", {n: s.trash}) : '')+'">'
    + (s.trash ? t("ov.trash", {n: s.trash}) : '&nbsp;')+'</div>'
    + '</div>').join("");
  cards.querySelectorAll(".card.click").forEach(c => c.onclick = (e) => {
    if(e.target.closest && e.target.closest(".srcname")) return;  // 选中源名复制，不跳转
    filt.sources = new Set([c.dataset.src]);
    filt.q = ""; filt.from = ""; filt.to = "";
    location.hash = "#/sessions";
  });
  cards.querySelectorAll(".tag").forEach(tEl => tEl.onclick = e => {
    e.stopPropagation();
    const src = tEl.closest(".card").dataset.src;
    if(tEl.classList.contains("e")) openExportModal(src);
    else if(tEl.classList.contains("k")) openBackupModal(src);
    else if(tEl.classList.contains("w")) openWriteModal(src);
    else if(tEl.classList.contains("b")) openBindModal(src);
  });
  cards.querySelectorAll(".bindlnk").forEach(l => l.onclick = e => {
    e.stopPropagation();
    openBindModal(l.closest(".card").dataset.src);
  });
  document.querySelectorAll("#srcCards .stat").forEach(el => el.textContent = t("ov.statLoading"));

  const cEl = document.createElement("div");
  cEl.className = "card";
  cEl.dataset.src = "";
  document.getElementById("cRow").appendChild(cEl);
  if(ov.store){
    const st = ov.store;
    const counts = Object.entries(st.counts||{}).map(([k,v])=>k+" "+v).join(" · ")||"（空）";
    const pull = Object.entries(st.state||{}).map(([k,v])=>k+" "+fmtShort(v)).join(" · ");
    const push = Object.entries(st.push||{}).map(([t,m])=>t+"（"+Object.entries(m).map(([k,v])=>k+" "+fmtShort(v)).join(" · ")+"）").join("；");
    cEl.innerHTML = '<div class="src"><span class="srcline"><span class="srcname">'+t("ov.cstore")+'</span></span>'
      + '<span class="tags"><span class="tag k" title="'+t("ov.cstore.backup.tip")+'">'+t("ov.tag.backup")+'</span></span></div>'
      + '<div class="path"><span class="pt" title="'+esc(st.dir)+'">'+esc(st.dir)+'</span></div>'
      + '<div class="kv">'+t("ov.cstore.counts", {counts: esc(counts)})+'</div>'
      + (pull?'<div class="kv">'+t("ov.cstore.pull", {v: esc(pull)})+'</div>':"")
      + (push?'<div class="kv">'+t("ov.cstore.push", {v: esc(push)})+'</div>':"");
  }else{
    cEl.innerHTML = '<div class="src"><span class="srcline"><span class="srcname">'+t("ov.cstore")+'</span></span>'
      + '<span class="tags"><span class="tag k" title="'+t("ov.cstore.backup.tip")+'">'+t("ov.tag.backup")+'</span></span></div>'
      + '<div class="path">'+t("ov.cstore.empty")+'</div>';
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
    document.getElementById("srcSearchN").textContent = q ? t("ov.searchHit", {n}) : "";
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
        const tailHtml = last ? t("ov.stat.tail", {time: esc(fmtShort(last.updated_at)), title: esc((last.title||"").slice(0,16))}) : "";
        const el = card.querySelector(".stat");
        el.innerHTML = t("ov.stat", {n, im, native: n-im, tail: tailHtml});
        const tailTxt = last ? t("ov.stat.tail", {time: fmtShort(last.updated_at), title: (last.title||"").slice(0,16)}) : "";
        el.title = t("ov.stat.tip", {n, im, native: n-im, tail: tailTxt});
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
  if(!entries.length){ tl.innerHTML = '<div class="hint">'+t("ov.tl.empty")+'</div>'; return; }
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
      const tip = t("ov.tl.tip", {src: SRC_CN[src]||src, title: m.title||m.id,
        flags: (m.trashed?t("flag.trashed"):"")+(m.subagent?t("flag.subagent"):""),
        from: fmt(m.span_first||m.created_at), to: fmt(Math.max(m.span_last||0,m.updated_at)), turns: m.turns});
      bars += '<div class="bar" style="left:'+l+'%;width:'+w+'%;background:'+(m.trashed?"#4b5563":COLORS[src])+';opacity:'+(m.subagent?".35":".85")+'" title="'+esc(tip)+'" data-href="#/session/'+src+"/"+encodeURIComponent(m.id)+'"></div>';
    }
    const shown = (all[src]||[]).filter(m => !m.subagent).length;  // 泳道计数与源卡同口径（不含🤖子代理）
    html += '<div class="lane"><div class="lb">'+esc(SRC_CN[src]||src)+" "+shown+'</div><div class="track">'+bars+"</div></div>";
  }
  tl.innerHTML = html;
  tl.querySelectorAll(".bar").forEach(b => b.onclick = () => { location.hash = b.dataset.href; });
}
