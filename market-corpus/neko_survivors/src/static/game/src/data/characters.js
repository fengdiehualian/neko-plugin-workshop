// ============================================================
// 角色定义
// 主角为猫娘计划的 Yui；其余为可解锁的猫娘伙伴。
// stats 为「基础值覆盖」，mods 为「初始修正」。
//
// 第十三轮：按用户要求把 7 个已有角色改成猫娘姐妹的真名，
// 并新增炮台专精的「neon宴会」（即现在的 Neon）。**id 一律不动** —— 存档、解锁记录、
// 角色专精表（archetypes.js）、立绘文件名都挂在 id 上，
// 改 id 会把玩家的解锁进度全部作废。只有显示用的 name/glyph 变了。
// ============================================================

export const CHARACTERS = [
  {
    id: 'yui',
    name: '悠怡',
    role: '均衡型 · 主角',
    color: '#ff7bac',
    accent: '#ffd6e6',
    shape: 'cat',
    glyph: '悠',
    desc: '猫娘计划的主角。各项能力均衡，没有明显短板，适合任何流派。',
    // 基础值覆盖
    stats: {},
    // 初始修正：加法
    mods: { maxHp: 4, moveSpeed: 5 },
    pct: {},
    startWeapon: 'cat_paw',
    unlock: null,
  },
  {
    id: 'mimi',
    name: '小八',
    role: '狂战型',
    color: '#ff5f6d',
    accent: '#ffc9cd',
    shape: 'cat',
    glyph: '八',
    desc: '好战的小猫。伤害更高，但体质偏弱。适合近战莽夫打法。',
    stats: {},
    mods: { maxHp: -4 },
    // 第四十五轮：基础 +5% 吸血（本作吸血 = 每次命中 x% 概率回 1 滴血）。
    // 用户原话："给小八做个基础加强：基础属性里加上5%吸血。"
    pct: { damage: 15, attackSpeed: 10, dodge: -5, lifesteal: 5 },
    startWeapon: 'claw_glove',
    unlock: { type: 'wave', value: 5, desc: '到达第 5 波' },
  },
  {
    id: 'luna',
    // 第一百六十轮：改名 —— 用户："我之前以为neon是有2套衣服，结果其实是
    // 2个人。现在的neon名字改成Roki，然后neon宴会变成Neon"。
    // **id 保持 luna 不动**（存档/解锁记录/立绘映射全挂 id）。立绘文件
    // neon.png 是资源路径，同样不动。
    name: 'Roki',
    role: '法师型',
    color: '#b794f6',
    accent: '#e3d6ff',
    shape: 'cat',
    glyph: 'N',
    desc: '沉迷魔法的猫娘。元素伤害突出，射程更远，但身板很脆。',
    stats: {},
    mods: { maxHp: -6, pickupRange: 15 },
    // 第四十六轮：基础移速 +8% ——
    // 用户原话："给neon也做个基础加强：加一个移速+8%的基础能力。"
    // "不然neon噩梦难度开局表现也有点糟糕，她无法甩开敌人，导致技能
    // 无法生效。"（她的技能是"离敌人越远伤害越高"，跑不開就白板。）
    // （历史注释里的"neon"指本角色，即现在的 Roki。）
    pct: { elemDamage: 25, rangedDamage: 10, attackSpeed: -5, moveSpeed: 8 },
    startWeapon: 'star_wand',
    unlock: { type: 'wave', value: 8, desc: '到达第 8 波' },
  },
  {
    id: 'shiro',
    name: '开发猫猫',
    role: '坦克型',
    color: '#5fd3e8',
    accent: '#cbf3fa',
    shape: 'cat',
    glyph: '开',
    desc: '毛茸茸的护盾猫。血量厚、护甲高，但走得慢、伤害低。',
    stats: {},
    // 第二十四轮：基础生命回复 0.4 → 0.8（用户："再给他一点点小buff"）
    mods: { maxHp: 12, armor: 3, hpRegen: 0.8 },
    pct: { moveSpeed: -10, damage: -10 },
    startWeapon: 'fish_shield',
    unlock: { type: 'wave', value: 12, desc: '到达第 12 波' },
  },
  {
    id: 'nana',
    name: '悠怡萌妹',
    role: '幸运型',
    color: '#ffc857',
    accent: '#fff0c9',
    shape: 'cat',
    glyph: '萌',
    desc: '被幸运女神眷顾的猫。掉落和商店品质极佳，收获更多材料。',
    stats: {},
    mods: { harvesting: 4, pickupRange: 30 },
    pct: { luck: 35, damage: -5 },
    // 第四十七轮：商店刷新费用减半 ——
    // 用户原话："给悠怡萌妹一个能力加强：商店刷新费用减半。"
    //（幸运型靠刷商店淘高品质货，刷新费减半和她的人设/玩法完全咬合。
    // 消费点在 shop.js 的 rerollCost getter，main.js 开局/读档时写入。）
    rerollCostMul: 0.5,
    startWeapon: 'lucky_coin',
    unlock: { type: 'wave', value: 15, desc: '到达第 15 波' },
  },
  {
    id: 'kuro',
    name: '库洛',
    role: '刺客型',
    color: '#8b93b0',
    accent: '#d7dced',
    shape: 'cat',
    glyph: '库',
    desc: '影子里潜行的黑猫。暴击极高、攻速快，但血量很低。',
    stats: {},
    mods: { maxHp: -8 },
    // 第一百五十六轮：基础闪避 8% → 12%（用户："库洛的基础闪避从+8%改到
    // 12%，稍微削弱一点她受到的伤害，把一击线拉高点"）；被动承伤 20% → 12%
    // 见 char-skills.js 的 damageTaken。
    pct: { critChance: 20, critDamage: 40, attackSpeed: 15, dodge: 12 },
    startWeapon: 'shadow_dagger',
    unlock: { type: 'wave', value: 18, desc: '到达第 18 波' },
  },
  // ============================================================
  // 第十一轮新增：悠怡洛丽塔（有立绘的第一个角色）
  //
  // 用户原话："尝试生成一个角色图并且实装她看看，尽量画风不要违和，
  //   角色人设我之前告诉过你了在猫娘姐妹的文件夹。"
  //
  // 人设来源：E:\猫娘姐妹\悠怡洛丽塔.jpg。素材夹里**没有文字设定**，
  // 人设只存在于图里，所以用视觉模型读出了配色和气质，写进下面的注释：
  //   发色/尾巴 深灰蓝 #3C4253、瞳色 灰蓝 #506A8C、
  //   服装 奶白 #F7F3EC + 低饱和灰蓝 #A4C0D6 + 哥特黑 #232731
  //   气质：内向、乖巧、精致如洋娃娃、温婉柔弱
  //
  // 定位做成**支援/续航型**，因为人设是"想要保护她"的治愈系 ——
  // 给一个莽夫型角色配上"柔弱乖巧"的立绘会违和。
  // 数值上偏生存和回复，输出偏低，逼玩家用"稳"的打法。
  //
  // 立绘：assets/characters/char_youyi.png（全身站姿，带 alpha）。
  // 和别的贴图一样是**可选增强** —— 加载不到就回落程序画的几何小猫。
  //
  // 第十三轮改名：显示名从「悠怡」改成「悠怡洛丽塔」。
  // 原因是主角 yui 也叫悠怡，两个角色同名会在角色选择界面里
  // 出现两个"悠怡"，玩家分不清。用立绘的版本名做区分。
  // ============================================================
  {
    id: 'youyi',
    name: '悠怡洛丽塔',
    role: '支援型',
    color: '#a4c0d6',
    accent: '#f7f3ec',
    shape: 'cat',
    glyph: '洛',
    desc: '温柔内向的猫娘妹妹。治疗与回复能力出众，护盾厚实，但输出偏低。',
    stats: {},
    // 生存向：血厚、回复快、拾取范围大（配合"照顾人"的人设）
    // **护甲必须放 mods**：armor 是 kind:'flat' 的属性，结算式是
    // `(base+flat)*(1+pct)`；base 和 flat 都是 0 时再乘多少 pct 还是 0。
    // 第十一轮它被写在 pct 里，实际是**完全无效**的一条 ——
    // 文档里还显示成"护甲 +200%"，看着很强其实什么都没加。
    mods: { maxHp: 6, hpRegen: 0.8, pickupRange: 25, armor: 2 },
    // 输出端明显吃亏，换来的是续航和一点运气（乖巧的孩子运气不会差）
    pct: { damage: -12, attackSpeed: 5, luck: 10 },
    startWeapon: 'fish_shield',
    unlock: null,
  },
  // ============================================================
  // 第十三轮新增：Neon（原"neon宴会"） —— 炮台专精
  //
  // 用户原话："新增角色neon宴会，擅长炮台类的角色，初始武器是炮台。"（neon宴会 = 现在的 Neon）
  //
  // 设计要点：
  //   * 初始武器给 **toy_turret（玩具炮台，tier 1）** —— 炮台系最低阶的那把。
  //     给高阶炮台会让开局太顺，而 toy_turret 正好是"炮台流"的入门票。
  //   * 本命流派 `['turret', 'turret']`：两个槽都填 turret，
  //     这样商店里炮台类武器/道具的权重是 1.2×1.2 = 1.44 倍（见 archetypes.js
  //     的 SHOP_ARCH_BONUS），真正做到"擅长炮台"。
  //     别的角色最多只有 1.2 倍，这是本角色的核心身份。
  //   * 数值上走"阵地战"：炮台伤害/攻速/上限都加，但自己血少、走得慢。
  //     炮台替你打，你就得站得住 —— 移速 -8% 逼玩家早点布好阵地。
  //   * 解锁条件放在第 20 波（通关），因为这是全场最强专精，
  //     不该开局就能选。
  // ============================================================
  {
    id: 'neon_party',
    // 第一百六十轮：改名 Neon—— 与 Roki（原 neon）区分，
    // 用户："其实是 2 个人"。**id 保持 neon_party 不动**。
    name: 'Neon',
    role: '炮台专精',
    color: '#7ce0d3',
    accent: '#fff2b8',
    shape: 'cat',
    glyph: '宴',
    desc: '把战场变成宴会厅的猫娘。炮台伤害、攻速、数量全面强化，但自己又脆又慢。',
    stats: {},
    // 炮台是"替身输出"，所以本体让出生存：血少、走得慢。
    // **turretMax 必须放 mods 不能放 pct** —— 它是 kind:'flat' 的属性，
    // 而 flat 的结算式是 `(base+flat) * (1+pct)`；base 和 flat 都是 0 时
    // 再乘多少 pct 还是 0，放 pct 里等于没写。items.js 里的炮台上限道具
    // 也是写在 mods 里的。
    mods: { maxHp: -4, moveSpeed: -8, turretMax: 1 },
    // 炮台三件套（伤害/攻速/上限）全给，换来的是本体输出和闪避为负
    pct: { turretDamage: 30, turretRate: 20, damage: -8, dodge: -5 },
    startWeapon: 'toy_turret',
    unlock: { type: 'wave', value: 20, desc: '到达第 20 波' },
  },
];

export const CHARACTER_MAP = Object.fromEntries(CHARACTERS.map((c) => [c.id, c]));

export function getCharacter(id) {
  return CHARACTER_MAP[id] || CHARACTERS[0];
}

/**
 * 哪些角色**真的有立绘**。
 *
 * 和敌人的 `getSpriteEnemies()` 是同一个套路，理由也一样：
 * 预加载如果无脑把整个 CHARACTERS 塞进去，每局都会发一堆必定 404 的请求。
 * 坏处不只是浪费带宽 —— 它会往控制台灌错误，把真正的报错淹掉。
 *
 * 判断放在数据层而不是 main.js 的调用处，是为了让**预加载和测试读同一份
 * 规则**。第十轮在敌人那边就踩过这个坑：过滤写在 main.js 里，
 * 测试直接调 buildPreloadPlan 就绕过了，于是测试报"缺贴图"而游戏其实是对的。
 *
 * 第十三轮：8 个角色全部有了立绘（用户把 8 张图放进 E:\猫娘姐妹\角色）。
 * 这里从手写数组改成**从 assets/characters 目录推导**是不行的 ——
 * 数据层要能在 Node 里裸跑（测试直接 import），碰文件系统会引入
 * 环境依赖。所以还是显式列表，但加了一条测试
 * （test/characters-check.mjs）保证它和目录内容一一对应，
 * 防止以后加了图忘了登记。
 *
 * 兜底不变：万一漏了，sprites.get() 返回 null，渲染器照样回落到几何小猫。
 */
export const SPRITE_CHARACTER_IDS = [
  'yui', 'mimi', 'luna', 'shiro', 'nana', 'kuro', 'youyi', 'neon_party',
];
export const getSpriteCharacters = () => SPRITE_CHARACTER_IDS.slice();

/**
 * 返回可选角色。
 *
 * 第十七轮：用户要求"把游戏按顺序解锁角色的设定删了，默认 8 个角色全开"。
 * 所以这里**永远返回全部角色**，不再按 unlockedIds 过滤。
 *
 * 函数本身保留（而不是删掉）是因为 main.js 和多个测试都在调它，
 * 而"全部可选"这个语义一行就能表达清楚；万一以后要加别的门槛，
 * 入口也还在。
 *
 * 各角色数据里的 `unlock` 字段同样保留 —— 它们现在只作为
 * "这个角色的设计难度定位"的说明（第 20 波那个是炮台专精，
 * 本来就是设计上最强的一档），test/gen-doc.mjs 还在读它生成表格。
 * 但**没有任何代码再拿它来拦人**。
 *
 * @param {string[]} _unlockedIds 已忽略，留着是为了不破坏调用签名。
 */
export function availableCharacters(_unlockedIds) {
  return CHARACTERS.slice();
}