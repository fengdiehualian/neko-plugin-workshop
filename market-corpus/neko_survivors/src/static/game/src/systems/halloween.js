// 第四十九轮同款精神续作 —— 第一百八十四轮：万圣节双猫（巧克力 & 香草）
// 素材与弹幕规格来自 Rabi-Ribi 万圣节 DLC 拆包（隔壁会话 Ghidra 逆向 +
// ASSET-GUIDE）：30+30 张切帧（choco_h / vanilla_h）、bgm13.ogg（
// Mischievous Masquerade）、hill_a 秋山背景。
//
// 设计（用户逐条拍板）：
//  · 纯演出 boss：排在芙兰后面出场，芙兰撤场 3 秒黑屏换背景，场景变亮
//    后放 BGM。她们没有 hitbox、不进敌人索敌列表，玩家武器打不到
//    —— "打也是到点，不打也是到点"，演完即胜。
//  · 伪横版：原版是宽屏，场地用宽矩形不画正方形；玩家的"只有头"猫娘
//    在横版背景前走位毫无违和。
//  · 弹幕伤害：每发 10% 真伤（无视护甲），战斗期间回血无效 —— 用户：
//    "做成每发弹幕10%真伤不给回血和护甲看看"。
//  · 时长：全部招式演完 ≈100 秒，不设固定秒数，演完离场即胜。
//  · 原版弹速单位是 px/帧@60fps —— 全部 ×60 换成 px/s（RECON-SPEC）。
import { TAU } from '../core/math.js';
import { audio } from './audio.js';
import { CHOCO_FRAMES, VANILLA_FRAMES } from './halloween-frames.js';
import { layoutWord } from './halloween-font.js';

// ---- 场地（宽屏，用户："移动场地可以大点，原版就是宽屏，我们不用画正方形限制"）----
export const ARENA = { cx: 0, cy: 40, hw: 640, hh: 330 };
// 双猫悬浮位（屏幕上半部，左右对称；y 基线 + Math.sin 浮动在 draw/update 里加）
export const CAT_Y = ARENA.cy - ARENA.hh + 120;

// ---- 185 轮：追踪猫爪（NEKO TRACKER）与角落封堵（见 _tickCorners）参数 ----
export const CLAW_SECTION_S = 13;   // 节长（原 7）
export const CLAW_LIFE_S = 12;       // 爪寿命（原 6）
export const CLAW_TURN = 2.8;        // 转向速率上限 rad/s（原 2.2）
export const CLAW_INTERVAL = 2.2;    // 发射间隔（原 1.2）
export const CLAW_PER_VOLLEY = 2;    // 每次几只（原 3）。寿命 12s ÷ 间隔 2.2s ≈ 5.5 次 × 2 ≈ 11 只同时在场
export const EMBER_SPEED = 0.8;       // 202 轮：追踪爪散出的紫色小弹，px/帧（原 0.4）
export const EMBER_RANGE = 280;       // 202 轮：射程 px（原 70）→ 飞行时间 = 280/(0.8×60) ≈ 5.8s（原 2.9s）
export const BEAD_SPLASH_Y = 6;      // 202 轮：灯珠触到场地底边以上多少 px 就散开
export const CLAW_MAX = 14;          // 同屏上限 —— 必须 > 稳态数量，否则「丢最老」会把寿命实际压回 ~6s（测试抓到过）
export const CORNER_PERIOD = { 2: 4.6, 3: 3.0 };   // 角落封堵的间隔（按阶段）
export const NO_CORNER_SECTIONS = new Set(['intro', 'split', 'candy']);   // 这些节不加角落弹
export const CAT_X_OFF = 270;

// 弹速换算：原版 px/帧 @60fps → px/s
const V = (framesPerPx) => framesPerPx * 60;

// 每发弹幕真伤 = 玩家 maxHp × 10%（用户拍板的测试口径）
export const HALLOWEEN_HIT_FRAC = 0.10;
export const HALLOWEEN_HIT_INVULN = 0.8;

// ---- 帧图懒加载（芙兰背景同款模式：失败静默，绝不阻塞）----
// 行映射（ASSET-GUIDE 启发式，线性编号 = 精灵表行优先）：
//   0-9 待机循环 / 10-20 施法+攻击 / 21-24 小动作 / 25-27 终结技 / 28-29 出场
const IDLE_F = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
const CAST_F = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20];
const FINISH_F = [25, 26, 27];
const IMG = {};
function loadFrames() {
  for (const [key, prefix] of [['choco', 'choco_h'], ['vanilla', 'vanilla_h']]) {
    for (let i = 0; i < 30; i++) {
      const id = `${key}_${i}`;
      if (IMG[id]) continue;
      try {
        const img = new Image();
        img.src = `assets/boss/halloween/${prefix}_${String(i).padStart(2, '0')}.png`;
        IMG[id] = img;   // 加载失败 onerror 时 naturalWidth === 0，draw 跳过走占位
      } catch { /* 忽略 */ }
    }
  }
}
loadFrames();

// ---- 原版音效（wav 直播，不走 SE 注册表 —— rabiribi 是 wav 不是 ogg）----
const SE = {};
function loadSe() {
  for (const s of ['laser13', 'etc13', 'hit13', 'jump13', 'laser1']) {
    try {
      const a = new Audio(`assets/boss/halloween/se/${s}.wav`);
      a.preload = 'auto';
      a.volume = s === 'laser13' ? 0.22 : s === 'laser1' ? 0.16 : 0.3;
      a.load();
      SE[s] = a;
    } catch { /* 忽略 */ }
  }
}
loadSe();
let _sfxLast = {};
function sfx(name, minGap = 0.18) {
  const now = performance.now() / 1000;
  if (now - (_sfxLast[name] ?? -9) < minGap) return;
  _sfxLast[name] = now;
  try { const v = SE[name]?.cloneNode(); if (v) { v.volume = SE[name].volume; v.play().catch(() => {}); } } catch { /* */ }
}
// 原曲 BGM：Mischievous Masquerade（dlc0 原始 wav —— 之前用的 bgm13.ogg
// 是基础包第 13 首，不是万圣节曲，用户实测"bgm也不对"）
let _bgmEl = null;
function bgmStart() {
  try {
    if (!_bgmEl) {
      _bgmEl = new Audio('assets/boss/halloween/mischievous.wav');
      _bgmEl.loop = true;
      _bgmEl.volume = 0.55;
    }
    _bgmEl.currentTime = 0;
    _bgmEl.play().catch(() => {});
  } catch { /* */ }
}
function bgmStop() {
  try { if (_bgmEl) { _bgmEl.pause(); } } catch { /* */ }
}

export class HalloweenFight {
  static ARENA = ARENA;
  constructor(main, opts = {}) {
    this.main = main;
    this.onDone = opts.onDone || (() => {});
    this.over = false;
    this.t = -(opts.chain ? 3 : 1.2);   // 黑屏换场 3 秒（芙兰衔接）/ 测试直入 1.2 秒
    this.timeScale = 1;
    this.outcome = null;
    // 双猫演出状态
    this.cats = {
      choco: { x: ARENA.cx - CAT_X_OFF, y: CAT_Y, cast: 0, frame: 0, ft: 0 },
      vanilla: { x: ARENA.cx + CAT_X_OFF, y: CAT_Y + 26, cast: 0, frame: 0, ft: 0 },
    };
    this.merged = true;          // 开场"合并猫"，第 5 节分裂成双猫
    this.splitFx = 0;
    this.phaseIdx = 0;           // 当前 section 下标
    this.phaseT = 0;             // section 内局部时间
    this._emit = 0;              // 通用发射计时器
    this.spellT = 0;             // 当前攻击名显示计时（渲染层扫入淡出）
    this.curSpell = '';
    this._alt = 0;               // 双猫交替旗
    this._orbit = 0;             // 激光旋转角
    this._cornerAlt = 0;         // 185 轮：角落封堵由哪只猫出手（交替）
    this.waves = [];             // 激光束（{x,y,angle,rot,t,life,who}）
    this.pumpkins = [];          // 南瓜（投影点 + 落地计时）
    this.timeLeft = 0;           // UI 显示：剩余演出秒数
    // ---- 视觉重写新增状态（纯演出/调试，不影响判定）----
    this._q = [];                // 定时队列 [{at, fn}] —— 取代 setTimeout，暂停时同步冻结
    this.splitAt = null;         // 分裂发生的演出时刻（渲染层做飞散缓动）
    this.flash = 0;              // 全屏白闪强度（分裂瞬间）
    this.shake = 0;              // 屏幕震动强度（渲染层 ×10px）
    this._buildTimeline();
    if (opts.phase > 0) this._seek(opts.phase);
  }

  /** 调试：直接跳到第 n 节（?hwphase=N），方便逐招截图验收 */
  _seek(n) {
    n = Math.min(n, this.sections.length - 1);
    let acc = 0;
    for (let i = 0; i < n; i++) acc += this.sections[i].dur;
    this.phaseIdx = n;
    this.phaseT = 0;
    this.t = acc;
    if (n > 5) { this.merged = false; this.splitAt = acc - 9; }
  }

  /** 演出时间轴 —— 每节 {name, dur, tick(dt, T)}，顺序执行，跑完 outro 即胜 */
  _buildTimeline() {
    this.sections = [
      // word = 该节开场由灯珠拼出、整块坠落的单词（用户："改成用发光的光球阵列拼出
      // 攻击单词，并让这些光球作为弹幕坠落"）；spell = 左上角像素字招式名
      { name: 'intro', spell: 'MISCHIEVOUS MASQUERADE', word: ['TRICK'], dur: 4.5, tick: (dt) => this._tickIntro(dt) },
      { name: 'ring', spell: 'FANG OF HARVEST', dur: 6, tick: (dt) => this._tickRing(dt) },
      { name: 'fan', spell: 'TRICK OR TREAT', word: ['TREAT'], dur: 7, tick: (dt, T) => this._tickFan(dt, T) },
      { name: 'wall', spell: 'MIDNIGHT BARRAGE', dur: 6, tick: (dt) => this._tickWall(dt) },
      { name: 'pumpkin', spell: 'PUMPKIN PANIC', word: ['BOO!'], dur: 8, tick: (dt) => this._tickPumpkin(dt) },
      { name: 'split', spell: 'WE ARE SCARY!', word: ['HAPPY', 'HALLOWEEN'], dur: 6, tick: (dt) => this._tickSplit(dt) },
      { name: 'alt_ring', spell: 'TWIN FANG DANCE', dur: 10, tick: (dt) => this._tickAltRing(dt) },
      { name: 'laser', spell: 'CROSS NIGHT PARADE', dur: 10, tick: (dt) => this._tickLaser(dt) },
      { name: 'claw', spell: 'NEKO TRACKER', dur: CLAW_SECTION_S, tick: (dt) => this._tickClaw(dt) },
      { name: 'candy', spell: 'SUGAR RUSH', word: ['SWEET'], dur: 7, tick: (dt) => this._tickCandy(dt) },
      { name: 'rage', spell: 'BUNNY EXTINCTION', word: ['BUNNY', 'EXTINCTION'], dur: 12, tick: (dt) => this._tickRage(dt) },
    ];
    this.totalShow = this.sections.reduce((a, s) => a + s.dur, 0) + 5;   // + outro
    this.timeLeft = this.totalShow;
  }

  get phase() { return this.phaseIdx >= 6 ? 3 : this.phaseIdx >= 2 ? 2 : 1; }

  /** 当前双猫的演出位置（分裂前重叠居中） */
  _catPos(who) {
    if (!this.merged) return this.cats[who];
    return { x: ARENA.cx, y: CAT_Y + 10 };
  }

  // ---- 弹幕发射：全部走 world.spawnEnemyBullet，halloween 标记走 10% 真伤 ----
  _shot(x, y, angle, speedPxFrame, opts = {}) {
    const w = this.main.world;
    // 弹幕上限：普通弹 650（芙兰 750 同级）；灯珠字母弹可冲到 900（单词是演出主角，不能被密度挤掉）
    const cap = opts.hwKind === 'bead' ? 900 : 650;
    if (w.enemyBullets.items.length > cap) return null;
    return w.spawnEnemyBullet({
      x, y, angle, speed: V(speedPxFrame),
      damage: 0,                       // 伤害走 halloweenHit 真伤通道，damage 不用
      radius: opts.radius ?? 8,        // 渲染缩放基准（8 = 标准弹，视觉直径 ≈ 34）
      // 判定半径独立于视觉：白核 + 亮盘约 6px 才是"实体"，外围光晕不判定
      // （原版擦弹才刺激；之前 9px 判定 + 密度上去后会"明明没碰到也中弹"）
      hitRadius: opts.hitRadius ?? 5.5,
      color: opts.color ?? 'amber',    // 发光色带键：amber/red/gold/violet/cyan/green（见 halloween-render.js）
      // 206 轮（用户："很多弹幕飞着飞着就自己没了，把所有弹幕持续时间翻倍吧"）：射程整体 x2
      range: (opts.range ?? 900) * 2,
      shape: opts.shape ?? 'orb',
      halloween: true,
      hwKind: opts.hwKind ?? '',
      hwPhase: opts.hwPhase ?? 0,
    });
  }

  _catShot(who, angle, speed, opts) {
    const p = this._catPos(who);
    this._shot(p.x, p.y + 20, angle, speed, opts);
  }

  /** 环形弹：起始角随机，N 发均分（RECON-SPEC：RandomInt(0,359)、angle = base + i*360/N） */
  _ring(who, n, speed, color) {
    const base = Math.floor(Math.random() * 360) * Math.PI / 180;
    for (let i = 0; i < n; i++) this._catShot(who, base + (i * TAU / n), speed, { color });
  }

  /** 三重对称扇：X 偏移 ±48/±96/±144 三对（RECON-SPEC 原值），向下扇 */
  _fan(who) {
    const px = this._catPos(who);
    for (const off of [-144, -96, -48, 48, 96, 144]) {
      const x = px.x + off;
      const dy = ARENA.cy + ARENA.hh - px.y;
      this._shot(x, px.y + 20, Math.atan2(dy, 0) + off * 0.0006, 2.0, { color: 'gold' });
    }
  }

  /** 直线弹墙：沿角度 56.16px/步 × 18.52 步，4 波 × 2 发（RECON-SPEC） */
  _wall(who, angle) {
    const px = this._catPos(who);
    const step = 56.16, steps = 18;
    for (let wv = 0; wv < 4; wv++) {
      // 走定时队列而非 setTimeout：暂停(ESC)时 update 不跑，弹墙也跟着冻结
      this._q.push({
        at: this.t + wv * 0.55,
        fn: () => {
          for (const perp of [-1, 1]) {
            const a = angle + perp * Math.PI / 2;
            for (let i = -steps / 2; i <= steps / 2; i++) {
              const sx = px.x + Math.cos(a) * step * i, sy = px.y + Math.sin(a) * step * i;
              if (Math.abs(sx - ARENA.cx) < ARENA.hw + 60 && Math.abs(sy - ARENA.cy) < ARENA.hh + 60) {
                this._shot(sx, sy, angle, 2.2, { color: 'red', shape: 'longball' });
              }
            }
          }
        },
      });
    }
  }

  /**
   * 灯珠字母弹幕：把单词拼成 5×7 点阵，每个格点一颗灯珠弹，整块刚体坠落。
   *  · 间距 pitch 随词宽自适应（11~21px，整词 ≤980px 宽），灯珠光晕 ≥ 间距 → 视觉串成霓虹管
   *  · 整词倾斜 7°（右上扬，报告 §3.1），从左到右每列延迟 28ms 依次点亮（报告 §3.4）
   *  · 延迟生成的灯珠起点按 v·delay 前移，保证整词是刚体、不会因为错时生成而"剪切"变形
   *  · 判定半径 4.5（只有白核算实体），字怀/笔画间隙就是安全通道
   */
  _spawnWord(lines) {
    const { cells, widthCells, heightCells } = layoutWord(lines);
    const pitch = Math.max(12, Math.min(24, 1160 / widthCells));   // 整词 ≤1160px 宽；单行短词可放大到 24px 间距（字高 168px）
    const tilt = -0.12;
    const cs = Math.cos(tilt), sn = Math.sin(tilt);
    const fall = 46;                                    // px/s，整词坠落速度
    const cx = ARENA.cx;
    const cy = ARENA.cy - ARENA.hh + 60 + (heightCells * pitch) / 2;   // 词顶贴近场地上沿
    for (const c of cells) {
      const lx = c.cx * pitch, ly = c.cy * pitch;
      const delay = c.col * 0.028;
      const x = cx + lx * cs - ly * sn;
      const y = cy + lx * sn + ly * cs + fall * delay;  // 延迟补偿：等价于 t0 时刻一起出发
      this._q.push({
        at: this.t + delay,
        fn: () => this._shot(x, y, Math.PI / 2, fall / 60, {
          color: 'gold', hwKind: 'bead', hwPhase: Math.random() * TAU, hitRadius: 4.5, range: 1300,
          radius: pitch * 0.5,   // 仅渲染：灯珠光晕/白核随点阵间距等比放大（报告：核:距 ≈ 1:4.2）
        }),
      });
    }
    sfx('etc13', 0.6);
  }

  /** 南瓜炸弹：抛物线落点 → 十字爆风（伪横版灵魂：下落可预判，横走可躲） */
  _pumpkin() {
    const x = ARENA.cx + (Math.random() * 2 - 1) * (ARENA.hw - 80);
    const y = ARENA.cy + (Math.random() * 2 - 1) * (ARENA.hh - 70);
    this.pumpkins.push({ x, y, t: 1.1, boom: 0 });
  }

  // ============================================================
  // 时间轴各节
  // ============================================================
  _tickIntro(dt) {
    // 登场：合并猫浮现（画层做 alpha 渐入），偶尔丢一颗环形弹当预告
    this._emit += dt;
    if (this._emit > 1.4) { this._emit = 0; this._ring('choco', 8, 2.4, 'amber'); }
  }

  _tickRing(dt) {
    this._emit += dt;
    if (this._emit > 1.15) { this._emit = 0; this._ring('choco', 16 + this.phase * 4, 3.0, 'amber'); }
  }

  _tickFan(dt) {
    this._emit += dt;
    if (this._emit > 1.05) {
      this._emit = 0;
      this._fan(this._alt % 2 ? 'vanilla' : 'choco');
      this._alt++;
    }
  }

  _tickWall(dt) {
    this._emit += dt;
    if (this._emit > 2.4) {
      this._emit = 0;
      const a = (Math.random() < 0.5 ? 0 : Math.PI / 2) + (Math.random() - 0.5) * 0.3;
      this._wall(this._alt % 2 ? 'vanilla' : 'choco', a);
      this._alt++;
    }
  }

  _tickPumpkin(dt) {
    this._emit += dt;
    if (this._emit > 0.55) { this._emit = 0; this._pumpkin(); }
  }

  _tickSplit(dt) {
    // 分裂演出：前 2.5s 合并猫闪烁蓄力 → 拆成双猫（保留原版 25% HP 拆分
    // 的结构神韵，改成纯演出节点 —— 用户拍板纯演出不打血）
    if (this.merged && this.phaseT > 2.5) {
      this.merged = false;
      this.splitFx = 0.6;
      this.splitAt = this.t;     // 渲染层据此做"从中间飞向两侧"的缓动
      this.flash = 1;            // 全屏白闪
      this.shake = 1;            // 屏幕震动（报告 §5：变身/大招 ±4px 8 帧衰减）
      sfx('hit13', 0.5);   // 原版分裂音
    }
  }

  _tickAltRing(dt) {
    this._emit += dt;
    if (this._emit > 1.0) {
      this._emit = 0;
      this._ring(this._alt % 2 ? 'vanilla' : 'choco', 24, 3.2, this._alt % 2 ? 'gold' : 'amber');
      this._alt++;
    }
  }

  _tickLaser(dt) {
    // 双猫旋转扫射激光：每束绕自身缓慢旋转，持续扫过场地（玩家找角速度差）
    this._emit += dt;
    if (this._emit > 2.2) {
      this._emit = 0;
      const who = this._alt % 2 ? 'vanilla' : 'choco';
      this.waves.push({ ...this._catPos(who), angle: Math.random() * TAU, rot: (Math.random() < 0.5 ? 1 : -1) * 0.9, t: 2.2, life: 2.2, who });
      this._alt++;
      sfx('laser13', 0.3);   // 原版激光音
    }
    for (const b of this.waves) {
      b.angle += b.rot * dt;
      // 前 0.7s 是预警细线（渲染层画淡线），不伤人 —— 给走位反应时间
      if (b.life - b.t < 0.7) continue;
      // 激光伤害走 halloweenHit 同款 10% 真伤：判定 = 玩家到射线垂距
      for (const pl of this.main.world.players()) {
        if (!pl || pl.dead || pl.downed || pl.invuln > 0 || pl.halloweenHit == null) continue;
        const dx = pl.x - b.x, dy = pl.y - b.y;
        const proj = dx * Math.cos(b.angle) + dy * Math.sin(b.angle);
        if (proj <= 0) continue;
        const perp = Math.abs(-dx * Math.sin(b.angle) + dy * Math.cos(b.angle));
        if (perp < 20) pl.halloweenHit(b.x, b.y);   // 视觉软边半宽 ≈ 28，核心更窄，判定收到 20
      }
    }
    this.waves = this.waves.filter((b) => (b.t -= dt) > 0);
  }

  /**
   * 角落封堵（185 轮，用户："双猫boss战的角落较为安全，适当增加飞往角落的弹幕吧"）。
   * 环/扇/墙都以猫或玩家为基准，躲在场地四角的玩家几乎碰不到任何东西。
   * 阶段 2 起每 CORNER_PERIOD[阶段] 秒，由当前出手的猫向四个角**逐个错开 0.3s**各打一束 3 发扇形弹
   * （不同时到达 → 角落里的人有读弹的时间，但不能一直赖着）。弹速 2.3 px/帧，射程盖过对角线。
   * 阶段 1（开场/环）不加；开场、分裂、糖果雨不加（见 NO_CORNER_SECTIONS）。
   */
  _tickCorners(dt) {
    const period = CORNER_PERIOD[this.phase];
    if (!period) return;
    this._cornerT = (this._cornerT ?? period * 0.5) - dt;
    if (this._cornerT > 0) return;
    this._cornerT = period;
    const who = this._cornerAlt++ % 2 ? 'vanilla' : 'choco';
    const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
    const start = (this._cornerRot = ((this._cornerRot ?? -1) + 1) % 4);   // 每轮从不同的角开始，别让人背
    for (let k = 0; k < 4; k++) {
      const [sx, sy] = corners[(start + k) % 4];
      const tx = ARENA.cx + sx * (ARENA.hw - 30), ty = ARENA.cy + sy * (ARENA.hh - 30);
      this._q.push({
        at: this.t + k * 0.3,
        fn: () => {
          const p = this._catPos(who);
          const base = Math.atan2(ty - (p.y + 20), tx - p.x);
          for (let i = -1; i <= 1; i++) this._shot(p.x, p.y + 20, base + i * 0.1, 2.3, { color: 'cyan', radius: 6, range: 1500, hwKind: 'corner' });
        },
      });
    }
  }

  /**
   * 灯珠字母弹触底散开（202 轮）。
   * 用户人工游玩："让那些拼成文字的弹幕在触碰底部的时候随机散开发射而不是只是沉下去就没了。"
   * 之前灯珠落过场地底边后继续往下沉，直到射程用尽才消失。现在：灯珠碰到场地底边 → 当场消失，
   * 在碰撞点向上半圆随机方向溅出 1 颗小弹（金色，速度/射程随机），整个单词落地时就是一场「碎成金粉」的飞溅。
   * 溅射弹是普通弹（走 650 上限，不占灯珠专用的 900 额度），所以单词再大也不会撑爆弹量。
   */
  _tickBeadSplash() {
    const floor = ARENA.cy + ARENA.hh - BEAD_SPLASH_Y;
    const list = this.main.world.enemyBullets.items;
    let hit = null;
    for (let i = 0; i < list.length; i++) {
      const b = list[i];
      if (b.dead || b.hwKind !== 'bead' || b.y < floor) continue;
      b.dead = true;
      (hit ??= []).push(b);
    }
    if (!hit) return;
    for (const b of hit) {
      // 上半圆随机方向（避开贴地的水平方向，±0.3rad），速度 1.0~3.0 px/帧，射程 140~360
      const ang = -Math.PI + 0.3 + Math.random() * (Math.PI - 0.6);
      this._shot(b.x, floor, ang, 1.0 + Math.random() * 2.0, { color: 'gold', radius: 4.5, hitRadius: 3.5, range: 140 + Math.random() * 220 });
    }
  }

  /** 离 (x,y) 最近的存活且未倒地的玩家（没有则 null） */
  _nearestAlive(x, y) {
    let best = null, bd = Infinity;
    for (const pl of this.main.world.players()) {
      if (!pl || pl.dead || pl.downed) continue;
      const d = (pl.x - x) ** 2 + (pl.y - y) ** 2;
      if (d < bd) { bd = d; best = pl; }
    }
    return best;
  }

  _tickClaw(dt) {
    // 追踪猫爪：慢速追踪弹（转向速率有限，走位拉扯可以甩掉）
    // 185 轮（用户："最后阶段那5个追踪紫球会散出一大堆弹幕很不错，但是持续时间太短了，还没怎么追就结束了……
    // 让她们持续更久一些。不然现在还没造成威胁就消散了"）：
    //   节长 7→13s、爪寿命 6→12s、转向 2.2→2.8 rad/s；追**最近的活人**（原来永远追第一个活着的，其余玩家几乎无感）；
    //   发射间隔 1.2→1.6s，同屏最多 CLAW_MAX 只，避免寿命翻倍后弹量爆炸。
    this._emit += dt;
    if (this._emit > CLAW_INTERVAL) {
      this._emit = 0;
      const who = this._alt % 2 ? 'vanilla' : 'choco';
      const px = this._catPos(who);
      const target = this._nearestAlive(px.x, px.y);
      if (target) {
        const base = Math.atan2(target.y - px.y, target.x - px.x);
        for (let i = 0; i < CLAW_PER_VOLLEY; i++) {
          const a = base + (i - (CLAW_PER_VOLLEY - 1) / 2) * 0.5;   // 2 只：±0.25rad；3 只：-0.5/0/+0.5
          const b = { x: px.x, y: px.y + 20, vx: Math.cos(a) * V(1.1), vy: Math.sin(a) * V(1.1), t: CLAW_LIFE_S, life: CLAW_LIFE_S };
          const list = (this._claws ??= []);
          list.push(b);
          if (list.length > CLAW_MAX) list.shift();   // 超限丢最老的
        }
      }
      this._alt++;
    }
    for (const c of (this._claws ?? [])) {
      const target = this._nearestAlive(c.x, c.y);
      if (target) {
        const want = Math.atan2(target.y - c.y, target.x - c.x);
        const cur = Math.atan2(c.vy, c.vx);
        let da = want - cur;
        while (da > Math.PI) da -= TAU;
        while (da < -Math.PI) da += TAU;
        const turn = Math.max(-CLAW_TURN * dt, Math.min(CLAW_TURN * dt, da));   // 转向速率上限
        const sp = Math.hypot(c.vx, c.vy);
        const na = cur + turn;
        c.vx = Math.cos(na) * sp; c.vy = Math.sin(na) * sp;
      }
      c.x += c.vx * dt; c.y += c.vy * dt;
      c.t -= dt;
      // 余烬弹：按**时间**发（原来是每帧 55% 概率 → 9 只爪 ≈ 300 发/秒，帧率越高越多），
      // 且短命（射程 70px ≈ 2.9s；原来 900px 能在场上躺 37 秒，把 650 的弹幕上限吃满）
      // 202 轮（用户人工游玩："最后的紫色追踪弹散出去的小子弹飞行时间飞行速度都翻倍看看，现在散出去一点点就消失了，几乎不可能打中人"）：
      // 速度 0.4→0.8 px/帧（24→48 px/s），飞行时间 2.9s→5.8s（射程 70→280 = 48 px/s × 5.8s）。
      if (Math.random() < dt * 7) this._shot(c.x, c.y, Math.random() * TAU, EMBER_SPEED, { color: 'violet', radius: 5, hitRadius: 4, range: EMBER_RANGE });
    }
    this._claws = (this._claws ?? []).filter((c) => c.t > 0
      && Math.abs(c.x - ARENA.cx) < ARENA.hw + 80 && Math.abs(c.y - ARENA.cy) < ARENA.hh + 80);
  }

  _tickCandy(dt) {
    // 糖果雨：随机落下，落地弹跳两下（垂直速度 ×-0.55，水平保留）
    this._emit += dt;
    if (this._emit > 0.3) {
      this._emit = 0;
      const x = ARENA.cx + (Math.random() * 2 - 1) * (ARENA.hw - 40);
      this._candies ??= [];
      this._candies.push({ x, y: ARENA.cy - ARENA.hh - 20, vx: (Math.random() - 0.5) * 160, vy: 60, bounces: 2, t: 7, color: ['red', 'gold', 'green', 'cyan'][(Math.random() * 4) | 0] });
    }
    const floor = ARENA.cy + ARENA.hh;
    for (const c of (this._candies ?? [])) {
      c.vy += 900 * dt;   // 伪横版重力
      c.x += c.vx * dt; c.y += c.vy * dt;
      if (c.y > floor && c.bounces > 0) {
        c.y = floor; c.vy = -c.vy * 0.55; c.bounces--;
      }
      if (c.y > floor && c.bounces <= 0) {
        // 落定炸开 4 向小弹
        for (let i = 0; i < 4; i++) this._shot(c.x, c.y, i * Math.PI / 2 + Math.PI / 4, 2.0, { color: c.color, radius: 5 });
        c.t = 0;
      }
      c.t -= dt;
    }
    this._candies = (this._candies ?? []).filter((c) => c.t > 0);
  }

  _tickRage(dt) {
    // 狂暴合奏：环形 + 扇 + 南瓜雨三线叠加，1.3× 节奏（BGM 高潮对齐这里）
    this._emit += dt;
    const k = 1 / 1.3;
    if (this._emit > 1.15 * k) { this._emit = 0; this._ring(this._alt % 2 ? 'vanilla' : 'choco', 30, 3.6, 'red'); this._alt++; }
    this._fanT = (this._fanT ?? 0) + dt;
    if (this._fanT > 1.4 * k) { this._fanT = 0; this._fan('choco'); this._fan('vanilla'); }
    this._pkT = (this._pkT ?? 0) + dt;
    if (this._pkT > 0.7 * k) { this._pkT = 0; this._pumpkin(); }
  }

  // ============================================================
  update(dtReal) {
    if (this.over) return;
    const dt = dtReal * this.timeScale;
    this.t += dt;

    // 黑屏换场：t<0 期间只走计时（渲染层按 t 画黑幕），t 过 0 的瞬间
    // 场景变亮 + BGM 起（用户："bgm也是场景变亮之后放"）
    if (this.t < 0) return;
    if (!this._bgmOn) {
      this._bgmOn = true;
      bgmStart();   // 原曲 Mischievous Masquerade（wav 直播）
    }

    // 视觉衰减量：分裂光斑 / 白闪 / 震动
    this.splitFx = Math.max(0, this.splitFx - dt);
    this.flash = Math.max(0, this.flash - dt * 1.8);
    this.shake = Math.max(0, this.shake - dt * 2.2);

    // 定时队列（弹墙分波 / 灯珠字母逐列点亮）
    if (this._q.length) {
      const keep = [];
      for (const e of this._q) { if (e.at <= this.t) e.fn(); else keep.push(e); }
      this._q = keep;
    }

    this._tickBeadSplash();

    // 拖尾位置采样（纯视觉）：每发弹每移动 ≥6px 记一个点，最多 6 个 → 渲染成余烬点链
    for (const b of this.main.world.enemyBullets.items) {
      if (!b.halloween || b.hwKind === 'bead') continue;
      const tr = b._tr ?? (b._tr = []);
      const n = tr.length;
      if (n === 0 || (b.x - tr[n - 2]) ** 2 + (b.y - tr[n - 1]) ** 2 >= 36) {
        tr.push(b.x, b.y);
        if (tr.length > 12) tr.splice(0, 2);
      }
    }

    // 南瓜落地/爆风
    for (const pk of this.pumpkins) {
      if (pk.t > 0) {
        pk.t -= dt;
        if (pk.t <= 0) {
          pk.boom = 0.35;
          pk.age = 0;           // 爆后时间：渲染层据此做"生长 → 判定期 → 余辉淡出"
          sfx('etc13', 0.25);   // 原版爆风音
          for (let i = 0; i < 4; i++) {
            this._shot(pk.x, pk.y, i * Math.PI / 2, 3.0, { color: 'amber', radius: 8, range: 420 });
          }
        }
      } else {
        pk.age = (pk.age ?? 0) + dt;
        // 判定窗口仍是 0.35s（boom）；age 超过它之后只是视觉余辉，**不再伤人**
        if (pk.boom > 0) {
          pk.boom -= dt;
          // 爆风持续判定：十字线附近 30px 内的玩家吃 10% 真伤
          for (const pl of this.main.world.players()) {
            if (!pl || pl.dead || pl.downed || pl.invuln > 0 || pl.halloweenHit == null) continue;
            const dx = pl.x - pk.x, dy = pl.y - pk.y;
            if ((Math.abs(dx) < 30 || Math.abs(dy) < 30) && Math.hypot(dx, dy) < 210) pl.halloweenHit(pk.x, pk.y);
          }
        }
      }
    }
    // 余辉 0.9s 才移除（用户："十字火焰断的有点太明显了，稍微过渡一丢丢"）
    this.pumpkins = this.pumpkins.filter((pk) => pk.t > 0 || (pk.age ?? 0) < 0.9);

    // 时间轴推进
    if (this.phaseIdx < this.sections.length) {
      const sec = this.sections[this.phaseIdx];
      const step = Math.min(dt, sec.dur - this.phaseT);
      // 206 轮（用户："白猫（香草）会卡最后的激光很久很久，让她持续一会就消失"）：
      // 激光束的寿命只在 _tickLaser 里递减，而它只在 laser 那一节被调用 ——
      // 章节一结束，最后几束就再也没人 tick，永远冻在屏幕上。
      // 这里补一条"离节残留衰减"：不在 laser 节时，把剩下的束按寿命继续走完。
      if (sec.name !== 'laser' && this.waves.length) {
        this.waves = this.waves.filter((b) => (b.t -= dt) > 0);
      }
      // 单词"独奏窗口"：带单词的节开场 1.6s 不叠加其他攻击，让灯珠字先被看清
      // （实测：与环形/扇形弹混在一起时，义眼和玩家都读不出单词）
      if (!(sec.word && this.phaseT < 1.6)) {
        sec.tick.call(this, dt, this.t);
        if (!NO_CORNER_SECTIONS.has(sec.name)) this._tickCorners(dt);
      }
      this.phaseT += step;
      if (this.phaseT >= sec.dur) { this.phaseIdx++; this.phaseT = 0; this._emit = 0; this.spellT = 0; this.spellT += 0; }
      this.timeLeft = Math.max(0, this.totalShow - this.t);
    } else {
      // outro：收招挥手 → 飞向月亮离场 → 胜利
      this.outroT = (this.outroT ?? 0) + dt;
      this.cats.choco.y = CAT_Y - this.outroT * 130;
      this.cats.vanilla.y = CAT_Y + 26 - this.outroT * 150;
      this.cats.choco.x += 60 * dt; this.cats.vanilla.x += 90 * dt;
      if (this.outroT > 4.2) this.finish('beat');
      return;
    }

    // 当前节攻击名计时（渲染层画扫入大字）
    const curSec = this.sections[this.phaseIdx];
    if (curSec && curSec.spell !== this.curSpell) {
      this.curSpell = curSec.spell; this.spellT = 0;
      if (curSec.word) this._spawnWord(curSec.word);   // 换招 → 灯珠单词坠落
    }
    this.spellT += dt;

    // 帧动画推进（待机/施法循环；狂暴期固定攻击帧）
    for (const who of ['choco', 'vanilla']) {
      const c = this.cats[who];
      c.ft += dt;
      const casting = this.phaseIdx >= 1;
      const frames = casting && this.phaseIdx >= 10 ? CAST_F : casting ? [10, 11, 12, 13] : IDLE_F;
      if (c.ft > 0.14) { c.ft = 0; c.frame = frames[(frames.indexOf(c.frame) + 1) % frames.length] ?? frames[0]; }
    }

    // 玩家全队失去战斗力 → 战败
    const team = this.main.world?.players?.() ?? [this.main.player];
    if (team.every((pl) => !pl || pl.dead || pl.downed)) { this.finish('death'); }

    // 素材加载诊断（v0.3.88 热修：用户实测"没素材，默认几何猫" ——
    // 60 帧切图若有 404/被代理拦截，开战 5 秒后把失败数直接报出来，
    // 一眼定位是静态资源问题还是代码问题）
    this._assetCheckT = (this._assetCheckT ?? 0) + dt;
    if (this._assetCheckT > 5 && !this._assetChecked) {
      this._assetChecked = true;
      let ok = 0;
      for (let i = 0; i < 30; i++) {
        for (const k of ['choco', 'vanilla']) {
          const img = IMG[`${k}_${i}`];
          if (img && img.complete && img.naturalWidth > 0) ok++;
        }
      }
      if (ok < 60) {
        console.warn(`[halloween] 素材只加载 ${ok}/60 —— 检查 assets/boss/halloween/ 是否同步`);
        try { this.main.ui.toast(`万圣节素材缺失（${ok}/60）——请 Ctrl+F5 强刷或检查 assets 同步`, 'bad', 8000); } catch { /* */ }
      }
    }
  }

  finish(outcome) {
    if (this.over) return;
    this.over = true;
    this.outcome = outcome;
    try { audio.stopMusic(); } catch { /* */ }
    bgmStop();
    this.onDone(outcome);
  }

  /** 立绘帧图片（渲染层用） */
  frameImg(who, frame) {
    const img = IMG[`${who}_${frame}`];
    return img && img.complete && img.naturalWidth > 0 ? img : null;
  }

  frames(who) { return who === 'choco' ? CHOCO_FRAMES : VANILLA_FRAMES; }
}
