/* 备份快照：源卡「备份」（会话勾选/口径/日期 + 阻断源原始库整份快照）与 C 库总览 */
import { SRC_CN, COLORS, WRITABLE, BLOCKED } from "../sources.js";
import { esc, fmtShort } from "../format.js";
import { cacheGet } from "../api.js";
import { openModal, closeModal } from "../modal.js";
import { t } from "../i18n/index.js";
export const BK_TGTS = ["dsh","codex","claude","hermes","opencode","workbuddy","minimax","pi","gemini","cline"];
export function renderSnapList(el, rows, withSource){
  el.innerHTML = rows.length
    ? rows.map(r => {
        if(r.raw){
          return '<div class="xitem" style="display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;cursor:default">'
            + (withSource ? '<span class="badge" style="background:'+COLORS[r.source]+';flex:none">'+esc(SRC_CN[r.source]||r.source)+'</span>' : '')
            + '<span class="xt" style="flex:1;min-width:0">' + esc(r.ts) + t("bk.raw", {count: r.count, size: r.size_kb}) + '</span>'
            + '<button class="mbtn ghost brst" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" data-raw="1" style="flex:none;padding:2px 10px;font-size:12px">'+t("bk.restore")+'</button>'
            + '<button class="mbtn ghost bdel" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="'+t("bk.del.tip")+'" style="flex:none;padding:2px 10px;font-size:12px;color:#ef4444;border-color:#ef4444">'+t("bk.del")+'</button>'
            + '<span title="'+esc(r.dir)+'" style="flex:1 1 100%;color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📁 '+esc(r.dir)+'</span></div>';
        }
        const selfOk = WRITABLE.has(r.source);
        return '<div class="xitem" style="display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;cursor:default">'
          + (withSource ? '<span class="badge" style="background:'+COLORS[r.source]+';flex:none">'+esc(SRC_CN[r.source]||r.source)+'</span>' : '')
          + '<span class="xt" style="flex:1;min-width:0">' + esc(r.ts)
          + t("bk.row", {count: r.count, size: r.size_kb,
              scope: r.with_imports ? t("bk.scope.both") : t("bk.scope.native")}) + '</span>'
          + '<select class="btgt" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="'+t("bk.target")+'" style="flex:none;font-size:12px;padding:2px 4px;background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:6px">'
          + (selfOk ? '' : '<option value="">'+t("bk.target.ph")+'</option>')
          + BK_TGTS.map(tg => '<option value="'+tg+'"'+(selfOk && tg===r.source ? " selected" : "")+'>'+esc(SRC_CN[tg]||tg)+'</option>').join("")
          + '</select>'
          + '<button class="mbtn ghost brst" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" style="flex:none;padding:2px 10px;font-size:12px">'+t("bk.restore")+'</button>'
          + '<button class="mbtn ghost bdel" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="'+t("bk.del.tip")+'" style="flex:none;padding:2px 10px;font-size:12px;color:#ef4444;border-color:#ef4444">'+t("bk.del")+'</button>'
          + '<span title="'+esc(r.dir)+'" style="flex:1 1 100%;color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📁 '+esc(r.dir)+'</span></div>';
      }).join("")
    : '<div class="hint">'+t("bk.none")+'</div>';
  el.querySelectorAll(".brst").forEach(b => b.onclick = async () => {
    const src = b.dataset.src, ts = b.dataset.ts;
    if(b.dataset.raw){
      if(!confirm(t("bk.confirm.raw", {name: SRC_CN[src]||src, ts}))) return;
      b.disabled = true; b.textContent = t("bk.restoring");
      try{
        const r = await (await fetch("/api/restore", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({source: src, ts: ts})})).json();
        alert(r.ok ? t("bk.done.raw", {n: r.restored}) : t("common.fail") + (r.error || r.detail));
      }catch(e){ alert(t("common.fail") + e); }
      b.disabled = false; b.textContent = t("bk.restore");
      return;
    }
    const sel = el.querySelector('.btgt[data-ts="'+ts+'"][data-src="'+src+'"]');
    const tgt = sel ? sel.value : "";
    if(!tgt){ alert(WRITABLE.has(src) ? t("bk.needtarget") : t("bk.needtarget.ro", {name: SRC_CN[src]||src})); return; }
    if(!confirm(t("bk.confirm", {name: SRC_CN[src]||src, ts, tgt: SRC_CN[tgt]||tgt}))) return;
    b.disabled = true; b.textContent = t("bk.restoring");
    try{
      const r = await (await fetch("/api/restore", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, ts: ts, target: tgt})})).json();
      alert(r.ok ? t("bk.done", {w: r.written, s: r.skipped, f: r.failed})
        + (r.target === "dsh" ? t("bk.done.dsh") : "") : t("common.fail") + (r.error || r.detail));
    }catch(e){ alert(t("common.fail") + e); }
    b.disabled = false; b.textContent = t("bk.restore");
  });
  el.querySelectorAll(".bdel").forEach(b => b.onclick = async () => {
    const src = b.dataset.src, ts = b.dataset.ts;
    if(!confirm(t("bk.confirm.del", {name: SRC_CN[src]||src, ts}))) return;
    b.disabled = true;
    try{
      const r = await (await fetch("/api/backup-del", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, ts: ts})})).json();
      if(r.ok){ b.closest(".xitem").style.opacity = .35; b.disabled = true; }
      else alert(t("common.fail") + (r.error || r.detail));
    }catch(e){ alert(t("common.fail") + e); }
  });
}
export async function fetchSnaps(src){
  try{ return await (await fetch("/api/backups" + (src ? "?source=" + encodeURIComponent(src) : ""))).json(); }catch(e){ return []; }
}
export async function openCBackupModal(){
  openModal(
    '<h3 style="margin:0 0 10px">'+t("bk.all.h")+'</h3>'
    + '<div class="hint">'+t("bk.all.hint")+'</div>'
    + '<div class="xlist" id="cbsnaps">'+t("common.loading")+'</div>'
    + '<button class="mbtn ghost" id="cbclose" style="margin-top:8px">'+t("common.close")+'</button>'
  );
  renderSnapList(document.getElementById("cbsnaps"), await fetchSnaps(null), true);
  document.getElementById("cbclose").onclick = closeModal;
}
export async function openBackupModal(src){
  if(BLOCKED.has(src)){
    openModal(
      '<h3 style="margin:0 0 10px">' + t("bk.h.raw", {name: esc(SRC_CN[src]||src)}) + '</h3>'
      + '<div class="hint">' + t("bk.raw.hint1", {name: esc(SRC_CN[src]||src)}) + '</div>'
      + '<div class="kv" style="margin-top:6px">'+t("bk.raw.hint2")+'</div>'
      + '<button class="mbtn" id="brawgo">'+t("bk.raw.go")+'</button>'
      + '<div class="kv" style="margin-top:10px">'+t("bk.snaps")+'</div><div class="xlist" id="bsnaps">'+t("common.loading")+'</div>'
      + '<button class="mbtn ghost" id="bclose" style="margin-top:8px">'+t("common.close")+'</button>'
    );
    document.getElementById("brawgo").onclick = async () => {
      const btn = document.getElementById("brawgo");
      btn.disabled = true; btn.textContent = t("bk.raw.doing");
      try{
        const r = await (await fetch("/api/backup", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({source: src})})).json();
        alert(r.ok ? t("bk.raw.done", {n: r.snapshots[0].count, k: r.snapshots[0].size_kb})
          + (r.snapshots[0].count ? "" : t("bk.raw.done.none")) : t("common.fail") + r.detail);
        if(r.ok) renderSnapList(document.getElementById("bsnaps"), await fetchSnaps(src), false);
      }catch(e){ alert(t("common.fail") + e); }
      btn.disabled = false; btn.textContent = t("bk.raw.go");
    };
    renderSnapList(document.getElementById("bsnaps"), await fetchSnaps(src), false);
    document.getElementById("bclose").onclick = closeModal;
    return;
  }
  let ms = [];
  try{ ms = await cacheGet(src); }catch(e){}
  ms = ms.filter(m => !m.subagent && !m.trashed).sort((a,b) => b.updated_at - a.updated_at);
  openModal(
    '<h3 style="margin:0 0 10px">' + t("bk.h", {name: esc(SRC_CN[src]||src)}) + '</h3>'
    + '<div class="kv">'+t("bk.scope")+'<span class="chipbtn on" data-imp="0">'+t("bk.scope.native")+'</span>'
    + '<span class="chipbtn" data-imp="1">'+t("bk.scope.both")+'</span></div>'
    + '<div class="kv" style="margin-top:6px">'+t("ex.pick")+'<span class="chipbtn on bkd" data-days="0">'+t("chip.all")+'</span>'
    + '<span class="chipbtn bkd" data-days="7">'+t("chip.d7")+'</span><span class="chipbtn bkd" data-days="30">'+t("chip.d30")+'</span>'
    + ' <span id="bkcount" style="color:var(--dim);font-size:12px"></span></div>'
    + '<div class="xlist" id="bklist"></div>'
    + '<button class="mbtn" id="bgo">'+t("bk.go")+'</button>'
    + '<div class="hint" style="margin:8px 0">'+t("bk.hint")+'</div>'
    + '<div class="kv">'+t("bk.snaps2")+'</div><div class="xlist" id="bsnaps">'+t("common.loading")+'</div>'
    + '<button class="mbtn ghost" id="bclose" style="margin-top:8px">'+t("common.close")+'</button>'
  );
  const list = document.getElementById("bklist");
  let withImports = 0;
  const count = () => {
    const all = list.querySelectorAll("input").length;
    const n = list.querySelectorAll("input:checked").length;
    document.getElementById("bkcount").textContent = t("ex.selected", {n, total: all});
    return n;
  };
  const render = () => {
    const pool = withImports ? ms : ms.filter(m => !m.imported);
    list.innerHTML = pool.length
      ? pool.map(m => '<label class="xitem"><input type="checkbox" checked value="'+esc(m.id)+'">'
          + '<span class="xt" title="'+esc(m.title||m.id)+'">'+esc((m.title||t("common.notitle")).slice(0,60))
          + (m.imported ? ' <span style="color:#f59e0b" title="'+t("bk.imp.tip")+'">📥</span>' : '') + '</span>'
          + '<span class="xd">'+esc(fmtShort(m.updated_at))+'</span></label>').join("")
      : '<div class="hint">'+t("bk.pool.empty")+'</div>';
    count();
  };
  render();
  document.querySelectorAll(".chipbtn[data-imp]").forEach(c => c.onclick = () => {
    document.querySelectorAll(".chipbtn[data-imp]").forEach(x => x.classList.remove("on"));
    c.classList.add("on"); withImports = Number(c.dataset.imp); render();
  });
  list.addEventListener("change", () => { count();
    document.querySelectorAll(".chipbtn.bkd").forEach(c => c.classList.remove("on")); });
  document.querySelectorAll(".chipbtn.bkd").forEach(c => c.onclick = () => {
    document.querySelectorAll(".chipbtn.bkd").forEach(x => x.classList.remove("on"));
    c.classList.add("on");
    const d = Number(c.dataset.days);
    const floor = d ? Date.now() - d*864e5 : 0;
    list.querySelectorAll("input[type=checkbox]").forEach(cb => {
      const m = ms.find(x => x.id === cb.value);
      cb.checked = !!m && (!floor || m.updated_at >= floor);
    });
    count();
  });
  const loadSnaps = async () => {
    renderSnapList(document.getElementById("bsnaps"), await fetchSnaps(src), false);
  };
  loadSnaps();
  document.getElementById("bgo").onclick = async () => {
    const ids = Array.from(list.querySelectorAll("input:checked")).map(cb => cb.value);
    if(!ids.length){ alert(t("ex.pickone")); return; }
    const btn = document.getElementById("bgo");
    btn.disabled = true; btn.textContent = t("bk.doing");
    try{
      const r = await (await fetch("/api/backup", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, with_imports: !!withImports, ids: ids.join(",")})})).json();
      alert(r.ok ? t("bk.done.normal", {n: r.snapshots[0].count, k: r.snapshots[0].size_kb}) : t("common.fail") + r.detail);
    }catch(e){ alert(t("common.fail") + e); }
    btn.disabled = false; btn.textContent = t("bk.go");
    loadSnaps();
  };
  document.getElementById("bclose").onclick = closeModal;
}
