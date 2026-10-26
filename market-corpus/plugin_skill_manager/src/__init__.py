"""plugin_skill_manager —— N.E.K.O Agent Skill 插件（接口层）。

两类 Skill
----------
**自带（builtin）**：插件目录 ``skills/<name>/SKILL.md``，随包分发，四个可执行 Skill
把知识库 / 数据库的核心操作封装成符合 Agent Skills 规范的入口：

* ``knowledge-search``  语义搜索知识库      -> plugin_knowledge_base:kb_search
* ``knowledge-import``  导入文件到知识库    -> plugin_knowledge_base:kb_import
* ``database-query``    查询热数据表        -> plugin_knowledge_base:kb_hot_list
* ``database-export``   导出热数据为文件    -> plugin_knowledge_base:kb_hot_export

**外部（external）**：用户文件夹里各 Agent 软件的 skills 根（``~/.agents/skills``、
``~/.claude/skills``、``~/.codex/skills`` … 见 ``engine/external.py``）。这些绝大多数是
「给 Agent 看的指令包」，没有可执行语义，因此**只登记不注册入口**——它们出现在
``skill_list`` / ``skill_validate`` / 面板里，可以按名字启用禁用，但不会污染宿主的
可调用入口表。挂了路由（``[skill_manager.routes.*]``）且 ``builtin_only = false`` 时才
注册为运行时入口。

挂载方式是 **Windows 目录联接（junction）**：``<plugin>/skills/<name>`` 指向源目录，
零拷贝、源目录改了立刻生效。宿主同步是整树复制，几百 MB 的 skill 复制一遍会拖慢启动。

启用/禁用状态持久化在插件存储（``skill_enabled:<name>``）；每次**入口调用**记录耗时与
成败，经知识库插件转发写入数据库插件的 ``skill_stats`` 表（Skill -> KB -> DB 链路，
本插件不直接操作数据库）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, ui

from .engine import external
from .engine.registry import (
    SkillDef,
    SkillFormatError,
    discover_with_errors,
    parse_skill_md,
)

KB_TARGET = "plugin_knowledge_base"

#: 自带 Skill 的默认路由：Skill 名 -> (kb 入口, 参数白名单)
DEFAULT_ROUTES: dict[str, dict] = {
    "knowledge-search": {"entry": "kb_search", "params": ["query", "top_k", "track_hot"]},
    "knowledge-import": {"entry": "kb_import", "params": ["source_dir", "single_file"]},
    "database-query": {"entry": "kb_hot_list", "params": ["limit", "include_demoted"]},
    "database-export": {"entry": "kb_hot_export", "params": ["path", "fmt", "include_demoted"]},
}

#: 挂载方式的中文说明（面板/列表用）
LINK_LABEL = {
    "junction": "目录联接",
    "copy": "已复制",
    "native": "随包自带",
    "failed": "挂载失败",
    "conflict": "占位冲突",
}


def _fail(exc: BaseException) -> Err:
    return Err(SdkError(str(exc)))


@neko_plugin
class PluginSkillManagerPlugin(NekoPluginBase):
    """Agent Skill 注册表：发现 / 挂载 / 注册 / 启用禁用 / 重载 / 调用统计。"""

    @ui.context(id="plugin_skill_manager_panel")
    async def _ui_panel_state(self):
        """Hosted 面板状态:插件元信息 + 入口清单(生成,方向C)。"""
        return {
            'plugin': {
                'id': 'plugin_skill_manager',
                'name': 'Plugin Skill Manager',
                'version': '0.2.0',
                'description': 'Agent Skill 管理器：自带 4 个可执行 Skill（knowledge-search / knowledge-import / database-query / database-export，转发到知识库插件），并把用户文件夹各 Agent 的 skills 根（.agents/.claude/.codex/…）以目录联接挂进 skills/ 统一登记、启用禁用与校验。',
            },
            'entries': [
                {
                    'id': 'skill_list',
                    'name': 'Skill 列表',
                    'description': '列出全部 Skill、来源、挂载方式与状态',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'skill_sync',
                    'name': '同步用户 Skill',
                    'description': '重扫用户文件夹各 skills 根并挂载到 skills/（目录联接）',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'skill_enable',
                    'name': '启用 Skill',
                    'description': '启用一个 Skill（可执行的路由型才注册入口）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'skill_disable',
                    'name': '禁用 Skill',
                    'description': '禁用一个 Skill 并注销其运行时入口',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'skill_reload',
                    'name': '重载 Skill 注册表',
                    'description': '重新扫描自带 skills/ 与用户 skills 根并按启用状态注册',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'skill_invoke',
                    'name': '调用 Skill',
                    'description': '手动触发一个可执行 Skill（等价于 Agent 调用路径，含统计）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'skill_validate',
                    'name': '校验 SKILL.md',
                    'description': '按 Agent Skills 规范校验全部 SKILL.md，返回每份文件结果',
                    'has_required': False,
                    'has_params': False
                },
            ],
        }

    def __init__(self, ctx):
        super().__init__(ctx)
        self._skills: dict[str, SkillDef] = {}
        self._routes: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._external: dict[str, external.ExternalSkill] = {}
        self._errors: list[str] = []
        self._notes: list[str] = []
        self._cfg: dict = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        # 宿主在构造期用不完整的 ctx 冻结 store.enabled（见 neko_live devlog），
        # plugin.toml 的 [plugin.store] 可能没生效——这里照既有插件先例手动兜底。
        try:
            if not getattr(self.store, "enabled", True):
                self.store.enabled = True
        except Exception:
            pass
        self._errors = []
        self._notes = []
        self._cfg = await self._load_cfg()
        await self._discover_and_register()
        summary = []
        for name, skill in sorted(self._skills.items()):
            summary.append(
                {
                    "name": name,
                    "origin": skill.origin,
                    "link": skill.link,
                    "enabled": await self._is_enabled(name),
                    "routable": name in self._routes,
                }
            )
        return Ok(
            {
                "skills": summary,
                "builtin": sum(1 for s in self._skills.values() if s.origin == "builtin"),
                "external": sum(1 for s in self._skills.values() if s.origin == "external"),
                "registered": sorted(n for n in self._skills if n in self._routes),
                "discover_errors": list(self._errors),
                "notes": list(self._notes),
            }
        )

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        # 只注销入口。**不摘挂载点**：
        # 卸载时摘掉会让下次启动必须重建 93 个 junction（>3s，超宿主启动预算），
        # 而且一旦 store 里的挂载名与自带重名，就会把自带真目录删掉。
        # 挂载点留着无害——重装/迁移时用 skill_sync(force=true) 重建即可。
        for name in list(self._skills):
            self.unregister_dynamic_entry(name)
        self._skills.clear()
        self._routes.clear()
        return Ok({"status": "closed"})

    async def _load_cfg(self) -> dict:
        try:
            dumped = await self.config.dump()
            return dict(dumped or {})
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _build_routes(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        """路由表：自带 Skill 走默认/配置覆盖；外部 Skill 只在显式配置且允许时挂。"""
        cfg = self._cfg.get("skill_manager", {}) or {}
        builtin_only = cfg.get("builtin_only", True)
        raw = cfg.get("routes") or {}

        merged: dict[str, dict] = {k: dict(v) for k, v in DEFAULT_ROUTES.items()}
        for name, spec in raw.items():
            if isinstance(spec, dict) and spec.get("entry"):
                merged[str(name)] = {
                    "entry": str(spec["entry"]),
                    "params": list(spec.get("params") or []),
                }

        routes: dict[str, tuple[str, tuple[str, ...]]] = {}
        for name, spec in merged.items():
            skill = self._skills.get(name)
            if skill is None:
                continue
            if skill.origin == "external" and builtin_only:
                self._notes.append(f"{name}: 外部 Skill 有路由定义，但 builtin_only=true，不注册入口")
                continue
            routes[name] = (str(spec["entry"]), tuple(spec.get("params") or ()))
        return routes

    # ------------------------------------------------------------------
    # 发现 / 挂载 / 注册
    # ------------------------------------------------------------------

    def _skills_root(self) -> Path:
        return self.plugin_dir / "skills"

    def _extra_roots(self) -> list[str]:
        cfg = self._cfg.get("skill_manager", {}) or {}
        return [str(p) for p in (cfg.get("extra_roots") or []) if p]

    def _home(self) -> Path:
        cfg = self._cfg.get("skill_manager", {}) or {}
        override = str(cfg.get("home", "") or "").strip()
        return Path(override).expanduser() if override else Path.home()

    async def _discover_and_register(self) -> None:
        self._errors = []
        self._notes = []

        # 1) 扫用户文件夹 + 校验现有挂载，**不摘不删**。
        #    启动有 3s 硬预算，而 93 个 junction 重挂要约 2.1s——必须在启动路径上避开。
        #    只有「新增 / 失效」的挂载点才会真正动盘，稳态下这一步是纯读。
        previous = await self._mounted_names()
        try:
            found, notes = await asyncio.to_thread(
                external.scan, self._home(), self._extra_roots()
            )
        except Exception as exc:  # 扫描失败不挡自带 Skill
            self._errors.append(f"external scan failed: {exc}")
            found, notes = [], []
        self._notes.extend(notes)

        # 2) 自带 Skill：只排除 **当前真的是联接** 的挂载点。
        #    不能用「外部候选名」来排除——用户 skill 根里也有 neko-plugin-dev 这类
        #    与自带同名的 Skill，那样会把自带错判成外部、进而覆盖掉自带真目录。
        link_names = await asyncio.to_thread(self._link_names)
        builtin_defs, errors = await asyncio.to_thread(
            discover_with_errors, self._skills_root(), link_names
        )
        self._errors.extend(errors)
        self._skills.clear()
        builtin_names = {s.name for s in builtin_defs}
        for skill in builtin_defs:
            skill.origin = "builtin"
            skill.link = "native"
            self._skills[skill.name] = skill

        # 3) 挂载外部 Skill：只处理「缺了 / 指错了」的，其余原样留着
        self._external = {}
        to_mount: list[external.ExternalSkill] = []
        for ext in found:
            if ext.name in builtin_names:
                self._notes.append(f"{ext.root}/{ext.name}: 与插件自带 Skill 同名，跳过")
                continue
            dest = self._skills_root() / ext.name
            try:
                status = await asyncio.to_thread(external.link_status, dest, ext.src)
            except OSError:
                status = "missing"
            if status == "linked":
                self._register_external(ext, dest, "junction")  # 复用已有挂载
            elif status == "conflict":
                # 这里是个真实目录：可能是自带 Skill 或用户数据。
                # 绝不覆盖——真要重挂请显式 skill_sync(force=true)。
                self._notes.append(
                    f"{ext.name}: skills/ 下已是真实目录（非联接），不覆盖；"
                    "如需重挂请用 skill_sync(force=true)"
                )
            else:
                to_mount.append(ext)

        # 上次挂过、这次不再存在的联接，摘掉（只删联接，真实目录一律留着）
        for name in set(previous) - {e.name for e in found}:
            d = self._skills_root() / name
            # 注意：**不能用 d.is_dir() 做守卫**。失效联接（目标已被删/改名）
            # 的 is_dir() 是 False —— is_dir() 会跟随重解析点，目标不在就为假。
            # 那正是这里唯一要处理的情况，用 is_dir() 会让清理永远不生效、
            # 失效挂载点无限堆积（实测 plugin-creator 就是这么留下的）。
            # external.is_link() 走 lstat 看重解析位，对失效联接同样为 True。
            if external.is_link(d):
                try:
                    await asyncio.to_thread(external.unmount, d)
                except Exception as exc:
                    self._errors.append(f"unmount stale {name} failed: {exc}")

        for ext in to_mount:
            await self._mount_one(ext)
        await self._save_mounted(sorted(self._external))

        # 4) 路由 + 注册（只有挂了路由的才注册成运行时入口）
        self._routes = self._build_routes()
        for name in list(self._skills):
            if name not in self._routes or not await self._is_enabled(name):
                self.unregister_dynamic_entry(name)
                continue
            self._register_one(name, self._skills[name])

    # ------------------------------------------------------------------
    # 外部 Skill 的挂载管理
    # ------------------------------------------------------------------

    _MOUNT_KEY = "external_mounts"

    async def _mounted_names(self) -> list[str]:
        """上次我们挂过的外部 Skill 名（用来认领非联接的复制残留）。"""
        try:
            result = await self.store.get(self._MOUNT_KEY)
        except Exception:
            return []
        if isinstance(result, Ok) and isinstance(result.value, dict):
            return [str(n) for n in (result.value.get("names") or [])]
        return []

    async def _save_mounted(self, names: list[str]) -> None:
        try:
            await self.store.set(self._MOUNT_KEY, {"names": sorted(names)})
        except Exception as exc:
            self._errors.append(f"persist external mounts failed: {exc}")

    def _link_names(self) -> set[str]:
        """``skills/`` 下所有重解析点（= 我们挂上去的外部 Skill）的名字。"""
        root = self._skills_root()
        if not root.is_dir():
            return set()
        names: set[str] = set()
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return names
        for d in entries:
            # 用 external.is_link() 而非 d.is_dir()：失效联接的 is_dir() 是 False
            # （is_dir 跟随重解析点，目标不在即为假），会让失效挂载点统计不到。
            if external.is_link(d):
                names.add(d.name)
        return names

    async def _unmount_stale(self) -> None:
        """摘掉全部外部挂载（force 重扫用）。**只删联接，永不删真实目录。**

        真实目录一律留着：那可能是随包自带的 Skill，也可能是用户自己放进去的东西。
        删掉自带的真目录是曾经踩过的坑（store 里的挂载名可能与自带重名）。
        """
        root = self._skills_root()
        if not root.is_dir():
            self._external = {}
            return
        try:
            entries = await asyncio.to_thread(lambda: sorted(root.iterdir()))
        except OSError as exc:
            self._errors.append(f"list skills/ failed: {exc}")
            self._external = {}
            return
        for d in entries:
            # 同上：失效联接也必须能摘掉，否则 force 重扫清不干净。
            if external.is_link(d):
                try:
                    await asyncio.to_thread(external.unmount, d)
                except Exception as exc:
                    self._errors.append(f"unmount {d.name} failed: {exc}")
        await self._save_mounted([])
        self._external = {}

    def _register_external(
        self, ext: external.ExternalSkill, dest: Path, how: str
    ) -> None:
        """登记一个外部 Skill（挂载点已就位，只登记不再动盘）。"""
        self._skills[ext.name] = SkillDef(
            name=ext.name,
            description=ext.description,
            dir=dest,
            license=ext.license,
            metadata=ext.metadata or {},
            origin="external",
            root=ext.root,
            link=how,
        )
        self._external[ext.name] = ext

    async def _mount_one(self, ext: external.ExternalSkill) -> None:
        dest = self._skills_root() / ext.name
        try:
            how, skipped = await asyncio.to_thread(
                external.mount, dest, ext.src, self._copy_max_bytes()
            )
        except Exception as exc:
            self._errors.append(f"mount {ext.name} failed: {exc}")
            return
        if how == "failed":
            self._errors.append(f"mount {ext.name} failed: junction 与复制都没成功")
            return
        for rel in skipped:
            self._notes.append(f"{ext.name}: 跳过超大文件 {rel}")
        self._register_external(ext, dest, how)

    def _copy_max_bytes(self) -> int:
        cfg = self._cfg.get("skill_manager", {}) or {}
        try:
            return int(cfg.get("copy_max_bytes", external.COPY_MAX_BYTES))
        except (TypeError, ValueError):
            return external.COPY_MAX_BYTES

    # ------------------------------------------------------------------
    # 动态入口
    # ------------------------------------------------------------------

    def _register_one(self, name: str, skill: SkillDef) -> bool:
        # 先注销再注册：reload/sync 时入口可能已经在册，直接注册会撞
        # EntryConflictError（duplicate entry id）。
        self.unregister_dynamic_entry(name)
        try:
            return self.register_dynamic_entry(
                entry_id=name,
                handler=self._make_handler(name),
                name=skill.name,
                description=skill.description,
                timeout=300.0,
            )
        except Exception as exc:  # 宿主侧其它冲突
            self._errors.append(f"register {name} failed: {exc}")
            return False

    def _make_handler(self, name: str):
        async def handler(**kwargs):
            return await self._invoke(name, kwargs)

        return handler

    async def _invoke(self, name: str, kwargs: dict):
        skill = self._skills.get(name)
        if skill is None:
            return Err(SdkError(f"unknown skill {name!r}"))
        if not await self._is_enabled(name):
            return Err(SdkError(f"skill {name!r} is disabled"))
        route = self._routes.get(name)
        if route is None:
            return Err(
                SdkError(
                    f"skill {name!r} has no KB route"
                    + ("（外部 Skill 默认只登记不注册入口）" if skill.origin == "external" else "")
                )
            )
        entry, allowed = route
        payload = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        started = time.perf_counter()
        result = await self.plugins.call_entry(f"{KB_TARGET}:{entry}", payload)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ok = isinstance(result, Ok)
        if not ok:
            result = Err(SdkError(f"{KB_TARGET}:{entry} -> {result.error}"))
        # 统计经 KB 插件转发到数据库插件；失败不影响调用结果
        stat = await self.plugins.call_entry(
            f"{KB_TARGET}:kb_record_skill_stat",
            {"skill": name, "duration_ms": round(elapsed_ms, 3), "ok": ok},
        )
        if not isinstance(stat, Ok):
            self.logger.warning("record skill stat failed: {}", stat.error)
        if ok:
            data = dict(result.value or {})
            data.setdefault("skill", name)
            data["elapsed_ms"] = round(elapsed_ms, 3)
            return Ok(data)
        return result

    # ------------------------------------------------------------------
    # 启用 / 禁用状态（PluginStore 持久化）
    # ------------------------------------------------------------------

    def _key(self, name: str) -> str:
        return f"skill_enabled:{name}"

    async def _is_enabled(self, name: str) -> bool:
        try:
            result = await self.store.get(self._key(name))
        except Exception:
            return True
        if isinstance(result, Ok) and result.value is not None:
            return bool(result.value.get("enabled", True))
        return True

    async def _set_enabled(self, name: str, enabled: bool) -> None:
        result = await self.store.set(self._key(name), {"enabled": enabled})
        if not isinstance(result, Ok):
            raise SdkError(f"persist skill state failed: {result.error}")

    def _is_registered(self, name: str) -> bool:
        try:
            entries = self.list_entries()
        except Exception:
            return False
        for e in entries or []:
            if isinstance(e, dict) and e.get("id") == name:
                return True
        return False

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    @ui.action(label='Skill 列表', tone="primary", refresh_context=True)

    @plugin_entry(id="skill_list", name="Skill 列表", description="列出全部 Skill、来源、挂载方式与状态")
    async def skill_list(self, origin: str = "", **_):
        rows = []
        wanted = (origin or "").strip().lower()
        for name, skill in sorted(self._skills.items()):
            if wanted and skill.origin != wanted:
                continue
            rows.append(
                {
                    "name": name,
                    "description": skill.description,
                    "license": skill.license,
                    "metadata": skill.metadata,
                    "origin": skill.origin,
                    "root": skill.root,
                    "link": skill.link,
                    "link_label": LINK_LABEL.get(skill.link, skill.link),
                    "routable": name in self._routes,
                    "enabled": await self._is_enabled(name),
                    "registered": self._is_registered(name),
                    "dir": str(skill.dir),
                }
            )
        return Ok(
            {
                "skills": rows,
                "count": len(rows),
                "builtin": sum(1 for s in self._skills.values() if s.origin == "builtin"),
                "external": sum(1 for s in self._skills.values() if s.origin == "external"),
                "discover_errors": list(self._errors),
                "notes": list(self._notes),
            }
        )

    @ui.action(label="同步用户 Skill", tone="primary", refresh_context=True)

    @plugin_entry(
        id="skill_sync",
        name="同步用户 Skill",
        description=(
            "重扫用户文件夹各 skills 根并挂载到 skills/（目录联接，零拷贝）。"
            "force=true 时先摘掉全部挂载再重建，用于宿主升级后联接被复制成实体文件的场景"
        ),
        timeout=300.0,
    )
    async def skill_sync(self, force: bool = False, **_):
        self._cfg = await self._load_cfg()
        if force:
            # 宿主迁移是整树复制（symlinks=False），会把联接实体化成真目录。
            # 这时 link_status 看不到重解析点，只能整批重挂。
            await self._unmount_stale()
        await self._discover_and_register()
        return Ok(
            {
                "builtin": sum(1 for s in self._skills.values() if s.origin == "builtin"),
                "external": sum(1 for s in self._skills.values() if s.origin == "external"),
                "roots": sorted({s.root for s in self._skills.values() if s.root}),
                "discover_errors": list(self._errors),
                "notes": list(self._notes),
            }
        )

    @ui.action(label="启用 Skill", refresh_context=True)

    @plugin_entry(
        id="skill_enable",
        name="启用 Skill",
        description="启用一个 Skill（挂了路由的会注册为运行时入口）",
    )
    async def skill_enable(self, name: str = "", **_):
        skill = self._skills.get(name)
        if skill is None:
            return Err(SdkError(f"unknown skill {name!r}"))
        try:
            await self._set_enabled(name, True)
        except SdkError as exc:
            return _fail(exc)
        if name not in self._routes:
            return Ok(
                {
                    "name": name,
                    "enabled": True,
                    "registered": False,
                    "routable": False,
                    "note": "该 Skill 没有 KB 路由，只登记不注册入口",
                }
            )
        try:
            ok = self.register_dynamic_entry(
                entry_id=name,
                handler=self._make_handler(name),
                name=skill.name,
                description=skill.description,
                timeout=300.0,
            )
        except Exception as exc:
            return _fail(exc)
        return Ok({"name": name, "enabled": True, "registered": ok, "routable": True})

    @ui.action(label="禁用 Skill", refresh_context=True)

    @plugin_entry(
        id="skill_disable",
        name="禁用 Skill",
        description="禁用一个 Skill 并注销其运行时入口",
    )
    async def skill_disable(self, name: str = "", **_):
        if name not in self._skills:
            return Err(SdkError(f"unknown skill {name!r}"))
        try:
            await self._set_enabled(name, False)
        except SdkError as exc:
            return _fail(exc)
        self.unregister_dynamic_entry(name)
        return Ok({"name": name, "enabled": False, "registered": False})

    @ui.action(label='重载 Skill 注册表', tone="primary", refresh_context=True)

    @plugin_entry(
        id="skill_reload",
        name="重载 Skill 注册表",
        description="重新扫描自带 skills/ 与用户 skills 根并按启用状态注册（热更新 SKILL.md）",
        timeout=300.0,
    )
    async def skill_reload(self, **_):
        self._cfg = await self._load_cfg()
        try:
            await self._discover_and_register()
        except SkillFormatError as exc:
            self._errors.append(str(exc))
            return _fail(exc)
        rows = []
        for name, skill in sorted(self._skills.items()):
            rows.append(
                {
                    "name": name,
                    "origin": skill.origin,
                    "enabled": await self._is_enabled(name),
                    "routable": name in self._routes,
                }
            )
        return Ok(
            {
                "skills": rows,
                "count": len(rows),
                "discover_errors": list(self._errors),
                "notes": list(self._notes),
            }
        )

    @ui.action(label="调用 Skill", refresh_context=True)

    @plugin_entry(
        id="skill_invoke",
        name="调用 Skill",
        description="手动触发一个可执行 Skill（等价于 Agent 调用路径，含统计）",
        timeout=300.0,
    )
    async def skill_invoke(self, name: str = "", params: dict | None = None, **_):
        if not name:
            return Err(SdkError("name is required"))
        if not await self._is_enabled(name):
            return Err(SdkError(f"skill {name!r} is disabled"))
        return await self._invoke(name, dict(params or {}))

    @ui.action(label='校验 SKILL.md', tone="primary", refresh_context=True)

    @plugin_entry(
        id="skill_validate",
        name="校验 SKILL.md",
        description="按 Agent Skills 规范校验全部 SKILL.md（含用户文件夹的），返回每份文件结果",
        timeout=300.0,
    )
    async def skill_validate(self, origin: str = "", **_):
        wanted = (origin or "").strip().lower()
        results = []

        def check(skill_dir: Path, label: str, src: str, ext_origin: str) -> None:
            if wanted and ext_origin != wanted:
                return
            md = skill_dir / "SKILL.md"
            if not md.exists():
                return
            try:
                data = parse_skill_md(md)
                results.append(
                    {
                        "dir": label,
                        "source": src,
                        "origin": ext_origin,
                        "ok": True,
                        "name": data.get("name"),
                        "description_len": len(str(data.get("description", ""))),
                    }
                )
            except SkillFormatError as exc:
                results.append(
                    {"dir": label, "source": src, "origin": ext_origin, "ok": False, "error": str(exc)}
                )

        # 自带：直接看插件目录（不是联接）
        root = self._skills_root()
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if not d.is_dir() or external.is_link(d):
                    continue
                check(d, d.name, str(d), "builtin")
        # 外部：校验真实源目录，这样联接断了也能看出问题
        for name, ext in sorted(self._external.items()):
            check(ext.src, name, str(ext.src), "external")

        return Ok({"files": results, "count": len(results)})