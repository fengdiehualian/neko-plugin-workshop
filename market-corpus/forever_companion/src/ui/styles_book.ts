// 日记目录与阅读页共用浅蓝点缀；纸面保持独立明暗色，避免壁纸调节影响正文。
// 阅读页及其祖先不得裁切，sticky 页脚用 --tm-save-clearance 避让设置保存条。
export const BOOK_STYLES = `
.tmb-card {
  --tmb-col: 780px;
  --tmb-round: "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic", "Noto Sans SC", "Segoe UI", sans-serif;
  --tmb-paper: #ffffff;
  --tmb-wash: #f4f9fc;
  --tmb-ink: #354b5a;
  --tmb-ink-soft: #718591;
  --tmb-accent: #4d8eaf;
  --tmb-line: #dce8ef;
  --tmb-rule: #e4edf2;
  --tmb-hover: #eef7fc;
  --tmb-binding: #bfdce9;
  letter-spacing: 0;
}
.neko-card.tmb-card { overflow: visible; }
.tmb-card .neko-card-body { gap: 18px; }
.tmb-card .tm-journal-toolbar {
  width: 100%; max-width: var(--tmb-col); margin: 0 auto;
  flex-wrap: wrap;
}
.tmb-card .tm-journal-actions { flex-wrap: wrap; }
.tmb-card .tm-derived { max-width: var(--tmb-col); width: 100%; margin-inline: auto; }
.tm-review { width: 100%; max-width: var(--tmb-col); min-width: 0; margin: 0 auto; }

/* 每个目录项都是一本可翻开的日记，日期和摘录可直接扫描。 */
.tmb-shelf {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
  width: 100%; max-width: var(--tmb-col); min-width: 0; margin: 0 auto;
}
.tmb-shelf > .neko-empty, .tmb-shelf > .tm-derived { grid-column: 1 / -1; }
.tmb-catalog-tools {
  grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  min-width: 0; margin-bottom: 2px;
}
.tmb-search {
  flex: 1 1 170px; min-width: 0; max-width: 330px; height: 34px; padding: 6px 10px;
  border: 1px solid var(--tmb-line); border-radius: 4px; background: var(--tmb-paper);
  color: var(--tmb-ink); font: 12px var(--tmb-round); outline: none;
}
.tmb-search::placeholder { color: var(--tmb-ink-soft); }
.tmb-search:focus { border-color: var(--tmb-accent); }
.tmb-catalog-nav { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.tmb-catalog-btn {
  width: 32px; height: 32px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--tmb-line); border-radius: 4px; background: var(--tmb-paper); color: var(--tmb-accent); cursor: pointer;
}
.tmb-catalog-btn:disabled { opacity: 0.35; cursor: default; }
.tmb-catalog-btn:hover:not(:disabled) { background: var(--tmb-hover); }
.tmb-catalog-btn:focus-visible { outline: 2px solid var(--tmb-accent); outline-offset: 2px; }
.tmb-catalog-btn .tmb-chevron { margin-left: 0; }
.tmb-catalog-btn .tmb-chevron--prev { transform: rotate(-135deg); }
.tmb-no-matches { grid-column: 1 / -1; padding: 35px 0; color: var(--tmb-ink-soft); font-size: 13px; text-align: center; }
.tmb-spine {
  position: relative; display: flex; flex-direction: column; gap: 10px; min-width: 0;
  min-height: 170px; padding: 18px 20px 15px 25px;
  border: 1px solid var(--tmb-line); border-radius: 6px;
  background: var(--tmb-paper); color: var(--tmb-ink); text-align: left;
  font-family: var(--tmb-round); letter-spacing: 0; cursor: pointer;
  box-shadow: 0 2px 5px rgba(52, 89, 112, 0.035);
  transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
}
.tmb-spine::before {
  content: ""; position: absolute; left: 0; top: 12px; bottom: 12px; width: 4px;
  border-radius: 0 2px 2px 0; background: var(--tmb-binding);
}
.tmb-spine--file::before { width: 2px; top: 18px; bottom: 18px; }
.tmb-spine:hover { background: var(--tmb-hover); border-color: #aecfde; box-shadow: 0 3px 12px rgba(52, 89, 112, 0.09); }
.tmb-spine:focus-visible, .tmb-stack:focus-visible, .tmb-btn:focus-visible, .tmb-reference summary:focus-visible {
  outline: 2px solid var(--tmb-accent); outline-offset: 3px;
}
.tmb-cover-meta, .tmb-cover-bottom {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px;
  color: var(--tmb-ink-soft); font-size: 11.5px; line-height: 1.5; min-width: 0;
}
.tmb-cover-meta { justify-content: space-between; flex-wrap: nowrap; align-items: flex-start; }
.tmb-cover-title {
  min-width: 0; color: var(--tmb-ink); font-size: 18px; font-weight: 600; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere;
}
.tmb-record-date { font-weight: 500; font-variant-numeric: tabular-nums; }
.tmb-cover-excerpt {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 42px; font-size: 13px; line-height: 1.65; color: var(--tmb-ink-soft); overflow-wrap: anywhere;
}
.tmb-cover-bottom { margin-top: auto; }
.tmb-cover-trend, .tmb-head-trend { display: inline-flex; align-items: center; gap: 6px; }
.tmb-card .tm-mood-dot { flex: 0 0 auto; width: 7px; height: 7px; box-shadow: none; }
.tmb-chevron { flex: 0 0 auto; width: 6px; height: 6px; border-top: 1.5px solid currentColor; border-right: 1.5px solid currentColor; transform: rotate(45deg); margin-left: auto; }

/* 归档入口单独成行，有明确的标签与时间范围。 */
.tmb-stack {
  grid-column: 1 / -1; width: 100%; min-width: 0;
  display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
  padding: 14px 4px; margin-top: 3px;
  border: 0; border-top: 1px solid var(--tmb-line); border-radius: 0;
  background: none; color: var(--tmb-accent); font: 13px var(--tmb-round); text-align: left; cursor: pointer;
}
.tmb-stack:hover { color: var(--tmb-ink); }
.tmb-stack:disabled { opacity: 0.6; cursor: wait; }
.tmb-archive-range { color: var(--tmb-ink-soft); font-size: 12px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.tmb-archive-icon { position: relative; width: 16px; height: 12px; border: 1px solid currentColor; border-radius: 2px; flex: 0 0 auto; }
.tmb-archive-icon::before { content: ""; position: absolute; left: -2px; right: -2px; top: -4px; height: 3px; border: 1px solid currentColor; border-radius: 1px; background: var(--tmb-paper); }
.tmb-archive-icon::after { content: ""; position: absolute; top: 3px; left: 5px; width: 4px; border-top: 1px solid currentColor; }

/* 正文行宽由阅读列控制，不再用装饰书脊、丝带占用空间。 */
.tmb-book { width: 100%; max-width: var(--tmb-col); min-width: 0; margin: 0 auto; }
.tmb-page {
  position: relative; display: flex; flex-direction: column; min-width: 0;
  min-height: clamp(430px, 65vh, 700px); padding: 30px 36px 0;
  border: 1px solid var(--tmb-line); border-top: 3px solid var(--tmb-binding); border-radius: 6px;
  background: var(--tmb-paper); box-shadow: 0 4px 18px rgba(52, 89, 112, 0.04);
}
.tmb-head { padding-bottom: 22px; margin-bottom: 24px; border-bottom: 1px solid var(--tmb-rule); min-width: 0; }
.tmb-reading-kicker {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px;
  color: var(--tmb-accent); font: 12px/1.5 var(--tmb-round);
}
.tmb-head-no { margin-left: auto; font-size: 11.5px; color: var(--tmb-ink-soft); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.tmb-reading-title {
  margin: 10px 0 12px; color: var(--tmb-ink); font-family: var(--tmb-round); font-size: 23px;
  line-height: 1.4; font-weight: 600; letter-spacing: 0; overflow-wrap: anywhere;
}
.tmb-reading-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; font-size: 12px; line-height: 1.6; color: var(--tmb-ink-soft); }
.tmb-reading-range { display: flex; flex-wrap: wrap; gap: 0 8px; }
.tmb-date-part { white-space: nowrap; }
.tmb-archive-badge { padding: 1px 6px; border: 1px solid var(--tmb-line); border-radius: 3px; color: var(--tmb-ink-soft); font-size: 11px; }
.tmb-page-body { flex: 1 1 auto; min-width: 0; max-width: 650px; width: 100%; margin: 0 auto; }
.tmb-entry { margin-top: 26px; padding: 22px 0 0; border-top: 1px solid var(--tmb-rule); background: none; box-shadow: none; }
.tmb-entry:first-child { margin-top: 0; padding-top: 0; border-top: 0; }
.tmb-entry-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin-bottom: 16px; }
.tmb-entry-kind { color: var(--tmb-accent); font: 600 12px/1.5 var(--tmb-round); }
.tmb-entry-when { color: var(--tmb-ink-soft); font: 11.5px/1.5 var(--tmb-round); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.tmb-sec { margin-top: 20px; }
.tmb-sec:first-child { margin-top: 0; }
.tmb-sec-title { margin-bottom: 8px; color: var(--tmb-accent); font: 600 13px/1.5 var(--tmb-round); }
.tmb-text { margin: 0; font-family: var(--tmb-round); font-size: 15px; line-height: 1.95; color: var(--tmb-ink); text-align: left; overflow-wrap: anywhere; }
.tmb-text + .tmb-text { margin-top: 14px; }

/* 原文在前，附栏在后；窄窗自然降为上下排。 */
.tmb-file-cols { display: grid; grid-template-columns: minmax(0, 1fr) 156px; gap: 28px; flex: 1 1 auto; min-width: 0; }
.tmb-file-main { min-width: 0; }
.tmb-file-aside { min-width: 0; border-left: 1px solid var(--tmb-rule); padding-left: 18px; }
.tmb-file-sign { margin-bottom: 12px; }
.tmb-seal {
  display: inline-block; max-width: 100%; padding: 4px 8px;
  border: 1px solid var(--tmb-line); border-radius: 3px; background: var(--tmb-wash);
  color: var(--tmb-accent); font-family: "Segoe UI", var(--tmb-round); font-size: 11.5px; line-height: 1.6;
  writing-mode: horizontal-tb; text-align: center; overflow-wrap: anywhere;
}
.tmb-reading-note { font-size: 12px; line-height: 1.8; color: var(--tmb-ink-soft); margin-bottom: 16px; overflow-wrap: anywhere; }
.tmb-reference { padding-top: 12px; border-top: 1px solid var(--tmb-rule); }
.tmb-reference summary { cursor: pointer; color: var(--tmb-accent); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.tmb-evidence { display: grid; gap: 14px; padding-top: 14px; }
.tmb-evidence-line { display: flex; flex-direction: column; gap: 3px; font-size: 12px; line-height: 1.75; color: var(--tmb-ink); }
.tmb-evidence-label { color: var(--tmb-ink-soft); }
.tmb-evidence-value { min-width: 0; overflow-wrap: anywhere; }
.tmb-quote { display: flex; flex-direction: column; gap: 4px; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.tmb-quote-kind { color: var(--tmb-ink-soft); font-size: 11px; }
.tmb-quote-text { color: var(--tmb-ink); }

/* 导航底色不透明，避免滚动正文从按钮背后透出。 */
.tmb-foot {
  position: sticky; bottom: var(--tm-save-clearance, 0px); z-index: 4;
  margin: 28px -36px 0; padding: 12px 18px; border-top: 1px solid var(--tmb-line);
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; min-width: 0;
  background: var(--tmb-paper); border-radius: 0 0 6px 6px; box-shadow: 0 -4px 12px rgba(52, 89, 112, 0.035);
}
.tmb-foot-mid { margin-left: auto; display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; max-width: 100%; }
.tmb-foot-mid--reader { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr); width: 340px; }
.tmb-foot-mid--reader > .tmb-btn { padding-inline: 6px; overflow-wrap: normal; }
.tmb-current-diary { flex: 1 1 100px; min-width: 0; max-width: 200px; text-align: center; }
.tmb-current-title { display: block; min-width: 0; color: var(--tmb-ink); font: 12px/1.5 var(--tmb-round); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tmb-btn {
  padding: 6px 10px; min-height: 32px; border: 1px solid var(--tmb-line); border-radius: 4px;
  background: var(--tmb-paper); color: var(--tmb-accent); font: 12px/1.5 var(--tmb-round);
  min-width: 0; max-width: 100%; white-space: normal; overflow-wrap: anywhere; cursor: pointer;
  transition: background 160ms ease;
}
.tmb-btn:hover:not(:disabled) { background: var(--tmb-hover); }
.tmb-btn:disabled { color: var(--tmb-ink-soft); opacity: 0.45; cursor: default; }
.tmb-ind, .tmb-leaf-ind { color: var(--tmb-ink-soft); font: 11.5px/1.5 var(--tmb-round); font-variant-numeric: tabular-nums; }

/* 两种生成门槛并排陈列，留出日记目录的视觉主位。 */
.tmb-meter { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 22px; padding: 0 0 20px; margin-bottom: 20px; border-bottom: 1px solid var(--tmb-line); }
.tmb-meter-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; color: var(--tmb-ink-soft); font: 11.5px/1.6 var(--tmb-round); min-width: 0; }
.tmb-meter-row:first-child { grid-column: 1; grid-row: 1; }
.tmb-meter-row--sub { grid-column: 2; grid-row: 1; }
.tmb-head-spacer { margin-left: auto; }
.tmb-meter-track { height: 4px; border-radius: 2px; overflow: hidden; background: var(--tmb-line); grid-row: 2; }
.tmb-meter-fill { height: 100%; background: #86bbd5; border-radius: 2px; }
.tmb-meter-due { flex-basis: 100%; color: var(--tmb-accent); }

@media (prefers-color-scheme: dark) {
  .tmb-card {
    --tmb-paper: #272b30;
    --tmb-wash: #2c343b;
    --tmb-ink: #dde7ec;
    --tmb-ink-soft: #a0b2be;
    --tmb-accent: #9acbe4;
    --tmb-line: #3b4952;
    --tmb-rule: #38424a;
    --tmb-hover: #2c3740;
    --tmb-binding: #577e92;
  }
  .tmb-spine:hover { border-color: #688b9e; }
  .tmb-meter-fill { background: #80abc2; }
}
@media (max-width: 680px) {
  .tmb-file-cols { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  .tmb-file-aside { border-left: 0; border-top: 1px solid var(--tmb-rule); padding: 18px 0 0; }
  .tmb-reading-note { margin-bottom: 12px; }
  .tmb-evidence { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .tmb-quote { grid-column: 1 / -1; }
}
@media (max-width: 460px) {
  .tmb-shelf { grid-template-columns: minmax(0, 1fr); gap: 10px; }
  .tmb-spine { padding: 16px 18px 14px 22px; min-height: 158px; }
  .tmb-page { padding: 22px 20px 0; min-height: clamp(360px, 62vh, 620px); }
  .tmb-head { margin-bottom: 20px; padding-bottom: 18px; }
  .tmb-reading-title { font-size: 20px; }
  .tmb-text { font-size: 14px; line-height: 1.95; }
  .tmb-foot { margin: 24px -20px 0; padding: 10px 12px; }
  .tmb-foot-mid { flex: 1 1 100%; margin-left: 0; justify-content: space-between; gap: 6px; }
  .tmb-foot-mid--reader { grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) minmax(0, 1fr); }
  .tmb-foot-mid > .tmb-btn { flex: 1 1 64px; }
  .tmb-meter { grid-template-columns: minmax(0, 1fr); gap: 6px; padding-bottom: 16px; margin-bottom: 16px; }
  .tmb-meter-row--sub { grid-column: 1; grid-row: 3; margin-top: 8px; }
  .tmb-meter-track:last-child { grid-row: 4; }
  .tmb-archive-range { flex-basis: 100%; padding-left: 26px; }
}
@media (prefers-reduced-motion: reduce) {
  .tmb-spine, .tmb-btn { transition: none; }
}
`
