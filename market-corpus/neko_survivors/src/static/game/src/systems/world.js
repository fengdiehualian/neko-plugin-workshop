// ============================================================
// World：战斗核心
// 持有所有实体列表、空间网格、刷怪、伤害结算、掉落。
// 是各系统之间的唯一协调者。
// ============================================================

import { EntityList, SpatialGrid } from '../entities/entity.js';
// 第一百六十三轮：移动端同屏普通怪上限（桌面 MOBILE=false 不受限）
import { MOBILE, MOBILE_TUNE } from '../core/mobile.js';
import { Enemy } from '../entities/enemy.js';
import { Bullet, EnemyBullet } from '../entities/bullet.js';
import { Pickup, PICKUP_TYPE } from '../entities/pickup.js';
import { TurretManager } from '../entities/turret.js';
import { FXSystem } from './fx.js';
import { getEnemy, getNormalEnemies, getEliteEnemies, getTreasureEnemies } from '../data/enemies.js';
import { Chest, rollChestReward, chestColors } from '../entities/chest.js';
import { ITEMS } from '../data/items.js';
import { getWaveConfig, getWaveDuration, waveScaling, spawnRateMul, bossAddProgress, WAVE_COUNT,
  SPECIAL_WAVE_FROM, SPECIAL_WAVE_COOLDOWN, SPECIAL_WAVE_CHANCE, SPECIAL_WAVE_POOLS,
  ENEMY_ALIVE_CAP } from '../data/waves.js';
import { getDifficulty, DEFAULT_DIFFICULTY, WAVE11_HP_BUFF, WAVE11_HP_BUFF_FROM, WAVE11_HP_BUFF_ALL } from '../data/difficulty.js';
import { angleTo, clamp, dist, TAU } from '../core/math.js';
import { effectiveLuck } from '../core/stats.js';
import { expandTerrain, blockedAt, resolveTerrain, findFreeSpot } from '../data/terrain.js';

export const WORLD_SIZE = 2400;

/**
 * 地图上同时存在的医疗包掉落上限（第十九轮）。
 *
 * 用户原话："把地图上的可存在医疗包上限改到30。如果玩家发育比较好不掉血，
 * 地图上最后会全是医疗包。满地图都是太花眼了。改到30个上限，这样缺血的
 * 时候有的吃也不至于太花哨。"
 *
 * 语义：场上（未拾取、未过期）的医疗包达到 30 个后，新的医疗包掉落
 * 不再生成；玩家吃掉一个立刻腾出一个名额。经验/金币掉落不受影响。
 */
export const HEAL_PICKUP_MAX = 30;

/**
 * 宝箱/宝箱怪能开出的道具池。
 *
 * 排除掉那些"必须有武器才成立"的道具（纯炮台加成在没炮台时是废奖励），
 * 也排除已经有叠加上限的诅咒类，避免玩家被迫吃负面效果。
 */
function getRewardItemPool() {
  return ITEMS.filter((it) => {
    // 第八十五轮：芙兰专属（莱万汀）不进宝箱/宝箱怪奖励池 ——
    // 用户："应该只有打败芙兰才能获得。"
    if (it.flandreOnly) return false;
    // 第一百三十四轮：Boss 专属掉落红品（小鱼干等 8 件）同理排除 ——
    // 唯一入口是第 10/20 波 Boss 击杀掉落。
    if (it.bossDropOnly) return false;
    // 纯炮台加成的道具，只在玩家已有炮台时才有意义 → 不放进通用奖励池
    const keys = Object.keys(it.pct || {}).concat(Object.keys(it.mods || {}));
    const turretOnly = keys.length > 0 && keys.every((k) => k.startsWith('turret'));
    if (turretOnly) return false;
    // 有诅咒的道具不白送（白送负面效果体验很差）
    if (it.curse && Object.keys(it.curse).length > 0) return false;
    return true;
  });
}

export class World {
  constructor(rng, camera) {
    this.rng = rng;
    this.camera = camera;
    this.halfSize = WORLD_SIZE / 2;

    this.enemies = new EntityList();
    this.bullets = new EntityList();
    this.enemyBullets = new EntityList();
    this.pickups = new EntityList();
    this.fx = new FXSystem();
    this.grid = new SpatialGrid(72);
    // 炮台（挂机流派）：不放进 EntityList，因为要按"最早放置"顺序替换
    this.turrets = new TurretManager();
    // 跟随宠物（小小猫娘）：由 main.js 创建并挂上来，跨波保留
    this.pet = null;

    // 不可通行地形（第十二轮）。由 main.js 在换背景时调用 setTerrain 填进来。
    // 空对象 = 没有障碍物（大部分地图都是这样）。
    this.terrain = { rects: [], circles: [] };
    this.terrainBackgroundId = null;

    // 刷怪
    this.spawnQueue = [];
    this.spawnTimer = 0;
    // 第一百七十二轮：刷怪预告列表（先出闪烁警告圈，1 秒后才落地）
    this.spawnWarnings = [];
    this.spawnInterval = 0.5;
    this.spawnAccumulator = 0;
    this.budgetUsed = 0;
    this.budgetTotal = 0;
    this.elitesToSpawn = 0;
    this.bossSpawned = false;
    this.currentWave = 0;

    /**
     * Boss 替换表（第十一轮）：`{ boss_cat_king: 'boss_acid_cat' }`。
     *
     * 第 10 / 20 波的 Boss 有远程/近战两个版本，出哪个由 main.js
     * 在开局时抽签决定（并记进存档，保证重试本波不会换 Boss）。
     * World 只在这里查表替换，**不自己抽签** —— 见 _spawnBoss 的说明。
     * 空对象 = 不替换，行为和第 10 轮之前完全一致。
     */
    this.bossChoices = {};

    // 难度（倍率组，见 data/difficulty.js）
    this.difficultyId = DEFAULT_DIFFICULTY;
    this.difficulty = getDifficulty(DEFAULT_DIFFICULTY);

    // 统计
    this.totalKills = 0;
    this.totalSpawned = 0;
    this.lastCleanupBonus = 0;

    // ---- 宝箱与宝箱怪（第二轮新增）----
    // 宝箱直接放进 enemies 列表：这样武器/子弹/炮台/宠物的索敌全都不用改，
    // 靠 isChest 标记在 AI 和碰撞处跳过它。
    this.chestsSpawned = 0;
    this.chestsMax = 3;             // 同时存在的宝箱上限
    this.chestTimer = 0;
    this.chestInterval = 0;         // 每波重算
    this.mimicTimer = 0;
    this.mimicInterval = 0;
    // 本回合宝箱怪总数（1~5，Boss 波为 0）与已刷数量
    this.mimicsTotal = 0;
    this.mimicsSpawned = 0;
    // 本波获得的"待结算道具"（回合结算时给玩家选择：留下或卖掉）
    this.pendingItemRewards = [];
    // 本波击杀宝箱怪 / 开箱的计数（给 UI 和吐槽用）
    this.treasureKills = 0;
    this.chestsOpened = 0;

    // 回调
    this.onEnemyKilled = null;
    this.onPickup = null;
    this.onPlayerHit = null;
    this.onBossKilled = null;
    this.onBossPhase = null;   // (boss, newPhase)
    this.onBossGated = null;   // (boss, gatePhase) 撞到 33%/66% 卡点时回调
    this.onPetHeal = null;     // (pet, amount)
    this.onChestOpened = null; // (chest, reward)
    this.onTreasureKilled = null; // (enemy, itemDef)
    // 武器开火（第十轮）：(weaponDef, heavy) —— 由 main.js 接到音频系统。
    // 走回调而不是在 weapon.js 里直接 import 音频单例，
    // 是为了让武器逻辑保持纯逻辑、能在测试里无音频地跑。
    this.onWeaponFire = null;

    this._tmp = [];
    this.player = null;
    // ---- 第一百七十轮：2P 实验（双猫娘同游）----
    // player2 = 第二个真玩家实体（完整 Player，AI 驱动）。null = 单人模式，
    // 所有主干路径通过 players()/nearestPlayerTo() 适配，单人行为零变化。
    // ---- 第一百八十四轮：1~4P（用户："改成自由选择1-4p吧"）----
    // player3 / player4 同构；partners() 是 P2~P4 的活跃列表，players()
    // 是 P1 + 全部队友。旧代码读 world.player2 的地方继续兼容（2P 局
    // 行为不变），新代码一律走 players()/partners()。
    this.player2 = null;
    this.player3 = null;
    this.player4 = null;

    // 多玩家帮助函数：返回当前活跃的玩家数组（P1 永远在，队友按存在过滤）。
    // 主干（仇恨/弹幕/拾取/结算）一律遍历这个数组，不写死单数。
    this.partners = () => [this.player2, this.player3, this.player4].filter(Boolean);
    this.players = () => {
      const ps = [this.player];
      for (const p of this.partners()) ps.push(p);
      return ps;
    };
    // 仇恨目标选择器（DeepSeek 建议的抽象）：谁离得近打谁。
    // 倒地（downed）和已死的玩家不可被选为目标；全都不可选时回落 P1。
    this.nearestPlayerTo = (x, y) => {
      let best = null, bestD = Infinity;
      for (const p of this.players()) {
        if (!p || p.dead || p.downed) continue;
        const d = Math.hypot(p.x - x, p.y - y);
        if (d < bestD) { bestD = d; best = p; }
      }
      return best ?? this.player;
    };

    // ---- 第一百六十轮：四重存在分身（莱万汀）----
    // 实体由 main._updatePhantoms 驱动（要读 player 的持有/战斗状态）；
    // 渲染从 world.phantoms 读。每局 new World 自带空数组，无需重置。
    this.phantoms = [];

    // ---- 第一百六十轮：动态难度（隐式适应，用户规格）----
    // "检测玩家某回合一直在秒杀目标，那么之后怪物的成长偷偷加速一些，
    //  检测玩家半天打不死一个怪，偷偷降低一些怪物成长。只针对秒杀和
    //  半天打不死一个怪。（1-3回合不检测……）"
    // 附加判定（用户追加）："可以加攻击前判定一下，现在被加攻击成长的怪
    //  是否会在成长后达到一击秒杀当前的玩家，如果是就不加强。我们不能
    //  因为玩家秒怪就让怪的攻击无脑成长到玩家满血也一击死。那样子买
    //  +攻击-血量的道具等于单纯给自己加难度了。"
    // 不进 UI、不进存档（新一局重新适应，读档回 1 重适应几波即可）。
    this.diffHp = 1;           // 敌人血量成长乘数
    this.diffDmg = 1;          // 敌人攻击成长乘数（受"一击线"门约束）
    this._killAges = [];       // 本波每个被击杀敌人的存活时长（秒）
    this._waveT = 0;           // 本波已进行时间
    this._maxContactDmg = 0;   // 本波出现过的最高接触伤害（一击线预判用）
  }

  /** 设置难度（开局前调用；会立刻作用于后续所有刷怪与伤害） */
  setDifficulty(id) {
    this.difficultyId = id;
    this.difficulty = getDifficulty(id);
    return this.difficulty;
  }

  // =========================================================
  // 波次控制
  // =========================================================
  startWave(waveIndex) {
    this.currentWave = waveIndex;
    const cfg = getWaveConfig(waveIndex);
    const base = waveScaling(waveIndex);
    const d = this.difficulty;

    // 把难度乘数折进波次缩放里，下游（Enemy / 掉落）不用知道难度存在
    //
    // 第十轮：第 11 波起敌人血量再 +20%（用户："第11回合开始所有敌人
    // hp 上限+20%，因为第十波打完之后还是会很富"）。
    // 默认只在噩梦生效 —— 用户这条是对噩梦的调参，理由里的"很富"
    // 也是他一局噩梦打下来的体感。要放开到全难度改 difficulty.js 的
    // WAVE11_HP_BUFF_ALL 一个布尔值即可。
    const hpWave11 = (WAVE11_HP_BUFF_ALL || d.id === 'nightmare')
      && waveIndex >= WAVE11_HP_BUFF_FROM ? WAVE11_HP_BUFF : 1;

    const scaling = {
      hp: base.hp * d.enemyHp * hpWave11,
      // Boss 专属血量基数（第十四轮）：Boss 走一条比小怪缓得多的曲线
      // （见 waves.js 的 BOSS_HP_GROWTH_PER_WAVE）。难度倍率 / 第 11 波 buff
      // 与普通怪完全一致，所以"同一份比例"仍然作用在四档难度上。
      bossHp: (base.bossHp ?? base.hp) * d.enemyHp * hpWave11,
      damage: base.damage * d.enemyDamage,
      speed: base.speed * d.enemySpeed,
      // 近战敌人单独一档移速（只有噩梦配了；没配就退回普通档）
      speedMelee: base.speed * (d.enemySpeedMelee ?? d.enemySpeed),
      // 第三十轮：弹幕射程成长 —— 故意**不乘任何难度倍率**（射程是空间
      // 属性，用户："所有难度通用。毕竟低难度弹幕也不疼"）。
      bulletRange: base.bulletRange ?? 1,
      // 第十九轮末：速度绝对上限（speedCap）已随 ENEMY_SPEED_CAP 一起移除 ——
      // 敌人移速随波次成长如实叠加、不再封顶（见 data/difficulty.js 第十九轮末
      // 的墓碑说明；Enemy 构造函数也不再读 scaling.speedCap）。
      xp: base.xp * d.xpMul,
      materials: base.materials * d.materialMul,
      // 无限模式的 Boss 专属成长（3 倍）：必须显式带过去，
      // 否则 Enemy 里读到的永远是 undefined → 退回 1，Boss 不会变强
      bossGrowth: base.bossGrowth ?? 1,
    };

    this.waveConfig = cfg;
    // ---- 第二十一轮：特殊波次 ----
    // 掷骰决定本波是否"全是某一类敌人"（11 波起、非 Boss 波、冷却外）。
    // 结果缓存在 World 上：重试本波复现同一个特殊波，不会重掷。
    this.specialWave = this._rollSpecialWave(waveIndex, cfg);
    // ---- 第一百六十轮：动态难度乘区（隐式适应）----
    // 只动血量与攻击成长，不动速度/经验/材料（用户只点名"攻击和血量"）。
    scaling.hp *= this.diffHp;
    scaling.damage *= this.diffDmg;
    // 本波击杀统计重置（动态难度检测数据）
    this._killAges.length = 0;
    this._waveT = 0;
    this._maxContactDmg = 0;
    this.waveScaling = scaling;
    this.waveDuration = getWaveDuration(waveIndex);
    // 难度影响刷怪预算（数量）
    // 第七轮：再乘一个随波次增长的刷怪率倍率（第 20 波 ×2.00，无限模式封顶维持）。
    // 这是**所有难度**共享的，不区分难度。
    this.spawnRateMul = spawnRateMul(waveIndex);
    // 第一百五十六轮：隔壁发免费鸡蛋了？—— 刷新数量 -50%（掉落翻倍在
    // _dropLoot；"不影响本局经济"= 不动 durationDamp / earlyBonus 这些
    // 平衡曲线，纯刷怪量减半 + 掉落纯 ×2）
    if (this.specialWave === 'egg') this.spawnRateMul *= 0.5;
    this.budgetTotal = Math.max(3, Math.round(cfg.budget * d.enemyCount * this.spawnRateMul));
    this.budgetUsed = 0;
    this.elitesToSpawn = Math.max(0, Math.round((cfg.elites ?? 0) * (d.enemyCount > 1 ? 1 + (d.enemyCount - 1) * 0.5 : 1)));
    // 体检修复（第九十二轮）：精英生成阈值要按"已刷序号"算，
    // 不然 elitesToSpawn ≥ 5 时阈值 ≥ 1.0（waveProgress 封顶 1），
    // 严格大于永远为假 → 后半局与整个无尽模式的精英静默消失。
    this.elitesTotal = this.elitesToSpawn;
    this.bossSpawned = false;
    this.spawnTimer = 0.4;
    // 新波开始：上一波的残余警告清干净（波次结算不该有跨波预告）
    this.spawnWarnings.length = 0;
    // 预算按时间均匀释放（预算变多时缩短间隔，保证波内刷得完）
    this.spawnInterval = this.waveDuration / Math.max(6, this.budgetTotal * 1.15);

    // Boss 战追加刷小怪（第十二轮）：只在 Boss 波启用爬升曲线。
    //
    // 用户说的是"20波boss战和后续的无限boss战"，而这两类在数据上
    // 都由 cfg.isBoss 标记（第 10 / 20 波硬编码，无限 Boss 波也是
    // isBoss: true）—— 所以用 isBoss 一个判据就能精确覆盖用户点名的
    // 两个场景，不会误伤普通波。
    this.bossWaveAdds = !!cfg.isBoss;

    // ---- 宝箱与宝箱怪 ----
    // 宝箱：每波 1~3 个，随机时间点出现（不是一开局就摆好）
    this.chestInterval = this.waveDuration / (2 + this.rng.next() * 1.5);
    this.chestTimer = 4 + this.rng.next() * 6;
    // 宝箱怪：用户第四轮要求"一回合最多刷 5 个，最少刷 1 个"。
    // 所以在开局就抽定本回合总数，再按总数平均分摊时间点刷出来。
    // Boss 波不刷（Boss 战已经够乱）。
    const isBossWave = !!cfg.isBoss;
    this.mimicsTotal = isBossWave ? 0 : (1 + Math.floor(this.rng.next() * 5));
    this.mimicsSpawned = 0;
    this.mimicTimer = 3 + this.rng.next() * 5;
    this.mimicInterval = this.mimicsTotal > 0 ? this.waveDuration / this.mimicsTotal : Infinity;
    this.pendingItemRewards = [];
    this.treasureKills = 0;
    this.chestsOpened = 0;

    // ---- 时长归一化 ----
    // 第三轮把波次时长从固定 30s 拉到最高 60s（Boss 关 90s）。
    // 时间变长 → 一波能杀完的敌人变多 → 每波材料收入直接翻倍，
    // 把第二轮的"中后期不要那么富"又推翻了。
    //
    // durationDamp 把"每波收入"拉回和 30 秒时代一致。
    // 用指数 1.4 而不是线性 1.0，因为长波不只是"多刷一会"：
    // 预算能被完整打完（短波常常打不完），收入对时长是**超线性**的。
    // 下限 0.35 兜底，避免极端时长把 Boss 的固定大掉落削得太狠。
    const ratio30 = 30 / Math.max(1, this.waveDuration);
    this.durationDamp = Math.max(0.3, Math.min(1, Math.pow(ratio30, 1.4)));
  }

  // =========================================================
  // 宝箱
  // =========================================================
  /** 找一个离玩家不太近也不太远的点放宝箱（要走过去打，但不用满地图找） */
  _chestPosition(player) {
    const a = this.rng.next() * TAU;
    const d = this.rng.range(260, 620);
    const half = this.halfSize - 60;
    return {
      x: clamp(player.x + Math.cos(a) * d, -half, half),
      y: clamp(player.y + Math.sin(a) * d, -half, half),
    };
  }

  /** 放一个宝箱 */
  spawnChest(player, opts = {}) {
    const luck = player?.stats?.values?.luck ?? 0;
    // 第十六轮：把角色的幸运总效果倍率也传下去。
    // rollChestReward 收的是"面板值 + 倍率"（不是 luckEff），
    // 因为它同时被好几个测试直接以裸数字调用，加可选参数最省事。
    const luckMul = player?.stats?.luckEffectMul ?? 1;
    const rare = !!opts.rare;
    const reward = rollChestReward(this.rng, luck, rare, luckMul);
    const col = chestColors(reward);
    let pos = opts.x !== undefined ? { x: opts.x, y: opts.y } : this._chestPosition(player);
    // 体检修复（第九十二轮）：宝箱落点也过地形检查 —— 宝箱固定不动、
    // 自身不做地形解算，落进障碍物里近战就永远够不着。
    // 指定坐标（宝箱怪奖励等调用方）尊重调用方，随机落点才检查；
    // 随机落点找不到空位就本次不刷（下一轮 chestTimer 会再试）。
    if (opts.x === undefined) {
      const spot = findFreeSpot(this.terrain, () => this._chestPosition(player), 20, 16);
      if (spot) pos = spot; else return null;
    }
    const chest = new Chest(pos.x, pos.y, {
      hp: rare ? 90 : 45,
      tier: rare ? 2 : 1,
      rare,
      reward,
      color: col.color,
      accent: col.accent,
    });
    this.enemies.add(chest);
    this.chestsSpawned++;
    this.fx?.ring(pos.x, pos.y, 40, col.color, 0.6);
    return chest;
  }

  /** 放一只宝箱怪（会逃跑的那种） */
  spawnTreasureMimic(player, opts = {}) {
    const pool = getTreasureEnemies();
    if (pool.length === 0) return null;
    // 稀有的那只概率低一些
    let def = pool[0];
    if (pool.length > 1) {
      // 第十六轮：改用 player.stats.luckEff 而不是 effectiveLuck(面板值)。
      // luckEff 会额外乘上角色的 luckEffectMul —— 悠怡萌妹的
      // "幸运总效果 +20%"就是靠它生效的（面板 × 0.8 × 1.2 = ×0.96）。
      // 如果这里继续读面板值，那个被动就只是属性面板上的一个数字，
      // 对任何实际概率都没有影响。
      const rareChance = 0.22 + Math.min(0.3, (player?.stats?.luckEff ?? 0) / 400);
      def = this.rng.next() < rareChance ? pool[1] : pool[0];
    }
    const pos = opts.x !== undefined ? { x: opts.x, y: opts.y } : this._spawnPosition(player, 420, 700);
    const e = this.spawnEnemyById(def.id, pos.x, pos.y);
    if (e) {
      this.fx?.nova(pos.x, pos.y, 90, def.color);
    }
    return e;
  }

  /**
   * 第九十三轮：当前场上**存活敌人**数（不含宝箱怪与已死亡待清扫的）。
   * 给 ENEMY_ALIVE_CAP 的刷怪上限用 —— 宝箱怪是彩袋不是威胁，不占额度。
   */
  aliveEnemyCount() {
    let n = 0;
    for (const e of this.enemies.items) {
      if (!e.dead && !e.isChest) n++;
    }
    return n;
  }

  /** 每帧推进宝箱/宝箱怪的刷新计时 */
  _updateTreasureSpawning(dt, player) {    const alive = this.enemies.items.filter((e) => !e.dead && e.isChest).length;

    // 宝箱
    if (this.chestTimer !== Infinity) {
      this.chestTimer -= dt;
      if (this.chestTimer <= 0) {
        if (alive < this.chestsMax) this.spawnChest(player);
        this.chestTimer = this.chestInterval * (0.7 + this.rng.next() * 0.6);
      }
    }

    // 宝箱怪：本回合刷够 mimicsTotal 只就停
    if (this.mimicTimer !== Infinity && this.mimicsSpawned < this.mimicsTotal) {
      this.mimicTimer -= dt;
      if (this.mimicTimer <= 0) {
        this.spawnTreasureMimic(player);
        this.mimicsSpawned++;
        this.mimicTimer = this.mimicInterval * (0.7 + this.rng.next() * 0.6);
      }
    }
  }

  /**
   * 开箱 / 击杀宝箱怪 → 结算奖励。
   *
   * 用户要求：
   *  - 宝箱怪："随机获得一个道具在回合结算的时候给予玩家，玩家可以选择拥有他或者卖掉换钱"
   *  - 宝箱："有概率给道具有概率是医疗包"
   * 所以道具**不立即生效**，而是进 pendingItemRewards，等回合结算时让玩家选。
   * 医疗包则立刻回血（那是即时救命的东西，不适合拖到结算）。
   */
  _resolveTreasure(player, entity) {
    if (entity.isChest) {
      this.chestsOpened++;
      const rw = entity.reward;
      if (rw.kind === 'heal') {
        const max = player.stats.get('maxHp');
        const amount = Math.max(8, Math.round(max * (rw.pct ?? 0.35)));
        player.heal(amount);
        this.fx?.damageText(entity.x, entity.y - 24, `+${amount}`, false, '#6ee7a8');
        this.fx?.sparkle(entity.x, entity.y, '#6ee7a8', 8);
        this.onChestOpened?.(entity, { kind: 'heal', amount });
      } else {
        const reward = this._rollRewardItem(player, rw.rare);
        if (reward) {
          this.pendingItemRewards.push(reward);
          this.fx?.damageText(entity.x, entity.y - 24, reward.name, false, '#ffc857');
          this.fx?.sparkle(entity.x, entity.y, '#ffc857', 10);
          this.onChestOpened?.(entity, { kind: 'item', item: reward });
        }
      }
    } else if (entity.def?.treasure) {
      this.treasureKills++;
      const reward = this._rollRewardItem(player, entity.def.treasureRare);
      if (reward) {
        this.pendingItemRewards.push(reward);
        this.fx?.damageText(entity.x, entity.y - 30, reward.name, false, '#ffc857');
        this.fx?.sparkle(entity.x, entity.y, '#ffc857', 12);
        this.onTreasureKilled?.(entity, reward);
      }
    }
  }

  /**
   * 抽一件道具作为奖励。品质与幸运挂钩。
   * @returns {{id:string,name:string,price:number,rare:boolean}|null}
   */
  _rollRewardItem(player, forceRare = false) {
    // 第十六轮：走 stats.luckEff（含角色幸运倍率），理由见 spawnTreasureMimic。
    const luckBonus = Math.min(1, Math.max(0, (player?.stats?.luckEff ?? 0) / 220));
    // 动态 import 会打乱打包/启动顺序，这里直接用顶部已导入的道具表
    const pool = this._itemPool ?? (this._itemPool = getRewardItemPool());
    if (!pool || pool.length === 0) return null;

    // 按价格分层加权：幸运越高越容易出贵的
    const weights = pool.map((it) => {
      const t = it.price >= 26 ? 3 : it.price >= 18 ? 2 : 1;
      let w = t === 1 ? 10 : t === 2 ? 6 : 3;
      if (t >= 2) w *= 1 + luckBonus * 1.8;
      if (forceRare) w *= t >= 2 ? 3 : 0.6;
      return w;
    });
    let total = 0;
    for (const w of weights) total += w;
    let r = this.rng.next() * total;
    let idx = pool.length - 1;
    for (let i = 0; i < pool.length; i++) {
      r -= weights[i];
      if (r <= 0) { idx = i; break; }
    }
    const def = pool[idx];
    return {
      id: def.id,
      name: def.name,
      price: def.price,
      rare: forceRare || def.price >= 26,
      // 卖出价：大约半价（和武器出售的手感一致）
      sellValue: Math.max(4, Math.round(def.price * 0.55)),
    };
  }

  get waveProgress() {
    return clamp(this.budgetUsed / Math.max(1, this.budgetTotal), 0, 1);
  }

  get isSpawnFinished() {
    return this.budgetUsed >= this.budgetTotal && this.spawnQueue.length === 0;
  }

  // =========================================================
  // 刷怪
  // =========================================================

  /**
   * 第二十一轮：掷特殊波（冲锋 / 弹幕 / 肉盾三类各 20%）。
   *
   * 规则（用户原话见 waves.js 的 SPECIAL_WAVE_POOLS 注释）：
   *  - 第 11 波起才可能触发；
   *  - Boss 波（10/20 波与无限 Boss 波）不参与；
   *  - 触发一次后，接下来 SPECIAL_WAVE_COOLDOWN(2) 个回合内不再触发 ——
   *    用"上一个特殊波的索引"来判断（waveIndex - last <= 2 就挡住），
   *    而不是递减计数器：那样重试普通波会把冷却偷偷烧掉；
   *  - 抽签结果按波次缓存：重试本波复现同一个特殊波（重试不该换怪）。
   *
   * @returns {'charge'|'bullet'|'tank'|'chaos'|'slack'|'payday'|'egg'|null}
   */
  _rollSpecialWave(waveIndex, cfg) {
    if (waveIndex + 1 < SPECIAL_WAVE_FROM) return null;
    if (cfg.isBoss) return null;
    const last = this._lastSpecialWave;
    if (last != null && waveIndex - last <= SPECIAL_WAVE_COOLDOWN) return null;

    // 重试复现：这一波已经掷过就直接用缓存结果
    this._specialCache ??= new Map();
    if (this._specialCache.has(waveIndex)) {
      const cached = this._specialCache.get(waveIndex);
      if (cached) this._lastSpecialWave = waveIndex;
      return cached;
    }

    // ---- 第一百五十六轮：三类扩七类 ----
    // 用户原话见 waves.js SPECIAL_WAVE_POOLS 的注释。总触发率保持原来的
    // 约 60%（SPECIAL_WAVE_CHANCE × 3），七类**均分**：每类 ≈8.6%。
    // 老三类排在前面的顺序不变（老存档缓存里存的是类型名，不受影响）。
    const SPW_TYPES = ['charge', 'bullet', 'tank', 'chaos', 'slack', 'payday', 'egg'];
    const r = this.rng.next();
    let type = null;
    const total = SPECIAL_WAVE_CHANCE * 3;
    if (r < total) {
      type = SPW_TYPES[Math.min(SPW_TYPES.length - 1, Math.floor(r / (total / SPW_TYPES.length)))];
    }

    this._specialCache.set(waveIndex, type);
    if (type) this._lastSpecialWave = waveIndex;
    return type;
  }

  _pickSpawnEntry() {
    // 第二十一轮：特殊波次 —— 本波被标记成 charge/bullet/tank 时，
    // 改从对应的全场类别池等权抽（不再读该波自己的 spawns），
    // 保证"这关全是XX类"不取决于原波次配置里恰好有没有那一类。
    if (this.specialWave) {
      const pool = SPECIAL_WAVE_POOLS[this.specialWave];
      // ---- 第一百六十轮：?. 容错 ----
      // chaos/slack/payday/egg 四类**没有 ids 字段**（不改刷怪构成，只挂
      // 波次模组）—— 旧写法 pool.ids.length 在这四类上直接 TypeError，
      // 每帧刷怪都炸，游戏卡死（用户实测"进入发薪日游戏卡住不动了"）。
      if (pool?.ids?.length > 0) {
        let r = this.rng.next() * pool.ids.length;
        for (const id of pool.ids) {
          r -= 1;
          if (r <= 0) return { id };
        }
        return { id: pool.ids[pool.ids.length - 1] };
      }
    }
    const cfg = this.waveConfig;
    const pool = cfg.spawns;
    let total = 0;
    for (const s of pool) total += s.weight;
    let r = this.rng.next() * total;
    for (const s of pool) {
      r -= s.weight;
      if (r <= 0) return s;
    }
    return pool[pool.length - 1];
  }

  /**
   * 换背景时同步地形（第十二轮）。
   *
   * 为什么地形要跟着背景走：障碍物在**画面上**就是背景图里的深色方块/花坛，
   * 所以"哪里挡路"完全由当前是哪张图决定。换图不换地形 = 撞空气墙，
   * 换地形不换图 = 能穿墙，两种都是玩家一眼就能看出来的 bug。
   *
   * @param {string|null} bgId 背景 id
   */
  setTerrain(bgId) {
    this.terrainBackgroundId = bgId ?? null;
    this.terrain = bgId ? expandTerrain(bgId, WORLD_SIZE) : { rects: [], circles: [] };
  }

  /** 当前位置是否被地形挡住 */
  isBlocked(x, y, radius = 0) {
    return blockedAt(this.terrain, x, y, radius);
  }

  /**
   * 找一个不在障碍物里的出生点（第十二轮）。
   *
   * 为什么必须做：出生点是"玩家周围 480~760 的随机角度"，完全可能正好
   * 落在深色方块或花坛里。那样敌人一出生就卡死在墙里 ——
   * 玩家看到的是"墙里有个怪在抽搐"，或者更糟：墙里源源不断地冒弹幕。
   *
   * 找不到空位时**宁可这次不刷**（返回 null），也不硬放进墙里：
   * 少刷一只怪只是节奏上少一点，卡一只怪在墙里却是永久的视觉 bug。
   */
  _spawnPosition(player, minDist = 480, maxDist = 760) {
    const pick = () => {
      const a = this.rng.next() * TAU;
      const d = this.rng.range(minDist, maxDist);
      const half = this.halfSize - 40;
      return {
        x: clamp(player.x + Math.cos(a) * d, -half, half),
        y: clamp(player.y + Math.sin(a) * d, -half, half),
      };
    };
    // 敌人体积按常见半径留一点余量，免得贴着墙边刷出来还是卡住
    return findFreeSpot(this.terrain, pick, 26, 24);
  }

  spawnEnemyById(id, x, y, mutate = false) {
    const def = getEnemy(id);
    if (!def) return null;
    // 第一百六十三轮：移动端同屏普通怪上限（用户："限制移动端同屏最大
    // 怪物数量（比如超过 50 个就不刷了）"）。boss 与剧情召唤不受限。
    if (MOBILE && !def.boss && this.enemies.items.length >= MOBILE_TUNE.MAX_ENEMIES) {
      return null;
    }
    const e = new Enemy(def, x, y, this.waveScaling ?? { hp: 1, damage: 1, speed: 1, xp: 1, materials: 1 }, this.rng);
    // 第一百零八轮：敌人持有 world 引用 —— 天气（川雾/台风推挤、
    // 移速乘区）在 _move 等无 world 参数的方法里也要读得到
    e.world = this;
    // ---- 第一百七十七轮（2P）→ 第一百八十四轮（1~4P）：多人血量补偿 ----
    // 用户："发现是多人模式就小怪基础额外+20%，精英+40%，boss加60%，
    // 只加血量。"（材料/经验共享后全场收入随人数涨，用血量把战斗时长
    // 拉回来。）184 轮按用户口径线性递增："每多一个玩家，血量就按我们
    // 之前的加法增加小怪20%精英40%boss60%" —— k = 额外玩家数：
    // 2P = +20%/+40%/+60%，4P = +60%/+120%/+180%。只乘血量，伤害/移速/
    // 经验/掉落一概不动。
    {
      const k = this.players().length - 1;
      if (k > 0) {
        // ---- 184 轮削弱（用户："把多人加血从每个人+20%小怪40%精英60%boss
        // 改成 15% 30% 45%吧。试试削弱一下看看，这样4个人小怪血量低了
        // 15%boss45%也不少了。不然她们几乎无法打过后期的怪"）：
        // 每额外玩家小怪 +15% / 精英 +30% / Boss +45%
        // —— 4P = ×1.45 / ×1.90 / ×2.35（原 ×1.6 / ×2.2 / ×2.8）。
        const hpMul = 1 + (def.boss ? 0.45 : def.elite ? 0.3 : 0.15) * k;
        e.maxHp = Math.round(e.maxHp * hpMul);
        e.hp = e.maxHp;
      }
    }
    // 召唤出来的敌人不带出生动画延迟太长
    e.spawnAnimation = 0.2;
    // ---- 第一百六十轮：噩梦专属基础血线 ----
    // 用户："噩梦难度的蒜蓉粉丝开背虾血量改到第十波见到他的时候大概
    // 35000左右的血量吧。（基础血线你算一下改了）"
    // 第 10 波（waveIndex 9）噩梦实际血量 = 基础 × bossHpCurve(9)≈4.260
    // × 噩梦 enemyHp 2.8 ≈ 基础 × 11.927 —— 35000 → 基础 2935。
    // 只覆盖噩梦（def.hp 是全难度基础线，直接改会把简单/中等/困难
    // 的虾也抬高 40%）。变异体等其他乘区照常叠在覆盖后的血量上。
    if (def.hpNightmare != null && this.difficulty?.id === 'nightmare') {
      const mul = def.hpNightmare / (def.hp ?? 10);
      e.maxHp = Math.round(e.maxHp * mul);
      e.hp = e.maxHp;
    }
    // ---- 第一百五十三轮：法环"深夜模式"变异体 ----
    // 用户："某些小怪（5%~10%概率）出生时会变成'变异体'，血量、伤害、
    // 移速大幅提升……击杀奖励是普通怪的 3 倍，甚至必定掉落一个回血包。"
    // 数值（用户硬性规格）：HP ×2.5 / 移速 ×1.2 / 伤害 ×1.5 / 奖励 ×3。
    // 接触伤害走 this.damage、弹幕走 dmgScale（两个独立乘区，见 128 行
    // 与各 _firePattern 注释），所以两个都 ×1.5 才是完整的"伤害 ×1.5"。
    if (mutate && !def.boss) {
      e.mutant = true;
      e.maxHp = Math.round(e.maxHp * 2.5);
      e.hp = e.maxHp;
      e.speed *= 1.2;
      e.damage *= 1.5;
      e.dmgScale *= 1.5;
      e.xpValue = Math.round(e.xpValue * 3);
      e.materialValue = Math.round(e.materialValue * 3);
    }
    // ---- 第一百五十六轮：特殊波数值模组（生成时一次性挂上）----
    // 全员摸鱼日：移速 -30%（攻速在 enemy 射击冷却处砍）
    if (this.specialWave === 'slack' && !def.boss) e.speed *= 0.7;
    // 发薪日：攻击 +20%（接触 damage 与弹幕乘区 dmgScale 两条通道都吃）
    if (this.specialWave === 'payday' && !def.boss) {
      e.damage *= 1.2;
      e.dmgScale *= 1.2;
    }
    this.enemies.add(e);
    this.totalSpawned++;
    // 动态难度统计：出生时刻（算存活时长）+ 本波最高接触伤害（一击线预判）
    e._bornWaveT = this._waveT;
    if (e.damage > this._maxContactDmg && !def.boss) this._maxContactDmg = e.damage;
    return e;
  }

  /**
   * 第一百六十轮：动态难度结算（每波结束由 main._endWave 调用，隐式无 UI）。
   *
   * 用户规格："检测玩家某回合一直在秒杀目标，那么之后怪物的成长偷偷加速
   * 一些，检测玩家半天打不死一个怪，偷偷降低一些怪物成长。只针对秒杀和
   * 半天打不死一个怪。（1-3回合不检测。如果玩家4回合开始就秒秒秒就每回合
   * 多加怪一点点攻击和血量直到不再秒秒秒，而反之一个精英怪打到超时都
   * 打不死，小怪也费劲，下回合怪物的攻击和血量成长就降低一些。）"
   *
   * 攻击一击门（用户追加）："可以加攻击前判定一下，现在被加攻击成长的怪
   * 是否会在成长后达到一击秒杀当前的玩家，如果是就不加强。"
   * —— 用本波实际出现过的最高接触伤害外推：加压后如果对当前玩家
   * （扣护甲后）≥ 满血，攻击乘数保持不动，只加血量。
   */
  tuneDiffScale() {
    const STEP = 0.06;         // 每回合 ±6%，温和爬坡
    const HP_MIN = 0.75, HP_MAX = 1.45, DMG_MIN = 0.8, DMG_MAX = 1.3;
    // currentWave 0 基（第 1 波 = 0）：1-3 回合不检测 = 第 4 波（3）起。
    // Boss 波时长固定、特殊波刷怪结构异常，数据失真都不检测。
    if (this.currentWave < 3 || this.bossSpawned || this.specialWave) {
      this._killAges.length = 0;
      return;
    }
    const ages = this._killAges;
    const n = ages.length;
    // 打满时限还没清完（main 超时路径也会走 _endWave）= "打不动"强信号
    const timedOut = this._waveT >= this.waveDuration - 0.5;
    let delta = 0;
    if (!timedOut && n >= 8) {
      let fast = 0;
      for (const a of ages) if (a < 1.5) fast++;
      if (fast / n >= 0.7) delta = STEP;            // 秒秒秒 → 偷偷加压
    }
    if (delta === 0 && timedOut) {
      let sum = 0;
      for (const a of ages) sum += a;
      const avg = n ? sum / n : Infinity;
      if (n < 5 || avg > 7) delta = -STEP;          // 精英磨到超时 → 偷偷减压
    }
    if (delta !== 0) {
      // 血量：无条件跟随
      this.diffHp = Math.min(HP_MAX, Math.max(HP_MIN, this.diffHp + delta));
      // 攻击：加压前过"一击线"门 —— 本波最高接触伤害按新乘数外推，
      // 扣掉玩家护甲后若 ≥ 玩家满血，攻击本轮不升（血量照升）。
      if (delta > 0) {
        const p = this.player;
        const armor = p?.stats?.values?.armor ?? 0;
        const peak = this._maxContactDmg * ((this.diffDmg + STEP) / Math.max(0.01, this.diffDmg));
        if (p && Math.max(1, peak - armor) >= p.maxHp) {
          // 用户："不能因为玩家秒怪就让怪的攻击无脑成长到玩家满血也一击死。"
          this.diffDmg = Math.max(DMG_MIN, this.diffDmg);   // 保持不动
        } else {
          this.diffDmg = Math.min(DMG_MAX, Math.max(DMG_MIN, this.diffDmg + STEP));
        }
      } else {
        this.diffDmg = Math.min(DMG_MAX, Math.max(DMG_MIN, this.diffDmg + delta));
      }
    }
    this._killAges.length = 0;
  }

  // =========================================================
  // 第一百七十二轮：刷怪预告（两阶段刷怪）
  // ---------------------------------------------------------
  // 用户："让ai猫娘能提前知道哪里会刷怪……知道哪里提前刷了就不过去，
  // 不知道一刷新就来个亲嘴。""给刷怪的地方提前画个小圈……提前闪烁
  // 1秒的小圈？"
  //
  // 原理：刷怪点本来就是 `_spawnPosition(player)` 实时随机的（种子化
  // rng，但受当前时间/预算/空地影响，提前推演不现实）——所以改成
  // **先预告、后登场**：选好点先挂一个 1 秒的警告（spawnWarnings，
  // 玩家看得见闪烁圈，AI 读同一份列表绕开），1 秒后怪才真正落地。
  //
  // · 普通怪警告 1.0s，精英 1.4s（更醒目）
  // · Boss 不预告（它有自己的入场演出，且固定从上方来）
  // · rng 消耗顺序：选种/选点在入队时、变异/异常在落地时 —— 和原版
  //   一样的随机量，只是多了一个 1 秒的"缓冲带"
  // =========================================================
  _queueSpawnOne(player) {
    const entry = this._pickSpawnEntry();
    const pos = this._spawnPosition(player);
    // 找不到空地就不刷（见 _spawnPosition 的说明）
    if (!pos) return null;
    this.spawnWarnings.push({ x: pos.x, y: pos.y, t: 1.0, entryId: entry.id, elite: false });
    return null;
  }

  /** 精英也走预告（圈更大更久，给玩家足够的逃离窗口） */
  _queueSpawnElite(player) {
    const def = this.rng.pick(getEliteEnemies());
    if (!def) return;
    const pos = this._spawnPosition(player, 520, 720);
    if (!pos) return;
    this.spawnWarnings.push({ x: pos.x, y: pos.y, t: 1.4, entryId: def.id, elite: true });
  }

  /**
   * 警告倒计时 → 真正落地。返回本帧落地的怪（ mostly null，一帧可能
   * 多个警告到期）。落地时的变异/行为异常判定保持原顺序。
   */
  _settleSpawnWarnings() {
    let spawnedAny = false;
    for (let i = this.spawnWarnings.length - 1; i >= 0; i--) {
      const w = this.spawnWarnings[i];
      w.t -= this._lastDt ?? 0.033;
      if (w.t > 0) continue;
      this.spawnWarnings.splice(i, 1);
      // 落地瞬间的变异掷骰（原 _spawnOne 的逻辑原样搬来）
      const mutChance = this.endless ? 0.20 : 0.10;
      const mutate = this.currentWave >= 5 && this.rng.next() < mutChance;
      const e = this.spawnEnemyById(w.entryId, w.x, w.y, mutate);
      if (e && !e.mutant) this._rollQuirk(e);
      spawnedAny = spawnedAny || !!e;
    }
    return spawnedAny;
  }

  _spawnOne(player) {    const entry = this._pickSpawnEntry();
    const pos = this._spawnPosition(player);
    // 找不到空地就不刷（见 _spawnPosition 的说明）
    if (!pos) return null;
    // ---- 第一百五十三轮：变异掷骰（每波刷怪时）----
    // 用户："每次生成都有 10% 的几率变成变异体（Boss 除外，1-5 回合不
    // 生成变异怪。无限模式概率变成 20%）"。currentWave 是 0 起算
    //（_beginWave(0) = 第 1 波，见 main 1001 行），所以"1-5 回合不出"
    // = currentWave >= 5 才开始掷。endless 由 main._beginWave 每波同步。
    const mutChance = this.endless ? 0.20 : 0.10;
    const mutate = this.currentWave >= 5 && this.rng.next() < mutChance;
    const e = this.spawnEnemyById(entry.id, pos.x, pos.y, mutate);
    // 行为异常在变异**之后**结算（用户："变异怪不吃这概率，先结算变异怪
    // 后结算这个"）
    if (e && !e.mutant) this._rollQuirk(e);
    return e;
  }

  /**
   * 第一百五十三轮：行为异常（"怪菌"）—— 20% 的波次小怪 AI 出故障。
   * 用户原话："怪物（boss除外）有20%产生行为异常现象（外形不变……）。
   * 近战怪随机套远程怪ai行为，远程怪随机套近战怪ai行为，摸鱼（直接挂机
   * 不动，挨打的时候50%概率恢复正常ai，50%概率用宝箱怪ai，就是会不停
   * 逃离玩家。）"
   * 奶妈/宝箱怪/自爆这类**机制怪**不掷 —— 它们的行为就是身份，换了直接
   * 坏掉机制（奶妈追着你砍、自爆怪不炸都算 bug 不是"异常"）。
   */
  _rollQuirk(e) {
    if (!e || e.boss) return;
    const b = e.def?.behavior;
    if (b === 'flee' || b === 'healer' || b === 'exploder' || b === 'boss'
      || b === 'meleeBoss' || e.isChest) return;
    if (!(this.rng.next() < 0.20)) return;
    if (e.melee) {
      // 近战怪：随机套远程 AI，或摸鱼
      e.aiOverride = this.rng.next() < 0.5 ? 'ranged' : 'idle';
    } else {
      // 远程怪：随机套近战 AI，或摸鱼
      e.aiOverride = this.rng.next() < 0.5 ? 'melee' : 'idle';
    }
  }

  _spawnElite(player) {
    const def = this.rng.pick(getEliteEnemies());
    if (!def) return;
    const pos = this._spawnPosition(player, 520, 720);
    if (!pos) return;
    this.spawnEnemyById(def.id, pos.x, pos.y);
  }

  /**
   * 生成本波的 Boss。
   *
   * 普通波用 `waveConfig.boss`（单个 id）；
   * 无限模式的 Boss 波用 `waveConfig.bosses`（数组，30 波起 2 个、35/40 波 3 个）。
   * 多个 Boss 时按横向扇形错开站位，避免叠在同一个点上。
   *
   * 第十一轮：`waveConfig.boss` 还要过一道 `bossChoices` 的替换 ——
   * 第 10 / 20 波的 Boss 有"远程 / 近战"两个版本，具体出哪个由 main.js
   * 在**开局时抽签并记进存档**。这里只负责查表，不自己抽
   * （World 每次 startWave 都会被调用，在这里抽签会导致"重试本波
   * 就换一个 Boss"，那 50% 概率就变成"重试到满意为止"了）。
   */
  _spawnBoss(player) {
    const cfg = this.waveConfig;
    // 体检修复（第九十二轮）：bossChoices 替换只作用于 cfg.boss
    // （10/20 波的"远程/近战"抽签位）。cfg.bosses 来自 endlessBossLineup
    // 的轮换表，是"40 波全员一次登场"的编排结果 —— 套用替换表会把
    // 抽中近战变体的那两个基础 id 无尽阵容也偷换掉， lineup 承诺作废。
    const resolve = (id) => this.bossChoices?.[id] ?? id;
    const ids = cfg.bosses && cfg.bosses.length
      ? [...cfg.bosses]
      : (cfg.boss ? [resolve(cfg.boss)] : []);
    if (ids.length === 0) return;

    // Boss 固定从上方入场
    const baseX = clamp(player.x, -this.halfSize + 200, this.halfSize - 200);
    const baseY = clamp(player.y - 420, -this.halfSize + 100, this.halfSize - 100);

    let spawnedAny = false;
    for (let i = 0; i < ids.length; i++) {
      // 多个 Boss 时横向铺开（-1/0/+1 之类的偏移）
      const spread = ids.length === 1 ? 0 : (i - (ids.length - 1) / 2) * 260;
      const x = clamp(baseX + spread, -this.halfSize + 120, this.halfSize - 120);
      const boss = this.spawnEnemyById(ids[i], x, baseY);
      if (boss) {
        spawnedAny = true;
        this.fx.nova(x, baseY, 120, boss.color);
      }
    }
    if (spawnedAny) {
      this.bossSpawned = true;
      this.camera.shake(14, 0.6);
    }
  }

  updateSpawning(dt, player, waveElapsed) {
    // ---- 第一百八十轮：天彗龙吼叫眩晕 —— 3 秒内完全停止刷怪 ----
    // 用户规格："龙吼叫眩晕3秒期间不刷怪。防止新刷的怪在3秒内杀死玩家。"
    // 冻结由 main 侧控制 world.update 的 dt 实现（dt=0），这里再兜一层
    // 显式开关，防以后有人绕过 dt 直接调 updateSpawning。
    if (this.spawnPaused) return;
    // 第一百七十二轮：警告倒计时**每帧都要走**（包括 isSpawnFinished
    // 之后 —— 预算刷完了，最后一批警告还得落地），先记 dt 再结算。
    this._lastDt = dt;
    this._settleSpawnWarnings();
    if (this.isSpawnFinished) return;

    this.spawnTimer -= dt;
    if (this.spawnTimer > 0) return;

    // 按剩余时间决定这一 tick 刷多少
    const remainingTime = Math.max(0.5, this.waveDuration - waveElapsed);
    const remainingBudget = Math.max(0, this.budgetTotal - this.budgetUsed);
    const perTick = Math.max(1, Math.ceil(remainingBudget / (remainingTime / this.spawnInterval)));

    // Boss 战追加刷小怪（第十二轮，用户要求）：
    // "从开头的生怪频率为波次的50%随时间增加到150%（既90秒用完的时候）"。
    //
    // 这里按**预算释放进度**发怪，而不是去缩放间隔或 perTick。
    // 前两版都失败过，原因值得记下来：
    //
    //   v1：乘 perTick → 完全无效。perTick 早就是 1（72 只摊到 82 个 tick），
    //       而 Math.max(1, round(1 × 0.5)) = 1。**整数量化**把曲线吃掉了。
    //   v2：除 spawnInterval（让 rate 作用于"多久刷一次"）→ 前半段确实慢了，
    //       但**尾巴被截断**：预算按原间隔在 82 秒左右就刷完了，
    //       82~90 秒没有怪，峰值只到 1.41 倍就停了，"90 秒达成 150%"没发生。
    //       根因是预算是**有限总量** —— 任何"平均倍率为 1"的曲线都改变不了
    //       "总共只刷 72 只"，而原间隔本身略微超发（spawnInterval 里那个
    //       1.15 系数），所以本来就会提前刷完。
    //
    // v3（现在）：直接算"到这一刻**应该**已经刷掉总预算的百分之几"——
    //   也就是 bossAddProgress()，它是 rate 曲线的归一化积分 P(u)=0.5u+0.5u²。
    //   好处是三件事同时成立：
    //     · P(0)=0、P(1)=1 → 总刷怪量**恰好**等于预算，不提前也不拖尾；
    //     · 刷怪精确铺满整波 → 150% 真的落在 90 秒那一刻；
    //     · 前半慢后半快由 P 的形状直接保证（P 下凸，P'=0.5+u 递增）。
    //   perTick 只作为"单 tick 上限"参与，避免某一帧突然爆一堆。
    // 用 Math.round 而不是 Math.floor：floor 会在波次结束时**差一只**
    //   （实测 71/72）—— 因为最后一帧的 waveElapsed 是 89.97 而不是 90，
    //   P(0.9997) × 72 = 71.95，floor 就是 71，剩下那只永远发不出去。
    //   round 把它补上，且 round 同样单调不减，不会破坏曲线形状。
    //
    // 但**不能只靠 round** —— 那是在赌最后一帧正好落在能进位的位置上。
    // 所以再加一道硬保险：时间已经走完（waveElapsed >= waveDuration）时，
    // 直接把剩余预算全部放出去。否则万一差一只，isSpawnFinished 永远为假，
    // updateSpawning 会一直空转（虽然波次本身是按时间结束的，
    // 但"刷怪没结束"会让下一次 startWave 前的状态不干净）。
    const timeUp = waveElapsed >= this.waveDuration;
    const addTarget = this.bossWaveAdds
      ? (timeUp
        ? this.budgetTotal
        : Math.round(this.budgetTotal * bossAddProgress(waveElapsed, this.waveDuration)))
      : this.budgetUsed + perTick;
    const wanted = this.bossWaveAdds
      ? Math.max(0, Math.min(addTarget - this.budgetUsed, remainingBudget))
      : perTick;

    // Boss 波：Boss 优先出场
    if ((this.waveConfig.boss || this.waveConfig.bosses) && !this.bossSpawned) {
      this._spawnBoss(player);
    }

    let spawned = 0;
    const maxPerTick = 6;
    // 第九十三轮：场上存活敌人硬上限（ENEMY_ALIVE_CAP，不含宝箱怪/死亡待扫）。
    // 被上限挡住的预算**不消耗**（budgetUsed 不加），场上一空继续排队刷 ——
    // "万一玩家发育不好也有活路"：屏幕不糊满、帧率不崩，波次只是清得慢。
    // headroom 把"已预告未落地"的警告也算进去 —— 否则预告堆着还没落地
    // 时 headroom 虚高，一口气挂 6 个新警告，落地时瞬间超上限。
    const headroom = Math.max(0, ENEMY_ALIVE_CAP - this.aliveEnemyCount() - this.spawnWarnings.length);
    while (spawned < Math.min(wanted, maxPerTick)
      && this.budgetUsed < this.budgetTotal
      && spawned < headroom) {
      this._queueSpawnOne(player);   // 第一百七十二轮：先预告，1 秒后落地
      this.budgetUsed++;
      spawned++;
    }

    // 精英按预算比例穿插出现。
    // 体检修复（第九十二轮）：阈值改按"已刷序号"——第 1 只在 40% 进度后、
    // 之后每只推迟 15%，封顶 95%。旧公式 0.25 + 总数×0.15 在总数 ≥5 时
    // 阈值 ≥1.0，精英一只都刷不出来（普通 17-19 波 / 噩梦 13 波起 /
    // 全部无尽普通波全部命中）。
    if (this.elitesToSpawn > 0) {
      const idx = this.elitesTotal - this.elitesToSpawn;   // 已刷出几只
      const threshold = Math.min(0.95, 0.25 + (idx + 1) * 0.15);
      if (this.waveProgress > threshold) {
        this._queueSpawnElite(player);   // 第一百七十二轮：精英同样预告
        this.elitesToSpawn--;
      }
    }

    // 下一次刷怪的间隔。
    //
    // Boss 波用固定的小间隔去**追赶**上面那条进度目标 —— 这样"什么时候刷"
    // 由进度曲线决定，而不是由间隔决定（间隔只影响追赶的粒度）。
    // 间隔取得比原来更细（spawnInterval 的 1/3），是为了让实际释放曲线
    // 更贴合 P(u) 的平滑形状，而不是一堆台阶。
    // 夹一个 0.05 的下限，免得某一帧刷爆。
    this.spawnTimer = Math.max(0.05,
      this.bossWaveAdds ? this.spawnInterval / 3 : this.spawnInterval);
  }

  // =========================================================
  // 伤害结算
  // =========================================================
  /**
   * @param {Player} player
   * @param {WeaponInstance} weapon
   * @param {Enemy} target
   * @param {number} baseDamage
   * @param {object} opts
   */
  dealDamage(player, weapon, target, baseDamage, opts = {}) {
    if (!target || target.dead) return 0;
    // 第一百四十六轮：维吉尔战钩子 —— 格挡领域斩碎远程弹。
    // 用户规格：P1/P2 游荡（不施法）时自动格挡，子弹不扣血直接销毁；
    // 近战不触发（近距离拼刀他挡不住 —— melee 不走这里，走下面的正常伤害）。
    // ---- 第一百八十四轮（用户实测 4AI"连维吉尔 33% 血都打不到"）——
    // 146 轮注释说近战照常，实现却把 melee 也拦了：guarding 期一切输出
    // 归零，游荡占比 60%+ → 有效 DPS 打对折再打折。现在按规格放行近战
    // （weapon 缺省的 AOE 来源照拦）。
    const vf = this.vergilFight;
    if (vf && target === vf.boss && vf.guarding && weapon?.def?.kind !== 'melee') {
      vf.parryAt(opts.fromX ?? target.x, opts.fromY ?? target.y);
      this.fx.damageText(target.x, target.y - target.radius, '格挡', false, '#8fd9f0');
      return { dodged: true, dealt: 0, parried: true };
    }
    const stats = player.stats.values;
    const rt = player.charRt;

    // 暴击
    let critChance = stats.critChance + (weapon?.def?.critBonus ?? 0);
    // 第四十一轮：暴击溢出转换 ——
    // 用户原话："暴击达到100%时，每提升1%暴击自动换成2%暴击伤害提升。
    // 让溢出的暴击有用。"
    // 超过 100% 的部分不再浪费：每 1% 溢出折算 +2% 暴击伤害；
    // 判定概率封顶 100%（必爆）。
    const critOverflow = Math.max(0, critChance - 100);
    const isCrit = this.rng.next() * 100 < Math.min(100, critChance);
    let dmg = baseDamage;
    if (isCrit) {
      dmg *= 1 + (stats.critDamage + critOverflow * 2
        + (weapon?.def?.critDamageBonus ?? 0)) / 100;
    }

    // ---- 第十六轮：角色技能对伤害的三处修正 ----
    //
    // 顺序是**刻意**的，从"最该先算"到"最后算"：
    //
    //  1. 幸运一击（悠怡萌妹）—— 用户说"造成伤害有10%概率造成150%伤害"，
    //     是**替代**这一次伤害的乘算，该在暴击之后、其它加成之前。
    //  2. 距离加成（neon）—— "离敌人越远伤害越高"，是本次命中的环境因素。
    //     必须在**命中时**用当前距离算，不能用开火时的距离：
    //     子弹飞行期间玩家一直在移动，用开火距离等于这条技能白给。
    //  3. 炮台叠层（Neon（原"neon宴会"））—— "该敌人受到的最终伤害 +1%×层数"，
    //     是挂在**目标**身上的易伤，所以放最后（它就是"最终"的意思）。
    if (rt) {
      const lucky = rt.rollLuckyHit?.() ?? 1;
      if (lucky !== 1) dmg *= lucky;

      if (rt.id === 'luna') {
        const dist = Math.hypot(target.x - player.x, target.y - player.y);
        dmg *= rt.distanceMul(dist);
      }

      if (rt.id === 'neon_party' && opts.fromTurret) {
        // 先读当前层数（不写），再叠一层给"下一次"用 ——
        // 反过来的话第一次命中就吃到了 +1%，和用户描述的
        // "命中时使它受到的最终伤害+1%"（先挨打再涨）不符。
        const cur = rt.turretBonusFor(target);
        if (cur > 0) dmg *= 1 + cur;
        rt.onTurretHit(target);
      }
    }

    // 命中目标
    // 第一百零八轮：天气 —— 浓雾 miss（走天气独立随机流，不污染主流）
    // 与雾雨"所有人造成的伤害+25%"（最终乘算）。opts.noWx 豁免
    // （天气自己的流星伤害不吃天气加成，避免循环放大）。
    const wx = this.weather;
    if (wx && !opts.noWx) {
      if (wx.miss > 0 && wx.rng.next() * 100 < wx.miss) {
        this.fx.damageText(target.x, target.y - target.radius, 'MISS', false, '#b8c4e0');
        return { dodged: true, dealt: 0 };
      }
      if (wx.playerOut !== 1) dmg *= wx.playerOut;
    }
    const res = target.takeDamage(dmg, { pierceArmor: opts.pierceArmor });
    // 第一百四十六轮：近战计量 —— 累计近战伤害达玩家 maxHp 10% 触发幻影剑
    //（dealDamage 顶部已处理格挡分支，能走到这里的都是真伤害）
    if (vf && target === vf.boss && weapon?.def?.kind === 'melee') {
      vf.noteMelee(res.dealt ?? 0);
    }

    if (res.dodged) {
      this.fx.damageText(target.x, target.y - target.radius, 'MISS', false, '#9aa3bb');
      return 0;
    }
    // Boss 锁血（登场/转阶段/卡点无敌）：不显示 0 伤害，改成"免疫"提示，
    // 否则玩家会以为是自己伤害不够，而不是机制在挡。
    if (res.blocked) {
      this.fx.damageText(target.x, target.y - target.radius, '免疫', false, '#8fd9f0');
      this.fx.ring(target.x, target.y, target.radius * 1.15, '#8fd9f0', 0.35, { player: true });
      return 0;
    }

    const dealt = res.dealt;
    player.damageDealt += dealt;
    if (weapon) weapon.damageDealt += dealt;

    // 击退
    // 第一百零八轮：台风天全场免疫击退（台风自己的推挤不走这里）
    if (opts.knockback && !target.boss && !(this.weather?.noKnockback)) {
      target.applyKnockback(opts.fromX ?? player.x, opts.fromY ?? player.y, opts.knockback);
    }

    // 伤害数字
    // 第一百三十一轮：过载减伤生效的命中 → 暗灰色数字（正常亮色），
    // 让玩家一眼区分"打中了但太硬"。命中音效/火花特效（下面 else 分支）
    // 刻意保留 —— 用户："不能看起来像子弹穿过去了。要让玩家知道
    // 「我打中了，但它太硬了」"。'
    this.fx.damageText(
      target.x, target.y - target.radius * 0.6, dealt, isCrit,
      res.overloaded ? '#8a8f98' : undefined,
    );
    // 卡点命中：换一个颜色 + 一圈冲击环，明确"打到了但被卡住"
    if (res.gated) {
      this.fx.ring(target.x, target.y, target.radius * 1.25, '#ffc857', 0.4, { player: true });
      this.fx.hit(target.x, target.y, '#ffc857', { player: true });
      if (this.onBossGated) this.onBossGated(target, res.gated);
    } else {
      // ---- 第九轮：命中特效分级（用户要"近战看起来有一些打击感"）----
      // 原来所有命中都只播一个 hit()，轻击和重击看起来一模一样。
      // 现在按"武器类型 + 暴击"决定强度：
      //   近战武器 → 播 impact（放射星芒 + 白核），重击额外带冲击波和碎片
      //   远程武器 → 只播 hit（弹幕本身已经很花了，再加星芒会糊成一团）
      // 这样"近战打击感"和"远程弹幕华丽"两个诉求分别满足，不会互相打架。
      const isMelee = (weapon?.def?.kind ?? weapon?.kind) === 'melee';
      const power = isCrit ? 1 : (isMelee ? 0.62 : 0.34);
      if (isMelee) {
        this.fx.impact(target.x, target.y, power,
          isCrit ? '#ffc857' : '#ffffff', opts.hitAngle ?? null, { player: true });
        // 第一百三十九轮 E：命中溅射碎火星（用户："周围溅射几个碎火星"）
        // —— 轻击 2 粒小的，暴击 4 粒大的（原暴击碎片保留并档）。
        this.fx.shards(target.x, target.y, target.radius * (isCrit ? 1 : 0.55),
          isCrit ? '#ffc857' : '#cfe4ff', isCrit ? 4 : 2, { player: true });
      } else {
        this.fx.hit(target.x, target.y, isCrit ? '#ffc857' : '#ffffff', { player: true });
      }
    }

    // 吸血
    const ls = stats.lifesteal + (weapon?.def?.lifesteal ?? 0);
    if (ls > 0) player.applyLifesteal(ls);

    // ---- 第一百三十四轮：超载芯片 —— 命中让敌人"受到的伤害 +1%"----
    // 最多 30%。挂在目标身上（enemy.debuffTakenPct），永久叠（用户没给
    // 重置条件；波次结束怪清场自然归零）。结算在 enemy.takeDamage 的
    // 护甲之后 —— 与"受到的伤害 +%"的字面一致。已死的怪不用叠。
    if (!res.killed && player.hasItem?.('overload_chip') && target.isEnemy) {
      target.debuffTakenPct = Math.min(30, (target.debuffTakenPct ?? 0) + 1);
    }

    // ---- 第一百三十四轮：幸运猫脚 —— 暴击时 20% 概率再暴击一次 ----
    // 递归一跳（完整结算：能吸血/能击杀/能触发超载芯片），带 noCritAgain
    // 防无限递归。只在真的暴击了才掷骰；宠物/道具内层调用不带猫脚判定。
    if (isCrit && !opts.noCritAgain
        && player.hasItem?.('lucky_cat_paw') && this.rng.next() * 100 < 20) {
      this.dealDamage(player, weapon, target, baseDamage, {
        ...opts, noCritAgain: true, knockback: 0,
      });
    }

    // ---- 第十六轮：小八「龙虾突击」----
    //
    // 用户原话："近战类武器攻击敌人的时候有概率20%触发一次龙虾追击，
    //           造成触发的武器伤害的200%。"
    //
    // 两个实现要点：
    //  1. **走完整的 dealDamage 递归**，而不是直接 `target.hp -= x`。
    //     直接扣血会绕过暴击/吸血/击杀/掉落，玩家会发现"龙虾打死的怪不掉东西"。
    //     递归是安全的：触发条件是 `weapon.kind === 'melee'` 且 opts 里
    //     带了 `noLobster` 标记，所以追击本身不会再触发追击（不会无限递归）。
    //  2. 伤害基数是 `dealt / 本次倍率`…不行 —— 直接用 `dmg`（这一击的实际伤害）
    //     乘 2。用户说的是"触发的武器伤害的200%"，就是这一下的 2 倍。
    if (rt && !opts.noLobster && !res.killed && weapon?.def?.kind === 'melee') {
      if (rt.rollLobster?.('melee')) {
        // 第四十五轮：触发龙虾突击时给小八回 5 滴血 ——
        // 用户原话："然后技能龙虾突击加强：造成龙虾突击时给小八回5滴血。"
        player.heal?.(5);
        this.fx?.damageText?.(player.x, player.y - 24, '+5', false, '#6ee7a8');
        const followDmg = dmg * (rt.lobsterMul ?? 2);
        // 视觉：一道橙红的追击划痕，和普通命中区分开
        this.fx?.thrust?.(player.x, player.y, target.x, target.y, 16, '#ff6b4a');
        this.fx?.damageText?.(target.x, target.y - target.radius * 1.1, '龙虾突击', false, '#ff6b4a');
        this.dealDamage(player, weapon, target, followDmg, {
          ...opts,
          noLobster: true,      // 防递归
          knockback: 0,
        });
      }
    }

    if (res.killed) {
      this._onKill(player, target, weapon);
    }
    return dealt;
  }

  /**
   * 宠物造成的伤害。
   *
   * 故意和玩家伤害分开：
   *  - 不吃玩家的暴击（宠物有自己的节奏，否则会喧宾夺主）
   *  - 不吃吸血（吸血是玩家武器的属性）
   *  - **会**触发正常的击杀掉落与击杀统计
   * 宠物的战斗力本身已经乘过玩家的伤害加成了（见 Pet.damage）。
   */
  petDealDamage(pet, target, baseDamage) {
    if (!target || target.dead) return 0;
    const res = target.takeDamage(baseDamage, {});
    if (res.dodged) {
      this.fx.damageText(target.x, target.y - target.radius, 'MISS', false, '#9aa3bb');
      return 0;
    }
    const dealt = res.dealt;
    this.fx.damageText(target.x, target.y - target.radius * 0.6, dealt, false, '#ffd166');
    this.fx.hit(target.x, target.y, '#ffd166', { player: true });
    if (res.killed) {
      // 击杀归属给玩家（掉落照常），但武器传 null
      this._onKill(this.player, target, null);
    }
    return dealt;
  }

  _onKill(player, enemy, weapon) {
    enemy.dead = true;
    // ---- 宝箱：不走普通击杀流程 ----
    // 宝箱不是"敌人"，不计入击杀数、不掉经验/材料，
    // 只结算它自己预先掷好的奖励（道具或医疗包）。
    if (enemy.isChest) {
      this.fx.explosion(enemy.x, enemy.y, 60, enemy.color);
      this.fx.sparkle(enemy.x, enemy.y, enemy.color, 10);
      this.camera.shake(5, 0.2);
      this._resolveTreasure(player, enemy);
      this.onEnemyKilled?.(enemy);
      return;
    }

    this.totalKills++;
    player.kills++;
    if (weapon) weapon.kills++;
    // 动态难度统计：这个敌人活了多久（秒杀检测用）
    if (enemy._bornWaveT != null) this._killAges.push(this._waveT - enemy._bornWaveT);

    // 掉落
    this._dropLoot(player, enemy);

    // 死亡特效
    if (enemy.boss) {
      this.fx.explosion(enemy.x, enemy.y, 160, enemy.color);
      this.fx.nova(enemy.x, enemy.y, 220, '#ffffff');
      this.camera.shake(22, 0.9);
      // 通知外层（用于猫娘吐槽 / 成就等），World 本身不关心谁在听
      if (typeof this.onBossKilled === 'function') {
        try { this.onBossKilled(enemy); } catch (e) {
          // 第一百六十五轮：不再静默吞 —— 上游一个未定义变量（p is not
          // defined）在这里被无声吞掉，boss 打死了却不结算、不掉红，
          // 查了整整一轮才定位。console.error 留痕，战斗照常不中断。
          console.error('[world] onBossKilled 回调异常（已隔离）:', e);
        }
      }
    } else if (enemy.elite) {
      this.fx.explosion(enemy.x, enemy.y, 80, enemy.color);
      this.camera.shake(8, 0.3);
    } else if (enemy.def?.treasure) {
      // 宝箱怪：金色爆开 + 结算道具奖励
      this.fx.explosion(enemy.x, enemy.y, 90, '#ffc857');
      this.fx.nova(enemy.x, enemy.y, 120, '#ffc857');
      this.camera.shake(7, 0.25);
      this._resolveTreasure(player, enemy);
    } else {
      this.fx.sparkle(enemy.x, enemy.y, enemy.color, 4);
    }

    // 分裂
    if (enemy.def?.behavior === 'splitter' && !enemy.splitDone) {
      enemy.splitDone = true;
      const n = enemy.def.splitCount ?? 3;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * TAU;
        const child = this.spawnEnemyById(enemy.def.splitInto ?? 'slime',
          enemy.x + Math.cos(a) * 26, enemy.y + Math.sin(a) * 26);
        if (child) {
          // 分裂体血量减半，避免滚雪球
          child.maxHp *= 0.5;
          child.hp = child.maxHp;
        }
      }
    }

    this.onEnemyKilled?.(enemy);
  }

  _dropLoot(player, enemy) {
    // 经验
    // 第一百五十六轮：隔壁发免费鸡蛋了？—— 全部掉落 ×2（经验与材料；
    // "不影响本局经济"指不动本函数里的 durationDamp / earlyBonus 等平衡
    // 曲线，掉落在曲线算完之后整体翻倍）
    const lootMul = this.specialWave === 'egg' ? 2 : 1;
    const xp = enemy.xpValue;
    const xpCount = Math.min(8, Math.max(1, Math.round(xp / 2)));
    const perXp = Math.max(1, Math.round(xp / xpCount)) * lootMul;
    for (let i = 0; i < xpCount; i++) {
      const a = this.rng.next() * TAU;
      const d = this.rng.range(4, 22);
      this.pickups.add(new Pickup(PICKUP_TYPE.XP, enemy.x + Math.cos(a) * d, enemy.y + Math.sin(a) * d, perXp));
    }

    // 材料掉落
    // 数值调整史：
    //  - 最初掉率 0.62、普通敌人 1 份 → 前期太穷
    //  - 改成 0.85 + 前 5 波 ×1.35 + 40% 双份 → 中期后期又太富
    //  - 然后：前期保持大方，中后期逐步收回（见 lateDamp）
    //  - 第三轮把波次时长从固定 30s 拉到最高 60s（Boss 关 90s）后，
    //    每波能杀完的敌人变多，收入又翻倍 → 再加一层"时长归一化"
    //    （见 durationDamp），让**每波**收入和原来一致。
    // 第十轮：幸运总效果削弱到 80%，统一走 effectiveLuck()。
    // 注意这里读的是"面板幸运 → 实际生效幸运"，所以面板上玩家
    // 仍然看到自己买的 +25%，只是换算出来的掉率倍率变小了。
    //
    // 第十六轮：改用 player.stats.luckEff —— 它在 effectiveLuck 之上
    // 再乘角色的 luckEffectMul（悠怡萌妹 = 1.2）。
    // 这一处是"幸运总效果 +20%"最核心的消费点：材料掉率。
    // 不改这里的话，萌妹的被动等于没写。
    const luckMul = 1 + player.stats.luckEff / 100;
    const w = this.currentWave;
    const earlyBonus = w < 5 ? 1.35 : w < 10 ? 1.15 : 1;
    // 中后期收紧：11-13 波 ×0.65，14 波以后 ×0.38。
    // （第三轮把时长拉长后，中后期收入仍偏高，这里顺势再收紧一档）
    const lateDamp = w < 10 ? 1 : w < 14 ? 0.65 : 0.38;
    const matChance = enemy.boss || enemy.elite
      ? 1
      : Math.min(1, 0.85 * luckMul * earlyBonus * this.durationDamp);
    if (this.rng.next() < matChance) {
      // 第一百五十六轮：鸡蛋波材料掉落也 ×2（lootMul 定义见上方经验段）
      const value = Math.max(1, Math.round(
        enemy.materialValue * lateDamp * this.durationDamp * (enemy.boss ? 1 : 1),
      )) * (this.specialWave === 'egg' ? 2 : 1);
      // 双份概率：前期有帮助，中后期收回
      const dbl = w < 10 ? 0.4 : 0.2;
      const count = enemy.boss ? 14 : enemy.elite ? 6 : (this.rng.next() < dbl ? 2 : 1);
      for (let i = 0; i < count; i++) {
        const a = this.rng.next() * TAU;
        const d = this.rng.range(4, 30);
        this.pickups.add(new Pickup(PICKUP_TYPE.MATERIAL, enemy.x + Math.cos(a) * d, enemy.y + Math.sin(a) * d, value));
      }
    }

    // 生命掉落（稀有）。
    // 第二十轮：掉落率对半砍（用户原话："把敌人的医疗包掉落率对半砍"）——
    // Boss 1 → 0.5、精英 0.5 → 0.25、小怪 0.03 → 0.015；幸运加成照旧乘在上面。
    // 回血包：变异体**必定**掉（第一百五十三轮，用户："击杀变异体后……
    // 甚至必定掉落一个回血包"），并且不受上限拦 —— 变异本身稀有，
    // 玩家打赢这个"小精英"理应拿满奖励。
    const healChance = enemy.mutant ? 1
      : (enemy.boss ? 0.5 : enemy.elite ? 0.25 : 0.015) * luckMul;
    if (this.rng.next() < healChance) {
      // 第十九轮：地图医疗包上限（用户原话："把地图上的可存在医疗包上限
      // 改到30。如果玩家发育比较好不掉血，地图上最后会全是医疗包。满地图
      // 都是太花眼了。改到30个上限，这样缺血的时候有的吃也不至于太花哨"）。
      // 到上限后新的不再生成；吃掉一个立刻又腾出一个名额。
      const healAlive = this.pickups.items.filter(
        (p) => !p.dead && p.type === PICKUP_TYPE.HEAL,
      ).length;
      if (enemy.mutant || healAlive < HEAL_PICKUP_MAX) {
        // 第二十轮：默认回血翻倍 2 → 4（用户原话："医疗包默认的回血翻倍"）。
        // Boss 的 999 是"回满"哨兵值，不跟着翻倍。
        // 变异体的血包给 6（第一百五十三轮）：比小怪的 4 多一口，
        // 配合"必定掉落"构成击杀奖励的一部分。
        this.pickups.add(new Pickup(PICKUP_TYPE.HEAL, enemy.x, enemy.y,
          enemy.boss ? 999 : enemy.mutant ? 6 : 4));
      }
    }
  }

  // =========================================================
  // 炮台部署
  // =========================================================
  /**
   * 放置一座炮台（由 behavior:'deploy' 的武器调用）。
   * 同一把武器有自己的上限 turretMax；放满后替换最早的一座。
   * 玩家的「炮台上限」属性会额外增加可同时存在的数量。
   */
  deployTurret(player, weapon) {
    const def = weapon.def;
    const bonus = Math.max(0, Math.round(player.stats.values.turretMax ?? 0));
    const max = Math.max(1, (def.turretMax ?? 3) + bonus);

    // 放在玩家身前一点，避免和玩家模型重叠
    const a = player.facing;
    const x = player.x + Math.cos(a) * 26;
    const y = player.y + Math.sin(a) * 26;

    const res = this.turrets.deploy(def, x, y, weapon, player, max);
    this.fx?.ring(x, y, 34, def.color, 0.45);
    return res;
  }

  // =========================================================
  // 生成弹丸
  // =========================================================
  spawnBullet(cfg) {
    // ---- 第一百三十四轮：智慧型魔棒 —— 远程/炮台攻击全部追踪 ----
    // 玩家武器与炮台的弹都从这里过（宠物弹不经过，见 pet.js 自己的
    // 生成路径）；已是 homing 的保持原样。近战 sweep/thrust 不发弹，
    // 天然不受影响。追踪强度 3 = 与星杖系追踪武器一致。
    const p = cfg.ownerPlayer ?? this.player;
    if (p?.hasItem?.('smart_wand') && !cfg.fromPet
        && cfg.behavior && cfg.behavior !== 'homing'
        && cfg.behavior !== 'orbit' && cfg.behavior !== 'spin') {
      cfg = { ...cfg, behavior: 'homing', homing: Math.max(cfg.homing ?? 0, 3) };
    }
    const b = new Bullet(cfg);
    // 第十六轮：显式记下开火者。
    // 默认回落到 this.player —— 玩家武器和炮台子弹都该归到玩家身上，
    // 而 world.player 就是当前这一局的玩家（重试本波会整个重建 World）。
    b.ownerPlayer = cfg.ownerPlayer ?? this.player;
    this.bullets.add(b);
    return b;
  }

  spawnEnemyBullet(cfg) {
    const b = new EnemyBullet(cfg);
    // ---- 第一百五十六轮：特殊波「内部混乱」----
    // 混乱波里所有敌方弹幕带阵营标记：除了打玩家，命中**近战怪**也会结算
    // 伤害（远程怪 vs 近战怪的互相伤害，见 update 的敌人子弹循环）。
    if (this.specialWave === 'chaos') b.hostileToEnemies = true;
    this.enemyBullets.add(b);
    return b;
  }

  // =========================================================
  // 范围查询（供武器使用）
  // =========================================================
  queryArc(x, y, facing, halfAngle, radius) {
    const out = [];
    const candidates = this.grid.query(x, y, radius, []);
    for (const e of candidates) {
      if (!e.isEnemy || e.dead) continue;
      const a = angleTo(x, y, e.x, e.y);
      let diff = (a - facing) % TAU;
      if (diff > Math.PI) diff -= TAU;
      if (diff < -Math.PI) diff += TAU;
      if (Math.abs(diff) <= halfAngle) out.push(e);
    }
    return out;
  }

  queryCapsule(x1, y1, x2, y2, radius) {
    const out = [];
    const seen = new Set();
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    const steps = Math.max(1, Math.ceil(len / Math.max(8, radius)));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const px = x1 + dx * t;
      const py = y1 + dy * t;
      const hits = this.grid.query(px, py, radius, []);
      for (const h of hits) {
        if (!h.isEnemy || h.dead || seen.has(h.id)) continue;
        seen.add(h.id);
        out.push(h);
      }
    }
    return out;
  }

  // =========================================================
  // 每帧更新
  // =========================================================
  update(dt, player, waveElapsed) {
    this.player = player;
    // 动态难度：本波计时（击杀存活时长/超时判定的基准）
    this._waveT += (dt || 0);

    // Boss 阶段需要知道"剩余时间比例"。main.js 负责计时，
    // 这里把 waveElapsed 换算成比例注入，Boss 就能同时用血量/时间双触发。
    const dur = this.waveDuration || 1;
    this.bossTimeRatio = clamp(1 - (waveElapsed || 0) / dur, 0, 1);

    // ---- 重建空间网格 ----
    this.grid.clear();
    for (const e of this.enemies.items) {
      if (!e.dead) this.grid.insert(e);
    }

    // ---- 刷怪 ----
    // 第四十九轮：芙兰朵露战期间 noSpawn=true —— 波次系统已经结束，
    // 场上只允许芙兰本尊，普通刷怪/宝箱/宝箱怪全部停掉。
    if (!this.noSpawn) {
      this.updateSpawning(dt, player, waveElapsed);
      // ---- 宝箱 / 宝箱怪 ----
      this._updateTreasureSpawning(dt, player);
    }

    // ---- 敌人 ----
    // 第一百七十轮：仇恨 = 距离最近的活玩家（倒地的不算），谁近打谁
    // 第一百八十二轮：天彗龙眩晕 —— enemyFrozen 时敌人个体 dt=0（原地
    // 僵住、不攻击不移动），但弹幕/掉落/拾取物照常飞行（用户："可以只是
    // 玩家和怪动不了吗"—— 是眩晕不是时停）。
    for (const e of this.enemies.items) {
      if (e.dead) continue;
      e.update(this.enemyFrozen ? 0 : dt, this.nearestPlayerTo(e.x, e.y), this);
    }

    // ---- 玩家子弹 ----
    for (const b of this.bullets.items) {
      if (b.dead) continue;
      b.update(dt, this);
      if (!b.dead) b.checkEnemyHit(this);
    }

    // ---- 炮台 ----
    this.turrets.update(dt, player, this);

    // ---- 宠物（小小猫娘）----
    if (this.pet && !this.pet.dead) {
      this.pet.update(dt, player, this);
      this.pet.clampToWorld(this);
    }

    // ---- 敌人子弹 ----
    // 第一百七十轮：传玩家数组，弹逐个判定圆碰撞、命中谁打谁
    const livePlayers = this.players();
    for (const b of this.enemyBullets.items) {
      if (b.dead) continue;
      b.update(dt, this, livePlayers);
      // ---- 第一百五十六轮：特殊波「内部混乱」----
      // 带阵营标记的敌方弹幕会打**近战怪**（远程怪朝近战怪放的弹）。
      // 变异怪不参与内讧（既不当目标也不被波及），Boss 也不吃。
      if (b.hostileToEnemies && !b.dead) {
        for (const e of this.enemies.items) {
          if (e.dead || !e.melee || e.mutant || e.boss || e.isChest) continue;
          const dx = e.x - b.x; const dy = e.y - b.y;
          const rr = e.radius + (b.radius ?? 6);
          if (dx * dx + dy * dy <= rr * rr) {
            e.takeDamage(b.damage ?? 4, { pierceArmor: true });
            b.dead = true;
            this.fx?.nova?.(b.x, b.y, 12, '#a8b6ff');
            break;
          }
        }
      }
    }

    // ---- 拾取物 ----
    // 第一百七十轮：归属 = 最近的活玩家（金币独立谁吃算谁）；
    // 经验共享在 pickup 内部处理（吸收时全队加）
    for (const p of this.pickups.items) {
      if (p.dead) continue;
      p.update(dt, this, this.nearestPlayerTo(p.x, p.y));
    }

    // ---- 特效 ----
    this.fx.update(dt);

    // ---- 清理 ----
    this.enemies.sweep();
    this.bullets.sweep();
    this.enemyBullets.sweep();
    this.pickups.sweep();
  }

  /** 波次结束时清场：把残余敌人清掉（Brotato 的做法） */
  endWaveCleanup(player, opts = {}) {
    let cleaned = 0;
    let bonus = 0;
    // 第四十八轮：Boss 关到时判过关（用户要求"无法获得boss击杀奖励"）——
    // removeBosses=true 时活着的 Boss 直接无声移走：不掉材料、不掉经验、
    // 不触发 onBossKilled（那是击杀奖励的入口）。
    const removeBosses = !!opts.removeBosses;
    // 清场奖励的折算比例：前期给一半（帮玩家攒钱），
    // 中后期逐步降到 30% —— 否则后期一波清场就能白拿上百材料。
    const w = this.currentWave;
    const ratio = w < 8 ? 0.5 : w < 14 ? 0.35 : 0.22;
    // 时长归一化：波次变长后残余敌人也变多，不清掉这层收入同样会翻倍
    const damp = this.durationDamp ?? 1;
    for (const e of this.enemies.items) {
      if (e.dead) continue;
      if (e.boss) {
        // 只清普通敌人，Boss 保留到被击杀（到时放行时例外，见上）
        if (!removeBosses) continue;
        e.dead = true;
        continue;
      }
      // 宝箱：不折算成材料，直接消失（没打掉就没奖励，这是合理的代价）
      if (e.isChest) { e.dead = true; continue; }
      e.dead = true;
      cleaned++;
      const v = Math.max(1, Math.round(e.materialValue * ratio * damp));
      this.pickups.add(new Pickup(PICKUP_TYPE.MATERIAL, e.x, e.y, v));
      bonus += v;
    }
    this.enemies.sweep();
    this.bullets.clear();
    this.enemyBullets.clear();
    // 炮台随波次清空：每波重新布阵，避免跨波无限累积
    this.turrets.clear();
    this.lastCleanupBonus = bonus;
    return cleaned;
  }

  clearAll() {
    this.enemies.clear();
    this.bullets.clear();
    this.enemyBullets.clear();
    this.turrets.clear();
    this.pickups.clear();
    this.fx.clear();
    this.grid.clear();
  }

  /** 供 UI 显示：场上敌人数量 */
  get enemyCount() { return this.enemies.length; }
}