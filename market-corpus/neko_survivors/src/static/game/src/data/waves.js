// ============================================================
// 波次配置
//
// 时长（用户要求）：
//   "中后期阶段时间慢慢增加，不要每关都是 30 秒，
//    后面的关卡慢慢加到最高 60 秒，boss 关 90 秒，
//    但是击杀 boss 直接过关并且自动获得 boss 的掉落经验和金币。"
//
//   所以时长是**随波次递增**的（见 getWaveDuration）：
//     第 1 波 30s → 缓慢爬升 → 第 20 波 60s
//     Boss 关（10 / 20 波）固定 90s，但 Boss 一死立刻结算
//
// spawns: 该波会出现的敌人 id 池，weight 决定出现频率
// 难度随波数线性/指数上升，曲线在 waves.js 的 waveScaling 里统一处理。
// ============================================================

export const WAVE_COUNT = 20;

// ---- 第七轮：刷怪率随波次增长 ----
// 用户原话："然后所有难度每回合出现的怪物随波次增加涨幅也提高0.05
//            也就是20回合的时候刷怪率加了100%。之后无限模式维持。"
//
// 用 1-based 波数：第 1 波 ×1.05，第 2 波 ×1.10 …… 第 20 波 ×2.00（精确 +100%）。
// 无限模式（21 波起）**维持** ×2.00 封顶，不再继续涨 —— 否则叠上
// ENDLESS_STAT_MUL 的 ×2 会让刷怪量彻底失控。
export const SPAWN_RATE_PER_WAVE = 0.05;
export const SPAWN_RATE_MAX = 2.0;

/** 第 N 波（0-based waveIndex）的刷怪量倍率 */
export function spawnRateMul(waveIndex) {
  const wave1 = (waveIndex ?? 0) + 1;   // 转成 1-based
  return Math.min(SPAWN_RATE_MAX, 1 + wave1 * SPAWN_RATE_PER_WAVE);
}

// ---- 无限模式（用户第四轮反馈）----
// "增加无限模式，在 20 波结束通关后询问玩家是否继续，然后开始 21 波，
//  之后也是时间越来越长。21 波开始敌人属性数量增加翻倍。
//  然后改为 5 波一个 boss，boss 属性成长改为 3 倍。
//  30 波开始每轮刷新 2 个 boss，然后 35 和 40 波是 3 个 boss。"
//
// 第四十八轮（用户要求）整张表重排：
// "然后变成23，26，29，32，35，38，40回合都是boss战。29，32回合为
//  2个boss，35，38回合为3个boss，40回合为4个boss，boss不重复出现，
//  即40回合把我们目前的4个boss一起出场。然后40回合往后每回合都是
//  4个boss出场。让玩家尽量在41回合放弃继续。"
//
// 用 1-based 的"第 N 波"来定义，避免到处 +1/-1 出错。
export const ENDLESS_START_WAVE = 21;      // 第 21 波起是无限模式
export const ENDLESS_BOSS_EVERY = 5;       // （旧表，保留给外部引用）无限模式每 5 波一个 Boss
export const ENDLESS_STAT_MUL = 2;         // 21 波起敌人属性/数量翻倍
export const ENDLESS_BOSS_GROWTH = 3;      // 无限模式的 Boss 成长是 3 倍

/**
 * 第九十三轮：场上**存活敌人**的硬上限（普通怪+精英，不含宝箱怪与 Boss）。
 *
 * 用户原话："A把敌人数量上限改到合理的数量'指不会严重影响帧率和出现屏幕里
 * 都是怪玩家无法做到不受伤'的程度，具体数值你自己判定，因为目前实际情况是
 * 玩家火力除了精英怪大部分还是可以消灭的，所以其实没怎么见过1400-2500的怪
 * 在场。但是确实可以加个上限万一玩家发育不好也有活路。"
 *
 * 取值 600 的理由：
 *   · 正常游玩的场上峰值在 100~300（第十轮体检压力模拟：成型 build 在
 *     18~20 波场活 200 上下）；600 是峰值的 2~3 倍，正常局**永远不会碰到**；
 *   · 发育不良的局（体检模拟里 DPS 跟不上的最差情况）18~20 波会堆到
 *     1400~2500 —— 封在 600 让屏幕不至于糊满、帧率先于伤害崩掉；
 *   · 预算不丢：被上限挡住的额度留在 budgetUsed 里排队，场上一空出来
 *     继续刷 —— "上限+排队"，不是删怪。
 * Boss 召唤物/精英穿插不在本上限内（量小），宝箱怪不是威胁也不占额度。
 */
export const ENEMY_ALIVE_CAP = 600;

// 第四十八轮：无尽 Boss 出场计划（1-based 波号 → 该波 Boss 数量）。
// 23/26 单 Boss 热身，29/32 双人组，35/38 三人组，40 起每波全员四连。
export const ENDLESS_BOSS_PLAN = [
  { wave: 23, count: 1 }, { wave: 26, count: 1 },
  { wave: 29, count: 2 }, { wave: 32, count: 2 },
  { wave: 35, count: 3 }, { wave: 38, count: 3 },
  { wave: 40, count: 4 },
];

// 四个 Boss 的轮换池（40 波刚好全员一次：4 个出场位对 4 个 Boss）
export const ENDLESS_BOSS_IDS = [
  'boss_cat_king',      // 路过被殴打的猫娘（远程弹幕）
  'boss_void_lord',     // 黑化开发猫猫（远程弹幕+冲锋）
  'boss_acid_cat',      // 蒜蓉粉丝开背虾（近战冲刺）
  'boss_apple_cider',   // 依趣苹果醋（近战冲刺+远弹）
];

/** 无限模式下第 N 波（1-based）该出几个 Boss */
export function endlessBossCount(wave1based) {
  if (wave1based >= 40) return 4;
  const hit = ENDLESS_BOSS_PLAN.find((p) => p.wave === wave1based);
  return hit ? hit.count : 0;
}

/**
 * 第四十八轮：某波的 Boss 阵容 —— 从轮换池按"此前已出场总数"接着取。
 * 纯函数（不持有状态）：读档/重试同一波得到的阵容完全一致。
 * 波内不重复（取的是池子里**不同**的下标），40 波时此前共出场
 * 1+1+2+2+3+3 = 12 个，12 % 4 = 0 → 正好 [猫娘, 黑猫, 虾, 醋] 全员。
 */
export function endlessBossLineup(wave1based) {
  const count = endlessBossCount(wave1based);
  if (!count) return null;
  let spawnedBefore = 0;
  for (let w = ENDLESS_START_WAVE; w < wave1based; w++) {
    spawnedBefore += endlessBossCount(w);
  }
  const start = spawnedBefore % ENDLESS_BOSS_IDS.length;
  const ids = [];
  for (let i = 0; i < count; i++) {
    ids.push(ENDLESS_BOSS_IDS[(start + i) % ENDLESS_BOSS_IDS.length]);
  }
  return ids;
}

/** 第 N 波（1-based）是不是 Boss 波 */
export function isBossWave1(wave1based) {
  if (wave1based === 10 || wave1based === 20) return true;
  return endlessBossCount(wave1based) > 0;
}

export const WAVE_DURATION = 30;         // 第 1 波的基准时长
export const WAVE_DURATION_MAX = 60;     // 普通波的上限（用户要求）
export const BOSS_WAVE_DURATION = 90;    // Boss 关时长（用户要求 90s）
export const ENDLESS_DURATION_MAX = 120; // 无限模式的时长上限

export const WAVES = [
  // 1-3：教学期，只有最基础的敌人
  { spawns: [{ id: 'slime', weight: 10 }, { id: 'mouse', weight: 6 }], budget: 7 },
  { spawns: [{ id: 'slime', weight: 10 }, { id: 'mouse', weight: 8 }], budget: 9 },
  { spawns: [{ id: 'slime', weight: 8 }, { id: 'mouse', weight: 9 }, { id: 'bat', weight: 5 }], budget: 11 },

  // 4-6：引入硬壳与远程
  { spawns: [{ id: 'slime', weight: 6 }, { id: 'mouse', weight: 8 }, { id: 'bat', weight: 7 }, { id: 'crab', weight: 5 }], budget: 14 },
  { spawns: [{ id: 'mouse', weight: 7 }, { id: 'bat', weight: 7 }, { id: 'crab', weight: 7 }, { id: 'spitter', weight: 5 }], budget: 17 },
  // 第九轮：从第 6 波开始加入"高速追击"的猎杀猫（用户："让一种近战敌人移动速度
  // 稍快点，可以一直追着玩家的感觉"），以及随机蛋 —— 让远程弹幕早点变多样。
  { spawns: [{ id: 'bat', weight: 6 }, { id: 'crab', weight: 8 }, { id: 'spitter', weight: 6 }, { id: 'bomber', weight: 5 }, { id: 'stalker', weight: 4 }, { id: 'chaos_egg', weight: 4 }], budget: 20 },

  // 7-9：加入精英与高威胁单位
  { spawns: [{ id: 'crab', weight: 7 }, { id: 'spitter', weight: 7 }, { id: 'bomber', weight: 7 }, { id: 'charger', weight: 6 }, { id: 'stalker', weight: 5 }], budget: 23, elites: 1 },
  { spawns: [{ id: 'spitter', weight: 6 }, { id: 'bomber', weight: 6 }, { id: 'charger', weight: 7 }, { id: 'splitter', weight: 6 }, { id: 'chaos_egg', weight: 5 }, { id: 'spiral_cat', weight: 4 }], budget: 27, elites: 1 },
  { spawns: [{ id: 'charger', weight: 7 }, { id: 'splitter', weight: 7 }, { id: 'tank', weight: 5 }, { id: 'ghost', weight: 6 }, { id: 'blade_cat', weight: 5 }, { id: 'spiral_cat', weight: 5 }], budget: 31, elites: 1 },

  // 10：第一个 Boss
  {
    spawns: [{ id: 'slime', weight: 6 }, { id: 'bomber', weight: 5 }, { id: 'ghost', weight: 5 }, { id: 'stalker', weight: 4 }],
    budget: 22, boss: 'boss_cat_king', isBoss: true, duration: BOSS_WAVE_DURATION,
  },

  // 11-14：Boss 后的压力回升
  { spawns: [{ id: 'spitter', weight: 6 }, { id: 'charger', weight: 7 }, { id: 'splitter', weight: 6 }, { id: 'tank', weight: 6 }, { id: 'blade_cat', weight: 6 }, { id: 'chaos_egg', weight: 5 }], budget: 34, elites: 2 },
  { spawns: [{ id: 'bomber', weight: 6 }, { id: 'charger', weight: 7 }, { id: 'ghost', weight: 7 }, { id: 'tank', weight: 6 }, { id: 'spiral_cat', weight: 6 }, { id: 'stalker', weight: 6 }], budget: 37, elites: 2 },
  { spawns: [{ id: 'splitter', weight: 7 }, { id: 'ghost', weight: 7 }, { id: 'tank', weight: 7 }, { id: 'spitter', weight: 6 }, { id: 'blade_cat', weight: 6 }, { id: 'chaos_egg', weight: 6 }], budget: 40, elites: 2 },
  { spawns: [{ id: 'charger', weight: 7 }, { id: 'ghost', weight: 7 }, { id: 'tank', weight: 8 }, { id: 'bomber', weight: 6 }, { id: 'spiral_cat', weight: 6 }, { id: 'stalker', weight: 7 }], budget: 44, elites: 3 },

  // 15-19：终盘高压
  { spawns: [{ id: 'ghost', weight: 7 }, { id: 'tank', weight: 8 }, { id: 'splitter', weight: 7 }, { id: 'charger', weight: 7 }, { id: 'blade_cat', weight: 7 }, { id: 'stalker', weight: 7 }], budget: 48, elites: 3 },
  { spawns: [{ id: 'tank', weight: 8 }, { id: 'ghost', weight: 8 }, { id: 'charger', weight: 7 }, { id: 'spitter', weight: 6 }, { id: 'spiral_cat', weight: 7 }, { id: 'chaos_egg', weight: 7 }], budget: 52, elites: 3 },
  { spawns: [{ id: 'tank', weight: 9 }, { id: 'ghost', weight: 8 }, { id: 'splitter', weight: 7 }, { id: 'bomber', weight: 7 }, { id: 'blade_cat', weight: 8 }, { id: 'stalker', weight: 8 }], budget: 57, elites: 4 },
  { spawns: [{ id: 'tank', weight: 9 }, { id: 'ghost', weight: 9 }, { id: 'charger', weight: 8 }, { id: 'splitter', weight: 7 }, { id: 'spiral_cat', weight: 8 }, { id: 'blade_cat', weight: 8 }], budget: 62, elites: 4 },
  { spawns: [{ id: 'tank', weight: 10 }, { id: 'ghost', weight: 9 }, { id: 'charger', weight: 9 }, { id: 'bomber', weight: 8 }, { id: 'stalker', weight: 9 }, { id: 'chaos_egg', weight: 8 }], budget: 68, elites: 5 },

  // 20：最终 Boss
  {
    spawns: [{ id: 'ghost', weight: 7 }, { id: 'tank', weight: 7 }, { id: 'charger', weight: 6 }, { id: 'blade_cat', weight: 6 }, { id: 'spiral_cat', weight: 6 }],
    budget: 30, boss: 'boss_void_lord', isBoss: true, duration: BOSS_WAVE_DURATION,
  },
];

/**
 * 普通敌人血量每波复利成长率。
 *
 * 第九轮用户要求"每回合加 10% 左右"，当时取 0.10。
 * 第十四轮用户反馈"噩梦 19 波 2800 伤害的远程武器还是会秒杀大部分怪物"，
 * 并给出反推目标："让 19 波最肉的怪物血量达到 30000"。
 *
 * 反解（噩梦难度、第 19 波，w = 18）：
 *   最肉的怪是 elite_crab —— 基础 160，近战所以还吃一档近战血量加成(1.2)
 *   最终血量 = 160 × 1.2(近战) × 2.8(噩梦) × 1.2(第 11 波起的额外 buff) × 曲线(18)
 *            = 645.12 × 曲线(18)
 *   令其 = 30000 → 曲线(18) = 46.5030 → 每波复利 = 46.5030^(1/18) − 1
 *
 * 旧值 0.10 只让曲线(18) = 5.56（elite_crab 只有 3587 血），
 * 正好就是用户说的"2800 伤害还是能秒"。
 *
 * 曲线第 1 波仍然是 1.000 —— 用户明确说"噩梦难度开局的难度合格了"，
 * 所以开局那几波不能被这条改动带高。四档难度共用这一条曲线，
 * 于是"同样的成长速度"自动等比例作用到简单/普通/困难/噩梦上。
 *
 * 第二十一轮：整体再 ×1.2（用户："给所有难度敌人每回合的生命成长再
 * +20%，包含boss。玩家的发育数值还是挺足的。我不希望玩家可以在任何
 * 回合秒杀除了最简单敌人之外的敌人"）。每波复利 23.78% → 28.53%；
 * 原锚点（19 波最肉 30000 / 20 波 Boss 10 万）相应上移
 * （≈ 5.9 万 / ≈ 16.1 万，精确值以 test/round21-check.mjs 的公式对账为准）。
 */
export const HP_GROWTH_PER_WAVE = 0.23776392063804086 * 1.2;

/**
 * Boss 血量每波复利成长率（第十四轮新增）。
 *
 * 用户："20 波的噩梦 boss 血量到 10 万，不然纯靠锁血撑 15 秒就死了"。
 *
 * 反解（噩梦难度、第 20 波，w = 19）：
 *   这一波的 Boss 是 boss_void_lord —— 基础 2250（Boss 不吃近战 buff）
 *   最终血量 = 2250 × 2.8(噩梦) × 1.2(第 11 波起) × 曲线(19)
 *            = 7560 × 曲线(19)
 *   令其 = 100000 → 曲线(19) = 13.2275 → 每波复利 = 13.2275^(1/19) − 1
 *
 * 为什么 Boss 要单独一条**更缓**的曲线：
 *   Boss 的基础血量本来就比小怪高一个数量级（void_lord 2250 vs elite_crab 160）。
 *   如果 Boss 也吃普通怪那条 23.78% 的复利，第 20 波会有 43.5 万血 ——
 *   是用户点名要的 10 万的 4 倍多，Boss 关会变成打不动的木桩。
 *   拆成两条之后，"Boss ≈ 2.5 倍最肉的精英"这个比例在第 10 / 20 波都成立：
 *     第 10 波：cat_king 930 × 3.398 = 3161   vs elite_crab 160×1.2×6.819 = 1309  → 2.4×
 *     第 20 波：void_lord 2250 × 13.2275 = 29762 vs elite_crab 160×1.2×57.560 = 11051 → 2.7×
 *
 * 第二十一轮：同样 ×1.2（与普通怪曲线同批的用户要求，"包含boss"）。
 * 每波复利 14.56% → 17.47%；第 20 波 Boss 锚点 10 万 → ≈16.1 万。
 */
export const BOSS_HP_GROWTH_PER_WAVE = 0.1455793292535872 * 1.2;

/** Boss 血量曲线（1~20 波用；和普通怪曲线一样从第 1 波的 1.0 起步） */
export function bossHpCurve(w) {
  return Math.pow(1 + BOSS_HP_GROWTH_PER_WAVE, w);
}

/** 第九轮那版复利成长率。留着只为了推导下面的 ENDLESS_HP_CARRY。 */
const OLD_HP_GROWTH_PER_WAVE = 0.10;

// ---- 第十五轮 ⑥ ----
// 用户原话："给所有难度的敌人攻击成长数值变为现在的1.2倍，即便是噩梦难度
//            第五波往后玩家还是可以抗伤攻击敌人，伤害只有初期会很痛。"
//
// 这是**成长率**乘 1.2，不是最终伤害乘 1.2：
//   旧：damage(w) = 1 + w × 0.075   → 第 20 波 2.50 倍
//   新：damage(w) = 1 + w × 0.09    → 第 20 波 2.80 倍
// 第 1 波（w=0）两种写法都是 1.0，所以**开局强度完全不变** ——
// 这正好对上用户说的"伤害只有初期会很痛"（初期本来就痛，不用再加）。
//
// 它作用在共享曲线上，所以 4 个难度的 enemyDamage 倍率（0.6/1/1.5/2.1）
// 会把这个比例原样带到各自难度 —— 即"所有难度"都 ×1.2。
export const ENEMY_DAMAGE_GROWTH_PER_WAVE = 0.12;

/**
 * 敌人伤害每波成长率。
 *
 * 第二十六轮 0.09 → 0.20 → 第二十七轮 0.20 → 0.15 →
 * 第二十九轮 0.15 → 0.12：
 * 用户："那现在让弹幕吃加成，然后把伤害成长曲线从0.15改到0.12"。
 * 弹幕接入成长通道后**全通道**变相放大（接触+弹幕+水柱同一乘区），
 * 所以曲线再压一档。第 20 波曲线 = 1 + 19×0.12 = 3.28 倍
 * （噩梦 ×2.1 = 6.888 倍总乘区）。第 1 波仍是 1.0（开局不变）。
 * 曲线是共享的，普通怪/精英/Boss 的接触与弹幕同曲线放大。
 */

/**
 * 无限模式（21 波起）的**额外**伤害成长率。
 *
 * 第二十六轮 0.072 → 0.16、第二十七轮 0.16 → 0.12：始终保持主曲线
 * "约 80%"的原比例（0.06/0.075 = 0.8）。它是叠在
 * ENEMY_DAMAGE_GROWTH_PER_WAVE 之上的第二段曲线，也是"敌人攻击成长"
 * 的一部分，所以跟着一起涨 —— 否则无限模式的伤害成长会比 1~20 波
 * 明显偏软，接续处会出现手感断层。
 */
export const ENDLESS_DAMAGE_GROWTH_PER_WAVE = 0.06 * (0.12 / 0.075);  // 0.096

/**
 * 精英 / Boss 伤害的独立乘区（第二十八轮新增，第二十九轮起弹幕同样吃）。
 *
 * 用户原话（二十八轮）："将噩梦难度的boss20波伤害再降低一些。将最高伤害
 * 的依趣苹果醋的碰触伤害从252.3改到80，然后反推回去算出其他伤害……
 * 将噩梦难度精英怪的最高伤害改到75……"
 * 用户原话（二十九轮）："把弹幕伤害成长做好了……精英怪上限锚点改到65，
 * BOSS上限锚点改到70。"
 *
 * 反推（噩梦第 20 波：曲线 3.28 × 噩梦 2.1 = 6.888 倍总乘区）：
 *   Boss   上限：苹果醋 31.2 → 31.2×6.888 = 214.9 ÷ 70 → ≈ 0.3257
 *   精英   上限：蟹将   12   → 12×6.888   = 82.66 ÷ 65 → ≈ 0.7864
 *   反推回去的其他接触伤害（噩梦 20 波）：
 *     Boss   猫娘 43.1 / 虾 53.9 / 黑化开发猫猫 59.2 / 苹果醋 70.0
 *     精英   毒液女王 43.3 / 巨型蟹将 65.0
 *   弹幕同乘区后（噩梦 20 波）：小怪弹幕 27.6~62.0、Boss 弹幕 26.9~37.7、
 *   水柱同样乘 —— "有时疼有时不疼"的断层消失。
 * 小怪不吃这两个乘区。乘区随曲线一起进无限模式。
 */
export const ELITE_DAMAGE_MUL = 0.7864;
export const BOSS_DAMAGE_MUL = 0.3257;

/**
 * 敌人弹幕**射程**的每波成长率（第三十轮新增）。
 *
 * 用户原话："弹幕怪的弹幕都消失太快了，看一下射程是不是太低了，可以弄个
 * 射程成长。让所有弹幕怪的弹幕在20波的时候能飞一个屏幕长。所有难度通用。
 * 毕竟低难度弹幕也不疼。"
 *
 * 锚点：屏幕长取本作整屏可视宽 1280px（char-skills 里"一个屏幕的距离"
 * = 640px 可视半径，整屏长就是 2×640）。弹幕默认射程 520（def 没有
 * projectileRange 时的兜底值）——
 *   520 × 曲线(19) ≥ 1280 → 曲线 ≥ 2.462 → k = 0.08
 *   （第 20 波 = 1 + 19×0.08 = 2.52，520 → 1310px ✓ 恰好飞过整屏）。
 * 第 1 波仍是 1.0（开局手感不变）；**不乘难度倍率**——射程是空间属性，
 * 四档难度通用（用户："所有难度通用"）；无限模式同一条线性曲线继续走
 * （第 25 波 2.92 倍 ≈ 1520px，仍在"出屏消散"的量级，不会滚雪球）。
 * Boss 的 900/1140 同乘——它们本来就超过一屏，成长后只是不再提前消散。
 * 水柱激光（jet.jetLength）不参与：长度是预警几何的一部分。
 */
export const ENEMY_BULLET_RANGE_GROWTH_PER_WAVE = 0.08;

/**
 * 无限模式普通怪血量的"接续倍率"（第十四轮新增）。
 *
 * 为什么需要它：
 *   无限模式（21 波起）的血量是**另一套**调好的数值（旧曲线 ×2 + bossGrowth，
 *   理由见 waveScaling 里那段长注释），所以它不会跟着本轮的新复利一起涨。
 *   结果是第 20 波（新曲线 57.56）掉到第 21 波（旧曲线×2 = 8.72）——
 *   **血量凭空少 6.6 倍**，玩家一进无限模式会发现怪突然变得一碰就碎。
 *   这不是难度设计，是两套数值拼接处的断崖。
 *
 * 取值：让第 21 波的曲线值 = 第 20 波曲线值 × （第九轮时 21/20 的那个比值）。
 *   第九轮：第 20 波 = 1.10¹⁹ = 6.1159，第 21 波 = 旧曲线(20)×2 = 9.3592
 *           → 比值 1.5303（无限模式本来就该比第 20 波硬一点）
 *   于是把新曲线也拉到同一个相对位置，**只改绝对高度、不改相对走势**：
 *           CARRY = 新曲线(19) / 1.10¹⁹ = 57.5597 / 6.1159 = 9.4115
 *   校验：第 21 波 = 8.723 × 9.4115 = 82.09，第 20 波 = 57.56 → 比值 1.426，
 *   和第九轮的 1.530 基本一致（差异来自旧曲线本身也被 max() 掺了一脚）。
 *
 * 为什么 Boss **不**打这个 CARRY：
 *   Boss 的血量预算在下面那段注释里是按旧曲线算死的（35 波要 7.6k DPS）。
 *   Boss 在第 20→21 波本来就是**上涨**的（10 万 → 21 万），不存在断崖，
 *   所以没有理由去动它 —— 动了就会把 35/40 波顶到打不动。
 *
 *   第二十一轮：成长率 ×1.2 后，CARRY 的公式**自动**跟随新曲线
 *   （≈ 19.3），第 20→21 波的相对走势保持不变，不需要手工重推。
 */
export const ENDLESS_HP_CARRY = Math.pow(
  (1 + HP_GROWTH_PER_WAVE) / (1 + OLD_HP_GROWTH_PER_WAVE),
  WAVE_COUNT - 1,
);

/**
 * 第 1~20 波的原始血量曲线（保留下来当"下限"，见 waveScaling）。
 *
 * 导出是为了让测试能断言"无限模式仍然严格等于旧曲线 ×2" ——
 * 这样以后有人再动血量曲线时，会立刻发现无限模式被连带改了。
 */
export function legacyHpCurve(w) {
  return 1 + w * 0.13 + Math.pow(w, 1.25) * 0.018;
}

/** 波次难度倍率：敌人血量/伤害随波数成长 */
export function waveScaling(waveIndex) {
  const w = waveIndex; // 0-based

  // ---- 血量 ----
  // 用户第九轮反馈："敌人的生命成长再每回合加 10% 左右，
  //                  目前的中期噩梦难度的怪还是会被秒。"
  //
  // 所以改成**复利**：第 n 波 = 1.10ⁿ。
  //
  // 为什么套一层 Math.max(旧曲线, 复利曲线)：
  //   旧曲线前段更陡（第 5 波 1.62 vs 复利 1.46），复利曲线后段更陡
  //   （第 20 波 6.12 vs 旧 4.18）。直接换成复利会让**前 10 波反而变简单**，
  //   而用户要的是"更耐打"。取两者较大值就同时满足：
  //   前期不比原来软，中后期按 10%/波复利涨上去。
  //   两条都是单调递增，取 max 仍然单调递增。
  //
  // 第 20 波从 4.18 倍提到 6.12 倍（+46%），正是用户说的"中期被秒"那一段。
  const legacyHp = legacyHpCurve(w);
  const compoundHp = Math.pow(1 + HP_GROWTH_PER_WAVE, w);
  let hp = Math.max(legacyHp, compoundHp);

  // Boss 走**单独一条**曲线（第十四轮，见 BOSS_HP_GROWTH_PER_WAVE 的说明）。
  // 普通怪那条 23.78% 的复利用在 Boss 身上会把第 20 波顶到 43 万血。
  // 无限模式（21 波起）下面会把 bossHp 拉回 legacyHp，保持原有数值不变。
  let bossHp = bossHpCurve(w);

  let damage = 1 + w * ENEMY_DAMAGE_GROWTH_PER_WAVE;
  let xp = 1 + w * 0.05;
  let materials = 1 + w * 0.03;

  // ---- 无限模式（21 波起）----
  // 用户要求："21 波开始敌人属性数量增加翻倍。然后改为 5 波一个 boss，
  // boss 属性成长改为 3 倍。"
  //
  // 这是**两个独立的条件**：
  //   普通敌人 → 第 21 波起属性与数量 ×2（ENDLESS_STAT_MUL）
  //   Boss     → 成长倍率改成 3 倍（ENDLESS_BOSS_GROWTH），
  //              再按"每个 Boss 波"小幅递增，让后面的 Boss 越来越硬
  //
  // 为什么不写成「每个 Boss 波直接 ×3」的复利：
  //   第 25/30/35/40 波会变成 6.8万 / 24万 / 84万 / 285万 血，
  //   而满配 T4 六武器的实际 DPS 约 4千~3.6万，
  //   第 35 波要打 840 秒 —— 90 秒的 Boss 关在数学上就打不完。
  //   那不是难度，是坏档。用户点名了 35 / 40 波要出 3 个 Boss，
  //   说明他预期能打到那里，所以曲线必须留出可达性。
  //
  // 现在第 25/30/35/40 波的**单个** Boss 血约 9.3万 / 14.7万 / 22.9万 / 35万，
  // 对应 90 秒内需要的持续 DPS 是 1.0k / 3.3k / 7.6k / 11.7k
  // （含同时 1/2/3 个 Boss 的合计）。配合「取消属性上限 + 商店无限刷新
  // + 升级稀有度 ×4」，一个成型 build 是能推过去的，再往后才真正卡住
  // —— 无限模式本来就该有个终点，只是不该在 35 波就撞墙。
  const wave1 = w + 1;
  const isEndless = wave1 >= ENDLESS_START_WAVE;

  // 复利成长只作用在 1~20 波。
  // 无限模式（21 波起）继续用旧曲线，因为下面那套 ×2 + bossGrowth 的
  // 数值是按旧曲线算好的（第 25/30/35/40 波的血量、需要的 DPS 都写在注释里）。
  // 如果在无限模式上再叠一层复利，第 25 波的血会从 9.3 万直接翻到 18 万，
  // 用户点名要能打到的 35/40 波就会被这层复利顶到打不动。
  // 用户这次的诉求是"中期噩梦的怪被秒"，也就是 1~20 波，所以改这里就够。
  //
  // 第十四轮补充（重要，别踩）：本轮把 1~20 波的复利从 10% 提到 23.78% 之后，
  // 第 20 波的曲线值从 6.12 涨到 57.56。**无限模式不能改用这条新曲线** ——
  // 23.78% 的复利跑到第 35 波是 1406 倍，一个 void_lord 就有 1060 万血，
  // 远超注释里算好的「35 波 3 个 Boss 合计需要 7.6k DPS」预算（可用的
  // 满配 DPS 上限约 36k）。所以无限模式**保持原样**：旧曲线 ×2 + bossGrowth，
  // 再乘一个 ENDLESS_HP_CARRY 把 20→21 波那个 6.6 倍的断崖补平（见它的注释）。
  if (isEndless) hp = legacyHp * ENDLESS_HP_CARRY;

  // Boss 血量：1~20 波走单独的 bossHpCurve；
  // 无限模式回到 legacyHp（**不乘 CARRY**），复现原来 def.hp × (旧曲线×2)
  // × bossGrowth 的数值 —— Boss 在 20→21 波本来就是涨的，没有断崖要补。
  if (isEndless) bossHp = legacyHp;

  let bossGrowth = 1;
  if (isEndless) {
    const extra = wave1 - ENDLESS_START_WAVE;   // 第 21 波 = 0
    // 基础 3 倍（用户要求），之后每个 Boss 周期（5 波）再 ×1.35
    const cycles = (wave1 - WAVE_COUNT) / ENDLESS_BOSS_EVERY;
    bossGrowth = ENDLESS_BOSS_GROWTH * Math.pow(1.35, cycles);
    hp *= ENDLESS_STAT_MUL;
    // Boss 血量在无限模式下**必须**跟着 ×2 —— 因为它取代了原来
    // def.hp × hp(=旧曲线×2) 里的那个 hp。漏掉这一步会让 21 波起的
    // Boss 凭空少一半血，正是 round4-check 里断言过的数值。
    bossHp *= ENDLESS_STAT_MUL;
    damage *= 1 + extra * ENDLESS_DAMAGE_GROWTH_PER_WAVE;
    xp *= 1 + extra * 0.04;
    materials *= 1 + extra * 0.02;
  }

  return {
    // 血量成长：1~20 波 = max(旧曲线, 复利ⁿ)（见上面 compoundHp 的说明）；
    // 无限模式 = 旧曲线 ×2（ENDLESS_STAT_MUL），保持原有难度设计。
    hp,
    // Boss 专属血量曲线（第十四轮）。普通怪不吃这个字段。
    bossHp,
    // 只对 Boss 额外生效的成长倍率（普通敌人不吃）
    bossGrowth,
    // 伤害成长偏缓（否则中期就被秒）
    damage,
    // 全局移速成长：1.5%/波封顶 28%（原有）。
    speed: 1 + Math.min(0.28, w * 0.015),
    // 第十九轮（移速成长）：非 Boss 敌人每波额外 +1% **自身**速度，线性
    // 不封顶（第十九轮末敌人移速绝对上限已整个移除）。Boss 不吃这个字段，
    // 排除逻辑在 enemy.js 构造函数里按 def.boss 判，任何生成路径都逃不掉。
    speedOwn: 1 + w * 0.01,
    // 第三十轮：弹幕射程成长（全难度通用，不乘难度倍率；锚点见
    // ENEMY_BULLET_RANGE_GROWTH_PER_WAVE 的注释）。第 20 波 2.52 倍，
    // 默认射程 520 → 1310px ≥ 整屏 1280。
    bulletRange: 1 + w * ENEMY_BULLET_RANGE_GROWTH_PER_WAVE,
    xp,
    // 材料成长：从 1 + w*0.06 降到 1 + w*0.03。
    // 第 20 波 2.2 倍 → 1.6 倍，配合清场奖励封顶一起收紧中后期收入。
    materials,
  };
}

/**
 * 波次结算奖励：清完一波额外给的材料。
 *
 * 数值调整史（重要）：
 *  - 最初没有这个奖励，前期太穷（经常几回合买不起东西）→ 加了 8 + w*2
 *  - 但线性增长到后期变成 46/波，叠上掉落以后每回合能拿 200+，
 *    玩家反馈「中期后期太富有了，每回合几百块」。
 *  - 现在改成 **前期照旧大方、中后期递减**：
 *      前 3 波维持 10 ~ 14（解决"前期穷"）
 *      之后增长放缓并封顶，第 20 波约 24（而不是 46）
 *
 * 掉落侧的收紧见 world.js 的 _dropLoot / endWaveCleanup。
 */
export function waveClearBonus(waveIndex) {
  const early = 10 + waveIndex * 2;              // 1-3 波：10 / 12 / 14
  const capped = Math.min(24, 14 + (waveIndex - 3) * 1.1); // 之后缓慢爬到 24 封顶
  // 体检修复（第九十二轮）：<=3 会让第 4 波拿 16、第 5 波反而拿 15
  // （曲线出现倒挂）。按注释本意"前 3 波"应为 <3，序列即单调。
  return Math.round(waveIndex < 3 ? early : capped);
}

/**
 * 奶妈怪从第几波（1-based）开始进入刷怪池。
 *
 * 用户原话："做一个奶妈怪，从11回合之后开始生成"。
 * 单独导出是为了让测试断言"第 10 波一定没有奶妈、第 11 波一定有"
 * 时不必手写这个魔数。
 */
export const HEALER_FROM_WAVE = 11;

// ============================================================
// 第十二轮（用户要求）：Boss 战追加刷小怪
//
// 用户原话：
//   "给20波boss战和后续的无限boss战追加生成小怪的设定，从开头的生怪频率
//    为波次的50%随时间增加到150%（既90秒用完的时候）。"
//
// 解读：Boss 波本身**已经有**刷怪预算（第 20 波 budget 30、无限 Boss 波
// 30+extra*3），但以前那个预算是不变的 —— 开头和结尾一样密。
// 用户要的是一条**随时间爬升的曲线**：
//   开战瞬间 = 该波标准频率的 50%
//   90 秒（波次结束）= 150%
//   中间线性插值
//
// 为什么用"倍率"而不是"额外加多少只"：
//   用户说的是"生怪频率为波次的 50% → 150%"，主语是**频率**。
//   做成倍率之后，第 20 波和无限模式的 Boss 波自动各按自己的预算缩放，
//   不用为两者分别配数值。
//
// 为什么这条只在 Boss 波生效：
//   用户明确限定了"20波boss战和后续的无限boss战"。普通波的刷怪节奏
//   是另一种设计（预算按时间均匀释放），套上爬升曲线会让普通波
//   开头太空、结尾爆炸。
// ============================================================

/** Boss 战开头的刷怪频率倍率（相对该波标准频率） */
export const BOSS_ADD_RATE_START = 0.5;
/** Boss 战结束（90 秒）时的刷怪频率倍率 */
export const BOSS_ADD_RATE_END = 1.5;

/**
 * Boss 战在某个时间点的刷怪频率倍率。
 *
 * @param {number} waveElapsed 本波已进行的秒数
 * @param {number} waveDuration 本波总时长（Boss 波是 90）
 * @returns {number} 0.5 ~ 1.5 之间的倍率
 *
 * 用 waveDuration 而不是写死 90：用户说的是"90秒用完的时候"，
 * 而 90 秒正是 BOSS_WAVE_DURATION 的值。用参数表达之后，
 * 如果以后 Boss 波时长改了，这条曲线会自动跟着走到新的结尾，
 * 不会停在 90 秒处不动。
 */
export function bossAddRate(waveElapsed, waveDuration) {
  const total = Math.max(1e-6, waveDuration);
  const t = Math.min(1, Math.max(0, waveElapsed / total));
  return BOSS_ADD_RATE_START + (BOSS_ADD_RATE_END - BOSS_ADD_RATE_START) * t;
}

/**
 * Boss 战在某个时间点，**应该已经刷掉的预算占总预算的比例**（0~1）。
 *
 * 这是 bossAddRate 的归一化积分：
 *     ∫₀ᵗ rate ds / ∫₀ᵀ rate ds
 * rate 从 0.5 线性升到 1.5 时，令 u = t/T，结果为：
 *     P(u) = 0.5·u + 0.5·u²
 *
 * 为什么需要一个"积分"版本，而不是直接把 rate 乘在刷怪间隔上：
 *   第一版就是乘间隔的，实测**曲线的尾巴被截断了** ——
 *   Boss 波的预算（第 20 波 72 只）按原来的间隔在 **82 秒**左右就刷完了，
 *   于是 82~90 秒完全没有怪，"150% 在 90 秒达成"这件事根本没发生，
 *   实际峰值只到 1.41 倍就停了。
 *   根因是预算是个**有限总量**：任何"平均倍率为 1"的曲线都改不了
 *   "总共只刷 72 只"这个事实，而原来的间隔本身是略微超发的
 *   （spawnInterval 里那个 1.15 的系数），所以本来就会提前刷完。
 *
 *   改成按"预算释放进度"来发怪之后：
 *     - 曲线**精确**归一化：P(0)=0，P(1)=1，所以总刷怪量恰好等于预算，
 *       既不提前刷完也不刷不完；
 *     - 刷怪**恰好铺满整波**，150% 真的落在 90 秒那一刻；
 *     - 前半段慢、后半段快是直接由 P(u) 的形状保证的（P 是下凸的）。
 *
 * 单调性：P'(u) = 0.5 + u > 0（u≥0），所以它严格递增，不会出现
 * "已经刷过的预算变少"这种会卡住刷怪的情况。
 */
export function bossAddProgress(waveElapsed, waveDuration) {
  const total = Math.max(1e-6, waveDuration);
  const u = Math.min(1, Math.max(0, waveElapsed / total));
  return 0.5 * u + 0.5 * u * u;
}

/** 奶妈怪在刷怪池里的权重。刻意低：它是**战术目标**，不是主力。 */
export const HEALER_WEIGHT = 5;

/**
 * 把奶妈怪注入某一波的刷怪池（第十二轮）。
 *
 * 为什么在这里集中注入，而不是往 WAVES 的 20 个池子里各写一行：
 *   1. 用户的条件是"第 11 波之后"，这是一条**波次规则**，不是每波的内容。
 *      写成循环后，条件只有一处，改开始波次只动一个常量。
 *   2. 无限模式（21 波起）会自动合成新配置 —— 在那里手写池子根本覆盖不到，
 *      而"11 波之后"在无限模式里显然也该成立。集中注入自动覆盖它。
 *   3. 手写 20 遍必然有漏的，而且以后有人加第 21 波又会漏。
 *
 * ⚠️ 必须返回**新对象**，绝不能原地改 cfg.spawns：
 *    WAVES 是模块级常量，原地 push 会污染它 —— 第二次取同一波时
 *    奶妈会被再 push 一次，权重越玩越高。用缓存保证同一波只算一次，
 *    而且每次拿到的是同一个对象（有测试依赖 config 的稳定性）。
 */
const _healerInjected = new Map();

function withHealer(cfg, wave1) {
  if (!(wave1 >= HEALER_FROM_WAVE)) return cfg;
  if (!cfg || !Array.isArray(cfg.spawns) || cfg.spawns.length === 0) return cfg;

  const key = wave1;
  const cached = _healerInjected.get(key);
  if (cached) return cached;

  // 已经手动写进池子的话就别重复注入了
  const already = cfg.spawns.some((s) => s.id === 'healer_bunny');
  if (already) {
    _healerInjected.set(key, cfg);
    return cfg;
  }

  const out = {
    ...cfg,
    spawns: [...cfg.spawns, { id: 'healer_bunny', weight: HEALER_WEIGHT }],
  };
  _healerInjected.set(key, out);
  return out;
}

// ============================================================
// 第二十一轮：特殊波次
//
// 用户原话："从11回合开始增加特殊波次。比如20%概率这关全是冲锋类敌人。
// 20%概率这关全是弹幕类敌人。20%概率这关全是肉盾类敌人。然后增加一个
// 触发冷却，触发一次之后后续2回合内不会再次触发特殊波次。"
//
// 结构：world.startWave 里掷骰（三类各 20%，掷中哪类这波就"全是"那一类），
// 命中后刷怪池换成下面的类别池（不再读该波自己的 spawns）。触发后
// SPECIAL_WAVE_COOLDOWN 个回合内不再掷骰。Boss 波（10/20 波与无限 Boss 波）
// 不参与 —— Boss 战有自己的节奏，往里塞"满屏弹幕"会变味。
// 重试本波必须复现同一个特殊波：World 按波次缓存抽签结果（同第十一轮
// Boss 替换表"重试不换 Boss"的思路）。
// ============================================================

/** 特殊波次从第 11 波开始（1-based 波号，即 waveIndex >= 10） */
export const SPECIAL_WAVE_FROM = 11;

/** 触发一次特殊波后，接下来 2 个回合不再触发 */
export const SPECIAL_WAVE_COOLDOWN = 2;

/** 每类的触发概率——原三类各 20%（总约六成波是特殊波）；
 *  第一百五十六轮扩到七类后**总触发率保持不变**，七类均分：
 *  每类 ≈ 0.6/7 ≈ 8.6%（见 world._rollSpecialWave 的抽签） */
export const SPECIAL_WAVE_CHANCE = 0.2;

/**
 * 特殊波次的三个类别池。
 *
 * 分类口径（按敌人的实际行为，不按名字）：
 *  - charge 冲锋类：behavior charger / 带 chargeSpeed（目前只有 charger 一个，
 *    但"满屏冲锋怪"本身就够特殊）；
 *  - bullet 弹幕类：真 shooter 四件套（spitter/chaos_egg/spiral_cat/blade_cat）；
 *    elite_spitter 仍留在精英池里，不在普通波客串；
 *  - tank 肉盾类：护甲 ≥4 的硬壳（tank/crab/elite_crab）+ 会分裂的 splitter
 *    （打死变两只，"量"上也是肉）。healer_bunny 是辅助，不归入任何一类，
 *    特殊波里不刷。
 */
export const SPECIAL_WAVE_POOLS = {
  charge: { label: '全是冲锋敌人！', ids: ['charger'] },
  bullet: { label: '全是弹幕敌人！', ids: ['spitter', 'chaos_egg', 'spiral_cat', 'blade_cat'] },
  tank: { label: '全是肉盾敌人！', ids: ['tank', 'crab', 'elite_crab', 'splitter'] },
  // ---- 第一百五十六轮：新四类（不改刷怪构成，只挂波次模组）----
  // 用户原话：
  //  "内部混乱：该模式下所有近战怪会对远程怪造成伤害，远程怪会对近战怪
  //   造成伤害。两边ai互相敌对（不影响本身敌对玩家，也就是靠近玩家还是
  //   会打玩家）。"
  //  "全员摸鱼日：所有怪物移动速度-30%，攻击速度-50%。"
  //  "发薪日：所有怪物攻击力+20%，射击频率+20%。"
  //  "隔壁发免费鸡蛋了？：怪物刷新数量-50%，但是所有怪物掉落翻倍
  //   （不影响本局经济）"
  // 没有 ids 字段 = 不换本波刷怪构成（_pickSpawnEntry 对无 pool 的类型
  // 走原波次配置），模组效果在 world / enemy 的消费点按类型生效。
  chaos: { label: '内部混乱！近战与远程互相敌对！' },
  slack: { label: '全员摸鱼日！敌人移速 -30%、攻速 -50%' },
  payday: { label: '发薪日！敌人攻击 +20%、射速 +20%' },
  egg: { label: '隔壁发免费鸡蛋了？怪少一半，掉落翻倍！' },
};

/** 某一波的配置（无限模式会合成 Boss 波并继续加难度） */
export function getWaveConfig(waveIndex) {
  if (waveIndex < WAVES.length) return withHealer(WAVES[waveIndex], waveIndex + 1);
  const wave1 = waveIndex + 1;
  // 无限模式的 Boss 波：按 endlessBossCount 决定放几个 Boss
  const bossCount = endlessBossCount(wave1);
  if (bossCount > 0) {
    const extra = wave1 - ENDLESS_START_WAVE;
    return withHealer({
      spawns: [
        { id: 'ghost', weight: 7 },
        { id: 'tank', weight: 7 },
        { id: 'charger', weight: 6 },
      ],
      budget: 30 + extra * 3,
      // 第四十八轮：按轮换表取阵容（波内不重复，40 波全员四连）
      bosses: endlessBossLineup(wave1) || [],
      isBoss: true,
      duration: BOSS_WAVE_DURATION,
      endless: true,
    }, wave1);
  }

  // 无限模式的普通波：复用最后几波并持续加难度
  const fallback = WAVES[WAVES.length - 2];
  const extra = wave1 - ENDLESS_START_WAVE + 1;
  return withHealer({
    ...fallback,
    budget: fallback.budget * (1 + extra * 0.15) * ENDLESS_STAT_MUL,
    endless: true,
  }, wave1);
}

/**
 * 某一波的时长（秒）。
 *
 * 设计（用户要求）：中后期慢慢变长，不是每关都 30 秒。
 *   - 第 1 波 30s
 *   - 线性爬到 **第 19 波（最后一个普通波）60s**
 *   - Boss 关（10 / 20 波）固定 90s
 *   - 无限模式（21 波起）继续慢慢变长，上限 120s
 *
 * 注意分母用的是"最后一个普通波的索引"（WAVE_COUNT - 2 = 18），
 * 而不是 WAVE_COUNT - 1。因为第 20 波是 Boss 关（时长被 90s 覆盖），
 * 用 19 当分母的话普通波永远爬不到 60 —— 第 19 波只会到 58s，
 * "最高 60 秒"就落空了。
 */
export function getWaveDuration(waveIndex) {
  const cfg = getWaveConfig(waveIndex);
  // 显式配了 duration 的（Boss 关）优先
  if (cfg.duration) return cfg.duration;

  const wave1 = waveIndex + 1;
  if (wave1 > WAVE_COUNT) {
    // 无限模式：从 60s 继续往 120s 慢慢加
    const t = Math.min(1, (wave1 - WAVE_COUNT) / 20);
    return Math.round(WAVE_DURATION_MAX + (ENDLESS_DURATION_MAX - WAVE_DURATION_MAX) * t);
  }

  // 普通波：从 WAVE_DURATION 线性爬到 WAVE_DURATION_MAX（第 19 波到顶）
  const LAST_NORMAL = Math.max(1, WAVE_COUNT - 2);   // 18
  const t = Math.min(1, Math.max(0, waveIndex) / LAST_NORMAL);
  return Math.round(WAVE_DURATION + (WAVE_DURATION_MAX - WAVE_DURATION) * t);
}