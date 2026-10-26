// 第一百八十五轮：无 DOM 环境下的画布工厂。
//
// 起因：本轮新增的特效模块（bullet-fx / comet-fx / vergil-fx / weather-fx / flandre-render）都在首次绘制时
// 用 document.createElement('canvas') 烘焙精灵。浏览器里没问题，但 Node 测试（round69 等会真调用 drawBg）
// 里没有 document，直接抛 ReferenceError。项目的一贯原则是"素材没就绪就不画、战斗照常"，特效模块也应遵守。
//
// 有 document 时：原样返回真画布（行为与改动前逐位一致）。
// 没有 document 时：返回一个无操作画布，烘焙与绘制都安静空转。
const NOOP_CTX = new Proxy({}, {
  get(_t, p) {
    if (p === 'createLinearGradient' || p === 'createRadialGradient' || p === 'createPattern') return () => ({ addColorStop() {} });
    if (p === 'measureText') return (s) => ({ width: String(s ?? '').length * 8 });
    return () => {};
  },
  set() { return true; },
});

export function makeCanvas() {
  if (typeof document !== 'undefined') return document.createElement('canvas');
  return { width: 0, height: 0, getContext: () => NOOP_CTX };
}
