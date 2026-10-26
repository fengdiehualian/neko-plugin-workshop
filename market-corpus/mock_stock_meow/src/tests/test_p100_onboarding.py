"""P10.0 守卫：新手引导（重中之重）+ 节奏（游戏速度 / 自动·手动）+ 视觉微调。

这一版解决的是**上手门槛**与**时间流速**两件事，不是又一次视觉换皮。
所以这里钉死的重点不是"好不好看"，而是三条硬契约：

1. **新手引导必须真的存在，而且真的会自动弹一次**
   —— 官方 ``docs/plugins/`` 里**没有** Onboarding 机制（只有 ``[[plugin.ui.guide]]``
   这个 Markdown 指南面 + 宿主自带的 ``Modal`` / ``Alert``），所以本插件
   **两条都做**：官方 guide 面 + 面板里的三步图文弹窗。

2. **节奏必须是"只改真实时间、不改游戏数值"**
   —— ``game_speed`` 只缩放心跳间隔；每天多少 tick、手续费、斩杀线、
   行情公式一律不动。任何把速度塞进引擎/模式层的改动都必须红。

3. **红线继续有效**
   —— 引擎层与模式层零改动、危险 CSS 继续禁用、i18n 键不许裸奔。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_REPO = _PLUGIN_ROOT / "storage" / "repo.py"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_TOML = _PLUGIN_ROOT / "plugin.toml"
_CONFIG_EXAMPLE = _PLUGIN_ROOT / "config.example.toml"
_GUIDE_MD = _PLUGIN_ROOT / "docs" / "guide.md"


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


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
        # CSS 的**结束**反引号独占一行 —— 用它定位结尾。
        # （分区取第一个反引号的话，CSS 注释里混进来的反引号会被当分隔符吞掉，
        #  守卫反而测不出问题。）
        end = rest.find("\n`\n")
        assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
        source = head + "\n/* <RRM_CSS> */\n" + rest[end + 3 :]
    return source


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


def _method_body(source: str, name: str) -> str:
    """截出某个方法的函数体（到下一个同级定义/注释块为止）。"""
    marker = f"    def {name}("
    assert marker in source, f"源码里找不到 {name}"
    _, _, tail = source.partition(marker)
    kept: list[str] = []
    for line in tail.split("\n")[1:]:
        if line.startswith("    def ") or line.startswith("    @") or line.startswith("    # ---"):
            break
        kept.append(line)
    return "\n".join(kept)


# ══════════════════════════════════════════════════════════════ ① 官方调研结论
#
# 官方文档里**没有** Onboarding / 欢迎语机制；官方的"引导"只有两样东西：
#   * ``[[plugin.ui.guide]]`` —— Markdown 只读指南面；
#   * 宿主自带的 ``Modal`` / ``Alert`` / ``Tip`` 组件。
# 下面这两条把调研结论钉死，防止有人以为宿主有现成机制而白折腾。


def test_official_guide_surface_is_declared() -> None:
    """官方推荐的"引导页"做法：``[[plugin.ui.guide]]`` + Markdown 文件。"""
    toml = _TOML.read_text(encoding="utf-8")
    assert "[[plugin.ui.guide]]" in toml, "缺少官方的 guide 面"
    assert 'entry = "docs/guide.md"' in toml
    assert _GUIDE_MD.exists(), "docs/guide.md 不存在"
    guide = _GUIDE_MD.read_text(encoding="utf-8")
    assert len(guide) > 800, "玩法说明手册太短了"
    for must in ("零花钱", "家底", "棺材本", "生活费", "梭哈", "割肉", "嘴臭"):
        assert must in guide, f"玩法说明手册里没讲「{must}」"


def test_guide_surface_has_read_only_permission() -> None:
    """guide 面是**只读**的（不许在文档面里动手改状态）。"""
    toml = _TOML.read_text(encoding="utf-8")
    block = toml.split("[[plugin.ui.guide]]", 1)[1]
    assert 'permissions = ["state:read"]' in block
    assert "action:call" not in block


# ══════════════════════════════════════════════════════════════ ② 空闲态大按钮


def test_idle_state_has_a_big_guide_button() -> None:
    """空闲态有一个**大号**《玩法说明》按钮（比开局按钮还醒目）。"""
    css = _panel_css()
    assert ".rrm-root .neko-button.rrm-guide-btn" in css, "缺少玩法说明按钮样式"
    block = css.split(".rrm-root .neko-button.rrm-guide-btn", 1)[1].split("}", 1)[0]
    assert "width: 100%" in block, "玩法说明按钮必须占满整行"
    assert "min-height: 62px" in block, "玩法说明按钮必须够大"
    assert "border-radius: 31px" in block, "玩法说明按钮必须是胶囊"
    code = _panel_code()
    assert 'className="rrm-guide-btn"' in code, "面板里没有用到 .rrm-guide-btn"
    assert "rrm-guide-btn-main" in code
    assert "rrm-guide-btn-sub" in code


def test_guide_button_lives_in_the_idle_card() -> None:
    """按钮挂在**空闲态**那块卡里（不是局中才有）。"""
    code = _panel_code()
    idle_marker = 't("panel.idle_modes_hint")'
    assert idle_marker in code, "找不到空闲态"
    tail = code.split(idle_marker, 1)[1]
    # 往后再找 1500 个字符就是空闲态卡片的剩余部分。
    window = tail[:1500]
    assert "rrm-guide-btn" in window, "玩法说明按钮不在空闲态卡片里"


# ══════════════════════════════════════════════════════════════ ③ 三步图文弹窗


def test_guide_modal_uses_host_modal_and_alert() -> None:
    """弹窗用**宿主自带的** ``Modal`` + ``Alert``（官方推荐做法）。"""
    code = _panel_code()
    assert "function GuideModal(" in code
    body = code.split("function GuideModal(", 1)[1].split("export default function Panel", 1)[0]
    assert "Modal," in body or "h(Modal" in body, "GuideModal 没用宿主的 Modal"
    assert "Alert" in body, "第三步的提醒应该用宿主的 Alert"
    assert 'size: "lg"' in body, "三步图文需要大尺寸弹窗"


def test_guide_modal_covers_the_three_required_topics() -> None:
    """三步分别讲：① 三笔钱 + 生活费；② 梭哈/割肉 + 看涨跌；③ 嘴臭劝退。"""
    i18n = _i18n()
    assert "零花钱" in i18n["guide.step1_body"]
    assert "家底" in i18n["guide.step1_body"]
    assert "救命钱" in i18n["guide.step1_body"]
    assert "生活费" in i18n["guide.step1_body"]
    assert "梭哈" in i18n["guide.step2_body"]
    assert "割肉" in i18n["guide.step2_body"]
    assert "涨跌" in i18n["guide.step2_body"]
    assert "嘴臭" in i18n["guide.step3_body"]
    assert "别玩" in i18n["guide.step3_alert"], "第三步必须劝退"


def test_guide_modal_has_three_numbered_steps() -> None:
    """弹窗里正好三步，每步一个编号。

    **P11.1**：三步的骨架换回宿主的原生 ``Steps`` / ``Step`` ——
    编号圆点由宿主 styles.css 画（``.neko-step-index`` 是圆形徽标），
    所以不再有自绘的 ``.rrm-guide-num``。
    """
    code = _panel_code()
    body = code.split("function GuideModal(", 1)[1].split("export default function Panel", 1)[0]
    assert body.count("step(") == 3, "《玩法说明》必须正好三步"
    # 注意：源码里 ``h(`` 与 ``Steps,`` 可能被换行拆开，所以用允许空白的正则，
    # 别写成呆板的字面量（否则改个排版就误报）。
    assert re.search(r"h\(\s*Steps,", body), "必须用宿主的原生 Steps"
    assert re.search(r"h\(\s*Step,", body), "必须用宿主的原生 Step"
    assert "index: index" in body, "每一步都要带编号"


def test_guide_steps_are_visually_distinct() -> None:
    """三步图文不能是三坨一样的灰字。

    **P11.1**：标题与编号圆点交给宿主的原生 ``Steps`` / ``Step``，
    插件这边只保留"正文"与"小字提示"两种排版 —— 两者字号、颜色、字重都不同。
    """
    css = _panel_css()
    for token in (
        ".rrm-guide-step-body",
        ".rrm-guide-step-tip",
    ):
        assert token in css, f"缺少 {token} 样式"
    body = css.split(".rrm-guide-step-body {", 1)[1].split("}", 1)[0]
    tip = css.split(".rrm-guide-step-tip {", 1)[1].split("}", 1)[0]
    assert body != tip, "正文与小字提示不能是同一套排版"
    # 自绘的那套（P11.1 已换成原生组件）不该再留在 CSS 里。
    assert ".rrm-guide-step-title" not in css, "标题交给宿主原生组件"
    assert ".rrm-guide-num" not in css, "编号圆点交给宿主原生组件"


# ══════════════════════════════════════════════════════════════ ④ 首次自动弹出


def test_onboarding_auto_opens_once_and_is_acked() -> None:
    """首次打开面板**自动弹一次**；关掉即算"看过了"。"""
    code = _panel_code()
    assert "onboarding.auto_open" in code, "面板没读 onboarding.auto_open"
    assert "guideAutoTried" in code, "缺少「只弹一次」的守卫"
    assert "setGuideOpen(true)" in code
    assert 'props.api.call("onboarding_ack"' in code, "关掉弹窗后没记已读"


def test_onboarding_state_is_stored_on_the_player_id() -> None:
    """已读状态记在 **player_id** 上（玩家行），外加一个全局只弹一次的标记。"""
    repo = _REPO.read_text(encoding="utf-8")
    assert "onboarding_seen" in repo, "players 表缺 onboarding_seen 列"
    assert "mark_player_onboarding_seen" in repo
    assert "player_onboarding_seen" in repo
    init = _init_text()
    assert '_SETTING_ONBOARDING = "onboarding_ack"' in init
    assert "self._repo.mark_player_onboarding_seen(player_id)" in init


def test_onboarding_block_reports_auto_open() -> None:
    """后端要给出"要不要自动弹"的判断。"""
    init = _init_text()
    assert "def _onboarding_block(" in init
    block = init.split("def _onboarding_block(", 1)[1].split("def ", 1)[0]
    assert '"auto_open"' in block
    assert '"seen"' in block


# ══════════════════════════════════════════════════════════════ ⑤ 节奏：配置层


def test_config_example_exposes_game_speed() -> None:
    """``config.example.toml`` 里暴露 ``game_speed``（玩家可自行改 toml）。"""
    text = _CONFIG_EXAMPLE.read_text(encoding="utf-8")
    assert "[game]" in text, "config.example.toml 缺 [game] 段"
    assert 'game_speed = "normal"' in text
    assert "slow" in text and "fast" in text, "配置注释里要写清三档"


def test_game_speed_has_three_levels() -> None:
    """慢 / 标准 / 快 三档，倍率只作用在**间隔**上。"""
    init = _init_text()
    assert '_GAME_SPEED_ORDER: tuple[str, ...] = ("slow", "normal", "fast")' in init
    assert '"slow": 3.0' in init
    assert '"normal": 1.0' in init
    assert '"fast": 0.5' in init
    block = init.split("def _tick_interval(", 1)[1].split("def ", 1)[0]
    assert "_GAME_SPEED_MULTIPLIER" in block
    assert "tick_interval_seconds" in block or "base_seconds" in block


def test_speed_never_leaks_into_the_engine_or_modes() -> None:
    """**红线**：速度只缩放间隔，绝不进入引擎层/模式层的任何计算。"""
    engine_dir = _PLUGIN_ROOT / "engine"
    modes_dir = _PLUGIN_ROOT / "modes"
    for folder in (engine_dir, modes_dir):
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in ("game_speed", "_GAME_SPEED", "auto_tick", "_SETTING_AUTO"):
                assert token not in text, f"{path.name} 里出现了 {token}（速度不许进引擎/模式层）"


def test_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层**零改动**。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


# ══════════════════════════════════════════════════════════════ ⑥ 节奏：入口与载荷


def test_three_pace_actions_exist() -> None:
    """三个入口：切速度 / 切自动手动 / 手动推进一拍。"""
    init = _init_text()
    for action in ("set_game_speed", "set_auto_tick", "advance_tick"):
        assert f'id="{action}"' in init, f"缺入口 {action}"
        assert f"async def {action}(" in init


def test_advance_tick_only_works_in_manual_mode() -> None:
    """自动模式下**不许**偷偷多走一拍（否则"自动"就是谎话）。"""
    init = _init_text()
    block = init.split("async def advance_tick(", 1)[1].split("async def onboarding_ack(", 1)[0]
    assert "_auto_tick_enabled()" in block
    assert 'advance_tick_requires_manual' in block


def test_pace_payload_is_present_in_every_stage() -> None:
    """空闲 / 局中 / 结算三种载荷里都要带 ``pace``（UI 得一直有得显示）。"""
    init = _init_text()
    assert init.count('"pace": pace') >= 2, "结算页与空闲态都要有 pace"
    assert '"pace": pace or {}' in init, "局中载荷也要有 pace"


def test_onboarding_payload_is_present_in_every_stage() -> None:
    """``onboarding`` 也要三种载荷都有。"""
    init = _init_text()
    assert '"onboarding": onboarding' in init
    assert '"onboarding": onboarding or {}' in init


def test_settings_table_holds_the_pace_preferences() -> None:
    """偏好设置落在插件自己的 ``settings`` 表（不碰宿主配置 ⇒ 不会重启插件）。"""
    repo = _REPO.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS settings" in repo
    assert "def get_setting(" in repo
    assert "def set_setting(" in repo
    init = _init_text()
    assert '_SETTING_GAME_SPEED = "game_speed"' in init
    assert '_SETTING_AUTO_TICK = "auto_tick"' in init


def test_panel_context_hot_path_never_touches_the_database() -> None:
    """**真机教训**：面板上下文在宿主侧只有 5 秒超时。

    宿主数据库每开一次会话都要走一趟 IPC —— 在上下文里多查一次表就能把
    请求拖到超时边缘（真机上已经因此让面板拿到空状态、插件被宿主停掉）。
    所以节奏块与引导块必须**纯内存**，偏好只在启动时**一次**读回。
    """
    init = _init_text()
    pace_block = _method_body(init, "_pace_block")
    assert "await" not in pace_block, "节奏块必须纯内存，不许 await"
    guide_block = _method_body(init, "_onboarding_block")
    assert "await" not in guide_block, "引导块必须纯内存，不许 await"
    # 偏好必须**一次会话**全部读回，而不是一个键一次查询。
    assert "def _ensure_settings_cache(" in init
    assert "get_settings_map" in init
    repo = _REPO.read_text(encoding="utf-8")
    assert "async def get_settings_map(" in repo
    assert "SELECT key, value FROM settings" in repo
    # 启动时预热一次。
    assert "await self._ensure_settings_cache()" in init


def test_manual_mode_stops_the_heartbeat_from_advancing() -> None:
    """手动模式下心跳**不自动推进**（只刷新基线）。"""
    init = _init_text()
    block = init.split("def _due_ticks(", 1)[1].split("def ", 1)[0]
    assert "_auto_tick_enabled()" in block
    assert "self._last_tick_at = now" in block, "手动模式也要刷新基线"


# ══════════════════════════════════════════════════════════════ ⑦ 节奏：面板 UI


def test_pace_control_component_exists() -> None:
    """面板里有一个节奏组件（速度三档 + 自动/手动）。"""
    code = _panel_code()
    assert "function PaceControl(" in code
    assert "panel.pace_title" in code


def test_auto_manual_is_visible_in_the_title_bar() -> None:
    """自动/手动徽标挂在**标题栏**上（一眼就能看见）。"""
    code = _panel_code()
    assert "rrm-pace-badge-auto" in code
    assert "rrm-pace-badge-manual" in code
    assert "panel.pace_auto_badge" in code
    assert "panel.pace_manual_badge" in code
    css = _panel_css()
    assert ".rrm-pace-badge-auto" in css
    assert ".rrm-pace-badge-manual" in css
    # 两种徽标必须**长得不一样**，否则玩家分不出自动还是手动。
    auto = css.split(".rrm-pace-badge-auto {", 1)[1].split("}", 1)[0]
    manual = css.split(".rrm-pace-badge-manual {", 1)[1].split("}", 1)[0]
    assert auto != manual


def test_speed_switch_has_three_tags() -> None:
    """速度切换是三档（慢 / 标准 / 快）。"""
    code = _panel_code()
    assert "panel.pace_speed_slow" in code
    assert "panel.pace_speed_normal" in code
    assert "panel.pace_speed_fast" in code
    assert "rrm-speed-tag-on" in code
    css = _panel_css()
    assert ".rrm-root .neko-button.rrm-speed-tag" in css
    assert ".rrm-root .neko-button.rrm-speed-tag-on" in css


def test_manual_mode_has_a_big_advance_button() -> None:
    """手动模式下才有【推进一拍】这个大按钮。"""
    code = _panel_code()
    assert "panel.pace_advance" in code
    assert "rrm-pace-advance" in code
    css = _panel_css()
    block = css.split(".rrm-root .neko-button.rrm-pace-advance {", 1)[1].split("}", 1)[0]
    assert "min-height: 52px" in block
    assert "border-radius: 26px" in block


# ══════════════════════════════════════════════════════════════ ⑧ i18n


def test_p10_i18n_keys_exist() -> None:
    """P10 新增的文案都要在词典里（不许裸奔）。"""
    i18n = _i18n()
    required = (
        "panel.guide_button",
        "panel.guide_button_sub",
        "guide.modal_title",
        "guide.step1_title",
        "guide.step1_body",
        "guide.step2_title",
        "guide.step2_body",
        "guide.step3_title",
        "guide.step3_body",
        "guide.step3_alert",
        "guide.footer",
        "guide.ack",
        "panel.pace_title",
        "panel.pace_auto_badge",
        "panel.pace_manual_badge",
        "panel.pace_auto_hint",
        "panel.pace_auto_hint_unknown",
        "panel.pace_manual_hint",
        "panel.pace_day_estimate",
        "panel.pace_day_estimate_unknown",
        "panel.pace_speed_slow",
        "panel.pace_speed_normal",
        "panel.pace_speed_fast",
        "panel.pace_switch_auto",
        "panel.pace_switch_manual",
        "panel.pace_advance",
        "panel.pace_to_settle",
        "error.bad_game_speed",
        "error.advance_tick_requires_manual",
        "error.advance_tick_busy",
    )
    missing = [key for key in required if key not in i18n]
    assert not missing, f"i18n 缺这些键：{missing}"


def test_pace_hints_mention_the_interval() -> None:
    """自动模式的提示里要能看到"每几秒一拍"。"""
    i18n = _i18n()
    assert "{seconds}" in i18n["panel.pace_auto_hint"]
    assert "{minutes}" in i18n["panel.pace_day_estimate"]
    assert "{ticks}" in i18n["panel.pace_to_settle"]


def test_panel_survives_an_empty_context() -> None:
    """宿主偶尔返回**空状态**（它自己忙，5 秒预算用光）。

    这时候面板不能闪回"标准 · 每 0 秒一拍"吓人 —— 必须沿用上一次知道的
    节奏，而且**绝不能**显示"0 秒"。
    """
    code = _panel_code()
    assert "paceRef" in code, "缺少节奏兜底缓存"
    assert "lastPace" not in code  # 命名统一用 paceRef
    assert "panel.pace_auto_hint_unknown" in code, "空状态下不该显示'每 0 秒'"
    assert "panel.pace_day_estimate_unknown" in code
    i18n = _i18n()
    assert "{seconds}" not in i18n["panel.pace_auto_hint_unknown"]


# ══════════════════════════════════════════════════════════════ ⑨ 视觉微调 + 红线


def test_shadow_is_soft_and_wide() -> None:
    """卡片阴影更柔和、发散范围更大（视觉任务三）。"""
    css = _panel_css()
    block = css.split(".rrm-root .neko-card {", 1)[1].split("}", 1)[0]
    assert "box-shadow: 0 18px 46px rgba(133, 163, 214, 0.15)" in block


def test_background_is_more_translucent() -> None:
    """浅蓝底色再通透一档（三段渐变：顶部天蓝 → 中段浅蓝 → 近白）。

    P21.0：底色叠成两层（上面一层淡蓝晕），主渐变本身一个字没改。
    """
    css = _panel_css()
    assert (
        "linear-gradient(180deg, #e4efff 0%, #f4faff 48%, #fbfdff 100%)" in css
    ), "底色的主渐变不许被改掉"
    assert "background-size: cover" in css


def test_dangerous_css_is_still_banned() -> None:
    """``position:absolute`` / ``filter`` 等危险写法继续禁用。"""
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
    for match in re.finditer(r"(?<![\w-])content\s*:", css):
        raise AssertionError(f"注入的 CSS 里不许再出现 content: 属性（{match.group(0)}）")


def test_no_dangerous_inline_styles() -> None:
    """代码里也不许出现 ``position:absolute`` / ``zIndex`` 这类写法。"""
    code = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
    ):
        assert token not in code, f"面板代码里不许再出现 {token}"

    # P14.0：内联的**线性渐变**解禁（档位卡 / 资金水位条要用它）。
    # 但角度仍然锁死：0.8.0 那条 X 线条 = 绝对定位 + **对角**渐变，
    # 定位已经被禁死，这里再禁一次对角 ⇒ 三重保险，X 线条不可能复活。
    for match in re.finditer(r"([a-z]+)-gradient\(([^)]*)\)", code):
        kind, body = match.group(1), match.group(2)
        assert kind == "linear", f"只用 linear-gradient，发现 {kind}-gradient"
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), (
                f"内联渐变只允许纵向 / 横向（0/90/180deg），发现 {angle.group(1)}deg"
            )
    assert "<div className=" not in code, "必须用 h(\"div\", { className: ... })"


def test_version_is_0_9_5() -> None:
    """版本号 = 0.10.5。"""
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in _init_text()
