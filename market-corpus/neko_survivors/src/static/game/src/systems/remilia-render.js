// ============================================================
// 蕾米莉亚终演出 · 渲染层（第二百零四轮重做）
//
// 用户反馈（逐条对应）：
//  "3，每隔几秒钟背景的芙兰图会闪烁一次。"      → 见下方"闪烁"两处修复（翅膀呼吸闸门 + 帧高归一化）
//  "5，弹幕会丢失渲染，弹幕的发光有时候会消失，把他维持全局。" → LOD 在 renderer 里对蕾米战强制全质量
//  "7，场景也许可以试试还原？……背景只有一个超级大的血月慢慢旋转，背景旁边是红魔馆的时钟塔。"
//        → tower.png / moonskin.png 都是从《东方红魔乡》新典六面标题画（frame_stage6）里抠出来的真素材
//  "8，蕾米和芙兰没有背后的魔法阵……你可以直接用我们自己的芙兰背后魔法阵。"
//        → sigil.png = 芙兰 Boss 的 magic_circle_86_hd（720²），按 MAGIC-CIRCLE.md 的原版配方双层反向旋转
//
// 素材：assets/boss/remilia/
//   remilia_00..23.png  24 帧战斗精灵
//   tower.png           时钟塔（抠好透明通道，底部渐隐）
//   moonskin.png        月面贴图（旋转用）
//   sigil.png           魔法阵
//
// 205 轮改动：
//   · 删掉符卡 cut-in（那张图就是 eff07，和芙兰战空气墙标记同一张，用户每 5 秒看到一次以为是残留 bug）
//   · 删掉头顶名字牌（用户："认识的都认识，不认识的标了也不认识"）
//   · 蕾米精灵再缩小一档，让她落在魔法阵里面（原来翼展比阵还大一圈）
// ============================================================
import { makeCanvas } from './no-dom.js';

const TAU = Math.PI * 2;

// ---- 贴图 ----
const IMG = { frames: [], tower: null, moon: null, sigil: null };
function loadArt() {
  if (typeof Image === 'undefined') return;
  if (!IMG.frames.length) {
    for (let i = 0; i < 24; i++) {
      const im = new Image();
      im.src = `assets/boss/remilia/remilia_${String(i).padStart(2, '0')}.png`;
      IMG.frames.push(im);
    }
  }
  const one = (key, file) => {
    if (IMG[key]) return;
    const im = new Image();
    im.src = `assets/boss/remilia/${file}`;
    IMG[key] = im;
  };
  one('tower', 'tower.png');
  one('moon', 'moonskin.png');
  one('sigil', 'sigil.png');
}
const ready = (im) => im && im.naturalWidth > 0;

// ---- 预烘焙 ----
let S = null;
function ensure() {
  if (typeof document === 'undefined') return false;
  if (S) return true;
  const mk = (w, h) => { const c = makeCanvas(); c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h)); return c; };
  const soft = (size, stops) => {
    const c = mk(size, size);
    const g = c.getContext('2d');
    const gr = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    for (const [o, col] of stops) gr.addColorStop(o, col);
    g.fillStyle = gr;
    g.fillRect(0, 0, size, size);
    return c;
  };
  S = {
    aura: soft(128, [[0, 'rgba(255,90,130,0.55)'], [0.45, 'rgba(200,30,60,0.22)'], [1, 'rgba(140,10,40,0)']]),
    auraF: soft(128, [[0, 'rgba(255,200,140,0.5)'], [0.45, 'rgba(255,120,160,0.2)'], [1, 'rgba(200,60,110,0)']]),
    shadow: soft(96, [[0, 'rgba(10,0,10,0.55)'], [0.6, 'rgba(10,0,10,0.22)'], [1, 'rgba(10,0,10,0)']]),
    backing: soft(96, [[0, 'rgba(18,0,10,0.62)'], [0.55, 'rgba(18,0,10,0.3)'], [1, 'rgba(18,0,10,0)']]),
    fog: soft(160, [[0, 'rgba(190,30,60,0.28)'], [0.55, 'rgba(150,15,45,0.12)'], [1, 'rgba(120,0,30,0)']]),
    moonShade: null,
    bat: null,
    sigilCool: null,
    sigilWarm: null,
  };
  // 血月球体的明暗（中心偏暗 + 边缘微亮，压出球感）
  {
    const n = 256, c = mk(n, n), g = c.getContext('2d');
    const gr = g.createRadialGradient(n * 0.44, n * 0.42, n * 0.05, n / 2, n / 2, n / 2);
    gr.addColorStop(0, 'rgba(120,0,26,0.30)');
    gr.addColorStop(0.55, 'rgba(90,0,20,0.12)');
    gr.addColorStop(0.86, 'rgba(255,90,110,0.10)');
    gr.addColorStop(1, 'rgba(255,140,150,0)');
    g.fillStyle = gr; g.fillRect(0, 0, n, n);
    S.moonShade = c;
  }
  // 小蝙蝠
  {
    const c = mk(32, 20), g = c.getContext('2d');
    g.fillStyle = 'rgba(28,4,14,0.92)';
    g.beginPath();
    g.moveTo(16, 12);
    g.quadraticCurveTo(10, 2, 2, 5);
    g.quadraticCurveTo(9, 9, 16, 12);
    g.quadraticCurveTo(23, 9, 30, 5);
    g.quadraticCurveTo(22, 2, 16, 12);
    g.fill();
    g.beginPath(); g.ellipse(16, 13, 2.6, 3.6, 0, 0, TAU); g.fill();
    S.bat = c;
  }
  return true;
}

/**
 * 把白色的法阵染成目标色。
 * 206 轮修复：原来用 multiply + destination-in 两步合成，结果 alpha 没保住 ——
 * 画出来是两个半透明大方块（用户截图里那两块黄/紫方块就是它）。
 * 改成逐像素染色：直接读 ImageData，只乘 RGB，alpha 原样保留，绝不会把圆形裁成方块。
 */
function tintSigil(hex) {
  if (!ready(IMG.sigil)) return null;
  const n = 512;
  const c = makeCanvas(); c.width = n; c.height = n;
  const g = c.getContext('2d');
  g.drawImage(IMG.sigil, 0, 0, n, n);
  const h = hex.replace('#', '');
  const tr = parseInt(h.slice(0, 2), 16) / 255;
  const tg = parseInt(h.slice(2, 4), 16) / 255;
  const tb = parseInt(h.slice(4, 6), 16) / 255;
  try {
    const img = g.getImageData(0, 0, n, n);
    const px = img.data;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i + 3] === 0) continue;
      px[i] = Math.min(255, px[i] * tr);
      px[i + 1] = Math.min(255, px[i + 1] * tg);
      px[i + 2] = Math.min(255, px[i + 2] * tb);
    }
    g.putImageData(img, 0, 0);
  } catch {
    // getImageData 被跨域策略拦下（本地 file:// 打开才会）：退回纯色遮罩，
    // 至少不会出现方块 —— 用 source-in 只保留图片 alpha 形状。
    g.globalCompositeOperation = 'source-in';
    g.fillStyle = hex;
    g.fillRect(0, 0, n, n);
    g.globalCompositeOperation = 'source-over';
  }
  return c;
}

function art() {
  loadArt();
  ensure();
  if (S && !S.sigilCool && ready(IMG.sigil)) {
    S.sigilCool = tintSigil('#e0bfff');   // 17 号脚本：蓝紫，0.8×
    S.sigilWarm = tintSigil('#ffff20');   // 18 号脚本：黄，1.0×
  }
  return { IMG, S };
}

/** 预热 */
export function warm() { art(); }

// ---------------------------------------------------------------
// 背景：超大的血月慢慢旋转 + 红魔馆的时钟塔
// ---------------------------------------------------------------
export function drawBg(ctx, fight, ax, ay, W, H) {
  const { IMG: img, S: s } = art();
  const t = fight?.t ?? 0;

  // 底色：近黑的深红
  const base = ctx.createLinearGradient(0, 0, 0, H);
  base.addColorStop(0, '#1a0209');
  base.addColorStop(0.55, '#2a0510');
  base.addColorStop(1, '#120106');
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, W, H);

  // ---- 超大的血月：要"超级大"但必须读得出是一颗月亮（能看见边界）----
  const R = Math.min(W, H) * 0.80;
  const mx = ax + W * 0.26;
  const my = ay - H * 0.06;

  // 外圈光晕（很淡，只是让边缘柔一点）
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const halo = ctx.createRadialGradient(mx, my, R * 0.82, mx, my, R * 1.22);
  halo.addColorStop(0, 'rgba(255,60,80,0.14)');
  halo.addColorStop(1, 'rgba(180,10,40,0)');
  ctx.fillStyle = halo;
  ctx.fillRect(mx - R * 1.3, my - R * 1.3, R * 2.6, R * 2.6);
  ctx.restore();

  // 月面：圆形裁剪 + 缓慢旋转（用户："超级大的血月慢慢旋转"）
  ctx.save();
  ctx.beginPath();
  ctx.arc(mx, my, R, 0, TAU);
  ctx.clip();
  ctx.fillStyle = '#4a0812';                   // 底：深绯红（原来太亮，整屏被洗成粉）
  ctx.fillRect(mx - R, my - R, R * 2, R * 2);
  if (ready(img.moon)) {
    ctx.globalAlpha = 0.62;                    // 月面贴图压暗
    ctx.translate(mx, my);
    ctx.rotate(t * 0.035);                     // 约 3 分钟一圈，慢到不抢戏
    const S2 = R * 3.1;
    ctx.drawImage(img.moon, -S2 / 2, -S2 / 2, S2, S2);
    ctx.globalAlpha = 1;
  }
  // 圆内再压一层暗红（乘法感），把"月盘"和背景分开
  ctx.fillStyle = 'rgba(70,4,16,0.34)';
  ctx.fillRect(mx - R, my - R, R * 2, R * 2);
  ctx.restore();
  // 球体明暗
  if (s) ctx.drawImage(s.moonShade, mx - R, my - R, R * 2, R * 2);
  // 月缘：一圈细亮边，让圆盘轮廓明确
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = 'rgba(255,120,140,0.28)';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(mx, my, R - 1, 0, TAU);
  ctx.stroke();
  ctx.restore();

  // ---- 时钟塔（红魔馆 Big Ben，画在月亮前面）----
  if (ready(img.tower)) {
    const th = H * 1.06;
    const tw = th * (img.tower.naturalWidth / img.tower.naturalHeight);
    const tx = ax - W * 0.5 + tw * 0.16;       // 靠左
    const ty = ay + H * 0.5 - th * 0.98;
    ctx.save();
    ctx.globalAlpha = 0.96;
    ctx.drawImage(img.tower, tx, ty, tw, th);
    ctx.restore();
  }

  // ---- 红雾（三层慢飘）----
  if (s) {
    for (const [yy, sc, spd, a] of [[0.16, 1.5, 0.05, 0.45], [0.55, 2.0, -0.035, 0.36], [0.86, 1.7, 0.07, 0.30]]) {
      const RR = Math.min(W, H) * sc;
      for (let i = 0; i < 3; i++) {
        const x = ax + Math.sin(t * spd + i * 2.1) * W * 0.35 + (i - 1) * W * 0.34;
        const y = ay + (yy - 0.5) * H * 0.9 + Math.cos(t * spd * 1.3 + i) * 28;
        ctx.globalAlpha = a;
        ctx.drawImage(s.fog, x - RR / 2, y - RR / 2, RR, RR);
      }
    }
    ctx.globalAlpha = 1;
  }
}

// ---------------------------------------------------------------
export function drawBats(ctx, fight, ax, ay) {
  const { S: s } = art();
  if (!s) return;
  const t = fight?.t ?? 0;
  ctx.save();
  for (const b of (fight?.bats ?? [])) {
    const R = 560 * b.r;
    const x = ax + Math.cos(b.a) * R;
    const y = ay + Math.sin(b.a) * R * 0.55;
    const flap = 0.55 + 0.45 * Math.sin(t * 13 + b.ph);
    const w = 26 * b.s, h = w * 0.62 * flap;
    ctx.globalAlpha = 0.55 + 0.35 * Math.sin(t * 2 + b.ph);
    ctx.drawImage(s.bat, x - w / 2, y - h / 2, w, h);
  }
  ctx.restore();
}

// ---------------------------------------------------------------
// 背后的魔法阵（原版配方：冷色 0.8× 正向 + 暖色 1.0× 反向，加法混合）
// ---------------------------------------------------------------
export function drawSigil(ctx, x, y, radius, k, t) {
  const { S: s } = art();
  if (!s || !s.sigilCool || k <= 0.01) return;
  const a = Math.max(0, Math.min(1, k));
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.translate(x, y);

  // 冷色层：0.8×，+0.026 rad/帧
  ctx.save();
  ctx.globalAlpha = a * 0.85;
  ctx.rotate(t * 1.56);
  const r1 = radius * 0.8 * 1.25;
  ctx.drawImage(s.sigilCool, -r1 / 2, -r1 / 2, r1, r1);
  ctx.restore();

  // 暖色层：1.0×，−0.026 rad/帧（反向；叠加处加法混合自然偏白）
  ctx.save();
  ctx.globalAlpha = a * 0.7;
  ctx.rotate(-t * 1.56);
  const r2 = radius * 1.25;
  ctx.drawImage(s.sigilWarm, -r2 / 2, -r2 / 2, r2, r2);
  ctx.restore();

  ctx.restore();
}

// ---------------------------------------------------------------
// 双人
// ---------------------------------------------------------------
function drawAura(ctx, img, x, y, R, alpha) {
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.drawImage(img, x - R, y - R, R * 2, R * 2);
  ctx.globalAlpha = 1;
}

/** 两人共用的精灵半径（用户："蕾米太大只了，让她和芙兰一样大"） */
export const CHAR_R = 66;
/**
 * 蕾米精灵的额外缩放。
 * 用户（205 轮）："蕾米都大出魔法阵一大圈了，让她的大小在魔法阵大小左右"。
 * 她的 24 帧是按内容紧裁的，芙兰那张 256² 贴图里人物只占中间一部分 —— 同一个 CHAR_R
 * 画出来，蕾米视觉上会大出芙兰一截。乘 0.76 之后她就落在魔法阵里面了。
 */
const REMI_SPRITE_SCALE = 0.76;
/** 精灵帧的标称高度 —— 帧原始尺寸在 135~151 之间浮动，用它做统一标度 */
const NOMINAL_H = 150;

/** 蕾米 */
export function drawRemilia(ctx, fight) {
  const { IMG: img, S: s } = art();
  if (!s) return;
  const b = fight?.b?.remilia;
  if (!b) return;
  const t = fight?.t ?? 0;
  const R = CHAR_R;

  // 背后的魔法阵（用户："蕾米和芙兰没有背后的魔法阵"）
  const sig = b.sigil ?? 0;
  if (sig > 0) drawSigil(ctx, b.x, b.y, R * 1.9, sig, t);

  // 暗色背衬（在血月/密集弹幕上保证可读）
  ctx.save();
  ctx.globalAlpha = b.alpha ?? 1;
  ctx.drawImage(s.backing, b.x - R * 1.5, b.y - R * 1.5, R * 3, R * 3);
  ctx.restore();

  // 206 轮（用户："蕾米的左右两侧有2个小蝙蝠很奇怪，去掉"）：
  // 这里原本会把她自己的「纯蝙蝠翼」帧（20/21）叠在身后，画出来就是左右各一只小蝙蝠。
  // 整块删掉，不画了。

  drawAura(ctx, s.aura, b.x, b.y, R * 1.9, (0.30 + 0.06 * Math.sin(t * 2.6)) * (b.alpha ?? 1));

  // 本体：统一标度（不按每帧高度拉伸 —— 旧写法会让 135/151 的帧互相拉扯，看起来像在闪）
  const fr = img.frames[b.frame ?? 0];
  ctx.save();
  ctx.globalAlpha = b.alpha ?? 1;
  ctx.translate(b.x, b.y);
  if (ready(fr)) {
    const K = (R * 2.6 * REMI_SPRITE_SCALE) / NOMINAL_H;
    const w = fr.naturalWidth * K, h = fr.naturalHeight * K;
    ctx.drawImage(fr, -w / 2, -h * 0.60, w, h);
  } else {
    ctx.fillStyle = 'rgba(200,30,60,0.75)';
    ctx.beginPath(); ctx.arc(0, 0, R * 0.7, 0, TAU); ctx.fill();
  }
  ctx.restore();
}

/** 芙兰 */
export function drawFlandreSprite(ctx, fight, getSprite) {
  const { S: s } = art();
  if (!s) return;
  const b = fight?.b?.flandre;
  if (!b) return;
  const t = fight?.t ?? 0;
  const R = CHAR_R;

  const sig = b.sigil ?? 0;
  if (sig > 0) drawSigil(ctx, b.x, b.y, R * 1.9, sig, t);

  ctx.save();
  ctx.globalAlpha = b.alpha ?? 1;
  ctx.drawImage(s.backing, b.x - R * 1.5, b.y - R * 1.5, R * 3, R * 3);
  ctx.restore();

  drawAura(ctx, s.auraF, b.x, b.y, R * 1.85, (0.28 + 0.08 * Math.sin(t * 2.9 + 1)) * (b.alpha ?? 1));

  const img = getSprite?.(b.spriteId);
  ctx.save();
  ctx.globalAlpha = b.alpha ?? 1;
  if (ready(img)) {
    const hh = R * 2.6, ww = hh * (img.naturalWidth / img.naturalHeight);
    ctx.drawImage(img, b.x - ww / 2, b.y - hh * 0.62, ww, hh);
  } else {
    ctx.fillStyle = 'rgba(255,120,160,0.8)';
    ctx.beginPath(); ctx.arc(b.x, b.y, R * 0.68, 0, TAU); ctx.fill();
  }
  ctx.restore();
}

// ---------------------------------------------------------------
// 205 轮：符卡 cut-in 已删除。
// 那张图就是 flandre-stage7/eff07_nc_4k.png —— 与芙兰战里"标出空气墙范围"的那层暗红旋涡是同一张，
// 换段时全屏闪一下，用户每几秒看到一次，以为是芙兰战的特效没删干净。换段现在只留卡名横幅 + 红闪。
export function drawScreen(ctx, fight, W, H) {
  // 207 轮：换场过渡黑幕（黑屏期间全黑 → 进入正片 0.9 秒淡出），
  // 让"从芙兰战切过来"不那么生硬，也顺带盖住换场那一瞬间的姿势切换。
  const veil = fight?.fade ?? 0;
  if (veil > 0.01) {
    ctx.save();
    ctx.fillStyle = `rgba(6, 2, 10, ${Math.min(1, veil)})`;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
  }
  const f = fight?.flash ?? 0;
  if (f > 0.01) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = `rgba(255,40,80,${Math.min(0.42, f * 0.5)})`;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
  }
  const g = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.46, W / 2, H / 2, Math.max(W, H) * 0.8);
  g.addColorStop(0, 'rgba(0,0,0,0)');
  g.addColorStop(1, 'rgba(12,0,8,0.68)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

export function drawHud(ctx, fight, W, H) {
  const info = fight?.cardInfo ?? { name: '', idx: 0, n: 6 };
  const total = 60;
  const t = Math.max(0, fight?.t ?? 0);
  const prog = Math.min(1, t / total);
  ctx.save();
  const bw = Math.min(560, W * 0.42), bx = W / 2 - bw / 2, by = H - 96;
  ctx.fillStyle = 'rgba(20,4,16,0.72)';
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(bx - 6, by - 6, bw + 12, 22, 11); else ctx.rect(bx - 6, by - 6, bw + 12, 22);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,120,150,0.5)';
  ctx.lineWidth = 1.5;
  ctx.stroke();
  const gr = ctx.createLinearGradient(bx, 0, bx + bw, 0);
  gr.addColorStop(0, '#ff3050'); gr.addColorStop(0.5, '#ff7bac'); gr.addColorStop(1, '#ffd166');
  ctx.fillStyle = gr;
  ctx.fillRect(bx, by, bw * prog, 10);
  ctx.fillStyle = '#ffe6ee';
  ctx.font = 'bold 15px "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(`${Math.ceil(Math.max(0, total - t))}`, W / 2, by + 5);
  if (info.name) {
    ctx.font = 'bold 20px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'alphabetic';
    const tw = ctx.measureText(info.name).width;
    ctx.fillStyle = 'rgba(20,2,12,0.62)';
    ctx.fillRect(W - tw - 44, 58, tw + 30, 34);
    ctx.fillStyle = 'rgba(255,120,150,0.9)';
    ctx.fillRect(W - tw - 44, 58, 3, 34);
    ctx.fillStyle = '#ffd9e6';
    ctx.fillText(info.name, W - 24, 82);
  }
  ctx.restore();
}

export const _internals = { IMG, loadArt };
