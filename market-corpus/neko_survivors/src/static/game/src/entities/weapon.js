// ============================================================
// 武器实例与 13 种开火行为
//
// 设计：每个武器实例持有自己的冷却计时，update 时若冷却完毕
// 且满足条件就触发一次"攻击事件"，由 spawn 回调把弹丸/判定
// 交给世界的对应系统处理。
// ============================================================

import { angleTo, TAU, clamp } from '../core/math.js';

/**
 * 需要"瞄准目标"的行为：这些武器必须先锁定最近的敌人，再朝它出手。
 *
 * 关键：sweep / thrust 这类近战也必须自动瞄准。
 * 若让它们依赖 player.facing（只在玩家移动时更新），玩家站着不动或
 * 只做小幅走位时就会朝空处挥击 —— 表现为"武器完全不造成伤害"。
 *
 * 不需要瞄准的是纯范围型：spin / nova / aura / orbit。
 */
const AIMED_BEHAVIORS = new Set([
  'sweep', 'thrust', 'shot', 'spread', 'homing', 'bounce', 'chain', 'beam',
  'quake', 'flurry',
]);

/**
 * 第一百七十五轮：近战救援 —— 用户实测"无法在 1 秒救起 1 倒队友"。
 * 排查结论：弹道救援（bullet.checkEnemyHit）**不吃任何无敌帧**，真凶是
 * **近战武器完全没接救援判定** —— sweep/thrust/quake 等直接
 * `queryArc → dealDamage` 打敌人，倒地队友在判定里根本不存在，
 * 拿近战的玩家紫条一动不动。这里给所有近战行为补上队友查询：
 * 命中倒地队友 = 一刀救援（0.2s gate 保留，防同帧多弹连计）。
 */
function rescueDownedAllies(player, world, x, y, range) {
  for (const ally of (world.players?.() ?? [])) {
    if (!ally || ally === player || !ally.downed) continue;
    const dx = ally.x - x, dy = ally.y - y;
    const rr = range + 40;   // 与弹道救援同款 +40 容差
    if (dx * dx + dy * dy > rr * rr) continue;
    const revived = ally.rescueHit();
    // gate 刚置位（=0.2）说明这一刀真的计了数，不是 0.2s 内的重复命中
    if (revived) {
      world.fx?.nova?.(ally.x, ally.y, 26, '#7cf3c4');
      world.fx?.damageText?.(ally.x, ally.y - ally.radius - 10, '救援成功！', false, '#7cf3c4');
    } else if (!ally._rescueGate || ally._rescueGate >= 0.19) {
      world.fx?.sparkle?.(ally.x, ally.y, '#7cf3c4');
      // 第一百七十八轮：进度取整显示（救援计数已整数化，这里兜底取整）
      world.fx?.damageText?.(ally.x, ally.y - ally.radius - 10,
        `救援 +1（${Math.round(ally.rescueNeed - ally.rescueProgress)}/${ally.rescueNeed}）`, false, '#7cf3c4');
    }
  }
}

export class WeaponInstance {
  constructor(def, level = 1) {
    this.def = def;
    this.level = level;
    this.cooldown = 0;
    this.angle = 0;       // 环绕类武器的当前角度
    this.spinAngle = 0;   // 挥舞动画角度（纯视觉）
    this.animT = 0;       // 攻击动画剩余时间
    this.animDuration = 0;
    this.justFired = false;
    this.kills = 0;
    this.damageDealt = 0;
  }

  get id() { return this.def.id; }
  get tier() { return this.def.tier; }

  /** 等级带来的数值成长：每级 +12% 伤害，微降冷却 */
  get levelDamageMul() { return 1 + (this.level - 1) * 0.12; }
  get levelCooldownMul() { return Math.max(0.6, 1 - (this.level - 1) * 0.04); }

  /** 计算本次攻击的实际伤害（不含暴击） */
  computeDamage(player) {
    const s = player.stats.values;
    let base = (this.def.damage ?? 5) * this.levelDamageMul;

    // 专精加成
    let kindBonus = 0;
    if (this.def.kind === 'melee') kindBonus += s.meleeDamage;
    else if (this.def.kind === 'ranged') kindBonus += s.rangedDamage;
    else if (this.def.kind === 'element') kindBonus += s.elemDamage;

    const global = s.damage;
    const mul = 1 + (global + kindBonus) / 100;
    let out = base * mul;

    // ---- 第十六轮：角色专属的"最终乘算" ----
    //
    // 这些加成**不能**写进 StatSheet 的 pct（那会和道具线性相加，
    // 玩家买 5 个 +10% 就把它稀释成 +8%），也不能写进 finalMul
    // （percent 属性的 base 是 0，乘出来恒为 0）。
    // 所以只能在伤害算完之后，按武器种类单独乘。
    //
    // 按 kind 分派而不是用一个全局系数，是因为 Neon（原"neon宴会"）的
    // "炮台 +20%、其他 -20%"要求两者走**不同**的系数。
    if (player.mulFor) {
      if (this.def.kind === 'melee') out *= player.mulFor('meleeDamage');
      else if (this.def.kind === 'ranged') out *= player.mulFor('rangedDamage');
      else if (this.def.kind === 'element') out *= player.mulFor('elemDamage');
      // 本体武器的"其他伤害"（Neon（原"neon宴会"）的 -20%）
      out *= player.mulFor('damage');
    }

    // ---- 第十六轮：开发猫猫「M博士来了」的伤害侧 ----
    //
    // 用户原话："受到伤害时伤害+3%，移速+3%持续5秒可无限叠加，
    //           5秒没受伤时buff清零。"
    //
    // 移速那一半在 player 的移动块里（走 mdMoveBonus），
    // 伤害这一半必须在这里 —— 因为它是"打出去的伤害"，
    // 而 computeDamage 是所有武器伤害的唯一出口。
    //
    // 用**加法**并进全局伤害百分比，而不是在 out 上再乘一次：
    // 用户说的是"伤害 +3%"，和道具的 +10% 是同一种东西，
    // 应该线性相加（叠 10 层 = +30%，正好等于三件 +10% 的道具）。
    // 如果改成乘法，10 层会变成 ×1.34，比用户要的更猛，
    // 而且"可无限叠加"会失控得更快。
    const md = player.charRt?.mdDamageBonus ?? 0;
    if (md > 0) out *= 1 + md / 100;

    // 体检修复（第九十二轮）：莱万汀的 +80% 最终伤害乘区。
    // （此前误走 stats.addFinalMul('damage',…) 被 percent 守卫静默拒绝，
    // 玩家打赢芙兰只吃到 +50% 承伤惩罚、拿不到收益。）
    if ((player.dealtFinalMul ?? 1) !== 1) out *= player.dealtFinalMul;

    // ---- 第一百六十轮：四重存在（莱万汀分身）的每发伤害补正 ----
    // 分身属性吃 25%，但攻速压到 35%（3 个分身合计射弹量 ≈ 玩家 105%，
    // 弹幕负载几乎不变）；这里把每发补到 71.4%，总 DPS 恒为玩家的 25%。
    // 本体没有这个字段（undefined → ?? 1），伤害链完全不受影响。
    if (player.cloneDmgScale != null) out *= player.cloneDmgScale;

    // ---- 第一百三十四轮：龙虾抱枕 —— 每 1% 吸血，伤害 +1%（最终乘算）----
    // 动态乘区：吸血面板会变（升级/其他道具），所以在这里现算而不是
    // 拿道具时折算进 dealtFinalMul。0 吸血 = 无加成。
    if (player.hasItem?.('lobster_pillow')) {
      const ls = player.stats.values.lifesteal ?? 0;
      if (ls > 0) out *= 1 + ls / 100;
    }

    return out;
  }

  /** 实际冷却（秒） */
  computeCooldown(player) {
    const s = player.stats.values;
    // 第十六轮：攻速的最终乘算也走 mulFor（库洛 +20%、开发猫猫 -12%，
    // 第二十三轮 0.8 → 0.88）。
    // 乘在 asMul 上而不是改 s.attackSpeed，理由同 computeDamage。
    const charAs = player.mulFor ? player.mulFor('attackSpeed') : 1;
    // 第一百零八轮：血月 —— 全场攻速 +10% 起步、每 5 秒再 +10%（敌我）。
    // cooldownDiv = 1/(1+加成)，乘上 asMul 等价于冷却缩短。
    const wxAtk = player.world?.weather?.cooldownDiv ?? 1;
    const asMul = (1 + s.attackSpeed / 100) * charAs * wxAtk;
    const base = (this.def.cooldown ?? 1) * this.levelCooldownMul;
    return Math.max(0.06, base / Math.max(0.2, asMul));
  }

  /** 实际射程 */
  computeRange(player) {
    const s = player.stats.values;
    let r = this.def.range ?? 60;
    const rangeStat = s.range ?? 0;
    if (this.def.kind !== 'melee') {
      r *= 1 + rangeStat / 100;
    } else {
      // 第三十二轮：近战武器也吃"攻击距离"加成 ——
      // 用户原话："让近战武器也享受攻击距离加成，攻击距离+100%能砍的
      // 距离加50%，如果是360度的盾武器则扩大一圈造成aoe150%的攻击范围。"
      // 档位：普通近战（横扫/突刺/震地/连击）打 **五折**（+100% 属性
      // → 砍的距离 +50%）；360 度武器（spin 咸鱼盾 / orbit 逗猫棒）
      // 第三十八轮从 150% 下调到 **85%** ——
      // 用户："360度武器范围变大这个效果好像锤子类武器也吃，现在一下
      // 半个屏幕，把360度武器的攻击范围强化从150%改到85%。不然锤子类
      // 武器过于强大。"（锤子的震地其实是五折档，但大范围近战整体收敛
      // 到 85% 的口径从此定死。）
      // 第十六轮库洛"近战武器攻击距离 +20%"保持原有专属乘区，叠在最后。
      const rate = (this.def.behavior === 'spin' || this.def.behavior === 'orbit') ? 0.85 : 0.5;
      let mul = 1 + (rangeStat / 100) * rate;
      if (player.charRt?.id === 'kuro') mul *= player.charRt.params.meleeRangeMul ?? 1.2;
      r *= mul;
    }
    // ---- 第一百三十四轮：智慧型魔棒 —— 远程攻击射程 +30%（最终乘算）----
    // "最终乘算" = 乘在所有 range 属性加成之后；近战不吃（用户说的是
    // "远程攻击"）。360 度/环绕武器的"范围"不是射程，同样不吃。
    if (this.def.kind !== 'melee' && player.hasItem?.('smart_wand')) {
      r *= 1.3;
    }
    return r;
  }

  /**
   * @param {number} dt
   * @param {Player} player
   * @param {World} world  提供 grid / spawn* / dealDamage 等接口
   */
  update(dt, player, world) {
    this.justFired = false;
    if (this.animT > 0) this.animT -= dt;

    // 环绕类武器持续旋转
    if (this.def.behavior === 'orbit') {
      this.angle += (this.def.orbitSpeed ?? 2.4) * dt * (1 + player.stats.values.attackSpeed / 200);
      // 环绕武器也有命中冷却，逐帧判定
      this._updateOrbit(dt, player, world);
      return;
    }

    this.cooldown -= dt;
    if (this.cooldown > 0) return;

    // 找最近目标（环绕/光环/新星这类纯范围武器不需要）
    const needsTarget = AIMED_BEHAVIORS.has(this.def.behavior);
    const range = this.computeRange(player);
    let target = null;
    if (needsTarget) {
      const searchRange = this.def.behavior === 'beam' ? range : range * 1.25;
      target = world.grid.nearest(player.x, player.y, searchRange, (e) => e.isEnemy && !e.dead);
      if (!target) return; // 没有目标就不开火，不浪费冷却
    }

    this._fire(player, world, target);
    this.cooldown = this.computeCooldown(player);
    this.justFired = true;

    // 攻击音效（第十轮，用户要求"给所有武器加个攻击音效"）。
    // 走 world 回调，不直接依赖音频单例 —— 武器逻辑保持纯逻辑，
    // 测试里不接这个回调就等于静音，不需要 mock 音频。
    // 播放限流在音频系统内部（见 audio.playWeapon），
    // 所以这里**每次开火都调**，由音频层决定这次要不要发声。
    world.onWeaponFire?.(this.def, false);

    // 视觉动画时长
    this.animDuration = Math.min(0.35, this.computeCooldown(player) * 0.6);
    this.animT = this.animDuration;
    this.spinAngle = 0;
  }

  /**
   * 第一百一十六轮：统一瞄准口 —— 用户："玩家按住左键的时候，所有武器
   * 的攻击方向都瞄准左键的落点。方便玩家在高难时候主动调整攻击目标，
   * 松开左键恢复自己索敌的设计。"
   * 优先级：① 玩家按住左键（main._updatePlaying 把鼠标换算成世界坐标
   * 存进 player.aimAt）→ 朝落点；② 松开 → 恢复原索敌：最近目标；
   * ③ 没有目标 → 朝玩家正面。所有弹道与近战弧/光束都从这一个口拿方向。
   */
  _aim(player, target) {
    if (player.aimAt) return angleTo(player.x, player.y, player.aimAt.x, player.aimAt.y);
    return target ? angleTo(player.x, player.y, target.x, target.y) : player.facing;
  }

  _fire(player, world, target) {
    const dmg = this.computeDamage(player);
    const range = this.computeRange(player);
    const def = this.def;

    switch (def.behavior) {
      // ---------------- 近战 ----------------
      case 'sweep': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        const half = ((def.arc ?? 120) / 2) * Math.PI / 180;
        const hits = world.queryArc(player.x, player.y, facing, half, range);
        for (const e of hits) {
          world.dealDamage(player, this, e, dmg, { knockback: def.knockback ?? 80, fromX: player.x, fromY: player.y });
        }
        rescueDownedAllies(player, world, player.x, player.y, range);   // 第一百七十五轮：近战救援
        world.fx.slash(player.x, player.y, facing, range, def.arc ?? 120, def.color, { player: true });
        break;
      }

      case 'thrust': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        const w = def.width ?? 22;
        const ex = player.x + Math.cos(facing) * range;
        const ey = player.y + Math.sin(facing) * range;
        const hits = world.queryCapsule(player.x, player.y, ex, ey, w / 2);
        for (const e of hits) {
          world.dealDamage(player, this, e, dmg, { knockback: def.knockback ?? 60, fromX: player.x, fromY: player.y });
        }
        rescueDownedAllies(player, world, player.x, player.y, range);   // 第一百七十五轮：近战救援
        world.fx.thrust(player.x, player.y, ex, ey, w, def.color, { player: true });
        // 第一百三十九轮 E：顿帧感 —— 命中时轻微震屏（幅度 2 / 0.1s），
        // 配合枪影"停留 0.05s 再缩回"，落空不震。
        if (hits.length > 0) world.camera?.shake(2, 0.1);
        break;
      }

      // ---------------- 近战重击（大范围冲击波） ----------------
      case 'quake': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        // 主判定：以落点为中心的圆形冲击波，比横扫更"重"
        const cx = player.x + Math.cos(facing) * range * 0.45;
        const cy = player.y + Math.sin(facing) * range * 0.45;
        const hits = world.queryArc(player.x, player.y, facing, Math.PI, range);
        for (const e of hits) {
          world.dealDamage(player, this, e, dmg, {
            knockback: def.knockback ?? 300, fromX: player.x, fromY: player.y,
          });
          // 僵直与破甲
          // 第二十五轮：Boss 不吃眩晕 —— 用户 bug 报告："蒜蓉粉丝开背虾的
          // 无敌时间变成了无限并且不攻击了"（贴脸近战无限刷新僵直把 Boss
          // 整个卡死）。小怪照旧吃晕；Boss 的硬直节奏只属于它自己的
          // 冲刺 recover，不归武器管。
          if (def.stun && !e.boss) e.stun = Math.max(e.stun ?? 0, def.stun);
          if (def.armorShred) e.armorShred = Math.max(e.armorShred ?? 0, def.armorShred);
        }
        world.fx.explosion(cx, cy, range * 0.8, def.color, { player: true });
        world.fx.ring(cx, cy, range, def.color, 0.4, { player: true });
        rescueDownedAllies(player, world, cx, cy, range);   // 第一百七十五轮：近战救援
        world.camera?.shake(6, 0.22);
        break;
      }

      // ---------------- 多段突刺（暴击流） ----------------
      case 'flurry': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        const n = def.thrusts ?? 5;
        const spread = ((def.spreadAngle ?? 50) * Math.PI) / 180;
        const w = def.width ?? 22;
        for (let i = 0; i < n; i++) {
          const t = n === 1 ? 0.5 : i / (n - 1);
          const a = facing - spread / 2 + spread * t;
          const ex = player.x + Math.cos(a) * range;
          const ey = player.y + Math.sin(a) * range;
          const hits = world.queryCapsule(player.x, player.y, ex, ey, w / 2);
          for (const e of hits) {
            world.dealDamage(player, this, e, dmg, {
              knockback: def.knockback ?? 60, fromX: player.x, fromY: player.y,
            });
          }
          world.fx.thrust(player.x, player.y, ex, ey, w, def.color, { player: true });
        }
        rescueDownedAllies(player, world, player.x, player.y, range);   // 第一百七十五轮：近战救援
        break;
      }

      // ---------------- 部署炮台 ----------------
      case 'deploy': {
        const res = world.deployTurret(player, this);
        if (res?.replaced) {
          world.fx?.sparkle(res.replaced.x, res.replaced.y, def.color, 3);
        }
        break;
      }

      case 'spin': {
        // 短时间内多次判定贴身敌人
        const hits = world.grid.query(player.x, player.y, range, []);
        for (const e of hits) {
          if (!e.isEnemy || e.dead) continue;
          world.dealDamage(player, this, e, dmg, { knockback: 40, fromX: player.x, fromY: player.y });
        }
        rescueDownedAllies(player, world, player.x, player.y, range);   // 第一百七十五轮：近战救援
        world.fx.spin(player.x, player.y, range, def.color, { player: true });
        break;
      }

      // ---------------- 远程 ----------------
      case 'shot': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        world.spawnBullet({
          x: player.x, y: player.y, angle: facing,
          speed: def.speed ?? 420, damage: dmg, range,
          // 第一百一十五轮：穿甲弹（player.apPierce）叠在武器自带穿透上
          pierce: (def.pierce ?? 0) + (player.apPierce ?? 0),
          itemPierce: player.apPierce ?? 0, radius: 6,
          color: def.color, owner: this, kind: def.kind,
          behavior: 'shot',
        });
        break;
      }

      case 'spread': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        const n = def.shots ?? 3;
        const spread = (def.spreadAngle ?? 20) * Math.PI / 180;
        for (let i = 0; i < n; i++) {
          const t = n === 1 ? 0.5 : i / (n - 1);
          const a = facing - spread / 2 + spread * t;
          world.spawnBullet({
            x: player.x, y: player.y, angle: a,
            speed: def.speed ?? 400, damage: dmg, range,
            pierce: (def.pierce ?? 0) + (player.apPierce ?? 0),
            itemPierce: player.apPierce ?? 0, radius: 6,
            color: def.color, owner: this, kind: def.kind,
            behavior: 'shot',
          });
        }
        break;
      }

      case 'homing': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        world.spawnBullet({
          x: player.x, y: player.y, angle: facing,
          speed: def.speed ?? 300, damage: dmg, range,
          pierce: (def.pierce ?? 0) + (player.apPierce ?? 0),
          itemPierce: player.apPierce ?? 0, radius: 7,
          color: def.color, owner: this, kind: def.kind,
          behavior: 'homing', homing: def.homing ?? 3,
          explosionRadius: def.explosionRadius ?? 0,
        });
        break;
      }

      case 'bounce': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        world.spawnBullet({
          x: player.x, y: player.y, angle: facing,
          speed: def.speed ?? 420, damage: dmg, range: range * 1.8,
          // 弹跳自带 0 穿透 —— 但穿甲弹照样给（用户：远程攻击可穿透）
          pierce: player.apPierce ?? 0,
          itemPierce: player.apPierce ?? 0, radius: 6,
          color: def.color, owner: this, kind: def.kind,
          behavior: 'bounce', bounces: def.bounces ?? 3, bounced: 0,
        });
        break;
      }

      case 'beam': {
        const facing = this._aim(player, target);
        this.attackAngle = facing;
        this.beamUntil = 0.18;
        const ex = player.x + Math.cos(facing) * range;
        const ey = player.y + Math.sin(facing) * range;
        const hits = world.queryCapsule(player.x, player.y, ex, ey, (def.width ?? 12) / 2);
        for (const e of hits) {
          world.dealDamage(player, this, e, dmg, { knockback: 20, fromX: player.x, fromY: player.y, pierceArmor: true });
        }
        world.fx.beam(player.x, player.y, ex, ey, def.width ?? 12, def.color, { player: true });
        break;
      }

      case 'chain': {
        // 闪电链：从最近目标开始，逐跳找最近的未命中敌人
        let current = target;
        let fromX = player.x, fromY = player.y;
        const hitIds = new Set();
        const chainRange = def.chainRange ?? 150;
        const maxChains = def.chains ?? 4;
        const points = [{ x: player.x, y: player.y }];
        for (let i = 0; i < maxChains && current; i++) {
          if (hitIds.has(current.id)) break;
          hitIds.add(current.id);
          points.push({ x: current.x, y: current.y });
          world.dealDamage(player, this, current, dmg, { knockback: 30, fromX, fromY });
          fromX = current.x; fromY = current.y;
          current = world.grid.nearest(fromX, fromY, chainRange, (e) => e.isEnemy && !e.dead && !hitIds.has(e.id));
        }
        world.fx.chain(points, def.color, { player: true });
        break;
      }

      // ---------------- 范围 ----------------
      case 'nova': {
        const hits = world.grid.query(player.x, player.y, range, []);
        for (const e of hits) {
          if (!e.isEnemy || e.dead) continue;
          world.dealDamage(player, this, e, dmg, { knockback: def.knockback ?? 140, fromX: player.x, fromY: player.y });
        }
        world.fx.nova(player.x, player.y, range, def.color, { player: true });
        break;
      }

      // ---------------- 持续型 ----------------
      case 'aura': {
        const hits = world.grid.query(player.x, player.y, range, []);
        for (const e of hits) {
          if (!e.isEnemy || e.dead) continue;
          world.dealDamage(player, this, e, dmg, { knockback: 0, fromX: player.x, fromY: player.y });
        }
        break;
      }

      default:
        break;
    }
  }

  /** 环绕武器：每帧对接触到的敌人造成伤害（每个敌人有独立冷却） */
  _updateOrbit(dt, player, world) {
    const def = this.def;
    const count = def.orbitCount ?? 1;
    // 第三十二轮：环绕半径走 computeRange —— 原来这里是裸 def.range，
    // 攻击距离属性完全加不上；360 度档按 150% 结算（见 computeRange）。
    const radius = this.computeRange(player);
    const dmg = this.computeDamage(player);
    const now = player.timeAlive;
    // 第三十三轮：球的命中半径数据化（逗猫棒 32，默认 16）——
    // "作为咸鱼盾的上位几乎打不中人"，球小碰到就滑过去了。
    const ballR = def.orbitBallRadius ?? 16;
    if (!this._orbitHits) this._orbitHits = new Map();

    // 清理过期的命中记录
    for (const [id, t] of this._orbitHits) {
      if (now - t > 0.4) this._orbitHits.delete(id);
    }

    for (let i = 0; i < count; i++) {
      const a = this.angle + (i / count) * TAU;
      const ox = player.x + Math.cos(a) * radius;
      const oy = player.y + Math.sin(a) * radius;
      const hits = world.grid.query(ox, oy, ballR, []);
      for (const e of hits) {
        if (!e.isEnemy || e.dead) continue;
        const last = this._orbitHits.get(e.id) ?? -99;
        if (now - last < 0.4) continue;
        this._orbitHits.set(e.id, now);
        world.dealDamage(player, this, e, dmg, { knockback: 30, fromX: ox, fromY: oy });
        // 第一百三十九轮 C：白色十字星光（用户："球击中敌人时，爆出白色
        // 的十字星光，类似猫咪拍打玩具"）—— IMPACT 放射星芒 + 一粒白火花
        world.fx.impact(ox, oy, 0.32, '#ffffff', a);
        world.fx.sparkle(ox, oy, '#ffffff', 1);
      }
    }
    // 第一百七十五轮：环绕球也能救队友（逗猫棒贴身转圈时顺路救援；
    // gate 0.2s 天然限频，不会刷屏）
    rescueDownedAllies(player, world, player.x, player.y, radius);
    this.orbitPositions = [];
    for (let i = 0; i < count; i++) {
      const a = this.angle + (i / count) * TAU;
      this.orbitPositions.push({ x: player.x + Math.cos(a) * radius, y: player.y + Math.sin(a) * radius });
    }
    this.orbitRadius = radius;

    // 第一百三十九轮 C：拖尾残影采样 —— 每 0.035s 记一帧球位（每球一条），
    // renderer 画 4 段渐隐残影，体现高速旋转。
    if (this._trailLen !== count) {
      this._trailLen = count;
      this._trail = Array.from({ length: count }, () => []);
    }
    if (now - (this._trailT ?? -1) > 0.035) {
      this._trailT = now;
      for (let i = 0; i < count; i++) {
        const t = this._trail[i];
        t.push({ x: this.orbitPositions[i].x, y: this.orbitPositions[i].y });
        if (t.length > 4) t.shift();
      }
    }
  }
}

/** 按行为分类，供 UI 显示 */
export const BEHAVIOR_LABEL = {
  sweep: '横扫', thrust: '突刺', spin: '旋击', shot: '射击',
  spread: '散射', homing: '追踪', bounce: '弹跳', beam: '光束',
  chain: '连锁', nova: '爆发', aura: '光环', orbit: '环绕',
  // 第二轮新增的流派（之前漏了标签，属性面板会显示 undefined）
  quake: '震地', flurry: '连击', deploy: '炮台',
};