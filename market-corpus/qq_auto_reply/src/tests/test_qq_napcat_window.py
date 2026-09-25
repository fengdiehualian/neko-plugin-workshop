"""NapCat 的启动窗口，以及"藏了窗口之后日志去哪"。

默认**后台**启动：自动化（一键部署 / 开机自启）不该弹一个控制台出来打断用户。

但藏窗口有个直接后果 —— NapCat 的 logger 默认 ``fileLogEnabled = false`` /
``consoleLogEnabled = true``（见 ``napcat.mjs``），也就是**只写控制台**；而插件启动
它时把 stdout/stderr 都丢进了 DEVNULL。窗口一藏，它的日志就**哪儿都不会留**，
``NapCat.Shell/logs/`` 会是空的。所以启动隐藏窗口时必须顺手把 ``config/napcat.json``
的 ``fileLog`` 打开。

这个亏已经吃过一次：WebUI 的 token 当初只出现在控制台里，谁都拿不到。
"""
from __future__ import annotations

import json
import subprocess

from plugin.plugins.qq_auto_reply import napcat_onebot_config as cfg
from plugin.plugins.qq_auto_reply import napcat_platform
from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


# ── 默认后台 ────────────────────────────────────────────────

def test_the_default_is_background(tmp_path):
    assert QQAutoReplyConfigStore(tmp_path).default_config()["show_napcat_window"] is False


def test_launch_spec_defaults_to_hidden():
    spec = napcat_platform.launch_spec("launcher.bat", windows=True)

    assert spec.kwargs["creationflags"] == NO_WINDOW


def test_launch_spec_can_still_show_the_window_on_request():
    """用户显式勾了「前台启动」就得真的弹 —— 改默认值不等于把这个能力拿掉了。"""
    spec = napcat_platform.launch_spec("launcher.bat", windows=True, show_window=True)

    assert spec.kwargs["creationflags"] == NEW_CONSOLE


# ── fileLog：藏了窗口之后的唯一去处 ─────────────────────────

def _napcat_json(napcat_dir, data: dict) -> None:
    cfg.config_dir_of(napcat_dir).mkdir(parents=True, exist_ok=True)
    cfg.napcat_config_path(napcat_dir).write_text(json.dumps(data), encoding="utf-8")


def _read(napcat_dir) -> dict:
    return json.loads(cfg.napcat_config_path(napcat_dir).read_text(encoding="utf-8"))


def test_file_log_is_turned_on(tmp_path):
    _napcat_json(tmp_path, {"fileLog": False, "consoleLog": True, "fileLogLevel": "debug"})

    assert cfg.ensure_file_log(tmp_path) is True

    assert _read(tmp_path)["fileLog"] is True


def test_turning_on_file_log_preserves_the_rest(tmp_path):
    """只动 fileLog 一个键 —— NapCat 自己的设置原样保留。"""
    _napcat_json(tmp_path, {"fileLog": False, "consoleLog": True, "packetBackend": "auto",
                            "o3HookMode": 1, "bypass": {"hook": False, "window": False}})

    cfg.ensure_file_log(tmp_path)

    data = _read(tmp_path)
    assert data["consoleLog"] is True and data["packetBackend"] == "auto"
    assert data["o3HookMode"] == 1 and data["bypass"] == {"hook": False, "window": False}


def test_file_log_is_idempotent(tmp_path):
    """每次启动都会跑一遍，已经是开的不该白写一次。"""
    _napcat_json(tmp_path, {"fileLog": False})

    assert cfg.ensure_file_log(tmp_path) is True
    assert cfg.ensure_file_log(tmp_path) is False


def test_a_missing_config_does_not_raise(tmp_path):
    """config/napcat.json 还没生成过时不能炸。"""
    assert cfg.ensure_file_log(tmp_path) is True

    assert _read(tmp_path)["fileLog"] is True
