"""
群聊权限管理模块

管理允许自动回复的 QQ 群聊
"""

from typing import Any, Dict, List, Optional

#: 群名单「没写级别」时的默认档：名单里的群就是信任群（走注意力门控正常参与）。
#: 想让她只在被 @ 时按低概率转发，必须**显式**写 ``normal``。漏写、写空、写错
#: 都不会把群降级成几乎不说话的普通群 —— 真机踩过这个静默默认（加了群却像没加）。
DEFAULT_GROUP_LEVEL = "trusted"


class GroupPermissionManager:
    """群聊权限管理器"""

    VALID_LEVELS = {"trusted", "normal"}
    #: 历史别名 → 今天的两级。``open`` 级已删除（它原本表示「按概率直接回复」），
    #: 现在的语义并入 ``trusted``（走注意力门控）——所以配置里残留的 ``"open"``
    #: 会被**自动升为 trusted**，不需要人工改配置。
    LEGACY_LEVEL_ALIASES = {"truth": "trusted", "open": "trusted"}

    def __init__(self, trusted_groups: List[Dict[str, Any]] = None):
        """
        初始化群聊权限管理器

        Args:
            trusted_groups: 群聊列表，格式: [{"group_id": "123456", "level": "trusted"}, ...]
                条目可以省略 ``level``（按 :data:`DEFAULT_GROUP_LEVEL` 收录），
                也可以直接写成群号字符串（手写配置常见，同样按默认档收录）。
        """
        self._groups: Dict[str, Dict[str, Any]] = {}

        if trusted_groups:
            for group in trusted_groups:
                # 容错：字符串条目按默认档收录，坏条目跳过（与 permission.py
                # 同一套防御，真机见过手写字符串数组炸 startup）。
                if not isinstance(group, dict):
                    raw = str(group or "").strip()
                    if raw:
                        self._groups[raw] = {
                            "level": DEFAULT_GROUP_LEVEL,
                            "normal_relay_probability": None,
                        }
                    continue
                group_id = str(group.get("group_id", "") or "").strip()
                level = group.get("level", DEFAULT_GROUP_LEVEL)
                if group_id:
                    self._groups[group_id] = {
                        "level": self._normalize_level(level),
                        "normal_relay_probability": self._normalize_probability(group.get("normal_relay_probability")),
                    }

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        # 空值与无法识别的级别都落到默认档：默认是「信任群」，只有显式写 normal
        # 才是仅转发的普通群。写错的级别不该悄悄把群降级成几乎不说话。
        normalized = str(level or DEFAULT_GROUP_LEVEL).strip().lower()
        normalized = cls.LEGACY_LEVEL_ALIASES.get(normalized, normalized)
        return normalized if normalized in cls.VALID_LEVELS else DEFAULT_GROUP_LEVEL

    @staticmethod
    def _normalize_probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            normalized = float(value)
        except Exception:
            return None
        if normalized < 0.0 or normalized > 1.0:
            return None
        return normalized

    def add_group(self, group_id: str, level: str = DEFAULT_GROUP_LEVEL, normal_relay_probability: Any = None):
        """
        添加群聊

        Args:
            group_id: 群号
            level: 权限等级 (trusted, normal)，省略时按 :data:`DEFAULT_GROUP_LEVEL`（trusted）
            normal_relay_probability: normal 群未被 @ 时转发给主人所使用的概率
"""
        normalized_group_id = str(group_id or "").strip()
        if not normalized_group_id:
            return
        self._groups[normalized_group_id] = {
            "level": self._normalize_level(level),
            "normal_relay_probability": self._normalize_probability(normal_relay_probability),
        }

    def remove_group(self, group_id: str):
        """移除群聊"""
        normalized_group_id = str(group_id or "").strip()
        if normalized_group_id in self._groups:
            del self._groups[normalized_group_id]

    def get_group_level(self, group_id: str) -> str:
        """
        获取群聊权限等级

        Args:
            group_id: 群号

        Returns:
            权限等级: trusted, normal, none
        """
        group_str = str(group_id or "").strip()
        group = self._groups.get(group_str) or {}
        return str(group.get("level") or "none")

    def get_normal_relay_probability(self, group_id: str) -> Optional[float]:
        group = self._groups.get(str(group_id or "").strip()) or {}
        return self._normalize_probability(group.get("normal_relay_probability"))

    def is_trusted_group(self, group_id: str) -> bool:
        """检查是否是 @ 后回复的信任群聊"""
        return self.get_group_level(str(group_id)) == "trusted"

    def is_allowed_group(self, group_id: str) -> bool:
        """检查群聊是否被允许（信任或普通）"""
        level = self.get_group_level(str(group_id))
        return level in self.VALID_LEVELS

    def list_groups(self) -> List[Dict[str, Any]]:
        """列出所有群聊"""
        result: List[Dict[str, Any]] = []
        for group_id, group in self._groups.items():
            item: Dict[str, Any] = {"group_id": group_id, "level": group.get("level", DEFAULT_GROUP_LEVEL)}
            normal_relay_probability = self._normalize_probability(group.get("normal_relay_probability"))
            if normal_relay_probability is not None:
                item["normal_relay_probability"] = normal_relay_probability
            result.append(item)
        return result
