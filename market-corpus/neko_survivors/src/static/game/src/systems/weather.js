// ============================================================
// 天气系统（第一百零八轮）
//
// 用户原话："进游戏之后正常回合每3秒一次判定20%进天气，然后天气持续
// 15-30秒，天气结束后对应时长内不会再次进入天气。boss回合和芙兰战天
// 气系统无效。"
//
// 架构铁律（第一百零八轮起长期有效）：
//   1. 天气的所有随机判定走 **独立 weatherRng 流**（main 的 rng.fork
//      派生）—— 主随机流一个字不动，几百个确定性测试的钉全部保平安。
//      视觉粒子（雨滴等）走 Math.random，与两条流都无关。
//   2. 天气只由 main._updatePlaying / _updateFlandre 驱动 update()，
//      测试直接 step world.update 的话天气完全不运转。
//   3. 增减益一律走"乘区/旗标"（miss/playerOut/moveMul/...），
//      不改任何单位的 stats 字段 —— 天气结束零残留。
// ============================================================

import { RNG } from '../core/rng.js';
// 第一百六十二轮：移动端天气视觉密度档位（桌面 MOBILE=false，数量全部不变）
import { MOBILE } from '../core/mobile.js';
import { WEATHER_DEFS, WEATHER_MAP } from '../data/weather.js';
import { warm as warmWeatherFx } from './weather-fx.js';   // 185 轮：特效精灵预热

const ROLL_INTERVAL = 3;     // 每 3 秒一次判定
const ROLL_CHANCE = 0.2;     // 20% 进天气
const DUR_MIN = 15;          // 持续 15-30 秒
const DUR_MAX = 30;
const ANNOUNCE_T = 10;       // 公告横幅挂 10 秒左右
// 第一百六十轮：结束淡出时长 —— 用户："给天气消失效果加个淡出效果，比如
// 2-3秒缓慢消失而不是一帧消失，尤其浓雾天一帧雾就没了很诡异。"
const WEATHER_FADE_T = 2.5;  // 自然结束淡出（forceEnd 走 1.2s 快速散）
const WEATHER_RNG_SALT = 0x57ea7ca1;

export class WeatherSystem {
  constructor(rng) {
    try { warmWeatherFx(); } catch { /* Node 测试环境无 document */ }
    this.rng = rng.fork(WEATHER_RNG_SALT);   // 独立流（铁律 1）
    this.phase = 'idle';       // idle → active → cooldown → idle
    this.id = null;
    this.t = 0;                // 当前天气已持续
    this.dur = 0;
    this.cd = 0;               // 冷却剩余（= 刚结束的天气时长）
    this.rollAcc = 0;          // 判定计时
    this.announceT = 0;        // 横幅剩余
    this.visT = 0;             // 视觉已持续（ramp 用）
    this.justStarted = false;  // 第一百一十四轮：开播旗标（main 消费 → 猫娘口播）

    // ---- 每帧刷新的效果旗标（hook 们只读这些）----
    this.miss = 0;             // 浓雾：双方额外 miss %
    this.playerOut = 1;        // 雾雨：玩家输出最终乘算
    this.enemyOut = 1;         // 雾雨：敌人输出最终乘算
    this.moveMul = 1;          // 晴岚：双方移速乘算
    this.enemyFireMul = 1;     // 血月：敌人攻速（fireRate 乘算）
    this.cooldownDiv = 1;      // 血月：玩家攻速（武器冷却除数）
    this.invulnMin = 0;        // 台风：玩家无敌帧下限（秒）
    this.noKnockback = false;  // 台风：所有人免疫击退
    this.vacuum = false;       // 快晴：吸收所有掉落物
    this.fogRamp = 0;          // 浓雾：视野雾 0→1（3 秒）

    // ---- 天气自己的实体 ----
    this.meteors = [];         // {x,y,r,warnT,side}
    this.metAcc = 0;
    this.burnZones = [];       // 第一百一十二轮：砸地后的持续燃烧区（纯视觉）
    this.flash = 0;            // 砸地白闪剩余（秒，屏幕层画）
    this.typhoons = [];        // {x,y,r,vx,vy,spin}
    this.fogClouds = [];       // 川雾：{x,y,r,vx,vy,turnT}
    this.burns = [];           // 流星余烬：{e,t,acc}
    this.dotAcc = 0;           // 烈日/无风 每秒跳
    this.drops = null;         // 雾雨雨滴（屏幕坐标，视觉）
    // ---- 第一百六十轮：结束淡出 ----
    // 用户："给天气消失效果加个淡出效果，比如2-3秒缓慢消失而不是一帧
    // 消失，尤其浓雾天一帧雾就没了很诡异。"
    this.fadeT = 0;            // fading 剩余（秒）
    this._fadeDur = WEATHER_FADE_T;
  }

  get def() { return WEATHER_MAP[this.id]; }
  get active() { return this.phase === 'active'; }
  /** 渲染淡出系数：active 1 → fading 线性 →0（cooldown/idle 0） */
  get visualAlpha() {
    if (this.phase === 'active') return 1;
    if (this.phase === 'fading') return Math.max(0, this.fadeT / this._fadeDur);
    return 0;
  }

  /** Boss 波 / 芙兰战 / 维吉尔战 —— 天气绝对不出现（用户铁律） */
  static blocked(world) {
    const cfg = world.waveConfig;
    return !!(world.flandreFight || world.vergilFight
      || cfg?.isBoss || cfg?.boss || (cfg?.bosses?.length ?? 0) > 0);
  }

  update(dt, world, player) {
    // 横幅倒计时任何状态都走（挂在boss开始后也要自然淡出）
    if (this.announceT > 0) this.announceT = Math.max(0, this.announceT - dt);
    // 第一百一十二轮：砸地白闪衰减（纯视觉）
    if (this.flash > 0) this.flash = Math.max(0, this.flash - dt);

    // ---- 铁律：Boss 波 / 芙兰战绝不出现天气 ----
    if (WeatherSystem.blocked(world)) {
      if (this.active) this.forceEnd();
      // 第一百八十三轮：淡出段的视觉消散**也要推进** —— 之前 blocked 直接
      // return，fadeT 永不倒计时 → 19 波的雾贴图一路卡到 20 波 boss、
      // 芙兰战都在（用户实测："雾气一直没消失……看不到周围的情况下打芙兰"）
      if (this.phase === 'fading') this._tickFade(dt, world);
      this._applyFlags();
      return;
    }

    if (this.phase === 'active') {
      this.t += dt;
      this.visT += dt;
      this._tickWeather(dt, world, player);
      if (this.t >= this.dur) this._end();
    } else if (this.phase === 'fading') {
      // ---- 第一百六十轮：淡出段（2.5s）----
      // 玩法效果已在 _end 里随 flags 归零停掉；这里只做视觉消散：
      // 雾团/龙卷按 r0 线性收缩到 0，雨滴按比例变稀。
      this._tickFade(dt, world);
    } else if (this.phase === 'cooldown') {
      this.cd -= dt;
      if (this.cd <= 0) { this.phase = 'idle'; this.rollAcc = 0; }
    } else {
      this.rollAcc += dt;
      if (this.rollAcc >= ROLL_INTERVAL) {
        this.rollAcc -= ROLL_INTERVAL;
        if (this.rng.next() < ROLL_CHANCE) this._start(world, player);
      }
    }

    this._applyFlags();
  }

  _start(world, player) {
    // 均匀挑一种（不与上一种重复）
    const pool = WEATHER_DEFS.filter((d) => d.id !== this.id);
    const def = pool[this.rng.int(0, pool.length - 1)];
    this.id = def.id;
    this.phase = 'active';
    this.t = 0;
    this.visT = 0;
    this.dur = this.rng.range(DUR_MIN, DUR_MAX);
    this.announceT = ANNOUNCE_T;
    this.justStarted = true;   // 第一百一十四轮：main 消费 → 猫娘口播 say
    this.meteors.length = 0;
    this.metAcc = 0;
    this.burnZones.length = 0;
    this.flash = 0;
    this.typhoons.length = 0;
    this.fogClouds.length = 0;
    this.burns.length = 0;
    this.dotAcc = 0;
    // 第一百六十二轮：移动端天气视觉密度减半（玩法旗标不变，纯视觉；
    // 桌面 MOBILE=false 恒 4 龙卷 / 8 雾团）
    if (def.id === 'typhoon') {
      const n = MOBILE ? 2 : 4;
      // 第一百三十五轮：龙卷风 2 → 4 个（用户："只有2个龙卷风太少啦"）
      for (let i = 0; i < n; i++) this._spawnTyphoon(world, player);
    }
    if (def.id === 'riverFog') {
      // 第一百三十五轮：雾团 4 → 8 团（用户："雾气数量多一倍"）
      for (let i = 0; i < (MOBILE ? 5 : 8); i++) this._spawnFogCloud(world, player);
    }
    if (def.id === 'rain') this._initDrops();
  }

  _end(fast = false) {
    // ---- 第一百六十轮：一帧清 → 2.5s 淡出（forceEnd 走 1.2s 快速散）----
    // id 和实体都保留（渲染继续画、visualAlpha 渐降）；玩法效果立刻停：
    // 下面的 _applyFlags 读不到 active，全部 hook 旗标归零。
    this.cd = this.dur;          // 天气结束后对应时长内不再进入
    // 185 轮：流星雨结束时，尚未起飞的预警圈（warnT > 0.55）取消；已起飞的火球留着继续落地（见 _fadeMeteors）
    if (this.id === 'meteor') this.meteors = this.meteors.filter((m) => m.warnT <= 0.55);
    this.phase = 'fading';
    this._fadeDur = fast ? 1.2 : WEATHER_FADE_T;
    this.fadeT = this._fadeDur;
    for (const c of this.fogClouds) c.r0 = c.r;   // 淡出收缩基准
    for (const tp of this.typhoons) tp.r0 = tp.r;
    this._applyFlags();
  }

  /** Boss 波开始等场合：立刻散（冷却照算，出门不回头）。1.2s 快速淡出 */
  forceEnd() {
    if (!this.active) return;
    const dur = this.dur;
    this._end(true);
    this.cd = dur;
  }

  /** 淡出段推进（第一百八十三轮抽出 —— 正常路径与 boss 波 blocked 路径共用） */
  _tickFade(dt, world = null) {
    this.fadeT -= dt;
    const k = this.visualAlpha;
    // 185 轮：流星雨的收尾 —— 之前淡出段不推进流星，空中的火球和燃烧区被冻住 2.5 秒再一把清掉（用户实测"卡住"）
    if (this.id === 'meteor') this._fadeMeteors(dt, world);
    for (const c of this.fogClouds) {
      if (c.r0 == null) c.r0 = c.r;
      c.r = c.r0 * k;
    }
    for (const tp of this.typhoons) {
      if (tp.r0 == null) tp.r0 = tp.r;
      tp.r = tp.r0 * k;
    }
    if (this.drops) this.drops.length = Math.ceil(this.drops.length * k);
    if (this.fadeT <= 0) {
      this.phase = 'cooldown';
      this._clearEntities();
      this.id = null;
    }
  }

  /**
   * 流星雨淡出段（纯视觉，无伤害、不上灼烧）：
   *  · 已经起飞的火球（warnT ≤ 起飞阈值）继续落地，落地只放视觉；
   *  · 燃烧区继续计时，剩余寿命封顶为淡出剩余 —— 所有特效在淡出结束的同一刻落幕，最后 0.8s 由渲染端渐隐。
   * 还没起飞的预警圈已在 _end 里取消（它们只是预警，天气已结束）。
   */
  _fadeMeteors(dt, world) {
    for (let i = this.meteors.length - 1; i >= 0; i--) {
      const m = this.meteors[i];
      m.warnT -= dt;
      if (m.warnT > 0) continue;
      this.meteors.splice(i, 1);
      if (world) this._impactVisual(world, m);
    }
    const cap = Math.max(0.01, this.fadeT);
    for (let i = this.burnZones.length - 1; i >= 0; i--) {
      const z = this.burnZones[i];
      z.t = Math.min(z.t - dt, cap);
      if (z.t <= 0) this.burnZones.splice(i, 1);
    }
  }

  /** 淡出段落地的纯视觉版 _impact：震屏减半、不扣血、不上灼烧；燃烧区寿命 ≤ 淡出剩余 */
  _impactVisual(world, m) {
    world.camera?.shake?.(4 + m.r / 80, 0.22);
    this.flash = Math.max(this.flash, 0.1);
    world.fx?.shockwave?.(m.x, m.y, m.r * 1.15, '#ffd9a0', 0.38);
    world.fx?.shockwave?.(m.x, m.y, m.r * 0.9, '#ff7a3a', 0.52);
    const shardN = Math.max(6, Math.min(24, Math.round(m.r / 10)));
    world.fx?.shards?.(m.x, m.y, Math.round(m.r * 0.4), '#ffb066', shardN);
    if (this.fadeT > 0.3) this.burnZones.push({ x: m.x, y: m.y, r: m.r, t: Math.min(3, this.fadeT), max: 3, visualOnly: true });
  }

  _clearEntities() {
    this.meteors.length = 0;
    this.burnZones.length = 0;
    this.flash = 0;
    this.typhoons.length = 0;
    this.fogClouds.length = 0;
    this.burns.length = 0;
    this.drops = null;
  }

  /** 把当前天气翻译成 hook 旗标（inactive 全部归零/归一） */
  _applyFlags() {
    const d = this.def;
    this.miss = 0; this.playerOut = 1; this.enemyOut = 1; this.moveMul = 1;
    this.enemyFireMul = 1; this.cooldownDiv = 1; this.invulnMin = 0;
    this.noKnockback = false; this.vacuum = false;
    if (!this.active || !d) {
      this.fogRamp = 0;
      // 淡出段：浓雾的视野灰幕跟随 visualAlpha 渐弱（玩法旗标保持归零）
      if (this.phase === 'fading' && d?.id === 'fog') {
        this.fogRamp = Math.min(1, this.visT / (d.rampS || 1)) * this.visualAlpha;
      }
      return;
    }
    const ramp = Math.min(1, this.visT / (d.rampS || 1));
    if (d.miss) this.miss = d.miss;
    if (d.outMul) { this.playerOut = d.outMul; this.enemyOut = d.outMul; }
    if (d.moveMul) this.moveMul = d.moveMul;
    if (d.atkBase) {
      const bonus = d.atkBase + d.atkStep * Math.floor(this.t / d.atkStepS);
      this.enemyFireMul = 1 + bonus;
      this.cooldownDiv = 1 / (1 + bonus);
    }
    if (d.invulnMin) this.invulnMin = d.invulnMin;
    if (d.noKnockback) this.noKnockback = true;
    if (d.vacuum) this.vacuum = true;
    this.fogRamp = d.id === 'fog' ? ramp : 0;
  }

  // ---------------------------------------------------------
  // 各天气的逐帧机制
  // ---------------------------------------------------------
  _tickWeather(dt, world, player) {
    const d = this.def;
    if (!d) return;

    // 烈日 / 无风：全场每秒跳 1 次
    if (d.dotPct || d.regenPct) {
      this.dotAcc += dt;
      if (this.dotAcc >= 1) {
        this.dotAcc -= 1;
        this._tickGlobalHp(world, player, d);
      }
    }

    // 流星雨
    if (d.id === 'meteor') this._tickMeteors(dt, world, player);

    // 台风：漂移 + 推挤
    if (d.id === 'typhoon') this._tickTyphoons(dt, world, player);

    // 川雾：漂移 + 推挤
    if (d.id === 'riverFog') this._tickFogClouds(dt, world, player);

    // 流星余烬烧灼（流星雨专属持续掉血）
    if (d.id === 'meteor') this._tickBurns(dt);
  }

  _tickGlobalHp(world, player, d) {
    // 烈日：扣 2% 最大生命，血量不足则扣到 1，**不会消灭目标**；
    // 无风：回 5% 最大生命。直接改 hp（不走 takeDamage，不吃护甲/闪避）。
    if (d.dotPct) {
      for (const e of world.enemies.items) {
        if (e.dead) continue;
        e.hp = Math.max(1, e.hp - e.maxHp * d.dotPct);
      }
      player.hp = Math.max(1, player.hp - player.maxHp * d.dotPct);
    }
    if (d.regenPct) {
      for (const e of world.enemies.items) {
        if (e.dead) continue;
        e.hp = Math.min(e.maxHp, e.hp + e.maxHp * d.regenPct);
      }
      player.hp = Math.min(player.maxHp, player.hp + player.maxHp * d.regenPct);
    }
  }

  _tickMeteors(dt, world, player) {
    // 每 1.5 秒在玩家周围 300-600px 圆环上刷 1-2 个警告圈
    this.metAcc += dt;
    if (this.metAcc >= 1.5 && this.meteors.length < 24) {
      this.metAcc -= 1.5;
      const n = this.rng.int(1, 2);
      for (let i = 0; i < n; i++) {
        if (this.meteors.length >= 24) break;
        const a = this.rng.next() * Math.PI * 2;
        const r = this.rng.range(300, 600);
        // 第一百三十五轮：流星大小随机性更丰富（用户："有的再小点，有的
        // 再大点，最大可以到半个屏幕那么大才比较震撼"）。固定 60 → 分档：
        //   55% 小（34~54） 30% 中（60~90，原口径带）
        //   12% 大（120~240） 3% 巨型（320~640 ≈ 半个屏幕，震撼弹）
        // r 同时是警告圈与 20% 最大生命真伤的判定半径 —— 巨型是"必须
        // 跑出去"的事件，出现率压到 3%。走 this.rng（机制参数不进
        // Math.random，保证重试本波可复现）。
        const roll = this.rng.next();
        let mr;
        if (roll < 0.55) mr = this.rng.range(34, 54);
        else if (roll < 0.85) mr = this.rng.range(60, 90);
        else if (roll < 0.97) mr = this.rng.range(120, 240);
        else mr = this.rng.range(320, 640);
        this.meteors.push({
          x: player.x + Math.cos(a) * r,
          y: player.y + Math.sin(a) * r,
          r: mr, warnT: 1,
          // 第一百一十二轮：火球从屏幕外斜着砸下来的方向（纯视觉，
          // Math.random）—— ±1 左右交替，落点不变
          side: Math.random() < 0.5 ? -1 : 1,
        });
      }
    }
    // 警告 1 秒后砸下：60px 内所有单位吃 20% 最大生命真实伤害 + 烧 3 秒
    for (let i = this.meteors.length - 1; i >= 0; i--) {
      const m = this.meteors[i];
      m.warnT -= dt;
      if (m.warnT > 0) continue;
      this._impact(world, player, m);
      this.meteors.splice(i, 1);
    }
    // 第一百一十二轮：燃烧区计时（纯视觉，与烧灼伤害同步 3 秒）
    for (let i = this.burnZones.length - 1; i >= 0; i--) {
      const z = this.burnZones[i];
      z.t -= dt;
      if (z.t <= 0) this.burnZones.splice(i, 1);
    }
  }

  _impact(world, player, m) {
    // ---- 第一百一十二轮砸地打击感（用户："不要只播一个碎裂"）----
    // 震屏 + 白闪 + 双冲击波 + 碎石火星，全走现成基建（camera.shake /
    // fx.shockwave / fx.shards），零新引擎代码。
    world.camera?.shake?.(9 + m.r / 40, 0.3);
    this.flash = 0.16;
    // 第一百三十五轮：视觉随流星半径走（固定 70/55 的冲击波配 640 的
    // 巨型流星会像针尖冒花）
    world.fx?.shockwave?.(m.x, m.y, m.r * 1.15, '#ffd9a0', 0.38);
    world.fx?.shockwave?.(m.x, m.y, m.r * 0.9, '#ff7a3a', 0.52);
    const shardN = Math.max(8, Math.min(30, Math.round(m.r / 8)));
    world.fx?.shards?.(m.x, m.y, Math.round(m.r * 0.45), '#ffb066', shardN);
    world.fx?.shards?.(m.x, m.y, Math.round(m.r * 0.32), '#e8542a', Math.max(5, Math.round(shardN / 2)));
    // 阶段 4：地面保留 3 秒燃烧区（对应 3 秒掉血，"这里还在烧，别进来"）
    // 第一百三十五轮：半径随流星（旧固定 60 配巨型流星不协调）
    this.burnZones.push({ x: m.x, y: m.y, r: m.r, t: 3, max: 3 });
    const hit = (unit) => {
      const dmg = unit.maxHp * 0.2;
      let dealt = 0;
      if (unit === player) {
        dealt = player.takeDamage(dmg, m.x, m.y, { pierceArmor: true, noMiss: true });
      } else {
        const res = unit.takeDamage(dmg, { pierceArmor: true });
        dealt = res?.dealt ?? 0;
      }
      // 无敌帧/miss 吃掉的砸击不上余烬
      if (dealt > 0) this.burns.push({ e: unit, t: 3, acc: 0, isPlayer: unit === player });
    };
    // 网格查询（世界空间格，判定半径 = 流星半径）
    const R = m.r;
    const near = world.grid ? world.grid.query(m.x, m.y, R, []) : [];
    for (const e of near) {
      if (e.dead) continue;
      const dx = e.x - m.x, dy = e.y - m.y;
      if (dx * dx + dy * dy <= R * R) hit(e);
    }
    const pdx = player.x - m.x, pdy = player.y - m.y;
    if (pdx * pdx + pdy * pdy <= R * R) hit(player);
  }

  _tickBurns(dt) {
    // 每秒 2% 最大生命，持续 3 秒（走 takeDamage，能打死，但吃不到护甲）
    for (let i = this.burns.length - 1; i >= 0; i--) {
      const b = this.burns[i];
      b.t -= dt;
      b.acc += dt;
      if (b.e.dead) { this.burns.splice(i, 1); continue; }
      while (b.acc >= 1) {
        b.acc -= 1;
        const dmg = b.e.maxHp * 0.02;
        if (b.isPlayer) b.e.takeDamage(dmg, b.e.x, b.e.y, { pierceArmor: true, noMiss: true });
        else b.e.takeDamage(dmg, { pierceArmor: true });
      }
      if (b.t <= 0) this.burns.splice(i, 1);
    }
  }

  _spawnTyphoon(world, player) {
    const a = this.rng.next() * Math.PI * 2;
    const r = this.rng.range(150, 450);
    const dir = this.rng.next() * Math.PI * 2;
    const spd = this.rng.range(50, 90);
    this.typhoons.push({
      x: player.x + Math.cos(a) * r, y: player.y + Math.sin(a) * r,
      r: this.rng.range(90, 140),
      vx: Math.cos(dir) * spd, vy: Math.sin(dir) * spd,
      spin: this.rng.next() < 0.5 ? 1 : -1,
      turnT: this.rng.range(1.5, 3),
      parts: [],   // 第一百一十轮：吸入粒子（纯视觉，Math.random）
    });
  }

  _tickTyphoons(dt, world, player) {
    const half = world.halfSize;
    for (const t of this.typhoons) {
      t.turnT -= dt;
      if (t.turnT <= 0) {
        t.turnT = this.rng.range(1.5, 3);
        const a = this.rng.next() * Math.PI * 2;
        const spd = Math.hypot(t.vx, t.vy);
        t.vx = Math.cos(a) * spd; t.vy = Math.sin(a) * spd;
      }
      t.x += t.vx * dt; t.y += t.vy * dt;
      t.x = Math.max(-half, Math.min(half, t.x));
      t.y = Math.max(-half, Math.min(half, t.y));

      // ---- 第一百一十轮：吸入粒子（纯视觉装饰，Math.random）----
      // 从外圈螺旋吸进中心。用户："随着时间，从外圈以螺旋轨迹被吸入
      // 台风中心，然后消失。"
      if (t.parts.length < 16 && Math.random() < dt * 7) {
        t.parts.push({
          a: Math.random() * Math.PI * 2,
          d: t.r * (1.05 + Math.random() * 0.7),
          w: (2.6 + Math.random() * 2.6) * t.spin,   // 角速度
          life: 0,
          max: 0.9 + Math.random() * 0.8,
          s: 1 + Math.random() * 1.8,                 // 碎屑大小
        });
      }
      for (let i = t.parts.length - 1; i >= 0; i--) {
        const p = t.parts[i];
        p.life += dt;
        p.a += p.w * dt * (1.6 + (1 - p.d / (t.r * 1.8)) * 2.2); // 越近转越快
        p.d -= dt * (t.r / p.max) * 1.15;
        if (p.d <= 4 || p.life > p.max) t.parts.splice(i, 1);
      }

      // 挤开所有单位（沿台风漂移方向推，不算击退所以台风天免击退也照推）
      const push = (u) => {
        const dx = u.x - t.x, dy = u.y - t.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > t.r * t.r) return;
        const d = Math.sqrt(d2) || 1;
        const falloff = 1 - d / t.r;            // 越靠中心推得越狠
        const mag = 260 * (0.35 + 0.65 * falloff);
        u.x += ((dx / d) * 0.6 + (t.vx / 100)) * mag * dt;
        u.y += ((dy / d) * 0.6 + (t.vy / 100)) * mag * dt;
        // 第一百一十轮：切向拉扯 —— 用户："根据距离中心的位置，给一个
        // 微弱的'向中心旋转'的切向力（不需要真做物理，视觉坐标偏移
        // 一下即可）。" 径向外推不变，叠加绕心旋转 + 轻微向心。
        const tx = (-dy / d) * t.spin, ty = (dx / d) * t.spin;
        const sw = mag * 0.5 * (0.3 + 0.7 * falloff);
        u.x += tx * sw * dt - (dx / d) * mag * 0.22 * falloff * dt;
        u.y += ty * sw * dt - (dy / d) * mag * 0.22 * falloff * dt;
      };
      push(player);
      for (const e of world.enemies.items) {
        if (!e.dead) push(e);
      }
    }
  }

  _spawnFogCloud(world, player) {
    const a = this.rng.next() * Math.PI * 2;
    const r = this.rng.range(0, 500);
    const dir = this.rng.next() * Math.PI * 2;
    const spd = this.rng.range(35, 70);
    // ---- 第一百一十一轮：云团聚合结构 ----
    // 用户："不要画完美的正圆。请在一个长条形/不规则的区域内，生成
    // 6-8 个大小不一、边缘极度模糊的椭圆雾气块。让它们以不同的速度
    // 缓慢移动、互相交叠。" 逻辑推挤半径不变（155-240，第一百零九轮
    // 的加强保留），视觉上由 7 个错落的椭圆雾块拼成一个不规则云团。
    const blobs = [];
    for (let i = 0; i < 7; i++) {
      const ba = (i / 7) * Math.PI * 2 + this.rng.next() * 0.9;
      const bd = this.rng.range(0, 0.62);
      blobs.push({
        ox: Math.cos(ba) * bd, oy: Math.sin(ba) * bd * 0.72,   // 略压扁 → 长条感
        rx: this.rng.range(0.34, 0.72),                        // 相对云团半径
        ry: this.rng.range(0.22, 0.5),
        spd: this.rng.range(0.5, 1.6),                          // 各块漂移速度不同
        phase: this.rng.next() * Math.PI * 2,
      });
    }
    this.fogClouds.push({
      x: player.x + Math.cos(a) * r, y: player.y + Math.sin(a) * r,
      r: this.rng.range(110 * Math.SQRT2, 170 * Math.SQRT2),
      vx: Math.cos(dir) * spd, vy: Math.sin(dir) * spd,
      turnT: this.rng.range(2, 4),
      seed: this.rng.next() * 100,
      blobs,
      parts: [],      // 水汽升腾粒子（纯视觉，Math.random）
      streaks: [],    // 雾内流线（顺着推人方向，纯视觉）
    });
  }

  _tickFogClouds(dt, world, player) {
    const half = world.halfSize;
    for (const c of this.fogClouds) {
      c.turnT -= dt;
      if (c.turnT <= 0) {
        c.turnT = this.rng.range(2, 4);
        const a = this.rng.next() * Math.PI * 2;
        const spd = Math.hypot(c.vx, c.vy);
        c.vx = Math.cos(a) * spd; c.vy = Math.sin(a) * spd;
      }
      c.x += c.vx * dt; c.y += c.vy * dt;
      // 出界就绕回玩家附近（雾跟着战团走）
      if (Math.abs(c.x - player.x) > half || Math.abs(c.y - player.y) > half
        || Math.abs(c.x) > half || Math.abs(c.y) > half) {
        const a = this.rng.next() * Math.PI * 2;
        const r = this.rng.range(200, 500);
        c.x = player.x + Math.cos(a) * r;
        c.y = player.y + Math.sin(a) * r;
        c.parts.length = 0;      // 传送后旧粒子/流线别飘在原地
        c.streaks.length = 0;
      }
      // 雾里的单位被推来推去 —— 第一百零九轮：推力 ×2（0.55 → 1.1 倍
      // 雾团漂移速度），用户："推力增加一倍……走进去没感觉"。
      const push = (u) => {
        const dx = u.x - c.x, dy = u.y - c.y;
        if (dx * dx + dy * dy > c.r * c.r) return;
        u.x += c.vx * 1.1 * dt;
        u.y += c.vy * 1.1 * dt;
      };
      push(player);
      for (const e of world.enemies.items) {
        if (!e.dead) push(e);
      }

      // ---- 第一百一十一轮：水汽升腾粒子 + 雾内流线（纯视觉装饰，
      // Math.random；走 RNG 纪律：不碰主流与 weatherRng）----
      // 粒子：雾区内生成，缓缓向上飘动并淡出，模拟水汽升腾。
      if (c.parts.length < 10 && Math.random() < dt * 4) {
        const pa = Math.random() * Math.PI * 2;
        const pd = Math.random() * c.r * 0.8;
        c.parts.push({
          x: c.x + Math.cos(pa) * pd, y: c.y + Math.sin(pa) * pd,
          vy: -(16 + Math.random() * 26),
          wx: Math.random() * Math.PI * 2,
          life: 0, max: 2 + Math.random() * 1.6,
          s: 1 + Math.random() * 1.6,
        });
      }
      for (let i = c.parts.length - 1; i >= 0; i--) {
        const p = c.parts[i];
        p.life += dt;
        p.y += p.vy * dt;
        p.x += Math.sin(p.life * 1.8 + p.wx) * 9 * dt;   // 轻微左右摇
        if (p.life > p.max) c.parts.splice(i, 1);
      }
      // 流线：顺着雾团漂移（= 推人）方向快速掠过，让玩家看见力场。
      const vn = Math.hypot(c.vx, c.vy) || 1;
      if (c.streaks.length < 8 && Math.random() < dt * 6) {
        const pa = Math.random() * Math.PI * 2;
        const pd = Math.random() * c.r * 0.85;
        c.streaks.push({
          x: c.x + Math.cos(pa) * pd, y: c.y + Math.sin(pa) * pd,
          dx: (c.vx / vn), dy: (c.vy / vn),
          spd: 120 + Math.random() * 110,
          len: 36 + Math.random() * 56,
          life: 0, max: 0.45 + Math.random() * 0.3,
        });
      }
      for (let i = c.streaks.length - 1; i >= 0; i--) {
        const s = c.streaks[i];
        s.life += dt;
        s.x += s.dx * s.spd * dt;
        s.y += s.dy * s.spd * dt;
        if (s.life > s.max) c.streaks.splice(i, 1);
      }
    }
  }

  // ---- 雾雨雨滴（纯视觉，Math.random；屏幕坐标）----
  _initDrops() {
    this.drops = [];
    // 第一百六十二轮：移动端雨滴密度减半（桌面 170 不变）
    for (let i = 0; i < (MOBILE ? 85 : 170); i++) {
      this.drops.push({
        x: Math.random() * 1, y: Math.random() * 1,
        len: 9 + Math.random() * 12,
        spd: 620 + Math.random() * 420,
        drift: 60 + Math.random() * 60,
      });
    }
  }
}
