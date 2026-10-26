// ============================================================
// 子弹：玩家子弹与敌人子弹
// 支持 直线 / 追踪 / 弹跳 三种行为
// ============================================================

import { Entity } from './entity.js';
import { angleTo, angleDiff, TAU } from '../core/math.js';
// 第一百三十二轮：波次真伤档位随弹携带（命中时按当前波结算）
import { trueDamageBonus } from '../data/difficulty.js';

export class Bullet extends Entity {
  constructor(cfg) {
    super();
    this.x = cfg.x; this.y = cfg.y;
    this.angle = cfg.angle;
    this.speed = cfg.speed;
    this.damage = cfg.damage;
    this.radius = cfg.radius ?? 6;
    this.color = cfg.color ?? '#ffffff';
    this.owner = cfg.owner ?? null;      // 武器实例
    // 第十六轮：显式记住"是谁打的"。
    //
    // 以前子弹只记武器实例（owner），伤害结算时靠 `ownerPlayer` 反查 ——
    // 而那个字段**从来没有被赋值过**，一直是 undefined。
    // 于是 `world.dealDamage(undefined, ...)` 里的 `player.stats` 必然抛错，
    // 只是没走到过那条路（world.js L613 直接读 player.stats）。
    // 第十六轮要给"命中时的距离"和"命中者的角色技能"结算，必须真的带上玩家。
    this.ownerPlayer = cfg.ownerPlayer ?? null;
    this.kind = cfg.kind ?? 'ranged';
    this.behavior = cfg.behavior ?? 'shot';
    this.pierce = cfg.pierce ?? 0;
    // 第一百一十五轮：穿甲弹 —— pierce 总数里"道具给的"部分单独记账：
    // 武器自带穿透（def.pierce）每一段都打**全额**；道具给的穿透每穿
    // 一个敌人 ×0.6 连乘衰减（100 → 60 → 36 → 22）。用户原话见
    // data/items.js 的 ap_round。衰减指数 itemUsed 只在消耗**道具**
    // 穿透时 +1，自带穿透不推指数 —— 否则买一件穿甲弹就把激光笔
    // （自带贯穿 99）砍成废铁。
    this.itemPierce = cfg.itemPierce ?? 0;
    this.innatePierce = this.pierce - this.itemPierce;   // 自带部分
    this.itemUsed = 0;                                   // 衰减指数（×0.6ⁿ）
    this.homing = cfg.homing ?? 0;
    this.bounces = cfg.bounces ?? 0;
    this.bounced = cfg.bounced ?? 0;
    this.explosionRadius = cfg.explosionRadius ?? 0;
    this.isEnemyBullet = false;

    this.maxRange = cfg.range ?? 400;
    this.traveled = 0;
    this.hitIds = new Set();
    this.trail = [];
    this.trailTimer = 0;
  }

  update(dt, world) {
    this.age += dt;

    // ---- 追踪 ----
    if (this.behavior === 'homing' && this.homing > 0) {
      const target = world.grid.nearest(this.x, this.y, 260, (e) => e.isEnemy && !e.dead && !this.hitIds.has(e.id));
      if (target) {
        const desired = angleTo(this.x, this.y, target.x, target.y);
        const diff = angleDiff(this.angle, desired);
        const maxTurn = this.homing * dt;
        this.angle += Math.max(-maxTurn, Math.min(maxTurn, diff));
      }
    }

    const dx = Math.cos(this.angle) * this.speed * dt;
    const dy = Math.sin(this.angle) * this.speed * dt;
    this.x += dx;
    this.y += dy;
    this.traveled += Math.hypot(dx, dy);

    // ---- 拖尾 ----
    this.trailTimer -= dt;
    if (this.trailTimer <= 0) {
      this.trailTimer = 0.03;
      this.trail.push({ x: this.x, y: this.y, a: 1 });
      if (this.trail.length > 6) this.trail.shift();
    }
    for (const t of this.trail) t.a -= dt * 3.5;

    // ---- 超出射程 ----
    if (this.traveled > this.maxRange) {
      if (this.explosionRadius > 0) this._explode(world);
      this.dead = true;
      return;
    }

    // ---- 超出世界边界 ----
    const half = world.halfSize + 60;
    if (Math.abs(this.x) > half || Math.abs(this.y) > half) {
      this.dead = true;
    }
  }

  _explode(world) {
    const hits = world.grid.query(this.x, this.y, this.explosionRadius, []);
    for (const e of hits) {
      if (!e.isEnemy || e.dead) continue;
      world.dealDamage(this.ownerPlayer, this.owner, e, this.damage * 0.7, {
        knockback: 60, fromX: this.x, fromY: this.y,
      });
    }
    world.fx.explosion(this.x, this.y, this.explosionRadius, this.color,
        this.isEnemyBullet ? null : { player: true });
  }

  /** 与敌人碰撞检测（由 World 调用） */
  checkEnemyHit(world) {
    // ---- 第一百七十一轮（2P）：救援 ----
    // 玩家弹命中**倒地的队友** = 伤害转为救援计数（打醒队友）。
    // 判定圈放大 40px：武器都是自动朝怪打的，弹道不一定正好穿过队友
    // 身体 —— "跑到队友身边开火就能救"（v0.3.48 救援无效的根因之一）。
    // 非 duo 模式 world.players() 只有 P1 自己，循环零命中零开销。
    for (const ally of (world.players?.() ?? [])) {
      if (!ally || ally === this.ownerPlayer || !ally.downed) continue;
      const adx = ally.x - this.x, ady = ally.y - this.y;
      const arr = this.radius + ally.radius + 40;
      if (adx * adx + ady * ady <= arr * arr) {
        this.dead = true;
        const revived = ally.rescueHit();
        if (revived) {
          world.fx?.nova?.(ally.x, ally.y, 26, '#7cf3c4');
          world.fx?.damageText?.(ally.x, ally.y - ally.radius - 10, '救援成功！', false, '#7cf3c4');
        } else if (!ally._rescueGate || ally._rescueGate >= 0.19) {
          // gate 刚置位（≈0.2）= 这一发真的计了数；残留 gate = 0.2s 内的
          // 重复命中，不算数也不刷字（第一百七十五轮：进度飘字）
          // 第一百七十八轮：进度取整显示（回涨是连续小数，直接减会出
          // "救援 +1（1.3700000000000003/5）"这种一长串）
          world.fx?.sparkle?.(ally.x, ally.y, '#7cf3c4');
          world.fx?.damageText?.(ally.x, ally.y - ally.radius - 10,
            `救援 +1（${Math.round(ally.rescueNeed - ally.rescueProgress)}/${ally.rescueNeed}）`, false, '#7cf3c4');
        }
        return;
      }
    }
    const hits = world.grid.query(this.x, this.y, this.radius, []);
    for (const e of hits) {
      if (!e.isEnemy || e.dead) continue;
      if (this.hitIds.has(e.id)) continue;
      this.hitIds.add(e.id);

      // 第一百四十六轮：维吉尔格挡领域 —— P1/P2 游荡时自动格挡，子弹
      // 不扣血、直接斩碎（火花/斩线特效和音效在 parryAt 里）。近战不
      // 走子弹，天然不受格挡影响。
      const vf = world.vergilFight;
      if (vf && e === vf.boss && vf.guarding) {
        vf.parryAt(this.x, this.y);
        this.dead = true;
        return;
      }

      // 第一百一十五轮：穿甲弹衰减 —— 伤害 = 基础 × 0.6^（已消耗的
      // 道具穿透数）。第一段永远全额（itemUsed=0）；道具穿透每用掉
      // 一段，下一段 ×0.6。
      world.dealDamage(this.ownerPlayer, this.owner, e, this.damage * Math.pow(0.6, this.itemUsed), {
        knockback: 50, fromX: this.x, fromY: this.y,
        // 第十六轮：标出"这是炮台打的"。
        // Neon（原"neon宴会"） 的「火力即正义」只认炮台命中，而炮台子弹和玩家子弹
        // 走的是**同一条** dealDamage 路径 —— 不显式标记的话，
        // 玩家本体武器的命中也会给敌人叠层，技能就不是"炮台专精"了。
        fromTurret: this.kind === 'turret',
      });

      if (this.explosionRadius > 0) {
        this._explode(world);
        this.dead = true;
        return;
      }

      if (this.pierce > 0) {
        this.pierce--;
        // 先消耗自带的（全额段），再消耗道具的（衰减段）
        if (this.innatePierce > 0) this.innatePierce--;
        else { this.itemPierce--; this.itemUsed++; }
      } else if (this.behavior === 'bounce' && this.bounced < this.bounces) {
        // 弹跳：转向最近的未命中敌人
        this.bounced++;
        const next = world.grid.nearest(this.x, this.y, 220, (en) => en.isEnemy && !en.dead && !this.hitIds.has(en.id));
        if (next) {
          this.angle = angleTo(this.x, this.y, next.x, next.y);
          this.traveled = Math.max(0, this.traveled - 60);
          return;
        }
        this.dead = true;
        return;
      } else {
        this.dead = true;
        return;
      }
    }
  }
}

/** 敌人子弹：命中玩家 */
export class EnemyBullet extends Entity {
  constructor(cfg) {
    super();
    this.x = cfg.x; this.y = cfg.y;
    this.angle = cfg.angle;
    this.speed = cfg.speed;
    this.damage = cfg.damage;
    this.radius = cfg.radius ?? 7;
    this.color = cfg.color ?? '#ff5f6d';
    this.maxRange = cfg.range ?? 500;
    this.traveled = 0;
    this.isEnemyBullet = true;
    this.isEnemy = false;
    // 第四十九轮：芙兰朵露弹幕 —— 命中走 30% 真实伤害（无视护甲/闪避），
    // 由 player.flandreHit() 处理；其余敌人子弹走普通 takeDamage。
    this.flandre = !!cfg.flandre;
    // ---- 第一百八十四轮：万圣节双猫弹幕 —— 10% 真伤通道（halloweenHit）
    this.halloween = !!cfg.halloween;
    // 视觉专用：hwKind='bead' 灯珠字母弹 / hwPhase 呼吸相位（渲染层 halloween-render.js 读取）
    this.hwKind = cfg.hwKind ?? '';
    this.remilia = !!cfg.remilia;   // 203 轮：蕾米终演出弹幕（走 remiliaHit 真伤通道）
    this.hwPhase = cfg.hwPhase ?? 0;
    // 第一百三十二轮：波次真伤档位（1 小怪 / 2 精英 / 3 Boss；0 = 不附加，
    // 芙兰弹与未标注来源的弹）—— 命中时按当时的 world.currentWave 结算。
    this.trueTier = cfg.trueTier ?? 0;
    this.rotation = 0;
    // 第八十八轮：弹幕形状 —— 'orb' 圆球（默认，历史行为）/
    // 'longball' 长球（两端圆润的椭圆，长:短 ≈ 2.2:1，沿飞行方向对齐，
    // 原版 495 波纹等大部分符卡用）/ 'crystal' 晶体（两端尖的叶片菱形，
    // 中间最宽，同样沿飞行方向，原版心脏等符卡用；第八十九轮用户命名：
    // longball = 椭圆蛋，crystal = 结晶蛋）/ 'jade' 大玉（第八十九轮：
    // 视觉大圈 + 小判定核，见 hitRadius）。
    // 只影响渲染：碰撞判定见 hitRadius。
    this.shape = cfg.shape ?? 'orb';
    // 第八十九轮：独立判定半径 —— 默认 = radius（历史行为）；
    // 大玉单独设小值：视觉直径 55-65px（radius≈30），白色外圈完全
    // 没有判定，只有中心 12-14px 的核才是命中区（用户："整个白色
    // 部分都没有命中判定"）。渲染画 radius，碰撞查 hitRadius。
    this.hitRadius = cfg.hitRadius ?? this.radius;
    // 第九十轮：大玉配色基色（渲染层 _jadePalette 按它取盘；
    // 不传 = 默认黄盘 '#d99a1e'）
    this.jadeColor = cfg.jadeColor;
    // 第九十一轮：来源标记（符卡脚本用来认领自己发的弹，如折反射
    // 的大玉尾随弹幕只跟自己的玉）
    this.tag = cfg.tag ?? '';
    // 第十五轮 ④：水柱（激光）标记。
    // 渲染层据此把它画成**细长的光束**而不是圆形弹丸。
    // 用户要的是"类似激光水柱，很快" —— 一个圆球以 1400px/s 飞过去
    // 在视觉上仍然只是"一颗很快的子弹"，必须有长度才有激光的观感。
    this.laser = !!cfg.laser;
    // 光束长度（仅 laser 用）；表示"这一束有多长"
    this.laserLength = cfg.laserLength ?? 120;
    // 第五十七轮：墙壁反弹（495 年的波纹）——
    // cfg.wall = { minX, maxX, minY }（竞技场上/左/右边界；底边故意不给）
    this.wall = cfg.wall ?? null;
    this.wallCount = { top: 0, left: 0, right: 0 };
    // 第七十五轮：减速弹（Fragile Wing 的 bullet_effects 减速淡出）——
    // cfg.decel = px/s²，speed 随时间线性衰减，衰减到 floorSpeed 为止
    this.decel = cfg.decel ?? 0;
    this.floorSpeed = cfg.floorSpeed ?? 0;
  }

  update(dt, world, players) {
    this.age += dt;
    this.rotation += dt * 6;
    if (this.decel > 0 && this.speed > this.floorSpeed) {
      this.speed = Math.max(this.floorSpeed, this.speed - this.decel * dt);
    }

    const dx = Math.cos(this.angle) * this.speed * dt;
    const dy = Math.sin(this.angle) * this.speed * dt;
    this.x += dx;
    this.y += dy;
    this.traveled += Math.hypot(dx, dy);

    // 第五十七轮：撞墙反弹 —— 头顶/左墙/右墙各可弹一次（合计最多 3 次，
    // 也可以一次不弹直落到底）。底墙不反弹：弹过去就过去了。
    // 反射几何：左右墙镜像 x 分量（angle → π - angle），头顶镜像 y 分量
    //（angle → -angle）。
    if (this.wall) {
      const w = this.wall;
      if (this.x <= w.minX && this.wallCount.left < 1) {
        this.x = w.minX;
        this.angle = Math.PI - this.angle;
        this.wallCount.left += 1;
      } else if (this.x >= w.maxX && this.wallCount.right < 1) {
        this.x = w.maxX;
        this.angle = Math.PI - this.angle;
        this.wallCount.right += 1;
      }
      if (this.y <= w.minY && this.wallCount.top < 1) {
        this.y = w.minY;
        this.angle = -this.angle;
        this.wallCount.top += 1;
      }
    }

    if (this.traveled > this.maxRange) { this.dead = true; return; }

    const half = world.halfSize + 80;
    if (Math.abs(this.x) > half || Math.abs(this.y) > half) { this.dead = true; return; }

    // 命中玩家 —— 第一百七十轮（2P）：遍历所有活玩家（倒地不可被弹选
    // 中），命中谁打谁。单人数组只有 P1，行为与旧版完全一致。
    // 第五十一轮：弹幕判定用玩家的 hitR（= radius × 80%，全模式通用），
    // 旧存档/测试桩没有 hitR 时回落 radius，行为不变。
    // 第八十九轮：敌弹侧改用独立 hitRadius（默认 = radius，行为不变；
    // 大玉的白色外圈不参与判定，只有中心小核能命中）。
    const list = Array.isArray(players) ? players : [players];
    for (const player of list) {
      if (!player || player.dead || player.downed) continue;
      const rr = this.hitRadius + (player.hitR ?? player.radius);
      const ddx = player.x - this.x, ddy = player.y - this.y;
      if (ddx * ddx + ddy * ddy > rr * rr) continue;
      // 第一百零八轮：浓雾里敌人的弹也会 miss（天气独立随机流）；
      // 雾雨"所有人造成的伤害+25%"乘在敌弹伤害上。
      const wxW = world?.weather;
      if (wxW && wxW.miss > 0 && wxW.rng.next() * 100 < wxW.miss) {
        this.dead = true;
        return;
      }
      // 第四十九轮：芙兰的弹 = 30% 真实伤害（无视护甲/闪避，保留无敌帧）
      // 第一百三十二轮：普通敌弹附加波次真伤 —— 档位在发射时记在
      // trueTier 上（小怪 1 / 精英 2 / Boss 3），命中时按**当时**波次算，
      // 跨波飞行的弹也吃最新成长。芙兰弹走 flandreHit，天然不叠加。
      const trueBonus = this.trueTier
        ? trueDamageBonus(world?.currentWave ?? 0, this.trueTier)
        : 0;
      // 第九十六轮：敌弹命中 → 弹幕档无敌帧 0.6s（其余伤害仍是 0.8s）
      // 第一百三十二轮：附加 trueBonus（round96 的 projectile 标记保留）
      if (this.flandre) player.flandreHit?.(this.x, this.y);
      // ---- 第一百八十四轮：万圣节双猫弹幕 —— 每发 10% 真伤（用户：
      // "做成每发弹幕10%真伤不给回血和护甲看看"），走 halloweenHit 通道。
      else if (this.halloween) player.halloweenHit?.(this.x, this.y);
      // ---- 第二百零三轮：蕾米莉亚终演出弹幕（与万圣节同口径的 10% 真伤）----
      else if (this.remilia) player.remiliaHit?.(this.x, this.y);
      else player.takeDamage(this.damage * (wxW?.enemyOut ?? 1), this.x, this.y, { projectile: true, trueBonus });
      this.dead = true;
      return;
    }
  }
}