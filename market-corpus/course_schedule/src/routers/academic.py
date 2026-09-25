"""教务系统适配器入口：列出可用适配器、从教务系统拉取并直接入库。"""

from __future__ import annotations

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry
from plugin.sdk.shared.core.router import PluginRouter

from .._academic_adapter import AcademicAdapterError
from .._adapters import get_adapter, list_adapters
from .._adapters._debuglog import dexc, dlog
from .import_export import _apply_normalized


class AcademicRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="academic")

    @property
    def repo(self):
        return self.main_plugin.repo

    @plugin_entry(id="list_academic_adapters", name="教务适配器列表", description="列出所有可用的教务系统适配器")
    async def list_academic_adapters(self, **_):
        return Ok({"adapters": list_adapters()})

    @plugin_entry(
        id="import_from_academic",
        name="从教务系统导入",
        description=(
            "使用指定教务适配器登录并拉取课表，直接入库。"
            "adapter=xiqueer 需要 base_url（或 school_code=12623 等预设）、username、password。"
            "选填 semester_selector 用于指定学期，semester_id 用于指定入库目标学期。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "adapter": {"type": "string", "description": "适配器 ID，如 xiqueer"},
                "base_url": {"type": "string", "description": "教务系统根地址（喜鹊儿/青果）"},
                "school_code": {"type": "string", "description": "预设学校代码，如 12623"},
                "username": {"type": "string", "description": "学号/工号"},
                "password": {"type": "string", "description": "密码（插件端仅用于本次请求，不持久化）"},
                "md5_password": {"type": "string", "description": "可选：直接传密码的 MD5 哈希"},
                "semester_selector": {"type": "object", "description": "选学期：{id|name|keyword}"},
                "semester_id": {"type": "integer", "description": "入库目标学期 ID，可空"},
            },
            "required": ["adapter", "username", "password"],
        },
    )
    async def import_from_academic(
        self,
        adapter: str,
        username: str,
        password: str,
        base_url: str = "",
        school_code: str = "",
        md5_password: str = "",
        semester_selector: dict | None = None,
        semester_id: int = 0,
        **_,
    ):
        try:
            adp = get_adapter(adapter, base_url=base_url, school_code=school_code)
        except AcademicAdapterError as exc:
            dlog(f"[academic] get_adapter({adapter!r}) failed: {exc}")
            return Err(SdkError(str(exc)))

        dlog(
            f"[academic] import_from_academic adapter={adapter!r} "
            f"base_url={base_url!r} school_code={school_code!r} "
            f"username={username!r} pwd_len={len(password)} "
            f"semester_id={semester_id} selector={semester_selector!r}"
        )
        try:
            data = await adp.pull(
                creds={"username": username, "password": password, "md5_password": md5_password},
                semester_selector=semester_selector,
            )
        except AcademicAdapterError as exc:
            dlog(f"[academic] pull failed (adapter error): {exc}")
            return Err(SdkError(f"教务适配器错误：{exc}"))
        except Exception as exc:
            dexc("[academic] pull failed (unexpected)", exc)
            return Err(SdkError(f"教务适配器异常：{exc}"))

        dlog(
            f"[academic] pull ok: semester={data.get('semester')!r} "
            f"courses={len(data.get('courses') or [])} keys={sorted(data.keys())}"
        )
        try:
            stats = await _apply_normalized(self, data, int(semester_id or 0))
        except Exception as exc:
            dexc("[academic] apply_normalized failed", exc)
            return Err(SdkError(f"入库失败：{exc}"))
        dlog(f"[academic] import done: stats={stats}")
        return Ok(
            {
                "adapter": adapter,
                "semester_fetched": data.get("semester"),
                "stats": stats,
            }
        )
