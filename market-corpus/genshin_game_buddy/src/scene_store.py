"""场景定义加载：从 scenes.toml 解析场景信号与关联术语。

与 term_store.py 同构的两层合并：默认层（只读）+ 覆盖层（用户可写）。
文件缺失 / 解析失败 → 返回空 dict，不抛异常。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SceneEntry:
    """一个场景的定义。"""

    name: str
    signals: tuple[str, ...] = ()
    context_terms: tuple[str, ...] = ()
    prompt: str = ""  # 拍板 2.0.61 补丁：原 hint 改名 prompt，避免与 context_pack.hint 混淆


def _clean_str_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(s.strip() for s in raw if isinstance(s, str) and s.strip())


def _parse_scenes(payload: dict, warnings: list[str]) -> dict[str, SceneEntry]:
    scenes: dict[str, SceneEntry] = {}
    scenes_table = payload.get("scenes")
    if scenes_table is None:
        return scenes
    if not isinstance(scenes_table, dict):
        warnings.append("scenes: root is not a table, skipped")
        return scenes
    for name, values in scenes_table.items():
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name:
            warnings.append("scenes: empty scene name, skipped")
            continue
        if not isinstance(values, dict):
            warnings.append(f"scenes: {clean_name!r} is not a table, skipped")
            continue
        scenes[clean_name] = SceneEntry(
            name=clean_name,
            signals=_clean_str_list(values.get("signals")),
            context_terms=_clean_str_list(values.get("context_terms")),
            prompt=str(values.get("prompt") or values.get("hint") or "").strip(),  # 向后兼容旧 "hint" key
        )
    return scenes


def _read_layer(path: Path, warn_msg_on_missing: str | None = None, warnings: list[str] | None = None) -> dict | None:
    if not path.is_file():
        if warn_msg_on_missing and warnings is not None:
            warnings.append(warn_msg_on_missing)
        return None
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"failed to parse {path}: {type(exc).__name__}: {exc}")
        return None


def load_scenes(
    default_root: Path,
    override_root: Path | None,
    game_id: str,
    terms_dir: str,
    warnings: list[str] | None = None,
) -> dict[str, SceneEntry]:
    """从 ``<terms_dir>/scenes.toml`` 加载场景定义。

    两层合并：同名场景以覆盖层为准。返回空 dict 表示无可加载的场景。
    拍板 2.0.72：默认文件缺失 / 解析失败都会通过 warnings 上报，
    让调用方能明确知道"为什么 scenes=0"——而不是静默返回空。
    """
    warn = warnings if warnings is not None else []
    rel = Path(terms_dir) / "scenes.toml"

    result: dict[str, SceneEntry] = {}

    base_path = default_root / rel
    base = _read_layer(
        base_path,
        warn_msg_on_missing=f"default scenes file not found: {base_path}",
        warnings=warn,
    )
    if base is not None:
        result.update(_parse_scenes(base, warn))

    if override_root is not None:
        override_path = override_root / rel
        override = _read_layer(
            override_path,
            warn_msg_on_missing=None,  # override 缺失是正常情况，不警告
            warnings=warn,
        )
        if override is not None:
            result.update(_parse_scenes(override, warn))

    return result
