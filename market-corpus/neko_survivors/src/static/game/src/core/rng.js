// ============================================================
// 可复现随机数（mulberry32）
// 每局用种子初始化，方便复现 bug 与做每日挑战。
// ============================================================

import { weightedIndex } from './math.js';

export class RNG {
  constructor(seed = Date.now()) {
    this.seed = seed >>> 0;
    this.state = this.seed || 1;
  }

  /** [0, 1) */
  next() {
    this.state |= 0;
    this.state = (this.state + 0x6d2b79f5) | 0;
    let t = Math.imul(this.state ^ (this.state >>> 15), 1 | this.state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** [min, max) */
  range(min, max) {
    return min + this.next() * (max - min);
  }

  /** [min, max] 整数 */
  int(min, max) {
    return Math.floor(min + this.next() * (max - min + 1));
  }

  /** 概率命中 */
  chance(p) {
    return this.next() < p;
  }

  pick(arr) {
    if (!arr || arr.length === 0) return undefined;
    return arr[Math.floor(this.next() * arr.length)];
  }

  /** 按权重取元素；weightFn 返回权重 */
  pickWeighted(arr, weightFn) {
    if (!arr || arr.length === 0) return undefined;
    const idx = weightedIndex(this, arr.map(weightFn));
    return arr[idx];
  }

  /** 原地洗牌（Fisher-Yates） */
  shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(this.next() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  /** 不重复抽 n 个 */
  sample(arr, n) {
    const copy = arr.slice();
    this.shuffle(copy);
    return copy.slice(0, n);
  }

  fork(salt) {
    return new RNG((this.state ^ Math.imul(salt | 0, 0x9e3779b9)) >>> 0);
  }
}

/** 生成随机种子 */
export function randomSeed() {
  return (Math.random() * 0xffffffff) >>> 0;
}