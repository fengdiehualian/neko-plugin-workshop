"""NapCat 自动登录（``config/webui.json`` 的 ``autoLoginAccount``）。

扫码登录一次之后，把登的账号记下来，下次启动 NapCat 就不必再扫。

依据在 NapCat 本体（``NapCat.Shell/napcat.mjs``）：启动时取
``process.env.NAPCAT_QUICK_ACCOUNT || WebUIConfig.autoLoginAccount``，
有值就 ``quickLoginWithUin()``；登录态没缓存住时它会自己回落二维码。

注意**不是**写启动器参数：``launcher-user.bat`` 末尾虽然把 ``%*`` 透传给
``NapCatWinBootMain.exe``（``quickLoginExample.bat`` 演示的也是这条路），
但那要每次启动都带上参数，而 WebUI 配置是持久的 —— 后者更稳，且用户手动
双击 launcher 启动时同样生效。
"""
from __future__ import annotations

import json

from plugin.plugins.qq_auto_reply import napcat_onebot_config as cfg


def _webui(napcat_dir, data: dict) -> None:
    d = cfg.config_dir_of(napcat_dir)
    d.mkdir(parents=True, exist_ok=True)
    cls = cfg.webui_config_path(napcat_dir)
    cls.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── 写入 ────────────────────────────────────────────────────

def test_sets_the_account(tmp_path):
    _webui(tmp_path, {"host": "::", "port": 6099, "autoLoginAccount": ""})

    assert cfg.set_auto_login_account(tmp_path, "3281414178") is True
    assert cfg.get_auto_login_account(tmp_path) == "3281414178"


def test_preserves_the_rest_of_the_file(tmp_path):
    """webui.json 里还有 host/port/token/主题一大堆，绝不能整份覆盖掉。"""
    _webui(tmp_path, {"host": "::", "port": 6099, "token": "abc123",
                      "autoLoginAccount": "", "disableWebUI": False})

    cfg.set_auto_login_account(tmp_path, "12345")

    data = json.loads(cfg.webui_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["host"] == "::" and data["port"] == 6099
    assert data["token"] == "abc123" and data["disableWebUI"] is False
    assert data["autoLoginAccount"] == "12345"


def test_creates_the_file_when_absent(tmp_path):
    """首次部署时 webui.json 还没被 NapCat 生成过 —— 缺失不该报错。

    **必须连 host/port/token 一起写**，不能只写 autoLoginAccount 就指望 NapCat
    用它自己的 schema 默认值补。它的 ``ensureConfigFileExists`` 只在文件**不存在**
    时才落盘一份完整默认配置；文件一旦被我们抢先建出来，它只在内存里补默认值 ——
    token 于是只活在它的控制台输出里，而插件启动 NapCat 时把 stdout 丢进了
    DEVNULL、``napcat.json`` 又默认 ``fileLog: false``，**这个 token 谁也拿不到**。

    代价不只是进不去 WebUI：需要验证码 / 新设备验证时，NapCat 只通过 WebUI 回调
    暴露它们（``needCaptcha``+``proofWaterUrl``、``needNewDevice``+``jumpUrl``），
    没有 token 就等于把用户唯一的出路堵死。
    """
    assert not cfg.webui_config_path(tmp_path).exists()

    assert cfg.set_auto_login_account(tmp_path, "999") is True

    data = json.loads(cfg.webui_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["autoLoginAccount"] == "999"
    assert data["host"] == "::" and data["port"] == 6099
    assert data["token"], "token 不落盘 = WebUI 进不去"


# ── 幂等与空值 ──────────────────────────────────────────────

def test_same_account_is_a_noop(tmp_path):
    """账号没变、字段也齐 —— 不该白写一次。"""
    _webui(tmp_path, {"host": "::", "port": 6099, "token": "abc123",
                      "autoLoginAccount": "12345"})

    assert cfg.set_auto_login_account(tmp_path, "12345") is False, "不该白写一次"


def test_a_field_incomplete_file_still_gets_completed(tmp_path):
    """账号没变但文件缺 token/host/port —— 必须补上。

    早先只看"账号一不一样"就早退，于是"只有 autoLoginAccount 的 webui.json"
    会被永远当成完好的，token 再也补不进来（真实部署踩到过）。
    """
    _webui(tmp_path, {"autoLoginAccount": "12345"})

    assert cfg.set_auto_login_account(tmp_path, "12345") is True

    data = json.loads(cfg.webui_config_path(tmp_path).read_text(encoding="utf-8"))
    assert data["token"], "缺 token 就得补"
    assert data["port"] == 6099


def test_switching_accounts_rewrites(tmp_path):
    _webui(tmp_path, {"autoLoginAccount": "111"})

    assert cfg.set_auto_login_account(tmp_path, "222") is True
    assert cfg.get_auto_login_account(tmp_path) == "222"


def test_empty_uin_is_refused(tmp_path):
    """空号会把 autoLoginAccount 清成空串 —— 那是"关掉自动登录"，不该由这里发生。"""
    _webui(tmp_path, {"autoLoginAccount": "111"})

    assert cfg.set_auto_login_account(tmp_path, "") is False
    assert cfg.set_auto_login_account(tmp_path, "   ") is False
    assert cfg.get_auto_login_account(tmp_path) == "111"


def test_corrupt_file_does_not_raise(tmp_path):
    """文件被写坏时 load() 返回 {}，我们只补自己那一个键，不抛。"""
    d = cfg.config_dir_of(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    cfg.webui_config_path(tmp_path).write_text("{ 不是 json", encoding="utf-8")

    assert cfg.set_auto_login_account(tmp_path, "777") is True
    assert cfg.get_auto_login_account(tmp_path) == "777"


# ── 启动前的兜底同步 ────────────────────────────────────────

def _svc(napcat_dir, *, setting: str = "") -> tuple[object, list]:
    """只装 _sync_auto_login_account 用到的那几样。"""

    from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService

    logs: list[str] = []
    svc = QQNapcatService(
        get_settings=lambda: {"napcat_directory": setting},
        config_dir=napcat_dir,
        emit_log=lambda level, msg: logs.append(msg),
    )
    svc._emit_log = lambda level, msg: logs.append(msg)
    return svc, logs


def test_sync_picks_up_an_account_that_logged_in_without_being_asked(tmp_path):
    """扫过码、却从没点过「补写 OneBot 配置」的用户 —— 启动时按目录扫出来记上。"""
    cfg.config_dir_of(tmp_path).mkdir(parents=True, exist_ok=True)
    (cfg.config_dir_of(tmp_path) / "onebot11_3281414178.json").write_text("{}", encoding="utf-8")
    svc, logs = _svc(tmp_path, setting=str(tmp_path))

    assert svc._sync_auto_login_account() is True
    assert cfg.get_auto_login_account(tmp_path) == "3281414178"
    assert any("3281414178" in m for m in logs)


def test_sync_is_a_noop_without_any_logged_account(tmp_path):
    cfg.config_dir_of(tmp_path).mkdir(parents=True, exist_ok=True)
    svc, logs = _svc(tmp_path, setting=str(tmp_path))

    assert svc._sync_auto_login_account() is False
    assert not cfg.webui_config_path(tmp_path).exists(), "没账号就别建 webui.json"


def test_sync_is_idempotent(tmp_path):
    """每次启动都跑一遍，第二次必须什么都不做。"""
    cfg.config_dir_of(tmp_path).mkdir(parents=True, exist_ok=True)
    (cfg.config_dir_of(tmp_path) / "onebot11_999.json").write_text("{}", encoding="utf-8")
    svc, _ = _svc(tmp_path, setting=str(tmp_path))

    assert svc._sync_auto_login_account() is True
    assert svc._sync_auto_login_account() is False
