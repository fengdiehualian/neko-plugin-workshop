"""tModLoader 服务端 mod 接口：状态回报 + 精细动作（移动/放方块/抓钩/合成/装备）。"""

from typing import Any, Dict, List, Optional

from .connection import Connection


class ModLink:
    """封装需要 mod 支持的精细动作与状态查询。"""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    # 注意：mod 对每条命令都会回 ACK，这些动作也必须把回执读走，
    # 否则残留回执会让后续请求读到上一条的答复（串线）
    async def place_tile(self, x: int, y: int, tile_type: int) -> bool:
        resp = await self.conn.request_mod(
            {"cmd": "place_tile", "x": x, "y": y, "tile": tile_type})
        return bool(resp and resp.get("ok"))

    async def break_tile(self, x: int, y: int) -> bool:
        resp = await self.conn.request_mod(
            {"cmd": "break_tile", "x": x, "y": y})
        return bool(resp and resp.get("ok"))

    async def hook(self, x: int, y: int) -> None:
        await self.conn.request_mod({"cmd": "hook", "x": x, "y": y})

    async def use_item(self, x: int = -1, y: int = -1) -> bool:
        # 带目标坐标（tile 格）：让角色朝向/挥动方向对准目标（战斗/砍树/钓鱼用）
        cmd = {"cmd": "use_item"}
        if x >= 0:
            cmd["target_x"] = x
        if y >= 0:
            cmd["target_y"] = y
        resp = await self.conn.request_mod(cmd)
        return bool(resp and resp.get("ok"))

    async def select_item(self, slot: int) -> bool:
        resp = await self.conn.request_mod({"cmd": "select_item", "slot": slot})
        return bool(resp and resp.get("ok"))

    async def craft(self, item_name: str = "", item_id: int = -1,
                    amount: int = 1, recipe_index: Optional[int] = None) -> int:
        cmd = {"cmd": "craft", "amount": amount}
        if recipe_index is not None:
            cmd["recipe_index"] = recipe_index
        if item_name:
            cmd["item_name"] = item_name
        if item_id > 0:
            cmd["item_id"] = item_id
        resp = await self.conn.request_mod(cmd, timeout=5.0)
        return int(resp.get("crafted", 0)) if resp else 0

    async def equip_item(self, inv_slot: int, equip_slot: int) -> bool:
        resp = await self.conn.request_mod(
            {"cmd": "equip", "inv": inv_slot, "equip": equip_slot})
        return bool(resp and resp.get("ok"))

    async def give_item(self, item_id: int, stack: int = 1) -> bool:
        resp = await self.conn.request_mod(
            {"cmd": "give_item", "item_id": item_id, "stack": stack})
        return bool(resp and resp.get("ok"))

    async def drop_item(self, inv_slot: int, stack: int = 1) -> bool:
        # 猫娘把背包某槽位物品丢到脚下，玩家拾取（转交）
        resp = await self.conn.request_mod(
            {"cmd": "drop_item", "slot": inv_slot, "stack": stack})
        return bool(resp and resp.get("ok"))

    async def use_item_slot(self, inv_slot: int) -> bool:
        # 选中并使用背包某槽位物品
        resp = await self.conn.request_mod(
            {"cmd": "use_item_slot", "slot": inv_slot})
        return bool(resp and resp.get("ok"))

    async def screenshot(self) -> Optional[Dict[str, Any]]:
        """请求 mod 截图一帧（C# 侧需实现 'screenshot' 命令）。

        返回 {"b64": ..., "mime": ...}；mod 未实现/失败返回 None（视觉管线静默降级）。
        """
        try:
            resp = await self.conn.request_mod({"cmd": "screenshot"}, timeout=5.0)
            if resp and resp.get("ok") and resp.get("image"):
                return {"b64": resp["image"],
                        "mime": resp.get("mime", "image/png")}
        except Exception:
            pass
        return None

    async def get_inventory(self) -> Dict[str, Any]:
        # 返回三大类：hotbar(手持栏) / equipped(装备栏) / inventory(主背包)。
        # 兼容旧版 LumiBridge 的 {items: [...]} 回执；旧版只返回一张
        # 50 格背包表，不能因为没有拆分字段而把背包误判为空。
        resp = await self.conn.request_mod({"cmd": "get_inventory"})
        if not resp or resp.get("error") or resp.get("type") != "inventory":
            raise ConnectionError("背包状态暂时不可用，不能据此判断缺少工具或物品")
        items = resp.get("items")
        if isinstance(items, list) and not any(
                key in resp for key in ("hotbar", "inventory", "equipped")):
            hotbar, inventory = [], []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                slot = item.get("inv_slot", item.get("slot"))
                if slot is None:
                    continue
                try:
                    slot = int(slot)
                except (TypeError, ValueError):
                    continue
                item["inv_slot"] = slot
                (hotbar if 0 <= slot < 10 else inventory).append(item)
            return {
                "hotbar": hotbar,
                "equipped": [],
                "inventory": inventory,
                "selected_slot": int(resp.get("selected_slot", 0) or 0),
                "slot_count": int(resp.get("slot_count", 50) or 50),
            }
        return {
            "hotbar": resp.get("hotbar", []),
            "equipped": resp.get("equipped", []),
            "inventory": resp.get("inventory", []),
            "selected_slot": resp.get("selected_slot", 0),
            "slot_count": int(resp.get("slot_count", 50) or 50),
        }

    async def enum_chests(self) -> List[Dict[str, Any]]:
        resp = await self.conn.request_mod({"cmd": "enum_chests"}, timeout=5.0)
        return resp.get("chests", []) if resp else []

    async def store_item(self, chest_x: int, chest_y: int,
                         inv_slot: int, stack: int = 1) -> bool:
        resp = await self.conn.request_mod({
            "cmd": "store_item", "x": chest_x, "y": chest_y,
            "slot": inv_slot, "stack": stack})
        return bool(resp and resp.get("ok"))

    async def take_from_chest(self, chest_x: int, chest_y: int,
                              item_id: int, stack: int = 1) -> bool:
        resp = await self.conn.request_mod({
            "cmd": "take_chest", "x": chest_x, "y": chest_y,
            "item_id": item_id, "stack": stack})
        return bool(resp and resp.get("ok"))

    # ── v0.5 基地系统能力（基地与补给系统） ──

    async def get_spawn(self) -> Optional[tuple]:
        """查询世界出生点坐标 (tile_x, tile_y)。"""
        resp = await self.conn.request_mod({"cmd": "get_spawn"}, timeout=3.0)
        if resp and resp.get("x") is not None:
            return int(resp.get("x", 0)), int(resp.get("y", 0))
        return None

    async def use_mirror(self) -> bool:
        """使用魔镜/冰雪镜回出生点（合法物品，没有就生成一个）。"""
        resp = await self.conn.request_mod({"cmd": "use_mirror"}, timeout=5.0)
        return bool(resp and resp.get("ok"))

    async def place_chest(self, x: int, y: int, style: int = 0) -> bool:
        """在 (x,y) 放置木箱。"""
        resp = await self.conn.request_mod({
            "cmd": "place_chest", "x": x, "y": y, "style": style}, timeout=3.0)
        return bool(resp and resp.get("ok"))

    async def quick_stack(self) -> int:
        """把背包物品快速堆叠进最近的箱子。返回堆叠的物品种数。"""
        resp = await self.conn.request_mod({"cmd": "quick_stack"}, timeout=3.0)
        return int(resp.get("ok", 0) or 0) if resp else 0

    # ── v0.5 砍树 / 钓鱼（陪伴式交互：什么任务用什么工具） ──

    async def find_trees(self, radius: int = 30) -> List[Dict[str, Any]]:
        """扫描附近树木，返回按距离排序的树根坐标列表。"""
        resp = await self.conn.request_mod(
            {"cmd": "find_trees", "radius": radius}, timeout=5.0)
        return resp.get("trees", []) if resp else []

    async def chop_trees(self, x: int, y: int) -> bool:
        """砍掉指定位置的一棵树（整列 kill）。"""
        resp = await self.conn.request_mod(
            {"cmd": "chop_trees", "x": x, "y": y}, timeout=5.0)
        return bool(resp and resp.get("ok"))

    async def find_water(self, radius: int = 30) -> List[Dict[str, Any]]:
        """扫描附近水域（钓鱼用），返回按距离排序的水面格坐标。"""
        resp = await self.conn.request_mod(
            {"cmd": "find_water", "radius": radius}, timeout=5.0)
        return resp.get("water", []) if resp else []

    async def find_fishing_rod(self) -> int:
        """找背包里的钓竿，返回 inv_slot（-1 = 没有）。"""
        try:
            inv = await self.get_inventory()
        except Exception:
            return -1
        for it in (inv.get("inventory", []) or []) + (inv.get("hotbar", []) or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "")
            if (int(it.get("fishing_pole", 0) or 0) > 0 or "钓竿" in name
                    or "鱼竿" in name or "fishing" in name.casefold()):
                slot = it.get("inv_slot", -1)
                return int(slot) if slot is not None else -1
        return -1

    async def navigate_to(self, x: int, y: int, timeout: int = 15,
                          allow_build: bool = False) -> bool:
        # 普通导航和流式导航共用可取消的请求管理，爬升也能被 stop_actions 停止。
        return await self.navigate_async(x, y, timeout=timeout,
                                         allow_build=allow_build)

    # ── v3.0: 流式导航──
    # C# 侧 BFS 寻路 + 逐点执行，通过 nav_* 事件流回传状态
    # （nav_moving/nav_arrived/nav_stuck/nav_timeout），Python 可中断/感知进度

    async def stop_actions(self) -> bool:
        """停止待发导航及 Mod 中的移动、攻击、挖掘和钩锁控制。"""
        import asyncio

        tasks = list(getattr(self, "_nav_tasks", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # 断线时无需再等待 stop_actions 的 TCP 回执；动作任务已经在
        # Python 侧取消，连接恢复后也不应把旧的停止请求排到新会话里。
        if not self.conn.is_mod_connected():
            return False
        resp = await self.conn.request_mod({"cmd": "stop_actions"}, timeout=3.0)
        return bool(resp and resp.get("ok"))

    def _track_navigation(self, task):
        if not hasattr(self, "_nav_tasks"):
            self._nav_tasks = set()
        self._nav_tasks.add(task)
        task.add_done_callback(self._nav_tasks.discard)
        return task

    async def navigate_async(self, x: int, y: int, timeout: int = 20,
                             on_tick=None, allow_build: bool = False) -> bool:
        """等待对应 req_id 的最终导航结果，同时每半秒执行环境感知回调。"""
        import asyncio

        request = self._track_navigation(asyncio.create_task(self.conn.request_mod(
            {"cmd": "navigate_stream", "x": x, "y": y,
             "timeout": timeout, "allow_build": bool(allow_build)},
            timeout=timeout + 5)))
        try:
            while True:
                done, _ = await asyncio.wait({request}, timeout=0.5)
                if done:
                    if request.cancelled():
                        return False
                    resp = request.result()
                    return bool(resp and resp.get("ok"))
                if on_tick is not None:
                    try:
                        if await on_tick():
                            await self.stop_actions()
                            return False
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
        except asyncio.CancelledError:
            await self.stop_actions()
            raise
        finally:
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    async def navigate_stream_fire(self, x: int, y: int) -> Optional[bool]:
        """替换上次后台导航，并暴露上一轮同目标的结果。

        跟随/自主巡逻使用 fire-and-forget，过去导航失败会被完全吞掉，
        行为循环便会无限重发同一条无效路线。只有“上一轮也是同一目标”
        时才返回它的结果；目标变化导致的取消返回 ``None``，避免误判。
        """
        import asyncio

        previous = getattr(self, "_nav_fire_task", None)
        previous_target = getattr(self, "_nav_fire_target", None)
        previous_result = None
        if previous is not None and previous.done() and previous_target == (x, y):
            try:
                previous_result = previous.result()
            except asyncio.CancelledError:
                previous_result = None
            except Exception:
                previous_result = False
        if previous is not None and not previous.done():
            if getattr(self, "_nav_fire_target", None) == (x, y):
                return None
            previous.cancel()
            await asyncio.gather(previous, return_exceptions=True)

        async def _fire():
            try:
                resp = await self.conn.request_mod(
                    {"cmd": "navigate_stream", "x": x, "y": y,
                     "timeout": 20, "allow_build": False},
                    timeout=25.0)
                return bool(resp and resp.get("ok"))
            except Exception:
                return False

        self._nav_fire_target = (x, y)
        self._nav_fire_task = self._track_navigation(asyncio.create_task(_fire()))
        return previous_result

    def _on_nav_event(self, msg: dict) -> None:
        """agent 转发 nav_* 事件到此（connection → agent._handle_mod_event → 这里）。"""
        for cb in list(getattr(self, "_nav_callbacks", None) or ()):
            try:
                cb(msg)
            except Exception:
                pass

    async def warp_to(self, x: int, y: int) -> bool:
        """传送到指定坐标"""
        resp = await self.conn.request_mod(
            {"cmd": "warp", "x": x, "y": y})
        return bool(resp and resp.get("ok"))

    async def damage_npc(self, npc_slot: int, damage: int) -> bool:
        """对 NPC 造成伤害"""
        resp = await self.conn.request_mod(
            {"cmd": "damage_npc", "slot": npc_slot, "damage": damage})
        return bool(resp and resp.get("ok"))

    async def send_chat(self, text: str) -> bool:
        """发送聊天消息"""
        resp = await self.conn.request_mod(
            {"cmd": "send_chat", "text": text})
        return bool(resp and resp.get("ok"))

    async def get_capabilities(self) -> Dict[str, Any]:
        resp = await self.conn.request_mod({"cmd": "get_capabilities"})
        return resp if resp else {}

    async def collect_items(self, radius: int = 600, item_id: Optional[int] = None) -> int:
        """收集附近掉落物品

        Args:
            radius: 收集半径（像素）

        Returns:
            收集到的物品数量
        """
        cmd = {"cmd": "collect_items", "radius": radius}
        if item_id is not None and item_id > 0:
            cmd["item_id"] = int(item_id)
        resp = await self.conn.request_mod(cmd, timeout=5.0)
        return int(resp.get("collected", 0)) if resp else 0

    async def dig_tile(self, x: int, y: int, timeout: float = 5.0) -> bool:
        """原生物品挖掘（mod 原生能力）：自动选镐子 + 光标定位 + controlUseItem。
        有工具动画/消耗/属性，比 break_tile 直接改 tile 真实。</summary>"""
        resp = await self.conn.request_mod(
            {"cmd": "dig_tile", "x": x, "y": y}, timeout=timeout)
        return bool(resp and resp.get("ok"))

    async def find_ore(self, radius: int = 30, tile_type: Optional[int] = None) -> List[Dict[str, Any]]:
        """Scan ore, or an exact TileID; unknown (-1) must not broaden the search."""
        if tile_type is not None and tile_type < 0:
            return []
        cmd = {"cmd": "find_ore", "radius": radius}
        if tile_type is not None:
            cmd["tile_type"] = tile_type
        resp = await self.conn.request_mod(cmd, timeout=5.0)
        return resp.get("ores", []) if resp else []

    async def scan_ledges(self, x0: int, y0: int, x1: int, y1: int) -> List[Dict[str, Any]]:
        # 扫描两点之间的可落脚平台，供分段爬升规划
        resp = await self.conn.request_mod({"cmd": "scan_ledges", "x0": x0, "y0": y0,
                                            "x1": x1, "y1": y1}, timeout=5.0)
        return resp.get("points", []) if resp else []

    async def get_recipes(self, category: str = "all") -> Optional[List[Dict[str, Any]]]:
        resp = await self.conn.request_mod({"cmd": "get_recipes", "cat": category},
                                           timeout=5.0)
        return resp.get("recipes") if resp and resp.get("ok") is not False else None

    async def get_recipe_status(self) -> Optional[Dict[str, Any]]:
        """Return live recipe/environment indexes; None means the session is stale."""
        resp = await self.conn.request_mod({"cmd": "get_recipes", "cat": "status"},
                                           timeout=5.0)
        if not resp or resp.get("ok") is False or "available" not in resp or "environment_ready" not in resp:
            return None
        return {"available": resp.get("available", []),
                "environment_ready": resp.get("environment_ready", [])}

    async def get_state(self, player_name: str = "") -> Dict[str, Any]:
        resp = await self.conn.request_mod(
            {"cmd": "get_state", "player_name": player_name})
        # 临时诊断：确认 resp 是不是真的 get_state 响应（排查 hp=0 串线）
        try:
            if not getattr(self, "_diag_cnt", None):
                self._diag_cnt = 0
            self._diag_cnt += 1
            if self._diag_cnt % 30 == 1:
                p = resp.get("player", {}) if isinstance(resp, dict) else None
                print(f"[diag] get_state resp: type={resp.get('type','?') if isinstance(resp, dict) else '?'} "
                      f"req_id={resp.get('req_id','?') if isinstance(resp, dict) else '?'} "
                      f"found={resp.get('found','?') if isinstance(resp, dict) else '?'} "
                      f"player.hp={p.get('hp','?') if p else '?'} "
                      f"player.x={p.get('x','?') if p else '?'} "
                      f"player.tileX={p.get('tileX','?') if p else '?'} "
                      f"keys={list(resp.keys())[:6] if isinstance(resp, dict) else resp}")
        except Exception:
            pass
        if not resp or resp.get("found") is False:
            return {}
        # 将 mod 原生字段映射为 Python 习惯字段，供大脑/战斗统一使用
        p = resp.get("player", {})
        npcs = resp.get("nearbyNpcs", [])
        out = {
            "hp": p.get("hp", 0), "mp": p.get("mana", 0),
            "max_life": p.get("maxLife", 0), "max_mp": p.get("maxMana", 0),
            "life": p.get("hp", 0),
            "x": p.get("x", 0), "y": p.get("y", 0),
            "tile_x": p.get("tileX", 0), "tile_y": p.get("tileY", 0),
            "alive": p.get("alive", p.get("hp", 0) > 0),
            "velocity_x": p.get("velocityX", 0), "velocity_y": p.get("velocityY", 0),
            "grounded": p.get("grounded", True),
            "in_water": bool(p.get("in_water", False)),
            "in_lava": bool(p.get("in_lava", False)),
            "in_honey": bool(p.get("in_honey", False)),
            "breath": int(p.get("breath", 200) or 0),
            "nearby_npcs": [
                {"name": n.get("name", ""), "slot": n.get("slot", 0),
                 "type": n.get("type", 0), "life": n.get("life", 0),
                 "alive": n.get("alive", n.get("life", 0) > 0),
                 "friendly": n.get("friendly", False), "townNPC": n.get("townNPC", False),
                 "tile_x": n.get("tileX", 0), "tile_y": n.get("tileY", 0),
                 "damage": n.get("damage", 0)}
                for n in npcs
            ],
            "nearby_players": [
                {"name": pl.get("name", ""), "tile_x": pl.get("tileX", 0),
                 "tile_y": pl.get("tileY", 0),
                 "hp": pl.get("hp", 0), "max_life": pl.get("max_life", 0),
                 "velocity_x": pl.get("velocityX", 0), "velocity_y": pl.get("velocityY", 0)}
                for pl in resp.get("nearbyPlayers", [])
            ],
            "biome": p.get("biome", ""), "buffs": p.get("buffs", []),
            "movement_state": p.get("movement_state", ""),
            "brightness": p.get("brightness", 1.0),
            "time_of_day": "白天" if resp.get("time", {}).get("dayTime", True) else "夜晚",
        }
        return out

    async def enum_items(self) -> List[Dict[str, Any]]:
        # 物品全表很大，给足超时
        resp = await self.conn.request_mod({"cmd": "enum_items"}, timeout=15.0)
        return resp.get("mods", []) if resp else []

    async def get_server_info(self) -> Dict[str, Any]:
        """查询 Mod 端的服务器环境信息：版本、难度、世界大小、邪恶类型等。
        返回 dict，字段:
          tmod_version_str  Packet 1 需要的完整版本字符串
          tmod_version      tML 版本号 (如 "2026.6.3.0")
          terraria_version  Terraria 版本号
          game_mode         世界难度 (0=Classic,1=Expert,2=Master,3=Journey)
          world_size        Small/Medium/Large
          evil_type         Corruption/Crimson
          world_name        世界名称
        """
        resp = await self.conn.request_mod({"cmd": "get_server_info"}, timeout=5.0)
        return resp if resp else {}

    async def join_server(self, host: str, port: int, password: str = "",
                          character_name: str = "",
                          wait_confirm: bool = False, confirm_timeout: int = 30) -> bool:
        """通过 Mod 加入多人服务器。

        character_name: 目标角色名——mod 侧会精确匹配（含自动重命名第一个 .plr），
                        确保角色文件存在且 fresh Player 命名正确。
        wait_confirm=False: 只发起连接（旧行为兼容），返回 ACK ok 即成功。
        wait_confirm=True:  发起后轮询 join_status，等待真实入服确认（模仿 Terraria-Bot 状态机）。
        """
        cmd = {"cmd": "join_server", "host": host, "port": port}
        if character_name:
            cmd["character_name"] = character_name
        if password:
            cmd["password"] = password
        resp = await self.conn.request_mod(cmd, timeout=5.0)
        ack_ok = bool(resp and resp.get("ok"))
        if not wait_confirm:
            return ack_ok

        # wait_confirm：不依赖 ACK（可能因读循环重建/网络抖动丢失），
        # 命令发出后直接轮询 join_status，以真实入服为准
        import asyncio
        consecutive_fail = 0
        for attempt in range(confirm_timeout * 4):  # 每 250ms 查一次
            await asyncio.sleep(0.25)
            status = await self.join_status()
            if not status:
                # 响应连续超时说明连接可能已断，提前返回让 agent 强制重连
                consecutive_fail += 1
                if consecutive_fail >= 4:
                    return False
                continue
            consecutive_fail = 0
            if status.get("joined") or status.get("in_world"):
                return True
            if not status.get("pending") and attempt > 60:  # 15s 后如果 pending=false 还没入服，失败了
                return False
        return False

    async def join_status(self) -> Dict[str, Any]:
        """查询 join 进度——模仿 Terraria-Bot 的状态机轮询。
        返回: {phase, net_mode, in_world, connected, joined, pending, timeout, menu_mode, ...}"""
        resp = await self.conn.request_mod({"cmd": "join_status"}, timeout=3.0)
        return resp if resp else {}

    async def select_character(self, name: str = "", index: int = -1) -> bool:
        """通知 mod 选择指定角色。传 name 按文件名匹配，传 index 按索引，都不传则选第一个。"""
        cmd: Dict[str, Any] = {"cmd": "select_character"}
        if name:
            cmd["name"] = name
        if index >= 0:
            cmd["index"] = index
        resp = await self.conn.request_mod(cmd, timeout=5.0)
        return bool(resp and resp.get("ok"))

    async def get_network_info(self) -> Dict[str, Any]:
        """查询当前游戏的网络状态：是否在服务器上、作为主机还是客户端等"""
        resp = await self.conn.request_mod({"cmd": "get_network_info"}, timeout=3.0)
        return resp if resp else {}
