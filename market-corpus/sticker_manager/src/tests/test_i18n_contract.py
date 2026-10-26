"""i18n 契约门：zh-CN 与 en 键集一致，且覆盖代码里出现的全部 tr() 键。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "i18n"
LOCALES = ["zh-CN", "en"]


def _messages(locale: str) -> dict[str, str]:
    return json.loads((I18N_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def _tr_keys_in_code() -> set[str]:
    # 词边界防误伤：zip 测试里的 writestr 尾巴恰好是 t-r-左括号-引号（真踩过），
    # 前面有字母时不算 tr() 调用。
    pattern = re.compile(r'(?<![A-Za-z0-9_])tr\(\s*"([^"]+)"')
    keys: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts or ".tmpgate" in path.parts:
            continue
        keys.update(pattern.findall(path.read_text(encoding="utf-8")))
    return keys


def _t_keys_in_panel() -> set[str]:
    """扫 hosted TSX 里的静态 `t("key")`；模板字面量（动态键）不纳入，
    它们必须有 defaultValue 兜底（契约在 panel 侧由构造保证）。

    v0.10.1 面板拆分后键分散在 ui/ 的 shared/preview/model 与 ui/components/**——
    递归扫 ui/ 全域（tsx+ts）；只扫顶层会让新文件的键绕过这道门。"""
    pattern = re.compile(r'\bt\("([^"]+)"')
    keys: set[str] = set()
    for pattern_path in ("*.tsx", "*.ts"):
        for path in (ROOT / "ui").rglob(pattern_path):
            keys.update(pattern.findall(path.read_text(encoding="utf-8")))
    return keys


def test_all_locales_exist():
    for locale in LOCALES:
        assert (I18N_DIR / f"{locale}.json").is_file()


def test_keysets_are_identical_across_locales():
    base = set(_messages(LOCALES[0]))
    for locale in LOCALES[1:]:
        assert set(_messages(locale)) == base, f"keyset drift in {locale}"


def test_no_empty_values():
    for locale in LOCALES:
        for key, value in _messages(locale).items():
            assert isinstance(value, str) and value.strip(), f"empty value {locale}/{key}"


def test_every_tr_key_is_translated():
    missing = _tr_keys_in_code() - set(_messages("zh-CN"))
    assert not missing, f"tr() keys without i18n entries: {sorted(missing)}"


def test_every_panel_t_key_is_translated():
    missing = _t_keys_in_panel() - set(_messages("zh-CN"))
    assert not missing, f"panel t() keys without i18n entries: {sorted(missing)}"
