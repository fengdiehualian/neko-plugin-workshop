// 万圣节双猫战 · 5×7 像素字模（第一百八十四轮视觉重写）
// 两个用途共用同一张字模表：
//   ① 灯珠字母弹幕（TRICK / BUNNY EXTINCTION 那种由发光球拼出来的单词）
//   ② HUD 像素字（左上角招式名、底部血条标签）—— 原版是白字 + 黑描边的宽字距像素字
// 字模来自经典 5×7 点阵；笔画 1 格宽，灯珠间距 ≥ 光晕直径的 0.6 时视觉上串成"霓虹管"，
// 与 RABI_CANVAS2D 报告 §3 的"灯珠阵 + 字怀是负空间"一致。

const RAW = {
  A: '01110 10001 10001 11111 10001 10001 10001',
  B: '11110 10001 10001 11110 10001 10001 11110',
  C: '01110 10001 10000 10000 10000 10001 01110',
  D: '11110 10001 10001 10001 10001 10001 11110',
  E: '11111 10000 10000 11110 10000 10000 11111',
  F: '11111 10000 10000 11110 10000 10000 10000',
  G: '01110 10001 10000 10111 10001 10001 01111',
  H: '10001 10001 10001 11111 10001 10001 10001',
  I: '01110 00100 00100 00100 00100 00100 01110',
  J: '00111 00010 00010 00010 00010 10010 01100',
  K: '10001 10010 10100 11000 10100 10010 10001',
  L: '10000 10000 10000 10000 10000 10000 11111',
  M: '10001 11011 10101 10101 10001 10001 10001',
  N: '10001 11001 10101 10011 10001 10001 10001',
  O: '01110 10001 10001 10001 10001 10001 01110',
  P: '11110 10001 10001 11110 10000 10000 10000',
  Q: '01110 10001 10001 10001 10101 10010 01101',
  R: '11110 10001 10001 11110 10100 10010 10001',
  S: '01111 10000 10000 01110 00001 00001 11110',
  T: '11111 00100 00100 00100 00100 00100 00100',
  U: '10001 10001 10001 10001 10001 10001 01110',
  V: '10001 10001 10001 10001 10001 01010 00100',
  W: '10001 10001 10001 10101 10101 11011 10001',
  X: '10001 10001 01010 00100 01010 10001 10001',
  Y: '10001 10001 01010 00100 00100 00100 00100',
  Z: '11111 00001 00010 00100 01000 10000 11111',
  0: '01110 10001 10011 10101 11001 10001 01110',
  1: '00100 01100 00100 00100 00100 00100 01110',
  2: '01110 10001 00001 00010 00100 01000 11111',
  3: '11110 00001 00001 01110 00001 00001 11110',
  4: '00010 00110 01010 10010 11111 00010 00010',
  5: '11111 10000 11110 00001 00001 10001 01110',
  6: '00110 01000 10000 11110 10001 10001 01110',
  7: '11111 00001 00010 00100 01000 01000 01000',
  8: '01110 10001 10001 01110 10001 10001 01110',
  9: '01110 10001 10001 01111 00001 00010 01100',
  '!': '00100 00100 00100 00100 00100 00000 00100',
  '.': '00000 00000 00000 00000 00000 00000 00100',
  '/': '00001 00001 00010 00100 01000 10000 10000',
  '-': '00000 00000 00000 11111 00000 00000 00000',
  ' ': '00000 00000 00000 00000 00000 00000 00000',
};

/** 解析后的字模：char → 7 行 × 5 列的 0/1 数组 */
export const GLYPHS = {};
for (const [ch, rows] of Object.entries(RAW)) {
  GLYPHS[ch] = rows.split(' ').map((r) => [...r].map((c) => (c === '1' ? 1 : 0)));
}

export const GLYPH_W = 5;
export const GLYPH_H = 7;
/** 字间距（格）—— 报告：字间距 ≈ 6.5 格（含字宽 5），即 1.5 格空隙 */
export const LETTER_GAP = 1.5;
/** 行距（格）—— 字高 7 + 2.5 空隙 */
export const LINE_GAP = 2.5;

/**
 * 把若干行单词排成"格点坐标"（以整块文字中心为原点，单位 = 格）。
 * 返回 { cells: [{cx, cy, col}], widthCells, heightCells }
 *   col = 该灯珠所在的全局列号（用来做"从左到右依次点亮"的延迟）
 */
export function layoutWord(lines) {
  const rows = [];
  let maxW = 0;
  for (const line of lines) {
    const text = String(line).toUpperCase();
    const w = text.length * (GLYPH_W + LETTER_GAP) - LETTER_GAP;
    rows.push({ text, w });
    if (w > maxW) maxW = w;
  }
  const totalH = rows.length * GLYPH_H + (rows.length - 1) * LINE_GAP;
  const cells = [];
  rows.forEach((row, li) => {
    const oy = li * (GLYPH_H + LINE_GAP) - totalH / 2;
    const ox = -row.w / 2;
    [...row.text].forEach((ch, ci) => {
      const g = GLYPHS[ch] ?? GLYPHS[' '];
      const lx = ox + ci * (GLYPH_W + LETTER_GAP);
      for (let r = 0; r < GLYPH_H; r++) {
        for (let c = 0; c < GLYPH_W; c++) {
          if (!g[r][c]) continue;
          cells.push({ cx: lx + c + 0.5, cy: oy + r + 0.5, col: lx + c + maxW / 2 });
        }
      }
    });
  });
  return { cells, widthCells: maxW, heightCells: totalH };
}

// ---- HUD 像素字：白字 + 黑描边，无发光（报告 §4.1），整串烘成小画布缓存 ----
const _textCache = new Map();

/**
 * 烘焙一串像素字到离屏画布。
 * @param {string} text
 * @param {object} o  cell(每格像素) / advance(字符步进，px) / fill / outline(描边 px) / dpr
 */
export function textSprite(text, o = {}) {
  const cell = o.cell ?? 2;
  const advance = o.advance ?? (GLYPH_W + LETTER_GAP) * cell;
  const fill = o.fill ?? '#F8F8F8';
  const outline = o.outline ?? 2;
  const dpr = o.dpr ?? 1;
  const key = `${text}|${cell}|${advance}|${fill}|${outline}|${dpr}`;
  const hit = _textCache.get(key);
  if (hit) return hit;
  const s = String(text).toUpperCase();
  const w = Math.ceil(s.length * advance + outline * 2 + cell);
  const h = Math.ceil(GLYPH_H * cell + outline * 2 + cell);
  const cv = document.createElement('canvas');
  cv.width = Math.max(1, Math.ceil(w * dpr));
  cv.height = Math.max(1, Math.ceil(h * dpr));
  const g = cv.getContext('2d');
  g.scale(dpr, dpr);
  g.imageSmoothingEnabled = false;
  // 先整串描一圈黑（每格外扩 outline），再填白，保证描边连续无缺口
  for (const pass of [0, 1]) {
    g.fillStyle = pass === 0 ? '#000' : fill;
    const grow = pass === 0 ? outline : 0;
    [...s].forEach((ch, i) => {
      const gl = GLYPHS[ch] ?? GLYPHS[' '];
      const ox = outline + i * advance;
      for (let r = 0; r < GLYPH_H; r++) {
        for (let c = 0; c < GLYPH_W; c++) {
          if (!gl[r][c]) continue;
          g.fillRect(ox + c * cell - grow, outline + r * cell - grow, cell + grow * 2, cell + grow * 2);
        }
      }
    });
  }
  const out = { canvas: cv, w, h };
  if (_textCache.size > 120) _textCache.clear();
  _textCache.set(key, out);
  return out;
}
