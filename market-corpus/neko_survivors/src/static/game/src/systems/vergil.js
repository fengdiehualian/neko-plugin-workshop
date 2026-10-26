// ============================================================
// 第一百四十一轮：正式 Boss 战 —— 大排档椅子·维吉尔
// ============================================================
// 三阶段表演型 Boss。用户规格：
//  · 伤害固定 5% maxHp 纯真伤、无视无敌帧（player.takeDamage 的
//    ignoreIFrames + fixedDamage，第一百四十轮已实装）、0.2s 受击内置 CD。
//  · 五个技能：三连追身刀波（追踪+旋摆变向）、冲刺斩、AOE 拉回、
//    球形次元斩、大招·满屏次元斩（timeScale=0.05 + Bury the Light @2:00）。
//  · 阶段：P1 (>66%) / P2 (33%~66%, ×1.1) / P3 (≤33%, ×1.3)。
//    P3 转阶段立刻放大招；之后四技能轮转，10 秒冷却再放大招；
//    第二次大招**不重置 BGM 进度**（继续播）。
//  · 平时游荡，底部（头）始终朝向玩家。
// 骨架照抄芙兰战：主循环 STATE.VERGIL 分支 + world.update 照推（noSpawn
// 停刷怪），Boss 是 enemies 数组里的隐身实体（玩家武器正常索敌输出）。
// ============================================================

import { Enemy } from '../entities/enemy.js';
import { TAU } from '../core/math.js';
// 第一百八十五轮：维吉尔战特效 v2（虚空背景：幽灵剑/青色光尘/冷雾；脉动辉光场地边框；刀光垫底辉光）
import * as VFX from './vergil-fx.js';
import { audio } from './audio.js';
import { pluginBridge } from '../plugin-bridge.js';
import {
  drawCrescent, drawZigzagBlade, drawDashSlash, drawAoeRing, drawRift,
  drawUltVeil, spawnShatter, drawShards, drawEdgeFlash, distToSegment,
  drawMirageEdge, drawParrySlash, drawParrySpark, drawParryRipple,
  mulberry32, drawBladeSpark,
} from './dmc-blades.js';

// ---- 数值 ----
export const VERGIL_MAX_HP = 66666;   // 第一百五十四轮再改：正式 Boss 血量 66666
// ---- 第一百六十轮：常驻锁血 ----
// 用户原话："大排档椅子血量66666还是容易死。让他的受到2000伤害无敌0.5秒
// 常驻。然后第三阶段改为受到2000伤害0.8秒试试。这样玩家再肥他也可以坚持
// 30秒左右了，本身大排档椅子伤害就低，总不能还没表演完就死了。"
// 全程每累计受到 2000 伤害 → 无敌（P1/P2 0.5s、P3 0.8s）；154 轮的
// P3 专属阶梯（_p3HpBase/_p3NextMilestone）退役。
const LOCK_DMG_STEP = 2000;           // 每累计受到 2000 伤害触发一次
const LOCK_INVULN = 0.5;              // P1/P2 无敌 0.5 秒
const P3_LOCK_INVULN = 0.8;           // P3 无敌 0.8 秒
export const HIT_FRAC = 0.05;        // 每击 5% 玩家最大生命的固定真伤
export const HIT_CD = 0.2;           // 受击内置 CD（防一帧多刀秒杀）
export const ULT_CD = 10;            // P3 大招冷却（真实秒，收刀起算）
export const FIGHT_TIME = 90;        // 限时：90 秒内没打死什么都不给
export const ARENA = { cx: 0, cy: -80, hw: 560, hh: 500 };   // 与芙兰同规格
const BGM_FILE = 'assets/bgm/bury_the_light.ogg';
const BGM_START_AT = 120;            // P3 大招："直接从2分钟开始播放"
// 入场演出（将近 10 秒）：0-3s 黑屏 → 3-6s 场地+背对图淡入（BGM 从 0 秒起）
// → 6-9s 静止（BGM 前奏）→ 9s 背对消失、椅子在原像素点出现、开战
const INTRO_BLACK_END = 3;
const INTRO_SCENE_END = 6;
const INTRO_END = 9;

const SKILL_ORDER = ['triple', 'dash', 'aoe', 'sphere', 'mirage'];

// 幻影剑（Mirage Edge）参数（第一百四十六轮）
const MIRAGE_COUNT = 6;          // 环绕剑数（5-6 把）
const MIRAGE_SUMMON = 3.0;       // 环绕旋转时长
const MIRAGE_GAP = 0.22;         // 依次发射间隔
const MIRAGE_SPEED = 560;        // 飞行速度
const MIRAGE_ORBIT = 92;         // 环绕半径
const MIRAGE_AUTO_CD = 20;       // 自动触发冷却（用户补充：防止纯远程看不到）
const MIRAGE_MELEE_FRAC = 0.1;   // 累计近战伤害达玩家 maxHp 的 10% 触发

export class VergilFight {
  constructor(main, { onDone }) {
    try { VFX.warm(); } catch { /* Node 测试环境无 document */ }
    this.main = main;
    this.onDone = onDone;
    this.over = false;
    this.t = 0;                      // 真实秒表（AI 节奏全用它）
    this.timeScale = 1;              // 大招时 0.05（全场慢动作）

    // Boss 实体（隐身，位置每帧同步，武器正常打它）
    const def = {
      id: 'vergil_chair', name: '大…大排档椅子？！？！',
      bossName: '大…大排档椅子？！？！',   // 哥特血条读 def.bossName
      hp: VERGIL_MAX_HP, radius: 52, speed: 0, damage: 0,
      xp: 0, materials: 0, boss: true,
    };
    const w = main.world;
    this.boss = new Enemy(def, 0, -200,
      { hp: 1, bossHp: 1, damage: 1, speed: 1, xp: 1, materials: 1, bulletRange: 1 },
      main.world.rng ?? main.rng);
    this.boss.speed = 0;
    this.boss.flandreHidden = true;              // 渲染层隐身（演出层自绘椅子）
    // 焊死普通 Boss 的阶段推进机器 + 清掉登场无敌（同芙兰，见 flandre.js）——
    // 不清的话玩家全程打不动他（义眼实机抓到的"免疫"）。
    // 第一百四十二轮：入场演出期间 hitbox 关闭（复用阶段锁通道，
    // invulnTimer>0 时 takeDamage 全 blocked）—— 用户反馈 2："boss在开始
    // 战斗前就受到伤害了，改成在大排档boss出现才开启hitbox"。
    // intro 结束（背对剪影消失、本尊现身那一刻）再清零开打。
    this.boss.bossPhase.invulnTimer = 999;
    this.boss.bossPhase.gatesUsed = { 2: true, 3: true };
    this.boss.bossPhase.update = () => 0;
    w.enemies.add(this.boss);

    // AI 状态
    this.phase = 1;
    // 常驻锁血计数（第一百六十轮）：_hpLast 上一帧血量、_dmgAccum 累计伤
    this._hpLast = this.boss.hp;
    this._dmgAccum = 0;
    this.spdMul = 1;                 // 技能频率/速度倍率：P2 1.1、P3 1.3
    this.state = 'intro';            // intro(入场演出) | wander(站桩) | cast | ult | pause | win
    this.stateT = 0;
    this.introT = 0;                 // 入场演出计时（0→9s）
    this.timeLeft = FIGHT_TIME;      // 90 秒限时
    this.pauseDur = 1.0;             // pause 状态时长（收刀 1s / P3 转阶段 5s）
    this.bgmFade = 0;                // >0 时 BGM 音量线性淡出（P3 转阶段 5s）
    this.ultBgmLocked = false;       // P3 大招后 BGM 完全交给演出（大招不再碰）
    this.castGap = 1.0;              // 不攻击空档 ≤1s（随时在砍人）
    this.ultCd = 0;                  // P3 大招冷却（只在 P3 倒数）
    this.ultCount = 0;
    this.bx = 0; this.by = -260;     // 椅子锚点（世界坐标）
    this.chairFacing = Math.PI / 2;  // 朝向：平时不转，只有冲刺斩/大招转向
    // 141 轮改：维吉尔是站桩装逼剑客 —— 删除游荡，平时纹丝不动（只剩
    // 呼吸浮动），位置只随技能位移（冲刺斩瞬移 / 球裂后撤 / 收刀回中央）

    // 演出对象
    this.waves = [];                 // 三连齐射直线月牙（141 轮改：不追踪直冲）
    this.trails = [];                // 冲刺斩路径刀痕（月牙残影，可带滑出速度）
    this.aoeBlades = [];             // AOE 拉回圈内的随机半月刀波（可命中）
    this.dashes = [];                // 冲刺斩残影线 {x1,y1,x2,y2,t,life}
    this.slashBars = [];             // 冲刺斩横贯刀光 {y,t,life}
    this.aoe = null;                 // {r,mode,st}
    this.rifts = [];                 // 球形裂缝 telegraph {x,y,st,dur,fired}
    this.radials = [];               // 裂缝爆射的放射折线 {cx,cy,a,len,t,life,seed}
    this.ultBlades = [];             // 满屏折线刀光
    this.veil = 0;                   // 大招暗幕强度
    this.shards = [];                // 玻璃碎片
    this.whiteFlash = 0;
    this.edgeFlash = 0;
    this.hitCd = 0;                  // 玩家受击内置 CD

    // ---- 第一百四十六轮：幻影剑（Mirage Edge）----
    this.mirage = null;              // 技能态 {st, fired, swords:[{a,seed}], flying:[{x,y,angle,speed,t,life,seed,trail}]}
    this.mirageAutoCd = MIRAGE_AUTO_CD;  // 自动触发冷却（20s，intro 后起算）
    this.meleeMeter = 0;             // 累计近战伤害（达玩家 maxHp 10% 触发）
    this.mirageEmbers = [];          // 环绕剑火星粒子

    // ---- 第一百四十六轮：格挡领域（P1/P2 游荡时斩碎远程子弹）----
    this.guarding = false;           // 每帧重算：phase<=2 && state==='wander'
    this.guardT = 0;                 // 本次格挡已持续（破碎特效只在真格挡过时放）
    this.guardShatterT = 0;          // >0 时画"格挡光圈闪烁破碎"
    this.parryFx = [];               // 斩击线 {x,y,seed,t,life}
    this.parrySparks = [];           // 火花 {x,y,vx,vy,t,life}
    this.parryRipples = [];          // 涟漪 {t, life}
    this._parryCount = 0;            // 0.3s 窗口内格挡数（≥6 触发涟漪）
    this._parryWindow = 0;
    this._parrySfxT = 0;             // 音效节流

    // ---- 第一百四十八轮：P3 蓄力演出（"极度装逼的蓄力状态"）----
    this.chargeT = 0;                // >0 = 蓄力中，倒计时 5→0（拔刀）
    this.chargeParts = [];           // 反向汇聚粒子 {sx,sy,k,sp,hue}
    this.chargeCracks = [];          // 空间裂缝折线 {x,y,seed,t,life}
    this.chargeBlack = 0;            // >0 = 拔刀前绝对黑屏（0.2s）
    this.bladeSparks = [];           // 第一百四十九轮：刀锋火花 {x,y,vx,vy,ang,t,life}
    this.chairVisible = false;       // 入场演出期间椅子隐藏（9s 时在原像素点出现）
    this.chairImg = new Image();
    // 演出素材放 assets/boss/（不进 assets/enemies/ —— 那里被贴图清单
    // 系统扫描，非清单文件会挂 sprite/eye 检查，见第一百四十一轮回归）
    this.chairImg.src = 'assets/boss/vergil_chair.png';
    // 入场演出：大排档背对剪影（大小与 Boss 相当，9s 时与椅子换位）
    this.silImg = new Image();
    this.silImg.src = 'assets/boss/vergil_silhouette.png';
    // 战斗场地背景（大排档椅子背景，画在 ARENA 方形内，芙兰同款位置；
    // 放 assets/boss/ 演出素材目录 —— backgrounds 目录被 assets-check
    // 要求与 backgrounds.js 的 id 全匹配，演出专用图不进轮换池）
    this.arenaImg = new Image();
    this.arenaImg.src = 'assets/boss/vergil_arena.jpg';

    // BGM（独立 <audio>，绕开 playScene 的场景池）
    this._bgm = null;
    this._bgmStarted = false;
    try {
      if (typeof Audio !== 'undefined') {
        this._bgm = new Audio(BGM_FILE);
        this._bgm.preload = 'auto';
        this._bgm.volume = 0.85;
      }
    } catch { /* 环境不支持就安静 */ }
    this._seEls = new Map();         // 技能音效模板（cloneNode 播放）
  }

  /**
   * 技能音效（assets/sfx/{name}.ogg，cloneNode 允许重叠）。
   * 次元斩.ogg → vergil_ult；攻击2.ogg → vergil_aoe；攻击1.ogg → vergil_atk。
   */
  _se(name, vol = 0.5) {
    try {
      if (typeof Audio === 'undefined') return;
      let tpl = this._seEls.get(name);
      if (!tpl) {
        tpl = new Audio(`assets/sfx/${name}.ogg`);
        tpl.preload = 'auto';
        this._seEls.set(name, tpl);
      }
      const v = tpl.cloneNode();
      v.volume = vol;
      v.muted = !audio.enabled;
      v.play().catch(() => { /* 自动播放被拦就安静 */ });
    } catch { /* */ }
  }

  /**
   * 格挡音效：复用攻击1.ogg，高 playbackRate + 极短截断 —— 清脆的"叮"。
   * 节流：同帧多颗子弹只出 2 声（_parrySfxT 80ms 窗口）。
   */
  _seParry() {
    if (this._parrySfxT > 0) return;
    this._parrySfxT = 0.08;
    try {
      if (typeof Audio === 'undefined') return;
      // 第一百五十一轮：tpl 路径改用 vergil_atk.ogg —— 之前写的中文名
      // 攻击1.ogg 从未部署（assets/sfx 里只有英文名），404 的 Audio 被缓存
      // 进 _seEls['vergil_atk'] 后，之后所有 _se('vergil_atk') 都在 clone
      // 这个坏模板 → 攻击声全灭（用户："所有的攻击1.ogg和次元斩.ogg都
      // 不生效了"）。与 _se 完全同源，顺带消除缓存 key 冲突。
      let tpl = this._seEls.get('vergil_atk');
      if (!tpl) { tpl = new Audio('assets/sfx/vergil_atk.ogg'); tpl.preload = 'auto'; this._seEls.set('vergil_atk', tpl); }
      const v = tpl.cloneNode();
      v.volume = 0.3 * (audio.enabled ? 1 : 0);
      v.muted = !audio.enabled;
      v.playbackRate = 3.2;
      v.play().then(() => { setTimeout(() => { try { v.pause(); } catch { /* */ } }, 70); }).catch(() => { /* */ });
    } catch { /* */ }
  }

  spd() { return this.spdMul; }

  /**
   * HUD 顶部倒计时（纯显示，同芙兰战 getDisplayTimer 通道）——
   * 90 秒限时倒数。此前波次计时冻结在 30（用户反馈 2）。
   * 第一百四十三轮：不带文字标签 —— 用户反馈 1："要不去掉斩字"
   *（大字下方再顶一行小字会把血条压得更低）。
   */
  getDisplayTimer() {
    return { label: '', left: Math.max(0, this.timeLeft), total: FIGHT_TIME };
  }

  /** 结算入口（胜利 / 失败都走这里，幂等） */
  finish(outcome) {
    if (this.over) return;
    this.over = true;
    // 第一百四十八轮：任何结算路径都把全场速度/蓄力态复位（蓄力中被
    // 打死/超时的话，别让 timeScale=0.2 泄漏回正常波次）
    this.timeScale = 1;
    this.chargeT = 0;
    this.chargeBlack = 0;
    try { this._bgm?.pause(); } catch { /* */ }
    this.onDone?.(outcome);
  }

  // ---------------------------------------------------------
  // 每帧更新（真实秒 dtReal；世界/玩家由 main 用 timeScale 慢放）
  // ---------------------------------------------------------
  update(dtReal) {
    if (this.over) return;
    this.t += dtReal;
    const p = this.main.player;
    this.hitCd = Math.max(0, this.hitCd - dtReal);
    this.edgeFlash = Math.max(0, this.edgeFlash - dtReal * 2.2);
    this.whiteFlash = Math.max(0, this.whiteFlash - dtReal * 2.6);
    if (this._bgm) this._bgm.muted = !audio.enabled;
    // 阶段锁血倒计时（bossPhase.update 被焊死，递减由本类代跑）
    if (this.boss.bossPhase.invulnTimer > 0) {
      this.boss.bossPhase.invulnTimer = Math.max(0, this.boss.bossPhase.invulnTimer - dtReal);
    }

    // ---- 入场演出（将近 10 秒）----
    if (this.state === 'intro') {
      this.introT += dtReal;
      // 3s：新场景淡入的同时，Bury the Light 从 0 秒开始正常播
      if (this.introT >= INTRO_BLACK_END && !this._bgmStarted) {
        try {
          if (this._bgm) { this._bgm.currentTime = 0; this._bgm.play().catch(() => {}); }
          this._bgmStarted = true;
        } catch { /* */ }
      }
      // ---- 第一百五十六轮：猫娘台词（走官方 TTS 语音，pluginBridge.say）----
      // 用户："在屏幕慢慢变黑的3秒，彻底黑了就触发第一句话（考虑到一点延迟，
      // 所以猫娘开始说话的时候我们应该已经可以看到椅子了），然后第二句台词
      // 就是boss亮血条的时候说，2句话应该间隔有5-6秒应该不至于说不完第一句
      // 话被第二句话打断。"——台词1 在 3s（黑屏转淡入，椅子即将可见），
      // 台词2 在 9s 开战瞬间（hitbox/血条开启的同一帧），间隔恰好 6s。
      if (this.introT >= INTRO_BLACK_END && !this._talkChair) {
        this._talkChair = true;
        pluginBridge.say('嗯？这个椅子它说它需要"帕瓦"，"帕瓦"是什么呀？');
      }
      // 9s：背对剪影消失 → 椅子在同一像素点出现 → 开战
      if (this.introT >= INTRO_END) {
        this.state = 'wander';
        this.stateT = 0;
        this.chairVisible = true;
        p.noAttack = false;              // 演出结束恢复攻击
        // hitbox 与血条此刻开启（用户反馈 2）
        this.boss.bossPhase.invulnTimer = 0;
        if (!this._talkAwake) {
          this._talkAwake = true;
          pluginBridge.say('哇！它动起来了喵！！');
        }
      }
      return;                            // 演出期间不跑 AI/技能
    }

    // ---- 第一百五十一轮：boss 锚点钳制在场地内 —— sphere 的后撤每次把
    // 椅子朝远离玩家方向推 90px 且无边界（用户："boss 甚至可以在顶部
    // 彻底消失"）。每帧归位，瞬移/收刀位移同样受约束。
    this.bx = Math.max(ARENA.cx - ARENA.hw + 90, Math.min(ARENA.cx + ARENA.hw - 90, this.bx));
    this.by = Math.max(ARENA.cy - ARENA.hh + 90, Math.min(ARENA.cy + ARENA.hh - 90, this.by));
    this.boss.x = this.bx;
    this.boss.y = this.by;

    // ---- 90 秒限时：没打死就收刀离场，什么都不给 ----
    //（win 状态停表 —— 胜利演出 1.4 秒内别让 timeout 抢先结算）
    if (this.state !== 'win') {
      this.timeLeft -= dtReal;
      if (this.timeLeft <= 0) { this.finish('timeout'); return; }
    }

    // ---- 第一百四十八轮：P3 蓄力演出推进（5 秒：入定 → 汇聚 → 拔刀）----
    if (this.chargeT > 0) {
      this.chargeT = Math.max(0, 5 - this.stateT);
      this.timeScale = 0.2;                        // 全场极慢速（boss 走 dtReal）
      // 反向粒子：全屏随机生成、加速向椅子汇聚（第 0.5~4.2 秒持续）
      if (this.chargeT > 0.8 && this.chargeT < 4.6) {
        const n = 2 + ((Math.random() * 2) | 0);
        for (let i = 0; i < n; i++) {
          const a = Math.random() * TAU;
          const d = 260 + Math.random() * 340;
          this.chargeParts.push({
            sx: this.bx + Math.cos(a) * d, sy: this.by + Math.sin(a) * d,
            k: 0, sp: 0.55 + Math.random() * 0.5,
            hue: Math.random() < 0.6 ? 'c' : 'v',   // 青色 / 紫色
          });
        }
      }
      // 空间裂缝：暗紫折线随机闪烁（第 2~4.5 秒）
      if (this.chargeT < 3.6 && this.chargeT > 0.5 && Math.random() < 0.22) {
        this.chargeCracks.push({
          x: this.bx + (Math.random() - 0.5) * 380,
          y: this.by + (Math.random() - 0.5) * 300,
          seed: (Math.random() * 1e9) | 0, t: 0, life: 0.35 + Math.random() * 0.25,
        });
      }
      // 第 5 秒：全部停止并消失 → 绝对黑屏 0.2s（BGM 淡出恰好归零）
      if (this.chargeT <= 0.2) {
        this.chargeParts.length = 0;
        this.chargeCracks.length = 0;
        this.chargeBlack = 1;
      } else {
        this.chargeBlack = 0;
      }
      // 粒子推进：k 递增（先慢后快 = 加速吸感），到心即灭
      for (let i = this.chargeParts.length - 1; i >= 0; i--) {
        const q = this.chargeParts[i];
        q.k += (0.35 + 0.65 * q.k) * q.sp * dtReal * 1.4;
        if (q.k >= 1) this.chargeParts.splice(i, 1);
      }
      for (let i = this.chargeCracks.length - 1; i >= 0; i--) {
        const c = this.chargeCracks[i];
        c.t += dtReal;
        if (c.t >= c.life) this.chargeCracks.splice(i, 1);
      }
    }

    // ---- P3 转阶段演出：BGM 5 秒淡出；锁血结束那一刻放大招 + 音量瞬回 ----
    //（第一百四十五轮：不再要求 state==='pause' —— stateT 与锁血倒计时的
    // 浮点累计有细微差，pause 可能提前一帧切走，检测条件从此永假，
    // BGM 音量永远停在 0（用户实测"直接没有bgm了"）。改用 _p3Pending
    // 旗标，wander/cast 任何状态下锁血+淡出都归零就立刻触发）
    if (this.bgmFade > 0) {
      this.bgmFade = Math.max(0, this.bgmFade - dtReal);
      try { if (this._bgm) this._bgm.volume = 0.85 * (this.bgmFade / 5); } catch { /* */ }
    }
    if (this.phase === 3 && this._p3Pending
        && this.boss.bossPhase.invulnTimer <= 0 && this.bgmFade <= 0) {
      // 锁血（5 秒）结束的那一刻：音量瞬间恢复 100%、直接从 2 分钟开始播
      this._p3Pending = false;
      this._p3UltFired = true;
      this.ultBgmLocked = true;          // 之后大招不再碰 BGM 任何设置
      try {
        if (this._bgm) {
          this._bgm.volume = 0.85;
          // 先确保在播再 seek —— 未加载的媒体元素 currentTime 赋值会被静默忽略
          if (this._bgm.paused) this._bgm.play().catch(() => { /* */ });
          this._bgm.currentTime = BGM_START_AT;
          this._p3Seeked = true;         // 回归断言用：seek 指令已发出
        }
      } catch { /* */ }
      // 第一百四十八轮：拔刀瞬间 —— 黑屏结束、白闪爆响、粒子清场
      this.chargeT = 0;
      this.chargeBlack = 0;
      this.chargeParts.length = 0;
      this.chargeCracks.length = 0;
      this.whiteFlash = 1;
      this._startUlt();
    }

    // ---- 阶段切换（血量门槛，切阶段清屏 + 短缓冲）----
    if (this.phase === 1 && this.boss.hp <= VERGIL_MAX_HP * 0.66) this._enterPhase(2);
    else if (this.phase === 2 && this.boss.hp <= VERGIL_MAX_HP * 0.33) this._enterPhase(3);

    // ---- 第一百六十轮：常驻锁血（替代 154 轮的 P3 专属里程碑）----
    // 每累计受到 2000 伤害 → 无敌 **累加** 0.5s（P3 每段 0.8s）。
    // 用户追认的叠加语义："假设玩家用了一次高伤害，一发打了他1万血，
    // 他是会触发5次锁血无敌2.5秒还是1次0.5秒？" —— 叠加才满足
    // "玩家再肥他也可以坚持30秒左右"的预算（总无敌 ≈ 总承伤/2000×段长）。
    // 复用 invulnTimer 通道（getter invulnerable 自动生效：takeDamage 全
    // blocked + 渲染层青色护盾），转阶段 5s 锁血只会被加长不被缩短。
    // while 处理单帧大量伤害（一发 1 万 = 5 段 = 2.5s）。
    {
      const drop = this._hpLast - this.boss.hp;
      // 转阶段演出窗口（_p3Pending：5s 大招锁血）期间冻结常驻锁 ——
      // 演出锁本身全挡伤害（真实战斗 drop=0），这里防的是任何直接改血
      // 的路径把大招演出拖长。
      if (drop > 0 && !this._p3Pending) {
        this._dmgAccum += drop;
        while (this._dmgAccum >= LOCK_DMG_STEP) {
          this._dmgAccum -= LOCK_DMG_STEP;
          this.boss.bossPhase.invulnTimer += this.phase >= 3 ? P3_LOCK_INVULN : LOCK_INVULN;
        }
      }
      this._hpLast = this.boss.hp;
    }

    // ---- 玩家死亡 → 结算 ----
    // ---- 第一百八十四轮（4P 多人兼容，用户："1p倒下就判定战败了，没有
    // 倒地"）—— 战败判定从"只看 P1"改成"**全队**失去战斗力"（每个玩家
    // 都 dead 或 downed）—— P1 倒下时只要还有队友站着，战斗就继续，
    // 队友可以救起 P1 或接替输出。单人局行为不变（team 只有 P1）。
    {
      const team = this.main.world?.players?.() ?? [p];
      if (team.every((pl) => !pl || pl.dead || pl.downed)) { this.finish('death'); return; }
    }
    // ---- Boss 被打死 → 胜利演出 ----
    // 第一百四十五轮：win 状态下不再重复进这里 —— 此前每帧在 _win() 后
    // return，状态机的 win 分支（1.4 秒后 finish）永远执行不到，
    // 游戏直接卡住（用户实测"死掉之后游戏直接卡住了，没有结算"）。
    if (this.state !== 'win' && (this.boss.dead || this.boss.hp <= 0)) { this._win(); return; }

    // ---- AI 状态机 ----
    this.stateT += dtReal;
    if (this.state === 'wander') {
      this._idle();
      if (this.stateT >= this.castGap / this.spd() && !this.over) {
        this._pickSkill();
      }
    } else if (this.state === 'cast') {
      this._idle();
      this._tickCast(dtReal);
    } else if (this.state === 'ult') {
      this._tickUlt(dtReal);
    } else if (this.state === 'pause') {
      // 收刀停顿（默认 1s）/ P3 转阶段（5s，配合 BGM 淡出与锁血）
      if (this.stateT >= this.pauseDur) { this.state = 'wander'; this.stateT = 0; }
    } else if (this.state === 'win') {
      if (this.stateT >= 1.4) this.finish('beat');
    }

    // ---- 第一百四十六轮：格挡领域状态 ----
    // P1/P2 且"游荡（不施法）"时自动格挡；出招瞬间光圈闪烁破碎（提示玩家）。
    // 近战不在此拦（拼刀他挡不住，melee 伤害照常走 dealDamage）。
    const wasGuard = this.guarding;
    this.guarding = this.phase <= 2 && this.state === 'wander' && !p.dead;
    // ---- 第一百八十四轮（用户："格挡特效改成一轮技能只生效1-2次……
    // 他每个动作间隔格挡那一小会次数减少了也不会太突兀"）—— 每次进入
    // 格挡（wander 期开始）重置本 burst 的特效计数（parryAt 里判）。
    if (this.guarding && !wasGuard) this._parryBurstN = 0;
    if (this.guarding) this.guardT += dtReal;
    if (wasGuard && !this.guarding && this.guardT > 0.4) {
      this.guardShatterT = 0.35;       // 出招了 —— 光圈闪烁破碎
      this.guardT = 0;
    } else if (!this.guarding) {
      this.guardT = 0;
    }
    this.guardShatterT = Math.max(0, this.guardShatterT - dtReal);

    // ---- 格挡特效推进 + 涟漪窗口 ----
    this._parrySfxT = Math.max(0, this._parrySfxT - dtReal);
    this._parryFxT = Math.max(0, (this._parryFxT ?? 0) - dtReal);
    this._parryWindow -= dtReal;
    if (this._parryWindow <= 0) {
      this._parryWindow = 0.3;
      if (this._parryCount >= 6) this.parryRipples.push({ t: 0, life: 0.4 });
      this._parryCount = 0;
    }
    for (let i = this.parryFx.length - 1; i >= 0; i--) {
      const s = this.parryFx[i];
      s.t += dtReal;
      if (s.t >= s.life) this.parryFx.splice(i, 1);
    }
    for (let i = this.parrySparks.length - 1; i >= 0; i--) {
      const s = this.parrySparks[i];
      s.t += dtReal;
      s.x += s.vx * dtReal;
      s.y += s.vy * dtReal;
      s.vx *= 0.92; s.vy *= 0.92;
      if (s.t >= s.life) this.parrySparks.splice(i, 1);
    }
    for (let i = this.parryRipples.length - 1; i >= 0; i--) {
      const r = this.parryRipples[i];
      r.t += dtReal;
      if (r.t >= r.life) this.parryRipples.splice(i, 1);
    }

    // ---- 幻影剑：自动触发计时 + 近战计量在 dealDamage 里喂 ----
    if (this.state !== 'intro') {
      this.mirageAutoCd -= dtReal;
      if (this.mirageAutoCd <= 0) {
        this.mirageAutoCd = MIRAGE_AUTO_CD;
        this._tryMirage();               // 20 秒一次（用户补充的保底通道）
      }
    }

    // ---- 幻影剑技能推进 ----
    if (this.mirage) this._tickMirage(dtReal);

    // ---- 幻影剑火星（环绕时随机飘散）----
    if (this.mirage && Math.random() < 0.5) {
      const sw = this.mirage.swords[(Math.random() * this.mirage.swords.length) | 0];
      const a = sw.a + this.mirage.orbitA;
      this.mirageEmbers.push({
        x: this.bx + Math.cos(a) * MIRAGE_ORBIT,
        y: this.by + Math.sin(a) * MIRAGE_ORBIT,
        vx: (Math.random() - 0.5) * 30, vy: -30 - Math.random() * 40,
        t: 0, life: 0.5 + Math.random() * 0.4,
      });
    }
    for (let i = this.mirageEmbers.length - 1; i >= 0; i--) {
      const e = this.mirageEmbers[i];
      e.t += dtReal;
      e.x += e.vx * dtReal;
      e.y += e.vy * dtReal;
      if (e.t >= e.life) this.mirageEmbers.splice(i, 1);
    }

    // ---- 直线月牙推进（141 轮改：速度翻倍、不追踪、直直冲出去）----
    for (let i = this.waves.length - 1; i >= 0; i--) {
      const wv = this.waves[i];
      wv.t += dtReal;
      if (wv.t >= wv.life) { this.waves.splice(i, 1); continue; }
      wv.x += Math.cos(wv.angle) * wv.speed * dtReal;
      wv.y += Math.sin(wv.angle) * wv.speed * dtReal;
      if (!p.dead && Math.hypot(p.x - wv.x, p.y - wv.y) < wv.r * 0.55) {
        if (this._hitPlayer()) wv.t = wv.life;
      }
    }

    // ---- 冲刺路径刀痕（月牙残影，可带滑出速度）----
    for (let i = this.trails.length - 1; i >= 0; i--) {
      const tr = this.trails[i];
      tr.t += dtReal;
      tr.x += (tr.vx ?? 0) * dtReal;
      tr.y += (tr.vy ?? 0) * dtReal;
      if (tr.t >= tr.life) this.trails.splice(i, 1);
    }

    // ---- AOE 拉回圈内的随机半月刀波（碰到玩家算一次伤害）----
    if (this.aoe && this.aoe.mode === 'hold') {
      this.aoe.spawnT = (this.aoe.spawnT ?? 0) + dtReal;
      if (this.aoe.spawnT >= 0.075) {          // 141 轮再改：数量翻倍
        this.aoe.spawnT = 0;
        const a = Math.random() * TAU;
        const rr = this.aoe.r * (0.15 + Math.random() * 0.75);
        this.aoeBlades.push({
          x: this.aoe.x + Math.cos(a) * rr,
          y: this.aoe.y + Math.sin(a) * rr,
          angle: Math.random() * TAU,
          t: 0, life: 0.55,
        });
      }
    }
    for (let i = this.aoeBlades.length - 1; i >= 0; i--) {
      const ab = this.aoeBlades[i];
      ab.t += dtReal;
      if (ab.t >= ab.life) { this.aoeBlades.splice(i, 1); continue; }
      if (!p.dead && Math.hypot(p.x - ab.x, p.y - ab.y) < 20) {
        this._hitPlayer();
      }
    }

    // ---- 冲刺斩条带判定 ----
    for (let i = this.slashBars.length - 1; i >= 0; i--) {
      const b = this.slashBars[i];
      b.t += dtReal;
      if (b.t >= b.life) { this.slashBars.splice(i, 1); continue; }
      if (!p.dead && b.armed && Math.abs(p.y - b.y) < 26) this._hitPlayer();
    }
    for (let i = this.dashes.length - 1; i >= 0; i--) {
      const d = this.dashes[i];
      d.t += dtReal;
      if (d.t >= d.life) this.dashes.splice(i, 1);
    }

    // ---- AOE 拉回 ----
    if (this.aoe) {
      const a = this.aoe;
      a.st += dtReal;
      if (a.mode === 'grow') {
        a.r = a.maxR * Math.min(1, a.st / 0.35) ** 0.6;
        if (a.st >= 0.35) { a.mode = 'hold'; a.st = 0; }
      } else if (a.mode === 'hold') {
        if (a.st >= 0.5) { a.mode = 'collapse'; a.st = 0; }
      } else if (a.mode === 'collapse') {
        a.r = a.maxR * Math.max(0, 1 - a.st / 0.12);
        if (a.st >= 0.12) {
          // 收缩瞬间：还在圈里的玩家被吸住挨一刀
          if (!p.dead && Math.hypot(p.x - a.x, p.y - a.y) < a.maxR * 0.85) {
            this._hitPlayer();
          }
          a.done = true;
        }
      }
      if (a.done) this.aoe = null;
    }

    // ---- 球形次元斩 ----
    for (let i = this.rifts.length - 1; i >= 0; i--) {
      const rf = this.rifts[i];
      rf.st += dtReal;
      if (!rf.fired && rf.st >= rf.dur) {
        rf.fired = true;
        this._se('vergil_atk', 0.6);
        for (let k = 0; k < 10; k++) {
          this.radials.push({
            cx: rf.x, cy: rf.y,
            a: (k / 10) * TAU + Math.random() * 0.5,
            len: 500 + Math.random() * 80,
            t: 0, life: 0.32, seed: (Math.random() * 1e9) | 0,
          });
        }
        this.whiteFlash = Math.max(this.whiteFlash, 0.35);
      }
      if (rf.st >= rf.dur + 0.15) this.rifts.splice(i, 1);
    }
    for (let i = this.radials.length - 1; i >= 0; i--) {
      const rb = this.radials[i];
      rb.t += dtReal;
      if (rb.t >= rb.life) { this.radials.splice(i, 1); continue; }
      // 射线从裂缝向外推进的当前头端
      const prog = Math.min(1, rb.t / 0.2);
      const x2 = rb.cx + Math.cos(rb.a) * rb.len * prog;
      const y2 = rb.cy + Math.sin(rb.a) * rb.len * prog;
      if (!p.dead && distToSegment(p.x, p.y, rb.cx, rb.cy, x2, y2) < 13) {
        this._hitPlayer();
      }
    }

    // ---- 满屏刀光（大招期间，真实秒表）----
    for (let i = this.ultBlades.length - 1; i >= 0; i--) {
      const b = this.ultBlades[i];
      b.t += dtReal;
      if (b.t >= b.life) { this.ultBlades.splice(i, 1); continue; }
      if (!p.dead && distToSegment(p.x, p.y, b.x1, b.y1, b.x2, b.y2) < 13) {
        this._hitPlayer();
      }
    }
  }

  /** 扣血：5% maxHp 固定真伤，无视无敌帧；0.2s 内置 CD 收在函数里。
   *  ---- 第一百八十四轮（4P，用户："椅子基本只打1p"）—— 攻击目标从
   *  固定 P1 改成"活着且非倒地"的玩家**随机分散**：1/4 血压不再全部
   *  压在 P1 头上。单人局永远选中 P1，行为不变。 */
  _hitPlayer() {
    if (this.hitCd > 0) return false;
    const all = this.main.world?.players?.() ?? [this.main.player];
    const alive = all.filter((pl) => pl && !pl.dead && !pl.downed);
    // （v0.3.77 曾把随机数写成 `main.rng ?? Math.random` 赋给 r 再调用——
    // main.rng 是 RNG 实例对象（.next()），不是函数，r() 每次抛 TypeError
    // 并炸穿当帧 update——用户控制台实锤"vergil.js:667 r is not a function"，
    // 拉人圈这类每帧命中判定的技能必定卡。改用 Math.random，
    // 攻击分散不需要种子。）
    const p = alive.length ? alive[(Math.random() * alive.length) | 0] : this.main.player;
    if (p.dead) return false;
    p.takeDamage(0, this.bx, this.by, {
      ignoreIFrames: true,
      fixedDamage: p.maxHp * HIT_FRAC,
      noMiss: true,
    });
    this.hitCd = HIT_CD;
    this.edgeFlash = 0.5;
    return true;
  }

  _enterPhase(n) {
    this.phase = n;
    this.spdMul = n === 2 ? 1.1 : n === 3 ? 1.3 : 1;
    // 切阶段：清屏 + **5 秒锁血**（借用普通 Boss 的阶段锁通道：invulnTimer>0
    // 时 takeDamage 全 blocked + 血条 🔒 视觉；递减由本类 update 代跑）
    this.boss.bossPhase.invulnTimer = 5;
    this.waves.length = 0;
    this.rifts.length = 0;
    this.radials.length = 0;
    this.aoe = null;
    this.state = 'wander';
    this.stateT = -0.8;                        // 0.8s 缓冲再开始放技能
    // 141 轮再改：转阶段不弹播报（用户："血条下面那个播报去掉"）
    if (n === 3) {
      // P3 演出：5 秒静止（BGM 音量 5 秒内 100%→0%，boss 停止攻击），
      // 锁血结束的那一刻由 update 触发大招 + 音量瞬回 100%@2:00
      this.state = 'pause';
      this.stateT = 0;
      this.pauseDur = 5;
      this.bgmFade = 5;
      this.ultCd = 0;
      this._p3Pending = true;   // 锁血+淡出都归零时由 update 触发大招
      // 第一百六十轮：常驻锁血不需要 P3 基线了（全程按"累计受伤 2000"算，
      // _dmgAccum 跨阶段连续累计，转阶段不清零）
      // 第一百四十八轮：蓄力入定 —— 全场极慢速（boss 本体走 dtReal 不受影响）
      this.timeScale = 0.2;
      this.chargeT = 5;
      this.chargeParts.length = 0;
      this.chargeCracks.length = 0;
      this.chargeBlack = 0;
    }
  }

  /** 站桩待机：不移动不转向（呼吸浮动在渲染层），只同步实体坐标 */
  _idle() {
    this.boss.x = this.bx;
    this.boss.y = this.by;
  }

  _pickSkill() {
    // P3：大招冷却好了就先放大招；否则四技能**纯随机**（第一百四十三轮，
    // 用户："让他不要规规矩矩循环，而是纯随机。比如他乐意的话可以球形
    // 次元斩5连休息休息一秒后再来一个5连"—— 允许连续同一技能）
    if (this.phase === 3) {
      this.ultCd -= (this.castGap / this.spd());
      if (this.ultCd <= 0) { this._startUlt(); return; }
    }
    let name = SKILL_ORDER[(Math.random() * SKILL_ORDER.length) | 0];
    // 幻影剑被随机抽到时若已在放/刚放过，换成普通技能（自动/计量触发另有通道）
    if (name === 'mirage' && !this._tryMirage()) {
      name = SKILL_ORDER[(Math.random() * 4) | 0];
    }
    if (name === 'mirage') {
      // 幻影剑是独立浮游容器（不走 cast 状态机）—— 只记个名，行为在 _tickMirage
      this.skill = { name: 'mirage', st: 0, fired: 0 };
      this.state = 'wander';
      this.stateT = 0;
      return;
    }
    this.state = 'cast';
    this.stateT = 0;
    this.skill = { name, st: 0, fired: 0 };
  }

  // ---------------------------------------------------------
  // 第一百四十六轮：幻影剑（Mirage Edge）
  // ---------------------------------------------------------

  /**
   * 触发幻影剑。三个入口：
   *  · _pickSkill 随机抽中；
   *  · 累计近战伤害达玩家 maxHp 的 10%（world.dealDamage → noteMelee）；
   *  · 每 20 秒自动一次（用户补充："防止玩家是纯远程完全没近战看不到"）。
   * @returns {boolean} 是否真的开始施放
   */
  _tryMirage() {
    if (this.mirage || this.over || this.state === 'intro' || this.state === 'win') return false;
    this.mirage = {
      st: 0, fired: 0, orbitA: 0,
      swords: Array.from({ length: MIRAGE_COUNT }, (_, i) => ({
        a: (i / MIRAGE_COUNT) * TAU, seed: (Math.random() * 1e9) | 0,
      })),
      flying: [],
    };
    this._se('vergil_atk', 0.6);
    return true;
  }

  /** 环绕 3 秒 → 依次朝玩家当前位置发射 → 飞行推进/命中（5% 真伤通道） */
  _tickMirage(dtReal) {
    const m = this.mirage;
    const p = this.main.player;
    m.st += dtReal;
    m.orbitA += dtReal * 2.2;

    // 环绕期结束 → 依次发射（每把锁定发射瞬间玩家的位置）
    if (m.st >= MIRAGE_SUMMON && m.fired < MIRAGE_COUNT) {
      const should = Math.min(MIRAGE_COUNT, 1 + (((m.st - MIRAGE_SUMMON) / MIRAGE_GAP) | 0));
      while (m.fired < should) {
        const sw = m.swords[m.fired];
        const a = sw.a + m.orbitA;
        const px = this.bx + Math.cos(a) * MIRAGE_ORBIT;
        const py = this.by + Math.sin(a) * MIRAGE_ORBIT;
        m.flying.push({
          x: px, y: py,
          angle: Math.atan2(p.y - py, p.x - px),
          speed: MIRAGE_SPEED, t: 0, life: 3,
          seed: sw.seed, trail: [], hitCd: 0,
        });
        m.fired++;
        this._se('vergil_atk', 0.45);
      }
    }

    // 飞行剑推进
    for (let i = m.flying.length - 1; i >= 0; i--) {
      const b = m.flying[i];
      b.t += dtReal;
      b.x += Math.cos(b.angle) * b.speed * dtReal;
      b.y += Math.sin(b.angle) * b.speed * dtReal;
      b.trail.push({ x: b.x, y: b.y });
      if (b.trail.length > 16) b.trail.shift();
      b.hitCd = Math.max(0, b.hitCd - dtReal);
      // 命中：5% 真伤通道（同刀波），每把剑 0.5s 最多咬一口
      if (!p.dead && b.hitCd <= 0
          && Math.hypot(p.x - b.x, p.y - b.y) < 26) {
        if (this._hitPlayer()) b.hitCd = 0.5;
      }
      // 飞出场外/超时 → 收
      if (b.t >= b.life
          || Math.abs(b.x - this.bx) > 1600 || Math.abs(b.y - this.by) > 1200) {
        m.flying.splice(i, 1);
      }
    }

    // 全部发完且飞完 → 收技能
    if (m.fired >= MIRAGE_COUNT && m.flying.length === 0) this.mirage = null;
  }

  /**
   * 近战计量：world.dealDamage 喂实际伤害，累计达玩家 maxHp 10% 触发一次。
   */
  noteMelee(dealt) {
    this.meleeMeter += dealt;
    const maxHp = this.main.player.stats.get('maxHp');
    if (this.meleeMeter >= maxHp * MIRAGE_MELEE_FRAC) {
      this.meleeMeter = 0;
      this._tryMirage();
    }
  }

  // ---------------------------------------------------------
  // 第一百四十六轮：格挡领域（P1/P2 游荡时斩碎远程子弹）
  // ---------------------------------------------------------

  /**
   * 斩碎一颗子弹（bullet.checkEnemyHit 在格挡时调用）：
   * 斩击线 + 火花 + 计数（0.3s 窗口 ≥6 颗触发空间涟漪）+ 音效节流。
   * @returns {boolean} 是否格挡（false = 未格挡，子弹正常结算）
   */
  parryAt(x, y) {
    if (!this.guarding) return false;
    // ---- 第一百八十四轮（用户实测真机"开头就卡顿"）—— 4 只 AI 贴脸
    // 输出后，每秒几十发弹被挡 → 每发一次斩击线+火花+伤害字+音效，真机
    // 渲染直接被刷爆。0.05s 全局节流 + **每 wander 期最多 2 次特效**
    // （用户："一轮技能只生效1-2次……次数减少了也不会太突兀"）：格挡
    // 判定本身（返回 true，弹照样碎）不受任何节流影响。
    if ((this._parryFxT ?? 0) > 0) return true;
    if ((this._parryBurstN ?? 0) >= 2) return true;
    this._parryFxT = 0.05;
    this._parryBurstN = (this._parryBurstN ?? 0) + 1;
    this._parryCount++;
    this.parryFx.push({ x, y, seed: (Math.random() * 1e9) | 0, t: 0, life: 0.1 });
    const n = 3 + ((Math.random() * 2) | 0);
    for (let i = 0; i < n; i++) {
      const a = Math.random() * TAU;
      const sp = 60 + Math.random() * 130;
      this.parrySparks.push({
        x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
        t: 0, life: 0.3 + Math.random() * 0.3,
      });
    }
    this._seParry();
    return true;
  }

  _tickCast(dtReal) {
    const sk = this.skill;
    if (!sk) { this.state = 'wander'; this.stateT = 0; return; }
    sk.st += dtReal;
    const p = this.main.player;
    const spd = this.spd();

    if (sk.name === 'triple') {
      // 141 轮终版：3 个刀波一组齐射，**连用 3 次 = 一共甩 9 个波**。
      // 每 0.2s 一轮，每轮扇形 ±0.2rad 三道，直线快冲（无追踪无旋摆）。
      const roundGap = 0.2 / spd;
      const rounds = Math.min(3, Math.floor(sk.st / roundGap) + 1);
      while (sk.fired < rounds) {
        const base = Math.atan2(p.y - this.by, p.x - this.bx);
        for (const off of [-0.2, 0, 0.2]) {
          this.waves.push({
            x: this.bx, y: this.by, angle: base + off,
            speed: 530 * spd, r: 46, t: 0, life: 2.2,
          });
        }
        this._se('vergil_atk', 0.5);
        sk.fired++;
      }
      if (sk.st > roundGap * 3 + 0.2) this._endCast();
    } else if (sk.name === 'dash') {
      if (!sk.dashed && sk.st >= 0.25 / spd) {
        sk.dashed = true;
        // 转向（冲刺斩是转向时机之一）
        this.chairFacing = Math.atan2(p.y - this.by, p.x - this.bx);
        // 瞬移到玩家面前（玩家与旧位之间 70px 处）
        const dx = this.bx - p.x, dy = this.by - p.y;
        const d = Math.hypot(dx, dy) || 1;
        const nx = p.x + (dx / d) * 70, ny = p.y + (dy / d) * 70;
        this.dashes.push({ x1: this.bx, y1: this.by, x2: nx, y2: ny, t: 0, life: 0.3 });
        // 141 轮改：路径上每 55px 一道月牙刀痕 —— 表示他瞬移了多远
        const gap = 55;
        const n = Math.max(1, Math.floor(d / gap));
        const dir = Math.atan2(-dy, -dx);            // 刀口朝冲刺方向
        for (let i = 1; i <= n; i++) {
          const t = i / (n + 1);
          this.trails.push({
            x: this.bx + dx * t, y: this.by + dy * t,
            angle: dir,
            t: i * 0.03, life: 0.5,
          });
        }
        // 141 轮再改：闪现路径**两侧**再划过几个半月弯弧 ——
        // 沿路径两侧错位布置，刀口向两侧斜挑，并向外滑出（挥砍感）
        for (const side of [-1, 1]) {
          for (let i = 1; i <= Math.max(2, n - 1); i++) {
            const t = i / (Math.max(2, n - 1) + 1);
            const px = -dy / d, py = dx / d;         // 路径法线
            const off = side * (46 + Math.random() * 26);
            this.trails.push({
              x: this.bx + dx * t + px * off,
              y: this.by + dy * t + py * off,
              angle: dir + side * (0.9 + Math.random() * 0.5),
              vx: px * side * 90, vy: py * side * 90,   // 向两侧滑出
              t: i * 0.03 + 0.05, life: 0.55,
            });
          }
        }
        this.bx = nx; this.by = ny;
        this.boss.x = nx; this.boss.y = ny;
        // 横贯屏幕的横向刀光（以玩家所在高度为准）
        const halfW = this.main.camera.viewW;
        this.slashBars.push({ y: p.y, x: this.main.camera.cx, halfW, t: 0, life: 0.22, armed: true });
        this._se('vergil_atk', 0.6);
        this.whiteFlash = Math.max(this.whiteFlash, 0.3);
      }
      if (sk.st > 0.7 / spd) this._endCast();
    } else if (sk.name === 'aoe') {
      if (!sk.started && sk.st >= 0.1) {
        sk.started = true;
        this.aoe = { x: this.bx, y: this.by, r: 0, maxR: 520, mode: 'grow', st: 0 };
        this._se('vergil_aoe', 0.6);       // 攻击2.ogg：AOE 拉回
      }
      if (sk.st > (0.1 + 0.35 + 0.5 + 0.12 + 0.3) / Math.sqrt(spd)) this._endCast();
    } else if (sk.name === 'sphere') {
      // 【球形次元斩】（普通技能，≠大招）：连放 5 次裂缝 —— 每 0.5s 一道，
      // 2.5s 总计，放完休息 1 秒（一切动作静止）。只后撤一次。
      if (!sk.retreated && sk.st >= 0.05) {
        sk.retreated = true;
        const dx = this.bx - p.x, dy = this.by - p.y;
        const d = Math.hypot(dx, dy) || 1;
        this.bx += (dx / d) * 90;
        this.by += (dy / d) * 90;
        this.boss.x = this.bx; this.boss.y = this.by;
      }
      const gap = 0.5 / spd;
      const should = Math.min(5, Math.floor(sk.st / gap) + 1);
      while (sk.fired < should) {
        sk.fired++;
        // 141 轮再改：第 1/3/5 斩瞄玩家当前位置；第 2 斩留在第 1 斩位置、
        // 第 4 斩留在第 3 斩位置（奇数更新落点，偶数复用）
        if (sk.fired % 2 === 1) sk.lastSpot = { x: p.x, y: p.y };
        this.rifts.push({ x: sk.lastSpot.x, y: sk.lastSpot.y, st: 0, dur: 0.3, fired: false });
        this._se('vergil_atk', 0.5);
      }
      if (sk.st > gap * 5 + 0.4) {
        this._endCast();
        // 连放结束 → 维吉尔收势休息 1 秒 + 收刀音效（第一百四十七轮，
        // 用户："放了一个收刀.ogg……放在球形次元斩5连结束后……当做收刀音效"）
        this._se('vergil_shutou', 0.65);
        this.state = 'pause';
        this.stateT = 0;
      }
    }
  }

  _endCast() {
    this.state = 'wander';
    this.stateT = 0;
    this.skill = null;
  }

  // ---- 【大招】满屏次元斩 —— "大招"这个词专指它（会减慢时间的那个）：
  // timeScale=0.05 + Bury the Light + 5 连快闪 + 收刀碎屏 + 停顿 1 秒。
  // 普通技能池里的"球形次元斩"（sphere）是另一个技能，别搞混。----
  _startUlt() {
    const p = this.main.player;
    this.state = 'ult';
    this.stateT = 0;
    this.ultCount++;
    this.timeScale = 0.05;            // 全场慢动作（用户硬性要求）
    this.chairVisible = false;        // 时停消失
    this._ultWaveN = 0;               // 已爆发的连数（0..5）
    this._ultWaveT = 0;
    // 大招是转向时机
    this.chairFacing = Math.atan2(p.y - this.by, p.x - this.bx);
    this._se('vergil_ult', 0.85);     // 次元斩.ogg
    // BGM：P3 大招（_p3UltFired）由转阶段演出直接操控过；此后
    // **大招不再碰 BGM 任何设置**（用户："之后不变bgm任何设置"）。
    if (!this.ultBgmLocked) {
      try {
        if (this._bgm) {
          if (!this._bgmStarted) {
            this._bgm.currentTime = BGM_START_AT;
            this._bgm.play().catch(() => { /* 自动播放被拦就安静 */ });
            this._bgmStarted = true;
          } else if (this._bgm.paused) {
            this._bgm.play().catch(() => { /* */ });
          }
        }
      } catch { /* */ }
    }
  }

  _tickUlt(dtReal) {
    const cam = this.main.camera;
    // 蓄力 0.3s → 10 连（141 轮再改：次数时间双翻倍 —— 每 0.18s 一连、
    // 每连 8 道、刀光稍留）→ 收刀 → 停顿 1s
    const CHARGE = 0.3, GAP = 0.18, COMBOS = 10;
    if (this.stateT >= CHARGE && this._ultWaveN < COMBOS) {
      if (!this._ultWaveT) this._ultWaveT = 0;
      this._ultWaveT += dtReal;
      while (this._ultWaveT >= GAP && this._ultWaveN < COMBOS) {
        this._ultWaveT -= GAP;
        this._ultWaveN++;
        this.veil = Math.min(1, this.veil + 0.11);
        // 第一百四十九轮：每波爆发 —— 白闪 0.1s + 重震屏（"咔咔咔"打击感）
        this.whiteFlash = Math.max(this.whiteFlash, 0.45);
        cam.shake(9, 0.15);
        const vw = cam.viewW, vh = cam.viewH;
        for (let i = 0; i < 8; i++) {
          const a = Math.random() * TAU;
          const ox = cam.cx + (Math.random() - 0.5) * vw;
          const oy = cam.cy + (Math.random() - 0.5) * vh;
          const half = Math.hypot(vw, vh) * 0.62;
          this.ultBlades.push({
            x1: ox - Math.cos(a) * half, y1: oy - Math.sin(a) * half,
            x2: ox + Math.cos(a) * half, y2: oy + Math.sin(a) * half,
            seed: (Math.random() * 1e9) | 0, t: 0, life: 0.2,
          });
          // 刀锋火花：两端 + 路径中段随机溅出（白色细长，0.2s 寿命）
          for (let s = 0; s < 4; s++) {
            const tt = s === 0 ? 0 : s === 1 ? 1 : Math.random();
            const sx = ox - Math.cos(a) * half + (2 * tt - 1) * Math.cos(a) * half;
            const sy = oy - Math.sin(a) * half + (2 * tt - 1) * Math.sin(a) * half;
            const sa = a + Math.PI / 2 + (Math.random() - 0.5) * 2.4;
            const sp = 160 + Math.random() * 260;
            this.bladeSparks.push({
              x: sx, y: sy, vx: Math.cos(sa) * sp, vy: Math.sin(sa) * sp,
              ang: sa, t: 0, life: 0.16 + Math.random() * 0.08,
            });
          }
        }
      }
    }
    if (this._ultWaveN >= COMBOS && this.timeScale < 1) {
      // 收刀：恢复时间、瞬移屏幕中央、白闪 + 碎屏
      this.timeScale = 1;
      this.veil = 0;
      this._ultWaveT = 0;
      this.chairVisible = true;
      this.bx = cam.cx; this.by = cam.cy - 60;
      this.boss.x = this.bx; this.boss.y = this.by;
      this.whiteFlash = 1;
      this._shatterBig(cam.cx, cam.cy - 60);
      if (this.phase === 3) this.ultCd = ULT_CD;    // 10 秒后再来一次
      // 收刀停顿复位为默认 1 秒（P3 转阶段曾把它设成 5）
      this.pauseDur = 1.0;
      // 5 连结束 → boss 停顿 1 秒一切动作（喘息收刀）
      this.state = 'pause';
      this.stateT = 0;
    }
  }

  /**
   * 满屏大碎裂（141 轮：收刀碎屏放大到接近半屏）——
   * 以 (x,y) 为中心、半屏半径内面积均匀撒 26 个爆点，每点 2 片大块碎片。
   */
  _shatterBig(x, y) {
    const R = this.main.camera.viewH * 0.5;    // ≈ 半屏
    for (let i = 0; i < 26; i++) {
      const a = Math.random() * TAU;
      const rr = Math.sqrt(Math.random()) * R;
      spawnShatter(this.shards, x + Math.cos(a) * rr, y + Math.sin(a) * rr,
        2, 1.4, 1.3);
    }
  }

  _win() {
    if (this.state === 'win') return;
    this.state = 'win';
    this.stateT = 0;
    this.timeScale = 1;
    this.veil = 0;
    this.waves.length = 0;
    this.ultBlades.length = 0;
    this.rifts.length = 0;
    this.radials.length = 0;
    this.trails.length = 0;
    this.aoeBlades.length = 0;
    this.slashBars.length = 0;
    this.dashes.length = 0;
    this.chairVisible = true;
    this.whiteFlash = 1;
    this._shatterBig(this.bx, this.by);
    this.main.ui.toast('大…大排档椅子？！？！碎了', 'good', 4000);
  }

  // ---------------------------------------------------------
  // 渲染（renderer.render 挂点）
  // ---------------------------------------------------------

  /**
   * 场地背景层（世界坐标系，画在游戏背景之上、一切实体之下 ——
   * renderer 在背景缓存之后单独调用）。
   * 1) ARENA 方形内画大排档椅子背景图（芙兰同款位置），透明度随入场
   *    演出 3→6 秒从 0→1；
   * 2) 全屏压暗底 + 场地挖洞 —— 用户反馈 1："背景没有全黑"。维吉尔战
   *    期间场地外一律近黑，只留方形场地亮着（贴图保持原亮度）。
   */
  renderArena(ctx, camera) {
    const { cx, cy, hw, hh } = ARENA;
    // ---- 场地边框（第一百五十一轮）—— 玩家钳制的边界可视化，淡淡一圈
    // 青色描边，让人知道"墙在哪"，黑幕从这条线外开始 ----
    // 185 轮：旧的 0.28 透明度细线 → 脉动辉光线 + 四角括号（入场演出 3s 起淡入）
    const fxA = this.state === 'intro'
      ? Math.max(0, Math.min(1, (this.introT - INTRO_BLACK_END) / (INTRO_SCENE_END - INTRO_BLACK_END)))
      : 1;
    VFX.drawArenaFrame(ctx, ARENA, this.t, fxA);
    // ---- 全屏压暗底，方形场地挖洞（evenodd）----
    ctx.save();
    ctx.beginPath();
    const vw = camera.viewW ?? 1600, vh = camera.viewH ?? 900;
    ctx.rect(camera.cx - vw / 2 - 8, camera.cy - vh / 2 - 8, vw + 16, vh + 16);
    ctx.rect(cx - hw, cy - hh, hw * 2, hh * 2);
    ctx.clip('evenodd');
    ctx.fillStyle = 'rgba(4,4,10,0.9)';
    ctx.fill();
    ctx.restore();
    // 185 轮：场地外的"纯黑"改造成虚空 —— 漂浮的幽灵剑 + 青色光尘 + 冷雾（只画在场地外）
    VFX.drawVoid(ctx, camera, ARENA, this.t, fxA);

    // ---- 场地内的大排档背景图 ----
    const img = this.arenaImg;    if (!img.complete || !img.naturalWidth) return;
    // 淡入曲线：0-3s 全黑看不到；3-6s 0→1；之后常驻
    const t = this.state === 'intro' ? this.introT : 99;
    if (t < INTRO_BLACK_END) return;
    const alpha = this.state === 'intro'
      ? Math.min(1, (t - INTRO_BLACK_END) / (INTRO_SCENE_END - INTRO_BLACK_END))
      : 1;
    ctx.save();
    ctx.globalAlpha = alpha;
    // clip 方形场地（芙兰同款）
    ctx.beginPath();
    ctx.rect(cx - hw, cy - hh, hw * 2, hh * 2);
    ctx.clip();
    // cover 拉伸：短边贴满
    const scale = Math.max((hw * 2) / img.naturalWidth, (hh * 2) / img.naturalHeight);
    const w2 = img.naturalWidth * scale, h2 = img.naturalHeight * scale;
    ctx.drawImage(img, cx - w2 / 2, cy - h2 / 2, w2, h2);
    ctx.restore();
  }

  /** 世界层：椅子、刀波、光圈、裂缝、冲刺残影（世界坐标系） */
  renderWorld(ctx, camera) {
    // 第一百四十七轮：**防御性归位 alpha** —— 玩家特效透明度 0% 时，
    // _scaledCtx 的 Proxy 会把 globalAlpha=0 写进共享的原 ctx 并残留
    //（玩家子弹/特效画完 → 椅子继承 0 → "boss 不攻击就透明，攻击的
    // 时候他的技能特效自己重设 alpha 又出现"，用户实测现象完全吻合）。
    // 演出层是 boss 本体，永远不接玩家的特效透明度。
    ctx.globalAlpha = 1;
    // 入场演出：大排档背对剪影（Boss 大小，站在椅子将出现的位置）
    if (this.state === 'intro' && this.silImg.complete && this.silImg.naturalWidth) {
      const t = this.introT;
      if (t >= INTRO_BLACK_END && t < INTRO_END) {
        const alpha = Math.min(1, (t - INTRO_BLACK_END) / (INTRO_SCENE_END - INTRO_BLACK_END));
        const img = this.silImg;
        const h = 130;                        // 与 Boss 椅子（120）相当
        const w2 = h * (img.naturalWidth / img.naturalHeight);
        ctx.save();
        ctx.globalAlpha = alpha * 0.92;
        ctx.drawImage(img, this.bx - w2 / 2, this.by - h + 6, w2, h);
        ctx.restore();
      }
    }

    // 椅子本体
    if (this.chairVisible && this.chairImg.complete && this.chairImg.naturalWidth) {
      // 141 轮改：平时**不转向**（装逼剑客站桩），朝向只在冲刺斩/大招时更新
      const a = this.chairFacing;
      const img = this.chairImg;
      const h = 120;
      const w2 = h * (img.naturalWidth / img.naturalHeight);
      ctx.save();
      // 影子
      ctx.fillStyle = 'rgba(0,0,0,0.28)';
      ctx.beginPath();
      ctx.ellipse(this.bx, this.by + 6, w2 * 0.42, 12, 0, 0, TAU);
      ctx.fill();
      ctx.translate(this.bx, this.by);
      // 蓄力/大招时轻微浮动
      // 第一百四十八轮：P3 蓄力震颤 —— 高频微抖 ±2px + 离地悬浮 5px
      const charging = this.chargeT > 0;
      const hover = this.state === 'ult' ? -14
        : charging ? -5 + (Math.random() - 0.5) * 4
        : Math.sin(this.t * 2.2) * 3;
      ctx.translate(0, hover);
      if (charging) ctx.translate((Math.random() - 0.5) * 4, (Math.random() - 0.5) * 4);
      ctx.rotate(a - Math.PI / 2);              // 图片底部(+y) 指向玩家
      ctx.drawImage(img, -w2 / 2, -h + 6, w2, h);
      //（第一百四十七轮：删掉图片底部的青色描边横线 —— 用户反馈那像
      // 一条奇怪的"蓝色长方形"还跟着转向，确实没必要）
      ctx.restore();
    }

    // 三连齐射直线月牙
    for (const wv of this.waves) {
      const k = Math.min(1, (1 - wv.t / wv.life) * 3);
      VFX.drawBladeGlow(ctx, wv.x, wv.y, wv.angle, wv.r, k);
      drawCrescent(ctx, wv.x, wv.y, wv.angle, wv.r, k);
    }

    // 冲刺路径刀痕（中线直刀痕 + 两侧斜挑滑出的月牙，141 轮再改）
    for (const tr of this.trails) {
      const k = Math.min(1, (1 - tr.t / tr.life) * 2.2);
      drawCrescent(ctx, tr.x, tr.y, tr.angle, tr.vx ? 34 : 26, k * 0.85);
    }

    // AOE 圈内随机半月刀波
    for (const ab of this.aoeBlades) {
      const k = Math.min(1, (1 - ab.t / ab.life) * 2);
      VFX.drawBladeGlow(ctx, ab.x, ab.y, ab.angle, 30, k * 0.8);
      drawCrescent(ctx, ab.x, ab.y, ab.angle, 30, k * 0.9);
    }

    // 冲刺残影线
    for (const d of this.dashes) {
      const k = 1 - d.t / d.life;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(160,240,255,${0.4 * k})`;
      ctx.lineWidth = 10 * k + 2;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(d.x1, d.y1);
      ctx.lineTo(d.x2, d.y2);
      ctx.stroke();
      ctx.restore();
    }

    // 冲刺斩横贯刀光
    for (const b of this.slashBars) {
      const k = 1 - b.t / b.life;
      drawDashSlash(ctx, this.main.camera.cx, b.y, b.halfW, k);
    }

    // AOE 光圈
    if (this.aoe) {
      drawAoeRing(ctx, this.aoe.x, this.aoe.y, this.aoe.r,
        1, this.aoe.mode);
    }

    // 球形裂缝 + 放射折线
    for (const rf of this.rifts) {
      const prog = rf.st / rf.dur;
      drawRift(ctx, rf.x, rf.y, 60 - 26 * Math.min(1, prog), 0.5 + prog * 0.5);
    }
    for (const rb of this.radials) {
      const k = 1 - rb.t / rb.life;
      const prog = Math.min(1, rb.t / 0.2);
      const x2 = rb.cx + Math.cos(rb.a) * rb.len * prog;
      const y2 = rb.cy + Math.sin(rb.a) * rb.len * prog;
      drawZigzagBlade(ctx, rb.cx, rb.cy, x2, y2, rb.seed, k);
    }

    // 满屏刀光（世界系画，屏幕外延展足够覆盖）
    for (const b of this.ultBlades) {
      const k = 1 - b.t / b.life;
      drawZigzagBlade(ctx, b.x1, b.y1, b.x2, b.y2, b.seed, k);
    }

    // ---- 第一百四十六轮：格挡领域（P1/P2 游荡时）----
    if (this.guarding) {
      // 常态微光圈：很淡的一圈青色领域（"他在格挡"的可视化）
      const ga = 0.10 + Math.sin(this.t * 3.1) * 0.04;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(90,220,255,${ga})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(this.bx, this.by, 64, 0, TAU);
      ctx.stroke();
      ctx.strokeStyle = `rgba(160,240,255,${ga * 0.7})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(this.bx, this.by, 58, 0, TAU);
      ctx.stroke();
      ctx.restore();
    }
    // 出招瞬间：格挡光圈闪烁破碎（裂成弧段飞散）
    if (this.guardShatterT > 0) {
      const k = this.guardShatterT / 0.35;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * TAU + 0.4;
        const rr = 64 + (1 - k) * 46;
        const sx = this.bx + Math.cos(a) * rr;
        const sy = this.by + Math.sin(a) * rr;
        ctx.strokeStyle = `rgba(140,230,255,${k * 0.9})`;
        ctx.lineWidth = 2.4 * k;
        ctx.beginPath();
        ctx.arc(sx, sy, 9, a - 0.5, a + 0.5);
        ctx.stroke();
      }
      ctx.restore();
    }

    // ---- 第一百四十六轮：幻影剑（Mirage Edge）----
    // 环绕剑（召唤期淡入、飞行期剩下的未发剑继续环绕）
    if (this.mirage) {
      const m = this.mirage;
      const alpha = m.st < 0.6 ? m.st / 0.6 : 1;
      const scale = 1 + Math.sin(this.t * 2.4) * 0.035;   // 呼吸缩放
      for (let i = m.fired; i < m.swords.length; i++) {
        const sw = m.swords[i];
        const a = sw.a + m.orbitA;
        drawMirageEdge(ctx,
          this.bx + Math.cos(a) * MIRAGE_ORBIT,
          this.by + Math.sin(a) * MIRAGE_ORBIT,
          a + Math.PI / 2,                     // 剑尖朝切线方向（旋转感）
          alpha, { seed: sw.seed, scale });
      }
      // 火星粒子
      for (const e of this.mirageEmbers) {
        const k = 1 - e.t / e.life;
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.fillStyle = `rgba(130,220,255,${0.7 * k})`;
        ctx.beginPath();
        ctx.arc(e.x, e.y, 1.3 * k + 0.3, 0, TAU);
        ctx.fill();
        ctx.restore();
      }
    }
    // 飞行剑：拖尾（长半透明蓝带）→ 本体
    if (this.mirage) {
      for (const b of this.mirage.flying) {
        if (b.trail.length > 1) {
          ctx.save();
          ctx.globalCompositeOperation = 'lighter';
          ctx.lineCap = 'round';
          for (let i = 1; i < b.trail.length; i++) {
            const k = i / b.trail.length;
            ctx.strokeStyle = `rgba(70,150,255,${0.28 * k})`;
            ctx.lineWidth = 9 * k;
            ctx.beginPath();
            ctx.moveTo(b.trail[i - 1].x, b.trail[i - 1].y);
            ctx.lineTo(b.trail[i].x, b.trail[i].y);
            ctx.stroke();
          }
          ctx.restore();
        }
        drawMirageEdge(ctx, b.x, b.y, b.angle, 1, { seed: b.seed });
      }
    }

    // ---- 第一百四十六轮：格挡特效（斩击线 → 火花 → 涟漪）----
    for (const s of this.parryFx) {
      const k = 1 - s.t / s.life;
      drawParrySlash(ctx, s.x, s.y, s.seed, k);
    }
    for (const s of this.parrySparks) drawParrySpark(ctx, s);
    for (const r of this.parryRipples) {
      drawParryRipple(ctx, this.bx, this.by, r.t / r.life, 150);
    }

    // ---- 第一百四十八轮：P3 蓄力演出世界层（全部 lighter 拉满）----
    if (this.chargeT > 0) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      // 能量气场：脚下扩散的一圈圈缓慢脉动青色光环
      for (let i = 0; i < 3; i++) {
        const ph = (this.t * 0.55 + i / 3) % 1;
        const rr = 26 + ph * 130;
        const a2 = (1 - ph) * 0.4;
        ctx.strokeStyle = `rgba(70,220,255,${a2})`;
        ctx.lineWidth = 3 * (1 - ph) + 0.6;
        ctx.beginPath();
        ctx.ellipse(this.bx, this.by + 8, rr, rr * 0.32, 0, 0, TAU);
        ctx.stroke();
      }
      // 反向汇聚粒子（青/紫小光点，加速吸向椅子中心）
      for (const q of this.chargeParts) {
        const e = q.k * q.k;                        // easeIn：越近越快
        const x = q.sx + (this.bx - q.sx) * e;
        const y = q.sy + (this.by - q.sy) * e;
        const col = q.hue === 'c' ? '120,235,255' : '190,120,255';
        ctx.fillStyle = `rgba(${col},${0.5 + 0.5 * q.k})`;
        ctx.beginPath();
        ctx.arc(x, y, 1.4 + 1.8 * q.k, 0, TAU);
        ctx.fill();
        // 短拖尾（指向来路）
        ctx.strokeStyle = `rgba(${col},${0.35 * q.k + 0.1})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + (q.sx - this.bx) * 0.03, y + (q.sy - this.by) * 0.03);
        ctx.stroke();
      }
      ctx.globalCompositeOperation = 'source-over';
      // 空间裂缝：黑/暗紫折线，高频闪烁（像玻璃即将碎开）
      for (const c of this.chargeCracks) {
        const k = 1 - c.t / c.life;
        const a2 = (0.35 + 0.65 * Math.abs(Math.sin(c.t * 42))) * k;
        const rnd = mulberry32(c.seed);
        let px = c.x, py = c.y;
        let ang = rnd() * TAU;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = `rgba(30,4,44,${a2})`;
        ctx.lineWidth = 2.6;
        ctx.beginPath();
        ctx.moveTo(px, py);
        for (let s = 0; s < 5; s++) {
          ang += (rnd() - 0.5) * 1.5;
          px += Math.cos(ang) * (10 + rnd() * 18);
          py += Math.sin(ang) * (10 + rnd() * 18);
          ctx.lineTo(px, py);
        }
        ctx.stroke();
        ctx.strokeStyle = `rgba(0,0,0,${a2 * 0.9})`;
        ctx.lineWidth = 1.1;
        ctx.stroke();
      }
      ctx.restore();
    }

    // 刀锋火花（第一百四十九轮：次元斩每波的白色细长溅射）
    //（renderWorld 是纯渲染，无 dt —— 沿 drawShards 先例用固定 1/60）
    for (let i = this.bladeSparks.length - 1; i >= 0; i--) {
      const s = this.bladeSparks[i];
      s.t += 1 / 60;
      s.x += s.vx / 60;
      s.y += s.vy / 60;
      s.vx *= 0.9; s.vy *= 0.9;
      if (s.t >= s.life) this.bladeSparks.splice(i, 1);
      else drawBladeSpark(ctx, s);
    }

    // 玻璃碎片
    drawShards(ctx, this.shards, 1 / 60);
  }

  /** 屏幕层：暗幕、红边、白闪、入场黑幕（屏幕坐标系，W×H）。血条走官方哥特条。 */
  renderScreen(ctx, W, H) {
    // 入场演出黑幕：0-3s 淡到全黑（旧场景消失）；3-6.5s 黑幕同步淡出，
    // 让大排档场地/背对剪影从黑里浮现（淡入淡出同时进行）
    if (this.state === 'intro') {
      const t = this.introT;
      let black = 0;
      if (t < INTRO_BLACK_END) black = t / INTRO_BLACK_END;
      else if (t < INTRO_SCENE_END + 0.5) black = Math.max(0, 1 - (t - INTRO_BLACK_END) / 3.5);
      if (black > 0.005) {
        ctx.save();
        ctx.fillStyle = `rgba(2,2,8,${black})`;
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
      return;                            // 演出期间不画演出层其余内容
    }
    // 大招暗幕（屏幕系）
    if (this.veil > 0.01) drawUltVeil(ctx, W, H, this.veil);
    // ---- 第一百四十八轮：P3 蓄力屏幕层 ----
    if (this.chargeT > 0) {
      // 第 1 秒渐入的极暗紫色 Vignette（边缘向内挤压）
      const vig = Math.min(1, (5 - this.chargeT)) * 0.6;
      if (vig > 0.01) {
        const cx = W / 2, cy = H / 2;
        const r = Math.hypot(cx, cy);
        const grad = ctx.createRadialGradient(cx, cy, r * 0.34, cx, cy, r);
        grad.addColorStop(0, 'rgba(24,0,40,0)');
        grad.addColorStop(0.72, `rgba(24,0,40,${vig * 0.45})`);
        grad.addColorStop(1, `rgba(10,0,22,${vig})`);
        ctx.save();
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
      }
      // 第 5 秒：绝对黑屏 0.2s（BGM 淡出恰好归零，纯黑无任何内容）
      if (this.chargeBlack && this.chargeT <= 0.2) {
        ctx.save();
        ctx.fillStyle = 'rgba(0,0,0,1)';
        ctx.fillRect(0, 0, W, H);
        ctx.restore();
        return;                            // 黑屏期间什么都看不见
      }
    }
    // 受击红边
    drawEdgeFlash(ctx, W, H, this.edgeFlash);
    // 白闪
    if (this.whiteFlash > 0.01) {
      ctx.save();
      ctx.fillStyle = `rgba(255,255,255,${Math.min(1, this.whiteFlash) * 0.92})`;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }
  }
}
