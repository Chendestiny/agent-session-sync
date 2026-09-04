/* 入口：刷新（丢弃缓存重载）+ 语言切换 + 哈希变化 + 首帧。模块延迟执行，DOM 已就绪 */
import { route } from "./router.js";
import { cachePurge } from "./api.js";
import { closeModal } from "./modal.js";
import { t, cur, setLang, onLangChange, applyStatic } from "./i18n/index.js";

document.getElementById("refresh").onclick = () => { cachePurge(); route(); };

/* 语言切换：按钮显示「目标语言」；切换后静态骨架 + 全部视图整段重绘（模板即语言） */
const langBtn = document.getElementById("lang");
const langBtnUpd = () => { langBtn.textContent = cur() === "zh" ? "EN" : "中文"; };
langBtnUpd();
langBtn.onclick = () => setLang(cur() === "zh" ? "en" : "zh");
onLangChange(() => {
  applyStatic();
  langBtnUpd();
  closeModal();
  route();
});

applyStatic();
window.addEventListener("hashchange", route);
route();
