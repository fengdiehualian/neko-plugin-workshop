/* Persistent play state; quota/account values are never changed by the game. */
(function(root){
 'use strict';
 const count=n=>Number.isSafeInteger(n)&&n>=0?n:0;
 const interval=n=>Number.isFinite(Number(n))&&Number(n)>=5&&Number(n)<=3600?Math.round(Number(n)):60;
 const anger=n=>n>=8?4:n>=6?3:n>=4?2:n>=2?1:0;
 const inside=(point,rect,margin=12)=>!!rect&&point.x>=rect.x-margin&&point.x<=rect.x+rect.width+margin&&point.y>=rect.y-margin&&point.y<=rect.y+rect.height+margin;
 class Game{
  constructor(saved={},clock=Date.now){this.clock=clock;this.intervalSeconds=interval(saved.intervalSeconds);this.catches=count(saved.catches);this.roundCatches=count(saved.roundCatches);this.rescues=count(saved.rescues);this.calmUntil=Number.isFinite(saved.calmUntil)?Math.min(saved.calmUntil,clock()+3600000):0;this.seen=Array.isArray(saved.seen)?saved.seen.filter(x=>typeof x==='string').slice(-256):[];}
  calm(){return this.calmUntil>this.clock();}
  caught(id){if(typeof id!=='string'||!id||id.length>100||this.seen.includes(id))return false;this.seen.push(id);this.seen=this.seen.slice(-256);this.catches++;this.roundCatches++;return true;}
  recover(){this.rescues++;this.roundCatches=0;this.calmUntil=this.clock()+this.intervalSeconds*1000;return this.calmUntil;}
  configure(seconds){const n=Number(seconds);if(!Number.isInteger(n)||n<5||n>3600)throw Error('请填写 5～3600 秒的整数喵～');this.intervalSeconds=n;if(this.calm())this.calmUntil=this.clock()+n*1000;}
  merge(stats={}){this.catches=Math.max(this.catches,count(stats.catches));this.rescues=Math.max(this.rescues,count(stats.rescues));}
  export(){return {intervalSeconds:this.intervalSeconds,catches:this.catches,roundCatches:this.roundCatches,rescues:this.rescues,calmUntil:this.calmUntil,seen:this.seen.slice()};}
 }
 const api={Game,anger,inside,interval};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.CatfoodChaseGame=api;
})(typeof window==='object'?window:globalThis);
