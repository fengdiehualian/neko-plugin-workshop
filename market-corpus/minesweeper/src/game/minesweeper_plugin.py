"""把扫雷的关键事件发送给 N.E.K.O. 插件（minesweeper_bridge）。

用法（在 minesweeper.py 里）：

    import minesweeper_plugin
    minesweeper_plugin.notify({"type": "win", "seconds": 42})

发送在后台守护线程里完成，不会阻塞 tkinter 主循环。
N.E.K.O. 未启动 / 插件未加载时，事件被静默丢弃，不影响游戏。
数据只走本机回环地址 127.0.0.1，不落盘、不联网。
"""

import json
import queue
import socket
import threading

HOST = "127.0.0.1"
PORT = 39001
CONNECT_TIMEOUT = 0.05

_queue = queue.Queue()
_worker = None
_lock = threading.Lock()
_enabled = True


def configure(host=HOST, port=PORT, enabled=True):
    """可选：修改目标地址或临时关闭发送。"""
    global HOST, PORT, _enabled
    HOST = host
    PORT = port
    _enabled = enabled


def _run():
    while True:
        item = _queue.get()
        if item is None:
            continue
        try:
            payload = (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
            with socket.create_connection((HOST, PORT), timeout=CONNECT_TIMEOUT) as s:
                s.sendall(payload)
        except OSError:
            pass
        except Exception:
            pass


def _ensure_worker():
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="neko-bridge", daemon=True)
            _worker.start()


def notify(event):
    """把一个事件排入队列。永不抛异常、永不阻塞。"""
    if not _enabled or not isinstance(event, dict):
        return
    _ensure_worker()
    _queue.put(event)
