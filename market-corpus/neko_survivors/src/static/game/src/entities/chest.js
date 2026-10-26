// ============================================================
// 宝箱
//
// 需求（用户原话）：
//   "增加地图上随机出现的宝箱和宝箱怪物。...
//    然后地图上随机出现的宝箱则是有概率给道具有概率是医疗包。
//    宝箱固定不动，需要攻击打破。"
//
// 所以宝箱：
//  - **固定不动**（不移动、不追人）
//  - **需要攻击打破**（有血量，走和敌人一样的受击流程）
//  - 掉落二选一：**道具**（进"待结算"队列）或**医疗包**
//  - 有光效，方便在地图上被发现
//
// 它被放进 world.enemies 列表里（这样武器/子弹/炮台/宠物的索敌
// 全都不用改就能打它），但标记 isChest = true，
// 敌人 AI 相关的地方都跳过它。
// ============================================================

import { Entity } from './entity.js';
import { TAU } from '../core/math.js';
import { effectiveLuckFor } from '../core/stats.js';

export class Chest extends Entity {
  constructor(x, y, opts = {}) {
    super();
    this.isChest = true;
    this.isEnemy = true;      // 让武器/子弹的索敌逻辑能打到它
    this.x = x;
    this.y = y;
    this.radius = opts.radius ?? 17;

    this.maxHp = opts.hp ?? 40;
    this.hp = this.maxHp;
    this.armor = 0;
    this.armorShred = 0;
    this.stun = 0;

    this.color = opts.color ?? '#ffc857';
    this.accent = opts.accent ?? '#ff9f68';
    this.tier = opts.tier ?? 1;          // 1 普通宝箱 / 2 华丽宝箱
    this.rare = !!opts.rare;

    // 奖励在生成时就决定好（这样光效颜色能提前暗示品质）
    this.reward = opts.reward ?? { kind: 'heal', value: 30 };
    this.opened = false;

    // 视觉
    this.glowPhase = Math.random() * TAU;
    this.hurtFlash = 0;
    this.spawnAnimation = 0.3;
    this.scale = 0.2;
    this.bobPhase = Math.random() * TAU;
  }

  get hpPercent() { return Math.max(0, this.hp / this.maxHp); }
  get xpValue() { return 0; }
  get materialValue() { return 0; }

  /** 宝箱也会被打，走和敌人一样的接口 */
  takeDamage(amount, opts = {}) {
    let dmg = amount;
    if (this.armor > 0 && !opts.pierceArmor) {
      dmg = Math.max(1, dmg * (1 / (1 + this.armor / 20)));
    }
    this.hp -= dmg;
    this.hurtFlash = 0.12;
    if (this.hp <= 0) {
      this.hp = 0;
      this.dead = true;
      return { killed: true, dealt: dmg };
    }
    return { killed: false, dealt: dmg };
  }

  applyKnockback() { /* 宝箱固定不动，不吃击退 */ }

  /** 宝箱不移动、不攻击，只做视觉计时 */
  update(dt) {
    this.age += dt;
    this.glowPhase += dt * 2.2;
    this.bobPhase += dt * 1.6;
    if (this.hurtFlash > 0) this.hurtFlash -= dt;
    if (this.spawnAnimation > 0) {
      this.spawnAnimation -= dt;
      this.scale = Math.min(1, 0.2 + (1 - this.spawnAnimation / 0.3) * 0.8);
    } else {
      this.scale = 1;
    }
  }
}

/**
 * 生成宝箱的奖励。
 *
 * 用户要求："宝箱则是有概率给道具有概率是医疗包"，
 * 且"掉落品质和幸运挂钩"（这条主要针对宝箱怪，但宝箱也一起受益）。
 *
 * @param {object} rng
 * @param {number} luck         玩家幸运值（面板值）
 * @param {boolean} forceRare   华丽宝箱（提高道具概率与品质）
 * @param {number} luckEffectMul 角色幸运总效果倍率（第十六轮）
 *        悠怡萌妹是 1.2，其余角色是 1（或 undefined，等价于 1）。
 *        宝箱走的是"面板值 + 倍率"而不是直接收 luckEff，
 *        是因为它的调用方（world）手上只有面板值，
 *        而且这个函数被好几个测试直接以数字调用 —— 加一个可选参数
 *        不会破坏那些调用点。
 */
export function rollChestReward(rng, luck = 0, forceRare = false, luckEffectMul = 1) {
  // 面板幸运 → 实际生效幸运（第十轮削弱到 80%，第十六轮再乘角色倍率）
  const eff = effectiveLuckFor(luck, luckEffectMul);
  const luckBonus = Math.min(1, Math.max(0, eff / 220));

  // 道具 vs 医疗包：基础 55% 道具，幸运和稀有度都会提高道具概率
  let itemChance = 0.55 + luckBonus * 0.25 + (forceRare ? 0.25 : 0);
  itemChance = Math.min(0.92, itemChance);

  if (rng.next() < itemChance) {
    return { kind: 'item', luck, rare: forceRare || rng.next() < luckBonus * 0.5 };
  }
  // 医疗包：按最大生命比例给，越到后期越值
  const pct = forceRare ? 0.5 : 0.35;
  return { kind: 'heal', pct };
}

/** 宝箱的外观按奖励类型区分颜色（玩家远远就能看出值不值得打） */
export function chestColors(reward) {
  if (reward.kind === 'heal') return { color: '#6ee7a8', accent: '#3ddc97' };
  if (reward.rare) return { color: '#ff9f68', accent: '#ff5f6d' };
  return { color: '#ffc857', accent: '#ff9f68' };
}