// Pure beat clock shared by browser and numerical motion tests.
(function(root){
 class RhythmMotion{
  constructor(){this.state=null;this.received=0;this.phase=0;this.gain=0;this.last=null;this.synced=false;}
  accept(state,now){this.state=state;this.received=now;}
  frame(now,visible=true){
   const dt=this.last===null?0:Math.max(0,Math.min(.05,(now-this.last)/1000));this.last=now;
   const s=this.state;
   const active=visible&&s?.enabled&&s.active&&Number.isFinite(s.bpm)&&s.bpm>=40&&s.bpm<=200&&Number.isFinite(s.phase)&&now-this.received<2000;
   if(active){
    const target=(s.phase+(now-this.received)*s.bpm/60000)%1;
    if(!this.synced){this.phase=target;this.synced=true;}
    else{this.phase=(this.phase+dt*s.bpm/60)%1;const error=((target-this.phase+1.5)%1)-.5;this.phase=(this.phase+error*(1-Math.exp(-dt*8))+1)%1;}
   }else{this.synced=false;}
   const desired=active?Math.max(0,Math.min(1,s.strength||0)):0;
   this.gain+=(desired-this.gain)*(1-Math.exp(-dt*10));
   // The pose and its velocity return smoothly to neutral on each musical beat.
   const wave=(1-Math.cos(2*Math.PI*this.phase))/2;
   const lift=wave*15*this.gain;
   const squash=(Math.cos(2*Math.PI*this.phase)*.012+wave*.012)*this.gain;
   return {active:!!active,phase:this.phase,lift,scaleX:1+squash,scaleY:1-squash,rotate:Math.sin(2*Math.PI*this.phase)*1.1*this.gain};
  }
 }
 root.CatfoodRhythmMotion=RhythmMotion;
 if(typeof module!=='undefined')module.exports=RhythmMotion;
})(typeof window!=='undefined'?window:globalThis);
