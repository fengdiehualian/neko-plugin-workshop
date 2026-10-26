(function(root){
 'use strict';
 const uid=()=>root.crypto?.randomUUID?.()||Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
 class ChaseStage{
  constructor(parent=document.body){
   this.layer=document.createElement('div');this.layer.className='catfood-chase-stage';this.layer.hidden=true;this.layer.dataset.animation='dsh-whale-smooth-yui-hit-v4';
   const shadowId='shade-'+uid();
   this.layer.innerHTML='<span class="chase-runner chase-maid" aria-hidden="true"><span class="chase-flip"><span class="chase-cell"><svg class="chase-angry-face" viewBox="0 0 512 512"><defs><linearGradient id="'+shadowId+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#11131d"/><stop offset=".48" stop-color="#171924" stop-opacity=".8"/><stop offset="1" stop-color="#171924" stop-opacity="0"/></linearGradient></defs><path class="face-shadow" fill="url(#'+shadowId+')"/><path class="anger-vein" d="M438 151q-9 1-9 10m13-18q-1-9-10-10m17 13q9-1 10-10m-12 18q1 9 10 9"/></svg><span class="maid-steam">♨</span></span></span></span><span class="chase-runner chase-cat" role="button" tabindex="0" aria-label="拖动小白猫回到原站位，恢复 YUI 精神"><span class="chase-flip"><img class="chase-cat-walk" src="/chase/neko-white-cat-walk.gif" alt="N.E.K.O 原版小白猫"><img class="chase-cat-bite" src="/chase/neko-white-cat-react.gif" alt=""><span class="chase-cell chase-yui-cell"><img class="chase-sprite-sheet" src="/chase/yui-dsh-style-run.png" alt="Q版 YUI 受惊跑路"></span></span></span><span class="chase-bite-mark" aria-hidden="true"><svg viewBox="0 0 64 48"><g class="bite-upper"><path d="M8 16Q32 0 56 16L48 25L40 18L32 28L24 18L16 25Z"/></g><g class="bite-lower"><path d="M10 36Q32 50 54 36L46 28L38 34L32 26L26 34L18 28Z"/></g></svg></span><span class="chase-shock" aria-hidden="true">✦</span><span class="chase-cuddle-burst" aria-hidden="true"></span><span class="chase-turn-flash" aria-hidden="true">!</span><span class="chase-home" aria-hidden="true"><span>♡</span><strong>放回这里喵～</strong></span><span class="chase-feedback" role="status" aria-live="polite" hidden></span>';
   parent.append(this.layer);this.cat=this.layer.querySelector('.chase-cat');this.maid=this.layer.querySelector('.chase-maid');this.running=false;this.frameId=0;
   // Replace only the fish images. The YUI sheet and original white-cat GIFs stay unchanged.
   this.whaleSheets=Object.fromEntries(Object.entries(root.CatfoodWhaleClips).filter(([key])=>!key.startsWith('yui')).map(([key,clip])=>{
    const image=document.createElement('img');image.className='chase-whale-sheet';image.alt='';image.src=clip.src;image.hidden=true;
    this.maid.querySelector('.chase-cell').prepend(image);return[key,image];
   }));this.whaleSheet=this.whaleSheets.run;
   this.whalePlayer=new root.CatfoodWhaleAnimation.Player();
   this.maidSheets=Object.values(this.whaleSheets);this.yuiSheet=this.cat.querySelector('.chase-sprite-sheet');this.turnFlash=this.layer.querySelector('.chase-turn-flash');this.turnFlash.hidden=true;this.face=this.maid.querySelector('svg');this.mark=this.layer.querySelector('.chase-bite-mark');this.shock=this.layer.querySelector('.chase-shock');this.feedback=this.layer.querySelector('.chase-feedback');this.homeNode=this.layer.querySelector('.chase-home');this.burst=this.layer.querySelector('.chase-cuddle-burst');
   this.burst.innerHTML=Array.from({length:9},(_,i)=>'<i>'+(i%3?'♡':'✦')+'</i>').join('');
   this.halo=document.createElement('span');this.halo.className='chase-daze';this.halo.setAttribute('aria-hidden','true');this.halo.innerHTML='<i>✦</i><i>✧</i><i>✦</i>';this.halo.hidden=true;this.layer.append(this.halo);
   this.ring=document.createElement('span');this.ring.className='chase-hit-ring';this.ring.setAttribute('aria-hidden','true');this.ring.hidden=true;this.layer.append(this.ring);
   this.yuiClip=root.CatfoodWhaleClips.yui;this.yuiSheet.src=this.yuiClip.src;this.yuiSheet.style.width=this.yuiClip.columns*100+'%';this.yuiSheet.style.height=this.yuiClip.rows*100+'%';
   this.yuiHitClip=root.CatfoodWhaleClips.yuiHit;this.yuiHitSheet=document.createElement('img');this.yuiHitSheet.className='chase-sprite-sheet chase-yui-hit';this.yuiHitSheet.alt='YUI 缩身受惊、晕乎乎再恢复';this.yuiHitSheet.src=this.yuiHitClip.src;this.yuiHitSheet.hidden=true;
   this.yuiHitSheet.style.width=this.yuiHitClip.columns*100+'%';this.yuiHitSheet.style.height=this.yuiHitClip.rows*100+'%';this.yuiSheet.parentElement.append(this.yuiHitSheet);
   this.ready=Promise.all([...this.layer.querySelectorAll('img')].map(image=>image.decode()));
   this.mark.hidden=true;this.shock.hidden=true;this.homeNode.hidden=true;this.burst.hidden=true;parent.append(this.feedback);
   this.simulation=new root.CatfoodChaseMotion.Pursuit();this.previousSeconds=0;this.reportedBites=0;this.knownCatches=0;this.catchBase=0;this.home=null;this.drag=null;this.returning=false;this.lastGeometry=-1;
   this.safeTop=parseFloat(getComputedStyle(this.layer).getPropertyValue('--catfood-feedback-top'))||8;
   this.cat.addEventListener('pointerdown',e=>{if(e.button!==0||!e.isPrimary)return;e.preventDefault();e.stopPropagation();if(this.beginDrag({x:e.clientX,y:e.clientY})){this.pointer=e.pointerId;this.cat.setPointerCapture(e.pointerId);}});
   this.cat.addEventListener('pointermove',e=>{if(e.pointerId===this.pointer)this.moveDrag({x:e.clientX,y:e.clientY});});
   this.cat.addEventListener('pointerup',e=>{if(e.pointerId!==this.pointer)return;this.endDrag({x:e.clientX,y:e.clientY});this.pointer=null;if(this.cat.hasPointerCapture(e.pointerId))this.cat.releasePointerCapture(e.pointerId);});
   this.cat.addEventListener('pointercancel',()=>this.cancelDrag());
   this.cat.addEventListener('lostpointercapture',()=>{if(this.drag)this.cancelDrag();});
   this.cat.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&this.home){e.preventDefault();const r=this.cat.getBoundingClientRect();this.beginDrag({x:r.x+r.width/2,y:r.y+r.height/2});this.endDrag({x:this.home.x+this.home.width/2,y:this.home.y+this.home.height/2});}else if(e.key==='Escape')this.cancelDrag();});
  }
  event(kind,suffix=kind){root.dispatchEvent(new CustomEvent('catfood:gameevent',{detail:{kind,event_id:this.runId+'-'+suffix}}));}
  configure(value={}){
   if(Number.isSafeInteger(value.catches)&&value.catches>=0){this.knownCatches=value.catches;if(!this.simulation.bites)this.catchBase=value.catches;}
   const h=value.home;if(h&&['x','y','width','height'].every(k=>Number.isFinite(h[k]))&&h.width>0&&h.height>0)this.home={...h};
  }
  facePose(frame,level){
   this.maid.dataset.anger=level;
   if(!frame.face)return;
   const [cx,cy,rx,ry]=frame.face.map(n=>n*512);
   this.face.querySelector('.face-shadow').setAttribute('d',`M${cx-rx} ${cy}a${rx} ${ry} 0 1 0 ${rx*2} 0a${rx} ${ry} 0 1 0 ${-rx*2} 0`);
   const x=cx+rx,y=cy-ry;
   this.face.querySelector('.anger-vein').setAttribute('d',`M${x-8} ${y}q8 0 8 8m-8-17q8 0 8-8m8 17q0-8 8-8m-8 17q0 8 8 8`);
   this.maid.querySelector('.maid-steam').style.left=(cx+rx)/512*100+'%';
   this.maid.querySelector('.maid-steam').style.top=Math.max(2,(cy-ry)/512*100-7)+'%';
  }
  drawWhale(seconds,bites,level,reversed){
   const frame=this.whalePlayer.frame(seconds,{bites,level,reversed}),{clip,index}=frame;
   if(this.whaleKey!==frame.key){this.whaleKey=frame.key;for(const [key,image]of Object.entries(this.whaleSheets))image.hidden=key!==frame.key;this.whaleSheet=this.whaleSheets[frame.key];this.whaleSheet.style.width=clip.columns*100+'%';this.whaleSheet.style.height=clip.rows*100+'%';}
   const signature=frame.key+':'+index+':'+level;
   if(this.whaleSignature!==signature){
    this.whaleSignature=signature;this.whaleSheet.style.transform=`translate3d(${-(index%clip.columns)*100/clip.columns}%,${-Math.floor(index/clip.columns)*100/clip.rows}%,0)`;
    this.maid.dataset.clip=frame.key;this.maid.dataset.frame=index;this.facePose(frame,level);
   }
  }
  beginDrag(point){
   if(!this.running||this.drag||this.returning)return false;const r=this.cat.getBoundingClientRect();
   this.drag={point:{...point},offset:{x:point.x-r.x-r.width/2,y:point.y-r.y-r.height/2}};this.blend=null;this.layer.classList.add('is-dragging');this.homeNode.hidden=!this.home;
   root.dispatchEvent(new CustomEvent('catfood:chasedrag',{detail:{active:true}}));return true;
  }
  moveDrag(point){if(this.drag)this.drag.point={...point};}
  endDrag(point){
   if(!this.drag)return false;this.moveDrag(point);const held=this.drag;this.drag=null;this.layer.classList.remove('is-dragging');this.homeNode.hidden=true;
   const at={x:point.x-held.offset.x,y:point.y-held.offset.y};
   const valid=root.CatfoodChaseGame.inside(at,this.home);
   root.dispatchEvent(new CustomEvent('catfood:chasedrag',{detail:{active:false,returned:valid}}));
   if(valid){this.returning=true;this.returningAt=at;root.dispatchEvent(new CustomEvent('catfood:catreturned',{detail:{event_id:this.runId+'-return'}}));}
   else{this.blend={at,started:this.previousSeconds};this.manualFeedback={text:'还没到原站位喵～',until:this.previousSeconds+1.4,x:at.x,y:at.y};}
   return valid;
  }
  cancelDrag(){this.drag=null;this.pointer=null;this.homeNode.hidden=true;this.layer.classList.remove('is-dragging');root.dispatchEvent(new CustomEvent('catfood:chasedrag',{detail:{active:false}}));}
  draw(seconds){
   seconds=Math.max(0,Number.isFinite(seconds)?seconds:0);
   const width=root.innerWidth,height=root.innerHeight,delta=seconds-this.previousSeconds;
   let p=this.running?this.simulation.advance(this.drag||this.returning||this.blend||this.inspectionPaused?0:delta,width,height):root.CatfoodChaseMotion.frame(seconds,width,height);
   this.previousSeconds=seconds;
   const total=Math.max(this.knownCatches,this.catchBase+(p.biteCount||0)),level=root.CatfoodChaseGame.anger(total);
   // Complete the tenth bite, then turn around at the current physical positions.
   if(this.running&&!this.drag&&!this.returning&&!this.simulation.reversed&&total>=10&&p.phase!=='bite'){
    this.simulation.reverse();p=this.simulation.snapshot(width,height);
    this.manualFeedback={text:'攻防转换！轮到本鱼啦喵～',until:seconds+2.6,x:p.maid.x,y:p.maid.y};
    root.dispatchEvent(new CustomEvent('catfood:chasereversed',{detail:{event_id:this.runId+'-reverse'}}));this.event('reverse');
   }
   const phase=p.phase||'chase',age=p.phaseAge||0,reversed=!!p.reversed,tag=phase==='counter-tag';this.reversed=reversed;
   const hit=root.CatfoodChaseMotion.impact(phase,age,reversed);
   this.layer.classList.toggle('is-reversed',reversed);this.layer.dataset.duel=reversed?'reverse':'normal';this.layer.dataset.roundCatches=total;
   if(this.labelReversed!==reversed){this.labelReversed=reversed;this.cat.setAttribute('aria-label',reversed?'拖动 Q版 YUI 回到原站位，恢复精神':'拖动小白猫回到原站位，恢复 YUI 精神');}
   if(this.drag){p.cat.x=this.drag.point.x-this.drag.offset.x;p.cat.y=this.drag.point.y-this.drag.offset.y-p.size*.05;}
   if(this.returning&&this.returningAt){p.cat.x=this.returningAt.x;p.cat.y=this.returningAt.y-p.size*.05;}
   if(this.blend){const t=Math.min(1,(seconds-this.blend.started)/.65),ease=(1-t)**2;p.cat.x+=(this.blend.at.x-p.cat.x)*ease;p.cat.y+=(this.blend.at.y-p.size*.05-p.cat.y)*ease;if(t>=1)this.blend=null;}
   this.layer.dataset.phase=this.drag?'held':phase;this.layer.dataset.phaseAge=age.toFixed(3);this.layer.dataset.bites=p.biteCount||0;
   this.layer.classList.toggle('is-biting',phase==='bite'&&!this.drag);this.layer.classList.toggle('is-reacting',phase==='bite'&&age>=.28&&!this.drag);
   for(const [node,position,kind]of [[this.cat,p.cat,'cat'],[this.maid,p.maid,'maid']]){
    const scale=kind==='maid'||reversed?1.3:1;node.style.width=p.size*scale+'px';node.style.height=p.size*scale+'px';
    const rect={x:position.x-p.size*scale/2,y:position.y+p.size*.55-p.size*scale,width:p.size*scale,height:p.size*scale};
    node.style.transform=`translate3d(${rect.x}px,${rect.y}px,0)`;if(kind==='cat')this.catRect=rect;
    const target=!!p.lastContact&&p.sinceBite<1.18&&(reversed?kind==='cat':kind==='maid'),squash=target?hit.squash:0;
    const facing=kind==='maid'?(reversed?root.CatfoodWhaleClips.hat.facing||-1:-1):!reversed?-1:1;
    node.firstElementChild.style.transform=`scale(${position.facing*facing*(1+squash)},${1-squash}) rotate(${target?hit.tilt:0}deg)`;
    node.firstElementChild.style.filter=target&&hit.flash>0?`brightness(${1+hit.flash*.45})`:'';node.dataset.facing=position.facing>0?'right':'left';
   }
   const yc=this.yuiClip,pose=Math.floor(seconds*yc.fps*(phase==='escape'?1.15:1))%yc.frames;
   this.cat.dataset.pose=pose;this.yuiSheet.style.transform=`translate3d(${-(pose%yc.columns)*100/yc.columns}%,${-Math.floor(pose/yc.columns)*100/yc.rows}%,0)`;
   const hurt=root.CatfoodChaseMotion.yuiHitPose(p,!!this.drag||this.returning);
   this.yuiSheet.hidden=hurt>=0;this.yuiHitSheet.hidden=hurt<0;this.cat.dataset.action=hurt>=0?'hit':'run';this.cat.dataset.hitFrame=hurt;
   this.cat.style.zIndex=hurt>=0?'6':'2';this.maid.style.zIndex='1';
   if(hurt>=0){const c=this.yuiHitClip;this.yuiHitSheet.style.transform=`translate3d(${-(hurt%c.columns)*100/c.columns}%,${-Math.floor(hurt/c.columns)*100/c.rows}%,0)`;}
   this.drawWhale(seconds,p.biteCount||0,level,reversed);
   const target=reversed?p.cat:p.maid;
   this.halo.hidden=hit.daze<=0||!!this.drag||this.returning;
   if(!this.halo.hidden){this.halo.style.left=target.x+'px';this.halo.style.top=(target.y-p.size*.7)+'px';this.halo.style.opacity=hit.daze;[...this.halo.children].forEach((star,i)=>{const a=seconds*8+i*Math.PI*2/3;star.style.transform=`translate3d(${Math.cos(a)*p.size*.3}px,${Math.sin(a)*p.size*.065}px,0) scale(${.85+.15*Math.sin(a)})`;});}
   this.ring.hidden=!(phase==='bite'||tag)||age>.35||!!this.drag;
   if(!this.ring.hidden){const radius=p.size*(.14+age*1.6);this.ring.style.cssText=`left:${p.lastContact.x}px;top:${p.lastContact.y}px;width:${radius}px;height:${radius}px;opacity:${Math.max(0,1-age/.35)}`;}
   if(this.home){for(const k of ['x','y','width','height'])this.homeNode.style[{x:'left',y:'top',width:'width',height:'height'}[k]]=this.home[k]+'px';this.homeNode.classList.toggle('is-ready',!!this.drag&&root.CatfoodChaseGame.inside({x:this.drag.point.x-this.drag.offset.x,y:this.drag.point.y-this.drag.offset.y},this.home));}
   const feedbackVisible=!this.drag&&!this.returning&&!!p.lastContact&&p.sinceBite<2.6,manual=!this.drag&&this.manualFeedback&&seconds<this.manualFeedback.until;
   this.feedback.hidden=!(feedbackVisible||manual);this.mark.hidden=phase!=='bite'||age>.28||!!this.drag;this.shock.hidden=!(phase==='bite'||tag)||age<.12||age>.78||!!this.drag;this.burst.hidden=!(phase==='bite'||tag)||age>.95||!!this.drag;
   this.turnFlash.hidden=phase!=='turn'||!!this.drag;
   if(!this.turnFlash.hidden){this.turnFlash.style.left=p.maid.x+'px';this.turnFlash.style.top=Math.max(12,p.maid.y-p.size*.7)+'px';this.turnFlash.style.transform=`translate(-50%,-100%) scale(${1+Math.sin(age*Math.PI/.9)*.4})`;}
   if(feedbackVisible||manual){
    const yuiCaught=feedbackVisible&&reversed&&p.lastContact.reversed,useManual=manual&&!yuiCaught;
    const line=yuiCaught?'呜喵！轻、轻一点喵～':useManual?this.manualFeedback.text:['啊呜，抓到啦喵～ ♡','怎、怎么又咬本鱼喵～','再咬就生气了喵！','本鱼忍不住了喵！','本鱼要反击了喵！'][level];if(this.feedback.textContent!==line)this.feedback.textContent=line;
    const at=yuiCaught?p.cat:useManual?this.manualFeedback:p.lastContact,bw=Math.min(250,width-16),safeTop=this.safeTop;
    this.feedback.style.maxWidth=bw+'px';this.feedback.style.left=Math.max(8,Math.min(width-bw-8,at.x-bw/2))+'px';this.feedback.style.top=Math.max(safeTop,Math.min(height-70,at.y-p.size*.85-55))+'px';
    this.mark.style.left=p.contact.x+'px';this.mark.style.top=p.contact.y+'px';this.mark.style.width=Math.max(20,p.size*.23)+'px';this.mark.style.height=Math.max(16,p.size*.18)+'px';this.mark.style.setProperty('--bite-close',Math.sin(Math.min(1,age/.3)*Math.PI));
    const target=reversed?p.cat:p.maid;this.shock.style.left=Math.min(width-36,target.x+p.size*.4)+'px';this.shock.style.top=Math.max(12,target.y-p.size*.55)+'px';this.shock.style.fontSize=Math.max(22,p.size*.3)+'px';
    this.burst.style.left=p.contact.x+'px';this.burst.style.top=p.contact.y+'px';
    [...this.burst.children].forEach((n,i)=>{n.textContent=reversed?'✦':i%3?'♡':'✦';const a=i*Math.PI*2/9,r=18+Math.min(1,age/.7)*p.size*.55;n.style.transform=`translate(${Math.cos(a)*r}px,${Math.sin(a)*r}px) rotate(${Math.sin(a)*20}deg) scale(${1+Math.sin(Math.min(1,age/.8)*Math.PI)*.6})`;n.style.opacity=Math.max(0,1-age/.95);});
   }
   if(reversed&&p.counterCount>this.reportedTags){this.reportedTags=p.counterCount;this.event('counter_tag','tag-'+p.counterCount);}
   if(reversed&&phase==='chase'&&!this.drag&&seconds-this.lastFlee>=25){this.lastFlee=seconds;this.event('flee','flee-'+Math.floor(seconds));}
   if(p.biteCount>this.reportedBites){this.reportedBites=p.biteCount;root.dispatchEvent(new CustomEvent('catfood:chasebite',{detail:{event_id:this.runId+'-'+p.biteCount,bite_id:p.biteCount,contact:p.lastContact,animation:'counter-chase-v4'}}));}
   // Native owns only the mouse hit target. Keep all visible motion in one compositor.
   if(seconds-this.lastGeometry>=1/60-.001){this.lastGeometry=seconds;root.dispatchEvent(new CustomEvent('catfood:chasegeometry',{detail:{...this.catRect,renderInWeb:true,reacting:this.layer.classList.contains('is-reacting'),facing:p.cat.facing,form:reversed?'yui':'cat'}}));}
   this.layer.dataset.seconds=seconds.toFixed(2);
  }
  start(){if(this.running)return;this.running=true;this.returning=false;this.reversed=false;this.drag=null;this.blend=null;this.manualFeedback=null;this.layer.hidden=false;this.started=performance.now();this.previousSeconds=0;this.reportedBites=0;this.catchBase=this.knownCatches;this.runId=uid();this.reportedTags=0;this.lastFlee=0;this.lastGeometry=-1;this.simulation.reset();this.whalePlayer.reset();this.event('start');const animate=now=>{if(!this.running)return;this.draw((now-this.started)/1000);this.frameId=requestAnimationFrame(animate);};this.frameId=requestAnimationFrame(animate);}
  stop(){this.running=false;cancelAnimationFrame(this.frameId);this.cancelDrag();this.layer.hidden=true;this.feedback.hidden=true;this.mark.hidden=true;this.shock.hidden=true;this.burst.hidden=true;this.turnFlash.hidden=true;this.halo.hidden=true;this.ring.hidden=true;this.yuiHitSheet.hidden=true;this.yuiSheet.hidden=false;this.cat.dataset.action='run';this.cat.dataset.hitFrame='-1';this.layer.classList.remove('is-biting','is-reacting','is-reversed');this.layer.dataset.phase='stopped';this.simulation.reset();}
  destroy(){this.stop();this.feedback.remove();this.layer.remove();}
 }
 root.CatfoodChaseStage=ChaseStage;
})(window);
