"""P15.0 守卫：原创化去品牌化 —— 源码里不得残留任何旧作专有名词。

背景
----
项目进入开源迭代阶段后，「版权风险死角」必须清零：仓库里**任何一个字节**
都不该出现指向早期同类作品的角色名 / 作品名 / 旧插件 id ——
包括注释、docstring、测试里的禁词元组（那恰恰是最容易被外部扫描器
当成「引用了原作角色名」的证据）。

做法
----
旧作专有名词**按 Unicode 码点构造**：

* 源码里**零明文** —— 外部 grep / 扫描器抓不到；
* 守卫能力**一点不减** —— 一旦有人把明文写回源码，本文件立刻失败。

同时，本作自己的合规目标改由「真实金融词」与「夸大收益词」两组禁词守护
（见 ``tests/test_card_flow.py`` / ``tests/test_p7_effects.py`` / ``tests/test_p80_ui.py``）——
**守护的是《拟股喵喵》自己的语境**，而不是旧作的字眼。

红线
----
本文件只读，不改任何东西；``engine/`` 与 ``modes/`` 同样纳入扫描范围。
"""

from __future__ import annotations

import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: 旧作专有名词 —— **按 Unicode 码点构造**，源码零明文。
#:
#: 注释里也不写明文，只用「角色名 A / 作品名 B / 旧插件 id」指代。
#:
#: P19.0：新增**前缀**一项 —— 旧角色名的「两个拉丁字母」单独出现时
#: 也要算命中（否则「名字的缩写」这种写法能绕过整词守卫）。
#: 一并守住的还有「作品名 + 后缀」那一串。
_LEGACY_TERMS: tuple[str, ...] = tuple(
    "".join(chr(cp) for cp in codepoints)
    for codepoints in (
        (0x4E45, 0x7559, 0x7F8E),                     # 旧角色名（3 字）
        (0x0046, 0x0058, 0x6218, 0x58EB),             # 旧角色名（2 个拉丁字母 + 战士）
        (0x53CD, 0x5411, 0x66B4, 0x5BCC),             # 旧中文名（4 字）
        (0x6A, 0x75, 0x6C, 0x69, 0x75, 0x6D, 0x65, 0x69),   # 旧插件 id（8 字母）
        (0x0046, 0x0058),                             # 旧角色名的前缀（2 个拉丁字母）
    )
)

#: 扫描范围：插件仓库的全部文本文件（``engine/`` / ``modes/`` 也在内）。
_TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".tsx", ".ts", ".txt", ".yml", ".yaml", ".cfg", ".ini"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache", "node_modules", "vendor"}

#: 内联 data URI 的 base64 载荷（``ui/assets/avatars.ts`` 里有一堆）。
#:
#: P19.0：扫描前**先把它们剥掉** —— base64 是随机字节，
#: 重新生成一次图片就可能随机撞出「两个拉丁字母」那项，
#: 让守卫**误报**。剥掉的是字节，不是人写的字。
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _strip_base64(text: str) -> str:
    """剥掉长串 base64（保留行结构，行号才不会错位）。"""
    return _BASE64_RUN.sub(" ", text)


def _iter_text_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(_PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(_PLUGIN_ROOT).parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        out.append(path)
    return out


def test_no_legacy_brand_terms_in_repository() -> None:
    """插件仓库里一个旧作专有名词的明文都不许有。"""
    offenders: list[str] = []
    for path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        for lineno, line in enumerate(_strip_base64(text).splitlines(), start=1):
            for term in _LEGACY_TERMS:
                if term in line:
                    offenders.append(f"{rel}:{lineno}: 命中 {len(term)} 字旧作词")
    assert not offenders, "仓库里还有旧作专有名词：\n" + "\n".join(offenders)


def test_legacy_terms_are_not_reconstructible_from_source() -> None:
    """守卫本身也不能泄底：码点常量不许被还原成明文写进源码。"""
    offenders: list[str] = []
    for path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        for term in _LEGACY_TERMS:
            # 码点序列本身（0x4E45, 0x7559…）可以出现，但拼好的明文不行
            if term in _strip_base64(text):
                offenders.append(f"{rel}: 明文出现")
    assert not offenders, "\n".join(offenders)


def test_readme_only_has_the_generic_inspiration_notice() -> None:
    """README 的免责声明只说「灵感来自早期同类虚构作品」，不带任何具体名字。"""
    readme = (_PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "灵感来自早期同类虚构作品" in readme
    assert "不使用任何已有作品的版权内容、角色名与视觉素材" in readme
    for term in _LEGACY_TERMS:
        assert term not in readme, term


def test_plugin_uses_its_own_brand_words() -> None:
    """本作自己的名字与内部 id 在仓库里是**在场**的（不是被误删）。"""
    assert (_PLUGIN_ROOT / "plugin.toml").exists()
    toml = (_PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'name = "mock_stock_meow"' in toml or "mock_stock_meow" in toml
    readme = (_PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "拟股喵喵" in readme
    assert "mock_stock_meow" in readme


def test_guard_terms_cover_both_kinds_of_legacy_naming() -> None:
    """守卫表本身要够全：角色名 / 作品名 / 旧插件 id / 前缀 四类都在。

    P19.0：前缀那一项**只有 2 个字母**，所以长度下界从 3 放宽到 2 ——
    宁可多守一项，也不给「缩写写法」留口子。
    """
    assert len(_LEGACY_TERMS) == 5
    assert len(set(_LEGACY_TERMS)) == 5
    for term in _LEGACY_TERMS:
        assert 2 <= len(term) <= 16, term


def test_guard_terms_are_built_from_codepoints_not_plaintext() -> None:
    """禁词表必须是**码点构造**出来的（源码里没有拼好的明文）。

    顺带守一件容易漏的事：**注释里也不许出现明文** ——
    外部扫描器扫的是整份文件，注释里的一个「缩写 + 战士」照样算命中。
    """
    source = (_PLUGIN_ROOT / "tests" / "test_p150_branding.py").read_text(encoding="utf-8")
    for term in _LEGACY_TERMS:
        assert term not in source, "禁词表的注释里漏了明文"
