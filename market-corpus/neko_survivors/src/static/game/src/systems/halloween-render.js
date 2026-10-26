// 万圣节双猫战 · 渲染层（第一百八十四轮视觉重写，参数出自 RABI_CANVAS2D 渲染参数书）
//
// 用户："所有特殊弹幕强制 lighter、中心纯白外围深橙、加拖尾；背景要黑→暖棕、巨大红月、
// 铁栅栏、十字墓碑、紫色浓雾；攻击名改成光球阵列拼出来的单词；立绘像素锐利 + sin 漂浮；
// 我要看到真正的光污染！"
//
// 结构（全部预烘焙，帧内零 createRadialGradient / 零 shadowBlur，见报告 §2.6）：
//   drawBg      屏幕空间：夜空 + 红月 + 栅栏 + 墓碑 + 地面（整层烘焙成一张画布）+ 动态雾/灯笼闪烁
//   drawWorldFx 世界空间：南瓜/糖果/猫爪/光柱 + 全部发光弹幕（lighter）+ 1/4 分辨率 Bloom
//   drawCats    世界空间：双猫像素立绘（4× 最近邻 + 呼吸 + 漂浮 + 光晕）
//   drawHud     屏幕空间：像素字招式名 / 底部双猫血条 / 头像 / 暗角 / 黑屏换场
//
// 与逻辑层的接口：全部只读 hf（HalloweenFight）与 world.enemyBullets 上的 b.halloween 弹，
// 不改任何判定。拖尾位置由 hf.update 采样到 b._tr。
import { textSprite } from './halloween-font.js';

const TAU = Math.PI * 2;
const BLOOM_K = 0.25;   // Bloom 画布相对屏幕的缩放（报告 §2.4：1/4 降采样）

function mk(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.ceil(w));
  c.height = Math.max(1, Math.ceil(h));
  return c;
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/** 确定性伪随机（背景每次生成都一样，不会"一刷新墓碑换位置"） */
function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

// ============================================================
// 1. 发光精灵（预烘焙，按弹种取色）—— 报告 §1.1/§1.2 色带
// ============================================================
// [白热核, 暖黄内边, 主发光, 中圈, 外圈, 远光晕]
const PALETTES = {
  amber: ['#FFFFFF', '#FFF3B0', '#FFC24A', '#FF8A1E', '#C24E00', '#8A2E00'],
  red: ['#FFFFFF', '#FFEAD0', '#FF9A3C', '#FF6A18', '#C23A00', '#7A1600'],   // 报告：深橙/红橙弹，不是粉红
  gold: ['#FFFFFF', '#FFFBD0', '#FFE05A', '#FFB81E', '#B06A00', '#6A3A00'],
  violet: ['#FFFFFF', '#F4E8FF', '#C8A2FF', '#8F5ACB', '#5A2A8A', '#2E1050'],
  cyan: ['#FFFFFF', '#E8FFFF', '#9CFFFF', '#4FB8D0', '#1E6A8A', '#0C3A50'],
  green: ['#FFFFFF', '#F0FFE0', '#B6FF7A', '#4FD04A', '#1E7A30', '#0A3A18'],
};

let SPR = null;   // 懒烘焙（模块加载时可能没有 document，如 node 测试）

function bakeGlow(pal) {
  const S = 128;
  const c = mk(S, S);
  const g = c.getContext('2d');
  const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  // 报告 §1.1：白核 0~10% / 暖黄 10~22% / 主发光 22~45% / 中圈 45~62% / 外圈 62~80% / 远光晕 80~100%
  r.addColorStop(0, rgba(pal[0], 1));
  r.addColorStop(0.10, rgba(pal[0], 1));
  r.addColorStop(0.20, rgba(pal[1], 0.95));
  r.addColorStop(0.38, rgba(pal[2], 0.70));
  r.addColorStop(0.55, rgba(pal[3], 0.45));
  r.addColorStop(0.72, rgba(pal[4], 0.22));
  r.addColorStop(0.88, rgba(pal[5], 0.08));
  r.addColorStop(1, rgba(pal[5], 0));
  g.fillStyle = r;
  g.fillRect(0, 0, S, S);
  return c;
}

/**
 * Bloom 专用泛光精灵：**去掉白核**，只保留暖色衰减。
 * 实测教训：Bloom 若直接复用带白核的发光精灵，几百发弹幕的白心被放大后会把整屏罩成
 * "红+白=粉紫"的雾霾（下半屏 36% 像素偏紫，原版 ≈1%）。泛光应该是弹体的色相扩散，不是漂白。
 */
function bakeBloomSprite(pal) {
  const S = 128;
  const c = mk(S, S);
  const g = c.getContext('2d');
  const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  r.addColorStop(0, rgba(pal[2], 0.55));
  r.addColorStop(0.35, rgba(pal[3], 0.30));
  r.addColorStop(0.70, rgba(pal[4], 0.10));
  r.addColorStop(1, rgba(pal[5], 0));
  g.fillStyle = r;
  g.fillRect(0, 0, S, S);
  return c;
}

function bakeCore() {
  const c = mk(32, 32);
  const g = c.getContext('2d');
  const r = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  r.addColorStop(0, 'rgba(255,255,255,1)');
  r.addColorStop(0.55, 'rgba(255,255,255,1)');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, 32, 32);
  return c;
}

/** 光柱截面：沿 y 方向的软边 1-(x/w)^2 轮廓，中心近白，边缘暖橙 → 透明（报告 §2.5） */
function bakeBeam() {
  const c = mk(16, 128);
  const g = c.getContext('2d');
  const l = g.createLinearGradient(0, 0, 0, 128);
  l.addColorStop(0, 'rgba(255,90,0,0)');
  l.addColorStop(0.20, 'rgba(255,120,20,0.20)');
  l.addColorStop(0.36, 'rgba(255,170,60,0.62)');
  l.addColorStop(0.46, 'rgba(255,236,170,0.95)');
  l.addColorStop(0.5, 'rgba(255,253,235,1)');
  l.addColorStop(0.54, 'rgba(255,236,170,0.95)');
  l.addColorStop(0.64, 'rgba(255,170,60,0.62)');
  l.addColorStop(0.80, 'rgba(255,120,20,0.20)');
  l.addColorStop(1, 'rgba(255,90,0,0)');
  g.fillStyle = l;
  g.fillRect(0, 0, 16, 128);
  return c;
}

/**
 * 末端渐隐的光柱（十字爆风用）：截面同 bakeBeam，沿长度方向 0.42 之后平滑衰减到 0。
 * 用户："南瓜爆炸之后的十字火焰断的有点太明显了，稍微过渡一丢丢" —— 原来四条臂是
 * 等宽硬截止（215px 处突然没有）+ 0.35s 一刀切消失；现在臂端渐隐、再叠末端柔光帽。
 */
function bakeBeamTaper() {
  const c = mk(128, 128);
  const g = c.getContext('2d');
  g.drawImage(bakeBeam(), 0, 0, 128, 128);
  g.globalCompositeOperation = 'destination-in';
  const l = g.createLinearGradient(0, 0, 128, 0);
  // 实测教训：渐隐从 42% 才开始 + 三层加法叠加 → 臂身亮度饱和在 ~230/255，渐隐被
  // "白色平台"吃掉，掉出饱和线后三步内骤降 43%（看起来仍是一刀切）。现在渐隐从 18% 起、
  // 更长更缓（≈ cos 曲线），配合下面降低叠加强度避免过曝。
  l.addColorStop(0, 'rgba(0,0,0,0.9)');
  l.addColorStop(0.06, 'rgba(0,0,0,1)');
  l.addColorStop(0.18, 'rgba(0,0,0,1)');
  l.addColorStop(0.40, 'rgba(0,0,0,0.78)');
  l.addColorStop(0.62, 'rgba(0,0,0,0.44)');
  l.addColorStop(0.82, 'rgba(0,0,0,0.16)');
  l.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = l;
  g.fillRect(0, 0, 128, 128);
  return c;
}

/** 像素南瓜灯（15×13 点阵 × 倍率，最近邻），雕刻面 #2A0A00，蒂 #4A5A18（报告 §1.3） */
const PUMPKIN_ROWS = [
  '......SS.......',
  '.....SSS.......',
  '...ooOOSOOoo...',
  '.oooOOOOOOOOoo.',
  'oooOOOOOOOOOOOo',
  'oOOkOOOOOOOkOOd',
  'OOkkkOOOOOkkkOd',
  'OOOkOOOOOOOkOOd',
  'OOOOOOOkOOOOOOd',
  'OOkOOOOOOOOkOOd',
  'dOkkOkkOkkOkkOd',
  '.dOkkkkkkkkkOd.',
  '..dddddddddd...',
];
const PUMPKIN_PAL = { S: '#4A5A18', o: '#FFA52A', O: '#FF8103', d: '#C24000', k: '#2A0A00' };

function bakePumpkin(scale) {
  const c = mk(15 * scale, 13 * scale);
  const g = c.getContext('2d');
  PUMPKIN_ROWS.forEach((row, y) => {
    for (let x = 0; x < 15; x++) {
      const ch = row[x] ?? '.';
      const col = PUMPKIN_PAL[ch];
      if (!col) continue;
      g.fillStyle = col;
      g.fillRect(x * scale, y * scale, scale, scale);
    }
  });
  return c;
}

/** 猫爪（追踪弹本体）：一个大肉垫 + 四个趾豆，白心紫边 */
function bakePaw() {
  const c = mk(72, 72);
  const g = c.getContext('2d');
  const blob = (x, y, rx, ry, rot) => {
    g.save();
    g.translate(x, y);
    g.rotate(rot);
    g.beginPath();
    g.ellipse(0, 0, rx, ry, 0, 0, TAU);
    g.fillStyle = '#C8A2FF';
    g.fill();
    g.beginPath();
    g.ellipse(0, 0, rx * 0.62, ry * 0.62, 0, 0, TAU);
    g.fillStyle = '#FFFFFF';
    g.fill();
    g.restore();
  };
  blob(36, 46, 17, 13, 0);
  blob(16, 28, 7, 9, -0.5);
  blob(30, 17, 7, 9, -0.12);
  blob(44, 17, 7, 9, 0.12);
  blob(58, 28, 7, 9, 0.5);
  return c;
}

/** 包装糖果：圆身 + 两侧包装纸三角 + 高光条，按颜色烘焙 */
const CANDY_COL = { red: '#FF5A4A', gold: '#FFC93C', green: '#3FE08A', cyan: '#4FB8FF' };
function bakeCandy(col) {
  const c = mk(44, 24);
  const g = c.getContext('2d');
  g.fillStyle = col;
  g.beginPath(); g.moveTo(2, 2); g.lineTo(16, 12); g.lineTo(2, 22); g.closePath(); g.fill();
  g.beginPath(); g.moveTo(42, 2); g.lineTo(28, 12); g.lineTo(42, 22); g.closePath(); g.fill();
  g.beginPath(); g.arc(22, 12, 10, 0, TAU); g.fill();
  g.strokeStyle = 'rgba(255,255,255,0.85)';
  g.lineWidth = 2.2;
  g.beginPath(); g.arc(22, 12, 6.2, -0.6, 1.6); g.stroke();
  g.fillStyle = 'rgba(255,255,255,0.9)';
  g.fillRect(18, 6, 3, 3);
  return c;
}

/** 紫雾带：多团柔光叠成一条可横向无缝平铺的雾（用户："一层缓慢漂移的紫色浓雾"） */
function bakeFog() {
  const W = 1024, H = 240;
  const c = mk(W, H);
  const g = c.getContext('2d');
  const rnd = seeded(77);
  // 实测校准（与原版截图逐带比色）：首版雾太浓，把下 1/3 屏幕染成 38%~68% 紫色像素（原版 ≈1%），
  // 栅栏/墓碑全被盖住。现在是"暖紫红的薄雾"：块数/不透明度都压到氛围级，仍可见漂移但不抢背景。
  for (let i = 0; i < 12; i++) {
    const x = rnd() * W, y = H * (0.35 + rnd() * 0.4), r = 80 + rnd() * 110;
    for (const dx of [-W, 0, W]) {   // 左右各补一份，保证平铺无接缝
      const rg = g.createRadialGradient(x + dx, y, 0, x + dx, y, r);
      rg.addColorStop(0, 'rgba(112,36,84,0.10)');
      rg.addColorStop(0.5, 'rgba(96,28,72,0.04)');
      rg.addColorStop(1, 'rgba(80,22,60,0)');
      g.fillStyle = rg;
      g.fillRect(x + dx - r, y - r, r * 2, r * 2);
    }
  }
  return c;
}

function sprites() {
  if (SPR) return SPR;
  const glow = {};
  const bloomOf = new Map();   // 发光精灵 → 对应的无白核泛光精灵
  for (const [k, pal] of Object.entries(PALETTES)) {
    glow[k] = bakeGlow(pal);
    bloomOf.set(glow[k], bakeBloomSprite(pal));
  }
  const candy = {};
  for (const [k, col] of Object.entries(CANDY_COL)) candy[k] = bakeCandy(col);
  SPR = {
    glow,
    bloomOf,
    core: bakeCore(),
    beam: bakeBeam(),
    beamTaper: bakeBeamTaper(),
    pumpkin3: bakePumpkin(3),
    pumpkin4: bakePumpkin(4),
    paw: bakePaw(),
    candy,
    fog: bakeFog(),
    bloom: null,
    bloomW: 0,
    bloomH: 0,
    bg: null,
    bgKey: '',
    vig: null,
    vigKey: '',
  };
  return SPR;
}

// ============================================================
// 2. 背景：烘焙静态层 + 每帧动态层（雾 / 灯笼闪烁 / 月晕呼吸）
// ============================================================
/**
 * 烘焙整张静态背景（屏幕空间，竞技场中心 = 画布中心）。
 * 地平线 GY = 中心下 150：上方 480px 全是夜空（弹幕主战场），下方 180px 是墓地地面。
 */
function bakeBg(W, H, dpr) {
  const c = mk(W * dpr, H * dpr);
  const g = c.getContext('2d');
  g.scale(dpr, dpr);
  g.imageSmoothingEnabled = false;
  const GY = Math.round(H / 2 + 150);
  const rnd = seeded(2025);

  // ---- 夜空：黑 → 暖棕（报告 §1.3，不是紫） ----
  const sky = g.createLinearGradient(0, 0, 0, GY);
  sky.addColorStop(0, '#000000');
  sky.addColorStop(0.30, '#0C0600');
  sky.addColorStop(0.55, '#190A00');
  sky.addColorStop(0.75, '#2A0D00');
  sky.addColorStop(0.92, '#391101');
  sky.addColorStop(1, '#3E1403');
  g.fillStyle = sky;
  g.fillRect(0, 0, W, GY + 2);

  // ---- 红月：中央偏左大橙月 r=95，平涂 + 月缘高光 + 30~40px 棕晕 ----
  const mx = Math.round(W / 2 - 122), my = Math.round(H / 2 - 200), MR = 95;
  const halo = g.createRadialGradient(mx, my, MR - 6, mx, my, MR + 70);
  halo.addColorStop(0, 'rgba(75,40,11,0.95)');
  halo.addColorStop(0.28, 'rgba(64,27,5,0.75)');
  halo.addColorStop(0.60, 'rgba(52,12,0,0.42)');
  halo.addColorStop(1, 'rgba(38,7,0,0)');
  g.fillStyle = halo;
  g.fillRect(mx - MR - 80, my - MR - 80, (MR + 80) * 2, (MR + 80) * 2);
  const disc = g.createLinearGradient(0, my - MR, 0, my + MR);
  disc.addColorStop(0, '#FFB46A');
  disc.addColorStop(1, '#FFBB6A');
  g.fillStyle = disc;
  g.beginPath(); g.arc(mx, my, MR, 0, TAU); g.fill();
  g.strokeStyle = '#FFC078';
  g.lineWidth = 2;
  g.beginPath(); g.arc(mx, my, MR - 1, 0, TAU); g.stroke();

  // ---- 远景：低矮山丘剪影 + 枯树 + 远处小墓碑（视差层，#243040 α0.6） ----
  g.fillStyle = '#140806';
  g.beginPath();
  g.moveTo(0, GY);
  for (let x = 0; x <= W; x += 16) {
    g.lineTo(x, GY - 26 - Math.sin(x * 0.011) * 14 - Math.sin(x * 0.027 + 1.3) * 9);
  }
  g.lineTo(W, GY);
  g.closePath();
  g.fill();
  const tree = (tx, th) => {
    g.strokeStyle = '#0d0508';
    g.lineCap = 'round';
    g.lineWidth = Math.max(3, th * 0.06);
    g.beginPath(); g.moveTo(tx, GY - 6); g.lineTo(tx + 4, GY - th); g.stroke();
    g.lineWidth = Math.max(2, th * 0.03);
    const br = (x0, y0, dx, dy, d) => {
      g.beginPath(); g.moveTo(x0, y0); g.lineTo(x0 + dx, y0 + dy); g.stroke();
      if (d > 0) {
        br(x0 + dx, y0 + dy, dx * 0.7 - dy * 0.35, dy * 0.7 + dx * 0.15 - 6, d - 1);
        br(x0 + dx, y0 + dy, dx * 0.6 + dy * 0.35, dy * 0.7 - dx * 0.15 - 4, d - 1);
      }
    };
    br(tx + 3, GY - th * 0.62, -th * 0.22, -th * 0.16, 2);
    br(tx + 4, GY - th * 0.78, th * 0.24, -th * 0.15, 2);
    br(tx + 4, GY - th, -th * 0.08, -th * 0.12, 1);
  };
  tree(W * 0.08, 150); tree(W * 0.93, 175); tree(W * 0.62, 118);
  g.globalAlpha = 0.6;
  for (let x = 30; x < W; x += 70 + rnd() * 60) {
    const s = 0.55 + rnd() * 0.35, hh = 36 * s;
    g.fillStyle = '#243040';
    if (rnd() < 0.5) {   // 十字
      g.fillRect(x - 3 * s, GY - 4 - hh, 6 * s, hh);
      g.fillRect(x - 11 * s, GY - 4 - hh * 0.78, 22 * s, 5 * s);
    } else {            // 圆顶碑
      g.beginPath(); g.moveTo(x - 9 * s, GY - 4); g.lineTo(x - 9 * s, GY - 4 - hh * 0.7);
      g.arc(x, GY - 4 - hh * 0.7, 9 * s, Math.PI, 0); g.lineTo(x + 9 * s, GY - 4); g.fill();
    }
  }
  g.globalAlpha = 1;

  // ---- 地面（先铺，栅栏立在地平线上）----
  const ground = g.createLinearGradient(0, GY, 0, H);
  ground.addColorStop(0, '#1E0C05');
  ground.addColorStop(1, '#110500');
  g.fillStyle = ground;
  g.fillRect(0, GY, W, H - GY);
  // 橙色横向条带（舞台质感）：近大远小、断续
  // 实测校准：原版条带亮度峰值 112~138，正是不透明的 #B04000/#CD540A（亮度≈111）；
  // 首版做成 0.2~0.45 半透明又细又密（峰值仅 ~40）。报告：条高 6~10px、间距 40~60px。
  for (let i = 0, y = GY + 12; y < H; i++) {
    const bh = Math.min(10, 6 + i * 1.3);
    let x = -rnd() * 200;
    while (x < W) {
      const len = 140 + rnd() * 260, gap = 24 + rnd() * 80;
      g.fillStyle = rnd() < 0.5 ? '#B04000' : '#CD540A';
      g.globalAlpha = Math.min(0.72, 0.5 + i * 0.08);   // 再亮会和 Bloom 叠成 205（原版峰值 138）
      g.fillRect(x, y, len, bh);
      g.fillStyle = '#FF9A3C';                 // 条带顶沿高光
      g.globalAlpha = 0.28;
      g.fillRect(x, y, len, 1);
      g.globalAlpha = 1;
      x += len + gap;
    }
    y += 40 + i * 8;
  }

  // ---- 铁栅栏：横杆 6px / 立柱 8~10px 间距 130 / 矛尖三角 18px（报告 §1.3） ----
  const R1 = GY - 24, R2 = GY - 92;
  const rail = (y) => {
    g.fillStyle = '#2C3732'; g.fillRect(0, y, W, 6);
    g.fillStyle = '#3C4A44'; g.fillRect(0, y, W, 1);
    g.fillStyle = '#1B231F'; g.fillRect(0, y + 5, W, 1);
  };
  // 细栏杆（每 26px 一根，带矛尖）
  for (let x = -6; x < W + 26; x += 26) {
    g.fillStyle = '#212F29'; g.fillRect(x, R2 - 10, 4, GY - R2 + 12);
    g.fillStyle = '#2C3732'; g.fillRect(x, R2 - 10, 1, GY - R2 + 12);
    g.fillStyle = '#212F29';
    g.beginPath(); g.moveTo(x - 1, R2 - 10); g.lineTo(x + 2, R2 - 19); g.lineTo(x + 5, R2 - 10); g.fill();
  }
  rail(R2); rail(R1);
  // 粗立柱 + 矛尖
  for (let x = W / 2 + 65 - 130 * Math.ceil((W / 2 + 65) / 130); x < W + 130; x += 130) {
    const topY = GY - 124;
    g.fillStyle = '#27352F'; g.fillRect(x - 5, topY, 10, GY - topY + 6);
    g.fillStyle = '#3C4A44'; g.fillRect(x - 5, topY, 2, GY - topY + 6);
    g.fillStyle = '#1B231F'; g.fillRect(x + 2, topY, 3, GY - topY + 6);
    g.fillStyle = '#27352F';
    g.beginPath(); g.moveTo(x - 7, topY); g.lineTo(x, topY - 18); g.lineTo(x + 7, topY); g.fill();
    g.fillStyle = '#3C4A44';
    g.beginPath(); g.moveTo(x - 7, topY); g.lineTo(x, topY - 18); g.lineTo(x - 1, topY); g.fill();
  }
  // 草叶剪影压住栏脚
  g.fillStyle = '#0a0402';
  for (let x = 0; x < W; x += 4 + rnd() * 3) {
    const h2 = 5 + rnd() * 12;
    g.beginPath(); g.moveTo(x, GY + 4); g.lineTo(x + 2, GY + 4 - h2); g.lineTo(x + 4, GY + 4); g.fill();
  }

  // ---- 近景墓碑（十字 + 圆顶，近大远小），#304058 受光 #43637B 暗部 #202828 苔藓 #284010 ----
  const graves = [];
  const NG = Math.max(8, Math.round(W / 150));
  for (let i = 0; i < NG; i++) {
    graves.push({
      x: (i + 0.5) * (W / NG) + (rnd() - 0.5) * 60,
      y: GY + 34 + rnd() * 120,
      cross: rnd() < 0.55,
    });
  }
  graves.sort((a, b) => a.y - b.y);
  for (const gr of graves) {
    const s = 0.8 + ((gr.y - GY) / 160) * 0.55;
    g.save();
    g.translate(gr.x, gr.y);
    g.fillStyle = 'rgba(0,0,0,0.42)';
    g.beginPath(); g.ellipse(2, 3, 22 * s, 6 * s, 0, 0, TAU); g.fill();
    const body = (px, py, w, h) => {
      g.fillStyle = '#304058'; g.fillRect(px, py, w, h);
      g.fillStyle = '#43637B'; g.fillRect(px, py, 2 * s, h);
      g.fillStyle = '#3C445B'; g.fillRect(px, py, w, 2 * s);
      g.fillStyle = '#202828'; g.fillRect(px + w - 3 * s, py, 3 * s, h);
      g.fillStyle = '#101820'; g.fillRect(px, py + h - 2 * s, w, 2 * s);
    };
    if (gr.cross) {
      body(-5 * s, -58 * s, 10 * s, 58 * s);
      body(-17 * s, -46 * s, 34 * s, 9 * s);
      g.fillStyle = '#284010'; g.fillRect(-5 * s, -20 * s, 4 * s, 5 * s); g.fillRect(8 * s, -42 * s, 6 * s, 3 * s);
    } else {
      g.fillStyle = '#304058';
      g.beginPath(); g.moveTo(-15 * s, 0); g.lineTo(-15 * s, -42 * s); g.arc(0, -42 * s, 15 * s, Math.PI, 0); g.lineTo(15 * s, 0); g.fill();
      g.fillStyle = '#43637B'; g.fillRect(-15 * s, -42 * s, 3 * s, 42 * s);
      g.fillStyle = '#202828'; g.fillRect(11 * s, -42 * s, 4 * s, 42 * s);
      g.fillStyle = '#101820'; g.fillRect(-15 * s, -3 * s, 30 * s, 3 * s);
      g.fillStyle = '#1b2530';
      g.fillRect(-7 * s, -36 * s, 14 * s, 2 * s); g.fillRect(-7 * s, -30 * s, 14 * s, 2 * s); g.fillRect(-5 * s, -24 * s, 10 * s, 2 * s);
      g.fillStyle = '#284010'; g.fillRect(-15 * s, -9 * s, 9 * s, 5 * s);
    }
    g.restore();
  }

  // ---- 栏杆上悬挂的两盏南瓜灯 ----
  const S2 = sprites();
  const lanterns = [];
  for (const lx of [W / 2 - 330, W / 2 + 352]) {
    const ly = R2 + 24;
    g.strokeStyle = '#1B231F'; g.lineWidth = 2;
    g.beginPath(); g.moveTo(lx, R2 + 6); g.lineTo(lx, ly); g.stroke();
    g.drawImage(S2.pumpkin3, Math.round(lx - 22), Math.round(ly));
    lanterns.push({ x: lx, y: ly + 20 });
  }

  return { canvas: c, GY, lanterns, moon: { x: mx, y: my, r: MR } };
}

function ensureBg(W, H, dpr) {
  const S = sprites();
  const key = `${W}x${H}@${dpr}`;
  if (S.bg && S.bgKey === key) return S.bg;
  S.bg = bakeBg(W, H, dpr);
  S.bgKey = key;
  return S.bg;
}

/** 屏幕空间背景：烘焙层 + 月晕呼吸 + 灯笼闪烁 + 两层紫雾 */
export function drawBg(ctx, hf, camera, W, H, dpr) {
  const S = sprites();
  const bg = ensureBg(W, H, dpr);
  const t = Math.max(0, hf.t ?? 0);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(bg.canvas, 0, 0, W, H);
  // 加法层：月晕呼吸 / 灯笼火光 —— 全是预烘焙精灵缩放，不建渐变
  ctx.globalCompositeOperation = 'lighter';
  ctx.imageSmoothingEnabled = true;
  ctx.globalAlpha = 0.10 + 0.03 * Math.sin(t * 0.9);
  ctx.drawImage(S.glow.amber, bg.moon.x - 230, bg.moon.y - 230, 460, 460);
  bg.lanterns.forEach((l, i) => {
    const fl = 0.34 + 0.10 * Math.sin(t * 8.3 + i * 2.1) + 0.05 * Math.sin(t * 21 + i);
    ctx.globalAlpha = fl;
    ctx.drawImage(S.glow.amber, l.x - 95, l.y - 95, 190, 190);
    ctx.globalAlpha = fl * 0.55;
    ctx.drawImage(S.glow.red, l.x - 200, l.y - 200, 400, 400);
  });
  // 紫雾：两层反向漂移，压在地平线附近（source-over，淡紫不抢弹幕）
  ctx.globalCompositeOperation = 'source-over';
  const fogH = 240;
  for (const [yy, spd, a, sc] of [[bg.GY - 110, 14, 0.55, 1.0], [bg.GY - 20, -22, 0.45, 1.15]]) {
    ctx.globalAlpha = a;
    const fw = 1024 * sc;
    let off = (t * spd) % fw;
    if (off > 0) off -= fw;
    for (let x = off; x < W; x += fw) ctx.drawImage(S.fog, x, yy, fw, fogH * sc);
  }
  ctx.restore();
}

// ============================================================
// 3. 世界层：南瓜 / 糖果 / 猫爪 / 光柱 / 全部发光弹幕 / Bloom
// ============================================================
function ensureBloom(W, H) {
  const S = sprites();
  const bw = Math.ceil(W * BLOOM_K), bh = Math.ceil(H * BLOOM_K);
  if (!S.bloom || S.bloomW !== bw || S.bloomH !== bh) {
    S.bloom = mk(bw, bh);
    S.bloomW = bw;
    S.bloomH = bh;
  }
  return S.bloom;
}

/** 带旋转/拉伸地画一根光柱（沿 +x，宽 w，长 len） */
function beamDraw(c, x, y, ang, len, w, alpha, k = 1, spr = null) {
  const S = SPR;
  c.save();
  c.translate(x * k, y * k);
  c.rotate(ang);
  c.globalAlpha = alpha;
  c.drawImage(spr ?? S.beam, 0, (-w / 2) * k, len * k, w * k);
  c.restore();
}

export function drawWorldFx(ctx, world, hf, camera, W, H) {
  const S = sprites();
  const t = Math.max(0, hf.t ?? 0);
  if ((hf.t ?? 0) < 0) return;
  const bloom = ensureBloom(W, H);
  const bc = bloom.getContext('2d');
  bc.setTransform(1, 0, 0, 1, 0, 0);
  bc.globalCompositeOperation = 'source-over';
  bc.globalAlpha = 1;
  bc.clearRect(0, 0, bloom.width, bloom.height);
  bc.globalCompositeOperation = 'lighter';
  const ox = camera.cx - W / 2, oy = camera.cy - H / 2;   // 世界 → 屏幕
  // 在 Bloom 画布上画一团光（世界坐标）
  const bglow = (spr, x, y, d, a) => {
    bc.globalAlpha = a;
    const dd = d * BLOOM_K;
    bc.drawImage(S.bloomOf.get(spr) ?? spr, (x - ox) * BLOOM_K - dd / 2, (y - oy) * BLOOM_K - dd / 2, dd, dd);
  };

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.imageSmoothingEnabled = true;

  // ---- 南瓜炸弹：落点预警（十字参考线 + 呼吸圈）→ 下落南瓜 → 十字光柱爆风 ----
  for (const pk of hf.pumpkins ?? []) {
    if (pk.t > 0) {
      const u = 1 - pk.t / 1.1;                       // 0 → 1 越来越近
      ctx.globalAlpha = 0.10 + 0.22 * u;
      beamDraw(ctx, pk.x, pk.y, 0, 210, 12, 0.10 + 0.22 * u);   // 右臂预警
      beamDraw(ctx, pk.x, pk.y, Math.PI, 210, 12, 0.10 + 0.22 * u);
      beamDraw(ctx, pk.x, pk.y, Math.PI / 2, 210, 12, 0.10 + 0.22 * u);
      beamDraw(ctx, pk.x, pk.y, -Math.PI / 2, 210, 12, 0.10 + 0.22 * u);
      const r = 40 - 14 * u + Math.sin(t * 18) * 2;
      ctx.globalAlpha = 0.35 + 0.5 * u;
      ctx.drawImage(S.glow.amber, pk.x - r, pk.y - r * 0.55, r * 2, r * 1.1);
      // 下落的南瓜（越近越大，带摇摆）
      const h = pk.t / 1.1;
      const sy = pk.y - h * 430;
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = Math.min(1, u * 3 + 0.2);
      ctx.imageSmoothingEnabled = false;
      const pw = S.pumpkin4.width, ph = S.pumpkin4.height;
      ctx.save();
      ctx.translate(pk.x, sy);
      ctx.rotate(Math.sin(t * 7 + pk.x) * 0.18);
      ctx.drawImage(S.pumpkin4, -pw / 2, -ph / 2);
      ctx.restore();
      ctx.imageSmoothingEnabled = true;
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.7;
      ctx.drawImage(S.glow.red, pk.x - 70, sy - 70, 140, 140);
      bglow(S.glow.amber, pk.x, sy, 170, 0.5);
    } else if ((pk.age ?? 0) < 0.9) {
      // 十字爆风三段式（判定窗口仍只有前 0.35s，余辉纯视觉）：
      //   0~0.10s 由中心向外"长出"（ease-out）→ 0.10~0.35s 满亮 → 0.35~0.90s 缓出
      const age = pk.age ?? 0;
      const gu = Math.min(1, age / 0.10);
      const grow = 1 - (1 - gu) * (1 - gu);
      const fade = age < 0.35 ? 1 : Math.pow(Math.max(0, 1 - (age - 0.35) / 0.55), 1.6);
      const len = 215 * grow;
      const w = 24 + 46 * Math.max(0, 1 - age / 0.9);     // 由粗到细
      for (const a of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
        // 强度刻意压低（0.62/0.5/0.3）：三层加法 + Bloom 叠起来才刚好接近白热，不再裁切在 255
        beamDraw(ctx, pk.x, pk.y, a, len, w, 0.62 * fade, 1, S.beamTaper);
        beamDraw(ctx, pk.x, pk.y, a, len, w * 0.34, 0.5 * fade, 1, S.beamTaper);
        beamDraw(bc, pk.x - ox, pk.y - oy, a, len, w * 2.2, 0.3 * fade, BLOOM_K, S.beamTaper);
        // 臂端柔光帽（小而淡，只负责把尖端融进夜空）
        ctx.globalAlpha = 0.3 * fade * grow;
        const ex = pk.x + Math.cos(a) * len * 0.8, ey = pk.y + Math.sin(a) * len * 0.8;
        ctx.drawImage(S.glow.amber, ex - 26, ey - 26, 52, 52);
      }
      ctx.globalAlpha = fade;
      const d = 190 * (1.3 - 0.3 * fade);
      ctx.drawImage(S.glow.amber, pk.x - d / 2, pk.y - d / 2, d, d);
      bglow(S.glow.amber, pk.x, pk.y, 340, 0.8 * fade);
    }
  }

  // ---- 激光：预警细线 0.7s → 白核 + 橙晕光柱（呼吸）+ 起点光斑 ----
  for (const b of hf.waves ?? []) {
    const age = b.life - b.t;
    if (age < 0.7) {
      const a = 0.25 + 0.35 * (age / 0.7);
      beamDraw(ctx, b.x, b.y, b.angle, 1700, 5, a);
      continue;
    }
    const fade = Math.min(1, b.t / 0.35, (age - 0.7) / 0.12);
    const br = 1 + 0.07 * Math.sin(t * 30 + b.angle * 3);
    beamDraw(ctx, b.x, b.y, b.angle, 1700, 56 * br, 0.55 * fade);
    beamDraw(ctx, b.x, b.y, b.angle, 1700, 24 * br, 0.92 * fade);
    beamDraw(ctx, b.x, b.y, b.angle, 1700, 7, fade);
    beamDraw(bc, b.x - ox, b.y - oy, b.angle, 1700, 130, 0.42 * fade, BLOOM_K);
    ctx.globalAlpha = fade;
    ctx.drawImage(S.glow.amber, b.x - 70, b.y - 70, 140, 140);
  }

  // ---- 猫爪（追踪弹）：紫色大肉垫 + 白心，持续喷余烬（余烬由逻辑层生成的小弹提供） ----
  for (const c of hf._claws ?? []) {
    const ang = Math.atan2(c.vy, c.vx) + Math.PI / 2;
    ctx.globalAlpha = 0.85;
    ctx.drawImage(S.glow.violet, c.x - 54, c.y - 54, 108, 108);
    ctx.save();
    ctx.translate(c.x, c.y);
    ctx.rotate(ang);
    const pul = 1 + 0.08 * Math.sin(t * 12);
    ctx.globalAlpha = 0.95;
    ctx.drawImage(S.paw, -26 * pul, -26 * pul, 52 * pul, 52 * pul);
    ctx.restore();
    bglow(S.glow.violet, c.x, c.y, 170, 0.55);
  }

  // ---- 糖果：包装糖 + 同色光晕，落地弹跳留影 ----
  for (const cd of hf._candies ?? []) {
    const key = CANDY_COL[cd.color] ? cd.color : 'red';
    ctx.globalAlpha = 0.8;
    ctx.drawImage(S.glow[key === 'red' ? 'red' : key], cd.x - 30, cd.y - 30, 60, 60);
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.translate(cd.x, cd.y);
    ctx.rotate(cd.x * 0.02 + t * 3);
    ctx.globalAlpha = 1;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(S.candy[key], -22, -12, 44, 24);
    ctx.restore();
    bglow(S.glow[key], cd.x, cd.y, 110, 0.4);
  }

  // ---- 全部发光弹幕（lighter）----
  drawBullets(ctx, bc, bglow, world.enemyBullets.items, hf, ox, oy, W, H, t);

  ctx.restore();

  // ---- Bloom 叠回：1/4 分辨率画布线性放大 = 天然大范围泛光（报告 §2.4）----
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.imageSmoothingEnabled = true;
  ctx.globalAlpha = 0.7;
  ctx.drawImage(bloom, ox, oy, W, H);
  ctx.restore();
}

function drawBullets(ctx, bc, bglow, items, hf, ox, oy, W, H, t) {
  const S = SPR;
  let n = 0;
  for (const b of items) if (b.halloween && !b.dead) n++;
  if (!n) return;
  // LOD：弹多时砍拖尾 / 外泛光，保 60fps（报告 §2.6）
  const lod = n > 540 ? 0 : n > 380 ? 1 : 2;
  const trailN = lod === 2 ? 5 : lod === 1 ? 3 : 0;

  for (const b of items) {
    if (!b.halloween || b.dead) continue;
    if (b.x < ox - 60 || b.x > ox + W + 60 || b.y < oy - 60 || b.y > oy + H + 60) continue;
    const age = b.age ?? 1;

    // ---- 灯珠字母弹幕：白核 4 / 暖黄边 7.5 / 橙晕 24 / 外泛光 44（报告 §3.2），逐珠呼吸 ----
    if (b.hwKind === 'bead') {
      const fadeIn = Math.min(1, age / 0.25);
      const breathe = 0.85 + 0.15 * Math.sin(t * 3 + (b.hwPhase ?? 0));
      const a = fadeIn * breathe;
      const pitch = (b.radius || 8) * 2;          // 逻辑层把点阵间距存在 radius 里（pitch/2）
      const dGlow = pitch * 1.9, dOuter = pitch * 3.2, dCore = pitch * 0.42;
      ctx.globalAlpha = a * 0.45;
      ctx.drawImage(S.glow.gold, b.x - dOuter / 2, b.y - dOuter / 2, dOuter, dOuter);
      ctx.globalAlpha = a;
      ctx.drawImage(S.glow.gold, b.x - dGlow / 2, b.y - dGlow / 2, dGlow, dGlow);
      ctx.globalAlpha = a;
      ctx.drawImage(S.core, b.x - dCore / 2, b.y - dCore / 2, dCore, dCore);
      bglow(S.glow.gold, b.x, b.y, pitch * 4.2, 0.55 * a);
      continue;
    }

    const glow = S.glow[b.color] ?? S.glow.amber;
    const s = (b.radius || 8) / 8;
    const pop = Math.min(1, 0.45 + age * 6.5);
    const long = b.shape === 'longball';

    // 拖尾：余烬点链（寿命内逐点变小变暗，报告 §2.3-B）
    const tr = b._tr;
    if (trailN && tr && tr.length >= 2) {
      const m = Math.min(tr.length / 2, trailN);
      for (let k = 0; k < m; k++) {
        const i = tr.length - 2 - 2 * k;
        const d = 24 * s * Math.pow(0.88, k);
        ctx.globalAlpha = 0.62 * Math.pow(0.72, k) * pop;
        ctx.drawImage(glow, tr[i] - d / 2, tr[i + 1] - d / 2, d, d);
      }
    }

    if (long) {
      // 长弹：沿飞行方向拉长 2.3×（弹墙那种米粒弹）
      ctx.save();
      ctx.translate(b.x, b.y);
      ctx.rotate(b.angle);
      ctx.globalAlpha = pop;
      ctx.drawImage(glow, -22 * s, -11 * s, 44 * s, 22 * s);
      ctx.globalAlpha = pop;
      ctx.drawImage(S.core, -8 * s, -3.5 * s, 16 * s, 7 * s);
      ctx.restore();
      bglow(glow, b.x, b.y, 76 * s, 0.45 * pop);
      continue;
    }

    const D = 34 * s * (0.8 + 0.2 * pop);
    if (lod > 0) {   // L1 外泛光（最大最淡）
      ctx.globalAlpha = 0.20 * pop;
      ctx.drawImage(glow, b.x - D * 0.95, b.y - D * 0.95, D * 1.9, D * 1.9);
    }
    ctx.globalAlpha = pop;                                  // L2/L3 主光晕 + 亮盘
    ctx.drawImage(glow, b.x - D / 2, b.y - D / 2, D, D);
    ctx.globalAlpha = pop;                                  // L4 纯白实心核
    const cd = 7.5 * s;
    ctx.drawImage(S.core, b.x - cd / 2, b.y - cd / 2, cd, cd);
    bglow(glow, b.x, b.y, 92 * s, 0.34 * pop);
  }
}

// ============================================================
// 4. 双猫立绘：4× 最近邻 + 呼吸 + sin 漂浮 + 彩色光晕
// ============================================================
export function drawCats(ctx, hf) {
  const S = sprites();
  const t = hf.t ?? 0;
  if (t < 0) return;
  const fade = Math.min(1, t / 1.0);
  const A = hf.constructor.ARENA;

  // 分裂过渡：合并时两只重叠在中间，分裂后缓动飞向各自站位
  const splitU = hf.splitAt != null ? Math.min(1, (t - hf.splitAt) / 0.9) : (hf.merged ? 0 : 1);
  const ease = 1 - Math.pow(1 - splitU, 3);

  ctx.save();
  ctx.globalAlpha = fade;
  for (const who of ['vanilla', 'choco']) {   // 先画香草后画巧克力，合并时巧克力在前
    const cat = hf.cats[who];
    if (!cat) continue;
    const ph = who === 'choco' ? 0 : 2.2;
    const bobY = Math.sin(t * 1.9 + ph) * 11;
    const swayX = Math.sin(t * 1.1 + ph * 0.7) * 7;
    const mx = A.cx + (who === 'choco' ? -14 : 14);
    const my = cat.y > 0 ? cat.y : cat.y;
    const x = (hf.merged ? mx : mx + (cat.x - mx) * ease) + swayX;
    const y = cat.y + bobY;

    const fdef = hf.frames(who)?.[cat.frame] ?? { size: [21, 36] };
    const img = hf.frameImg(who, cat.frame);
    const k = 4;
    const sw = fdef.size[0] * k, sh = fdef.size[1] * k;
    const breathe = 1 + 0.028 * Math.sin(t * 2.4 + ph);   // 呼吸：纵向轻微伸缩，脚底不动

    // 背后的彩色光晕（巧克力暖橙 / 香草冷白紫），加法
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = (0.30 + 0.08 * Math.sin(t * 2.6 + ph)) * fade;
    const gk = who === 'choco' ? S.glow.amber : S.glow.violet;
    ctx.drawImage(gk, x - 130, y - 130, 260, 260);
    ctx.restore();

    if (img) {
      ctx.save();
      ctx.imageSmoothingEnabled = false;            // 像素锐利，不糊
      ctx.translate(x, y + sh / 2);                 // 以脚底为锚
      ctx.scale(1, breathe);
      ctx.drawImage(img, -sw / 2, -sh, sw, sh);
      ctx.restore();
    } else {
      // 素材没载入时的兜底：简易猫头（保证永远有东西可看）
      ctx.save();
      ctx.fillStyle = who === 'choco' ? '#8a4b2a' : '#f3ead9';
      ctx.beginPath(); ctx.arc(x, y, 30, 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.moveTo(x - 28, y - 10); ctx.lineTo(x - 22, y - 44); ctx.lineTo(x - 4, y - 26); ctx.fill();
      ctx.beginPath(); ctx.moveTo(x + 28, y - 10); ctx.lineTo(x + 22, y - 44); ctx.lineTo(x + 4, y - 26); ctx.fill();
      ctx.restore();
    }
  }

  // 合并蓄力：绕身旋转的光珠环（分裂前的"蓄势"）
  if (hf.merged && hf.phaseIdx >= 5) {
    ctx.globalCompositeOperation = 'lighter';
    const cx = A.cx, cy = hf.cats.choco.y + 10;
    const u = Math.min(1, (hf.phaseT ?? 0) / 2.5);
    for (let i = 0; i < 10; i++) {
      const a = t * (2 + u * 5) + (i * TAU) / 10;
      const r = 90 - 30 * u;
      ctx.globalAlpha = 0.5 + 0.5 * u;
      ctx.drawImage(S.glow.amber, cx + Math.cos(a) * r - 16, cy + Math.sin(a) * r * 0.6 - 16, 32, 32);
    }
  }
  ctx.restore();
}

// ============================================================
// 5. HUD（屏幕空间）：像素字招式名 / 底部双猫血条 / 头像 / 暗角 / 黑屏换场
// ============================================================
const GREEN_STOPS = [[0, '#E9F08C'], [0.35, '#D8DA78'], [0.7, '#B9C33F'], [1, '#6A7F00']];
const RED_STOPS = [[0, '#B01A05'], [0.55, '#8E150C'], [1, '#5E0B04']];

function vgrad(ctx, y, h, stops) {
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  for (const [p, c] of stops) g.addColorStop(p, c);
  return g;
}

function drawBar(ctx, x, y, w, h, frac, label, count, sc, dpr, face) {
  frac = Math.max(0, Math.min(1, frac));
  // 外框：2px 黑 + 内侧 1px 白（报告 §4.3）
  ctx.fillStyle = '#000';
  ctx.fillRect(x - 2 * sc, y - 2 * sc, w + 4 * sc, h + 4 * sc);
  ctx.fillStyle = vgrad(ctx, y, h, RED_STOPS);
  ctx.fillRect(x, y, w, h);
  const fw = w * frac;
  if (fw > 0.5) {
    ctx.fillStyle = vgrad(ctx, y, h, GREEN_STOPS);
    ctx.beginPath();
    // 填充分界处做 5px 斜切（不直上直下）
    const slant = Math.min(5 * sc, fw);
    ctx.moveTo(x, y); ctx.lineTo(x + fw, y); ctx.lineTo(x + fw - slant, y + h); ctx.lineTo(x, y + h);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.55)';       // 顶部 1px 高光
    ctx.fillRect(x, y, Math.max(0, fw - slant * 0.5), Math.max(1, sc));
  }
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = Math.max(1, sc);
  ctx.strokeRect(x + 0.5 * sc, y + 0.5 * sc, w - sc, h - sc);

  // 标签：白字 1px 黑边，居中于条上方 3px
  const lab = textSprite(label, { cell: 1.35 * sc, advance: 8.6 * sc, outline: Math.max(1, sc), dpr });
  ctx.drawImage(lab.canvas, Math.round(x + w / 2 - lab.w / 2), Math.round(y - 3 * sc - lab.h), lab.w, lab.h);

  // 左侧计数框 Xnn：黑底 1px 白框
  const bw = 44 * sc, bh = 22 * sc, bx = x - bw - 8 * sc, by = y - 2 * sc;
  ctx.fillStyle = '#000';
  ctx.fillRect(bx, by, bw, bh);
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = Math.max(1, sc);
  ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
  const cs = textSprite(count, { cell: 2 * sc, advance: 12 * sc, outline: 0, dpr });
  ctx.drawImage(cs.canvas, Math.round(bx + bw / 2 - cs.w / 2), Math.round(by + bh / 2 - cs.h / 2), cs.w, cs.h);

  // 头像：压在血条右端外侧的圆（2px 白环 + 黑影）
  if (face) {
    const r = 21 * sc, fx = x + w + 30 * sc, fy = y + h / 2;
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.beginPath(); ctx.arc(fx + 2 * sc, fy + 3 * sc, r + 2 * sc, 0, TAU); ctx.fill();
    ctx.beginPath(); ctx.arc(fx, fy, r, 0, TAU); ctx.clip();
    ctx.fillStyle = face.bg;
    ctx.fillRect(fx - r, fy - r, r * 2, r * 2);
    if (face.img) {
      ctx.imageSmoothingEnabled = false;
      const k = 3.1 * sc;
      ctx.drawImage(face.img, fx - face.w * k / 2, fy - r + 1 * sc, face.w * k, face.h * k);
    }
    ctx.restore();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2 * sc;
    ctx.beginPath(); ctx.arc(fx, fy, r, 0, TAU); ctx.stroke();
  }
}

export function drawHud(ctx, hf, W, H, dpr) {
  const S = sprites();
  const t = hf.t ?? 0;
  const sc = Math.min(W / 1280, H / 720);

  ctx.save();
  ctx.imageSmoothingEnabled = false;

  if (t >= 0) {
    // ---- 左上：当前招式名（像素宽字距，白 #F8F8F8 + 黑描边 2px，无发光）----
    const name = hf.curSpell;
    if (name) {
      const ts = textSprite(name, { cell: 1.6 * sc, advance: 17.5 * sc, outline: 2 * sc, dpr });
      const since = hf.spellT ?? 9;
      // 换招瞬间闪烁两下，之后常亮（原版标题是常驻的）
      ctx.globalAlpha = since < 0.6 ? (Math.floor(since * 14) % 2 ? 0.35 : 1) : 1;
      // 游戏自带 DOM 顶栏(hud-top)是满宽 69px，标题放 y=26 会被它盖掉前半截（实测只剩 INCTION），下移到 y=80
      ctx.drawImage(ts.canvas, Math.round(26 * sc), 80, ts.w, ts.h);
      ctx.globalAlpha = 1;
    }

    // ---- 底部：两行双猫血条（演出进度当血量：随演出推进掉光）----
    const total = hf.totalShow || 95;
    const prog = Math.max(0, 1 - t / total);
    const pw = 532 * sc, ph = 18 * sc;
    const x0 = W - (1280 - 556) * sc;
    // 让开游戏自己的 DOM 底栏(hud-bottom 116px)：第二条血条的下沿必须在 H-122 以上
    const yBase = H - 122 - 66 * sc;
    const vimg = hf.frameImg('vanilla', 0), cimg = hf.frameImg('choco', 0);
    const vd = hf.frames('vanilla')?.[0]?.size ?? [21, 36];
    const cd = hf.frames('choco')?.[0]?.size ?? [21, 36];
    drawBar(ctx, x0, yBase, pw, ph, prog, 'HALLOWEEN CAT VANILLA SPECIAL',
      `X${Math.ceil(prog * 50) + 1}`, sc, dpr, { img: vimg, w: vd[0], h: vd[1], bg: '#3a2a52' });
    drawBar(ctx, x0, yBase + 44 * sc, pw, ph, Math.pow(prog, 1.12), 'HALLOWEEN CAT CHOCOLATE SPECIAL',
      `X${Math.ceil(Math.pow(prog, 1.12) * 50) + 2}`, sc, dpr, { img: cimg, w: cd[0], h: cd[1], bg: '#5a2e14' });
  }

  // ---- 分裂白闪（加法）----
  if ((hf.flash ?? 0) > 0) {
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = Math.min(0.75, hf.flash);
    ctx.fillStyle = '#FFE9B0';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';
  }

  // ---- 暗角（预烘焙）----
  const vkey = `${W}x${H}`;
  if (!S.vig || S.vigKey !== vkey) {
    const c = mk(W, H);
    const g = c.getContext('2d');
    const r = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.42, W / 2, H / 2, Math.hypot(W, H) * 0.55);
    r.addColorStop(0, 'rgba(0,0,0,0)');
    r.addColorStop(1, 'rgba(0,0,0,0.38)');
    g.fillStyle = r;
    g.fillRect(0, 0, W, H);
    S.vig = c;
    S.vigKey = vkey;
  }
  ctx.globalAlpha = 1;
  ctx.drawImage(S.vig, 0, 0, W, H);

  // ---- 黑屏换场：t<0 全黑，t 过 0 后 1.2s 内渐亮（用户："芙兰撤场3秒黑屏…场景变亮之后放bgm"）----
  const black = t < 0 ? 1 : Math.max(0, 1 - t / 1.2);
  if (black > 0) {
    ctx.globalAlpha = black;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
  }
  ctx.restore();
}
