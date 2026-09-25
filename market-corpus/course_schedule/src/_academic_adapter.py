"""教务系统适配器基类。

每个 adapter 负责把某个教务平台的课表数据转成 ImportExportRouter 能吃的
normalized dict（见 routers/import_export.py 顶部注释），然后复用同一条入库路径。

子类只需实现三个方法：
    async authenticate(creds) -> None
    async fetch_semesters() -> list[dict]           # [{name, start_date, end_date, id?}]
    async fetch_courses(semester) -> list[dict]     # [{name, code, teacher, location, sessions:[...]}]
    async fetch_exceptions(semester) -> list[dict]   # 可选

adapter 只做数据获取和转换，不写数据库。
"""

from __future__ import annotations

import asyncio
from typing import Any


class AcademicAdapterError(Exception):
    pass


class AcademicAdapter:
    """教务适配器抽象基类。"""

    adapter_id: str = "base"
    adapter_name: str = "Base Adapter"

    def __init__(self, **kwargs):
        self._opts = kwargs
        self._authenticated = False

    # ── 子类需实现 ──

    async def authenticate(self, creds: dict[str, Any]) -> None:
        """用账号/密码/cookie 等凭证完成登录。成功后 self._authenticated=True。"""
        raise NotImplementedError

    async def fetch_semesters(self) -> list[dict]:
        """返回可用学期列表，每项 {name, start_date?, end_date?, adapter_id?}。"""
        raise NotImplementedError

    async def fetch_courses(self, semester: dict) -> list[dict]:
        """返回某学期全部课程，每项结构与 normalized.courses[i] 一致。"""
        raise NotImplementedError

    async def fetch_exceptions(self, semester: dict) -> list[dict]:
        """可选：返回调课/放假信息，每项结构与 normalized.exceptions[i] 一致。"""
        return []

    # ── 公共方法 ──

    async def pull(self, creds: dict[str, Any], semester_selector: dict | None = None) -> dict:
        """完整拉取流程：登录 → 选学期 → 拉课程 → 组装 normalized dict。"""
        await self.authenticate(creds)

        # 把 semester_keyword 存到实例上，fetch_semesters() 可以读
        if semester_selector and semester_selector.get("keyword"):
            self._semester_keyword = semester_selector["keyword"]

        semesters = await self.fetch_semesters()
        if not semesters:
            raise AcademicAdapterError("适配器未返回任何学期")

        selected = semesters[0]
        if semester_selector:
            # 按 id / name / 关键字匹配
            sid = semester_selector.get("id")
            sname = semester_selector.get("name") or semester_selector.get("keyword")
            for s in semesters:
                if sid is not None and s.get("adapter_id") == sid:
                    selected = s
                    break
                if sname and sname in (s.get("name") or ""):
                    selected = s
                    break

        courses, exceptions = await asyncio.gather(
            self.fetch_courses(selected),
            self.fetch_exceptions(selected),
        )
        return {
            "source": self.adapter_id,
            "semester": {
                "name": selected.get("name"),
                "start_date": selected.get("start_date"),
                "end_date": selected.get("end_date"),
                "adapter_id": selected.get("adapter_id"),
            },
            "courses": courses,
            "exceptions": exceptions,
        }
