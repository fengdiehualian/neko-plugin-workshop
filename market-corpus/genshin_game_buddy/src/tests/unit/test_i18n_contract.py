"""I 节：i18n 键的双向一致性、占位符契约与命名空间。

核心约定（也写在 ``__init__.py::_t`` 的 docstring 里）：
  * 所有用户可见文案只经 ``self._t("字面量 key", ...)`` 与 ``tr("字面量 key", ...)``；
  * ``self.i18n.t`` 全插件只允许出现一次（在 ``_t`` 内部做变量转发）。

没有这两条，"死键 / 缺键"扫描就不可靠，sts2 那 14 个"声明了没人读"的配置/文案
就会重演。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import pytest
from conftest import PLUGIN_DIR, PLUGIN_MODULES, load_i18n, read_plugin_source
from plugin.sdk.shared.i18n import PluginI18n

PLACEHOLDER_RE = re.compile(r"\{\{?\s*([A-Za-z_][\w.-]*)\s*\}\}?")
KEY_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
ALLOWED_NAMESPACES = {"entries", "tools", "errors", "context", "lookup", "status", "terms", "perception"}

#: ``PluginI18n.t(self, key, *, locale=None, default="", **params)`` 的形参名。
#: 任何叫这些名字的占位符都拿不到实参——``{key}`` 曾经真的踩过这个坑。
RESERVED_PLACEHOLDERS = {"key", "locale", "default"}


@dataclass(frozen=True)
class CallSite:
    module: str
    lineno: int
    form: str
    key: str | None
    kwargs: frozenset[str]
    default: str | None


def _literal_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def collect_call_sites() -> list[CallSite]:
    sites: list[CallSite] = []
    for module in PLUGIN_MODULES:
        tree = ast.parse(read_plugin_source(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "_t":
                form = "_t"
            elif isinstance(func, ast.Name) and func.id == "tr":
                form = "tr"
            elif isinstance(func, ast.Attribute) and func.attr == "t":
                form = "i18n.t"
            else:
                continue
            default = None
            for keyword in node.keywords:
                if keyword.arg == "default":
                    default = _literal_str(keyword.value)
            sites.append(
                CallSite(
                    module=module,
                    lineno=node.lineno,
                    form=form,
                    key=_literal_str(node.args[0]),
                    kwargs=frozenset(kw.arg for kw in node.keywords if kw.arg),
                    default=default,
                )
            )
    return sites


@pytest.fixture(scope="module")
def messages() -> dict[str, str]:
    return load_i18n("zh-CN")


@pytest.fixture(scope="module")
def sites() -> list[CallSite]:
    return collect_call_sites()


# =============================================================================
# I-01 ~ I-03：双向一致性与字面量要求
# =============================================================================


@pytest.mark.static
def test_i18n__no_dead_keys(messages: dict[str, str], sites: list[CallSite]) -> None:
    referenced = {site.key for site in sites if site.form in {"_t", "tr"} and site.key}
    assert set(messages) - referenced == set()


@pytest.mark.static
def test_i18n__no_missing_keys(messages: dict[str, str], sites: list[CallSite]) -> None:
    referenced = {site.key for site in sites if site.form in {"_t", "tr"} and site.key}
    assert referenced - set(messages) == set()


@pytest.mark.static
def test_i18n__key_arguments_are_literals(sites: list[CallSite]) -> None:
    dynamic = [site for site in sites if site.form in {"_t", "tr"} and not site.key]
    assert dynamic == [], dynamic


@pytest.mark.static
def test_i18n__i18n_t_is_the_single_choke_point(sites: list[CallSite]) -> None:
    forwards = [site for site in sites if site.form == "i18n.t"]
    assert len(forwards) == 1, forwards
    assert forwards[0].module == "__init__.py"
    assert forwards[0].key is None  # 转发用的就是变量，这正是允许的唯一一处


# =============================================================================
# I-04 ~ I-06：占位符
# =============================================================================


@pytest.mark.static
@pytest.mark.parametrize("form", ["_t", "tr"])
def test_i18n__placeholders_match_call_kwargs(
    messages: dict[str, str], sites: list[CallSite], form: str
) -> None:
    problems = []
    for site in sites:
        if site.form != form or not site.key:
            continue
        text = messages.get(site.key, site.default or "")
        placeholders = set(PLACEHOLDER_RE.findall(text))
        expected = site.kwargs - {"default"}
        if placeholders != expected:
            problems.append(f"{site.module}:{site.lineno} {site.key} {placeholders} != {expected}")
    assert problems == []


@pytest.mark.static
def test_i18n__no_reserved_placeholder_names(messages: dict[str, str]) -> None:
    offenders = {
        key: sorted(set(PLACEHOLDER_RE.findall(value)) & RESERVED_PLACEHOLDERS)
        for key, value in messages.items()
    }
    offenders = {key: names for key, names in offenders.items() if names}
    assert offenders == {}, offenders


@pytest.mark.static
def test_i18n__rendered_values_have_no_braces(messages: dict[str, str]) -> None:
    for key, value in messages.items():
        params = {name: "X" for name in PLACEHOLDER_RE.findall(value)}
        rendered = PluginI18n({"zh-CN": messages}, default_locale="zh-CN").t(key, **params)
        assert "{" not in rendered and "}" not in rendered, (key, rendered)


@pytest.mark.static
def test_i18n__rendered_values_non_empty(messages: dict[str, str]) -> None:
    engine = PluginI18n({"zh-CN": messages}, default_locale="zh-CN")
    for key, value in messages.items():
        params = {name: "X" for name in PLACEHOLDER_RE.findall(value)}
        rendered = engine.t(key, **params)
        assert rendered.strip() == rendered and rendered, (key, rendered)


@pytest.mark.static
def test_i18n__missing_locale_falls_back_to_declared_default(messages: dict[str, str]) -> None:
    """声明了 ``default_locale = "zh-CN"`` 时，未知 locale 落到中文默认串。

    这是 SDK 的既定契约（``locale_candidates`` = 请求 locale → 其主语言 →
    default_locale → 其主语言 → "en"），**不是**泄露：默认语言是插件自己声明的。
    真正确保的是"不崩、不返回半个字符串、不串到别的 locale 文案"。
    """
    key = next(iter(messages))
    engine = PluginI18n({"zh-CN": messages}, default_locale="zh-CN")
    assert engine.t(key, locale="en-US") == messages[key]
    assert engine.t(key, locale="fr-FR") == messages[key]


@pytest.mark.static
def test_i18n__no_bundle_at_all_returns_key_or_default(messages: dict[str, str]) -> None:
    key = next(iter(messages))
    empty = PluginI18n({}, default_locale="zh-CN")
    assert empty.t(key, locale="en-US", default="FALLBACK") == "FALLBACK"
    assert empty.t(key, locale="en-US") == key


# =============================================================================
# I-07 ~ I-15：命名空间与内容
# =============================================================================


@pytest.mark.static
def test_i18n__namespace_whitelist(messages: dict[str, str]) -> None:
    prefixes = {key.split(".", 1)[0] for key in messages}
    assert prefixes <= ALLOWED_NAMESPACES, prefixes - ALLOWED_NAMESPACES


@pytest.mark.static
def test_i18n__keys_are_flat_dotted(messages: dict[str, str]) -> None:
    for key, value in messages.items():
        assert KEY_RE.match(key), key
        assert isinstance(value, str), key
        assert value.strip(), key


@pytest.mark.static
def test_i18n__context_keys_complete(messages: dict[str, str]) -> None:
    expected = {
        "context.title",
        "context.title_switched",
        "context.tone",
        "context.terms_header",
        "context.term_line",
        "context.no_terms",
        "context.hint",
    }
    assert expected <= set(messages)
    assert len([key for key in messages if key.startswith("context.")]) == 7


@pytest.mark.static
def test_i18n__lookup_keys_complete(messages: dict[str, str]) -> None:
    expected = {
        "lookup.found_header",
        "lookup.entry_line",
        "lookup.slang_label",
        "lookup.avoid_label",
        "lookup.not_found",
        "lookup.no_game",
    }
    assert expected <= set(messages)
    assert len([key for key in messages if key.startswith("lookup.")]) == 6


@pytest.mark.static
def test_i18n__error_keys_complete(messages: dict[str, str]) -> None:
    assert len([key for key in messages if key.startswith("errors.")]) == 8


@pytest.mark.static
def test_i18n__status_keys_complete(messages: dict[str, str]) -> None:
    assert len([key for key in messages if key.startswith("status.")]) == 8


@pytest.mark.static
def test_i18n__terms_keys_complete(messages: dict[str, str]) -> None:
    assert len([key for key in messages if key.startswith("terms.")]) == 3


@pytest.mark.static
def test_i18n__entry_and_tool_keys_complete(messages: dict[str, str]) -> None:
    assert len([key for key in messages if key.startswith("entries.")]) == 13
    assert len([key for key in messages if key.startswith("tools.")]) == 4


@pytest.mark.static
def test_i18n__no_placeholder_like_todo(messages: dict[str, str]) -> None:
    for key, value in messages.items():
        for marker in ("TODO", "FIXME", "待补", "TBD"):
            assert marker not in value, (key, marker)


@pytest.mark.static
def test_i18n__total_key_count(messages: dict[str, str]) -> None:
    assert len(messages) == 52


# =============================================================================
# I-18：default= 与 zh-CN.json 不漂移
# =============================================================================


@pytest.mark.static
def test_i18n__default_args_match_zh_cn_values(
    messages: dict[str, str], sites: list[CallSite]
) -> None:
    drifted = []
    checked = 0
    for site in sites:
        if site.form != "tr" or not site.key or site.default is None:
            continue
        checked += 1
        if messages.get(site.key) != site.default:
            drifted.append(f"{site.module}:{site.lineno} {site.key}")
    assert checked == 17, checked  # 6 个入口 name+description(12) + set_game 参数(1) + 2 个工具(4)
    assert drifted == []


@pytest.mark.static
def test_i18n__locale_dir_matches_manifest() -> None:
    import tomllib

    with (PLUGIN_DIR / "plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    i18n = manifest["plugin"]["i18n"]
    locales_dir = PLUGIN_DIR / i18n["locales_dir"]
    assert locales_dir.is_dir()
    assert (locales_dir / f"{i18n['default_locale']}.json").is_file()
