// ============================================================
// 四重存在 —— 莱万汀的分身（第一百六十轮）
//
// 用户设计："每5秒判定一次，有20%概率变出3个玩家分身，继承玩家的25%
// 属性（血量，伤害，护甲，攻速之类的都是玩家的25%），武器和玩家相同。
// ai会攻击附近的敌人（比较自由自在不用非得跟着玩家），然后分身上限为
// 3个……死了一个分身，就从玩家位置再生成一个分身。"
//
// 性能口径（用户担忧炮台流满屏炮台卡死，并授权削弱方案）：
//   "比如调高分身攻击，降低分身攻速……让她能总dps维持在玩家的25%"
//   → 分身攻速 = 玩家的 35%（武器冷却 ×1/0.35），每发伤害补到玩家的
//   71%（cloneDmgScale），总 DPS 恒为玩家的 25%。
//   → 3 个分身合计射弹量 ≈ 玩家的 105%（3×0.35），弹幕负载几乎不变。
//   → 炮台上限走 25% 属性后取整：炮台流全场最多 玩家 N 座 + 分身 3 座。
//   → 分身渲染是轻量光影（无立绘采样），每帧开销 ≈ 3 个普通怪。
//   → 敌人不会主动索敌分身（AI 仍锁玩家）；敌人顺路撞上才会磨分身血。
// ============================================================
import { WeaponInstance as Weapon } from './weapon.js';

/** 分身攻速比例（= 玩家攻速 × PHANTOM_APS） */
const PHANTOM_APS = 0.35;

export class PhantomClone {
  /**
   * @param player 玩家本体（读属性/武器模板；奖励归属仍归玩家）
   * @param world  World 实例（grid/fx/dealDamage）
   * @param x,y    出生点（用户规格：从玩家位置生成）
   * @param rng    世界随机流（走独立 fork，不扰动波次复现）
   */
  constructor(player, world, x, y, rng) {
    this.world = world;
    this.isPhantom = true;
    this.isEnemy = false;
    this.dead = false;
    // dealDamage/_onKill 会累计这两个字段（world.js 1009/1067 行）——
    // 没有初始值会变成 NaN。伤害统计无意义（不进结算），但必须能加。
    this.kills = 0;
    this.damageDealt = 0;
    this.x = x; this.y = y;
    this.facing = player.facing ?? 0;
    this.radius = Math.max(10, (player.radius ?? 14) * 0.8);
    // 202 轮（用户："莱万汀召唤的3个玩家分身是紫色矩形小猫，让她们变成玩家的样子"）：
    // 分身直接复用玩家的绘制函数（renderer._drawPlayer），所以把它读的那 15 个字段补齐：
    // 立绘 id / 配色 / 朝向 / 走路相位 / 眨眼 / 受击闪 / 无敌闪 / 护盾（分身没有护盾，恒 0）。
    this.characterId = player.characterId;
    this.color = player.color;
    this.accent = player.accent;
    this.blinkPhase = 0;
    this.hurtFlash = 0;
    this.invuln = 0;
    this.shield = 0; this.maxShield = 0;
    this.isMoving = false;
    this.walkPhase = 0;
    this.spawnT = 0;                     // 出场特效计时
    this.hitFlash = 0;

    // ---- 属性副本：25% ----
    // 血/甲/移速线性 ×0.25；攻速按"攻速 = 1 + attackSpeed/100"的公式
    // 反解出 35% 攻速对应的加数；伤害走 cloneDmgScale 补正（见下）。
    const s = player.stats.values;
    const asClone = (PHANTOM_APS * (1 + (s.attackSpeed ?? 0) / 100) - 1) * 100;
    this.stats = {
      values: {
        maxHp: Math.max(1, Math.round((s.maxHp ?? 20) * 0.25)),
        hpRegen: (s.hpRegen ?? 0) * 0.25,
        armor: (s.armor ?? 0) * 0.25,
        moveSpeed: (s.moveSpeed ?? 0) * 0.25 + 40,   // 底速保底，别慢成爬行
        attackSpeed: asClone,
        damage: (s.damage ?? 0) * 0.25,
        meleeDamage: (s.meleeDamage ?? 0) * 0.25,
        rangedDamage: (s.rangedDamage ?? 0) * 0.25,
        elemDamage: (s.elemDamage ?? 0) * 0.25,
        critChance: (s.critChance ?? 0) * 0.25,
        critDamage: (s.critDamage ?? 0) * 0.25,
        lifesteal: 0,                    // 分身不吸血（避免刷血永动）
        pickupRange: 0, luck: 0, harvesting: 0,
        turretDamage: (s.turretDamage ?? 0) * 0.25,
        turretRate: (s.turretRate ?? 0) * 0.25,
        turretRange: (s.turretRange ?? 0) * 0.25,
        // 炮台上限 25% 取整、至少 1 座：炮台流全场 = 玩家 N 座 + 分身 ≤3 座
        turretMax: Math.max(1, Math.round((s.turretMax ?? 1) * 0.25)),
      },
    };
    this.maxHp = this.stats.values.maxHp;
    this.hp = this.maxHp;

    // ---- 武器：与玩家同款同级 ----
    this.weapons = player.weapons.map((w) => new Weapon(w.def, w.level));

    // ---- 伤害补正通道（weapon.computeDamage 末尾读取）----
    // 口径校准：damage 等通道是"加数百分比"，25% 属性并不等于每发 25%
    //（damage=50 的玩家 → 分身 12.5，每发自然比是 1.125/1.5 = 0.75）。
    // 所以在构造时用主武器**实测标定**：把每发比钉死在 0.25/0.35 = 71.4%，
    // 配 35% 攻速 ⇒ 总 DPS 恒为玩家的 25%，任意玩家 build 都成立。
    this.dealtFinalMul = 1;   // 莱万汀的最终乘区只属于本体，分身不吃
    this.cloneDmgScale = 1;
    const w0 = this.weapons[0];
    if (w0) {
      const dP = w0.computeDamage(player) || 1;
      const dC = w0.computeDamage(this) || 1;
      this.cloneDmgScale = (0.25 / PHANTOM_APS) / (dC / dP);
    }
  }

  update(dt, world, player) {
    if (this.dead) return;
    this.age = (this.age ?? 0) + dt;
    this.spawnT = Math.max(0, this.spawnT - dt);
    this.hitFlash = Math.max(0, this.hitFlash - dt);
    this.hp = Math.min(this.maxHp, this.hp + (this.stats.values.hpRegen ?? 0) * dt);

    // 202 轮（用户："移速和玩家一致，25%移速实在太慢了"）：移动速度 = 玩家本体当前的实际移速（含角色被动 / 道具 /
    // 天气 / 晴岚 等全部乘区，见 Player.curSpeed）。其它属性（血 / 甲 / 伤害 / 攻速）仍按 25% 继承，只有"腿"和玩家一样快。
    // （旧值 = 0.25×移速加成 + 40 ≈ 40~60 px/s，而玩家本体 ≥215 px/s，分身一直被甩在身后。）
    const SP = player.curSpeed ?? player.baseSpeed ?? 215;
    const x0 = this.x, y0 = this.y;
    // ---- AI：有怪打怪（自由索敌），没怪松弛跟随玩家 ----
    let target = null;
    try {
      target = world.grid.nearest(this.x, this.y, 420, (e) => e.isEnemy && !e.dead) || null;
    } catch { /* grid 未就绪 */ }

    let tx; let ty;
    if (target) {
      const d = Math.hypot(target.x - this.x, target.y - this.y);
      // 近战主武器贴脸，其余在射程七成处站桩
      const mainKind = this.weapons[0]?.def?.kind ?? 'ranged';
      const want = mainKind === 'melee' ? Math.max(30, (target.radius ?? 12) + 24)
        : Math.max(120, (this.weapons[0]?.def?.range ?? 260) * 0.7);
      if (d > want) {
        const step = Math.min(SP * dt, d - want);   // 不越过站位点，避免在站位附近抖动
        this.x += ((target.x - this.x) / d) * step;
        this.y += ((target.y - this.y) / d) * step;
      }
      tx = target.x; ty = target.y;
    } else {
      const d = Math.hypot(player.x - this.x, player.y - this.y);
      if (d > 150) {
        const step = Math.min(SP * dt, d - 150);
        this.x += ((player.x - this.x) / d) * step;
        this.y += ((player.y - this.y) / d) * step;
      }
    }

    // 走路动画（_drawPlayer 读 isMoving / walkPhase）+ 受击闪 + 出场闪烁
    const moved = Math.hypot(this.x - x0, this.y - y0) > 0.01;
    this.isMoving = moved;
    this.walkPhase = moved ? this.walkPhase + dt * 11 : 0;
    this.hurtFlash = this.hitFlash;
    this.invuln = this.spawnT;       // 出场 0.45s 半透明闪烁（_drawPlayer 的无敌闪烁通道）
    // 面向目标（武器瞄准用）
    if (tx != null) this.facing = Math.atan2(ty - this.y, tx - this.x);
    else if (d2(player, this) > 40) this.facing = Math.atan2(player.y - this.y, player.x - this.x);

    // ---- 武器开火（与玩家同款同级；坐标 owner=分身，奖励归属=玩家）----
    for (const w of this.weapons) {
      w.update(dt, this, world);
    }

    // ---- 接触磨损：敌人顺路撞上会磨分身血（AI 不追分身）----
    let touch = 0;
    try {
      for (const e of world.grid.query(this.x, this.y, this.radius + 26, [])) {
        if (!e.isEnemy || e.dead) continue;
        const d = Math.hypot(e.x - this.x, e.y - this.y);
        if (d < (e.radius ?? 12) + this.radius) touch += (e.damage ?? 5) * 1.5;
      }
    } catch { /* */ }
    if (touch > 0) {
      this.hp -= touch * dt;
      this.hitFlash = 0.12;
    }

    // （不越界：分身只朝敌人/玩家移动，二者都在场内，无需额外边界裁剪）

    if (this.hp <= 0) {
      this.dead = true;
      world.fx.explosion(this.x, this.y, 46, '#ffb3d4');
      world.fx.sparkle(this.x, this.y, '#ff7bac', 10);
    }
  }
}

function d2(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
