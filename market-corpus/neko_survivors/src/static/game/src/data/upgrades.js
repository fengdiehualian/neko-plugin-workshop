// ============================================================
// 升级系统
// 每级获得 1 个升级选项（3 选 1），部分等级额外给属性点。
// 每个升级有 weight（出现权重）和 maxLevel（可重复次数）。
//
// ---- 稀有度（用户第四轮反馈）----
// "增加升级的选择稀有度，和运气挂钩，然后 5,10,15 级必定出一个
//  蓝色品质/紫色品质/橙色品质的天赋。（对应 4 个稀有度，
//  然后就是效果分别翻 2,3,4 倍）。"
//
// 所以每次抽升级时，先抽**稀有度**（受幸运影响），再把这条升级的
// 效果乘以稀有度倍率：普通 ×1 / 稀有 ×2 / 史诗 ×3 / 传说 ×4。

// 第十轮：幸运总效果削弱到 80%。所有幸运消费点统一走 effectiveLuck()，
// 不要直接读 stats.values.luck（散落在 4 个文件 6 处，直接读必然漏改）。
//
// 第十六轮：角色还能再带一个 luckEffectMul（悠怡萌妹 +20%），
// 所以改用 effectiveLuckFor(luck, mul)。它是 effectiveLuck 的超集 ——
// mul 传 1（或不传）时两者逐位相同，所以老的调用点行为不变。
import { effectiveLuckFor } from '../core/stats.js';
// ============================================================

/**
 * 4 档稀有度。
 *
 * mult 是效果倍率；downsideMult 是**负面效果**的倍率（故意比正面低）——
 * 否则"传说级 破釜沉舟"会变成 最大生命 -40%，玩家会觉得被坑。
 *
 * ---------------------------------------------------------------
 * 第十二轮（用户要求）：效果倍率从 ×1/×2/×3/×4 **削弱**到 ×1/×1.5/×2/×3。
 *
 * 用户原话："削弱一下升级成长，从1/2/3/4倍提升，改到1/1.5/2/3的提升。
 *            也就是稀有是普通的1.5倍，史诗是2倍，传奇是3倍。削弱一些玩家成长。
 *            考虑到你思考中提到最肉的敌人噩梦难度19波也只有3000血。"
 *
 * 动机：玩家成长曲线比敌人血量曲线陡太多 —— 噩梦第 19 波最肉的小怪
 * （巨型蟹将）也才不到 3000 血，而玩家一发能打 5000+，等于后期完全无压力。
 * 砍掉升级的高档收益，比继续无脑堆敌人血量更对，因为：
 *   堆敌人血量会让**发育不好**的玩家更难受（雪上加霜），
 *   砍升级收益则是让**发育好**的玩家收敛 —— 只削上限，不削下限。
 *
 * ⚠️ downsideMult 必须跟着一起降，否则会破坏"负面比正面增长慢"这条设计规则：
 *    改之前 legendary 是 正面×4 / 负面×2.5（负面 = 正面的 62.5%）；
 *    如果只降正面到 ×3、负面还留 ×2.5，比例就变成 83%，
 *    传说升级会从"高风险高回报"变成"陷阱"，这不是用户要的。
 *
 *    于是这里显式维持**原表就一直在遵守的那条公式**：
 *        downsideMult = 1 + (mult - 1) * 0.5
 *    验证（旧表）：mult 1/2/3/4  →  1 / 1.5 / 2 / 2.5   ✅ 与旧表逐项吻合
 *    第十二轮（新表）：mult 1/1.5/2/3 →  1 / 1.25 / 1.5 / 2
 *    这样"负面只按正面增量的一半增长"这条注释描述的行为完全没有变，
 *    变的只是正面被削了、负面跟着等比例收敛。
 *    （test/round4-check.mjs 断言的就是这条公式，不是写死的数字。）
 *
 * ---------------------------------------------------------------
 * 第十八轮（用户要求）：**再削一次**，从 ×1/×1.5/×2/×3
 * 改到 ×1/×1.33/×1.66/×2。
 *
 * 用户原话："给升级效果从 1/1.5/2/3 变成 1/1.33/1.66/2。削弱等级成长效果。"
 *
 * 动机和第十二轮一样（玩家成长比敌人血量陡），但这次还叠加了
 * 另外两条经济压制（商品每波 +10%、刷新第 11 波起 +5）。
 * 三条一起作用，目标是让"6 传说"这种成型速度明显变慢。
 *
 * 数值上是把 4 档**等距**排开：1 → 1.33 → 1.66 → 2，
 * 相邻档差恒为 0.33（旧表是 0.5 / 0.5 / 1，最后一档特别陡）。
 * 传说从 ×3 砍到 ×2，是本次削得最狠的一档 —— 这正是用户抱怨的
 * "很容易 6 传说"的来源。
 *
 * downsideMult 依然严格走同一条公式：
 *    mult 1/1.33/1.66/2 → 1 / 1.165 / 1.33 / 1.5
 * 所以"负面只按正面增量的一半增长"这条设计规则继续成立，
 * test/round4-check.mjs 断言的公式不需要改（它算的是公式，不是写死的数）。
 * ---------------------------------------------------------------
 */
export const UPGRADE_RARITIES = [
  { id: 'common',    name: '普通', color: '#9aa3bb', mult: 1,    downsideMult: 1,    weight: 70 },
  { id: 'rare',      name: '稀有', color: '#5fd3e8', mult: 1.33, downsideMult: 1.165, weight: 22 },
  { id: 'epic',      name: '史诗', color: '#b794f6', mult: 1.66, downsideMult: 1.33,  weight: 6  },
  { id: 'legendary', name: '传说', color: '#ffa726', mult: 2,    downsideMult: 1.5,   weight: 2  },
];

export const RARITY_MAP = Object.fromEntries(UPGRADE_RARITIES.map((r) => [r.id, r]));
export const getRarity = (id) => RARITY_MAP[id] || UPGRADE_RARITIES[0];

/** 稀有度由低到高的索引，用来做"至少 N 档"的保底 */
const RARITY_ORDER = UPGRADE_RARITIES.map((r) => r.id);

/**
 * 保底稀有度：5 级必出蓝色、10 级必出紫色、15 级（及之后每 5 级）必出橙色。
 *
 * 用 `level % 5 === 0` 而不是写死 15，是为了**无限模式**下继续有保底
 * （20 / 25 / 30 级依然给传说）。
 *
 * @returns {string|null} 稀有度 id，没有保底则 null
 */
export function guaranteedRarity(level) {
  if (level >= 15 && level % 5 === 0) return 'legendary';
  if (level === 10) return 'epic';
  if (level === 5) return 'rare';
  return null;
}

/**
 * 按幸运抽一个稀有度。
 *
 * 幸运越高越容易出高稀有度；幸运的收益对越高的档位越大
 * （所以堆幸运是"赌高品质"的玩法，而不是线性收益）。
 *
 * @param {object} rng
 * @param {number} luck          面板幸运
 * @param {number} luckEffectMul 角色幸运总效果倍率（第十六轮，默认 1）
 */
export function rollRarity(rng, luck = 0, luckEffectMul = 1) {
  // 面板幸运 → 实际生效幸运（第十轮削弱到 80%；
  // 第十六轮再乘角色的 luckEffectMul —— 悠怡萌妹是 1.2）
  const luckBonus = Math.min(1, Math.max(0, effectiveLuckFor(luck, luckEffectMul) / 250));
  const weights = UPGRADE_RARITIES.map((r, i) => {
    // 普通档不因幸运变多，越高档涨得越快
    if (i === 0) return r.weight;
    return r.weight * (1 + luckBonus * (1 + i * 0.9));
  });
  let total = 0;
  for (const w of weights) total += w;
  let roll = rng.next() * total;
  for (let i = 0; i < weights.length; i++) {
    roll -= weights[i];
    if (roll <= 0) return UPGRADE_RARITIES[i].id;
  }
  return 'common';
}

/** 取两个稀有度里更高的那个（用于保底抬升） */
function atLeast(current, floor) {
  const a = RARITY_ORDER.indexOf(current);
  const b = RARITY_ORDER.indexOf(floor);
  return RARITY_ORDER[Math.max(a, b)];
}

/** 把一条升级的数值按稀有度缩放（正面全额，负面减半增长） */
function scaleUpgrade(base, rarityId) {
  const r = getRarity(rarityId);
  const scale = (obj, isDownsideAware) => {
    if (!obj) return undefined;
    const out = {};
    for (const k in obj) {
      const v = obj[k];
      // 负值（代价）用更温和的倍率；0 和正值按全额
      const m = (isDownsideAware && v < 0) ? r.downsideMult : r.mult;
      out[k] = v * m;
    }
    return out;
  };
  return {
    ...base,
    rarity: r.id,
    rarityName: r.name,
    rarityColor: r.color,
    mult: r.mult,
    // 护盾按**档位查表**，不吃 mult（原因见 up_heal_now 的注释）。
    // 放在这里而不是让 player 自己去查表，是为了让"抽到的这条升级"
    // 自带最终数值 —— 存档/UI/测试看到的都是同一个数。
    shieldPct: shieldPctFor(base, r.id),
    mods: scale(base.mods, false),
    pct: scale(base.pct, true),
  };
}

export const UPGRADES = [
  // ---------- 基础属性 ----------
  {
    id: 'up_maxhp', name: '强健体魄', glyph: '心', icon: 'i-heart', color: '#ff5f6d',
    mods: { maxHp: 6 }, maxLevel: 99, weight: 10,
    desc: '最大生命 +6。',
  },
  {
    id: 'up_damage', name: '锋利', glyph: '伤', icon: 'i-damage', color: '#ff9f68',
    pct: { damage: 8 }, maxLevel: 99, weight: 10,
    desc: '伤害 +8%。',
  },
  {
    id: 'up_attackspeed', name: '敏捷', glyph: '速', icon: 'i-atkspeed', color: '#7dd87d',
    pct: { attackSpeed: 8 }, maxLevel: 99, weight: 10,
    desc: '攻击速度 +8%。',
  },
  {
    id: 'up_movespeed', name: '轻快脚步', glyph: '步', icon: 'i-speed', color: '#5fd3e8',
    pct: { moveSpeed: 6 }, maxLevel: 99, weight: 9,
    desc: '移动速度 +6%。',
  },
  {
    id: 'up_armor', name: '硬化', glyph: '甲', icon: 'i-armor', color: '#8fd9f0',
    // 第十九轮：+2 → +3。护甲减伤阈值同期从 15 翻倍到 30（50% 减伤点），
    // 同样的甲换到的减伤近乎减半 —— 升级还给 +2 就成了没人选的陷阱选项。
    mods: { armor: 3 }, maxLevel: 99, weight: 8,
    desc: '护甲 +3。',
  },
  {
    id: 'up_regen', name: '恢复', glyph: '愈', icon: 'i-plus', color: '#6ee7a8',
    mods: { hpRegen: 0.5 }, maxLevel: 99, weight: 8,
    desc: '每秒回复 +0.5。',
  },
  {
    id: 'up_pickup', name: '扩大拾取', glyph: '拾', icon: 'i-magnet', color: '#8fd9f0',
    mods: { pickupRange: 30 }, maxLevel: 12, weight: 6,
    desc: '拾取范围 +30。',
  },
  {
    id: 'up_harvest', name: '收获', glyph: '收', icon: 'i-gem', color: '#ffc857',
    mods: { harvesting: 2 }, maxLevel: 20, weight: 6,
    desc: '收获 +2（每波结束额外材料）。',
  },

  // ---------- 战斗细节 ----------
  {
    // 用户第四轮："将暴击提升的基础属性降低到 3%，
    // 不然太容易到 100% 了。这样就算是金色暴击也只有 12% 提升不会太离谱。"
    // → 基础 3%，传说（×4）正好 12%
    id: 'up_crit', name: '致命一击', glyph: '暴', icon: 'i-crit', color: '#ffc857',
    pct: { critChance: 3 }, maxLevel: 12, weight: 7,
    desc: '暴击率 +3%。',
  },
  {
    id: 'up_critdmg', name: '重击', glyph: '重', icon: 'i-critdmg', color: '#ffb300',
    pct: { critDamage: 15 }, maxLevel: 12, weight: 6,
    desc: '暴击伤害 +15%。',
  },
  {
    id: 'up_lifesteal', name: '汲取', glyph: '吸', icon: 'i-drop', color: '#c62828',
    pct: { lifesteal: 3 }, maxLevel: 10, weight: 6,
    desc: '吸血 +3%。',
  },
  {
    id: 'up_dodge', name: '灵巧', glyph: '闪', icon: 'i-dodge', color: '#e8ecf6',
    pct: { dodge: 4 }, maxLevel: 10, weight: 6,
    desc: '闪避 +4%。',
  },
  {
    id: 'up_luck', name: '好运', glyph: '运', icon: 'i-paw', color: '#ffe08a',
    pct: { luck: 12 }, maxLevel: 15, weight: 6,
    desc: '幸运 +12%。',
  },
  {
    id: 'up_range', name: '远视', glyph: '远', icon: 'i-range', color: '#5fd3e8',
    pct: { range: 8 }, maxLevel: 10, weight: 5,
    // 第三十四轮：提示从"远程射程"改成"全射程" —— 第三十二轮起近战也吃
    // 这个属性（普通近战五折、360 度 150%），炮台的索敌距离同样吃，
    // 再叫"远程射程"就是错误描述了。用户："把升级选项的那个远程射程
    // 提示改成全射程提示升级（即包含近战远战和炮台射程）相当于强化。"
    desc: '全射程 +8%（近战、远程、炮台都吃）。',
  },

  // ---------- 专精 ----------
  {
    id: 'up_melee', name: '近战专精', glyph: '近', icon: 'i-sword', color: '#c9d6e8',
    pct: { meleeDamage: 12 }, maxLevel: 15, weight: 7,
    desc: '近战伤害 +12%。',
  },
  {
    id: 'up_ranged', name: '远程专精', glyph: '远2', icon: 'i-arrow', color: '#8fd9f0',
    pct: { rangedDamage: 12 }, maxLevel: 15, weight: 7,
    desc: '远程伤害 +12%。',
  },
  {
    id: 'up_elem', name: '元素专精', glyph: '元', icon: 'i-orb', color: '#b794f6',
    pct: { elemDamage: 12 }, maxLevel: 15, weight: 7,
    desc: '元素伤害 +12%。',
  },

  // ---------- 强力但有代价 ----------
  {
    id: 'up_glass', name: '破釜沉舟', glyph: '破', icon: 'i-burst', color: '#ff4d4d',
    pct: { damage: 18, attackSpeed: 10, maxHp: -0.10 }, maxLevel: 5, weight: 4,
    desc: '伤害 +18%，攻速 +10%，最大生命 -10%。',
  },
  {
    id: 'up_fortress', name: '铁壁', glyph: '壁', icon: 'i-armor', color: '#8b93b0',
    mods: { armor: 6 }, pct: { moveSpeed: -6 }, maxLevel: 8, weight: 4,
    desc: '护甲 +6，移速 -6%。',
  },
  {
    id: 'up_greed', name: '贪得无厌', glyph: '贪', icon: 'i-coin', color: '#ffb300',
    mods: { harvesting: 8 }, pct: { damage: -6 }, maxLevel: 5, weight: 4,
    desc: '收获 +8，伤害 -6%。',
  },
  {
    id: 'up_berserk', name: '狂暴', glyph: '狂', icon: 'i-rage', color: '#ff4d4d',
    pct: { attackSpeed: 22, dodge: -6 }, maxLevel: 5, weight: 4,
    desc: '攻速 +22%，闪避 -6%。',
  },

  // ---------- 特殊：直接回血 ----------
  //
  // 第十二轮（用户反馈）：
  //   "给那个回复全部生命的选项的高级品质等级改成回满血并且额外回复 50%
  //    生命上限的护盾，史诗给 100% 护盾，传说给 150% 血护盾
  //    （不然传奇回血也只是回满血太菜了）。"
  //
  // 做法：`heal` 保持 999（=回满，这是所有档位的保底），
  // 额外加一个 `shieldPct` 字段表示"生命上限的多少倍护盾"。
  //
  // ⚠️ 为什么 shieldPct 用**普通/稀有/史诗/传说四档手写**，
  //    而不是像其它升级那样交给 scaleUpgrade 的 rarity.mult 去乘：
  //      mult 是 1/2/3/4。如果 shieldPct 基础值写 0.5，四档就变成
  //      0.5/1.0/1.5/2.0 —— 传说档会是 200%，而用户要的是 **150%**。
  //      用户的档位是 50/100/150（等差 +50），不是 50/100/150/200 的 ×2/×3。
  //      所以这里显式按稀有度查表，不走乘算。
  //      （普通档用户没提，给 0 = 只回满血，保持它作为"保底治疗"的定位。）
  {
    id: 'up_heal_now', name: '猫娘的慰藉', glyph: '疗', icon: 'i-plus', color: '#6ee7a8',
    heal: 999, shieldPct: { common: 0, rare: 0.5, epic: 1.0, legendary: 1.5 },
    maxLevel: 99, weight: 5,
    desc: '立即回复全部生命。高品质额外获得护盾。',
  },
];

/**
 * 取某条升级在某稀有度下的护盾比例（生命上限的倍数）。
 *
 * 之所以做成函数而不是在 scaleUpgrade 里处理：
 *   它**不参与 rarity.mult 乘算**（见上面 up_heal_now 的注释），
 *   是个按档位查表的覆盖值。放这里是为了让 "护盾怎么算" 只有一个执行点，
 *   测试可以直接断言这张表，而不是去猜一个乘法结果。
 *
 * @returns {number} 生命上限的倍数（0 表示不给护盾）
 */
export function shieldPctFor(base, rarityId) {
  const table = base?.shieldPct;
  if (!table) return 0;
  return table[rarityId] ?? 0;
}

export const UPGRADE_MAP = Object.fromEntries(UPGRADES.map((u) => [u.id, u]));
export const getUpgrade = (id) => UPGRADE_MAP[id];

/**
 * 抽取升级选项。
 *
 * 用户要求：稀有度和运气挂钩，且 5/10/15 级必定出蓝/紫/橙。
 * 所以流程是：
 *   1. 先按 weight 抽「哪些升级」出现
 *   2. 再给每个选项抽「稀有度」（受幸运影响）
 *   3. 如果这一级有保底，**至少有一个选项**被抬到保底稀有度
 *   4. 把效果按稀有度倍率缩放
 *
 * @param {RNG} rng
 * @param {Map<string, number>} taken 已选升级 -> 次数
 * @param {number} count 抽几个
 * @param {object} [opts]
 * @param {number} [opts.level] 当前等级（用于保底）
 * @param {number} [opts.luck]  玩家幸运（用于稀有度权重）
 */
export function rollUpgrades(rng, taken, count = 3, opts = {}) {
  const pool = UPGRADES.filter((u) => (taken.get(u.id) || 0) < (u.maxLevel ?? 99));
  if (pool.length === 0) return [];

  const picks = [];
  const available = pool.slice();
  const n = Math.min(count, available.length);
  for (let i = 0; i < n; i++) {
    // 按 weight 加权抽
    let total = 0;
    for (const u of available) total += (u.weight ?? 1);
    let r = rng.next() * total;
    let idx = available.length - 1;
    for (let j = 0; j < available.length; j++) {
      r -= (available[j].weight ?? 1);
      if (r <= 0) { idx = j; break; }
    }
    picks.push(available[idx]);
    available.splice(idx, 1);
  }

  // 每个选项抽稀有度
  const luck = opts.luck ?? 0;
  const luckEffectMul = opts.luckEffectMul ?? 1;
  const rarities = picks.map(() => rollRarity(rng, luck, luckEffectMul));

  // 保底：这一级该出的最低稀有度，至少落在其中一个选项上
  const floor = guaranteedRarity(opts.level ?? 0);
  if (floor) {
    // 抬升到保底：优先抬"当前最低"的那个，让保底尽量显眼
    let worst = 0;
    for (let i = 1; i < rarities.length; i++) {
      if (RARITY_ORDER.indexOf(rarities[i]) < RARITY_ORDER.indexOf(rarities[worst])) worst = i;
    }
    rarities[worst] = atLeast(rarities[worst], floor);
  }

  return picks.map((u, i) => scaleUpgrade(u, rarities[i]));
}

/** 升级所需经验：随等级递增 */
export function xpForLevel(level) {
  // 1->2 需要 5，之后逐渐变多
  return Math.floor(4 + level * 2.2 + Math.pow(level, 1.5) * 0.8);
}