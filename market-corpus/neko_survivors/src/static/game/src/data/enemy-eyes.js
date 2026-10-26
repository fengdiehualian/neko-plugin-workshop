// ============================================================
// 敌人眼睛锚点表 —— **自动生成，不要手改**
//
// 生成器：artkit/enemy-eye-apply.mjs
//   （脸的位置来自 artkit/enemy-face-grid.mjs 的视觉标注）
//
// 背景（用户第九轮）："有的小怪的形象的眼睛飞出了自己的脸部。
//   你可以看看怪物们的眼睛适配是否有问题，尽量让眼睛不要出了它自己的脸。"
//
// 根因是**两套坐标系**：
//   贴图按 S = 2.6r 画在 (-S/2, -0.60S)，世界 y ∈ [-1.56r, +1.04r]
//   眼睛却按 r 尺度画在敌人原点附近，而且有一份 0.6r 的横向"看向"偏移。
// 于是眼睛既不在贴图的脸心上（偏右 0.17r），间距也不随头大小变化，
// 在贴图上就表现为"右眼压着右边脸颊/轮廓"。
//
// 现在：
//   1. 眼睛**居中在视觉标注出的脸心**上（不再有固定的横向偏移）
//   2. 间距/半径都随"脸宽"缩放
//   3. 再用该行贴图的**实际身体左右边界**夹紧（l/r + margin），
//      所以无论脸被估得偏多少，眼睛都不可能出轮廓
//
// u/v: 眼心在图内的 0~1 比例；wf: 脸宽占图宽；
// l/r: 眼睛那一行的身体左右边界（0~1），renderer 用它做最终夹紧。
// 换算到渲染坐标（贴图画在 (-S/2, -0.60S)，S = 2.6r）：
//   wx = (u - 0.5) * S     wy = -0.60 * S + v * S
// ============================================================

/** 未收录的敌人（新增的 / Boss）兜底：上中部，脸宽取一半 */
export const EYE_ANCHOR_FALLBACK = { u: 0.5, v: 0.30, wf: 0.5, l: 0.2, r: 0.8 };

/** 两眼间距 / 眼半径 / 距边缘留白 / 朝玩家位移上限，都相对"脸宽" */
export const EYE_SHAPE = {
  spacing: 0.46,
  radius: 0.13,
  margin: 0.12,
  lookGain: 0.06,
};

/** id -> { u, v, wf, l, r } */
export const EYE_ANCHORS = {
  "bat": { u: 0.5, v: 0.5781, wf: 0.64, l: 0.0547, r: 0.9414 },
  "blade_cat": { u: 0.5, v: 0.5117, wf: 0.64, l: 0.0742, r: 0.9219 },
  "bomber": { u: 0.5, v: 0.5508, wf: 0.8, l: 0.0469, r: 0.9531 },
  "chaos_egg": { u: 0.5, v: 0.5586, wf: 0.84, l: 0.0703, r: 0.9375 },
  "charger": { u: 0.5, v: 0.6211, wf: 0.64, l: 0.0938, r: 0.8594 },
  "crab": { u: 0.5, v: 0.5508, wf: 0.7, l: 0.0898, r: 0.9609 },
  "elite_crab": { u: 0.5, v: 0.5508, wf: 0.84, l: 0.0703, r: 0.9297 },
  "elite_spitter": { u: 0.5, v: 0.6094, wf: 0.7, l: 0.1211, r: 0.8789 },
  "ghost": { u: 0.5, v: 0.5, wf: 0.6, l: 0.1914, r: 0.8086 },
  // 第十二轮新增：奶妈怪（樱花护士）。
  //
  // l/r 用 artkit/row-bounds.mjs **实测**，不是估的：
  //     node artkit/row-bounds.mjs assets/enemies/enemy_healer_bunny.png 0.62
  //     v=0.620 → y=158 → l=0.2773  r=0.7227（脸宽 0.445）
  // 第一版我按"最宽处"填了 l=0.1875/r=0.8125，那是**耳朵**的宽度，
  // 结果 test/enemy-eye-check.mjs 立刻报"眼睛边缘压出轮廓 32 例"。
  // 这只怪上半部分是长耳朵，脸比整体轮廓窄得多，必须按脸那一行量。
  //
  // u 取 0.5（脸确实居中），v=0.621 是实测脸心所在的采样行。
  "healer_bunny": { u: 0.5, v: 0.6211, wf: 0.44, l: 0.2773, r: 0.7227 },
  "mouse": { u: 0.45, v: 0.5586, wf: 0.7, l: 0.0547, r: 0.9609 },
  "slime": { u: 0.5, v: 0.5703, wf: 0.7, l: 0.0742, r: 0.9297 },
  "spiral_cat": { u: 0.5, v: 0.5195, wf: 0.64, l: 0.082, r: 0.9219 },
  "spitter": { u: 0.5, v: 0.6211, wf: 0.8, l: 0.0938, r: 0.9023 },
  "splitter": { u: 0.4851, v: 0.6016, wf: 0.56, l: 0.168, r: 0.7578 },
  "stalker": { u: 0.61, v: 0.5586, wf: 0.53, l: 0.0625, r: 0.9414 },
  "tank": { u: 0.5, v: 0.5586, wf: 0.84, l: 0.0469, r: 0.9531 },
  "treasure_mimic": { u: 0.5, v: 0.5898, wf: 0.64, l: 0.1328, r: 0.9609 },
  "treasure_mimic_elite": { u: 0.4506, v: 0.5117, wf: 0.64, l: 0.0781, r: 0.7617 },
};

/** 取某个敌人的锚点，没有就用兜底 */
export function eyeAnchor(id) {
  return EYE_ANCHORS[id] || EYE_ANCHOR_FALLBACK;
}

/** 贴图绘制尺度：贴图边长 = SPRITE_SCALE × 敌人半径 */
export const SPRITE_SCALE = 2.6;
/** 贴图左上角的 y 偏移（占贴图边长的比例） */
export const SPRITE_OFFSET_Y = -0.60;
/** 眼半径的下限，相对敌人半径（很小的怪也要看得见眼睛） */
export const EYE_MIN_OF_RADIUS = 0.06;

/**
 * 算出两只眼睛在**敌人本地坐标系**里的位置（敌人原点为中心，单位与半径一致）。
 *
 * 这是眼睛几何的**唯一实现**：renderer 用它来画，测试用它来采样 alpha。
 * 抽成纯函数就是为了避免"renderer 里一套、测试里抄一套"——那样改了渲染
 * 而测试还绿着，等于没测。
 *
 * @param {{u:number,v:number,wf:number,l:number,r:number}} anchor
 * @param {number} radius 敌人半径（r）
 * @param {number} [facing] 朝向弧度（眼睛整体朝玩家偏一点）
 * @returns {{eyeR:number, size:number, offY:number, faceW:number,
 *            spanL:number, spanR:number, eyes:{x:number,y:number}[]}}
 */
export function eyePlacement(anchor, radius, facing = 0) {
  const a = anchor || EYE_ANCHOR_FALLBACK;
  const size = SPRITE_SCALE * radius;
  const offY = SPRITE_OFFSET_Y * size;
  const faceW = a.wf * size;
  const eyeR = Math.max(radius * EYE_MIN_OF_RADIUS, faceW * EYE_SHAPE.radius);
  const toX = (u) => -size / 2 + u * size;
  const toY = (v) => offY + v * size;
  const baseX = toX(a.u);
  const baseY = toY(a.v);
  const spacingHalf = faceW * EYE_SHAPE.spacing / 2;
  const lookGain = faceW * EYE_SHAPE.lookGain;
  const lookX = Math.cos(facing) * lookGain;
  const lookY = Math.sin(facing) * lookGain * 0.6;
  const margin = faceW * EYE_SHAPE.margin;
  const spanL = toX(a.l);
  const spanR = toX(a.r);
  const loX = spanL + margin + eyeR;
  const hiX = spanR - margin - eyeR;
  let dx = spacingHalf;
  let cx = baseX + lookX;
  if (hiX > loX) {
    // 头太窄时先把间距压小，再把整组居中推进边界内
    dx = Math.min(dx, (hiX - loX) / 2);
    cx = Math.max(loX + dx, Math.min(hiX - dx, cx));
  } else {
    // 连一只眼睛都放不下：退回"该行身体的正中"，不允许出界
    cx = (spanL + spanR) / 2;
    dx = 0;
  }
  return {
    eyeR, size, offY, faceW, spanL, spanR,
    eyes: [-1, 1].map((side) => ({ x: cx + side * dx, y: baseY + lookY })),
  };
}
