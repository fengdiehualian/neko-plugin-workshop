(()=>{
 const key='catfood-chase-game',storage=()=>{try{return JSON.parse(localStorage.getItem(key)||'{}')}catch{return {}}};
 const game=new window.CatfoodChaseGame.Game(storage());window.catfoodGame=game;
 const persist=()=>{try{localStorage.setItem(key,JSON.stringify(game.export()));}catch{}};
 const publish=()=>{persist();window.dispatchEvent(new Event('catfood:gamechange'));};
 window.catfoodGameCatch=detail=>{
  if((!browserPreview&&!recordingActive)||exiting||document.body.classList.contains('record-finished')||!game.caught(detail?.event_id))return;
  recordAction('game',{kind:'catch',event_id:detail.event_id}).catch(()=>{});publish();
 };
 window.catfoodGameReturned=()=>{
  if((!browserPreview&&!recordingActive)||exiting||document.body.classList.contains('record-finished')||!window.catfoodQuotaState||window.catfoodQuotaState.percent>=10||game.calm())return;
  game.recover();recordAction('game',{kind:'rescue'}).catch(()=>{});publish();say('呼……被抱回来就安心多了喵～');
 };
 window.catfoodGameEvent=detail=>{
  if((!browserPreview&&!recordingActive)||exiting||document.body.classList.contains('record-finished')||!['start','reverse','counter_tag','flee','pause','resume','stop'].includes(detail?.kind))return;
  recordAction('game',{kind:detail.kind,event_id:detail.event_id||crypto.randomUUID()}).catch(()=>{});
 };
 window.addEventListener('catfood:gameevent',e=>window.catfoodGameEvent(e.detail));
 window.addEventListener('catfood:chasebite' ,e=>window.catfoodGameCatch(e.detail));
 window.addEventListener('catfood:catreturned',()=>window.catfoodGameReturned());
 window.addEventListener('catfood:recordsnapshot',e=>{const before=game.catches;game.merge(e.detail?.game_totals);persist();if(before!==game.catches)window.dispatchEvent(new Event('catfood:gamechange'));});
 const panel=document.getElementById('chaseGamePanel'),input=document.getElementById('hungerInterval'),status=document.getElementById('chaseGameStatus');
 document.getElementById('chaseGameOpen').onclick=()=>{closeContextMenus();input.value=game.intervalSeconds;status.textContent='';panel.hidden=false;};
 document.getElementById('chaseGameClose').onclick=()=>panel.hidden=true;
 document.getElementById('chaseGameSave').onclick=()=>{try{game.configure(input.value);publish();status.textContent='已保存，再次饥饿的间隔为 '+game.intervalSeconds+' 秒喵～';}catch(e){status.textContent=e.message;}};
 window.addEventListener('catfood:recordend',()=>{panel.hidden=true;});
})();
