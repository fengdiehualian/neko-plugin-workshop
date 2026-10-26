// ============================================================
// 不可通行地形（第十二轮，用户要求）
//
// 用户原话：
//   "我们最初的地图的深色方块还有花园地图的花坛变成不可进入地形，包括敌人，
//    然后把框使用原背景的颜色略深一点描个边（不要画在地图素材里，直接放游戏里
//    让玩家理解这是墙就可以了）增加场景复杂度，但是boss场景不使用这两张有
//    复杂地形的图。"
//
// 拆成四条独立的要求，逐条对应到下面的设计：
//
//   1) 实验室的深色方块 + 花园的花坛 → 不可通行（玩家**和敌人**都挡）
//   2) 用"原背景色略深一点"描边，让玩家一眼看懂这是墙
//   3) 描边**画在游戏里**，不烘焙进地图素材
//      —— 这样以后换/重做背景图，描边不用跟着重画；而且描边是矢量画的，
//         不管相机缩放到多少都清晰，烘进 JPEG 会糊。
//   4) Boss 波不使用这两张图
//
// ------------------------------------------------------------
// 为什么用"归一化坐标 + 世界平铺"而不是写死世界坐标
//
// 背景图是以 BG_TILE（1024 世界像素）为周期平铺的，而且图案原点锚在
// **世界坐标原点**（见 renderer._drawBackground）。所以一张图上的一个
// 方块，在世界里其实会重复出现在无数个位置：
//
//     世界位置 = 图案内坐标 + (i, j) × BG_TILE     （i, j 为整数）
//
// 如果地形只按"图里那个位置"写一份世界坐标，那它只在其中一个格子里挡路，
// 而玩家在别的格子上会看到**一模一样的深色方块却能穿过去** —— 这是最糟糕
// 的一种 bug，因为画面在骗人。
//
// 所以这里存的是**图案内的归一化坐标**（0~1），运行时再展开成所有重复实例。
// 好处是坐标直接来自对图片的像素测量（artkit/dark-rects.mjs 的输出），
// 图和碰撞盒天然对齐，改图也不用改代码。
// ============================================================

/**
 * 竞技场边长（世界像素）。
 *
 * ⚠️ 这里**刻意重复**了 world.js 里的 `WORLD_SIZE = 2400`，而不是 import 它。
 * 原因是模块循环：world.js 要 import terrain.js 来做碰撞，
 * 如果 terrain.js 反过来 import world.js 拿这个常量，就形成循环依赖。
 * ESM 能容忍这种循环（提升的 const 在求值时通常已就绪），但它非常脆弱 ——
 * 换个打包器、换个加载顺序就会变成 `undefined`，然后竞技场边长变成 NaN，
 * 所有障碍物坐标全废。复制一个 2400 换来零风险，是划算的。
 *
 * 一致性由测试保证：test/terrain-check.mjs 会断言这两个值相等。
 */
import { buildMaze, SNOW_MAZE } from './maze.js';

export const WORLD_SIZE = 2400;

/** 背景图的平铺周期（世界像素）。和 renderer._ensureBgCache 里的 tile 一致。 */
export const BG_TILE = 1024;

/**
 * 一个障碍物定义（图案内归一化坐标）。
 * @typedef {Object} ObstacleDef
 * @property {number} x0 @property {number} y0  左上角（0~1）
 * @property {number} x1 @property {number} y1  右下角（0~1）
 */

/**
 * 各地图的地形。
 *
 * 坐标全部来自 `artkit/dark-rects.mjs` 的**像素实测**，不是估的：
 *
 *   实验室（bg_lab.jpg，阈值 0.70）测到 6 个 124×124 的深色方块，
 *   排在 128 像素的网格上。这里取它们的归一化范围。
 *
 *   花园（bg_garden.jpg）中央一个圆形花坛，用**圆**而不是矩形描述
 *   （见下面的 circles）—— 花坛是圆的，用外接矩形会挡住四个角上的
 *   正常地面，玩家会觉得"明明没东西却走不过去"。
 */
export const TERRAIN = {
  // ---- 猫娘实验室：6 块深色地砖 ----
  lab: {
    name: '猫娘实验室',
    // 6 个深色方块，归一化坐标（x0, y0, x1, y1）
    rects: [
      [0.8770, 0.1270, 1.0000, 0.2480],
      [0.6270, 0.2520, 0.7480, 0.3730],
      [0.7520, 0.5020, 0.8730, 0.6230],
      [0.0000, 0.6270, 0.1230, 0.7480],
      [0.3770, 0.7520, 0.4980, 0.8730],
      [0.8770, 0.7520, 1.0000, 0.8730],
    ],
    circles: [],
  },

  // ---- 修剪花园：中央圆形花坛 ----
  garden: {
    name: '修剪花园',
    rects: [],
    // 圆心在图案正中，半径 0.12（和美术要求的半径一致）
    circles: [
      { cx: 0.5, cy: 0.5, r: 0.12 },
    ],
  },

  // ---- 月光森林（第一百八十五轮新增；tools/make-bg-new.py 生成，贴图与碰撞同源）----
  // 9 个圆：6 棵树冠 + 3 块长苔巨石（碰撞半径 = 画出来半径的 92%，贴图里树冠有阴影晕边）
  forest: {
    name: '月光森林',
    rects: [
    ],
    circles: [
      { cx: 0.1800, cy: 0.2200, r: 0.0782 },
      { cx: 0.6200, cy: 0.1200, r: 0.0690 },
      { cx: 0.8300, cy: 0.4700, r: 0.0828 },
      { cx: 0.3500, cy: 0.6200, r: 0.0736 },
      { cx: 0.7000, cy: 0.8200, r: 0.0644 },
      { cx: 0.0800, cy: 0.8000, r: 0.0598 },
      { cx: 0.4800, cy: 0.3800, r: 0.0368 },
      { cx: 0.9300, cy: 0.1200, r: 0.0322 },
      { cx: 0.2200, cy: 0.4600, r: 0.0322 },
    ],
  },

  // ---- 雪原（第一百八十五轮新增；tools/make-bg-new.py 生成，贴图与碰撞同源）----
  // 9 个圆：冰封池塘(最大) + 5 棵覆雪松 + 3 块冰岩
  snow: {
    name: '雪原',
    // 第一百九十八轮：世界坐标里的「冰晶迷宫」（唯一一座，不随图案平铺）。见 data/maze.js
    maze: SNOW_MAZE,
    rects: [
    ],
    circles: [
      { cx: 0.3000, cy: 0.3500, r: 0.0920 },
      { cx: 0.7500, cy: 0.2000, r: 0.0552 },
      { cx: 0.8200, cy: 0.6200, r: 0.0598 },
      { cx: 0.1500, cy: 0.7800, r: 0.0552 },
      { cx: 0.5500, cy: 0.8500, r: 0.0506 },
      { cx: 0.5200, cy: 0.1200, r: 0.0460 },
      { cx: 0.9000, cy: 0.3800, r: 0.0368 },
      { cx: 0.4000, cy: 0.6600, r: 0.0322 },
      { cx: 0.6800, cy: 0.4500, r: 0.0276 },
    ],
  },

  // ---- 沙漠遗迹（第一百八十五轮新增；tools/make-bg-new.py 生成，贴图与碰撞同源）----
  // 5 个圆(4 株仙人掌 + 1 块红岩) + 6 个矩形(4 块砂岩石块 + 2 根倒塌石柱)
  desert: {
    name: '沙漠遗迹',
    rects: [
      [0.1000, 0.1400, 0.2000, 0.2400],
      [0.6000, 0.0800, 0.7000, 0.1800],
      [0.7800, 0.5200, 0.8800, 0.6200],
      [0.3000, 0.6000, 0.4000, 0.7000],
      [0.4500, 0.3400, 0.6200, 0.4000],
      [0.0500, 0.6200, 0.1000, 0.8200],
    ],
    circles: [
      { cx: 0.8800, cy: 0.1800, r: 0.0276 },
      { cx: 0.2400, cy: 0.9000, r: 0.0276 },
      { cx: 0.6200, cy: 0.6200, r: 0.0276 },
      { cx: 0.5000, cy: 0.9000, r: 0.0258 },
      { cx: 0.2000, cy: 0.4500, r: 0.0506 },
    ],
  },
};

/** 有复杂地形的地图 id（其余地图完全没有障碍物） */
export const TERRAIN_IDS = Object.keys(TERRAIN);

/**
 * Boss 波**不允许**使用的地图。
 *
 * 用户要求："boss 场景不使用这两张有复杂地形的图"。
 *
 * 为什么这条要单独存在、而不是在选图时过滤：背景是在 main.js 的
 * _beginWave 里选的，而"这一波是不是 Boss 波"只有 waves.js 知道。
 * 把这份名单放在地形模块里（而不是散在 main.js 的判断里），
 * 是为了让"哪些图有地形"和"哪些图 Boss 不能用"这两件事**始终一致** ——
 * 以后新增一张有地形图，只要往 TERRAIN 里加一条，Boss 波就自动避开它，
 * 不会出现"加了地形但忘了加进 Boss 黑名单"这种漏洞。
 */
export const BOSS_BANNED_BACKGROUNDS = TERRAIN_IDS;

/**
 * 把某个背景的地形展开成世界坐标的障碍物列表。
 *
 * @param {string} bgId 背景 id
 * @param {number} [worldSize] 竞技场边长（默认 WORLD_SIZE）
 * @returns {{rects: Array<{x0:number,y0:number,x1:number,y1:number}>, circles: Array<{cx:number,cy:number,r:number}>}}
 *
 * ------------------------------------------------------------
 * 图案原点是**世界原点**，不是竞技场左上角
 * ------------------------------------------------------------
 * 这是本节最容易写错的地方，所以把推导写下来。
 *
 * 渲染器里背景的屏幕偏移是（renderer._drawBackground）：
 *     bgOx = ((-camera.cx + W/2) mod tile + tile) mod tile
 * 缓存贴图画在屏幕 `bgOx - tile` 处，缓存第 0 像素 = 图案坐标 0。
 * 于是屏幕上 x 处的像素取到的图案坐标是
 *     (x - bgOx + tile) mod tile = (x - bgOx) mod tile
 * 而屏幕 x 对应的世界坐标是 w = camera.cx + x - W/2，所以
 *     x - bgOx ≡ x + camera.cx - W/2 = w   (mod tile)
 * 也就是：**图案坐标 ≡ 世界坐标 (mod tile)**，图案原点锚在世界原点。
 *
 * 结论：一个归一化坐标 n 的图案元素，在世界里的重复位置是
 *     n * BG_TILE + i * BG_TILE        （i、j 为整数）
 * **没有** -worldSize/2 那一项。
 *
 * 我第一版就是错写成了 `-half + i*BG_TILE`（想当然地以为图案从竞技场
 * 左上角开始铺），结果所有碰撞盒整体偏移了 1200 像素 ——
 * 玩家会看到"深色方块在这儿、墙却在那儿"。测试里有一条专门盯这个：
 * 断言图案正中的障碍物落在世界 (0, 0) 附近。
 *
 * 只在**确实与竞技场相交**时输出，避免生成一堆界外的无用障碍物。
 */
export function expandTerrain(bgId, worldSize = WORLD_SIZE) {
  const t = TERRAIN[bgId];
  const out = { rects: [], circles: [] };
  if (!t) return out;

  const half = worldSize / 2;
  // ---- 第一百九十八轮：迷宫 + 冰砖广场（plaza）----
  // 迷宫区域里贴图烘焙的池塘/松树会透出来 —— 所以迷宫下面铺一块不透明广场把它们盖住，同时撤掉广场内的图案碰撞体：
  // 「画什么就挡什么」（第十二轮的铁律）。广场从"迷宫外框 + 边距"起步，把任何与它相交的图案障碍的整个画出范围
  // （视觉半径 = 碰撞半径 / 0.92）吞进来，直到稳定；这样不会留下"半个池塘露在广场外却没有碰撞"的画面。
  let maze = null, plaza = null;
  if (t.maze) {
    maze = buildMaze(t.maze);
    plaza = { x0: maze.x0 - 50, y0: maze.y0 - 50, x1: maze.x1 + 50, y1: maze.y1 + 50 };
    const inst = [];
    const sp = Math.ceil(half / BG_TILE) + 1;
    for (let i = -sp; i <= sp; i++) for (let j = -sp; j <= sp; j++) for (const c of t.circles) {
      const rr = (c.r * BG_TILE) / 0.92;
      inst.push({ cx: i * BG_TILE + c.cx * BG_TILE, cy: j * BG_TILE + c.cy * BG_TILE, rr });
    }
    for (let grow = true, guard = 0; grow && guard < 20; guard++) {
      grow = false;
      for (const c of inst) {
        if (c.cx + c.rr <= plaza.x0 || c.cx - c.rr >= plaza.x1 || c.cy + c.rr <= plaza.y0 || c.cy - c.rr >= plaza.y1) continue;
        const nx0 = Math.min(plaza.x0, c.cx - c.rr), ny0 = Math.min(plaza.y0, c.cy - c.rr), nx1 = Math.max(plaza.x1, c.cx + c.rr), ny1 = Math.max(plaza.y1, c.cy + c.rr);
        if (nx0 < plaza.x0 || ny0 < plaza.y0 || nx1 > plaza.x1 || ny1 > plaza.y1) { plaza = { x0: nx0, y0: ny0, x1: nx1, y1: ny1 }; grow = true; }
      }
    }
  }
  const inPlaza = (cx, cy, r) => plaza && cx + r > plaza.x0 && cx - r < plaza.x1 && cy + r > plaza.y0 && cy - r < plaza.y1;
  // 需要覆盖 [-half, half] 的重复范围：i 从 -ceil(half/tile) 到 +ceil(half/tile)
  const span = Math.ceil(half / BG_TILE) + 1;
  for (let i = -span; i <= span; i++) {
    for (let j = -span; j <= span; j++) {
      const ox = i * BG_TILE;
      const oy = j * BG_TILE;

      for (const [x0, y0, x1, y1] of t.rects) {
        const r = {
          x0: ox + x0 * BG_TILE,
          y0: oy + y0 * BG_TILE,
          x1: ox + x1 * BG_TILE,
          y1: oy + y1 * BG_TILE,
        };
        if (r.x1 <= -half || r.x0 >= half || r.y1 <= -half || r.y0 >= half) continue;
        if (plaza && r.x1 > plaza.x0 && r.x0 < plaza.x1 && r.y1 > plaza.y0 && r.y0 < plaza.y1) continue;
        out.rects.push(r);
      }

      for (const c of t.circles) {
        const cc = {
          cx: ox + c.cx * BG_TILE,
          cy: oy + c.cy * BG_TILE,
          r: c.r * BG_TILE,
        };
        if (cc.cx + cc.r <= -half || cc.cx - cc.r >= half
          || cc.cy + cc.r <= -half || cc.cy - cc.r >= half) continue;
        if (inPlaza(cc.cx, cc.cy, cc.r / 0.92)) continue;   // 198 轮：广场内的图案障碍已被盖住，同步撤掉碰撞体
        out.circles.push(cc);
      }
    }
  }
  if (maze) {
    for (const w of maze.walls) out.rects.push(w);
    out.maze = maze;
    out.plaza = plaza;
  }
  return out;
}

/**
 * 某个位置是否在障碍物内。
 *
 * @param {{rects:Array,circles:Array}} terrain expandTerrain 的结果
 * @param {number} x @param {number} y
 * @param {number} [radius] 单位半径。>0 时按"圆和障碍物相交"判定，
 *        也就是**把单位的体积算进去**，否则单位会有一半嵌进墙里。
 * @returns {boolean}
 */
export function blockedAt(terrain, x, y, radius = 0) {
  if (!terrain) return false;

  for (const r of terrain.rects) {
    // 圆 vs 轴对齐矩形：先找矩形上离圆心最近的点，再比距离
    const nx = Math.max(r.x0, Math.min(x, r.x1));
    const ny = Math.max(r.y0, Math.min(y, r.y1));
    const dx = x - nx, dy = y - ny;
    if (dx * dx + dy * dy < radius * radius) return true;
    // radius 为 0 时上面的比较是 `0 < 0` = false，要单独处理"点在矩形内"
    if (radius === 0 && x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1) return true;
  }

  for (const c of terrain.circles) {
    const dx = x - c.cx, dy = y - c.cy;
    const rr = c.r + radius;
    if (dx * dx + dy * dy < rr * rr) return true;
  }
  return false;
}

/**
 * 把一个单位推出障碍物（碰撞解算）。
 *
 * 做法：如果当前位置被挡，就沿着"从障碍物指向单位"的方向推到边界上。
 * 用**最小平移**（推最近的边），而不是简单地往回退 —— 往回退会让单位
 * 卡在墙上抖动，因为它每帧都想往里走、每帧都被退回到同一个位置。
 * 推到最近边界上之后，单位会自然地沿着墙滑行。
 *
 * @param {{rects:Array,circles:Array}} terrain
 * @param {{x:number,y:number,radius:number}} ent 会被就地修改
 * @param {number} [prevX] @param {number} [prevY] 上一帧位置。
 *        给了就用"轴向分离"退法（更稳，不会穿角），
 *        没给就只用最小平移。
 * @returns {boolean} 是否发生了修正
 */
export function resolveTerrain(terrain, ent, prevX, prevY) {
  if (!terrain) return false;
  let hit = false;

  for (const r of terrain.rects) {
    // 圆 vs 矩形：最近点
    const nx = Math.max(r.x0, Math.min(ent.x, r.x1));
    const ny = Math.max(r.y0, Math.min(ent.y, r.y1));
    let dx = ent.x - nx, dy = ent.y - ny;
    const d2 = dx * dx + dy * dy;

    if (d2 >= ent.radius * ent.radius && d2 > 0) continue;   // 没碰上
    hit = true;

    if (d2 > 1e-9) {
      // 圆心在矩形外：沿最近点方向推到刚好相切
      const d = Math.sqrt(d2);
      const push = ent.radius - d;
      ent.x += (dx / d) * push;
      ent.y += (dy / d) * push;
    } else {
      // 圆心在矩形内（陷进去了）：选**最近的一条边**推出去，推得最少
      const dl = ent.x - r.x0, dr = r.x1 - ent.x;
      const dt = ent.y - r.y0, db = r.y1 - ent.y;
      const m = Math.min(dl, dr, dt, db);
      if (m === dl) ent.x = r.x0 - ent.radius;
      else if (m === dr) ent.x = r.x1 + ent.radius;
      else if (m === dt) ent.y = r.y0 - ent.radius;
      else ent.y = r.y1 + ent.radius;
    }
  }

  for (const c of terrain.circles) {
    const dx = ent.x - c.cx, dy = ent.y - c.cy;
    const d2 = dx * dx + dy * dy;
    const rr = c.r + ent.radius;
    if (d2 >= rr * rr) continue;
    hit = true;
    const d = Math.sqrt(d2);
    if (d < 1e-6) {
      // 正好在圆心：没有方向信息，随便挑一个方向推出去
      ent.x = c.cx + rr;
    } else {
      ent.x = c.cx + (dx / d) * rr;
      ent.y = c.cy + (dy / d) * rr;
    }
  }

  return hit;
}

/**
 * 找一个**不在障碍物里**的出生点。
 *
 * 为什么必须做这件事：出生点原本是"玩家周围 480~760 的随机角度"，
 * 完全可能正好落在深色方块或花坛里。那样敌人一出生就卡在墙里，
 * 玩家看到的是"怪在墙里抽搐"或者"墙里凭空冒出一堆弹幕"。
 *
 * 做法：最多试 `tries` 次，每次换一个随机角度和距离；全都不行就
 * 返回 null（调用方自行决定是放弃这次刷新还是硬放）。
 * 用有限次尝试而不是"穷举所有角度"，是为了避免极端情况下卡死主循环。
 *
 * @param {{rects:Array,circles:Array}} terrain
 * @param {() => {x:number,y:number}} pick 产生候选点的函数
 * @param {number} radius 单位半径
 * @param {number} [tries]
 * @returns {{x:number,y:number}|null}
 */
export function findFreeSpot(terrain, pick, radius, tries = 24) {
  if (!terrain || (terrain.rects.length === 0 && terrain.circles.length === 0)) {
    return pick();
  }
  for (let i = 0; i < tries; i++) {
    const p = pick();
    if (!blockedAt(terrain, p.x, p.y, radius)) return p;
  }
  return null;
}
