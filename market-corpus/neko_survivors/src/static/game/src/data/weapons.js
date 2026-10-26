// ============================================================
// 武器定义
//
// kind: 'melee' | 'ranged' | 'element' | 'turret'
// tier: 1-4   （1 普通 / 2 精良 / 3 史诗 / 4 传说，4 只能靠合成得到）
//
// 流派设计（每条线都有自己的「史诗」和「传说」顶点，
// 不会再出现"近战玩到最后变成喵喵炮"）：
//
//   近战·横扫吸血  cat_paw ─┐
//                  claw_glove ─┴→ tuna_blade → fish_bone_scythe → void_scythe
//   近战·重击      fish_shield → mouse_toy  → iron_hammer      → cat_king_hammer
//   近战·暴击      shadow_dagger → crit_lance → thousand_claw  → nine_lives_claw
//   远程           pebble_toss → yarn_thrower → laser_pointer  → meow_cannon
//   元素·追踪      star_wand  → bubble_gun   → meteor_paw     ─┐
//   元素·连锁      lucky_coin → bell_chime   → chain_bell     ─┴→ thunder_god_bell
//   炮台           toy_turret → cardboard_turret → cat_ear_turret → meow_fortress
//
// behavior: 决定开火模式，由 weapon.js 解释
//   'sweep'    近战横扫（扇形判定，带击退）
//   'thrust'   近战突刺（细长矩形）
//   'spin'     环绕自身持续伤害
//   'quake'    近战重击（大范围冲击波 + 强击退 + 可选僵直/破甲）
//   'flurry'   多段突刺（一次向前方扇形戳出多根，暴击流专用）
//   'shot'     直线弹丸
//   'spread'   扇形多发
//   'homing'   追踪弹丸
//   'bounce'   弹跳弹丸
//   'beam'     持续光束（瞬时判定）
//   'chain'    闪电链
//   'nova'     以自身为中心的爆发环
//   'orbit'    环绕的实体弹
//   'deploy'   部署炮台（放置后自动索敌开火）
// ============================================================

export const WEAPONS = [
  // ==========================================================
  // Tier 1 · 普通
  // ==========================================================
  {
    id: 'cat_paw', name: '猫爪', tier: 1, kind: 'melee', behavior: 'sweep',
    color: '#ff7bac', glyph: '爪',
    damage: 6, cooldown: 0.85, range: 62, arc: 130, knockback: 90,
    desc: '挥出毛茸茸的猫爪，横扫身前的敌人。',
  },
  {
    id: 'claw_glove', name: '利爪手套', tier: 1, kind: 'melee', behavior: 'thrust',
    color: '#ff5f6d', glyph: '抓',
    damage: 9, cooldown: 1.0, range: 78, width: 26, knockback: 60,
    desc: '向前突刺，命中范围窄但伤害更高。',
  },
  {
    id: 'fish_shield', name: '咸鱼盾', tier: 1, kind: 'melee', behavior: 'spin',
    color: '#5fd3e8', glyph: '鱼',
    damage: 5, cooldown: 0.55, range: 54, tickRate: 0.35,
    desc: '把咸鱼抡起来绕身旋转，持续伤害贴近的敌人。',
  },
  {
    id: 'shadow_dagger', name: '影刃', tier: 1, kind: 'melee', behavior: 'thrust',
    color: '#8b93b0', glyph: '刃',
    damage: 7, cooldown: 0.5, range: 66, width: 20, knockback: 30, critBonus: 10,
    desc: '极快的短刺，暴击率额外提升。暴击流的起点。',
  },
  {
    id: 'pebble_toss', name: '丢石子', tier: 1, kind: 'ranged', behavior: 'shot',
    color: '#9aa3bb', glyph: '石',
    damage: 5, cooldown: 0.7, range: 320, speed: 460, pierce: 0,
    desc: '最朴素的远程手段。射速稳定。',
  },
  {
    id: 'star_wand', name: '星星魔杖', tier: 1, kind: 'element', behavior: 'homing',
    color: '#b794f6', glyph: '星',
    // 第四十六轮：pierce 0 → 1，可穿透一个敌人 ——
    // 用户原话："然后让星星魔杖可以穿透一个敌人。"
    //（neon 噩梦开局被近身围住时，拐弯星打前排无输出；穿透后打穿过
    // 前排还能摸到后面的追兵，配合她"离得越远伤害越高"的技能。）
    damage: 6, cooldown: 1.0, range: 300, speed: 300, homing: 3.2, pierce: 1,
    desc: '发射会拐弯追人的小星星，很难打空，还能贯穿一个敌人。',
  },
  {
    id: 'lucky_coin', name: '幸运硬币', tier: 1, kind: 'ranged', behavior: 'bounce',
    color: '#ffc857', glyph: '币',
    damage: 4, cooldown: 0.9, range: 300, speed: 420, bounces: 3,
    desc: '弹跳的硬币，能在敌人之间来回弹。',
  },
  {
    id: 'toy_turret', name: '发条炮台', tier: 1, kind: 'turret', behavior: 'deploy',
    color: '#a9b4c9', glyph: '台',
    damage: 5, cooldown: 3.4, range: 230, speed: 380,
    turretLife: 0, turretMax: 2, turretCooldown: 1.1,
    desc: '上发条就会自己转的小炮台。最多同时 2 座，会自动射附近的敌人。',
  },

  // ==========================================================
  // Tier 2 · 精良
  // ==========================================================
  {
    id: 'tuna_blade', name: '金枪鱼刀', tier: 2, kind: 'melee', behavior: 'sweep',
    color: '#7ec8e3', glyph: '鲔',
    damage: 12, cooldown: 0.9, range: 74, arc: 140, knockback: 120,
    desc: '用冻硬的金枪鱼当武器，范围与伤害兼顾。',
  },
  {
    id: 'mouse_toy', name: '逗猫棒', tier: 2, kind: 'melee', behavior: 'orbit',
    color: '#ffd166', glyph: '逗',
    damage: 7, cooldown: 0.4, range: 88, orbitCount: 1,
    // 第三十三轮：球变大一倍 + 转圈速度 +50%。
    // 用户原话："把逗猫棒那个转圈武器的那个球变大一倍，作为咸鱼盾的
    // 上位几乎打不中人。扩大一倍转圈速度+50%看看。"
    // 第三十五轮：还是菜，转速直接拉到 7.8 rad/s —— 用户："转圈速度
    // 改到7.8rad/s试试"。命中判定 orbitBallRadius = 32（16 → 32）不变。
    orbitSpeed: 7.8, orbitBallRadius: 32,
    desc: '环绕身周飞舞的大号玩具，持续撞击敌人。重击流的跳板。',
  },
  {
    id: 'crit_lance', name: '暴击长矛', tier: 2, kind: 'melee', behavior: 'thrust',
    color: '#ffd166', glyph: '矛',
    damage: 15, cooldown: 1.1, range: 118, width: 26, knockback: 70,
    critBonus: 18, critDamageBonus: 40,
    desc: '专挑要害的长矛。突刺距离远，暴击率 +18%、暴击伤害 +40%。',
  },
  {
    id: 'yarn_thrower', name: '毛线枪', tier: 2, kind: 'ranged', behavior: 'spread',
    color: '#ff9f68', glyph: '线',
    damage: 6, cooldown: 1.1, range: 280, speed: 400, shots: 3, spreadAngle: 22,
    desc: '一次射出三团毛线，覆盖面更广。',
  },
  {
    id: 'bubble_gun', name: '泡泡枪', tier: 2, kind: 'element', behavior: 'shot',
    color: '#8fd9f0', glyph: '泡',
    damage: 8, cooldown: 0.75, range: 330, speed: 380, pierce: 1,
    desc: '轻盈的泡泡，可以穿过一个敌人。',
  },
  {
    id: 'bell_chime', name: '铃铛', tier: 2, kind: 'element', behavior: 'nova',
    color: '#ffe08a', glyph: '铃',
    damage: 11, cooldown: 2.2, range: 130, knockback: 140,
    desc: '摇响铃铛，以自身为中心炸开一圈音波。',
  },
  {
    id: 'cardboard_turret', name: '纸箱炮台', tier: 2, kind: 'turret', behavior: 'deploy',
    color: '#d9b382', glyph: '箱',
    damage: 8, cooldown: 3.0, range: 270, speed: 410,
    turretLife: 0, turretMax: 3, turretCooldown: 1.0,
    desc: '把纸箱扣在地上当炮台。最多同时 3 座，自动射附近的敌人。',
  },

  // ==========================================================
  // Tier 3 · 史诗（商店最高能刷到这一层）
  // ==========================================================
  {
    id: 'fish_bone_scythe', name: '鱼骨镰', tier: 3, kind: 'melee', behavior: 'sweep',
    color: '#c9d6e8', glyph: '镰',
    damage: 22, cooldown: 1.05, range: 96, arc: 180, knockback: 180, lifesteal: 3,
    desc: '巨大的鱼骨镰刀，横扫半圈并吸取生命。',
  },
  {
    id: 'iron_hammer', name: '铁锤', tier: 3, kind: 'melee', behavior: 'quake',
    color: '#a9b4c9', glyph: '锤',
    damage: 34, cooldown: 1.9, range: 132, knockback: 300,
    shockwave: true, stun: 0.5,
    desc: '砸地引发冲击波。范围大、击退强，命中还会短暂僵直敌人。',
  },
  {
    id: 'thousand_claw', name: '千爪乱舞', tier: 3, kind: 'melee', behavior: 'flurry',
    color: '#ff5f6d', glyph: '千',
    damage: 17, cooldown: 0.95, range: 108, width: 22, knockback: 60,
    thrusts: 5, spreadAngle: 52, critBonus: 28, critDamageBonus: 60,
    desc: '一次向前方扇形戳出 5 道爪击。暴击率 +28%、暴击伤害 +60%。',
  },
  {
    id: 'laser_pointer', name: '激光笔', tier: 3, kind: 'ranged', behavior: 'beam',
    color: '#ff3b6b', glyph: '光',
    damage: 16, cooldown: 1.4, range: 420, width: 14, pierce: 99,
    desc: '一道贯穿全场的光束，命中路径上所有敌人。',
  },
  {
    id: 'meteor_paw', name: '陨石猫爪', tier: 3, kind: 'element', behavior: 'homing',
    color: '#ff7043', glyph: '陨',
    damage: 20, cooldown: 1.6, range: 380, speed: 260, homing: 4.0, pierce: 1,
    explosionRadius: 60,
    desc: '召唤燃烧的猫爪追踪敌人，命中后小范围爆炸。',
  },
  {
    id: 'chain_bell', name: '雷铃', tier: 3, kind: 'element', behavior: 'chain',
    color: '#ffe066', glyph: '雷',
    damage: 14, cooldown: 1.5, range: 300, chains: 4, chainRange: 150,
    desc: '闪电在敌人之间跳跃，最多连锁 4 次。',
  },
  {
    id: 'cat_ear_turret', name: '猫耳炮台', tier: 3, kind: 'turret', behavior: 'deploy',
    color: '#5fd3e8', glyph: '耳',
    damage: 16, cooldown: 2.4, range: 330, speed: 470, shots: 2, spreadAngle: 12,
    turretLife: 0, turretMax: 5, turretCooldown: 0.9,
    desc: '双联装猫耳炮台，一次两发。最多 5 座。',
  },

  // ==========================================================
  // Tier 4 · 传说（只能靠合成得到，每条流派的顶点）
  // ==========================================================
  {
    id: 'void_scythe', name: '虚空镰', tier: 4, kind: 'melee', behavior: 'sweep',
    color: '#b794f6', glyph: '虚',
    damage: 42, cooldown: 0.95, range: 118, arc: 220, knockback: 240, lifesteal: 6,
    desc: '切开空间的巨镰，横扫近一整圈并大量吸血。横扫吸血流的顶点。',
  },
  {
    id: 'cat_king_hammer', name: '猫王战锤', tier: 4, kind: 'melee', behavior: 'quake',
    color: '#ffb300', glyph: '王',
    damage: 78, cooldown: 2.1, range: 200, knockback: 460,
    // 第一百一十三轮：击晕 0.9 → 0.6 —— 用户："猫王战锤的击晕时间减少
    // 一些。不要砍废他，但是现在确实太强了。" 仍高于铁锤的 0.5s，
    // 重击流顶点的定位不动。
    shockwave: true, stun: 0.6, armorShred: 0.35,
    desc: '猫王的战锤。砸下时大范围冲击波，强击退、较长僵直，并削减敌人护甲。近战重击流的顶点。',
  },
  {
    id: 'nine_lives_claw', name: '九命连爪', tier: 4, kind: 'melee', behavior: 'flurry',
    color: '#ff2d55', glyph: '九',
    damage: 30, cooldown: 0.72, range: 132, width: 26, knockback: 70,
    thrusts: 9, spreadAngle: 64, critBonus: 45, critDamageBonus: 90,
    desc: '九条命换来的九连爪，向前方扇形戳出 9 道。暴击率 +45%、暴击伤害 +90%。暴击流的顶点。',
  },
  {
    id: 'meow_cannon', name: '喵喵炮', tier: 4, kind: 'ranged', behavior: 'spread',
    color: '#ff4d94', glyph: '喵',
    damage: 26, cooldown: 0.85, range: 400, speed: 520, shots: 5, spreadAngle: 34, pierce: 1,
    desc: '喵喵叫的散弹炮，五发齐射且能穿透。远程流的顶点。',
  },
  {
    id: 'thunder_god_bell', name: '雷神铃', tier: 4, kind: 'element', behavior: 'chain',
    color: '#fff176', glyph: '霆',
    damage: 30, cooldown: 1.2, range: 380, chains: 8, chainRange: 190,
    desc: '雷声滚滚，闪电连锁 8 次。元素流的顶点。',
  },
  {
    id: 'meow_fortress', name: '喵喵要塞', tier: 4, kind: 'turret', behavior: 'deploy',
    color: '#6ee7a8', glyph: '塞',
    damage: 30, cooldown: 2.0, range: 380, speed: 500, shots: 3, spreadAngle: 18, pierce: 1,
    turretLife: 0, turretMax: 8, turretCooldown: 0.85,
    desc: '三连装要塞炮台，最多 8 座，摆好阵就能挂机。炮台流的顶点。',
  },
];

export const WEAPON_MAP = Object.fromEntries(WEAPONS.map((w) => [w.id, w]));
export const getWeapon = (id) => WEAPON_MAP[id];

/**
 * 合成配方：两个相同 id 的武器 + 材料 -> 产物
 *
 * 设计原则：**每个流派的顶点是自己的专属武器**。
 * 旧版本里 tuna_blade / yarn_thrower / laser_pointer 全都会变成 meow_cannon，
 * 导致「近战玩到最后也变成远程」，所以这里按流派拆开了。
 */

// ---- 第十五轮 ② ----
// 用户原话："给所有传说武器的基础售卖价格和史诗武器合成他们的价格变贵20%，
//            也就是1.2倍价格。"
//
// 两处一起涨，因为它们说的是同一件事的两面：**拿到传说武器的总代价**。
//   1) 传说（T4）武器在商店的基础售价 —— 见 shop.js 的 _weaponPrice
//   2) 史诗（T3）武器合成传说武器的配方费 —— 见下面的 RECIPES
//
// 只动 T4 和 T3→T4 这一段：T1→T2、T2→T3 的合成费保持原样，
// 免得把前中期的节奏也一起拖慢（用户只说了"传说"和"合成他们"）。
export const LEGENDARY_PRICE_MUL = 1.2;

/** 传说（T4）武器在商店的基础售价（未乘随机浮动 / 波数涨价 / 幸运折扣） */
export const LEGENDARY_BASE_PRICE = 40 * LEGENDARY_PRICE_MUL;   // 48

export const RECIPES = [
  // ---- T1 → T2 ----
  { from: 'cat_paw',          to: 'tuna_blade',       cost: 8  },
  { from: 'claw_glove',       to: 'tuna_blade',       cost: 8  },
  { from: 'fish_shield',      to: 'mouse_toy',        cost: 10 },
  { from: 'shadow_dagger',    to: 'crit_lance',       cost: 12 },
  { from: 'pebble_toss',      to: 'yarn_thrower',     cost: 8  },
  { from: 'star_wand',        to: 'bubble_gun',       cost: 10 },
  { from: 'lucky_coin',       to: 'bell_chime',       cost: 10 },
  { from: 'toy_turret',       to: 'cardboard_turret', cost: 10 },

  // ---- T2 → T3 ----
  { from: 'tuna_blade',       to: 'fish_bone_scythe', cost: 18 },
  { from: 'mouse_toy',        to: 'iron_hammer',      cost: 20 },
  { from: 'crit_lance',       to: 'thousand_claw',    cost: 22 },
  { from: 'yarn_thrower',     to: 'laser_pointer',    cost: 18 },
  { from: 'bubble_gun',       to: 'meteor_paw',       cost: 24 },
  { from: 'bell_chime',       to: 'chain_bell',       cost: 20 },
  { from: 'cardboard_turret', to: 'cat_ear_turret',   cost: 20 },

  // ---- T3 → T4（各流派各自的传说顶点） ----
  // 第十五轮 ②：这一段整体 ×1.2（用户要求"史诗武器合成他们的价格变贵20%"）。
  // 原值：34 / 34 / 36 / 24 / 34 / 34 / 36 → ×1.2 后四舍五入。
  { from: 'fish_bone_scythe', to: 'void_scythe',      cost: 41 },
  { from: 'iron_hammer',      to: 'cat_king_hammer',  cost: 41 },
  { from: 'thousand_claw',    to: 'nine_lives_claw',  cost: 43 },
  { from: 'laser_pointer',    to: 'meow_cannon',      cost: 29 },
  { from: 'meteor_paw',       to: 'thunder_god_bell', cost: 41 },
  { from: 'chain_bell',       to: 'thunder_god_bell', cost: 41 },
  { from: 'cat_ear_turret',   to: 'meow_fortress',    cost: 43 },
];

export const RECIPE_MAP = Object.fromEntries(RECIPES.map((r) => [r.from, r]));

/** 按 tier 分组，供商店抽取 */
export function weaponsByTier(tier) {
  return WEAPONS.filter((w) => w.tier === tier);
}

export const TIER_NAMES = { 1: '普通', 2: '精良', 3: '史诗', 4: '传说' };
export const TIER_COLORS = { 1: '#9aa3bb', 2: '#5fd3e8', 3: '#b794f6', 4: '#ffc857' };