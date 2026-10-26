import test from 'node:test'
import assert from 'node:assert/strict'
import { startPanelRefresh } from '../ui/panel-refresh.js'

function fixture (refresh) {
  const listeners=new Map(),timers=new Map();let sequence=0,observer
  const document={hidden:false,documentElement:{},addEventListener:(k,v)=>listeners.set(k,v),removeEventListener:k=>listeners.delete(k)}
  class Observer {constructor(fn){observer=this;this.fn=fn}observe(){}disconnect(){this.closed=true}}
  const stop=startPanelRefresh({refresh,document,Observer,setTimeout:fn=>{timers.set(++sequence,fn);return sequence},clearTimeout:id=>timers.delete(id)})
  return {document,listeners,timers,stop,get observer(){return observer},async tick(){const [id,fn]=timers.entries().next().value;timers.delete(id);await fn()}}
}

test('visible panel refreshes state periodically and stops on unmount',async()=>{
  let calls=0;const f=fixture(async()=>{calls++})
  assert.equal(calls,0);await f.tick();assert.equal(calls,1);assert.equal(f.timers.size,1)
  f.stop();assert.equal(f.timers.size,0);assert.equal(f.listeners.size,0);assert.equal(f.observer.closed,true)
})

test('hidden tabs and hidden parent panels do not poll; returning resumes',async()=>{
  let calls=0;const f=fixture(async()=>{calls++})
  f.document.hidden=true;f.listeners.get('visibilitychange')();assert.equal(f.timers.size,0)
  f.document.hidden=false;f.listeners.get('visibilitychange')();await f.tick();assert.equal(calls,1)
  f.observer.fn([{isIntersecting:false}]);assert.equal(f.timers.size,0)
  f.observer.fn([{isIntersecting:true}]);await f.tick();assert.equal(calls,2);f.stop()
})

test('slow and failed reads never overlap or schedule after disposal',async()=>{
  let resolve,calls=0;const f=fixture(()=>{calls++;return new Promise(r=>{resolve=r})})
  const pending=f.tick();assert.equal(calls,1);assert.equal(f.timers.size,0)
  f.listeners.get('visibilitychange')();assert.equal(f.timers.size,0)
  f.stop();resolve();await pending;assert.equal(f.timers.size,0)
  const failed=fixture(async()=>{throw Error('offline')});await failed.tick();assert.equal(failed.timers.size,1);failed.stop()
})
