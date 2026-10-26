// ============================================================
// plugin-bridge.js — 第一百二十四轮：插件环境 ↔ 官方猫娘通道
//
// 用户转述官方开发者的说法：插件可以通过官方插件通信接口请求宿主去
// 播放语音和展示 Live2D —— 正确姿势不是前端去读 /static/ 的模型文件
// （沙箱跨域拦死），而是：
//
//   游戏页面（与插件服务同源 48916）
//     → POST /plugin/neko_survivors/hosted-ui/action/game_event
//       （同源 fetch，插件管理器自带的 action 通道）
//     → 插件 Python 壳 @plugin_entry game_event
//     → SDK push_message(visibility=["chat"], ai_behavior="respond")
//     → 官方猫娘在聊天界面生成一段播报/吐槽（自带官方 Live2D 形象 + TTS）
//
// 不在插件环境（本地浏览器 / 48911 部署）时 init() 探测失败，
// 整个桥静默禁用，零开销。
// ============================================================

/**
 * 第一百二十七轮：基址不再写死 —— 运行时从页面路径推导。
 * 游戏就跑在插件面板的 iframe 里，pathname 一定是 /plugin/<id>/ui/...；
 * 从这里截出 /plugin/<id>，再拼出 action 探活 URL，并用 new URL(...,origin)
 * 绝对化 —— 杜绝任何"相对路径被页面基址吃掉前缀"的可能。
 * （用户抓包：请求打到了 hosted-ui/action/game_event —— 少了插件前缀；
 *   无论根因是缓存还是解析，这一版都会把完整 URL 打进控制台。）
 *
 * 第一百七十六轮：**跨源桥** —— 用户实测宿主内置版（48911 的
 * /static/game/games/neko-survivors）里官方猫娘完全哑巴（聊天框没有
 * 波次播报、语音台词全哑）。根因：内置版的页面路径不是 /plugin/...，
 * 同源也不代理插件 action → 探活 404 → 桥整个禁用。这跟多人模式
 * 没有任何关系（单人在内置版一样哑），纯粹是"换了个页面环境，桥没跟上"。
 * 修法：非插件面板环境时，跨源直连本机插件管理器（48916）——实测它对
 * action/ui-info 响应带 Access-Control-Allow-Origin: <origin> 反射，
 * 浏览器跨源 fetch 放行。探活逐候选尝试，命中即锁定。
 */
function candidateBases() {
  const list = [];
  // ① 插件面板 iframe：同源相对路径（原有路径，优先）
  try {
    const m = location.pathname.match(/^(\/plugin\/[^/]+)\/ui\//);
    if (m) list.push((p) => new URL(p, location.origin).href);
  } catch { /* 忽略 */ }
  // ② 内置版 / 本地页：跨源直连本机插件管理器（48916）
  for (const host of ['127.0.0.1', 'localhost']) {
    list.push((p) => `http://${host}:48916${p}`);
  }
  return list;
}

function pluginPath(action) {
  return `/plugin/neko_survivors/hosted-ui/action/${action}`;
}

const ABS = (path) => new URL(path, location.origin).href;

export const pluginBridge = {
  enabled: false,
  /** 命中的候选 URL 构造器（init 探活后锁定） */
  _resolve: null,
  /** 各事件的上报时间（秒），客户端侧限频 */
  _last: new Map(),

  /** 探测是否在插件面板里（同源）或能跨源够到本机插件管理器，命中即启用 */
  async init() {
    const probe = '/plugin/neko_survivors/ui-info';
    for (const resolve of candidateBases()) {
      const url = resolve(probe);
      try {
        console.log('[neko-bridge] 探活 URL：', url, '（页面路径：', location.pathname, '）');
        const r = await fetch(url, { method: 'GET' });
        if (r.ok) {
          this._resolve = resolve;
          this.enabled = true;
          break;
        }
      } catch { /* 下一个候选 */ }
    }
    console.log(`[neko-bridge] 探活结果 → ${this.enabled ? `命中，桥启用` : '全部未命中，桥禁用（插件管理器没开时零开销）'}`);
    return this.enabled;
  },

  /**
   * 上报一个游戏事件给官方猫娘。
   * @param {string} event  事件类型（start / boss / end）
   * @param {string} text   给猫娘看的战况描述（她会用自己的口吻播报）
   * @param {number} [minGapSec] 同类事件最小间隔（客户端侧限频）
   */
  report(event, text, minGapSec = 30) {
    this._post('game_event', { event, text }, minGapSec, event);
  },

  /**
   * 照念台词 —— 走宿主官方 TTS（/api/game/<type>/speak 的 B 层输出通道，
   * 和内置小游戏台词同一条管线）。宠物业/天气台词、各种 _nekoSay 固定
   * 台词都从这里出声。
   * 第一百二十八轮：**去掉 CD** —— 官方 TTS 不走 LLM、完全免费，限频只会
   * 把密集台词（隐藏 Boss 战的连发提示）整句吞掉；排队/打断交给官方管线。
   */
  say(text) {
    this._post('speak', { text: String(text || '').slice(0, 300) }, 0, 'speak');
  },

  /** 统一限频 POST action（fire-and-forget；minGapSec<=0 表示不限） */
  _post(action, args, minGapSec, gapKey) {
    if (!this.enabled || !this._resolve) return;
    const now = Date.now() / 1000;
    const key = gapKey || action;
    if (minGapSec > 0) {
      const last = this._last.get(key) || 0;
      if (now - last < minGapSec) {
        console.log(`[neko-bridge] ${action} 被客户端限频吞掉（距上次 ${Math.round(now - last)}s < ${minGapSec}s）`);
        return;
      }
      this._last.set(key, now);
    }
    const url = this._resolve(`/plugin/neko_survivors/hosted-ui/action/${action}`);
    console.log(`[neko-bridge] → POST ${url}:`, args);
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args, kind: 'panel' }),
    }).then((r) => {
      if (!r.ok) {
        // ---- 第一百七十九轮：404 自愈 ----
        // 用户实测：插件文件更新后，宿主运行时仍缓存**老版入口表**
        //（"UI action 'game_event' is not a plugin entry"），桥全程 404，
        // 而面板"入口点"按钮不受影响 —— 因为那条路不走 action 注册表。
        // 宿主有 /plugin/<id>/reload（POST）会重启插件并重读 meta；
        // 收到 404/409 就自动 reload 一次再补发本条（限流 90s，防连环刷）。
        console.warn(`[neko-bridge] ${action} 响应 ${r.status}，尝试自动 reload 插件后补发…`);
        this._selfHealThen(action, args, gapKey);
      } else {
        console.log(`[neko-bridge] ${action} → 已受理`);
      }
    }).catch((e) => {
      console.warn('[neko-bridge] 请求失败：', e && e.message);
    });
  },

  /** 上次自愈时间（限流：90 秒内只 reload 一次） */
  _lastHeal: 0,

  _selfHealThen(action, args, gapKey) {
    const now = Date.now() / 1000;
    if (now - this._lastHeal < 90) return;
    this._lastHeal = now;
    const reloadUrl = this._resolve('/plugin/neko_survivors/reload');
    fetch(reloadUrl, { method: 'POST' }).then((r) => {
      if (!r.ok) throw new Error(`reload ${r.status}`);
      console.log('[neko-bridge] 插件已 reload，2 秒后补发', action);
      // 插件重启需要几秒；gapKey 已在本条发送前记录过限频，补发绕开它
      return new Promise((res) => setTimeout(res, 2000));
    }).then(() => {
      const url = this._resolve(`/plugin/neko_survivors/hosted-ui/action/${action}`);
      return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ args, kind: 'panel' }),
      });
    }).then((r) => {
      console.log(`[neko-bridge] 补发 ${action} → ${r.ok ? '已受理' : r.status}`);
    }).catch((e) => {
      console.warn('[neko-bridge] 自愈失败（去插件面板把 neko_survivors 停止再启动一次即可）：', e && e.message);
    });
  },

  /** 事件 → 给猫娘看的战况一句话（她会用自己口吻重新播报） */
  formatEvent(event, ctx = {}) {
    const LABELS = {
      run_start: '新一轮围攻开始了',
      wave_start: '进入了新的一波',
      level_up: '升了一级',
      low_hp: '血量见底了',
      boss_spawn: 'Boss 登场了',
      boss_phase: 'Boss 进入了更强的阶段',
      boss_kill: '把 Boss 打倒了',
      weapon_combo: '武器合成了更高品质',
      death: '这一轮没能撑住',
      win: '撑过了全部围攻，通关了',
      // ---- 第一百七十六轮：代打/双人模式的自评场景也有正经标签 ----
      // （duo watch 下 pilot.on 恒真，波次播报全走 pilot_wave —— 原来
      //  落到兜底"战况更新"，官方猫娘念出来干巴巴的）
      pilot_takeover: '要替主人上场代打了',
      pilot_handover: '把操作还给主人了',
      pilot_wave: '正在替主人打这一波',
      pilot_win: '替主人通关了全部围攻',
      pilot_lose: '替主人打这一波时输了',
    };
    const label = LABELS[event] || '战况更新';
    const bits = [];
    if (ctx.wave) bits.push(`第 ${ctx.wave} 波`);
    if (ctx.difficultyName) bits.push(ctx.difficultyName);
    if (ctx.characterName) bits.push(`操纵${ctx.characterName}`);
    if (ctx.bossName) bits.push(`对手是 ${ctx.bossName}`);
    // 第一百三十五轮：新波开播报不再报血量 —— 新回合开始必然满血，
    // "血量 100%"是废话（用户："每次都播报血量有点没意义……可以改成
    // 其他的"）。改成报等级（有成长感）；其它事件（低血量告警等）
    // 的血量照旧保留，那才是有信息量的时刻。
    const isWaveStart = event === 'wave_start';
    if (!isWaveStart && typeof ctx.hpRatio === 'number') bits.push(`血量 ${Math.round(ctx.hpRatio * 100)}%`);
    if (ctx.level != null) bits.push(`已到 Lv ${ctx.level}`);
    if (ctx.kills != null) bits.push(`击杀 ${ctx.kills}`);
    if (ctx.weaponName) bits.push(`主武器 ${ctx.weaponName}`);
    return `${label}（${bits.join('，')}）`;
  },
};
