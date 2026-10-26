// Original YUI-inspired cube model. The approved raster skin is copied unchanged.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const here=path.dirname(fileURLToPath(import.meta.url)), out=path.join(here,'neko_yui');
const json=async(file,value)=>{await fs.mkdir(path.dirname(path.join(out,file)),{recursive:true});await fs.writeFile(path.join(out,file),JSON.stringify(value)+'\n');};
const face=(u,v,w=1,h=1)=>({uv:[u,v],uv_size:[w,h]});
const solid=(u,v)=>Object.fromEntries(['north','south','east','west','up','down'].map(n=>[n,face(u+.1,v+.1,.8,.8)]));
// Palette samples are coordinates in the unchanged imagegen-generated 64x64 skin.
const ink=solid(18,0), hair=solid(12,2), highlight=solid(9,0), white=solid(41,22);
const blue=solid(53,46), pale=solid(26,29), skin=solid(19,6);
const boxUV=(u,v,w,h,d)=>({north:face(u+d,v+d,w,h),east:face(u,v+d,d,h),
  south:face(u+2*d+w,v+d,w,h),west:face(u+d+w,v+d,d,h),
  up:face(u+d,v,w,d),down:face(u+d+w,v+d,w,-d)});
const cube=(origin,size,uv,extra={})=>({origin,size,uv,...extra});
const rounded=(origin,size,uv)=>[cube(origin,size,uv)]; // Explicit cubes remain editable in Blockbench.
const bones=[];
const bone=(name,parent,pivot,cubes=[],rotation)=>{const b={name,...(parent?{parent}:{}),pivot,...(rotation?{rotation}:{}),...(cubes.length?{cubes}:{})};bones.push(b);return b;};
const add=(b,origin,size,uv,extra)=>b.cubes.push(cube(origin,size,uv,extra));

bone('Root',null,[0,0,0]);bone('MAllBody','Root',[0,13,0]);bone('AllBody','MAllBody',[0,13,0]);
bone('UpBody','AllBody',[0,16,0]);
const torso=bone('UpperBody','UpBody',[0,19,0],rounded([-3.5,14,-1.9],[7,9.5,3.8],boxUV(16,16,8,12,4)));
add(torso,[-3.65,15.7,-2.05],[7.3,.65,4.1],ink);
add(torso,[-.55,15.65,-2.25],[1.1,.8,.3],pale);
add(torso,[-.25,15.85,-2.45],[.5,.4,.25],white);
add(torso,[-1.1,22.9,-1.35],[2.2,1.2,2.7],skin);
const bow=(b,x,y,z,scale=1,color=white)=>{
  add(b,[x-1.6*scale,y-.5*scale,z],[1.3*scale,scale,.55*scale],color,{pivot:[x,y,z],rotation:[0,0,-15]});
  add(b,[x+.3*scale,y-.5*scale,z],[1.3*scale,scale,.55*scale],color,{pivot:[x,y,z],rotation:[0,0,15]});
  add(b,[x-.35*scale,y-.35*scale,z-.1*scale],[.7*scale,.7*scale,.8*scale],pale);
};
bow(torso,0,21.2,-2.45,.9);
add(torso,[-.22,19.3,-2.12],[.44,1.5,.3],white);
bone('AllHead','UpperBody',[0,24,0]);bone('MHead','AllHead',[0,24,0]);
const head=bone('Head','MHead',[0,24,0],[cube([-4,24,-4],[8,8,8],boxUV(0,0,8,8,8))]);
bone('Hat','Head',[0,24,0],[cube([-4,24,-4],[8,8,8],boxUV(32,0,8,8,8),{inflate:.13})]);
bone('ViewLocator','Head',[0,28,-3.7]);
for(const [side,s] of [['Right',-1],['Left',1]]){
  const ear=bone(side+'Ear','Head',[s*2.8,31.4,.3],[
    cube([s*2.8-1.35,31.5,-.85],[2.7,1.4,1.7],hair),
    cube([s*2.8-.95,32.7,-.65],[1.9,1.45,1.3],hair),
    cube([s*2.8-.45,34.0,-.45],[.9,1.1,.9],ink),
    cube([s*2.8-.85,31.95,-.94],[1.7,.75,.16],white),
    cube([s*2.8-.45,32.7,-.74],[.9,.9,.16],pale)
  ],[0,0,s*9]);
  // Two independently animated sections prevent the twin tails behaving like a rigid cape.
  const upper=bone(side+'Hair','Head',[s*4.3,29,2],[
    cube([s*4.8-.8,24,1.55],[1.6,5.5,1.65],hair),
    cube([s*4.8-.68,24.2,1.4],[.35,5,.23],highlight)
  ],[4,0,-s*5]);
  const lower=bone(side+'HairTip',side+'Hair',[s*4.8,24,2.1],[
    cube([s*4.85-.72,19.9,1.7],[1.44,4.3,1.4],hair),
    cube([s*4.9-.5,18.8,1.9],[1,1.4,1],ink)
  ],[-4,0,s*7]);
  const ribbon=bone(side+'HairRibbon','Head',[s*4.2,29,2],[cube([s*4.2-.5,28.7,1.15],[1,.6,1.9],pale)]);
  bow(ribbon,s*4.2,29.2,1.25,.62);
  add(head,[s*3.4-.45,23.1,-2.4],[.9,4.5,1.3],hair);
}
// Rose hair ornament and the small cat perched above the fringe.
add(head,[1.65,29.3,-4.25],[1.2,1.2,.35],blue,{pivot:[2.25,29.9,-4.1],rotation:[0,0,45]});
add(head,[1.92,29.55,-4.47],[.7,.7,.25],white);
add(head,[2.1,29.7,-4.7],[.35,.35,.25],pale);
add(head,[-.8,31.9,-.9],[1.6,1.15,1.3],white);
add(head,[-.8,32.85,-.65],[.4,.45,.7],white);add(head,[.4,32.85,-.65],[.4,.45,.7],white);
for(const x of [-.45,.3])add(head,[x,32.35,-1.03],[.17,.2,.15],blue);
add(head,[-.1,32.1,-1.04],[.2,.15,.15],skin);

const skirt=bone('Skirt','UpBody',[0,16.2,0],[cube([-3.6,14.5,-2.1],[7.2,1.8,4.2],white)]);
// Eight sloped panels form an A-line silhouette; stepped hems avoid unsupported mesh primitives.
for(let i=0;i<8;i++){
  const r=2.65,p=bone('SkirtPanel'+i,'Skirt',[0,16.2,0],[],[0,i*45,0]);
  // Wider lower strips overlap their neighbors; all panels rotate around the same center.
  p.cubes=[cube([-1.4,13.7,r],[2.8,1.8,.45],i%2?pale:white,{pivot:[0,15.5,r],rotation:[22,0,0]}),
    cube([-1.8,11.9,r],[3.6,1.8,.45],i%2?pale:white,{pivot:[0,15.5,r],rotation:[22,0,0]}),
    cube([-2.15,10.1,r],[4.3,1.8,.45],i%2?pale:white,{pivot:[0,15.5,r],rotation:[22,0,0]}),
    cube([-2.2,9.8,r-.05],[4.4,.55,.65],white,{pivot:[0,15.5,r],rotation:[22,0,0]})];
  for(const x of [-1.9,-.95,0,.95,1.9])add(p,[x-.3,9.5,r-.08],[.6,.55,.65],white,{pivot:[0,15.5,r],rotation:[22,0,0]});
}
// Front split overskirt, waist lacing and hem bows use the original blue/white palette.
for(const s of [-1,1]){
  add(skirt,[s*2.6-1.35,12.4,-3.4],[2.7,2.4,.45],pale,{pivot:[s*1.3,15.2,-2.1],rotation:[20,0,s*15]});
  bow(skirt,s*4.5,10.6,-2.7,.58);
}
const armBones=[];
for(const [side,s,u,v] of [['Right',-1,40,16],['Left',1,32,48]]){
  const shoulder=s*4.8;
  const a=bone(side+'Arm','UpperBody',[shoulder,22.7,0],[
    cube([shoulder-1.35,18.1,-1.5],[2.7,5.5,3],{north:face(u+4,v+4,3,6),south:face(u+11,v+4,3,6),east:face(u,v+4,4,6),west:face(u+7,v+4,4,6),up:face(u+4,v,3,4),down:face(u+7,v+4,3,-4)}),
    cube([shoulder-1.6,20.1,-1.65],[3.2,3,3.3],white)
  ]);
  const f=bone(side+'ForeArm',side+'Arm',[shoulder,18.2,0],[cube([shoulder-1.2,13.5,-1.35],[2.4,4.8,2.7],white),cube([shoulder-1.3,13.6,-1.45],[2.6,.6,2.9],pale)]);
  const h=bone(side+'Hand',side+'ForeArm',[shoulder,13.5,0],[cube([shoulder-.85,12.4,-.9],[1.7,1.4,1.8],skin)]);
  bow(f,shoulder,14.1,-1.7,.5);
  bone(side+'HandLocator',side+'Hand',[shoulder,12.8,0]);
  armBones.push(a,f,h,bones.at(-1));
  bone(side+'ShoulderLocator','UpperBody',[s*4.3,23.4,0]);
}
bone('ElytraLocator','UpperBody',[0,23,2]);
bone('DownBody','AllBody',[0,13,0]);
for(const [side,s,u,v] of [['Right',-1,0,16],['Left',1,16,48]]){
  const x=s*1.8;
  bone(side+'Leg','DownBody',[x,13,0],[cube([x-1.4,7,-1.5],[2.8,6,3],{
    north:face(u+4,v+4,4,6),south:face(u+12,v+4,4,6),east:face(u,v+4,4,6),west:face(u+8,v+4,4,6),up:face(u+4,v,4,4),down:face(u+8,v+4,4,-4)})]);
  const lower=bone(side+'LowerLeg',side+'Leg',[x,7,0],[cube([x-1.35,1.2,-1.45],[2.7,5.8,2.9],white)]);
  const foot=bone(side+'Foot',side+'LowerLeg',[x,1.5,0],[cube([x-1.45,0,-2.2],[2.9,1.6,3.75],white),cube([x-1.48,0,-2.22],[2.96,.35,3.8],pale)]);
  bow(lower,x,6.2,-1.7,.48,ink);bow(foot,x,1.0,-2.35,.45);
}
bone('Tail','UpBody',[0,15,2],[cube([-.55,14.5,2],[1.1,1.1,3.5],hair)],[18,10,0]);
bone('TailMid','Tail',[0,15,5.2],[cube([-.5,14.5,5.2],[1,1,3],hair)],[0,-26,0]);
bone('TailTip','TailMid',[0,15,8],[cube([-.55,14.45,8],[1.1,1.1,2.2],ink)],[0,-32,0]);
bone('molang',null,[0,0,0]);

const geometry=(id,items)=>({format_version:'1.12.0','minecraft:geometry':[{description:{identifier:'geometry.'+id,texture_width:64,texture_height:64,visible_bounds_width:4,visible_bounds_height:4,visible_bounds_offset:[0,1,0]},bones:items}]});
await json('models/main.json',geometry('neko_yui',bones));
// First-person arms have their own root bones; no hidden torso or head geometry is included.
const armNames=new Set(bones.filter(b=>/^(Left|Right)(Arm|ForeArm|Hand|HandLocator)$/.test(b.name)).map(b=>b.name));
await json('models/arm.json',geometry('neko_yui_arm',bones.filter(b=>armNames.has(b.name)).map(b=>{
  const copy=structuredClone(b);if(!armNames.has(copy.parent))delete copy.parent;return copy;
})));

const keys=(length,values)=>Object.fromEntries(values.map((v,i)=>[(length*i/(values.length-1)).toFixed(3),v]));
const rot=values=>({rotation:values});
const pose=(length,b,loop=true)=>({loop,animation_length:length,bones:b});
const sway=(length,amount=6)=>({
  Tail:rot(keys(length,[[0,-amount,0],[0,amount,0],[0,-amount,0]])),
  TailMid:rot(keys(length,[[0,amount,0],[0,-amount,0],[0,amount,0]])),
  RightHair:rot(keys(length,[[0,0,-2],[3,0,0],[0,0,-2]])),LeftHair:rot(keys(length,[[0,0,2],[3,0,0],[0,0,2]]))
});
const main={empty:pose(0,{}),idle:pose(3,{...sway(3),UpperBody:{position:keys(3,[[0,0,0],[0,.1,0],[0,0,0]])}})};
const gait=(length,leg,arm,bounce)=>pose(length,{
  ...sway(length,9),
  AllBody:{position:keys(length,[[0,0,0],[0,bounce,0],[0,0,0],[0,bounce,0],[0,0,0]])},
  RightLeg:rot(keys(length,[[leg,0,0],[0,0,0],[-leg,0,0],[0,0,0],[leg,0,0]])),
  LeftLeg:rot(keys(length,[[-leg,0,0],[0,0,0],[leg,0,0],[0,0,0],[-leg,0,0]])),
  RightLowerLeg:rot(keys(length,[[0,0,0],[0,0,0],[18,0,0],[0,0,0],[0,0,0]])),
  LeftLowerLeg:rot(keys(length,[[18,0,0],[0,0,0],[0,0,0],[0,0,0],[18,0,0]])),
  RightArm:rot(keys(length,[[-arm,0,3],[0,0,3],[arm,0,3],[0,0,3],[-arm,0,3]])),
  LeftArm:rot(keys(length,[[arm,0,-3],[0,0,-3],[-arm,0,-3],[0,0,-3],[arm,0,-3]])),
  Skirt:rot(keys(length,[[2,0,0],[-2,0,0],[2,0,0],[-2,0,0],[2,0,0]]))
});
main.walk=gait(.9,22,16,.15);main.run=gait(.6,35,28,.35);
main.sneaking=pose(2,{...sway(2,4),AllBody:{position:[0,-2,0]},UpperBody:rot([20,0,0]),RightLeg:rot([-15,0,0]),LeftLeg:rot([-15,0,0])});
main.sneak=gait(1.25,13,9,.05);main.sneak.bones.AllBody.position=[0,-2,0];main.sneak.bones.UpperBody=rot([20,0,0]);
const sitting={RightLeg:rot([-80,-8,0]),LeftLeg:rot([-80,8,0]),RightLowerLeg:rot([65,0,0]),LeftLowerLeg:rot([65,0,0]),RightArm:rot([-15,0,4]),LeftArm:rot([-15,0,-4])};
main.sit=pose(3,{...sitting,...sway(3)});main.boat=pose(3,{...sitting,...sway(3)});main.ride=pose(3,{...sitting,...sway(3)});main.ride_pig=structuredClone(main.ride);
main.jump=pose(.8,{...sway(.8),RightLeg:rot([-12,0,0]),LeftLeg:rot([10,0,0]),RightLowerLeg:rot([18,0,0]),RightArm:rot([0,0,15]),LeftArm:rot([0,0,-15])});
main.fly=pose(2,{...sway(2),RightArm:rot([0,0,25]),LeftArm:rot([0,0,-25]),RightLeg:rot([10,0,0]),LeftLeg:rot([-10,0,0])});
main.elytra_fly=pose(1,{AllBody:rot([-80,0,0]),RightArm:rot([10,0,10]),LeftArm:rot([10,0,-10])});main.riptide=structuredClone(main.elytra_fly);
main.swim=gait(1.2,15,45,.1);main.swim.bones.AllBody.rotation=[-80,0,0];main.swim_stand=gait(1.8,8,10,.05);
main.climbing=pose(1,{AllBody:rot([-80,0,0])});main.climb=structuredClone(main.swim);
main.sleep=pose(2,{AllBody:rot([90,0,0])});
main.attacked=pose(.35,{UpperBody:rot(keys(.35,[[0,0,0],[-15,5,0],[0,0,0]]))},false);
main.death=pose(1,{AllBody:rot(keys(1,[[0,0,0],[0,0,30],[0,0,90]]))},false);
main.ladder_up=gait(1.2,20,55,0);main.ladder_stillness=pose(1,{RightArm:rot([-120,0,0]),LeftArm:rot([-120,0,0])});main.ladder_down=structuredClone(main.ladder_up);
for(const [side,s,action] of [['Right',1,'mainhand'],['Left',-1,'offhand']]){
  main['use_'+action]=pose(.4,{[side+'Arm']:rot([-55,0,s*10]),[side+'ForeArm']:rot([-25,0,0])},false);
  main[side==='Right'?'swing_hand':'swing_offhand']=pose(.4,{[side+'Arm']:rot(keys(.4,[[0,0,0],[-80,s*12,0],[-30,0,0],[0,0,0]]))},false);
}
const extra={
  extra1:pose(2,{...sway(2),RightArm:rot([-5,0,155]),RightForeArm:rot(keys(2,[[0,0,-15],[0,0,20],[0,0,-15],[0,0,20],[0,0,-15]]))},false),
  extra2:pose(1.8,{...sway(1.8,12),RightArm:rot([0,0,140]),LeftArm:rot([0,0,-140]),Head:rot(keys(1.8,[[0,0,-4],[0,0,4],[0,0,-4]]))},false),
  extra3:pose(2,{...sway(2),UpperBody:rot(keys(2,[[0,0,0],[18,0,0],[18,0,0],[0,0,0]])),RightArm:rot([-15,0,5]),LeftArm:rot([-15,0,-5])},false)
};
Object.assign(main,extra); // Web receives a single authoritative clip name, so extras are resolvable there too.
await json('animations/main.animation.json',{format_version:'1.8.0',animations:main});
await json('animations/extra.animation.json',{format_version:'1.8.0',animations:extra});
const conditional={};
for(const [side,s,hand] of [['Right',1,'mainhand'],['Left',-1,'offhand']]){
  conditional['hold_'+hand]=pose(0,{[side+'Arm']:rot([-10,0,s*3])});
  for(const action of ['eat','drink'])conditional['use_'+hand+':'+action]=pose(1.5,{[side+'Arm']:rot([-65,0,s*12]),[side+'ForeArm']:rot([-35,0,0])});
  for(const action of ['shield','block'])conditional['use_'+hand+':'+action]=pose(0,{[side+'Arm']:rot([-55,0,s*25]),[side+'ForeArm']:rot([-25,0,0])});
  conditional['use_'+hand+':bow']=pose(.5,{RightArm:rot([-85,0,-10]),LeftArm:rot([-85,0,12]),[side+'ForeArm']:rot([-30,0,0])},'hold_on_last_frame');
  conditional['use_'+hand+':spear']=pose(.5,{[side+'Arm']:rot([-155,0,s*12])},'hold_on_last_frame');
}
await json('animations/arm.animation.json',{format_version:'1.8.0',animations:conditional});
await json('ysm.json',{spec:2,metadata:{name:'NEKO · YUI 蓝白洛丽塔',tips:'黑猫耳 · 双马尾 · 蓝白裙装\n为 ag_NEKO 制作的方块风格原创模型',
  authors:[{name:'jcs130',role:'角色方案 / 模型委托'},{name:'Codex',role:'原创几何 / 动画 / 打包'}],
  license:{type:'Custom',desc:'原创几何与动画按本仓库许可；角色设计参考 N.E.K.O. YUI，未包含其 Live2D 源文件。'},link:{home:'https://github.com/jcs130/mc-agent-neko'}},
  properties:{height_scale:.9,width_scale:.9,free:true,default_texture:'default',preview_animation:'idle',extra_animation:{extra1:'招手 · 喵',extra2:'开心欢呼',extra3:'礼貌鞠躬'}},
  files:{player:{model:{main:'models/main.json',arm:'models/arm.json'},animation:{main:'animations/main.animation.json',arm:'animations/arm.animation.json',extra:'animations/extra.animation.json'},texture:['textures/default.png']}}});
await fs.mkdir(path.join(out,'textures'),{recursive:true});
await fs.copyFile(path.resolve(here,'../../skins/yui-lolita-slim.png'),path.join(out,'textures/default.png'));
const resources=['ysm.json','models/main.json','models/arm.json','animations/main.animation.json','animations/arm.animation.json','animations/extra.animation.json','textures/default.png'];
const files={};for(const file of resources){const bytes=await fs.readFile(path.join(out,file));files[file]={bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};}
await fs.writeFile(path.join(here,'neko-yui-manifest.json'),JSON.stringify({modelId:'neko_yui',textureId:'default',version:'1.0.0',ysmTarget:'2.4.1',files},null,2)+'\n');
console.log(JSON.stringify({out,bones:bones.length,cubes:bones.reduce((n,b)=>n+(b.cubes?.length??0),0),mainClips:Object.keys(main).length,resources:resources.length}));
