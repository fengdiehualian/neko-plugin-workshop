(()=>{
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
 let paused=localStorage.getItem('catfood-quota-animation-paused')==='true',wanted=false,nativePending=false,inline=null,readyTimer=null,current='still',ended=false,endedSession=null,assetFailed=false;
 const button=document.getElementById('quotaAnimationToggle');
 function configuration(){const r=document.getElementById('returnSpot').getBoundingClientRect();return {home:{x:r.x,y:r.y,width:r.width,height:r.height},catches:window.catfoodGame?.roundCatches||0};}
 function nativeCommand(action){const bridge=window.webkit?.messageHandlers?.nekoWidget;if(!bridge||!window.catfoodChaseCapabilities?.desktop)return false;bridge.postMessage({action,session_id:recordingState?.id,...configuration()});return true;}
 function stop(){if(wanted)window.catfoodGameEvent?.({kind:'stop'});wanted=false;nativePending=false;clearTimeout(readyTimer);nativeCommand('chaseStop');inline?.stop();document.body.classList.remove('chase-native-active','chase-inline-active');}
 function apply(){
  const active=!document.hidden&&!ended&&window.catfoodQuotaOnline!==false&&(!window.catfoodQuotaReadAt||Date.now()-window.catfoodQuotaReadAt<25000)&&(browserPreview||(!document.body.classList.contains('record-inactive')&&!document.body.classList.contains('record-finished')));
  const q=window.catfoodQuotaState;
  const recovered=active&&!paused&&!q?.stale&&q?.percent<10&&window.catfoodGame?.calm();
  const requested=recovered?'recovered':window.CatfoodChaseMotion.reaction(q,{active,paused,reduced:reduced.matches});
  const next=assetFailed&&requested==='chase'?'cat-static':requested;
  document.body.dataset.quotaReaction=next;button.textContent=paused?'恢复晃动与追逐':'暂停晃动与追逐';
  inline?.configure(configuration());
  if(next===current)return;current=next;
  if(next!=='chase'){stop();return;}
  wanted=true;
  if(nativeCommand('chaseStart')){nativePending=true;readyTimer=setTimeout(()=>{if(nativePending){stop();document.body.dataset.quotaReaction='cat-static';}},8000);}
  else if(browserPreview){
   inline ||=new window.CatfoodChaseStage();inline.configure(configuration());const pending=inline;
   pending.ready.then(()=>{if(wanted&&current==='chase'&&inline===pending){pending.start();document.body.classList.add('chase-inline-active');}})
    .catch(()=>{assetFailed=true;stop();current='cat-static';document.body.dataset.quotaReaction='cat-static';});
  }
 }
 window.catfoodNativeChaseState=value=>{if(value.session_id&&value.session_id!==recordingState?.id)return;clearTimeout(readyTimer);nativePending=false;document.body.classList.toggle('chase-native-active',!!value.active&&wanted);if(!value.active&&wanted){wanted=false;document.body.dataset.quotaReaction='cat-static';}};
 button.onclick=()=>{if(wanted)window.catfoodGameEvent?.({kind:paused?'resume':'pause'});paused=!paused;localStorage.setItem('catfood-quota-animation-paused',String(paused));apply();};
 window.addEventListener('catfood:gamechange',()=>{apply();if(wanted)nativeCommand('chaseConfigure');});
 window.addEventListener('catfood:quota',apply);window.addEventListener('catfood:chasecapabilities',()=>{current='still';apply();});
 window.addEventListener('catfood:recordend',()=>{ended=true;endedSession=recordingState?.id;current='still';stop();document.body.dataset.quotaReaction='still';});
 new MutationObserver(()=>{if(recordingActive&&!exiting&&recordingState?.id!==endedSession)ended=false;apply();}).observe(document.body,{attributes:true,attributeFilter:['class']});
 reduced.addEventListener('change',apply);document.addEventListener('visibilitychange',()=>{if(document.hidden){stop();current='still';}else apply();});
 setInterval(apply,250);setInterval(()=>{if(wanted)nativeCommand('chaseHeartbeat');},1200);
 window.addEventListener('pagehide',()=>{stop();inline?.destroy();});apply();
})();
