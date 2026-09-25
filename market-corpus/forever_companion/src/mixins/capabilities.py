"""能力中心运行面（1.2.7）：统一开关判定 / 面板功能管理入口 / 工具显隐同步。

设计契约（与 core/capabilities.py 的声明层配套）：

- **唯一判定入口** ``_cap_effective(cap_id, lanlan/shard)``：所有功能闸
  （``_mood_enabled`` / ``_fragments_enabled`` / ``_review_enabled`` /
  ``_journal_enabled`` / ``_emotion_sense_enabled`` 及注入点）收编至此，
  散点 if 不再各自为政；判定 = 总开关(root) → 用户否决(caps@*) →
  配置默认(既有 [mood]/[fragments]/… 段) → 依赖链，四道闸一次算清。
- **否决式覆盖层**：面板「功能管理」只往 ``caps@<角色>`` / ``caps@*`` 写
  "关"，开 = 移除条目回落配置默认；plugin.toml 各段照旧作默认值，
  旧设置页开关（写 settings 覆盖层）与本层互不覆写、天然合流。
- **按角色独立**：否决集按角色分片（与周期/情绪/日记同一套 fail-closed
  纪律），``caps@*`` 为全局份、对所有角色生效；面板开关作用于宿主当前角色。
- **工具显隐（高级选项 ``[capabilities].hide_disabled_tools``）**：
  默认温和模式——关闭的能力其 @llm_tool 仍在位、调用被拒（既有语义不变）；
  开启后，对**所有已登记角色都不生效**的能力，其工具经
  ``LLM_TOOL_UNREGISTER`` 从宿主可见面摘除，恢复生效时重新注册。
  实现只动"宿主可见性"通知（meta 与动态入口留在本地），因此开关瞬时
  重挂零成本、_ensure_tools_registered 巡检对隐藏工具跳过（不抵消）。
- **tick 监督**：``_sync_tool_visibility`` 每趟 tick 跑一次（纯内存比对，
  只在增删时发通知），覆盖"面板没开、只靠对话/超时改变状态"的场景。
"""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, Ok, Result, SdkError, plugin_entry, tr, ui

from ..core.capabilities import (
    CAPABILITY_SPECS,
    CapabilitySpec,
    CapState,
    capability_tools_for,
    evaluate_capabilities,
)
from ..core.intros import build_intro_payload
from ..core.state import _caps_key

JsonObject = dict[str, Any]

# 全局否决集的伪角色名（Store key "caps@*"；角色名不会取 "*"，无冲突）
CAPS_GLOBAL = "*"


class CapabilityMixin:
    """能力中心：统一开关判定、面板入口、工具显隐同步。"""

    # ==========================================
    # 判定
    # ==========================================

    def _cap_lanlan(self, shard: Any = None, lanlan: str = "") -> str:
        """解析判定归属角色名：显式 lanlan > shard.lanlan > 当前角色。"""
        name = str(lanlan or "").strip()
        if not name and shard is not None:
            name = str(getattr(shard, "lanlan", "") or "").strip()
        if not name:
            name = self._current_shard_name()
        return name

    def _cap_off_set(self, lanlan: str) -> set[str]:
        """该角色生效的否决集 = 全局份 ∪ 角色份（内存，_ensure_shard 时载入）。"""
        out = set(self._caps_off.get(CAPS_GLOBAL) or ())
        out |= set(self._caps_off.get(lanlan) or ())
        return out

    def _cap_config_flags(self) -> dict[str, bool]:
        """各能力绑定的既有配置键现值（经 _refresh_config 合成后的 cfg 视图）。"""
        flags: dict[str, bool] = {}
        for spec in CAPABILITY_SPECS.values():
            if spec.config is None:
                continue
            section, key, default = spec.config
            cfg = getattr(self, f"_{section}_cfg", None) or {}
            flags[spec.id] = bool(cfg.get(key, default))
        return flags

    def _cap_states(self, lanlan: str = "", shard: Any = None) -> dict[str, CapState]:
        """解析该角色全部能力的生效状态与原因（纯计算，每调用一次重算一遍）。"""
        name = self._cap_lanlan(shard=shard, lanlan=lanlan)
        shard_obj = shard if shard is not None else self._get_shard(name)
        return evaluate_capabilities(
            root_enabled=self._enabled(shard_obj),
            config_flags=self._cap_config_flags(),
            overrides_off=self._cap_off_set(name),
        )

    def _cap_effective(self, cap_id: str, lanlan: str = "", shard: Any = None) -> bool:
        """唯一判定入口：该能力对该角色此刻是否生效。未知 id 按 True 宽容
        （能力层是否决制——不认识的名字没有否决权，绝不因拼错 id 悄悄关掉功能）。"""
        if cap_id not in CAPABILITY_SPECS:
            return True
        return self._cap_states(lanlan=lanlan, shard=shard)[cap_id].enabled

    def _cap_flags_cfg(self) -> JsonObject:
        """[capabilities] 段（toml 默认 + settings 覆盖层，_refresh_config 合成）。"""
        return getattr(self, "_caps_cfg", None) or {}

    def _cap_hide_tools_enabled(self) -> bool:
        return bool(self._cap_flags_cfg().get("hide_disabled_tools", False))

    # ==========================================
    # 载入 / 落盘
    # ==========================================

    def _cap_norm_off(self, raw: Any) -> set[str]:
        """Store 记录 {cap_id: true} → 否决集（坏条目静默丢弃，宽容载入）。"""
        if not isinstance(raw, dict):
            return set()
        return {str(k) for k, v in raw.items() if v and str(k) in CAPABILITY_SPECS}

    async def _cap_load(self, lanlan: str) -> None:
        """从 Store 载入某角色的否决集（_ensure_shard 调用；全局份在 _load_state）。"""
        res = await self._store_read(_caps_key(lanlan))
        self._caps_off[lanlan] = self._cap_norm_off(res.value if isinstance(res, Ok) else None)

    async def _cap_save(self, lanlan: str) -> Result[None]:
        """否决集落盘唯一出口（记录式 {cap_id: true}，空集也写——删除会让
        下次载入回落为无否决，与"清空"语义等价，写空记录更省一份歧义）。"""
        payload = {cap_id: True for cap_id in sorted(self._caps_off.get(lanlan) or ())}
        return await self._store_write(_caps_key(lanlan), payload, f"caps@{lanlan or '*'}")

    def _cap_config_on(self, cap_id: str, lanlan: str = "", shard: Any = None) -> bool:
        """忽略总开关的"系统本身是否开着"：配置默认 ∧ 非用户否决（不含依赖链）。

        供状态条"情绪系统未开启"提示与就绪清单这类"别因总开关叠加双重提示"
        的展示位使用：总开关关着时那些位置已另有横幅，不把链路捕成双黄。
        """
        spec = CAPABILITY_SPECS.get(cap_id)
        if spec is None:
            return True
        name = self._cap_lanlan(shard=shard, lanlan=lanlan)
        if cap_id in self._cap_off_set(name):
            return False
        if spec.config is None:
            return True
        section, key, default = spec.config
        cfg = getattr(self, f"_{section}_cfg", None) or {}
        return bool(cfg.get(key, default))

    def _cap_view(self, lanlan: str) -> JsonObject:
        """能力总览视图（按角色）：dashboard 轮询与 list_capabilities 入口共用。

        字段极轻（9 行 × 小字段，无正文无 IO），1.2.7 起随 dashboard 5s 轮询
        下发——总开关/设置页/后台变化与功能页永远同帧，不再有"按需拉一次
        就定格"的陈旧窗口（图库/统计那种大数据按需拉取纪律不适用于此）。
        调用方需确保 shard 已载入（dashboard 路径天然满足；入口自行 ensure）。
        """
        states = self._cap_states(lanlan)
        off_set = self._cap_off_set(lanlan)
        caps: list[JsonObject] = []
        for spec in CAPABILITY_SPECS.values():
            if not spec.managed:
                continue
            state = states[spec.id]
            caps.append({
                "id": spec.id,
                "group": spec.group,
                "llm": spec.llm,
                "tools": list(spec.tools),
                "enabled": state.enabled,
                "source": state.source,
                "blocked_by": list(state.blocked_by),
                # user_off：否决集里有它（哪怕上游已挡着也如实回显，
                # 上游恢复后仍保持关——用户的选择在，只是暂时看不见）
                "user_off": spec.id in off_set,
            })
        return {
            "lanlan": lanlan,
            "master_enabled": bool(self._enabled(self._get_shard(lanlan))),
            "hide_disabled_tools": self._cap_hide_tools_enabled(),
            "capabilities": caps,
        }

    # ==========================================
    # 面板入口
    # ==========================================

    @ui.action(
        label=tr("actions.list_capabilities.label", default="查看功能清单"),
        tone="default",
    )
    @plugin_entry(
        id="list_capabilities",
        name=tr("entries.list_capabilities.name", default="查看功能清单"),
        description=tr(
            "entries.list_capabilities.description",
            default="返回全部功能能力的声明与当前生效状态（按角色）：开关态、不生效原因、上游依赖与 LLM 触点类型。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "lanlan": {"type": "string", "description": "角色名；留空 = 宿主当前角色"},
            },
        },
    )
    async def list_capabilities(self, lanlan: str = "", **_: Any):
        """能力清单入口（API/调试用）：面板功能页走 dashboard 轮询里的
        capabilities 字段（同一构建器 _cap_view，口径唯一）。"""
        name = str(lanlan or "").strip() or self._current_shard_name()
        await self._ensure_shard(name)
        return Ok(self._cap_view(name))

    @ui.action(
        label=tr("actions.get_capability_intro.label", default="查看功能介绍"),
        tone="default",
    )
    @plugin_entry(
        id="get_capability_intro",
        name=tr("entries.get_capability_intro.name", default="查看功能介绍"),
        description=tr(
            "entries.get_capability_intro.description",
            default="返回指定功能能力的介绍：作用、主要场景、限制与注意事项、原理流程与静态依赖声明（文案以 i18n 引用下发，由面板按语言解析）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "capability_id": {"type": "string", "description": "能力 id（见 list_capabilities）"},
            },
            "required": ["capability_id"],
        },
        metadata={"agent_hidden": True},
    )
    async def get_capability_intro(self, capability_id: str = "", **_: Any):
        """一项功能的介绍卡片数据（按需拉取，不进 5s 轮询）。

        文案事实源在 core/intros.py（中文原文 + key 派生规则），这里只把
        tr() 引用原样透传：action 返回值不经宿主 i18n 解析（只有
        dashboard context 会解析），前端用 t(key, {defaultValue}) 自行
        按宿主语言展开——zh 走 default，en 等小语种进 i18n/*.json 补 key
        即可生效，三层零耦合。依赖/触点/配置键从声明表现场取，介绍与
        开关状态永不同源不同。
        """
        cap_id = str(capability_id or "").strip()
        spec = CAPABILITY_SPECS.get(cap_id)
        if spec is None or not spec.managed:
            self.logger.warning("unknown capability (intro): {}", repr(cap_id))
            return Err(SdkError("unknown_capability"))
        # SDK 的 tr(key, *, default=) 里 default 是 keyword-only（生产实测：
        # 直接传 build_intro_payload 会炸 "tr() takes 1 positional argument"）；
        # 这里包一层适配成 core 层的 (key, zh) 双参约定。测试桦已收紧同构。
        return Ok(build_intro_payload(spec, lambda k, d: tr(k, default=d)))

    @ui.action(
        label=tr("actions.set_capability.label", default="开关功能能力"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_capability",
        name=tr("entries.set_capability.name", default="开关功能能力"),
        description=tr(
            "entries.set_capability.description",
            default="开启或关闭一项功能能力（按角色）；关闭为否决式，重新打开回落既有配置默认。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "capability_id": {"type": "string", "description": "能力 id（见 list_capabilities）"},
                "enabled": {"type": "boolean", "description": "true=开（撤否决），false=关（写否决）"},
                "lanlan": {"type": "string", "description": "角色名；留空 = 宿主当前角色"},
            },
            "required": ["capability_id", "enabled"],
        },
    )
    async def set_capability(self, capability_id: str = "", enabled: bool = True, lanlan: str = "", **_: Any):
        """面板开/关一项能力（按角色；lanlan 缺省 = 宿主当前角色）。

        enabled=False → 写否决；True → 撤否决回落配置默认。开不获得
        "强行点亮"权力：上游关着 / 配置关着时开关会如实回弹并说明原因，
        面板据此提示用户去哪一层打开。
        """
        cap_id = str(capability_id or "").strip()
        spec = CAPABILITY_SPECS.get(cap_id)
        if spec is None or not spec.managed:
            self.logger.warning("unknown capability (set): {}", repr(cap_id))
            return Err(SdkError("unknown_capability"))
        name = str(lanlan or "").strip() or self._current_shard_name()
        await self._ensure_shard(name)
        current = self._cap_effective(cap_id, lanlan=name)
        offs = self._caps_off.setdefault(name, set())
        if enabled:
            # 撤否决 = 回落配置默认；只动角色份（全局份 caps@* 不在面板暴露，
            # 若跨层强行解锁会造成"内存开了、重启又关"的两张皮）
            offs.discard(cap_id)
        else:
            offs.add(cap_id)
        res_save = await self._cap_save(name)
        self._sync_tool_visibility()
        states = self._cap_states(name)
        state = states[cap_id]
        payload: JsonObject = {
            "id": cap_id,
            "lanlan": name,
            "was_enabled": current,
            "enabled": state.enabled,
            "source": state.source,
            "blocked_by": list(state.blocked_by),
            "hide_disabled_tools": self._cap_hide_tools_enabled(),
        }
        if isinstance(res_save, Err):
            # 既定契约：内存当场生效、盘上保持原样，向用户如实报错——
            # 回稳定码（面板按 panel.errors.persistFailed 翻译，i18n 契约第九轮），细节已进日志
            payload["persist_error"] = "persist_failed"
            self.logger.warning("persist caps for {} failed: {}", name, res_save.error)
        if enabled and not state.enabled:
            # 用户点"开"但没能点亮（配置/上游挡着）：如实回传原因，面板回弹
            payload["note"] = "reverted_to_default"
        return Ok(payload)

    @ui.action(
        label=tr("actions.set_capability_flags.label", default="保存功能管理选项"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_capability_flags",
        name=tr("entries.set_capability_flags.name", default="功能管理高级选项"),
        description=tr(
            "entries.set_capability_flags.description",
            default="能力中心全局选项：hide_disabled_tools（关闭的能力是否从模型可见面摘除工具）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "hide_disabled_tools": {"type": "boolean"},
            },
        },
        metadata={"agent_hidden": True},
    )
    async def set_capability_flags(self, hide_disabled_tools: bool | None = None, **_: Any):
        """能力中心全局选项（写 settings 覆盖层的 capabilities 段）。"""
        if hide_disabled_tools is not None:
            override = self._settings_override.setdefault("capabilities", {})
            override["hide_disabled_tools"] = bool(hide_disabled_tools)
            self._caps_cfg["hide_disabled_tools"] = bool(hide_disabled_tools)
            res_save = await self._save_settings()
            self._sync_tool_visibility()
            if isinstance(res_save, Err):
                return res_save
        return Ok({"hide_disabled_tools": self._cap_hide_tools_enabled()})

    # ==========================================
    # 工具显隐同步（高级选项）
    # ==========================================

    def _cap_scan_lanlans(self) -> list[str]:
        """显隐判定要遍历的角色集：已登记角色 ∪ 当前角色（至少一个占位 ""）。"""
        names = [str(n) for n in self._lanlan_index if str(n)]
        current = self._current_shard_name()
        if current and current not in names:
            names.append(current)
        return names or [""]

    def _cap_desired_hidden_tools(self) -> set[str]:
        """当前状态下应从宿主可见面摘除的工具集（纯计算，供同步与测试断言）。"""
        if not self._cap_hide_tools_enabled():
            return set()
        states_per_role: list[dict[str, CapState]] = [
            self._cap_states(lanlan=name) for name in self._cap_scan_lanlans()
        ]
        on_tools: set[str] = set()
        off_tools: set[str] = set()
        for states in states_per_role:
            on, off = capability_tools_for(states)
            on_tools |= on
            off_tools |= off
        # 任一角色生效即保留：宿主工具注册没有按角色隐藏的通道
        return off_tools - on_tools

    def _sync_tool_visibility(self) -> None:
        """把"关闭的能力不该被模型看见"落到宿主（只在差集变化时发通知）。

        温和模式（默认）下 desired 恒为空集：全部在位，行为与 1.2.x 完全一致。
        只发 LLM_TOOL_REGISTER/UNREGISTER 通知、不动本地 ``_llm_tools`` 与
        动态入口——meta 永远在本地，重新可见是零成本重挂；
        host_coord 的丢失巡检（_reemit_missing_tools）对隐藏名单跳过，
        不会每 5 分钟把摘掉的工具偷偷挂回来。
        """
        llm_tools = getattr(self, "_llm_tools", None)
        notify_on = getattr(self, "_notify_llm_tool_registered", None)
        notify_off = getattr(self, "_notify_llm_tool_unregistered", None)
        if not isinstance(llm_tools, dict) or not callable(notify_on) or not callable(notify_off):
            return  # SDK 桩/老宿主：显隐不可用，温和模式语义天然成立
        desired = self._cap_desired_hidden_tools()
        hidden = self._cap_hidden_tools
        to_hide = sorted((desired - hidden) & set(llm_tools))
        to_show = sorted((hidden & set(llm_tools)) - desired)
        if not to_hide and not to_show:
            return
        actually_hidden: list[str] = []
        actually_shown: list[str] = []
        for name in to_hide:
            meta = llm_tools.get(name)
            try:
                notify_off(name, role=getattr(meta, "role", None))
            except Exception as exc:  # noqa: BLE001 - 单工具失败不影响其余
                self.logger.debug("tool hide failed for {}: {}", name, exc)
                continue
            hidden.add(name)
            actually_hidden.append(name)
        for name in to_show:
            meta = llm_tools.get(name)
            try:
                notify_on(meta)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("tool unhide failed for {}: {}", name, exc)
                continue
            hidden.discard(name)
            actually_shown.append(name)
        self.logger.info(
            "tool visibility synced: hidden={} shown={} (hide_disabled_tools={})",
            actually_hidden,
            actually_shown,
            self._cap_hide_tools_enabled(),
        )

    # ==========================================
    # 声明表访问（面板分组渲染与 i18n 缺 key 时的兜底展示名）
    # ==========================================

    def _cap_spec(self, cap_id: str) -> CapabilitySpec | None:
        return CAPABILITY_SPECS.get(cap_id)
