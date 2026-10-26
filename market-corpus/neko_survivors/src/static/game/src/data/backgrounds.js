// ============================================================
// 关卡背景
//
// 第七轮用户要求："场景你看可以搞一些嘛？目前是纯色背景，可以做个符合我们
//   游戏风格的背景吗？…能体现猫娘计划的背景就更好了，就是有点科技风或者
//   城市风格的，但是不到赛博朋克的地步。也可以多做几张关卡轮流替换。"
//
// 第十一轮用户要求："设计4张新背景图，做到总共5张地图随机背景并且设置
//   1张图不会隔一回合被再次调用的设置。背景风格还是城市风格和近未来风格，
//   也可以略微赛博朋克一点，也可以来个花园效果地图，毕竟我们有洛丽塔服装角色。"
//
// 背景是**可选增强**：贴图没加载到，渲染器就回落到原来的纯色 + 网格，
// 游戏完全不受影响（和武器/道具图标一样的降级策略）。
//
// 每个背景是一张**无缝可平铺**的正方形 JPEG（assets/backgrounds/bg_<id>.jpg），
// 由 artkit/make-background.mjs 生成。
//
// 两条硬约束（写在这里是为了以后加图时别忘）：
//   1) 无缝：游戏用 createPattern(img,'repeat') 铺满整个竞技场
//   2) 够暗够平：场上同时有几十个敌人 + 弹幕 + 掉落物，
//      背景一抢眼玩家就找不到怪了。渲染器还会再叠一层 42% 的深色蒙版。
// ============================================================

/**
 * @typedef {Object} BackgroundDef
 * @property {string} id      贴图 id，对应 assets/backgrounds/bg_<id>.jpg
 * @property {string} name    中文名（设置界面/调试显示用）
 * @property {string} mood    一句话说明，给猫娘吐槽/关卡选择用
 */

/** @type {BackgroundDef[]} */
export const BACKGROUNDS = [
  {
    id: 'lab',
    name: '猫娘实验室',
    mood: '干净明亮的研究所地板，最贴合猫娘计划的气质',
  },
  // ---- 以下 4 张是第十一轮新增，凑成 5 张随机池 ----
  {
    id: 'city',
    name: '城市人行道',
    mood: '现代都市的铺装广场，浅灰石板接缝规整，白天感',
  },
  {
    id: 'station',
    name: '地铁站台',
    mood: '近未来地铁站台，深灰地砖配一条低对比度安全线，灯光偏暗',
  },
  {
    id: 'rooftop',
    name: '天台机房',
    mood: '设备层天台，深色花纹钢板与检修标记，工业冷感',
  },
  {
    id: 'garden',
    name: '修剪花园',
    mood: '整齐的草坪与浅色石板，零星花瓣 —— 给洛丽塔风的角色配的舞台',
  },
  // ---- 第一百八十五轮新增 3 张（用户："也画几个新地图包括地图上的障碍……目前只有 5 个地图有点少而且都偏机械风了"）----
  // 自然风景向，和前 5 张的科技/城市感拉开；三张都带障碍(见 terrain.js)，Boss 波自动避开。
  {
    id: 'forest',
    name: '月光森林',
    mood: '苔藓林地与落叶，浓密的树冠和长满青苔的巨石挡路',
  },
  {
    id: 'snow',
    name: '雪原',
    mood: '冰蓝色雪地，冰封的池塘、覆雪的松树与冰岩',
  },
  {
    id: 'desert',
    name: '沙漠遗迹',
    mood: '暖色沙丘上的古代遗迹：砂岩石块、倒塌石柱、开花的仙人掌',
  },
];

/** 默认背景（第 1 关用）。不存在就返回 null，渲染器自行回落。 */
export const DEFAULT_BACKGROUND = 'lab';

/** 全部背景 id（预加载用） */
export const BACKGROUND_IDS = BACKGROUNDS.map((b) => b.id);

/**
 * 32 位整数散列（splitmix32 的变体）。
 *
 * 为什么需要它：背景的"随机"必须**可复现**（见 backgroundForWave 的说明），
 * 所以不能用 Math.random。而 `(waveIndex * 大质数) % n` 这种朴素写法
 * 低位相关性很强 —— 实测前 5 波只抽到 3 种图（garden→rooftop→station→
 * rooftop→station），看起来像坏了。这个散列把输入位充分混合后再取模。
 */
function hash32(x) {
  let z = (x + 0x9e3779b9) | 0;
  z = Math.imul(z ^ (z >>> 16), 0x21f0aaad);
  z = Math.imul(z ^ (z >>> 15), 0x735a2d97);
  return (z ^ (z >>> 15)) >>> 0;
}

/**
 * 每一"轮"的背景顺序表（缓存）。
 *
 * 用「每 n 波为一轮、每轮是 0..n-1 的一个乱序排列」而不是
 * 「每波 hash % n」：
 *
 *   - **hash % n 做不到"每张图都用得上"**。它可能连着好几波抽到同一批图，
 *     玩一局下来有的图一次没出现。实测就是这个问题。
 *   - **排列保证 n 波里每张图正好用一次**。既均匀，又还是乱的
 *     （每一轮的乱序都不一样）。
 *
 * 接缝问题：第 k 轮的**第一个**不能等于第 k-1 轮的**最后一个**，
 * 否则两轮交界处会出现"相邻两波同图"。所以生成第 k 轮时，
 * 如果撞上了就把整条排列**旋转一格**（旋转不改变"每张用一次"的性质）。
 *
 * 这也让 backgroundForWave 完全由 waveIndex 决定 —— 不需要外部传历史状态。
 */
let _permN = -1;
let _perms = [];

function ensurePerms(n, uptoCycle) {
  if (_permN !== n) { _permN = n; _perms = []; }
  while (_perms.length <= uptoCycle) {
    const c = _perms.length;
    const arr = [];
    for (let k = 0; k < n; k++) arr.push(k);
    // 确定性 Fisher-Yates：种子只来自轮次号
    let h = hash32(c * 0x9e3779b9 + 0x85ebca6b);
    for (let i = n - 1; i > 0; i--) {
      h = hash32(h + i * 0x27d4eb2f);
      const j = h % (i + 1);
      const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    // 接缝：这一轮的第一个不能等于上一轮的最后一个
    if (c > 0 && n > 1 && arr[0] === _perms[c - 1][n - 1]) {
      arr.push(arr.shift());   // 旋转一格，仍然"每张用一次"
    }
    _perms.push(arr);
  }
}

/**
 * 按关卡序号取背景（第十一轮改）。
 *
 * 用户要求："总共5张地图随机背景并且设置1张图不会隔一回合被再次调用的设置"
 *
 * 拆成两条独立规则：
 *
 *   1) **随机**：不再是 `waveIndex % length` 的固定轮转。
 *      固定轮转玩两遍就背下来了（第 1 关永远是实验室、第 2 关永远是人行道），
 *      用户要的是"每局不一样"。现在每 5 波为一轮，每轮是一份独立的乱序排列。
 *
 *   2) **不隔一回合重复**：相邻两波不能用同一张。
 *      这一条比"纯随机"重要得多 —— 纯随机会有 1/5 的概率连着两波同图，
 *      玩家的感受是"这游戏背景根本没换"，而不是"运气不好"。
 *
 * 随机数来源：**不用 Math.random**，而是由 waveIndex 确定性推导。原因有两个：
 *   - 同一个 waveIndex 必须永远给同一个背景。背景是**在 _beginWave 里
 *     算一次**然后塞给渲染器的；如果这里用 Math.random，那么重试本波、
 *     读档、甚至调试时重新调用，都会换成另一张图，玩家会看到背景闪烁。
 *   - 测试才能断言（`backgroundForWave(7)` 必须可复现）。
 *
 * @param {number} waveIndex 0-based 波次序号
 * @param {string|null} [prevId] 上一波用的背景 id；传了就保证不会返回它
 * @returns {string|null}
 */
export function backgroundForWave(waveIndex, prevId = null) {
  const n = BACKGROUNDS.length;
  if (!n) return null;

  const i = Math.max(0, waveIndex | 0);
  const cycle = Math.floor(i / n);
  const pos = i % n;
  ensurePerms(n, cycle);
  const perm = _perms[cycle];

  let pick = perm[pos];
  // 显式满足"不返回 prevId"这条契约（也是两轮交界处的第二道保险）。
  //
  // 为什么往后挪一格是安全的：排列里元素互不相同，所以
  // perm[pos+1] 一定 ≠ perm[pos] = prevId。而且这个"挪一格"会顺着
  // 后续波次一致地传递下去、到最后一位绕回开头，
  // 所以**一轮里返回的值仍然正好覆盖所有图各一次**，不会漏图。
  if (prevId != null && n > 1 && BACKGROUNDS[pick].id === prevId) {
    pick = perm[(pos + 1) % n];
  }
  return BACKGROUNDS[pick].id;
}

/**
 * 走一遍第 0..count-1 波，返回每波会用的背景 id。
 *
 * 抽出来是因为"不连续重复"是**跨波的状态**（第 N 波要知道第 N-1 波用了什么），
 * 而 backgroundForWave 是纯函数。让调用方自己维护 prevId 很容易写错
 * （main.js 里就要多存一个字段），所以这里提供一个走完整序列的辅助函数，
 * 测试和文档都用它。
 *
 * @param {number} count 波数
 * @param {number} [startWave] 从第几波开始（0-based）
 * @returns {string[]}
 */
export function backgroundSequence(count, startWave = 0) {
  const out = [];
  let prev = null;
  for (let k = 0; k < count; k++) {
    const id = backgroundForWave(startWave + k, prev);
    out.push(id);
    prev = id;
  }
  return out;
}

/**
 * Boss 波选图（第一百八十五轮从 main.js 抽出来，main 与测试共用同一份实现）。
 *
 * 规则（用户第十二轮）：Boss 波不使用带复杂地形的图。`banned` 是禁用名单。
 *
 * 为什么要重写：原来在 main.js 里"往后试 BACKGROUNDS.length 张"，图少禁用也少时够用；
 * 第一百八十五轮图增至 8 张、其中 5 张带障碍后，窗口可能恰好一张可用图都没覆盖
 * （300 个波次里实测有 4 个会落在带障碍的图上）。现在：
 *   1) 先在后续 3 轮的排列里找（仍保持"同一波次永远同一张图"的可复现性）；
 *   2) 仍找不到就走确定性兜底 —— 在可用图里挑第一张不等于上一波的，保证绝不返回禁用图。
 *
 * @param {number} waveIndex 0-based
 * @param {string|null} prevId 上一波用的背景
 * @param {string[]} banned 禁用 id 列表
 */
export function backgroundForBossWave(waveIndex, prevId, banned) {
  const bg = backgroundForWave(waveIndex, prevId);
  if (!bg || !banned.includes(bg)) return bg;
  const n = BACKGROUNDS.length;
  for (let k = 1; k <= n * 3; k++) {
    const cand = backgroundForWave(waveIndex + k, prevId);
    if (cand && !banned.includes(cand)) return cand;
  }
  const ok = BACKGROUNDS.filter((b) => !banned.includes(b.id));
  if (!ok.length) return bg;                       // 全部被禁：无从避让，保持原抽取结果
  return (ok.find((b) => b.id !== prevId) ?? ok[0]).id;
}

/** 按 id 取定义（找不到返回 null） */
export function getBackground(id) {
  return BACKGROUNDS.find((b) => b.id === id) || null;
}
