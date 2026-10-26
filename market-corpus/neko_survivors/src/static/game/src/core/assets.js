// ============================================================
// 贴图加载层
//
// 设计原则：**贴图永远是可选的增强，不是必需品。**
// 任何一张图缺失、损坏、加载失败，都必须无缝回落到原来的文字/图元画法，
// 绝不能因为少一张图就白屏或者报错。
//
// 为什么这么做：美术资源是一批批生成的（受额度、进度影响），
// 游戏必须做到"有多少用多少"——今天只有 13 张武器图标，
// 明天补到 54 张，中间任何时候游戏都得能正常玩。
//
// 用法：
//   import { sprites } from './core/assets.js';
//   await sprites.preload();                 // 启动时调一次，失败也不抛错
//   sprites.get('weapon', 'cat_paw')         // → HTMLImageElement | null
//   sprites.has('weapon', 'cat_paw')         // → boolean
//   sprites.apply(el, 'weapon', id, glyphEl) // 直接操作 DOM，最省事
// ============================================================

/** 贴图根目录（相对页面）。NEKO 部署后结构与本地一致。 */
const ROOT = './assets';

/**
 * 各类资源的文件名规则与目录。
 * 新增一类资源只要在这里加一行。
 *
 * `ext` 默认 .png（带 alpha 的贴图都要 PNG）；
 * 背景是不透明的照片级纹理，用 JPEG 体积能小一半以上，所以单独指定。
 */
export const SPRITE_KINDS = {
  weapon: { dir: 'weapons', prefix: 'weapon_' },
  item: { dir: 'items', prefix: 'item_' },
  character: { dir: 'characters', prefix: 'char_' },
  enemy: { dir: 'enemies', prefix: 'enemy_' },
  background: { dir: 'backgrounds', prefix: 'bg_', ext: '.jpg' },
  // 第九轮：独立运行时读不到宿主猫娘信息时的保底立绘。
  // 它是一张静态图，不是 Live2D —— 只是保证"右下角不会空着"。
  avatar: { dir: 'avatar', prefix: 'avatar_' },
  // 第十轮：地面掉落物的贴图。
  // 原来只有血包（heal）。第十九轮把用户给的素材接了进来：
  // 经验宝石（xp）+ 双造型金币（coin_1/coin_2，每枚掉落随机选一张）。
  // 加载不到就回落到程序画的几何块。
  pickup: { dir: 'pickup', prefix: '' },
  // 第十九轮：地图宝箱（固定可打的箱子）的专属贴图。
  // chest_1 普通宝箱 / chest_2 华丽宝箱 / chest_heal 医疗包宝箱。
  // 加载不到就回落到程序画的圆圈，不影响可玩性。
  chest: { dir: 'chest', prefix: '' },
  // 第十一轮：宠物「小小猫娘」的贴图（用户给的 小小猫娘.png）。
  // 和其它贴图一样是可选增强 —— 加载不到就回落到程序画的几何小猫。
  pet: { dir: 'pet', prefix: 'pet_' },
  // 第一百轮：芙兰战背景大魔法阵（原版 Extra 关 stg7enm sprite #86，
  // 高清 720² 重制版，见 touhou-flandre/flandre-stage7/MAGIC-CIRCLE.md）。
  // 可选增强：加载不到就不画阵，战斗照常。
  fx: { dir: 'fx', prefix: '' },
};

/**
 * 文件名别名（第一百二十轮）：个别素材的文件名和数据 id 对不上时，
 * 在这里登记一层映射，不改数据、不改用户的文件名。
 *
 * 用户原话："穿甲弹没图标，我在 assets/items 里放了
 * item_armor_piercer.png 当穿甲弹图标。用上它。"
 * 默认规则会去找 item_ap_round.png；别名让它指向 item_armor_piercer.png。
 */
export const SPRITE_FILE_ALIAS = {
  item: {
    ap_round: 'armor_piercer',
    // 第一百三十四轮：Boss 专属掉落红品 —— 用户直接用中文名放的素材，
    // 别名映射到用户文件名，不改文件、不改数据 id。
    // '!' 前缀 = 完整文件名（素材不带 item_ 前缀，见 url() 注释）。
    dried_fish: '!小鱼干',
    lobster_pillow: '!龙虾抱枕',
    smart_wand: '!智慧型魔棒',
    dr_m_notes: '!M博士心得',
    lucky_cat_paw: '!幸运猫脚',
    black_cape: '!黑色披风',
    cat_taffy: '!猫娘奶糖',
    overload_chip: '!超载芯片',
  },
};

export class SpriteStore {
  constructor() {
    /** @type {Map<string, HTMLImageElement>} key = `${kind}:${id}` */
    this._loaded = new Map();
    /** @type {Set<string>} 尝试过但失败的，避免反复重试 */
    this._failed = new Set();
    this._ready = false;
    this._stats = { requested: 0, loaded: 0, failed: 0 };
  }

  /** 贴图是否可用（全都没加载到就整体走老画法） */
  get ready() { return this._ready; }
  get stats() { return { ...this._stats }; }

  _key(kind, id) { return `${kind}:${id}`; }

  /** 拼出某个资源的 URL。id 里不该有路径分隔符，先挡一道。 */
  url(kind, id) {
    const k = SPRITE_KINDS[kind];
    if (!k) return null;
    if (!id || /[\\/]|\.\./.test(id)) return null;
    // 第一百二十轮：文件名别名（如 ap_round → armor_piercer）
    // 第一百三十八轮：别名以 '!' 开头 = **完整文件名**，不再拼 kind 前缀。
    // 用户给的红品素材是裸中文名（小鱼干.png），不带 item_ 前缀 ——
    // 旧逻辑拼出 item_小鱼干.png 404，图标一直回落成文字（用户实测
    // "上面写的字，而不是图片"）。浏览器对中文路径自动百分号编码，
    // NEKO 静态服务按 UTF-8 解码，实测可取。
    const aliased = SPRITE_FILE_ALIAS[kind]?.[id];
    if (aliased != null) {
      return aliased.startsWith('!')
        ? `${ROOT}/${k.dir}/${aliased.slice(1)}${k.ext || '.png'}`
        : `${ROOT}/${k.dir}/${k.prefix}${aliased}${k.ext || '.png'}`;
    }
    return `${ROOT}/${k.dir}/${k.prefix}${id}${k.ext || '.png'}`;
  }

  /**
   * 同步取一张已加载的贴图；没有就返回 null（调用方负责回落）。
   */
  get(kind, id) {
    if (!this._ready && this._loaded.size === 0) return null;
    return this._loaded.get(this._key(kind, id)) || null;
  }

  has(kind, id) { return this._loaded.has(this._key(kind, id)); }

  /**
   * 加载一批 id。单张失败只记一笔，不影响其他张，也绝不抛错。
   * @param {string} kind
   * @param {string[]} ids
   * @returns {Promise<{loaded:number, failed:number}>}
   */
  async loadMany(kind, ids) {
    const list = [...new Set(ids)].filter(Boolean);
    if (!list.length) return { loaded: 0, failed: 0 };

    let loaded = 0, failed = 0;
    // 并发加载，但每张都独立 try —— 一张坏图不能拖垮整批
    await Promise.all(list.map(async (id) => {
      const key = this._key(kind, id);
      if (this._loaded.has(key) || this._failed.has(key)) return;
      const url = this.url(kind, id);
      this._stats.requested++;
      if (!url) { this._failed.add(key); this._stats.failed++; failed++; return; }
      try {
        const img = await loadImage(url);
        this._loaded.set(key, img);
        this._stats.loaded++;
        loaded++;
      } catch {
        this._failed.add(key);
        this._stats.failed++;
        failed++;
      }
    }));
    return { loaded, failed };
  }

  /**
   * 启动时预加载。**永远不会 reject**。
   * @param {{weapons?:string[], items?:string[], characters?:string[], enemies?:string[]}} plan
   */
  async preload(plan = {}) {
    const jobs = [];
    for (const [kind, ids] of Object.entries(plan)) {
      if (!SPRITE_KINDS[kind] || !Array.isArray(ids) || !ids.length) continue;
      jobs.push(this.loadMany(kind, ids));
    }
    if (!jobs.length) { this._ready = true; return this.stats; }
    try {
      await Promise.all(jobs);
    } catch {
      // loadMany 内部已经吞掉单张错误，这里只是最后一道保险
    }
    this._ready = this._loaded.size > 0;
    return this.stats;
  }

  /**
   * 把贴图贴到一个元素上：有图就显示图并隐藏字形，没有就保持原样。
   *
   * 注意：**只隐藏字形本身，不能隐藏所有子元素** ——
   * 图标容器里通常还挂着等级角标（`.w-tag`）、品级标签（`.slot-tier`）
   * 这类兄弟节点，全隐藏会把它们一起弄没。
   *
   * @param {HTMLElement} el      容器（需要 position:relative）
   * @param {string} kind
   * @param {string} id
   * @param {object} [opt]
   * @param {string} [opt.hide]   要隐藏的字形选择器
   * @param {string} [opt.cls]    给 <img> 加的类名
   * @param {number} [opt.inset]  贴图相对容器的内缩（px），让图标不顶边
   * @returns {boolean} 是否用上了贴图
   */
  apply(el, kind, id, opt = {}) {
    if (!el) return false;
    const { hide = '.w-glyph, .glyph, .slot-glyph, .glyph-auto', cls = 'sprite-img', inset = 3 } = opt;
    const img = this.get(kind, id);
    if (!img) return false;

    let node = el.querySelector(':scope > img.' + cls);
    if (!node) {
      // --- 坑 1：容器可能是 position:static，绝对定位的 <img> 会跑到别处 ---
      // 查计算样式而不是内联样式（模板里没写不代表 CSS 里没有）。
      try {
        if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      } catch { el.style.position = 'relative'; }

      // --- 坑 2：字形可能是个**裸文本节点**，没有 <span> 可以隐藏 ---
      // querySelectorAll 找不到文本节点，于是贴图盖上去后汉字还露在下面。
      // 把裸文本包进 span 再隐藏。
      const bare = [...el.childNodes].filter(
        (n) => n.nodeType === 3 && n.textContent.trim().length > 0,
      );
      for (const t of bare) {
        const span = document.createElement('span');
        span.className = 'glyph-auto';
        t.parentNode.insertBefore(span, t);
        span.appendChild(t);
      }

      node = document.createElement('img');
      node.className = cls;
      node.alt = '';
      node.draggable = false;
      // 绝对定位盖在容器上，不改动原有布局
      Object.assign(node.style, {
        position: 'absolute',
        inset: `${inset}px`,
        width: `calc(100% - ${inset * 2}px)`,
        height: `calc(100% - ${inset * 2}px)`,
        objectFit: 'contain',
        pointerEvents: 'none',
        imageRendering: 'auto',
      });
      el.appendChild(node);
      // 只隐藏字形，角标/标签照常显示
      try {
        for (const g of el.querySelectorAll(hide)) {
          if (g !== node) g.style.visibility = 'hidden';
        }
      } catch { /* 选择器不支持也不影响贴图 */ }
      el.classList.add('has-sprite');
    }
    if (node.getAttribute('src') !== img.src) node.src = img.src;
    return true;
  }

  /**
   * 批量给容器贴图。`nodes` 是 [{el, kind, id}]，返回成功数量。
   * 失败（没图）的保持原样，不报错。
   */
  applyAll(nodes = [], opt = {}) {
    let n = 0;
    for (const it of nodes) {
      if (!it?.el) continue;
      if (this.apply(it.el, it.kind, it.id, { ...opt, ...(it.opt || {}) })) n++;
    }
    return n;
  }
}

/** 加载单张图片，失败 reject（内部函数，不对外） */
function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    let settled = false;
    img.onload = () => {
      if (settled) return;
      settled = true;
      // 0 尺寸说明图坏了（有些服务器会返回 200 + 空内容）
      if (!img.naturalWidth || !img.naturalHeight) reject(new Error('图片尺寸为 0: ' + url));
      else resolve(img);
    };
    img.onerror = () => { if (!settled) { settled = true; reject(new Error('加载失败: ' + url)); } };
    img.decoding = 'async';
    img.src = url;
    // 兜底超时：网络卡住不能让游戏一直等
    setTimeout(() => { if (!settled) { settled = true; reject(new Error('加载超时: ' + url)); } }, 8000);
  });
}

export const sprites = new SpriteStore();

/**
 * 根据当前游戏数据算出"要预加载哪些 id"。
 * 单独抽出来是为了让测试能直接调，不用起整套游戏。
 */
export function buildPreloadPlan({
  weapons = [], items = [], characters = [], enemies = [], backgrounds = [],
  pickups = [], pets = [], chests = [], fx = [],
} = {}) {
  const ids = (arr) => arr.map((x) => (typeof x === 'string' ? x : x?.id)).filter(Boolean);
  return {
    weapon: ids(weapons),
    item: ids(items),
    character: ids(characters),
    enemy: ids(enemies),
    // 背景只传 id 字符串数组（如 ['lab']），没有对应的"数据对象"
    background: ids(backgrounds),
    // 掉落物同理，传裸 id 字符串（如 ['heal']）
    pickup: ids(pickups),
    // 宠物同理，传裸 id 字符串（如 ['kitten']）
    pet: ids(pets),
    // 宝箱同理，传裸 id 字符串（第十九轮：['chest_1','chest_2','chest_heal']）
    chest: ids(chests),
    // 特效贴图（第一百轮：['magic_circle_86']）
    fx: ids(fx),
  };
}
