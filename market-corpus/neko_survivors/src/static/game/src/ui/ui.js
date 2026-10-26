// ============================================================
// DOM UI 层
// 负责 HUD 更新与各种覆盖界面（标题/选人/升级/商店/结算）。
// 界面用回调把玩家选择交回游戏状态机，自身不持有游戏逻辑。
// ============================================================

import { fmtStat, STAT_DEFS, fmtMod } from '../core/stats.js';
import { describeStat } from '../core/stat-detail.js';
import { TIER_NAMES, TIER_COLORS, RECIPE_MAP, getWeapon } from '../data/weapons.js';
import { itemTier, ITEM_TIER_COLORS, getItem as getItemDef } from '../data/items.js';
import { BEHAVIOR_LABEL } from '../entities/weapon.js';
import { getDifficulty, difficultyList } from '../data/difficulty.js';
// 第十六轮：角色选择界面要展示"能力 + 技能"
import { getCharSkill } from '../data/char-skills.js';
import { GAME_VERSION } from '../version.js';
import { sprites } from '../core/assets.js';

/**
 * 转义要放进 HTML 属性里的文本。
 * 属性提示里会出现 `"` `'` `<` 这类字符（比如"减伤 50%"没问题，
 * 但武器描述里可能有括号和引号），不转义会把属性截断甚至注入标签。
 */
function escAttr(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, ' ');
}

/**
 * 生成一把武器的详细数据文本（用于悬浮提示）。
 *
 * 用户第五轮反馈："点击自己的武器可以查看详细数据。
 * 不然有时候搞不懂自己的武器到底啥品质。"
 *
 * 这里用的是 WeaponInstance 自己的计算方法（computeDamage /
 * computeCooldown / computeRange），所以显示的就是**实际战斗数值**，
 * 不是数据表里的裸值。
 */
function weaponDetailText(w, player) {
  const tier = w.def.tier ?? 1;
  const tName = TIER_NAMES[tier] || TIER_NAMES[1];
  const kindName = w.def.kind === 'melee' ? '近战'
    : w.def.kind === 'ranged' ? '远程'
      : w.def.kind === 'element' ? '元素'
        : w.def.kind === 'turret' ? '炮台' : '武器';
  const beh = BEHAVIOR_LABEL[w.def.behavior] || w.def.behavior;

  const lines = [
    `${w.def.name}　${tName}品质 · ${kindName} · ${beh}`,
    w.level > 1 ? `等级 Lv.${w.level}（伤害 ×${w.levelDamageMul.toFixed(2)}）` : '等级 Lv.1',
  ];

  // 实际数值（吃玩家属性加成）
  try {
    const dmg = w.computeDamage(player);
    const cd = w.computeCooldown(player);
    const range = w.computeRange(player);
    lines.push(`伤害 ${dmg.toFixed(1)}　冷却 ${cd.toFixed(2)}s　射程 ${Math.round(range)}`);
    if (cd > 0) lines.push(`单把 DPS ≈ ${(dmg / cd).toFixed(1)}`);
  } catch {
    // 缺 player 时退回到数据表裸值，不影响显示
    lines.push(`伤害 ${w.def.damage ?? '-'}　冷却 ${w.def.cooldown ?? '-'}s`);
  }

  if (w.def.desc) lines.push(w.def.desc);
  return lines.join('\n');
}

export class UI {
  constructor() {
    this.el = {
      hud: document.getElementById('hud'),
      waveNum: document.getElementById('hud-wave-num'),
      timerText: document.getElementById('hud-timer-text'),
      timerFill: document.getElementById('hud-timer-fill'),
      timerLabel: document.getElementById('hud-timer-label'),
      materials: document.getElementById('hud-materials'),
      difficulty: document.getElementById('hud-difficulty'),
      // 第一百三十八轮：血条/护盾/经验条/Lv 改由 renderer._drawHudBars
      // 纯 Canvas 绘制，这些 DOM 引用退役（容器壳保留占位防布局位移）。
      weapons: document.getElementById('hud-weapons'),
      stats: document.getElementById('hud-stats'),
      toast: document.getElementById('hud-toast'),
      overlay: document.getElementById('overlay'),
    };
    this._toasts = [];
    this._lastHud = {};
  }

  // =========================================================
  // HUD
  // =========================================================
  showHud(show = true) {
    this.el.hud.classList.toggle('hidden', !show);
  }

  updateHud(game) {
    const p = game.player;
    const e = this.el;

    // 波次
    if (this._lastHud.wave !== game.waveIndex + 1) {
      e.waveNum.textContent = String(game.waveIndex + 1);
      this._lastHud.wave = game.waveIndex + 1;
    }

    // 难度徽标（只在难度变化时更新）
    if (this._lastHud.diff !== game.difficultyId) {
      const d = getDifficulty(game.difficultyId);
      if (e.difficulty) {
        e.difficulty.textContent = d.name;
        e.difficulty.style.color = d.color;
        e.difficulty.style.borderColor = d.color;
        // 中等难度不必特别标注，避免视觉噪音
        e.difficulty.style.display = d.id === 'normal' ? 'none' : '';
      }
      this._lastHud.diff = game.difficultyId;
    }

    // 计时
    // 第五十二轮（用户反馈 2）：芙兰战期间顶部波次倒计时没有意义（waveTimeLeft
    // 已冻结，看起来像"卡在 30"）—— 用户："可以暂时让他不出现？毕竟这个
    // 到0在普通模式下就是进入下一回合了"。原波次逻辑一行不动（省得
    // "到 0 秒自动过关"之类的回归）。
    // 第九十七轮：同位置另挂一个**纯显示**的芙兰战分段倒计时 ——
    // 序章弹幕 ？？？30s / 符卡 n/4 各 30s / EP 1/3、2/3 20s、3/3 30s，
    // 数据来自 flandreFight.getDisplayTimer()，战斗结束自动隐藏。
    // 第一百四十一轮：维吉尔战同通道 —— 90 秒限时倒数（用户反馈 2：
    // "计时器全程锁定30秒显示，而不是90秒变动"）。
    const fight = game.world?.flandreFight;
    const vfight = game.world?.vergilFight;
    const disp = fight
      ? (fight.getDisplayTimer?.() ?? null)
      : (vfight ? (vfight.getDisplayTimer?.() ?? null) : null);
    const timerBox = e.timerText && e.timerText.closest('.hud-timer');
    if (timerBox) {
      timerBox.classList.toggle('hidden', !!(fight || vfight) && !disp);
      timerBox.classList.toggle('flandre', !!fight && !!disp);
    }
    if (e.timerLabel) {
      // 第一百四十三轮：空 label（维吉尔战去"斩"字）不占位
      const showLabel = !!disp && !!disp.label;
      e.timerLabel.textContent = showLabel ? disp.label : '';
      e.timerLabel.style.display = showLabel ? '' : 'none';
    }
    if (disp) {
      const left = Math.max(0, Math.ceil(disp.left));
      if (this._lastHud.timer !== `f${left}`) {
        e.timerText.textContent = String(left);
        this._lastHud.timer = `f${left}`;
      }
      e.timerFill.style.transform = `scaleX(${disp.total > 0 ? Math.max(0, disp.left / disp.total) : 0})`;
    } else {
      const remaining = Math.max(0, Math.ceil(game.waveTimeLeft));
      if (this._lastHud.timer !== remaining) {
        e.timerText.textContent = String(remaining);
        this._lastHud.timer = remaining;
      }
      const pct = game.waveDuration > 0 ? game.waveTimeLeft / game.waveDuration : 0;
      e.timerFill.style.transform = `scaleX(${pct})`;
    }

    // 185 轮：最后 5 秒计时器变红脉动（UI v2；CSS .hud-timer.urgent）
    if (timerBox) {
      const lf = Math.ceil(disp ? disp.left : game.waveTimeLeft);
      timerBox.classList.toggle('urgent', lf > 0 && lf <= 5);
    }

    // 材料
    // 第五十八轮：芙兰战的右上角被符卡名/消弹提示/EP倒计时（画布层）占用，
    // 材料框下移给它们腾位置 —— 用户："符卡显示会被金币数量盖住……
    // 让符卡的显示高于金币数。不然都看不到符卡名字。"
    document.body.classList.toggle('flandre-hud', !!game.world?.flandreFight);
    if (this._lastHud.mat !== p.materials) {
      // 第一百六十五轮：材料缩写（用户："1100金币显示1.1k"）+ NaN 兜底
      e.materials.textContent = this._fmtMats(p.materials);
      this._lastHud.mat = p.materials;
    }

    // 生命：改由 renderer._drawHudBars 纯 Canvas 绘制（第一百三十八轮）。

    // 第一百零九轮：护盾条 —— 同上退役（Canvas 侧带流光与平滑扣除）。

    // 第一百三十八轮：经验条/Lv 改由 renderer._drawHudBars 纯 Canvas 绘制，
    // DOM 版退役（原来这里每帧改 xpFill.style.width 和 level.textContent）。

    // 武器（第一百三十八轮修复：重构时误删了这行调用，装备框整个消失）
    this._renderHudWeapons(p);

    // 关键属性
    this._renderHudStats(p);
  }

  _renderHudWeapons(p) {
    const key = p.weapons.map((w) => `${w.def.id}:${w.level}`).join(',');
    if (this._lastHud.weapons === key) return;
    this._lastHud.weapons = key;

    this.el.weapons.innerHTML = p.weapons.map((w) => {
      const t = w.def.tier;
      return `<div class="hud-weapon tier-${t}" data-sprite="weapon:${w.def.id}" title="${w.def.name} Lv.${w.level}">
        <span class="w-glyph" style="color:${w.def.color}">${w.def.glyph}</span>
        ${w.level > 1 ? `<span class="w-tag">Lv${w.level}</span>` : ''}
      </div>`;
    }).join('');
    this._applySprites(this.el.weapons);
  }

  _renderHudStats(p) {
    const s = p.stats.values;
    const shown = ['damage', 'attackSpeed', 'critChance', 'armor', 'moveSpeed'];
    const key = shown.map((k) => Math.round(s[k])).join(',');
    if (this._lastHud.stats === key) return;
    this._lastHud.stats = key;

    this.el.stats.innerHTML = shown.map((k) => {
      const def = STAT_DEFS[k];
      // HUD 上的属性也能悬浮看详情（同样的说明文案）。
      // 第十六轮：把角色的幸运倍率传进去，否则萌妹看到的说明是错的
      // （写着"按 80% 计入"，实际是 96%）。
      return `<span class="hud-stat has-tip" data-tip="${escAttr(describeStat(k, s, p.stats.luckEffectMul ?? 1))}">`
        + `${def.short} <b>${fmtStat(k, s[k])}</b></span>`;
    }).join('');
    this._bindTips(this.el.stats);
  }

  /**
   * 材料数缩写（第一百六十五轮）：≥1000 显示 1.1k（≥100k 显示 123k，
   * ≥1e6 显示 1.2m）；非有限值（NaN 等脏数据）显示 0 兜底 —— 用户报过
   * "金币过多右上角显示 NaN"，显示层不再外溢。
   */
  _fmtMats(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '0';
    if (n >= 1000000) return `${(Math.round(n / 100000) / 10)}m`;
    if (n >= 1000) {
      const k = n / 1000;
      return `${k >= 100 ? Math.round(k) : Math.round(k * 10) / 10}k`;
    }
    return String(Math.floor(n));
  }

  /**
   * 飘一条提示。
   *
   * @param text 文案
   * @param kind 样式后缀（good/bad/...）
   * @param holdMs 停留时长（毫秒）。默认 1400 —— 大多数提示扫一眼就够。
   *   第十二轮第 7 项（宠物偷道具）用户要求"悬浮提示 **3~5 秒**"，
   *   因为那条提示是**异步**出现的（宠物自己决定什么时候顺走），
   *   玩家很可能正在看别的地方，1.4 秒会直接错过。所以给它开一个口子。
   */
  toast(text, kind = '', holdMs = 1400) {
    const div = document.createElement('div');
    div.className = `toast ${kind}`;
    div.textContent = text;
    // 第五十三轮：小小猫娘的台词框（"小小猫娘：…"兜底通道）透明度压到 70%，
    // 和她头顶的气泡一致 —— 用户："让小小猫娘所有对话框透明度改到70%，
    // 文字不变。别让小小猫娘成为敌人帮手。"
    if (text.startsWith('小小猫娘：')) div.style.opacity = '0.7';
    this.el.toast.appendChild(div);
    setTimeout(() => {
      div.style.transition = 'opacity .3s, transform .3s';
      div.style.opacity = '0';
      div.style.transform = 'translateY(-6px)';
      setTimeout(() => div.remove(), 320);
    }, holdMs);
  }

  /**
   * 显示 NEKO 猫娘连接状态。
   * 独立运行时（没有 NEKO 宿主）保持隐藏，不打扰玩家。
   */
  setNekoStatus(status) {
    const el = document.getElementById('neko-status');
    if (!el) return;
    if (!status || !status.available) {
      // 只有"本该能用但失败了"才提示；纯粹不在 NEKO 里就静默
      const quiet = ['sdk-not-present', 'host-bootstrap-missing', 'not-initialized',
        'standalone-web'].includes(status?.reason);
      el.classList.toggle('hidden', true);
      if (!quiet && status?.reason) {
        el.classList.remove('hidden');
        el.classList.add('warn');
        el.textContent = `猫娘未接入（${status.reason}）`;
      }
      return;
    }
    el.classList.remove('hidden');
    el.classList.remove('warn');
    // 第九轮：独立模式没有对话能力，只有形象，
    // 所以显示"某某（形象）"而不是容易误解的"某某 在线"。
    if (status.standalone) {
      const who = status.avatarModel || 'Yui';
      const isImg = status.avatarHow === 'fallback-image';
      const isHidden = status.avatarHow === 'hidden';
      el.textContent = isImg ? `${who}（本地保底图）`
        : isHidden ? '猫娘形象未连接' : `${who}（形象）`;
      el.title = isHidden
        ? '当前环境加载不到 Live2D，形象区已隐藏（不挡视野）。NEKO 启动后会自动恢复。'
        : '网页直连模式：只显示猫娘形象，没有对话/语音能力。'
          + (status.avatarPath ? `\n模型：${status.avatarPath}` : '');
      return;
    }
    const name = status.characterName || '猫娘';
    el.textContent = `${name} 在线`;
    el.title = '能力：' + (status.caps || []).join(', ');
  }

  // =========================================================
  // 覆盖层
  // =========================================================
  clearOverlay() {
    this._shopLive = null;   // 201 轮：关掉任何界面都不再让 tickShop 去重绘商店
    this.el.overlay.innerHTML = '';
  }

  _mount(html) {
    // 重渲染前先收掉旧气泡，否则会留一个孤儿挂在 body 上
    this.hideTip();
    this.el.overlay.innerHTML = html;
    const root = this.el.overlay.firstElementChild;
    this._applySprites(root);
    return root;
  }

  // =========================================================
  // 贴图（美术资源）
  //
  // 约定：任何想用贴图的容器加 `data-sprite="kind:id"`，
  // 渲染完统一走一遍 _applySprites 扫描。
  // 这样加新贴图只需要在模板里写一个属性，不用到处写 if/else。
  //
  // 关键：**没有对应贴图时什么都不做** —— 原来的字形照常显示。
  // 所以美术资源可以一批批补，任何中间状态游戏都正常。
  // =========================================================
  _applySprites(root) {
    if (!root || !sprites.ready) return 0;
    let n = 0;
    try {
      for (const el of root.querySelectorAll('[data-sprite]')) {
        const spec = el.getAttribute('data-sprite');
        const idx = spec.indexOf(':');
        if (idx <= 0) continue;
        const kind = spec.slice(0, idx);
        const id = spec.slice(idx + 1);
        if (sprites.apply(el, kind, id)) n++;
      }
    } catch { /* 贴图出问题绝不影响界面 */ }
    return n;
  }

  /** 贴图异步加载完了 —— 重绘当前界面，让已经渲染的内容用上贴图 */
  notifySpritesReady() {
    try { this._applySprites(this.el.overlay); } catch { /* 忽略 */ }
    try { this._applySprites(this.el.weapons); } catch { /* 忽略 */ }
    try { this._applySprites(this.el.stats); } catch { /* 忽略 */ }
    try { this._applySprites(document.body); } catch { /* 忽略 */ }
  }

  // =========================================================
  // 悬浮提示（第五轮新增）
  //
  // 为什么不用纯 CSS 的 ::after：
  //   数值面板是 `overflow-y: auto` 的（内容多时滚动），
  //   按 CSS 规范，一个轴设了非 visible 之后另一个轴也会变成 auto，
  //   于是绝对定位的气泡会被**裁掉**，鼠标一移上去什么都没有。
  //   所以改成"一个挂到 body 上的浮动层 + 跟随鼠标定位"，
  //   不受任何祖先的 overflow 影响。
  // =========================================================
  _ensureTip() {
    if (this._tipEl && document.body.contains(this._tipEl)) return this._tipEl;
    const el = document.createElement('div');
    el.className = 'tip-float';
    el.setAttribute('role', 'tooltip');
    document.body.appendChild(el);
    this._tipEl = el;
    return el;
  }

  /** 隐藏浮动提示 */
  hideTip() {
    if (this._tipEl) this._tipEl.classList.remove('show');
  }

  /**
   * 给容器内所有 [data-tip] 元素挂上悬浮提示。
   * 每次重渲染后都要重新调用（元素是新的）。
   */
  _bindTips(root) {
    if (!root) return;
    root.querySelectorAll('[data-tip]').forEach((node) => {
      node.addEventListener('mouseenter', () => {
        const text = node.getAttribute('data-tip');
        if (!text) return;
        const el = this._ensureTip();
        el.textContent = text;
        el.classList.add('show');
      });
      node.addEventListener('mousemove', (ev) => {
        const el = this._tipEl;
        if (!el || !el.classList.contains('show')) return;
        const pad = 14;
        const w = el.offsetWidth;
        const h = el.offsetHeight;
        let x = ev.clientX + pad;
        let y = ev.clientY + pad;
        // 贴到右/下边缘时翻到另一侧，避免被窗口裁掉
        if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
        if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
        el.style.left = `${Math.max(8, x)}px`;
        el.style.top = `${Math.max(8, y)}px`;
      });
      node.addEventListener('mouseleave', () => this.hideTip());
    });
  }

  // ---------------- 标题页 ----------------
  showTitle({ onStart, onContinue, hasSave, seed, difficulty, onSetDifficulty, onToggleAudio, onExit, audioEnabled, onFlandreTest, onVergilLab, onHalloweenTest }) {
    const d = getDifficulty(difficulty);
    const diffBtns = difficultyList().map((x) => `
      <button class="diff-btn ${x.id === d.id ? 'active' : ''}" data-diff="${x.id}"
              style="${x.id === d.id ? `border-color:${x.color};color:${x.color}` : ''}"
              title="${x.desc}">
        ${x.name}
      </button>`).join('');

    const root = this._mount(`
      <!-- 第一百六十轮：主界面视觉大重构（用户："主界面现在太像网页Demo了，
           缺乏游戏氛围感……神秘、高级、带有魔法与猫娘的结合感"）
           结构：title-scene（全屏容器）
             ├ title-vignette   CSS 暗角（紫红极暗，压焦点）
             ├ title-stars      上升魔法光点（DOM 粒子，CSS 动画）
             └ title-panel      毛玻璃哥特面板（四角发光小猫爪 + 猫耳标题）
               （版本号 + 种子码沉到屏幕右下角 .title-meta）-->
      <div class="title-scene">
        <div class="title-stars" aria-hidden="true"></div>
        <div class="title-vignette" aria-hidden="true"></div>
        <div class="screen narrow title-panel">
          <svg class="corner-paw" aria-hidden="true"><use href="#i-paw"/></svg>
          <svg class="corner-paw" aria-hidden="true"><use href="#i-paw"/></svg>
          <svg class="corner-paw" aria-hidden="true"><use href="#i-paw"/></svg>
          <svg class="corner-paw" aria-hidden="true"><use href="#i-paw"/></svg>
          <div class="title-head">
            <span class="ear left" aria-hidden="true"></span>
            <h1 data-text="猫娘姐妹">猫娘姐妹</h1>
            <span class="ear right" aria-hidden="true"></span>
          </div>
          <div class="sub">
            猫娘计划版土豆兄弟 · NEKO Sisters<br>
            在 20 波围攻中活下来，收集材料，把武器合成到传说品质。
          </div>

          <div class="diff-block">
            <div class="diff-label">难度</div>
            <div class="diff-row">${diffBtns}</div>
            <div class="diff-desc" style="color:${d.color}">${d.desc}</div>
          </div>

          <div class="btn-row btn-row-main">
            <button class="btn primary btn-start" data-act="start">开始新游戏</button>
          </div>
          <div class="btn-row">
            ${hasSave ? '<button class="btn" data-act="continue">继续游戏</button>' : ''}
            <button class="btn small" data-act="audio">${audioEnabled ? '🔊 音效开' : '🔇 音效关'}</button>
            <!-- 第四十九轮补充：临时调试直通按钮（用户要求，方便人工测验芙兰战），正式版可删 -->
            ${onFlandreTest ? '<button class="btn small" data-act="flandre-test" style="border-color:#ff4060;color:#ff4060">🐱 直接挑战芙兰！</button>' : ''}
        ${onHalloweenTest ? '<button class="btn small" data-act="halloween-test" style="border-color:#ff9f43;color:#ff9f43">🎃 直接挑战万圣节双猫！</button>' : ''}
            <!-- 第一百四十轮：椅子维吉尔刀光实验室（临时挑战按钮，用户要看效果） -->
            ${onVergilLab ? '<button class="btn small" data-act="vergil-lab" style="border-color:#59f2ff;color:#59f2ff">⚔ 挑战：大排档椅子·维吉尔</button>' : ''}
            <button class="btn small danger" data-act="exit">关闭游戏</button>
            <!-- 第一百二十二轮：音效自检按钮下线（用户："把音效自检按钮
                 隐藏或者去掉"—— 诊断使命完成，audio.seSelfTest 保留在
                 audio.js 里以备将来排查，只是不再有入口） -->
          </div>

          <!-- 操作提示沉底：不抢主按钮风头（用户："排版太挤了……放到面板的
               最下方，字号稍微缩小，颜色调暗一点"） -->
          <div class="keys">
            <span class="key"><b>WASD</b> / 方向键 移动</span>
            <span class="key"><b>武器自动开火</b></span>
            <span class="key"><b>按住左键</b> 所有武器瞄准鼠标方向，松开恢复自动索敌</span>
            <span class="key"><b>ESC</b> 暂停</span>
            <span class="key"><b>M</b> 静音</span>
          </div>
        </div>
        <!-- 版本号 + 种子码：屏幕右下角极暗位（用户："移到屏幕最右下角的
             极暗位置，不要放在视觉中心"）。版本唯一来源 src/version.js -->
        <div class="title-meta">v${GAME_VERSION} · 种子 ${seed}</div>
      </div>
    `);
    // 上升魔法光点：36 个随机参数的 DOM 粒子，动画交给 CSS（性能友好）
    {
      const stars = root.querySelector('.title-stars');
      let html = '';
      for (let i = 0; i < 36; i++) {
        const left = (i * 97.13) % 100;
        const size = 2 + ((i * 31) % 4);
        const dur = 9 + ((i * 53) % 14);
        const delay = -((i * 29) % 18);
        const tone = i % 3 === 0 ? 'pink' : i % 3 === 1 ? 'cyan' : 'violet';
        html += `<i class="t-${tone}" style="left:${left.toFixed(1)}%;width:${size}px;height:${size}px;animation-duration:${dur}s;animation-delay:${delay}s"></i>`;
      }
      stars.innerHTML = html;
    }
    root.querySelector('[data-act="start"]').onclick = onStart;
    // 第四十九轮补充：临时调试直通按钮
    const flTest = root.querySelector('[data-act="flandre-test"]');
    if (flTest) flTest.onclick = onFlandreTest;
    // 第一百八十四轮：万圣节双猫直通（v0.3.88 热修 —— 上轮绑定行
    // 替换静默失败，按钮渲染了却没 onclick，用户实测"点击无反应"）
    const hwTest = root.querySelector('[data-act="halloween-test"]');
    if (hwTest) hwTest.onclick = onHalloweenTest;
    // 第一百四十轮：椅子维吉尔刀光实验室
    const vgLab = root.querySelector('[data-act="vergil-lab"]');
    if (vgLab) vgLab.onclick = onVergilLab;
    // 第六十五轮的音效自检接线已随按钮一并下线（第一百二十二轮）
    const cont = root.querySelector('[data-act="continue"]');
    if (cont) cont.onclick = onContinue;
    root.querySelector('[data-act="audio"]').onclick = onToggleAudio;
    // 第十四轮 ⑥：开始菜单也要能关（用户原话见 showPause）
    if (onExit) root.querySelector('[data-act="exit"]').onclick = onExit;
    else root.querySelector('[data-act="exit"]').remove();
    root.querySelectorAll('[data-diff]').forEach((b) => {
      b.onclick = () => onSetDifficulty(b.dataset.diff);
    });
  }

  // ---------------- 角色选择 ----------------
  showCharacterSelect({ characters, onConfirm, onBack, difficulty, onSetDifficulty, onNekoPlay, onDuoPlay, onDuoWatch, onPartyPlay }) {
    let selected = characters[0].id;
    const d = getDifficulty(difficulty);

    const cardHtml = (c) => {
      const lines = [];
      // 角色修正展示
      for (const k in (c.mods || {})) {
        lines.push(`<div class="stat-line"><span>${STAT_DEFS[k].name}</span><span class="v ${c.mods[k] > 0 ? 'up' : 'down'}">${c.mods[k] > 0 ? '+' : ''}${c.mods[k]}</span></div>`);
      }
      for (const k in (c.pct || {})) {
        lines.push(`<div class="stat-line"><span>${STAT_DEFS[k].name}</span><span class="v ${c.pct[k] > 0 ? 'up' : 'down'}">${c.pct[k] > 0 ? '+' : ''}${c.pct[k]}%</span></div>`);
      }
      const startW = getWeapon(c.startWeapon);

      // ---- 第十六轮：能力 + 技能 ----
      //
      // 用户原话："给每个角色加一个能力适应和技能，改好了写在角色介绍里，
      //            如果不够展示加个小滚动条，
      //            **不要把我的括号里的内容写进去**"
      //
      // 两点：
      //  1. 括号里的说明（"最终乘算""最高累计30%概率"…）是给实现者看的，
      //     已经翻译成 char-skills.js 里的数值，**不进这里**。
      //     文案直接用数据层写好的 ability.text / skill.text。
      //  2. "不够展示加个小滚动条" → 这块固定 max-height + overflow-y:auto
      //     （见 styles.css 的 .char-skill-box）。卡片本身高度不固定，
      //     给整张卡加滚动条会让"点卡片选中"变得别扭（滚动和点击打架），
      //     所以只滚技能这一块。
      const cs = getCharSkill(c.id);
      const skillHtml = cs ? `
          <div class="char-skill-box">
            <div class="skill-row">
              <span class="skill-tag ability">能力</span>
              <span class="skill-name">${cs.ability.name}</span>
            </div>
            <div class="skill-text">${cs.ability.text}</div>
            <div class="skill-row">
              <span class="skill-tag skill">技能</span>
              <span class="skill-name">${cs.skill.name}</span>
            </div>
            <div class="skill-text">${cs.skill.text}</div>
          </div>` : '';

      return `
        <div class="card ${c.id === selected ? 'selected' : ''}" data-char="${c.id}">
          <div class="card-head">
            <div class="avatar" data-sprite="character:${c.id}" style="background:${c.color}22;border-color:${c.color};color:${c.color}">${c.glyph}</div>
            <div>
              <div class="card-name">${c.name}</div>
              <!-- 第一百六十轮：定位 = 带底色圆角小标签（用户："做成一个带
                   底色的圆角小标签，不要只是一行小字"） -->
              <div class="card-role" style="background:${c.color}1f;border-color:${c.color}66;color:color-mix(in srgb,${c.color} 74%,#0f1f38)">${c.role}</div>
            </div>
          </div>
          <div class="card-desc">${c.desc}</div>
          <div class="card-stats">
            ${lines.join('')}
            <div class="stat-line"><span>初始武器</span><span class="v" style="color:${startW.color}">${startW.name}</span></div>
          </div>
          ${skillHtml}
        </div>`;
    };

    const diffBtns = difficultyList().map((x) => `
      <button class="diff-btn ${x.id === d.id ? 'active' : ''}" data-diff="${x.id}"
              style="${x.id === d.id ? `border-color:${x.color};color:${x.color}` : ''}">${x.name}</button>`).join('');

    const root = this._mount(`
      <div class="screen">
        <h2>选择你的猫娘</h2>
        <div class="sub">每个角色有不同的初始属性与起始武器，选一个开始这局。</div>
        <div class="diff-block">
          <div class="diff-label">难度</div>
          <div class="diff-row">${diffBtns}</div>
          <div class="diff-desc" style="color:${d.color}">${d.desc}</div>
        </div>
        <div class="char-grid" id="char-grid">${characters.map(cardHtml).join('')}</div>
        <div class="btn-row">
          <button class="btn primary" data-act="go">用这个角色开始</button>
          <button class="btn" data-act="back">返回</button>
          <!-- 第一百六十六轮：「猫娘帮你玩」—— NekoPilot 代打入口 -->
          ${onNekoPlay ? '<button class="btn" data-act="neko-play" style="border-color:#ff9ec7;color:#ff9ec7">🐾 猫娘帮你玩</button>' : ''}
          <!-- 第一百七十轮：「双猫娘同游」—— 2P 实验（P1 手操 + P2 AI 猫娘队友） -->
          ${onDuoPlay ? '<button class="btn" data-act="duo-play" style="border-color:#9ec7ff;color:#9ec7ff">🐱‍👤 和猫娘一起玩（实验）</button>' : ''}
          <!-- 第一百七十一轮：双猫娘围观 —— 1P/2P 全 AI，纯看戏 -->
          ${onDuoWatch ? '<button class="btn" data-act="duo-watch" style="border-color:#c79eff;color:#c79eff">👀 看2个猫娘手忙脚乱（实验）</button>' : ''}
        </div>
        <!-- 第一百八十四轮：1~4P 小队 —— P1 真人 + 1~3 个 AI 猫娘；
             AI 全走 agentMoveTarget 不抢键盘，敌血按人数 +20/40/60% 递增 -->
        ${onPartyPlay ? `
        <div class="btn-row" style="gap:6px">
          <span style="font-size:12px;color:#8a93a6;align-self:center">猫娘小队：</span>
          <button class="btn" data-act="party-2" style="border-color:#9ec7ff;color:#9ec7ff;padding:4px 12px">2P（+1猫娘）</button>
          <button class="btn" data-act="party-3" style="border-color:#ffd166;color:#ffd166;padding:4px 12px">3P（+2猫娘）</button>
          <button class="btn" data-act="party-4" style="border-color:#4cc9f0;color:#4cc9f0;padding:4px 12px">4P（+3猫娘）</button>
        </div>` : ''}
      </div>
    `);

    const grid = root.querySelector('#char-grid');
    grid.addEventListener('click', (ev) => {
      const card = ev.target.closest('[data-char]');
      if (!card) return;
      selected = card.dataset.char;
      grid.querySelectorAll('.card').forEach((el) => el.classList.toggle('selected', el.dataset.char === selected));
    });

    root.querySelector('[data-act="go"]').onclick = () => onConfirm(selected);
    root.querySelector('[data-act="back"]').onclick = onBack;
    // 第一百六十六轮：猫娘代打入口
    const nekoBtn = root.querySelector('[data-act="neko-play"]');
    if (nekoBtn) nekoBtn.onclick = () => onNekoPlay(selected);
    // 第一百七十轮：双猫娘同游（2P 实验）
    const duoBtn = root.querySelector('[data-act="duo-play"]');
    if (duoBtn) duoBtn.onclick = () => onDuoPlay(selected);
    // 第一百七十一轮：双猫娘围观（全 AI）
    const watchBtn = root.querySelector('[data-act="duo-watch"]');
    if (watchBtn) watchBtn.onclick = () => onDuoWatch();
    // 第一百八十四轮：1~4P 小队
    for (const n of [2, 3, 4]) {
      const b = root.querySelector(`[data-act="party-${n}"]`);
      if (b && onPartyPlay) b.onclick = () => onPartyPlay(selected, n);
    }
    root.querySelectorAll('[data-diff]').forEach((b) => {
      b.onclick = () => onSetDifficulty(b.dataset.diff);
    });
  }

  // ---------------- 升级（3 选 1） ----------------
  // 第四十一轮："稍后再选"按钮删除 —— 它并不会稍后再选：
  // 关闭后 _afterOverlay() 见 pendingLevelUps > 0 会立刻重抽重开，
  // 玩家看到的就是"点了稍后再选，选项刷新了一遍又弹回来"。
  // 原按钮的位置改为快捷键说明：E 自动选一次 / Q 本局升级全自动。
  showLevelUp({ level, options, onPick }) {
    const cardHtml = (u) => {
      const parts = [];
      for (const k in (u.mods || {})) parts.push(fmtMod(k, u.mods[k]));
      for (const k in (u.pct || {})) parts.push(fmtMod(k, u.pct[k]));
      if (u.heal) parts.push('回复全部生命');
      // 护盾（第十二轮）：只在真的有盾时才写，普通档不写
      // （普通档 shieldPct = 0，写出来会是"护盾 +0%"很怪）。
      if (u.shieldPct > 0) parts.push(`护盾 ${Math.round(u.shieldPct * 100)}% 生命上限`);
      // 稀有度（用户要求）：普通/稀有/史诗/传说，效果分别 ×1/×2/×3/×4。
      // 用稀有度颜色描边 + 角标，让"这次出了个好的"一眼可见。
      const rc = u.rarityColor || u.color;
      const rn = u.rarityName || '普通';
      const isPlain = rn === '普通';
      const badge = isPlain
        ? ''
        : `<div class="card-rarity" style="background:${rc}22;border-color:${rc};color:${rc}">`
          + `${rn} · 效果 ×${u.mult}</div>`;
      return `
        <div class="card${isPlain ? '' : ' card--rare'}" data-up="${u.id}"
             style="${isPlain ? '' : `border-color:${rc};box-shadow:0 0 0 1px ${rc}55,0 0 18px ${rc}33`}">
          <div class="card-head">
            <div class="avatar" style="background:${u.color}22;border-color:${rc};color:${u.color}">${
              // 第一百六十轮：升级选项图标 —— 用户："各个选项还是字呢，你能做
              // 点对应的简单图标吗？就像你暂停界面画的那10个属性图标一样。"
              // 有 icon 字段走 SVG 线条图标（与暂停十图标同一套 symbol 库），
              // 没有的回落原 glyph 字。
              u.icon
                ? `<svg class="up-icon" viewBox="0 0 24 24" width="26" height="26"><use href="#${u.icon}"/></svg>`
                : u.glyph
            }</div>
            <div>
              <div class="card-name">${u.name}</div>
              <div class="card-role">升级选项</div>
            </div>
          </div>
          ${badge}
          <div class="card-desc">${u.desc}</div>
          <div class="card-stats">
            ${parts.map((t) => `<div class="stat-line"><span>${t}</span></div>`).join('')}
          </div>
        </div>`;
    };

    const root = this._mount(`
      <div class="screen">
        <h2>升级！ Lv.${level}</h2>
        <div class="sub">选一个强化。<b style="color:#ffd166">E</b> 自动选一次 ·
          <b style="color:#ffd166">Q</b> 本局升级全自动（下一局自动关闭）</div>
        <div class="char-grid">${options.map(cardHtml).join('')}</div>
      </div>
    `);

    root.querySelectorAll('[data-up]').forEach((card) => {
      card.onclick = () => onPick(card.dataset.up);
    });
  }

  // ---------------- 商店 ----------------
  showShop({ player, shop, waveIndex, waveIncome, pendingRewards, onBuy, onReroll, onCombine, onLock, onNext, onSell, onTakeReward, onSellReward }) {
    // 当前选中的武器槽（点了才显示"确认出售"，避免误触）
    let selected = -1;

    // 第一百六十轮：保留换行的 tip 转义（道具卡悬浮详情也要用，从下方
    // shop-char 段上提）。escAttr 会把换行压成空格，这里配合
    // .tip-float 的 white-space: pre-line 让多行文本各占一行。
    const tipEsc = (s) => String(s ?? '')
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    /** 常驻的详细数值面板（左侧竖排），不再需要按 ESC 才能看 */
    const statPanelHtml = () => {
      const s = player.stats.values;
      const groups = [
        ['攻击', ['damage', 'attackSpeed', 'critChance', 'critDamage', 'lifesteal']],
        ['防御', ['maxHp', 'armor', 'dodge', 'hpRegen']],
        ['机动', ['moveSpeed', 'pickupRange']],
        ['收益', ['luck', 'harvesting']],
      ];
      // 有炮台武器时才显示炮台分组，避免干扰其他流派
      const hasTurret = player.weapons.some((w) => w.def.kind === 'turret');
      if (hasTurret) {
        groups.push(['炮台', ['turretDamage', 'turretRate', 'turretRange', 'turretMax']]);
      }
      const secs = groups.map(([title, keys]) => {
        const rows = keys.filter((k) => STAT_DEFS[k]).map((k) => {
          const def = STAT_DEFS[k];
          // 第五轮：鼠标悬浮显示"这个数值到底意味着什么"（护甲多少减伤、
          // 幸运加多少掉率…）。文案由 core/stat-detail.js 统一生成，
          // 保证和战斗代码用的是同一套公式。
          // 第十六轮：带上角色的幸运倍率，说明里的"已按 X% 计入"才是真话。
          const detail = describeStat(k, s, player.stats.luckEffectMul ?? 1);
          return `<div class="sp-row has-tip" data-tip="${escAttr(detail)}">`
            + `<span class="sp-k">${def.name}</span>`
            + `<span class="sp-v">${fmtStat(k, s[k])}</span>`
            + `<span class="sp-info">?</span>`
            + `</div>`;
        }).join('');
        return `<div class="sp-sec"><div class="sp-title">${title}</div>${rows}</div>`;
      }).join('');

      const income = waveIncome
        ? `<div class="sp-sec">
             <div class="sp-title">本波收入</div>
             <div class="sp-row"><span class="sp-k">击杀掉落</span><span class="sp-v" style="color:var(--gold)">+${waveIncome.total - waveIncome.harvest - waveIncome.clear}</span></div>
             ${waveIncome.harvest > 0 ? `<div class="sp-row"><span class="sp-k">收获属性</span><span class="sp-v" style="color:var(--gold)">+${waveIncome.harvest}</span></div>` : ''}
             <div class="sp-row"><span class="sp-k">波次奖励</span><span class="sp-v" style="color:var(--gold)">+${waveIncome.clear}</span></div>
             <div class="sp-row sp-total"><span class="sp-k">合计</span><span class="sp-v">+${waveIncome.total}</span></div>
           </div>`
        : '';

      // 已持有道具
      // 第一百六十五轮：道具行 hover 显示效果（用户图5 反馈："下面的道具
      // （23种）的道具放上去什么都不显示，我是想要那个显示道具效果"）——
      // 与商店道具卡同款"当前累计"算法：线性乘区 ×数量、诅咒乘算复合，
      // 买了 10 个直接显示 10 倍效果，不用玩家自己算。属性行走的是同一个
      // _bindTips 委托，加了 data-tip 即自动生效。
      const fmtN = (x) => Math.round(x * 100) / 100;
      const itemRows = [...player.items.entries()].map(([id, n]) => {
        const def = getItemDef(id);
        if (!def) return '';
        const lines = [String(def.desc ?? '')];
        if (n > 0) {
          lines.push(`── 当前累计（已持有 ${n} 个）──`);
          for (const [k, v] of Object.entries(def.mods || {})) {
            lines.push(`${STAT_DEFS[k]?.name ?? k} +${fmtN(v * n)}`);
          }
          for (const [k, v] of Object.entries(def.pct || {})) {
            lines.push(`${STAT_DEFS[k]?.name ?? k} ${v * n >= 0 ? '+' : ''}${fmtN(v * n)}%`);
          }
          for (const [k, v] of Object.entries(def.curse || {})) {
            const remain = Math.round((1 - v) ** n * 1000) / 10;
            lines.push(`${STAT_DEFS[k]?.name ?? k} -${Math.round(v * 100)}%（乘算 ×${n} → 剩余 ${remain}%）`);
          }
        }
        return `<div class="sp-row has-tip" data-tip="${tipEsc(lines.join('\n'))}"><span class="sp-k" style="color:${def.color}">${def.name}</span><span class="sp-v">×${n}</span></div>`;
      }).join('');
      const items = itemRows
        ? `<div class="sp-sec"><div class="sp-title">道具（${player.items.size} 种）</div>${itemRows}</div>`
        : '';

      return `
        <aside class="stat-panel">
          <div class="sp-head">
            <span class="sp-name">${player.def?.name ?? player.char?.name ?? '猫娘'}</span>
            <span class="sp-lv">Lv.${player.level}</span>
          </div>
          <div class="sp-hp">
            <div class="sp-hp-bar"><i style="width:${Math.max(0, (player.hp / player.maxHp) * 100)}%"></i></div>
            <div class="sp-hp-text">${Math.ceil(player.hp)} / ${Math.round(player.maxHp)}${player.hp >= player.maxHp ? ' <span style="color:var(--green)">满血</span>' : ''}</div>
          </div>
          ${secs}
          ${items}
          ${income}
        </aside>`;
    };

    // 201 轮：货架被「界面之外」的动作改动时（宠物偷道具、队友 AI、读档恢复……）界面必须跟着重绘，
    // 否则玩家点的是旧画面，而点击落在已经换成别的商品的下标上（多人模式下「锁定失效」的第二层原因）。
    // 签名只覆盖 P1 看得见、并且点击会作用到的东西。
    const sig = () => shop.offers.slice(0, 4).map((o) => (o ? `${o.data?.id ?? ''}${o.sold ? 's' : ''}` : '-')).join(',')
      + `|${[...shop.locked].sort().join('.')}|${Math.floor(player.materials)}|${shop.rerollCount}|${pendingRewards?.length ?? 0}`;
    const live = { calc: sig, last: '', redraw: null };

    const render = () => {
      // ---- 第一百七十一轮（2P）：只显示前 SHOP_SLOTS 格 —— 后半是
      // P2 队友的专属货架，玩家的界面看不见也买不到
      const visibleOffers = shop.offers.slice(0, 4);
      const offerHtml = visibleOffers.map((o, i) => {
        if (!o) return '<div></div>';
        const locked = shop.locked.has(i);
        const afford = player.materials >= o.price && !o.sold;
        const isWeapon = o.kind === 'weapon';
        const tier = isWeapon ? o.data.tier : itemTier(o.data);
        const tierName = isWeapon ? TIER_NAMES[tier] : ['', '普通', '精良', '稀有'][tier];
        const color = isWeapon ? TIER_COLORS[tier] : ITEM_TIER_COLORS[tier];
        const behavior = isWeapon ? (BEHAVIOR_LABEL[o.data.behavior] ?? '') : '';
        const ownedCount = isWeapon
          ? player.findWeapons(o.data.id).length
          : (player.items.get(o.data.id) || 0);

        // 关键词条：直接给出"买了会变成什么"，不用去猜
        // 第九十五轮：武器卡此前只渲染"基础伤害 + 攻击间隔"两行，
        // 射程/横扫角度/多段/连锁/贯穿/吸血/击退/暴击/僵直全都没画 ——
        // 用户原话："大肥鱼做的武器没把数值详细写出来。"（大肥鱼=DeepSeek
        // 的外号）数值一直都在 data/weapons.js 里，这里把它们如实列出，
        // 并补一行升级成长（weapon.js：每级伤害 +12%、冷却 -4%，下限 60%）。
        const weaponStatRows = (d) => {
          const rows = [
            ['基础伤害', `${d.damage ?? '-'}`],
            ['攻击间隔', `${d.turretCooldown ?? d.cooldown ?? '-'}s`],
          ];
          if (d.range != null) rows.push(['射程', d.range]);
          if (d.arc) rows.push(['横扫角度', `${d.arc}°`]);
          if (d.thrusts) rows.push(['多段戳刺', `${d.thrusts} 段`]);
          if (d.chains) rows.push(['连锁跳数', `${d.chains} 跳`]);
          if (d.shots) rows.push(['连发', `${d.shots} 发`]);
          if (d.pierce != null) rows.push(['贯穿', d.pierce >= 99 ? '无限' : `${d.pierce} 个`]);
          if (d.lifesteal) rows.push(['吸血概率', `+${d.lifesteal}%`]);
          if (d.knockback) rows.push(['击退', d.knockback]);
          if (d.critBonus) rows.push(['暴击率', `+${d.critBonus}%`]);
          if (d.critDamageBonus) rows.push(['暴击伤害', `+${d.critDamageBonus}%`]);
          if (d.explosionRadius) rows.push(['爆炸半径', d.explosionRadius]);
          if (d.stun) rows.push(['僵直', `${d.stun}s`]);
          if (d.kind === 'turret') rows.push(['同时上限', `${d.turretMax ?? '-'} 座`]);
          return rows;
        };
        const effectHtml = isWeapon
          ? weaponStatRows(o.data).map(([k, v]) =>
              `<div class="stat-line"><span>${k}</span><span class="v">${v}</span></div>`).join('')
            + `<div class="stat-line"><span>每升一级</span><span class="v up-tip">伤害 +12% · 冷却 -4%</span></div>`
          : Object.entries({ ...(o.data.mods || {}) }).map(([k, v]) =>
              `<div class="stat-line"><span>${STAT_DEFS[k]?.name ?? k}</span><span class="v up">+${v}</span></div>`).join('')
            + Object.entries({ ...(o.data.pct || {}) }).map(([k, v]) =>
              `<div class="stat-line"><span>${STAT_DEFS[k]?.name ?? k}</span><span class="v ${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : ''}${v}%</span></div>`).join('')
            // 诅咒类（扣最大生命）：明确标出是乘算，玩家能预期叠加后果
            + Object.entries({ ...(o.data.curse || {}) }).map(([k, v]) =>
              `<div class="stat-line"><span>${STAT_DEFS[k]?.name ?? k}</span><span class="v down">-${Math.round(v * 100)}%（乘算）</span></div>`).join('');

        // 第十五轮 ③：猫爪印记（小小猫娘免费帮你拿到的道具）。
        //
        // 用**内联 SVG** 而不是 emoji（🐾）：emoji 在不同系统上字形差别很大
        // （Windows 是彩色、有些 Linux 是黑白方块），而这是"她给你的礼物"
        // 的标记，必须长得稳定。SVG 还能跟着 CSS 上色。
        const pawHtml = o.pawMark ? `<div class="paw-mark" title="小小猫娘帮你拿到的">
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <ellipse cx="12" cy="15.5" rx="5.2" ry="4.4"/>
                <ellipse cx="5.2" cy="9.4" rx="2.5" ry="2.9"/>
                <ellipse cx="9.6" cy="5.6" rx="2.5" ry="2.9"/>
                <ellipse cx="14.4" cy="5.6" rx="2.5" ry="2.9"/>
                <ellipse cx="18.8" cy="9.4" rx="2.5" ry="2.9"/>
              </svg></div>` : '';

        // ---- 第一百六十轮：道具悬浮详情（用户："目前商店页面我们可以鼠标
        // 停留在属性上看详情，但是看不了道具，把道具也加上，然后效果提升
        // 维持最新（比如我买了一个道具，数量1看他的效果描述就是正常的
        // 描述，买了10个之后放上去直接显示10倍效果而不用我自己计算……
        // 直接放上去显示介绍和最终加成数字。）"----
        // 线性乘区（mods/pct）直接 ×数量；诅咒类是乘算，按 (1-v)^n 算复合值。
        const itemTip = (() => {
          if (isWeapon) return null;   // 武器已有 weaponDetailText 的 tip
          const n = ownedCount;
          const fmtN = (x) => Math.round(x * 100) / 100;
          const lines = [String(o.data.desc ?? '')];
          if (n > 0) {
            lines.push(`── 当前累计（已持有 ${n} 个）──`);
            for (const [k, v] of Object.entries(o.data.mods || {})) {
              lines.push(`${STAT_DEFS[k]?.name ?? k} +${fmtN(v * n)}`);
            }
            for (const [k, v] of Object.entries(o.data.pct || {})) {
              lines.push(`${STAT_DEFS[k]?.name ?? k} ${v * n >= 0 ? '+' : ''}${fmtN(v * n)}%`);
            }
            for (const [k, v] of Object.entries(o.data.curse || {})) {
              const remain = Math.round((1 - v) ** n * 1000) / 10;
              lines.push(`${STAT_DEFS[k]?.name ?? k} -${Math.round(v * 100)}%（乘算 ×${n} → 剩余 ${remain}%）`);
            }
          }
          return lines.join('\n');
        })();

        return `
          <div class="card tier-${tier} ${locked ? 'locked' : ''} ${o.sold ? 'sold ' : ''}${o.sold || !afford ? 'cant-afford' : ''}"
               data-slot="${i}"
               ${itemTip ? `has-tip data-tip="${tipEsc(itemTip)}"` : ''}>
            <div class="tier-badge t${tier}" style="color:${color}">${tierName}</div>
            ${locked ? '<div class="lock-badge">🔒 已锁定</div>' : ''}
            ${pawHtml}
            <div class="card-head">
              <div class="avatar" data-sprite="${isWeapon ? 'weapon' : 'item'}:${o.data.id}" style="background:${o.data.color}22;border-color:${o.data.color};color:${o.data.color}">${o.data.glyph}</div>
              <div>
                <div class="card-name">${o.data.name}</div>
                <div class="card-role">${isWeapon ? `武器 · ${behavior}` : '道具'}${ownedCount ? ` · 已有 ${ownedCount}` : ''}</div>
              </div>
            </div>
            <div class="card-desc">${o.data.desc}</div>
            <div class="card-stats">${effectHtml}</div>
            <div class="shop-foot" style="margin-top:10px">
              <span class="price-tag" style="position:static">${o.price} 材料</span>
              ${afford || o.sold ? '' : '<span class="hint" style="color:var(--red)">材料不足</span>'}
            </div>
            <div style="display:flex;gap:6px;margin-top:8px">
              <button class="btn small" data-buy="${i}" ${o.sold || !afford ? 'disabled' : ''}>${o.sold ? '已售出' : '购买'}</button>
              <button class="btn small ${locked ? 'primary' : ''}" data-lock="${i}" ${o.sold ? 'disabled' : ''}>${locked ? '解锁' : '锁定'}</button>
            </div>
          </div>`;
      }).join('');

      // 合成区
      const combineHtml = (() => {
        const seen = new Set();
        const rows = [];
        for (const w of player.weapons) {
          const id = w.def.id;
          if (seen.has(id)) continue;
          seen.add(id);
          const info = shop.canCombine(player, id);
          if (!info) continue;
          const recipe = RECIPE_MAP[id];
          const target = getWeapon(recipe.to);
          const canDo = !!info.ok;
          rows.push(`
            <div class="card" style="cursor:default;border-color:${canDo ? '#6ee7a8' : 'var(--line)'}">
              <div class="card-head">
                <div class="avatar" data-sprite="weapon:${w.def.id}" style="background:${w.def.color}22;border-color:${w.def.color};color:${w.def.color}">${w.def.glyph}</div>
                <div>
                  <div class="card-name">${w.def.name} × 2</div>
                  <div class="card-role">合成 → <span style="color:${target.color}">${target.name}</span></div>
                </div>
              </div>
              <div class="card-desc">${target.desc}</div>
              <div style="display:flex;gap:6px;margin-top:10px;align-items:center">
                <button class="btn small primary" data-combine="${id}" ${canDo ? '' : 'disabled'}>
                  合成（${recipe.cost} 材料）
                </button>
                ${!canDo && info.needMaterials ? `<span class="hint">还差 ${info.needMaterials} 材料</span>` : ''}
              </div>
            </div>`);
        }
        if (rows.length === 0) {
          return '<div class="hint">收集两把相同武器即可在这里合成更强力的武器。</div>';
        }
        return `<div class="char-grid">${rows.join('')}</div>`;
      })();

      // 武器栏（每格可点击出售）
      // 第五轮：格子按**品质**发光描边（白/蓝/紫/金），鼠标悬浮看详细数据
      const tray = [];
      for (let i = 0; i < 6; i++) {
        const w = player.weapons[i];
        if (w) {
          const combinable = RECIPE_MAP[w.def.id] && player.findWeapons(w.def.id).length >= 2;
          const value = shop.sellValue ? shop.sellValue(w) : 0;
          const tier = w.def.tier ?? 1;
          const tColor = TIER_COLORS[tier] || TIER_COLORS[1];
          const tName = TIER_NAMES[tier] || TIER_NAMES[1];
          tray.push(`
            <div class="weapon-slot filled tier-${tier} ${combinable ? 'combinable' : ''} ${selected === i ? 'selected' : ''}"
                 data-slot-idx="${i}"
                 data-sprite="weapon:${w.def.id}"
                 style="--tier-color:${tColor}"
                 data-tip="${escAttr(weaponDetailText(w, player))}"
                 title="${escAttr(`${w.def.name} Lv.${w.level} · ${tName}（点击查看出售）`)}">
              <span class="w-glyph" style="color:${w.def.color}">${w.def.glyph}</span>
              ${w.level > 1 ? `<span class="w-tag">Lv${w.level}</span>` : ''}
              <span class="slot-name">${w.def.name}</span>
              <span class="slot-tier" style="color:${tColor}">${tName}</span>
              <span class="slot-sell">出售 +${value}</span>
            </div>`);
        } else {
          tray.push('<div class="weapon-slot"><span style="color:#4a5270">＋</span></div>');
        }
      }

      // 选中某个武器后弹出的确认条
      const sel = player.weapons[selected];
      const sellBar = sel
        ? `<div class="sell-bar">
             <span>要卖掉 <b style="color:${sel.def.color}">${sel.def.name}${sel.level > 1 ? ` Lv.${sel.level}` : ''}</b> 吗？</span>
             <span class="hint">可换回 ${shop.sellValue ? shop.sellValue(sel) : 0} 材料</span>
             <button class="btn small danger" data-act="confirm-sell">确认出售</button>
             <button class="btn small" data-act="cancel-sell">取消</button>
           </div>`
        : '';

      const rerollCost = shop.rerollCost;
      const canReroll = player.materials >= rerollCost;

      // ---- 宝箱奖励待领取（用户要求：可以选择拥有他或者卖掉换钱）----
      const rewards = pendingRewards || [];
      const rewardHtml = rewards.length === 0 ? '' : `
        <div class="sec-label" style="color:var(--gold)">宝箱战利品（${rewards.length} 件 · 选择留下或卖掉）</div>
        <div class="char-grid">
          ${rewards.map((r, i) => {
            const def = getItemDef(r.id);
            const color = def?.color || '#ffc857';
            const desc = def?.desc || '';
            return `
              <div class="card" style="cursor:default;border-color:${color}">
                <div class="card-head">
                  <div class="avatar"${def ? ` data-sprite="item:${def.id}"` : ''} style="background:${color}22;border-color:${color};color:${color}">${def?.glyph || '🎁'}</div>
                  <div>
                    <div class="card-name">${r.name}${r.rare ? ' <span style="color:var(--gold)">★</span>' : ''}</div>
                    <div class="card-role">战利品</div>
                  </div>
                </div>
                <div class="card-desc">${desc}</div>
                <div style="display:flex;gap:6px;margin-top:10px;align-items:center">
                  <button class="btn small primary" data-take-reward="${i}">留下</button>
                  <button class="btn small danger" data-sell-reward="${i}">卖掉 +${r.sellValue}</button>
                </div>
              </div>`;
          }).join('')}
        </div>`;

      // 第二十轮：最左侧角色栏（用户："在商店页面最左侧的空白区域放上角色
      // 的头像，鼠标放上去会显示角色的能力和技能，以防玩家忘记自己的角色
      // 能力具体加成。目前左侧还空着一些。"）悬停说明复用 _bindTips 的
      // data-tip 通道（tipEsc 已上提到 showShop 开头）。
      const charDef = player.char || {};
      const cs = getCharSkill(charDef.id);
      const charTip = cs
        ? `${charDef.name || charDef.id}\n能力【${cs.ability.name}】${cs.ability.text}\n技能【${cs.skill.name}】${cs.skill.text}`
        : (charDef.name || charDef.id || '');
      const charPanel = `
        <div class="shop-char has-tip" data-tip="${tipEsc(charTip)}">
          <div class="avatar shop-char-avatar" data-sprite="character:${charDef.id}"
               style="background:${charDef.color}22;border-color:${charDef.color};color:${charDef.color}">${charDef.glyph}</div>
          <div class="shop-char-name">${charDef.name || ''}</div>
          <div class="hint">鼠标放上看能力 / 技能</div>
        </div>`;

      this.el.overlay.innerHTML = `
        <div class="screen shop-screen">
          <div class="shop-head">
            <div>
              <h2>第 ${waveIndex + 1} 波结束 · 商店</h2>
              <div class="sub" style="margin:0">买武器和道具，或者把两把相同武器合成更强的。</div>
            </div>
            <div class="shop-money"><span class="mat-icon"></span>${this._fmtMats(player.materials)}</div>
          </div>

          <div class="shop-body">
            ${charPanel}
            <div class="shop-main">
              ${rewardHtml}
              <div class="sec-label">武器栏（最多 6 把 · 点击可出售）</div>
              <div class="weapon-tray">${tray.join('')}</div>
              ${sellBar}

              <div class="sec-label">商品</div>
              <div class="char-grid">${offerHtml}</div>

              <div class="sec-label">合成</div>
              ${combineHtml}
            </div>

            ${statPanelHtml()}
          </div>

          <div class="shop-foot">
            <div style="display:flex;gap:8px;align-items:center">
              <button class="btn" data-act="reroll" ${canReroll ? '' : 'disabled'}>刷新（${rerollCost} 材料）</button>
              <span class="hint">🔒 锁定的商品会在下一家商店保留</span>
            </div>
            <button class="btn primary" data-act="next">进入第 ${waveIndex + 2} 波</button>
          </div>
        </div>
      `;

      // 事件绑定
      this.el.overlay.querySelectorAll('[data-buy]').forEach((b) => {
        b.onclick = () => { onBuy(Number(b.dataset.buy)); render(); };
      });
      this.el.overlay.querySelectorAll('[data-lock]').forEach((b) => {
        b.onclick = () => { onLock(Number(b.dataset.lock)); render(); };
      });
      this.el.overlay.querySelectorAll('[data-combine]').forEach((b) => {
        b.onclick = () => { onCombine(b.dataset.combine); render(); };
      });
      const rr = this.el.overlay.querySelector('[data-act="reroll"]');
      if (rr) rr.onclick = () => { onReroll(); render(); };
      this.el.overlay.querySelector('[data-act="next"]').onclick = onNext;

      // ---- 出售武器：点格子选中 → 确认出售 ----
      this.el.overlay.querySelectorAll('[data-slot-idx]').forEach((el) => {
        el.onclick = () => {
          const idx = Number(el.dataset.slotIdx);
          selected = selected === idx ? -1 : idx;   // 再点一次取消
          render();
        };
      });

      // 每次 render 后重新挂悬浮提示（DOM 是新生成的）
      this._bindTips(this.el.overlay);
      // 商店自己写 innerHTML（没走 _mount），所以贴图也要在这里补一遍
      this._applySprites(this.el.overlay);
      const confirmSell = this.el.overlay.querySelector('[data-act="confirm-sell"]');
      if (confirmSell) {
        confirmSell.onclick = () => {
          const w = player.weapons[selected];
          if (w && onSell) onSell(w);
          selected = -1;
          render();
        };
      }
      const cancelSell = this.el.overlay.querySelector('[data-act="cancel-sell"]');
      if (cancelSell) cancelSell.onclick = () => { selected = -1; render(); };

      // ---- 宝箱战利品：留下 / 卖掉 ----
      this.el.overlay.querySelectorAll('[data-take-reward]').forEach((b) => {
        b.onclick = () => {
          const i = Number(b.dataset.takeReward);
          if (onTakeReward) onTakeReward(i);
          render();
        };
      });
      this.el.overlay.querySelectorAll('[data-sell-reward]').forEach((b) => {
        b.onclick = () => {
          const i = Number(b.dataset.sellReward);
          if (onSellReward) onSellReward(i);
          render();
        };
      });
      live.last = sig();        // 201 轮：每次重绘后记下签名，tickShop 只在签名变了才重绘
      live.redraw = render;
      this._shopLive = live;    // （_mount/clearOverlay 会清掉它，所以每次渲染后重新挂上）
    };

    render();
  }

  /** 201 轮：主循环在 SHOP 状态下每帧调用；货架/锁/材料在界面之外变了就重绘 */
  tickShop() {
    const l = this._shopLive;
    if (!l || !l.redraw) return;
    if (l.calc() !== l.last) l.redraw();
  }

  // ---------------- 无限模式询问 ----------------
  /**
   * 第 20 波通关后询问是否继续（用户第四轮要求）。
   * 不直接结算，让玩家自己选"见好就收"还是"继续挑战"。
   * 第一百二十九轮：战绩块补齐成与结算页同款六项（用户："通关 20 波
   * 只显示等级和击杀，我记得之前显示很多的来着"——这个询问页一直只有
   * 两项，看着像结算页缩水了）；Boss 提示文案更新为第四十八轮的现行
   * 出场表（旧文案"每 5 波一个 Boss"是第四轮的，早就过时了）。
   */
  showEndlessPrompt({ wave, player, stats = null, onContinue, onStop }) {
    const s = stats || null;
    const statRows = s
      ? [
          ['到达波次', `${wave} / ${wave}`],
          ['击杀数', s.kills],
          ['造成伤害', Math.round(s.damageDealt)],
          ['承受伤害', Math.round(s.damageTaken)],
          ['角色等级', player.level],
          ['存活时间', `${Math.floor(s.timeAlive)} 秒`],
        ]
      : [
          ['等级', `Lv.${player.level}`],
          ['击杀', player.kills],
        ];
    const root = this._mount(`
      <div class="screen narrow">
        <h1 style="font-size:30px">通关！</h1>
        <div class="sub">
          你带着猫娘们撑过了全部 ${wave} 波。要进入<b>无限模式</b>继续吗？
        </div>
        <div class="result-stats">
          ${statRows.map(([k, v]) => `<div class="result-stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}
        </div>
        <div class="card-desc" style="margin:12px 0">
          第 21 波起敌人属性与数量翻倍；Boss 会以 1 → 2 → 3 → 4 只的规模
          轮番登场，间隔越来越短，后期每波都是四连决战——见好就收也是
          一种勇气。
        </div>
        <div class="btn-row">
          <button class="btn primary" data-act="go">继续挑战（无限模式）</button>
          <button class="btn" data-act="stop">见好就收，结算</button>
        </div>
      </div>
    `);
    root.querySelector('[data-act="go"]').onclick = onContinue;
    root.querySelector('[data-act="stop"]').onclick = onStop;
  }

  // ---------------- 结算 ----------------
  showResult({ win, player, waveIndex, stats, onRestart, onTitle,
               canRetry = false, waveRetries = 0, totalRetries = 0, onRetryWave }) {
    // 无限模式下波次可能远超 20，所以分母用"至少 20"
    const total = Math.max(20, waveIndex + 1);
    const endless = waveIndex + 1 > 20;
    const rows = [
      ['到达波次', `${waveIndex + 1} / ${total}`],
      ['击杀数', stats.kills],
      ['造成伤害', Math.round(stats.damageDealt)],
      ['承受伤害', Math.round(stats.damageTaken)],
      ['角色等级', player.level],
      ['存活时间', `${Math.floor(stats.timeAlive)} 秒`],
    ];
    // 重试次数也作为一项战绩展示出来（用户要求"记录重试次数"）
    if (waveRetries > 0 || totalRetries > 0) {
      rows.push(['重试次数', waveRetries > 0
        ? `本波 ${waveRetries} 次（累计 ${totalRetries} 次）`
        : `累计 ${totalRetries} 次`]);
    }

    // 输了才有「重试本波」：直接回到这一波开头，不用从第 1 波重打。
    // 放在最左边并高亮，因为这是玩家死了以后最想点的那个。
    const retryBtn = canRetry
      ? `<button class="btn primary" data-act="retry">重试第 ${waveIndex + 1} 波</button>`
      : '';
    // 「再来一局」在能重试时降级为次要按钮，避免和重试抢注意力
    const againCls = canRetry ? 'btn' : 'btn primary';

    const root = this._mount(`
      <div class="screen narrow">
        <h1 style="font-size:32px">${win ? '通关！' : '被击倒了…'}</h1>
        <div class="sub">
          ${win
            ? (endless
              ? `无限模式打到了第 ${waveIndex + 1} 波。猫娘们都看呆了。`
              : `你带着猫娘们撑过了全部 20 波。真是个了不起的指挥官。`)
            : `在第 ${waveIndex + 1} 波倒下了。${canRetry ? '要直接从这一波再来一次吗？' : '再来一次？'}`}
        </div>
        <div class="result-stats">
          ${rows.map(([k, v]) => `<div class="result-stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}
        </div>
        ${canRetry ? '<div class="sub" style="margin-top:14px;opacity:.75">重试会回到这一波刚开局的样子（等级、武器、材料都保留）。</div>' : ''}
        <div class="btn-row">
          ${retryBtn}
          <button class="${againCls}" data-act="again">${canRetry ? '从第 1 波重开' : '再来一局'}</button>
          <button class="btn" data-act="title">返回标题</button>
        </div>
      </div>
    `);
    const retryEl = root.querySelector('[data-act="retry"]');
    if (retryEl && onRetryWave) retryEl.onclick = onRetryWave;
    root.querySelector('[data-act="again"]').onclick = onRestart;
    root.querySelector('[data-act="title"]').onclick = onTitle;
  }

  // ---------------- 暂停 ----------------
  // 第一百三十九轮：属性方块图标映射（暗金 = 进攻系 / 淡紫 = 防御辅助系）
  static PAUSE_ICONS = {
    damage: 'i-damage', attackSpeed: 'i-atkspeed', critChance: 'i-crit',
    critDamage: 'i-critdmg', armor: 'i-armor', dodge: 'i-dodge',
    moveSpeed: 'i-speed', luck: 'i-paw', harvesting: 'i-gem', lifesteal: 'i-drop',
  };
  static PAUSE_GOLD = new Set(['damage', 'attackSpeed', 'critChance', 'critDamage', 'lifesteal']);

  showPause({ onResume, onQuit, onExit, player, fxAlpha = 100, onFxAlpha = null, pilotOn = false, onTogglePilot = null }) {
    const s = player.stats.values;
    const rows = [];
    // 第一百三十九轮：进阶反馈 —— 和上一次暂停的快照比，刚涨过的数值闪绿光
    const prev = this._lastPauseStats ?? {};
    for (const k of ['damage', 'attackSpeed', 'critChance', 'critDamage', 'armor', 'dodge', 'moveSpeed', 'luck', 'harvesting', 'lifesteal']) {
      const tone = UI.PAUSE_GOLD.has(k) ? 'gold' : 'violet';
      const changed = prev[k] !== undefined && Math.abs(Number(prev[k]) - Number(s[k])) > 0.01;
      rows.push(`<div class="result-stat tone-${tone}${changed ? ' rs-flash' : ''}">` +
        `<svg class="rs-icon"><use href="#${UI.PAUSE_ICONS[k]}"/></svg>` +
        `<div class="rs-txt"><div class="k">${STAT_DEFS[k].name}</div>` +
        `<div class="v">${fmtStat(k, s[k])}</div></div></div>`);
    }
    this._lastPauseStats = { ...s };

    const root = this._mount(`
      <div class="screen">
        <h2 class="pause-title">已暂停</h2>
        <div class="sub">当前属性一览</div>
        <div class="result-stats">${rows.join('')}</div>
        <div style="margin-top:16px;text-align:left">
          <div class="sub" style="margin-bottom:6px">设置</div>
          <div style="display:flex;align-items:center;gap:12px">
            <span style="white-space:nowrap">玩家特效</span>
            <input type="range" class="fx-slider" min="0" max="100" step="5" value="${fxAlpha}"
              data-act="fx-alpha" style="flex:1">
            <span data-act="fx-alpha-val" style="min-width:44px;text-align:right">${fxAlpha}%</span>
          </div>
          <div class="sub" style="margin-top:6px;opacity:.75">
            调低自己攻击的透明度 —— 弹幕太密看不清敌人弹幕时有用（敌人弹幕不受影响）。
          </div>
        </div>
        <div class="btn-row">
          <!-- 第一百六十六轮：猫娘代打切换开关（显示当前状态）-->
          ${onTogglePilot ? `<button class="btn" data-act="toggle-pilot" style="${pilotOn ? 'border-color:#ff9ec7;color:#ff9ec7;background:#ff9ec722' : ''}">🐾 猫娘帮你玩：${pilotOn ? '开（她正在打）' : '关'}</button>` : ''}
          <button class="btn primary" data-act="resume">继续游戏</button>
          <button class="btn" data-act="quit">放弃本局</button>
          <button class="btn danger" data-act="exit">关闭游戏</button>
        </div>
      </div>
    `);
    root.querySelector('[data-act="resume"]').onclick = onResume;
    root.querySelector('[data-act="quit"]').onclick = onQuit;
    // 第一百六十六轮：猫娘代打切换 —— main 侧切完会重开暂停菜单刷新状态
    const pilotBtn = root.querySelector('[data-act="toggle-pilot"]');
    if (pilotBtn) pilotBtn.onclick = () => onTogglePilot();
    // 第三十九轮："玩家特效"透明度滑条 —— 拖动即时生效并持久化
    const fxSlider = root.querySelector('[data-act="fx-alpha"]');
    const fxVal = root.querySelector('[data-act="fx-alpha-val"]');
    if (fxSlider && fxVal) {
      fxSlider.oninput = () => {
        const v = Number(fxSlider.value);
        fxVal.textContent = `${v}%`;
        if (onFxAlpha) onFxAlpha(v);
      };
    }
    // 第十四轮 ⑥：关闭游戏。
    // 用户原话："加一个关闭游戏的按钮在暂停界面和开始菜单。不然通过猫娘主动
    //            邀请进入游戏之后想关闭只能 alt+f4。"
    if (onExit) root.querySelector('[data-act="exit"]').onclick = onExit;
    else root.querySelector('[data-act="exit"]').remove();
  }
}