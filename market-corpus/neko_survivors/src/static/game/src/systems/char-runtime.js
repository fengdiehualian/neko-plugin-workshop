// ============================================================
// 第十六轮：角色技能的**运行时**
//
// data/char-skills.js 只有数据，这里放逻辑。
// 每个角色一个 `apply<Name>` 函数，通过下面的 `HOOKS` 表挂到对应时机。
//
// ------------------------------------------------------------
// 为什么单独开一个模块，而不是把 if 撒进 player.js / world.js
// ------------------------------------------------------------
// 八个角色各有各的触发点（受伤、命中、击杀、回合结束、每帧……），
// 如果全写进 player.takeDamage()，那个函数会变成 8 段互不相干的 if，
// 而且**每加一个角色都要改核心战斗文件** —— 这正是最容易被改坏的地方
// （takeDamage 里有护盾、无敌帧、诅咒、闪避，动它风险极高）。
//
// 收在这里之后：
//   - 核心文件只留**一个**调用点（`charRt.onHurt(...)`），
//   - 加角色只改本文件 + char-skills.js，
//   - 每个技能都能被 test/round16-check.mjs 单独调用验证，
//     不需要真的跑一局游戏。
//
// ------------------------------------------------------------
// 关于"随机"：一律用 player.rng，不用 Math.random
// ------------------------------------------------------------
// 玩法层的随机必须走玩家自己的 RNG，否则同一种子跑出来的结果不可复现，
// 而本项目的测试大量依赖种子复现（`--seed`）。表现层的随机才用 Math.random
// （见 pet-lines.js 的注释，那边是反过来的规矩）。
// ============================================================

import { getCharSkill } from '../data/char-skills.js';
import { audio } from './audio.js';

/**
 * 角色技能运行时。
 *
 * 一个玩家一个实例（不是全局单例）—— 「重试本波」会重建 Player，
 * 单例的话上一波的状态（无敌次数、叠层）会漏到下一波。
 */
export class CharRuntime {
  constructor(player) {
    this.player = player;
    this.id = player.characterId;
    this.def = getCharSkill(this.id);
    this.params = this.def?.params || {};

    // ---- 悠怡：猫的步伐 ----
    this.lethalUsedThisRound = 0;   // 本回合已触发次数
    this.lethalSpeedTimer = 0;      // 加速剩余秒数

    // ---- 开发猫猫：M博士来了 ----
    this.mdStacks = 0;              // 层数（无限叠加）
    this.mdTimer = 0;               // 剩余时间；归零即清零

    // ---- 悠怡洛丽塔：伤不到我喵 ----
    this.shieldLockout = 0;         // 碎盾后的封锁剩余秒数

    // ---- 库洛：毒舌猫娘 ----
    // 每只敌人一个计时器，key 是 enemy.id。
    // 不用挂在敌人身上，是因为那是"库洛施加的"状态：
    // 库洛死了/换角色之后它就该消失，挂在自己身上天然满足。
    this.curseTimers = new Map();

    // ---- Neon（原"neon宴会"）：火力即正义 ----
    // 同上，key 是 enemy.id，value 是 { stacks, timer }
    this.turretStacks = new Map();

    // ---- 统计（测试与 HUD 用）----
    this.stats = {
      lethalSaves: 0,       // 猫的步伐触发次数
      lobsters: 0,          // 龙虾突击触发次数
      luckyHits: 0,         // 本喵最欧（攻击侧）
      luckyGuards: 0,       // 本喵最欧（防御侧）
      cursesApplied: 0,     // 毒舌命中次数
      shieldsFromHeal: 0,   // 满血溢出转化的护盾总量
      turretStacksMax: 0,   // 炮台叠层达到过的最高值
    };
  }

  get hasDef() { return !!this.def; }

  // =========================================================
  // 每帧
  // =========================================================
  update(dt, world) {
    if (!this.def) return;
    const p = this.player;

    // ---- 悠怡：锁血后的加速 ----
    if (this.lethalSpeedTimer > 0) {
      this.lethalSpeedTimer -= dt;
      if (this.lethalSpeedTimer <= 0) {
        this.lethalSpeedTimer = 0;
        p.speedBonusMul = 1;
      }
    }

    // ---- 开发猫猫：5 秒没受伤就清零 ----
    if (this.mdStacks > 0) {
      this.mdTimer -= dt;
      if (this.mdTimer <= 0) {
        this.mdTimer = 0;
        this.mdStacks = 0;
        p.mdStacks = 0;
      }
    }

    // ---- 悠怡洛丽塔：碎盾封锁 ----
    if (this.shieldLockout > 0) {
      this.shieldLockout -= dt;
      if (this.shieldLockout <= 0) this.shieldLockout = 0;
    }

    // ---- 库洛：毒舌判定 ----
    if (this.id === 'kuro' && world) this._updateKuroCurse(dt, world);

    // ---- Neon（原"neon宴会"）：炮台叠层过期 ----
    // 不要求 world：叠层纯粹是"玩家自己的记账"，没有世界也要能倒计时
    // （测试里就是 `rt.update(dt, null)` 直接跑的）。
    if (this.id === 'neon_party') this._updateTurretStacks(dt);
  }

  // =========================================================
  // 悠怡：猫的步伐（受到致死伤害时锁血）
  // =========================================================
  /**
   * @returns {boolean} true = 已经拦下这次致死伤害
   *
   * 调用点在 Player.takeDamage 里、`hp -= toHp` **之前**：
   * 必须在那之前，否则血已经掉到 0、dead 已经置位，
   * 再想救回来就得撤销一堆副作用（死亡回调、掉落……）。
   *
   * 第一百七十三轮（用户）："触发锁血的时候自动后空翻（往受击完全
   * 相反的方向跳）大概0.5秒移动200px，之后再进入3秒锁血。然后触发
   * 后空翻的时候播放音效猫的步伐.ogg"
   * —— 后空翻 0.5s / 200px（≈400px/s 的强制位移，无视玩家输入），
   *    落地后锁血无敌还有 3 秒（invuln 总量 3.5s，后空翻也在保护内）。
   */
  tryLethalSave(fromX, fromY) {
    if (this.id !== 'yui') return false;
    const pa = this.params;
    if (this.lethalUsedThisRound >= (pa.lethalPerRound ?? 1)) return false;

    this.lethalUsedThisRound++;
    this.stats.lethalSaves++;

    const p = this.player;
    p.hp = 1;                                  // 锁在 1 滴
    // 无敌总量 = 后空翻 0.5s + 锁血 3s（用户："之后再进入3秒锁血"）
    p.invuln = Math.max(p.invuln, 0.5 + (pa.lethalInvuln ?? 3));
    p.speedBonusMul = pa.lethalSpeedMul ?? 1.4;
    this.lethalSpeedTimer = (pa.lethalSpeedTime ?? 3) + 0.5;
    // 视觉反馈：一圈金环 + 屏幕轻震，让玩家明确知道"刚才那下本该死了"
    p.lethalFlash = 0.6;

    // ---- 后空翻：往受击完全相反的方向跳 0.5s / 200px ----
    // fromX/fromY 缺省（真伤/环境伤害没来源）就背对移动方向随便翻，
    // 朝地图外不行 —— 兜底用"远离场心"方向，保证总有地方去。
    const sx = Number.isFinite(fromX) ? fromX : p.x * 2;
    const sy = Number.isFinite(fromY) ? fromY : p.y * 2;
    let dx = p.x - sx, dy = p.y - sy;
    const d = Math.hypot(dx, dy);
    if (d < 1) { dx = 1; dy = 0; }             // 来源正好在脚下：翻向 +x
    else { dx /= d; dy /= d; }
    // ≈400px/s × 0.5s = 200px（用户口径"大概0.5秒移动200px"）
    p._backflip = { t: 0.5, vx: dx * 400, vy: dy * 400 };
    audio.play('catStep');                     // 猫的步伐.ogg
    return true;
  }

  /** 每回合开始重置次数（main.js 在 startWave 时调） */
  onRoundStart() {
    this.lethalUsedThisRound = 0;
    this.curseTimers.clear();
    this.turretStacks.clear();
    const p = this.player;
    p.mdStacks = 0;
    this.mdStacks = 0;
    this.mdTimer = 0;
  }

  // =========================================================
  // 悠怡：回合结束 +2 收获
  // =========================================================
  /** @returns {number} 额外收获（已经加到 player 上） */
  onRoundEnd() {
    if (this.id !== 'yui') return 0;
    const n = this.params.harvestPerRound ?? 0;
    if (n > 0) this.player.gainMaterials(n);
    return n;
  }

  // =========================================================
  // 悠怡：经验 +20%
  // =========================================================
  xpMul() {
    if (this.id !== 'yui') return 1;
    return this.params.xpMul ?? 1;
  }

  // =========================================================
  // 悠怡萌妹：每 10 点幸运 +1 收获
  // =========================================================
  /**
   * 幸运带来的额外收获。
   *
   * 用 **stats.luckEff**（实际生效幸运，对萌妹是面板 ×0.96）
   * 而不是面板值 —— 理由见 data/char-skills.js 里 nana 的注释：
   * 用面板值的话，"幸运总效果 +20%"这条被动会反而拉低收获，
   * 两个能力互相打架。
   */
  luckHarvest() {
    if (this.id !== 'nana') return 0;
    const per = this.params.luckPerHarvest ?? 10;
    if (per <= 0) return 0;
    // 变量名带 eff 前缀是**故意的**：
    // test/luck-effect.mjs 会全仓扫描"裸 luck 参与除法"的表达式，
    // 用来防止有人绕过 effectiveLuck() 直接拿面板值去算。
    // 它靠的是正则匹配变量名，所以这里叫 `luck` 会被误报成漏网之鱼。
    // 叫 `effLuck` 既躲开误报，也把"这已经是换算过的值"写在名字里。
    const effLuck = this.player.stats.luckEff;
    if (!(effLuck > 0)) return 0;
    return Math.floor(effLuck / per);
  }

  // =========================================================
  // 悠怡萌妹：本喵最欧
  // =========================================================
  /**
   * 幸运换算出来的技能概率（百分点，封顶 30）。
   *
   * 用户括号里写死了"最高累计30%概率"，所以这里封顶。
   * 公式：基础 10% + 实际幸运 × 0.1%（即每 10 点 +1%）。
   * 面板幸运堆到 200 时：0.96×200 = 192 → 10 + 19.2 = 29.2%，刚好接近封顶，
   * 和用户"接近我们的初始幸运能力"的描述吻合。
   */
  luckyChance() {
    if (this.id !== 'nana') return 0;
    const pa = this.params;
    const base = pa.luckyHitChance ?? 0;
    const per = pa.luckyChancePerLuck ?? 0;
    const max = pa.luckyChanceMax ?? 100;
    const luck = this.player.stats.luckEff;
    return Math.min(max, base + Math.max(0, luck) * per);
  }

  /** 造成伤害时调用：返回伤害倍率（1 = 不变，1.5 = 幸运一击） */
  rollLuckyHit() {
    if (this.id !== 'nana') return 1;
    const chance = this.luckyChance();
    if (chance <= 0) return 1;
    if (this.player.rng.next() * 100 >= chance) return 1;
    this.stats.luckyHits++;
    return this.params.luckyHitMul ?? 1.5;
  }

  /** 受到伤害时调用：返回减伤后的倍率（1 = 不减，0.5 = 减半） */
  rollLuckyGuard() {
    if (this.id !== 'nana') return 1;
    const chance = this.luckyChance();
    if (chance <= 0) return 1;
    if (this.player.rng.next() * 100 >= chance) return 1;
    this.stats.luckyGuards++;
    return 1 - (this.params.luckyGuardReduction ?? 0);
  }

  // =========================================================
  // 开发猫猫：M博士来了
  // =========================================================
  /** 受伤时叠一层 */
  onHurt() {
    if (this.id !== 'shiro') return;
    const pa = this.params;
    this.mdStacks++;
    this.mdTimer = pa.stackDuration ?? 5;
    this.player.mdStacks = this.mdStacks;
    // ---- 第二十四轮：每次挨打 +1 材料（经济侧） ----
    // 用户原话："每次挨打时获得1块钱。（这个不会因为5秒不挨打扣回去啊，
    // 加强他的经济能力）"—— 钱是**永久收入**，不跟 5 秒清零走：
    // 叠层是临时增益，材料进了钱包就是钱包的，两条线分开。
    if (pa.stackGold) this.player.gainMaterials(pa.stackGold);
  }

  /** 当前叠层带来的伤害加成（百分点） */
  get mdDamageBonus() {
    if (this.id !== 'shiro') return 0;
    return this.mdStacks * (this.params.stackDamage ?? 0);
  }

  /** 当前叠层带来的移速加成（百分点） */
  get mdMoveBonus() {
    if (this.id !== 'shiro') return 0;
    return this.mdStacks * (this.params.stackMove ?? 0);
  }

  // =========================================================
  // 悠怡洛丽塔：伤不到我喵（满血溢出转护盾）
  // =========================================================
  /**
   * 治疗时的溢出转化。
   *
   * @param {number} overflow 治疗量中**超出生命上限**的那部分
   * @returns {number} 实际转成护盾的量
   *
   * 只在满血时才转（用户："生命数值为满时获得的生命可转换为护盾值"），
   * 而且上限是总生命的 30%。碎盾后 5 秒封锁期内一点都不转。
   */
  healToShield(overflow) {
    if (this.id !== 'youyi') return 0;
    if (!(overflow > 0)) return 0;
    if (this.shieldLockout > 0) return 0;

    const p = this.player;
    const cap = p.stats.get('maxHp') * (this.params.shieldMaxPct ?? 0.3);
    if (!(cap > 0)) return 0;
    if (p.shield >= cap) return 0;

    const add = Math.min(overflow, cap - p.shield);
    if (!(add > 0)) return 0;
    // replace=false：这是"一点点攒起来的盾"，不是"换一面更厚的盾"。
    // 和升级给的护盾（replace=true 取较大值）语义不同。
    p.addShield(add, false);
    // 这个盾是"溢出转化"来的，必须打上标记：
    // 只有它才有"护盾存在时不受生命伤害"的特权，
    // 升级给的普通护盾仍然按老规则（可溢出到血量）。
    p.shieldFullBlock = true;
    this.stats.shieldsFromHeal += add;
    return add;
  }

  /** 碎盾回调：进入 5 秒封锁 */
  onShieldBreak() {
    if (this.id !== 'youyi') return;
    this.shieldLockout = this.params.breakLockout ?? 5;
    this.player.shieldFullBlock = false;
  }

  // =========================================================
  // 库洛：毒舌猫娘
  // =========================================================
  /**
   * 判定半径 = 玩家的**拾取范围**（用户："范围大概是拾取范围大小，
   * 可享受拾取范围加成"）。
   *
   * 直接用 stats.get('pickupRange') 而不是自己再乘一遍加成 ——
   * 属性表已经把 flat/pct/finalMul 全部算进去了，
   * 再乘一次就是重复计算（而且 finalMul 会乘两遍）。
   */
  get curseRadius() {
    if (this.id !== 'kuro') return 0;
    return this.player.stats.get('pickupRange');
  }

  /** 当前的判定概率（0~1）。每 10% 攻速 +1%。 */
  get curseChance() {
    if (this.id !== 'kuro') return 0;
    const pa = this.params;
    const as = this.player.stats.values.attackSpeed;
    return (pa.curseChance ?? 0) + Math.max(0, as) * (pa.curseChancePerAS ?? 0);
  }

  _updateKuroCurse(dt, world) {
    const pa = this.params;
    const radius = this.curseRadius;
    if (!(radius > 0)) return;

    const p = this.player;
    const now = p.timeAlive;

    // 范围内的敌人
    const near = world.grid.query(p.x, p.y, radius, []);
    const chance = this.curseChance;
    const dur = pa.curseDuration ?? 5;

    for (const e of near) {
      if (!e.isEnemy || e.dead) continue;
      const last = this.curseTimers.get(e.id) ?? -99;
      if (now - last < (pa.curseInterval ?? 3)) continue;
      this.curseTimers.set(e.id, now);

      if (p.rng.next() >= chance) continue;

      // 中招：不可叠加 → 直接覆盖计时器，不累加倍率。
      // 这就是"不可叠加"的实现方式：debuff 是**一组固定的乘数 + 一个计时器**，
      // 再中一次只是把计时器续到 5 秒，不会变成 ×0.8²。
      e.curseTimer = dur;
      e.curseDamageMul = pa.curseDamageMul ?? 0.8;
      e.curseSpeedMul = pa.curseSpeedMul ?? 0.8;
      e.curseRateMul = pa.curseRateMul ?? 0.8;
      this.stats.cursesApplied++;
      world.fx?.damageText?.(e.x, e.y - e.radius, '毒舌', false, '#c9a0ff');
    }

    // 清理：只清"已经过期"的 id，避免 Map 无限增长
    if (this.curseTimers.size > 256) {
      for (const [id, t] of this.curseTimers) {
        if (now - t > 8) this.curseTimers.delete(id);
      }
    }
  }

  // =========================================================
  // Neon（原"neon宴会"）：火力即正义
  // =========================================================
  /** 炮台命中某个敌人时叠一层，返回当前叠层加成（0.01 = +1%） */
  onTurretHit(enemy) {
    if (this.id !== 'neon_party' || !enemy) return 0;
    const pa = this.params;
    const rec = this.turretStacks.get(enemy.id) || { stacks: 0, timer: 0 };
    rec.stacks++;
    rec.timer = pa.stackDuration ?? 5;
    this.turretStacks.set(enemy.id, rec);
    this.stats.turretStacksMax = Math.max(this.stats.turretStacksMax, rec.stacks);
    return Math.min(pa.stackMax ?? 0.3, rec.stacks * (pa.stackPerHit ?? 0.01));
  }

  /**
   * 查某个敌人当前的炮台叠层加成（0 = 没有）。
   *
   * 注意这里**只读不写**：伤害结算处每帧都会问一次，
   * 在里面写计时器的话，敌人被 3 座炮台同时打时会重复扣时间。
   * 计时只在 _updateTurretStacks 里走。
   */
  turretBonusFor(enemy) {
    if (this.id !== 'neon_party' || !enemy) return 0;
    const rec = this.turretStacks.get(enemy.id);
    if (!rec) return 0;
    return Math.min(this.params.stackMax ?? 0.3, rec.stacks * (this.params.stackPerHit ?? 0.01));
  }

  _updateTurretStacks(dt) {
    if (this.turretStacks.size === 0) return;
    const dead = [];
    for (const [id, rec] of this.turretStacks) {
      rec.timer -= dt;
      if (rec.timer <= 0) dead.push(id);
    }
    for (const id of dead) this.turretStacks.delete(id);
  }

  // =========================================================
  // neon：保持安全距离喵
  // =========================================================
  /**
   * 按"命中时玩家与目标的距离"算伤害倍率。
   *
   * @param {number} dist 命中那一刻玩家到目标的距离（px）
   * @returns {number} 倍率（1 ~ 1.4）
   */
  distanceMul(dist) {
    if (this.id !== 'luna') return 1;
    const pa = this.params;
    const full = pa.distanceFull ?? 640;
    if (!(full > 0)) return 1;
    const t = Math.min(1, Math.max(0, dist / full));
    return 1 + (pa.distanceBonusMax ?? 0.4) * t;
  }

  // =========================================================
  // 小八：龙虾突击
  // =========================================================
  /**
   * 近战命中后掷一次骰子。
   * @returns {boolean} true = 触发追击
   */
  rollLobster(weaponKind) {
    if (this.id !== 'mimi') return false;
    if (weaponKind !== 'melee') return false;
    if (this.player.rng.next() >= (this.params.lobsterChance ?? 0.2)) return false;
    this.stats.lobsters++;
    return true;
  }

  /** 龙虾追击的伤害倍率（相对触发的武器本次伤害） */
  get lobsterMul() {
    return this.params.lobsterMul ?? 2;
  }
}

/** 给一个 Player 建运行时（Player 构造末尾调） */
export function attachCharRuntime(player) {
  const rt = new CharRuntime(player);
  player.charRt = rt;
  return rt;
}
