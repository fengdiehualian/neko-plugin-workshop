// ============================================================
// 第四十九轮：隐藏挑战 Boss —— 芙兰朵露·斯卡蕾特（东方红魔乡 Extra）
//
// 用户原话（需求全文见本轮提交信息）：噩梦难度 + 打败 20 波 Boss 总重开
// 次数 ≤ 3 才能见到；20 波通关后不进商店/结算，演出序列（渐黑 3s → 静默
// 3s → 小小猫娘警告 → 震动 → 右上角符卡名 → U.N. Owen → 弹幕生存 30s，
// 她本人不出场）→ 芙兰登场正式弹幕战（50 万血，4 阶段 × 30s，每阶段
// 25% 血或到时切换，打败 3 张进 Extra Phantom：无敌抱腿坐姿 60s 存活战）
// → 结算三分支（死亡无奖励 / 3 倍 Boss 奖励 / 3 倍奖励 + 莱万汀）。
//
// 弹幕几何参考 touhou-flandre/（隔壁会话的反编译规格）：
//   emit() 数学照抄，参考系换成"以地图中心偏上为锚的竞技场"（方案 A/B 折中：
//   不锁玩家视角，但把玩家软性圈在竞技场里），子弹预算砍到 ≤750 发，
//   瞄准加 0.6 倍提前量，计时全部用 dt 累加（不用帧数取模）。
// ============================================================

import { Enemy } from '../entities/enemy.js';
import { FLANDRE_HIT_INVULN } from '../entities/player.js';
import { getEnemy } from '../data/enemies.js';
import { TAU, clamp } from '../core/math.js';
import { audio } from './audio.js';
import { MOBILE } from '../core/mobile.js';

// ---- 数值（用户逐条指定）----
// 第八十六轮：总血量 50万 → 80万 —— 用户："把芙兰的总血量改到80万。
// 然后每打掉20%血锁血切换符卡，前80%血对应4张卡，最后20%血全程锁血
// 进EP模式的70秒演出。防止玩家dps过高直接把芙兰秒了进不去EP。输出
// 不够的话就正常30秒一张卡，累计触发4张卡之后还是正常进EP。然后
// 80万血，最后16万全程锁血。相当于打芙兰64万血。"
export const FLANDRE_MAX_HP = 800000;      // "血量为80万"（第八十六轮 50万 → 80万）
export const PROLOGUE_CARD_TIME = 30;     // 秘弹生存 30 秒（原版 90 秒）
export const CARD_TIME = 30;              // 每张 EX 符卡 30 秒
export const CARD_HP_FRAC = 0.2;          // 第八十六轮：每张卡 20%（16 万）——
// 打掉 20% 锁血切下一张（30 秒内打不掉就超时切，两条路先到先切）；
// 前 4 张共 80%，最后 20%（16 万）是 EP 的演出血量，全程锁死。
export const CARDS_TOTAL = 4;             // 一共随机 4 张
export const CARDS_TO_EXTRA = 3;          // （第六十五轮已退役：现在按
// cardsDone >= CARDS_TOTAL 进 EP，见 _nextCard；数值仅为兼容旧测试保留）
export const EXTRA_TIME = 70;             // 第六十一轮：20 + 20 + 30 = 70 秒
export const EXTRA_CARD_TIME = 20;        // EP 前两张各 20 秒
export const EXTRA_HEART_TIME = 30;       // 第六十一轮：最后的「弹幕的心脏」30 秒
export const SWITCH_LOCK = 3;             // 切符卡：清全屏弹幕 + 锁血 3 秒
export const HIT_FRAC = 0.22;             // 每发命中扣 22% 最大生命的真实伤害
// （第五十六轮：30% → 24%；第七十四轮：24% → 22% —— 用户："考虑到猫娘
// 本身是个桌宠，用户群体大概都不是弹幕游戏受众，我们再削弱一下芙兰的
// 伤害。从24%真伤改到22%。这样差不多又可以多抗一发。"）
export const PURGE_RADIUS = 180;          // 消弹光圈半径（第七十六轮 130 → +50：
// 用户："我们现在陆续加强了很多芙兰的符卡威力之后这个消弹半径不够用了，
// 遇到必吃的弹幕用了消弹也无法转移到安全地带就被再次压死了。"）
export const PURGE_CD = 15;               // 消弹冷却 15 秒
export const CULL_MARGIN = 140;               // 199 轮：弹飞出场地外多少 px 回收（见 _cullOutside）
// 同屏弹幕预算。
// 第一百九十九轮（用户："解除 800 的限制……你可以测试一下电脑极限，比如改到 1000、1100 会卡吗？只要不是掉帧严重都可以改……"）：
// 真机压测（芙兰战真实场景、全质量 LOD2、批绘制；机器当时还被别的程序占着约 59% CPU，数字偏保守）——
//   弹数   渲染CPU   帧间隔(最好/中位)
//    800    5.4ms    14.1 / 15.1ms
//   1000    6.6ms    16.5 / 18.2ms
//   1200    8.1ms    18.1 / 20.7ms        ← 仍接近 60fps 的最大值，取为桌面上限
//   1500    9.6ms    19.9 / 22.8ms
//   2000   10.1ms    21.1 / 26.2ms        （中位已 38fps）
//   3000   11.6ms    25.2 / 31.2ms        （~32fps，明显掉帧）
// 渲染成本亚线性（批绘制），掉帧主要来自 GPU 填充与弹的更新/碰撞。移动端保持原来的 750。
// 实际生效值是 FlandreFight._capNow：持续卡顿时自动降到 BULLET_CAP_MIN、恢复后升回（见 _adaptCap）。
export const BULLET_CAP = MOBILE ? 750 : 1200;
export const BULLET_CAP_MIN = MOBILE ? 500 : 800;
export const LEAD = 0.6;                  // 提前量系数（VS-HANDOFF 4.2 推荐 0.6）

// 竞技场：地图中心偏上方（用户原话"或者说地图中心偏上方"）
// 第五十五轮：左右各扩 100（用户："可移动区域过小了，左右各扩大100像素
// 点空气墙位置"）。420 → 520。
import { ARENA as _ARENA } from './flandre-consts.js';
export const ARENA = _ARENA;   // 185 轮：数值搬到 flandre-consts.js（避免与 flandre-spells.js 循环依赖），此处原样再导出

const COLORS = {
  red: '#ff4060', dark: '#c81e3c', white: '#fff2f4',
  gold: '#ffd166', pink: '#ff7f9f', deep: '#8f1029',
  // 第一百零四轮：495 波纹 3 色循环用的亮浅蓝（用户："加一个亮浅蓝色"）
  sky: '#5fd4ff',
};
// 第七十五轮：补到 7 色 —— Fragile Wing 的 14 槽贴图循环步长 2 → 7 种
// 色相（用户："原来是各种颜色的彩色弹幕"）；序章彩虹弹多一色不受影响。
const RAINBOW = ['#ff4060', '#ff9f43', '#ffe066', '#7bed9f', '#4cd7d0', '#70a1ff', '#c56cf0'];

// 序章演出时间轴（秒）
const FADE_IN = 3;        // 背景 3 秒内慢慢变黑
const SILENCE_END = 6;    // 再静默 3 秒（3+3）
const SHAKE_AT = 6;       // 静默结束 → 小小猫娘警告 + 屏幕轻微晃动 2.5 秒
const CARD_AT = 8.5;      // 震动结束 → 右上角符卡名 + BGM + 第一颗弹幕
export const PROLOGUE_END = CARD_AT + PROLOGUE_CARD_TIME;   // 38.5 秒芙兰登场

// ---- EX 难度符卡池（原版 10 张减掉序章秘弹 = 9 张随机；新典 3 张不进池）----
export const CARD_POOL = [
  'cranberry', 'laevateinn', 'fourkind', 'kagome', 'maze',
  'starbow', 'catadioptric', 'clock', 'ripples',
];

// ---- 第五十六轮：Extra Phantom 专属 3 张（新典为芙兰追加，规格书 3.3 /
//      6.11~6.13：咒弹 Fragile Wing 131 / 咒弹 伏行之血痕 132 / 「弹幕的心脏」
//      133）—— EP 阶段只放这三张，顺序释放，不再从随机池里抽。
export const EXTRA_SPELLS = ['fragile', 'bloodstain', 'heart'];

export const CARD_INFO = {
  prologue: { name: '秘弹「之后就一个人都没有了吗？」' },
  cranberry: { name: '禁忌「Cranberry Trap」' },
  laevateinn: { name: '禁忌「Lævateinn」' },
  fourkind: { name: '禁忌「Four of a Kind」' },
  kagome: { name: '禁忌「笼中鸟 Kagome Kagome」' },
  maze: { name: '禁忌「恋之迷宫 Maze of Love」' },
  starbow: { name: '禁弹「Starbow Break」' },
  catadioptric: { name: '禁弹「Catadioptric」' },
  clock: { name: '禁弹「过后一时辰的时钟」' },
  ripples: { name: '禁弹「495 年的波纹」' },
  // 第五十六轮：Extra Phantom 专属（新典追加）
  fragile: { name: '咒弹「Fragile Wing」' },
  bloodstain: { name: '咒弹「伏行之血痕」' },
  heart: { name: '「弹幕的心脏」' },
};

/**
 * 解锁判定：噩梦难度通关 20 波即见到她。
 *
 * 第一百八十五轮（用户实测："噩梦难度 0 重开没有遇到芙兰……把噩梦难度遇到芙兰的前置条件删了吧，感觉是遇到了
 * 什么 bug"）：删除「重开次数 ≤ 3」前置。排查发现那个 bug 就是这个前置本身 ——
 * 它读的 meta.manualRetries 从来没有在新局清零（meta.retries 有 5 处清零，唯独它是跨局永久累计并落盘），
 * 手动重试几次后永远 >3，芙兰就再也不出现。第二参数 retries 保留只为兼容旧调用，现在被忽略。
 */
export function flandreUnlockOk(difficultyId, _retries) {
  return difficultyId === 'nightmare';
}

// 芙兰本尊的敌人定义在 data/enemies.js（'flandre'）——
// 那里同时登记了 4 个姿势贴图位（flandre_sit/sit2/sit3/laevateinn）。
// 本体贴图 = 172 号切片；Extra Phantom 抱腿循环 = 202/205/208；
// Lævateinn 符卡攻击姿势 = 192（用户亲自核对的编号表）。
const SIT_FRAMES = ['flandre_sit', 'flandre_sit2', 'flandre_sit3'];
// 第五十轮：172~175 是她不移动的常态动画（只有翅膀在动）——用户原话：
// "把芙兰一动不动变成172-175慢慢循环，让她能动一下。"
const IDLE_FRAMES = ['flandre', 'flandre_idle2', 'flandre_idle3', 'flandre_idle4'];

// ============================================================
// 第五十二轮（用户反馈 3/4）：
//   STAGE_DY   —— 她的舞台点在地图中心上方多少像素。用户："位置在往上
//                 一点点，还是有点低。" 190 → 270。
//   TINT_CARDS —— 登场（正式符卡战）背景转成的暗红（3 秒过渡）。
//   TINT_EP    —— Extra Phantom 的背景红：比正式战更鲜艳，但不刺眼。
//   EP_AT_TIMEOUT —— 第六十五轮起为正式机制：120 秒到点（4 张卡不管打成
//                 什么样）必进 Extra Phantom。用户："只要玩家在120秒内
//                 没被打死就能见到这三张卡……120秒内打败4张卡提前见，
//                 打不过就120秒到时见。"
// ============================================================
export const STAGE_DY = 270;
export const TINT_CARDS = '#3a0a12';
export const TINT_EP = '#7d1730';
export const EP_AT_TIMEOUT = true;

// ============================================================
// 符卡脚本：每个 tick(sp, dt, f) 自己管计时。
// sp = { id, name, t, dur, st(脚本私有状态), speedMul }
// f  = 战斗控制器（emit/fan/ring/aim/玩家引用等）
// 全部走 dt 累加，不用帧数取模（VS-HANDOFF 4.3）。
// ============================================================
const SPELLS = {
  // 序章：秘弹「And Then Will There Be None?」—— 原版这张卡她本人不在场，
  // 四条旋转弹臂组成的"十字"扫场 + 中心环。我们保留这个签名，密度 ÷8。
  prologue: {
    tick(sp, dt, f) {
      const st = sp.st;
      // 第六十三轮：10 秒一个变化，共 3 个阶段 ——
      // 用户："给开局的秘弹「之后就一个人都没有了吗？」改成10秒一个变化，
      // 做3个变化，现在的2个变化差距很小，没啥威胁。"
      //   阶段1（0-10s）：4 臂十字基准态
      //   阶段2（10-20s）：十字整体**反转**旋转方向 + 明显加速收紧（原版的
      //     "变化"从这里提前到 10s 整，差距拉开）
      //   阶段3（20-30s）：质变 —— 4 臂十字裂成**双向双重十字**（8 臂，
      //     内外圈反向旋转）+ 中心环变双环
      const phase = sp.t >= 20 ? 3 : sp.t >= 10 ? 2 : 1;
      if (st.phase !== phase) {
        st.phase = phase;
        if (phase === 2) st.dir = -(st.dir ?? 1);   // 反转在这时发生
      }
      const dir = st.dir ?? 1;
      const cfg = phase === 1
        ? { up: 1, step: 0.085, ring: 2.2, ringSpeed: 120 }
        : phase === 2
          ? { up: 1.45, step: 0.058, ring: 1.5, ringSpeed: 150 }
          : { up: 1.85, step: 0.05, ring: 1.0, ringSpeed: 175 };
      st.rot = (st.rot ?? 0) + dt * 0.85 * cfg.up * dir;
      st.acc = (st.acc ?? 0) + dt;
      while (st.acc >= cfg.step) {
        st.acc -= cfg.step;
        const arms = phase === 3 ? 8 : 4;
        for (let arm = 0; arm < arms; arm++) {
          // 阶段3：内圈 4 臂顺 rot 转、外圈 4 臂**反向**转且错开 22.5°
          const inner = arm < 4;
          const a = inner
            ? st.rot + (arm % 4) * TAU / 4
            : -st.rot * 1.2 + (arm % 4) * TAU / 4 + Math.PI / 8;
          const rad = inner ? 360 : 300;
          const px = f.ax + Math.cos(a) * rad;
          const py = f.ay + Math.sin(a) * rad;
          f.emit({ x: px, y: py, angle: a + Math.PI / 2,
            speed: 150 * cfg.up * (inner ? 1 : 0.85),
            radius: 6, color: phase === 3 && !inner ? COLORS.gold
              : (arm % 2 ? COLORS.red : COLORS.white) });
        }
      }
      st.ringAcc = (st.ringAcc ?? 0) + dt;
      if (st.ringAcc >= cfg.ring) {
        st.ringAcc -= cfg.ring;
        const base = f.rnd() * TAU;
        if (phase === 3) {
          // 双环：两个相反方向的圆环同时炸开
          f.ring(f.ax, f.ay, 22, base, cfg.ringSpeed, COLORS.dark, 5);
          f.ring(f.ax, f.ay, 22, base + Math.PI, cfg.ringSpeed * 1.15, COLORS.red, 5);
        } else {
          f.ring(f.ax, f.ay, 22, base, cfg.ringSpeed, COLORS.dark, 5);
        }
      }
    },
  },

  // 禁忌「Cranberry Trap」：瞬移 + 每次落点留下两个旋转副发射器（双臂螺旋）
  cranberry: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.tp = (st.tp ?? 0) + dt;
      if (st.tp >= 4 || !st.emitters) {
        st.tp = 0;
        st.emitters = [];
        f.teleportBoss();
        for (const side of [-1, 1]) {
          st.emitters.push({
            x: f.bx + side * 70, y: f.by + f.rnd() * 80 - 40,
            rot: f.rnd() * TAU, dir: side, fire: 0, ring: 0, age: 0,
          });
        }
      }
      for (const em of st.emitters) {
        em.age += dt; em.rot += dt * 2.4 * em.dir;
        em.fire += dt;
        if (em.fire >= 0.12) {
          em.fire -= 0.12;
          f.emit({ x: em.x, y: em.y, angle: em.rot, speed: 180, radius: 5, color: COLORS.red });
          f.emit({ x: em.x, y: em.y, angle: em.rot + Math.PI, speed: 180, radius: 5, color: COLORS.white });
        }
        em.ring += dt;
        if (em.ring >= 2.4) {
          em.ring -= 2.4;
          f.ring(em.x, em.y, 14, em.rot, 150, COLORS.gold, 5);
        }
      }
      st.emitters = st.emitters.filter((em) => em.age < 4.2);
      // 芙兰本体补一组慢速瞄准弹，防止玩家站桩
      st.aimAcc = (st.aimAcc ?? 0) + dt;
      if (st.aimAcc >= 1.4) {
        st.aimAcc -= 1.4;
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 3, 30, 190, COLORS.pink, 6);
      }
    },
  },

  // 禁忌「Lævateinn」：旋转火焰魔剑 —— 第七十二轮按原作 §6.2（Sub45/46/47）
  // 重做。用户："我们的莱瓦汀符卡和原来的芙兰符卡差距极大，我们的卡芙兰
  // 一直在发射旋转超密集弹幕，原版不是这样，看一下解包和还原度有没有
  // 问题。" + "激光音效特别吵，设置cd"。
  //   原作结构：芙兰在屏幕上**大幅移动**（move_position_interp_decelerate
  //   是 BOSS 最常用），每段移动配**一条旋转的激光**（剑）：laser_create
  //   长 388→420 渐变、宽 24，Sub47 laser_rotate 旋转；子弹只有"弹 2"
  //   —— 弹幕是配角。旧版（第六十二轮）是双向射线每 0.05s × 5 发补弹
  //   ≈ 200 发/秒的弹链，观感完全不对。
  //   新版：每 ~2.5s 一段减速滑步（场内留边随机落点），出鞘一条旋转激光
  //   （长 388/420 × 场地比例 ≈ 930/1010，宽 26，0.9 rad/s 慢扫，扫中
  //   玩家按接触伤害结算，玩家无敌帧天然限频）；激光音只在出鞘时一声
  //   （audio SE_MIN_GAP lazer00=1.2s 兜底）；弹幕降为辅助：1.6s 一轮
  //   5 向瞄准扇 + 2.9s 一轮 14 发环。
  laevateinn: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.lasers = st.lasers ?? [];
      // —— 移动段：大幅移动（原作 BOSS 常用的减速插值移动）
      if (!st.move) {
        st.move = {
          t: 0, dur: 2.0, pause: 0,
          fx: f.bx, fy: f.by,
          tx: f.ax + (f.rnd() * 2 - 1) * (ARENA.hw - 140),
          // 第八十二轮：只在上半屏移动 —— 用户："让她无法达到屏幕下方，
          // 只能在上半屏移动，不然贴着脸莱万汀太难了。"
          // ty ∈ [上缘+140, 中线-120]，带激光的俯冲永远够不到下半屏。
          ty: f.ay - ARENA.hh + 140 + f.rnd() * (ARENA.hh - 260),
        };
        // 每段移动配一条旋转激光（laser_create + Sub47 laser_rotate）
        st.lasers.push({
          x: f.bx, y: f.by,
          angle: f.rnd() * TAU,
          angVel: (f.rnd() > 0.5 ? 1 : -1) * 0.9,
          len: 1010, width: 26,       // 原作 388→420 长 / 24 宽 × 场地比例
          age: 0, life: 2.6, fade: 1,
        });
        audio.play('flandreLaser');    // 出鞘一声（SE_MIN_GAP lazer00=1.2s 兜底）
      }
      const mv = st.move;
      mv.t += dt;
      const k = Math.min(1, mv.t / mv.dur);
      const e = 1 - (1 - k) * (1 - k);            // 减速插值（快速出、缓停）
      if (f.boss) {
        f.boss.x = mv.fx + (mv.tx - mv.fx) * e;
        f.boss.y = mv.fy + (mv.ty - mv.fy) * e;
      }
      if (k >= 1) {
        mv.pause += dt;
        if (mv.pause >= 0.5) st.move = null;      // 短暂停顿 → 下一段
      }
      // —— 激光：旋转 + 渐隐 + 扫中判定（玩家无敌帧天然限频）
      const p = f.main.player;
      for (const L of st.lasers) {
        L.age += dt; L.life -= dt; L.angle += L.angVel * dt;
        if (L.life < 0.5) L.fade = Math.max(0, L.life / 0.5);
        if (!p.dead && p.invuln <= 0) {
          const dx = p.x - L.x, dy = p.y - L.y;
          const proj = dx * Math.cos(L.angle) + dy * Math.sin(L.angle);
          if (proj > 0 && proj < L.len) {
            const dist = Math.abs(-dx * Math.sin(L.angle) + dy * Math.cos(L.angle));
            // 第九十七轮：激光扫中也按芙兰战口径给 1s 无敌帧
            //（普通关卡的 0.8/0.6 分档不适用于她的战斗）
            if (dist < L.width + (p.radius ?? 10)) {
              // 第一百三十轮：激光扫中从固定 10 点真伤改为 16% 最大血量真伤。
              // 用户："挑战模式的悠怡只有 24 血，被扫一次扣 10 相当于将近
              // 40% 真伤了"——按血量百分比后 24 血 ≈ 4 点（弹幕 22% 与激光
              // 16% 拉开梯度），高血量角色也不会被激光扫一下白给太多。
              // 口径与 flandreHit 一致：max(1, round(maxHp × frac))。
              const dmg = Math.max(1, Math.round(p.stats.get('maxHp') * 0.16));
              p.takeDamage(dmg, p.x, p.y, { invuln: FLANDRE_HIT_INVULN, noTaken: true });
            }
          }
        }
      }
      st.lasers = st.lasers.filter((L) => L.life > 0);
      // —— 第七十七轮：激光扫过留下密集弹幕 ——
      // 用户："原版激光扫过之后会留下密集弹幕，我们的扫过很干净，所以
      // 这张卡现在特别简单。" 沿光束每 0.15s 在 5 个距离点（间距 175px）
      // 向两侧法向喷慢弹（speed 60，±0.3rad 散布）—— 扫过的扇形区域被
      // 慢弹带填满，玩家躲激光时还要记住刚才扫过的地方不能站。
      st.spray = (st.spray ?? 0) + dt;
      if (st.spray >= 0.15) {
        st.spray -= 0.15;
        for (const L of st.lasers) {
          for (let k = 1; k <= 5; k++) {
            const d = k * 175;
            if (d > L.len * 0.95) break;
            const side = f.rnd() < 0.5 ? 1 : -1;
            f.emit({
              x: L.x + Math.cos(L.angle) * d,
              y: L.y + Math.sin(L.angle) * d,
              angle: L.angle + side * (Math.PI / 2) + (f.rnd() * 2 - 1) * 0.3,
              speed: 60,
              color: f.rnd() < 0.5 ? COLORS.red : COLORS.pink,
              radius: 4.5, range: 420,
              shape: 'longball',   // 第九十一轮：激光扫后弹幕 = 椭圆蛋（用户指定）
            });
          }
        }
      }
      // —— 弹幕辅助（原作"弹 2"，压力很低）
      st.fan = (st.fan ?? 0) + dt;
      if (st.fan >= 1.6) {
        st.fan -= 1.6;
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 5, 22, 170, COLORS.red, 5.5);
      }
      st.ring2 = (st.ring2 ?? 0) + dt;
      if (st.ring2 >= 2.9) {
        st.ring2 -= 2.9;
        f.ring(f.bx, f.by, 14, f.rnd() * TAU, 130, COLORS.gold, 5);
      }
    },
  },

  // 禁忌「Four of a Kind」：4 个分身绕芙兰旋转，各自朝玩家打扇形。
  // 第七十三轮（用户："让4个分身芙兰变成单纯的弹幕发射器，不算怪，不算
  // boss，没有血，她们就是4个动画发射器。然后本体芙兰这张卡期间隐身，
  // 但是hitbox覆盖4个分身芙兰。因为我们是全程自动攻击，所以会自动锁定
  // 这个'玩家看不到，但是系统看得到'的芙兰本体攻击，然后伤害也会只结算
  // 1次而不是4个分身吃4倍。"）—— 推翻第七十一轮"本体可见可受击"：
  //   · 分身 = 纯视觉动画发射器（不再进敌人数组，无血无判定无 AI），
  //     由渲染层按敌人同款贴图画（_drawFlandreClones）；
  //   · 本体隐身（flandreHidden），hitbox 半径扩到 轨道半径+本体半径，
  //     自动攻击锁定/命中这个"看不见的大圆" → 伤害天然只结算 1 次；
  //   · 大 hitbox 不做身体接触伤害（damage 临时归零，卡结束恢复）——
  //     隐形的身体碰不得。
  fourkind: {
    tick(sp, dt, f) {
      const st = sp.st;
      // 开卡：本体隐身 + 大 hitbox；4 个动画发射器就位
      if (!st.clones) {
        st.clones = [];
        if (f.boss) {
          f.boss.flandreHidden = true;              // 渲染层跳过 → 隐身
          f._bossRadiusSave = f.boss.radius;
          f.boss.radius = 130 + f.boss.radius;      // hitbox 覆盖 4 个分身轨道
          f._bossDamageSave = f.boss.damage;
          f.boss.damage = 0;                        // 隐形身体不做接触伤害
        }
        for (let i = 0; i < 4; i++) {
          st.clones.push({
            x: f.bx, y: f.by, spriteId: IDLE_FRAMES[0],
            radius: f._bossRadiusSave ?? 24,           // 贴图尺寸 = 本体同款
          });
        }
      }
      st.rot = (st.rot ?? 0) + dt * 0.55;
      st.fire = (st.fire ?? 0) + dt;
      st.ring = (st.ring ?? 0) + dt;
      // 分身站位 + 立绘动画（纯视觉发射器，渲染层 _drawFlandreClones 画）
      const frames = IDLE_FRAMES[Math.floor(f.t / 0.35) % IDLE_FRAMES.length];
      for (let i = 0; i < 4; i++) {
        const a = st.rot + (i * TAU) / 4;
        const c = st.clones[i];
        c.x = f.bx + Math.cos(a) * 130;
        c.y = f.by + Math.sin(a) * 130;
        c.spriteId = frames;
      }
      if (st.fire >= 0.6) {
        st.fire -= 0.6;
        for (let i = 0; i < 4; i++) {
          const a = st.rot + (i * TAU) / 4;
          const px = f.bx + Math.cos(a) * 130, py = f.by + Math.sin(a) * 130;
          // 第九十一轮：四重存在全弹 = 椭圆蛋（颜色保持原红/白，用户指定）
          f.fan(px, py, f.aim(px, py), 3, 24, 210, i % 2 ? COLORS.red : COLORS.white, 5.5, 'longball');
        }
      }
      if (st.ring >= 3.2) {
        st.ring -= 3.2;
        for (let i = 0; i < 4; i++) {
          const a = st.rot + (i * TAU) / 4;
          f.ring(f.bx + Math.cos(a) * 130, f.by + Math.sin(a) * 130, 10, a, 170, COLORS.dark, 5, 0, 'longball');
        }
      }
    },
  },

  // 禁忌「笼中鸟 Kagome Kagome」：发射器沿圆轨道绕场飞，定期放环
  kagome: {
    tick(sp, dt, f) {
      const st = sp.st;
      if (!st.orbiters) {
        st.orbiters = [];
        for (let i = 0; i < 4; i++) {
          st.orbiters.push({ phase: (i * TAU) / 4, spd: 0.55 * (i % 2 ? 1 : -1), r: 250 + i * 40, fire: 0 });
        }
      }
      for (const o of st.orbiters) {
        o.phase += dt * o.spd;
        const px = f.ax + Math.cos(o.phase) * o.r;
        const py = f.ay + Math.sin(o.phase) * o.r * 0.85;
        o.fire += dt;
        if (o.fire >= 1.0) {
          o.fire -= 1.0;
          f.ring(px, py, 12, o.phase + Math.PI / 2, 165, COLORS.red, 5);
        }
      }
      // 中央"鸟笼"：慢速大环压场
      st.cage = (st.cage ?? 0) + dt;
      if (st.cage >= 2.6) {
        st.cage -= 2.6;
        f.ring(f.ax, f.ay, 26, f.rnd() * TAU, 105, COLORS.gold, 6);
      }
      // 第八十九轮：大玉发射源（验证）—— 从舞台点每 2.2 秒一颗，
      // 慢速瞄准弹。视觉半径 30（直径 60，落在原版 55-65px 区间），
      // 判定只有 13：白色外圈完全无判定，中心小核才命中（用户：
      // "整个白色部分都没有命中判定"）。
      st.jade = (st.jade ?? 1.2) + dt;
      if (st.jade >= 2.2) {
        st.jade -= 2.2;
        f.emit({
          x: f.bx, y: f.by, angle: f.aim(f.bx, f.by), speed: 85,
          radius: 30, hitRadius: 13, shape: 'jade',
        });
      }
    },
  },

  // 禁忌「恋之迷宫 Maze of Love」：第七十七轮按用户记忆重做 ——
  // "原版是密集弹幕封路有缺口，玩家需要在普通弹幕的攻击下再被密集弹幕
  // 锁死路之前赶到缺口然后前往下一个缺口，我们现在的'迷宫'全是窟窿一丁
  // 点都不迷宫。看看怎么改比较好？"
  //   · 封路墙：每 3.6s 一道从四边轮换（下→右→上→左）扫过全场的密集
  //     弹墙（弹间距 40px），墙上只留一个 170px 的缺口 —— 迟到就被墙
  //     锁路；墙速 110px/s，穿场要 ~10s，场上常驻 2~3 道 = 真·迷宫。
  //   · 普通压力：§6.5 的"随机偏移发射点 + 左右各偏 1.4° 双向散射"瞄准
  //     弹（原作 bullet_random(2,6,1,1,0,0,1.59534,1.546253)，
  //     1.59534≈π/2+0.0245 / 1.546253≈π/2-0.0245，shoot_offset 语义）；
  //     外加 0.9s 一轮随机点小环（保留旧版）。
  //   旧版只有随机位置低密度环（用户："全是窟窿"）。
  //   第九十一轮：全部弹幕 = 椭圆蛋，双色轮流 —— 用户："恋之迷宫是椭圆蛋，
  //   颜色是浅青绿/薄荷绿的长椭圆弹幕（外圈饱和薄荷青绿带点冷调，内层往
  //   中心过渡到白色/极浅青，柔和发光边缘）……还有黄色长椭圆弹（主体亮黄/
  //   金黄偏暖，外圈金色描边细而清晰，中心亮黄到白渐变）。2种弹幕轮流切换
  //   发射。"（白芯/发光边/描边由 longball 渲染管线统一呈现。）
  maze: {
    tick(sp, dt, f) {
      const st = sp.st;
      const MINT = '#2fd6b0';    // 饱和薄荷青绿（冷调）
      const GOLD = '#ffbe3d';    // 暖调金黄
      const nextCol = () => (st.flip = !st.flip) ? MINT : GOLD;
      // —— 封路墙（开卡 1.4s 后第一道）
      st.wallAcc = (st.wallAcc ?? 2.2) + dt;
      if (st.wallAcc >= 3.6) {
        st.wallAcc -= 3.6;
        const side = (st.wallSide = ((st.wallSide ?? -1) + 1) % 4);
        const wallLen = (side % 2 === 0 ? ARENA.hw * 2 : ARENA.hh * 2) + 80;
        const gapC = (f.rnd() * 2 - 1) * wallLen * 0.35;   // 缺口在中间 70% 随机
        const gapHalf = 85;
        const wc = nextCol();
        for (let d = -wallLen / 2; d <= wallLen / 2; d += 40) {
          if (Math.abs(d - gapC) < gapHalf) continue;      // 唯一缺口
          let x, y, ang;
          if (side === 0) { x = f.ax + d; y = f.ay - ARENA.hh - 40; ang = Math.PI / 2; }
          else if (side === 1) { x = f.ax + ARENA.hw + 40; y = f.ay + d; ang = Math.PI; }
          else if (side === 2) { x = f.ax + d; y = f.ay + ARENA.hh + 40; ang = -Math.PI / 2; }
          else { x = f.ax - ARENA.hw - 40; y = f.ay + d; ang = 0; }
          f.emit({
            x, y, angle: ang + (f.rnd() * 2 - 1) * 0.02, speed: 110,
            // 第八十七轮：radius 5 → 7.5（×1.5）—— 用户："这张卡的弹幕
            // 太小了，导致卡本身没啥压制力。"
            color: wc, radius: 7.5, range: wallLen + 160,
            shape: 'longball',
          });
        }
      }
      // —— §6.5 随机偏移双向散射瞄准弹（set_float_rand(384,180) × 场地比例）
      st.acc = (st.acc ?? 0) + dt;
      if (st.acc >= 1.2) {
        st.acc -= 1.2;
        const sx = f.bx + (f.rnd() * 2 - 1) * 96;
        const sy = f.by + (f.rnd() * 2 - 1) * 48;
        const a = f.aim(sx, sy);
        const sc = nextCol();
        f.emit({ x: sx, y: sy, angle: a + 0.0245, speed: 175, color: sc, radius: 7.5, shape: 'longball' });
        f.emit({ x: sx, y: sy, angle: a - 0.0245, speed: 175, color: sc, radius: 7.5, shape: 'longball' });
      }
      // —— 随机点小环（别贴脸刷：离玩家至少 140px）
      st.ringAcc = (st.ringAcc ?? 0) + dt;
      if (st.ringAcc >= 0.9) {
        st.ringAcc -= 0.9;
        let px = 0, py = 0;
        for (let tries = 0; tries < 8; tries++) {
          px = f.ax + (f.rnd() * 2 - 1) * (ARENA.hw - 40);
          py = f.ay + (f.rnd() * 2 - 1) * (ARENA.hh - 40);
          if (Math.hypot(px - f.P.x, py - f.P.y) > 140) break;
        }
        f.ring(px, py, 12, f.rnd() * TAU, 175, nextCol(), 7.5, 0, 'longball');
      }
    },
  },

  // 禁弹「Starbow Break」：发射器飞出减速停下 → 同心圆扩散（彩虹色是这张卡的身份）
  // 第七十轮：按原作 §6.6（Sub67/68/69）重做 —— 用户："禁弹「Starbow Break」
  // （星虹破碎）也是弹幕太少了，你上一张禁弹改的很好，这张也那样还原。"
  //   原作：芙兰静止，从 6 个固定星形角度（135°/157.5°/180°/45°/22.5°/0°）
  //   生成发射器，两侧对称再投放（原作 (-32,336..144)/(416,336..144)，
  //   角 -0.5890486 / 3.3379421）；发射器「随机方向飞出 + move_speed(2.5)
  //   + move_as_interp_decelerate(90)」= 150px/s 飞 90 帧（1.5s）减速停下，
  //   然后做同心圆扩散。旧版只有原地六芒扇（24 发/0.88s ≈ 27 发/秒），
  //   新版稳态 ~13 个发射器 × 13 发同心圆 / 1.4s ≈ 120 发/秒，且弹幕
  //   从四面八方的发射器涌出 —— 这才是星虹破碎的"星空炸裂"感。
  starbow: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.es = st.es ?? [];
      st.wave = (st.wave ?? 0) + dt;
      if (st.wave >= 2.8) {
        st.wave -= 2.8;
        st.es = st.es.filter((e) => e.life > 0);
        // 6 个固定星形角度（原作数值原样抄）
        const ANGLES = [2.3561945, 2.7488935, 3.1415927, 0.7853982, 0.3926991, 0];
        for (const a of ANGLES) {
          const dir = a + (f.rnd() * 2 - 1) * 0.3;    // 原作 move_rand_in_bounds：随机方向飞出
          st.es.push(this._mkEmitter(f, dir, f.bx + Math.cos(a) * 90, f.by + Math.sin(a) * 90));
        }
        // 两侧对称投放（原作 (-32, y)/(416, y)，角 -0.5890486 / 3.3379421）
        const sy = f.ay + (f.rnd() * 2 - 1) * 220;
        st.es.push(this._mkEmitter(f, -0.5890486, f.ax - ARENA.hw - 60, sy));
        st.es.push(this._mkEmitter(f, 3.3379421, f.ax + ARENA.hw + 60, sy));
      }
      // —— 第七十八轮：发射器画成"大的实蛋" ——
      // 用户："禁弹过后一时刻的时钟是不是有4个弹幕发射器？而且好像是隐
      // 形的？要不把他们的位置变成一颗大的实蛋显示，不然有时候莫名被
      // 0帧刷新的子弹命中，完全无法判断怎么躲。"
      // —— 星虹的发射器（st.es）一直没进渲染通道 = 隐形弹源：弹环凭空
      // 出现，玩家不知道往哪躲。现在每帧把发射器位置推送 vis 通道，画成
      // 和发射器同色的实心大弹（r=14），弹环从哪来一目了然。
      // （推送放在移动循环之后，实蛋位置 = 本帧实时位置）
      for (const e of st.es) {
        if (e.life <= 0) continue;
        e.life -= dt;
        e.t = Math.min(1.5, e.t + dt);
        const k = Math.max(0, 1 - e.t / 1.5);          // 原作 move_as_interp_decelerate(90)
        e.x += e.vx * k * dt;
        e.y += e.vy * k * dt;
        // 保持在空气墙内（原作 move_rand_in_bounds 语义：不出界）
        e.x = Math.max(f.ax - ARENA.hw + 20, Math.min(f.ax + ARENA.hw - 20, e.x));
        e.y = Math.max(f.ay - ARENA.hh + 20, Math.min(f.ay + ARENA.hh - 20, e.y));
        e.ring -= dt;
        if (e.ring <= 0) {
          e.ring = 1.4;
          f.ring(e.x, e.y, 13, f.rnd() * TAU, 120, e.col, 5);
        }
      }
      st.vis = st.es.filter((e) => e.life > 0).map((e) => ({ x: e.x, y: e.y, r: 14,
        kind: 'orb', color: e.col, age: e.t, dir: 0 }));
    },
    _mkEmitter(f, angle, x, y, col) {
      return {
        x, y,
        vx: Math.cos(angle) * 150,                    // 原作 move_speed(2.5) × 60
        vy: Math.sin(angle) * 150,
        t: 0, life: 4.6, ring: 0.9,
        col: RAINBOW[Math.floor(f.rnd() * RAINBOW.length)],
      };
    },
  },

  // 禁弹「Catadioptric」：多层速度递减的随机弹幕，"反射镜"般的层叠。
  // 第六十七轮：按原作 §6.7（Sub75）重做 —— 用户："这张卡弹幕实在太少了。
  // 多还原一些，难得打不过我会告诉你。"
  //   原作五层**同帧齐射**（不是一次一层！），速度 5.2→0.5 递减
  //   （×60 → 312→30 px/s：快弹在内、慢弹在外），每层角度随机范围递增
  //   （0 → ±π/32，即 ±1.4°→±5.6°，缝隙不可背板）。
  //   密度：9+18+18+12+12 = 69 发/轮 × 每 1.1 秒 ≈ 63 发/秒
  //   （旧版 24 发 / 1.35 秒 ≈ 18 发/秒，约 3.5 倍）。
  catadioptric: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.acc = (st.acc ?? 0) + dt;
      if (st.acc >= 1.1) {
        st.acc -= 1.1;
        st.dir = -(st.dir ?? 1);                      // 每轮换向 → 镜面反射感
        const base = f.rnd() * TAU;
        const LAYERS = [
          { n: 9, spd: [312, 312], jit: 0, r: 5.5 },             // 快弹核心（原 5.2）
          { n: 18, spd: [300, 210], jit: Math.PI / 128, r: 5.5 }, // ±1.4°
          { n: 18, spd: [210, 150], jit: Math.PI / 48, r: 5.5 },  // ±3.75°
          { n: 12, spd: [150, 90], jit: Math.PI / 40, r: 5 },     // ±4.5°（原 6 发，加密）
          { n: 12, spd: [90, 30], jit: Math.PI / 32, r: 5 },      // ±5.6°（原 6 发，加密）
        ];
        for (let li = 0; li < LAYERS.length; li++) {
          const L = LAYERS[li];
          const color = li % 2 ? COLORS.red : COLORS.white;
          for (let i = 0; i < L.n; i++) {
            const a = base + st.dir * (i / L.n) * TAU + (f.rnd() * 2 - 1) * L.jit;
            const spd = L.spd[0] + (L.spd[1] - L.spd[0]) * f.rnd();   // 原作每层速度也是区间随机
            f.emit({ x: f.bx, y: f.by, angle: a, speed: spd, radius: L.r, color });
          }
        }
      }
      // —— 第九十一轮：蓝紫大玉蛋齐射 —— 用户："禁弹「Catadioptric」
      // 增加蓝紫色大玉蛋，芙兰向自己的头顶发射5颗大玉蛋（90度扇形散开，
      // 左右各45度）然后大玉蛋会反弹（类似495的反弹），然后之后会右射
      // 5发大玉在左射5发大玉，都是90度扇形会反弹，大玉蛋飞过的地方会有
      // 尾随的弹幕同移动路线的大弹幕（可以做2倍大小的普通球弹）和球弹。
      // 每次射击间隔3秒，然后循环。"
      // 齐射顺序：上（-90°）→ 右（0°）→ 左（180°），3 秒一轮换。
      const DIRS = [-Math.PI / 2, 0, Math.PI];
      st.vAcc = (st.vAcc ?? 1.0) + dt;
      if (st.vAcc >= 3) {
        st.vAcc -= 3;
        const dir = DIRS[st.vIdx = ((st.vIdx ?? -1) + 1) % 3];
        const wall = st.wall ?? (st.wall = {
          minX: ARENA.cx - ARENA.hw, maxX: ARENA.cx + ARENA.hw,
          minY: ARENA.cy - ARENA.hh,
        });
        for (let i = 0; i < 5; i++) {
          const a = dir + ((i / 4) - 0.5) * (Math.PI / 2);   // 90° 扇形（±45°）
          f.emit({
            x: f.bx, y: f.by, angle: a, speed: 115,
            radius: 30, hitRadius: 13, shape: 'jade',
            jadeColor: '#6f5ff0',          // 蓝紫
            wall, range: 1600,             // 495 同款三面反弹（约 14 秒航程）
            tag: 'catadioptric',
          });
        }
      }
      // 尾随弹幕：每颗飞行中的大玉，每 0.3s 沿当前位置落
      // 1 颗 2 倍普通球（r12，慢速漂）+ 1 颗普通球弹（r5）。
      // 节奏/射程按弹幕预算收敛（0.3s 一对、短射程自灭），
      // 不挤占本卡自机狙扇的预算 —— 用户担心"难度暴增"。
      st.tAcc = (st.tAcc ?? 0) + dt;
      if (st.tAcc >= 0.3) {
        st.tAcc -= 0.3;
        const jades = f.main.world.enemyBullets.items.filter(
          (b) => !b.dead && b.tag === 'catadioptric' && b.shape === 'jade');
        for (const j of jades) {
          f.emit({ x: j.x, y: j.y, angle: f.rnd() * TAU, speed: 22,
            radius: 12, color: '#9f8cf5', range: 140 });
          f.emit({ x: j.x, y: j.y, angle: f.rnd() * TAU, speed: 55,
            radius: 5, color: '#c9befa', range: 190 });
        }
      }
    },
  },

  // 禁弹「过后一时辰的时钟」：四角"钟摆"向中心推进，沿途打瞄准弹
  // 第一百零二轮（用户："符卡过后一时刻的钟弹幕太小了，整体加大一些，
  // 可以再改一部分变成椭圆蛋增加视觉冲击，目前就他这张卡最单薄了"）：
  //   · 瞄准弹 r5 → r7（金色圆弹，整体加大 40%）；
  //   · 每 4.5s 的环形齐射改用 longball 椭圆蛋（2.2:1，沿飞行方向）
  //     r7.5 —— 周期性大蛋齐射就是"视觉冲击"担当。
  clock: {
    tick(sp, dt, f) {
      const st = sp.st;
      if (!st.hands) {
        const corners = [
          { x: f.ax - ARENA.hw + 40, y: f.ay - ARENA.hh + 40 },
          { x: f.ax + ARENA.hw - 40, y: f.ay - ARENA.hh + 40 },
          { x: f.ax - ARENA.hw + 40, y: f.ay + ARENA.hh - 40 },
          { x: f.ax + ARENA.hw - 40, y: f.ay + ARENA.hh - 40 },
        ];
        st.hands = corners.map((c) => ({ x: c.x, y: c.y, home: c, fire: 0, ring: 0 }));
      }
      for (const h of st.hands) {
        const dx = f.ax - h.x, dy = f.ay - h.y;
        const d = Math.hypot(dx, dy);
        if (d < 70) { h.x = h.home.x; h.y = h.home.y; continue; }   // 到中心回角落
        h.x += (dx / d) * 42 * dt; h.y += (dy / d) * 42 * dt;
        h.fire += dt;
        if (h.fire >= 0.75) {
          h.fire -= 0.75;
          f.emit({ x: h.x, y: h.y, angle: f.aim(h.x, h.y), speed: 200, radius: 7, color: COLORS.gold });
        }
        h.ring += dt;
        if (h.ring >= 4.5) {
          h.ring -= 4.5;
          f.ring(h.x, h.y, 10, f.rnd() * TAU, 150, COLORS.dark, 7.5, 0, 'longball');   // 椭圆蛋齐射
        }
      }
    },
  },

  // 禁弹「495 年的波纹」：同心波纹，带旋转缺口（安全 lanes 随波转动）。
  // 第五十七轮：波纹弹会撞墙反弹 —— 头顶/左墙/右墙各一次（原版行为，
  // 用户："芙兰的弹幕在撞到头顶左侧和右侧的墙壁的时候，弹幕会反弹"）；
  // 底墙不反弹，弹过去就过去了。每颗弹最多 3 次反弹。
  // 第五十九轮：波纹 26 → 31 颗（+20%，缺口 6 → 7 等比、旋转步 +4 → +5，
  // 演出效果不变）；内圈补弹 18 → 22。
  ripples: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.wall = st.wall ?? {
        minX: ARENA.cx - ARENA.hw, maxX: ARENA.cx + ARENA.hw,
        minY: ARENA.cy - ARENA.hh,
      };
      st.acc = (st.acc ?? 0) + dt;
      // 第七十四轮（用户："原版495的波纹是会慢慢越射越快的，我们一直是
      // 匀速，让这个卡在30秒内越射越快，差不多最后一秒总共加快50%射速
      // 吧。"）—— 射出间隔随卡内时间线性收窄：1.5s → 1.0s（终盘射速
      // +50%）。
      // 第七十八轮（用户："150刚到就结束了，其实难度没提升多少芙兰就换
      // 卡了。改到30秒加速到175%看看。"）—— 终盘 +50% → +75%（射速
      // 175%，间隔 1.5 → ≈0.857s）。
      const prog = Math.min(1, sp.t / 30);            // 0 → 1（30 秒卡走满）
      const interval = 1.5 / (1 + 0.75 * prog);       // 1.5 → ≈0.857
      if (st.acc >= interval) {
        st.acc -= interval;
        st.gap = ((st.gap ?? 0) + 5) % 31;   // 缺口每次转 5 格
        // 第一百零四轮：3 色弹幕 —— 用户："一个现在的颜色，加一个金色，
        // 加一个亮浅蓝色。3层循环，一个圈一个色。" 每圈整体一色：
        // 暗红（原色）→ 金 → 亮浅蓝 → 暗红 …… 周而复始。
        st.wave = (st.wave ?? 0) + 1;
        const waveColor = [COLORS.deep, COLORS.gold, COLORS.sky][(st.wave - 1) % 3];
        for (let i = 0; i < 31; i++) {
          if (i >= st.gap && i < st.gap + 7) continue;   // 7 格安全缝
          const a = (i / 31) * TAU;
          // 第八十八轮：495 波纹 = 原版"长球螺旋" → longball（沿飞行方向）
          f.emit({ x: f.bx, y: f.by, angle: a, speed: 190, radius: 6, color: waveColor, wall: st.wall, shape: 'longball' });
        }
        st.inner = (st.inner ?? 0) + 1;
        if (st.inner % 3 === 0) f.ring(f.bx, f.by, 22, f.rnd() * TAU, 130, COLORS.red, 5, 0, 'longball');
      }
    },
  },

  // ============================================================
  // 第五十六轮：Extra Phantom 专属 3 张（新典追加，规格书 6.11~6.13）。
  // 原版数据里的帧参数全部换算成 dt 累加；密度照旧砍到原版的一半以下。
  // ============================================================

  // 咒弹「Fragile Wing」（131）：第七十五轮按解包 §6.11 重做 —— 用户：
  // "原来是各种颜色的彩色弹幕而且会追着玩家的位置持续生成弹幕然后弹幕
  // 在飞出去。" 解包要点：
  //   · 发射器做 **14 槽贴图循环**（$i1 += 2; $i1 %= 14 → 7 种色相）——
  //     彩色弹幕的出处，旧版单色 COLORS.red 完全丢了这条；
  //   · 每次发射是 bullet_circle(5) = **5 发整环**（72° 间隔），环的起始
  //     角每次 += $f2 = -π/14×1.5 ≈ -0.337 rad —— 逐环错开的整环就是
  //     "羽毛扇开"（旧版打成 36° 扇，观感不对）；
  //   · bullet_effects(300, …, 0.005) = **减速淡出** —— 弹射出后慢慢减
  //     速漂着（新增敌弹 decel/floorSpeed 通道）；
  //   · 4 个翅膀每 2 秒依次生成（自转方向逐个取反），+514 帧后一次补
  //     5 个（扇位间隔 0.589049 rad、基础角 = -π/2 + 0.3927 + 玩家角）。
  //     翅膀在**生成瞬间瞄准玩家**（$f1 = player_angle）—— "追着玩家的
  //     位置持续生成弹幕"的出处。
  fragile: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.wings = st.wings ?? [];
      st.n = st.n ?? 0;
      st.slot = st.slot ?? 0;                    // 14 槽贴图循环的游标
      while (st.n < 4 && sp.t >= 1 + st.n * 2) {
        st.wings.push({ a: f.aim(f.bx, f.by), dir: (st.n % 2 ? -1 : 1), fire: 0 });
        st.n += 1;
      }
      if (!st.burst && sp.t >= 8.5) {
        st.burst = true;
        const base = -Math.PI / 2 + 0.392699 + f.aim(f.bx, f.by);
        for (let i = 0; i < 5; i++) {
          st.wings.push({ a: base + i * 0.589049, dir: (i % 2 ? -1 : 1), fire: f.rnd() * 0.14 });
        }
      }
      // 发射器可视体（渲染层 _drawFlandreEmitters 画成旋转的小羽翼）
      st.vis = st.wings.map((w, i) => ({ x: f.bx, y: f.by, r: 10, kind: 'wing',
        color: RAINBOW[(st.slot + i * 2) % 7], age: w.fire, dir: w.dir }));
      for (const w of st.wings) {
        w.fire += dt;
        if (w.fire >= 0.3) {
          w.fire -= 0.3;
          st.slot = (st.slot + 2) % 14;          // 贴图槽步长 2、模 14 → 彩色循环
          const col = RAINBOW[(st.slot / 2) % 7];
          for (let i = 0; i < 5; i++) {          // bullet_circle(5)：5 发整环
            // 第九十一轮：球弹 → 七彩结晶蛋（七彩沿用 RAINBOW 循环）
            f.emit({ x: f.bx, y: f.by, angle: w.a + (i * TAU) / 5,
              speed: 200, radius: 5, color: col, shape: 'crystal',
              decel: 30, floorSpeed: 55, range: 1100 });
          }
          w.a += w.dir * (Math.PI / 14) * 1.5;   // 环起始角 += -π/14×1.5（扇开）
        }
      }
    },
  },

  // 咒弹「伏行之血痕」（132）：第七十五轮再还原 —— 用户："是各种大玉
  // 弹幕带着一大堆小弹幕发出去，我们可以把领头弹幕的体积做大点，还有
  // 再还原点。" 解包 §6.12：5 个发射器从屏幕顶不同 x 依次落下（+40/+101/
  // +161/+221/+281 帧），各自持续吐慢速环弹、角度每帧加固定值 = 旋转
  // 喷泉。发射器本体在原版是**可见的大玉弹**（bullet 贴图的 enemy）——
  // 旧版隐形，用户看到的"大玉带小弹"就是它。现在：发射器画成大玉
  // （渲染层 vis 通道，深红外圈 + 白高光），小弹环弹跟在后面。
  bloodstain: {
    tick(sp, dt, f) {
      const st = sp.st;
      st.ems = st.ems ?? [];
      st.n = st.n ?? 0;
      while (st.n < 5 && sp.t >= 0.7 + st.n * 1.0) {
        st.ems.push({
          x: ARENA.cx + (f.rnd() * 2 - 1) * (ARENA.hw - 80),
          y: ARENA.cy - ARENA.hh + 10,
          a: f.rnd() * TAU, spin: (f.rnd() * 2 - 1) * 1.1, fire: 0,
        });
        st.n += 1;
      }
      st.vis = st.ems.map((em) => ({ x: em.x, y: em.y, r: 15, kind: 'blood',
        color: COLORS.dark, age: em.fire, dir: em.spin }));
      for (const em of st.ems) {
        if (em.y < ARENA.cy + 80) em.y += 34 * dt;   // 缓慢下压
        em.a += em.spin * dt;
        em.fire += dt;
        if (em.fire >= 0.3) {
          em.fire -= 0.3;
          f.ring(em.x, em.y, 12, em.a, 95, COLORS.dark, 4.5);
        }
      }
      // 芙兰本体补一组瞄准弹，防站桩
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= 1.6) {
        st.aim -= 1.6;
        // 第七十八轮：粉色自机狙体积加倍（6 → 12）—— 用户："咒弹伏行之
        // 血痕这个卡你做的芙兰会射3个粉色弹幕自机狙，但是粉色弹幕太小了。
        // 几乎没威胁，加大一倍体积吧。"
        // 第九十轮：改成粉色大玉蛋 —— 用户："把那个粉色弹幕改成粉色大玉
        // 蛋射玩家。大玉蛋颜色改法就是中圈粉色，内圈改成深粉到偏黑的粉。"
        // 视觉 30 / 判定 13（大玉标准），jadeColor 走渲染层的粉盘。
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 3, 24, 185, COLORS.pink, 30, 'jade',
          { hitRadius: 13, jadeColor: COLORS.pink });
      }
    },
  },

  // 「弹幕的心脏」（133）：第七十五轮再还原 —— 用户："原版更像是花朵
  // 一样的密集弹幕，我们的也完全对不上。" 解包 §6.13：两个「心室」在
  // 芙兰下方，各自不断生成「火花」，火花以 **每 1~6 帧一发** 的密度打
  // 5 向扇形（spread π/8），基准角持续自转 + 贴图槽在 2/3 间交替（两种
  // 颜色交替）—— 密集旋转的扇形瓣叠出来就是"花朵"。旧版每 0.28s 一发
  // 太稀，且单色。现在：心室 2×2=4 个、0.2s 一发、金银双色交替，
  // 心室本身画成脉动的心红色球（vis 通道）。
  heart: {
    tick(sp, dt, f) {
      const st = sp.st;
      if (!st.init) {
        st.init = true;
        if (f.boss) { f.boss.x = f.ax; f.boss.y = f.ay - STAGE_DY; }
      }
      st.ems = st.ems ?? [];
      st.reloc = (st.reloc ?? 0) + dt;
      st.slot = st.slot ?? 0;
      if (!st.ems.length || st.reloc >= 10) {
        st.reloc = 0;
        st.ems = [];
        for (const side of [-1, 1]) {
          for (let k = 0; k < 2; k++) {
            st.ems.push({
              x: f.bx + side * (46 + k * 34) + (f.rnd() * 24 - 12),
              y: f.by + 32 + (f.rnd() * 24 - 12),
              a: f.rnd() * TAU, spin: side * 0.85 * (k ? -1 : 1), fire: f.rnd() * 0.5,
            });
          }
        }
      }
      st.vis = st.ems.map((em) => ({ x: em.x, y: em.y, r: 9, kind: 'vent',
        color: COLORS.red, age: em.fire, dir: em.spin }));
      for (const em of st.ems) {
        em.a += em.spin * dt;
        em.fire += dt;
        // 第七十五轮：0.28s → 0.2s、金银双色交替（贴图槽 2/3）——
        // 用户："原版更像是花朵一样的密集弹幕"。
        if (em.fire >= 0.2) {
          em.fire -= 0.2;
          st.slot = 1 - st.slot;
          // 第九十一轮：结晶蛋加一种紫色 —— 用户："「弹幕的心脏」的结晶蛋
          // 颜色增加一种紫色结晶蛋。可以替换一部分粉色结晶蛋（我看你
          // 自己做的心脏符卡粉色蛋挺多的）。" 粉色的份额拆一半给紫
          //（RAINBOW 的 #c56cf0）：金 50% / 粉 25% / 紫 25%。
          let col;
          if (st.slot) {
            col = COLORS.gold;
          } else {
            st.pk = !st.pk;
            col = st.pk ? COLORS.pink : '#c56cf0';
          }
          const sx = em.x + Math.cos(em.a) * 26;
          const sy = em.y + Math.sin(em.a) * 26;
          // 第八十八轮：心脏 = 原版晶体弹 → crystal（沿飞行方向的尖头叶片）
          f.fan(sx, sy, em.a, 5, 22.5, 150, col, 5, 'crystal');
          f.emit({ x: sx, y: sy, angle: em.a + Math.PI, speed: 60, radius: 4, color: col, shape: 'crystal' });
        }
      }
      st.beat = (st.beat ?? 0) + dt;
      // 第六十一轮：心跳大环 2s → 1.4s 一圈、20 → 26 颗
      if (st.beat >= 1.4) {
        st.beat -= 1.4;
        f.ring(f.bx, f.by, 26, f.rnd() * TAU, 110, COLORS.red, 6, 0, 'crystal');   // 第八十八轮：晶体
      }
      // 第六十一轮：本体每 2.2 秒一组慢速 3 向瞄准弹（心室打不到的死角
      // 也有压力；原版她本人站在场中心持续参与弹幕）
      st.aim = (st.aim ?? 0) + dt;
      if (st.aim >= 2.2) {
        st.aim -= 2.2;
        // 第八十八轮：本体瞄准弹同样走晶体（整卡统一弹形）
        f.fan(f.bx, f.by, f.aim(f.bx, f.by), 3, 24, 95, COLORS.red, 5, 'crystal');
      }
    },
  },
};

// ---- 第一百八十五轮：符卡重做（版本 B/C）----
// 新脚本放在 flandre-spells.js，按 id 覆盖上面的旧实现；没重做的卡继续用旧的。
// 这样可以分批上线、随时回退（删掉 NEW_SPELLS 里对应项即可），也方便在 Node 里无头模拟（tools/_spell-sim.mjs）。
import { NEW_SPELLS } from './flandre-spells.js';
Object.assign(SPELLS, NEW_SPELLS);
export { SPELLS };

// ============================================================
// 战斗控制器
// ============================================================
export class FlandreFight {
  /**
   * @param {object} main  Game 实例（要 input/camera/world/player/ui/audio）
   * @param {object} opts  { onDone(outcome) }  outcome: 'death'|'beat'|'partial'
   */
  constructor(main, opts = {}) {
    this.main = main;
    this.onDone = opts.onDone || (() => {});
    this.rng = main.world.rng;
    this.t = 0;                    // 整场计时
    this.phase = 'prologue';       // prologue | cards | extra | done
    this.over = false;
    this.spell = null;             // 当前符卡 {id,name,t,dur,st,speedMul}
    this.cardsQueue = [];
    this.cardsDone = 0;            // 打败的符卡数（30 秒内打掉 25% 才算）
    this.lockLeft = 0;             // 切符卡锁血剩余
    this.extraLeft = 0;
    this.extraCardTimer = 0;
    this.extraCardsLeft = 0;
    this.outcome = null;
    this.boss = null;
    this.phaseStartHp = 0;
    this.spellName = '';
    // 序章旗标
    this._warned = false;
    this._shaked = false;
    this._named = false;
    this._hinted = false;
    // 消弹
    this.purgeCd = 0;
    this._capNow = BULLET_CAP;   // 199 轮：自适应弹量上限的当前值（见 _adaptCap）
    this._purgeWasReady = true;
    this.nova = null;              // {x,y,r,hold,auto}
    // 渲染接口：背景压暗（black → 暗红）
    this.tint = { alpha: 0, color: '#000000' };
    // 第七十轮：eff07 真素材背景可见度（渲染层按它画，见 tintUpdate 与
    // renderer._drawFlandreBg；stage7 深青层已按用户要求撤掉）
    this.bg = { eff: 0 };
    // ---- 第一百零一轮：白色雾气粒子（etama4 引擎粒子复刻，加法混合）----
    // 场景2 常驻呼吸雾：16 个半透明光斑/星芒在竞技场背景层慢漂，
    // α 在 0.2~0.5 间按各自相位呼吸（第一百零二轮按用户实机反馈加大：
    // "雾气几乎看不见……α 从 0.2±0.1 提升到 0.35±0.15，让它更实一点"，
    // 数量 12 → 16、尺寸整体 +30% 左右）。
    // 【注意】纯装饰粒子用 Math.random —— **绝不碰 this.rng**：
    // 游戏 RNG 流是确定性测试的命根子，多抽一次全盘错位。
    this.mistAmbient = [];
    for (let i = 0; i < 16; i++) {
      const star = i % 4 === 0;   // 每 4 个里 1 个星芒，其余是软雾球
      this.mistAmbient.push({
        x: ARENA.cx + (Math.random() * 2 - 1) * ARENA.hw * 0.92,
        y: ARENA.cy + (Math.random() * 2 - 1) * ARENA.hh * 0.92,
        vx: (Math.random() * 2 - 1) * 8,
        vy: (Math.random() * 2 - 1) * 6,
        size: star ? 60 + Math.random() * 54 : 90 + Math.random() * 110,
        phase: Math.random() * Math.PI * 2,
        speed: 0.5 + Math.random() * 0.9,
        star,
      });
    }
    // 场景1 螺旋吸入光晕：每张符卡开场 6 个（effectId 17 Attract，
    // 256px 环 → 60 帧收缩到中心，粉白染色），渲染层按参数式画
    this.mistOrbs = [];
  }

  /** 竞技场锚点 */
  get ax() { return ARENA.cx; }
  get ay() { return ARENA.cy; }
  get P() { return this.main.player; }
  get bx() { return this.boss ? this.boss.x : this.ax; }
  get by() { return this.boss ? this.boss.y : this.ay; }
  /** 符卡脚本里的随机数便捷入口 */
  rnd() { return this.rng.next(); }

  // ---------- 发射原语（emit 数学来自反编译规格，参考系换成本竞技场）----------

  emit(cfg) {
    if (this.over) return;
    const list = this.main.world.enemyBullets;
    if (list.items.length >= this._capNow) return;   // 预算硬顶（自适应：BULLET_CAP_MIN ~ BULLET_CAP）
    // 第五十四轮（用户反馈 3）：弹幕齐射声 —— audio 内部节流，
    // 每一波 fan/ring 至多响一次，不会变成噪音墙。
    audio.play('flandreShot');
    const mul = this.spell?.speedMul ?? 1;
    this.main.world.spawnEnemyBullet({
      x: cfg.x, y: cfg.y, angle: cfg.angle,
      speed: cfg.speed * mul,
      damage: 0,             // 伤害走 flandreHit 的比例真伤规则，不用这个字段
      radius: cfg.radius ?? 6, color: cfg.color ?? COLORS.red,
      // 第六十四轮：495 波纹的弹要吃到 3 次反弹 —— 场地长轴一次穿越就
      // 1000+px，1500 的默认射程反弹一次就"射程用尽"（用户原话）。带墙
      // 反弹的弹给 4800：3 次反弹（每次 ≈ 一次全穿越）也飞得完。
      range: cfg.wall ? 4800 : (cfg.range ?? 1500),
      flandre: true,
      // 第五十七轮：墙壁反弹配置（只有 495 波纹会传）—— 透传给敌弹
      wall: cfg.wall ?? null,
      // 第七十五轮：减速弹透传（Fragile Wing 的 bullet_effects 减速淡出）
      decel: cfg.decel ?? 0,
      floorSpeed: cfg.floorSpeed ?? 0,
      // 第八十八轮：弹幕形状透传（'orb' 默认 / 'longball' 椭圆蛋 /
      // 'crystal' 结晶蛋 / 'jade' 大玉）
      shape: cfg.shape ?? 'orb',
      // 第八十九轮：独立判定半径透传（不传 = radius，行为不变；
      // 大玉传小值：白色外圈无判定）
      hitRadius: cfg.hitRadius,
      // 第九十轮：大玉配色基色（渲染层按它取盘；不传 = 默认黄盘）
      jadeColor: cfg.jadeColor,
      // 第九十一轮：来源标记透传
      tag: cfg.tag ?? '',
    });
    // 第一颗弹幕出现 → 小小猫娘提示（用户指定台词与空格教学）
    if (!this._hinted) {
      this._hinted = true;
      // 第七十六轮：三句教学改为**时间轴错开** —— 用户："猫娘的教学从
      // '有什么靠近了'这句下一句就是'按住shirt减速'中间漏了2句台词没
      // 显示，但是语音是好好地说完了的。" 根因：三句在同一帧连发，猫娘
      // 计划的气泡一帧内互相顶掉，只剩最后一句（Shift）可见；语音队列
      // 却完整播完 → 声画错位。现在每句占一个 2.6s 气泡档期（语音也随
      // 各自时刻走，重新同步）。
      // 第五十四轮：台词改回自然的"我"（用户："可以出现'我'和'自己'，
      // 不然她现在的台词是人家可以用人家的力量帮你，太多人家了"）。
      // 唯一禁令：不许出现"小小猫娘"四个字 —— 那是称呼不是名字。
      // 第六十五轮：移速教学也在这里弹一次，5 秒 ——
      // 用户："再芙兰登场前的30秒演出的小小猫娘消弹教学里把这个提示也
      // 弹一次5秒显示'按住shirt可以慢速移动'"（shirt 是 Shift 的笔误）
      this._hintQueue = [
        { text: '我可以用我的力量帮你！' },
        { text: '按空格键，我可以帮你清理弹幕哦' },
        { text: '按住 Shift 可以慢速移动', dur: 5000 },
      ];
      this._hintAt = this.t;
    }
  }

  /** 扇形：count 发均匀铺在 spreadDeg 里（原版 bullet_fan 几何）；
   *  extra（第九十轮）= 追加进每颗弹的 cfg（如大玉的 hitRadius/jadeColor） */
  fan(x, y, angle, count, spreadDeg, speed, color, radius, shape, extra) {
    const spread = (spreadDeg * Math.PI) / 180;
    for (let i = 0; i < count; i++) {
      const a = count === 1 ? angle : angle + spread * (i / (count - 1) - 0.5);
      this.emit({ x, y, angle: a, speed, color, radius, shape, ...extra });
    }
  }

  /** 环形：count 发均布（原版 bullet_circle），jitterDeg 可选随机抖动 */
  ring(x, y, count, baseAngle, speed, color, radius, jitterRad = 0, shape, extra) {
    for (let i = 0; i < count; i++) {
      const a = baseAngle + (i / count) * TAU + (jitterRad ? (this.rng.next() * 2 - 1) * jitterRad : 0);
      this.emit({ x, y, angle: a, speed, color, radius, shape, ...extra });
    }
  }

  /** 提前量瞄准（VS-HANDOFF 4.2：lead 0.6） */
  aim(x, y) {
    const p = this.P;
    const lead = LEAD;
    const tx = p.x + (p.vx || 0) * lead;
    const ty = p.y + (p.vy || 0) * lead;
    return Math.atan2(ty - y, tx - x);
  }

  teleportBoss() {
    // 第五十一轮：符卡自带的移动**保留** —— 用户："如果芙兰的符卡设置本身
    // 她会动就让她继续动，不要追着玩家打就行"。恢复 Cranberry 的瞬移；
    // 落点限定在舞台上带（中心偏上区域），纯位置演出，与玩家位置无关。
    if (!this.boss) return;
    this.boss.x = this.ax + (this.rng.next() * 2 - 1) * (ARENA.hw - 140);
    // 第五十二轮：瞬移带跟着舞台点上移（她整体"住"得更高了）
    this.boss.y = this.ay - 120 + (this.rng.next() * 2 - 1) * 150;
    this.boss.vx = 0;
    this.boss.vy = 0;
  }

  /**
   * 第五十一轮（替代第五十轮的 _pinBoss）：只压住速度、不钉位置。
   * Boss AI 的追踪已经靠实例 speed=0 死透，这里防的是击退/碰撞残留速度；
   * 符卡脚本对位置的改动（瞬移）原样保留。
   */
  _settleBoss() {
    if (!this.boss) return;
    this.boss.vx = 0;
    this.boss.vy = 0;
  }

  petSay(text, dur = 2600) {
    const pet = this.main.world.pet;
    // 第五十九轮：只出现一个框（用户："有时候对话框x2，就是会语音的
    // 和不会语音的都出现，改一下只出现一个。做个优先级判定啥的"）——
    // 之前是先画头顶气泡、再走猫娘计划的气泡+语音，两句同文同时弹。
    // 现在按优先级：① 猫娘计划的猫娘（气泡 + 语音，最显眼）
    //               ② 小小猫娘头顶气泡（无语音）
    //               ③ 粉色 toast（都不可用时的兜底）
    if (this.main._nekoSay?.(text, { key: `flandre:${text.slice(0, 8)}` })) return;
    if (pet?.say) pet.say(text, dur, this.main.world, 'heal');
    else this.main.ui.toast(`小小猫娘：${text}`, 'info', dur);
  }

  // ---------- 消弹（小小猫娘）----------

  tryManualPurge() {
    if (this.over || !this._hinted) return;
    if (this.purgeCd > 0) {
      this.petSay('人家还没准备好，再等等。');
      return;
    }
    this.startNova(false);
    // 第八十三轮："开消弹"音效 —— 用户："是小小猫娘芙兰boss战主动开
    // 消弹技能的音效"。只在手动触发时响；受击自动消弹（autoPurge）不响。
    audio.play('petPurge');
    this.purgeCd = PURGE_CD;
    this._purgeWasReady = false;
    // 第七十九轮：主动消弹给玩家 1 秒无敌 —— 用户："我发现你做的莱万汀
    // 的激光无法被主动消弹去除，所以被激光追上消弹了也必吃激光。修激光
    // 比较麻烦，直接给玩家1秒无敌让他能目押穿过激光。小小猫娘的消弹2秒
    // 基本不会中弹，所以无敌1秒不会对平衡造成太大问题。"
    // 只在**手动**触发时给（autoPurge 走的是受击无敌帧，不加）；
    // max 保护：不打断已有更长无敌。
    const p = this.main.player;
    if (p) p.invuln = Math.max(p.invuln ?? 0, 1);
  }

  /** 玩家中弹时的小小猫娘自动消弹（不计冷却，用户要求） */
  autoPurge() {
    if (this.over || this.phase === 'done') return;
    this.startNova(true);
  }

  startNova(auto) {
    const src = this.main.world.pet || this.P;   // 没宠物就以玩家为圆心
    // 第五十六轮：光圈扩散到最大后的持续时间 1 秒 → 2 秒
    // （用户："小小猫娘的消弹光圈扩散到最大之后持续时间从1秒加强到2秒"）；
    // 第七十四轮：自动消弹 0.5 秒 → 0.8 秒；
    // 第九十七轮：自动消弹 0.8 秒 → 1 秒 —— 用户："被动的小小猫娘消弹也
    // 持续1秒吧"，与芙兰战受击无敌帧 FLANDRE_HIT_INVULN 对齐，做到
    // "无敌多久、光圈护多久"。
    this.nova = { x: src.x, y: src.y, r: 8, hold: auto ? 1 : 2, auto };
    this.main.world.fx.nova(src.x, src.y, PURGE_RADIUS * 2, auto ? '#aef3ff' : '#ffe9a8', null);
    if (!auto) this.main.camera.shake(3, 0.15);
  }

  /**
   * 第一百零一轮：雾气粒子推进。
   * 常驻雾：慢漂 + 撞竞技场边界反弹，相位自然推进（渲染层按相位算 α 呼吸）；
   * 螺旋吸入光晕：按 60fps 帧时钟推进（原版运动学以帧为单位），到寿正寝就移除。
   */
  _mistUpdate(dt) {
    for (const m of this.mistAmbient) {
      m.x += m.vx * dt;
      m.y += m.vy * dt;
      if (m.x < ARENA.cx - ARENA.hw || m.x > ARENA.cx + ARENA.hw) m.vx *= -1;
      if (m.y < ARENA.cy - ARENA.hh || m.y > ARENA.cy + ARENA.hh) m.vy *= -1;
    }
    if (this.mistOrbs.length) {
      for (const o of this.mistOrbs) o.t += dt * 60;   // 帧时钟
      this.mistOrbs = this.mistOrbs.filter((o) => o.t <= o.life);
    }
  }

  _purgeUpdate(dt) {
    if (this.purgeCd > 0) {
      this.purgeCd -= dt;
      if (this.purgeCd <= 0 && !this._purgeWasReady) {
        this._purgeWasReady = true;
        this.petSay('人家准备好了，力量恢复了。');
      }
    }
    const n = this.nova;
    if (!n) return;
    if (n.r < PURGE_RADIUS) {
      n.r = Math.min(PURGE_RADIUS, n.r + 1000 * dt);   // 扩散很快（用户要求）
    } else {
      n.hold -= dt;
      if (n.hold <= 0) { this.nova = null; return; }
    }
    for (const b of this.main.world.enemyBullets.items) {
      if (b.dead || !b.flandre) continue;
      const dx = b.x - n.x, dy = b.y - n.y;
      if (dx * dx + dy * dy <= n.r * n.r) {
        b.dead = true;
        this.main.world.fx.sparkle?.(b.x, b.y, n.auto ? '#aef3ff' : '#ffe9a8');
      }
    }
  }

  // ---------- 主循环 ----------

  /** 第七十六轮：教学台词按档期逐句上屏（第 idx 句在 +2.6×idx 秒触发） */
  _hintTick() {
    const q = this._hintQueue;
    if (!q?.length) return;
    if (this.t - this._hintAt >= 2.6 * (this._hintDone ?? 0)) {
      const h = q.shift();
      this._hintDone = (this._hintDone ?? 0) + 1;
      this.petSay(h.text, h.dur);
    }
  }

  /**
   * 自适应弹量上限（199 轮）。dt 是真实帧间隔：平滑后 > 27ms（持续 <37fps）就每帧 −3 发往下收、直到 BULLET_CAP_MIN；
   * 平滑后 < 21ms（>47fps）再每帧 +1 发往回放，最高 BULLET_CAP。这样配置差的机器不会被 1200 拖垮，配置好的一直满额。
   * 忽略 dt>0.1 的样本（切后台/卡顿尖峰），免得一次尖峰就把上限砍掉。
   */
  _adaptCap(dt) {
    if (!(dt > 0) || dt > 0.1) return;
    this._ema = (this._ema ?? 0.016) * 0.96 + dt * 0.04;
    if (this._ema > 0.027) this._capNow = Math.max(BULLET_CAP_MIN, this._capNow - 3);
    else if (this._ema < 0.021) this._capNow = Math.min(BULLET_CAP, this._capNow + 1);
  }

  update(dt) {
    if (this.over) return;
    this.t += dt;
    this._adaptCap(dt);
    this._purgeUpdate(dt);
    this._mistUpdate(dt);
    this._hintTick();

    // 序章演出
    if (this.phase === 'prologue') {
      this.tint.alpha = clamp(this.t / FADE_IN, 0, 1);   // 3 秒渐黑
      if (this.t >= SILENCE_END && !this._warned) {
        this._warned = true;
        this.petSay('注意，有什么危险的家伙靠近了！');
      }
      if (this.t >= SHAKE_AT && this.t < SHAKE_AT + 2.5) {
        // 屏幕轻微晃动 2~3 秒
        this.main.camera.shake(4.5, 0.12);
      }
      if (this.t >= CARD_AT && !this._named) {
        this._named = true;
        this.spellName = CARD_INFO.prologue.name;
        audio.playScene?.('flandre');                   // U.N. Owen
        this._beginSpell('prologue', PROLOGUE_CARD_TIME);
      }
      // 199 轮（用户："教学30秒的弹幕……ai猫娘完全不动直接吃弹幕到全死"）：序章是无奖励的教学卡，队友 AI 的受伤减半。
      // 躲避本身已改善（见 neko-pilot 序章分支：口袋待命，旧行为站正中心 17 次→5 次），这里再给一道保险。
      this._setPrologueEase(0.5);
      if (this.spell) this._tickSpell(dt);
      if (this.t >= PROLOGUE_END) { this._setPrologueEase(1); this._spawnFlandre(); }
      return;
    }

    // 正式符卡战
    if (this.phase === 'cards') {
      this._settleBoss();   // 第五十一轮：只压速度；符卡自带移动保留
      this._wanderUpdate(dt);   // 第六十一轮：舞台点附近慢速游走（上半屏）
      this._timeoutTick();      // 第六十二轮：最后 10 秒滴答
      // 第六十五轮：120 秒到点必进 Extra Phantom —— 正式机制（原 3 张
      // 保底门槛已按用户要求去掉），4 张打完则提前见。
      this.cardsElapsed = (this.cardsElapsed ?? 0) + dt;
      if (EP_AT_TIMEOUT && this.cardsElapsed >= CARDS_TOTAL * CARD_TIME) {
        this._enterExtra();
        return;
      }
      if (this.lockLeft > 0) {
        this.lockLeft -= dt;
      } else {
        this._tickSpell(dt);
      }
      const lost = (this.phaseStartHp - (this.boss?.hp ?? 0))
        + (this.cloneDamage ?? 0);   // 第六十三轮：Four of a Kind 期间伤害记在分身账上
      const beaten = lost >= FLANDRE_MAX_HP * CARD_HP_FRAC;
      const timeUp = this.spell && this.spell.t >= this.spell.dur;
      if ((beaten || timeUp) && this.lockLeft <= 0) {
        if (beaten) this.cardsDone++;
        else {
          // 第一百一十九轮：超时芙兰自己扣除本卡剩余血量 —— 用户："或者
          // 改成超时芙兰自己扣除当前符卡的剩余血量。……比如玩家打了10%
          // 的血到了30秒，芙兰扣掉剩下没扣的10%开下一张卡。然后EP还是
          // 永久锁20%血持续70秒。" 扣到本卡地板（起点 −20% 总血），下一
          // 张卡的 20% 从分段线重新起算 —— 伤害进度永不因超时白打。
          const floor = this._hpFloor();
          if (this.boss && this.boss.hp > floor) this.boss.hp = floor;
        }
        // 第七十二轮：fault = "收符卡失败"的嘲讽音 —— 只在普通 4 卡
        // **超时收失败**时播（用户："普通芙兰的4个卡可以打死可以播这个，
        // 开头的那个30秒演出和最后的70秒锁血的3张卡不播放fault"）；
        // 打败（收成功）不播；序章演出不走这里；EP 三张锁血卡在下面
        // extra 分支，已不再播。
        if (!beaten) audio.play('cardEnd');
        this._nextCard();
      }
      return;
    }

    // Extra Phantom：无敌抱腿坐姿存活战（第六十一轮：20 + 20 + 30 = 70 秒）
    if (this.phase === 'extra') {
      this._settleBoss();   // 第五十一轮：同上
      // 第六十一轮：EP 不游走 —— 用户："抱着腿的姿势动起来反而怪"
      this._timeoutTick();      // 第六十二轮：最后 10 秒滴答
      this.extraLeft -= dt;
      this.extraCardTimer -= dt;
      if (this.extraCardTimer <= 0 && this.extraCardsLeft > 0) {
        this.extraCardsLeft--;
        // 第六十一轮：前两张各 20 秒，最后的「弹幕的心脏」30 秒
        //（用户："这张卡时间从20秒变到30秒，也就是芙兰的Extra Phantom
        // 模式总时间变为70秒"）
        this.extraCardTimer = this.extraCardsLeft === 0 ? EXTRA_HEART_TIME : EXTRA_CARD_TIME;
        // 第七十二轮：EP 三张是锁血卡（100% 收复失败），不再播 fault ——
        // 用户："最后这三个卡的芙兰是锁血70秒，符卡是100%收复失败的。
        // 这个音效是有点嘲讽感觉得"。
        // 第五十六轮：EP 只放**专属 3 张**且顺序释放（fragile → bloodstain
        // → heart）—— 用户："芙兰进入Extra Phantom模式时释放的是莱万汀
        // 符卡，而不是她的Extra Phantom专属的3张符卡。改成只释放那3个
        // 而且是顺序释放。" 随机池是正式战用的，EP 不再抽。
        if (!this.extraQueue || this.extraQueue.length === 0) {
          this.extraQueue = [...EXTRA_SPELLS];
        }
        this._beginSpell(this.extraQueue.shift(), EXTRA_CARD_TIME, 1.25);
        this._clearBullets();
      }
      this._tickSpell(dt);
      if (this.extraLeft <= 0) this._finish('beat');   // 存活 60 秒 → 满奖励
    }
  }

  _beginSpell(id, dur, speedMul = 1) {
    this.spell = { id, name: CARD_INFO[id]?.name ?? id, t: 0, dur, st: {}, speedMul };
    this.spellName = this.spell.name;
    this.cloneDamage = 0;   // 第六十三轮：分身伤害账每张卡清零
    this._removeClones();   // 第六十三轮：上一张的四重存在分身退场
    // 第一百零一轮：卡开场 6 个粉白光晕螺旋吸入 —— effectId 17 Attract：
    // 从 BOSS 周围 256px 随机角生成，60 帧（1s）收缩到中心；与 power0
    // 充能音（spellCard 里已同步触发）构成每张符卡开场的标志性演出。
    for (let i = 0; i < 6; i++) {
      this.mistOrbs.push({
        th: Math.random() * Math.PI * 2,   // 装饰粒子不碰游戏 RNG
        t: 0, life: 60,
        spin: i % 2 ? 1 : -1,      // 相邻反向，交错感
      });
    }
    if (this.mistOrbs.length > 24) this.mistOrbs.splice(0, this.mistOrbs.length - 24);
    // 第五十四轮（用户反馈 3）：符卡启动音效
    audio.play('spellCard');
    // 第五十四轮（用户反馈 4）：莱万汀固定回舞台点 ——
    // 用户："如果她在角落释放莱万汀会变成不可躲避的弹幕，因为玩家
    // 绕不过去只能硬吃弹幕等死。" 十字旋转弹幕必须以舞台中心为轴。
    if (id === 'laevateinn' && this.boss) {
      this.boss.x = this.ax;
      this.boss.y = this.ay - STAGE_DY;
      this.boss.vx = 0;
      this.boss.vy = 0;
    }
  }

  _tickSpell(dt) {
    const sp = this.spell;
    if (!sp) return;
    sp.t += dt;
    SPELLS[sp.id].tick(sp, dt, this);
    this._cullOutside();
    // 姿势贴图：Lævateinn 卡用专属攻击姿势（192），
    // Extra Phantom 用抱腿坐姿三帧循环（202/205/208，0.3s 一帧），
    // 其余时候回本体（172）。用户亲自核对的编号表。
    if (this.boss) {
      if (this.phase === 'extra') {
        this.boss.spriteId = SIT_FRAMES[Math.floor(this.t / 0.3) % SIT_FRAMES.length];
      } else if (sp.id === 'laevateinn') {
        this.boss.spriteId = 'flandre_laevateinn';
      } else {
        // 常态：172~175 翅膀慢慢循环（0.35s 一帧）
        this.boss.spriteId = IDLE_FRAMES[Math.floor(this.t / 0.35) % IDLE_FRAMES.length];
      }
    }
  }

  /** 序章减伤：对 P2~P4（队友 AI）生效，P1 不动（P1 要么是人类，要么有代打光环自带 0.25×） */
  _setPrologueEase(v) {
    const list = this.main.world.partners?.() ?? [];
    for (const pl of list) if (pl && pl.flandreEase !== v) pl.flandreEase = v;
  }

  /**
   * 弹飞出场地外 CULL_MARGIN 就回收（第一百九十九轮）。
   *
   * 用户："有些符卡弹幕飞行时间过短，子弹飞一半就消失了，你可以拉长一些。"
   * 根因：真实游戏里弹只在【射程用尽】或飞出整个世界边界(±1280)时才死 —— 没有"飞出场地就回收"。
   * 为了不让弹在场外白占 750 的预算，几张卡把射程压得很短(520/560/640/700/900)，结果弹在场地里飞到一半就凭空消失。
   * 现在：射程放开到"够飞出场地"，由这里在弹离开场地 140px 后统一回收 —— 弹的寿命 = 它在场地里能飞的全部距离。
   * （符卡模拟器 tools/_spell-sim.mjs 早就按这个规则算的，现在真实游戏与模拟一致。）
   * 反弹墙弹(495)在场地边缘反弹，不会越过边界；侧边刷出的弹起点在场外 40px，在 140px 余量内。
   */
  _cullOutside() {
    const items = this.main.world.enemyBullets?.items;
    if (!items) return;
    const cx = this.ax, cy = this.ay, hw = ARENA.hw + CULL_MARGIN, hh = ARENA.hh + CULL_MARGIN;
    for (let i = 0; i < items.length; i++) {
      const b = items[i];
      if (b.dead || !b.flandre) continue;
      if (Math.abs(b.x - cx) > hw || Math.abs(b.y - cy) > hh) b.dead = true;
    }
  }

  _pickCard() {
    if (this.cardsQueue.length === 0) {
      this.cardsQueue = [...CARD_POOL];
      for (let i = this.cardsQueue.length - 1; i > 0; i--) {
        const j = Math.floor(this.rng.next() * (i + 1));
        [this.cardsQueue[i], this.cardsQueue[j]] = [this.cardsQueue[j], this.cardsQueue[i]];
      }
    }
    return this.cardsQueue.pop();
  }

  /** 芙兰登场：序章结束（用户："30秒结束符卡结束，芙兰从屏幕中心偏上方出现"） */
  _spawnFlandre() {
    const w = this.main.world;
    this.spell = null;
    // 第一百轮：魔法阵计时起点（渲染层据此做 5 秒慢淡入）
    this.circleBornAt = this.t;
    this.circleAnchor = null;
    this.circleOutAt = null;
    const def = getEnemy('flandre');
    // 第五十二轮：舞台点再往上抬（用户："位置在往上一点点，还是有点低"）
    const e = new Enemy(def, this.ax, this.ay - STAGE_DY,
      { hp: 1, bossHp: 1, damage: 1, speed: 1, xp: 1, materials: 1, bulletRange: 1 }, this.rng);
    // 第五十轮：站桩舞台点 —— def 上的 speed=120 是给移速地板检查看的，
    // 实例归零，位置由 _pinBoss 每帧钉死，Boss AI 永远追不到人。
    e.speed = 0;
    // 第五十轮：芙兰出现 → 立刻恢复玩家攻击
    //（用户："把不攻击的设置改成芙兰出现就恢复攻击"）
    this.main.player.noAttack = false;
    // 她的阶段由本控制器管：标准锁血门（33%/66%）与 25% 分段冲突，
    // 直接把两个门标记为已用（applyDamageLock 会跳过），登场无敌清零。
    e.bossPhase.invulnTimer = 0;
    e.bossPhase.gatesUsed = { 2: true, 3: true };
    // 第六十七轮：把普通 Boss 的**阶段推进机器**整个焊死 ——
    // 用户："芙兰第一张卡结束后会莫名播报她进入第二阶段……过几秒播报她
    // 进入了第三阶段，过几秒直接进商店。疑似变成了'普通boss'机制。"
    // 根因：def.behavior='boss' → Enemy._boss 每帧跑 bossPhase.update
    // (hpRatio, timeRatio)。玩家 DPS 融掉 33%/66% 血线（或战斗期间注入
    // 的过期 bossTimeRatio）就触发普通 Boss 的转阶段：播报 + 5 秒无敌
    // （渲染层画成护盾）+ 冲锋 mods —— 全是普通 boss 行为，不属于符卡战。
    // gatesUsed 只锁 applyDamageLock 的卡点，拦不住 update() 的播报。
    // 直接把 update 换成永 0：阶段永远 1，没有任何转阶段演出/无敌/mods。
    e.bossPhase.update = () => 0;
    // 拦截 takeDamage：序章后她的"可否被打、最多打到哪"由控制器说了算
    //（切符卡锁 3 秒 / Extra Phantom 全程无敌 / 血量地板锁血不死）。
    const realTake = e.takeDamage.bind(e);
    e.takeDamage = (amount, opts) => {
      // 第七十一轮：去掉 fourkindActive 拦截 —— 用户要求四重存在期间
      // 第七十三轮：四重存在期间本体照样可受击（她只是隐身，hitbox 扩大
      // 覆盖分身轨道 —— 自动攻击锁定"看不见但系统看得到"的本体）。
      if (this.over || this.lockLeft > 0 || this.phase === 'extra') {
        return { blocked: true, dmg: 0, reason: 'flandre', gated: 0 };
      }
      // 第八十六轮：血量地板（锁血）—— 伤害最多把 hp 打到地板值，
      // Enemy 永远到不了 0，不会触发死亡副作用（旧版"锁 1 血"是先挨打
      // 再复位，中途会触发 Enemy 死亡结算 → 直接过关跳过 EP，正是
      // 用户报告的"秒杀芙兰没进 EP 也没拿莱万汀"的根因）。
      // 地板 = max(总血 20%, 本卡起点 − 20% 总血)：
      //   第 1 卡 80万→64万、第 2 卡 64万→48万、第 3 卡 48万→32万、
      //   第 4 卡 32万→16万；低 DPS 卡超时时起点更高、地板跟着抬高。
      //   EP（最后 20%）在上面整个拦截，16 万全程锁死。
      const floor = this._hpFloor();
      const allowed = e.hp - floor;
      if (allowed <= 0) {
        return { blocked: true, dmg: 0, reason: 'flandre', gated: 0 };
      }
      const res = realTake(Math.min(amount, allowed), opts);
      // 保险丝：理论上地板钳制后 hp 不可能 ≤ 0，留着兜底
      if (!res?.blocked && e.hp <= 0) { e.hp = 1; e.dead = false; }
      return res;
    };
    w.enemies.add(e);
    this.boss = e;
    w.fx.shockwave?.(e.x, e.y, 120, COLORS.red);
    w.fx.nova(e.x, e.y, 260, COLORS.gold, null);
    this.main.camera.shake(6, 0.5);
    this.phase = 'cards';
    this._nextCard(true);
  }

  /**
   * 第八十六轮：当前血量地板（锁血线）。
   * 用户："每打掉20%血锁血切换符卡，前80%血对应4张卡，最后20%血全程
   * 锁血进EP……相当于打芙兰64万血。"
   *   · 'cards' 阶段：max(总血 20%, 本卡起点 − 总血 20%)
   *     —— 高 DPS 时每张卡恰好锁在分段线上（80/64/48/32 万），切卡后
   *     从锁血线继续；低 DPS 卡超时时起点更高、地板跟着抬高。
   *   · 'extra' 阶段不需要地板：takeDamage 整体拦截，EP 血量全程锁死。
   */
  _hpFloor() {
    const start = this.phaseStartHp || FLANDRE_MAX_HP;
    return Math.max(FLANDRE_MAX_HP * 0.2, start - FLANDRE_MAX_HP * CARD_HP_FRAC);
  }

  /** 切符卡：清全屏弹幕 + 锁血 3 秒（用户要求） */
  _nextCard() {    this._clearBullets();
    this.lockLeft = SWITCH_LOCK;
    // 第六十一轮：每次切符卡把芙兰拉回默认位置（舞台点）——
    // 用户："每次切换符卡都会重新把芙兰拉回默认位置，然后她可以在自己的
    // 默认位置中心点附近随机移动……尽量是上半个屏幕随机移动，速度不要太快。"
    if (this.boss) {
      this.boss.x = this.ax;
      this.boss.y = this.ay - STAGE_DY;
      this.boss.vx = 0; this.boss.vy = 0;
    }
    if (this.cardsDone >= CARDS_TOTAL) {
      // 第六十五轮：4 张全部打败 → 提前进 EP（原来是打过 3 张就进，
      // 用户："额外的芙兰3张卡见面条件去掉，只要玩家在120秒内没被打死
      // 就能见到这三张卡……120秒内打败4张卡提前见，打不过就120秒到时见"）
      this._enterExtra();
      return;
    }
    const prevNo = this.spell?.st?._cardNo ?? 0;
    if (prevNo >= CARDS_TOTAL) {
      // 体检修复（第九十二轮）：第四张卡也打完但 cardsDone 未满 4 的路径
      //（第 4 卡 30 秒超时）此前走 _finish('partial') —— 速通玩家拿到
      // 3 倍奖励却见不到 EP、没有莱万汀；反而全超时苟到 120 秒的玩家
      // 100% 进 EP（EP_AT_TIMEOUT）。奖励与表现倒挂，也违背第六十五轮
      // "120 秒内活着就能见到 EP"的承诺。现在统一进 EP，'partial'
      // 不再有合法入口。
      this._enterExtra();
      return;
    }
    const id = this._pickCard();
    this._beginSpell(id, CARD_TIME);
    this.spell.st._cardNo = prevNo + 1;
    this.phaseStartHp = this.boss?.hp ?? FLANDRE_MAX_HP;
  }

  /**
   * 第六十一轮：符卡战期间芙兰在舞台点附近慢慢游走 ——
   * 用户："她可以在自己的默认位置中心点附近随机移动……尽量是上半个屏幕
   * 随机移动，速度不要太快。" 规则：
   *   · 每 2~4 秒在舞台点 ±130px 内挑一个新目标点，42px/s 慢慢挪过去
   *   · y 一律钳在 -480 ~ -230（屏幕上半区，绝不往下走）
   *   · 莱万汀例外：十字弹幕必须以舞台中心为轴（第五十四轮的既定要求）
   *   · Extra Phantom 不游走（用户："抱着腿的姿势动起来反而怪"）
   *   · cranberry 的随机瞬移不受影响（teleportBoss 照常，游走从落点继续）
   */
  _wanderUpdate(dt) {
    const boss = this.boss;
    if (!boss) return;
    if (this.spell?.id === 'laevateinn') return;   // 莱万汀钉在舞台中心
    const homeY = this.ay - STAGE_DY;
    const st = this.spell?.st ?? (this.spell = { st: {} }).st;
    st.think = (st.think ?? 0) - dt;
    const dx = (st.tx ?? boss.x) - boss.x;
    const dy = (st.ty ?? boss.y) - boss.y;
    if (st.think <= 0 || (dx * dx + dy * dy) < 100) {
      st.tx = this.ax + (this.rng.next() * 2 - 1) * 130;
      st.ty = Math.max(-480, Math.min(-230, homeY + (this.rng.next() * 2 - 1) * 110));
      st.think = 2 + this.rng.next() * 2;
    }
    const d = Math.hypot(dx, dy) || 1;
    const step = Math.min(42 * dt, d);
    boss.x += (dx / d) * step;
    boss.y += (dy / d) * step;
  }

  _clearBullets() {
    for (const b of this.main.world.enemyBullets.items) b.dead = true;
  }

  /**
   * 第六十三轮：Four of a Kind 的分身退场 —— 直接从敌人数组移除
   * （不走死亡流程：不掉经验不掉材料）。同时本体解除隐身/锁定。
   */
  _removeClones() {
    // 第七十三轮：分身是纯视觉发射器（不在敌人数组里），退场 = 清空列表；
    // 本体的隐身 / 大 hitbox / 接触伤害归零 三件套在此复原。
    this.fourkindActive = false;
    this._cloneLock = 0;
    if (this.boss) {
      this.boss.flandreHidden = false;
      if (this._bossRadiusSave != null) this.boss.radius = this._bossRadiusSave;
      if (this._bossDamageSave != null) this.boss.damage = this._bossDamageSave;
      this._bossRadiusSave = null;
      this._bossDamageSave = null;
    }
    const list = this.main.world.enemies.items;
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].isClone) list.splice(i, 1);
    }
  }

  /**
   * 第九十七轮：HUD 顶部（原波次计时器位置）的分段倒计时 —— **纯显示**，
   * 不参与任何结算/判定/切卡（原 waveTimeLeft 完全不动，用户："省的有
   * bug，比如到0秒自动过关之类的"）。
   * 用户原话："每张开启的时候在原位置挂一个新的30秒计时，到时或者芙兰
   * 被打的换卡就显示下一个30秒。然后到EP芙兰模式则是显示20/20/30。
   * 方便初见玩家知道他得坚持多久而不是绝望。然后芙兰战结束记得把这个
   * 计时器也去掉或者隐藏。"
   * 序章（她登场前的 30 秒演出弹）也挂一个 ？？？ 计时 —— 初见玩家
   * 至少知道"撑过这段就好"，不剧透她是谁。
   * 返回 { left, total, label }；战斗结束/未开始返回 null（HUD 据此隐藏）。
   */
  getDisplayTimer() {
    if (this.over || this.phase === 'done') return null;
    if (this.phase === 'prologue') {
      // 前 8.5 秒只有渐黑/静默/警告、没有弹幕 —— 不挂表（压迫感拉满）；
      // 第一颗弹幕出现（CARD_AT，_beginSpell('prologue')）才挂 ？？？ 30 秒
      if (!this.spell) return null;
      return {
        left: Math.max(0, this.spell.dur - this.spell.t),
        total: this.spell.dur,
        label: '？？？',
      };
    }
    if (this.phase === 'cards') {
      const no = Math.min(CARDS_TOTAL, this.spell?.st?._cardNo ?? 1);
      return {
        left: this.spell ? Math.max(0, this.spell.dur - this.spell.t) : CARD_TIME,
        total: CARD_TIME,
        label: `符卡 ${no}/${CARDS_TOTAL}`,
      };
    }
    if (this.phase === 'extra') {
      // EP 三段 20/20/30：extraCardTimer 正是"距下一段/结束"的秒数，
      // 与 extraLeft(70) 天然同步（20+20+30=70）。
      // 第九十九轮：标签不缩写成 EP —— 用户："把EP全名写出来，不然
      // 新玩家不知道是啥。写成ExtraPhantom"
      const seg = Math.min(3, Math.max(1, 3 - (this.extraCardsLeft ?? 3)));
      const total = seg === 3 ? EXTRA_HEART_TIME : EXTRA_CARD_TIME;
      return { left: Math.max(0, this.extraCardTimer ?? 0), total, label: `ExtraPhantom ${seg}/3` };
    }
    return null;
  }

  /**
   * 第六十二轮：最后 10 秒滴答（timeout.wav，每秒一声，GUI 层行为）。
   * 符卡战的"卡剩 10 秒"和 EP 的"存活剩 10 秒"都算。
   */
  _timeoutTick() {
    let left = null;
    if (this.phase === 'cards' && this.spell) left = this.spell.dur - this.spell.t;
    else if (this.phase === 'extra') left = this.extraLeft;
    if (left !== null && left > 0 && left <= 10) {
      const c = Math.ceil(left);
      if (this._tickSec !== c) { this._tickSec = c; audio.play('cardTick'); }
    } else {
      this._tickSec = null;
    }
  }

  /** Extra Phantom：BGM 换曲、坐姿、背景黑 → 暗红、无敌 60 秒 */
  _enterExtra() {
    this.phase = 'extra';
    this._removeClones();   // 第六十三轮：EP 开始前分身退场（本体独自抱腿）
    this.extraLeft = EXTRA_TIME;
    this.extraCardTimer = 0;      // 立刻开第一张（update 里会重置成 20）
    this.extraCardsLeft = 3;      // 每 20 秒一张，60 秒 3 张
    // 第五十六轮：EP 专属队列在这里初始化 —— 第一张就是 fragile，
    // 后面 bloodstain → heart，不再抽随机池（之前第一张会漏成随机卡）
    this.extraQueue = [...EXTRA_SPELLS];
    this.spellName = 'Extra Phantom';
    audio.playScene?.('flandre_ex');
    // 抱腿坐姿由 _tickSpell 每帧按时间切帧（this.t 驱动循环）
    this.main.ui.toast('芙兰朵露展开了她真正的力量……', 'bad', 3000);
  }

  _finish(outcome) {
    if (this.over) return;
    this.over = true;
    this.outcome = outcome;
    this.phase = 'done';
    // 第一百轮：魔法阵退场 —— 记下锚点与时刻，渲染层做 2 秒淡出
    //（原版 script 中断后 120 帧 = 2s）。boss 置空前先留个影。
    this.circleOutAt = this.t;
    if (this.boss) this.circleAnchor = { x: this.boss.x, y: this.boss.y };
    // 第六十二轮：大爆炸 / BOSS 崩坏音（enep01，effect_sound 18）
    audio.play('flandreCrash');
    this._removeClones();   // 第六十三轮：分身退场
    this._clearBullets();
    if (this.boss) this.boss.dead = true;   // 静默移走，不掉任何东西
    this.boss = null;
    this.spell = null;
    this.spellName = '';
    this.tint.alpha = 0;
    this.onDone(outcome);
  }

  /** 玩家死了（任何阶段）：整场作废 —— 第六十五轮起 EP 阶段死亡另有结算 */
  onPlayerDeath() {
    this.petSay('呜……快逃啊——！');
    // 第六十五轮：用户："额外模式死了拿经验和金币，没死拿莱万汀" ——
    // EP 阶段死亡给经验+材料（新 outcome 'extraDeath'）；正式卡阶段死亡
    // 依旧什么都没有。
    this._finish(this.phase === 'extra' ? 'extraDeath' : 'death');
  }

  /**
   * 背景渐变，每帧由 main 调。
   * 第七十二轮按用户再改法重做："背景还是错了，黑色慢慢转换为暗红是
   * 暗红色，纯色块变化，不要有图。等芙兰出来后再慢慢浮现图，而且图不是
   * 贴在整个屏幕空间大小，是芙兰boss战斗的玩家活动区域大小，就是你用
   * 红框圈起来的那个大小。"
   *   · 序章：纯色块渐变 —— 3 秒渐黑（tint.alpha，演出驱动）后，30 秒
   *     弹幕期间 tint.color 从 #000 慢慢过渡到 TINT_CARDS 暗红（速率
   *     1/30）；**不出图**（bg.eff 恒 0）。
   *   · 芙兰出现（cards）/ EP：eff07_nc_4k 以 1/3 速率慢慢浮现到 100%；
   *     渲染层把图**裁进空气墙内的玩家活动区域**（ARENA 矩形 × 相机），
   *     墙外保持纯色暗红。
   *   · 不再转换；战斗结束 world.flandreFight 置空，渲染层自动回到常规
   *     背景 —— 无残留。
   */
  tintUpdate(dt) {
    const bg = this.bg ?? (this.bg = { eff: 0 });
    const step = (v, target, rate) => {
      const d = target - v;
      return Math.abs(d) <= rate * dt ? target : v + Math.sign(d) * rate * dt;
    };
    if (this.phase === 'prologue') {
      // 纯色渐变期：30 秒弹幕期间 #000 → 暗红（TINT_CARDS），绝不出图
      if (this.t >= CARD_AT) {
        bg.eff = 0;
        this._lerpTint(TINT_CARDS, 1 / 30, dt);
      }
    } else {
      // 芙兰出现（cards）/ Extra Phantom：3 秒内浮现到 100%，之后不再转换
      bg.eff = step(bg.eff, 1, 1 / 3);
      this._lerpTint(this.phase === 'extra' ? TINT_EP : TINT_CARDS, 1 / 3, dt);
    }
  }

  /** tint.color 线性过渡（第七十二轮抽出来给序章慢速纯色渐变复用） */
  _lerpTint(target, rate, dt) {
    const t = this.tint;
    t.alpha = Math.min(1, t.alpha + dt / 3);
    if (this._tintTarget !== target) {
      this._tintTarget = target;
      this._tintMix = 0;
      this._tintFrom = t.color;
    }
    if (this._tintMix >= 1) { t.color = target; return; }
    this._tintMix = Math.min(1, this._tintMix + rate * dt);
    const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
    const from = hex(this._tintFrom);
    const to = hex(target);
    t.color = '#' + from.map((v, i) => Math.round(v + (to[i] - v) * this._tintMix)
      .toString(16).padStart(2, '0')).join('');
  }
}
