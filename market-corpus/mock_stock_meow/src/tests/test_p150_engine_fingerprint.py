"""P15.0 守卫：引擎层 / 模式层的**长期**代码零改动（比 git diff 更强）。

为什么需要这条
--------------
P7 / P8 留下的两条守卫查的是「**工作树**相对 HEAD 有没有文件变动」——
它只在**提交前**有用。一旦有人把引擎层改动**提交**进去，
``git diff HEAD`` 就是空的，那两条守卫照样绿 ⇒ 红线形同虚设。

本条把红线**锚定在一份基线文件**上（``tests/_engine_fingerprint.json``）：

* 基线是在**已发布、已验收**的提交上生成的（不是当前工作树）；
* 指纹 = 剥掉 docstring 后的 AST dump 的 SHA256；
* 注释与 docstring **可以**自由改（清理版权字眼需要），
  任何**可执行代码**的改动 —— 哪怕改的是一个常量 ——
  都会让指纹对不上 ⇒ 无论改没提交，CI 都会红。

改基线文件本身需要显式提交，代码审查看得见。
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import code_fingerprint

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_BASELINE = _PLUGIN_ROOT / "tests" / "_engine_fingerprint.json"
_REDLINE_LAYERS = ("engine", "modes")


def _baseline() -> dict[str, str]:
    assert _BASELINE.exists(), f"缺基线文件：{_BASELINE}"
    data = json.loads(_BASELINE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return {str(k): str(v) for k, v in data.items()}


def test_engine_and_mode_code_matches_the_published_baseline() -> None:
    """引擎层 / 模式层的代码必须与已发布基线**逐文件**一致。"""
    baseline = _baseline()
    offenders: list[str] = []
    for rel, expected in sorted(baseline.items()):
        path = _PLUGIN_ROOT / rel
        if not path.exists():
            offenders.append(f"{rel}（文件不见了）")
            continue
        actual = code_fingerprint(path.read_text(encoding="utf-8"))
        if actual != expected:
            offenders.append(f"{rel}（代码指纹变了）")
    assert not offenders, "引擎层/模式层代码被改动了：\n" + "\n".join(offenders)


def test_no_new_files_in_redline_layers() -> None:
    """红线层不许**新增**文件（绕过基线也能改行为）。"""
    baseline = _baseline()
    known = set(baseline)
    actual = {
        path.relative_to(_PLUGIN_ROOT).as_posix()
        for layer in _REDLINE_LAYERS
        for path in ( _PLUGIN_ROOT / layer ).rglob("*.py")
        if "__pycache__" not in path.parts
    }
    extra = sorted(actual - known)
    assert not extra, f"红线层不许新增文件：{extra}"


def test_baseline_covers_both_layers() -> None:
    """基线本身要覆盖引擎层与模式层（不能只锚一半）。"""
    baseline = _baseline()
    assert any(rel.startswith("engine/") for rel in baseline)
    assert any(rel.startswith("modes/") for rel in baseline)
    assert len(baseline) >= 8, len(baseline)


def test_fingerprint_is_comment_and_docstring_insensitive() -> None:
    """指纹工具本身：改注释 / 改 docstring **不许**改变指纹。"""
    source = (
        '"""原始 docstring。"""\n'
        "\n"
        "import json\n"
        "\n"
        "\n"
        "def f(x):\n"
        '    """函数 docstring。"""\n'
        "    return x + 1\n"
    )
    edited = (
        '"""改过的 docstring。"""\n'
        "\n"
        "import json  # 加个注释\n"
        "\n"
        "\n"
        "def f(x):\n"
        '    """也改过了。"""\n'
        "    # 再加一行注释\n"
        "    return x + 1\n"
    )
    assert code_fingerprint(source) == code_fingerprint(edited)

    changed = edited.replace("return x + 1", "return x + 2")
    assert code_fingerprint(edited) != code_fingerprint(changed)
