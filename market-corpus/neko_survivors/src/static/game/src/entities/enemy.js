// ============================================================
// 敌人实体与 AI 行为
// ============================================================

import { Entity } from './entity.js';
import { angleTo, clamp, dist, TAU } from '../core/math.js';
import { BossPhaseState } from '../data/boss-phases.js';
// 第一百八十五轮：新 Boss 的招牌技能（潘多拉·格镜落弹 / 诺诺·天幕横雨+三向落柱）
import { runBossSignature } from '../systems/boss-signatures.js';
import { isMeleeEnemy } from '../data/enemies.js';
import { resolveTerrain } from '../data/terrain.js';
// 第十二轮：远程怪射速/弹速 +20%、近战怪基础血量 +20%（都不含 Boss）
import {
  ENEMY_FIRE_RATE_BUFF,
  ENEMY_BULLET_SPEED_BUFF,
  ENEMY_MELEE_HP_BUFF,
} from '../data/difficulty.js';
// 第一百三十二轮：波次真实伤害附加（6 波起，制裁高护甲/高回血玩家）
import { trueDamageBonus } from '../data/difficulty.js';
import { ELITE_DAMAGE_MUL, BOSS_DAMAGE_MUL } from '../data/waves.js';
// 第一百二十一轮：Boss 射击音效（shoot1 齐射 / shoot2 水柱）。
import { audio } from '../systems/audio.js';
// 第一百三十一轮：Boss 过载减伤（0.5s 掉血 ≥10% maxHp → 一层 50% 乘算减伤）
import { OverloadGuard } from '../systems/overload.js';

/**
 * 第九十二轮：标准 Boss 弹幕的七彩结晶环配色（与芙兰符卡的 RAINBOW 同组）。
 * 用户原话："增加一些结晶蛋进去，让结晶蛋也出现在我们的普通boss战里，
 * 然后好看一些，色彩丰富一些。"
 */
export const BOSS_RAINBOW = ['#ff4060', '#ff9f43', '#ffe066', '#7bed9f', '#4cd7d0', '#70a1ff', '#c56cf0'];

// ============================================================
// 第十四轮 ⑦：宝箱怪「挨打加速」buff
//
// 用户原话：
//   "给宝箱怪加一个挨打加移动速度的 buff，效果为：受到攻击时 +20% 移动速度
//    3 秒，然后速度变为 90% 5 秒。之后可以再次触发该效果。"
//
// 数值刻意写成常量而不是散在代码里的字面量 —— 这样测试可以直接 import
// 它们做断言，不用把 1.2 / 0.9 抄一遍（抄一遍就会在改数值时忘记同步）。
//
// 注意 1.2 和 0.9 是**乘在最终移速上**的系数，不是"速度属性 +20%"。
// 宝箱怪本来就不该被打死得那么轻松，所以先给一段"更难抓"的窗口，
// 再给一段"变慢"的窗口作为玩家追上它的机会 —— 这就是用户描述的两段节奏。
export const MIMIC_BOOST_MUL = 1.2;        // 挨打后：+20% 移速
export const MIMIC_BOOST_DURATION = 3;     // 持续 3 秒
export const MIMIC_SLOW_MUL = 0.9;         // 之后：速度变为 90%
export const MIMIC_SLOW_DURATION = 5;      // 持续 5 秒

// ---- 第十八轮 ⑦：撞墙 / 被地形卡住的检测 ----
//
// 用户原话见 _checkStuck 的注释。这里只说明数值怎么定的：
//
// STUCK_MOVE_RATIO = 0.35
//   实测正常移动时 实际位移/理论位移 ≈ 1.0（有一点点因为侧向摆动 < 1）。
//   完全顶在墙上时 ≈ 0。取 0.35 是为了容忍"沿墙滑行"（那是**有效**移动，
//   不该被判成卡住）—— 沿着墙走的时候两个分量里有一个是满的，
//   合成位移大概是理论的 0.4~0.7，正好在阈值之上。
//
// STUCK_TIME = 0.35 秒
//   按 60fps 是 21 帧。太短会在转身/急停的瞬间误判（那几帧位移确实小），
//   太长则玩家已经把它打死在墙上了。0.35 秒肉眼上就是"愣了一下"。
//
// STUCK_ESCAPE_TIME = 1.2 秒
//   脱困方向的有效期。要够长到真的能挪出卡住的位置，
//   又不能长到让它一直往侧面跑（那样看起来像在绕圈）。
export const STUCK_MOVE_RATIO = 0.35;
export const STUCK_TIME = 0.35;
export const STUCK_ESCAPE_TIME = 1.2;

export class Enemy extends Entity {
  constructor(def, x, y, scaling, rng) {
    super();
    this.def = def;
    this.isEnemy = true;
    this.rng = rng;

    this.x = x; this.y = y;
    this.radius = def.radius ?? 12;

    // 应用波次成长
    // Boss 额外吃 bossGrowth（无限模式的 3 倍成长，普通敌人不吃）
    this.boss = !!def.boss;

    // 近战/远程的分类**必须在算血量之前**定下来（第十二轮）。
    // 原来这段在下面（算完 hp 之后），本轮给近战加基础血量时，
    // 如果还留在原地就会读到 undefined —— 所有怪都算远程，加成静默失效。
    // 这种"顺序耦合"很难从代码上一眼看出来，所以顺手把它提到前面，
    // 让"先分类、再按分类算数值"的顺序显式化。
    this.melee = isMeleeEnemy(def);

    // 第十二轮（用户反馈）：近战怪**基础**血量 +20%，不含 Boss。
    // 乘在 def.hp 上（而不是乘在最终的 scaling.hp 上），这样后续的
    // 波次复利、难度倍率、无限模式 ×2 都会在这 +20% 之上继续乘 ——
    // 正是用户要的"后续难度波次放大多倍之后也会肉一些"。
    const meleeHpBuff = this.melee ? ENEMY_MELEE_HP_BUFF : 1;
    // 第十四轮：Boss 走**单独一条**更缓的血量曲线（waves.js 的 bossHpCurve）。
    // 普通怪那条 23.78% 的复利若用在 Boss 上，第 20 波 void_lord 会有 43.5 万血，
    // 而用户点名要的是 10 万。所以 Boss 读 scaling.bossHp，普通怪读 scaling.hp。
    // 兜底到 scaling.hp 是为了让老测试里手写的 scaling 对象（没有 bossHp 字段）
    // 仍然能跑 —— 那些地方 hp 和 bossHp 本来就该是同一个值。
    const hpBase = this.boss ? (scaling.bossHp ?? scaling.hp) : scaling.hp;
    const hpMul = hpBase * meleeHpBuff * (this.boss ? (scaling.bossGrowth ?? 1) : 1);
    this.maxHp = (def.hp ?? 10) * hpMul;
    this.hp = this.maxHp;
    // 移速：近战敌人走单独一档（噩梦 +15% vs 全体 +10%，见 difficulty.js）。
    const speedScaling = this.melee ? (scaling.speedMelee ?? scaling.speed) : scaling.speed;
    // 第十九轮（移速成长）：非 Boss 敌人每波额外 +1% **自身**速度
    // （用户原话："给所有难度敌人增加移速成长。每回合加快他自身速度的1%，
    // boss不享用"）。叠在 waves.js 原有的全局波次成长之上；排除放在
    // 构造函数里是结构性的 —— 普通/精英/Boss/召唤所有生成路径都过这里，
    // 按 def.boss 判，不靠调用方自觉。
    const ownGrowth = this.boss ? 1 : (scaling.speedOwn ?? 1);
    // 第十九轮末（移除速度上限）：原来的绝对上限 ENEMY_SPEED_CAP(200) 整个
    // 移除 —— 用户原话："移除敌人速度上限的设置，因为玩家后续发育之后
    // 移动速度也会更快。如果后续敌人移速超过215（我们的新默认速度）导致
    // 玩家被围攻，那也是玩家不愿意升级速度的原因"。成长叠加下后期敌人
    // 比玩家默认快是被接受的设定；scaling 里的 cap 字段从此无人消费
    // （world.js / 难度表 / 测试里的同步删除见各自文件的说明）。
    this.speed = (def.speed ?? 60) * speedScaling * ownGrowth;
    // 第二十八轮：精英/Boss 的伤害走独立乘区（锚点见 waves.js 注释）。
    // 第二十九轮：乘区结果存成 dmgScale —— **弹幕/水柱从此也吃这套加成**
    //（用户："原来弹幕不吃加成，那我知道问题在哪了……等于折腾了一圈
    // 让疼的更疼，不疼的还是不疼"）。之前只有接触伤害随波次成长，
    // 弹幕永远是基础值：噩梦 20 波吐酸猫弹幕还是 4 点，隔靴搔痒。
    // 小怪不吃精英/Boss 乘区。
    const dmgRoleMul = def.boss ? BOSS_DAMAGE_MUL : def.elite ? ELITE_DAMAGE_MUL : 1;
    this.dmgScale = scaling.damage * dmgRoleMul;
    this.damage = (def.damage ?? 3) * this.dmgScale;
    // 第三十轮：弹幕射程成长（波次曲线，全难度通用，见 waves.js 注释）。
    // 小怪默认射程 520，第 20 波 ×2.52 = 1310px，恰好飞过整屏 1280。
    this.rangeScale = scaling.bulletRange ?? 1;
    this.armor = def.armor ?? 0;
    this.xpValue = Math.max(1, Math.round((def.xp ?? 1) * scaling.xp));
    this.materialValue = Math.max(1, Math.round((def.materials ?? 1) * scaling.materials));

    this.color = def.color;
    this.shape = def.shape ?? 'circle';
    this.behavior = def.behavior ?? 'chase';
    this.elite = !!def.elite;
    // 第一百五十三轮：行为异常（"怪菌"）—— 波次刷怪时由 world._rollQuirk
    // 掷骰写入：null=正常 | 'melee'=套近战AI | 'ranged'=套远程AI |
    // 'idle'=摸鱼挂机 | 'flee'=摸鱼被打后学宝箱怪逃跑。update 的行为
    // 分派优先读它；变异体（mutant）不参与掷骰。
    this.aiOverride = null;
    this.mutant = false;

    // 状态
    this.facing = 0;
    this.attackCooldown = rng.range(0, def.attackCooldown ?? 1);
    this.hurtFlash = 0;
    // 表情计时（第十轮）：宝箱怪挨打后 >< 的持续时间。
    // 和 hurtFlash 分开 —— 那个只有 0.12s，做表情太短了。
    this.hurtExpr = 0;
    this.knockbackX = 0;
    this.knockbackY = 0;
    this.wobblePhase = rng.range(0, TAU);
    this.state = 'idle';       // charger 用
    this.stateTimer = 0;
    this.summonTimer = def.summonCooldown ? def.summonCooldown * 0.6 : 0;
    this.splitDone = false;
    this.spawnAnimation = 0.28; // 出生放大动画
    this.scale = 0.2;

    // 控制状态（猫王战锤等重击武器会施加）
    this.stun = 0;          // 僵直剩余秒数：>0 时不动也不攻击
    this.armorShred = 0;    // 护甲削减比例 0~1

    // ---- 奶妈怪的增益状态（第十二轮）----
    //
    // 被奶妈怪治疗过的敌人会拿到一个限时 buff：攻击 +20% / 30% 固定减伤。
    // 存在**被治疗者自己身上**（不是奶妈身上），因为一个奶妈会同时
    // 奶好几只怪，而每只怪的 buff 剩余时间是独立的。
    //
    // buffDamagePct / buffDamageReduction 是给 takeDamage 和接触伤害
    // 读取的两个字段。默认 0 = 没 buff。
    this.buffTimer = 0;
    this.buffDamagePct = 0;
    this.buffDamageReduction = 0;
    // 奶妈自己的治疗节拍（每 healInterval 结算一次）
    this.healTick = 0;
    // 给渲染用：刚刚治疗过的闪光（0~1 递减）
    this.healFlash = 0;

    // Boss 三阶段（血量/时间双触发，见 data/boss-phases.js）
    this.bossPhase = this.boss ? new BossPhaseState() : null;
    // 第一百三十一轮：Boss「过载减伤」（含芙兰 —— 她也是 boss:true 的
    // Enemy，自动继承）。0.5s 内实际掉血 ≥ 10% maxHp → 一层 50% 乘算
    // 减伤，每层独立计时 5 秒。时钟用 this.overloadNow（update 里累加），
    // 注入式设计让 round131 测试能精确复现"连打 5 秒叠几层"。
    this.overload = this.boss ? new OverloadGuard(this.maxHp) : null;
    this.overloadNow = 0;
    this.overloadFlash = 0;   // 触发新层时的金红闪光（渲染层读）
    this.chargeTimer = 3;   // Boss 冲锋计时器（二阶段起使用）
    // 第十一轮：近战 Boss 的"连续冲刺"剩余次数（依趣苹果醋 = 2，会连冲两次）
    this._chargeRepeatsLeft = 0;

    // ---- 第十八轮 ⑦：撞墙检测（"逃跑的怪会一直撞墙移不动"）----
    //
    // 用户原话："给喜欢远离玩家的怪物判定，移动距离和速度严重不匹配的时候
    //   换个方向移动。我的意思是他们现在逃跑的时候如果撞到了墙壁和障碍
    //   就会一直撞墙完全移不动了。加个检测让她撞墙就换方向，不好做的话
    //   就换成她自检速度和移动距离不匹配（指撞了墙）的时候换个方向继续逃
    //   而不是在墙壁上任玩家宰割。"
    //
    // 用户给了两个方案，第二个（速度 vs 实际位移不匹配）是**更通用**的：
    // 它同时覆盖了世界边界墙和地形障碍（resolveTerrain 把怪推回来时
    // 速度不变但位移为 0），而"撞墙"字面检测只覆盖前者。
    // 所以这里实现第二个，效果上等于"撞到什么就换方向"。
    //
    // 实现：每帧记下实际位移，和"按 this.speed 本该走的距离"比。
    // 连续 stuckTime 秒都不匹配（比值 < STUCK_MOVE_RATIO）就判定卡住，
    // 给一个随机的**侧向**脱困方向，持续 STUCK_ESCAPE_TIME 秒。
    this._stuckTimer = 0;        // 连续"动不了"的累计时间
    this._escapeTimer = 0;       // 脱困方向还要用多久
    this._escapeX = 0;
    this._escapeY = 0;

    // ---- 第十五轮 ④：虾 Boss 的"猛退 + 水柱"状态机 ----
    //
    // 用户原话："三阶段加一个突然停止攻击然后猛的后退一大截然后同时射出水柱
    //            （类似激光水柱，很快）攻击玩家的动作。"
    //
    // 拆成三个子阶段，用 jetPhase 表示：
    //   null      → 没在放，正常贴脸
    //   'retreat' → 猛退（位移由 jetDir 决定，速度 = 距离/时长）
    //   'windup'  → 退到位后短暂停顿（蓄势，也是玩家的预警窗口）
    //   发完水柱回到 null，并进入 jetCd 冷却
    //
    // 为什么要有 windup 这一档：用户要的是"突然"后退 + 水柱，
    // 但**完全没有预警的高速弹**在弹幕游戏里属于不可躲伤害。
    // 0.22 秒的停顿 + 一道亮线预告，既保留"突然"的观感，又留出反应余地。
    this.jetPhase = null;
    this.jetTimer = 0;
    this.jetCd = 0;
    this.jetDirX = 0;
    this.jetDirY = 0;

    // ---- 第十六轮：库洛「毒舌猫娘」的 debuff ----
    //
    // 三个乘数 + 一个计时器，全部由 CharRuntime 写入（见 char-runtime.js）。
    // **不可叠加**的实现方式：中招时直接覆盖计时器，不累加倍率。
    // 计时器归零时一次性清干净 —— 和奶妈 buff 同一个套路（见下面 buffTimer），
    // 留一个残留的 0.8 会让敌人永久变弱。
    this.curseTimer = 0;
    this.curseDamageMul = 1;
    this.curseSpeedMul = 1;
    this.curseRateMul = 1;

    // ---- 宝箱怪：游荡 / 逃跑 状态机 ----
    // 用户第四轮："怪物是默认游荡在地图里，'看到'玩家（距离稍近）
    // 或者受到攻击时主动远离玩家，在一段时间后重新回归随机游荡模式
    // 而不要一开始出现就远离玩家。"
    // 第一百五十三轮：这四个字段改为**全员初始化** —— 行为异常
    // （aiOverride='flee'）会让普通小怪也走 _flee，而 alert() 里
    // Math.max(this.fleeTimer, seconds) 遇到 undefined 会算出 NaN，
    // NaN > 0 恒假 → 逃逸 AI 完全失效（怪原地呆住）。宝箱怪专属的
    // 加减速计时器仍留在下面的 def 判定里。
    this.wanderAngle = this.rng.next() * TAU;
    this.wanderTimer = 0;
    this.fleeTimer = 0;      // >0 表示正在逃跑
    this.alerted = false;    // 是否已经"看到"玩家（进入过逃跑状态）
    if (this.def.behavior === 'flee') {
      // ---- 第十四轮 ⑦：挨打加速 ----
      // 用户原话："给宝箱怪加一个挨打加移动速度的 buff，效果为：受到攻击时
      // +20% 移动速度 3 秒，然后速度变为 90% 5 秒。之后可以再次触发该效果。"
      //
      // 两个计时器而不是一个"阶段"变量：
      //   两个阶段的**效果是不同性质**的（加速 vs 减速），
      //   用一个变量表达要额外判断"现在是哪段"，反而更容易写错。
      //   而且 boost 结束时 slow 必须**正好从那一刻**开始计时，
      //   所以 slow 的时长是在 boost 归零的那一帧才赋值的。
      this.mimicBoostTimer = 0;   // >0：+20% 移速
      this.mimicSlowTimer = 0;    // >0：90% 移速
    }
  }

  get hpPercent() { return clamp(this.hp / this.maxHp, 0, 1); }

  /**
   * 第十六轮：毒舌 debuff 换算出来的**冷却倍率**。
   *
   * 用户说的是"攻速 -20%"。攻速降 20% ⇔ 冷却变成 1/0.8 = 1.25 倍，
   * 不是冷却 ×0.8（那是**加快**攻击，正好反了）。
   *
   * 单独开一个 getter 而不是在 4 处调用点各写一次 `/ this.curseRateMul`：
   * 这个方向的换算很容易写反，而且反了以后表现是"被骂之后打得更凶"，
   * 玩家会以为技能是负收益。收成一处，改一次就够。
   */
  get curseCooldownMul() {
    const m = this.curseRateMul;
    return m > 0 && m !== 1 ? 1 / m : 1;
  }

  /**
   * 第一百三十二轮：本敌人当前应附加的真实伤害。
   * 接触/自爆直接用；弹幕在发射时把档位（trueTier）记到弹上，
   * 命中时按**当时**的波次算（见 bullet.js）。
   * 芙兰战全程排除 —— 她已经走 22% 最大生命值真伤体系，不叠加固定真伤。
   */
  trueBonusFor(world) {
    if (world?.flandreFight) return 0;
    const tier = this.boss ? 3 : this.elite ? 2 : 1;
    return trueDamageBonus(world?.currentWave ?? 0, tier);
  }

  /**
   * 弹幕档位标记：1 小怪 / 2 精英 / 3 Boss。
   * spawnEnemyBullet 的调用方把它塞进 cfg（trueTier），弹命中时用。
   */
  get trueTier() {
    return this.boss ? 3 : this.elite ? 2 : 1;
  }

  takeDamage(amount, opts = {}) {
    // 幽灵闪避
    if (this.def.dodge && this.rng.next() < this.def.dodge) {
      return { dodged: true, dealt: 0 };
    }

    // ---- Boss 锁血（第九轮用户要求）----
    // 登场 5 秒 / 2 阶段 5 秒 / 3 阶段 5 秒无敌，
    // 外加 33% 和 66% 两个卡点各锁一次。
    // 这样"数值再高"也要分至少 3 轮才打得死 Boss。
    let blocked = false;
    if (this.bossPhase) {
      const lock = this.bossPhase.applyDamageLock(this.hp, this.maxHp, amount);
      if (lock.blocked) {
        this.bossPhase.lastBlocked = 0.1;
        // 免疫反馈：不打伤害数字，只弹一个"免疫"提示
        return { blocked: true, dodged: false, dealt: 0, killed: false, reason: lock.reason };
      }
      if (lock.gated) {
        this.bossPhase.lastBlocked = 0.1;
        // 被卡点截断：血量停在这一档，但**仍然算作一次有效命中**
        amount = lock.dmg;
      }
      blocked = !!lock.gated;
    }

    let dmg = amount;
    if (!opts.pierceArmor && this.armor > 0) {
      // 破甲：猫王战锤这类武器会先削掉一部分护甲再结算
      const effArmor = this.armor * (1 - (this.armorShred || 0));
      dmg = Math.max(1, dmg * (1 / (1 + effArmor / 20)));
    }

    // ---- 第一百三十一轮：Boss 过载减伤（独立乘算层）----
    //
    // 位置在护甲与奶妈减伤之后、扣血之前：过载是对"实际掉血速度"的
    // 限流，与护甲/固定减伤各自独立乘算。层数越高伤害越小（0.5^N），
    // 但永远 > 0 —— 乘算叠加防无敌，符合用户设计。
    let overloaded = false;
    if (this.overload) {
      this.overload.tick(this.overloadNow);
      dmg = Math.max(1, dmg * this.overload.multiplier);
    }

    // ---- 第一百三十四轮：超载芯片 —— 受到伤害 +1%×层数（最多 30%）----
    // 玩家攻击命中时叠层（world.dealDamage），放在护甲之后：护甲先减，
    // 易伤再放大 —— 与"受到的伤害 +1%"的字面一致。
    if ((this.debuffTakenPct ?? 0) > 0) {
      dmg *= 1 + this.debuffTakenPct / 100;
    }
    // ---- 奶妈怪给的固定减伤（第十二轮）----
    //
    // 用户要的是"30% 固定减伤"。这里刻意用**乘算的独立一层**，
    // 而不是加进 armor 里：
    //   护甲走的是 1/(1+armor/20) 这条递减曲线，同样的 30 点护甲
    //   对高甲怪收益远小于低甲怪。而用户说的是"固定减伤"——
    //   不管目标本来有多少甲，都精确少挨 30%。
    //   叠在护甲**之后**，两层各自独立，语义清楚。
    //
    // 夹到 0.9 上限：固定减伤如果叠满 100% 就变成无敌，
    // 玩家永远打不死被奶的怪。留 10% 是"能打但很肉"。
    if (this.buffDamageReduction > 0) {
      const red = Math.min(0.9, this.buffDamageReduction);
      dmg = Math.max(1, dmg * (1 - red));
    }

    this.hp -= dmg;
    this.hurtFlash = 0.12;

    // ---- 第一百五十三轮：摸鱼怪被打醒 ----
    // 用户："摸鱼（直接挂机不动，挨打的时候50%概率恢复正常ai，50%概率
    // 用宝箱怪ai，就是会不停逃离玩家。）"——每次挨打独立掷骰。
    if (this.aiOverride === 'idle') {
      this.aiOverride = this.rng.next() < 0.5 ? null : 'flee';
    }

    // ---- 第一百三十一轮：过载层记账（用**实际掉血**触发）----
    // 减伤生效后掉血变慢，层数增长自然放缓 —— 机制自我平衡。
    // 触发新层 → 金红闪光计时器（渲染层读 overloadFlash 画护盾特效）。
    if (this.overload) {
      if (this.overload.recordDamage(this.overloadNow, dmg)) {
        this.overloadFlash = 0.35;
        overloaded = true;
      }
    }
    // 表情计时（第十轮）：比受击闪白长得多。
    // hurtFlash 只有 0.12 秒，是给"整只怪刷白"用的，快到几乎看不见；
    // 而 >< 这种表情需要 **0.5 秒**玩家才能看清"她哎呀了一下"。
    // 所以单独一个计时器，不共用。
    this.hurtExpr = 0.5;
    // 宝箱怪被打了就立刻进入逃跑状态（用户要求："或者受到攻击时主动远离玩家"）
    if (this.def.behavior === 'flee') {
      this.alert(3.5);
      // 第十四轮 ⑦：挨打加速。用户要求"之后可以再次触发该效果"——
      // 所以每次命中都**重置**两个计时器（而不是只在 boost 未激活时才触发）。
      // 重触发时 slow 被清掉，重新从 3 秒的加速开始，
      // 于是"连续挨打"表现为一直在加速段，这是符合直觉的：
      // 玩家越打它跑得越快，逼玩家必须一次性打死或先控住。
      this.mimicBoostTimer = MIMIC_BOOST_DURATION;
      this.mimicSlowTimer = 0;
    }
    if (this.hp <= 0) {
      this.hp = 0;
      this.dead = true;
      return { killed: true, dealt: dmg, gated: blocked, overloaded };
    }
    return { killed: false, dealt: dmg, gated: blocked, overloaded };
  }

  applyKnockback(fromX, fromY, force) {
    const a = angleTo(fromX, fromY, this.x, this.y);
    const mass = this.boss ? 8 : this.elite ? 3 : (this.def.radius ?? 12) / 12;
    // 第三十七轮：精英吃到的击退再 -50% ——
    // 用户原话："猫王战锤的击退太强力了，站着不动没有任何怪可以靠近，
    // 让精英怪吃到的击退-50%，让精英近战怪有接近可能。小怪无法接近
    // 是对的。毕竟是传说武器。"
    // 小怪照旧全额吃（传说武器清群的排面保留），Boss 另有 mass 8。
    const eliteResist = this.elite ? 0.5 : 1;
    this.knockbackX += Math.cos(a) * force * eliteResist / mass;
    this.knockbackY += Math.sin(a) * force * eliteResist / mass;
  }

  /**
   * @param {number} dt
   * @param {Player} player
   * @param {World} world
   */
  update(dt, player, world) {
    this.age += dt;
    // 第一百三十一轮：闪光先衰减、过载后置位 —— 新结算出的层当帧就有满值闪光
    if (this.overloadFlash > 0) this.overloadFlash = Math.max(0, this.overloadFlash - dt);
    // 过载减伤的时钟推进 + 窗口滚动结算 + 到期层清理
    if (this.overload) {
      this.overloadNow += dt;
      if (this.overload.update(this.overloadNow)) {
        this.overloadFlash = 0.35;   // 停火时窗口结算出的新层也闪
      }
    }
    if (this.spawnAnimation > 0) {
      this.spawnAnimation -= dt;
      this.scale = clamp(1 - this.spawnAnimation / 0.28, 0.2, 1);
    } else {
      this.scale = 1;
    }
    if (this.hurtFlash > 0) this.hurtFlash -= dt;
    if (this.hurtExpr > 0) this.hurtExpr -= dt;
    if (this.stun > 0) this.stun -= dt;

    // ---- 第十四轮 ⑦：宝箱怪挨打 buff 的两段计时 ----
    // 先走加速段；加速归零的那一帧**才开始**减速段的 5 秒。
    // 顺序不能反：如果并行倒计时，两段会重叠，速度系数互相打架。
    if (this.mimicBoostTimer > 0) {
      this.mimicBoostTimer -= dt;
      if (this.mimicBoostTimer <= 0) {
        this.mimicBoostTimer = 0;
        this.mimicSlowTimer = MIMIC_SLOW_DURATION;
      }
    } else if (this.mimicSlowTimer > 0) {
      this.mimicSlowTimer -= dt;
      if (this.mimicSlowTimer <= 0) this.mimicSlowTimer = 0;
    }

    const toPlayerX = player.x - this.x;
    const toPlayerY = player.y - this.y;
    const distToPlayer = Math.hypot(toPlayerX, toPlayerY) || 1;
    this.facing = Math.atan2(toPlayerY, toPlayerX);

    // ---- 第一百五十六轮：特殊波「内部混乱」----
    // 用户："该模式下所有近战怪会对远程怪造成伤害，远程怪会对近战怪造成
    // 伤害。两边ai互相敌对（不影响本身敌对玩家，也就是靠近玩家还是会打
    // 玩家）。"
    // 实现：近战怪把**最近的远程怪**当追击目标（走到哪都撞它，见下面的
    // 内讧接触伤害）；远程怪的站位/开火方向朝**最近的近战怪**（弹幕带
    // 阵营标记，world 侧会让它打怪）。facing 仍看玩家（眼睛照旧盯着你，
    // 保留"敌对玩家"的原味）。变异怪 / Boss / 宝箱怪不参与内讧。
    let aimX = toPlayerX;
    let aimY = toPlayerY;
    let aimDist = distToPlayer;
    let aimFoe = null;
    if (world?.specialWave === 'chaos' && !this.mutant && !this.boss
      && !this.isChest && this.behavior !== 'flee' && this.behavior !== 'healer'
      && this.behavior !== 'exploder') {
      aimFoe = this._nearestFoe(world);
      if (aimFoe) {
        aimX = aimFoe.x - this.x;
        aimY = aimFoe.y - this.y;
        aimDist = Math.hypot(aimX, aimY) || 1;
      }
    }

    // ---- 第二十五轮：Boss 无敌倒计时提到这里统一走 ----
    // 用户 bug 报告："蒜蓉粉丝开背虾……他的无敌时间变成了无限并且不攻击
    // 了"，实测补充："只要远离虾，虾就动起来并且可以攻击和掉血了……可能
    // 是近战武器的效果导致他卡在无敌状态了"。
    // 根因：近战武器的 stun 命中后，上面的僵直分支会提前 return，而
    // bossPhase.tick(dt)（登场/转阶段无敌的倒计时）原来只藏在
    // _meleeBoss/_boss 里 —— 贴脸连续近战的 Boss 被无限刷新僵直，
    // 无敌永远不减、行为全停；跑远了打不到，僵直才自然到期。
    // 无敌倒计时属于"世界时间"，跟当前行为无关，放进 update() 每帧必走。
    this.bossPhase?.tick(dt);

    // 僵直中：不动、不攻击，但仍要处理击退位移，
    // 否则被锤飞的敌人会瞬间定在原地，手感很怪。
    // （第三十八轮：击退位移自带边界/墙壁约束，晕着的怪也不会被锤出图。）
    if (this.stun > 0) {
      this._applyKnockback(dt, world);
      return;
    }

    // ---- 第十八轮 ⑦：撞墙检测的"帧前"部分 ----
    // 记下这一帧的起点，行为跑完之后拿实际位移来对账（见 _checkStuck）。
    // 脱困计时也在这里递减，这样"该往哪走"在行为分派时已经是最终值。
    if (this._escapeTimer > 0) this._escapeTimer -= dt;
    const frameStartX = this.x;
    const frameStartY = this.y;

    // ---- 行为分派 ----
    // 第一百五十三轮：行为异常优先 —— aiOverride 是"怪菌"给这只怪套上的
    // 别人 AI（'melee'/'ranged'）或故障态（'idle' 摸鱼挂机）。_flee 是
    // 现成的宝箱怪逃跑逻辑，摸鱼被打后 50% 切过去（见 takeDamage）。
    // ---- 行为分派 ----
    // 第一百五十六轮：分派参数统一用 aim*（内部混乱时是内讧目标的方向/
    // 距离，其余时刻就是玩家的）—— 追击、站位、开火方向全部跟着目标走。
    // 'boss'/'meleeBoss' 不会出现在 chaos（上面已过滤），参数换不换无感。
    switch (this.aiOverride === 'idle' ? '_idle' : (this.aiOverride ?? this.behavior)) {
      // 摸鱼：原地发呆什么都不做。后面的击退/边界/地形/接触伤害照常跑 ——
      // 它还是一只怪，只是"脑子坏了"，撞到玩家照样烫。
      case '_idle':     break;
      // 异常互换：近战怪被塞了远程 AI（保持距离+放弹），远程怪被塞了近战
      // AI（闷头贴脸）。_shooter/_chase 的 def 字段全部有 ?? 缺省，
      // 跨类型套用不需要额外配置。'flee'（摸鱼被打后学宝箱怪逃跑）复用
      // 下面原有的 case 'flee' 分支，不在此重复。
      case 'melee':     this._chase(dt, player, aimDist, aimX, aimY); break;
      case 'ranged':    this._shooter(dt, player, aimDist, aimX, aimY, world); break;
      case 'chase':     this._chase(dt, player, aimDist, aimX, aimY); break;
      case 'tank':      this._chase(dt, player, aimDist, aimX, aimY); break;
      case 'zigzag':    this._zigzag(dt, player, aimDist, aimX, aimY); break;
      case 'orbit':     this._orbit(dt, player, aimDist, aimX, aimY); break;
      case 'shooter':   this._shooter(dt, player, aimDist, aimX, aimY, world); break;
      // 第九轮新增：近战 + 远程混合（压上来的同时放弹幕，不能靠风筝躲）
      case 'chaseShooter': this._chaseShooter(dt, player, aimDist, aimX, aimY, world); break;
      case 'charger':   this._charger(dt, player, aimDist, aimX, aimY); break;
      case 'exploder':  this._exploder(dt, player, aimDist, aimX, aimY, world); break;
      case 'splitter':  this._chase(dt, player, aimDist, aimX, aimY); break;
      case 'boss':      this._boss(dt, player, distToPlayer, toPlayerX, toPlayerY, world); break;
      // 第十一轮：近战冲锋型 Boss（酸液猫将 / 依趣苹果醋）。
      // 和 'boss' 的根本区别：那个保持 200px 中距离放弹幕，
      // 这个**主动贴脸**，玩家躲不掉，只能靠走位拉开。
      case 'meleeBoss': this._meleeBoss(dt, player, distToPlayer, toPlayerX, toPlayerY, world); break;
      // 第十二轮：奶妈怪 —— 保持距离、AOE 治疗友军、给友军上 buff。
      // 注意它**不追人**，所以不会像别的怪一样堆在玩家脸上。
      case 'healer':    this._healer(dt, player, distToPlayer, toPlayerX, toPlayerY, world); break;
      case 'flee':      this._flee(dt, player, distToPlayer, toPlayerX, toPlayerY, world); break;
      default:          this._chase(dt, player, aimDist, aimX, aimY); break;
    }

    // ---- 第十八轮 ⑦：撞墙检测的"帧后"部分 ----
    this._checkStuck(dt, frameStartX, frameStartY);

    // ---- 奶妈 buff 的倒计时（第十二轮）----
    //
    // 放在行为分派**之后**统一处理，而不是在 _healer 里：
    // buff 是挂在**被治疗的怪**身上的，而那些怪大多数这一帧走的
    // 是 _chase / _shooter 之类的分支 —— 在 _healer 里递减只能减到
    // 奶妈自己那一份，被奶的怪永远不会掉 buff。
    if (this.buffTimer > 0) {
      this.buffTimer -= dt;
      if (this.buffTimer <= 0) {
        // 到点一次性清干净，避免残留一个 >0 的减伤让怪永久变肉
        this.buffTimer = 0;
        this.buffDamagePct = 0;
        this.buffDamageReduction = 0;
      }
    }

    // ---- 第十六轮：毒舌 debuff 的倒计时 ----
    // 和奶妈 buff 同一个位置、同一个理由：debuff 挂在被骂的怪身上，
    // 而它这一帧可能走的是任何一个行为分支，写在分支里必然漏。
    if (this.curseTimer > 0) {
      this.curseTimer -= dt;
      if (this.curseTimer <= 0) {
        this.curseTimer = 0;
        this.curseDamageMul = 1;
        this.curseSpeedMul = 1;
        this.curseRateMul = 1;
      }
    }
    if (this.healFlash > 0) this.healFlash -= dt * 2.5;

    // ---- 击退衰减（第三十八轮起带边界/墙壁约束）----
    this._applyKnockback(dt, world);

    // ---- 世界边界（软约束，Boss 不限制） ----
    if (!this.boss) {
      const half = world.halfSize - this.radius;
      this.x = clamp(this.x, -half, half);
      this.y = clamp(this.y, -half, half);
    }

    // ---- 不可通行地形（第十二轮）----
    //
    // 放在世界边界**之后**：先保证没跑出竞技场，再解决和墙的穿插。
    // 顺序反过来的话，贴边的障碍物可能把敌人推出竞技场。
    //
    // 记下移动前的位置（this.x/y 在本函数开头已经加过速度），
    // 用 _prevTerrainX/_prevTerrainY 保存"这一帧速度施加前"的位置。
    // 没保存过就直接用当前位置 —— resolveTerrain 会用"推到最近边"
    // 的退法，同样不会卡住。
    if (world.terrain) {
      const px = this._terrainPrevX ?? this.x;
      const py = this._terrainPrevY ?? this.y;
      if (resolveTerrain(world.terrain, this, px, py)) {
        // 撞墙后清掉撞墙方向的速度分量，否则敌人会一直"贴着墙使劲顶"，
        // 表现为抖动。清掉之后它会沿墙滑行，看起来是正常的寻路。
        this.vx *= 0.2;
        this.vy *= 0.2;
      }
    }

    // ---- 接触伤害 ----
    if (this.damage > 0 && this.behavior !== 'exploder') {
      const rr = this.radius + player.radius;
      if (distToPlayer < rr && player.invuln <= 0) {
        // 奶妈 buff 的"攻击 +20%"在这里生效（接触伤害这条路径）。
        // 远程怪的弹幕伤害在 _firePattern 里另算（见那里的 damage 计算）。
        // 第十六轮：毒舌的"伤害 -20%"同样在这里乘（远程那条见 _firePattern）。
        // 第一百零八轮：雾雨"所有人造成的伤害+25%"乘在这里；浓雾的
        // miss 也判在这里（走天气独立随机流，不占主流）。
        const wxW = world?.weather;
        let dmg = this.damage * (1 + this.buffDamagePct / 100) * this.curseDamageMul;
        if (wxW) {
          if (wxW.enemyOut !== 1) dmg *= wxW.enemyOut;
          if (wxW.miss > 0) {
            // 浓雾里敌人也会打空 —— 挥空即可，不给玩家无敌帧
            if (wxW.rng.next() * 100 < wxW.miss) return;
          }
        }
        // 第一百三十二轮：接触伤害附加波次真伤（6 波起，无视护甲直扣 HP）
        player.takeDamage(dmg, this.x, this.y, { trueBonus: this.trueBonusFor(world) });
        // 撞击后自己也退一点（台风天全场免击退，这一下也免）
        if (!(wxW?.noKnockback)) this.applyKnockback(player.x, player.y, 40);
      }
    }

    // ---- 第一百五十六轮：内部混乱的内讧接触伤害 ----
    // 近战怪贴到远程怪身上也造成伤害（0.5s 一跳，防每帧结算秒杀）；
    // 对怪伤害走 takeDamage，不吃玩家侧的天气/击退那套。
    if (aimFoe && this.damage > 0 && this.behavior !== 'exploder') {
      this._chaosHitCd = Math.max(0, (this._chaosHitCd ?? 0) - dt);
      const rrFoe = this.radius + aimFoe.radius;
      if (this._chaosHitCd <= 0 && aimDist < rrFoe) {
        aimFoe.takeDamage(this.damage, {});
        this._chaosHitCd = 0.5;
        world?.fx?.nova?.(aimFoe.x, aimFoe.y, 14, '#ffb0a0');
      }
    }
  }

  /**
   * 第一百五十六轮：内部混乱 —— 找最近的对立阵营怪。
   * 近战怪找最近的远程怪，远程怪找最近的近战怪。变异怪 / Boss / 宝箱 /
   * 奶妈 / 宝箱怪不参与（它们有更重要的身份）。带 0.25s 缓存：
   * 全场怪两两求距离每帧跑太浪费，0.25s 内方向够新。
   */
  _nearestFoe(world) {
    const now = world?.t ?? performance.now() / 1000;
    if (this._foeCache && !this._foeCache.dead
      && now - this._foeCacheT < 0.25) {
      return this._foeCache;
    }
    let best = null;
    let bd = Infinity;
    for (const e of world.enemies.items) {
      if (e === this || e.dead || e.mutant || e.boss || e.isChest) continue;
      if (e.melee === this.melee) continue;                 // 同阵营跳过
      if (e.def?.behavior === 'flee' || e.def?.behavior === 'healer') continue;
      const d = (e.x - this.x) ** 2 + (e.y - this.y) ** 2;
      if (d < bd) { bd = d; best = e; }
    }
    this._foeCache = best;
    this._foeCacheT = now;
    return best;
  }

  /** 击退位移与衰减（僵直时也要跑，否则被击飞的敌人会定住） */
  _applyKnockback(dt, world) {
    if (this.knockbackX === 0 && this.knockbackY === 0) return;
    this.x += this.knockbackX * dt;
    this.y += this.knockbackY * dt;
    // ---- 第三十八轮：击退不可越界（墙壁和边界都不可被击退进入）----
    // 用户原话："猫王战锤的击退会把敌人击退出地图外，可以给墙壁和边界
    // 加个不可被击退进入的设计吗？"
    // 原来的边界 clamp 写在 update() 后段还带 `if (!this.boss)`：
    // 被锤**晕**的怪走僵直分支提前 return 直接跳过它（战锤正好带
    // 0.9s 晕），Boss 更是从不被 clamp —— 大力击退就这么把怪送出图。
    // 现在位移后立刻夹回竞技场并做地形解算，对所有敌人生效。
    if (world) {
      const half = world.halfSize - this.radius;
      this.x = clamp(this.x, -half, half);
      this.y = clamp(this.y, -half, half);
      if (world.terrain) {
        resolveTerrain(world.terrain, this, this.x, this.y);
      }
    }
    const decay = Math.exp(-8 * dt);
    this.knockbackX *= decay;
    this.knockbackY *= decay;
    if (Math.abs(this.knockbackX) < 1) this.knockbackX = 0;
    if (Math.abs(this.knockbackY) < 1) this.knockbackY = 0;
  }

  // ---------------------------------------------------------
  /**
   * 按方向移动一帧。
   *
   * @param {number} [speedOverride] 覆盖 this.speed（px/s）。
   *   第十五轮 ④ 加的：虾 Boss 的"猛退"要按 距离/时长 算出速度，
   *   而那个速度**远高于**它的常规移速（420px / 0.28s ≈ 1500px/s），
   *   不该和寻路移速共用一个字段。
   *   只在"这是脚本化的位移、不是自主寻路"时才该用它。
   */
  _move(dt, dirX, dirY, speedOverride) {
    // 记录"这一帧位移之前"的位置，给后面的地形碰撞解算用。
    // 必须在**每一次** _move 开头写，而不是每帧开头写一次 ——
    // 有的行为一帧里会调 _move 两次（比如先靠近再侧移），
    // 只记一次的话第二次用的就是过期的位置。
    this._terrainPrevX = this.x;
    this._terrainPrevY = this.y;

    const mag = Math.hypot(dirX, dirY);
    if (mag < 1e-9) return;

    // 第十六轮：毒舌的减速乘在这里而不是改 this.speed。
    // 改 this.speed 会被 _move 的调用方读回去（比如虾 Boss 的
    // 距离/时长算速），debuff 一结束就再也还原不回来了。
    // 第一百零八轮：晴岚移速乘区同样乘在这里（this.world 由
    // spawnEnemyById 注入）。
    let spd = (speedOverride ?? this.speed) * this.curseSpeedMul * mag
      * (this.world?.weather?.moveMul ?? 1);

    // ---- 第十九轮末：绝对上限夹持已整个移除 ----
    //
    // 第十八轮 ⑥ 曾把 ENEMY_SPEED_CAP(200) 夹在**最终速度**上（防止行为里
    // 的方向系数突破上限）。第十九轮末用户要求移除速度上限的设置（原话见
    // 构造函数里的引用），成长叠加后敌人比玩家默认快是被接受的设定 ——
    // 行为里的各种系数（三阶段贴脸 1.22 × chaseSpeedMul 等）现在如实叠加，
    // 不再被任何常数封顶。
    // speedOverride 仍是**脚本化位移**（虾 Boss 的猛退 1500px/s）专用；
    // 冲刺/冲锋不走 _move，直接 `this.x += chargeDir * chargeSpeed * dt`。

    this.x += (dirX / mag) * spd * dt;
    this.y += (dirY / mag) * spd * dt;
  }

  /**
   * 第十八轮 ⑦：撞墙 / 被地形卡住的检测。
   *
   * 用户原话："给喜欢远离玩家的怪物判定，移动距离和速度严重不匹配的时候
   *   换个方向移动。我的意思是他们现在逃跑的时候如果撞到了墙壁和障碍
   *   就会一直撞墙完全移不动了。加个检测让她撞墙就换方向，不好做的话
   *   就换成她自检速度和移动距离不匹配（指撞了墙）的时候换个方向继续逃
   *   而不是在墙壁上任玩家宰割。"
   *
   * 用户给了两个方案，这里实现的是**第二个**（速度 vs 实际位移不匹配），
   * 因为它同时覆盖两种情况：
   *   · 世界边界墙 —— `clamp` 把怪夹在边上，速度还在，位移没了；
   *   · 地形障碍   —— `resolveTerrain` 把它推回原位，同样是"速度在、位移没"。
   * 而字面意义的"撞墙检测"只覆盖前者。用户自己也说了"不好做的话就换成"，
   * 说明他关心的是**效果**（别卡住），不是实现方式。
   *
   * 判定口径：本帧实际位移 / 本帧理论位移 < STUCK_MOVE_RATIO 就算"没动"。
   * 连续 STUCK_TIME 秒都这样 → 判定卡住，给一个随机脱困方向。
   *
   * 为什么用比值而不是绝对距离：怪的 speed 差异很大（34 ~ 200），
   * 绝对阈值要么对慢怪太宽松（34 的怪每帧只走 0.57px，正常移动也像卡住），
   * 要么对快怪太严格。比值天然归一化。
   *
   * @param {number} dt
   * @param {number} startX 本帧开始时的 x（行为分派之前）
   * @param {number} startY 本帧开始时的 y
   */
  _checkStuck(dt, startX, startY) {
    // 只在"确实想移动"的时候判。stun / 击退 / 出生动画期间不该算卡住。
    //
    // 第十九轮末：_move 的绝对上限夹持已移除，两边都按真实速度算，
    // 不再需要任何口径对齐。
    const wanted = this.speed * this.curseSpeedMul * dt;
    if (wanted < 1e-4) return;

    const moved = Math.hypot(this.x - startX, this.y - startY);
    if (moved / wanted >= STUCK_MOVE_RATIO) {
      // 正常移动了 → 清零累计，并把脱困方向也清掉
      this._stuckTimer = 0;
      return;
    }

    this._stuckTimer += dt;
    if (this._stuckTimer < STUCK_TIME) return;

    // ---- 判定卡住：挑一个脱困方向 ----
    //
    // 方向选"当前朝向的侧向"而不是纯随机：
    // 卡住的怪多半是在贴着墙往前顶，沿墙滑行比掉头往回跑更容易脱身
    // （掉头会直接撞进玩家怀里，而它本来就是在逃跑）。
    // 左右随机，这样多只怪不会挤在同一个方向。
    this._stuckTimer = 0;
    this._escapeTimer = STUCK_ESCAPE_TIME;
    const side = this.rng.next() < 0.5 ? 1 : -1;
    // 用 facing（它一直指向玩家）的垂线当脱困方向
    this._escapeX = -Math.sin(this.facing) * side;
    this._escapeY = Math.cos(this.facing) * side;
    // 略微带一点"远离当前位置"的偏置，避免刚换方向又顶在同一面墙上
    const len = Math.hypot(this._escapeX, this._escapeY) || 1;
    this._escapeX /= len;
    this._escapeY /= len;
  }

  /**
   * 第十八轮 ⑦：如果正在脱困，返回覆盖用的方向；否则返回 null。
   *
   * 只给"会主动远离玩家"的行为用（flee / shooter / healer 这类），
   * 追击类的怪卡住了也无所谓 —— 它们本来就该顶着玩家，
   * 而且玩家的火力会很快把它们清掉，不会出现"任玩家宰割"的观感问题。
   */
  _escapeDir() {
    if (this._escapeTimer <= 0) return null;
    return { x: this._escapeX, y: this._escapeY };
  }

  _chase(dt, player, d, dx, dy) {
    this._move(dt, dx / d, dy / d);
  }

  _zigzag(dt, player, d, dx, dy) {
    // 朝玩家前进，同时侧向摆动
    this.wobblePhase += dt * 6;
    const nx = dx / d, ny = dy / d;
    const px = -ny, py = nx;
    const wob = Math.sin(this.wobblePhase) * 0.75;
    this._move(dt, nx + px * wob, ny + py * wob);
  }

  _orbit(dt, player, d, dx, dy) {
    // 保持约 90 距离绕圈
    const desired = 90;
    const nx = dx / d, ny = dy / d;
    const px = -ny, py = nx;
    let radial = 0;
    if (d > desired + 12) radial = 1;
    else if (d < desired - 12) radial = -1;
    const tangent = 0.9;
    this._move(dt, nx * radial + px * tangent, ny * radial + py * tangent);
  }

  /**
   * 统一的"发射弹幕"入口。
   *
   * 第九轮用户要求："加多一到两种弹幕类型敌人，让弹幕类型丰富一些，
   *                  不要只是自机狙，来点随机蛋怪物……"
   *
   * 所以把弹幕做成**可选的 pattern**，而不是只有"朝玩家打"一种：
   *   aimed  —— 自机狙（原来唯一的行为，多个敌人仍在用）
   *   spread —— 扇形散射（朝玩家张开一个角度）
   *   ring   —— 环形弹幕（360° 均匀，逼玩家找缝钻）
   *   random —— 随机蛋：角度随机，让玩家无法靠"预判准星"躲
   *   spiral —— 螺旋：每发角度递增，形成旋转弹幕墙
   *
   * 所有 pattern 都走 world.spawnEnemyBullet，所以难度倍率、
   * 碰撞、渲染这些下游逻辑完全不用改。
   *
   * @param {World} world
   * @param {number} baseAngle 基准角度（一般是"朝向玩家"）
   */
  _firePattern(world, baseAngle) {
    const def = this.def;
    const pattern = def.bulletPattern ?? 'aimed';
    const count = Math.max(1, def.projectileCount ?? 1);
    // 第十二轮：远程怪弹幕速度 +20%。
    //
    // 加在这里（而不是改每个敌人的 projectileSpeed）：
    //   这是**全局**调整，改数据表要动 5 个敌人、还容易漏。
    //   而且这个函数只被 _shooter / _chaseShooter 调用 ——
    //   Boss 走 _boss() / _meleeBoss()，各自直接 spawnEnemyBullet，
    //   所以"不用改 boss"这一条是**结构性**满足的，不靠自觉。
    //   （test/enemy-tuning.mjs 会真起一个 Boss 开火来验证这一点。）
    const speed = (def.projectileSpeed ?? 220) * ENEMY_BULLET_SPEED_BUFF;
    // 奶妈 buff 的"攻击 +20%"对弹幕同样生效（第十二轮）。
    // 不然 buff 只加强近战怪，"被奶的远程怪反而没变强"会很反直觉。
    // 第十六轮：毒舌的"伤害 -20%"同样要覆盖弹幕这条路径。
    // 第二十九轮：弹幕吃波次/难度/角色乘区（this.dmgScale）——
    // 原来这里是裸的基础值，后期完全挠痒痒。没有 projectileDamage 时
    // 回退到**基础接触伤害**（以前回退的是已乘过的 this.damage，
    // 改成基础值再统一乘 dmgScale，数学等价、且不会双重缩放）。
    const damage = (def.projectileDamage ?? (def.damage ?? 3)) * this.dmgScale
      * (1 + this.buffDamagePct / 100) * this.curseDamageMul;
    const radius = def.projectileRadius ?? 7;
    const color = def.projectileColor ?? this.color;
    const range = (def.projectileRange ?? 520) * this.rangeScale;   // 第三十轮：射程成长

    // 螺旋需要跨帧记住上一发的角度
    if (pattern === 'spiral') {
      this.spiralAngle = (this.spiralAngle ?? baseAngle) + (def.spiralStep ?? 0.5);
    }

    for (let i = 0; i < count; i++) {
      let a;
      switch (pattern) {
        case 'ring':
          // 360° 均匀铺开；基准角加上序号偏移，让每轮弹幕错开
          a = baseAngle + (i / count) * TAU;
          break;
        case 'random': {
          // 随机蛋：在 ±randomSpread 度内随机（默认全向）
          const spreadRad = (def.randomSpread ?? 360) * Math.PI / 180;
          a = baseAngle + (this.rng.next() - 0.5) * spreadRad;
          break;
        }
        case 'spiral':
          a = this.spiralAngle + (i / count) * TAU;
          break;
        case 'spread':
        default: {
          const spread = (def.projectileSpread ?? 0) * Math.PI / 180;
          const t = count === 1 ? 0.5 : i / (count - 1);
          a = baseAngle - spread / 2 + spread * t;
          break;
        }
      }
      world.spawnEnemyBullet({ trueTier: this.trueTier,
        x: this.x, y: this.y, angle: a,
        speed, damage, radius, color, range,
        shape: def.projectileShape,   // 第一百八十五轮：小怪弹型（drop/shard/star/petal/blade…），不传 = orb
      });
    }
  }

  /**
   * 给 Boss 弹幕的角度加一点随机抖动（第十轮，用户要求）。
   *
   * 用户原话："给第十关的boss的弹幕加一点随机角度概率，比如5-15度随机，
   *            不然记住弹幕路线之后还是容易躲。"
   *
   * 为什么需要：Boss 环形弹幕的角度是 (i/count)*TAU + age*0.6，
   * **完全确定**。玩家只要背下"转多快、每圈几发"，就能靠肌肉记忆穿过去，
   * Boss 战就变成背板而不是临场反应 —— 这正是用户说的问题。
   *
   * 抖动量在 [lo, hi] 度之间取，符号随机。
   *
   * **逐发独立调用**，不是"整圈共用一个抖动值"。这个区别很关键：
   * 整圈一起转的话，环上相邻弹幕的间距仍然是精确的 360/count，
   * 玩家只要算准"缝隙在 45° 的整数倍上"照样能站进去 ——
   * 环旋转只是换了个相位，背板依然有效。
   * 逐发抖之后**间距本身就不均匀**了，没有固定缝隙可站。
   *
   * 但依然保留"环"的结构 —— 如果做成纯随机角度，弹幕就没有形状可读，
   * 玩家只能碰运气，那是"难"而不是"好玩"。
   *
   * 由 def.bulletJitterDeg 控制，没配的 Boss 完全不受影响：
   *   数字      → 固定幅度（±该度数内随机）
   *   [lo, hi]  → 幅度在 lo~hi 度之间随机
   */
  _bulletJitter(def) {
    const j = def.bulletJitterDeg;
    if (!j) return 0;
    const [lo, hi] = Array.isArray(j) ? j : [j, j];
    const mag = lo + this.rng.next() * (hi - lo);
    const sign = this.rng.next() < 0.5 ? -1 : 1;
    return sign * mag * Math.PI / 180;
  }

  _shooter(dt, player, d, dx, dy, world) {
    // 第三十二轮：交战距离也吃射程成长曲线（用户："远程小怪经常离玩家
    // 很近才射弹幕……如果是判定和玩家的实际距离，也让他们数值有所成长"）。
    // 之前这里是不动的 attackRange（240~360），弹幕射程第 20 波已经
    // 1310px，交战圈还钉在脸附近。现在同步 ×2.52：吐酸猫从 605px 外
    // 就开始开火，弹幕真正变成"远处的压力"。
    const range = (this.def.attackRange ?? 240) * this.rangeScale;
    const nx = dx / d, ny = dy / d;
    if (d > range * 0.85) {
      this._move(dt, nx, ny);
    } else if (d < range * 0.55) {
      this._move(dt, -nx, -ny); // 太近就后退
    } else {
      // 保持距离并侧移
      this._move(dt, -ny * 0.5, nx * 0.5);
    }

    this.attackCooldown -= dt;
    if (this.attackCooldown <= 0 && d < range) {
      // 第七轮：弹幕类敌人开火频率倍率（噩梦 ×1.3 → 冷却除以 1.3）。
      // Boss **不**走这里（Boss 是 _boss()），所以"boss不用加"自动满足。
      //
      // 第十二轮：再叠一层全局的 +20%（ENEMY_FIRE_RATE_BUFF）。
      // 两层是**相乘**：噩梦弹幕怪的最终射速 = 1.3 × 1.2 = 1.56 倍。
      // 第一百五十六轮：特殊波模组 —— 全员摸鱼日攻速 -50%、发薪日射速 +20%。
      const fireRate = (world?.difficulty?.enemyFireRate ?? 1) * ENEMY_FIRE_RATE_BUFF
        * (world?.weather?.enemyFireMul ?? 1)   // 第一百零八轮：血月敌我攻速
        * (world?.specialWave === 'slack' ? 0.5 : 1)
        * (world?.specialWave === 'payday' ? 1.2 : 1);
      // 第十六轮：毒舌的"攻速 -20%"→ 冷却 ×1.25（见 curseCooldownMul 的注释）
      this.attackCooldown = (this.def.attackCooldown ?? 2) / fireRate * this.curseCooldownMul;
      this._firePattern(world, Math.atan2(dy, dx));
    }
  }

  /**
   * 奶妈怪（第十二轮，用户新增）。
   *
   * 用户原话：
   *   "做一个奶妈怪，从11回合之后开始生成，会和玩家保持距离不靠近，
   *    aoe治疗受伤的敌人，大概每秒回20%敌人的生命，让被治疗的敌人获得
   *    buff：攻击+20%，获得30%固定减伤。奶妈怪移动速度要快，不超过玩家
   *    默认100%，血量和远程怪差不多就行。"
   *
   * 行为拆成两件事：
   *   1. **站位**：和 shooter 一样保持中距离，但逻辑更简单 ——
   *      远了就靠近、近了就后退、距离合适就侧移。
   *      它不会主动贴脸（用户："会和玩家保持距离不靠近"）。
   *   2. **治疗**：每 healInterval 秒（5 秒 CD）发出一次治疗脉冲，
   *      把 healRadius 内受伤的友军按"目标自己生命上限"的比例回血
   *      （小怪 20% / 精英 10% / Boss 0.5%，第三十一轮），
   *      并给它们挂上限时 buff。
   *
   * 为什么按**目标的**生命上限而不是固定数值回血：
   *   固定数值在后期毫无意义（第 19 波小怪几千血，回 20 点等于没回），
   *   而按比例回血在任何波次都保持"必须优先点掉奶妈"这个压力。
   *   比例本身按目标类型分档（第三十一轮）：Boss 档压到 0.5%，
   *   否则高波次 Boss 临死召唤一波奶妈就直接回满（用户实测噩梦 20 波
   *   黑化开发猫猫 16 万血一口被奶 3 万多）。
   *
   * 为什么只奶**受伤的**友军：
   *   满血的怪再奶是浪费，而且会让"集火一只"变得没有意义 ——
   *   玩家打掉的那点血瞬间被其他人满上。只奶受伤的怪，
   *   buff 就集中在"正在扛伤害的那几只"身上，玩家的策略仍然有效。
   */
  _healer(dt, player, d, dx, dy, world) {
    const def = this.def;
    const dist = def.keepDistance ?? 260;
    const nx = dx / d, ny = dy / d;

    // ---- 站位：保持中距离，绝不贴脸 ----
    // 分三档，和 _shooter 同一套手感（近了退、远了进、不远不近侧移）——
    // 复用同一套距离感，玩家看到它就能凭直觉预判它的走位。
    if (d > dist * 1.05) {
      this._move(dt, nx, ny);          // 太远：靠近战场（它要够得着友军）
    } else if (d < dist * 0.7) {
      this._move(dt, -nx, -ny);        // 太近：后退，不和玩家贴身
    } else {
      this._move(dt, -ny * 0.6, nx * 0.6);  // 合适：侧移，避免站桩被点
    }

    // ---- 治疗节拍 ----
    this.healTick -= dt;
    if (this.healTick > 0) return;
    this.healTick = def.healInterval ?? 5;

    const radius = def.healRadius ?? 190;
    // 第三十一轮：治疗改成 **5 秒 CD 的脉冲**，奶量按**目标类型**分档
    //（每一次脉冲回复目标生命上限的比例，不再是"每秒 20%"）。
    // 用户原话："奶人cd5秒，小怪回复20%，精英10%，boss0.5%。不然黑化
    // 开发猫猫我一辈子也打不死。她16万血奶一口30000多太可怕了。"
    // 旧机制每秒 20%、多奶妈叠加，噩梦 20 波 16 万血的 Boss 临死召唤
    // 一波奶妈直接回满；现在 Boss 档一口只有 0.5%（≈805）。
    // 第一百一十五轮：兜底默认与 healPct 新档位（15%/5%/0.5%）同步
    const pctTable = def.healPct ?? { normal: 0.15, elite: 0.05, boss: 0.005 };

    let healedCount = 0;
    for (const e of world.enemies.items) {
      if (e === this || e.dead) continue;
      if (e.isChest) continue;              // 宝箱怪不是战斗单位，不奶
      if (e.hp >= e.maxHp) continue;        // 只奶受伤的
      const dx2 = e.x - this.x, dy2 = e.y - this.y;
      if (dx2 * dx2 + dy2 * dy2 > radius * radius) continue;

      // 档位看**被奶的目标**：奶小怪 20%、奶精英 10%、奶 Boss 0.5%
      const pct = e.boss ? pctTable.boss : e.elite ? pctTable.elite : pctTable.normal;
      e.hp = Math.min(e.maxHp, e.hp + e.maxHp * pct);
      // 挂 buff（覆盖式刷新，不叠加）
      const b = def.healBuff || {};
      e.buffDamagePct = b.damagePct ?? 0;
      e.buffDamageReduction = b.damageReduction ?? 0;
      e.buffTimer = def.healBuffDuration ?? 4;
      e.healFlash = 1;
      healedCount++;
    }

    // 治疗特效：只有真的奶到了才放，否则场上会有大量无声光效
    if (healedCount > 0) {
      world.fx?.heal?.(this.x, this.y, radius)
        ?? world.fx?.ring?.(this.x, this.y, radius, def.color);
    }
  }

  /**
   * 近战 + 远程的混合敌人：平时贴脸追人，冷却好了就放一发弹幕。
   *
   * 第九轮用户要求："或者你修改一些近战的敌人让他们拥有远程能力"
   * —— 目的是让"只靠拉开距离"不再是无敌打法。
   *
   * 和纯 shooter 的区别：不保持距离、不后退，就是一路压上来，
   * 所以玩家不能靠"风筝"解决它，必须真的走位。
   */
  _chaseShooter(dt, player, d, dx, dy, world) {
    const nx = dx / d, ny = dy / d;
    this._move(dt, nx, ny);   // 一直追

    this.attackCooldown -= dt;
    // 第三十二轮：交战距离同 _shooter，吃射程成长曲线
    const range = (this.def.attackRange ?? 260) * this.rangeScale;
    if (this.attackCooldown <= 0 && d < range) {
      // 第十二轮：同样叠一层全局 +20%（见 _shooter 的说明）
      const fireRate = (world?.difficulty?.enemyFireRate ?? 1) * ENEMY_FIRE_RATE_BUFF
        * (world?.weather?.enemyFireMul ?? 1);   // 第一百零八轮：血月敌我攻速
      // 第十六轮：毒舌的"攻速 -20%"→ 冷却 ×1.25（见 curseCooldownMul 的注释）
      this.attackCooldown = (this.def.attackCooldown ?? 2) / fireRate * this.curseCooldownMul;
      this._firePattern(world, Math.atan2(dy, dx));
    }
  }

  _charger(dt, player, d, dx, dy) {
    const windup = this.def.chargeWindup ?? 0.7;
    if (this.state === 'idle') {
      this._move(dt, dx / d, dy / d);
      if (d < 220) this._beginCharge(dx / d, dy / d, windup);
    } else {
      this._runChargeStates(dt, dx / d, dy / d, windup);
    }
  }

  /** 进入蓄力状态（charger 和 Boss 第二阶段之后的冲锋都用它） */
  _beginCharge(nx, ny, windup) {
    this.state = 'windup';
    this.stateTimer = windup;
    this.chargeDirX = nx;
    this.chargeDirY = ny;
    // 第十四轮：`chargeDistanceMul` 拉长**冲刺距离**而不动速度。
    // 乘在 duration 上是刻意的 —— 见 enemies.js 里依趣苹果醋那段注释：
    // 用户说"冲刺的时候比玩家快"，速度本来就够，缺的是够不到人。
    const duration = (this.def.chargeDuration ?? 0.55) * (this.def.chargeDistanceMul ?? 1);
    this._chargeDuration = duration;
  }

  /**
   * 推进冲锋状态机：windup（蓄力，缓慢转向）→ charge（高速位移）→ recover（硬直）。
   * 抽出来是因为 Boss 的冲锋也要用同一套手感，不能各写一份。
   *
   * @param {boolean} [track=true] 蓄力期间要不要**继续追踪**玩家。
   *
   *   - `true`（默认，普通 charger / 远程 Boss 用）：蓄力时缓慢转向，
   *     玩家横向跑位就能甩掉。这是"小怪级"的冲锋，允许被走位化解。
   *   - `false`（第十一轮的近战 Boss 用）：**方向在蓄力开始那一刻就锁死**。
   *     用户要的是"停顿一下就冲刺玩家位置" —— 冲的是**你当时站的地方**，
   *     不是一路咬着你转。锁死方向才有"躲开"的成就感；
   *     如果一路追踪，340~380 的速度是躲不掉的，那不叫压迫感叫必死。
   */
  _runChargeStates(dt, nx, ny, windup, track = true) {
    const duration = this._chargeDuration ?? this.def.chargeDuration ?? 0.55;

    if (this.state === 'windup') {
      this.stateTimer -= dt;
      if (track) {
        // 蓄力时缓慢转向，给玩家躲避机会
        this.chargeDirX += (nx - this.chargeDirX) * dt * 1.5;
        this.chargeDirY += (ny - this.chargeDirY) * dt * 1.5;
        const len = Math.hypot(this.chargeDirX, this.chargeDirY) || 1;
        this.chargeDirX /= len; this.chargeDirY /= len;
      }
      // track=false 时完全不碰 chargeDirX/Y —— 方向已锁
      if (this.stateTimer <= 0) {
        this.state = 'charge';
        this.stateTimer = duration;
        this.chargeSpeed = this.def.chargeSpeed ?? 260;
        // 第三十七轮：普通 charger 出手的瞬间**再次定位玩家** ——
        // 用户原话："让它冲刺的时候再次定位玩家的位置。目前的情况是
        // 玩家在A，它蓄力，蓄力结束玩家已经到B了，冲刺距离又短，毫无
        // 靠近玩家的可能。改成蓄力的时候玩家在A，蓄力结束时玩家到B
        // 它的冲刺攻击位置是B而不是A，这时候玩家就需要转移到C，不然
        // 就要吃+100%冲刺距离的攻击了。"
        // 原来蓄力期只有 1.5/s 的缓慢转向，玩家横跑一步就甩掉；
        // 现在出鞘一刻吸附到当前方向（nx/ny 就是本帧指向玩家的向量），
        // 躲避窗口后移到冲刺**开始之后**。Boss（track=false）保持
        // 锁死方向的设计不变（第十一轮）。
        if (track) {
          this.chargeDirX = nx;
          this.chargeDirY = ny;
        }
      }
    } else if (this.state === 'charge') {
      this.stateTimer -= dt;
      this.x += this.chargeDirX * this.chargeSpeed * dt;
      this.y += this.chargeDirY * this.chargeSpeed * dt;
      if (this.stateTimer <= 0) {
        this.state = 'recover';
        this.stateTimer = 0.5;
      }
    } else {
      this.stateTimer -= dt;
      if (this.stateTimer <= 0) this.state = 'idle';
    }
  }

  _exploder(dt, player, d, dx, dy, world) {
    this._move(dt, dx / d, dy / d);
    const trigger = this.radius + player.radius + 10;
    if (d < trigger) {
      this._explode(world, player);
    }
  }

  _explode(world, player) {
    if (this.dead) return;
    this.dead = true;
    this.exploded = true;
    const radius = this.def.explosionRadius ?? 70;
    // 体检修复（第九十二轮）：爆炸伤害补上 dmgScale（波次×难度×角色乘区）。
    // 第二十九轮"接触+弹幕+水柱同一乘区"漏了这条通道 —— 噩梦 20 波
    // 自爆固定 14 点而同级接触已 ×6.9，自爆猫后期退化为送材料。
    // dmgScale 第 1 波 ≈1，前期数值不变。
    const dmg = (this.def.explosionDamage ?? 12) * this.dmgScale;
    const d = dist(this.x, this.y, player.x, player.y);
    if (d < radius + player.radius) {
      // 第一百三十二轮：自爆同样附加波次真伤（接触类伤害）
      player.takeDamage(dmg, this.x, this.y, { trueBonus: this.trueBonusFor(world) });
    }
    world.fx.explosion(this.x, this.y, radius, this.color);
    world.camera.shake(6, 0.2);
  }

  /**
   * 让宝箱怪进入"逃跑"状态一段时间。
   * 被打到、或者玩家靠近到警戒距离时调用。
   */
  alert(seconds = 3.5) {
    this.fleeTimer = Math.max(this.fleeTimer, seconds);
    this.alerted = true;
  }

  /**
   * 宝箱怪的行为：默认在地图上**随机游荡**，被"看到"或打到时才逃跑，
   * 跑一段时间后重新回到游荡。
   *
   * 用户第四轮反馈："宝箱怪数量设置成一回合最多刷 5 个，最少刷 1 个。
   * 而且怪物是默认游荡在地图里，'看到'玩家（距离稍近）或者受到攻击时
   * 主动远离玩家，在一段时间后重新回归随机游荡模式而不要一开始出现就
   * 远离玩家。（目前宝箱怪太多而且全在地图的各个角落躲着，
   * 变成去角落抓他们的游戏了）"
   *
   * 所以关键改动：
   *   1. 开局不逃 —— 玩家不靠近就慢慢游荡（玩家能从容地走过去）
   *   2. 警戒距离内（SIGHT）才触发逃跑，跑 3.5 秒后回归游荡
   *   3. 游荡时保持一定速度但会定期换方向，看起来像"在逛地图"
   */
  _flee(dt, player, d, dx, dy, world) {
    const SIGHT = 210;          // 警戒距离：玩家进这个圈就被"看到"
    const COMFORT = 300;        // 逃到这个距离外就不再拼命跑
    const WANDER_SPEED = 0.42;  // 游荡时的速度系数（比冲刺慢）

    // ---- 状态推进 ----
    if (this.fleeTimer > 0) {
      this.fleeTimer -= dt;
      if (this.fleeTimer <= 0) {
        // 跑完了，回到游荡
        this.fleeTimer = 0;
        this.wanderAngle = this.rng.next() * TAU;
        this.wanderTimer = 1.5 + this.rng.next() * 2.5;
      }
    } else if (d < SIGHT) {
      // 游荡中"看到"了玩家 → 开始逃跑
      this.alert(3.5 - Math.min(2, COMFORT / Math.max(1, d)) * 0.2);
    }

    let vx, vy;

    if (this.fleeTimer > 0) {
      // ---- 逃跑 ----
      const nx = dx / d, ny = dy / d;
      vx = -nx; vy = -ny;
      // 离得太远就不再拼命跑（否则玩家永远追不上）
      if (d > COMFORT) { vx *= 0.35; vy *= 0.35; }

      // 靠近边界时沿切线滑走，避免贴墙被堵死（保留第三轮的逻辑）
      const half = world.halfSize - this.radius - 30;
      if (Math.abs(this.x) > half || Math.abs(this.y) > half) {
        const tx = -vy, ty = vx;
        const towardCenter = -(this.x * vx + this.y * vy) > 0 ? 1 : -1;
        vx = tx * towardCenter + (-this.x / world.halfSize) * 0.6;
        vy = ty * towardCenter + (-this.y / world.halfSize) * 0.6;
      }
    } else {
      // ---- 随机游荡 ----
      this.wanderTimer -= dt;
      if (this.wanderTimer <= 0) {
        // 换一个方向，但避免急转弯（保持角度变化在一个扇形内）
        this.wanderAngle += (this.rng.next() - 0.5) * 2.2;
        this.wanderTimer = 1.2 + this.rng.next() * 2.8;
      }
      vx = Math.cos(this.wanderAngle) * WANDER_SPEED;
      vy = Math.sin(this.wanderAngle) * WANDER_SPEED;

      // 游荡也别贴墙：靠近边界时把游荡方向往场地中心掰
      const half = world.halfSize - this.radius - 60;
      if (Math.abs(this.x) > half || Math.abs(this.y) > half) {
        vx += (-this.x / world.halfSize) * 1.6;
        vy += (-this.y / world.halfSize) * 1.6;
      }
    }

    const len = Math.hypot(vx, vy) || 1;
    // 第十四轮 ⑦：挨打 buff 的移速系数，乘在归一化方向上（和 Boss 的
    // chaseSpeedMul 同一个套路）—— 乘方向向量而不是乘 this.speed，
    // 是为了不影响 this.speed 本身（它还要给别的逻辑读）。
    //
    // 两段互斥：加速段优先。理论上不会同时 >0（见 update() 里的接力），
    // 但真同时出现时选"加速"更安全 —— 免得玩家刚打中它反而变慢，那太反直觉。
    const buffMul = this.mimicBoostTimer > 0 ? MIMIC_BOOST_MUL
      : this.mimicSlowTimer > 0 ? MIMIC_SLOW_MUL
      : 1;
    // 第十八轮 ⑦：逃跑时撞墙就换方向。
    // 这是用户点名的那一类怪（"喜欢远离玩家的怪物"），
    // 原来贴墙时靠"往场地中心掰"来解，但障碍物不在地图中心，
    // 所以会一直顶着障碍物原地抖 —— 用户描述的就是这个现象。
    const esc = this._escapeDir();
    if (esc) {
      this._move(dt, esc.x * buffMul, esc.y * buffMul);
      this.facing = Math.atan2(esc.y, esc.x);
      return;
    }
    this._move(dt, (vx / len) * buffMul, (vy / len) * buffMul);
    this.facing = Math.atan2(vy, vx);
  }

  /**
   * 第十五轮 ④：发射"水柱"（激光）。
   *
   * 用户原话："同时射出水柱（类似激光水柱，很快）攻击玩家的动作。"
   *
   * 实现要点：
   *   · 速度取 1400px/s（远高于普通弹幕的 210~280）—— 这就是"很快"。
   *   · 用 `laser: true` 让渲染层画成细长光束，而不是圆弹。
   *   · 伤害明显高于扇形弹（26 vs 9）：它是一个**有预警的高危动作**，
   *     打中就该痛，否则"猛退 + 蓄势"这一整套表演没有回报。
   *   · 方向在 windup 结束时才锁定（见调用处），所以玩家可以靠
   *     后退后的横向走位躲开 —— 这是可躲的，不是必中。
   */
  _fireWaterJet(world, def, jet, pm, phase) {
    const diff = world.difficulty ?? { bulletSpeed: 1 };
    const speedMul = pm?.bulletSpeed ?? 1;
    const count = Math.max(1, jet.jetCount ?? 1);
    // 多发时左右各偏一点，形成一小片水幕而不是一条线
    const spreadDeg = count > 1 ? 12 : 0;
    for (let i = 0; i < count; i++) {
      const t = count === 1 ? 0.5 : i / (count - 1);
      const off = (t - 0.5) * spreadDeg * Math.PI / 180;
      const a = Math.atan2(this.jetDirY, this.jetDirX) + off;
      world.spawnEnemyBullet({ trueTier: this.trueTier,
        x: this.x, y: this.y, angle: a,
        // 水柱速度**不叠难度倍率**。
        // 理由：噩梦的 bulletSpeed 是 1.28 倍，1400 × 1.28 = 1792px/s，
        // 在 60fps 下每帧走 30px —— 玩家（默认 200px/s，每帧 3.3px）
        // 根本没有反应窗口，那就变成不可躲伤害了。
        // 用户要的是"很快"，不是"必中"；1400 本身已经远超普通弹幕的
        // 210~280，观感上足够"激光"。所以这里刻意不加 diff.bulletSpeed。
        speed: jet.jetSpeed * speedMul,
        damage: jet.jetDamage * this.dmgScale,   // 第二十九轮：水柱也吃成长
        radius: jet.jetWidth * 0.5,
        color: '#8fe3ff',
        range: jet.jetLength,   // 激光水柱长度是预警几何的一部分，不参与射程成长（第三十轮）
        laser: true,
        laserLength: jet.jetLength * 0.35,
      });
    }
    // 发射反馈：一圈白色扩张 + 轻微震屏，强调"这一下很重"
    // 第一百二十一轮：水柱射击音 shoot2 —— 一次发射只响一声（冷却 3.4s）。
    audio.play('shoot2');
    world.fx?.nova?.(this.x, this.y, 90, '#8fe3ff');
    world.camera?.shake?.(8, 0.2);
  }

  _boss(dt, player, d, dx, dy, world) {
    const def = this.def;

    // ---- 阶段推进 ----
    // 血量进度或时间进度谁先到 66% / 33% 就切阶段（见 data/boss-phases.js）
    // 第二十五轮：tick(dt) 挪去了 update()（僵直/被晕时无敌倒计时也要走）
    let phaseUp = 0;
    if (this.bossPhase) {
      const hpRatio = this.hpPercent;
      // 剩余时间比例：由 world 在每帧注入（Boss 不知道外层计时器）
      const timeRatio = world.bossTimeRatio ?? 1;
      phaseUp = this.bossPhase.update(hpRatio, timeRatio);
      if (phaseUp) {
        const mods = this.bossPhase.mods;
        world.fx.nova(this.x, this.y, 260, mods.color || def.color);
        world.fx.explosion(this.x, this.y, 180, mods.color || def.color);
        world.camera.shake(18, 0.7);
        world.onBossPhase?.(this, phaseUp);
      }
    }
    const pm = this.bossPhase?.mods ?? null;

    // 保持中距离（阶段越高越贴身，压迫感更强）
    const desired = 200;
    const nx = dx / d, ny = dy / d;
    const moveMul = pm?.moveSpeed ?? 1;

    // 第十五轮 ④：`kite: 'flee'` —— 移动方式从"追击玩家"改成"远离玩家"。
    //
    // 用户原话："然后第十关的弹幕boss移动方式从追击玩家变成远离玩家。"
    //
    // 原来的三段逻辑里，只有"太近"那一段是后退，另外两段是靠近 / 绕圈。
    // 现在改成**永远在逃**：
    //   · 太近（< desired - 60）→ 用满速后退（比原来更快脱离，逃得果断）
    //   · 太远（> desired + 40）→ **不再靠近**，改成横向绕圈
    //     （否则它会为了"保持 200 距离"又追回来，和"远离玩家"矛盾）
    //   · 中间地带 → 横向绕圈 + 一点点持续后退，保持"一直在躲"的观感
    //
    // 横向绕圈那一项保留：纯直线后退会被玩家一路平推顶到墙角，
    // 加上切向分量才能形成"绕着你转圈躲"的走位，也更符合弹幕 Boss
    // "边躲边放弹幕"的设计。
    if (def.kite === 'flee') {
      // 第四十轮：三阶段专属 +20% 移速（phase3SpeedMul，只有被殴猫娘配了）。
      // 用户原话："给路过被殴打的猫娘boss的三阶段加个新设定，+20%移速，
      // 让她跑得更快一些看起来更不想被打的样子。"
      const fleeMul = moveMul
        * (this.bossPhase?.phase >= 3 ? (def.phase3SpeedMul ?? 1) : 1);
      // 基础方向：背离玩家
      let fx = -nx, fy = -ny;
      // 切向分量（绕圈）—— 用玩家指向 Boss 的垂直方向
      const tx = -ny, ty = nx;
      // 离得越远，绕圈比重越大；贴脸时几乎纯后退
      const orbit = clamp((d - (desired - 60)) / 260, 0, 1);
      fx += tx * orbit * 0.9;
      fy += ty * orbit * 0.9;
      // 边界：贴墙时把方向往场地中心掰，避免被顶到角落原地抖
      const half = world.halfSize - this.radius - 40;
      if (Math.abs(this.x) > half || Math.abs(this.y) > half) {
        fx += (-this.x / world.halfSize) * 1.8;
        fy += (-this.y / world.halfSize) * 1.8;
      }
      const fl = Math.hypot(fx, fy) || 1;
      // 第十八轮 ⑦：如果刚判定"贴墙动不了"，改用脱困方向。
      // 放在最后覆盖，是因为前面那套（背离 + 绕圈 + 往中心掰）
      // 在**被障碍物卡住**时全都不管用 —— 障碍物不在中心方向上，
      // 往中心掰反而可能顶得更死。
      const esc = this._escapeDir();
      if (esc) this._move(dt, esc.x * fleeMul, esc.y * fleeMul);
      else this._move(dt, (fx / fl) * fleeMul, (fy / fl) * fleeMul);
    } else if (d > desired + 40) this._move(dt, nx * moveMul, ny * moveMul);
    else if (d < desired - 60) this._move(dt, -nx * moveMul, -ny * moveMul);
    else this._move(dt, -ny * 0.4 * moveMul, nx * 0.4 * moveMul);

    // 第三阶段：持续震屏，做"压迫感"的主要来源
    if (pm?.shake) world.camera.shake(pm.shake, 0.12);

    // 弹幕
    this.attackCooldown -= dt;
    if (this.attackCooldown <= 0) {
      const diff = world.difficulty ?? { bulletCount: 1, bulletSpeed: 1 };
      const fireRate = pm?.fireRate ?? 1;
      const bulletMul = pm?.bulletMul ?? 1;
      const speedMul = pm?.bulletSpeed ?? 1;

      this.attackCooldown = (def.attackCooldown ?? 2.4) * fireRate * this.curseCooldownMul;
      const count = Math.max(1, Math.round((def.projectileCount ?? 8) * diff.bulletCount * bulletMul));
      const spread = def.projectileSpread ?? 360;
      const baseAngle = Math.atan2(dy, dx);
      const isFullCircle = spread >= 360;
      const bulletSpeed = (def.projectileSpeed ?? 240) * diff.bulletSpeed * speedMul;
      // 第九十二轮：主环按 crystalEvery 抽样结晶化 —— 形状只影响渲染
      //（结晶蛋），碰撞仍是圆形 radius；颜色用 Boss 自己的结晶色。
      const ce = def.crystalEvery ?? 0;
      const cc = def.crystalColor ?? def.color;
      for (let i = 0; i < count; i++) {
        let a;
        if (isFullCircle) {
          a = (i / count) * TAU + this.age * 0.6;
        } else {
          const t = count === 1 ? 0.5 : i / (count - 1);
          a = baseAngle - (spread * Math.PI / 180) / 2 + (spread * Math.PI / 180) * t;
        }
        // 抖动**逐发**独立取（见 _bulletJitter 的说明）。
        // 不是"整圈一起转一个随机角度" —— 那样环上的间距仍然是精确等分的，
        // 玩家照样能算准"缝隙在 45° 的整数倍上"然后站进去。
        // 逐发抖之后间距本身就不均匀了，没有固定的缝隙可站。
        a += this._bulletJitter(def);
        const crystal = ce > 0 && i % ce === ce - 1;
        world.spawnEnemyBullet({ trueTier: this.trueTier,
          x: this.x, y: this.y, angle: a,
          speed: bulletSpeed,
          damage: (def.projectileDamage ?? 10) * this.dmgScale,   // 第二十九轮：吃成长
          radius: 9,
          color: crystal ? cc : def.color,
          shape: crystal ? 'crystal' : undefined,
          range: 900 * this.rangeScale,   // 第三十轮：射程成长
        });
      }
      // 第一百二十一轮：一整轮齐射只响一声（用户："记得和芙兰一样的处理
      // 不要频率太高了"—— 芙兰 tan00 就是每轮一发，SE_MIN_GAP 再兜底）。
      audio.play('shoot1');
      world.fx.nova(this.x, this.y, 40, pm?.color || def.color);

      // 交错弹：噩梦难度本来就有；二三阶段所有难度都会出现，
      // 第三阶段再叠一层反向慢速弹，形成"钻缝"压力。
      // 第九十二轮：阶段个性不再是"单纯变多" ——
      //   · 被殴猫娘（phase3CrystalRings）：三阶段起两圈交错环全部结晶化
      //   · 黑化开发猫猫（phase3RainbowRing）：三阶段反向环 = 七彩结晶环
      const rings = [];
      if (diff.bulletCount >= 1.6) rings.push({ mul: 0.6, speed: 0.72, color: '#ffd6e7', radius: 8 });
      if (this.bossPhase && this.bossPhase.phase >= 2) {
        rings.push({
          mul: 0.5, speed: 0.9,
          color: pm?.color || '#ffffff', radius: 8,
          shape: (def.phase3CrystalRings && this.bossPhase.phase >= 3) ? 'crystal' : undefined,
        });
      }
      if (this.bossPhase && this.bossPhase.phase >= 3) {
        if (def.phase3RainbowRing) {
          // 反向七彩结晶环：和主环逆向旋转，缝隙更窄（黑化开发猫猫签名视觉）
          rings.push({ mul: 0.7, speed: 0.62, rainbow: true, radius: 7, reverse: true, shape: 'crystal' });
        } else {
          // 反向环：和主环逆向旋转，缝隙更窄
          rings.push({
            mul: 0.7, speed: 0.62, color: '#ffffff', radius: 7, reverse: true,
            shape: def.phase3CrystalRings ? 'crystal' : undefined,
          });
        }
      }
      for (const r of rings) {
        const n2 = Math.max(4, Math.round(count * r.mul));
        const offset = this.age * (r.reverse ? -1.1 : 0.9) + Math.PI / n2;
        for (let i = 0; i < n2; i++) {
          // 交错环同样逐发抖（理由见主环那段注释）
          const a = (i / n2) * TAU + offset + this._bulletJitter(def);
          world.spawnEnemyBullet({ trueTier: this.trueTier,
            x: this.x, y: this.y, angle: a,
            speed: bulletSpeed * r.speed,
            damage: Math.round((def.projectileDamage ?? 10) * this.dmgScale * 0.75),   // 第二十九轮：吃成长
            radius: r.radius,
            color: r.rainbow ? BOSS_RAINBOW[i % BOSS_RAINBOW.length] : r.color,
            shape: r.shape,
            range: 900 * this.rangeScale,   // 第三十轮：射程成长
          });
        }
      }

      // 第九十二轮：结晶扇（按 Boss 配置，二三阶段）—— 朝玩家的短扇结晶蛋。
      // 颜色循环取自 Boss 自己的配色表（被殴猫娘=粉/白猫爪扇，
      // 黑化开发猫猫=紫/品红/蓝"报错输出"）。形状只影响渲染，
      // 碰撞仍是圆形 radius；伤害打 8 折，压力来自"要多认一条弹道"。
      const fan = def.crystalFan;
      if (fan && this.bossPhase && this.bossPhase.phase >= (fan.fromPhase ?? 2)) {
        const n = fan.count ?? 3;
        const halfSpread = (fan.spreadDeg ?? 36) * Math.PI / 360;
        for (let i = 0; i < n; i++) {
          const t = n === 1 ? 0.5 : i / (n - 1);
          const a = baseAngle - halfSpread + halfSpread * 2 * t;
          world.spawnEnemyBullet({ trueTier: this.trueTier,
            x: this.x, y: this.y, angle: a,
            speed: bulletSpeed * (fan.speedMul ?? 1),
            damage: Math.round((def.projectileDamage ?? 10) * this.dmgScale * 0.8),
            radius: 8, range: 900 * this.rangeScale,
            color: fan.colors[i % fan.colors.length],
            shape: 'crystal',
          });
        }
      }
    }

    // 召唤小怪
    if (def.summonCount && def.summonCooldown) {
      this.summonTimer -= dt;
      if (this.summonTimer <= 0) {
        const summonRate = pm?.summonRate ?? 1;
        this.summonTimer = def.summonCooldown * summonRate;
        const diff = world.difficulty ?? { enemyCount: 1 };
        const n = Math.max(1, Math.round(def.summonCount * Math.min(1.6, diff.enemyCount) * (pm?.bulletMul ?? 1) * 0.8));
        for (let i = 0; i < n; i++) {
          const a = (i / n) * TAU + this.age;
          world.spawnEnemyById('slime', this.x + Math.cos(a) * 70, this.y + Math.sin(a) * 70);
        }
        world.fx.nova(this.x, this.y, 90, '#ffffff');
      }
    }

    // 冲锋（二阶段起）：冲撞是"压迫感"最直接的手段
    if (pm?.charge && def.chargeSpeed) {
      this.chargeTimer = (this.chargeTimer ?? 3) - dt;
      if (this.chargeTimer <= 0 && this.state === 'idle') {
        const fast = this.bossPhase.phase >= 3;
        this.chargeTimer = fast ? 2.2 : 3.4;
        this._beginCharge(nx, ny, fast ? 0.34 : 0.5);
        this.chargeSpeed = def.chargeSpeed * (fast ? 1.25 : 1);
      }
      // 复用 charger 的状态机（windup → charge → recover）
      if (this.state !== 'idle') this._runChargeStates(dt, nx, ny, 0.5);
    }

    // 185 轮：招牌技能（def.signature；没配的 Boss 完全不受影响）
    if (def.signature) runBossSignature(def.signature, this, dt, player, world, this.bossPhase?.phase ?? 1, pm);
  }

  /**
   * 近战冲锋型 Boss（第十一轮）：酸液猫将 · 派特 / 依趣苹果醋。
   *
   * 用户原话：
   *   "设计2个新的近战类型的boss，就是会冲锋和突击玩家……"
   *   "可以设计它不断追击玩家，停顿一下就冲刺玩家位置，
   *    然后有类似苹果醋的液体攻击弹幕效果。3个阶段要压迫感越来越强。"
   *
   * 和 `_boss`（远程风筝型）的**核心区别**：
   *
   *   `_boss`   保持 200px 中距离，弹幕是主要威胁 → 玩家可以拉远绕着打。
   *   `_meleeBoss` **一路贴脸**，冲刺是主要威胁 → 玩家必须持续走位，
   *                没有"安全距离"这个概念。
   *
   * 循环是：**追击 → 停顿蓄力 → 锁定你当前位置冲刺 → 硬直 → 再追击**。
   *
   * 为什么冲刺方向要"锁定"而不是追踪（见 _runChargeStates 的 track 参数）：
   * 冲刺速度 340~380 远高于玩家 200，如果一路追踪就是必中。
   * 锁死方向才有"看到预警线 → 横向闪开"的操作空间 ——
   * 这才是压迫感，而不是无解的必死。
   *
   * 三阶段的递增方式（压迫感来源逐级变化，不只是数值放大）：
   *   1 阶段：追击 + 偶尔冲刺。玩家主要感受"它一直在靠近"。
   *   2 阶段：冲刺冷却缩短、速度提升，开始甩液体弹幕。玩家开始"没空站桩"。
   *   3 阶段：冲刺几乎不停，弹幕变成大范围液体爆发，屏幕持续震动。
   *           玩家被迫一直跑，这就是"压迫感最强"。
   */
  _meleeBoss(dt, player, d, dx, dy, world) {
    const def = this.def;
    const nx = dx / d, ny = dy / d;

    // ---- 阶段推进（和 _boss 同一套双触发逻辑）----
    // 第二十五轮：tick(dt) 挪去了 update()（僵直/被晕时无敌倒计时也要走）
    let phaseUp = 0;
    if (this.bossPhase) {
      const hpRatio = this.hpPercent;
      const timeRatio = world.bossTimeRatio ?? 1;
      phaseUp = this.bossPhase.update(hpRatio, timeRatio);
      if (phaseUp) {
        const mods = this.bossPhase.mods;
        world.fx.nova(this.x, this.y, 280, mods.color || def.color);
        world.fx.explosion(this.x, this.y, 190, mods.color || def.color);
        world.camera.shake(20, 0.8);
        world.onBossPhase?.(this, phaseUp);
      }
    }
    const pm = this.bossPhase?.mods ?? null;
    const phase = this.bossPhase?.phase ?? 1;

    // 第三阶段持续震屏 —— 和 _boss 一样，这是"压迫感"最廉价的来源
    if (pm?.shake) world.camera.shake(pm.shake, 0.12);

    // ---- 第十五轮 ④：三阶段的"猛退 + 水柱" ----
    //
    // 用户原话："三阶段加一个突然停止攻击然后猛的后退一大截然后同时射出水柱
    //            （类似激光水柱，很快）攻击玩家的动作。"
    //
    // 这个动作**接管位移和开火**，所以要在下面所有逻辑之前处理并提前 return，
    // 否则贴脸移动会把后退抵消掉（两套速度互相打架，和冲锋那段的理由一样）。
    const jet = def.phaseWaterJet;
    if (jet && phase >= 3) {
      if (this.jetCd > 0) this.jetCd -= dt;

      if (this.jetPhase === 'retreat') {
        this.jetTimer -= dt;
        // 用"距离 / 时长"算速度，这样改 retreatDistance 时
        // retreatDuration 不用跟着调 —— 数值之间不耦合。
        const spd = jet.retreatDistance / Math.max(0.05, jet.retreatDuration);
        this._move(dt, this.jetDirX, this.jetDirY, spd);
        if (this.jetTimer <= 0) {
          this.jetPhase = 'windup';
          this.jetTimer = jet.windup;
          // 预警：一圈扩张的环，告诉玩家"要来了"
          world.fx?.ring?.(this.x, this.y, 120, '#8fe3ff', 0.4);
        }
        return;
      }

      if (this.jetPhase === 'windup') {
        this.jetTimer -= dt;
        // 停顿期间重新对准玩家（用户要的是"射向玩家"，
        // 退到位之后再瞄一次比用后退时的旧方向更符合直觉）
        const aimLen = Math.hypot(dx, dy) || 1;
        this.jetDirX = dx / aimLen;
        this.jetDirY = dy / aimLen;
        if (this.jetTimer <= 0) {
          this._fireWaterJet(world, def, jet, pm, phase);
          this.jetPhase = null;
          this.jetCd = jet.cooldown;
        }
        return;
      }

      // 空闲：冷却好了就起手
      if (this.jetCd <= 0 && this.state === 'idle') {
        this.jetPhase = 'retreat';
        this.jetTimer = jet.retreatDuration;
        // 后退方向 = 背离玩家
        const backLen = Math.hypot(dx, dy) || 1;
        this.jetDirX = -dx / backLen;
        this.jetDirY = -dy / backLen;
        // 起手前先"突然停止攻击"——用户明确要求先停火再退。
        // 把弹幕冷却重置，这样后退的这 0.28s 里不会顺手甩出一发扇形弹。
        this.attackCooldown = Math.max(this.attackCooldown, jet.retreatDuration + jet.windup + 0.1);
        return;
      }
    }

    // ---- 移动 ----
    //
    // 冲锋状态机接管位移时不走 _move，否则两套速度会互相抵消。
    // recover（硬直）期间也停住 —— 这是玩家的**输出窗口**，
    // 没有硬直的话近战 Boss 会变成纯粹的"一直贴着"，没有节奏。
    if (this.state === 'idle') {
      // 常驻贴脸：一路朝玩家压过去。
      //
      // 注意这里**没有"太近了就后退"的分支**（_boss 有）。
      // 近战 Boss 的定位就是"不想让你拉开"，后退会自相矛盾。
      // 第十四轮：`chaseSpeedMul` 只加**非冲刺**的贴脸移速（用户原话：
      // "让它非冲刺攻击的移动速度 +20%"）。阶段加成和它相乘，所以
      // 第三阶段的压迫感是在"已经 +20% 的基础上"再叠 —— 不会互相覆盖。
      //
      // 第十九轮末：敌人移速上限已移除，moveMul 的系数如实叠加 ——
      // 苹果醋 132 × 1.342(噩梦) × 1.2 × 1.22(三阶段) ≈ 260，比玩家
      // 默认 215 快：这是用户接受的设定（"不愿意升级速度就会被围攻"）。
      const moveMul = (pm?.moveSpeed ?? 1) * (def.chaseSpeedMul ?? 1);
      this._move(dt, nx * moveMul, ny * moveMul);
    }

    // ---- 冲刺 ----
    //
    // 阶段越高冲得越勤。第三阶段几乎"硬直完就再冲"。
    // 用 chargeTimer 而不是固定冷却，是为了让节奏有点随机感
    // （每次都精确 2.2 秒玩家会开始数拍子）。
    //
    // 第十五轮 ④：`phaseCharge` —— 用户对虾 Boss 的要求是
    // "二阶段也加个突然冲刺撞击玩家的动作"。
    // 这里的"突然"是关键字：原来的蓄力是 0.55s，玩家看得到预警线，
    // 属于"可读的威胁"。现在二阶段起改用 **0.24s 的极短蓄力**，
    // 观感上就是"毫无征兆地撞过来"——这才是用户要的那个动作。
    //
    // 保留红色预警线（渲染层按 state === 'windup' 画），所以它不是
    // 不可躲伤害：0.24s 足够玩家做出一次横向移动，但不够从容。
    const sudden = !!def.phaseCharge && phase >= 2;
    const windup = sudden ? 0.24 : (def.chargeWindup ?? 0.55);
    const chargeEnabled = phase >= 2 || this.state !== 'idle';
    if (chargeEnabled && def.chargeSpeed) {
      this.chargeTimer = (this.chargeTimer ?? 3) - dt;
      if (this.chargeTimer <= 0 && this.state === 'idle') {
        // 冲刺间隔：1 阶段 3.4s → 2 阶段 2.6s → 3 阶段 1.7s
        // 突然冲刺型再缩短一点，让"突然"这件事有足够的发生频率
        let gap = phase >= 3 ? 1.7 : phase >= 2 ? 2.6 : 3.4;
        if (sudden) gap *= 0.75;
        // 加一点随机，避免节奏被数拍子
        this.chargeTimer = gap * (0.85 + world.rng.next() * 0.3);
        const speedMul = phase >= 3 ? 1.3 : phase >= 2 ? 1.15 : 1;
        this.chargeSpeed = def.chargeSpeed * speedMul * (sudden ? 1.1 : 1);
        // **锁定玩家当前位置**（见 _runChargeStates 的 track 说明）
        this._beginCharge(nx, ny, windup);
        // 连续冲刺（依趣苹果醋的招牌）：冲完一段立刻再锁一次方向。
        // 玩家躲开第一次会以为安全，第二次正是命中时机。
        this._chargeRepeatsLeft = (def.chargeRepeats ?? 1) - 1;
      }
      if (this.state !== 'idle') {
        // track=false：方向已锁，蓄力期间不再转向
        const wasCharging = this.state;
        this._runChargeStates(dt, nx, ny, windup, false);
        // 一段冲完（进入 recover）且还有剩余次数 → 立刻接下一段
        if (wasCharging === 'charge' && this.state === 'recover'
            && this._chargeRepeatsLeft > 0) {
          this._chargeRepeatsLeft--;
          this._beginCharge(nx, ny, (def.chargeWindup ?? 0.55) * 0.55);
        }
      }
    }

    // ---- 液体弹幕 ----
    //
    // 用户要的是"类似苹果醋的液体攻击弹幕效果"。
    // 所以不是远程 Boss 那种环形密网，而是**朝玩家方向的扇形飞溅**：
    // 条数少、扇面窄，作用是把玩家从"刚好安全的角落"赶出来。
    this.attackCooldown -= dt;
    if (this.attackCooldown <= 0) {
      const diff = world.difficulty ?? { bulletCount: 1, bulletSpeed: 1 };
      const fireRate = pm?.fireRate ?? 1;
      const bulletMul = pm?.bulletMul ?? 1;
      const speedMul = pm?.bulletSpeed ?? 1;

      this.attackCooldown = (def.attackCooldown ?? 2.2) * fireRate * this.curseCooldownMul;
      const count = Math.max(1, Math.round((def.projectileCount ?? 5) * diff.bulletCount * bulletMul));
      const spread = def.projectileSpread ?? 70;
      const baseAngle = Math.atan2(dy, dx);
      const bulletSpeed = (def.projectileSpeed ?? 210) * diff.bulletSpeed * speedMul;
      // 第九十二轮：飞溅里按 crystalEvery 抽样结晶化（苹果醋 = 金黄
      // 结晶"气泡"，视觉是液体里翻起的气泡；开背虾不配 → 保持原样）。
      const ce = def.crystalEvery ?? 0;
      const cc = def.crystalColor ?? def.color;
      for (let i = 0; i < count; i++) {
        const t = count === 1 ? 0.5 : i / (count - 1);
        let a = baseAngle - (spread * Math.PI / 180) / 2 + (spread * Math.PI / 180) * t;
        a += this._bulletJitter(def);
        const crystal = ce > 0 && i % ce === ce - 1;
        world.spawnEnemyBullet({ trueTier: this.trueTier,
          x: this.x, y: this.y, angle: a,
          speed: bulletSpeed,
          damage: (def.projectileDamage ?? 10) * this.dmgScale,   // 第二十九轮：吃成长
          radius: 10,
          color: crystal ? cc : def.color,
          shape: crystal ? 'crystal' : undefined,
          // 第二十轮：飞行距离 +50%（760 → 1140）。用户："蒜蓉粉丝开背虾的
          // 弹幕也消失的比较快，飞行距离+50%"。三阶段地面酸同比例放大。
          range: 1140 * this.rangeScale,   // 第三十轮：射程成长
        });
      }
      // 第一百二十一轮：近战双 Boss（开背虾/苹果醋）的液体扇也是齐射，
      // 同样一轮一声（普通模式 4 个 Boss 都用 shoot1）。
      audio.play('shoot1');
      world.fx.nova(this.x, this.y, 46, pm?.color || def.color);

      // 第三阶段：再补一圈**慢速滞留**的液体，形成"地上都是酸"的观感。
      // 慢速是为了让玩家还能穿过去，但穿过就要吃伤害 —— 逼玩家选路线。
      // 第九十二轮：苹果醋的滞留环每隔一颗换成**青苹果色结晶蛋**
      //（phase3AppleCrystals）—— "酸得回甘"的第三阶段签名，色彩也丰富。
      if (phase >= 3) {
        const apple = def.phase3AppleCrystals;
        const n2 = Math.max(4, Math.round(count * 0.8));
        for (let i = 0; i < n2; i++) {
          const a = (i / n2) * TAU + this.age * 0.8 + this._bulletJitter(def);
          const crystal = !!apple && i % 2 === 0;
          world.spawnEnemyBullet({ trueTier: this.trueTier,
            x: this.x, y: this.y, angle: a,
            speed: bulletSpeed * 0.55,
            damage: Math.round((def.projectileDamage ?? 10) * this.dmgScale * 0.7),   // 第二十九轮：吃成长
            radius: 12,
            color: crystal ? apple.color : (pm?.color || def.color),
            shape: crystal ? 'crystal' : undefined,
            range: 930,   // 第二十轮：620 × 1.5（跟着主弹幕一起 +50%）
          });
        }
      }
    }

    // ---- 召唤小怪 ----
    if (def.summonCount && def.summonCooldown) {
      this.summonTimer -= dt;
      if (this.summonTimer <= 0) {
        const summonRate = pm?.summonRate ?? 1;
        this.summonTimer = def.summonCooldown * summonRate;
        const diff = world.difficulty ?? { enemyCount: 1 };
        const n = Math.max(1, Math.round(def.summonCount * Math.min(1.6, diff.enemyCount) * (pm?.bulletMul ?? 1) * 0.8));
        for (let i = 0; i < n; i++) {
          const a = (i / n) * TAU + this.age;
          world.spawnEnemyById('slime', this.x + Math.cos(a) * 70, this.y + Math.sin(a) * 70);
        }
        world.fx.nova(this.x, this.y, 90, '#ffffff');
      }
    }
  }
}