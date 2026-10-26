// ============================================================
// 炮台实体
//
// 玩家用 'deploy' 行为的武器放置炮台，炮台自己索敌开火。
// 这是「挂机流派」的核心：摆好阵就不需要一直手动走位输出。
//
// 设计取舍：
//  - 炮台默认永久存在（turretLife = 0），一轮里最多 turretMax 座。
//    放满之后再放，会替换掉最早的那座（否则放不下新炮台很别扭）。
//  - 炮台不吃接触伤害，也不会被敌人撞掉 —— 敌人 AI 不为炮台分心，
//    这样玩家摆阵的收益是可预期的，不会"放下去两秒就没了"。
//  - 炮台伤害走 world.dealDamage，因此暴击/吸血/击退等结算完全一致。
// ============================================================

import { Entity } from './entity.js';
import { angleTo } from '../core/math.js';

export class Turret extends Entity {
  constructor(def, x, y, ownerWeapon, player) {
    super();
    this.isTurret = true;
    this.def = def;
    this.weapon = ownerWeapon;   // 记录是哪把武器放出来的（用于统计与伤害归属）
    this.radius = 13;

    this.x = x;
    this.y = y;

    // 从玩家属性快照里取炮台相关加成
    //
    // 第十六轮：再加一层角色专属的"最终乘算"（Neon（原"neon宴会"）：炮台伤害/攻速 +20%）。
    // 走 player.mulFor 而不是并进 s.turretDamage —— 那个是**求和**的，
    // 会被道具线性稀释（买 5 个 +10% 之后，+20% 只值 +13%），
    // 而用户要的是"最终乘算"。
    const s = player.stats.values;
    // 第四十二轮：升级属性按比例折算进炮台 ——
    // 用户原话："让炮台可以吃到升级里的伤害提升，攻速提升，范围提升。
    // 比如近战，远程，元素伤害提升选择后炮台伤害跟着提升50%数值。
    // 攻速提升也提升50%炮台攻速。射程则是100%提升炮台射程。
    // 不然Neon（原"neon宴会"）这种买6炮台的角色很多升级效果都是0。"
    //  - 伤害：近战/远程/元素三类升级按 **50%** 折算进炮台伤害
    //    （通用 damage 仍 100% 计入，维持原状）；
    //  - 攻速：attackSpeed 按 **50%** 折算进炮台攻速；
    //  - 射程：range 按 **100%** 计入炮台索敌距离（与 turretRange 同池相加）。
    // 快照语义不变：部署时读一次，之后升级不影响已放下的炮台。
    const kindDmg = (s.meleeDamage + s.rangedDamage + s.elemDamage) * 0.5;
    this.damageMul = 1 + (s.damage + s.turretDamage + kindDmg) / 100;
    this.damageMul *= player.mulFor ? player.mulFor('turretDamage') : 1;
    this.range = (def.range ?? 260) * (1 + (s.turretRange + s.range) / 100);
    // 第一百三十四轮：智慧型魔棒 —— 炮台也是"炮台攻击"，射程同样 +30%
    //（最终乘算）；追踪注入见 world.spawnBullet。
    if (player.hasItem?.('smart_wand')) this.range *= 1.3;
    this.rateMul = 1 + (s.turretRate + s.attackSpeed * 0.5) / 100;
    this.rateMul *= player.mulFor ? player.mulFor('turretRate') : 1;
    // 记住放它的玩家：炮台每帧要问"这个角色的技能有没有给目标叠层"
    this.ownerPlayer = player;

    this.cooldown = 0.35;        // 落地后短暂延迟，避免瞬间齐射
    this.angle = 0;
    this.recoil = 0;
    this.spawnAnim = 0.3;
    this.shotsFired = 0;
    this.damageDealt = 0;
    this.life = def.turretLife ?? 0;  // 0 = 永久
  }

  get id_() { return this.id; }

  update(dt, player, world) {
    this.age += dt;
    if (this.spawnAnim > 0) this.spawnAnim -= dt;
    if (this.recoil > 0) this.recoil -= dt;

    // 限时炮台（当前武器都是永久，保留给以后用）
    if (this.life > 0) {
      this.life -= dt;
      if (this.life <= 0) { this.dead = true; return; }
    }

    // 索敌
    const target = world.grid.nearest(this.x, this.y, this.range, (e) => e.isEnemy && !e.dead);
    if (!target) return;
    this.angle = angleTo(this.x, this.y, target.x, target.y);

    this.cooldown -= dt * this.rateMul;
    if (this.cooldown > 0) return;

    // 炮台伤害：武器基础伤害 × 等级成长 × 炮台/全局伤害加成
    //
    // 第十六轮：「M博士来了」的伤害加成是**动态**的（受伤叠层、5 秒清零），
    // 不能像 damageMul 那样在构造时缓存 —— 那样炮台一落地就永远
    // 用着落地那一刻的层数。所以这一项每次开火现算。
    const md = this.ownerPlayer?.charRt?.mdDamageBonus ?? 0;
    const base = (this.def.damage ?? 5) * (this.weapon?.levelDamageMul ?? 1);
    const dmg = base * this.damageMul * (1 + md / 100);

    const def = this.def;
    const n = def.shots ?? 1;
    const spread = ((def.spreadAngle ?? 0) * Math.PI) / 180;
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1);
      const a = this.angle - spread / 2 + spread * t;
      world.spawnBullet({
        x: this.x, y: this.y, angle: a,
        speed: def.speed ?? 400,
        damage: dmg,
        range: this.range * 1.15,
        // 第一百一十五轮：穿甲弹 —— 炮台子弹同样享受（用户："远程，元素，
        // 炮台"）；衰减在 Bullet 内部按 itemPierce 记账。
        pierce: (def.pierce ?? 0) + (this.ownerPlayer?.apPierce ?? 0),
        itemPierce: this.ownerPlayer?.apPierce ?? 0,
        radius: 5,
        color: def.color,
        owner: this.weapon,          // 归属到放它的那把武器
        // 第一百七十八轮：**炮台弹归属到主人** —— 原来不传 ownerPlayer，
        // spawnBullet 回落成 world.player（P1）：duo 下 P2 的炮台弹伤害
        // 记到 P1 头上，更糟的是"打倒倒地的 P2"会被当成 P1 的救援刀
        //（AI 猫娘被自己的炮台"救"起来的根因）。
        ownerPlayer: this.ownerPlayer,
        ownerTurret: this,
        kind: 'turret',
        behavior: 'shot',
      });
    }

    this.shotsFired += n;
    this.cooldown = Math.max(0.1, (def.turretCooldown ?? def.cooldown ?? 3) / Math.max(0.2, this.rateMul));
    this.recoil = 0.16;
    world.fx?.ring(this.x, this.y, 16, def.color, 0.22);

    // 第十四轮 ④（用户反馈"我这边游玩一直没有武器音效"）：
    // **炮台武器原本一声不响**。
    //
    // 原因：武器音效只挂在 Weapon.update() → world.onWeaponFire 这一条路上，
    // 而炮台是独立实体（Turret），自己 spawnBullet、**从不经过 Weapon.update()**。
    // 于是 toy_turret / cardboard_turret / cat_ear_turret / meow_fortress
    // 这 4 把炮台武器全程静音，audio.js 里的 _turretSfx() 也成了死代码。
    //
    // 用户的主武器如果是炮台（neon 的本命流派就是炮台），听起来就完全像"没有武器音效"。
    // 这里补上同一个回调 —— 限流仍然全部由 audio.playWeapon 内部负责，
    // 所以炮台摆得多也不会爆音。
    world.onWeaponFire?.(def, false);
  }
}

/**
 * 炮台管理器：负责「最多 N 座、放满替换最早的一座」。
 * 放在 World 里，随波次清理。
 */
export class TurretManager {
  constructor() {
    this.items = [];
  }

  get count() { return this.items.length; }

  /**
   * 放置一座炮台。
   * @returns {{placed:boolean, replaced:Turret|null, turret:Turret}}
   */
  deploy(def, x, y, weapon, player, maxCount) {
    let replaced = null;
    if (this.items.length >= maxCount) {
      // 放满了：替换最早放的那一座（玩家不用先手动拆）
      replaced = this.items.shift();
      replaced.dead = true;
    }
    const t = new Turret(def, x, y, weapon, player);
    this.items.push(t);
    return { placed: true, replaced, turret: t };
  }

  update(dt, player, world) {
    for (const t of this.items) {
      if (!t.dead) t.update(dt, player, world);
    }
    // 清理已死的
    for (let i = this.items.length - 1; i >= 0; i--) {
      if (this.items[i].dead) this.items.splice(i, 1);
    }
  }

  /** 同一把武器已经放了几座 */
  countFor(weapon) {
    let n = 0;
    for (const t of this.items) if (t.weapon === weapon) n++;
    return n;
  }

  clear() {
    for (const t of this.items) t.dead = true;
    this.items.length = 0;
  }
}