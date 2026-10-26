// ============================================================
// Boss 阶段系统
//
// 需求（用户原话）：
//   "给 boss 设计一下 2 阶段和 3 阶段，最好有一个阶段压迫感更强一些。
//    按照血量或者剩余时间转换状态。
//    （血量 33%/66% 转换或者时间剩下 33%/66% 转换，哪个先到先用哪个同时判定）"
//
// 也就是**双触发**：血量和时间各算一套阈值，谁先跨过就切阶段。
// 这样既保证"打得快也会变强"（血量触发），也保证"拖时间也会变强"
// （时间触发），不会出现玩家龟缩就永远见不到二三阶段的情况。
//
// 阶段含义：
//   1 阶段：开场，标准弹幕（100%）
//   2 阶段：血量 ≤66% 或 剩余时间 ≤66% → 弹幕变密、加速，开始加压
//   3 阶段：血量 ≤33% 或 剩余时间 ≤33% → **压迫感最强**：
//           全屏弹幕 + 连续冲锋 + 频繁召唤，屏幕会持续震动
//
// 数值设计原则：阶段提升的是"威胁密度"而不是"一发秒杀"，
// 玩家仍有走位空间（Boss 弹幕靠钻缝躲，不靠无敌帧硬吃）。
// ============================================================

/** 阶段阈值：血量比例与时间比例都用这组 */
export const PHASE_THRESHOLDS = [
  { phase: 3, at: 0.33 },
  { phase: 2, at: 0.66 },
];

// ---- 锁血参数（第九轮用户要求，见 BossPhaseState 里的说明）----

/** 登场无敌时长（秒） */
export const BOSS_SPAWN_INVULN = 5;
/** 每次转阶段的无敌时长（秒） */
export const BOSS_PHASE_INVULN = 5;
/** 撞到 33%/66% 卡点后的无敌时长（秒） */
export const BOSS_GATE_LOCK = 5;
/**
 * 一场 Boss 战最少要打多少次"有效伤害轮次"。
 *
 * 用户要求："让玩家伤害数值再高也至少需要打 boss 3 次伤害才行。"
 *
 * 拆开算：3 个无敌段（登场 + 2 阶段 + 3 阶段）本身就把战斗切成 4 段，
 * 再加 33%/66% 两个卡点各挡一次，所以高伤玩家也至少要分 3 轮才能打死。
 * 这个常数只用于测试断言与文档，逻辑上由上面的段数与卡点数共同保证。
 */
export const BOSS_MIN_DAMAGE_ROUNDS = 3;

/**
 * 每个阶段的战斗参数倍率。
 *
 * 只影响"出手频率/密度/速度"，不直接改 damage ——
 * 伤害仍然走 Enemy 的正常结算和护甲减免，避免阶段跳跃时瞬间秒人。
 */
export const PHASE_MODS = {
  1: {
    name: '第一阶段',
    fireRate: 1.0,      // 攻击冷却倍率（越小越快）
    bulletMul: 1.0,     // 弹幕条数倍率
    bulletSpeed: 1.0,
    summonRate: 1.0,
    moveSpeed: 1.0,
    charge: false,
    shake: 0,
    color: null,
  },
  2: {
    name: '第二阶段',
    fireRate: 0.78,     // 攻速 +28%
    bulletMul: 1.35,
    bulletSpeed: 1.12,
    summonRate: 0.78,
    moveSpeed: 1.10,
    charge: true,       // 开始冲锋
    shake: 5,
    color: '#ffb74d',
  },
  3: {
    name: '第三阶段 · 狂暴',
    fireRate: 0.58,     // 攻速 +72%
    bulletMul: 1.85,    // 弹幕几乎翻倍 → 压迫感来源
    bulletSpeed: 1.28,
    summonRate: 0.6,
    moveSpeed: 1.22,
    charge: true,
    shake: 13,          // 持续震屏
    color: '#ff3d6e',
  },
};

/**
 * 计算 Boss 当前应该处于哪个阶段。
 *
 * 判定方式完全按用户原话："血量 33%/66% 转换或者时间剩下 33%/66% 转换，
 * 哪个先到先用哪个同时判定"。
 *
 * 所以：
 *   血量比例 ≤ 66% → 至少 2 阶段；≤ 33% → 3 阶段
 *   剩余时间比例 ≤ 66% → 至少 2 阶段；≤ 33% → 3 阶段
 *
 * "哪个先到用哪个" = 两个比例里**更吃紧的那个**（掉血更多 / 时间更少），
 * 也就是 min。取 min 再比阈值，天然实现了"同时判定、谁先到算谁"。
 *
 * @param {object} opts
 * @param {number} opts.hpRatio      剩余血量比例 0~1
 * @param {number} opts.timeRatio    剩余时间比例 0~1（timeLeft / duration）
 * @param {number} [opts.prevPhase]  上一帧的阶段（只增不减）
 * @returns {number} 1 | 2 | 3
 */
export function computeBossPhase({ hpRatio, timeRatio, prevPhase = 1 }) {
  // 两个信号里"更吃紧"的那个（越小越接近结束）
  const worst = Math.min(clamp01(hpRatio), clamp01(timeRatio));

  let phase = 1;
  // 从高到低判断，保证跨过 66% 时直接落在正确的档位
  for (const t of PHASE_THRESHOLDS) {
    if (worst <= t.at) { phase = t.phase; break; }
  }
  // 阶段只增不减：避免血量被治疗或时间延长时掉回低阶段
  return Math.max(prevPhase, phase);
}

/**
 * Boss 阶段状态机：挂在 Enemy 实例上。
 * 只负责"判断该不该换阶段 + 换的时候播一次演出"，具体倍率由 mods getter 提供。
 */
export class BossPhaseState {
  constructor() {
    this.phase = 1;
    this.justChanged = false;   // 本帧是否刚切阶段（供上层播音效/吐槽）
    this.changeFx = 0;          // 切换演出剩余时间

    // ---- 无敌锁血（第九轮用户要求；第一百一十九轮改共享护盾池）----
    // 第九轮原话："给所有难度 boss 加一个转阶段锁血 5 秒的效果……
    //            刚登场 5 秒不受伤害，2 阶段 5 秒，3 阶段 5 秒，一共 15 秒，
    //            然后加个锁血检测（卡在 33% 和 66% 一次），
    //            让玩家伤害数值再高也至少需要打 boss 3 次伤害才行。"
    //
    // 第一百一十九轮用户原话："目前是时间每消耗33%boss进入无敌5秒，
    // 血量下降33%boss无敌5秒。也就是如果玩家打得慢，boss有可能锁血
    // 4-5次，远超我们设置的15秒锁血。改成33%血量和33%最后剩余时间共享
    // 一次护盾，有一个条件达成用了之后另一个条件达成时就用不了了……
    // 设置普通boss最多锁血3次共计15秒，之后无论发生啥都不给第四次锁血。"
    //
    // 共享规则（每个卡点一份护盾，先到先用）：
    //   gatesUsed[t] = 该卡点（2=66% / 3=33%）的护盾是否已被消费：
    //     · 血量侧先到 → applyDamageLock 截断伤害 + gateLockTimer 5 秒；
    //       之后时间侧跨过同一卡点，转阶段**不再给无敌**。
    //     · 时间侧先到 → update() 转阶段给 5 秒无敌；之后血量真的打到
    //       同一卡点，applyDamageLock 直接放行（既不截断也不再锁）。
    //   两个卡点 + 登场 = 全场最多 3 次锁血 = 15 秒，之后无第四次。
    this.invulnTimer = BOSS_SPAWN_INVULN;
    this.gatesUsed = { 2: false, 3: false };   // 每卡点的共享护盾（先到先用）
    this.gateLockTimer = 0;                    // 卡血时的无敌剩余时间
    this.lastBlocked = 0;                      // 本帧是否被无敌/卡血挡下（给 renderer 用）
  }

  /** 当前阶段的参数倍率 */
  get mods() { return PHASE_MODS[this.phase]; }

  /** 现在是否处于"打不动"的状态（登场无敌 / 转阶段无敌 / 卡血无敌） */
  get invulnerable() { return this.invulnTimer > 0 || this.gateLockTimer > 0; }

  /** 剩余无敌时间（给 UI / 特效用） */
  get invulnLeft() { return Math.max(this.invulnTimer, this.gateLockTimer); }

  /**
   * 结算一次伤害前的锁血检查。
   *
   * @param {number} hp     当前血量
   * @param {number} maxHp  血量上限
   * @param {number} dmg    这一发准备造成的伤害
   * @returns {{blocked:boolean, dmg:number, reason:string, gated:number}}
   *   blocked=true 表示完全没掉血（无敌中）；
   *   gated 非 0 表示这一发撞到了 33%/66% 卡点、伤害被截断。
   */
  applyDamageLock(hp, maxHp, dmg) {
    // 1) 无敌中：完全免伤
    if (this.invulnerable) {
      return { blocked: true, dmg: 0, reason: 'invuln', gated: 0 };
    }
    if (maxHp <= 0) return { blocked: false, dmg, reason: '', gated: 0 };

    // 2) 卡点检测：把这一发会跨过的阈值里**最高的那个**卡住。
    //
    //    顺序很关键：PHASE_THRESHOLDS 是按 at 升序写的（0.33 在前），
    //    但"一发秒杀"会同时跨过 0.66 和 0.33，这时必须停在 0.66 ——
    //    否则玩家会从 100% 直接跳到 33%，白白吃掉一个卡点，
    //    也拿不到 2 阶段的演出。所以这里按 at **降序**遍历。
    //
    //    例：血 100%、一发打 90% → 跨过 0.66 和 0.33 → 停在 66%（gated=2）。
    const byDesc = [...PHASE_THRESHOLDS].sort((a, b) => b.at - a.at);
    for (const t of byDesc) {
      if (this.gatesUsed[t.phase]) continue;
      const gateHp = maxHp * t.at;
      if (hp > gateHp && hp - dmg < gateHp) {
        this.gatesUsed[t.phase] = true;
        this.gateLockTimer = BOSS_GATE_LOCK;
        // 只打到卡点为止（保底 1 点，避免出现 0 伤害的观感问题）
        const allowed = Math.max(1, hp - gateHp);
        return { blocked: false, dmg: allowed, reason: 'gate', gated: t.phase };
      }
    }
    return { blocked: false, dmg, reason: '', gated: 0 };
  }

  /**
   * 每帧更新。
   * @returns {number} 本次是否发生了阶段提升（0 = 没变，2/3 = 新阶段）
   */
  update(hpRatio, timeRatio) {
    const next = computeBossPhase({ hpRatio, timeRatio, prevPhase: this.phase });
    this.justChanged = false;
    if (next > this.phase) {
      this.phase = next;
      this.justChanged = true;
      this.changeFx = 1.6;            // 切换演出时长
      // 第一百一十九轮：共享护盾 —— 该卡点的护盾若已被血量侧（卡点锁血）
      // 用掉，时间侧转阶段不再给无敌；反之血量侧撞卡点时会因 gatesUsed
      // 已置位而直接放行。转阶段的无敌：取"剩余无敌"和"完整 5 秒"里更
      // 长的那个（避免登场无敌还剩 0.2 秒时刚好转阶段、只无敌 0.2 秒）。
      if (!this.gatesUsed[next]) {
        this.gatesUsed[next] = true;
        this.invulnTimer = Math.max(this.invulnTimer, BOSS_PHASE_INVULN);
      }
      return next;
    }
    return 0;
  }

  tick(dt) {
    if (this.changeFx > 0) this.changeFx -= dt;
    if (this.invulnTimer > 0) this.invulnTimer = Math.max(0, this.invulnTimer - dt);
    if (this.gateLockTimer > 0) this.gateLockTimer = Math.max(0, this.gateLockTimer - dt);
    if (this.lastBlocked > 0) this.lastBlocked = Math.max(0, this.lastBlocked - dt);
  }
}

function clamp01(v) {
  if (!Number.isFinite(v)) return 0;
  return v < 0 ? 0 : v > 1 ? 1 : v;
}