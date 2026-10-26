from __future__ import annotations

import asyncio
import copy
import math
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async

from . import settings_schema


class QQAutoReplyConfigStore:
    FILE_NAME = "business_config.json"

    #: 枚举取值**从 settings_schema 派生**，不再手工镜像。
    #
    # 这个集合曾经是手抄的一份副本（`{"text","voice","both"}`），而下面的归一化是
    # 「不在表里就**静默**改成默认值」。于是往真源 enum 里加一个新取值时，界面能选、
    # 能存，运行时却被无声改回旧默认 —— 又一个"能配置但无效"的静默失效，而且连日志都没有。
    # `qq_connection_mode` 早就是从真源派生的（`__init__.CONNECTION_MODES`），所以这里不是
    # "有意分家"，是漏改。
    #
    # `VALID_STRATEGY_MODES` / `_normalize_strategy_mode` 随「回复策略」一起删除了
    # （2026-09-27）：那个键已经没有第二个取值，归一化也就没有意义。
    VALID_REPLY_MODES = frozenset(
        settings_schema.BY_KEY["reply_mode"].enum or ()
    )

    #: 上一次 load() 是否真的迁移/清理过键（调用方据此决定要不要落盘）。
    migration_applied: bool = False

    def __init__(self, base_dir: Path):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def default_backlog_labels() -> list[dict[str, Any]]:
        """默认关键词标签表。真相在 ``settings_schema.DEFAULT_BACKLOG_LABELS``。"""
        return copy.deepcopy(settings_schema.DEFAULT_BACKLOG_LABELS)

    @staticmethod
    def default_emotion_multipliers() -> dict[str, float]:
        """默认情绪倍率表。真相在 ``settings_schema.DEFAULT_EMOTION_MULTIPLIERS``。"""
        return copy.deepcopy(settings_schema.DEFAULT_EMOTION_MULTIPLIERS)

    @staticmethod
    def normalize_emotion_multipliers(value: Any) -> dict[str, float]:
        """归一「情绪 → 倍率」表。

        接受 JSON 对象（或已解析的 dict）。**非法即整份回退默认**而不是部分保留：
        这张表参与注意力涨跌计算，半份坏数据比没有数据更难查。键先去掉空白，
        值必须是有限数值；空表按非法处理（空表等于关掉所有情绪影响，多半是误清）。
        """
        if isinstance(value, str):
            import json as _json

            try:
                value = _json.loads(value or "{}")
            except (TypeError, ValueError):
                return QQAutoReplyConfigStore.default_emotion_multipliers()
        if not isinstance(value, dict) or not value:
            return QQAutoReplyConfigStore.default_emotion_multipliers()
        normalized: dict[str, float] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key or "").strip()
            if not key:
                return QQAutoReplyConfigStore.default_emotion_multipliers()
            try:
                number = float(raw_val)
            except (TypeError, ValueError):
                return QQAutoReplyConfigStore.default_emotion_multipliers()
            if not math.isfinite(number):
                return QQAutoReplyConfigStore.default_emotion_multipliers()
            normalized[key] = number
        return normalized

    @staticmethod
    def normalize_backlog_labels(labels: Any) -> list[dict[str, Any]]:
        if labels is None:
            return QQAutoReplyConfigStore.default_backlog_labels()
        if not isinstance(labels, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in labels:
            if not isinstance(item, dict):
                continue
            label_id = str(item.get("id") or "").strip()
            label_text = str(item.get("label") or "").strip()
            if not label_id or not label_text or label_id in seen_ids:
                continue
            keywords = item.get("keywords")
            if not isinstance(keywords, list):
                keywords = []
            normalized_keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
            priority = item.get("priority", 0)
            try:
                normalized_priority = int(priority)
            except Exception:
                normalized_priority = 0
            normalized.append({
                "id": label_id,
                "label": label_text,
                "keywords": normalized_keywords,
                "priority": normalized_priority,
            })
            seen_ids.add(label_id)
        return normalized

    @classmethod
    def normalize_reply_mode(cls, value: Any) -> str:
        mode = str(value or "").strip().lower()
        return mode if mode in cls.VALID_REPLY_MODES else "text"

    #: 两代注意力配置命名合并：旧 ``group_attention_*`` → 今天唯一一套
    #: ``attention_*``。只搬运「旧名有值」的语义，搬完一律删旧名，否则
    #: 配置文件里会长期并存两份同义键（这正是下面那批僵尸键的成因）。
    _LEGACY_ATTENTION_KEY_MAP = {
        "group_attention_max_score": "attention_max_score",
        "group_attention_min_threshold": "attention_min_threshold",
        "group_attention_focus_threshold": "attention_focus_threshold",
        "group_attention_focus_send_threshold": "attention_focus_hold_threshold",
        "group_attention_message_gain": "attention_batch_message_gain",
    }

    #: 只在 ``business_config.json`` 里留尸的键：schema、前端、任何 .py 都不再
    #: 认识它们（实测全仓 0 命中），但因为 ``load()``/``save()`` 的 update 语义
    #: 会一直随文件传递。不删就等于「配置里有一半注意力旋钮是假的」。
    #: 后半批是疲劳/作息系统整体删除后的遗留键。
    _LEGACY_ZOMBIE_KEYS = (
        "group_attention_decay_per_second",
        "group_attention_focus_cooldown_seconds",
        "group_attention_focus_lock_seconds",
        "group_attention_focus_rise_seconds",
        "group_attention_keyword_boost_scale",
        "group_attention_message_recovery",
        "group_attention_reply_penalty",
        # 疲劳/作息系统整体删除后留下的键（schema/前端/运行时都不再认识）。
        "fatigue_enabled",
        "fatigue_circadian_peak_hour",
        "fatigue_circadian_low_hour",
        "fatigue_session_per_reply",
        "fatigue_awake_idle_timeout",
        "fatigue_tiers",
        # open 级删除后没人再读的概率键（群概率闸与私聊转发概率）。
        "open_reply_probability",
        "truth_reply_probability",
        # 「回复策略」删除后的遗留键（2026-09-27）。它在模式合并后只剩一个取值，
        # 也就是个假旋钮：界面选不动、代码里恒真。与 enable_group_attention 一样，
        # 删掉的是**配置键**而不是某个功能 —— 回复策略就是动态注意力那一条路。
        "strategy_mode",
        # neko_scene 时代的两条"唤醒"键（2026-09-27）。它们的描述一直写着
        # 「已废弃（改用 attention + backlog_labels）」，但键本身留在 schema 里 ——
        # 于是配置里挂着两个看起来能调、实际零消费方的旋钮。昵称/别名那一半能力
        # 现在由 `addressee_names` + `addressing.py` 正式接管，这两条没有存在理由了。
        "neko_dynamic_waking_users",
        "neko_dynamic_waking_keywords",
        # 「冷场破冰阈值」删除后的遗留键（2026-09-29）。它数的是**焦点切换次数** ——
        # 跨群焦点取舍删掉后没有切换事件可数，判据换成 `icebreaker_idle_seconds`
        # （**每群自己**静默多久）。留着它等于配置里挂一个改了不生效的旋钮。
        "icebreaker_cold_threshold",
        # rise/fall **相位机**删除后的遗留键（2026-09-29）。整套蜜月 + 让位的前提是
        # "同一时刻只有一个群能说话"；跨群取舍删除后热度只由"这个群最后一条消息离现在
        # 多远"决定（`attention_heat_warm_gap_seconds`）。三个键里
        # `attention_fall_boost_attenuation` 其实**更早就已经是死键**（消息加成不再按
        # 相位衰减，只剩一个没人调用的 accessor），另外两个是相位机的计时器。
        "attention_honeymoon_seconds",
        "attention_fall_seconds",
        "attention_fall_boost_attenuation",
        # **主动破冰 + 休眠**整套删除后的遗留键（2026-09-29）。使用者口径：
        # 「不能用时间判断，需要换一个策略」→ 四个事件驱动备选给他选 → 「干脆不要这个先」。
        # 于是触发判据（`icebreaker_idle_seconds`）、按住时长（`icebreaker_hold_seconds`）、
        # 休眠开关与自动醒秒数、以及只服务破冰的话题表（`proactive_topics`）一起退役。
        # 休眠的**唯一触发源**就是"她主动开口没人接"，所以它也一并删除（连带状态字段：
        # 老存档里的 `dormant_*` 不再读，免得那些群被一个没有触发源的标记冻住分数）。
        "icebreaker_idle_seconds",
        "icebreaker_hold_seconds",
        "icebreaker_dormant_enabled",
        "icebreaker_dormant_seconds",
        "proactive_topics",
        # 「到点自动醒」删除后的遗留键（2026-09-29 使用者口径：「到点自动醒也不要」）。
        # 它的唯一作用是给 `dormant_until` 写一个未来时刻，那套模型已随键一起删除：
        # 现在休眠就是一个布尔量，醒来只有点名 / 群里热起来两条路。
        "dormancy_auto_wake_seconds",
    )

    #: 前缀兜底：逐个列名字**必然会漏**（``fatigue_tiers`` 就是这么漏掉的——它只出现
    #: 在真机配置文件里，repo 内 0 命中，纯靠肉眼对不出来）。历史上发生过两类：
    #: 整代配置被改名（``group_attention_*``）与整块功能被删除（``fatigue*``）。
    #: 映射表先跑，所以 ``group_attention_*`` 里那 5 个活键已被搬走，这里只会清残留。
    _LEGACY_ZOMBIE_PREFIXES = ("group_attention_", "fatigue")

    @classmethod
    def _migrate_attention_keys(cls, settings: dict[str, Any]) -> bool:
        """就地合并两代注意力命名并清掉僵尸键。幂等，可反复调用。

        返回**是否真的改动了内容**：调用方据此决定要不要把结果落盘 ——
        迁移只改内存视图的话，配置文件会长期留着旧键（真机上就出现过：
        插件已按新键跑，磁盘里却还躺着 6 个 fatigue 键与 open 概率）。
        """
        changed = False
        for old, new in cls._LEGACY_ATTENTION_KEY_MAP.items():
            if old not in settings:
                continue
            changed = True
            legacy_value = settings.pop(old)
            # 新名已有值 ⇒ 以新名为准（它才是当前保存链路写的那个）；
            # 新名缺失才搬运旧值，避免把用户调过的参数悄悄退回默认。
            if new not in settings:
                settings[new] = legacy_value
        for zombie in cls._LEGACY_ZOMBIE_KEYS:
            if zombie in settings:
                settings.pop(zombie, None)
                changed = True
        for key in [k for k in settings if str(k).startswith(cls._LEGACY_ZOMBIE_PREFIXES)]:
            settings.pop(key, None)
            changed = True
        return changed

    def default_config(self) -> dict[str, Any]:
        """全部默认值。**唯一真相在 ``settings_schema.SETTINGS``**。

        这里不再逐键手写：默认值、保存白名单、入口 JSON schema、dashboard 快照四层
        都由那张表生成。历史上一个键要在这四处各写一遍，漏一处就是静默失效
        （回复缓冲的两个开关、``retroactive_review_max_reply`` 都踩过）。
        """
        return settings_schema.defaults()

    async def exists(self) -> bool:
        return self._path.is_file()

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_config()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_config()
        # 必须作用在**原始 payload** 上：下面 merged 会先用 default_config()
        # 把新名填满，届时已无法区分「用户存过新名」与「schema 默认值」。
        self.migration_applied = self._migrate_attention_keys(payload)
        merged = self.default_config()
        merged.update(payload)
        merged["trusted_users"] = payload.get("trusted_users") if isinstance(payload.get("trusted_users"), list) else []
        merged["trusted_groups"] = payload.get("trusted_groups") if isinstance(payload.get("trusted_groups"), list) else []
        # 存量 trust 池：**只读透传，永不改名、永不删键、永不归一**。池已上移
        # memory_server，这份磁盘数据是一次性迁移源，且每次启动都会被重推
        # （服务端按 account 哨兵幂等跳过）。归一/截断它等于悄悄改写迁移源，
        # 而池文件一旦丢失就再也恢复不到迁移时刻的状态。
        # `merged.update(payload)` 已经原样带过来了，这里刻意不做任何处理。
        merged["backlog_labels"] = self.normalize_backlog_labels(payload.get("backlog_labels"))
        merged["attention_emotion_multipliers"] = self.normalize_emotion_multipliers(
            payload.get("attention_emotion_multipliers")
        )
        reply_mode = self.normalize_reply_mode(payload.get("reply_mode"))
        if reply_mode != "text" or "reply_mode" in payload:
            merged["reply_mode"] = reply_mode
        elif payload.get("audio_reply_enabled") is True:
            merged["reply_mode"] = "voice"
        else:
            merged["reply_mode"] = "text"
        merged["group_prompts"] = payload.get("group_prompts") if isinstance(payload.get("group_prompts"), dict) else {}
        merged.pop("audio_reply_enabled", None)
        return merged

    async def create_empty(self) -> dict[str, Any]:
        config = self.default_config()
        await self.save(config)
        return config

    async def save(self, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.default_config()
            normalized.update(dict(config or {}))
            # 旧名一律丢弃、僵尸键一律不落盘（新名已在 default_config() 里）。
            self._migrate_attention_keys(normalized)
            normalized["trusted_users"] = list(normalized.get("trusted_users") or [])
            normalized["trusted_groups"] = list(normalized.get("trusted_groups") or [])
            # 见 load()：存量 trust 池只读透传，save 不重建、不归一。
            # `normalized.update(dict(config))` 已原样保留原值。
            normalized["backlog_labels"] = self.normalize_backlog_labels(normalized.get("backlog_labels"))
            normalized["attention_emotion_multipliers"] = self.normalize_emotion_multipliers(
                normalized.get("attention_emotion_multipliers")
            )
            normalized["reply_mode"] = self.normalize_reply_mode(normalized.get("reply_mode"))
            normalized["group_prompts"] = {
                str(k): str(v) for k, v in (normalized.get("group_prompts") or {}).items()
                if str(k).strip() and str(v).strip()
            }
            normalized.pop("audio_reply_enabled", None)
            await atomic_write_json_async(self._path, normalized)
            return normalized
