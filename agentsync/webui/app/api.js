/* API 访问 + 会话列表缓存（内存 + sessionStorage；点刷新才重新加载，F5 也不丢） */
export let metasCache = {};          // source -> metas[]（cachePurge 整体重置，live binding 对外可见）
export async function jget(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error("HTTP "+r.status+" "+(await r.text().catch(()=>"")).slice(0,200));
  return r.json();
}
export function cacheGet(src){
  if(metasCache[src] !== undefined) return Promise.resolve(metasCache[src]);
  try{
    const raw = sessionStorage.getItem("ass-metas-"+src);
    if(raw){ metasCache[src] = JSON.parse(raw); return Promise.resolve(metasCache[src]); }
  }catch(e){}
  return jget("/api/sessions?source="+encodeURIComponent(src)).then(ms => {
    metasCache[src] = ms;
    try{ sessionStorage.setItem("ass-metas-"+src, JSON.stringify(ms)); }catch(e){}
    return ms;
  });
}
export function cachePurge(){
  metasCache = {};
  try{
    Object.keys(sessionStorage).filter(k => k.startsWith("ass-metas-"))
      .forEach(k => sessionStorage.removeItem(k));
  }catch(e){}
}
