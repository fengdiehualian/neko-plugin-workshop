// 维吉尔战特效 v2（第一百八十五轮 · 全局美化）
//
// 用户："芙兰维吉尔 boss 战背景美化，目前除了一张图之后剩下的地方就是纯黑或者深红，你有好的想法让它更好看就做"
//      "维吉尔技能特效优化更好看一些"
//
// 思路：维吉尔 = 幻影剑 / 次元斩 / 青色。场地外那片"纯黑"改造成一片**虚空**：
//   · 大量竖直漂浮的幽灵剑(细长剑身 + 护手 + 青色辉光，缓慢上升并淡入淡出)——呼应他的"幻影剑"招式；
//   · 青色光尘上升；两层缓慢横向漂移的冷雾；
//   · 场地边框改成脉动的青色辉光线 + 四角括号，"墙在哪"一眼可见；
// 刀光(月牙/横斩)的形状是多轮打磨过的，不动；这里只在它们**下面垫一层**加法辉光和拖影。
//
// 全部预烘焙，帧内零渐变创建；位置由确定性公式给出(无状态、不占内存)。
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

let S = null;
function ensure() {
  if (S) return;
  // 青色柔光
  const glow = mk(128, 128);
  {
    const g = glow.getContext('2d');
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, 'rgba(235,255,255,0.95)'); gr.addColorStop(0.18, 'rgba(120,235,255,0.7)');
    gr.addColorStop(0.5, 'rgba(60,170,230,0.24)'); gr.addColorStop(1, 'rgba(40,120,200,0)');
    g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  }
  // 幽灵剑：竖直，剑尖朝上。64×256
  const sword = mk(64, 256);
  {
    const g = sword.getContext('2d');
    g.translate(32, 0);
    // 外辉光（宽、淡）
    const halo = g.createLinearGradient(-30, 0, 30, 0);
    halo.addColorStop(0, 'rgba(80,200,255,0)'); halo.addColorStop(0.5, 'rgba(120,230,255,0.22)'); halo.addColorStop(1, 'rgba(80,200,255,0)');
    g.fillStyle = halo; g.fillRect(-30, 6, 60, 240);
    // 剑身：尖头 → 宽 → 护手
    const blade = g.createLinearGradient(0, 0, 0, 200);
    blade.addColorStop(0, 'rgba(230,255,255,0.95)'); blade.addColorStop(0.35, 'rgba(130,225,255,0.8)'); blade.addColorStop(1, 'rgba(60,150,220,0.55)');
    g.fillStyle = blade;
    g.beginPath(); g.moveTo(0, 4); g.lineTo(7, 40); g.lineTo(6, 198); g.lineTo(-6, 198); g.lineTo(-7, 40); g.closePath(); g.fill();
    g.fillStyle = 'rgba(255,255,255,0.7)';           // 中脊
    g.fillRect(-0.9, 10, 1.8, 186);
    // 护手 + 柄
    g.fillStyle = 'rgba(110,210,255,0.85)';
    g.fillRect(-17, 198, 34, 5);
    g.fillRect(-3.5, 203, 7, 38);
    g.fillStyle = 'rgba(200,245,255,0.9)';
    g.beginPath(); g.arc(0, 246, 5, 0, TAU); g.fill();
  }
  // 冷雾（可横向无缝平铺）
  const fog = mk(1024, 220);
  {
    const g = fog.getContext('2d');
    for (let i = 0; i < 12; i++) {
      const x = hash(i, 1) * 1024, y = 40 + hash(i, 2) * 140, r = 90 + hash(i, 3) * 110;
      for (const dx of [-1024, 0, 1024]) {
        const gr = g.createRadialGradient(x + dx, y, 0, x + dx, y, r);
        gr.addColorStop(0, 'rgba(90,170,220,0.16)'); gr.addColorStop(0.55, 'rgba(60,120,190,0.06)'); gr.addColorStop(1, 'rgba(40,90,160,0)');
        g.fillStyle = gr; g.fillRect(x + dx - r, y - r, r * 2, r * 2);
      }
    }
  }
  S = { glow, sword, fog };
}

/** 预热（preload 时调用，避免第一帧现场烘焙） */
export function warm() { ensure(); }

/**
 * 场地外的虚空：叠在已有暗幕之上，剪裁在"场地外"区域。
 * @param a  0~1 总强度（入场演出淡入）
 */
export function drawVoid(ctx, camera, arena, t, a) {
  if (a <= 0.01) return;
  ensure();
  const { cx, cy, hw, hh } = arena;
  const vw = camera.viewW ?? 1600, vh = camera.viewH ?? 900;
  const L = camera.cx - vw / 2 - 8, T = camera.cy - vh / 2 - 8;
  ctx.save();
  // 只画在场地外（evenodd 挖洞）
  ctx.beginPath();
  ctx.rect(L, T, vw + 16, vh + 16);
  ctx.rect(cx - hw, cy - hh, hw * 2, hh * 2);
  ctx.clip('evenodd');
  ctx.imageSmoothingEnabled = true;

  // ① 冷雾：两层反向漂移，贴着屏幕上下
  for (const [yy, spd, al, sc] of [[T + vh * 0.12, 9, 0.8, 1.3], [T + vh * 0.62, -14, 0.65, 1.5]]) {
    ctx.globalAlpha = a * al;
    const fw = 1024 * sc;
    let off = ((t * spd) % fw); if (off > 0) off -= fw;
    for (let x = L + off - fw; x < L + vw + fw; x += fw) ctx.drawImage(S.fog, x, yy, fw, 220 * sc);
  }

  ctx.globalCompositeOperation = 'lighter';
  // ② 幽灵剑：28 把，随机分布在屏幕周围，缓慢上升，每把有自己的周期/大小/透明度
  for (let i = 0; i < 28; i++) {
    const period = 16 + hash(i, 4) * 16, ph = hash(i, 5) * period;
    const u = ((t + ph) % period) / period;                         // 0→1 一个生命周期
    const bx = L + hash(i, 6) * (vw + 16);
    const sz = 0.55 + hash(i, 7) * 0.95;                           // 缩放
    const h = 256 * sz, w = 64 * sz;
    const y = T + vh + h * 0.2 - u * (vh + h * 1.3);               // 自下而上
    const fade = Math.sin(u * Math.PI);                            // 两头淡
    ctx.globalAlpha = a * fade * (0.12 + 0.2 * hash(i, 8)) * (0.85 + 0.15 * Math.sin(t * 1.6 + i));
    const sway = Math.sin(t * 0.4 + i) * 8;
    ctx.drawImage(S.sword, bx + sway - w / 2, y, w, h);
  }

  // ③ 青色光尘：70 颗，缓慢上升并左右摇摆
  for (let i = 0; i < 70; i++) {
    const period = 9 + hash(i, 9) * 9, ph = hash(i, 10) * period;
    const u = ((t + ph) % period) / period;
    const px = L + hash(i, 11) * (vw + 16) + Math.sin(t * 0.7 + i * 1.7) * 14;
    const py = T + vh - u * (vh + 30);
    const sz = 6 + hash(i, 12) * 16;
    ctx.globalAlpha = a * Math.sin(u * Math.PI) * (0.25 + 0.35 * hash(i, 13));
    ctx.drawImage(S.glow, px - sz, py - sz, sz * 2, sz * 2);
  }
  ctx.restore();
}

/** 场地边框：脉动辉光线 + 四角括号（取代原来 0.28 透明度的细线） */
export function drawArenaFrame(ctx, arena, t, a) {
  if (a <= 0.01) return;
  const { cx, cy, hw, hh } = arena;
  const x0 = cx - hw, y0 = cy - hh, x1 = cx + hw, y1 = cy + hh;
  const pulse = 0.55 + 0.25 * Math.sin(t * 1.8);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  ctx.globalAlpha = a * pulse * 0.45;
  ctx.strokeStyle = 'rgba(70,200,255,1)'; ctx.lineWidth = 7;
  ctx.strokeRect(x0, y0, hw * 2, hh * 2);
  ctx.globalAlpha = a * (0.55 + 0.25 * pulse);
  ctx.strokeStyle = 'rgba(190,245,255,1)'; ctx.lineWidth = 1.6;
  ctx.strokeRect(x0, y0, hw * 2, hh * 2);
  // 四角 L 形括号
  const B = 54;
  ctx.globalAlpha = a * 0.95; ctx.strokeStyle = '#e9fdff'; ctx.lineWidth = 3.2;
  for (const [x, y, sx, sy] of [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]]) {
    ctx.beginPath(); ctx.moveTo(x, y + sy * B); ctx.lineTo(x, y); ctx.lineTo(x + sx * B, y); ctx.stroke();
    ctx.globalAlpha = a * 0.5; ctx.fillStyle = '#9ff0ff';
    ctx.beginPath(); ctx.arc(x, y, 5, 0, TAU); ctx.fill();
    ctx.globalAlpha = a * 0.95;
  }
  ctx.restore();
}

/**
 * 月牙刀光垫底辉光：一团柔光 + 沿反方向拖出的长光带。
 * (x,y) 为刀光中心，angle 为飞行方向，r 为刀光半径，k 为生命进度(0~1，越大越亮)
 */
export function drawBladeGlow(ctx, x, y, angle, r, k) {
  if (k <= 0.02) return;
  ensure();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const d = r * 5.2;
  ctx.globalAlpha = Math.min(1, k) * 0.55;
  ctx.drawImage(S.glow, x - d / 2, y - d / 2, d, d);
  // 拖影：沿 -angle 方向 3 团渐小渐淡的光
  const ca = Math.cos(angle), sa = Math.sin(angle);
  for (let i = 1; i <= 3; i++) {
    const dd = r * 1.5 * i, sz = d * (0.7 - i * 0.14);
    ctx.globalAlpha = Math.min(1, k) * (0.34 - i * 0.08);
    ctx.drawImage(S.glow, x - ca * dd - sz / 2, y - sa * dd - sz / 2, sz, sz);
  }
  ctx.restore();
}
