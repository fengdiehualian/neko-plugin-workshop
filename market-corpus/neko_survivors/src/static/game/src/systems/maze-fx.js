// 冰晶迷宫渲染（第一百九十八轮，用户游玩反馈第 4 条）
//
// 整座迷宫（广场冰砖地面 + 通道地面 + 冰墙 + 墙影）在首次绘制时烘焙到【一张】离屏图，之后每帧只一次 drawImage；
// 入口/出口两盏灯笼用预烘焙的柔光精灵做轻微呼吸。零帧内渐变创建、零 Math.random（装饰随机全部由坐标哈希推出，暂停时冻结）。
//
// 画法要点（顶视角、冰砖质感）：
//   · 广场：64px 冰砖，逐块亮度微差 + 细缝线，外缘一圈深色描边 + 内侧亮边（和游戏里障碍统一的"描边墙"语言）
//   · 通道：比广场略深一档的蓝灰，让人一眼看出哪里是路
//   · 冰墙：先给整个墙并集描一圈深色外框（宽 4，只露出外半边 → 并集轮廓，拐角处不出现重叠接缝），
//           再填浅冰色，再叠内缩的更亮高光，最后每段墙几条斜向冰裂/高光线
import { makeCanvas } from './no-dom.js';

const TAU = Math.PI * 2;
const SC = 1.25;   // 烘焙分辨率倍数（1013px 广场 → ~1266px 画布，≈6.4MB；比 1.0 在高 DPI 屏上清晰得多）

function hash(i, s = 0) {
  const x = Math.sin(i * 127.1 + s * 311.7) * 43758.5453123;
  return x - Math.floor(x);
}

let CACHE = null;   // { key, canvas, w, h }
let GLOW = null;

function glowSprite() {
  if (GLOW) return GLOW;
  const c = makeCanvas(); c.width = c.height = 96;
  const g = c.getContext('2d');
  const gr = g.createRadialGradient(48, 48, 0, 48, 48, 48);
  gr.addColorStop(0, 'rgba(255,255,255,0.95)'); gr.addColorStop(0.18, 'rgba(255,255,255,0.7)');
  gr.addColorStop(0.5, 'rgba(255,255,255,0.18)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 96, 96);
  return (GLOW = c);
}

function bake(t) {
  const { maze, plaza } = t;
  const key = `${maze.x0}|${maze.y0}|${maze.size}|${maze.walls.length}|${plaza.x0}|${plaza.y0}|${plaza.x1}|${plaza.y1}`;
  if (CACHE && CACHE.key === key) return CACHE;
  const w = plaza.x1 - plaza.x0, h = plaza.y1 - plaza.y0;
  const cv = makeCanvas();
  cv.width = Math.ceil(w * SC); cv.height = Math.ceil(h * SC);
  const g = cv.getContext('2d');
  g.scale(SC, SC);
  g.translate(-plaza.x0, -plaza.y0);   // 之后全部用世界坐标作画

  // ---- 1) 广场冰砖 ----
  const SL = 64;
  const gx0 = Math.floor(plaza.x0 / SL) * SL, gy0 = Math.floor(plaza.y0 / SL) * SL;
  g.save();
  g.beginPath(); g.roundRect?.(plaza.x0, plaza.y0, w, h, 26); if (!g.roundRect) g.rect(plaza.x0, plaza.y0, w, h); g.clip();
  g.fillStyle = '#9db8cc'; g.fillRect(plaza.x0, plaza.y0, w, h);
  for (let y = gy0; y < plaza.y1; y += SL) for (let x = gx0; x < plaza.x1; x += SL) {
    const k = hash(x * 0.013 + y * 0.029);
    const l = 150 + Math.floor(k * 22);
    g.fillStyle = `rgb(${l - 8},${l + 10},${l + 28})`;
    g.fillRect(x + 1, y + 1, SL - 2, SL - 2);
    if (k > 0.82) { g.fillStyle = 'rgba(255,255,255,0.10)'; g.fillRect(x + 1, y + 1, SL - 2, (SL - 2) / 2); }   // 偶尔一块更亮的砖
  }
  g.strokeStyle = 'rgba(46,74,104,0.38)'; g.lineWidth = 1.4;
  g.beginPath();
  for (let x = gx0; x <= plaza.x1; x += SL) { g.moveTo(x, plaza.y0); g.lineTo(x, plaza.y1); }
  for (let y = gy0; y <= plaza.y1; y += SL) { g.moveTo(plaza.x0, y); g.lineTo(plaza.x1, y); }
  g.stroke();
  // 通道地面：迷宫内部比广场深一档，路一眼可读
  g.fillStyle = 'rgba(40,78,118,0.30)';
  g.fillRect(maze.x0 + maze.wall, maze.y0 + maze.wall, maze.size - maze.wall * 2, maze.size - maze.wall * 2);
  // 每个格子中心一点浅色菱形纹（"脚印"般的引路暗纹），装饰随机由坐标哈希决定
  for (let r = 0; r < maze.n; r++) for (let c = 0; c < maze.n; c++) {
    const p = maze.cellCenter(r, c), k = hash(r * 9.1 + c * 3.7, 2);
    g.fillStyle = `rgba(210,236,252,${0.10 + k * 0.10})`;
    g.beginPath(); g.moveTo(p.x, p.y - 12); g.lineTo(p.x + 9, p.y); g.lineTo(p.x, p.y + 12); g.lineTo(p.x - 9, p.y); g.closePath(); g.fill();
  }
  g.restore();
  // 广场外框：深色描边 + 内侧亮边
  g.lineJoin = 'round';
  g.strokeStyle = 'rgba(30,50,80,0.9)'; g.lineWidth = 5;
  g.beginPath(); g.roundRect?.(plaza.x0 + 1, plaza.y0 + 1, w - 2, h - 2, 26); if (!g.roundRect) g.rect(plaza.x0 + 1, plaza.y0 + 1, w - 2, h - 2); g.stroke();
  g.strokeStyle = 'rgba(220,240,255,0.55)'; g.lineWidth = 1.6;
  g.beginPath(); g.roundRect?.(plaza.x0 + 6, plaza.y0 + 6, w - 12, h - 12, 22); if (!g.roundRect) g.rect(plaza.x0 + 6, plaza.y0 + 6, w - 12, h - 12); g.stroke();

  // ---- 2) 冰墙 ----
  const W = maze.walls;
  // 墙影（偏向右下，柔）
  g.save();
  g.shadowColor = 'rgba(14,30,58,0.55)'; g.shadowBlur = 12; g.shadowOffsetX = 6; g.shadowOffsetY = 9;
  g.fillStyle = '#6f93b3';
  for (const r of W) g.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  g.restore();
  // 并集外框（宽 4 的描边，被随后的填充盖住内半边 → 只剩外轮廓，拐角无接缝）
  g.strokeStyle = '#2b4a6c'; g.lineWidth = 4;
  for (const r of W) g.strokeRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  // 冰体
  g.fillStyle = '#b4d6ee';
  for (const r of W) g.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  // 内缩高光（顶面更亮）
  g.fillStyle = '#d6eefb';
  for (const r of W) { const ins = 3; if (r.x1 - r.x0 > ins * 2 + 2 && r.y1 - r.y0 > ins * 2 + 2) g.fillRect(r.x0 + ins, r.y0 + ins, r.x1 - r.x0 - ins * 2, r.y1 - r.y0 - ins * 2); }
  // 中脊亮线 + 冰裂（每段墙沿长边一条脊线，2~3 条短斜裂）
  W.forEach((r, i) => {
    const horiz = (r.x1 - r.x0) >= (r.y1 - r.y0);
    g.strokeStyle = 'rgba(255,255,255,0.75)'; g.lineWidth = 1.6;
    g.beginPath();
    if (horiz) { g.moveTo(r.x0 + 6, (r.y0 + r.y1) / 2 - 2); g.lineTo(r.x1 - 6, (r.y0 + r.y1) / 2 - 2); }
    else { g.moveTo((r.x0 + r.x1) / 2 - 2, r.y0 + 6); g.lineTo((r.x0 + r.x1) / 2 - 2, r.y1 - 6); }
    g.stroke();
    g.strokeStyle = 'rgba(70,112,150,0.55)'; g.lineWidth = 1.2;
    const long = horiz ? r.x1 - r.x0 : r.y1 - r.y0;
    const cracks = 2 + (hash(i, 5) > 0.5 ? 1 : 0);
    g.beginPath();
    for (let k = 0; k < cracks; k++) {
      const u = 0.18 + (k + hash(i * 3 + k, 6) * 0.6) / cracks * 0.64;
      if (horiz) { const x = r.x0 + long * u; g.moveTo(x, r.y0 + 6); g.lineTo(x + 7, r.y1 - 6); }
      else { const y = r.y0 + long * u; g.moveTo(r.x0 + 6, y); g.lineTo(r.x1 - 6, y + 7); }
    }
    g.stroke();
  });
  // 墙顶冰晶闪点（四角星）
  g.fillStyle = 'rgba(255,255,255,0.9)';
  W.forEach((r, i) => {
    if (hash(i, 8) < 0.55) return;
    const x = r.x0 + (r.x1 - r.x0) * (0.2 + hash(i, 9) * 0.6), y = r.y0 + (r.y1 - r.y0) * (0.2 + hash(i, 10) * 0.6);
    g.beginPath(); g.moveTo(x, y - 4); g.lineTo(x + 1.2, y - 1.2); g.lineTo(x + 4, y); g.lineTo(x + 1.2, y + 1.2); g.lineTo(x, y + 4); g.lineTo(x - 1.2, y + 1.2); g.lineTo(x - 4, y); g.lineTo(x - 1.2, y - 1.2); g.closePath(); g.fill();
  });

  CACHE = { key, canvas: cv, w, h };
  return CACHE;
}

/** 预烘焙（换到雪原图时调用，避免第一帧现场烘焙造成卡顿） */
export function warm(t) {
  if (t?.maze && t?.plaza) bake(t);
  glowSprite();
}

/** 每帧：一次 drawImage + 两盏灯笼呼吸。t 为 world.terrain（含 maze / plaza），ctx 为屏幕空间（identity） */
export function drawMaze(ctx, camera, W, H, t, now) {
  if (!t?.maze || !t?.plaza) return;
  const b = bake(t);
  const sx = t.plaza.x0 - camera.cx + W / 2, sy = t.plaza.y0 - camera.cy + H / 2;
  if (sx > W || sy > H || sx + b.w < 0 || sy + b.h < 0) return;   // 完全在屏外
  ctx.drawImage(b.canvas, 0, 0, b.canvas.width, b.canvas.height, sx, sy, b.w, b.h);
  const gl = glowSprite();
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const [pt, col] of [[t.maze.entrance, '#7dffb0'], [t.maze.exit, '#ffd070']]) {
    const x = pt.x - camera.cx + W / 2, y = pt.y - camera.cy + H / 2;
    if (x < -120 || x > W + 120 || y < -120 || y > H + 120) continue;
    const pulse = 0.75 + 0.25 * Math.sin(now * 2.4 + pt.x * 0.01);
    ctx.globalAlpha = 0.55 * pulse;
    // 着色：先用精灵画白光，再叠一层同位置的有色小光斑（避免每帧改精灵像素）
    ctx.drawImage(gl, x - 52, y - 52, 104, 104);
    ctx.globalAlpha = 0.5 * pulse;
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(x, y, 9 + 2 * pulse, 0, TAU); ctx.fill();
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = '#ffffff';
    ctx.beginPath(); ctx.arc(x, y, 3.2, 0, TAU); ctx.fill();
  }
  ctx.restore();
}

export const _internals = { SC };
