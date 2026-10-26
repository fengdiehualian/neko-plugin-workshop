// ============================================================
// 独立运行时的 Live2D（第九轮）
//
// 背景：
//   在 NEKO 里，宿主页负责装配 Live2D 控制器（见 neko-host.html），
//   游戏只是通过 SDK 的 avatar-renderer 能力挂上去。
//   但用 http://127.0.0.1:5188/ 单独打开时没有宿主，
//   window.NekoMiniGame 不存在，右下角永远是空的。
//
// 做法：
//   没有宿主时，我们自己扮演宿主 —— 把 NEKO 的 Live2D 引擎栈拉起来，
//   然后**照样用官方 avatar-host 的 mount 契约**装配。
//   这样独立运行与宿主运行的挂载路径是同一套代码，
//   不会出现"两边行为不一致"的坑。
//
// 依赖 server.mjs 的代理：
//   /static/libs/*、/static/live2d/* 这些是 NEKO 的资源，
//   静态页单独打开时跨源（48911 vs 5188）会被同源策略拦掉，
//   所以 server.mjs 把它们反代成同源路径。
//   NEKO 没开 → 全部失败 → 回落到静态保底图，游戏不受影响。
//
// 设计原则（沿用全局约定）：
//   Live2D 是**可选增强**，不是必需项。任何一步失败都静默降级，
//   绝不影响游戏本体。
// ============================================================

/** 引擎脚本，顺序不能变（与 NEKO 官方一致） */
const ENGINE_SCRIPTS = [
  '/static/libs/live2dcubismcore.min.js',
  '/static/libs/live2d.min.js',
  '/static/libs/pixi.min.js',
  '/static/libs/index.min.js',
  '/static/live2d/live2d-core.js',
  '/static/live2d/live2d-emotion.js',
  '/static/live2d/live2d-model.js',
];

/** avatar-host 本身（本地 neko-sdk/ 通过 server.mjs 别名映射到这个路径） */
const AVATAR_HOST_SRC = '/static/game/sdk/neko-minigame-avatar-host.js';

/**
 * 第一百二十五轮：插件面板模式基址。
 *
 * 插件 iframe 的源（48916）上没有 /static/libs 这些 NEKO 资源，
 * 但插件的 Python 壳会在启动时把当前猫娘的模型与引擎同步进
 * static/vendor/，随面板同源伺服。这里从 pathname 提取
 * `/plugin/<id>/ui/` 前缀，引擎与模型一律改从插件 vendor 加载。
 *
 * @returns {string|null} 插件基址（如 '/plugin/neko_survivors/ui/'），非插件环境 null
 */
export function pluginUiBase() {
  const m = location.pathname.match(/^(\/plugin\/[^/]+\/ui\/)/);
  return m ? m[1] : null;
}

function engineSources() {
  const base = pluginUiBase();
  if (!base) return { host: AVATAR_HOST_SRC, scripts: ENGINE_SCRIPTS };
  return {
    host: `${base}vendor/neko-minigame-avatar-host.js`,
    scripts: ENGINE_SCRIPTS.map((src) => `${base}vendor/engine/${src.split('/').pop()}`),
  };
}

let _enginePromise = null;

/**
 * 第一百二十六轮：插件面板里给 live2d 引擎的宿主 API 请求装一个同源 shim。
 *
 * live2d-core / live2d-model 会去请求宿主的 /api/live2d/...（表情/参数/
 * 模型列表等）；插件面板源上没有这些路由（404 刷控制台，表情参数丢失），
 * 跨源去 48911 又被 CORS 拦。这里在引擎加载前把 /api/live2d/ 的 fetch
 * 短路成空数据 200 —— 引擎拿到"没有额外配置"的正常空响应，形象/待机
 * 动作照常，控制台不再刷 404。
 */
function installLive2dApiShim() {
  if (window.__nekoLive2dApiShim) return;
  window.__nekoLive2dApiShim = true;
  const origFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (url.includes('/api/live2d/')) {
        console.warn('[neko-bridge] 插件面板拦截 Live2D 宿主 API（返回空数据）：', url);
        const body = url.includes('/models') ? '[]' : '{}';
        return Promise.resolve(new Response(body, {
          status: 200, headers: { 'Content-Type': 'application/json' },
        }));
      }
    } catch { /* 解析不了就按原样放行 */ }
    return origFetch(input, init);
  };
}

/** 插一个 <script>，加载失败要 reject（否则后面报错看不懂） */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-neko-live2d="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded === '1') { resolve(); return; }
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () => reject(new Error(`SCRIPT_LOAD_FAILED:${src}`)));
      return;
    }
    const s = document.createElement('script');
    s.src = src;
    s.async = false;
    s.dataset.nekoLive2d = src;
    s.addEventListener('load', () => { s.dataset.loaded = '1'; resolve(); });
    s.addEventListener('error', () => reject(new Error(`SCRIPT_LOAD_FAILED:${src}`)));
    document.head.appendChild(s);
  });
}

/**
 * 拉起 Live2D 引擎栈（幂等，多次调用共享同一个 Promise）。
 * @returns {Promise<boolean>} 引擎是否可用
 */
export function ensureEngine() {
  if (_enginePromise) return _enginePromise;
  _enginePromise = (async () => {
    const { host, scripts } = engineSources();
    if (pluginUiBase()) installLive2dApiShim();
    console.log('[neko-bridge] Live2D 引擎加载开始：', host, scripts.length, '个引擎脚本');
    // avatar-host 优先：它提供 mount 契约和 fitLive2DModel
    await loadScript(host);
    for (const src of scripts) await loadScript(src);
    const ok = (
      typeof window.Live2DManager === 'function'
      && typeof window.NekoMiniGameAvatarHost !== 'undefined'
    );
    console.log('[neko-bridge] Live2D 引擎加载结果：', ok ? '成功' : '失败');
    return ok;
  })().catch((e) => {
    // 失败就把 Promise 清掉，允许下次重试（比如用户中途才把 NEKO 开起来）
    _enginePromise = null;
    console.warn('[neko] Live2D 引擎加载失败：', e && e.message);
    return false;
  });
  return _enginePromise;
}

/**
 * 给容器准备一个 canvas（宿主模式下这个 canvas 写在 HTML 里，
 * 独立模式下槽位是空的，得自己造）。
 */
function ensureCanvas(container, canvasId) {
  let canvas = document.getElementById(canvasId);
  if (!canvas || !container.contains(canvas)) {
    canvas = document.createElement('canvas');
    canvas.id = canvasId;
    canvas.className = 'neko-live2d-canvas';
    // 尺寸交给引擎按容器算，这里只保证它能显示
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.display = 'block';
    container.appendChild(canvas);
  }
  return canvas;
}

/**
 * 在独立运行时把 Live2D 挂进容器。
 *
 * @param {HTMLElement} container 槽位元素（#neko-avatar-slot）
 * @param {string} modelPath      模型 .model3.json 路径
 * @param {{canvasId?:string, slot?:string}} [opts]
 * @returns {Promise<object|null>} 控制器，失败返回 null
 */
export async function mountStandaloneLive2D(container, modelPath, opts = {}) {
  if (!container || !modelPath) return null;
  const ok = await ensureEngine();
  if (!ok) return null;

  const canvasId = opts.canvasId || 'neko-live2d-canvas';
  const slot = opts.slot || 'yui';
  const canvas = ensureCanvas(container, canvasId);

  // 容器尺寸为 0 时（比如整块被 :empty 规则隐藏）引擎算不出 viewport，
  // 先量一次，量不到就放弃。
  const rect = container.getBoundingClientRect();
  if (!(rect.width > 0) || !(rect.height > 0)) return null;

  const host = window.NekoMiniGameAvatarHost.create({
    slots: {
      [slot]: {
        containerId: container.id,
        createController: async (config) => {
          if (typeof window.Live2DManager !== 'function') {
            throw new Error('LIVE2D_ENGINE_UNAVAILABLE');
          }
          const mgr = new window.Live2DManager();
          // live2d-core / live2d-interaction 依赖这个全局
          window.live2dManager = mgr;

          const path = (config && config.model && config.model.path) || modelPath;
          await mgr.initPIXI(canvasId, container.id, {});
          if (typeof mgr.loadModel === 'function') {
            await mgr.loadModel(path, { minimalEmbed: true });
            mgr._loadedModelPath = path;
          }
          return {
            resize: (viewport, fit) => {
              try {
                if (mgr.pixi_app && mgr.pixi_app.renderer) {
                  const w = Math.max(1, Math.round(viewport.width));
                  const h = Math.max(1, Math.round(viewport.height));
                  mgr.pixi_app.renderer.resize(w, h);
                  if (typeof window.NekoMiniGameAvatarHost.fitLive2DModel === 'function'
                      && mgr.currentModel) {
                    window.NekoMiniGameAvatarHost.fitLive2DModel(
                      mgr.currentModel, { width: w, height: h }, fit || {});
                  }
                }
              } catch (e) {
                console.warn('[neko] avatar resize 失败:', e && e.message);
              }
            },
            setModel: async (model) => {
              const p = (model && model.path) || path;
              // 幂等：createController 已经加载过同一个模型
              if (mgr.currentModel && mgr._loadedModelPath === p) return;
              if (typeof mgr.loadModel === 'function') {
                await mgr.loadModel(p, { minimalEmbed: true });
                mgr._loadedModelPath = p;
              }
            },
            getState: () => ({
              modelLoaded: !!mgr.currentModel,
              emotion: mgr.currentEmotion || 'neutral',
              modelName: mgr.modelName || null,
            }),
            focus: (point) => { try { mgr.setFocus && mgr.setFocus(point); } catch { /* */ } },
            setEmotion: (name) => { try { mgr.setEmotion && mgr.setEmotion(name); } catch { /* */ } },
            pause: () => { try { mgr.pauseRendering && mgr.pauseRendering(); } catch { /* */ } },
            resume: () => { try { mgr.resumeRendering && mgr.resumeRendering(); } catch { /* */ } },
            dispose: () => {
              try { mgr.pauseRendering && mgr.pauseRendering(); } catch { /* */ }
              try { mgr.destroy && mgr.destroy(); } catch { /* */ }
            },
          };
        },
      },
    },
  });

  const ctrl = await host.mount({
    slot,
    model: { type: 'live2d', path: modelPath },
    viewport: { mode: 'container' },
    fit: { mode: 'contain', align: 'bottom-center', padding: 4 },
    resize: { mode: 'container' },
  });
  return ctrl;
}

/** 测试/换装用：把已加载的引擎 Promise 清掉 */
export function _resetEngine() {
  _enginePromise = null;
}
