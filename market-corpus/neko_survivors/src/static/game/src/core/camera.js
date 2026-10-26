// ============================================================
// 相机：跟随玩家 + 平滑 + 屏幕震动 + 世界<->屏幕坐标转换
// ============================================================

import { clamp, damp } from './math.js';

export class Camera {
  constructor() {
    this.x = 0; this.y = 0;
    this.targetX = 0; this.targetY = 0;
    this.viewW = 1280; this.viewH = 720;
    this.shakeAmount = 0;
    this.shakeTime = 0;
    this.offsetX = 0; this.offsetY = 0;
    this._t = 0;
    this.smoothing = 12;
  }

  resize(w, h) {
    this.viewW = w;
    this.viewH = h;
  }

  /** 立刻把相机对准某点（换关/开局用） */
  snapTo(x, y) {
    this.x = this.targetX = x;
    this.y = this.targetY = y;
  }

  follow(x, y, dt, lead = 0) {
    this.targetX = x;
    this.targetY = y;
    this.x = damp(this.x, this.targetX, this.smoothing, dt);
    this.y = damp(this.y, this.targetY, this.smoothing, dt);
  }

  /** 触发屏幕震动 */
  shake(amount, duration = 0.18) {
    if (amount > this.shakeAmount || this.shakeTime <= 0) {
      this.shakeAmount = Math.max(this.shakeAmount, amount);
      this.shakeTime = Math.max(this.shakeTime, duration);
    }
  }

  update(dt) {
    this._t += dt;
    if (this.shakeTime > 0) {
      this.shakeTime -= dt;
      const k = Math.max(0, this.shakeTime) * 6;
      this.offsetX = Math.sin(this._t * 61.7) * this.shakeAmount * k;
      this.offsetY = Math.cos(this._t * 53.3) * this.shakeAmount * k;
      if (this.shakeTime <= 0) {
        this.shakeAmount = 0;
        this.offsetX = this.offsetY = 0;
      }
    } else {
      this.offsetX = this.offsetY = 0;
    }
  }

  /** 相机中心（含震动） */
  get cx() { return this.x + this.offsetX; }
  get cy() { return this.y + this.offsetY; }

  /** 世界坐标 -> 屏幕坐标 */
  worldToScreen(wx, wy) {
    return {
      x: wx - this.cx + this.viewW / 2,
      y: wy - this.cy + this.viewH / 2,
    };
  }

  /** 屏幕坐标 -> 世界坐标 */
  screenToWorld(sx, sy) {
    return {
      x: sx + this.cx - this.viewW / 2,
      y: sy + this.cy - this.viewH / 2,
    };
  }

  /** 可见世界矩形（带边距，用于剔除） */
  visibleRect(margin = 80) {
    const hw = this.viewW / 2 + margin;
    const hh = this.viewH / 2 + margin;
    return {
      x: this.cx - hw,
      y: this.cy - hh,
      w: hw * 2,
      h: hh * 2,
      left: this.cx - hw,
      right: this.cx + hw,
      top: this.cy - hh,
      bottom: this.cy + hh,
    };
  }

  /** 某个点是否大致可见 */
  isVisible(x, y, pad = 120) {
    return Math.abs(x - this.cx) < this.viewW / 2 + pad
        && Math.abs(y - this.cy) < this.viewH / 2 + pad;
  }
}