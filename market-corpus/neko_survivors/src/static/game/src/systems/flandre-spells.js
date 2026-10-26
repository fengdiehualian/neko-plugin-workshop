// 芙兰新符卡脚本（第一百八十五轮 · 版本 B）。覆盖 flandre.js 里同名的旧符卡；没列在这里的继续用旧的。
//
// 用户："不用 100% 还原但是能看出来是'她'的感觉就行……你有更华丽的弹幕写法就用……
//       如果做到特别好看不留弹缝都行，毕竟有小小猫娘 15 秒一次的消弹，让玩家学会资源管理也行。"
//
// 设计原则（每张卡都遵守，tools/_spell-sim.mjs 量化验证）：
//   1. 判定远小于视觉：hitRadius = 视觉半径 × 0.6（视觉参数书 §0-8）。
//   2. 有"读得懂的结构"：螺旋丝线 / 网格笼 / 带缺口的环 / 星形波前 —— 玩家看一眼知道往哪躲。
//   3. 30 秒内会躲的 AI 被击中约 1~3 次；原地不动的人被击中 ≥10 次（逼人移动）。
//   4. 全部走 dt 累加，不用帧数取模；视觉随机只用 f.rnd()（游戏 RNG 流保持确定性）。
//   5. 发射器可视体写进 sp.st.vis（kind: 'glow'），渲染层画成发光球。
import { ARENA } from './flandre-consts.js';
import { audio } from './audio.js';
import { FLANDRE_HIT_INVULN } from '../entities/player.js';

// 199 轮：「够飞出场地」的射程。弹的真实寿命由 FlandreFight._cullOutside（飞出场地 140px 回收）决定，不再靠压短射程去省预算。
const LONG = 3200;

const TAU = Math.PI * 2;
const C = {
  magenta: '#E050F0', blue: '#4060FF', red: '#FF3040', white: '#FFF2F4', gold: '#FFE060',
  mint: '#30E080', cyan: '#60E8E8', violet: '#A060FF', pink: '#FF80C0', sky: '#5fd4ff',
};
const RAINBOW = ['#FF4040', '#FFA040', '#FFE040', '#60FF60', '#40E0FF', '#A060FF', '#FF40C0'];
const lerp = (a, b, k) => a + (b - a) * k;
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

/** 发一颗弹：判定半径缩到视觉的 60%，其余透传给 f.emit */
function shot(f, x, y, angle, speed, color, shape, r, extra) {
  f.emit({ x, y, angle, speed, color, shape, radius: r, hitRadius: r * 0.6, ...extra });
}
/** 规范化角度差到 [-π, π] */
function angDiff(a, b) { let d = (a - b) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU; return d; }
/** 发射器可视体（渲染层 kind:'glow'） */
const glow = (x, y, r, color, age, a = 1) => ({ x, y, r, color, age, a, kind: 'glow' });

export const NEW_SPELLS = {
  // ============================================================
  // 禁忌「Cranberry Trap」· 糖丝
  // 她瞬移，每次落点放出两颗发光的"糖球"，各自甩出 3 条彩色糖丝（大珠 + 小米粒交替的珠链），
  // 两球转向相反、丝线互相穿插成蛛网；糖球寿命尽时炸成一圈金色星星。
  // 结构读法：丝线之间有大片空地（螺旋臂间距大），站在两条丝线中间、随丝线外推慢慢挪。
  // ============================================================
  cranberry: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.tp = (st.tp ?? 99) + dt;
      if (st.tp >= 5.2) {
        st.tp = 0;
        f.teleportBoss();
        st.flip = -(st.flip ?? -1);
        st.ems = [-1, 1].map((side, i) => ({
          x: f.bx + side * 95, y: f.by + (f.rnd() - 0.5) * 70, dir: st.flip * (i ? -1 : 1),
          rot: f.rnd() * TAU, acc: 0, age: 0, ring: 0, bead: 0,
        }));
      }
      for (const em of st.ems ?? []) {
        em.age += dt;
        const life = 5.0;
        if (em.age > life) continue;
        em.rot += dt * lerp(1.05, 1.5, k) * em.dir;
        em.acc += dt;
        // 199 轮（用户：新符卡密度偏低，玩家有消弹和回血 buff）：珠距 28~36px → 21~27px（模拟器：高手 30 秒仅被打 0.2 次，有余量）
        const step = lerp(0.15, 0.11, k);
        while (em.acc >= step) {
          em.acc -= step; em.bead++;
          for (let arm = 0; arm < 3; arm++) {
            const a = em.rot + (arm * TAU) / 3;
            if (em.bead % 2 === 0) shot(f, em.x, em.y, a, 150, C.magenta, 'orb', 6.5);
            else shot(f, em.x, em.y, a, 150, C.blue, 'rice', 4.2);
          }
        }
        em.ring += dt;
        if (em.ring >= 2.5) {
          em.ring -= 2.5;
          const base = f.rnd() * TAU;
          for (let i = 0; i < 20; i++) shot(f, em.x, em.y, base + (i * TAU) / 20, 105, C.gold, 'star', 5.5);
        }
      }
      // 本体：慢速瞄准米弹扇，防止在丝线缝里站桩
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= lerp(1.9, 1.4, k)) {
        st.aim = 0;
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 7, 30, 200, C.pink, 5, 'rice', { hitRadius: 3 });
      }
      st.vis = (st.ems ?? []).filter((e) => e.age < 5.0).map((e) => glow(e.x, e.y, 13, C.magenta, e.age, Math.min(1, (5.0 - e.age) / 0.5)));
    },
  },

  // ============================================================
  // 禁忌「笼中鸟 Kagome Kagome」· 网格笼
  // 发射器从四边飞过：横向飞过的在头顶一路"洒"下一排向下漂的绿珠，纵向飞过的在侧边洒出一列横漂的绿珠。
  // 一排一列叠在一起，场上渐渐织成一座会移动的格子笼；每格的珠距留出可钻的缝。
  // 金色大玉偶尔从笼外飘进来当"鸟"，追着你走。
  // ============================================================
  kagome: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      const L = ARENA.cx - ARENA.hw, R = ARENA.cx + ARENA.hw, T = ARENA.cy - ARENA.hh, B = ARENA.cy + ARENA.hh;
      st.fl = st.fl ?? [];
      st.spawn = (st.spawn ?? 99) + dt;
      if (st.spawn >= lerp(2.0, 1.5, k)) {
        st.spawn = 0; st.n = (st.n ?? 0) + 1;
        const horizontal = st.n % 2 === 1;
        const rev = f.rnd() < 0.5;
        const spd = 330;
        if (horizontal) {
          st.fl.push({ h: true, x: rev ? R + 20 : L - 20, y: T + 24, vx: rev ? -spd : spd, vy: 0, acc: 0, dirShot: Math.PI / 2, phase: f.rnd() });
        } else {
          st.fl.push({ h: false, x: rev ? R - 24 : L + 24, y: T - 20, vx: 0, vy: spd, acc: 0, dirShot: rev ? Math.PI : 0, phase: f.rnd() });
        }
      }
      const gap = lerp(36, 32, k);                       // 珠距（px）；模拟器：46px→13发/秒笼子织不起来，30px/1.6s→AI 被打 8 次必死，取中间
      for (const e of st.fl) {
        e.x += e.vx * dt; e.y += e.vy * dt;
        e.acc += Math.hypot(e.vx, e.vy) * dt;
        while (e.acc >= gap) {
          e.acc -= gap;
          shot(f, e.x, e.y, e.dirShot, 92, (st.n + Math.floor(e.x / 97)) % 5 === 0 ? C.gold : C.mint, 'orb', 6);
        }
      }
      st.fl = st.fl.filter((e) => e.x > L - 80 && e.x < R + 80 && e.y > T - 80 && e.y < B + 80);
      // "鸟"：金色大玉从笼外慢速追人
      st.bird = (st.bird ?? 0) + dt;
      if (st.bird >= 3.6) {
        st.bird = 0;
        const a = f.aim(f.bx, f.by);
        f.emit({ x: f.bx, y: f.by, angle: a, speed: 120, color: C.gold, shape: 'orb', radius: 11, hitRadius: 6, range: LONG });
        f.fan(f.bx, f.by, a, 7, 40, 190, C.mint, 5, 'rice', { hitRadius: 3 });
      }
      st.vis = st.fl.map((e) => glow(e.x, e.y, 10, C.mint, f.t));
    },
  },

  // ============================================================
  // 禁忌「恋之迷宫 Maze of Love」· 缺口环
  // 每隔一小会儿从她身上扩出一圈完整的青色光环——只缺一个口；缺口一次比一次转得远，
  // 连成一条螺旋的"迷宫通道"，通道会慢慢变窄。圈内另有一条蓝色米弹螺旋缓缓旋出。
  // 结构读法：永远朝缺口走；缺口总在你方向 ±35° 以内，别贪心去追上一个缺口。
  // ============================================================
  maze: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.ring = (st.ring ?? 0.6) + dt;
      st.gapAng = st.gapAng ?? f.aim(f.bx, f.by);
      const interval = lerp(1.3, 0.92, k);
      if (st.ring >= interval) {
        st.ring = 0; st.n = (st.n ?? 0) + 1;
        const N = 84, gapN = Math.round(lerp(9, 6, k));   // 模拟器：54 发/圈时 530px 处弹距 ~62px，AI 随便钻，加密到 84 让缺口真正有意义
        // 缺口：以玩家方向为基准偏 ±35°，且每圈绕向翻转，形成蛇形通道
        st.gapAng = f.aim(f.bx, f.by) + (f.rnd() - 0.5) * 1.9;   // ±54°：缺口常常不在你正前方，站桩会被打
        const fast = st.n % 4 === 0;                       // 每 4 圈一圈"快环"：先快后缓，追上前一圈压缩通道
        for (let i = 0; i < N; i++) {
          const th = (i * TAU) / N + st.gapAng + Math.PI / N;
          if (Math.abs(angDiff(th, st.gapAng)) < (gapN / 2) * (TAU / N)) continue;
          shot(f, f.bx, f.by, th, fast ? 210 : 128, fast ? C.sky : C.cyan, 'orb', 5.5, fast ? { decel: 90, floorSpeed: 110 } : undefined);
        }
      }
      // 内圈蓝米弹螺旋（3 臂，不密，作为"路标"并逼人别贴着她站）
      st.rot = (st.rot ?? 0) + dt * 1.7;
      st.spi = (st.spi ?? 0) + dt;
      while (st.spi >= 0.16) {
        st.spi -= 0.16;
        for (let arm = 0; arm < 3; arm++) shot(f, f.bx, f.by, st.rot + (arm * TAU) / 3, 96, C.blue, 'rice', 4.4);
      }
      st.vis = [glow(f.bx, f.by, 15, C.cyan, f.t)];
    },
  },

  // ============================================================
  // 禁弹「Starbow Break」· 星虹
  // 一颗颗五角星形的"波前"从她和两侧的星点扩散出来：星尖跑得快、凹处跑得慢，所以形状一直保持是星；
  // 颜色按方向分成彩虹 7 段。每个波前都在你的方向上留一道"星门"（缺口）——凹处正对着你时最宽。
  // ============================================================
  starbow: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.beat = (st.beat ?? 0.5) + dt;
      const interval = lerp(0.8, 0.55, k);   // 199 轮：0.95~0.65s → 0.8~0.55s（更密；高手原本 30 秒 0 次被打）
      if (st.beat >= interval) {
        st.beat = 0; const n = (st.n = (st.n ?? 0) + 1);
        const o = n % 3 === 0
          ? { x: f.bx, y: f.by }
          : { x: ARENA.cx + (n % 2 ? -1 : 1) * 330, y: ARENA.cy - 140 };
        const rot = f.rnd() * TAU, points = 5, inner = 0.46;
        // 星形多边形顶点：偶数下标为尖(1)，奇数为凹(inner)
        const V = [];
        for (let i = 0; i < points * 2; i++) { const a = rot + (i * Math.PI) / points; const rr = i % 2 ? inner : 1; V.push([Math.cos(a) * rr, Math.sin(a) * rr]); }
        const towards = Math.atan2(f.P.y - o.y, f.P.x - o.x);
        const NB = 66, gate = 0.3;   // 199 轮：60→66 发/星（星门宽度不变）；预算回到 800 后留出峰值余量
        for (let i = 0; i < NB; i++) {
          const th = (i * TAU) / NB + rot + Math.PI / NB;
          if (Math.abs(angDiff(th, towards)) < gate) continue;   // 星门
          // 射线与星形边的交点半径 → 速度比
          const dx = Math.cos(th), dy = Math.sin(th);
          let rr = 1;
          for (let j = 0; j < V.length; j++) {
            const P = V[j], Q = V[(j + 1) % V.length], Ex = Q[0] - P[0], Ey = Q[1] - P[1];
            const den = dx * Ey - dy * Ex;
            if (Math.abs(den) < 1e-9) continue;
            const r = (P[0] * Ey - P[1] * Ex) / den, u = (P[0] * dy - P[1] * dx) / den;
            if (r > 0 && u >= 0 && u <= 1) { rr = r; break; }
          }
          const sector = Math.floor((((th - rot) % TAU) + TAU) % TAU / TAU * RAINBOW.length);
          shot(f, o.x, o.y, th, 150 * rr, RAINBOW[sector], 'star', 5.6, { range: LONG });
        }
      }
      // 本体补射：慢速瞄准大玉，鼓励移动而非站桩
      st.big = (st.big ?? 0) + dt;
      if (st.big >= 3.0) {   // 199 轮：3.4s → 3.0s
        st.big = 0;
        f.emit({ x: f.bx, y: f.by, angle: f.aim(f.bx, f.by), speed: 105, color: C.violet, shape: 'orb', radius: 10, hitRadius: 5.5, range: LONG });
      }
      st.vis = [glow(f.bx, f.by, 14, C.gold, f.t)];
    },
  },

  // ============================================================
  // ===== 版本 C ===============================================
  // ============================================================

  // 禁弹「Catadioptric」· 镜面
  // 她左右各立一面"镜"(发射点)，两面镜交替向玩家打出一扇会减速的蓝/红碎片（先快后缓，像光被折射后慢下来）；
  // 保留用户指定的机制：每 3 秒一轮 5 颗蓝紫大玉（上→右→左），三面反弹，沿途掉落尾随的大球与小球。
  catadioptric: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.acc = (st.acc ?? 0.5) + dt;
      if (st.acc >= lerp(0.9, 0.66, k)) {   // 199 轮：1.0~0.75s → 0.9~0.66s（更密；N 固定 12；0.86~0.62 时高手 5.0 次压线，留余量）
        st.acc = 0; st.n = (st.n ?? 0) + 1;
        for (const side of [-1, 1]) {
          const ex = f.bx + side * 140, ey = f.by + 10;
          const base = f.aim(ex, ey) + side * 0.3 * (st.n % 2 ? 1 : -1);
          const N = 12;   // 必须保持 12：站桩玩家正对扇面中央偏 ±0.3rad 处，N=12 时恰好正对一发弹(i=3, off=-0.307)，N=13 时落在两发之间的缝里 → 站桩只被打 3 次，失去逼人移动的作用（round188 抓到）。更密靠加快节奏，不靠加弹数
          for (let i = 0; i < N; i++) {
            const off = (i / (N - 1) - 0.5) * 1.35;
            shot(f, ex, ey, base + off, 270 - Math.abs(off) * 70, side < 0 ? C.blue : C.red, 'shard', 5.6, { decel: 150, floorSpeed: 78, range: LONG });
          }
        }
      }
      // —— 用户指定：蓝紫大玉三面反弹 + 尾随弹（原样保留）——
      const DIRS = [-Math.PI / 2, 0, Math.PI];
      st.vAcc = (st.vAcc ?? 1.0) + dt;
      if (st.vAcc >= 3) {
        st.vAcc -= 3;
        const dir = DIRS[st.vIdx = ((st.vIdx ?? -1) + 1) % 3];
        const wall = st.wall ?? (st.wall = { minX: ARENA.cx - ARENA.hw, maxX: ARENA.cx + ARENA.hw, minY: ARENA.cy - ARENA.hh });
        for (let i = 0; i < 5; i++) {
          const a = dir + ((i / 4) - 0.5) * (Math.PI / 2);
          f.emit({ x: f.bx, y: f.by, angle: a, speed: 115, radius: 30, hitRadius: 13, shape: 'jade', jadeColor: '#6f5ff0', wall, range: 1600, tag: 'catadioptric' });
        }
      }
      st.tAcc = (st.tAcc ?? 0) + dt;
      if (st.tAcc >= 0.3) {
        st.tAcc -= 0.3;
        const jades = f.main.world.enemyBullets.items.filter((b) => !b.dead && b.tag === 'catadioptric' && b.shape === 'jade');
        for (const j of jades) {
          f.emit({ x: j.x, y: j.y, angle: f.rnd() * TAU, speed: 22, radius: 12, color: '#9f8cf5', range: 140 });
          f.emit({ x: j.x, y: j.y, angle: f.rnd() * TAU, speed: 55, radius: 5, color: '#c9befa', range: 190 });
        }
      }
      st.vis = [glow(f.bx - 140, f.by + 10, 12, C.blue, f.t), glow(f.bx + 140, f.by + 10, 12, C.red, f.t)];
    },
  },

  // 禁弹「过后一时辰的时钟」· 时针分针
  // 两根看不见的"指针"从她身上伸出：分针(金)转得快、时针(冰蓝)转得慢，沿针身每隔一段就缓缓漂出一颗弹，
  // 针扫过的地方留下一片渐渐散开的螺旋弹带——像时钟被拧着走。每 6 秒"报时"：12 颗金色大珠等分炸开。
  clock: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.hands = st.hands ?? [
        { ang: f.rnd() * TAU, w: 1.25, col: C.gold, rs: [100, 190, 280, 370, 460], acc: 0, shape: 'orb' },
        { ang: f.rnd() * TAU, w: -0.5, col: '#A8C8FF', rs: [130, 250, 370, 490], acc: 0, shape: 'rice' },
      ];
      for (const h of st.hands) {
        h.ang += h.w * lerp(1, 1.3, k) * dt;
        h.acc += dt;
        while (h.acc >= 0.15) {   // 199 轮：珠距 0.2s → 0.15s（更密）
          h.acc -= 0.15;
          for (const r of h.rs) {
            const x = f.bx + Math.cos(h.ang) * r, y = f.by + Math.sin(h.ang) * r;
            if (Math.abs(x - ARENA.cx) > ARENA.hw || y < ARENA.cy - ARENA.hh || y > ARENA.cy + ARENA.hh) continue;
            shot(f, x, y, h.ang + (h.w > 0 ? Math.PI / 2 : -Math.PI / 2), 46, h.col, h.shape, h.shape === 'orb' ? 6 : 4.4, { range: LONG });
          }
        }
      }
      st.chime = (st.chime ?? 3.5) + dt;
      if (st.chime >= 5) {   // 199 轮：6s → 5s 报时
        st.chime = 0;
        const base = f.rnd() * TAU;
        for (let i = 0; i < 12; i++) shot(f, f.bx, f.by, base + (i * TAU) / 12, 140, C.gold, 'orb', 9);
        for (let i = 0; i < 12; i++) shot(f, f.bx, f.by, base + ((i + 0.5) * TAU) / 12, 95, '#A8C8FF', 'ring', 6);
      }
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= 1.7) { st.aim = 0; f.fan(f.bx, f.by, f.aim(f.bx, f.by), 5, 26, 180, C.white, 5, 'rice', { hitRadius: 3 }); }
      st.vis = [glow(f.bx, f.by, 15, C.gold, f.t), ...st.hands.map((h) => glow(f.bx + Math.cos(h.ang) * 70, f.by + Math.sin(h.ang) * 70, 8, h.col, f.t, 0.8))];
    },
  },

  // 咒弹「Fragile Wing」· 羽
  // 两片翅膀(左右镜像)像扇子一样来回扫，每次扫出一把彩虹花瓣；花瓣先快后慢最后悬停成一片缓缓下沉的"羽雾"。
  // 8.5 秒后第二波：7 片羽翼从她身后同时炸开，各自带一圈螺旋。
  fragile: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.acc = (st.acc ?? 0) + dt;
      st.slot = st.slot ?? 0;
      const step = lerp(0.15, 0.1, k);
      while (st.acc >= step) {
        st.acc -= step; st.slot = (st.slot + 1) % 7;
        const sweep = Math.sin(sp.t * 1.05) * 1.05;
        const aim0 = f.aim(f.bx, f.by);
        for (const side of [-1, 1]) {
          const a = aim0 + sweep * side + side * 0.35;
          shot(f, f.bx + side * 28, f.by, a, 235, RAINBOW[(st.slot + (side > 0 ? 3 : 0)) % 7], 'petal', 5.4, { decel: 105, floorSpeed: 44, range: LONG });
        }
      }
      // 瞄准花瓣扇：每 1.1s 一把 7 瓣、先快后缓，逼人别站在原地（模拟器：纯扫射站桩只被打 2 次）
      st.seed = (st.seed ?? 0.5) + dt;
      if (st.seed >= lerp(1.3, 0.95, k)) {
        st.seed = 0;
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 7, 38, 215, RAINBOW[(st.slot + 2) % 7], 5.6, 'petal', { hitRadius: 3.4, decel: 90, floorSpeed: 60, range: LONG });
      }
      if (!st.burst && sp.t >= 8.5) {
        st.burst = true; st.burstT = 0;
      }
      if (st.burst) {
        st.burstT += dt;
        st.bacc = (st.bacc ?? 0) + dt;
        while (st.bacc >= 0.2 && st.burstT < 6) {
          st.bacc -= 0.2; st.bslot = ((st.bslot ?? 0) + 1) % 7;
          for (let i = 0; i < 7; i++) {
            const a = -Math.PI / 2 + (i / 7) * TAU + st.burstT * 0.9 * (i % 2 ? 1 : -1);
            shot(f, f.bx, f.by, a, 180, RAINBOW[(st.bslot + i) % 7], 'petal', 5, { decel: 70, floorSpeed: 50, range: LONG });
          }
        }
        if (st.burstT >= 6) st.burst = false, st.burstDone = true;
      }
      if (sp.t >= 8.5 && !st.burstDone && !st.burst) st.burst = true;
      st.vis = [glow(f.bx - 28, f.by, 10, RAINBOW[st.slot], f.t), glow(f.bx + 28, f.by, 10, RAINBOW[(st.slot + 3) % 7], f.t)];
    },
  },

  // 咒弹「伏行之血痕」· 血雨
  // 5 颗发光的血珠从场顶依次落下，每颗边下沉边甩出双臂旋转的"水滴"血线，水滴每隔 2.6 秒再炸开一圈白色米弹；
  // 保留用户指定的粉色大玉自机狙。
  bloodstain: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      st.ems = st.ems ?? []; st.n = st.n ?? 0;
      while (st.n < 5 && sp.t >= 0.7 + st.n * 1.0) {
        st.ems.push({ x: ARENA.cx + (f.rnd() * 2 - 1) * (ARENA.hw - 90), y: ARENA.cy - ARENA.hh + 10, a: f.rnd() * TAU, spin: (f.rnd() > 0.5 ? 1 : -1) * (0.9 + f.rnd() * 0.6), fire: 0, ring: 1 + f.rnd() * 1.2 });
        st.n++;
      }
      for (const em of st.ems) {
        if (em.y < ARENA.cy + 60) em.y += 30 * dt;
        em.a += em.spin * dt; em.fire += dt; em.ring += dt;
        while (em.fire >= 0.16) {
          em.fire -= 0.16;
          for (let arm = 0; arm < 2; arm++) shot(f, em.x, em.y, em.a + arm * Math.PI, 105, '#FF2030', 'drop', 5.4);
        }
        if (em.ring >= 2.6) {
          em.ring = 0;
          const base = f.rnd() * TAU;
          for (let i = 0; i < 16; i++) shot(f, em.x, em.y, base + (i * TAU) / 16, 85, C.white, 'rice', 4.4);
        }
      }
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= lerp(1.8, 1.4, k)) {
        st.aim = 0;
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 3, 24, 185, C.pink, 30, 'jade', { hitRadius: 13, jadeColor: C.pink });
      }
      st.vis = st.ems.map((e) => glow(e.x, e.y, 15, '#FF2030', f.t));
    },
  },

  // 「弹幕的心脏」· 玫瑰
  // 每个心跳(1.1 秒)，她打出一整圈弹，但每颗弹的速度按玫瑰线 |cos(3θ)| 调制——快的跑到外面、慢的留在里面，
  // 圈在扩散中自己长成一朵三瓣玫瑰，并一直慢慢自转；颜色从花心的紫渐变到花瓣尖的粉与金。左右两个心室另打一对反向旋转的晶体扇。
  heart: {
    tick(sp, dt, f) {
      const st = sp.st, k = clamp01(sp.t / sp.dur);
      if (!st.init) { st.init = true; if (f.boss) { f.boss.x = ARENA.cx; f.boss.y = ARENA.cy - 270; } }
      st.beat = (st.beat ?? 0.6) + dt;
      if (st.beat >= lerp(1.2, 0.95, k)) {
        st.beat = 0; st.n = (st.n ?? 0) + 1;
        const N = 66, petals = st.n % 2 ? 3 : 4, rot = st.n * 0.37 + f.rnd() * 0.2;
        for (let i = 0; i < N; i++) {
          const th = (i / N) * TAU;
          const c = Math.abs(Math.cos(petals * th * 0.5));
          const spd = 70 + 150 * c;
          const col = c > 0.82 ? C.gold : c > 0.5 ? C.pink : '#c56cf0';
          shot(f, f.bx, f.by, th + rot, spd, col, c > 0.82 ? 'star' : 'shard', c > 0.82 ? 5.6 : 5, { range: LONG });
        }
      }
      st.rot = (st.rot ?? 0) + dt;
      st.fire = (st.fire ?? 0) + dt;
      while (st.fire >= 0.22) {
        st.fire -= 0.22;
        for (const side of [-1, 1]) {
          const ex = f.bx + side * 70, ey = f.by + 36;
          f.fan(ex, ey, Math.PI / 2 + side * Math.sin(st.rot * 0.8) * 0.9, 3, 18, 150, side < 0 ? C.pink : C.gold, 5, 'shard', { hitRadius: 3 });
        }
      }
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= 2.2) { st.aim = 0; f.fan(f.bx, f.by, f.aim(f.bx, f.by), 3, 24, 95, C.red, 5, 'shard', { hitRadius: 3 }); }
      st.vis = [glow(f.bx, f.by, 16, C.pink, f.t), glow(f.bx - 70, f.by + 36, 9, C.pink, f.t), glow(f.bx + 70, f.by + 36, 9, C.gold, f.t)];
    },
  },
};

// =================================================================
// 第二批重做（第一百九十九轮）：莱瓦汀 · 炎剑 / 四重存在
// 用户："没改的芙兰卡你也可以都改改，除了开头必放的教学卡和 495 年的波纹……其他卡都可以改改。"
// 保持不变的东西（都是用户指定过的规格）：
//   · 莱瓦汀：旋转激光（16% 最大生命真伤 + 1s 无敌帧）、只在上半屏移动、激光扫后留下"椭圆蛋"弹幕、渲染读 st.lasers；
//   · 四重存在：分身是纯视觉发射器、本体隐身 + 大 hitbox + 接触伤害归零（_removeClones 靠 _bossRadiusSave 复原）、全弹 = 椭圆蛋。
// 新的东西：
//   · 炎剑：出剑前 0.7s 预警线 + 扫掠范围（可读）；后半程变"剪刀"（两把剑反向对扫）；出剑瞬间一圈火星爆开；
//   · 四重存在：4 个分身各有不同的"花色"打法（瞄准扇 / 环 / 螺旋 / 骤雨），每 7 秒花色轮换，轮换时有 0.9s 喘息 + 一圈信号环。
// =================================================================
const IDLE_FRAMES_4K = ['flandre', 'flandre_idle2', 'flandre_idle3', 'flandre_idle4'];
const FLAME = ['#ff4060', '#ff7a3c', '#ffd166'];

const LAEV = {
  tick(sp, dt, f) {
    const st = sp.st, k = clamp01(sp.t / sp.dur);
    st.lasers ??= [];
    if (!st.seg) {
      st.n = (st.n ?? 0) + 1;
      st.seg = {
        phase: 'move', t: 0, dur: 1.1,
        fx: f.bx, fy: f.by,
        tx: f.ax + (f.rnd() * 2 - 1) * (ARENA.hw - 160),
        ty: f.ay - ARENA.hh + 140 + f.rnd() * (ARENA.hh - 300),
      };
    }
    const sg = st.seg;
    sg.t += dt;
    if (sg.phase === 'move') {
      const e = 1 - (1 - clamp01(sg.t / sg.dur)) ** 2;
      if (f.boss) { f.boss.x = sg.fx + (sg.tx - sg.fx) * e; f.boss.y = sg.fy + (sg.ty - sg.fy) * e; }
      if (sg.t >= sg.dur) {
        sg.phase = 'warn'; sg.t = 0;
        const dir = f.rnd() < 0.5 ? 1 : -1;
        // 几何（模拟器逐项量过）：扫掠扇面约 100°，离 boss 500px 的玩家要横向逃出扇面需 >600px，1.7s 内跑不到 ——
        // 若扇面包住玩家所在方向，这张卡就是"必中"。所以：剑从玩家身侧起手、朝远离玩家的方向扫开，站着不动是安全的，
        // 只有走进扫掠区才会被烧；二阶段的"剪刀"是第二把剑从另一侧扫向玩家、在离玩家 0.45rad 处停住，
        // 夹出一条约 52° 的可站走廊（两把剑都有预警线 + 扫程扇面）。
        const aim = f.aim(f.bx, f.by) + (f.rnd() * 2 - 1) * 0.3;
        const w = lerp(0.8, 1.0, k), span = w * 1.9, gapA = 0.2 + f.rnd() * 0.35, gapB = 0.45;
        const scissors = k >= 0.5;
        const mk = (ang, vel) => ({ x: f.bx, y: f.by, angle: ang, angVel: vel, len: 1010, width: 22, age: 0, life: 99, fade: 1, warn: 0.85, sweep: vel * 1.9 });
        st.lasers.push(mk(aim + dir * gapA, dir * w));
        if (scissors) st.lasers.push(mk(aim - dir * (gapB + span), dir * w));
      }
    } else if (sg.phase === 'warn') {
      if (sg.t >= 0.85) {
        sg.phase = 'sweep'; sg.t = 0;
        for (const L of st.lasers) { L.warn = 0; L.life = 1.9 + 0.5; }
        audio.play('flandreLaser');
        const n = 18, base = f.rnd() * TAU;
        for (let i = 0; i < n; i++) shot(f, f.bx, f.by, base + (i * TAU) / n, 150, FLAME[i % 3], 'star', 5.4, { decel: 70, floorSpeed: 78, range: LONG });
      }
    } else if (sg.phase === 'sweep') {
      if (sg.t >= 2.4) { sg.phase = 'rest'; sg.t = 0; }
    } else if (sg.phase === 'rest') {
      if (sg.t >= 1.0) st.seg = null;
    }
    const p = f.main.player;
    for (const L of st.lasers) {
      if (L.warn > 0) { L.warn -= dt; continue; }
      L.age += dt; L.life -= dt; L.angle += L.angVel * dt;
      if (L.life < 0.5) L.fade = Math.max(0, L.life / 0.5);
      if (!p.dead && p.invuln <= 0 && L.fade > 0.2) {
        const dx = p.x - L.x, dy = p.y - L.y;
        const proj = dx * Math.cos(L.angle) + dy * Math.sin(L.angle);
        if (proj > 0 && proj < L.len * Math.min(1, L.age / 0.35)) {
          const dist = Math.abs(-dx * Math.sin(L.angle) + dy * Math.cos(L.angle));
          if (dist < L.width + (p.radius ?? 10)) {
            const dmg = Math.max(1, Math.round(p.stats.get('maxHp') * 0.16));
            p.takeDamage(dmg, p.x, p.y, { invuln: FLANDRE_HIT_INVULN, noTaken: true });
          }
        }
      }
    }
    st.lasers = st.lasers.filter((L) => L.life > 0 || L.warn > 0);
    st.spray = (st.spray ?? 0) + dt;
    if (st.spray >= 0.16) {
      st.spray -= 0.16;
      for (const L of st.lasers) {
        if (L.warn > 0 || L.age < 0.1) continue;
        for (let j = 1; j <= 6; j++) {
          const d = j * 150;
          if (d > L.len * 0.95) break;
          const side = f.rnd() < 0.5 ? 1 : -1;
          f.emit({
            x: L.x + Math.cos(L.angle) * d, y: L.y + Math.sin(L.angle) * d,
            angle: L.angle + side * (Math.PI / 2) + (f.rnd() * 2 - 1) * 0.3,
            speed: 70, color: FLAME[(j + (st.n ?? 0)) % 3], radius: 4.8, hitRadius: 3, range: LONG, shape: 'longball',
          });
        }
      }
    }
    st.fan = (st.fan ?? 0) + dt;
    if (st.fan >= lerp(1.6, 1.25, k)) {
      st.fan = 0;
      f.fan(f.bx, f.by, f.aim(f.bx, f.by), 7, 26, 175, C.red, 5.5);
    }
    st.ring2 = (st.ring2 ?? 0) + dt;
    if (st.ring2 >= 2.6) {
      st.ring2 = 0;
      f.ring(f.bx, f.by, 16, f.rnd() * TAU, 125, C.gold, 5);
    }
    st.vis = [glow(f.bx, f.by, 15, '#ff7a3c', f.t)];
  },
};

// 四重存在的颜色是用户第九十一轮指定的「原红/白/深红」（flandre.js 的 COLORS.red/white/dark），不能用本文件的 C 色板（C.red 是另一个红）
const K4 = { red: '#ff4060', white: '#fff2f4', dark: '#c81e3c' };
const FOUR_KIND = {
  tick(sp, dt, f) {
    const st = sp.st, k = clamp01(sp.t / sp.dur);
    if (!st.clones) {
      st.clones = [];
      st.rot = 0; st.shift = 0; st.shiftT = 0; st.hush = 0; st.acc = [0, 0, 0, 0]; st.sp = 0;
      if (f.boss) {
        f.boss.flandreHidden = true;
        f._bossRadiusSave = f.boss.radius;
        f.boss.radius = 130 + f.boss.radius;
        f._bossDamageSave = f.boss.damage;
        f.boss.damage = 0;
      }
      for (let i = 0; i < 4; i++) st.clones.push({ x: f.bx, y: f.by, spriteId: IDLE_FRAMES_4K[0], radius: f._bossRadiusSave ?? 24 });
    }
    st.rot += dt * lerp(0.5, 0.8, k);
    const frame = IDLE_FRAMES_4K[Math.floor(f.t / 0.35) % IDLE_FRAMES_4K.length];
    const pos = [];
    for (let i = 0; i < 4; i++) {
      const a = st.rot + (i * TAU) / 4;
      const rr = 130 + Math.sin(f.t * 1.3 + i * 1.7) * 14;
      const c = st.clones[i];
      c.x = f.bx + Math.cos(a) * rr; c.y = f.by + Math.sin(a) * rr; c.spriteId = frame;
      pos.push({ x: c.x, y: c.y, a });
    }
    st.shiftT += dt;
    if (st.shiftT >= 7) {
      st.shiftT = 0; st.shift++; st.hush = 0.9;
      for (let i = 0; i < 4; i++) f.ring(pos[i].x, pos[i].y, 10, pos[i].a, 140, i % 2 ? K4.red : K4.white, 5.2, 0, 'longball');
    }
    st.hush = Math.max(0, st.hush - dt);
    if (st.hush > 0) return;
    for (let r = 0; r < 4; r++) st.acc[r] += dt;
    const who = (r) => pos[(r + 4 - (st.shift % 4)) % 4];
    if (st.acc[0] >= lerp(0.65, 0.5, k)) {
      st.acc[0] = 0;
      const o = who(0);
      f.fan(o.x, o.y, f.aim(o.x, o.y), 6, 30, 215, K4.red, 5.5, 'longball');
    }
    if (st.acc[1] >= 2.2) {
      st.acc[1] = 0;
      const o = who(1);
      f.ring(o.x, o.y, 18, o.a, 150, K4.white, 5, 0, 'longball');
    }
    if (st.acc[2] >= 0.085) {
      st.acc[2] = 0;
      const o = who(2);
      st.sp += 0.43;
      f.emit({ x: o.x, y: o.y, angle: st.sp, speed: 130, color: K4.dark, radius: 5, hitRadius: 3, shape: 'longball', range: LONG });
      f.emit({ x: o.x, y: o.y, angle: st.sp + Math.PI, speed: 130, color: K4.dark, radius: 5, hitRadius: 3, shape: 'longball', range: LONG });
    }
    if (st.acc[3] >= 0.11) {
      st.acc[3] = 0;
      const o = who(3);
      f.emit({ x: o.x, y: o.y, angle: f.aim(o.x, o.y) + (f.rnd() * 2 - 1) * 0.55, speed: 165 + f.rnd() * 40, color: K4.white, radius: 4.6, hitRadius: 2.8, shape: 'longball', range: LONG });
    }
    st.vis = [];
  },
};

Object.assign(NEW_SPELLS, { laevateinn: LAEV, fourkind: FOUR_KIND });