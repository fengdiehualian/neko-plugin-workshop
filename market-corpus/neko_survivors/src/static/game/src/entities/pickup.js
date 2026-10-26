// ============================================================
// 掉落物：经验宝石 / 材料 / 生命
// 材料会被玩家的拾取范围自动吸引。
// ============================================================

import { Entity } from './entity.js';
import { TAU, clamp } from '../core/math.js';

export const PICKUP_TYPE = {
  XP: 'xp',
  MATERIAL: 'material',
  HEAL: 'heal',
};

export class Pickup extends Entity {
  constructor(type, x, y, value = 1) {
    super();
    this.type = type;
    this.x = x; this.y = y;
    this.value = value;
    this.radius = type === PICKUP_TYPE.XP ? 6 : 7;
    this.vx = 0; this.vy = 0;

    // 出生时的小弹跳
    const a = Math.random() * TAU;
    const s = 40 + Math.random() * 50;
    this.vx = Math.cos(a) * s;
    this.vy = Math.sin(a) * s;

    this.attracted = false;
    this.bobPhase = Math.random() * TAU;
    this.life = 0;
    // 第十九轮：金币双造型（coin_1/coin_2），每枚掉落随机定一张并保持不变 ——
    // 满地金币时两种造型自然混排，比清一色更像"散落的钱"。
    this.variant = Math.random() < 0.5 ? 1 : 2;
  }

  update(dt, world, player) {
    this.age += dt;
    this.life += dt;
    this.bobPhase += dt * 3;

    // 体检修复（第九十二轮）：经验/材料拾取物 90 秒后自动消散。
    // 此前唯一回收路径是"被捡走/Boss 击杀全场回收/重开"，无尽模式
    // 没捡走的会永久跨波累积（拾取半径只有 110px），几十波后上万
    // 实体把 update/渲染拖成 O(N) 卡帧。医疗包有自己的 30 上限，
    // 不在此列（回收逻辑保持原样）。90 秒 ≈ 一个大波的长度，
    // 单波内正常玩法不受影响。
    if ((this.type === PICKUP_TYPE.XP || this.type === PICKUP_TYPE.MATERIAL)
      && this.life > 90) {
      // 第九十三轮：过期不再白扣 —— 记入玩家的"暂存补偿"额度，
      // 之后捡到的对应拾取物双倍收益（见 _collect）。打得很艰难时
      // 掉落物过期等于自动存起来，不再恶性循环。
      if (this.type === PICKUP_TYPE.XP) player.xpDebt = (player.xpDebt ?? 0) + 1;
      else player.materialDebt = (player.materialDebt ?? 0) + 1;
      this.dead = true;
      return;
    }

    const dx = player.x - this.x;
    const dy = player.y - this.y;
    const d = Math.hypot(dx, dy) || 1;

    // 第一百七十轮：猫娘代打轻量光环 —— `_pilotMagnet` 是代打大脑给
    // 她的"大范围磁吸"（无锁伤无回血，靠吃满地图掉落发育）。取 max
    // 是不削弱玩家自己的拾取范围道具。
    const pickupRange = Math.max(player.stats.get('pickupRange'), player._pilotMagnet ?? 0);

    // ---- 第十八轮 ⑩：满血时不主动吸医疗包 ----
    //
    // 用户原话："加个判定，玩家生命值大于等于 100% 时拾取范围不主动拿医疗包。"
    //
    // 为什么要有这条：拾取范围是**自动**生效的（进圈就被拉过来），
    // 所以满血玩家只要路过，就会把地图上的医疗包白白吃掉。
    // 而医疗包是救命资源，玩家多半想留着 —— 这条让"拾取范围"
    // 从"帮你捡东西"变回纯粹的收益，而不是偷偷替他花掉救命钱。
    //
    // 注意只关掉**吸取**（range 那一半）。玩家自己踩上去（d < rr）
    // 仍然照常捡 —— 那是他的主动动作，不该被系统拦。
    // 用 >= maxHp 而不是 > hp：满血就是"一点都用不上"。
    const fullHp = player.hp >= player.maxHp;
    const noAutoHeal = this.type === PICKUP_TYPE.HEAL && fullHp;

    // 第一百零八轮：快晴 —— 自动吸收地图上所有掉落物（医疗包满血不
    // 强喂的旧规则保留：满血时医疗包留在原地，等需要时再吸）。
    const vacuum = !noAutoHeal && world.weather?.vacuum === true;
    if (!noAutoHeal && (vacuum || this.attracted || d < pickupRange)) {
      this.attracted = true;
      // 第十轮（用户要求）："要不就是道具吸得太慢了，加快一些。"
      //
      // 改之前是"加速度"模型：pull = 900/d 再乘 12。
      // 问题是 pull 与距离**成反比** —— 掉落物刚进范围（d 最大）时
      // 加速度最小，而玩家正以 200px/s 往外走，
      // 于是掉落物永远追不上，看起来就像"根本没在吸"。
      //
      // 现在改成**目标速度**模型：直接算一个朝向玩家的速度，
      // 再用一个较快的系数把当前速度转过去（保留一点惯性，
      // 不至于看起来像瞬移）。
      // 速度随距离递减，但下限 380 —— 始终高于玩家移速（第十九轮起 215），
      // 所以只要被吸住就一定能追上。
      const targetSpeed = clamp(1300 - d, 380, 1300);
      const tvx = (dx / d) * targetSpeed;
      const tvy = (dy / d) * targetSpeed;
      const k = Math.min(1, dt * 14);
      this.vx += (tvx - this.vx) * k;
      this.vy += (tvy - this.vy) * k;
    } else {
      // 摩擦停止
      const decay = Math.exp(-6 * dt);
      this.vx *= decay;
      this.vy *= decay;
    }

    this.x += this.vx * dt;
    this.y += this.vy * dt;

    // 拾取判定
    const rr = this.radius + player.radius + 4;
    if (d < rr) {
      this._collect(world, player);
    }

    // 世界边界
    const half = world.halfSize - 4;
    if (this.x < -half) { this.x = -half; this.vx = Math.abs(this.vx) * 0.5; }
    if (this.x > half) { this.x = half; this.vx = -Math.abs(this.vx) * 0.5; }
    if (this.y < -half) { this.y = -half; this.vy = Math.abs(this.vy) * 0.5; }
    if (this.y > half) { this.y = half; this.vy = -Math.abs(this.vy) * 0.5; }
  }

  _collect(world, player) {
    this.dead = true;
    // 第九十三轮：有暂存额度时双倍收益，每次消耗 1 点（用户口径：
    // "接下来捡的每一枚都是双倍，直到捡够……从之后开始是正常单倍"）。
    // 双倍 = 同一笔收益在 switch 里发两次（走 gainXp/gainMaterials 入口）
    // —— 幸运/角色经验加成对两份一视同仁，不会"只有基础份吃加成"。
    const doubled = this.type === PICKUP_TYPE.XP
      ? (player.xpDebt ?? 0) > 0
      : this.type === PICKUP_TYPE.MATERIAL && (player.materialDebt ?? 0) > 0;
    if (doubled) {
      if (this.type === PICKUP_TYPE.XP) player.xpDebt--;
      else player.materialDebt--;
    }
    switch (this.type) {
      case PICKUP_TYPE.XP:
        // ---- 第一百七十轮（2P）：经验共享 —— 谁吸到，全队都加
        //（各自升各自的级、各自选卡）；双倍额度只结算吸取者的
        player.gainXp(this.value);
        if (doubled) player.gainXp(this.value);
        for (const other of (world.players?.() ?? [player])) {
          if (other && other !== player && !other.dead) other.gainXp(this.value);
        }
        world.fx.sparkle(this.x, this.y, doubled ? '#a7f3d0' : '#6ee7a8');
        break;
      case PICKUP_TYPE.MATERIAL:
        // 金币独立：谁吃到归谁（world 分发时已把最近的玩家传进来）
        player.gainMaterials(this.value);
        if (doubled) player.gainMaterials(this.value);
        world.fx.sparkle(this.x, this.y, doubled ? '#ffe08a' : '#ffc857');
        break;
      case PICKUP_TYPE.HEAL:
        // ---- 第一百八十四轮（用户："地图上的血包（+4血那个）和回血宝箱
        // 会共享吗？不共享的话设置共享"）—— 此前只回拾取者，4P 里 3 个
        // 队友永远捡不到治疗。现在与经验共享同口径：谁吸到，全队各回
        // value（活着且非倒地）。
        player.heal(this.value);
        for (const other of (world.players?.() ?? [player])) {
          if (other && other !== player && !other.dead && !other.downed) other.heal(this.value);
        }
        world.fx.sparkle(this.x, this.y, '#ff5f6d');
        break;
    }
    world.onPickup?.(this);
  }
}