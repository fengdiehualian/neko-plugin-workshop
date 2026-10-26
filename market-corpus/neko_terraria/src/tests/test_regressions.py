"""Regression tests for the Terraria bridge fixes."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

# Load the real bridge modules without executing the host-only plugin entrypoint.
_ROOT = Path(__file__).resolve().parents[1]
for _subdir in ("", "bridge", "autonomous", "core", "polish"):
    _name = "_terraria_regression" + (f".{_subdir}" if _subdir else "")
    _package = ModuleType(_name)
    _package.__path__ = [str(_ROOT / _subdir)]
    sys.modules[_name] = _package


def _bridge(name):
    return importlib.import_module(f"_terraria_regression.bridge.{name}")


Connection = _bridge("connection").Connection
EquipmentManager = _bridge("equipment").EquipmentManager
item_id = _bridge("item_npc_dict").item_id
ModItemRegistry = _bridge("mod_registry").ModItemRegistry
_best_stat = _bridge("upgrade")._best_stat


def test_registry_normalizes_names_aliases_and_preserves_cache(tmp_path: Path):
    registry = ModItemRegistry(str(tmp_path))
    entry = {
        "mod": "Example",
        "items": [{
            "id": 9001,
            "name": "Iron Ore",
            "aliases": ["iron_ore", "铁矿别名"],
            "use": "ore",
            "tags": ["mine"],
        }],
    }
    registry.sync_from_enum([entry])

    assert registry.live is True
    assert registry.resolve(" iron_ore ") == 9001
    assert registry.resolve("IRON ORE") == 9001
    assert registry.resolve("铁矿别名") == 9001
    assert registry.find_by_use("ore") == [9001]
    assert registry.find_by_tag("mine") == [9001]

    # 空枚举 = mod 未加载/未连接：撤销会话内权威性（磁盘缓存仅供显示，
    # 绝不能当权威身份用——否则会按过期数据认错物品）。
    assert registry.sync_from_enum([]) == {"added": [], "updated": [], "removed": []}
    assert registry.live is False
    assert registry.resolve("Iron Ore") == -1
    assert registry.find_by_use("ore") == []
    assert registry.find_by_tag("mine") == []
    # 缓存文件本身保留（供显示/下次加载），重新同步即恢复权威。
    assert (tmp_path / "data" / "mod_items" / "Example.json").exists()
    registry.sync_from_enum([entry])
    assert registry.live is True
    assert registry.resolve("Iron Ore") == 9001


def test_item_id_uses_registry_then_original_id_fallback():
    # live=False：注册表尚未同步（会话内非权威）——仍先问注册表，
    # 未命中再回退原版 ID 表；不得因缓存缺失而拒绝解析。
    registry = SimpleNamespace(
        resolve=lambda name: 9010 if "mod sword" in str(name).casefold() else -1,
        live=False)

    assert item_id("Mod Sword", registry) == 9010
    assert item_id("IRON ORE", registry) == 11
    assert item_id("铁矿", registry) == 11


class _DropMod:
    def __init__(self, inventory):
        self.inventory = inventory
        self.dropped = []

    async def get_inventory(self):
        return self.inventory

    async def drop_item(self, slot, stack):
        self.dropped.append((slot, stack))
        return True


def test_give_to_player_drops_from_backpack_and_rejects_partial_transfer():
    mod = _DropMod({
        "hotbar": [{"id": 8, "stack": 2, "inv_slot": 0}],
        "inventory": [{"id": 8, "stack": 3, "inv_slot": 10}],
        "equipped": [],
    })

    manager = EquipmentManager(mod)
    assert asyncio.run(manager.give_to_player(8, 4))
    assert mod.dropped == [(0, 2), (10, 2)]

    mod.dropped.clear()
    assert not asyncio.run(manager.give_to_player(8, 6))
    assert mod.dropped == []


def test_connection_on_message_deduplicates_callback():
    connection = Connection("127.0.0.1", 9877)
    callback = Mock()

    connection.on_message(callback)
    connection.on_message(callback)

    assert connection._event_callbacks == [callback]


def test_best_stat_scans_all_inventory_sections():
    inventory = {
        "hotbar": [{"damage": 4}],
        "inventory": [{"damage": 12}],
        "equipped": [{"damage": 8}],
    }
    assert _best_stat(inventory, "damage") == 12


def test_agent_stop_cancels_real_task_loop():
    async def scenario():
        agent_type = _bridge("agent").TerrariaAgent
        agent = agent_type.__new__(agent_type)
        agent._running = True
        agent._background_tasks = set()
        # stop() 会撤销物品/配方注册表权威性并检查启动任务（未启动 → None）；
        # stop_everything() 还要经过 coordinator/inquiry/mod/plugin 这几个协作方。
        agent.registry = SimpleNamespace(invalidate=Mock())
        agent.recipe_book = SimpleNamespace(invalidate=Mock())
        agent._start_task = None
        agent.executor = SimpleNamespace(cancel_current=AsyncMock(), busy=lambda: False)
        agent.longterm = SimpleNamespace(stop_all=AsyncMock(), busy_kinds=lambda: [])
        agent.coordinator = SimpleNamespace(cancel_pending_commands=Mock())
        agent.inquiry = SimpleNamespace(cancel_all=Mock())
        agent.mod = SimpleNamespace(stop_actions=AsyncMock(return_value=True))
        agent.plugin = SimpleNamespace(_autonomous_brain=None)
        agent.launcher = SimpleNamespace(close=Mock())
        agent.conn = SimpleNamespace(close=Mock())
        chain = _bridge("task_chain").TaskChain(None, None, None)
        task = agent._spawn_background_task(chain.run_loop())
        await asyncio.sleep(0)

        await agent.stop()

        assert task.cancelled()
        assert not agent._background_tasks
        assert not agent._running
        agent.executor.cancel_current.assert_awaited_once()
        agent.longterm.stop_all.assert_awaited_once()
        agent.launcher.close.assert_called_once()
        agent.conn.close.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("armor_type", [0, 1, 2])
def test_auto_equip_only_uses_matching_armor_slot(armor_type):
    mod = SimpleNamespace(
        get_inventory=AsyncMock(return_value={
            "inventory": [{"inv_slot": 12, "defense": 5, "armor_type": armor_type}],
            "equipped": [{"armor_slot": armor_type, "defense": 2}],
        }),
        equip_item=AsyncMock(return_value=True),
    )
    assert asyncio.run(EquipmentManager(mod).auto_equip())
    mod.equip_item.assert_awaited_once_with(12, armor_type)


def test_auto_equip_without_upgrade_reports_no_change():
    mod = SimpleNamespace(
        get_inventory=AsyncMock(return_value={
            "inventory": [{"inv_slot": 12, "defense": 2, "armor_type": 0}],
            "equipped": [{"armor_slot": 0, "defense": 5}],
        }),
        equip_item=AsyncMock(),
    )
    assert not asyncio.run(EquipmentManager(mod).auto_equip())
    mod.equip_item.assert_not_awaited()


def test_mining_resolves_mod_item_from_agent_registry(tmp_path):
    registry = ModItemRegistry(str(tmp_path))
    registry.sync_from_enum([{"mod": "Test", "items": [{"id": 9000, "name": "Custom Ore"}]}])
    mod = SimpleNamespace(get_inventory=AsyncMock(return_value={}), find_ore=AsyncMock(return_value=[]))
    agent = SimpleNamespace(registry=registry, get_inventory_sync=lambda: {}, get_state=lambda: {})
    mining = _bridge("mining").MiningEngine(mod, agent)
    assert asyncio.run(mining.mine_target("custom_ore")) == (9000, 0)
    mod.find_ore.assert_awaited_once()


def test_unknown_mining_target_never_starts_digging(tmp_path):
    mod = SimpleNamespace(get_inventory=AsyncMock(), dig_tile=AsyncMock())
    mining = _bridge("mining").MiningEngine(mod, SimpleNamespace(registry=ModItemRegistry(str(tmp_path))))
    assert asyncio.run(mining.mine_target("unknown ore")) == (-1, 0)
    assert asyncio.run(mining.mine_ore_inplace("unknown ore", 5, 5)) == 0
    mod.get_inventory.assert_not_awaited()
    mod.dig_tile.assert_not_awaited()


@pytest.mark.parametrize("delivered", [True, False])
def test_give_goal_never_reports_unconfirmed_delivery(delivered):
    """丢出物品 ≠ 主人收到：未确认拾取前，绝不报交付完成（假完成红线）。

    give_to_player 返回 True 也只代表"丢出动作成功"，不构成主人已拾取的证据；
    因此无论交付动作成功与否，整步都必须判未完成，且不得声称已交付。
    """
    module = _bridge("task_chain")
    equip = SimpleNamespace(give_to_player=AsyncMock(return_value=delivered))
    agent = SimpleNamespace(resolve_item=lambda _: 11, log=Mock())
    mod = SimpleNamespace(get_inventory=AsyncMock(
        return_value={"hotbar": [], "inventory": []}))
    chain = module.TaskChain(None, mod, equip, agent)
    goal = module.Goal("give", "铁矿", amount=3)

    assert asyncio.run(chain._execute(goal)) is False
    assert goal.actual == 0  # 未确认收到 → 不得计数
    assert goal.outcome == "unconfirmed"
    assert "未确认" in goal.report_fail
    assert ("已丢出" in goal.evidence) is delivered


def test_mining_goal_fails_when_delivery_fails():
    module = _bridge("task_chain")
    mining = SimpleNamespace(mine_target=AsyncMock(return_value=(11, 3)))
    equip = SimpleNamespace(give_to_player=AsyncMock(return_value=False))
    agent = SimpleNamespace(resolve_item=lambda _: 11, log=Mock())
    mod = SimpleNamespace(get_inventory=AsyncMock(
        return_value={"hotbar": [], "inventory": []}))
    chain = module.TaskChain(mining, mod, equip, agent)
    goal = module.Goal("mine", "铁矿", amount=3, deliver_to_player=True)

    # 挖够 3 个但交付未确认 → 整步不算完成，也不得报"已交给主人"
    assert not asyncio.run(chain._execute(goal))
    assert goal.actual == 0
    assert goal.outcome == "unconfirmed"
    assert "未确认" in goal.report_fail


@pytest.mark.parametrize("crafted", [0, 2])
def test_upgrade_count_excludes_equipping_existing_items(crafted):
    agent = SimpleNamespace(equip=SimpleNamespace(auto_equip=AsyncMock(return_value=True)))
    upgrade = _bridge("upgrade").UpgradeEngine(agent)
    upgrade._recipes = AsyncMock(return_value=[{"name": "example"}])
    upgrade._try_craft_upgrades = AsyncMock(return_value=crafted)
    assert asyncio.run(upgrade._check_and_craft()) == crafted
