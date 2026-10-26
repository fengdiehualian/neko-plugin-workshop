// ============================================================
// 流派（职业）与商店加权
//
// 用户第四轮反馈：
//   "增加角色职业武器刷新率，我看你给每个流派都弄了一个角色，
//    但是不太容易获得她们的武器，刷新率和职业道具改到 120%。
//    高于其他武器道具一些方便玩家玩某个流派。"
//
// 做法：给武器/道具打「流派」标签，给角色打「偏好流派」标签，
// 商店抽商品时把匹配的权重乘 SHOP_ARCH_BONUS（1.2 = 120%）。
//
// 为什么不直接把 arch 写进 weapons.js / items.js 的每个条目：
//   那要改 54 个对象字面量，容易漏、也容易把已有数值改坏。
//   集中成一张映射表，一眼能看出「哪些 id 还没归类」，
//   而且 extensibility-check 可以直接断言覆盖率。
// ============================================================

/** 流派显示名 */
export const ARCH_LABEL = {
  sweep:  '横扫吸血',
  quake:  '重击',
  crit:   '暴击',
  ranged: '远程',
  homing: '元素·追踪',
  chain:  '元素·连锁',
  turret: '炮台',
  tank:   '生存',
  damage: '通用输出',
  luck:   '幸运经济',
  speed:  '机动',
};

/** 商店里「本命流派」商品的权重倍率（用户要求 120%） */
export const SHOP_ARCH_BONUS = 1.2;

/**
 * 武器 id → 流派。
 * 按 weapons.js 头部的流派树整理，一条线一个流派。
 */
export const WEAPON_ARCH = {
  // 近战·横扫吸血
  cat_paw: 'sweep',
  claw_glove: 'sweep',
  tuna_blade: 'sweep',
  fish_bone_scythe: 'sweep',
  void_scythe: 'sweep',
  // 近战·重击
  fish_shield: 'quake',
  mouse_toy: 'quake',
  iron_hammer: 'quake',
  cat_king_hammer: 'quake',
  // 近战·暴击
  shadow_dagger: 'crit',
  crit_lance: 'crit',
  thousand_claw: 'crit',
  nine_lives_claw: 'crit',
  // 远程
  pebble_toss: 'ranged',
  yarn_thrower: 'ranged',
  laser_pointer: 'ranged',
  meow_cannon: 'ranged',
  // 元素·追踪
  star_wand: 'homing',
  bubble_gun: 'homing',
  meteor_paw: 'homing',
  // 元素·连锁
  lucky_coin: 'chain',
  bell_chime: 'chain',
  chain_bell: 'chain',
  thunder_god_bell: 'chain',
  // 炮台
  toy_turret: 'turret',
  cardboard_turret: 'turret',
  cat_ear_turret: 'turret',
  meow_fortress: 'turret',
};

/**
 * 道具 id → 流派。
 * 一件道具只归一个流派（决定它受哪个角色的加权）。
 */
export const ITEM_ARCH = {
  // 生存
  warm_milk: 'tank',
  cat_armor: 'tank',
  thick_fur: 'tank',
  healing_herb: 'tank',
  steel_plate: 'tank',
  // 通用输出
  sharp_fang: 'damage',
  catnip: 'damage',
  glass_cannon: 'damage',
  cursed_collar: 'damage',
  blood_pact: 'damage',
  berserk_pill: 'damage',
  // 分路输出
  scope_lens: 'ranged',
  // 第一百一十五轮：穿甲弹 —— 远程/元素/炮台穿透，归远程流派
  ap_round: 'ranged',
  iron_knuckle: 'sweep',
  magic_gem: 'homing',
  crit_eye: 'crit',
  // 第四十九轮：芙兰的魔剑 —— 纯输出（最终乘算通道）
  laevatein: 'damage',
  // 第一百四十一轮：维吉尔的阎魔刀 —— 次元斩清场，归输出流派
  yamato: 'damage',
  // 第一百三十四轮：Boss 专属掉落红品（8 件）—— round4 要求每件道具
  // 都有流派标签；按效果主轴归档。
  dried_fish: 'damage',        // 全属性 +1：泛用成长，归输出
  lobster_pillow: 'damage',    // 吸血转伤害：吸血输出流
  smart_wand: 'ranged',        // 远程追踪 + 射程
  dr_m_notes: 'tank',          // 承伤 -20% + 受击反伤
  lucky_cat_paw: 'crit',       // 暴击再暴击
  black_cape: 'tank',          // 闪避生存
  cat_taffy: 'tank',           // 回复生存
  overload_chip: 'damage',     // 易伤叠层：输出
  // 炮台
  gear_oil: 'turret',
  radar_chip: 'turret',
  turret_blueprint: 'turret',
  // 幸运经济
  lucky_bell: 'luck',
  shiny_coin: 'luck',
  magnet_paw: 'luck',
  four_leaf: 'luck',
  lucky_cat: 'luck',
  greed_idol: 'luck',
  // 机动
  running_shoes: 'speed',
  feather: 'speed',
};

/**
 * 角色 id → 本命流派列表。
 *
 * 依据是每个角色的**初始武器所属流派**，再加上符合人设的第二流派：
 *   yui  猫爪        → 横扫；均衡型所以也给远程
 *   mimi 利爪手套    → 横扫；狂战型所以给暴击
 *   luna 星星魔杖    → 元素追踪；法师所以两条元素线都要
 *   shiro 咸鱼盾     → 重击；坦克所以配炮台（站着打）
 *   nana 幸运硬币    → 元素连锁；幸运型所以配幸运经济
 *   kuro 影刃        → 暴击；刺客所以配远程（激光笔那条线）
 *   youyi 咸鱼盾     → 重击；支援型/治疗所以配生存（站着扛、慢慢耗）
 *   neon_party 玩具炮台 → 炮台；双炮台本命，1.44 倍商店权重（唯一一个）
 *
 * 注意：`damage` / `tank` / `speed` 这类通用道具**不算任何角色的本命**，
 * 所以它们对所有角色都是 100% 权重，不会被某个角色独占。
 */
export const CHARACTER_ARCH = {
  yui: ['sweep', 'ranged'],
  mimi: ['sweep', 'crit'],
  luna: ['homing', 'chain'],
  shiro: ['quake', 'turret'],
  nana: ['chain', 'luck'],
  kuro: ['crit', 'ranged'],
  // 第十一轮新增的悠怡洛丽塔。初始武器是咸鱼盾（quake），
  // 第二流派选 tank —— 人设是"温婉柔弱但很会照顾人"，
  // 数值上也刻意做成了偏续航、输出吃亏，所以本命给生存而不是输出线。
  youyi: ['quake', 'tank'],
  // 第十三轮新增的 Neon（原"neon宴会"）。初始武器是玩具炮台（turret）。
  // **两个槽都给 turret**，这是全场唯一一个双炮台本命 ——
  // 商店权重 1.2×1.2 = 1.44 倍（SHOP_ARCH_BONUS 见文件开头），
  // 别的角色最多 1.2 倍。用户要的就是"擅长炮台"，这是最直接的表达。
  neon_party: ['turret', 'turret'],
};

/** 取武器流派 */
export function weaponArch(def) {
  return WEAPON_ARCH[def?.id] || null;
}

/** 取道具流派 */
export function itemArch(def) {
  return ITEM_ARCH[def?.id] || null;
}

/** 取角色的本命流派列表 */
export function characterArch(characterId) {
  return CHARACTER_ARCH[characterId] || [];
}

/**
 * 商店权重倍率：商品流派命中玩家本命流派时返回 SHOP_ARCH_BONUS，否则 1。
 *
 * @param {string|null} arch       商品的流派
 * @param {string[]}    playerArch 玩家的本命流派列表
 */
export function archWeightMul(arch, playerArch) {
  if (!arch || !playerArch || playerArch.length === 0) return 1;
  return playerArch.includes(arch) ? SHOP_ARCH_BONUS : 1;
}
