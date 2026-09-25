// 扁平绘本风书本样式（1.3.1 日记页可爱化 → 1.3.1c 换配方为「白底彩点」）：
// 个人日记 = 她的暖白纸手账，我的日记 = 记录者的冷白纸手账——大面积一律近白
// （对齐宿主 UI Kit：底 #f7f9fc、卡片纯白玻璃、彩色只做小面积点缀），
// 颜色只住在小件上：书脊皮（心情色阶）、丝带、题签、落款印、进度条。
// 两本共用「书脊列 + 纸页 + sticky 页脚」的骨架，性格靠冷暖白底 + 点缀色分岔。
//
// 配方沿革（三轮真机反馈的账都记在这，改之前先读完）：
//   1.3.0 拟真旧书（棕褐布面/米黄宣纸/冷灰卷宗）→ 用户：与面板玻璃层脱节；
//   1.3.1 扁平绘本粉/蓝紫 → 用户：粉味过重；且架上书脊吃全站 moodDotColor 的
//     蓝/琥珀，翻开却是粉纸，外内两个色系（1.3.1b 收淡 + 书脊色阶搬进粉紫系）；
//   1.3.1c 定量诊断：宿主的近白大面（色距 max-min≤11）+ 7~10% alpha 色晕 vs
//     书本 S50%+ 的大面积彩面，色相又撞（H340 暖粉 vs 宿主 H216 冷蓝白）——
//     "味道浓"是配方问题不是浓度问题。本轮换成白底彩点：纸与封皮全部褪到
//     近白/灰调（大面色距≤20），点纹纸纹、条纹胶带、彩色大投影全部撤除，
//     可爱感靠造型（圆角/贴纸/丝带/歪斜纸叠）而非色相撑。
//   1.3.1d 书架书脊统一（用户：架上颜色不统一）：1.3.1c 的 shelfInk 以暖灰为中点
//     向灰玫（H345）/雾紫（H268）两端插值，摆幅近 77°，相邻本冷暖交错时架上
//     读作"乱"不读作"梯度"——收进封皮同族粉（H≈340），心情只驱动浓淡（色相
//     零漂移）。诊断口径：统一感来自色相锁死，不来自降低饱和度。
//
// 为什么不塞进 styles.ts：styles.ts（89KB）是"面板公共玻璃层"，这一坨是"桌面物件层"——
// 纸/墨/装订/架子的调色板互不相通，混在一起后面谁也不敢删。单独一个模块 + 第二个
// <style> 标签（diary.tsx 内联挂），归属清楚，也保住 hosted 依赖预算的可预测性。
//
// 硬约束（都来自宿主 hosted-tsx 运行时，别试着想绕）：
//   1. 无 SVG：装订缝线/丝带/落款印一律用 background-image + clip-path 画（同 ring.tsx 先例）
//   2. 纸面**不能** overflow:hidden——它会变成 sticky 页脚的滚动容器，把"常驻下缘"
//      当场废掉；所以丝带/页脚外扩一律靠 clip-path 自剪，不靠父级裁
//   3. 半透明 sticky 页脚在"纸叠"上会露馅（文字从半透区穿过去），所以页脚底色用
//      实色渐变 + 不画圆角（页脚即页面收口），只保留毛玻璃模糊这一处
//   4. 颜色写死在本文件（含暗色孪生块），不走面板 --tm-* 外观变量：面板「背景调节」
//      管的是玻璃卡片，不该把手写的纸也一起调淡
export const BOOK_STYLES = `

/* ============================================================
   0. 书本调色板（局部变量，只活在本模块的选择器里）
   —— 白底彩点：大面近白（暖白/冷白分书），饱和色只留给小件
   ============================================================ */
.tmb-book, .tmb-shelf {
  --tmb-kai: "KaiTi", "STKaiti", "Kaiti SC", "BiauKai", "DFKai-SB", "Noto Serif SC", "Songti SC", "SimSun", serif;
  --tmb-round: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic", "Noto Sans SC", "Segoe UI", sans-serif;
  --tmb-mono: ui-monospace, "Cascadia Mono", "Menlo", Consolas, "Courier New", monospace;
  /* 手账本纸：暖白三档（色距≤11，与宿主底色的"无色"同一预算，只多一丝米暖） */
  --tmb-paper-hi: #fffefc;
  --tmb-paper-mid: #fdfaf6;
  --tmb-paper-lo: #faf5ef;
  --tmb-paper-edge: rgba(212, 199, 189, 0.48);
  --tmb-ink: #4d4448;
  --tmb-ink-soft: rgba(125, 108, 115, 0.9);
  --tmb-rule: rgba(196, 176, 184, 0.28);
  /* 封皮：灰粉/灰紫粉（大面彩点里唯一的"面"，饱和度也让给书脊心情色去表达） */
  --tmb-cloth: #e9d5da;
  --tmb-ribbon: #e3b7c6;
  --tmb-tape-a: rgba(245, 236, 239, 0.92);
  --tmb-tape-b: rgba(255, 255, 255, 0.95);
  /* 观测手账纸：冷白两档（向宿主 #f7f9fc 看齐） */
  --tmb-file-paper-hi: #fdfeff;
  --tmb-file-paper-lo: #f3f5f9;
  --tmb-file-edge: rgba(196, 205, 222, 0.45);
  --tmb-file-ink: #4a5266;
  --tmb-file-rule: rgba(150, 162, 186, 0.28);
  /* 手账封皮：灰蓝；落款印：雾靛（小件，允许这点浓度） */
  --tmb-file-cloth: #d8dfea;
  --tmb-seal: rgba(125, 140, 192, 0.75);
}

/* ============================================================
   1. 书架：目录视图（书脊一排，点一根抽出那页）
   ============================================================ */
/* 书脊是"站着的小绘本"，不是卡片：横向排、底边坐在隔板上。
   隔板挂在 .tmb-shelf::after（真元素不行——它得跟着行高走），
   换行时行与行之间留足气口，读作"两层架"而不是一排按钮。
   1.3.1c：隔板褪成暖白软垫——架上唯一有颜色的东西应该是书脊皮（心情） */
.tmb-shelf {
  display: flex; flex-wrap: wrap; align-items: flex-end; gap: 5px 6px;
  padding: 18px 8px 14px; margin: 0 -6px;
}
.tmb-shelf { position: relative; }
.tmb-shelf::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: 5px; height: 7px;
  border-radius: 4px;
  background: linear-gradient(180deg, rgba(241, 234, 229, 0.95) 0%, rgba(230, 220, 214, 0.8) 55%, rgba(219, 208, 203, 0.65) 100%);
  box-shadow: 0 4px 9px rgba(190, 175, 172, 0.16), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.tmb-spine {
  position: relative; flex: 0 0 auto;
  width: 34px; height: 186px; padding: 8px 0 9px;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  border: 1px solid rgba(255, 255, 255, 0.6); border-radius: 8px 8px 6px 6px;
  /* 书脊皮 = 全页最大的"彩点"：底 = shelfInk 单色族明度阶（1.3.1d：封皮同族粉，
     开心鲜亮粉/低落深灰玫瑰/无数据雾粉，色相零漂移），浓=好/淡灰=坏的语义
     与全站心情同方向；纯色 + 左上一处柔和高光，没有布纹、没有圆柱明暗 */
  background-color: var(--tmb-spine-base, rgb(232, 214, 220));
  background-image: linear-gradient(118deg, rgba(255, 255, 255, 0.45) 0%, rgba(255, 255, 255, 0.16) 38%, rgba(255, 255, 255, 0) 62%);
  box-shadow: 0 4px 10px rgba(180, 165, 172, 0.18), inset 0 0 0 1px rgba(255, 255, 255, 0.25);
  cursor: pointer;
  transition: transform 0.18s cubic-bezier(0.22, 0.61, 0.36, 1), box-shadow 0.18s ease;
}
/* 抽出来一点：整根上移 + 影子拉长（悬停即"这本可以拿"） */
.tmb-spine:hover {
  transform: translateY(-12px);
  box-shadow: 0 14px 22px rgba(180, 165, 172, 0.24), inset 0 0 0 1px rgba(255, 255, 255, 0.32);
}
.tmb-spine:focus-visible { outline: 2px solid rgba(190, 140, 160, 0.75); outline-offset: 2px; }
/* 书顶：纸上沿露出的一线（圆头小白条，读作"页口"不读作"毛边"） */
.tmb-spine-top {
  width: 24px; height: 4px; border-radius: 2px;
  background: linear-gradient(180deg, #ffffff, rgba(250, 246, 242, 0.9));
  box-shadow: 0 1px 0 rgba(170, 155, 150, 0.2);
}
/* 竖排日期：浅脊配深字（1.3.1c 脊色褪淡后白字对比度不够，翻面成墨色 + 白描边）；
   1.3.1e 架上精简：页码方块/书根段数/spacer 随 JSX 退场（信息进悬停 tooltip） */
.tmb-spine-date {
  writing-mode: vertical-rl; text-orientation: mixed;
  margin-top: 9px; font-size: 11px; line-height: 1.1; letter-spacing: 0.04em;
  font-family: var(--tmb-round);
  color: rgba(72, 55, 63, 0.92);
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.4);
  max-height: 104px; overflow: hidden;
}
/* 旧版迁移页：一枚贴在脊上的小圆角标签 */
.tmb-spine-flag {
  position: absolute; top: 44%; left: 2px; right: 2px;
  padding: 1px 0; border-radius: 4px; text-align: center;
  background: rgba(240, 226, 205, 0.95); color: #7d6748;
  font-size: 8.5px; line-height: 1.5; letter-spacing: 0.06em;
  box-shadow: 0 1px 2px rgba(160, 140, 110, 0.22);
}

/* ---- 我的日记：观测手账的盒脊（同一根骨架，换配色）---- */
.tmb-spine--file {
  width: 31px; height: 168px;
  border-color: rgba(255, 255, 255, 0.55); border-radius: 7px 7px 5px 5px;
  background-color: var(--tmb-file-cloth);
  background-image: linear-gradient(118deg, rgba(255, 255, 255, 0.42) 0%, rgba(255, 255, 255, 0.14) 38%, rgba(255, 255, 255, 0) 62%);
  box-shadow: 0 4px 10px rgba(160, 170, 190, 0.18), inset 0 0 0 1px rgba(255, 255, 255, 0.2);
}
.tmb-spine--file:hover { box-shadow: 0 14px 22px rgba(160, 170, 190, 0.24), inset 0 0 0 1px rgba(255, 255, 255, 0.28); }
.tmb-spine--file .tmb-spine-top { background: linear-gradient(180deg, #ffffff, rgba(246, 248, 251, 0.9)); box-shadow: 0 1px 0 rgba(150, 160, 180, 0.18); }
.tmb-spine--file .tmb-spine-date { font-family: var(--tmb-mono); font-size: 11px; letter-spacing: -0.02em; color: rgba(58, 68, 96, 0.92); text-shadow: 0 1px 0 rgba(255, 255, 255, 0.4); }

/* 书架空态/加载中的一句小字由现有 .tm-derived 承担，不另立样式 */

/* ============================================================
   2. 书：书脊列 + 纸页 + sticky 页脚
   ============================================================ */
.tmb-book {
  display: grid; grid-template-columns: 27px minmax(0, 1fr);
  align-items: stretch; width: 100%; max-width: var(--tmb-col, 640px); margin: 2px auto 0;
}
/* 1.3.0 真机反馈「日记一页大一小」：宿主 Card 的 body 是 display:grid，
   grid 子项带 margin:auto 即从 stretch 退化为 shrink-to-fit——书宽随该页
   最长文字行的 max-content 浮动（1 段短页 ≈533px vs 2 段长页顶满 640px），
   居中又让左缘跟着书脊漂。显式 width:100% 钉回「满列宽、封顶 640」，
   两本书与藏书阁共用本类，一处修全。 */
/* 1.3.0 真机反馈"全屏下书很扁"：纸页实测 728×366 ≈ 2.0:1。宽收到 640
   （正文列 ≈ 540px → 15px 下约 36 字/行，中文书舒适行宽），高见 .tmb-page
   竖版下限。列宽变量挂在 .tmb-card（Card 元素）上：工具条与书都是它的后代
   → 同宽居中，「请她写一篇」不再飞到屏幕最右缘与书脱节 */
.tmb-card { --tmb-col: 640px; }
.tmb-card .tm-journal-toolbar { max-width: var(--tmb-col); margin-inline: auto; }
/* 书脊列：扁平绘本封（纯色 + 一处受光 + 大圆角）+ 一条缝线 + 圆角贴纸题签 */
.tmb-book-strip {
  position: relative; border-radius: 12px 0 0 12px;
  background-color: var(--tmb-cloth);
  background-image: linear-gradient(96deg, rgba(255, 255, 255, 0.4) 0%, rgba(255, 255, 255, 0.12) 42%, rgba(255, 255, 255, 0) 66%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.4), -1px 0 0 rgba(190, 168, 175, 0.16);
}
/* 装订：一条白缝线（dashed border，绘本的机器锁线） */
.tmb-book-stitch {
  position: absolute; left: 13px; top: 22px; bottom: 22px; width: 0;
  border-left: 2px dashed rgba(255, 255, 255, 0.75);
}
/* 题签：贴在封面上的一张圆角白贴纸，字仍竖排楷体——那是她书的"名" */
.tmb-book-title {
  position: absolute; top: 26px; left: 4px; right: 5px; max-height: 112px; overflow: hidden;
  padding: 8px 1px; border-radius: 8px;
  writing-mode: vertical-rl; text-orientation: mixed;
  background: rgba(255, 253, 250, 0.97);
  box-shadow: inset 0 0 0 1px rgba(208, 182, 190, 0.45), 0 2px 5px rgba(185, 160, 168, 0.18);
  font-family: var(--tmb-kai); font-size: 11.5px; line-height: 1.2; letter-spacing: 0.06em;
  color: #8c6b76; text-align: center;
  transform: rotate(1.2deg);
}
.tmb-book-strip--file {
  background-color: var(--tmb-file-cloth);
  background-image: linear-gradient(96deg, rgba(255, 255, 255, 0.38) 0%, rgba(255, 255, 255, 0.12) 42%, rgba(255, 255, 255, 0) 66%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.38), -1px 0 0 rgba(160, 172, 195, 0.16);
}
/* 观测手账的装订：同样收成一个缝线（三孔打孔器退役） */
.tmb-book-holes {
  position: absolute; left: 7px; top: 22px; bottom: 22px; width: 0;
  border-left: 2px dashed rgba(255, 255, 255, 0.78);
}
.tmb-book-title--file {
  top: 30px; left: 2px; right: 3px; max-height: 96px;
  background: rgba(254, 255, 255, 0.98); color: #5f6c8e;
  box-shadow: inset 0 0 0 1px rgba(170, 182, 208, 0.45), 0 2px 5px rgba(160, 172, 195, 0.18);
  font-family: var(--tmb-round); font-size: 11px; letter-spacing: 0.08em;
  transform: rotate(-1.2deg);
}

/* ---- 纸页 ---- */
.tmb-page {
  /* flex 列 + 竖版下限：她只写两段时纸仍是一整页（空白落在页底、页脚钉在最下），
     而不是被内容拽成一条横幅——"扁"的主因（纸高曾完全由内容决定）。
     右内边距 62 = 丝带车道（丝带占纸页右缘 46~61px）：车道开在页上而不是页眉上，
     否则正文行尾仍会从丝带下面穿过去 */
  position: relative; display: flex; flex-direction: column;
  min-height: clamp(430px, 68vh, 700px);
  padding: 17px 62px 0 22px;
  border: 1px solid var(--tmb-paper-edge); border-left: none;
  border-radius: 0 12px 12px 0;
  background-color: var(--tmb-paper-mid);
  /* 1.3.1c：点纹纸纹撤除——大面只留"顶部一线受光 + 基色微暖渐变"，
     干净得像面板那张白玻璃卡，只是形状还是一本书 */
  background-image:
    radial-gradient(70% 45% at 12% 4%, rgba(255, 255, 255, 0.75), rgba(255, 255, 255, 0) 60%),
    linear-gradient(168deg, var(--tmb-paper-hi) 0%, var(--tmb-paper-mid) 55%, var(--tmb-paper-lo) 100%);
  box-shadow:
    inset 12px 0 14px -12px rgba(190, 170, 178, 0.16),
    inset -1px 0 0 rgba(255, 255, 255, 0.8),
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 10px 24px rgba(185, 168, 174, 0.12),
    0 2px 0 rgba(212, 199, 189, 0.3);
}
/* 页眉：一条浅界栏压底，字距拉开（和正文彻底分层） */
.tmb-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 0 12px 7px 0; margin-bottom: 11px;
  border-bottom: 1px solid var(--tmb-rule);
}
.tmb-head-kicker {
  font-family: var(--tmb-kai); font-size: 12px; letter-spacing: 0.22em;
  color: var(--tmb-ink-soft); text-transform: none;
}
.tmb-head-no { font-family: var(--tmb-round); font-size: 14px; font-weight: 700; color: var(--tmb-ink); letter-spacing: 0.02em; }
.tmb-head-meta { font-size: 12.5px; color: var(--tmb-ink-soft); font-variant-numeric: tabular-nums; font-family: var(--tmb-round); }
.tmb-head-spacer { margin-left: auto; }
.tmb-head-trend { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--tmb-ink-soft); }
.tmb-head-trend .tm-mood-dot { width: 8px; height: 8px; box-shadow: 0 0 0 1px rgba(190, 170, 178, 0.24); }

/* 段落＝一张贴进手账的纸：自己的边、自己的影、极轻微歪斜（手贴的效果）。
   ⚠这里**不加** overflow:hidden：它是 .tmb-page 的直接子块，
   .tmb-page 才是 sticky 页脚的滚动上下文，别在这儿添堵 */
.tmb-entry {
  position: relative; margin-top: 14px; padding: 13px 12px 13px 14px;
  border: 1px solid rgba(212, 199, 189, 0.5); border-radius: 10px;
  background-image:
    linear-gradient(172deg, rgba(255, 255, 254, 0.85) 0%, rgba(253, 250, 246, 0.55) 100%);
  box-shadow: 0 2px 6px rgba(185, 170, 175, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
.tmb-entry:nth-child(even) { transform: rotate(-0.18deg); }
.tmb-entry:nth-child(odd) { transform: rotate(0.16deg); }
/* 四角贴纸（素色半透明小胶带，1.3.1c 撤掉条纹——形状保留、颜色退场） */
.tmb-entry::before, .tmb-entry::after {
  content: ""; position: absolute; width: 11px; height: 26px; border-radius: 2px;
  background: linear-gradient(180deg, var(--tmb-tape-a), var(--tmb-tape-b));
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.6), 0 1px 2px rgba(175, 160, 165, 0.14);
}
.tmb-entry::before { left: -6px; top: 5px; transform: rotate(-34deg); }
.tmb-entry::after { right: -6px; bottom: 5px; transform: rotate(-34deg); }
.tmb-entry:first-child { margin-top: 0; }
/* 落笔时间：楷体小字，像她在页角写的日期 */
.tmb-entry-when {
  font-family: var(--tmb-kai); font-size: 12.5px; letter-spacing: 0.08em;
  color: var(--tmb-ink-soft); margin-bottom: 6px;
}
/* 分节小标题：界栏 + 字距拉开（"这段时间 / 我在想 / 对他的感觉 / 想说的"） */
.tmb-sec { margin-top: 11px; }
.tmb-sec:first-child { margin-top: 0; }
.tmb-sec-title {
  display: flex; align-items: center; gap: 8px; margin-bottom: 3px;
  font-family: var(--tmb-kai); font-size: 12.5px; letter-spacing: 0.16em;
  color: var(--tmb-ink-soft);
}
.tmb-sec-title::after { content: ""; flex: 1 1 auto; height: 1px; background: var(--tmb-rule); }
/* 正文：圆润字面 + 中性暖墨 + 2.08 行距 + 中文惯例首行缩进；justify 拉齐右边 */
.tmb-text {
  margin: 0; font-family: var(--tmb-round); font-size: 15px; line-height: 2.08;
  color: var(--tmb-ink); text-align: justify; text-indent: 2em;
  overflow-wrap: break-word; word-break: break-word;
}
.tmb-text + .tmb-text { margin-top: 7px; }
/* 首字下沉（全页只给第一段）：楷体大字，带一丝玫瑰——全页唯一允许有颜色的字 */
.tmb-text--lead { text-indent: 0; }
.tmb-text--lead::first-letter {
  float: left; padding: 3px 7px 0 0;
  font-family: var(--tmb-kai); font-size: 2.15em; line-height: 1.02;
  color: rgba(158, 112, 128, 0.92);
}

/* 丝带书签：压在页脚之上、被页脚遮住下半（真丝带的层叠关系）。
   页很高时百分比会拉成一条桌旗，故上下限夹住：短页 96px、长页最多 420px */
.tmb-ribbon {
  position: absolute; top: 0; right: 46px; width: 15px; height: 56%;
  min-height: 96px; max-height: 420px;
  background-color: var(--tmb-ribbon);
  background-image: linear-gradient(90deg, rgba(255, 255, 255, 0.4) 0%, rgba(255, 255, 255, 0) 55%);
  clip-path: polygon(0 0, 100% 0, 100% 100%, 50% 88%, 0 100%);
  z-index: 2; pointer-events: none;
}
/* 封面内衬的合页阴影：靠近书脊那一侧压一道（中性雾） */
.tmb-page-body { position: relative; flex: 1 1 auto; min-height: 0; }
.tmb-page-body::before {
  content: ""; position: absolute; left: -18px; top: 0; bottom: 0; width: 18px;
  background: linear-gradient(90deg, rgba(190, 170, 178, 0.0), rgba(190, 170, 178, 0.12));
  pointer-events: none;
}

/* 页脚：常驻下缘（连续卷轴 + 分页视觉的落点）——实色底 + 上界栏 + 模糊，
   不画圆角（页脚即页面的收口），见文件头约束 3 */
.tmb-foot {
  position: sticky; bottom: 0; z-index: 4;
  /* 页脚左右顶到纸边（含丝带车道），但不向下顶：纸页已是 flex 列且 padding-bottom=0，
     再给负 bottom 会在页脚下面留一条纸底白缝 */
  margin: 14px -62px 0 -22px; padding: 9px 18px 11px;
  display: flex; align-items: center; gap: 10px;
  background-image: linear-gradient(180deg, rgba(255, 253, 251, 0.97) 0%, rgba(250, 245, 240, 0.99) 100%);
  border-top: 1px solid rgba(212, 196, 202, 0.45);
  /* 页脚负外边距顶到纸边，右下角跟随纸页圆角（否则直角会在圆纸上露一尖） */
  border-radius: 0 0 12px 0;
  box-shadow: 0 -5px 12px rgba(185, 170, 175, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.9);
  -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px);
}
.tmb-foot-mid { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.tmb-btn {
  border: 1px solid rgba(205, 185, 192, 0.5); border-radius: 999px;
  padding: 5px 13px; background: rgba(255, 254, 253, 0.92);
  color: #7d666e; font-family: var(--tmb-round); font-size: 12.5px; letter-spacing: 0.04em;
  cursor: pointer;
  transition: background 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}
.tmb-btn:hover:not(:disabled) { background: #ffffff; box-shadow: 0 3px 9px rgba(190, 170, 178, 0.2); transform: translateY(-1px); }
.tmb-btn:active:not(:disabled) { transform: translateY(0) scale(0.97); }
.tmb-btn:disabled { opacity: 0.4; cursor: default; }
.tmb-btn:focus-visible { outline: 2px solid rgba(190, 140, 160, 0.7); outline-offset: 2px; }
.tmb-ind {
  min-width: 54px; text-align: center; font-size: 12.5px; letter-spacing: 0.06em;
  color: rgba(125, 102, 110, 0.88); font-variant-numeric: tabular-nums;
  font-family: var(--tmb-round);
}
.tmb-leaf-ind { font-size: 12px; color: rgba(140, 120, 127, 0.85); font-variant-numeric: tabular-nums; font-family: var(--tmb-round); }

/* ============================================================
   3. 冷白观测手账（我的日记专属）：1.3.0 双栏档案袋——纸页内分两栏，
   左窄栏=卷首事实（成文日/区间/轮数/她自主起的情绪）+「本卷依据」
   +落款印（读作档案袋封面），右宽栏=居中标题+纯正文；DOM 序正文在前，
   窄窗塔单列天然「正文优先」。数据感靠版式：等宽数字、表格线、居中标题；
   无丝带，右内边距收短（共享 .tmb-page 的 62px 丝带车道是给她那本的）
   ============================================================ */
.tmb-page--file {
  border-color: var(--tmb-file-edge);
  padding: 17px 26px 0 22px;
  background-color: var(--tmb-file-paper-lo);
  /* 1.3.1c：格线撤除——冷白净面，与宿主白玻璃卡同族，只比她的那本冷一档 */
  background-image:
    radial-gradient(66% 50% at 8% 2%, rgba(255, 255, 255, 0.8), rgba(255, 255, 255, 0) 60%),
    linear-gradient(168deg, var(--tmb-file-paper-hi) 0%, #f8fafc 54%, var(--tmb-file-paper-lo) 100%);
  box-shadow:
    inset 14px 0 16px -14px rgba(160, 172, 195, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 10px 22px rgba(160, 172, 195, 0.12),
    0 2px 0 rgba(196, 205, 222, 0.3);
}
/* 旧页眉行（tmb-head 系）与口径行（tmb-dossier 系）已随双栏改版从卷宗 JSX 退场；
   journal 那本仍用 .tmb-head 基座规则，不受牵连 */
/* 双栏骨架：左信息栏 190px + 右正文栏（1.3.0 整体提档一档：栏宽随字号加宽）；
   aside 在 DOM 里排在正文后（窄窗降级），宽窗用显式栏位拉回左列。
   注意：本容器不得加 overflow——sticky 页脚的滚动上下文仍是 .tmb-page（DESIGN 书本约束 ②） */
.tmb-file-cols {
  display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 0 18px;
  flex: 1 1 auto; min-height: 0;
}
.tmb-file-main { grid-column: 2; grid-row: 1; min-width: 0; }
.tmb-file-aside {
  grid-column: 1; grid-row: 1;
  border-right: 1px solid var(--tmb-file-rule); padding-right: 14px;
}
.tmb-file-date {
  font-family: var(--tmb-mono); font-size: 17px; font-weight: 700; color: #4f5a78;
  letter-spacing: 0.01em;
}
.tmb-file-kicker { font-size: 12px; letter-spacing: 0.14em; color: rgba(90, 102, 130, 0.8); margin-bottom: 10px; }
.tmb-file-rows { display: grid; gap: 5px; margin-bottom: 12px; }
.tmb-file-row {
  display: flex; justify-content: space-between; align-items: baseline; gap: 6px;
  font-size: 13px; color: rgba(74, 86, 110, 0.9); flex-wrap: wrap;
}
.tmb-file-row > span { opacity: 0.65; }
.tmb-file-row > b { font-family: var(--tmb-mono); font-weight: 700; color: #46536f; }
/* 左栏里的依据块：去掉大面底色与双虚线框，只留一条上虚线与卷首事实分层；
   窄栏里标签/值改上下行（横排在窄栏里必折得参差）；字号与卷首事实行同档 12px */
.tmb-file-aside .tmb-evidence {
  margin: 2px 0 0; padding: 8px 0 0;
  border-top: 1px dashed rgba(150, 162, 186, 0.45); border-bottom: none;
  background: none;
}
.tmb-file-aside .tmb-evidence-line { flex-direction: column; align-items: flex-start; gap: 1px; line-height: 1.55; }
.tmb-file-aside .tmb-quote { flex-wrap: wrap; line-height: 1.6; }
/* 落款印：住在左栏尾端（不再 float 压正文首段）；双线方框、楷体竖排、
   轻旋转的"盖章感"保留，雾靛——还是"一枚盖上去的章"（小件，允许浓度） */
.tmb-file-sign { margin-top: 12px; }
.tmb-file-sign .tmb-seal { float: none; clear: none; margin: 0; }
/* 标题居于一栏顶（"这一段时间的记录"）：圆体宽字距 */
.tmb-file-title {
  margin: 2px 0 12px; text-align: center;
  font-family: var(--tmb-round); font-size: 14px; font-weight: 700; letter-spacing: 0.3em;
  color: #4f5a78;
}
/* 正文与个人日记那本完全同档（1.3.0 统一字号反馈："整体小了一点"）：
   字号/行距不再单独压低，继承 .tmb-text 基座 15px/2.08，只保留冷墨色温差 */
.tmb-text--file {
  color: var(--tmb-file-ink);
}
.tmb-page--file .tmb-entry {
  border-color: rgba(196, 205, 222, 0.5);
  background-image:
    linear-gradient(172deg, rgba(255, 255, 255, 0.75) 0%, rgba(250, 252, 254, 0.5) 100%);
  box-shadow: 0 2px 6px rgba(160, 172, 195, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.tmb-page--file .tmb-entry::before, .tmb-page--file .tmb-entry::after {
  background: linear-gradient(180deg, rgba(244, 246, 250, 0.92), rgba(255, 255, 255, 0.95));
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.6), 0 1px 2px rgba(160, 172, 195, 0.14);
}
.tmb-page--file .tmb-entry:nth-child(even), .tmb-page--file .tmb-entry:nth-child(odd) { transform: none; }
/* 观测手账不贴角、不歪斜：那是她手边的东西，这本是整理好的记录 */
/* 落款印本体：方框双线 + 楷体竖排 + 轻旋转（盖章必不正的仪式感保留），
   雾靛——位置由 .tmb-file-sign 接管（1.3.0 起不再 float 进正文） */
.tmb-seal {
  float: right; clear: both; margin: 16px 2px 0 18px;
  width: 46px; height: 66px; padding: 6px 0;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid var(--tmb-seal); border-radius: 8px;
  color: var(--tmb-seal);
  background-image: radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0) 62%);
  box-shadow: inset 0 0 0 1px var(--tmb-seal);
  writing-mode: vertical-rl; text-orientation: upright;
  font-family: var(--tmb-kai); font-size: 12.5px; letter-spacing: 0.04em; line-height: 1.05;
  transform: rotate(-5.5deg);
}
.tmb-page--file .tmb-foot {
  background-image: linear-gradient(180deg, rgba(253, 254, 255, 0.97) 0%, rgba(244, 246, 250, 0.99) 100%);
  border-top-color: rgba(196, 205, 222, 0.5);
  /* 本页右内边距 26（无丝带车道），页脚负外边距跟手收短 */
  margin: 14px -26px 0 -22px;
  box-shadow: 0 -5px 12px rgba(160, 172, 195, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.95);
}
.tmb-page--file .tmb-btn { border-color: rgba(178, 188, 210, 0.5); background: rgba(254, 255, 255, 0.92); color: #5f6c8e; }
.tmb-page--file .tmb-btn:hover:not(:disabled) { background: #ffffff; box-shadow: 0 3px 9px rgba(160, 172, 195, 0.22); }
.tmb-page--file .tmb-ind, .tmb-page--file .tmb-leaf-ind { font-family: var(--tmb-mono); color: rgba(90, 102, 130, 0.92); }
/* 素材进度：细灰轨 + 雾蓝填充（小件彩点），向面板那根进度条的观感看齐 */
.tmb-meter { display: grid; gap: 5px; margin: 0 0 12px; }
.tmb-meter-row { display: flex; align-items: baseline; gap: 8px; font-family: var(--tmb-mono); font-size: 12.5px; color: rgba(90, 102, 130, 0.92); letter-spacing: 0.01em; }
.tmb-meter-track { height: 5px; border-radius: 999px; background: rgba(196, 205, 222, 0.3); box-shadow: inset 0 1px 1px rgba(150, 162, 186, 0.1); overflow: hidden; }
.tmb-meter-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #b6c3e4, #9dadd9); }
/* 双门槛副行（1.3.0）：天数维度比轮数低一级字号与透明度，主次不互摸 */
.tmb-meter-row--sub { font-size: 11.5px; opacity: 0.78; }
/* 到期徽标：灰玫小胶囊——"到时候了"是事实陈述不是庆祝，不给动效 */
.tmb-meter-due {
  font-size: 11.5px; letter-spacing: 0.04em; padding: 0 6px; border-radius: 999px;
  color: #a2707f; border: 1px solid rgba(190, 148, 165, 0.45); background: rgba(250, 243, 246, 0.85);
}
/* 本卷依据（1.3.0）：成文时固化的素材快照——附页的口吻，
   虚线压顶与正文分层，等宽小字列事实（语气分布/心情走向/原话摘录） */
.tmb-evidence {
  position: relative; z-index: 1;
  margin: 2px 0 10px; padding: 7px 10px 8px;
  border-top: 1px dashed rgba(150, 162, 186, 0.45); border-bottom: 1px dashed rgba(150, 162, 186, 0.25);
  background: rgba(255, 255, 255, 0.4);
  display: grid; gap: 4px;
}
.tmb-evidence-title {
  font-family: var(--tmb-round); font-size: 13px; letter-spacing: 0.1em; color: rgba(90, 102, 130, 0.9);
}
.tmb-evidence-line { display: flex; align-items: baseline; gap: 8px; font-family: var(--tmb-mono); font-size: 12.5px; color: rgba(74, 86, 110, 0.95); }
.tmb-evidence-label { flex: 0 0 auto; opacity: 0.72; letter-spacing: 0.02em; }
.tmb-evidence-value { min-width: 0; overflow-wrap: anywhere; }
/* 原话摘录：语录贴条：kind 小圆角标签 + 引文，不抢正文视觉 */
.tmb-quote { display: flex; align-items: baseline; gap: 6px; font-size: 12.5px; font-family: var(--tmb-mono); color: rgba(74, 86, 110, 0.92); overflow-wrap: anywhere; }
.tmb-quote-kind {
  flex: 0 0 auto; font-family: var(--tmb-round); font-size: 11.5px; letter-spacing: 0.04em;
  padding: 0 5px; border: 1px solid rgba(178, 188, 210, 0.5); border-radius: 4px; color: rgba(90, 102, 130, 0.9);
}

/* 藏书阁（1.3.0）：书架末尾横叠的一小摞——写满下架的旧页都在这儿，只读翻阅。
   不标本数、不提 52 上限（1.3.0 显示层纪律：不在活架上加计数）；
   1.3.1c 板色褪成灰粉/灰蓝，摞本身安静、朱印小点留色；
   坐在同一根隔板上（.tmb-shelf 的 align-items: flex-end 负责兜底） */
.tmb-stack {
  position: relative; flex: 0 0 auto;
  width: 58px; height: 46px; margin-left: 16px;
  border: 0; padding: 0; background: none; cursor: pointer;
}
.tmb-stack-slab {
  position: absolute; left: 2px; right: 2px; bottom: 0;
  height: 13px; border-radius: 6px;
  background: linear-gradient(118deg, rgba(255, 255, 255, 0.4) 0%, rgba(255, 255, 255, 0) 55%), #e6d2d7;
  border: 1px solid rgba(255, 255, 255, 0.6);
  box-shadow: 0 2px 5px rgba(185, 168, 175, 0.16);
}
.tmb-stack-slab--back { background: linear-gradient(118deg, rgba(255, 255, 255, 0.35) 0%, rgba(255, 255, 255, 0) 55%), #dfc9cf; left: 6px; right: -2px; transform: rotate(-0.7deg); }
.tmb-stack-slab--mid { background: linear-gradient(118deg, rgba(255, 255, 255, 0.38) 0%, rgba(255, 255, 255, 0) 55%), #e3ced4; bottom: 11px; left: 3px; right: 5px; transform: rotate(0.5deg); }
.tmb-stack-slab--top {
  bottom: 22px; left: 4px; right: 3px;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.tmb-stack:hover .tmb-stack-slab--top { transform: translateY(-3px) rotate(-0.8deg); box-shadow: 0 5px 10px rgba(185, 168, 175, 0.22); }
/* 切口一侧的纸口线：三板各画一道，读出"这里是一摞书，不是一块砖" */
.tmb-stack-slab::before {
  content: ""; position: absolute; left: 3px; right: 3px; top: 50%;
  height: 3px; border-radius: 2px;
  background: linear-gradient(180deg, #ffffff, rgba(250, 246, 242, 0.85));
  box-shadow: inset 0 0 0 1px rgba(190, 170, 178, 0.14);
}
/* 封面题签：一枚灰玫小圆印，里面一个"藏"字（盖章位子在书摞右上角） */
.tmb-stack-seal {
  position: absolute; right: 5px; top: -3px; z-index: 2;
  width: 16px; height: 16px; border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  background: #cfa3ae;
  color: #fff; font-size: 10px; line-height: 1;
  font-family: var(--tmb-kai);
  box-shadow: 0 1px 3px rgba(180, 145, 158, 0.3);
  transform: rotate(-4deg);
}
/* 丝带从书摞里探出来，搭在隔板沿上 */
.tmb-stack-ribbon {
  position: absolute; left: 10px; top: 10px; z-index: 1;
  width: 7px; height: 20px;
  background: linear-gradient(180deg, var(--tmb-ribbon), #d9aebb);
  clip-path: polygon(0 0, 100% 0, 100% 100%, 50% 78%, 0 100%);
  box-shadow: 0 1px 2px rgba(180, 145, 158, 0.24);
}
/* 档案室的收纳盒摞（1.3.0）：同一摞板子的灰蓝孪生——丝带换成一张探出的标签纸 */
.tmb-stack--file .tmb-stack-slab {
  background: linear-gradient(118deg, rgba(255, 255, 255, 0.4) 0%, rgba(255, 255, 255, 0) 55%), #d9e0ec;
  border-color: rgba(255, 255, 255, 0.55);
  box-shadow: 0 2px 5px rgba(160, 172, 195, 0.16);
}
.tmb-stack--file .tmb-stack-slab--back { background: linear-gradient(118deg, rgba(255, 255, 255, 0.35) 0%, rgba(255, 255, 255, 0) 55%), #cfd8e6; }
.tmb-stack--file .tmb-stack-slab--mid { background: linear-gradient(118deg, rgba(255, 255, 255, 0.38) 0%, rgba(255, 255, 255, 0) 55%), #d4dcea; }
.tmb-stack--file .tmb-stack-slab::before {
  background: linear-gradient(180deg, #ffffff, rgba(246, 248, 251, 0.85));
  box-shadow: inset 0 0 0 1px rgba(160, 172, 195, 0.14);
}
.tmb-stack--file:hover .tmb-stack-slab--top { box-shadow: 0 5px 10px rgba(160, 172, 195, 0.24); }
.tmb-stack--file .tmb-stack-seal { background: #9fabcc; }
.tmb-stack--file .tmb-stack-ribbon {
  left: 9px; top: 8px; width: 16px; height: 13px;
  background: linear-gradient(180deg, #ffffff, rgba(244, 246, 250, 0.95));
  clip-path: polygon(0 0, 100% 0, 100% 100%, 50% 84%, 0 100%);
  border: 1px solid rgba(178, 188, 210, 0.4);
  box-shadow: 0 1px 2px rgba(160, 172, 195, 0.18);
}

/* ============================================================
   4. 暗色孪生：一间夜里的书房——同样白底彩点，只是"白"翻成
   深中性纸（暖炭/冷板岩），点缀色提亮一档保证小件在暗面可读
   ============================================================ */
@media (prefers-color-scheme: dark) {
  .tmb-book, .tmb-shelf {
    --tmb-paper-hi: #2b2724;
    --tmb-paper-mid: #262220;
    --tmb-paper-lo: #211d1c;
    --tmb-paper-edge: rgba(168, 152, 146, 0.32);
    --tmb-ink: #e8e0da;
    --tmb-ink-soft: rgba(198, 180, 182, 0.9);
    --tmb-rule: rgba(178, 156, 160, 0.26);
    --tmb-cloth: #6b5c63;
    --tmb-ribbon: #9c7484;
    --tmb-tape-a: rgba(72, 62, 64, 0.85);
    --tmb-tape-b: rgba(58, 50, 52, 0.9);
    --tmb-file-paper-hi: #262a33;
    --tmb-file-paper-lo: #1c1f27;
    --tmb-file-edge: rgba(148, 158, 178, 0.26);
    --tmb-file-ink: #d5dae4;
    --tmb-file-rule: rgba(148, 158, 178, 0.22);
    --tmb-file-cloth: #555d6e;
    --tmb-seal: rgba(160, 172, 212, 0.8);
  }
  .tmb-shelf::after {
    background: linear-gradient(180deg, rgba(78, 68, 66, 0.7) 0%, rgba(58, 50, 49, 0.6) 55%, rgba(40, 34, 33, 0.55) 100%);
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(220, 200, 196, 0.12);
  }
  .tmb-spine { border-color: rgba(255, 255, 255, 0.16); box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4), inset 0 0 0 1px rgba(255, 255, 255, 0.08); }
  .tmb-spine:hover { box-shadow: 0 14px 22px rgba(0, 0, 0, 0.5), inset 0 0 0 1px rgba(255, 255, 255, 0.12); }
  .tmb-spine-top { background: linear-gradient(180deg, rgba(112, 100, 96, 0.95), rgba(72, 64, 60, 0.85)); box-shadow: 0 1px 0 rgba(0, 0, 0, 0.4); }
  .tmb-spine-date { color: rgba(244, 236, 232, 0.92); text-shadow: 0 1px 1px rgba(28, 22, 22, 0.6); }
  .tmb-spine-flag { background: rgba(118, 98, 68, 0.95); color: #efe0c8; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4); }
  .tmb-spine--file { background-color: var(--tmb-file-cloth); border-color: rgba(255, 255, 255, 0.14); box-shadow: 0 4px 10px rgba(0, 0, 0, 0.42), inset 0 0 0 1px rgba(255, 255, 255, 0.07); }
  .tmb-spine--file .tmb-spine-top { background: linear-gradient(180deg, rgba(92, 98, 112, 0.95), rgba(54, 58, 68, 0.85)); }
  .tmb-spine--file .tmb-spine-date { color: rgba(236, 240, 248, 0.9); text-shadow: 0 1px 1px rgba(16, 18, 26, 0.6); }
  .tmb-book-strip { box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12), -1px 0 0 rgba(0, 0, 0, 0.25); }
  .tmb-book-strip--file { box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1), -1px 0 0 rgba(0, 0, 0, 0.28); }
  .tmb-book-title { background: rgba(52, 45, 44, 0.95); color: #e2ccd4; box-shadow: inset 0 0 0 1px rgba(188, 156, 168, 0.3), 0 2px 5px rgba(0, 0, 0, 0.4); }
  .tmb-book-title--file { background: rgba(38, 42, 52, 0.96); color: #ccd2e2; box-shadow: inset 0 0 0 1px rgba(150, 160, 186, 0.3), 0 2px 5px rgba(0, 0, 0, 0.4); }
  .tmb-page {
    box-shadow:
      inset 12px 0 14px -12px rgba(0, 0, 0, 0.55),
      inset -1px 0 0 rgba(255, 246, 226, 0.05),
      inset 0 1px 0 rgba(255, 246, 226, 0.07),
      0 10px 24px rgba(0, 0, 0, 0.45),
      0 2px 0 rgba(0, 0, 0, 0.3);
  }
  .tmb-page--file { box-shadow: inset 14px 0 16px -14px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.05), 0 10px 22px rgba(0, 0, 0, 0.44), 0 2px 0 rgba(0, 0, 0, 0.3); }
  .tmb-file-aside { border-right-color: rgba(148, 158, 178, 0.22); }
  .tmb-file-date, .tmb-file-row > b { color: #d9dfec; }
  .tmb-file-kicker { color: rgba(188, 196, 214, 0.8); }
  .tmb-file-row { color: rgba(202, 208, 224, 0.94); }
  .tmb-file-aside .tmb-evidence { border-top-color: rgba(148, 158, 178, 0.3); }
  .tmb-file-aside .tmb-quote-kind { border-color: rgba(148, 158, 178, 0.3); }
  .tmb-entry { border-color: rgba(178, 156, 160, 0.24); background-image: linear-gradient(172deg, rgba(58, 52, 50, 0.5) 0%, rgba(46, 41, 40, 0.35) 100%); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.34), inset 0 1px 0 rgba(255, 246, 226, 0.05); }
  .tmb-entry::before, .tmb-entry::after { box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08), 0 1px 2px rgba(0, 0, 0, 0.3); }
  .tmb-page--file .tmb-entry { border-color: rgba(148, 158, 178, 0.18); background-image: linear-gradient(172deg, rgba(46, 50, 60, 0.5) 0%, rgba(38, 41, 50, 0.35) 100%); }
  .tmb-page--file .tmb-entry::before, .tmb-page--file .tmb-entry::after { background: linear-gradient(180deg, rgba(58, 62, 74, 0.9), rgba(44, 48, 58, 0.95)); }
  .tmb-file-title { color: #d9dfec; }
  .tmb-text--file { color: #d5dae4; }
  .tmb-seal { background-image: radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0) 62%); }
  .tmb-foot { background-image: linear-gradient(180deg, rgba(42, 38, 36, 0.97) 0%, rgba(34, 30, 29, 0.99) 100%); border-top-color: rgba(178, 156, 160, 0.28); box-shadow: 0 -5px 14px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 246, 226, 0.05); }
  .tmb-btn { border-color: rgba(198, 172, 176, 0.3); background: rgba(56, 50, 49, 0.9); color: #e8e0da; }
  .tmb-btn:hover:not(:disabled) { background: rgba(72, 64, 62, 0.95); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.42); }
  .tmb-ind, .tmb-leaf-ind { color: rgba(216, 200, 200, 0.9); }
  .tmb-page--file .tmb-foot { background-image: linear-gradient(180deg, rgba(33, 37, 46, 0.97) 0%, rgba(25, 28, 35, 0.99) 100%); border-top-color: rgba(148, 158, 178, 0.22); }
  .tmb-page--file .tmb-btn { border-color: rgba(148, 158, 178, 0.26); background: rgba(40, 44, 54, 0.92); color: #d5dae4; }
  .tmb-page--file .tmb-btn:hover:not(:disabled) { background: rgba(52, 57, 70, 0.96); }
  .tmb-page--file .tmb-ind, .tmb-page--file .tmb-leaf-ind { color: rgba(206, 212, 226, 0.9); }
  .tmb-meter-track { background: rgba(148, 158, 178, 0.18); box-shadow: inset 0 1px 1px rgba(0, 0, 0, 0.4); }
  .tmb-meter-fill { background: linear-gradient(90deg, #7c88a8, #66708c); }
  .tmb-meter-row { color: rgba(194, 200, 216, 0.95); }
  .tmb-meter-due { color: #dcb2be; border-color: rgba(196, 150, 168, 0.45); background: rgba(58, 44, 48, 0.6); }
  .tmb-evidence { border-top-color: rgba(148, 158, 178, 0.3); border-bottom-color: rgba(148, 158, 178, 0.16); background: rgba(0, 0, 0, 0.18); }
  .tmb-evidence-title { color: rgba(194, 200, 216, 0.9); }
  .tmb-evidence-line, .tmb-quote { color: rgba(202, 208, 224, 0.94); }
  .tmb-quote-kind { border-color: rgba(148, 158, 178, 0.3); color: rgba(194, 200, 216, 0.9); }
  .tmb-text--lead::first-letter { color: rgba(220, 172, 190, 0.9); }
  .tmb-page-body::before { background: linear-gradient(90deg, rgba(0, 0, 0, 0), rgba(0, 0, 0, 0.34)); }
  .tmb-stack-slab { background: linear-gradient(118deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0) 55%), #5c5055; border-color: rgba(255, 255, 255, 0.14); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45); }
  .tmb-stack-slab--back { background: linear-gradient(118deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0) 55%), #52474c; }
  .tmb-stack-slab--mid { background: linear-gradient(118deg, rgba(255, 255, 255, 0.11) 0%, rgba(255, 255, 255, 0) 55%), #574b50; }
  .tmb-stack-slab::before { background: linear-gradient(180deg, rgba(214, 198, 196, 0.45), rgba(170, 152, 152, 0.38)); box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.3); }
  .tmb-stack-seal { background: #83656e; color: #f2e8e8; }
  .tmb-stack-ribbon { background: linear-gradient(180deg, var(--tmb-ribbon), #6e525c); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.35); }
  .tmb-stack--file .tmb-stack-slab { background: linear-gradient(118deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0) 55%), #4a4f5c; border-color: rgba(255, 255, 255, 0.12); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.46); }
  .tmb-stack--file .tmb-stack-slab--back { background: linear-gradient(118deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0) 55%), #414652; }
  .tmb-stack--file .tmb-stack-slab--mid { background: linear-gradient(118deg, rgba(255, 255, 255, 0.11) 0%, rgba(255, 255, 255, 0) 55%), #454a58; }
  .tmb-stack--file .tmb-stack-slab::before { background: linear-gradient(180deg, rgba(196, 202, 214, 0.45), rgba(150, 156, 170, 0.38)); }
  .tmb-stack--file .tmb-stack-seal { background: #5d6478; }
  .tmb-stack--file .tmb-stack-ribbon { background: linear-gradient(180deg, rgba(216, 222, 234, 0.9), rgba(168, 174, 190, 0.85)); border-color: rgba(20, 24, 34, 0.6); }
}

/* ============================================================
   5. 小窗降级（1.2.9 之后的同一条纪律：不留"看得见但点不到"的东西）
   窗口很窄时书脊列与斜角贴纸先让路，正文优先吃宽度
   ============================================================ */
@media (max-width: 460px) {
  .tmb-book { grid-template-columns: 19px minmax(0, 1fr); }
  .tmb-book-stitch, .tmb-book-holes { display: none; }
  .tmb-book-title { left: 2px; right: 2px; font-size: 9.5px; }
  .tmb-page { padding: 14px 40px 0 15px; min-height: clamp(360px, 62vh, 620px); }
  .tmb-entry { padding: 11px 9px 11px 10px; }
  .tmb-entry::before, .tmb-entry::after { display: none; }
  .tmb-entry:nth-child(even), .tmb-entry:nth-child(odd) { transform: none; }
  .tmb-text { font-size: 14px; line-height: 2.02; }
  /* 双栏卷宗窄窗降级：塔单列——DOM 序正文在前，信息栏自然换到正文后（「正文优先」）；
     分隔线从右缘竖线换顶部虚线；本页无丝带，右内边距比通用值更短 */
  .tmb-file-cols { grid-template-columns: minmax(0, 1fr); }
  .tmb-file-main { grid-column: 1; }
  .tmb-file-aside {
    grid-column: 1; grid-row: 2; margin-top: 14px; padding-right: 0;
    border-right: none; border-top: 1px dashed rgba(150, 162, 186, 0.4); padding-top: 12px;
  }
  .tmb-page--file { padding: 14px 18px 0 15px; }
  .tmb-page--file .tmb-foot { margin: 12px -18px 0 -15px; }
  .tmb-foot { margin: 12px -40px 0 -15px; padding: 8px 12px 10px; }
  /* 小窗车道收窄（丝带右 24 + 宽 12 → 占 24~36），页右内边距 40 已足避让 */
  .tmb-card { --tmb-col: 100%; }
  .tmb-btn { padding: 5px 10px; font-size: 12px; }
  .tmb-ribbon { right: 24px; width: 12px; }
}

/* 动效关闭偏好：书本不弹、纸叠不歪 */
@media (prefers-reduced-motion: reduce) {
  .tmb-spine, .tmb-spine:hover, .tmb-btn, .tmb-btn:hover:not(:disabled) { transform: none !important; transition: none !important; }
  .tmb-entry:nth-child(even), .tmb-entry:nth-child(odd) { transform: none !important; }
  .tmb-stack-slab, .tmb-stack-slab--top, .tmb-stack:hover .tmb-stack-slab--top { transform: none !important; transition: none !important; }
}
`
