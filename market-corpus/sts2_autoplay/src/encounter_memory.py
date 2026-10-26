# -*- coding: utf-8 -*-
"""怪物/事件实例知识库（t31）—— 纯观测采集器 + 独立落盘层。

## 这个文件是什么

插件每次 tick 都会看到 `/state`。本模块把其中的【战斗遭遇】与【事件选项】摘出来，
跨局累积到一个**独立文件** `encounter_db.json`，使得再次遇到同一只怪/同一个事件时，
猫娘能说出"这只怪上次啃了我多少血"这类**基于实例的**第一人称经验。

严格的规范来源：`.dsh-artifacts/_t30/ENCOUNTER-DB-CONTRACT.md`
（sha16 `0c17acf4e511d283`，641 行，owner=macro-analyst）。本实现**不自行改口径**；
字段名/类型/单位/缺失语义全部照该契约，下面凡是引用 `§x.y` 均指该契约的章节。

## ★★ 三个必须先读的实测事实（决定实现形态）

### 1. `route[]` 是【tick 段表】不是遭遇表（契约 §1 / §10.1）
实测同一场 `CUBEX_CONSTRUCT`（floor=6）被切成 **5 条** route 条目：
```
screen=COMBAT          room=CUBEX_CONSTRUCT  floor=6  hp 57->57
screen=CARD_SELECTION  room=CUBEX_CONSTRUCT  floor=6  hp 57->57
screen=COMBAT          room=CUBEX_CONSTRUCT  floor=6  hp 57->31
screen=CARD_SELECTION  room=CUBEX_CONSTRUCT  floor=6  hp 31->31
screen=COMBAT          room=CUBEX_CONSTRUCT  floor=6  hp 31->10
```
真实损血 = 57-10 = **47**，但各段 `hp_lost` 相加 = **233**（虚高 5 倍）。
⇒ 本模块**不继承** `route[]`/`hp_lost` 任何字段，改为**自己逐 tick 记录**再按 §3.1 合并。

### 2. `run_id` 在【顶层】，不在 `run` 层（t31 实机实测，契约 §1 未指明层级）
```
实测 4 个样本一致：
  d['run_id']        = 'C6CM8CZRR9KQ'    ← 真实值
  d['run']['run_id'] = None              ← ★ run 的 16 个键里【根本没有】run_id
```
若照字面取 `run['run_id']` ⇒ 恒 None ⇒ **所有局共用一个 run_id ⇒ 跨局串味**。
⇒ 本模块一律从**顶层**取 `run_id`，见 `_extract_run_id()`。

### 3. `enemy_id` 与房间名是两回事（契约 §1）
`/state.combat.enemies[*].enemy_id` 才是单体的稳定标识（如 `BYRDONIS`）；
`route[]` 里的 `room` 名既可能是敌人名也可能是 `room:COMBAT` 这类**结构占位符**。
⇒ 本模块按 `enemy_id` 聚合（契约 §6.5 实测：单体口径 73 键/3 单例，
   组合口径 95 键/15 单例 ⇒ 单体样本利用率高 4 倍）。

## ★ 设计约束：本模块**纯观测**
采集器**不得**改变任何决策行为（契约验收要求）。故：
- 只读输入字典，**从不写回**调用方传进来的对象；
- 不抛异常：任何解析失败都降级为"本 tick 不记录"，绝不影响自动游玩主循环；
- 落盘失败只告警，不中断（与 `catgirl_memory.save()` 同口径）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

# --------------------------------------------------------------------------------------
# 常量（契约 §5.3 / §3.5）
# --------------------------------------------------------------------------------------

#: 库形状版本。字段新增不升版（读方按缺失处理为 null）；改名/改语义/删字段才 +1。
SCHEMA_VERSION = 1

#: 原始条目上限。★ 契约 §5.3 定为 20000（远大于 `catgirl_memory` 的 cap=50）。
ENCOUNTER_CAP = 20000

#: 契约 §3.1：这两个屏出现在战斗中途**不算结束**（战斗后抽牌会插入）。
#: 实测 `COMBAT->COMBAT` 相邻出现 288 次 ⇒ 不能只按"离开 COMBAT"判边界。
COMBAT_INTERSTITIAL_SCREENS: frozenset[str] = frozenset(
    {"CARD_SELECTION", "CARD_SELECTION_REWARD"}
)

#: 契约 §3.5 处方降级理由（字符串枚举，供 `block_needed_reason` 用）。
REASON_INTENT_INCOMPLETE = "intent_incomplete"
REASON_NO_TURN_DATA = "no_turn_data"

#: 契约 §4.3：事件选完直接进战斗 ⇒ 跨 tick 差分失真，代价字段写 null。
REASON_POST_EVENT_COMBAT = "post_event_combat"


def _default_encounter_db_path() -> str | None:
    """解析 encounter_db.json 的路径。

    与 `catgirl_memory._default_memory_path()` **同目录但不同文件** ——
    契约 §5.1 决策：独立文件，因为 `catgirl_memory.json` 是 cap=50 环形缓冲
    （实测 20 分钟内就有局被逐出），而实例知识是**跨局累积**资产。

    返回 None 表示环境无法确定（此时 store 退化为仅内存、不落盘）。
    """
    base = os.environ.get("NEKO_PLUGIN_DATA_DIR") or ""
    if base:
        return os.path.join(base, "sts2_autoplay", "encounter_db.json")
    try:
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            return os.path.join(local, "N.E.K.O", "sts2_autoplay", "encounter_db.json")
    except Exception:
        pass
    return None


def _as_int(value: Any) -> int | None:
    """宽容取整：不是数字则 None（★ 不返回 0 —— 0 与 None 语义不同，契约 §3.6）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text and (text.lstrip("-").isdigit()):
            try:
                return int(text)
            except ValueError:
                return None
    return None


def _as_str_or_none(value: Any) -> str | None:
    """字符串型缺失统一写 None（不是 "unknown" —— 契约 §3.6）。"""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return bool(value)


# ======================================================================================
# §3.5 处方：三层降级
# ======================================================================================


def compute_block_needed(
    *,
    lethal_risks: Any,
    end_turn_will_kill_player: Any,
    enemies: list[dict[str, Any]],
    player_block: int | None,
) -> dict[str, Any]:
    """按契约 §3.5 三层降级算处方，返回 `{block_needed, lethal, reason, source}`。

    ## 三层（★ 顺序不可换 —— 游戏自己算的比我们反推权威）

    **第 1 层**：`combat.lethal_risks` 非空 ⇒ 直接采用（最权威）。
        实测当前恒为 `[]`；契约 §11 标注"非空形状未验证" ⇒ 我只原样透传，不解析其内部结构。

    **第 2 层**：`combat.end_turn_will_kill_player == True` ⇒ 本回合必须处理，`lethal=True`。
        实测该键恒 `False`。

    **第 3 层**（反推）：`block_needed = max(0, enemy_total_damage - player_block)`。

    ## ★ 0 与 null 必须分开（契约 §3.5 末条）
    ```
    0    = 算出来【不需要】格挡（确实没有伤害）
    null = 【算不出来】（数据不足）—— 绝不用 0 冒充
    ```
    这是本模块最容易写错的地方，故有专门的测例（`test_zero_and_null_are_distinct`）。
    """
    out: dict[str, Any] = {
        "block_needed": None,
        "lethal": False,
        "reason": None,
        "source": None,
    }

    # ---- 第 1 层：游戏自己算好的致命风险 ----
    risks = lethal_risks if isinstance(lethal_risks, list) else []
    if risks:
        out["lethal"] = True
        out["source"] = "lethal_risks"
        # ★ 契约未定义 lethal_risks 元素形状（§11 未验证）⇒ 不猜，原样留给上层展示。
        out["block_needed"] = None
        out["reason"] = "lethal_risks_present"
        return out

    # ---- 第 2 层：结束回合会死 ----
    if _as_bool(end_turn_will_kill_player, False):
        out["lethal"] = True
        out["source"] = "end_turn_will_kill_player"

    # ---- 第 3 层：反推 ----
    # ★ 累加前 `or 0`（非攻击意图 total_damage 为 null），但**入库保留 null**（契约 §10.5）。
    if not enemies:
        out["reason"] = REASON_NO_TURN_DATA
        return out

    total = 0
    complete = True
    for enemy in enemies:
        if not isinstance(enemy, dict):
            continue
        if not _as_bool(enemy.get("is_alive"), False):
            continue
        intents = enemy.get("intents")
        # ★ 存活敌人 intents 为空/缺失 ⇒ 算不出（契约 §3.5 降级规则）
        if not isinstance(intents, list) or not intents:
            complete = False
            break
        for intent in intents:
            if not isinstance(intent, dict):
                continue
            total += _as_int(intent.get("total_damage")) or 0
    if not complete:
        out["reason"] = REASON_INTENT_INCOMPLETE
        return out

    block = _as_int(player_block)
    if block is None:
        out["reason"] = REASON_NO_TURN_DATA
        return out

    # ★ 这里到得了 ⇒ 真的算出来了 ⇒ 用 int（含 0），**不是** null。
    out["block_needed"] = max(0, total - block)
    out["source"] = "derived"
    return out


# ======================================================================================
# §3.1 遭遇边界
# ======================================================================================


def _enemy_group_key(enemies: list[dict[str, Any]]) -> str:
    """`enemy_group_key` = 敌人 enemy_id **排序后**用 `+` 连接（契约 §1 / §10.6）。

    ★ 必须排序：实测 `TWIG_SLIME_M+TWIG_SLIME_S` 与排序后形态**并存**
      ⇒ 不排序会把同一组敌人算成两个 key。

    ## ★★ 为什么**不**按 `is_alive` 过滤（这是 t31 修掉的一个真缺陷）

    契约 §3.1 的伪码写 `g = sorted(enemy_id for enemies if is_alive)`。
    照抄它会造成**两个**实测可复现的后果（`_t31_rootcause_dead.log`）：

    **后果 1：最后一击那一 tick 被整条丢弃。**
    ```
    全活 [NIBBIT(True)]  → 'NIBBIT'
    全死 [NIBBIT(False)] → ''        ← ★ 空串
    ```
    而 `_observe` 里判据是 `if group and key3 != self._cur_key:` ⇒ 空串为假
    ⇒ 该 tick **既不 append_turn 也不 close** ⇒ 末态永久丢失 ⇒
    · `victory` **永远判不出**（看不到 `is_alive=False`）
    · `true_hp_loss` 用倒数第二 tick 的 hp（实测 70→70 得 0，真值应为 70→60 得 10）

    **后果 2：打怪过程中 key 会缩水 ⇒ 一场战斗被劈成多场。**
    ```
    部分 [A(True), B(False)] → 'A'    ← ★ 只剩 A，与开局 'A+B' 不同
    ```
    ⇒ 只要打死其中一只，key3 就从 `A+B` 变成 `A` ⇒ 判为"换组"
    ⇒ **一次遭遇被错误地关掉并重开**，正是契约 §1 要防的"一场战斗被切成多条"。

    ## 修法
    用**全部**敌人的 `enemy_id` 做身份键（**不看**存活状态）。
    这就是契约 §1 表格里 `enemy_group_key` 的定义
    （"该遭遇中敌人的组合标识"—— 是**该遭遇**的组合，不是"这一刻活着的组合"），
    所以本修法**不改变契约口径**，只是纠正 §3.1 伪码的副作用。

    ★ 兼容：`enemies` 为空 ⇒ 返回 `''`（调用方据此跳过本 tick，行为不变）。
    """
    ids = sorted(
        str(e.get("enemy_id"))
        for e in enemies
        if isinstance(e, dict) and e.get("enemy_id")
    )
    return "+".join(ids)


def _is_room_placeholder(room: Any) -> bool:
    """`room:*` 是结构占位符而非实体（契约 §6.2）。

    实测：每个 floor 都有 `room:COMBAT` 且损血 = 0 ⇒ 若不清掉，
    它会在库里变成一只"叫 room:COMBAT 的怪"，污染统计。
    """
    return str(room or "").startswith("room:")


#: `enemy_index` 条目里允许落盘的键（★ 白名单 —— 防止把内部状态泄进 JSON）。
#: ★★ 为什么必须白名单而不是"剔除下划线"：`_recompute` 是 **callable**，
#:   一旦落盘 `json.dump` 抛 `TypeError`（实测导致整个库**从未被保存**）。
_PUBLIC_INDEX_KEYS = (
    "samples", "avg_true_hp_loss", "p90_true_hp_loss", "avg_turns",
    "deaths", "last_updated",
)


def _public_index(index: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把 `enemy_index` 裁成**纯数据**形态（只留 `_PUBLIC_INDEX_KEYS`）。

    ★ 派生表是**可重建**的（§5.2）：`_loss_sum` / `_turn_sum` / `_recompute` 这些
      累加中间态只需活在内存里；落盘后由 `_reindex_originals()` 从原始重算。
    """
    out: dict[str, dict[str, Any]] = {}
    for eid, entry in index.items():
        if not isinstance(entry, dict):
            continue
        out[str(eid)] = {k: entry.get(k) for k in _PUBLIC_INDEX_KEYS}
    return out


def _recompute_index_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """重算一个 `enemy_index` 条目的派生数值（模块级 —— 不进 JSON）。"""
    loss_n = int(entry.get("_loss_n") or 0)
    entry["avg_true_hp_loss"] = (
        round(float(entry.get("_loss_sum") or 0.0) / loss_n, 2) if loss_n else None
    )
    turn_n = int(entry.get("_turn_n") or 0)
    entry["avg_turns"] = (
        round(float(entry.get("_turn_sum") or 0.0) / turn_n, 2) if turn_n else None
    )
    vals = sorted(v for v in (entry.get("_loss_values") or []) if isinstance(v, int))
    if vals:
        k = max(0, min(len(vals) - 1, int(round(0.9 * (len(vals) - 1)))))
        entry["p90_true_hp_loss"] = vals[k]
    else:
        entry["p90_true_hp_loss"] = None
    return entry


# ======================================================================================
# 采集器
# ======================================================================================


class EncounterMemory:
    """怪物/事件实例知识库：逐 tick 采集 → 按遭遇合并 → 落盘。

    Args:
        path: `encounter_db.json` 路径（None ⇒ 仅内存、不落盘，用于测试）。
        logger: 可选 logger（告警用；**不**参与决策）。
        clock: 可注入的时间源（测试用），返回 epoch 秒。

    ★ 线程模型：与 `catgirl_memory` 一致 —— **假定单线程调用**（service 的 tick 链路）。
      本模块自己不加锁（加密锁反而会与既有代码的调用假设不一致）。
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        logger: Any = None,
        clock: Any = None,
    ) -> None:
        self._path = path
        self._logger = logger
        self._clock = clock if callable(clock) else time.time
        self._encounters: list[dict[str, Any]] = []
        self._enemy_index: dict[str, dict[str, Any]] = {}
        self._dirty = False

        # --- 当前进行中的 encounter（内存态，未落盘）---
        self._cur_key: tuple[str, str, str] | None = None
        self._cur_turns: list[dict[str, Any]] = []
        self._cur_start_hp: int | None = None
        self._cur_lowest_hp: int | None = None
        self._cur_meta: dict[str, Any] = {}
        self._cur_seq = 0
        self._last_seq_floor: tuple[str, str] | None = None

        # --- 事件屏跨 tick 状态 ---
        self._ev_screen_key: str | None = None
        self._ev_options: dict[int, dict[str, Any]] = {}
        self._ev_before: dict[str, Any] = {}
        self._ev_pending: dict[str, Any] | None = None
        self._ev_seqs: dict[str, int] = {}

        self.load()

    # ---------------------------------------------------------------- 属性

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def encounters(self) -> list[dict[str, Any]]:
        return self._encounters

    @property
    def enemy_index(self) -> dict[str, dict[str, Any]]:
        return self._enemy_index

    # ---------------------------------------------------------------- 落盘

    def load(self) -> None:
        """读库；**损坏不静默清空**（沿用 t16 修好的 `catgirl_memory.load` 口径）。"""
        self._encounters = []
        self._enemy_index = {}
        if not self._path or not os.path.exists(self._path):
            return
        try:
            raw = open(self._path, "r", encoding="utf-8").read()
        except OSError:
            self._log(f"encounter_db load failed (IO): {self._path}")
            return
        if not raw.strip():
            return
        data: Any = None
        try:
            data = json.loads(raw)
        except ValueError:
            # 抢救：取第一个完整对象（与 catgirl_memory 同一策略）
            try:
                data, end = json.JSONDecoder().raw_decode(raw)
                self._log(
                    f"encounter_db load: RECOVERED partial file {self._path} "
                    f"(trailing_residue_chars={len(raw) - end})"
                )
            except ValueError:
                self._log(f"encounter_db load: unparsable, starting empty: {self._path}")
                return
        if not isinstance(data, dict):
            self._log(f"encounter_db load: not a dict, starting empty: {self._path}")
            return
        records = data.get("encounters")
        if isinstance(records, list):
            self._encounters = [r for r in records if isinstance(r, dict)][-ENCOUNTER_CAP:]
        # ★ 派生表**一律由原始重建**（契约 §5.2 要求"可完全重建"）。
        #   为什么即使文件里存了 `enemy_index` 也要重建：落盘时它被裁成
        #   纯数据形态（`_public_index` 剔掉 `_loss_sum` 等内存累加量）
        #   ⇒ 载入后的条目**缺累加中间态**，继续往它上面累加会算错。
        #   重建则保证"派生永远与原始自洽"，且这正是契约点名要的性质。
        if self._encounters:
            self._reindex_originals()
        else:
            self._enemy_index = {}

    def save(self) -> None:
        """写回磁盘（幂等；path=None 时静默跳过）。

        ★ 沿用 `catgirl_memory.save()` 的加固：**唯一 tmp 名 + fsync + os.replace**，
          避免宿主 reload 时新旧两个 Store 交错写导致"完整前缀 + 中段残片"。

        ★★ 捕获要宽：`_enemy_index` 的条目里带 `_recompute`（callable），
           若序列化时把它带上，`json.dump` 会抛 **TypeError**（不是 OSError）。
           实测：只捕 OSError ⇒ 异常逃逸 ⇒ 整个库**从未落盘**（门抓到的真 bug）。
           ⇒ 这里 (a) 序列化前剔除下划线私有键，(b) 捕 Exception 兜底，
             但**必须显式记日志**（不静默 —— 「保存失败」不能装作没发生）。
        """
        if not self._path:
            return
        tmp = "%s.%d.%s.tmp" % (self._path, os.getpid(), uuid.uuid4().hex[:8])
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "encounter_cap": ENCOUNTER_CAP,
                "updated_at": self._clock(),
                "encounters": self._encounters,
                "enemy_index": _public_index(self._enemy_index),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001 —— 落盘失败必须可见，但不打断游玩
            self._log(f"encounter_db save failed: {self._path} ({exc!r})")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    # ---------------------------------------------------------------- 主入口

    def observe(self, state: dict[str, Any]) -> dict[str, Any]:
        """每 tick 调一次。返回本次的观测动作摘要（供日志/测试，**不**参与决策）。

        ★ 纯观测契约：`state` 只读，本方法**不修改**入参。
          任何异常都被吞掉并降级为 `{"ok": False, ...}` —— 采集永不影响游玩。
        """
        try:
            return self._observe(state)
        except Exception as exc:  # noqa: BLE001 —— 可观测性不得打断主循环
            self._log(f"encounter observe failed: {exc!r}")
            return {"ok": False, "error": repr(exc)}

    def _observe(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {"ok": False, "error": "not_a_dict"}
        screen = str(state.get("screen") or "")
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        run_id = _extract_run_id(state)
        act_id = _as_str_or_none(run.get("act_id")) or ""
        floor = _as_int(run.get("floor"))
        floor_s = "" if floor is None else str(floor)

        action = {"ok": True, "screen": screen, "recorded": 0}

        combat = state.get("combat") if isinstance(state.get("combat"), dict) else None
        in_combat = bool(state.get("in_combat")) and isinstance(combat, dict)

        # ---------------- 战斗侧：§3.1 边界 ----------------
        if in_combat:
            enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
            group = _enemy_group_key(enemies)
            key3 = (act_id, floor_s, group)
            if group and key3 != self._cur_key:
                # 三元组变了 ⇒ 关旧的、开新的
                if self._cur_key is not None:
                    self._close_encounter(ended_by="group_change", game_over=None)
                self._open_encounter(
                    state=state, run_id=run_id, act_id=act_id, floor=floor, group=group
                )
                action["recorded"] = 1
            elif group:
                self._append_turn(state, combat)
                action["recorded"] = 1
        else:
            # 离开 COMBAT：CARD_SELECTION 不算结束（契约 §3.1 ③）
            if self._cur_key is not None and screen not in COMBAT_INTERSTITIAL_SCREENS:
                go = state.get("game_over") if isinstance(state.get("game_over"), dict) else None
                self._close_encounter(ended_by="screen_change", game_over=go)
                action["closed"] = True

        # ---------------- 事件侧：§4 ----------------
        try:
            ev_action = self._observe_event(state)
            action["event"] = ev_action
        except Exception as exc:  # noqa: BLE001
            self._log(f"encounter observe_event failed: {exc!r}")
            action["event"] = {"ok": False, "error": repr(exc)}

        return action

    # ---------------------------------------------------------------- 战斗：开/关/回合

    def _open_encounter(
        self,
        *,
        state: dict[str, Any],
        run_id: str,
        act_id: str,
        floor: int | None,
        group: str,
    ) -> None:
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        combat = state.get("combat") if isinstance(state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}

        # encounter_seq：同一 (act, floor) 下的第几场（★ 用于 encounter_key 唯一性）
        floor_tag = (act_id, "" if floor is None else str(floor))
        if floor_tag != self._last_seq_floor:
            self._last_seq_floor = floor_tag
            self._cur_seq = 0
        else:
            self._cur_seq += 1

        self._cur_key = (act_id, "" if floor is None else str(floor), group)
        self._cur_turns = []
        self._cur_start_hp = _as_int(player.get("current_hp"))
        self._cur_lowest_hp = self._cur_start_hp
        self._cur_meta = {
            "run_id": run_id,
            "act_id": act_id,
            "floor": floor,
            "enemy_group_key": group,
            "encounter_seq": self._cur_seq,
            "ascension": _as_int(run.get("ascension")),
            "ascension_effects": _slim_ascension_effects(run.get("ascension_effects")),
            "character_id": _as_str_or_none(run.get("character_id")),
            "boss_id": _as_str_or_none(run.get("boss_id")),
            "node_type": _current_node_type(state),
            "enemies_seen": [
                str(e.get("enemy_id"))
                for e in (combat.get("enemies") or [])
                if isinstance(e, dict) and e.get("enemy_id")
            ],
            "recorded_at": _iso(self._clock()),
        }
        self._append_turn(state, combat)

    def _append_turn(self, state: dict[str, Any], combat: dict[str, Any]) -> None:
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []

        hp = _as_int(player.get("current_hp"))
        if hp is not None:
            self._cur_lowest_hp = hp if self._cur_lowest_hp is None else min(self._cur_lowest_hp, hp)

        turn = {
            "turn": _as_int(state.get("turn")),
            "player_hp": hp,
            "player_block": _as_int(player.get("block")),
            "player_energy": _as_int(player.get("energy")),
            "hand_card_ids": [
                str(c.get("card_id")) for c in hand if isinstance(c, dict) and c.get("card_id")
            ],
            "hand_cards_playable": [
                _as_bool(c.get("playable"), False) for c in hand if isinstance(c, dict)
            ],
            "played_cards": self._played_cards(player, hand),
            "enemies": [_slim_enemy(e) for e in enemies if isinstance(e, dict)],
            "enemy_total_damage": sum(
                (_as_int(i.get("total_damage")) or 0)
                for e in enemies
                if isinstance(e, dict) and _as_bool(e.get("is_alive"), False)
                for i in (e.get("intents") or [])
                if isinstance(i, dict)
            ),
        }
        # ★ 处方（契约 §3.5），用**本回合**的数据
        turn["block_needed"] = compute_block_needed(
            lethal_risks=combat.get("lethal_risks"),
            end_turn_will_kill_player=combat.get("end_turn_will_kill_player"),
            enemies=[e for e in enemies if isinstance(e, dict)],
            player_block=_as_int(player.get("block")),
        )
        self._cur_turns.append(turn)

    def _played_cards(
        self, player: dict[str, Any], hand: list[Any]
    ) -> list[dict[str, Any]]:
        """`played_cards`（契约 §3.2 诚实边界）。

        ## ★★ 为什么全是 null
        `/state` 的 combat 层**没有**"本回合打出过哪些牌、打给谁"的历史字段。
        实测 `combat.player` 只有 `cards_played_this_turn` / `attacks_played_this_turn` /
        `skills_played_this_turn` 三个**计数**（t31 实机核对：player 的 14 个键里
        只有这三个含 "play"）⇒ 能得到**数量**，得不到**是哪张**、更得不到**打给谁**。

        ⇒ 规范决策（契约 §3.2 / §8.5）：字段保留、值允许 null，
          并**明标"Inferred, not measured"** —— 不假装有这个信息。
          要精确需要 mod 侧新增事件（契约 §8.5 标为"待评估"，非 v1 必做）。
        """
        count = _as_int(player.get("cards_played_this_turn"))
        if not count or count <= 0:
            return []
        return [
            {
                "card_id": None,
                "target_index": None,
                # ★ 这句话必须留在数据里，读库的人才知道这不是实测值。
                "_note": "Inferred, not measured: /state has no per-card target field",
            }
            for _ in range(count)
        ]

    def _close_encounter(self, *, ended_by: str, game_over: dict[str, Any] | None) -> None:
        if self._cur_key is None:
            return
        act_id, floor_s, group = self._cur_key
        turns = self._cur_turns

        # ★★ 「末 tick」= 本遭遇**最后一次观测**那条，不是"最后一条碰巧有 hp 的"。
        #   契约 §3.3：`true_hp_loss` 端点缺 ⇒ null。
        #   ⚠️ 我第一版写的是 `for t in reversed(turns): if isinstance(hp, int): break`
        #      —— 那会**跨过**末 tick 的缺失、回溯到一个更旧的 hp，
        #      等于用旧数据冒充末态（实测：末 tick 缺 hp 时得 0，真值应为 null）。
        last_hp = turns[-1].get("player_hp") if turns else None
        if not isinstance(last_hp, int):
            last_hp = None

        # ★ 结局判定（契约 §3.3）
        result = "unknown"
        if game_over is not None and game_over.get("is_victory") is False:
            result = "defeat"
        elif last_hp == 0:
            result = "defeat"
        elif turns:
            # ★ 胜利看的是**末 tick 的敌人是否全死**。
            #   修掉 `_enemy_group_key` 的 is_alive 过滤后，全死那一 tick 终于能被记录，
            #   所以这条分支现在**真的可达**（此前恒不可达 ⇒ victory 永远判不出）。
            final_enemies = turns[-1].get("enemies") or []
            alive = [
                e for e in final_enemies
                if isinstance(e, dict) and _as_bool(e.get("is_alive"), False)
            ]
            if final_enemies and not alive:
                result = "victory"

        # ★ true_hp_loss = max(0, 首 tick hp - 末 tick hp)；两端缺一 ⇒ null（契约 §1/§3.3）
        if self._cur_start_hp is None or last_hp is None:
            true_hp_loss: int | None = None
        else:
            true_hp_loss = max(0, self._cur_start_hp - last_hp)

        floor = _as_int(floor_s)
        record = {
            "encounter_key": {
                # ★ run_id 取【顶层】（§1 未指明层级；实测 run 层根本没有该键）
                "run_id": self._cur_meta.get("run_id"),
                "act_id": act_id,
                "floor": floor,
                "encounter_seq": self._cur_meta.get("encounter_seq"),
            },
            "enemy_group_key": group,
            "enemy_ids": [e for e in (self._cur_meta.get("enemies_seen") or [])],
            "ascension": self._cur_meta.get("ascension"),
            "ascension_effects": self._cur_meta.get("ascension_effects"),
            "character_id": self._cur_meta.get("character_id"),
            "boss_id": self._cur_meta.get("boss_id"),
            "node_type": self._cur_meta.get("node_type"),
            "recorded_at": self._cur_meta.get("recorded_at"),
            "turns": turns,
            "outcome": {
                "result": result,
                "total_turns": len(turns),
                "true_hp_loss": true_hp_loss,
                "lowest_hp_seen": self._cur_lowest_hp,
                "ended_by": ended_by,
            },
            # ★ 单体级：每只怪一行，经 (encounter_key, enemy_index) 与遭遇级关联（契约 §3.4）
            "enemy_rows": self._build_enemy_rows(turns, true_hp_loss, result),
        }
        self._encounters.append(record)
        self._enforce_cap()
        self._reindex_originals()
        self._dirty = True
        self.save()

        self._cur_key = None
        self._cur_turns = []
        self._cur_start_hp = None
        self._cur_lowest_hp = None

    def _build_enemy_rows(
        self, turns: list[dict[str, Any]], true_hp_loss: int | None, result: str
    ) -> list[dict[str, Any]]:
        """单体级行（契约 §3.4）。

        ★ 关键口径：**不把群体伤害硬分给某一只**。
          每只的 `intent_total_damage` 只表示【它本回合将要造成多少】，
          不是"已经造成多少" —— 后者 /state 里没有（强写就是编造）。
        """
        seen: dict[int, dict[str, Any]] = {}
        for turn in turns:
            for idx, enemy in enumerate(turn.get("enemies") or []):
                if not isinstance(enemy, dict):
                    continue
                eid = enemy.get("enemy_id")
                if not eid or _is_room_placeholder(eid):
                    continue
                row = seen.setdefault(
                    idx,
                    {
                        "enemy_index": idx,
                        "enemy_id": str(eid),
                        "name": enemy.get("name"),
                        "max_hp": enemy.get("max_hp"),
                        "is_alive_end": enemy.get("is_alive"),
                        "intent_total_damage_max": None,
                        "powers_seen": [],
                    },
                )
                row["is_alive_end"] = enemy.get("is_alive")
                dmg = enemy.get("intent_total_damage")
                if dmg is not None:
                    prev = row["intent_total_damage_max"]
                    row["intent_total_damage_max"] = dmg if prev is None else max(prev, dmg)
                for pw in enemy.get("powers") or []:
                    if isinstance(pw, dict) and pw.get("power_id"):
                        if pw["power_id"] not in row["powers_seen"]:
                            row["powers_seen"].append(pw["power_id"])
        rows = []
        for idx in sorted(seen):
            row = seen[idx]
            rows.append(
                {
                    **row,
                    # ★ 人可读的字段名澄清（契约 §3.4 要求写进注释）：
                    #   intent_total_damage_max = 它【将要】打我的最大值，不是它已打我的量
                    "_intent_semantics": "incoming_intent_per_turn_not_realized_damage",
                    # 结局按"这只还活着吗"判定（不把整场胜负硬分给单只）
                    "result": "defeat" if result == "defeat" else (
                        "victory" if row.get("is_alive_end") is False else "unknown"
                    ),
                    "encounter_true_hp_loss": true_hp_loss,
                }
            )
        return rows

    # ---------------------------------------------------------------- 派生统计

    def _enforce_cap(self) -> None:
        """超上限时按 `recorded_at` 淘汰最旧，但**先并入 enemy_index**（契约 §5.3）。

        ★ "降采样而非直接丢" —— 直接丢会让长期统计失真。
        """
        if len(self._encounters) <= ENCOUNTER_CAP:
            return
        overflow = len(self._encounters) - ENCOUNTER_CAP
        dropped = self._encounters[:overflow]
        for rec in dropped:
            self._absorb_into_index(rec)
        self._encounters = self._encounters[overflow:]

    def _absorb_into_index(self, record: dict[str, Any]) -> None:
        outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else {}
        for row in record.get("enemy_rows") or []:
            if not isinstance(row, dict) or not row.get("enemy_id"):
                continue
            eid = str(row["enemy_id"])
            entry = self._enemy_index.setdefault(eid, self._blank_index_entry())
            entry["samples"] = int(entry.get("samples") or 0) + 1
            loss = row.get("encounter_true_hp_loss")
            if isinstance(loss, int):
                entry["_loss_sum"] = float(entry.get("_loss_sum") or 0.0) + loss
                entry["_loss_n"] = int(entry.get("_loss_n") or 0) + 1
            entry["_turn_sum"] = float(entry.get("_turn_sum") or 0.0) + int(
                outcome.get("total_turns") or 0
            )
            entry["_turn_n"] = int(entry.get("_turn_n") or 0) + 1
            if row.get("result") == "defeat":
                entry["deaths"] = int(entry.get("deaths") or 0) + 1
            entry["last_updated"] = _iso(self._clock())
            _recompute_index_entry(entry)

    @staticmethod
    def _blank_index_entry() -> dict[str, Any]:
        """新建一个 `enemy_index` 条目（内存态；落盘时由 `_public_index` 裁剪）。"""
        return {
            "samples": 0,
            "avg_true_hp_loss": None,
            "p90_true_hp_loss": None,
            "avg_turns": None,
            "deaths": 0,
            "last_updated": None,
            # --- 以下为内存中间态，**不落盘**（见 `_public_index`）---
            "_loss_sum": 0.0,
            "_loss_n": 0,
            "_turn_sum": 0.0,
            "_turn_n": 0,
            "_loss_values": [],
        }

    def _reindex_originals(self) -> None:
        """由 `encounters` **完全重建** `enemy_index`（契约 §5.2 要求可重建）。

        ★ 这是"派生统计必须可由原始完全重建"的实现：任何时刻调它，
          结果只依赖 `self._encounters`。
        """
        self._enemy_index = {}
        for rec in self._encounters:
            outcome = rec.get("outcome") if isinstance(rec.get("outcome"), dict) else {}
            for row in rec.get("enemy_rows") or []:
                if not isinstance(row, dict) or not row.get("enemy_id"):
                    continue
                eid = str(row["enemy_id"])
                entry = self._enemy_index.setdefault(eid, self._blank_index_entry())
                entry["samples"] = int(entry.get("samples") or 0) + 1
                loss = row.get("encounter_true_hp_loss")
                if isinstance(loss, int):
                    entry["_loss_sum"] = float(entry.get("_loss_sum") or 0.0) + loss
                    entry["_loss_n"] = int(entry.get("_loss_n") or 0) + 1
                    entry["_loss_values"].append(loss)
                entry["_turn_sum"] = float(entry.get("_turn_sum") or 0.0) + int(
                    outcome.get("total_turns") or 0
                )
                entry["_turn_n"] = int(entry.get("_turn_n") or 0) + 1
                if row.get("result") == "defeat":
                    entry["deaths"] = int(entry.get("deaths") or 0) + 1
                entry["last_updated"] = rec.get("recorded_at")
        for entry in self._enemy_index.values():
            _recompute_index_entry(entry)

    def rebuild_index(self) -> dict[str, dict[str, Any]]:
        """public：从原始重建派生表（测试用；也保证"可重建"是**可调用**的）。"""
        self._reindex_originals()
        return self._enemy_index

    # ---------------------------------------------------------------- 事件侧

    def _observe_event(self, state: dict[str, Any]) -> dict[str, Any]:
        """事件选项采集（契约 §4）。"""
        ev = state.get("event") if isinstance(state.get("event"), dict) else None

        # ★★ 先结算"待定选择"，**即使本 tick 已经不在 EVENT 屏**。
        #   我第一版把这段放在 `if ev is None: return` **之后** ⇒
        #   事件结束（切到 MAP/COMBAT）时 `event` 变 None ⇒ 提前返回 ⇒
        #   **待定选择永远结算不了 ⇒ 事件记录一条都不落盘**（门抓到 7 个红）。
        #   ⇒ 契约 §4 要的"跨 tick diff 判定 chosen"必须涵盖**离开事件屏**那一步。
        settled = None
        if self._ev_pending is not None:
            now_key = _event_screen_key(state, str(self._ev_pending.get("event_id") or ""))
            if now_key != self._ev_pending.get("screen_key"):
                settled = self._settle_event_option(state)

        if ev is None:
            # 不在事件屏：重置本屏记忆，返回已结算信息
            self._ev_screen_key = None
            self._ev_options = {}
            self._ev_before = {}
            return {"ok": True, "screen": False, "settled": settled}

        event_id = _as_str_or_none(ev.get("event_id"))
        if not event_id:
            return {"ok": True, "screen": True, "skipped": "no_event_id", "settled": settled}
        options = ev.get("options") if isinstance(ev.get("options"), list) else []
        screen_key = _event_screen_key(state, event_id)

        if screen_key != self._ev_screen_key:
            # 新的事件屏：记下"选前"基线
            self._ev_screen_key = screen_key
            self._ev_options = {}
            self._ev_before = _event_baseline(state)

        for opt in options:
            if not isinstance(opt, dict):
                continue
            idx = _as_int(opt.get("index"))
            if idx is None:
                continue
            self._ev_options[idx] = {
                # ★ 以【实机抓到的 8 个键】为准（契约 §2.4 / §4.2），不得只用 5 个
                "event_id": event_id,
                "option_index": idx,
                "title": opt.get("title"),
                "description": opt.get("description"),
                "text_key": opt.get("text_key"),
                "is_locked": _as_bool(opt.get("is_locked"), False),
                "is_proceed": _as_bool(opt.get("is_proceed"), False),
                "will_kill_player": _as_bool(opt.get("will_kill_player"), False),
                "has_relic_preview": _as_bool(opt.get("has_relic_preview"), False),
            }
        return {
            "ok": True, "screen": True, "options": len(self._ev_options), "settled": settled,
        }

    def note_event_choice(self, state: dict[str, Any], option_index: int) -> None:
        """service 在**真的派发** `choose_event_option` 后调 —— 记下"某个 index 被选了"。

        ★ 为什么不在这里判定"被接受"：契约 §4.3 说拒绝信号需先实机确认可观测性
          （本轮未触发 ⇒ `unverified`）。所以本方法只**记事实**，
          等下一个 tick 的差分来判定代价/收益（`_settle_event_option`）。
        """
        if not isinstance(option_index, int):
            return
        event_id = ""
        ev = state.get("event") if isinstance(state.get("event"), dict) else {}
        event_id = _as_str_or_none(ev.get("event_id")) or ""
        if not event_id:
            return
        self._ev_pending = {
            "screen_key": _event_screen_key(state, event_id),
            "event_id": event_id,
            "option_index": option_index,
            "chosen_at": self._clock(),
            "before": dict(self._ev_before or {}),
            "options_snapshot": {k: v for k, v in self._ev_options.items()},
        }

    def _settle_event_option(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """把待定选择结算成一条事件选项记录（契约 §4.2/§4.3）。"""
        pending = self._ev_pending
        self._ev_pending = None
        if not pending:
            return None
        before = pending.get("before") if isinstance(pending.get("before"), dict) else {}
        after = _event_baseline(state)

        screen_now = str(state.get("screen") or "")
        unreliable = screen_now == "COMBAT"
        if unreliable:
            # ★ 契约 §4.3：选完直接进战斗 ⇒ 差分失真 ⇒ 代价写 null + 理由
            cost_hp = cost_max_hp = cost_gold = None
            gain_cards: Any = None
            gain_relics: Any = None
            gain_potions: Any = None
            reason = REASON_POST_EVENT_COMBAT
        else:
            cost_hp, cost_max_hp, cost_gold = _diff_costs(before, after)
            gain_cards = _diff_gain(before.get("deck"), after.get("deck"))
            gain_relics = _diff_gain(before.get("relics"), after.get("relics"))
            gain_potions = _diff_gain(before.get("potions"), after.get("potions"))
            reason = None

        event_id = str(pending.get("event_id") or "")
        chosen_index = pending.get("option_index")
        records = []
        options_snapshot = pending.get("options_snapshot") or {}
        # ★★ 队长裁定③（2026-10-20 03:0x）：`chosen_unverified` 必须落库。
        #   理由（实机实测）：事件一结束，游戏就把 options 换成单个 PROCEED
        #   （实测 WHISPERING_HOLLOW is_finished=True ⇒ options 只剩 1 个）
        #   ⇒ 若我在【结束态】才去看选项集，就再也回看不到"玩家选了哪个"。
        #   ⇒ 故 chosen 由**跨 tick diff** 判定；判不出时一律 chosen=false +
        #     chosen_unverified=true —— ★ 不编造。
        #   触发条件：本屏选项集为空（结束态才进来 / 事件屏瞬时不可读）。
        chosen_unverified = not options_snapshot
        for idx in sorted(options_snapshot):
            opt = dict(options_snapshot[idx])
            opt["chosen"] = (False if chosen_unverified else idx == chosen_index)
            opt["chosen_unverified"] = chosen_unverified
            opt["cost_hp"] = cost_hp if idx == chosen_index else (None if unreliable else 0)
            opt["cost_max_hp"] = cost_max_hp if idx == chosen_index else (None if unreliable else 0)
            opt["cost_gold"] = cost_gold if idx == chosen_index else (None if unreliable else 0)
            opt["gain_cards"] = (gain_cards or []) if idx == chosen_index else []
            opt["gain_relics"] = (gain_relics or []) if idx == chosen_index else []
            opt["gain_potions"] = (gain_potions or []) if idx == chosen_index else []
            opt["rejected"] = False
            opt["reject_reason"] = None
            if reason:
                opt["cost_unreliable_reason"] = reason
            opt["seen_at"] = _iso(self._clock())
            opt["sample_seq"] = self._ev_seqs.get(event_id, 0)
            records.append(opt)

        self._ev_seqs[event_id] = self._ev_seqs.get(event_id, 0) + 1
        # ★ 选项集为空 ⇒ 没有可落的行为；但仍返回结算结果（含 chosen_unverified 语义），
        #   让日志/调用方知道"这次没判出来"，而不是静默什么都不说。
        if not records:
            return {
                "event_id": event_id, "chosen": None, "records": 0,
                "chosen_unverified": True,
            }
        path = self._event_log_path()
        try:
            existing: list[Any] = []
            if path and os.path.exists(path):
                try:
                    parsed = json.loads(open(path, "r", encoding="utf-8").read())
                    existing = parsed if isinstance(parsed, list) else []
                except ValueError:
                    existing = []
            existing.extend(records)
            if path:
                tmp = "%s.%d.%s.tmp" % (path, os.getpid(), uuid.uuid4().hex[:8])
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001 —— 采集落盘失败不得打断游玩
            self._log(f"event option record failed: {exc!r}")
        return {
            "event_id": event_id, "chosen": chosen_index, "records": len(records),
            "chosen_unverified": chosen_unverified,
        }

    def _event_log_path(self) -> str | None:
        if not self._path:
            return None
        return os.path.join(os.path.dirname(self._path), "encounter_events.json")

    # ---------------------------------------------------------------- 叙述（§9）

    def render_enemy_narration(
        self, enemy_id: str, *, incoming_damage: int | None = None, player_block: int | None = None
    ) -> dict[str, Any]:
        """把某只怪的实例知识渲染成**猫娘第一人称**的一句话（契约 §9）。

        ★ 规范硬要求（契约 §9.1）：
          ① 用"我"作主语；
          ② **不得**出现「插件」「系统」「对方」「根据记录」「数据显示」等**转达性**措辞；
          ③ 数字必须来自记录，不得润色；
          ④ 不确定就说不确定（与 §3.6 的 null 口径一致）。

        ★ 渲染规则（契约 §9.4）：
          · 有 intent 伤害 ⇒ 说具体数字
          · `true_hp_loss = null` ⇒ 不说"掉了多少血"，改说"这次没量准"
          · 样本不足（§6.4 单例怪）⇒ 不说"我通常如何"，改说"我只见过它一次"
        """
        entry = self._enemy_index.get(str(enemy_id))
        samples = int((entry or {}).get("samples") or 0)
        name = _display_name(enemy_id)

        parts: list[str] = []
        if samples <= 0:
            parts.append(f"我还没跟{name}交过手。")
        elif samples == 1:
            loss = (entry or {}).get("avg_true_hp_loss")
            if loss is None:
                parts.append(f"我只见过{name}一次，那次没量准掉了我多少血。")
            else:
                parts.append(f"我只见过{name}一次，那次它啃掉我 {int(loss)} 点血。")
        else:
            avg = (entry or {}).get("avg_true_hp_loss")
            p90 = (entry or {}).get("p90_true_hp_loss")
            deaths = int((entry or {}).get("deaths") or 0)
            if isinstance(avg, int | float):
                line = f"我打过{name} {samples} 次，平均被它啃掉 {_fmt_num(avg)} 点血"
                if isinstance(p90, int):
                    line += f"，最惨那次掉了 {p90} 点"
                parts.append(line + "。")
            else:
                parts.append(f"我打过{name} {samples} 次，但损血没量准。")
            if deaths:
                parts.append(f"它有 {deaths} 次把我打死了。")

        if incoming_damage is not None:
            line = f"它这回合要打过来 {incoming_damage} 点"
            if player_block is not None:
                need = max(0, incoming_damage - player_block)
                line += f"，我手上只有 {player_block} 点格挡"
                if need > 0:
                    line += f"，得先补到 {need} 点才敢交回合"
                else:
                    line += "，够扛了"
            parts.append(line + "。")

        text = "".join(parts) or f"我还得多见几次{name}才敢下判断。"
        return {"enemy_id": str(enemy_id), "name": name, "samples": samples, "text": text}

    def render_event_narration(self, event_id: str) -> dict[str, Any]:
        """事件选项的实例叙述（第一人称）。

        ★ 契约 §4.2 要求 `is_locked=true` 必须记 —— 因为它是"此选项在当前条件下
          不可选"的知识（例如金币不足）。叙述里也要说出来，否则我会给出
          **不可执行**的建议。
        """
        recs = self._event_records_for(event_id)
        seen = len(recs)
        title = _display_name(event_id)
        if not seen:
            return {"event_id": str(event_id), "samples": 0, "text": f"我还没遇过{title}。"}
        locked = [r for r in recs if r.get("is_locked")]
        chosen = [r for r in recs if r.get("chosen")]
        text = f"我遇过{title} {seen} 次。"
        if locked:
            titles = "、".join(str(r.get("title")) for r in locked[:2] if r.get("title"))
            if titles:
                text += f"有几条路当时走不通（{titles}），我只好换别的。"
        if chosen:
            last = chosen[-1]
            text += f"上次我选了「{last.get('title')}」。"
        return {"event_id": str(event_id), "samples": seen, "text": text}

    def _event_records_for(self, event_id: str) -> list[dict[str, Any]]:
        path = self._event_log_path()
        if not path or not os.path.exists(path):
            return []
        try:
            data = json.loads(open(path, "r", encoding="utf-8").read())
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [r for r in data if isinstance(r, dict) and str(r.get("event_id")) == str(event_id)]

    # ---------------------------------------------------------------- 杂项

    def _log(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.info(message)
        except Exception:
            pass

    def stats(self) -> dict[str, Any]:
        """只读摘要（供 status/日志；不进决策）。"""
        return {
            "encounters": len(self._encounters),
            "enemy_ids": len(self._enemy_index),
            "schema_version": SCHEMA_VERSION,
            "in_progress": self._cur_key is not None,
            "dirty": self._dirty,
        }


# ======================================================================================
# 模块级工具
# ======================================================================================


def _extract_run_id(state: dict[str, Any]) -> str:
    """★ 从**顶层**取 run_id（t31 实机实测；见模块 docstring 第 2 条）。

    兼容：若顶层缺失，才回退到 `run` 层（老 mod 形状），最后才空串。
    """
    top = _as_str_or_none(state.get("run_id"))
    if top:
        return top
    run = state.get("run") if isinstance(state.get("run"), dict) else {}
    return _as_str_or_none(run.get("run_id")) or ""


def _event_screen_key(state: dict[str, Any], event_id: str) -> str:
    """事件屏隔离键 —— 用于判定「还是不是同一个事件屏」。

    ★★ 必须含 `screen` 本身（这是我第一版漏掉的，门抓到 8 个红）：
      若只由 `run_id|act|floor|event_id` 组成，则**离开事件屏到同层另一屏**
      （MAP/REWARD 等，实测 floor 不变）时 key **完全相同**
      ⇒ 判为"还在同一屏"⇒ 待定选择**永远不结算** ⇒ 事件记录一条都不落盘。

    ★ 我第一版还用了 `is_finished` 当页内阶段标志，但实测它是**误导**的：
      `is_finished` 在**选择被接受之前**就可能为 True（实机 WHISPERING_HOLLOW
      抓到 is_finished=True 且只剩 PROCEED）⇒ 把它放进 key 会让
      "同一屏的两个阶段"被当成两屏，反而不该结算时结算。已移除。

    ⇒ 正确构成：`screen|run_id|act_id|floor|event_id`
      · 含 screen ⇒ 换屏必然触发结算（本轮修正）
      · 含 run_id/act/floor ⇒ 换局换层算新屏（t28 教训：不隔离会串屏）
      · 含 event_id ⇒ 同屏换事件算新屏
    """
    run = state.get("run") if isinstance(state.get("run"), dict) else {}
    return "|".join(
        [
            str(state.get("screen") or ""),
            _extract_run_id(state),
            str(run.get("act_id") or ""),
            str(run.get("floor") or ""),
            str(event_id),
        ]
    )


def _event_baseline(state: dict[str, Any]) -> dict[str, Any]:
    """选前基线（供跨 tick diff 推代价/收益，契约 §4.3）。

    ★ `deck` 的取法要**宽容**：实机是对象列表（取 `card_id`），但老形状/测试替身
      可能是纯字符串列表。若只认 dict，则字符串条目会被**静默丢掉** ⇒
      基线恒为空 ⇒ `gain_cards` 永远算不出来（门抓到过这个形态）。
      ⇒ 两种形态都收（`_entry_token`）。
    """
    run = state.get("run") if isinstance(state.get("run"), dict) else {}
    deck = run.get("deck") if isinstance(run.get("deck"), list) else []
    relics = run.get("relics") if isinstance(run.get("relics"), list) else []
    potions = run.get("potions") if isinstance(run.get("potions"), list) else []
    return {
        "hp": _as_int(run.get("current_hp")),
        "max_hp": _as_int(run.get("max_hp")),
        "gold": _as_int(run.get("gold")),
        "deck": [_entry_token(c) for c in deck],
        "relics": [_entry_token(r) for r in relics],
        "potions": [_entry_token(p) for p in potions],
    }


def _entry_token(item: Any) -> Any:
    """把牌/遗物/药水条目归一成一个可比较的身份串。

    ★ 兼容 dict（取 id 字段）与纯字符串（直接用）—— 见 `_event_baseline` 注释。
    """
    return _id_of(item)


def _id_of(item: Any) -> Any:
    if isinstance(item, dict):
        for key in ("relic_id", "potion_id", "id", "card_id", "name"):
            if item.get(key):
                return item.get(key)
        return None
    return item


def _diff_costs(before: dict[str, Any], after: dict[str, Any]) -> tuple[Any, Any, Any]:
    """代价 = 选前 - 选后（正数表示消耗）。算不出 ⇒ None（不写 0）。"""
    def _delta(key: str) -> Any:
        b, a = before.get(key), after.get(key)
        if not isinstance(b, int) or not isinstance(a, int):
            return None
        return max(0, b - a)

    return _delta("hp"), _delta("max_hp"), _delta("gold")


def _diff_gain(before: Any, after: Any) -> list[str]:
    """收益 = 选后新增的条目（列表差）。"""
    b = before if isinstance(before, list) else []
    a = after if isinstance(after, list) else []
    return [str(x) for x in a if x is not None and x not in b]


def _current_node_type(state: dict[str, Any]) -> str | None:
    """当前地图节点的 node_type（契约 §2.5 —— 精英判定的**权威**字段）。"""
    mp = state.get("map") if isinstance(state.get("map"), dict) else {}
    nodes = mp.get("nodes") if isinstance(mp.get("nodes"), list) else []
    cur = mp.get("current_node") if isinstance(mp.get("current_node"), dict) else {}
    row, col = cur.get("row"), cur.get("col")
    if row is None or col is None:
        return None
    for n in nodes:
        if isinstance(n, dict) and n.get("row") == row and n.get("col") == col:
            return _as_str_or_none(n.get("node_type"))
    return None


def _slim_enemy(enemy: dict[str, Any]) -> dict[str, Any]:
    """单只敌人的本回合快照（契约 §3.2 的 `enemies[]` 表）。

    ★ `intent_total_damage` / `intent_hits` 在**非攻击时保持 null**（不是 0）——
      契约 §10.5 实测：`intent_type=StatusCard` 时三者全 null。
      累加时用 `or 0`，但**写库保留 null**。
    """
    intents = enemy.get("intents") if isinstance(enemy.get("intents"), list) else []
    dmg = None
    hits = None
    for i in intents:
        if not isinstance(i, dict):
            continue
        d = _as_int(i.get("total_damage"))
        h = _as_int(i.get("hits"))
        if d is not None:
            dmg = d if dmg is None else max(dmg, d)
        if h is not None:
            hits = h if hits is None else max(hits, h)
    return {
        "enemy_id": _as_str_or_none(enemy.get("enemy_id")),
        "name": _as_str_or_none(enemy.get("name")),
        "current_hp": _as_int(enemy.get("current_hp")),
        "max_hp": _as_int(enemy.get("max_hp")),
        "block": _as_int(enemy.get("block")),
        "is_alive": _as_bool(enemy.get("is_alive"), False),
        "is_hittable": _as_bool(enemy.get("is_hittable"), False),
        "intent_move_id": _as_str_or_none(enemy.get("move_id")),
        "intent_types": [
            str(i.get("intent_type")) for i in intents if isinstance(i, dict) and i.get("intent_type")
        ],
        # ★ null 表示"无伤害概念"，不是 0
        "intent_total_damage": dmg,
        "intent_hits": hits,
        "powers": [
            {
                "power_id": _as_str_or_none(p.get("power_id")),
                "amount": _as_int(p.get("amount")),
                "is_debuff": _as_bool(p.get("is_debuff"), False),
            }
            for p in (enemy.get("powers") or [])
            if isinstance(p, dict)
        ],
    }


def _slim_ascension_effects(effects: Any) -> list[dict[str, Any]]:
    """进阶效果（契约 §2.6：A10 的敌人更难，同只怪处方随 ascension 不同）。"""
    if not isinstance(effects, list):
        return []
    out = []
    for e in effects:
        if isinstance(e, dict):
            out.append({"id": _as_str_or_none(e.get("id")), "name": _as_str_or_none(e.get("name"))})
    return out


#: 常见 enemy_id → 中文显示名（叙述用）。★ 只影响**措辞**，不影响任何数据字段。
_ENEMY_CN: dict[str, str] = {
    "WATERFALL_GIANT": "瀑布巨兽",
    "BYRDONIS": "多尼斯异鸟",
    "NIBBIT": "尼比特",
    "CUBEX_CONSTRUCT": "立方构造体",
    "TWIG_SLIME_S": "树枝史莱姆（小）",
    "TWIG_SLIME_M": "树枝史莱姆（中）",
    "LEAF_SLIME_S": "树叶史莱姆（小）",
    "SEAPUNK": "海痞",
    "SHRINKER_BEETLE": "缩身甲虫",
    "BYGONE_EFFIGY": "往昔雕像",
    "CORPSE_SLUG": "腐尸鼻涕虫",
    "TERROR_EEL": "恐惧鳗",
    "FUZZY_WURM_CRAWLER": "绒毛虫爬行者",
    "FLYCONID": "飞菌",
    "SLUDGE_SPINNER": "淤泥纺锤",
    "VINE_SHAMBLER": "藤蔓行者",
}


def _display_name(identifier: Any) -> str:
    """把 enemy_id / event_id 变成叙述里自然的称呼（中文优先，回退原 id）。"""
    key = str(identifier or "")
    return _ENEMY_CN.get(key, key or "那家伙")


def _fmt_num(value: Any) -> str:
    if isinstance(value, float):
        return str(int(value)) if abs(value - int(value)) < 1e-9 else f"{value:.1f}"
    return str(value)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
