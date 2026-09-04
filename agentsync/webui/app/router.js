/* 哈希路由：#/ 总览 · #/sessions 列表 · #/session/<src>/<id> 详情 */
import { renderOverview } from "./views/overview.js";
import { renderSessions } from "./views/sessions.js";
import { renderDetail } from "./views/detail.js";
const app = document.getElementById("app");
export function route(){
  const h = location.hash || "#/";
  const view = h.startsWith("#/session/") ? "sessions" : (h === "#/sessions" ? "sessions" : "overview");
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  app.classList.remove("fit");   // 仅总览视图整页零滚动（renderOverview 里再加回）
  const parts = h.slice(2).split("/").map(decodeURIComponent);
  if(parts[0] === "session" && parts[1] && parts[2]) renderDetail(parts[1], parts[2]);
  else if(parts[0] === "sessions") renderSessions();
  else renderOverview();
}
