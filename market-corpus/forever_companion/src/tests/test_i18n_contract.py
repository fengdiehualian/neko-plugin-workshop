# -*- coding: utf-8 -*-
"""面板 i18n 契约门（1.3.0 第九轮）——后端硬编码人类语言串短路前端 t() 的回归防线。

bug 的形状（1.md 主项 #10）：后端在用户可见渠道（面板 toast）多塞一句中文/英文
裸串，前端 `String(r.note || t(...))` 的取值顺序让它永远压过翻译。本轮把契约改为：

1. 面板可达入口（mixins/panel.py、mixins/capabilities.py、mixins/shards.py）的
   `"note"` 字面量与 `Err(SdkError(...))` 只准携带**稳定 ASCII 码**
   （`^[a-z][a-z0-9_]*$`）；动态细节一律进日志，不进用户可见文案。
2. 每个发出的码必须在**全部 8 个 locale** 有对应译文键（末段 == camelCase(码)，
   白名单显式例外）。
3. TSX/TS 里所有字面量 `t("key")` 引用的键必须在全部 8 个 locale 存在——
   否则非中文用户拿到的是中文 defaultValue（1.md「硬前提」条）。
4. 8 个 locale 的键集合必须完全一致（missing/orphan 0）。

豁免（有意为之，不随本门收缩）：
- mixins/mood_actions.py 的 note：那是 @llm_tool 给模型的**行为指令**，消费者是
  LLM 而非 UI toast；
- mixins/debug_entries.py 的 note：debug_mode 专用调试入口，开发者面向、
  仅经 Plugin Manager 入口列表手工触发，不进面板通路；
- 数据字段值（reason / mode / dormant_reason 的取值、diary item 的 note 透传）：
  只约束"note 键 + SdkError 实参"这两个用户可见文案出口，字段值由前端按码分支。
"""

from __future__ import annotations

import ast
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt"]
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 面板可达（ui/*.tsx 的 api.call / ActionButton 会消费其 Err 与 note）的后端文件
PANEL_SURFACES = [
    ROOT / "mixins" / "panel.py",
    ROOT / "mixins" / "capabilities.py",
    ROOT / "mixins" / "shards.py",
]

# 稳定码 → 非 camelCase 末段的既有键（白名单：本轮之前就存在的 code-driven 惯例）
CODE_KEY_ALLOWLIST = {
    # set_capability 的 note 码（1.2.7 既有），panel.tsx 按 r.note === 码 分支，
    # 文案键在 panel.features.reverted
    "reverted_to_default": "panel.features.reverted",
}


def _camel(code: str) -> str:
    return re.sub(r"_+([a-z0-9])", lambda m: m.group(1).upper(), code)


def _load_locale(locale: str) -> dict[str, str]:
    with io.open(ROOT / "i18n" / f"{locale}.json", encoding="utf-8") as f:
        return json.load(f)


def _collect_backend_codes() -> set[str]:
    """AST 扫描面板可达后端文件：note 字面量与 SdkError 实参必须都是稳定码，
    并把所有发出的码收集起来供键同步断言使用。"""
    codes: set[str] = set()
    problems: list[str] = []
    for path in PANEL_SURFACES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            # 1) dict 字面量里的 "note": <常量>
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if not (isinstance(k, ast.Constant) and k.value == "note"):
                        continue
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        if not CODE_RE.match(v.value):
                            problems.append(
                                f"{rel}:{getattr(node, 'lineno', '?')} note 字面量不是稳定码: {v.value!r}"
                            )
                        else:
                            codes.add(v.value)
                    # 非字面量（item.get("note") 等数据透传）不设限
            # 2) SdkError(...) 实参
            if (
                isinstance(node, ast.Call)
                and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "SdkError"
                and node.args
            ):
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if not CODE_RE.match(a.value):
                        problems.append(f"{rel}:{node.lineno} SdkError 字面量不是稳定码: {a.value!r}")
                    else:
                        codes.add(a.value)
                elif isinstance(a, ast.Attribute) and a.attr == "code":
                    # 带码异常（ImageDataUrlError.code）：码在定义处受同一门约束，
                    # 此处放行但要求定义文件扫描覆盖（core/appearance.py）
                    continue
                else:
                    problems.append(
                        f"{rel}:{node.lineno} SdkError 实参必须是稳定码常量或 exc.code，实得 {ast.unparse(a)}"
                    )
    # 带码异常定义处：core/appearance.py 的 ImageDataUrlError 第一实参也是面板可见码
    app = ROOT / "core" / "appearance.py"
    tree = ast.parse(app.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "ImageDataUrlError"
            and node.args
        ):
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if not CODE_RE.match(a.value):
                    problems.append(f"core/appearance.py:{node.lineno} 码不是稳定形: {a.value!r}")
                else:
                    codes.add(a.value)
    assert not problems, "面板可见文案违规（只准稳定码，细节进日志）:\n" + "\n".join(problems)
    return codes


def _tsx_literal_keys() -> set[str]:
    """ui/ 下所有字面量 t("key") 引用（不含模板字符串拼接的动态键）。"""
    keys: set[str] = set()
    pat = re.compile(r'(?<![A-Za-z0-9_$.])t\("([A-Za-z0-9._-]+)"')
    for path in sorted((ROOT / "ui").glob("*.tsx")) + sorted((ROOT / "ui").glob("*.ts")):
        src = path.read_text(encoding="utf-8")
        keys.update(pat.findall(src))
    return keys


# ------------------------------------------------------------------


def test_backend_panel_strings_are_stable_codes() -> None:
    codes = _collect_backend_codes()
    assert codes, "扫描失败：一个码都没抓到（多半是解析规则坏了），门不能空转"


def test_every_backend_code_has_a_locale_key() -> None:
    codes = _collect_backend_codes()
    bundles = {loc: _load_locale(loc) for loc in LOCALES}
    missing: list[str] = []
    for code in sorted(codes):
        want = _camel(code)
        allow = CODE_KEY_ALLOWLIST.get(code)
        if allow is not None:
            hit = {loc: allow for loc in LOCALES if allow in bundles[loc]}
            if len(hit) == len(LOCALES):
                continue
            missing.append(f"{code} -> 白名单键 {allow} 未进全部 locale")
            continue
        for loc, bundle in bundles.items():
            tail = [k for k in bundle if k.rsplit(".", 1)[-1] == want]
            if not tail:
                missing.append(f"{code} -> camelCase {want!r} 缺 {loc}（键面 panel.errors.*）")
    assert not missing, "后端码未同步进八语:\n" + "\n".join(missing[:20])


def test_tsx_referenced_keys_exist_in_all_locales() -> None:
    refs = _tsx_literal_keys()
    assert refs, "扫描失败：ui/ 一个 t() 引用都没抓到"
    bundles = {loc: _load_locale(loc) for loc in LOCALES}
    missing = []
    for key in sorted(refs):
        for loc in LOCALES:
            if key not in bundles[loc]:
                missing.append(f"{key} 缺 {loc}")
    assert not missing, (
        "TSX 引用但 bundle 缺键（非中文用户将吃到中文 defaultValue，1.md 硬前提）:\n"
        + "\n".join(missing[:40])
    )


def test_all_locales_share_identical_keyset() -> None:
    bundles = {loc: _load_locale(loc) for loc in LOCALES}
    base = set(bundles["en"])
    drift: list[str] = []
    for loc, bundle in bundles.items():
        keys = set(bundle)
        if keys != base:
            drift.append(f"{loc}: missing={sorted(base - keys)[:5]} extra={sorted(keys - base)[:5]}")
    assert not drift, "八语键集漂移:\n" + "\n".join(drift)


def test_new_note_codes_not_regressing_to_sentences() -> None:
    """本轮改掉的旧违规不得复活（形状级钉桩：这几句原文一旦回到面板通路即红）。"""
    codes = _collect_backend_codes()
    banned_fragments = ["当前没有生效的情绪动作", "已进入该情绪状态", "没能送到她手上", "已开始写这一篇"]
    for path in PANEL_SURFACES:
        src = path.read_text(encoding="utf-8")
        for frag in banned_fragments:
            assert frag not in src, f"{path.name} 又出现了被 i18n 契约淘汰的裸串: {frag!r}"
    # 同时确认这些语义已改由码承载
    assert "no_active_mood" in codes and "mood_applied" in codes
