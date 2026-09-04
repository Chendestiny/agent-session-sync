/* 入口：刷新（丢弃缓存重载）+ 哈希变化 + 首帧。模块延迟执行，DOM 已就绪 */
import { route } from "./router.js";
import { cachePurge } from "./api.js";
document.getElementById("refresh").onclick = () => { cachePurge(); route(); };
window.addEventListener("hashchange", route);
route();
