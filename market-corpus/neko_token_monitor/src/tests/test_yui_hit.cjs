const assert=require('node:assert/strict');
const {yuiHitPose,Pursuit}=require('../static/chase-motion.js');
const hit=t=>({reversed:true,lastContact:{reversed:true},sinceBite:t});
for(const [time,pose]of [[-.01,-1],[0,0],[.119,0],[.12,1],[.399,1],[.4,2],[.859,2],[.86,3],[1.149,3],[1.15,-1]])assert.equal(yuiHitPose(hit(time)),pose);
assert.equal(yuiHitPose(hit(.2),true),-1);
assert.equal(yuiHitPose({...hit(.2),reversed:false}),-1);
assert.equal(yuiHitPose({...hit(.2),lastContact:{reversed:false}}),-1);
assert.equal(yuiHitPose({...hit(.2),lastContact:null}),-1);
assert.equal(yuiHitPose(hit(Infinity)),-1);
for(const [width,height]of [[400,560],[1440,900]]){
 const game=new Pursuit();game.reverse();let p;
 for(let i=0;i<15000&&game.counterTags===0;i++)p=game.advance(1/60,width,height);
 assert.equal(game.counterTags,1);let tags=game.counterTags;const poses=new Set();
 for(let i=0;i<120;i++){
  p=game.advance(1/60,width,height);const pose=yuiHitPose(p);if(pose>=0)poses.add(pose);
  assert.equal(game.counterTags,tags,'reaction does not add duplicate contacts');
 }
 assert.deepEqual([...poses],[0,1,2,3]);assert.equal(yuiHitPose(p),-1);
 game.reset();assert.equal(game.counterTags,0);assert.equal(game.lastContact,null);
}
console.log('PASS: four expression timings, 1.15-second recovery, held/normal/invalid guards, natural reverse contact, no duplicate records and reset.');
