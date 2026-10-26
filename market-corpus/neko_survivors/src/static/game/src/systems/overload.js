// ============================================================
// overload.js — 第一百三十一轮：Boss「过载减伤」
//
// 用户设计（原文要点）：
//   · 触发：Boss（包括芙兰）在 0.5 秒内掉血超过其最大生命值的 10%
//     → 获得一层"过载减伤" Buff
//   · 每层 50% 减伤，**乘算叠加**（50% → 75% → 87.5%…），防无敌
//   · 层数无限叠，但**每层独立计时 5 秒**，到点该层消失；
//     新层不刷新旧层的时间
//
// 最终目的（用户）：玩家没法停火，用视觉反馈告诉玩家——
// "现在你的输出被限制了，但这正是你专心躲弹幕的 5 秒钟！"
//
// 设计：纯逻辑类，时钟由调用方注入（recordDamage/tick 传 now），
// 便于测试精确复现"连续输出 5 秒叠几层、5 秒后是否恢复"。
// ============================================================

/** 触发阈值：0.5 秒窗口内累计掉血 ≥ 10% maxHp → 一层 */
export const OVERLOAD_THRESHOLD = 0.10;
/** 触发窗口（秒） */
export const OVERLOAD_WINDOW = 0.5;
/** 每层持续秒数（各层独立计时） */
export const OVERLOAD_LAYER_SECONDS = 5;
/** 每层减伤比例（乘算叠加：1 层 50%、2 层 75%、3 层 87.5%…） */
export const OVERLOAD_LAYER_FRAC = 0.5;

export class OverloadGuard {
  /**
   * @param {number} maxHp  Boss 最大生命值（阈值按比例算）
   * @param {object} [opts] 覆盖默认参数（测试用）
   */
  constructor(maxHp, opts = {}) {
    this.maxHp = Math.max(1, maxHp);
    this.threshold = opts.threshold ?? OVERLOAD_THRESHOLD;
    this.window = opts.window ?? OVERLOAD_WINDOW;
    this.layerSeconds = opts.layerSeconds ?? OVERLOAD_LAYER_SECONDS;
    this.layerFrac = opts.layerFrac ?? OVERLOAD_LAYER_FRAC;
    /** 层列表：每项 { expireAt }，各自独立计时 */
    this.layers = [];
    /** 窗口累计器：最近 window 秒内实际掉血量（触发后清零重攒） */
    this._acc = 0;
    this._windowStart = 0;
  }

  /**
   * 记一笔实际掉血（**减伤后**真实扣掉的血）。
   * 语义（严格对应用户原话"0.5 秒内掉血超过 10% → 一层"）：
   * 每个 0.5s 滚动窗口结算一次 —— 窗口关闭时若该窗累计掉血 ≥
   * 阈值，给**一层**（一窗至多一层，同一窗打再狠也只算一次"过载"）。
   * 结算发生在下一笔伤害到来 / 显式 settle 时，延迟 < 一个采样间隔。
   * @returns {boolean} 本次调用是否结算出了新层（调用方据此播闪光特效）
   */
  recordDamage(now, dmg) {
    if (now - this._windowStart >= this.window) {
      const fired = this._settle(now);
      this._windowStart = now;
      this._acc = 0;
      if (dmg > 0) this._acc += dmg;
      return fired;
    }
    if (dmg > 0) this._acc += dmg;
    return false;
  }

  /** 结算当前窗口：累计掉血 ≥ 阈值 → 给一层（各自独立计时） */
  _settle(now) {
    if (this._acc >= this.threshold * this.maxHp) {
      this.layers.push({ expireAt: now + this.layerSeconds });
      this._acc = 0;
      return true;
    }
    return false;
  }

  /**
   * 每帧推进：滚动结算到期的窗口 + 清理过期的层。
   * Enemy.update 调这个（而不是只 tick）—— 停火时窗口照样能结算，
   * 减伤/闪光不会拖到下一刀才生效。
   * @returns {boolean} 本次是否结算出了新层
   */
  update(now) {
    let fired = false;
    if (now - this._windowStart >= this.window) {
      fired = this._settle(now);
      this._windowStart = now;
      this._acc = 0;
    }
    this.tick(now);
    return fired;
  }

  /** 过期清理：到点的层各自消失（新层不刷新旧层时间） */
  tick(now) {
    if (!this.layers.length) return;
    this.layers = this.layers.filter((l) => l.expireAt > now);
  }

  /** 当前层数 */
  get layersCount() {
    return this.layers.length;
  }

  /** 当前总减伤比例（乘算叠加）：1 − 0.5^层数 */
  get reduction() {
    return 1 - Math.pow(this.layerFrac, this.layers.length);
  }

  /** 伤害乘区（直接乘到 incoming 伤害上）：0.5^层数 */
  get multiplier() {
    return Math.pow(this.layerFrac, this.layers.length);
  }
}
