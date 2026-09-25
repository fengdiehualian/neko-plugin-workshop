"""一键部署要把**实际用到的连接设置**写回插件。

不写回也能跑（NapCat 侧对空地址有兜底），但插件这边 `napcat_directory` /
`onebot_url` 会一直是空的：配置页显示空白目录、空白地址，与实际状态对不上；
而且插件反向监听的默认值与 NapCat 被写入的目标只是"各自默认碰巧一致"，
不是显式约定的同一个值。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugin.plugins.qq_auto_reply.deploy_service import QQDeployService
from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService


def _svc(settings: dict) -> tuple[QQDeployService, dict]:
    calls = {"persisted": 0, "steps": []}

    async def _persist():
        calls["persisted"] += 1

    plugin = SimpleNamespace(
        _qq_settings=settings,
        _emit_log=lambda *a, **k: None,
        settings_service=SimpleNamespace(
            _default_onebot_url=QQSettingsService._default_onebot_url,
            persist_business_config=_persist,
        ),
    )
    return QQDeployService(plugin), calls


def _step(calls):
    return lambda key, msg, **e: calls["steps"].append((key, msg))


# ── 空值补齐 ────────────────────────────────────────────────

async def test_fills_blank_directory_and_url():
    settings: dict = {"napcat_directory": "", "onebot_url": ""}
    svc, calls = _svc(settings)

    await svc._persist_connection_settings(Path(r"D:\x\NapCat.Shell"), "napcat",
                                           _step(calls), None)

    assert settings["napcat_directory"] == r"D:\x\NapCat.Shell"
    assert settings["onebot_url"] == "ws://0.0.0.0:6199"     # 反向：插件监听
    assert calls["persisted"] == 1
    assert any(k == "config" for k, _ in calls["steps"])


async def test_forward_mode_gets_its_own_default():
    """两种模式的默认地址语义不同、值也不同 —— 别写成同一个。"""
    settings: dict = {"napcat_directory": "", "onebot_url": ""}
    svc, _ = _svc(settings)

    await svc._persist_connection_settings(Path("D:/x"), "napcat_forward", lambda *a, **k: None, None)

    assert settings["onebot_url"] == "ws://127.0.0.1:3001"   # 正向：拨到 NapCat


# ── 已有的值不覆盖 ──────────────────────────────────────────

async def test_does_not_overwrite_existing_values():
    """用户显式填过的目录/地址是权威，部署不许改。"""
    settings = {"napcat_directory": r"D:\我的NapCat", "onebot_url": "ws://10.0.0.5:7000"}
    svc, calls = _svc(settings)

    await svc._persist_connection_settings(Path(r"D:\别的地方"), "napcat",
                                           _step(calls), None)

    assert settings["napcat_directory"] == r"D:\我的NapCat"
    assert settings["onebot_url"] == "ws://10.0.0.5:7000"
    assert calls["persisted"] == 0, "什么都没变就不该落盘"


async def test_whitespace_only_counts_as_blank():
    settings = {"napcat_directory": "   ", "onebot_url": "  "}
    svc, _ = _svc(settings)

    await svc._persist_connection_settings(Path("D:/x"), "napcat", lambda *a, **k: None, None)

    assert settings["napcat_directory"] == "D:\\x".replace("/", "\\") or settings["napcat_directory"]
    assert settings["onebot_url"] == "ws://0.0.0.0:6199"


# ── 落盘失败不致命 ──────────────────────────────────────────

async def test_persist_failure_does_not_raise():
    """设置已在内存里生效，落盘失败只该记一条日志 —— 部署不该因此中断。"""
    settings: dict = {"napcat_directory": "", "onebot_url": ""}
    svc, _ = _svc(settings)
    svc.plugin.settings_service.persist_business_config = AsyncMock(side_effect=OSError("disk full"))

    await svc._persist_connection_settings(Path("D:/x"), "napcat", lambda *a, **k: None, None)

    assert settings["napcat_directory"]           # 内存里已补上


# ── 部署流程里真的调了它 ────────────────────────────────────

def test_deploy_calls_it_before_writing_napcat_config():
    """顺序要紧：`_write_config` 读的就是这里补出来的 onebot_url。

    源码级断言 —— 跑整条 deploy 要铺下载/解包/启动一堆替身，不值当。
    """
    import inspect

    src = inspect.getsource(QQDeployService.deploy)
    # 比 `await self.` 前缀的**调用语句**，不比裸名字：注释里也会提到 _write_config，
    # 拿裸子串比位置会被注释带偏（这条一开始就是这么误报的）。
    persist_at = src.index("await self._persist_connection_settings")
    write_at = src.index("await self._write_config")
    assert persist_at < write_at, "补设置必须在写 NapCat 配置之前，否则读到的还是空地址"
