// ============================================================
// 难度设置
//
// 每个难度是一组乘数，作用在不同环节：
//   enemyHp      敌人血量倍率
//   enemyDamage  敌人伤害倍率
//   enemyCount   每波刷怪预算倍率（数量）
//   enemySpeed   敌人移速倍率
//   enemyFireRate 敌人（弹幕类小怪）开火频率倍率；Boss 不吃这个
//   bulletCount  Boss 弹幕条数倍率（噩梦会更密，偏弹幕游戏）
//   bulletSpeed  Boss 弹幕速度倍率
//   materialMul  材料掉落倍率（难度越高给得越多，作为补偿）
//   xpMul        经验倍率
//
// 所有倍率都只做"乘法叠加"，不改敌人表本身，方便调参。
//
// 数值调整史：
//   用户反馈"地图偏大，敌人整体数量略少" → 所有难度 enemyCount **整体 +20%**。
//   这里直接写进基础倍率（而不是在 world 里再乘一次），
//   这样难度表本身就是"最终数值"，看表就知道实际刷多少。
// ============================================================

/** 敌人数量整体上调系数（用户要求 +20%）；改这里一处即可全局生效 */
export const ENEMY_COUNT_BUFF = 1.2;

// ---- 第五轮：整体难度偏简单，按用户要求再抬一档 ----
// 用户原话："目前难度整体简单，即便是噩梦难度也轻轻松松，
// 比原版土豆兄弟的难度 5 可能还简单的多，我的预期是噩梦难度像
// 土豆兄弟的难度 6，像他的噩梦一样难，你可以试试把所有难度的怪的
// 血量再提高 20%，噩梦难度提高 40% 看看，然后噩梦难度刷怪再加 20% 看看。"
//
// 所以：
//   - 所有难度 enemyHp × 1.2
//   - 噩梦额外再抬（合计 ×1.4）
//   - 噩梦 enemyCount 再 +20%
// 写成具名常量，之后要再调只改这两个数。
export const HP_BUFF_ALL = 1.2;        // 全难度血量 +20%
export const HP_BUFF_NIGHTMARE = 1.4;  // 噩梦血量改为 +40%（覆盖上面的 1.2）

// ---- 第七轮 ----
// 用户原话："噩梦难度现在有挑战性了，小怪数量再加10%"
// 1.2 → 1.32（×1.1）
export const COUNT_BUFF_NIGHTMARE = 1.32;

// ---- 第十五轮 ① ----
// 用户原话："给除了噩梦之外的难度的初始刷怪数值乘以1.5，这三个难度的怪
//            少的可怜。"
//
// 只作用于简单 / 中等 / 困难，**噩梦完全不动** —— 用户明确把它排除在外
// （噩梦本来就已经是 1.75 × 1.2 × 1.32 ≈ 2.77 倍预算，再多就变纯弹幕地狱了）。
//
// 作用点是 enemyCount，也就是每波的**刷怪预算**（budgetTotal =
// cfg.budget × enemyCount × spawnRateMul）。预算同时决定总数和刷怪间隔
// （见 world.startWave 的 spawnInterval），所以乘上去是"怪更多、来得也更密"，
// 正好对应"少的可怜"这个体感。
export const NON_NIGHTMARE_COUNT_BUFF = 1.5;

/** 敌人开火频率倍率（第七轮）。用户："弹幕类型的敌人射击频率加快30%（boss不用加）"。
 *  只在噩梦生效；实现处是 entities/enemy.js 的 _shooter()，Boss 走 _boss() 不受影响。 */
export const FIRE_RATE_NIGHTMARE = 1.3;

// ---- 第十轮：噩梦移速与中期血量 ----
//
// 用户原话："噩梦难度所有敌人移速+10%。近战敌人则是改成+15%
//            （但是不要超过玩家的默认100%移速，如果玩家买了扣移速的道具
//             被怪追着打那是他自己的抉择问题）然后第11回合开始所有敌人
//            hp上限+20%，因为第十波打完之后还是会很富，加点血方便玩家
//            无法秒杀他们。"
//
// 三个要点：
//   1. 噩梦全体移速 ×1.10
//   2. 近战敌人改成 ×1.15（**替代** 1.10，不是叠加）
//   3. 移速曾有固定常数封顶（第十轮=玩家默认 100%；第十九轮玩家提到 215
//      后=其 93%）—— 第十九轮末上限整个移除（见下面的墓碑说明）

/** 噩梦全体敌人移速倍率（×1.10） */
export const SPEED_BUFF_NIGHTMARE = 1.10;

/**
 * 噩梦**近战**敌人移速倍率（×1.15）。
 *
 * 注意是**替代** SPEED_BUFF_NIGHTMARE 而不是在它基础上再乘 ——
 * 用户说的是"近战敌人则是改成+15%"，"改成"就是换一个值。
 * 叠乘会变成 ×1.265，明显超出用户要的幅度。
 */
export const SPEED_BUFF_NIGHTMARE_MELEE = 1.15;

// 第十九轮末：**敌人移速绝对上限已移除**（墓碑说明）。
//
// 原 ENEMY_SPEED_CAP = 200（第十轮起就是"玩家默认移速"的同义词），
// 本义是"敌人不超过玩家默认移速"。用户原话：
//   "移除敌人速度上限的设置，因为玩家后续发育之后移动速度也会更快。
//    如果后续敌人移速超过215（我们的新默认速度）导致玩家被围攻，
//    那也是玩家不愿意升级速度的原因，我们一开始的数值已经明显低于玩家了。"
// 即把"保护不升速的玩家"从硬规则降级为玩家自己的抉择：敌人移速随波次
// 成长如实叠加，后期超过玩家默认 215 是被接受的设定 —— 要么升移速，
// 要么被围攻。难度表里的 enemySpeedCap 字段与 world.js / enemy.js 的
// 夹持逻辑一并删除；ENEMY_SPEED_FLOOR（120）保留不动。

/**
 * 第 11 波（0-based 索引 10）起，敌人血量上限额外 +20%。
 *
 * 用户理由："因为第十波打完之后还是会很富，加点血方便玩家无法秒杀他们。"
 *
 * 作用范围：**只在噩梦**。用户这一整条都是在对噩梦调参（"噩梦难度所有敌人
 * 移速+10%"开头），而且"第 11 回合开始所有敌人 hp 上限 +20%"里的"所有敌人"
 * 是在跟前面的"近战敌人"对照（全体都要加，不是只有近战），不是在说
 * "所有难度"。想让其他难度也吃这个加成，把 WAVE11_HP_BUFF_ALL 改成 true 即可。
 */
export const WAVE11_HP_BUFF = 1.20;
/** 第 11 波起血量加成的起始波次（0-based；10 = 第 11 波） */
export const WAVE11_HP_BUFF_FROM = 10;
/** 是否把上面这条血量加成推广到全部难度（用户没说，默认只在噩梦生效） */
export const WAVE11_HP_BUFF_ALL = false;

// ---- 第十二轮：中后期小怪被秒杀的专项调整 ----
//
// 用户原话："让敌人的远程怪射击频率+20%，弹幕速度+20%（不用改boss），
//            近战怪基础血量再统一+20%。目前发育好的情况下5-6回合开始
//            就容易把小怪们秒杀了。所以我们这次加基础血量，这样后续
//            难度波次放大多倍之后也会肉一些。"
//
// 为什么单独拆出这三个常量，而不是直接改难度表：
//   这三个是**全局**调整（用户没提难度），而难度表里每一项都是
//   "相对于标准的倍率"。混进去会让"噩梦比中等硬多少"变得看不出来。
//   拆成具名常量之后，以后要调只改这里一个数，而且
//   test/difficulty-tuning.mjs 可以直接断言这三个值。
//
// 注意这三条**都不作用于 Boss**：
//   - 射速/弹速：只加在 _firePattern 里，而它只被 _shooter / _chaseShooter
//     调用；Boss 走 _boss() / _meleeBoss()，各自直接生成弹幕，不经过这里。
//   - 近战血量：Boss 的 isMeleeEnemy() 直接返回 false（Boss 的血量是
//     单独一条曲线，见 waves.js 的 bossGrowth）。

/**
 * 远程敌人的射击频率加成（= 攻击冷却 ÷ 本值）。
 *
 * 两次叠乘史：
 *  - 第三轮：+20%（用户"远程敌人攻速 +20%"）→ 1.2；
 *  - 第二十轮：再 +20%（用户："给所有难度的敌人的弹幕射击频率+20%，
 *    目前射击频率太低了构不成威胁"）→ 1.2 × 1.2 = 1.44。
 *
 * 和第七轮的 FIRE_RATE_NIGHTMARE（只在噩梦 ×1.3）仍是**相乘**关系：
 * 噩梦的弹幕怪最终射速是 1.3 × 1.44 = 1.872 倍。
 * 这层对**所有难度**生效；Boss 不吃（走 _boss / _meleeBoss 路径）。
 */
export const ENEMY_FIRE_RATE_BUFF = 1.44;

/** 远程敌人的弹幕速度 +20%（同样不含 Boss） */
export const ENEMY_BULLET_SPEED_BUFF = 1.2;

/**
 * 近战敌人的**基础**血量 +20%（不含 Boss）。
 *
 * "基础"血量 = 直接乘在 def.hp 上，所以后续波次倍率、难度倍率、
 * 无限模式 ×2 都是在这 +20% 之上继续乘 —— 这正是用户要的
 * "这样后续难度波次放大多倍之后也会肉一些"。
 *
 * 只给近战：用户是拿"远程怪"和"近战怪"对照着说的，
 * 远程那条他给的是射速和弹速，血没动。所以远程怪的血量保持原样。
 */
export const ENEMY_MELEE_HP_BUFF = 1.2;


export const DIFFICULTY = {
  easy: {
    id: 'easy',
    name: '简单',
    tag: '轻松',
    color: '#6ee7a8',
    desc: '敌人更少、更软、更慢。适合熟悉玩法，或者只想和猫娘聊天。',
    enemyHp: 0.72 * HP_BUFF_ALL,
    enemyDamage: 0.6,
    enemyCount: 0.7 * ENEMY_COUNT_BUFF * NON_NIGHTMARE_COUNT_BUFF,   // 1.26
    enemySpeed: 0.9,
    enemyFireRate: 1,
    bulletCount: 0.6,
    bulletSpeed: 0.85,
    materialMul: 1.25,
    xpMul: 1.15,
  },
  normal: {
    id: 'normal',
    name: '中等',
    tag: '标准',
    color: '#5fd3e8',
    // 第一百一十七轮：描述按用户口吻重写 —— "普通改成：生存20波，
    // 难度较低，适合休闲。"
    desc: '生存20波，难度较低，适合休闲。',
    enemyHp: 1 * HP_BUFF_ALL,
    enemyDamage: 1,
    enemyCount: 1 * ENEMY_COUNT_BUFF * NON_NIGHTMARE_COUNT_BUFF,     // 1.8
    enemySpeed: 1,
    enemyFireRate: 1,
    bulletCount: 1,
    bulletSpeed: 1,
    materialMul: 1,
    xpMul: 1,
  },
  hard: {
    id: 'hard',
    name: '困难',
    tag: '硬核',
    color: '#ffc857',
    // 第一百一十七轮："困难改成：敌人属性上升，数量变多了，略有挑战
    // 性的难度~"
    desc: '敌人属性上升，数量变多了，略有挑战性的难度~',
    enemyHp: 1.45 * HP_BUFF_ALL,
    enemyDamage: 1.5,
    enemyCount: 1.35 * ENEMY_COUNT_BUFF * NON_NIGHTMARE_COUNT_BUFF,  // 2.43
    enemySpeed: 1.05,
    enemyFireRate: 1,
    bulletCount: 1.3,
    bulletSpeed: 1.1,
    materialMul: 1.15,
    xpMul: 1.1,
  },
  nightmare: {
    id: 'nightmare',
    name: '噩梦',
    tag: '弹幕',
    color: '#ff5f6d',
    // 第一百一十七轮："噩梦改成：游戏会变成弹幕游戏喵~还有特别的家伙
    // 等着你哦~"（"特别的家伙" = 噩梦 20 波通关且重开不超 3 次现身的
    // 隐藏 Boss，见 waves.js / enemies.js boss_void_lord）
    desc: '游戏会变成弹幕游戏喵~还有特别的家伙等着你哦~',
    // 用户把噩梦单独拎出来说了 +40%，所以这里用 HP_BUFF_NIGHTMARE
    // 而**不是**再叠一层 HP_BUFF_ALL（那样会变成 +68%，超出预期）
    enemyHp: 2.0 * HP_BUFF_NIGHTMARE,
    enemyDamage: 2.1,
    enemyCount: 1.75 * ENEMY_COUNT_BUFF * COUNT_BUFF_NIGHTMARE,
    enemySpeed: 1.22 * SPEED_BUFF_NIGHTMARE,
    // 近战敌人吃更高一档（见 SPEED_BUFF_NIGHTMARE_MELEE 的说明）。
    // 其他难度不设这个字段 —— world 里没配就退回 enemySpeed，
    // 所以简单/中等/困难的敌人移速**完全没变**。
    enemySpeedMelee: 1.22 * SPEED_BUFF_NIGHTMARE_MELEE,
    // 第十九轮末：敌人移速绝对上限（enemySpeedCap）已随 ENEMY_SPEED_CAP
    // 一起移除 —— 敌人移速随成长如实叠加，不再封顶（见文件上部墓碑说明）。
    // 第七轮：弹幕类小怪开火频率 +30%（Boss 走 _boss()，不受影响）
    enemyFireRate: FIRE_RATE_NIGHTMARE,
    bulletCount: 1.9,
    bulletSpeed: 1.28,
    materialMul: 1.4,
    xpMul: 1.25,
  },
};

/** 展示顺序 */
export const DIFFICULTY_ORDER = ['easy', 'normal', 'hard', 'nightmare'];

// ---- 第一百三十二轮：波次真实伤害附加 ----
//
// 用户设计："从第 6 波开始，敌人的所有伤害（接触、弹幕）都附加真实伤害。
//   6-10 波：小怪 +1，精英 +2，Boss +3；11-15 波：+2/+4/+6；
//   16-20 波：+3/+6/+9；无尽模式每回合额外 +1（21 波 +4/+7/+10，
//   22 波 +5/+8/+11，以此类推）。这部分真伤无视护甲，直接扣 HP，
//   且跟着现有的无敌帧一起结算（每次有效受击只结算 1 次附加真伤）。
//   芙兰朵露除外——芙兰战已经走 22% 最大生命值真伤。"
//
// 制裁对象很明确：中后期堆护甲/堆回血的玩家 —— 护甲乘区减不掉这部分，
// 高护甲不再是"站着让怪挠痒痒"的免死金牌。tier：1 小怪 / 2 精英 / 3 Boss。

/** 真伤从第 6 波（1-based）开始 */
export const TRUE_DMG_FROM_WAVE = 6;

/**
 * 计算某波次、某档位敌人附加的真实伤害。
 * @param {number} wave 当前波次（1-based；world.currentWave）
 * @param {number} tier 1 小怪 / 2 精英 / 3 Boss
 */
export function trueDamageBonus(wave, tier) {
  if (wave < TRUE_DMG_FROM_WAVE) return 0;
  // 阶段基值：6-10 波小怪 +1、11-15 +2、16-20 +3（精英 ×2、Boss ×3）
  const base = wave >= 16 ? 3 : wave >= 11 ? 2 : 1;
  const endless = wave > 20 ? wave - 20 : 0;   // 无尽每回合再 +1
  return base * tier + endless;
}

export const DEFAULT_DIFFICULTY = 'normal';

export function getDifficulty(id) {
  return DIFFICULTY[id] || DIFFICULTY[DEFAULT_DIFFICULTY];
}

/** 给 UI 用的列表 */
export function difficultyList() {
  return DIFFICULTY_ORDER.map((id) => DIFFICULTY[id]);
}
