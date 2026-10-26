// 敌弹视觉语言 v2（第一百八十五轮 · 全局美化第 1 步）
//
// 用户："远程小怪的弹幕表现力变好一些，目前只有一个圆蛋" + "芙兰……每张符卡都看不出来是原版的卡……
// 能华丽就华丽"。依据 FLANDRE_13符卡_Canvas2D视觉参数书 §0/§2：
//   · 所有弹 = 白核 + 同色饱和边框 + 同色柔光（不是纯色实心球）
//   · 白核中心 #FFFFFF → 边缘 #FFF6F0；核与边框之间有一圈深色过渡（TH06 的特征）
//   · 柔光走加法(lighter)，密处自然过曝成白；判定远小于视觉（见 hitRadius，逻辑层不动）
//   · 预烘焙精灵 + drawImage 缩放，不逐弹建渐变，不用 shadowBlur
//
// 弹型（shape）：
//   orb    圆弹 S          rice   米弹 R（= 旧 longball）     shard  碎片/菱形（= 旧 crystal）
//   drop   水滴（酸液）     star   五角星                      petal  花瓣/叶片
//   ring   空心环           blade  弯刃（新月形）
//   jade / laser 仍走旧渲染（大玉带红色判定圈、水柱是专用光束，由 renderer 保留）
//
// 使用：renderer 每帧开头 beginFrame(n)，之后逐弹 drawEnemyBullet(ctx, b)。
import { MOBILE } from '../core/mobile.js';
import { makeCanvas } from './no-dom.js';

const TAU = Math.PI * 2;
const SZ = 64;          // 精灵画布边长（弹型统一朝 +x，中心 (32,32)）
const BODY_R = 15;      // 精灵里"本体半径"基准（orb 的半径；其余弹型以此为尺）

let _lod = 2;           // 2 = 拖尾+柔光 / 1 = 只柔光 / 0 = 只本体
export const LOD_STEP1 = 340, LOD_STEP0 = 520, LOD_HYST = 40;
/**
 * 按弹量选 LOD。
 * 第一百九十九轮（用户："子弹经常一闪一闪的，应该是你设置的子弹过多把特效渲染砍了的效果，芙兰战不用生效这个，
 * 整体已经很流畅了……不是关闭LOD是改成全部生效LOD，然后不要因为弹幕多关闭它，让它一直存在"）：
 *  · forceFull = 芙兰战：永远全质量（LOD 2：拖尾 + 柔光），不随弹量降档；
 *  · 其它战斗仍按弹量降档以保帧率，但加【迟滞】：降档要超过阈值 +40，升回要低于阈值 −40。
 *    原来弹数在 340/520 上下波动时每帧切档，所有弹的拖尾/柔光整片闪烁。
 */
export function beginFrame(bulletCount, forceFull = false) {
  if (forceFull) { _lod = 2; return; }
  const n = bulletCount;
  if (_lod === 2) { if (n > LOD_STEP1 + LOD_HYST) _lod = n > LOD_STEP0 + LOD_HYST ? 0 : 1; }
  else if (_lod === 1) { if (n > LOD_STEP0 + LOD_HYST) _lod = 0; else if (n < LOD_STEP1 - LOD_HYST) _lod = 2; }
  else if (n < LOD_STEP0 - LOD_HYST) _lod = n < LOD_STEP1 - LOD_HYST ? 2 : 1;
}
export function currentLod() { return _lod; }

// ---------------------------------------------------------------
// 颜色工具
// ---------------------------------------------------------------
function parse(c) {
  if (typeof c !== 'string') return [255, 64, 96];
  let h = c.trim();
  if (h[0] === '#') {
    if (h.length === 4) h = '#' + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
    const n = parseInt(h.slice(1, 7), 16);
    if (Number.isFinite(n)) return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m = /rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)/i.exec(h);
  if (m) return [+m[1], +m[2], +m[3]];
  return [255, 64, 96];
}
const mixc = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const css = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

function mk(w, h) {
  const c = makeCanvas();
  c.width = w; c.height = h;
  return c;
}

// ---------------------------------------------------------------
// 精灵烘焙（按 shape|color 缓存，首次遇到时烘一次）
// ---------------------------------------------------------------
const bodyCache = new Map();
const glowCache = new Map();

/** 白核 → 浅边 → 饱和边框 → 深色过渡 的径向填充（以 (cx,cy) 为心，半径 r） */
function bodyFill(g, cx, cy, r, col) {
  const light = mixc(col, [255, 255, 255], 0.45);
  const dark = mixc(col, [20, 0, 10], 0.45);
  const edge = mixc(col, [20, 0, 10], 0.7);
  const grad = g.createRadialGradient(cx, cy, 0, cx, cy, r);
  grad.addColorStop(0, '#FFFFFF');
  grad.addColorStop(0.36, '#FFFFFF');
  grad.addColorStop(0.46, '#FFF6F0');
  grad.addColorStop(0.58, css(light, 1));
  grad.addColorStop(0.70, css(col, 1));
  grad.addColorStop(0.88, css(dark, 1));
  grad.addColorStop(0.97, css(edge, 1));
  grad.addColorStop(1, css(edge, 0));
  return grad;
}

function bakeBody(shape, col) {
  const c = mk(SZ, SZ);
  const g = c.getContext('2d');
  const R = BODY_R;
  g.translate(32, 32);
  switch (shape) {
    case 'rice': {            // 米弹：同一套渐变沿长轴拉长 → 核/边框自然成椭圆
      g.save();
      g.scale(22 / R, 8.5 / R);
      g.fillStyle = bodyFill(g, 0, 0, R, col);
      g.beginPath(); g.arc(0, 0, R, 0, TAU); g.fill();
      g.restore();
      break;
    }
    case 'drop': {            // 水滴：头圆在前(+x)，尾尖在后
      g.fillStyle = bodyFill(g, 6, 0, 17, col);
      g.beginPath();
      g.moveTo(-26, 0);
      g.bezierCurveTo(-14, 0, -4, -10, 6, -10);
      g.arc(6, 0, 10, -Math.PI / 2, Math.PI / 2);
      g.bezierCurveTo(-4, 10, -14, 0, -26, 0);
      g.closePath(); g.fill();
      break;
    }
    case 'star': {
      g.fillStyle = bodyFill(g, 0, 0, 17, col);
      g.beginPath();
      for (let i = 0; i < 10; i++) {
        const rr = i % 2 ? 7.5 : 17, a = -Math.PI / 2 + (i * Math.PI) / 5;
        g[i ? 'lineTo' : 'moveTo'](Math.cos(a) * rr, Math.sin(a) * rr);
      }
      g.closePath(); g.fill();
      break;
    }
    case 'petal': {           // 叶片/花瓣：两条二次曲线
      g.fillStyle = bodyFill(g, -2, 0, 24, col);
      g.beginPath();
      g.moveTo(-24, 0);
      g.quadraticCurveTo(-2, -19, 24, 0);
      g.quadraticCurveTo(-2, 19, -24, 0);
      g.closePath(); g.fill();
      break;
    }
    case 'shard': {           // 碎片：风筝形，尖头朝前
      g.fillStyle = bodyFill(g, -2, 0, 24, col);
      g.beginPath();
      g.moveTo(25, 0); g.lineTo(-6, -11); g.lineTo(-24, 0); g.lineTo(-6, 11);
      g.closePath(); g.fill();
      break;
    }
    case 'ring': {            // 空心环：白色内沿 → 彩色环身
      const grad = g.createRadialGradient(0, 0, 7, 0, 0, 16);
      grad.addColorStop(0, css(col, 0));
      grad.addColorStop(0.12, '#FFFFFF');
      grad.addColorStop(0.38, '#FFF6F0');
      grad.addColorStop(0.62, css(col, 1));
      grad.addColorStop(0.9, css(mixc(col, [20, 0, 10], 0.5), 1));
      grad.addColorStop(1, css(mixc(col, [20, 0, 10], 0.7), 0));
      g.fillStyle = grad;
      g.beginPath(); g.arc(0, 0, 16, 0, TAU); g.fill();
      break;
    }
    case 'blade': {           // 弯刃：大圆减小圆 = 新月，凸面朝前
      g.fillStyle = bodyFill(g, 4, 0, 22, col);
      g.beginPath(); g.arc(4, 0, 21, 0, TAU); g.fill();
      g.globalCompositeOperation = 'destination-out';
      g.beginPath(); g.arc(-7, 0, 18, 0, TAU); g.fill();
      break;
    }
    case 'orb':
    default: {
      g.fillStyle = bodyFill(g, 0, 0, R, col);
      g.beginPath(); g.arc(0, 0, R, 0, TAU); g.fill();
      // 左上一点高光（报告 §2：渲染顺序最后一步）
      g.fillStyle = 'rgba(255,255,255,0.85)';
      g.beginPath(); g.arc(-4.5, -4.5, 1.8, 0, TAU); g.fill();
    }
  }
  return c;
}

function bakeGlow(col) {
  const c = mk(SZ, SZ);
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  const hot = mixc(col, [255, 255, 255], 0.25);
  grad.addColorStop(0, css(hot, 0.62));
  grad.addColorStop(0.35, css(col, 0.34));
  grad.addColorStop(0.7, css(mixc(col, [20, 0, 10], 0.3), 0.1));
  grad.addColorStop(1, css(col, 0));
  g.fillStyle = grad;
  g.fillRect(0, 0, SZ, SZ);
  return c;
}

function getBody(shape, color) {
  const key = shape + '|' + color;
  let s = bodyCache.get(key);
  if (!s) {
    s = bakeBody(shape, parse(color));
    if (bodyCache.size > 160) bodyCache.clear();   // 彩虹/多色符卡也封顶，防无界增长
    bodyCache.set(key, s);
  }
  return s;
}
function getGlow(color) {
  let s = glowCache.get(color);
  if (!s) {
    s = bakeGlow(parse(color));
    if (glowCache.size > 160) glowCache.clear();
    glowCache.set(color, s);
  }
  return s;
}

// 旧 shape 名映射到新弹型
const ALIAS = { longball: 'rice', crystal: 'shard' };
// 这些弹型有朝向（沿飞行方向）；其余（orb/star/ring）不转或自转
const ORIENTED = new Set(['rice', 'drop', 'petal', 'shard', 'blade']);

/**
 * 画一颗敌弹（世界坐标，调用方已 translate 到世界）。
 * b: EnemyBullet（x,y,angle,radius,color,shape,rotation）
 */
export function drawEnemyBullet(ctx, b) {
  const shape = ALIAS[b.shape] ?? b.shape ?? 'orb';
  const color = b.color ?? '#ff4060';
  const body = getBody(shape, color);
  const r = b.radius ?? 6;
  // 本体基准半径 BODY_R(=15) 对应视觉半径 r×1.12 → 整张精灵边长
  const D = (r * 1.12 * SZ) / BODY_R;
  const age = b.age ?? 1;
  const pop = age < 0.1 ? 0.55 + age * 4.5 : 1;     // 出生 0.1s 内由小变大（"弹出"感）

  // 柔光（加法）：LOD≥1。移动端不用 lighter，改成淡淡的 source-over。
  if (_lod >= 1) {
    const gl = getGlow(color);
    const GD = D * 1.9 * pop;
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
    ctx.globalAlpha = MOBILE ? 0.22 : 0.62;
    ctx.drawImage(gl, b.x - GD / 2, b.y - GD / 2, GD, GD);
    // 余烬尾（LOD=2）：弹是匀速直线，直接按 angle 往回摆两个渐淡的小光点，O(1) 无历史
    if (_lod >= 2 && !MOBILE) {
      const ca = Math.cos(b.angle), sa = Math.sin(b.angle);
      for (let k = 1; k <= 2; k++) {
        const d = r * (1.5 + k * 1.5), TD = D * (0.95 - k * 0.2) * pop;
        ctx.globalAlpha = 0.34 / k;
        ctx.drawImage(gl, b.x - ca * d - TD / 2, b.y - sa * d - TD / 2, TD, TD);
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  // 本体
  ctx.globalAlpha = 1;
  const BD = D * pop;
  if (shape === 'orb') {
    ctx.drawImage(body, b.x - BD / 2, b.y - BD / 2, BD, BD);
  } else {
    ctx.save();
    ctx.translate(b.x, b.y);
    ctx.rotate(ORIENTED.has(shape) ? b.angle : (b.rotation ?? 0) * 0.5);
    ctx.drawImage(body, -BD / 2, -BD / 2, BD, BD);
    ctx.restore();
  }
}

/**
 * 批量绘制（第一百九十九轮）。
 *
 * 为什么：芙兰战改为"永远全质量"(LOD 2) 之后，几百发弹就把帧时间推到 16~20ms（maze p95 30ms）。
 * 逐发绘制时每发弹要 2 次 globalCompositeOperation 切换 + 3 次加法 drawImage + 非圆形弹的 save/translate/rotate/restore，
 * 混合模式切换与 save/restore 是 canvas 里最贵的操作，几百发被放大几百倍。
 * 做法：①两遍绘制 —— 第一遍加法混合一次画完所有弹的柔光/余烬点，第二遍普通混合画本体，混合模式切换从 O(弹数) 降到每帧 2 次；
 *       ②旋转用一次 setTransform 直接合成（基准矩阵 × 平移 × 旋转），不用 save/restore；
 *       ③精灵引用缓存在弹对象上（b._fxB / b._fxG），不再每发每帧做字符串拼接 + Map 查找。
 * 画面与 drawEnemyBullet 逐像素一致（同样的精灵、透明度、尺寸），只是调用方式不同；LOD 语义不变。
 */
export function drawBatch(ctx, list) {
  const n = list.length;
  if (!n) return;
  const lod = _lod, mob = MOBILE;
  // —— 预处理：精灵引用 + 尺寸（每发一次，缓存在对象上）——
  for (let i = 0; i < n; i++) {
    const b = list[i];
    if (b._fxK !== b.shape || b._fxC !== b.color) {
      const shape = ALIAS[b.shape] ?? b.shape ?? 'orb';
      const color = b.color ?? '#ff4060';
      b._fxK = b.shape; b._fxC = b.color; b._fxS = shape;
      b._fxB = getBody(shape, color); b._fxG = getGlow(color);
      b._fxD = ((b.radius ?? 6) * 1.12 * SZ) / BODY_R;
      b._fxO = ORIENTED.has(shape);
    }
  }
  // —— 第一遍：柔光 + 余烬尾（加法，一次切换）——
  if (lod >= 1) {
    ctx.globalCompositeOperation = mob ? 'source-over' : 'lighter';
    ctx.globalAlpha = mob ? 0.22 : 0.62;
    for (let i = 0; i < n; i++) {
      const b = list[i], age = b.age ?? 1, pop = age < 0.1 ? 0.55 + age * 4.5 : 1, GD = b._fxD * 1.9 * pop;
      ctx.drawImage(b._fxG, b.x - GD / 2, b.y - GD / 2, GD, GD);
    }
    if (lod >= 2 && !mob) {
      for (let k = 1; k <= 2; k++) {
        ctx.globalAlpha = 0.34 / k;
        for (let i = 0; i < n; i++) {
          const b = list[i], age = b.age ?? 1, pop = age < 0.1 ? 0.55 + age * 4.5 : 1;
          const r = b.radius ?? 6, d = r * (1.5 + k * 1.5), TD = b._fxD * (0.95 - k * 0.2) * pop;
          const ca = Math.cos(b.angle), sa = Math.sin(b.angle);
          ctx.drawImage(b._fxG, b.x - ca * d - TD / 2, b.y - sa * d - TD / 2, TD, TD);
        }
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }
  // —— 第二遍：本体 ——
  ctx.globalAlpha = 1;
  const M = ctx.getTransform();
  const ma = M.a, mb = M.b, mc = M.c, md = M.d, me = M.e, mf = M.f;
  let rotated = false;
  for (let i = 0; i < n; i++) {
    const b = list[i], age = b.age ?? 1, BD = b._fxD * (age < 0.1 ? 0.55 + age * 4.5 : 1);
    if (b._fxS === 'orb') {
      if (rotated) { ctx.setTransform(ma, mb, mc, md, me, mf); rotated = false; }
      ctx.drawImage(b._fxB, b.x - BD / 2, b.y - BD / 2, BD, BD);
    } else {
      const th = b._fxO ? b.angle : (b.rotation ?? 0) * 0.5, c = Math.cos(th), s = Math.sin(th);
      ctx.setTransform(ma * c + mc * s, mb * c + md * s, -ma * s + mc * c, -mb * s + md * c, ma * b.x + mc * b.y + me, mb * b.x + md * b.y + mf);
      rotated = true;
      ctx.drawImage(b._fxB, -BD / 2, -BD / 2, BD, BD);
    }
  }
  if (rotated) ctx.setTransform(ma, mb, mc, md, me, mf);
}

/** 测试/诊断用：当前缓存规模 */
export function _cacheSize() { return { body: bodyCache.size, glow: glowCache.size }; }
