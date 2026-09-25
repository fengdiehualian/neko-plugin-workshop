"""一键部署后的「等扫码登录」轮询。

出码之后用户还得拿手机扫，这段时间以前没人盯着 —— 得靠用户自己想起来点
「补写 OneBot 配置」。现在前端定时调 `deploy(action="login_poll")`，后端扫到
登录成功就把收尾做完。

要紧的是**防重入**：收尾会写配置、起运行时，而前端是 5 秒一次 —— 不记状态的话
每轮都重做一遍。

（收尾**不重启** NapCat：它热读 OneBot 配置，而重启会掐断用户刚建立的登录会话。）
"""
from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import napcat_onebot_config as cfg
from plugin.plugins.qq_auto_reply.deploy_service import QQDeployService


def _svc(tmp_path, *, logged_uin: str = "", auto_ok: bool = True,
         runtime_uin: str = "", prewrote: str = ""):
    calls = {"applied": [], "runtime": []}

    async def _apply(*, uin="", restart=False, emit=None):
        calls["applied"].append(uin)
        calls.setdefault("restart", []).append(restart)
        return {"ok": True, "uin": uin, "restarted": restart}

    async def _restart(auto_start):
        calls["runtime"].append(auto_start)
        if not auto_start:
            # 与真实实现一致：auto_start=False 时早退，不动运行时
            return {"ok": False, "status": "", "error": ""}
        return {"ok": auto_ok, "status": "started" if auto_ok else "",
                "error": "" if auto_ok else "启动被拒"}

    async def _login_status():
        if runtime_uin:
            return {"status": "online", "self_id": runtime_uin, "nickname": "bot"}
        return {"status": "offline", "self_id": None, "nickname": None}

    svc = QQDeployService.__new__(QQDeployService)
    svc.plugin = SimpleNamespace(
        _qq_settings={},
        _emit_log=lambda *a, **k: None,
        napcat_service=SimpleNamespace(get_napcat_directory=lambda: tmp_path),
        runtime_service=SimpleNamespace(fetch_login_status_payload=_login_status),
        #: 部署阶段是否已经替这个号写过 onebot11 配置 —— 见 poll_login 的判据说明
        _deploy_prewrote_onebot_uin=prewrote,
        _auto_start_fields=staticmethod(
            lambda auto: {"auto_started": bool(auto.get("ok")),
                          "auto_start_status": str(auto.get("status") or ""),
                          "auto_start_error": str(auto.get("error") or "")}),
    )
    svc.apply_onebot_config = _apply
    svc._restart_auto_reply_runtime = _restart

    d = cfg.config_dir_of(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    if logged_uin:
        (d / f"onebot11_{logged_uin}.json").write_text("{}", encoding="utf-8")
    return svc, calls


# ── 还没登录 ────────────────────────────────────────────────

async def test_pending_when_nobody_has_logged_in(tmp_path):
    svc, calls = _svc(tmp_path)

    assert await svc.poll_login() == {"status": "pending"}
    assert calls["applied"] == [], "没登录就不该动手"


# ── 登录了 ──────────────────────────────────────────────────

async def test_completes_once_the_account_appears(tmp_path):
    """登录成功的信号 = ``onebot11_<uin>.json`` 出现（NapCat 登录后自己建的）。"""
    svc, calls = _svc(tmp_path, logged_uin="3281414178")

    r = await svc.poll_login()

    assert r["status"] == "completed"
    assert r["uin"] == "3281414178"
    assert calls["applied"] == ["3281414178"]


async def test_second_poll_is_a_noop(tmp_path):
    """**防重入**：前端每 5 秒调一次，第二次必须什么都不做 ——
    否则每轮都会 stop/start 一次 NapCat。"""
    svc, calls = _svc(tmp_path, logged_uin="3281414178")

    assert (await svc.poll_login())["status"] == "completed"
    assert (await svc.poll_login())["status"] == "already"
    assert (await svc.poll_login())["status"] == "already"

    assert calls["applied"] == ["3281414178"], "只能收尾一次"


async def test_a_different_account_completes_again(tmp_path):
    """换号登录时要重新收尾 —— 只按 uin 记，不是"做过了就永远不做"。"""
    svc, calls = _svc(tmp_path, logged_uin="111")

    assert (await svc.poll_login())["status"] == "completed"

    d = cfg.config_dir_of(tmp_path)
    (d / "onebot11_222.json").write_text("{}", encoding="utf-8")
    r = await svc.poll_login()

    assert r["status"] == "completed" and r["uin"] == "222"
    assert calls["applied"] == ["111", "222"]


# ── 边界 ────────────────────────────────────────────────────

async def test_pending_when_the_filename_carries_no_uin(tmp_path):
    """目录里只有一个不合规的名字时不能崩，也不能拿它去收尾。"""
    d = cfg.config_dir_of(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / "onebot11_.json").write_text("{}", encoding="utf-8")
    svc, calls = _svc(tmp_path)

    assert (await svc.poll_login())["status"] == "pending"
    assert calls["applied"] == []


async def test_missing_napcat_dir_raises(tmp_path):
    """还没定位到 NapCat 就来轮询 —— 明确报错，让前端把它当失败停下。"""
    import pytest

    svc = QQDeployService.__new__(QQDeployService)
    svc.plugin = SimpleNamespace(
        _qq_settings={},
        _emit_log=lambda *a, **k: None,
        napcat_service=SimpleNamespace(get_napcat_directory=lambda: None),
    )

    with pytest.raises(RuntimeError):
        await svc.poll_login()


# ── 收尾必须**把自动回复拉起来** ────────────────────────────

async def test_completion_also_starts_the_runtime(tmp_path):
    """回归：服务层的 apply_onebot_config 只"写配置 + 重启 NapCat"，
    "启动自动回复"那一步在入口包装层。poll_login 直接调服务，绕过了那层 ——
    不自己补上就是收尾收一半：配置对了、NapCat 重启了，自动回复却没起来，
    用户还得手动点一次「启动」。
    """
    svc, calls = _svc(tmp_path, logged_uin="111")

    r = await svc.poll_login()

    assert calls["runtime"] == [True], "收尾没启动自动回复"
    assert r["auto_started"] is True


async def test_auto_start_false_only_writes_config(tmp_path):
    svc, calls = _svc(tmp_path, logged_uin="111")

    r = await svc.poll_login(auto_start=False)

    assert calls["applied"] == ["111"]        # 配置照写
    assert calls["runtime"] == [False]        # 但不动运行时
    assert r["auto_started"] is False


async def test_runtime_failure_is_reported_not_raised(tmp_path):
    """运行时起不来不能让整个收尾变失败 —— 配置此时已经写好了。"""
    svc, calls = _svc(tmp_path, logged_uin="111", auto_ok=False)

    r = await svc.poll_login()

    assert r["status"] == "completed"
    assert r["auto_started"] is False
    assert len(calls["runtime"]) == 1


async def test_completion_does_not_restart_napcat(tmp_path):
    """**不重启**：NapCat 热读 OneBot 配置，改完直接生效。

    而重启的代价很实在 —— 用户刚扫码登录成功，重启就把会话掐断了，只能靠
    快速登录捞回来。
    """
    svc, calls = _svc(tmp_path, logged_uin="111")

    await svc.poll_login()

    assert calls["restart"] == [False]


# ── 填了 QQ 号那条路：判据②不能用，登录确认后才记自动登录 ──────────
#
# 填了号时插件在**部署阶段**就把 onebot11_<uin>.json 写好了（并指向自己），
# 所以"文件出现"从第一秒起就恒真 —— 拿它当登录信号会在用户还没扫码时就误判成功。
# 那条路只能靠运行时报出的 self_id。

async def test_prewritten_config_is_not_a_login_signal(tmp_path):
    """插件预写过配置时，光有文件不算登录 —— 必须报 pending 等真登录。"""
    svc, calls = _svc(tmp_path, logged_uin="3281414178", prewrote="3281414178")

    assert (await svc.poll_login())["status"] == "pending"
    assert calls["applied"] == [], "还没登录就不该补写配置"


async def test_runtime_self_id_is_the_signal_when_prewritten(tmp_path):
    """运行时报出 self_id 才算登录成功。"""
    svc, calls = _svc(tmp_path, logged_uin="3281414178",
                      prewrote="3281414178", runtime_uin="3281414178")

    r = await svc.poll_login()

    assert r["status"] == "completed" and r["uin"] == "3281414178"


async def test_prewritten_path_never_rewrites_or_restarts(tmp_path):
    """配置部署阶段就写好了，登录后 NapCat 直接拨进来 —— 此刻那条连接是活的。

    补写配置和重启运行时都会把它掐断，所以这条分支只记账号，别的都不碰。
    """
    svc, calls = _svc(tmp_path, logged_uin="3281414178",
                      prewrote="3281414178", runtime_uin="3281414178")

    await svc.poll_login()

    assert calls["applied"] == [], "不该重写配置"
    assert calls["runtime"] == [], "更不该重启运行时 —— 会掐断刚建立的连接"


async def test_runtime_wins_over_a_stale_file(tmp_path):
    """文件是旧号的、运行时报的是新号 —— 以运行时为准。

    预写的那份可能是用户手填错的号；真正登进去的号只有运行时知道。
    """
    svc, calls = _svc(tmp_path, logged_uin="111",
                      prewrote="111", runtime_uin="222")

    r = await svc.poll_login()

    assert r["uin"] == "222"


async def test_auto_login_account_is_recorded_only_after_login(tmp_path):
    """自动登录账号在**登录确认之后**才落盘。

    提前记的是用户手填的号，未必是他扫码登进去的那个；NapCat 的 quickLogin
    只认登录态缓存，第一次无论如何都要扫码。
    """
    d = cfg.config_dir_of(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    svc, _ = _svc(tmp_path, prewrote="3281414178", runtime_uin="3281414178")

    assert cfg.get_auto_login_account(tmp_path) == "", "轮询之前不该已经写上了"
    (d / "onebot11_3281414178.json").write_text("{}", encoding="utf-8")

    await svc.poll_login()

    assert cfg.get_auto_login_account(tmp_path) == "3281414178"


# ── WebUI 的 host/port/token 必须由我们补全 ─────────────────
#
# NapCat 的 ensureConfigFileExists 只在文件**不存在**时才写一份带随机 token 的
# 完整默认配置。插件一旦抢先建了这个文件，NapCat 就只在内存里补默认值、从不落盘 ——
# token 于是只存在于它的控制台，而插件把 NapCat 的 stdout 丢进了 DEVNULL。
# 没有 token 就没有 WebUI；而验证码 / 新设备验证**只经 WebUI 暴露**。

async def test_webui_config_gets_a_persisted_token(tmp_path):
    from plugin.plugins.qq_auto_reply import napcat_onebot_config as cfg2

    cfg2.set_auto_login_account(tmp_path, "3281414178")

    data = cfg2.load(cfg2.webui_config_path(tmp_path))
    assert data["autoLoginAccount"] == "3281414178"
    assert data["token"], "token 必须落盘 —— 否则 WebUI 进不去"
    assert data["port"] == 6099
    assert data["host"] == "::"


async def test_webui_token_is_stable_across_writes(tmp_path):
    """token 一旦写下就不再变 —— 它是进程启动时随机生成的，不落盘等于每次重启都换。"""
    from plugin.plugins.qq_auto_reply import napcat_onebot_config as cfg2
    from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService

    cfg2.set_auto_login_account(tmp_path, "111")
    first = cfg2.load(cfg2.webui_config_path(tmp_path))["token"]

    cfg2.set_auto_login_account(tmp_path, "222")
    second = cfg2.load(cfg2.webui_config_path(tmp_path))["token"]

    assert first == second

    # 而且 get_webui_url 能把它拼进链接 —— 这就是界面那个「打开 NapCat 配置页」
    svc = QQNapcatService.__new__(QQNapcatService)
    svc.get_napcat_directory = lambda: tmp_path
    assert f"token={first}" in svc.get_webui_url()
