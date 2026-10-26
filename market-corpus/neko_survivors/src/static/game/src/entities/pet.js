// ============================================================
// 跟随宠物：小小猫娘
//
// 需求（用户原话）：
//   "增加一个小小猫娘和玩家一起玩，就是一个跟随宠物，把猫娘的对话框挪到宠物头上。
//    宠物的图我也之后给你。宠物默认无敌，伤害和白色武器差不多，
//    看情况攻击敌人或者帮玩家捡取金币和经验，不会离玩家太远。
//    在玩家血量低于 50% 的时候给玩家一次 20% 的回血，冷却 15 秒。
//    然后小小猫娘可以吃到玩家的 buff 加成，玩家伤害越高，小猫娘就越厉害。"
//
// 设计要点：
//  - **无敌**：不参与任何伤害结算，敌人也完全不理会它（不吸引仇恨）。
//  - **伤害对标白色（T1）武器**：用 T1 武器的平均伤害作为基准，
//    再乘上玩家当前的伤害/攻速加成 —— 这就是"吃到玩家的 buff 加成"。
//  - **行为切换**：附近有敌人就打，没有敌人就去捡最近的掉落物。
//    血量低于 50% 时优先回去回血（那是救命动作）。
//  - **不离开玩家太远**：超过 leash 距离就无条件往回走。
//  - **回血**：玩家 HP < 50% 时触发，回 20% 最大生命，冷却 15 秒。
//
// 造型是占位几何图形（环形小猫 + 耳朵 + 尾巴），
// 之后换成贴图只需要改 Renderer._drawPet。
// ============================================================

import { Entity } from './entity.js';
import { TAU, clamp, dist } from '../core/math.js';
import { RNG } from '../core/rng.js';
import { WEAPONS } from '../data/weapons.js';
import {
  PET_HEAL_LINES, PET_PICKUP_LINES, PET_FREE_ITEM_LINES, makeLinePicker,
} from '../data/pet-lines.js';

// ---- 可调参数 ----
const FOLLOW_DISTANCE = 58;     // 跟随时想保持的距离
const LEASH = 260;              // 超过这个距离就必须回来（"不会离玩家太远"）
const ATTACK_RANGE = 190;       // 索敌范围
const ATTACK_COOLDOWN = 0.85;   // 出手间隔（基准，会受玩家攻速影响）
const PICKUP_RANGE = 240;       // 帮忙捡东西的范围
const PET_RADIUS = 11;
const BASE_SPEED = 215;

// 回血
export const PET_HEAL_THRESHOLD = 0.5;   // 玩家血量低于这个比例才触发
export const PET_HEAL_RATIO = 0.2;       // 回 20% 最大生命
export const PET_HEAL_COOLDOWN = 15;     // 冷却 15 秒

// ---- 大范围吸取（第十轮，用户要求）----
//
// 用户原话："让小小猫娘在没攻击目标的时候偶尔触发一次大范围拾取金钱和
//            经验的效果（比如以她自身为圆形半径周围一定量的吸收）
//            冷却也设置到15秒。"
//
// 设计要点：
//  - **只在没攻击目标时**触发。用户明确说了这个前提 ——
//    打架的时候她该去打架，不该突然跑去扫地。
//    所以触发条件挂在"这一帧 _findEnemy 没找到敌人"上。
//  - 以**她自己**为圆心（用户原话"以她自身为圆形半径"），不是玩家。
//    她本来就会跟着玩家跑，所以实际覆盖范围会随她的站位移动。
//  - 15 秒冷却（和回血冷却同一个数字，但是独立计时）。
//  - 一次性把范围内的经验/材料**全部收掉**（走 _collect，所以
//    经验/材料正常结算，和平时捡东西走同一条路径）。
export const PET_VACUUM_RADIUS = 420;    // 吸取半径（比平时的 240 大一截）
export const PET_VACUUM_COOLDOWN = 15;   // 冷却 15 秒

// 第十四轮 ⑤：有敌人时触发大范围吸取所需的**最少**掉落物件数。
// 旧版要求"完全没有攻击目标"才吸，实测等于永不触发（详见 update() 里的分析）。
// 战斗中改成"攒够 6 件才吸"，既保留了"这是偶尔来一发的清场技能"的定位，
// 又让它真的会在实战里出现。
export const PET_VACUUM_MIN_PICKUPS = 6;

/**
 * 白色（T1）武器的平均 DPS 基准。
 * 从武器表实时算出来，而不是写死一个数字 ——
 * 以后调整 T1 武器数值时宠物会自动跟着变。
 */
export function whiteWeaponBaseline() {
  const t1 = WEAPONS.filter((w) => w.tier === 1 && w.kind !== 'turret');
  if (t1.length === 0) return { damage: 6, cooldown: 0.8 };
  let dmg = 0, cd = 0;
  for (const w of t1) { dmg += (w.damage ?? 5); cd += (w.cooldown ?? 0.8); }
  return { damage: dmg / t1.length, cooldown: cd / t1.length };
}

/** 宠物的种类（用户之后给图，这里先留好扩展位） */
export const PET_TYPES = {
  kitten: {
    id: 'kitten',
    name: '小小猫娘',
    color: '#ffe0ec',
    accent: '#ff7bac',
    radius: PET_RADIUS,
  },
};

export class Pet extends Entity {
  constructor(typeId, player) {
    super();
    this.isPet = true;
    this.def = PET_TYPES[typeId] || PET_TYPES.kitten;
    this.radius = this.def.radius;
    this.invulnerable = true;      // 默认无敌（用户要求）

    // 出生在玩家旁边
    this.x = player.x + 40;
    this.y = player.y + 20;
    this.vx = 0;
    this.vy = 0;

    // 行为
    this.state = 'follow';         // follow | attack | fetch | heal
    this.facing = 0;
    this.attackCd = 0;
    this.healCd = 0;               // 回血冷却剩余
    this.healsGiven = 0;
    this.pickedUp = 0;             // 帮忙捡了多少件
    this.kills = 0;
    this.damageDealt = 0;
    // 大范围吸取（见 PET_VACUUM_* 的说明）。
    // 初始给一个较短的"首触"冷却，免得开局第一帧就吸一次 ——
    // 那时场上还没有掉落物，吸了也没观感。
    this.vacuumCd = 6;
    this.vacuumsUsed = 0;
    this.vacuumAnim = 0;           // 吸取特效的动画计时

    // 动画
    this.bobPhase = 0;
    this.walkPhase = 0;
    this.attackAnim = 0;
    this.healAnim = 0;
    // 第十二轮第 7 项：偷到商店道具后头顶冒 $ 的动画计时。
    // 渲染器从**计时器**推表情（不读 emote 字段），所以要给它一个
    // 能自然衰减到 0 的计时器，而不是设一次 emote 就完事。
    this.stealAnim = 0;
    this.emote = '';               // 头顶小表情（! / ♥ / …）

    // ---- 第十五轮 ③：技能台词 ----
    // 台词用的随机源。
    //
    // 用独立的 RNG 而不是 world.rng：台词纯粹是**表现层**，
    // 如果它去消耗 world.rng，就会改变敌人刷新/掉落的随机序列 ——
    // 那会让"同一颗种子跑出同一局"这个可复现性失效（很多测试依赖它）。
    // 表现层的随机必须和玩法随机分开，这是条硬规矩。
    this.rng = new RNG(player.seed ?? 20250815);
    // 三个"洗牌袋"取词器（见 data/pet-lines.js 的 makeLinePicker）。
    // 每类 10 句，抽完一轮之前不重复。
    const rand = () => this.rng.next();
    this._healLine = makeLinePicker(PET_HEAL_LINES, rand);
    this._pickupLine = makeLinePicker(PET_PICKUP_LINES, rand);
    this._freeItemLine = makeLinePicker(PET_FREE_ITEM_LINES, rand);
    // 最近说的一句 + 剩余显示时间。
    // 渲染器据此在头顶画气泡（和 emote 一样的计时器套路）。
    this.line = '';
    this.lineTimer = 0;
    // 拾取台词要**节流**：捡东西一帧可能连捡 5 件，
    // 每件都说一句会把气泡刷成走马灯（而且听起来像卡带）。
    this._pickupLineCd = 0;

    // 目标缓存
    this.target = null;
    this.fetchTarget = null;

    // 基准伤害（对标白色武器）
    this._baseline = whiteWeaponBaseline();
  }

  /** 宠物不受伤（无敌）：保留接口，方便以后做"宠物也会掉血"的玩法 */
  takeDamage() { return 0; }

  /**
   * 说一句台词（第十五轮 ③）。
   *
   * 只负责**记录**，不负责显示 —— 显示由渲染层（头顶气泡）和
   * main.js（toast）各自决定。这样台词系统不依赖 DOM，
   * 单测里可以直接断言 this.line。
   *
   * @param {string} text 要说的内容
   * @param {number} [holdMs=1800] 显示时长（毫秒）
   * @param {object} [world] 有的话会顺带触发 world.onPetSay 回调
   * @param {string} [kind='line'] 台词类别，供显示层决定"要不要弹提示"。
   *   第十八轮 ③ 加的。用户反馈："小小猫娘偷东西和发动范围拾取技能的
   *   台词做好了吗？我从来没听过。"
   *
   *   查下来台词**一直都在**，问题在**显示**：world.onPetSay 这个回调
   *   全仓从来没有人挂过（只有这里在调），所以台词只能靠渲染层那行
   *   小小的头顶气泡 —— 而气泡跟着宠物在场上飘，玩家正在走位打怪，
   *   基本不可能注意到。
   *
   *   加 kind 是为了让 main.js 能区分对待：
   *     'pickup'   顺路捡东西——**太频繁，不弹提示**（只走气泡）
   *     'vacuum'   范围拾取技能发动——弹提示（这是用户点名的"技能"）
   *     'heal'     治疗技能——已经有了单独的"回了 N 血"提示
   *     'freeItem' 从商店白拿道具——已经有"帮你拿了 xx 道具"提示
   *   这样既让该被看见的台词被看见，又不会把屏幕刷满。
   */
  say(text, holdMs = 1800, world = null, kind = 'line') {
    if (!text) return;
    this.line = text;
    this.lineTimer = holdMs / 1000;
    world?.onPetSay?.(this, text, kind);
  }

  /** 治疗台词（治疗技能触发时调用） */
  sayHeal(world) { this.say(this._healLine(), 1800, world, 'heal'); }

  /**
   * 拾取台词。带节流：`_pickupLineCd > 0` 时直接跳过。
   *
   * 为什么要节流：大范围吸取一次能收十几件，逐个说就是刷屏。
   * 1.6 秒的间隔正好"一次吸取只说一句"，听起来是她在邀功，
   * 而不是在念清单。
   */
  sayPickup(world) {
    if (this._pickupLineCd > 0) return;
    this._pickupLineCd = 1.6;
    this.say(this._pickupLine(), 1600, world, 'pickup');
  }

  /**
   * 第十八轮 ③：**范围拾取技能发动**时的台词。
   *
   * 和 sayPickup 用的是同一个台词池（它本来就是拾取类的话），
   * 但走独立的 kind='vacuum'，所以 main.js 会额外弹一条提示 ——
   * 这是用户点名"从来没听过"的那一条。
   *
   * 为什么不复用 sayPickup 的节流：技能自己已经有 15 秒冷却，
   * 不可能刷屏；而 _tryVacuum 内部会先对每件掉落物调 _collect →
   * sayPickup，那个 1.6 秒节流会把第一句也吃掉。
   * 所以这里**绕过** _pickupLineCd 直接说，保证技能一定出声。
   */
  sayVacuum(world) {
    this.say(this._pickupLine(), 1800, world, 'vacuum');
  }

  /** 从商店拿到免费道具时的台词（注意：措辞是"帮你拿到"，不含"偷"） */
  sayFreeItem(world) { this.say(this._freeItemLine(), 2600, world, 'freeItem'); }

  /**
   * 从玩家属性快照里重算战斗力。
   * 这是"玩家伤害越高，小猫娘就越厉害"的落点：
   * 宠物伤害 = 白色武器基准 × (1 + 玩家伤害加成/100)
   * 宠物攻速 = 白色武器基准冷却 / (1 + 玩家攻速加成/100)
   */
  get damage() {
    const s = this.player?.stats.values;
    if (!s) return this._baseline.damage;
    return this._baseline.damage * (1 + s.damage / 100);
  }

  get attackInterval() {
    const s = this.player?.stats.values;
    const aspd = s ? s.attackSpeed : 0;
    return Math.max(0.18, this._baseline.cooldown / (1 + aspd / 100));
  }

  /** 宠物攻速倍率（给 UI/测试用） */
  get powerLevel() {
    const s = this.player?.stats.values;
    if (!s) return 1;
    return 1 + s.damage / 100;
  }

  update(dt, player, world) {
    this.age += dt;
    this.player = player;
    this.bobPhase += dt * 4;
    if (this.attackCd > 0) this.attackCd -= dt;
    if (this.healCd > 0) this.healCd -= dt;
    if (this.vacuumCd > 0) this.vacuumCd -= dt;
    if (this.attackAnim > 0) this.attackAnim -= dt;
    if (this.healAnim > 0) this.healAnim -= dt;
    if (this.vacuumAnim > 0) this.vacuumAnim -= dt;
    if (this.stealAnim > 0) this.stealAnim -= dt;
    // 第十五轮 ③：台词气泡与拾取台词的节流计时
    if (this.lineTimer > 0) this.lineTimer -= dt;
    if (this._pickupLineCd > 0) this._pickupLineCd -= dt;

    // ---- 行为分派 ----
    // 优先级：回血 > 打架 > 捡东西 > 跟随
    if (this._tryHeal(player, world)) {
      this._moveToward(player.x, player.y, dt, 1.15);
      return;
    }

    const toPlayer = dist(this.x, this.y, player.x, player.y);
    // 离得太远就无条件回来（"不会离玩家太远"）
    if (toPlayer > LEASH) {
      this.state = 'follow';
      this._moveToward(player.x, player.y, dt, 1.35);
      this._face(player.x, player.y);
      return;
    }

    // 第五十一轮：芙兰战期间小小猫娘转**护卫模式** ——
    // 用户："小小猫娘在芙兰boss战的时候太过于倾向攻击芙兰了，导致她的
    // 消弹有时候完全保护不到玩家。" 消弹光圈以她为圆心，贴着玩家才护得住；
    // 芙兰 50 万血也不缺她这点输出。全场只跟随，不去打 Boss。
    this.target = world.flandreFight ? null : this._findEnemy(world);
    this.fetchTarget = this._findPickup(world);

    // ---- 大范围吸取 ----
    // 用户原话（第十轮）是"没攻击目标的时候"触发。但按那个条件实测
    // 等于永不触发（见下面的分析）。折中：
    //   · 没目标时 —— 有 1 件就吸（保持原意：清场）
    //   · 有目标时 —— 要攒够 PET_VACUUM_MIN_PICKUPS 件才吸
    // 这样它仍然是"偶尔来一发"的技能，但玩家真的能看见，
    // 而且"攒够一堆才吸"比"随时吸"更有节奏感。
    //
    // **必须排在 _grabTouching 之前**：_grabTouching 会把身位重叠的
    // 掉落物直接收掉，如果它先跑，就会把吸取技能"等着的口粮"吃掉，
    // 导致冷却转好了却因为范围内瞬间没东西而吸不出来
    // （这是实测踩到的坑：40 秒里只触发了 1 次，而不是 3 次）。
    // 语义上也该如此 —— 主动技能优先于顺手捡。
    if (this.vacuumCd <= 0) {
      const minCount = this.target ? PET_VACUUM_MIN_PICKUPS : 1;
      this._tryVacuum(world, player, minCount);
    }

    // ---- 第十四轮 ⑤：顺手捡（用户反馈"没见过小小猫娘触发技能捡道具"）----
    //
    // 根因：旧版把"捡东西"整段放在 `if (this.target) { ...attack...; return; }`
    // **之后**。也就是说 —— **只要 190px 内有一只怪，她就永远在打架，
    // 捡东西和大范围吸取这两段代码一次都不会执行**。
    //
    // 而怪几乎总是有的：它们主动追玩家，而宠物就贴在玩家身边。
    // 所以这两条分支在实战里基本是死代码，用户当然"从来没见过"。
    //
    // 这里补一个**零成本**的顺手捡：她已经贴到的东西直接收掉，
    // 不需要移动、也不打断打架。掉落物本来就落在她脚边（怪死在附近），
    // 所以这一条就能让"帮忙捡金币和经验"在战斗中真正看得见。
    this._grabTouching(world, player);

    // ---- 决定：去捡东西还是去打人 ----
    // 规则简单且可预测：**谁近做谁**。
    // 用距离比较而不是固定优先级，是因为两者的"值得做"程度本来就随
    // 距离变化：脚边的金币顺手就捡了，远处的怪才值得专门跑过去。
    if (this.fetchTarget) {
      const dp = dist(this.x, this.y, this.fetchTarget.x, this.fetchTarget.y);
      const de = this.target ? dist(this.x, this.y, this.target.x, this.target.y) : Infinity;
      if (dp <= de) {
        this.state = 'fetch';
        const p = this.fetchTarget;
        this._face(p.x, p.y);
        this._moveToward(p.x, p.y, dt, 1.1);
        if (dist(this.x, this.y, p.x, p.y) < this.radius + p.radius + 6) {
          this._collect(p, world, player);
        }
        return;
      }
    }

    if (this.target) {
      this.state = 'attack';
      this._doAttack(dt, world);
      return;
    }

    this.state = 'follow';
    this._followPlayer(dt, player);
  }

  /**
   * 第十四轮 ⑤：把**已经碰到**的掉落物直接收掉。
   *
   * 和 _findPickup 的区别：这个不移动、不看优先级，只处理"身位已经重叠"
   * 的情况。所以它可以在打架途中安全调用，不会让宠物丢下敌人跑去追金币。
   */
  _grabTouching(world, player) {
    const list = world?.pickups?.items;
    if (!list || list.length === 0) return 0;
    let n = 0;
    // 收集后再结算：_collect 会把 pickup.dead 置位，边遍历边改容易漏。
    const touch = [];
    for (const p of list) {
      if (p.dead) continue;
      if (p.type !== 'xp' && p.type !== 'material') continue;
      const r = this.radius + p.radius + 10;
      const dx = p.x - this.x, dy = p.y - this.y;
      if (dx * dx + dy * dy <= r * r) touch.push(p);
    }
    for (const p of touch) { this._collect(p, world, player); n++; }
    return n;
  }

  // ---------------------------------------------------------
  // 回血
  // ---------------------------------------------------------
  _tryHeal(player, world) {
    if (this.healCd > 0) return false;
    if (player.dead) return false;
    const max = player.stats.get('maxHp');
    if (max <= 0) return false;
    const ratio = player.hp / max;
    if (ratio >= PET_HEAL_THRESHOLD) return false;

    // 触发一次 20% 最大生命的回复，然后进入 15 秒冷却
    const amount = max * PET_HEAL_RATIO;
    // force：芙兰战 noHeal 唯一合法豁免（第四十九轮明确保留小小猫娘半血治疗）
    const healed = player.heal(amount, { force: true });
    // ---- 第一百八十四轮（用户："1p猫娘倒地次数是最少的，2-3-4p猫娘倒地
    // 明显多……要不让小小猫娘的回血触发的时候也回复其他3个猫娘的血"）——
    // 治疗以前只喂 P1，4P 的 2~4P 局内零回复，倒地次数差距的直接来源。
    // 现在触发时全队（活着、非倒地）各回 20% 自身最大生命。
    let teamHealed = 0;
    for (const pl of (world.players?.() ?? [player])) {
      if (!pl || pl === player || pl.dead || pl.downed) continue;
      const m2 = pl.stats?.get?.('maxHp') ?? 0;
      if (m2 > 0 && pl.hp > 0) teamHealed += pl.heal(m2 * PET_HEAL_RATIO, { force: true });
    }
    if (teamHealed > 0) {
      world.fx?.damageText(player.x, player.y - 52, `全队 +${Math.round(teamHealed)}`, false, '#6ee7b7');
    }
    this.healCd = PET_HEAL_COOLDOWN;
    this.healsGiven++;
    this.healAnim = 0.7;
    this.emote = 'heart';
    this.emoteTimer = 1.2;
    // 第十五轮 ③：治疗台词
    this.sayHeal(world);

    world.fx?.sparkle(this.x, this.y, '#ff7bac');
    world.fx?.ring(this.x, this.y, 34, '#ff7bac', 0.5);
    world.fx?.damageText(player.x, player.y - 30, `+${Math.round(healed)}`, false, '#6ee7a8');
    world.onPetHeal?.(this, healed);
    return true;
  }

  // ---------------------------------------------------------
  // 索敌与攻击
  // ---------------------------------------------------------
  _findEnemy(world) {
    if (!world.grid) return null;
    // 优先打最近的；用 grid 的 nearest 避免遍历全部敌人
    return world.grid.nearest(this.x, this.y, ATTACK_RANGE, (e) => e.isEnemy && !e.dead) || null;
  }

  _doAttack(dt, world) {
    const t = this.target;
    if (!t || t.dead) { this.target = null; return; }
    this._face(t.x, t.y);

    const d = dist(this.x, this.y, t.x, t.y);
    // 贴身一点再打（远程距离也够得着，但走过去打更有"一起上"的感觉）
    if (d > this.radius + t.radius + 8) {
      this._moveToward(t.x, t.y, dt, 1.2);
    } else {
      // 到身位了就停住，避免抖
      this.vx *= 0.6; this.vy *= 0.6;
    }

    if (this.attackCd > 0) return;
    if (d > ATTACK_RANGE) return;

    this.attackCd = this.attackInterval;
    this.attackAnim = 0.22;

    const dealt = world.petDealDamage(this, t, this.damage);
    this.damageDealt += dealt;
    if (t.dead) this.kills++;
  }

  // ---------------------------------------------------------
  // 捡东西
  // ---------------------------------------------------------
  _findPickup(world) {
    let best = null, bestD = PICKUP_RANGE;
    for (const p of world.pickups.items) {
      if (p.dead) continue;
      // 只帮忙捡经验和材料（医疗包留给玩家自己踩，避免抢手感）
      if (p.type !== 'xp' && p.type !== 'material') continue;
      const d = dist(this.x, this.y, p.x, p.y);
      if (d < bestD) { bestD = d; best = p; }
    }
    return best;
  }

  _collect(pickup, world, player) {
    pickup.dead = true;
    this.pickedUp++;
    if (pickup.type === 'xp') player.gainXp(pickup.value);
    else if (pickup.type === 'material') player.gainMaterials(pickup.value);
    world.fx?.sparkle(pickup.x, pickup.y, '#ffd166');
    world.onPickup?.(pickup);
    // 第十五轮 ③：拾取台词。
    // 放在这里（而不是 _tryVacuum 里）的好处：无论走"顺手捡"还是
    // "大范围吸取"，只要是她捡的就会说 —— 两条路径共用同一句话，
    // 不会出现"吸取会说话、顺手捡不会"的割裂感。
    // 节流在 sayPickup 内部（一次吸十几件只说一句）。
    this.sayPickup(world);
  }

  /**
   * 大范围吸取：把以**她自己**为圆心、半径 PET_VACUUM_RADIUS 内的
   * 经验与材料一次性全收掉（第十轮，用户要求）。
   *
   * 为什么以宠物为圆心而不是玩家：用户原话是"以她自身为圆形半径
   * 周围一定量的吸收"。她平时跟在玩家身边，所以实际覆盖区域会
   * 随着她的走位移动 —— 这也让这个技能有一点"她跑去帮你扫一圈"
   * 的观感，比固定在玩家脚下更有存在感。
   *
   * 只收经验和材料，**不碰医疗包** —— 和 _findPickup 的取舍一致：
   * 医疗包是玩家的救命资源，不该被自动吃掉（否则玩家想留到最后
   * 再踩的时候会莫名其妙少一个）。
   *
   * 收走时逐个调 _collect（而不是批量结算），这样：
   *   - 经验/材料走的是和平常捡东西**完全相同**的代码路径
   *   - 每个掉落物都会冒一个 sparkle，玩家能看清"她吸了一堆"
   *   - world.onPickup 回调照常触发（音效/统计不会漏）
   *
   * @param {number} [minCount=1] 至少吸到几件才真正发动。
   *   第十四轮 ⑤ 加的：战斗中要攒够一定数量才值得"来一发"，
   *   否则 15 秒冷却会被一件脚边的金币白白吃掉。
   * @returns {number} 这次吸到的件数
   */
  _tryVacuum(world, player, minCount = 1) {
    // 冷却由技能自己把关，而不是只靠调用方 ——
    // 这样以后别的地方（比如某个道具"立刻刷新宠物技能"）调这个函数时，
    // 也不会绕过冷却。
    if (this.vacuumCd > 0) return 0;

    const list = world?.pickups?.items;
    if (!list || list.length === 0) return 0;

    // 先收集，再结算 —— 不能边遍历边改 dead 标记（_collect 会改）。
    const hit = [];
    const r2 = PET_VACUUM_RADIUS * PET_VACUUM_RADIUS;
    for (const p of list) {
      if (p.dead) continue;
      if (p.type !== 'xp' && p.type !== 'material') continue;
      const dx = p.x - this.x, dy = p.y - this.y;
      if (dx * dx + dy * dy <= r2) hit.push(p);
    }

    // 一件都没吸到就**不进冷却** —— 否则玩家会在"场上没东西"的时候
    // 白白浪费一次技能，冷却转好了却什么也没发生，体验很差。
    // 第十四轮 ⑤：同理，"攒得不够"也不进冷却 ——
    // 否则刚打完一波、地上只有 2 件时技能就空转了。
    if (hit.length === 0) return 0;
    if (hit.length < minCount) return 0;

    for (const p of hit) this._collect(p, world, player);

    // 第十八轮 ③：技能发动时**额外**说一句（kind='vacuum'）。
    // 放在结算循环**之后**：循环里每件掉落物都会走 _collect → sayPickup，
    // 但那句被 1.6 秒节流挡掉（第一件除外）。这里再补一句、覆盖掉，
    // 保证"范围拾取发动"这件事在台词和提示上都能被看见。
    this.sayVacuum(world);

    this.vacuumCd = PET_VACUUM_COOLDOWN;
    this.vacuumsUsed++;
    // 只维护动画计时器；头顶表情由渲染器从计时器推导
    // （见 renderer._drawPet），所以这里不用去写 this.emote。
    this.vacuumAnim = 0.5;
    // 视觉反馈：一圈扩散的环 + 从她身上散出的光点
    world.fx?.ring?.(this.x, this.y, PET_VACUUM_RADIUS * 0.5, '#ffe0ec', 0.5);
    world.fx?.nova?.(this.x, this.y, PET_VACUUM_RADIUS * 0.35, '#ffd166');
    return hit.length;
  }

  // ---------------------------------------------------------
  // 移动
  // ---------------------------------------------------------
  _followPlayer(dt, player) {
    const d = dist(this.x, this.y, player.x, player.y);
    // 离得太近就不挤（避免和玩家模型重叠抖动）
    if (d < FOLLOW_DISTANCE) {
      this.vx *= 0.82; this.vy *= 0.82;
      this.x += this.vx * dt;
      this.y += this.vy * dt;
      return;
    }
    this._moveToward(player.x, player.y, dt, 1);
  }

  _moveToward(tx, ty, dt, speedMul = 1) {
    const dx = tx - this.x, dy = ty - this.y;
    const d = Math.hypot(dx, dy) || 1;
    const s = BASE_SPEED * speedMul;
    this.vx = (dx / d) * s;
    this.vy = (dy / d) * s;
    this.x += this.vx * dt;
    this.y += this.vy * dt;
    this.walkPhase += dt * 12;
    this._face(tx, ty);
  }

  _face(tx, ty) {
    this.facing = Math.atan2(ty - this.y, tx - this.x);
  }

  /** 世界边界 */
  clampToWorld(world) {
    const half = world.halfSize - this.radius;
    this.x = clamp(this.x, -half, half);
    this.y = clamp(this.y, -half, half);
  }
}