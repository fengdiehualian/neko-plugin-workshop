"""
安全限制器 - 多层安全保护机制
"""
import time
from typing import Any, Dict


class SafetyError(Exception):
    """安全检查失败异常"""
    pass


class SafetyLimiter:
    """安全限制器"""
    
    def __init__(self, config: Dict[str, Any], logger):
        self.logger = logger
        coyote_config = config.get("coyote", {})
        
        self.max_intensity_a = coyote_config.get("safety_max_intensity_a", 50)
        self.max_intensity_b = coyote_config.get("safety_max_intensity_b", 50)
        self.min_interval_seconds = 5
        
        self.last_action_time = 0
        self.action_count = 0
        
        self.logger.info(
            f"SafetyLimiter initialized: max_a={self.max_intensity_a}, "
            f"max_b={self.max_intensity_b}"
        )
    
    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证并调整参数，确保安全
        
        Args:
            params: 包含 intensity, channel, duration_ms, waveform 的参数字典
            
        Returns:
            调整后的安全参数
            
        Raises:
            SafetyError: 违反安全规则
        """
        now = time.time()
        
        # 检查时间间隔
        if now - self.last_action_time < self.min_interval_seconds:
            raise SafetyError(
                f"操作过于频繁，最小间隔 {self.min_interval_seconds} 秒"
            )
        
        # 强度限制
        intensity = params.get("intensity", 0)
        channel = params.get("channel", "A")
        
        if channel == "A":
            max_allowed = self.max_intensity_a
        elif channel == "B":
            max_allowed = self.max_intensity_b
        else:  # both
            max_allowed = min(self.max_intensity_a, self.max_intensity_b)
        
        if intensity > max_allowed:
            self.logger.warning(
                f"强度 {intensity} 超过安全上限 {max_allowed}，已调整"
            )
            intensity = max_allowed
        
        # 持续时间限制（最长10秒）
        duration_ms = min(params.get("duration_ms", 2000), 10000)
        
        # 更新状态
        self.last_action_time = now
        self.action_count += 1
        
        safe_params = {
            "mood": params.get("mood"),
            "intensity": intensity,
            "channel": channel,
            "duration_ms": duration_ms,
            "waveform": params.get("waveform", "BREATH"),
        }
        
        self.logger.debug(f"Validated params: {safe_params}")
        return safe_params
    
    def can_execute_punishment(self) -> bool:
        """
        检查是否允许执行惩罚模式（高强度）
        
        Returns:
            True 如果允许，False 否则
        """
        # 惩罚模式需要更长的冷却时间
        now = time.time()
        punishment_cooldown = 60  # 60秒冷却
        
        return (now - self.last_action_time) >= punishment_cooldown
    
    def reset(self):
        """重置状态"""
        self.last_action_time = 0
        self.action_count = 0
        self.logger.info("SafetyLimiter reset")
    
    def validate_pulse(self, intensity: int, duration_ms: int):
        """
        简化的脉冲验证（兼容新 API 调用）
        
        Args:
            intensity: 强度
            duration_ms: 持续时间（毫秒）
            
        Raises:
            SafetyError: 违反安全规则
        """
        now = time.time()
        
        # 检查时间间隔
        if now - self.last_action_time < self.min_interval_seconds:
            raise SafetyError(
                f"操作过于频繁，最小间隔 {self.min_interval_seconds} 秒"
            )
        
        # 强度限制（使用两个通道中较小的上限）；超限钳制而不是直接失败，
        # 否则 LLM 写强度 200 / 文本标记兜底会整次动作被丢掉
        max_allowed = min(self.max_intensity_a, self.max_intensity_b)
        if intensity > max_allowed:
            self.logger.warning(
                "强度 %d 超过安全上限 %d，已钳制", intensity, max_allowed
            )
        
        # 持续时间限制（最长10秒）
        if duration_ms > 10000:
            raise SafetyError(f"持续时间 {duration_ms}ms 超过上限 10000ms")
        
        # 更新状态
        self.last_action_time = now
        self.action_count += 1
        return max(0, min(int(intensity), max_allowed))
