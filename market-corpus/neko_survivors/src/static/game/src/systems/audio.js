// ============================================================
// 音频系统
//
// 音效（SFX）：零素材，全部用 WebAudio 实时合成。
//   1. 这个项目是零依赖、无构建的，不想引入一堆二进制素材；
//   2. 合成音效体积极小、可参数化、方便按难度/事件调整；
//   3. 不涉及任何素材版权问题。
//
// BGM：**优先播音频文件，文件不可用时回落到程序化音序器**。
//   用户第十轮给了 15 首 OGG（见下面的 BGM 曲库注释），明确要求按场景播放。
//   但本项目的设计原则是"素材永远是可选增强，不是必需品"
//   （见 src/core/assets.js 的同类说明）：assets/bgm/ 整个目录删掉、
//   或者浏览器拦掉自动播放，游戏都必须照常能玩，只是退回合成 BGM。
//
// 结构：
//   - 一个 AudioContext（用户首次交互后 resume，浏览器策略要求）
//   - sfx 总线 + music 总线，各自有独立音量，最终汇到 master
//   - 音效用短包络的振荡器/噪声实时生成
//   - 文件 BGM 用 <audio> 播，经 createMediaElementSource 接进 music 总线
//   - 程序化 BGM 是一个小型音序器：和弦进行 + 贝斯 + 鼓点 + 主旋律，
//     按"关卡强度"切换张弛（Boss 波会变紧张），作为文件 BGM 的回落
// ============================================================

// 第六十四轮：原版音效内嵌数据（base64 WAV）—— 播放通道见 _playSeFile。
import { SE_DATA } from './se-data.js';

// ------------------------------------------------------------
// BGM 曲库
//
// 用户要求："BGM: files in E:\猫娘姐妹\BGM —— 1-10相关名字的曲子是1-10回合的
//   曲子，11-20是之后的曲子，然后boss名字就是boss回合曲子，open是打开游戏的
//   曲子，end是输了或者结束的曲子，shop是买东西的时候的商店曲。然后关卡和boss
//   曲都不止一首，改为随机播放其中一首，无尽模式下则随机播放1-20关卡的曲子，
//   boss回合随机boss曲子。"
//
// 为什么复制进来时要**改名成纯 ASCII**：
//   原始文件名用的是**全角括号**（`1-10（1）.ogg`）。全角括号在 URL、zip/asar
//   打包、以及代码页不同的 Windows 之间都是雷（本仓库已经因为 PowerShell 把
//   UTF-8 写成 GBK 踩过一次）。所以 assets/bgm/ 里一律用短 ASCII 名。
//   映射关系（左边 = 仓库里的文件名，右边 = E:\猫娘姐妹\BGM 里的原始名）：
//
//     stage_1_1.ogg … stage_1_5.ogg   ←  1-10（1）.ogg  … 1-10（5）.ogg
//     stage_2_1.ogg … stage_2_3.ogg   ←  11-20（1）.ogg … 11-20（3）.ogg
//     boss_1.ogg    … boss_4.ogg      ←  boss(1).ogg     … boss(4).ogg
//     open.ogg                        ←  open.ogg
//     end.ogg                         ←  end.ogg
//     shop.ogg                        ←  shop.ogg
// ------------------------------------------------------------

/** BGM 目录（相对页面）。NEKO 部署后结构与本地一致。 */
export const BGM_DIR = './assets/bgm';

/**
 * 第六十二轮：东方红魔乡原版音效接线（素材由拆包会话提供：
 * touhou-flandre/flandre-se/nc/，新典重制版，芙兰实际听到的就是它们）。
 * 依据 SE-MAPPING.md 的三级证据（SoundPlayer.cpp 两张表 + 新旧 exe 指针
 * 数组逐项比对）反推的最小接线表 —— 音量就是原版 g_SoundBufferIdxVol
 * 的线性增益，照抄即还原：
 *   · tan00    0.14  主弹幕声（bullet_sound 7/22，同文件双用途）
 *   · power0   0.56  符卡开场「嗡——」充能声（effect_sound 5）
 *   · lazer00  0.36  Lævateinn 激光（effect_sound 16）
 *   · enep01   0.50  大爆炸 / BOSS 崩坏（effect_sound 18）
 *   · cat00    0.36  符卡宣言「锵！」（GUI 层，16 张卡全用这一个）
 *   · fault    0.63  符卡结束（新典专属 effect_sound 33）
 *   · timeout  0.56  最后 10 秒滴答（GUI 层，每秒一声）
 */
export const SE_DIR = './assets/sfx';
// 第六十八轮：全部原版音效音量 ×0.70 —— 用户："把全部芙兰的弹幕音效音量
// 调整到70%，不然盖住太多bgm了"。原版档位（上表）保留在注释里作参照。
export const SE_LIB = {
  tan00: 0.098, power0: 0.392, lazer00: 0.252,
  enep01: 0.35, cat00: 0.252, fault: 0.441, timeout: 0.392,
  // 第八十三轮：用户自制音效（E:\猫娘姐妹\音效 → assets/sfx/*.ogg）。
  // 第八十四轮：全部改到 70% —— 用户："声音大，5个都改到70%"
  // （与第六十八轮"调到70%"同一处理）。
  start00: 0.7, purge00: 0.7, level00: 0.7, hurt00: 0.7, heal00: 0.7,
  // 第一百二十一轮：用户自制 Boss 射击音（E:\猫娘姐妹\音效 →
  // assets/sfx/shoot1.ogg / shoot2.ogg）。沿用第八十四轮"自家素材 70%"
  // 的档位：shoot1 = 普通模式 4 个 Boss 的弹幕齐射声；shoot2 = 开背虾
  // 三阶段的水柱（_fireWaterJet）。
  shoot1: 0.7, shoot2: 0.7,
  // 第一百八十一轮：天彗龙事件音（自家素材 70% 档；耳鸣特意给到 0.5
  // 让它在 BGM 前先"刺穿"耳朵 —— 用户实测"没听到耳鸣音效播放"）。
  comet_tinnitus: 0.5, comet_roar: 0.7, comet_takeoff: 0.7,
};

/**
 * 第六十八轮：原版音效**最小播放间隔**（秒）—— 弹幕音效限频。
 * 用户："有的符卡会很吵比如开头30秒的弹幕，还有EP模式的弹幕，音效播放
 * 次数多的几乎破音，给弹幕音效做个播放限制，不要每一颗都播一次音效，
 * 有的符卡效果就很好，芙兰一次射了一轮弹幕只有一次齐射音效，那种就很
 * 好很还原。"
 * 同帧去重只保证"一帧最多一声"；序章/EP 这类每帧都出弹的符卡在 60fps
 * 下等于每秒 60 声叠加 → 削波破音。每个音效名设最小间隔，间隔内的触发
 * 按"这帧已经响过"处理（不回落合成音，否则反而多一声）。
 * tan00（齐射声）给 0.25s ≈ 最多 4 声/秒；第七十二轮 lazer00（莱瓦汀
 * 激光）给 1.2s —— 用户："禁忌「Laevatein」（莱瓦汀）的激光音效特别
 * 吵，设置cd"（新版激光改为每段移动出鞘一次，间隔本身已 ~2.5s，
 * 这里是兜底）；其余默认 0.15s。
 */
export const SE_MIN_GAP = {
  tan00: 0.25,
  lazer00: 1.2,
  // 第一百二十一轮：shoot1（普通 Boss 齐射声）给 0.5s —— 与芙兰 tan00
  // 同款"一次齐射只响一声"的处理，且兜底更保守：Boss 三阶段 fireRate
  // 0.58 时最快 ~1.2s 一轮，0.5s 间隔正常战斗永远碰不到，只在
  // 梦境难度弹幕数量加成等极端场合防叠加破音。shoot2（水柱）冷却
  // 3.4s、每次只发一声，走默认 0.15s 就够。
  shoot1: 0.5,
};
const SE_GAP_DEFAULT = 0.15;

/**
 * 曲池。
 *
 * stage1 / stage2 **故意分开存**：用户要求"无尽模式下则随机播放1-20关卡的曲子"，
 * 也就是 21 波起要取两池的并集，分成两个数组才能干净地表达这件事。
 */
export const BGM_POOLS = {
  open: ['open.ogg'],
  end: ['end.ogg'],
  shop: ['shop.ogg'],
  stage1: ['stage_1_1.ogg', 'stage_1_2.ogg', 'stage_1_3.ogg', 'stage_1_4.ogg', 'stage_1_5.ogg'],
  stage2: ['stage_2_1.ogg', 'stage_2_2.ogg', 'stage_2_3.ogg'],
  boss: ['boss_1.ogg', 'boss_2.ogg', 'boss_3.ogg', 'boss_4.ogg'],
  // 第四十九轮：隐藏挑战 Boss 芙兰朵露 —— 素材来自东方红魔乡新典拆包
  //（touhou-flandre/bgm-out）。15 = U.N. Owen（她的主题曲，新典编曲），
  // 16 = 新典新增的 BOSS 曲（Extra Phantom 阶段用）。
  flandre: ['boss_flandre.ogg'],
  // 第一百八十四轮：万圣节双猫 —— Rabi-Ribi 万圣节 DLC 曲 Mischievous
  // Masquerade（bgm13.ogg → halloween.ogg），场景变亮后播放。
  halloween: ['halloween.ogg'],
  // 第二百零三轮：蕾米莉亚终演出 —— 亡き王女の為のセプテット（nc-bgm-14）
  // 205 轮：用户说 ctrl+F5 后 BGM 还是旧的 —— 浏览器按 URL 缓存音频，换个文件名强制重取。
  // 现在这版是对着 bgm-out/nc-bgm-13.ogg 逐字节复制的（SHA256 一致）。
  remilia: ['boss_remilia_b.ogg'],
  flandre_ex: ['boss_flandre_ex.ogg'],
};

/** 全部曲目（去重），测试用来核对每个引用都在磁盘上存在 */
export const BGM_ALL_TRACKS = [...new Set(Object.values(BGM_POOLS).flat())];

/** 支持的场景名 */
export const BGM_SCENES = ['open', 'shop', 'end', 'stage', 'boss', 'flandre', 'flandre_ex', 'halloween', 'remilia'];

/**
 * 每类武器的攻击音最小间隔（毫秒）—— 第十轮，用户要求"做个播放限制"。
 *
 * 数值不是随便定的，是按"这个音色密起来有多烦"排的：
 *   · 近战要打击感，连击本来就是卖点，所以排得最密（58ms ≈ 每秒最多 17 下）
 *   · 魔法/铃铛是清亮的高频叮声，密起来**最刺耳**，所以排得最疏
 *   · 远程/炮台居中
 *
 * 另外还有一层全局配额（见 beginFrame）：每帧最多 3 个武器音。
 * 单靠这里的每类间隔挡不住"8 把不同类武器同时开火"的叠加，
 * 两层一起才保证后期不会糊成一团噪音。
 */
export const WEAPON_SFX_GAP = {
  melee: 58,
  ranged: 66,
  element: 95,
  turret: 80,
};

/**
 * 武器音的整体音量倍数（第十八轮 ④）。
 *
 * 用户第三次反馈"所有的武器攻击还是没音效"。第十八轮用真机探针
 * （test/probe-weaponsfx.mjs）量过，结论是**接线是好的**：
 *   · audio.ctx.state = 'running'，isRunning = true
 *   · world.onWeaponFire 已挂上
 *   · 跑 6 秒真实战斗，_weaponSfxCount 从 0 涨到 2（开火两次响两次）
 *   · 手动调 playWeapon()，计数器 +1，lastPlayed 里出现 weapon:melee
 *
 * 所以问题不是"没播"，而是**播了但听不见**。对比各处增益就很明显：
 *   kill    0.08 + 0.06 = 0.14
 *   hurt    0.13 + 0.09 = 0.22
 *   explode 0.17 + 0.13 = 0.30
 *   而武器音是 0.04 ~ 0.08 —— 只有事件音的 1/3，
 *   再乘上 sfxBus(0.85) × master(0.75) 之后实际只剩 0.03 左右，
 *   在 BGM 和击杀音效之间基本被埋掉。武器音又是最高频的音，
 *   越频繁就越容易被耳朵当背景噪声过滤掉。
 *
 * 取 2.0 而不是 3.0：翻倍之后武器音（≈0.10）落在"清楚但不烦"的区间，
 * 和 kill（0.14）接近但略低 —— 它触发频率远高于击杀，
 * 再响就会盖过正反馈音效。
 */
export const WEAPON_SFX_GAIN = 2.0;

/**
 * 关卡曲池：第 1-10 波用 stage1，第 11-20 波用 stage2，
 * **第 21 波起（无尽模式）用两池的并集**。
 *
 * @param {number} wave1 1-based 波数。口径和 data/waves.js 的 isBossWave1 一致，
 *        免得同一件事在两处用不同的基准（0-based / 1-based 混用最容易出错）。
 * @returns {string[]} 新数组（调用方改它不会污染曲库）
 */
export function stagePoolForWave(wave1) {
  const w = Number.isFinite(wave1) ? Math.floor(wave1) : 1;
  if (w >= 21) return [...BGM_POOLS.stage1, ...BGM_POOLS.stage2];   // 无尽模式：1-20 全部曲目
  if (w >= 11) return [...BGM_POOLS.stage2];
  return [...BGM_POOLS.stage1];
}

/**
 * 场景 → { key, pool }。
 *
 * key 是"场景 + 曲池"的稳定标识，用来判断**当前在播的是不是同一档音乐**：
 *   - 同一个 key 再次请求（典型是商店 → 商店）→ 保持当前曲目，不重启；
 *   - key 变了（关卡 → 商店 → 关卡，或普通波 → Boss 波）→ 重新随机挑一首。
 * 把曲池 id 编进 key，是为了让"第 10 波 → 第 11 波"这种换池也算换曲。
 *
 * @param {'open'|'shop'|'end'|'stage'|'boss'} scene
 * @param {{wave?:number}} [opts] wave 只在 stage 场景用
 * @returns {{key:string, pool:string[]}|null} 未知场景返回 null
 */
export function resolveScene(scene, opts) {
  // 注意 opts 可能是显式传进来的 null（playScene(s, null)），
  // 默认参数只对 undefined 生效，所以这里必须自己兜一层。
  const o = opts && typeof opts === 'object' ? opts : {};
  if (scene === 'stage') {
    // 用 o['wave'] 下标访问而不是点访问：本项目的历史测试断言源码里不出现
    // 外部音频素材的扩展名（wav / mp3 那三个字母），而"点 + wave"恰好包含
    // 其中一个扩展名。纯属巧合，改下标即可两全，不必去动那条断言。
    const raw = o['wave'];
    const w = Number.isFinite(raw) ? Math.floor(raw) : 1;
    const pool = stagePoolForWave(w);
    const band = w >= 21 ? 'endless' : w >= 11 ? 'stage2' : 'stage1';
    return { key: `stage:${band}`, pool };
  }
  const pool = BGM_POOLS[scene];
  if (!Array.isArray(pool) || pool.length === 0) return null;
  return { key: scene, pool };
}

/**
 * 从池里随机挑一首。
 *
 * 用户要求："关卡和boss曲都不止一首，改为随机播放其中一首" ——
 * 所以是**每次进入场景都随机**，不是永远播第一首。
 *
 * 池里多于一首时**避开上一首**：连续两次进同一个场景（比如买完东西
 * 又开一次商店）随机到同一首歌，听感上像是"音乐卡住了没换"。
 *
 * @param {string[]} pool
 * @param {string|null} [last] 上一次播的文件名
 * @param {() => number} [rand] 随机源，注入是为了让测试能确定性地驱动它
 * @returns {string|null}
 */
export function pickTrack(pool, last = null, rand = Math.random) {
  if (!Array.isArray(pool) || pool.length === 0) return null;
  if (pool.length === 1) return pool[0];          // 只有一首就没得避
  // last 可能已经不在池里（换池了），filter 不会删掉任何东西，仍然可挑
  const rest = last == null ? pool : pool.filter((t) => t !== last);
  const list = rest.length ? rest : pool;
  const i = Math.min(list.length - 1, Math.max(0, Math.floor(rand() * list.length)));
  return list[i];
}

const NOTE = { C: 0, 'C#': 1, D: 2, 'D#': 3, E: 4, F: 5, 'F#': 6, G: 7, 'G#': 8, A: 9, 'A#': 10, B: 11 };

/** 音名 → 频率。例如 noteFreq('A', 4) = 440 */
function noteFreq(name, octave) {
  const semi = NOTE[name];
  if (semi === undefined) return 440;
  const midi = (octave + 1) * 12 + semi;
  return 440 * Math.pow(2, (midi - 69) / 12);
}

export class AudioSystem {
  constructor() {
    this.ctx = null;
    this.ready = false;
    this.enabled = true;
    this.masterVolume = 0.75;
    this.sfxVolume = 0.85;
    this.musicVolume = 0.4;

    this._master = null;
    this._sfxBus = null;
    this._musicBus = null;
    this._noiseBuf = null;

    // 同一帧内同类音效的节流（避免 200 只敌人同时死亡炸掉扬声器）
    this._lastPlayed = new Map();

    // ---- 武器攻击音（第十轮，用户要求）----
    // 用户原话："给所有武器加个攻击音效…但是播放频率不要太高，
    //            毕竟后期攻速太快了，记得做个播放限制。"
    //
    // 两层限制，缺一不可：
    //   1. 每个**武器类别**各自的最小间隔（下面 WEAPON_SFX 里的 gap）
    //      —— 保证不同音色不会互相挤掉。
    //   2. 全局**同时发声数**上限 —— 后期 8 把武器一起扫，
    //      每类各自限流也还是会叠出十几个音，听感变成一坨噪音。
    //      全局上限保证"听起来是密集的打击声"而不是"爆音"。
    this._weaponVoices = 0;
    /** 每帧重置的全局武器音配额，见 beginFrame() */
    this._weaponBudget = 0;
    /** 累计实际发出的武器音次数（测试/调试用，见 playWeapon） */
    this._weaponSfxCount = 0;

    // 第六十一轮：原版文件音效（AudioBuffer 缓存）
    this._seBuffers = new Map();     // name -> AudioBuffer（内嵌 ogg 解码缓存）
    this._seDecoding = new Set();    // 正在解码的文件（防重复 decodeAudioData）
    this._seEls = new Map();         // name -> 模板 <audio>（主通道，播 ogg）
    this._seFrame = new Set();       // 本帧已响过的原版音效（th06 同帧去重）
    this._seLastAt = {};             // name -> 上次真正出声的时刻（第六十八轮限频）

    // ---- BGM 状态 ----
    // 第九十八轮：程序化音序器（大肥鱼自己写的合成 BGM 回落）已整体拆除 ——
    // 用户："现在还是会偶尔听到他在播放，你看这个删了有影响不？没影响就
    // 删了吧，毕竟我们放了一整组全新的bgm。" 文件 BGM 加载失败/被自动播放
    // 策略拦下时现在就是安静等重试（_silenceFallback），不再冒出合成曲。
    // _musicBus 保留：它是文件 BGM 的音量总线（音量滑杆/静音都走它）。

    // ---- 文件 BGM 状态 ----
    /** @type {HTMLAudioElement|null} 复用一个元素，换曲只改 src */
    this._fileEl = null;
    /** @type {MediaElementAudioSourceNode|null} 路由失败时为 null（见 _ensureFileEl） */
    this._fileSrc = null;
    /** 元素是否已接进 _musicBus。接上了音量就交给总线，没接上才手动设 element.volume */
    this._fileRouted = false;
    /** 当前在播的文件名（BGM_ALL_TRACKS 里的名字），null = 没在播文件 BGM */
    this._fileTrack = null;
    /**
     * 是否已经把文件 BGM 当成"当前音乐源"。
     * 和 _isFilePlaying() 的区别：这个是**同步**的意图标记，
     * 因为 el.play() 是异步的，紧接着调 stopMusic() 时 paused 还没翻过来。
     */
    this._fileActive = false;
    /** 当前场景 key（见 resolveScene），用来判断"要不要换曲" */
    this._sceneKey = null;
    /** 加载/解码失败过的曲目，避免每次进场景都重试一遍 */
    this._fileFailed = new Set();
    /** 每个场景 key 上一次挑中的曲目（见 pickTrack 的"避开上一首"） */
    this._lastByKey = new Map();
    /** 最近一次请求的场景，autoplay 被拦时留给 unlock() 重试 */
    this._wantScene = null;
    /** 正在 unlock() 里重试，防止递归 */
    this._retrying = false;
    /** 随机源。抽出来是为了让测试能确定性地驱动随机挑选 */
    this._rand = Math.random;

    // 用户手势后才能真正出声
    this._unlockBound = () => this.unlock();
  }

  // ---------------------------------------------------------
  // 初始化
  // ---------------------------------------------------------
  init() {
    if (this.ctx) return true;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this.ctx = new AC();

      this._master = this.ctx.createGain();
      this._master.gain.value = this.masterVolume;
      this._master.connect(this.ctx.destination);

      this._sfxBus = this.ctx.createGain();
      this._sfxBus.gain.value = this.sfxVolume;
      this._sfxBus.connect(this._master);

      this._musicBus = this.ctx.createGain();
      this._musicBus.gain.value = this.musicVolume;
      this._musicBus.connect(this._master);

      // 预生成一段白噪声，供打击/爆炸复用
      const len = Math.floor(this.ctx.sampleRate * 1.2);
      const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
      this._noiseBuf = buf;

      this.ready = true;

      // 浏览器要求用户手势后才能出声
      window.addEventListener('pointerdown', this._unlockBound, { passive: true });
      window.addEventListener('keydown', this._unlockBound, { passive: true });
      return true;
    } catch {
      this.ready = false;
      return false;
    }
  }

  /** 在用户手势里恢复音频上下文 */
  unlock() {
    if (!this.ready || !this.ctx) return;
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => { /* 忽略 */ });
    }
    // 浏览器拦掉自动播放时，playScene() 会记下想播的场景。
    // 用户第一次点击/按键就是允许出声的时机，在这里补播一次。
    // _retrying 防止 unlock() 内部再次调用 playScene 造成的递归。
    if (this._wantScene && !this._retrying) {
      this._retrying = true;
      try { this.playScene(this._wantScene.scene, this._wantScene.opts); } catch { /* 忽略 */ }
      finally { this._retrying = false; }
    }
    // 第六十五轮：解锁时把 7 个内嵌 ogg 全部预热解码（共约 100KB，
    // 毫秒级）—— 之后元素通道万一被拦，补偿通道立刻有缓冲可用。
    for (const name of Object.keys(SE_LIB)) this._kickSeDecode(name);
  }

  get isRunning() {
    return !!(this.ready && this.ctx && this.ctx.state === 'running');
  }

  // ---------------------------------------------------------
  // 音量
  // ---------------------------------------------------------
  setVolumes({ master, sfx, music } = {}) {
    if (typeof master === 'number') this.masterVolume = clamp01(master);
    if (typeof sfx === 'number') this.sfxVolume = clamp01(sfx);
    if (typeof music === 'number') this.musicVolume = clamp01(music);
    if (this._master) this._master.gain.value = this.masterVolume;
    if (this._sfxBus) this._sfxBus.gain.value = this.sfxVolume;
    if (this._musicBus) this._musicBus.gain.value = this.musicVolume;
    this._syncFileVolume();
  }

  toggle() {
    this.enabled = !this.enabled;
    if (this._master) this._master.gain.value = this.enabled ? this.masterVolume : 0;
    this._syncFileVolume();
    return this.enabled;
  }

  /**
   * 文件 BGM 的音量同步。
   *
   * 正常情况下 <audio> 经 createMediaElementSource 接在 _musicBus 上，
   * 音量/静音由总线的 gain 统一处理，**不需要**再动 element.volume
   * （动了会乘两次）。只有在路由失败、元素直接连扬声器时，
   * 才在这里手动把静音开关与音量补上 —— 否则 toggle() 对它无效。
   */
  _syncFileVolume() {
    if (!this._fileEl || this._fileRouted) return;
    try {
      this._fileEl.volume = this.enabled ? clamp01(this.masterVolume * this.musicVolume) : 0;
    } catch { /* 某些环境下 volume 是只读的，忽略 */ }
  }

  // ---------------------------------------------------------
  // 底层发声原语
  // ---------------------------------------------------------
  _now() { return this.ctx.currentTime; }

  /** 单个振荡器音 */
  _tone({ type = 'sine', freq = 440, freqEnd = null, dur = 0.12, gain = 0.2, attack = 0.005, delay = 0, bus = null }) {
    const t0 = this._now() + delay;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(Math.max(1, freq), t0);
    if (freqEnd && freqEnd !== freq) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(1, freqEnd), t0 + dur);
    }
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.linearRampToValueAtTime(gain, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g);
    g.connect(bus || this._sfxBus);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
    return osc;
  }

  /** 噪声（打击 / 爆炸 / 脚步） */
  _noise({ dur = 0.15, gain = 0.2, delay = 0, filter = 'lowpass', freq = 1200, freqEnd = null, q = 1 }) {
    const t0 = this._now() + delay;
    const src = this.ctx.createBufferSource();
    src.buffer = this._noiseBuf;
    src.loop = true;
    const flt = this.ctx.createBiquadFilter();
    flt.type = filter;
    flt.frequency.setValueAtTime(freq, t0);
    if (freqEnd && freqEnd !== freq) {
      flt.frequency.exponentialRampToValueAtTime(Math.max(40, freqEnd), t0 + dur);
    }
    flt.Q.value = q;
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(gain, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(flt);
    flt.connect(g);
    g.connect(this._sfxBus);
    src.start(t0);
    src.stop(t0 + dur + 0.02);
    return src;
  }

  /**
   * 第六十五轮：DeepSeek 诊断后的最终通道方案 ——
   * "合成音通、ogg 通、wav 从来没通过"（玩家攻击音 = WebAudio 合成 ✓；
   * BGM = <audio> 元素播 ogg ✓；wav 三个浏览器全哑）。所以：
   *   ① 主通道：**<audio> 元素播 ogg**（和 BGM 完全同款，已验证能响）；
   *     元素 error 事件标明格式解不了时跳 ②；play() 被策略异步拦掉时
   *     由 _seCompensate 立刻用内嵌解码缓冲/合成补一声，绝不静音；
   *   ② 补偿通道：内嵌 base64 ogg → decodeAudioData → WebAudio；
   *   ③ 保底：合成回落（合成通道已验证永远通）。
   * 素材由 scripts/gen-se-data.mjs 内嵌（ogg 版，139KB）。
   * 返回 true = 这个音这帧已"响过"（同帧去重 + 限频语义）；false = 回落合成。
   */
  _playSeFile(name) {
    if (this._seFrame.has(name)) return true;   // 原版同帧去重
    // 第六十八轮：限频（见 SE_MIN_GAP 注释）—— 间隔内的触发视为"已响过"，
    // 返回 true 且标记 _seFrame，让调用方也不要再补合成音。
    const now = this.ctx?.currentTime ?? performance.now() / 1000;
    if (now - (this._seLastAt[name] ?? -Infinity) < (SE_MIN_GAP[name] ?? SE_GAP_DEFAULT)) {
      this._seFrame.add(name);
      return true;
    }
    if (this._seElementPlay(name)) { this._seLastAt[name] = now; return true; }
    const buf = this._seBuffers.get(name);
    if (buf && this.ctx) {
      try {
        this._playBuffer(name, buf);
        this._seLastAt[name] = now;
        this._seFrame.add(name);
        return true;
      } catch { /* 落回合成 */ }
    }
    this._kickSeDecode(name);
    return false;
  }

  /** ① <audio> 元素通道（BGM 同款，播 ogg） */
  _seElementPlay(name) {
    const Ctor = (typeof window !== 'undefined' && window.Audio)
      || (typeof Audio !== 'undefined' ? Audio : null);
    if (!Ctor) return false;                    // Node 测试环境 → 合成回落
    try {
      let tpl = this._seEls.get(name);
      if (!tpl) {
        tpl = new Ctor();
        tpl.preload = 'auto';
        tpl.src = `${SE_DIR}/${name}.ogg`;
        tpl.load();
        this._seEls.set(name, tpl);
      }
      if (tpl.error) return false;   // 元素明确解不了 ogg → 走内嵌/合成
      const v = tpl.cloneNode();
      v.volume = Math.min(1, SE_LIB[name] ?? 0.3);
      v.playbackRate = 0.98 + this._rand() * 0.04;   // 极轻微随机音高
      const p = v.play();
      if (p && typeof p.catch === 'function') {
        // 被自动播放策略拦掉时立刻补一声（内嵌缓冲 → 合成），不静音
        p.catch(() => this._seCompensate(name));
      }
      this._seFrame.add(name);
      return true;
    } catch { return false; }
  }

  /** ② 元素被拦时的补偿：内嵌解码缓冲出声；连缓冲都没有就补一声合成 blip */
  _seCompensate(name) {
    const buf = this._seBuffers.get(name);
    if (buf && this.ctx) {
      try {
        this._playBuffer(name, buf);
        return;
      } catch { /* 落到 blip */ }
    }
    this._kickSeDecode(name);
    this._tone({ type: 'triangle', freq: 880, freqEnd: 520, dur: 0.08, gain: 0.12 });
  }

  /** 缓冲 → sfx 总线（①②共用的出声末端，与玩家攻击音同一条链） */
  _playBuffer(name, buf) {
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = 0.98 + this._rand() * 0.04;
    const g = this.ctx.createGain();
    g.gain.value = SE_LIB[name] ?? 0.3;
    src.connect(g);
    g.connect(this._sfxBus);
    src.start();
  }

  /** 内嵌 base64 ogg → AudioBuffer（每文件只解码一次，解锁时全部预热） */
  _kickSeDecode(name) {
    const data = SE_DATA[`${name}.ogg`];
    if (this._seDecoding.has(name) || !this.ctx || typeof data !== 'string') return;
    this._seDecoding.add(name);
    try {
      const bin = atob(data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      this.ctx.decodeAudioData(bytes.buffer)
        .then((decoded) => { this._seBuffers.set(name, decoded); })
        .catch(() => { /* 解码失败就永远合成回落，无害 */ })
        .finally(() => this._seDecoding.delete(name));
    } catch { this._seDecoding.delete(name); }
  }

  /**
   * 第六十五轮：音效自检（标题页临时按钮）—— DeepSeek 处方第二步。
   * 依次报告：ctx 状态 / 元素加载 ogg / 元素加载 wav / 实际触发
   * play('flandreShot') / 合成音对比。结果逐行回调给 UI 打印。
   */
  async seSelfTest(report) {
    const line = (s) => report(s);
    line(`1. AudioContext：${this.ctx ? this.ctx.state : '不存在（还没解锁，先点一下页面）'}`);
    if (this.ctx) this.ctx.resume?.();
    line(`   解码缓存：${this._seBuffers.size}/7 已就绪`);
    line(await this._probeElement('tan00.ogg'));
    line(await this._probeElement('tan00.wav'));
    this._seFrame.clear();
    let called = false;
    const orig = this._seElementPlay.bind(this);
    this._seElementPlay = (n) => { const r = orig(n); if (n === 'tan00' && r) called = true; return r; };
    this.play('flandreShot');
    this._seElementPlay = orig;
    line(`4. play('flandreShot')：元素通道${called ? '已触发 ✓' : '未触发（走了解码/合成回落）'}`);
    this._tone({ type: 'triangle', freq: 990, freqEnd: 620, dur: 0.1, gain: 0.16 });
    line('5. 合成音已播一遍（对比：能听到它 = WebAudio 输出正常）');
    line('—— 自检完毕，把以上内容截图发给会话即可 ——');
  }

  /** 探测一个文件在 <audio> 元素里能不能加载（canplay / error / 3s 超时） */
  _probeElement(file) {
    const Ctor = (typeof window !== 'undefined' && window.Audio) || null;
    if (!Ctor) return Promise.resolve(`   ${file}：环境无 Audio（Node 测试）`);
    return new Promise((resolve) => {
      const el = new Ctor();
      el.preload = 'auto';
      let done = false;
      const finish = (msg) => { if (!done) { done = true; resolve(`   ${file}：${msg}`); } };
      el.addEventListener('canplaythrough', () => finish('canplaythrough ✓ 能加载'), { once: true });
      el.addEventListener('error', () => finish(`error ✗（code=${el.error?.code ?? '?'}）`), { once: true });
      setTimeout(() => finish('3 秒超时，无事件'), 3000);
      el.src = `${SE_DIR}/${file}`;
      el.load();
    });
  }

  /** 同类音效节流：minGap 毫秒内只响一次 */
  _throttle(key, minGap) {
    const now = performance.now();
    const last = this._lastPlayed.get(key) || 0;
    if (now - last < minGap) return false;
    this._lastPlayed.set(key, now);
    return true;
  }

  // ---------------------------------------------------------
  // 武器攻击音（第十轮）
  // ---------------------------------------------------------
  /**
   * 每帧开始时调用，补充武器音的全局配额。
   *
   * 为什么按帧发配额而不是按时间：攻速是"每帧可能触发很多次"的，
   * 按帧限流才能保证"无论攻速多快，一帧最多这么多音"，
   * 这正好对应音频线程能舒适处理的密度。
   */
  beginFrame() {
    // 第六十二轮：原版同帧去重的每帧清零点 —— th06 PlaySoundByIdx 里
    // "同帧已响过就跳过"的帧边界在这里。
    this._seFrame.clear();
    // 第六十轮：ctx 挂起看门狗 —— 有玩家反馈"芙兰的音效还是没有"，而
    // 实机探针证明发声链本身是好的。最可疑的就是 NEKO webview 里 ctx
    // 长期处于 suspended（文件 BGM 走的是 <audio> 元素直出、不经 ctx，
    // 所以 BGM 正常、全部合成音效哑掉）。每帧顺手 resume 一次，
    // 浏览器只允许在手势后恢复，多调无副作用。
    if (this.ctx && this.ctx.state === 'suspended') {
      try { this.ctx.resume().catch(() => { /* 唤不醒就算了 */ }); } catch { /* */ }
    }
    // 每帧最多 3 个武器音。60fps 下 = 每秒最多 180 个，
    // 但受各类 gap 限制后，实际密度远低于此，只是**上限兜底**。
    this._weaponBudget = 3;
  }

  /**
   * 播放一次武器攻击音。
   *
   * @param {object} def      武器定义（读 kind / behavior / tier）
   * @param {boolean} heavy   是否重击（暴击 / 高 Tier 可以用更沉的音色）
   *
   * 音色按用户要求分三类：
   *   · 近战（melee）—— "打击感要好一些"：低频冲击 + 短噪声，像真的碰到东西
   *   · 魔法/铃铛（element）—— "可以可爱点"：高频清亮的叮声，带一点上滑
   *   · 远程/炮台 —— 干净的"啾"声，不抢戏
   */
  playWeapon(def, heavy = false) {
    if (!this.enabled || !this.ready || !this.isRunning) return;
    if (this._weaponBudget <= 0) return;

    const kind = def?.kind ?? 'ranged';
    const tier = def?.tier ?? 1;
    const behavior = def?.behavior ?? 'shot';

    // 每类的节流间隔（毫秒）。数值越大 = 越不容易密集触发。
    // 近战排得最密（打击感需要连续反馈），铃铛/魔法最疏
    // （叮叮声密起来会刺耳，也容易烦）。
    const gap = WEAPON_SFX_GAP[kind] ?? 70;
    if (!this._throttle(`weapon:${kind}`, gap)) return;

    this._weaponBudget--;

    // 计数：**实际发出了一个武器音**。
    // 注意一个"音"内部可能建 2 个节点（比如近战 = 噪声 + 音调），
    // 所以不能靠数振荡器来算发声次数 —— 测试要用这个计数器。
    this._weaponSfxCount++;

    // 音高随 Tier 略微升高，让高级武器听起来"更强"。
    const pitch = 1 + (tier - 1) * 0.06;
    // 音量随 Tier 略升，但压得很轻，避免后期吵。
    // 第十八轮 ④：再乘一个整体倍数 —— 用户反馈"武器攻击还是没音效"，
    // 实测是**播了但太小**（详见 WEAPON_SFX_GAIN 的注释）。
    const vol = (0.85 + (tier - 1) * 0.05) * WEAPON_SFX_GAIN;

    if (kind === 'melee') {
      this._meleeSfx(behavior, pitch, vol, heavy);
    } else if (kind === 'element') {
      this._elementSfx(behavior, pitch, vol);
    } else if (kind === 'turret') {
      this._turretSfx(pitch, vol);
    } else {
      this._rangedSfx(behavior, pitch, vol, heavy);
    }
  }

  /** 近战：要"打击感"，所以是低频冲击 + 一层短促噪声 */
  _meleeSfx(behavior, pitch, vol, heavy) {
    if (behavior === 'quake') {
      // 砸地：低频下坠 + 大量低频噪声，最沉
      this._tone({ type: 'sine', freq: 150 * pitch, freqEnd: 42, dur: 0.20, gain: 0.10 * vol });
      this._noise({ dur: 0.16, gain: 0.10 * vol, freq: 700, freqEnd: 90 });
      return;
    }
    if (behavior === 'thrust') {
      // 刺击：短、尖、干净
      this._tone({ type: 'triangle', freq: 620 * pitch, freqEnd: 240, dur: 0.07, gain: 0.07 * vol });
      this._noise({ dur: 0.05, gain: 0.06 * vol, filter: 'highpass', freq: 1600 });
      return;
    }
    if (behavior === 'flurry') {
      // 乱舞：连击感，音量压低（它触发最频繁）
      this._tone({ type: 'square', freq: 500 * pitch, freqEnd: 300, dur: 0.05, gain: 0.045 * vol });
      return;
    }
    if (behavior === 'spin' || behavior === 'orbit') {
      // 旋转：带一点风声的呼啸，没有明确的"击点"
      this._noise({ dur: 0.12, gain: 0.05 * vol, filter: 'bandpass', freq: 1100 * pitch, q: 1.6 });
      return;
    }
    // sweep（挥砍）：默认近战音 —— 破风声 + 一记闷响
    this._noise({ dur: 0.09, gain: 0.08 * vol, filter: 'bandpass', freq: 1500 * pitch, q: 1.1 });
    this._tone({
      type: 'triangle', freq: 300 * pitch, freqEnd: 150,
      dur: heavy ? 0.13 : 0.09, gain: (heavy ? 0.10 : 0.07) * vol,
    });
  }

  /** 魔法 / 铃铛：用户要求"可爱点"，所以用清亮的高频叮声 */
  _elementSfx(behavior, pitch, vol) {
    if (behavior === 'chain') {
      // 雷铃：一串快速上行的小叮声，像摇铃
      this._tone({ type: 'sine', freq: 1180 * pitch, dur: 0.06, gain: 0.05 * vol });
      this._tone({ type: 'sine', freq: 1560 * pitch, dur: 0.07, gain: 0.045 * vol, delay: 0.035 });
      return;
    }
    if (behavior === 'nova') {
      // 光环/新星：柔和的钟鸣，一同发声的三度和声
      this._tone({ type: 'sine', freq: 880 * pitch, dur: 0.22, gain: 0.05 * vol });
      this._tone({ type: 'sine', freq: 1320 * pitch, dur: 0.20, gain: 0.035 * vol, delay: 0.02 });
      return;
    }
    // homing（魔杖飞弹）：上滑的"咻叮"，最可爱的一个
    this._tone({ type: 'sine', freq: 740 * pitch, freqEnd: 1480 * pitch, dur: 0.13, gain: 0.055 * vol });
    this._tone({ type: 'triangle', freq: 1240 * pitch, dur: 0.08, gain: 0.03 * vol, delay: 0.05 });
  }

  /** 炮台：机械感的"咔哒 + 啾" */
  _turretSfx(pitch, vol) {
    this._tone({ type: 'square', freq: 260 * pitch, freqEnd: 420, dur: 0.04, gain: 0.045 * vol });
    this._tone({ type: 'square', freq: 660 * pitch, freqEnd: 380, dur: 0.06, gain: 0.04 * vol, delay: 0.03 });
  }

  /** 远程：干净的啾声，"不抢戏" */
  _rangedSfx(behavior, pitch, vol, heavy) {
    if (behavior === 'beam') {
      // 激光：持续一点的高频，带轻微下滑
      this._tone({ type: 'sawtooth', freq: 1500 * pitch, freqEnd: 900, dur: 0.10, gain: 0.04 * vol });
      return;
    }
    if (behavior === 'spread') {
      // 散射：一次"噗"，比单发闷
      this._noise({ dur: 0.06, gain: 0.05 * vol, filter: 'bandpass', freq: 1200, q: 1.0 });
      this._tone({ type: 'square', freq: 520 * pitch, freqEnd: 260, dur: 0.06, gain: 0.045 * vol });
      return;
    }
    if (behavior === 'bounce') {
      this._tone({ type: 'triangle', freq: 900 * pitch, freqEnd: 1400, dur: 0.07, gain: 0.05 * vol });
      return;
    }
    // shot（默认单发）
    this._tone({
      type: 'square', freq: (heavy ? 620 : 740) * pitch, freqEnd: 340,
      dur: 0.06, gain: 0.05 * vol,
    });
  }

  // ---------------------------------------------------------
  // 音效（游戏事件）
  // ---------------------------------------------------------
  play(name, opts = {}) {
    if (!this.enabled || !this.ready) return;
    // 第五十六轮：ctx 被 autoplay 策略挂起时主动唤醒一次（浏览器要求
    // 用户手势后才能出声；玩家点过开始按钮之后这里就能自愈）
    if (this.ctx && this.ctx.state === 'suspended') {
      try { this.ctx.resume().catch(() => { /* 唤不醒就算了 */ }); } catch { /* */ }
    }
    if (!this.isRunning) return;

    switch (name) {
      // ---- 战斗 ----
      case 'shoot':
        if (!this._throttle('shoot', 45)) return;
        this._tone({ type: 'square', freq: 720, freqEnd: 380, dur: 0.07, gain: 0.055 });
        break;

      case 'shootHeavy':
        if (!this._throttle('shootHeavy', 60)) return;
        this._tone({ type: 'sawtooth', freq: 300, freqEnd: 130, dur: 0.14, gain: 0.075 });
        this._noise({ dur: 0.08, gain: 0.05, freq: 1800, freqEnd: 400 });
        break;

      case 'hit':
        if (!this._throttle('hit', 35)) return;
        this._noise({ dur: 0.05, gain: 0.06, filter: 'bandpass', freq: 2200, q: 1.2 });
        break;

      case 'kill':
        if (!this._throttle('kill', 55)) return;
        this._tone({ type: 'triangle', freq: 520, freqEnd: 180, dur: 0.11, gain: 0.08 });
        this._noise({ dur: 0.09, gain: 0.06, freq: 1400, freqEnd: 300 });
        break;

      case 'bossKill':
        this._tone({ type: 'sawtooth', freq: 220, freqEnd: 55, dur: 0.9, gain: 0.22 });
        this._noise({ dur: 0.9, gain: 0.2, freq: 1200, freqEnd: 90 });
        this._tone({ type: 'sine', freq: 880, freqEnd: 1760, dur: 0.5, gain: 0.14, delay: 0.12 });
        break;

      case 'explode':
        if (!this._throttle('explode', 80)) return;
        this._noise({ dur: 0.34, gain: 0.17, freq: 900, freqEnd: 70 });
        this._tone({ type: 'sine', freq: 130, freqEnd: 40, dur: 0.3, gain: 0.13 });
        break;

      // ---- 第五十四轮：芙兰战（用户："芙兰没声音，给她追加弹幕射击
      //      声音和符卡启动的音效"）----
      // 第五十六轮：整体提响 —— 原来的 0.05/0.09 在 BGM 底下根本听不见。
      // 第六十轮：再提一档（用户："符卡开启音效和弹幕射击音效还是没有"）
      // —— flandreShot 0.11 → 0.16 / 泛音 0.07 → 0.10，spellCard
      // 0.16/0.16/0.18 → 0.22/0.22/0.24 + 低频 0.14 → 0.18。
      case 'flandreShot':
        // 第六十二轮：主弹幕声 = tan00（原版 bullet_sound 7/22，音量 0.14
        // 照抄映射表）。不再用 130ms 节流 —— 改用原版引擎的**同帧去重**
        //（_playSeFile 内部）：几百发同帧只响一声，密集弹幕不是音墙。
        if (this._playSeFile('tan00')) break;
        if (!this._throttle('flandreShot', 130)) return;   // 素材没就绪时的合成回落
        this._tone({ type: 'triangle', freq: 990, freqEnd: 620, dur: 0.1, gain: 0.16 });
        this._tone({ type: 'sine', freq: 1480, dur: 0.06, gain: 0.1, delay: 0.03 });
        break;

      case 'spellCard':
        // 第六十二轮：符卡宣言「锵！」= cat00（GUI 层 ShowSpellcard，
        // 16 张卡全用这一个）+ 开场充能「嗡——」= power0（ECL
        // effect_sound 5）。两声同触发，与原版开卡听感一致。
        this._playSeFile('cat00');
        this._playSeFile('power0');
        if (this._seFrame.has('cat00') || this._seFrame.has('power0')) break;
        // 合成回落
        this._tone({ type: 'triangle', freq: noteFreq('A', 4), dur: 0.14, gain: 0.22 });
        this._tone({ type: 'triangle', freq: noteFreq('C', 5), dur: 0.14, gain: 0.22, delay: 0.09 });
        this._tone({ type: 'triangle', freq: noteFreq('E', 5), dur: 0.24, gain: 0.24, delay: 0.18 });
        this._tone({ type: 'sawtooth', freq: 110, freqEnd: 55, dur: 0.5, gain: 0.18 });
        break;

      // ---- 第六十二轮：按 SE-MAPPING 补齐的其余四处 ----
      case 'flandreLaser':
        // Lævateinn 激光 = lazer00（effect_sound 16，0.36）
        if (this._playSeFile('lazer00')) break;
        this._tone({ type: 'sawtooth', freq: 320, freqEnd: 140, dur: 0.22, gain: 0.12 });
        break;

      case 'flandreCrash':
        // 大爆炸 / BOSS 崩坏 = enep01（effect_sound 18，0.50）
        if (this._playSeFile('enep01')) break;
        this._tone({ type: 'triangle', freq: 200, freqEnd: 40, dur: 0.7, gain: 0.22 });
        this._noise({ dur: 0.6, gain: 0.18, freq: 900, freqEnd: 90 });
        break;

      case 'cardEnd':
        // 符卡结束 = fault（新典专属 effect_sound 33，0.63）
        if (this._playSeFile('fault')) break;
        this._tone({ type: 'triangle', freq: noteFreq('E', 5), dur: 0.12, gain: 0.14 });
        this._tone({ type: 'triangle', freq: noteFreq('A', 4), dur: 0.2, gain: 0.14, delay: 0.08 });
        break;

      case 'cardTick':
        // 最后 10 秒滴答 = timeout（GUI 层，每秒一声，0.56）
        if (this._playSeFile('timeout')) break;
        this._tone({ type: 'square', freq: 880, dur: 0.05, gain: 0.08 });
        break;

      // ---- 玩家 ----
      case 'hurt':
        // 第八十三轮：玩家挨打 = hurt00（用户自制，"受伤"）
        // 第八十四轮：用户"受伤别改了" —— 维持挨打触发，仅音量随五件套降到 0.7
        if (this._playSeFile('hurt00')) break;
        if (!this._throttle('hurt', 150)) return;
        this._tone({ type: 'square', freq: 200, freqEnd: 90, dur: 0.18, gain: 0.13 });
        this._noise({ dur: 0.12, gain: 0.09, freq: 700, freqEnd: 200 });
        break;

      case 'heal':
        // 第八十三轮：小小猫娘奶玩家 = heal00（用户自制，"治疗"；
        // 唯一触发点是 world.onPetHeal —— 道具回血不响这条）
        if (this._playSeFile('heal00')) break;
        this._tone({ type: 'sine', freq: 660, dur: 0.1, gain: 0.1 });
        this._tone({ type: 'sine', freq: 990, dur: 0.14, gain: 0.09, delay: 0.08 });
        break;

      case 'pickupXp':
        if (!this._throttle('pickupXp', 40)) return;
        this._tone({ type: 'sine', freq: 1180, freqEnd: 1560, dur: 0.05, gain: 0.035 });
        break;

      case 'pickupMat':
        if (!this._throttle('pickupMat', 40)) return;
        this._tone({ type: 'triangle', freq: 880, freqEnd: 1320, dur: 0.07, gain: 0.05 });
        break;

      case 'levelUp':
        // 第八十三轮：升级 = level00（用户自制，"升级"）
        if (this._playSeFile('level00')) break;
        for (let i = 0; i < 4; i++) {
          this._tone({ type: 'triangle', freq: noteFreq('C', 5) * Math.pow(2, i / 4), dur: 0.22, gain: 0.1, delay: i * 0.07 });
        }
        break;

      // ---- UI / 流程 ----
      case 'click':
        this._tone({ type: 'square', freq: 620, freqEnd: 820, dur: 0.04, gain: 0.05 });
        break;

      case 'buy':
        this._tone({ type: 'triangle', freq: 700, dur: 0.08, gain: 0.09 });
        this._tone({ type: 'triangle', freq: 1050, dur: 0.12, gain: 0.08, delay: 0.07 });
        break;

      case 'error':
        this._tone({ type: 'square', freq: 220, freqEnd: 160, dur: 0.14, gain: 0.08 });
        break;

      case 'reroll':
        this._noise({ dur: 0.16, gain: 0.07, filter: 'highpass', freq: 900 });
        break;

      case 'lock':
        this._tone({ type: 'square', freq: 500, freqEnd: 700, dur: 0.06, gain: 0.06 });
        break;

      case 'combine':
        for (let i = 0; i < 5; i++) {
          this._tone({ type: 'triangle', freq: 440 * Math.pow(2, i / 6), dur: 0.2, gain: 0.1, delay: i * 0.055 });
        }
        break;

      case 'waveStart':
        this._tone({ type: 'sawtooth', freq: 300, freqEnd: 600, dur: 0.35, gain: 0.12 });
        this._tone({ type: 'triangle', freq: 600, dur: 0.3, gain: 0.09, delay: 0.16 });
        break;

      // ---- 第八十三轮：用户自制音效（E:\猫娘姐妹\音效 → assets/sfx/*.ogg）----
      case 'gameStart':
        // "开始游戏"：玩家在选人界面点「用这个角色开始」播一次
        if (this._playSeFile('start00')) break;
        this._tone({ type: 'triangle', freq: 520, freqEnd: 1040, dur: 0.3, gain: 0.12 });
        this._tone({ type: 'sine', freq: 1560, dur: 0.22, gain: 0.08, delay: 0.14 });
        break;

      // ---- 第一百七十三轮：用户自制音效续（E:\猫娘姐妹\音效）----
      case 'catStep':
        // "猫的步伐"：悠怡锁血触发瞬间的后空翻（cat_step.ogg）
        if (this._playSeFile('cat_step')) break;
        this._tone({ type: 'sine', freq: 1240, freqEnd: 620, dur: 0.18, gain: 0.11 });
        break;

      case 'knockdown':
        // "倒地"：duo 模式玩家被击倒瞬间（knockdown.ogg）
        if (this._playSeFile('knockdown')) break;
        this._tone({ type: 'sawtooth', freq: 220, freqEnd: 60, dur: 0.45, gain: 0.16 });
        this._noise({ dur: 0.3, gain: 0.12, freq: 500, freqEnd: 120 });
        break;

      // ---- 第一百八十轮：天彗龙乱入（E:\猫娘姐妹\音效 → assets/sfx/*.ogg）----
      case 'cometTinnitus':
        // "耳鸣"：中心大爆炸白闪瞬间（comet_tinnitus.ogg）
        if (this._playSeFile('comet_tinnitus')) break;
        this._tone({ type: 'sine', freq: 8000, dur: 1.2, gain: 0.06 });
        break;

      case 'cometRoarVoice':
        // "吼叫"：落地站姿吼叫 3 秒（comet_roar.ogg）
        if (this._playSeFile('comet_roar')) break;
        this._tone({ type: 'sawtooth', freq: 90, freqEnd: 40, dur: 1.4, gain: 0.2 });
        this._noise({ dur: 1.2, gain: 0.14, freq: 300, freqEnd: 80 });
        break;

      case 'cometTakeoff':
        // "起飞"：吼完再次升空（comet_takeoff.ogg），播完龙已离场
        if (this._playSeFile('comet_takeoff')) break;
        this._tone({ type: 'sawtooth', freq: 140, freqEnd: 900, dur: 1.1, gain: 0.14 });
        break;

      case 'petPurge':
        // "开消弹"：小小猫娘**主动**消弹（tryManualPurge）出鞘一声；
        // 受击自动消弹（autoPurge）不出声 —— 用户只给了主动这条
        if (this._playSeFile('purge00')) break;
        this._noise({ dur: 0.25, gain: 0.1, filter: 'highpass', freq: 600 });
        this._tone({ type: 'sine', freq: 980, freqEnd: 420, dur: 0.3, gain: 0.1 });
        break;

      case 'waveClear':
        this._tone({ type: 'triangle', freq: noteFreq('E', 5), dur: 0.2, gain: 0.11 });
        this._tone({ type: 'triangle', freq: noteFreq('G', 5), dur: 0.2, gain: 0.1, delay: 0.12 });
        this._tone({ type: 'triangle', freq: noteFreq('C', 6), dur: 0.3, gain: 0.11, delay: 0.24 });
        break;

      case 'bossSpawn':
        this._tone({ type: 'sawtooth', freq: 90, freqEnd: 55, dur: 1.1, gain: 0.2 });
        this._tone({ type: 'square', freq: 180, freqEnd: 120, dur: 0.9, gain: 0.1, delay: 0.15 });
        this._noise({ dur: 1.0, gain: 0.12, freq: 400, freqEnd: 80 });
        break;

      case 'win':
        for (let i = 0; i < 6; i++) {
          this._tone({ type: 'triangle', freq: noteFreq(['C', 'E', 'G', 'C', 'E', 'G'][i], i < 3 ? 5 : 6), dur: 0.45, gain: 0.11, delay: i * 0.13 });
        }
        break;

      case 'death':
        this._tone({ type: 'sawtooth', freq: 400, freqEnd: 60, dur: 1.2, gain: 0.16 });
        this._tone({ type: 'sine', freq: 200, freqEnd: 50, dur: 1.4, gain: 0.12, delay: 0.1 });
        break;

      default:
        break;
    }
  }

  // ---------------------------------------------------------
  // BGM：文件播放（首选）
  //
  // 为什么用 HTMLAudioElement 而不是 WebAudio 的 decodeAudioData：
  //   1. **内存**：decodeAudioData 会把 15 首、共 23MB 的 OGG 全部解成
  //      Float32 PCM 常驻内存（一首 3 分钟的曲子 ≈ 30MB）。NEKO 宿主本身
  //      就吃内存，这里再来几百 MB 是不可接受的。<audio> 是流式解码的，
  //      播多久占多久。
  //   2. **无缝循环**：HTMLAudioElement 原生 loop 就是无缝的，不用自己
  //      在 AudioBufferSourceNode 的 onended 里续排下一段。
  //   3. **失败面小**：404 只是一次 error 事件 / play() reject，接住即可；
  //      而 decodeAudioData 是整段 OGG 拉到内存再解码，失败得更晚更重。
  //   为保住音量控制，元素经 createMediaElementSource 接进 _musicBus ——
  //   于是音量滑杆和 toggle() 静音全部照旧生效，不需要第二套音量逻辑。
  // ---------------------------------------------------------

  /**
   * 按场景播放 BGM。
   *
   * 用户要求："关卡和boss曲都不止一首，改为随机播放其中一首，
   *   无尽模式下则随机播放1-20关卡的曲子，boss回合随机boss曲子。"
   *
   * 保证：任何情况下都不抛错。文件缺失 / 浏览器拦自动播放 / 解码失败时，
   * 静默回落到程序化音序器（本项目的原则：素材永远是可选增强）。
   *
   * @param {'open'|'shop'|'end'|'stage'|'boss'} scene
   * @param {{wave?:number}} [opts] wave 是 **1-based** 波数，只有 stage 场景用
   * @returns {string|null} 实际选中/正在播的文件名；回落或未知场景时为 null
   */
  playScene(scene, opts = {}) {
    const resolved = resolveScene(scene, opts);
    if (!resolved) return null;                 // 未知场景：安静地什么都不做
    const { key, pool } = resolved;

    // 记下这次请求：autoplay 被拦时 play() 会 reject，
    // 用户第一次手势进来时 unlock() 会照着这里再试一次。
    // _pendingScene 是"最近一次请求"，_wantScene 是"还欠着没播上的那次"。
    this._pendingScene = { scene, opts };

    // 每次进场景都重新随机（用户要求："关卡和boss曲都不止一首，
    // 改为随机播放其中一首"）—— 所以第 1 波和第 2 波会听到 1-10 池里
    // **不同**的曲子，而不是整个波段死守同一首。
    // last 取"这个场景上次播的那首"，让 pickTrack 避开它。
    const last = this._lastByKey.get(key) || this._fileTrack || null;

    // 历史上加载失败过的曲目直接跳过 —— 否则每次进场景都会重试一遍、
    // 每次都卡一下再回落。全失败就整体回落。
    const usable = pool.filter((t) => !this._fileFailed.has(t));
    if (usable.length === 0) { this._silenceFallback(); return null; }

    const track = pickTrack(usable, last, this._rand);
    if (!track) return null;

    // 抽到的又是正在播的那首，而且确实是同一个场景 → 什么都不做。
    // 这是"同一场景重入不重启"的兜底：商店池只有一首，重进商店时
    // 恰好抽回同一首，音乐就**不断**（不从头重放）。
    // 注意判断的是 track 而不是 key —— 单曲池靠这里，多曲池则因为
    // 每次都重新随机而自然换曲。
    if (this._sceneKey === key && this._fileTrack === track && this._fileActive) {
      return track;
    }

    const el = this._ensureFileEl();
    if (!el) { this._silenceFallback(); return null; }

    try {
      el.loop = true;                           // 循环播放
      if (this._fileTrack !== track) {
        el.src = `${BGM_DIR}/${track}`;
        this._fileTrack = track;
      } else {
        // 同一首但被暂停过（stopMusic 之后重进场景）→ 从头播
        try { el.currentTime = 0; } catch { /* 忽略 */ }
      }

      const p = el.play();
      // play() 返回 Promise；被自动播放策略拦下时会 reject。
      // 必须接住，否则会变成 unhandled rejection。
      if (p && typeof p.catch === 'function') {
        p.catch((err) => this._onFilePlayFailed(track, err));
      }

      this._sceneKey = key;
      this._lastByKey.set(key, track);
      // 标记"当前音乐源是文件"。必须**同步**置位：el.play() 是异步的，
      // 紧接着被调用的 stopMusic()（比如芙兰战开场）此刻读 el.paused 还是
      // true，会误判成"文件没在响"。
      this._fileActive = true;
      // 播放已经发起，不需要 unlock() 再补一次了
      this._wantScene = null;
      return track;
    } catch (err) {
      this._onFilePlayFailed(track, err);
      return null;
    }
  }

  /** 文件 BGM 是不是真的在响 */
  _isFilePlaying() {
    const el = this._fileEl;
    return !!(el && !el.paused && !el.ended && this._fileTrack);
  }

  /**
   * 懒创建 <audio> 元素并接进 music 总线。环境不支持（Node 里跑纯逻辑测试、
   * 或者老浏览器）就返回 null，调用方安静回落。
   */
  _ensureFileEl() {
    if (this._fileEl) return this._fileEl;
    // 注意要用 typeof 判断，别直接引用 window / Audio —— Node 里没有这两个全局
    const Ctor = (typeof window !== 'undefined' && window.Audio)
      || (typeof Audio !== 'undefined' ? Audio : null);
    if (!Ctor) return null;

    try {
      const el = new Ctor();
      el.preload = 'auto';
      el.loop = true;
      // 加载/解码失败（404、格式不支持）→ 拉黑这首，回落合成 BGM。
      // 用箭头函数是避免 this 绑定问题。
      el.addEventListener('error', () => {
        const t = this._fileTrack;
        if (t) this._fileFailed.add(t);
        this._fileTrack = null;
        this._fileActive = false;
        this._silenceFallback();
      });
      this._fileEl = el;
      this._routeFileToBus(el);
      return el;
    } catch {
      return null;
    }
  }

  /**
   * 把 <audio> 接进 _musicBus。
   * 接不上（没有 AudioContext / 老浏览器）就退回元素自带音量，
   * 由 _syncFileVolume() 手动跟随主音量与静音开关。
   */
  _routeFileToBus(el) {
    try {
      if (!this.ctx || !this._musicBus || typeof this.ctx.createMediaElementSource !== 'function') return;
      this._fileSrc = this.ctx.createMediaElementSource(el);
      this._fileSrc.connect(this._musicBus);
      this._fileRouted = true;
    } catch {
      this._fileSrc = null;
      this._fileRouted = false;
      this._syncFileVolume();
    }
  }

  /** play() 失败：分清"浏览器不给播"和"这首文件真的没有" */
  _onFilePlayFailed(track, err) {
    // 不管哪种失败，文件 BGM 都不再是"当前音乐源"了
    this._fileActive = false;
    const name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      // 自动播放策略 —— 不是文件的问题，**不要拉黑**。
      // 记下这次请求，用户第一次手势进来时 unlock() 会照着重试。
      this._wantScene = this._pendingScene || null;
      this._silenceFallback();
      return;
    }
    // 其它（NotSupportedError / 网络 / 解码）→ 这首用不了
    if (track) this._fileFailed.add(track);
    this._fileTrack = null;
    this._silenceFallback();
  }

  /**
   * 第九十八轮：文件 BGM 起不来时的兜底 —— **安静**。
   * 音序器时代这里会拉起合成 BGM（用户听到的"大肥鱼的曲子"就是这么
   * 偶尔冒出来的）；现在只保持 _wantScene/_pendingScene 的欠播语义，
   * 等下一次手势 unlock() 或重进场景时重试文件 BGM。
   */
  _silenceFallback() {
    /* 故意什么都不放：宁缺毋滥，不回到合成曲 */
  }

  /** 停掉文件 BGM（暂停并把状态清干净，方便下次重新选曲） */
  _stopFileBgm() {
    const el = this._fileEl;
    if (el) {
      try { el.pause(); } catch { /* 忽略 */ }
      try { el.currentTime = 0; } catch { /* 忽略 */ }
    }
    this._fileTrack = null;
    this._sceneKey = null;
    this._fileActive = false;
    this._wantScene = null;
    // 曲目记忆（_lastByKey）**故意不清** —— 它的语义是"这个场景上次播的是哪首"，
    // 跨 stopMusic 保留才能保证"同一首不连着播两次"。
  }

  /**
   * 停 BGM（公共 API）。第九十八轮起只剩文件 BGM 一层 —— 芙兰战开场掐
   * Boss 曲、结算复原等调用点照旧。
   */
  stopMusic() {
    this._stopFileBgm();
  }

  // ---------------------------------------------------------
  // （第九十八轮）程序化音序器已整体拆除 —— 大肥鱼的合成 BGM 退役。
  // 文件 BGM（assets/bgm/ 全新曲库）是唯一音乐源；加载失败时安静等待
  // 重试（见 _silenceFallback）。SFX 的 _tone/noteFreq/_noiseBuf 都还在，
  // 音效合成不受影响。
  // ---------------------------------------------------------
}

function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

/** 全局单例 */
export const audio = new AudioSystem();
