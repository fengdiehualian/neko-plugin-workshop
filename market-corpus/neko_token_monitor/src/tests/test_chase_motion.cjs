const assert=require('node:assert/strict');
const {reaction,frame}=require('../static/chase-motion.js');
for(const [percent,expected] of [[100,'still'],[75,'still'],[50.001,'still'],[50,'still'],[49.999,'shake'],[25,'shake'],[24.999,'shake'],[10,'shake'],[9.999,'chase'],[0,'chase']])assert.equal(reaction({percent}),expected);
assert.equal(reaction(null),'still');assert.equal(reaction({percent:1,stale:true}),'still');
assert.equal(reaction({percent:1},{active:false}),'still');assert.equal(reaction({percent:1},{paused:true}),'still');
assert.equal(reaction({percent:24},{reduced:true}),'still');assert.equal(reaction({percent:1},{reduced:true}),'cat-static');
for(const [w,h]of [[400,404],[1440,900],[2560,1440],[320,240]]){
 let minX=w,maxX=0,minY=h,maxY=0;
 for(let i=0;i<11400;i++){
  const p=frame(i/60,w,h);
  for(const key of ['cat','maid']){const v=p[key];assert.ok(Number.isFinite(v.x)&&Number.isFinite(v.y));assert.ok(v.x>p.size*.5&&v.x<w-p.size*.5);assert.ok(v.y>p.size*.67&&v.y<h-p.size*.67);minX=Math.min(minX,v.x);maxX=Math.max(maxX,v.x);minY=Math.min(minY,v.y);maxY=Math.max(maxY,v.y);}
  const delayed=frame(i/60-1.1,w,h);assert.ok(Math.abs(p.cat.x-delayed.maid.x)<1e-8&&Math.abs(p.cat.y-delayed.maid.y)<1e-8);
 }
 assert.ok(maxX-minX>w*.3&&maxY-minY>h*.15);
}
console.log('PASS: strict thresholds, restoration, stale/missing quota, pause, reduced motion, 190-second bounded trajectories and exact pursuit delay on 4 screen sizes.');
