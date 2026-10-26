import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCommand,commandPolicy} from '../ui/intent.mjs';

test('questions, negations, quotations and future conditions retain a model-independent non-action boundary',()=>{
  for(const text of ['安全带系好了吗？','安全带系好了没','安全带系好了没有','不要系安全带','不要，打火','他说“打火”','如果准备好了就出发','不 要 系 安 全 带'])
    assert.equal(commandPolicy(text).conversationOnly,true,text);
});
test('positive corrections and an immediate stop retain their explicit action',()=>{
  for(const text of ['别向右，向左','请把安全带系上','先刹车，等我说完再前进'])
    assert.equal(commandPolicy(text).conversationOnly,false,text);
});
test('immediate multi-step manoeuvres remain eligible for model planning',()=>{
  const text='5公里每小时前进半秒然后刹车';
  assert.equal(parseCommand(text).commands.length,0);
  assert.equal(commandPolicy(text).conversationOnly,false);
  for(const request of ['不要太快，5公里每小时前进半秒然后刹车','别紧张，向左一点然后回正'])
    assert.equal(commandPolicy(request).conversationOnly,false,request);
});
