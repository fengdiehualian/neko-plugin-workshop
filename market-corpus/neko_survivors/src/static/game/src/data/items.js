// ============================================================
// 道具定义
// mods  = 加法修正（生命、护甲、收获、拾取范围…）
// pct   = 百分比修正（伤害、攻速、移速…）
// maxStacks = 最多叠加次数（不写默认 99）
//
// ⚠️ 关于「扣最大生命」的写法（踩过的坑）：
// flat 类属性（maxHp）的 pct 修正按 **乘算** 叠加，即
//     final = (base + flat) * (1 + Σpct)
// 所以 maxHp:-0.2 买 5 个是 (1-0.2*5) = 0 → 被 min 夹到 1。
// 这会让玩家"永久只有 1 点生命上限"，是上一版的严重缺陷。
//
// 现在统一用 **curse** 字段表达"扣上限"：每次购买立刻按乘算
// 扣掉当前上限的一个比例，且整体下限受 CURSE_FLOOR 保护，
// 不会把玩家压到 1 点血。见 player.js 的 applyItemMods()。
// ============================================================

// ---- CURSE_FLOOR 统一到一份（第十二轮）----
//
// 这个常量原来在 core/stats.js 和这里**各写了一份 0.45**。
// 两份值一样的时候看不出问题，但它其实是同一个游戏规则的两个执行点：
//   core/stats.js  → addCurse() 兜底、以及负 pct 乘数下限（真正生效的地方）
//   data/items.js  → 只是个副本，被 test/round2-check.mjs 从这里 import
// 一旦有人只改一处（比如把地板从 0.45 调到 0.35），
// 就会出现"数值算的是 0.35、测试断言的是 0.45"这种最难查的漂移 ——
// 而且副本这份还会**掩盖**漏改：测试绿着，游戏却已经变了。
//
// 所以改成从 core/stats.js 单点导出、这里原样再导出：
// 老的 `import { CURSE_FLOOR } from '../data/items.js'`（round2-check）
// 不用动，但值只有一份真源。
//
// 方向是 items → stats 而不是反过来：stats.js 是纯数值核心，
// 不该 import 数据表（那会引入循环依赖，也会让核心层依赖具体道具）。
import { CURSE_FLOOR } from '../core/stats.js';
export { CURSE_FLOOR };

export const ITEMS = [
  // ---------- 芙兰朵露战利品（第四十九轮）----------
  // 用户要求：Extra Phantom 存活 60 秒掉落"莱万汀"，红色至尊品质，
  // "造成的伤害获得80%最终加成，受到的伤害获得50%最终加成"。
  // 伤害侧走 stats.addFinalMul('damage')（第十六轮的最终乘算通道），
  // 承伤侧挂在 player.takenFinalMul（见 player.js applyItemMods/takeDamage）。
  {
    id: 'laevatein', name: '莱万汀', color: '#ff2e4d', glyph: '莱', price: 99,
    // 第一百六十轮：数值调整 + 附带【四重存在】——
    // 用户："从造成的伤害+80%（最终乘算）改到+60%……受到的伤害增加从
    // +50%改到+30%。有概率释放【四重存在】。"
    finalDamageMul: 1.6, finalTakenMul: 1.3, maxStacks: 1,
    // 第八十五轮：芙兰专属 —— 用户："应该只有打败芙兰才能获得。"
    // 商店随机池 / 宝箱奖励池一律排除；唯一入口是芙兰战的结算掉落
    //（main.js 结算三分支直接塞货架，不走随机池）。
    flandreOnly: true,
    desc: '芙兰的禁弹魔剑。造成的伤害 +60%（最终乘算），受到的伤害 +30%（最终乘算）。持有期间，每 5 秒有 20% 概率释放【四重存在】：变出至多 3 个分身（继承 25% 属性与同款武器，总输出约为自身的 25%），分身倒下后从你所在的位置重新现形。',
  },
  // ---------- 维吉尔战利品（第一百四十一轮）----------
  // 用户要求：90 秒内打败大排档椅子·维吉尔掉落"阎魔刀"，红色至尊。
  // "在玩家生命低于 50% 的时候释放一次次元斩……时间变慢效果存在，
  // 所有怪物都变慢。每回合限一次。"（每斩 500 真伤的数值不写进描述 ——
  // 描述与数值分离是用户原话）。触发逻辑在 main.js _updatePlaying，
  // bossDropOnly 让它同莱万汀一样不进商店随机池/宝箱池。
  {
    id: 'yamato', name: '阎魔刀', color: '#ff2e4d', glyph: '阎', price: 88,
    maxStacks: 1, bossDropOnly: true,
    desc: '次元斩的具现。生命低于 50% 时自动释放一次次元斩：时间变慢，所有怪物陷入慢动作，斩击撕裂全场敌人。每回合限一次。',
  },
  // ---------- 生存类 ----------
  {
    id: 'warm_milk', name: '热牛奶', color: '#fff3d6', glyph: '奶', price: 12,
    // 第二十二轮：最大生命 6 → 4（用户："削弱"）
    mods: { maxHp: 4, hpRegen: 0.3 },
    desc: '睡前一杯。最大生命 +4，每秒回复 +0.3。',
  },
  {
    id: 'cat_armor', name: '毛线护甲', color: '#8fd9f0', glyph: '甲', price: 15,
    // 第二十二轮：加强 —— 护甲 3 → 4，新增最大生命 +2（用户："加强"）
    mods: { armor: 4, maxHp: 2 },
    desc: '织得密密实实的毛线衣。护甲 +4，最大生命 +2。',
  },
  {
    id: 'lucky_bell', name: '幸运铃', color: '#ffe08a', glyph: '铃', price: 18,
    // 第二十二轮：闪避 6 → 4、幸运 10 → 8（用户："削弱"）
    pct: { dodge: 4, luck: 8 },
    desc: '叮当作响。闪避 +4%，幸运 +8%。',
  },
  {
    id: 'thick_fur', name: '厚毛皮', color: '#e0c9a6', glyph: '毛', price: 20,
    // 第二十二轮：最大生命 10 → 8（移速 -4% 不变；用户："削弱"）
    mods: { maxHp: 8 },
    pct: { moveSpeed: -4 },
    desc: '蓬松保暖。最大生命 +8，移速 -4%。',
  },
  {
    id: 'healing_herb', name: '治愈猫薄荷', color: '#6ee7a8', glyph: '草', price: 22,
    // 第二十二轮：每秒回复 1.0 → 0.5（削弱）。
    // 第二十四轮后用户复核："治愈猫薄荷削弱后快不如热牛奶了，热牛奶甚至
    // 还有生命上限加成。把治愈猫薄荷的生命回复从+0.5改到+0.8，不然它
    // 完全不如白色品质的热牛奶能打。"
    mods: { hpRegen: 0.8 },
    desc: '闻一闻就舒服。每秒回复 +0.8。',
  },

  // ---------- 输出类 ----------
  {
    id: 'sharp_fang', name: '尖锐獠牙', color: '#ff9f68', glyph: '牙', price: 16,
    pct: { damage: 8 },
    desc: '咬合力惊人。伤害 +8%。',
  },
  {
    id: 'catnip', name: '猫薄荷', color: '#7dd87d', glyph: '薄', price: 18,
    // 第三十二轮：额外加攻击范围 +5%（用户："给那个攻击速度+10%的道具
    // 额外加个攻击范围+5%的效果"）。近战按五折结算、360 度武器 150%，
    // 见 weapon.computeRange。
    pct: { attackSpeed: 10, range: 5 },
    desc: '兴奋起来手速变快。攻速 +10%，攻击范围 +5%。',
  },
  {
    id: 'scope_lens', name: '瞄准镜片', color: '#5fd3e8', glyph: '镜', price: 16,
    // 第二十二轮：远程伤害 12 → 8（射程 +10% 不变；用户："削弱"）
    pct: { rangedDamage: 8, range: 10 },
    desc: '看得更远也更准。远程伤害 +8%，射程 +10%。',
  },
  {
    id: 'iron_knuckle', name: '铁指套', color: '#c9d6e8', glyph: '拳', price: 16,
    pct: { meleeDamage: 12 },
    desc: '近身格斗必备。近战伤害 +12%。',
  },
  {
    id: 'magic_gem', name: '魔力宝石', color: '#b794f6', glyph: '宝', price: 20,
    pct: { elemDamage: 14 },
    desc: '蕴含元素之力。元素伤害 +14%。',
  },
  {
    id: 'crit_eye', name: '猫瞳', color: '#ffc857', glyph: '瞳', price: 24,
    // 第二十二轮：暴击率 8 → 5、暴击伤害 20 → 15（用户："削弱"）
    pct: { critChance: 5, critDamage: 15 },
    desc: '在黑暗中也能看清要害。暴击率 +5%，暴击伤害 +15%。',
  },
  {
    id: 'glass_cannon', name: '玻璃炮', color: '#ff5f6d', glyph: '玻', price: 28,
    pct: { damage: 25 },
    curse: { maxHp: 0.15 },
    desc: '威力巨大但很脆。伤害 +25%，最大生命 -15%（乘算，可叠加但有下限）。',
  },

  // ---------- 炮台流派 ----------
  {
    id: 'gear_oil', name: '齿轮机油', color: '#d9b382', glyph: '油', price: 16,
    // 第二十二轮：炮台攻速 18 → 12（用户："削弱"）
    pct: { turretRate: 12 },
    desc: '让炮台转得更快。炮台攻速 +12%。',
  },
  {
    id: 'radar_chip', name: '雷达芯片', color: '#5fd3e8', glyph: '雷', price: 18,
    pct: { turretRange: 20, turretDamage: 8 },
    desc: '索敌更远也更准。炮台射程 +20%，炮台伤害 +8%。',
  },
  {
    id: 'turret_blueprint', name: '炮台图纸', color: '#6ee7a8', glyph: '图', price: 26,
    // 第二十二轮：炮台伤害 22 → 16（炮台上限 +1 不变；用户："削弱"）
    pct: { turretDamage: 16 },
    mods: { turretMax: 1 },
    desc: '更精密的炮台设计。炮台伤害 +16%，可同时多放 1 座炮台。',
  },
  {
    id: 'steel_plate', name: '钢制装甲板', color: '#a9b4c9', glyph: '钢', price: 22,
    // 第二十二轮：炮台伤害 10 → 7（护甲 +5 / 移速 -3% 不变；用户："削弱"）
    mods: { armor: 5 },
    pct: { turretDamage: 7, moveSpeed: -3 },
    desc: '又厚又重。护甲 +5，炮台伤害 +7%，移速 -3%。',
  },

  // ---------- 经济 / 功能类 ----------
  {
    id: 'shiny_coin', name: '闪亮硬币', color: '#ffc857', glyph: '金', price: 14,
    mods: { harvesting: 3 },
    desc: '每波结束多捡一些。收获 +3。',
  },
  {
    id: 'magnet_paw', name: '磁力猫爪', color: '#8fd9f0', glyph: '磁', price: 12,
    mods: { pickupRange: 45 },
    desc: '材料会自动飞过来。拾取范围 +45。',
  },
  {
    id: 'four_leaf', name: '四叶草', color: '#6ee7a8', glyph: '叶', price: 20,
    // 第二十二轮：幸运 25 → 16（用户："削弱"）
    pct: { luck: 16 },
    desc: '幸运 +16%。商店里更容易出好东西。',
  },
  {
    id: 'lucky_cat', name: '招财猫', color: '#ffd166', glyph: '招', price: 26,
    mods: { harvesting: 6 },
    pct: { luck: 15 },
    desc: '举手招财。收获 +6，幸运 +15%。',
  },
  {
    id: 'running_shoes', name: '跑鞋', color: '#5fd3e8', glyph: '鞋', price: 14,
    pct: { moveSpeed: 12 },
    desc: '跑得飞快。移速 +12%。',
  },
  {
    id: 'feather', name: '轻盈羽毛', color: '#e8ecf6', glyph: '羽', price: 18,
    // 第二十二轮：移速 8 → 6、闪避 5 → 4（用户："削弱"）
    pct: { moveSpeed: 6, dodge: 4 },
    desc: '脚步轻得像风。移速 +6%，闪避 +4%。',
  },

  // ---------- 高风险高回报 ----------
  {
    // 第一百一十五轮：穿甲弹 —— 用户："增加一个新道具'穿甲弹'品质：
    // 史诗（t3）。效果，你的远程攻击（远程，元素，炮台）可以穿透一个
    // 敌人，穿透后的伤害-40%。再次购买效果变为穿透数值+1。（穿透上限5）
    // 买一个可以穿过一个敌人，买2个可以穿2个，但是伤害会第二次扣抵
    // （比如100伤害，穿透一次是打60，穿透2次是打36，3次是22）"
    // 价格 30 → itemTier 3（史诗紫）。穿透段衰减 = 每穿一个 ×0.6
    // （100 → 60 → 36 → 21.6≈22），只在**道具给的**穿透上衰减，
    // 武器自带穿透（如贯穿 99 的激光笔）不吃衰减 —— 不然现有武器被
    // 顺手砍残。上限 5 由 maxStacks 把关。
    id: 'ap_round', name: '穿甲弹', color: '#b794f6', glyph: '穿', price: 30,
    apPierce: 1, maxStacks: 5,
    desc: '远程/元素/炮台攻击可穿透 1 个敌人，穿透后的伤害 -40%。再次购买穿透 +1（上限 5）。',
  },
  {
    id: 'cursed_collar', name: '诅咒项圈', color: '#8b93b0', glyph: '咒', price: 24,
    pct: { damage: 20, attackSpeed: 15, dodge: -5 },
    curse: { maxHp: 0.20 },
    desc: '力量有代价。伤害 +20%，攻速 +15%，闪避 -5%，最大生命 -20%（乘算，可叠加但有下限）。',
  },
  {
    id: 'blood_pact', name: '血之契约', color: '#c62828', glyph: '血', price: 30,
    pct: { damage: 18, lifesteal: 5 },
    curse: { maxHp: 0.12 },
    desc: '以血换力。伤害 +18%，吸血 +5%，最大生命 -12%（乘算）。',
    maxStacks: 3,
  },
  {
    id: 'greed_idol', name: '贪婪神像', color: '#ffb300', glyph: '贪', price: 25,
    mods: { harvesting: 10 },
    pct: { damage: -8 },
    desc: '钱比命重要。收获 +10，伤害 -8%。',
  },
  {
    id: 'berserk_pill', name: '狂暴药丸', color: '#ff4d4d', glyph: '狂', price: 26,
    pct: { attackSpeed: 30 },
    // 第九轮修正（用户反馈）：
    //   "狂暴药丸削弱的护甲30%太高了，改成固定数值比较好，或者也变成乘算扣除，
    //    如果走护甲流派买2个药丸自己的护甲就掉完了。"
    //
    // 原来写在 pct 里（armor: -0.3）。护甲是 kind:'flat' 属性，
    // 所以 pct 是**加算**的：(base+flat) × (1 + pct)，
    // 买 2 个就是 ×0.4，买 4 个直接 ×(1-1.2) = 负数 → 护甲清零。
    // 这对护甲流派是毁灭性的，而且越堆护甲损失越大。
    //
    // 改用 curse（真正的乘算，和「诅咒」类道具同一套机制）：
    //   每个药丸 ×0.7，买 n 个就是 0.7ⁿ，且有 CURSE_FLOOR 兜底，
    //   永远不会把护甲扣成 0。护甲 30 买 2 个还有 14.7，买 3 个还有 10.3，
    //   既保住了"用防御换攻速"的取舍，又不至于把流派直接废掉。
    curse: { armor: 0.3 },
    desc: '停不下来。攻速 +30%，护甲 ×0.7（乘算，可叠加）。',
  },

  // ============================================================
  // ---------- 第一百三十四轮：Boss 专属掉落红品（8 件）----------
  //
  // 用户设计："不再进商店，改为 Boss 专属掉落。击败第 10 波 Boss 掉落
  // 1 个随机红品；击败第 20 波 Boss 掉落 1 个随机红品（为无尽模式准备）。
  // 掉落后在商店领取，和宝箱掉落一样。10 和 20 的 Boss 掉落的随机红品
  // 不含莱万汀。"
  //
  // bossDropOnly: true —— 商店随机池 / 宝箱 / 宝箱怪池 / 兜底全部排除，
  // 唯一入口是 Boss 击杀掉落（main.js onBossKilled 塞货架，走宝箱战利品
  // 同款"留下或卖掉"流程）。
  // 效果实现散布在消费点，字段只是声明：
  //   levelUpAllStats  → player.applyUpgrade（每升一级全属性 +1）
  //   lifestealHealBonus / lifestealDamageMul → applyLifesteal / computeDamage
  //   forceHoming      → world.spawnBullet（远程/炮台弹追踪）+ computeRange ×1.3
  //   thornsRadius/thornsArmorMul → main.js player.onHurt（受击 AOE = 护甲×50）
  //   critAgainChance  → world.dealDamage（暴击时概率再暴击一次）
  //   hurtMissChance   → player.takeDamage（受伤概率变 miss）
  //   statCapBonus     → stats（闪避上限 80 → 90）
  //   healDoubleBelowHalf → player.heal（低于 50% 血治疗翻倍）
  //   hitDebuff        → world.dealDamage / enemy.takeDamage（命中叠易伤）
  // ============================================================
  {
    id: 'dried_fish', name: '小鱼干', color: '#ff6d6d', glyph: '鱼', price: 38,
    bossDropOnly: true, maxStacks: 1,
    levelUpAllStats: 1,
    desc: '很普通的小鱼干，但这是那个人买给人家的，看到它人家就充满了力量。每升一级，所有属性 +1。',
  },
  {
    id: 'lobster_pillow', name: '龙虾抱枕', color: '#ff5252', glyph: '虾', price: 40,
    bossDropOnly: true, maxStacks: 1,
    lifestealHealBonus: 1, lifestealDamageMul: true,
    desc: '应该不会有人不喜欢吃龙虾吧~龙虾人家最喜欢了。吸血回复翻倍，每有 1% 的吸血，伤害增加 1%（最终乘算）。',
  },
  {
    id: 'smart_wand', name: '智慧型魔棒', color: '#e64a8f', glyph: '棒', price: 40,
    bossDropOnly: true, maxStacks: 1,
    forceHoming: true, rangeFinalMul: 1.3,
    desc: '对的，对的，魔法也是法，枪法也是法。所有远程攻击和炮台攻击都会追踪敌人，且远程攻击射程 +30%（最终乘算）。',
  },
  {
    id: 'dr_m_notes', name: 'M博士心得', color: '#d32f2f', glyph: '博', price: 42,
    bossDropOnly: true, maxStacks: 1,
    finalTakenMul: 0.8, thornsRadius: 300, thornsArmorMul: 50,
    desc: '上面密密麻麻的写满了什么时候什么地点挨打更舒服，看着吓人。受到的伤害 -20%（最终乘算），每次受到伤害时触发一次 300 范围的 AOE 攻击，伤害为护甲数值的 50 倍。',
  },
  {
    id: 'lucky_cat_paw', name: '幸运猫脚', color: '#ff7043', glyph: '脚', price: 40,
    bossDropOnly: true, maxStacks: 1,
    critAgainChance: 0.2, hurtMissChance: 15,
    desc: '谁说只有兔子的脚丫才能带来好运，人家的也可以，要踩踩你吗？暴击时有 20% 概率再暴击一次，受伤时有 15% 概率变成 miss。',
  },
  {
    id: 'black_cape', name: '黑色披风', color: '#8e24aa', glyph: '披', price: 38,
    bossDropOnly: true, maxStacks: 1,
    pct: { dodge: 20 }, statCapBonus: { dodge: 10 },
    desc: '这是人家的衣服，谁和你说是披风的？闪避 +20%，闪避上限变成 90%。',
  },
  {
    id: 'cat_taffy', name: '猫娘奶糖', color: '#ef5350', glyph: '糖', price: 38,
    bossDropOnly: true, maxStacks: 1,
    mods: { hpRegen: 2 }, healDoubleBelowHalf: true,
    desc: '哇，这个超级甜呢。感觉体力恢复了好多！生命回复 +2，生命低于 50% 时，受到的所有治疗效果翻倍。',
  },
  {
    id: 'overload_chip', name: '超载芯片', color: '#f44336', glyph: '芯', price: 45,
    bossDropOnly: true, maxStacks: 1,
    hitDebuff: { perHit: 1, max: 30 },
    desc: '这个东西怎么越来越红了？不会爆炸吧喵？攻击命中敌人会让其获得受到的伤害 +1% 的效果，最多叠加 30%。',
  },
];

export const ITEM_MAP = Object.fromEntries(ITEMS.map((i) => [i.id, i]));
export const getItem = (id) => ITEM_MAP[id];

/** 道具稀有度：按价格粗分，用于商店权重 */
export function itemTier(item) {
  if (item.price >= 28) return 3;
  if (item.price >= 20) return 2;
  return 1;
}

export const ITEM_TIER_COLORS = { 1: '#9aa3bb', 2: '#5fd3e8', 3: '#b794f6' };