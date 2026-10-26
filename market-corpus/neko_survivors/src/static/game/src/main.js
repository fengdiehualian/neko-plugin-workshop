// ============================================================
// 游戏状态机 + 主循环
// 状态：title -> charselect -> playing <-> levelup / shop -> result
// ============================================================

import { RNG, randomSeed } from './core/rng.js';
// 第一百六十三轮：移动端主循环限帧 30fps（仅手机，桌面恒 false 不受限）
import { MOBILE } from './core/mobile.js';
import { Input } from './core/input.js';
import { Camera } from './core/camera.js';
import { Renderer } from './systems/renderer.js';
import { World } from './systems/world.js';
import { Shop } from './systems/shop.js';
import { WeatherSystem } from './systems/weather.js';
import { CometDisaster } from './systems/comet.js';
import { Player } from './entities/player.js';
import { PICKUP_TYPE } from './entities/pickup.js';
import { Pet } from './entities/pet.js';
// 第一百六十轮：四重存在（莱万汀分身）
import { PhantomClone } from './entities/phantom.js';
import { UI } from './ui/ui.js';
import { rollUpgrades } from './data/upgrades.js';
import { getUpgrade } from './data/upgrades.js';
// 第十七轮：CHARACTERS 不再需要 —— 解锁逻辑删掉后，
// 选人界面直接用 availableCharacters()（现在恒等于全部角色）。
import { availableCharacters, getCharacter, getSpriteCharacters, CHARACTERS, CHARACTER_MAP } from './data/characters.js';
import { WAVE_COUNT, waveClearBonus, SPECIAL_WAVE_POOLS } from './data/waves.js';
import { getEnemy, ENEMIES, getSpriteEnemies, BOSS_VARIANTS, pickBossForWave } from './data/enemies.js';
import { getDifficulty, DEFAULT_DIFFICULTY } from './data/difficulty.js';
import { audio } from './systems/audio.js';
import { fxSettings } from './systems/fx.js';
import { clamp } from './core/math.js';
import { sprites, buildPreloadPlan } from './core/assets.js';
import { WEAPONS } from './data/weapons.js';
import { ITEMS } from './data/items.js';
import { BACKGROUNDS, DEFAULT_BACKGROUND, backgroundForWave, backgroundForBossWave } from './data/backgrounds.js';
import { BOSS_BANNED_BACKGROUNDS } from './data/terrain.js';
// 属性修正的存档读写收拢在 core/stats.js（StatSheet 自己所在的模块）：
// 见那边的长注释 —— 在 main.js 手抄字段名正是 curse 漏存的根因。
import { serializeStatMods, restoreStatMods } from './core/stats.js';
import { neko, EVENT } from './neko-integration.js';
// 第一百二十四轮：插件环境 → 官方猫娘通道（插件 action → push_message）
import { pluginBridge } from './plugin-bridge.js';
// 第四十九轮：隐藏挑战 Boss 芙兰朵露（素材与弹幕规格来自 touhou-flandre 拆包）
import { FlandreFight, flandreUnlockOk, ARENA } from './systems/flandre.js';
// 第一百八十四轮：万圣节双猫（巧克力 & 香草）—— Rabi-Ribi 万圣节 DLC 拆包素材
import { HalloweenFight, ARENA as HALLOWEEN_ARENA } from './systems/halloween.js';
import { RemiliaFight, REMILIA_ARENA, REMILIA_TOTAL } from './systems/remilia.js';
import * as RemiRender from './systems/remilia-render.js';
import { VergilFight, VERGIL_MAX_HP, ARENA as VARENA } from './systems/vergil.js';
import { NekoPilot } from './systems/neko-pilot.js';

export const STATE = {
  TITLE: 'title',
  CHAR_SELECT: 'char_select',
  PLAYING: 'playing',
  LEVEL_UP: 'level_up',
  SHOP: 'shop',
  PAUSED: 'paused',
  RESULT: 'result',
  // 第四十九轮：隐藏挑战 Boss 芙兰朵露的专属状态（世界活着，波次系统停摆）
  FLANDRE: 'flandre',
  HALLOWEEN: 'halloween',
  // 第二百零三轮：蕾米莉亚终演出（芙兰 EP 之后、万圣节双猫之前的中段）
  REMILIA: 'remilia',
  // 第一百四十一轮：大排档椅子·维吉尔 Boss 战（同芙兰骨架）
  VERGIL: 'vergil',
};

const SAVE_KEY = 'neko_miao_save_v1';
const META_KEY = 'neko_miao_meta_v1';
// 波次快照键（第八轮「重试本波」用）。和自动存档分开，语义不同：
// SAVE_KEY 是"当前进度"，RETRY_KEY 是"这一波刚开始时的样子"。
const RETRY_KEY = 'neko_miao_wave_snapshot_v1';
// 旧存档键：改名前的存档也读一下，避免老玩家进度丢失
const LEGACY_SAVE_KEY = 'neko_survivors_save_v1';
const LEGACY_META_KEY = 'neko_survivors_meta_v1';

export class Game {
  constructor(canvas) {
    this.canvas = canvas;
    this.input = new Input(canvas);
    this.camera = new Camera();
    this.renderer = new Renderer(canvas);
    this.ui = new UI();

    // 贴图是可选增强：缺失时全部回落到原来的字形/图元画法。
    // 这里只登记"要加载什么"，真正的加载在 _loadSprites() 里异步做，
    // 且**不阻塞启动** —— 图还没到就先按老画法渲染。
    this._spritesLoaded = false;

    this.state = STATE.TITLE;
    this.rng = new RNG(randomSeed());
    this.player = null;
    this.world = null;
    this.shop = null;

    this.waveIndex = 0;
    this.waveTimer = 0;
    this.waveDuration = 30;
    this.waveTimeLeft = 30;
    this.waveEnding = false;

    // 难度（标题/选人界面选择）
    this.difficultyId = DEFAULT_DIFFICULTY;
    this._lastWaveIncome = null;

    this.pendingLevelUps = 0;
    this.currentUpgradeOptions = [];

    this._lastTime = 0;
    this._rafId = null;
    this.running = false;
    this.paused = false;

    this.meta = this._loadMeta();
    // 第三十九轮：启动时恢复"玩家特效"透明度（暂停菜单可调，0~100）
    fxSettings.playerAlpha = (this.meta.fxAlpha ?? 100) / 100;

    // 监听窗口尺寸
    this._onResize = () => {
      this.renderer.resize();
      this.camera.resize(this.renderer.width, this.renderer.height);
    };
    window.addEventListener('resize', this._onResize);
    this._onResize();

    // 调试用
    window.__game = this;

    // 音频：浏览器要求用户手势后才能出声，这里先建上下文，首次点击/按键再 resume
    audio.init();
    this.audio = audio;              // 便于测试与调试
    this.audioReady = audio.ready;
    this.audioPlaying = () => audio.isRunning;

    // N.E.K.O 猫娘接入：独立运行时静默跳过，不影响游戏
    this.nekoReady = false;
    this._initNeko();

    // 贴图预加载：异步、不阻塞、失败静默回落。
    // 故意不 await —— 图还没到就先按老画法渲染，加载完 UI 会自己重绘。
    this._loadSprites();
  }

  /** 切换难度（只允许在开局前调用） */
  setDifficulty(id) {
    this.difficultyId = id;
    getDifficulty(id);   // 校验
    if (this.world) this.world.setDifficulty(id);
    // 商店也要知道难度：噩梦难度第 11 波起价格改成每波 +10%（第七轮）。
    // 放在这里而不是 new Shop() 旁边，是因为读档路径也会走 setDifficulty，
    // 一处接线两条路径都覆盖。
    if (this.shop) this.shop.difficultyId = id;
    return this.difficultyId;
  }

  // =========================================================
  // N.E.K.O 猫娘集成
  // =========================================================
  async _initNeko() {
    try {
      // 让 runtime 心跳能带上当前局势
      neko.setStateSnapshot(() => ({
        wave: this.waveIndex + 1,
        hpRatio: this.player && this.player.maxHp > 0 ? this.player.hp / this.player.maxHp : 1,
        level: this.player ? this.player.level : 1,
        kills: this.player ? this.player.kills : 0,
        difficulty: this.difficultyId,
        characterName: this.player ? (this.player.char?.name || '') : '',
      }));

      const ok = await neko.init();
      this.nekoReady = ok;
      if (ok) {
        this.ui.setNekoStatus(neko.status());
        // 挂载猫娘的 Live2D（宿主支持时）。
        // 第八轮：挂哪个模型由宿主接口解析（跟随当前猫娘，支持创意工坊），
        // 不再写死 yui 原皮。
        // 第九轮：独立模式（网页直连）在 _initStandalone 里已经挂好了，
        // 这里的 mountAvatar 只针对宿主模式。
        if (!neko.isStandalone) {
          const slot = document.getElementById('neko-avatar-slot');
          if (slot) await neko.mountAvatar(slot);
        }
        // 玩家在宿主里换猫娘/换衣服后，右下角跟着换
        neko.startAvatarWatch();
        this.ui.setNekoStatus(neko.status());
      } else {
        this.ui.setNekoStatus(neko.status());
      }
    } catch {
      this.nekoReady = false;
    }
    // 第一百二十四/二十六轮：官方猫娘桥**总是**探活 —— 插件面板里
    // standalone 形象挂成功后 nekoReady 会是 true，但对话/语音仍然只能
    // 走桥；非插件环境探活 404，桥保持禁用，零开销。
    pluginBridge.init()
      .then((on) => {
        if (on) {
          console.log('[neko-bridge] 插件面板环境 → 官方猫娘通道已启用（战况播报 + 台词 TTS）');
          this.ui.setNekoStatus(neko.status());
        }
      })
      .catch(() => { /* 探测失败：桥保持禁用 */ });
  }

  /**
   * 加载贴图。**任何情况下都不会抛错、不会阻塞游戏**。
   *
   * 图没生成全也不影响玩：缺哪张那张就继续用原来的字形/图元。
   * 所以这里刻意不 await 进启动关键路径 —— 慢的话让它慢慢加载，
   * 加载完再让 UI 重绘一次即可。
   */
  async _loadSprites() {
    try {
      const plan = buildPreloadPlan({
        weapons: WEAPONS,
        items: ITEMS,
        // 敌人贴图（第七轮）。
        //
        // **只预加载"确实有贴图"的那批**：Boss 从来没做过贴图（一直是程序画的
        // 几何星形），把它们塞进预加载列表只会每次开局都发两个必定 404 的请求 ——
        // 白占带宽，还会往控制台里灌错误，把真正的报错淹没掉
        // （browser-check 的"无控制台错误"就是被这两条 404 顶掉的）。
        //
        // 规则放在 data/enemies.js 的 getSpriteEnemies() 里，而不是写成
        // 内联的 filter —— 这样 assets-check 能直接调同一个函数来断言
        // "预加载列表里没有缺贴图的敌人"，不会出现测试和游戏各写一份规则。
        //
        // 兜底逻辑不变：万一某个敌人确实没有贴图，
        // sprites.get() 返回 null，渲染器照样回落到几何形状。
        enemies: getSpriteEnemies(),
        // 关卡背景（第七轮）。加载失败就保持纯色地面，不影响可玩性。
        backgrounds: BACKGROUNDS,
        // 掉落物贴图（第十轮血包；第十九轮补上经验宝石 + 双造型金币）。
        // 没有就回落到程序画的几何块 —— 这正是"贴图是可选增强"原则的用法。
        pickups: ['heal', 'xp', 'coin_1', 'coin_2'],
        // 地图宝箱贴图（第十九轮）：宝箱1 / 宝箱2 / 治疗宝箱。
        chests: ['chest_1', 'chest_2', 'chest_heal'],
        // 宠物贴图（第十一轮，用户要求把小小猫娘换成 小小猫娘.png）。
        // 同样可选：取不到就回落到程序画的几何小猫。
        pets: ['kitten'],
        // 特效贴图（第一百轮）：芙兰战背景大魔法阵 —— 原版 Extra 关
        // stg7enm sprite #86 的 720² 高清重制版。可选增强，失败就不画。
        fx: ['magic_circle_86'],
        // 角色立绘（第十一轮；第十三轮起 8 个角色全都有图）。
        // 仍然走 getSpriteCharacters() 而不是整个 CHARACTERS ——
        // 万一以后加了没有立绘的角色，这样不会每局发一堆必定 404。
        // 和敌人那边用同一个套路，让测试能读同一份规则。
        characters: getSpriteCharacters(),
      });
      const stats = await sprites.preload(plan);
      this._spritesLoaded = sprites.ready;
      // 加载完后重绘一次，让已经渲染过的界面（比如标题/商店）用上贴图
      if (this._spritesLoaded && this.ui) {
        try { this.ui.notifySpritesReady(); } catch { /* 忽略 */ }
      }
      return stats;
    } catch {
      this._spritesLoaded = false;
      return { loaded: 0, failed: 0 };
    }
  }

  /** 统一的吐槽入口：任何异常都不影响游戏 */
  _nekoComment(event, ctx = {}) {
    // ---- 第一百六十六轮：猫娘代打的自我认知 ----
    // pilot 接管期间她知道"现在是我在玩"，不再夸主人（杀怪/升级/
    // 合成的夸奖全部哑火），改为第一人称自评战况；输了求安慰，
    // 通关了邀功求夸。
    if (window.NekoAgent?.pilot?.on) {
      const remap = {
        [EVENT.RUN_START]: EVENT.PILOT_WAVE,
        [EVENT.WAVE_START]: EVENT.PILOT_WAVE,
        [EVENT.LEVEL_UP]: null,          // 升级是自己的事，不说话
        [EVENT.LOW_HP]: null,            // 自己的血自己扛（hpLow 事件通道另有）
        [EVENT.BOSS_SPAWN]: EVENT.PILOT_WAVE,
        [EVENT.BOSS_PHASE]: null,
        [EVENT.BOSS_KILL]: EVENT.PILOT_WAVE,
        [EVENT.WEAPON_COMBO]: null,      // 自己买的自己知道
        [EVENT.DEATH]: EVENT.PILOT_LOSE,
        [EVENT.WIN]: EVENT.PILOT_WIN,
        [EVENT.IDLE]: null,
      };
      const mapped = Object.prototype.hasOwnProperty.call(remap, event) ? remap[event] : event;
      if (!mapped) return;
      event = mapped;
    }
    // 第一百二十六轮：nekoReady 只代表"形象/状态可用"，不代表有对话能力。
    // 插件面板里 standalone 形象挂上后 nekoReady 也是 true —— 那时 SDK
    // 对话通道并不存在，必须继续走桥（第一百二十五轮修好后这成了
    // "形象有了、台词语音全没了"的元凶）。
    if (this.nekoReady && !neko.isStandalone) {
      neko.comment(event, ctx).catch(() => { /* 忽略 */ });
      return;
    }
    // 插件环境（SDK 没连上/独立模式）→ 走官方猫娘通道：
    // 同源 POST 插件 action → Python push_message(ai_behavior="respond")
    // → 官方猫娘在聊天界面带形象+语音播报战况。非插件环境桥是禁用的，零开销。
    this._pluginComment(event, ctx);
  }

  /** 插件环境限频播报（详细间隔由插件 Python 侧再兜一层） */
  _pluginComment(event, ctx = {}) {
    try {
      const text = pluginBridge.formatEvent(event, ctx);
      console.log(`[neko-bridge] 事件 ${event} →`, text);
      pluginBridge.report(event, text, 25);
    } catch { /* 永不影响游戏 */ }
  }

  /**
   * 第五十一轮：让猫娘计划的猫娘开口说**固定台词**（气泡 + 语音）。
   * 返回是否送达 —— 没连上宿主（网页直连模式）时 false，
   * 调用方自行回落到粉色 toast。
   * 第一百二十五轮：插件面板模式走官方 TTS（speak 通道照念台词），
   * 送达成功同样返回 true —— 调用方不再出粉色 toast，她本人来说。
   */
  _nekoSay(text, opts = {}) {
    // 第一百二十六轮：与 _nekoComment 同口径 —— standalone（含插件面板）
    // 即使形象挂上了也没有 SDK 对话通道，固定台词一律优先走桥（官方 TTS）。
    if (this.nekoReady && !neko.isStandalone) {
      try { return neko.sayFixed(text, opts); } catch { return false; }
    }
    try {
      if (pluginBridge.enabled) {
        neko.showBubble(text);   // 游戏内气泡照画（第一百二十八轮：桥模式不再"只有声音没有气泡"）
        pluginBridge.say(text);  // 语音走官方 TTS（无 CD，台词不再被吞）
        return true;
      }
    } catch { /* 永不影响游戏 */ }
    return false;
  }

  /** 组装给猫娘的当前局势描述 */
  _nekoContext(extra = {}) {
    const p = this.player;
    const diff = getDifficulty(this.difficultyId);
    if (!p) return { difficultyName: diff.name, ...extra };
    return {
      wave: this.waveIndex + 1,
      hpRatio: p.maxHp > 0 ? p.hp / p.maxHp : 1,
      level: p.level,
      kills: p.kills,
      characterName: p.def?.name || p.char?.name,
      weaponName: p.weapons[0]?.def?.name,
      difficultyName: diff.name,
      ...extra,
    };
  }

  // =========================================================
  // 存档 / 元数据
  // =========================================================
  _loadMeta() {
    // 体检修复（第九十二轮）：按字段逐个合并默认值。旧 meta（或手改 meta）
    // 缺字段时，_endRun 的 += / Math.max 会算出 NaN 并写回存档
    // （表现为统计偶尔清零一局）。
    const DEFAULTS = { unlocked: [], bestWave: 0, totalKills: 0, runs: 0, wins: 0, retries: 0, manualRetries: 0, flandreUnlocked: false };
    try {
      const raw = localStorage.getItem(META_KEY) || localStorage.getItem(LEGACY_META_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
          return { ...DEFAULTS, ...parsed,
            bestWave: Number(parsed.bestWave) || 0,
            totalKills: Number(parsed.totalKills) || 0,
            runs: Number(parsed.runs) || 0,
            wins: Number(parsed.wins) || 0,
            retries: Number(parsed.retries) || 0,
            manualRetries: Number(parsed.manualRetries) || 0,
            unlocked: Array.isArray(parsed.unlocked) ? parsed.unlocked : [],
          };
        }
      }
    } catch (e) { /* 忽略 */ }
    return { ...DEFAULTS };
  }

  _saveMeta() {
    try {
      localStorage.setItem(META_KEY, JSON.stringify(this.meta));
    } catch (e) { /* 忽略 */ }
  }

  /**
   * 按波次解锁角色。
   *
   * ⚠️ 第十七轮：用户要求"把游戏按顺序解锁角色的设定删了，默认 8 个角色全开"。
   * 所以这里**永远返回空数组** —— 不再有人因为打到第 N 波而"解锁"，
   * 因为所有人一开始就都在。
   *
   * 保留函数（而不是删掉调用点）是为了：
   *   - 老存档里 meta.unlocked 已经攒了一堆 id，读档路径完全不用改；
   *   - _endWave 里那句 `for (const c of newly) toast(...)` 自然变成空转，
   *     不会因为 newly 是 undefined 而炸；
   *   - 以后真要加别的门槛，入口还在。
   *
   * @returns {object[]} 永远是空数组
   */
  _unlockByWave(_waveReached) {
    return [];
  }

  // =========================================================
  // 生命周期
  // =========================================================
  start() {
    if (this.running) return;
    this.running = true;
    this._lastTime = performance.now();
    this.showTitle();
    this._loop(this._lastTime);
    // 第一百四十轮：?vergil=1 直达刀光实验室
    if (new URLSearchParams(location.search).has('vergil')) this._enterVergilLab();
  }

  stop() {
    this.running = false;
    if (this._rafId) cancelAnimationFrame(this._rafId);
    this._rafId = null;
  }

  showTitle() {
    this.state = STATE.TITLE;
    this.ui.showHud(false);
    // 标题/菜单曲（用户要求："open是打开游戏的曲子"）
    audio.playScene('open');
    this.ui.showTitle({
      seed: this.rng.seed,
      hasSave: !!this._readSave(),
      difficulty: this.difficultyId,
      onStart: () => this.showCharSelect(),
      onContinue: () => this.loadGame(),
      onSetDifficulty: (id) => { this.setDifficulty(id); this.showTitle(); },
      onToggleAudio: () => {
        const on = audio.toggle();
        this.ui.toast(on ? '音效已开启' : '音效已关闭', on ? 'good' : 'bad');
        this.showTitle();
      },
      // 第十四轮 ⑥：开始菜单也能关游戏
      onExit: () => this._exitGame(),
      // 第一百一十七轮：直通芙兰战按钮改名「直接挑战芙兰！」并**默认
      // 隐藏** —— 用户："把这个按钮默认隐藏，在玩家成功击败过一次EP
      // 难度芙兰之后永久开启。" EP（Extra Phantom）存活 60 秒拿到
      // outcome 'beat' 时置 meta.flandreUnlocked（localStorage 永久）。
      // 第一百八十五轮（用户："把主菜单的测试双猫boss战按钮换成芙兰按钮吧，我可以直接进入测试"）：
      // 芙兰按钮**常显**、不再要求击败过一次 EP；直通 4P 测试局（P1 自己操作，P2~P4 AI）。
      // 万圣节双猫的标题页按钮下线（传 null 隐藏）；?halloween=1 直达口子保留，测试/调参不受影响。
      onFlandreTest: () => this._startFlandreTest(),
      onHalloweenTest: null,
      // 第一百四十轮：椅子维吉尔刀光实验室（临时挑战按钮 + ?vergil=1 直达）
      // 第一百五十五轮：测试按钮下线（用户："测试完毕，很不错，把测试
      // 大排档椅子的按钮隐藏，ui布局看一下不难看就行"）—— 传 null 隐藏
      // （同芙兰直通按钮的约定，ui.showTitle 对 falsy 整个不渲染按钮）。
      // 测试入口保留 ?vergil=1 直达；_enterVergilLab 本体保留不删。
      onVergilLab: null,
      // 第六十五轮的 onSeCheck（音效自检）随标题页按钮一并下线（第一百二十二轮）
      audioEnabled: audio.enabled,
    });
  }

  showCharSelect() {
    this.state = STATE.CHAR_SELECT;
    const chars = availableCharacters(this.meta.unlocked);
    // 选人界面继续用标题曲。这里显式再要一次 'open' 是为了覆盖
    // "结算界面 → 再来一局" 这条路径（否则会一直放着 end 曲）。
    // 曲池只有一首 + playScene 的同场景去重，所以 title↔charselect
    // 来回切不会把音乐掐了重放。
    audio.playScene('open');
    this.ui.showCharacterSelect({
      characters: chars,
      difficulty: this.difficultyId,
      onConfirm: (id) => {
        // 第八十三轮："开始游戏" —— 玩家点「用这个角色开始」播一次
        //（用户自制音效 start00；无限模式继续挑战不经这里，不出声）
        audio.play('gameStart');
        this.startRun(id);
      },
      onBack: () => this.showTitle(),
      onSetDifficulty: (id) => { this.setDifficulty(id); this.showCharSelect(); },
      // 第一百六十六轮：「猫娘帮你玩」—— 用当前选中的角色+难度开一局，
      // 并把方向盘交给 NekoPilot（接管语音由 pilot.start 自己播）
      onNekoPlay: (id) => {
        audio.play('gameStart');
        window.__duo = false;
        this.__duo = false;   // 184 轮：game 实例同步（pilot 救援分支读它）
        this.startRun(id);
        window.NekoAgent?.pilot?.start(this, { characterId: id });
      },

      // ---- 第一百七十一轮：「双猫娘同游」（2P 实验）——
      // P1 = 玩家操控（当前选中角色），P2 = AI 猫娘（**从剩下 7 个角色
      // 随机**，不和玩家撞角色）。经验共享、金币独立、最近仇恨、全队
      // 倒地判负、倒地救援（黑夜君临环）。duo 下 AI 猫娘无 buff。
      // ---- 第一百八十四轮：泛化 _startParty(size) —— 1~4P 一个入口。
      onDuoPlay: (id) => {
        audio.play('gameStart');
        window.__duo = true;
        this.__duo = true;   // 184 轮：game 实例同步（pilot 救援分支读它）
        this.startRun(id, { party: 2 });
        this._bindPartyPilots(2);
        const nmB = CHARACTER_MAP[this.world.player2?.characterId]?.name ?? '';
        this._nekoSay?.(` duo 开局！${nmB} 也来帮忙了喵～`);
      },

      // ---- 第一百八十四轮：1~4P 开局 —— P1 真人 + size-1 个 AI 猫娘
      //（都走 agentMoveTarget，不抢键盘）。
      onPartyPlay: (id, size) => {
        audio.play('gameStart');
        window.__duo = true;
        this.__duo = true;   // 184 轮：game 实例同步
        this.startRun(id, { party: size });
        this._bindPartyPilots(size);
        const names = this.world.partners()
          .map((pl) => CHARACTER_MAP[pl.characterId]?.name ?? pl.characterId);
        this._nekoSay?.(`${size} 人小队开局！猫娘阵容：${names.join('、')}，看我们的喵～`);
      },

      // ---- 第一百七十一轮：「看2个猫娘手忙脚乱」→ 184 轮「全员猫娘」——
      // 1P 和所有队友 **都是** AI 猫娘（随机不撞角色），玩家纯围观。
      // 暂停菜单的"让猫娘玩"开关依然可用（关掉 1P 大脑就能接管）。
      onDuoWatch: () => {
        audio.play('gameStart');
        window.__duo = true;
        this.__duo = true;   // 184 轮：game 实例同步
        const ids = CHARACTERS.map((c) => c.id);
        const pick = (excl) => {
          const pool = ids.filter((x) => !excl.includes(x));
          return pool[Math.floor(Math.random() * pool.length)];
        };
        const a = pick([]);
        const b = pick([a]);
        this.startRun(a, { duo: true, partnerId: b });
        // 1P 也交给 AI：adopt 不重开局，直接接管当前局面
        window.NekoAgent?.pilot?.adopt(this);
        this._bindPartyPilots(2);
        const nmA = CHARACTER_MAP[a]?.name ?? a;
        const nmB = CHARACTER_MAP[b]?.name ?? b;
        this._nekoSay?.(`两只猫娘上场：${nmA} 和 ${nmB}，看戏吧喵～`);
      },
    });
  }

  /** 第一百八十四轮：把 1~size 号队友的大脑绑到各自的实体上
   * （startRun/retryWave/开局入口共用；pilots[i] ↔ partners()[i]）。
   * —— 无条件 start：pilot3/4 是全新实例 on=false，旧 2P 的"已在驱动
   * 才重绑"判断会让它们永远绑不上（用户实测"3p/4p 从来没动过"）。 */
  _bindPartyPilots(size) {
    const fresh = this.world.partners();
    const pilots = window.NekoAgent?.pilots;
    if (Array.isArray(pilots)) {
      pilots.forEach((pt, i) => {
        const np = fresh[i];
        if (np && pt) pt.start(this, { entity: np });
      });
    } else if (window.NekoAgent?.pilot2 && fresh[0]) {
      window.NekoAgent.pilot2.start(this, { entity: fresh[0] });
    }
  }

  // =========================================================
  // 开局
  // =========================================================
  /** 第一百七十一轮：创建队友（startRun party 与 retryWave 共用）。
   *  partnerId 缺省 = 从除已选角色之外的池子里随机（不撞角色）。
   *  第一百八十四轮：slot 2/3/4 —— 落到 world.player2/3/4；出生点按
   *  槽位错开，避免四人叠在同一坐标。 */
  _spawnPartner(characterId, partnerId, slot = 2) {
    const ids = CHARACTERS.map((c) => c.id);
    const taken = [this.player.characterId, ...this.world.partners().map((p) => p.characterId)];
    if (!partnerId || taken.includes(partnerId)) {
      const pool = ids.filter((x) => !taken.includes(x));
      partnerId = pool[Math.floor(Math.random() * pool.length)];
    }
    const p2 = new Player(partnerId, this.rng);
    p2.isP2 = true;
    this.player.duoMode = true;
    p2.duoMode = true;
    p2.world = this.world;
    const offs = [[60, 40], [-60, 40], [60, -40]][Math.min(3, slot) - 2] ?? [60, 40];
    p2.x = this.player.x + offs[0];
    p2.y = this.player.y + offs[1];
    if (slot === 3) this.world.player3 = p2;
    else if (slot === 4) this.world.player4 = p2;
    else this.world.player2 = p2;
    return p2;
  }

  startRun(characterId, opts = {}) {
    this.rng = new RNG(randomSeed());
    this.player = new Player(characterId, this.rng);
    this.world = new World(this.rng, this.camera);
    this.world.setDifficulty(this.difficultyId);
    // ---- 第一百七十轮：2P 猫娘队友（双猫娘同游实验）----
    // opts.duo = true 时创建第二个真玩家实体（另一个角色），两人都打
    // duoMode 标：死亡一律转倒地等队友救援，都倒了才判负。
    this.world.player2 = null;
    this.world.player3 = null;
    this.world.player4 = null;
    // ---- 第一百八十四轮：1~4P —— opts.party = 2/3/4（兼容旧 duo=true）。
    // P1 永远是玩家；队友全部由 AI 猫娘驱动（agentMoveTarget，不抢键盘）。
    if (opts.duo) this._spawnPartner(characterId, opts.partnerId, 2);
    const party = Math.max(opts.duo ? 2 : 1, Math.min(4, opts.party ?? 1));
    for (let s = opts.duo ? 3 : 2; s <= party; s++) {
      this._spawnPartner(characterId, null, s);
    }
    this._partySize = party;
    this._duoEnabled = party >= 2 || !!opts.duo;
    // ---- 万圣节战实测纠偏（用户："ai猫娘的倒地系统疑似失效"）：pilot 的救援分支读
    // `globalThis.__duo || g.__duo`，而这两个标志此前只在标题菜单的 2P/3P/4P 按钮和
    // ?party= 里置位 —— 测试按钮 / 任何直接 startRun({party}) 的入口里它们是 undefined，
    // 救援 AI 整段被短路。统一在 startRun 里按人数置位，入口再多也不会漏。
    window.__duo = this._duoEnabled;
    this.__duo = this._duoEnabled;
    // 第一百零八轮：天气系统 —— 随机判定走独立流（rng.fork），主随机
    // 流一个字不动；实体通过 world.weather / player.world 读旗标。
    this.weather = new WeatherSystem(this.rng);
    this.world.weather = this.weather;
    // 第一百八十轮：天彗龙乱入状态机（跨局持久，resetRun 清"全局一次"标记）
    this.comet = this.comet || new CometDisaster();
    this.comet.resetRun();
    this.world.comet = this.comet;
    this.player.world = this.world;
    this.shop = new Shop(this.rng);
    this.shop.difficultyId = this.difficultyId;   // 噩梦分段涨价（第七轮）
    // 第四十七轮：角色专属刷新费倍率（悠怡萌妹 0.5，其余 1）
    this.shop.rerollCostMul = this.player.def?.rerollCostMul ?? 1;
    this.camera.snapTo(0, 0);

    // Boss 击杀 -> 猫娘欢呼
    this._bindBossEvents();
    this._bindWorldAudio();
    this._bindPlayerSkillEvents();

    // 跟随宠物：小小猫娘（默认无敌，跨波保留）
    this._spawnPet();

    this.waveIndex = 0;
    this.waveEnding = false;
    this.pendingLevelUps = 0;
    this._bossKillPending = false;
    this._lastHpRatio = 1;
    this._shopDeferred = false;   // 体检修复：新局清掉"商店被升级打断"旗子
    // 第一百四十一轮：维吉尔触发标志（每局一次）+ 阎魔刀次元斩状态复位
    this._vergilFought = false;
    this.playerUlt = null;
    this.playerUltWave = -1;
    if (this.world) this.world.playerUlt = null;
    // 第八轮：重试计数只在本局内累计（meta.retries 原来是跨局总数）。
    // 必须在 _beginWave 之前清零 —— 那一波会顺手拍快照。
    this.waveRetries = 0;
    // 第四十一轮：Q 的"本局升级全自动"在新局自动关闭
    //（用户："按Q这回合所有升级都自动，下回合自动停止。"）
    this.autoLevelAll = false;
    // 第三十四轮：重试计数在回到第 1 波时清零 —— 用户："重试次数计数
    // 设置为玩家如果回到第一波就清零，而不是一直继承。" meta.retries
    // 原来永久累计（跨局继承），语义改为"本局（自第 1 波起）累计"，
    // 开新一局就归零重新数。
    this.meta.retries = 0;
    this.meta.manualRetries = 0;   // 185 轮：此前漏了清零，跨局永久累计（芙兰"0 重开却见不到"的根因）
    this._saveMeta();
    // 第十一轮：上一波用过的背景 id，用来保证相邻两波不重复。
    // 开局清零 —— 每局第 1 波都应该能随便抽（不该被上一局的最后一波限制）。
    this._prevBackgroundId = null;
    // 无限模式：第 20 波通关后询问玩家是否继续（见 _askEndless）
    this.endlessEnabled = false;
    // 第十一轮：抽这一局的 Boss 版本（第 10/20 波远程 or 近战）。
    // **必须在 _beginWave 之前**抽好 —— 第 1 波虽然不出 Boss，
    // 但快照是在 _beginWave 里拍的，抽签要进得去快照。
    this._rollBossChoices();
    this._beginWave(0);

    this.ui.clearOverlay();
    this.ui.showHud(true);
    this.state = STATE.PLAYING;
    this._clearSave();

    // BGM 开始（首次点击后音频上下文已解锁）。
    // 第 1 波由 _beginWave(0) 在下面接线，这里不重复点歌。
    audio.unlock();

    // 猫娘开场打气
    this._nekoComment(EVENT.RUN_START, {
      characterName: this.player.char?.name || this.player.def?.name,
      wave: 1,
      level: 1,
      kills: 0,
      hpRatio: 1,
    });
  }

  /**
   * 第十一轮：抽第 10 / 20 波的 Boss 版本（远程 or 近战，各 50%）。
   *
   * 用户原话："boss在10/20回合随机50%概率出现，即近战远程boss各50%"
   *
   * 三个设计决定，都是为了不让"随机"变成"可以刷"：
   *
   * 1) **在开局时一次抽完全部**，而不是每波临到才抽。
   *    这样抽签结果天然进得了开局快照，重试本波不会换 Boss。
   *
   * 2) **抽签结果记进存档**（见 _serializeRun 的 bossChoices）。
   *    否则玩家想要近战 Boss 就反复重试，50% 会退化成 100%。
   *
   * 3) **按"波次"抽而不是"按 Boss"抽** —— 保证第 10 波不会抽出
   *    第 20 波那个 2400 血的 Boss（那会让第 10 波直接变成坏档）。
   *    见 data/enemies.js 的 BOSS_VARIANTS。
   */
  _rollBossChoices() {
    const choices = {};
    for (const wave1based of Object.keys(BOSS_VARIANTS)) {
      const ranged = BOSS_VARIANTS[wave1based].ranged;
      const picked = pickBossForWave(Number(wave1based), this.rng);
      // 只记"和默认不一样"的 —— 抽中远程时不必存，
      // 省得存档里塞一堆恒等映射，也让老存档的语义更清楚。
      if (picked && picked !== ranged) choices[ranged] = picked;
    }
    this.world.bossChoices = choices;
    return choices;
  }

  _beginWave(index) {

    this.waveIndex = index;
    // 第一百八十轮：天彗龙乱入判定 —— 第 6 波起 10%、每波 +5%，
    // 每回合判一次、全局一局一次；命中后 5 秒前摇进演出。
    //（测试按钮 forceTrigger 走同一 pending 通道。）
    this.comet?.roll?.(index + 1, this.rng);
    // 第一百六十五轮：战利品防洪闸 —— bug 修复前 boss 结算卡死，玩家被迫
    // 反复"继续游戏"重打第 10 波，每波 1~5 件宝箱战利品只进不出，商店里
    // 堆出"慢慢一页"（用户图6）。现在每波开始时最多保留 12 件（10 件正常
    // 领取余量），最旧的自动折价换成材料，toast 说明去向。
    {
      const pend = this.world?.pendingItemRewards;
      if (Array.isArray(pend) && pend.length > 12) {
        const sold = pend.splice(0, pend.length - 12);
        const mats = sold.reduce((s, r) => s + (Number(r?.sellValue) || 4), 0);
        if (this.player && mats > 0) this.player.materials += mats;
        this.ui?.toast?.(`战利品栏堆了太多！${sold.length} 件旧战利品自动折价 +${mats} 材料`, '', 5000);
      }
    }
    this.world.startWave(index);
    // 第一百五十三轮：无限模式标志同步给 world —— 变异体概率判定用
    //（普通 10% / 无限 20%）。每波同步一次，读档/当场开启都自然生效。
    this.world.endless = this.endlessEnabled;
    // 第二十一轮：特殊波次提示（冲锋/弹幕/肉盾；11 波起 20%×3 类）
    // 第一百六十轮：停留 2.6s → 8s —— 用户："特殊波次播报框停留时间太短了，
    // 改到持续8秒吧，有时候看不到字就没了。"
    if (this.world.specialWave) {
      const pool = SPECIAL_WAVE_POOLS[this.world.specialWave];
      if (pool) this.ui?.toast(`特殊波次：${pool.label}`, 'bad', 8000);
    }
    this.waveDuration = this.world.waveDuration;
    this.waveTimer = 0;
    this.waveTimeLeft = this.waveDuration;
    this.waveEnding = false;
    // 第四十八轮：Boss 关到时放行的"弃奖"标记，每波重置
    this._bossForfeit = false;

    // 第十六轮：角色技能的"每回合"状态重置。
    // 悠怡的「猫的步伐」是"每回合限1次"，库洛的毒舌判定表、
    // Neon（原"neon宴会"）的炮台叠层也都该跟着新一波清空 —— 上一波的敌人已经没了，
    // 留着那些 id 只会让 Map 越滚越大。
    // 第一百八十四轮：**duo 全员**都要重置 —— 之前只重置 P1，2P 悠怡的
    // lethalUsedThisRound 卡在 1，锁血一次之后每个新回合都直接死
    //（用户实测："2p的悠怡的猫的步伐不会刷新，只生效过一次之后的新
    // 回合都是直接死"）；双死重开时 P2 重建实例才"看起来好了"。
    for (const pl of (this.world.players?.() ?? [this.player])) {
      pl?.charRt?.onRoundStart?.();
    }

    // 本波是不是 Boss 波。
    //
    // ⚠️ 这里必须**先**算出来（startWave 已经把 waveConfig 填好了），
    // 因为选背景要用它 —— 用户要求"boss 场景不使用这两张有复杂地形的图"。
    // 原来的代码把这段放在选背景**之后**（为了在点歌前拿到 bossIds），
    // 那样选背景时还不知道是不是 Boss 波，只能先选一张再补救，
    // 会白白消耗掉"相邻两波不同图"的一次机会。
    const cfg = this.world.waveConfig;
    const bossIds = cfg?.bosses?.length ? cfg.bosses : (cfg?.boss ? [cfg.boss] : []);
    const isBossWave = bossIds.length > 0;

    // 关卡背景随机替换（第七轮引入，第十一轮改成 5 张随机池）。
    //
    // 把**上一波用的 id 传进去**，让 backgroundForWave 保证相邻两波不同图。
    // 这一条不能靠调用方事后检查 —— 状态在 renderer 里，而 _beginWave
    // 有可能在 renderer 还没建好时被调用（开局第 0 波），
    // 所以这里用 `this._prevBackgroundId` 单独记一份，而不是读 renderer。
    //
    // 贴图没加载到也没关系：渲染器回落纯色 + 网格。
    //
    // 第十二轮：Boss 波要**避开有复杂地形的地图**（用户要求）。
    // 做法是在抽到的图撞上黑名单时，顺着 backgroundForWave 的排列
    // 往后找一张可用的 —— 复用它的"下一张"逻辑，而不是自己再随机一次，
    // 这样仍然保持"同一波次永远给同一张图"的可复现性。
    if (this.renderer) {
      // 185 轮：Boss 波选图抽成 backgroundForBossWave（窗口放宽 + 确定性兜底，见 backgrounds.js）
      const bg = isBossWave
        ? backgroundForBossWave(index, this._prevBackgroundId ?? null, BOSS_BANNED_BACKGROUNDS)
        : backgroundForWave(index, this._prevBackgroundId ?? null);
      if (bg) {
        this.renderer.backgroundId = bg;
        this._prevBackgroundId = bg;
        // 地形必须和背景同步换：障碍物在画面上就是这张图里的深色方块/花坛。
        // 换图不换地形 = 撞空气墙；换地形不换图 = 能穿墙。
        this.world.setTerrain(bg);
        // 198 轮：雪原的「冰晶迷宫」深处每波放一只宝箱 —— 走进去的理由（用户："1个入口1个出口里面各种窄路选择的感觉"）。
        // 落点是 BFS 离入口最远的格子中心（见 data/maze.js），保证在通道里、够得着；普通宝箱，不是稀有箱。
        const mz = this.world.terrain?.maze;
        if (mz) {
          try {
            this.world.spawnChest(this.player, { x: mz.chest.x, y: mz.chest.y });
            this.renderer?.warmMaze?.(this.world.terrain);
          } catch { /* 宝箱失败不影响开波 */ }
        }
      }
    }

    // 宠物跨波保留，但每波开局拉回玩家身边，避免上一波跑太远
    if (this.pet) {
      this.pet.x = this.player.x + 40;
      this.pet.y = this.player.y + 20;
      this.pet.vx = 0;
      this.pet.vy = 0;
    }
    // 注意：这里不要清商店锁定。
    // 锁的语义是「保留到下一家商店」，由 Shop.roll() 在开店时消费。
    // 在这里清会导致"锁了等于没锁"。

    // 本波是不是 Boss 波：已在上面（选背景之前）算好，见那里的说明。
    // bossIds / isBossWave 就是那两个变量。

    // BGM 按场景切（用户要求："1-10相关名字的曲子是1-10回合的曲子，
    // 11-20是之后的曲子……boss名字就是boss回合曲子"，且每类不止一首、
    // "改为随机播放其中一首，无尽模式下则随机播放1-20关卡的曲子"）。
    // 放在 _beginWave 里，是因为**所有**进波次的路径（开局 / 商店下一波 /
    // 重试本波 / 读档）都走这里，一处接线四条路径都覆盖。
    // wave 传 1-based（和第几波的说法一致），内部按 1-10 / 11-20 / 21+ 分池。
    if (isBossWave) {
      audio.playScene('boss');
    } else {
      audio.playScene('stage', { wave: index + 1 });
    }

    // 新一波提示（第 1 波已由开局台词覆盖）
    if (index > 0) {
      audio.play('waveStart');
      if (isBossWave) {
        audio.play('bossSpawn');
        // 多个 Boss 时把名字念出来，让玩家知道要打几只
        const names = bossIds.map((id) => getEnemy(id)?.name || id).join('、');
        this._nekoComment(EVENT.BOSS_SPAWN, this._nekoContext({
          bossName: bossIds.length > 1 ? `${names}（共 ${bossIds.length} 只）` : names,
        }));
      } else {
        this._nekoComment(EVENT.WAVE_START, this._nekoContext());
      }
    }

    // 第八轮：拍下"这一波刚开局"的快照，供死亡后「重试本波」恢复。
    // 放在最后拍，确保拍的是完全初始化好的状态。
    this._snapshotWave();
  }

  // =========================================================
  // 主循环
  // =========================================================
  _loop = (now) => {
    if (!this.running) return;
    this._rafId = requestAnimationFrame(this._loop);

    // 第一百六十三轮：移动端限帧 30fps（用户死命令："直接把游戏主循环
    // 限制在 30 帧（30fps）。2D 游戏 30 帧完全可玩，但能救 50% 的性能"）。
    // 间隔不足 33ms 的帧直接跳过 —— 不更新、不渲染、不推进 _lastTime，
    // 下一次真正执行的帧用完整间隔算 dt（>100ms 的部分仍被钳到 0.1）。
    if (MOBILE && now - this._lastTime < 32) return;

    let dt = (now - this._lastTime) / 1000;
    this._lastTime = now;
    // 防止切后台回来时时间跳变
    if (dt > 0.1) dt = 0.1;
    if (dt <= 0) dt = 1 / 60;

    this._update(dt);
    this._render();

    this.input.endFrame();
  };

  _update(dt) {
    // 第一百六十四轮：NekoAgent 事件钩子（hp<30% / lv≥10 → 全局事件，
    // 宿主插件监听 'neko-agent-event' 触发 TTS 等）。_tick 内部全吞错。
    window.NekoAgent?._tick?.(this);

    // M 键静音（任何状态下都能用）
    if (this.input.pressed('KeyM')) {
      const on = audio.toggle();
      this.ui.toast(on ? '音效已开启' : '音效已关闭', on ? 'good' : 'bad');
    }

    // ---- 第四十一轮：升级快捷键 ----
    // 用户原话："增加一个等级提升的按键设置并且写在升级选项下面，就是
    // 原来的稍后再选按钮这边。玩家可以按E自动升级一次，按Q这回合所有
    // 升级都自动，下回合自动停止。自动升级效果为：系统自动选择一个提升
    // 给玩家，不用玩家自己选了，如果有高品质的选项优先选择高品质，如果
    // 都是同品质则随机一个选择。"
    // E：替玩家自动选当前这一次；Q：切换"本局全自动"（新局自动关闭）。
    if (this.state === STATE.LEVEL_UP && this.currentUpgradeOptions?.length) {
      if (this.input.pressed('KeyE')) {
        this._applyUpgradePick(this._autoPickOption(this.currentUpgradeOptions));
      } else if (this.input.pressed('KeyQ')) {
        this._toggleAutoLevel();
      }
    } else if (this.state === STATE.PLAYING && this.input.pressed('KeyQ')) {
      this._toggleAutoLevel();   // 游戏中也能预开（下次升级起全自动）
    }

    switch (this.state) {
      case STATE.PLAYING: this._updatePlaying(dt); break;
      // 第四十九轮：芙兰朵露战 —— 世界照推，波次/刷怪停摆，战斗由控制器接管
      case STATE.FLANDRE: this._updateFlandre(dt); break;
      // 第一百四十一轮：大排档椅子·维吉尔 Boss 战（同款骨架 + 大招全场 timeScale）
      case STATE.VERGIL: this._updateVergil(dt); break;
      // 第一百八十四轮：万圣节双猫（巧克力&香草）纯演出生存战
      case STATE.HALLOWEEN: this._updateHalloween(dt); break;
        case STATE.REMILIA: this._updateRemilia(dt); break;
      case STATE.PAUSED:
        if (this.input.pressed('Escape')) this._resume();
        break;
      case STATE.TITLE:
      case STATE.CHAR_SELECT:
      case STATE.RESULT:
        // 这些状态下游戏世界冻结，只响应 UI
        break;
      case STATE.LEVEL_UP:
        // 等待 UI 选择
        break;
      case STATE.SHOP:
        // 等待 UI 选择；201 轮：货架被界面之外的动作改动时让商店界面跟着重绘
        this.ui.tickShop?.();
        break;
    }
  }

  _updatePlaying(dt) {
    // ESC 暂停
    if (this.input.pressed('Escape')) {
      this._pause();
      return;
    }

    const p = this.player;

    // 第一百一十六轮：左键手动瞄准 —— 用户："玩家按住左键的时候，所有
    // 武器的攻击方向都瞄准左键的落点。方便玩家在高难时候主动调整攻击
    // 目标，松开左键恢复自己索敌的设计。"
    // 把鼠标屏幕坐标换算成**世界坐标**存到 p.aimAt，weapon._fire 统一
    // 消费（所有弹道/近战弧/光束的 facing 都从这一个口出）。
    // 换算：世界层渲染只做 translate(W/2 - cx, H/2 - cy)，无缩放；
    // input.mouse 是 canvas 背景像素（×dpr），先除回 dpr 得 CSS 像素。
    // 松开左键 → aimAt = null，自动回到"索敌优先、没有目标朝正面"。
    if (this.input.mouse.down) {
      const dpr = this.renderer.dpr || 1;
      const mx = this.input.mouse.x / dpr;
      const my = this.input.mouse.y / dpr;
      p.aimAt = {
        x: mx - this.renderer.width / 2 + this.camera.cx,
        y: my - this.renderer.height / 2 + this.camera.cy,
      };
    } else {
      p.aimAt = null;
    }

    // ---- 第一百八十轮：天彗龙乱入 ----
    // 状态机先跑：roar/boom 眩晕期间冻结世界（dt=0 给 world.update、
    // 波次计时暂停、updateSpawning 有 spawnPaused 兜底），玩家照常可动。
    const cometFreeze = this.comet.update(dt, this);

    // 波次计时（天彗龙演出期间暂停 —— 演出时间不算波时）
    // 第一百八十二轮：眩晕改"局部冻结"——世界照常跑（弹幕/掉落飞），
    // 只把敌我双方个体 dt=0（world.enemyFrozen + 玩家 stunDt）。
    if (!cometFreeze) {
      this.waveTimer += dt;
      this.waveTimeLeft = Math.max(0, this.waveDuration - this.waveTimer);
    }
    this.world.spawnPaused = !!cometFreeze;
    this.world.enemyFrozen = !!cometFreeze;

    // 补充本帧的武器音配额（第十轮）。
    // 必须在 p.update() **之前** —— 武器是在玩家更新里开火的，
    // 配额要在这之前就位，否则本帧的武器音全被挡掉。
    audio.beginFrame();

    // ---- 第一百四十一轮：阎魔刀 · 次元斩 ----
    // 生命低于 50% 时自动释放，每回合限一次；时间变慢只作用于怪物
    //（世界 dt×0.05），玩家照常速拔刀。斩击只伤敌人：每连每怪 500 真伤。
    if (this.playerUlt) {
      // 已在次元斩中：推进演出（真实秒）
      const pu = this.playerUlt;
      pu.t += dt;
      const CHARGE = 0.3, GAP = 0.18, COMBOS = 10, LIFE = 0.2;
      if (pu.t >= CHARGE && pu.launched < COMBOS) {
        pu.acc = (pu.acc ?? 0) + dt;
        while (pu.acc >= GAP && pu.launched < COMBOS) {
          pu.acc -= GAP;
          pu.launched++;
          const cam = this.camera;
          const vw = cam.viewW, vh = cam.viewH;
          const half = Math.hypot(vw, vh) * 0.62;
          // 第一百五十轮：**屏内每个敌人一道瞄准刀光 + 500 真伤**——
          // 用户："最好阎魔刀技能触发的时候屏幕上的每个敌人都能被砍个
          // 七八刀起步"——10 连 = 每怪保底 10 刀。敌人多时刀光封顶
          // 24 道/连（超出只结算伤害，防 100+ 怪时刀光数组爆炸）。
          // 敌人侧确认：Enemy.takeDamage **没有** 0.2s 受击无敌帧
          //（0.2s invuln 是玩家侧机制）——每刀都实打实掉血。
          let aimed = 0;
          const foes = this.world.enemies.items.filter((e) => !e.dead
            && Math.abs(e.x - cam.cx) < vw && Math.abs(e.y - cam.cy) < vh);
          for (const e of foes) {
            e.takeDamage(500, { pierceArmor: true });
            if (aimed < 24) {
              aimed++;
              const a2 = Math.random() * Math.PI * 2;
              pu.blades.push({
                x1: e.x - Math.cos(a2) * half, y1: e.y - Math.sin(a2) * half,
                x2: e.x + Math.cos(a2) * half, y2: e.y + Math.sin(a2) * half,
                seed: (Math.random() * 1e9) | 0, t: 0, life: LIFE,
              });
            }
          }
          // 装饰刀光：满屏随机 4 道（演出密度）
          for (let i = 0; i < 4; i++) {
            const a = Math.random() * Math.PI * 2;
            const ox = cam.cx + (Math.random() - 0.5) * vw;
            const oy = cam.cy + (Math.random() - 0.5) * vh;
            pu.blades.push({
              x1: ox - Math.cos(a) * half, y1: oy - Math.sin(a) * half,
              x2: ox + Math.cos(a) * half, y2: oy + Math.sin(a) * half,
              seed: (Math.random() * 1e9) | 0, t: 0, life: LIFE,
            });
          }
        }
      }
      for (let i = pu.blades.length - 1; i >= 0; i--) {
        const b = pu.blades[i];
        b.t += dt;
        if (b.t >= b.life) pu.blades.splice(i, 1);
      }
      if (pu.t >= CHARGE + COMBOS * GAP + 0.4) this.playerUlt = null;
      this.world.playerUlt = this.playerUlt;   // 渲染层读这个画玩家刀光
    } else if (this.world.playerUlt) {
      this.world.playerUlt = null;
    } else if (
      // ---- 第一百八十四轮：阎魔刀对 2P 也生效 ----
      // 用户："如果猫的步伐不刷新，是不是阎魔刀也不刷新？刷新效果不在
      // 2p角色身上生效？"—— 触发与每波限一次标志都只认 P1。现在 P2
      // 自己的 yamato 自己判（p2UltWave 独立计数），演出复用同一段。
      (
        (p.hasItem?.('yamato') && !p.dead
          && p.hp < p.stats.get('maxHp') * 0.5
          && this.playerUltWave !== this.waveIndex)
        || this.world.partners?.().some((q) => !q.dead && !q.downed
          && q.hasItem?.('yamato')
          && q.hp < q.stats.get('maxHp') * 0.5
          && !((this._mateUltWaves ??= {})[q === this.world.player2 ? 2 : q === this.world.player3 ? 3 : 4] === this.waveIndex))
      )
      && this.world.enemies.items.some((e) => !e.dead)
    ) {
      if (p.hasItem?.('yamato') && !p.dead && p.hp < p.stats.get('maxHp') * 0.5) {
        this.playerUltWave = this.waveIndex;     // 每回合限一次
      } else {
        // 队友触发：各自槽位独立的每回合限一次（184 轮 1~4P 泛化）
        const q = this.world.partners().find((pl) => !pl.dead && !pl.downed
          && pl.hasItem?.('yamato') && pl.hp < pl.stats.get('maxHp') * 0.5
          && (this._mateUltWaves ??= {})[pl === this.world.player2 ? 2 : pl === this.world.player3 ? 3 : 4] !== this.waveIndex);
        if (q) (this._mateUltWaves ??= {})[q === this.world.player2 ? 2 : q === this.world.player3 ? 3 : 4] = this.waveIndex;
      }
      this.playerUlt = { t: 0, blades: [], launched: 0, acc: 0 };
      this.world.playerUlt = this.playerUlt;
      this.ui.toast('阎魔刀 · 次元斩！', 'good', 2500);
      // ---- 第一百六十轮：次元斩音效（玩家侧）----
      // 用户："玩家的阎魔刀触发的时候也触发一次次元斩.ogg音效吧。不要bgm
      // 但是音效要有。不过玩家触发的音量改到70%省的吵。"
      // （boss 侧同一段音效走 vergil.js 的 _se 模板；这里玩家侧单独播，
      // 音量 70%。每回合限一次的触发，不需要缓存实例。）
      try {
        const se = new Audio('assets/sfx/vergil_ult.ogg');
        se.volume = 0.7;
        se.play().catch(() => { /* 自动播放策略兜底：失败静音不炸主循环 */ });
      } catch { /* */ }
    }

    // 更新玩家（第一百八十一轮：天彗龙 boom/roar 眩晕对**玩家也生效**
    // —— 用户："玩家没有吃到眩晕，这个眩晕对玩家也要有效"。dt=0 = 定住；
    // AI 的 moveAxis 由 agentBlocked 一并清零，演出期间谁都动不了。）
    const stunDt = cometFreeze ? 0 : dt;
    p.update(stunDt, this.input, this.camera, this.world);

    // ---- 第一百七十一轮：2P 真人键位（右侧小键盘）----
    // duo 模式 P2 可以由第二位真人用小键盘 ↑↓←→（8/2/4/6）操控；
    // 小键盘有输入 = 真人接管，立刻清掉 AI 的 agentMoveTarget（人抢
    // 回控制权）。没输入时才由 pilot2 的 AI 驱动。
    // 小键盘 0 = 芙兰战减速、小键盘 . = 消弹（与 P1 的 Q/空格同效果）。
    // ---- 第一百八十四轮（1~4P）：P3/P4 只由各自 AI 驱动（无键位）；
    // 键盘只有一个，真人永远是 P1 —— AI 全部走 agentMoveTarget，
    // **不存在抢键盘**（用户担心的"强键盘"只可能发生在 P1 切 AI 代打，
    // 那是显式开关）。
    const p2 = this.world.player2;
    if (p2) {
      const p2axis = cometFreeze ? { x: 0, y: 0 } : this.input.moveAxisP2();
      if (p2axis.x !== 0 || p2axis.y !== 0) p2.agentMoveTarget = null;
      p2.update(stunDt, { moveAxis: () => p2axis, p2SkillSlow: () => this.input.p2SkillSlow(), p2SkillClear: () => this.input.p2SkillClear() }, this.camera, this.world);
    }
    for (const pl of [this.world.player3, this.world.player4]) {
      if (pl) pl.update(stunDt, { moveAxis: () => ({ x: 0, y: 0 }) }, this.camera, this.world);
    }

    // ---- 第一百六十轮：四重存在（莱万汀分身）----
    this._updatePhantoms(dt);

    // 第一百零八轮：天气调度与逐帧机制（Boss 波/芙兰战内部会自行短路）
    this.weather.update(dt, this.world, p);
    // 第一百一十四轮：天气开播 → 猫娘口播提示词（气泡贴在小小猫娘头顶，
    // 连了猫娘计划则走 TTS 语音，"颜表情符号不用说，光说文字"——
    // say 字段就是 hint 的纯文字版）
    if (this.weather.justStarted) {
      this.weather.justStarted = false;
      const say = this.weather.def?.say;
      if (say) this.pet?.say(say, 4200, this.world, 'weather');
    }

    // 更新世界（阎魔刀次元斩期间怪物慢放 ×0.05 —— 玩家照常速。
    // 第一百八十二轮：天彗龙眩晕**不再全冻世界** —— 弹幕照常飞，
    // 敌人个体冻结在 world.update 内部（enemyFrozen → e.update(0)）。
    this.world.update(dt * (this.playerUlt ? 0.05 : 1), p, this.waveTimer);

    // 相机跟随
    this.camera.follow(p.x, p.y, dt);
    this.camera.update(dt);

    // 玩家死亡 —— 第一百七十轮（2P）：P1 倒下时若队友还活着/倒地可救，
    // 游戏继续（等待救援或队友独自作战）；全队都倒下才算输。
    // 注意：duo 模式下 takeDamage 已把所有玩家死亡都转成 downed，
    // p.dead 为 true 只在非 duo 模式（兼容单人全部既有流程）。
    // ---- 第一百八十四轮：判定泛化到 1~4P —— 任何队友还"可救"
    //（活着或倒地未死）就不判负；全员倒地/死亡 = 全队失去战斗能力。
    const mates = this.world.partners();
    const mateDown = (pl) => pl && (pl.dead || pl.downed);
    const allMatesDown = mates.length > 0 && mates.every(mateDown);
    if (p.dead && (!mates.length || allMatesDown)) {
      this._endRun(false);
      return;
    }
    if (this._duoEnabled && p.downed && allMatesDown) {
      this._endRun(false);
      return;
    }

    // 升级处理（优先于波次结束）
    if (p.pendingLevelUps > 0) {
      this._showLevelUp();
      return;
    }

    // Boss 击杀 → 直接过关。
    // 标记是在 world.update() 内部打的，必须等遍历结束再结算。
    if (this._bossKillPending) {
      this._bossKillPending = false;
      this.ui.toast('Boss 已击杀，直接过关！', 'good');
      this._endWave();
      return;
    }

    // 波次结束判定
    const bossAlive = this.world.enemies.items.some((e) => e.boss && !e.dead);
    const timeUp = this.waveTimeLeft <= 0;
    if (timeUp && !this.waveEnding) {
      // 第四十八轮：Boss 关到时不再无限 +15 秒硬拖。
      // 用户原话："修改boss关判定，目前是时间到0之后会自动+15秒，无限加
      // 直到打死boss，改为到时判定这回合通过，但是无法获得boss击杀奖励
      // 和全图素材自动收集的奖励。"
      // Boss 没死 = 没有击杀掉落、没有 onBossKilled 的全场拾取真空；
      // 清场时把它无声移走（不给材料），波次照常结算进商店。
      if (bossAlive) {
        this._bossForfeit = true;
        this.ui.toast('时间到！Boss 未击杀 —— 本波通过，但拿不到 Boss 奖励', 'bad', 3000);
      }
      this._endWave();
      return;
    }

    // HUD
    this.ui.updateHud(this);

    // 低血量告警：只在"刚跌破阈值"时触发一次，靠 cooldown 兜底
    const hpRatio = p.maxHp > 0 ? p.hp / p.maxHp : 1;
    if (hpRatio < 0.3 && this._lastHpRatio >= 0.3) {
      audio.play('hurt');
      this._nekoComment(EVENT.LOW_HP, this._nekoContext());
      neko.setEmotion('surprised');
    }
    this._lastHpRatio = hpRatio;
  }

  /**
   * 第十六轮：把玩家身上的技能事件接到 UI/音效。
   *
   * 为什么要有这一层：技能逻辑（char-runtime.js）刻意**不认识** UI 和音频
   * —— 那样它才能在 Node 里被测试直接调用，不需要 mock 一整个 DOM。
   * 所以技能只改状态、只翻标记，反馈由这里补。
   *
   * 注意 `onShieldBreak` 以前**从来没有被挂过**：
   * Player.takeDamage 里一直在调 `this.onShieldBreak?.()`，
   * 但那个回调永远是 undefined，等于这条分支是空的。
   * 悠怡洛丽塔的「伤不到我喵」需要它在碎盾时进入 5 秒封锁，
   * 所以这里补上，并且顺带把 charRt 的通知也接进去。
   */
  _bindPlayerSkillEvents() {
    const p = this.player;
    if (!p) return;

    p.onShieldBreak = () => {
      // 技能侧的碎盾处理（进入封锁期 + 关掉完全格挡开关）
      p.charRt?.onShieldBreak?.();
      if (p.shieldFullBlock === false && p.charRt?.id === 'youyi') {
        this.ui?.toast('护盾碎掉了，5 秒内没法再挡喵…', '', 1600);
      }
    };
  }

  /**
   * 创建跟随宠物「小小猫娘」。
   *
   * 用户要求：和玩家一起玩、默认无敌、伤害对标白色武器、
   * 帮忙打怪或捡东西、不离玩家太远、低血回血、吃玩家 buff。
   * 具体行为在 entities/pet.js，这里只负责挂到世界和接事件。
   */
  _spawnPet() {
    if (!this.world || !this.player) return null;
    this.pet = new Pet('kitten', this.player);
    this.world.pet = this.pet;
    // 回血时给个音效 + 猫娘关心一句
    this.world.onPetHeal = (pet, amount) => {
      audio.play('heal');
      this.ui.toast(`小小猫娘给你回了 ${Math.round(amount)} 血！`, 'good');
    };
    return this.pet;
  }

  /**
   * 第一百六十轮：四重存在（莱万汀的被动）。
   *
   * 用户设计："每5秒判定一次，有20%概率变出3个玩家分身，继承玩家的25%
   * 属性……武器和玩家相同。ai会攻击附近的敌人……分身上限为3个……
   * 死了一个分身，就从玩家位置再生成一个分身。"
   * —— 判定通过时把分身补齐到 3（场上满 3 就没效果，死了补缺口）。
   *
   * 数值口径见 entities/phantom.js：攻速 35% + 每发 71.4%，总 DPS 恒为
   * 玩家 25%；3 分身合计射弹量 ≈ 玩家 105%，弹幕负载几乎不变。
   */
  _updatePhantoms(dt) {
    const p = this.player;
    const world = this.world;
    if (!p || !world) return;
    const list = world.phantoms;

    // 铁律口径：Boss 战（芙兰/维吉尔）期间分身消散；没带莱万汀同样清空。
    // 推进演出（次元斩）里也先收起来，别让分身在时间慢放里白嫖输出。
    const busy = this.world.flandreFight || this.world.vergilFight || this.playerUlt;
    if (busy || p.dead || !p.hasItem?.('laevatein')) {
      if (list.length > 0) {
        for (const ph of list) {
          world.fx.sparkle(ph.x, ph.y, '#ffb3d4', 6);
        }
        list.length = 0;
      }
      this._phantomTimer = 5;
      return;
    }

    // 每 5 秒判定一次，20% 概率补齐到 3 个（从玩家位置生成）
    this._phantomTimer = (this._phantomTimer ?? 5) - dt;
    if (this._phantomTimer <= 0) {
      this._phantomTimer = 5;
      if (list.length < 3 && this.rng.next() < 0.20) {
        const had = list.length;
        while (list.length < 3) {
          const a = this.rng.next() * Math.PI * 2;
          const ph = new PhantomClone(p, world, p.x + Math.cos(a) * 46, p.y + Math.sin(a) * 46, this.rng);
          ph.spawnT = 0.45;
          list.push(ph);
          world.fx.ring(p.x, p.y, 64, '#ffb3d4', 0.5, { player: true });
        }
        this.ui.toast(had > 0 ? '四重存在：消散的分身重新现形！' : '四重存在！', 'good', 2600);
        audio.play('heal');
      }
    }

    // 分身逐帧更新（AI/武器/接触磨损）；倒下的炸一朵花，下一轮判定补员
    for (const ph of list) ph.update(dt, world, p);
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].dead) {
        this.ui.toast('一个分身消散了……', 'info', 2200);
        list.splice(i, 1);
      }
    }
  }

  /**
   * Boss 相关事件。
   *
   * 用户要求："击杀 boss 直接过关并且自动获得 boss 的掉落经验和金币"。
   * 所以 Boss 一死就：
   *   1) 立刻结算本波（不用等 90 秒走完）
   *   2) Boss 的经验/材料自动到手（不用跑过去捡）
   *   3) 播欢呼 + 猫娘吐槽
   */
  _bindBossEvents() {
    if (!this.world) return;

    // ---- 第一百三十四轮：M博士心得 —— 受伤触发 300 范围 AOE（护甲×50）----
    // 用户："每次受到伤害的时候触发一次300范围的aoe攻击，伤害为护甲数值
    // 的 50 倍。"挂在 player.onHurt（takeDamage 真正扣血后触发；闪避/
    // miss/免疫不会调它）。走完整 dealDamage：能暴击（玩家面板暴击），
    // 无武器 → 不吃武器系加成；伤害 0 护甲时为 0（0×50），不出手省特效。
    this.player.onHurt = (amount, sx, sy) => {
      const p = this.player;
      if (p.dead || !p.hasItem('dr_m_notes')) return;
      const armor = p.stats.get('armor');
      if (armor <= 0) return;
      const dmg = Math.max(1, Math.round(armor * 50));
      const R = 300;
      let hitAny = false;
      for (const e of this.world.grid.query(p.x, p.y, R, [])) {
        if (!e.isEnemy || e.dead) continue;
        const dx = e.x - p.x, dy = e.y - p.y;
        if (dx * dx + dy * dy > R * R) continue;
        hitAny = true;
        this.world.dealDamage(p, null, e, dmg, { thorns: true });
      }
      // 视觉：一圈金色冲击环（命中与否都放 —— "挨打反弹"的反馈感）
      this.world.fx?.ring?.(p.x, p.y, R * 0.35, '#ffd23a', 0.4, { player: true });
      if (hitAny) this.world.camera?.shake?.(4, 0.15);
    };

    this.world.onBossKilled = (boss) => {
      audio.play('bossKill');
      this._nekoComment(EVENT.BOSS_KILL, this._nekoContext({ bossName: boss.def?.name }));
      neko.setEmotion('happy');

      // ---- 第一百三十四轮：Boss 专属掉落（10/20 波各掉 1 个随机红品）----
      // 用户："击败第 10 波 Boss 掉落 1 个随机红品；击败第 20 波 Boss 掉落
      // 1 个随机红品（为无尽模式准备）。掉落后在商店领取，和宝箱掉落一样。
      // 10 和 20 的 Boss 掉落的随机红品不含莱万汀。"
      // 池子 = 8 件 bossDropOnly 红品（莱万汀是 flandreOnly，天然不在内）。
      {
        // 第一百三十五轮修复：waveIndex 是 0 起算的（_beginWave(0) = 玩家
        // 的第 1 波），所以"第 10/20 波"= currentWave 9/19。旧写法
        // wave === 10 || wave === 20 永远不成立 → Boss 从不掉红品
        //（用户："打过第十波boss没有掉落红色物品"）。转成玩家视角 1-based。
        const wave = (this.world.currentWave ?? 0) + 1;
        if (wave === 10 || wave === 20) {
          // 第一百六十轮：池子排除阎魔刀 —— 用户："按理说阎魔刀不出现在
          // 普通boss掉落里。"（此前池子含 yamato，20 波 boss 随机抽中它时
          // 看起来就像"维吉尔的刀又掉了一把"。）同时排除已持有的
          //（bossDropOnly 全是 maxStacks=1，抽到了也只能领失败白摆着）。
          // 【0.3.41 修复·重大】这里的 `p` 从来就没被定义过 —— 上轮改池子
          // 时把 this.player 误写成了 p，boss 一死这句就 ReferenceError，
          // 而 world 侧的 try/catch 把异常静默吞掉：onBossKilled 中断，
          // 后面的 _bossKillPending / _autoCollectBossLoot 全都不执行 ——
          // 表现就是用户报的"打死 10 波 boss 不结算、90 秒跑完、无红装"。
          // 改回 this.player，并用可选链保证老存档/极端情况下不炸。
          const pool = ITEMS.filter((it) =>
            it.bossDropOnly && it.id !== 'yamato' && !this.player?.hasItem?.(it.id));
          if (pool.length > 0) {
            const def = this.rng.pick(pool);
            this.world.pendingItemRewards.push({
              id: def.id, name: def.name, price: def.price, rare: true,
              sellValue: Math.max(4, Math.round(def.price * 0.55)),
            });
            this.ui.toast(`击败了波次 Boss！掉落了【${def.name}】！在商店里决定留下还是卖掉`, 'good', 8000);
          }
        }
      }

      // 自动拾取 Boss 掉落的经验与材料（用户明确要求）
      this._autoCollectBossLoot();

      // 直接过关。
      // 注意：这里**不能**立刻调 _endWave() —— 我们此刻还在
      // world.update() 的敌人遍历里，_endWave 会 sweep 掉 enemies 数组，
      // 导致正在进行的 for...of 读到错乱的索引。所以只打个标记，
      // 等本帧 update 结束后在 _update() 里统一处理。
      //
      // 无限模式 30 波起会同时有 2~3 个 Boss：必须**全部**击杀才算过关。
      if (this.state === STATE.PLAYING && !this.waveEnding) {
        const others = this.world.enemies.items.some((e) => e.boss && !e.dead);
        if (others) {
          this.ui.toast(`还有 Boss 活着！`, 'bad');
        } else {
          this._bossKillPending = true;
        }
      }
    };

    // 阶段切换：给玩家一个明确的"变强了"信号
    this.world.onBossPhase = (boss, phase) => {
      audio.play('explode');
      const name = boss.def?.name ?? 'Boss';
      this.ui.toast(`${name} 进入第 ${phase} 阶段！`, 'bad');
      // 第三十六轮：原来这里错发 EVENT.LOW_HP（低血量警告），导致每次
      // Boss 转阶段小小猫娘都喊"血量过低"——用户："boss转换阶段的
      // 时候会触发小小猫娘的血量过低提醒"。现在发独立的 BOSS_PHASE
      // 场景（台词讲的是"Boss 变强了"，不再冒充低血警告）。
      this._nekoComment(EVENT.BOSS_PHASE, this._nekoContext({ bossName: name, phase }));
      neko.setEmotion('surprised');
    };
  }

  /**
   * 把场上所有掉落物直接收进玩家口袋（Boss 击杀后调用）。
   * 只收经验/材料/生命，不做其他副作用。
   */
  _autoCollectBossLoot() {
    const world = this.world;
    const p = this.player;
    if (!world || !p) return { xp: 0, materials: 0 };
    let xp = 0, mats = 0;
    for (const pk of world.pickups.items) {
      if (pk.dead) continue;
      if (pk.type === PICKUP_TYPE.XP) { p.gainXp(pk.value); xp += pk.value; }
      else if (pk.type === PICKUP_TYPE.MATERIAL) { p.gainMaterials(pk.value); mats += pk.value; }
      else if (pk.type === PICKUP_TYPE.HEAL) { p.heal(pk.value); }
      pk.dead = true;
      world.fx?.sparkle(pk.x, pk.y, '#ffc857');
    }
    world.pickups.sweep();
    this._lastBossLoot = { xp, materials: mats };
    return this._lastBossLoot;
  }

  /** 把世界里的战斗事件接到音效上（在 startRun 里挂一次） */
  _bindWorldAudio() {
    if (!this.world) return;
    this.world.onEnemyKilled = (enemy) => {
      if (enemy.boss) return;                 // Boss 死亡单独播（onBossKilled）
      if (enemy.elite) audio.play('explode');
      else audio.play('kill');
    };
    this.world.onPlayerHit = () => audio.play('hurt');
    // 武器开火音（第十轮）。限流全在 audio.playWeapon 内部，
    // 所以这里直接转发即可 —— 主循环不需要知道攻速有多快。
    this.world.onWeaponFire = (def, heavy) => audio.playWeapon(def, heavy);
    this.world.onPickup = (pk) => {
      if (pk.type === 'xp') audio.play('pickupXp');
      else if (pk.type === 'material') audio.play('pickupMat');
      else if (pk.type === 'heal') audio.play('heal');
    };
    // ---- 第十八轮 ③：小小猫娘的台词终于有显示层了 ----
    //
    // 用户反馈："小小猫娘偷东西和发动范围拾取技能的台词做好了吗？
    //            我从来没听过。"
    //
    // 台词一直都在（data/pet-lines.js 每类 10 句），但 world.onPetSay
    // 这个回调**全仓从来没有人挂过** —— 只有 pet.say() 在调它。
    // 于是台词唯一的出口就是渲染层那行小小的头顶气泡，
    // 而气泡跟着宠物在场上飘，玩家正在走位打怪，几乎不可能注意到。
    //
    // 这里把它接上。按 kind 分流，避免刷屏：
    //   · 'vacuum'  —— 范围拾取技能发动。**弹提示**（用户点名的技能）
    //   · 'freeItem'—— 白拿道具。不在这里弹，因为 _maybePetSteal 已经
    //                  弹了"小小猫娘帮你拿了 xx 道具"（用户指定的原话）
    //   · 'pickup'  —— 顺路捡东西，1.6 秒一次，**不弹**（会刷屏）
    //   · 'heal'    —— 不在这里弹，_createPet 已经有"回了 N 血"提示
    this.world.onPetSay = (pet, text, kind) => {
      if (kind === 'vacuum') {
        // 提示里带上名字，让玩家知道是谁在说话（气泡贴在宠物身上自带这个信息，
        // toast 飘在别处，不加名字会不知道是谁）
        this.ui.toast(`小小猫娘：${text}`, 'good', 2600);
      }
      // 第一百一十四轮：kind 'weather'（天气提示词口播）不弹 toast ——
      // 底部横幅已经在显示同款信息，气泡 + 语音就够
      if (kind === 'weather') {
        this._nekoSay?.(text, { key: `weather:${text.slice(0, 8)}` });
        return;
      }
      // 第七十九轮：技能台词接语音 —— 用户："小小猫娘的技能很多都没有
      // 语音，让她们实现语音，比如治疗相关的只有字纯哑巴。"
      // heal / vacuum / freeItem 走猫娘计划的语音（同芙兰战 petSay 的主
      // 通道）；'pickup' 是顺路捡东西（1.6s 一句），维持第十八轮的防刷屏
      // 设计只出气泡不出声。1 秒最小间隔防两类技能同帧抢语音；
      // 没连猫娘计划时维持原样（气泡/toast，静默回落）。
      if (kind === 'pickup') return;
      const now = performance.now();
      if (now - (this._petVoiceAt ?? -Infinity) < 1000) return;
      if (this._nekoSay?.(text, { key: `pet:${kind}:${text.slice(0, 8)}` })) {
        this._petVoiceAt = now;
        // 第八十二轮：语音框送达时清掉头顶纯文字气泡 —— 用户："小小猫娘
        // 的台词会出现2个框，就是一个文字框一个带语音的文字框会一起出现，
        // 比如治愈框。让有语音框的时候纯文字框不要出现。"
        // （sayHeal 先设了 pet.line 才回调进来；语音通道接住后这里撤掉它）
        pet.line = '';
        pet.lineTimer = 0;
      }
    };
  }

  // =========================================================
  // 升级
  // =========================================================
  _showLevelUp() {
    this.state = STATE.LEVEL_UP;
    const p = this.player;
    // 稀有度与幸运挂钩（见 upgrades.js 的 rollRarity）
    const options = rollUpgrades(this.rng, p.upgradesTaken, 3, {
      level: p.level,
      luck: p.stats.values.luck,
      // 第十六轮：把角色的幸运总效果倍率一起传下去，
      // 否则悠怡萌妹的"幸运总效果 +20%"到不了升级稀有度这一处。
      luckEffectMul: p.stats.luckEffectMul ?? 1,
    });
    this.currentUpgradeOptions = options;

    if (options.length === 0) {
      // 没有可用升级，直接跳过
      p.pendingLevelUps = 0;
      this.state = STATE.PLAYING;
      return;
    }

    // 第四十一轮：Q 模式（本局全自动）—— 不弹 UI，直接替玩家选。
    // 累积了多次升级时，_afterOverlay 会递归进这里逐次自动选完。
    if (this.autoLevelAll) {
      this._applyUpgradePick(this._autoPickOption(options));
      return;
    }

    this.ui.showLevelUp({
      level: p.level,
      options,
      onPick: (id) => {
        // 注意：必须用**这一轮抽到的那份**（效果已按稀有度缩放），
        // 不能用 getUpgrade(id) 拿基础定义 —— 那样稀有度倍率会被丢掉。
        const def = this.currentUpgradeOptions.find((o) => o.id === id) || getUpgrade(id);
        this._applyUpgradePick(def);
      },
    });
  }

  /**
   * 第四十一轮：应用一次升级选择（手动点卡 / E 自动 / Q 全自动共用）。
   * 原 onPick 闭包的整体搬到这里，三条路径行为完全一致。
   */
  _applyUpgradePick(def) {
    const p = this.player;
    // 注意：必须用**这一轮抽到的那份**（效果已按稀有度缩放），
    // 不能用 getUpgrade(id) 拿基础定义 —— 那样稀有度倍率会被丢掉。
    if (def) p.applyUpgrade(def);
    p.pendingLevelUps--;
    audio.play('levelUp');
    this.state = STATE.PLAYING;
    this.ui.clearOverlay();
    this._afterOverlay();
    // 体检修复（第九十二轮）：升级全部选完后，把被升级打断的商店补回来
    //（_openShop 的 pendingLevelUps 转跳 → 最后一次选完 state=PLAYING，
    // 旧代码没有任何路径重新开店 → 芙兰胜利后软锁）。
    if (this._shopDeferred && p.pendingLevelUps <= 0) {
      this._shopDeferred = false;
      this._openShop();
      return;   // _openShop 已经把界面/BGM/存档接走了，不再发升级吐槽
    }
    this._nekoComment(EVENT.LEVEL_UP, this._nekoContext({
      upgradeName: def ? (def.rarityName && def.rarityName !== '普通'
        ? `${def.rarityName}·${def.name}` : def.name) : undefined,
    }));
    neko.setEmotion('happy');
  }

  /**
   * 第四十一轮：自动选择的规则 ——
   * 用户原话："如果有高品质的选项优先选择高品质，如果都是同品质则随机
   * 一个选择。" 升级的稀有度排序就是 mult（1 / 1.33 / 1.66 / 2），
   * 取最高档里的随机一个。
   */
  _autoPickOption(options) {
    let best = -Infinity;
    for (const o of options) best = Math.max(best, o.mult ?? 1);
    const pool = options.filter((o) => (o.mult ?? 1) === best);
    return pool[Math.floor(this.rng.next() * pool.length)];
  }

  /** 第四十一轮：Q 切换"本局升级全自动"（新局在 startRun 里重置为关）。 */
  _toggleAutoLevel() {
    this.autoLevelAll = !this.autoLevelAll;
    this.ui.toast(this.autoLevelAll
      ? '本局升级全自动：开（下一局自动关闭）'
      : '本局升级全自动：关', this.autoLevelAll ? 'good' : 'bad');
    // 升级界面正开着时按下 Q：立刻把当前这次自动选掉
    if (this.autoLevelAll && this.state === STATE.LEVEL_UP
      && this.currentUpgradeOptions?.length) {
      this._applyUpgradePick(this._autoPickOption(this.currentUpgradeOptions));
    }
  }

  /** 从覆盖层返回游戏后的统一处理 */
  _afterOverlay() {
    if (this.player.pendingLevelUps > 0) {
      this._showLevelUp();
    }
  }

  // =========================================================
  // 波次结束 -> 商店
  // =========================================================
  _endWave() {
    const p = this.player;
    this.waveEnding = true;
    audio.play('waveClear');
    // 第一百八十轮：天彗龙 BGM 保留至回合结束 —— 此刻停，下一场景点回原曲
    this.comet?.onWaveEnd?.();

    // ---- 第一百七十轮（2P）：波次结束重置倒下次数（救援刀数恢复
    // 首档 5 刀）；倒地的人自动满血复活（用户："过了这回合倒地的人
    // 自动满血复活，不然下回合还要救援"）----
    // ---- 第一百八十四轮（1~4P）：全员结算（队友回血/倒地清零）----
    for (const pl of this.world.players()) {
      pl.resetDownCount();
      if (pl.downed) {
        pl.downed = false;
        pl.hp = pl.maxHp;
        pl.invuln = 1.5;
      }
    }

    // ---- 第一百六十轮：动态难度结算（隐式，见 world.tuneDiffScale）----
    // 在清场前调：此刻 world._waveT / _killAges 还是本波的完整数据。
    this.world.tuneDiffScale();

    // 清场（Boss 除外，但 Boss 波此时应该已经死了）
    // 第四十八轮：Boss 关到时放行（_bossForfeit）时，活着的 Boss 也一并
    // 无声移走 —— 不掉落、不触发击杀结算。
    const cleaned = this.world.endWaveCleanup(p, { removeBosses: !!this._bossForfeit });
    const cleanupBonus = this.world.lastCleanupBonus || 0;

    // 收获属性：额外材料
    const harvest = Math.round(p.stats.values.harvesting);
    if (harvest > 0) p.gainMaterials(harvest);

    // 第十六轮：角色技能带来的额外收获
    //   悠怡      → 每回合结束 +2
    //   悠怡萌妹  → 每拥有 10 点（实际生效）幸运 +1
    // 两个都走 charRt，各自返回实际加了多少，便于下面小结里显示。
    // 第一百八十四轮：duo 全员都结算（2P 悠怡/萌妹的回合收益此前一直没发）
    let skillHarvest = p.charRt?.onRoundEnd?.() ?? 0;
    let luckHarvest = p.charRt?.luckHarvest?.() ?? 0;
    if (luckHarvest > 0) p.gainMaterials(luckHarvest);
    for (const pl of (this.world.players?.() ?? [])) {
      if (pl === p || !pl || pl.dead) continue;
      skillHarvest += pl.charRt?.onRoundEnd?.() ?? 0;
      const lk = pl.charRt?.luckHarvest?.() ?? 0;
      if (lk > 0) pl.gainMaterials(lk);
      luckHarvest += lk;
    }

    // 波次结算奖励：稳定的材料收入，避免前期太穷
    const clearBonus = waveClearBonus(this.waveIndex);
    p.gainMaterials(clearBonus);

    // 每波结束回满血：否则拿不到回血道具的话，下一波开局就可能被秒
    // ---- 第一百八十四轮（1~4P）：**全员**回满 —— 之前只回 P1（p），
    // 队友上波剩一滴这波还是一滴（用户实测）。倒地的顺手满血复活。
    let healed = 0;
    for (const pl of this.world.players()) {
      const gap = pl.maxHp - pl.hp;
      if (gap > 0.5) {
        pl.hp = pl.maxHp;
        healed += gap;
      }
    }
    if (healed > 0.5) audio.play('heal');

    // 结算本波
    const waveReached = this.waveIndex + 1;

    // 上报给 NEKO（事件契约）
    if (this.nekoReady) {
      neko.reportWaveCleared(waveReached, p.kills).catch(() => { /* 忽略 */ });
    }

    // 解锁角色
    const newly = this._unlockByWave(waveReached);
    for (const c of newly) {
      this.ui.toast(`解锁新猫娘：${c.name}！`, 'good');
    }

    // 本波收入小结（让玩家看到钱从哪来）
    this._lastWaveIncome = {
      cleanup: cleanupBonus,
      harvest: harvest + skillHarvest + luckHarvest,
      clear: clearBonus,
      total: cleanupBonus + harvest + skillHarvest + luckHarvest + clearBonus,
    };

    // 是否通关
    if (waveReached >= WAVE_COUNT && !this.endlessEnabled) {
      // 第四十九轮（用户要求）：噩梦难度 + 打败 20 波 Boss 的总重开次数 ≤ 3，
      // 通关后不进结算 —— 芙兰朵露来了。奖励（经验/金币/全图收集）上面已经发完。
      // ---- 第一百八十四轮（4P 实测）：芙兰门槛改用**手动**重试计数 ——
      // AI 团灭自动重试也累计 meta.retries，AI 代打打到 20 波时早已 >3，
      // 芙兰永不解锁（用户："芙兰还没出来就莫名判定赢了"）。手动重开才
      // 算"重开次数"；旧档没有 manualRetries 字段时回落总数（保守）。
      // 185 轮：重开次数前置已删除（见 flandreUnlockOk 的说明），噩梦通关 20 波必见芙兰。
      if (flandreUnlockOk(this.difficultyId)) {
        this._startFlandre();
        return;
      }
      // 无限模式（用户第四轮要求）：
      // "在 20 波结束通关后询问玩家是否继续，然后开始 21 波"。
      // 所以这里不直接结算，而是先问玩家要不要继续。
      this._askEndless(waveReached);
      return;
    }

    // 第一百四十一轮：噩梦难度第 10 波 Boss 倒下 → 大排档椅子维吉尔降临
    //（每局一次；黑屏 3s → 大排档场地 9 秒演出 → 90 秒限时 Boss 战。
    // 结算后 _vergilFinish 会正常进第 10 波商店）
    if (waveReached === 10 && this.difficultyId === 'nightmare' && !this._vergilFought) {
      this._startVergil();
      return;
    }

    // 进入商店
    this._openShop();
  }

  // =========================================================
  // 第四十九轮：隐藏挑战 Boss 芙兰朵露
  // =========================================================

  /**
   * 第四十九轮补充：直通芙兰战（跳过"20 波 + 重开 ≤3"校验：直接开一局
   * 悠怡萌妹的噩梦、重开清零，走同一条 _startFlandre()）。
   * 第一百一十七轮转正：改名「直接挑战芙兰！」并默认隐藏 —— 用户：
   * "把这个按钮默认隐藏，在玩家成功击败过一次EP难度芙兰之后永久开启。"
   * 解锁条件见 _finishFlandre 的 outcome 'beat' 分支（meta.flandreUnlocked）。
   */
  _debugStartFlandre() {
    this.setDifficulty('nightmare');
    this.startRun('yui');
    // 第五十二轮（用户反馈 1）：测试局直接发两把远程武器 —— 悠怡萌妹默认的
    // 猫爪是近战，玩家在远处躲弹幕时根本够不着芙兰，看起来就像"还是不攻击"。
    // 近战猫爪保留，喵喵加农炮 + 星星魔杖让攻击肉眼可见。
    try {
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('star_wand');
    } catch { /* 武器表变动时不让测试按钮崩 */ }
    this.meta.retries = 0;   // 满足"总重开次数不大于3"的前置
    this._startFlandre();
  }

  /**
   * 第一百四十一轮：大排档椅子·维吉尔 Boss 战。
   * 两种入口：
   *  · 正式：噩梦难度打完第 10 波 Boss 自动触发（保留玩家当前局，
   *    战后进第 10 波商店 → 第 11 波）—— 用户："之后可以打第十一回合"。
   *  · 测试：标题按钮 / ?vergil=1 → _startVergil({ test: true }) 开独立局。
   * 入场演出（9 秒）由 VergilFight 的 intro 状态自管：3s 黑屏 → 3s 场地
   * 淡入 + Bury the Light 从 0 秒起播 → 9s 背对剪影换成本尊开战。
   */
  _startVergil(opts = {}) {
    if (opts.test) {
      this.setDifficulty('nightmare');
      this.startRun('yui');
      // 测试局输出：3 把喵喵炮 + 最终伤害 ×5（用户反馈 2：裸 3 炮 70 秒
      // 打不到 66% 血量门槛，见不到转阶段和大招 —— charMul 是角色专属
      // 最终乘算注入口，测试局专属，本体无感知）
      try {
        this.player.addWeapon('meow_cannon');
        this.player.addWeapon('meow_cannon');
        this.player.addWeapon('meow_cannon');
        this.player.charMul = { ...this.player.charMul, damage: 5 };
        // 第一百五十四轮：测试悠怡 DPS 还是太低（用户："测试的悠怡反而
        // dps太低，给她几个伤害道具和生命回复道具方便我测试3阶段，不然
        // 测试的悠怡甚至打不过5万血的大排档椅子"）—— 血条涨到 66666 后
        // 更打不动了。塞一组测试装：伤害件（獠牙×3 无诅咒 + 血之契约带
        // 5% 吸血）+ 回复件（奶糖 regen2/半血治疗翻倍 + 热牛奶×3），
        // 站得住才有得测三阶段。
        for (const id of ['sharp_fang', 'sharp_fang', 'sharp_fang', 'blood_pact',
          'cat_taffy', 'warm_milk', 'warm_milk', 'warm_milk']) {
          this.player.addItem(id);
        }
      } catch { /* */ }
      this.meta.retries = 0;
    }
    this._vergilFought = true;                // 本局触发标志（第 10 波不重复进）
    const p = this.player;
    p.noAttack = true;                        // 入场演出期间不攻击（fight 解除）
    // 202 轮（用户："维吉尔boss战也禁用玩家回血吧，只能触发小小猫娘回血"）：整场维吉尔战，生命回复 / 吸血 / 血包 / 奶糖 /
    // 回血道具 全部失效（Player.heal 统一拦 noHeal）；唯一豁免是小小猫娘的治疗（走 heal(..., { force: true })，与芙兰战同口径）。
    // P1 与队友一起禁（队友也有回复属性）；收场在 _teardownVergil 里还原。
    this._setNoHealAll(true);
    this.world.noSpawn = true;
    audio.stopMusic();                        // 掐掉当前曲，等演出第 3 秒的 Bury the Light
    this.vergil = new VergilFight(this, {
      onDone: (outcome) => this._vergilFinish(outcome),
    });
    this.world.vergilFight = this.vergil;     // 渲染层读这个画演出层
    this.state = STATE.VERGIL;
  }

  /** 维吉尔战每一帧：世界照推（吃 timeScale 慢放），AI 用真实秒 */
  _updateVergil(dtReal) {
    const fight = this.vergil;
    const p = this.player;
    if (!fight || !p) return;
    if (this.input.pressed('Escape')) {
      this._pause();
      return;
    }
    // 大招全场慢动作：世界与玩家都吃 timeScale（用户硬性要求）
    const dt = dtReal * fight.timeScale;
    audio.beginFrame();
    // 第一百四十二轮：HUD 刷新（含顶部 90 秒倒计时）—— 此前 VERGIL 分支
    // 不调 updateHud，计时器冻结在进战前的 30（用户反馈 1）
    this.ui.updateHud(this);
    this.weather.update(dt, this.world, p);
    p.update(dt, this.input, this.camera, this.world);
    // ---- 第一百七十一轮：P2 也参战（芙兰/维吉尔战世界照推，队友照动）
    // ---- 第一百八十四轮（1~4P）：P3/P4 同样参战（AI 驱动，无键位）
    const p2v = this.world.player2;
    if (p2v) {
      const ax2 = this.input.moveAxisP2();
      if (ax2.x !== 0 || ax2.y !== 0) p2v.agentMoveTarget = null;
      p2v.update(dt, { moveAxis: () => ax2, p2SkillSlow: () => this.input.p2SkillSlow() }, this.camera, this.world);
    }
    for (const pl of [this.world.player3, this.world.player4]) {
      if (pl) pl.update(dt, { moveAxis: () => ({ x: 0, y: 0 }) }, this.camera, this.world);
    }
    // waveElapsed 传 0：残留的 bossTimeRatio 不参与任何判定（同芙兰双保险）
    this.world.update(dt, p, 0);
    // 玩家软性圈在竞技场里（与芙兰同规格）
    // 第一百五十一轮：改用**维吉尔自己的 ARENA**（560×500）—— 之前误用
    // 芙兰的（520×470），场地底部 30px 永远够不着（用户："下面底部也
    // 到不了"）；并补上芙兰有而这里漏抄的**镜头锁** —— 没有它相机会跟着
    // 玩家跑出方形场地，视口大半被场外黑幕吞掉（用户："游戏下面是黑色
    // 的，上面看不到"）。
    const minX = VARENA.cx - VARENA.hw, maxX = VARENA.cx + VARENA.hw;
    const minY = VARENA.cy - VARENA.hh, maxY = VARENA.cy + VARENA.hh;
    p.x = clamp(p.x, minX, maxX);
    p.y = clamp(p.y, minY, maxY);
    // ---- 第一百八十四轮（1~4P）：全部实体都关在场地里（AI 目标点已由
    // pilot clamp，这里兜底实体坐标本身）
    for (const pl of this.world.partners()) {
      pl.x = clamp(pl.x, minX, maxX);
      pl.y = clamp(pl.y, minY, maxY);
    }
    // 镜头锁到舞台中心（芙兰同款；shake 偏移仍由 camera 自身叠加）
    this.camera.x = VARENA.cx;
    this.camera.y = VARENA.cy;
    // AI / 技能 / 命中判定：真实秒（大招里刀光节奏不吃慢动作）
    fight.update(dtReal);
  }

  /**
   * 维吉尔战结算（用户指定）：
   *   beat    —— 90 秒内打败：3 倍第 10 波 Boss 的经验/金币 + 阎魔刀
   *              （红色至尊，商店战利品栏里决定留下还是卖掉）；
   *   timeout / death —— 什么都不获得。
   * 两条路都正常进入第 10 波商店，之后可以打第 11 波（不回标题）。
   */
  _vergilFinish(outcome) {
    this._teardownVergil();
    audio.stopMusic();
    const p = this.player;
    // 死亡路径复活满血（芙兰战同款"死局不残留"口径，正常继续流程）
    if (p.dead) {
      p.dead = false;
      p.hp = p.stats.get('maxHp');
      p.invuln = Math.max(p.invuln ?? 0, 1.5);
    }
    if (outcome === 'beat') {
      // 奖励：3 倍第 10 波 Boss（boss_void_lord）的经验和金币
      const def = getEnemy('boss_void_lord');
      const scaling = this.world.waveScaling ?? { xp: 1, materials: 1 };
      const xp = Math.round((def?.xp ?? 120) * 3 * (scaling.xp ?? 1));
      const mats = Math.round((def?.materials ?? 120) * (scaling.materials ?? 1) * 3);
      p.gainXp(xp);
      p.gainMaterials(mats);
      audio.play('bossKill');
      this.ui.toast(`次元斩落下！获得 ${xp} 经验、${mats} 材料`, 'good', 4500);
      // 阎魔刀：红色至尊（每回合一次的次元斩），走宝箱战利品流程
      // 第一百六十轮：已持有则不再掉第二把 —— 用户："20波打败芙兰之后
      // 还会掉一把额外的阎魔刀，也就是玩家可能有2把了。看一下原因。"
      //（重复挑战可再次触发此掉落；maxStacks=1 只拦"持有"，不拦掉落）
      if (!p.hasItem?.('yamato')) {
        this.world.pendingItemRewards.push({
          id: 'yamato', name: '阎魔刀', price: 88, rare: true, sellValue: 66,
        });
        this.ui.toast('他掉落了【阎魔刀】！在商店里决定留下还是卖掉', 'good', 6000);
      } else {
        const pity = 120;
        p.gainMaterials(pity);
        this.ui.toast(`你已持有阎魔刀，他把刀意化作了 ${pity} 材料送你`, 'good', 5000);
      }
    } else if (outcome === 'timeout') {
      this.ui.toast('90 秒到……椅子收刀离场，什么都没有留下', 'bad', 4200);
    } else {
      this.ui.toast('被次元斩吞没了……什么都没有留下', 'bad', 4200);
    }
    // 正常进入第 10 波商店（下一波 = 第 11 波）
    this._openShop();
  }

  /** 维吉尔战现场复位（正常结算 / ESC 放弃 / 关闭游戏共用） */
  /**
   * 185 轮：Boss 战收尾时把仍处于倒地的队员拉起来（幂等）。
   * 芙兰战/维吉尔战败北（全队 dead/downed）后回到普通流程，不能带着 downed 标记和 0 血进商店/下一波；
   * 口径与 _endWave 的"过了这回合倒地的人自动满血复活"一致。
   */
  _reviveDownedAfterBoss() {
    for (const pl of (this.world?.players?.() ?? [])) {
      if (!pl) continue;
      if (pl.downed && !pl.dead) { pl.downed = false; pl.hp = pl.maxHp; pl.invuln = 1.5; }
      pl.resetDownCount?.();
    }
  }

  /**
   * 202 轮：给所有玩家（P1 + 队友）统一开/关「禁疗」（小小猫娘的 force 治疗不受影响）。
   *
   * 注意：不能只遍历 world.players() —— _startVergil 在开局的很早期就会调用它，
   * 那时 world.players() 还可能是空数组（真机实测：noAttack 设上了、noHeal 却没设上，
   * 维吉尔战里玩家照样能回血）。所以 P1 用 this.player 显式兜住，队友两个来源都查一遍。
   */
  _setNoHealAll(on) {
    const list = new Set();
    if (this.player) list.add(this.player);
    for (const pl of (this.world?.players?.() ?? [])) if (pl) list.add(pl);
    for (const pl of (this.world?.partners?.() ?? [])) if (pl) list.add(pl);
    for (const pl of list) pl.noHeal = !!on;
    return list.size;
  }

  _teardownVergil() {
    this._reviveDownedAfterBoss();
    this._setNoHealAll(false);   // 202 轮：维吉尔战结束，回血恢复
    if (this.world) {
      this.world.noSpawn = false;
      this.world.vergilFight = null;
      // 椅子实体随清场一起消失
      const items = this.world.enemies?.items;
      if (items) {
        for (let i = items.length - 1; i >= 0; i--) {
          if (items[i]?.def?.id === 'vergil_chair') items.splice(i, 1);
        }
      }
      this.world.enemyBullets.clear();
    }
    this.vergil = null;
  }

  /**
   * 第一百四十轮（旧）：刀光实验室 —— 已升级为正式 Boss 战。
   * 现在是测试直通入口（标题按钮 / ?vergil=1）：开独立局 + 双武器。
   */
  _enterVergilLab() {
    this._startVergil({ test: true });
  }

  /**
   * 20 波通关且解锁条件满足时触发。
   * 用户原话："打败20波boss获得经验和金币奖励+全图收集后没有正常的弹出
   * 游戏结束或者商店页面。而是背景在3秒内慢慢变黑直到彻底黑掉……"
   * （奖励在 _endWave 前半段已经发完，这里只接管"接下来发生什么"。）
   */
  _startFlandre() {
    const p = this.player;
    // 整场芙兰战：生命回复和吸血全部失效（小小猫娘的半血治疗走 heal()，保留）
    p.noHeal = true;
    // 第五十轮：演出+秘弹期间不攻击（共 40 秒，芙兰登场瞬间恢复）
    p.noAttack = true;
    // 玩家每中一发，小小猫娘自动触发一次消弹（不影响手动冷却）
    p.onFlandrePlayerHit = () => this.flandre?.autoPurge();
    // 第六十五轮：boss 战期间强制锁移速（Shift 按住时减半，见 player.js）
    // 第一百一十三轮：110% → 120% —— 用户："我记得之前设置速度是锁
    // 110%。现在改到120%（shirt依旧减半）。" 且改为**绝对锁**（无视
    // 库洛自己的 20% 移速），见 player.js 的速度分支。
    p.flandreSpeed = 1.2;
    this.world.noSpawn = true;
    audio.stopMusic();                       // 静默 3 秒前先掐掉 Boss 曲
    this.flandre = new FlandreFight(this, {
      onDone: (outcome) => this._flandreFinish(outcome),
    });
    this.world.flandreFight = this.flandre;  // 渲染层读这个画压暗与符卡名
    this.state = STATE.FLANDRE;
  }

  /**
   * 第一百八十四轮：万圣节双猫（巧克力 & 香草）纯演出生存战。
   * 触发：①芙兰战胜利（beat）后 3 秒黑屏衔接 —— 用户："演出就老规矩，
   * 打完芙兰，芙兰撤场3秒黑屏，在3秒换到rabiribi的背景"；②测试直入
   * ?halloween=1 或标题页【直接挑战万圣节双猫！】按钮（onHalloweenTest）。
   * 弹幕每发 10% 真伤、战斗期间回血无效（用户拍板的测试口径）；
   * 无 hitbox：双猫只存在于演出层，玩家武器索敌列表里没有她们。
   */
  _startHalloween(opts = {}) {
    const p = this.player;
    // 整场万圣节战：回血和吸血全部无效（用户："做成每发弹幕10%真伤
    // 不给回血和护甲看看"）—— 真伤走 halloweenHit 天然无视护甲，
    // 回血由 noHeal 统一拦（小小猫娘 force 治疗保留，芙兰同款）。
    p.noHeal = true;
    this.world.noSpawn = true;
    this.world.enemies.items.forEach((e) => { e.dead = true; });   // 撤场：清掉残余敌人
    this.world.enemyBullets.items.forEach((b) => { b.dead = true; });
    audio.stopMusic();                // 黑屏期间静默，场景变亮后才起 Mischievous Masquerade
    this.halloween = new HalloweenFight(this, {
      chain: !!opts.chain,
      phase: opts.phase ?? 0,   // 调试：?hwphase=N 直达第 N 节
      onDone: (outcome) => this._halloweenFinish(outcome),
    });
    this.world.halloweenFight = this.halloween;
    this.state = STATE.HALLOWEEN;
  }

  _updateHalloween(dt) {
    try {
      // ESC 暂停（v0.3.88 热修：这条分支漏接 ESC —— 芙兰战同款教训，
      // 用户实测万圣节战"无法暂停"）
      if (this.input.pressed('Escape')) {
        this._pause();
        return;
      }
      const hf = this.halloween;
      const arena = HALLOWEEN_ARENA;
      const p = this.player;
      p.update(dt, { moveAxis: () => this.input.moveAxis() }, this.camera, this.world);
      // 队友照常参战（AI 大脑每帧走 NekoAgent._tick 驱动 agentMoveTarget）
      const p2h = this.world.player2;
      if (p2h) {
        const ax2h = this.input.moveAxisP2();
        p2h.update(dt, { moveAxis: () => ax2h, p2SkillSlow: () => this.input.p2SkillSlow() }, this.camera, this.world);
      }
      for (const pl of [this.world.player3, this.world.player4]) {
        if (pl) pl.update(dt, { moveAxis: () => ({ x: 0, y: 0 }) }, this.camera, this.world);
      }
      this.world.update(dt, p, 0);
      // 全员关进宽屏场地
      const minX = arena.cx - arena.hw, maxX = arena.cx + arena.hw;
      const minY = arena.cy - arena.hh, maxY = arena.cy + arena.hh;
      for (const pl of [p, ...this.world.partners()]) {
        if (!pl) continue;
        pl.x = clamp(pl.x, minX, maxX);
        pl.y = clamp(pl.y, minY, maxY);
      }
      // 镜头锁舞台中心
      this.camera.x = arena.cx;
      this.camera.y = arena.cy;
      this.camera.update(dt);
      this.ui.updateHud(this);
      hf?.update(dt);
    } catch (e) { console.error('halloween update', e); }
  }

  _halloweenFinish(outcome) {
    this._teardownHalloween();
    audio.stopMusic();
    const p = this.player;
    if (outcome !== 'death') {
      // 通关奖励：芙兰结算同级再翻一档（万圣节糖果大礼包）
      const def = getEnemy('boss_void_lord');
      const scaling = this.world.waveScaling ?? { xp: 1, materials: 1 };
      const xp = Math.round((def?.xp ?? 120) * 4 * (scaling.xp ?? 1));
      const mats = Math.round((def?.materials ?? 120) * 4 * (scaling.materials ?? 1));
      p.gainXp(xp);
      p.gainMaterials(mats);
      audio.play('bossKill');
      this.ui.toast(`双猫玩累了回家啦！获得 ${xp} 经验、${mats} 材料`, 'good', 4500);
      if (!this.meta.halloweenUnlocked) {
        this.meta.halloweenUnlocked = true;
        this._saveMeta();
      }
    }
    this._teardownFlandre();          // 复用芙兰战的收尾（noHeal/锁速/世界复位）
    if (this._halloweenChain) {
      // 连战模式（芙兰→双猫）：双猫演完 = 芙兰原本的"20 波通关"走无尽询问
      this._halloweenChain = false;
      this._askEndless(this.waveIndex + 1);
    } else {
      this.state = STATE.SHOP;        // 测试局：回商店整备
      this._openShop();
    }
  }

  _teardownHalloween() {
    if (this.world.halloweenFight) this.world.halloweenFight = null;
    if (this.halloween) this.halloween = null;
    const p = this.player;
    p.noHeal = this.world.flandreFight ? p.noHeal : false;
    p.onFlandrePlayerHit = null;
  }
  /**
   * 第二百零三轮：蕾米莉亚终演出（芙兰 EP 打完 → 这里 60 秒 → 万圣节双猫 90 秒）。
   *
   * 用户："最后一张卡是弹幕的心脏，30秒到了的时候芙兰就输了……现在加个30秒到了，蕾米直接飞进来，bgm切蕾米的，
   *       然后芙兰从抱腿动画换回飞行动画，两个人一起攻击玩家……让蕾米芙兰继续无敌演出60秒，然后接上双猫的90秒。"
   * 两人都没有 hitbox（纯演出）；弹幕每发 10% 真实伤害，走 Player.remiliaHit（与万圣节同口径），战斗期间禁疗。
   * @param {{chain?:boolean}} opts chain = 从芙兰战衔接（3 秒黑屏换场）
   */
  _startRemilia(opts = {}) {
    const p = this.player;
    p.noHeal = true;
    // 204 轮：中弹时小小猫娘自动消弹（与芙兰战同口径）
    p.onFlandrePlayerHit = () => { this.remilia?.autoPurge?.(); };
    // 204 轮（用户："这个应该是继续使用芙兰的机制"）：沿用芙兰战的 120% 基础移速锁 ——
    // Player.update 里 Shift 半速只在 flandreSpeed 生效时才起作用。
    p.flandreSpeed = 1.2;
    this.world.noSpawn = true;
    this.world.enemies.items.forEach((e) => { e.dead = true; });
    this.world.enemyBullets.items.forEach((b) => { b.dead = true; });
    audio.stopMusic();                       // 黑屏期间静默，场景变亮后起 Sepette
    try { RemiRender.warm(); } catch { /* 预热失败不影响演出 */ }
    this.remilia = new RemiliaFight(this, {
      chain: !!opts.chain,
      onDone: (outcome) => this._remiliaFinish(outcome),
    });
    this.world.remiliaFight = this.remilia;
    this.state = STATE.REMILIA;
  }

  _updateRemilia(dt) {
    try {
      if (this.input.pressed('Escape')) { this._pause(); return; }
      const rf = this.remilia;
      // 207 轮（用户："蕾米芙兰战音效只有开头的一颗弹幕有音效"）：
      // 芙兰战每帧都调 audio.beginFrame()，它会清掉"同帧去重"表（_seFrame）。
      // 不调的话 tan00 这颗弹幕音一辈子只会响一次 —— 这就是"只有开头有音效"的原因。
      audio.beginFrame();
      // 204 轮（用户："小小猫娘消弹技能可使用"）：与芙兰战同键位（Space / P2 小键盘 .）。
      if (this.input.pressed('Space')) rf?.tryManualPurge?.();
      if (this.input.p2SkillClear()) rf?.tryManualPurge?.();
      const arena = REMILIA_ARENA;
      const p = this.player;
      // 204 轮：直接把手柄/键盘 input 交给 Player.update —— 用户要求"shirt 半速可使用"。
      // 原来只传 { moveAxis } 桩，Player.update 里的 input.isDown('ShiftLeft') 永远读不到。
      p.update(dt, this.input, this.camera, this.world);
      const p2r = this.world.player2;
      if (p2r) {
        const ax2r = this.input.moveAxisP2();
        p2r.update(dt, { moveAxis: () => ax2r, p2SkillSlow: () => this.input.p2SkillSlow() }, this.camera, this.world);
      }
      for (const pl of [this.world.player3, this.world.player4]) {
        if (pl) pl.update(dt, { moveAxis: () => ({ x: 0, y: 0 }) }, this.camera, this.world);
      }
      this.world.update(dt, p, 0);
      const minX = arena.cx - arena.hw, maxX = arena.cx + arena.hw;
      const minY = arena.cy - arena.hh, maxY = arena.cy + arena.hh;
      for (const pl of [p, ...this.world.partners()]) {
        if (!pl) continue;
        pl.x = clamp(pl.x, minX, maxX);
        pl.y = clamp(pl.y, minY, maxY);
      }
      this.camera.x = arena.cx;
      this.camera.y = arena.cy;
      this.camera.update(dt);
      this.ui.updateHud(this);
      rf?.update(dt);
    } catch (e) { console.error('remilia update', e); }
  }

  _remiliaFinish(outcome) {
    this._teardownRemilia();
    audio.stopMusic();
    const p = this.player;
    if (outcome !== 'death') {
      const def = getEnemy('boss_void_lord');
      const scaling = this.world.waveScaling ?? { xp: 1, materials: 1 };
      const xp = Math.round((def?.xp ?? 120) * 3 * (scaling.xp ?? 1));
      const mats = Math.round((def?.materials ?? 120) * 3 * (scaling.materials ?? 1));
      p.gainXp(xp);
      p.gainMaterials(mats);
      audio.play('bossKill');
      this.ui.toast(`红雾散尽，姐妹俩退场了！获得 ${xp} 经验、${mats} 材料`, 'good', 4500);
    }
    if (p.dead) {
      p.dead = false;
      p.hp = p.stats.get('maxHp');
      p.invuln = Math.max(p.invuln ?? 0, 1.5);
    }
    if (outcome === 'death') {
      // 倒在蕾米手上 = 本次挑战结束（与双猫战败同口径）
      this._teardownFlandre();
      this.state = STATE.SHOP;
      this._openShop();
      return;
    }
    // 演出继续：接万圣节双猫 90 秒
    this._halloweenChain = true;
    this._startHalloween({ chain: true });
  }

  _teardownRemilia() {
    if (this.world.remiliaFight) this.world.remiliaFight = null;
    if (this.remilia) this.remilia = null;
    const p = this.player;
    if (this.world) {
      this.world.noSpawn = false;
      this.world.enemyBullets.clear();
    }
    if (p) {
      p.noHeal = this.world?.flandreFight ? p.noHeal : false;
      p.flandreSpeed = null;                 // 204 轮：解开 120% 移速锁
      p.onFlandrePlayerHit = null;
    }
  }

  /**
   * 第二百零三轮：直通蕾米终演出测试局（?remilia=1）。
   * 悠怡 + 三喵喵炮 + 伤害×5 + 回复件，4P 阵容（AI 猫娘一起躲弹幕）—— 调参/截图用。
   */
  _startRemiliaTest(opts = {}) {
    try {
      this.setDifficulty('nightmare');
      this.startRun('yui', { party: 4 });
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.charMul = { ...this.player.charMul, damage: 5 };
      for (const id of ['cat_taffy', 'warm_milk', 'warm_milk']) this.player.addItem(id);
      this.meta.retries = 0;
      this._bindPartyPilots(4);
      this._startRemilia({ chain: true });
    } catch (e) { console.error('[remilia-test]', e); }
  }
  /**
   * 第一百八十四轮：直通万圣节双猫测试局（?halloween=1 / 标题页按钮）。
   * 悠怡 + 三喵喵炮 + 伤害×5 + 回复件，AI 大脑全开 —— 人工跑一轮演出用。
   */
  _startHalloweenTest(opts = {}) {
    try {
      this.setDifficulty('nightmare');
      this.startRun('yui', { party: 4 });   // 4P 阵容（AI 猫娘一起躲弹幕）
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.charMul = { ...this.player.charMul, damage: 5 };
      for (const id of ['cat_taffy', 'warm_milk', 'warm_milk']) this.player.addItem(id);
      this.meta.retries = 0;
      this._bindPartyPilots(4);
      // 不 adopt P1 —— 用户要**自己操作**（v0.3.88 热修：adopt 会把 P1
      // 交给 AI 木偶接管，键盘被 agentBlocked 拦掉 = "无法操作"）。
      // P2~P4 照常 AI 参战游走躲弹。
      this._startHalloween({ test: true, phase: opts.phase ?? 0 });
    } catch { /* */ }
  }

  /** 第一百八十四轮：直通芙兰战测试局（?flandre=1，同维吉尔实验室口子）。
   * 悠怡 + 三喵喵炮 + 伤害×5 + 回复件，AI 大脑全开 —— 弹幕躲避调参用。 */
  _startFlandreTest(opts = {}) {
    try {
      this.setDifficulty('nightmare');
      // 202 轮（用户人工游玩后："把芙兰挑战按钮进入的玩家数改成只有1p，不要2-4p了"）：
      // 芙兰挑战按钮 = 单人。185 轮曾改成 4P（AI 队友一起躲弹幕/倒地/救人），推翻。
      // 想看多人芙兰战：正式路径（噩梦通关 20 波），或调试入口 ?flandre=1&party=N。
      const party = opts.party >= 2 && opts.party <= 4 ? opts.party : 1;   // 按钮不传 = 1P；只有调试入口 ?flandre=1&party=N 才开多人
      this.startRun('yui', party >= 2 ? { party } : {});
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.addWeapon('meow_cannon');
      this.player.charMul = { ...this.player.charMul, damage: 5 };
      for (const id of ['cat_taffy', 'warm_milk', 'warm_milk']) this.player.addItem(id);
      this.meta.retries = 0;
      this.meta.manualRetries = 0;
      if (party >= 2) this._bindPartyPilots(party);
      // 185 轮：不再 adopt P1 —— 旧版把 P1 交给 AI 木偶接管，玩家键盘被 agentBlocked 拦掉 = "无法操作"
      //（与万圣节测试局 v0.3.88 同一个坑）。P2~P4 照常 AI 参战；用户要**自己**进去玩。
      this._startFlandre();
    } catch (e) {
      console.error('[flandre-test]', e);   // 以前这里是空 catch，?flandre=1 启动失败时完全没有任何线索
      this.ui?.toast?.('芙兰测试局启动失败：' + (e?.message ?? e), 'bad');
    }
  }

  /**
   * 体检修复（第九十二轮）：芙兰战的现场复位收拢成一个入口。
   * 正常结算（_flandreFinish）与非正常退出（ESC 放弃 / 关闭游戏）
   * 共用，保证两条路径都不会把 noHeal/noSpawn/压暗层残留到局外。
   */
  _teardownFlandre() {
    const p = this.player;
    this._reviveDownedAfterBoss();
    for (const pl of (this.world?.partners?.() ?? [])) if (pl) pl.flandreEase = 1;   // 199 轮：序章减伤复位（序章中途退出也不残留）
    if (p) {
      p.noHeal = false;
      p.noAttack = false;
      p.flandreSpeed = null;
      p.onFlandrePlayerHit = null;
    }
    if (this.world) {
      this.world.noSpawn = false;
      this.world.flandreFight = null;
      this.world.enemyBullets.clear();
    }
    this.flandre = null;
  }

  /** 芙兰战每一帧：世界照推（武器/宠物/特效/敌方子弹），刷怪与波次停摆 */
  _updateFlandre(dt) {
    const fight = this.flandre;
    const p = this.player;
    if (!fight || !p) return;
    // 第六十四轮：芙兰弹幕战恢复 ESC 暂停 ——
    // 用户："芙兰弹幕站全程不能使用暂停，把我们的暂停系统恢复。"
    // 之前这条分支漏接了 ESC 检查（只有 _updatePlaying 有），暂停菜单
    // 在整场芙兰战里都打不开。
    if (this.input.pressed('Escape')) {
      this._pause();
      return;
    }
    if (this.input.pressed('Space')) fight.tryManualPurge();
    // ---- 第一百七十一轮：P2 芙兰战技能（小键盘 . = 消弹）----
    if (this.input.p2SkillClear()) fight.tryManualPurge();
    // 第一百零八轮：芙兰战天气绝对无效 —— update 内部检测到芙兰战直接
    // 短路并强制散掉已有天气
    this.weather.update(dt, this.world, p);
    // 玩家照常更新（移动/开火——武器自动索敌到芙兰本尊）
    audio.beginFrame();
    p.update(dt, this.input, this.camera, this.world);
    // ---- 第一百七十一轮：P2 也参战（芙兰战）
    // ---- 第一百八十四轮（1~4P）：P3/P4 同样参战
    const p2f = this.world.player2;
    if (p2f) {
      const ax2 = this.input.moveAxisP2();
      if (ax2.x !== 0 || ax2.y !== 0) p2f.agentMoveTarget = null;
      p2f.update(dt, { moveAxis: () => ax2, p2SkillSlow: () => this.input.p2SkillSlow() }, this.camera, this.world);
    }
    for (const pl of [this.world.player3, this.world.player4]) {
      if (pl) pl.update(dt, { moveAxis: () => ({ x: 0, y: 0 }) }, this.camera, this.world);
    }
    // 第六十七轮：waveElapsed 传 0 —— 战斗期间 world.bossTimeRatio 恒为 1，
    // 不让打完 20 波残留的过期计时参与任何 bossPhase 判定（双保险，
    // 主修在 _spawnFlandre 里焊死阶段机）。
    this.world.update(dt, p, 0);
    // 撞墙闪光的自然淡出（0.45s；贴着墙时下方判定会一直刷新）
    p.wallFlash = Math.max(0, (p.wallFlash ?? 0) - dt);
    // 玩家软性圈在竞技场里（弹幕以地图中心偏上为舞台锚）
    // 第五十四轮：贴墙判定 —— 只要贴在边界上（±0.5px 内）就点亮 wallFlash，
    // 渲染层画淡红圆环。按"位移被钳制"判定的话，松开方向键站墙边时
    // 光圈会熄灭，玩家就又不知道墙在哪了。
    const minX = ARENA.cx - ARENA.hw, maxX = ARENA.cx + ARENA.hw;
    const minY = ARENA.cy - ARENA.hh, maxY = ARENA.cy + ARENA.hh;
    const fx = clamp(p.x, minX, maxX);
    const fy = clamp(p.y, minY, maxY);
    const EPS = 0.5;
    if (fx !== p.x || fy !== p.y || p.x <= minX + EPS || p.x >= maxX - EPS
      || p.y <= minY + EPS || p.y >= maxY - EPS) p.wallFlash = 0.45;
    p.x = fx;
    p.y = fy;
    // ---- 第一百八十四轮（1~4P）：全部实体关进芙兰场地（同维吉尔兜底）
    for (const pl of this.world.partners()) {
      pl.x = clamp(pl.x, minX, maxX);
      pl.y = clamp(pl.y, minY, maxY);
    }
    // 镜头锁到舞台中心（shake 偏移仍由 camera 自身叠加）
    this.camera.x = ARENA.cx;
    this.camera.y = ARENA.cy;
    this.camera.update(dt);
    // 芙兰战期间 HUD 照常刷新（血条对这种生存战至关重要）
    this.ui.updateHud(this);
    fight.tintUpdate(dt);
    fight.update(dt);
    // 玩家死亡 → 整场作废（拿不到任何芙兰奖励），正常回到结算流程
    // 185 轮：判负从"只看 P1"改成"全队失去战斗力"（每个玩家 dead 或 downed），与维吉尔战同口径 ——
    // P1 倒下时只要还有队友站着，战斗继续，队友可以救起 P1。单人局 team 只有 P1，行为不变。
    {
      const team = this.world.players?.() ?? [p];
      if ((p.dead || team.every((pl) => !pl || pl.dead || pl.downed)) && !fight.over) fight.onPlayerDeath();
    }
  }

  /**
   * 芙兰战结算（用户指定的三分支）：
   *   death   —— 什么都拿不到，正常继续打 21 波；
   *   partial —— 3 倍 20 波噩梦 Boss 的经验和金币；
   *   beat    —— 同上 + 莱万汀（宝箱战利品式：商店里留下或卖掉）。
   */
  _flandreFinish(outcome) {
    const p = this.player;
    // 体检修复（第九十二轮）：复位逻辑收拢进 _teardownFlandre()
    //（_endRun/_exitGame 的非正常退出也走它）
    this._teardownFlandre();

    // 第五十九轮：战斗结束 → 立刻停掉芙兰 BGM 并清掉"欠播"状态 ——
    // 用户："芙兰boss战结束之后bgm复原。我发现打过芙兰之后打下一波
    // bgm还是芙兰的。"
    // 根因：战斗中浏览器若把自动播放重新拦了一次（战斗 4 分多钟，
    // 切后台很常见），_onFilePlayFailed 会把 _wantScene 欠成
    // {scene:'flandre'}；战斗结束后没有任何地方清它，之后任意一次
    // 手势触发 unlock() 就把芙兰曲"补播"进下一波。stopMusic 会把
    // _wantScene/_sceneKey/_fileTrack 一起清零，后续场景（商店/下一波）
    // 从干净状态 playScene，不可能再带出芙兰曲。
    audio.stopMusic();

    if (outcome !== 'death') {
      const def = getEnemy('boss_void_lord');
      const scaling = this.world.waveScaling ?? { xp: 1, materials: 1 };
      const xp = Math.round((def?.xp ?? 120) * 3 * (scaling.xp ?? 1));
      const mats = Math.round((def?.materials ?? 120) * 3 * (scaling.materials ?? 1));
      p.gainXp(xp);
      p.gainMaterials(mats);
      audio.play('bossKill');
      if (outcome === 'beat') {
        this.ui.toast(` Extra Phantom 存活成功！获得 ${xp} 经验、${mats} 材料`, 'good', 4500);
        // 第一百一十七轮：击败 EP 芙兰 → 永久解锁「直接挑战芙兰！」按钮
        //（localStorage meta，跨会话生效；只奖一次，重复击败无副作用）
        if (!this.meta.flandreUnlocked) {
          this.meta.flandreUnlocked = true;
          this._saveMeta();
          this.ui.toast('标题页解锁了【直接挑战芙兰！】', 'good', 6000);
        }
        // 莱万汀：红色至尊（+60% 最终伤害 / +30% 最终承伤），走宝箱战利品
        // 流程。第一百六十轮：已持有同样不再重复掉（同阎魔刀的去重口径）。
        if (!p.hasItem?.('laevatein')) {
          this.world.pendingItemRewards.push({
            id: 'laevatein', name: '莱万汀', price: 99, rare: true, sellValue: 60,
          });
          this.ui.toast('芙兰掉落了【莱万汀】！在商店里决定留下还是卖掉', 'good', 6000);
        } else {
          const pity = 160;
          p.gainMaterials(pity);
          this.ui.toast(`莱万汀已在你手中，芙兰留下了 ${pity} 材料作为战利品`, 'good', 5000);
        }
      } else if (outcome === 'extraDeath') {
        // 第六十五轮：用户："额外模式死了拿经验和金币，没死拿莱万汀" ——
        // EP 阶段被弹幕打死也结算经验+材料（没有莱万汀）；正式卡阶段
        // 死亡依旧什么都没有。
        this.ui.toast(`倒在 Extra Phantom……仍获得 ${xp} 经验、${mats} 材料`, 'info', 4500);
      } else {
        this.ui.toast(`击退了芙兰朵露！获得 ${xp} 经验、${mats} 材料`, 'good', 4500);
      }
    } else {
      this.ui.toast('芙兰朵露消失在黑暗里……什么都没有留下', 'bad', 4000);
    }

    // 体检修复（第九十二轮）：death / extraDeath 的 p.dead 无人复位，
    // 此前"继续挑战"会变成尸体购物（下一波第一帧必死结算），"见好就收"
    // 会给死局记 win。设计口径是"death —— 什么都拿不到，**正常继续**
    // 打 21 波"，所以两条死亡路径在这里复活满血，和无尽询问语义对齐。
    if (p.dead) {
      p.dead = false;
      p.hp = p.stats.get('maxHp');
      p.invuln = Math.max(p.invuln ?? 0, 1.5);
    }

    // ---- 第一百八十四轮（用户："演出就老规矩，打完芙兰，芙兰撤场3秒
    // 黑屏，在3秒换到rabiribi的背景"）—— 芙兰战胜（beat）→ 3 秒黑屏
    // 换场 → 万圣节双猫纯演出生存战（连战）。奖励照常发，双猫战胜利
    // 后再走无尽询问；双猫战败 = 本次挑战结束（回商店整备再试）。
    if (outcome === 'beat') {
      // 202 轮以前：芙兰 EP 打赢 → 直接接万圣节双猫。
      // 第二百零三轮（用户："加个30秒到了，蕾米直接飞进来……让蕾米芙兰继续无敌演出60秒，然后接上双猫的90秒"）：
      // 中间插进蕾米莉亚的 60 秒双人演出，仍然 3 秒黑屏换场；双猫在 _remiliaFinish 里接上。
      this._remiliaChain = true;
      this._startRemilia({ chain: true });
      return;
    }

    // 游戏不结束：正常进入 20 波通关结算（继续 21 波）
    this._askEndless(this.waveIndex + 1);
  }

  /**
   * 通关第 20 波后询问是否进入无限模式。
   *
   * 用户要求："增加无限模式，在 20 波结束通关后询问玩家是否继续，
   * 然后开始 21 波，之后也是时间越来越长。"
   */
  _askEndless(waveReached) {
    this.state = STATE.RESULT;
    // ---- 第一百八十四轮（4P 实测）：询问挂起标记 —— AI pilot 的
    // _settle 看到它就知道该点"继续无限"而不是 _endRun（旧路径 AI 会
    // 瞬间点掉询问直接结算，玩家"无法打 21 回合"）。
    this._endlessPromptOpen = true;
    const p = this.player;
    this._nekoComment(EVENT.WIN, this._nekoContext());
    neko.setEmotion('happy');

    this.ui.showEndlessPrompt({
      wave: waveReached,
      player: p,
      // 第一百二十九轮：战绩块与结算页同款六项（用户："通关 20 波只显示
      // 等级和击杀，我记得之前显示很多的来着"）
      stats: {
        kills: p.kills,
        damageDealt: p.damageDealt,
        damageTaken: p.damageTaken,
        timeAlive: p.timeAlive,
      },
      onContinue: () => {
        // 打开无限模式：从第 21 波继续
        this._endlessPromptOpen = false;
        this.endlessEnabled = true;
        this.ui.clearOverlay();
        this.ui.toast('无限模式开启！23 波起 Boss 轮番登场', 'good');
        audio.play('levelUp');
        this._openShop();
      },
      onStop: () => {
        this._endlessPromptOpen = false;
        this.ui.clearOverlay();
        this._endRun(true);
      },
    });
  }

  _openShop() {
    this.state = STATE.SHOP;
    const p = this.player;

    // 处理累积的升级。
    // 体检修复（第九十二轮）：记一个"商店被升级打断"的旗子。旧代码直接
    // 转跳升级界面后旗子就丢了 —— 芙兰结算发 ~700 经验（30+ 级必升 2~4 级），
    // 玩家选「继续挑战」进店时被升级打断，选完最后一个升级 state 回
    // PLAYING、商店永久丢失，玩家被困空竞技场（软锁）。
    if (p.pendingLevelUps > 0) {
      this._shopDeferred = true;
      this._showLevelUp();
      return;
    }

    // 商店曲（用户要求："shop是买东西的时候的商店曲"）。
    // 放在 _showLevelUp 的提前 return 之后 —— 否则升级覆盖层还压在上面，
    // 音乐就先切到商店了，跟眼前的界面不符。
    audio.playScene('shop');

    this.shop.currentWave = this.waveIndex;
    this.shop.roll(p, this.waveIndex);
    // ---- 第一百七十一轮（2P）：商店分池 —— duo 下队友追加自己的专属
    // 货架（每人 4 格），P1 的 UI 和 AI 只看前 4 格，谁也抢不走谁的货。
    // 队友的 AI 从各自 slotBase 起买（见 pilot._nextWave 的 slotBase）。
    // ---- 第一百八十四轮（1~4P）：P3/P4 各追加 4 格。
    for (const mate of this.world.partners()) {
      const base = this.shop.offers.length;
      for (let i = 0; i < 4; i++) {
        this.shop.offers[base + i] = this.shop._makeOffer(mate, this.waveIndex, i);
      }
    }
    this._saveGame();

    // ---- 第十二轮第 7 项：小小猫娘偷道具 ----
    // 用户原话："加个小小猫娘偷取道具的功能，就是有时候她会从商店里偷一个
    //            道具给你，默认20%，幸运越高概率越高最高50%，
    //            并且悬浮提示3-5秒（小小猫娘帮你拿了xx道具）。"
    //
    // 放在 roll() **之后**、showShop() **之前**：
    //   * roll() 之后 —— 必须有货架才能偷。
    //   * showShop() 之前 —— 偷完的那一格要直接显示成已售出，
    //     否则玩家会看到一件买不了的商品（点下去才报"已售出"）。
    //
    // 只在**有宠物**时触发：宠物是第十一轮的跟随单位，
    // 没有宠物就没有"小小猫娘"，偷东西的人都不存在。
    this._maybePetSteal(p);

    // ---- 第一百六十轮：进商店时提醒待领取战利品 ----
    // 用户反馈链的教训：Boss 红品掉落 toast 只挂几秒（本轮已加到 8s），
    // 打完 Boss 直接弹商店，玩家一走神就错过"掉了什么"。落袋为安：
    // 进商店时按待领取数量再提醒一次。
    const pend = this.world.pendingItemRewards?.length ?? 0;
    if (pend > 0) {
      this.ui.toast(`有 ${pend} 件战利品待领取！在商店下方的战利品栏决定留下还是卖掉`, 'good', 8000);
    }

    this.ui.showShop({
      player: p,
      shop: this.shop,
      waveIndex: this.waveIndex,
      waveIncome: this._lastWaveIncome,
      // 宝箱战利品：本波开箱/杀宝箱怪攒下的道具，等玩家决定留下还是卖掉
      pendingRewards: this.world.pendingItemRewards || [],
      onTakeReward: (i) => {
        const list = this.world.pendingItemRewards || [];
        const r = list[i];
        if (!r) return;
        if (p.addItem(r.id)) {
          // 第一百七十七轮（2P）：宝箱/Boss 战利品**一人一份** ——
          // 用户："宝箱和boss战利品也一人一份。"领的人拿一份，队友
          //（若在且活着）也直接白拿一份同样的。
          const mate = this.world.players?.().find?.((o) => o && o !== p && !o.dead);
          if (mate) mate.addItem(r.id);
          list.splice(i, 1);
          audio.play('buy');
          this.ui.toast(mate ? `留下 ${r.name}（队友也拿了一份）` : `留下 ${r.name}`, 'good');
        } else {
          audio.play('error');
          this.ui.toast(`${r.name} 已达叠加上限`, 'bad');
        }
      },
      onSellReward: (i) => {
        const list = this.world.pendingItemRewards || [];
        const r = list[i];
        if (!r) return;
        p.gainMaterials(r.sellValue);   // 走共享入账（duo 下队友同步到账）
        list.splice(i, 1);
        audio.play('buy');
        this.ui.toast(`卖掉 ${r.name}，换回 ${r.sellValue} 材料`, 'good');
      },
      onBuy: (slot) => {
        const res = this.shop.buy(p, slot);
        if (!res.ok) { audio.play('error'); this.ui.toast(res.reason, 'bad'); }
        else if (res.combined) {
          // 武器栏满时买下可合成的武器 → 自动合成
          audio.play('combine');
          this.ui.toast(res.upgradedExisting
            ? `自动合成：${res.target.name} 升到 Lv.${res.level}！`
            : `自动合成：${res.target.name}！`, 'good');
          this._nekoComment(EVENT.WEAPON_COMBO, this._nekoContext({ weaponName: res.target.name }));
          neko.setEmotion('happy');
        }
        else if (res.upgraded) {
          audio.play('buy');
          this.ui.toast(`升级：${res.offer.name} → Lv.${res.weapon.level}`, 'good');
        }
        else { audio.play('buy'); this.ui.toast(`购买：${res.offer.name}`, 'good'); }

        // 买空货架 → 免费自动刷新一次（用户要求）
        if (res.autoReroll) {
          audio.play('reroll');
          this.ui.toast('买空啦！免费刷新一次货架', 'good');
        }
      },
      onReroll: () => {
        const res = this.shop.reroll(p);
        if (!res.ok) { audio.play('error'); this.ui.toast(res.reason, 'bad'); }
        else audio.play('reroll');
      },
      onCombine: (weaponId) => {
        const res = this.shop.combine(p, weaponId);
        if (!res.ok) { audio.play('error'); this.ui.toast(res.reason, 'bad'); }
        else {
          audio.play('combine');
          this.ui.toast(res.upgraded
            ? `${res.target.name} 升级到 Lv.${res.level}！`
            : `合成成功：${res.target.name}！`, 'good');
          this._nekoComment(EVENT.WEAPON_COMBO, this._nekoContext({ weaponName: res.target.name }));
          neko.setEmotion('happy');
        }
      },
      onLock: (slot) => {
        const locked = this.shop.toggleLock(slot);
        audio.play('lock');
        this.ui.toast(locked ? '已锁定，下一家商店还会在' : '已取消锁定', locked ? 'good' : '');
      },
      onSell: (weaponInst) => {
        const name = weaponInst.def.name;
        const value = this.shop.sellWeapon(p, weaponInst);
        audio.play('buy');
        this.ui.toast(`已出售 ${name}，换回 ${value} 材料`, 'good');
      },
      onNext: () => {
        const next = this.waveIndex + 1;
        audio.play('click');
        this.ui.clearOverlay();
        // 第九十四轮：state 必须先于 _beginWave 切走 —— _beginWave 末尾
        // 拍波次快照，state 还挂在 SHOP 上会把 phase:'shop' 写进快照
        //（正是"重试本波变开店"回归的源头，见 _snapshotWave 的注释）。
        this.state = STATE.PLAYING;
        this._beginWave(next);
      },
    });
  }

  /**
   * 小小猫娘偷一件商店道具（第十二轮第 7 项）。
   *
   * 用户要求：默认 20%、幸运越高最高 50%、悬浮提示 3~5 秒、
   * 文案是「小小猫娘帮你拿了 xx 道具」。
   *
   * 概率和货架操作都在 `shop.tryStealItem()` 里（那边有完整的边界处理），
   * 这里只负责三件事：判断有没有宠物、放提示、放音效。
   *
   * 提示时长取 **4000ms**：用户说"3-5 秒"，取中间值。
   * 为什么不用默认的 1.4 秒 —— 这条提示是**异步**出现的，
   * 玩家此刻很可能正在看别的格子（或者刚点开商店还没看货架），
   * 1.4 秒会直接错过，那就等于没提示。
   *
   * 音效复用 `buy`：项目里没有专门的"偷"音效，
   * 而 `buy` 是"东西到手了"的通用正反馈，语义上最接近。
   * 不要为这一条去加新音频资源 —— 按项目原则，音频是可选增强。
   */
  _maybePetSteal(player) {
    const pet = this.pet || this.world?.pet;
    if (!pet || pet.dead) return null;

    const res = this.shop.tryStealItem(player);
    if (!res) return null;

    // 宠物头顶冒个得意的 $ 表情，配合提示。
    // 走 stealAnim 计时器（渲染器从计时器推表情，不读 emote 字段），
    // 时长和 toast 的 4 秒对齐 —— 提示在的时候表情也在。
    try { pet.stealAnim = 4.0; } catch { /* 忽略 */ }

    // 第十五轮 ③：免费道具台词。
    // 用户点名要求**不要用"偷"相关的词**，所以台词是"人家给你拿到好道具啦"
    // 这种邀功语气（见 data/pet-lines.js 的 PET_FREE_ITEM_LINES）。
    try { pet.sayFreeItem(this.world); } catch { /* 忽略 */ }
    // 道具上贴猫爪印记（用户要求）。
    // 标记打在**这个道具实例**上，渲染器据此在图标角上画一个猫爪。
    // 用实例字段而不是全局开关：玩家一次只免费拿一件，
    // 但商店里还有别的道具，全局开关会全都被印上爪子。
    try { res.offer.pawMark = 1; } catch { /* 忽略 */ }

    audio.play('buy');
    // 第十二轮用户点名要的文案，原样保留。
    // （第十五轮我一度改成"帮你拿到…啦"去和宠物台词统一，但那属于
    //   自作主张改用户的原话 —— test/pet-steal-check.mjs 立刻红了，
    //   而且它红得有道理：用户写的是引号里的字面要求，不是示意。）
    this.ui.toast(`小小猫娘帮你拿了${res.item.name}道具`, 'good', 4000);
    return res;
  }

  // =========================================================
  // 暂停
  // =========================================================
  _pause() {
    // 第七十轮：记住从哪个状态暂停 —— 芙兰战里 ESC 暂停再恢复必须回到
    // 芙兰战。用户："目前游戏中使用暂停键，镜头会变为以玩家为中心模式，
    // 然后芙兰不攻击了。" 旧版 _resume 无条件回 PLAYING：镜头被 follow
    // 拉到玩家身上、fight.update 没人调导致芙兰停手 —— 全是这一行引起。
    this._pauseFrom = this.state;
    this.state = STATE.PAUSED;
    this.ui.showPause({
      player: this.player,
      onResume: () => this._resume(),
      onQuit: () => {
        this.ui.clearOverlay();
        this._endRun(false, true);
      },
      onExit: () => this._exitGame(),
      // 第一百六十六轮：猫娘代打切换开关（显示当前状态，点了切换并
      // 重开暂停菜单刷新文案）
      pilotOn: !!window.NekoAgent?.pilot?.on,
      onTogglePilot: () => {
        const pl = window.NekoAgent?.pilot;
        if (!pl) return;
        if (pl.on) pl.stop(this);   // 交还：语音"诶，要你亲自出马了吗？"
        else pl.adopt(this);        // 接管：语音"要人家帮你玩了吗～"
        // 重画暂停菜单刷新开关状态。**_pauseFrom 必须保住原值** ——
        // _pause() 会把 _pauseFrom 记成"当前状态"，而当前就是 PAUSED，
        // 之后点继续 state 回到 PAUSED = 永远卡在暂停（实测"切换就卡死"）
        const from = this._pauseFrom;
        this._pause();
        this._pauseFrom = from;
      },
      // 第三十九轮："玩家特效"透明度滑条（0~100%，持久化到 meta）
      fxAlpha: this.meta.fxAlpha ?? 100,
      onFxAlpha: (v) => {
        this.meta.fxAlpha = v;
        fxSettings.playerAlpha = v / 100;
        this._saveMeta();
      },
    });
  }

  /**
   * 第十四轮 ⑥：关闭游戏。
   *
   * 用户原话："加一个关闭游戏的按钮在暂停界面和开始菜单。不然通过猫娘主动
   *            邀请进入游戏之后想关闭只能 alt+f4。"
   *
   * 做法直接照抄 NEKO 官方 soccer 示例的退出流程（见
   * static/game/games/soccer/soccer-demo.js 的 finally 段）：
   *   1. 先停掉本局（存档、停 BGM），别让音频在关窗后还响
   *   2. window.close() —— NEKO 用 window.open 弹子窗口，所以这一句通常有效
   *   3. 若没关掉（普通标签页 / 用户手敲 URL 进来，浏览器不允许脚本关自己）
   *      就退回到主界面的根路径 '/'，语义上等价于"回到猫娘聊天界面"
   *
   * 为什么不能只写 window.close()：浏览器只在"这个窗口是脚本自己打开的"
   * 前提下才允许脚本关闭它。单独打开 index.html 调试时 close() 是静默失败的，
   * 所以必须有 location 兜底，否则按钮看起来"点了没反应"。
   */
  _exitGame() {
    try {
      this.running = false;
      if (this._rafId) cancelAnimationFrame(this._rafId);
      this._rafId = null;
      if (this.flandre) this._teardownFlandre();   // 体检修复：退出先拆芙兰战现场
      if (this.vergil) this._teardownVergil();      // 141 轮：维吉尔战同理
      if (this.player && this.world) this._saveGame();
      audio.stopMusic?.();
    } catch { /* 退出流程里任何一步失败都不该挡住关窗 */ }

    // 给用户一个"正在关闭"的反馈，而不是页面瞬间消失
    this.ui.clearOverlay();
    this.ui.toast?.('正在关闭游戏…', '', 900);

    setTimeout(() => {
      try { window.close(); } catch { /* 普通标签页会抛，忽略 */ }
      // close() 没生效时兜底回主界面。
      // 用 !window.closed 判断而不是无条件跳转 —— 已经关掉的窗口不该再导航。
      setTimeout(() => {
        if (!window.closed) {
          try { window.location.assign('/'); } catch { /* 忽略 */ }
        }
      }, 150);
    }, 600);
  }

  _resume() {
    this.ui.clearOverlay();
    // 第七十轮：回到暂停前的状态（芙兰战暂停 → 恢复回芙兰战，镜头/战斗不脱轨）
    this.state = this._pauseFrom ?? STATE.PLAYING;
  }

  // =========================================================
  // 波次快照 / 重试本波（第八轮）
  // =========================================================
  //
  // 用户要求："给游戏增加输了之后重试当前波次的按钮，但是记录重试次数，
  // 不是死了就立马只能从第一波从头打。"
  //
  // 做法：在**每波开始时**存一份当时的完整状态快照。
  // 死了以后点「重试本波」就恢复这份快照 —— 于是回到"这一波刚开局"的样子：
  // 等级、武器、道具、材料、血量全都是进这波时的，不用从第 1 波重打。
  //
  // 为什么不复用自动存档 SAVE_KEY：
  //   那个是"当前进度"，读档语义是"接着玩"；快照是"回到这波开头"，语义不同。
  //   而且死亡时自动存档会被 _clearSave() 清掉。所以单独一个 key。
  //
  // 为什么快照要在**波次开始时**拍、而不是死亡时拍：
  //   死亡时玩家已经掉血、怪也清了一批，那时候拍下来恢复出来的不是"这波开头"。
  _snapshotWave() {
    if (!this.player || !this.world) return;
    try {
      // 体检修复回归（第九十四轮）：快照的 phase **必须**强制为 'play'。
      // _beginWave 也可能从商店的"下一波"进来，而 state = PLAYING 是
      // 在 _beginWave **之后**才设的 —— 直接透传 _serializeRun() 会把
      // phase: 'shop' 写进波次快照，之后死亡点"重试本波"时
      // _applyRunData 的 phase === 'shop' 分支会跳过 _beginWave 直接
      // 开店：计时器不重置，剩几秒就重开几秒（用户实测 3 秒）。
      // 快照在语义上就是"波次开局"，恒为 'play'。
      const run = this._serializeRun();
      run.phase = 'play';
      localStorage.setItem(RETRY_KEY, JSON.stringify({
        run,
        retries: this.waveRetries || 0,
      }));
    } catch (e) { /* 忽略：存不下就只是没有重试按钮 */ }
  }

  /** 读波次快照；没有或坏了就返回 null */
  _readWaveSnapshot() {
    try {
      const raw = localStorage.getItem(RETRY_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !data.run || !data.run.weapons) return null;
      return data;
    } catch (e) { return null; }
  }

  /** 有快照才显示「重试本波」按钮 */
  canRetryWave() {
    return !!this._readWaveSnapshot();
  }

  /**
   * 重试当前波次：恢复到这一波开头的样子，并把重试次数 +1。
   * 重试次数记在 meta 里（跨局累计，持久化），也记在当前局里（给 UI 显示）。
   */
  retryWave(fromAi = false) {
    const snap = this._readWaveSnapshot();
    if (!snap) { this.showCharSelect(); return false; }

    const wave = snap.run.waveIndex;
    this.waveRetries = (snap.retries || 0) + 1;

    // 累计到 meta 并落盘（这是用户要的"记录重试次数"）
    // ---- 第一百八十四轮（4P 实测）：AI 团灭自动重试传 fromAi=true，
    // 只进总数 retries；芙兰解锁的"重开 ≤3"口径看 manualRetries（只有
    // 玩家手动点的重试才 +1）—— 不然 AI 代打必然把门槛吃穿，芙兰永不
    // 解锁（"芙兰还没出来就莫名判定赢了"）。
    this.meta.retries = (this.meta.retries || 0) + 1;
    if (!fromAi) this.meta.manualRetries = (this.meta.manualRetries || 0) + 1;
    this._saveMeta();

    this._applyRunData(snap.run, { fromRetry: true });

    // ---- 第一百七十一轮修复：duo 重试保留队友 ——
    // 快照重建 world 是单人流程，这里把队友补回来（v0.3.48"重试后
    // 变回单人"的根因）。重开局的队友满血满状态、倒地次数清零。
    // ---- 第一百八十四轮：1~4P —— 按开局人数全量重拉 + AI 全量重绑。
    if (this._duoEnabled) {
      const size = this._partySize || 2;
      const oldChars = [this.world.player2, this.world.player3, this.world.player4]
        .filter(Boolean).map((pl) => pl.characterId);
      for (let s = 2; s <= size; s++) {
        this._spawnPartner(this.player.characterId, oldChars[s - 2], s);
      }
      // ---- 第一百七十四轮：AI 队友重绑 ——
      // _spawnPartner 造的是**新**实体，pilots 还牵着旧的（duo watch
      // 团灭自动重试后，队友 AI 会失联挂机）。新实体一落地就把大脑接上。
      const pilots = window.NekoAgent?.pilots;
      if (Array.isArray(pilots)) {
        const fresh = this.world.partners();
        pilots.forEach((pt, i) => {
          const np = fresh[i];
          if (np && pt) pt.start(this, { entity: np });   // 无条件重绑（幂等）
        });
      } else if (window.NekoAgent?.pilot2?.on) {
        const np2 = this.world.player2;
        if (np2) window.NekoAgent.pilot2.start(this, { entity: np2 });
      }
    }

    // 波次 BGM 已由 _applyRunData → _beginWave 按波数点好（含 Boss 波）。
    // 这里只负责解锁 + 补播被自动播放策略拦下的那次请求。
    audio.unlock();
    this.ui.clearOverlay();
    this.ui.showHud(true);
    this.state = STATE.PLAYING;

    // 猫娘对重试说句话（不刷屏：这是玩家主动点的，一次一句没问题）
    this._nekoComment(EVENT.RUN_START, {
      characterName: this.player.char?.name || this.player.def?.name,
      wave: wave + 1,
      level: this.player.level,
      kills: this.player.kills,
      hpRatio: this.player.hp / Math.max(1, this.player.stats.get('maxHp')),
    });
    return true;
  }

  // =========================================================
  // 结束
  // =========================================================
  _endRun(win, quit = false) {
    this.state = STATE.RESULT;
    const p = this.player;

    // 体检修复（第九十二轮）：芙兰战里 ESC → 放弃/关闭 时清掉战斗现场，
    // 否则结算页背后还压着芙兰的暗层和符卡名（渲染层读 world.flandreFight）。
    if (this.flandre) this._teardownFlandre();
    // 141 轮：维吉尔战同理（noSpawn/noAttack/演出层不残留到结算页）
    if (this.vergil) this._teardownVergil();

    // 第一百八十四轮：天彗龙的 BGM 是独立 audio 元素（不进 audio 的场景
    // 通道），audio.stopMusic() 管不到 —— 团灭时如果事件正在放，结算页
    // 会一直压着 comet_bgm 双重奏（用户实测："团灭了之后天彗龙的bgm一直
    // 持续没停过"）。这里显式停掉，换回普通结算曲。
    this.comet?.stopBgm?.();

    audio.stopMusic();
    // 结算曲（用户要求："end是输了或者结束的曲子"）。
    // 必须放在 stopMusic() 之后：stopMusic 会把文件 BGM 的状态清空，
    // 先点歌会被这次清理顺手停掉。
    audio.playScene('end');
    if (!quit) audio.play(win ? 'win' : 'death');

    // 猫娘收尾：通关欢呼 / 阵亡安慰（主动退出不吐槽）
    if (!quit) {
      this._nekoComment(win ? EVENT.WIN : EVENT.DEATH, this._nekoContext());
      neko.setEmotion(win ? 'happy' : 'sad');
    }
    if (this.nekoReady) {
      const outcome = quit ? 'quit' : (win ? 'win' : 'death');
      neko.reportResult(outcome, this.waveIndex + 1, p.kills, p.characterId)
        .catch(() => { /* 忽略 */ });
    }

    // 更新元数据
    const waveReached = win ? WAVE_COUNT : this.waveIndex + 1;
    this.meta.bestWave = Math.max(this.meta.bestWave, waveReached);
    this.meta.totalKills += p.kills;
    this.meta.runs++;
    if (win) this.meta.wins++;
    this._unlockByWave(waveReached);
    this._saveMeta();
    this._clearSave();

    this.ui.showHud(false);
    this.ui.showResult({
      win,
      player: p,
      waveIndex: this.waveIndex,
      stats: {
        kills: p.kills,
        damageDealt: p.damageDealt,
        damageTaken: p.damageTaken,
        timeAlive: p.timeAlive,
      },
      // 第八轮：输了以后可以直接重试本波（不用从第 1 波重打）。
      // 通关和主动退出不给这个按钮 —— 没有"重试"的语义。
      canRetry: !win && !quit && this.canRetryWave(),
      waveRetries: this.waveRetries || 0,
      totalRetries: this.meta.retries || 0,
      onRetryWave: () => this.retryWave(),
      onRestart: () => this.showCharSelect(),
      onTitle: () => this.showTitle(),
    });
  }

  // =========================================================
  // 存档
  // =========================================================
  /**
   * 把当前这一局序列化成可存可恢复的对象。
   * 自动存档、波次快照（重试本波）都用它，保证两边字段永远一致。
   */
  _serializeRun() {
    const p = this.player;
    return {
      characterId: p.characterId,
      waveIndex: this.waveIndex,
      level: p.level,
      xp: p.xp,
      xpNeeded: p.xpNeeded,
      materials: p.materials,
      hp: p.hp,
      // 护盾（第十二轮）：和 hp 一样是**运行时状态**，不是属性修正，
      // 所以必须单独存。不存的话读档会白送一次"护盾被清空"——
      // 玩家刚花一个传说升级拿到的 150% 盾，重试本波就没了。
      // 老存档没有这个字段 → 恢复时按 0 处理（等价于"没有护盾"）。
      shield: p.shield,
      maxShield: p.maxShield,
      weapons: p.weapons.map((w) => ({ id: w.def.id, level: w.level })),
      items: [...p.items.entries()],
      upgrades: [...p.upgradesTaken.entries()],
      // 属性修正（base/flat/pct/**curse**）统一由 stats.js 打包。
      // 以前这里手写 `{ base, flat, pct }` 三件套，加 curse 时漏掉了，
      // 结果读档 / 重试本波会把诅咒惩罚全部洗掉（血上限 21.50 读一次档
      // 变回 42.00），玩家可以用读档逃掉诅咒代价。详见 serializeStatMods()。
      stats: serializeStatMods(p.stats),
      seed: this.rng.seed,
      kills: p.kills,
      difficulty: this.difficultyId,
      // 第十一轮：第 10/20 波 Boss 的"远程/近战"抽签结果。
      //
      // **必须存**。不存的话「重试本波」会重新抽一次 ——
      // 玩家想要近战 Boss 就一直重试到抽中为止，50% 概率形同虚设。
      // 存下来之后，同一波无论重试多少次都是同一个 Boss。
      bossChoices: this.world?.bossChoices ?? {},
      // 体检修复（第九十二轮）：无尽模式标志必须进存档。旧代码漏掉后，
      // 无尽中途读档标志回 false → 第 23+ 波重新命中 20 波通关分支
      //（噩梦+重试≤3 时会重新触发芙兰战刷奖励）。
      endless: !!this.endlessEnabled,
      // 体检修复（第九十二轮）：记"存档时正开着商店"。商店存档点在
      // _endWave 发完本波收入之后，旧代码读档会 _beginWave(N) 重打
      // 第 N 波再发一遍收入 → 退出/继续循环刷钱。存 phase 后读档直接
      // 重开商店。（波次快照在 _beginWave 里拍，state=PLAYING，
      // 恒为 'play'，不受影响。）
      phase: this.state === STATE.SHOP ? 'shop' : 'play',
      // 体检修复（第九十二轮）：芙兰掉落的莱万汀在领取前挂在
      // pendingItemRewards，旧代码不序列化 → 开店存档后读档直接丢失。
      pendingItemRewards: (this.world?.pendingItemRewards ?? [])
        .filter((r) => r && typeof r.id === 'string'),
      // 体检修复（第九十二轮）：莱万汀的最终乘算挂的是玩家裸字段
      //（不在 StatSheet 里），不存的话读档/重试会静默洗掉
      //（+80% 伤害 / +50% 承伤双双消失）。老存档没有 → 按 1 处理。
      dealtFinalMul: p.dealtFinalMul ?? 1,
      takenFinalMul: p.takenFinalMul ?? 1,
      // 第九十三轮：过期拾取物的双倍补偿额度也是运行时状态 ——
      // 不存的话读档/重试本波会把"暂存的收益"清零。
      xpDebt: p.xpDebt ?? 0,
      materialDebt: p.materialDebt ?? 0,
      // ---- 第一百八十四轮：多人模式进存档（用户："继续游戏应该是不兼容
      // 我们的新多人模式"—— 4P 存档读回来队伍蒸发）。party=人数，mates=
      // 每个队友的完整成长快照（角色/血量/等级/武器/道具/升级/属性修正）。
      party: this._duoEnabled ? (this._partySize || 2) : 0,
      p1Ai: !!window.NekoAgent?.pilot?.on,
      mates: this._duoEnabled ? this.world.partners().map((pl, i) => ({
        slot: i + 2,
        characterId: pl.characterId,
        hp: pl.hp,
        level: pl.level,
        xp: pl.xp,
        xpNeeded: pl.xpNeeded,
        weapons: pl.weapons.map((w) => ({ id: w.def.id, level: w.level })),
        items: [...pl.items.entries()],
        upgrades: [...pl.upgradesTaken.entries()],
        stats: serializeStatMods(pl.stats),
      })) : [],
    };
  }

  _saveGame() {
    // 第一百三十六轮：存档守卫 —— waveIndex 不是有效波次（undefined /
    // 负数/读档恢复期）时**不写档**。旧代码会照单全收，而
    // JSON.stringify 会把 undefined 字段直接丢掉 → 档里从此没有
    // waveIndex → 下次读档永远回到第 1 波但道具成长还在（用户实测：
    // "插件版继续会回到第一波，但是道具和成长还在"）。跳过这次写入，
    // 保留上一份完好的档。
    if (!Number.isInteger(this.waveIndex) || this.waveIndex < 0) return;
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify(this._serializeRun()));
    } catch (e) { /* 忽略 */ }
  }

  /**
   * 从序列化数据恢复一局（loadGame 和「重试本波」共用）。
   *
   * 注意：这里只负责重建世界和成长，**不管** UI 状态和 BGM ——
   * 那两件事调用方各自处理，因为读档和重试的界面流程不一样。
   *
   * @param {object} data      _serializeRun 的产物
   * @param {object} [opts]    fromRetry: 「重试本波」调用 —— **永远**重开
   *   波次，不吃 phase === 'shop' 的开店分支（重试的语义就是"重打这一波"，
   *   绝不可能是"进商店"；也兜住 localStorage 里已被污染的旧快照）。
   */
  _applyRunData(data, opts = {}) {
    this.rng = new RNG(data.seed || randomSeed());
    this.player = new Player(data.characterId, this.rng);
    this.world = new World(this.rng, this.camera);
    // 恢复存档里的难度（旧存档没有该字段 → 用默认）
    if (data.difficulty) this.difficultyId = data.difficulty;
    this.world.setDifficulty(this.difficultyId);
    this.shop = new Shop(this.rng);
    this.shop.difficultyId = this.difficultyId;   // 噩梦分段涨价（第七轮）
    // 第四十七轮：读档路径同样恢复角色专属刷新费倍率
    this.shop.rerollCostMul = this.player.def?.rerollCostMul ?? 1;
    // 第一百零八轮：读档路径同样挂天气系统
    this.weather = new WeatherSystem(this.rng);
    this.world.weather = this.weather;
    this.player.world = this.world;

    // 第十一轮：恢复 Boss 抽签结果。老存档没有这个字段 → 保持空对象，
    // 也就是"不替换"，行为和第 10 轮之前一致（不会炸）。
    this.world.bossChoices = data.bossChoices && typeof data.bossChoices === 'object'
      ? { ...data.bossChoices }
      : {};

    // 体检修复（第九十二轮）：恢复无尽标志。老的无尽存档没有 endless
    // 字段 → 用 waveIndex 兜底（存档已打到 20 波之后 = 一定开过无尽），
    // 避免老档在第 23 波重新弹"要进入无限模式吗"。
    this.endlessEnabled = !!data.endless
      || (Number(data.waveIndex) + 1) > WAVE_COUNT;

    // 绑定音效 + BGM
    this._bindWorldAudio();
    this._bindBossEvents();
    this._bindPlayerSkillEvents();
    this._spawnPet();

    // 还原成长
    const p = this.player;
    p.level = data.level;
    p.xp = data.xp;
    p.xpNeeded = data.xpNeeded;
    p.materials = data.materials;
    p.kills = data.kills || 0;

    // 还原属性修正（flat / pct / curse）。
    // 走 stats.js 的 restoreStatMods：
    //   - 老存档没有 stats.curse 字段 → 当成"没有诅咒"（乘数 1），不报错；
    //   - 存档里的脏值（NaN / 0 / 负数）会被挡掉并按 CURSE_FLOOR 夹住，
    //     免得一个 NaN 顺着乘法把血量上限污染成 NaN。
    restoreStatMods(p.stats, data.stats);

    // 还原武器
    p.weapons = [];
    for (const w of data.weapons) p.addWeapon(w.id, w.level);

    // 还原道具
    p.items = new Map(data.items);
    p.upgradesTaken = new Map(data.upgrades);

    // 体检修复（第九十二轮）：恢复莱万汀的最终乘算裸字段与待领奖励。
    // 乘算做有限正数校验（同护盾的防 NaN 手法），脏值按 1 处理。
    const dMul = Number(data.dealtFinalMul);
    p.dealtFinalMul = Number.isFinite(dMul) && dMul > 0 ? dMul : 1;
    const tMul = Number(data.takenFinalMul);
    p.takenFinalMul = Number.isFinite(tMul) && tMul > 0 ? tMul : 1;
    // 第九十三轮：恢复过期拾取物的双倍补偿额度（有限非负校验，脏值按 0）。
    const xDebt = Number(data.xpDebt);
    p.xpDebt = Number.isFinite(xDebt) && xDebt > 0 ? Math.floor(xDebt) : 0;
    const mDebt = Number(data.materialDebt);
    p.materialDebt = Number.isFinite(mDebt) && mDebt > 0 ? Math.floor(mDebt) : 0;
    this.world.pendingItemRewards = Array.isArray(data.pendingItemRewards)
      ? data.pendingItemRewards.filter((r) => r && typeof r.id === 'string')
      : [];

    // 还原血量
    p.hp = clamp(data.hp, 1, p.stats.get('maxHp'));
    p._lastMaxHp = p.stats.get('maxHp');

    // 还原护盾（第十二轮）。老存档没有该字段 → 0。
    // 夹掉 NaN / 负数：护盾直接参与 takeDamage 的减法，
    // 一个 NaN 会让"护盾 > 0"永远为假（NaN 比较恒 false），
    // 也就是护盾静默失效 —— 比报错更难查，所以在这里挡住。
    const savedShield = Number(data.shield);
    p.shield = Number.isFinite(savedShield) && savedShield > 0 ? savedShield : 0;
    const savedMaxShield = Number(data.maxShield);
    p.maxShield = Number.isFinite(savedMaxShield) && savedMaxShield > 0
      ? Math.max(savedMaxShield, p.shield)
      : p.shield;

    this.camera.snapTo(p.x, p.y);

    // 每波开始时清零的运行时标志（重试时尤其要清干净，
    // 否则上一波残留的 waveEnding / 待升级会立刻触发结算）
    this.waveEnding = false;
    this.pendingLevelUps = 0;
    this._bossKillPending = false;
    this._lastHpRatio = 1;
    this._shopDeferred = false;

    // 体检修复（第九十二轮）：商店存档点读档后直接重开商店，
    // 不重打已结算的第 N 波（否则每轮"退出→继续"白赚一遍波次收入）。
    // 第九十四轮：重试本波**绕过**这个分支（见 opts.fromRetry 的说明）——
    // 否则波次快照若带 phase:'shop'，重试会变成开店且计时器不重置。
    // ---- 第一百八十四轮：多人队伍恢复（用户："继续游戏应该是不兼容我们
    // 的新多人模式"）—— retryWave 不走这里（它有全量重拉逻辑，双拉会
    // 叠出重复队友）。必须在 shop/_beginWave 之前就位：开店分池和波次
    // 的 players() 遍历都依赖队友在场。
    if (!opts.fromRetry && Number(data.party) >= 2 && Array.isArray(data.mates) && data.mates.length > 0) {
      window.__duo = true;
      this.__duo = true;
      this._partySize = Math.min(4, Number(data.party));
      this._duoEnabled = true;
      for (const m of data.mates) {
        const pl = this._spawnPartner(this.player.characterId, m.characterId, m.slot || 2);
        if (!pl) continue;
        pl.level = Number(m.level) || 1;
        pl.xp = Number(m.xp) || 0;
        if (Number(m.xpNeeded) > 0) pl.xpNeeded = Number(m.xpNeeded);
        try { restoreStatMods(pl.stats, m.stats); } catch { /* 老档无字段按裸装 */ }
        pl.weapons = [];
        for (const w of (Array.isArray(m.weapons) ? m.weapons : [])) pl.addWeapon(w.id, w.level);
        pl.items = new Map(Array.isArray(m.items) ? m.items : []);
        pl.upgradesTaken = new Map(Array.isArray(m.upgrades) ? m.upgrades : []);
        pl.hp = clamp(Number(m.hp) || pl.maxHp, 1, pl.stats.get('maxHp'));
        pl._lastMaxHp = pl.stats.get('maxHp');
      }
      this._bindPartyPilots(this._partySize);
      // 原局 P1 也是 AI（全员猫娘）→ 读档后同样接管
      if (data.p1Ai) window.NekoAgent?.pilot?.adopt(this);
    }
    if (!opts.fromRetry && data.phase === 'shop') {
      // 第一百三十六轮：商店分支同样要拿到规整后的波次 —— 否则
      // this.waveIndex 保持旧值/undefined，店里"开始下一波"会算出 NaN
      this.waveIndex = Number.isInteger(data.waveIndex) && data.waveIndex >= 0
        ? data.waveIndex : 0;
      this._openShop();
      return;
    }
    // 第一百三十六轮：读档端兜底 —— 老档/坏档没有 waveIndex（或不是
    // 非负整数）时回落第 1 波，绝不把 undefined 传进 _beginWave
    //（否则 this.waveIndex 被污染，下一次进店存档又把 undefined 写回去，
    // "回到第 1 波"从此固化）。
    const waveIdx = Number.isInteger(data.waveIndex) && data.waveIndex >= 0
      ? data.waveIndex : 0;
    this._beginWave(waveIdx);
  }

  _readSave() {
    try {
      const raw = localStorage.getItem(SAVE_KEY) || localStorage.getItem(LEGACY_SAVE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  _clearSave() {
    try {
      localStorage.removeItem(SAVE_KEY);
      localStorage.removeItem(LEGACY_SAVE_KEY);
    } catch (e) { /* 忽略 */ }
  }

  loadGame() {
    const data = this._readSave();
    if (!data) { this.showCharSelect(); return; }

    this._applyRunData(data);

    // 波次 BGM 已由 _applyRunData → _beginWave 按波数点好（含 Boss 波）。
    // 这里只负责解锁 + 补播被自动播放策略拦下的那次请求。
    audio.unlock();

    this.ui.clearOverlay();
    this.ui.showHud(true);
    this.state = STATE.PLAYING;
  }

  // =========================================================
  // 渲染
  // =========================================================
  _render() {
    if (this.state === STATE.TITLE || this.state === STATE.CHAR_SELECT) {
      // 标题界面画一个静态背景
      this.renderer.renderIdleBackground();
      return;
    }
    if (this.player && this.world) {
      this.renderer.render(this.world, this.camera, this.player);
      this._anchorBubbleToPet();
    }
  }

  /**
   * 把猫娘台词气泡挪到宠物头上（用户要求）。
   *
   * 气泡是 NEKO SDK 的 presentation.bubble 挂到 #neko-bubble-slot 里的 DOM，
   * 所以这里只负责移动那个容器 —— 不需要（也不能）改 SDK 内部实现。
   * 用 position:fixed + 世界坐标转屏幕坐标，每帧跟随。
   */
  _anchorBubbleToPet() {
    const slot = this._bubbleSlot || (this._bubbleSlot = document.getElementById('neko-bubble-slot'));
    if (!slot) return;
    const pet = this.world?.pet;
    if (!pet || pet.dead) return;

    const sp = this.camera.worldToScreen(pet.x, pet.y - pet.radius * 2.4);
    // 气泡底部对准宠物头顶：容器用 transform 把自己往上抬
    slot.classList.add('anchored');
    slot.style.left = `${Math.round(sp.x)}px`;
    slot.style.top = `${Math.round(sp.y)}px`;
  }
}

// ============================================================
// 启动
// ============================================================
const canvas = document.getElementById('game');
const game = new Game(canvas);
window.__game = game;
game.start();

// ============================================================
// 第一百六十四轮：window.NekoAgent —— 猫娘（宿主 AI）专属控制接口
//
// 与"模拟键鼠+截图OCR"的物理外挂相对的**底层 API 直调**方案：
// 宿主插件读 getGameState() 拿精简局势 JSON，用 doAction(str) 下达
// 结构化指令，setPuppetMode(true) 接管键盘（玩家可随时抢回）。
// 血量 <30% 或升到 10 级时在 window 上抛 CustomEvent 'neko-agent-event'
// （detail.type = 'hpLow' | 'levelUp10' | 'puppet'），供 TTS/直播弹幕。
//
// 【健壮性契约】所有公开方法 **绝不 throw**：内部全量 try/catch，
// 异常一律折叠成 { ok:false, error } 返回值，外部插件瞎传参数也不会
// 打断游戏主循环。
// ============================================================
window.NekoAgent = {
  /** 代打大脑（第一百六十五轮）：start()/stop()/report()，tick 由 _tick 驱动 */
  pilot: new NekoPilot(),

  /** 第一百七十轮（2P）：队友猫娘的大脑 —— 驱动 world.player2（P2），
   *  entity 模式，双 0buff 实验；duo 局由 _duoPlay 启动 */
  pilot2: new NekoPilot(),

  /** 第一百八十四轮（1~4P）：P3/P4 的大脑 —— 与 pilot2 同构的 entity
   *  模式实例；pilots 数组（字面量之后聚合）按槽位序供重绑遍历。 */
  pilot3: new NekoPilot(),
  pilot4: new NekoPilot(),

  /** 内部：抛全局事件（构造失败也吞掉） */
  _emit(type, detail) {
    try {
      window.dispatchEvent(new CustomEvent('neko-agent-event', {
        detail: { type, ...detail, t: Date.now() },
      }));
    } catch { /* 事件通道失败不影响游戏 */ }
  },

  /**
   * 事件钩子：主循环每帧调用。hp<30% 触发一次 hpLow（回升后重置），
   * level≥10 触发一次 levelUp10（降回 10 以下自动重置，下一局可重发）。
   */
  _tick(g) {
    try {
      // 代打大脑优先于事件钩子 —— 它要看到所有状态（level_up/shop/
      // vergil/result），而事件钩子只在 playing 下有意义。大脑崩溃
      // 只影响代打，绝不拖垮游戏主循环。
      // ---- 第一百七十一轮修复：pilot2 先于 pilot —— 推波是 pilot 的
      // 职责，pilot 一买东西就 _beginWave（shop 态只活一帧）；pilot2 排
      // 后面就永远轮不到商店（v0.3.48/49 实测 P2 八波零购买）。
      // 先让队友们买东西，再让 P1 买+推波（1~4P：pilot3/4 也排前面）。
      if (this.pilots) for (const pt of this.pilots) { if (pt?.on) pt.tick(g); }
      else if (this.pilot2?.on) this.pilot2.tick(g);
      if (this.pilot?.on) this.pilot.tick(g);
      const p = g?.player;
      if (!p || g.state !== 'playing') return;
      const maxHp = Number(p.maxHp) || 0;
      const hp = Number(p.hp) || 0;
      if (maxHp > 0 && hp / maxHp < 0.3) {
        if (!this._hpLowFired) {
          this._hpLowFired = true;
          this._emit('hpLow', { hp, maxHp, ratio: Math.round((hp / maxHp) * 1000) / 1000 });
        }
      } else {
        this._hpLowFired = false;
      }
      const lv = Number(p.level) || 0;
      if (lv >= 10) {
        if (!this._lv10Fired) {
          this._lv10Fired = true;
          this._emit('levelUp10', { level: lv });
        }
      } else {
        this._lv10Fired = false;
      }
    } catch { /* 钩子永不抛错 */ }
  },

  /**
   * 【1. 状态感知】精简局势 JSON。
   * 必含：player.x/y、hp/maxHp、最近 5 个怪物的相对坐标+类型、
   * wave、isOverlayOpen；升级界面时附 upgradeOptions（id/name/desc）。
   */
  getGameState() {
    try {
      const g = window.__game;
      if (!g) return { ok: false, error: 'game not ready' };
      const p = g.player;
      const st = {
        ok: true,
        state: g.state ?? null,
        // 波次：world.currentWave 0 起算（与 HUD 同款 +1）；无 world 时退回 game.waveIndex
        wave: ((g.world?.currentWave ?? (Number.isInteger(g.waveIndex) ? g.waveIndex : 0)) + 1) || 1,
        puppet: !!g.agentPuppet,
        isOverlayOpen: g.state !== 'playing',
        player: null,
        enemies: [],
        upgradeOptions: [],
      };
      if (p) {
        st.player = {
          x: Math.round((Number(p.x) || 0) * 10) / 10,
          y: Math.round((Number(p.y) || 0) * 10) / 10,
          hp: Math.round((Number(p.hp) || 0) * 10) / 10,
          maxHp: Math.round((Number(p.maxHp) || 0) * 10) / 10,
          level: Number(p.level) || 0,
          dead: !!p.dead,
        };
      }
      // 【0.3.41 修复】STATE.LEVEL_UP 是 'level_up'（带下划线）——之前写成
      // 'levelup'，getGameState 在真实升级界面永远不给选项、chooseUpgrade
      // 永远被守卫拦下（行为探针当时造假状态没暴露）。与 STATE 枚举对齐。
      if ((g.state === 'level_up' || g.state === 'levelup')
        && Array.isArray(g.currentUpgradeOptions)) {
        st.upgradeOptions = g.currentUpgradeOptions.map((o, i) => ({
          index: i,
          id: o?.id ?? null,
          name: o?.name ?? `选项${i}`,
          desc: o?.desc ?? '',
        }));
      }
      if (p && Array.isArray(g.world?.enemies?.items)) {
        const near = [];
        for (const e of g.world.enemies.items) {
          if (!e || e.dead) continue;
          const dx = e.x - p.x;
          const dy = e.y - p.y;
          near.push({ type: e.def?.id ?? 'unknown', dx: Math.round(dx), dy: Math.round(dy), d2: dx * dx + dy * dy });
        }
        near.sort((a, b) => a.d2 - b.d2);
        st.enemies = near.slice(0, 5).map(({ d2, ...rest }) => rest);
      }
      return st;
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err) };
    }
  },

  /**
   * 【2. 行动指令】字符串 DSL：
   *   move(x, y)         —— 移动到地图坐标（到位自动停）
   *   stop()             —— 停止移动
   *   chooseUpgrade(i)   —— 升级三选一选第 i 个（0/1/2）
   *   buyShopItem(i)     —— 商店买第 i 个槽位
   */
  doAction(actionStr) {
    try {
      if (typeof actionStr !== 'string' || !actionStr.trim()) {
        return { ok: false, error: 'actionStr required, e.g. "move(320, 180)"' };
      }
      const m = actionStr.trim().match(/^(move|stop|chooseUpgrade|buyShopItem)\s*\(\s*(.*?)\s*\)$/i);
      if (!m) return { ok: false, error: `unknown action: ${actionStr.slice(0, 60)}` };
      const name = m[1].toLowerCase();
      const args = m[2] ? m[2].split(',').map((s) => Number(s.trim())) : [];
      const g = window.__game;
      if (!g) return { ok: false, error: 'game not ready' };

      if (name === 'move') {
        const [x, y] = args;
        if (!g.player) return { ok: false, error: 'no player (not in a run)' };
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return { ok: false, error: 'move(x, y) needs two finite numbers' };
        }
        g.player.agentMoveTarget = { x, y };
        return { ok: true, action: 'move', target: { x, y } };
      }
      if (name === 'stop') {
        if (g.player) g.player.agentMoveTarget = null;
        return { ok: true, action: 'stop' };
      }
      if (name === 'chooseupgrade') {
        const i = args[0];
        // 【0.3.41 修复】同上：真实状态是 'level_up'（兼容无下划线旧写法）
        if (g.state !== 'level_up' && g.state !== 'levelup') {
          return { ok: false, error: `not in levelup (state=${g.state})` };
        }
        const opts = Array.isArray(g.currentUpgradeOptions) ? g.currentUpgradeOptions : [];
        if (!Number.isInteger(i) || i < 0 || i >= opts.length) {
          return { ok: false, error: `index out of range (0..${opts.length - 1})` };
        }
        const picked = opts[i];
        g._applyUpgradePick(picked);
        return { ok: true, action: 'chooseUpgrade', picked: picked?.name ?? picked?.id ?? i };
      }
      if (name === 'buyshopitem') {
        const i = args[0];
        if (g.state !== 'shop') return { ok: false, error: `not in shop (state=${g.state})` };
        if (!g.shop || typeof g.shop.buy !== 'function') return { ok: false, error: 'no shop instance' };
        if (!g.player) return { ok: false, error: 'no player' };
        if (!Number.isInteger(i) || i < 0) return { ok: false, error: 'buyShopItem(i) needs a slot index ≥ 0' };
        const res = g.shop.buy(g.player, i);
        return { ok: true, action: 'buyShopItem', slot: i, result: res ?? null };
      }
      return { ok: false, error: 'unreachable action branch' };
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err) };
    }
  },

  /**
   * 【3. 木偶模式】true：键盘完全接管（Input.agentBlocked），角色只听
   * doAction；false：玩家立即抢回控制权（并清除未完成的移动意图）。
   */
  setPuppetMode(on) {
    try {
      const flag = !!on;
      Input.agentBlocked = flag;
      const g = window.__game;
      if (g) {
        g.agentPuppet = flag;
        if (!flag && g.player) g.player.agentMoveTarget = null;
      }
      this._emit('puppet', { enabled: flag });
      return { ok: true, puppet: flag };
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err) };
    }
  },
};

// 第一百八十四轮（1~4P）：按槽位序聚合队友大脑 [pilot2, pilot3, pilot4]，
// retryWave 重绑 / _tick 驱动都遍历这个数组。
window.NekoAgent.pilots = [window.NekoAgent.pilot2, window.NekoAgent.pilot3, window.NekoAgent.pilot4];

// ---- 第一百八十四轮：?party=N 自动开局（1~4，调试/演示用）----
// 随机 P1 角色 + N-1 个 AI 猫娘，跳过选角界面；?seed= 复现同局。
// **必须放在 NekoAgent（pilots）定义之后** —— 第一版放在 game.start()
// 后面，那时 pilots 还不存在，_bindPartyPilots 绑了个寂寞（用户实测
// "3p/4p 从来没动过"的第二根因）。
try {
  const q = new URLSearchParams(location.search);
  const party = parseInt(q.get('party') ?? '', 10);
  if (party >= 1 && party <= 4) {
    const ids = CHARACTERS.map((c) => c.id);
    const pick = (excl) => {
      const pool = ids.filter((x) => !excl.includes(x));
      return pool[Math.floor(Math.random() * pool.length)];
    };
    const a = pick([]);
    game.startRun(a, party >= 2 ? { party } : {});
    window.__duo = party >= 2;
    game.__duo = party >= 2;   // 184 轮：game 实例同步（pilot 救援分支读它）
    if (party >= 2) {
      game._bindPartyPilots(party);
      // **必须 adopt**：pilot.start(game) 无 entity 参数时走"代打"分支，
      // 内部会 g.startRun('luna') 重新单人开局 —— 把刚拉起来的 4P 全冲掉
      //（探针实锤 partySize=1、队友蒸发）。adopt 只就地接管 P1 大脑。
      window.NekoAgent?.pilot?.adopt(game);
    }
  }
  // ?cont=1：自动"继续游戏"（等价点标题的继续按钮）—— 4P 存档卡死复现用
  if (q.has('cont')) {
    try { game.loadGame(); } catch (e) {
      try { fetch('http://127.0.0.1:8033/probe?name=cont-error', { method: 'POST', body: String(e && e.stack || e) }); } catch { /* */ }
    }
  }
  // ?savequit=1&party=N：t=3s 强制存档 + dump 存档字段，t=4s 剥掉参数
  // 重载成 ?probe=1&cont=1 —— 全自动复现"4P 退出→继续游戏"链路。
  if (q.has('savequit')) {
    setTimeout(() => {
      try {
        game._saveGame();
        const raw = localStorage.getItem('neko_miao_save_v1') || '(empty)';
        let keys = '(none)';
        try { keys = Object.keys(JSON.parse(raw)).join(','); } catch { /* */ }
        fetch('http://127.0.0.1:8033/probe?name=save-dump', {
          method: 'POST',
          body: JSON.stringify({ party: game._partySize, duoEnabled: game._duoEnabled, keys }),
        });
      } catch { /* */ }
    }, 3000);
    setTimeout(() => {
      try { location.href = location.origin + location.pathname + '?probe=1&cont=1'; } catch { /* */ }
    }, 4000);
  }
  // ?flandre=1：直通芙兰战测试局（同 ?vergil=1 的口子，4P 弹幕躲避
  // 调试用 —— 正常路径要打穿 20 波才能见到她）
  if (q.has('flandre')) {
    game._startFlandreTest({ party });   // 202 轮：?flandre=1 = 单人；?flandre=1&party=4 才开 4P
  }
  // ?halloween=1：直通万圣节双猫测试局（人工跑一轮演出 —— 用户要求
  // "你试试跑一轮加个测试按钮，我人工跑跑看"）。
  // ?remilia=1：直通蕾米终演出测试局
  if (q.has('remilia')) {
    game._startRemiliaTest();
  }
  if (q.has('halloween')) {
    // ?hwphase=N（0~10）直达第 N 节，逐招截图验收用
    game._startHalloweenTest({ phase: Number(q.get('hwphase')) || 0 });
  }
  // ?probe=1：把对局画面/状态定时 POST 给本地探针服务器（184 轮 4P
  // 修复验证用 —— headless 环境没有 CDP 截图通道，canvas 自证最干净）。
  // 与 party/flandre 任一开局参数搭配使用。
  if (q.get('probe') === '1' && (party >= 1 || q.has('flandre') || q.has('cont') || q.has('halloween'))) {
    const cv = canvas;
    // ---- 184 轮：vergil=1（与 party 组合）—— 8s 时直接走正式入口进维
    // 吉尔战（保留 4P 阵容），并在 dump 里挂帧时长 profiler 定位卡顿。
    if (q.has('vergil')) {
      setTimeout(() => { try { game._startVergil(); } catch { /* */ } }, 8000);
      setTimeout(() => {
        try {
          let last = performance.now(); const dts = [];
          const prof = (now) => { dts.push(now - last); last = now; if (dts.length < 3000) requestAnimationFrame(prof); };
          requestAnimationFrame(prof);
          for (const t of [20000, 35000, 50000, 58000]) {
            setTimeout(() => {
              try {
                const g2 = game;
                const w = g2.world;
                const sorted = [...dts].sort((a, b) => a - b);
                const snap = {
                  t, state: g2.state, samples: dts.length,
                  avgMs: dts.length ? Math.round(dts.reduce((a, b) => a + b, 0) / dts.length * 10) / 10 : 0,
                  p95Ms: sorted.length ? Math.round(sorted[Math.floor(sorted.length * 0.95)] * 10) / 10 : 0,
                  maxMs: sorted.length ? Math.round(sorted[sorted.length - 1] * 10) / 10 : 0,
                  counts: {
                    bullets: w?.enemyBullets?.items?.length ?? 0,
                    parryFx: w?.vergilFight?.parryFx?.length ?? 0,
                    parrySparks: w?.vergilFight?.parrySparks?.length ?? 0,
                    waves: w?.vergilFight?.waves?.length ?? 0,
                    pickups: w?.pickups?.items?.length ?? 0,
                    enemies: w?.enemies?.items?.length ?? 0,
                  },
                  bossHp: (() => { try { const vf = w?.vergilFight; return vf ? { hp: Math.round(vf.boss.hp), max: Math.round(vf.boss.maxHp), invuln: Math.round(vf.boss.bossPhase?.invulnTimer ?? 0), phase: vf.phase, st: vf.state } : null; } catch { return null; } })(),
                  mates: (() => { try { const vf = w?.vergilFight; if (!vf) return null; return w.partners().map((pl) => ({ id: pl.characterId, d: Math.round(Math.hypot(pl.x - vf.boss.x, pl.y - vf.boss.y)), weap: (pl.weapons || []).map((wp) => wp?.def?.kind ?? '?') })); } catch { return null; } })(),
                };
                fetch('http://127.0.0.1:8033/probe?name=perf-' + t, { method: 'POST', body: JSON.stringify(snap) });
              } catch { /* */ }
            }, t);
          }
        } catch { /* */ }
      }, 8500);
    }
    for (const t of [4000, 8000, 12000, 16000, 20000, 24000]) {
      setTimeout(() => {
        try {
          const data = cv.toDataURL('image/png');
          fetch('http://127.0.0.1:8033/probe?name=frame-' + t, { method: 'POST', body: data });
        } catch { /* 探针失败不影响游戏 */ }
      }, t);
    }
    for (const t of [4500, 25000, 38000, 50000]) {
      setTimeout(() => {
        try {
          const g2 = game;
          const snap = {
            t,
            duo: window.__duo,
            partySize: g2._partySize,
            duoEnabled: g2._duoEnabled,
            state: g2.state,
            wave: g2.waveIndex,
            w2: !!g2.world.player2, w3: !!g2.world.player3, w4: !!g2.world.player4,
            p1: { x: Math.round(g2.player.x), y: Math.round(g2.player.y), tgt: !!g2.player.agentMoveTarget, mats: Math.round(g2.player.materials ?? 0) },
            mates: g2.world.partners().map((pl) => ({
              id: pl.characterId, x: Math.round(pl.x), y: Math.round(pl.y),
              tgt: !!pl.agentMoveTarget, down: pl.downed, dead: pl.dead,
              pilotOn: (window.NekoAgent?.pilots ?? []).some((pt) => pt.entity === pl),
              mats: Math.round(pl.materials ?? 0),
              armor: Math.round((pl.stats?.get?.('armor') ?? 0) * 10) / 10,
              armorWave: pl._armorWave,
              hpRegen: Math.round((pl.stats?.get?.('hpRegen') ?? 0) * 10) / 10,
            })),
          };
          fetch('http://127.0.0.1:8033/probe?name=state-' + t, { method: 'POST', body: JSON.stringify(snap) });
        } catch { /* */ }
      }, t);
    }
    // ?probe=1&rush=1：钱包预算压测 —— 每 3s 强开商店（跳过战斗），
    // 快速堆到高波次看 matCap 是否仍把各人钱包压在水位内。
    if (q.has('rush')) {
      setInterval(() => {
        try {
          const g2 = game;
          if (g2.state === 'playing' && Number.isInteger(g2.waveIndex)) g2._openShop();
        } catch { /* */ }
      }, 3000);
    }
    // ?probe=1&rescue=1：救援行为验证 —— 6s 时强制打倒一只猫娘，
    // 之后每 2s dump 其余 AI 与倒地者的距离（应持续缩小 = 在去救）。
    if (q.get('rescue') === '1') {
      setTimeout(() => {
        try {
          const victim = game.world.partners()[2];
          if (victim && !victim.downed) {
            victim.down();
            game.world.partners().forEach((pl) => { pl.hp = pl.maxHp; });   // 其余满血排除回血干扰
          }
        } catch { /* */ }
      }, 6000);
      for (const t of [8000, 10000, 12000]) {
        setTimeout(() => {
          try {
            const g2 = game;
            const victim = g2.world.partners().find((pl) => pl.downed);
            const rescuers = g2.world.partners().filter((pl) => !pl.downed && !pl.dead).map((pl) => ({
              id: pl.characterId, x: Math.round(pl.x), y: Math.round(pl.y),
              tgt: pl.agentMoveTarget ? { x: Math.round(pl.agentMoveTarget.x), y: Math.round(pl.agentMoveTarget.y) } : null,
              distToVictim: victim ? Math.round(Math.hypot(pl.x - victim.x, pl.y - victim.y)) : null,
            }));
            fetch('http://127.0.0.1:8033/probe?name=rescue-' + t, {
              method: 'POST',
              body: JSON.stringify({ t, victimDown: !!victim, victim: victim ? { id: victim.characterId, x: Math.round(victim.x), y: Math.round(victim.y) } : null, rescuers }),
            });
          } catch { /* */ }
        }, t);
      }
    }
    // ?probe=1&chest=1：拆箱走位验证 —— 6s 时在 P1 旁 300px 放一个
    // 静止宝箱，之后每 2s dump 各 AI 与宝箱的距离（应缩到 58/150 内）。
    if (q.get('chest') === '1') {
      setTimeout(() => {
        try {
          game.world.spawnChest(game.player, { x: game.player.x + 300, y: game.player.y });
        } catch { /* */ }
      }, 6000);
      for (const t of [6500, 8000, 10000, 12000]) {
        setTimeout(() => {
          try {
            const g2 = game;
            const chest = g2.world.enemies.items.find((e) => e.isChest && !e.dead && !e.def);
            fetch('http://127.0.0.1:8033/probe?name=chest-' + t, {
              method: 'POST',
              body: JSON.stringify({
                t,
                chest: chest ? { x: Math.round(chest.x), y: Math.round(chest.y), hp: Math.round(chest.hp), maxHp: chest.maxHp, dead: chest.dead } : null,
                mates: g2.world.partners().filter((pl) => !pl.dead).map((pl) => ({
                  id: pl.characterId, x: Math.round(pl.x), y: Math.round(pl.y),
                  distToChest: chest ? Math.round(Math.hypot(pl.x - chest.x, pl.y - chest.y)) : null,
                })),
              }),
            });
          } catch { /* */ }
        }, t);
      }
    }
  }
} catch { /* 参数异常走正常选角流程 */ }