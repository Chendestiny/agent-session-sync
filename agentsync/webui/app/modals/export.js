/* 整源导出弹窗：口径=原生（排除子代理/回收站/导入副本），md/jsonl 可选，快选 + 手动勾选 */
import { SRC_CN } from "../sources.js";
import { esc, fmtShort } from "../format.js";
import { cacheGet } from "../api.js";
import { openModal, closeModal } from "../modal.js";
import { t } from "../i18n/index.js";
export async function openExportModal(src){
  let ms = [];
  try{ ms = await cacheGet(src); }catch(e){}
  ms = ms.filter(m => !m.subagent && !m.trashed && !m.imported)   // 口径=原生
         .sort((a,b) => b.updated_at - a.updated_at);
  openModal(
    '<h3 style="margin:0 0 10px">' + t("ex.h", {name: esc(SRC_CN[src]||src)}) + '</h3>'
    + '<div class="kv">'+t("ex.fmt")+'</div>'
    + '<label><input type="radio" name="xfmt" value="md" checked> '+t("ex.fmt.md")+'</label>'
    + '<label><input type="radio" name="xfmt" value="jsonl"> '+t("ex.fmt.jsonl")+'</label>'
    + '<div class="kv" style="margin-top:10px">'+t("ex.pick")+'<span class="chipbtn on" data-days="0">'+t("chip.all")+'</span>'
    + '<span class="chipbtn" data-days="7">'+t("chip.d7")+'</span><span class="chipbtn" data-days="30">'+t("chip.d30")+'</span>'
    + ' <span id="xcount" style="color:var(--dim);font-size:12px"></span></div>'
    + '<div class="xlist" id="xlist">' + (ms.length
        ? ms.map(m => '<label class="xitem"><input type="checkbox" checked value="'+esc(m.id)+'">'
            + '<span class="xt" title="'+esc(m.title||m.id)+'">'+esc((m.title||t("common.notitle")).slice(0,60))+'</span>'
            + '<span class="xd">'+esc(fmtShort(m.updated_at))+'</span></label>').join("")
        : '<div class="hint">'+t("ex.none")+'</div>') + '</div>'
    + '<div class="hint" style="margin:8px 0">'+t("ex.scopeNote")+'</div>'
    + '<button class="mbtn" id="xgo">'+t("ex.go")+'</button><button class="mbtn ghost" id="xclose">'+t("common.close")+'</button>'
  );
  const list = document.getElementById("xlist");
  const count = () => {
    const n = list.querySelectorAll("input:checked").length;
    document.getElementById("xcount").textContent = t("ex.selected", {n, total: ms.length});
    return n;
  };
  count();
  list.addEventListener("change", () => { count();
    document.querySelectorAll(".chipbtn").forEach(c => c.classList.remove("on")); });  // 手动改选后快选不再高亮
  document.querySelectorAll(".chipbtn").forEach(c => c.onclick = () => {
    document.querySelectorAll(".chipbtn").forEach(x => x.classList.remove("on"));
    c.classList.add("on");
    const d = Number(c.dataset.days);
    const floor = d ? Date.now() - d*864e5 : 0;
    list.querySelectorAll("input[type=checkbox]").forEach(cb => {
      const m = ms.find(x => x.id === cb.value);
      cb.checked = !!m && (!floor || m.updated_at >= floor);
    });
    count();
  });
  document.getElementById("xgo").onclick = () => {
    const fmt = document.querySelector('input[name="xfmt"]:checked').value;
    const ids = Array.from(list.querySelectorAll("input:checked")).map(cb => cb.value);
    if(!ids.length){ alert(t("ex.pickone")); return; }
    location.href = "/api/export-source?source=" + encodeURIComponent(src) + "&fmt=" + fmt
                  + "&ids=" + encodeURIComponent(ids.join(","));
    closeModal();
  };
  document.getElementById("xclose").onclick = closeModal;
}
