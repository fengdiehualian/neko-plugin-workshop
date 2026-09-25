"""教务系统适配器注册表。

使用：
    from ._adapters import get_adapter, list_adapters
    adapter = get_adapter("xiqueer", base_url=..., school_code=...)
    await adapter.authenticate(creds={"username": "...", "password": "..."})
    data = await adapter.pull(creds)

支持的适配器：
    - xiqueer   喜鹊儿（青果 KingoSOFT 教务）
    - zhengfang 正方教务管理系统
"""

from __future__ import annotations

from .._academic_adapter import AcademicAdapter, AcademicAdapterError
from .xiqueer import XiQueErAdapter
from .zhengfang import ZhengFangAdapter

_REGISTRY: dict[str, type[AcademicAdapter]] = {}


def _register(adapter_id: str):
    def _wrap(cls):
        cls.adapter_id = adapter_id
        _REGISTRY[adapter_id] = cls
        return cls

    return _wrap


def list_adapters() -> list[dict[str, str]]:
    return [{"id": a.adapter_id, "name": a.adapter_name} for a in _REGISTRY.values()]


def get_adapter(adapter_id: str, **kwargs) -> AcademicAdapter:
    if adapter_id not in _REGISTRY:
        raise AcademicAdapterError(f"未知的教务适配器：{adapter_id}。可用：{list(_REGISTRY.keys())}")
    return _REGISTRY[adapter_id](**kwargs)


# ── 注册内置适配器 ──

_REGISTRY["xiqueer"] = XiQueErAdapter
_REGISTRY["zhengfang"] = ZhengFangAdapter
