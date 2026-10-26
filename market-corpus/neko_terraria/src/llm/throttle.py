"""全局 LLM 调用限流器：防止 API 过载。

双 LLM 架构：
- 主 LLM (priority=normal/low): 对话交互、情感表达
- 意图 LLM (priority=high): 结构化推理、任务规划
- 紧急事件 (priority=emergency): 危险警报（专用配额）
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class GlobalLLMThrottle:
    """全局 LLM 调用频率限制器。

    跨模块追踪所有 LLM 调用，防止短时间内过多请求导致 API 配额耗尽。
    """

    def __init__(self, max_calls_per_minute: int = 15,
                 emergency_reserve: int = 3) -> None:
        """
        Args:
            max_calls_per_minute: 每分钟最大调用次数
            emergency_reserve: 紧急情况保留配额（如危险警报）
        """
        self.max_calls = max_calls_per_minute
        self.emergency_reserve = emergency_reserve
        # 调用记录: [(timestamp, source, priority), ...]
        self._calls: List[Tuple[float, str, str]] = []
        self._total_calls = 0
        self._rejected_calls = 0
        self._last_log = 0

    def acquire(self, source: str, priority: str = "normal") -> bool:
        """尝试获取调用权限。

        Args:
            source: 调用来源
            priority: "high"(意图解析) / "emergency"(危险) / "normal"(交互) / "low"(思考)

        Returns:
            True: 允许; False: 拒绝（已达限流）
        """
        now = time.time()

        # 清理 60 秒前的记录
        self._calls = [(t, s, p) for t, s, p in self._calls if now - t < 60]

        current_count = len(self._calls)

        # 紧急调用：保留专用配额
        if priority == "emergency":
            if current_count >= self.max_calls + self.emergency_reserve:
                self._rejected_calls += 1
                log.warning(f"[限流] 紧急调用被拒绝 {source}, 当前 {current_count}/{self.max_calls}")
                return False
        else:
            # 普通调用：检查是否达到限制
            if current_count >= self.max_calls:
                self._rejected_calls += 1
                log.warning(f"[限流] 调用被拒绝 {source}, 当前 {current_count}/{self.max_calls}")
                self._log_stats()
                return False

        # 允许调用，记录
        self._calls.append((now, source, priority))
        self._total_calls += 1

        # 定期打印统计（每分钟一次）
        if now - self._last_log > 60:
            self._log_stats()

        return True

    def _log_stats(self) -> None:
        """打印调用统计。"""
        now = time.time()
        recent = [s for t, s, p in self._calls if now - t < 60]
        stats: Dict[str, int] = {}
        for source in recent:
            stats[source] = stats.get(source, 0) + 1

        log.info(f"[限流] 当前 {len(recent)}/{self.max_calls} 调用/分钟, "
                 f"总计 {self._total_calls} 次, 拒绝 {self._rejected_calls} 次")
        log.info(f"[限流] 来源分布: {stats}")
        self._last_log = now

    def get_stats(self) -> Dict[str, int]:
        """获取统计数据。"""
        now = time.time()
        recent = [(t, s, p) for t, s, p in self._calls if now - t < 60]
        stats = {
            "current_count": len(recent),
            "max_calls": self.max_calls,
            "total_calls": self._total_calls,
            "rejected_calls": self._rejected_calls,
        }
        # 按来源分组
        for _, source, _ in recent:
            key = f"source_{source}"
            stats[key] = stats.get(key, 0) + 1
        return stats

    def reset_stats(self) -> None:
        """重置统计数据（保留调用记录）。"""
        self._total_calls = 0
        self._rejected_calls = 0
        log.info("[限流] 统计已重置")


# 全局单例
_global_throttle: Optional[GlobalLLMThrottle] = None


def get_throttle(config: Optional[dict] = None) -> GlobalLLMThrottle:
    """获取全局限流器单例（可动态更新配置）。"""
    global _global_throttle
    if _global_throttle is None:
        max_calls = 15
        reserve = 3
        if config:
            max_calls = config.get("llm_max_calls_per_minute", 15)
            reserve = config.get("llm_emergency_reserve", 3)
        _global_throttle = GlobalLLMThrottle(max_calls, reserve)
    elif config:
        _global_throttle.max_calls = config.get("llm_max_calls_per_minute", 15)
        _global_throttle.emergency_reserve = config.get("llm_emergency_reserve", 3)
    return _global_throttle
