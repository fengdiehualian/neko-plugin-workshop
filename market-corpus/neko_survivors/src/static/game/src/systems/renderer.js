// ============================================================
// 渲染器
// 占位图形阶段：所有实体用几何形状 + 颜色区分。
// 每个形状带一点"猫娘感"：耳朵、尾巴、眼睛。
// 之后替换为贴图时，只需改 draw* 方法内部。
// ============================================================

import { TAU, clamp } from '../core/math.js';
// 第一百六十二轮：移动端性能档位（shadowBlur 全关 / dpr 1.5 / 标题粒子减半；
// 桌面 MOBILE=false，所有原值不变）
import { MOBILE, MOBILE_TUNE } from '../core/mobile.js';
import { PICKUP_TYPE } from '../entities/pickup.js';
import { FX_TYPE, fxSettings } from '../systems/fx.js';
// 敌人贴图（第七轮）。没加载到贴图时**照旧走几何形状**，所以贴图始终是可选增强。
import { sprites } from '../core/assets.js';
// 吸取半径要从宠物那边取，避免渲染器和逻辑各写一个数字导致环和实际范围对不上
import { PET_VACUUM_RADIUS } from '../entities/pet.js';
// 第五十四轮：芙兰战空气墙可视化的竞技场矩形（和逻辑层同一个数值来源）
import { ARENA, PURGE_CD } from './flandre.js';
// 第一百八十五轮：芙兰战渲染 v2 版本 A（水晶翅膀背景 / 专属星形法阵 / 宣言冲击波 / 铭牌 HUD）
import * as FR from './flandre-render.js';
// 第一百八十五轮：天气特效 v2（流星雨火球/燃烧区、血月、太阳、花瓣、热浪、雨纹）
import * as WFX from './weather-fx.js';
// 第一百九十八轮：雪原「冰晶迷宫」渲染（整座烘焙成一张图，每帧一次 drawImage）
import * as MZ from './maze-fx.js';
// 第二百零三轮：蕾米莉亚终演出（背景/双人/cut-in/HUD）
import * as RR from './remilia-render.js';
import { REMILIA_ARENA } from './remilia.js';
import { drawZigzagBlade } from './dmc-blades.js';
// 第一百八十四轮：万圣节双猫战渲染（发光弹幕 / Bloom / 墓地背景 / 灯珠字母 / 像素 HUD），
// 全部在独立模块，渲染器只留调用点
import * as HR from './halloween-render.js';
// 第一百八十五轮：敌弹视觉语言 v2（白核彩环 + 柔光 + 7 种弹型），laser / jade 仍走旧渲染
import * as BFX from './bullet-fx.js';
// 眼睛锚点（第九轮）：贴图按 2.6r 画，眼睛必须按同一个尺度画在"脸"上，
// 否则就会像用户看到的那样"眼睛飞到脸外面"。见 data/enemy-eyes.js 的说明。
import { eyeAnchor, eyePlacement } from '../data/enemy-eyes.js';

// ============================================================
// 第六十九轮：芙兰战背景素材懒加载缓存（flandre-stage7 解包素材，
// 已拷入 assets/flandre-stage7/）。加载完成前/失败时返回 null，
// 渲染层退回纯色保底 —— 贴图永远是可选增强，绝不阻塞、绝不黑屏。
// ============================================================
const _flandreBgCache = new Map();
function flandreBgImg(file) {
  let e = _flandreBgCache.get(file);
  if (!e) {
    e = { img: null, failed: false };
    try {
      if (typeof Image !== 'undefined') {
        const im = new Image();
        im.onload = () => { e.img = im; };
        im.onerror = () => { e.failed = true; };
        im.src = `./assets/flandre-stage7/${file}`;
      } else {
        e.failed = true;   // Node 测试环境：走纯色保底分支
      }
    } catch { e.failed = true; }
    _flandreBgCache.set(file, e);
  }
  return e.img && !e.failed ? e.img : null;
}

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    // 第一百六十二轮：移动端 DPR 上限 1.5（像素填充率是手机第二大瓶颈）
    this.dpr = Math.min(window.devicePixelRatio || 1, MOBILE ? MOBILE_TUNE.DPR_CAP : 2);
    this.width = 0;
    this.height = 0;
    this.bgPattern = null;
    // 关卡背景贴图（第七轮）。默认 'lab'；加载不到就回落到纯色 + 网格。
    this.backgroundId = 'lab';
    // 背景离屏缓存（第八轮性能优化）。详见 _ensureBgCache。
    this._bgCache = null;
    this._bgCacheSrc = null;
    this._bgCacheTile = 0;
    // 受击闪白剪影缓存（第八轮）。key = 贴图 → Map(颜色 → 离屏画布)
    this._flashCache = new Map();
    // 径向光晕精灵缓存（第十轮性能优化）。
    //
    // 为什么需要：第九轮把特效做得华丽之后，`createRadialGradient` 变成
    // 每帧、每个特效都新建一次的对象 —— 基准实测 IMPACT 单项 68µs，
    // 400 个就是 27ms，直接超掉 16.7ms 的帧预算。
    //
    // 但光晕的形状是**固定**的，只有颜色在变（而颜色种类就那么十几种）。
    // 所以每种颜色只烘焙一次到离屏画布，之后 drawImage 缩放复用。
    // 实测比每次新建渐变快一个数量级，而且画出来的东西**像素级一致**。
    this._glowCache = new Map();
    // 子弹拖尾的线性渐变条，同理按颜色缓存
    this._tailCache = new Map();
    // 本帧特效数量，供 LOD 降级判断（见 _fxSaturated）
    this._fxCount = 0;
  }

  /**
   * 取（并缓存）一张径向光晕精灵：中心白 → 中段 color → 边缘全透明。
   *
   * 精灵是 128×128 的固定画布，用的时候 drawImage 缩放到目标半径。
   * 缩放是硬件插值的，几乎不花钱；而 createRadialGradient + fill
   * 每帧都要重新解析渐变并逐像素求值。
   */
  _glowSprite(color) {
    let cv = this._glowCache.get(color);
    if (cv) return cv;
    const S = 128;
    cv = document.createElement('canvas');
    cv.width = S; cv.height = S;
    const c = cv.getContext('2d');
    const g = c.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
    g.addColorStop(0, '#ffffff');
    g.addColorStop(0.45, color);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = g;
    c.fillRect(0, 0, S, S);
    // 缓存上限：颜色种类理论上有限，但存档/皮肤可能带来长尾，
    // 加个上限避免无限增长（超了就整体清空重来，代价可忽略）
    if (this._glowCache.size > 64) this._glowCache.clear();
    this._glowCache.set(color, cv);
    return cv;
  }

  /** 取（并缓存）一条"透明 → color"的水平渐变条，给子弹拖尾用。 */
  _tailSprite(color) {
    let cv = this._tailCache.get(color);
    if (cv) return cv;
    const W = 64, H = 8;
    cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const c = cv.getContext('2d');
    const g = c.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, 'rgba(0,0,0,0)');
    g.addColorStop(1, color);
    c.fillStyle = g;
    c.fillRect(0, 0, W, H);
    if (this._tailCache.size > 64) this._tailCache.clear();
    this._tailCache.set(color, cv);
    return cv;
  }

  /**
   * 当前是否处于"特效饱和"状态 —— 用于降级绘制细节。
   *
   * 思路（LOD，游戏业的标准做法）：特效少的时候用满细节；
   * 特效多到糊成一片时，**外发光那几层最宽、最吃填充率的装饰
   * 本来就看不出来了**，直接省掉，省下的填充率就是帧率。
   *
   * 阈值为什么是 120：实机基准里正常战斗的峰值约 60~90 个特效，
   * 120 之上才开始降级 —— 也就是**正常玩永远看不到降级**，
   * 只有后期极端堆叠时才会生效。
   */
  _fxSaturated() {
    return this._fxCount > 120;
  }

  resize() {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    this.width = w;
    this.height = h;
    this.canvas.width = Math.floor(w * this.dpr);
    this.canvas.height = Math.floor(h * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    // 尺寸变了，背景离屏缓存要重建（否则会露出没铺到的边）
    this._bgCache = null;
    this._bgCacheSrc = null;
  }

  // =========================================================
  // 标题界面背景：径向深蓝紫星空 + 上升魔法光点 + 暗角
  // （第一百六十轮视觉重构 —— 用户："主界面现在太像网页Demo了……神秘、
  //   高级、带有魔法与猫娘的结合感""把背景里那些生硬的几何猫爪删掉，
  //   改成类似星空的动态深色背景（比如深蓝色到紫色的径向渐变）""增加
  //   粒子特效：背景里有缓缓向上升起的粉色/蓝色光点""屏幕边缘加一圈
  //   极暗的紫红色 Vignette"——旧版线性灰渐变 + 7 个椭圆拼的几何爪印
  //   全部退役。）
  // =========================================================
  renderIdleBackground() {
    const ctx = this.ctx;
    const W = this.width, H = this.height;
    const t = performance.now() / 1000;

    // ---- 径向底：明亮天空色（206 轮：标题页整体换成浅色主题）----
    const grad = ctx.createRadialGradient(
      W * 0.5, H * 0.36, 30,
      W * 0.5, H * 0.52, Math.max(W, H) * 0.78,
    );
    grad.addColorStop(0, '#f2f9ff');
    grad.addColorStop(0.55, '#dcecff');
    grad.addColorStop(1, '#cfe4fa');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    // ---- 远景静态星（闪烁）：浅底上用淡蓝灰，像细小的浮尘 ----
    ctx.save();
    ctx.fillStyle = '#7fa6cf';
    // 第一百六十二轮：移动端标题星星减半（桌面 46 不变）
    for (let i = 0; i < (MOBILE ? 23 : 46); i++) {
      const seed = i * 97.13;
      const x = (seed * 5.31) % W;
      const y = (seed * 3.77) % H;
      const tw = 0.12 + 0.5 * Math.abs(Math.sin(t * 0.7 + i * 1.71));
      ctx.globalAlpha = tw * 0.45;
      ctx.beginPath();
      ctx.arc(x, y, 0.7 + (i % 3) * 0.4, 0, TAU);
      ctx.fill();
    }
    ctx.restore();

    // ---- 上升魔法光点（粉/蓝/紫，双层圆光晕，向上漂 + 轻微横摆）----
    // 与芙兰战的魔法粒子同一色系，主界面和战斗的氛围连起来。
    ctx.save();
    // 第一百六十二轮：移动端标题光尘减半（桌面 40 不变）
    for (let i = 0; i < (MOBILE ? 20 : 40); i++) {
      const seed = i * 137.508;
      const speed = 9 + (i % 5) * 7;                       // 各自不同的上升速度
      const x = (((seed * 7.3 + Math.sin(t * 0.35 + i) * 42) % (W + 60)) + W + 60) % (W + 60) - 30;
      const y = (((seed * 3.7 - t * speed) % (H + 90)) + H + 90) % (H + 90) - 45;
      const r = 1.1 + (i % 3);
      const glow = 0.10 + (i % 5) * 0.05;
      // 206 轮：浅底上要更饱和的颜色才看得见（原来是给深紫星空配的）
      ctx.fillStyle = i % 3 === 0 ? '#ff5d9e' : i % 3 === 1 ? '#2aa8d0' : '#8a6bf0';
      // 外圈光晕（大而淡）+ 内核（小而亮）
      ctx.globalAlpha = glow * 0.85;
      ctx.beginPath();
      ctx.arc(x, y, r * 3.2, 0, TAU);
      ctx.fill();
      ctx.globalAlpha = Math.min(0.9, glow + 0.5);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, TAU);
      ctx.fill();
    }
    ctx.restore();

    // ---- 暗角：浅蓝雾（原来是暗紫红，浅底上会脏）----
    const vig = ctx.createRadialGradient(
      W * 0.5, H * 0.5, Math.min(W, H) * 0.42,
      W * 0.5, H * 0.5, Math.max(W, H) * 0.74,
    );
    vig.addColorStop(0, 'rgba(255,255,255,0)');
    vig.addColorStop(1, 'rgba(120,170,220,0.16)');
    ctx.fillStyle = vig;
    ctx.fillRect(0, 0, W, H);
  }

  // =========================================================
  // 主绘制入口
  // =========================================================

  render(world, camera, player) {
    const ctx = this.ctx;
    const W = this.width, H = this.height;
    // 地形描边要读 world.terrain（见 _drawTerrain）。
    // 存在实例上而不是层层传参，是因为 _drawBackground 的签名已经被
    // 好几个地方调用了，加参数会牵动一圈。
    this.world = world;

    // ---- 第一百八十轮：天彗龙屏幕震动（背景一起晃，压迫感拉满）----
    // 万圣节双猫分裂时的震动（hf.shake 0~1 ×9px）与天彗龙震动取大者
    const hwShake = (world.halloweenFight?.shake ?? 0) * 9;
    const shk = Math.max(world.comet?.shake ?? 0, hwShake);
    if (shk > 0) {
      ctx.save();
      ctx.translate((Math.random() * 2 - 1) * shk,
        (Math.random() * 2 - 1) * shk);
      this._cometShakeRestore = true;
    } else {
      this._cometShakeRestore = false;
    }

    // ---- 背景 ----
    this._drawBackground(ctx, camera, W, H);

    // ---- 第四十九轮：芙兰朵露战背景（第七十二轮改法）----
    // 用户："背景还是错了，黑色慢慢转换为暗红是暗红色，纯色块变化，不要
    // 有图。等芙兰出来后再慢慢浮现图，而且图不是贴在整个屏幕空间大小，
    // 是芙兰boss战斗的玩家活动区域大小，就是你用红框圈起来的那个大小。"
    // 分层：黑底（序章 3 秒渐黑）→ 纯色暗红层（全屏，含墙外）→
    // eff07 只裁进空气墙内的活动区（芙兰登场后浮现）。
    // 全部只画在 world.flandreFight 非空时 —— 战斗结束对象被置空，
    // 下一帧自动回到常规背景，无限模式/重开零残留。
    const fight = world.flandreFight;
    if (fight) {
      this._drawFlandreBg(ctx, fight, camera, W, H);
    }
    // 第一百八十四轮：万圣节双猫背景（屏幕空间整层烘焙：黑→暖棕夜空/红月/栅栏/墓碑/雾）
    if (world.halloweenFight) HR.drawBg(ctx, world.halloweenFight, camera, W, H, this.dpr);
    // 第二百零三轮：蕾米莉亚终演出背景（深红夜空 / 血色月亮 / 红雾 / 红魔馆远景 / 蝙蝠群）—— 屏幕空间
    if (world.remiliaFight) {
      RR.drawBg(ctx, world.remiliaFight, W / 2 + (REMILIA_ARENA.cx - camera.cx), H / 2 + (REMILIA_ARENA.cy - camera.cy), W, H);
    }

    ctx.save();
    ctx.translate(W / 2 - camera.cx, H / 2 - camera.cy);

    // ---- 第一百四十一轮：维吉尔战场地背景（大排档椅子背景，ARENA 方形内，
    // 世界坐标系 —— 画在游戏背景之上、一切实体之下；透明度随入场演出淡入）----
    if (world.vergilFight) world.vergilFight.renderArena(ctx, camera);

    // ---- 第一百八十四轮：万圣节双猫战背景（世界坐标，实体之下）----
    // 黑屏换场（t<0 全黑 → 变亮）→ 伪横版夜空（深紫渐变+满月+山影，
    // hill_a 贴图下轮接入）+ 宽屏场地边界。


    // 第二百零三轮：绕场飞的蝙蝠群（世界坐标，实体之下）
    if (world.remiliaFight) RR.drawBats(ctx, world.remiliaFight, REMILIA_ARENA.cx, REMILIA_ARENA.cy);

    // ---- 世界边界 ----
    this._drawWorldBounds(ctx, world);

    // ---- 第一百七十二轮：刷怪预告圈（世界坐标，压在实体之下）----
    // 用户："给刷怪的地方提前画个小圈……提前闪烁1秒的小圈？"
    // 闪烁 + 收缩的红色警戒圈：1 秒后这里会落地一只怪。
    if (world.spawnWarnings?.length) this._drawSpawnWarnings(ctx, world);

    // ---- 第五十四轮：芙兰战的空气墙可视化 ----
    // 常显竞技场边界 + 撞墙时玩家周围的淡红圆环（main 里钳制时点亮 wallFlash）
    if (world.flandreFight) this._drawFlandreWalls(ctx, player);
    // ---- 第七十二轮：莱瓦汀旋转激光（世界坐标，压在实体下面）----
    if (world.flandreFight?.spell?.id === 'laevateinn') this._drawFlandreLasers(ctx, world.flandreFight);
    // ---- 第七十三轮：四重存在的 4 个分身 = 纯动画发射器（视觉体，不在
    // 敌人数组里），按敌人同款贴图画（s = r×2.6，锚点上移 0.60s）----
    if (world.flandreFight?.spell?.id === 'fourkind') this._drawFlandreClones(ctx, world.flandreFight);
    // ---- 第七十五轮：EP 三卡的发射器可视体（血痕的大玉领弹 / 心脏的
    // 心室 / 易碎之翼的羽翼）—— spell.st.vis 由各符卡 tick 每帧重建 ----
    if (world.flandreFight?.spell?.st?.vis) this._drawFlandreEmitters(ctx, world.flandreFight.spell.st.vis);
    // ---- 第一百轮：芙兰背后的大魔法阵（世界坐标，压在实体最底下）----
    if (world.flandreFight) this._drawFlandreMistOrbs(ctx, world.flandreFight);
    if (world.flandreFight) this._drawFlandreMagicCircle(ctx, world.flandreFight);
    // ---- 第一百零二轮：常驻呼吸雾提到魔法阵之上、实体之下 ----
    // 用户实机反馈："雾气几乎看不见——大概率是被魔法阵的加法混合光芒
    // 给吞了……把常驻呼吸雾从世界底层提到魔法阵之上，Boss和弹幕之下，
    // 让它在阵的前面飘。"
    if (world.flandreFight) this._drawFlandreAmbientMist(ctx, world.flandreFight);
    // 第六十三轮：Four of a Kind 的分身改用真敌人实体绘制（正常大小、
    // 可被击中），这里的画布版分身退役。本体的隐身由 _drawEnemy 处理。

    // ---- 拾取物 ----
    for (const p of world.pickups.items) {
      if (!camera.isVisible(p.x, p.y, 40)) continue;
      this._drawPickup(ctx, p);
    }

    // ---- 敌人 ----
    for (const e of world.enemies.items) {
      if (!camera.isVisible(e.x, e.y, 60)) continue;
      this._drawEnemy(ctx, e);
    }
    // ---- 第一百五十三轮：变异体黑红飘散粒子（敌人之上、子弹之下）----
    this._tickMutantParticles(ctx);

    // ---- 第一百八十四轮：万圣节双猫立绘画在弹幕**下层**（她们没有判定，是背景里的演出角色；
    // 灯珠字母弹幕开场会落在猫身下，画在上层会把 EXTINCTION 左半截挡住）----
    if (world.halloweenFight) HR.drawCats(ctx, world.halloweenFight);
    // 第二百零三轮：蕾米 + 芙兰 —— 放到弹幕**上层**（见下方 enemyBullets 之后）。
    // 这场弹幕密度是全游戏最高的（终段 ~900 发），画在下面两人会被完全埋掉。

    // ---- 敌人子弹 ----
    BFX.beginFrame(world.enemyBullets.items.length, !!(world.flandreFight || world.remiliaFight));   // 按弹量选 LOD（拖尾/柔光）；199 轮：芙兰战永远全质量，其它战斗带迟滞
    // 199 轮：普通弹收集成一批交给 BFX.drawBatch（两遍绘制：柔光/余烬一次加法 + 本体一次普通；旋转 setTransform 合成）。
    // 激光(laser)与大玉(jade)是特例，仍走 _drawEnemyBullet，画在批之后（在最上层，大玉的判定圈要看得清）。
    const batch = this._bfxBatch ?? (this._bfxBatch = []);
    batch.length = 0;
    let special = null;
    for (const b of world.enemyBullets.items) {
      if (b.halloween) continue;   // 万圣节弹走自己的发光批处理（下面 HR.drawWorldFx）
      if (!camera.isVisible(b.x, b.y, 40)) continue;
      if (b.laser || b.shape === 'jade') (special ??= []).push(b);
      else batch.push(b);
    }
    BFX.drawBatch(ctx, batch);
    if (special) for (const b of special) this._drawEnemyBullet(ctx, b);
    // 第二百零三轮：蕾米 + 芙兰画在弹幕之上（终段近千发弹，放下面会被埋掉）
    if (world.remiliaFight) {
      RR.drawRemilia(ctx, world.remiliaFight);
      RR.drawFlandreSprite(ctx, world.remiliaFight, (id) => sprites.get('enemy', id));
    }

    // 第一百八十四轮：万圣节双猫战世界层 —— 南瓜/糖果/猫爪/光柱 + 全部发光弹幕(lighter) + Bloom
    if (world.halloweenFight) HR.drawWorldFx(ctx, world, world.halloweenFight, camera, W, H);

    // ---- 炮台（画在敌人下面，避免挡住敌人轮廓） ----
    for (const t of world.turrets.items) {
      if (!camera.isVisible(t.x, t.y, 60)) continue;
      this._drawTurret(ctx, t);
    }

    // ---- 玩家 ----
    this._drawPlayer(ctx, player);
    // ---- 第一百七十一轮：倒地救援环（P1 倒地也画）----
    if (player.downed) this._drawRescueRing(ctx, player);

    // ---- 第一百七十轮（2P）→ 第一百八十四轮（1~4P）：队友猫娘 P2~P4 ----
    // 复用同一绘制流程（完整角色渲染），头顶另加槽位角标（各自专属色）
    // + 独立血条 + 倒地时的黑夜君临式救援环。
    for (const mate of (world.partners?.() ?? [])) {
      if (!camera.isVisible(mate.x, mate.y, 60)) continue;
      this._drawPlayer(ctx, mate);
      if (mate.downed) this._drawRescueRing(ctx, mate);
      const slot = (mate === world.player2) ? 0 : (mate === world.player3) ? 1 : 2;
      this._drawP2Badge(ctx, mate, Renderer.PARTNER_COLORS[slot] ?? '#9ec7ff', `P${slot + 2}`);
    }

    // ---- 第一百六十轮：四重存在分身（莱万汀）----
    // 轻量光影绘制（不采立绘采样，3 个分身的每帧开销 ≈ 3 个普通怪）：
    // 粉紫半透明身形 + 猫耳 + 顶部血条，一眼可辨"这不是本体"。
    for (const ph of world.phantoms ?? []) {
      if (ph.dead || !camera.isVisible(ph.x, ph.y, 60)) continue;
      this._drawPhantom(ctx, ph);
    }

    // ---- 宠物（小小猫娘）：画在玩家上方，避免被玩家挡住 ----
    if (world.pet && !world.pet.dead && camera.isVisible(world.pet.x, world.pet.y, 60)) {
      // 把 camera 传进去：台词气泡要按缩放反向补偿字号
      // （renderer 本身不持有 camera，它是每帧从 world 拿的）
      this._drawPet(ctx, world.pet, camera);
    }

    // ---- 玩家子弹 ----
    // 第三十九轮：玩家特效透明度 —— 玩家子弹套缩放 ctx，敌人子弹原样
    for (const b of world.bullets.items) {
      if (!camera.isVisible(b.x, b.y, 40)) continue;
      this._drawBullet(b.isEnemyBullet ? ctx : this._scaledCtx(ctx), b);
    }

    // ---- 特效 ----
    // 记录本帧的特效数量，供 _fxSaturated() 做 LOD 降级判断。
    this._fxCount = world.fx.items.length;
    // 第三十九轮：带 player 标记的特效按"玩家特效"透明度绘制
    for (const fx of world.fx.items) {
      this._drawFX(fx.player ? this._scaledCtx(ctx) : ctx, fx);
    }

    // ---- 浮动伤害数字（画在最上层） ----
    for (const fx of world.fx.items) {
      if (fx.type === FX_TYPE.DAMAGE_TEXT) this._drawDamageText(ctx, fx);
    }

    // ---- 第一百零八轮：天气世界层（川雾云/台风/流星/浓雾视野）----
    // 压在实体与伤害数字之上、屏幕 UI 之下。
    this._drawWeatherWorld(ctx, world, camera, player, W, H);

    // ---- 第一百四十一轮：维吉尔战世界层演出（椅子/追踪月牙/光圈/裂缝）----
    if (world.vergilFight) world.vergilFight.renderWorld(ctx, camera);

    // ---- 第一百八十四轮：万圣节双猫立绘（世界坐标，实体之上 —— "把
    // 两个活泼的猫娘直接站在我们的ui界面上攻击玩家"：镜头锁定后世界
    // 坐标即屏幕位置，顶部悬浮 + Math.sin 浮动，无 hitbox 纯演出）----


    // ---- 第一百四十一轮：阎魔刀 · 次元斩（玩家侧满屏刀光，金色偏白）----
    if (world.playerUlt) {
      for (const b of world.playerUlt.blades) {
        const k = 1 - b.t / b.life;
        drawZigzagBlade(ctx, b.x1, b.y1, b.x2, b.y2, b.seed, k, 7, '#ffe9a8');
      }
    }

    // ---- 第一百八十轮：天彗龙世界层（地面黑影/火雨/彗尾/龙本体/
    //      冲击波/声波环/眩晕星星）—— 压在实体与伤害数字之上、屏幕 UI 之下
    //      （第一百八十二轮：传入 world —— 眩晕星星要画在玩家头顶）----
    if (world.comet) world.comet.drawWorld(ctx, world);

    ctx.restore();

    // ---- 屏幕空间叠加 ----
    this._drawScreenOverlay(ctx, world, player, W, H);

    // ---- 第一百八十轮：天彗龙屏幕层（白闪/撕裂暗纹/？？？袭击横幅）----
    if (world.comet) world.comet.drawScreen(ctx, W, H);

    // ---- 第一百四十一轮：维吉尔战屏幕层（大招暗幕/红边/白闪/Boss 血条）----
    if (world.vergilFight) world.vergilFight.renderScreen(ctx, W, H);
    // 第二百零三轮：蕾米战屏幕层（红闪 + 暗角）
    if (world.remiliaFight) RR.drawScreen(ctx, world.remiliaFight, W, H);

    // ---- 第一百三十八轮：底部 HUD 条（纯 Canvas）----
    // 血条（底座/渐变/延迟伤害残影/护盾流光/描边数字）+ 经验条
    // （渐变光带/猫爪节点/呼吸充能/高光滑点/Lv 弹跳）。DOM 版退役。
    this._drawHudBars(ctx, player, W, H);

    // ---- 第一百八十三轮：队友离屏指示器（184 轮泛化到 P2~P4）----
    // 队友跑出 1P 视口时，屏幕边缘显示专属色徽章 + 指向箭头 + 名字，
    // 告诉 1P "AI 猫娘可能去了哪"（呼吸闪烁提醒）。一人一色不混淆。
    this._drawPartnersOffscreen(ctx, world, camera, W, H);

    // ---- 第一百八十四轮：万圣节双猫屏幕层（像素字招式名 / 底部双猫血条 / 暗角 / 黑屏换场）----
    if (world.halloweenFight) HR.drawHud(ctx, world.halloweenFight, W, H, this.dpr);
    // 第二百零三轮：蕾米战 HUD（连战进度条 + 剩余秒数 + 符卡名）
    // 205 轮：cut-in 已删（那张 eff07 会被认成芙兰战的空气墙标记）
    if (world.remiliaFight) RR.drawHud(ctx, world.remiliaFight, W, H);

    // ---- 第一百八十轮：震动 translate 的配对 restore ----
    if (this._cometShakeRestore) ctx.restore();
  }

  /** 队友专属色（184 轮：P2 粉 / P3 金 / P4 青 —— 用户认可"一个角色一个颜色"） */
  static PARTNER_COLORS = ['#ff9ec7', '#ffd166', '#4cc9f0'];

  /** P2~P4 离屏指示器（小地图同款配色：白心 + 各自专属色） */
  _drawPartnersOffscreen(ctx, world, camera, W, H) {
    const partners = world?.partners?.() ?? [];
    for (let i = 0; i < partners.length; i++) {
      const pl = partners[i];
      if (!pl || pl.dead) continue;
      const slot = (pl === world.player2) ? 0 : (pl === world.player3) ? 1 : 2;
      const color = Renderer.PARTNER_COLORS[slot] ?? '#ff9ec7';
      this._drawOneOffscreen(ctx, pl, camera, W, H, color, `P${slot + 2}`, '#ffd9ea');
    }
  }

  _drawOneOffscreen(ctx, pl, camera, W, H, color, tag, nameColor) {
    const margin = 58;                     // 贴边距离
    const bottomSafe = H - 74;             // 底部 HUD 条让位
    const sx = pl.x - camera.cx + W / 2;   // 队友的屏幕坐标
    const sy = pl.y - camera.cy + H / 2;
    if (sx >= margin && sx <= W - margin && sy >= margin && sy <= bottomSafe) return;

    const cx = Math.min(W - margin, Math.max(margin, sx));
    const cy = Math.min(bottomSafe, Math.max(margin, sy));
    const ang = Math.atan2(sy - cy, sx - cx);   // 从徽章指向真实方位

    const blink = 0.72 + 0.28 * Math.sin(performance.now() / 260);
    ctx.save();
    ctx.globalAlpha = blink;
    // 指向箭头（三角，从徽章边缘指向队友真实方向）
    const ar = 24;
    const tipX = cx + Math.cos(ang) * ar;
    const tipY = cy + Math.sin(ang) * ar;
    const bx = cx + Math.cos(ang) * (ar - 13);
    const by = cy + Math.sin(ang) * (ar - 13);
    const px = -Math.sin(ang) * 7;
    const py = Math.cos(ang) * 7;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(tipX, tipY);
    ctx.lineTo(bx + px, by + py);
    ctx.lineTo(bx - px, by - py);
    ctx.closePath();
    ctx.fill();
    // 徽章圆
    ctx.beginPath();
    ctx.arc(cx, cy, 17, 0, TAU);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(20,6,14,0.8)';
    ctx.stroke();
    // 槽位字样（P2/P3/P4）
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 13px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(tag, cx, cy + 1);
    // 角色名小字
    const name = pl.def?.name ?? '';
    if (name) {
      ctx.globalAlpha = blink * 0.8;
      ctx.font = '10px "Microsoft YaHei", sans-serif';
      ctx.strokeStyle = 'rgba(0,0,0,0.85)';
      ctx.lineWidth = 3;
      ctx.strokeText(name, cx, cy + 29);
      ctx.fillStyle = nameColor;
      ctx.fillText(name, cx, cy + 29);
    }
    ctx.restore();
  }

  // =========================================================
  // 背景离屏缓存（第八轮性能优化）
  // =========================================================
  //
  // 为什么需要：
  //   原来每帧都用 `createPattern` + `fillRect(W+2tile, H+2tile)` 铺地面。
  //   实测这一项就要 **36.9ms/帧**，而 60fps 的总预算是 16.7ms ——
  //   也就是说光是画个地面就把帧预算吃掉两倍多，这就是"加了背景之后掉帧"的根因。
  //   （对比：60 个敌人的贴图渲染只要 0.1ms，所以敌人贴图不是问题。）
  //
  // 为什么能缓存：
  //   地面贴图是**静态**的，每帧变的只有相机偏移。所以可以预先把
  //   "铺满视口 + 一个贴图余量"的结果画进离屏画布，之后每帧只做一次
  //   `drawImage` 取窗口。实测 8.2ms，快 4.5 倍。
  //
  // 为什么按设备像素存：
  //   ctx 上带着 dpr 缩放，如果缓存按 CSS 像素存、再被 dpr 放大，
  //   地面会变糊。所以缓存宽高乘上 dpr，贴回去时正好 1:1，保持清晰。
  _ensureBgCache(bgImg, W, H) {
    const tile = bgImg.naturalWidth || 1024;
    const dpr = this.dpr || 1;
    const cw = Math.ceil((W + tile) * dpr);
    const ch = Math.ceil((H + tile) * dpr);
    if (cw <= 0 || ch <= 0) return null;
    // 尺寸或贴图没变就直接复用
    if (this._bgCache && this._bgCacheSrc === bgImg
        && this._bgCache.width === cw && this._bgCache.height === ch) {
      return this._bgCache;
    }
    try {
      const c = document.createElement('canvas');
      c.width = cw;
      c.height = ch;
      const x = c.getContext('2d');
      if (!x) return null;
      x.scale(dpr, dpr);
      const p = x.createPattern(bgImg, 'repeat');
      if (!p) return null;
      x.fillStyle = p;
      x.fillRect(0, 0, W + tile, H + tile);
      // 压暗层烘焙进缓存：地面只是陪衬，敌人和弹幕必须始终是最亮的。
      // 竞技场里有几十个单位，背景一旦抢眼玩家就找不到怪了。
      x.setTransform(1, 0, 0, 1, 0, 0);
      x.fillStyle = 'rgba(10,12,18,0.42)';
      x.fillRect(0, 0, cw, ch);

      this._bgCache = c;
      this._bgCacheSrc = bgImg;
      this._bgCacheTile = tile;
      return c;
    } catch {
      // 内存不足等异常：静默放弃缓存，回落到纯色地面，不影响可玩性
      this._bgCache = null;
      return null;
    }
  }

  // =========================================================
  // 受击闪白遮罩（第八轮修复"白色方块"）
  // =========================================================
  //
  // 原来的写法是直接在**主画布**上：
  //     ctx.globalCompositeOperation = 'source-atop';
  //     ctx.fillRect(-s/2, -s*0.60, s, s);
  // 但 source-atop 是"只在**已有像素**上着色"——而主画布上已经有背景地面
  // 和其他所有东西了，它们在那个矩形里全是不透明的，于是整个方块都被涂白。
  // 这就是用户看到的"宝箱怪挨打时有白色方块闪光"。
  //
  // 正确做法：在**离屏画布**上做遮罩。离屏里只有这一张贴图，
  // source-atop 就只会落在贴图自己的像素上，得到贴合轮廓的剪影。
  // 结果按 (贴图, 颜色) 缓存，所以每帧只是一次 drawImage，几乎不花钱。
  _flashCanvas(img, color) {
    if (!img || !img.naturalWidth) return null;
    let byColor = this._flashCache.get(img);
    if (!byColor) { byColor = new Map(); this._flashCache.set(img, byColor); }
    let c = byColor.get(color);
    if (c) return c;
    try {
      c = document.createElement('canvas');
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const x = c.getContext('2d');
      if (!x) return null;
      x.drawImage(img, 0, 0);
      // 这张离屏里只有贴图，所以 source-atop 正好等于"给剪影上色"
      x.globalCompositeOperation = 'source-atop';
      x.fillStyle = color;
      x.fillRect(0, 0, c.width, c.height);
      byColor.set(color, c);
      return c;
    } catch {
      return null;
    }
  }

  // =========================================================
  // 背景：网格地面 + 视差点缀
  // =========================================================
  _drawBackground(ctx, camera, W, H) {
    ctx.fillStyle = '#12141c';
    ctx.fillRect(0, 0, W, H);

    // ---- 贴图地面（第七轮；第八轮改为离屏缓存）----
    // 背景是**可选增强**：没加载到就保持原来的纯色 + 网格，观感依然完整。
    // 图案锚在世界坐标上（随相机平移），所以地面看起来是"贴在世界上"
    // 而不是"贴在屏幕上"。贴图生成时已要求无缝可平铺，repeat 不会有接缝。
    const bgImg = sprites.get('background', this.backgroundId);
    if (bgImg) {
      const cache = this._ensureBgCache(bgImg, W, H);
      if (cache) {
        const tile = this._bgCacheTile;
        const bgOx = (((-camera.cx + W / 2) % tile) + tile) % tile;
        const bgOy = (((-camera.cy + H / 2) % tile) + tile) % tile;
        // 把缓存贴到屏幕上。缓存里画的是"图案原点在世界原点"的一整块地面，
        // 所以要把它的左上角摆到图案坐标 (0,0) 此刻所在的屏幕位置：
        //     destX = bgOx - tile      （bgOx ∈ [0,tile)，所以落在 [-tile, 0)）
        // 这样屏幕像素 sx 取到的图案坐标就是 sx - (bgOx - tile)，随相机
        // 变化的速率正好是 -1，和世界网格、和所有单位完全一致。
        //
        // ⚠️ 这里**不能**写成 -bgOx。写成 -bgOx 会让图案坐标变成 sx + bgOx，
        // 速率变成 -1 + d(bgOx)/dcx = -2 —— 地面会以两倍速朝**反方向**滚，
        // 玩起来就是"人在往左走、地面使劲往右跑"的跑步机感。
        // 旧实现（translate(bgOx - tile)）就是这个式子，改造时符号抄反了。
        ctx.drawImage(cache, bgOx - tile, bgOy - tile, W + tile, H + tile);
      } else {
        // 缓存建不起来（极低内存）：退回直接铺 pattern
        const tile = bgImg.naturalWidth || 1024;
        const bgOx = (((-camera.cx + W / 2) % tile) + tile) % tile;
        const bgOy = (((-camera.cy + H / 2) % tile) + tile) % tile;
        const p = ctx.createPattern(bgImg, 'repeat');
        if (p) {
          ctx.save();
          ctx.translate(bgOx - tile, bgOy - tile);
          ctx.fillStyle = p;
          ctx.fillRect(0, 0, W + tile * 2, H + tile * 2);
          ctx.restore();
          ctx.fillStyle = 'rgba(10,12,18,0.42)';
          ctx.fillRect(0, 0, W, H);
        }
      }
    }

    const cell = 64;
    const ox = ((-camera.cx + W / 2) % cell + cell) % cell;
    const oy = ((-camera.cy + H / 2) % cell + cell) % cell;

    ctx.strokeStyle = 'rgba(255,255,255,0.035)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = ox; x < W; x += cell) {
      ctx.moveTo(Math.floor(x) + 0.5, 0);
      ctx.lineTo(Math.floor(x) + 0.5, H);
    }
    for (let y = oy; y < H; y += cell) {
      ctx.moveTo(0, Math.floor(y) + 0.5);
      ctx.lineTo(W, Math.floor(y) + 0.5);
    }
    ctx.stroke();

    // 大格线，增加纵深感
    // 第一百六十三轮：手机端跳过大格线（用户卡顿死命令的一部分 ——
    // 贴图地面本就离屏缓存，网格是仅剩的每帧矢量背景绘制，砍一半）。
    if (!MOBILE) {
    const big = cell * 4;
    const bx = ((-camera.cx + W / 2) % big + big) % big;
    const by = ((-camera.cy + H / 2) % big + big) % big;
    ctx.strokeStyle = 'rgba(255,123,172,0.055)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let x = bx; x < W; x += big) {
      ctx.moveTo(Math.floor(x) + 0.5, 0);
      ctx.lineTo(Math.floor(x) + 0.5, H);
    }
    for (let y = by; y < H; y += big) {
      ctx.moveTo(0, Math.floor(y) + 0.5);
      ctx.lineTo(W, Math.floor(y) + 0.5);
    }
    ctx.stroke();
    }

    // ---- 不可通行地形描边（第十二轮）----
    this._drawTerrain(ctx, camera, W, H);
  }

  /** 198 轮：换到带迷宫的地图时预烘焙（避免第一帧现场烘焙卡一下） */
  warmMaze(terrain) { try { MZ.warm(terrain); } catch { /* 无 DOM 环境 */ } }

  /**
   * 给不可通行地形描边（第十二轮，用户要求）。
   *
   * 用户原话："把框使用原背景的颜色略深一点描个边（不要画在地图素材里，
   * 直接放游戏里让玩家理解这是墙就可以了）"。
   *
   * 三个设计决定，对应三句原话：
   *
   *  1) **描边而不是填充**。"描个边"就是只要轮廓。而且填充会把背景图的
   *     深色方块/花坛盖住 —— 那就白画素材了。
   *
   *  2) **运行时画，不烘焙进贴图**。除了用户明确要求，还有个实际好处：
   *     描边是矢量画的，不管相机怎么缩放都清晰；烘进 JPEG 会糊，
   *     而且以后换背景图就得重画一遍。
   *
   *  3) **"原背景的颜色略深一点"**。这里不能直接取样背景图那个像素 ——
   *     取样会拿到"深色方块本身的颜色"，在深色方块上画深色描边等于没画。
   *     用户的意思应该是"比**地面**深一点"，所以用一个固定的深色 +
   *     一点暖色调，和游戏整体的粉紫主题协调，而且在深色方块和
   *     花园花坛上都看得清。
   *
   * 描边用**世界坐标 → 屏幕坐标**换算，所以它跟着相机走，像画在地上。
   */
  _drawTerrain(ctx, camera, W, H) {
    const t = this.world?.terrain;
    if (!t || (t.rects.length === 0 && t.circles.length === 0)) return;

    const toSX = (wx) => wx - camera.cx + W / 2;
    const toSY = (wy) => wy - camera.cy + H / 2;

    // 198 轮：迷宫广场 + 冰墙先画（盖住贴图里烘焙的池塘/松树），其上再画常规障碍描边
    if (t.maze) MZ.drawMaze(ctx, camera, W, H, t, performance.now() / 1000);

    ctx.save();
    // 只画看得见的那部分，屏幕外的直接跳过（障碍物可能有很多重复实例）
    const inView = (x0, y0, x1, y1) => x1 >= 0 && x0 <= W && y1 >= 0 && y0 <= H;

    ctx.lineWidth = 3;
    ctx.strokeStyle = this._terrainStroke ?? 'rgba(38,28,48,0.85)';

    // 矩形（实验室的深色方块）
    ctx.beginPath();
    for (const r of t.rects) {
      if (r.maze) continue;   // 198 轮：迷宫墙自带并集外框（见 maze-fx.js），不再逐段描边（会在拐角处画出内部接缝）
      const x0 = toSX(r.x0), y0 = toSY(r.y0);
      const x1 = toSX(r.x1), y1 = toSY(r.y1);
      if (!inView(x0, y0, x1, y1)) continue;
      ctx.rect(Math.round(x0) + 0.5, Math.round(y0) + 0.5,
        Math.round(x1 - x0), Math.round(y1 - y0));
    }
    ctx.stroke();

    // 圆形（花园的花坛）
    if (t.circles.length) {
      ctx.beginPath();
      for (const c of t.circles) {
        const cx = toSX(c.cx), cy = toSY(c.cy);
        if (!inView(cx - c.r, cy - c.r, cx + c.r, cy + c.r)) continue;
        ctx.moveTo(cx + c.r, cy);
        ctx.arc(cx, cy, c.r, 0, Math.PI * 2);
      }
      ctx.stroke();
    }

    // 内侧再压一道更浅的线，做出"路缘"的厚度感。
    // 单线看起来像画上去的记号，双线才像实体的边。
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = this._terrainStrokeInner ?? 'rgba(255,255,255,0.10)';
    ctx.beginPath();
    for (const r of t.rects) {
      if (r.maze) continue;   // 198 轮：迷宫墙自带并集外框（见 maze-fx.js），不再逐段描边（会在拐角处画出内部接缝）
      const x0 = toSX(r.x0), y0 = toSY(r.y0);
      const x1 = toSX(r.x1), y1 = toSY(r.y1);
      if (!inView(x0, y0, x1, y1)) continue;
      ctx.rect(Math.round(x0) + 3.5, Math.round(y0) + 3.5,
        Math.round(x1 - x0) - 6, Math.round(y1 - y0) - 6);
    }
    for (const c of t.circles) {
      const cx = toSX(c.cx), cy = toSY(c.cy);
      if (!inView(cx - c.r, cy - c.r, cx + c.r, cy + c.r)) continue;
      ctx.moveTo(cx + c.r - 3, cy);
      ctx.arc(cx, cy, Math.max(0, c.r - 3), 0, Math.PI * 2);
    }
    ctx.stroke();
    ctx.restore();
  }

  /** 第一百七十二轮：刷怪预告 —— 闪烁收缩的红色警戒圈（1 秒后落地成怪） */
  _drawSpawnWarnings(ctx, world) {
    const now = performance.now() / 1000;
    for (const w of world.spawnWarnings) {
      // 进度 0→1（t 从满秒数倒数到 0）
      const total = w.elite ? 1.4 : 1.0;
      const p = Math.min(1, Math.max(0, 1 - w.t / total));
      // 闪烁：频率随进度加快（越接近落地闪得越急），alpha 抖动
      const blink = 0.5 + 0.5 * Math.abs(Math.sin(now * (6 + p * 10)));
      const alpha = 0.30 + 0.45 * blink;
      // 收缩：外圈从 46px 收到 20px（精英 58→26），像"怪要从这里钻出来"
      const r = (w.elite ? 58 : 46) * (1 - p * 0.55);
      const color = w.elite ? '#ffb84d' : '#ff5d5d';
      ctx.save();
      ctx.globalAlpha = alpha;
      // 外圈
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(w.x, w.y, r, 0, Math.PI * 2);
      ctx.stroke();
      // 内虚线圈（旋转感）：给"危险区"的读感
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(w.x, w.y, r * 0.62, now * 2, now * 2 + Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      // 中心点
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(w.x, w.y, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  _drawWorldBounds(ctx, world) {
    const h = world.halfSize;
    ctx.strokeStyle = 'rgba(255,123,172,0.35)';
    ctx.lineWidth = 3;
    ctx.setLineDash([14, 10]);
    ctx.strokeRect(-h, -h, h * 2, h * 2);
    ctx.setLineDash([]);

    // 边界外的暗角
    ctx.fillStyle = 'rgba(8,9,13,0.72)';
    const OUT = 4000;
    ctx.fillRect(-h - OUT, -h - OUT, OUT, h * 2 + OUT * 2);
    ctx.fillRect(h, -h - OUT, OUT, h * 2 + OUT * 2);
    ctx.fillRect(-h, -h - OUT, h * 2, OUT);
    ctx.fillRect(-h, h, h * 2, OUT);
  }

  // =========================================================
  // 玩家：猫娘占位造型
  // =========================================================
  /** 第一百六十轮：四重存在分身 —— 粉紫半透明光影小猫（性能友好，零贴图采样） */
  _drawPhantom(ctx, ph) {
    // 202 轮（用户："莱万汀召唤的3个玩家分身是紫色矩形小猫，让她们变成玩家的样子"）：
    // 分身 = 玩家本体的外观。直接复用 _drawPlayer（立绘 / 走路摆动 / 朝向翻转 / 眨眼 / 轨道武器都一样），
    // PhantomClone 已经补齐了它读的 15 个字段。为了不和本体混淆，只加两样最轻的标记：
    // 脚下一圈粉色细环 + 头顶小血条（原来的"紫色矩形小猫"身体画法整体退役）。
    const r = ph.radius;
    const a = ph.spawnT > 0 ? clamp(1 - ph.spawnT / 0.45, 0, 1) : 1;
    ctx.save();
    ctx.globalAlpha = 0.55 * a;
    ctx.strokeStyle = '#ff7bac';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.ellipse(ph.x, ph.y + r * 0.85, r * 1.15, r * 0.46, 0, 0, TAU);
    ctx.stroke();
    ctx.restore();

    this._drawPlayer(ctx, ph);

    ctx.save();
    const w = r * 2.2;
    const ratio = clamp(ph.hp / ph.maxHp, 0, 1);
    const top = ph.y - r * 2.6;
    ctx.globalAlpha = 0.9 * a;
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillRect(ph.x - w / 2, top, w, 4);
    ctx.fillStyle = '#ff7bac';
    ctx.fillRect(ph.x - w / 2, top, w * ratio, 4);
    ctx.restore();
  }

  /**
   * 第一百七十一轮：《艾尔登法环·黑夜君临》式倒地救援环。
   * · 外圈 3 段底环（段间留缺口），亮起的段数 = 第几次倒地
   * · 内圈紫色发光进度条：倒地即满（1倒=1/3圈、3倒=整圈），
   *   队友命中一刀缩一点，缩完（0%）立刻 30% 血复活
   * · 3 秒没被队友打 → 紫条回涨（次数越多涨越快，见 player.rescueRegain）
   */
  _drawRescueRing(ctx, p) {
    const r = p.radius + 14;
    const segs = Math.min(3, Math.max(1, p.downCount));
    const gap = 0.18;                       // 段间缺口（弧度）
    const arcEach = (Math.PI * 2) / 3;      // 三等分基准（固定 3 段布局）
    const start = -Math.PI / 2;             // 从头顶 12 点方向起画
    const full = Math.PI * 2;
    ctx.save();
    ctx.lineCap = 'round';
    // ---- 黑色描边底层（粗一圈，把整个环从背景里顶出来）----
    ctx.strokeStyle = 'rgba(0,0,0,0.8)';
    ctx.lineWidth = 9;
    for (let i = 0; i < segs; i++) {
      const a0 = start + i * arcEach + gap / 2;
      const a1 = start + (i + 1) * arcEach - gap / 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, a0, a1);
      ctx.stroke();
    }
    // ---- 段底（深紫灰，画满本次倒地的段数）----
    ctx.strokeStyle = 'rgba(84,70,140,0.6)';
    ctx.lineWidth = 6;
    for (let i = 0; i < segs; i++) {
      const a0 = start + i * arcEach + gap / 2;
      const a1 = start + (i + 1) * arcEach - gap / 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, a0, a1);
      ctx.stroke();
    }
    // ---- 紫色发光进度（剩余量；从 12 点顺时针，长度 = 段数比例 × 剩余）----
    const ratio = clamp((p.rescueProgress ?? 0) / Math.max(1, p.rescueNeed), 0, 1);
    if (ratio > 0.005) {
      const len = full * (segs / 3) * ratio;
      ctx.shadowColor = '#9a5cff';
      ctx.shadowBlur = 14;
      ctx.strokeStyle = '#b98cff';
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, start, start + len);
      ctx.stroke();
      // 内侧再补一条细亮线，做出"能量条"层次
      ctx.shadowBlur = 6;
      ctx.strokeStyle = '#e6d9ff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r - 4, start, start + len);
      ctx.stroke();
      ctx.shadowBlur = 0;
    }
    ctx.restore();
  }

  /** 第一百七十轮（2P）→ 184 轮（1~4P）：队友头顶角标 —— 独立血条 +
   *  槽位标签（P2/P3/P4，各自专属色）。color/tag 缺省 = 旧 P2 蓝样式。 */
  _drawP2Badge(ctx, p2, color = '#9ec7ff', tag = 'P2') {
    const y = p2.y - p2.radius - 22;
    const w = 46, h = 5;
    ctx.save();
    ctx.textAlign = 'center';
    // 血条底
    ctx.fillStyle = 'rgba(0,0,0,0.45)';
    ctx.fillRect(p2.x - w / 2 - 1, y - 1, w + 2, h + 2);
    const ratio = clamp(p2.hp / Math.max(1, p2.maxHp), 0, 1);
    ctx.fillStyle = ratio < 0.3 ? '#ff5f6d' : '#6ee7a8';
    ctx.fillRect(p2.x - w / 2, y, w * ratio, h);
    // 槽位角标
    ctx.font = 'bold 11px system-ui';
    ctx.fillStyle = color;
    ctx.fillText(tag, p2.x, y - 5);
    ctx.restore();
  }

  _drawPlayer(ctx, p) {
    const bob = p.isMoving ? Math.sin(p.walkPhase) * 2 : Math.sin(p.age * 2) * 1.2;
    const r = p.radius;

    ctx.save();
    ctx.translate(p.x, p.y + bob);

    // 影子
    ctx.fillStyle = 'rgba(0,0,0,0.32)';
    ctx.beginPath();
    ctx.ellipse(0, r * 0.85 - bob, r * 0.92, r * 0.36, 0, 0, TAU);
    ctx.fill();

    // 无敌闪烁
    const flashing = p.invuln > 0 && Math.floor(p.invuln * 20) % 2 === 0;
    ctx.globalAlpha = flashing ? 0.42 : 1;

    // ---- 护盾光环（第十二轮）----
    //
    // 画成一个**青色半透明圆环**，而不是 HUD 上多一条血条。
    // 理由：护盾是贴着角色存在的状态，玩家需要知道"现在这层还在不在"。
    // 放在头顶血条上的话，眼睛得离开角色去看 UI —— 而躲弹幕的时候
    // 眼睛根本不可能离开角色。
    //
    // 透明度随"还剩多少盾"变化，快碎的时候会闪 —— 玩家能感觉到
    // "盾要没了该跑了"，不用去读数字。
    if (p.shield > 0) {
      const ratio = p.maxShield > 0 ? clamp(p.shield / p.maxShield, 0, 1) : 1;
      const pulse = 0.5 + Math.sin(p.age * 6) * 0.5;
      // 低血量护盾时用闪烁提醒（和"残血红边"同一个设计语言）
      const low = ratio < 0.3 ? pulse : 1;
      ctx.save();
      ctx.globalAlpha = (0.35 + ratio * 0.45) * low;
      ctx.strokeStyle = '#6ee7ff';
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(0, 0, r * 1.75, 0, TAU);
      ctx.stroke();
      // 内层更淡的一圈，做出"能量护罩"的厚度感
      ctx.globalAlpha = (0.14 + ratio * 0.2) * low;
      ctx.strokeStyle = '#bff3ff';
      ctx.lineWidth = 6;
      ctx.beginPath();
      ctx.arc(0, 0, r * 1.75, 0, TAU);
      ctx.stroke();
      ctx.restore();
    }

    // ---- 贴图优先，几何小猫兜底（第十一轮）----
    //
    // 和宠物/敌人一样的策略：贴图是**可选增强**，加载不到就回落到
    // 原来那套程序画的几何猫。这样加角色立绘不会成为一个"必须成功"的
    // 依赖，美术没跟上时游戏照常能玩。
    const charSprite = sprites.get('character', p.characterId);
    if (charSprite) {
      // 立绘是**全身站立**的，锚点定在"脚底"而不是中心 ——
      // 否则角色会像浮在半空。所以往上偏移一个半高。
      const drawH = r * 3.6;
      const drawW = drawH * (charSprite.width / charSprite.height);
      // 朝向：和宠物同一套（左移时水平翻转）
      const flip = Math.cos(p.facing) < -0.15;
      ctx.save();
      if (flip) ctx.scale(-1, 1);
      // 走路时轻微上下摆动，别让立绘像贴纸一样僵着
      const step = p.isMoving ? Math.sin(p.walkPhase) * r * 0.10 : 0;
      ctx.drawImage(charSprite,
        -drawW / 2, -drawH + r * 0.85 + step, drawW, drawH);
      ctx.restore();
    } else {
    // 尾巴（跟随移动摆动）
    const tailSwing = Math.sin(p.age * 4 + (p.isMoving ? p.walkPhase : 0)) * 0.35;
    ctx.strokeStyle = p.color;
    ctx.lineWidth = 4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(-r * 0.7, r * 0.3);
    ctx.quadraticCurveTo(
      -r * 1.7, r * 0.2 + Math.sin(tailSwing) * 6,
      -r * 1.9, -r * 0.7 + Math.sin(tailSwing) * 10,
    );
    ctx.stroke();

    // 身体
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.fill();

    // 肚子高光
    ctx.fillStyle = p.accent;
    ctx.beginPath();
    ctx.ellipse(0, r * 0.22, r * 0.6, r * 0.5, 0, 0, TAU);
    ctx.fill();

    // 猫耳
    ctx.fillStyle = p.color;
    for (const s of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(s * r * 0.30, -r * 0.78);
      ctx.lineTo(s * r * 0.72, -r * 1.62);
      ctx.lineTo(s * r * 0.92, -r * 0.62);
      ctx.closePath();
      ctx.fill();
      // 耳内
      ctx.fillStyle = p.accent;
      ctx.beginPath();
      ctx.moveTo(s * r * 0.42, -r * 0.82);
      ctx.lineTo(s * r * 0.68, -r * 1.36);
      ctx.lineTo(s * r * 0.78, -r * 0.72);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = p.color;
    }

    // 眼睛（朝移动方向看）
    const lookX = Math.cos(p.facing) * r * 0.16;
    const lookY = Math.sin(p.facing) * r * 0.16;
    // 眨眼：把眼睛的纵向半径压扁。blinkPhase 从 0.12 递减到 0，
    // 用 sin 曲线让"闭合 → 张开"是平滑的，而不是硬切（硬切像掉帧）。
    const blinkT = p.blinkPhase > 0 ? p.blinkPhase / 0.12 : 0;
    const eyeSquash = Math.sin(Math.min(1, blinkT) * Math.PI);
    const eyeRy = r * 0.19 * (1 - eyeSquash);
    if (eyeRy > 0.6) {
      ctx.fillStyle = '#1a1d29';
      for (const s of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(s * r * 0.30 + lookX * 0.5, -r * 0.10 + lookY * 0.5,
          r * 0.15, eyeRy, 0, 0, TAU);
        ctx.fill();
      }
      // 眼里的高光（快闭上时淡出，避免出现"只有高光没有眼珠"的怪相）
      if (eyeSquash < 0.7) {
        ctx.fillStyle = `rgba(255,255,255,${0.85 * (1 - eyeSquash)})`;
        for (const s of [-1, 1]) {
          ctx.beginPath();
          ctx.arc(s * r * 0.30 + lookX * 0.5 + 1.5, -r * 0.16 + lookY * 0.5,
            r * 0.055, 0, TAU);
          ctx.fill();
        }
      }
    } else {
      // 完全闭上：画一条弯弯的闭眼线，比"什么都不画"可爱得多
      ctx.strokeStyle = '#1a1d29';
      ctx.lineWidth = Math.max(1.2, r * 0.055);
      ctx.lineCap = 'round';
      ctx.beginPath();
      for (const s of [-1, 1]) {
        const cx = s * r * 0.30 + lookX * 0.5;
        const cy = -r * 0.10 + lookY * 0.5;
        ctx.moveTo(cx - r * 0.15, cy);
        ctx.quadraticCurveTo(cx, cy + r * 0.09, cx + r * 0.15, cy);
      }
      ctx.stroke();
    }

    // 腮红
    ctx.fillStyle = 'rgba(255,123,172,0.42)';
    for (const s of [-1, 1]) {
      ctx.beginPath();
      ctx.ellipse(s * r * 0.62, r * 0.14, r * 0.2, r * 0.13, 0, 0, TAU);
      ctx.fill();
    }
    }   // ← 结束「无贴图时走几何小猫」的 else

    // 受伤闪白（贴图和几何两条路径都要闪，所以放在 else 外面）
    // 第一百六十七轮：原来这里是红色大圆（1.12×半径），用户观感反馈
    // "看起来像是 hitbox"。改成半透明白闪 —— 只提示"挨打了"，
    // 不再像身上套了个圈。
    if (p.hurtFlash > 0) {
      ctx.globalAlpha = clamp(p.hurtFlash / 0.25, 0, 1) * 0.55;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(0, 0, r * 1.05, 0, TAU);
      ctx.fill();
    }

    ctx.globalAlpha = 1;
    ctx.restore();

    // 环绕武器
    this._drawOrbitWeapons(ctx, p);
  }

  _drawOrbitWeapons(ctx, p) {
    // 第三十九轮：环绕球是玩家攻击的一部分，吃"玩家特效"透明度
    ctx.save();
    ctx.globalAlpha = fxSettings.playerAlpha;
    for (const w of p.weapons) {
      if (w.def.behavior !== 'orbit' || !w.orbitPositions) continue;
      // 第三十三轮：视觉球随 orbitBallRadius 同比例放大（默认 16 → 9px）
      const br = (w.def.orbitBallRadius ?? 16) * 0.5625;
      for (let i = 0; i < w.orbitPositions.length; i++) {
        const pos = w.orbitPositions[i];
        // ---- 第一百三十九轮 C：连线（玩家 → 球的弯曲绳线）----
        // 用户："画一条半透明的、轻微弯曲的动态曲线，让它看起来是
        // '连着玩家在甩'，而不是凭空绕着玩家飞。"
        ctx.save();
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        ctx.strokeStyle = 'rgba(255,210,58,0.42)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        const mx = (p.x + pos.x) / 2, my = (p.y + pos.y) / 2;
        // 控制点垂直偏移随时间摆动 → 绳子的"甩动感"
        const dx = pos.x - p.x, dy = pos.y - p.y;
        const len = Math.hypot(dx, dy) || 1;
        const sway = Math.sin(p.age * 9 + i * 2.4) * len * 0.12;
        ctx.quadraticCurveTo(mx - (dy / len) * sway, my + (dx / len) * sway, pos.x, pos.y);
        ctx.stroke();
        ctx.restore();

        // ---- 拖尾残影：4 段渐隐黄影（高速旋转的速度感）----
        const trail = w._trail?.[i] ?? [];
        for (let j = 0; j < trail.length; j++) {
          const tp = trail[j];
          const f = (j + 1) / (trail.length + 1);
          ctx.save();
          ctx.globalAlpha = fxSettings.playerAlpha * f * 0.3;
          ctx.fillStyle = w.def.color;
          ctx.beginPath();
          ctx.arc(tp.x, tp.y, br * (0.45 + f * 0.5), 0, TAU);
          ctx.fill();
          ctx.restore();
        }

        // ---- 环形拖尾（F 轮，用户："加一条半透明的黄色环形拖尾，
        // 像彗星尾巴，体现甩起来的离心力"）—— 球贴着轨道圆周跑，
        // 拖尾直接沿轨道往回扫一段弧，分 6 段渐细渐淡，比散点更"环"。
        if (w.orbitRadius) {
          const ang = Math.atan2(pos.y - p.y, pos.x - p.x);
          const SEGS = 6, SWEEP = 1.05;
          ctx.save();
          ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
          ctx.strokeStyle = w.def.color;
          ctx.lineCap = 'round';
          for (let sIdx = 0; sIdx < SEGS; sIdx++) {
            const f = 1 - sIdx / SEGS;             // 头亮尾淡
            const a0 = ang - (sIdx * SWEEP) / SEGS;
            const a1 = ang - ((sIdx + 1) * SWEEP) / SEGS;
            ctx.globalAlpha = f * f * 0.30;
            ctx.lineWidth = br * 1.35 * f + 0.4;
            ctx.beginPath();
            ctx.arc(p.x, p.y, w.orbitRadius, a1, a0);
            ctx.stroke();
          }
          ctx.restore();
        }

        // ---- 毛线球本体：底色球 + 交错缠绕暗黄线 + 高光 ----
        ctx.save();
        ctx.translate(pos.x, pos.y);
        ctx.fillStyle = w.def.color;
        ctx.beginPath();
        ctx.arc(0, 0, br, 0, TAU);
        ctx.fill();
        // 毛线缠绕：三条交错弧线（各随年龄差速旋转 → 线团在"滚"）
        ctx.strokeStyle = 'rgba(150,102,16,0.75)';
        ctx.lineWidth = Math.max(1, br * 0.14);
        ctx.lineCap = 'round';
        const spinA = p.age * 6;
        for (let s = 0; s < 3; s++) {
          const ra = spinA + s * 2.1;
          ctx.beginPath();
          ctx.ellipse(0, 0, br * 0.72, br * (0.26 + s * 0.14), ra, 0.3, Math.PI - 0.3);
          ctx.stroke();
        }
        // 白高光（左上）
        ctx.fillStyle = 'rgba(255,255,255,0.8)';
        ctx.beginPath();
        ctx.arc(-br * 0.32, -br * 0.34, br * 0.22, 0, TAU);
        ctx.fill();
        ctx.restore();
      }
    }
    ctx.restore();
  }

  /**
   * 第三十九轮："玩家特效"透明度的实现载体。
   *
   * 用户原话："增加个暂停菜单的设置选项，让玩家可拖动"玩家特效"条来
   * 调整自己攻击的透明度。从0到100%自由调整。不然噩梦难度的无限模式
   * 敌人弹幕太密集了，玩家攻击速度也很快，导致看不清敌人弹幕……"
   *
   * 为什么用 Proxy 包一层 ctx：特效绘制代码里到处都是
   * `ctx.globalAlpha = <绝对值>`（fade 曲线、LOD 分层……），
   * 在外面设一次透明度会被它们覆盖。代理把**所有** globalAlpha 赋值
   * 按比例缩放，一条设置覆盖全部特效路径，不用逐个改绘制代码。
   * 100%（默认）时直接原样返回，零开销。
   */
  _scaledCtx(ctx) {
    const a = fxSettings.playerAlpha;
    if (a >= 1) return ctx;
    return new Proxy(ctx, {
      set(t, k, v) {
        if (k === 'globalAlpha') v *= a;
        t[k] = v;
        return true;
      },
      get(t, k) {
        const v = t[k];
        return typeof v === 'function' ? v.bind(t) : v;
      },
    });
  }

  // =========================================================
  // 敌人
  // =========================================================
  _drawEnemy(ctx, e) {
    // 第六十三轮：四重存在期间中央本体隐身（用户："把中间的大芙兰设置
    // 隐身不可被击中"）—— 场上只显示 4 个正常大小的分身。
    if (e.flandreHidden) return;
    const r = e.radius * (e.scale ?? 1);

    ctx.save();
    ctx.translate(e.x, e.y);

    // 第十五轮 ④：`facePlayer` —— 让 Boss 的"头"始终朝着玩家。
    //
    // 用户原话："然后让这个虾boss的头部始终正对玩家"。
    //
    // 这里刻意分成两种做法，因为**有贴图**和**没贴图**的几何前提不同：
    //
    //  · 有贴图（用户给的立绘）：用**水平镜像**，不用旋转。
    //    立绘是"竖构图"的（虾那张内容框 160x236，明显高于宽），
    //    整张按角度旋转会变成斜躺着的盘子，非常怪。
    //    镜像则是 2D 游戏里表达"朝向"的通用做法，任何构图都成立。
    //    玩家在左半屏 → 贴图翻到朝左，反之朝右。
    //
    //  · 没贴图（下面 shrimp 那段程序画的几何虾）：用**真旋转**。
    //    因为那个形状是沿 +x 画的（头在 +x），旋转到 e.facing
    //    就精确地"头指向玩家"，几何上严格成立。
    //
    // 影子画在旋转**之前**（第二十轮挪到这里）：影子属于"地面"，
    // 不该跟着身体朝向转。以前只有镜像（左右对称，看不出）和几何 Boss 的
    // 旋转，问题不明显；虾改成整图旋转后，影子跟着转会变成斜的椭圆。
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    ctx.beginPath();
    ctx.ellipse(0, r * 0.8, r * 0.85, r * 0.3, 0, 0, TAU);
    ctx.fill();

    // e.facing 由 enemy.js 每帧朝玩家更新，所以两种做法共用同一个角度。
    //
    // 第二十轮：`faceRotate` —— 用户："蒜蓉粉丝开背虾boss没有做到图像的
    // 头部一直对着玩家，它的图片上面顶端中心就是头部，让那个点始终对着
    // 玩家。也就是图片旋转效果。" 虾的立绘头在**顶端中心**，水平镜像
    // 表达不了"头在正上方"的构图 → 整张旋转。几何：贴图不转时"上"是
    // -y 方向，要让 -y 指向 facing，转 (facing + 90°)。
    // 没贴图时仍走普通 rotate(e.facing)（几何虾的头画在 +x）。
    const facePlayer = !!e.def?.facePlayer;
    const faceRotate = !!e.def?.faceRotate;
    const hasSprite = !!sprites.get('enemy', e.spriteId || e.def?.id);
    if (facePlayer && faceRotate && hasSprite) {
      ctx.rotate(e.facing + Math.PI / 2);
    } else if (facePlayer && hasSprite) {
      // cos(facing) < 0 表示玩家在左边
      if (Math.cos(e.facing) < 0) ctx.scale(-1, 1);
    } else if (facePlayer) {
      ctx.rotate(e.facing);
    }

    // 蓄力预警
    if (e.behavior === 'charger' && e.state === 'windup') {
      const pulse = 0.5 + Math.sin(e.age * 30) * 0.5;
      ctx.strokeStyle = `rgba(255,45,85,${0.4 + pulse * 0.5})`;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(0, 0, r + 8 + pulse * 4, 0, TAU);
      ctx.stroke();
    }

    const col = e.hurtFlash > 0 ? '#ffffff' : (e.mutant ? '#9c2231' : e.color);
    // 第一百五十三轮：变异体红色轮廓光 —— 用户："给变异小怪的边缘加一圈
    // 红色的发光描边（ctx.shadowColor = '#ff0000'; ctx.shadowBlur = MOBILE ? 0 :10;），
    // 像大树守卫那样浑身透着红光"。在形状/贴图绘制前挂 shadow，画完清掉。
    if (e.mutant) {
      ctx.shadowColor = '#ff0000';
      ctx.shadowBlur = MOBILE ? 0 :10;
    }

    // ---- 第一百三十一轮：过载减伤触发闪光 ----
    // 每触发一层"过载减伤"（0.5s 掉血 ≥10% maxHp）闪一次金红护盾光圈。
    // 有层期间留一圈淡金呼吸底光 —— 用户："要显示当前减伤……让玩家知道
    // 「现在你的输出被限制了，但这正是你专心躲弹幕的 5 秒钟」"。
    if (e.overload && (e.overloadFlash > 0 || e.overload.layersCount > 0)) {
      const has = e.overload.layersCount > 0;
      const breath = has ? 0.5 + Math.sin(e.age * 5) * 0.5 : 0;
      const flash = Math.max(0, e.overloadFlash / 0.35);
      const glowR = r + 6 + flash * 10 + breath * 3;
      const g3 = ctx.createRadialGradient(0, 0, r * 0.5, 0, 0, glowR);
      g3.addColorStop(0, 'rgba(255,200,87,0)');
      g3.addColorStop(0.75, `rgba(255,200,87,${0.06 + breath * 0.05})`);
      g3.addColorStop(1, `rgba(255,120,60,${0.18 + flash * 0.5 + breath * 0.08})`);
      ctx.fillStyle = g3;
      ctx.beginPath();
      ctx.arc(0, 0, glowR, 0, TAU);
      ctx.fill();
      // 触发瞬间的一道亮环（0.35s 内从大缩到无）
      if (flash > 0) {
        ctx.strokeStyle = `rgba(255,214,120,${0.85 * flash})`;
        ctx.lineWidth = 3 * flash + 1;
        ctx.beginPath();
        ctx.arc(0, 0, r + 4 + (1 - flash) * 16, 0, TAU);
        ctx.stroke();
      }
    }

    // 宝箱怪 / 宝箱：加一圈脉动光效，方便玩家在地图上看到（用户要求）
    if (e.def?.glow || e.isChest) {
      const pulse = 0.5 + Math.sin((e.glowPhase ?? e.age * 2)) * 0.5;
      const gr = r + 10 + pulse * 8;
      const g = ctx.createRadialGradient(0, 0, r * 0.4, 0, 0, gr);
      g.addColorStop(0, hexA(e.color, 0.45 + pulse * 0.25));
      g.addColorStop(1, hexA(e.color, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(0, 0, gr, 0, TAU);
      ctx.fill();
    }

    // ---- Boss 锁血护盾（第九轮）----
    // 登场 5 秒 / 转阶段 5 秒 / 卡点 5 秒内 Boss 免疫伤害。
    // 必须有明确的视觉信号，否则玩家会以为"伤害打不出去"是 bug。
    // 画法：一圈脉动的青色护盾环 + 半透明填充，脉动频率随剩余时间加快。
    if (e.bossPhase && e.bossPhase.invulnerable) {
      const left = e.bossPhase.invulnLeft;
      const pulse = 0.5 + Math.sin(e.age * (6 + 14 * (1 - Math.min(1, left / 5)))) * 0.5;
      const shieldR = r + 6 + pulse * 5;
      // 填充
      const g2 = ctx.createRadialGradient(0, 0, r * 0.5, 0, 0, shieldR);
      g2.addColorStop(0, 'rgba(143,217,240,0)');
      g2.addColorStop(0.72, `rgba(143,217,240,${0.10 + pulse * 0.10})`);
      g2.addColorStop(1, `rgba(143,217,240,${0.34 + pulse * 0.22})`);
      ctx.fillStyle = g2;
      ctx.beginPath();
      ctx.arc(0, 0, shieldR, 0, TAU);
      ctx.fill();
      // 环
      ctx.strokeStyle = `rgba(180,240,255,${0.55 + pulse * 0.35})`;
      ctx.lineWidth = 2.5 + pulse * 1.5;
      ctx.beginPath();
      ctx.arc(0, 0, shieldR, 0, TAU);
      ctx.stroke();
      // 六边形"护盾格"纹理，和普通圆形血条区分开
      ctx.strokeStyle = `rgba(143,217,240,${0.25 + pulse * 0.2})`;
      ctx.lineWidth = 1.5;
      for (let i = 0; i < 6; i++) {
        const a2 = e.age * 0.8 + i * TAU / 6;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a2) * r * 0.75, Math.sin(a2) * r * 0.75);
        ctx.lineTo(Math.cos(a2) * shieldR, Math.sin(a2) * shieldR);
        ctx.stroke();
      }
    }

    // 按形状绘制
    ctx.fillStyle = col;
    ctx.strokeStyle = e.elite || e.boss ? '#ffffff' : 'rgba(0,0,0,0.35)';
    ctx.lineWidth = e.boss ? 4 : e.elite ? 3 : 2;

    // ---- 贴图优先（第七轮）----
    // 有敌人贴图就画贴图，没有就**原样**走下面的几何形状。
    // 贴图里**不含眼睛**（生成时就要求无脸），眼睛仍由下面那段 canvas 代码
    // 每帧按 e.facing 画，所以"眼睛会动"的设计完全保留。
    // 受击闪白：在贴图上叠一层半透明白，而不是像几何形状那样换 fillStyle。
    // 第十九轮：地图宝箱（isChest）不再蹭 enemy 贴图通道 —— 按奖励/品阶选
    // 专属贴图：chest_heal（奖励是医疗包）/ chest_2（华丽）/ chest_1（普通）。
    // 宝箱怪（treasure_mimic）是真敌人，仍走 enemy 贴图，不受影响。
    // 贴图优先（第七轮）—— 第一百五十三轮：**变异体换暗红染色版贴图**：
    // multiply 保住原立绘的明暗细节、只把色相压成干涸血液的暗红，
    // 再叠一层半透明血红（source-atop）加强"浸血"观感 —— 两步都做在
    // 离屏画布上并按贴图缓存（_mutantCanvas），主画布零污染。
    const enemyImg = e.isChest
      ? sprites.get('chest',
          e.reward?.kind === 'heal' ? 'chest_heal' : (e.tier === 2 ? 'chest_2' : 'chest_1'))
      // 第四十九轮：芙兰朵露 Extra Phantom 要切"抱腿坐姿"贴图 ——
      // 实例上允许 spriteId 覆盖 def.id，其它敌人不受影响。
      : sprites.get('enemy', e.spriteId || e.def?.id);
    const drawImg = e.mutant && enemyImg && !e.isChest
      ? this._mutantCanvas(enemyImg) : enemyImg;
    if (drawImg) {
      // 受击时轻微"弹一下"，让打击感更明显（对宝箱怪尤其重要，
      // 因为它不还手、只是逃，需要更强的受击反馈）
      const flash = e.hurtFlash > 0 ? Math.min(1, e.hurtFlash * 3) : 0;
      const pop = 1 + flash * 0.06;
      // 贴图含耳朵，整体比碰撞体高，所以画得比 r 大一圈并上移，让球体对齐碰撞圆。
      // 宝箱贴图不是正方形：按长边贴合、以碰撞圆为中心画（r=17 → 约 44px 的箱子）。
      const s = r * 2.6 * pop;
      let dx; let dy; let dw; let dh;
      if (e.isChest) {
        const k = s / Math.max(drawImg.naturalWidth, drawImg.naturalHeight);
        dw = drawImg.naturalWidth * k;
        dh = drawImg.naturalHeight * k;
        dx = -dw / 2;
        dy = -dh / 2;
      } else {
        dw = dh = s;
        dx = -s / 2;
        dy = -s * 0.60;
      }
      ctx.drawImage(drawImg, dx, dy, dw, dh);
      ctx.shadowBlur = MOBILE ? 0 :0;    // rim light 只罩贴图这一下（变异体，第一百五十三轮）

      if (flash > 0) {
        // 用**离屏剪影**叠色，而不是在主画布上 source-atop。
        // 主画布上已经有背景和其他单位，source-atop 会把整个矩形涂白
        // ——那正是用户看到的"白色方块"。
        //
        // 宝箱怪用暖金色（她是个装着宝贝的箱子，白闪太生硬），
        // 其余敌人用白色，保持原有打击感。
        const tint = e.def?.glow || e.isChest ? '#ffd98a' : '#ffffff';
        const sil = this._flashCanvas(enemyImg, tint);
        if (sil) {
          ctx.save();
          // 'lighter' 让叠色像"被打亮"而不是糊上一层不透明色，
          // 配合下面的 alpha 能保留贴图本身的明暗细节
          ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
          ctx.globalAlpha = flash * (tint === '#ffffff' ? 0.55 : 0.42);
          ctx.drawImage(sil, dx, dy, dw, dh);
          ctx.restore();
        }
      }
    } else {
    switch (e.shape) {
      case 'chest': {
        // 宝箱：箱体 + 盖子 + 锁扣，一眼能认出是"可以打的箱子"
        const w = r * 1.15, h = r * 0.95;
        ctx.beginPath();
        ctx.rect(-w, -h * 0.15, w * 2, h * 1.15);
        ctx.fill(); ctx.stroke();
        // 盖子（圆弧）
        ctx.beginPath();
        ctx.moveTo(-w, -h * 0.15);
        ctx.quadraticCurveTo(0, -h * 1.5, w, -h * 0.15);
        ctx.closePath();
        ctx.fillStyle = e.hurtFlash > 0 ? '#ffffff' : (e.accent ?? col);
        ctx.fill(); ctx.stroke();
        // 锁扣
        ctx.fillStyle = 'rgba(20,20,28,0.85)';
        ctx.fillRect(-r * 0.18, -h * 0.2, r * 0.36, h * 0.45);
        break;
      }
      case 'tri': {
        ctx.beginPath();
        ctx.moveTo(r * 1.15, 0);
        ctx.lineTo(-r * 0.8, -r * 0.95);
        ctx.lineTo(-r * 0.8, r * 0.95);
        ctx.closePath();
        ctx.fill(); ctx.stroke();
        break;
      }
      case 'diamond': {
        ctx.beginPath();
        ctx.moveTo(0, -r * 1.15);
        ctx.lineTo(r * 1.15, 0);
        ctx.lineTo(0, r * 1.15);
        ctx.lineTo(-r * 1.15, 0);
        ctx.closePath();
        ctx.fill(); ctx.stroke();
        break;
      }
      case 'hex': {
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = (i / 6) * TAU - Math.PI / 2;
          const px = Math.cos(a) * r * 1.1;
          const py = Math.sin(a) * r * 1.1;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
        ctx.fill(); ctx.stroke();
        break;
      }
      case 'square': {
        const s = r * 0.92;
        ctx.beginPath();
        ctx.rect(-s, -s, s * 2, s * 2);
        ctx.fill(); ctx.stroke();
        break;
      }
      // 第十五轮 ④：虾的形状（蒜蓉粉丝开背虾专用）。
      //
      // 同样是为了"贴图没加载时也认得出"。虾的辨识特征是：
      // 分节的弯身 + 头部的长触须 + 尾扇。整体朝 +x 方向画，
      // 这样配合 facePlayer 的旋转，头就永远指着玩家。
      case 'shrimp': {
        // 弯身：几段递减的圆，沿一条上拱的弧排开，形成"分节"的观感
        const segs = 5;
        for (let i = 0; i < segs; i++) {
          const t = i / (segs - 1);            // 0 = 头, 1 = 尾
          const cx = r * (0.55 - t * 1.5);     // 从头部往后
          const cy = -Math.sin(t * Math.PI) * r * 0.5;  // 上拱
          const rad = r * (0.62 - t * 0.26);
          ctx.beginPath();
          ctx.arc(cx, cy, rad, 0, TAU);
          ctx.fill(); ctx.stroke();
        }
        // 尾扇：末端一个三角形，像虾尾
        ctx.beginPath();
        ctx.moveTo(-r * 1.0, -r * 0.15);
        ctx.lineTo(-r * 1.55, -r * 0.62);
        ctx.lineTo(-r * 1.55, r * 0.42);
        ctx.closePath();
        ctx.fill(); ctx.stroke();
        // 触须：从头部往前伸两根细线
        ctx.save();
        ctx.strokeStyle = 'rgba(0,0,0,0.55)';
        ctx.lineWidth = Math.max(1.5, r * 0.07);
        for (const a of [-0.42, 0.30]) {
          ctx.beginPath();
          ctx.moveTo(r * 0.85, Math.sin(a) * r * 0.2);
          ctx.quadraticCurveTo(r * 1.5, Math.sin(a) * r * 0.7,
            r * 2.0, Math.sin(a) * r * 1.25);
          ctx.stroke();
        }
        ctx.restore();
        break;
      }
      case 'star': {
        this._starPath(ctx, r * 1.15, r * 0.52, 5);
        ctx.fill(); ctx.stroke();
        break;
      }
      // 第十一轮：饮料瓶形状（依趣苹果醋专用）。
      //
      // 为什么值得单独做一个形状而不是复用 circle：
      // 用户明确说这个 Boss "是一种苹果醋饮料"，它的**辨识度就是瓶子** ——
      // 一个圆形配上标签色，玩家只会觉得是"又一个绿色球"。
      // 瓶身（直筒）+ 收窄的瓶颈 + 瓶盖，三个特征一摆出来就认得了。
      //
      // 而且这条路径是**贴图加载不到时的兜底**（贴图优先，
      // 见上面的 petSprite / enemy sprite 分支）。所以即使美术资源
      // 还没做，玩家也能看出"这是个瓶子"。
      case 'bottle': {
        const bw = r * 0.78;      // 瓶身半宽
        const bh = r * 1.05;      // 瓶身半高
        const neckW = bw * 0.42;  // 瓶颈半宽
        const neckH = r * 0.42;   // 瓶颈高
        ctx.beginPath();
        // 从左下角开始，顺时针描一圈瓶子的轮廓
        ctx.moveTo(-bw, bh);
        ctx.lineTo(-bw, -bh + r * 0.30);
        // 肩部：斜着收进瓶颈
        ctx.quadraticCurveTo(-bw, -bh, -neckW, -bh - neckH * 0.12);
        ctx.lineTo(-neckW, -bh - neckH);
        // 瓶盖（比瓶颈略宽一点，像拧上去的）
        ctx.lineTo(neckW, -bh - neckH);
        ctx.lineTo(neckW, -bh - neckH * 0.12);
        ctx.quadraticCurveTo(bw, -bh, bw, -bh + r * 0.30);
        ctx.lineTo(bw, bh);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        // 瓶盖单独上一块深色，强化"这是个饮料瓶"
        ctx.save();
        ctx.fillStyle = 'rgba(20,20,24,0.85)';
        ctx.beginPath();
        ctx.rect(-neckW, -bh - neckH, neckW * 2, neckH * 0.62);
        ctx.fill();
        ctx.restore();
        // 标签：横向一条浅色带，模拟瓶身贴纸
        ctx.save();
        ctx.globalAlpha = 0.55;
        ctx.fillStyle = '#f2efe6';
        ctx.beginPath();
        ctx.rect(-bw * 0.92, -r * 0.16, bw * 1.84, r * 0.62);
        ctx.fill();
        ctx.restore();
        break;
      }
      case 'blob': {
        ctx.beginPath();
        const pts = 10;
        for (let i = 0; i <= pts; i++) {
          const a = (i / pts) * TAU;
          const wob = 1 + Math.sin(a * 3 + e.age * 3) * 0.1;
          const px = Math.cos(a) * r * wob;
          const py = Math.sin(a) * r * wob;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
        ctx.fill(); ctx.stroke();
        break;
      }
      default: { // circle
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, TAU);
        ctx.fill(); ctx.stroke();
      }
    }
    }   // ← 结束「无贴图时走几何形状」的 else
    if (e.mutant) ctx.shadowBlur = MOBILE ? 0 :0;   // rim light 只罩本体（几何兜底路径）

    // 眼睛：朝向玩家
    //
    // ---- 第九轮修复：眼睛曾经"长在肚脐上" ----
    // 用户反馈："有的小怪的形象的眼睛飞出了自己的脸部……
    //            尽量让眼睛不要出了它自己的脸。"
    //
    // 根因是**两套坐标系**：
    //   贴图按 S = 2.6r 画在 (-S/2, -0.60S)，世界 y ∈ [-1.56r, +1.04r]
    //   眼睛却按 r 尺度画在敌人原点附近，还额外带一份 0.6r 的横向"看向"偏移。
    // 结果是：眼睛既不在贴图的脸心上（整组偏右 0.17r → 右眼压着右脸颊），
    // 大小和间距也不随头部尺寸变化。
    //
    // 现在眼睛的位置/大小全部由 data/enemy-eyes.js 的 eyePlacement() 算出来：
    //   u/v（脸心）由视觉模型在带百分比网格的贴图上标出，
    //   l/r（那一行的身体左右边界）由贴图 alpha 量出，用来做最终夹紧 ——
    //   所以即使脸被估偏，眼睛也不可能跑出身体轮廓。
    // 抽成共享纯函数是为了让测试能复用同一套公式（而不是各写一份）。
    // 第十五轮 ④：Boss 立绘**自带脸**，不能再叠 canvas 眼睛。
    //
    // 普通敌人贴图生成时就要求"无脸"（见敌人贴图的规范），所以眼睛由
    // 下面那段每帧按 e.facing 画出来，实现"眼睛会动"。
    // 但用户给的这三张 Boss 是**完整立绘** —— 脸上本来就有眼睛。
    // 再叠一组 canvas 眼睛会变成"四只眼"，比没有眼睛更糟。
    // 所以用 noCanvasEyes 显式关掉，而不是靠"贴图里没脸"这个假设。
    // 第二十轮：固定宝箱/医疗箱也不画眼睛 —— 用户："它们本身不会动不算
    // 活物"。第十九轮给宝箱接贴图后 enemyImg 不再是 null，眼睛通道被顺带
    // 打开（宝箱没有 def，noCanvasEyes 拦不住），这里明确关上。
    const drawEyes = enemyImg && !e.isChest && !e.def?.noCanvasEyes;
    if (drawEyes) {
      const p = eyePlacement(eyeAnchor(e.def?.id), r, e.facing);
      const eyeR = p.eyeR;

      // ---- 表情（第十轮，用户要求）----
      // 三种状态：
      //   'hurt'  宝箱怪挨打 → >< （眯眼）
      //   'angry' 蓄力/冲锋中 → 愤怒眼（眼角下压 + 眉线）
      //   'normal'
      const expr = this._enemyExpression(e);

      if (expr === 'hurt') {
        // >< ：两条向内的斜线，取代圆眼。挨打时看起来"哎呀"一下。
        ctx.strokeStyle = 'rgba(10,10,14,0.92)';
        ctx.lineWidth = Math.max(1.4, eyeR * 0.62);
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const eye of p.eyes) {
          const d = eyeR * 1.05;
          // 左眼 "<" 形、右眼 ">" 形 —— 对称的眯眼
          const sgn = eye.x < 0 ? 1 : -1;
          ctx.moveTo(eye.x - sgn * d, eye.y - d);
          ctx.lineTo(eye.x + sgn * d * 0.55, eye.y);
          ctx.lineTo(eye.x - sgn * d, eye.y + d);
        }
        ctx.stroke();
      } else if (expr === 'angry') {
        // 愤怒眼：眼睛压扁一点，上方加一道向内下压的眉线。
        // 眉线是"愤怒"的关键 —— 只压扁眼睛看不出情绪。
        ctx.fillStyle = 'rgba(10,10,14,0.92)';
        for (const eye of p.eyes) {
          ctx.beginPath();
          ctx.ellipse(eye.x, eye.y + eyeR * 0.12, eyeR * 1.05, eyeR * 0.72, 0, 0, TAU);
          ctx.fill();
        }
        ctx.strokeStyle = 'rgba(10,10,14,0.92)';
        ctx.lineWidth = Math.max(1.3, eyeR * 0.55);
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const eye of p.eyes) {
          const sgn = eye.x < 0 ? 1 : -1;   // 眉线向内下压
          ctx.moveTo(eye.x - sgn * eyeR * 1.5, eye.y - eyeR * 1.15);
          ctx.lineTo(eye.x + sgn * eyeR * 1.3, eye.y - eyeR * 0.25);
        }
        ctx.stroke();
        // 愤怒时不画高光，眼神更"狠"
      } else {
        ctx.fillStyle = 'rgba(10,10,14,0.92)';
        for (const eye of p.eyes) {
          ctx.beginPath();
          ctx.arc(eye.x, eye.y, eyeR, 0, TAU);
          ctx.fill();
        }
        // 高光：让眼睛"活"一点
        ctx.fillStyle = 'rgba(255,255,255,0.7)';
        for (const eye of p.eyes) {
          ctx.beginPath();
          ctx.arc(eye.x - eyeR * 0.3, eye.y - eyeR * 0.35, Math.max(0.7, eyeR * 0.32), 0, TAU);
          ctx.fill();
        }
      }
    } else if (!enemyImg) {
      // ---- 无贴图（几何形状）才走这里 ----
      //
      // ⚠️ 第十八轮 ⑥ 修的真 bug：这个分支原来只是 `else`，
      // 判的是 `!drawEyes` —— 于是**有贴图但 noCanvasEyes 为真**的
      // Boss 也会掉进来，在立绘上再画一对几何眼睛，正好就是
      // 用户说的"有 4 个眼睛"。
      //
      // 第十五轮加 noCanvasEyes 时只关掉了上面那个贴图分支，
      // 没想到 else 会兜住。改成显式判 `!enemyImg`：
      //   · 有贴图 + 要画眼   → 上面
      //   · 有贴图 + 不画眼   → **两个分支都不进**（立绘自带脸，什么都不叠）
      //   · 无贴图            → 这里（几何眼睛 + 表情）
      //
      // 形状本来就以原点为中心，原来的公式是对的。
      // 表情在这里同样生效 —— 贴图是可选增强，表情不该只在有贴图时才有
      // （项目原则："贴图永远是可选增强，不是必需品"）。
      const ex = Math.cos(e.facing) * r * 0.28;
      const ey = Math.sin(e.facing) * r * 0.28;
      const eyeR = Math.max(1.6, r * 0.14);
      const expr = this._enemyExpression(e);
      const eyes = [-1, 1].map((sgn) => ({
        x: ex * 0.6 + sgn * r * 0.30, y: ey * 0.6 - r * 0.06,
      }));
      if (expr === 'hurt') {
        ctx.strokeStyle = 'rgba(10,10,14,0.9)';
        ctx.lineWidth = Math.max(1.4, eyeR * 0.62);
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const eye of eyes) {
          const d = eyeR * 1.05;
          const sgn = eye.x < 0 ? 1 : -1;
          ctx.moveTo(eye.x - sgn * d, eye.y - d);
          ctx.lineTo(eye.x + sgn * d * 0.55, eye.y);
          ctx.lineTo(eye.x - sgn * d, eye.y + d);
        }
        ctx.stroke();
      } else {
        ctx.fillStyle = 'rgba(10,10,14,0.9)';
        for (const eye of eyes) {
          ctx.beginPath();
          if (expr === 'angry') {
            ctx.ellipse(eye.x, eye.y + eyeR * 0.12, eyeR * 1.05, eyeR * 0.72, 0, 0, TAU);
          } else {
            ctx.arc(eye.x, eye.y, eyeR, 0, TAU);
          }
          ctx.fill();
        }
        if (expr === 'angry') {
          ctx.strokeStyle = 'rgba(10,10,14,0.9)';
          ctx.lineWidth = Math.max(1.3, eyeR * 0.55);
          ctx.lineCap = 'round';
          ctx.beginPath();
          for (const eye of eyes) {
            const sgn = eye.x < 0 ? 1 : -1;
            ctx.moveTo(eye.x - sgn * eyeR * 1.5, eye.y - eyeR * 1.15);
            ctx.lineTo(eye.x + sgn * eyeR * 1.3, eye.y - eyeR * 0.25);
          }
          ctx.stroke();
        } else {
          ctx.fillStyle = 'rgba(255,255,255,0.7)';
          for (const eye of eyes) {
            ctx.beginPath();
            ctx.arc(eye.x - eyeR * 0.3, eye.y - eyeR * 0.35, Math.max(0.7, eyeR * 0.32), 0, TAU);
            ctx.fill();
          }
        }
      }
    }

    // 精英/Boss 标记
    if (e.elite) {
      ctx.strokeStyle = 'rgba(255,200,87,0.9)';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.arc(0, 0, r + 6, 0, TAU);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.restore();

    // 血条（受伤后显示）
    if (e.hp < e.maxHp && !e.boss) {
      const w = Math.max(20, r * 2);
      const h = 3.5;
      const y = e.y - r - 10;
      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.fillRect(e.x - w / 2, y, w, h);
      ctx.fillStyle = e.mutant ? '#ff4d5e' : e.elite ? '#ffc857' : '#ff5f6d';
      ctx.fillRect(e.x - w / 2, y, w * e.hpPercent, h);
    }

    // ---- 第一百五十三轮：变异体狂暴标记 ----
    // 用户："在变异小怪头顶加一个极其醒目的红色标志"。用发光的"危"字：
    // 红字 + 红色 shadow 光晕 + sin 闪烁，restore 后的世界坐标画（不受
    // facePlayer 镜像影响，汉字永远不会反）。
    if (e.mutant) {
      // 飘散的黑红粒子：火星（红）+ 黑烟，身上随机点冒出、缓缓向上飘。
      // 粒子数组存在 renderer 上（纯视觉，不影响任何判定）。
      this._mutantParts ??= [];
      if (this._mutantParts.length < 240 && Math.random() < 0.3) {
        const a = Math.random() * TAU;
        const rr = Math.random() * r * 0.8;
        this._mutantParts.push({
          x: e.x + Math.cos(a) * rr, y: e.y + Math.sin(a) * rr * 0.7,
          vx: (Math.random() - 0.5) * 14, vy: -(16 + Math.random() * 26),
          t: 0, life: 0.7 + Math.random() * 0.7,
          red: Math.random() < 0.6, sz: 1.2 + Math.random() * 1.8,
        });
      }
      const blink = 0.6 + Math.sin(e.age * 7) * 0.4;
      ctx.save();
      ctx.font = `bold ${Math.max(13, Math.round(r * 0.95))}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.shadowColor = '#ff0000';
      ctx.shadowBlur = MOBILE ? 0 :9;
      ctx.fillStyle = `rgba(255,32,48,${blink})`;
      ctx.fillText('危', e.x, e.y - r - 22);
      ctx.restore();
    }
  }

  /**
   * 第一百五十三轮：变异体的黑红飘散粒子更新+绘制（世界层，敌人之后）。
   * dt 固定 1/60（渲染层惯例，同 drawShards）——纯视觉不接游戏时间。
   */
  _tickMutantParticles(ctx) {
    const list = this._mutantParts;
    if (!list || !list.length) return;
    const dt = 1 / 60;
    for (let i = list.length - 1; i >= 0; i--) {
      const p = list[i];
      p.t += dt;
      if (p.t >= p.life) { list.splice(i, 1); continue; }
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vy *= (1 - 0.6 * dt);            // 上升减速，飘
      const k = 1 - p.t / p.life;
      if (p.red) {
        // 暗红火星：亮核 + 柔和红晕
        ctx.fillStyle = `rgba(255,64,52,${0.75 * k})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.sz * k + 0.4, 0, TAU);
        ctx.fill();
      } else {
        // 黑烟：半透明暗团，越老越大越淡
        ctx.fillStyle = `rgba(16,12,18,${0.5 * k})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.sz * (1 + p.t * 2.2), 0, TAU);
        ctx.fill();
      }
    }
  }

  /**
   * 第一百五十三轮：变异体暗红染色离屏 —— multiply 把立绘色相压成
   * 干涸血液的暗红（保留明暗细节），再叠半透明血红加强"浸血"感。
   * 两步都在离屏上做，主画布零污染（与 _flashCanvas 同一防"白方块"
   * 思路）。按贴图实例缓存，一个 def 只染一次。
   */
  _mutantCanvas(img) {
    if (!img || !img.naturalWidth) return null;
    let c = this._mutantCache?.get(img);
    if (c) return c;
    try {
      c = document.createElement('canvas');
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const x = c.getContext('2d');
      if (!x) return null;
      x.drawImage(img, 0, 0);
      // ① multiply 暗红：色相整体染成血红，明暗关系保留
      x.globalCompositeOperation = 'multiply';
      x.fillStyle = '#a03040';
      x.fillRect(0, 0, c.width, c.height);
      // multiply 对透明像素在部分浏览器会写入不透明色 —— source-atop
      // 收一遍 alpha：只保留原贴图形状内的像素
      x.globalCompositeOperation = 'destination-in';
      x.drawImage(img, 0, 0);
      // ② 半透明血色浸染
      x.globalCompositeOperation = 'source-atop';
      x.fillStyle = 'rgba(110,8,20,0.42)';
      x.fillRect(0, 0, c.width, c.height);
      (this._mutantCache ??= new Map()).set(img, c);
      return c;
    } catch {
      return null;
    }
  }

  /**
   * 决定敌人此刻该用哪套眼睛（第十轮，用户要求）。
   *
   *   'hurt'  宝箱怪刚挨打 → >< 眯眼，"哎呀"一下
   *   'angry' 正在蓄力/冲锋 → 愤怒眼
   *   'normal'
   *
   * 为什么蓄力也算 angry：蓄力是**即将冲过来**的那 0.7 秒，
   * 玩家正好需要一眼看出"这只怪要动了"。表情比红色预警环更直观，
   * 而且两者叠加不冲突（环在脚下，眉在脸上）。
   */
  _enemyExpression(e) {
    // 宝箱怪挨打 → ><。
    // 只给宝箱怪（用户点名要的），别的怪挨打太多、一直眯眼会很吵。
    // 用 hurtExpr 而不是 hurtFlash：后者只有 0.12 秒，表情根本看不清。
    const id = e.def?.id ?? '';
    if (e.hurtExpr > 0 && (id === 'treasure_mimic' || id === 'treasure_mimic_elite')) {
      return 'hurt';
    }
    // 蓄力 / 冲锋中 → 愤怒眼
    if (e.state === 'windup' || e.state === 'charge') return 'angry';
    return 'normal';
  }

  _starPath(ctx, outer, inner, points) {
    ctx.beginPath();
    for (let i = 0; i < points * 2; i++) {
      const r = i % 2 === 0 ? outer : inner;
      const a = (i / (points * 2)) * TAU - Math.PI / 2;
      const px = Math.cos(a) * r;
      const py = Math.sin(a) * r;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
  }

  // =========================================================
  // 炮台
  // =========================================================
  _drawTurret(ctx, t) {
    // 第一百三十九轮 D 重构（用户："灰白色六边形线框太丑"）：
    //   底座 = 深灰实心六边形 + 金属渐变 + 亮棱边；炮管 = 深色短管 +
    //   炮口环；核心 = 发光色球，开火瞬间随 recoil 闪亮。
    // 品质差异只体现在 def.color（核心/描边/辉光），结构全档通用。
    const def = t.def;
    // 出生放大动画
    const grow = t.spawnAnim > 0 ? 1 - t.spawnAnim / 0.3 : 1;
    const s = 0.5 + grow * 0.5;
    const r = t.radius;

    ctx.save();
    ctx.translate(t.x, t.y);

    // 底座阴影
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = '#000';
    ctx.beginPath();
    ctx.ellipse(0, r * 0.55, r * 1.05, r * 0.5, 0, 0, TAU);
    ctx.fill();
    ctx.globalAlpha = 1;

    ctx.scale(s, s);

    // ---- 底座：深灰金属六边形 ----
    const hex = () => {
      ctx.beginPath();
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * TAU + Math.PI / 6;
        const px = Math.cos(a) * r, py = Math.sin(a) * r;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
    };
    hex();
    const mg = ctx.createLinearGradient(0, -r, 0, r);   // 金属纵向渐变
    mg.addColorStop(0, '#3d4356');
    mg.addColorStop(0.45, '#2a2e3d');
    mg.addColorStop(1, '#1c1f2b');
    ctx.fillStyle = mg;
    ctx.fill();
    // 金属亮棱边：上半圈亮、下半圈暗 —— 用两段描边模拟受光
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = 'rgba(255,255,255,0.28)';
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 6) * TAU + Math.PI / 6;
      const a2 = ((i + 1) / 6) * TAU + Math.PI / 6;
      if (i === 0) { ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r); }
      ctx.lineTo(Math.cos(a2) * r, Math.sin(a2) * r);
    }
    ctx.stroke();
    ctx.strokeStyle = def.color;
    ctx.globalAlpha = 0.55;
    hex();
    ctx.stroke();                                        // 品质色细描边
    ctx.globalAlpha = 1;
    // 内圈刻线（机械感）
    ctx.strokeStyle = 'rgba(255,255,255,0.10)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * TAU + Math.PI / 6;
      const px = Math.cos(a) * r * 0.72, py = Math.sin(a) * r * 0.72;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.stroke();

    // ---- 炮管：朝目标的深色短管 + 炮口环，开火后坐 ----
    const recoil = t.recoil > 0 ? t.recoil / 0.16 : 0;
    const barrelLen = r * 1.5 - recoil * 4;
    ctx.save();
    ctx.rotate(t.angle);
    ctx.fillStyle = '#171a24';
    ctx.fillRect(r * 0.2, -4.2, barrelLen, 8.4);
    // 管身高光棱线
    ctx.fillStyle = 'rgba(255,255,255,0.16)';
    ctx.fillRect(r * 0.2, -4.2, barrelLen, 2);
    // 炮口环（品质色）
    ctx.strokeStyle = def.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(barrelLen + r * 0.2, -5);
    ctx.lineTo(barrelLen + r * 0.2, 5);
    ctx.stroke();
    ctx.restore();

    // ---- 核心：发光色球，开火瞬间闪亮 ----
    const flash = 1 + recoil * 1.1;                      // 开火瞬间核心暴涨
    if (!this._fxSaturated()) {
      const gr = r * 1.15 * flash;
      ctx.globalAlpha = 0.55;
      ctx.drawImage(this._glowSprite(def.color), -gr, -gr, gr * 2, gr * 2);
      ctx.globalAlpha = 1;
    }
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.42 * flash, 0, TAU);
    ctx.fillStyle = def.color;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.2, 0, TAU);
    ctx.fillStyle = '#ffffff';
    ctx.fill();

    ctx.restore();
  }

  // =========================================================
  // 宠物：小小猫娘
  // 占位几何造型（圆身 + 猫耳 + 尾巴 + 飘浮），
  // 之后换成用户提供的贴图时只改这个方法。
  // =========================================================
  _drawPet(ctx, pet, camera = null) {
    const def = pet.def;
    const r = pet.radius;
    // 第十二轮（用户反馈）：
    //   "小小猫娘默认的上下浮动加大一些，有点像蹦蹦跳跳追着你的感觉
    //    可爱一些就好。"
    //
    // 2.2 → 6.5，并且**混入一个二倍频**：
    //   纯 sin 的上下移动看起来像"坐电梯"，匀速又机械。
    //   叠加一个相位错开的二倍频之后，上升快、回落慢，
    //   重心有一点点"顿"——那才是蹦跳的感觉。
    //   两项之和的极值约 6.5+2.0=8.5px，相对半径约 15px 的宠物是明显的幅度，
    //   但还不到"离开影子"的程度（影子会跟着反向补偿，见下）。
    const bobBase = Math.sin(pet.bobPhase);
    const bob = bobBase * 6.5 + Math.sin(pet.bobPhase * 2 + 0.6) * 2.0;
    const s = 1 + (pet.attackAnim > 0 ? pet.attackAnim * 0.35 : 0);
    const healGlow = pet.healAnim > 0 ? pet.healAnim / 0.7 : 0;

    ctx.save();
    ctx.translate(pet.x, pet.y + bob);

    // 影子
    //
    // 关键：影子要**反向**抵消 bob（`- bob` 而不是 `- bobBase`），
    // 否则影子会跟着宠物一起上下飘 —— 看起来像整只猫连同地面一起在动，
    // 而不是"猫在跳"。用完整的 bob 抵消后，影子始终贴在地面上。
    // 跳得越高影子越小越淡，这是"离地"的视觉线索。
    const lift = Math.max(0, bob) / 8.5;   // 0~1
    ctx.fillStyle = `rgba(0,0,0,${0.3 - lift * 0.12})`;
    ctx.beginPath();
    ctx.ellipse(0, r * 0.95 - bob, r * (0.8 - lift * 0.18), r * (0.28 - lift * 0.06), 0, 0, TAU);
    ctx.fill();

    // 回血时的粉色光晕
    if (healGlow > 0) {
      const g = ctx.createRadialGradient(0, 0, r * 0.3, 0, 0, r * 3);
      g.addColorStop(0, `rgba(255,123,172,${0.5 * healGlow})`);
      g.addColorStop(1, 'rgba(255,123,172,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(0, 0, r * 3, 0, TAU);
      ctx.fill();
    }

    ctx.scale(s, s);

    // ---- 贴图优先（第十一轮，用户要求"把小小猫娘的形象替换成小小猫娘.png"）----
    //
    // 和武器/敌人/道具一样的策略：**贴图是可选增强**。
    // 取不到就原样走下面的几何绘制，游戏完全不受影响。
    //
    // 注意贴图里的形象是**趴卧姿的横躺小猫**（宽高比约 1.34），
    // 所以这里按宽度铺开而不是塞进一个正方形 —— 塞成正方形会把
    // 她压扁成一颗球，看起来就不像"趴着的小猫吐司"了。
    //
    // 朝向：贴图本身是左右对称的趴趴姿，所以只需要在朝左时水平翻转。
    // 用 facing 的 cos 判断而不是"上一次的朝向"，避免走位时抖动翻转。
    const petSprite = sprites.get('pet', 'kitten');
    if (petSprite) {
      const facingLeft = Math.cos(pet.facing) < -0.15;
      ctx.save();
      if (facingLeft) ctx.scale(-1, 1);
      const drawW = r * 3.4;
      const drawH = drawW * (petSprite.height / petSprite.width);
      ctx.drawImage(petSprite, -drawW / 2, -drawH / 2, drawW, drawH);
      ctx.restore();
      // 这里**没有**受击闪白 —— 宠物是永久无敌的（pet.js 的 takeDamage 直接
      // 返回 0，注释写明"用户要求"），所以它永远不会被打，也就没有受击状态。
      // 第一版我照着敌人的写法加了一段 hurtFlash 闪白，那是个永远不会触发的分支。
      ctx.restore();
      // 贴图分支也要画吸取环和表情，所以不能直接 return —— 见下面的公共段
      // 第十五轮 ③ 修正：必须把 camera 一起传下去。
      // 台词气泡要用 camera.zoom 反向补偿字号，而 camera 是 render() 的
      // 参数、不是渲染器的字段 —— 漏传就是 `camera is not defined`
      // （纯几何分支不会崩，只有贴图分支会，所以很容易漏掉）。
      this._drawPetOverlays(ctx, pet, r, bob, camera);
      return;
    }

    // 尾巴
    ctx.strokeStyle = def.accent;
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(-r * 0.6, r * 0.3);
    ctx.quadraticCurveTo(-r * 1.9, r * 0.1 + Math.sin(pet.walkPhase * 0.5) * 3, -r * 1.5, -r * 0.7);
    ctx.stroke();

    // 身体
    ctx.fillStyle = def.color;
    ctx.strokeStyle = def.accent;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.fill(); ctx.stroke();

    // 猫耳（两个三角）
    ctx.fillStyle = def.accent;
    for (const sx of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(sx * r * 0.25, -r * 0.75);
      ctx.lineTo(sx * r * 0.62, -r * 1.55);
      ctx.lineTo(sx * r * 0.82, -r * 0.55);
      ctx.closePath();
      ctx.fill();
    }

    // 眼睛：朝向目标
    const ex = Math.cos(pet.facing) * r * 0.22;
    const ey = Math.sin(pet.facing) * r * 0.22;
    ctx.fillStyle = '#2a1a24';
    for (const sx of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(ex + sx * r * 0.3, ey - r * 0.08, Math.max(1.3, r * 0.15), 0, TAU);
      ctx.fill();
    }
    // 腮红
    ctx.fillStyle = 'rgba(255,123,172,0.55)';
    for (const sx of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(sx * r * 0.58, r * 0.22, r * 0.16, 0, TAU);
      ctx.fill();
    }

    ctx.restore();

    this._drawPetOverlays(ctx, pet, r, bob, camera);
  }

  /**
   * 宠物身上**两种画法共用**的叠加层：大范围吸取环 + 头顶表情。
   *
   * 抽出来的原因：贴图分支和几何分支都要画这两样。
   * 第一版我把它们留在几何分支末尾，结果走贴图时就全没了 ——
   * 吸取环是**功能性提示**（玩家靠它知道"她刚收走了一圈"），
   * 不能因为换了张图就消失。
   */
  _drawPetOverlays(ctx, pet, r, bob, camera = null) {
    // 大范围吸取的扩散环（第十轮）。
    //
    // 为什么不复用 fx.ring：那个是 FX 系统里的一次性特效，位置固定；
    // 而吸取是"以宠物为中心"的，她可能正在移动，环跟着她更准确。
    // 更重要的是这个环**要能提示范围** —— 玩家看到环就知道
    // "她刚才把这一圈都收走了"，而不是莫名其妙多了一堆材料。
    if (pet.vacuumAnim > 0) {
      const t = 1 - pet.vacuumAnim / 0.5;      // 0 → 1
      const rr = r + (PET_VACUUM_RADIUS - r) * t;
      ctx.save();
      ctx.globalAlpha = Math.max(0, 0.5 * (1 - t));
      ctx.strokeStyle = '#ffe0ec';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(pet.x, pet.y, rr, 0, TAU);
      ctx.stroke();
      ctx.restore();
    }

    // 头顶表情（回血 ♥ / 打架 ! / 大范围吸取 ✦ / 偷到东西 $）
    //
    // 表情是从**动画计时器**推出来的，而不是读 pet.emote 字段 ——
    // 那些计时器由行为逻辑维护，天然就是"动作进行中就显示、结束就消失"。
    // 第十轮加了大范围吸取，所以这里多一档 ✦；
    // 第十二轮第 7 项加了"偷商店道具"，多一档 $（得意）。
    const emote = pet.healAnim > 0 ? '♥'
      : pet.stealAnim > 0 ? '$'
        : pet.attackAnim > 0.1 ? '!'
          : pet.vacuumAnim > 0 ? '✦' : '';
    if (emote) {
      ctx.save();
      ctx.font = `${Math.round(r * 1.5)}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.fillStyle = emote === '♥' ? '#ff7bac'
        : emote === '✦' ? '#ffe0ec'
          : emote === '$' ? '#ffd166' : '#ffc857';
      ctx.fillText(emote, pet.x, pet.y - r * 1.9 + bob);
      ctx.restore();
    }

    // ---- 第十五轮 ③：台词气泡 ----
    //
    // 用户原话："给小小猫娘加技能台词……触发的时候说。"
    //
    // 画在**世界坐标**里（跟着她走），而不是屏幕 HUD：
    // 台词是"她说的"，必须贴着她，否则玩家不知道是谁在说话。
    // 但气泡要**抵消相机的缩放**，否则拉远时字会小到看不清 ——
    // 所以用 ctx.save/scale(1/zoom) 把字号固定成屏幕像素。
    if (pet.lineTimer > 0 && pet.line) {
      // 最后 0.35 秒淡出，避免"啪"地消失
      const fade = Math.min(1, pet.lineTimer / 0.35);
      this._drawPetBubble(ctx, pet, pet.line, r, bob, fade, camera);
    }
  }

  /**
   * 画宠物头顶的台词气泡（第十五轮 ③）。
   *
   * 抽成独立方法而不是塞在 _drawPet 里，是因为气泡的排版逻辑
   * （量宽度、换行、圆角、尾巴三角）和宠物造型完全无关，
   * 以后想让别的单位也能说话可以直接复用。
   */
  _drawPetBubble(ctx, pet, text, r, bob, fade = 1, camera = null) {
    ctx.save();
    ctx.globalAlpha = fade;

    // 字号按**屏幕像素**固定（抵消相机缩放）。
    // 第五十五轮修：上一轮改样式时把 zoom 的声明删掉了，小小猫娘一开口
    // 气泡绘制就 ReferenceError，整个渲染循环死掉 → 游戏黑屏。
    // 教训：改绘制代码必须配"真画一次"的回归（见 round55-check）。
    const zoom = camera?.zoom || 1;
    const fs = 13 / zoom;
    ctx.font = `600 ${fs}px system-ui, "Microsoft YaHei", sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    const padX = 13 / zoom;
    const padY = 9 / zoom;
    const maxW = 280 / zoom;
    const lines = this._wrapBubbleText(ctx, text, maxW);
    const lineH = fs * 1.5;   // 和 .neko-bubble-slot 的 line-height 1.5 一致
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + padX * 2;
    const h = lines.length * lineH + padY * 2;
    // 气泡底边在宠物头顶上方一点，尾巴再往下指
    const bx = pet.x;
    const by = pet.y - r * 1.9 + bob - h * 0.5 - fs * 1.5;

    // 圆角矩形（不用 roundRect：老一点的 Chrome 没有）
    const rr = Math.min(h / 2, 12 / zoom);
    ctx.beginPath();
    ctx.moveTo(bx - w / 2 + rr, by - h / 2);
    ctx.lineTo(bx + w / 2 - rr, by - h / 2);
    ctx.quadraticCurveTo(bx + w / 2, by - h / 2, bx + w / 2, by - h / 2 + rr);
    ctx.lineTo(bx + w / 2, by + h / 2 - rr);
    ctx.quadraticCurveTo(bx + w / 2, by + h / 2, bx + w / 2 - rr, by + h / 2);
    ctx.lineTo(bx - w / 2 + rr, by + h / 2);
    ctx.quadraticCurveTo(bx - w / 2, by + h / 2, bx - w / 2, by + h / 2 - rr);
    ctx.lineTo(bx - w / 2, by - h / 2 + rr);
    ctx.quadraticCurveTo(bx - w / 2, by - h / 2, bx - w / 2 + rr, by - h / 2);
    ctx.closePath();

    // 台词气泡（206 轮：整体换浅色主题）
    // 用户："小小猫娘不走语音的纯台词对话框还是黑色本体粉色边框，改到和语音框一样，
    //       然后给这个语音框加个可爱颜色的边框吧。"
    // 这个气泡是画布画的（不在 DOM 里），所以之前那批 CSS 覆盖全都没碰到它。
    // 207 轮（用户："小小猫娘对话框适当半透明一些吧，不然芙兰战的台词又会挡住弹幕反而成为了难度之一"）：
    // 0.96 → 0.55，边框保持不透明、字仍是深蓝，弹幕能透出来。
    ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
    ctx.fill();
    ctx.strokeStyle = '#ffa8cf';
    ctx.lineWidth = 2 / zoom;
    ctx.stroke();

    // 指向她的小尾巴（一个三角形）
    const ty = by + h / 2;
    ctx.beginPath();
    ctx.moveTo(bx - fs * 0.45, ty);
    ctx.lineTo(bx, ty + fs * 0.7);
    ctx.lineTo(bx + fs * 0.45, ty);
    ctx.closePath();
    // 尾巴跟本体一样半透明（207 轮）
    ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
    ctx.fill();
    // 只描两条斜边，底边不描（否则会在气泡上划一道线）
    ctx.strokeStyle = '#ffa8cf';
    ctx.lineWidth = 2 / zoom;
    ctx.beginPath();
    ctx.moveTo(bx - fs * 0.45, ty);
    ctx.lineTo(bx, ty + fs * 0.7);
    ctx.lineTo(bx + fs * 0.45, ty);
    ctx.stroke();

    // 文字（深蓝，配白底）
    ctx.fillStyle = '#1d3c63';
    const startY = by - ((lines.length - 1) * lineH) / 2 + fs * 0.06;
    lines.forEach((l, i) => ctx.fillText(l, bx, startY + i * lineH));
    ctx.restore();
  }

  /**
   * 第五十四轮（用户反馈 5）：芙兰战的空气墙可视化。
   * ① 竞技场边界常显一圈极淡的红色虚线 —— 第一眼知道"这里有墙"；
   * ② 撞墙瞬间：玩家周围画一个淡红圆环（半径 = 玩家半径 + 16），
   *    wallFlash 由 main 的钳制逻辑点亮（0.45s 淡出，贴墙持续按住则常亮）。
   * 用户："方便玩家第一眼知道，这是空气墙而不是继续撞墙等死。"
   */
  /**
   * 第六十三轮：Four of a Kind 期间中央本体隐身（用户："把中间的大芙兰
   * 设置隐身不可被击中"）—— 场上只看得到 4 个正常大小的分身。
   */
  _drawFlandreClones(ctx, fight) {
    return;   // 画布版已退役，保留空实现以免旧引用炸（真身在敌人循环里画）
  }

  /**
   * 第七十二轮：芙兰战背景 —— 纯色渐变 + eff07 只铺活动区。
   * 用户："黑色慢慢转换为暗红是暗红色，纯色块变化，不要有图。等芙兰
   * 出来后再慢慢浮现图，而且图不是贴在整个屏幕空间大小，是芙兰boss
   * 战斗的玩家活动区域大小，就是你用红框圈起来的那个大小。"
   * 层次：黑底 → tint.color 纯色（全屏，含墙外）→ eff07 裁进 ARENA
   * 矩形（玩家可移动区域）。混合系数 fight.tintUpdate 驱动。
   * 素材懒加载，没就绪/失败退纯色保底，绝不阻塞。
   */
  // =========================================================
  // 第一百轮：芙兰背后的大魔法阵
  //
  // 素材 = 原版 Extra 关特效图集 stg7enm 的 sprite #86（720² 高清重制版，
  // 已入 assets/fx/magic_circle_86.png），移植配方逐条照抄
  // touhou-flandre/flandre-stage7/MAGIC-CIRCLE.md：
  //   · 蓝紫层 RGB(224,191,255) 0.8×，+1.56 rad/s（原 +0.026 rad/帧）
  //   · 黄层   RGB(255,255,32)  1.0×，−1.56 rad/s（反向）
  //   · 两层加法混合（lighter），同相位反向转 → 六芒星线条周期性交叠
  //     又错开，中心叠出亮白 —— 原版"呼吸感"的来源
  //   · 入场 5 秒慢淡入（原 300 帧）；退场 2 秒淡出（原 120 帧）
  //   · 金色 RGB(255,224,32) 0.4× 小阵：符卡开始瞬间点缀 ——
  //     0.5s 淡入（原 30 帧）→ 持续 3s → 2s 淡出，不旋转
  // 染色 = 亮度当遮罩：白底 sprite × 目标色（multiply）再以原图
  // alpha 复原（destination-in），按色值缓存三张离屏画布。
  // =========================================================
  _drawFlandreMagicCircle(ctx, fight) {
    // 锚点：跟着她本人走；死后（boss 置空）用退场瞬间留的影子原地淡出
    const anchor = fight.boss || fight.circleAnchor;
    if (!anchor) return;
    const t = fight.t;
    const born = fight.circleBornAt ?? t;
    let alpha = Math.max(0, Math.min(1, (t - born) / 5));            // 5s 慢淡入
    if (fight.circleOutAt != null) {
      alpha *= Math.max(0, Math.min(1, 1 - (t - fight.circleOutAt) / 2)); // 2s 淡出
    }
    if (alpha <= 0.004) return;

    // 第一百八十五轮：符卡专属星形法阵（叠在原有紫/黄两层阵外）+ 宣言冲击波
    FR.drawSigil(ctx, fight, anchor, alpha);
    FR.drawDeclare(ctx, fight, anchor);

    const purple = this._tintedMagicCircle('rgb(224,191,255)');
    const yellow = this._tintedMagicCircle('rgb(255,255,32)');
    if (!purple || !yellow) return;   // 素材没就绪：不画，战斗照常

    const ang = 1.56 * (t - born);
    ctx.save();
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';   // 原版 BLEND_MODE_ADDITIVE
    ctx.globalAlpha = alpha;
    this._blitMagicCircle(ctx, purple, anchor.x, anchor.y, 288, ang);    // 0.8×
    this._blitMagicCircle(ctx, yellow, anchor.x, anchor.y, 360, -ang);   // 1.0× 反向
    // 金色 0.4× 点缀：符卡开始瞬间（script 16）
    const sp = fight.spell;
    if (sp) {
      let ga = 0;
      if (sp.t < 0.5) ga = sp.t / 0.5;
      else if (sp.t < 3.5) ga = 1;
      else if (sp.t < 5.5) ga = 1 - (sp.t - 3.5) / 2;
      if (ga > 0) {
        const gold = this._tintedMagicCircle('rgb(255,224,32)');
        if (gold) {
          ctx.globalAlpha = alpha * ga;
          this._blitMagicCircle(ctx, gold, anchor.x, anchor.y, 144, 0);   // 不转
        }
      }
    }
    ctx.restore();
  }

  /** 按色值缓存染色后的魔法阵离屏画布（亮度当遮罩 × 目标色） */
  _tintedMagicCircle(color) {
    return this._tintedSprite('fx', 'magic_circle_86', color);
  }

  /**
   * 第一百零一轮：通用染色贴图缓存 —— 白/灰底 sprite × 目标色，
   * 原生半透明通道用 destination-in 复原。键 = 类别: id: 色值。
   */
  _tintedSprite(kind, id, color) {
    this._mcCache = this._mcCache || {};
    const key = `${kind}:${id}:${color}`;
    if (this._mcCache[key]) return this._mcCache[key];
    const img = sprites.get(kind, id);
    if (!img || typeof document === 'undefined') return null;
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    if (!w || !h) return null;
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    g.globalCompositeOperation = 'multiply';        // 白底 × 目标色 = 染色
    g.fillStyle = color;
    g.fillRect(0, 0, w, h);
    g.globalCompositeOperation = 'destination-in';  // 复原原生的半透明通道
    g.drawImage(img, 0, 0);
    this._mcCache[key] = c;
    return c;
  }

  /**
   * 第一百零一轮：白色雾气粒子（MIST-PARTICLES.md 移植配方）。
   * 场景1 螺旋吸入：每张符卡开场 6 个粉白光晕（RGB(255,128,255)，
   * ECL 0xFF80FFFF 的 RGB 段），r = 256×(1-p) 螺旋收缩到她中心，
   * 60 帧寿命，淡入 + 先放大后缩小，相邻光晕反向旋出交错感。
   * 加法混合；画在魔法阵之下（第一百零二轮拆层后仅此一层留在底下）。
   */
  _drawFlandreMistOrbs(ctx, fight) {
    const anchor = fight.boss || fight.circleAnchor;
    if (!anchor || !fight.mistOrbs?.length) return;
    const pink = this._tintedSprite('fx', 'mist_glow', 'rgb(255,128,255)');
    if (!pink) return;
    ctx.save();
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';   // 原版 BLEND_MODE_ADDITIVE
    for (const o of fight.mistOrbs) {
      const p = Math.max(0, Math.min(1, o.t / o.life));
      const r = 256 * (1 - p);                       // Attract 收缩
      const th = o.th + p * 2.4 * o.spin;            // 螺旋角
      const a = Math.min(1, o.t / 40) * (1 - p);     // 40 帧淡入 + 渐隐
      const s = 90 + 110 * Math.sin(Math.PI * p);    // 先放大后缩小
      if (a <= 0.02 || s <= 2) continue;
      ctx.globalAlpha = a * 0.9;
      ctx.save();
      ctx.translate(anchor.x + Math.cos(th) * r, anchor.y + Math.sin(th) * r);
      ctx.rotate(o.spin * 2 * p);
      ctx.drawImage(pink, -s / 2, -s / 2, s, s);
      ctx.restore();
    }
    ctx.restore();
  }

  /**
   * 第一百零二轮：常驻呼吸雾（从世界底层提到魔法阵之上、实体之下 ——
   * 用户实机反馈雾被阵的加法光芒吞了）。
   * α = 0.35 ± 0.15 呼吸（原 0.2±0.1，用户："提升到 0.35±0.15，让它
   * 更实一点"）；染色带极淡粉青偏色：雾球 rgb(255,219,238) 淡粉、
   * 星芒 rgb(214,240,255) 淡青（用户："原版雾气是白色的，但在暗红
   * 背景下可以带一点点极淡的粉青偏色"）。
   */
  _drawFlandreAmbientMist(ctx, fight) {
    const t = fight.t;
    const glow = this._tintedSprite('fx', 'mist_glow', 'rgb(255,219,238)');
    const starFlare = this._tintedSprite('fx', 'star_flare', 'rgb(214,240,255)');
    const star = this._tintedSprite('fx', 'star_24', 'rgb(214,240,255)') || starFlare;
    if (!glow || !starFlare) return;
    ctx.save();
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';   // 原版 BLEND_MODE_ADDITIVE
    for (const m of fight.mistAmbient ?? []) {
      const a = 0.35 + 0.15 * Math.sin(m.phase + t * m.speed);
      if (a <= 0.02) continue;
      const img = m.star ? star : glow;
      if (!img) continue;
      ctx.globalAlpha = a;
      const s = m.size;
      ctx.drawImage(img, m.x - s / 2, m.y - s / 2, s, s);
    }
    ctx.restore();
  }

  /** 以 (x,y) 为中心、给定边长与角度画一张已染色的阵 */
  _blitMagicCircle(ctx, tex, x, y, size, angle) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.drawImage(tex, -size / 2, -size / 2, size, size);
    ctx.restore();
  }

  _drawFlandreBg(ctx, fight, camera, W, H) {
    // ① 黑底（序章 3 秒渐黑）
    const alpha = Math.min(1, fight.tint?.alpha ?? 1);
    if (alpha > 0.004) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, W, H);
      // ② 纯色暗红层：整个屏幕（墙外也保持这个暗红），纯色块无图
      const col = fight.tint?.color;
      if (col && col !== '#000000') {
        ctx.fillStyle = col;
        ctx.fillRect(0, 0, W, H);
      }
      ctx.restore();
    }
    // ③ eff07 暗红旋涡：只铺空气墙内的玩家活动区域（芙兰登场后浮现）
    if ((fight.bg?.eff ?? 0) > 0.004) {
      const rx = W / 2 + (ARENA.cx - camera.cx) - ARENA.hw;
      const ry = H / 2 + (ARENA.cy - camera.cy) - ARENA.hh;
      const rw = ARENA.hw * 2, rh = ARENA.hh * 2;
      const img = flandreBgImg('eff07_nc_4k.png');
      ctx.save();
      ctx.beginPath();
      ctx.rect(rx, ry, rw, rh);
      ctx.clip();
      ctx.globalAlpha = fight.bg.eff;
      if (img) {
        // 原作 ANM：缩放 1.75 + 慢速平移循环 —— 在活动区内 cover + 慢速往复
        const sc = Math.max(rw / img.width, rh / img.height) * 1.75;
        const w = img.width * sc, h = img.height * sc;
        const sway = Math.sin((fight.t ?? 0) * 0.05);
        const x = rx + (rw - w) / 2 + sway * (rw - w) / 2;
        const y = ry + (rh - h) / 2;
        ctx.drawImage(img, x, y, w, h);
      } else {
        ctx.fillStyle = '#2b0606';                    // 原作均色 (43,6,6) 保底
        ctx.fillRect(rx, ry, rw, rh);
      }
      ctx.restore();
    }
    // ④ 第一百八十五轮：水晶翅膀剪影 + 红雾 + 余烬 + 暗角（屏幕空间，墙外也有）
    FR.drawBg(ctx, fight, W / 2 + (ARENA.cx - camera.cx), H / 2 + (ARENA.cy - camera.cy), W, H);
  }

  /**
   * 第七十二轮：莱瓦汀旋转激光（原作 laser_create：长 388→420 渐变、
   * 宽 24、Sub47 laser_rotate）。三层光带：红辉 → 橙芯 → 白热，
   * 出鞘 0.35s 内快速伸长，消散前 0.5s 渐隐。世界坐标（相机平移内）。
   */
  _drawFlandreLasers(ctx, fight) {
    const ls = fight.spell?.st?.lasers ?? [];
    for (const L of ls) {
      if (L.warn > 0) {
        // 199 轮：出剑预警 —— 细亮线（指示起手方向）+ 淡淡的扫程扇面（指示这把剑将要扫过哪里）。
        // 越接近出剑越亮、扇面越实；纯画面，不参与判定。
        const t = fight.t ?? 0, near = 1 - Math.min(1, L.warn / 0.85);
        ctx.save();
        ctx.translate(L.x, L.y);
        if (L.sweep) {
          ctx.globalAlpha = 0.07 + 0.10 * near;
          ctx.fillStyle = '#ff3c28';
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.arc(0, 0, L.len, L.angle, L.angle + L.sweep, L.sweep < 0);
          ctx.closePath();
          ctx.fill();
        }
        ctx.rotate(L.angle);
        ctx.globalAlpha = (0.35 + 0.5 * near) * (0.75 + 0.25 * Math.sin(t * 28));
        ctx.fillStyle = '#ffd9b0';
        ctx.fillRect(0, -1.5, L.len, 3);
        ctx.restore();
        continue;
      }
      if (L.life <= 0 || L.fade <= 0) continue;
      const len = L.len * Math.min(1, L.age / 0.35);   // 388→420 的长度渐变起步
      const w = L.width;
      ctx.save();
      ctx.translate(L.x, L.y);
      ctx.rotate(L.angle);
      ctx.globalAlpha = 0.32 * L.fade;                 // 外辉
      ctx.fillStyle = '#ff3c28';
      ctx.fillRect(0, -w, len, w * 2);
      ctx.globalAlpha = 0.78 * L.fade;                 // 橙芯
      ctx.fillStyle = '#ff7a3c';
      ctx.fillRect(0, -w * 0.5, len, w);
      ctx.globalAlpha = L.fade;                        // 白热刃
      ctx.fillStyle = '#ffe9c9';
      ctx.fillRect(0, -w * 0.16, len, w * 0.32);
      ctx.restore();
    }
  }

  /**
   * 第七十三轮：四重存在的分身 —— 纯动画发射器。用户："让4个分身芙兰
   * 变成单纯的弹幕发射器，不算怪，不算boss，没有血，她们就是4个动画
   * 发射器。" 本体隐身（flandreHidden），hitbox 扩大到覆盖 4 个分身轨道
   * —— 自动攻击锁定的是"看不见但系统看得到"的本体，伤害只结算 1 次。
   * 这里只负责按敌人同款贴图把 4 个分身画出来（含 0.35s 立绘循环）。
   */
  _drawFlandreClones(ctx, fight) {
    const clones = fight.spell?.st?.clones ?? [];
    for (const c of clones) {
      const img = sprites.get('enemy', c.spriteId);
      if (!img) continue;
      const s = (c.radius ?? 24) * 2.6;      // 与 _drawEnemy 同款：r × 2.6
      ctx.drawImage(img, c.x - s / 2, c.y - s * 0.60, s, s);
    }
  }

  /**
   * 第七十五轮：EP 符卡的发射器可视体。用户对血痕的描述："各种大玉
   * 弹幕带着一大堆小弹幕发出去，我们可以把领头弹幕的体积做大点" ——
   * 原版发射器就是可见的 bullet 贴图 enemy。三种：
   *   blood —— 大玉领弹：深红大圆 + 亮边 + 白高光，随自转微摆；
   *   vent  —— 心室：脉动的红球（心跳感）；
   *   wing  —— 羽翼：按出射色染色的小菱形，缓慢自转。
   */
  _drawFlandreEmitters(ctx, vis) {
    for (const v of vis) {
      if (v.kind === 'glow') { FR.drawGlowEmitter(ctx, v, this.world?.flandreFight?.t ?? 0); continue; }   // 185 轮：新符卡发射器 = 发光球
      ctx.save();
      ctx.translate(v.x, v.y);
      if (v.kind === 'blood') {
        const wob = Math.sin((v.age ?? 0) * 9) * 0.12;
        ctx.rotate((v.dir ?? 0) * 2 + wob);
        ctx.fillStyle = '#7d1024';                       // 大玉外圈
        ctx.beginPath(); ctx.arc(0, 0, v.r, 0, TAU); ctx.fill();
        ctx.fillStyle = '#c81e3c';                       // 玉体
        ctx.beginPath(); ctx.arc(0, -v.r * 0.1, v.r * 0.82, 0, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,242,244,0.85)';        // 白高光
        ctx.beginPath(); ctx.arc(-v.r * 0.3, -v.r * 0.38, v.r * 0.2, 0, TAU); ctx.fill();
      } else if (v.kind === 'orb') {
        // 第七十八轮：星虹发射器的"大的实蛋"—— 实心大弹（同发射器色），
        // 暗外圈 + 白高光，让隐形弹源可见（用户："把他们的位置变成一颗
        // 大的实蛋显示，不然有时候莫名被0帧刷新的子弹命中"）
        const wob = 1 + Math.sin((v.age ?? 0) * 5) * 0.08;
        ctx.fillStyle = 'rgba(0,0,0,0.35)';              // 暗外圈
        ctx.beginPath(); ctx.arc(0, 0, v.r * wob, 0, TAU); ctx.fill();
        ctx.fillStyle = v.color ?? '#ff7f9f';            // 实蛋本体
        ctx.beginPath(); ctx.arc(0, 0, v.r * 0.82 * wob, 0, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,0.75)';        // 白高光
        ctx.beginPath(); ctx.arc(-v.r * 0.3, -v.r * 0.36, v.r * 0.22, 0, TAU); ctx.fill();
      } else if (v.kind === 'vent') {
        const pulse = 1 + Math.sin((v.age ?? 0) * 6) * 0.14;
        ctx.fillStyle = 'rgba(200,30,60,0.35)';          // 心晕
        ctx.beginPath(); ctx.arc(0, 0, v.r * 1.7 * pulse, 0, TAU); ctx.fill();
        ctx.fillStyle = '#e03050';                       // 心体
        ctx.beginPath(); ctx.arc(0, 0, v.r * pulse, 0, TAU); ctx.fill();
        ctx.fillStyle = '#ffd7de';                       // 高光
        ctx.beginPath(); ctx.arc(-v.r * 0.28, -v.r * 0.3, v.r * 0.22, 0, TAU); ctx.fill();
      } else {                                           // wing
        ctx.rotate((v.age ?? 0) * (v.dir ?? 1) * 2.4);
        ctx.fillStyle = v.color ?? '#fff';
        ctx.beginPath();
        ctx.moveTo(0, -v.r); ctx.lineTo(v.r * 0.5, 0);
        ctx.lineTo(0, v.r); ctx.lineTo(-v.r * 0.5, 0);
        ctx.closePath(); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.beginPath();
        ctx.moveTo(0, -v.r * 0.5); ctx.lineTo(v.r * 0.22, 0);
        ctx.lineTo(0, v.r * 0.5); ctx.lineTo(-v.r * 0.22, 0);
        ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    }
  }

  _drawFlandreWalls(ctx, player) {
    ctx.save();
    // 边界虚线
    ctx.strokeStyle = 'rgba(255, 96, 96, 0.16)';
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 8]);
    ctx.strokeRect(ARENA.cx - ARENA.hw, ARENA.cy - ARENA.hh, ARENA.hw * 2, ARENA.hh * 2);
    ctx.setLineDash([]);
    // 撞墙反馈圆环
    const flash = player.wallFlash ?? 0;
    if (flash > 0) {
      const k = Math.min(1, flash / 0.45);
      ctx.beginPath();
      ctx.arc(player.x, player.y, player.radius + 16, 0, TAU);
      ctx.strokeStyle = `rgba(255, 96, 96, ${(0.5 * k).toFixed(3)})`;
      ctx.lineWidth = 2.5;
      ctx.stroke();
      ctx.fillStyle = `rgba(255, 96, 96, ${(0.1 * k).toFixed(3)})`;
      ctx.fill();
    }
    ctx.restore();
  }

  /**
   * 第五十三轮：气泡折行（按字符量宽，CJK 无空格也能断）。
   * 上限之内原样一行；超了就从右往左挤成多行。
   */
  _wrapBubbleText(ctx, text, maxW) {
    if (ctx.measureText(text).width <= maxW) return [text];
    const lines = [];
    let cur = '';
    for (const ch of text) {
      if (cur && ctx.measureText(cur + ch).width > maxW) {
        lines.push(cur);
        cur = ch;
      } else {
        cur += ch;
      }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  // =========================================================
  // 子弹
  // =========================================================
  _drawBullet(ctx, b) {
    // 第一百三十九轮 D：子弹视觉身份分家（用户："魔法棒、硬币、炮台
    // 全是长条+十字星，同质化太严重"）——
    //   element  → 元素光球（大圆 + 彗尾圆点 + 环绕小金星，无十字芒）
    //   bounce   → 旋转金币（翻滚椭圆 + 厚度线 + 速度线残影）
    //   turret   → 能量曳光弹（加粗长条 + 青色外晕 + 多角刺芒头 + 尾焰）
    //   其余     → 通用弹头椭圆 + 白芯（去掉千篇一律的十字星芒）
    const isCoin = b.behavior === 'bounce';
    const isOrb = b.kind === 'element';
    const isTracer = b.kind === 'turret';

    // ---- 拖尾 ----
    if (isCoin) {
      // 金币：不要圆形拖尾，画 3 道"速度线"（沿反方向平行错开的短线）
      if (b.trail.length > 0) {
        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.angle);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        ctx.strokeStyle = b.color;
        ctx.lineCap = 'round';
        for (let i = 0; i < 3; i++) {
          const off = (i - 1) * b.radius * 1.1;
          const ln = b.radius * (3.2 - i * 0.7);
          ctx.globalAlpha = 0.4 - i * 0.1;
          ctx.lineWidth = 1.6 - i * 0.3;
          ctx.beginPath();
          ctx.moveTo(-b.radius * 1.2, off);
          ctx.lineTo(-b.radius * 1.2 - ln, off * 1.4);
          ctx.stroke();
        }
        ctx.restore();
      }
    } else if (isOrb) {
      // 光球：彗星尾巴 —— trail 上取 4 个节点画渐小渐淡的同色圆点
      const pts = b.trail.filter((t) => t.a > 0);
      if (pts.length > 1) {
        ctx.save();
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        const n = Math.min(4, pts.length);
        for (let i = 0; i < n; i++) {
          const f = 1 - (i + 1) / (n + 1);        // 越靠后越小越淡
          const tp = pts[pts.length - 1 - i];
          ctx.globalAlpha = 0.34 * (0.4 + f * 0.6);
          ctx.fillStyle = b.color;
          ctx.beginPath();
          ctx.arc(tp.x, tp.y, b.radius * (0.35 + f * 0.6), 0, TAU);
          ctx.fill();
        }
        ctx.restore();
      }
    } else {
      // 曳光弹 / 通用：连续光带（第九轮原样，曳光弹加宽加亮）
      if (b.trail.length > 1) {
        ctx.save();
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        const pts = b.trail.filter((t) => t.a > 0);
        if (pts.length > 1) {
          const wMul = isTracer ? 2.1 : 1.7;
          ctx.globalAlpha = isTracer ? 0.4 : 0.34;
          ctx.strokeStyle = b.color;
          ctx.lineWidth = b.radius * wMul;
          ctx.beginPath();
          ctx.moveTo(pts[0].x, pts[0].y);
          for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
          ctx.globalAlpha = 0.5;
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = b.radius * (isTracer ? 0.75 : 0.55);
          ctx.beginPath();
          ctx.moveTo(pts[0].x, pts[0].y);
          for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
        ctx.restore();
      }
    }
    ctx.globalAlpha = 1;

    ctx.save();
    ctx.translate(b.x, b.y);
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';

    // 外发光：缓存精灵（数量最多的绘制对象，省每帧渐变）
    if (!this._fxSaturated()) {
      const gr = b.radius * 3.2;
      ctx.globalAlpha = 0.34;
      ctx.drawImage(this._glowSprite(b.color), -gr, -gr, gr * 2, gr * 2);
    }

    ctx.globalAlpha = 1;

    if (isCoin) {
      // ---- 旋转金币：翻滚椭圆 + 厚度竖线 + 高光 ----
      ctx.rotate(b.age * 8);
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.ellipse(0, 0, b.radius * 1.4, b.radius * 0.8, 0, 0, TAU);
      ctx.fill();
      // 厚度：沿短轴的中线（翻滚时像硬币侧面）
      ctx.strokeStyle = 'rgba(122,80,10,0.9)';
      ctx.lineWidth = Math.max(1, b.radius * 0.22);
      ctx.beginPath();
      ctx.moveTo(0, -b.radius * 0.8);
      ctx.lineTo(0, b.radius * 0.8);
      ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,0.85)';
      ctx.beginPath();
      ctx.arc(-b.radius * 0.45, -b.radius * 0.25, b.radius * 0.2, 0, TAU);
      ctx.fill();
    } else if (isOrb) {
      // ---- 元素光球：高亮大圆 + 白芯 + 环绕小金星 ----
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.arc(0, 0, b.radius * 1.15, 0, TAU);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.beginPath();
      ctx.arc(0, 0, b.radius * 0.5, 0, TAU);
      ctx.fill();
      // 环绕金色小星星（3 颗差速旋转）
      ctx.fillStyle = '#ffe08a';
      for (let i = 0; i < 3; i++) {
        const sa = b.age * 5 + (i / 3) * TAU;
        const sx = Math.cos(sa) * b.radius * 1.9;
        const sy = Math.sin(sa) * b.radius * 1.9;
        ctx.save();
        ctx.translate(sx, sy);
        ctx.rotate(sa * 2);
        this._starPath(ctx, b.radius * 0.4, b.radius * 0.16, 4);
        ctx.fill();
        ctx.restore();
      }
    } else if (isTracer) {
      // ---- 能量曳光弹：加粗长条 + 青色能量外晕 + 多角刺芒头 + 尾焰 ----
      ctx.rotate(b.angle);
      // 青色能量外晕
      ctx.globalAlpha = 0.3;
      ctx.strokeStyle = b.color;
      ctx.lineWidth = b.radius * 1.3;
      ctx.beginPath();
      ctx.ellipse(0, 0, b.radius * 2.3, b.radius * 1.25, 0, 0, TAU);
      ctx.stroke();
      // 主体长条（加粗）
      ctx.globalAlpha = 1;
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.ellipse(-b.radius * 0.3, 0, b.radius * 2.0, b.radius * 0.9, 0, 0, TAU);
      ctx.fill();
      // 尾部喷射焰：朝后的锥形（白→透明的能量喷射）
      ctx.globalAlpha = 0.55;
      ctx.fillStyle = '#ffffff';
      const flame = b.radius * (2.6 + Math.sin(b.age * 40) * 0.5);  // 抖动的焰长
      ctx.beginPath();
      ctx.moveTo(-b.radius * 1.2, -b.radius * 0.55);
      ctx.lineTo(-b.radius * 1.2 - flame, 0);
      ctx.lineTo(-b.radius * 1.2, b.radius * 0.55);
      ctx.closePath();
      ctx.fill();
      // 头部：爆发状多角刺芒（替代十字星）
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#ffffff';
      this._starPath(ctx, b.radius * 1.9, b.radius * 0.65, 6);
      ctx.fill();
    } else {
      // ---- 通用弹头：椭圆 + 白芯（十字星芒已除名）----
      ctx.rotate(b.angle);
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.ellipse(0, 0, b.radius * 1.5, b.radius, 0, 0, TAU);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.beginPath();
      ctx.arc(b.radius * 0.25, 0, b.radius * 0.42, 0, TAU);
      ctx.fill();
    }

    ctx.restore();
  }

  _drawEnemyBullet(ctx, b) {
    // 第十五轮 ④：水柱（激光）单独一条渲染路径。
    //
    // 用户要的是"类似激光水柱，很快"。一个圆球以 1400px/s 飞过去，
    // 视觉上仍然只是"一颗很快的子弹"—— 必须画出**长度**才有激光感。
    // 所以这里画一条从当前位置向后延伸的细长光束，两端渐隐，
    // 中心叠一层高亮，形成"水柱"的通透质感。
    if (b.laser) {
      ctx.save();
      ctx.translate(b.x, b.y);
      ctx.rotate(b.angle);
      const L = b.laserLength ?? 120;
      const w = b.radius;
      ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
      // 外层柔光（宽而淡）
      ctx.globalAlpha = 0.35;
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.moveTo(-L, -w * 2.2);
      ctx.lineTo(0, -w * 0.5);
      ctx.lineTo(0, w * 0.5);
      ctx.lineTo(-L, w * 2.2);
      ctx.closePath();
      ctx.fill();
      // 中层主体
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(-L * 0.9, -w);
      ctx.lineTo(0, -w * 0.35);
      ctx.lineTo(0, w * 0.35);
      ctx.lineTo(-L * 0.9, w);
      ctx.closePath();
      ctx.fill();
      // 内层高亮核心（近白，强化"激光"的灼亮感）
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.moveTo(-L * 0.75, -w * 0.35);
      ctx.lineTo(0, -w * 0.16);
      ctx.lineTo(0, w * 0.16);
      ctx.lineTo(-L * 0.75, w * 0.35);
      ctx.closePath();
      ctx.fill();
      // 弹头：前端一颗亮球，作为"水柱的头部"
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(0, 0, w * 0.9, 0, TAU);
      ctx.fill();
      ctx.restore();
      return;
    }

    // 第一百八十五轮：除了大玉(jade，带红色判定圈)之外的敌弹全部走 bullet-fx v2
    //（白核 + 彩色边框 + 柔光 + 余烬尾，7 种弹型）。下面是旧实现，只剩 jade 在用。
    if (b.shape !== 'jade') { BFX.drawEnemyBullet(ctx, b); return; }

    // 第九轮：敌方弹幕也要看得清，但**不能比自机子弹更亮** ——
    // 否则满屏弹幕时玩家分不清哪些是自己的伤害、哪些要躲。
    // 所以敌弹保留"实心球 + 单层光晕"的厚重感，不加白色星芒。
    //
    // 但加了**朝向后方的彗尾**：原来是个完全对称的球，
    // 静态看根本判断不出它往哪飞（用户要求"让弹幕类型丰富一些"，
    // 而可读性是弹幕游戏的第一优先）。
    // 这里不存历史轨迹数组 —— 敌弹数量在弹幕模式下可能上百，
    // 每颗存一串点的话内存和 GC 都会明显变差。
    // 敌弹是匀速直线运动，所以直接用 angle 现画一条渐变尾巴，O(1)。
    ctx.save();
    ctx.translate(b.x, b.y);

    // 第八十九轮：大玉（jade）—— 视觉直径 55-65px 的大弹，三层配色
    // （外圈白色柔光 → 中圈淡黄米白 → 核心深黄到黑），轻微发光不糊团。
    // 关键：判定比视觉小得多 —— 红色危险圈画在 hitRadius 边缘（真正
    // 的命中边界），白色外圈完全无判定。自带上色，不走公共彗尾/光晕
    //（那些按 b.color 染色，和大玉的固定配色不搭）。
    if (b.shape === 'jade') {
      const r = b.radius;
      const hr = b.hitRadius ?? r;
      // 第九十轮：大玉配色按基色取盘 —— 用户定调的改法：
      // "中圈 = 主色，内圈 = 深主色到偏黑的主色（径向渐变），外圈 =
      // 主色冲白的柔光"。既定色给手工调过的表；新颜色走 _jadePalette
      // 的通用推导，以后加色只要在表里补一行（或直接靠推导）。
      const P = this._jadePalette(b.jadeColor);
      // 轻微发光：外圈色的光晕精灵，低透明度（不糊成一团）
      ctx.globalAlpha = 0.3;
      ctx.drawImage(this._glowSprite(P.outer), -r * 1.5, -r * 1.5, r * 3, r * 3);
      ctx.globalAlpha = 1;
      // 外圈：柔光
      ctx.fillStyle = P.outer;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, TAU);
      ctx.fill();
      // 中圈：主色
      ctx.fillStyle = P.mid;
      ctx.beginPath();
      ctx.arc(0, 0, r * 0.62, 0, TAU);
      ctx.fill();
      // 核心：深主色到偏黑主色的径向渐变（边缘最亮、中心最暗）
      const core = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.36);
      core.addColorStop(0, P.ci);
      core.addColorStop(0.45, P.cm);
      core.addColorStop(1, P.co);
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(0, 0, r * 0.36, 0, TAU);
      ctx.fill();
      // 淡红危险轮廓 = 真实判定边缘（hitRadius），不是视觉边缘
      ctx.strokeStyle = 'rgba(255,70,70,0.55)';
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(0, 0, hr + 1.4, 0, TAU);
      ctx.stroke();
      ctx.restore();
      return;
    }

    // 彗尾：从子弹后方 3.2r 处消失，越靠近弹体越亮
    const tailLen = b.radius * 3.2;
    ctx.save();
    ctx.rotate(b.angle);
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
    // 用缓存的渐变条代替每帧新建 linearGradient。
    // drawImage 到 [-tailLen, 0] 这一段，宽高按半径缩放。
    const tailH = b.radius * 1.1;
    ctx.globalAlpha = 0.5;
    ctx.drawImage(this._tailSprite(b.color), -tailLen, -tailH / 2, tailLen, tailH);
    ctx.restore();

    // 外光晕（缓存精灵）
    const gr = b.radius * 2.4;
    ctx.globalAlpha = 0.42;
    ctx.drawImage(this._glowSprite(b.color), -gr, -gr, gr * 2, gr * 2);
    ctx.globalAlpha = 1;

    // 第八十八轮：弹幕形状 —— longball 长球（椭圆）/ crystal 晶体（尖头
    // 叶片）。都按**飞行方向**对齐（原版观感：弹幕朝哪飞长轴指向哪）；
    // 圆球 orb 保持自转 b.rotation。碰撞判定不在这里，radius 不变。
    if (b.shape === 'longball' || b.shape === 'crystal') {
      ctx.rotate(b.angle);
      const r = b.radius;
      if (b.shape === 'longball') {
        // 长球：长:短 ≈ 2.2:1（原版长轴 8-10px / 短轴 4-5px 的比例）
        const rx = r * 2.2, ry = r;
        // 描边底：从弹色衍生的深色描边（第九十一轮 —— 用户要金黄长椭圆
        // "外圈金色描边，比较细，但清晰"；深红/薄荷等各色同样成立）
        ctx.fillStyle = this._shade(b.color, 0.38);
        ctx.beginPath();
        ctx.ellipse(0, 0, rx + 1.2, ry + 1.2, 0, 0, TAU);
        ctx.fill();
        // 淡红危险轮廓（与圆球弹同一视觉规则）
        ctx.strokeStyle = 'rgba(255,70,70,0.55)';
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.ellipse(0, 0, rx + 1.4, ry + 1.4, 0, 0, TAU);
        ctx.stroke();
        // 本体
        ctx.fillStyle = b.color;
        ctx.beginPath();
        ctx.ellipse(0, 0, rx, ry, 0, 0, TAU);
        ctx.fill();
        // 前端高亮（长球的空间感靠"头亮尾暗"）
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.beginPath();
        ctx.ellipse(rx * 0.28, 0, rx * 0.34, ry * 0.42, 0, 0, TAU);
        ctx.fill();
      } else {
        // 晶体：两端尖锐的叶片菱形，中间最宽（长:短 ≈ 2.6:1）
        const L = r * 2.6, W = r * 1.0;
        const leaf = (scale) => {
          ctx.beginPath();
          ctx.moveTo(L * scale, 0);
          ctx.quadraticCurveTo(0, -W * 1.4 * scale, -L * scale, 0);
          ctx.quadraticCurveTo(0, W * 1.4 * scale, L * scale, 0);
          ctx.closePath();
        };
        // 深色描边底 + 淡红危险轮廓
        ctx.fillStyle = 'rgba(0,0,0,0.35)';
        leaf(1.14);
        ctx.fill();
        ctx.strokeStyle = 'rgba(255,70,70,0.55)';
        ctx.lineWidth = 1.6;
        leaf(1.26);
        ctx.stroke();
        // 本体（紫粉系弹色）+ 中心偏白的芯
        ctx.fillStyle = b.color;
        leaf(1);
        ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,0.8)';
        leaf(0.45);
        ctx.fill();
      }
      ctx.restore();
      return;
    }

    ctx.rotate(b.rotation);

    // 深色描边：让弹幕在浅色背景/特效堆里也能读出边界
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.beginPath();
    ctx.arc(0, 0, b.radius + 1.2, 0, TAU);
    ctx.fill();

    // 淡红色轮廓（第十轮，用户要求）。
    //
    // 起因：地图上分散的经验/材料掉落物太多，玩家在混战里分不清
    // "这颗东西是会打死我的弹幕"还是"这颗是我要去捡的钱"。
    // 掉落物用的是绿/黄/红方块，所以给**敌弹**统一加一圈淡红描边，
    // 形成"带红圈 = 危险"的稳定视觉规则。
    //
    // 为什么是淡红而不是亮红：满屏弹幕时亮红会糊成一片红，反而更乱。
    // 这里只用 0.55 透明度、1.6px 宽，是"余光能扫到"的程度。
    // 画在深色描边**之外**（半径 +1.4），所以红圈不会被盖住。
    ctx.strokeStyle = 'rgba(255,70,70,0.55)';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(0, 0, b.radius + 1.4, 0, TAU);
    ctx.stroke();

    ctx.fillStyle = b.color;
    ctx.beginPath();
    ctx.arc(0, 0, b.radius, 0, TAU);
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    ctx.arc(-b.radius * 0.25, -b.radius * 0.25, b.radius * 0.36, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  /** 颜色工具：hex 往黑压 k（0~1），带缓存（第九十一轮，椭圆蛋描边用） */
  _shade(hex, k) {
    const cache = (this._shadeCache ??= new Map());
    const key = `${hex}|${k}`;
    if (cache.has(key)) return cache.get(key);
    const n = parseInt((hex ?? '#ffffff').slice(1), 16);
    const f = (v) => Math.round(v * (1 - k)).toString(16).padStart(2, '0');
    const out = `#${f((n >> 16) & 255)}${f((n >> 8) & 255)}${f(n & 255)}`;
    cache.set(key, out);
    return out;
  }

  /**
   * 第九十轮：大玉配色盘 —— 按基色返回 {outer, mid, ci, cm, co}。
   * 用户定调的改法："中圈主色，内圈深主色到偏黑的主色，外圈主色冲白"。
   *   · 既定色（手工调过）直接查表：默认黄盘保留第八十九轮原配色；
   *     粉盘（血痕自机狙）中圈粉、核心深粉到偏黑粉；
   *   · 其他新颜色走通用推导：外圈 = 主色冲白 82%，中圈 = 主色冲白 28%，
   *     核心 = 主色压黑 90% → 压黑 62% → 主色。以后改各种颜色大玉蛋，
   *     只要给 emit 传 jadeColor 就行，推导盘直接可用。
   */
  _jadePalette(base) {
    const cache = (this._jadePalCache ??= new Map());
    if (cache.has(base)) return cache.get(base);
    let P;
    if (base === '#d99a1e' || !base) {
      P = { outer: '#fdfaf2', mid: '#f6ecc8', ci: '#120c04', cm: '#3a2606', co: '#d99a1e' };
    } else if (base === '#ff7f9f') {   // COLORS.pink：粉色大玉蛋（血痕）
      P = { outer: '#fdf3f6', mid: '#ff9db4', ci: '#1c040b', cm: '#66102a', co: '#ff7f9f' };
    } else {
      const mix = (hex, to, k) => {
        const n = parseInt(hex.slice(1), 16);
        const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        const t = to === 'w' ? 255 : 0;
        const f = (v) => Math.round(v + (t - v) * k).toString(16).padStart(2, '0');
        return `#${f(r)}${f(g)}${f(b)}`;
      };
      P = {
        outer: mix(base, 'w', 0.82), mid: mix(base, 'w', 0.28),
        ci: mix(base, 'b', 0.9), cm: mix(base, 'b', 0.62), co: base,
      };
    }
    cache.set(base, P);
    return P;
  }

  /**
   * 按长边贴合缩放画一张图（保持宽高比、以原点为中心）。
   * 第十九轮加的：宝箱/经验/金币的素材都不是正方形，
   * 直接塞方框会压扁 —— 统一走这里。
   */
  _drawFit(ctx, img, box) {
    const k = box / Math.max(img.naturalWidth, img.naturalHeight);
    const w = img.naturalWidth * k;
    const h = img.naturalHeight * k;
    ctx.drawImage(img, -w / 2, -h / 2, w, h);
  }

  // =========================================================
  // 拾取物
  // =========================================================
  _drawPickup(ctx, p) {
    const bob = Math.sin(p.bobPhase) * 2;
    ctx.save();
    ctx.translate(p.x, p.y + bob);

    if (p.type === PICKUP_TYPE.XP) {
      // 第十九轮：经验宝石贴图（素材 经验.png）。16px：原来的几何块是 8px
      // 见方，贴图带造型细节，略放大才认得出，又不会比医疗包还显眼。
      const img = sprites.get('pickup', 'xp');
      if (img) {
        this._drawFit(ctx, img, 16);
      } else {
        ctx.fillStyle = '#6ee7a8';
        ctx.rotate(Math.PI / 4);
        ctx.fillRect(-4, -4, 8, 8);
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.fillRect(-2, -2, 3, 3);
      }
    } else if (p.type === PICKUP_TYPE.MATERIAL) {
      // 第十九轮：金币贴图（素材 金币1/金币2），每枚掉落随机选一张
      // （p.variant 在 Pickup 构造时定死），满地混排时两种造型自然交错。
      // 18px：和经验宝石同级，比原来的 9px 几何块大一号。
      const img = sprites.get('pickup', p.variant === 2 ? 'coin_2' : 'coin_1');
      if (img) {
        this._drawFit(ctx, img, 18);
      } else {
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = '#ffc857';
        ctx.fillRect(-4.5, -4.5, 9, 9);
        ctx.fillStyle = 'rgba(255,255,255,0.55)';
        ctx.fillRect(-2, -2, 3.5, 3.5);
      }
    } else {
      // 生命：血包。
      //
      // 第十轮（用户要求）：用他自己放进来的"魔爪"能量饮料图当图标。
      // 尺寸刻意和原来的红十字**差不多大**（用户原话："大小和红加号差不多，
      // 不要太大"）—— 红十字的横向跨度是 12px，所以这里画成 14px 见方
      // （红十字 12 宽 12 高，14 是加了一点点让它不至于显得比原来小）。
      //
      // 第十二轮（用户反馈）："魔爪医疗包图标放大50%，不然都看不出来是魔爪，
      // 目前太瘦了。" —— 15 → 22.5。
      //
      // 第十八轮 ⑩（用户反馈）："加大魔爪医疗包图标 100%。还是太小了。"
      //   —— 22.5 → 45（再翻一倍，相对最初的 15 是 3 倍）。
      //
      // 为什么"太瘦"的根因是**尺寸**而不是贴图本身：
      //   源图是 64×64，但魔爪罐子本身是竖长条，实测内容外接框只有
      //   25×60（alpha-bbox）。塞进 15px 的方框后，罐子只剩 15px 高、
      //   约 6px 宽 —— 在那个尺寸下看起来就是一根竖线，认不出是罐子。
      //   22.5 时宽度才到 9px，用户仍然说小，所以这次直接给到 45：
      //   罐子高 45px、宽约 11px，是"一眼能认出是瓶饮料"的尺寸。
      const healImg = sprites.get('pickup', 'heal');
      if (healImg) {
        // 原图是竖长条（罐子），在方框里按长边贴合，
        // 也就是宽度会自动变窄 —— 正好是罐子的真实比例。
        // 45 = 15 × 3（第十二轮 ×1.5，第十八轮再 ×2）
        const S = 45;
        // 呼吸感的轻微缩放，让地上的血包不显得是贴上去的静态图
        const pulse = 1 + Math.sin(p.bobPhase * 1.6) * 0.06;
        ctx.drawImage(healImg, -S / 2 * pulse, -S / 2 * pulse, S * pulse, S * pulse);
      } else {
        ctx.fillStyle = '#ff5f6d';
        ctx.fillRect(-6, -2, 12, 4);
        ctx.fillRect(-2, -6, 4, 12);
      }
    }
    ctx.restore();
  }

  // =========================================================
  // 特效
  // =========================================================
  // =========================================================
  // 第一百三十九轮 C：三层刀光（SLASH / SPIN 共用）
  // =========================================================
  //
  // 用户："一个半透明的月牙弧线出现一瞬间就消失，像塑料片，没有切割感。"
  // 新结构（外→内）：
  //   · 最外层 尖锐亮白 高光（最宽，运动方向感来源）
  //   · 中间层 青色 主体
  //   · 最内层 极细深蓝 核心
  // 两端（剑尖/剑柄）用锥形渐变（createConicGradient）渐隐到全透明；
  // 整体加法混合，暗色地板上像在发光。内核不支持 conic 时退回实线
  //（旧内核只是少了渐隐，三层结构仍在）。
  _bladeArc(ctx, r, a0, a1, fade, phaseShift = 0) {
    ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
    ctx.lineCap = 'round';
    const span = a1 - a0;
    const useConic = typeof ctx.createConicGradient === 'function';
    const layers = [
      { col: '#ffffff', w: 10 * fade + 2.5, rr: r * 1.02, al: 0.92 },  // 白高光
      { col: '#7ee7ff', w: 6.5 * fade + 2, rr: r * 0.94, al: 0.8 },    // 青主体
      { col: '#1e3a8a', w: 2.2 * fade + 0.8, rr: r * 0.86, al: 0.85 }, // 深蓝核
    ];
    for (const L of layers) {
      ctx.globalAlpha = L.al * fade;
      if (useConic) {
        const g = ctx.createConicGradient(a0 + phaseShift, 0, 0);
        const q = Math.min(0.5, span / Math.PI * 2 / 2); // span/TAU（防 0）
        g.addColorStop(0, 'rgba(255,255,255,0)');
        g.addColorStop(Math.min(0.999, q * 0.18), L.col);
        g.addColorStop(Math.min(0.9999, q * 0.82), L.col);
        g.addColorStop(Math.min(1, q), 'rgba(255,255,255,0)');
        g.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.strokeStyle = g;
      } else {
        ctx.strokeStyle = L.col;
      }
      ctx.lineWidth = L.w;
      ctx.beginPath();
      ctx.arc(0, 0, L.rr, a0, a1);
      ctx.stroke();
    }
  }

  _drawFX(ctx, fx) {
    const k = clamp(fx.t / fx.life, 0, 1);
    const fade = 1 - k;

    switch (fx.type) {
      case FX_TYPE.SLASH: {
        // 第一百三十九轮 C 重构（用户："像塑料片，没有切割感"）：
        // 三层刀光（白高光/青主体/深蓝核，见 _bladeArc）+ 两端锥形渐隐
        // + 加法混合；残影弧相位后移制造"挥过去"的运动方向感。
        // 火星粒子由 fx.slash() 生成（SHARD 类型单独渲染）。
        const half = (fx.arcDeg * Math.PI / 180) / 2;
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.rotate(fx.angle);
        const r = fx.range * (0.62 + k * 0.5);

        // 残影弧：武器本色、相位后移（落后于主刀光）→ 运动模糊。
        // 饱和时跳过（满屏特效里只是糊）。
        if (!this._fxSaturated()) {
          ctx.save();
          ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
          ctx.globalAlpha = fade * 0.26;
          ctx.strokeStyle = fx.color;
          ctx.lineWidth = 15 * fade + 3;
          ctx.beginPath();
          ctx.arc(0, 0, r * 1.04, -half - 0.14, half - 0.14);
          ctx.stroke();
          ctx.restore();
        }

        this._bladeArc(ctx, r, -half, half, fade);
        ctx.restore();
        break;
      }

      case FX_TYPE.IMPACT: {
        // 近战命中的核心表现：放射星芒 + 白核 + 加色发光
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.rotate(fx.angle);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';

        // 中心白核：快速膨胀然后消失。
        // 用缓存的径向精灵代替 createRadialGradient —— 形状固定、只有颜色变，
        // 而这是全项目最热的特效路径（基准里 IMPACT 单项 68µs）。
        //
        // 饱和时跳过：这颗核是**最宽**的一层，填充率最高，
        // 而在满屏特效里它只是无数个亮点之一，看不出来。
        const coreR = (fx.power * 10 + 5) * (0.4 + k * 1.2);
        if (!this._fxSaturated()) {
          ctx.globalAlpha = fade * 0.95;
          ctx.drawImage(this._glowSprite(fx.color), -coreR, -coreR, coreR * 2, coreR * 2);
        }

        // 放射星芒：长短交替，像漫画的"啪"字效果。
        //
        // 第十轮性能优化：原来每根星芒都是独立的 beginPath + stroke，
        // 12 根就是 12 次绘制调用。基准测下来**每次绘制调用的固定开销**
        // 才是真正的瓶颈（新建渐变几乎不花钱，切换 blend 模式也只 ~1µs）。
        // 所以按"长/短"分两组，每组只起一条 path、只 stroke 一次：
        // 12 次调用 → 2 次，画出来的东西完全一样。
        // 放射星芒 → 第一百三十九轮 E：锐利尖刺（用户："不要温柔的线，
        // 要爆开的锐刺"）—— 每根刺是外尖内宽的细长三角，长短交替。
        // 仍按长/短分两组，每组一条 path 一次 fill（第十轮的性能约定不变）。
        ctx.globalAlpha = fade;
        ctx.fillStyle = '#ffffff';
        for (const long of [true, false]) {
          ctx.beginPath();
          for (let i = 0; i < fx.rays; i++) {
            if ((i % 2 === 0) !== long) continue;
            const a2 = (i / fx.rays) * TAU;
            const r1 = (6 + fx.power * 8) * (0.5 + k * 0.9);
            const r2 = r1 + (long ? 20 : 11) * (0.6 + fx.power * 0.9) * (0.4 + fade * 0.8);
            const ca = Math.cos(a2), sa = Math.sin(a2);
            const bw = (long ? 2.6 : 1.6) * (0.5 + fade * 0.6);   // 根部半宽
            // 细长三角：内缘两点 + 外尖一点
            ctx.moveTo(ca * r1 - sa * bw, sa * r1 + ca * bw);
            ctx.lineTo(ca * r2, sa * r2);                         // 锐利外尖
            ctx.lineTo(ca * r1 + sa * bw, sa * r1 - ca * bw);
          }
          ctx.fill();
        }
        ctx.restore();
        break;
      }

      case FX_TYPE.SHOCKWAVE: {
        // 又粗又钝的压缩环：外圈厚、内圈空，配重击
        ctx.save();
        ctx.translate(fx.x, fx.y);
        const rr = fx.radius * (0.35 + k * 1.15);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        // 外环
        ctx.globalAlpha = fade * 0.85;
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = 14 * fade + 2;
        ctx.beginPath();
        ctx.arc(0, 0, rr, 0, TAU);
        ctx.stroke();
        // 白色薄边贴在外环外侧
        ctx.globalAlpha = fade * 0.7;
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 3 * fade + 0.5;
        ctx.beginPath();
        ctx.arc(0, 0, rr + 6 * fade, 0, TAU);
        ctx.stroke();
        ctx.restore();
        break;
      }

      case FX_TYPE.SHARD: {
        // 带旋转和轻微重力的碎屑。
        // ox/oy 是出生偏移（见 fx.js 的 shards 注释）：让碎片别糊在爆炸白核上。
        const x = fx.x + (fx.ox ?? 0) + fx.vx * fx.t;
        // 0.5 的"重力"让碎片有下坠弧线，比直线飞散更有实体感
        const y = fx.y + (fx.oy ?? 0) + fx.vy * fx.t + 90 * fx.t * fx.t;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(fx.rot + fx.spin * fx.t);
        ctx.globalAlpha = fade;
        ctx.fillStyle = fx.color;
        // 三角碎片比方形更像"碎掉的壳"
        const s2 = fx.size * (0.5 + fade * 0.7);
        ctx.beginPath();
        ctx.moveTo(0, -s2);
        ctx.lineTo(s2 * 0.9, s2 * 0.8);
        ctx.lineTo(-s2 * 0.9, s2 * 0.7);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
        break;
      }

      case FX_TYPE.THRUST: {
        // 第一百三十九轮 E 重构（用户："像一根白棍子，完全没有刺穿感"）：
        //   1. 梭形枪影：前端收成锐尖、根部略粗（两侧 quadratic 鼓肚），
        //      渐变从剑尖纯白 → 冷蓝 → 半透明银灰尾；
        //   2. 速度残影：主干两侧 3 道极细半透明线跟着刺出；
        //   3. 顿帧：出现后满长钉住 0.05s（k<0.28），然后尖端迅速缩回
        //      （不是平滑淡出）。
        ctx.save();
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        const dx = fx.ex - fx.x;
        const dy = fx.ey - fx.y;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;      // 前进方向
        const px = -uy, py = ux;                 // 垂直方向

        // ---- 顿帧曲线：短暂停留 → 迅速缩回 ----
        const HOLD = 0.28;
        const shrink = k < HOLD ? 1 : 1 - Math.pow((k - HOLD) / (1 - HOLD), 1.5);
        const alpha = k < HOLD ? 1 : Math.max(0, 1 - ((k - HOLD) / (1 - HOLD)) * 1.05);
        const tip = len * shrink;                // 尖端位置（缩回时往回拉）
        const tx = fx.x + ux * tip, ty = fx.y + uy * tip;
        const w = fx.width * (0.8 + 0.2 * shrink);

        // ---- 梭形主体（尖端白 → 冷蓝 → 半透明尾）----
        const grad = ctx.createLinearGradient(tx, ty, fx.x, fx.y);
        grad.addColorStop(0, 'rgba(255,255,255,0.98)');   // 剑尖高亮纯白
        grad.addColorStop(0.3, '#cfe4ff');                // 冷蓝
        grad.addColorStop(1, 'rgba(150,170,205,0.22)');   // 银灰半透明尾
        ctx.globalAlpha = alpha * 0.95;
        ctx.fillStyle = grad;
        const mxp = fx.x + ux * tip * 0.55, myp = fx.y + uy * tip * 0.55;
        ctx.beginPath();
        ctx.moveTo(tx, ty);                                        // 锐尖
        ctx.quadraticCurveTo(mxp + px * w * 0.62, myp + py * w * 0.62,
          fx.x + px * w * 0.5, fx.y + py * w * 0.5);               // 上腹鼓肚
        ctx.lineTo(fx.x - px * w * 0.5, fx.y - py * w * 0.5);      // 根部略粗
        ctx.quadraticCurveTo(mxp - px * w * 0.62, myp - py * w * 0.62,
          tx, ty);                                                 // 下腹鼓肚
        ctx.closePath();
        ctx.fill();

        // ---- 速度残影：3 道极细半透明线（两侧+后方，比主干短一截）----
        ctx.strokeStyle = '#cfe4ff';
        ctx.lineCap = 'round';
        for (let i = 0; i < 3; i++) {
          const off = (i - 1) * w * 0.85;
          const back = tip * (0.2 + i * 0.08);     // 各道起始位置错开
          ctx.globalAlpha = alpha * (0.22 - i * 0.05);
          ctx.lineWidth = 1.1;
          ctx.beginPath();
          ctx.moveTo(fx.x + ux * back * 0.35 + px * off, fx.y + uy * back * 0.35 + py * off);
          ctx.lineTo(fx.x + ux * (tip * 0.82 - back * 0.2) + px * off, fx.y + uy * (tip * 0.82 - back * 0.2) + py * off);
          ctx.stroke();
        }

        // ---- 剑尖小星芒（替代圆斑，锐利收尾）----
        if (shrink > 0.25) {
          ctx.globalAlpha = alpha;
          ctx.fillStyle = '#ffffff';
          ctx.save();
          ctx.translate(tx, ty);
          ctx.rotate(fx.t * 3);
          this._starPath(ctx, fx.width * 0.55, fx.width * 0.2, 4);
          ctx.fill();
          ctx.restore();
        }
        ctx.restore();
        break;
      }

      case FX_TYPE.SPIN: {
        // 第一百三十九轮 C：咸鱼盾等全弧旋击也换三层刀光（用户：
        // "全弧形的咸鱼盾之类的，都可以改改"）—— 弧随进度旋转扫过，
        // 两端锥形渐隐，加法混合发光。
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.rotate(k * Math.PI * 1.7);
        this._bladeArc(ctx, fx.range * (0.78 + k * 0.14), -0.35, Math.PI * 0.7, fade);
        ctx.restore();
        break;
      }

      case FX_TYPE.BEAM: {
        ctx.save();
        ctx.globalAlpha = fade;
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = fx.width * (0.4 + fade * 0.6);
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(fx.x, fx.y);
        ctx.lineTo(fx.ex, fx.ey);
        ctx.stroke();
        ctx.globalAlpha = fade * 0.9;
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = fx.width * 0.3 * fade;
        ctx.beginPath();
        ctx.moveTo(fx.x, fx.y);
        ctx.lineTo(fx.ex, fx.ey);
        ctx.stroke();
        ctx.restore();
        break;
      }

      case FX_TYPE.NOVA: {
        // 第一百三十九轮 C 重构（用户："半透明实心黄圈像一滩果冻"）：
        // 铃铛音波共振 —— 取消实心填充，改 3 层粗细/速度不同的波浪环
        //（金→淡黄→透明），加法混合发光；环边缘随机音符/爪印伴飞淡出；
        // 圆心一记闪光十字星"叮"一下即逝。节奏前快后慢（音波撞空气）。
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        const R = fx.radius;
        const ek = 1 - Math.pow(1 - k, 2.6);   // 前半段极快，后半段骤减

        // 圆心闪光十字星：只在最初一瞬（k<0.22）—— F 轮整体降亮一点
        if (k < 0.22) {
          const star = (1 - k / 0.22) * 0.8;
          ctx.globalAlpha = star;
          ctx.strokeStyle = '#fff6d0';
          ctx.lineWidth = 2;
          const L1 = 14 * star + 3;
          ctx.beginPath();
          for (let i = 0; i < 4; i++) {
            const a = i * Math.PI / 2 + Math.PI / 4;
            ctx.moveTo(Math.cos(a) * 3, Math.sin(a) * 3);
            ctx.lineTo(Math.cos(a) * L1, Math.sin(a) * L1);
          }
          ctx.stroke();
          ctx.globalAlpha = star;
          ctx.drawImage(this._glowSprite('#ffffff'), -7, -7, 14, 14);
        }

        // 三层波浪音波环：外粗扩散快、内细稍慢；F 轮细调（用户：
        // "波浪边缘调圆滑一点、中心亮度降一点，像真实空气震波"）——
        // 扭曲幅度 0.028→0.011、分段 34→56（曲线更圆滑），三层整体降亮。
        const rings = [
          { w: 6.5, sp: 1.0, col: '#ffd23a', al: 0.52 },  // 外圈：亮金
          { w: 4.0, sp: 0.8, col: '#ffe98a', al: 0.42 },  // 中圈：淡金
          { w: 2.0, sp: 0.6, col: '#fff6d0', al: 0.36 },  // 内圈：近白淡黄
        ];
        const SEG = 56;
        for (const rg of rings) {
          const rr = R * (0.16 + Math.pow(ek, rg.sp) * 0.94);
          ctx.globalAlpha = rg.al * fade;
          ctx.strokeStyle = rg.col;
          ctx.lineWidth = rg.w * fade + 0.6;
          ctx.beginPath();
          for (let i = 0; i <= SEG; i++) {
            const a = (i / SEG) * TAU;
            const wob = 1 + Math.sin(a * 9 + k * 38) * 0.011;  // 轻微震颤
            const px = Math.cos(a) * rr * wob;
            const py = Math.sin(a) * rr * wob;
            if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
          }
          ctx.closePath();
          ctx.stroke();
        }

        // 音符/猫爪印伴飞：各自进度贴着环边缘，旋转着淡出
        if (fx.notes) {
          ctx.font = '13px "Segoe UI Emoji", "Segoe UI", sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          for (const nt of fx.notes) {
            const dd = R * (0.14 + Math.pow(ek, nt.sp) * 1.0);
            ctx.save();
            ctx.translate(Math.cos(nt.a) * dd, Math.sin(nt.a) * dd);
            ctx.rotate(nt.rot + k * nt.spin);
            ctx.globalAlpha = fade * 0.95;
            ctx.font = `${nt.size}px "Segoe UI Emoji", "Segoe UI", sans-serif`;
            ctx.fillText(nt.ch, 0, 0);
            ctx.restore();
          }
        }
        ctx.restore();
        break;
      }

      case FX_TYPE.EXPLOSION: {
        // 第九轮升级：原来只有一层径向渐变，视觉上"糊成一团橙色"。
        // 现在分三层：白热核心 → 主火球 → 外圈冲击环，
        // 并且用加色混合，让它在弹幕堆里也能"亮"出来。
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        const r = fx.radius * (0.35 + k * 0.85);

        // 外圈冲击环：给爆炸一个明确的"边界"，否则边缘会糊
        ctx.globalAlpha = fade * 0.55;
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = 7 * fade + 1.5;
        ctx.beginPath();
        ctx.arc(0, 0, r * 0.92, 0, TAU);
        ctx.stroke();

        // 主火球：**中心留给白核**，所以用"从 color 渐隐"的精灵，
        // 而不是从白色开始 —— 否则白核叠在白色上互相抵消。
        // 同样是缓存精灵，省掉每帧新建渐变。
        ctx.globalAlpha = fade * 0.9;
        ctx.drawImage(this._glowSprite(fx.color), -r, -r, r * 2, r * 2);

        // 白热核心：一颗小而极亮的芯，这是"炸"的关键。
        // 不乘 fade 之外的衰减 —— 爆炸最亮的就是最初那一瞬。
        const coreR = r * (0.46 - k * 0.16);
        if (coreR > 0.5) {
          ctx.globalAlpha = fade;
          ctx.drawImage(this._glowSprite('#ffffff'), -coreR, -coreR, coreR * 2, coreR * 2);
        }
        ctx.restore();
        break;
      }

      case FX_TYPE.CHAIN: {
        // 第九轮升级：原来只是一条 4px 的线，视觉上像占位符。
        // 现在做成真正的闪电：
        //   1. 三层叠加（外光晕 / 主电光 / 白核），有体积感
        //   2. 折线中段随机抖动（每帧重算 → 天然的"噼啪闪烁"）
        //   3. 随机分叉的细支流
        // 抖动每帧重算是有意的：闪电本来就该闪，静态的闪电反而假。
        ctx.save();
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';

        // 先把折线路径算出来（主路径 + 分叉），三层共用同一批点，
        // 否则每层抖动不同会画成三条互不相干的线。
        //
        // 抖动幅度从 ±24 提到 ±34，并且**沿垂直于主方向抖动** ——
        // 纯随机 xy 抖动会把折角磨圆，看起来像"弯曲的发光线"；
        // 垂直抖动才能做出闪电那种尖锐的锯齿。
        const pts = [];
        for (let i = 0; i < fx.points.length; i++) {
          const p = fx.points[i];
          if (i === 0) { pts.push({ x: p.x, y: p.y, branch: null }); continue; }
          const prev = fx.points[i - 1];
          const dx = p.x - prev.x, dy = p.y - prev.y;
          const segLen = Math.hypot(dx, dy) || 1;
          const nx = -dy / segLen, ny = dx / segLen;   // 垂直方向
          const mid = 0.35 + Math.random() * 0.3;      // 折点不居中，更自然
          const jitter = (Math.random() - 0.5) * 34;
          const mx = prev.x + dx * mid + nx * jitter;
          const my = prev.y + dy * mid + ny * jitter;

          // 分叉：从折点再甩一根，方向偏回主方向附近（不是乱飞）
          const hasBranch = Math.random() < 0.75;
          let branch = null;
          if (hasBranch) {
            const ba = Math.atan2(dy, dx) + (Math.random() - 0.5) * 1.9;
            const blen = 18 + Math.random() * 34;
            branch = { x: mx + Math.cos(ba) * blen, y: my + Math.sin(ba) * blen };
          }
          pts.push({ x: mx, y: my, branch });
          pts.push({ x: p.x, y: p.y, branch: null });
        }

        const strokePath = () => {
          ctx.beginPath();
          ctx.moveTo(pts[0].x, pts[0].y);
          for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
          ctx.stroke();
        };

        // 1) 外光晕
        ctx.globalAlpha = fade * 0.45;
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = 13;
        strokePath();

        // 2) 主电光
        ctx.globalAlpha = fade * 0.95;
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = 5.5;
        strokePath();

        // 3) 白核
        ctx.globalAlpha = fade;
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        strokePath();

        // 4) 分叉（比主干细、偏白）
        ctx.globalAlpha = fade * 0.8;
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        for (const p of pts) {
          if (!p.branch) continue;
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.branch.x, p.branch.y);
        }
        ctx.stroke();
        ctx.restore();
        break;
      }

      case FX_TYPE.SPARKLE: {
        const x = fx.x + fx.vx * fx.t;
        const y = fx.y + fx.vy * fx.t;
        ctx.globalAlpha = fade;
        ctx.fillStyle = fx.color;
        ctx.beginPath();
        ctx.arc(x, y, fx.size * fade, 0, TAU);
        ctx.fill();
        ctx.globalAlpha = 1;
        break;
      }

      case FX_TYPE.HIT: {
        // 第九轮升级：加色发光 + 中心白闪，让"打中了"更明显。
        //
        // 第十轮性能优化：这是**触发最频繁**的特效（远程武器每次命中都放一个），
        // 原来 6 根放射线各起一条 path、各 stroke 一次 —— 实测 29 次绘制调用。
        // 现在按线宽分两组批量画，6 次 stroke → 2 次，调用数砍掉一半以上。
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
        ctx.globalAlpha = fade;
        // 中心白闪
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(0, 0, 3 + k * 4, 0, TAU);
        ctx.fill();
        // 放射线：长短/粗细交替，按"粗"和"细"分两批
        ctx.strokeStyle = fx.color;
        ctx.lineCap = 'round';
        const r1 = 4 + k * 8;
        const r2 = 8 + k * 16;
        for (const thick of [true, false]) {
          ctx.lineWidth = (thick ? 3 : 1.6) * fade + 0.6;
          ctx.beginPath();
          for (let i = 0; i < 6; i++) {
            if ((i % 2 === 0) !== thick) continue;
            const a = (i / 6) * TAU + 0.4;
            ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
            ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
          }
          ctx.stroke();
        }
        ctx.restore();
        break;
      }

      case FX_TYPE.RING: {
        // 第一百三十九轮 E：完美正圆 → 破碎虚线圈（用户："正圆太温柔，
        // 快速扩大后立刻碎裂消失"）—— 虚线段 + 随机起角，前半段扩张、
        // 后半段碎裂散淡。
        ctx.save();
        ctx.translate(fx.x, fx.y);
        ctx.rotate(fx.seed ?? 0);
        const crack = k > 0.55 ? (1 - k) / 0.45 : 1;   // 碎裂淡出曲线
        ctx.globalAlpha = fade * (k > 0.55 ? crack * crack : 0.7);
        ctx.strokeStyle = fx.color;
        ctx.lineWidth = 3 * fade + 1;
        ctx.setLineDash([fx.radius * 0.34, fx.radius * 0.2]);
        ctx.beginPath();
        ctx.arc(0, 0, fx.radius * (0.4 + k * 0.8), 0, TAU);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
        break;
      }

      default: break;
    }
  }

  _drawDamageText(ctx, fx) {
    const k = clamp(fx.t / fx.life, 0, 1);
    const alpha = k < 0.7 ? 1 : 1 - (k - 0.7) / 0.3;
    const y = fx.y + fx.vy * fx.t;
    const scale = fx.isCrit ? 1 + (1 - k) * 0.35 : 1;

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(fx.x, y);
    ctx.scale(scale, scale);
    ctx.font = fx.isCrit
      ? '800 19px "PingFang SC", "Microsoft YaHei", sans-serif'
      : '700 14px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,0.75)';
    ctx.strokeText(fx.text, 0, 0);
    ctx.fillStyle = fx.color;
    ctx.fillText(fx.text, 0, 0);
    ctx.restore();
  }

  // =========================================================
  // 屏幕叠加：受伤红边、Boss 血条、小地图
  // =========================================================
  _drawScreenOverlay(ctx, world, player, W, H) {
    // ---- 第四十九轮：芙兰朵露战 HUD（右上角符卡名 + 消弹冷却）----
    const flandre = world.flandreFight;
    // 第一百八十五轮：符卡名铭牌 + 消弹冷却进度条 + EP 倒计时 + 宣言白闪（旧的纯文字版已退役）
    if (flandre && flandre.spellName) FR.drawHud(ctx, flandre, W, H, this.dpr, PURGE_CD);

    // 受伤红边
    const hpPct = player.hp / player.maxHp;
    if (hpPct < 0.35) {
      const intensity = (0.35 - hpPct) / 0.35;
      const pulse = 0.5 + Math.sin(player.age * 5) * 0.5;
      const grad = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.32, W / 2, H / 2, Math.max(W, H) * 0.62);
      grad.addColorStop(0, 'rgba(255,0,40,0)');
      grad.addColorStop(1, `rgba(255,0,40,${0.18 + intensity * 0.3 * pulse})`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);
    }

    // ---- 第一百零八轮：天气屏幕层（色调/雨/血月红光/快晴太阳）----
    // 画在 Boss 血条与小地图之前 —— HUD 不吃天气色调。
    this._drawWeatherScreen(ctx, world, player, W, H);

    // Boss 血条
    // 第四十八轮：无尽模式 40 波起同屏 4 个 Boss —— 每个一条，纵向排列。
    // 用户原话："然后多boss的情况下显示多个血条，目前是只显示一个，
    // 然后排列好展示。"
    // 第七十一轮：跳过四重存在的分身（isClone）—— 用户："目前四重存在
    // 这个卡会显示5个芙兰的血条……试试把血条去掉"。本体一条照旧。
    const bosses = world.enemies.items.filter((e) => e.boss && !e.dead && !e.isClone);
    if (bosses.length > 0) {
      const bw = Math.min(560, W - 80);
      const bx = (W - bw) / 2;
      // 第九十九轮：芙兰战期间血条整体下移一点 —— 顶中的分段倒计时
      //（？？？/符卡/ExtraPhantom）和血条不再打架。用户："目前会盖住
      // 芙兰的血条一点，把芙兰的血条往下移动一点点。让两个要素分开。"
      // 第一百四十三轮：维吉尔战同通道下移 —— 顶部 90 秒大字压着血条
      //（用户反馈 1："盖住血条了……被挡住就再下移点"；维吉尔没有文字
      // 标签，下移量比芙兰少一点即可）
      const barTop = 62 + (world.flandreFight ? 18 : world.vergilFight ? 12 : 0);
      ctx.save();
      for (let i = 0; i < bosses.length; i++) {
        const boss = bosses[i];
        const by = barTop + i * 26;
        // 第一百零六轮：所有 Boss 统一哥特血条 —— 用户："把刚才给芙兰画的
        // 双层缓冲 + 哥特风边框 + 菱形刻度提取成通用的渲染方法，芙兰和
        // 普通 Boss 都调用它。" 芙兰走紫色主题 + 四道刻度 + 狂暴金；
        // 普通 Boss 走黑红主题 + 1/3、2/3 两道刻度 + 阶段锁无敌视觉。
        if (world.flandreFight && boss === world.flandreFight.boss) {
          this._drawFancyBossBar(ctx, boss, {
            x: bx, y: by, w: bw, theme: 'flandre',
            rage: world.flandreFight.phase === 'extra',
            t: world.flandreFight.t,
            ticks: [0.2, 0.4, 0.6, 0.8],
          });
        } else if (world.vergilFight && boss === world.vergilFight.boss) {
          // 第一百四十一轮：维吉尔走普通哥特主题（黑红 + 66%/33% 阶段刻度）。
          // lock 动态读阶段锁血（141 轮追加：切 2/3 阶段锁血 5 秒 → 🔒 闪烁）
          // 第一百四十二轮：入场演出期间整条不画 —— 用户反馈 2："boss血条
          // 出现太早而且跟着屏幕黑屏……排档背对.png消失之后开启这些"
          if (world.vergilFight.state === 'intro') continue;
          this._drawFancyBossBar(ctx, boss, {
            x: bx, y: by, w: bw, theme: 'boss',
            t: Date.now() / 1000,
            ticks: [1 / 3, 2 / 3],
            lock: (boss.bossPhase?.invulnTimer ?? 0) > 0,
          });
        } else {
          this._drawFancyBossBar(ctx, boss, {
            x: bx, y: by, w: bw, theme: 'boss',
            t: Date.now() / 1000,
            ticks: [1 / 3, 2 / 3],   // 普通 Boss 66% / 33% 切阶段
            lock: (boss.bossPhase?.invulnTimer ?? 0) > 0,
          });
        }
      }
      ctx.restore();
    }

    // 小地图
    this._drawMinimap(ctx, world, player, W, H);

    // ---- 第一百零八轮：天气公告横幅（第一百一十四轮挪到**底部中央**）----
    // 用户："天气提示框和回合倒计时重合了，干脆垂直移动到底部，底部啥
    // 都没有把天气给他吧。" 顶部中央是回合倒计时（hud-timer），底部左
    // 右分别是 HUD 血条堆和小地图，只有底部中央是空的 —— 横幅放
    // H-66..H-14。挂 10 秒左右；一行天气名（大）+ 一行提示（正常）。
    this._drawWeatherBanner(ctx, world, W, H);
  }

  /**
   * 第一百零六轮：通用哥特 Boss 血条（芙兰 + 普通波 Boss 共用）——
   * 由第一百零三轮的芙兰专用版提取而来，双层缓冲 / 哥特边框 / 菱形
   * 刻度全部保留，配色与刻度位通过参数适配：
   *   · theme 'flandre'：暗紫面板 + 亮紫红填充 + EP 狂暴金脉动 + 红光呼吸
   *   · theme 'boss'   ：黑红面板 + 各 Boss 专属色填充 + 暖金哥特边框
   *   · o.ticks        ：菱形刻度位（芙兰 20/40/60/80%，普通 1/3、2/3）
   *   · o.lock         ：阶段锁无敌 —— 边框快闪 + 血条闪烁 + 🔒 提示
   * 缓冲状态按 boss 引用分档存 Map（无尽 4 Boss 各追各的），换 boss
   * 自动重置；dt 用真实帧间隔（上限 0.1s 防切页大步跳）。
   */
  _drawFancyBossBar(ctx, boss, o) {
    const { x, y, w, theme } = o;
    // ---- 缓冲状态机（按 boss 分档）----
    const now = Date.now();
    const dt = this._barTs ? Math.min(0.1, (now - this._barTs) / 1000) : 1 / 60;
    this._barTs = now;
    this._barStates = this._barStates || new Map();
    let st = this._barStates.get(boss);
    if (!st || st.max !== boss.maxHp) {
      st = { buf: 1, delay: 0, prev: 1, max: boss.maxHp };
      this._barStates.set(boss, st);
      if (this._barStates.size > 16) {   // 防长局膨胀：丢最老的一档
        this._barStates.delete(this._barStates.keys().next().value);
      }
    }
    const frac = Math.max(0, Math.min(1, boss.hp / boss.maxHp));
    if (frac < st.prev - 1e-6) st.delay = 0.5;   // 新伤害 → 重置缓冲延迟
    st.prev = frac;
    if (st.delay > 0) st.delay -= dt;
    else if (st.buf > frac) st.buf = Math.max(frac, st.buf - 0.2 * dt);   // 每秒追 20%
    if (st.buf < frac) st.buf = frac;            // 血量回升（理论不发生）直接贴平

    const t = o.t ?? now / 1000;
    const rage = theme === 'flandre' && (o.rage || frac <= 0.2);   // EP 狂暴仅芙兰
    const lock = !!o.lock;   // 阶段锁无敌（普通 Boss 转阶段 5s）
    const h = 24;
    const gothic = theme === 'flandre'
      ? { panelTop: '#361b52', panelBot: '#1e0f33', glow: 'rgba(186,130,255,0.28)',
          frame: 'rgba(216,180,255,0.85)', text: '#ffffff', rageText: '#ffe9a8' }
      : { panelTop: '#3a1016', panelBot: '#1c070c', glow: 'rgba(255,176,96,0.25)',
          frame: 'rgba(255,206,140,0.85)', text: '#fff6ea', rageText: '#fff6ea' };

    ctx.save();
    // ---- ① 暗色渐变面板（主题色）+ 轻微内发光 ----
    const panel = ctx.createLinearGradient(0, y, 0, y + h);
    panel.addColorStop(0, gothic.panelTop);
    panel.addColorStop(1, gothic.panelBot);
    ctx.fillStyle = panel;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = gothic.glow;
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 1.5, y + 1.5, w - 3, h - 3);

    // ---- ② 哥特边框：外框线 + 两端尖角 ----
    ctx.strokeStyle = gothic.frame;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x - 0.5, y - 0.5, w + 1, h + 1);
    ctx.fillStyle = gothic.frame;
    ctx.beginPath();
    ctx.moveTo(x - 9, y + h / 2); ctx.lineTo(x - 1, y + 3); ctx.lineTo(x - 1, y + h - 3);
    ctx.closePath(); ctx.fill();                     // 左尖角 ◄
    ctx.beginPath();
    ctx.moveTo(x + w + 9, y + h / 2); ctx.lineTo(x + w + 1, y + 3); ctx.lineTo(x + w + 1, y + h - 3);
    ctx.closePath(); ctx.fill();                     // 右尖角 ►

    // ---- ②′ EP 狂暴红光呼吸（仅芙兰）----
    if (rage) {
      const breath = 0.45 + 0.35 * Math.abs(Math.sin(t * 2.6));
      ctx.save();
      ctx.shadowColor = `rgba(255,36,72,${0.55 + 0.35 * breath})`;
      ctx.shadowBlur = MOBILE ? 0 :10 + 14 * breath;
      ctx.strokeStyle = `rgba(255,60,90,${0.25 + 0.3 * breath})`;
      ctx.lineWidth = 2;
      ctx.strokeRect(x - 2, y - 2, w + 4, h + 4);
      ctx.restore();
    }

    // ---- ②″ 阶段锁无敌（普通 Boss）：边框快闪提示 ----
    if (lock) {
      const blink = 0.3 + 0.7 * Math.abs(Math.sin(t * 8));
      ctx.save();
      ctx.strokeStyle = `rgba(255,214,120,${blink})`;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(x - 2, y - 2, w + 4, h + 4);
      ctx.restore();
    }

    // ---- ③ 缓冲层（余烬亮橙，延迟后每秒追 20%）----
    const innerX = x + 3, innerW = w - 6, innerY = y + 4, innerH = h - 8;
    if (st.buf > frac + 0.001) {
      const ember = ctx.createLinearGradient(0, innerY, 0, innerY + innerH);
      if (rage) {
        ember.addColorStop(0, 'rgba(255,176,80,0.85)');
        ember.addColorStop(1, 'rgba(255,120,40,0.7)');
      } else {
        ember.addColorStop(0, 'rgba(255,122,48,0.95)');
        ember.addColorStop(1, 'rgba(200,58,16,0.95)');
      }
      ctx.fillStyle = ember;
      ctx.fillRect(innerX, innerY, innerW * st.buf, innerH);
    }

    // ---- ④ 即时血量层：芙兰紫红/狂暴金脉动；普通 Boss 用专属色渐变 ----
    const fill = ctx.createLinearGradient(0, innerY, 0, innerY + innerH);
    if (rage) {
      const pulse = 0.72 + 0.28 * Math.abs(Math.sin(t * 4.2));   // 狂暴脉动
      fill.addColorStop(0, `rgba(255,214,74,${pulse})`);
      fill.addColorStop(1, `rgba(255,138,0,${pulse})`);
    } else if (theme === 'flandre') {
      fill.addColorStop(0, '#ff2d78');   // 亮紫红
      fill.addColorStop(1, '#b30f5e');
    } else {
      fill.addColorStop(0, boss.color || '#ff4060');
      fill.addColorStop(1, this._shade(boss.color || '#ff4060', 0.55));
    }
    if (lock) ctx.globalAlpha = 0.6 + 0.4 * Math.abs(Math.sin(t * 8));   // 锁血闪烁
    ctx.fillStyle = fill;
    ctx.fillRect(innerX, innerY, innerW * frac, innerH);
    ctx.globalAlpha = 1;

    // ---- ⑤ 阶段刻度：发光金线 + 菱形挡板 ----
    ctx.shadowColor = 'rgba(255,208,64,0.9)';
    ctx.shadowBlur = MOBILE ? 0 :6;
    ctx.fillStyle = '#ffd23a';
    for (const k of (o.ticks ?? [0.2, 0.4, 0.6, 0.8])) {
      const tx = innerX + innerW * k;
      ctx.fillRect(tx - 0.75, y + 1, 1.5, h - 2);              // 金色刻度线
      ctx.save();
      ctx.translate(tx, y + h / 2);
      ctx.rotate(Math.PI / 4);
      ctx.fillRect(-2.6, -2.6, 5.2, 5.2);                      // 菱形挡板 ◆
      ctx.restore();
    }
    ctx.shadowBlur = MOBILE ? 0 :0;

    // ---- ⑥ 名字与数值（3px 圆角黑描边）----
    ctx.font = '700 12px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,0.9)';
    const barText = `${boss.def.bossName}  ${Math.ceil(boss.hp)} / ${Math.ceil(boss.maxHp)}`;
    ctx.strokeText(barText, x + w / 2, y + h / 2 + 0.5);
    ctx.fillStyle = rage ? gothic.rageText : gothic.text;
    ctx.fillText(barText, x + w / 2, y + h / 2 + 0.5);
    // 阶段锁无敌提示：🔒 护盾标（右尖角外侧）
    if (lock) {
      ctx.font = '700 13px sans-serif';
      ctx.fillText('🔒', x + w + 24, y + h / 2);
    }

    // ---- ⑦ 过载减伤徽标（第一百三十一轮）----
    // 血条正下方：🛡 ×层数 −减伤%。有层时徽标金橙呼吸 —— 用户要求
    // "血条附近必须显示当前减伤层数或减伤百分比"，并让玩家明白
    // "输出被限制的这几秒，正是专心躲弹幕的窗口"。
    if (boss.overload && boss.overload.layersCount > 0) {
      const ov = boss.overload;
      const pct = Math.round(ov.reduction * 100);
      const pulse = 0.6 + 0.4 * Math.abs(Math.sin(t * 4));
      const text = `🛡 过载 ×${ov.layersCount}  −${pct}%`;
      ctx.font = '700 12px "PingFang SC", "Microsoft YaHei", sans-serif';
      ctx.strokeStyle = 'rgba(0,0,0,0.9)';
      ctx.lineWidth = 3;
      ctx.strokeText(text, x + w / 2, y + h + 11);
      ctx.fillStyle = `rgba(255,${Math.round(150 + 60 * pulse)},60,${0.75 + 0.25 * pulse})`;
      ctx.fillText(text, x + w / 2, y + h + 11);
      // 血条外框叠一层金橙描边，余光也能注意到
      ctx.strokeStyle = `rgba(255,180,80,${0.35 + 0.35 * pulse})`;
      ctx.lineWidth = 2;
      ctx.strokeRect(x - 2, y - 2, w + 4, h + 4);
    }
    ctx.restore();
  }

  /** 十六进制颜色调暗（×f），哥特血条普通 Boss 渐变用 */
  _shade(hex, f) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex || '#ff4060';
    const n = parseInt(m[1], 16);
    const r = Math.round(((n >> 16) & 255) * f);
    const g = Math.round(((n >> 8) & 255) * f);
    const b = Math.round((n & 255) * f);
    return `rgb(${r},${g},${b})`;
  }

  // =========================================================
  // 第一百零八轮：天气渲染 —— 世界层 / 屏幕层 / 公告横幅
  //
  // 性能红线（设计时定死，见第一百零八轮方案讨论）：
  //   · 粒子一律纯色 + 单路径批量画（雨 = 一个 beginPath 全部 lineTo
  //     后一次 stroke），绝不用逐粒子 shadowBlur / 渐变；
  //   · 屏幕色调 = 每帧至多 2-3 次全屏 fillRect（globalCompositeOperation
  //     用 GPU 混合），不用 getImageData / CSS filter；
  //   · 浓雾 = 一个跟随玩家的径向渐变 fillRect（外圈即末档色标，天然
  //     覆盖 400px 以外），一帧一次。
  // =========================================================

  /** 世界层：川雾云 → 台风 → 流星警告/砸落 → 浓雾视野（雾最上） */
  _drawWeatherWorld(ctx, world, camera, player, W, H) {
    const w = world.weather;
    // 第一百六十轮：淡出段（phase 'fading'）继续绘制 —— 实体半径在
    // weather.update 里线性收缩、fogRamp 跟随 visualAlpha，视觉 2.5s 渐隐
    if (!w || !(w.active || w.phase === 'fading')) return;
    const d = w.def;
    if (!d) return;
    const ramp = Math.min(1, w.visT / (d.rampS || 1)) * (w.visualAlpha ?? 1);
    const t = w.visT;
    // 视野外裁剪（世界坐标下的可视矩形，留 200px 余量）
    const vx0 = camera.cx - W / 2 - 200, vy0 = camera.cy - H / 2 - 200;
    const vw = W + 400, vh = H + 400;

    // ---- 川雾：云团聚合（第一百一十一轮全面重画）----
    // 用户原话："川雾的视觉效果太差了，现在就是两个死板的大圆圈。……
    // 不要画完美的正圆。请在一个长条形/不规则的区域内，生成 6-8 个大
    // 小不一、边缘极度模糊的椭圆雾气块。……持续生成一些细小的、半透
    // 明的淡青色/冷白色水汽粒子。让它们缓缓向上飘动并淡出……在雾区内，
    // 画一些顺着雾气移动方向的浅色流线……改成带一点点冷蓝色或青白色
    // 的半透明渐变（比如 rgba(200,220,230,0.15)）。整个雾区的边缘必须
    // 完全虚化到 0。……当玩家处于雾中并被强制位移时，在玩家脚下画一
    // 点微弱的拖尾或水波纹。"
    //
    // 性能：模糊雾块用**离屏软斑点精灵**（首帧烘焙一次径向渐变，之后
    // 每块只是一次 drawImage 缩放 —— 边缘天然虚化到 0，零渐变分配）。
    // 4 团 × 7 块 = 28 次 drawImage + ≤40 粒子 + ≤32 流线，一帧轻松。
    if (d.id === 'riverFog') {
      if (!this._fogBlobSprite) {
        const cv = document.createElement('canvas');
        cv.width = cv.height = 160;
        const c2 = cv.getContext('2d');
        const g = c2.createRadialGradient(80, 80, 4, 80, 80, 80);
        // 冷蓝/青白（用户示例 rgba(200,220,230,0.15) 的基色系）
        g.addColorStop(0, 'rgba(206,226,236,0.62)');
        g.addColorStop(0.45, 'rgba(198,220,232,0.34)');
        g.addColorStop(0.8, 'rgba(190,214,228,0.10)');
        g.addColorStop(1, 'rgba(190,214,228,0)');
        c2.fillStyle = g;
        c2.fillRect(0, 0, 160, 160);
        this._fogBlobSprite = cv;
      }
      const spr = this._fogBlobSprite;
      for (const c of w.fogClouds) {
        if (c.x < vx0 - c.r * 1.6 || c.x > vx0 + vw + c.r * 1.6
          || c.y < vy0 - c.r * 1.6 || c.y > vy0 + vh + c.r * 1.6) continue;
        ctx.save();
        // 7 个椭圆雾块：各自慢漂 + 呼吸，互相交叠成长条形不规则云团
        for (const b of c.blobs) {
          const wob = Math.sin(t * 0.55 * b.spd + b.phase);
          const bx = c.x + b.ox * c.r + Math.cos(t * 0.3 * b.spd + b.phase) * c.r * 0.05;
          const by = c.y + b.oy * c.r + Math.sin(t * 0.24 * b.spd + b.phase * 1.7) * c.r * 0.04;
          const rx = c.r * b.rx * (1 + wob * 0.06);
          const ry = c.r * b.ry * (1 - wob * 0.05);
          ctx.globalAlpha = 0.5 + 0.14 * wob;
          ctx.drawImage(spr, bx - rx, by - ry, rx * 2, ry * 2);
        }
        // 雾内流线：顺着推人方向快速掠过 —— 看得见的力场
        ctx.strokeStyle = '#e2f0f6';
        ctx.lineCap = 'round';
        for (const s of c.streaks) {
          const k = Math.sin((s.life / s.max) * Math.PI);   // 淡入淡出
          ctx.globalAlpha = 0.4 * k * ramp;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.moveTo(s.x, s.y);
          ctx.lineTo(s.x - s.dx * s.len, s.y - s.dy * s.len);
          ctx.stroke();
        }
        // 水汽升腾粒子：淡青/冷白，缓缓向上飘并淡出
        for (const p of c.parts) {
          const k = Math.sin((p.life / p.max) * Math.PI);   // 升起-消散
          ctx.globalAlpha = 0.5 * k * ramp;
          ctx.fillStyle = '#cfe8f0';
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.s, 0, TAU);
          ctx.fill();
        }
        ctx.restore();
      }

      // 玩家被雾裹挟的反馈：站在雾里时脚下画水波纹 + 顺着推力方向的
      // 淡拖尾（用户："让玩家知道'我被雾裹挟了'"）
      const cc = w.fogClouds.find((c) => {
        const dx = player.x - c.x, dy = player.y - c.y;
        return dx * dx + dy * dy <= c.r * c.r;
      });
      if (cc) {
        // 取玩家所在那团雾的漂移方向（= 推的方向）
        const vn = Math.hypot(cc.vx, cc.vy) || 1;
        const ddx = cc.vx / vn, ddy = cc.vy / vn;
        ctx.save();
        // 脚下水波纹：两圈压扁呼吸环
        ctx.strokeStyle = '#cfe6ee';
        for (let i = 0; i < 2; i++) {
          const rr = player.radius * (1.1 + i * 0.55) + Math.sin(t * 4.2 - i * 1.6) * 3;
          ctx.globalAlpha = 0.22 - i * 0.07;
          ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.ellipse(player.x, player.y + player.radius * 0.55, rr, rr * 0.42, 0, 0, TAU);
          ctx.stroke();
        }
        // 推力方向淡拖尾：3 个渐隐残影
        for (let i = 1; i <= 3; i++) {
          ctx.globalAlpha = 0.10 - i * 0.025;
          ctx.drawImage(spr,
            player.x - ddx * i * 13 - player.radius * 1.3,
            player.y - ddy * i * 13 - player.radius * 1.3,
            player.radius * 2.6, player.radius * 2.6);
        }
        ctx.restore();
      }
    }

    // ---- 台风：多层旋转气旋（第一百一十轮全面重画）----
    // 用户原话："台风的视觉效果现在太单薄了，只有几条细圆弧，完全不够
    // 看。……画 3-4 层叠加的半透明气旋。内圈：半径小，旋转极快，线条
    // 密集，透明度高（灰白色）。外圈：半径大，旋转稍慢，线条稀疏，边
    // 缘带有柔和渐变，透明度低。颜色：可以用带一点点青灰色的白，或者
    // 暗银色，看起来更像狂风。" 外圈柔和渐变用一环径向渐变（一帧一次
    // createRadialGradient，性能可扛），弧线全部按层批量 stroke。
    if (d.id === 'typhoon') {
      // 199 轮：整个绘制挪进 weather-fx.js 的 drawTyphoon（锥形螺旋臂 + 落叶尘团 + 风暴眼 + 地面阴影 + 内部闪光 + 吸入粒子）。
      // 110 轮的规格（3~4 层叠加、内快外慢、青灰白、外缘柔和）原样保留；只改渲染，半径/推力/吸力等玩法数值不动。
      for (const tp of w.typhoons) {
        if (tp.x < vx0 - tp.r * 1.5 || tp.x > vx0 + vw + tp.r * 1.5
          || tp.y < vy0 - tp.r * 1.5 || tp.y > vy0 + vh + tp.r * 1.5) continue;
        WFX.drawTyphoon(ctx, tp, t, ramp);
      }
    }
    // ---- 流星雨：四阶段全流程（第一百一十二轮重做）----
    // 用户原话："【阶段 1】不要只是一个静态红圈。做一个从外向内收缩的
    // 红色环形，或者内部有红色倒计时填充的圈。边缘加一点轻微的呼吸闪
    // 烁。【阶段 2】取消那个黄色的矩形条！改成从屏幕上方斜着砸下来的
    // 火球。火球本体：一个高亮的橙色/金色圆形，边缘带有不规则的暗红
    // 色。拖尾特效：火球后面必须跟着一条长长的、由大变小、由亮变暗的
    // 半透明红色拖尾……模拟在大气层摩擦燃烧的感觉。【阶段 4】砸完之
    // 后，地面红圈不要立刻消失，保留 3 秒。这 3 秒内，圈内画几簇跳动
    // 的火焰……3 秒结束后再逐渐淡出。"
    // （阶段 3 的震屏/白闪/冲击波/碎片在 weather._impact 触发，走
    // 现成的 camera.shake / fx.shockwave / fx.shards；白闪在屏幕层画。）
    if (d.id === 'meteor') {
      for (const m of w.meteors) {
        const pulse = 0.5 + 0.5 * Math.sin(t * 12 + m.x * 0.37);   // 呼吸闪烁
        const prog = 1 - Math.max(0, m.warnT);                     // 0→1 倒计时
        ctx.save();
        // ---- 阶段 1：收缩环 + 倒计时（185 轮：加法辉光 + 向内收拢的刻度，WFX.drawMeteorWarn）----
        WFX.drawMeteorWarn(ctx, m, prog, pulse);
        ctx.restore();

        // ---- 阶段 2：火球（最后 0.55 秒从屏幕外斜砸下来；185 轮：白热核 + 火团 + 12 团余烬尾，WFX.drawMeteorBall）----
        if (m.warnT < 0.55) {
          const f = 1 - m.warnT / 0.55;
          WFX.drawMeteorBall(ctx, m, m.side * 520, -760, f * f, ramp, t);
        }
      }
      // ---- 阶段 4：持续燃烧区（3 秒，与烧灼伤害同寿命；185 轮：精灵火舌 + 上飘火星，WFX.drawBurnZone）----
      for (const z of w.burnZones) WFX.drawBurnZone(ctx, z, t, Math.min(1, z.t / 0.8));
    }
    // ---- 浓雾：视野圈（最上层）----
    // 第一百三十六轮二次重写（用户："插件市场里玩正常的，通过插件市场的
    // 浏览器玩还是闪"—— 那个"浏览器"是系统 Edge）。上一版虽然把渐变
    // 烘焙成了贴图，但"雾底 fillRect + destination-out 擦洞"仍是**逐帧
    // 半透明大层合成**，不同 GPU/合成路径（NEKO 内嵌 WebView vs Edge）
    // 光栅化结果有差异，Edge 上外圈依旧量化抖动。
    //
    // 这一版做到**零合成模式**：
    //   · 首帧烘焙一张"环带"贴图 —— 中心（视野内）全透明，200~400 线性
    //     升到雾色，贴图外圈全是雾色；
    //   · 运行时**一次 drawImage** 把贴图盖在玩家周围（贴图外圈与远处
    //     雾底同色，视觉上无缝延伸），全程只有位图采样，任何内核都稳。
    // 口径不变：200 内全透 / 200→400 渐变 / 400 外雾底。
    if (d.id === 'fog' && w.fogRamp > 0) {
      const a = w.fogRamp;
      const FOG_TEX = 512;     // 烘焙源尺寸
      const FOG_SPAN = 2400;   // 运行时目标尺寸（世界单位，盖住 400 洞 + 大片雾底）
      if (!this._fogRingSprite) {
        const cv = document.createElement('canvas');
        cv.width = cv.height = FOG_TEX;
        const c2 = cv.getContext('2d');
        const g = c2.createRadialGradient(256, 256, 4, 256, 256, 256);
        const R = 256;                          // 源半径 ↔ 世界 1200（FOG_SPAN/2）
        g.addColorStop(0, 'rgba(233,235,241,0)');
        g.addColorStop(200 / (FOG_SPAN / 2), 'rgba(233,235,241,0)');   // 200 世界单位内全透
        g.addColorStop(Math.min(0.995, 400 / (FOG_SPAN / 2)), `rgba(233,235,241,0.97)`); // 400 处雾满
        g.addColorStop(1, 'rgba(233,235,241,0.97)');  // pad：贴图外圈全是雾
        c2.fillStyle = g;
        c2.fillRect(0, 0, FOG_TEX, FOG_TEX);
        this._fogRingSprite = cv;
      }
      // 环带贴图（globalAlpha 控制渐入；中心透明不遮视野）
      ctx.save();
      ctx.globalAlpha = a;
      // ---- 第一百五十九轮：修"浓雾不跟玩家"----
      // 用户："浓雾现在固定在某个地方不动了……玩家往左他也会往左一点点，
      // 往右同理，但是就一点点就不动了。"
      // 根因：本层在世界坐标系里（render 入口 translate(W/2-cx, H/2-cy)，
      // 见川雾云的绘制口径），而旧公式 tx = player.x - vx0 是**屏幕系**
      // 写法 —— 在世界系里等于把相机扣了两次，雾圈位置退化成
      // (player.x - camera.cx) + 常数，只跟着"玩家与镜头的差值"小幅
      // 挪动，镜头追平就钉死在世界原点附近，玩家走远就露出一大块无雾区。
      // 修复：世界系里直接以玩家为雾圈中心（与川雾云同口径）。
      const tx = player.x - FOG_SPAN / 2, ty = player.y - FOG_SPAN / 2;
      ctx.drawImage(this._fogRingSprite, tx, ty, FOG_SPAN, FOG_SPAN);
      // 兜底：超大视口（>2400，比如 4K 全屏）时贴图盖不满的四边补纯雾底
      //（普通屏幕不会触发；带与贴图不重叠，无接缝）
      ctx.fillStyle = 'rgba(233,235,241,0.97)';
      const bx0 = player.x - FOG_SPAN / 2, by0 = player.y - FOG_SPAN / 2;
      const bx1 = player.x + FOG_SPAN / 2, by1 = player.y + FOG_SPAN / 2;
      if (by0 > vy0) ctx.fillRect(vx0, vy0, vw, Math.min(vh, by0 - vy0));                       // 上
      if (by1 < vy0 + vh) ctx.fillRect(vx0, by1, vw, vy0 + vh - by1);                           // 下
      const my0 = Math.max(vy0, by0), my1 = Math.min(vy0 + vh, by1);
      if (my1 > my0) {
        if (bx0 > vx0) ctx.fillRect(vx0, my0, Math.min(vw, bx0 - vx0), my1 - my0);              // 左
        if (bx1 < vx0 + vw) ctx.fillRect(bx1, my0, vx0 + vw - bx1, my1 - my0);                  // 右
      }
      ctx.restore();
      // 雾里流动的雾絮（199 轮：原来是 8 个硬边白圆盘 —— 柔雾里唯一的硬边；换成柔边椭圆雾絮精灵，随机漂移 + 缓慢拉伸）
      WFX.drawFogWisps(ctx, player.x, player.y, t, a);
    }
  }

  /** 屏幕层：色调（暗/灰/暖/亮）+ 血月红光 + 快晴太阳 + 雾雨雨滴 */
  _drawWeatherScreen(ctx, world, player, W, H) {
    const w = world.weather;
    // 第一百六十轮：淡出段继续绘制（雨滴密度/色调由 weather 端收缩驱动）
    if (!w || !(w.active || w.phase === 'fading')) return;
    const d = w.def;
    if (!d) return;
    const ramp = Math.min(1, w.visT / (d.rampS || 1)) * (w.visualAlpha ?? 1);
    const t = w.visT;

    // ---- 流星雨砸地白闪（第一百一十二轮阶段 3，全屏快闪快衰减）----
    if (d.id === 'meteor' && w.flash > 0) {
      ctx.save();
      ctx.fillStyle = `rgba(255,244,230,${Math.min(0.75, w.flash * 3.4)})`;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }

    // 血月：整体略暗 + 顶部血红亮光（"不要太暗和太刺眼"）
    if (d.id === 'bloodMoon') {
      ctx.save();
      ctx.fillStyle = `rgba(30,4,14,${0.22 * ramp})`;
      ctx.fillRect(0, 0, W, H);
      const glow = ctx.createLinearGradient(0, 0, 0, H * 0.45);
      const pulse = 0.75 + 0.25 * Math.sin(t * 1.8);
      glow.addColorStop(0, `rgba(255,40,60,${0.20 * ramp * pulse})`);
      glow.addColorStop(1, 'rgba(255,40,60,0)');
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, W, H * 0.45);
      ctx.restore();
      WFX.drawBloodMoon(ctx, W, H, t, ramp);   // 185 轮：巨大的血色月亮 + 上升红色余烬
    }

    // 台风：底色只压成暗冷色调（第一百一十轮 —— 用户："现在的底色不用
    // 那么灰，保持暗色调就行，但把风痕画亮一点（白/浅青），增加对比
    // 度。" 旧的 saturation 去灰退役，换成淡淡的暗蓝罩）。
    if (d.id === 'typhoon') {
      ctx.save();
      ctx.fillStyle = `rgba(14,20,34,${0.18 * ramp})`;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();

      // ---- 全屏风痕（Wind Streaks，增强 3）----
      // 横穿屏幕的细长白/浅青线，快速掠过 + 快速衰减。粒子状态挂在
      // 渲染器上（纯视觉装饰，Math.random；走 RNG 纪律：不碰主流与
      // weatherRng）。单条 path 批量画，一次 stroke。
      if (!this._windStreaks) this._windStreaks = [];
      const ws = this._windStreaks;
      const want = Math.round(26 * ramp);
      while (ws.length < want) {
        const a = Math.random() * Math.PI * 2;
        const spd = 700 + Math.random() * 900;
        ws.push({
          x: Math.random() * W, y: Math.random() * H,
          vx: Math.cos(a) * spd, vy: Math.sin(a) * spd,
          len: 50 + Math.random() * 110,
          life: 0, max: 0.35 + Math.random() * 0.45,
          cool: Math.random() < 0.4,     // 四成是浅青，其余纯白
        });
      }
      ctx.save();
      ctx.lineCap = 'round';
      for (let i = ws.length - 1; i >= 0; i--) {
        const s = ws[i];
        s.life += 1 / 60;
        if (s.life > s.max) { ws.splice(i, 1); continue; }
        s.x += s.vx / 60; s.y += s.vy / 60;
        const k = 1 - s.life / s.max;          // 快速衰减
        const grow = Math.min(1, s.life * 8);  // 出现瞬间拉长
        ctx.globalAlpha = 0.55 * k * ramp;
        ctx.strokeStyle = s.cool ? '#bfeafc' : '#f2faff';
        ctx.lineWidth = 1.1 + k * 0.9;
        const n = Math.hypot(s.vx, s.vy) || 1;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(s.x - (s.vx / n) * s.len * grow, s.y - (s.vy / n) * s.len * grow);
        ctx.stroke();
      }
      ctx.restore();
    }

    // 晴岚：亮度 +15%（加法混合淡白）
    if (d.id === 'breeze') {
      ctx.save();
      ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
      ctx.fillStyle = `rgba(255,252,235,${0.10 * ramp})`;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
      if (!MOBILE) WFX.drawPetals(ctx, W, H, t, ramp);   // 185 轮：花瓣与嫩叶顺风飘
    }

    // 烈日：暖度 + 亮度（橙罩 + 淡黄加法）
    if (d.id === 'scorch') {
      ctx.save();
      ctx.fillStyle = `rgba(255,120,30,${0.08 * ramp})`;
      ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = MOBILE ? 'source-over' : 'lighter';
      ctx.fillStyle = `rgba(255,210,110,${0.07 * ramp})`;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
      if (!MOBILE) WFX.drawHeat(ctx, W, H, t, ramp);   // 185 轮：地表热浪 + 底部橙色辉光
    }

    // 快晴：右上的太阳（185 轮：核心 + 缓转日光射线 + 光尘，取代单个渐变光斑）
    if (d.id === 'clear') {
      WFX.drawSun(ctx, W, H, player.screenX ?? W * 0.82, t, ramp);
    }
    // 雾雨：小雨（170 滴，单路径批量一次 stroke —— 性能红线见类头注释）
    if (d.id === 'rain' && w.drops) {
      const dt = 1 / 60;
      ctx.save();
      ctx.strokeStyle = `rgba(190,214,240,${0.36 * ramp})`;
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      for (const dr of w.drops) {
        dr.y += dr.spd * dt / H;
        dr.x += dr.drift * dt / W;
        if (dr.y > 1.05) { dr.y -= 1.1; dr.x = Math.random(); }
        if (dr.x > 1.02) dr.x -= 1.04;
        const px = dr.x * W, py = dr.y * H;
        const k = dr.len / Math.hypot(dr.spd, dr.drift);
        ctx.moveTo(px, py);
        ctx.lineTo(px - dr.drift * k, py - dr.spd * k);
      }
      ctx.stroke();
      ctx.restore();
      if (!MOBILE) WFX.drawRainExtras(ctx, W, H, t, ramp);   // 185 轮：地面水纹 + 低空薄雾
    }
  }

  /** 公告横幅：底部中央（第一百一十四轮，原顶部与回合倒计时重叠），
   * 一行天气名（大）+ 一行提示（正常），挂 10 秒左右；
   * 与左侧 HUD 血条堆、右侧小地图（x0 = W-130）都不重叠。 */
  // =========================================================
  // 底部 HUD 条（第一百三十八轮，纯 Canvas）
  // =========================================================
  //
  // 血条 + 经验条视觉大重构（用户逐条设计）：
  //   血条 —— 哥特底座 / 黑描边 / 深红→亮粉渐变 / **延迟伤害残影**
  //   （受伤瞬间扣血，残影 0.5s 平滑跟上，打击感）/ 护盾冰蓝光带
  //   （流光 + 平滑扣除）/ 数字黑描边 + 外发光。
  //   经验条 —— 加粗 12px / 暗紫底内发光 / 粉红→亮青渐变光带 + 外发光 /
  //   25/50/75% 金色猫爪节点 / >90% 呼吸充能 / 拾取高光白点滑动 /
  //   Lv 金色渐变大字 + 黑描边 + 升级弹跳。
  //
  // 性能：渐变/爪印/流光贴图全部惰性烘焙缓存；每帧只有 2 个 fillRect、
  // 3 次 drawImage（爪）+ 少量文本描边填充，无逐帧渐变大对象分配。
  _drawHudBars(ctx, player, W, H) {
    // ---- 时间与惰性状态 ----
    const now = performance.now() / 1000;
    if (this._hudLastNow == null) this._hudLastNow = now;
    const dt = Math.min(0.1, Math.max(0, now - this._hudLastNow));
    this._hudLastNow = now;
    const S = this._hud ??= {};

    // ================ 血条 ================
    //
    // 第一百三十八轮修复（用户："属性文字叠在血条上了""装备栏不见了"）：
    // 之前血条画在"贴底 30px"的自算坐标上，而 DOM 布局里血条行在
    // 武器栏/属性栏**上方** —— Canvas 血条正好压在属性/武器区域上。
    // 改为**直接测量 DOM 占位壳的真实位置**（hud-hp-bar 壳 + 数字 ghost），
    // Canvas 跟随 DOM 布局画，窗口缩放时重测 —— 永不重叠。
    {
      if (!this._hudShell || this._hudShellW !== W || this._hudShellH !== H) {
        const barEl = document.querySelector('.hud-hp-bar');
        if (barEl) {
          // 第一百六十三轮：Letterbox 等比缩放下，getBoundingClientRect()
          // 返回的是**视口坐标**（含 #app 整体缩放与位移），而 Canvas 的
          // 绘制坐标是 #app 本地坐标。以 canvas 自身 rect 为参照换算回
          // 本地：k = 视口宽 / CSS 宽。桌面无变换时 k=1、cr 偏移为 0，
          // 数值与旧逻辑逐位一致（桌面零影响）。
          const cr = this.canvas.getBoundingClientRect();
          const k = (cr.width / (this.canvas.clientWidth || cr.width)) || 1;
          const lx = (v) => (v - cr.left) / k;
          const ly = (v) => (v - cr.top) / k;
          const r = barEl.getBoundingClientRect();
          const shell = { x: lx(r.left), y: ly(r.top), w: r.width / k, h: r.height / k };
          const ghostEl = document.getElementById('hud-hp-ghost');
          if (ghostEl) {
            const g = ghostEl.getBoundingClientRect();
            shell.textX = lx(g.left);
            shell.textY = ly(g.top + g.height / 2);
          }
          this._hudShell = shell;
          this._hudShellW = W;
          this._hudShellH = H;
        }
      }
      const shell = this._hudShell;
      if (shell) {
      const HP_H = shell.h;
      const hpW = shell.w;
      const hpX = shell.x;
      const hpY = shell.y;
      const r = 8;
      const hpRatio = Math.max(0, Math.min(1, player.hp / player.maxHp));

      // ---- 延迟伤害残影（Lerp Bar）----
      // 掉血：残影停在原位 0.5s 内平滑跟上；回血：瞬间跟齐（不拖泥带水）。
      if (S.hpDisp == null || hpRatio > S.hpDisp) S.hpDisp = hpRatio;
      else S.hpDisp = Math.max(hpRatio, S.hpDisp - dt / 0.5 * Math.max(0.05, S.hpDisp - hpRatio));
      const dispRatio = S.hpDisp;

      // ---- 护盾平滑 ----
      const shieldRatio = Math.max(0, Math.min(1, player.shield / player.maxHp));
      if (S.shieldDisp == null) S.shieldDisp = shieldRatio;
      else {
        const k = 1 - Math.exp(-dt * 10);       // 双向平滑（扣除/回复都动画）
        S.shieldDisp += (shieldRatio - S.shieldDisp) * k;
        if (Math.abs(S.shieldDisp - shieldRatio) < 0.001) S.shieldDisp = shieldRatio;
      }

      // ---- 底座：微倾斜的深灰哥特面板（黑描边 + 刻痕纹理）----
      const pad = 4, skew = 7;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(hpX - pad + skew, hpY - pad);
      ctx.lineTo(hpX + hpW + pad - skew, hpY - pad);
      ctx.lineTo(hpX + hpW + pad, hpY + HP_H + pad);
      ctx.lineTo(hpX - pad, hpY + HP_H + pad);
      ctx.closePath();
      // 金属底渐变（上亮下暗的冷灰）——缓存
      const plateKey = `plate${hpW}`;
      if (S[plateKey] == null) {
        const g = ctx.createLinearGradient(0, hpY - pad, 0, hpY + HP_H + pad);
        // 205 轮：血条底座从深灰金属换成白色玻璃（配合浅色 HUD）
        g.addColorStop(0, '#ffffff');
        g.addColorStop(0.5, '#f3f9ff');
        g.addColorStop(1, '#dcebfa');
        S[plateKey] = g;
      }
      ctx.fillStyle = S[plateKey];
      ctx.fill();
      // 哥特刻痕：斜向细线（pattern 平铺，首帧烘焙）
      if (!S.hudHatch) {
        const tcv = document.createElement('canvas');
        tcv.width = tcv.height = 8;
        const t2 = tcv.getContext('2d');
        t2.strokeStyle = 'rgba(120,170,230,0.13)';
        t2.lineWidth = 1;
        t2.beginPath(); t2.moveTo(-2, 10); t2.lineTo(10, -2);
        t2.moveTo(-2, 4); t2.lineTo(4, -2); t2.stroke();
        S.hudHatch = ctx.createPattern(tcv, 'repeat');
      }
      ctx.fillStyle = S.hudHatch;
      ctx.fill();
      // 细黑描边
      ctx.strokeStyle = 'rgba(196,220,244,0.95)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      // 底座顶部金属高光
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(hpX - pad + skew + 3, hpY - pad + 1.5);
      ctx.lineTo(hpX + hpW + pad - skew - 3, hpY - pad + 1.5);
      ctx.stroke();
      ctx.restore();

      // ---- 圆角矩形 helper ----
      const rr = (x, y, w, h, rad) => {
        const rr2 = Math.min(rad, w / 2, h / 2);
        ctx.beginPath();
        ctx.moveTo(x + rr2, y);
        ctx.arcTo(x + w, y, x + w, y + h, rr2);
        ctx.arcTo(x + w, y + h, x, y + h, rr2);
        ctx.arcTo(x, y + h, x, y, rr2);
        ctx.arcTo(x, y, x + w, y, rr2);
        ctx.closePath();
      };

      const inX = hpX + 2, inW = hpW - 4, inY = hpY + 2, inH = HP_H - 4;

      // ---- 延迟伤害残影（亮橙白，在血色之下先画）----
      if (dispRatio > hpRatio + 0.001) {
        ctx.save();
        rr(inX, inY, inW, inH, 6); ctx.clip();
        const gW = S[`dmg${inW}`] ??= (() => {
          const g = ctx.createLinearGradient(inX, 0, inX + inW, 0);
          g.addColorStop(0, '#ffe9c9'); g.addColorStop(1, '#ff9a3a');
          return g;
        })();
        ctx.fillStyle = gW;
        ctx.fillRect(inX + inW * hpRatio, inY, inW * (dispRatio - hpRatio), inH);
        ctx.restore();
      }

      // ---- 血量填充：深红 → 亮红 → 亮粉 渐变 ----
      if (hpRatio > 0) {
        const gKey = `hpFill${inW}`;
        if (S[gKey] == null) {
          const g = ctx.createLinearGradient(inX, 0, inX + inW, 0);
          g.addColorStop(0, '#8f1220');
          g.addColorStop(0.55, '#e0263c');
          g.addColorStop(1, '#ff7d9c');
          S[gKey] = g;
        }
        ctx.save();
        rr(inX, inY, inW, inH, 6); ctx.clip();
        ctx.fillStyle = S[gKey];
        ctx.fillRect(inX, inY, inW * hpRatio, inH);
        // 血条内顶部高光（玻璃感）
        ctx.fillStyle = 'rgba(255,255,255,0.22)';
        ctx.fillRect(inX, inY, inW * hpRatio, 2);
        ctx.restore();
      }

      // ---- 护盾：冰蓝光带覆盖 + 流光 + 平滑扣除 ----
      if (S.shieldDisp > 0.003) {
        const sw = inW * Math.min(1, S.shieldDisp);
        ctx.save();
        rr(inX, inY, inW, inH, 6); ctx.clip();
        ctx.fillStyle = 'rgba(120,200,255,0.58)';
        ctx.fillRect(inX, inY, sw, inH);
        // 流光：斜向亮带贴图平移（首帧烘焙 96×32，两张拼接无缝循环）
        if (!S.shieldStreak) {
          const scv = document.createElement('canvas');
          scv.width = 96; scv.height = 32;
          const s2 = scv.getContext('2d');
          const sg = s2.createLinearGradient(0, 0, 96, 0);
          sg.addColorStop(0, 'rgba(220,242,255,0)');
          sg.addColorStop(0.5, 'rgba(220,242,255,0.55)');
          sg.addColorStop(1, 'rgba(220,242,255,0)');
          s2.fillStyle = sg;
          s2.fillRect(0, 0, 96, 32);
          S.shieldStreak = scv;
        }
        const shift = (now * 46) % 192;
        ctx.globalAlpha = 0.85;
        ctx.drawImage(S.shieldStreak, inX + shift - 192, inY, 96, inH);
        ctx.drawImage(S.shieldStreak, inX + shift - 96, inY, 96, inH);
        ctx.drawImage(S.shieldStreak, inX + shift, inY, 96, inH);
        ctx.globalAlpha = 1;
        // 盾顶冷高光
        ctx.fillStyle = 'rgba(210,238,255,0.5)';
        ctx.fillRect(inX, inY, sw, 1.5);
        ctx.restore();
      }

      // ---- 血条槽内描边（黑，增强对比）----
      rr(inX - 0.5, inY - 0.5, inW + 1, inH + 1, 7);
      ctx.strokeStyle = 'rgba(0,0,0,0.9)';
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // ---- 数字：黑描边 + 淡金外发光（位置 = DOM ghost 测量点）----
      const hpText = `${Math.ceil(player.hp)} / ${Math.round(player.maxHp)}`;
      const txX = shell.textX ?? hpX + hpW + 10;
      const txY = shell.textY ?? hpY + HP_H / 2;
      ctx.save();
      ctx.font = '800 15px "Segoe UI", "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.shadowColor = 'rgba(255,214,140,0.55)';
      ctx.shadowBlur = MOBILE ? 0 :5;
      ctx.lineWidth = 4;
      ctx.strokeStyle = 'rgba(255,255,255,0.95)';
      ctx.lineJoin = 'round';
      ctx.strokeText(hpText, txX, txY);
      ctx.shadowBlur = MOBILE ? 0 :0;
      ctx.fillStyle = '#1d3c63';
      ctx.fillText(hpText, txX, txY);
      ctx.restore();
      }
    }

    // ================ 经验条 ================
    {
      const BAR_H = 12;
      const y0 = H - BAR_H;
      const ratio = Math.max(0, Math.min(1, player.xp / player.xpNeeded));

      // ---- 动画推进 ----
      if (S.lastRatio == null) { S.lastRatio = ratio; S.lastLevel = player.level; }
      // 拾取反馈：经验增加 → 高光白点从旧位置滑向新位置
      if (ratio > S.lastRatio + 0.0001) {
        S.xpSwipe = { from: S.lastRatio, to: ratio, t: 0 };
      } else if (player.level > S.lastLevel || ratio < S.lastRatio - 0.2) {
        // 升级：白点滑到头 + Lv 弹跳
        S.xpSwipe = { from: Math.max(S.lastRatio, 0.9), to: 1, t: 0 };
        S.lvBounce = 0.42;
      }
      S.lastRatio = ratio; S.lastLevel = player.level;
      const swipe = S.xpSwipe;
      if (swipe) {
        swipe.t += dt / 0.28;
        if (swipe.t >= 1) S.xpSwipe = null;
      }
      if (S.lvBounce > 0) S.lvBounce = Math.max(0, S.lvBounce - dt);

      // ---- 底条：半透明暗紫 + 内发光 ----
      const bgKey = `xpBg${W}`;
      if (S[bgKey] == null) {
        const g = ctx.createLinearGradient(0, y0, 0, H);
        // 205 轮：经验条底座同样换成浅色
        g.addColorStop(0, 'rgba(255,255,255,0.96)');
        g.addColorStop(1, 'rgba(228,241,255,0.98)');
        S[bgKey] = g;
      }
      ctx.fillStyle = S[bgKey];
      ctx.fillRect(0, y0, W, BAR_H);
      // 内发光：顶部冷光线 + 内侧紫描边
      ctx.fillStyle = 'rgba(120,180,240,0.35)';
      ctx.fillRect(0, y0, W, 1);
      ctx.strokeStyle = 'rgba(170,205,240,0.9)';
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, y0 + 0.5, W - 1, BAR_H - 1);

      // ---- 填充：粉红 → 亮青 渐变光带（+ 呼吸充能 >90%）----
      const gKey = `xpFill${W}`;
      if (S[gKey] == null) {
        const g = ctx.createLinearGradient(0, y0, W, y0);
        // 205 轮：经验填充改成社区蓝 → 青（原来是粉紫霓虹）
        g.addColorStop(0, '#4aa8ff');
        g.addColorStop(0.55, '#5fc8f5');
        g.addColorStop(1, '#67d3f0');
        S[gKey] = g;
      }
      const nearFull = ratio > 0.9;
      const pulse = nearFull ? 0.5 + 0.5 * Math.sin(now * 5.2) : 0;
      if (ratio > 0) {
        ctx.save();
        ctx.fillStyle = S[gKey];
        if (nearFull) ctx.globalAlpha = 0.82 + 0.18 * pulse;
        ctx.shadowColor = nearFull ? `rgba(255,140,220,${0.5 + 0.5 * pulse})` : 'rgba(216,115,239,0.4)';
        ctx.shadowBlur = MOBILE ? 0 :nearFull ? 8 + 12 * pulse : 5;
        ctx.fillRect(0, y0 + 1, W * ratio, BAR_H - 2);
        // 光带顶部高光
        ctx.shadowBlur = MOBILE ? 0 :0;
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.fillRect(0, y0 + 1, W * ratio, 1.5);
        ctx.restore();
      }

      // ---- 金色猫爪节点（25% / 50% / 75%）----
      if (!S.pawSprite) {
        const pcv = document.createElement('canvas');
        pcv.width = pcv.height = 48;
        const p2 = pcv.getContext('2d');
        p2.fillStyle = '#ffd23a';
        p2.strokeStyle = 'rgba(120,72,10,0.9)';
        p2.lineWidth = 2;
        // 主垫
        p2.beginPath();
        p2.ellipse(24, 30, 11, 8.5, 0, 0, Math.PI * 2);
        p2.fill(); p2.stroke();
        // 三趾
        for (const [px, py, pr] of [[13, 17, 4.6], [24, 13.5, 4.9], [35, 17, 4.6]]) {
          p2.beginPath();
          p2.ellipse(px, py, pr, pr + 0.6, 0, 0, Math.PI * 2);
          p2.fill(); p2.stroke();
        }
        S.pawSprite = pcv;
      }
      ctx.save();
      ctx.shadowColor = 'rgba(255,210,58,0.8)';
      ctx.shadowBlur = MOBILE ? 0 :4;
      for (const p of [0.25, 0.5, 0.75]) {
        ctx.drawImage(S.pawSprite, W * p - 9, y0 + BAR_H / 2 - 9, 18, 18);
      }
      ctx.restore();

      // ---- 拾取高光白点（快速滑向新位置 + 拖尾）----
      if (swipe) {
        const k = 1 - Math.pow(1 - Math.min(1, swipe.t), 3);   // ease-out-cubic
        const px = (swipe.from + (swipe.to - swipe.from) * k) * W;
        ctx.save();
        ctx.shadowColor = 'rgba(255,255,255,0.95)';
        ctx.shadowBlur = MOBILE ? 0 :10;
        // 拖尾（身后三个递减亮点）
        for (let i = 1; i <= 3; i++) {
          const tk = Math.max(0, swipe.t - i * 0.07);
          const txx = (swipe.from + (swipe.to - swipe.from) * (1 - Math.pow(1 - tk, 3))) * W;
          ctx.globalAlpha = 0.5 - i * 0.13;
          ctx.fillStyle = '#ffffff';
          ctx.beginPath();
          ctx.arc(txx, y0 + BAR_H / 2, 4.5 - i * 1.1, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(px, y0 + BAR_H / 2, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // ---- Lv 文本：金色渐变大字 + 粗黑描边 + 升级弹跳 ----
      const bounce = S.lvBounce > 0
        ? 1 + 0.55 * Math.sin((1 - S.lvBounce / 0.42) * Math.PI)
        : 1;
      const lvSize = Math.round(21 * bounce);
      const lvText = `Lv.${player.level}`;
      ctx.save();
      ctx.font = `800 ${lvSize}px "Segoe UI", "Microsoft YaHei", sans-serif`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'alphabetic';
      const lvY = H - BAR_H - 7;
      if (S.lvGradY !== lvY || !S.lvGrad) {
        const g = ctx.createLinearGradient(0, lvY - lvSize, 0, lvY);
        g.addColorStop(0, '#fff3c4');
        g.addColorStop(0.55, '#ffd23a');
        g.addColorStop(1, '#f59e0b');
        S.lvGrad = g; S.lvGradY = lvY;
      }
      ctx.shadowColor = 'rgba(255,200,80,0.5)';
      ctx.shadowBlur = MOBILE ? 0 :6;
      ctx.lineWidth = 5;
      ctx.strokeStyle = 'rgba(10,6,18,0.95)';
      ctx.lineJoin = 'round';
      ctx.strokeText(lvText, W - 14, lvY);
      ctx.shadowBlur = MOBILE ? 0 :0;
      ctx.fillStyle = S.lvGrad;
      ctx.fillText(lvText, W - 14, lvY);
      ctx.restore();
    }
  }

  _drawWeatherBanner(ctx, world, W, H) {
    const w = world.weather;
    if (!w || !w.active || w.announceT <= 0) return;
    const d = w.def;
    if (!d) return;
    const name = d.name;
    const hint = d.hint;
    const color = d.color;
    // 进出 0.4s 淡入淡出（结尾淡出；开场由 announceT=10 起跳自然全显）
    let alpha = 1;
    if (w.announceT < 0.5) alpha = w.announceT / 0.5;

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.font = '700 21px "PingFang SC", "Microsoft YaHei", sans-serif';
    const nameW = ctx.measureText(name).width;
    ctx.font = '13px "PingFang SC", "Microsoft YaHei", sans-serif';
    const hintW = ctx.measureText(hint).width;
    const boxW = Math.max(nameW, hintW) + 56;
    const boxH = 52;
    const bx = (W - boxW) / 2;
    const by = H - 66;
    // 面板
    ctx.fillStyle = 'rgba(12,12,22,0.78)';
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(bx, by, boxW, boxH, 10) : ctx.rect(bx, by, boxW, boxH);
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha * 0.9;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // 第一行：天气名（大字，天气色）
    ctx.globalAlpha = alpha;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '700 21px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillStyle = color;
    ctx.fillText(name, W / 2, by + 16);
    // 第二行：提示（正常大小）
    ctx.font = '13px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.fillStyle = 'rgba(238,236,246,0.92)';
    ctx.fillText(hint, W / 2, by + 38);
    ctx.restore();
  }

  _drawMinimap(ctx, world, player, W, H) {
    const size = 116;
    const pad = 14;
    const x0 = W - size - pad;
    const y0 = H - size - pad - 24;
    const scale = size / (world.halfSize * 2);

    ctx.save();
    ctx.globalAlpha = 0.72;
    ctx.fillStyle = 'rgba(18,20,28,0.85)';
    ctx.strokeStyle = 'rgba(255,255,255,0.16)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x0, y0, size, size, 8);
    ctx.fill();
    ctx.stroke();

    const toMap = (wx, wy) => ({
      x: x0 + (wx + world.halfSize) * scale,
      y: y0 + (wy + world.halfSize) * scale,
    });

    // 敌人点
    for (const e of world.enemies.items) {
      const p = toMap(e.x, e.y);
      ctx.fillStyle = e.boss ? '#ff4d94' : e.elite ? '#ffc857' : 'rgba(255,95,109,0.75)';
      const s = e.boss ? 4 : e.elite ? 3 : 2;
      ctx.fillRect(p.x - s / 2, p.y - s / 2, s, s);
    }

    // 拾取物
    ctx.fillStyle = 'rgba(110,231,168,0.5)';
    for (const p0 of world.pickups.items) {
      const p = toMap(p0.x, p0.y);
      ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
    }

    // 玩家
    const pp = toMap(player.x, player.y);
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(pp.x, pp.y, 3.5, 0, TAU);
    ctx.fill();
    ctx.strokeStyle = player.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(pp.x, pp.y, 5.5, 0, TAU);
    ctx.stroke();

    // ---- 队友点（183 轮 2P → 184 轮 1~4P）：每个队友小地图上白点 +
    //      各自专属色外圈（P2 粉 / P3 金 / P4 青），一人一色区分身份 ----
    const mateColors = Renderer.PARTNER_COLORS;
    for (const pl of (world.partners?.() ?? [])) {
      if (!pl || pl.dead) continue;
      const slot = (pl === world.player2) ? 0 : (pl === world.player3) ? 1 : 2;
      const mp = toMap(pl.x, pl.y);
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(mp.x, mp.y, 3, 0, TAU);
      ctx.fill();
      ctx.strokeStyle = mateColors[slot] ?? '#ff9ec7';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(mp.x, mp.y, 5, 0, TAU);
      ctx.stroke();
    }

    ctx.restore();
  }
}

/**
 * 把 #rrggbb 转成带透明度的 rgba()。
 * 用于宝箱/宝箱怪的径向光晕渐变（canvas 渐变需要 rgba 而不是 hex）。
 */
function hexA(hex, alpha) {
  if (typeof hex !== 'string' || hex[0] !== '#') {
    return `rgba(255,200,87,${alpha})`;
  }
  let h = hex.slice(1);
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const n = parseInt(h, 16);
  if (Number.isNaN(n)) return `rgba(255,200,87,${alpha})`;
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}