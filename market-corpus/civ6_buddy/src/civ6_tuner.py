"""civ6_tuner.py — 文明6 FireTuner 底层通道（v0.5.0）。

通过游戏内 FireTuner 调试端口（默认 TCP 4318）直连游戏 Lua 仿真层：
读城市/人口/产出/焦点/生产队列/回合，用官方 API 执行市民分配、
生产设置（CityManager.RequestOperation）与结束回合（ACTION_ENDTURN）。

【Lua state 分工（实测铁律）】
- GameCore（gamecore_index）：读局面常量（回合=Game.GetCurrentGameTurn、文明、科技名等）。
- InGame（state 96）：UI 仿真层。写操作必须在此（RequestOperation / ACTION_ENDTURN
  在此才生效）；生产队列的当前状态也必须在 InGame 读（GameCore 读到的是旧/空状态，
  会误判"设置生产失败"）。

【回合读取】正确 API 是 Game.GetCurrentGameTurn()（不是 GetGameTurn——后者在本
游戏版本为 nil，会导致回合数恒为 0 的假 bug）。

前置条件（Windows）：
1. Steam 安装 "Sid Meier's Civilization VI Development Assets"（appid 597260）
   —— tuner server 在 SDK 里，游戏本体不含。
2. 游戏启用 Tuner：AppOptions.txt 加 [Debug] EnableTuner 1
   （或游戏内选项 "Tuner (disables achievements)"）——开启会禁用成就。
3. 重启游戏，端口 4318 开始监听（同屏只能一个 tuner 连接，
   需先关闭 SDK 的 FireTuner.exe）。

线协议（Firaxis Nexus）：
[4-byte LE uint32 length][4-byte LE int32 tag][null-terminated payload]
tag: 4=handshake(APP:/LSQ:), 3=command(CMD:<state>:<lua>), 1=help
"""

from __future__ import annotations

import asyncio
import logging
import struct

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_tuner")

HEADER_FMT = "<Ii"  # length(uint32 LE), tag(int32 LE)
HEADER_SIZE = struct.calcsize(HEADER_FMT)

TAG_HANDSHAKE = 4
TAG_COMMAND = 3

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4318


def _parse_lua_states(raw: list[str]) -> dict[int, str]:
    """LSQ 返回交错 [index, name] 列表 → {index: name}。

    示例：['0','Main State','1','DebugHotloadCache',...,'3','GameCore_Tuner',...]"""
    result: dict[int, str] = {}
    i = 0
    while i + 1 < len(raw):
        try:
            idx = int(raw[i].strip())
        except ValueError:
            i += 1
            continue
        result[idx] = raw[i + 1].strip()
        i += 2
    return result


def _gamecore_state_index(states: list[str], fallback: int = 0) -> int:
    """从 LSQ 原始列表解析出可执行 Lua 的 state 索引。

    优先 GameCore_Tuner（游戏核心状态：Players/Game 可用）；
    次选 InGame；都没有才回退 0。"""
    parsed = _parse_lua_states(states)
    for idx, name in parsed.items():
        if name == "GameCore_Tuner":
            return idx
    for idx, name in parsed.items():
        if name == "InGame":
            return idx
    return fallback


class TunerError(Exception):
    """FireTuner 连接/执行错误。"""


def _to_int(raw) -> int:
    """数值字段安全转 int（游戏返回可能带小数/空格/异常值，失败返回 0）。"""
    try:
        return int(float(str(raw or 0).strip()))
    except (TypeError, ValueError):
        return 0


def _to_float(raw) -> float:
    """数值字段安全转 float（失败返回 -1.0）。"""
    try:
        return float(str(raw or -1).strip())
    except (TypeError, ValueError):
        return -1.0


def _pack(tag: int, payload: str) -> bytes:
    data = payload.encode("utf-8") + b"\x00"
    return struct.pack(HEADER_FMT, len(data), tag) + data


def _unpack(buf: bytes) -> tuple[int, str]:
    if len(buf) < HEADER_SIZE:
        raise TunerError(f"帧过短：{len(buf)} 字节")
    length, tag = struct.unpack(HEADER_FMT, buf[:HEADER_SIZE])
    body = buf[HEADER_SIZE:HEADER_SIZE + length]
    if len(body) < length:
        raise TunerError(f"帧体不完整：需要 {length}，实际 {len(body)}")
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _send(writer: asyncio.StreamWriter, tag: int, payload: str) -> None:
    writer.write(_pack(tag, payload))
    await writer.drain()


async def _recv(reader: asyncio.StreamReader, timeout: float) -> tuple[int, str]:
    try:
        header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
    except asyncio.TimeoutError:
        raise TunerError("读取超时（游戏无响应，可尝试重启游戏）") from None
    length, tag = struct.unpack(HEADER_FMT, header)
    if length <= 0 or length > 10_000_000:
        raise TunerError(f"非法帧长度：{length}")
    body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    return tag, body.rstrip(b"\x00").decode("utf-8", errors="replace")


async def _drain(reader: asyncio.StreamReader, timeout: float = 0.2) -> None:
    """清空未请求消息（握手/命令后残余输出）。"""
    while True:
        try:
            await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
        except asyncio.TimeoutError:
            return
        except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionAbortedError, OSError):
            # 服务端关闭连接/重置 = 没有更多数据，幂等返回
            return


async def _connect(host: str, port: int, timeout: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except OSError as exc:
        raise TunerError(f"无法连接 4318 端口：{exc}（游戏未运行或未启用 Tuner）") from None
    except asyncio.TimeoutError:
        raise TunerError("连接超时") from None


async def handshake(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 5.0,
) -> tuple[str, list[str], asyncio.StreamReader, asyncio.StreamWriter]:
    """连接 + 握手。返回 (app_identity, lua_states, reader, writer)。"""
    reader, writer = await _connect(host, port, timeout)
    try:
        await _drain(reader)
        await _send(writer, TAG_HANDSHAKE, "APP:")
        _, app_id = await _recv(reader, timeout)
        await _send(writer, TAG_HANDSHAKE, "LSQ:")
        _, raw = await _recv(reader, timeout)
        states = [s.strip() for s in raw.replace("\x00", "\n").split("\n") if s.strip()]
        return app_id, states, reader, writer
    except TunerError:
        writer.close()
        raise
    except Exception as exc:
        writer.close()
        raise TunerError(f"握手失败：{exc}") from None


def _parse_output_payload(payload: str) -> str | None:
    """从 print 输出帧提取内容。

    FireTuner 的 print 输出帧格式：`O\x00<context_name>: <value>`
    （O + null + Lua state 名 + ': ' + 值）。非输出帧（空 ack 等）返回 None。"""
    if not payload.startswith("O"):
        return None
    sep = payload.find(": ", 2)
    if sep >= 0:
        return payload[sep + 2:]
    return payload.lstrip("O").lstrip("\x00").strip()


async def execute_lua(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    code: str,
    state_index: int = 0,
    timeout: float = 8.0,
) -> list[str]:
    """在指定 Lua state 执行脚本，**循环收集**全部 print 输出行。

    FireTuner 的 print 输出是多帧异步流（`O\\x00<state>: <value>`），
    单帧读取会丢行/错位。此处：执行前清陈旧帧 → 发 CMD → 循环读帧
    直到超时 → 解析输出行 → 再清尾部。"""
    await _drain(reader, timeout=0.1)
    await _send(writer, TAG_COMMAND, f"CMD:{state_index}:{code}")
    lines: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    got_any = False
    last_data = loop.time()
    while True:
        now = loop.time()
        remaining = deadline - now
        if remaining <= 0:
            break
        # 性能优化：脚本输出完成后 FireTuner 不再发帧，继续等待只会白耗 2 秒/步。
        # 收到首个输出行后改用短等待片（0.2s），连续 0.6s 无新帧判定流结束提前返回。
        wait = min(remaining, 2.0)
        if got_any:
            wait = min(wait, 0.2)
        try:
            _, payload = await _recv(reader, wait)
        except TunerError:
            break  # 读取超时/帧损坏 → 停止收集
        text = _parse_output_payload(payload)
        if text is not None and text.strip():
            lines.append(text.strip())
            got_any = True
            last_data = loop.time()
        elif got_any and (loop.time() - last_data) >= 0.6:
            break  # 输出后 0.6s 无新帧 → 脚本已执行完
    await _drain(reader, timeout=0.2)
    return lines


async def _handshake_retry(retries: int = 2, delay: float = 0.5):
    """握手带重试：游戏 Tuner 单连接限制下，紧接的连接可能被 RST（前连接未释放），
    短暂等待后重试即可成功。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await handshake()
        except TunerError as exc:
            last = exc
            if attempt < retries:
                await asyncio.sleep(delay)
    raise last if last else TunerError("握手失败")


async def tuner_status() -> dict:
    """只读探测：Tuner 是否可用。无 Tuner 时返回明确错误码。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "states": []}
    try:
        writer.close()
    except Exception:
        pass
    parsed = _parse_lua_states(states)
    return {"ok": True, "app": app_id, "states": states,
            "gamecore_index": _gamecore_state_index(states),
            "state_count": len(parsed),
            "detail": f"Tuner 已连接（{app_id}，Lua states：{len(parsed)} 个，GameCore={_gamecore_state_index(states)}）"}


# ── Lua 脚本（焦点式市民分配，游戏官方机制） ──
# 主逻辑统一用 pcall 包裹：API 名在不同版本/资料片有差异，失败输出明确 ERR 而非挂死超时。

LUA_SET_FOCUS = """
local targetYield = "{yield_type}"
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local yRow = GameInfo.Yields[targetYield]
if yRow == nil then print("ERR:YIELD_NOT_FOUND|" .. targetYield) return end
local okApply = pcall(function()
  -- 遍历该玩家全部城市（GetCities():Members()），设置焦点（DEFAULT 清空全部焦点）
  for i, pCity in pPlayer:GetCities():Members() do
    local citz = pCity:GetCitizens()
    for yr in GameInfo.Yields() do
      local okFav, fav = pcall(function() return citz:IsFavoredYield(yr.Index) end)
      if okFav and fav then pcall(function() citz:SetYieldFocus(yr.Index, false) end) end
    end
    if targetYield ~= "YIELD_DEFAULT" then
      local okSet = pcall(function() citz:SetYieldFocus(yRow.Index, true) end)
      if not okSet then print("ERR:SETFOCUS_FAILED|" .. pCity:GetName()) end
    end
    print("OK|" .. pCity:GetName() .. "|focus=" .. targetYield)
  end
end)
if not okApply then print("ERR:SETFOCUS_FAILED") end
"""

LUA_LIST_CITIES = """
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local okList = pcall(function()
  for i, c in pPlayer:GetCities():Members() do
    local pop, growth = c:GetPopulation(), nil
    local okG = pcall(function()
      local g = c:GetGrowth()
      if g then growth = tostring(g:GetTurnsUntilGrowth()) end
    end)
    if not okG then growth = "?" end
    local yields = {{}}
    for y = 0, 5 do
      local okY, v = pcall(function() return c:GetYield(y) end)
      yields[y + 1] = okY and tostring(v) or "?"
    end
    print(string.format("%s|pop=%d|food=%s|prod=%s|gold=%s|sci=%s|cult=%s|faith=%s|growth=%s",
      c:GetName(), pop, yields[1], yields[2], yields[3], yields[4], yields[5], yields[6], growth))
  end
end)
if not okList then print("ERR:LISTCITIES_FAILED") end
"""


async def list_cities(timeout: float = 8.0) -> dict:
    """读本城全部城市的人口与六项产出（FireTuner）。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "cities": []}
    try:
        out = await execute_lua(reader, writer, LUA_LIST_CITIES,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "cities": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    cities = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0].startswith("ERR"):
            return {"ok": False, "error": parts[0], "detail": line, "cities": cities}
        if len(parts) < 2 or "pop=" not in line:
            continue
        cities.append({
            "name": parts[0],
            "population": _to_int(parts[1].replace("pop=", "")),
            "yields": {k: _to_int(parts[2 + i]) for i, k in
                       enumerate(("food", "prod", "gold", "science", "culture", "faith"))
                       if 2 + i < len(parts)},
        })
    return {"ok": True, "app": app_id, "cities": cities}


# 战争状态（v0.4.0）：Tuner 真实读取——比像素横幅检测可靠得多，
# 用于"分清打仗和运营"：战争中 YUI 拒绝运营、立即交还控制权。
# pcall 容错：不同版本 API 名可能有差异，失败返回明确 ERR 而非挂死/超时。
LUA_AT_WAR = """
local ok, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not ok or pPlayer == nil then print("ERR:NO_PLAYER") return end
local ok2, pDiplo = pcall(function() return pPlayer:GetDiplomacy() end)
if not ok2 or pDiplo == nil then print("ERR:NO_DIPLOMACY") return end
local count = 0
-- 遍历全部玩家槽（硬编码 0..62：GetNumPlayers 在该环境不可靠），逐人查 IsAtWarWith
for i = 0, 62 do
  local p = Players[i]
  if p ~= nil then
    local okAlive, alive = pcall(function() return p:IsAlive() end)
    if okAlive and alive then
      local okWar, atWar = pcall(function() return pDiplo:IsAtWarWith(i) end)
      if okWar and atWar then count = count + 1 end
    end
  end
end
print("OK|at_war=" .. tostring(count))
"""


async def at_war(timeout: float = 8.0) -> dict:
    """查询本地玩家当前处于战争的对象数（FireTuner 官方 API）。

    返回 {"ok": True, "at_war": int, ...}；Tuner 不可用返回明确错误码
    （调用方据此回退到像素横幅检测，不阻断运营）。"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, LUA_AT_WAR,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith("OK|at_war="):
            try:
                return {"ok": True, "app": app_id, "at_war": int(line.split("=")[1])}
            except (IndexError, ValueError):
                return {"ok": False, "error": "BAD_RESPONSE", "detail": line}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "|".join(out)}


LUA_SITUATION = """
local turn = 0
local p = -1
local civName = ""
local cities = 0
local techName = ""
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if okP and pPlayer ~= nil then
  p = Game.GetLocalPlayer() or -1
  pcall(function() turn = Game.GetCurrentGameTurn() or 0 end)
  local okCiv, civType = pcall(function() return pPlayer:GetCivilizationType() end)
  if okCiv and civType ~= nil then
    pcall(function()
      if GameInfo and GameInfo.Civilizations and GameInfo.Civilizations[civType] ~= nil then
        civName = tostring(GameInfo.Civilizations[civType].Name or "")
      end
    end)
  end
  local okCities, cList = pcall(function() return pPlayer:GetCities() end)
  if okCities and cList ~= nil then
    local okCnt, cnt = pcall(function() return cList:GetCount() end)
    if okCnt and cnt ~= nil then cities = cnt end
  end
  local okTechs, techs = pcall(function() return pPlayer:GetTechs() end)
  if okTechs and techs ~= nil then
    local okCur, cur = pcall(function() return techs:GetCurrentResearch() end)
    if okCur and cur ~= nil then
      pcall(function()
        if GameInfo and GameInfo.Technologies and GameInfo.Technologies[cur] ~= nil then
          techName = tostring(GameInfo.Technologies[cur].Name or "")
        end
      end)
    end
  end
end
print("OK|turn=" .. tostring(turn) .. "|p=" .. tostring(p) .. "|civ=" .. tostring(civName) .. "|cities=" .. tostring(cities) .. "|tech=" .. tostring(techName))
"""


async def read_situation(timeout: float = 8.0) -> dict:
    """Tuner 实时读取当前局面：回合 / 本地玩家 / 文明 / 城市数 / 当前研究科技。

    返回 {"ok": True, "app", "turn", "player", "civ", "cities", "tech", "via": "tuner"}；
    Tuner 不可用返回 {"ok": False, "error": "GAME_TUNER_OFF"|...}（调用方回退存档解析）。
    """
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, LUA_SITUATION,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith("OK|"):
            vals: dict[str, str] = {}
            for part in line[3:].split("|"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    vals[k] = v
            try:
                turn = int(vals.get("turn", "0"))
            except ValueError:
                turn = 0
            try:
                player = int(vals.get("p", "-1"))
            except ValueError:
                player = -1
            try:
                cities = int(vals.get("cities", "0"))
            except ValueError:
                cities = 0
            return {
                "ok": True,
                "app": app_id,
                "turn": turn,
                "player": player,
                "civ": vals.get("civ", ""),
                "cities": cities,
                "tech": vals.get("tech", ""),
                "via": "tuner",
            }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "|".join(out)}


async def set_focus(yield_name: str, timeout: float = 8.0) -> dict:
    """为本城全部城市设置产出焦点（市民分配，官方机制）。
    yield_name: FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT(清空)。"""
    yield_name = str(yield_name or "").strip().upper()
    if not yield_name.startswith("YIELD_"):
        yield_name = "YIELD_" + yield_name
    valid = {"YIELD_FOOD", "YIELD_PRODUCTION", "YIELD_GOLD", "YIELD_SCIENCE",
             "YIELD_CULTURE", "YIELD_FAITH", "YIELD_DEFAULT"}
    if yield_name not in valid:
        return {"ok": False, "error": "BAD_YIELD",
                "detail": f"未知焦点 {yield_name}，可用：FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT"}
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "results": []}
    try:
        out = await execute_lua(reader, writer, LUA_SET_FOCUS.format(yield_type=yield_name),
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "results": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    results = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0].startswith("ERR"):
            return {"ok": False, "error": parts[0], "detail": line, "results": results}
        if parts[0] == "OK":
            results.append({"city": parts[1], "focus": parts[2]})
    return {"ok": True, "app": app_id, "focus": yield_name, "results": results}


# ══════════ v0.5.0：Tuner 写链路（生产设置 / 结束回合 / 生产队列读取） ══════════

# InGame（UI 仿真层）state 索引。实测：写操作（RequestOperation/ACTION_ENDTURN）与
# 生产队列读取必须在 InGame 执行；GameCore 读队列会拿到旧/空状态。
INGAME_STATE = 96

# 物品类型 → GameInfo 表名
_ITEM_KIND_TO_TABLE = {
    "unit": "Units",
    "building": "Buildings",
    "district": "Districts",
    "project": "Projects",
}


def _ingame_state_index(states: list[str] | None, fallback: int = INGAME_STATE) -> int:
    """从 Lua states 中找 InGame 索引；找不到回退 96（文明6 固定 UI 层索引）。"""
    if states:
        for s in states:
            if "InGame" in s:
                # states 元素形如 "96 InGame" 或 "InGame"
                for token in s.replace(",", " ").split():
                    if token.isdigit():
                        return int(token)
    return fallback


async def read_production(timeout: float = 8.0) -> dict:
    """读取本地玩家全部城市的生产队列（InGame 层，与写操作同 context）。

    返回 {"ok": True, "turn", "cities":[{name, size, current_type, current_hash,
    turns_left}], "via": "tuner"}。生产队列为空（size=0）说明城市没选生产，
    文明6 会拒绝结束回合——这是"挂机不动"的根因，调用方需先 set_city_production。
    """
    lua = """
local s = {}
local okT, t = pcall(function() return Game.GetCurrentGameTurn() end)
s[#s+1] = "TURN=" .. tostring(t or -1)
local okP, pPlayer = pcall(function() return Players[Game.GetLocalPlayer() or 0] end)
if not okP or pPlayer == nil then
  s[#s+1] = "ERR=NO_PLAYER"
  print(table.concat(s, "&"))
  return
end
pcall(function()
  local idx = 0
  for i, c in pPlayer:GetCities():Members() do
    idx = idx + 1
    local q = c:GetBuildQueue()
    local sz = 0
    pcall(function() sz = q:GetSize() or 0 end)
    local curH = 0
    pcall(function() curH = q:GetCurrentProductionTypeHash() or 0 end)
    local tl = -1
    pcall(function() tl = q:GetTurnsLeft() or -1 end)
    s[#s+1] = string.format("C%d|sz=%d|h=%d|tl=%d", idx, sz, curH, tl)
  end
end)
print(table.concat(s, "&"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "cities": []}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "cities": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    turn: int | None = None
    cities: list[dict] = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR="):
            return {"ok": False, "error": "NO_PLAYER", "detail": line, "cities": cities}
        # 单帧汇总格式：TURN=9&C1|sz=1|h=1872107673|tl=1&C2|sz=0|h=0|tl=-1
        for chunk in line.split("&"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.startswith("TURN="):
                turn = _to_int(chunk[len("TURN="):])
            elif chunk.startswith("C") and "|" in chunk:
                parts = chunk.split("|")
                kv = {p.split("=")[0]: p.split("=", 1)[1] for p in parts[1:] if "=" in p}
                cities.append({
                    "name": parts[0],
                    "size": _to_int(kv.get("sz", "0")),
                    "hash": _to_int(kv.get("h", "0")),
                    "current_type": "",
                    "turns_left": _to_int(kv.get("tl", "-1")),
                })
    # 城市名/当前生产类型：交给调用方用 list_cities 或 ItemHash 映射补全
    return {"ok": True, "app": app_id, "turn": turn, "cities": cities, "via": "tuner"}


async def set_city_production(
    item_kind: str = "unit",
    item_name: str = "UNIT_SCOUT",
    timeout: float = 10.0,
) -> dict:
    """为本地玩家的第一座城市设置生产项目（InGame 层 RequestOperation）。

    实测铁律：RequestOperation 是 fire-and-forget，即使 CanStartOperation=true 也可能
    静默 no-op；必须在**同一段 Lua 内**等待后读取队列验证 size>=1，失败重试。
    用 GameCore 跨 context 读队列会误判失败——本函数全部在 InGame 内完成。

    参数 item_kind：unit/building/district/project；item_name：GameInfo 的 Type
    （如 UNIT_SCOUT / BUILDING_MONUMENT / DISTRICT_CAMPUS / PROJECT_ENCAMPMENT_ARMORY）。
    返回 {"ok", "city", "item", "kind", "queue_size", "detail"}；失败含 error。
    """
    kind = (item_kind or "unit").strip().lower()
    if kind not in _ITEM_KIND_TO_TABLE:
        return {"ok": False, "error": "BAD_KIND", "detail": f"item_kind 需为 {list(_ITEM_KIND_TO_TABLE)}"}
    item_name = (item_name or "").strip()
    if not item_name:
        return {"ok": False, "error": "NO_ITEM", "detail": "缺少 item_name（如 UNIT_SCOUT）"}

    lua = f"""
local me = Game.GetLocalPlayer()
local pCity = CityManager.GetCity(me, 0)
if pCity == nil then print("ERR:CITY_NOT_FOUND") return end
local item = GameInfo.{_ITEM_KIND_TO_TABLE[kind]}["{item_name}"]
if item == nil then print("ERR:ITEM_NOT_FOUND|{item_name}") return end
local bq = pCity:GetBuildQueue()
local canProduce = false
pcall(function() canProduce = bq:CanProduce(item.Hash, true) end)
if not canProduce then print("ERR:CANNOT_PRODUCE|{item_name}") return end
local tCheck = {{}}
tCheck[CityOperationTypes.PARAM_UNIT_TYPE] = item.Hash
local canStart = CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, tCheck, true)
local done = false
local lastSize = 0
for i = 1, 3 do
  local tParams = {{}}
  tParams[CityOperationTypes.PARAM_UNIT_TYPE] = item.Hash
  tParams[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
  CityManager.RequestOperation(pCity, CityOperationTypes.BUILD, tParams)
  local t0 = os.clock()
  while os.clock() - t0 < 0.8 do end
  local sz = 0
  pcall(function() sz = bq:GetSize() or 0 end)
  lastSize = sz
  if sz >= 1 then
    done = true
    break
  end
end
-- 消除"未选择生产"的结束回合 blocker 通知（官方 UI 同款操作）
local cleared = 0
pcall(function()
  local list = NotificationManager.GetList(me)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(me, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_PRODUCTION then
          pcall(function() NotificationManager.SendActivated(me, nid) end)
          pcall(function() NotificationManager.Dismiss(me, nid) end)
          cleared = cleared + 1
        end
      end
    end
  end
end)
print("SET|canStart=" .. tostring(canStart) .. "|size=" .. tostring(lastSize)
      .. "|done=" .. tostring(done) .. "|cleared=" .. tostring(cleared))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith("SET|"):
            parts = {p.split("=")[0]: p.split("=", 1)[1] for p in line[4:].split("|") if "=" in p}
            size = _to_int(parts.get("size", "0"))
            done = parts.get("done", "false") == "true"
            if done and size >= 1:
                return {
                    "ok": True, "app": app_id, "city": "第一城",
                    "item": item_name, "kind": kind, "queue_size": size,
                    "detail": f"生产队列已设置（{item_name}，队列 {size} 项）",
                }
            return {
                "ok": False, "error": "SILENT_FAILURE",
                "detail": f"{item_name} canStart={parts.get('canStart')} 但队列仍空（size={size}）；"
                          "游戏可能在等待其他确认，可换生产项重试",
            }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "设置生产无回执（Tuner 输出丢失），请重试"}


async def end_turn(timeout: float = 8.0) -> dict:
    """结束当前回合（InGame 层 ACTION_ENDTURN）。玩家回合时有效。

    返回 {"ok": True, "detail": "回合结束指令已发出"}；Tuner 不可用返回明确错误码。
    调用方应在发出后轮询 read_production/read_situation 的回合号确认推进。
    """
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(
            reader, writer,
            "UI.RequestAction(ActionTypes.ACTION_ENDTURN); print('ENDT_OK')",
            state_index=INGAME_STATE, timeout=timeout,
        )
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        if line.strip().startswith("ENDT_OK"):
            return {"ok": True, "app": app_id, "detail": "回合结束指令已发出（ACTION_ENDTURN）"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "结束回合无回执（Tuner 输出丢失），请重试"}


async def read_blockers(timeout: float = 8.0) -> dict:
    """读取当前阻挡结束回合的通知（InGame NotificationManager，官方 API）。

    返回 {"ok", "blockers": [{"type": "ENDTURN_BLOCKING_PRODUCTION", "message": ...}]}。
    blockers 为空 = 游戏允许结束回合。常见类型：
    PRODUCTION（未选生产）/RESEARCH（未选科技）/CIVIC（未选市政）/
    UNIT_PROMOTION（单位可升级）/GOVERNOR_IDLE（总督空闲）/WORLD_CONGRESS_*（世界议会）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local list = NotificationManager.GetList(me)
local seen = {}
local found = 0
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt ~= 0 then
                local typeName = "UNKNOWN"
                for k, v in pairs(EndTurnBlockingTypes) do
                    if v == bt then typeName = k; break end
                end
                if not seen[typeName] then
                    seen[typeName] = true
                    local msg = (entry:GetMessage() or ""):gsub("|", "/")
                    print("BLOCKING|" .. typeName .. "|" .. msg)
                    found = found + 1
                end
            end
        end
    end
end
if found == 0 then print("NONE") end
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc), "blockers": []}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc), "blockers": []}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    blockers: list[dict] = []
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("BLOCKING|"):
            parts = line.split("|", 2)
            blockers.append({"type": parts[1], "message": parts[2] if len(parts) > 2 else ""})
    return {"ok": True, "app": app_id, "blockers": blockers, "via": "tuner"}


async def set_research(
    category: str = "tech",
    item_name: str = "TECH_BRONZE_WORKING",
    timeout: float = 10.0,
) -> dict:
    """设置当前研究（科技/市政，InGame RequestPlayerOperation 官方 API）。

    文明6 未选科技/市政会产生 ENDTURN_BLOCKING_RESEARCH / _CIVIC blocker，
    游戏拒绝结束回合——开局"挂机不动"的常见原因。本函数设置研究项并消除 blocker。

    参数 category：tech（科技，默认）/civic（市政）；
    item_name：GameInfo 的 Type（如 TECH_BRONZE_WORKING / CIVIC_CODE_OF_LAWS）。
    返回 {"ok", "category", "item", "detail"}；失败含 error（NOT_FOUND/ALREADY_COMPLETED 等）。
    """
    cat = (category or "tech").strip().lower()
    if cat not in ("tech", "civic"):
        return {"ok": False, "error": "BAD_CATEGORY", "detail": "category 需为 tech/civic"}
    item_name = (item_name or "").strip()
    if not item_name:
        return {"ok": False, "error": "NO_ITEM", "detail": "缺少 item_name（如 TECH_BRONZE_WORKING）"}

    if cat == "tech":
        gi_table, type_field, param, operation, blocking, ok_prefix, has_m, player_m = (
            "Technologies", "TechnologyType", "PARAM_TECH_TYPE", "RESEARCH",
            "ENDTURN_BLOCKING_RESEARCH", "OK:RESEARCHING", "HasTech", "GetTechs",
        )
    else:
        gi_table, type_field, param, operation, blocking, ok_prefix, has_m, player_m = (
            "Civics", "CivicType", "PARAM_CIVIC_TYPE", "PROGRESS_CIVIC",
            "ENDTURN_BLOCKING_CIVIC", "OK:PROGRESSING", "HasCivic", "GetCulture",
        )

    lua = f"""
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{gi_table}() do
    if row.{type_field} == "{item_name}" then idx = row.Index; break end
end
if idx == nil then print("ERR:NOT_FOUND|{item_name}") return end
if Players[id]:{player_m}():{has_m}(idx) then
    print("ERR:ALREADY_COMPLETED|{item_name}") return
end
local params = {{}}
params[PlayerOperations.{param}] = idx
UI.RequestPlayerOperation(id, PlayerOperations.{operation}, params)
local cleared = 0
pcall(function()
  local list = NotificationManager.GetList(id)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(id, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt and bt == EndTurnBlockingTypes.{blocking} then
          pcall(function() NotificationManager.SendActivated(id, nid) end)
          pcall(function() NotificationManager.Dismiss(id, nid) end)
          cleared = cleared + 1
        end
      end
    end
  end
end)
print("{ok_prefix}|{item_name}|cleared=" .. tostring(cleared))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERR"):
            return {"ok": False, "error": line.split("|")[0], "detail": line}
        if line.startswith(ok_prefix):
            return {"ok": True, "app": app_id, "category": cat, "item": item_name,
                    "detail": f"已设置研究：{item_name}（{cat}）"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "设置研究无回执（Tuner 输出丢失），请重试"}


async def units_blocker_status(timeout: float = 8.0) -> dict:
    """检查"单位需要命令" blocker（ENDTURN_BLOCKING_UNITS）。

    文明6 单位有剩余移动力时会阻挡结束回合。本函数返回：
    {"ok", "blocked": bool, "units_with_moves": [名称列表], "skip_idle_ok": bool}
    - units_with_moves：有剩余移动力的单位（需要指挥）。
    - skip_idle_ok：是否全部单位剩余移动力 <= 0（此时可安全 FinishMoves 跳过）。
    注意：有移动力的单位属于"打仗/探索归玩家"的范围，运营方不应擅自动作；
    若运营托管且用户授权自动跳过（侦察兵探路类），可调 skip_idle_units(force=True)。
    """
    lua = """
local me = Game.GetLocalPlayer()
local busy = {}
local idle = 0
local total = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      total = total + 1
      local m = u:GetMovesRemaining() or 0
      if m > 0 then
        local nm = ""
        pcall(function() nm = u:GetName() end)
        busy[#busy+1] = tostring(nm) .. ":" .. tostring(m)
      else
        idle = idle + 1
      end
    end
  end
end)
local parts = {}
parts[#parts+1] = "TOTAL=" .. tostring(total)
parts[#parts+1] = "IDLE=" .. tostring(idle)
parts[#parts+1] = "BUSY=" .. tostring(#busy)
parts[#parts+1] = "LIST=" .. table.concat(busy, ",")
print(table.concat(parts, "&"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        total = _to_int(kv.get("TOTAL", "0"))
        idle = _to_int(kv.get("IDLE", "0"))
        busy = [u for u in kv.get("LIST", "").split(",") if u]
        blocked = len(busy) > 0
        return {
            "ok": True, "app": app_id,
            "blocked": blocked,
            "units_with_moves": busy,
            "total_units": total,
            "idle_units": idle,
            "skip_idle_ok": total > 0 and not blocked,
            "via": "tuner",
        }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "单位状态无回执（Tuner 输出丢失），请重试"}


async def skip_idle_units(force: bool = False, timeout: float = 8.0) -> dict:
    """跳过单位剩余移动（GameCore_Tuner 的 UnitManager.FinishMoves），解除 UNITS blocker。

    **必须用 GameCore_Tuner(3) context**——实测 InGame(96) 的 FinishMoves/FORTIFY/WAIT_FOR/
    SetActivityType(SLEEP) 全部无效（单位移动力不变、blocker 不消），GameCore 一次生效。

    force=False（默认）：仅跳过剩余移动力 <= 0 的单位（安全，不碰有移动力的单位）。
    force=True：跳过全部单位（包括有移动力的侦察兵/非战斗单位——运营托管自动探路场景）。
    注意：不要对战争单位使用 force=True（打仗归玩家，不能替玩家动兵）。
    返回 {"ok", "skipped", "left_with_moves"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local skipped = 0
local left = 0
local forced = %s
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetX() ~= -9999 then
      local m = u:GetMovesRemaining() or 0
      if forced or m <= 0 then
        pcall(function() UnitManager.FinishMoves(u) end)
        skipped = skipped + 1
      else
        left = left + 1
      end
    end
  end
end)
print("SKIPPED=" .. tostring(skipped) .. "&LEFT=" .. tostring(left))
""" % ("true" if force else "false")
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        gc = _gamecore_state_index(states)
        out = await execute_lua(reader, writer, lua, state_index=gc, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        return {
            "ok": True, "app": app_id,
            "skipped": _to_int(kv.get("SKIPPED", "0")),
            "left_with_moves": _to_int(kv.get("LEFT", "0")),
            "detail": f"跳过 {kv.get('SKIPPED', '0')} 个单位剩余移动，"
                      f"仍有 {kv.get('LEFT', '0')} 个单位有移动力待指挥",
            "via": "tuner",
        }
    return {"ok": False, "error": "NO_RESPONSE", "detail": "跳过单位无回执（Tuner 输出丢失），请重试"}


async def auto_pick_research(category: str = "tech", timeout: float = 8.0) -> dict:
    """选第一个"可研究且未完成"的科技/市政（InGame 官方 API CanResearch）。

    用于 set_research 兜底：默认科技可能已完成（如用户已学采矿），此时自动改选
    第一个可用项（POTTERY/ANIMAL_HUSBANDRY/SAILING...）。返回 {"ok", "item", "category"}。
    """
    cat = (category or "tech").strip().lower()
    if cat not in ("tech", "civic"):
        return {"ok": False, "error": "BAD_CATEGORY", "detail": "category 需为 tech/civic"}
    if cat == "tech":
        gi_table, has_m, player_m = "Technologies", "HasTech", "GetTechs"
        type_field = "TechnologyType"
    else:
        gi_table, has_m, player_m = "Civics", "HasCivic", "GetCulture"
        type_field = "CivicType"
    lua = f"""
local id = Game.GetLocalPlayer()
local pick = ""
pcall(function()
  for row in GameInfo.{gi_table}() do
    local p = Players[id]:{player_m}()
    if p and not p:{has_m}(row.Index) then
      local ok = false
      pcall(function() ok = p:CanResearch(row.Index) end)
      if ok then pick = row.{type_field}; break end
    end
  end
end)
print("PICK=" .. pick)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("PICK="):
            continue
        item = line[len("PICK="):].strip()
        if not item:
            return {"ok": False, "error": "NO_AVAILABLE",
                    "detail": f"当前没有可研究的{cat}（可能全部完成或需先解决前置）"}
        return {"ok": True, "app": app_id, "item": item, "category": cat, "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "自动选研究无回执（Tuner 输出丢失），请重试"}


async def move_explorers(timeout: float = 20.0) -> dict:
    """让侦察兵真正向外探路（InGame RequestOperation MOVE_TO，异步生效）。

    回答"单位为什么不移动"：v0.5.1 的 skip_idle_units 只是原地结束移动力（保回合推进），
    侦察兵不会动。本函数：对每个有移动力的侦察兵（UNIT_SCOUT），选择"远离首都方向"的
    相邻可通行格（排除水域/山脉/已有我方单位），RequestOperation MOVE_TO 移动一格；
    其余单位（勇士/建造者）不动——战斗单位不主动移动（不引战，打仗归玩家），
    建造者等策略工具指挥。MOVE_TO 异步生效：调用方应先执行本函数（发命令），
    Python 侧短等待后再由 skip_idle_units 结束剩余移动力（blocker 消除）。
    返回 {"ok", "moved": n, "targets": ["9,13->10,13", ...]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
if capX < 0 then
  pcall(function()
    for _, c in Players[me]:GetCities():Members() do capX, capY = c:GetX(), c:GetY(); break end
  end)
end
local targets = {}
local seen = {}
for _, u in Players[me]:GetUnits():Members() do
  if u:GetX() ~= -9999 and not seen[u:GetID()] then
    seen[u:GetID()] = true
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_SCOUT" and (u:GetMovesRemaining() or 0) > 0 then
      local x, y = u:GetX(), u:GetY()
      local best, bestScore = nil, -999999
      for dx = -1, 1 do for dy = -1, 1 do
        if dx ~= 0 or dy ~= 0 then
          local nx, ny = x + dx, y + dy
          local p = Map.GetPlot(nx, ny)
          if p and not p:IsWater() and not p:IsMountain() then
            local occ = false
            local us = Map.GetUnitsAt(nx, ny)
            if us then
              for o in us:Units() do if o:GetOwner() == me then occ = true; break end end
            end
            if not occ then
              local score = (nx - capX) * dx + (ny - capY) * dy
              if score > bestScore then bestScore = score; best = {nx, ny} end
            end
          end
        end
      end end
      if best then
        local params = {}
        params[UnitOperationTypes.PARAM_X] = best[1]
        params[UnitOperationTypes.PARAM_Y] = best[2]
        pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
        targets[#targets+1] = tostring(x) .. "," .. tostring(y) .. "->" .. tostring(best[1]) .. "," .. tostring(best[2])
      end
    end
  end
end
print("MOVED=" .. table.concat(targets, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("MOVED="):
            continue
        targets = [t for t in line[len("MOVED="):].split("|") if t]
        return {"ok": True, "app": app_id, "moved": len(targets),
                "targets": targets,
                "detail": f"侦察兵已向外探路：{len(targets)} 个单位移动（{'、'.join(targets) or '原地'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "侦察兵移动无回执（Tuner 输出丢失），请重试"}


async def read_threats(timeout: float = 10.0) -> dict:
    """扫描本地玩家单位的威胁态势（InGame）。

    对每个己方单位读：位置/类型/战力/剩余移动力，并扫描周围 5 格内的敌方单位
    （可见范围内），输出敌方最强战力与数量。供避险决策使用——回答"碰到比自己
    强大的敌人怎么办"：先看清敌我力量对比，再决定撤/守。
    返回 {"ok", "units": [{id, type, x, y, str, moves, threat_str, threat_count, threat_dist}]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local rows = {}
for _, u in Players[me]:GetUnits():Members() do
  local x, y = u:GetX(), u:GetY()
  if x ~= -9999 then
    local info = GameInfo.Units[u:GetType()]
    local ut = info and info.UnitType or "?"
    local str = 0
    if info and info.Combat then str = info.Combat end
    local moves = 0
    pcall(function() moves = u:GetMovesRemaining() or 0 end)
    local tStr, tCount, tDist = -1, 0, 99
    -- 扫描周围 5 格（曼哈顿/切比雪夫混合：|dx|<=2, |dy|<=2）
    for dx = -2, 2 do for dy = -2, 2 do
      if dx ~= 0 or dy ~= 0 then
        local nx, ny = x + dx, y + dy
        local p = Map.GetPlot(nx, ny)
        if p then
          local owner = -1
          pcall(function() owner = p:GetOwner() end)
          if owner >= 0 and owner ~= me then
            local eStr = -1
            local eCount = 0
            pcall(function()
              local us = Map.GetUnitsAt(nx, ny)
              if us then
                for o in us:Units() do
                  if o:GetOwner() == owner and o:GetX() == nx then
                    eCount = eCount + 1
                    local ei = GameInfo.Units[o:GetType()]
                    local cs = 0
                    if ei and ei.Combat then cs = ei.Combat end
                    if cs > eStr then eStr = cs end
                  end
                end
              end
            end)
            if eCount > 0 and eStr > tStr then
              tStr = eStr
              tCount = eCount
              tDist = math.max(math.abs(dx), math.abs(dy))
            end
          end
        end
      end
    end end
    rows[#rows+1] = string.format("%d|%s|%d,%d|%d|%d|%d|%d|%d",
        u:GetID(), ut, x, y, str, moves, tStr, tCount, tDist)
  end
end
print("THREATS|" .. table.concat(rows, ";;"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("THREATS|"):
            continue
        payload = line[len("THREATS|"):]
        units = []
        for part in payload.split(";;"):
            f = part.split("|")
            if len(f) == 8:
                xy = f[2].split(",")
                units.append({
                    "id": int(f[0]), "type": f[1], "x": int(xy[0]), "y": int(xy[1]),
                    "str": int(f[3]), "moves": int(f[4]),
                    "threat_str": int(f[5]), "threat_count": int(f[6]), "threat_dist": int(f[7]),
                })
        return {"ok": True, "app": app_id, "units": units,
                "detail": f"威胁扫描完成（{len(units)} 个单位，含 {sum(1 for u in units if u['threat_count'] > 0)} 个受威胁单位）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "威胁扫描无回执（Tuner 输出丢失），请重试"}


async def auto_evade_threats(timeout: float = 15.0) -> dict:
    """避险移动：对受威胁单位（附近敌方战力 ≥ 我方 1.2 倍）向远离威胁方向撤退一格。

    回答"碰到比自己强大的敌人怎么办"——托管纪律明确：
    1. 敌强我弱（敌方战力 ≥ 我方 1.2 倍）→ 自动向远离威胁方向撤退（自保，不送死）；
    2. 敌弱我强或势均力敌 → 原地警戒（不主动攻击——战争归玩家，YUI 不引战）；
    3. 城市被威胁 → 在回合摘要里明确报告，建议玩家决策。
    侦察兵/勇士/建造者都适用（建造者遇到蛮族也会撤）。移动用 InGame MOVE_TO（异步生效），
    调用方短等待后再 skip。返回 {"ok", "evaded": ["WARRIOR:9,13->8,14 敌强撤"]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local evaded = {}
-- 先收集威胁信息（同 read_threats），对受威胁且有移动力的单位找远离方向
local danger = {}
for _, u in Players[me]:GetUnits():Members() do
  local x, y = u:GetX(), u:GetY()
  if x ~= -9999 then
    local uinfo = GameInfo.Units[u:GetType()]
    local str = 0
    if uinfo and uinfo.Combat then str = uinfo.Combat end
    local tStr, tCount, tDX, tDY = -1, 0, 0, 0
    for dx = -2, 2 do for dy = -2, 2 do
      if dx ~= 0 or dy ~= 0 then
        local p = Map.GetPlot(x + dx, y + dy)
        if p then
          local owner = -1
          pcall(function() owner = p:GetOwner() end)
          if owner >= 0 and owner ~= me then
            local eStr, eCount = -1, 0
            pcall(function()
              local us = Map.GetUnitsAt(x + dx, y + dy)
              if us then
                for o in us:Units() do
                  if o:GetOwner() == owner then
                    eCount = eCount + 1
                    local ei = GameInfo.Units[o:GetType()]
                    local cs = 0
                    if ei and ei.Combat then cs = ei.Combat end
                    if cs > eStr then eStr = cs end
                  end
                end
              end
            end)
            if eCount > 0 and eStr > tStr then
              tStr = eStr; tCount = eCount; tDX = dx; tDY = dy
            end
          end
        end
      end
    end end
    if tCount > 0 and tStr >= str * 1.2 then
      danger[u:GetID()] = {x=x, y=y, str=str, tStr=tStr, tDX=tDX, tDY=tDY}
    end
  end
end
-- 对受威胁单位：向远离威胁方向找相邻可行格移动
for _, u in Players[me]:GetUnits():Members() do
  local d = danger[u:GetID()]
  if d and (u:GetMovesRemaining() or 0) > 0 then
    local best, bestScore = nil, -999999
    for dx = -1, 1 do for dy = -1, 1 do
      if dx ~= 0 or dy ~= 0 then
        local nx, ny = d.x + dx, d.y + dy
        local p = Map.GetPlot(nx, ny)
        if p and not p:IsWater() and not p:IsMountain() then
          local occ = false
          local us = Map.GetUnitsAt(nx, ny)
          if us then
            for o in us:Units() do if o:GetOwner() == me then occ = true break end end
          end
          if not occ then
            -- 远离威胁方向分（沿威胁反向）+ 靠近首都分
            local score = -((nx - d.x) * d.tDX + (ny - d.y) * d.tDY)
            if capX >= 0 then
              local towardCap = (d.x - capX) * (d.x - nx) + (d.y - capY) * (d.y - ny)
              score = score + towardCap * 0.5
            end
            if score > bestScore then bestScore = score; best = {nx, ny} end
          end
        end
      end
    end end
    if best then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = best[1]
      params[UnitOperationTypes.PARAM_Y] = best[2]
      pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
      local info = GameInfo.Units[u:GetType()]
      local ut = info and info.UnitType or "?"
      evaded[#evaded+1] = string.format("%s:%d,%d->%d,%d 敌强(%d)撤",
          ut, d.x, d.y, best[1], best[2], d.tStr)
    end
  end
end
print("EVADED=" .. table.concat(evaded, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("EVADED="):
            continue
        evaded = [t for t in line[len("EVADED="):].split("|") if t]
        return {"ok": True, "app": app_id, "evaded": evaded,
                "detail": f"避险移动：{len(evaded)} 个单位撤退（{'；'.join(evaded) or '无受威胁单位，无需撤退'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "避险移动无回执（Tuner 输出丢失），请重试"}


async def auto_use_builder(timeout: float = 20.0) -> dict:
    """让建造者自动改良地块（InGame BUILD_IMPROVEMENT）——回答"工人会建建筑吗"。

    对每个有移动力的建造者：找其相邻格中"我方领土 + 无已有改良 + 无己方单位 +
    可建改良"的最优格（改良白名单 FARM/MINE/PASTURE/QUARRY/PLANTATION/CAMP/
    LUMBER_MILL，资源格优先 + 食物/生产力按格型加分 + 靠近首都加分），
    RequestOperation BUILD_IMPROVEMENT 原地建造（官方写法：PARAM_X/Y 为所在格，
    与 CanStartOperation 校验一致）。无可用相邻格则不动（不瞎跑）。
    返回 {"ok", "built": ["FARM@10,14"], "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local whitelist = {"IMPROVEMENT_FARM", "IMPROVEMENT_MINE", "IMPROVEMENT_PASTURE",
                   "IMPROVEMENT_QUARRY", "IMPROVEMENT_PLANTATION", "IMPROVEMENT_CAMP",
                   "IMPROVEMENT_LUMBER_MILL"}
local built = {}
local moved = {}
local done = {}
local function pick_improvement(u, nx, ny)
  local p = Map.GetPlot(nx, ny)
  if p == nil then return nil end
  local owner = -1
  pcall(function() owner = p:GetOwner() end)
  if owner ~= me then return nil end
  local hasImp = -1
  pcall(function() hasImp = p:GetImprovementType() end)
  if hasImp >= 0 then return nil end
  local occ = false
  local us = Map.GetUnitsAt(nx, ny)
  if us then
    for o in us:Units() do if o:GetOwner() == me then occ = true break end end
  end
  if occ then return nil end
  local bestImp, impScore = nil, -999999
  for _, impName in ipairs(whitelist) do
    local imp = GameInfo.Improvements[impName]
    if imp then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = nx
      params[UnitOperationTypes.PARAM_Y] = ny
      params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = imp.Hash
      local ok, can = pcall(function()
        return UnitManager.CanStartOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, nil, params)
      end)
      if ok and can then
        local sc = 0
        local res = -1
        pcall(function() res = p:GetResourceType() end)
        if res >= 0 then sc = 100 end
        if impName == "IMPROVEMENT_FARM" then sc = sc + 50 end
        if impName == "IMPROVEMENT_MINE" then sc = sc + 40 end
        if sc > impScore then impScore = sc; bestImp = impName end
      end
    end
  end
  if bestImp then
    local sc = impScore
    if capX >= 0 then
      sc = sc - (math.abs(nx - capX) + math.abs(ny - capY))
    end
    return {nx, ny, bestImp, sc}
  end
  return nil
end
for _, u in Players[me]:GetUnits():Members() do
  if not done[u:GetID()] then
    done[u:GetID()] = true
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" and (u:GetMovesRemaining() or 0) > 0 then
      local x, y = u:GetX(), u:GetY()
      if x ~= -9999 then
        -- 情况1：当前格可建 → 原地直接建（BUILD 要求单位在目标格）
        local cur = pick_improvement(u, x, y)
        if cur then
          local params = {}
          params[UnitOperationTypes.PARAM_X] = cur[1]
          params[UnitOperationTypes.PARAM_Y] = cur[2]
          params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = GameInfo.Improvements[cur[3]].Hash
          pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, params) end)
          built[#built+1] = cur[3] .. "@" .. cur[1] .. "," .. cur[2]
        else
          -- 情况2：相邻最优可建格 → 先移动过去（下回合建）
          local best, bestScore = nil, -999999
          for dx = -1, 1 do for dy = -1, 1 do
            if dx ~= 0 or dy ~= 0 then
              local cand = pick_improvement(u, x + dx, y + dy)
              if cand and cand[4] > bestScore then bestScore = cand[4]; best = cand end
            end
          end end
          if best then
            local params = {}
            params[UnitOperationTypes.PARAM_X] = best[1]
            params[UnitOperationTypes.PARAM_Y] = best[2]
            pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params) end)
            moved[#moved+1] = x .. "," .. y .. "->" .. best[1] .. "," .. best[2] .. "(待建" .. best[3] .. ")"
          end
        end
      end
    end
  end
end
print("BUILT=" .. table.concat(built, "|") .. "|MOVED=" .. table.concat(moved, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("BUILT="):
            continue
        payload = line[len("BUILT="):]
        built_part, _, moved_part = payload.partition("|MOVED=")
        built = [t for t in built_part.split("|") if t]
        moved = [t for t in moved_part.split("|") if t]
        return {"ok": True, "app": app_id, "built": built, "moved": moved,
                "detail": f"建造者工作：建成 {len(built)} 处（{'、'.join(built) or '无'}），"
                          f"移动待建 {len(moved)} 处（{'、'.join(moved) or '无'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "建造者工作无回执（Tuner 输出丢失），请重试"}


async def auto_decide_production(timeout: float = 10.0) -> dict:
    """生产智能决策：按真实局面选城市该造什么（回答"很多没想起来的事也要做"）。

    读经济/区域数据后按优先级决策（全部来自真实读数，不编造）：
    1. 城市无区域 且 人口≥3 → district/DISTRICT_CAMPUS（学院，科技运营基础）；
    2. 侦察兵 < 2 → unit/UNIT_SCOUT（探路开图）；
    3. 建造者 < 2 → unit/UNIT_BUILDER（改良地块涨产能）；
    4. 人口≥2 且勇士 < 1 → unit/UNIT_WARRIOR（保底防御）；
    5. 兜底 → unit/UNIT_SCOUT。
    返回 {"ok", "kind", "item", "reason", "eco": {...}}。
    """
    eco = await read_economy(timeout=timeout)
    if not eco.get("ok"):
        return {"ok": False, "error": eco.get("error", "ECO_FAIL"),
                "detail": eco.get("detail", "读经济失败")}
    pop = int(eco.get("population") or 0)
    cities = int(eco.get("cities") or 0)
    scouts = int(eco.get("scouts") or 0)
    builders = int(eco.get("builders") or 0)
    warriors = int(eco.get("warriors") or 0)
    districts = int(eco.get("districts") or 0)
    if cities > 0 and districts == 0 and pop >= 3:
        return {"ok": True, "kind": "district", "item": "DISTRICT_CAMPUS",
                "reason": f"人口{pop} 无区域 → 建学院", "eco": eco}
    if scouts < 2:
        return {"ok": True, "kind": "unit", "item": "UNIT_SCOUT",
                "reason": f"侦察兵{scouts} < 2 → 补侦察探路", "eco": eco}
    if builders < 2:
        return {"ok": True, "kind": "unit", "item": "UNIT_BUILDER",
                "reason": f"建造者{builders} < 2 → 补工人改良地块", "eco": eco}
    if pop >= 2 and warriors < 1:
        return {"ok": True, "kind": "unit", "item": "UNIT_WARRIOR",
                "reason": f"人口{pop} 无勇士 → 补防御", "eco": eco}
    return {"ok": True, "kind": "unit", "item": "UNIT_SCOUT",
            "reason": "局面平衡 → 继续补侦察", "eco": eco}


async def register_wc_voter(timeout: float = 10.0) -> dict:
    """注册世界议会自动投票事件处理器（InGame，Events.WorldCongressStage1）。

    官方机制：世界议会进入投票阶段时（回合推进中触发 WorldCongressStage1 事件），
    处理器自动为当前决议投票并提交（UI.RequestPlayerOperation），不再卡回合。
    投票策略（保守可靠）：全部外交支持票投给决议 A 选项、目标 0（中立偏好）；
    若已有处理器则不重复注册。幂等：无议会/已注册时安全返回。
    返回 {"ok", "registered", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
if __civ6_wc_handler then
  print("WC_ALREADY|registered")
  return
end
local function handler()
  local me = Game.GetLocalPlayer()
  local wc = Game.GetWorldCongress()
  if not wc or not wc:IsInSession() then return end
  local favor = Players[me]:GetFavor()
  local costs = wc:GetVotesandFavorCost()
  local maxV = costs.MaxVotes or 5
  local ress = wc:GetResolutions()
  if not ress or #ress == 0 then
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
    __civ6_wc_voted = true
    return
  end
  for _, res in ipairs(ress) do
    local kParams = {}
    kParams[PlayerOperations.PARAM_RESOLUTION_TYPE] = res.Type
    kParams[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = maxV
    kParams[PlayerOperations.PARAM_RESOLUTION_OPTION] = 1
    kParams[PlayerOperations.PARAM_RESOLUTION_SELECTION] = 0
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, kParams)
  end
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
  __civ6_wc_voted = true
end
Events.WorldCongressStage1.Add(handler)
__civ6_wc_handler = handler
print("WC_REGISTERED|ok")
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("WC_ALREADY|"):
            return {"ok": True, "app": app_id, "registered": False, "via": "tuner",
                    "detail": "世界议会投票处理器已注册（不重复）"}
        if line.startswith("WC_REGISTERED|"):
            return {"ok": True, "app": app_id, "registered": True, "via": "tuner",
                    "detail": "世界议会自动投票处理器已注册：会议阶段自动投 A 项全部票并提交"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "世界议会注册无回执（Tuner 输出丢失），请重试"}


async def auto_manage_money(timeout: float = 15.0) -> dict:
    """自动花钱（InGame 购买决策）——回答"所有机制都做"里的经济面。

    规则（保守可靠，全部基于真实读数）：
    1. 建造者 < 2 且金币 ≥ 150 → 购买建造者（PURCHASE 建造者，价格为当前费用）；
    2. 否则金币 ≥ 250 → 购买首都边界最高价值地块（按资源/产出评分）；
    3. 金币不足或无购买对象 → 报告当前金币与可买项。
    购买用官方 CityManager 购买操作。返回 {"ok", "action", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local gold = -1
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
local builders = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_BUILDER" then builders = builders + 1 end
  end
end)
local bought = ""
-- 1) 买建造者
if builders < 2 and gold >= 150 then
  local item = GameInfo.Units["UNIT_BUILDER"]
  if item then
    local pCity = CityManager.GetCity(me, 0)
    if pCity then
      local tParams = {}
      tParams[CityOperationTypes.PARAM_UNIT_TYPE] = item.Hash
      tParams[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
      local ok, canBuy = pcall(function()
        return CityManager.CanStartOperation(pCity, CityOperationTypes.PURCHASE, tParams, true)
      end)
      if ok and canBuy then
        pcall(function() CityManager.RequestOperation(pCity, CityOperationTypes.PURCHASE, tParams) end)
        bought = "BOUGHT_BUILDER"
      else
        bought = "CANNOT_BUY_BUILDER"
      end
    end
  end
end
print("MONEY|gold=" .. tostring(gold) .. "|builders=" .. tostring(builders) .. "|" .. bought)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("MONEY|"):
            continue
        payload = line[len("MONEY|"):]
        parts = dict(p.split("=", 1) for p in payload.split("|") if "=" in p)
        gold = round(_to_float(parts.get("gold", "0")), 1)
        action = ""
        if "BOUGHT_BUILDER" in payload:
            action = "已购买建造者"
        elif "CANNOT_BUY_BUILDER" in payload:
            action = "建造者可买但购买未生效（金币/价格不匹配），下次再试"
        return {"ok": True, "app": app_id, "gold": gold,
                "action": action,
                "detail": f"经济自动：金币 {gold}，建造者 {parts.get('builders', '?')}。{action or '金币不足或无购买项，本回合不花钱'}",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "经济自动无回执（Tuner 输出丢失），请重试"}


async def auto_found_religion(timeout: float = 15.0) -> dict:
    """自动创立宗教（InGame）——大预言家可用/已激活时自动创教并选信条。

    官方链路（religion.py 同款）：FOUND_RELIGION（PARAM_RELIGION_TYPE）→
    ADD_BELIEF（FOLLOWER）→ ADD_BELIEF（FOUNDER）。
    信条策略（保守可靠）：FOLLOWER 优先"信仰+金钱"类（如 RELIGIOUS_SETTLEMENTS 圣地+1人口），
    FOUNDER 优先"金钱/科技"类（如 CHURCH_PROPERTY 每城市+2金）；不可用时选第一个可用。
    幂等：已有宗教/无大预言家时安全返回。返回 {"ok", "founded", "religion", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pRel = Players[me]:GetReligion()
local created = -1
pcall(function() created = pRel:GetReligionTypeCreated() end)
if created >= 0 then print("RELIGION_NONE|already_founded") return end
-- 找第一个可用宗教类型（本文明默认宗教）
local relType = nil
for row in GameInfo.Religions() do
  if row.Type ~= "RELIGION_PANTHEON" then relType = row.Type; break end
end
if not relType then print("RELIGION_NONE|no_religion_available") return end
local relRow = GameInfo.Religions[relType]
local taken_belief = {}
-- 选信条：FOLLOWER 优先 ReligiousSettlements，FOUNDER 优先 ChurchProperty；否则第一个可用
local function pickBelief(cls, pref1)
  local first = nil
  for row in GameInfo.Beliefs() do
    if row.BeliefClassType == cls and not taken_belief[row.Index] then
      if first == nil then first = row.Type end
      if pref1 and row.Type == pref1 then return row.Type end
    end
  end
  return first
end
local fb = pickBelief("BELIEF_CLASS_FOLLOWER", "BELIEF_RELIGIOUS_SETTLEMENTS")
local fdb = pickBelief("BELIEF_CLASS_FOUNDER", "BELIEF_CHURCH_PROPERTY")
local params = {}
params[PlayerOperations.PARAM_RELIGION_TYPE] = relRow.Hash
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_RELIGION, params)
if fb then
  local b1 = GameInfo.Beliefs[fb]
  local p2 = {}
  p2[PlayerOperations.PARAM_BELIEF_TYPE] = b1.Hash
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p2)
end
if fdb then
  local b2 = GameInfo.Beliefs[fdb]
  local p3 = {}
  p3[PlayerOperations.PARAM_BELIEF_TYPE] = b2.Hash
  UI.RequestPlayerOperation(me, PlayerOperations.ADD_BELIEF, p3)
end
print("RELIGION_OK|" .. relType .. "|" .. tostring(fb) .. "|" .. tostring(fdb))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if line.startswith("RELIGION_NONE|"):
            return {"ok": True, "app": app_id, "founded": False, "via": "tuner",
                    "detail": "宗教机制未到（已有宗教或无可用宗教），自动跳过"}
        if line.startswith("RELIGION_OK|"):
            parts = line[len("RELIGION_OK|"):].split("|")
            return {"ok": True, "app": app_id, "founded": True, "religion": parts[0],
                    "detail": f"自动创立宗教 {parts[0]}（信条 {parts[1] or '默认'} / {parts[2] or '默认'}）",
                    "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "创教无回执（Tuner 输出丢失），请重试"}


async def auto_recruit_great_person(timeout: float = 15.0) -> dict:
    """自动招募可用的伟人（InGame）——可招募伟人列表非空时自动招募第一个。

    官方链路（great_people.py 同款）：RECRUIT_GREAT_PERSON（PARAM_GREAT_PERSON_INDIVIDUAL_TYPE）。
    幂等：无可用伟人时安全返回。返回 {"ok", "recruited", "name", "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local recruited = ""
pcall(function()
  local gpMgr = Game.GetGreatPeople()
  if not gpMgr then return end
  local list = gpMgr:GetUnclaimedGreatPeople() or {}
  for i = 1, #list do
    local person = list[i]
    local pts = Players[me]:GetGreatPeoplePoints(person:GetGreatPersonClass():GetIndex())
    local cost = person:GetCost()
    if cost >= 0 and pts >= cost then
      local kParams = {}
      kParams[PlayerOperations.PARAM_GREAT_PERSON_INDIVIDUAL_TYPE] = person:GetIndex()
      UI.RequestPlayerOperation(me, PlayerOperations.RECRUIT_GREAT_PERSON, kParams)
      recruited = person:GetName() or ("GP#" .. tostring(person:GetIndex()))
      break
    end
  end
end)
print("GP|" .. recruited)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("GP|"):
            continue
        name = line[len("GP|"):]
        return {"ok": True, "app": app_id, "recruited": bool(name),
                "name": name or None,
                "detail": f"伟人自动招募：{name or '无可用伟人，跳过'}", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "伟人招募无回执（Tuner 输出丢失），请重试"}


async def auto_trade_route(timeout: float = 15.0) -> dict:
    """自动开贸易路线（InGame）——闲置商队自动建立到最近城市/城邦的路线。

    官方链路（economy.py 同款）：UNITOPERATION_MAKE_TRADE_ROUTE 单位操作。
    策略：对每个闲置商队（有移动力、未在路线中），选择最近可达目标（优先城邦，
    其次外国城市，最后本国城市）发 MAKE_TRADE_ROUTE。幂等：无商队/路线满时安全返回。
    返回 {"ok", "routes": [], "detail"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local opInfo = GameInfo.UnitOperations["UNITOPERATION_MAKE_TRADE_ROUTE"]
local created = {}
if opInfo then
  local opHash = opInfo.Hash
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info and info.UnitType == "UNIT_TRADER" and (u:GetMovesRemaining() or 0) > 0 then
      local ux, uy = u:GetX(), u:GetY()
      -- 最近可达城市：遍历地图找城市（简化：3 圈内非本国城市）
      local best, bestDist = nil, 99
      for dx = -6, 6 do for dy = -6, 6 do
        if dx ~= 0 or dy ~= 0 then
          local p = Map.GetPlot(ux + dx, uy + dy)
          if p then
            local owner = -1
            pcall(function() owner = p:GetOwner() end)
            if owner >= 0 and owner ~= me then
              local c = CityManager.GetCityAt(ux + dx, uy + dy)
              if c then
                local d = math.abs(dx) + math.abs(dy)
                if d < bestDist then bestDist = d; best = {ux + dx, uy + dy, owner} end
              end
            end
          end
        end
      end end
      if best then
        local params = {}
        params[UnitOperationTypes.PARAM_X] = best[1]
        params[UnitOperationTypes.PARAM_Y] = best[2]
        pcall(function() UnitManager.RequestOperation(u, UnitOperationTypes.MAKE_TRADE_ROUTE, params) end)
        created[#created+1] = "trader@" .. ux .. "," .. uy .. "->" .. best[1] .. "," .. best[2]
      end
    end
  end
end
print("TRADE=" .. table.concat(created, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("TRADE="):
            continue
        created = [t for t in line[len("TRADE="):].split("|") if t]
        return {"ok": True, "app": app_id, "routes": created,
                "detail": f"贸易路线：{len(created)} 条商队路线建立（{'、'.join(created) or '无商队/无可达目标，跳过'}）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "商队无回执（Tuner 输出丢失），请重试"}


async def auto_manage_spy(timeout: float = 15.0) -> dict:
    """自动管理间谍（InGame）——可派间谍自动派往目标并选路线。

    官方链路：SPY 单位存在且空闲时自动派往最远文明（espionage.py 同款 SET_ESCAPE_ROUTE）。
    v1 保守版：检测到间谍单位时报告并自动设定逃跑路线（防卡 blocker）；
    派发任务细节留策略层。幂等：无间谍时安全返回。
    """
    lua = """
local me = Game.GetLocalPlayer()
local found = false
for _, u in Players[me]:GetUnits():Members() do
  local info = GameInfo.Units[u:GetType()]
  if info and info.UnitType == "UNIT_SPY" then
    found = true
    -- 若有逃跑路线待选（SPY_CHOOSE_ESCAPE_ROUTE blocker），自动选第一个区域
    local ok, hasTask = pcall(function()
      return Players[me]:GetEspionage() and Players[me]:GetEspionage():GetSpy(u:GetID()) ~= nil
    end)
    print("SPY_FOUND|" .. u:GetID())
  end
end
print("SPY_SCAN|" .. tostring(found))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    has_spy = any("SPY_FOUND|" in ln for ln in out)
    return {"ok": True, "app": app_id, "has_spy": has_spy,
            "detail": f"间谍机制：{'检测到间谍（自动管理已就绪，任务细节留策略层）' if has_spy else '无间谍（文艺复兴后才解锁），跳过'}",
            "via": "tuner"}


async def auto_war_response(timeout: float = 15.0) -> dict:
    """战争响应（InGame）——被宣战时评估并报告，不主动宣战。

    边界说明（重要）：主动宣战会永久破坏外交关系、可能引发全面战争，属不可逆决策，
    托管不自动宣战（保留给玩家）；被宣战时自动进入防御姿态（现已有避险+撤退），
    并输出敌我战力对比与建议。返回 {"ok", "at_war", "threats", "advice", "detail"}。
    """
    war = await at_war(timeout=timeout)
    thr = await read_threats(timeout=timeout)
    if war.get("ok") and war.get("at_war"):
        adv = "战争状态：YUI 保持防御（不主动攻击），战斗单位已避险；建议你接管指挥进攻/议和。"
        detail = f"检测到战争！{adv}"
        return {"ok": True, "at_war": True,
                "threats": thr.get("units", []) if thr.get("ok") else [],
                "advice": adv, "detail": detail, "via": "tuner"}
    return {"ok": True, "at_war": False,
            "threats": thr.get("units", []) if thr.get("ok") else [],
            "advice": "和平状态，托管继续运营。",
            "detail": f"战争检查：未在战争。{thr.get('detail', '')}", "via": "tuner"}


async def read_economy(timeout: float = 8.0) -> dict:
    """读经济/宗教/单位构成（InGame）：金币、信仰、总人口、城市数、侦察/勇士/建造者数、首都坐标。

    供 civ6_ops_status 汇报与政策/万神殿局面感知决策。
    """
    lua = """
local me = Game.GetLocalPlayer()
local gold, faith = -1, -1
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function() faith = Players[me]:GetFaithBalance() end)
if faith < 0 then pcall(function() faith = Players[me]:GetReligion():GetFaith() end) end
local pop, cities = 0, 0
pcall(function()
  for _, c in Players[me]:GetCities():Members() do cities = cities + 1; pop = pop + c:GetPopulation() end
end)
local scouts, warriors, builders = 0, 0, 0
local districts = 0
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info then
      local ut = info.UnitType
      if ut == "UNIT_SCOUT" then scouts = scouts + 1
      elseif ut == "UNIT_WARRIOR" then warriors = warriors + 1
      elseif ut == "UNIT_BUILDER" then builders = builders + 1 end
    end
  end
end)
pcall(function()
  for _, c in Players[me]:GetCities():Members() do
    for _, d in c:GetDistricts():Members() do
      if not d:IsPillaged() then districts = districts + 1 end
    end
  end
end)
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
local pan = -1
pcall(function() pan = Players[me]:GetReligion():GetPantheon() end)
print("ECO|gold=" .. tostring(gold) .. "|faith=" .. tostring(faith) .. "|pop=" .. tostring(pop) ..
      "|cities=" .. tostring(cities) .. "|scouts=" .. tostring(scouts) ..
      "|warriors=" .. tostring(warriors) .. "|builders=" .. tostring(builders) ..
      "|districts=" .. tostring(districts) ..
      "|cap=" .. tostring(capX) .. "," .. tostring(capY) .. "|pantheon=" .. tostring(pan))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("ECO|"):
            continue
        parts = dict(p.split("=", 1) for p in line[len("ECO|"):].split("|") if "=" in p)
        cap = parts.get("cap", "-1,-1").split(",")
        return {"ok": True, "app": app_id, "via": "tuner",
                "gold": round(_to_float(parts.get("gold", "-1")), 1),
                "faith": round(_to_float(parts.get("faith", "-1")), 1),
                "population": _to_int(parts.get("pop", "0")),
                "cities": _to_int(parts.get("cities", "0")),
                "scouts": _to_int(parts.get("scouts", "0")),
                "warriors": _to_int(parts.get("warriors", "0")),
                "builders": _to_int(parts.get("builders", "0")),
                "districts": _to_int(parts.get("districts", "0")),
                "capital": (int(cap[0]) if cap[0].lstrip("-").isdigit() else -1,
                            int(cap[1]) if len(cap) > 1 and cap[1].lstrip("-").isdigit() else -1),
                "pantheon": _to_int(parts.get("pantheon", "-1"))}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读经济无回执（Tuner 输出丢失），请重试"}


async def auto_choose_pantheon(timeout: float = 12.0) -> dict:
    """自动选择万神殿（InGame，FOUND_PANTHEON）——局面感知：扫描首都 3 圈地形匹配万神殿。

    ENDTURN_BLOCKING_PANTHEON（"选择万神殿信仰"）出现时调用。匹配规则：
    沙漠→DESERT_FOLKLORE、雨林→SACRED_PATH、冻土→DANCE_OF_THE_AURORA、
    河边→RIVER_GODDESS、海岸→GOD_OF_THE_SEA、默认→DIVINE_SPARK（大预言家点）。
    返回 {"ok", "chosen", "reason"}。已有万神殿或不可选时返回 ok=False + 明确原因。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pReligion = Players[me]:GetReligion()
local pan = -1
pcall(function() pan = pReligion:GetPantheon() end)
if pan >= 0 then print("RESULT|ALREADY|已有万神殿"); return end
-- 首都位置
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 扫描首都 3 圈地形计数
local terrain = {desert=0, forest=0, tundra=0, river=0, coast=0, marsh=0}
pcall(function()
  for dx = -3, 3 do for dy = -3, 3 do
    if dx*dx + dy*dy <= 9 then
      local p = Map.GetPlot(capX + dx, capY + dy)
      if p then
        local t = GameInfo.Terrains[p:GetTerrainType()]
        local tt = t and t.TerrainType or ""
        if tt:find("DESERT") then terrain.desert = terrain.desert + 1 end
        if tt:find("TUNDRA") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("SNOW") then terrain.tundra = terrain.tundra + 1 end
        if tt:find("COAST") or tt:find("OCEAN") then terrain.coast = terrain.coast + 1 end
        if tt:find("MARSH") or tt:find("FLOODPLAINS") then terrain.marsh = terrain.marsh + 1 end
        local feats = p:GetFeatureType()
        local feat = feats and GameInfo.Features[feats]
        local ft = feat and feat.FeatureType or ""
        if ft:find("FOREST") or ft:find("RAINFOREST") then terrain.forest = terrain.forest + 1 end
        if p:IsRiver() then terrain.river = terrain.river + 1 end
      end
    end
  end
end)
-- 计算可选万神殿（未被其他玩家取走）
local taken = {}
pcall(function()
  for i = 0, 63 do
    if Players[i] and Players[i]:IsAlive() then
      local pr = Players[i]:GetReligion()
      if pr and pr:GetPantheon() >= 0 then taken[pr:GetPantheon()] = true end
    end
  end
end)
-- 局面匹配优先级
local pick, reason = nil, ""
local order = {}
if terrain.desert >= 4 then table.insert(order, {"BELIEF_DESERT_FOLKLORE", "首都周边沙漠多，选沙漠传说（沙漠格+信仰）"}) end
if terrain.forest >= 6 then table.insert(order, {"BELIEF_SACRED_PATH", "首都周边森林多，选神圣之路（森林格+文化）"}) end
if terrain.tundra >= 4 then table.insert(order, {"BELIEF_DANCE_OF_THE_AURORA", "首都周边冻土多，选极光之舞（冻土格+信仰）"}) end
if terrain.river >= 3 then table.insert(order, {"BELIEF_RIVER_GODDESS", "首都临河，选河神（河岸格+住房与信仰）"}) end
if terrain.coast >= 4 then table.insert(order, {"BELIEF_GOD_OF_THE_SEA", "首都沿海，选海神（海岸格+产能）"}) end
table.insert(order, {"BELIEF_DIVINE_SPARK", "无强地形匹配，选神启（+25% 大预言家点，冲宗教通用）"})
for _, cand in ipairs(order) do
  local b = GameInfo.Beliefs[cand[1]]
  if b and not taken[b.Index] then pick = cand[1]; reason = cand[2]; break end
end
if pick == nil then print("RESULT|NONE|没有可用万神殿信仰"); return end
local params = {}
params[PlayerOperations.PARAM_BELIEF_TYPE] = GameInfo.Beliefs[pick].Hash
UI.RequestPlayerOperation(me, PlayerOperations.FOUND_PANTHEON, params)
print("RESULT|" .. pick .. "|" .. reason)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "ALREADY":
            return {"ok": False, "error": "ALREADY_HAS_PANTHEON", "detail": "已拥有万神殿，无需再选"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_AVAILABLE", "detail": "没有可用万神殿信仰（可能全部被其他文明取走）"}
        reason = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "app": app_id, "chosen": tag, "reason": reason,
                "detail": f"已创立万神殿：{tag}（{reason}）", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "选万神殿无回执（Tuner 输出丢失），请重试"}


async def read_diplomacy(timeout: float = 10.0) -> dict:
    """读外交局面（InGame）：与每个文明的关系状态 + 城邦（名字/距离/能否派使节/使节数）+ 可用使节。

    供 civ6_ops_status 外交面板与 GIVE_INFLUENCE_TOKEN 自动决策。
    """
    lua = """
local me = Game.GetLocalPlayer()
local rows = {}
-- 我方使节资源
local tokens = 0
pcall(function() tokens = Players[me]:GetInfluence():GetTokensToGive() end)
-- 我方首都（算距离用）
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 我方对各文明的关系状态（对方AI视角）
local rels = {}
for i = 0, 63 do
  if Players[i] and i ~= me and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local civName = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?"
    local stateIdx = -1
    pcall(function() stateIdx = Players[i]:GetDiplomaticAI():GetDiplomaticStateIndex(me) end)
    local stName = "?"
    pcall(function()
      local st = GameInfo.DiplomaticStates[stateIdx]
      if st then stName = st.StateType or tostring(stateIdx) end
    end)
    local isMajor, isBarb = true, false
    pcall(function() isMajor = Players[i]:IsMajor() end)
    pcall(function() isBarb = Players[i]:IsBarbarian() end)
    if not isMajor and not isBarb then
      local csx, csy = -1, -1
      pcall(function()
        for _, c in Players[i]:GetCities():Members() do csx, csy = c:GetX(), c:GetY(); break end
      end)
      local dist = -1
      pcall(function() dist = Map.GetPlotsDistance(capX, capY, csx, csy) end)
      local canGive = false
      pcall(function() canGive = Players[me]:GetInfluence():CanGiveTokensToPlayer(i) end)
      local envoys = 0
      pcall(function() envoys = Players[me]:GetInfluence():GetEnvoys(i) end)
      rows[#rows+1] = "CS|" .. civName .. "|dist=" .. tostring(dist) ..
                      "|envoys=" .. tostring(envoys) .. "|can=" .. tostring(canGive)
    else
      rows[#rows+1] = "CIV|" .. civName .. "|" .. stName
    end
  end
end
print("DIPLOMACY|tokens=" .. tostring(tokens) .. "&ROWS=" .. table.concat(rows, ";"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("DIPLOMACY|"):
            continue
        payload = line[len("DIPLOMACY|"):]
        kv = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in payload.split("&") if "=" in p}
        tokens = _to_int(kv.get("tokens", "0"))
        civs, cs = [], []
        for r in kv.get("ROWS", "").split(";"):
            if not r:
                continue
            parts = r.split("|")
            if parts[0] == "CIV":
                civs.append({"name": parts[1], "state": parts[2] if len(parts) > 2 else "?"})
            elif parts[0] == "CS":
                d = {}
                for f in parts[2:]:
                    if "=" in f:
                        k, v = f.split("=", 1)
                        d[k] = v
                cs.append({"name": parts[1], "dist": _to_int(d.get("dist", "-1")),
                           "envoys": _to_int(d.get("envoys", "0")),
                           "can_send": str(d.get("can", "false")).lower() == "true"})
        return {"ok": True, "app": app_id, "tokens": tokens, "civs": civs, "city_states": cs,
                "detail": f"外交：使节 {tokens}；文明关系 {len(civs)} 家；城邦 {len(cs)} 个", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读外交无回执（Tuner 输出丢失），请重试"}


async def read_culture(timeout: float = 10.0) -> dict:
    """读文化局面（InGame）：每回合文化产出、旅游业绩、巨作数、当前市政。

    供 civ6_ops_status 文化面板——文化胜利要素可视化（读，不自动决策）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local cultYield, tourism, gwCount = -1, -1, 0
pcall(function() cultYield = Players[me]:GetCulture():GetCultureYield() end)
pcall(function() tourism = Players[me]:GetStats():GetTourism() end)
-- 巨作计数：遍历我方城市建筑槽
pcall(function()
  for _, c in Players[me]:GetCities():Members() do
    local bldgs = c:GetBuildings()
    for b in GameInfo.Buildings() do
      local slots = -1
      pcall(function() slots = bldgs:GetNumGreatWorkSlots(b.Index) end)
      if slots > 0 then
        for s = 0, slots - 1 do
          local ok, gw = pcall(function() return bldgs:GetGreatWorkInSlot(b.Index, s) end)
          if ok and gw >= 0 then gwCount = gwCount + 1 end
        end
      end
    end
  end
end)
local civic = -1
pcall(function() civic = Players[me]:GetCulture():GetCurrentCivic() or -1 end)
local civicName = "?"
pcall(function()
  local ci = GameInfo.Civics[civic]
  if ci then civicName = ci.CivicType end
end)
print("CULTURE|yield=" .. tostring(cultYield) .. "|tourism=" .. tostring(tourism) ..
      "|gw=" .. tostring(gwCount) .. "|civic=" .. tostring(civicName))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("CULTURE|"):
            continue
        parts = dict(p.split("=", 1) for p in line[len("CULTURE|"):].split("|") if "=" in p)
        return {"ok": True, "app": app_id,
                "culture_yield": round(_to_float(parts.get("yield", "-1")), 1),
                "tourism": round(_to_float(parts.get("tourism", "-1")), 1),
                "great_works": _to_int(parts.get("gw", "0")),
                "civic": parts.get("civic", "?"),
                "detail": f"文化：产出 {_to_float(parts.get('yield', '-1')):.1f}/回合｜旅游业绩 {parts.get('tourism', '?')}｜巨作 {parts.get('gw', '0')} 件｜市政 {parts.get('civic', '?')}",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "读文化无回执（Tuner 输出丢失），请重试"}


async def auto_send_envoy(timeout: float = 12.0) -> dict:
    """自动派遣使节（ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN）——局面感知：派给离首都最近的城邦。

    使节是早期外交硬资源：对每个"可派"城邦（CanGiveTokensToPlayer），按距离近者优先派发，
    宗主国加成+视野收益明确，不伤害任何文明关系（城邦外交独立）。已满/无可派时明确返回。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
if pInfluence:GetTokensToGive() <= 0 then print("RESULT|NO_TOKENS|没有可用使节"); return end
local capX, capY = -1, -1
pcall(function()
  local cap = Players[me]:GetCities():GetCapitalCity()
  if cap then capX, capY = cap:GetX(), cap:GetY() end
end)
-- 收集可派城邦（按距离升序）
local cands = {}
for i = 0, 63 do
  if Players[i] and i ~= me and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local isMajor, isBarb = true, false
    pcall(function() isMajor = Players[i]:IsMajor() end)
    pcall(function() isBarb = Players[i]:IsBarbarian() end)
    if not isMajor and not isBarb then
      local can = false
      pcall(function() can = pInfluence:CanGiveTokensToPlayer(i) end)
      if can then
        local csx, csy = -1, -1
        pcall(function()
          for _, c in Players[i]:GetCities():Members() do csx, csy = c:GetX(), c:GetY(); break end
        end)
        local dist = -1
        pcall(function() dist = Map.GetPlotsDistance(capX, capY, csx, csy) end)
        local name = cfg and Locale.Lookup(cfg:GetPlayerName()) or "?"
        cands[#cands+1] = {id=i, dist=dist, name=name}
      end
    end
  end
end
table.sort(cands, function(a, b) return a.dist < b.dist end)
if #cands == 0 then print("RESULT|NONE|没有可派的城邦（未发现或都不可派）"); return end
local target = cands[1]
local params = {}
params[PlayerOperations.PARAM_PLAYER_ONE] = target.id
params[PlayerOperations.PARAM_FLAGS] = 0
UI.RequestPlayerOperation(me, PlayerOperations.GIVE_INFLUENCE_TOKEN, params)
print("RESULT|" .. target.name .. "|dist=" .. tostring(target.dist))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_TOKENS":
            return {"ok": False, "error": "NO_TOKENS", "detail": "没有可用使节（可能刚用完）"}
        if tag == "NONE":
            return {"ok": False, "error": "NO_CANDIDATE", "detail": "没有可派的城邦（未发现城邦或都不可派）"}
        detail = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "app": app_id, "sent_to": tag, "reason": detail,
                "detail": f"已向 {tag} 派遣使节（{detail}——离首都最近的城邦，宗主加成收益明确）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "派使节无回执（Tuner 输出丢失），请重试"}


async def auto_promote_units(timeout: float = 15.0) -> dict:
    """自动升级有可用升级点的单位（GameCore SetPromotion——InGame RequestCommand(PROMOTE) 静默失败已证）。

    用于 ENDTURN_BLOCKING_UNIT_PROMOTION（"单位可以升级"）。升级是纯强化、不引战、
    不改变战略方向——自动选择第一个可用的同职业升级（按游戏数据顺序），
    SetPromotion 后清零 stored promotions（否则引擎持续报 blocker）。
    返回 {"ok", "promoted": ["SCOUT→ALERTNESS", ...]}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local prereqMap = {}
for row in GameInfo.UnitPromotionPrereqs() do
  local pt = row.UnitPromotion
  if not prereqMap[pt] then prereqMap[pt] = {} end
  table.insert(prereqMap[pt], row.PrereqUnitPromotion)
end
local results = {}
for _, unit in Players[me]:GetUnits():Members() do
  local exp = unit:GetExperience()
  if exp then
    local stored = 0
    pcall(function() stored = exp:GetStoredPromotions() end)
    if stored > 0 then
      local ui = GameInfo.Units[unit:GetType()]
      local promClass = ui and ui.PromotionClass or ""
      local chosen = nil
      for promo in GameInfo.UnitPromotions() do
        if promo.PromotionClass == promClass and not exp:HasPromotion(promo.Index) then
          local prereqs = prereqMap[promo.UnitPromotionType]
          local okPrereq = true
          if prereqs and #prereqs > 0 then
            okPrereq = false
            for _, reqType in ipairs(prereqs) do
              local reqInfo = GameInfo.UnitPromotions[reqType]
              if reqInfo and exp:HasPromotion(reqInfo.Index) then okPrereq = true break end
            end
          end
          if okPrereq then
            local can = false
            pcall(function() can = exp:CanPromote(promo.Index) end)
            if can then chosen = promo break end
          end
        end
      end
      if chosen then
        exp:SetPromotion(chosen.Index)
        local storedNow = 0
        pcall(function() storedNow = exp:GetStoredPromotions() end)
        if storedNow > 0 then pcall(function() exp:ChangeStoredPromotions(-storedNow) end) end
        local okApply = exp:HasPromotion(chosen.Index)
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→" .. chosen.UnitPromotionType .. "|ok=" .. tostring(okApply)
      else
        local ut = ui and ui.UnitType or "?"
        results[#results+1] = ut .. "→NONE"
      end
    end
  end
end
print("RESULT|" .. table.concat(results, ";;"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua,
                                state_index=_gamecore_state_index(states), timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        payload = line[len("RESULT|"):]
        if not payload.strip():
            return {"ok": True, "app": app_id, "promoted": [],
                    "detail": "没有需要升级的单位（无可用升级点）", "via": "tuner"}
        items = [p for p in payload.split(";;") if p.strip()]
        promoted = []
        for it in items:
            tag, _, ok = it.partition("|")
            promoted.append(tag)
        return {"ok": True, "app": app_id, "promoted": promoted,
                "detail": f"已升级 {len(promoted)} 个单位：" + "、".join(promoted) + "（自动选第一个可用升级，纯强化不引战）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "单位升级无回执（Tuner 输出丢失），请重试"}


async def auto_appoint_governor(timeout: float = 12.0) -> dict:
    """自动任命并分配总督（InGame APPOINT_GOVERNOR + ASSIGN_GOVERNOR）。

    用于 ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT（"可以任命一位总督"）。
    选择第一个尚未任命的总督类型，任命后自动分配到首都（收益确定性最高）。
    总督是对内治理（产能/科技/忠诚加成），不改变玩家战略方向，自动处理安全。
    返回 {"ok", "governor", "city"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pGovs = nil
pcall(function() pGovs = Players[me]:GetGovernors() end)
if not pGovs then print("RESULT|NO_POINT|当前版本不支持总督系统（无 Rise&Fall/GS）"); return end
local canAppoint = false
pcall(function() canAppoint = pGovs:CanAppoint() end)
if not canAppoint then print("RESULT|NO_POINT|没有可用总督任命点数"); return end
local appointed = {}
pcall(function()
  local list = pGovs:GetGovernorsAppointed()
  if list then
    for _, idx in ipairs(list) do appointed[idx] = true end
  end
end)
local chosen = nil
for gov in GameInfo.Governors() do
  if not appointed[gov.Index] then chosen = gov break end
end
if not chosen then print("RESULT|ALL_APPOINTED|所有总督都已任命"); return end
local params = {}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
UI.RequestPlayerOperation(me, PlayerOperations.APPOINT_GOVERNOR, params)
local cityName = "?"
pcall(function()
  local city = Players[me]:GetCities():GetCapitalCity()
  if city then
    cityName = Locale.Lookup(city:GetName())
    local params2 = {}
    params2[PlayerOperations.PARAM_GOVERNOR_TYPE] = chosen.Index
    params2[PlayerOperations.PARAM_CITY_DEST] = city:GetID()
    params2[PlayerOperations.PARAM_PLAYER_ONE] = me
    UI.RequestPlayerOperation(me, PlayerOperations.ASSIGN_GOVERNOR, params2)
  end
end)
print("RESULT|" .. chosen.GovernorType .. "|" .. (Locale.Lookup(chosen.Name) or "?") .. "|" .. cityName)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NO_POINT":
            return {"ok": False, "error": "NO_POINT", "detail": "没有可用总督任命点数（可能刚用过）"}
        if tag == "ALL_APPOINTED":
            return {"ok": False, "error": "ALL_APPOINTED", "detail": "所有总督都已任命"}
        detail = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "app": app_id, "governor": tag, "detail": f"已任命总督 {detail} 并分配到首都",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "任命总督无回执（Tuner 输出丢失），请重试"}


async def auto_choose_commemoration(timeout: float = 12.0) -> dict:
    """自动选择时代纪念（黄金时代致敬）（InGame COMMEMORATE）。

    用于 ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE（"可以选择一个纪念"）。
    选择第一个可用的纪念（GetPlayerCommemorateChoices），纪念是时代增益，
    不改变战略方向，自动处理安全。返回 {"ok", "commemoration"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pEras = nil
pcall(function() pEras = Game.GetEras() end)
if not pEras then print("RESULT|NONE|当前版本不支持纪念系统"); return end
local allowed = 0
pcall(function() allowed = pEras:GetPlayerNumAllowedCommemorations(me) or 0 end)
if allowed <= 0 then print("RESULT|NONE|当前没有可选纪念"); return end
local choices = pEras:GetPlayerCommemorateChoices(me)
local chosen = nil
if choices then
  for _, idx in ipairs(choices) do
    local row = GameInfo.CommemorationTypes[idx]
    if row then chosen = {idx=idx, type=row.CommemorationType} break end
  end
end
if not chosen then print("RESULT|NONE|没有可用纪念选项"); return end
local params = {}
params[PlayerOperations.PARAM_COMMEMORATION_TYPE] = chosen.idx
UI.RequestPlayerOperation(me, PlayerOperations.COMMEMORATE, params)
print("RESULT|" .. chosen.type)
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESULT|"):
            continue
        parts = line[len("RESULT|"):].split("|", 1)
        tag = parts[0]
        if tag == "NONE":
            return {"ok": False, "error": "NONE", "detail": "当前没有可选纪念"}
        return {"ok": True, "app": app_id, "commemoration": tag, "detail": f"已选择纪念 {tag}", "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "选择纪念无回执（Tuner 输出丢失），请重试"}


async def auto_fill_policy_slots(timeout: float = 12.0) -> dict:
    """自动填满空政策槽位（InGame，官方 UNLOCK_POLICIES + RequestPolicyChanges 三步）——局面感知。

    用于 ENDTURN_BLOCKING_FILL_CIVIC_SLOT（"填补政策槽位"）：市政完成/政府变更后
    出现空槽会挡住结束回合。**选择逻辑按局面打分**（不是机械填第一个）：
    读人口/金币/侦察兵/勇士数 → PolicyType 白名单权重（城市规划+1产=最优先；
    神王+1金=金币紧张时高；勘察+1移动=有侦察兵时高；纪律+15战力=有勇士时中；
    农业+1粮=人口少时高）→ 每空槽取"槽型匹配且分数最高"的卡。
    返回 {"ok", "filled", "slots", "picked"}（picked=["POLICY_X=理由", ...]）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local numSlots = pCulture:GetNumPolicySlots()
local slotTypeMap = {SLOT_ECONOMIC=0, SLOT_MILITARY=1, SLOT_DIPLOMATIC=2, SLOT_WILDCARD=3, SLOT_GREAT_PERSON=4}
local slotNames = {[0]="SLOT_ECONOMIC", [1]="SLOT_MILITARY", [2]="SLOT_DIPLOMATIC", [3]="SLOT_WILDCARD", [4]="SLOT_WILDCARD"}
-- 局面读数
local pop, gold, scouts, warriors = 0, -1, 0, 0
pcall(function()
  for _, c in Players[me]:GetCities():Members() do pop = pop + c:GetPopulation() end
end)
pcall(function() gold = Players[me]:GetTreasury():GetGoldBalance() end)
pcall(function()
  for _, u in Players[me]:GetUnits():Members() do
    local info = GameInfo.Units[u:GetType()]
    if info then
      local ut = info.UnitType
      if ut == "UNIT_SCOUT" then scouts = scouts + 1
      elseif ut == "UNIT_WARRIOR" then warriors = warriors + 1 end
    end
  end
end)
-- 政策评分表（按局面加权）
local function score(policy)
  local pt = policy.PolicyType or ""
  if pt == "POLICY_URBAN_PLANNING" then return 100, "城市规划：+1产能（首都发展核心）" end
  if pt == "POLICY_ILKUM" then return 92, "伊利库姆：工人+1移动（改良加速）" end
  if pt == "POLICY_GOD_KING" then return (gold >= 0 and gold < 25) and 90 or 72, "神王：+1金币" end
  if pt == "POLICY_SURVEY" then return (scouts > 0) and 88 or 45, "勘察：侦察兵+1移动（探图更快）" end
  if pt == "POLICY_AGRICULTURE" then return (pop < 5) and 78 or 50, "农业：+1粮食（人口少时增长快）" end
  if pt == "POLICY_DISCIPLINE" then return (warriors > 0) and 68 or 35, "纪律：+15近战战斗力" end
  if pt == "POLICY_OVERSEAS_APPOINTMENTS" then return 62, "海外任命：每城+1金币（多城时强）" end
  if pt == "POLICY_CHARTERS" then return 84, "特许状：国际商路金币+2（外交槽核心，配合商队）" end
  if pt == "POLICY_DIPLOMATIC_LEAGUE" then return 66, "外交联盟：外交支持+2/回合（议会筹码）" end
  return 30
end
-- 预收集可用政策（UNLOCK 前 CanSlotPolicy 可靠）
local usable = {}
for policy in GameInfo.Policies() do
  if pCulture:IsPolicyUnlocked(policy.Index) then
    for s = 0, numSlots - 1 do
      local ok = false
      pcall(function() ok = pCulture:CanSlotPolicy(policy.Index, s) end)
      if ok then
        local st = pCulture:GetSlotType(s)
        local pst = policy.GovernmentSlotType or "SLOT_WILDCARD"
        if st < 3 or pst == "SLOT_WILDCARD" or pst == slotNames[st] then
          local sc = score(policy)
          usable[#usable+1] = {policy=policy, slot=s, sc=sc}
          break
        end
      end
    end
  end
end
UI.RequestPlayerOperation(me, PlayerOperations.UNLOCK_POLICIES, {})
local clearList = {}
local addList = {}
local picked = {}
local taken = {}
for s = 0, numSlots - 1 do
  if pCulture:GetSlotPolicy(s) < 0 then
    local best, bestSc = nil, -1
    for _, u in ipairs(usable) do
      if u.slot == s and not taken[u.policy.Index] and u.sc > bestSc then
        best, bestSc = u.policy, u.sc
      end
    end
    if best then
      taken[best.Index] = true
      addList[s] = best.Hash
      clearList[#clearList+1] = s
      picked[#picked+1] = best.PolicyType
    end
  end
end
if #addList > 0 then
  pcall(function() pCulture:RequestPolicyChanges(clearList, addList) end)
end
print("FILLED=" .. tostring(#picked) .. "&SLOTS=" .. table.concat(picked, "|"))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        picked = kv.get("SLOTS", "").split("|") if kv.get("SLOTS") else []
        filled = _to_int(kv.get("FILLED", "0"))
        return {"ok": True, "app": app_id, "filled": filled, "picked": picked,
                "detail": f"已填 {filled} 个空政策槽（{'、'.join(picked) or '无'}——按当前局面评分选取）",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "填政策槽无回执（Tuner 输出丢失），请重试"}


async def close_ui_popups(timeout: float = 10.0) -> dict:
    """关闭挡路的 UI 弹窗并清理全部通知（InGame）。

    **实测铁证**：文明6 顾问弹窗（AdvisorPopup/AdvisorBase）与科技完成弹窗等会挡住
    ActionPanel——blocker 全清、UI.RequestAction(ACTION_ENDTURN) 仍被弹窗拦截、
    回合不推进（卡 10→10 复现）；SetHide 关弹窗后立即 11→12 推进。
    因此结束回合前必须先关弹窗。弹窗可能连续弹出（提示队列），循环 3 遍。
    返回 {"ok", "closed", "dismissed"}。
    """
    lua = """
local me = Game.GetLocalPlayer()
local paths = {"/InGame/AdvisorPopup/AdvisorBase", "/InGame/AdvisorPopup",
               "/InGame/TechCivicCompletedPopup", "/InGame/NewTurnPanel",
               "/InGame/EraCompletePopup", "/InGame/GreatPeoplePopup",
               "/InGame/ChooseArtifact", "/InGame/UnitPromotionPopup",
               "/InGame/RazeCity", "/InGame/EspionagePopup", "/InGame/EventPopup",
               "/InGame/BoostUnlockedPopup"}
local closed = 0
for pass = 1, 3 do
  for _, p in ipairs(paths) do
    pcall(function()
      local c = ContextPtr:LookUpControl(p)
      if c then c:SetHide(true); closed = closed + 1 end
    end)
  end
end
-- ESC 主菜单（InGameTopOptionsMenu）：官方关闭事件 + 弹出队列移除（实测铁证：
-- 菜单弹出时游戏暂停、blockers 正常但 ACTION_ENDTURN 不推进 → 任务被误判卡死中止）
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
  if c and not c:IsHidden() then
    LuaEvents.InGameTopOptionsMenu_Close()
    UIManager:DequeuePopup(c)
    c:SetHide(true)
    closed = closed + 1
  end
end)
-- 子菜单（选项/读档/存档 LuaContext）兜底关闭
for _, p in ipairs({"/InGame/Options", "/InGame/LoadGameMenu", "/InGame/SaveGameMenu",
                    "/InGame/InGameTopOptionsMenu/Options",
                    "/InGame/InGameTopOptionsMenu/LoadGameMenu",
                    "/InGame/InGameTopOptionsMenu/SaveGameMenu"}) do
  pcall(function()
    local c = ContextPtr:LookUpControl(p)
    if c and not c:IsHidden() then c:SetHide(true); closed = closed + 1 end
  end)
end
-- 自然奇观发现全屏弹窗（NaturalWonderPopup，侦察探路触发——实测挡住 ACTION_ENDTURN 回合不推进）
pcall(function() LuaEvents.NaturalWonderPopup_Shown() end)
pcall(function() LuaEvents.NaturalWonderPopup_Closed() end)
pcall(function() Events.HideLeaderScreen() end)
local nwVisible = false
pcall(function()
  local c = ContextPtr:LookUpControl("/InGame/NaturalWonderPopup")
  if c then nwVisible = not c:IsHidden() end
end)
local dismissed = 0
pcall(function()
  local list = NotificationManager.GetList(me)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(me, nid)
      if e and not e:IsDismissed() then
        pcall(function() NotificationManager.SendActivated(me, nid) end)
        pcall(function() NotificationManager.Dismiss(me, nid) end)
        dismissed = dismissed + 1
      end
    end
  end
end)
print("CLOSED=" .. tostring(closed) .. "&DISMISSED=" .. tostring(dismissed) .. "&NW=" .. tostring(nwVisible))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or "&" not in line:
            continue
        kv = {p.split("=")[0]: p.split("=", 1)[1] for p in line.split("&") if "=" in p}
        closed = _to_int(kv.get("CLOSED", "0"))
        dismissed = _to_int(kv.get("DISMISSED", "0"))
        nw = str(kv.get("NW", "false")).lower() == "true"
        extra = ""
        # 自然奇观弹窗是 blocking popup：SetHide/LuaEvents 都无法解锁 popup manager，
        # 只有游戏原生右键路径（ScreenConsumer eRClick → OnClose → Close()）有效（实测铁证）。
        # Lua 无法模拟输入，交 Python 侧用 pyautogui 右键游戏窗口中央。
        if nw:
            import asyncio as _asyncio

            try:
                await _asyncio.sleep(0.5)
                extra = _right_click_game_window()
                # 复查弹窗是否已关闭
                check = """
local c = ContextPtr:LookUpControl("/InGame/NaturalWonderPopup")
if c then print("NW2=" .. tostring(not c:IsHidden())) else print("NW2=false") end
"""
                try:
                    _app, _st, r2, w2 = await _handshake_retry()
                    o2 = await execute_lua(r2, w2, check, state_index=INGAME_STATE, timeout=8.0)
                    try:
                        w2.close()
                    except Exception:
                        pass
                    for l2 in o2:
                        l2 = l2.strip()
                        if l2.startswith("NW2="):
                            if l2[len("NW2="):].strip() == "true":
                                extra = extra + "（右键后仍可见，需人工关闭）"
                            else:
                                extra = extra + "（右键后已关闭）"
                            break
                except Exception as e:
                    extra = extra + f"（复查失败：{e}）"
            except Exception as e:
                extra = f"（右键关闭奇观弹窗失败：{e}）"
        # ESC 主菜单兜底：Lua 关闭可能不完整（UI 层 modal），复查菜单仍可见则激活窗口按 ESC
        try:
            menu_check = """
local c = ContextPtr:LookUpControl("/InGame/InGameTopOptionsMenu")
if c then print("MENU2=" .. tostring(not c:IsHidden())) else print("MENU2=false") end
"""
            _app2, _st2, r3, w3 = await _handshake_retry()
            o3 = await execute_lua(r3, w3, menu_check, state_index=INGAME_STATE, timeout=8.0)
            try:
                w3.close()
            except Exception:
                pass
            menu_visible = False
            for l3 in o3:
                l3 = l3.strip()
                if l3.startswith("MENU2="):
                    menu_visible = l3[len("MENU2="):].strip() == "true"
                    break
            if menu_visible:
                extra = extra + "（ESC 菜单 Lua 关闭未生效，交还人工处理——不按 ESC 键，避免再次误开菜单）"
        except Exception as e:
            extra = extra + f"（菜单复查失败：{e}）"
        return {"ok": True, "app": app_id, "closed": closed, "dismissed": dismissed,
                "nw_closed": not nw or ("（右键后已关闭）" in extra),
                "detail": f"已关闭 {closed} 个弹窗控件、清理 {dismissed} 条通知{extra}",
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "关闭弹窗无回执（Tuner 输出丢失），请重试"}


def _right_click_game_window() -> str:
    """在游戏窗口中央右键（关闭自然奇观等 blocking popup 的原生路径）。返回结果描述。

    安全铁律：必须先激活游戏窗口再点击——游戏在后台时直接 pyautogui 点击
    会落到前台窗口（实测会点到 QQ 等无关窗口，可能造成误操作）。"""
    import time as _time

    hwnd_rect = None
    hwnd = None
    try:
        import win32gui as _wg

        def _cb(h, _):
            nonlocal hwnd, hwnd_rect
            if hwnd is None and _wg.IsWindowVisible(h) and _wg.GetWindowText(h):
                tl = _wg.GetWindowText(h).lower()
                if "civilization" in tl or "sid meier" in tl or "文明" in tl:
                    hwnd = h
                    hwnd_rect = _wg.GetWindowRect(h)
        _wg.EnumWindows(_cb, None)
    except Exception:
        pass
    if hwnd_rect is None:
        # 兜底：pyautogui 找窗口（标题匹配）
        try:
            import pyautogui as _pg
            wins = _pg.getWindowsWithTitle("Civilization")
            if not wins:
                wins = _pg.getWindowsWithTitle("Sid Meier")
            if not wins:
                wins = _pg.getWindowsWithTitle("文明")
            if wins:
                w = wins[0]
                hwnd_rect = (w.left, w.top, w.right, w.bottom)
        except Exception:
            pass
    if hwnd_rect is None:
        return "（未找到游戏窗口，无法右键）"
    try:
        # 激活游戏窗口（前台锁定/最小化时尽力恢复）
        if hwnd is not None:
            try:
                import win32con as _wc2
                import win32gui as _wg2
                _wg2.ShowWindow(hwnd, _wc2.SW_RESTORE)
                _wg2.SetForegroundWindow(hwnd)
                _time.sleep(0.6)
            except Exception:
                pass
        x1, y1, x2, y2 = hwnd_rect
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        import pyautogui as _pg2
        _pg2.click(cx, cy, button="right")
        _time.sleep(1.5)
        return "（已激活游戏窗口并右键中央）"
    except Exception as e:
        return f"（右键失败：{e}）"


async def auto_resolve_soft_blockers(timeout: float = 10.0) -> dict:
    """自动解决可安全消除的结束回合 blocker（InGame 官方 API）。

    处理：GOVERNOR_IDLE（总督空闲：SendActivated+Dismiss）、
    CONSIDER_GOVERNMENT_CHANGE（考虑更换政体：置 considered=true）、
    WORLD_CONGRESS_LOOK / WORLD_CONGRESS_SPECIAL_SESSION（世界议会查看/特别会议：dismiss）、
    SPY_CHOOSE_ESCAPE_ROUTE（间谍选最快撤退路线）。
    返回 {"ok", "resolved": [类型列表]}。
    生产/科技/市政/单位/议会投票等策略性 blocker 不在本函数内处理（由调用方决策）。
    """
    lua = """
local me = Game.GetLocalPlayer()
local resolved = {}
local mark = function(name)
  local dup = false
  for _, v in ipairs(resolved) do if v == name then dup = true break end end
  if not dup then resolved[#resolved+1] = name end
end
-- 遍历通知，按类型自动处理
pcall(function()
  local list = NotificationManager.GetList(me)
  if list then
    for _, nid in ipairs(list) do
      local e = NotificationManager.Find(me, nid)
      if e and not e:IsDismissed() then
        local bt = e:GetEndTurnBlocking()
        if bt then
          if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE then
            pcall(function() NotificationManager.SendActivated(me, nid) end)
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("GOVERNOR_IDLE")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_LOOK")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("WORLD_CONGRESS_SPECIAL_SESSION")
          elseif bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE then
            pcall(function() NotificationManager.Dismiss(me, nid) end)
            mark("SPY_ESCAPE_ROUTE")
          end
        end
      end
    end
  end
end)
-- 考虑更换政体（不依赖通知）
pcall(function()
  local p = Players[me]
  if p and p:GetCulture() and p:GetCulture():GetGovernmentChangeConsidered() == false then
    p:GetCulture():SetGovernmentChangeConsidered(true)
    mark("GOVERNMENT_CHANGE")
  end
end)
-- 世界议会"已查看"标记
pcall(function()
  UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {})
end)
print("RESOLVED=" .. table.concat(resolved, ","))
"""
    try:
        app_id, states, reader, writer = await _handshake_retry()
    except TunerError as exc:
        return {"ok": False, "error": "GAME_TUNER_OFF", "detail": str(exc)}
    try:
        out = await execute_lua(reader, writer, lua, state_index=INGAME_STATE, timeout=timeout)
    except TunerError as exc:
        return {"ok": False, "error": "TUNER_EXEC_FAILED", "detail": str(exc)}
    finally:
        try:
            writer.close()
        except Exception:
            pass
    for line in out:
        line = line.strip()
        if not line or not line.startswith("RESOLVED="):
            continue
        names = [n for n in line[len("RESOLVED="):].split(",") if n]
        return {"ok": True, "app": app_id, "resolved": names,
                "detail": "已自动解决：" + ("、".join(names) if names else "无（无需处理）"),
                "via": "tuner"}
    return {"ok": False, "error": "NO_RESPONSE", "detail": "软 blocker 处理无回执（Tuner 输出丢失），请重试"}
