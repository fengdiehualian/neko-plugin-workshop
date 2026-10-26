"""人类化时序：反应延迟正态分布 + 动作时长变异 + 命令思考间隙。"""

import random


class HumanTiming:
    # ---- 基础反应 ----
    def reaction_delay(self, fast: bool = False) -> float:
        """模拟人类反应时间。fast=True 用于紧急情况。"""
        if fast:
            return random.uniform(0.15, 0.5)
        return max(0.1, random.gauss(0.3, 0.1))

    def action_duration(self, base: float) -> float:
        return base * random.uniform(0.9, 1.15)

    # ---- 命令处理 ----
    def command_delay(self) -> float:
        """收到命令后"理解+决定"的思考延迟，0.6-1.8s。"""

        return random.uniform(0.6, 1.8)

    def step_gap(self) -> float:
        """步骤间"看一眼成果"的呼吸间隙，0.3-1.0s。"""

        return random.uniform(0.3, 1.0)

    def startup_delay(self) -> float:
        """启动动画等待，0.5-2s。"""
        
        return random.uniform(0.5, 2.0)
