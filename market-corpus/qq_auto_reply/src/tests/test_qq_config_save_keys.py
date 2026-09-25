"""界面提交的设置键必须全在 `_CONFIG_SAVE_KEYS` 白名单里。

这是"点了保存没效果"的成因那一族：`save` 那条链是**三层逐个具名透传**
（入口白名单 → dashboard_service 具名参数 → settings_service 逐个 kwargs.get），
**任何一层没点名，那个键就被静默丢掉** —— 界面照常弹「设置已保存」，
但值刷新后弹回原值，看起来就是"保存没生效"。

`enable_group_attention` 就这么丢过：界面一直在发，三层都没接。
"""
from __future__ import annotations

import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[1]


def _allowlist() -> set[str]:
    src = (BASE / "__init__.py").read_text(encoding="utf-8")
    i = src.index("_CONFIG_SAVE_KEYS = frozenset({")
    return set(re.findall(r'"([a-z_0-9]+)"', src[i:src.index("})", i)]))


def _ui_save_keys(html_name: str) -> set[str]:
    """从 `let args={...}` 里抠出顶层键名。"""
    html = (BASE / "static" / html_name).read_text(encoding="utf-8")
    m = re.search(r"let args=\{(.*?)\};", html, re.S)
    assert m, f"{html_name}: 找不到 args 字面量（界面改写法了？）"
    return set(re.findall(r"(?:^|,)\s*([a-z_][a-z_0-9]*)\s*:", m.group(1)))


def test_napcat_save_form_sends_only_allowed_keys():
    """napcat.html 的「保存设置」提交 31 个键 —— 一个都不能落在白名单外。"""
    outside = sorted(_ui_save_keys("napcat.html") - _allowlist())
    assert not outside, (
        f"这些键会被静默丢弃（三层具名透传没接）：{outside}。"
        f"补 `_CONFIG_SAVE_KEYS` + dashboard_service.save_settings 的签名与转发。"
    )


def test_allowlist_matches_the_service_signature():
    """白名单里也不能有服务层不认的键 —— 那种会在服务层抛 TypeError。"""
    import inspect

    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService

    params = set(inspect.signature(QQDashboardService.save_settings).parameters)
    params.discard("self")
    unknown = sorted(_allowlist() - params)
    assert not unknown, f"白名单里有服务层不认的键：{unknown}"


def test_the_attention_switch_is_wired_end_to_end():
    """这条是上面两个通用守卫的**具体案例** —— 它曾经真的断过。"""
    assert "enable_group_attention" in _allowlist()
    assert "enable_group_attention" in _ui_save_keys("napcat.html")

    import inspect

    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    assert "enable_group_attention" in inspect.signature(
        QQDashboardService.save_settings).parameters
    # 最后一层：真的写进设置
    assert '"enable_group_attention"' in inspect.getsource(
        QQSettingsService._save_settings_locked)


def test_every_allowlisted_key_is_actually_written():
    """**第三层守卫**：白名单里有、而 `settings_service` 从不写的键会被静默丢掉。

    `save` 是三层具名透传，前两条守卫只盯住了前两层（界面 → 白名单，
    白名单 → dashboard 签名）。最里层这一跳没有守卫就只能靠人肉比对 ——
    `enable_group_attention` 当初断的就是这里。
    """
    import inspect

    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    src = inspect.getsource(QQSettingsService._save_settings_locked)
    missing = sorted(k for k in _allowlist() if f'"{k}"' not in src)
    assert not missing, (
        f"白名单里有、但 settings_service 从不写进设置：{missing}。"
        f"界面会显示「已保存」，值却存不下来。"
    )
