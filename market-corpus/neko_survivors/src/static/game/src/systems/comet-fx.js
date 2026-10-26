// 天彗龙袭击 · 特效 v2（第一百八十五轮 · 全局美化）
//
// 用户："天彗龙袭击事件的天彗龙弹幕效果和龙吼之类的也不太好"；依据 天彗龙袭击_视觉弹幕规格.md：
//   · 火是加法(lighter)、烟是普通混合(source-over)，烟必须画在火之上，否则火被"压灭"成脏球
//   · 单团爆炸 = 白核 + 火球(2~3 球叠成爆米花) + 暗酒红烟团 + 黑色碎屑 + 向上飘的余烬
//   · 彗星头(白热泪滴) + 激波钻石(尾迹上等距的亮点，超音速的标志)
//   · 袭击期间红色暗角呼吸
//
// 本模块只管"画"，不碰任何判定/伤害（那些仍在 comet.js 里）。全部预烘焙，帧内零渐变创建。
// 注意：规格书第 9 节说"没有龙"并不准确 —— 现有游戏有真正的龙贴图(fly.png / roar.png)，
// 保留不动；本次重做的是火雨、爆炸、冲击波、龙吼与屏幕氛围。
import { makeCanvas } from './no-dom.js';
const TAU = Math.PI * 2;

function mk(w, h) {
  const c = makeCanvas();
  c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h));
  return c;
}
/** 确定性伪随机：同一个 seed 的同一个 i 永远给出同一个值（让每团爆炸形状稳定、不闪烁） */
export function hash(seed, i) {
  const s = Math.sin(seed * 127.1 + i * 311.7) * 43758.5453123;
  return s - Math.floor(s);
}
const ease = (u) => 1 - (1 - u) * (1 - u);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

// ---------------------------------------------------------------
// 精灵
// ---------------------------------------------------------------
let S = null;

function radial(stops, size = 128) {
  const c = mk(size, size), g = c.getContext('2d');
  const gr = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [p, col] of stops) gr.addColorStop(p, col);
  g.fillStyle = gr; g.fillRect(0, 0, size, size);
  return c;
}

/** 烟团：几个重叠的暗酒红圆斑，边缘不规则（比单个渐变像"翻卷的烟"） */
function bakeSmoke(seed) {
  const c = mk(160, 160), g = c.getContext('2d');
  for (let i = 0; i < 7; i++) {
    const a = hash(seed, i) * TAU, d = 18 + hash(seed, i + 9) * 22;
    const x = 80 + Math.cos(a) * d, y = 80 + Math.sin(a) * d, r = 34 + hash(seed, i + 20) * 20;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, 'rgba(86,20,28,0.55)');
    gr.addColorStop(0.55, 'rgba(60,14,20,0.32)');
    gr.addColorStop(1, 'rgba(42,10,8,0)');
    g.fillStyle = gr; g.fillRect(x - r, y - r, r * 2, r * 2);
  }
  return c;
}

/** 黑色碎屑：不规则多边形 */
function bakeDebris(seed) {
  const c = mk(32, 32), g = c.getContext('2d');
  g.fillStyle = '#1a0a08';
  g.beginPath();
  const n = 5 + ((hash(seed, 1) * 3) | 0);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * TAU, r = 6 + hash(seed, i + 3) * 9;
    g[i ? 'lineTo' : 'moveTo'](16 + Math.cos(a) * r, 16 + Math.sin(a) * r);
  }
  g.closePath(); g.fill();
  g.strokeStyle = 'rgba(255,90,40,0.55)'; g.lineWidth = 1; g.stroke();   // 被火烤红的边
  return c;
}

/** 地面裂缝：9 条放射状锯齿线，白热核 + 橙辉 */
function bakeCracks() {
  const N = 512, c = mk(N, N), g = c.getContext('2d');
  g.translate(N / 2, N / 2);
  g.lineCap = 'round'; g.lineJoin = 'round';
  const lines = [];
  for (let i = 0; i < 9; i++) {
    const a0 = (i / 9) * TAU + hash(3, i) * 0.4;
    const pts = [[0, 0]];
    let r = 0, a = a0;
    while (r < 240) { r += 22 + hash(i, r) * 26; a += (hash(i + 5, r) - 0.5) * 0.55; pts.push([Math.cos(a) * r, Math.sin(a) * r]); }
    lines.push(pts);
  }
  for (const [w, col] of [[11, 'rgba(255,60,20,0.25)'], [6, 'rgba(255,140,50,0.6)'], [2.2, 'rgba(255,244,214,0.95)']]) {
    g.strokeStyle = col; g.lineWidth = w;
    for (const pts of lines) { g.beginPath(); pts.forEach((p, k) => g[k ? 'lineTo' : 'moveTo'](p[0], p[1])); g.stroke(); }
  }
  return c;
}

/** 声波环：软边宽环（亮度峰值在 0.82R） */
function bakeWaveRing(warm) {
  const N = 256;
  return radial(warm
    ? [[0, 'rgba(255,200,120,0)'], [0.62, 'rgba(255,120,60,0)'], [0.8, 'rgba(255,240,220,0.9)'], [0.9, 'rgba(255,100,50,0.5)'], [1, 'rgba(255,60,20,0)']]
    : [[0, 'rgba(255,255,255,0)'], [0.66, 'rgba(255,230,230,0)'], [0.82, 'rgba(255,250,250,0.85)'], [0.92, 'rgba(255,200,200,0.35)'], [1, 'rgba(255,150,150,0)']], N);
}

function ensure() {
  if (S) return;
  S = {
    // 火球三阶段：白热 → 橙 → 暗红（规格 §2 火焰色带）
    hot: radial([[0, 'rgba(255,248,236,1)'], [0.2, 'rgba(255,225,214,0.95)'], [0.45, 'rgba(255,192,96,0.55)'], [1, 'rgba(255,140,60,0)']]),
    orange: radial([[0, 'rgba(255,230,160,0.95)'], [0.3, 'rgba(255,122,48,0.85)'], [0.7, 'rgba(240,56,4,0.4)'], [1, 'rgba(208,48,16,0)']]),
    red: radial([[0, 'rgba(240,80,30,0.8)'], [0.45, 'rgba(208,48,16,0.55)'], [0.8, 'rgba(138,26,8,0.2)'], [1, 'rgba(138,26,8,0)']]),
    ember: radial([[0, 'rgba(255,200,110,1)'], [0.4, 'rgba(255,176,80,0.6)'], [1, 'rgba(255,120,40,0)']], 32),
    smoke: [bakeSmoke(1), bakeSmoke(2), bakeSmoke(3)],
    debris: [bakeDebris(1), bakeDebris(2), bakeDebris(3), bakeDebris(4)],
    cracks: bakeCracks(),
    waveWarm: bakeWaveRing(true),
    waveCool: bakeWaveRing(false),
    vig: null, vigKey: '',
  };
}

/** 预热：把全部精灵烘焙好（实测第一颗火球首次出现时现场烘焙造成 30ms 卡顿；挂在 preloadCometAssets 里提前做） */
export function warm() { ensure(); }

// ---------------------------------------------------------------
// 下落火球 + 落点预警（加法层）
// ---------------------------------------------------------------
/** 彗星头 + 余烬尾 + 激波钻石。b: {x,y,r,vy,landY,seed} */
export function drawFalling(ctx, b, t) {
  ensure();
  const r = b.r;
  // 尾：沿 -y 向上的 7 团余烬，越远越小越暗（红→橙）
  const tl = Math.min(170, b.vy * 0.2);
  const steps = 7;
  for (let i = steps; i >= 1; i--) {
    const u = i / steps, yy = b.y - u * tl;
    const sz = r * (3.4 - 2.2 * u) * (0.9 + 0.1 * Math.sin(t * 30 + i));
    ctx.globalAlpha = 0.85 * (1 - u * 0.85);
    ctx.drawImage(u < 0.45 ? S.orange : S.red, b.x - sz / 2 + (hash(b.seed, i) - 0.5) * r * 0.5, yy - sz / 2, sz, sz);
  }
  // 激波钻石：尾迹上 3 个等距亮点
  for (let k = 1; k <= 3; k++) {
    const yy = b.y - k * (tl / 4.2), sz = r * (0.9 - k * 0.12);
    ctx.globalAlpha = 0.9 - k * 0.2;
    ctx.drawImage(S.hot, b.x - sz, yy - sz, sz * 2, sz * 2);
  }
  // 头：橙色外晕 + 白热核
  const o = r * 5.2;
  ctx.globalAlpha = 0.8; ctx.drawImage(S.orange, b.x - o / 2, b.y - o / 2, o, o);
  const h = r * 3.2;
  ctx.globalAlpha = 1; ctx.drawImage(S.hot, b.x - h / 2, b.y - h / 2, h, h);
}

/** 地面落点预警：越接近地面越亮；半径 = 实际伤害圈（逻辑里是 110） */
export function drawLandingMarker(ctx, b, R = 110) {
  const prox = clamp01(1 - (b.landY - b.y) / 760);
  if (prox <= 0.02) return;
  const pulse = 0.75 + 0.25 * Math.sin(prox * 30);
  ctx.globalAlpha = prox * 0.55 * pulse;
  ctx.strokeStyle = '#ff3a1c'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(b.x, b.landY, R * (0.55 + 0.45 * prox), 0, TAU); ctx.stroke();
  ctx.globalAlpha = prox * 0.22;
  const d = R * 1.5; ctx.drawImage(S.red, b.x - d / 2, b.landY - d / 2, d, d);
}

// ---------------------------------------------------------------
// 爆炸：火(加法) / 烟(普通) / 碎屑(普通) / 余烬(加法) 四层分开画，保证"烟在火之上"
// ---------------------------------------------------------------
/** fire+ember 加法层。a = 爆炸后经过的秒数；R = 火球基准半径；n = LOD 数量系数 */
export function drawExplosionFire(ctx, x, y, R, a, seed, lod = 1) {
  ensure();
  if (a < 0 || a > 2.2) return;
  const grow = ease(clamp01(a / 0.26));
  // 白闪（0~0.06s）
  if (a < 0.1) {
    const d = (R * 0.55 + R * 0.7 * clamp01(a / 0.06)) * 2;
    ctx.globalAlpha = 1 - a * 6; ctx.drawImage(S.hot, x - d / 2, y - d / 2, d, d);
  }
  // 火球：3 团叠成爆米花；阶段切换 白热 → 橙 → 暗红
  const fireA = a < 0.26 ? 1 : Math.max(0, 1 - (a - 0.26) / 0.62);
  if (fireA > 0.01) {
    const spr = a < 0.1 ? S.hot : a < 0.38 ? S.orange : S.red;
    for (let i = 0; i < 3; i++) {
      const ox = (hash(seed, i) - 0.5) * R * 0.7, oy = (hash(seed, i + 4) - 0.5) * R * 0.7;
      const rr = R * (0.8 + hash(seed, i + 8) * 0.5) * grow;
      const d = rr * 2;
      ctx.globalAlpha = fireA * (i === 0 ? 1 : 0.8);
      ctx.drawImage(spr, x + ox - d / 2, y + oy - d / 2, d, d);
    }
  }
  // 余烬：向上飘 1.8s
  const ne = lod >= 1 ? 8 : 4;
  if (a < 1.9) {
    for (let i = 0; i < ne; i++) {
      const life = 1.1 + hash(seed, i + 30) * 0.8;
      if (a > life) continue;
      const u = a / life;
      const ex = x + (hash(seed, i + 40) - 0.5) * R * 1.6 + Math.sin(a * 4 + i) * 8;
      const ey = y - 10 - u * (60 + hash(seed, i + 50) * 80);
      const sz = 5 + hash(seed, i + 60) * 5;
      ctx.globalAlpha = (1 - u) * 0.95;
      ctx.drawImage(S.ember, ex - sz, ey - sz, sz * 2, sz * 2);
    }
  }
}

/** 烟（普通混合，画在火之上）。a 到 ~1.7s 才褪尽 */
export function drawExplosionSmoke(ctx, x, y, R, a, seed, lod = 1) {
  ensure();
  if (a < 0.1 || a > 2.4) return;
  const u = clamp01((a - 0.1) / 1.7);
  const n = lod >= 1 ? 3 : 2;
  for (let i = 0; i < n; i++) {
    const rr = R * (1.0 + hash(seed, i + 70) * 0.5) * (0.8 + 0.7 * ease(u));
    const ox = (hash(seed, i + 74) - 0.5) * R * 0.9, oy = (hash(seed, i + 78) - 0.5) * R * 0.5 - u * R * 0.5;
    const d = rr * 2;
    // 实测：17 团爆炸余烟叠加时烟占屏 9.3%（旧版 0.1%）而橙色火焰从 2.0% 掉到 0.76% —— 烟压得太狠。
    // 0.62 → 0.40，并让烟更快散（衰减曲线 (1-u)^1.35），仍然在火之上，但火焰能透出来。
    ctx.globalAlpha = 0.40 * Math.pow(1 - u, 1.35) * (u < 0.1 ? u / 0.1 : 1);
    ctx.drawImage(S.smoke[i % 3], x + ox - d / 2, y + oy - d / 2, d, d);
  }
}

/** 黑色碎屑：向外抛出，阻尼 + 重力，随旋转淡出 */
export function drawExplosionDebris(ctx, x, y, R, a, seed, lod = 1) {
  ensure();
  if (a < 0 || a > 1.0) return;
  const n = lod >= 1 ? 6 : 3;
  for (let i = 0; i < n; i++) {
    const th = hash(seed, i + 90) * TAU, sp = R * (1.0 + hash(seed, i + 94) * 1.1);
    const dist = sp * (1 - Math.exp(-a * 3.2));
    const px = x + Math.cos(th) * dist, py = y + Math.sin(th) * dist * 0.7 + a * a * 70;
    const sz = 8 + hash(seed, i + 98) * 9;
    ctx.save();
    ctx.translate(px, py); ctx.rotate(a * (3 + hash(seed, i) * 6) + i);
    ctx.globalAlpha = 1 - a;
    ctx.drawImage(S.debris[i & 3], -sz / 2, -sz / 2, sz, sz);
    ctx.restore();
  }
}

/** 地面裂缝（中心大爆炸）：0~0.4s 向外裂开，随后慢慢暗下去 */
export function drawCracks(ctx, x, y, R, age, life = 4.2) {
  ensure();
  if (age < 0 || age > life) return;
  const g = ease(clamp01(age / 0.4)), d = R * 2 * g;
  ctx.globalAlpha = (age < 0.4 ? 1 : Math.pow(1 - (age - 0.4) / (life - 0.4), 1.4)) * (0.8 + 0.2 * Math.sin(age * 9));
  ctx.drawImage(S.cracks, x - d / 2, y - d / 2, d, d);
}

/** 加法声波/冲击波环（贴图，宽软边）；kind: 'warm' | 'cool' */
export function drawWave(ctx, x, y, rad, alpha, warm) {
  ensure();
  const d = rad * 2.25;           // 贴图亮度峰值在 0.82R → 实际半径 = rad
  ctx.globalAlpha = alpha;
  ctx.drawImage(warm ? S.waveWarm : S.waveCool, x - d / 2, y - d / 2, d, d);
}

/** 彗尾粒子（龙身后的拖尾）：k = 剩余生命 0..1 */
export function drawTrailPuff(ctx, s, k) {
  ensure();
  const d = s.r * (1.8 + (1 - k) * 1.8) * 2;
  ctx.globalAlpha = k * 0.85;
  ctx.drawImage(k > 0.55 ? S.orange : S.red, s.x - d / 2, s.y - d / 2, d, d);
}

/** 龙的光晕（沿龙体局部坐标，头朝 -y）：头前亮点 + 身后拖出的余烬 —— 让剪影在暗处也看得清 */
export function drawDragonAura(ctx, w, h, t) {
  ensure();
  const head = w * 0.62;
  ctx.globalAlpha = 0.9; ctx.drawImage(S.orange, -head / 2, -h * 0.5 - head * 0.35, head, head);
  ctx.globalAlpha = 1; const hc = w * 0.26; ctx.drawImage(S.hot, -hc / 2, -h * 0.5 - hc * 0.25, hc, hc);
  for (let i = 0; i < 6; i++) {
    const u = i / 6, sz = w * (0.5 - u * 0.32), yy = h * 0.05 + u * h * 0.75;
    ctx.globalAlpha = 0.5 * (1 - u) * (0.85 + 0.15 * Math.sin(t * 20 + i));
    ctx.drawImage(u < 0.4 ? S.orange : S.red, -sz / 2, yy - sz / 2, sz, sz);
  }
}

// ---------------------------------------------------------------
// 屏幕：红色暗角呼吸 + 袭击前天色变暗
// ---------------------------------------------------------------
export function drawVignette(ctx, W, H, intensity, t) {
  if (intensity <= 0.01) return;
  const key = `${W}x${H}`;
  if (!S) ensure();
  if (!S.vig || S.vigKey !== key) {
    S.vig = mk(W, H);
    const g = S.vig.getContext('2d');
    const gr = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.34, W / 2, H / 2, Math.hypot(W, H) * 0.58);
    gr.addColorStop(0, 'rgba(90,0,0,0)'); gr.addColorStop(1, 'rgba(90,0,0,1)');
    g.fillStyle = gr; g.fillRect(0, 0, W, H);
    S.vigKey = key;
  }
  ctx.globalAlpha = intensity * 0.35 * (0.8 + 0.2 * Math.sin(t * Math.PI));   // 2s 一次呼吸
  ctx.drawImage(S.vig, 0, 0, W, H);
}
