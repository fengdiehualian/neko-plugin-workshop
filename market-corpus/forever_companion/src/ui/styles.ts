// Panel materials share neutral surfaces; wallpaper transparency is controlled by appearance variables.
export const PANEL_STYLES = `
.tm-backup { min-width: 0; }
.tm-backup-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.tm-backup-actions .neko-button, .tm-backup .neko-download { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
.tm-backup-preview { border-top: 1px solid var(--tm-divider); margin-top: 16px; padding-top: 12px; }
.tm-backup-preview h4 { font-size: 14px; margin: 0 0 10px; }
.tm-backup-preview p { margin: 10px 0; }
.tm-backup-roles { max-height: 100px; overflow: auto; overflow-wrap: anywhere; }
.tm-backup-path { overflow-wrap: anywhere; user-select: text; font-size: 12px; margin: 8px 0; }
.tm-backup-result { margin-top: 14px; }
.tm-backup-error { color: var(--danger); overflow-wrap: anywhere; }
.tm-mood-current { display: grid; gap: 5px; min-width: 0; }
.tm-mood-choice { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; overflow-wrap: anywhere; }
.tm-mood-choice strong { font-size: 15px; font-weight: 600; }
.tm-mood-choice-dot { width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: var(--muted); }
.tm-mood-choice-dot-active { background: var(--primary); }
.tm-mood-source { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.tm-mood-reason { min-width: 0; margin-top: 8px; }
.tm-mood-reason summary, .tm-mood-earlier summary { font-size: 12px; color: var(--secondary); cursor: pointer; padding: 5px 0; overflow-wrap: anywhere; }
.tm-mood-reason p { margin: 5px 0; font-size: 13px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.tm-mood-observation { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--tm-divider); }
.tm-mood-observation-title { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 13px; }
.tm-mood-observation-bars { display: grid; gap: 9px; margin-top: 12px; }
.tm-mood-observation-bar { display: grid; grid-template-columns: minmax(0, 100px) minmax(30px, 1fr) 32px; align-items: center; gap: 10px; font-size: 12px; }
.tm-mood-observation-bar > span { overflow-wrap: anywhere; }
.tm-mood-observation-value { text-align: right; font-variant-numeric: tabular-nums; }
.tm-mood-observation-track { height: 6px; border-radius: 3px; background: var(--tm-track); overflow: hidden; }
.tm-mood-observation-track > div { height: 100%; }
.tm-mood-actions { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.tm-mood-actions .neko-button { min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
.tm-mood-history { padding: 0; margin: 0; list-style: none; }
.tm-mood-event { display: grid; grid-template-columns: minmax(0, 94px) minmax(0, 1fr); gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--tm-divider); }
.tm-mood-event:last-child { border-bottom: 0; }
.tm-mood-event time { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.tm-mood-event-body { min-width: 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.tm-mood-earlier { border-top: 1px solid var(--tm-divider); padding-top: 8px; }
.tm-mood-empty { padding: 8px 0; }
@media (max-width: 420px) {
  .tm-mood-event { grid-template-columns: minmax(0, 74px) minmax(0, 1fr); gap: 10px; }
  .tm-mood-reason summary, .tm-mood-earlier summary { min-height: 36px; display: list-item; }
}
.neko-page {
  --text: #28282c; --muted: #64646c; --primary: #2676ba; --secondary: #466d91;
  --danger: #ba4054; --warning: #895211; --success: #397321; --info: #466d91;
  --radius-xl: 8px; --radius-lg: 8px; --radius-md: 8px; --radius-sm: 6px;
  --tm-surface-rgb: 255, 255, 255; --tm-surface-alpha: 0.92; --tm-chrome-alpha: 0.84; --tm-input-alpha: 0.94;
  --tm-border: rgba(40, 40, 44, 0.14); --tm-divider: rgba(40, 40, 44, 0.10);
  --tm-hover: rgba(40, 40, 44, 0.055); --tm-accent-soft: rgba(38, 118, 186, 0.10);
  --tm-focus: #2676ba; --tm-shadow: 0 2px 10px rgba(20, 20, 24, 0.055);
  --tm-warning-text: #895211; --tm-success-text: #397321; --tm-info-text: #286a83; --tm-config-text: #7954a4;
  --tm-moon-shadow: radial-gradient(circle at 36% 32%, #d1d1d6, #bcbcc4 80%);
  --tm-knob: #fff; --tm-track: rgba(90, 90, 100, 0.26);
  --tm-locked-bg: #dedee4; --tm-locked-glyph: #85858f; --tm-heat-empty: rgba(96, 96, 108, 0.14);
  --surface: rgba(var(--tm-surface-rgb), 0.92); --surface-strong: rgb(var(--tm-surface-rgb));
  --border: var(--tm-border); --shadow-soft: var(--tm-shadow);
  height: 100vh; padding: 0; gap: 0; display: flex; flex-direction: column; overflow: hidden;
  position: relative; z-index: 0; background: #f4f4f6; color: var(--text); letter-spacing: 0;
}

/* Role colors stay inside the plugin; raw wallpaper colors remain available for swatches. */
.tm-appearance-root {
  --primary: var(--tm-theme-primary-light, #2676ba);
  --secondary: var(--tm-theme-secondary-light, #466d91);
  --tm-accent-soft: color-mix(in srgb, var(--primary) 10%, transparent);
  --tm-secondary-soft: color-mix(in srgb, var(--secondary) 10%, transparent);
  --tm-focus: var(--primary); --info: var(--secondary); --tm-info-text: var(--secondary);
}

.neko-card, .tm-card, .tm-settings-surface {
  border: 1px solid var(--tm-border); border-radius: 8px;
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-surface-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-glass-filter, none);
  backdrop-filter: var(--tm-glass-filter, none);
  box-shadow: var(--tm-shadow);
}
.neko-input, .neko-select, .neko-textarea {
  color: var(--text); border-color: var(--tm-border);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-input-alpha) * var(--tm-card-k, 1)));
}
.neko-input::placeholder, .neko-textarea::placeholder { color: var(--muted); opacity: 0.8; }
.neko-select option { color: var(--text); background: rgb(var(--tm-surface-rgb)); }
.neko-input:focus, .neko-select:focus, .neko-textarea:focus { border-color: var(--primary); box-shadow: none; }
.neko-input[data-invalid="true"], .neko-select[data-invalid="true"], .neko-textarea[data-invalid="true"] {
  border-color: var(--danger); box-shadow: none;
}
.neko-page :is(button, a, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid var(--tm-focus); outline-offset: 2px;
}
.neko-button {
  color: var(--text); background: var(--tm-hover); border-color: var(--tm-border);
  border-radius: 6px; box-shadow: none; white-space: normal;
}
.neko-button:hover:not(:disabled) {
  background: var(--tm-accent-soft); border-color: var(--primary); box-shadow: none;
}
.neko-button[data-tone="primary"] {
  color: var(--primary); background: var(--tm-accent-soft); border-color: var(--primary); box-shadow: none;
}
.neko-button[data-tone="primary"]:hover:not(:disabled) {
  color: var(--text); background: var(--tm-accent-soft); border-color: var(--primary); box-shadow: none;
}
.neko-button[data-tone="danger"] { color: var(--danger, #d14f64); background: rgba(245, 108, 108, 0.10); border-color: rgba(245, 108, 108, 0.42); }
.neko-button[data-tone="success"] { color: var(--tm-success-text); background: rgba(103, 194, 58, 0.10); border-color: rgba(103, 194, 58, 0.38); }
.neko-button[data-tone="danger"]:hover:not(:disabled) { background: rgba(245, 108, 108, 0.16); border-color: var(--danger); }
.neko-button[data-tone="success"]:hover:not(:disabled) { background: rgba(103, 194, 58, 0.16); border-color: var(--success); }
.neko-button:active:not(:disabled) { transform: translateY(0) scale(0.98); }
/* 宿主 kit 对禁用按钮用了 cursor: wait（Windows 上显示转圈忙碌光标），这里纠正为不可点击 */
.neko-page .neko-button:disabled { cursor: not-allowed; }

/* Tooltips keep natural width; header actions anchor to the card's right edge. */
.neko-page .neko-tooltip-content {
  width: max-content;
  max-width: min(280px, 80vw);
  white-space: normal;
}
.tm-card-header .neko-tooltip[data-placement="bottom"] .neko-tooltip-content {
  left: auto; right: 0; max-width: min(280px, calc(100vw - 150px));
  overflow-wrap: anywhere; transform: translateY(-4px);
}
.tm-card-header .neko-tooltip[data-placement="bottom"]:is(:hover, :focus, :focus-within) .neko-tooltip-content {
  transform: translateY(0);
}

.tm-statusbar {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 14px 22px;
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-chrome-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-glass-filter, none); backdrop-filter: var(--tm-glass-filter, none);
  border-bottom: 1px solid var(--tm-border); box-shadow: none;
}

.tm-ring-wrap {
  position: relative; width: 80px; height: 80px; flex: 0 0 auto;
  cursor: pointer; user-select: none;
  transition: transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.tm-ring-wrap:hover { transform: scale(1.06); }
.tm-ring-wrap:active { transform: scale(0.96); }
.tm-ring-glow {
  display: none;
}
.tm-ring-track {
  position: absolute; inset: 0; border-radius: 999px; overflow: hidden;
  -webkit-mask: radial-gradient(farthest-side, transparent 61%, #000 62%);
  mask: radial-gradient(farthest-side, transparent 61%, #000 62%);
}
.tm-ring-surge-sweep {
  position: absolute; inset: -25%;
  background: conic-gradient(from 0deg, rgba(255, 255, 255, 0) 0deg, rgba(255, 255, 255, 0.9) 45deg, rgba(255, 255, 255, 0) 100deg);
  animation: tm-ring-surge 0.85s cubic-bezier(0.3, 0.6, 0.35, 1) both;
}
/* 外圈细轨道：中性发丝环，阶段分布由边界小点表达 */
.tm-ring-orbit {
  position: absolute; inset: 0; z-index: 2; border-radius: 999px; pointer-events: none;
  border: 2px solid rgba(100, 116, 139, 0.20);
}
.tm-ring-phase-dot {
  position: absolute; left: 50%; top: 50%; z-index: 2; pointer-events: none;
  width: 5px; height: 5px; margin-left: -2.5px; margin-top: -2.5px; border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.9);
}
.tm-ring-marker {
  position: absolute; z-index: 3; width: 12px; height: 12px; border-radius: 999px;
  background: var(--text); border: 2.5px solid rgba(255, 255, 255, 0.95);
  transform: translate(-50%, -50%);
  transition: transform 0.2s ease;
  animation: tm-breathe 2.4s ease-in-out infinite;
}
.tm-ring-wrap:hover .tm-ring-marker { transform: translate(-50%, -50%) scale(1.18); }

/* 中心月相盘：玻璃暗面 + 整面亮盘 + 同尺寸暗盘横向扫过（translateX 驱动盈亏）。
   原则：暗面=与面板融合的玻璃（新月≈隐身），只有亮部是实体；扫掠暗盘与暗面同材质，
   暗盘必须是实体遮盖（不透明渐变）--曾用 backdrop-filter 压暗下方亮盘，但面板运行环境
   里该滤镜不生效：暗盘只剩自身 10~15% 透明渐变，该遮住的区域整个透出亮盘纹理（穿模），
   已改为实体暗玻璃。无球面罩层--发光体要 bloom 不要灰。 */
.tm-moon {
  position: absolute; inset: 15%; z-index: 3; border-radius: 999px; overflow: hidden;
}
.tm-moon-base {
  position: absolute; inset: 0; border-radius: 999px;
  background: radial-gradient(circle at 36% 32%, rgba(148, 163, 184, 0.10), rgba(100, 116, 139, 0.15) 80%);
  -webkit-backdrop-filter: blur(3px);
  backdrop-filter: blur(3px);
  /* 新月轮廓光：右缘一线极淡亮边，让暗盘在月初也有月亮轮廓 */
  box-shadow: inset -1.5px 0 2px rgba(246, 239, 221, 0.22), inset 0 0 0 1px rgba(100, 116, 139, 0.12);
}
.tm-moon-lit {
  position: absolute; inset: 0; border-radius: 999px;
  background:
    radial-gradient(circle 7px at 63% 36%, rgba(168, 152, 118, 0.15), transparent 72%),
    radial-gradient(circle 5px at 41% 58%, rgba(168, 152, 118, 0.11), transparent 72%),
    radial-gradient(circle at 38% 32%, #fffef9, #f5edda 65%, #e9ddc2 100%);
  /* 内发光：让亮部像光源而不是纸片 */
  box-shadow: inset 0 0 12px rgba(255, 252, 240, 0.75);
}
/* 扫掠暗盘：与暗面同材质的实体暗玻璃（不透明），横向扫过遮住亮盘；边缘 0.5px 羽化。
   禁止改回半透明+backdrop-filter--面板环境该滤镜不生效，暗区会整个透出亮盘（穿模） */
.tm-moon-shadow {
  position: absolute; inset: 0; border-radius: 999px;
  background: var(--tm-moon-shadow);
  box-shadow: inset 0 0 0 1px rgba(100, 116, 139, 0.12);
  filter: blur(0.5px);
}
/* 月相盘中心的信息 pill：三视图文本 */
.tm-moon-pill {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  z-index: 4; max-width: 92%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  padding: 1px 7px; border-radius: 999px; font-size: 10px; font-weight: 700; line-height: 1.5;
  color: var(--text);
  background: rgba(var(--tm-surface-rgb), 0.85);
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
  box-shadow: 0 1px 5px rgba(2, 6, 23, 0.16);
  pointer-events: none;
  animation: tm-center-pop 0.32s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}

/* 圆环三视图指示点：让"点击切换"可发现，点本身也可直接点按切换 */
.tm-ring-col { display: flex; flex-direction: column; align-items: center; gap: 6px; flex: 0 0 auto; }
.tm-ring-dots { display: flex; gap: 5px; }
.tm-ring-dot {
  width: 6px; height: 6px; border-radius: 999px; border: none; padding: 0; cursor: pointer;
  background: rgba(100, 116, 139, 0.28);
  transition: background 0.18s ease, transform 0.18s ease;
}
.tm-ring-dot:hover { background: rgba(100, 116, 139, 0.5); }
.tm-ring-dot-active { background: var(--primary); transform: scale(1.25); }

.tm-status-text { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1 1 180px; }
.tm-phase-label { font-size: 19px; font-weight: 800; line-height: 1.25; }
.tm-status-sub { color: var(--muted); font-size: 12.5px; line-height: 1.5; }
.tm-status-right { display: flex; align-items: center; gap: 10px; margin-left: auto; }

/* ---- 精简状态条（0.9.0）：她是谁 · 一句话状态 · 心情胶囊 · 开关 ---- */
/* row-gap 5px：提示行换行后与主行的距离（column-gap 维持 12px） */
.tm-statusbar-slim { padding: 10px 22px; gap: 5px 12px; }
.tm-status-main {
  display: inline-flex; align-items: center; gap: 10px; min-width: 0;
  padding: 4px 10px 4px 6px; border: none; border-radius: 999px; cursor: pointer;
  background: transparent; color: var(--text); text-align: left;
  transition: background 0.15s ease;
}
.tm-status-main:hover { background: var(--tm-hover); }
.tm-status-dot { width: 9px; height: 9px; border-radius: 999px; flex: 0 0 auto; }
.tm-status-name { font-size: 14px; font-weight: 750; white-space: nowrap; }
.tm-status-summary { font-size: 13px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tm-statusbar-slim .tm-status-main:hover .tm-status-summary { color: var(--text); }

/* ---- 总览页（0.9.0）：陪伴仪表盘 ---- */
.tm-ov-hero { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.tm-ov-hero-text { display: flex; flex-direction: column; gap: 4px; min-width: 160px; flex: 1 1 200px; }
.tm-ov-name { font-size: 16px; font-weight: 800; opacity: 0.85; }
.tm-ov-mood-chip { display: inline-flex; align-items: center; gap: 10px; margin-top: 4px; flex-wrap: wrap; }
.tm-ov-mood { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
.tm-ov-mood-word { font-size: 26px; font-weight: 800; line-height: 1.2; min-width: 72px; }
.tm-ov-mood-bars { display: grid; gap: 10px; flex: 1 1 220px; }
.tm-ov-minibar { display: grid; grid-template-columns: 52px 1fr 34px; align-items: center; gap: 10px; }
.tm-ov-minibar-label { color: var(--muted); font-size: 12px; }
.tm-ov-minibar-value { color: var(--muted); font-size: 12px; text-align: right; }
.tm-ov-minibar-track {
  position: relative; height: 8px; border-radius: 999px; overflow: hidden;
  background: rgba(148, 163, 184, 0.22);
}
.tm-ov-minibar-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; z-index: 2; transition: left 0.3s ease, width 0.3s ease; }
.tm-ov-diary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.tm-ov-tile {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
  padding: 12px 14px; border-radius: var(--radius-md); cursor: pointer; text-align: left;
  border: 1px solid var(--tm-divider); background: transparent;
  color: var(--text);
  transition: border-color 0.15s ease, background 0.15s ease;
}
.tm-ov-tile:hover { background: var(--tm-hover); border-color: var(--primary); }
.tm-ov-tile-num { font-size: 22px; font-weight: 800; line-height: 1.1; }
.tm-ov-tile-label { font-size: 13px; font-weight: 650; }
.tm-ov-tile-sub { color: var(--muted); font-size: 11.5px; }
/* 生日设置卡（1.3.1 改版·二次，「时光」页）：纯设置项一行——日期按钮 + 自绘月历
   （内嵌展开，非浮层：Card 玻璃层 overflow/backdrop-filter 会裁掉绝对定位层，
   实机踩坑 2026-09-14）+ 纪念手记开关 + 清除（确认即保存，「保存」按钮与原生
   date input 一并退役：原生弹层是浏览器 UI，「今天」改不了文案也改不了行为）。
   总览生日卡的倒数/当天高亮形态已退役，对应样式随之下架。全 CSS 绘制、零 SVG。 */
.tm-bday-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tm-bday-field {
  font: inherit; font-size: 13px; padding: 5px 10px; border-radius: var(--radius-sm);
  border: 1px solid var(--tm-border);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-input-alpha) * var(--tm-card-k, 1)));
  color: var(--muted); cursor: pointer; font-variant-numeric: tabular-nums;
}
.tm-bday-field--set { color: var(--text); font-weight: 650; }
.tm-bday-field:hover { border-color: var(--primary); }
.tm-bday-field:disabled { opacity: 0.55; cursor: default; }
.tm-bday-pop {
  width: min(260px, 100%); margin-top: 10px; padding: 10px;
  border-radius: var(--radius-md); border: 1px solid var(--tm-border);
  background: var(--tm-hover);
}
.tm-bday-pop-head { display: flex; align-items: center; gap: 4px; margin-bottom: 6px; }
.tm-bday-pop-title { flex: 1 1 auto; text-align: center; font-size: 12.5px; font-weight: 700; color: var(--text); }
.tm-bday-nav {
  font: inherit; font-size: 13px; line-height: 1; cursor: pointer;
  width: 22px; height: 22px; border-radius: 8px;
  border: 1px solid transparent; background: transparent; color: var(--muted);
}
.tm-bday-nav:hover { background: var(--tm-hover); color: var(--text); }
.tm-bday-nav:disabled { opacity: 0.5; cursor: default; }
.tm-bday-week { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; margin-bottom: 2px; }
.tm-bday-week-cell { text-align: center; font-size: 10.5px; color: var(--muted); }
.tm-bday-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
.tm-bday-day {
  font: inherit; font-size: 12px; height: 26px; border-radius: 8px; cursor: pointer;
  border: 1px solid transparent; background: transparent; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.tm-bday-day:hover { background: var(--tm-hover); }
.tm-bday-day--blank { visibility: hidden; pointer-events: none; }
.tm-bday-day--today { border-color: var(--primary); color: var(--primary); font-weight: 700; }
.tm-bday-day--sel { background: var(--tm-accent-soft); border-color: var(--primary); color: var(--text); font-weight: 700; }
.tm-bday-day:disabled { opacity: 0.35; cursor: default; }
.tm-bday-pop-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.tm-bday-actions { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; }
.tm-bday-btn {
  font: inherit; font-size: 12.5px; font-weight: 650; cursor: pointer;
  padding: 6px 12px; border-radius: 999px; border: 1px solid transparent;
  background: var(--tm-accent-soft); border-color: var(--primary); color: var(--primary);
}
.tm-bday-btn:hover { filter: brightness(1.05); }
.tm-bday-btn--ghost { background: transparent; color: var(--muted); border-color: var(--tm-border); }
.tm-bday-btn:disabled { opacity: 0.55; cursor: default; }
.tm-ov-signals { display: grid; gap: 12px; margin-top: 14px; padding-top: 12px; border-top: 1px dashed rgba(148, 163, 184, 0.35); }
.tm-ov-signal { display: grid; gap: 6px; }
.tm-ov-signal-label { color: var(--muted); font-size: 12px; font-weight: 650; }
.tm-ov-signal-value { font-size: 13.5px; font-weight: 600; }

/* ---- 模型通道卡（0.9.0）：通道行 + 状态灯 ---- */
.tm-channel-row { display: grid; gap: 6px; padding: 10px 0; }
.tm-channel-head { display: flex; align-items: center; gap: 10px; }
.tm-channel-label { font-size: 13.5px; font-weight: 700; }
.tm-channel-light {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 650;
}
.tm-channel-light::before { content: ""; width: 7px; height: 7px; border-radius: 999px; background: currentColor; }
.tm-light-ok { background: rgba(103, 194, 58, 0.14); color: var(--tm-success-text); }
.tm-light-dormant { background: rgba(245, 176, 77, 0.16); color: var(--tm-warning-text); }
.tm-light-off { background: rgba(148, 163, 184, 0.16); color: var(--muted); }
.tm-channel-divider { height: 1px; margin: 4px 0; background: rgba(148, 163, 184, 0.25); }
/* 通道控件行：槽位与通道方式两个下拉并排分宽，窄窗下换行各占一行（1.3.2 前每行
   只有一个下拉、无需排版规则，加 mode 旋钮后必须给，否则两个控件竖着堆成两条） */
.tm-channel-control { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.tm-channel-control > * { flex: 1 1 160px; min-width: 0; }
.tm-channel-warn {
  margin-top: 10px; padding: 10px 12px; border-radius: var(--radius-md);
  font-size: 12px; line-height: 1.6;
  background: rgba(245, 176, 77, 0.10); border: 1px solid rgba(245, 176, 77, 0.35);
  color: var(--tm-warning-text);
}

/* 连续心情胶囊：心情词文字直接可见；胶囊底色随 valence 内联（暖琥珀/灰蓝/中性灰），
   光晕大小与呼吸速度随 arousal 内联 */
.tm-mood-pill {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  padding: 3px 10px; border-radius: 999px; flex: 0 0 auto;
  box-shadow: 0 1px 3px rgba(2, 6, 23, 0.18);
}
.tm-mood-pill-word {
  position: relative; z-index: 1; color: #ffffff; font-size: 12.5px; font-weight: 700;
  line-height: 1.5; text-shadow: 0 1px 2px rgba(2, 6, 23, 0.28); white-space: nowrap;
}
.tm-mood-pill-halo {
  display: none;
}

/* 当前心情仪表盘（情绪页）：大字心情词 + 双向愉悦度条 + 活跃度条 */
.tm-gauge { display: flex; flex-direction: column; gap: 10px; }
.tm-gauge-head { display: flex; align-items: baseline; gap: 12px; }
.tm-gauge-word { font-size: 22px; font-weight: 800; line-height: 1.3; }
.tm-gauge-hint { color: var(--muted); font-size: 12px; line-height: 1.5; }
.tm-gauge-row { display: flex; align-items: center; gap: 10px; }
.tm-gauge-label { flex: 0 0 52px; color: var(--muted); font-size: 12.5px; }
/* 轨道：渐变底色让"量程"本身可见——愉悦度左灰蓝→中灰→右暖琥珀，活跃度浅灰渐深；
   游标（marker）永远可见，静息态也有明确读数位置 */
.tm-gauge-track {
  position: relative; flex: 1; height: 12px; border-radius: 999px;
  box-shadow: inset 0 1px 2px rgba(2, 6, 23, 0.08);
}
.tm-gauge-track-bipolar {
  background: linear-gradient(90deg,
    rgba(110, 141, 171, 0.30) 0%, rgba(148, 163, 184, 0.14) 50%, rgba(245, 176, 77, 0.30) 100%);
}
.tm-gauge-track-plain {
  background: linear-gradient(90deg,
    rgba(100, 116, 139, 0.20) 0%, rgba(96, 165, 250, 0.24) 50%, rgba(244, 63, 94, 0.24) 100%);
}
.tm-gauge-zero {
  position: absolute; left: 50%; top: -1px; bottom: -1px; width: 1.5px;
  background: rgba(100, 116, 139, 0.55); transform: translateX(-50%);
}
.tm-gauge-baseline-tick {
  position: absolute; top: -1px; bottom: -1px; width: 1.5px;
  background: rgba(37, 99, 235, 0.55); transform: translateX(-50%);
}
.tm-gauge-fill {
  position: absolute; top: 0; bottom: 0; border-radius: 999px; opacity: 0.75;
  transition: left 0.3s ease, width 0.3s ease, background 0.3s ease;
}
.tm-gauge-marker {
  position: absolute; top: 50%; width: 14px; height: 14px; border-radius: 999px;
  transform: translate(-50%, -50%);
  border: 2px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 1px 4px rgba(2, 6, 23, 0.30), 0 0 6px rgba(255, 255, 255, 0.25);
  transition: left 0.3s ease, background 0.3s ease;
}
.tm-gauge-value {
  flex: 0 0 44px; text-align: right; font-size: 12.5px; font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums;
}

.tm-warnstrip { padding: 8px 16px 0; }
.tm-warnstrip .neko-alert { padding: 5px 12px; border-radius: var(--radius-sm); font-size: 12.5px; line-height: 1.5; }
/* 关闭状态细提示（1.1.7）：状态条卡片内第二行（.tm-statusbar 本就 flex-wrap，
   整宽子元素自动换行）。替代旧的两条独立 Alert 条与卡片间悬浮提示行——
   提示收进卡片内部后，状态条与内容区之间不再有任何夹缝元素 */
.tm-status-hints { flex: 1 0 100%; display: flex; flex-wrap: wrap; gap: 2px 18px; font-size: 12px; color: var(--muted); line-height: 1.5; }
.tm-offhint-item { display: inline-flex; align-items: center; gap: 6px; }
.tm-offhint-dot { opacity: 0.6; }
.tm-offhint-link {
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12px; color: var(--primary); text-decoration: underline; text-underline-offset: 2px;
}
.tm-offhint-link:hover { opacity: 0.8; }
.tm-offhint-link:disabled { cursor: default; opacity: 0.6; }

.tm-body { flex: 1; display: flex; min-height: 0; }
.tm-tabs {
  display: flex; flex-direction: column; gap: 4px; min-width: 76px; padding: 12px 6px;
  border-right: 1px solid var(--tm-border);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-chrome-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-sidebar-glass-filter, none);
  backdrop-filter: var(--tm-sidebar-glass-filter, none);
  /* 小窗滚动（终态）：宿主插件详情窗口高度不够时，shell（100vh +
     overflow:hidden）会把末页页签裁掉且无从触达——导航栏自身可滚即可。
     滚动条本体彻底隐藏（用户反馈：看得见反而不如纯滚动手感舒服），
     滚轮/触控板/拖滑块照常；scrollbar-width:none 不占 gutter，侧栏宽度
     不被吃。历经三版演进：常驻细条→悬停浮现（:hover 命不中槽、改 pointer
     右缘命中带）→无条纯滚，停在这版别再回头 */
  overflow-y: auto; overscroll-behavior: contain;
  scrollbar-width: none;
}
.tm-tabs::-webkit-scrollbar { width: 0; height: 0; display: none; }
.tm-tab {
  display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 8px;
  border: 1px solid transparent; border-radius: var(--radius-md); background: transparent;
  color: var(--muted); font-size: 12.5px; cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, border-color 0.18s ease;
}
.tm-tab:hover { background: var(--tm-hover); color: var(--text); }
.tm-tab-active {
  background: var(--tm-accent-soft); border-color: var(--tm-divider); color: var(--primary);
  box-shadow: inset 3px 0 0 var(--primary);
}
.tm-tab .tm-ico { opacity: 0.7; }
.tm-tab:hover .tm-ico { opacity: 0.9; }
.tm-tab-active .tm-ico { opacity: 1; }

.tm-content {
  flex: 1; min-width: 0; overflow-y: auto; overflow-x: hidden; padding: 16px 18px;
  /* 滚动条静默化（panel.tsx contentHot/contentScrolling）：槽 8px 恒占位
     避免浮现抽动，轨道/滑块默认全透明即隐形；.tm-content-hot（悬停槽位
     或滚动中）才着色。
     ⚠两坑：①不用 scrollbar-color/scrollbar-width 非 auto 值——Chromium 下一
     设它们就整体屏蔽 ::-webkit-scrollbar 定制，垂直条退回 ~17px 经典宽吃宽度；
     ②overflow-x 必须显式 hidden——overflow-y:auto 会把 visible 的 overflow-x
     连坐成 auto，入场动画 translateX/1fr min-content 等几 px 瞬时溢出都会弹
     横向条（日历页反馈同因）。页面不横滚；长热力图的滚动独立在图内。 */
}
.tm-content::-webkit-scrollbar { width: 8px; }
.tm-content::-webkit-scrollbar-track { background: transparent; }
.tm-content::-webkit-scrollbar-thumb { background: transparent; border-radius: 999px; }
.tm-content-hot::-webkit-scrollbar-thumb { background: rgba(100, 116, 139, 0.4); }
.tm-content::-webkit-scrollbar-thumb:hover { background: rgba(100, 116, 139, 0.6); }

.tm-pane { display: grid; gap: 12px; align-content: start; animation: tm-pane-in 280ms cubic-bezier(0.22, 0.61, 0.36, 1) both; }

.tm-card-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px 0; }
.tm-card-title { margin: 0; font-size: 15px; font-weight: 720; }
.tm-card-body { padding: 12px 14px 14px; display: grid; gap: 12px; }

.tm-iconbtn {
  width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center;
  border-radius: var(--radius-md); border: 1px solid transparent; background: transparent;
  font-size: 16px; line-height: 1; cursor: pointer; color: var(--muted);
  transition: background 0.18s ease, transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.tm-iconbtn:hover { background: var(--tm-hover); color: var(--text); }
.tm-iconbtn-active { background: var(--tm-accent-soft); border-color: var(--primary); color: var(--primary); }

.tm-cal-nav { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.tm-cal-label { font-weight: 650; }
.tm-cal-head, .tm-cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; } /* minmax(0,1fr)：1fr 隐式最小 auto 不收缩到 min-content 以下，小窗撞线 */
.tm-cal-wd { color: var(--muted); font-size: 12px; text-align: center; padding: 2px 0; }
.tm-cal-cell {
  padding: 4px 0; text-align: center; border-radius: var(--radius-sm); font-size: 13px;
  border: 1px solid transparent;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease, background 0.3s ease, opacity 0.3s ease;
}
.tm-cal-cell[data-phase="menstrual"] { background: rgba(245, 108, 108, 0.16); }
.tm-cal-cell[data-phase="follicular"] { background: rgba(103, 194, 58, 0.14); }
.tm-cal-cell[data-phase="ovulatory"] { background: rgba(20, 184, 166, 0.14); }
/* 锚点前的日子按平稳期着色（cycle.py 的标签映射同语义），灰格改为中性底 */
.tm-cal-cell[data-phase="luteal"], .tm-cal-cell[data-phase="before_start"] { background: rgba(100, 116, 139, 0.10); }
.tm-cal-cell[data-today="1"] { border-color: var(--primary); font-weight: 700; }
.tm-cal-cell[data-future="1"] { opacity: 0.55; }
.tm-cal-cell[data-selectable="1"] { cursor: pointer; }
.tm-cal-cell[data-selectable="1"]:hover { border-color: var(--primary); opacity: 1; }
.tm-cal-cell[data-picked="1"] {
  border-color: var(--primary); font-weight: 700; opacity: 1;
  box-shadow: inset 0 0 0 1px var(--primary);
  animation: tm-pick-pop 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.tm-cal-tidebar {
  display: block; width: 12px; height: 2.5px; margin: 2px auto 0;
  border-radius: 999px; background: var(--danger); opacity: 0.75;
}
.tm-legend { margin-top: 10px; display: flex; gap: 14px; flex-wrap: wrap; align-items: center; color: var(--muted); font-size: 12.5px; }
.tm-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.tm-dot { width: 9px; height: 9px; border-radius: 999px; display: inline-block; }
.tm-dot-today { border: 1.5px solid var(--primary); }
.tm-cal-actions { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; animation: tm-slide-in 0.25s cubic-bezier(0.22, 0.61, 0.36, 1) both; }
.tm-picked-label { color: var(--text); font-size: 12.5px; font-weight: 650; }
.tm-pick-hint { color: var(--primary); font-size: 12.5px; animation: tm-hint-pulse 1.6s ease-in-out infinite; }
.tm-cal-meta { color: var(--muted); font-size: 12px; }

.tm-tool { display: grid; gap: 8px; align-content: start; }
.tm-derived { color: var(--muted); font-size: 12.5px; line-height: 1.6; }

.tm-lanlan-list { display: grid; gap: 8px; }
.tm-lanlan-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 9px 12px; border-radius: var(--radius-md);
  background: transparent;
  border: 1px solid var(--tm-divider);
}
.tm-lanlan-row[data-orphan="1"] { border-color: rgba(245, 108, 108, 0.35); background: rgba(245, 108, 108, 0.06); }
.tm-lanlan-name { font-weight: 650; }
.tm-lanlan-current { color: var(--primary); font-size: 12px; font-weight: 650; }
.tm-lanlan-meta { color: var(--muted); font-size: 12.5px; }
.tm-lanlan-spacer { margin-left: auto; }
/* 保存条吸底：设置页滚动到底部仍可见，磨砂底与面板一致；负外边距抵消 .tm-content 内边距实现通栏 */
.tm-save {
  position: sticky; bottom: 0; z-index: 5;
  display: flex; justify-content: flex-end;
  margin: 0 -18px -16px; padding: 10px 18px;
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-chrome-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-glass-filter, none); backdrop-filter: var(--tm-glass-filter, none);
  border-top: 1px solid var(--tm-border); box-shadow: none;
}
/* 无 help 文案的 Field 预留一行 help 的高度（12px×1.5=18px），保证同一 Grid 内各词条等高、底部对齐 */
.tm-reserve-help::after { content: ""; display: block; height: 18px; }
/* Chromium 下同样式的 select 比 input 高 2px，统一控件高度，保证设置项等高对齐 */
.tm-pane .neko-input, .tm-pane .neko-select { height: 40px; }

.tm-diary { display: grid; gap: 10px; }
.tm-diary-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.tm-diary-count { color: var(--muted); font-size: 12.5px; }

/* 时光日记时间线：她的手记（暖调）与自动碎片（蓝调）混排，来源徽标 + 类型着色；
   按日期分组的组头吸顶，扫读时始终知道自己在看哪一天 */
.tm-timeline { display: grid; gap: 8px; }
.tm-day-group { display: grid; gap: 7px; }
.tm-day-head {
  position: sticky; top: 0; z-index: 2;
  display: flex; align-items: baseline; gap: 8px;
  padding: 5px 6px; margin: 0 -2px;
  border-radius: var(--radius-sm);
  font-size: 12.5px; font-weight: 750; color: var(--text);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-input-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-glass-filter, none); backdrop-filter: var(--tm-glass-filter, none);
}
.tm-day-count { font-weight: 550; color: var(--muted); font-size: 11.5px; }
.tm-tl-row {
  display: grid; gap: 5px; padding: 10px 12px; border-radius: var(--radius-md);
  border: 1px solid var(--tm-divider); border-left: 2px solid rgba(245, 176, 77, 0.5);
  background: transparent;
}
.tm-tl-row[data-source="auto"] { border-left-color: rgba(110, 141, 171, 0.55); }
.tm-tl-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tm-tl-badge {
  display: inline-flex; align-items: center; gap: 3px; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 650; line-height: 1.6;
  background: rgba(245, 176, 77, 0.18); color: var(--text);
}
.tm-tl-row[data-source="auto"] .tm-tl-badge { background: rgba(64, 158, 255, 0.14); }
.tm-tl-badge[data-kind="overstep"] { background: rgba(245, 108, 108, 0.16); }
.tm-tl-badge[data-kind="dislike"] { background: rgba(250, 204, 21, 0.20); }
.tm-tl-badge[data-kind="like"] { background: rgba(103, 194, 58, 0.15); }
.tm-tl-mood { color: var(--muted); font-size: 12px; font-weight: 650; }
.tm-tl-ts { color: var(--muted); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.tm-tl-spacer { margin-left: auto; }
.tm-tl-delete {
  border: none; background: transparent; color: var(--muted); font-size: 11.5px; cursor: pointer;
  padding: 1px 6px; border-radius: var(--radius-sm); transition: background 0.15s ease, color 0.15s ease;
}
.tm-tl-delete:hover { background: rgba(245, 108, 108, 0.14); color: var(--danger, #f56c6c); }
/* 碎片原话的"他说的话"气泡：左侧竖线 + 淡蓝底，和她的正文一眼区分 */
.tm-tl-quote {
  padding: 6px 10px; border-left: 2px solid var(--tm-border);
  background: transparent; border-radius: 0;
  font-size: 13px; font-weight: 650; line-height: 1.55;
}
.tm-tl-note { color: var(--muted); font-size: 12px; line-height: 1.5; }
.tm-tl-entry { font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
.tm-diary-more { display: flex; justify-content: center; padding-top: 2px; }
.tm-diary-end { text-align: center; color: var(--muted); font-size: 12px; padding: 4px 0 2px; }

/* 个人日记/我的日记工具行 + 心情圆点（复用状态栏配色算法）。
   1.3.0：目录与书页改拟真书本（书脊/纸页），样式见 ui/styles_book.ts */
.tm-journal-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.tm-journal-actions { display: inline-flex; align-items: center; gap: 8px; }
.tm-mood-dot {
  width: 10px; height: 10px; border-radius: 999px; display: inline-block; flex: 0 0 auto;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.7);
}

/* 我的日记：素材进度条（下一份评价攒了多少轮）+ 设置子卡标题 */
.tm-review { display: grid; gap: 10px; }
.tm-review-progress { display: grid; gap: 6px; }
.tm-review-bar {
  height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(148, 163, 184, 0.22);
}
.tm-review-bar-fill {
  height: 100%; border-radius: 999px;
  background: var(--secondary);
  transition: width 0.3s ease;
}
.tm-review-settings {
  margin-top: 14px; padding-top: 12px; display: grid; gap: 10px;
  border-top: 1px dashed rgba(148, 163, 184, 0.35);
}
.tm-subcard-title { font-size: 13px; font-weight: 700; }
.tm-number-input {
  width: 120px; padding: 6px 10px; border-radius: var(--radius-md);
  border: 1px solid var(--tm-border);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-input-alpha) * var(--tm-card-k, 1)));
  font-size: 13px; color: var(--text);
}

/* 1.3.0：旧「米色卡片」书页（.tm-book* / .tm-paper）已整体换成拟真书本，
   书架/纸页/丝带/朱印一律见 ui/styles_book.ts */

@keyframes tm-pane-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes tm-slide-in { from { opacity: 0; transform: translateX(12px); } to { opacity: 1; transform: none; } }
@keyframes tm-pick-pop { from { transform: scale(0.85); } to { transform: scale(1); } }
@keyframes tm-breathe { 0%, 100% { box-shadow: 0 0 0 0 var(--tm-accent-soft); } 50% { box-shadow: 0 0 0 7px transparent; } }
@keyframes tm-hint-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
@keyframes tm-ring-surge { from { transform: rotate(0deg); opacity: 1; } to { transform: rotate(340deg); opacity: 0; } }
@keyframes tm-center-pop { from { opacity: 0; transform: scale(0.72); } to { opacity: 1; transform: scale(1); } }

@media (prefers-color-scheme: dark) {
  .neko-page {
    --text: #f0f0f2; --muted: #b8b8c1; --primary: #8bc5ff; --secondary: #9dbfdf;
    --danger: #ff919a; --warning: #f4c374; --success: #a4d989; --info: #9dbfdf;
    --tm-surface-rgb: 32, 32, 36; --tm-surface-alpha: 0.94; --tm-chrome-alpha: 0.90; --tm-input-alpha: 0.96;
    --tm-border: rgba(232, 232, 240, 0.18); --tm-divider: rgba(232, 232, 240, 0.12);
    --tm-hover: rgba(232, 232, 240, 0.065); --tm-accent-soft: rgba(139, 197, 255, 0.12);
    --tm-focus: #8bc5ff; --tm-shadow: 0 2px 12px rgba(0, 0, 0, 0.18);
    --tm-warning-text: #f4c374; --tm-success-text: #a4d989; --tm-info-text: #99d0e4; --tm-config-text: #d0b6ed;
    --tm-moon-shadow: radial-gradient(circle at 36% 32%, #38383e, #29292f 80%);
    --tm-knob: #f0f0f2; --tm-track: rgba(210, 210, 220, 0.24);
    --tm-locked-bg: #53535e; --tm-locked-glyph: #d0d0d8; --tm-heat-empty: rgba(210, 210, 222, 0.15);
    background: #18181c;
  }
  .tm-appearance-root {
    --primary: var(--tm-theme-primary-dark, #8bc5ff);
    --secondary: var(--tm-theme-secondary-dark, #9dbfdf);
    --tm-accent-soft: color-mix(in srgb, var(--primary) 12%, transparent);
    --tm-secondary-soft: color-mix(in srgb, var(--secondary) 12%, transparent);
  }
  .tm-ring-dot { background: rgba(148, 163, 184, 0.35); }
  .tm-ring-dot:hover { background: rgba(148, 163, 184, 0.6); }
  .tm-ring-dot-active { background: var(--primary); }
  .tm-lanlan-row[data-orphan="1"] { border-color: rgba(245, 108, 108, 0.3); background: rgba(245, 108, 108, 0.08); }
  .tm-ring-orbit { border-color: rgba(148, 163, 184, 0.28); }
  .tm-ring-phase-dot { border-color: rgba(15, 23, 42, 0.85); }
  .tm-moon-pill { box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35); }
  .tm-moon-base { background: radial-gradient(circle at 36% 32%, rgba(148, 163, 184, 0.06), rgba(100, 116, 139, 0.10) 80%); }
  .tm-ring-marker { border-color: rgba(15, 23, 42, 0.9); }
  .tm-mood-pill { box-shadow: 0 1px 3px rgba(2, 6, 23, 0.5); }
  .tm-gauge-track { box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.35); }
  .tm-gauge-track-bipolar {
    background: linear-gradient(90deg,
      rgba(110, 141, 171, 0.38) 0%, rgba(148, 163, 184, 0.16) 50%, rgba(245, 176, 77, 0.38) 100%);
  }
  .tm-gauge-track-plain {
    background: linear-gradient(90deg,
      rgba(148, 163, 184, 0.18) 0%, rgba(96, 165, 250, 0.30) 50%, rgba(244, 63, 94, 0.30) 100%);
  }
  .tm-gauge-zero { background: rgba(148, 163, 184, 0.55); }
  .tm-gauge-baseline-tick { background: rgba(147, 197, 253, 0.6); }
  .tm-gauge-marker { border-color: rgba(15, 23, 42, 0.9); box-shadow: 0 1px 4px rgba(2, 6, 23, 0.6); }
  .tm-mood-dot { box-shadow: 0 0 0 2px rgba(32, 32, 36, 0.8); }
  .tm-review-bar { background: rgba(148, 163, 184, 0.18); }
  .tm-review-settings { border-top-color: rgba(148, 163, 184, 0.22); }
}

@media (prefers-reduced-motion: reduce) {
  .tm-pane, .tm-cal-actions, .tm-ring-marker, .tm-pick-hint, .tm-cal-cell,
  .tm-ring-wrap, .tm-moon-pill, .tm-mood-pill-halo { animation: none !important; transition: none !important; }
  .tm-ring-surge-sweep { display: none !important; }
}

/* ============================================================
   面板外观（1.2.0：图片图库 + 可调背景）
   ============================================================ */
/* Only the image is filtered; the independent dim layer must remain unaffected. */
.tm-bg { position: absolute; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
.tm-bg-image { position: absolute; inset: 0; background-repeat: no-repeat; }
.tm-bg-video { display: block; max-width: none; border: 0; }
.tm-bg-dim { position: absolute; inset: 0; background: #18181c; }
.tm-statusbar, .tm-body, .tm-warnstrip { position: relative; z-index: 1; }
/* 整体字体显示强度：只作用于正文继承链上的文字（按钮/徽标等自带色的组件不受影响）；
   低浓度自动加深投影保证可读。color-mix 不支持的老浏览器回退原 --text */
.tm-has-bg .tm-body, .tm-has-bg .tm-statusbar {
  color: var(--text);
  color: var(--tm-text-color, var(--text));
  text-shadow: var(--tm-text-shadow, none);
}

/* Settings use flat sections; hidden views stay mounted during imports. */
.tm-settings { gap: 0; }
.tm-settings [hidden] { display: none !important; }
.tm-settings-surface { display: grid; min-width: 0; overflow: hidden; }
.tm-settings-nav {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--tm-border);
}
.tm-settings-tab, .tm-media-tab {
  box-sizing: border-box; min-width: 0; min-height: 44px; padding: 10px 8px;
  border: 0; border-bottom: 2px solid transparent; border-radius: 0;
  background: transparent; color: var(--muted); font: inherit; font-size: 13px; line-height: 1.5;
  overflow-wrap: anywhere; cursor: pointer; letter-spacing: 0;
}
.tm-settings-tab[aria-selected="true"], .tm-media-tab[aria-selected="true"] {
  border-bottom-color: var(--primary); color: var(--primary); font-weight: 600;
}
.tm-settings-tab:hover, .tm-media-tab:hover { background: var(--tm-accent-soft); }
.tm-settings-section { display: grid; gap: 14px; min-width: 0; padding: 16px; }
.tm-settings-section + .tm-settings-section { border-top: 1px solid var(--tm-border); }
.tm-settings-section h3 { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.5; overflow-wrap: anywhere; }
.tm-settings-disclosure { min-width: 0; border-top: 1px solid var(--tm-border); }
.tm-settings-disclosure > summary {
  box-sizing: border-box; min-height: 44px; padding: 10px 8px; border-radius: 4px;
  font-size: 13px; font-weight: 600; line-height: 1.5; list-style-position: inside; cursor: pointer; overflow-wrap: anywhere;
}
.tm-settings-disclosure > summary:hover { background: var(--tm-accent-soft); }
.tm-settings-disclosure > summary:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: 2px; }
.tm-settings-disclosure[open] > summary { color: var(--primary); }
.tm-settings-disclosure-body { display: grid; gap: 12px; min-width: 0; padding: 8px 4px 12px; }
.tm-settings-reset-row { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; }
.tm-settings-danger h3 { color: var(--danger); }
.tm-settings .tm-reopen-guide { margin-top: 0; }
.tm-wallpaper-current { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; font-size: 12px; color: var(--muted); }
.tm-wallpaper-current strong { min-width: 0; color: var(--text); font-weight: 500; overflow-wrap: anywhere; }
.tm-media-tabs { display: flex; gap: 12px; border-bottom: 1px solid var(--tm-border); }
.tm-media-tab { display: flex; align-items: center; justify-content: center; gap: 8px; min-width: 80px; }
.tm-media-count { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tm-gallery-empty { padding: 12px 0 4px; color: var(--muted); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.tm-media-import-fields { display: grid; gap: 10px; min-width: 0; }

/* Appearance gallery and adjustments share one column. */
.tm-appearance { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; gap: 10px; }
.tm-appearance .neko-field { min-width: 0; }
.tm-appearance .neko-segmented { display: flex; flex-wrap: wrap; }
.tm-appearance .neko-segmented-button { flex: 1 1 90px; min-width: 0; font-size: 13px; line-height: 1.4; white-space: normal; overflow-wrap: anywhere; }
.tm-appearance-adjust { min-width: 0; border-top: 1px solid var(--tm-border); }
.tm-appearance-adjust-summary {
  box-sizing: border-box; min-height: 44px; padding: 10px 8px;
  font-size: 13px; font-weight: 600; line-height: 1.5; color: var(--text);
  border-radius: 4px; list-style-position: inside; overflow-wrap: anywhere; cursor: pointer;
}
.tm-appearance-adjust-summary::marker { color: var(--primary); }
.tm-appearance-adjust-summary:hover { background: var(--tm-accent-soft); }
.tm-appearance-adjust-summary:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: 2px; }
.tm-appearance-adjust[open] > .tm-appearance-adjust-summary { color: var(--primary); }
.tm-appearance-adjust-body { display: grid; min-width: 0; gap: 10px; padding: 10px 8px 2px; }
.tm-appearance-adjust-body .neko-slider-input { scroll-margin-block-end: var(--tm-save-clearance, 72px); }
.tm-appearance-save {
  display: flex; gap: 8px; align-items: center;
  border-top: 1px solid var(--tm-border);
  padding-top: 10px;
}
.tm-save-hint { margin-right: auto; font-size: 12px; color: var(--muted); }

/* 图库网格：缩略图平铺 + 左上「不用壁纸」格 + 角标/删除钮 */
.tm-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; }
.tm-gallery-tile {
  position: relative; height: 114px; box-sizing: border-box; border-radius: 8px; overflow: hidden;
  border: 1px solid var(--tm-border); background: var(--tm-hover);
}
.tm-gallery-pick {
  position: absolute; inset: 0; width: 100%; height: 100%; padding: 0; border: 0;
  display: grid; place-items: center; border-radius: inherit; color: var(--text); cursor: pointer;
  background-color: transparent; background-size: cover; background-position: center; background-repeat: no-repeat;
}
.tm-gallery-pick:hover { box-shadow: inset 0 0 0 2px var(--tm-border); }
.tm-gallery-pick:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: -4px; }
.tm-gallery-active { border: 2px solid var(--primary); }
.tm-gallery-applied:not(.tm-gallery-active) { border-color: var(--primary); }
.tm-gallery-preview .tm-gallery-use { color: var(--primary); }
.tm-gallery-none { background: var(--tm-hover); }
.tm-gallery-none-mark { font-size: 22px; line-height: 1; color: var(--muted); text-shadow: none; }
.tm-gallery-name {
  position: absolute; left: 0; right: 0; bottom: 0; height: 26px; box-sizing: border-box; z-index: 1;
  padding: 5px 7px; font-size: 11px; line-height: 16px; color: var(--text);
  background: rgba(var(--tm-surface-rgb), 0.9); pointer-events: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* 无缩略图（旧迁移图回填前的窗口期）：棋盘格占位 */
.tm-gallery-thumbless {
  background-image: repeating-conic-gradient(rgba(148, 163, 184, 0.35) 0% 25%, transparent 0% 50%);
  background-size: 16px 16px;
}
.tm-gallery-use {
  position: absolute; left: 4px; top: 4px; z-index: 1; max-width: calc(100% - 30px);
  padding: 2px 5px; border-radius: 4px; font-size: 10px; line-height: 1.4;
  color: var(--text); background: rgba(var(--tm-surface-rgb), 0.94); pointer-events: none;
  overflow-wrap: anywhere;
}
.tm-gallery-del {
  position: absolute; top: 3px; right: 3px; width: 20px; height: 20px; border-radius: 50%;
  z-index: 2; display: grid; place-items: center; font-size: 14px; line-height: 1; color: #fff;
  padding: 0; border: 0; background: rgba(28, 28, 32, 0.82); cursor: pointer; opacity: 0.9;
}
.tm-gallery-del:hover { background: var(--danger); opacity: 1; }
.tm-gallery-del:focus-visible { outline-offset: 1px; opacity: 1; }
.tm-gallery-video-mark {
  position: absolute; right: 7px; bottom: 34px; width: 23px; height: 20px;
  border-radius: 3px; background: rgba(28, 28, 32, 0.82); pointer-events: none;
}
.tm-gallery-video-mark::after {
  content: ""; position: absolute; left: 9px; top: 6px;
  border-left: 7px solid #fff; border-top: 4px solid transparent; border-bottom: 4px solid transparent;
}
.tm-media-import { display: grid; min-width: 0; gap: 10px; }
.tm-media-import-busy .neko-image-upload { pointer-events: none; opacity: 0.6; }
.tm-wallpaper-video-input, .tm-wallpaper-directory-input {
  display: block; box-sizing: border-box; width: 100%; max-width: 100%; min-width: 0;
  padding: 6px; border: 1px solid var(--tm-border); border-radius: 4px;
  color: var(--text); background: var(--tm-hover); font: inherit; font-size: 12px;
}
.tm-wallpaper-video-input::file-selector-button, .tm-wallpaper-directory-input::file-selector-button {
  max-width: 100%; padding: 7px 9px; margin-right: 8px; border-radius: 4px;
  border: 1px solid var(--tm-border); background: var(--tm-button-bg); color: var(--text);
  font: inherit; white-space: normal; cursor: pointer;
}
.tm-wallpaper-video-input:focus-visible, .tm-wallpaper-directory-input:focus-visible {
  outline: 2px solid var(--tm-focus); outline-offset: 2px;
}
.tm-video-progress { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; }
.tm-wallpaper-candidates { display: grid; min-width: 0; gap: 7px; }
.tm-wallpaper-candidate {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; min-width: 0; gap: 12px;
  align-items: center; padding: 8px 0; border-bottom: 1px solid var(--tm-border);
}
.tm-wallpaper-candidate-name { display: grid; min-width: 0; gap: 3px; font-size: 12px; overflow-wrap: anywhere; }
.tm-wallpaper-candidate-name span { color: var(--muted); font-size: 11px; }
.tm-wallpaper-candidate .neko-button { max-width: 120px; white-space: normal; overflow-wrap: anywhere; }
.tm-wallpaper-motion-label { display: flex; gap: 8px; align-items: center; font-size: 13px; cursor: pointer; }
.tm-wallpaper-motion { width: 18px; height: 18px; flex: 0 0 18px; accent-color: var(--primary); }
.tm-media-storage { display: grid; min-width: 0; gap: 8px; }
.tm-media-policy { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.tm-media-policy label { display: grid; flex: 1 1 130px; gap: 4px; min-width: 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.tm-media-policy input { width: 100%; box-sizing: border-box; min-height: 32px; border: 1px solid var(--tm-border); border-radius: 5px; padding: 5px 7px; color: var(--text); background: var(--surface); }
.tm-media-policy input:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: 1px; }

.tm-theme-colors { display: flex; align-items: center; flex-wrap: wrap; min-width: 0; gap: 10px 18px; }
.tm-theme-source { color: var(--muted); font-size: 12px; flex: 0 1 auto; overflow-wrap: anywhere; }
.tm-theme-color { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; min-width: 0; max-width: 100%; font-size: 12px; color: var(--text); }
.tm-theme-color code { color: var(--muted); font-size: 12px; white-space: nowrap; }
.tm-theme-swatch { width: 18px; height: 18px; flex: 0 0 18px; border-radius: 4px; border: 1px solid var(--tm-border); }

/* 背景调节：填充/位置/滑杆组；未选壁纸时整组降透明度（控件已 disabled） */
.tm-adjust-grid { display: grid; gap: 10px; }
.tm-adjust-off { opacity: 0.45; pointer-events: none; }
.tm-pos-grid { display: inline-grid; grid-template-columns: repeat(3, 30px); grid-auto-rows: 30px; gap: 4px; }
.tm-pos-cell {
  border: 1px solid var(--tm-border); border-radius: 6px; padding: 0;
  background: var(--tm-hover); cursor: pointer;
  display: grid; place-items: stretch;
}
.tm-pos-active { border: 2px solid var(--primary); background: var(--tm-accent-soft); }
.tm-pos-dot { width: 7px; height: 7px; border-radius: 50%; margin: 6px; background: var(--muted); }
.tm-pos-active .tm-pos-dot { background: var(--primary); }

/* ============================================================
   纯 CSS 线框图标（hosted 运行时不支持 SVG，颜色随 currentColor）
   ============================================================ */
.tm-ico { position: relative; display: inline-block; width: 16px; height: 16px; flex: 0 0 auto; }
/* 日历：圆角边框 + 顶部横线 */
.tm-ico-calendar { border: 1.5px solid currentColor; border-radius: 3px; }
.tm-ico-calendar::before {
  content: ""; position: absolute; left: 1px; right: 1px; top: 3.5px;
  height: 1.5px; background: currentColor;
}
/* 周期：圆环 + 轨道上的小点（呼应状态栏圆环的今日标记） */
.tm-ico-cycle { border: 1.5px solid currentColor; border-radius: 999px; }
.tm-ico-cycle::after {
  content: ""; position: absolute; right: 0; top: 0;
  width: 4px; height: 4px; border-radius: 999px; background: currentColor;
}
/* 注入：消息气泡里两行字 */
.tm-ico-inject { border: 1.5px solid currentColor; border-radius: 4px; }
.tm-ico-inject::before {
  content: ""; position: absolute; left: 2.5px; right: 2.5px; top: 4px;
  height: 1.5px; background: currentColor;
}
.tm-ico-inject::after {
  content: ""; position: absolute; left: 2.5px; right: 5.5px; top: 8px;
  height: 1.5px; background: currentColor;
}
/* 情绪：半填充圆（愉悦度双向条的冷暖两极意象） */
.tm-ico-mood {
  border: 1.5px solid currentColor; border-radius: 999px;
  background: linear-gradient(90deg, currentColor 50%, transparent 50%);
}
/* 总览：四象限网格（仪表盘意象） */
.tm-ico-overview {
  background:
    linear-gradient(currentColor, currentColor) 0 0 / 100% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 0 100% / 100% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 0 0 / 1.5px 100% no-repeat,
    linear-gradient(currentColor, currentColor) 100% 0 / 1.5px 100% no-repeat,
    linear-gradient(currentColor, currentColor) 50% 35% / 25% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 35% 50% / 1.5px 30% no-repeat;
}
/* 功能管理（1.2.7）：两行开关意象（轨道 + 错位的旋钮） */
.tm-ico-features {
  background:
    linear-gradient(currentColor, currentColor) 0 25% / 100% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 0 75% / 100% 1.5px no-repeat;
}
.tm-ico-features::before {
  content: ""; position: absolute; left: 2px; top: 19%;
  width: 4px; height: 4px; border-radius: 999px; background: currentColor;
}
.tm-ico-features::after {
  content: ""; position: absolute; right: 2px; top: 69%;
  width: 4px; height: 4px; border-radius: 999px; background: currentColor;
}
/* 设置：齿轮意象（外圈 + 中心圆点） */
.tm-ico-settings { border: 1.5px solid currentColor; border-radius: 999px; }
.tm-ico-settings::after {
  content: ""; position: absolute; left: 50%; top: 50%;
  width: 4px; height: 4px; margin: -2px 0 0 -2px;
  border-radius: 999px; background: currentColor;
}
/* 日记：本子轮廓 + 左侧装订线 */
.tm-ico-diary { border: 1.5px solid currentColor; border-radius: 2px 4px 4px 2px; }
.tm-ico-diary::before {
  content: ""; position: absolute; left: 3px; top: 1px; bottom: 1px;
  width: 1.5px; background: currentColor;
}
/* 管理：三条滑轨 + 两个滑块圆点 */
.tm-ico-manage {
  background:
    linear-gradient(currentColor, currentColor) 0 3px / 100% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 0 7.25px / 100% 1.5px no-repeat,
    linear-gradient(currentColor, currentColor) 0 11.5px / 100% 1.5px no-repeat;
}
.tm-ico-manage::before {
  content: ""; position: absolute; left: 2px; top: 1.25px;
  width: 3.5px; height: 3.5px; border-radius: 999px; background: currentColor;
}
.tm-ico-manage::after {
  content: ""; position: absolute; right: 2px; top: 9.5px;
  width: 3.5px; height: 3.5px; border-radius: 999px; background: currentColor;
}

/* ---- Companion statistics ---- */
.tm-hero-card {
  position: relative; padding: 14px 16px;
}

/* ---- 自适应悬停提示（AdaptiveTip）：tip 单例挂在 document.body 的 fixed 元素 ----
   坐标由悬停瞬间的视口量测写入 style；上弹时底边对齐 top、下翻时顶边对 top。
   必须挂 body：.tm-heat-scroll 的 container-type 与 .neko-card 的 backdrop-filter
   都会劫持 fixed 后代的包含块（视口坐标被按容器内坐标解释，实测偏移 ~180px），
   卡片 overflow:hidden 还会裁剪——body 无 transform/filter 祖先，fixed 即真视口系 */
/* 包裹层保留盒模型（量测坐标用）。display:block + line-height:0：inline-block 会被
   行内 strut（基础字号行高 ~19px）撑高——10px 格子行实际 ~20.5px，行距翻倍且左侧
   星期标签列（11.5px 节距）与之渐行错位；压掉 strut 后包裹层高度即内容本高。
   hero 统计卡/徽章卡的包裹层在 flex/grid 上下文里不含行内内容，不受影响 */
.tm-tip-wrap { display: block; line-height: 0; }
.tm-tip {
  position: fixed; z-index: 10000; transform: translate(-50%, -100%);
  max-width: 240px; padding: 5px 9px; border-radius: 8px;
  background: rgba(28, 28, 32, 0.96); color: #f0f0f2;
  font-size: 11px; line-height: 1.45; text-align: center;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.24); text-shadow: none;
  pointer-events: none; white-space: normal;
}
.tm-tip-below { transform: translate(-50%, 0); }
/* hero 数字区：相伴天数大数字 + 纪念日进度环（氛围卡内上半区）。
   与下方统计卡的间距 12→18px：统计卡与 hero 同为玻璃质感，间距小了糊成一片 */
.tm-hero {
  display: flex; align-items: center; justify-content: space-between; gap: 18px;
  flex-wrap: wrap; margin-bottom: 18px;
}
.tm-hero-days { display: flex; flex-direction: column; gap: 6px; cursor: default; }
.tm-hero-days-value {
  font-size: 40px; font-weight: 800; line-height: 1; letter-spacing: 0; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.tm-hero-days-label { font-size: 12.5px; color: var(--muted); font-weight: 600; }
/* 纪念日进度环：conic-gradient 填充 + 玻璃内芯（数字居中）。
   内芯 inset 6px → 实际 52px，数字/单位行盒必须 ≤52（曾写死 line-height:64px，
   52px 容器里塞 64px 行盒 + baseline 对齐 → 整组字下溢、数字中心偏离圆心）；
   line-height:1 让文字盒=字号，align-items:center 把盒中心压到圆心 */
.tm-hero-ring { display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: default; }
.tm-hero-ring-track {
  position: relative; width: 64px; height: 64px; border-radius: 999px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: none;
}
.tm-hero-ring-inner {
  position: absolute; inset: 6px; border-radius: 999px;
  display: flex; align-items: center; justify-content: center; gap: 1px;
  background: rgb(var(--tm-surface-rgb)); box-shadow: inset 0 0 0 1px var(--tm-divider);
}
.tm-hero-ring-num { font-size: 19px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.tm-hero-ring-unit { font-size: 10px; color: var(--muted); line-height: 1; }
.tm-hero-ring-label { font-size: 11px; color: var(--muted); font-weight: 600; }
/* 统计玻璃小卡（氛围卡内底边距由卡片接管） */
.tm-stat-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 16px; }
.tm-hero-card .tm-stat-strip { margin-bottom: 0; }
.tm-stat-strip > :not(:first-child) { border-left: 1px solid var(--tm-divider); }
.tm-stat-cell {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 10px 4px; cursor: default;
}
.tm-stat-value { font-size: 17px; font-weight: 750; line-height: 1.2; font-variant-numeric: tabular-nums; }
.tm-stat-label { font-size: 11px; color: var(--muted); }
@media (max-width: 560px) {
  .tm-stat-strip { grid-template-columns: repeat(2, 1fr); }
  .tm-stat-strip > :nth-child(odd) { border-left: 0; }
}

/* ---- 相处徽章墙（成就卡片式 2.0）：横向 4 列卡片（窄面板降列），月光蓝主调 ----
   天数类按难度升级月相（新月→半月→满月→星拱月→潮汐月），
   事件类专属图形（羽毛笔/书页/信封）；未解锁整卡灰化剪影 + 进度条 */
/* 徽章墙横向排列：4 列 × 2 行，窄面板逐级降列 */
.tm-badge-wall { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.tm-ach {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 12px; border-radius: 8px;
  background: transparent; border: 1px solid var(--tm-divider);
  cursor: default;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.tm-ach:hover { background: var(--tm-hover); border-color: var(--tm-border); }
.tm-ach-on {
  border-color: var(--primary);
}
.tm-ach-on:hover { background: var(--tm-accent-soft); border-color: var(--primary); }
/* The dark icon base provides contrast for the silver moon glyphs. */
.tm-ach-icon {
  position: relative; flex: 0 0 46px; width: 46px; height: 46px; border-radius: 999px;
  display: flex; align-items: center; justify-content: center;
  background: #34343c; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}
.tm-ach-on .tm-ach-icon::before {
  content: ""; position: absolute; inset: 0; border-radius: 999px; pointer-events: none;
  background-image:
    radial-gradient(circle 1px, rgba(226, 240, 255, 0.95) 50%, transparent 52%),
    radial-gradient(circle 0.8px, rgba(226, 240, 255, 0.8) 50%, transparent 52%),
    radial-gradient(circle 0.7px, rgba(226, 240, 255, 0.7) 50%, transparent 52%);
  background-position: 22% 24%, 76% 20%, 70% 78%;
  background-repeat: no-repeat;
}
.tm-ach-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.tm-ach-name { font-size: 12px; font-weight: 700; line-height: 1.25; }
.tm-ach-sub { font-size: 10.5px; color: var(--muted); line-height: 1.25; }
.tm-ach-on .tm-ach-sub { color: var(--muted); }
/* 相伴进度条：未解锁时显示当前相伴天数占目标的进度 */
.tm-ach-bar {
  display: block; height: 3px; margin-top: 2px; border-radius: 999px;
  background: rgba(148, 163, 184, 0.28); overflow: hidden;
}
.tm-ach-bar-fill {
  display: block; height: 100%; border-radius: 999px;
  background: var(--secondary);
}
/* 未解锁：整卡灰化剪影。图标底座换成中性灰夜空（不白不黑），
   不再用深海军蓝灰化后的偏黑底 */
.tm-ach:not(.tm-ach-on) .tm-ach-icon {
  filter: grayscale(1);
  background: var(--tm-locked-bg); box-shadow: inset 0 0 0 1px var(--tm-border);
}
.tm-ach:not(.tm-ach-on) .tm-ach-bar-fill { background: rgba(148, 163, 184, 0.6); }

/* ---- 徽章图形：月相五档 + 羽毛笔/书页/信封（配色走月光银蓝）---- */
.tm-glyph { position: relative; display: block; width: 26px; height: 26px; }
.tm-ach-on .tm-glyph { color: #eaf3ff; }
.tm-ach:not(.tm-ach-on) .tm-glyph { color: var(--tm-locked-glyph); }
/* 新月：银蓝圆盘 + mask 切出一弯（镂空处透出底座夜空色，不产生色差） */
.tm-glyph-crescent {
  border-radius: 999px;
  background: radial-gradient(circle at 34% 30%, #ffffff 0%, #dcebff 45%, #9cc2ef 100%);
  box-shadow: 0 0 6px rgba(190, 220, 255, 0.55);
  -webkit-mask: radial-gradient(circle at 74% 24%, transparent 52%, #000 54%);
  mask: radial-gradient(circle at 74% 24%, transparent 52%, #000 54%);
}
.tm-ach:not(.tm-ach-on) .tm-glyph-crescent { box-shadow: none; }
/* 半月：右半亮面 */
.tm-glyph-half {
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(148, 178, 214, 0.3) 0 50%, #dcebff 50% 62%, #9cc2ef 100%);
  box-shadow: 0 0 6px rgba(190, 220, 255, 0.45);
}
.tm-ach:not(.tm-ach-on) .tm-glyph-half { box-shadow: none; }
/* 满月：圆满亮盘 + 光晕 */
.tm-glyph-full {
  border-radius: 999px;
  background: radial-gradient(circle at 36% 30%, #ffffff 0%, #e8f2ff 42%, #b6d2f2 75%, #8fb6e4 100%);
  box-shadow: 0 0 8px rgba(200, 228, 255, 0.65);
}
.tm-ach:not(.tm-ach-on) .tm-glyph-full { box-shadow: none; }
/* 星拱月：满月居中 + 环拱星光点 */
.tm-glyph-orbit { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; }
.tm-glyph-orbit .tm-glyph-full { width: 17px; height: 17px; }
.tm-glyph-orbit::before, .tm-glyph-orbit::after {
  content: ""; position: absolute; border-radius: 999px;
  background: #dcebff; box-shadow: 0 0 4px rgba(200, 228, 255, 0.8);
}
.tm-glyph-orbit::before { left: 1px; top: 7px; width: 3px; height: 3px; }
.tm-glyph-orbit::after { right: 2px; top: 17px; width: 2.5px; height: 2.5px; }
/* 潮汐月：新月上浮 + 两道涌动潮水线 */
.tm-glyph-tide { width: 30px; height: 30px; }
.tm-glyph-tide .tm-glyph-crescent {
  position: absolute; left: 50%; top: 1px; width: 15px; height: 15px; margin-left: -7.5px;
}
.tm-glyph-wave {
  position: absolute; left: 3px; width: 24px; height: 3px; border-radius: 2px;
  background: linear-gradient(90deg, transparent, #9cc2ef 18%, #e8f2ff 50%, #9cc2ef 82%, transparent);
}
.tm-glyph-w1 { bottom: 7px; }
.tm-glyph-w2 { bottom: 2px; left: 6px; width: 18px; opacity: 0.65; }
/* 羽毛笔：斜笔杆（圆角长条旋转）+ 笔尖三角 */
.tm-glyph-quill-body {
  position: absolute; left: 5px; top: 2px; width: 5.5px; height: 16px;
  border-radius: 3px 3px 1px 1px;
  background: currentColor; opacity: 0.9;
  transform: rotate(32deg);
}
.tm-glyph-quill-nib {
  position: absolute; left: 10.5px; top: 14.5px; width: 0; height: 0;
  border-left: 2.6px solid transparent; border-right: 2.6px solid transparent;
  border-top: 5px solid currentColor;
  transform: rotate(32deg);
}
/* 书页：摊开双页（两个带折角的梯形）+ 两行字线 */
.tm-glyph-book-page {
  position: absolute; top: 5px; width: 10px; height: 14px;
  background: currentColor; opacity: 0.85;
}
.tm-glyph-book-left { left: 2px; clip-path: polygon(0 12%, 100% 0, 100% 100%, 0 100%); }
.tm-glyph-book-right { right: 2px; clip-path: polygon(0 0, 100% 12%, 100% 100%, 0 100%); }
.tm-glyph-book-line { position: absolute; height: 1.6px; border-radius: 1px; background: rgba(23, 41, 74, 0.85); }
.tm-glyph-book-l1 { left: 4.5px; top: 10px; width: 6.5px; }
.tm-glyph-book-l2 { left: 13px; top: 10px; width: 6.5px; }
/* 信封：矩形底 + 盖片三角 + 封口圆点 */
.tm-glyph-letter { border: 1.8px solid currentColor; border-radius: 3px; opacity: 1; }
.tm-glyph-letter-flap {
  position: absolute; left: 0; top: 0; width: 0; height: 0;
  border-left: 10.5px solid transparent; border-right: 10.5px solid transparent;
  border-top: 7px solid currentColor; opacity: 0.85;
}
.tm-glyph-letter-seal {
  position: absolute; left: 50%; top: 12px; width: 4.5px; height: 4.5px; margin-left: -2.25px;
  border-radius: 999px; background: currentColor;
}
@media (max-width: 720px) {
  .tm-badge-wall { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 440px) {
  .tm-badge-wall { grid-template-columns: 1fr; }
}

/* Seven weekday rows with fixed gaps; long histories scroll only inside the chart. */
.tm-heat {
  --tm-heat-lv1: #d9eaf3; --tm-heat-lv2: #b4d5e7;
  --tm-heat-lv3: #81b5d2; --tm-heat-lv4: #4f8fb4;
}
.tm-heat-scroll {
  overflow-x: auto; padding: 2px 0 8px; container-type: inline-size;
  scrollbar-width: thin; scrollbar-color: var(--tm-border) transparent;
}
.tm-heat-scroll:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: 2px; border-radius: 2px; }
/* Reserve 32px for weekdays and use the actual week count, including 53-week years. */
.tm-gh {
  --tm-gh-gap: 3px;
  --tm-gh-cell: clamp(11px, calc((100cqw - 32px - (var(--tm-gh-weeks) - 1) * 3px) / var(--tm-gh-weeks)), 15px);
  display: flex; width: max-content; flex-direction: column; gap: 8px;
}
.tm-heat-card .neko-card-header { padding-top: 14px; padding-bottom: 0; }
.tm-heat-card .neko-card-body { padding-top: 12px; padding-bottom: 14px; }
/* Month groups occupy the same columns as their dates. */
.tm-gh-monthrow { display: flex; gap: var(--tm-gh-gap); }
.tm-gh-corner { width: 29px; flex: 0 0 29px; }
.tm-gh-monthslot {
  width: calc(var(--tm-gh-month-weeks) * var(--tm-gh-cell) + (var(--tm-gh-month-weeks) - 1) * var(--tm-gh-gap));
  flex: none; height: 16px; position: relative;
}
/* Keep the final label inside the date grid even for a one-week partial month. */
.tm-gh-monthslot:last-child .tm-gh-month { left: auto; right: 0; }
.tm-gh-month {
  position: absolute; left: 0; top: 0; white-space: nowrap;
  font-size: 11px; color: var(--muted); font-weight: 500; line-height: 16px;
}
/* 主体：左侧星期标签列 + 周列区 */
.tm-gh-body { display: flex; gap: 8px; }
.tm-gh-wdcol { display: flex; flex-direction: column; gap: var(--tm-gh-gap); width: 24px; flex: 0 0 24px; }
.tm-gh-wd {
  height: var(--tm-gh-cell); font-size: 11px; color: var(--muted); line-height: 1;
  display: flex; align-items: center; user-select: none;
}
.tm-gh-wd-empty { visibility: hidden; }
.tm-gh-cols { display: flex; gap: var(--tm-gh-gap); }
.tm-gh-col { display: flex; flex-direction: column; gap: var(--tm-gh-gap); }
/* 未来/窗口外占位：透明格保持列高对齐 */
.tm-heat-future { display: inline-block; width: var(--tm-gh-cell); height: var(--tm-gh-cell); }
/* 格子本体 */
.tm-heat-cell {
  position: relative; display: inline-block;
  width: var(--tm-gh-cell); height: var(--tm-gh-cell); border-radius: 2px;
  box-shadow: inset 0 0 0 1px rgba(27, 31, 35, 0.08); cursor: default;
}
.tm-gh .tm-heat-cell:hover { outline: 2px solid var(--text); outline-offset: 1px; }
.tm-heat-lv0 { background: var(--tm-heat-empty); }
.tm-heat-lv1 { background: var(--tm-heat-lv1); }
.tm-heat-lv2 { background: var(--tm-heat-lv2); }
.tm-heat-lv3 { background: var(--tm-heat-lv3); }
.tm-heat-lv4 { background: var(--tm-heat-lv4); }
@media (prefers-color-scheme: dark) {
  .tm-heat {
    --tm-heat-lv1: #263e4d; --tm-heat-lv2: #426781;
    --tm-heat-lv3: #679bbd; --tm-heat-lv4: #9ccae3;
  }
}
.tm-has-bg .tm-heat {
  --tm-heat-lv1: #263e4d; --tm-heat-lv2: #426781;
  --tm-heat-lv3: #679bbd; --tm-heat-lv4: #9ccae3;
}
/* 底部脚注：说明文案 + 图例（GitHub 同款右下角布局） */
.tm-heat-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.tm-heat-note { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tm-heat-legend { display: flex; align-items: center; gap: 4px; justify-content: flex-end; margin-left: auto; }
/* The legend keeps its own size outside the chart's sizing container. */
.tm-heat-legend .tm-heat-cell { width: 11px; height: 11px; flex: 0 0 11px; }
.tm-heat-legend-label { font-size: 10.5px; color: var(--muted); margin: 0 2px; }

/* ---- 月报 ---- */
.tm-month-nav { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.tm-month-btn {
  border: 1px solid var(--tm-border); background: transparent; color: inherit;
  border-radius: 6px; width: 28px; height: 26px; cursor: pointer; font-size: 14px; line-height: 1;
  transition: transform 0.12s ease, background 0.12s ease;
}
.tm-month-btn:hover:not(:disabled) { background: var(--tm-hover); }
.tm-month-btn:disabled { opacity: 0.35; cursor: default; }
.tm-month-label { display: flex; align-items: center; gap: 6px; font-weight: 700; font-size: 13.5px; }
.tm-month-loading { color: var(--muted); }
.tm-month-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.tm-month-cell {
  display: flex; flex-direction: column; gap: 2px; padding: 10px 4px; min-width: 0;
  align-items: center; text-align: center; overflow-wrap: anywhere;
}
.tm-month-cell:not(:nth-child(3n + 1)) { border-left: 1px solid var(--tm-divider); }
.tm-month-cell-value { font-size: 14.5px; font-weight: 750; font-variant-numeric: tabular-nums; }
.tm-month-cell-label { font-size: 11px; color: var(--muted); }
.tm-month-tone { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.tm-month-tone-label { font-size: 11.5px; color: var(--muted); }
.tm-month-tone-item {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
  padding: 3px 9px; border-radius: 999px;
  background: transparent; border: 1px solid var(--tm-divider);
}
.tm-month-tone-count { font-size: 10.5px; color: var(--muted); }
.tm-month-tone-dot { display: inline-block; width: 8px; height: 8px; border-radius: 999px; }
/* 本月声音：玻璃引言卡（渐变顶边 + 大引号 + 衬线引文） */
.tm-month-voice { margin-top: 12px; }
.tm-month-voice-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; color: var(--muted); margin-bottom: 6px;
}
.tm-month-voice-text {
  position: relative; font-size: 13.5px; line-height: 1.8; padding: 14px 16px 12px 38px;
  border-left: 3px solid rgb(245, 176, 77); overflow-wrap: anywhere;
}
.tm-month-voice-text::before {
  content: "“"; position: absolute; left: 10px; top: 6px;
  font-size: 26px; line-height: 1; font-family: Georgia, "Times New Roman", serif;
  color: rgba(245, 176, 77, 0.75); pointer-events: none;
}
.tm-month-voice-empty { margin-top: 10px; font-size: 12px; color: var(--muted); }

/* 时光页签图标：时钟（圆 + 指针） */
.tm-ico-moment { border: 1.5px solid currentColor; border-radius: 999px; }
.tm-ico-moment::before {
  content: ""; position: absolute; left: 50%; top: 50%;
  width: 1.5px; height: 4px; margin: -4px 0 0 -0.75px;
  background: currentColor; transform-origin: 50% 100%;
}
.tm-ico-moment::after {
  content: ""; position: absolute; left: 50%; top: 50%;
  width: 3px; height: 1.5px; margin: -0.75px 0 0 -0.75px;
  background: currentColor;
}

/* 靶心：日历选日模式按钮 */
.tm-ico-target { border: 1.5px solid currentColor; border-radius: 999px; }
.tm-ico-target::after {
  content: ""; position: absolute; left: 50%; top: 50%;
  width: 4px; height: 4px; margin: -2px 0 0 -2px;
  border-radius: 999px; background: currentColor;
}

/* ---- 新手引导向导（1.2.6）---- */
.tm-ob-step { display: grid; gap: 10px; padding: 4px 0 8px; }
.tm-ob-lead { margin: 0; color: var(--text); font-size: 13.5px; line-height: 1.7; }
.tm-ob-note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.tm-ob-feats { display: grid; gap: 8px; }
.tm-ob-feat {
  display: grid; gap: 2px; padding: 9px 12px; border-radius: var(--radius-md);
  border-bottom: 1px solid var(--tm-divider);
}
.tm-ob-feat-name { font-size: 13px; font-weight: 700; color: var(--text); }
.tm-ob-feat-sub { font-size: 12px; line-height: 1.55; color: var(--muted); }
.tm-ob-state {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px; border-radius: var(--radius-md);
  border: 1px solid var(--tm-divider); background: transparent;
  font-size: 13px; color: var(--text);
}
.tm-ob-state-ok { color: var(--tm-success-text); font-weight: 650; }
.tm-ob-check { font-weight: 800; }
.tm-ob-chans { display: grid; gap: 6px; }
.tm-ob-chan {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; border-radius: var(--radius-md);
  border-bottom: 1px solid var(--tm-divider);
  font-size: 12.5px; color: var(--text);
}
.tm-ob-chan-spacer { flex: 1; }
.tm-ob-tips { display: grid; gap: 6px; }
.tm-ob-tip { font-size: 12.5px; line-height: 1.6; color: var(--text); padding-left: 14px; position: relative; }
.tm-ob-tip::before {
  content: ""; position: absolute; left: 2px; top: 7px;
  width: 5px; height: 5px; border-radius: 999px; background: var(--primary);
}
.tm-ob-foot { display: flex; align-items: center; gap: 8px; width: 100%; }
.tm-ob-foot-right { display: flex; gap: 8px; margin-left: auto; }
.tm-ob-dots { display: flex; gap: 6px; margin: 0 auto; }
.tm-ob-dot {
  width: 7px; height: 7px; border-radius: 999px;
  background: rgba(148, 163, 184, 0.4);
  transition: background 160ms ease, transform 160ms ease;
}
.tm-ob-dot-on { background: var(--primary); transform: scale(1.25); }

/* ---- 就绪清单（1.2.6）---- */
.tm-guide-progress { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.tm-guide-bar {
  flex: 1; height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(148, 163, 184, 0.22);
}
.tm-guide-bar-fill {
  display: block; height: 100%; border-radius: 999px;
  background: var(--primary);
  transition: width 320ms cubic-bezier(0.22, 0.61, 0.36, 1);
}
.tm-guide-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tm-guide-list { display: grid; gap: 6px; }
.tm-guide-row {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 10px; border-radius: var(--radius-md);
  border: 1px solid var(--tm-divider); background: transparent;
}
.tm-guide-row-ok { opacity: 0.62; }
.tm-guide-dot {
  flex: 0 0 9px; width: 9px; height: 9px; border-radius: 999px;
  background: rgba(148, 163, 184, 0.45);
}
.tm-guide-dot-ok { background: #34b27b; }
.tm-guide-text { font-size: 12.5px; color: var(--text); line-height: 1.5; }
.tm-guide-must {
  font-size: 10.5px; font-weight: 700; color: var(--tm-warning-text);
  background: rgba(245, 158, 11, 0.14); border-radius: 999px; padding: 1px 7px;
}
.tm-guide-spacer { flex: 1; }
.tm-guide-foot { margin-top: 8px; font-size: 11.5px; line-height: 1.55; color: var(--muted); }
.tm-reopen-guide { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
.tm-reopen-hint { font-size: 12px; color: var(--muted); }
/* 功能管理页（1.2.7）：能力行列表 + LLM 徽标侧栏 + 高级选项说明 */
.tm-features-pane { min-width: 0; grid-template-columns: minmax(0, 1fr); }
.tm-feat-list { display: flex; flex-direction: column; gap: 6px; }
.tm-feat-row .tm-field { margin-bottom: 0; }
.tm-feat-side { display: inline-flex; align-items: center; gap: 10px; }
.tm-feat-note { font-size: 12px; line-height: 1.6; color: var(--muted); margin-top: 8px; }
/* 1.2.7 单页化：三组卡横向并排（4/2/3 行，总高≈最高列）+ 高级选项单行窄条；
   窄窗回落双栏/单栏 */
.tm-feat-cols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
.tm-feat-adv {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 8px 14px; border-radius: var(--radius-md);
  border: 1px solid var(--tm-border);
  background: rgba(var(--tm-surface-rgb), calc(var(--tm-surface-alpha) * var(--tm-card-k, 1)));
  -webkit-backdrop-filter: var(--tm-glass-filter, none); backdrop-filter: var(--tm-glass-filter, none);
}
.tm-feat-adv-label { font-size: 12px; font-weight: 720; color: var(--muted); flex: none; }
.tm-feat-adv-switch { flex: 0 1 auto; min-width: 0; max-width: 100%; cursor: help; }
.tm-feat-adv-switch .tm-sw { max-width: 100%; }
.tm-feat-adv-help {
  flex: 1; min-width: 120px; text-align: right; font-size: 11px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
@media (max-width: 900px) { .tm-feat-cols { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 620px) { .tm-feat-cols { grid-template-columns: minmax(0, 1fr); } }
/* Function introductions keep plain reading sections separate from their framed demo. */
.tm-feat-label { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tm-ci-open {
  font-size: 11px; line-height: 1.4; padding: 1px 9px; border-radius: 999px;
  border: 1px dashed var(--tm-border); background: transparent;
  color: var(--primary); cursor: pointer; transition: background 0.15s ease, border-color 0.15s ease;
}
.tm-ci-open:hover { background: var(--tm-accent-soft); border-color: var(--primary); }
.tm-ci-page {
  display: flex; flex-direction: column; gap: 16px; min-width: 0;
  container: tm-ci / inline-size; font-size: 13px; line-height: 1.65; letter-spacing: 0;
}
.tm-ci-back {
  display: inline-flex; align-items: center; gap: 7px; min-width: 0; max-width: 100%;
  font-size: 12px; line-height: 1.5; padding: 5px 7px; border-radius: 5px;
  border: 1px solid transparent; background: transparent; color: var(--muted);
  cursor: pointer; overflow-wrap: anywhere; text-align: left;
  transition: background 0.15s ease, color 0.15s ease;
}
.tm-ci-back:hover { background: var(--tm-accent-soft); color: var(--primary); }
.tm-ci-back-icon {
  flex: 0 0 12px; width: 12px; height: 12px; position: relative;
  background: linear-gradient(currentColor, currentColor) center / 11px 1.5px no-repeat;
}
.tm-ci-back-icon::before {
  content: ""; position: absolute; width: 6px; height: 6px; left: 1px; top: 3px;
  border-left: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg);
}
.tm-ci-title {
  margin: 0; min-width: 0; font-size: 16px; font-weight: 720; line-height: 1.5;
  color: var(--text); overflow-wrap: anywhere;
}
.tm-ci-head-spacer { flex: 1; }
.tm-ci { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.tm-ci-head {
  display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap;
  padding-bottom: 12px; border-bottom: 1px solid var(--tm-divider);
}
.tm-ci-head .neko-badge { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
.tm-ci-sec { min-width: 0; }
.tm-ci-h {
  margin: 0 0 9px; font-size: 14px; font-weight: 700; line-height: 1.5;
  letter-spacing: 0; color: var(--text); text-transform: none; overflow-wrap: anywhere;
}
.tm-ci-summary { padding-left: 12px; border-left: 3px solid var(--primary); }
.tm-ci-summary .tm-ci-h { color: var(--primary); margin-bottom: 6px; }
.tm-ci-purpose { margin: 0; font-size: 14px; line-height: 1.75; color: var(--text); overflow-wrap: anywhere; }
.tm-ci-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));
  gap: 18px 28px; align-items: start; min-width: 0;
}
.tm-ci-grid > .tm-ci-sec { padding-top: 14px; border-top: 1px solid var(--tm-divider); }
.tm-ci-limits .tm-ci-h { color: var(--tm-warning-text); }
.tm-ci-list { margin: 0; padding-left: 18px; }
.tm-ci-list li {
  padding-left: 2px; font-size: 13px; line-height: 1.7; color: var(--text);
  margin: 0 0 6px; overflow-wrap: anywhere;
}
.tm-ci-list li:last-child { margin-bottom: 0; }
.tm-ci-list li::marker { color: var(--secondary); }
.tm-ci-list-warn li::marker { color: var(--tm-warning-text); }
.tm-ci-requirements { padding-top: 14px; border-top: 1px solid var(--tm-divider); }
.tm-ci-prerequisite { margin: 0 0 10px; font-size: 13px; color: var(--text); overflow-wrap: anywhere; }
.tm-ci-facts { display: flex; flex-direction: column; gap: 10px; margin: 0; min-width: 0; }
.tm-ci-fact { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 8px 18px; min-width: 0; }
.tm-ci-fact dt {
  margin: 0; font-size: 13px; font-weight: 600; line-height: 1.65;
  color: var(--muted); overflow-wrap: anywhere;
}
.tm-ci-fact dd {
  margin: 0; min-width: 0; font-size: 13px; line-height: 1.65;
  color: var(--text); overflow-wrap: anywhere;
}
.tm-ci-actions { padding-top: 16px; border-top: 1px solid var(--tm-divider); }
.tm-ci-actions .tm-ci-fact { padding: 10px 0; border-bottom: 1px solid var(--tm-divider); }
.tm-ci-actions .tm-ci-fact:first-child { padding-top: 0; }
.tm-ci-actions .tm-ci-fact:last-child { padding-bottom: 0; border-bottom: 0; }
.tm-ci-actions dt { font-weight: 600; color: var(--text); }
.tm-ci-deps { display: flex; flex-wrap: wrap; gap: 4px 16px; list-style: none; padding: 0; margin: 0; }
.tm-ci-deps li { min-width: 0; color: var(--tm-info-text); overflow-wrap: anywhere; }
.tm-ci-model { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 10px; }
.tm-ci-model > .neko-badge { flex: 0 1 auto; max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
.tm-ci-model p { flex: 1 1 240px; margin: 0; min-width: 0; overflow-wrap: anywhere; }
.tm-ci-demo-section { padding-top: 16px; border-top: 1px solid var(--tm-divider); }
.tm-ci-demo-section .tm-demo-heading { font-size: 14px; font-weight: 700; line-height: 1.5; }
.tm-ci-technical { min-width: 0; padding-top: 12px; border-top: 1px solid var(--tm-divider); }
.tm-ci-tech-summary {
  display: list-item; width: fit-content; max-width: 100%; padding: 4px 2px;
  list-style-position: inside; cursor: pointer; font-size: 13px; font-weight: 650;
  line-height: 1.6; color: var(--muted); overflow-wrap: anywhere; border-radius: 4px;
}
.tm-ci-tech-summary::marker { color: var(--primary); }
.tm-ci-tech-summary:hover { color: var(--primary); background: var(--tm-accent-soft); }
.tm-ci-tech-summary:focus-visible { outline: 2px solid var(--tm-focus); outline-offset: 2px; }
.tm-ci-technical[open] > .tm-ci-tech-summary { margin-bottom: 12px; color: var(--text); }
.tm-ci-code-list { display: flex; flex-wrap: wrap; gap: 5px 16px; list-style: none; padding: 0; margin: 0; }
.tm-ci-code-list li { min-width: 0; max-width: 100%; }
.tm-ci-code-list code { font-size: 13px; color: var(--tm-config-text); white-space: normal; overflow-wrap: anywhere; }
.tm-ci-tools-count { margin: 0 0 7px; font-size: 13px; color: var(--muted); }
.tm-ci-mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
@container tm-ci (max-width: 520px) {
  .tm-ci-fact { grid-template-columns: minmax(0, 1fr); gap: 3px; }
  .tm-ci-model { align-items: flex-start; }
  .tm-ci-model p { flex-basis: 100%; }
}
.tm-ci-flow {
  display: flex; flex-wrap: wrap; align-items: center; row-gap: 6px; overflow-wrap: anywhere;
  padding: 8px 10px; border-left: 2px solid var(--tm-divider);
}
.tm-ci-flow-step { display: inline-flex; align-items: center; max-width: 100%; }
.tm-ci-flow-arrow { margin: 0 6px; color: rgba(100, 116, 139, 0.8); font-weight: 700; font-size: 12px; }
.tm-ci-node {
  display: inline-flex; align-items: center; max-width: 100%;
  font-size: 11.5px; line-height: 1.35; padding: 4px 10px; border-radius: 999px;
  overflow-wrap: anywhere;
  border: 1px solid var(--tm-border); background: transparent; color: var(--text);
}
.tm-ci-node[data-kind="src"] { border-color: rgba(59, 130, 246, 0.55); background: rgba(59, 130, 246, 0.08); }
.tm-ci-node[data-kind="proc"] { border-color: rgba(139, 92, 246, 0.5); background: rgba(139, 92, 246, 0.07); }
.tm-ci-node[data-kind="gate"] { border-style: dashed; border-color: rgba(245, 158, 11, 0.65); background: rgba(245, 158, 11, 0.07); }
.tm-ci-node[data-kind="out"] { border-color: rgba(16, 185, 129, 0.55); background: rgba(16, 185, 129, 0.08); }
.tm-ci-flow-empty, .tm-ci-empty, .tm-ci-loading {
  padding: 18px 0; text-align: center; font-size: 12px; color: var(--muted);
}
.tm-ci-error { padding: 10px 12px; font-size: 12px; color: var(--tm-warning-text); background: rgba(245, 158, 11, 0.09); border-radius: var(--radius-sm); }
/* ---- 动画开关 TmSwitch（1.3.1）：统一替换宿主 Kit 原生 checkbox 开关 ----
   经典顺滑型：滑块 0.25s 缓动平移、轨道同步渐变换色；开启态浅蓝与面板
   淡蓝磨砂底同族（#93c5fd/#60a5fa 系）。small 档供状态条/引导行等窄位。 */
.tm-sw { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; vertical-align: middle; min-width: 0; }
.tm-sw--dis { cursor: not-allowed; opacity: 0.5; }
/* 隐藏原生 checkbox 但保留键盘可达（focus-visible 环画在轨道上） */
.tm-sw-input {
  position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  border: 0; clip: rect(0 0 0 0); overflow: hidden; white-space: nowrap;
}
.tm-sw-track {
  position: relative; flex: none; width: 40px; height: 22px; border-radius: 999px;
  background: var(--tm-track); box-shadow: inset 0 0 0 1px var(--tm-border);
  transition: background 0.25s ease, box-shadow 0.25s ease;
}
.tm-sw--on .tm-sw-track {
  background: var(--primary); box-shadow: none;
}
.tm-sw-knob {
  position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%;
  background: var(--tm-knob); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.26);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.tm-sw--on .tm-sw-knob { transform: translateX(18px); }
.tm-sw--sm .tm-sw-track { width: 34px; height: 19px; }
.tm-sw--sm .tm-sw-knob { width: 15px; height: 15px; }
.tm-sw--sm.tm-sw--on .tm-sw-knob { transform: translateX(15px); }
.tm-sw-input:focus-visible + .tm-sw-track { outline: 2px solid var(--tm-focus); outline-offset: 2px; }
.tm-sw:not(.tm-sw--dis):hover .tm-sw-knob { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.26), 0 0 0 2px var(--tm-accent-soft); }
.tm-sw-text { font-size: 12.5px; line-height: 1.5; color: var(--text); min-width: 0; overflow-wrap: anywhere; }

/* Plain mode separates soft control fills from readable text; wallpaper roles stay unchanged. */
.neko-page:not(.tm-has-bg) {
  --text: #45515a; --muted: #64727c;
  --tm-border: rgba(96, 133, 152, 0.20); --tm-divider: rgba(96, 133, 152, 0.14);
  --tm-hover: rgba(115, 162, 187, 0.08);
  --tm-shadow: 0 2px 8px rgba(55, 85, 101, 0.04);
  --tm-track: rgba(120, 154, 172, 0.26);
  --tm-locked-bg: #e6edf0; --tm-locked-glyph: #899ca6;
  --tm-heat-empty: rgba(120, 154, 172, 0.13);
  background: #f6f9fa;
}
.neko-page:not(.tm-has-bg) .tm-appearance-root {
  --primary: #456f89; --secondary: #4f6e81;
  --tm-control-primary: #81b5d2; --tm-control-secondary: #99b8c6;
  --tm-accent-soft: #e8f2f7; --tm-accent-hover: #e0edf4; --tm-accent-border: #b2cfdf;
  --tm-secondary-soft: #edf3f5; --tm-focus: #5f879e;
}
.neko-page:not(.tm-has-bg) .neko-button[data-tone="primary"] {
  border-color: var(--tm-accent-border);
}
.neko-page:not(.tm-has-bg) .neko-button[data-tone="primary"]:hover:not(:disabled),
.neko-page:not(.tm-has-bg) .tm-tab-active:hover {
  color: var(--primary); background: var(--tm-accent-hover); border-color: var(--tm-accent-border);
}
.neko-page:not(.tm-has-bg) .tm-tab-active {
  border-color: var(--tm-accent-border); box-shadow: inset 3px 0 0 var(--tm-focus);
}
.neko-page:not(.tm-has-bg) :is(.tm-ring-dot-active, .tm-guide-bar-fill, .tm-sw--on .tm-sw-track) {
  background: var(--tm-control-primary);
}
.neko-page:not(.tm-has-bg) .tm-sw--on .tm-sw-track {
  box-shadow: inset 0 0 0 1px var(--tm-focus);
}
.neko-page:not(.tm-has-bg) .neko-slider-input { accent-color: var(--tm-control-primary); }
.neko-page:not(.tm-has-bg) .tm-review-bar-fill { background: var(--tm-control-secondary); }

@media (prefers-color-scheme: dark) {
  .neko-page:not(.tm-has-bg) {
    --text: #e2eaed; --muted: #b3c0c6;
    --tm-surface-rgb: 48, 54, 57;
    --tm-border: rgba(181, 207, 219, 0.20); --tm-divider: rgba(181, 207, 219, 0.14);
    --tm-hover: rgba(164, 196, 210, 0.09);
    --tm-shadow: 0 2px 10px rgba(0, 0, 0, 0.10);
    --tm-track: rgba(177, 198, 210, 0.25);
    --tm-locked-bg: #414d52; --tm-locked-glyph: #aabbc3;
    background: #272d30;
  }
  .neko-page:not(.tm-has-bg) .tm-appearance-root {
    --primary: #b0d1e1; --secondary: #aac5d1;
    --tm-control-primary: #83b0c8; --tm-control-secondary: #9cb9c6;
    --tm-accent-soft: #364953; --tm-accent-hover: #40545f; --tm-accent-border: #668594;
    --tm-secondary-soft: #35464e; --tm-focus: #97bfd2;
  }
}

/* Wallpaper materials are independent of the operating system theme. */
.neko-page.tm-has-bg {
  --text: #f4f4f6; --muted: #d0d0d8; --primary: #8bc5ff; --secondary: #9dbfdf;
  --danger: #ff919a; --warning: #f4c374; --success: #b2df96; --info: #9dbfdf;
  --tm-surface-rgb: 28, 28, 32; --tm-surface-alpha: 0.58; --tm-chrome-alpha: 0.40; --tm-input-alpha: 0.78;
  --tm-border: rgba(244, 244, 250, 0.22); --tm-divider: rgba(244, 244, 250, 0.16);
  --tm-hover: rgba(244, 244, 250, 0.09); --tm-accent-soft: rgba(139, 197, 255, 0.16);
  --tm-focus: #8bc5ff; --tm-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
  --tm-warning-text: #f4c374; --tm-success-text: #b2df96; --tm-info-text: #a8d9ec; --tm-config-text: #d7bef0;
  --tm-moon-shadow: radial-gradient(circle at 36% 32%, #38383e, #29292f 80%);
  --tm-knob: #fff; --tm-track: rgba(225, 225, 235, 0.32);
  --tm-locked-bg: #53535e; --tm-locked-glyph: #d0d0d8; --tm-heat-empty: rgba(230, 230, 240, 0.18);
  color-scheme: dark; background: #18181c;
}
.tm-has-bg .tm-appearance-root {
  --primary: var(--tm-theme-primary-dark, #8bc5ff);
  --secondary: var(--tm-theme-secondary-dark, #9dbfdf);
  --tm-accent-soft: color-mix(in srgb, var(--primary) 8%, #18181c);
  --tm-secondary-soft: color-mix(in srgb, var(--secondary) 8%, #18181c);
}
.tm-has-bg :is(.neko-button[data-tone="primary"], .tm-tab-active, .tm-ci-open) {
  text-shadow: none;
}
.tm-has-bg .tm-ci-open { background: var(--tm-accent-soft); }
.tm-has-bg .tm-tab-active:hover { color: var(--primary); background: var(--tm-accent-soft); }
.tm-has-bg :is(button, a, input, select, textarea, [tabindex]):focus-visible,
.tm-has-bg .neko-button[data-tone="primary"]:focus-visible {
  box-shadow: 0 0 0 4px rgba(24, 24, 28, 0.9);
}
.tm-has-bg .tm-ring-marker { border-color: #28282c; }
.tm-has-bg .tm-ring-phase-dot { border-color: #28282c; }
.tm-has-bg .tm-mood-dot { box-shadow: 0 0 0 2px rgba(28, 28, 32, 0.72); }
.tm-has-bg :is(.neko-modal, .neko-toast) { text-shadow: none; }

@media (max-width: 520px) {
  .tm-statusbar, .tm-statusbar-slim { padding-left: 12px; padding-right: 12px; }
  .tm-status-main { flex: 1 1 140px; min-width: 0; }
  .tm-statusbar-slim .tm-status-name { max-width: 42%; overflow: hidden; text-overflow: ellipsis; }
  .tm-status-summary { min-width: 0; flex: 1; }
  .tm-tabs { min-width: 64px; padding-left: 4px; padding-right: 4px; }
  .tm-tab { padding: 9px 5px; font-size: 12px; }
  .tm-content { padding: 12px 10px; }
  .tm-save { margin: 0 -10px -12px; padding: 10px; }
  .tm-ov-diary-grid { grid-template-columns: minmax(0, 1fr); gap: 6px; }
  .tm-ov-tile { padding: 10px 12px; }
  .tm-hero-card { padding-left: 0; padding-right: 0; }
  .tm-journal-toolbar, .tm-diary-toolbar { flex-wrap: wrap; }
  .tm-appearance-save { flex-wrap: wrap; }
  .tm-save-hint { flex: 1 0 100%; }
  .tm-month-label { min-width: 0; flex-wrap: wrap; }
  .tm-month-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tm-month-cell:not(:nth-child(3n + 1)) { border-left: 0; }
  .tm-month-cell:nth-child(even) { border-left: 1px solid var(--tm-divider); }
}
@media (prefers-reduced-motion: reduce) {
  .neko-page, .neko-card, .tm-sw-track, .tm-sw-knob, .tm-heat-cell, .neko-button {
    animation: none !important; transition: none !important;
  }
}
`
