/* 整源导出弹窗：口径=原生（排除子代理/回收站/导入副本），md/jsonl 可选，快选 + 手动勾选 */
import { SRC_CN } from "../sources.js";
import { esc, fmtShort } from "../format.js";
import { cacheGet } from "../api.js";
import { openModal, closeModal } from "../modal.js";
export async function openExportModal(src){
  let ms = [];
  try{ ms = await cacheGet(src); }catch(e){}
  ms = ms.filter(m => !m.subagent && !m.trashed && !m.imported)   // 口径=原生
         .sort((a,b) => b.updated_at - a.updated_at);
  openModal(
    '<h3 style="margin:0 0 10px">导出 ' + esc(SRC_CN[src]||src) + ' 会话</h3>'
    + '<div class="kv">格式：</div>'
    + '<label><input type="radio" name="xfmt" value="md" checked> Markdown（人读 · 合并单文件）</label>'
    + '<label><input type="radio" name="xfmt" value="jsonl"> IR JSONL（机读 · 一行一会话 · 与 C 库同构，可 push 回写）</label>'
    + '<div class="kv" style="margin-top:10px">会话（可手动勾选）：<span class="chipbtn on" data-days="0">全部</span>'
    + '<span class="chipbtn" data-days="7">最近 7 天</span><span class="chipbtn" data-days="30">最近 30 天</span>'
    + ' <span id="xcount" style="color:var(--dim);font-size:12px"></span></div>'
    + '<div class="xlist" id="xlist">' + (ms.length
        ? ms.map(m => '<label class="xitem"><input type="checkbox" checked value="'+esc(m.id)+'">'
            + '<span class="xt" title="'+esc(m.title||m.id)+'">'+esc((m.title||"(无标题)").slice(0,60))+'</span>'
            + '<span class="xd">'+esc(fmtShort(m.updated_at))+'</span></label>').join("")
        : '<div class="hint">该源无可导出的原生会话</div>') + '</div>'
    + '<div class="hint" style="margin:8px 0">口径 = 原生会话：排除 🤖子代理、🗑回收站、📥导入副本（正主在各自原生源）。</div>'
    + '<button class="mbtn" id="xgo">⬇ 下载</button><button class="mbtn ghost" id="xclose">关闭</button>'
  );
  const list = document.getElementById("xlist");
  const count = () => {
    const n = list.querySelectorAll("input:checked").length;
    document.getElementById("xcount").textContent = "已选 " + n + " / " + ms.length;
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
    if(!ids.length){ alert("请至少勾选一条会话"); return; }
    location.href = "/api/export-source?source=" + encodeURIComponent(src) + "&fmt=" + fmt
                  + "&ids=" + encodeURIComponent(ids.join(","));
    closeModal();
  };
  document.getElementById("xclose").onclick = closeModal;
}
