"""界面提交的设置键必须全在 `_CONFIG_SAVE_KEYS` 白名单里。

这是"点了保存没效果"的成因那一族：`save` 那条链是**三层逐个具名透传**
（入口白名单 → dashboard_service 具名参数 → settings_service 逐个 kwargs.get），
**任何一层没点名，那个键就被静默丢掉** —— 界面照常弹「设置已保存」，
但值刷新后弹回原值，看起来就是"保存没生效"。

`enable_group_attention` 就这么丢过：界面一直在发，三层都没接。（那个键后来被整个
删掉了 —— 它是假旋钮，见 settings_schema 的删除说明；但当年这条经验促成了下面这些
守卫，它们与具体某个键无关，仍然有效。）

===== 归并到 settings_schema 之后的形状 =====

白名单现在由 ``settings_schema.SAVEABLE_KEYS`` 生成，所以"白名单"和"表"不可能对不上；
第 3 条守卫因此换了标的：它现在盯的是**表里标了 handler 的键，settings_service 里
是不是真有代码管它** —— 那才是"声明了却没人实现"这类静默失效。
"""
from __future__ import annotations

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[1]


def _allowlist() -> set[str]:
    """真实白名单。**从源码正则抠字面量已经不可靠了**（它现在是生成式），直接导入。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    return set(settings_schema.SAVEABLE_KEYS)


def _ui_save_keys(html_name: str) -> set[str]:
    """从 `let args={...}` 里抠出顶层键名。"""
    html = (BASE / "static" / html_name).read_text(encoding="utf-8")
    m = re.search(r"let args=\{(.*?)\};", html, re.S)
    assert m, f"{html_name}: 找不到 args 字面量（界面改写法了？）"
    return set(re.findall(r"(?:^|,)\s*([a-z_][a-z_0-9]*)\s*:", m.group(1)))


def test_napcat_save_form_sends_only_allowed_keys():
    """napcat.html 的「保存设置」提交的键 —— 一个都不能落在白名单外。"""
    outside = sorted(_ui_save_keys("napcat.html") - _allowlist())
    assert not outside, (
        f"这些键会被静默丢弃（三层具名透传没接）：{outside}。"
        f"补 `settings_schema` 里对应 spec 的 saveable=True + 服务层处理。"
    )


def test_allowlist_is_generated_from_the_schema_table():
    """白名单必须**恰好**是表里 saveable 的键（含别名）—— 防手工改回去。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    expected = {
        name
        for spec in settings_schema.SETTINGS
        if spec.saveable
        for name in (spec.key, *spec.aliases)
    }
    assert _allowlist() == expected


def test_every_declared_handler_is_actually_implemented():
    """**第三层守卫**：表里声明了 handler，`settings_service` 里就得真有代码管它。

    归并之后"白名单里的键没被写"已经结构上不可能（通用路径覆盖所有
    ``handler is None`` 的键），剩下能断的是这一种：表里写了个 handler 名字，
    实际没人实现 —— 那个键就会静静地走通用路径，联动/抛错/延迟发布全丢。
    """
    import inspect

    from plugin.plugins.qq_auto_reply import settings_schema
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    src = inspect.getsource(QQSettingsService._save_settings_locked)
    declared = {spec.key: spec.handler for spec in settings_schema.SETTINGS if spec.handler}

    # 有 handler 的键：它的具名块必须在 _save_settings_locked 里（读或写都算）。
    missing = sorted(k for k in declared if f'"{k}"' not in src)
    assert not missing, (
        f"表里标了 handler，但 settings_service 里没有对应代码：{missing}。"
        f"这些键的联动逻辑会被静默跳过。"
    )


def test_plain_keys_are_covered_by_the_generic_path():
    """``handler is None`` 的 saveable 键由通用路径写 —— 它的 kind 必须受支持。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    supported = {"int", "float", "bool", "str", "list", "dict"}
    plain = [s for s in settings_schema.SETTINGS if s.saveable and not s.handler]
    unsupported = sorted(s.key for s in plain if s.kind not in supported)
    assert not unsupported, f"通用路径不认识这些 kind：{unsupported}"
    assert plain, "通用路径没有任何键可管 —— 表结构变了？"


def test_allowlist_matches_the_service_signature():
    """白名单里每个键都必须是 ``QQDashboardService.save_settings`` 的**具名参数**。

    **这条守卫被我拆掉过一次，然后立刻踩了它防的那个坑。** 归并到 settings_schema 时
    我把它换成了"白名单 == 表里 saveable 的键"（自证式，永远为真），于是新增的 15 个键
    进了白名单、却没进 signature —— 界面一点保存就：

        TypeError: QQDashboardService.save_settings() got an unexpected keyword
        argument 'reply_burst_window_seconds'

    教训：这条断言不是冗余的。`_config_save` 用白名单过滤后 `**payload` 转发，
    白名单比签名宽 **一定** 炸；而生成式的白名单让它更容易比签名宽（我加了键、
    没人提醒我加参数）。留着它。
    """
    import inspect

    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService

    params = set(inspect.signature(QQDashboardService.save_settings).parameters) - {"self"}
    unknown = sorted(_allowlist() - params)
    assert not unknown, (
        f"白名单里有 dashboard_service.save_settings 不认的键：{unknown}。"
        f"界面会报 TypeError（unexpected keyword argument）。"
        f"给 save_settings 补具名参数 + 转发，见 settings_schema 加键流程。"
    )


def test_settings_form_is_not_backfilled_while_dirty():
    """有未保存编辑时，状态推送不得回填设置表单。

    `_settingsDirty` 曾经是**只写不读**的：置位与清除都很齐全（文档级 input 监听置位、
    切连接方向置位、进配置页清、保存成功清），但**没有任何地方检查它** —— 那个守卫
    当初只写了一半。后果是每 2 秒一次的状态推送都会走 applyDashboard → applySettings，
    把正在输入的框覆盖回存档值：用户看到"改到一半自己变回去了"。

    这条守卫钉三件事，任何一件丢了都会让 bug 复活：
      1. applySettings 开头有闸；
      2. 标记真的**被读**（不是又变回只写）；
      3. 标记能被置起 —— 否则闸永远不开，等于没有。
    """
    html = (BASE / "static" / "napcat.html").read_text(encoding="utf-8")

    head = html[html.index("function applySettings(s)"):][:400]
    assert "if(_settingsDirty)return" in head, (
        "applySettings 缺少脏标记守卫 —— 未保存的编辑会被状态推送回填覆盖"
    )
    assert html.count("if(_settingsDirty)") >= 1, "脏标记没有被任何地方读取"
    assert "_settingsDirty=!0" in html, "没有任何地方置脏标记，守卫形同虚设"
    assert "_settingsDirty=!1" in html, "没有任何地方清脏标记，守卫会永久生效"
