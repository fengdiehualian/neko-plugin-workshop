// Exercise production pointer handlers with a small deterministic DOM adapter.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(){this.children=[];this.dataset={};this.attrs={};this.handlers={};this.style={setProperty(){}};this.classes=new Set();this.classList={add:v=>this.classes.add(v),remove:v=>this.classes.delete(v),contains:v=>this.classes.has(v),toggle:(v,on)=>on?this.classes.add(v):this.classes.delete(v)};this.captured=new Set();}
 set className(value){this.classes=new Set(value.split(' '));}get className(){return [...this.classes].join(' ');}
 setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}
 append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
 replaceChildren(){this.children=[];}
 addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
 emit(type,e={}){e={type,preventDefault(){},stopPropagation(){},...e};for(const fn of this.handlers[type]||[])fn(e);}
 closest(selector){return (selector==='.food-bag'&&this.classes.has('food-bag'))||(selector==='#character'&&this.id==='character')?this:this.parent?.closest(selector);}
 getBoundingClientRect(){return this.rect||{left:0,top:0,width:51,height:80};}
 getClientRects(){return this.visible===false?[]:[this.getBoundingClientRect()];}
 cloneNode(){const n=new Node();n.className=this.className;return n;}
 setPointerCapture(id){this.captured.add(id);}hasPointerCapture(id){return this.captured.has(id);}releasePointerCapture(id){this.captured.delete(id);}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
}
const body=new Node(),shelf=new Node(),character=new Node(),portrait=new Node(),cat=new Node(),doc=new Node(),root=new Node();
character.id='character';portrait.rect={left:200,top:150,width:205,height:207};cat.visible=false;
character.querySelector=()=>portrait;let overlay=false;
doc.body=body;doc.createElement=()=>new Node();doc.getElementById=id=>({foodBags:shelf,character,catForm:cat}[id]);doc.elementFromPoint=()=>overlay?new Node():character;
const calls=[];Object.assign(root,{document:doc,CatfoodQuotaMood:require('../static/quota-mood.js'),CatfoodTopUp:{open:profile=>calls.push(profile)}});
vm.runInNewContext(fs.readFileSync(require.resolve('../static/food-bags.js'),'utf8'),{window:root,Intl});
const state=(profile='codex',p=80)=>({profile,config:{source:'manual_quota',quota_unit:'%',quota_remaining:p}});
root.catfoodRefreshBags(state());assert.equal(shelf.children.length,3);
const event=(target,x,y,extra={})=>({target,clientX:x,clientY:y,pointerId:1,isPrimary:true,button:0,...extra});
function start(){const bag=shelf.children[0];shelf.emit('pointerdown',event(bag,20,300));return bag;}
function move(bag,x=300,y=280){shelf.emit('pointermove',event(bag,x,y));}
function up(bag,x=300,y=280,type='pointerup'){shelf.emit(type,event(bag,x,y));}
let bag=start();move(bag);assert.equal(character.classList.contains('food-drop-ready'),true);
doc.emit('keydown',{target:bag,key:'Enter'});assert.deepEqual(calls,[]);
assert.equal(body.children.length,1);root.catfoodRefreshBags(state('deepseek',0));assert.equal(shelf.dataset.brand,'codex');
up(bag);assert.deepEqual(calls,['codex']);assert.equal(shelf.dataset.brand,'deepseek');assert.equal(shelf.children.length,1);assert.equal(body.children.length,0);assert.equal(bag.hasPointerCapture(1),false);
// A click, outside drop, cancellation, lost capture, blur and Escape never open.
bag=start();up(bag);
bag=start();move(bag,10,10);up(bag,10,10);
bag=start();move(bag);up(bag,300,280,'pointercancel');
bag=start();move(bag);up(bag,300,280,'lostpointercapture');
bag=start();move(bag);root.emit('blur');
bag=start();move(bag);doc.emit('keydown',{target:bag,key:'Escape'});
bag=start();move(bag);root.emit('resize');
assert.deepEqual(calls,['codex']);assert.equal(body.children.length,0);
// A dialog obscuring the face must block the drop, even at valid coordinates.
overlay=true;bag=start();move(bag);up(bag);overlay=false;assert.deepEqual(calls,['codex']);
// Another pointer and a right click cannot commit a feeding gesture.
bag=start();move(bag);shelf.emit('pointerup',event(bag,300,280,{pointerId:2}));assert.deepEqual(calls,['codex']);up(bag);
assert.deepEqual(calls,['codex','deepseek']);
shelf.emit('pointerdown',event(shelf.children[0],20,300,{button:2}));assert.equal(body.children.length,0);
doc.emit('keydown',{target:shelf.children[0],key:'Enter'});assert.deepEqual(calls,['codex','deepseek','deepseek']);
console.log('PASS: production drag handlers preserve the starting provider through polls; accept face-only release; cancel cleanly; respect overlays, secondary pointers and keyboard access.');
