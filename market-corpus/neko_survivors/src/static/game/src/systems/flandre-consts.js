// 芙兰战共享常量（第一百八十五轮）。
// 单独成文件是为了避免 flandre.js ↔ flandre-spells.js 的循环依赖：
// 符卡脚本文件要读场地尺寸，而 flandre.js 又要 import 符卡脚本。
export const ARENA = { cx: 0, cy: -80, hw: 520, hh: 470 };
/** 舞台点：她"住"的位置（地图中心上方 STAGE_DY） */
export const STAGE_DY = 270;
