/* 弹窗底座：遮罩 + 点外部关闭 + 复制到剪贴板（含 execCommand 回退） */
export function closeModal(){ const m = document.getElementById("modalOv"); if(m) m.remove(); }
export function openModal(html){
  closeModal();
  const ov = document.createElement("div");
  ov.className = "modal"; ov.id = "modalOv";
  ov.innerHTML = '<div class="box">'+html+'</div>';
  ov.addEventListener("click", e => { if(e.target === ov) closeModal(); });
  document.body.appendChild(ov);
}
export function copyText(t, btn){
  const done = ok => { if(btn){ const old = btn.textContent; btn.textContent = ok ? "已复制 ✓" : "复制失败";
    setTimeout(() => { btn.textContent = old; }, 1500); } };
  const fallback = () => {
    const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta);
    ta.select(); let ok = false; try{ ok = document.execCommand("copy"); }catch(e){}
    ta.remove(); done(ok);
  };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(() => done(true), fallback);
  } else fallback();
}
