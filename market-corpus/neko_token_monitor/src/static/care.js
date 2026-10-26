(()=>{
 const panel=document.getElementById('carePanel'),status=document.getElementById('careStatus');
 const save=document.getElementById('careSave');
 const enabled=document.getElementById('careEnabled'),voice=document.getElementById('careVoice'),tasks=document.getElementById('taskFeedback'),character=document.getElementById('careCharacter');
 document.getElementById('careOpen').onclick=async()=>{
  for(const field of [enabled,voice,tasks,character,save])field.disabled=true;
  closeContextMenus();panel.hidden=false;status.textContent='读取提醒设置喵～';
  try{const r=await fetch('/api/care');if(!r.ok)throw Error('提醒服务暂时未连接喵～');const d=await r.json();enabled.checked=!!d.enabled;voice.checked=!!d.voice;tasks.checked=d.task_feedback!==false;character.value=d.character_name||'YUI';status.textContent=d.background?'同一档只提醒一次，进一步下降仍会提醒喵～':'当前仅预览，后台提醒尚未连接喵～';window.CatfoodQuiet?.renderList(document.getElementById('careNotices'),d);}
  catch(e){status.textContent=e.message;}
  finally{for(const field of [enabled,voice,tasks,character,save])field.disabled=false;}
 };
 document.getElementById('careClose').onclick=()=>panel.hidden=true;
 document.getElementById('careSave').onclick=async()=>{
  save.disabled=true;
  try{const r=await fetch('/api/care',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({enabled:enabled.checked,voice:voice.checked,task_feedback:tasks.checked,character_name:character.value})}),d=await r.json();if(!r.ok)throw Error(d.error||'陪伴设置暂未保存喵～');status.textContent='陪伴设置已保存喵～';window.CatfoodQuiet?.renderList(document.getElementById('careNotices'),d);}catch(e){status.textContent=e.message;}finally{save.disabled=false;}
 };
 window.addEventListener('catfood:recordend',()=>panel.hidden=true);
})();
