/* 备份快照：源卡「备份」（会话勾选/口径/日期 + 阻断源原始库整份快照）与 C 库总览 */
import { SRC_CN, COLORS, WRITABLE, BLOCKED } from "../sources.js";
import { esc, fmtShort } from "../format.js";
import { cacheGet } from "../api.js";
import { openModal, closeModal } from "../modal.js";
export const BK_TGTS = ["dsh","codex","claude","hermes","opencode","workbuddy","minimax","pi","gemini","cline"];
export function renderSnapList(el, rows, withSource){
  el.innerHTML = rows.length
    ? rows.map(r => {
        if(r.raw){
          return '<div class="xitem" style="display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;cursor:default">'
            + (withSource ? '<span class="badge" style="background:'+COLORS[r.source]+';flex:none">'+esc(SRC_CN[r.source]||r.source)+'</span>' : '')
            + '<span class="xt" style="flex:1;min-width:0">' + esc(r.ts)
            + ' · 📦 原始库 ' + r.count + ' 文件 · ' + r.size_kb + ' KB</span>'
            + '<button class="mbtn ghost brst" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" data-raw="1" style="flex:none;padding:2px 10px;font-size:12px">还原</button>'
            + '<button class="mbtn ghost bdel" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="删除快照目录（不可恢复）" style="flex:none;padding:2px 10px;font-size:12px;color:#ef4444;border-color:#ef4444">删</button>'
            + '<span title="'+esc(r.dir)+'" style="flex:1 1 100%;color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📁 '+esc(r.dir)+'</span></div>';
        }
        const selfOk = WRITABLE.has(r.source);
        return '<div class="xitem" style="display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;cursor:default">'
          + (withSource ? '<span class="badge" style="background:'+COLORS[r.source]+';flex:none">'+esc(SRC_CN[r.source]||r.source)+'</span>' : '')
          + '<span class="xt" style="flex:1;min-width:0">' + esc(r.ts)
          + ' · ' + r.count + ' 条 · ' + r.size_kb + ' KB · ' + (r.with_imports ? "原生+导入" : "原生") + '</span>'
          + '<select class="btgt" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="还原目标" style="flex:none;font-size:12px;padding:2px 4px;background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:6px">'
          + (selfOk ? '' : '<option value="">选择目标…</option>')
          + BK_TGTS.map(t => '<option value="'+t+'"'+(selfOk && t===r.source ? " selected" : "")+'>'+esc(SRC_CN[t]||t)+'</option>').join("")
          + '</select>'
          + '<button class="mbtn ghost brst" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" style="flex:none;padding:2px 10px;font-size:12px">还原</button>'
          + '<button class="mbtn ghost bdel" data-src="'+esc(r.source)+'" data-ts="'+esc(r.ts)+'" title="删除快照目录（不可恢复）" style="flex:none;padding:2px 10px;font-size:12px;color:#ef4444;border-color:#ef4444">删</button>'
          + '<span title="'+esc(r.dir)+'" style="flex:1 1 100%;color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📁 '+esc(r.dir)+'</span></div>';
      }).join("")
    : '<div class="hint">（暂无快照）</div>';
  el.querySelectorAll(".brst").forEach(b => b.onclick = async () => {
    const src = b.dataset.src, ts = b.dataset.ts;
    if(b.dataset.raw){
      if(!confirm("还原 " + (SRC_CN[src]||src) + " 原始库@" + ts + "？\n· 加密库文件原位覆盖写回（不解密、不解析）\n· 请先完全退出 " + (SRC_CN[src]||src))) return;
      b.disabled = true; b.textContent = "还原中…";
      try{
        const r = await (await fetch("/api/restore", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({source: src, ts: ts})})).json();
        alert(r.ok ? ("还原完成：原位写回 " + r.restored + " 个库文件") : ("失败：" + (r.error || r.detail)));
      }catch(e){ alert("失败：" + e); }
      b.disabled = false; b.textContent = "还原";
      return;
    }
    const sel = el.querySelector('.btgt[data-ts="'+ts+'"][data-src="'+src+'"]');
    const tgt = sel ? sel.value : "";
    if(!tgt){ alert(WRITABLE.has(src) ? "请先选择还原目标" : (SRC_CN[src]||src) + " 不可写（只读源），请先选择还原目标"); return; }
    if(!confirm("还原 " + (SRC_CN[src]||src) + "@" + ts + " → " + (SRC_CN[tgt]||tgt)
      + "？\n· 走幂等写入：已存在的会话自动跳过，只补缺\n· 请先完全退出 " + (SRC_CN[tgt]||tgt))) return;
    b.disabled = true; b.textContent = "还原中…";
    try{
      const r = await (await fetch("/api/restore", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, ts: ts, target: tgt})})).json();
      alert(r.ok ? ("还原完成：写入 " + r.written + " · 幂等跳过 " + r.skipped + " · 失败 " + r.failed
        + (r.target === "dsh" ? "\n（dsh 侧挂分组：退出 dsh 后 attach-dsh）" : "")) : ("失败：" + (r.error || r.detail)));
    }catch(e){ alert("失败：" + e); }
    b.disabled = false; b.textContent = "还原";
  });
  el.querySelectorAll(".bdel").forEach(b => b.onclick = async () => {
    const src = b.dataset.src, ts = b.dataset.ts;
    if(!confirm("删除快照 " + (SRC_CN[src]||src) + "@" + ts + "？\n· 整个快照目录从磁盘移除，不可恢复\n· 只动 C 库 backups/，不碰任何 agent 数据")) return;
    b.disabled = true;
    try{
      const r = await (await fetch("/api/backup-del", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, ts: ts})})).json();
      if(r.ok){ b.closest(".xitem").style.opacity = .35; b.disabled = true; }
      else alert("失败：" + (r.error || r.detail));
    }catch(e){ alert("失败：" + e); }
  });
}
export async function fetchSnaps(src){
  try{ return await (await fetch("/api/backups" + (src ? "?source=" + encodeURIComponent(src) : ""))).json(); }catch(e){ return []; }
}
export async function openCBackupModal(){
  openModal(
    '<h3 style="margin:0 0 10px">📦 备份快照总览（C 库 backups/）</h3>'
    + '<div class="hint">全部快照存放于 <b>~/.session-sync/backups/&lt;源&gt;/&lt;时间戳&gt;/</b>；还原=幂等写入需选目标（只读源必须显式选）；删除=目录级移除不可恢复。备份入口在各源卡片「备份」tag。</div>'
    + '<div class="xlist" id="cbsnaps">加载中…</div>'
    + '<button class="mbtn ghost" id="cbclose" style="margin-top:8px">关闭</button>'
  );
  renderSnapList(document.getElementById("cbsnaps"), await fetchSnaps(null), true);
  document.getElementById("cbclose").onclick = closeModal;
}
export async function openBackupModal(src){
  if(BLOCKED.has(src)){
    openModal(
      '<h3 style="margin:0 0 10px">备份 ' + esc(SRC_CN[src]||src) + '（原始库快照）</h3>'
      + '<div class="hint">' + esc(SRC_CN[src]||src) + ' 读取被加密阻断（正文库自加密，IR 读不出），'
      + '会话级备份/导出/写入均不可用。<b>唯一可做的留档 = 把加密库文件整份拷进 C 库</b>，'
      + '待日后解密攻破即可回补。</div>'
      + '<div class="kv" style="margin-top:6px">留档对象：ModularData/ai-agent/database.db（+wal/shm，Trae 运行中也可拷，建议退出后更稳）</div>'
      + '<button class="mbtn" id="brawgo">📦 立即快照原始库</button>'
      + '<div class="kv" style="margin-top:10px">已有快照：</div><div class="xlist" id="bsnaps">加载中…</div>'
      + '<button class="mbtn ghost" id="bclose" style="margin-top:8px">关闭</button>'
    );
    document.getElementById("brawgo").onclick = async () => {
      const btn = document.getElementById("brawgo");
      btn.disabled = true; btn.textContent = "快照中…";
      try{
        const r = await (await fetch("/api/backup", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({source: src})})).json();
        alert(r.ok ? ("原始库快照完成：" + r.snapshots[0].count + " 文件 · " + r.snapshots[0].size_kb + " KB"
          + (r.snapshots[0].count ? "" : "（未找到库文件，检查 Trae 是否安装）")) : ("失败：" + r.detail));
        if(r.ok) renderSnapList(document.getElementById("bsnaps"), await fetchSnaps(src), false);
      }catch(e){ alert("失败：" + e); }
      btn.disabled = false; btn.textContent = "📦 立即快照原始库";
    };
    renderSnapList(document.getElementById("bsnaps"), await fetchSnaps(src), false);
    document.getElementById("bclose").onclick = closeModal;
    return;
  }
  let ms = [];
  try{ ms = await cacheGet(src); }catch(e){}
  ms = ms.filter(m => !m.subagent && !m.trashed).sort((a,b) => b.updated_at - a.updated_at);
  openModal(
    '<h3 style="margin:0 0 10px">备份 ' + esc(SRC_CN[src]||src) + '（快照到 C 库 backups/）</h3>'
    + '<div class="kv">口径：<span class="chipbtn on" data-imp="0">原生</span>'
    + '<span class="chipbtn" data-imp="1">原生+导入</span></div>'
    + '<div class="kv" style="margin-top:6px">会话（可手动勾选）：<span class="chipbtn on bkd" data-days="0">全部</span>'
    + '<span class="chipbtn bkd" data-days="7">最近 7 天</span><span class="chipbtn bkd" data-days="30">最近 30 天</span>'
    + ' <span id="bkcount" style="color:var(--dim);font-size:12px"></span></div>'
    + '<div class="xlist" id="bklist"></div>'
    + '<button class="mbtn" id="bgo">📦 立即备份</button>'
    + '<div class="hint" style="margin:8px 0">快照=会话 IR 落 ~/.session-sync/backups/，不碰源数据、不推进增量水位。</div>'
    + '<div class="kv">已有快照（还原走幂等写入，已存在自动跳过）：</div><div class="xlist" id="bsnaps">加载中…</div>'
    + '<button class="mbtn ghost" id="bclose" style="margin-top:8px">关闭</button>'
  );
  const list = document.getElementById("bklist");
  let withImports = 0;
  const count = () => {
    const all = list.querySelectorAll("input").length;
    const n = list.querySelectorAll("input:checked").length;
    document.getElementById("bkcount").textContent = "已选 " + n + " / " + all;
    return n;
  };
  const render = () => {
    const pool = withImports ? ms : ms.filter(m => !m.imported);
    list.innerHTML = pool.length
      ? pool.map(m => '<label class="xitem"><input type="checkbox" checked value="'+esc(m.id)+'">'
          + '<span class="xt" title="'+esc(m.title||m.id)+'">'+esc((m.title||"(无标题)").slice(0,60))
          + (m.imported ? ' <span style="color:#f59e0b" title="导入副本">📥</span>' : '') + '</span>'
          + '<span class="xd">'+esc(fmtShort(m.updated_at))+'</span></label>').join("")
      : '<div class="hint">（该口径下无会话）</div>';
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
    if(!ids.length){ alert("请至少勾选一条会话"); return; }
    const btn = document.getElementById("bgo");
    btn.disabled = true; btn.textContent = "备份中…";
    try{
      const r = await (await fetch("/api/backup", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({source: src, with_imports: !!withImports, ids: ids.join(",")})})).json();
      alert(r.ok ? ("快照完成：" + r.snapshots[0].count + " 条 · " + r.snapshots[0].size_kb + " KB") : ("失败：" + r.detail));
    }catch(e){ alert("失败：" + e); }
    btn.disabled = false; btn.textContent = "📦 立即备份";
    loadSnaps();
  };
  document.getElementById("bclose").onclick = closeModal;
}
