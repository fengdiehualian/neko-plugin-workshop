// Read the public hosted context only. No action, model call or game command.
export function startPanelRefresh({refresh,document,Observer=globalThis.IntersectionObserver,
  setTimeout=globalThis.setTimeout,clearTimeout=globalThis.clearTimeout,interval=3000}) {
  let disposed=false,intersecting=true,inflight=false,timer=null,failures=0
  const visible=()=>!disposed&&!document.hidden&&intersecting
  const cancel=()=>{if(timer!==null)clearTimeout(timer);timer=null}
  const schedule=()=>{
    cancel()
    if(visible()&&!inflight)timer=setTimeout(tick,Math.min(30000,interval*2**failures))
  }
  const tick=async()=>{
    timer=null
    if(!visible()||inflight)return
    inflight=true
    try {await refresh();failures=0} catch {failures=Math.min(4,failures+1)}
    finally {inflight=false;schedule()}
  }
  const changed=()=>schedule()
  document.addEventListener('visibilitychange',changed)
  const observer=typeof Observer==='function'?new Observer(entries=>{intersecting=entries.some(e=>e.isIntersecting);schedule()}):null
  observer?.observe(document.documentElement)
  schedule()
  return ()=>{disposed=true;cancel();observer?.disconnect();document.removeEventListener('visibilitychange',changed)}
}
