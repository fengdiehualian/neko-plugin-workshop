"""P8.1：体验修复与视觉打磨（回归防线）。

0.9.0 真机截图暴露的五类硬伤，这里一条条钉死：

1. **结算页占位符泄漏**：``t("panel.mode")`` 没传参 ⇒ 玩家直接看到
   字面量「模式：{mode}」；
2. **英文枚举裸露**：破产原因 ``starved``、入场身份 ``quick``、结局
   ``inaction`` 直接怼在界面上；
3. **K 线开局尴尬**：点数不足时最后一根柱子永远是满格，开局就是一根
   顶天立地的绿柱；
4. **操作区太散 / 下拉框横跨整屏**：胶囊按钮要**各占 48% 紧凑并排**，
   标的下拉框要限宽居中；
5. **梗图太小 / 空闲态没料**：破产猫要放大居中，空闲态要有**随机开场嘲讽**。

另外钉死几条红线：引擎层零改动、素材仍然是内联 data URI、
新文案必须进 i18n 词典。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_ASSETS_DIR = _PLUGIN_ROOT / "ui" / "assets"
_TOML = _PLUGIN_ROOT / "plugin.toml"


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
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


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- ① 占位符泄漏


def test_settlement_mode_label_has_no_placeholder() -> None:
    """结算页的「模式」标签**不许**再带 ``{mode}`` 占位符。

    真机事故：结算页那一行渲染出来是「模式: {mode}」—— 因为代码写的是
    ``t("panel.mode")``，**没传参**。``panel.mode`` 这条文案本身是给
    「模式：{mode}」这种**徽标**用的（局中状态条），结算页的模式是
    KeyValue 里的一个**值**，所以标签必须单独给一条不带占位符的。
    """
    code = _panel_code()
    settlement = code.split('state.stage === "finished"', 1)[-1]
    assert 't("panel.mode")' not in settlement, "结算页不许直接用带占位符的 panel.mode"
    assert 't("panel.mode_label")' in settlement, "结算页必须用不带占位符的 panel.mode_label"

    i18n = _i18n()
    assert "panel.mode_label" in i18n, "i18n 里缺 panel.mode_label"
    assert "{" not in i18n["panel.mode_label"], "panel.mode_label 里不许有占位符"


def test_no_i18n_placeholder_can_leak_to_the_screen() -> None:
    """面板里所有 ``t(key, params)`` 的 **params 必须齐全**。

    宿主 ``t()`` 找不到 ``{xxx}`` 对应的参数时会**原样留下占位符**。
    所以凡是文案里带 ``{...}`` 的键，用它的时候必须传参。
    """
    code = _panel_code()
    i18n = _i18n()
    placeholder_keys = {
        key for key, value in i18n.items() if re.search(r"\{[A-Za-z_][\w.-]*\}", value)
    }
    assert placeholder_keys, "i18n 里应该还有带占位符的键"

    for key in sorted(placeholder_keys):
        # 只看面板里出现的那些
        if f'"{key}"' not in code:
            continue
        # 找到每一次使用点，检查后面有没有跟 params
        for match in re.finditer(rf't\(\s*"{re.escape(key)}"\s*(,|\))', code):
            tail = match.group(1)
            assert tail == ",", f"t(\"{key}\") 没传参 ⇒ {key} 的占位符会原样漏到界面上"
        # 有条件的三元写法也要过
        for match in re.finditer(rf't\(\s*"{re.escape(key)}"\s*,', code):
            assert match.start() > 0


# ---------------------------------------------------------------- ② 英文枚举


def test_settlement_enums_are_translated() -> None:
    """结算页不许把**英文枚举**直接显示给玩家。

    0.9.0 真机截图里赫然写着「破产原因: starved」「入场身份: quick」——
    那是后端的内部 key，玩家完全看不懂。现在统一走 i18n 词典。
    """
    code = _panel_code()
    settlement = code.split('state.stage === "finished"', 1)[-1]
    for raw in (
        "report.death_reason ||",
        "report.identity ||",
        "report.ending ||",
        "catgirlResult.death_reason ||",
    ):
        assert raw not in settlement, f"结算页不许直接显示 {raw}（英文枚举要过 i18n）"

    for helper in (
        'enumText(t, "death_reason"',
        'enumText(t, "identity.label"',
        'enumText(t, "ending"',
    ):
        assert helper in settlement, f"结算页必须用 {helper} 翻成中文"


def test_enum_helper_never_shows_the_internal_key() -> None:
    """``enumText`` 翻不到时只能给破折号，绝不能把内部 key 露出去。"""
    code = _panel_code()
    assert "function enumText(" in code
    block = code.split("function enumText(", 1)[1].split("\n}", 1)[0]
    assert "defaultValue" in block, "必须用 defaultValue 探测 key 缺失"
    assert 'return text || "—"' in block, "翻不到时只能给破折号"


def test_enum_i18n_keys_exist() -> None:
    """枚举翻译用的 i18n 键必须齐全（身份 / 破产原因 / 结局）。"""
    i18n = _i18n()
    for key in (
        "identity.label.debt",
        "identity.label.prove",
        "identity.label.quick",
        "death_reason.starved",
        "death_reason.liquidated",
        "death_reason.harvested",
        "ending.inaction",
    ):
        assert key in i18n, f"i18n 缺 {key}"


# ---------------------------------------------------------------- ③ K 线开局


def test_kline_waits_for_enough_points() -> None:
    """K 线点数不足时不画柱子，改成居中提示语。

    0.9.0 真机截图：刚开局只有一根**顶天立地的绿柱**（区间归一化让最后一根
    永远是满格），极其突兀。现在点数少于阈值就不画柱子。
    """
    code = _panel_code()
    assert "const MIN_KLINE_POINTS = 5" in code, "K 线必须有明确的最小点数阈值"
    block = code.split("function Kline(", 1)[1]
    assert "points.length < MIN_KLINE_POINTS" in block, "K 线必须在点数不足时走另一条路"
    assert "warmingText" in block, "K 线必须有 warmingText 提示语"
    # 提示语是**居中**的
    assert 'justifyContent: "center"' in block
    # 面板调用处必须传 warmingText
    assert 'warmingText={t("panel.kline_warming")}' in code
    assert "panel.kline_warming" in _i18n()


def test_kline_stays_the_main_visual() -> None:
    """K 线依然是上半区的主视觉（高度不能被这次修复顺带砍掉）。

    P21.0：高度改成 CSS 表达式（clamp(232px, 34vh, 520px)）——
    跟着 iframe 高度自适应，全屏时自动填满上半部分。
    守卫盯的是**下限**不许退。
    """
    code = _panel_code()
    match = re.search(r"const KLINE_HEIGHT_CSS = \"clamp\((\d+)px", code)
    assert match, "K 线必须有明确的高度"
    assert int(match.group(1)) >= 200, "K 线是主视觉，高度不能低于 200px"


# ---------------------------------------------------------------- ④ 操作区


def test_trade_buttons_are_compact_and_centred() -> None:
    """两个胶囊按钮**各占 48%**、紧凑并排、整排居中。"""
    css = _panel_css()
    assert ".rrm-root .neko-grid.rrm-trade-row {" in css
    block = css.split(".rrm-root .neko-grid.rrm-trade-row {", 1)[1].split("}", 1)[0]
    assert "48%" in block, "两个胶囊按钮必须各占 48% 宽"
    assert "justify-content: center" in block, "整排要居中"
    code = _panel_code()
    assert 'className="rrm-trade-row"' in code
    assert "<Grid cols={2} gap={8}" in code, "胶囊行的间距要收紧到 8"


def test_select_rows_are_width_limited() -> None:
    """标的下拉框不许横跨整屏 —— 限宽（交易卡两个还额外居中）。"""
    css = _panel_css()
    assert ".rrm-root .neko-inline.rrm-select-row" in css
    block = css.split(".rrm-root .neko-inline.rrm-select-row .neko-select {", 1)[1].split("}", 1)[0]
    assert "max-width" in block, "下拉框必须限宽"
    code = _panel_code()
    assert code.count('className="rrm-select-row"') == 2, "买入 / 卖出两个选择器都要限宽"

    # K 线卡的标的框同样不许满屏（它是面板上最显眼的那个满宽下拉框）。
    assert ".rrm-root .neko-inline.rrm-kline-row .neko-select" in css, "K 线标的框也要限宽"
    kline_block = css.split(".rrm-root .neko-inline.rrm-kline-row .neko-select {", 1)[1].split("}", 1)[0]
    assert "max-width" in kline_block, "K 线标的框必须限宽"
    assert 'className="rrm-kline-row"' in code, "K 线那一行必须挂上 rrm-kline-row"


# ---------------------------------------------------------------- ⑤ 梗图与空闲态


def test_bankrupt_cat_is_replaced_by_a_css_wallet() -> None:
    """破产配图改成**纯 CSS 拼的扁平空钱包**（P9.0）。

    0.9.1 那张「破产猫」是 PIL 画的黄色土豆，玩家点名太丑 —— 图和素材文件
    一起废掉了，现在结算页的配图是面板里 CSS 拼出来的钱包。
    """
    code = _panel_code()
    assert "MEOW_BANKRUPT" not in code, "黄色土豆那张图不许再回来"
    assert "function BankruptArt(" in code, "结算页要改成 CSS 拼的空钱包"
    assert "rrm-wallet" in code
    css = _panel_css()
    assert ".rrm-root .rrm-wallet {" in css
    assert ".rrm-root .rrm-wallet-scene {" in css
    assert "justify-content: center" in css, "钱包要居中"


def test_meme_source_images_are_high_resolution() -> None:
    """梗图源 PNG 必须是 320×240（放大显示才不糊）。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - 环境没 PIL 就跳过尺寸断言
        return
    # P9.0：结算页的「破产猫」PNG 已删除（改成 CSS 拼的空钱包），
    # 这里只剩网贷弹窗那张猫爪图。
    for name in ("meow-loan.png",):
        path = _ASSETS_DIR / name
        assert path.exists(), f"缺 {name}"
        assert Image.open(path).size == (320, 240), f"{name} 必须是 320×240"


def test_idle_state_has_a_random_opening_taunt() -> None:
    """空闲态的猫娘要**随机**说一句开场嘲讽（且轮询时不换话术）。"""
    code = _panel_code()
    assert "IDLE_TAUNT_KEYS" in code, "要有开场嘲讽的候选表"
    assert "Math.random()" in code, "开场嘲讽要随机抽"
    # 随机值必须**在挂载时定一次**：用 useState 的惰性初始化，
    # 不能每次渲染都摇（面板每 3 秒轮询一次，否则话术会自己跳）。
    assert "useState<string>(() =>" in code, "随机值必须用 useState 惰性初始化锁住"
    i18n = _i18n()
    taunts = [key for key in i18n if key.startswith("panel.idle_taunt_")]
    assert len(taunts) >= 5, "开场嘲讽至少要有 5 句"
    for key in taunts:
        assert key in code, f"{key} 必须在代码里被引用"


def test_settlement_title_and_buttons_are_mocking() -> None:
    """破产时结算页的标题和按钮要**带嘲讽**。"""
    code = _panel_code()
    assert "panel.settlement_title_dead" in code
    assert "panel.settlement_title_alive" in code
    assert "panel.settlement_again" in code
    assert "panel.death_scene_skip_mock" in code
    i18n = _i18n()
    for key in (
        "panel.settlement_title_dead",
        "panel.settlement_title_alive",
        "panel.settlement_again",
        "panel.death_scene_skip_mock",
    ):
        assert key in i18n, f"i18n 缺 {key}"


# ---------------------------------------------------------------- ⑥ 红线


def test_new_strings_all_live_in_the_i18n_dictionary() -> None:
    """新增的文案必须**全在** i18n 词典里（面板里不许出现裸中文字面量）。"""
    i18n = _i18n()
    for key in (
        "panel.mode_label",
        "panel.mode_classic",
        "panel.mode_infinite",
        "panel.kline_warming",
        "panel.settlement_title_dead",
        "panel.settlement_title_alive",
        "panel.settlement_again",
        "panel.death_scene_skip_mock",
        "ending.inaction",
    ):
        assert key in i18n, f"i18n 缺 {key}"


def test_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层的**代码**零改动。

    P15.0 起判定从「文件名 diff」升级为「**代码指纹**」：
    注释与 docstring 可以自由改（清理版权字眼需要），
    但任何**可执行代码**的改动都会被抓出来。
    """
    from conftest import engine_mode_code_changes

    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_assets_are_still_inline_data_uris() -> None:
    """素材仍然是**内联 base64**，不许写相对路径。"""
    source = (_ASSETS_DIR / "avatars.ts").read_text(encoding="utf-8")
    assert "data:image/png;base64," in source
    # 注释里**故意**写了 ``<img src="./assets/xxx.png">`` 来解释「为什么不用它」，
    # 所以必须先剥注释再查代码。
    code = _strip_comments(source)
    assert 'src="./assets/' not in code, "不许用相对路径引用素材（宿主不服务插件目录）"
    panel = _panel_code()
    assert 'from "./assets/avatars"' in panel, "必须从编译产物里 import"


def test_version_is_0_9_1() -> None:
    """版本号 = 0.10.5。"""
    toml = _TOML.read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in toml
