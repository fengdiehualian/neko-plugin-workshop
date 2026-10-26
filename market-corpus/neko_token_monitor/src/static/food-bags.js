/* Bags represent relative account allowance, never context length or used tokens. */
(function(root){
 'use strict';
 const mood=typeof module==='object'&&module.exports?require('./quota-mood.js'):root.CatfoodQuotaMood;
 const brands={
  codex:{label:'Codex',color:'#548b7d',icon:'codex'},
  gpt:{label:'GPT',color:'#548b7d',icon:'gpt'},
  claude:{label:'Claude',color:'#c77d5c',icon:'claude'},
  deepseek:{label:'DeepSeek',color:'#5c7fc5',icon:'deepseek'},
  trae:{label:'TRAE',color:'#599e89',icon:'trae'},
  workbuddy:{label:'WorkBuddy',color:'#6d7ebf',icon:'workbuddy.svg'},
  gemini:{label:'Gemini',color:'#8c7ac4',icon:'gemini'},
  kimi:{label:'Kimi',color:'#7e8597',icon:'kimi'},
  qwen:{label:'Qwen',color:'#9274c4',icon:'qwen'},
  glm:{label:'GLM',color:'#7282aa'},doubao:{label:'豆包',color:'#85aa87'},
  jev:{label:'JEV',color:'#bd7582'},openrouter:{label:'OpenRouter',color:'#8585a9'},
  siliconflow:{label:'硅基流动',color:'#8a83bc'},other:{label:'猫粮',color:'#8c92b4'}
 };
 const profiles={codex:'codex',trae:'trae',workbuddy:'workbuddy',deepseek:'deepseek',deepseek_chat:'deepseek',deepseek_harness:'deepseek',openai:'gpt',chatgpt:'gpt',anthropic:'claude',claude:'claude',gemini:'gemini',kimi:'kimi',kimi_api_cn:'kimi',kimi_api_global:'kimi',qwen:'qwen',qwen_api:'qwen',doubao:'doubao',glm:'glm',jev:'jev',openrouter:'openrouter',siliconflow:'siliconflow'};
 function brand(state={}){
  const c=state?.config||{},profile=state?.profile;
  // A shared account's logo belongs to its provider, even when its model differs.
  let key=profiles[profile]||profiles[c.source];
  if(!key){
   const model=String(c.model||'').toLowerCase();
   key=/deepseek/.test(model)?'deepseek':/claude|anthropic/.test(model)?'claude':/codex/.test(model)?'codex':/gpt|openai/.test(model)?'gpt':/gemini/.test(model)?'gemini':/kimi|moonshot/.test(model)?'kimi':/qwen|通义|千问/.test(model)?'qwen':/trae/.test(model)?'trae':/workbuddy/.test(model)?'workbuddy':'other';
  }
  return {key,...brands[key]};
 }
 function plan(state){
  const q=mood.quota(state);
  const percent=q&&Number.isFinite(q.percent)&&q.percent>=0&&q.percent<=100?q.percent:null;
  const count=percent>=75?3:percent!==null&&percent>=50?2:1;
  return {...brand(state),profile:state?.profile||null,percent,count,stale:!!(q?.stale||state?.stale||state?.error),basis:q?.basis||'额度比例未知；保留一袋作为补充入口'};
 }
 // Portraits share the 1000-square face layout; use the rendered rectangle so
 // window scaling, costume changes and character movement share one target.
 function faceRect(rect,cat=false){
  const [left,top,right,bottom]=cat?[.1,.04,.9,.8]:[.30,.46,.75,.80];
  return {left:rect.left+rect.width*left,top:rect.top+rect.height*top,right:rect.left+rect.width*right,bottom:rect.top+rect.height*bottom};
 }
 function contains(rect,x,y){
  return !!rect&&Number.isFinite(x)&&Number.isFinite(y)&&x>=rect.left&&x<=rect.right&&y>=rect.top&&y<=rect.bottom;
 }
 const bagShape='<svg viewBox="0 0 54 82" aria-hidden="true"><ellipse class="bag-floor" cx="27" cy="78" rx="22" ry="3"/><path class="bag-paper" d="M8 9 Q27 4 46 9 L43 20 Q51 43 49 71 Q27 78 5 71 Q3 43 11 20 Z"/><path class="bag-fold" d="M8 9 Q27 5 46 9 L43 20 Q27 17 11 20 Z M6 65 Q27 70 48 65 L49 71 Q27 78 5 71 Z"/><path class="bag-seam" d="M13 12 L41 12 M11 69 Q27 73 43 69 M10 27 Q6 42 9 55"/><g class="bag-paw"><ellipse cx="27" cy="60" rx="4" ry="3"/><circle cx="22" cy="56" r="1.5"/><circle cx="26" cy="54" r="1.5"/><circle cx="30" cy="55" r="1.5"/><circle cx="33" cy="58" r="1.5"/></g><path class="bag-empty-crease" d="M10 30 L22 40 L12 48 M43 32 L32 46 L42 55 M16 58 L28 52 L38 60"/></svg>';
 function mount(){
  const target=root.document?.getElementById('foodBags');if(!target)return;
  const doc=root.document,character=doc.getElementById('character');
  let signature='',latest=null,drag=null;
  function face(){
   if(!character||!character.getClientRects().length)return null;
   const portrait=character.querySelector('.yuiPortrait'),cat=doc.getElementById('catForm');
   const image=portrait?.getClientRects().length?portrait:cat;
   if(!image?.getClientRects().length)return null;
   const box=image.getBoundingClientRect();if(!box.width||!box.height)return null;
   return faceRect(box,image===cat);
  }
  function overFace(x,y){return contains(face(),x,y)&&!!doc.elementFromPoint(x,y)?.closest('#character');}
  const open=profile=>{if(profile)root.CatfoodTopUp?.open(profile);};
  function move(e){
   const d=drag;if(!d||e.pointerId!==d.id)return;
   if(!d.moved&&Math.hypot(e.clientX-d.x,e.clientY-d.y)<5)return;
   if(!d.moved){
    d.moved=true;d.ghost=d.bag.cloneNode(true);d.ghost.classList.add('food-bag-ghost');
    d.ghost.removeAttribute('role');d.ghost.removeAttribute('tabindex');d.ghost.setAttribute('aria-hidden','true');
    d.ghost.style.setProperty('--bag-accent',d.color);doc.body.append(d.ghost);d.bag.classList.add('bag-held');
   }
   e.preventDefault();e.stopPropagation();
   d.ghost.style.left=(e.clientX-d.width/2)+'px';d.ghost.style.top=(e.clientY-d.height/2)+'px';
   d.ghost.style.width=d.width+'px';d.ghost.style.height=d.height+'px';
   character?.classList.toggle('food-drop-ready',overFace(e.clientX,e.clientY));
  }
  function finish(e){
   const d=drag;if(!d||(e&&e.pointerId!==d.id))return;
   const accepted=e?.type==='pointerup'&&d.moved&&overFace(e.clientX,e.clientY);
   drag=null;d.ghost?.remove();d.bag.classList.remove('bag-held');character?.classList.remove('food-drop-ready');
   if(d.bag.hasPointerCapture(d.id))d.bag.releasePointerCapture(d.id);
   if(e){e.stopPropagation();if(e.cancelable)e.preventDefault();}
   // Polls may change the current platform while the pointer is captured.
   // Always open the provider carried by this bag, then redraw the shelf.
   if(accepted)open(d.profile);
   render(latest);
  }
  target.addEventListener('pointerdown',e=>{
   const bag=e.target.closest('.food-bag');
   if(!bag||drag||!e.isPrimary||e.button!==0||e.ctrlKey||!latest?.profile)return;
   e.preventDefault();e.stopPropagation();
   const box=bag.getBoundingClientRect(),value=plan(latest);
   drag={id:e.pointerId,bag,profile:latest.profile,color:value.color,x:e.clientX,y:e.clientY,width:box.width,height:box.height,moved:false};
   bag.setPointerCapture(e.pointerId);
  });
  target.addEventListener('pointermove',move);
  for(const type of ['pointerup','pointercancel','lostpointercapture'])target.addEventListener(type,finish);
  target.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();});
  doc.addEventListener('keydown',e=>{
   if(e.key==='Escape'&&drag){e.preventDefault();e.stopPropagation();finish();}
   else if(!drag&&['Enter',' '].includes(e.key)&&e.target.closest('.food-bag')){e.preventDefault();e.stopPropagation();open(latest?.profile);}
  });
  root.addEventListener('blur',()=>finish());
  root.addEventListener('resize',()=>finish());
  if(root.MutationObserver)new root.MutationObserver(()=>{
   if(drag&&(doc.body.classList.contains('bubble-hidden')||doc.body.classList.contains('record-finished')||
      (doc.body.classList.contains('record-inactive')&&!doc.body.classList.contains('browser-preview'))))finish();
  }).observe(doc.body,{attributes:true,attributeFilter:['class']});
  function render(state){
   latest=state;if(drag)return;
   const value=plan(state);target.hidden=false;
   const text=(value.stale?'上次数据，':'')+value.label+(value.percent===null?' 猫粮额度待更新':' 猫粮剩余 '+new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(value.percent)+'%')+'，'+value.count+' 袋';
   target.setAttribute('aria-label',text);target.title=text+'。拖到 YUI 脸上松开以打开对应充值入口；75% 起 3 袋，50% 起 2 袋，其余保留 1 袋。'+value.basis;
   target.dataset.brand=value.key;if(value.percent===null)delete target.dataset.remaining;else target.dataset.remaining=String(value.percent);
   target.dataset.count=String(value.count);target.classList.toggle('old-bags',value.stale);
   const next=value.key+'|'+value.count+'|'+value.profile;
   if(signature===next)return;signature=next;target.replaceChildren();
   target.style.setProperty('--bag-accent',value.color);
   for(let i=0;i<value.count;i++){
    const bag=root.document.createElement('span');bag.className='food-bag';bag.setAttribute('role','button');bag.tabIndex=value.profile?0:-1;bag.setAttribute('aria-label','拖给 YUI 补充 '+value.label+' 猫粮，或按回车打开入口');bag.innerHTML=bagShape;
    const mark=root.document.createElement('span');mark.className='bag-brand';
    const fallback=()=>{mark.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 14c2-2 2-5 6-5s4 3 6 5c4 5-1 8-6 5-5 3-10 0-6-5Z"/><ellipse fill="currentColor" cx="4" cy="9" rx="2" ry="3" transform="rotate(-25 4 9)"/><ellipse fill="currentColor" cx="9" cy="5" rx="2" ry="3"/><ellipse fill="currentColor" cx="15" cy="5" rx="2" ry="3"/><ellipse fill="currentColor" cx="20" cy="9" rx="2" ry="3" transform="rotate(25 20 9)"/></svg>';};
    if(value.icon){const icon=root.document.createElement('img');icon.src='/brands/'+value.icon+(value.icon.endsWith('.svg')?'':'.png');icon.alt='';icon.draggable=false;icon.onerror=fallback;mark.append(icon);}else fallback();
    bag.append(mark);target.append(bag);
   }
  }
  root.catfoodRefreshBags=render;
 }
 const api={brand,plan,faceRect,contains};
 if(typeof module==='object'&&module.exports)module.exports=api;else{root.CatfoodFoodBags=api;mount();}
})(typeof window==='object'?window:globalThis);
