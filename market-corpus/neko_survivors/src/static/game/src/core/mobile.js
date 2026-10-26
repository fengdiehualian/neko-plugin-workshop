// ============================================================
// 移动端环境探测与性能档位（第一百六十二轮）
//
// 用户需求："把我们现有的代码打包成 Android App……移动端性能降级
// （极其重要）……自动降低粒子特效数量、关闭阴影（shadowBlur）、
// 减少天气的渲染密度，确保能稳住 60 帧。"
//
// 【红线】桌面版零行为变化：MOBILE 探测在桌面浏览器/本地文件/NEKO
// 实机上恒为 false，所有消费点都是 `MOBILE ? … : 原值` 的三目门控，
// 桌面走的一律是原有字面值。
//
// 手机版壳（mobile/ 目录）与电脑版完全独立：手机版 index.html 由
// mobile/build.mjs 从桌面 index.html 派生并注入触屏层，电脑版的
// index.html 不引用本文件之外任何移动端脚本。
// ============================================================

// ---- 探测（同步、无副作用，可在任意模块 import 时求值）----
const isMobileUA = typeof navigator !== 'undefined'
  && /Android|iPhone|iPad|iPod|Mobile|Silk/i.test(navigator.userAgent || '');
const hasCoarsePointer = typeof window !== 'undefined'
  && typeof window.matchMedia === 'function'
  && window.matchMedia('(pointer: coarse)').matches;
const isCapacitor = typeof window !== 'undefined'
  && !!(window.Capacitor?.isNativePlatform?.());

/** 真·移动环境（原生 App / 手机 UA / 触摸主指针） */
export const MOBILE = !!(isCapacitor || (isMobileUA && hasCoarsePointer) || isMobileUA);

/**
 * 移动端性能档位（桌面全不生效）：
 *  - FX_MAX_ITEMS   同屏特效上限（桌面 240 → 手机 120，fx.js 基准 150≈6ms/帧）
 *  - SHADOW_BLUR    是否允许 shadowBlur（17 处使用，手机上关闭——Canvas 2D
 *                   的阴影是逐帧高斯，移动 GPU 上是最大的帧率杀手之一）
 *  - WEATHER_MUL    天气视觉密度倍率（雨滴/雾团/龙卷数量）
 *  - TITLE_PARTICLES 标题星空粒子倍率
 *  - DPR_CAP        devicePixelRatio 上限（桌面 2 → 手机 1.5，像素填充率
 *                   是手机的第二大瓶颈；1.5 在 6 英寸屏上依然锐利）
 */
export const MOBILE_TUNE = {
  FX_MAX_ITEMS: 80,
  SHADOW_BLUR: false,
  WEATHER_MUL: 0.4,
  TITLE_PARTICLES: 0.5,
  DPR_CAP: 1.0,
  /** 同屏普通怪上限（boss/剧情召唤不受限）——第一百六十三轮 */
  MAX_ENEMIES: 50,
};
