/* Deterministic chase: both characters follow the same bounded path, with a delay. */
(function(root){
 'use strict';
 function reaction(quota,{active=true,paused=false,reduced=false}={}){
  if(!active||paused||quota?.stale||!Number.isFinite(quota?.percent))return 'still';
  if(quota.percent<10)return reduced?'cat-static':'chase';
  return quota.percent<50&&!reduced?'shake':'still';
 }
 function frame(seconds,width,height){
  const size=Math.max(56,Math.min(150,width*.28,height*.28));
  const padX=size*.65+12,padY=size*.8+14;
  const ax=Math.max(0,width/2-padX),ay=Math.max(0,height/2-padY);
  const point=t=>{
   const a=t*Math.PI*2/19;
   return {x:width/2+ax*Math.sin(a),y:height/2+ay*Math.sin(2*a+.28),facing:Math.cos(a)>=0?1:-1};
  };
  return {cat:point(seconds-1.1),maid:point(seconds),size};
 }
 // Check the cat's mouth against the maid's torso, excluding transparent hair/tail padding.
 function contact(cat,maid,size){
  const mouth={x:cat.x+cat.facing*size*.39,y:cat.y+size*.14};
  const body={x:maid.x+maid.facing*size*.1,y:maid.y+size*.05};
  const score=((mouth.x-body.x)/(size*.37))**2+((mouth.y-body.y)/(size*.43))**2;
  const ahead=(maid.x-cat.x)*cat.facing>=-size*.12;
  return {hit:ahead&&score<=1,score,x:mouth.x,y:mouth.y};
 }
 // A short readable hit, followed by a brief daze while escaping. No screen shake.
 function impact(phase,age,reversed=false){
  if(!['bite','counter-tag','escape'].includes(phase))return {amount:0,squash:0,tilt:0,flash:0,daze:0};
  const t=phase==='escape'?age+(reversed?1.05:.58):age;
  const amount=t<.58?Math.sin(Math.min(1,t/.58)*Math.PI):0;
  const squash=t<.18?Math.sin(t/.18*Math.PI)*.16:0;
  return {amount,squash,tilt:Math.sin(t*32)*Math.exp(-t*5)*12,flash:Math.max(0,1-t/.16),daze:Math.max(0,Math.min(1,(t-.08)/.1, (1.18-t)/.28))};
 }
 // Give the actual target a readable expression, then return to the run cycle.
 function yuiHitPose(frame,held=false){
  const age=frame.sinceBite;
  if(held||!frame.reversed||!frame.lastContact?.reversed||!Number.isFinite(age)||age<0||age>=1.15)return -1;
  return age<.12?0:age<.4?1:age<.86?2:3;
 }
 class Pursuit{
  constructor(){this.reset();}
  reset(){this.maidTrack=0;this.catTrack=-1.4;this.phase='chase';this.age=0;this.bites=0;this.counterTags=0;this.reversed=false;this.elapsed=0;this.lastContact=null;this.width=0;this.height=0;}
  reverse(){if(this.reversed)return false;this.reversed=true;this.phase='turn';this.age=0;this.lastContact=null;return true;}
  advance(delta,width,height){
   if(this.width&&(this.width!==width||this.height!==height)){
    this.phase='chase';this.age=0;if(this.reversed)this.maidTrack=this.catTrack+1.4;else this.catTrack=this.maidTrack-1.4;this.lastContact=null;
   }
   this.width=width;this.height=height;
   // Fixed small steps prevent tunnelling through a target during a slow render frame.
   const duration=Math.max(0,Math.min(.25,Number.isFinite(delta)?delta:0));
   const steps=Math.max(1,Math.ceil(duration*120)),dt=duration/steps;
   for(let i=0;i<steps;i++){
    this.age+=dt;this.elapsed+=dt;
    if(this.phase==='turn'){
     if(this.age>=.9){this.phase='chase';this.age=0;}
    }else if(this.phase==='bite'||this.phase==='counter-tag'){
     // A 60 ms anticipation, then continuous movement under the recoil.
     if(this.age>.06){
      const drift=dt*.22;if(this.reversed){this.catTrack-=drift;this.maidTrack-=drift;}else{this.maidTrack+=drift;this.catTrack+=drift;}
     }
     if(this.age>=(this.reversed?1.05:.58)){this.phase='escape';this.age=0;}
    }else if(this.phase==='escape'){
     if(this.reversed){this.catTrack-=dt*1.7;if(this.age>.28)this.maidTrack-=dt*.5;}
     else{this.maidTrack+=dt*1.65;if(this.age>.28)this.catTrack+=dt*.5;}
     if(this.age>=1.1){this.phase='chase';this.age=0;}
    }else{
     if(this.reversed){this.catTrack-=dt*1.08;this.maidTrack=Math.max(this.catTrack,this.maidTrack-dt*1.34);}
     else{this.maidTrack+=dt;this.catTrack=Math.min(this.maidTrack,this.catTrack+dt*1.22);}
     const p=this.base(width,height),hit=this.reversed?contact(p.maid,p.cat,p.size*1.05):contact(p.cat,p.maid,p.size);
     if(this.age>.45&&hit.hit){
      this.phase=this.reversed?'counter-tag':'bite';this.age=0;if(this.reversed)this.counterTags++;else this.bites++;
      this.lastContact={...hit,at:this.elapsed,bite_id:this.bites,counter_id:this.counterTags,reversed:this.reversed};
     }
    }
   }
   return this.snapshot(width,height);
  }
  base(width,height){
   const p=frame(this.maidTrack,width,height);
   const cat=frame(this.catTrack,width,height).maid;
   if(this.reversed){p.maid.facing*=-1;cat.facing*=-1;}
   return {...p,cat};
  }
  snapshot(width,height){
   const p=this.base(width,height),a=this.age,s=p.size;
   const mouth=this.reversed?contact(p.maid,p.cat,s*1.05):contact(p.cat,p.maid,s);
   if(this.phase==='bite'||this.phase==='counter-tag'){
    const chaser=this.reversed?p.maid:p.cat,target=this.reversed?p.cat:p.maid;
    const pounce=Math.sin(Math.min(1,a/.22)*Math.PI);
    chaser.x+=chaser.facing*pounce*s*.07;chaser.y-=pounce*s*.08;
    const recoil=impact(this.phase,a).amount;
    target.y-=recoil*s*.19;
    target.x+=(target.facing*.11+Math.sin(a*34)*.012)*recoil*s;
    if(this.phase==='counter-tag'){
     const space=Math.sin(Math.min(1,a/1.05)*Math.PI);
     chaser.x-=chaser.facing*space*s*.34;
     target.x+=target.facing*space*s*.18;
    }
   }
   const padX=s*.65+12,padY=s*.8+14;
   for(const n of [p.cat,p.maid]){n.x=Math.max(padX,Math.min(width-padX,n.x));n.y=Math.max(padY,Math.min(height-padY,n.y));}
   return {...p,phase:this.phase,phaseAge:a,biteCount:this.bites,counterCount:this.counterTags,reversed:this.reversed,contact:mouth,lastContact:this.lastContact,sinceBite:this.lastContact?this.elapsed-this.lastContact.at:Infinity};
  }
 }
 const api={reaction,frame,contact,impact,yuiHitPose,Pursuit};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.CatfoodChaseMotion=api;
})(typeof window==='object'?window:globalThis);
