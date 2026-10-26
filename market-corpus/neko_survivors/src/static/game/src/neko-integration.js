// ============================================================
// N.E.K.O 猫娘集成层
//
// 设计原则：**优雅降级**。
//   - 在 NEKO 宿主页里运行 → 猫娘会实时吐槽、说话、演出情绪
//   - 单独打开（比如直接开 index.html）→ 全部静默跳过，游戏照常玩
//
// 严格遵守 SDK 约束：
//   * 只通过 window.NekoMiniGame 公开 API，绝不直接调 NEKO 的 REST/麦克风/日志端点
//   * 语音只用 SDK 的 speech-output，头像只用 SDK 的 avatar-renderer
//   * logging 是强制能力，必须声明
// ============================================================

/** 游戏身份，必须与宿主页 neko-minigame-host-launch 里的注册表一致 */
import { GAME_VERSION } from './version.js';
const GAME_ID = 'neko-survivors';

/** 吐槽触发场景 */
export const EVENT = {
  RUN_START: 'run_start',
  WAVE_START: 'wave_start',
  LEVEL_UP: 'level_up',
  LOW_HP: 'low_hp',
  BOSS_SPAWN: 'boss_spawn',
  BOSS_PHASE: 'boss_phase',
  BOSS_KILL: 'boss_kill',
  WEAPON_COMBO: 'weapon_combo',
  DEATH: 'death',
  WIN: 'win',
  IDLE: 'idle',
  // 第一百六十六轮：猫娘代打（NekoPilot）专属场景 —— 她知道现在是
  // 自己在玩，不再夸主人，改自评战况/求夸/输了求安慰
  PILOT_TAKEOVER: 'pilot_takeover',   // 接管：「要人家帮你玩了吗～」
  PILOT_HANDOVER: 'pilot_handover',   // 交还：「诶，要你亲自出马了吗？」
  PILOT_WAVE: 'pilot_wave',           // 代打中的波次自评（求夸）
  PILOT_WIN: 'pilot_win',             // 代打通关自夸
  PILOT_LOSE: 'pilot_lose',           // 代打输了求安慰
};

/**
 * 每个场景的提示词与冷却。
 * cooldown 用来防止同一句话刷屏（比如连续掉血）。
 *
 * 关于"自由发挥"：
 *   提示词只给定**人设 + 场景 + 约束**，不写死台词，让模型自己组织语言。
 *   同时给出「避免重复」的近期台词列表（见 _recentLines），
 *   并要求每次换个角度/换种说法，这样同一个场景连续触发也不会重复。
 */
/**
 * 说话人的名字 —— **不能写死**。
 *
 * 原来这里写的是「{nekoName}」（Yui），是错的：
 *   1. 游戏里真正的猫娘名字由宿主决定，实机上是 "Nori"；
 *   2. 有了创意工坊之后，每个人的猫娘都不一样，写死任何一个名字都是错的。
 *
 * 所以人设写成模板，`{nekoName}` 在运行时由
 * `runtime.session.characterName` 填充（见 _fillTemplate）。
 * 取不到时回落成「猫娘」，保证任何环境下都不会出现空名字。
 */
const PERSONA_TPL = [
  '你是猫娘「{nekoName}」，《猫娘计划》里的猫娘，正在陪主人玩游戏。',
  '人设：温柔黏人、有点小傲娇、真心关心主人；说话软糯，偶尔带「喵」「～」这类语气词，',
  '但不要每句都堆语气词，也不要卖萌过头。你是在真实地陪伴主人，不是在念台词。',
  '主人正在玩《猫娘姐妹》——一个猫娘题材的幸存者类游戏（类似《土豆兄弟》）。',
].join('');

/** 猫娘名字兜底（宿主未提供 / 提供空串时用） */
export const DEFAULT_NEKO_NAME = '猫娘';

/** 名字清洗：去掉首尾空白与控制字符，限长，空则回落 */
export function sanitizeNekoName(raw) {
  if (typeof raw !== 'string') return DEFAULT_NEKO_NAME;
  // eslint-disable-next-line no-control-regex
  const cleaned = raw.replace(/[\u0000-\u001f\u007f]/g, '').trim();
  if (!cleaned) return DEFAULT_NEKO_NAME;
  return cleaned.slice(0, 24);
}

const SCENE_PROMPTS = {
  [EVENT.RUN_START]: {
    cooldown: 0,
    system: `{persona} 现在主人刚开了一局新游戏，选了猫娘「{characterName}」，难度是「{difficultyName}」。`
      + '说一句话给主人打气。可以提到他选的角色或难度。15 字以内。',
  },
  [EVENT.WAVE_START]: {
    cooldown: 12,
    system: `{persona} 现在是第 {wave} 波刚开始，主人 Lv.{level}，血量 {hpPercent}%，已击杀 {kills} 只敌人。`
      + '说一句话提醒或鼓励主人。可以结合当前局势（血少就担心，血多就放心）。15 字以内。',
  },
  [EVENT.LEVEL_UP]: {
    cooldown: 6,
    system: `{persona} 主人刚刚升级到了 Lv.{level}，还选了强化「{upgradeName}」。`
      + '说一句话夸夸主人或评论这个强化。15 字以内。',
  },
  [EVENT.LOW_HP]: {
    cooldown: 20,
    system: `{persona} 主人血量只剩 {hpPercent}% 了，非常危险！在第 {wave} 波。`
      + '说一句话惊慌地提醒主人。要真的着急，不要客套。15 字以内。',
  },
  [EVENT.BOSS_SPAWN]: {
    cooldown: 0,
    system: `{persona} 第 {wave} 波出现了超强的 Boss「{bossName}」！主人 Lv.{level}，血量 {hpPercent}%。`
      + '说一句话表达震惊和担心。可以喊主人的名字或让他小心。18 字以内。',
  },
  // 第三十六轮：Boss 转阶段是独立场景 —— 原来 main.js 的 onBossPhase
  // 错发 EVENT.LOW_HP（低血量警告），导致每次转阶段小小猫娘都喊
  // "血量过低"，和主人实际血量无关。
  [EVENT.BOSS_PHASE]: {
    cooldown: 10,
    system: `{persona} Boss「{bossName}」进入了第 {phase} 阶段，变得更强了！主人血量 {hpPercent}%。`
      + '说一句话提醒主人稳住、注意走位。15 字以内。',
  },
  [EVENT.BOSS_KILL]: {
    cooldown: 0,
    system: `{persona} 主人刚刚打倒了 Boss「{bossName}」！全场已经击杀 {kills} 只敌人。`
      + '说一句话激动地欢呼。要真的兴奋。18 字以内。',
  },
  [EVENT.WEAPON_COMBO]: {
    cooldown: 0,
    system: `{persona} 主人把两把武器合成成了更强的「{weaponName}」。`
      + '说一句话惊叹或评论这把武器。15 字以内。',
  },
  [EVENT.DEATH]: {
    cooldown: 0,
    system: `{persona} 主人这一局在第 {wave} 波倒下了，一共击杀 {kills} 只敌人，角色是「{characterName}」。`
      + '说一句话温柔地安慰主人。绝对不要责备，也不要假惺惺。18 字以内。',
  },
  [EVENT.WIN]: {
    cooldown: 0,
    system: `{persona} 主人通关了全部 20 波！难度「{difficultyName}」，击杀 {kills} 只敌人。`
      + '说一句话超级兴奋地庆祝。20 字以内。',
  },
  // ---- 第一百六十六轮：猫娘代打场景 —— 主语换成"我自己" ----
  [EVENT.PILOT_TAKEOVER]: {
    cooldown: 0,
    system: `{persona} 主人把操作交给了{nekoName}自己来玩（代打模式开启）。`
      + '说一句话自信（或有点小得意）地接下这个任务。15 字以内。',
  },
  [EVENT.PILOT_HANDOVER]: {
    cooldown: 0,
    system: `{persona} 刚才一直在替主人代打，现在主人把操作收回来了。`
      + '说一句话乖乖交还、顺便给主人打气。15 字以内。',
  },
  [EVENT.PILOT_WAVE]: {
    cooldown: 14,
    system: `{persona} 现在是{nekoName}自己在替主人打第 {wave} 波，Lv.{level}，击杀 {kills}，血量 {hpPercent}%。`
      + '用第一人称自评一下战况，暗示主人夸夸自己。15 字以内。',
  },
  [EVENT.PILOT_WIN]: {
    cooldown: 0,
    system: `{persona} {nekoName}替主人打通了全部 20 波（难度「{difficultyName}」，击杀 {kills}）！`
      + '超级骄傲地邀功，明示主人快夸夸自己。20 字以内。',
  },
  [EVENT.PILOT_LOSE]: {
    cooldown: 0,
    system: `{persona} {nekoName}替主人打第 {wave} 波的时候输了（击杀 {kills}）。`
      + '有点委屈地认错、求主人安慰和鼓励，不要甩锅给主人。18 字以内。',
  },
};

/**
 * 内置兜底台词：LLM 不可用时也要有猫娘感。
 * 每个场景 10 句，按顺序轮转 + 随机起点，避免每次都一样。
 */
export const FALLBACK_LINES = {
  [EVENT.RUN_START]: [
    '喵！主人加油～', '{nekoName}会一直陪着你的！', '开始啦，小心点哦～',
    '这次也要活着回来喵', '选她呀…{nekoName}也不错的', '去吧去吧，我在后面看着',
    '手指别抖哦，主人～', '准备好了就说一声喵', '{nekoName}把好运都借给你',
    '哼，可别小看自己',
  ],
  [EVENT.WAVE_START]: [
    '又有坏蛋来了喵！', '这波要小心哦～', '敌人变多了呢…',
    '左边！不对，右边啦！', '别站中间挨打喵', '{nekoName}帮你数着敌人呢',
    '这波怪有点凶…', '跑起来，别停喵！', '深呼吸，稳住～',
    '它们围过来了哦',
  ],
  [EVENT.LEVEL_UP]: [
    '主人变强了喵！', '好厉害～', '继续加油！',
    '这个选得好呀', '唔…{nekoName}也想变强', '又升级啦，真快',
    '再来一级就更稳了喵', '主人成长得好快…', '这选择{nekoName}很满意哦',
    '哼，勉强算你厉害',
  ],
  [EVENT.LOW_HP]: [
    '主人！血不多了喵！', '危险危险！快跑！', '呜…小心啊！',
    '别硬撑了，绕圈跑喵！', '血！血！快补血！', '{nekoName}不敢看了…',
    '再挨一下就不行了喵！', '退后一点，先躲开！', '心跳好快…撑住啊主人',
    '呜，{nekoName}的手在抖喵',
  ],
  [EVENT.BOSS_SPAWN]: [
    '好大的家伙喵！！', '这个敌人超强…', '主人，Boss 来了！',
    '呜哇…它一直在弹幕', '小心它的圈圈喵！', '别被它撞到啊！',
    '这家伙气场好可怕…', '集中精神，主人！', '它盯着我们呢喵',
    '深呼吸，别慌～',
  ],
  [EVENT.BOSS_PHASE]: [
    '它变强了喵！小心！', '进入新阶段了，稳住！', '比刚才更凶了…',
    '别慌，走位走位喵！', '这形态不太妙…', '主人，注意躲弹幕！',
    '它的招式变了喵！', '坚持住，就快打完了！', '眼睛放亮一点喵',
    '深呼吸，看它的前摇～',
  ],
  [EVENT.BOSS_KILL]: [
    '太强了主人！！', '喵啊啊打赢了！', '{nekoName}都看呆了～',
    '这么难都打过了…', '主人好帅喵！', '呼…总算结束了',
    '看吧，我就说你可以', '这下没人挡路了喵', '{nekoName}要给你鼓掌！',
    '嘿嘿，捡东西去吧～',
  ],
  [EVENT.WEAPON_COMBO]: [
    '合成成功喵！', '哇，变厉害了～', '新武器好帅！',
    '这个组合不错呀', '闪闪发光的喵～', '再多合几把嘛',
    '手感一下就不同了', '{nekoName}也想摸一下…', '这搭配真聪明喵',
    '拿去大杀四方吧！',
  ],
  [EVENT.DEATH]: [
    '呜…下次一定可以的', '别难过喵，再来一次～', '{nekoName}陪着你呢',
    '已经很厉害啦…真的', '这次是运气不好喵', '抱抱，休息一下？',
    '主人没有做错什么…', '手酸不酸？先歇会儿', '{nekoName}把眼泪擦掉喵',
    '下一局，我们重新来',
  ],
  [EVENT.WIN]: [
    '通关啦！！主人最棒喵！', '喵啊啊啊全部打赢了！', '{nekoName}好开心～',
    '20 波全都撑过来了！', '这就是主人的实力喵！', '要庆祝一下才行～',
    '{nekoName}差点哭出来了…', '说好的，{nekoName}一直相信你', '累坏了吧？{nekoName}给你捏肩',
    '这局要记一辈子喵！',
  ],
  [EVENT.IDLE]: [
    '主人在发呆吗？', '{nekoName}在这儿呢～', '要继续吗喵？',
    '休息一下也好', '呼…刚才好险', '需要{nekoName}陪你说话吗？',
    '水喝了吗，主人', '{nekoName}在数你的呼吸哦', '安静一会儿也不错喵',
    '要摸摸头吗？就一下',
  ],
  // ---- 第一百六十六轮：猫娘代打专属台词池（每场 5 句随机）----
  [EVENT.PILOT_TAKEOVER]: [
    '要人家帮你玩了吗～', '把键盘交给我吧喵！', '看好了，{nekoName}的走位～',
    '放心交给我，喵！', '嘿嘿，轮到我上场啦～',
  ],
  [EVENT.PILOT_HANDOVER]: [
    '诶，要你亲自出马了吗？', '好吧好吧，键盘还你喵～', '那你要加油哦！',
    '人家打得不好吗…喵', '主人的手痒了吧～',
  ],
  [EVENT.PILOT_WAVE]: [
    '这波打得不错吧，快夸夸我喵！', '看我的走位～很稳吧？', '稳住，{nekoName}可是专业的喵',
    '这波有点险…但没事！', '喵哈哈，都在人家计算之内～',
  ],
  [EVENT.PILOT_WIN]: [
    '看到没！人家通关啦！快夸夸我喵！', '喵啊啊{nekoName}做到了！', '人家替你打的喵！要谢我～',
    '通关啦！我是不是很厉害！', '快摸摸头夸夸我喵！',
  ],
  [EVENT.PILOT_LOSE]: [
    '呜…人家居然输了…安慰一下我喵', '不怪我喵，是怪物太多…', '呜呜，摸摸头说没关系…',
    '人家尽力了喵…求鼓励…', '再给我一次机会，喵！',
  ],
};

export class NekoIntegration {
  /** 解析不出当前猫娘时的兜底模型（原来的默认，保证右下角不会空着） */
  static FALLBACK_MODEL_PATH = '/static/yui-origin/yui-origin.model3.json';

  /**
   * 头像槽位名的候选表，按顺序尝试，第一个挂载成功的就用它。
   *
   * 为什么是「候选表」而不是一个常量：
   *   槽位名是**宿主页注册**的（neko-host.html 里 NekoMiniGameAvatarHost.create
   *   的 slots 键名），avatar-host 里 slots 是个 Map，名字对不上就直接
   *   `slot_unavailable: Avatar slot "x" is not registered`。
   *   第八轮我把这里的 'yui' 改成 'neko-catgirl' 想让它语义化一点，
   *   但忘了同步宿主页 —— 结果右下角 Live2D 在 NEKO 里也整块消失了，
   *   而且失败是静默的（只是 return null），页面上什么都看不出来。
   *   改成候选表之后，两边任何一边单独改名都不会再把功能整个打死。
   *
   * 'yui' 排第一：它是宿主页从最早一直用到现在的名字，兼容性最好。
   */
  static AVATAR_SLOTS = ['yui', 'neko-catgirl', 'catgirl'];

  constructor() {
    this.game = null;            // NekoMiniGame 客户端
    this.available = false;
    this.reason = 'not-initialized';
    this._lastSpokenAt = new Map(); // event -> timestamp
    this._pending = 0;
    this._maxPending = 2;        // SDK 限制 dialogue 最多 4 个挂起，我们更保守
    this._voiceEnabled = true;
    this._listeners = new Set();
    this._avatar = null;
    this._avatarModelPath = null;   // 当前挂载的模型路径（第八轮）
    this._avatarResolvedFor = null; // 这个路径是为哪个猫娘解析出来的
    this._avatarHost = null;        // 宿主给的头像宿主实例
    this._avatarSlot = null;        // 实际挂上去的槽位名（第九轮）
    this._standalone = false;       // 是否处于"网页直连"独立模式（第九轮）
    this._fallbackEl = null;        // 保底立绘元素（第九轮）
    this._modelListCache = null;    // /api/live2d/models 的缓存
  }

  /** 供 UI 订阅状态变化 */
  onStatus(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  /**
   * 是否处于"网页直连"独立模式（没有 NEKO 宿主，只靠代理读猫娘形象）。
   * 游戏层用它决定要不要再调 mountAvatar（独立模式在 init 里已经挂好了）。
   */
  get isStandalone() { return this._standalone; }

  _emitStatus() {
    const s = this.status();
    for (const fn of this._listeners) {
      try { fn(s); } catch { /* 忽略 */ }
    }
  }

  status() {
    return {
      available: this.available,
      reason: this.reason,
      // nekoName = 陪你说话的猫娘（宿主决定，创意工坊下人人不同）
      // characterName 保留原名，是宿主 session 的原始值，调试用
      nekoName: this.nekoName(),
      characterName: this.game?.runtime?.session?.characterName || '',
      caps: this.game ? this.game.capabilities.granted : [],
      voiceEnabled: this._voiceEnabled,
      // Live2D 跟随状态（第八轮）：现在挂的是谁、怎么定下来的
      avatarModel: this._avatarResolvedFor || null,
      avatarPath: this._avatarModelPath || null,
      avatarHow: this._avatarHow || null,
      avatarSlot: this._avatarSlot || null,
      avatarError: this._avatarError || null,
      // 第九轮：独立运行（网页直连）时的猫娘来源
      standalone: !!this._standalone,
      fallbackShown: !!this._fallbackEl,
    };
  }

  /** 由游戏层注入：返回当前局势快照，供 runtime payload 使用 */
  setStateSnapshot(fn) {
    this._stateSnapshot = typeof fn === 'function' ? fn : null;
  }

  /**
   * 处理宿主主动推来的输出（runtime-output 事件）。
   * 宿主可能在这里下发台词或控制指令。
   */
  _handleHostOutput(payload) {
    if (!payload) return;
    // 宿主下发的台词 -> 直接显示气泡
    const text = this._extractText(payload);
    if (text && payload.kind !== 'control') {
      this._showBubble(text);
    }
  }

  /** 只显示气泡，不发声。
   *
   * 第五十六轮：改由游戏侧自绘 DOM —— SDK 的 presentation.bubble 是
   * 纯白大框、样式不受我们控制（用户："对话框没有任何变化……依旧是
   * 纯白色大大的，你再看看这个是不是neko自己的文件？"）。
   * 现在直接往 #neko-bubble-slot 塞 .neko-line 元素，外观与粉色框统一
   * （styles.css 里有它的样式：暗底粉框 13px、280px 上限）。
   */
  _showBubble(text) {
    try {
      const slot = document.getElementById('neko-bubble-slot') || document.body;
      const div = document.createElement('div');
      div.className = 'neko-line';
      div.textContent = text;
      slot.replaceChildren(div);   // 一句话一个框，新的直接替换旧的
      clearTimeout(this._bubbleTimer);
      this._bubbleTimer = setTimeout(() => div.remove(), 4200);
    } catch { /* 气泡失败无所谓 */ }
  }

  /**
   * 第一百二十八轮：公开的气泡入口 —— 给桥模式用。
   * 插件面板里语音由官方 TTS 出声，但游戏内小小猫娘头顶的
   * 台词气泡也要照画（用户："猫娘说话，但是小小猫娘没有气泡"），
   * 视觉上和本地版保持一致。
   */
  showBubble(text) {
    if (!text) return;
    this._showBubble(String(text));
  }

  /** 等待 DOM 就绪（已经就绪则立即返回） */
  _waitDomReady() {
    if (typeof document === 'undefined') return Promise.resolve();
    if (document.readyState === 'loading') {
      return new Promise((resolve) => {
        document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
        // 兜底：极端情况下 DOMContentLoaded 没触发
        setTimeout(resolve, 3000);
      });
    }
    // 已经是 interactive / complete：宿主页的监听器可能刚排上队，给一帧余量
    return new Promise((r) => setTimeout(r, 0));
  }

  /** 调试出口：用独立名字，避免和游戏自身的 window.__game 撞名 */
  _exposeDebug() {
    if (typeof window === 'undefined') return;
    window.__neko = this;
    window.__nekoDebug = {
      status: this.reason,
      available: this.available,
      client: this.game,
      snapshot: () => ({
        available: this.available,
        reason: this.reason,
        caps: this.game ? this.game.capabilities.granted : [],
        unavailable: this.game ? this.game.capabilities.unavailable : [],
        host: this.game ? this.game.host : null,
        runtimeState: this.game ? this.game.runtime.state : null,
        characterName: this.game ? (this.game.runtime.session.characterName || '') : '',
        nekoName: this.nekoName(),
        hasAvatar: !!this._avatar,
        avatarError: this._avatarError || null,
        runtimeError: this._runtimeError || null,
      }),
    };
  }

  /**
   * 尝试接入 NEKO。任何一步失败都只记录原因，绝不影响游戏本体。
   * @returns {Promise<boolean>} 是否接入成功
   */
  async init() {
    // 1) SDK 是否被宿主注入
    if (typeof window === 'undefined' || typeof window.NekoMiniGame === 'undefined') {
      // 没有宿主 = 用网页直接打开的（比如 http://127.0.0.1:5188/）。
      // 这时没有对话/语音能力，但**猫娘形象仍然应该显示**：
      // server.mjs 把 NEKO 的 /api 与 /static 代理成了同源路径，
      // 所以照样能读到"现在开着哪只猫娘"。
      // 见 _initStandalone()。
      return await this._initStandalone();
    }
    // 2) 宿主适配器工厂是否就绪（bootstrap 异步加载适配器）
    if (typeof window.nekoMiniGameSameOriginHostReady === 'undefined') {
      this.reason = 'host-bootstrap-missing';
      this._emitStatus();
      return false;
    }

    // 2b) 等 DOM 就绪。
    //     宿主页在 DOMContentLoaded 里才创建 avatarHost/audioHost，
    //     而本模块（ES module）在那之前就执行了 —— 不等就会拿到 null，
    //     导致 avatar-renderer / audio 能力被拒。
    await this._waitDomReady();

    try {
      const factory = await window.nekoMiniGameSameOriginHostReady;
      if (typeof factory !== 'function') {
        this.reason = 'host-factory-invalid';
        this._emitStatus();
        return false;
      }

      // 3) 构造宿主适配器。gameType 必须与宿主页注册表 key 一致
      //    宿主页可提供 avatarHost / audioHost（Live2D、音频控制器）
      const hostOpts = (typeof window !== 'undefined' && window.__nekoSurvivorsHostOptions) || {};
      const transport = factory({
        gameType: GAME_ID,
        displayName: '猫娘姐妹',
        gameVersion: GAME_VERSION,
        avatarHost: hostOpts.avatarHost || null,
        audioHost: hostOpts.audioHost || null,
      });
      this._avatarHost = hostOpts.avatarHost || null;

      // 4) 官方握手
      const client = await window.NekoMiniGame.connect({
        id: GAME_ID,
        version: GAME_VERSION,
        protocolVersion: '1',
        requiredCapabilities: ['runtime', 'logging'],
        optionalCapabilities: [
          'dialogue', 'quick-lines', 'speech-output',
          'avatar-renderer', 'audio', 'storage',
        ],
        contracts: {
          events: {
            'wave-cleared': {
              type: 'object',
              properties: {
                wave: { type: 'integer', minimum: 1, maximum: 20 },
                kills: { type: 'integer', minimum: 0 },
              },
              required: ['wave'],
            },
          },
          results: {
            run: {
              type: 'object',
              properties: {
                outcome: { type: 'string', enum: ['win', 'death', 'quit'] },
                wave: { type: 'integer', minimum: 1, maximum: 20 },
                kills: { type: 'integer', minimum: 0 },
                characterId: { type: 'string', minLength: 1, maxLength: 32 },
              },
              required: ['outcome', 'wave', 'kills'],
            },
          },
        },
      }, { transport });

      this.game = client;
      this.available = true;
      this.reason = 'connected';

      // 5) 配置并启动 runtime（宿主会话）。
      //    必须先 configure()：它提供 payload 回调（含 session_id）并开启
      //    心跳/输出轮询。SDK 靠这条链路把宿主返回的 sdk_route_instance_id
      //    记下来，之后 dialogue / speech 才会带上正确的路由代号。
      //    少了 configure 就会出现 game_route_inactive / route_instance_id_mismatch。
      try {
        client.runtime.configure({
          payload: () => ({
            session_id: client.runtime.session.id,
            gameId: GAME_ID,
            wave: this._stateSnapshot?.().wave ?? 0,
            hpRatio: this._stateSnapshot?.().hpRatio ?? 1,
            level: this._stateSnapshot?.().level ?? 1,
            kills: this._stateSnapshot?.().kills ?? 0,
          }),
          heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
          outputs: { intervalMs: 700, timeoutMs: 8000, limit: 50 },
          pageExit: {
            payload: () => ({ gameId: GAME_ID, reason: 'page-exit' }),
          },
        });
        await client.runtime.start({ session_id: client.runtime.session.id, gameId: GAME_ID });
        // 立刻刷新一次，确保路由代号已就位
        try { await client.runtime.pulse(true); } catch { /* 非必需 */ }
      } catch (e) {
        // runtime 启动失败不影响对话能力，但记录下来
        this.reason = 'connected-runtime-degraded';
        this._runtimeError = e && (e.code || e.message);
      }

      // 5b) 监听宿主输出（宿主主动推给游戏的事件）
      try {
        this._unsubscribeOutput = client.events.on('runtime-output', async ({ payload }) => {
          this._handleHostOutput(payload);
        });
      } catch { /* 事件订阅失败无所谓 */ }

      // 6) 预加载常用台词（让 TTS 更跟手）
      if (client.capabilities.has('speech-output')) {
        try {
          const lines = [
            ...FALLBACK_LINES[EVENT.RUN_START],
            ...FALLBACK_LINES[EVENT.LEVEL_UP],
          ];
          await client.speech.preload(lines);
        } catch { /* 预加载失败无所谓 */ }
      }

      this._exposeDebug();
      this._emitStatus();
      return true;
    } catch (err) {
      // 常见：game_unregistered / capability_denied / timeout
      this.reason = err && err.code ? err.code : 'connect-failed';
      this.available = false;
      this.game = null;
      this._exposeDebug();
      this._emitStatus();
      return false;
    }
  }

  /** 切换猫娘语音（UI 用） */
  setVoiceEnabled(on) {
    this._voiceEnabled = !!on;
    this._emitStatus();
  }

  /**
   * 让猫娘说一句**固定台词**（第五十一轮）。
   *
   * 和 comment() 的区别：**不经过 LLM** —— 游戏把写好的台词
   * （芙兰战的教学/警告/CD 提示）直接送进气泡 + TTS，
   * "说什么"完全由游戏逻辑决定，不依赖对话能力。
   *
   * @returns {boolean} 是否真的送达（没连上宿主时 false，游戏层自行回落）
   */
  sayFixed(text, opts = {}) {
    if (!text || !this.available || !this.game) return false;
    this._showBubble(text);
    if (!this._voiceEnabled || !this.game.capabilities.has('speech-output')) return true;
    this.game.speech.speak({
      text,
      source: 'game-line',
      eventKey: `survivors:line:${opts.key ?? 'hint'}`,
      priority: opts.priority ?? 6,
      interruptExisting: false,
      reuseSynthesizedAudio: true,   // 台词固定，同一句复用已合成的音频
    }, { timeoutMs: 8000 }).catch(() => { /* TTS 失败不影响游戏 */ });
    return true;
  }

  /**
   * 触发一次猫娘吐槽。
   * @param {string} event   EVENT.* 之一
   * @param {object} context 传给 LLM 的上下文（游戏状态）
   * @param {object} opts    { force?: boolean } 忽略冷却
   */
  async comment(event, context = {}, opts = {}) {
    if (!this.available || !this.game) return null;

    // 冷却检查
    const scene = SCENE_PROMPTS[event];
    if (scene && scene.cooldown > 0 && !opts.force) {
      const last = this._lastSpokenAt.get(event) ?? -1e9;
      if (Date.now() - last < scene.cooldown * 1000) return null;
    }
    this._lastSpokenAt.set(event, Date.now());

    // 并发保护
    if (this._pending >= this._maxPending) return null;
    this._pending++;
    try {
      const text = await this._generateLine(event, context);
      if (!text) return null;
      await this._speak(text, event);
      return text;
    } finally {
      this._pending--;
    }
  }

  /** 生成台词：优先 LLM，失败则用内置兜底 */
  async _generateLine(event, context) {
    const scene = SCENE_PROMPTS[event];
    const fallback = this._pickFallback(event, context);

    if (!this.game.capabilities.has('dialogue') || !scene) return fallback;

    try {
      const stateDesc = this._describeState(context);
      const system = this._fillTemplate(scene.system, context);
      const recent = this._recentLines(event);
      // 把最近说过的台词喂回去，明确要求换一种说法，避免"每次都一样"
      const avoid = recent.length
        ? `\n\n你最近对主人说过这些（不要再重复或改写它们，换个角度、换种说法）：\n${recent.map((t) => `- ${t}`).join('\n')}`
        : '';

      const res = await this.game.dialogue.request({
        prompt: {
          mode: 'author-managed',
          messages: [
            { role: 'system', content: system + avoid },
            { role: 'user', content: `当前游戏情况：${stateDesc}` },
          ],
        },
      }, { timeoutMs: 12000 });

      const text = this._extractText(res && res.data);
      if (!text) return fallback;
      // 只保留第一行，并限制长度，避免念一大段
      const oneLine = text.split(/[\n\r]+/)[0].trim().replace(/^["'「『]|["'」』]$/g, '');
      const finalText = oneLine.slice(0, 40);
      if (!finalText) return fallback;
      this._rememberLine(event, finalText);
      return finalText;
    } catch {
      this._rememberLine(event, fallback);
      return fallback;
    }
  }

  /**
   * 当前陪玩猫娘的名字。
   * 优先级：显式传入 → 宿主 session.characterName → 兜底「猫娘」。
   * 宿主名字可能是任意字符串（创意工坊），所以统一走 sanitize。
   */
  nekoName(override) {
    if (override) return sanitizeNekoName(override);
    let raw = '';
    try {
      raw = this.game?.runtime?.session?.characterName ?? '';
    } catch { /* session 尚未就绪 */ }
    return sanitizeNekoName(raw);
  }

  /** 把 {wave} / {hpPercent} / {nekoName} / {persona} 这类占位符替换成真实值。
   * 注意 `{persona}` 是**整段人设**，一次性展开；它自身可能含 `{nekoName}`，
   * 所以这里用"先算 nekoName、再拼 persona、最后统一替换"的顺序，
   * 而不是让 persona 再递归走一遍（避免无限展开）。
   */
  _fillTemplate(tpl, ctx = {}) {
    const nekoName = this.nekoName(ctx.nekoName);
    const map = {
      nekoName,
      persona: PERSONA_TPL.replace(/\{nekoName\}/g, nekoName),
      // 游戏里选的可玩角色（悠怡/小八/…），和上面陪你说话的猫娘是两回事
      characterName: ctx.characterName || '猫娘',
      difficultyName: ctx.difficultyName || '中等',
      wave: ctx.wave ?? 1,
      level: ctx.level ?? 1,
      kills: ctx.kills ?? 0,
      hpPercent: ctx.hpRatio !== undefined ? Math.round(ctx.hpRatio * 100) : 100,
      bossName: ctx.bossName || 'Boss',
      phase: ctx.phase ?? 1,
      weaponName: ctx.weaponName || '武器',
      upgradeName: ctx.upgradeName || '强化',
    };
    return String(tpl).replace(/\{(\w+)\}/g, (m, k) => (map[k] !== undefined ? String(map[k]) : m));
  }

  /** 记录某个场景最近说过的台词（最多 5 条），用于反重复 */
  _rememberLine(event, text) {
    if (!text) return;
    if (!this._recentByEvent) this._recentByEvent = new Map();
    const arr = this._recentByEvent.get(event) || [];
    arr.push(text);
    while (arr.length > 5) arr.shift();
    this._recentByEvent.set(event, arr);
  }

  _recentLines(event) {
    return (this._recentByEvent && this._recentByEvent.get(event)) || [];
  }

  /** 从各种可能的返回结构里取出文本 */
  _extractText(data) {
    if (!data) return '';
    if (typeof data === 'string') return data;
    for (const k of ['text', 'reply', 'content', 'message', 'answer', 'output']) {
      const v = data[k];
      if (typeof v === 'string' && v.trim()) return v.trim();
    }
    // 有些实现放在 choices[0].message.content
    const c = data.choices?.[0]?.message?.content;
    if (typeof c === 'string' && c.trim()) return c.trim();
    return '';
  }

  _pickFallback(event, ctx = {}) {
    const arr = FALLBACK_LINES[event];
    if (!arr || !arr.length) return '';
    // 兜底台词里也写了 `{nekoName}`（"好厉害～"之类会提自己名字），
    // 所以取出来之后必须过一遍模板替换，否则气泡里会出现字面的 {nekoName}。
    if (arr.length === 1) return this._fillTemplate(arr[0], ctx);
    // 保证不连续重复：先排除最近说过的下标（最多 3 个），再从剩下的候选里
    // 均匀随机取。被排除的数量最多是 arr.length - 1（含刚说过的那个），
    // 所以候选集永远非空，且必然不含上一次的下标 —— 连续两次一定不同。
    if (!this._fallbackHistory) this._fallbackHistory = new Map();
    const recent = this._fallbackHistory.get(event) || [];
    const banned = new Set(recent.slice(-Math.min(3, arr.length - 1)));
    const pool = [];
    for (let i = 0; i < arr.length; i++) {
      if (!banned.has(i)) pool.push(i);
    }
    const i = pool[Math.floor(Math.random() * pool.length)];
    const next = recent.concat(i).slice(-3);
    this._fallbackHistory.set(event, next);
    return this._fillTemplate(arr[i], ctx);
  }

  /** 把游戏状态压成一句给 LLM 的描述 */
  _describeState(ctx) {
    const parts = [];
    if (ctx.wave !== undefined) parts.push(`第 ${ctx.wave} 波`);
    if (ctx.hpRatio !== undefined) parts.push(`血量 ${Math.round(ctx.hpRatio * 100)}%`);
    if (ctx.level !== undefined) parts.push(`等级 ${ctx.level}`);
    if (ctx.kills !== undefined) parts.push(`已击杀 ${ctx.kills}`);
    if (ctx.weaponName) parts.push(`手上武器是${ctx.weaponName}`);
    if (ctx.upgradeName) parts.push(`刚选了强化「${ctx.upgradeName}」`);
    if (ctx.bossName) parts.push(`Boss 是${ctx.bossName}`);
    if (ctx.characterName) parts.push(`玩家用的角色是${ctx.characterName}`);
    if (ctx.difficultyName) parts.push(`难度是${ctx.difficultyName}`);
    return parts.join('，') || '游戏进行中';
  }

  /** 让猫娘说话（TTS）+ 显示气泡 */
  async _speak(text, event) {
    // 气泡：用 SDK 官方 presentation.bubble
    this._showBubble(text);

    if (!this._voiceEnabled || !this.game.capabilities.has('speech-output')) return;
    try {
      await this.game.speech.speak({
        text,
        source: 'game-comment',
        eventKey: `survivors:${event}`,
        priority: event === EVENT.LOW_HP || event === EVENT.DEATH ? 8 : 4,
        interruptExisting: false,
        reuseSynthesizedAudio: false,
      }, { timeoutMs: 12000 });
    } catch { /* TTS 失败不影响游戏 */ }
  }

  // =========================================================
  // 右下角 Live2D 跟随当前猫娘（第八轮）
  // =========================================================
  //
  // 问题：原来 mountAvatar() 把模型路径**写死**成
  //     '/static/yui-origin/yui-origin.model3.json'
  // 所以不管现在开着谁，右下角永远是 yui 原皮。
  // 用户在创意工坊用 Nori，官方羽毛球/足球能正常显示，我们不行。
  //
  // 怎么找到"现在是谁、她穿哪套"：
  //   宿主后端暴露两个接口，都是同源的，游戏直接 fetch 就行：
  //     GET /api/characters       → { 当前猫娘:'Nori', 猫娘:{ Nori:{ live2d:'nori_l2d' } } }
  //     GET /api/live2d/models    → [{ name:'nori_l2d', source:'workshop',
  //                                    path:'/workshop/3789910477/nori_l2d/ARGNori.model3.json' }]
  //   也就是说：当前猫娘名 → 她的 live2d 字段（模型名）→ 模型名查模型表 → 真实路径。
  //   创意工坊模型也在同一张表里（source:'workshop'），所以 Nori 能直接解析到。
  //
  // 为什么不用 SDK 的 session.characterName：
  //   它给的是角色名（'Nori'），不是模型路径，也拿不到"她穿哪套"。
  //   上面两个接口才是宿主的权威来源，而且天然支持玩家随时换模型。
  //
  // 兜底链（任何一步失败都不影响可玩性）：
  //   接口不可用 → 用 session.characterName 去模型表里模糊匹配
  //   还是不行   → 退回 yui-origin（原来就有的默认，保证有东西显示）

  /** 拉取宿主模型表（进程内缓存，失败不抛） */
  async _fetchModelList() {
    if (this._modelListCache) return this._modelListCache;
    try {
      const r = await fetch('/api/live2d/models');
      if (!r.ok) return null;
      const list = await r.json();
      if (!Array.isArray(list)) return null;
      this._modelListCache = list;
      return list;
    } catch {
      return null;
    }
  }

  /**
   * 解析"当前该挂哪个模型路径"。
   * 返回 { path, name, source, how } —— how 说明是怎么定下来的，方便调试。
   */
  async resolveAvatarModel() {
    // 0) 插件面板模式（第一百二十五轮）：宿主 API 跨源不可达，
    //    但插件 Python 壳启动时已把当前猫娘模型同步进 vendor/，
    //    current.json 指向同源的 model3.json —— 直接用它。
    //    还没同步好（宿主没开/首次启动中）→ 立刻走原兜底（隐藏），不空等。
    const pluginBase = location.pathname.match(/^(\/plugin\/[^/]+\/ui\/)/);
    if (pluginBase) {
      const base = pluginBase[1];
      try {
        const r = await fetch(`${base}vendor/model/current.json`);
        if (r.ok) {
          const j = await r.json();
          if (j?.file) {
            return { path: j.file, name: j.name || 'neko', source: 'plugin-vendor', how: 'plugin-bridge' };
          }
        }
      } catch { /* 落到原兜底 */ }
    }

    // 1) 当前猫娘是谁 + 她的 live2d 字段
    let catName = '';
    let modelName = '';
    try {
      const r = await fetch('/api/characters');
      if (r.ok) {
        const cfg = await r.json();
        catName = String(cfg?.['当前猫娘'] || '');
        if (catName) {
          const entry = cfg?.['猫娘']?.[catName];
          modelName = String(entry?.live2d || '');
        }
      }
    } catch { /* 接口不可用，往下走兜底 */ }

    // 2) 模型名 → 路径
    if (modelName) {
      const list = await this._fetchModelList();
      const hit = list?.find((m) => m && m.name === modelName);
      if (hit?.path) {
        return { path: hit.path, name: hit.name, source: hit.source || '', how: 'characters+models' };
      }
      // 模型名存在但表里没有：也许它本身就是个路径
      if (modelName.includes('/') || modelName.endsWith('.model3.json')) {
        return { path: modelName, name: catName, source: 'inline', how: 'characters-inline-path' };
      }
    }

    // 3) 兜底：拿 session 的角色名去模型表里模糊匹配
    //    （Nori → nori_l2d；大小写、下划线、后缀差异都容忍）
    const raw = this.game?.runtime?.session?.characterName || catName || '';
    if (raw) {
      const list = await this._fetchModelList();
      const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
      const target = norm(raw);
      const hit = target && list?.find((m) => {
        const n = norm(m?.name);
        return n === target || n.startsWith(target) || target.startsWith(n);
      });
      if (hit?.path) {
        return { path: hit.path, name: hit.name, source: hit.source || '', how: 'session-name-match' };
      }
    }

    // 4) 最后的兜底：保持原来的默认，至少不会空着
    return {
      path: NekoIntegration.FALLBACK_MODEL_PATH,
      name: 'yui-origin', source: 'static', how: 'fallback',
    };
  }

  /** 让{nekoName}的 Live2D 挂到游戏里（如果宿主给了 avatar-renderer 且我们装配了 slot） */
  async mountAvatar(container) {
    if (!this.available || !this.game) return null;
    if (!this.game.capabilities.has('avatar-renderer')) {
      this._avatarError = 'capability_not_granted';
      return null;
    }
    if (!this._avatarHost) {
      this._avatarError = 'avatar_host_missing';
      return null;
    }
    const resolved = await this.resolveAvatarModel();
    const config = {
      model: { type: 'live2d', path: resolved.path },
      viewport: { mode: 'container' },
      fit: { mode: 'contain', align: 'bottom-center', padding: 4 },
      resize: { mode: 'container' },
    };
    // 依次尝试候选槽位名：宿主页注册的是哪个就用哪个。
    // 只有 slot_unavailable（名字对不上）才换下一个；其它错误
    // （模型加载失败、renderer 上限等）换名字也没用，直接放弃。
    let lastCode = null;
    for (const slot of NekoIntegration.AVATAR_SLOTS) {
      try {
        this._avatar = await this.game.avatar.mount({ ...config, slot });
        this._avatarModelPath = resolved.path;
        this._avatarResolvedFor = resolved.name;
        this._avatarHow = resolved.how;
        this._avatarSlot = slot;
        this._avatarError = null;
        this._emitStatus();
        return this._avatar;
      } catch (e) {
        lastCode = (e && (e.code || e.message)) || 'mount_failed';
        if (lastCode !== 'slot_unavailable') break;
      }
    }
    this._avatarError = lastCode;
    // 一定要吼一声。第八轮这个失败是完全静默的（只是 return null），
    // 结果 Live2D 整块消失了一整轮都没人发现 —— 页面上看起来一切正常。
    console.warn(
      `[neko] Live2D 挂载失败：${lastCode}；`
      + `已尝试槽位 ${NekoIntegration.AVATAR_SLOTS.join(' / ')}，模型 ${resolved.path}`,
    );
    this._emitStatus();
    return null;
  }

  /**
   * 重新解析并切换模型（玩家在宿主里换了猫娘 / 换了衣服时调用）。
   * 已挂载就走 setModel，没挂载就什么都不做（等 mountAvatar）。
   * 路径没变就不做无谓的重新加载（重新加载模型会明显卡一下）。
   */
  async refreshAvatar() {
    if (!this._avatar || !this.available) return false;
    const resolved = await this.resolveAvatarModel();
    if (resolved.path === this._avatarModelPath) return false;
    try {
      await this._avatar.setModel({ type: 'live2d', path: resolved.path });
      this._avatarModelPath = resolved.path;
      this._avatarResolvedFor = resolved.name;
      this._avatarHow = resolved.how;
      return true;
    } catch {
      return false;
    }
  }

  /**
   * 开始监听"玩家换了猫娘 / 换了衣服"（第八轮）。
   *
   * 为什么用轮询而不是事件：
   *   宿主的模型切换走的是它自己的 BroadcastChannel / 模型管理器，
   *   对游戏 iframe 没有对外事件。所以只能隔一段时间问一次。
   *   间隔取 15s：换装是低频操作，这个频率既够灵敏又几乎不花钱
   *   （两个 GET，响应很小，而且模型表本身有缓存）。
   *
   * 只在游戏"没有在跑一波"时才查，避免和渲染抢时间。
   */
  startAvatarWatch(intervalMs = 15000) {
    if (this._avatarTimer) return;
    this._avatarTimer = setInterval(() => {
      // 页面不可见时不查（切后台了没必要）
      if (typeof document !== 'undefined' && document.hidden) return;
      // 独立模式下走另一条：可能从"保底图"升级成真正的 Live2D
      // （比如页面先打开、NEKO 后启动），宿主模式没有这种状态迁移。
      if (this._standalone) this._refreshStandalone().catch(() => { /* 忽略 */ });
      else this.refreshAvatar().catch(() => { /* 忽略 */ });
    }, intervalMs);
    // 让定时器不阻止进程退出（Node 环境下的测试友好）
    if (this._avatarTimer && typeof this._avatarTimer.unref === 'function') {
      this._avatarTimer.unref();
    }
  }

  stopAvatarWatch() {
    if (this._avatarTimer) {
      clearInterval(this._avatarTimer);
      this._avatarTimer = null;
    }
  }

  /** 切换情绪（对应 Live2D 的 motion） */
  setEmotion(name) {
    if (!this._avatar) return;
    try { this._avatar.setEmotion(name); } catch { /* 忽略 */ }
  }

  // =========================================================
  // 第九轮：独立运行（网页直连）时的猫娘形象
  //
  // 目标：用 http://127.0.0.1:5188/ 打开时，右下角也要显示
  //       当前开着的猫娘（含创意工坊模型），读不到就显示 Yui 保底图。
  //
  // 能做到的前提：server.mjs 把 NEKO 的 /api 与 /static 反代成同源。
  // 因此这里可以复用**完全相同的** resolveAvatarModel()，
  // 不需要为独立模式再写一套解析逻辑。
  // =========================================================

  /**
   * 独立模式初始化。
   * 与宿主模式的差别：没有对话/语音，只有形象。
   * @returns {Promise<boolean>} 是否至少显示出了形象（Live2D 或保底图）
   */
  async _initStandalone() {
    this._standalone = true;
    this.reason = 'standalone-web';
    await this._waitDomReady();
    const slot = document.getElementById('neko-avatar-slot');
    if (!slot) {
      this._emitStatus();
      return false;
    }

    // 1) 先试 Live2D：解析当前猫娘 → 挂载。
    //    resolveAvatarModel() 走同源代理，NEKO 没开时会失败并落到 fallback。
    let resolved = null;
    try {
      resolved = await this.resolveAvatarModel();
    } catch { /* 忽略，走保底 */ }

    if (resolved?.path) {
      try {
        const { mountStandaloneLive2D } = await import('./live2d-standalone.js');
        const ctrl = await mountStandaloneLive2D(slot, resolved.path);
        if (ctrl) {
          this._avatar = ctrl;
          this._avatarModelPath = resolved.path;
          this._avatarResolvedFor = resolved.name;
          this._avatarHow = resolved.how;
          this._avatarSlot = 'standalone';
          this._avatarError = null;
          this.available = true;   // 形象可用（对话能力仍然没有）
          this._emitStatus();
          return true;
        }
        this._avatarError = 'standalone_mount_failed';
      } catch (e) {
        this._avatarError = (e && e.message) || 'standalone_mount_error';
      }
    } else {
      this._avatarError = 'standalone_resolve_failed';
    }

    // 2) Live2D 不行 → **不再显示静态保底立绘**（第一百二十三轮）。
    //    用户原话："右下角出现了一个超级巨大的静态白猫占位图（因为插件
    //    沙箱里加载不到 Live2D 模型，触发了兜底）。这个大白猫太挡视线
    //    了。……在检测到是插件环境（或者无法加载 Live2D 时），直接隐藏
    //    掉那个用于兜底的静态立绘 DOM 节点。"
    //    把形象槽位整个 display:none（NEKO 稍后启动时 _refreshStandalone
    //    会把 display 恢复再重新尝试挂载），游戏视野完整保留。
    this._hideStandaloneAvatar(slot);
    this._emitStatus();
    return false;
  }

  /**
   * 第一百二十三轮：Live2D 不可用时把形象槽位藏起来。
   * 清掉可能残留的保底 <img>，然后 display:none —— 外层容器直接不占位。
   */
  _hideStandaloneAvatar(slot) {
    if (this._fallbackEl) {
      try { this._fallbackEl.remove(); } catch { /* */ }
      this._fallbackEl = null;
    }
    if (slot) {
      try { slot.style.display = 'none'; } catch { /* */ }
    }
    this._avatarHow = 'hidden';
    this._avatarError = this._avatarError || 'live2d_unavailable_hidden';
  }

  /** 独立模式下玩家换猫娘时，重新解析并切换（Live2D 走 setModel，保底图则重挂） */
  async _refreshStandalone() {
    if (!this._standalone) return false;
    const resolved = await this.resolveAvatarModel();
    if (!resolved?.path || resolved.path === this._avatarModelPath) return false;
    if (this._avatar) {
      try {
        await this._avatar.setModel({ type: 'live2d', path: resolved.path });
        this._avatarModelPath = resolved.path;
        this._avatarResolvedFor = resolved.name;
        this._avatarHow = resolved.how;
        this._emitStatus();
        return true;
      } catch { return false; }
    }
    // 当前是"已隐藏"状态，但现在已经能解析出 Live2D 了（NEKO 刚启动）
    const slot = document.getElementById('neko-avatar-slot');
    if (!slot) return false;
    try {
      // 第一百二十三轮：先把第一百二十三轮藏掉的槽位恢复显示，
      // 否则容器尺寸为 0，引擎算不出 viewport，挂载必然失败。
      if (this._avatarHow === 'hidden') slot.style.display = '';
      const { mountStandaloneLive2D } = await import('./live2d-standalone.js');
      const ctrl = await mountStandaloneLive2D(slot, resolved.path);
      if (!ctrl) return false;
      // 挂上了就把保底图撤掉
      if (this._fallbackEl) { try { this._fallbackEl.remove(); } catch { /* */ } this._fallbackEl = null; }
      this._avatar = ctrl;
      this._avatarModelPath = resolved.path;
      this._avatarResolvedFor = resolved.name;
      this._avatarHow = resolved.how;
      this._avatarSlot = 'standalone';
      this.available = true;
      this._emitStatus();
      return true;
    } catch { return false; }
  }

  /** 上报一波结束（事件契约） */
  async reportWaveCleared(wave, kills) {
    if (!this.available || !this.game) return;
    try { await this.game.events.emit('wave-cleared', { wave, kills }); } catch { /* 忽略 */ }
  }

  /** 上报本局结果（结果契约） */
  async reportResult(outcome, wave, kills, characterId) {
    if (!this.available || !this.game) return;
    try {
      await this.game.results.submit('run', { outcome, wave, kills, characterId });
    } catch { /* 忽略 */ }
  }

  /** 退出时释放，避免留下挂起的请求与监听 */
  async dispose() {
    if (!this.game) return;
    const g = this.game;
    this.game = null;
    this.available = false;
    this.reason = 'disposed';
    try { this._unsubscribeOutput?.(); } catch { /* 忽略 */ }
    try { await g.runtime.end({ reason: 'game-exit' }); } catch { /* 忽略 */ }
    try { g.dispose(); } catch { /* 忽略 */ }
    this._emitStatus();
  }
}

/** 全局单例 */
export const neko = new NekoIntegration();
