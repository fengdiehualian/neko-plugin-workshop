// 普通 Boss 招牌技能（第一百八十五轮 · 新增 2 个远程 Boss）
//
// 用户："闲暇之余给我们设计 2 个新的普通 boss 出来"；素材取自 rabiribi 弹幕库 DANMAKU-LIBRARY.md：
//   潘多拉(Pandora，魔女)：「格镜落弹」—— 弹幕按 64px 网格上下镜像砸落，先镜像后乱数
//   诺诺(Nono，机器人)  ：「天幕横雨」+「三向落柱」—— 斜雨铺满天幕，三向落柱圈出安全方块
//
// 这里只负责"招牌"：Boss 通用的环形弹/召唤/阶段仍走 enemy.js 的 _boss()。
// 每个技能是 (e, dt, ctx) 的纯函数式状态机，状态存在 e._sig 上；所有时间用 dt 累加。
// 随机：优先用 world.rng（确定性），没有则退回固定序列 —— 绝不用 Math.random。
//
// 难度原则（沿用芙兰/万圣节的做法）：
//   · 判定半径 = 视觉半径 × 0.6；
//   · 每个图样都留"读得懂的安全区"（落弹留缝列 / 落柱之间留方块 / 雨有稀疏带）；
//   · 同屏弹量有上限（SIG_BULLET_CAP），超限停发，防卡顿；
//   · 三阶段递进：1 阶段单一图样，2 阶段叠加第二图样，3 阶段加快 + 追加第三图样。

const TAU = Math.PI * 2;
export const SIG_BULLET_CAP = 260;

function rngOf(world) {
  const r = world?.rng;
  if (r && typeof r.next === 'function') return () => r.next();
  let s = 0x2f6e2b1;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

/** 本 Boss 一发弹的伤害（口径与 enemy.js _firePattern 一致） */
function dmgOf(e) {
  const def = e.def;
  return (def.projectileDamage ?? (def.damage ?? 3)) * (e.dmgScale ?? 1)
    * (1 + (e.buffDamagePct ?? 0) / 100) * (e.curseDamageMul ?? 1);
}

/** 发一颗弹（统一加判定缩小 + 同屏上限）。返回是否真的发了 */
function put(e, world, x, y, angle, speed, shape, color, r, range = 900) {
  const list = world.enemyBullets?.items;
  if (list && list.length >= SIG_BULLET_CAP) return false;
  world.spawnEnemyBullet({
    trueTier: e.trueTier, x, y, angle, speed, damage: dmgOf(e),
    radius: r, hitRadius: r * 0.6, color, range, shape,
  });
  return true;
}

// ---------------------------------------------------------------
// 潘多拉 · 格镜落弹
// ---------------------------------------------------------------
// 1 阶段：每 3.4s 一次「落弹」—— 以玩家为中心铺 64px 网格列，随机挑一部分列，
//         **左右镜像**同时落下，正对玩家的 2 列留空（安全缝，每次缝的位置随机偏一格）。
// 2 阶段：叠加「侧击」—— 左右对称的两排横向弹（行也按 64px 网格，留一行缝）。
// 3 阶段：落弹加快到 2.4s、列更多；叠加 Boss 身周缓转的 3 臂星弹螺旋。
const GRID = 64;
function pandora(e, dt, ctx) {
  const { world, player, phase } = ctx;
  const S = e._sig ??= { drop: 1.6, side: 3.0, spiral: 0, rot: 0 };
  const rnd = rngOf(world);
  const px = player.x, py = player.y;

  S.drop -= dt;
  if (S.drop <= 0) {
    S.drop = phase >= 3 ? 2.4 : phase === 2 ? 3.0 : 3.4;
    const cols = phase >= 3 ? 11 : phase === 2 ? 9 : 7;           // 单侧最多几列
    // 镜像轴 = 安全缝的中心：放在玩家侧方 2~4 格处（左右随机）。
    // 模拟器教训：缝若正对玩家脚下，站桩玩家一次都不会被打（1 阶段形同虚设）；
    // 现在必须横移 128~256px 才能进缝，落弹 2 秒到达，走得过去。
    const axis = px + (rnd() < 0.5 ? -1 : 1) * GRID * (2 + Math.floor(rnd() * 3)) + (Math.floor(rnd() * 3) - 1) * (GRID / 2);
    const top = py - 430;
    for (let c = 2; c <= cols + 1; c++) {                          // c = 1 的两侧(|col|≤1)是 3 列宽的安全缝，直接不发
      if (rnd() < 0.34) continue;                                  // 随机挑一部分列，避免整面墙
      for (const sgn of [-1, 1]) {                                 // 以缝为轴左右镜像：同一列号两侧同时落
        const x = axis + sgn * c * GRID;
        const n = 4 + Math.floor(rnd() * 2);                       // 每列 4~5 颗，纵向间距 52px
        for (let k = 0; k < n; k++) {
          put(e, world, x, top - k * 52, Math.PI / 2, phase >= 3 ? 250 : 215, 'drop', '#c56cf0', 7.5);
        }
      }
    }
  }
  if (phase >= 2) {
    S.side -= dt;
    if (S.side <= 0) {
      S.side = phase >= 3 ? 3.4 : 4.6;
      const gapRow = Math.floor(rnd() * 5) - 2;                    // 缝在第几行（相对玩家，±2 格）
      for (let r = -4; r <= 4; r++) {
        if (r === gapRow) continue;
        const y = py + r * GRID;
        // 左右镜像：两侧同时向中间压
        for (const sgn of [-1, 1]) {
          for (let k = 0; k < 3; k++) {
            put(e, world, px + sgn * (520 + k * 54), y, sgn < 0 ? 0 : Math.PI, 205, 'rice', '#ff9fe0', 6);
          }
        }
      }
    }
  }

  if (phase >= 3) {
    S.rot += dt * 0.9;
    S.spiral += dt;
    while (S.spiral >= 0.22) {
      S.spiral -= 0.22;
      for (let a = 0; a < 3; a++) put(e, world, e.x, e.y, S.rot + (a * TAU) / 3, 120, 'star', '#ffd6f5', 6, 800);
    }
  }
}

// ---------------------------------------------------------------
// 诺诺 · 天幕横雨 + 三向落柱
// ---------------------------------------------------------------
// 1 阶段：「天幕横雨」—— 从屏幕上沿斜着落下一片细雨（每批 6 颗，间距 70px），
//         批与批错开，雨中有稀疏带（玩家钻缝即可）。
// 2 阶段：叠加「三向落柱」—— 每 4.6s，以玩家为心：一根向下的柱 + 左右两根水平柱；
//         三根柱之间留出三块方形安全区，柱身 8 颗、间距 26px。
// 3 阶段：雨更密；落柱 3.4s 一次，且柱心跟随玩家预判一小段（更逼人走位）。
function nono(e, dt, ctx) {
  const { world, player, phase } = ctx;
  const S = e._sig ??= { rain: 0.8, pillar: 3.5, k: 0 };
  const rnd = rngOf(world);
  const px = player.x, py = player.y;

  S.rain -= dt;
  if (S.rain <= 0) {
    S.rain = phase >= 3 ? 0.42 : phase === 2 ? 0.55 : 0.7;
    S.k++;
    const slant = 1.15;                                             // 斜落角（弧度，≈66°）
    const n = phase >= 3 ? 9 : 6;
    const span = 780;
    const x0 = px - span / 2 + rnd() * 140 - 70;
    for (let i = 0; i < n; i++) {
      const x = x0 + (i / (n - 1)) * span + (S.k % 2 ? 35 : 0);
      if ((i + S.k) % 5 === 0) continue;                           // 稀疏带：每批固定缺几颗
      put(e, world, x - 320, py - 420, slant, 235, 'rice', '#7fe8ff', 5.4, 1100);
    }
  }

  if (phase >= 2) {
    S.pillar -= dt;
    if (S.pillar <= 0) {
      S.pillar = phase >= 3 ? 3.4 : 4.6;
      const lead = phase >= 3 ? 0.35 : 0;                          // 3 阶段：落点向玩家移动方向预判
      const cx = px + (player.vx ?? 0) * lead;
      const cy = py + (player.vy ?? 0) * lead;
      const SP = 26, N = 8, SPEED = 210, COL = '#4ab8ff';
      // 向下的柱：从上方 cy-430 起，竖直 N 颗排成一条线，一起往下压
      for (let k = 0; k < N; k++) put(e, world, cx, cy - 430 - k * SP, Math.PI / 2, SPEED, 'orb', COL, 6.5);
      // 向右的柱（从左侧来）与向左的柱（从右侧来），错开半个玩家身位
      for (let k = 0; k < N; k++) {
        put(e, world, cx - 520 - k * SP, cy - 96, 0, SPEED, 'orb', COL, 6.5);
        put(e, world, cx + 520 + k * SP, cy + 96, Math.PI, SPEED, 'orb', COL, 6.5);
      }
    }
  }
}

const TABLE = { pandora, nono };

/** enemy.js 的 _boss() 末尾调用。name 来自 def.signature；未知名字静默忽略。 */
export function runBossSignature(name, e, dt, player, world, phase, pm) {
  const fn = TABLE[name];
  if (!fn || !player || !world) return;
  fn(e, dt, { player, world, phase: phase || 1, pm });
}

export const SIGNATURE_NAMES = Object.keys(TABLE);
