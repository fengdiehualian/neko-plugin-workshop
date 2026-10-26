// ============================================================
// 实体基类 + 对象池
// 为性能考虑，实体用普通对象 + 自由列表管理，不产生大量 GC。
// ============================================================

let NEXT_ID = 1;

export class Entity {
  constructor() {
    this.id = NEXT_ID++;
    this.x = 0; this.y = 0;
    this.vx = 0; this.vy = 0;
    this.radius = 10;
    this.dead = false;
    this.age = 0;
  }

  update(dt) { this.age += dt; }

  /** 屏幕外且远离玩家时可以被回收 */
  isOffscreen(cam, pad = 400) {
    return !cam.isVisible(this.x, this.y, pad);
  }
}

/** 简单对象池 */
export class Pool {
  constructor(factory, reset, initial = 0) {
    this.factory = factory;
    this.reset = reset;
    this.free = [];
    for (let i = 0; i < initial; i++) this.free.push(factory());
    this.created = initial;
  }

  obtain() {
    const obj = this.free.pop();
    if (obj) return obj;
    this.created++;
    return this.factory();
  }

  release(obj) {
    if (this.reset) this.reset(obj);
    this.free.push(obj);
  }

  clear() { this.free.length = 0; }
}

/**
 * 实体列表：支持 O(1) 删除（交换到末尾）
 */
export class EntityList {
  constructor() {
    this.items = [];
  }

  add(e) { this.items.push(e); return e; }

  /** 就地移除所有 dead 的实体，并回调 */
  sweep(onRemove) {
    const arr = this.items;
    for (let i = arr.length - 1; i >= 0; i--) {
      if (arr[i].dead) {
        const e = arr[i];
        const last = arr.pop();
        if (i < arr.length) arr[i] = last;
        if (onRemove) onRemove(e);
      }
    }
  }

  clear() { this.items.length = 0; }

  get length() { return this.items.length; }

  forEach(fn) {
    const arr = this.items;
    for (let i = 0; i < arr.length; i++) fn(arr[i], i);
  }
}

/** 空间网格：加速大量实体的邻近查询 */
export class SpatialGrid {
  constructor(cellSize = 64) {
    this.cellSize = cellSize;
    this.cells = new Map();
  }

  _key(cx, cy) { return cx * 73856093 ^ cy * 19349663; }

  clear() { this.cells.clear(); }

  insert(entity) {
    const cs = this.cellSize;
    const cx = Math.floor(entity.x / cs);
    const cy = Math.floor(entity.y / cs);
    const k = this._key(cx, cy);
    let bucket = this.cells.get(k);
    if (!bucket) { bucket = []; this.cells.set(k, bucket); }
    bucket.push(entity);
  }

  /** 查询圆形范围内的实体 */
  query(x, y, radius, out = []) {
    out.length = 0;
    const cs = this.cellSize;
    const minCx = Math.floor((x - radius) / cs);
    const maxCx = Math.floor((x + radius) / cs);
    const minCy = Math.floor((y - radius) / cs);
    const maxCy = Math.floor((y + radius) / cs);
    const r2 = radius * radius;
    for (let cx = minCx; cx <= maxCx; cx++) {
      for (let cy = minCy; cy <= maxCy; cy++) {
        const bucket = this.cells.get(this._key(cx, cy));
        if (!bucket) continue;
        for (let i = 0; i < bucket.length; i++) {
          const e = bucket[i];
          if (e.dead) continue;
          const dx = e.x - x, dy = e.y - y;
          const rr = radius + (e.radius || 0);
          if (dx * dx + dy * dy <= rr * rr) out.push(e);
        }
      }
    }
    return out;
  }

  /** 找最近的实体 */
  nearest(x, y, maxRadius, filter) {
    const cs = this.cellSize;
    const maxC = Math.ceil(maxRadius / cs);
    const cx0 = Math.floor(x / cs);
    const cy0 = Math.floor(y / cs);
    let best = null;
    let bestD2 = maxRadius * maxRadius;
    for (let cx = cx0 - maxC; cx <= cx0 + maxC; cx++) {
      for (let cy = cy0 - maxC; cy <= cy0 + maxC; cy++) {
        const bucket = this.cells.get(this._key(cx, cy));
        if (!bucket) continue;
        for (let i = 0; i < bucket.length; i++) {
          const e = bucket[i];
          if (e.dead) continue;
          if (filter && !filter(e)) continue;
          const dx = e.x - x, dy = e.y - y;
          const d2 = dx * dx + dy * dy;
          if (d2 < bestD2) { bestD2 = d2; best = e; }
        }
      }
    }
    return best;
  }
}

/** 圆形范围伤害辅助：返回范围内实体 */
export function queryCircle(grid, x, y, radius) {
  return grid.query(x, y, radius);
}

/** 矩形（线段扫掠）范围查询：用于突刺/光束。近似为多个圆 */
export function queryCapsule(grid, x1, y1, x2, y2, radius, out = []) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  const steps = Math.max(1, Math.ceil(len / (radius * 0.8)));
  const seen = new Set();
  out.length = 0;
  for (let i = 0; i <= steps; i++) {
    const t = steps === 0 ? 0 : i / steps;
    const px = x1 + dx * t;
    const py = y1 + dy * t;
    const hits = grid.query(px, py, radius, []);
    for (const h of hits) {
      if (seen.has(h.id)) continue;
      seen.add(h.id);
      out.push(h);
    }
  }
  return out;
}