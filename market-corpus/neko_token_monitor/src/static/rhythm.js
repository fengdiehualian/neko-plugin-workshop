(()=>{
 const get=id=>document.getElementById(id),pose=get('rhythmPose'),clock=new window.CatfoodRhythmMotion();
 let state=null,busy=false,changing=false,frameId=0,shown=document.visibilityState!=='hidden';
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
 async function timedFetch(url,options={},milliseconds=2500){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),milliseconds);
  try{return await fetch(url,{...options,signal:controller.signal});}finally{clearTimeout(timeout);}
 }
 function render(){
  if(!state)return;
  get('rhythmToggle').textContent=state.enabled?'停止跟拍':'开始听音乐';
  get('rhythmOpen').textContent=state.enabled?'随音乐跳动 · 已开启':'随音乐跳动';
  if(document.activeElement!==get('rhythmStrength'))get('rhythmStrength').value=state.strength??.65;
  const messages={starting:'正在连接系统音频…',permission_required:'请在 macOS 设置中允许「猫粮节拍」\n屏幕与系统音频录制，随后会自动重试。',listening:'正在听音乐，等待稳定节拍喵～',manual:'手动校准',stale:'节拍连接中断，人物已停止跟拍。',stopped:'跟拍已关闭。',error:'节拍识别未能启动。'};
  get('rhythmStatus').textContent=state.error||(state.active?`${state.source==='manual'?'手动速度':'识别到节拍'} · ${Math.round(state.bpm)} BPM${state.source==='system'?'\n识别可信度 '+Math.round(state.confidence*100)+'%':''}`:messages[state.capture_status]||'等待音乐节拍');
  const unsupported=state.supported===false;
  for(const id of ['rhythmToggle','rhythmManual','rhythmAuto'])get(id).disabled=changing||unsupported;
  if(unsupported)get('rhythmStatus').textContent='自动跟拍目前支持 macOS 13 及以上；Windows 尚未接入。';
 }
 function accept(value){state=value;clock.accept(value,performance.now());render();}
 async function command(action,values={}){
  changing=true;render();let failure='';
  try{
   if(!csrf){await update();if(!csrf)throw Error('插件尚未连接');}
   const response=await timedFetch('/api/rhythm',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({action,...values})},5000);
   const value=await response.json();if(!response.ok)throw Error(value.error||'节拍设置失败');accept(value);
  }catch(error){failure=error.message;}
  finally{changing=false;render();if(failure)get('rhythmStatus').textContent=failure;}
 }
 async function poll(){
  if(busy||changing||!shown)return;busy=true;
  try{const response=await timedFetch('/api/rhythm');if(!response.ok)throw Error();accept(await response.json());}
  catch{accept({enabled:false,active:false,capture_status:'stale',error:'节拍连接中断，人物已停止跟拍。'});}
  finally{busy=false;}
 }
 function animate(now){
  const visible=shown&&!reduced.matches&&!document.body.classList.contains('record-inactive')&&!get('character').matches(':active');
  const p=clock.frame(now,visible);
  pose.style.transform=`translateY(${-p.lift}px) scale(${p.scaleX},${p.scaleY}) rotate(${p.rotate}deg)`;
  frameId=requestAnimationFrame(animate);
 }
 get('rhythmOpen').onclick=()=>{get('menu').hidden=true;get('rhythmPanel').hidden=false;poll();};
 get('rhythmClose').onclick=()=>get('rhythmPanel').hidden=true;
 get('rhythmToggle').onclick=()=>command(state?.enabled?'stop':'start');
 get('rhythmManual').onclick=()=>command('manual',{bpm:Number(get('rhythmBpm').value)});
 get('rhythmAuto').onclick=()=>command('auto');
 get('rhythmStrength').onchange=()=>command('strength',{strength:Number(get('rhythmStrength').value)});
 window.addEventListener('catfood:recordend',()=>{get('rhythmPanel').hidden=true;command('stop');});
 document.addEventListener('visibilitychange',()=>{
  shown=document.visibilityState!=='hidden';
  if(!shown){clock.accept(null,performance.now());cancelAnimationFrame(frameId);pose.style.transform='none';if(state?.enabled)command('stop');}
  else{frameId=requestAnimationFrame(animate);poll();}
 });
 window.addEventListener('pagehide',()=>{
  cancelAnimationFrame(frameId);
  if(csrf&&state?.enabled)fetch('/api/rhythm',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:'{"action":"stop"}',keepalive:true}).catch(()=>{});
 });
 poll();setInterval(poll,350);frameId=requestAnimationFrame(animate);
})();
