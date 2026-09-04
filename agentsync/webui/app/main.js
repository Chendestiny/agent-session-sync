/* 入口：刷新（丢弃缓存重载）+ 主题/语言切换 + 哈希变化 + 首帧。模块延迟执行，DOM 已就绪 */
import { route } from "./router.js";
import { cachePurge } from "./api.js";
import { closeModal } from "./modal.js";
import { cur, setLang, onLangChange, applyStatic } from "./i18n/index.js";
import { curTheme, setTheme } from "./theme.js";

document.getElementById("refresh").onclick = () => { cachePurge(); route(); };

/* 主题：CSS 变量整体切换即时生效；头部图标按钮 + 自绘下拉（名称/色块/勾选位走 data-i18n） */
setTheme(curTheme());   // 归一化（内联脚本已挂过 attr，这里兜底+防坏值）
const themeBtn = document.getElementById("themeBtn");
const themeMenu = document.getElementById("themeMenu");
const themeUpd = () => {
  themeMenu.querySelectorAll(".themeItem").forEach(it =>
    it.classList.toggle("on", it.dataset.value === curTheme()));
};
themeUpd();
themeBtn.onclick = e => { e.stopPropagation(); themeMenu.hidden = !themeMenu.hidden; };
themeMenu.querySelectorAll(".themeItem").forEach(it => it.onclick = () => {
  setTheme(it.dataset.value);
  themeUpd();
  themeMenu.hidden = true;
});
document.addEventListener("click", e => {   // 点菜单外任意处收起
  if(!themeMenu.hidden && !e.target.closest("#themeWrap")) themeMenu.hidden = true;
});
document.addEventListener("keydown", e => { if(e.key === "Escape") themeMenu.hidden = true; });

/* 语言切换：按钮显示「目标语言」；切换后静态骨架 + 全部视图整段重绘（模板即语言） */
const langBtn = document.getElementById("lang");
const langBtnUpd = () => { langBtn.textContent = cur() === "zh" ? "EN" : "中文"; };
langBtnUpd();
langBtn.onclick = () => setLang(cur() === "zh" ? "en" : "zh");
onLangChange(() => {
  applyStatic();
  langBtnUpd();
  themeUpd();   // 勾选位与语言无关，兜底同步
  closeModal();
  route();
});

applyStatic();
window.addEventListener("hashchange", route);
route();
