"""界面文案看门狗：页面上**用户能看见**的 i18n 缺口必须为零。

**为什么要有这条**：本会话我自己就犯过一次 —— 给注意力参数加了 4 个新输入框
（`cfg-att-freq-target-gap` / `-freq-min-mult` / `-freq-max-mult` / `cfg-att-lock-seconds`），
`data-hint` 键却没写进 i18n 包。而 `applyAttentionHints()` 的实现是：

    var txt = t(k, '');
    if (txt) el.setAttribute('data-title', txt);
    else el.removeAttribute('data-title')      // ← 缺键就**把 tooltip 删掉**

于是那 4 个「?」悬停没有任何内容，而且**没有任何测试会红**。缺 `data-i18n` 键时因为
回落是元素原有文本、页面看着照旧，只有英文界面下悄悄变成中文 —— 也是同一个盲区。

这条只钉**用户可见**的三类，不管"没翻译"（那属于翻译缺口，回落到原文仍可读）：

1. `data-hint="K"` 缺键 → tooltip 被静默删除
2. `t('K')` **不带第二参数** → 缺键时页面上直接显示 `ui.xxx.yyy` 这种键名
3. `data-i18n="K"` 且元素**自身没有文本** → 缺键时该处显示空白
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[1]
STATIC = BASE / "static"
I18N = BASE / "i18n"
LOCALES = ("zh-CN", "en")

_PAGES = sorted(p for p in STATIC.glob("*.html")) + sorted(STATIC.glob("*.js"))


def _bundles() -> dict[str, dict]:
    return {
        loc: json.loads((I18N / f"{loc}.json").read_text(encoding="utf-8"))
        for loc in LOCALES
    }


def _hint_keys(text: str) -> set[str]:
    return set(re.findall(r'data-hint="([^"]+)"', text))


def _fallbackless_t_keys(text: str) -> set[str]:
    """只认 `t('键')` 这种**没有第二参数**的调用。

    带 fallback 的 `t('键','默认')` 缺键时显示默认值，不构成可见缺陷 —— 本项目的
    界面代码几乎全部带 fallback，所以这个集合正常情况下就是空的（这也是为什么
    它值得钉住：一旦有人写了不带 fallback 的调用又忘了加键，页面会直接印出键名）。
    """
    keys = set(re.findall(r"\bt\(\s*'([A-Za-z][A-Za-z0-9_.]*)'\s*\)", text))
    keys |= set(re.findall(r'\bt\(\s*"([A-Za-z][A-Za-z0-9_.]*)"\s*\)', text))
    return keys


def _empty_i18n_elements(text: str) -> set[str]:
    """`data-i18n="K"` 且元素自身没有文本 —— 缺键就显示空白。

    用**配对闭合**的写法匹配（非贪婪到同名闭合标签），避免把带嵌套标签的元素
    （例如里面还有 `<b>`）误判成空。
    """
    keys: set[str] = set()
    for m in re.finditer(
        r"<([a-z]+)\b[^>]*\bdata-i18n=\"([^\"]+)\"[^>]*>(.*?)</\1>", text, re.S,
    ):
        if not m.group(3).strip():
            keys.add(m.group(2))
    return keys


def _report() -> list[str]:
    bundles = _bundles()
    problems: list[str] = []
    for path in _PAGES:
        text = path.read_text(encoding="utf-8")
        for loc, bundle in bundles.items():
            for label, keys in (
                ("data-hint 缺键 → tooltip 被静默删除", _hint_keys(text)),
                ("t('键') 无 fallback 缺键 → 页面显示键名", _fallbackless_t_keys(text)),
                ("data-i18n 缺键且元素无文本 → 显示空白", _empty_i18n_elements(text)),
            ):
                for key in sorted(k for k in keys if k not in bundle):
                    problems.append(f"{path.name} [{loc}] {label}: {key}")
    return problems


def test_no_user_visible_i18n_gap():
    problems = _report()
    assert not problems, (
        "页面上用户能看见的 i18n 缺口（缺键的 data-hint / 无 fallback 的 t / 空标签）:\n"
        + "\n".join(f"  ✗ {p}" for p in problems)
    )


def test_the_scanner_actually_scans_something():
    """先证明扫描器在干活 —— 解析失配会让上面那条空过。"""
    napcat = (STATIC / "napcat.html").read_text(encoding="utf-8")
    # 防呆下限（证明正则在工作）。面板在 D 档收缩后注意力控件从 22 降到 7，
    # hint 总数因此下降，故从 20 调到 10。
    assert len(_hint_keys(napcat)) >= 10, "一个 data-hint 都没扫到，解析器失配了"
    # 界面代码几乎全用 `t('键','默认')` 这种带 fallback 的写法，所以"无 fallback"
    # 集合本来就该是空的 —— 用**总调用数**证明正则在工作，而不是拿这个集合卡阈值。
    total_t_calls = len(re.findall(r"\bt\(\s*['\"]", napcat))
    assert total_t_calls >= 100, f"只扫到 {total_t_calls} 个 t() 调用，解析器失配了"
    bundles = _bundles()
    assert len(bundles["zh-CN"]) > 300, "i18n 包没读进来"


def test_fallbackless_scanner_recognises_both_quote_styles():
    assert _fallbackless_t_keys("x=t('ui.a.b')") == {"ui.a.b"}
    assert _fallbackless_t_keys('x=t("ui.a.b")') == {"ui.a.b"}
    assert _fallbackless_t_keys("x=t('ui.a.b','默认')") == set()


def test_the_scanner_catches_a_removed_hint_key():
    """注入一个不存在的 hint 键，扫描器必须报出来（否则这条是空测）。"""
    text = '<div><span class="param-hint" data-hint="ui.attention.zzz_missing.hint">?</span></div>'
    bundles = _bundles()
    missing = [k for k in _hint_keys(text) if k not in bundles["zh-CN"]]
    assert missing == ["ui.attention.zzz_missing.hint"]


def test_the_scanner_ignores_elements_with_fallback_text():
    """带内嵌标签、有文本的元素不能误判成"空标签"。"""
    text = '<div data-i18n="ui.napcat.config.hint">说明里有 <b>加粗</b> 文字</div>'
    assert _empty_i18n_elements(text) == set()


def test_no_unknown_data_i18n_attributes():
    """只认 `data-i18n` 和 `data-i18n-placeholder` —— 写错名字不会报错，也不会有空缺提示。

    **为什么要有这条**：`status.html` 里有 3 个输入框写的是 `data-i18n-ph="ui.status.qq_ph"`
    这种属性。`i18n.js` 只扫 `data-i18n` 和 `data-i18n-placeholder`，**没有任何脚本认
    `data-i18n-ph`** —— 于是那三个 placeholder 永远是硬编码中文（英文界面下也不变），
    而且上面三类检查全都照不到（属性名不对，键压根没进扫描集合）。

    只钉"属性名必须是 i18n.js 支持的"这一件事：键存不存在由上面那条负责。
    """
    known = {"data-i18n", "data-i18n-placeholder"}
    problems: list[str] = []
    for path in _PAGES:
        text = path.read_text(encoding="utf-8")
        for attr in sorted(set(re.findall(r'\b(data-i18n[a-z-]*)="', text))):
            if attr not in known:
                problems.append(f"{path.name}: {attr}（i18n.js 不认这个属性）")
    assert not problems, (
        "页面上有 i18n.js 不认识的 data-i18n-* 属性 —— 那些文案不会被翻译：\n"
        + "\n".join(f"  ✗ {p}" for p in problems)
    )


def test_the_unknown_attribute_scanner_works():
    """先证明扫描器能抓到 —— 注入一个写错的属性名必须被报出来。"""
    assert re.findall(r'\b(data-i18n[a-z-]*)="', '<input data-i18n-ph="k">') == ["data-i18n-ph"]
    assert re.findall(r'\b(data-i18n[a-z-]*)="', '<i data-i18n-placeholder="k">') == [
        "data-i18n-placeholder"
    ]


def test_bundles_have_the_same_key_set():
    """两个语种的键集合必须一致 —— 少一个就是某个语种下静默缺文案。"""
    bundles = _bundles()
    zh, en = set(bundles["zh-CN"]), set(bundles["en"])
    assert zh == en, (
        f"仅 zh-CN 有: {sorted(zh - en)}\n仅 en 有: {sorted(en - zh)}"
    )


def test_no_duplicate_keys_in_bundles():
    """JSON 里重复的键后者胜出，前者会被静默忽略 —— 顺手钉住。"""
    for loc in LOCALES:
        raw = (I18N / f"{loc}.json").read_text(encoding="utf-8")
        keys = re.findall(r'^\s*"([^"]+)"\s*:', raw, re.M)
        # 用 ast 读一遍更可靠：json 解析本身不会报重复键
        data = ast.literal_eval(raw) if raw.strip().startswith("{") else json.loads(raw)
        assert isinstance(data, dict)
        dups = {k: keys.count(k) for k in set(keys) if keys.count(k) > 1}
        assert not dups, f"i18n/{loc}.json 有重复键: {dups}"
