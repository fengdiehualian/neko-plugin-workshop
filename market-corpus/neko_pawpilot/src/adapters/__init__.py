"""适配层：外部接口。"""

from .hud_ocr import HudOcr
from .push_sender import PushSender
from .telemetry_client import TelemetryReader, TruckSnapshot

__all__ = ["HudOcr", "PushSender", "TelemetryReader", "TruckSnapshot"]
