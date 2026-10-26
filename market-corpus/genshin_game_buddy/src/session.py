"""会话状态机：当前游戏 + 术语索引的加载/卸载 + KV 持久化 + 重推计数。

三条不变量
----------
1. **不谎报持久化**：store 被禁用或写失败时 ``SwitchResult.persisted is False``，
   调用方必须把它如实转达给模型（``errors.store_unavailable``），不得报"已记住"。
2. **锁内不做 await**：进程里同时存在入口所在的事件循环与 timer 的独立循环，
   跨线程 ``threading.Lock`` 里 await 会把某个循环线程整条堵死。因此挂锁区间
   严格保持同步（文件 IO 是同步的），持久化写在锁外。
3. **切换即卸载**：``switch()`` 先清空旧术语索引再加载新的；加载失败则回滚成
   切换前的状态（要么旧游戏仍可用，要么明确失败，不存在"半切换"）。

本模块不接触 SDK、不接触 ``self``：store 与术语加载器都是注入的鸭子类型，
因此可以脱离宿主单测。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .game_registry import GameEntry, GameRegistry, PluginOptions
from .term_store import TermLibrary, TermLoadError

#: 当前游戏在 KV 里的键。
STATE_KEY = "current_game"


def utc_now_iso() -> str:
    """当前 UTC 时间（秒精度 ISO8601）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class GameSession:
    """一次登记的结果。可安全序列化进 KV。"""

    game_id: str
    display_name: str
    switched_at: str = ""
    previous_game_id: str = ""

    def to_record(self) -> dict[str, str]:
        return {
            "game_id": self.game_id,
            "display_name": self.display_name,
            "switched_at": self.switched_at,
            "previous_game_id": self.previous_game_id,
        }

    @classmethod
    def from_record(cls, raw: object) -> GameSession | None:
        """从 KV 值还原。形状不对返回 ``None``（陈旧/被手改过的记录一律忽略）。"""
        if not isinstance(raw, Mapping):
            return None
        game_id = raw.get("game_id")
        if not isinstance(game_id, str) or not game_id.strip():
            return None
        display = raw.get("display_name")
        previous = raw.get("previous_game_id")
        switched_at = raw.get("switched_at")
        return cls(
            game_id=game_id.strip(),
            display_name=display.strip() if isinstance(display, str) and display.strip() else game_id.strip(),
            switched_at=switched_at if isinstance(switched_at, str) else "",
            previous_game_id=previous.strip() if isinstance(previous, str) else "",
        )


@dataclass(frozen=True)
class SwitchResult:
    """一次登记/切换的完整结果。"""

    session: GameSession
    library: TermLibrary
    persisted: bool = False


def _unwrap_store_result(result: object) -> tuple[bool, Any]:
    """把 SDK 的 ``Result`` 或裸值统一成 ``(ok, value)``。"""
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        if not is_ok():
            return False, None
        return True, getattr(result, "value", None)
    if isinstance(result, Mapping) and "value" in result:
        return True, result.get("value")
    return True, result


class SessionPersistence:
    """当前游戏的 KV 持久化。

    store 不可用时**不抛异常**，只是把 ``available`` 暴露为 False，让上层如实降级。
    """

    def __init__(self, store: object, *, key: str = STATE_KEY, logger: object | None = None) -> None:
        self._store = store
        self._key = key
        self._logger = logger

    @property
    def store(self) -> object:
        return self._store

    @property
    def available(self) -> bool:
        return bool(getattr(self._store, "enabled", False))

    def _warn(self, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            logger.warning(message)
        except Exception:  # pragma: no cover - 日志本身失败不应影响业务
            pass

    async def load(self) -> GameSession | None:
        if not self.available:
            return None
        try:
            raw = await self._store.get(self._key, None)
        except Exception:
            self._warn("SessionPersistence: store get failed")
            return None
        ok, value = _unwrap_store_result(raw)
        if not ok:
            self._warn("SessionPersistence: store get rejected")
            return None
        return GameSession.from_record(value)

    async def save(self, session: GameSession) -> bool:
        if not self.available:
            return False
        try:
            raw = await self._store.set(self._key, session.to_record())
        except Exception:
            self._warn("SessionPersistence: store set failed")
            return False
        ok, _ = _unwrap_store_result(raw)
        if not ok:
            self._warn("SessionPersistence: store set rejected")
        return ok

    async def clear(self) -> bool:
        if not self.available:
            return False
        try:
            raw = await self._store.delete(self._key)
        except Exception:
            self._warn("SessionPersistence: store delete failed")
            return False
        ok, _ = _unwrap_store_result(raw)
        return ok


class GameSessionManager:
    """当前游戏 + 术语索引的运行时状态机。"""

    def __init__(
        self,
        *,
        persistence: SessionPersistence,
        load_library_fn: Callable[[GameEntry], TermLibrary],
        options: PluginOptions | None = None,
        logger: object | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._persistence = persistence
        self._load_library_fn = load_library_fn
        self._options = options or PluginOptions()
        self._logger = logger
        self._clock = clock or utc_now_iso
        self._lock = threading.Lock()
        self._current: GameSession | None = None
        self._library: TermLibrary | None = None
        self._persisted = False

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------

    @property
    def current(self) -> GameSession | None:
        return self._current

    @property
    def library(self) -> TermLibrary | None:
        return self._library

    @property
    def persisted(self) -> bool:
        """最近一次登记是否真的写进了 KV。"""
        return self._persisted

    @property
    def options(self) -> PluginOptions:
        return self._options

    def set_options(self, options: PluginOptions) -> None:
        self._options = options

    def _warn(self, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            logger.warning(message)
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # 状态变更
    # ------------------------------------------------------------------

    def unload(self) -> None:
        """卸载当前游戏与术语索引（只清内存；KV 记录保留，供下次自动恢复）。"""
        self._current = None
        self._library = None
        self._persisted = False

    def _activate_locked(self, entry: GameEntry, *, reload_only: bool = False) -> tuple[GameSession, TermLibrary]:
        """同步完成"卸载旧索引 → 加载新索引 → 提交状态"；失败回滚。

        调用方必须已持有 ``self._lock``。这里刻意不做任何 await。
        """
        previous_session, previous_library = self._current, self._library
        # ④ 先卸载旧索引，避免新旧索引同时存在。
        self._current, self._library = None, None
        try:
            library = self._load_library_fn(entry)
        except TermLoadError:
            self._current, self._library = previous_session, previous_library
            raise

        keep_switched_at = (
            reload_only
            and previous_session is not None
            and previous_session.game_id == entry.game_id
        )
        session = GameSession(
            game_id=entry.game_id,
            display_name=entry.display_name,
            switched_at=previous_session.switched_at if keep_switched_at else self._clock(),
            previous_game_id="" if reload_only else (previous_session.game_id if previous_session else ""),
        )
        self._current, self._library = session, library
        return session, library

    def _resolve_default(self, registry: GameRegistry) -> GameEntry | None:
        raw = self._options.default_game
        if not raw:
            return None
        entry = registry.get(raw) or registry.resolve(raw).game
        if entry is None or not entry.enabled:
            return None
        return entry

    async def restore(self, registry: GameRegistry) -> SwitchResult | None:
        """启动时恢复：``auto_restore_last_game`` 决定是否读 KV，``default_game`` 兜底。"""
        stored = await self._persistence.load() if self._options.auto_restore_last_game else None

        entry: GameEntry | None = None
        if stored is not None:
            candidate = registry.get(stored.game_id)
            if candidate is not None and candidate.enabled:
                entry = candidate
        if entry is None:
            entry = self._resolve_default(registry)
        if entry is None:
            if stored is not None:
                # 存的是已下架/已删除的游戏：清掉陈旧记录，避免每次启动都读到它。
                await self._persistence.clear()
            return None

        with self._lock:
            try:
                session, library = self._activate_locked(entry)
            except TermLoadError:
                self._warn("GameSessionManager: restore failed to load term library")
                return None

        persisted = await self._persistence.save(session)
        self._persisted = persisted
        return SwitchResult(session=session, library=library, persisted=persisted)

    async def switch(self, entry: GameEntry) -> SwitchResult:
        """切换/登记游戏。术语库加载失败时回滚并抛 :class:`TermLoadError`。"""
        with self._lock:
            session, library = self._activate_locked(entry)

        persisted = await self._persistence.save(session)
        self._persisted = persisted
        return SwitchResult(session=session, library=library, persisted=persisted)

    def ensure_library(self, entry: GameEntry, *, force: bool = False) -> TermLibrary:
        """按需（重新）加载术语索引，不改变已登记的切换时间。

        ``force=True`` 用于"用户刚改过覆盖层"的场景：忽略内存缓存重新读盘。
        术语文件只有几个小 TOML，重读成本可忽略，但用户期望"改了立刻生效"。
        """
        with self._lock:
            if (
                not force
                and self._library is not None
                and self._current is not None
                and self._current.game_id == entry.game_id
            ):
                return self._library
            _, library = self._activate_locked(entry, reload_only=True)
            return library

    def revalidate(self, registry: GameRegistry) -> str:
        """配置热更新后重新对齐注册表。返回 ``no_game`` / ``kept`` / ``reloaded`` / ``dropped``。"""
        with self._lock:
            session = self._current
            if session is None:
                return "no_game"
            entry = registry.get(session.game_id)
            if entry is None or not entry.enabled:
                self.unload()
                return "dropped"
            try:
                self._activate_locked(entry, reload_only=True)
            except TermLoadError:
                self.unload()
                return "dropped"
            return "reloaded"


class ReinjectCounter:
    """"每 N 轮重推一次语境"的计数器。``every_n <= 0`` 即彻底关闭。"""

    def __init__(self, every_n: int = 0) -> None:
        self._every_n = max(int(every_n), 0) if isinstance(every_n, int) else 0
        self._pending = 0

    @property
    def every_n(self) -> int:
        return self._every_n

    @property
    def pending(self) -> int:
        return self._pending

    @property
    def enabled(self) -> bool:
        return self._every_n > 0

    def reconfigure(self, every_n: int) -> None:
        self._every_n = max(int(every_n), 0) if isinstance(every_n, int) else 0
        if self._every_n <= 0:
            self._pending = 0

    def observe(self, count: int = 1) -> bool:
        """记入 ``count`` 轮；跨过阈值返回 True，并把余数留到下一轮累加。

        一次只报一次 True（跨了多个阈值也不重复触发），余数取模保留——
        否则一次 7 条的突发会留下 4 的欠账，之后每条消息都继续触发。
        """
        if self._every_n <= 0:
            self._pending = 0
            return False
        if not isinstance(count, int) or count <= 0:
            return False
        self._pending += count
        if self._pending < self._every_n:
            return False
        self._pending %= self._every_n
        return True

    def reset(self) -> None:
        self._pending = 0


__all__ = [
    "STATE_KEY",
    "GameSession",
    "GameSessionManager",
    "ReinjectCounter",
    "SessionPersistence",
    "SwitchResult",
    "utc_now_iso",
]
