"""P8.0：正式化《拟股喵喵》+ 清丽轻科技风 UI 重构（回归防线）。

覆盖六件事：

1. **身份**：内部 ID 改成 ``mock_stock_meow``，中文名《拟股喵喵》，
   界面上不许再出现早期版本沿用下来的旧字眼；
2. **主题**：宿主 CSS 变量在 ``.rrm-root`` 上被重新定义成浅色系，
   涨是薄荷绿、跌是珊瑚粉；
3. **布局**：行情换成**方块卡片网格**、操作台换成**并排胶囊大按钮**、
   家道三档的资金文案；
4. **素材**���``ui/assets/`` 里有群友头像与梗图，面板用**内联 data URI** 渲染；
5. **崩溃防线**：零 ``position`` / ``filter`` / ``opacity`` / ``z-index``，
   渐变只准纵向，卡片与布局容器之间没有 div 包裹层；
6. **不变量**：引擎层与模式层逐字节零改动，轮询与失败重连仍在。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_ASSETS_DIR = _PLUGIN_ROOT / "ui" / "assets"
_TOML = _PLUGIN_ROOT / "plugin.toml"


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """剥掉 ``//`` 与 ``/* */`` 注释。

    面板顶上有一段「事故复盘」注释，里面**故意**写着 ``position:absolute`` /
    ``linear-gradient`` 这些字样 —— 源码扫描必须先把注释剥掉，否则守卫会误报。
    """
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _panel_code() -> str:
    """面板源码**去掉注释与 CSS 字符串**后的 TSX 代码正文。"""
    source = _strip_comments(_panel_text())
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        body, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    body, _, _ = rest.partition("`")
    return body


def test_css_template_string_is_not_truncated() -> None:
    """``RRM_CSS`` 模板字符串**没被反引号截断**。

    真踩过的坑：CSS 写在 ``const RRM_CSS = `...` `` 里，**注释里只要出现
    一个反引号**，模板字符串就当场结束 —— 后面整段 CSS 变成语法错误，
    面板直接白屏。当时是官方 TSX 校验报出来的；这里再加一道本地防线，
    让报错信息一眼能看懂。
    """
    css = _panel_css()
    assert len(css) > 2000, f"CSS 只有 {len(css)} 字符，多半被反引号截断了"
    # 末尾必须是收尾那条 escape 按钮规则
    assert "rrm-escape" in css, "CSS 末尾不是收尾规则，说明被提前截断了"
    for probe in (
        ".rrm-root .neko-action-control",
        ".rrm-root .neko-button.rrm-pill {",
        ".rrm-root .neko-button.rrm-mini {",
        ".rrm-root .neko-button.rrm-pill-buy",
        ".rrm-root .rrm-avatar {",
    ):
        assert probe in css, f"CSS 缺 {probe}（模板字符串被截断了？）"


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- ① 身份


def test_plugin_id_is_mock_stock_meow() -> None:
    """内部 ID 已经是 ``mock_stock_meow``。"""
    toml = _TOML.read_text(encoding="utf-8")
    assert 'id = "mock_stock_meow"' in toml
    assert 'entry = "plugin.plugins.mock_stock_meow:MockStockMeowPlugin"' in toml
    assert "reverse_rich_sim" not in toml


def test_plugin_name_is_meow() -> None:
    """中文名全线换成《拟股喵喵》。"""
    toml = _TOML.read_text(encoding="utf-8")
    assert 'name = "拟股喵喵"' in toml
    assert _i18n()["plugin.name"] == "拟股喵喵"


def test_no_legacy_branding_on_the_ui() -> None:
    """界面文案里不许出现真实金融词，也不许出现夸大收益的承诺式说法。"""
    forbidden = ("稳赚", "保本", "包赚", "一夜暴富", "翻倍收益", "稳赢")
    offenders: list[str] = []
    for path in (
        _PANEL,
        _I18N_FILE,
        _TOML,
        _PLUGIN_ROOT / "community_roster.json",
        _PLUGIN_ROOT / "config.example.toml",
        _PLUGIN_ROOT / "__init__.py",
    ):
        text = path.read_text(encoding="utf-8")
        for word in forbidden:
            if word in text:
                offenders.append(f"{path.name}:{word}")
    assert not offenders, offenders


def test_llm_tools_are_renamed_too() -> None:
    """两个 LLM 工具也跟着改了名（旧的会残留在 main_server 的注册表里）。"""
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert 'name="mock_stock_meow_join"' in source
    assert 'name="mock_stock_meow_status"' in source
    assert "reverse_rich_sim_join" not in source


# ---------------------------------------------------------------- ② 主题


def test_panel_overrides_the_host_theme_variables() -> None:
    """浅色风靠**重新定义宿主 CSS 变量**实现（一次改动全局生效）。"""
    css = _panel_css()
    for token in (
        "--success: #17916f",
        "--danger: #d6486a",
        "--surface: rgba(255, 255, 255, 0.85)",
        "--radius-md: 20px",
    ):
        assert token in css, f"主题变量少了 {token}"
    assert ".rrm-root {" in css


def test_cards_use_the_frosted_treatment() -> None:
    """卡片是半透明白 + 20px 大圆角 + 极柔的蓝影（P9.0 设计图规格）。"""
    css = _panel_css()
    assert "background: rgba(255, 255, 255, 0.85)" in css
    assert "border-radius: 20px" in css
    assert "box-shadow: 0 18px 46px rgba(133, 163, 214, 0.15)" in css
    assert "border: 1px solid rgba(255, 255, 255, 0.6)" in css


def test_trend_colors_are_mint_and_coral() -> None:
    """涨 = 薄荷绿，跌 = 珊瑚粉（canvas 里画不出 CSS 变量，只能写死）。"""
    source = _panel_text()
    assert 'const TREND_UP = "#35BFA0"' in source
    assert 'const TREND_DOWN = "#F2677F"' in source


def test_background_gradient_is_vertical_only() -> None:
    """背景渐变只准**纵向**（0.8.0 的 X 线条是对角渐变）。"""
    css = _panel_css()
    gradients = re.findall(r"([a-z]+)-gradient\(([^)]*)\)", css)
    assert gradients, "P8.0 应当有背景渐变"
    for kind, body in gradients:
        assert kind == "linear", kind
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), angle.group(1)


# ---------------------------------------------------------------- ③ 布局


def test_market_is_a_grid_of_tiles() -> None:
    """行情列表换成了方块卡片网格。"""
    source = _panel_text()
    assert "function StockTile(" in source
    assert "<Columns fluid" in source
    assert "h(StockTile" in source
    # 旧的表格没了
    assert "DataTable<Stock>" not in source


def test_trade_buttons_are_capsules_side_by_side() -> None:
    """一键梭哈 / 一键割肉 是两个并排的胶囊大按钮。"""
    source = _panel_text()
    assert "rrm-pill rrm-pill-buy" in source
    assert "rrm-pill rrm-pill-sell" in source
    css = _panel_css()
    assert ".rrm-root .neko-button.rrm-pill {" in css
    # 各占一半宽
    assert "flex: 1 1 0;" in css
    # 辅助按钮缩小
    assert ".rrm-root .neko-button.rrm-mini {" in css
    assert "rrm-mini" in source


def test_capsule_buttons_have_the_row_to_themselves() -> None:
    """两个大按钮**独占一行** —— 那一行里不许再塞 `Select`。

    真机教训（0.9.0 首轮）：两个胶囊按钮原本和 `Select` 同一行，
    `Select` 抢走几乎全部宽度 ⇒ 大按钮被压成**竖排细条**（中文一字一行），
    「一键割肉」还被卡片裁掉。用户的原话是「**两个并排**、各占一半宽度」。
    """
    code = _panel_code()
    # 一个布局块 = <Inline ...> ... </Inline> 或 <Grid ...> ... </Grid>
    rows = re.findall(r"<(?:Inline|Grid)\b.*?</(?:Inline|Grid)>", code, re.S)
    buy_rows = [row for row in rows if "rrm-pill-buy" in row]
    assert len(buy_rows) == 1, "梭哈胶囊按钮必须只出现在一个布局行里"
    row = buy_rows[0]
    assert "rrm-pill-sell" in row, "梭哈和割肉必须在**同一行**并排"
    # 任何一行都：要么有大按钮、要么有选择器，但不能同时有
    for chunk in rows:
        if "rrm-pill" in chunk:
            assert "<Select" not in chunk, "有胶囊大按钮的行不许混进 Select"
    # 中文不许竖排：按钮文本锁死不换行
    css = _panel_css()
    pill_block = css.split(".rrm-root .neko-button.rrm-pill {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap;" in pill_block, "胶囊按钮必须锁死不换行（否则中文会竖排）"


def test_capsule_buttons_split_the_row_evenly() -> None:
    """两个大按钮**各占一半宽**。

    真机教训（0.9.0 二轮）：改用 `Inline` + `flex: 1 1 0` 还是不均分 ——
    `ActionButton` 会把按钮包进 ``<div class="neko-action-control">``，
    而那个壳在宿主里是 ``inline-grid``（收缩到内容宽），
    ``flex`` 落不到真正的 flex 子项上 ⇒ 梭哈变窄条、割肉占满整行。

    修法：用 **`Grid cols={2}``**（网格列天生等分），并把那个壳改成
    ``grid`` + 里面的按钮 ``width: 100%``。
    """
    code = _panel_code()
    rows = re.findall(r"<(?:Inline|Grid)\b.*?</(?:Inline|Grid)>", code, re.S)
    row = [chunk for chunk in rows if "rrm-pill-buy" in chunk][0]
    assert "<Grid cols={2}" in row, "两个大按钮必须用两列网格（cols={2}）才等分"

    css = _panel_css()
    assert ".rrm-root .neko-action-control {" in css, "必须处理 ActionButton 的外壳"
    shell = css.split(".rrm-root .neko-action-control {", 1)[1].split("}", 1)[0]
    assert "display: grid;" in shell, "外壳要改成 grid（撑满所在列）"
    assert ".rrm-root .neko-action-control > .neko-button.rrm-pill { width: 100%; }" in css


def test_money_labels_follow_the_family_stage() -> None:
    """资金文案随家道三档变：零花钱 / 家底 / 救命钱。"""
    i18n = _i18n()
    assert i18n["visual.assets_rich"] == "零花钱"
    assert i18n["visual.assets_broke"] == "家底"
    assert i18n["visual.assets_dying"] == "救命钱"
    source = _panel_text()
    assert "visual.assets_dying" in source
    assert "visual.cash_dying" in source


def test_no_plain_asset_wording_on_the_ui() -> None:
    """「资金 / 资产」这种后台味儿的词不许再出现在界面上。"""
    i18n = _i18n()
    for key, value in i18n.items():
        if key.startswith(("visual.", "panel.", "market.")):
            assert "总资产" not in value, key
    assert i18n["market.total_asset"] == "身家"


# ---------------------------------------------------------------- ④ 素材


def test_assets_folder_exists_with_avatars_and_memes() -> None:
    """``ui/assets/`` 是真的静态资源目录。"""
    assert _ASSETS_DIR.is_dir()
    pngs = sorted(p.name for p in _ASSETS_DIR.glob("*.png"))
    # P9.0：结算页那张「破产猫」PNG 已删（改成 CSS 拼的空钱包），少一张。
    assert len(pngs) >= 7, pngs
    # P9.0：破产猫那张 PNG 已删（结算页改成 CSS 拼的空钱包），
    # 网贷弹窗那张猫爪图还在。
    assert any("loan" in name for name in pngs)
    assert not any("bankrupt" in name for name in pngs), "黄色土豆那张图不许再回来"
    assert any("loan" in name for name in pngs)
    for alias in ("星辰", "Wehos", "老王", "小明", "熬夜怪", "潜水员"):
        assert ( _ASSETS_DIR / f"avatar-{alias}.png").exists(), alias


def test_assets_are_inlined_as_data_uri() -> None:
    """面板用的是**内联 data URI**（宿主不服务插件目录，相对路径必然 404）。"""
    avatars_ts = (_ASSETS_DIR / "avatars.ts").read_text(encoding="utf-8")
    # P9.0：破产猫那张图删了，data URI 少一条（6 个头像 + 1 张猫爪图）。
    assert avatars_ts.count("data:image/png;base64,") >= 7
    source = _panel_text()
    assert 'from "./assets/avatars"' in source


def test_avatars_ts_avoids_the_unsupported_generic_comma() -> None:
    """``Record<string, X>`` 的逗号会被宿主链接器当成「一个 export 多个声明」。"""
    avatars_ts = (_ASSETS_DIR / "avatars.ts").read_text(encoding="utf-8")
    code = _strip_comments(avatars_ts)
    assert "Record<" not in code
    assert "export const AVATARS: { [alias: string]: AvatarEntry } = {" in code


def test_roster_avatars_are_rendered_next_to_stock_names() -> None:
    """群友头像挂在股票名旁边。"""
    source = _panel_text()
    assert "function RosterAvatar(" in source
    assert "h(RosterAvatar, { alias: stock.roster_alias })" in source
    # 加载失败退回 Emoji，绝不破图
    assert "entry.fallback" in source


def test_loan_modal_uses_an_image_and_settlement_uses_css_art() -> None:
    """网贷弹窗用 ``<img>`` 渲染梗图；结算页改成**纯 CSS 拼的空钱包**。

    P9.0：0.9.1 那张「破产猫」是 PIL 画的黄色土豆，玩家直呼辣眼睛 ——
    这张图和它的素材文件一起废掉了，结算页配图改成 CSS 拼的扁平空钱包。
    """
    source = _panel_text()
    assert "MEOW_LOAN" in source
    assert 'h("img"' in source
    assert "MEOW_BANKRUPT" not in source, "黄色土豆那张图不许再回来"
    assert "BankruptArt" in source, "结算页要改成 CSS 拼的空钱包"
    assert "rrm-wallet" in source


def test_no_tobacco_motifs_anywhere() -> None:
    """**绝对不要**引入任何吸烟元素（规避《尼古喵喵》的审查风险）。"""
    banned = ("🚬", "🚭", "cigarette", "tobacco", "smoke", "烟草", "抽烟", "吸烟", "香烟")
    targets = [
        _PANEL,
        _I18N_FILE,
        _PLUGIN_ROOT / "__init__.py",
        _PLUGIN_ROOT / "community_roster.json",
        _PLUGIN_ROOT / "cards.py",
        _ASSETS_DIR / "avatars.ts",
        _PLUGIN_ROOT / "tools" / "gen_assets.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word.lower() not in text.lower(), f"{path.name} 出现了 {word}"


# ---------------------------------------------------------------- ⑤ 崩溃防线


def test_panel_css_still_has_no_layout_breaking_declarations() -> None:
    """P7 的崩溃防线**一条都没破**（渐变除外，它被精确收窄成纵向）。"""
    css = _panel_css()
    for token in (
        "position: absolute",
        "position: fixed",
        "z-index",
        "filter:",
        "clip-path",
        "inset:",
        "opacity",
        "@keyframes",
        "radial-gradient",
    ):
        assert token not in css, f"注入的 CSS 里不许再出现 {token}"
    # ``content:``（伪元素画东西 = 遮罩层前奏）单独查 —— ``justify-content:``
    # 这类 **flex 属性**里也含 "content" 子串，不能一刀切。
    assert not re.search(r"(?<![\w-])content\s*:", css), "注入的 CSS 里不许再出现 content: 属性"
    for match in re.finditer(r"animation:\s*([^;]+);", css):
        value = match.group(1).strip().replace("!important", "").strip()
        assert value == "none", value


def test_panel_code_has_no_absolute_positioning() -> None:
    """面板代码里也不许出现定位 / zIndex / 动画 / 渐变。"""
    source = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
        "<div className=",
    ):
        assert token not in source, token

    # P14.0：内联的**线性渐变**解禁（档位卡 / 资金水位条要用它）。
    # 但角度仍然锁死：0.8.0 那条 X 线条 = 绝对定位 + **对角**渐变，
    # 定位已经被禁死，这里再禁一次对角 ⇒ 三重保险，X 线条不可能复活。
    for match in re.finditer(r"([a-z]+)-gradient\(([^)]*)\)", source):
        kind, body = match.group(1), match.group(2)
        assert kind == "linear", f"只用 linear-gradient，发现 {kind}-gradient"
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), (
                f"内联渐变只允许纵向 / 横向（0/90/180deg），发现 {angle.group(1)}deg"
            )


def test_tiles_have_no_wrapper_div() -> None:
    """方块卡片是网格容器的**直接**子元素（0.8.0 的第四条根因）。"""
    source = _panel_text()
    assert "<div className=" not in source
    assert 'className="rrm-root"' in source
    assert "<Stack gap={12}>" in source


# ---------------------------------------------------------------- ⑥ 不变量


def test_engine_and_mode_layers_are_untouched_by_p8() -> None:
    """P8 **不许**碰引擎层与模式层（数值与公式零改动）。

    P15.0 起判定从「文件名 diff」升级为「**代码指纹**」：
    注释与 docstring 可以自由改（清理版权字眼需要），
    但任何**可执行代码**的改动都会被抓出来。
    """
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_panel_still_polls_and_resyncs() -> None:
    """3 秒自轮询与失败重连**必须还在**（P7.4 的能力不能丢）。"""
    source = _panel_text()
    assert "const PANEL_POLL_INTERVAL_MS = 3000" in source
    assert "setInterval" in source
    assert "props.api.refresh()" in source
    assert "const onActionError" in source


def test_idle_state_still_offers_join_buttons() -> None:
    """空闲态的开局按钮不能被 P8 的重构弄丢。

    P14.0：按钮变成三档卡（街机 / 命运挑战 / 无尽），命运挑战点开是
    入门 / 进阶 / 地狱 —— 五档一个都不能少。
    """
    source = _panel_text()
    assert 'actionById("join")' in source
    for mode in (
        "classic_arcade",
        "classic_beginner",
        "classic_advanced",
        "classic_hell",
        "infinite",
    ):
        assert f'"{mode}"' in source, f"必须有 {mode} 这一档"
