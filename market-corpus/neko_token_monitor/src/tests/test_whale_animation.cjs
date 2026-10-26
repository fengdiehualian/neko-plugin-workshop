const assert=require('node:assert/strict');
const {Player,clips}=require('../static/whale-animation.js');
const player=new Player();
assert.equal(player.frame(0).key,'run');
assert.notEqual(player.frame(.05).index,player.frame(.3).index);
for(let bites=1;bites<=8;bites++){
 const t=bites*4,level=Math.floor(bites/2);
 assert.equal(player.frame(t,{bites,level}).key,'shock');
 assert.equal(player.frame(t+.5,{bites,level}).key,'shock');
 assert.equal(player.frame(t+1.2,{bites,level}).key,bites%2?'run':'angry');
 assert.equal(player.frame(t+2.1,{bites,level}).key,'run');
}
assert.equal(player.frame(40,{bites:10,level:4,reversed:true}).key,'hat');
assert.equal(player.frame(50,{bites:10,level:4,reversed:true}).key,'hat');
assert.equal(clips.hat.preserved,true);assert.equal(clips.hat.src,'/chase/whale-maid-tiger-run.png');assert.equal(clips.yui.frames,12);
for(const [key,clip]of Object.entries(clips)){
 assert.ok(clip.frames>=(key==='yuiHit'?4:['hat','yui'].includes(key)?6:20));assert.ok(clip.columns*clip.rows>=clip.frames);
 assert.ok(clip.columns*clip.cell<=4096&&clip.rows*clip.cell<=4096);
 assert.equal(clip.faces.length,key.startsWith('yui')||key==='hat'?0:clip.frames);
 for(const face of clip.faces)assert.ok(face.every(n=>n>0&&n<1));
}
player.reset();assert.equal(player.frame(0).key,'run');
console.log('PASS: original clips, real run frames, one-shot shock, tier anger, preserved hooded whale reversal, reset and texture limits.');
