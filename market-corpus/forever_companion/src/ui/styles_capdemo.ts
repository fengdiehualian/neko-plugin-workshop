// Adapted Lucide/Feather paths use CSS masks because hosted JSX has no SVG namespace.
export const CAPDEMO_STYLES = `
/*
Icon masks adapted from Lucide/Feather. Upstream license checked 2026-10-03.

ISC License
Copyright (c) 2026 Lucide Icons and Contributors
Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT,
OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE,
DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS
SOFTWARE.

The MIT License (MIT) (for portions derived from Feather)
Copyright (c) 2013-present Cole Bemis
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
*/
.tm-demo {
  container: tm-demo / inline-size; min-width: 0; width: 100%;
  --tm-demo-paper: rgb(var(--tm-surface-rgb)); --tm-demo-line: var(--tm-border);
  --tm-demo-blue: var(--primary); --tm-demo-green: var(--success); --tm-demo-ink: var(--text);
  --tm-demo-mode-ink: #64748b; --tm-demo-mode-surface: color-mix(in srgb, #64748b 8%, var(--tm-demo-paper)); --tm-demo-mode-line: color-mix(in srgb, #64748b 38%, var(--tm-demo-line));
  --tm-demo-input-ink: var(--muted); --tm-demo-input-surface: var(--tm-hover); --tm-demo-input-line: var(--tm-demo-line);
  --tm-demo-process-ink: var(--muted); --tm-demo-process-surface: var(--tm-hover); --tm-demo-process-line: var(--tm-demo-line);
  --tm-demo-output-ink: var(--muted); --tm-demo-output-surface: var(--tm-hover); --tm-demo-output-line: var(--tm-demo-line);
  --tm-demo-progress: 0; color: var(--text); letter-spacing: 0;
}
.tm-demo[data-mode="before"] {
  --tm-demo-input-ink: var(--muted); --tm-demo-input-surface: var(--tm-hover); --tm-demo-input-line: var(--tm-demo-line);
  --tm-demo-process-ink: var(--muted); --tm-demo-process-surface: var(--tm-hover); --tm-demo-process-line: var(--tm-demo-line);
  --tm-demo-output-ink: var(--muted); --tm-demo-output-surface: var(--tm-hover); --tm-demo-output-line: var(--tm-demo-line);
}
.tm-demo[data-mode="after"] {
  --tm-demo-mode-ink: #15803d; --tm-demo-mode-surface: color-mix(in srgb, #22c55e 10%, var(--tm-demo-paper)); --tm-demo-mode-line: color-mix(in srgb, #15803d 52%, var(--tm-demo-line));
  --tm-demo-input-ink: color-mix(in srgb, #2563eb 74%, var(--text));
  --tm-demo-input-surface: color-mix(in srgb, #3b82f6 11%, var(--tm-demo-paper));
  --tm-demo-input-line: color-mix(in srgb, #2563eb 55%, var(--tm-demo-line));
  --tm-demo-process-ink: color-mix(in srgb, #7c3aed 74%, var(--text));
  --tm-demo-process-surface: color-mix(in srgb, #8b5cf6 11%, var(--tm-demo-paper));
  --tm-demo-process-line: color-mix(in srgb, #7c3aed 55%, var(--tm-demo-line));
  --tm-demo-output-ink: color-mix(in srgb, #15803d 74%, var(--text));
  --tm-demo-output-surface: color-mix(in srgb, #22c55e 11%, var(--tm-demo-paper));
  --tm-demo-output-line: color-mix(in srgb, #15803d 55%, var(--tm-demo-line));
}
.tm-demo *, .tm-demo *::before, .tm-demo *::after { box-sizing: border-box; }
.tm-demo p, .tm-demo blockquote { margin: 0; }
.tm-demo-heading { margin: 0 0 12px; font-size: 12px; font-weight: 600; line-height: 1.6; color: var(--text); }
.tm-demo p, .tm-demo span, .tm-demo button { overflow-wrap: anywhere; }
.tm-demo button { font: inherit; letter-spacing: 0; cursor: pointer; }
.tm-demo button:disabled { cursor: not-allowed; opacity: .42; }
.tm-demo-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.tm-demo-modes { display: inline-flex; min-width: 0; border: 1px solid var(--tm-demo-line); border-radius: 6px; padding: 3px; background: var(--tm-demo-paper); }
.tm-demo-modes button { padding: 6px 12px; border: 0; border-radius: 4px; background: transparent; color: var(--muted); line-height: 1.35; font-size: 12px; font-weight: 600; }
.tm-demo-modes button[aria-pressed="false"]:hover { background: var(--tm-hover); }
.tm-demo-modes button[aria-pressed="true"] {
  color: #1e40af;
  background: #bfdbfe;
  box-shadow: inset 0 0 0 1px #93c5fd;
}
.tm-demo-modes button[aria-pressed="true"]:hover { background: #b3d4fb; }
.tm-demo-sample { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); font-size: 11px; }
.tm-demo-controls { display: flex; gap: 4px; margin-left: auto; flex: 0 0 auto; }
.tm-demo-icon-button { width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: 5px; color: var(--muted); background: transparent; }
.tm-demo-icon-button:hover:not(:disabled) { border-color: var(--tm-demo-line); color: var(--primary); background: var(--tm-accent-soft); }
.tm-demo-stage { position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 318px; background: var(--tm-demo-paper); border: 1px solid var(--tm-demo-line); border-radius: 8px; overflow: hidden; }
.tm-demo-beatline { display: flex; align-items: center; gap: 24px; min-height: 36px; padding: 8px 20px; color: var(--muted); font-size: 10px; border-bottom: 1px solid var(--tm-divider); background: var(--tm-hover); }
.tm-demo-beatline span { display: flex; align-items: center; gap: 7px; }
.tm-demo-beatline span::before { content: ""; width: 4px; height: 4px; border-radius: 50%; background: var(--tm-demo-line); }
.tm-demo-beatline span[data-active="true"] { color: var(--tm-demo-input-ink); }
.tm-demo-beatline span[data-active="true"]::before { background: var(--tm-demo-input-ink); }
.tm-demo-beatline span:nth-child(2)[data-active="true"] { color: var(--tm-demo-process-ink); }
.tm-demo-beatline span:nth-child(2)[data-active="true"]::before { background: var(--tm-demo-process-ink); }
.tm-demo-beatline span:nth-child(3)[data-active="true"] { color: var(--tm-demo-output-ink); }
.tm-demo-beatline span:nth-child(3)[data-active="true"]::before { background: var(--tm-demo-output-ink); }
.tm-demo-scene { flex: 1; display: grid; align-items: center; min-width: 0; padding: 24px 28px; min-height: 240px; }
.tm-demo-caption { min-height: 50px; display: flex; align-items: flex-start; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--tm-divider); background: var(--tm-hover); font-size: 12px; line-height: 1.65; }
.tm-demo-caption-mark { display: inline-flex; color: var(--primary); padding-top: 2px; flex: 0 0 auto; }
.tm-demo[data-mode="before"] .tm-demo-caption-mark { color: var(--muted); }
.tm-demo-progress { height: 2px; overflow: hidden; background: var(--tm-divider); margin: 8px 0 12px; }
.tm-demo-progress > span { display: block; height: 100%; width: 100%; transform-origin: left; transform: scaleX(var(--tm-demo-progress)); background: var(--tm-demo-input-ink); }
.tm-demo[data-phase="1"] .tm-demo-progress > span { background: var(--tm-demo-process-ink); }
.tm-demo[data-phase="2"] .tm-demo-progress > span { background: var(--tm-demo-output-ink); }
.tm-demo-flow { display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap; margin: 0 0 12px; }
.tm-demo .tm-demo-flow-node { display: inline-flex; gap: 7px; align-items: flex-start; flex: 1 1 100px; min-width: 0; padding: 9px 10px; color: var(--muted); border: 1px solid var(--tm-demo-line); border-radius: 5px; background: transparent; line-height: 1.6; font-size: 11px; white-space: normal; }
.tm-demo .tm-demo-flow-node[data-active="true"] { color: var(--primary); border-color: var(--primary); background: var(--tm-accent-soft); }
.tm-demo .tm-demo-flow-node[data-done="true"] { color: var(--tm-success-text); }
.tm-demo-step-number { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; font-size: 10px; border: 1px solid currentColor; border-radius: 50%; flex: 0 0 auto; }
.tm-demo-detail { display: flex; flex-direction: column; gap: 5px; line-height: 1.7; color: var(--muted); font-size: 11px; }
.tm-demo-notice { font-size: 10px; }
.tm-demo-label { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 10px; line-height: 1.6; }
.tm-demo-small-tag { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: var(--muted); line-height: 1.65; }
.tm-demo-small-tag .tm-demo-icon { width: 13px; height: 13px; }
.tm-demo-lane-title { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; color: var(--muted); }
.tm-demo-lane-title .tm-demo-icon { color: var(--secondary); }
.tm-demo-scene-footnote { font-size: 11px; line-height: 1.65; color: var(--muted); text-align: center; }
.tm-demo-note { border: 1px solid var(--tm-demo-line); border-radius: 6px; padding: 12px; background: var(--tm-demo-paper); min-width: 0; }
.tm-demo-note p { font-size: 12px; line-height: 1.7; margin-top: 5px; }
.tm-demo-note[data-tone="primary"] { border-color: color-mix(in srgb, var(--primary) 45%, var(--tm-demo-paper)); background: color-mix(in srgb, var(--primary) 5%, var(--tm-demo-paper)); }
.tm-demo-note[data-tone="success"] { border-color: color-mix(in srgb, var(--success) 40%, var(--tm-demo-paper)); background: color-mix(in srgb, var(--success) 5%, var(--tm-demo-paper)); }
.tm-demo-icon { display: inline-block; flex: 0 0 auto; width: 17px; height: 17px; background: currentColor; mask: var(--tm-demo-icon) center / contain no-repeat; -webkit-mask: var(--tm-demo-icon) center / contain no-repeat; }
.tm-demo-icon[data-icon="previous"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m15 18-6-6 6-6'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="next"], .tm-demo-icon[data-icon="arrow"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m9 18 6-6-6-6'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="play"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 3 14 9-14 9V3Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="pause"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2'%3E%3Crect x='5' y='3' width='4' height='18' rx='1'/%3E%3Crect x='15' y='3' width='4' height='18' rx='1'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="replay"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 11a9 9 0 1 1 3 6.7M3 4v7h7'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="check"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m20 6-11 11-5-5'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="info"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='12' cy='12' r='10'/%3E%3Cpath d='M12 16v-4M12 8h.01'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="flask"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M9 3h6m-5 0v7l-5 9a1 1 0 0 0 1 2h12a1 1 0 0 0 1-2l-5-9V3M8 16h8'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="chat"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="layers"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m12 3 10 6-10 6L2 9l10-6Zm-10 12 10 6 10-6M2 12l10 6 10-6'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="calendar"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='5' width='18' height='16' rx='2'/%3E%3Cpath d='M16 3v4M8 3v4M3 11h18'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="gift"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='8' width='18' height='4' rx='1'/%3E%3Cpath d='M5 12v8h14v-8M12 8v12M12 8H8.5A2.5 2.5 0 1 1 11 5.5L12 8Zm0 0h3.5A2.5 2.5 0 1 0 13 5.5L12 8Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="spark"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="flag"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 22V3s4-2 8 0 8 0 8 0v12s-4 2-8 0-8 0-8 0'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="book"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="monitor"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='2' y='3' width='20' height='14' rx='2'/%3E%3Cpath d='M8 21h8M12 17v4'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="shield"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Zm-3-11 2 2 4-4'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="lock"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='11' width='18' height='11' rx='2'/%3E%3Cpath d='M7 11V7a5 5 0 0 1 10 0v4'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="wrench"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m14.7 6.3 3 3 3.3-3.3a6 6 0 0 1-7.9 7.9l-7.9 7.9a2.1 2.1 0 0 1-3-3l7.9-7.9a6 6 0 0 1 7.9-7.9l-3.3 3.3Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="heart"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="clock"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='10'/%3E%3Cpath d='M12 6v6l4 2'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="scan"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 7V3h4m10 0h4v4m0 10v4h-4M7 21H3v-4M7 10h10M7 14h10'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="filter"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M22 3H2l8 9.5V19l4 2v-8.5L22 3Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="quote"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 21c3 0 7-3 7-7V5H2v9h5c0 3-2 4-4 4v3Zm12 0c3 0 7-3 7-7V5h-8v9h5c0 3-2 4-4 4v3Z'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="mail"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='2' y='4' width='20' height='16' rx='2'/%3E%3Cpath d='m22 7-9 6a2 2 0 0 1-2 0L2 7'/%3E%3C/svg%3E"); }
.tm-demo-icon[data-icon="pen"] { --tm-demo-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5'/%3E%3C/svg%3E"); }

.tm-demo-chat-layout { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr); gap: 28px; align-items: center; }
.tm-demo-conversation { display: flex; flex-direction: column; align-items: flex-start; gap: 12px; min-width: 0; }
.tm-demo-bubble { max-width: 92%; padding: 10px 12px; border: 1px solid var(--tm-demo-line); border-radius: 6px; font-size: 12px; line-height: 1.7; }
.tm-demo-incoming { background: var(--tm-hover); border-color: var(--tm-demo-line); }
.tm-demo-answer { align-self: flex-end; background: var(--tm-hover); }
.tm-demo-context { position: relative; align-self: stretch; border: 1px dashed var(--tm-demo-line); padding: 10px 12px; min-height: 54px; border-radius: 4px; }
.tm-demo-context .tm-demo-label { color: var(--muted); }
.tm-demo-context p { font-size: 11px; line-height: 1.65; }
.tm-demo-context[data-open="true"] { border-color: var(--tm-demo-line); }
.tm-demo-context-slip { position: absolute; width: 8px; height: 8px; border: 1px solid var(--tm-demo-line); background: var(--tm-demo-paper); top: -5px; right: 12px; }
.tm-demo-state-column { display: flex; flex-direction: column; gap: 12px; align-items: stretch; }
.tm-demo-state-index { font-size: 10px; color: var(--muted); display: flex; align-items: center; gap: 10px; }
.tm-demo-state-index > span { height: 1px; background: var(--tm-demo-line); flex: 1; }
.tm-demo-route { align-self: center; color: var(--muted); }
.tm-demo-route .tm-demo-icon { transform: rotate(90deg); }

.tm-demo-calendar-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: center; gap: 34px; }
.tm-demo-calendar { border: 1px solid var(--tm-demo-line); border-radius: 6px; overflow: hidden; background: var(--tm-demo-paper); }
.tm-demo-calendar-top { display: flex; align-items: center; gap: 8px; background: var(--tm-hover); border-bottom: 1px solid var(--tm-divider); padding: 10px 12px; font-size: 12px; line-height: 1.6; color: var(--muted); }
.tm-demo-calendar-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); padding: 10px; gap: 4px; }
.tm-demo-calendar-grid > span { position: relative; display: flex; justify-content: center; align-items: center; aspect-ratio: 1.15; border-radius: 3px; font-size: 11px; color: var(--muted); }
.tm-demo-calendar-grid > span[data-passed="true"] { background: var(--tm-hover); }
.tm-demo-calendar-grid > span[data-target="true"] { background: var(--tm-hover); color: var(--muted); font-weight: 600; border: 1px solid var(--tm-demo-line); }
.tm-demo-calendar-grid > span > i { position: absolute; top: -5px; right: -5px; display: flex; padding: 2px; border-radius: 3px; background: var(--tm-demo-paper); color: var(--muted); }
.tm-demo-calendar-grid > span > i .tm-demo-icon { width: 11px; height: 11px; }
.tm-demo-calendar-meta { display: flex; justify-content: space-between; gap: 10px; padding: 7px 10px; border-top: 1px solid var(--tm-divider); font-size: 9px; line-height: 1.6; color: var(--muted); }
.tm-demo-calendar-delivery { display: flex; flex-direction: column; align-items: center; gap: 13px; min-width: 0; }
.tm-demo-date-gate { display: flex; align-items: center; gap: 8px; font-size: 11px; line-height: 1.7; color: var(--muted); }
.tm-demo-date-gate[data-enabled="true"] > .tm-demo-icon { color: var(--muted); }
.tm-demo-envelope { position: relative; width: 100%; height: 104px; padding-top: 0; border: 1px solid var(--tm-demo-line); border-radius: 4px; margin-top: 24px; }
.tm-demo-envelope-flap { position: absolute; left: 0; right: 0; top: 0; height: 54px; background: color-mix(in srgb, var(--secondary) 8%, var(--tm-demo-paper)); clip-path: polygon(0 0, 100% 0, 50% 100%); z-index: 3; transform-origin: top; transition: transform .65s ease; }
.tm-demo-envelope-front { position: absolute; inset: 35% 0 0; background: color-mix(in srgb, var(--primary) 4%, var(--tm-demo-paper)); border-top: 1px solid var(--tm-demo-line); z-index: 3; }
.tm-demo-letter { position: absolute; inset: 8px 9px 6px; padding: 8px 10px; border: 1px solid var(--tm-demo-line); border-radius: 3px; background: var(--tm-demo-paper); font-size: 11px; line-height: 1.65; z-index: 2; transition: transform .7s ease; }
.tm-demo[data-phase="2"][data-mode="after"] .tm-demo-envelope-flap { transform: rotateX(180deg); z-index: 1; }
.tm-demo[data-phase="2"][data-mode="after"] .tm-demo-letter { transform: translateY(-40px); z-index: 4; }

.tm-demo-activity-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .9fr) minmax(0, 1fr); gap: 20px; align-items: center; }
.tm-demo-host { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.tm-demo-monitor { width: 100%; max-width: 195px; }
.tm-demo-monitor-screen { min-height: 116px; display: flex; flex-direction: column; align-items: center; gap: 9px; padding: 13px; border: 2px solid var(--tm-demo-line); border-radius: 6px; font-size: 12px; line-height: 1.65; text-align: center; background: var(--tm-hover); }
.tm-demo-monitor-screen > .tm-demo-icon { color: var(--muted); }
.tm-demo-monitor-base { display: block; width: 38%; height: 14px; margin: 0 auto; border-bottom: 2px solid var(--tm-demo-line); border-left: 1px solid var(--tm-demo-line); border-right: 1px solid var(--tm-demo-line); }
.tm-demo-signal-bars { display: flex; align-items: flex-end; gap: 4px; height: 21px; }
.tm-demo-signal-bars i { width: 6px; height: 7px; background: var(--tm-demo-line); border-radius: 1px; }
.tm-demo-signal-bars i:nth-child(2) { height: 12px; }.tm-demo-signal-bars i:nth-child(3) { height: 19px; }.tm-demo-signal-bars i:nth-child(4) { height: 13px; }.tm-demo-signal-bars i:nth-child(5) { height: 8px; }
.tm-demo-privacy-gate { display: flex; flex-direction: column; align-items: center; gap: 9px; text-align: center; font-size: 11px; line-height: 1.65; color: var(--muted); }
.tm-demo-privacy-gate > .tm-demo-icon { width: 29px; height: 29px; }
.tm-demo-privacy-control { display: flex; align-items: center; gap: 6px; color: var(--text); cursor: pointer; font-size: 10px; }
.tm-demo-privacy { width: 13px; height: 13px; accent-color: var(--primary); margin: 0; }
.tm-demo-gate-track { position: relative; display: flex; align-items: center; justify-content: space-between; width: 100%; height: 18px; border-top: 1px dashed var(--tm-demo-line); padding: 0 7px; }
.tm-demo-gate-track > i { width: 7px; height: 7px; border-radius: 2px; background: var(--primary); transform: translateY(-10px); }
.tm-demo-privacy-gate[data-closed="true"] { color: var(--muted); }
.tm-demo-privacy-gate[data-closed="true"] .tm-demo-gate-track i { background: var(--tm-demo-line); }
.tm-demo-activity-output { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; border: 1px solid var(--tm-demo-line); padding: 14px; border-radius: 6px; font-size: 12px; line-height: 1.7; }
.tm-demo-activity-output > .tm-demo-icon { color: var(--muted); width: 25px; height: 25px; }
.tm-demo-activity-output[data-enabled="true"] { border-color: color-mix(in srgb, var(--success) 50%, var(--tm-demo-paper)); }
.tm-demo-activity-output[data-enabled="true"] > .tm-demo-icon { color: var(--success); }

.tm-demo-mood-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr) minmax(0, 1fr); gap: 22px; align-items: center; }
.tm-demo-mood-in { display: flex; flex-direction: column; gap: 16px; }
.tm-demo-mood-in .tm-demo-bubble { max-width: 100%; }
.tm-demo-tool-call { display: flex; align-items: flex-start; gap: 8px; font-size: 10px; color: var(--muted); line-height: 1.7; }
.tm-demo-tool-call[data-enabled="true"] > .tm-demo-icon { color: var(--muted); }
.tm-demo-mood-instrument { position: relative; min-height: 196px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 9px; }
.tm-demo-mood-dial { position: absolute; width: 154px; height: 77px; top: 5px; left: 50%; transform: translateX(-50%); border: 7px solid var(--tm-demo-line); border-bottom: 0; border-radius: 154px 154px 0 0; }
.tm-demo-mood-dial span { position: absolute; width: 2px; height: 6px; background: var(--muted); bottom: 0; left: 50%; transform-origin: center 0; }
.tm-demo-mood-dial span:nth-of-type(1) { transform: rotate(-80deg) translateY(-60px); }.tm-demo-mood-dial span:nth-of-type(2) { transform: rotate(-40deg) translateY(-60px); }.tm-demo-mood-dial span:nth-of-type(3) { transform: rotate(0deg) translateY(-60px); }.tm-demo-mood-dial span:nth-of-type(4) { transform: rotate(40deg) translateY(-60px); }.tm-demo-mood-dial span:nth-of-type(5) { transform: rotate(80deg) translateY(-60px); }
.tm-demo-mood-needle { position: absolute; height: 53px; width: 2px; background: var(--tm-demo-line); bottom: 0; left: 50%; transform-origin: center bottom; transform: rotate(-35deg); transition: transform 1.2s ease; }
.tm-demo-mood-needle::after { content: ""; position: absolute; bottom: -4px; left: -3px; width: 8px; height: 8px; background: var(--tm-demo-line); border-radius: 50%; }
.tm-demo[data-mode="after"][data-phase="2"] .tm-demo-mood-needle { transform: rotate(30deg); }
.tm-demo-mood-center { padding-top: 86px; text-align: center; font-size: 11px; line-height: 1.7; color: var(--muted); }
.tm-demo-mood-center > .tm-demo-icon { width: 15px; height: 15px; }
.tm-demo-mood-history { display: flex; align-items: flex-end; justify-content: center; gap: 4px; width: 90%; height: 26px; border-bottom: 1px solid var(--tm-demo-line); }
.tm-demo-mood-history i { flex: 1; max-width: 13px; min-height: 3px; background: var(--tm-demo-line); border-radius: 2px 2px 0 0; transform-origin: bottom; transition: background .6s ease, transform .8s ease; }
.tm-demo[data-mode="after"][data-phase="2"] .tm-demo-mood-history i { background: var(--tm-demo-line); }
.tm-demo-mood-out { display: flex; flex-direction: column; gap: 12px; }
.tm-demo-clock { display: flex; align-items: center; gap: 8px; font-size: 10px; color: var(--muted); }
.tm-demo-clock i { height: 2px; background: var(--tm-demo-line); flex: 1; transform-origin: left; }

.tm-demo-tone-layout { display: flex; flex-direction: column; gap: 12px; }
.tm-demo-fast-lane { border-bottom: 1px solid var(--tm-demo-line); padding-bottom: 13px; }
.tm-demo-inline-message { display: grid; grid-template-columns: minmax(0, 1.7fr) 18px minmax(0, 1fr) 18px; align-items: center; gap: 16px; margin-top: 12px; font-size: 12px; line-height: 1.7; }
.tm-demo-inline-message > span:first-child { border-left: 2px solid var(--tm-demo-line); padding-left: 9px; }
.tm-demo-inline-message > .tm-demo-icon:last-child { color: var(--muted); }
.tm-demo-parallel-split { height: 14px; display: flex; align-items: center; gap: 8px; margin-left: 15px; color: var(--muted); }
.tm-demo-parallel-split span { height: 1px; width: 22px; background: var(--tm-demo-line); }
.tm-demo-parallel-split i { width: 6px; height: 6px; border-radius: 2px; background: var(--tm-demo-line); }
.tm-demo-analysis-lane { display: grid; grid-template-columns: minmax(0, .5fr) minmax(0, 1fr) minmax(0, 1.1fr); align-items: center; gap: 22px; padding: 10px 14px; border-left: 2px solid var(--tm-demo-line); background: var(--tm-hover); border-radius: 0 5px 5px 0; }
.tm-demo-tone-scan p, .tm-demo-analysis-result p { font-size: 11px; line-height: 1.7; }
.tm-demo-wave { position: relative; height: 45px; display: flex; align-items: center; gap: 3px; justify-content: center; overflow: hidden; margin-bottom: 8px; }
.tm-demo-wave > i { width: 3px; border-radius: 2px; background: var(--tm-demo-line); }
.tm-demo-wave > span { position: absolute; top: 1px; bottom: 1px; left: 0; width: 1px; background: var(--tm-demo-line); opacity: 0; }
.tm-demo-analysis-result { padding-left: 15px; border-left: 1px solid var(--tm-demo-line); }
.tm-demo-tone-footnote { padding: 0 15px; }

.tm-demo-fragments-layout { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, .65fr) minmax(0, 1fr); gap: 24px; align-items: center; }
.tm-demo-quote-stack { display: flex; flex-direction: column; gap: 10px; }
.tm-demo-quote { position: relative; padding: 11px 11px 15px; border-left: 2px solid var(--tm-demo-line); background: var(--tm-hover); font-size: 12px; line-height: 1.8; }
.tm-demo-quote > span:first-child { font-size: 28px; line-height: .7; color: var(--muted); }
.tm-demo-quote p { position: relative; z-index: 1; }
.tm-demo-highlight { position: absolute; bottom: 11px; left: 11px; right: 11px; height: 8px; background: color-mix(in srgb, var(--success) 15%, var(--tm-demo-paper)); transform-origin: left; transform: scaleX(0); transition: transform .8s ease; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-highlight, .tm-demo[data-mode="after"][data-phase="2"] .tm-demo-highlight { transform: scaleX(1); }
.tm-demo-text-line { width: 88%; height: 5px; background: var(--tm-hover); border-radius: 2px; }.tm-demo-text-line:last-child { width: 67%; }
.tm-demo-fragment-filter { display: flex; flex-direction: column; align-items: center; gap: 9px; color: var(--muted); font-size: 10px; line-height: 1.7; text-align: center; }
.tm-demo-fragment-filter > .tm-demo-icon { width: 34px; height: 34px; }
.tm-demo-fragment-filter[data-enabled="true"] > .tm-demo-icon { color: var(--secondary); }
.tm-demo-filter-slits { display: flex; gap: 3px; height: 14px; }.tm-demo-filter-slits i { height: 2px; width: 12px; background: var(--tm-demo-line); }
.tm-demo-fragment-book { position: relative; min-height: 172px; border: 1px solid var(--tm-demo-line); border-left: 4px solid var(--tm-demo-line); padding: 14px 12px; border-radius: 3px 6px 6px 3px; display: flex; flex-direction: column; gap: 8px; font-size: 12px; line-height: 1.7; }
.tm-demo-fragment-book[data-enabled="true"] { border-left-color: var(--success); }
.tm-demo-book-tab { position: absolute; right: 13px; top: -1px; width: 8px; height: 21px; background: var(--tm-demo-line); clip-path: polygon(0 0, 100% 0, 100% 100%, 50% 75%, 0 100%); }
.tm-demo-original-quote { font-size: 10px; line-height: 1.8; color: var(--muted); border-left: 1px solid var(--tm-demo-line); padding-left: 7px; }
.tm-demo-book-lines { display: flex; flex-direction: column; gap: 6px; margin-top: auto; }.tm-demo-book-lines i { height: 1px; background: var(--tm-demo-line); }
.tm-demo-fragments-layout > .tm-demo-scene-footnote { grid-column: 1 / -1; }

.tm-demo-journal-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .9fr) minmax(0, 1fr); gap: 20px; align-items: center; }
.tm-demo-invitation { padding: 13px; border: 1px solid var(--tm-demo-line); border-radius: 6px; font-size: 12px; line-height: 1.7; }
.tm-demo-invitation > p { margin-top: 9px; }
.tm-demo-materials { display: flex; align-items: flex-end; gap: 7px; margin: 14px 0; height: 31px; }
.tm-demo-materials > span { width: 28px; height: 26px; border: 1px solid var(--tm-demo-line); background: var(--tm-hover); border-radius: 2px; transform: rotate(-8deg); }.tm-demo-materials > span:nth-child(2) { transform: rotate(5deg); height: 30px; }.tm-demo-materials > span:nth-child(3) { transform: rotate(-3deg); height: 22px; }
.tm-demo-invitation-time { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--muted); }
.tm-demo-journal-choice { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 8px; font-size: 11px; line-height: 1.7; }
.tm-demo-choice-line { width: 100%; height: 14px; border-top: 1px dashed var(--tm-demo-line); }
.tm-demo-choice-buttons { display: flex; flex-direction: column; width: 100%; gap: 6px; margin-top: 6px; }
.tm-demo-choice-buttons button { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 7px; border: 1px solid var(--tm-demo-line); border-radius: 5px; background: transparent; color: var(--muted); font-size: 10px; line-height: 1.6; }
.tm-demo-choice-buttons button[aria-pressed="true"] { color: var(--primary); border-color: var(--primary); background: var(--tm-accent-soft); }
.tm-demo-journal-page { position: relative; min-height: 176px; border: 1px solid var(--tm-demo-line); border-left-width: 3px; padding: 13px 12px 29px; border-radius: 2px 5px 5px 2px; font-size: 11px; line-height: 1.75; }
.tm-demo-journal-page p { margin: 9px 0; }
.tm-demo-writing-lines { display: flex; flex-direction: column; gap: 9px; }.tm-demo-writing-lines i { height: 1px; background: var(--tm-demo-line); transform-origin: left; }.tm-demo-writing-lines i:last-child { width: 65%; }
.tm-demo-journal-page[data-written="true"] { border-left-color: var(--success); }.tm-demo-journal-page[data-written="true"] .tm-demo-writing-lines i { background: color-mix(in srgb, var(--secondary) 35%, var(--tm-demo-paper)); }
.tm-demo-page-status { position: absolute; bottom: 8px; right: 12px; left: 12px; font-size: 9px; line-height: 1.6; color: var(--muted); }
.tm-demo-journal-layout > .tm-demo-scene-footnote { grid-column: 1 / -1; }

.tm-demo-review-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .7fr) minmax(0, 1.15fr); gap: 25px; align-items: center; }
.tm-demo-review-sources { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.tm-demo-source-row { display: flex; align-items: center; gap: 5px; min-height: 31px; padding-bottom: 8px; border-bottom: 1px solid var(--tm-divider); font-size: 10px; color: var(--muted); }
.tm-demo-source-row > .tm-demo-icon { color: var(--muted); width: 15px; height: 15px; }.tm-demo-source-row > span { margin-right: auto; }
.tm-demo-source-row > i { width: 5px; height: 15px; border-radius: 1px; background: var(--tm-demo-line); }
.tm-demo-mini-chart { display: flex; align-items: flex-end; gap: 3px; height: 20px; }.tm-demo-mini-chart i { height: 10px; width: 5px; background: var(--tm-demo-line); }.tm-demo-mini-chart i:nth-child(2) { height: 15px; }.tm-demo-mini-chart i:nth-child(4) { height: 20px; }
.tm-demo-mini-sheets { display: flex; gap: 4px; margin: 0 !important; }.tm-demo-mini-sheets i { display: block; height: 17px; width: 12px; border: 1px solid var(--tm-demo-line); border-radius: 2px; }
.tm-demo-review-compiler { display: flex; flex-direction: column; align-items: center; gap: 10px; font-size: 10px; line-height: 1.8; color: var(--muted); text-align: center; }
.tm-demo-review-compiler > .tm-demo-icon { width: 32px; height: 32px; }.tm-demo-review-compiler[data-enabled="true"] > .tm-demo-icon { color: var(--muted); }
.tm-demo-compiler-packets { display: flex; gap: 8px; width: 100%; justify-content: center; }.tm-demo-compiler-packets i { width: 6px; height: 6px; border: 1px solid var(--tm-demo-line); border-radius: 2px; }
.tm-demo-review-page { border: 1px solid var(--tm-demo-line); border-radius: 3px; padding: 13px; min-height: 174px; display: flex; flex-direction: column; gap: 10px; font-size: 12px; line-height: 1.75; }
.tm-demo-review-page[data-enabled="true"] { border-top: 3px solid var(--secondary); }
.tm-demo-review-rule { height: 1px; background: var(--tm-demo-line); }
.tm-demo-review-page .tm-demo-review-destination { font-size: 10px; color: var(--muted); }
.tm-demo-review-page > .tm-demo-small-tag { margin-top: auto; }

/* The comparison is semantic: before stays neutral, while after separates
   incoming context, background processing, and the resulting state. */
.tm-demo[data-mode="before"] :is(
  .tm-demo-context[data-open="true"], .tm-demo-date-gate[data-enabled="true"],
  .tm-demo-tool-call[data-enabled="true"], .tm-demo-analysis-lane[data-enabled="true"],
  .tm-demo-fragment-filter[data-enabled="true"], .tm-demo-review-compiler[data-enabled="true"]
) {
  border-color: var(--tm-demo-line);
  color: var(--muted);
  background: var(--tm-hover);
}
.tm-demo[data-mode="before"] :is(
  .tm-demo-answer, .tm-demo-activity-output, .tm-demo-fragment-book,
  .tm-demo-review-page, .tm-demo-mood-out, .tm-demo-analysis-result,
  .tm-demo-journal-page
) {
  border-color: var(--tm-demo-line);
  background: var(--tm-hover);
}
.tm-demo[data-mode="before"] .tm-demo-calendar-grid > span[data-target="true"] {
  color: var(--muted);
  border-color: var(--tm-demo-line);
  background: var(--tm-hover);
}
.tm-demo[data-mode="after"] .tm-demo-context[data-open="true"],
.tm-demo[data-mode="after"] .tm-demo-date-gate[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-tool-call[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-analysis-lane[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-fragment-filter[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-review-compiler[data-enabled="true"] {
  color: var(--tm-demo-process-ink);
  border-color: var(--tm-demo-process-line);
  background: var(--tm-demo-process-surface);
}
.tm-demo[data-mode="after"] .tm-demo-context[data-open="true"] .tm-demo-label,
.tm-demo[data-mode="after"] .tm-demo-analysis-lane[data-enabled="true"] .tm-demo-lane-title,
.tm-demo[data-mode="after"] .tm-demo-fragment-filter[data-enabled="true"] > .tm-demo-icon,
.tm-demo[data-mode="after"] .tm-demo-review-compiler[data-enabled="true"] > .tm-demo-icon {
  color: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"] :is(
  .tm-demo-activity-output[data-enabled="true"],
  .tm-demo-fragment-book[data-enabled="true"], .tm-demo-review-page[data-enabled="true"],
  .tm-demo-analysis-result, .tm-demo-journal-page[data-written="true"]
) {
  border-color: var(--tm-demo-output-line);
  background: var(--tm-demo-output-surface);
}
.tm-demo[data-mode="after"] .tm-demo-activity-output[data-enabled="true"] > .tm-demo-icon,
.tm-demo[data-mode="after"] .tm-demo-fragment-book[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-review-page[data-enabled="true"],
.tm-demo[data-mode="after"] .tm-demo-journal-page[data-written="true"] {
  color: var(--tm-demo-output-ink);
}
.tm-demo[data-mode="after"] .tm-demo-activity-output[data-enabled="true"] .tm-demo-label,
.tm-demo[data-mode="after"] .tm-demo-review-page[data-enabled="true"] .tm-demo-label,
.tm-demo[data-mode="after"] .tm-demo-journal-page[data-written="true"] .tm-demo-label {
  color: var(--tm-demo-output-ink);
}
.tm-demo[data-mode="after"] .tm-demo-envelope[data-open="true"] {
  border-color: var(--tm-demo-output-line);
  background: var(--tm-demo-output-surface);
}
.tm-demo[data-mode="after"] .tm-demo-date-gate[data-enabled="true"] > .tm-demo-icon {
  color: var(--tm-demo-output-ink);
}
.tm-demo[data-mode="after"] .tm-demo-privacy-gate[data-closed="false"],
.tm-demo[data-mode="after"] .tm-demo-privacy-gate[data-closed="false"] .tm-demo-privacy-control {
  color: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"] .tm-demo-privacy-gate[data-closed="false"] .tm-demo-gate-track > i {
  background: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"] .tm-demo-mood-instrument[data-enabled="true"] {
  color: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"] .tm-demo-mood-instrument[data-enabled="true"] .tm-demo-mood-dial {
  border-color: var(--tm-demo-process-line);
}
.tm-demo[data-mode="after"] .tm-demo-mood-instrument[data-enabled="true"] .tm-demo-mood-needle,
.tm-demo[data-mode="after"] .tm-demo-mood-instrument[data-enabled="true"] .tm-demo-mood-needle::after {
  background: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"] .tm-demo-mood-instrument[data-enabled="true"] .tm-demo-mood-center {
  color: var(--tm-demo-process-ink);
}
.tm-demo[data-mode="after"][data-phase="1"] :is(
  .tm-demo-context[data-open="true"],
  .tm-demo-date-gate[data-enabled="true"], .tm-demo-tool-call[data-enabled="true"],
  .tm-demo-analysis-lane[data-enabled="true"], .tm-demo-fragment-filter[data-enabled="true"],
  .tm-demo-review-compiler[data-enabled="true"]
) {
  box-shadow: 0 0 0 2px color-mix(in srgb, #7c3aed 20%, transparent);
}
.tm-demo[data-mode="after"][data-phase="2"] :is(
  .tm-demo-activity-output[data-enabled="true"],
  .tm-demo-fragment-book[data-enabled="true"], .tm-demo-review-page[data-enabled="true"],
  .tm-demo-analysis-result, .tm-demo-journal-page[data-written="true"]
) {
  box-shadow: 0 0 0 2px color-mix(in srgb, #15803d 20%, transparent);
}

/* Semantic objects settle between beats; decorative movement never carries the only explanation. */
.tm-demo[data-phase="0"] :is(.tm-demo-conversation > .tm-demo-incoming, .tm-demo-calendar, .tm-demo-host, .tm-demo-mood-in, .tm-demo-fast-lane, .tm-demo-quote-stack, .tm-demo-invitation, .tm-demo-review-sources) { animation: tm-demo-arrive .9s ease both; }
.tm-demo .tm-demo-answer, .tm-demo .tm-demo-activity-output, .tm-demo .tm-demo-fragment-book,
.tm-demo .tm-demo-review-page, .tm-demo .tm-demo-mood-out, .tm-demo .tm-demo-analysis-result {
  opacity: .48; transform: translateY(7px); transition: opacity .55s ease, transform .7s ease, border-color .4s ease;
}
.tm-demo[data-phase="2"] :is(.tm-demo-answer, .tm-demo-activity-output, .tm-demo-fragment-book, .tm-demo-review-page, .tm-demo-mood-out, .tm-demo-analysis-result) { opacity: 1; transform: translateY(0); }
.tm-demo[data-phase="0"] :is(.tm-demo-context, .tm-demo-process-note, .tm-demo-date-gate, .tm-demo-tool-call, .tm-demo-analysis-lane, .tm-demo-fragment-filter, .tm-demo-review-compiler) { opacity: .4; }
.tm-demo :is(.tm-demo-context, .tm-demo-process-note, .tm-demo-date-gate, .tm-demo-tool-call, .tm-demo-analysis-lane, .tm-demo-fragment-filter, .tm-demo-review-compiler) { transition: opacity .5s ease; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-wave > span { opacity: 1; background: var(--tm-demo-process-ink); animation: tm-demo-scan 2s linear infinite; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-gate-track > i,
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-compiler-packets > i { animation: tm-demo-packet 1.8s ease-in-out infinite; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-gate-track > i:nth-child(2),
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-compiler-packets > i:nth-child(2) { animation-delay: .22s; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-gate-track > i:nth-child(3),
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-compiler-packets > i:nth-child(3) { animation-delay: .44s; }
.tm-demo[data-private="true"] .tm-demo-gate-track > i { animation: none !important; }
.tm-demo[data-mode="after"][data-phase="1"] .tm-demo-context-slip { animation: tm-demo-slip 2s ease-in-out infinite; }
.tm-demo[data-choice="write"] .tm-demo-writing-lines > i { animation: tm-demo-write .8s ease both; }
.tm-demo[data-choice="write"] .tm-demo-writing-lines > i:nth-child(2) { animation-delay: .15s; }.tm-demo[data-choice="write"] .tm-demo-writing-lines > i:nth-child(3) { animation-delay: .3s; }.tm-demo[data-choice="write"] .tm-demo-writing-lines > i:nth-child(4) { animation-delay: .45s; }
.tm-demo[data-playing="false"] * { animation-play-state: paused !important; transition: none !important; }
.tm-demo[data-reduced="true"] *, .tm-demo[data-reduced="true"] *::before, .tm-demo[data-reduced="true"] *::after { animation: none !important; transition: none !important; }
@keyframes tm-demo-scan { from { transform: translateX(0); } to { transform: translateX(200px); } }
@keyframes tm-demo-packet { 0%, 15% { opacity: .25; transform: translate(-8px, -3px); } 60% { opacity: 1; transform: translate(7px, -3px); } 100% { opacity: .25; transform: translate(12px, -3px); } }
@keyframes tm-demo-slip { 0%, 20% { transform: translateY(-12px); opacity: 0; } 55%, 80% { transform: translateY(4px); opacity: 1; } 100% { transform: translateY(4px); opacity: 0; } }
@keyframes tm-demo-write { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes tm-demo-arrive { from { transform: translateY(6px); } to { transform: translateY(0); } }
@container tm-demo (max-width: 560px) {
  .tm-demo-scene { padding: 20px 18px; min-height: 258px; }
  .tm-demo-chat-layout, .tm-demo-calendar-layout { gap: 18px; }
  .tm-demo-activity-layout, .tm-demo-mood-layout, .tm-demo-fragments-layout, .tm-demo-journal-layout, .tm-demo-review-layout { gap: 12px; }
  .tm-demo-mood-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .tm-demo-mood-out { grid-column: 1 / -1; flex-direction: row; align-items: center; }.tm-demo-mood-out > .tm-demo-note { flex: 1; }.tm-demo-mood-out > .tm-demo-clock { width: 25%; }
  .tm-demo-mood-dial { width: 130px; height: 65px; border-width: 6px; }.tm-demo-mood-dial span { display: none; }.tm-demo-mood-needle { height: 47px; }.tm-demo-mood-center { padding-top: 74px; }
  .tm-demo-mood-instrument { min-height: 176px; }
  .tm-demo-analysis-lane { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
  .tm-demo-analysis-lane > .tm-demo-lane-title { grid-column: 1 / -1; }
  .tm-demo-inline-message { gap: 8px; }
  .tm-demo-privacy-gate > .tm-demo-icon { width: 23px; height: 23px; }
  .tm-demo-activity-output, .tm-demo-monitor-screen { padding: 10px; }
  .tm-demo-journal-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }.tm-demo-journal-choice { grid-column: 1; grid-row: 2; }.tm-demo-journal-page { grid-column: 2; grid-row: 1 / 3; align-self: stretch; }.tm-demo-choice-buttons { flex-direction: row; }.tm-demo-choice-buttons button { flex: 1; }
  .tm-demo-choice-line { display: none; }
}
@container tm-demo (max-width: 380px) {
  .tm-demo-toolbar { gap: 8px; }.tm-demo-controls { gap: 0; }.tm-demo-sample { flex: 1; justify-content: flex-end; }.tm-demo-controls { width: 100%; justify-content: flex-end; margin-top: -4px; }
  .tm-demo-modes button { padding: 6px 9px; }
  .tm-demo-beatline { gap: 18px; padding-left: 12px; }
  .tm-demo-scene { padding: 18px 12px; }
  .tm-demo-chat-layout { grid-template-columns: minmax(0, 1fr); gap: 15px; }.tm-demo-state-column { display: none; }.tm-demo-context p { font-size: 10px; }
  .tm-demo-calendar-layout { grid-template-columns: minmax(0, 1fr) minmax(0, .95fr); gap: 12px; }.tm-demo-calendar-grid { padding: 5px; gap: 2px; }.tm-demo-calendar-grid > span { font-size: 9px; }.tm-demo-calendar-top { padding: 8px; font-size: 10px; }.tm-demo-calendar-meta { font-size: 8px; flex-direction: column; gap: 0; }.tm-demo-date-gate { font-size: 10px; }.tm-demo-letter { padding: 7px; font-size: 10px; }
  .tm-demo-activity-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 15px; }.tm-demo-host { align-self: start; }.tm-demo-privacy-gate { padding-top: 7px; }.tm-demo-activity-output { grid-column: 1 / -1; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 7px; }.tm-demo-activity-output > p, .tm-demo-activity-output > .tm-demo-small-tag { width: 100%; }
  .tm-demo-fragments-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }.tm-demo-fragment-filter { grid-column: 1; grid-row: 2; flex-direction: row; text-align: left; }.tm-demo-fragment-filter > .tm-demo-icon { width: 25px; height: 25px; }.tm-demo-filter-slits { display: none; }.tm-demo-fragment-book { grid-column: 2; grid-row: 1 / 3; align-self: stretch; font-size: 11px; }.tm-demo-quote { font-size: 11px; }
  .tm-demo-review-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 13px; }.tm-demo-review-compiler { grid-column: 1; grid-row: 2; flex-direction: row; text-align: left; }.tm-demo-review-page { grid-column: 2; grid-row: 1 / 3; align-self: stretch; font-size: 11px; }.tm-demo-compiler-packets { display: none; }.tm-demo-source-row { flex-wrap: wrap; }.tm-demo-source-row > span { margin: 0; }
  .tm-demo-caption { padding: 10px 12px; font-size: 11px; }
  .tm-demo .tm-demo-flow-node { flex-basis: 115px; }
}
@media (prefers-reduced-motion: reduce) {
  .tm-demo *, .tm-demo *::before, .tm-demo *::after { animation: none !important; transition: none !important; }
}
`
