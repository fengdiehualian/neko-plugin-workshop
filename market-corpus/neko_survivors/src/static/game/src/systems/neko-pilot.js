// ============================================================
// neko-pilot.js — 猫娘代打大脑（第一百六十五轮）
//
// 用户原话："那你的新版，猫娘可以自己玩游戏了吗？她能做到1-20回合
// 通关噩梦难度吗？如果不可以，尝试让她可以"
//
// 设计：NekoAgent 是"手脚"（getGameState/doAction/setPuppetMode），
// 这里是"大脑"——一个每帧决策的状态机，只用公开原语 + 少量只读状态：
//
//   playing  → 战斗走位：有远程武器就风筝（威胁向量逃离＋中心拉力防
//              出界＋拾取吸引）；全近战才贴脸输出
//   vergil   → 环绕维吉尔保持 240~360 距离（近了吃居合、远了打不着）；
//              就算 90 秒打不死，活着撑到结算也算过
//   level_up → 打分选卡（伤害>攻速>血量>暴击…，前 5 波血量加权）
//   shop     → 优先买远程武器（早期定风筝基调），再补 afford 道具，
//              然后 _beginWave(+1) 推进（战利品留给防洪闸折价）
//   result   → 20 波且活着 = 通关（_endRun(true)）；否则重试当前回合
//              （第一百七十四轮：模式保留 + 同波 5 次上限）
//
// 健壮性同 NekoAgent 契约：tick 永不抛错；大脑崩溃只影响代打，
// 绝不拖垮游戏主循环。
// ============================================================

// 第一百七十八轮：地形避让需要 blockedAt（实验室地砖/花园花坛）
import { blockedAt } from '../data/terrain.js';
import { ARENA as VIRGIL_ARENA } from './vergil.js';
import { ARENA as FLAN_ARENA } from './flandre.js';
import { ARENA as HALLOWEEN_ARENA } from './halloween.js';

/** 选卡打分表：属性 id → 权重（噩梦通关的稳健成长序）
 *  luna 基础 10 血 —— 血量/移速/护甲是风筝流的生命线，权重给足。
 *  第一百八十四轮（用户："1/s回血和10点护甲左右的发育优先级拉高点"）：
 *  hpRegen 5→8、armor 6→8 —— 生存成长序贴近输出。 */
const STAT_SCORE = {
  damage: 10, attackSpeed: 8, maxHp: 10, critChance: 6, moveSpeed: 7,
  armor: 8, critDamage: 4, hpRegen: 8, lifesteal: 4, luck: 4,
  harvesting: 3, pickupRange: 2, dodge: 5,
};

// ---- 第一百六十六轮：接管/交还的切换语音（用户点名要的"三五句"）----
// 通过 game._nekoSay 播报（气泡 + TTS，插件桥模式下走官方语音通道）
const TAKEOVER_LINES = [
  '要人家帮你玩了吗～', '把键盘交给我吧喵！', '看好了，人家的走位～',
  '放心交给我，喵！', '嘿嘿，轮到我上场啦～',
];
const HANDOVER_LINES = [
  '诶，要你亲自出马了吗？', '好吧好吧，键盘还你喵～', '那你要加油哦！',
  '人家打得不好吗…喵', '主人的手痒了吧～',
];
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)] ?? arr[0];

// =====================================================================
// 第一百九十七轮：AI 三档智商（用户游玩一整局后的反馈第 6 条）
//   "目前的ai太聪明了……几乎不会死掉，太强了。这是好事，说明你的能力，但是游戏就是要突发够多才好玩，
//    你能做3个智商的ai吗？最高的就是现在的你的作品，几乎不会死，横扫一切。然后做个普通的，比现在最强的容易吃弹幕一些，
//    再做个菜的，吃弹幕概率再高点并且可能出现走位失误之类的。然后3个智商随机抽取进入游戏。
//    不过不改变拆箱子和救人逻辑，让最菜的ai也会努力在队友全倒下只剩下自己的时候也去努力救人。"
//
// 只作用于【躲避与走位】，下面这些完全不受档位影响：救援（含近身踹醒）、拆宝箱、升级选卡、商店、结算、光环/护甲。
// 档位通过 5 种"人类式"的不完美实现，而不是直接加伤害或减血量（那样玩家看得出来是作弊式削弱）：
//   period      决策间隔（帧）—— 反应延迟。救援期间一律回到 4，保证最菜的也积极救人
//   scan/skip   感知：只看得见近处的弹；每颗该躲的弹有概率被"漏看"
//   angErr      躲避方向误差（弧度）—— 知道要躲，但闪的方向不够准
//   taus/dirs   预测式找缝（_safeStep）看多远、试几个方向 —— 高手能算 0.52s 后的缝，菜鸟只看眼前 0.22s 的四邻
//   safeSkip    _safeStep 整个被"走神"跳过的概率
//   stumble     偶发失误：每隔约 stumbleEvery 秒朝随机方向踉跄 stumbleDur 秒
// pro = 第一百八十五轮以前的行为，逐项为"中性值"，保证与旧版一致。
// =====================================================================
export const AI_SKILLS = {
  pro:    { id: 'pro',    label: '高手', period: 4, scan: 1.0,  skip: 0,    angErr: 0,   taus: [0.10, 0.22, 0.36, 0.52], dirs: 16, safeSkip: 0,    scoreNoise: 0,  stumbleEvery: 0,  stumbleDur: 0 },
  normal: { id: 'normal', label: '普通', period: 5, scan: 0.82, skip: 0.26, angErr: 0.45, taus: [0.10, 0.22, 0.36],       dirs: 12, safeSkip: 0.26, scoreNoise: 8,  stumbleEvery: 15, stumbleDur: 0.32 },
  rookie: { id: 'rookie', label: '菜鸟', period: 8, scan: 0.65, skip: 0.45, angErr: 0.9, taus: [0.10, 0.22],              dirs: 8,  safeSkip: 0.45, scoreNoise: 16, stumbleEvery: 6,  stumbleDur: 0.45 },
};
export const AI_SKILL_IDS = Object.keys(AI_SKILLS);

/** 三档等概率抽签。globalThis.__aiSkill = 'pro'|'normal'|'rookie' 可强制（测试/探针/想固定难度时用） */
export function rollSkill(rnd = Math.random) {
  const forced = globalThis.__aiSkill;
  if (AI_SKILLS[forced]) return forced;
  const u = rnd();
  return u < 1 / 3 ? 'pro' : u < 2 / 3 ? 'normal' : 'rookie';
}

export class NekoPilot {
  constructor() {
    this.on = false;
    this.skill = 'pro';       // 197 轮：AI 档位（entity 模式在 start 时抽签；P1 代打保持 pro）
    this._rnd = Math.random;  // 可注入（测试要确定性）
    this._fr = 0;             // 帧计数（失误计时用）
    this.win = false;
    this.gaveUp = false;
    this.attempt = 0;
    this.maxAttempts = 3;
    this.characterId = 'yui';
    this.difficulty = 'nightmare';
    this.startedAt = 0;
    this._aimT = 0;          // 走位节流（帧计）
    this._events = [];       // 战报
  }

  /** 当前生效的档位参数（globalThis.__aiSkill 强制优先） */
  _skillCfg() {
    const forced = globalThis.__aiSkill;
    return AI_SKILLS[AI_SKILLS[forced] ? forced : this.skill] ?? AI_SKILLS.pro;
  }

  /** 战报（探针/宿主读取） */
  get report() {
    return {
      on: this.on, win: this.win, gaveUp: this.gaveUp,
      attempt: this.attempt, maxAttempts: this.maxAttempts,
      difficulty: this.difficulty,
      events: this._events.slice(-200),
    };
  }

  _log(text) {
    this._events.push({ t: Date.now(), text });
    try { console.log(`[neko-pilot] ${text}`); } catch { /* */ }
  }

  /**
   * 开打：设难度 → 开局 → 键盘接管（防人类干扰）。
   * @param {object} g  游戏实例（window.__game）
   * @param {object} [opts] { difficulty, characterId, maxAttempts }
   */
  start(g, opts = {}) {
    // ---- 第一百七十轮：entity 模式（pilot2 驱动 P2 队友）----
    // 不重开局、不抢键盘、不挂光环（双 0buff 实验）；P2 已由 main 的
    // startRun(duo) 创建好，这里只是"接管她的大脑"。
    if (opts.entity) {
      this.entity = opts.entity;
      this.on = true;
      this.win = false;
      this.gaveUp = false;
      this._lastPush = -999;
      this._events = [];
      this._hpSnapshot = null;
      this._tgt = null; this._lastX = null; this._lastY = null; this._stuckN = 0;
      // 197 轮：每个 AI 队友开局随机抽一档智商（重试/新局重新抽）。opts.skill 可指定
      this.skill = AI_SKILLS[opts.skill] ? opts.skill : rollSkill(this._rnd);
      this._stumble = null;
      this._log(`P2 猫娘上线：${opts.entity.characterId}（双 0buff 实验）· 智商：${AI_SKILLS[this.skill].label}`);
      return true;
    }
    // 难度默认跟当前选角界面选的难度走（用户从选角界面点"猫娘帮你玩"
    // 时期望打的就是选中的难度，不是写死的噩梦）
    this.entity = null;
    this.difficulty = opts.difficulty ?? g?.difficultyId ?? 'nightmare';
    this.characterId = opts.characterId ?? 'luna';
    this.maxAttempts = opts.maxAttempts ?? 3;
    this.on = true;
    this.win = false;
    this.gaveUp = false;
    this.attempt = 1;
    // ---- 第一百七十四轮：同回合重试计数（团灭重试 5 次上限）
    this._retryWave = null;
    this._retryN = 0;
    this._lastPush = -999;
    this._events = [];
    this.startedAt = Date.now();
    this._hpSnapshot = null;   // 新局满血，无快照
    this._tgt = null; this._lastX = null; this._lastY = null; this._stuckN = 0;
    try {
      if (this.difficulty && typeof g.setDifficulty === 'function' && g.difficultyId !== this.difficulty) {
        g.setDifficulty(this.difficulty);
      }
      g.ui?.clearOverlay?.();
      g.startRun(this.characterId);
      globalThis.NekoAgent?.setPuppetMode?.(true);
      this._log(`代打开始：${this.characterId} · ${this.difficulty} · 最多 ${this.maxAttempts} 次尝试`);
      g._nekoSay?.(pick(TAKEOVER_LINES));
    } catch (e) {
      this._log(`开局失败：${e?.message ?? e}`);
      this.on = false;
    }
    return this.on;
  }

  /**
   * 中途接管（暂停菜单切换开）：不重开局，就地接手当前局面。
   */
  adopt(g) {
    this.on = true;
    this.win = false;
    this.gaveUp = false;
    this.attempt = Math.max(1, this.attempt);
    this._lastPush = -999;
    this._events = this._events || [];
    this.startedAt = this.startedAt || Date.now();
    // 第一百六十八轮：交出瞬间快照血量**比例** —— 代打期间光环回复拉满
    // 也不能带走：切回来时按比例还原，堵死"切猫娘回满血再切回来 = 不死"
    // 的漏洞。光环回血只服务于她自己活着。
    try { if (g?.player) this._hpSnapshot = g.player.hp / Math.max(1, g.player.maxHp); } catch { /* */ }
    this._tgt = null; this._lastX = null; this._lastY = null; this._stuckN = 0;
    try {
      globalThis.NekoAgent?.setPuppetMode?.(true);
      this._log(`代打中途接管（第 ${(g?.waveIndex ?? 0) + 1} 波）`);
      g?._nekoSay?.(pick(TAKEOVER_LINES));
    } catch { /* */ }
    return true;
  }

  /** 停止代打，控制权立刻还给玩家 */
  stop(g) {
    this.on = false;
    try { globalThis.NekoAgent?.setPuppetMode?.(false); } catch { /* */ }
    try {
      if (g?.player) {
        // 还原交出时的血量比例（第一百六十八轮防漏洞，见 adopt）
        if (this._hpSnapshot != null) {
          g.player.hp = Math.max(1, Math.round(this._hpSnapshot * g.player.maxHp));
          this._hpSnapshot = null;
        }
        g.player.pilotBuff = false;
        // 轻量/第三档光环一并清（第一百七十/一七一轮）
        g.player.pilotLite = false;
        g.player.pilotTough = false;
        g.player._pilotMagnet = 0;
        g.player._pilotIframe = 0;
        g.player._pilotEarly = false;
        g.player.agentMoveTarget = null;   // 清掉她的移动意图，别拽着玩家走
      }
    } catch { /* */ }
    this._tgt = null; this._lastX = null; this._lastY = null; this._stuckN = 0;
    try { g?._nekoSay?.(pick(HANDOVER_LINES)); } catch { /* */ }
    this._log('代打结束，控制权交还玩家');
  }

  /** 每帧决策入口（NekoAgent._tick 里调用，永远不抛） */
  tick(g) {
    if (!this.on || !g) return;
    try {
      // ---- 第一百七十轮：实体参数化（2P）----
      // entity 模式（pilot2）：this.entity 是队友 P2 —— 不重开局、
      // 不抢键盘、不挂任何光环（双 0buff 实验）、不处理结算
      //（双死判负由 main），P2 倒地时原地待救援。
      const p = this.entity ?? g.player;
      if (!p) return;
      if (this.entity) {
        // 第一百八十四轮：倒地不再干等 —— 15% 龟速爬向活着的队友
        //（用户："猫娘ai倒地状态ai优先往活着的那个人那移动"）
        if (p.dead) return;
        if (p.downed) {
          const mate = this._mateOf(g, p, false);
          if (mate) {
            p.agentMoveTarget = { x: mate.x, y: mate.y };
          }
          return;   // 爬行由 player.update 的倒地段执行，其余决策照旧跳过
        }
        if (g.state === 'result') return; // 结算交给 P1 的 pilot
        if (p.pendingLevelUps > 0) {
          // P2~P4 升级自动化（不走选卡 UI）—— 第一百八十四轮改成"缺口
          // 优先"发育包（用户："至少让她们能把回血努力做到每秒1点起步"
          // "10点护甲左右的发育优先级拉高点"）：
          //   ① hpRegen < 1 → 每级 +0.5（两级补到 1/s 起步线）
          //   ② armor < 10 → 每级 +2（五级补到 10 点护甲）
          //   ③ 缺口补齐后 → +6 生命上限 / +8% 伤害（旧包，稳步成长）
          p.pendingLevelUps--;
          const regen = p.stats.get('hpRegen') ?? 0;
          const armor = p.stats.get('armor') ?? 0;
          if (regen < 1) {
            p.stats.addFlat('hpRegen', 0.5);
            this._log(`升级：+0.5 生命回复（现 ${Math.min(1, regen + 0.5).toFixed(1)}/s，缺口补齐线 1）`);
          } else if (armor < 10) {
            p.stats.addFlat('armor', 2);
            this._log(`升级：+2 护甲（现 ${Math.min(10, armor + 2)}，缺口补齐线 10）`);
          } else {
            p.stats.addFlat('maxHp', 6);
            p.stats.addPct('damage', 8);
            p.heal(6);
            this._log('升级：+6 生命上限 / +8% 伤害（自动）');
          }
          p.stats.dirty = true;
        }
      }

      // ---- 卡墙检测 ----
      // 有移动目标但 ~0.3s 位移 <0.6px = 撞墙了 —— 侧移 90° 绕行。
      // （agentMoveTarget 是直线走位，遇地形障碍会顶死在墙上）
      if (p && this._lastX != null) {
        const moved = Math.hypot(p.x - this._lastX, p.y - this._lastY);
        if (moved < 0.6 && p.agentMoveTarget) {
          if (++this._stuckN > 18) {
            const t = p.agentMoveTarget;
            const dx = t.x - p.x, dy = t.y - p.y;
            const d = Math.hypot(dx, dy) || 1;
            // 第一百七十一轮修复：直设实体自己的 agentMoveTarget ——
            // 走全局 doAction 会把指令打到 g.player（P1）头上，
            // 这就是 v0.3.48"AI 控制了 1p、2p 挂机"的根因
            p.agentMoveTarget = { x: p.x - (dy / d) * 130, y: p.y + (dx / d) * 130 };
            this._stuckN = 0;
            this._lastX = p.x; this._lastY = p.y;
            return;
          }
        } else this._stuckN = 0;
        this._lastX = p.x; this._lastY = p.y;
      } else if (p) { this._lastX = p.x; this._lastY = p.y; }

      // ---- 光环调度（第一百七十一轮重构）----
      // · pilotBuff  全套（锁1+回复）—— 单机默认
      // · pilotLite  磁吸400 + 前3波减伤40% —— 探针 __altbuff
      // · pilotTough 全图磁吸 + 无敌帧2s + 前3波减伤50% —— 探针 __buff3
      // · duo 且无 __buff3：双 0buff（2P 实验基准）；duo 且 __buff3：
      //   两只都挂 pilotTough（用户："单ai不行就双ai一起玩"）
      {
        const duo = globalThis.__duo || g.__duo;
        const buff3 = window.__buff3 || g.__buff3;
        const nobuff = window.__nobuff || g.__nobuff;
        const crew = this.entity ? [this.entity] : (g.players?.() ?? [g.player]);
        for (const ent of crew) {
          if (!ent || ent.dead) continue;
          // ---- 第一百八十四轮：AI 猫娘隐藏护甲（用户："ai猫娘默认+10护甲，
          // 每回合自动+2……30点护甲有50%减伤约等于将近2倍hp了。目前猫娘ai
          // 经常被1-2下就打死了。只加护甲不容易被玩家肉眼分辨出来"）——
          // 标记挂在**实体**上（_armorWave），同一实体无论被几个 pilot 的
          // crew 覆盖都只涨一次；retryWave 重建实体后从 +10 重新起步（波次
          // 也倒退了，成长同步重置，公平）。真人玩家（未被任何 pilot 驱动）
          // 不在 crew 里，拿不到 —— 只喂 AI 猫娘。
          if (ent._armorWave !== (g.waveIndex ?? 0)) {
            const armorBonus = ent._armorWave === undefined ? 10 : 2;
            ent._armorWave = g.waveIndex ?? 0;
            if (ent.stats?.addFlat) {
              ent.stats.addFlat('armor', armorBonus);
              ent.stats.dirty = true;
            }
          }
          if (nobuff || (duo && !buff3)) {
            ent.pilotBuff = false; ent.pilotLite = false; ent.pilotTough = false;
            ent._pilotMagnet = 0; ent._pilotIframe = 0; ent._pilotEarly = false;
            // （184 轮：0buff 实验基准保持原样 —— 用户要求先别加光环 buff，
            // 看升级缺口包修复后的自然表现；回血走升级/道具/商店缺口补齐。）
          } else if (buff3) {
            ent.pilotBuff = false; ent.pilotLite = false; ent.pilotTough = true;
            ent._pilotEarly = (g.waveIndex ?? 0) < 3;
            ent._pilotMagnet = 99999;   // 全图吸附
            ent._pilotIframe = 2.0;
          } else if (!this.entity) {
            ent.pilotBuff = true; ent.pilotLite = false; ent.pilotTough = false;
            if (ent.hp > 0) {
              // ≈12/s —— 刻意压到"围殴+维吉尔真伤连击"能击穿的水位：
              // 用户要"输了也挺有趣，但不能真的一局都赢不了"，留死亡窗口
              ent.hp = Math.min(ent.maxHp, ent.hp + 0.4);
            }
          } else {
            ent.pilotBuff = false; ent.pilotLite = false; ent.pilotTough = false;
          }
        }
      }
      switch (g.state) {
        case 'playing': this._combat(g, false, p); break;
        case 'vergil': this._combat(g, true, p); break;
        case 'flandre': this._combat(g, true, p); break;   // 芙兰战同款环绕（20 波后挑战）
      case 'halloween': this._combat(g, true, p); break; // 第一百八十四轮：万圣节双猫纯演出（躲弹幕游走即可）
      case 'remilia': this._combat(g, true, p); break;   // 第二百零三轮：蕾米终演出同上（双人纯演出，躲弹幕）
        case 'level_up': this._pickUpgrade(g, p); break;
        case 'shop': this._nextWave(g, p); break;
        case 'result': if (!this.entity) this._settle(g); break;
        default: break; // title/char_select/paused 等不动作
      }
    } catch { /* 决策异常不许影响游戏 */ }
  }

  // ---------------------------------------------------------
  // 战斗走位
  // ---------------------------------------------------------
  /** 第一百八十四轮（1~4P）：队友选择器 —— 除自己外最近的活人 /
   * 最近的倒地队友。旧 `world.player2` 直读只认 2P，3/4P 会漏人。 */
  _mateOf(g, p, wantDowned) {
    let best = null, bestD = Infinity;
    for (const pl of (g.world?.players?.() ?? [])) {
      if (!pl || pl === p || pl.dead) continue;
      if (wantDowned ? !pl.downed : pl.downed) continue;
      const d = Math.hypot(pl.x - p.x, pl.y - p.y);
      if (d < bestD) { bestD = d; best = pl; }
    }
    return best;
  }

  _combat(g, vergilMode, ent) {
    const p = ent ?? g.player;
    if (!p || p.dead) return;
    // ---- 第一百八十四轮：倒地 = 龟速爬向活着的队友 ----
    // player.update 倒地段会以 15% 移速执行 agentMoveTarget（不能攻击）；
    // AI 在这里只负责把目标设成活人，让倒地者主动凑向救援。
    if (p.downed) {
      const mate = this._mateOf(g, p, false);
      if (mate) {
        p.agentMoveTarget = { x: mate.x, y: mate.y };
      }
      return;
    }
    // 节流：每 4 帧重算一次目标（~0.13s）——太慢会被噩梦怪海围死
    // 197 轮：档位 = 反应延迟（pro 4 帧 / normal 5 / rookie 8）；但队友倒地时一律回到 4 帧 ——
    // 用户："不改变拆箱子和救人逻辑，让最菜的ai也会努力……去努力救人"，救援决策不能被降速。
    const S = this._skillCfg();
    this._fr++;
    const rescueMode = (g.world?.players?.() ?? []).some((pl) => pl && pl !== p && pl.downed && !pl.dead);
    const per = rescueMode ? 4 : S.period;
    if (++this._aimT % per !== 1 % per) return;
    this._dodgeNow = false;   // 本次决策是否处于"躲弹中"（置位后跳过走位低通，指令直达）

    const world = g.world;
    const half = world?.halfSize ?? 700;
    const lowHp = p.maxHp > 0 && p.hp / p.maxHp < 0.6;   // 6 成血就开始怂
    const threatR = lowHp ? 430 : 310;      // 残血更怂：警戒圈加大
    const fleeMag = lowHp ? 300 : 210;

    const enemies = world?.enemies?.items;
    let fleeX = 0, fleeY = 0, nearest = null, nearestD = Infinity;
    let bossE = null, bossD = Infinity;   // 普通波的 boss（交战拉力用）
    // ---- 第一百八十四轮（用户："修复之后猫娘ai好像也不打他了"，实测
    // boss 血一滴不掉 + AI 卡在 250~570）—— boss 战的 flee 排斥必须
    // **跳过 boss 本体**：维吉尔实体也在 enemies 列表里，贴身 95 时
    // "位置排斥 + 最近敌人 1.1 权重" 合成出 ~200px 的逃逸向量，把
    // 环绕目标点顶到斥力平衡点（250+）—— 越想贴身被推得越远，AI
    // 永远悬在武器射程外。boss 战的威胁是**弹幕**，不是他的站桩身体。
    const bossBody = world?.vergilFight?.boss ?? world?.flandreFight?.boss ?? null;
    if (Array.isArray(enemies)) {
      for (const e of enemies) {
        if (!e || e.dead || e.isChest) continue;
        if (e === bossBody) continue;
        const dx = p.x - e.x, dy = p.y - e.y;
        const d = Math.hypot(dx, dy) || 1;
        if (d < nearestD) { nearestD = d; nearest = e; }
        // 第一百八十四轮：标记波次 boss（"几乎不打 boss"修复的拉力目标）
        if ((e.def?.boss || e.isBoss) && d < bossD) { bossD = d; bossE = e; }
        // 弹幕怪的警戒圈更大（子弹飞得远）
        const R = e.def?.behavior === 'bullet' ? threatR + 120 : threatR;
        if (d < R) {
          const w = 1 - d / R;
          fleeX += (dx / d) * w;
          fleeY += (dy / d) * w;
        }
      }
      // 包围逃逸：加权和会被多方向抵消（=站桩等死被围死，实测 4-6 波
      // 全这么死的）—— 最近怪的完整权重单独主导，保证永远在"离开
      // 最危险方向"的路上
      if (nearest && nearestD < threatR) {
        const ux = (p.x - nearest.x) / (nearestD || 1);
        const uy = (p.y - nearest.y) / (nearestD || 1);
        fleeX += ux * 1.1;
        fleeY += uy * 1.1;
      }
    }

    // ---- 第一百八十四轮：弹幕躲避（敌弹也是威胁）----
    // 用户："猫娘ai对弹幕好像不怎么敏感？有时候直直的走上去吃弹幕。"
    // 之前威胁只算怪本身的位置排斥 —— 弹在飞她根本"看不见"。现在对每
    // 颗"真的会打到我"的敌弹做弹道判定：瞄准线垂距 <46px 才紧张，然后
    // 沿弹速的垂直方向闪（两侧选离弹更远的一边）；越近权重越大。
    try {
      const ebs = world?.enemyBullets?.items;
      if (Array.isArray(ebs)) {
        for (const b of ebs) {
          if (!b || b.dead || b.hostileToEnemies) continue;
          const dx = p.x - b.x, dy = p.y - b.y;
          const d = Math.hypot(dx, dy);
          if (d > 230 * S.scan || d < 1) continue;   // 197 轮：感知范围随档位（菜鸟只看得见 150px 内的弹）
          // ---- 万圣节战实测纠偏：EnemyBullet 是 angle+speed 运动，从不写 vx/vy
          // （Entity 基类的 vx=vy=0 一直是 0）→ 旧写法 bv 恒为 0，**每一发敌弹都被当成
          // "静止的"跳过**，184 轮起整套弹幕躲避对全游戏敌弹都是空转。
          // 现在优先用 vx/vy（有的实体会写），否则由 angle/speed 还原速度向量。
          const spd0 = b.speed ?? 0;
          const bvx = b.vx || Math.cos(b.angle ?? 0) * spd0;
          const bvy = b.vy || Math.sin(b.angle ?? 0) * spd0;
          const bv = Math.hypot(bvx, bvy);
          if (bv < 40) continue;   // 慢速/静止的（燃烧带之类走专门逻辑）
          // 接近性：弹速方向与"弹→我"方向的夹角余弦 > 0.55 才算朝我来
          const dot = ((bvx / bv) * dx + (bvy / bv) * dy) / d;
          if (dot < 0.55) continue;
          // 瞄准线垂距：打不着的弹不躲（省下走位专心输出）
          const along = d * dot;
          const miss = Math.sqrt(Math.max(0, d * d - along * along));
          if (miss > 46) continue;
          // 197 轮：漏看——该躲的弹有概率根本没注意到（pro 为 0，行为不变）
          if (S.skip && this._rnd() < S.skip) continue;
          // 垂直闪避：两侧选离弹更远的一边，越近权重越大
          let px = -bvy / bv, py = bvx / bv;
          if (S.angErr) {   // 197 轮：躲避方向误差——知道要躲，但闪得不够准
            const er = (this._rnd() - 0.5) * 2 * S.angErr, ce = Math.cos(er), se = Math.sin(er);
            const rx = px * ce - py * se, ry = px * se + py * ce; px = rx; py = ry;
          }
          const side = (dx * px + dy * py) >= 0 ? 1 : -1;
          const w = (1 - d / 230) * 2.4;
          fleeX += px * side * w;
          fleeY += py * side * w;
        }
      }
    } catch { /* 弹幕列表异常不影响走位 */ }

    // ---- 第一百七十一轮：救援优先 ----
    // duo 下队友倒地：第一时间跑去队友身边 —— 武器弹穿过倒地队友即救援。
    // ---- 第一百八十四轮（4P 二次实测再修）：救援是倒地后的全场最高优先级。
    // 上一版门槛 70 仍太怂（用户实测"队友躺在必经之路上也不愿意多动半个
    // 身位"），且旧目标点带 +30 偏移 —— AI 走到旁边就站住，救援全靠"武器
    // 弹恰好穿过队友"的运气，永远救不起来。现在三刀：
    //   ① 门槛砍到 40（用户点破"换方向并不会掉速度"——绕路不影响自动
    //     开火的 DPS，只有真被贴脸围死才放弃）；
    //   ② 目标点直接压到倒地队友本体，不走偏移站位；
    //   ③ 贴身 60px 内**近身踹醒**（黑夜君临式处决）—— rescueHit 自带
    //     0.2s 全局 CD，5 刀 1 秒救起 1 倒，彻底不赌弹道。
    const downedMates = (g.world?.players?.() ?? []).filter((pl) => pl && pl !== p && pl.downed && !pl.dead);
    // ---- 第一百八十四轮（用户抓出致命 bug）：救援分支此前判 `g.__duo`
    // —— 但 game 实例上**从来没有**这个字段（只有 window.__duo），条件
    // 永远为 false，救援逻辑从上线起一行都没执行过！之前"偶尔救起"全是
    // 路过武器弹恰好穿过倒地者的运气（用户："只有刚好踩着队友尸体过去
    // 才救"）。现在与光环段同口径 globalThis.__duo || g.__duo（globalThis
    // 在浏览器即 window，node 测试桩也安全）。
    // 用户场景（绝对安全拆 2 个箱子路过倒地者不救）正是这个短路 + 拆箱
    // 分支抢走走位的结果：拆箱门槛 420 无比宽松，救援又从不触发。
    const duoOn = globalThis.__duo || g.__duo;
    // ---- 第一百八十四轮（用户实测："活着的猫娘是真的扛着怪海的伤害过来
    // 救人，反而让她自己死了……救人优先度已经高到过高了"）—— 救人开关
    // 修好后优先级反超了自保，冲进怪海中心 = 双倒。现在加**安全保活**三道
    // 闸（能救则救，送死不救）：
    //   ① 自保门槛回归：自己身边有怪贴脸威胁时先脱身（普通 150 / 残血 260
    //      —— 救援的"换方向不掉速"依然成立，但穿怪堆会掉血掉命）；
    //   ② 血线硬门槛：自己 hp <45% 不送 —— 救援者再倒就是团灭加速；
    //   ③ 怪海危险度：倒地者身边 150 内 ≥5 只 = 怪海中心，绕开放弃
    //    （多人倒地的团灭倒计时放宽到 ≥7 才放弃 —— 那时赌命值得）。
    const hpRatio = p.maxHp > 0 ? p.hp / p.maxHp : 1;
    const hpOk = hpRatio > 0.45;
    const selfGate = lowHp ? 260 : 150;
    let swarmBest = null, swarmN = 0;
    if (duoOn && downedMates.length > 0) {
      // ---- 第一百八十四轮（用户："1倒的队友非常好救，1倒优先度高一些，
      // 2倒较高，3倒最低。因为3倒要30下，抗血救等于死"）—— 目标选择从
      // "最近"改成"倒地次数×距离"加权评分：downCount 每多 1，等效距离
      // +260px（1倒 5 刀 / 2倒 10 刀 / 3倒 30 刀，刀数差就是时间差）。
      // 3 倒者只有真空位（没有更省的候选）时才轮到。
      let best = null, bestScore = Infinity;
      for (const m of downedMates) {
        const d = Math.hypot(m.x - p.x, m.y - p.y);
        const score = d + (m.downCount ?? 0) * 260;
        if (score < bestScore) { bestScore = score; best = m; }
      }
      if (best) {
        const rawD = bestScore - (best.downCount ?? 0) * 260;
        for (const e of (Array.isArray(enemies) ? enemies : [])) {
          if (!e || e.dead || e.isChest) continue;
          if (Math.hypot(e.x - best.x, e.y - best.y) < 150) swarmN++;
        }
        const multiDown = downedMates.length >= 2;
        const swarmed = swarmN >= (multiDown ? 7 : 5);
        // ---- 第一百八十四轮（用户："队友是在自己250px范围内倒地而且是一倒，
        // 优先度拉高救援。一倒的救援从来都是顺手的事，现在加了自保之后又
        // 回到了队友倒脸上都跑路"）—— **脸上 1 倒**（250 内 + downCount≤1
        // = 5 刀的事）**无视三道闸直接救**：贴脸倒地时风险本来就低，5 刀
        // 1 秒，比绕路风筝更安全。其余场景维持三道闸。
        const faceDown = rawD < 250 && (best.downCount ?? 0) <= 1;
        if (faceDown || (hpOk && !swarmed && nearestD > selfGate)) {
          if (rawD < 60) {
            // 已贴身：站桩踹（清移动目标防止漂移），rescueHit 的 0.2s gate
            // 在 player.update 里按 dt 递减，这里每帧调只会按节拍生效
            p.agentMoveTarget = null;
            best.rescueHit();
          } else {
            p.agentMoveTarget = { x: best.x, y: best.y };
          }
          return;
        }
        // 三道闸任一没过 → 记下最近倒地者供弹道顺手救援（武器开火照旧），
        // 走位交给正常风筝/拆箱 —— 保活优先于救援
        swarmBest = best;
      }
    }

    // 只要有"非近战"武器就风筝（ranged/element/turret 都算）；
    // 全近战则保持攻击半径环绕输出
    let hasRanged = false;
    try {
      hasRanged = (p.weapons || []).some((w) => {
        const k = w?.def?.kind;
        return k === 'ranged' || k === 'element' || k === 'turret';
      });
    } catch { /* */ }

    // ---- 第一百八十四轮：拆箱目标（用户："近战流派的猫娘不会靠近宝箱去
    // 拆，挂机在攻击范围外，自动攻击也打不了箱子"）—— 宝箱固定不动，
    // 而走位决策只看会动的怪：近战 AI 永远在猫爪半径外干瞪眼，武器够
    // 不着箱子。现在：附近有**静止宝箱**且自己没被贴脸围死（>90）时，
    // 主动走到攻击距离内（近战贴 58 = 猫爪攻击边缘；远程 150）站桩拆箱。
    // 宝箱怪是会跑的 flee 怪，索敌自然会打，不归这里管。boss 战跳过。
    if (!vergilMode && nearestD > 90) {
      let chest = null, chestD = Infinity;
      if (Array.isArray(enemies)) {
        for (const e of enemies) {
          if (!e || e.dead || !e.isChest || e.def) continue;   // 只管静止宝箱
          const d = Math.hypot(e.x - p.x, e.y - p.y);
          if (d < chestD) { chestD = d; chest = e; }
        }
      }
      if (chest && chestD < 420) {
        const stand = hasRanged ? 150 : 58;
        if (chestD > stand + 8) {
          // 直奔箱子（救援已在上面的分支 return；这里优先于风筝/巡逻，
          // 路上武器照常开火，扫到谁打谁）
          p.agentMoveTarget = { x: chest.x, y: chest.y };
          return;
        }
        // 已进攻击距离：清移动目标站桩 —— 走近后宝箱就是"最近的敌人"，
        // 武器自动开火自然转火拆箱
        p.agentMoveTarget = null;
        return;
      }
    }

    let tx, ty;
    if (vergilMode) {
      // ---- 第一百八十三轮：boss 战走位全面修正 ----
      // 用户实测三连："维吉尔战猫娘会出框""芙兰战猫娘不在场地里，整场
      // boss 只有我""21 回合猫娘 ai 直接挂机不动了"。
      // 根因：这里只认 world.vergilFight.boss —— 芙兰战时是 null → 目标
      // 站桩；且环绕目标点从不 clamp 进 boss 场地 → 一路挤出空气墙。
      // 修：① boss 兼容芙兰战；② 全部目标点 clamp 进对应 ARENA；
      //    ③ 环绕距离收紧（攻击欲望——用户："对 boss 攻击欲望很低"）。
      const vf = world?.vergilFight;
      const ff = world?.flandreFight;
      const hf = world?.halloweenFight;
      // 第二百零三轮：蕾米莉亚终演出也是"宽屏纯演出"，与万圣节双猫共用同一块场地（REMILIA_ARENA 与它同尺寸）
      const rf = world?.remiliaFight;
      const perf = hf ?? rf;
      // 199 轮：P1 代打在芙兰战里会用小小猫娘消弹（见 _maybePurge）。队友 AI（entity）不按 —— 消弹是 P1 的技能，冷却共享。
      if (ff && !this.entity) this._maybePurge(world, p, ff);
      const b = vf?.boss ?? ff?.boss;
      // 第一百八十四轮：万圣节双猫战用宽屏场地（用户："移动场地可以大点"）
      const arena = perf ? HALLOWEEN_ARENA : ff ? FLAN_ARENA : VIRGIL_ARENA;
      if (b && !b.dead) {
        const dx = p.x - b.x, dy = p.y - b.y;
        const d = Math.hypot(dx, dy) || 1;
        // ---- 第一百八十四轮（用户："猫娘ai打维吉尔会一直卡远程射击位，
        // 而维吉尔可以格挡远程伤害"+"修复之后猫娘ai好像也不打他了"）——
        // guarding 只拦**远程**（melee 拼刀照常结算，v0.3.80 已修）。
        // 站位必须匹配**武器实际射程**（melee 射程仅 54~78）：
        //   · 有近战 → 全程贴身：guarding 95~150 拼刀、非 guarding 压到
        //     70~120（v0.3.81 曾让非 guarding 也外圈环绕 170~330，近战
        //     武器永远够不着 → AI 输出归零 = "不打维吉尔"，实测 boss 血
        //     一滴不掉，本版修正）。
        //   · 纯远程 → guarding 期退 260~330 保存血量（打不进别白挨砍）；
        //     非 guarding 压到 200~280（ranged 射程 300+，留弹道余量）。
        const guarding = vf?.guarding === true && !ff && !hf;
        const hasMelee = (p.weapons || []).some((w) => w?.def?.kind === 'melee');
        const inner = guarding ? (hasMelee ? 95 : 260) : (hasMelee ? 70 : 200);
        const outer = guarding ? (hasMelee ? 150 : 330) : (hasMelee ? 120 : 280);
        if (d < inner) { tx = p.x + (dx / d) * (inner - 15); ty = p.y + (dy / d) * (inner - 15); }
        else if (d > outer) { tx = b.x + (dx / d) * (outer - 25); ty = b.y + (dy / d) * (outer - 25); }
        else { // 环绕切向移动，不吃直线弹幕
          const tang = hasMelee ? (guarding ? 90 : 60) : (guarding ? 120 : 110);
          tx = p.x + (-dy / d) * tang; ty = p.y + (dx / d) * tang;
        }
        // ---- 第一百八十四轮（用户："4ai猫娘都不躲芙兰弹幕"）----
        // 环绕目标点此前**完全不用** flee 向量 —— 普通波的风筝靠它，
        // boss 战却只有"切向环绕"一种姿态，全屏符卡弹（芙兰的扩散/雨弹）
        // 根本绕不开。现在把敌人排斥+弹幕闪避的合成向量按 0.8 权重叠进
        // 环绕目标（低血 1.1 更怂），再统一 clamp 场内 —— 环绕照跑、
        // 弹幕照躲，两者不再互斥。
        // ---- 万圣节战实测纠偏：fleeX/fleeY 是**权重**（量级≈1），不是像素。旧写法
        // `fleeX * dodgeW * (fleeMag/210)` 只把目标点挪了约 1px，闪避等于没有。
        // 现在按方向 × min(|flee|,1.6) × fleeMag（≈210px）× dodgeW 折算成像素。
        const dodgeW = lowHp ? 1.1 : 0.8;
        const fm = Math.hypot(fleeX, fleeY);
        if (fm > 0.01) {
          const kpx = (Math.min(fm, 1.6) / fm) * fleeMag * dodgeW;
          tx += fleeX * kpx;
          ty += fleeY * kpx;
        }
      } else if (perf) {
        // ---- 第一百八十四轮：万圣节双猫战 —— 她俩没有 hitbox 不可打，
        // AI 的全部工作是**活下来**：场内大圈巡游（0.6×宽 / 0.55×高），
        // 弹幕/南瓜/激光躲避照常叠加（flee 向量按 dodgeW 混入）。
        // 用户："4人模式……她们几乎无法打过后期的怪" → AI 别站桩吃弹。
        // 第二百零三轮：蕾米终演出（rf）走同一条分支 —— 同样是"打不到的纯演出，只能活下来"。
        this._hwA = (this._hwA ?? Math.random() * 6.283) + 0.5 / 60;
        tx = arena.cx + Math.cos(this._hwA) * arena.hw * 0.6;
        ty = arena.cy + Math.sin(this._hwA) * arena.hh * 0.55;
        // 躲弹幕/激光/南瓜十字：预测式找缝（见 _safeStep）。没危险时返回原目标，照常巡游。
        const ss = this._safeStep(g, p, tx, ty, arena);
        tx = ss.x; ty = ss.y;
        this._dodgeNow = !!ss.danger;
      } else if (ff) {
        // ---- 199 轮（用户："教学30秒的弹幕因为没有芙兰存在，所以ai猫娘完全不动直接吃弹幕到全死"）----
        // 序章 30 秒教学弹幕期间芙兰还没出场（ff.boss 为空），原来落进下面"待命"分支 —— 站着不动不躲弹。
        // 现在和有 boss 时一样做预测式找缝（_safeStep），在场地下半区巡游（弹幕多从上方来）。
        // 待命点：实测（tools/_prologue-map.mjs / _prologue-map2.mjs，全场 80+ 个静止位置 × 3~4 种子）——
        // 这张卡的旋转十字弹幕在场地【侧边/四角堆积】（|x|≥0.5×半宽、|y|≥0.5×半高时 18~20 次被击中），中心也不好（12~17），
        // 真正安全的是一个小口袋：|x| ≤ 0.2×半宽、y = 中心 ± 0.55×半高（4.3~5 次，比默认出生点站桩还好）。
        // 所以：待在离自己较近的那一侧口袋里，横向只在 ±0.2×半宽内微调；弹逼近时再由 _safeStep 预测躲避。
        const band = (p.y >= arena.cy ? 1 : -1) * arena.hh * 0.55;
        tx = Math.max(arena.cx - arena.hw * 0.2, Math.min(arena.cx + arena.hw * 0.2, p.x));
        ty = arena.cy + band;
        // 不做任何位移躲避 —— 三种躲法 A/B 实测（10 个种子，30 秒）：
        //   纯口袋待命 5.5 次 ＜ 口袋+逃避向量 7.0 次 ＜ 口袋+_safeStep 预测跳步 7.6~9.7 次。
        // 这张卡是以场地中心为轴的切线"风车"弹线，一动就容易横穿弹线；站在口袋里让弹线从旁边掠过最稳。
        // （旧行为：站在场地正中心不动 = 17.0 次，所以"全死"。）
      } else {
        // boss 不在场（演出阶段）：守在场地中心附近待命，绝不乱跑出框
        tx = arena.cx; ty = arena.cy;
      }
      // 目标点 clamp 进 boss 场地（内缩 46 —— 贴空气墙也有走位余量）。
      // 威胁躲避目标同样被 clamp：躲弹幕也不会被挤出场。
      const clampToArena = (v) => ({
        x: Math.max(arena.cx - arena.hw + 46, Math.min(arena.cx + arena.hw - 46, v.x)),
        y: Math.max(arena.cy - arena.hh + 46, Math.min(arena.cy + arena.hh - 46, v.y)),
      });
      ({ x: tx, y: ty } = clampToArena({ x: tx, y: ty }));
      this._bossArenaClamp = clampToArena;   // 走位平滑/救援目标也走同一 clamp
    } else {
      this._bossArenaClamp = null;
    }
    if (!vergilMode && !hasRanged && nearest && nearestD < 200) {
      // 近战：只对"落单"怪环绕输出（200 内 ≤3 只）；被围殴立刻转逃跑。
      // 环绕半径 70 = 猫爪类武器攻击边缘 —— 打得到怪、碰撞吃不到。
      let crowd = 0;
      if (Array.isArray(enemies)) {
        for (const e of enemies) {
          if (!e || e.dead || e.isChest) continue;
          if (Math.hypot(e.x - p.x, e.y - p.y) < 200) crowd++;
        }
      }
      if (crowd <= 3) {
        const d = nearestD || 1;
        if (d > 95) {
          tx = nearest.x + (p.x - nearest.x) / d * 70;
          ty = nearest.y + (p.y - nearest.y) / d * 70;
        } else {
          // 切向环绕 + 拉回 70 半径
          const ux = (p.x - nearest.x) / d, uy = (p.y - nearest.y) / d;
          tx = p.x + (-uy) * 70 + ux * (70 - d) * 0.6;
          ty = p.y + (ux) * 70 + uy * (70 - d) * 0.6;
        }
      } else {
        tx = p.x + fleeX * fleeMag;
        ty = p.y + fleeY * fleeMag;
      }
    } else if (!vergilMode) {
      // ---- 第一百八十四轮（用户："修复之后猫娘ai好像也不打他了"）——
      // 这个分支是**普通波**的"风筝/捡漏/巡逻"移动，boss 战绝不能进：
      // 修掉 boss 排斥后 flee=0 → hasThreat=false → 落进下面的巡逻
      // 分支，把环绕目标点整个覆盖成 0.72×半场巡逻圈（~400px）——AI
      // 永远悬在射程外，实测 boss 血一滴不掉。boss 战的移动就是上面的
      // 武器分站位环绕（弹幕躲避已按 dodgeW 混入），到此为止。
      const hasThreat = (fleeX !== 0 || fleeY !== 0);
      if (hasThreat) {
        // 风筝：逃离威胁（旋转 ~34° 走弧线 —— 纯直线逃跑会被直线弹幕
        // 追上，弧线让每发子弹都差一点）+ 场心拉力 + 拾取吸引
        // 第一百六十九轮：逃跑方向锥（<45°、<180px）内有**安全掉落**
        // （自身离怪 >200）就直奔顺路吃 —— 吸附机制，逃跑不白跑
        const rot = 0.6 * (this._orbitDir ??= (Math.random() < 0.5 ? 1 : -1));
        const cosR = Math.cos(rot), sinR = Math.sin(rot);
        const rfX = fleeX * cosR - fleeY * sinR;
        const rfY = fleeX * sinR + fleeY * cosR;
        const distC = Math.hypot(p.x, p.y);
        const pullC = Math.max(0, distC - half * 0.78) / 160;
        const onPath = this._pickupOnPath(g, p, rfX, rfY);
        if (onPath) {
          tx = onPath.x; ty = onPath.y;
        } else {
          tx = p.x + rfX * fleeMag - p.x * 0.1 * pullC + this._pickupPull(g, p, 'x') * 0.5;
          ty = p.y + rfY * fleeMag - p.y * 0.1 * pullC + this._pickupPull(g, p, 'y') * 0.5;
        }
      } else if (nearest && nearestD > 500) {
        // 第一百六十九轮：真安全（最近怪 >500）才离巡逻圈去捡漏 ——
        // 只捡自身离怪 >220 的安全掉落；阈值定 500 而不是 350：
        // 教训 —— 350 就判定"安全"会主动走向怪（无光环实测波 1 送头）
        const pk = this._safePickup(g, p, 620);
        if (pk) {
          tx = pk.x; ty = pk.y;
        } else {
          this._orbit = (this._orbit ?? Math.random() * 6.283) + 0.3;
          tx = Math.cos(this._orbit) * half * 0.72;
          ty = Math.sin(this._orbit) * half * 0.72;
        }
      } else {
        // 安全期绝不站桩：贴边绕场巡逻（0.72×半场）—— 怪从场内刷，
        // 场边一侧永远只有半个方向的怪，幸存者类的经典遛怪位。
        // 场心拉力 dead-zone 同步放大到 0.78，巡逻圈不被拽回。
        this._orbit = (this._orbit ?? Math.random() * 6.283) + 0.3;
        const px = Math.cos(this._orbit) * half * 0.72;
        const py = Math.sin(this._orbit) * half * 0.72;
        const pk = this._nearestPickup(g, p);
        tx = pk ? pk.x * 0.6 + px * 0.4 : px;
        ty = pk ? pk.y * 0.6 + py * 0.4 : py;
      }
    }
    // ---- 走位平滑（低通 + 迟滞）----
    // 威胁向量帧间微小变化 → 目标点左右横跳（实测"鬼畜般高速抖动"）。
    // 新目标先和旧目标按 4:6 混合；混合后位移 <26px 就不更新 —— 角色继续
    // 朝旧目标走，只有真正有意义的方向变化才会改道。
    // 躲弹中(_dodgeNow)不做低通：0.52s 的预测窗口里，60% 旧目标惯性会让闪避慢半拍
    let mix = (this._tgt && !this._dodgeNow)
      ? { x: tx * 0.4 + this._tgt.x * 0.6, y: ty * 0.4 + this._tgt.y * 0.6 }
      : { x: tx, y: ty };
    // ---- 第一百八十四轮：墙 = 大颗弹幕（路径穿墙预判绕行）----
    // 用户："撞墙还是会蹭墙很久，这段时间够她满血到死了。有什么更好的
    // 逻辑写法吗？比如把墙对她的概念写成大颗弹幕？"—— 就是这个思路：
    // 目的地方向被墙掐断时，像躲弹幕一样往通畅的斜向绕（±52°/±97° 依次
    // 试，取首个通路），不再顶着墙蹭。178 轮的落点外推继续兜底。
    try {
      const terrain = g.world?.terrain;
      if (terrain && ((terrain.rects?.length ?? 0) + (terrain.circles?.length ?? 0)) > 0) {
        const pad = (p.radius ?? 12) + 14;
        const mdx = mix.x - p.x, mdy = mix.y - p.y;
        const md = Math.hypot(mdx, mdy);
        if (md > 60) {   // 短移动不预判（绕行本身有走位成本）
          const blocked = (x, y) => blockedAt(terrain, x, y, pad);
          const pathHit = [0.35, 0.65, 1.0].some((k) => blocked(p.x + mdx * k, p.y + mdy * k));
          if (pathHit) {
            const baseA = Math.atan2(mdy, mdx);
            let done = false;
            for (const da of [0.9, -0.9, 1.7, -1.7]) {
              const a = baseA + da;
              const nx = p.x + Math.cos(a) * md, ny = p.y + Math.sin(a) * md;
              if (!blocked(p.x + (nx - p.x) * 0.5, p.y + (ny - p.y) * 0.5) && !blocked(nx, ny)) {
                mix = { x: nx, y: ny };
                done = true;
                break;
              }
            }
            if (!done) mix = { x: p.x * 0.55, y: p.y * 0.55 };   // 四面全堵 → 拉回身位
          }
        }
      }
    } catch { /* 地形异常不影响走位 */ }
    // ---- 第一百七十二轮：刷怪预告避让 ----
    // 用户："知道哪里提前刷了就不过去，不知道一刷新就来个亲嘴。"
    // world.spawnWarnings = 1 秒后落地成怪的位置 —— 目标点若落在某个
    // 警告的 240px 危险半径内，就沿"远离警告"方向把落点推出去，
    // 宁可多走两步也不站到刷新点上。
    try {
      const warns = g.world?.spawnWarnings;
      if (Array.isArray(warns) && warns.length) {
        for (const w of warns) {
          const dx = mix.x - w.x, dy = mix.y - w.y;
          const d = Math.hypot(dx, dy) || 1;
          if (d < 240) {
            const push = (240 - d) + 60;   // 推出危险半径再多让 60px
            mix = { x: mix.x + (dx / d) * push, y: mix.y + (dy / d) * push };
          }
        }
      }
    } catch { /* 预告列表异常不影响走位 */ }
    // ---- 第一百七十八轮：地形避让（AI 不再撞墙卡死）----
    // 用户："猫娘ai走位挺强的，比我还好，但是死是撞墙死了……她卡在上面
    // 死了2次，自救起来还是秒死。" 实验室地砖/花园花坛这两张图的障碍
    // 会把风筝路径掐断：目标点落进障碍 → 角色顶墙蹭蹭不动 → 怪围上来。
    // 落点若 blocked 就沿"障碍中心 → 落点"方向推出去；推完还堵就朝
    // 场心方向拉（场心永远不堵）。
    try {
      const terrain = g.world?.terrain;
      if (terrain && ((terrain.rects?.length ?? 0) + (terrain.circles?.length ?? 0)) > 0) {
        const pad = (p.radius ?? 12) + 18;
        if (blockedAt(terrain, mix.x, mix.y, pad)) {
          // 找包含/最贴近落点的障碍，拿它的中心当"外推源"；外推量按
          // 障碍**实际尺寸**算（实验室地砖 ~290px 见方，推 90px 还在砖里）
          let cx = 0, cy = 0, halfSpan = 0, best = Infinity;
          for (const r of (terrain.rects ?? [])) {
            const mx = (r.x0 + r.x1) / 2, my = (r.y0 + r.y1) / 2;
            const d = Math.hypot(mix.x - mx, mix.y - my);
            if (d < best) {
              best = d; cx = mx; cy = my;
              halfSpan = Math.max(r.x1 - r.x0, r.y1 - r.y0) / 2;   // 半宽
            }
          }
          for (const c of (terrain.circles ?? [])) {
            const d = Math.hypot(mix.x - c.cx, mix.y - c.cy);
            if (d < best) { best = d; cx = c.cx; cy = c.cy; halfSpan = c.r; }
          }
          let dx = mix.x - cx, dy = mix.y - cy;
          const d = Math.hypot(dx, dy) || 1;
          dx /= d; dy /= d;
          const push = halfSpan + pad + 26;   // 半宽 + 身位余量，保证出墙
          let nx = cx + dx * push, ny = cy + dy * push;
          if (blockedAt(terrain, nx, ny, pad)) {
            // 外推还堵（落点在障碍另一侧/贴角落）：改朝场心拉
            nx = mix.x * 0.4; ny = mix.y * 0.4;
          }
          mix = { x: nx, y: ny };
        }
      }
    } catch { /* 地形异常不影响走位 */ }
    // ---- 第一百八十四轮（用户："维吉尔砍谁也不动，4个人都在四角挂机"）----
    // 迟滞在 boss 战会**死锁**：维吉尔战 enemies 空 → flee=0 → 切向环绕点
    // 几乎不随帧变化 → 到位后 mix 与旧 _tgt 永远 <26 → 每帧 return 不更新
    // agentMoveTarget = 站桩到底。boss 战环绕本身是连续运动，跳过迟滞。
    // ---- 第一百八十四轮（用户："几乎不打 boss……90 秒到了几乎受到的
    // 伤害 1% 都没有"）—— 普通波的 boss 交战拉力：目标点 32% 权重混向
    // boss —— 风筝姿态保留（威胁排斥照算），但整体站位被慢慢拉进武器
    // 射程，90 秒不再空过。迟滞对有 boss 的波同样跳过（拉力是缓慢位移，
    // 会被 <26px 迟滞吃掉）。
    if (bossE && !vergilMode) {
      mix = { x: mix.x * 0.68 + bossE.x * 0.32, y: mix.y * 0.68 + bossE.y * 0.32 };
    }
    // ---- 197 轮：偶发走位失误（只在非救援/非拆箱的常规走位阶段；救援与拆箱在更早处已 return）----
    // 每个决策拍以 (拍长秒数 / stumbleEvery) 的概率开始一次踉跄：stumbleDur 秒内朝随机方向走，无视目标。
    if (S.stumbleEvery) {
      const nowS = this._fr / 60;
      if (!this._stumble && this._rnd() < (per / 60) / S.stumbleEvery) {
        this._stumble = { until: nowS + S.stumbleDur, ang: this._rnd() * Math.PI * 2 };
      }
      if (this._stumble) {
        if (nowS < this._stumble.until) {
          let sx = p.x + Math.cos(this._stumble.ang) * 140, sy = p.y + Math.sin(this._stumble.ang) * 140;
          if (this._bossArenaClamp) ({ x: sx, y: sy } = this._bossArenaClamp({ x: sx, y: sy }));
          this._dodgeNow = true;
          p.agentMoveTarget = { x: sx, y: sy };
          return;
        }
        this._stumble = null;
      }
    }
    if (!vergilMode && !bossE && this._tgt && Math.hypot(mix.x - this._tgt.x, mix.y - this._tgt.y) < 26) return;
    // ---- 第一百八十三轮：最终落点也 clamp 进 boss 场地 ----
    // spawn 警告避让/地形避让都可能把落点推到空气墙外 —— boss 战里一切
    // 目标以场地为界（用户："她疑似还在用原本的地图移动"）。
    if (this._bossArenaClamp) mix = this._bossArenaClamp(mix);
    this._tgt = mix;
    // 直设实体自己的 agentMoveTarget（第一百七十一轮修复，不走全局 doAction）
    p.agentMoveTarget = { x: mix.x, y: mix.y };
  }

  /**
   * 预测式躲避（第一百八十五轮，万圣节战 A/B 实测驱动）。
   *
   * 为什么要它：旧的"朝我来的弹 → 往垂直方向闪"只对单发追踪有效；面对环形弹、弹墙这类
   * 密集图样，左右相邻的弹把垂直力互相抵消，AI 根本不知道"缝在哪"。实测同样 9 秒弹幕，
   * 修完速度向量/像素换算之后命中数仍是 6~17 次（基线 7~17），所以必须换算法。
   *
   * 做法：对"原地 + 16 个方向"每个候选，按弹的直线轨迹模拟未来 0.52s（4 个采样点），
   * 取候选位置到每颗弹的最小间隙（扣掉双方判定半径 + 6px 余量）；再叠加万圣节的两类
   * 非弹危险区（旋转激光射线、南瓜十字爆风）。评分 = 间隙 − 离"原本想去的位置"的距离 × 0.06，
   * 所以没危险时仍走原来的环绕/巡游，有危险时才被推进缝里。
   * 仅用于万圣节战；返回新的目标点（离开当前位置 ≤160px）。
   */
  /**
   * 199 轮：芙兰战里 P1 代打用"小小猫娘消弹"自救（用户："教学30秒的弹幕……ai猫娘完全不动直接吃弹幕到全死"）。
   * 此前 AI 从来不按消弹 —— 它是这套战斗最核心的保命手段（半径 180、冷却 15s、手动触发送 1s 无敌）。
   * 触发：≥1 颗弹在 0.3 秒内会擦到自己（弹道垂距 < 判定半径 + 自身 + 10px），且消弹已就绪（首版门槛 ≥2 颗：30 秒里 3 个种子只触发 1 次、且在 32 秒才触发，太晚）。
   * 只有 P1 代打调用（!this.entity）：消弹冷却是整场共享的，队友 AI 按了会抢走人类玩家的技能。
   */
  _maybePurge(world, p, ff) {
    if (ff.over || !ff._hinted || ff.purgeCd > 0 || ff.nova) return false;
    const ebs = world?.enemyBullets?.items;
    if (!Array.isArray(ebs)) return false;
    const myR = p.hitR ?? p.radius ?? 10;
    let imminent = 0;
    for (const b of ebs) {
      if (!b || b.dead) continue;
      const dx = p.x - b.x, dy = p.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 > 140 * 140) continue;
      const sp = b.speed ?? 0; if (sp < 30) continue;
      const vx = Math.cos(b.angle ?? 0) * sp, vy = Math.sin(b.angle ?? 0) * sp;
      const tcl = (dx * vx + dy * vy) / (sp * sp);          // 最近点时刻
      if (tcl < 0 || tcl > 0.3) continue;
      const mx = dx - vx * tcl, my = dy - vy * tcl;
      if (Math.hypot(mx, my) < (b.hitRadius ?? b.radius ?? 6) + myR + 10) imminent++;
      if (imminent >= 1) break;
    }
    if (imminent < 1) return false;   // 冷却只有 15s、手动触发还送 1s 无敌：有 1 颗确定会命中就按，不省着
    ff.tryManualPurge();
    return true;
  }
  _safeStep(g, p, tx, ty, arena) {
    const S = this._skillCfg();
    // 197 轮：走神——整次预测被跳过（返回原目标，当没看见）。pro 为 0
    if (S.safeSkip && this._rnd() < S.safeSkip) return { x: tx, y: ty, danger: 0 };
    const world = g.world;
    const hf = world?.halloweenFight;
    const ebs = world?.enemyBullets?.items;
    const SCAN = 290;
    const th = [];   // 扁平数组：x, y, vx, vy, r
    if (Array.isArray(ebs)) {
      for (const b of ebs) {
        if (!b || b.dead || b.hostileToEnemies) continue;
        const dx = b.x - p.x, dy = b.y - p.y;
        if (dx * dx + dy * dy > SCAN * SCAN) continue;
        const sp = b.speed ?? 0;
        th.push(b.x, b.y, b.vx || Math.cos(b.angle ?? 0) * sp, b.vy || Math.sin(b.angle ?? 0) * sp, b.hitRadius ?? b.radius ?? 7);
      }
    }
    // 199 轮：芙兰战的激光（莱瓦汀炎剑）也算危险区。此前 _safeStep 只认万圣节的 waves，对芙兰的激光是瞎的。
    // 字段映射：angVel→rot；warn（预警秒数）→delay：预警期内光束尚未生效，hazard 里按 delay 推迟。
    let lasers = hf?.waves;
    if (!lasers) {
      const fl = world?.flandreFight?.spell?.st?.lasers;
      // span = 这把剑【还要扫过的角度】：预警期 = 整个扫程；生效后 = 剩余可扫时间（去掉最后 0.5s 渐隐）× 角速度。
      // 扫掠扇面模式（有 span）把整片将被扫过的扇面一次性当危险区，不按时间点预测 —— 激光 800px/s 的角速度下，
      // 0.52s 的预测视野看见它时已来不及横挪出整个扇面（实测每个回合都被扫一次）。
      lasers = fl && fl.length ? fl.map((L) => ({ x: L.x, y: L.y, angle: L.angle, rot: L.angVel, delay: L.warn > 0 ? L.warn : 0, len: L.len, span: L.warn > 0 ? (L.sweep ?? L.angVel * 1.9) : L.angVel * Math.max(0, L.life - 0.5) })) : [];
    }
    const pumpkins = hf?.pumpkins ?? [];
    if (!th.length && !lasers.length && !pumpkins.length) return { x: tx, y: ty, danger: 0 };

    const myR = p.hitR ?? p.radius ?? 10;
    const spd = Math.max(120, (p.baseSpeed ?? 220) * (p.stats?.moveSpeedMultiplier ?? 1));
    const TAUS = S.taus;   // 197 轮：预测地平线随档位（pro 0.52s / normal 0.36s / rookie 0.22s）
    const minX = arena.cx - arena.hw + 40, maxX = arena.cx + arena.hw - 40;
    const minY = arena.cy - arena.hh + 40, maxY = arena.cy + arena.hh - 40;

    // 非弹危险区代价：激光射线 / 南瓜十字（预警期也避，免得到点才反应）
    const hazard = (x, y, tau) => {
      let cost = 0;
      for (const w of lasers) {
        if (w.span != null) {
          const dx = x - w.x, dy = y - w.y, d = Math.hypot(dx, dy);
          if (d < 1 || d > w.len + 40) continue;
          let da = Math.atan2(dy, dx) - w.angle;
          while (da > Math.PI) da -= 2 * Math.PI;
          while (da < -Math.PI) da += 2 * Math.PI;
          const sgn = w.span >= 0 ? 1 : -1, off = da * sgn, half = Math.atan(46 / d);
          if (off >= -half && off <= Math.abs(w.span) + half) cost += 70;
          continue;
        }
        const dl = w.delay ?? 0;
        if (tau < dl) continue;
        const ang = w.angle + w.rot * (tau - dl);
        const dx = x - w.x, dy = y - w.y;
        const proj = dx * Math.cos(ang) + dy * Math.sin(ang);
        if (proj <= 0) continue;
        if (Math.abs(-dx * Math.sin(ang) + dy * Math.cos(ang)) < 38) cost += 70;
      }
      for (const pk of pumpkins) {
        const active = (pk.t > 0) ? tau >= pk.t - 0.12 : (pk.boom ?? 0) > 0;
        if (!active) continue;
        const dx = x - pk.x, dy = y - pk.y;
        if ((Math.abs(dx) < 50 || Math.abs(dy) < 50) && Math.hypot(dx, dy) < 235) cost += 70;
      }
      return cost;
    };

    let best = null, bestScore = -Infinity, stayClear = 99;
    for (let k = -1; k < S.dirs; k++) {
      const ang = k < 0 ? 0 : (k * 2 * Math.PI) / S.dirs;   // pro: 16 向，与旧版 k*π/8 逐位相同
      const ux = k < 0 ? 0 : Math.cos(ang), uy = k < 0 ? 0 : Math.sin(ang);
      let clear = 99, hz = 0, lx = p.x, ly = p.y;
      for (const tau of TAUS) {
        const x = Math.max(minX, Math.min(maxX, p.x + ux * spd * tau));
        const y = Math.max(minY, Math.min(maxY, p.y + uy * spd * tau));
        lx = x; ly = y;
        for (let i = 0; i < th.length; i += 5) {
          const bx = th[i] + th[i + 2] * tau, by = th[i + 1] + th[i + 3] * tau;
          const gap = Math.hypot(x - bx, y - by) - (myR + th[i + 4] + 6);
          if (gap < clear) clear = gap;
        }
        if (lasers.length || pumpkins.length) hz += hazard(x, y, tau);
      }
      if (k < 0) stayClear = clear;
      const c = clear < 0 ? clear * 3 : Math.min(clear, 36);
      const score = c - hz - Math.hypot(lx - tx, ly - ty) * 0.06 + (S.scoreNoise ? (this._rnd() - 0.5) * S.scoreNoise : 0);
      if (score > bestScore) { bestScore = score; best = { ux, uy, k }; }
    }
    if (!best || best.k < 0) return { x: p.x, y: p.y, danger: stayClear < 30 ? 1 : 0, stay: true };
    return { x: p.x + best.ux * 160, y: p.y + best.uy * 160, danger: stayClear < 30 ? 1 : 0 };
  }

  /** 420 内最近拾取物（巡逻时顺路捡） */
  _nearestPickup(g, p) {
    try {
      const items = g.world?.pickups?.items;
      if (!Array.isArray(items)) return null;
      let best = null, bestD = 420;
      for (const it of items) {
        if (!it || it.dead) continue;
        const d = Math.hypot(it.x - p.x, it.y - p.y);
        if (d < bestD) { bestD = d; best = it; }
      }
      return best;
    } catch { return null; }
  }

  /** 第一百六十九轮：安全掉落 —— 范围内且自身离最近怪 >220 的掉落 */
  _safePickup(g, p, maxD) {
    try {
      const items = g.world?.pickups?.items;
      const enemies = g.world?.enemies?.items;
      if (!Array.isArray(items)) return null;
      let best = null, bestD = maxD;
      for (const it of items) {
        if (!it || it.dead) continue;
        const d = Math.hypot(it.x - p.x, it.y - p.y);
        if (d >= bestD) continue;
        let minE = Infinity;
        if (Array.isArray(enemies)) {
          for (const e of enemies) {
            if (!e || e.dead || e.isChest) continue;
            const ed = Math.hypot(it.x - e.x, it.y - e.y);
            if (ed < minE) minE = ed;
          }
        }
        if (minE > 220) { bestD = d; best = it; }
      }
      return best;
    } catch { return null; }
  }

  /** 第一百六十九轮：吸附 —— 逃跑方向锥（夹角 <45°、距离 <180）内的
   *  安全掉落（自身离怪 >200），逃跑路上顺路吃 */
  _pickupOnPath(g, p, dirX, dirY) {
    try {
      const m = Math.hypot(dirX, dirY) || 1;
      const ux = dirX / m, uy = dirY / m;
      const items = g.world?.pickups?.items;
      const enemies = g.world?.enemies?.items;
      if (!Array.isArray(items)) return null;
      let best = null, bestD = 180;
      for (const it of items) {
        if (!it || it.dead) continue;
        const dx = it.x - p.x, dy = it.y - p.y;
        const d = Math.hypot(dx, dy);
        if (d < 1 || d > 180) continue;
        if ((dx * ux + dy * uy) / d <= 0.7 || d >= bestD) continue;
        let minE = Infinity;
        if (Array.isArray(enemies)) {
          for (const e of enemies) {
            if (!e || e.dead || e.isChest) continue;
            const ed = Math.hypot(it.x - e.x, it.y - e.y);
            if (ed < minE) minE = ed;
          }
        }
        if (minE > 200) { bestD = d; best = it; }
      }
      return best;
    } catch { return null; }
  }

  /** 最近的拾取物对走位的吸引（只追 420 内的，弱权重） */
  _pickupPull(g, p, axis) {
    try {
      const items = g.world?.pickups?.items;
      if (!Array.isArray(items)) return 0;
      let best = null, bestD = 420;
      for (const it of items) {
        if (!it || it.dead) continue;
        const d = Math.hypot(it.x - p.x, it.y - p.y);
        if (d < bestD) { bestD = d; best = it; }
      }
      if (!best) return 0;
      return (best[axis] - p[axis]) * 0.25;
    } catch { return 0; }
  }

  // ---------------------------------------------------------
  // 选卡：打分制
  // ---------------------------------------------------------
  _pickUpgrade(g, ent) {
    // entity 模式（P2）不走选卡 UI：升级在 tick 里自动应用
    if (this.entity) return;
    const opts = Array.isArray(g.currentUpgradeOptions) ? g.currentUpgradeOptions : [];
    if (opts.length === 0) return;
    const early = (g.waveIndex ?? 0) < 4;   // 前 4 波：见血必选（噩梦 10 血基底扛不住）
    // ---- 第一百八十四轮（用户："1/s回血、10点护甲的发育优先级拉高"）：
    // 缺口加权 —— hpRegen 没到 1/s 时回血选项权重 ×3；护甲没到 10 时
    // 护甲选项 ×2.5。补齐后回落基础权重（不无脑堆满）。
    const regenGap = (ent.stats?.get?.('hpRegen') ?? 0) < 1;
    const armorGap = (ent.stats?.get?.('armor') ?? 0) < 10;
    let bestI = 0, bestS = -Infinity;
    opts.forEach((o, i) => {
      let s = 0;
      for (const [k, v] of Object.entries(o?.mods || {})) {
        s += (STAT_SCORE[k] ?? 2) * Math.abs(Number(v) || 1) * 0.2;
      }
      for (const [k, v] of Object.entries(o?.pct || {})) {
        let w = STAT_SCORE[k] ?? 2;
        if (k === 'maxHp' && early) w *= 1.8;
        if (k === 'hpRegen' && regenGap) w *= 3;
        if (k === 'armor' && armorGap) w *= 2.5;
        s += w * (Number(v) || 0);
      }
      // mods 同样吃缺口加权（有的升级包把回复/护甲放 flat 里）
      if (regenGap && Number(o?.mods?.hpRegen) > 0) s += 30;
      if (armorGap && Number(o?.mods?.armor) > 0) s += 25;
      // 血量选项绝对优先：maxHp 是加法（+6），前期一条命的价值远超输出
      if (early && Number(o?.mods?.maxHp) > 0) s += 100;
      // 带 curse / 负面 pct 的选项（>18% 伤害换 -10% 血那种）一票否决。
      // ---- 184 轮（用户："升级的时候有高品质负面属性的升级也可以点"）
      // —— 改成看**等效生命值**：EHP = maxHp × (1 + min(1, armor/30))
      // （30 甲=50% 减伤≈2倍血），EHP>50 的一击线足够，负面升级正常
      // 参与评分；血薄时照旧否决。
      const maxHpU = ent.stats?.get?.('maxHp') ?? ent.maxHp ?? 0;
      const ehpU = maxHpU * (1 + Math.min(1, (ent.stats?.get?.('armor') ?? 0) / 30));
      // （v0.3.80 曾把这里的 ent 误写成 p —— 方法内无 p，ReferenceError 被
      // tick 的静默 catch 吞掉，1P adopt 模式选卡整段跳过 = "AI 的 1P 需要
      // 手动升级"。用户实测抓出，v0.3.82 修复。）
      if (o?.pct && Object.values(o.pct).some((v) => Number(v) < 0) && ehpU <= 50) s -= 1000;
      if (s > bestS) { bestS = s; bestI = i; }
    });
    globalThis.NekoAgent?.doAction?.(`chooseUpgrade(${bestI})`);
  }

  // ---------------------------------------------------------
  // 商店：优先买远程武器（定风筝基调），再补 afford 道具，然后推波
  // ---------------------------------------------------------
  _nextWave(g, ent) {
    try {
      const shop = g.shop;
      const p = ent ?? g.player;
      if (shop && p && Array.isArray(shop.offers)) {
        // ① 先合成：两把同名 → 高一阶武器（T4 传说只能合成得到，伤害跃升）
        // 第一百七十一轮（2P）：entity 模式跳过 —— canCombine 会扫到
        // P1 货架的同名武器（分池泄漏，花 P2 的钱买 P1 的格子）
        try {
          if (!this.entity) {
          const ids = new Set((p.weapons || []).map((w) => w?.def?.id).filter(Boolean));
          for (const wid of ids) {
            const info = shop.canCombine?.(p, wid);
            if (info?.ok) {
              const r = shop.combine(p, wid);
              if (r?.ok) this._log(`合成 ${wid} → ${r.target?.name ?? '?'}${r.upgraded ? `（升到 Lv.${r.level}）` : ''}`);
            }
          }
          }
        } catch { /* 合成失败不影响购买 */ }
        const weaponCount = (p.weapons || []).length;
        // ---- 第一百六十九轮：土豆兄弟铁律 —— 前 5 波武器栏没满 6 把，
        // 钱全砸武器（道具再香不看；武器多 = 输出成倍，发育滚雪球）；
        // 满 6 后转向攻速/伤害/血量。减益道具依旧一票否决。
        // ---- 第一百八十四轮（用户："接手之后进商店发现居然有1k+金币，
        // 她们走位不是很差为啥后期这么爱暴毙，是否是钱一直屯着发育不良？"）
        // —— 实锤：buyCap=3 + 补货门槛苛刻，中后期每波收入几十上百，
        // 只花得掉 3 件的钱 → 金币无上限囤积 = 发育停滞 = 暴毙。
        // 修：① 每波买满自己 4 格；② 富有（>150）时任何非诅咒道具都买
        // （属性保值，钱趴着才是亏）；③ 囤钱 >120 自动刷新货架接着买
        // （最多 3 轮，刷新费便宜且保留已售格）。
        const rushWeapons = weaponCount < 6 && (g.waveIndex ?? 0) < 5;
        const buyCap = 4;
        let bought = 0;
        // ---- 第一百七十一轮（2P）：商店分池 —— entity 模式只买自己
        // 的专属格，P1 买前半，互不抢货。
        // ---- 第一百八十四轮（1~4P）：货架按槽位排 P1|P2|P3|P4 各 4 格
        // —— slotBase = 槽位 × 4，各买各的段，绝不错拿别人的货。
        const world2 = g.world;
        // 201 轮：段号 = 队友在 world.partners() 里的顺序 + 1 —— 与 main._openShop 拼货架的顺序一致。
        // 旧写法只认 player2/player3，其余一律当 3：4P 时多个队友挤在同一段（P4 的 12~15 格）互相抢货，P2/P3 的货架没人买。
        const mateList = world2?.partners?.() ?? [];
        const mateIdx = this.entity ? mateList.indexOf(this.entity) : -1;
        const slotIdx = this.entity
          ? (mateIdx >= 0 ? mateIdx + 1 : (this.entity === world2?.player2 ? 1 : this.entity === world2?.player3 ? 2 : 3))
          : 0;
        const slotBase = slotIdx * 4;
        const slotEnd = slotIdx === 0 ? 4 : Math.min(shop.offers.length, slotBase + 4);
        // ---- 184 轮（用户："金钱还是很容易有几百余额，让她们1-10回合
        // 余额不超过30，11-20不超过100。该花就花"）—— 硬预算：低于 10 波
        // 压到 30，11 波起 100。刷新门槛与兜底扫货都按这个水位走。
        const matCap = (g.waveIndex ?? 0) < 10 ? 30 : 100;
        for (let round = 0; round < 5 && bought < buyCap; round++) {
          if (round > 0) {
            // 囤钱刷新：钱趴着 = 发育不良（用户实测 1k+ 金币暴毙局）
            if (p.materials < matCap + 40) break;
            const rr = shop.reroll?.(p, slotBase);   // 201 轮：只刷新自己那一段（旧写法刷新的是 P1 的前 4 格）
            if (!rr?.ok) break;
            this._log(`囤钱刷新货架（剩 ${Math.floor(p.materials)}）`);
          }
          for (let slot = slotBase; slot < slotEnd && bought < buyCap; slot++) {
          const o = shop.offers[slot];
          if (!o || o.sold) continue;
          if (!(p.materials >= o.price)) continue;
          const isWeapon = o.kind === 'weapon';
          // 决策：非近战武器见到就买（远程/元素/炮台都定风筝基调）；
          // 同名武器已持有就不重复买（重复买占栏不叠属性）；
          // 诅咒道具绝不买（血之契约 -30% maxHp 曾把猫娘坑到 10 血暴毙）
          const isRangedWeapon = isWeapon && o.data?.kind !== 'melee';
          const hasCurse = o.data?.curse && Object.keys(o.data.curse).length > 0;
          // 贪婪神像类：没有 curse 字段但 pct 带负值（伤害-8% 换收获），
          // 风筝流输出就是命，同样不买
          const hasNegPct = o.data?.pct && Object.values(o.data.pct).some((v) => Number(v) < 0);
          // ---- 184 轮（用户："负面道具不是不可以买，但要保障一击线不能
          // 太低，比如猫娘ai自己算一下自己的等效生命值高于40-50再买扣血
          // 上限的道具，不至于买了满血一下就死了"）—— EHP>50 放行负面。
          const maxHpS = p.stats?.get?.('maxHp') ?? p.maxHp ?? 0;
          const ehpOk = maxHpS * (1 + Math.min(1, (p.stats?.get?.('armor') ?? 0) / 30)) > 50;
          if ((hasCurse || hasNegPct) && !ehpOk) continue;
          // 血量/移速道具见到必买（10 血基底的命根子 + 风筝流的腿）
          const isHpItem = ['warm_milk', 'healing_herb', 'cat_taffy', 'wool_armor', 'steel_plate', 'running_shoes', 'feather'].includes(o.data?.id);
          // 同名武器允许持有 2 把（游戏机制：两把同名可合成升级），
          // 第 3 把起纯占栏，跳过
          if (isWeapon && typeof p.findWeapons === 'function' && p.findWeapons(o.data.id).length >= 2) continue;
          // 决策：冲刺期武器优先（近战先塞栏，之后靠合成腾位），但回血/
          // 护甲道具**照样买**（用户："1/s回血起步"—— 武器冲刺不该牺牲
          // 血线）；满栏后远程武器见到仍买 / 血量移速必买 / 1~2 件较贵补给。
          // 富有（>150）时任何非诅咒道具都买 —— 属性保值，囤钱=发育不良。
          const rich = p.materials > 150;
          const want = rushWeapons
            ? (isWeapon || isHpItem || rich)
            : (isRangedWeapon
              || (isWeapon && weaponCount < 6)
              || isHpItem
              || (!isWeapon && (bought < 2 || rich) && (o.price >= 10 || rich)));
          if (!want) continue;
          const r = shop.buy(p, slot);
          if (r && r.ok !== false) {
            bought++;
            this._log(`商店购入【${o.data?.name ?? o.data?.id ?? slot}】(-${o.price})${rushWeapons && isWeapon ? '（6 武器冲刺）' : ''}`);
          }
          }
        }
        // ---- 兜底扫货：刷新买完仍超预算 → 挑自己格子里最贵的非诅咒
        // 直接买（该花就花，钱趴着=发育不良；负面道具吃上面的 EHP 门槛）。
        let guard = 0;
        while (p.materials > matCap && guard++ < 6) {
          let pick = -1, pickPrice = 0;
          for (let slot = slotBase; slot < slotEnd; slot++) {
            const o = shop.offers[slot];
            if (!o || o.sold || o.price > p.materials) continue;
            const oc = o.data?.curse && Object.keys(o.data.curse).length > 0;
            const onp = o.data?.pct && Object.values(o.data.pct).some((v) => Number(v) < 0);
            if ((oc || onp) && !ehpOk) continue;
            if (o.price > pickPrice) { pickPrice = o.price; pick = slot; }
          }
          if (pick < 0) break;
          const r = shop.buy(p, pick);
          if (!r || r.ok === false) break;
          bought++;
          this._log(`兜底扫货【${shop.offers[pick]?.data?.name ?? pick}】(-${shop.offers[pick]?.price})`);
        }
      }
    } catch { /* 商店买不成不影响推波 */ }
    // 第一百七十轮（2P）：entity 模式（pilot2）只买东西**不推波** ——
    // 推波是全局动作，由 P1 的 pilot 统一负责（不然两个大脑各推一次）
    if (this.entity) return;
    if (typeof g._beginWave === 'function' && Number.isInteger(g.waveIndex)) {
      // 防抖：同一波只推一次（shop 态每帧都会 tick 进来，不防抖会一帧
      // 推好几波 —— 实测曾一路推到 53638 波）
      if (this._lastPush === g.waveIndex) return;
      this._lastPush = g.waveIndex;
      this._log(`第 ${g.waveIndex + 1} 波结算完毕 → 开第 ${g.waveIndex + 2} 波`);
      // 先关商店 overlay 再推波 —— 与正常"下一波"按钮回调同序。漏了这步
      // 会导致商店界面挂在屏上，玩家点"下一波"就又跳一波（实测跳波）。
      g.ui?.clearOverlay?.();
      g._beginWave(g.waveIndex + 1);
      // _beginWave 只做波次装配、不切状态（正常由商店按钮回调切）——
      // pilot 绕过 UI 直接推，必须自己把状态切回 playing
      g.state = 'playing';
    }
  }

  // ---------------------------------------------------------
  // 结算：20 波活着 = 通关；团灭 = **重试当前回合**（第一百七十四轮）
  // ---------------------------------------------------------
  // 用户原话："看2个猫娘模式疑似团灭之后变回1个猫娘玩了，回合也变成1，
  // 是用了单人猫娘模式重开吗？让猫娘ai不管一个人玩还是2个人玩团灭了都
  // 继续维持当前模式重试当前回合而不是回到第一回合吧。可以设置重试5次
  // 还过不去判定失败不重试第六次了。"
  //
  // 旧实现：死亡 → startRun(单人) → 模式丢失 + 回到第 1 波（就是用户
  // 看到的"变回1个猫娘 + 回合变成1"）。
  // 新实现：死亡 → g.retryWave()（快照重试：当前回合、装备/等级保留、
  // duo 由 retryWave 里的 _spawnPartner + pilot2 重绑保留）。
  // 同一回合重试 5 次仍过不去 → 判定失败，不再试第 6 次。
  _settle(g) {
    const reached = (g.waveIndex ?? 0) + 1;
    // ---- 第一百八十四轮（4P 实测）：20 波通关弹"无限模式"询问时，
    // **选继续而不是结算** —— 旧逻辑直接 g._endRun(true)，玩家看到的是
    // "芙兰还没出来就判定赢了，只有再来一局和返回标题，无法打 21 回合"
    //（芙兰没解锁时询问被 AI 瞬间点掉）。AI 挂机 = 帮玩家继续打。
    if (!g.player?.dead && reached >= 20 && g._endlessPromptOpen) {
      this._log('20 波通关：无限模式询问挂起 → AI 选择继续（开 21 波）');
      g._endlessPromptOpen = false;
      g.endlessEnabled = true;
      try { g.ui?.clearOverlay?.(); } catch { /* */ }
      g._openShop();
      return;
    }
    if (!g.player?.dead && reached >= 20) {
      this.win = true;
      this._log(`★ 通关！${this.characterId} 第 ${this.attempt} 次尝试完成 20 波${this.difficulty}（现实用时 ${Math.round((Date.now() - this.startedAt) / 1000)}s）`);
      this.stop(g);
      try { g._endRun(true); } catch { /* */ }
      return;
    }
    // 换了波就清零本回合的重试计数（"重试 5 次"按**同一回合**算）
    if (this._retryWave !== g.waveIndex) {
      this._retryWave = g.waveIndex;
      this._retryN = 0;
    }
    // 第 5 次重试仍倒在同一回合 → 判定失败，不重试第 6 次
    if ((this._retryN ?? 0) >= 5) {
      this.gaveUp = true;
      this._log(`✗ 第 ${reached} 波重试 ${this._retryN} 次仍未通过，判定失败（不重试第 6 次）`);
      this.stop(g);
      return;
    }
    // ---- 自动重试当前回合（快照重试：模式保留、不回第一回合）----
    this._retryN = (this._retryN ?? 0) + 1;
    this._hpSnapshot = null;
    this._tgt = null; this._lastX = null; this._lastY = null; this._stuckN = 0;
    this._log(`第 ${reached} 波团灭 → 重试当前回合（本回合第 ${this._retryN}/5 次）`);
    try {
      g.ui?.clearOverlay?.();
      const retried = g.retryWave?.(true);   // fromAi：不计入芙兰的"手动重开"门槛
      if (!retried) {
        // 没有快照（比如开局第一秒就没了）：兜底重开 —— duo 维持 duo
        // watch（两个 AI 的模式），单人维持单人。
        this._log('无波次快照，兜底重开（保留模式）');
        if ((globalThis.__duo || g.__duo) && typeof g.onDuoWatch === 'function') g.onDuoWatch();
        else g.startRun(this.characterId);
      }
      globalThis.NekoAgent?.setPuppetMode?.(true);
    } catch (e) {
      this._log(`重试失败：${e?.message ?? e}`);
      this.stop(g);
    }
  }
}
