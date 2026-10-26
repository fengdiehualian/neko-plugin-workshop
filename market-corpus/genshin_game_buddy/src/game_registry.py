"""游戏注册表与插件选项（身份真源）。

把宿主下发的生效配置解析成结构化对象：
  - ``[genshin_game_buddy]`` → :class:`PluginOptions`（可调项，全部有范围钳制）
  - ``[games.<game_id>]``      → :class:`GameRegistry`（身份，术语文件之前就要能解析）

设计约定（与 plugin.toml 自定义段注释一致）：
  - 身份字段（game_id / display_name / aliases / terms_dir / enabled / context_terms）
    只在本段声明；术语包里的 ``core.toml [game]`` 只承载内容（tone / notes）。
  - 本模块**没有任何 IO**：配置由 ``self.config.dump()`` 下发，这里只做纯解析与匹配，
    因此可以脱离宿主单测。

隐私约定：本模块不持有、不返回、不记录任何用户原文。用户输入只经过
:func:`normalize_key` 变成临时匹配键，调用方只允许记录"是否命中"与键长。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: 未显式声明 ``terms_dir`` 时使用的相对路径模板（相对 plugin_dir）。
TERMS_DIR_TEMPLATE = "terms/{game_id}"

#: 部分匹配（子串/前缀）的最短归一化键长。1 个字符的输入太容易误命中。
_MIN_PARTIAL_LEN = 2

_WS_RE = re.compile(r"[\s\u3000]+")
_GAME_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# 玩家常把游戏名写在书名号/引号里，归一化时统一剥掉两端包裹符。
_TRIM_CHARS = "《》「」『』\"'“”‘’()（）[]【】<>"


def normalize_key(text: object) -> str:
    """把一段文本归一化成可比较的键：NFKC + casefold + 去空白 + 剥两端包裹符。

    归一化是幂等的，且不含任何隐式截断——调用方可以安全地拿它做字典键。
    """
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _WS_RE.sub("", folded)
    return folded.strip(_TRIM_CHARS)


def as_section(container: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """安全取出一个 TOML 表；不是表就返回空表（绝不抛）。"""
    if not isinstance(container, Mapping):
        return {}
    value = container.get(name)
    return value if isinstance(value, Mapping) else {}


def _clean_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_str_list(value: object) -> tuple[str, ...]:
    """把 TOML 值规范成字符串元组：接受字符串（视作单元素）与字符串列表。"""
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


# 拍板 2.0.66：OCR 配置档位 + 每档的 interval/threads 默认值
OCR_PROFILES: tuple[str, ...] = ("auto", "eco", "balanced", "performance", "custom")
OCR_PROFILE_DEFAULTS: dict[str, dict[str, int]] = {
    "eco":        {"interval": 30, "threads": 1},
    "balanced":   {"interval": 15, "threads": 1},
    "performance": {"interval": 8,  "threads": 2},
}


def _clean_profile(value: object) -> str:
    """把 TOML 值规范成 ocr_profile 枚举：非法值→默认 'auto'。"""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in OCR_PROFILES:
            return v
    return "auto"


def is_safe_relative_dir(value: str) -> bool:
    """``terms_dir`` 只允许 plugin_dir 下的普通相对路径（拒绝绝对路径与 ``..``）。"""
    if not value:
        return False
    if value.startswith(("/", "\\")) or ":" in value:
        return False
    parts = [part for part in re.split(r"[\\/]+", value) if part not in ("", ".")]
    return bool(parts) and all(part != ".." for part in parts)


def _clamp_int(value: object, *, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


# =============================================================================
# 插件可调项
# =============================================================================


@dataclass(frozen=True)
class PluginOptions:
    """``[genshin_game_buddy]`` 的解析结果。

    每个字段都有硬范围：清单里写错值不会让插件崩，也不会让预算失控。
    """

    default_game: str = ""
    auto_restore_last_game: bool = True
    context_inject_max_terms: int = 40
    max_context_chars: int = 800
    lookup_max_matches: int = 3
    lookup_max_chars_per_term: int = 200
    reinject_every_n_messages: int = 0
    # 拍板 2.0.64：参数化配置框架（为 2.0.65 Hosted UI 面板铺路）——本轮就把所有可调项落地，
    # 代码里读 self._options.xxx，不留 magic number。下面 7 个字段即使本轮只在 3 个被消费，
    # 也全部读到并存着，方便面板"读配置 → 展示 → 写回"三件事不需要重构。
    # 拍板 2.0.65：ocr_perceive_interval_seconds 的下限从 5 降到 3（tick 装饰器已硬编码 3s），
    # 新增 ocr_worker_threads（1-2）。
    # 拍板 2.0.66：新增 ocr_profile（auto/eco/balanced/performance/custom）——startup 时 auto 档
    # 探测 CPU+内存自动选档；用户手动改 interval/threads 自动切 custom 档。
    ocr_profile: str = "auto"  # 配置档（auto=启动时探测；eco/balanced/performance=预设；custom=自定义）
    ocr_perceive_interval_seconds: int = 15  # OCR 感知周期（秒）。3=极限、15=平衡、60=省 CPU
    ocr_worker_threads: int = 1  # OCR worker 线程数（1=串行 / 2=允许 2 个 OCR 并发）
    screen_activation_limit: int = 12  # 单次 screen-activate 上限
    query_activation_ttl_seconds: int = 300  # 用户消息命中术语的 TTL
    scene_prompt_reinject_seconds: int = 0  # scene prompt 重推间隔（0=只推一次）
    # 拍板 2.0.67：场景切换感知增强——S1 变化驱动 OCR + S2 场景切换立即 respond
    change_driven_enabled: bool = False  # 拍板 2.0.70：默认关——S1 change-detect 真机连续 worker 卡死，默认关回到 2.0.66 稳定行为。用户手动开也行，但真根因未定位前默认关。
    change_detect_threshold: int = 8  # 轻量帧哈希差异阈值（dHash 16 位中允许的差异位，[1, 64]）
    scene_switch_cooldown_seconds: int = 30  # 场景切换冷却（绕过 300s 全局冷却；[10, 300]）
    # 拍板 2.0.71：高频 OCR + 场景状态机——把 OCR 链路拆成"场景判定（高频）"和"术语激活（低频）"
    # 真机单次 OCR 链路 1.44s（capture 0.14 + ocr 1.30 + detect 0.00）→ interval < 1.5s 会排队。
    # ocr_scene_interval_seconds：场景判定+切换即推（默认 2s）；抓屏/OCR 缓存给 term 复用。
    # ocr_term_interval_seconds：术语激活（默认 15s，复用 scene 轮次的抓屏结果）。
    # scene_hysteresis_count：场景切换滞回（连续 N 次同一新场景才算切换，默认 3）。
    ocr_scene_interval_seconds: int = 2  # OCR 场景判定周期（秒）。默认 2s，[1, 30]
    ocr_term_interval_seconds: int = 15  # OCR 术语激活周期（秒）。默认 15s，[3, 300]
    scene_hysteresis_count: int = 3  # 场景切换滞回次数。默认 3（连续 3 次同一新场景才算切换），[1, 10]
    desktop_markers_enabled: bool = True  # 黑名单总开关
    desktop_markers_extra: tuple[str, ...] = ()  # 用户自定义额外桌面特征

    @classmethod
    def from_section(cls, section: Mapping[str, Any] | None) -> PluginOptions:
        data = section if isinstance(section, Mapping) else {}
        return cls(
            default_game=_clean_str(data.get("default_game")),
            auto_restore_last_game=_as_bool(data.get("auto_restore_last_game"), True),
            context_inject_max_terms=_clamp_int(
                data.get("context_inject_max_terms"), low=0, high=50, default=40
            ),
            max_context_chars=_clamp_int(
                data.get("max_context_chars"), low=16, high=8192, default=800
            ),
            lookup_max_matches=_clamp_int(
                data.get("lookup_max_matches"), low=1, high=10, default=3
            ),
            lookup_max_chars_per_term=_clamp_int(
                data.get("lookup_max_chars_per_term"), low=40, high=2000, default=200
            ),
            reinject_every_n_messages=_clamp_int(
                data.get("reinject_every_n_messages"), low=0, high=500, default=0
            ),
            # 拍板 2.0.64：6 个新可调项，每项都 clamp
            # 拍板 2.0.65：interval 下限 5→3（配合 tick 装饰器固定 3s）；新增 ocr_worker_threads
            # 拍板 2.0.66：新增 ocr_profile（auto/eco/balanced/performance/custom）
            ocr_profile=_clean_profile(data.get("ocr_profile")),
            ocr_perceive_interval_seconds=_clamp_int(
                data.get("ocr_perceive_interval_seconds"), low=3, high=300, default=15
            ),
            ocr_worker_threads=_clamp_int(
                data.get("ocr_worker_threads"), low=1, high=2, default=1
            ),
            screen_activation_limit=_clamp_int(
                data.get("screen_activation_limit"), low=1, high=30, default=12
            ),
            query_activation_ttl_seconds=_clamp_int(
                data.get("query_activation_ttl_seconds"), low=10, high=3600, default=300
            ),
            scene_prompt_reinject_seconds=_clamp_int(
                data.get("scene_prompt_reinject_seconds"), low=0, high=3600, default=0
            ),
            # 拍板 2.0.67：S1 变化驱动 OCR（开/关 + 哈希差异阈值 [1, 64]）
            #   S2 场景切换冷却 [10, 300]（绕过 300s 全局冷却）
            change_driven_enabled=_as_bool(data.get("change_driven_enabled"), False),  # 拍板 2.0.70：默认关
            change_detect_threshold=_clamp_int(
                data.get("change_detect_threshold"), low=1, high=64, default=8
            ),
            scene_switch_cooldown_seconds=_clamp_int(
                data.get("scene_switch_cooldown_seconds"), low=10, high=300, default=30
            ),
            # 拍板 2.0.71：高频 OCR + 场景状态机
            ocr_scene_interval_seconds=_clamp_int(
                data.get("ocr_scene_interval_seconds"), low=1, high=30, default=2
            ),
            ocr_term_interval_seconds=_clamp_int(
                data.get("ocr_term_interval_seconds"), low=3, high=300, default=15
            ),
            scene_hysteresis_count=_clamp_int(
                data.get("scene_hysteresis_count"), low=1, high=10, default=3
            ),
            desktop_markers_enabled=_as_bool(
                data.get("desktop_markers_enabled"), True
            ),
            desktop_markers_extra=_clean_str_list(data.get("desktop_markers_extra")),
        )


# =============================================================================
# 游戏身份
# =============================================================================


@dataclass(frozen=True)
class GameEntry:
    """一个已登记游戏的**身份**（不含任何术语内容）。"""

    game_id: str
    display_name: str
    aliases: tuple[str, ...]
    terms_dir: str
    enabled: bool = True
    context_terms: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()

    def normalized_keys(self) -> tuple[str, ...]:
        """本游戏全部可用于匹配的归一化键（id + 正式名 + 别名），去重保序。"""
        keys: list[str] = []
        for raw in (self.game_id, self.display_name, *self.aliases):
            key = normalize_key(raw)
            if key and key not in keys:
                keys.append(key)
        return tuple(keys)


@dataclass(frozen=True)
class GameMatch:
    """一次声明的解析结果。

    ``game`` 是命中的条目（可能 ``enabled = False``，由调用方判定）；
    ``reason`` 用于日志，不含用户原文。
    """

    game: GameEntry | None
    reason: str
    matched_len: int = 0

    @property
    def found(self) -> bool:
        return self.game is not None

    @property
    def usable(self) -> bool:
        return self.game is not None and self.game.enabled


@dataclass(frozen=True)
class GameRegistry:
    """``[games.*]`` 的解析结果。不可变，可安全跨线程共享。"""

    games: tuple[GameEntry, ...] = ()
    issues: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> GameRegistry:
        section = as_section(config, "games")
        games: list[GameEntry] = []
        issues: list[str] = []

        for raw_game_id, raw_entry in section.items():
            game_id = str(raw_game_id).strip()
            if not _GAME_ID_RE.match(game_id):
                issues.append(f"games.{game_id}: id must match ^[a-z][a-z0-9_]*$")
                continue
            if not isinstance(raw_entry, Mapping):
                issues.append(f"games.{game_id}: entry must be a table")
                continue

            display_name = _clean_str(raw_entry.get("display_name"))
            if not display_name:
                issues.append(f"games.{game_id}: display_name is required")
                continue

            aliases = _clean_str_list(raw_entry.get("aliases"))
            if not aliases:
                issues.append(f"games.{game_id}: aliases is required and must be non-empty")
                continue

            declared_dir = _clean_str(raw_entry.get("terms_dir"))
            if declared_dir and not is_safe_relative_dir(declared_dir):
                issues.append(f"games.{game_id}: terms_dir must be a relative path inside plugin_dir")
                declared_dir = ""

            games.append(
                GameEntry(
                    game_id=game_id,
                    display_name=display_name,
                    aliases=aliases,
                    terms_dir=declared_dir or TERMS_DIR_TEMPLATE.format(game_id=game_id),
                    enabled=_as_bool(raw_entry.get("enabled"), True),
                    context_terms=_clean_str_list(raw_entry.get("context_terms")),
                    signals=_clean_str_list(raw_entry.get("signals")),
                )
            )

        return cls(games=tuple(games), issues=tuple(issues))

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, game_id: object) -> GameEntry | None:
        wanted = str(game_id or "").strip()
        if not wanted:
            return None
        for game in self.games:
            if game.game_id == wanted:
                return game
        return None

    def enabled_games(self) -> tuple[GameEntry, ...]:
        return tuple(game for game in self.games if game.enabled)

    def available_names(self) -> str:
        """给错误文案用的"当前可用"列表；不包含被下架的游戏。"""
        return "、".join(game.display_name for game in self.enabled_games())

    def resolve(self, text: object) -> GameMatch:
        """把用户说出的游戏名解析成注册条目。

        顺序：精确 game_id → 精确别名 → 最长部分匹配。部分匹配要求
        归一化后至少 2 个字符，避免单字误命中。
        """
        query = normalize_key(text)
        if not query:
            return GameMatch(None, "not_found")

        for game in self.games:
            if query == normalize_key(game.game_id):
                return GameMatch(game, "exact_id", len(query))

        for game in self.games:
            if query in game.normalized_keys():
                return GameMatch(game, "exact_alias", len(query))

        if len(query) >= _MIN_PARTIAL_LEN:
            best_game: GameEntry | None = None
            best_score: tuple[int, int] | None = None
            for index, game in enumerate(self.games):
                for key in game.normalized_keys():
                    if len(key) < _MIN_PARTIAL_LEN:
                        continue
                    if query in key or key in query:
                        score = (len(key), -index)
                        if best_score is None or score > best_score:
                            best_score, best_game = score, game
            if best_game is not None:
                return GameMatch(best_game, "partial", len(query))

        return GameMatch(None, "not_found")


__all__ = [
    "TERMS_DIR_TEMPLATE",
    "GameEntry",
    "GameMatch",
    "GameRegistry",
    "PluginOptions",
    "as_section",
    "is_safe_relative_dir",
    "normalize_key",
]
