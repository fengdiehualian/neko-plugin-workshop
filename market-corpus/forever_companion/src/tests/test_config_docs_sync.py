"""plugin.toml 与各份人读文档/示例的同步检查门。

背景：README 的配置表与 config.example.toml 是用户实际照抄的唯一来源，但它们
与 plugin.toml 没有任何机器绑定——历史上 [mood].default_action_minutes（20 vs
实际 10）与 [emotion_sense].window_turns（3 vs 实际 2）在 0.6.8 改了默认值后
一直没人回来同步 README；[stats] 与 [emotion_sense] 两段则从来没进过示例文件。
都属于"改代码不改文档"的显示层说谎，本门把三件事钉死：

1. **README 覆盖**：plugin.toml 每个业务配置叶子键都必须在 README 配置表里有一行。
2. **README 取值**：表里「默认」列若是单个反引号字面量，必须与 plugin.toml 的实际值
   相等（bool / 数值 / 字符串各按类型归一化比较）。词表类（list/dict）与非
   字面量说明（如「内置屏蔽词表」）跳过取值比较，只查覆盖。
3. **示例覆盖**：config.example.toml 必须列出每个业务配置键，且不得出现 [plugin*] 段
   （示例里的值允许与出厂默认不同，故不查值）。

组合行（`ovulation_day` / `ovulation_window` 共用一行、默认列写 `14` / `3`）按
位置对齐比较。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TOML_PATH = PLUGIN_ROOT / "plugin.toml"
README_PATH = PLUGIN_ROOT / "README.md"
EXAMPLE_PATH = PLUGIN_ROOT / "config.example.toml"

# README 未收录的顶层段（元信息/运行时/store 由别处文档说明，不进配置表）
NON_CONFIG_SECTIONS = {
    "plugin",
    "plugin_runtime",
    "plugin_store",
    "plugin_state",
    "adapter",
}

# 配置表里裸键（不带 [section]. 前缀）归属的段
TIDE = "tide"

_BACKTICK = re.compile(r"`([^`]*)`")


def _load_manifest() -> dict[str, Any]:
    with TOML_PATH.open("rb") as fh:
        return tomllib.load(fh)


def _config_leaf_keys(manifest: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    """返回 {f"{section}.{key}": 值}，跳过 [tide.phases.*] 这类嵌套子表。"""
    leaves: dict[str, tuple[str, Any]] = {}
    for section, body in manifest.items():
        if section in NON_CONFIG_SECTIONS or not isinstance(body, dict):
            continue
        for key, value in body.items():
            if isinstance(value, dict):
                continue  # 子表（phases.*）按行单独说明，不做逐键比对
            leaves[f"{section}.{key}"] = value
    return leaves


def _readme_table_rows() -> dict[str, str]:
    """解析 README「## 配置」小节的表格 → {f"{section}.{key}": 默认列原文}。"""
    text = README_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("## 配置") and "plugin.toml" in ln),
        None,
    )
    assert start is not None, "README 里找不到「## 配置（plugin.toml ...）」小节"

    rows: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        key_cell, default_cell = cells[0], cells[-1]
        names = _BACKTICK.findall(key_cell)
        if not names:
            continue  # 表头 / 分隔行 / `phases.*` 之类的说明行
        for position, name in enumerate(names):
            # `[mood].enabled` → "mood.enabled"；裸键（不带 [section]. 前缀）归 [tide]
            spec = f"{TIDE}.{name}" if not name.startswith("[") else name[1:]
            spec = spec.replace("]", "")
            if "." not in spec:
                continue
            # 组合行按位置取对应的默认字面量，单键行取整列
            literals = _BACKTICK.findall(default_cell)
            if len(names) > 1 and len(literals) == len(names):
                rows[spec] = f"`{literals[position]}`"
            else:
                rows[spec] = default_cell
    return rows


def _same_scalar(toml_value: Any, literal: str) -> bool:
    """把 README 反引号字面量与 plugin.toml 值按类型归一化后比较。"""
    literal = literal.strip().strip('"').strip("'")
    if isinstance(toml_value, bool):
        return literal.lower() in {"true", "false"} and (literal.lower() == "true") == toml_value
    if isinstance(toml_value, (int, float)):
        try:
            return float(literal) == float(toml_value)
        except ValueError:
            return False
    if isinstance(toml_value, str):
        return literal == toml_value
    return True  # list/dict 等不参与取值比对


def test_readme_covers_every_config_key():
    """plugin.toml 的每个业务配置键都必须在 README 配置表里出现。"""
    leaves = _config_leaf_keys(_load_manifest())
    rows = _readme_table_rows()
    missing = sorted(k for k in leaves if k not in rows)
    assert not missing, (
        "README 配置表缺少以下 plugin.toml 已声明的配置项（新增配置必须同步文档）："
        + ", ".join(missing)
    )


def test_readme_defaults_match_plugin_toml():
    """README 默认列写字面量时，必须与 plugin.toml 的实际默认值一致。"""
    leaves = _config_leaf_keys(_load_manifest())
    rows = _readme_table_rows()

    drifted: list[str] = []
    for spec, toml_value in sorted(leaves.items()):
        default_cell = rows.get(spec)
        if default_cell is None:
            continue
        literals = _BACKTICK.findall(default_cell)
        if len(literals) != 1:
            continue  # 「—」「内置屏蔽词表」等说明性默认列，只查覆盖不查值
        if not _same_scalar(toml_value, literals[0]):
            drifted.append(f"{spec}: README=`{literals[0]}` / plugin.toml={toml_value!r}")

    assert not drifted, "README 配置表默认值与 plugin.toml 不一致：\n  " + "\n  ".join(drifted)


def test_config_example_covers_every_config_key():
    """config.example.toml 必须列出每个业务配置键，且不得出现 [plugin*] 段。

    与 README 同一类漂移：1.1.0 加 [stats]、0.6.x 加 [emotion_sense] 时都没回来
    补示例，新键对用户隐形。example 里的值允许与出厂默认不同（如 forbidden_words
    只示 5 项），所以只查覆盖不查值。
    """
    manifest = _load_manifest()
    example = tomllib.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    forbidden = [s for s in example if s == "plugin" or s.startswith("plugin_")]
    assert not forbidden, f"config.example.toml 不得包含 [plugin*] 段：{forbidden}"

    missing: list[str] = []
    for section, body in manifest.items():
        if section in NON_CONFIG_SECTIONS or not isinstance(body, dict):
            continue
        shown = example.get(section) or {}
        for key, value in body.items():
            if isinstance(value, dict):
                continue
            if key not in shown:
                missing.append(f"{section}.{key}")
    assert not missing, (
        "config.example.toml 缺少以下配置键（新增配置必须同步示例）：" + ", ".join(sorted(missing))
    )
