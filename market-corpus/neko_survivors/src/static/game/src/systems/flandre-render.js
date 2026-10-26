// 芙兰战渲染 v2 · 版本 A：通用部件（第一百八十五轮 · 全局美化第 2 步）
//
// 用户："不是追求还原芙兰战而是做出好玩的芙兰战……现在做的 3 重魔法阵也还行，有更好的效果就做……
//       背景目前除了一张图之后剩下的地方就是纯黑或者深红，有好的想法让它更好看就做。"
//
// 保留：既有的紫/黄魔法阵贴图、eff07 旋涡、粉白雾球（在 renderer.js 里原样不动）。
// 新增（本模块，全部预烘焙，帧内零 createRadialGradient / shadowBlur）：
//   drawBg      屏幕空间 —— 水晶翅膀剪影（芙兰的标志，横跨全屏，墙外也有）+ 漂移红雾 + 上升余烬 + 暗角
//   drawSigil   世界空间 —— 每张符卡专属的星形法阵（{n/k} 星多边形 + 符文环），叠在原有两层阵外面
//   drawDeclare 世界空间 —— 符卡宣言：核心闪光 + 双层冲击波环 + 放射光芒（主题色）
//   drawHud     屏幕空间 —— 符卡名铭牌（滑入，分类 + 标题）、消弹冷却进度条、EP 倒计时、宣言白闪
import { makeCanvas } from './no-dom.js';
const TAU = Math.PI * 2;

// ---------------------------------------------------------------
// 每张符卡的主题：颜色（取自视觉参数书 §3 色板）+ 法阵星形 {n/k}
// ---------------------------------------------------------------
export const CARD_THEME = {
  prologue:    { color: '#6a7cff', n: 7,  k: 2 },
  cranberry:   { color: '#E050F0', n: 5,  k: 2 },
  laevateinn:  { color: '#FF3040', n: 6,  k: 2 },
  fourkind:    { color: '#5040F0', n: 8,  k: 3 },
  kagome:      { color: '#30E080', n: 6,  k: 2 },
  maze:        { color: '#60E8E8', n: 7,  k: 3 },
  starbow:     { color: '#FFE060', n: 9,  k: 4 },
  catadioptric:{ color: '#4a64f0', n: 5,  k: 2 },
  clock:       { color: '#A8C8FF', n: 12, k: 5 },
  ripples:     { color: '#5a6cf0', n: 10, k: 3 },
  fragile:     { color: '#FF80C0', n: 7,  k: 2 },
  bloodstain:  { color: '#FF2030', n: 5,  k: 2 },
  heart:       { color: '#D060F0', n: 9,  k: 2 },
};
const DEFAULT_THEME = { color: '#ff4060', n: 5, k: 2 };

function mk(w, h) {
  const c = makeCanvas();
  c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h));
  return c;
}
function rgb(hex) {
  const n = parseInt(String(hex).replace('#', '').slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const rgba = (hex, a) => { const c = rgb(hex); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; };
const ease = (u) => 1 - (1 - u) * (1 - u) * (1 - u);
function seeded(seed) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }

// ---------------------------------------------------------------
// 预烘焙精灵
// ---------------------------------------------------------------
const CACHE = { glow: new Map(), sigil: new Map(), banner: new Map() };
let WING = null;      // { canvas, crystals:[{x,y,r,color}], w, h }
let FOG = null;
let RAYS = null;
let VIG = null, vigKey = '';

/** 径向柔光（白热中心 → 主题色 → 透明） */
function glowOf(hex) {
  let s = CACHE.glow.get(hex);
  if (s) return s;
  s = mk(128, 128);
  const g = s.getContext('2d');
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, 'rgba(255,255,255,0.95)');
  gr.addColorStop(0.16, rgba(hex, 0.9));
  gr.addColorStop(0.45, rgba(hex, 0.38));
  gr.addColorStop(1, rgba(hex, 0));
  g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  CACHE.glow.set(hex, s);
  return s;
}

/**
 * 星形法阵 {n/k}：外环 + 24 段符文 + 星多边形（含 gcd>1 的复合星）+ 顶点小环 + 内圈小星。
 * 先画宽的低 α 辉线，再画细亮线 —— 烘焙阶段用 shadowBlur 无妨（只烘一次）。
 */
function sigilOf(n, k, hex) {
  const key = `${n}/${k}|${hex}`;
  let s = CACHE.sigil.get(key);
  if (s) return s;
  const S = 512, C = S / 2;
  s = mk(S, S);
  const g = s.getContext('2d');
  g.translate(C, C);
  g.lineCap = 'round'; g.lineJoin = 'round';
  const col = hex, hot = '#ffffff';
  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  const star = (R, rot) => {
    const comps = gcd(n, k), per = n / comps;
    g.beginPath();
    for (let c = 0; c < comps; c++) {
      for (let j = 0; j <= per; j++) {
        const idx = (c + j * k) % n, a = rot - Math.PI / 2 + (idx / n) * TAU;
        g[j ? 'lineTo' : 'moveTo'](Math.cos(a) * R, Math.sin(a) * R);
      }
    }
  };
  const strokeBoth = (w) => {
    g.shadowColor = col; g.shadowBlur = 14; g.strokeStyle = rgba(col, 0.55); g.lineWidth = w * 3; g.stroke();
    g.shadowBlur = 0; g.strokeStyle = rgba(col, 0.95); g.lineWidth = w * 1.4; g.stroke();
    g.strokeStyle = 'rgba(255,255,255,0.9)'; g.lineWidth = Math.max(0.8, w * 0.45); g.stroke();
  };
  // 双层外环
  g.beginPath(); g.arc(0, 0, 244, 0, TAU); strokeBoth(2);
  g.beginPath(); g.arc(0, 0, 214, 0, TAU); strokeBoth(1.2);
  // 24 段符文：长短交替的刻线 + 每段中点一个小菱形（"字符"）
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * TAU, long = i % 2 === 0;
    const r0 = 216, r1 = long ? 242 : 232;
    g.beginPath(); g.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); g.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
    g.strokeStyle = rgba(col, 0.9); g.lineWidth = long ? 2.4 : 1.4; g.stroke();
    const am = a + TAU / 48, rm = 228;
    g.save(); g.translate(Math.cos(am) * rm, Math.sin(am) * rm); g.rotate(am);
    g.beginPath(); g.moveTo(-5, 0); g.lineTo(0, -3.2); g.lineTo(5, 0); g.lineTo(0, 3.2); g.closePath();
    g.fillStyle = rgba(col, 0.85); g.fill(); g.restore();
  }
  // 主星 + 顶点小环
  star(204, 0); strokeBoth(2.2);
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (i / n) * TAU;
    g.beginPath(); g.arc(Math.cos(a) * 204, Math.sin(a) * 204, 11, 0, TAU); strokeBoth(1.1);
  }
  // 内圈：小星（反向）+ 圈
  g.beginPath(); g.arc(0, 0, 98, 0, TAU); strokeBoth(1.4);
  star(92, Math.PI / n); strokeBoth(1.4);
  g.beginPath(); g.arc(0, 0, 8, 0, TAU); g.fillStyle = hot; g.fill();
  if (CACHE.sigil.size > 28) CACHE.sigil.clear();
  CACHE.sigil.set(key, s);
  return s;
}

/**
 * 水晶翅膀（单侧，向右展开；左侧镜像）：弯折的暗红骨架 + 垂挂的水晶枝。
 * 返回 canvas 与水晶位置（运行时逐颗叠加呼吸光晕）。
 */
function bakeWing() {
  const W = 780, H = 900, SX = 24, SY = 470;
  const c = mk(W, H), g = c.getContext('2d');
  g.lineCap = 'round'; g.lineJoin = 'round';
  // 主骨：肩 → 肘（高）→ 腕 → 尖（低垂）
  const bone = [[SX, SY], [150, 300], [330, 190], [520, 230], [700, 360]];
  const at = (t) => {   // 沿折线取点
    const segs = bone.length - 1, f = Math.min(segs - 1e-6, t * segs), i = f | 0, u = f - i;
    return [bone[i][0] + (bone[i + 1][0] - bone[i][0]) * u, bone[i][1] + (bone[i + 1][1] - bone[i][1]) * u];
  };
  const stroke = (pts, w, col) => { g.beginPath(); pts.forEach((p, i) => g[i ? 'lineTo' : 'moveTo'](p[0], p[1])); g.strokeStyle = col; g.lineWidth = w; g.stroke(); };
  stroke(bone, 22, '#12030a');
  stroke(bone, 12, '#2a0712');
  stroke(bone, 3, '#6a1426');
  // 水晶枝：{沿骨位置, 枝长, 水晶高}
  const spec = [[0.22, 120, 120, '#FF4040'], [0.40, 210, 150, '#FFA040'], [0.56, 170, 140, '#FFE040'],
    [0.72, 230, 150, '#60FF60'], [0.86, 160, 120, '#40E0FF'], [0.97, 110, 100, '#A060FF']];
  const crystals = [];
  const rnd = seeded(5);
  spec.forEach(([t, len, ch, col], idx) => {
    const [bx, by] = at(t);
    const tx = bx + (rnd() - 0.5) * 28, ty = by + len;
    stroke([[bx, by], [bx + (tx - bx) * 0.4, by + len * 0.5], [tx, ty]], 7, '#14040b');
    stroke([[bx, by], [bx + (tx - bx) * 0.4, by + len * 0.5], [tx, ty]], 2, '#4a0f20');
    // 水晶：细长菱形，顶端挂在枝尖
    const cw = 20 + idx % 2 * 6, cy0 = ty, cy1 = ty + ch, cm = ty + ch * 0.38;
    g.beginPath(); g.moveTo(tx, cy0); g.lineTo(tx + cw, cm); g.lineTo(tx, cy1); g.lineTo(tx - cw, cm); g.closePath();
    const gr = g.createLinearGradient(tx - cw, cy0, tx + cw, cy1);
    gr.addColorStop(0, '#ffffffcc'); gr.addColorStop(0.35, col); gr.addColorStop(1, rgba(col, 0.55));
    g.fillStyle = gr; g.fill();
    g.strokeStyle = 'rgba(20,0,10,0.85)'; g.lineWidth = 2.5; g.stroke();
    // 中脊高光
    g.beginPath(); g.moveTo(tx, cy0 + 4); g.lineTo(tx, cy1 - 6); g.strokeStyle = 'rgba(255,255,255,0.55)'; g.lineWidth = 1.6; g.stroke();
    crystals.push({ x: tx, y: cm, r: ch * 0.95, color: col });
  });
  return { canvas: c, crystals, w: W, h: H, sx: SX, sy: SY };
}

function bakeFog() {
  const W = 1024, H = 260, c = mk(W, H), g = c.getContext('2d');
  const rnd = seeded(31);
  for (let i = 0; i < 14; i++) {
    const x = rnd() * W, y = H * (0.3 + rnd() * 0.45), r = 90 + rnd() * 120;
    for (const dx of [-W, 0, W]) {      // 左右补一份保证横向无缝
      const gr = g.createRadialGradient(x + dx, y, 0, x + dx, y, r);
      gr.addColorStop(0, 'rgba(170,24,40,0.22)'); gr.addColorStop(0.55, 'rgba(120,12,28,0.09)'); gr.addColorStop(1, 'rgba(90,8,20,0)');
      g.fillStyle = gr; g.fillRect(x + dx - r, y - r, r * 2, r * 2);
    }
  }
  return c;
}

function bakeRays() {
  const S = 256, c = mk(S, S), g = c.getContext('2d');
  g.translate(S / 2, S / 2);
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * TAU, long = i % 2 === 0, L = long ? 126 : 84, w = long ? 0.07 : 0.045;
    const gr = g.createLinearGradient(0, 0, Math.cos(a) * L, Math.sin(a) * L);
    gr.addColorStop(0, 'rgba(255,255,255,0.95)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr;
    g.beginPath(); g.moveTo(0, 0); g.lineTo(Math.cos(a - w) * L, Math.sin(a - w) * L); g.lineTo(Math.cos(a + w) * L, Math.sin(a + w) * L); g.closePath(); g.fill();
  }
  return c;
}

/** 没有 DOM（Node 测试 / 极端环境）时返回 false，调用方直接跳过绘制 —— 与项目"素材没就绪就不画、战斗照常"的原则一致 */
function ensure() {
  if (typeof document === 'undefined') return false;
  if (!WING) { WING = bakeWing(); FOG = bakeFog(); RAYS = bakeRays(); }
  return true;
}

// ---------------------------------------------------------------
// 背景（屏幕空间）
// ---------------------------------------------------------------
/** ax, ay = 竞技场中心的屏幕坐标 */
export function drawBg(ctx, fight, ax, ay, W, H) {
  if (!ensure()) return;
  const t = fight.t ?? 0;
  const eff = Math.min(1, fight.bg?.eff ?? 0);                // 芙兰登场后才浮现
  const tintA = Math.min(1, fight.tint?.alpha ?? 0);
  ctx.save();
  ctx.imageSmoothingEnabled = true;

  // ① 水晶翅膀：双翼镜像，缓慢"呼吸"（绕肩部小幅摆动 + 整体微缩放）
  if (eff > 0.01) {
    const sc = (H / WING.h) * 1.12;
    const sh = fight.spell && fight.spell.t < 0.6 ? 1 + 0.05 * Math.sin((fight.spell.t / 0.6) * Math.PI) : 1;   // 宣言时展翅
    for (const side of [1, -1]) {
      ctx.save();
      ctx.translate(ax + side * 70 * sc, ay - H * 0.08);
      ctx.scale(side * sc * sh, sc * sh);
      ctx.rotate(Math.sin(t * 0.55 + (side > 0 ? 0 : 1.7)) * 0.03);
      ctx.translate(-WING.sx, -WING.sy);
      ctx.globalAlpha = 0.62 * eff;
      ctx.drawImage(WING.canvas, 0, 0);
      // 水晶辉光：逐颗呼吸（加法）
      ctx.globalCompositeOperation = 'lighter';
      WING.crystals.forEach((cr, i) => {
        const pulse = 0.55 + 0.45 * Math.sin(t * 1.6 + i * 1.3 + (side > 0 ? 0 : 2));
        ctx.globalAlpha = (0.16 + 0.22 * pulse) * eff;
        const gs = glowOf(cr.color), d = cr.r * 1.9;
        ctx.drawImage(gs, cr.x - d / 2, cr.y - d / 2, d, d);
      });
      ctx.globalCompositeOperation = 'source-over';
      ctx.restore();
    }
  }

  // ② 红雾：两层反向漂移
  if (tintA > 0.01) {
    for (const [yy, spd, a, sc] of [[H * 0.52, 12, 0.7, 1.0], [H * 0.74, -19, 0.55, 1.2]]) {
      ctx.globalAlpha = a * tintA * (0.4 + 0.6 * eff);
      const fw = 1024 * sc, fh = 260 * sc;
      let off = (t * spd) % fw; if (off > 0) off -= fw;
      for (let x = off; x < W; x += fw) ctx.drawImage(FOG, x, yy, fw, fh);
    }
  }

  // ③ 上升的余烬（确定性公式，无状态）：38 颗，红/粉/金三色，闪烁
  if (tintA > 0.05) {
    ctx.globalCompositeOperation = 'lighter';
    const cols = ['#ff3a4a', '#ff7aa8', '#ffc060'];
    for (let i = 0; i < 38; i++) {
      const h1 = ((i * 2654435761) >>> 0) / 4294967296, h2 = ((i * 40503 + 17) >>> 0) % 997 / 997;
      const spd = 14 + h2 * 26, size = 7 + h1 * 14;
      const y = H + 20 - ((t * spd + h1 * (H + 60)) % (H + 60));
      const x = (h2 * W + Math.sin(t * 0.6 + i) * 24 + W) % W;
      ctx.globalAlpha = (0.2 + 0.4 * (0.5 + 0.5 * Math.sin(t * 2 + i * 1.7))) * tintA * (1 - y / (H + 60) * 0.5);
      const gs = glowOf(cols[i % 3]);
      ctx.drawImage(gs, x - size, y - size, size * 2, size * 2);
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  // ④ 暗角
  const key = `${W}x${H}`;
  if (!VIG || vigKey !== key) {
    VIG = mk(W, H);
    const g = VIG.getContext('2d');
    const gr = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.4, W / 2, H / 2, Math.hypot(W, H) * 0.56);
    gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,0.5)');
    g.fillStyle = gr; g.fillRect(0, 0, W, H); vigKey = key;
  }
  ctx.globalAlpha = Math.max(tintA, eff);
  ctx.drawImage(VIG, 0, 0, W, H);
  ctx.restore();
}

// ---------------------------------------------------------------
// 法阵 + 宣言（世界空间）
// ---------------------------------------------------------------
export function themeOf(fight) {
  const id = fight.spell?.id ?? (fight.phase === 'prologue' ? 'prologue' : null);
  return CARD_THEME[id] ?? DEFAULT_THEME;
}

/** 叠在原有紫/黄两层阵外面的专属星形法阵；alpha 复用原阵的淡入淡出 */
export function drawSigil(ctx, fight, anchor, alpha) {
  if (!ensure()) return;
  const th = themeOf(fight);
  const t = fight.t ?? 0, born = fight.circleBornAt ?? t;
  const sp = fight.spell;
  const grow = sp ? ease(Math.min(1, sp.t / 0.7)) : 1;           // 新符卡开场：从小长到满
  const spin = (t - born);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.translate(anchor.x, anchor.y);
  // 外层大阵（顺时针慢转）
  const sOut = sigilOf(th.n, th.k, th.color);
  ctx.globalAlpha = alpha * 0.9;
  ctx.save(); ctx.rotate(spin * 0.32); const D1 = 470 * grow; ctx.drawImage(sOut, -D1 / 2, -D1 / 2, D1, D1); ctx.restore();
  // 内层小阵（逆时针、更亮，k 不同 → 视觉上两颗星互相穿插）
  const sIn = sigilOf(th.n === 5 ? 7 : 5, 2, th.color);
  ctx.globalAlpha = alpha * 0.75;
  ctx.save(); ctx.rotate(-spin * 0.55); const D2 = 250 * grow; ctx.drawImage(sIn, -D2 / 2, -D2 / 2, D2, D2); ctx.restore();
  // 心跳光：中心一团主题色
  ctx.globalAlpha = alpha * (0.3 + 0.12 * Math.sin(t * 3.2));
  const gs = glowOf(th.color), gd = 150 * grow; ctx.drawImage(gs, -gd / 2, -gd / 2, gd, gd);
  ctx.restore();
}

/** 符卡宣言：核心闪光 + 双层冲击波 + 放射光芒（前 0.8s） */
export function drawDeclare(ctx, fight, anchor) {
  const sp = fight.spell;
  if (!sp || sp.t > 0.9) return;
  if (!ensure()) return;
  const th = themeOf(fight), u = sp.t / 0.9;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.translate(anchor.x, anchor.y);
  // 放射光芒：缓慢旋转并放大淡出
  ctx.globalAlpha = Math.pow(1 - u, 1.4) * 0.9;
  const R = (200 + 520 * ease(u)); ctx.save(); ctx.rotate(u * 0.8); ctx.drawImage(RAYS, -R, -R, R * 2, R * 2); ctx.restore();
  // 核心闪光
  const gs = glowOf(th.color);
  ctx.globalAlpha = Math.pow(1 - u, 1.2);
  const cd = 140 + 360 * ease(u); ctx.drawImage(gs, -cd / 2, -cd / 2, cd, cd);
  // 两道冲击波环（错开）
  for (const [delay, w0] of [[0, 9], [0.12, 5]]) {
    const v = Math.max(0, Math.min(1, (u - delay) / (1 - delay)));
    if (v <= 0 || v >= 1) continue;
    ctx.globalAlpha = Math.pow(1 - v, 1.5);
    ctx.lineWidth = w0 * (1 - v) + 1;
    ctx.strokeStyle = th.color;
    ctx.beginPath(); ctx.arc(0, 0, 40 + 460 * ease(v), 0, TAU); ctx.stroke();
    ctx.lineWidth = Math.max(1, (w0 * (1 - v) + 1) * 0.35); ctx.strokeStyle = '#ffffff';
    ctx.beginPath(); ctx.arc(0, 0, 40 + 460 * ease(v), 0, TAU); ctx.stroke();
  }
  ctx.restore();
}

/** 发射器发光体（符卡脚本 st.vis 里 kind:'glow'）：白热核 + 主题色辉光 + 缓慢旋转的十字星芒，加法 */
export function drawGlowEmitter(ctx, v, t) {
  if (!ensure()) return;
  const a = (v.a ?? 1);
  const pulse = 0.85 + 0.15 * Math.sin(t * 7 + (v.x + v.y) * 0.01);
  const d = v.r * 5.2 * pulse;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = a * 0.9;
  ctx.drawImage(glowOf(v.color), v.x - d / 2, v.y - d / 2, d, d);
  ctx.globalAlpha = a;
  const cd = v.r * 2.0;
  ctx.drawImage(glowOf('#FFFFFF'), v.x - cd / 2, v.y - cd / 2, cd, cd);
  ctx.globalAlpha = a * 0.55;
  ctx.translate(v.x, v.y); ctx.rotate(t * 1.3);
  const R = v.r * 3.1;
  ctx.drawImage(RAYS, -R, -R, R * 2, R * 2);
  ctx.restore();
}

// ---------------------------------------------------------------
// HUD：符卡名铭牌 + 消弹冷却条 + EP 倒计时 + 宣言白闪
// ---------------------------------------------------------------
const FONT = '"Microsoft YaHei", "PingFang SC", sans-serif';

function bannerSprite(name, th, pw, dpr) {
  const key = `${name}|${th.color}|${pw}|${dpr}`;
  let s = CACHE.banner.get(key);
  if (s) return s;
  const ph = 60;
  s = mk(pw * dpr, ph * dpr);
  const g = s.getContext('2d');
  g.scale(dpr, dpr);
  // 拆 "分类「标题」"
  const m = /^(.*?)「(.*)」$/.exec(name);
  const cat = m ? m[1] : '', title = m ? m[2] : name;
  // 底板：左实右虚的暗红渐变，上下细线用主题色
  const bg = g.createLinearGradient(0, 0, pw, 0);
  bg.addColorStop(0, 'rgba(14,2,10,0)'); bg.addColorStop(0.18, 'rgba(24,4,16,0.86)'); bg.addColorStop(1, 'rgba(46,8,24,0.92)');
  g.fillStyle = bg; g.fillRect(0, 0, pw, ph);
  const ln = g.createLinearGradient(0, 0, pw, 0);
  ln.addColorStop(0, rgba(th.color, 0)); ln.addColorStop(0.3, rgba(th.color, 0.95)); ln.addColorStop(1, rgba(th.color, 0.5));
  g.fillStyle = ln; g.fillRect(0, 0, pw, 2); g.fillRect(0, ph - 2, pw, 2);
  // 右侧斜切装饰条
  g.fillStyle = th.color;
  g.beginPath(); g.moveTo(pw - 8, 0); g.lineTo(pw, 0); g.lineTo(pw, ph); g.lineTo(pw - 16, ph); g.closePath(); g.fill();
  g.fillStyle = 'rgba(255,255,255,0.55)';
  g.beginPath(); g.moveTo(pw - 20, 0); g.lineTo(pw - 17, 0); g.lineTo(pw - 29, ph); g.lineTo(pw - 32, ph); g.closePath(); g.fill();
  // 文字：分类（小、主题色偏亮）+ 标题（大、白、主题色辉光 —— 烘焙时 shadowBlur 无妨）
  g.textAlign = 'right'; g.textBaseline = 'alphabetic';
  const tx = pw - 40;
  if (cat) {
    g.font = `700 12px ${FONT}`; g.fillStyle = rgba(th.color, 0.95);
    // 手动字距
    const chars = [...cat]; let x = tx;
    for (let i = chars.length - 1; i >= 0; i--) { g.fillText(chars[i], x, 21); x -= g.measureText(chars[i]).width + 3; }
  }
  let fs = 23;
  g.font = `800 ${fs}px ${FONT}`;
  const maxW = pw - 70;
  while (g.measureText(title).width > maxW && fs > 13) { fs -= 1; g.font = `800 ${fs}px ${FONT}`; }
  g.shadowColor = th.color; g.shadowBlur = 12; g.fillStyle = '#fff';
  g.fillText(title, tx, 49);
  g.shadowBlur = 0; g.fillText(title, tx, 49);
  if (CACHE.banner.size > 30) CACHE.banner.clear();
  CACHE.banner.set(key, s);
  return s;
}

function chip(ctx, x, y, w, h, frac, label, colorHex, glowPulse) {
  ctx.fillStyle = 'rgba(14,2,10,0.72)'; ctx.fillRect(x, y, w, h);
  ctx.fillStyle = rgba(colorHex, 0.5 + glowPulse * 0.3); ctx.fillRect(x, y, Math.max(0, w * frac), h);
  ctx.fillStyle = 'rgba(255,255,255,0.25)'; ctx.fillRect(x, y, Math.max(0, w * frac), 1);
  ctx.strokeStyle = rgba(colorHex, 0.8); ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  ctx.font = `600 12px ${FONT}`; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  ctx.fillStyle = 'rgba(0,0,0,0.6)'; ctx.fillText(label, x + w - 8 + 1, y + h / 2 + 1);
  ctx.fillStyle = '#fff'; ctx.fillText(label, x + w - 8, y + h / 2);
}

export function drawHud(ctx, fight, W, H, dpr, purgeCdMax) {
  if (!fight.spellName) return;
  const th = themeOf(fight);
  const t = fight.t ?? 0;
  // 名称变化 → 记下滑入起点（写在 fight 上，仅渲染用）
  if (fight._hudName !== fight.spellName) { fight._hudName = fight.spellName; fight._hudAt = t; }
  const since = t - (fight._hudAt ?? t);

  ctx.save();
  // 宣言白闪（0.12s，加法）
  const sp = fight.spell;
  if (sp && sp.t < 0.12) {
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.38 * (1 - sp.t / 0.12);
    ctx.fillStyle = '#ffeef0'; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';
  }

  // 铭牌：右上角，0.35s 从右侧滑入，之后常驻（2.4s 后略微变淡以免抢视线）
  const pw = Math.round(Math.min(380, Math.max(260, W * 0.3)));
  const slide = since < 0.35 ? (1 - ease(since / 0.35)) * (pw + 30) : 0;
  const x = W - pw - 14 + slide, y = 12;
  const spr = bannerSprite(fight.spellName, th, pw, dpr);
  ctx.globalAlpha = since < 0.35 ? 1 : since < 2.4 ? 1 : 0.88;
  ctx.drawImage(spr, x, y, pw, 60);
  // 滑入时的扫光
  if (since < 0.7) {
    ctx.globalCompositeOperation = 'lighter';
    const sx = x + (pw + 60) * (since / 0.7) - 60;
    const g = ctx.createLinearGradient(sx, 0, sx + 60, 0);
    g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, 'rgba(255,255,255,0.5)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.fillRect(Math.max(x, sx), y, Math.min(60, x + pw - sx), 60);
    ctx.globalCompositeOperation = 'source-over';
  }
  ctx.globalAlpha = 1;

  // 小小猫娘消弹：进度条 chip（就绪时呼吸发亮）
  const cw = 214, cx = W - cw - 14, cy = y + 60 + 8;
  const cd = Math.max(0, fight.purgeCd ?? 0), ready = cd <= 0;
  const frac = ready ? 1 : 1 - cd / (purgeCdMax || 15);
  chip(ctx, cx, cy, cw, 22, frac, ready ? '空格 · 小小猫娘消弹  就绪!' : `空格 · 小小猫娘消弹  ${Math.ceil(cd)}s`,
    ready ? '#5fe6ff' : '#9a8aa8', ready ? 0.5 + 0.5 * Math.sin(t * 6) : 0);
  let ny = cy + 22 + 5;
  ctx.font = `12px ${FONT}`; ctx.textAlign = 'right'; ctx.textBaseline = 'top';
  ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillText('按住 Shift 慢速移动（移速减半）', W - 14 + 1, ny + 1);
  ctx.fillStyle = 'rgba(255,255,255,0.55)'; ctx.fillText('按住 Shift 慢速移动（移速减半）', W - 14, ny);
  ny += 20;
  if (fight.phase === 'extra') {
    chip(ctx, cx, ny, cw, 22, Math.max(0, Math.min(1, (fight.extraLeft ?? 0) / 70)), `Extra Phantom 存活 ${Math.ceil(fight.extraLeft ?? 0)}s`, '#ffd166', 0.3);
  }
  ctx.restore();
}
