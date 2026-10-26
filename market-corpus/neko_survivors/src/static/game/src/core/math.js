// ============================================================
// 数学与向量工具
// ============================================================

export const TAU = Math.PI * 2;

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
export const lerp = (a, b, t) => a + (b - a) * t;
export const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);

export function dist2(ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  return dx * dx + dy * dy;
}
export function dist(ax, ay, bx, by) {
  return Math.sqrt(dist2(ax, ay, bx, by));
}
export function angleTo(ax, ay, bx, by) {
  return Math.atan2(by - ay, bx - ax);
}

/** 把角度差归一到 [-PI, PI] */
export function angleDiff(a, b) {
  let d = (b - a) % TAU;
  if (d > Math.PI) d -= TAU;
  if (d < -Math.PI) d += TAU;
  return d;
}

/** 指数趋近，帧率无关 */
export function damp(current, target, lambda, dt) {
  return lerp(current, target, 1 - Math.exp(-lambda * dt));
}

/** 圆-圆碰撞 */
export function circleHit(ax, ay, ar, bx, by, br) {
  const r = ar + br;
  return dist2(ax, ay, bx, by) <= r * r;
}

/** 点是否在扇形内（用于近战判定） */
export function inArc(px, py, cx, cy, facing, halfAngle, radius) {
  const d2 = dist2(px, py, cx, cy);
  if (d2 > radius * radius) return false;
  const a = angleTo(cx, cy, px, py);
  return Math.abs(angleDiff(facing, a)) <= halfAngle;
}

/** 圆与轴对齐矩形是否相交 */
export function circleRectHit(cx, cy, r, rx, ry, rw, rh) {
  const nx = clamp(cx, rx, rx + rw);
  const ny = clamp(cy, ry, ry + rh);
  return dist2(cx, cy, nx, ny) <= r * r;
}

/** 随机单位向量 */
export function randDir(rng) {
  const a = rng.next() * TAU;
  return { x: Math.cos(a), y: Math.sin(a) };
}

/** 数组随机取一个 */
export function pickOne(rng, arr) {
  return arr[Math.floor(rng.next() * arr.length)];
}

/** 按权重取索引 */
export function weightedIndex(rng, weights) {
  let total = 0;
  for (const w of weights) total += w;
  if (total <= 0) return 0;
  let r = rng.next() * total;
  for (let i = 0; i < weights.length; i++) {
    r -= weights[i];
    if (r <= 0) return i;
  }
  return weights.length - 1;
}