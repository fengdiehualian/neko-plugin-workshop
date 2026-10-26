// 天气特效 v2（第一百八十五轮 · 全局美化）
//
// 用户："天气系统……特效优化，你有更好的想法就优化"。
// 范围：流星雨(火球/燃烧区)、血月、快晴、晴岚、烈日、雾雨。
// 不动：台风 / 川雾 / 浓雾 —— 那三个是用户逐轮写规格定下来的，视觉与手感都已认可。
//
// 全部预烘焙精灵 + 确定性公式（无状态），帧内零 createRadialGradient / shadowBlur。
// 粒子位置全由 (i, t) 推出，不占内存、暂停时自然冻结。
import { makeCanvas } from './no-dom.js';
const TAU = Math.PI * 2;

function mk(w, h) {
  const c = makeCanvas();
  c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h));
  return c;
}
function hash(i, s = 0) {
  const x = Math.sin(i * 127.1 + s * 311.7) * 43758.5453123;
  return x - Math.floor(x);
}
function radial(size, stops) {
  const c = mk(size, size), g = c.getContext('2d');
  const gr = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [p, col] of stops) gr.addColorStop(p, col);
  g.fillStyle = gr; g.fillRect(0, 0, size, size);
  return c;
}

let S = null;
function ensure() {
  if (S) return;
  S = {
    hot: radial(128, [[0, 'rgba(255,250,235,1)'], [0.2, 'rgba(255,225,170,0.95)'], [0.5, 'rgba(255,150,60,0.5)'], [1, 'rgba(255,100,30,0)']]),
    fire: radial(128, [[0, 'rgba(255,230,160,0.95)'], [0.35, 'rgba(255,128,48,0.8)'], [0.75, 'rgba(220,50,16,0.3)'], [1, 'rgba(180,30,10,0)']]),
    ember: radial(32, [[0, 'rgba(255,210,120,1)'], [0.45, 'rgba(255,170,70,0.6)'], [1, 'rgba(255,120,40,0)']]),
    redGlow: radial(128, [[0, 'rgba(255,90,70,0.5)'], [0.5, 'rgba(220,30,40,0.2)'], [1, 'rgba(160,10,30,0)']]),
    sun: radial(256, [[0, 'rgba(255,255,240,1)'], [0.12, 'rgba(255,244,190,0.85)'], [0.35, 'rgba(255,220,130,0.3)'], [1, 'rgba(255,200,100,0)']]),
    warm: radial(64, [[0, 'rgba(255,230,160,0.7)'], [1, 'rgba(255,200,100,0)']]),
    mist: radial(128, [[0, 'rgba(190,214,240,0.5)'], [0.6, 'rgba(170,200,230,0.18)'], [1, 'rgba(160,190,225,0)']]),
    rays: null, moon: null, petal: [], ripple: null,
  };
  // 日光射线（扇形，从中心发散）
  {
    const N = 512, c = mk(N, N), g = c.getContext('2d');
    g.translate(N / 2, N / 2);
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * TAU + hash(i, 3) * 0.2, w = 0.04 + hash(i, 4) * 0.05, L = 250;
      const gr = g.createLinearGradient(0, 0, Math.cos(a) * L, Math.sin(a) * L);
      gr.addColorStop(0, 'rgba(255,248,210,0.9)'); gr.addColorStop(1, 'rgba(255,240,190,0)');
      g.fillStyle = gr;
      g.beginPath(); g.moveTo(0, 0); g.lineTo(Math.cos(a - w) * L, Math.sin(a - w) * L); g.lineTo(Math.cos(a + w) * L, Math.sin(a + w) * L); g.closePath(); g.fill();
    }
    S.rays = c;
  }
  // 血月：暗红圆盘 + 月面斑 + 缘光
  {
    const N = 256, c = mk(N, N), g = c.getContext('2d');
    const gr = g.createRadialGradient(N / 2, N / 2, N * 0.16, N / 2, N / 2, N / 2);
    gr.addColorStop(0, 'rgba(255,70,60,0.5)'); gr.addColorStop(0.55, 'rgba(200,20,40,0.22)'); gr.addColorStop(1, 'rgba(150,0,30,0)');
    g.fillStyle = gr; g.fillRect(0, 0, N, N);
    g.beginPath(); g.arc(N / 2, N / 2, N * 0.27, 0, TAU);
    const disc = g.createRadialGradient(N * 0.45, N * 0.42, 4, N / 2, N / 2, N * 0.27);
    disc.addColorStop(0, '#ff7a64'); disc.addColorStop(0.7, '#d02a34'); disc.addColorStop(1, '#8c1224');
    g.fillStyle = disc; g.fill();
    g.save(); g.clip();
    for (let i = 0; i < 7; i++) { g.fillStyle = `rgba(90,0,20,${0.14 + hash(i, 9) * 0.14})`; g.beginPath(); g.arc(N / 2 + (hash(i, 5) - 0.5) * N * 0.36, N / 2 + (hash(i, 6) - 0.5) * N * 0.36, 6 + hash(i, 7) * 14, 0, TAU); g.fill(); }
    g.restore();
    S.moon = c;
  }
  // 花瓣 / 叶片（两色）
  for (const col of ['#ffd2e0', '#c8f2b8', '#fff0b0']) {
    const c = mk(24, 24), g = c.getContext('2d');
    g.translate(12, 12);
    const gr = g.createLinearGradient(-9, 0, 9, 0);
    gr.addColorStop(0, col); gr.addColorStop(1, 'rgba(255,255,255,0.7)');
    g.fillStyle = gr;
    g.beginPath(); g.moveTo(-9, 0); g.quadraticCurveTo(0, -8, 9, 0); g.quadraticCurveTo(0, 8, -9, 0); g.fill();
    g.strokeStyle = 'rgba(255,255,255,0.5)'; g.lineWidth = 0.8; g.beginPath(); g.moveTo(-8, 0); g.lineTo(8, 0); g.stroke();
    S.petal.push(c);
  }
  // 水纹
  {
    const c = mk(64, 32), g = c.getContext('2d');
    g.strokeStyle = 'rgba(210,230,250,0.9)'; g.lineWidth = 2;
    g.beginPath(); g.ellipse(32, 16, 28, 11, 0, 0, TAU); g.stroke();
    g.lineWidth = 1; g.globalAlpha = 0.6; g.beginPath(); g.ellipse(32, 16, 17, 6.5, 0, 0, TAU); g.stroke();
    S.ripple = c;
  }
}
export function warm() { ensure(); }

// =================================================================
// 流星雨（世界坐标）
// =================================================================
/**
 * 下落火球：白热核 + 橙色火团 + 沿来路的 12 团渐小渐暗余烬尾 + 前方炽热光晕。
 * (px,py) 当前位置；ox,oy = 入射起点相对落点偏移；k = 0..1 落地进度；m = {x,y}
 */
export function drawMeteorBall(ctx, m, ox, oy, k, ramp, t) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 12; i >= 1; i--) {
    const tb = Math.max(0, k - i * 0.045);
    const tx = m.x + ox * (1 - tb), ty = m.y + oy * (1 - tb);
    const u = i / 12, sz = (46 - u * 30) * (0.9 + 0.1 * Math.sin(t * 30 + i));
    ctx.globalAlpha = (0.7 - u * 0.6) * ramp;
    ctx.drawImage(u < 0.4 ? S.fire : S.redGlow, tx - sz / 2 + (hash(i, 1) - 0.5) * 6, ty - sz / 2, sz, sz);
  }
  const px = m.x + ox * (1 - k), py = m.y + oy * (1 - k);
  ctx.globalAlpha = 0.85 * ramp; ctx.drawImage(S.fire, px - 40, py - 40, 80, 80);
  ctx.globalAlpha = ramp; ctx.drawImage(S.hot, px - 26, py - 26, 52, 52);
  ctx.restore();
}

/** 落点预警环（阶段 1）：加法的红环 + 内部倒计时填充 + 外圈细环。prog = 0..1 */
export function drawMeteorWarn(ctx, m, prog, pulse) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const d = m.r * 2.3;
  ctx.globalAlpha = 0.22 * prog + 0.06; ctx.drawImage(S.redGlow, m.x - d / 2, m.y - d / 2, d, d);
  const shrink = m.r * (1.5 - 0.5 * prog);
  ctx.strokeStyle = '#ff3a22'; ctx.lineWidth = 2.5 + prog * 2;
  ctx.globalAlpha = 0.45 + 0.4 * pulse;
  ctx.beginPath(); ctx.arc(m.x, m.y, shrink, 0, TAU); ctx.stroke();
  ctx.globalAlpha = 0.18 + 0.1 * pulse; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(m.x, m.y, shrink + 8, 0, TAU); ctx.stroke();
  // 8 个向内收拢的小箭头刻度，让"正在锁定"一眼可读
  ctx.globalAlpha = 0.5 * prog; ctx.lineWidth = 2;
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU + prog * 1.2, r0 = shrink + 4, r1 = shrink + 14 * (1 - prog) + 4;
    ctx.beginPath(); ctx.moveTo(m.x + Math.cos(a) * r1, m.y + Math.sin(a) * r1); ctx.lineTo(m.x + Math.cos(a) * r0, m.y + Math.sin(a) * r0); ctx.stroke();
  }
  ctx.restore();
}

/** 持续燃烧区：地面辉光盘 + 3 簇跳动的火舌(精灵叠加) + 上飘火星。fade = 0..1 */
export function drawBurnZone(ctx, z, t, fade) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const d = z.r * 2.2;
  ctx.globalAlpha = 0.5 * fade; ctx.drawImage(S.redGlow, z.x - d / 2, z.y - d / 2, d, d);
  ctx.globalAlpha = 0.55 * fade; ctx.strokeStyle = '#ff4a22'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(z.x, z.y, z.r, 0, TAU); ctx.stroke();
  const seed = z.x * 0.013 + z.y * 0.007;
  for (let c = 0; c < 5; c++) {
    const ca = (c / 5) * TAU + seed, fx = z.x + Math.cos(ca) * z.r * 0.5, fy = z.y + Math.sin(ca) * z.r * 0.5;
    for (let i = 0; i < 3; i++) {
      const fl = 0.5 + 0.5 * Math.sin(t * 13 + c * 2.1 + i * 1.7);
      const sz = (44 - i * 11) * (0.75 + fl * 0.4);
      ctx.globalAlpha = (0.55 - i * 0.12) * fade * (0.65 + fl * 0.35);
      ctx.drawImage(i === 0 ? S.fire : S.hot, fx - sz / 2, fy - i * 10 - fl * 7 - sz / 2, sz, sz);
    }
  }
  for (let i = 0; i < 12; i++) {
    const per = 1.4 + hash(i, 2) * 1.2, u = ((t + hash(i, 3) * per) % per) / per;
    const ex = z.x + (hash(i, 4) - 0.5) * z.r * 1.6 + Math.sin(t * 3 + i) * 6, ey = z.y - u * 70;
    ctx.globalAlpha = (1 - u) * 0.8 * fade; ctx.drawImage(S.ember, ex - 5, ey - 5, 10, 10);
  }
  ctx.restore();
}

// =================================================================
// 屏幕层
// =================================================================
/** 血月：左上方一轮巨大的血色月亮 + 缘光呼吸 + 12 颗缓缓上升的红色余烬 */
export function drawBloodMoon(ctx, W, H, t, ramp) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const R = Math.min(W, H) * 0.62, x = W * 0.18, y = H * 0.16;
  ctx.globalAlpha = ramp * (0.55 + 0.1 * Math.sin(t * 1.4)); ctx.drawImage(S.moon, x - R / 2, y - R / 2, R, R);
  for (let i = 0; i < 14; i++) {
    const per = 7 + hash(i, 1) * 6, u = ((t + hash(i, 2) * per) % per) / per;
    const ex = hash(i, 3) * W + Math.sin(t * 0.8 + i) * 18, ey = H + 10 - u * (H + 20);
    const sz = 8 + hash(i, 4) * 12;
    ctx.globalAlpha = ramp * Math.sin(u * Math.PI) * 0.4;
    ctx.drawImage(S.redGlow, ex - sz, ey - sz, sz * 2, sz * 2);
  }
  ctx.restore();
}

/** 快晴：右上的太阳（核心 + 缓转日光射线 + 柔光），取代原来的单个渐变 */
export function drawSun(ctx, W, H, sunX, t, ramp) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const pulse = 0.85 + 0.15 * Math.sin(t * 1.3);
  const R = Math.min(W, H) * 1.15;
  ctx.globalAlpha = ramp * 0.5 * pulse; ctx.drawImage(S.sun, sunX - R / 2, H * 0.14 - R / 2, R, R);
  ctx.save(); ctx.translate(sunX, H * 0.14); ctx.rotate(t * 0.05);
  const RR = Math.min(W, H) * 1.5;
  ctx.globalAlpha = ramp * 0.16 * pulse; ctx.drawImage(S.rays, -RR / 2, -RR / 2, RR, RR);
  ctx.restore();
  // 光尘：缓慢漂浮的暖色亮点
  for (let i = 0; i < 18; i++) {
    const per = 10 + hash(i, 1) * 8, u = ((t + hash(i, 2) * per) % per) / per;
    const px = hash(i, 3) * W + Math.sin(t * 0.5 + i) * 20, py = (hash(i, 4) * 0.8 + u * 0.3) * H;
    const sz = 6 + hash(i, 5) * 10;
    ctx.globalAlpha = ramp * Math.sin(u * Math.PI) * 0.35; ctx.drawImage(S.warm, px - sz, py - sz, sz * 2, sz * 2);
  }
  ctx.restore();
}

/** 晴岚：花瓣与嫩叶顺风斜飘，旋转摇摆 */
export function drawPetals(ctx, W, H, t, ramp) {
  ensure();
  ctx.save();
  for (let i = 0; i < 26; i++) {
    const per = 9 + hash(i, 1) * 7, u = ((t + hash(i, 2) * per) % per) / per;
    const x = (hash(i, 3) * 1.3 - 0.15 + u * 0.35) * W + Math.sin(t * 1.2 + i * 2) * 26;
    const y = -20 + u * (H + 40);
    const sz = 12 + hash(i, 4) * 12, sp = S.petal[i % 3];
    ctx.globalAlpha = ramp * Math.sin(u * Math.PI) * 0.8;
    ctx.save(); ctx.translate(x, y); ctx.rotate(t * (0.8 + hash(i, 5)) + i); ctx.scale(1, 0.55 + 0.45 * Math.sin(t * 2 + i));
    ctx.drawImage(sp, -sz / 2, -sz / 2, sz, sz);
    ctx.restore();
  }
  ctx.restore();
}

/** 烈日：地表热浪(上升的暖色光斑) + 底部橙色辉光带 */
export function drawHeat(ctx, W, H, t, ramp) {
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 16; i++) {
    const per = 6 + hash(i, 1) * 5, u = ((t + hash(i, 2) * per) % per) / per;
    const x = hash(i, 3) * W + Math.sin(t * 1.1 + i * 1.7) * 30, y = H * 0.95 - u * H * 0.5;
    const sz = 50 + hash(i, 4) * 70;
    ctx.globalAlpha = ramp * Math.sin(u * Math.PI) * 0.12; ctx.drawImage(S.warm, x - sz, y - sz * 0.6, sz * 2, sz * 1.2);
  }
  ctx.globalAlpha = ramp * (0.12 + 0.03 * Math.sin(t * 2)); ctx.drawImage(S.fire, -W * 0.2, H * 0.78, W * 1.4, H * 0.5);
  ctx.restore();
}

/** 雾雨：地面水纹 + 低空薄雾(两团横向漂移)。雨线本身仍由 renderer 里的批量 stroke 画 */
export function drawRainExtras(ctx, W, H, t, ramp) {
  ensure();
  ctx.save();
  for (let i = 0; i < 14; i++) {
    const per = 1.1 + hash(i, 1) * 0.9, u = ((t + hash(i, 2) * per) % per) / per;
    const cyc = Math.floor((t + hash(i, 2) * per) / per);
    const x = hash(i + cyc * 13, 3) * W, y = H * (0.3 + hash(i + cyc * 7, 4) * 0.65);
    const sz = 14 + u * 44;
    ctx.globalAlpha = ramp * (1 - u) * 0.5; ctx.drawImage(S.ripple, x - sz, y - sz * 0.5, sz * 2, sz);
  }
  for (const [yy, spd, a] of [[H * 0.72, 22, 0.5], [H * 0.86, -16, 0.4]]) {
    const off = ((t * spd) % W + W) % W;
    ctx.globalAlpha = ramp * a * 0.5;
    for (const x0 of [off - W, off]) ctx.drawImage(S.mist, x0, yy, W, H * 0.3);
  }
  ctx.restore();
}

// =================================================================
// 台风（第一百九十九轮重画，用户："龙卷风川雾啥的……只要你能做的更好看就动"）
// =================================================================
// 保留你第一百一十轮定下的规格：3~4 层叠加、内圈快外圈慢、青灰白、外缘柔和渐隐、风暴眼、吸入粒子线段。
// 提升体积感：①锥形渐隐的螺旋臂（预烘焙软边精灵，3 层，内快外慢）②被卷起的落叶/尘团由外向内螺旋收拢
// ③风暴眼 = 深色核 + 细亮环 ④地面风暴阴影 ⑤内部偶发闪光。只改渲染；半径/推力/吸力等玩法数值一律不动。
let TY = null;
function ensureTy() {
  if (TY) return TY;
  const soft = (size, stops) => radial(size, stops);
  // 螺旋臂：沿对数螺旋盖一串软边圆章，宽度中段最大、两头收尖，透明度由根部到梢部渐隐
  const arm = (seed, sweep, turns) => {
    const N = 256, c = mk(N, N), g = c.getContext('2d');
    const stamp = soft(32, [[0, 'rgba(255,255,255,0.9)'], [0.5, 'rgba(255,255,255,0.35)'], [1, 'rgba(255,255,255,0)']]);
    const K = 70;
    for (let k = 0; k < K; k++) {
      const u = k / (K - 1);
      const ang = u * sweep;
      const rr = 128 * (0.18 + 0.78 * Math.pow(u, 0.85));
      const w = 9 + 24 * Math.sin(Math.PI * Math.min(1, u * 0.92 + 0.04)) * (0.8 + 0.2 * hash(k, seed));
      g.globalAlpha = (0.20 + 0.55 * Math.sin(Math.PI * Math.min(1, u * 0.9 + 0.05))) * (1 - 0.35 * u);
      g.drawImage(stamp, 128 + Math.cos(ang) * rr - w, 128 + Math.sin(ang) * rr - w, w * 2, w * 2);
      void turns;
    }
    return c;
  };
  TY = {
    arms: [arm(1, 1.9, 1), arm(2, 2.2, 1), arm(3, 1.6, 1)],
    shadow: soft(128, [[0, 'rgba(14,22,40,0.55)'], [0.55, 'rgba(14,22,40,0.28)'], [1, 'rgba(14,22,40,0)']]),
    puff: soft(64, [[0, 'rgba(235,244,252,0.8)'], [0.45, 'rgba(220,234,246,0.38)'], [1, 'rgba(210,226,242,0)']]),
    eye: soft(128, [[0, 'rgba(6,10,20,0.85)'], [0.6, 'rgba(14,20,36,0.4)'], [1, 'rgba(14,20,36,0)']]),
    flash: soft(96, [[0, 'rgba(255,255,255,0.9)'], [0.3, 'rgba(190,225,255,0.5)'], [1, 'rgba(160,200,255,0)']]),
    leaf: ['#9db86a', '#c9a45a', '#8fae7c'].map((col) => {
      const c = mk(20, 20), g = c.getContext('2d'); g.translate(10, 10);
      g.fillStyle = col; g.beginPath(); g.moveTo(-8, 0); g.quadraticCurveTo(0, -6, 8, 0); g.quadraticCurveTo(0, 6, -8, 0); g.fill();
      g.strokeStyle = 'rgba(255,255,255,0.45)'; g.lineWidth = 0.9; g.beginPath(); g.moveTo(-7, 0); g.lineTo(7, 0); g.stroke();
      return c;
    }),
  };
  return TY;
}

/** 一个台风：tp = {x,y,r,spin,parts}；ctx 已处于世界坐标（本函数内部 translate）；t 为天气视觉时间；ramp 为渐入渐出 */
export function drawTyphoon(ctx, tp, t, ramp) {
  ensure(); ensureTy();
  const R = tp.r;
  ctx.save();
  ctx.translate(tp.x, tp.y);
  // 1) 地面风暴阴影（让它"压"在地上，而不是浮在上面）
  ctx.globalAlpha = 0.55 * ramp;
  ctx.drawImage(TY.shadow, -R * 1.35, -R * 1.35, R * 2.7, R * 2.7);
  // 2) 外缘柔和的风环（沿用旧版"外圈柔和渐变"的意图）
  ctx.globalAlpha = 0.5 * ramp;
  ctx.drawImage(S.mist, -R * 1.3, -R * 1.3, R * 2.6, R * 2.6);
  // 3) 螺旋臂 3 层：[半径比, 角速度, 臂数, 透明度, 精灵序号] —— 内圈快而密、外圈慢而疏（1/110 轮规格）
  const LAY = [[0.62, 4.0, 5, 0.50, 0], [0.98, 2.4, 4, 0.36, 1], [1.34, 1.3, 3, 0.22, 2]];
  for (const [rm, spd, nArms, al, si] of LAY) {
    const rot = t * spd * tp.spin + rm * 5.1, size = R * rm * 2;
    ctx.globalAlpha = al * ramp;
    for (let i = 0; i < nArms; i++) {
      ctx.save();
      ctx.rotate(rot + (i * Math.PI * 2) / nArms);
      ctx.drawImage(TY.arms[si], -size / 2, -size / 2, size, size);
      ctx.restore();
    }
  }
  // 4) 被卷起的落叶 / 尘团：由外向内螺旋收拢，近中心更快；两端淡入淡出
  const NP = 16;
  for (let i = 0; i < NP; i++) {
    const u = (hash(i, 1) + t * (0.05 + 0.03 * hash(i, 2))) % 1;
    const rr = R * (1.2 - 1.0 * u);
    const a = hash(i, 3) * Math.PI * 2 + t * tp.spin * (1.0 + 2.6 * u * u) * (0.8 + 0.4 * hash(i, 4));
    const x = Math.cos(a) * rr, y = Math.sin(a) * rr * 0.92;
    const fade = Math.sin(Math.PI * u);
    if (i % 3 === 0) {
      const s = R * (0.16 + 0.1 * hash(i, 5));
      ctx.globalAlpha = 0.5 * fade * ramp; ctx.drawImage(TY.puff, x - s, y - s, s * 2, s * 2);
    } else {
      const s = 9 + 6 * hash(i, 6);
      ctx.save(); ctx.translate(x, y); ctx.rotate(t * (2 + 3 * hash(i, 7)) + i); ctx.scale(1, 0.5 + 0.5 * Math.abs(Math.sin(t * 3 + i)));
      ctx.globalAlpha = 0.85 * fade * ramp; ctx.drawImage(TY.leaf[i % 3], -s / 2, -s / 2, s, s);
      ctx.restore();
    }
  }
  // 5) 风暴眼：深色核 + 细亮环（呼吸）
  const ey = R * 0.5;
  ctx.globalAlpha = 0.75 * ramp; ctx.drawImage(TY.eye, -ey, -ey, ey * 2, ey * 2);
  ctx.strokeStyle = '#d6e6f4'; ctx.lineWidth = 1.6;
  for (let i = 0; i < 2; i++) {
    const rr = R * (0.2 + 0.1 * i) + Math.sin(t * 4.6 * tp.spin - i * 1.8) * R * 0.03;
    ctx.globalAlpha = (0.2 - i * 0.07) * ramp;
    ctx.beginPath(); ctx.arc(0, 0, rr, 0, Math.PI * 2); ctx.stroke();
  }
  // 6) 内部偶发闪光（每台风每 ~4.3s 一次，持续 0.18s，位置由时间槽哈希决定）
  const slot = Math.floor(t / 4.3), ph = t - slot * 4.3;
  if (ph < 0.18) {
    const fx = (hash(slot + tp.x * 0.01, 11) - 0.5) * R * 1.2, fy = (hash(slot + tp.x * 0.01, 12) - 0.5) * R * 1.2;
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = (1 - ph / 0.18) * 0.7 * ramp; ctx.drawImage(TY.flash, fx - R * 0.35, fy - R * 0.35, R * 0.7, R * 0.7);
    ctx.restore();
  }
  // 7) 吸入粒子线段（沿用游戏状态里的 tp.parts：越近中心越快越亮）
  ctx.strokeStyle = '#e6f2fa'; ctx.lineCap = 'round';
  for (const p of (tp.parts ?? [])) {
    const k = 1 - p.d / (R * 1.75);
    ctx.globalAlpha = Math.min(0.75, 0.16 + k * 0.55) * ramp;
    ctx.lineWidth = p.s * (0.7 + k * 0.8);
    const pa = p.a - p.w * 0.05;
    ctx.beginPath(); ctx.moveTo(Math.cos(pa) * p.d, Math.sin(pa) * p.d); ctx.lineTo(Math.cos(p.a) * p.d, Math.sin(p.a) * p.d); ctx.stroke();
  }
  ctx.restore();
}

/** 浓雾里流动的雾絮：8 团柔边椭圆，绕玩家缓慢漂移并随时间拉伸（取代旧的 8 个硬边白圆盘）。ctx 为世界坐标 */
export function drawFogWisps(ctx, px, py, t, a) {
  ensure();
  ctx.save();
  for (let i = 0; i < 8; i++) {
    const fx = px + Math.cos(t * 0.35 + i * 2.3) * (240 + (i % 3) * 60);
    const fy = py + Math.sin(t * 0.28 + i * 1.7) * (200 + (i % 4) * 50);
    const stretch = 1.5 + 0.5 * Math.sin(t * 0.4 + i), w = 150 + (i % 3) * 40, h = w / stretch;
    ctx.globalAlpha = (0.20 + 0.06 * Math.sin(t * 0.6 + i * 1.3)) * a;
    ctx.translate(fx, fy); ctx.rotate(0.5 * Math.sin(t * 0.2 + i));
    ctx.drawImage(S.mist, -w / 2, -h / 2, w, h);
    ctx.rotate(-0.5 * Math.sin(t * 0.2 + i)); ctx.translate(-fx, -fy);
  }
  ctx.restore();
}