// ============================================================
// 敌人定义
// behavior: 'chase' 直冲 | 'zigzag' 蛇形 | 'charger' 蓄力冲撞
//           'shooter' 远程 | 'orbit' 绕圈 | 'splitter' 死亡分裂
//           'exploder' 死亡爆炸 | 'tank' 缓慢高血 | 'boss' Boss 专用
// ============================================================

// 第七轮：Boss 掉落整体打折。用户："boss目前有点太富有了，降低20%掉落的金钱和经验。"
// 掉落量在 world._dropLoot 里是从 xp / materials 线性换算的，所以在此处乘系数
// 即可得到精确的 -20%（猫王 60→48，虚空 120→120×0.8=96）。
export const BOSS_LOOT_MUL = 0.8;

// ---- 第十八轮 ⑨：移速地板 ----
//
// 用户原话："让所有移速过慢的怪物速度适当增加，有个怪物几乎没怎么动就被
//   玩家打死了，尽量让所有怪都至少有玩家 60% 以上的移速吧。"
//
// 地板 120 是第十八轮按"当时玩家默认移速 200 的 60%"定的**固定绝对值**。
// 第十九轮玩家默认移速提到 215（entities/player.js 的 baseSpeed）后地板
// **没有跟着涨**：120 ÷ 215 ≈ 56%，"至少 60%"按字面已不成立（那要把地板
// 和所有 120 速的怪一起提到 129）。保持 120 的理由：60% 只是手段，
// 目的"慢怪也能真正走到玩家面前"不受玩家提速影响；而且第一波正在被围殴，
// 不该顺手加强怪。
// 原来慢得离谱的几个（铁甲猫 34 = 17%、螺旋猫 38 = 19%、
// 巨壳蟹 40 = 20%、混乱蛋 40 = 20%、硬壳蟹 42 = 21%）现在都能真正走到
// 玩家面前，而不是站在原地被远程点死。
//
// 底下那条 ENEMY_SPEED_FLOOR 由 test/difficulty-tuning.mjs 断言，
// 以后加新怪忘了给够速度会被测出来 —— 这条规则靠人肉检查太容易漏。
//
// 上方只谈**地板**。天花板（原 ENEMY_SPEED_CAP=200）已于第十九轮末整个
// 移除（用户：玩家后期可以更快，敌人超过 215 也是"不升移速"的代价）——
// 现在只有下限这一条硬规则：新怪的 base speed 仍不得低于 120。
export const ENEMY_SPEED_FLOOR = 120;
/** 玩家默认移速（entities/player.js 的 baseSpeed），第十九轮起 215 */
export const PLAYER_BASE_SPEED = 215;

export const ENEMIES = [
  // ---------- 普通 ----------
  {
    id: 'slime', name: '史莱姆', hp: 8, speed: 120, damage: 3, radius: 13,
    color: '#6ee7a8', shape: 'blob', behavior: 'chase', xp: 1, materials: 1,
    desc: '最基础的敌人，慢吞吞地撞过来。',
  },
  {
    id: 'mouse', name: '机械鼠', hp: 6, speed: 120, damage: 2, radius: 11,
    color: '#9aa3bb', shape: 'tri', behavior: 'zigzag', xp: 1, materials: 1,
    desc: '跑得很快，走位飘忽。',
  },
  {
    id: 'bat', name: '蝙蝠猫', hp: 10, speed: 120, damage: 3, radius: 12,
    color: '#b794f6', shape: 'diamond', behavior: 'orbit', xp: 1, materials: 1,
    desc: '喜欢绕着你转圈，抓不太准。',
  },
  {
    id: 'crab', name: '硬壳蟹', hp: 22, speed: 120, damage: 5, radius: 16,
    color: '#ff9f68', shape: 'hex', behavior: 'chase', armor: 4, xp: 2, materials: 2,
    desc: '壳很硬，需要更高伤害才打得动。',
  },
  {
    id: 'spitter', name: '吐酸猫', hp: 14, speed: 120, damage: 4, radius: 14,
    color: '#a5d64a', shape: 'blob', behavior: 'shooter', xp: 2, materials: 2,
      projectileShape: 'drop',      // 185 轮：酸液水滴（头圆尾尖，沿飞行方向）
    attackCooldown: 2.0, attackRange: 240, projectileSpeed: 220, projectileDamage: 4,
    desc: '会远程吐酸液，注意保持距离。',
  },
  {
    id: 'bomber', name: '自爆猫', hp: 12, speed: 120, damage: 0, radius: 14,
    color: '#ff5f6d', shape: 'circle', behavior: 'exploder', xp: 2, materials: 2,
    explosionDamage: 14, explosionRadius: 76,
    desc: '靠近就自爆，务必提前打掉。',
  },
  {
    id: 'tank', name: '铁甲猫', hp: 60, speed: 120, damage: 8, radius: 22,
    color: '#8b93b0', shape: 'square', behavior: 'chaseShooter', armor: 8, xp: 4, materials: 4,
      projectileShape: 'shard', projectileColor: '#ffa04a',   // 185 轮：重炮 = 橙色金属碎片（灰色弹在暗图上看不清）
    // 第九轮：用户要"修改一些近战的敌人让他们拥有远程能力"。
    // 铁甲猫原本是纯近战且极慢（speed 34），玩家只要走开就永远安全 ——
    // 这正是用户说的"几乎没啥受伤机会"。给它一门重炮：
    // 慢速 + 高伤 + 单发，所以它仍然可以靠走位躲，但不能再被无视。
    // 用 chaseShooter（一路压上来的同时开炮），而不是 shooter（会后退），
    // 这样它"缓慢推进的坦克"这个定位不变。
    bulletPattern: 'aimed',
    attackCooldown: 3.2, attackRange: 300, projectileSpeed: 190,
    projectileDamage: 9, projectileCount: 1, projectileRadius: 10,
    desc: '移动缓慢但极难击杀，撞上很痛，还会远距离轰一发重炮。',
  },
  {
    id: 'charger', name: '冲锋猫', hp: 26, speed: 120, damage: 7, radius: 16,
    color: '#ff7043', shape: 'tri', behavior: 'charger', xp: 3, materials: 3,
    // 第二十五轮：冲刺速度 +20%（260 → 312）。用户："那个橙色的蓄力冲刺
    // 猫猫小怪的冲刺速度太慢了，蓄力半天还是慢悠悠的过去。让它的冲刺
    // 速度+20%。"蓄力时长/冲刺距离不变，只提速。
    chargeSpeed: 312, chargeWindup: 0.7, chargeDuration: 0.55,
    // 第三十七轮：冲刺距离 +100%（0.55s × 2 = 1.1s，速度不动）——
    // 用户："橙色猫猫敌人的冲刺距离+100%。并且让它冲刺的时候再次定位
    // 玩家的位置。……这时候玩家就需要转移到C，不然就要吃+100%冲刺
    // 距离的攻击了。"（出手瞬间重新瞄准的实现见 enemy.js 的
    // _runChargeStates；chargeDistanceMul 是第十四轮的现成距离倍率。）
    chargeDistanceMul: 2,
    desc: '会蓄力后高速冲撞，看到红光就闪开。',
  },
  {
    id: 'splitter', name: '分裂猫', hp: 30, speed: 120, damage: 5, radius: 19,
    color: '#7dd87d', shape: 'blob', behavior: 'splitter', xp: 3, materials: 3,
    splitInto: 'slime', splitCount: 3,
    desc: '死亡后会分裂成三只史莱姆。',
  },
  {
    id: 'ghost', name: '幽灵猫', hp: 18, speed: 120, damage: 6, radius: 15,
    color: '#c9d6e8', shape: 'diamond', behavior: 'chase', xp: 3, materials: 3,
    dodge: 0.25,
    desc: '身体半透明，有四分之一概率打空。',
  },

  // ---------- 第九轮新增：弹幕类型多样化 ----------
  //
  // 用户原话："加多一到两种弹幕类型敌人，让弹幕类型丰富一些，不要只是自机狙，
  //            来点随机蛋怪物，或者你修改一些近战的敌人让他们拥有远程能力，
  //            然后再让一种近战敌人移动速度稍快点，可以一直追着玩家的感觉，
  //            不然目前远程弹幕太少，近战敌人速度太慢导致玩家只要不是被包围死
  //            几乎没啥受伤机会。"
  //
  // 三个问题对应三个新敌人：
  //   1. "不要只是自机狙，来点随机蛋" → chaos_egg（随机角度弹）
  //   2. "修改一些近战的敌人让他们拥有远程能力" → 已有的 charger/spitter
  //      之外再加 blade_cat（近战贴脸 + 环形弹幕，不能靠拉开距离躲）
  //   3. "让一种近战敌人移动速度稍快点，可以一直追着玩家" → stalker（高速直追）
  {
    id: 'chaos_egg', name: '混乱蛋', hp: 20, speed: 120, damage: 4, radius: 15,
    color: '#ff8fd4', shape: 'circle', behavior: 'shooter', xp: 3, materials: 3,
      projectileShape: 'star',      // 185 轮：混乱 = 乱飞的小星星
    // 随机蛋：角度在 360° 内随机 → 玩家没法靠"站桩躲准星"规避
    bulletPattern: 'random', randomSpread: 360,
    attackCooldown: 1.6, attackRange: 300, projectileSpeed: 170,
    projectileDamage: 5, projectileCount: 2, projectileRadius: 9,
    desc: '吐出的蛋方向完全随机，没有安全站位可言。',
  },
  {
    id: 'spiral_cat', name: '螺旋猫', hp: 34, speed: 120, damage: 5, radius: 17,
    color: '#7ad7ff', shape: 'hex', behavior: 'shooter', xp: 4, materials: 4,
      projectileShape: 'petal',     // 185 轮：螺旋 = 旋出的花瓣
    // 螺旋：每发角度递增，形成旋转的弹幕墙，逼玩家绕着走
    bulletPattern: 'spiral', spiralStep: 0.62,
    attackCooldown: 0.5, attackRange: 340, projectileSpeed: 195,
    projectileDamage: 5, projectileCount: 1, projectileRadius: 8,
    desc: '持续旋转喷射弹幕，把场地切成一片片。',
  },
  {
    id: 'blade_cat', name: '刃猫', hp: 40, speed: 120, damage: 9, radius: 17,
    color: '#ffb347', shape: 'tri', behavior: 'chaseShooter', xp: 4, materials: 4,
      projectileShape: 'blade',     // 185 轮：刃猫 = 甩出的弯刃（环形 6 连）
    // 一边贴脸追一边放环形弹幕：拉开距离也躲不掉，必须真的走位
    bulletPattern: 'ring',
    attackCooldown: 2.6, attackRange: 300, projectileSpeed: 180,
    projectileDamage: 6, projectileCount: 6, projectileRadius: 8,
    desc: '贴着你打，同时甩出一圈弹幕，拉开距离也没用。',
  },
  {
    id: 'stalker', name: '猎杀猫', hp: 30, speed: 132, damage: 8, radius: 14,
    color: '#ff5c8a', shape: 'diamond', behavior: 'chase', xp: 3, materials: 3,
    // 用户要的"一直追着玩家的感觉"：速度刻意高于玩家基础移速，
    // 所以不靠位移甩不掉，必须靠输出或地形把它清掉。
    desc: '跑得比你快，会一直咬着你不放。',
  },

  // ---------- 奶妈怪（第十二轮，用户新增）----------
  //
  // 用户原话：
  //   "做一个奶妈怪，从11回合之后开始生成，会和玩家保持距离不靠近，
  //    aoe治疗受伤的敌人，大概每秒回20%敌人的生命，让被治疗的敌人获得
  //    buff：攻击+20%，获得30%固定减伤。奶妈怪移动速度要快，不超过玩家
  //    默认100%，血量和远程怪差不多就行。奶妈怪形象可以是樱花色的兔耳
  //    敌人，耳朵长一点，带个小蝴蝶结在耳朵上方便玩家一眼认出是奶妈怪。"
  //
  // 逐条对应：
  //   保持距离      → behavior 'healer'（见 enemy.js 的 _healer）
  //   每秒回 20%    → 第三十一轮改为：5 秒 CD 的脉冲，小怪档 20%
  //                  （healPct，按**目标自己的生命上限**算；精英 10% /
  //                  Boss 0.5%，用户："她16万血奶一口30000多太可怕了"）
  //   攻击 +20%     → healBuff: { damagePct: 20 }
  //   30% 固定减伤  → healBuff: { damageReduction: 0.30 }
  //   移速快但要甩得掉 → speed 130。它要"快"以便跟上战场，但真贴着
  //                  玩家默认移速（215）做会和玩家同速 —— 玩家永远甩不掉
  //                  一个会奶人的怪，体验会变成"被追着磨"。130 能追上
  //                  大部队、又追不上全速逃跑的玩家（波次成长叠上去后
  //                  另说，那是全体敌人共同的后期压力，不是奶妈特调）。
  //   血量同远程怪   → hp 34（远程怪 螺旋猫 34 / 刃猫 40 之间，取 34）
  //   樱花色兔耳 + 蝴蝶结 → 贴图 enemy_healer_bunny.png
  //
  // 为什么给 `spawnFromWave: 11` 而不是把它塞进常规池按波次随机：
  //   用户说的是"从 11 回合之后开始生成" —— 这是一条**硬门槛**，
  //   不是概率。塞进常规池的话，第 8 波也可能随机到它，
  //   而前中期玩家的输出还不足以越过它去点掉被奶的怪，会很难受。
  //   world._spawnOne 会读这个字段做过滤。
  {
    id: 'healer_bunny', name: '樱花护士', hp: 34, speed: 130, damage: 4, radius: 15,
    color: '#ffb3c9', shape: 'circle', behavior: 'healer', armor: 2,
    xp: 4, materials: 4,
    // 从这里开始才会出现在刷怪池里
    spawnFromWave: 11,
    // 治疗参数
    healRadius: 190,          // AOE 半径
    // 第三十一轮：治疗从"每秒回 20%"改成"5 秒一次的脉冲，奶量按目标类型
    // 分档"。用户原话："我在打噩梦20关的黑化开发猫猫的时候快打赢了他召唤
    // 了一波奶妈小怪，奶妈小怪瞬间给他奶满了。……奶人cd5秒，小怪回复20%，
    // 精英10%，boss0.5%。不然黑化开发猫猫我一辈子也打不死。她16万血奶一口
    // 30000多太可怕了。" 旧机制下多个奶妈各自 0.5s 一跳、每秒 20%，
    // Boss 16 万血一秒被奶 3 万多、召唤潮直接回满。新档位下 Boss 一口
    // 只有 0.5%（≈805），"优先点奶妈"的压力还在，但 Boss 血线回到
    // 玩家 DPS 可压制的量级。
    healInterval: 5,          // 奶人 CD 5 秒（脉冲间隔）
    // 第一百一十五轮：奶量再削 —— 用户："削弱奶妈敌人的奶量，改为小怪
    // +15%，精英+5%，boss+0.5%（boss不变）"。
    healPct: {                // 每次脉冲回复**目标生命上限**的比例
      normal: 0.15,           //   小怪 20% → 15%
      elite: 0.05,            //   精英 10% → 5%
      boss: 0.005,            //   Boss 0.5%（不变）
    },
    healBuffDuration: 4,      // 被奶到的敌人保持 buff 4 秒
    healBuff: { damagePct: 20, damageReduction: 0.30 },
    // 保持距离：和 shooter 类似，但不后退得那么远
    keepDistance: 260,
    // 它自己**不攻击玩家**（damage 只在被贴脸时象征性生效）。
    // 这是刻意的：一个又奶又打的怪会让"优先点掉奶妈"这个正确解法
    // 变成"必须点掉奶妈"，压力过大。它只是让别的怪变强。
    desc: '樱花色的兔耳护士。会远远地给受伤的同伴回血并加持护盾，优先点掉它。',
  },

  // ---------- 精英 ----------
  {
    id: 'elite_crab', name: '巨型蟹将', hp: 160, speed: 120, damage: 12, radius: 30,
    color: '#ff8a50', shape: 'hex', behavior: 'chase', armor: 12, xp: 10, materials: 12,
    elite: true, scale: 1.0,
    desc: '精英敌人。血厚甲高，击杀后掉落大量材料。',
  },
  {
    id: 'elite_spitter', name: '毒液女王', hp: 120, speed: 120, damage: 8, radius: 26,
    color: '#c6e04a', shape: 'blob', behavior: 'shooter', xp: 10, materials: 12,
      projectileShape: 'drop',      // 185 轮：毒液女王三连酸液水滴
    elite: true, attackCooldown: 1.3, attackRange: 280, projectileSpeed: 260,
    projectileDamage: 7, projectileCount: 3, projectileSpread: 26,
    desc: '精英敌人。三连吐酸，压制力很强。',
  },

  // ---------- 宝箱怪 ----------
  // 用户要求："宝箱怪物会努力远离玩家但是本身有一定的光效方便玩家看到他，
  //            然后击杀宝箱怪物随机获得一个道具在回合结算的时候给予玩家。"
  // 所以它：不追人（behavior 'flee'）、发光、血不多但跑得快，
  // 击杀后把一个"待结算道具"塞进 world.pendingItemRewards。
  {
    id: 'treasure_mimic', name: '宝箱怪', hp: 26, speed: 120, damage: 0, radius: 15,
    color: '#ffc857', shape: 'chest', behavior: 'flee',
    xp: 6, materials: 6, glow: true,
    treasure: true,
    desc: '会逃跑的宝箱怪。追到并击杀它，就能拿到一件道具（回合结束时结算）。',
  },
  {
    id: 'treasure_mimic_elite', name: '华丽宝箱怪', hp: 70, speed: 120, damage: 0, radius: 20,
    color: '#ff9f68', shape: 'chest', behavior: 'flee',
    xp: 14, materials: 14, glow: true,
    treasure: true, treasureRare: true,
    desc: '更稀有的宝箱怪，掉落品质更好。',
  },

  // ---------- Boss ----------
  // 血量按"玩家在该波的实际 DPS"定，并会随玩家发育水平浮动。
  //
  // 数值调整史：
  //  - 最初猫王 900 / 虚空 2200，缩放公式过陡（第 20 波 10.46 倍），
  //    有效血超过 2 万，45 秒内数学上打不死。
  //  - 后来缩放改缓 + 基础血降到 620 / 1500，能打了。
  //  - 但经济调整后玩家成型更快，噩梦难度下 Boss 活不过 10 秒 →
  //    现在整体 **+50%**（620→930，1500→2250）。
  //
  // 三阶段设计见 data/boss-phases.js：
  //   阶段切换按「血量 66%/33%」或「剩余时间 66%/33%」**谁先到算谁**。
  //
  // 第七轮：用户原话 "boss目前有点太富有了，降低20%掉落的金钱和经验。"
  //   猫王 60/60 → 48/48，虚空 120/120 → 96/96（见 BOSS_LOOT_MUL）。
  //   掉落是按 xpValue/materialValue 线性缩放的，所以这里改数值即为精确 -20%。
  //
  // ---- 第十九轮 ②：全部 Boss 基础伤害 +20%（damage 与 projectileDamage 一起）----
  //   用户原话："目前我的魔法师都可以扛着噩梦难度的boss伤害把她打死。"
  //   远程 Boss 的主要输出是弹幕，只加接触伤害等于没加 —— 所以两条一起 ×1.2。
  //   Boss 召唤出来的小怪是它们自己的数值，不在 +20% 范围内。
  //   19.2/26.4/10.8 这类小数是 ×1.2 的精确结果；伤害管线全程浮点，无取整损耗。
  {
    id: 'boss_cat_king', name: '路过被殴打的猫娘', hp: 1860, speed: 160, damage: 19.2, radius: 46,
    color: '#ff4d94', shape: 'star', behavior: 'boss', armor: 10,
    xp: 60 * BOSS_LOOT_MUL, materials: 60 * BOSS_LOOT_MUL,
    boss: true, bossName: '路过被殴打的猫娘',
    attackCooldown: 2.4, attackRange: 320, projectileSpeed: 240, projectileDamage: 12,
    projectileCount: 8, projectileSpread: 360, summonCount: 4, summonCooldown: 8,
    // 第十轮（用户要求）：弹幕角度加 5~15° 随机抖动。
    // 用户原话："不然记住弹幕路线之后还是容易躲。"
    // 抖动幅度取 5~15° 而不是固定值：固定幅度仍然可以背
    // （"每发偏 10°"也是确定的），幅度本身随机才真的没法预判。
    bulletJitterDeg: [5, 15],
    // 第十五轮 ④：这三张 Boss 立绘**自带脸**，所以关掉 canvas 画的眼。
    // 普通敌人贴图是"无脸"的，眼睛由渲染层每帧画（所以会跟着玩家转）。
    // Boss 立绘是完整人物，再叠一组眼睛会变成四只眼。
    noCanvasEyes: true,
    // 第十五轮 ⑤：血量翻倍（930 → 1860）。
    // 第十五轮 ④：改名 + 换图（见 sprites 的 boss_cat_king）。
    // 第十五轮 ④：**移动方式从追击玩家改成远离玩家**（kite: 'flee'）——
    //   它本来就会保持中距离放弹幕，现在改成被逼退时主动拉开，
    //   玩家的体感是"这只猫娘一直在躲我"。
    //
    // 第十八轮 ⑥：**移速 46 → 160**。
    //   用户原话："给被殴打的猫娘 boss 移动速度 +50%，现在她逃跑的太慢了，
    //             让她速度快一些。至少有玩家默认移速的 80% 以上。"
    //   46 × 1.5 = 69，离"玩家默认移速的 80%"（200 × 0.8 = 160）差得远，
    //   所以直接取 160 这个下限 —— 用户给的是一句"至少"，160 才满足后半句。
    //   它本来就该是"追不上的逃跑型 Boss"，跑得比小怪快才合理。
    kite: 'flee',
    // 第四十轮：三阶段专属 +20% 移速（flee 分支消费，见 enemy.js _boss）。
    // 用户原话："给路过被殴打的猫娘boss的三阶段加个新设定，+20%移速，
    // 让她跑得更快一些看起来更不想被打的样子。"
    // 三阶段实际移速 = 160 × 1.22(阶段倍率) × 1.2 = 234.2 px/s，
    // 比玩家默认移速 200 还快 —— 真正的"不想被打"。
    phase3SpeedMul: 1.2,
    // ---- 第九十二轮：结晶蛋点缀 + 阶段个性 ----
    // 用户原话："目前标准模式的4个boss都只有普通蛋，你可以设计一下他们的
    // 弹幕，增加一些结晶蛋进去……除了开背虾boss，其他3个boss3个阶段的
    // 弹幕变化大部分都只是单纯的变多了。"
    // 被殴猫娘的个性：边逃边撒"猫爪结晶"——
    //   主环每 3 颗抽 1 颗粉结晶蛋；二阶段起朝玩家甩 3 连粉/白结晶扇
    //   （撒娇式猫爪拍）；三阶段两圈交错环全部结晶化（粉白交织）。
    crystalEvery: 3,
    crystalColor: '#ff8fb8',
    crystalFan: {
      fromPhase: 2, count: 3, spreadDeg: 34, speedMul: 1.1,
      colors: ['#ff7fae', '#fff2f4'],
    },
    phase3CrystalRings: true,
    desc: '第 10 波的守关 Boss。会环形弹幕并召唤小怪，血量越低攻势越猛，还会一路躲着你跑。',
  },
  {
    id: 'boss_void_lord', name: '黑化开发猫猫', hp: 2250, speed: 120, damage: 26.4, radius: 54,
    color: '#7c4dff', shape: 'star', behavior: 'boss', armor: 18,
    xp: 120 * BOSS_LOOT_MUL, materials: 120 * BOSS_LOOT_MUL,
    boss: true, bossName: '黑化开发猫猫',
    attackCooldown: 2.0, attackRange: 360, projectileSpeed: 280, projectileDamage: 16.8,
    projectileCount: 12, projectileSpread: 360, summonCount: 6, summonCooldown: 7,
    chargeSpeed: 300,
    // 第十轮（用户要求）：第 20 关 Boss 也加 5~15° 随机角度，
    // 理由和第 10 关一样 —— 环形弹幕角度完全确定就能被背板。
    // 它的弹幕更密（12 发 + 最多 3 层交错环），所以抖动带来的
    // "缝隙位置不可预测"效果比猫王更明显。
    bulletJitterDeg: [5, 15],
    // 第十五轮 ④：立绘自带脸，关掉 canvas 眼睛（理由见 boss_cat_king）
    noCanvasEyes: true,
    // ---- 第九十二轮：结晶蛋点缀 + 阶段个性 ----
    // 黑化开发猫猫的个性：失控的程序在"报错"——
    //   主环每 4 颗抽 1 颗亮紫结晶蛋；二阶段起朝玩家喷 5 连
    //   紫/品红/蓝"报错输出"结晶扇（比猫爪扇更宽更快）；
    //   三阶段反向环升级成**七彩结晶环**（全场唯一的彩虹环签名）。
    crystalEvery: 4,
    crystalColor: '#b388ff',
    crystalFan: {
      fromPhase: 2, count: 5, spreadDeg: 46, speedMul: 1.15,
      colors: ['#c56cf0', '#e879f9', '#70a1ff'],
    },
    phase3RainbowRing: true,
    desc: '第 20 波的最终 Boss。弹幕更密，还会冲锋与召唤，第三阶段压迫感极强。',
  },

  // ============================================================
  // 第十一轮：两个**近战冲锋型** Boss
  //
  // 用户原话："设计2个新的近战类型的boss，就是会冲锋和突击玩家，
  //   然后boss在10/20回合随机50%概率出现，即近战远程boss各50%，
  //   然后20的近战boss名字是'依趣苹果醋'。没错是一种苹果醋饮料，
  //   因为猫娘工作室的人都不喜欢他，所以把它做成boss挺有趣的，
  //   可以设计它不断追击玩家，停顿一下就冲刺玩家位置，
  //   然后有类似苹果醋的液体攻击弹幕效果。3个阶段要压迫感越来越强。"
  //
  // 和原来两个 Boss 的**根本区别**：
  //   原来两个（猫王/虚空）是 `behavior: 'boss'`，会保持 200px 中距离放弹幕，
  //   属于"远程风筝型" —— 玩家可以拉开距离绕着打。
  //   这两个是 `behavior: 'meleeBoss'`，**主动贴脸**，玩家躲不掉，
  //   必须靠走位和位移拉开。这是完全不同的压迫感来源。
  //
  // 共同机制（见 enemy.js 的 _meleeBoss）：
  //   - 常驻追击：一直朝玩家压上来，不停顿
  //   - **停顿蓄力 → 冲刺玩家当前位置**（用户点名要的手感）
  //   - 冲刺撞到玩家造成高额伤害（比弹幕疼得多）
  //   - 弹幕是"液体飞溅"式的近距扇形，不是环形密网
  //
  // 三阶段递增（沿用 boss-phases.js 的 PHASE_MODS，但近战 boss 更吃
  // `moveSpeed` 和 `fireRate`）：
  //   1 阶段：正常追击 + 偶尔冲刺
  //   2 阶段：冲刺更频繁、更快，开始甩液体弹幕
  //   3 阶段：连续冲刺 + 大范围液体爆发，压迫感最强
  // ============================================================
  // ---- 第一百八十五轮：2 个新的普通远程 Boss（与旧远程 Boss 各占一半远程名额）----
  // 用户："闲暇之余给我们设计 2 个新的普通 boss 出来"。招牌技能见 systems/boss-signatures.js，
  // 素材取自 rabiribi 弹幕库：潘多拉「格镜落弹」、诺诺「天幕横雨 + 三向落柱」。
  // 数值沿用同波次旧 Boss（血量/护甲/奖励/伤害完全对齐），平衡口径不变；只换"玩法"。
  {
    // speed 120 = 移速地板（第十八轮：所有怪不得慢于地板）
    id: 'boss_pandora', name: '格镜魔女·潘多拉', hp: 1860, speed: 120, damage: 19.2, radius: 44,
    color: '#c56cf0', shape: 'star', behavior: 'boss', armor: 10,
    xp: 60 * BOSS_LOOT_MUL, materials: 60 * BOSS_LOOT_MUL,
    boss: true, bossName: '格镜魔女·潘多拉',
    attackCooldown: 2.8, attackRange: 340, projectileSpeed: 200, projectileDamage: 12,
    projectileCount: 6, projectileSpread: 360, summonCount: 3, summonCooldown: 9,
    bulletJitterDeg: [5, 15],
    noCanvasEyes: true,
    // 不配 kite:'flee'：那是被殴猫娘的专属走位（第四十轮测试钉死"只有她在用"）；潘多拉走通用的保持中距离。
    signature: 'pandora',
    desc: '第 10 波的守关 Boss（魔女）。弹幕按 64px 网格左右镜像砸落，正对你的位置留着安全缝；血越少，侧击与星弹螺旋越多。',
  },
  {
    id: 'boss_nono', name: '天幕机兵·诺诺', hp: 2250, speed: 120, damage: 26.4, radius: 50,
    color: '#4ab8ff', shape: 'star', behavior: 'boss', armor: 18,
    xp: 120 * BOSS_LOOT_MUL, materials: 120 * BOSS_LOOT_MUL,
    boss: true, bossName: '天幕机兵·诺诺',
    attackCooldown: 2.4, attackRange: 360, projectileSpeed: 220, projectileDamage: 16.8,
    projectileCount: 8, projectileSpread: 360, summonCount: 4, summonCooldown: 8,
    bulletJitterDeg: [5, 15],
    noCanvasEyes: true,
    signature: 'nono',
    desc: '第 20 波的守关 Boss（机器人）。斜雨铺满天幕，二阶段起三向落柱围出三块安全方块，三阶段预判你的走位。',
  },
  {
    id: 'boss_acid_cat',
    name: '蒜蓉粉丝开背虾',
    // 第十五轮 ⑤：血量翻倍（1050 → 2100）。和第 10 关的远程 Boss 一起翻，
    // 因为"第十关的 boss"指这一关会出现的两个 Boss（50% 随机）。
    // 第十八轮 ⑨：speed 74 → 120（移速地板，见文件头的 ENEMY_SPEED_FLOOR）。
    hp: 2100, speed: 120, damage: 24, radius: 44,   // 第十九轮②：20 × 1.2
    // 第一百六十轮：噩梦专属基础血线 —— 用户："噩梦难度的蒜蓉粉丝开背虾
    // 血量改到第十波见到他的时候大概35000左右的血量吧。（基础血线你算一下
    // 改了）" 2935 × bossHpCurve(9)≈4.260 × 噩梦 2.8 ≈ 35005。
    hpNightmare: 2935,
    color: '#ff8a5c', shape: 'shrimp', behavior: 'meleeBoss', armor: 12,
    xp: 60 * BOSS_LOOT_MUL, materials: 60 * BOSS_LOOT_MUL,
    boss: true, bossName: '蒜蓉粉丝开背虾',
    // 近战 Boss 的弹幕是"液体飞溅"：条数少、扇面窄、速度慢但持续久，
    // 作用是把玩家**从安全位置赶开**，而不是靠密度杀人。
    // 所以 projectileCount 远低于远程 Boss 的 8~12。
    attackCooldown: 2.2, attackRange: 240, projectileSpeed: 210, projectileDamage: 10.8,   // 第十九轮②：9 × 1.2
    projectileCount: 5, projectileSpread: 70, summonCount: 3, summonCooldown: 9,
    // 冲刺参数：蓄力 0.55s（玩家有反应时间），冲刺 0.5s，速度 340。
    // 蓄力期间会画红色预警线（复用 charger 的表现），所以是"可读的威胁"。
    chargeSpeed: 340, chargeWindup: 0.55, chargeDuration: 0.5,
    bulletJitterDeg: [5, 15],
    // ---- 第十五轮 ④：虾 Boss 的三条新要求 ----
    //
    // 用户原话："然后第十关的近战boss名字变成'蒜蓉粉丝开背虾'，然后让这个虾
    //            boss的头部始终正对玩家，二阶段也加个突然冲刺撞击玩家的动作，
    //            三阶段加一个突然停止攻击然后猛的后退一大截然后同时射出水柱
    //            （类似激光水柱，很快）攻击玩家的动作。"
    //
    // ① 头部始终正对玩家 → facePlayer: true（渲染层用，见 renderer）
    // ② 二阶段突然冲刺撞击 → phaseCharge: true（_meleeBoss 里在阶段 ≥2 时
    //    额外插入一次蓄力更短、更突然的冲刺）
    // ③ 三阶段"停止攻击 → 猛退 → 水柱" → phaseWaterJet 配置
    facePlayer: true,
    // 第二十轮：立绘的头在**顶端中心** → 渲染层整图旋转（renderer 的
    // faceRotate 分支），让"那个点"始终对着玩家；其他 Boss 仍是水平镜像。
    faceRotate: true,
    phaseCharge: true,
    // 立绘自带"脸"（虾头和虾壳的纹理），关掉 canvas 眼睛（理由见 boss_cat_king）
    noCanvasEyes: true,
    phaseWaterJet: {
      // 后退距离（px）：要"猛的后退一大截"，取 420 —— 比一次冲刺还远，
      // 让玩家立刻失去贴脸机会，然后被迫面对接下来的水柱。
      retreatDistance: 420,
      // 后退耗时：0.28s 走完 420px ≈ 1500px/s，观感是"弹开"而不是"走开"。
      retreatDuration: 0.28,
      // 后退结束到水柱发射之间的停顿（蓄势，给玩家一点点反应时间但不长）
      windup: 0.22,
      // 水柱：又细又快，像激光。"很快"是用户的原话。
      jetSpeed: 1400,
      jetLength: 900,
      jetWidth: 16,
      jetDamage: 26,
      jetCount: 1,
      // 一次水柱结束后到下次尝试之间的间隔（避免刷屏）
      cooldown: 3.4,
    },
    desc: '第 10 波的守关 Boss（近战型，50% 概率出场）。虾头永远对着你，二阶段会突然撞过来，三阶段会弹开并喷出高速水柱。',
  },
  {
    id: 'boss_apple_cider',
    name: '依趣苹果醋',
    // 第十八轮 ⑨：speed 78 → 120（移速地板，见文件头的 ENEMY_SPEED_FLOOR）。
    // 第十九轮（苹果醋加强，用户原话："依趣苹果醋boss太菜了，它的速度不快
    //   弹幕还很容易消失"）：
    //   ① 总移速 +10% —— speed 120 → 132，冲刺 chargeSpeed 380 → 418（包括冲刺）。
    //   ② 弹幕消失范围 ×2 —— projectileRange 520（默认值）→ 1040。
    //      原来弹幕飞 520px 就没了，远程角色站在 550px 外输出他等于白给，
    //      现在弹幕能追到安全位，必须真正跑位，不能原地无伤风筝。
    hp: 2400, speed: 132, damage: 31.2, radius: 52,   // 第十九轮②：伤害 26×1.2；第十九轮：速度 120×1.1
    color: '#c8862f', shape: 'bottle', behavior: 'meleeBoss', armor: 20,
    xp: 120 * BOSS_LOOT_MUL, materials: 120 * BOSS_LOOT_MUL,
    boss: true, bossName: '依趣苹果醋',
    // 第十八轮 ⑥：**它是唯一漏掉 noCanvasEyes 的 Boss**。
    //
    // 用户原话："给 boss 的眼睛去掉，我们的素材图都有眼睛或者它本身不需要
    //   眼睛（比如苹果醋 boss），目前有眼睛的 boss 会有 4 个眼睛了。"
    //
    // 另外三个 Boss 第十五轮就标了 noCanvasEyes，只有苹果醋漏了 ——
    // 而它恰恰是用户点名"本身不需要眼睛"的那个（瓶身没有脸）。
    // 补上之后 4 个 Boss 都不会再叠 canvas 眼睛。
    // （顺带说明：真正让"4 只眼"显形的是 renderer 里那个 `else` 分支，
    //   第十八轮一并改成了 `else if (!enemyImg)`，见那里的注释。）
    noCanvasEyes: true,
    // 第 20 波的近战 Boss，比派特全面更强：
    // 冲刺更快、弹幕更酸（更多、更慢、留得更久，逼玩家持续移动）。
    attackCooldown: 1.9, attackRange: 280, projectileSpeed: 190, projectileDamage: 15.6,   // 第十九轮②：13 × 1.2
    projectileRange: 1040,   // 第十九轮：弹幕消失范围 ×2（默认 520）
    projectileCount: 7, projectileSpread: 90, summonCount: 4, summonCooldown: 8,
    chargeSpeed: 418, chargeWindup: 0.48, chargeDuration: 0.55,   // 第十九轮：380 × 1.1（包括冲刺）
    // 第十四轮（用户反馈）："依能苹果醋 boss 速度还是太慢了，让它非冲刺攻击的
    // 移动速度 +20%，冲刺距离 +30% 看看。冲刺的距离太短了，虽然冲刺的时候
    // 比玩家快，但是冲刺太短导致他还是没法伤害到我就死了。"
    //
    // 注意用户把两件事分得很清楚：
    //   ① "非冲刺攻击的移动速度" → 只加**贴脸走路**那一段（chaseSpeedMul）。
    //      加在冲刺速度上会变成"冲得更快"，那不是他要的。
    //   ② "冲刺距离" → 只加**距离**，不动速度（chargeDistanceMul 乘在 duration 上）。
    //      用户已经说了"冲刺的时候比玩家快"，速度是够的，缺的是**够不到人**。
    //      所以拉长位移时间：同样 380 的速度多跑 30% 的距离，
    //      单次 209px → 272px，连冲两次 418px → 543px。
    //      玩家仍然能靠横向走位躲开（速度没变，轨迹可读），
    //      但"躲开第一次就安全"不再成立 —— 这正是这个 Boss 的设计意图。
    chaseSpeedMul: 1.2,
    chargeDistanceMul: 1.3,
    // 苹果醋的"招牌"：冲刺**连续两次**（停顿一下再冲一次），
    // 玩家躲开第一次就以为安全，正是第二次的命中时机。
    // 这是"饮料敌人"的性格化设计 —— 酸得**回甘**，后劲在第二口。
    chargeRepeats: 2,
    bulletJitterDeg: [5, 15],
    // ---- 第九十二轮：结晶蛋点缀（液体里的"气泡"）----
    // 飞溅扇每 3 颗抽 1 颗金黄结晶蛋（苹果醋里翻起的气泡）；
    // 三阶段慢速滞留环每隔一颗换成**青苹果色结晶蛋**（"回甘"的签名）。
    // 开背虾不配这些字段 —— 用户点名它保持原样。
    crystalEvery: 3,
    crystalColor: '#ffd166',
    phase3AppleCrystals: { color: '#7bed9f' },
    desc: '第 20 波的最终 Boss（近战型，50% 概率出场）。黏着你不放，停顿即冲刺，还会连冲两次。',
  },

  // =========================================================
  // 第四十九轮：隐藏挑战 Boss —— 芙兰朵露·斯卡蕾特（东方红魔乡 Extra）
  // 素材来自 touhou-flandre 拆包，用户亲自核对了切片编号：
  //   172 = 正式战本体（173~175 = 常态动画循环，第五十轮补入）；
  //   202/205/208 = 抱腿坐姿循环（Extra Phantom）；
  //   192 = 莱万汀攻击姿势（仅 Lævateinn 符卡期间切换）。
  //
  // 这里登记 8 个 id 只为满足"贴图必须对应真实敌人 id"的资产纪律
  //（enemy-sprite-check 的孤儿检查）；真正登场的只有 'flandre'，
  // 其余 7 个是**贴图位**，由战斗控制器（systems/flandre.js）通过
  // 实例上的 spriteId 覆盖来切换，永远不会被刷怪池引用。
  // =========================================================
  {
    id: 'flandre', name: '芙兰朵露·斯卡蕾特',
    // 第八十六轮：总血量 50万 → 80万（用户："把芙兰的总血量改到80万"；
    // 配套每卡 20% 锁血分段见 flandre.js CARD_HP_FRAC）
    hp: 800000, speed: 120, damage: 12, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0,
    xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露·斯卡蕾特',
    // 她的攻击全部由符卡脚本发（flandre:true 敌弹走 30% 真实伤害），
    // Boss AI 的自带弹环必须永久哑火：Infinity 冷却永远不会 <= 0。
    attackCooldown: Infinity,
    noCanvasEyes: true,
    desc: '隐藏挑战 Boss。噩梦难度 20 波通关且重开不超 3 次才会现身。',
  },
  { id: 'flandre_sit', name: '芙兰朵露（抱腿）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: 'Extra Phantom 姿势贴图位（循环帧 202）。',
  },
  // 第五十轮：常态动画循环帧（用户："172-175 的插图其实是个她不移动的
  // 常态动画（只有翅膀再动），把芙兰一动不动变成172-175慢慢循环"）
  { id: 'flandre_idle2', name: '芙兰朵露（常态2）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: '常态动画贴图位（循环帧 173，只有翅膀在动）。',
  },
  { id: 'flandre_idle3', name: '芙兰朵露（常态3）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: '常态动画贴图位（循环帧 174）。',
  },
  { id: 'flandre_idle4', name: '芙兰朵露（常态4）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: '常态动画贴图位（循环帧 175）。',
  },
  { id: 'flandre_sit2', name: '芙兰朵露（抱腿2）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: 'Extra Phantom 姿势贴图位（循环帧 205）。',
  },
  { id: 'flandre_sit3', name: '芙兰朵露（抱腿3）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: 'Extra Phantom 姿势贴图位（循环帧 208）。',
  },
  { id: 'flandre_laevateinn', name: '芙兰朵露（莱万汀）', hp: 500000, speed: 120, damage: 0, radius: 48,
    color: '#ff4060', shape: 'circle', behavior: 'boss', armor: 0, xp: 0, materials: 0,
    boss: true, bossName: '芙兰朵露', attackCooldown: Infinity, noCanvasEyes: true,
    desc: 'Lævateinn 符卡专属攻击姿势贴图位（192）。',
  },
];

export const ENEMY_MAP = Object.fromEntries(ENEMIES.map((e) => [e.id, e]));
export const getEnemy = (id) => ENEMY_MAP[id];

/**
 * 哪些 behavior 算「近战敌人」（第十轮，噩梦移速调参用）。
 *
 * 用户原话："噩梦难度所有敌人移速+10%。近战敌人则是改成+15%"
 *
 * 判据是**这个敌人靠什么造成威胁**，而不是它有没有远程手段：
 *
 *   chase / zigzag / orbit / charger / splitter / exploder / flee
 *       → 纯靠贴身接触伤害，标准近战。
 *
 *   chaseShooter（铁甲猫 / 刃猫）
 *       → **算近战**。这两个是第九轮"给近战敌人加远程能力"的产物
 *         （见上面 tank / blade_cat 的注释），它们的定位仍然是
 *         "一路压上来贴脸"的敌人，只是顺手带了门炮。
 *         如果按"有弹幕就算远程"归类，它们反而会比纯近战慢，
 *         与用户"近战敌人要更快"的意图正好相反。
 *
 *   shooter（吐酸猫 / 混乱蛋 / 螺旋猫 / 毒液女王）
 *       → 远程。它们会主动拉开距离放弹幕，加速反而会让它们
 *         更容易保持安全距离，不是玩家想要的"压迫感"。
 *
 *   boss
 *       → 不算近战。Boss 的血量/弹幕/冲锋是单独调过的，
 *         这里只吃"所有敌人 +10%"那一档。
 */
export const MELEE_BEHAVIORS = new Set([
  'chase', 'zigzag', 'orbit', 'charger', 'splitter', 'exploder', 'flee',
  'chaseShooter',
]);

/** 这个敌人算不算近战（用于噩梦移速的 +15% 档） */
export function isMeleeEnemy(def) {
  if (!def) return false;
  if (def.boss) return false;
  return MELEE_BEHAVIORS.has(def.behavior ?? 'chase');
}


/**
 * 普通敌人 / 精英 / Boss 分类。
 *
 * 注意：这里用 getter 而不是固定的 filter 结果数组。
 * 原来的写法（`export const NORMAL_ENEMIES = ENEMIES.filter(...)`）
 * 在模块加载时就把结果冻结了 —— 之后再往 ENEMIES 里 push 新敌人，
 * 这几个数组不会更新，导致"新加的敌人刷不出来"。
 * 用 getter 每次都重新过滤，扩展数据表时才符合直觉。
 *
 * 宝箱怪（treasure）要排除在普通池之外：它由 world 单独按概率刷新，
 * 不能混进常规刷怪池，否则每波都会满地宝箱怪。
 */
export const getNormalEnemies = () => ENEMIES.filter((e) => !e.elite && !e.boss && !e.treasure);
export const getEliteEnemies = () => ENEMIES.filter((e) => e.elite);
export const getBossEnemies = () => ENEMIES.filter((e) => e.boss);
/** 宝箱怪单独一类 */
export const getTreasureEnemies = () => ENEMIES.filter((e) => e.treasure);

/**
 * 哪些敌人**真的有贴图**（第十轮，第十五轮扩充）。
 *
 * 为什么需要这个函数：Boss 一直没有贴图（程序画的几何形状），
 * 但预加载曾经把整个 ENEMIES 都塞进去，于是每次开局都发两个必定 404 的
 * 请求。坏处不只是浪费带宽 —— 它会往控制台灌错误，把真正的报错淹掉。
 *
 * 这个判断放在数据层而不是 main.js 里，是为了让**预加载和测试读同一份规则**。
 * 第一版把过滤写在 main.js 的调用处，测试直接调 buildPreloadPlan 就绕过了
 * 过滤，于是测试报"缺贴图"而游戏其实是对的 —— 典型的"两份真相"。
 *
 * 注意：这是"我们**知道**没有贴图"的白名单式排除，不是"文件不存在"的
 * 运行时探测（那要发请求才能知道，正是我们要避免的）。
 * 兜底逻辑不变：万一漏了，sprites.get() 返回 null，渲染器照样回落几何形状。
 *
 * ---- 第十五轮 ④ 的改动 ----
 * 用户给第 10 / 20 关的三个 Boss 提供了立绘，所以这里从"排除全部 Boss"
 * 改成"普通敌人全部 + **明确有图的 Boss**"。
 *
 * 为什么用显式白名单而不是 `!e.boss || true`（即全部放行）：
 *   第 20 关的近战 Boss（依趣苹果醋）目前**还没有**立绘。
 *   全部放行会让它每局发一个必定 404 的请求 —— 正是上面那段注释
 *   要避免的问题。所以只有真的有图的才列进来。
 *   以后补图了，往 BOSS_SPRITE_IDS 里加一行即可。
 */
export const BOSS_SPRITE_IDS = new Set([
  'boss_cat_king',    // 第 10 关远程：路过被殴打的猫娘
  'boss_acid_cat',    // 第 10 关近战：蒜蓉粉丝开背虾
  'boss_void_lord',   // 第 20 关远程：黑化开发猫猫
  'boss_pandora',     // 第 10 关远程(新)：格镜魔女·潘多拉（185 轮，几何绘制立绘 tools/make-boss-art.py）
  'boss_nono',        // 第 20 关远程(新)：天幕机兵·诺诺
  // 第四十九轮：芙兰朵露全套（本体 + 常态三帧 + 抱腿坐姿三帧 + 莱万汀姿势）
  'flandre',
  'flandre_idle2',
  'flandre_idle3',
  'flandre_idle4',
  'flandre_sit',
  'flandre_sit2',
  'flandre_sit3',
  'flandre_laevateinn',
]);

export const getSpriteEnemies = () =>
  ENEMIES.filter((e) => !e.boss || BOSS_SPRITE_IDS.has(e.id));

// 兼容旧引用：仍然是数组，但每次访问都重新计算
export const NORMAL_ENEMIES = getNormalEnemies();
export const ELITE_ENEMIES = getEliteEnemies();
export const BOSS_ENEMIES = getBossEnemies();

// ============================================================
// 第十一轮：Boss 的"远程 / 近战"二选一
//
// 用户原话："boss在10/20回合随机50%概率出现，即近战远程boss各50%"
//
// 设计成"每个 Boss 波配一对（远程, 近战），开局时按 50% 抽一个"，
// 而不是"随机从 4 个 Boss 里抽" —— 后者会让第 10 波有概率冒出
// 第 20 波的 Boss（血量 2400 打第 10 波的玩家，直接坏档）。
//
// 抽签**在开局那一刻定死**并记进存档（见 main.js 的 _serializeRun），
// 否则玩家重试同一波会刷出不同的 Boss —— 想打近战就重试到近战为止，
// 那 50% 就形同虚设了。
// ============================================================

/**
 * 每个 Boss 波的备选：`[远程, 近战]`。
 *
 * key 是波次序号（1-based），和 waves.js 的 BOSS 波一一对应。
 * 加新 Boss 波时**必须**在这里也加一条，否则那一波会退回
 * waves.js 里写死的那个（见 pickBossForWave 的兜底）。
 */
export const BOSS_VARIANTS = {
  // rangedAlt（185 轮）：远程名额里的"备选"；远程 50% 里 ranged 与 rangedAlt 平分。
  10: { ranged: 'boss_cat_king', rangedAlt: ['boss_pandora'], melee: 'boss_acid_cat' },
  20: { ranged: 'boss_void_lord', rangedAlt: ['boss_nono'], melee: 'boss_apple_cider' },
};

/** 近战 Boss 的 id 集合（测试和渲染器判断"要不要画蓄力预警线"用） */
export const MELEE_BOSS_IDS = new Set(
  Object.values(BOSS_VARIANTS).map((v) => v.melee),
);

/** 这个 Boss 是不是近战冲锋型 */
export function isMeleeBoss(defOrId) {
  const id = typeof defOrId === 'string' ? defOrId : defOrId?.id;
  return !!id && MELEE_BOSS_IDS.has(id);
}

/**
 * 给某个 Boss 波抽一个 Boss。
 *
 * @param {number} wave1based  波次序号（1-based，10 / 20）
 * @param {object} rng         要有 next() 返回 [0,1)
 * @param {boolean} forceMelee 强制近战（测试用；正常游戏不传）
 * @returns {string|null} Boss id；这一波没有配对的备选时返回 null，
 *   调用方应当回落到 waves.js 里写死的那个 Boss。
 */
export function pickBossForWave(wave1based, rng, forceMelee = null) {
  const pair = BOSS_VARIANTS[wave1based];
  if (!pair) return null;
  // 185 轮：**仍然只抽一次 rng**（随机流消耗与旧版逐位一致，种子复现的存档不会错位）。
  // 把 u ∈ [0,1) 劈成两半：u<0.5 → 近战；否则远程，并用剩下的 0.5 再均分给 ranged 与 rangedAlt。
  // forceMelee 显式传 true/false 时语义不变（false → 主远程 pair.ranged，测试依赖这一点）。
  if (forceMelee !== null) return forceMelee ? pair.melee : pair.ranged;
  const u = rng.next();
  if (u < 0.5) return pair.melee;
  const pool = [pair.ranged, ...(pair.rangedAlt ?? [])];
  const sub = (u - 0.5) * 2;                                  // [0,1)
  return pool[Math.min(pool.length - 1, Math.floor(sub * pool.length))];
}