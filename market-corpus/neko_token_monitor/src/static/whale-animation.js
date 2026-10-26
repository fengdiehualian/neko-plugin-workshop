/* Playback only: chase positions, collision, YUI and counters remain in their existing owners. */
(function(root){
 'use strict';
 const clips=typeof module==='object'&&module.exports?require('./whale-clips.js'):root.CatfoodWhaleClips;
 class Player{
  constructor(){this.reset();}
  reset(){this.bites=0;this.level=0;this.hitAt=-Infinity;this.angry=false;}
  frame(seconds,{bites=0,level=0,reversed=false}={}){
   if(bites>this.bites){this.hitAt=seconds;this.angry=level>this.level;this.bites=bites;}
   this.level=level;
   const age=Math.max(0,seconds-this.hitAt);
   let key='run',time=seconds,loop=true;
   if(reversed)key='hat';
   else if(age<1.15){key='shock';time=age/1.15*(clips.shock.frames/clips.shock.fps);loop=false;}
   else if(this.angry&&age<2.05){key='angry';time=(age-1.15)/.9*(clips.angry.frames/clips.angry.fps);loop=false;}
   const clip=clips[key],raw=Math.max(0,Math.floor(time*clip.fps));
   const index=loop?raw%clip.frames:Math.min(clip.frames-1,raw);
   return {key,clip,index,face:clip.faces[index]||null};
  }
 }
 const api={Player,clips};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.CatfoodWhaleAnimation=api;
})(typeof window==='object'?window:globalThis);
