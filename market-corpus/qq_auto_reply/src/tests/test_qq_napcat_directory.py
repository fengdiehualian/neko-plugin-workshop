"""NapCat 目录的"已配置 / 未配置"语义。

踩过的坑是一个**反馈环**：控制面板把**解析后**的目录（未配置时是 ``str(Path())``
== ``"."``）当作 ``napcat_directory`` 字段暴露出去，而那个字段在界面上是**可编辑
输入框** —— 于是 "." 被填进框里、保存时又当成用户显式配置存了回去。从此一键部署
认定"目录里没有启动器"而拒绝安装，且每开一次配置页保存一次就固化一次。

这里钉两头：读设置时 ``"."`` 与空串同义；暴露给界面的必须是**配置值**而非解析值。
"""
from __future__ import annotations

from pathlib import Path

from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService


def _svc(napcat_directory: str) -> QQNapcatService:
    return QQNapcatService(get_settings=lambda: {"napcat_directory": napcat_directory})


# ── "." 与空串同义 ──────────────────────────────────────────

def test_dot_is_treated_as_unconfigured():
    """``"."`` 是 ``str(Path())`` 的样子，不是一次有意义的显式选择。"""
    for raw in (".", "./", ".\\", "  .  "):
        assert _svc(raw).get_configured_napcat_path() == "", raw


def test_empty_is_unconfigured():
    assert _svc("").get_configured_napcat_path() == ""
    assert _svc("   ").get_configured_napcat_path() == ""


def test_real_path_is_kept_verbatim():
    for raw in (r"D:\NapCat.Shell", "/opt/napcat", r"C:\Users\me\NapCat"):
        assert _svc(raw).get_configured_napcat_path() == raw


def test_unconfigured_setting_never_reports_as_configured():
    """**配置值**必须是空，无论解析结果落在哪。

    ``get_napcat_directory()`` 未配置时会回落到自带目录（``NapCat.Shell`` 存在就
    用它），都没有才返回 ``Path()``（== ``"."``）—— 那个宽容行为是有意的：
    返回空目录会让 ``ensure_napcat_started`` 报一个其实没发生的硬错误。

    所以这条只钉"配置值侧"的语义；解析结果本身按环境而定，不做断言。
    真正的危险是把它**当成配置回填**，那由
    ``test_dashboard_exposes_the_configured_value_not_the_resolved_one`` 守。
    """
    svc = _svc(".")
    assert svc.get_configured_napcat_path() == ""
    assert isinstance(svc.get_napcat_directory(), Path)


# ── 暴露给界面的必须是配置值 ────────────────────────────────

def test_dashboard_exposes_the_configured_value_not_the_resolved_one():
    """控制面板那个字段会被界面回填进输入框、保存时原样提交 ——
    给它解析结果就等于替用户写了一次配置，"." 就是这么进到设置里的。

    这里做源码级断言（而不是跑整个 dashboard：那要铺十几个服务替身）。
    若有人把它改回 `str(napcat_dir)`，这条会红。
    """
    import inspect
    import re

    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService

    src = inspect.getsource(QQDashboardService.build_dashboard_state)
    line = next(ln for ln in src.splitlines() if '"napcat_directory":' in ln)
    assert "get_configured_napcat_path()" in line, (
        f"napcat_directory 字段必须暴露**配置值**，当前是：{line.strip()}")
    assert "str(napcat_dir)" not in line, line.strip()
    # 解析后的位置另起字段，别混用
    assert re.search(r'"napcat_directory_resolved":', src)
