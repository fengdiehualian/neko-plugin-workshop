"""``qq_official_bind_start`` —— 出码入口，**不做任何拦截**。

新建机器人还是复用已有的，是用户在手机连接页上选的（实测：选「已有的机器人」绑的就是那台，
不会新建）。所以本地按账本拦截没有依据 —— 那个 ``force`` 参数本来就建立在"服务端能强制新建"
这个错误前提上。账本只作提示（``reusable_appid``），供界面提醒用户扫码时选「已有的」。
"""
from __future__ import annotations

import inspect
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.plugins.qq_auto_reply import qq_official_bind as bind


def _plugin(settings: dict, config_dir):
    p = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    p._qq_settings = settings
    # plugin_id / config_dir 都是只读 property：前者读 ctx.plugin_id，
    # 后者走 resolve_plugin_dir(ctx) → Path(ctx.config_path).parent
    p.ctx = SimpleNamespace(plugin_id="qq_auto_reply",
                            config_path=str(config_dir / "plugin.toml"))
    p.logger = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                               error=lambda *a: None)
    return p


def _stub_task(monkeypatch):
    async def _create():
        return bind.BindSession(task_id="task-1", bind_key="k",
                                qrcode="https://q.qq.com/qqbot/openclaw/connect.html?task_id=task-1")
    monkeypatch.setattr(bind, "create_bind_task", _create)
    monkeypatch.setattr(bind, "render_qr_png", lambda data, dest: True)


# ── 不再有 force 参数 ───────────────────────────────────────

def test_bind_start_takes_no_force_parameter():
    """别再把它加回来 —— 服务端管不了"新建还是复用"。

    合并进 `deploy` 之后，绑定这条路只剩 `deploy(action="bind_start")` 一个入口，
    它的私有方法只该收 (self, kw)。
    """
    params = inspect.signature(QQAutoReplyPlugin._deploy_bind_start).parameters
    assert list(params) == ["self", "kw"]


# ── 账本非空时照常出码 ──────────────────────────────────────

async def test_ledger_with_a_bot_still_starts_a_task(monkeypatch, tmp_path):
    """账本里已有机器人也照常出码，并把 appid 作为**提示**带回。"""
    settings: dict = {}
    bind.remember_bot(settings, appid="1903565393", secret="s")
    _stub_task(monkeypatch)
    p = _plugin(settings, tmp_path)

    r = await p.deploy(action="bind_start")

    assert r.is_ok()
    assert r.value["existing_bots"] == 1
    assert r.value["reusable_appid"] == "1903565393"
    assert r.value["task_id"] == "task-1"
    assert r.value["qrcode_ready"] is True
    # 会话存下来了，后续 poll 才能解密
    assert p._qq_bind_session.task_id == "task-1"


async def test_empty_ledger_reports_no_reusable_bot(monkeypatch, tmp_path):
    """账本为空时提示字段是空串（不是 None）—— 前端直接拿去拼字符串。"""
    _stub_task(monkeypatch)
    p = _plugin({}, tmp_path)

    r = await p.deploy(action="bind_start")

    assert r.value["existing_bots"] == 0
    assert r.value["reusable_appid"] == ""


async def test_create_failure_is_reported(monkeypatch, tmp_path):
    async def _boom():
        raise RuntimeError("网络不通")

    monkeypatch.setattr(bind, "create_bind_task", _boom)
    p = _plugin({}, tmp_path)

    r = await p.deploy(action="bind_start")

    assert r.is_err()
    assert "BIND_START_FAILED" in str(r.error)


def test_bot_exists_gate_is_gone_from_the_ui_too():
    """闸门拆干净了 —— 界面上再冒出 BOT_EXISTS 说明有地方没跟上服务端。"""
    status_html = pathlib.Path(__file__).resolve().parents[1] / "static" / "status.html"
    assert "BOT_EXISTS" not in status_html.read_text(encoding="utf-8")
