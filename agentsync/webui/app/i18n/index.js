/* i18n：字典 + t() 插值 + localStorage 记忆 + 切换回调。
   约定：字典值可含可信 HTML（静态翻译）；动态参数由调用处 esc() 后再传入。
   回退链：当前语言 → zh → key 本身（漏翻肉眼可见，自纠错）。 */
import zh from "./zh.js";
import en from "./en.js";

const DICTS = {zh, en};
const KEY = "ass-lang";
let lang = "zh";
try{ const saved = localStorage.getItem(KEY); if(DICTS[saved]) lang = saved; }catch(e){}
const subs = new Set();

export function t(key, params){
  let s = DICTS[lang][key];
  if(s === undefined){ s = zh[key]; }        // 回退中文
  if(s === undefined) return key;             // 连 zh 都没有：直接露 key
  if(params) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
  return s;
}
export function cur(){ return lang; }
export function setLang(l){
  if(!DICTS[l] || l === lang) return false;
  lang = l;
  try{ localStorage.setItem(KEY, l); }catch(e){}
  subs.forEach(f => f());
  return true;
}
export function onLangChange(f){ subs.add(f); return () => subs.delete(f); }
/* 静态骨架翻译：index.html 中 data-i18n / data-i18n-title 元素（视图由模板重绘自带语言） */
export function applyStatic(){
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.title = t("app.title");
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
}
