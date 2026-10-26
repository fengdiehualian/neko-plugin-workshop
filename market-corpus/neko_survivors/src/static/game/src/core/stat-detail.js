// ============================================================
// 属性详情（第五轮新增）
//
// 用户反馈："增加详细数值查看，比如商店页面鼠标放在自己的信息上
// 可以查看部分模糊的信息详情，比如护甲多少数值有多少减伤，
// 目前有多少闪避，幸运增加了多少掉率之类的。"
//
// 面板上原来只显示"护甲 23"、"幸运 45%"这种裸数值，玩家看不出
// 实际收益。这里把每个属性**换算成玩家真正关心的结果**：
//   护甲 30   → 减伤 50%（承受 50% 伤害；第十九轮阈值翻倍，原 15 甲）
//   幸运 45%  → 材料掉落概率 ×1.45；商店高品质概率 +20%
//   攻速 120% → 开火频率 ×2.20
//
// 关键：这里的公式必须和战斗代码里用的是同一套，否则就是骗玩家。
// 每条都标注了对应的实现位置，改数值时一起改。
// ============================================================

import { STAT_DEFS, armorMultiplier, effectiveLuckFor, LUCK_EFFECT_MUL } from './stats.js';

/** 把 0~1 的比例写成 "60.5%" */
function pct(x, digits = 1) {
  return `${(x * 100).toFixed(digits)}%`;
}

/** clamp01 */
const c01 = (v) => Math.max(0, Math.min(1, v));

/**
 * 生成一个属性的"人话解释"。
 *
 * @param {string} key   属性 key
 * @param {object} s     已经算好的最终属性值（player.stats.values）
 * @param {number} luckEffectMul 角色幸运总效果倍率（第十六轮，可选）
 *        悠怡萌妹是 1.2。不传等价于 1，也就是老的 ×0.8 行为。
 * @returns {string}     一行说明；没有可解释的返回空串
 */
export function describeStat(key, s, luckEffectMul = 1) {
  const v = s[key];
  if (v === undefined) return '';

  switch (key) {
    // ---- 生存 ----
    case 'maxHp':
      return '当前生命上限。受伤后每波结束会回满。';

    case 'hpRegen':
      return v > 0 ? `每秒回复 ${v.toFixed(1)} 点生命。` : '没有生命回复。';

    case 'armor': {
      // 实现：entities/player.js takeDamage → stats.armorMul → armorMultiplier()
      const mul = armorMultiplier(v);
      const cut = 1 - mul;
      if (v <= 0) return '没有护甲，承受全额伤害。';
      return `减伤 ${pct(cut)}，实际只承受 ${pct(mul)} 伤害。`
        + `（递减曲线：30 甲 = 减伤 50%，最多减伤 80%）`;
    }

    case 'dodge':
      // 实现：entities/player.js takeDamage → rng < dodge 则完全免伤
      return v > 0
        ? `有 ${pct(v / 100)} 的概率完全躲开一次伤害（闪避成功不受任何伤害）。`
        : '没有闪避，所有攻击都会命中。';

    case 'lifesteal':
      // 实现：systems/world.js dealDamage → player.applyLifesteal(ls)
      // applyLifesteal 里是 heal(1)，也就是**每次回 1 点**，
      // 不是按伤害百分比回血 —— 文案必须跟实现一致，否则是骗玩家。
      return v > 0
        ? `命中时有 ${pct(v / 100)} 的概率回复 1 点生命（每次固定 1 点，不看伤害高低）。`
        : '没有吸血。';

    // ---- 输出 ----
    case 'damage':
      return v !== 0
        ? `所有武器伤害 ×${(1 + v / 100).toFixed(2)}（即 ${v > 0 ? '+' : ''}${Math.round(v)}%）。`
        : '武器伤害没有额外加成。';

    case 'meleeDamage':
      return v !== 0
        ? `仅近战武器伤害 ×${(1 + v / 100).toFixed(2)}。`
        : '近战武器没有额外加成。';

    case 'rangedDamage':
      return v !== 0
        ? `仅远程武器伤害 ×${(1 + v / 100).toFixed(2)}。`
        : '远程武器没有额外加成。';

    case 'elemDamage':
      return v !== 0
        ? `仅元素武器伤害 ×${(1 + v / 100).toFixed(2)}。`
        : '元素武器没有额外加成。';

    case 'attackSpeed': {
      // 实现：entities/weapon.js computeCooldown → base / (1 + as/100)
      const mul = 1 + v / 100;
      if (v === 0) return '武器按原始频率开火。';
      if (mul <= 0) return '攻速被压到极低，武器几乎无法开火。';
      return `开火频率 ×${mul.toFixed(2)}，也就是每秒多打 ${Math.round((mul - 1) * 100)}%。`;
    }

    case 'critChance': {
      // 实现：systems/world.js dealDamage → rng*100 < critChance
      const cd = s.critDamage ?? 50;
      return v > 0
        ? `每次命中 ${pct(v / 100)} 概率暴击，暴击造成 ×${(1 + cd / 100).toFixed(2)} 伤害。`
        : '不会暴击。';
    }

    case 'critDamage':
      // 基础 50 表示"暴击多打 50%"
      return `暴击时额外 +${Math.round(v)}% 伤害（暴击总倍率 ×${(1 + v / 100).toFixed(2)}）。`;

    case 'range':
      // 第三十四轮：近战也吃这个属性了（第三十二轮），提示改成全射程口径
      return v !== 0
        ? `全射程 ×${(1 + v / 100).toFixed(2)}（近战按五折、360 度武器按 150% 结算，远程/炮台全额）。`
        : '武器射程没有加成。';

    // ---- 机动 ----
    case 'moveSpeed':
      return v !== 0
        ? `移动速度 ×${(1 + v / 100).toFixed(2)}。`
        : '基础移动速度。';

    case 'pickupRange':
      return `自动吸取材料的半径 ${Math.round(v)} 像素（越大越不用走过去捡）。`;

    // ---- 收益 ----
    case 'luck': {
      // 实现分散在多处（第十轮统计出 6 个消费点，散在 4 个文件里）：
      //   systems/world.js _dropLoot        → 材料掉落概率 × (1 + luck/100)
      //   systems/world.js _dropLoot        → 医疗包概率 × (1 + luck/100)
      //   systems/shop.js  _makeOffer       → luckBonus = clamp01(luck/220)
      //   systems/world.js 宝箱怪稀有概率    → +min(0.3, luck/400)
      //   data/chest.js    rollChestReward  → 开箱出道具/稀有的概率
      //   data/upgrades.js rollRarity       → 升级选项的稀有度权重
      //   systems/world.js _rollRewardItem  → 奖励道具的价格分层权重
      //
      // **第十轮**：用户要求"幸运效果太强力了，获取容易，
      // 将幸运的总效果调整为80%"。所以上面每一处都改成了
      // effectiveLuck()（core/stats.js），统一乘 0.8。
      // 面板显示的仍是买到的原值，这里算出来的收益也用同一个函数，
      // 保证"面板说的"和"实际生效的"永远一致。
      // 第十六轮：角色还能再带一个 luckEffectMul（悠怡萌妹 +20%），
      // 所以这里用 effectiveLuckFor(v, luckEffectMul)。
      // 面板必须显示**真实生效**的收益 —— 否则萌妹玩家会看到
      // "总效果按 80% 计入"，但实际是 96%，工具提示就在骗人。
      if (v === 0) return '没有幸运加成，掉落与商店按基础概率。';
      const eff = effectiveLuckFor(v, luckEffectMul);
      const dropMul = 1 + eff / 100;
      const shopBonus = c01(eff / 220);
      const mimicBonus = Math.min(0.3, Math.max(0, eff) / 400);
      const parts = [`材料与医疗包掉落概率 ×${dropMul.toFixed(2)}`];
      if (shopBonus > 0) {
        parts.push(`商店高品质商品权重 +${Math.round(shopBonus * 100)}%`);
      }
      if (mimicBonus > 0) {
        parts.push(`华丽宝箱怪概率 +${pct(mimicBonus)}`);
      }
      // 把"实际按多少计入"明确写出来，否则玩家会觉得数值对不上。
      // 有角色倍率时写真实倍率（0.8 × 1.2 = 96%），没有就写基础的 80%。
      const totalMul = LUCK_EFFECT_MUL * (luckEffectMul > 0 ? luckEffectMul : 1);
      parts.push(`（幸运总效果已按 ${Math.round(totalMul * 100)}% 计入）`);
      return parts.join('；') + '。';
    }

    case 'harvesting':
      return v > 0
        ? `每波结束时额外获得 ${Math.round(v)} 材料。`
        : '每波结束没有额外材料。';

    // ---- 炮台 ----
    case 'turretDamage':
      return v !== 0
        ? `仅炮台伤害 ×${(1 + v / 100).toFixed(2)}。`
        : '炮台伤害没有额外加成。';

    case 'turretRate':
      return v !== 0
        ? `炮台开火频率 ×${(1 + v / 100).toFixed(2)}。`
        : '炮台按原始频率开火。';

    case 'turretRange':
      return v !== 0
        ? `炮台索敌距离 ×${(1 + v / 100).toFixed(2)}。`
        : '炮台索敌距离没有加成。';

    case 'turretMax': {
      // 唯一保留上限的属性（防止实体过多崩帧）
      const cap = STAT_DEFS.turretMax.max;
      return `额外可同时存在 ${Math.round(v)} 座炮台（上限 ${cap}，超过的会替换最旧的一座）。`;
    }

    default:
      return STAT_DEFS[key]?.desc || '';
  }
}

/**
 * 生成某个属性的"明细行"（用于悬浮卡片）。
 * 返回 { title, value, detail } 结构，UI 直接渲染。
 */
export function statDetailRow(key, s) {
  const def = STAT_DEFS[key];
  if (!def) return null;
  return {
    key,
    title: def.name,
    value: s[key],
    detail: describeStat(key, s),
  };
}

/**
 * 生成整组属性的明细（按 UI 的分组顺序）。
 * @param {object} s player.stats.values
 * @param {string[][]} groups [[组名, [key...]], ...]
 */
export function statDetailGroups(s, groups) {
  return groups.map(([title, keys]) => ({
    title,
    rows: keys
      .filter((k) => STAT_DEFS[k])
      .map((k) => statDetailRow(k, s))
      .filter(Boolean),
  }));
}
