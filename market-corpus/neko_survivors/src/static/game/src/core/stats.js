// ============================================================
// 属性系统
// 设计目标：复刻土豆兄弟的"数值手感" —— 属性由
//   基础值 + 角色修正 + 升级加成 + 道具/武器加成 合成，
// 并且区分「加法」与「乘法（百分比）」两类修正。
//
// ---- 关于上限（用户第四轮反馈）----
// "我看你是不是设置了属性上限，玩家的部分数值到 400 就不涨了，
//  把上限取消让玩家可以更爽一些，除非这个数值过高可能会导致游戏崩溃。
//  当然暴击率吸血率这些上限就 100% 就行了。"
//
// 所以：
//   - 只保留「概率类」的上限（暴击率 / 吸血 = 100%），
//     否则 120% 暴击率这种数值没有意义。
//   - 攻速 / 移速 / 幸运 / 闪避 的 max 全部取消 —— 让玩家能堆到爽。
//   - turretMax 是唯一的例外：它直接决定场上的炮台实体数量，
//     不封顶会真的把帧率打崩（就是用户说的"可能会导致游戏崩溃"）。
// ============================================================

/** 属性定义表：key -> { name, short, kind, base, min, max, fmt, desc } */
export const STAT_DEFS = {
  maxHp:        { name: '最大生命',  short: 'HP',   kind: 'flat',    base: 20,  min: 1,    fmt: 'int',   desc: '你能承受的伤害总量' },
  hpRegen:      { name: '生命回复',  short: 'REG',  kind: 'flat',    base: 0,   min: 0,    max: 10,   fmt: 'dec1',  desc: '每秒回复的生命值（上限10，第九十六轮）' },
  // 吸血是概率，保留 100% 上限（用户明确要求）
  lifesteal:    { name: '吸血',      short: 'LS',   kind: 'percent', base: 0,   min: 0,    max: 100, fmt: 'pct', desc: '命中时回复生命的概率' },
  damage:       { name: '伤害',      short: 'DMG',  kind: 'percent', base: 0,   min: -90,  fmt: 'pct',   desc: '对所有武器的伤害加成' },
  meleeDamage:  { name: '近战伤害',  short: 'MEL',  kind: 'percent', base: 0,   min: -90,  fmt: 'pct',   desc: '仅对近战武器生效' },
  rangedDamage: { name: '远程伤害',  short: 'RNG',  kind: 'percent', base: 0,   min: -90,  fmt: 'pct',   desc: '仅对远程武器生效' },
  elemDamage:   { name: '元素伤害',  short: 'ELE',  kind: 'percent', base: 0,   min: -90,  fmt: 'pct',   desc: '仅对元素武器生效' },
  // 攻速上限已取消（原来封顶 400%）
  attackSpeed:  { name: '攻击速度',  short: 'AS',   kind: 'percent', base: 0,   min: -80,  fmt: 'pct', desc: '武器开火频率' },
  // 暴击率是概率，保留 100% 上限（用户明确要求）
  critChance:   { name: '暴击率',    short: 'CRIT', kind: 'percent', base: 0,   min: 0,    max: 100, fmt: 'pct', desc: '造成双倍伤害的概率' },
  critDamage:   { name: '暴击伤害',  short: 'CDMG', kind: 'percent', base: 50,  min: 0,    fmt: 'pct',   desc: '暴击时的额外伤害倍率' },
  armor:        { name: '护甲',      short: 'ARM',  kind: 'flat',    base: 0,   min: 0,    fmt: 'int',   desc: '按递减曲线减少受到的伤害' },
  // 闪避也是概率 —— 第四十一轮上限 100% → 80%
  //（用户："然后闪避的上限改为80%。目前是100%导致玩家无敌。"）
  // 不设上限会让玩家堆到 999% 变成无敌，属于用户说的
  // "数值过高可能会导致游戏崩溃"那一类。
  dodge:        { name: '闪避',      short: 'DGE',  kind: 'percent', base: 0,   min: 0,    max: 80,  fmt: 'pct', desc: '完全躲开伤害的概率' },
  // 移速上限已取消（原来封顶 200%）
  moveSpeed:    { name: '移动速度',  short: 'SPD',  kind: 'percent', base: 0,   min: -70,  fmt: 'pct', desc: '移动快慢' },
  // 幸运上限已取消（原来封顶 300%）
  luck:         { name: '幸运',      short: 'LCK',  kind: 'percent', base: 0,   min: -50,  fmt: 'pct', desc: '更好的掉落与商店品质' },
  harvesting:   { name: '收获',      short: 'HRV',  kind: 'flat',    base: 0,   min: 0,    fmt: 'int',   desc: '每波结束额外获得的材料' },
  // 第十轮（用户要求）：默认拾取范围**翻倍**（55 → 110）。
  //
  // 用户原话："地图中后期太容易飘一整图的钱和经验了，让所有难度的默认
  //            拾取范围加大一倍，让玩家可以自动吸收周围一定范围的道具，
  //            目前是买了很多加拾取范围的道具也吃不到很多。"
  //
  // 注意改的是 **base**（基础值）而不是某个难度的倍率 ——
  // 所以简单到噩梦四个难度一起受益，正是"所有难度"的意思。
  // 道具给的 flat 加成仍然在这个基础上继续叠加。
  pickupRange:  { name: '拾取范围',  short: 'PICK', kind: 'flat',    base: 110, min: 20,   fmt: 'int',   desc: '自动吸取材料的半径' },
  range:        { name: '射程',      short: 'RNG2', kind: 'percent', base: 0,   min: -60,  fmt: 'pct',   desc: '全射程加成：近战五折、360 度武器 150%、远程/炮台全额（第三十二轮起）' },
  // ---- 炮台流派 ----
  turretDamage: { name: '炮台伤害',  short: 'TDMG', kind: 'percent', base: 0,   min: -90,  fmt: 'pct',   desc: '仅对炮台生效' },
  // 炮台攻速上限已取消（原来封顶 400%）
  turretRate:   { name: '炮台攻速',  short: 'TRATE',kind: 'percent', base: 0,   min: -80,  fmt: 'pct', desc: '炮台的开火频率' },
  turretRange:  { name: '炮台射程',  short: 'TRNG', kind: 'percent', base: 0,   min: -60,  fmt: 'pct',   desc: '炮台的索敌距离' },
  // 注意：曾经有个 turretHp「炮台耐久」，但炮台是永久存在且免疫接触伤害的，
  // 这个属性从来没有被任何代码读过，道具上写着 +1 却毫无作用（误导玩家）。
  // 现已换成真正有用的「炮台上限」：直接增加能同时存在的炮台数量。
  //
  // turretMax 是**唯一保留上限**的属性：它决定场上炮台实体数量，
  // 不封顶会真的把帧率打崩（用户说的"可能会导致游戏崩溃"的例外）。
  turretMax:    { name: '炮台上限',  short: 'TMAX', kind: 'flat',    base: 0,   min: 0,    max: 8,   fmt: 'int',   desc: '额外可同时存在的炮台数量' },
};

export const STAT_KEYS = Object.keys(STAT_DEFS);

/**
 * 诅咒下限：扣最大生命的道具（诅咒项圈/血之契约/玻璃炮…）累计
 * 最多扣掉这个比例的上限。
 *
 * 为什么需要它：flat 类属性（maxHp）的 pct 修正是**乘算累加**的，
 * `(base+flat) * (1 + Σpct)`。若用 pct:{maxHp:-0.2} 买 5 个，
 * Σpct = -1.0 → 乘数 0 → 生命上限被夹到 1，玩家永久只有 1 点血。
 *
 * 改成每次购买立刻按乘算扣一次、并且整体受这个下限保护，
 * 100 血扣 5 次还剩 100×0.8⁵ ≈ 33，而不是 1。
 */
export const CURSE_FLOOR = 0.45;

// ---- 第十轮：幸运总效果削弱到 80% ----
//
// 用户原话："幸运效果太强力了，获取容易，将幸运的总效果调整为80%，
//           也就是削弱了一些。"
//
// 注意削的是**效果**不是**数值**：
// 面板上显示的幸运仍然是买到的那个数（玩家看到的 "+25% 幸运" 不变），
// 但它在所有结算处的作用都乘 0.8。
// 这样做的好处是：
//   1. 不用改 6 个不同文件里的道具/升级数据（改了还容易漏）
//   2. 玩家面板不会出现"我明明买了 +25 怎么只加了 20"的困惑
//   3. 想再调平衡只改这一个数
//
// 幸运在这个项目里有 6 个消费点，散落在 4 个文件里：
//   data/chest.js      rollChestReward  → 开箱出道具/稀有的概率
//   data/upgrades.js   rollRarity       → 升级选项的稀有度权重
//   systems/shop.js    _roll / _price   → 商店品质与折扣
//   systems/world.js   _dropLoot        → 材料/血包掉落概率
//   systems/world.js   宝箱怪稀有概率
//   systems/world.js   _rollRewardItem  → 奖励道具的价格分层权重
// 全部改成读 effectiveLuck()，不再直接用 stats.values.luck。
export const LUCK_EFFECT_MUL = 0.8;

/**
 * 把面板幸运换算成**实际生效**的幸运。
 *
 * 所有结算处都该用这个函数，而不是直接读 stats.values.luck ——
 * 否则改 LUCK_EFFECT_MUL 时必然漏掉几处（这正是散落 6 处的问题）。
 *
 * 注意它对负幸运也生效（-50 的面板值 → -40 的实际值），
 * 也就是"幸运的负面影响同样削弱 20%"，符合"总效果 80%"的语义。
 */
export function effectiveLuck(luck) {
  const v = Number(luck);
  if (!Number.isFinite(v)) return 0;
  return v * LUCK_EFFECT_MUL;
}

/**
 * 第十六轮：带**角色专属倍率**的幸运换算。
 *
 * 用户原话（悠怡萌妹）："幸运总效果+20%（即0.8*1.2可以到96%的幸运效果，
 *   接近我们的初始幸运能力）"
 *
 * 所以这是**乘在 0.8 上**的，不是乘在幸运数值上：
 *   普通角色   effectiveLuck(x)     = x * 0.8
 *   悠怡萌妹   effectiveLuckFor(x, 1.2) = x * 0.8 * 1.2 = x * 0.96
 *
 * 为什么单独开一个函数而不是让调用方自己乘：
 *   幸运有 6 个消费点散在 4 个文件里（见上面 LUCK_EFFECT_MUL 的注释）。
 *   如果每个消费点都写成 `effectiveLuck(luck) * charLuckMul`，
 *   迟早有一处漏掉 —— 那正是第十轮改 0.8 时踩过的坑。
 *   统一从 StatSheet 上读（sheet.luckEffectMul），消费点只改一行。
 *
 * @param {number} luck
 * @param {number} [mul] 角色倍率；undefined / 1 时退化成 effectiveLuck()
 */
export function effectiveLuckFor(luck, mul) {
  const base = effectiveLuck(luck);
  const m = Number(mul);
  if (!Number.isFinite(m) || m === 1) return base;
  return base * m;
}

/** 全部属性归零的修正对象 */
export function emptyMods() {
  const m = {};
  for (const k of STAT_KEYS) m[k] = 0;
  return m;
}

/** 把一组修正累加到目标对象上 */
export function addMods(target, mods) {
  if (!mods) return target;
  for (const k in mods) {
    if (k in target) target[k] += mods[k];
  }
  return target;
}

/**
 * 计算最终属性值。
 *
 * 关键规则（区分两类属性，这是数值正确性的核心）：
 *
 *  - kind === 'percent' 的属性（伤害/攻速/暴击率…），它的「值本身就是一个百分数」。
 *    因此 pct 修正按**百分点**直接相加：
 *        final = base + flat + pct
 *    例：伤害基础 0，道具给 pct:{damage:8} → 伤害 = 8（即 +8%）
 *
 *  - kind === 'flat' 的属性（最大生命/护甲…），pct 修正是**乘数**：
 *        final = (base + flat) * (1 + pct)
 *    例：最大生命 10，道具给 pct:{maxHp:-0.15} → 10 * 0.85 = 8.5（即 -15%）
 *
 *    这个乘数有下限 CURSE_FLOOR（见下面的实现注释）：Σpct 叠到 -1 以下时
 *    只按 -55% 扣，否则加血道具会被负乘数反向放大 —— 越买血越少。
 *
 * 这套规则让数据表可以自然地写成「伤害 +8%」「最大生命 -15%」两种直觉形式。
 *
 * @param {object} base      基础值（通常来自 STAT_DEFS.base）
 * @param {object} flatMods  加法修正总和
 * @param {object} pctMods   百分比修正总和
 */
export function computeStats(base, flatMods, pctMods, capBonus = {}) {
  const out = {};
  for (const k of STAT_KEYS) {
    const def = STAT_DEFS[k];
    const b = Number.isFinite(base[k]) ? base[k] : def.base;
    // NaN / undefined / Infinity 一律当 0：脏数据只该"不生效"，
    // 不该顺着乘法把整条属性链污染成 NaN（NaN 连 min/max 都夹不住）。
    const f = Number.isFinite(flatMods[k]) ? flatMods[k] : 0;
    const p = Number.isFinite(pctMods[k]) ? pctMods[k] : 0;

    let v;
    if (def.kind === 'percent') {
      // 值本身就是百分数：pct 按百分点相加
      v = b + f + p;
    } else {
      // 值是一个数量：pct 作为乘数。
      //
      // ---- 乘数下限（第十一轮，用户"血量上限变 4"的根因）----
      // pct 是**线性累加**的：final = (base+flat) × (1 + Σpct)。
      // 而 Σpct 可以被打穿 1：「破釜沉舟」基础 -10%，稀有度还会放大
      // 负面（传说 downsideMult 当时是 2.5 → 单次 -25%），5 次全传说 = -125%。
      // 于是乘数 = -0.25：玩家血上限基础池 632 算出来是 -158，
      // 再被 maxHp.min=1 夹成 1 —— 加血道具越多反而越接近 1 点血。
      // 用户看到的 4 就是这条曲线的中间点：(20+60)×(1-0.95)=4。
      //
      // 第十二轮：稀有度倍率被削（传说 4→3、downsideMult 2.5→2），
      // 5 次全传说的惩罚从 -125% 降到 -100%，**仍然打穿 1**，
      // 所以这道下限照旧必须留着 —— 削弱倍率不等于这道保护可以删。
      //
      // 所以乘数必须夹到 [CURSE_FLOOR, +∞)：负 pct 最多扣掉 55%，
      // 和 curse 字段（也是 CURSE_FLOOR 兜底）语义统一。
      // 这样"加血道具永远是正收益"这条玩家直觉才成立，
      // 而且护甲/拾取范围等所有 flat 属性一起受保护。
      let mul = 1 + p;
      if (mul < CURSE_FLOOR) mul = CURSE_FLOOR;
      v = (b + f) * mul;
    }

    // 第一百三十四轮：上限夹紧感知 capBonus（黑色披风：闪避上限 90）。
    // 必须在**这里**放宽 —— 等 computeStats 返回后再放宽已经来不及，
    // 值早被 def.max=80 截断了。
    const cap = def.max !== undefined ? def.max + (capBonus[k] ?? 0) : undefined;
    if (def.min !== undefined) v = Math.max(def.min, v);
    if (cap !== undefined) v = Math.min(cap, v);
    out[k] = v;
  }
  // 护甲上限：避免无敌
  out.armor = Math.min(out.armor, 120);
  return out;
}

/**
 * 护甲减伤曲线（土豆兄弟同款思路）。
 * 第十九轮：阈值翻倍 —— 原来 15 甲 = 减伤 50%，用户反馈"护甲太容易获得，
 * 连魔法师都能轻松 30~40 甲顶着 Boss 打"，现在 **30 甲 = 减伤 50%**，
 * 同样的甲换到的减伤少一截。
 * 上限不变：最多减伤 80%。护甲面板 cap 120（上面两处 Math.min）现在
 * 恰好 = 30×4 = 曲线饱和点：1/(1+120/30) = 0.2 正好触到下限，一点不浪费。
 */
export function armorMultiplier(armor) {
  const m = 1 / (1 + Math.max(0, armor) / 30);
  return Math.max(0.2, m); // 最多减伤 80%
}

/** 格式化显示 */
export function fmtStat(key, value) {
  const def = STAT_DEFS[key];
  if (!def) return String(value);
  switch (def.fmt) {
    case 'pct':  return `${value >= 0 ? '' : ''}${Math.round(value)}%`;
    case 'int':  return String(Math.round(value));
    case 'dec1': return (Math.round(value * 10) / 10).toFixed(1);
    default:     return String(value);
  }
}

/**
 * 把一条修正格式化成 "+15% 伤害" / "-15% 最大生命" 这样的文本。
 * 注意：同一个数值在 percent 属性上是「百分点」，在 flat 属性上是「乘数比例」。
 */
export function fmtMod(key, value) {
  const def = STAT_DEFS[key];
  if (!def) return `${key} ${value}`;
  if (value === 0) return '';
  const sign = value > 0 ? '+' : '';

  // percent 属性的值就是百分数
  if (def.kind === 'percent') {
    return `${sign}${Math.round(value)}% ${def.name}`;
  }
  // flat 属性：小数视为乘数比例（-0.15 -> -15%），整数视为数量
  if (Math.abs(value) < 1 && value !== 0) {
    return `${sign}${Math.round(value * 100)}% ${def.name}`;
  }
  const shown = def.fmt === 'dec1' ? (Math.round(value * 10) / 10).toFixed(1) : Math.round(value);
  return `${sign}${shown} ${def.name}`;
}

/** 把 mods 对象转成可读文本数组 */
export function fmtMods(mods) {
  const out = [];
  for (const k in mods) {
    if (mods[k]) out.push(fmtMod(k, mods[k]));
  }
  return out;
}

/**
 * 运行时属性容器：缓存最终值，脏标记重算。
 */
export class StatSheet {
  constructor(baseValues) {
    this.base = { ...baseValues };
    this.flat = emptyMods();
    this.pct = emptyMods();
    // 诅咒：key -> 累计乘数（每次购买乘以 (1-frac)，真正的乘算）
    this.curse = emptyMods();
    for (const k of STAT_KEYS) this.curse[k] = 1;
    // 第一百三十四轮：属性上限加成（黑色披风：闪避上限 80 → 90）。
    // key -> 附加量；values 夹紧时用 def.max + capBonus[key] 当上限。
    // 只放宽数值上限，不改变 min（负闪避照旧夹住）。
    this.capBonus = {};

    // ---- 第十六轮：最终乘算（角色专属能力）----
    //
    // 用户原话里反复出现"（最终乘算）"，例如
    //   "生命成长和护甲成长+20%（最终乘算），伤害成长和攻速成长还有
    //     移速成长-20%（最终乘算）"
    //
    // 这和已有的 `pct` **不是一回事**，两者不能混用：
    //   pct      → 加进求和里，和道具/升级的加成**线性相加**。
    //              买 10 个 +10% 伤害是 +100%，不是 1.1¹⁰。
    //   finalMul → 在所有加法都算完之后，再乘一次。
    //              它必须**最后**生效，否则"最终乘算"就名不副实：
    //              先乘再被道具线性叠加，玩家的 +20% 会被稀释成 +8%。
    //
    // 默认是空对象而不是全 1 的表：绝大多数角色没有这一项，
    // 空对象让 `for (const k in ...)` 直接跳过，热路径零成本。
    // （用 emptyMods() 填一堆 1 的话，每帧都要遍历 30 个键做 30 次无用乘法。）
    this.finalMul = {};

    // 幸运总效果的角色专属倍率（第十六轮，悠怡萌妹专用）。
    // 见 effectiveLuckFor()。undefined = 用全局 LUCK_EFFECT_MUL。
    this.luckEffectMul = undefined;

    this.cache = null;
    this.dirty = true;
  }

  /** 设置基础值（角色定义用） */
  setBase(key, value) {
    this.base[key] = value;
    this.dirty = true;
  }

  /** 累加一条加法修正 */
  addFlat(key, value) {
    if (!(key in this.flat)) return;
    this.flat[key] += value;
    this.dirty = true;
  }

  /** 累加一条百分比修正（0.1 = +10%） */
  addPct(key, value) {
    if (!(key in this.pct)) return;
    this.pct[key] += value;
    this.dirty = true;
  }

  /**
   * 施加一次「诅咒」：按比例扣减某个 flat 属性的最终值。
   *
   * 与 addPct 的区别（这是修掉"生命上限变 1"的关键）：
   *   addPct('maxHp', -0.2) 五次 → 乘数 (1-1.0)=0 → 上限归 1
   *   addCurse('maxHp', 0.2) 五次 → 乘数 0.8⁵ ≈ 0.328 → 100 血还剩 33
   *
   * @param {number} frac 0.2 表示扣掉 20%
   */
  addCurse(key, frac) {
    if (!(key in this.curse)) return;
    this.curse[key] *= Math.max(0.01, 1 - frac);
    // 兜底：不管叠多少层，至少保留 CURSE_FLOOR 的比例
    if (this.curse[key] < CURSE_FLOOR) this.curse[key] = CURSE_FLOOR;
    this.dirty = true;
  }

  /** 批量应用 { flat: {...}, pct: {...} } 或直接的 mods 对象 */
  apply(mods, asPercent = false) {
    if (!mods) return;
    for (const k in mods) {
      if (asPercent) this.addPct(k, mods[k]);
      else this.addFlat(k, mods[k]);
    }
  }

  /**
   * 累加一条「最终乘算」修正（第十六轮，角色专属能力用）。
   *
   * 语义：在所有 flat / pct / curse 都算完之后，再乘一次。
   * 同一个属性被调用多次时**连乘**（1.2 × 1.2 = 1.44），
   * 因为"最终乘算"是乘算语义，相加会让第二次加成被前一次稀释。
   *
   * ⚠️ 只对 `kind: 'flat'` 的属性有意义。
   *    percent 类属性（伤害/攻速/移速）的 base 是 0，
   *    `0 × 1.2` 还是 0 —— 那就是一条静默失效的加成。
   *    这类"伤害/攻速最终乘算"必须落到**消费点**上
   *    （见 systems/char-skills.js），不能写在这里。
   *    这里加一道显式拒绝，避免以后有人踩同一个坑。
   *
   * @param {string} key
   * @param {number} mul 1.2 = +20%
   */
  addFinalMul(key, mul) {
    if (!(key in this.flat)) return;
    const def = STAT_DEFS[key];
    if (def && def.kind === 'percent') {
      // 不抛异常：数据层写错不该让整局游戏崩掉。
      // 但也不静默吞掉 —— 返回 false 让测试能断言"这条没生效"。
      if (typeof console !== 'undefined' && !this._warnedFinalMul) {
        this._warnedFinalMul = new Set();
      }
      if (this._warnedFinalMul && !this._warnedFinalMul.has(key)) {
        this._warnedFinalMul.add(key);
        console.warn(`[stats] addFinalMul 对 percent 属性 "${key}" 无效（base=0，乘算恒为 0）。` +
          '请把这条加成放到伤害结算处。');
      }
      return false;
    }
    this.finalMul[key] = (this.finalMul[key] ?? 1) * mul;
    this.dirty = true;
    return true;
  }

  /** 重置全部修正（重算装备时用） */
  resetMods() {
    this.flat = emptyMods();
    this.pct = emptyMods();
    for (const k of STAT_KEYS) this.curse[k] = 1;
    this.capBonus = {};
    this.dirty = true;
  }

  /** 第一百三十四轮：放宽某属性的上限（黑色披风：闪避 +10 上限） */
  addCapBonus(key, extra) {
    if (!(key in this.capBonus)) this.capBonus[key] = 0;
    this.capBonus[key] += extra;
    this.dirty = true;
  }

  get values() {
    if (this.dirty || !this.cache) {
      const out = computeStats(this.base, this.flat, this.pct, this.capBonus ?? {});
      // 诅咒按乘算作用在最终值上（夹紧同样感知 capBonus）
      for (const k of STAT_KEYS) {
        const c = this.curse[k];
        if (c !== 1) {
          out[k] *= c;
          const def = STAT_DEFS[k];
          const cap = def.max !== undefined ? def.max + ((this.capBonus ?? {})[k] ?? 0) : undefined;
          if (def.min !== undefined) out[k] = Math.max(def.min, out[k]);
          if (cap !== undefined) out[k] = Math.min(cap, out[k]);
        }
      }
      // ---- 第十六轮：最终乘算（必须放在最后一步）----
      //
      // 顺序很重要：诅咒 → 最终乘算。
      // 反过来的话，"+20% 生命成长"会先被诅咒削一刀，
      // 而用户说的是"最终乘算"，意思是它作用在**结算后的结果**上。
      // 诅咒是"扣基础池"的语义（它本来就是乘在最终值上的），
      // 所以两者都放最后、按 诅咒→finalMul 走，加成不吃诅咒的二次削减。
      for (const k in this.finalMul) {
        const m = this.finalMul[k];
        if (m === 1) continue;
        out[k] *= m;
        const def = STAT_DEFS[k];
        if (def) {
          if (def.min !== undefined) out[k] = Math.max(def.min, out[k]);
          if (def.max !== undefined) out[k] = Math.min(def.max, out[k]);
        }
      }
      // 护甲上限兜底：finalMul 也可能把护甲推过 120
      out.armor = Math.min(out.armor, 120);
      this.cache = out;
      this.dirty = false;
    }
    return this.cache;
  }

  get(key) { return this.values[key]; }

  /** 方便地拿常用派生值 */
  get damageMultiplier() { return 1 + this.values.damage / 100; }
  get attackSpeedMultiplier() { return 1 + this.values.attackSpeed / 100; }
  get moveSpeedMultiplier() { return 1 + this.values.moveSpeed / 100; }
  get armorMul() { return armorMultiplier(this.values.armor); }

  /**
   * 本角色实际生效的幸运（第十六轮）。
   *
   * 消费点应该用**这个**而不是 effectiveLuck(stats.values.luck)：
   * 角色专属倍率（悠怡萌妹 +20%）只有从这里读才拿得到。
   * 老代码全部走 effectiveLuck()，所以两者在普通角色上完全等价，
   * 不会改变任何既有平衡。
   */
  get luckEff() { return effectiveLuckFor(this.values.luck, this.luckEffectMul); }
}

/** 默认基础值 */
export function defaultBaseStats() {
  const b = {};
  for (const k of STAT_KEYS) b[k] = STAT_DEFS[k].base;
  return b;
}

// ============================================================
// 属性修正的存档序列化
// ============================================================
//
// 为什么这两个函数放在 stats.js、而不是留在 main.js 的 _serializeRun 里：
//
//   第十二轮的真 bug —— `stats.curse` 从来没被存过。
//   main.js 里手写的保存字段是 `{ base, flat, pct }`，加 curse 的时候
//   漏了一份，于是每次读档 / 「重试本波」所有诅咒惩罚静默消失：
//   玩家用诅咒项圈换来的伤害和攻速留下了，代价（血量上限）却没了，
//   读档就能把诅咒洗白，诅咒机制形同虚设。
//
//   根因是"谁的数据谁序列化"这条被绕开了：StatSheet 有 4 个字段，
//   保存代码在另一个文件里手抄字段名，加字段时必然漏。
//   所以把"StatSheet 怎么进存档"收拢到 StatSheet 自己所在的模块，
//   以后再加属性字段（比如护盾、韧性）只改这里一处。
//
//   顺带的好处：这两个函数是纯函数，不需要 DOM，
//   test/curse-save.mjs 能直接拿真实存档路径做断言，
//   而不是在测试里重抄一遍字段名（重抄就又是一次"两边各写一份"）。

/**
 * 把 StatSheet 里"成长出来的修正"打包成可 JSON 化的存档片段。
 *
 * 注意这里**不含**运行时缓存（cache/dirty）——
 * 那是纯派生数据，存进去只会让存档变大，读档时算一遍就有了。
 */
export function serializeStatMods(sheet) {
  return {
    base: { ...sheet.base },
    flat: { ...sheet.flat },
    pct: { ...sheet.pct },
    curse: { ...sheet.curse },
    // 第十六轮：角色专属的最终乘算。
    // **必须存**，否则读档/重试本波之后"最终乘算"这类能力静默消失 ——
    // 和第十二轮 curse 没存是同一个 bug 形状（玩家白拿好处或白吃亏）。
    finalMul: { ...(sheet.finalMul || {}) },
    // 幸运总效果的角色倍率（undefined 也要存，它和"1"不是一回事：
    // 老存档缺这个字段时读出来是 undefined，正好等价于"没改过"。）
    luckEffectMul: sheet.luckEffectMul,
  };
}

/**
 * 把存档里的修正装回 StatSheet（读档与「重试本波」共用同一条路径）。
 *
 * 两个必须守住的兼容性/健壮性点：
 *
 *  1. **老存档没有 curse 字段**。curse 是后加的，旧存档里根本没这个 key。
 *     这里必须把它当成"没有诅咒"（乘数全 1，也就是 StatSheet 构造时的默认值），
 *     而不是崩掉、更不是补 0 —— 补 0 会把所有属性直接乘成 0。
 *     所以循环里 `continue` 掉缺失/非法的 key，保留默认的 1。
 *
 *  2. **存档是玩家本机能改的文本**，里面可能是 NaN、0、负数、字符串。
 *     curse 是直接参与乘法的一环（`out[k] *= c`），一个 NaN 就能把
 *     血量上限污染成 NaN —— 而 NaN 连 min/max 都夹不住，
 *     这正是 stat-maxhp 那一轮踩过的坑。所以只接受
 *     `Number.isFinite(v) && v > 0` 的值，并且夹到 [CURSE_FLOOR, 1]：
 *       - 下限和 addCurse 用同一个 CURSE_FLOOR，保证"读档前后不变"；
 *       - 上限 1 是因为 curse 按定义就是"惩罚乘数"（addCurse 只会乘
 *         `1 - frac` 且 frac > 0）。不夹上限的话，手改存档写个
 *         `curse.dodge = 99` 就能白拿 99 倍属性 —— 读档路径不该成为
 *         比正常游戏更强的入口。
 *
 * 注意：**base 有意不还原**。它由"属性表默认值 + 角色定义"决定，
 * 每次 new Player() 都是同一份，存档里的 base 只是留档；
 * 老代码也没还原过它。这里保持原样，免得把"角色基础值被存档覆盖"
 * 这个新语义偷偷引进来。
 */
export function restoreStatMods(sheet, data) {
  if (!data || typeof data !== 'object') return sheet;

  if (data.flat && typeof data.flat === 'object') {
    sheet.flat = { ...sheet.flat, ...data.flat };
  }
  if (data.pct && typeof data.pct === 'object') {
    sheet.pct = { ...sheet.pct, ...data.pct };
  }

  // 老存档：data.curse 是 undefined → 整个分支跳过 → curse 保持全 1。
  const c = data.curse;
  if (c && typeof c === 'object') {
    for (const k of STAT_KEYS) {
      const v = Number(c[k]);
      if (!Number.isFinite(v) || v <= 0) continue;
      sheet.curse[k] = Math.min(1, Math.max(CURSE_FLOOR, v));
    }
  }

  // ---- 第十六轮：最终乘算 ----
  //
  // 同一套防御性校验，理由完全一样（这是读档路径，存档是玩家本机能改的文本）：
  //   - 只接受有限正数。`finalMul` 直接参与 `out[k] *= m`，
  //     一个 NaN 就能把血量上限污染成 NaN，而 NaN 连 min/max 都夹不住。
  //   - 上限夹到 10：手改存档写 `finalMul.maxHp = 1e9` 就能白拿十亿倍血。
  //     正常玩法里最高的最终乘算是个位数（角色能力都在 1.2 上下），
  //     10 倍已经宽到不可能误伤正常存档。
  //   - 下限 0.1：同理，"最终乘算"是角色能力，不该是秒杀自己的开关。
  const fm = data.finalMul;
  if (fm && typeof fm === 'object') {
    for (const k in fm) {
      if (!(k in sheet.flat)) continue;
      const v = Number(fm[k]);
      if (!Number.isFinite(v) || v <= 0) continue;
      sheet.finalMul[k] = Math.min(10, Math.max(0.1, v));
    }
  }

  // 幸运总效果倍率。同样夹范围：0.8*1.2=0.96 是唯一的正常值，
  // 允许 0.1~10 是为了以后可能加别的角色，而不是为了放开手改存档。
  const lem = Number(data.luckEffectMul);
  if (Number.isFinite(lem) && lem > 0) {
    sheet.luckEffectMul = Math.min(10, Math.max(0.1, lem));
  }

  sheet.dirty = true;
  return sheet;
}