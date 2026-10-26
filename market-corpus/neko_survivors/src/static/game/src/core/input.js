// ============================================================
// 输入：键盘（WASD / 方向键） + 鼠标（瞄准，可选）
// 只暴露轮询式状态，不派发事件，避免和游戏循环竞态。
// ============================================================

export class Input {
  // 第一百六十四轮：NekoAgent 木偶模式总闸 —— true 时真实键盘输入被
  // 完全接管（keys 持续清空），角色只听 window.NekoAgent 的指令；
  // 置回 false 玩家立即抢回控制权。静态属性：Input 不持有 game 引用。
  static agentBlocked = false;

  constructor(canvas) {
    this.canvas = canvas;
    this.keys = new Set();
    this.justPressed = new Set();
    this.mouse = { x: 0, y: 0, down: false };
    this.anyInput = false;

    this._onKeyDown = (e) => {
      // 木偶模式：吞掉一切键盘（含 ESC/空格），防页面滚动照旧
      if (['Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.code)) {
        e.preventDefault();
      }
      if (Input.agentBlocked) {
        // 第一百六十七轮：**ESC 放行** —— 否则代打期间玩家连暂停菜单都
        // 唤不出来，彻底无法接管（实测："esc完全失效"）。只吞操作键，
        // ESC 照常入队走暂停流程，玩家从菜单里的开关抢回方向盘。
        if (e.code !== 'Escape') {
          if (this.keys.size) this.keys.clear();
          if (this.justPressed.size) this.justPressed.clear();
          return;
        }
      }
      if (!this.keys.has(e.code)) this.justPressed.add(e.code);
      this.keys.add(e.code);
      this.anyInput = true;
    };
    this._onKeyUp = (e) => { this.keys.delete(e.code); };
    this._onBlur = () => { this.keys.clear(); this.mouse.down = false; };

    this._onMouseMove = (e) => {
      const r = canvas.getBoundingClientRect();
      this.mouse.x = (e.clientX - r.left) * (canvas.width / r.width);
      this.mouse.y = (e.clientY - r.top) * (canvas.height / r.height);
    };
    this._onMouseDown = () => { this.mouse.down = true; };
    this._onMouseUp = () => { this.mouse.down = false; };

    window.addEventListener('keydown', this._onKeyDown);
    window.addEventListener('keyup', this._onKeyUp);
    window.addEventListener('blur', this._onBlur);
    window.addEventListener('mousemove', this._onMouseMove);
    window.addEventListener('mousedown', this._onMouseDown);
    window.addEventListener('mouseup', this._onMouseUp);
  }

  /** 每帧末尾调用，清空"刚按下"集合 */
  endFrame() {
    this.justPressed.clear();
    this.anyInput = false;
  }

  isDown(code) { return this.keys.has(code); }
  pressed(code) { return this.justPressed.has(code); }

  /** 移动轴向量（已归一化） */
  moveAxis() {
    // 木偶模式下键盘轴恒为零（keys 已被 _onKeyDown 持续清空，这里双保险）
    if (Input.agentBlocked) return { x: 0, y: 0 };
    let x = 0, y = 0;
    if (this.isDown('KeyA') || this.isDown('ArrowLeft')) x -= 1;
    if (this.isDown('KeyD') || this.isDown('ArrowRight')) x += 1;
    if (this.isDown('KeyW') || this.isDown('ArrowUp')) y -= 1;
    if (this.isDown('KeyS') || this.isDown('ArrowDown')) y += 1;
    if (x !== 0 && y !== 0) {
      const inv = 1 / Math.SQRT2;
      x *= inv; y *= inv;
    }
    return { x, y };
  }

  /**
   * 第一百七十一轮：P2 专属移动轴 —— 右侧小键盘 ↑↓←→（Numpad8/2/4/6）。
   * duo 模式第二真人玩家用，与 P1 键位完全隔离，互不抢键。
   * 注意：小键盘 8/2/4/6 在 NumLock 开启时是数字键（'Numpad8'），
   * 关闭时是方向键（'ArrowUp' 等）—— 两种都收。
   */
  moveAxisP2() {
    let x = 0, y = 0;
    if (this.isDown('Numpad4') || this.isDown('NumpadLeft')) x -= 1;
    if (this.isDown('Numpad6') || this.isDown('NumpadRight')) x += 1;
    if (this.isDown('Numpad8') || this.isDown('NumpadUp')) y -= 1;
    if (this.isDown('Numpad2') || this.isDown('NumpadDown')) y += 1;
    if (x !== 0 && y !== 0) {
      const inv = 1 / Math.SQRT2;
      x *= inv; y *= inv;
    }
    return { x, y };
  }

  /** P2 技能键（芙兰战）：小键盘 0 = 减速、小键盘 . = 消弹（第一百七十一轮） */
  p2SkillSlow() { return this.isDown('Numpad0') || this.isDown('NumpadInsert'); }
  p2SkillClear() { return this.pressed('NumpadDecimal'); }

  dispose() {
    window.removeEventListener('keydown', this._onKeyDown);
    window.removeEventListener('keyup', this._onKeyUp);
    window.removeEventListener('blur', this._onBlur);
    window.removeEventListener('mousemove', this._onMouseMove);
    window.removeEventListener('mousedown', this._onMouseDown);
    window.removeEventListener('mouseup', this._onMouseUp);
  }
}