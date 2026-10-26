# -*- coding: utf-8 -*-
"""统一帮助渲染器: 帮助文档 + 主题 → HTML → PNG(官方 UI Kit 视觉)。

用法(由 core/brain.py 调用)::

    doc = normalize_help(raw, game_id, game_name)
    page = resolve_topic(doc, topic)
    pages = await renderer.render(doc, page)      # List[bytes]

- 视觉**不在这里发明**: token 与类名全部取自官方 UI Kit(themes.py), 换主题只换 token。
- 浏览器渲染复用 ``ImageRenderer.render_html``(Playwright + 插件内置 Chromium);
  没有浏览器时返回 ``[]``, 调用方继续降级(PIL / 纯文本)。
- 结果按 HTML 哈希缓存, 同内容不重复起浏览器。
"""
from __future__ import annotations

import hashlib
import html as _html
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from .contract import Group, HelpDoc, Page
from .themes import AssetResolver, icon_glyph, theme_tokens, theme_veil

log = logging.getLogger(__name__)

WIDTH = 740
CATALOG_HEIGHT_BUDGET = 1400   # 总图每页高度预算(px): 装得下就一页给全, 装不下才分页
GROUP_CMD_PER_PAGE = 22        # 分组页每页指令数
FLAT_CMD_PER_PAGE = 26         # 扁平(老格式)帮助每页指令数

BASE_CSS = """
* { box-sizing:border-box; margin:0; padding:0; }
body { width:%(width)dpx; position:relative; color:var(--text); background:var(--bg);
  font-family:Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI",
              "Microsoft YaHei", sans-serif; }
.bgart { position:absolute; inset:0; z-index:0;
  background-size:cover; background-position:76%% 16%%; }
.veil { position:absolute; inset:0; z-index:1; background:%(veil)s; }
.page { position:relative; z-index:2; padding:18px; display:grid; gap:12px; }
.head { display:flex; align-items:center; gap:12px; }
.ava { width:58px; height:58px; border-radius:50%%; overflow:hidden; flex:0 0 58px;
  border:2px solid rgba(64,158,255,0.55); box-shadow:var(--shadow-soft); background:#fff; }
.ava img { width:100%%; height:100%%; object-fit:cover; object-position:center 16%%;
  display:block; }
.page-title { font-size:22px; font-weight:760; letter-spacing:0.2px; }
.page-sub { margin-top:4px; color:var(--muted); font-size:13px; line-height:1.55; }
.roleTag { display:inline-flex; align-items:center; gap:5px; margin-top:6px;
  border:1px solid rgba(20,184,166,0.3); background:rgba(20,184,166,0.1); color:#0f766e;
  border-radius:999px; padding:2px 9px; font-size:11.5px; font-weight:650; }
.stats { display:flex; gap:12px; }
.stat { flex:1; padding:9px 12px; border:1px solid var(--border);
  border-radius:var(--radius-lg); background:var(--surface); display:grid; gap:3px; }
.stat .l { color:var(--muted); font-size:12px; font-weight:650; }
.stat .v { color:var(--text); font-size:18px; font-weight:780; line-height:1.1; }
.card { border:1px solid var(--border); border-radius:var(--radius-md);
  background:var(--surface); overflow:hidden; }
.card-hd { padding:11px 13px 0; display:flex; align-items:center; gap:8px; }
.card-tt { font-size:15px; font-weight:720; }
.badge { display:inline-flex; align-items:center; gap:6px; width:fit-content; margin-left:auto;
  padding:3px 9px; border-radius:999px; border:1px solid var(--border);
  background:var(--surface-strong); color:var(--muted); font-size:12px; font-weight:650; }
.badge::before { content:''; width:7px; height:7px; border-radius:999px;
  background:var(--primary); }
.badge[data-tone="info"]::before { background:var(--info); }
.card-bd { padding:10px 13px 13px; }
.chips { display:flex; flex-wrap:wrap; gap:5px; }
.chip { border:1px solid var(--border); border-radius:999px; padding:2px 8px; font-size:11.5px;
  background:var(--surface-strong); }
.chip.more { color:var(--muted); }
.tip { border:1px solid rgba(230,162,60,0.28); border-radius:14px; padding:11px 12px;
  background:rgba(230,162,60,0.1); line-height:1.65; font-size:12.5px; display:flex;
  gap:9px; align-items:flex-start; }
.tip img { width:30px; height:30px; border-radius:50%%; flex:0 0 30px; object-fit:cover;
  object-position:center 16%%; border:1px solid rgba(230,162,60,0.45); }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.grid1 { display:grid; grid-template-columns:1fr; gap:12px; }
/* 总图: 大类 → 子分组 → 逐条指令 */
.subrows { display:grid; gap:10px; }
.subrow { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.subico { font-size:14px; }
.subnm { font-size:12.5px; font-weight:650; color:var(--text); }
.subn { font-size:11px; color:var(--muted); background:rgba(148,163,184,0.12);
  border-radius:999px; padding:1px 7px; margin-right:2px; }
.sub-nm { font-size:12px; color:var(--text); }
.sub-n { font-size:11px; color:var(--muted); }
.table { width:100%%; border-collapse:separate; border-spacing:0; overflow:hidden;
  border:1px solid var(--border); border-radius:var(--radius-lg); }
.table th, .table td { padding:10px 12px; text-align:left; font-size:13px;
  border-bottom:1px solid var(--border); }
.table th { color:var(--muted); font-size:12px; font-weight:650;
  background:rgba(148,163,184,0.08); }
.table tr:last-child td { border-bottom:none; }
.table td.k { color:var(--primary); font-weight:650; white-space:nowrap; width:36%%; }
.table td.d { color:var(--text); }
.divider { height:1px; background:var(--border); }
.foot { display:flex; align-items:center; justify-content:center; gap:8px;
  color:var(--muted); font-size:12px; }
.foot img { width:16px; height:16px; border-radius:4px; }
.emoji { font-family:'Segoe UI Emoji','Apple Color Emoji',sans-serif; font-size:15px; }
.mascot { position:absolute; right:8px; bottom:2px; width:112px; z-index:3;
  filter:drop-shadow(0 6px 16px rgba(15,23,42,0.28)); }
/* 版式块 */
.steps { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }.step { border:1px solid var(--border); border-radius:var(--radius-md); padding:8px 10px;
  background:var(--surface-strong); text-align:center; min-width:92px; }
.step .s1 { display:block; font-size:12.5px; font-weight:650; margin-top:4px; }
.step .s2 { font-size:10.5px; color:var(--muted); }
.arrow { color:var(--muted); font-size:15px; }
.slots { display:flex; gap:10px; }
.slot { flex:1; border:1px dashed var(--border); border-radius:var(--radius-md);
  padding:10px 8px; text-align:center; background:var(--surface-strong); }
.slot .sn { display:block; font-size:12.5px; font-weight:650; margin-top:5px; }
.slot .sd { font-size:10.5px; color:var(--muted); }
.bag { display:grid; gap:6px; }
.cell { aspect-ratio:1/1; border:1px solid var(--border); border-radius:var(--radius-sm);
  background:var(--surface-strong); display:flex; align-items:center;
  justify-content:center; font-size:14px; }
.cell.on { border-color:var(--primary); background:rgba(64,158,255,0.10); }
/* 棋盘块(棋类游戏): 交叉点/格心两种摆法 + 坐标 + 最后一手标记 */
.board { display:grid; gap:0; padding:5px; background:var(--surface-strong);
  border:1px solid var(--border); border-radius:var(--radius-sm); }
.bcell { position:relative; aspect-ratio:1/1; display:flex; align-items:center;
  justify-content:center; }
.bcell.box { border:1px solid var(--border); }
.ln { position:absolute; background:var(--border); }
.ln.h { left:0; right:0; height:1px; top:50%%; }
.ln.v { top:0; bottom:0; width:1px; left:50%%; }
.bcell.el .ln.h { left:50%%; }
.bcell.er .ln.h { right:50%%; }
.bcell.et .ln.v { top:50%%; }
.bcell.eb .ln.v { bottom:50%%; }
.pc { position:relative; z-index:2; width:80%%; height:80%%; border-radius:50%%;
  display:flex; align-items:center; justify-content:center; font-size:11px;
  font-weight:700; border:1px solid rgba(0,0,0,.35); }
.pc-b { background:#2f3038; color:#fff; }
.pc-w { background:#fbfbf6; color:#2f3038; }
.pc-r { background:#c8443c; color:#fff; }
.pc.last { box-shadow:0 0 0 2px var(--primary); }
.star { position:absolute; width:18%%; height:18%%; border-radius:50%%;
  background:var(--border); z-index:1; }
.blab { display:flex; align-items:center; justify-content:center;
  font-size:9px; color:var(--muted); }
.block-tt { font-size:12.5px; font-weight:650; color:var(--muted); margin-bottom:8px; }
/* 卡片组(天赋/成就这类) */
.cardgrid { display:grid; gap:10px; }
.mini { border:1px solid var(--border); border-radius:var(--radius-md);
  background:var(--surface-strong); padding:10px; display:grid; gap:4px;
  justify-items:center; text-align:center; }
.mini-ico { font-size:20px; }
.mini-nm { font-size:12.5px; font-weight:650; color:var(--text); }
.mini-ds { font-size:11px; color:var(--muted); line-height:1.45; }
/* 进度条(属性/总评这类) */
.stats2 { display:grid; gap:8px; }
.statrow { display:flex; align-items:center; gap:9px; }
.statlb { font-size:12.5px; color:var(--text); min-width:44px; }
.bar { flex:1; height:9px; border-radius:999px; overflow:hidden;
  background:rgba(148,163,184,0.22); }
.bar i { display:block; height:100%%; border-radius:inherit; background:var(--primary); }
.statvl { font-size:12.5px; font-weight:650; color:var(--text); min-width:26px;
  text-align:right; }
.statnt { font-size:11.5px; color:var(--muted); min-width:48px; }
/* 目录页(一级帮助): 只列功能分组, 不下钻明细 —— 指令分级的第一步 */
.dirrow { padding:9px 13px; border-bottom:1px solid var(--border); }
.dirrow:last-child { border-bottom:none; }
.dirtop { display:flex; align-items:center; gap:7px; }
.dirn { font-size:13.5px; font-weight:720; color:var(--text); }
.dirb { margin-left:auto; font-size:11.5px; color:var(--muted); }
.dirc { display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }
"""

# 聊天窗友好(窄版): 宿主图片气泡 max-width≈280px, 所以正文按 320px 渲染;
# 两列会挤压 → 统一单列, 表格改成"指令在上、说明在下"。
NARROW_CSS = """
.grid2 { grid-template-columns:1fr; }
.stats { flex-direction:column; gap:8px; }
.page { padding:14px; }
.page-title { font-size:19px; }
.ava { width:46px; height:46px; flex:0 0 46px; }
.mascot { width:84px; }
.table { border:none; border-radius:0; }
.table th { display:none; }
.table tr { display:block; padding:8px 2px; border-bottom:1px solid var(--border); }
.table tr:last-child { border-bottom:none; }
.table td { display:block; width:auto !important; white-space:normal !important;
  padding:0; border:none; }
.table td.k { font-size:13.5px; margin-bottom:2px; }
.table td.d { color:var(--muted); font-size:12.5px; line-height:1.5; }
.steps { flex-direction:column; align-items:stretch; }
.step { min-width:0; }
.slots { flex-direction:column; }
"""


def _esc(text: Any) -> str:
    return _html.escape(str(text if text is not None else ""), quote=True)


class HelpRenderer:
    """帮助图渲染器(统一入口)。"""

    def __init__(self, img_renderer: Any, code_dir: str = "", cache_dir: str = "",
                 width: Optional[int] = None, config_manager: Any = None):
        self.img = img_renderer
        self.assets = AssetResolver(code_dir, cache_dir)
        self.cache_dir = cache_dir or ""
        self._icon_override: Dict[str, str] = {}
        self._cfg_mgr = config_manager
        self._width_override = width
        self.width = 320
        self.narrow = True
        self.catalog_budget = CATALOG_HEIGHT_BUDGET
        self.group_cmds = GROUP_CMD_PER_PAGE
        self.flat_cmds = FLAT_CMD_PER_PAGE
        self._refresh_layout()

    def _refresh_layout(self) -> None:
        """刷新版式参数。**每次渲染前调用** → 面板改宽度立即生效，不用重启。

        宽度来源优先级：构造时显式传入 → 主配置 ``[help] width`` →
        环境变量 ``NEKO_ARCADE_HELP_WIDTH`` → 默认 320。
        宿主聊天图片气泡 max-width≈280px，320 是窄版默认值；窗口/宿主不同
        可以随时调大调小，这里不写死。
        """
        width = self._width_override
        if width is None and self._cfg_mgr is not None:
            try:
                cfg = self._cfg_mgr.load_main_config() or {}
                section = cfg.get("help") if isinstance(cfg.get("help"), dict) else {}
                raw = section.get("width") or cfg.get("help_width")
                if raw is not None:
                    width = int(raw)
            except Exception:  # noqa: BLE001 - 配置坏值不影响默认宽度
                width = None
        if width is None:
            try:
                width = int(os.environ.get("NEKO_ARCADE_HELP_WIDTH") or 320)
            except (TypeError, ValueError):
                width = 320
        try:
            w = int(width)
        except (TypeError, ValueError):
            w = 320
        self.width = max(240, min(900, w))
        self.narrow = self.width < 520
        self.catalog_budget = 900 if self.narrow else CATALOG_HEIGHT_BUDGET
        self.group_cmds = 10 if self.narrow else GROUP_CMD_PER_PAGE
        self.flat_cmds = 12 if self.narrow else FLAT_CMD_PER_PAGE

    def layout(self) -> Dict[str, Any]:
        """当前版式参数(供面板/日志查看)。"""
        return {"width": self.width, "narrow": self.narrow,
                "catalog_budget": self.catalog_budget,
                "group_cmds": self.group_cmds, "flat_cmds": self.flat_cmds}

    # ── 对外主入口 ────────────────────────────────────────
    async def _prepare_assets(self) -> None:
        """把主题素材(头像/背景/立绘/徽章)就位。

        包体里**不再带图片**：本地有就用本地（开发环境/旧包升级），
        没有就按 ``AssetResolver.ASSET_IDS`` 从素材源下载补齐到缓存
        —— 首次运行由这里自动完成，之后走本地缓存。
        素材拿不到不阻断渲染（主题会优雅降级）。
        """
        try:
            from ...adapters.asset_store import active_store
        except Exception:  # noqa: BLE001 - 独立运行时（测试）没有适配层
            return
        store = active_store()
        if store is None:
            return
        mapping: Dict[str, str] = {}
        for key, asset_id in AssetResolver.ASSET_IDS.items():
            try:
                path = await store.ensure(asset_id)
            except Exception as exc:  # noqa: BLE001
                log.debug("素材 %s 就位失败: %s", asset_id, exc)
                continue
            if path is not None:
                mapping[key] = str(path)
        self.assets.set_paths(mapping)

    async def render(self, doc: HelpDoc, page: Page, theme: str = "light",
                     use_cache: bool = True) -> List[bytes]:
        """渲染一个寻址结果(可能多页)。无浏览器/失败时返回空列表。"""
        self._refresh_layout()
        await self._prepare_assets()
        theme = self._pick_theme(doc, theme)
        payloads = self._build_pages(doc, page)
        out: List[bytes] = []
        for html in payloads:
            png = await self._render_one(html, theme, use_cache)
            if not png:
                return []            # 任一处失败即整体降级, 由调用方兜底
            out.append(png)
        return out

    async def render_custom(self, spec: Dict[str, Any], theme: str = "",
                            use_cache: bool = True) -> List[bytes]:
        """渲染一个"自定义页面"(游戏只给数据: 标题/副标题/版式块/指令表)。

        这是渲染桥接的底座: 游戏永远不写 HTML/CSS, 只描述要展示什么。
        """
        self._refresh_layout()
        await self._prepare_assets()
        theme_name = self._pick_theme(HelpDoc(theme=str(spec.get("theme") or "")), theme)
        png = await self._render_one(self._spec_page(spec), theme_name, use_cache)
        return [png] if png else []

    def build_html(self, doc: HelpDoc, page: Page, theme: str = "light",
                   page_index: int = 0) -> str:
        """公开给测试/预览用: 直接拿到某一页的 HTML。"""
        self._refresh_layout()
        payloads = self._build_pages(doc, page)
        if not payloads:
            return ""
        idx = max(0, min(page_index, len(payloads) - 1))
        return self._wrap(payloads[idx], self._pick_theme(doc, theme))

    # ── 内部: 主题与渲染 ──────────────────────────────────
    @staticmethod
    def _pick_theme(doc: HelpDoc, theme: str) -> str:
        name = (theme or "").strip().lower()
        if name in ("light", "dark"):
            return name
        doc_theme = (getattr(doc, "theme", "") or "").strip().lower()
        return doc_theme if doc_theme in ("light", "dark") else "light"

    async def _render_one(self, body: str, theme: str, use_cache: bool) -> Optional[bytes]:
        html = self._wrap(body, theme)
        if not html:
            return None
        key = hashlib.md5(html.encode("utf-8")).hexdigest()[:20]
        cache_path = os.path.join(self.cache_dir, f"help-{key}.png") if self.cache_dir else ""
        if use_cache and cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, "rb") as fh:
                    data = fh.read()
                if data:
                    return data
            except OSError:
                pass
        render_html = getattr(self.img, "render_html", None)
        if not callable(render_html):
            return None
        png = await render_html(html, "", self.width, 800, selector="body")
        if not png:
            return None
        if cache_path:
            try:
                with open(cache_path, "wb") as fh:
                    fh.write(png)
            except OSError:
                pass
        return png

    # ── 内部: 页面组装 ────────────────────────────────────
    def _wrap(self, body: str, theme: str) -> str:
        css = BASE_CSS % {"width": self.width, "veil": theme_veil(theme)}
        if self.narrow:
            css += NARROW_CSS
        bg = self.assets.uri("bg")
        mascot = self.assets.uri("mascot")
        bg_layer = (f'<div class="bgart" style="background-image:url({bg})"></div>'
                    if bg else "")
        mascot_tag = f'<img class="mascot" src="{mascot}" alt="">' if mascot else ""
        return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                f'<style>:root {{{theme_tokens(theme)}}}\n{css}</style></head><body>'
                f'{bg_layer}<div class="veil"></div><div class="page">{body}</div>'
                f'{mascot_tag}</body></html>')

    def _icon(self, name: str) -> str:
        return _esc(icon_glyph(name, self._icon_override))

    def _head(self, title: str, subtitle: str = "", role: str = "") -> str:
        avatar = self.assets.uri("avatar")
        ava = f'<div class="ava"><img src="{avatar}" alt=""></div>' if avatar else ""
        role_tag = f'<div class="roleTag">🐾 {_esc(role)}</div>' if role else ""
        sub = f'<div class="page-sub">{_esc(subtitle)}</div>' if subtitle else ""
        return (f'<div class="head">{ava}<div><div class="page-title">{_esc(title)}</div>'
                f'{sub}{role_tag}</div></div>')

    def _foot(self) -> str:
        brand = self.assets.uri("brand")
        img = f'<img src="{brand}" alt="">' if brand else ""
        return (f'<div class="divider"></div><div class="foot">{img}'
                f'N.E.K.O 猫娘小游戏 · 帮助</div>')

    def _tip(self, body: str) -> str:
        avatar = self.assets.uri("avatar")
        img = f'<img src="{avatar}" alt="">' if avatar else ""
        return f'<div class="tip">{img}<span>{body}</span></div>'

    def _chips(self, items: Sequence[str], limit: int = 3, total: int = 0) -> str:
        shown = list(items[:limit])
        html = "".join(f'<span class="chip">{_esc(i)}</span>' for i in shown)
        extra = (total or len(items)) - len(shown)
        if extra > 0:
            html += f'<span class="chip more">+{extra}</span>'
        return f'<div class="chips">{html}</div>'

    def _table(self, rows: Sequence[Sequence[str]],
               headers: Sequence[str] = ("指令", "说明")) -> str:
        head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
        body = "".join(
            f'<tr><td class="k">{_esc(r[0])}</td><td class="d">{_esc(r[1])}</td></tr>'
            for r in rows if len(r) >= 2)
        return f'<table class="table"><tr>{head}</tr>{body}</table>'

    def _board(self, block: Dict[str, Any]) -> str:
        """棋盘块: 行列数据 + 坐标标签 + 标记(最后一手/星位)。

        rows:     [[单元格, ...], ...]；单元格 = "" | "汉字/符号" | {"g":.., "c":..}
        placement: cross(交叉点, 围棋/五子棋/象棋) | cell(格心, 黑白棋/国际象棋)
        col_labels/row_labels: 坐标(A..O / 1..15), 给了就在左侧/上方画格子外的标签
        marks:    {"r,c": "last" | "star"}
        """
        rows = [list(x) for x in (block.get("rows") or []) if isinstance(x, (list, tuple))]
        if not rows:
            return ""
        cols = max(len(x) for x in rows)
        placement = str(block.get("placement") or "cross").lower()
        col_labels = [str(x) for x in (block.get("col_labels") or [])]
        row_labels = [str(x) for x in (block.get("row_labels") or [])]
        marks = block.get("marks") if isinstance(block.get("marks"), dict) else {}
        has_left, has_top = bool(row_labels), bool(col_labels)
        grid_cols = cols + (1 if has_left else 0)
        out: List[str] = []
        if has_top:
            if has_left:
                out.append('<div class="blab"></div>')
            for c in range(cols):
                lab = col_labels[c] if c < len(col_labels) else ""
                out.append(f'<div class="blab">{_esc(lab)}</div>')
        for ri, row in enumerate(rows):
            if has_left:
                lab = row_labels[ri] if ri < len(row_labels) else ""
                out.append(f'<div class="blab">{_esc(lab)}</div>')
            for ci in range(cols):
                raw = row[ci] if ci < len(row) else ""
                glyph, colour = "", ""
                if isinstance(raw, dict):
                    glyph = str(raw.get("g") or "")
                    colour = str(raw.get("c") or "")
                elif raw:
                    glyph = str(raw)
                cls = ["bcell"]
                if placement != "cross":
                    cls.append("box")
                else:
                    if ci == 0:
                        cls.append("el")
                    if ci == cols - 1:
                        cls.append("er")
                    if ri == 0:
                        cls.append("et")
                    if ri == len(rows) - 1:
                        cls.append("eb")
                mark = str(marks.get(f"{ri},{ci}") or "")
                inner = ""
                if placement == "cross":
                    inner = '<span class="ln h"></span><span class="ln v"></span>'
                if glyph:
                    pcls = {"white": "pc-w", "red": "pc-r"}.get(colour, "pc-b")
                    if mark == "last":
                        pcls += " last"
                    inner += f'<span class="pc {pcls}">{_esc(glyph)}</span>'
                elif mark == "star":
                    inner += '<span class="star"></span>'
                out.append(f'<div class="{" ".join(cls)}">{inner}</div>')
        style = f"grid-template-columns:repeat({grid_cols},1fr)"
        return f'<div class="board" style="{style}">{"".join(out)}</div>'

    def _block(self, block: Dict[str, Any]) -> str:
        """版式块: chips / table / steps / flow / slots / bag / text。"""
        kind = str(block.get("type") or "text").lower()
        title = str(block.get("title") or "")
        head = f'<div class="block-tt">{_esc(title)}</div>' if title else ""
        items = block.get("items") or []
        if kind in ("chips", "list"):
            names = [str(i.get("name") if isinstance(i, dict) else i) for i in items]
            return head + self._chips(names, limit=block.get("limit") or 12)
        if kind == "table":
            headers = block.get("headers") or ["指令", "说明"]
            return head + self._table(block.get("rows") or [], headers)
        if kind in ("steps", "flow"):
            parts = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                if i:
                    parts.append('<span class="arrow">→</span>')
                parts.append(
                    '<div class="step"><span class="emoji">%s</span>'
                    '<span class="s1">%s</span><span class="s2">%s</span></div>'
                    % (self._icon(str(it.get("icon") or "")), _esc(it.get("name") or ""),
                       _esc(it.get("sub") or "")))
            return head + f'<div class="steps">{"".join(parts)}</div>'
        if kind == "slots":
            parts = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                parts.append(
                    '<div class="slot"><span class="emoji">%s</span>'
                    '<span class="sn">%s</span><span class="sd">%s</span></div>'
                    % (self._icon(str(it.get("icon") or "")), _esc(it.get("name") or ""),
                       _esc(it.get("sub") or "")))
            return head + f'<div class="slots">{"".join(parts)}</div>'
        if kind == "bag":
            cols = int(block.get("cols") or 8)
            filled = set(block.get("filled") or [])
            icons = block.get("icons") or []
            cells = []
            for i in range(int(block.get("cells") or (cols * 2))):
                glyph = ""
                if i < len(icons) and icons[i]:
                    glyph = f'<span class="emoji">{self._icon(str(icons[i]))}</span>'
                cells.append('<div class="cell%s">%s</div>'
                             % (" on" if i in filled else "", glyph))
            return (head + f'<div class="bag" style="grid-template-columns:repeat({cols},1fr)">'
                    f'{"".join(cells)}</div>')
        if kind == "board":
            return head + self._board(block)
        if kind == "cards":
            cols = max(1, min(int(block.get("cols") or 3), 4))
            items_html = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                desc = str(it.get("desc") or "")
                items_html.append(
                    '<div class="mini"><span class="mini-ico emoji">%s</span>'
                    '<span class="mini-nm">%s</span>'
                    '%s</div>'
                    % (self._icon(str(it.get("icon") or "")), _esc(it.get("name") or ""),
                       f'<span class="mini-ds">{_esc(desc)}</span>' if desc else ""))
            return (head + f'<div class="cardgrid" style="grid-template-columns:'
                    f'repeat({cols},1fr)">{"".join(items_html)}</div>')
        if kind == "stats":
            rows_html = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                value = it.get("value", 0)
                top = float(it.get("max") or 10) or 10.0
                try:
                    pct = max(0.0, min(100.0, float(value) / top * 100.0))
                except (TypeError, ValueError):
                    pct = 0.0
                note = str(it.get("note") or "")
                note_html = f'<span class="statnt">{_esc(note)}</span>' if note else ""
                rows_html.append(
                    '<div class="statrow">'
                    f'<span class="mini-ico emoji">{self._icon(str(it.get("icon") or ""))}</span>'
                    f'<span class="statlb">{_esc(it.get("label") or "")}</span>'
                    f'<span class="bar"><i style="width:{pct:.0f}%"></i></span>'
                    f'<span class="statvl">{_esc(value)}</span>'
                    f'{note_html}'
                    '</div>')
            return head + f'<div class="stats2">{"".join(rows_html)}</div>'
        return head + f'<div class="page-sub">{_esc(block.get("text") or "")}</div>'

    # ── 页面类型 ─────────────────────────────────────────
    def _group_card(self, group: Group, index: int, cols: int = 2) -> str:
        """分组卡: 组名 + 条数 + 代表指令(3 条 + "+N"); 有子分组时在卡内展开。"""
        own = [c.cmd for c in group.commands]
        total = len(group.all_commands())
        tone = ' data-tone="info"' if index % 2 else ""
        chips = self._chips(own, limit=3 if cols == 2 else 6, total=len(own)) if own else ""
        subs = ""
        if group.groups:
            rows = "".join(
                f'<div class="subrow"><span class="subico emoji">{self._icon(s.icon)}</span>'
                f'<span class="subnm">{_esc(s.name)}</span>'
                f'<span class="subn">{len(s.all_commands())}</span></div>'
                for s in group.groups)
            subs = f'<div class="subrows">{rows}</div>'
        return (f'<div class="card"><div class="card-hd"><span class="emoji">'
                f'{self._icon(group.icon)}</span><span class="card-tt">{_esc(group.name)}'
                f'</span><span class="badge"{tone}>{total} 条</span></div>'
                f'<div class="card-bd">{chips}{subs}</div></div>')

    def _estimate_card(self, group: Group, cols: int = 2) -> int:
        """预估一张分组卡高度(px): 用于按比例分页(避免半空页/挤压)。"""
        per_row = 2 if self.narrow else (3 if cols == 2 else 6)
        n = min(len(group.commands), per_row)
        chip_rows = max(1, (n + per_row - 1) // per_row) if group.commands else 0
        return 62 + chip_rows * 30 + len(group.groups) * 26

    def _pack_catalog(self, groups: Sequence[Group], cols: int,
                      budget: int) -> List[List[Group]]:
        """按预估高度把分组打包成页: 两列成行, 每页不超过高度预算。"""
        pages: List[List[Group]] = []
        cur: List[Group] = []
        used = 0
        for g in groups:
            h = self._estimate_card(g, cols)
            row_h = h
            if cur and len(cur) % cols != 0:              # 与本行已有卡作伴
                prev_h = self._estimate_card(cur[-1], cols)
                row_h = max(h, prev_h) - prev_h           # 只补差额
            if cur and used + row_h > budget:
                pages.append(cur)
                cur, used, row_h = [], 0, h
            cur.append(g)
            used += row_h
        if cur:
            pages.append(cur)
        return pages or [[]]

    def _catalog(self, doc: HelpDoc, page: Page) -> List[str]:
        """一级帮助(目录): 只列功能分组 + 条数 + 代表指令, **一页给全**。

        ⚠️ 指令分级: 目录页不下钻指令明细 —— 想看某一类发「<游戏>帮助 <分组名>」。
        11 个分组也能收在一页(分组特别多才分页, 每页 14 组)。
        """
        groups = doc.groups
        per_page = 14
        chunks = [groups[i:i + per_page]
                  for i in range(0, len(groups), per_page)] or [groups]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            rows = []
            for g in chunk:
                own = [c.cmd for c in g.commands]
                chips = self._chips(own, limit=2, total=len(own)) if own else ""
                rows.append(
                    f'<div class="dirrow"><div class="dirtop">'
                    f'<span class="diri emoji">{self._icon(g.icon)}</span>'
                    f'<span class="dirn">{_esc(g.name)}</span>'
                    f'<span class="badge" style="margin-left:auto">'
                    f'{len(g.all_commands())} 条</span></div>'
                    + (f'<div class="dirc">{chips}</div>' if chips else "")
                    + '</div>')
            subtitle = doc.subtitle or (doc.text and doc.text[:60]) or ""
            if len(chunks) > 1:
                subtitle = f"{subtitle} （{pi + 1}/{len(chunks)}）".strip()
            gname = doc.title or doc.game_name
            sample = groups[0].name if groups else "功能名"
            tip_body = (f'想看某一类的详细指令 → 发「<b>{_esc(gname)}帮助 功能名</b>」，'
                        f'例如「{_esc(gname)}帮助 {_esc(sample)}」。')
            if page.miss and pi == 0:
                guess = "、".join(page.suggestions) if page.suggestions else "上面的功能分组"
                tip_body = f'没找到「{_esc(getattr(page, "topic", "") or "")}」这个分类喵，' \
                           f'你是想看 <b>{_esc(guess)}</b> 吗？'
            body = (self._head(doc.title or doc.game_name, subtitle, "喵喵陪你玩")
                    + f'<div class="stats"><div class="stat"><div class="l">功能分组</div>'
                      f'<div class="v">{len(groups)}</div></div>'
                      f'<div class="stat"><div class="l">指令总数</div>'
                      f'<div class="v">{doc.command_count}</div></div></div>'
                    + self._tip(tip_body)
                    + f'<div class="card"><div class="card-bd" style="padding:0">'
                      f'{"".join(rows)}</div></div>'
                    + self._foot())
            out.append(body)
        return out

    def _group_page(self, doc: HelpDoc, page: Page) -> List[str]:
        group = page.group
        if group is None:
            return self._catalog(doc, page)
        blocks = "".join(f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
                         for b in group.blocks)
        # 子分组: 在父页里各自成卡片(二级内容不丢)
        for sub in group.groups:
            sub_blocks = "".join(
                f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
                for b in sub.blocks)
            sub_rows = self._table([[c.cmd, c.desc] for c in sub.commands])
            blocks += (f'<div class="card"><div class="card-hd"><span class="emoji">'
                       f'{self._icon(sub.icon)}</span><span class="card-tt">'
                       f'{_esc(sub.name)}</span><span class="badge">'
                       f'{len(sub.commands)} 条</span></div>'
                       f'<div class="card-bd">{sub_rows}</div></div>{sub_blocks}')
        cmds = group.commands
        chunks = [cmds[i:i + self.group_cmds]
                  for i in range(0, len(cmds), self.group_cmds)] or [cmds]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            subtitle = doc.title or doc.game_name
            if len(chunks) > 1:
                subtitle += f" · {pi + 1}/{len(chunks)}"
            rows = []
            for c in chunk:
                desc = c.desc or ""
                if c.aliases:
                    desc += f"（别名: {'/'.join(c.aliases[:3])}）"
                rows.append([c.cmd, desc])
            others = [g.name for g in doc.groups if g.id != group.id][:6]
            tail = (self._tip("其他功能：" + "、".join(f"<b>{_esc(o)}</b>" for o in others)
                              + "　→　发「帮助 功能名」") if others else "")
            head = (self._head(group.name, subtitle, "喵喵陪你玩") if pi == 0
                    else self._head(f"{group.name}（续）", subtitle))
            body = (head + (blocks if pi == 0 else "")
                    + f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                    + tail + self._foot())
            out.append(body)
        return out

    def _command_page(self, doc: HelpDoc, page: Page) -> List[str]:
        """指令页: 声明了专属版式块的指令(如「我的纳戒」)→ 单独出一张专属图。

        「我的纳戒」「我的背包」这类查看指令, 用户要看的是一张**自己的图**
        (背包格/装备栏), 而不是分组页里的一行说明。
        """
        cmd = page.command
        if cmd is None:
            return self._catalog(doc, page)
        group = page.group
        usage = cmd.cmd + (" " + " ".join(cmd.params) if cmd.params else "")
        rows: List[Sequence[str]] = [["用法", usage]]
        if cmd.desc:
            rows.append(["说明", cmd.desc])
        if cmd.params:
            rows.append(["参数", " / ".join(cmd.params)])
        if group is not None:
            rows.append(["所属", group.name])
        related: List[str] = []
        if group is not None:
            related = [c.cmd for c in group.commands if c.cmd != cmd.cmd]
        related = list(dict.fromkeys([*cmd.related, *related]))
        own_blocks = "".join(
            f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
            for b in cmd.blocks)
        tip = (f'直接发「<b>{_esc(cmd.cmd)}</b>」就能用喵'
               + ("；换装备名即可，例如「装备 青锋剑」" if cmd.params else ""))
        body = (self._head(cmd.cmd, (group.name if group else "") or (doc.title or ""),
                           "喵喵陪你玩")
                + own_blocks
                + f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                + (f'<div class="card"><div class="card-bd">'
                   f'<div class="block-tt">同一分类的其他指令</div>'
                   f'{self._chips(related, limit=8)}</div></div>' if related else "")
                + self._tip(tip) + self._foot())
        return [body]

    def _flat(self, doc: HelpDoc, page: Page) -> List[str]:
        cmds = doc.flat
        chunks = [cmds[i:i + self.flat_cmds]
                  for i in range(0, len(cmds), self.flat_cmds)] or [cmds]
        out: List[str] = []
        for pi, chunk in enumerate(chunks):
            subtitle = (doc.text or "")[:78]
            if len(chunks) > 1:
                subtitle = f"{subtitle} （{pi + 1}/{len(chunks)}）".strip()
            body = (self._head(doc.title or doc.game_name, subtitle, "喵喵陪你玩")
                    + f'<div class="card"><div class="card-bd">'
                      f'{self._table([[c.cmd, c.desc] for c in chunk])}</div></div>'
                    + self._foot())
            out.append(body)
        return out

    def _build_pages(self, doc: HelpDoc, page: Page) -> List[str]:
        if doc.groups:
            if page.kind == "group":
                return self._group_page(doc, page)
            if page.kind == "command":
                return self._command_page(doc, page)
            return self._catalog(doc, page)
        return self._flat(doc, page)

    def _spec_page(self, spec: Dict[str, Any]) -> str:
        """自定义页面骨架: 标题 + 版式块 + 指令表(全部由游戏给的数据驱动)。"""
        blocks = "".join(
            f'<div class="card"><div class="card-bd">{self._block(b)}</div></div>'
            for b in (spec.get("blocks") or []) if isinstance(b, dict))
        rows = [r for r in (spec.get("rows") or []) if isinstance(r, (list, tuple))
                and len(r) >= 2]
        parsed = [c for c in (_as_command(x) for x in (spec.get("commands") or []))
                  if c is not None]
        cmds = (f'<div class="card"><div class="card-bd">'
                f'{self._table([[c.cmd, c.desc] for c in parsed])}</div></div>'
                if parsed else "")
        table = (f'<div class="card"><div class="card-bd">{self._table(rows)}</div></div>'
                 if rows else "")
        chips = spec.get("chips")
        chip_card = (f'<div class="card"><div class="card-bd">'
                     f'{self._chips([str(c) for c in chips], limit=len(chips))}</div></div>'
                     if chips else "")
        tip = self._tip(str(spec["tip"])) if spec.get("tip") else ""
        return (self._head(str(spec.get("title") or ""), str(spec.get("subtitle") or ""),
                           str(spec.get("role") or ""))
                + blocks + table + cmds + chip_card + tip + self._foot())


def _as_command(raw: Any) -> Optional[Any]:
    """把桥接传入的指令数据(二元组或对象)转成内部 Command。"""
    from .contract import _parse_command  # 局部导入避免循环依赖
    return _parse_command(raw)
