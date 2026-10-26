const assert=require('node:assert/strict');
const {Pursuit}=require('../static/chase-motion.js');
for(const [width,height]of [[320,240],[400,560],[1440,900],[2560,1410]]){
 const game=new Pursuit();let p;
 for(let i=0;i<120*60&&game.bites<10;i++)p=game.advance(1/60,width,height);
 assert.equal(game.bites,10);while(game.phase==='bite')p=game.advance(1/60,width,height);
 const before=game.base(width,height);assert.equal(game.reverse(),true);assert.equal(game.reverse(),false);
 const turned=game.base(width,height);
 for(const name of ['cat','maid']){
  assert.equal(turned[name].x,before[name].x);assert.equal(turned[name].y,before[name].y);
  assert.equal(turned[name].facing,-before[name].facing,'both reverse without teleporting');
 }
 const phases=new Set();let tags=0;
 for(let i=0;i<90*60;i++){
  p=game.advance(1/60,width,height);phases.add(p.phase);
  assert.equal(p.reversed,true);assert.equal(p.biteCount,10,'revenge does not count as fish being caught');
  for(const name of ['cat','maid']){
   assert.ok(p[name].x>=p.size*.65+11&&p[name].x<=width-p.size*.65-11);
   assert.ok(p[name].y>=p.size*.8+13&&p[name].y<=height-p.size*.8-13);
  }
  if(p.counterCount>tags){assert.equal(p.counterCount,tags+1);assert.equal(p.phase,'counter-tag');assert.ok(p.lastContact.hit);assert.ok(p.lastContact.score<=1);tags=p.counterCount;}
 }
 assert.ok(tags>=4);for(const phase of ['turn','chase','counter-tag','escape'])assert.ok(phases.has(phase));
 game.advance(0,800,600);assert.equal(game.reversed,true);assert.equal(game.phase,'chase');
 game.reset();assert.equal(game.reversed,false);assert.equal(game.bites,0);assert.equal(game.counterTags,0);
}
console.log('PASS: tenth-catch role swap without teleporting, opposite facing, reverse pursuit and real contact, 90-second bounds on four screens, distinct counters, resize and reset.');
