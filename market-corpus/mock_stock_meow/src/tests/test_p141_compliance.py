"""P14.1 守卫：去死亡化合规整改。

这一版把「玩家可见文案」里的物理死亡语义整体换成「破产 / 清算 / 被赶出家门」
的求生叙事。它要钉死的硬契约：

* **硬验收**：``i18n/zh-CN.json`` / ``story.json`` / ``ui/panel.tsx`` / ``cards.py``
  四个点名文件里，玩家可见的**文案**里不许出现「死 / 亡 / 墓 / 遗」单字，
  也不许出现「天台」这个组合词。
* **插件层全量**：除引擎层（``engine/``）与模式层（``modes/``）之外，
  其它玩家可见文案同样一个都不许有。
* **引擎层零改动**：引擎的 ``DEATH_TITLES`` **逐字未动**（仍含旧词），
  但插件层把**5 种**破产原因**全覆盖**改写 ⇒ 玩家永远看不到引擎原文。
* **墓碑 Emoji 出局**：``🪦`` 不许再出现（``🥣`` / ``🤡`` 取代）；
  ``🩸``（病危）换成 ``💸``；``💀`` 换成 ``🤡``。
* **第四章破产结局**：``loan_default`` 的叙事必须是「终身给猫娘打工的契约」。

**技术术语例外**：``死锁 / 锁死 / 写死 / 卡死 / 钉死 / 禁死 / 死的 / 死磕``
是工程用语（面板防死锁、源码扫描守卫把它锁死……），**不算死亡语义**，
允许出现在注释与文案里。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: 真·死亡语义（单字）。
_FORBIDDEN_CHARS = "死亡墓遗"
#: 真·死亡语义（组合词）—— 「天」「台」单独出现不算（今天 / 每天 / 舞台）。
_FORBIDDEN_WORDS = ("天台",)
#: 技术术语白名单（工程用语，非死亡语义）。
_TECH_TERMS = ("死锁", "锁死", "写死", "卡死", "钉死", "禁死", "死的", "死磕")

#: 引擎层与模式层是红线层 —— 500 局基线逐字节全等，一个字节都不许动。
_REDLINE_DIRS = ("engine/", "modes/")

#: 用户点名的四个文件（硬验收）。
_MUST_BE_CLEAN = (
    "i18n/zh-CN.json",
    "story.json",
    "ui/panel.tsx",
    "cards.py",
)

#: 玩家可见面的文件类型。
_TEXT_SUFFIXES = {".py", ".tsx", ".ts", ".json", ".toml", ".md"}

_TECH_RE = re.compile("|".join(_TECH_TERMS))
_BAD_RE = re.compile(r"[死亡墓遗]|天台")

_TSX_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TSX_LINE_COMMENT = re.compile(r"//[^\n]*")
_TSX_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`", re.S)
_TOML_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.S)


def _strip_tech(text: str) -> str:
    """把技术术语抠掉，剩下的才按死亡语义判定。"""
    return _TECH_RE.sub("", text)


def _hits(text: str) -> list[str]:
    """返回文案里的死亡语义词（空列表 = 干净）。"""
    stripped = _strip_tech(text)
    found = [c for c in _FORBIDDEN_CHARS if c in stripped]
    found += [w for w in _FORBIDDEN_WORDS if w in stripped]
    return found


# ── 各语言的「玩家可见文案」提取 ────────────────────────────────────────
def _py_literals(text: str) -> list[str]:
    """Python：所有字符串常量（含 docstring）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _tsx_literals(text: str) -> list[str]:
    """TSX/TS：字符串字面量的内容 + JSX 文本（剥离注释与字符串后的残留）。"""
    no_comment = _TSX_LINE_COMMENT.sub(" ", _TSX_BLOCK_COMMENT.sub(" ", text))
    strings = [m.group(0)[1:-1] for m in _TSX_STRING.finditer(no_comment)]
    residue = re.sub(r"\{[^{}]*\}", " ", _TSX_STRING.sub(" ", no_comment))
    return strings + [residue]


def _json_literals(text: str) -> list[str]:
    """JSON：所有键与值。"""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                out.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            out.append(node)

    walk(data)
    return out


def _toml_literals(text: str) -> list[str]:
    """TOML：所有字符串字面量的内容。"""
    return [m.group(0)[1:-1] for m in _TOML_STRING.finditer(text)]


def _md_literals(text: str) -> list[str]:
    """Markdown：整篇正文按行切开（README 是市场页，代码块也算）。"""
    return re.sub(r"`[^`\n]*`", " ", text).splitlines()


def _literals(rel: str, text: str) -> list[str]:
    suffix = Path(rel).suffix.lower()
    if suffix == ".py":
        return _py_literals(text)
    if suffix in (".tsx", ".ts"):
        return _tsx_literals(text)
    if suffix == ".json":
        return _json_literals(text)
    if suffix == ".toml":
        return _toml_literals(text)
    if suffix == ".md":
        return _md_literals(text)
    return []


def _plugin_files() -> list[Path]:
    """插件层玩家可见面（排除引擎层 / 模式层 / 测试面 / 隐藏目录）。"""
    out: list[Path] = []
    for path in sorted(_PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        if rel.startswith((".git/", "__pycache__/", ".venv/", ".pytest_cache/")):
            continue
        if rel.startswith(_REDLINE_DIRS) or rel.startswith("tests/"):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        out.append(path)
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════ ① 硬验收
def test_named_files_have_no_death_words() -> None:
    """四个点名文件的玩家可见文案里，一个死亡语义词都不许有。"""
    offenders: list[str] = []
    for rel in _MUST_BE_CLEAN:
        path = _PLUGIN_ROOT / rel
        assert path.exists(), rel
        for literal in _literals(rel, _read(path)):
            for word in _hits(literal):
                offenders.append(f"{rel}: [{word}] {literal[:60]}")
    assert not offenders, "玩家可见文案里还有死亡语义词：\n" + "\n".join(offenders)


def test_plugin_layer_has_no_death_words() -> None:
    """插件层（引擎层 / 模式层除外）的玩家可见文案全部干净。"""
    offenders: list[str] = []
    for path in _plugin_files():
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        for literal in _literals(rel, _read(path)):
            for word in _hits(literal):
                offenders.append(f"{rel}: [{word}] {literal[:60]}")
    assert not offenders, "插件层还有死亡语义词：\n" + "\n".join(offenders)


def test_named_files_have_no_death_words_in_raw_text() -> None:
    """四个点名文件**连注释一起**也不许有死亡语义词（单条 grep 可证）。"""
    offenders: list[str] = []
    for rel in _MUST_BE_CLEAN:
        path = _PLUGIN_ROOT / rel
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            words = _hits(line)
            if words:
                offenders.append(f"{rel}:{lineno}: [{'/'.join(words)}] {line.strip()[:80]}")
    assert not offenders, "点名文件里还有死亡语义词：\n" + "\n".join(offenders)


# ══════════════════════════════════════════════════════════ ② 引擎零改动
def test_engine_death_titles_are_untouched() -> None:
    """红线：引擎的 ``DEATH_TITLES`` **逐字未动**（仍含旧词）。

    玩家看不到它 —— 靠插件层把 5 种破产原因全覆盖改写（见下面那条）。
    """
    from engine.settlement import DEATH_TITLES

    assert "死" in DEATH_TITLES["harvested"]
    assert "命" in DEATH_TITLES["welfare_neglect"]


def test_plugin_overrides_every_engine_death_reason() -> None:
    """插件层把引擎的**每一种**破产原因都改写成安全文案。

    P14.1 的核心：只覆盖一种 ⇒ 玩家能从结算页直接读到引擎原文。
    """
    from engine.settlement import DEATH_TITLES
    from plugins.mock_stock_meow import MockStockMeowPlugin

    overrides = MockStockMeowPlugin._DEATH_TITLE_OVERRIDES
    missing = set(DEATH_TITLES) - set(overrides)
    assert not missing, f"这些破产原因没被插件层覆盖：{sorted(missing)}"


def test_overridden_titles_have_no_death_words() -> None:
    """改写后的称号（玩家真的能看到的那一句）不含死亡语义词。"""
    from engine.models import Player
    from plugins.mock_stock_meow import MockStockMeowPlugin

    for reason in MockStockMeowPlugin._DEATH_TITLE_OVERRIDES:
        player = Player("p", "n", 0.0, "infinite")
        player.death_reason = reason
        player.death_title = "引擎给的原始称号"
        MockStockMeowPlugin._override_death_title(player)
        title = player.death_title or ""
        assert title != "引擎给的原始称号", reason
        assert not _hits(title), f"{reason} → {title}"


# ══════════════════════════════════════════════════════════ ③ i18n 键齐全
def test_i18n_has_a_safe_key_for_every_death_reason() -> None:
    """``death.*`` 命名空间覆盖引擎的每一种破产原因。"""
    from engine.settlement import DEATH_TITLES

    i18n = json.loads(_read(_PLUGIN_ROOT / "i18n" / "zh-CN.json"))
    for reason in DEATH_TITLES:
        key = f"death.{reason}"
        assert key in i18n, key
        assert i18n[key], key
        assert not _hits(i18n[key]), f"{key} → {i18n[key]}"


def test_death_reason_labels_are_safe() -> None:
    """结算页第三屏用的中文短标签（穷困潦倒 / 资产归零 / …）不含死亡语义词。"""
    from plugins.mock_stock_meow.cards import DEATH_REASON_LABELS, EPITAPHS

    for label in DEATH_REASON_LABELS.values():
        assert not _hits(label), label
    for epitaph in EPITAPHS.values():
        assert not _hits(epitaph), epitaph


# ══════════════════════════════════════════════════════════ ④ 第四章结局
def test_loan_default_epitaph_is_the_work_contract() -> None:
    """第四章【借高利贷】的破产结局 = 签下终身给猫娘打工的契约。"""
    from plugins.mock_stock_meow.cards import ACHIEVEMENTS, EPITAPHS

    expected = (
        "你彻底破产了，抵押了所有能抵押的东西。"
        "为了还债，你被迫签下了终身给猫娘打工的契约，每天负责端茶倒水、买小鱼干。"
    )
    assert EPITAPHS["loan_default"] == expected
    debt = next(item for item in ACHIEVEMENTS if item["source"] == "loan_default")
    assert debt["hint"] == expected


def test_death_scene_uses_bridge_not_rooftop() -> None:
    """三屏叙事：站天桥看车流 → 被猫娘一记飞踢踹回出租屋 → 破产。"""
    i18n = json.loads(_read(_PLUGIN_ROOT / "i18n" / "zh-CN.json"))
    screen1 = i18n["death_scene.screen1"]
    screen2 = i18n["death_scene.screen2"]
    screen3 = i18n["death_scene.screen3"]
    assert "天桥" in screen1
    assert "飞踢" in screen2
    assert "出租屋" in screen2
    assert "破产" in screen3
    for text in (screen1, screen2, screen3):
        assert not _hits(text), text


def test_taunts_offer_survival_not_self_destruction() -> None:
    """Yui / 猫娘的嘲讽改成「去睡桥洞」「去要饭」这类求生去向。"""
    i18n = json.loads(_read(_PLUGIN_ROOT / "i18n" / "zh-CN.json"))
    assert "睡桥洞" in i18n["taunt.buy.1"]
    assert "要饭" in i18n["taunt.bankrupt.1"]
    for key, value in i18n.items():
        if key.startswith("taunt."):
            assert not _hits(value), f"{key} → {value}"


# ══════════════════════════════════════════════════════════ ⑤ Emoji
def test_tombstone_emoji_is_gone() -> None:
    """墓碑 Emoji 出局（``🪦`` → ``🥣`` / ``🤡``）。"""
    offenders: list[str] = []
    for path in _plugin_files():
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        for literal in _literals(rel, _read(path)):
            if "🪦" in literal:
                offenders.append(f"{rel}: {literal[:60]}")
    assert not offenders, "还有墓碑 Emoji：\n" + "\n".join(offenders)


def test_critical_emoji_is_money_not_blood() -> None:
    """病危（🩸）换成资金链断裂（💸）；💀 换成 🤡。"""
    i18n = json.loads(_read(_PLUGIN_ROOT / "i18n" / "zh-CN.json"))
    assert i18n["visual.critical"] == "资金链断裂"
    assert i18n["visual.stage_dying"] == "家道：濒临破产"

    panel = _read(_PLUGIN_ROOT / "ui" / "panel.tsx")
    assert "🩸" not in panel
    assert "💸" in panel

    plugin_source = _read(_PLUGIN_ROOT / "__init__.py")
    assert '"💀"' not in plugin_source
    assert '"🤡"' in plugin_source
