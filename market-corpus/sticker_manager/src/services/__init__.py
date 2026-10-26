"""有状态层：表情库文件存储（library）、发送链路（sender）、工具注册心跳（tool_watch）、
存在感注入（awareness）与本次运行读数（runstats）。"""

from .awareness import Awareness
from .lanlan import LanlanResolver, latest_lanlan
from .library import Library, LibraryResult
from .runstats import RunStats, gave_shape
from .sender import Sender, SendResult
from .tool_watch import TOOL_WATCH_INTERVAL_SEC, ToolWatch, missing_tool_names

__all__ = [
    "Awareness",
    "LanlanResolver",
    "Library",
    "LibraryResult",
    "RunStats",
    "Sender",
    "SendResult",
    "TOOL_WATCH_INTERVAL_SEC",
    "ToolWatch",
    "gave_shape",
    "latest_lanlan",
    "missing_tool_names",
]
