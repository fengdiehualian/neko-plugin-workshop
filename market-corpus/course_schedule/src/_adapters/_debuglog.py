"""插件级调试日志。

N.E.K.O 只把「启动阶段」的 stdout 写入插件日志面板；entry 执行期间
（尤其 run_in_executor 工作线程里）的 print 不会进入面板。
因此适配器的诊断信息需要三路输出：
1. 宿主 logger（N.E.K.O.Plugin_<id>.plugin.<id>，与错误日志同名空间）
2. 插件目录下的 academic_debug.log 文件（开发者可直接读取）
3. stdout 兜底
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback

_LOCK = threading.Lock()
_LOG_FILENAME = "academic_debug.log"
_MAX_BYTES = 512 * 1024

# 与宿主错误日志中的 logger 名称空间保持一致，
# 复用宿主的 handler 让诊断信息出现在 N.E.K.O 日志面板
_HOST_LOGGER_NAME = "N.E.K.O.Plugin_course_schedule.plugin.course_schedule"

_logger: logging.Logger | None = None


def _host_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger(_HOST_LOGGER_NAME)
        # 不额外挂 handler/level，完全复用宿主配置
        _logger.propagate = True
    return _logger


def _log_file_path() -> str:
    # _debuglog.py 位于 <插件根>/_adapters/ 下
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, _LOG_FILENAME)


def dlog(msg: str) -> None:
    """写一行调试日志（线程安全，三路输出，任何一路失败静默跳过）。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        _host_logger().info(msg)
    except Exception:
        pass
    try:
        path = _log_file_path()
        with _LOCK:
            try:
                if os.path.exists(path) and os.path.getsize(path) > _MAX_BYTES:
                    os.remove(path)
            except Exception:
                pass
            with open(path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def dexc(prefix: str, exc: BaseException) -> None:
    """记录异常 + 完整 traceback。"""
    dlog(f"{prefix} EXC: {type(exc).__name__}: {exc}")
    try:
        tb = traceback.format_exc()
        dlog(f"{prefix} traceback:\n{tb}".replace("\n", "\n    "))
    except Exception:
        pass
