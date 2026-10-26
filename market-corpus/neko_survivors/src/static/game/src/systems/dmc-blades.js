// =========================================================
// 第一百四十轮：DMC 刀光视觉库 —— "大排档椅子维吉尔"专用
// =========================================================
// 纯 Canvas 绘制函数，无状态、无依赖，供维吉尔 Boss 战 /
// 刀光测试场景（vergil-lab）共用。
//
// 参考：素材 1.png（层叠青色新月弧能量轨迹）、5.png（满屏白色
// 交错刀光线）。用户验收标准："半月弧度 + 发光感足够帅"。

const TAU = Math.PI * 2;

/** mulberry32 —— 折线刀光的确定性伪随机（每帧重画形状一致）。第一百四十八轮起导出共用 */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * 半月刀波（第一百四十一轮 C：纯几何多边形重构 —— 用户："单向、不闭合、
 * 外弧长、内弧短、两端尖锐"，弃用贝塞尔+closePath（旧版画成麻花）。
 *
 * 几何：局部系里飞行方向 = +x，月牙像 "）" 横放 ——
 *   外弧 = 上尖→下尖，中点向 +x（前方/迎风面）鼓出 WdOut；
 *   内弧 = 下尖→上尖，同向弯但深度小（WdIn），中心留出月牙厚度；
 *   两端 sin(πt) 自然归零 → 完全收尖，无需 closePath。
 * 三层叠加：底层光晕（放大 1.35、半透明青）→ 中层实心亮青 →
 *   核心白线沿弧中线 → 头部破空（最凸点放射白线 + 火星）。
 *
 * @param {number} angle  飞行方向（外弧凸起永远迎着它）
 * @param {number} r      基准半径（L = r*2.2 ≈ 刀光长度，W = r*0.72 ≈ 宽度）
 * @param {number} k      生命 0→1（1 = 刚出生最亮）
 */
export function drawCrescent(ctx, x, y, angle, r, k, tint = '#59f2ff') {
  const L = r * 2.2;                 // 刀光基础长度（用户建议 ~100px）
  const W = r * 0.72;                // 基础宽度（~30px）
  const Hw = L * 0.5;                // 上下尖离弦中点的半距
  const Hf = L * 0.18;               // 弦的位置（尖端基准线）
  const sweep = L * 0.16;            // 后掠：上尖拖后，下尖在前，斜置更帅
  const WdOut = W;                   // 外弧最大凸出（迎风面）
  const WdIn = W * 0.22;             // 内弧深度 → 中心厚度 0.78W
  const N = 14;                      // 每条弧采样段数
  const ax = Hf - sweep;             // 上尖 x（拖后）
  const bx = Hf;                     // 下尖 x（在前）

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);                 // 局部 +x = 飞行方向 → 凸面自动迎风
  ctx.globalAlpha = Math.min(1, k * 1.6);

  // 形状多边形：外弧（上尖→下尖）+ 内弧（下尖→上尖）。
  // 每点 = 弦上位置 + 法向正弦鼓出：sin(π·0)=sin(π·1)=0 → 两端自然收尖。
  const buildPath = (scale) => {
    ctx.beginPath();
    for (let i = 0; i <= N; i++) {           // 外弧：上尖 → 下尖（长、凸）
      const t = i / N;
      const yy = -Hw + (Hw - -Hw) * t;
      const xx = ax + (bx - ax) * t + Math.sin(Math.PI * t) * WdOut;
      const px = xx * scale, py = yy * scale;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    for (let i = 0; i <= N; i++) {           // 内弧：下尖 → 上尖（短、陡）
      const t = i / N;
      const yy = Hw + (-Hw - Hw) * t;
      const xx = bx + (ax - bx) * t + Math.sin(Math.PI * t) * WdIn;
      ctx.lineTo(xx * scale, yy * scale);
    }
    // 不 closePath —— 两尖处内外弧坐标已重合，直线闭合零长度
  };

  ctx.globalCompositeOperation = 'lighter';

  // ① 底层光晕：最宽最暗的青色（放大 1.35 的同形状填充）
  ctx.fillStyle = hexA(tint, 0.16 * k);
  buildPath(1.35);
  ctx.fill();

  // ② 中层主体：实心亮青
  ctx.fillStyle = hexA(tint, 0.8 * k);
  buildPath(1);
  ctx.fill();

  // ③ 核心高光：沿弧中线（外弧与内弧的中点轨迹）极细纯白线
  ctx.strokeStyle = `rgba(255,255,255,${0.95 * k})`;
  ctx.lineWidth = 1.6 * k + 0.4;
  ctx.lineCap = 'round';
  ctx.beginPath();
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const yy = -Hw + (Hw - -Hw) * t;
    const xx = ax + (bx - ax) * t + Math.sin(Math.PI * t) * (WdOut + WdIn) * 0.5;
    if (i === 0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
  }
  ctx.stroke();

  // ④ 头部火星（第一百五十二轮：删掉"三根白线+小圆点"的放射 —— 用户：
  // "像鸡爪/穿模的 Debug 符号，极其突兀"。火花保留但只画**发光圆点**：
  // 径向渐变光斑，没有任何线段/多边形线框）
  if (k > 0.25) {
    const tipX = bx + WdOut + 2;
    for (const [dx, dy, rr] of [[10, -4, 2.6], [13, 3, 1.8]]) {
      const gx = tipX + dx * k, gy = dy * k;
      const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, rr * 2.6);
      g.addColorStop(0, `rgba(255,255,255,${0.9 * k})`);
      g.addColorStop(0.45, `rgba(160,240,255,${0.5 * k})`);
      g.addColorStop(1, 'rgba(120,220,255,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(gx, gy, rr * 2.6, 0, TAU);
      ctx.fill();
    }
  }

  ctx.restore();
}

/**
 * 折线贯穿刀光（图5 满屏次元斩）。白色细长折线，带空间撕裂感，
 * 两端渐隐到透明。形状由 seed 确定（每帧重画不闪烁）。
 *
 * @param {number} x1,y1,x2,y2 折线起止（贯穿屏 = 屏外到屏外）
 * @param {number} seed   伪随机种子
 * @param {number} k      生命 0→1
 * @param {number} [segs] 折段数（默认 7）
 * @param {string} [tint] 主色（默认青白；阎魔刀玩家版传 '#ffe9a8' 金白）
 */
export function drawZigzagBlade(ctx, x1, y1, x2, y2, seed, k, segs = 7, tint = '#a0e6ff') {
  const rnd = mulberry32(seed);
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;

  // 第一百四十九轮：撕裂空间的折线感 —— 每 ~50px 一个极小的垂直随机偏移
  //（±5px，闪电/玻璃裂痕质感；两端偏移收 0 → 渐隐）。段数按长度自适应，
  // 满屏刀光（len 1500+）会有 30+ 个折点，细碎高频抖动。
  const n = Math.max(segs, Math.round(len / 50));
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const edge = Math.sin(t * Math.PI);                 // 中段偏移大、两端为 0
    const off = (rnd() - 0.5) * 2 * 5 * edge;
    pts.push([x1 + dx * t + px * off, y1 + dy * t + py * off]);
  }

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  const trace = () => {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  };
  // ---- 多层渲染（用户规格：发光的能量刃，不是白线）----
  // 底层光晕：10~15px 半透明青色，大面积泛光
  ctx.strokeStyle = `rgba(0,255,255,${0.3 * k})`;
  ctx.lineWidth = 10 + 5 * k;
  trace(); ctx.stroke();
  // 中层能量：4~6px 亮蓝青
  ctx.strokeStyle = `rgba(100,200,255,${0.8 * k})`;
  ctx.lineWidth = 4 + 2 * k;
  trace(); ctx.stroke();
  // 核心高光：1.5px 纯白刀锋（tint 调用方决定锋色 —— 阎魔刀金、次元斩白）
  ctx.strokeStyle = hexA(tint === '#a0e6ff' ? '#ffffff' : tint, Math.min(1, k * 1.15));
  ctx.lineWidth = 1.5;
  trace(); ctx.stroke();
  ctx.restore();
}

/**
 * 刀锋火花：白色细长小粒子，寿命极短（~0.2s），沿速度方向拉出细线。
 * 生成在斩击线两端与路径上（调用方 push {x,y,vx,vy,t,life,ang}）。
 */
export function drawBladeSpark(ctx, s) {
  const k = Math.max(0, 1 - s.t / s.life);
  const L = 5 + 7 * k;                          // 越老越短
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = `rgba(255,255,255,${0.9 * k})`;
  ctx.lineWidth = 1.3;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(s.x, s.y);
  ctx.lineTo(s.x - Math.cos(s.ang) * L, s.y - Math.sin(s.ang) * L);
  ctx.stroke();
  // 青色小尾晕
  ctx.strokeStyle = `rgba(120,220,255,${0.4 * k})`;
  ctx.lineWidth = 2.6;
  ctx.beginPath();
  ctx.moveTo(s.x, s.y);
  ctx.lineTo(s.x - Math.cos(s.ang) * L * 0.6, s.y - Math.sin(s.ang) * L * 0.6);
  ctx.stroke();
  ctx.restore();
}

/**
 * 屏幕碎裂（图6 收刀碎屏）。多边形玻璃碎片向四周爆开并淡出。
 *
 * spawnShatter(list, x, y, count) —— 生成碎片数据（push 进调用方数组）
 * drawShards(ctx, list, dt)       —— 更新 + 绘制；碎片出屏/淡完自动移除
 */

/**
 * @param {Array} list 碎片容器
 * @param {number} x,y 爆点
 * @param {number} count 片数（10-15）
 * @param {number} [spread] 初速倍率
 * @param {number} [scale] 碎片尺寸倍率（141 轮：大碎屏用 1.3+）
 */
export function spawnShatter(list, x, y, count = 12, spread = 1, scale = 1) {
  for (let i = 0; i < count; i++) {
    const a = Math.random() * TAU;
    const spd = (140 + Math.random() * 340) * spread;
    // 5-7 边不规则凸多边形（外接半径 8-22 的随机极坐标顶点）
    const n = 5 + (Math.random() * 3 | 0);
    const verts = [];
    for (let v = 0; v < n; v++) {
      const va = (v / n) * TAU + Math.random() * 0.5;
      const vr = (8 + Math.random() * 14) * scale;
      verts.push([Math.cos(va) * vr, Math.sin(va) * vr]);
    }
    list.push({
      x, y,
      vx: Math.cos(a) * spd, vy: Math.sin(a) * spd,
      rot: Math.random() * TAU, vr: (Math.random() - 0.5) * 9,
      verts, t: 0, life: 0.7 + Math.random() * 0.5,
    });
  }
}

/** 更新并绘制碎片；返回剩余数量 */
export function drawShards(ctx, list, dt) {
  for (let i = list.length - 1; i >= 0; i--) {
    const s = list[i];
    s.t += dt;
    if (s.t >= s.life) { list.splice(i, 1); continue; }
    s.x += s.vx * dt;
    s.y += s.vy * dt;
    s.vx *= (1 - 1.4 * dt);
    s.vy *= (1 - 1.4 * dt);
    s.rot += s.vr * dt;
    const k = 1 - s.t / s.life;                  // 淡出
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(s.rot);
    ctx.globalAlpha = k * 0.85;
    ctx.beginPath();
    ctx.moveTo(s.verts[0][0], s.verts[0][1]);
    for (let v = 1; v < s.verts.length; v++) ctx.lineTo(s.verts[v][0], s.verts[v][1]);
    ctx.closePath();
    ctx.fillStyle = `rgba(190,235,255,${0.3 * k})`;
    ctx.fill();
    ctx.strokeStyle = `rgba(240,252,255,${0.9 * k})`;
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.restore();
  }
  return list.length;
}

/**
 * 受击红光：屏幕边缘红色 vignette 闪一下（用户："被刀波砍中时，
 * 屏幕边缘闪一下红光"）。
 * @param {number} k 强度 0→1（1 = 刚命中）
 */
export function drawEdgeFlash(ctx, w, h, k) {
  if (k <= 0.01) return;
  const g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.32,
    w / 2, h / 2, Math.max(w, h) * 0.72);
  g.addColorStop(0, 'rgba(255,40,40,0)');
  g.addColorStop(1, `rgba(255,36,36,${0.5 * Math.min(1, k)})`);
  ctx.save();
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

/** '#59f2ff' + alpha → rgba() 字符串 */
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// =========================================================
// 第一百四十一轮追加：冲刺斩 / AOE 拉回 / 球形裂缝 / 大招暗幕
// =========================================================

/**
 * 冲刺斩的横向贯穿刀光（图2）：一条全屏宽的水平光带，
 * 中心亮白、上下沿青光渐隐，加法混合；k 生命 0→1 快速淡出。
 * y 为世界坐标横条的纵轴（世界层调用）。
 */
export function drawDashSlash(ctx, x, y, halfW, k, tint = '#8ff4ff') {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const h = 30 + 26 * k;                     // 展开中略收
  const g = ctx.createLinearGradient(0, y - h, 0, y + h);
  g.addColorStop(0, hexA(tint, 0));
  g.addColorStop(0.32, hexA(tint, 0.35 * k));
  g.addColorStop(0.5, `rgba(255,255,255,${0.85 * k})`);
  g.addColorStop(0.68, hexA(tint, 0.35 * k));
  g.addColorStop(1, hexA(tint, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - halfW, y - h, halfW * 2, h * 2);
  // 中心锐线
  ctx.strokeStyle = `rgba(255,255,255,${0.9 * k})`;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(x - halfW, y);
  ctx.lineTo(x + halfW, y);
  ctx.stroke();
  ctx.restore();
}

/**
 * AOE 拉回光圈（图3）：双描边大圈。
 * mode: 'grow'（扩散）| 'hold'（停留脉动）| 'collapse'（瞬间收缩，向心细线）
 */
export function drawAoeRing(ctx, x, y, r, k, mode, tint = '#59f2ff') {
  if (r <= 2) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const breathe = mode === 'hold' ? 1 + Math.sin(k * 22) * 0.015 : 1;
  const rr = r * breathe;
  // 外圈（青粗）+ 内圈（白细）
  ctx.strokeStyle = hexA(tint, (mode === 'collapse' ? 0.85 : 0.5) * k);
  ctx.lineWidth = mode === 'collapse' ? 7 : 5;
  ctx.beginPath(); ctx.arc(x, y, rr, 0, TAU); ctx.stroke();
  ctx.strokeStyle = `rgba(255,255,255,${0.75 * k})`;
  ctx.lineWidth = 1.8;
  ctx.beginPath(); ctx.arc(x, y, rr * 0.965, 0, TAU); ctx.stroke();
  // 收缩时：8 根向心细线 + 内侧淡填充
  if (mode === 'collapse') {
    ctx.strokeStyle = hexA(tint, 0.5 * k);
    ctx.lineWidth = 1.4;
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * TAU + k * 3;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(a) * rr * 1.3, y + Math.sin(a) * rr * 1.3);
      ctx.lineTo(x + Math.cos(a) * rr * 0.55, y + Math.sin(a) * rr * 0.55);
      ctx.stroke();
    }
    ctx.fillStyle = hexA(tint, 0.08 * k);
    ctx.beginPath(); ctx.arc(x, y, rr, 0, TAU); ctx.fill();
  }
  ctx.restore();
}

/**
 * 球形次元裂缝（图4）：紫色空间裂缝 telegraph —— 旋转双圈 + 十字暗纹，
 * 生成期 r 从大到小收，爆发前最亮。
 */
export function drawRift(ctx, x, y, r, k, tint = '#b06cff') {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const spin = k * 7;
  ctx.strokeStyle = hexA(tint, 0.55 * k);
  ctx.lineWidth = 2.6;
  ctx.beginPath(); ctx.arc(x, y, r, spin, spin + TAU); ctx.stroke();
  ctx.strokeStyle = `rgba(230,200,255,${0.8 * k})`;
  ctx.lineWidth = 1.4;
  ctx.beginPath(); ctx.arc(x, y, r * 0.68, -spin * 1.4, -spin * 1.4 + TAU); ctx.stroke();
  // 中心暗核（空间撕开的洞）
  const g = ctx.createRadialGradient(x, y, 0, x, y, r * 0.6);
  g.addColorStop(0, `rgba(20,4,34,${0.85 * k})`);
  g.addColorStop(1, hexA(tint, 0));
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(x, y, r * 0.6, 0, TAU); ctx.fill();
  ctx.restore();
}

/**
 * 大招暗幕（图5）：满屏斩击时压暗背景的深紫色遮罩，
 * k 0→1 渐入；边缘更深（vignette）聚焦中央刀光。
 */
export function drawUltVeil(ctx, w, h, k) {
  if (k <= 0.01) return;
  ctx.save();
  ctx.fillStyle = `rgba(24,8,40,${0.52 * Math.min(1, k)})`;
  ctx.fillRect(0, 0, w, h);
  const g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.25,
    w / 2, h / 2, Math.max(w, h) * 0.7);
  g.addColorStop(0, 'rgba(10,2,20,0)');
  g.addColorStop(1, `rgba(10,2,20,${0.5 * Math.min(1, k)})`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

/**
 * 点到线段的最短距离 —— 满屏刀光的命中判定用。
 */
export function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  if (l2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + dx * t), py - (y1 + dy * t));
}

// =========================================================
// 第一百四十六轮：幻影剑（Mirage Edge）+ 格挡领域特效
// =========================================================
// 参考图（图1.png）：高亮发光的流体能量剑 —— 亮青核心 + 柔和蓝色外晕、
// 轮廓带随机微小偏移（火焰感/不稳定能量感）。用户规格：
//  · 多层 lighter 描边：20px 深蓝 α0.1 → 10px 亮蓝 α0.4 → 3px 纯白 α1
//  · 内部能量折线从剑柄向剑尖蔓延
//  · 十字护手 + 剑柄残影；悬浮呼吸缩放；飞行拖尾（调用方画）

/**
 * 幻影剑本体（世界坐标系，angle = 指向方向）。
 *
 * @param {number} x,y    剑柄锚点
 * @param {number} angle  朝向（剑尖方向）
 * @param {number} alpha  整体透明度（召唤淡入/发射淡出用）
 * @param {object} [opts] seed 稳定随机；scale 呼吸缩放（调用方算 sin）；
 *                        trailPts 拖尾点 [{x,y}]（飞行态，调用方先画）
 */
export function drawMirageEdge(ctx, x, y, angle, alpha, opts = {}) {
  const seed = opts.seed ?? 7;
  const scale = opts.scale ?? 1;
  const rnd = mulberry32(seed);

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.scale(scale, scale);
  ctx.globalAlpha = alpha;
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // ---- 剑身轮廓：中心线 + 两侧随机微偏移（火焰状不稳定边缘）----
  // 局部系：剑柄在 (0,0)，剑尖在 (L,0)。
  const L = 96;
  const N = 10;                                 // 中心线采样段数
  const mid = [], up = [], dn = [];
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    // 剑身中线：轻微弯弧（参考图的微弯剑身）+ 尖端收细由宽度控制
    const bend = Math.sin(t * Math.PI) * 4;
    const px = t * L;
    const py = -bend;
    mid.push([px, py]);
    // 宽度：剑根宽 → 剑尖收成尖；微偏移集中在中段（火焰舔舐感）
    const halfW = (1 - t) ** 0.7 * 5.2 + 0.8;
    const wob = Math.sin(t * 11 + rnd() * 1.6) * 1.3 * Math.sin(t * Math.PI);
    up.push([px, py - halfW + wob]);
    dn.push([px, py + halfW + wob]);
  }
  const traceMid = () => {
    ctx.beginPath();
    ctx.moveTo(mid[0][0], mid[0][1]);
    for (let i = 1; i <= N; i++) ctx.lineTo(mid[i][0], mid[i][1]);
  };
  const traceEdge = () => {
    ctx.beginPath();
    ctx.moveTo(up[0][0], up[0][1]);
    for (let i = 1; i <= N; i++) ctx.lineTo(up[i][0], up[i][1]);
    ctx.lineTo(mid[N][0], mid[N][1]);           // 剑尖
    for (let i = N; i >= 0; i--) ctx.lineTo(dn[i][0], dn[i][1]);
    ctx.closePath();
  };

  // ---- 多层描边发光（极度关键，用户规格三层）----
  // 底晕只沿中线铺（闭合轮廓晕 20px 会糊成一团，义眼看成水母的教训）
  ctx.strokeStyle = `rgba(30,60,200,${0.12 * alpha})`;
  ctx.lineWidth = 20;
  traceMid(); ctx.stroke();
  // 轮廓层：亮蓝
  ctx.strokeStyle = `rgba(70,140,255,${0.4 * alpha})`;
  ctx.lineWidth = 7;
  traceEdge(); ctx.stroke();
  // 中线淡晕（把轮廓晕连成体）
  ctx.strokeStyle = `rgba(90,180,255,${0.3 * alpha})`;
  ctx.lineWidth = 5;
  traceMid(); ctx.stroke();
  // 核心层：极亮纯白
  ctx.strokeStyle = `rgba(255,255,255,${1.0 * alpha})`;
  ctx.lineWidth = 2.6;
  traceMid(); ctx.stroke();

  // ---- 内部能量纹理：从剑柄向剑尖蔓延的亮色折线/短线 ----
  ctx.strokeStyle = `rgba(150,240,255,${0.55 * alpha})`;
  ctx.lineWidth = 1.4;
  const veins = 4;
  for (let v = 0; v < veins; v++) {
    const t0 = rnd() * 0.25;
    ctx.beginPath();
    let px = mid[0][0] + t0 * L, py = (rnd() - 0.5) * 6;
    ctx.moveTo(px, py);
    const segs = 3 + (rnd() * 3 | 0);
    for (let s2 = 0; s2 < segs; s2++) {
      px += (0.14 + rnd() * 0.2) * L;
      py += (rnd() - 0.5) * 10;
      ctx.lineTo(px, py);
    }
    ctx.stroke();
  }

  // ---- 护手（十字/斜角）+ 剑柄（带残影）----
  ctx.globalCompositeOperation = 'source-over';
  // 剑柄残影（两道渐弱的短柄）
  for (let g = 2; g >= 0; g--) {
    ctx.strokeStyle = `rgba(50,90,220,${(0.32 - g * 0.1) * alpha})`;
    ctx.lineWidth = 5 - g * 0.8;
    ctx.beginPath();
    ctx.moveTo(-8 - g * 7, 0);
    ctx.lineTo(0, 0);
    ctx.stroke();
  }
  // 护手：垂直短棒 + 斜角
  ctx.strokeStyle = `rgba(40,80,210,${0.9 * alpha})`;
  ctx.lineWidth = 4.5;
  ctx.beginPath();
  ctx.moveTo(2, -11);
  ctx.lineTo(2, 11);
  ctx.stroke();
  ctx.strokeStyle = `rgba(90,170,255,${0.7 * alpha})`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-3, -8); ctx.lineTo(7, 8);
  ctx.moveTo(-3, 8); ctx.lineTo(7, -8);
  ctx.stroke();

  ctx.restore();
}

/**
 * 格挡斩击线：子弹被切碎瞬间的一道纯白折线刀光（一闪即逝 0.1s）。
 * k = 生命 0→1（1 刚触发）。形状由 seed 稳定。
 */
export function drawParrySlash(ctx, x, y, seed, k) {
  const rnd = mulberry32(seed);
  const a = rnd() * Math.PI;                    // 折线主方向
  const len = 26 + rnd() * 14;
  const ux = Math.cos(a), uy = Math.sin(a);
  const px = -uy, py = ux;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  // 主折线（3 折）
  const pts = [[x - ux * len, y - uy * len]];
  for (let i = 1; i <= 3; i++) {
    const t = i / 3;
    pts.push([x - ux * len + ux * len * 2 * t + px * (rnd() - 0.5) * 14,
              y - uy * len + uy * len * 2 * t + py * (rnd() - 0.5) * 14]);
  }
  const trace = () => {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  };
  ctx.strokeStyle = `rgba(180,240,255,${0.5 * k})`;
  ctx.lineWidth = 4 * k + 1;
  trace(); ctx.stroke();
  ctx.strokeStyle = `rgba(255,255,255,${k})`;
  ctx.lineWidth = 1.4 * k + 0.4;
  trace(); ctx.stroke();
  ctx.restore();
}

/**
 * 格挡火花：子弹碎裂成的 3-4 个青色微小光点，向四周弹开淡出。
 * spawn 由 vergilFight 调（容器在它身上）；这里只画单个。
 */
export function drawParrySpark(ctx, s) {
  const k = Math.max(0, 1 - s.t / s.life);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.fillStyle = `rgba(140,240,255,${k})`;
  ctx.beginPath();
  ctx.arc(s.x, s.y, 1.6 * k + 0.4, 0, TAU);
  ctx.fill();
  // 短拖尾
  ctx.strokeStyle = `rgba(90,200,255,${0.5 * k})`;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(s.x, s.y);
  ctx.lineTo(s.x - s.vx * 0.03, s.y - s.vy * 0.03);
  ctx.stroke();
  ctx.restore();
}

/**
 * 格挡涟漪：大量子弹同时被斩碎时，椅子周身一圈急速收缩的青色光圈。
 * k: 0→1 生命（0 刚触发 = 大圈，1 收缩完毕消失）
 */
export function drawParryRipple(ctx, x, y, k, baseR = 120) {
  const r = baseR * (1 - k) + 18;
  const a = (1 - k) * 0.7;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.strokeStyle = `rgba(90,220,255,${a})`;
  ctx.lineWidth = 3 * (1 - k) + 0.5;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.stroke();
  ctx.strokeStyle = `rgba(220,250,255,${a * 0.8})`;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, r * 0.82, 0, TAU);
  ctx.stroke();
  ctx.restore();
}
