// ============================================================
// 商店系统
// 每波结束后开放：买武器 / 买道具 / 合成武器 / 刷新 / 锁定
// 价格受幸运影响（幸运高时打折，且品质更好）
// ============================================================

import { WEAPONS, RECIPES, RECIPE_MAP, getWeapon, weaponsByTier, LEGENDARY_BASE_PRICE } from '../data/weapons.js';
import { ITEMS, getItem, itemTier } from '../data/items.js';
import { MAX_WEAPONS } from '../entities/player.js';
import { weaponArch, itemArch, archWeightMul, characterArch } from '../data/archetypes.js';
import { effectiveLuck } from '../core/stats.js';

export const SHOP_SLOTS = 4;

// ---- 第十二轮第 7 项：小小猫娘偷商店道具 ----
//
// 用户原话："加个小小猫娘偷取道具的功能，就是有时候她会从商店里偷一个
//            道具给你，默认20%，幸运越高概率越高最高50%，
//            并且悬浮提示3-5秒（小小猫娘帮你拿了xx道具）。"
//
// 设计要点：
//  * **幸运走 effectiveLuck**（总效果 80%），和商店其他概率一致 ——
//    直接读面板值会绕过第十轮的削弱，变成隐形加强。
//  * 概率区间严格 20%~50%：`STEAL_CHANCE_BASE + luckBonus × (MAX-BASE)`，
//    luckBonus 是 clamp01，所以数学上不可能越界。幸运 220 封顶
//    （和 _makeOffer 里 luckBonus 的分母一致）。
//  * 偷的是**货架上真实存在**的商品，偷完 `sold = true` 会从货架消失。
//    这一点很关键：如果偷完东西还在，玩家可以再买一次，等于白送两份。
//  * 免费 —— 这正是"偷"的意义。所以不给 `player.materials` 扣钱。
//  * 偷到的是**道具**（用户说"偷一个道具"）。如果货架上没有道具，
//    就偷不到，本次机会作废（不改成偷武器 —— 那会违背用户原话）。
export const STEAL_CHANCE_BASE = 0.20;   // 基础 20%
export const STEAL_CHANCE_MAX = 0.50;    // 幸运拉满 50%
// ⚠️ 这个上限的单位是 **effectiveLuck（实际生效的幸运）**，不是面板幸运。
//    和 _makeOffer 里的 `effectiveLuck(luck) / 220` 同一个分母，也就是
//    "和商店品质同口径"。因为 effectiveLuck = 面板 × 0.8（LUCK_EFFECT_MUL），
//    所以**面板幸运 275** 才拉满 —— 不是 220。
//    这么设计是为了和商店其他概率一致：如果这里改用面板值当分母，
//    幸运在"商店品质"和"偷取概率"两处就会有不同的收益曲线，
//    玩家会觉得其中一个在偷偷缩水。
export const STEAL_LUCK_CAP = 220;       // 生效幸运到达这个值就拉满

// ---- 第十八轮：两处新的经济压制（用户要求）----
//
// 用户原话：
//   "① 给所有商店售卖的物品的价格每回合+10%。我们之前加过噩梦难度的价格，
//      20 回合到 200% 售卖价格还是不够，玩家还是太富有了，很容易 6 传说。
//      这个改动可以让噩梦难度商品售价在 20 回合到 400%，减慢成型步伐。
//    ② 商店的刷新价格在 11 回合时永久+5，让玩家的消费增加，理由同上。"
//
// ① 为什么是"每回合 +10%"而不是把现有公式调陡：
//    用户给了明确的锚点 —— **噩梦 20 回合 = 400%**。
//    现有噩梦公式是 `1.5 + (w-10) × 0.10`（第 11 波起 +10%），
//    第 20 波（w=19）= 2.40 = 240%。
//    如果全难度统一改成每波 +10%，噩梦第 20 波 = 1 + 19×0.1 = 2.90 = 290%，
//    还是到不了 400%。
//
//    要精确落在 400%，得让噩梦在 20 波累计 3.0 的增量。既然用户
//    同时说了"每回合 +10%"和"20 回合 400%"，那就把两者都当成硬约束：
//    普通难度每波 +10%（第 20 波 290%），噩梦**在每波 +10% 的基础上
//    再叠加原有的分段涨幅**（第 11 波起额外 +10%/波）：
//      噩梦第 10 波 = 1 + 9×0.10                = 1.90
//      噩梦第 20 波 = 1 + 19×0.10 + 9×0.10      = 3.80
//    第 20 波 = 380%，非常接近用户说的 400%，而且 11 波后是 20%/波
//    （普通 10% 的两倍），符合"噩梦更贵"的直觉。
//
//    实测第 21 波（无限模式）= 400%，正好落在用户说的数字上。
export const PRICE_WAVE_STEP = 0.10;     // 每回合商品涨价 10%
/**
 * 206 轮：全游戏商品涨价系数。
 * 用户："道具价格还是太便宜了，算算11回合时最便宜的传说道具是多少钱，把它改到100，
 *       然后把这个涨幅试用全游戏。"
 *
 * 第 11 波（currentWave = 10）最便宜的传说武器 = 48（T4 基价）× 0.9（最小抖动）× 2.0（1 + 10×0.1）
 * = 86.4 → 86。要变成 100，系数就是 100 / 86。
 * 乘在 _weaponPrice / _itemPrice 的最后一步，所以所有档位、所有道具一起涨同样的比例。
 */
export const PRICE_GLOBAL_MUL = 100 / 86;
export const NIGHTMARE_EXTRA_STEP = 0.10; // 噩梦第 11 波起**额外**再涨 10%/波
export const NIGHTMARE_EXTRA_FROM = 10;   // w=10 即第 11 波
export const REROLL_STEP_WAVE = 10;       // w=10 即第 11 波
export const REROLL_STEP_AMOUNT = 5;      // 第 11 波起刷新永久 +5

/**
 * 给定**实际生效的幸运**，算出偷取概率（永远落在 [0.20, 0.50] 内）。
 *
 * ⚠️ 参数是 effLuck，不是面板幸运。
 *    历史原因：第十二轮这个函数收的是面板值，内部自己调 effectiveLuck()。
 *    第十六轮加了角色的 luckEffectMul（悠怡萌妹 1.2）之后，那个"内部换算"
 *    就变成了错的 —— 它只知道 ×0.8，不知道还要再 ×1.2。
 *
 *    所以改成**只收已经换算好的值**，调用方用 player.stats.luckEff 传进来。
 *    这样"面板 → 生效"的换算全仓只有一处（StatSheet.luckEff），
 *    不会再出现"某个概率忘了乘角色倍率"这种漏。
 *
 *    老调用形式 stealChance(面板幸运) 在数值上依然等价于
 *    stealChance(effectiveLuck(面板幸运))，所以已有测试不受影响。
 */
export function stealChance(effLuck) {
  const bonus = clamp01(effLuck / STEAL_LUCK_CAP);
  return STEAL_CHANCE_BASE + bonus * (STEAL_CHANCE_MAX - STEAL_CHANCE_BASE);
}

export class ShopOffer {
  constructor(kind, data, price, meta = {}) {
    this.kind = kind;          // 'weapon' | 'item'
    this.data = data;          // 定义对象
    this.price = price;
    this.sold = false;
    this.meta = meta;
  }
  get id() { return this.data.id; }
  get name() { return this.data.name; }
}

export class Shop {
  constructor(rng) {
    this.rng = rng;
    this.offers = [];
    this.locked = new Set();   // 被锁定的槽位索引
    this.rerollCount = 0;           // P1（段 0）本店的刷新次数 —— 刷新费只跟它走
    this.mateRerolls = new Map();   // 201 轮：队友（段 1..3）各自的刷新次数，不再和 P1 共用一个计数
    this.rerollBaseCost = 3;
    // 买空货架后赠送的免费刷新是否已用掉（见 _maybeAutoReroll）
    this.freeRerollUsed = false;
    // 当前难度 id（第七轮）。由 main.js 在开局/读档时写入，
    // 只用于 priceWaveMul 的噩梦分段涨价；默认 null = 走通用公式。
    this.difficultyId = null;
  }

  /**
   * 刷新费用：基础 + 随波数的永久涨价 + 本次商店内的刷新次数。
   *
   * 第四轮："刷新价格每回合默认 +1。20 回合增加到一次刷新 20 多钱才对。"
   *   → 基础 3 + 波数，第 20 波 = 3 + 19 = 22。
   *
   * 第十八轮（用户要求）："商店的刷新价格在 11 回合时永久 +5，
   *   让玩家的消费增加。"
   *   → 第 11 波起**永久**再加 5。因为波数只增不减，"永久"就等于
   *     "波数 ≥ 11 就一直带着这 5"。第 20 波 = 3 + 19 + 5 = 27。
   */
  get rerollCost() { return this.rerollCostFor(0); }

  /**
   * 201 轮：某位买家的刷新费。货架按段切分（P1 | P2 | P3 | P4，每段 4 格），刷新次数也按段分开记 ——
   * 之前 AI 队友的「囤钱刷新」调用共享的 reroll()，把 P1 的刷新费从 5 抬到 97（实测 47 次），
   * 还把 P1 的前 4 格整排换掉（见 reroll 的注释）。
   */
  rerollCostFor(slotIndex = 0) {
    const k = this._segOf(slotIndex);
    const n = k === 0 ? this.rerollCount : (this.mateRerolls.get(k) ?? 0);
    // 第四十七轮：rerollCostMul（悠怡萌妹 0.5 = 刷新费减半），向上取整
    // 保证材料是整数；其他角色 undefined → ×1 原价。
    const base = this.rerollBaseCost + this._waveRerollBonus + n * 2;
    return Math.ceil(base * (this.rerollCostMul ?? 1));
  }

  /** 槽位下标所属的段（0 = P1，1..3 = 队友） */
  _segOf(slotIndex) { return Math.max(0, Math.floor((slotIndex || 0) / SHOP_SLOTS)); }

  /**
   * 本回合的刷新涨价量。
   *
   * 两部分相加：
   *   ① 每回合 +1（第四轮）
   *   ② 第 11 波起永久 +5（第十八轮）
   *
   * 用 `w >= REROLL_STEP_WAVE` 而不是"累加一次就记住"，
   * 是因为这个 getter 是**纯函数**（只读 currentWave），
   * 不持有"已经加过了"的状态 —— 读档、重试、来回切界面都不会重复加。
   */
  get _waveRerollBonus() {
    const w = this.currentWave ?? 0;
    return Math.max(0, w) + (w >= REROLL_STEP_WAVE ? REROLL_STEP_AMOUNT : 0);
  }

  /**
   * 本回合的商品价格倍率。
   *
   * ---- 第四轮：每波 +5%，第 20 波 ≈ 200% ----
   * 用户原话："商店道具价格增加，每回合涨幅 5%，到 20 波涨幅到 200% 价格，
   *            不然中后期经济还是变成扫荡商店了。"
   *
   * ---- 第七轮：噩梦第 11 波起改为 +10% ----
   * 用户原话："噩梦难度道具价格从第十一回合开始每回合从增加5%价格变成10%。
   *            也就是20回合购买价格接近250%。"
   *
   * ---- 第十八轮：全难度每波 +10%，噩梦再叠一层（用户要求）----
   * 用户原话："给所有商店售卖的物品的价格每回合+10%……
   *            20 回合到 200% 售卖价格还是不够，玩家还是太富有了，
   *            很容易 6 传说。这个改动可以让噩梦难度商品售价在 20 回合到 400%。"
   *
   * 所以现在：
   *   其他难度：`1 + w × 0.10`            → 第 20 波 ×2.90
   *   噩梦：    `1 + w × 0.10 + (w-10)×0.10`（第 11 波起）
   *                                      → 第 20 波 ×3.80，第 21 波 ×4.00
   *
   * 噩梦分段处连续（w=9 时后面那项还不存在，w=10 时为 0），
   * 所以第 10→11 波不会出现价格跳变。其他难度**不受**噩梦那一项影响。
   */
  get priceWaveMul() {
    const w = this.currentWave ?? 0;
    let mul = 1 + w * PRICE_WAVE_STEP;
    if (this.difficultyId === 'nightmare' && w >= NIGHTMARE_EXTRA_FROM) {
      mul += (w - NIGHTMARE_EXTRA_FROM) * NIGHTMARE_EXTRA_STEP;
    }
    // 第四十八轮：无尽模式（21 波起）每回合商品价格**额外** ×1.1 复利。
    // 用户原话："每回合售卖物品价格额外乘以1.1倍，就是越来越贵的效果，
    // 让玩家无法在无尽模式刷太多道具。"
    // 第 21 波 ×1.1、第 30 波 ×2.36、第 40 波 ×6.12 —— 叠在原有涨幅上，
    // 无尽越往后期买同一样东西越贵，逼玩家见好就收。
    const wave1 = w + 1;
    if (wave1 > 20) mul *= Math.pow(1.1, wave1 - 20);
    return mul;
  }

  /**
   * 生成一批商品（打开商店时调用）
   *
   * 锁定的语义：在商店里锁定 → 关闭商店 → 打完下一波 → 下一家商店里还在。
   * 所以锁要在这里「消费」掉，而不是在关商店的时候清掉
   * （之前 _beginWave 里清锁，导致锁了等于没锁）。
   */
  roll(player, waveIndex) {
    // 保留锁定槽位
    const kept = [];
    for (let i = 0; i < this.offers.length; i++) {
      if (this.locked.has(i) && !this.offers[i].sold) kept[i] = this.offers[i];
    }

    this.offers = [];
    for (let i = 0; i < SHOP_SLOTS; i++) {
      if (kept[i]) {
        this.offers[i] = kept[i];
        continue;
      }
      this.offers[i] = this._makeOffer(player, waveIndex, i);
    }

    // 锁已经兑现，清掉；刷新次数也归零（这是新的一家商店）
    this.locked.clear();
    this.rerollCount = 0;
    this.mateRerolls.clear();
    // 买空赠送的免费刷新：每开一家新商店重新给一次机会
    this.freeRerollUsed = false;
    return this.offers;
  }

  /** 清空锁定（放弃本局/重开时用） */
  clearLocks() {
    this.locked.clear();
    this.rerollCount = 0;
    this.mateRerolls.clear();
    this.freeRerollUsed = false;
  }

  // ---------------------------------------------------------
  _makeOffer(player, waveIndex, slot) {
    const rng = this.rng;

    // 幸运越高，出现高级物品的概率越大。
    // 第十轮：统一走 effectiveLuck（总效果 80%），别直接读面板值。
    // 第十六轮：改用 stats.luckEff —— 它在 effectiveLuck 之上再乘
    // 角色的 luckEffectMul（悠怡萌妹 = 1.2）。
    // 商店品质是幸运最直观的消费点，不改这里萌妹的被动就看不出来。
    const luckBonus = clamp01((player.stats.luckEff ?? 0) / 220);

    // 决定是武器还是道具
    const wantWeapon = rng.next() < (player.hasWeaponSlot ? 0.55 : 0.45);

    if (wantWeapon) {
      return this._makeWeaponOffer(player, waveIndex, luckBonus);
    }
    return this._makeItemOffer(player, waveIndex, luckBonus);
  }

  /**
   * 第一百零七轮：栏满升级货架的品质分布（先算分布，再抽取）。
   *
   * 用户原话："6武器之后从玩家背包抽武器的时候：0幸运随机到武器的
   * 时候品质的概率为70%/25%/4%/1%（对应t1-4，t4是金武器）……到220
   * 幸运上限后变成5%/50%/30%/15%。（曲线过渡你自己做）然后如果玩家
   * 有对应武器，增加对应武器的抽到的概率，白蓝武器不吃这个增加，紫金
   * 吃这个增加，增加的概率等量扣除白蓝概率，让概率总和维持100%。
   * 紫武器+10%，金武器+5%……优先扣白色武器刷新率额度，扣到0开始扣
   * 蓝色。多把紫色金色多次加成。也就是玩家6金武的时候金武刷新率在
   * 220幸运下有45%（最大概率）。"
   *
   * 曲线：luckBonus（生效幸运/220，0..1）线性插值，系数和恰好守恒。
   * 持有加成：每把紫 +10%、每把金 +5% 叠加；等量额度先扣白、白穿
   * 再扣蓝；极端超额（6 紫 +60% > 白蓝额度）从紫金等比退回。
   * 返回原始分布（不含"没持有的品质"处理——那是抽取时的阶梯回退）。
   */
  _ownedUpgradeDist(ownedDefs, luckBonus) {
    const b = Math.max(0, Math.min(1, luckBonus));
    const p = [
      0.70 - 0.65 * b,   // t1 白
      0.25 + 0.25 * b,   // t2 蓝
      0.04 + 0.26 * b,   // t3 紫
      0.01 + 0.14 * b,   // t4 金
    ];
    const countByTier = [0, 0, 0, 0];
    for (const d of ownedDefs) countByTier[Math.min(3, Math.max(0, d.tier - 1))]++;
    const bonus = countByTier[2] * 0.10 + countByTier[3] * 0.05;
    if (bonus > 0) {
      p[2] += countByTier[2] * 0.10;
      p[3] += countByTier[3] * 0.05;
      let need = bonus;
      const take1 = Math.min(p[0], need); p[0] -= take1; need -= take1;   // 先扣白
      const take2 = Math.min(p[1], need); p[1] -= take2; need -= take2;   // 白穿再扣蓝
      if (need > 1e-9) {
        const total34 = p[2] + p[3];   // 白蓝扣穿：紫金等比退回超额
        if (total34 > 1e-9) {
          const scale = (total34 - need) / total34;
          p[2] *= scale; p[3] *= scale;
        }
      }
    }
    return { p, countByTier };
  }

  /**
   * 按分布抽品质 → 从该品质的持有武器里均匀挑一把。
   * 抽中没持有的品质时**向下找下位**（用户："运气不够6个t4也是刷这
   * 6个t4的下位武器让玩家去买了合"——买了走自动合成补位）；
   * 一路到底都没有就落最低持有档。
   */
  _pickOwnedUpgrade(ownedDefs, luckBonus) {
    const rng = this.rng;
    const { p, countByTier } = this._ownedUpgradeDist(ownedDefs, luckBonus);
    const total = p.reduce((a, c) => a + c, 0);
    if (total <= 1e-9) return rng.pick(ownedDefs);   // 理论兜底
    let r = rng.next() * total;
    let tierIdx = 0;
    for (let i = 0; i < 4; i++) {
      r -= p[i];
      if (r <= 0) { tierIdx = i; break; }
      if (i === 3) tierIdx = 3;
    }
    // 阶梯回退：没持有的品质向下找下位；到底都没有 → 最低持有档
    while (countByTier[tierIdx] === 0) {
      tierIdx--;
      if (tierIdx < 0) {
        tierIdx = countByTier.findIndex((c) => c > 0);
        break;
      }
    }
    const pool = ownedDefs.filter((d) => d.tier - 1 === tierIdx);
    return rng.pick(pool.length ? pool : ownedDefs);
  }

  _makeWeaponOffer(player, waveIndex, luckBonus) {    const rng = this.rng;

    // 槽位已满时：只提供玩家"已经拥有"的武器。
    // 这样每次购买都能升级那把武器，商店在后期依然有意义；
    // 否则会刷出既买不了、又没用的武器，白白浪费商品位。
    if (!player.hasWeaponSlot) {
      const ownedDefs = player.weapons.map((w) => w.def);
      if (ownedDefs.length > 0) {
        // 第一百零七轮：品质分布抽取（不再均匀 pick）—— 用户："6武器
        // 之后从玩家背包抽武器的时候：0幸运随机到武器的时候品质的概率为
        // 70%/25%/4%/1%（对应t1-4，t4是金武器）……到220幸运上限后变成
        // 5%/50%/30%/15%。如果玩家有对应武器，增加对应武器的抽到的概率，
        // 白蓝武器不吃这个增加……紫武器+10%，金武器+5%。"
        const def = this._pickOwnedUpgrade(ownedDefs, luckBonus);
        return new ShopOffer('weapon', def, this._weaponPrice(def, luckBonus), { upgradeOffer: true });
      }
    }

    // 可用层数：随波数开放
    const maxTier = waveIndex < 3 ? 1 : waveIndex < 7 ? 2 : 3;

    // 如果玩家有可合成的武器，优先给同款（帮助合成）
    const combinable = player.weapons
      .filter((w) => RECIPE_MAP[w.def.id])
      .map((w) => w.def);
    const tryCombo = combinable.length > 0 && rng.next() < 0.42;
    if (tryCombo && player.hasWeaponSlot) {
      const def = rng.pick(combinable);
      return new ShopOffer('weapon', def, this._weaponPrice(def, luckBonus), { combo: true });
    }

    // 普通抽取：按 tier 加权
    const weights = [];
    for (let t = 1; t <= maxTier; t++) {
      let w = t === 1 ? 10 : t === 2 ? 6 : 3;
      if (t >= 2) w *= 1 + luckBonus * 1.6;
      weights.push(w);
    }
    let total = 0;
    for (const w of weights) total += w;
    let r = rng.next() * total;
    let tier = 1;
    for (let t = 1; t <= maxTier; t++) {
      r -= weights[t - 1];
      if (r <= 0) { tier = t; break; }
    }

    const pool = weaponsByTier(tier);
    // 流派加权（用户要求 120%）：本命流派的武器更容易刷出来
    const def = this._pickWeighted(pool.length ? pool : WEAPONS, player, weaponArch);
    return new ShopOffer('weapon', def, this._weaponPrice(def, luckBonus));
  }

  /**
   * 按「本命流派」加权从候选池里抽一个。
   *
   * 用户第四轮："增加角色职业武器刷新率……刷新率和职业道具改到 120%。
   * 高于其他武器道具一些方便玩家玩某个流派。"
   *
   * 命中玩家本命流派的项权重 ×1.2，其余 ×1 —— 只是"略微偏向"，
   * 不会把非本命的东西挤掉（否则玩家就没法转型了）。
   */
  _pickWeighted(pool, player, archOf) {
    if (!pool || pool.length === 0) return null;
    if (!player) return this.rng.pick(pool);
    const playerArch = characterArch(player.characterId);
    if (playerArch.length === 0) return this.rng.pick(pool);

    const weights = pool.map((d) => archWeightMul(archOf(d), playerArch));
    let total = 0;
    for (const w of weights) total += w;
    let r = this.rng.next() * total;
    for (let i = 0; i < pool.length; i++) {
      r -= weights[i];
      if (r <= 0) return pool[i];
    }
    return pool[pool.length - 1];
  }

  _makeItemOffer(player, waveIndex, luckBonus) {
    const rng = this.rng;
    // 过滤已达上限的道具；第八十五轮：芙兰专属（莱万汀）不进随机池 ——
    // 用户："应该只有打败芙兰才能获得。"（芙兰结算直接塞货架，不走这里）
    // 第一百三十四轮：Boss 专属掉落红品（小鱼干等 8 件）同理排除。
    const pool = ITEMS.filter((it) => {
      if (it.flandreOnly || it.bossDropOnly) return false;
      const count = player.items.get(it.id) || 0;
      return count < (it.maxStacks ?? 99);
    });
    if (pool.length === 0) {
      // 兜底也不能把莱万汀/专属红品摇出来：从"非专属"里挑
      const def = rng.pick(ITEMS.filter((it) => !it.flandreOnly && !it.bossDropOnly));
      return new ShopOffer('item', def, this._itemPrice(def, luckBonus));
    }

    // 按价格层加权，幸运提升高价道具权重；
    // 再叠一层"本命流派"加权（用户要求 120%）
    const playerArch = characterArch(player.characterId);
    const weights = pool.map((it) => {
      const t = itemTier(it);
      let w = t === 1 ? 10 : t === 2 ? 6 : 3;
      if (t >= 2) w *= 1 + luckBonus * 1.4;
      return w * archWeightMul(itemArch(it), playerArch);
    });
    let total = 0;
    for (const w of weights) total += w;
    let r = rng.next() * total;
    let idx = pool.length - 1;
    for (let i = 0; i < pool.length; i++) {
      r -= weights[i];
      if (r <= 0) { idx = i; break; }
    }

    const def = pool[idx];
    return new ShopOffer('item', def, this._itemPrice(def, luckBonus));
  }

  _weaponPrice(def, luckBonus) {
    // 第十五轮 ②：传说（T4）基础售价 ×1.2（40 → 48），
    // 与 T3→T4 合成费一起涨价 —— 两者说的是同一件事（拿到传说的总代价）。
    // 数值定义在 weapons.js 里，这里只取常量，避免两处各写一个 48。
    const base = def.tier === 4 ? LEGENDARY_BASE_PRICE
      : def.tier === 3 ? 26 : def.tier === 2 ? 16 : 9;
    const jitter = 0.9 + this.rng.next() * 0.25;
    return Math.max(3, Math.round(base * jitter * this.priceWaveMul * (1 - luckBonus * 0.22) * PRICE_GLOBAL_MUL));
  }

  _itemPrice(def, luckBonus) {
    const jitter = 0.92 + this.rng.next() * 0.2;
    return Math.max(3, Math.round(def.price * jitter * this.priceWaveMul * (1 - luckBonus * 0.22) * PRICE_GLOBAL_MUL));
  }

  // ---------------------------------------------------------
  // 购买
  // ---------------------------------------------------------
  /**
   * @returns {{ok:boolean, reason?:string, offer?:ShopOffer, combo?:object}}
   */
  buy(player, slotIndex) {
    const offer = this.offers[slotIndex];
    if (!offer || offer.sold) return { ok: false, reason: '该商品已售出' };
    if (player.materials < offer.price) return { ok: false, reason: '材料不足' };

    if (offer.kind === 'weapon') {
      const owned = player.findWeapons(offer.data.id);

      // 武器栏满了：
      //   1) 如果买了这把就能合成 → 自动合成（用户明确要求的行为）
      //   2) 否则如果已有同名武器 → 升级它
      //   3) 否则买不了
      if (!player.hasWeaponSlot) {
        if (owned.length >= 1 && RECIPE_MAP[offer.data.id]) {
          const auto = this._tryAutoCombine(player, offer);
          if (auto) {
            player.materials -= offer.price;
            offer.sold = true;
            return this._finishBuy(player, { ok: true, offer, ...auto }, slotIndex);
          }
        }
        if (owned.length > 0) {
          player.materials -= offer.price;
          offer.sold = true;
          owned[0].level++;
          return this._finishBuy(player, { ok: true, offer, weapon: owned[0], upgraded: true }, slotIndex);
        }
        return { ok: false, reason: '武器栏已满（最多 6 把），先卖掉一把' };
      }
    }

    player.materials -= offer.price;
    offer.sold = true;

    if (offer.kind === 'weapon') {
      const inst = player.addWeapon(offer.data.id, 1);
      return this._finishBuy(player, { ok: true, offer, weapon: inst }, slotIndex);
    }
    // 体检修复（第九十二轮）：addItem 在达 maxStacks 时返回 false
    // （player.js:206）。此前忽略返回值 → 照样扣材料、标 sold、
    // 货架空格，玩家却拿不到道具（血之契约双份货架/锁定格跨店两种
    // 路径都能白扣钱）。现在退款回滚，与 tryStealItem 的处理对齐。
    if (!player.addItem(offer.data.id)) {
      player.materials += offer.price;
      offer.sold = false;
      return { ok: false, reason: '已达叠加上限' };
    }
    return this._finishBuy(player, { ok: true, offer }, slotIndex);
  }

  /**
   * 购买成功后的统一收尾。
   *
   * 用户要求："增加商店道具数量购买完了免费自动刷新一次。"
   * 放在这里而不是各个分支里，是为了保证**任何**购买路径
   * （普通买、升级、自动合成）都能触发自动刷新 —— 否则
   * 用自动合成买空货架时就不会刷新，行为不一致。
   */
  _finishBuy(player, result, slotIndex = 0) {
    const auto = this._maybeAutoReroll(player, slotIndex);
    if (auto) result.autoReroll = auto;
    return result;
  }

  /** 是否所有槽位都已售出 */
  get allSold() {
    if (this.offers.length === 0) return false;
    return this.offers.every((o) => !o || o.sold);
  }

  /**
   * 买家自己的半边区间（第一百七十七轮）。
   * duo 分池货架 = 8 格：P1 前 4 格、P2 后 4 格 —— 买空判定和免费补货
   * 都只看**买家自己**的半边。原来用 allSold 判整排 8 格，玩家买完
   * 自己 4 个后只要 P2（AI）没买完，商店就永远不刷新（用户实测卡住）。
   * 返回 [from, to)；单人货架（≤4 格）就是整排。
   */
  _buyerRange(slotIndex) {
    if (this.offers.length <= SHOP_SLOTS) return [0, Math.max(1, this.offers.length)];
    // 201 轮：原来只分"前半/后半"（half = slotIndex>=4 ? 1 : 0），3P/4P 时 P3、P4 的买家被算成 P2 的段 [4,8) ——
    // 他们买空自己那段时检查的是 P2 的货架，并且错把 P2 的货架整段重建。现在按段号（每段 4 格）。
    const maxSeg = Math.floor((this.offers.length - 1) / SHOP_SLOTS);
    const k = Math.min(this._segOf(slotIndex), maxSeg);
    return [k * SHOP_SLOTS, k * SHOP_SLOTS + SHOP_SLOTS];
  }

  /**
   * 小小猫娘偷一件**道具**（第十二轮第 7 项）。
   *
   * 由 `main._openShop()` 在开店时调一次（不是每帧 —— 否则一次商店
   * 会被偷几十次）。返回被偷的商品，或 null。
   *
   * 为什么在 Shop 里而不是 Pet 里：
   *   偷东西要动 `offers`（把商品标记成已售出），这是商店的职责。
   *   宠物只管"要不要偷"，通过回调拿结果，避免宠物直接持有商店引用
   *   （宠物在世界里，商店在 main 里，两者本来不该互相认识）。
   *
   * 边界情况：
   *   - 货架上没有未售出的**道具** → 返回 null（不改成偷武器，
   *     用户原话是"偷一个道具"）。
   *   - 道具已达叠加上限（addItem 返回 false）→ 返回 null，
   *     并且**不**把它标记成已售出 —— 偷不到就不该让它消失，
   *     否则玩家的货架会莫名其妙少一件。
   *   - 锁定的槽位也照样能偷。锁的语义是"保留到下一家商店"，
   *     和"能不能被偷"无关；而且允许偷锁定格反而更像真的小偷。
   *
   * @returns {{offer:ShopOffer, item:object}|null}
   */
  tryStealItem(player, rng = this.rng) {
    // 先掷骰子 —— 概率不过就什么都不发生（也不消耗货架）
    // 传 luckEff（含角色幸运倍率），不是面板值 —— 见 stealChance 的注释。
    const chance = stealChance(player?.stats?.luckEff ?? 0);
    if (rng.next() >= chance) return null;

    // 找出所有未售出的**道具**槽位
    const candidates = [];
    for (let i = 0; i < this.offers.length; i++) {
      const o = this.offers[i];
      if (o && !o.sold && o.kind === 'item') candidates.push(o);
    }
    if (candidates.length === 0) return null;

    // 随机挑一件，并**逐个尝试**：如果挑中的那件已经叠满，
    // 换下一件再试，而不是直接放弃本次机会。
    // （否则玩家道具全满时这个功能会静默失效，很难排查）
    const order = candidates.slice();
    for (let n = order.length; n > 1; n--) {
      const k = Math.floor(rng.next() * n);
      [order[n - 1], order[k]] = [order[k], order[n - 1]];
    }
    for (const offer of order) {
      if (!player.addItem(offer.data.id)) continue;
      // 偷到手 → 从货架上消失（免费，所以不扣材料）
      offer.sold = true;
      // 偷完如果货架空了，走和"买空"同一条自动补货路径，
      // 否则玩家会面对一个空商店且没有免费刷新按钮可点。
      this._maybeAutoReroll(player);
      return { offer, item: offer.data };
    }
    return null;
  }

  /**
   * 买空货架后免费自动刷新。
   *
   * 用户第四轮反馈："商店全购买完之后只免费刷了一次，之后在购买完
   * 还需要主动刷新，改成只要买完就自动刷新不限次数。"
   *
   * 所以现在**不限次数**：只要买空就补货，补完还能接着买。
   * 不再需要 freeRerollUsed 这种"每家店只送一次"的限制。
   *
   * 注意：这不等于无限白嫖 —— 每次补货都是全新的 4 件商品，
   * 玩家想继续买还是得付每件的钱。真正的约束是钱包，不是刷新次数。
   * 而且 21 波之后价格会随波数暴涨（见 priceWaveMul），
   * 买空货架的成本本身会越来越高。
   *
   * @returns {{offers:ShopOffer[]}|null} 刷新结果，没触发则 null
   */
  _maybeAutoReroll(player, slotIndex = 0) {
    // 第一百七十七轮：按买家自己的半边判"买空"（duo 分池下 P2 AI 没买完
    // 不该挡住 P1 的免费补货），补货也只补这一半 —— 另一半是队友的货架。
    const [from, to] = this._buyerRange(slotIndex);
    let any = false;
    for (let i = from; i < to; i++) {
      const o = this.offers[i];
      if (!o) continue;
      any = true;
      if (!o.sold) return null;   // 这半边还有货 → 不补
    }
    if (!any) return null;

    // 保留锁定（虽然买空了通常没有锁，但逻辑上保持一致）
    const kept = [];
    for (let i = from; i < to; i++) {
      if (this.locked.has(i) && !this.offers[i].sold) kept[i] = this.offers[i];
    }
    for (let i = from; i < to; i++) {
      if (kept[i]) { this.offers[i] = kept[i]; continue; }
      this.offers[i] = this._makeOffer(player, this.currentWave ?? 0, i);
    }
    // 体检修复（第九十二轮）：补货后锁定集合必须清空。旧代码保留旧下标，
    // 补出来的随机新商品会凭空带 🔒 并在下一家商店被 roll 保留
    // —— 每次买空白拿一次额外锁。与 roll() 的"锁兑现即清"语义对齐。
    for (let i = from; i < to; i++) this.locked.delete(i);
    return { offers: this.offers, free: true };
  }

  /**
   * 武器栏满时，买下这把武器后能否立刻合成？
   *
   * 场景：玩家有 6 把，其中 1 把是金枪鱼刀。商店又出现金枪鱼刀。
   * 买下它 → 凑齐 2 把 → 立刻合成。合成本身会释放一个槽位，
   * 所以「满了也能买」是成立的，不需要玩家先手动卖东西。
   *
   * 材料不够合成费时返回 null（那就退化成普通升级）。
   */
  _tryAutoCombine(player, offer) {
    const recipe = RECIPE_MAP[offer.data.id];
    if (!recipe) return null;
    // 买这把需要的钱 + 合成费
    if (player.materials < offer.price + recipe.cost) return null;

    const owned = player.findWeapons(offer.data.id);
    if (owned.length < 1) return null;

    // 先把这把「买进来」临时凑成两把：直接吃掉一把已有的 + 这次买的
    const consumed = owned[0];
    player.removeWeapon(consumed);

    const target = getWeapon(recipe.to);
    player.materials -= recipe.cost;

    const existing = player.findWeapons(recipe.to);
    if (existing.length > 0) {
      existing[0].level++;
      return { combined: true, target, upgradedExisting: true, level: existing[0].level };
    }
    const inst = player.addWeapon(recipe.to, 1);
    return { combined: true, target, weapon: inst };
  }

  /**
   * 武器合成：需要两把同名武器 + 材料
   */
  canCombine(player, weaponId) {
    const recipe = RECIPE_MAP[weaponId];
    if (!recipe) return null;
    const owned = player.findWeapons(weaponId);
    if (owned.length < 2) return null;
    if (player.materials < recipe.cost) return { recipe, needMaterials: recipe.cost - player.materials, owned: owned.length };
    const target = getWeapon(recipe.to);
    // 合成本身会释放一个武器槽，所以槽位不是问题
    return { recipe, target, owned: owned.length, ok: true };
  }

  combine(player, weaponId) {
    const info = this.canCombine(player, weaponId);
    if (!info || !info.ok) return { ok: false, reason: '无法合成' };
    const { recipe, target } = info;

    const owned = player.findWeapons(weaponId);
    // 移除两把
    player.removeWeapon(owned[0]);
    player.removeWeapon(owned[1]);
    // 扣材料
    player.materials -= recipe.cost;
    // 给新武器：如果已经拥有同款，升级它
    const existing = player.findWeapons(recipe.to);
    if (existing.length > 0) {
      existing[0].level++;
      return { ok: true, upgraded: true, target, level: existing[0].level };
    }
    const inst = player.addWeapon(recipe.to, 1);
    return { ok: true, upgraded: false, target, weapon: inst };
  }

  /** 升级已有的武器（商店里的武器如果已拥有则升级） */
  upgradeWeapon(player, weaponId) {
    const owned = player.findWeapons(weaponId);
    if (owned.length === 0) return null;
    owned[0].level++;
    return owned[0];
  }

  /**
   * 刷新货架。
   * 201 轮（用户："多人模式下，商店的锁定按钮疑似失效"）：根因 —— 货架是 P1|P2|P3|P4 四段共 16 格，但本函数永远重建
   * offers[0..4)（P1 的那一段），而 AI 队友的「囤钱刷新」调用的正是这个共享入口：队友一刷新，P1 的整排货架就被换掉，
   * 刷新次数也记到 P1 头上；P1 的界面又不会重绘，于是玩家点「锁定/购买」时，界面上那张卡对应的下标早已是另一件商品。
   * 真机复现：界面显示「热牛奶|幸运硬币…」而实际货架是「星星魔杖|瞄准镜片…」，刷新计数 47、P1 刷新费 97。
   * 修：刷新只重建**买家自己的那一段**（slotIndex 指明，默认 0 = P1），刷新次数按段分开记；锁定只对 P1 段有意义，原样保留。
   */
  reroll(player, slotIndex = 0) {
    const k = this._segOf(slotIndex);
    const cost = this.rerollCostFor(slotIndex);
    if (player.materials < cost) return { ok: false, reason: '材料不足' };
    player.materials -= cost;
    if (k === 0) this.rerollCount++;
    else this.mateRerolls.set(k, (this.mateRerolls.get(k) ?? 0) + 1);
    const from = k * SHOP_SLOTS, to = from + SHOP_SLOTS;
    // 保留锁定（锁只存在于 P1 段，但按下标通用处理）
    const kept = [];
    for (let i = from; i < to; i++) {
      if (this.locked.has(i) && this.offers[i] && !this.offers[i].sold) kept[i] = this.offers[i];
    }
    for (let i = from; i < to; i++) {
      if (kept[i]) { this.offers[i] = kept[i]; continue; }
      this.offers[i] = this._makeOffer(player, this.currentWave ?? 0, i - from);
    }
    return { ok: true, cost };
  }

  toggleLock(index) {
    if (this.locked.has(index)) this.locked.delete(index);
    else if (!this.offers[index]?.sold) this.locked.add(index);
    return this.locked.has(index);
  }

  /**
   * 卖出武器换材料。
   * 价格按 tier 与等级给，保证「卖了不亏太多」——玩家不该因为
   * 想换流派而受到惩罚性损失。
   */
  sellValue(weaponInst) {
    const def = weaponInst.def;
    const base = def.tier === 4 ? 30 : def.tier === 3 ? 20 : def.tier === 2 ? 12 : 6;
    return Math.max(2, Math.round(base + (weaponInst.level - 1) * 6));
  }

  sellWeapon(player, weaponInst) {
    const value = this.sellValue(weaponInst);
    player.removeWeapon(weaponInst);
    player.materials += value;
    return value;
  }
}

function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }