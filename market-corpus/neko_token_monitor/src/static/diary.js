(()=>{
 const get=id=>document.getElementById(id),panel=get('historyPanel');
 let busy=false,last=0,storyKey='',detailKey='';
 const duration=seconds=>seconds<60?seconds+' 秒':seconds<3600?Math.floor(seconds/60)+' 分钟':Math.floor(seconds/3600)+' 小时 '+Math.floor(seconds%3600/60)+' 分钟';
 async function refresh(force=false){
  if(panel.hidden||busy||!force&&Date.now()-last<5000)return;
  busy=true;
  try{
   const response=await fetch('/api/diary');if(!response.ok)throw Error('日记暂时未连接，本机记录仍保留喵～');
   const d=await response.json();last=Date.now();get('diaryDate').textContent=d.date||'今天';
   get('diaryDuration').textContent=duration(d.seconds||0);get('diaryPets').textContent=(d.partial_counts?'至少 ':'')+(d.pet||0)+' 次';get('diaryAsks').textContent=(d.partial_counts?'至少 ':'')+(d.ask||0)+' 次';
   get('diaryCoverage').textContent=[d.partial_duration?'陪伴时长从此版开始统计，旧记录仍保留。':'成功进入监测后，才开始记录陪伴时间。',d.partial_counts?'旧版跨日互动缺少点击时间，次数显示可确认的下限。':''].filter(Boolean).join('');
   const stories=JSON.stringify(d.entries||[]);
   if(stories!==storyKey){
    storyKey=stories;get('diaryTimeline').replaceChildren();
    for(const entry of d.entries||[]){const row=document.createElement('article');row.className='diaryEntry';row.setAttribute('role','listitem');const time=document.createElement('time');time.dateTime=entry.at;time.textContent=new Date(entry.at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});const text=document.createElement('p');text.textContent=entry.text;row.append(time,text);get('diaryTimeline').append(row);}
    if(!(d.entries||[]).length){const empty=document.createElement('p');empty.className='diaryEmpty';empty.textContent='今天还没有新的陪伴故事，安静待着也很好喵～';get('diaryTimeline').append(empty);}
   }
   const details=JSON.stringify([d.technical,d.deliveries]);
   if(details!==detailKey){
    detailKey=details;get('historyRows').replaceChildren();
    for(const row of d.technical||[]){const item=document.createElement('details');const title=document.createElement('summary');title.textContent=new Date(row.start).toLocaleString('zh-CN')+' · '+(row.active?'记录中':row.memory_saved?'已同步记忆':'本机已保存');item.append(title);for(const note of row.status_events||[]){const text=document.createElement('p');text.textContent=note.at+' · '+note.message;item.append(text);}get('historyRows').append(item);}
    get('diaryDeliveries').replaceChildren();for(const row of d.deliveries||[]){const text=document.createElement('p');text.textContent=row.at+' · '+row.text;get('diaryDeliveries').append(text);}
   }
   get('diaryStatus').textContent='';
  }catch(error){get('diaryStatus').textContent=error.message;}
  finally{busy=false;}
 }
 window.catfoodRefreshDiary=refresh;
 setInterval(()=>refresh(),5000);
})();
