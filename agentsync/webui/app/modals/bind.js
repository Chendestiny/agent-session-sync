/* 数据目录绑定弹窗：粘绝对路径 / 原生选目录 / 网页选文件夹预填；成功后重载当前视图 */
import { SRC_CN } from "../sources.js";
import { esc } from "../format.js";
import { cachePurge } from "../api.js";
import { openModal, closeModal } from "../modal.js";
import { route } from "../router.js";   // 环引用 router→overview→本模块：仅事件回调里调用，函数声明提升保证可用
export function openBindModal(src){
  const inp = 'style="flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:var(--fg);padding:6px 8px;font-size:13px"';
  openModal(
    '<h3 style="margin:0 0 10px">绑定 ' + esc(SRC_CN[src]||src) + ' 数据目录</h3>'
    + '<div class="hint">粘贴该源存储的绝对路径——应用根目录或 sessions / state.db 等直接目标均可，后端自动识别结构并校验。<br>'
    + '注：浏览器安全策略不允许网页读取所选文件夹的绝对路径，「选文件夹」只用于预填文件夹名，仍需补全。</div>'
    + '<div style="display:flex;gap:8px;margin:8px 0">'
    + '<input id="bpath" type="text" ' + inp + ' placeholder="例如 C:\\Users\\me\\.' + (src==='zcode'?'zcode':src) + '">'
    + '<button class="mbtn ghost" id="bnative" style="margin:0;flex:none" title="本地服务弹系统对话框，直接拿绝对路径">原生选择…</button>'
    + '<label class="mbtn ghost" style="margin:0;flex:none" title="浏览器安全策略拿不到绝对路径，只做预填">网页选文件夹<input type="file" webkitdirectory id="bpick" style="display:none"></label></div>'
    + '<div id="bmsg" class="hint" style="min-height:18px"></div>'
    + '<button class="mbtn" id="bgo">校验并保存</button>'
    + '<button class="mbtn ghost" id="bunbind">解绑（恢复自动探测）</button>'
    + '<button class="mbtn ghost" id="bclose">关闭</button>'
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
    msg.textContent = guess.startsWith("~") ? "已预填文件夹名（" + guess + "）——请补全为绝对路径后保存"
                                            : "已带入所选路径，确认无误后保存";
  };
  document.getElementById("bnative").onclick = async () => {
    msg.textContent = "等待系统对话框…（取消则无操作）";
    try{
      const r = await fetch("/api/pick-folder", {method: "POST"});
      const d = await r.json();
      if(d.ok && d.path){
        pathEl.value = d.path;
        msg.textContent = "已选：" + d.path + "（自动校验保存中…）";
        post(d.path);   // 选完直接绑定
      } else {
        msg.textContent = d.ok ? "已取消选择" : ("原生对话框不可用：" + (d.error || "") + "——请改用粘贴路径");
      }
    }catch(e){ msg.textContent = "请求失败：" + e.message; }
  };
  const post = async path => {
    try{
      const r = await fetch("/api/bind-path", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({source: src, path: path})});
      const d = await r.json();
      msg.textContent = d.detail || (d.ok ? "OK" : "失败");
      if(d.ok) setTimeout(() => { closeModal(); cachePurge(); route(); }, 700);
    }catch(e){ msg.textContent = "请求失败：" + e.message; }
  };
  document.getElementById("bgo").onclick = () => { msg.textContent = "校验中…"; post(pathEl.value.trim()); };
  document.getElementById("bunbind").onclick = () => post("");
  document.getElementById("bclose").onclick = closeModal;
}
