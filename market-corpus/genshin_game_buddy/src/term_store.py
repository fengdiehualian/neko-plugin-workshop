"""术语库加载：维度白名单 + 默认层/覆盖层逐字段合并 + 查询索引。

两层结构
--------
* **默认层**：``plugin_dir / <terms_dir> / <dimension>.toml``（只读，随插件包分发）
* **覆盖层**：``data_path() / <terms_dir> / <dimension>.toml``（用户可写，可选）

覆盖层按**术语 key 逐字段**合并：只写 ``brief`` 就只换 ``brief``，其余字段继承默认层
（C-09）；覆盖层可以新增术语（C-10）；只写一个维度文件不会抹掉其余维度（C-11）。

维度白名单固定为 :data:`DIMENSIONS`：目录里的 ``extra.toml`` 之类一律不读（B-12）。

只读、无第三方依赖：解析用标准库 ``tomllib``，因此可脱离宿主单测。
本模块不接收、不返回任何用户原文。
"""

from __future__ import annotations

# 平台把 Python 钉在 3.11（仓库 pyproject: requires-python = "==3.11.*"），
# 因此直接用标准库 tomllib，不引 tomli 兜底 —— 那会让本插件产生一个未声明的
# 第三方依赖，与"零依赖"的清单声明冲突（红线 E-17）。
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .game_registry import is_safe_relative_dir, normalize_key

#: 维度白名单与加载顺序。顺序即"语境块自动选取"的优先级。
DIMENSIONS: tuple[str, ...] = ("core", "characters", "slang", "systems")

#: 允许进入自动注入语境块的维度（其余维度只在查询时按需返回）。
CONTEXT_DIMENSIONS: tuple[str, ...] = ("core", "slang")

#: 进游戏时默认常驻上下文的维度（核心层）。characters 留给 lookup_game_term 按需查（拍板 2.0.56）。
CORE_LAYER_DIMENSIONS: tuple[str, ...] = ("core", "slang", "systems")

#: ``brief`` 的软上限：超过只记 warning，不丢条目（内容质量问题要早暴露）。
MAX_BRIEF_CHARS = 80

#: 部分匹配（前缀/子串）的最短归一化键长。
_MIN_PARTIAL_LEN = 2

#: 条目可选字段白名单（未知字段忽略，不报错）。
_OPTIONAL_STR_FIELDS = ("avoid", "source")


class TermLoadError(Exception):
    """术语库整体不可用。``code`` 是稳定的错误码，映射到 i18n 键。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TermEntry:
    """一条术语（合并后的完整形态）。"""

    key: str
    kind: str
    brief: str
    dimension: str = ""
    aliases: tuple[str, ...] = ()
    slang: tuple[str, ...] = ()
    avoid: str = ""
    #: 出处说明。只出现在查询返回体里，**永不进入自动注入的语境块**（省字符）。
    source: str = ""

    def match_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        for raw in (self.key, *self.aliases, *self.slang):
            key = normalize_key(raw)
            if key and key not in keys:
                keys.append(key)
        return tuple(keys)


@dataclass(frozen=True)
class TermLibrary:
    """一个游戏的完整术语库。不可变，加载完即可跨线程安全读取。"""

    game_id: str
    display_name: str
    tone: str
    entries: tuple[TermEntry, ...]
    by_key: Mapping[str, TermEntry]
    loaded_dimensions: tuple[str, ...] = ()
    empty_dimensions: tuple[str, ...] = ()
    failed_dimensions: tuple[str, ...] = ()
    skipped_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def size(self) -> int:
        return len(self.entries)

    def get(self, key: object) -> TermEntry | None:
        wanted = key.strip() if isinstance(key, str) else ""
        return self.by_key.get(wanted) if wanted else None

    def context_candidates(self) -> tuple[TermEntry, ...]:
        """按维度优先级（core → slang）给出自动注入候选，维度内保持文件顺序。"""
        ordered: list[TermEntry] = []
        for dimension in CONTEXT_DIMENSIONS:
            ordered.extend(entry for entry in self.entries if entry.dimension == dimension)
        return tuple(ordered)

    def match(self, query: object, limit: int) -> tuple[TermEntry, ...]:
        """在当前游戏内查询。分级命中：精确 → 前缀 → 子串 → brief 文本。"""
        if not isinstance(limit, int) or limit <= 0:
            return ()
        normalized = normalize_key(query)
        if not normalized:
            return ()

        ranked: list[str] = []

        def push(key: str) -> None:
            if key not in ranked:
                ranked.append(key)

        for entry in self.entries:
            if normalized in entry.match_keys():
                push(entry.key)

        if len(normalized) >= _MIN_PARTIAL_LEN:
            for entry in self.entries:
                if any(key.startswith(normalized) for key in entry.match_keys()):
                    push(entry.key)
            for entry in self.entries:
                if any(normalized in key for key in entry.match_keys()):
                    push(entry.key)
            # 拍板 2.0.58：key/alias 是 query 的子串——处理 LLM 传自然语言
            # （如"纳西妲的角色故事" → 命中"纳西妲"）。key 长度也要求 >= _MIN_PARTIAL_LEN 避免噪声。
            for entry in self.entries:
                if any(
                    len(key) >= _MIN_PARTIAL_LEN and key in normalized
                    for key in entry.match_keys()
                ):
                    push(entry.key)
            for entry in self.entries:
                if normalized in normalize_key(entry.brief):
                    push(entry.key)

        return tuple(self.by_key[key] for key in ranked[:limit])


# =============================================================================
# 解析
# =============================================================================


#: 单层单维度的读取结果。"文件不存在"与"文件损坏"必须区分：
#: 前者是正常的空维度，后者要在启动状态里如实报 partial。
_LAYER_ABSENT = "absent"
_LAYER_OK = "ok"
_LAYER_FAILED = "failed"


def _read_layer(
    path: Path, dimension: str, warnings: list[str]
) -> tuple[Mapping[str, Any] | None, str]:
    """读一层的一个维度文件，返回 ``(payload, status)``。"""
    if not path.is_file():
        return None, _LAYER_ABSENT
    try:
        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except Exception:
        # 不回显异常正文（可能含文件内容片段），只记维度名与文件名。
        warnings.append(f"{dimension}: malformed toml, skipped ({path.name})")
        return None, _LAYER_FAILED
    if not isinstance(payload, Mapping):
        warnings.append(f"{dimension}: toml root is not a table, skipped ({path.name})")
        return None, _LAYER_FAILED
    return payload, _LAYER_OK


def _extract_patches(
    payload: Mapping[str, Any] | None,
    dimension: str,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    """取出 ``[terms.<key>]`` 表，返回 ``{term_key: {字段: 原始值}}``（只含出现过的字段）。"""
    if payload is None:
        return {}
    raw_terms = payload.get("terms")
    if raw_terms is None:
        return {}
    if not isinstance(raw_terms, Mapping):
        warnings.append(f"{dimension}: [terms] is not a table, skipped")
        return {}

    patches: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in raw_terms.items():
        key = str(raw_key).strip()
        if not key:
            warnings.append(f"{dimension}: term with empty key, skipped")
            continue
        if not isinstance(raw_value, Mapping):
            warnings.append(f"{dimension}: term {key!r} is not a table, skipped")
            continue
        patches[key] = {str(field): value for field, value in raw_value.items()}
    return patches


def _extract_tone(
    payload: Mapping[str, Any] | None,
    dimension: str,
    warnings: list[str],
) -> str:
    """只有 ``core.toml`` 的 ``[game].tone`` 有效；``notes`` 刻意不读（永不外泄）。"""
    if payload is None:
        return ""
    game_table = payload.get("game")
    if game_table is None:
        return ""
    if not isinstance(game_table, Mapping):
        warnings.append(f"{dimension}: [game] is not a table, ignored")
        return ""
    if dimension != "core":
        warnings.append(f"{dimension}: [game] table is only meaningful in core.toml, ignored")
        return ""
    tone = game_table.get("tone")
    return tone.strip() if isinstance(tone, str) else ""


def _clean_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_str_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = value
    else:
        return ()
    out: list[str] = []
    for item in items:
        text = item.strip() if isinstance(item, str) else ""
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _build_entry(
    key: str,
    dimension: str,
    values: Mapping[str, Any],
    warnings: list[str],
) -> TermEntry | None:
    """把合并后的字段校验成条目；不合格返回 ``None``（调用方记 skipped）。"""
    kind = _clean_str(values.get("kind"))
    if not kind:
        warnings.append(f"{dimension}: term {key!r} rejected (kind is required)")
        return None

    brief = _clean_str(values.get("brief"))
    if not brief:
        warnings.append(f"{dimension}: term {key!r} rejected (brief is required)")
        return None
    if len(brief) > MAX_BRIEF_CHARS:
        warnings.append(
            f"{dimension}: term {key!r} brief is {len(brief)} chars (soft limit {MAX_BRIEF_CHARS})"
        )

    optional = {field: _clean_str(values.get(field)) for field in _OPTIONAL_STR_FIELDS}
    return TermEntry(
        key=key,
        kind=kind,
        brief=brief,
        dimension=dimension,
        aliases=_clean_str_list(values.get("aliases")),
        slang=_clean_str_list(values.get("slang")),
        avoid=optional["avoid"],
        source=optional["source"],
    )


def _merge_dimension(
    dimension: str,
    base_patches: Mapping[str, Mapping[str, Any]],
    override_patches: Mapping[str, Mapping[str, Any]],
    warnings: list[str],
    skipped: list[str],
) -> list[TermEntry]:
    """逐字段合并一个维度，返回校验通过的条目（保持"默认层顺序 + 覆盖层新增"）。"""
    ordered_keys = list(base_patches)
    ordered_keys.extend(key for key in override_patches if key not in base_patches)

    entries: list[TermEntry] = []
    for key in ordered_keys:
        values = dict(base_patches.get(key, {}))
        values.update(override_patches.get(key, {}))
        entry = _build_entry(key, dimension, values, warnings)
        if entry is None:
            skipped.append(f"{dimension}:{key}")
        else:
            entries.append(entry)
    return entries


def load_library(
    *,
    default_root: Path,
    override_root: Path | None,
    game_id: str,
    display_name: str,
    terms_dir: str,
    warn: Callable[[str], None] | None = None,
) -> TermLibrary:
    """加载一个游戏的术语库。

    :raises TermLoadError: ``terms_dir`` 非法或默认层目录不存在（``terms_dir_missing``）。
    """
    warnings: list[str] = []
    skipped: list[str] = []

    if not is_safe_relative_dir(terms_dir):
        raise TermLoadError("terms_dir_missing", "terms_dir is not a safe relative path")

    base_dir = Path(default_root).joinpath(*terms_dir.replace("\\", "/").split("/"))
    if not base_dir.is_dir():
        raise TermLoadError("terms_dir_missing", f"default layer missing for {game_id}")

    override_dir: Path | None = None
    if override_root is not None:
        candidate = Path(override_root).joinpath(*terms_dir.replace("\\", "/").split("/"))
        override_dir = candidate if candidate.is_dir() else None

    loaded_dimensions: list[str] = []
    empty_dimensions: list[str] = []
    failed_dimensions: list[str] = []
    entries: list[TermEntry] = []
    by_key: dict[str, TermEntry] = {}
    tone = ""

    for dimension in DIMENSIONS:
        base_payload, base_status = _read_layer(base_dir / f"{dimension}.toml", dimension, warnings)
        if override_dir is not None:
            override_payload, override_status = _read_layer(
                override_dir / f"{dimension}.toml", dimension, warnings
            )
        else:
            override_payload, override_status = None, _LAYER_ABSENT
        if base_status == _LAYER_ABSENT and override_status == _LAYER_ABSENT:
            continue

        loaded_dimensions.append(dimension)
        if _LAYER_FAILED in (base_status, override_status):
            failed_dimensions.append(dimension)

        dimension_tone = _extract_tone(base_payload, dimension, warnings)
        if dimension_tone:
            tone = dimension_tone

        base_patches = _extract_patches(base_payload, dimension, warnings)
        override_patches = _extract_patches(override_payload, dimension, warnings)
        dimension_entries = _merge_dimension(
            dimension, base_patches, override_patches, warnings, skipped
        )

        if not dimension_entries:
            empty_dimensions.append(dimension)

        for entry in dimension_entries:
            if entry.key in by_key:
                # 跨维度重名：先到先得（DIMENSIONS 顺序即优先级），并记 warning。
                warnings.append(
                    f"{dimension}: term {entry.key!r} already defined in "
                    f"{by_key[entry.key].dimension!r}, ignored"
                )
                skipped.append(f"{dimension}:{entry.key}")
                continue
            by_key[entry.key] = entry
            entries.append(entry)

    for message in warnings:
        if callable(warn):
            warn(message)

    return TermLibrary(
        game_id=game_id,
        display_name=display_name,
        tone=tone,
        entries=tuple(entries),
        by_key=by_key,
        loaded_dimensions=tuple(loaded_dimensions),
        empty_dimensions=tuple(empty_dimensions),
        failed_dimensions=tuple(failed_dimensions),
        skipped_terms=tuple(skipped),
        warnings=tuple(warnings),
    )


__all__ = [
    "CONTEXT_DIMENSIONS",
    "DIMENSIONS",
    "MAX_BRIEF_CHARS",
    "TermEntry",
    "TermLibrary",
    "TermLoadError",
    "load_library",
]
