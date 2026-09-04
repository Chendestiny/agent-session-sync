/* 主题（换肤）：html[data-theme] 切一组 CSS 变量，即时生效（无需重绘视图）。
   三套配色移植自 D:\Project\my-website\frontend\src\themes.js（4 选 3，原 midnight 深色退役）：
   indigo = 深空蓝紫（其 dark：近黑底 + indigo #6366f1 / cyan 光效）——默认
   olive  = 冷灰橄榄（其 olive：灰调底 + 低饱和高亮橄榄 #a3e635，工具感）
   sand   = 浅色暖沙（其 light：沙白纸面 + 赤陶 #c2410c）
   localStorage key=ass-theme；index.html 里有防闪烁内联脚本（首帧前先挂 attr）。 */
const KEY = "ass-theme";
export const THEMES = ["indigo", "olive", "sand"];
let cur = "indigo";
try{ const saved = localStorage.getItem(KEY); if(THEMES.includes(saved)) cur = saved; }catch(e){}
export function curTheme(){ return cur; }
export function setTheme(name){
  cur = THEMES.includes(name) ? name : "indigo";
  document.documentElement.dataset.theme = cur;
  try{ localStorage.setItem(KEY, cur); }catch(e){}
  return cur;
}
