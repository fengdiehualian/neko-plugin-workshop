const assert=require('node:assert/strict');
const {contact,Pursuit}=require('../static/chase-motion.js');
const cat={x:0,y:0,facing:1};
assert.ok(contact(cat,{x:45,y:0,facing:1},100).hit);
assert.ok(!contact(cat,{x:200,y:0,facing:1},100).hit);
assert.ok(!contact(cat,{x:45,y:100,facing:1},100).hit);
assert.ok(!contact({...cat,facing:-1},{x:45,y:0,facing:1},100).hit);
assert.ok(contact(cat,{x:55,y:0,facing:-1},100).hit);
function simulate(w,h,rate=60,duration=180){
 const sim=new Pursuit(),hits=[],phases=new Set();let previousCount=0;
 for(let i=0;i<duration*rate;i++){
  const p=sim.advance(1/rate,w,h);phases.add(p.phase);
  for(const n of [p.cat,p.maid]){assert.ok(Number.isFinite(n.x)&&Number.isFinite(n.y));assert.ok(n.x>p.size*.5&&n.x<w-p.size*.5);assert.ok(n.y>p.size*.67&&n.y<h-p.size*.67);}
  if(p.biteCount>previousCount){
   assert.equal(p.biteCount,previousCount+1);assert.equal(p.phase,'bite');assert.ok(p.lastContact.score<=1);assert.ok(p.lastContact.hit);
   hits.push(p.lastContact);previousCount=p.biteCount;
  }
 }
 assert.deepEqual([...phases].sort(),['bite','chase','escape']);assert.ok(hits.length>=10);
 for(let i=1;i<hits.length;i++)assert.ok(hits[i].at-hits[i-1].at>2.5,'one event per contact and an escape cooldown');
 return hits;
}
for(const [w,h]of [[320,240],[400,404],[1440,900],[2560,1410]])simulate(w,h);
const sixty=simulate(1440,900,60),thirty=simulate(1440,900,30),ten=simulate(1440,900,10);
assert.equal(sixty.length,thirty.length);assert.equal(sixty.length,ten.length);
for(let i=0;i<sixty.length;i++){assert.ok(Math.abs(sixty[i].at-thirty[i].at)<.04);assert.ok(Math.abs(sixty[i].at-ten[i].at)<.04);}
const stalled=new Pursuit();stalled.advance(10,1440,900);assert.ok(stalled.elapsed<=.251);assert.equal(stalled.bites,0);
const resized=new Pursuit();while(resized.phase!=='bite')resized.advance(1/60,1440,900);
const count=resized.bites;resized.advance(0,320,240);assert.equal(resized.phase,'chase');assert.equal(resized.bites,count);assert.equal(resized.lastContact,null);
resized.reset();assert.equal(resized.bites,0);assert.equal(resized.phase,'chase');
console.log('PASS: geometric contact and misses, 180-second catch/bite/escape cycles on 4 viewports, single-event cooldown, 10/30/60fps consistency, stalled-frame clamp and resize/reset cleanup.');
