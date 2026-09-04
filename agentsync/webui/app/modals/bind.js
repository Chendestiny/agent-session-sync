/* 数据目录绑定弹窗：粘绝对路径 / 原生选目录 / 网页选文件夹预填；成功后重载当前视图 */
import { SRC_CN } from "../sources.js";
import { esc } from "../format.js";
import { cachePurge } from "../api.js";
import { openModal, closeModal } from "../modal.js";
import { t } from "../i18n/index.js";
import { route } from "../router.js";   // 环引用 router→overview→本模块：仅事件回调里调用，函数声明提升保证可用
export function openBindModal(src){
  const inp = 'style="flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg);padding:6px 8px;font-size:13px"';
  openModal(
    '<h3 style="margin:0 0 10px">' + t("bd.h", {name: esc(SRC_CN[src]||src)}) + '</h3>'
    + '<div class="hint">'+t("bd.hint")+'</div>'
    + '<div style="display:flex;gap:8px;margin:8px 0">'
    + '<input id="bpath" type="text" ' + inp + ' placeholder="'+t("bd.ph", {name: src==='zcode'?'zcode':src})+'">'
    + '<button class="mbtn ghost" id="bnative" style="margin:0;flex:none" title="'+t("bd.native.tip")+'">'+t("bd.native")+'</button>'
    + '<label class="mbtn ghost" style="margin:0;flex:none" title="'+t("bd.pickweb.tip")+'">'+t("bd.pickweb")+'<input type="file" webkitdirectory id="bpick" style="display:none"></label></div>'
    + '<div id="bmsg" class="hint" style="min-height:18px"></div>'
    + '<button class="mbtn" id="bgo">'+t("bd.go")+'</button>'
    + '<button class="mbtn ghost" id="bunbind">'+t("bd.unbind")+'</button>'
    + '<button class="mbtn ghost" id="bclose">'+t("common.close")+'</button>'
  );
  const msg = document.getElementById("bmsg");
  const pathEl = document.getElementById("bpath");
  document.getElementById("bpick").onchange = ev => {
    const fs = ev.target.files;
    if(!fs || !fs.length) return;
    const f = fs[0];
    let guess = (f.path && String(f.path)) || "";   // 非标准属性：Electron/旧内核才有
    if(!guess){
      const root = (f.webkitRelativePath || "").split("/")[0];
      guess = root ? "~/" + root : "";
    }
    if(guess) pathEl.value = guess;
    msg.textContent = guess.startsWith("~") ? t("bd.guess", {guess}) : t("bd.prefilled");
  };
  document.getElementById("bnative").onclick = async () => {
    msg.textContent = t("bd.waiting");
    try{
      const r = await fetch("/api/pick-folder", {method: "POST"});
      const d = await r.json();
      if(d.ok && d.path){
        pathEl.value = d.path;
        msg.textContent = t("bd.chosen", {path: d.path});
        post(d.path);   // 选完直接绑定
      } else {
        msg.textContent = d.ok ? t("bd.cancelled") : t("bd.native.fail", {err: d.error || ""});
      }
    }catch(e){ msg.textContent = t("bd.reqfail", {msg: e.message}); }
  };
  const post = async path => {
    try{
      const r = await fetch("/api/bind-path", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({source: src, path: path})});
      const d = await r.json();
      msg.textContent = d.detail || (d.ok ? "OK" : t("bd.fail"));
      if(d.ok) setTimeout(() => { closeModal(); cachePurge(); route(); }, 700);
    }catch(e){ msg.textContent = t("bd.reqfail", {msg: e.message}); }
  };
  document.getElementById("bgo").onclick = () => { msg.textContent = t("bd.checking"); post(pathEl.value.trim()); };
  document.getElementById("bunbind").onclick = () => post("");
  document.getElementById("bclose").onclick = closeModal;
}
