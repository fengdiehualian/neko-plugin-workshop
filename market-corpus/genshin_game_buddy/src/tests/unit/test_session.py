"""A-14 / A-22 / A-23 / A-28 / D-05：会话状态机、持久化与重推计数。

``session.py`` 的依赖（store、术语加载器）都是注入的鸭子类型，
所以这里完全不构造插件，直接驱动状态机。
"""

from __future__ import annotations

import pytest
from conftest import FakeLogger, FakeStore
from plugin.plugins.genshin_game_buddy.game_registry import GameEntry, GameRegistry, PluginOptions
from plugin.plugins.genshin_game_buddy.session import (
    STATE_KEY,
    GameSession,
    GameSessionManager,
    ReinjectCounter,
    SessionPersistence,
)
from plugin.plugins.genshin_game_buddy.term_store import TermEntry, TermLibrary, TermLoadError

# =============================================================================
# 构造辅助
# =============================================================================


def make_library(game_id: str, *keys: str) -> TermLibrary:
    entries = tuple(TermEntry(key=key, kind="机制", brief=f"{key}的说明") for key in keys)
    return TermLibrary(
        game_id=game_id,
        display_name=game_id,
        tone="",
        entries=entries,
        by_key={entry.key: entry for entry in entries},
    )


class Loader:
    """按 game_id 返回预置术语库；标记为失败的 game 抛 TermLoadError。"""

    def __init__(self, libraries: dict[str, TermLibrary], failing: set[str] | None = None) -> None:
        self.libraries = libraries
        self.failing = failing or set()
        self.calls: list[str] = []

    def __call__(self, entry: GameEntry) -> TermLibrary:
        self.calls.append(entry.game_id)
        if entry.game_id in self.failing:
            raise TermLoadError("terms_dir_missing", entry.game_id)
        return self.libraries[entry.game_id]


def make_entry(game_id: str, *, enabled: bool = True) -> GameEntry:
    return GameEntry(
        game_id=game_id,
        display_name=game_id.upper(),
        aliases=(game_id,),
        terms_dir=f"terms/{game_id}",
        enabled=enabled,
    )


def make_registry(*game_ids: str, disabled: tuple[str, ...] = ()) -> GameRegistry:
    return GameRegistry(
        games=tuple(make_entry(gid, enabled=gid not in disabled) for gid in game_ids)
    )


def make_manager(
    *,
    store: object | None = None,
    libraries: dict[str, TermLibrary] | None = None,
    failing: set[str] | None = None,
    options: PluginOptions | None = None,
    logger: FakeLogger | None = None,
) -> tuple[GameSessionManager, Loader]:
    loader = Loader(libraries or {}, failing)
    manager = GameSessionManager(
        persistence=SessionPersistence(store, logger=logger),
        load_library_fn=loader,
        options=options or PluginOptions(),
        logger=logger,
        clock=lambda: "2026-01-01T00:00:00+00:00",
    )
    return manager, loader


# =============================================================================
# GameSession 序列化
# =============================================================================


@pytest.mark.unit
def test_session_record__round_trips() -> None:
    session = GameSession(
        game_id="genshin",
        display_name="原神",
        switched_at="2026-01-01T00:00:00+00:00",
        previous_game_id="honkai",
    )
    restored = GameSession.from_record(session.to_record())
    assert restored == session


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        None,
        "genshin",
        42,
        {},
        {"game_id": ""},
        {"game_id": "   "},
        {"game_id": 5},
        [],
    ],
)
def test_session_record__malformed_records_are_ignored(raw: object) -> None:
    assert GameSession.from_record(raw) is None


@pytest.mark.unit
def test_session_record__missing_optional_fields_are_tolerated() -> None:
    session = GameSession.from_record({"game_id": "genshin"})
    assert session is not None
    assert session.display_name == "genshin"
    assert session.switched_at == ""
    assert session.previous_game_id == ""


# =============================================================================
# SessionPersistence（A-14：不谎报成功）
# =============================================================================


@pytest.mark.unit
async def test_session__store_disabled__write_returns_false_not_silent_success() -> None:
    store = FakeStore(enabled=False)
    persistence = SessionPersistence(store)
    assert persistence.available is False
    assert await persistence.save(GameSession("genshin", "原神")) is False
    assert await persistence.load() is None
    assert store.write_calls == 0  # 禁用时根本不碰 store


@pytest.mark.unit
async def test_session__store_write_rejected__save_is_false() -> None:
    store = FakeStore(enabled=True)
    store.fail_write = True
    persistence = SessionPersistence(store)
    assert await persistence.save(GameSession("genshin", "原神")) is False


@pytest.mark.unit
async def test_session__store_round_trip() -> None:
    store = FakeStore(enabled=True)
    persistence = SessionPersistence(store)
    session = GameSession("genshin", "原神", "2026-01-01T00:00:00+00:00")
    assert await persistence.save(session) is True
    assert STATE_KEY in store.data
    assert await persistence.load() == session
    assert await persistence.clear() is True
    assert await persistence.load() is None


@pytest.mark.unit
async def test_session__store_exception_is_swallowed_and_logged() -> None:
    class ExplodingStore:
        enabled = True

        async def get(self, key, default=None):
            raise RuntimeError("boom")

        async def set(self, key, value):
            raise RuntimeError("boom")

    logger = FakeLogger()
    persistence = SessionPersistence(ExplodingStore(), logger=logger)
    assert await persistence.load() is None
    assert await persistence.save(GameSession("genshin", "原神")) is False
    assert "store get failed" in logger.text
    assert "store set failed" in logger.text


# =============================================================================
# A-22 / A-23：恢复与兜底
# =============================================================================


@pytest.mark.unit
async def test_session__auto_restore__true_restores_stored_game() -> None:
    store = FakeStore(enabled=True)
    await SessionPersistence(store).save(GameSession("genshin", "原神"))
    manager, _loader = make_manager(
        store=store, libraries={"genshin": make_library("genshin", "抽卡")}
    )
    result = await manager.restore(make_registry("genshin", "wuwa"))
    assert result is not None
    assert result.session.game_id == "genshin"
    assert result.persisted is True
    assert manager.library is not None and manager.library.get("抽卡") is not None


@pytest.mark.unit
async def test_session__auto_restore__false_ignores_stored_value() -> None:
    store = FakeStore(enabled=True)
    await SessionPersistence(store).save(GameSession("genshin", "原神"))
    manager, _loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin")},
        options=PluginOptions(auto_restore_last_game=False),
    )
    assert await manager.restore(make_registry("genshin")) is None
    assert manager.current is None
    # 记录本身不该被删：下次打开开关还能用
    assert STATE_KEY in store.data


@pytest.mark.unit
async def test_session__default_game__empty_means_no_game() -> None:
    manager, _loader = make_manager(
        store=FakeStore(enabled=True),
        libraries={"genshin": make_library("genshin")},
        options=PluginOptions(default_game=""),
    )
    assert await manager.restore(make_registry("genshin")) is None


@pytest.mark.unit
async def test_session__default_game__nonempty_is_used_when_unset() -> None:
    manager, _loader = make_manager(
        store=FakeStore(enabled=True),
        libraries={"genshin": make_library("genshin")},
        options=PluginOptions(default_game="genshin"),
    )
    result = await manager.restore(make_registry("genshin", "wuwa"))
    assert result is not None and result.session.game_id == "genshin"


@pytest.mark.unit
async def test_session__default_game__alias_is_resolved_too() -> None:
    registry = GameRegistry.from_config(
        {"games": {"wuthering_waves": {"display_name": "鸣潮", "aliases": ["鸣潮", "WuWa"]}}}
    )
    manager, _loader = make_manager(
        store=FakeStore(enabled=True),
        libraries={"wuthering_waves": make_library("wuthering_waves")},
        options=PluginOptions(default_game="WuWa"),
    )
    result = await manager.restore(registry)
    assert result is not None and result.session.game_id == "wuthering_waves"


@pytest.mark.unit
async def test_session__stored_game_gone_from_registry_is_dropped_and_record_cleared() -> None:
    store = FakeStore(enabled=True)
    await SessionPersistence(store).save(GameSession("removed_game", "被删掉的游戏"))
    manager, _loader = make_manager(
        store=store, libraries={"genshin": make_library("genshin")}
    )
    assert await manager.restore(make_registry("genshin")) is None
    assert STATE_KEY not in store.data


@pytest.mark.unit
async def test_session__stored_game_disabled_falls_back_to_default() -> None:
    store = FakeStore(enabled=True)
    await SessionPersistence(store).save(GameSession("genshin", "原神"))
    manager, _loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin"), "wuwa": make_library("wuwa")},
        options=PluginOptions(default_game="wuwa"),
    )
    result = await manager.restore(make_registry("genshin", "wuwa", disabled=("genshin",)))
    assert result is not None and result.session.game_id == "wuwa"


@pytest.mark.unit
async def test_session__restore_failure_returns_none_without_raising() -> None:
    store = FakeStore(enabled=True)
    await SessionPersistence(store).save(GameSession("genshin", "原神"))
    logger = FakeLogger()
    manager, _loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin")},
        failing={"genshin"},
        logger=logger,
    )
    assert await manager.restore(make_registry("genshin")) is None
    assert manager.current is None
    assert "restore failed" in logger.text


# =============================================================================
# switch / unload / revalidate
# =============================================================================


@pytest.mark.unit
async def test_switch__loads_new_library_and_records_previous_game() -> None:
    store = FakeStore(enabled=True)
    manager, loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin", "抽卡"), "wuwa": make_library("wuwa", "声骸")},
    )
    first = await manager.switch(make_entry("genshin"))
    assert first.session.previous_game_id == ""
    assert first.persisted is True

    second = await manager.switch(make_entry("wuwa"))
    assert second.session.previous_game_id == "genshin"
    assert second.session.switched_at == "2026-01-01T00:00:00+00:00"
    assert second.library.game_id == "wuwa"
    assert loader.calls == ["genshin", "wuwa"]


@pytest.mark.unit
async def test_switch__unloads_old_index_so_old_terms_stop_matching() -> None:
    store = FakeStore(enabled=True)
    manager, _loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin", "雷神"), "wuwa": make_library("wuwa", "声骸")},
    )
    await manager.switch(make_entry("genshin"))
    assert manager.library is not None and manager.library.match("雷神", 3)

    await manager.switch(make_entry("wuwa"))
    assert manager.library is not None
    assert manager.library.match("雷神", 3) == ()
    assert manager.library.match("声骸", 3)


@pytest.mark.unit
async def test_switch__failure_rolls_back_to_previous_game() -> None:
    store = FakeStore(enabled=True)
    manager, _loader = make_manager(
        store=store,
        libraries={"genshin": make_library("genshin", "雷神"), "broken": make_library("broken")},
        failing={"broken"},
    )
    await manager.switch(make_entry("genshin"))
    with pytest.raises(TermLoadError):
        await manager.switch(make_entry("broken"))

    # 回滚：旧游戏仍然可用，KV 里也还是旧游戏
    assert manager.current is not None and manager.current.game_id == "genshin"
    assert manager.library is not None and manager.library.match("雷神", 3)
    assert store.data[STATE_KEY]["game_id"] == "genshin"


@pytest.mark.unit
async def test_switch__store_disabled_reports_persisted_false() -> None:
    manager, _loader = make_manager(
        store=FakeStore(enabled=False), libraries={"genshin": make_library("genshin")}
    )
    result = await manager.switch(make_entry("genshin"))
    assert result.persisted is False
    assert manager.persisted is False
    assert manager.current is not None  # 内存状态照常生效，只是不被记住


@pytest.mark.unit
async def test_unload__clears_memory_but_keeps_the_kv_record() -> None:
    store = FakeStore(enabled=True)
    manager, _loader = make_manager(store=store, libraries={"genshin": make_library("genshin")})
    await manager.switch(make_entry("genshin"))
    manager.unload()
    assert manager.current is None
    assert manager.library is None
    assert STATE_KEY in store.data


@pytest.mark.unit
def test_ensure_library__reloads_without_touching_switched_at() -> None:
    manager, loader = make_manager(
        store=FakeStore(enabled=True), libraries={"genshin": make_library("genshin", "抽卡")}
    )
    entry = make_entry("genshin")
    first = manager.ensure_library(entry)
    assert first.game_id == "genshin"
    assert loader.calls == ["genshin"]
    # 已经装着同一个游戏 → 不重复读盘
    manager.ensure_library(entry)
    assert loader.calls == ["genshin"]


@pytest.mark.unit
async def test_revalidate__no_game() -> None:
    manager, _loader = make_manager(store=FakeStore(enabled=True), libraries={})
    assert manager.revalidate(make_registry("genshin")) == "no_game"


@pytest.mark.unit
async def test_revalidate__reloads_when_still_registered() -> None:
    manager, loader = make_manager(
        store=FakeStore(enabled=True), libraries={"genshin": make_library("genshin", "抽卡")}
    )
    await manager.switch(make_entry("genshin"))
    before = manager.current
    assert manager.revalidate(make_registry("genshin", "wuwa")) == "reloaded"
    assert manager.current is not None and manager.current.switched_at == before.switched_at
    assert loader.calls == ["genshin", "genshin"]


@pytest.mark.unit
async def test_revalidate__drops_when_game_removed_or_disabled() -> None:
    manager, _loader = make_manager(
        store=FakeStore(enabled=True), libraries={"genshin": make_library("genshin")}
    )
    await manager.switch(make_entry("genshin"))
    assert manager.revalidate(make_registry("wuwa")) == "dropped"
    assert manager.current is None

    manager2, _loader2 = make_manager(
        store=FakeStore(enabled=True), libraries={"genshin": make_library("genshin")}
    )
    await manager2.switch(make_entry("genshin"))
    assert manager2.revalidate(make_registry("genshin", disabled=("genshin",))) == "dropped"
    assert manager2.current is None


@pytest.mark.unit
async def test_revalidate__drops_when_terms_become_unavailable() -> None:
    manager, loader = make_manager(
        store=FakeStore(enabled=True), libraries={"genshin": make_library("genshin")}
    )
    await manager.switch(make_entry("genshin"))
    loader.failing = {"genshin"}
    assert manager.revalidate(make_registry("genshin")) == "dropped"
    assert manager.current is None
    assert manager.library is None


# =============================================================================
# A-28：重推计数
# =============================================================================


@pytest.mark.unit
def test_reinject__zero_is_off() -> None:
    counter = ReinjectCounter(0)
    assert counter.enabled is False
    for _ in range(100):
        assert counter.observe(1) is False
    assert counter.pending == 0


@pytest.mark.unit
def test_reinject__n_repushes_after_n_messages() -> None:
    counter = ReinjectCounter(3)
    assert counter.enabled is True
    assert counter.observe(1) is False
    assert counter.pending == 1
    assert counter.observe(1) is False
    assert counter.pending == 2
    assert counter.observe(1) is True  # 第 3 条
    assert counter.pending == 0
    assert counter.observe(1) is False


@pytest.mark.unit
def test_reinject__keeps_remainder_across_thresholds() -> None:
    counter = ReinjectCounter(3)
    assert counter.observe(7) is True
    assert counter.pending == 1  # 7 - 3 = 4 → 只扣一次阈值，余数留到下次
    assert counter.observe(2) is True


@pytest.mark.unit
def test_reinject__ignores_non_positive_counts() -> None:
    counter = ReinjectCounter(3)
    assert counter.observe(0) is False
    assert counter.observe(-2) is False
    assert counter.pending == 0


@pytest.mark.unit
def test_reinject__reconfigure_and_reset() -> None:
    counter = ReinjectCounter(3)
    counter.observe(2)
    assert counter.pending == 2
    counter.reconfigure(5)
    assert counter.every_n == 5 and counter.pending == 2
    counter.reconfigure(0)
    assert counter.enabled is False and counter.pending == 0
    counter.reconfigure(2)
    counter.observe(1)
    counter.reset()
    assert counter.pending == 0
