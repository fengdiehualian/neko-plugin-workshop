"""功能能力声明与纯解析（1.2.7 能力中心 · 数据层）。

一个"能力"（capability）= 一个可独立开关的功能模块。本文件是**纯数据 +
纯函数**（零 SDK 依赖，与 core/ 其他模块同一纪律）：

- ``CAPABILITY_SPECS``：全部能力的声明表——展示分组、依赖链、配置绑定
  （映射到既有 plugin.toml 段，**不迁移不重排**）、占用的 LLM 工具清单、
  LLM 触点类型（injection=往上下文推话 / tool=注册给模型的工具 /
  direct=直连小模型 / host_http=调宿主内部端点 / none=纯本地）。
  新增功能模块时在这里登记一行，开关判定 / 面板功能管理页 / 工具显隐
  同步全部自动接通。
- ``evaluate_capabilities``：给定"总开关 + 各能力配置默认 + 用户否决集"，
  按声明序（依赖必须先于被依赖者声明）解析出每个能力的生效状态与
  **不生效的原因**（user_off / config_off / upstream_off / master_off）。
  原因要能一路传到面板：让用户知道"这个开关为什么是灰的"。

开关语义（能力层只做**否决**，不获准强行点亮任何功能）：

- 用户否决（caps 覆盖层，按角色）：面板「功能管理」里关掉的能力记在
  ``caps@<角色>`` 与全局 ``caps@*``，命中即 off——**只有 off 能进这张表**；
  重新打开 = 从否决集移除、回落配置默认，绝不存在"配置关着但功能页硬开"
  的两张皮。
- 配置默认：绑定的既有配置键（``[mood].enabled`` 等）照常生效，旧设置
  路径（toml / 面板各页开关经 settings 覆盖层）零改动。
- 依赖链：上游能力不生效则下游连带不生效（例：情绪引擎关 → 语气感知 /
  碎片 / 个人日记 / 我的日记全部熄灭），``blocked_by`` 记录上游 id 供面板
  显示"被谁挡住"。
- 总开关（``[tide].enabled`` / per-shard）是根：关闭时所有能力一律不生效
  （fail-closed 的既有契约原样保留）。

工具显隐（面板「功能管理」的高级选项 ``hide_disabled_tools``）：
默认温和模式——关闭的能力其 @llm_tool 仍在位，调用时被运行时闸门拒绝
（沿用 [mood].enabled 的既有语义）；开启高级选项后，对**所有已登记角色
都不生效**的能力，其工具经 ``LLM_TOOL_UNREGISTER`` IPC 从宿主摘除（模型
不再看见），恢复生效时重新注册。工具显隐是全局的（宿主工具注册没有
按角色隐藏的通道），"每个角色都不生效"才摘、"任一角色生效"即留；
运行时按角色的拒绝闸始终独立于显隐，不会因摘除而放松。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class CapabilitySpec:
    """一项能力的静态声明。

    id: 能力标识（进 Store 否决集与面板协议，勿改名）
    group: 面板分组展示用（rhythm=身体节律 / mood=情绪感知 / diary=日记与记录）
    label: 展示名兜底（面板 i18n 缺 key 时用；正常走 panel.features.<id>.label）
    depends: 上游能力 id（解析按声明序进行，声明表顺序即拓扑序）
    config: 配置绑定 (cfg 段名, 键, 默认)；None = 无既有配置键，默认常开
    tools: 该能力占用的 @llm_tool 名（高级选项"关闭时隐藏工具"的摘除清单）
    llm: LLM 触点类型标注（injection/tool/direct/host_http/none）
    managed: False = 面板不展示不可否决（预留：纯数据能力如相处统计）
    """

    id: str
    group: str
    label: str
    depends: tuple[str, ...] = ()
    config: tuple[str, str, Any] | None = None
    tools: tuple[str, ...] = ()
    llm: str = "none"
    managed: bool = True


# 声明表：顺序即解析拓扑序（依赖者必须排在自己的 depends 之后）。
# 配置绑定逐字对应 plugin.toml 既有段——能力层不新增配置键、不迁移数据。
CAPABILITY_SPECS: dict[str, CapabilitySpec] = {
    spec.id: spec
    for spec in (
        # ---- 身体节律（rhythm）----
        CapabilitySpec(
            id="whisper",
            group="rhythm",
            label="身体轻语",
            llm="injection",
            # 无独立配置键：历史上身体轻语只被总开关与 inject_mode 频控管着，
            # 能力层补上独立否决闸（关掉后 read 注入停，统计/情绪链路照常）
        ),
        CapabilitySpec(
            id="phase_openers",
            group="rhythm",
            label="阶段开场白",
            config=("tide", "phase_openers", True),
            llm="injection",
        ),
        CapabilitySpec(
            id="activity_sense",
            group="rhythm",
            label="生活感知",
            depends=("whisper",),  # 活动行搭载在身体轻语的注入里
            config=("tide", "activity_context", True),
            llm="none",  # 读宿主 OS 活动快照，不动用模型
        ),
        CapabilitySpec(
            id="anniversary",
            group="rhythm",
            label="纪念日轻语",
            config=("stats", "anniversary_inject", True),
            llm="injection",
        ),
        CapabilitySpec(
            id="birthday",
            group="rhythm",
            label="生日轻语",
            config=("birthday", "enabled", True),
            llm="injection",
        ),
        # ---- 情绪与感知（mood）----
        CapabilitySpec(
            id="mood_engine",
            group="mood",
            label="情绪引擎",
            config=("mood", "enabled", True),
            llm="tool",
            tools=(
                "mood_ebb_tide",
                "mood_sea_fog",
                "mood_shallow_reef",
                "mood_storm_surge",
                "mood_seek_harbor",
                "mood_ripple",
                "mood_warm_current",
                "mood_spring_tide",
                "mood_rising_tide",
                "mood_drift_bottle",
            ),
        ),
        CapabilitySpec(
            id="tone_sense",
            group="mood",
            label="语气感知",
            depends=("mood_engine",),
            config=("emotion_sense", "enabled", True),
            llm="host_http",  # 默认走宿主情感端点，选槽位时直连
        ),
        # ---- 三本日记（diary）----
        CapabilitySpec(
            id="fragments",
            group="diary",
            label="时光日记·自动碎片",
            depends=("mood_engine",),
            config=("fragments", "enabled", True),
            llm="direct",
            # 检索工具温和模式下始终在位（日记关只停"捕获"），
            # 高级隐藏模式随本能力摘挂
            tools=("mood_recall_fragments",),
        ),
        CapabilitySpec(
            id="journal",
            group="diary",
            label="个人日记",
            depends=("mood_engine",),
            config=("journal", "enabled", True),
            llm="injection",
            tools=("mood_journal_write",),
        ),
        CapabilitySpec(
            id="review",
            group="diary",
            label="我的日记",
            depends=("mood_engine",),
            config=("review", "enabled", True),
            llm="direct",
        ),
    )
}

# 工具名 → 所属能力 id 的反查表（_reemit 巡检与显隐同步用）
TOOL_CAPABILITY: dict[str, str] = {
    tool: spec.id for spec in CAPABILITY_SPECS.values() for tool in spec.tools
}

# 不生效原因词表（进面板提示与日志；稳定字符串，勿改名）
SOURCE_ON = "on"
SOURCE_USER_OFF = "user_off"  # 功能管理里被用户关掉
SOURCE_CONFIG_OFF = "config_off"  # 绑定的既有配置键关着（旧设置页路径）
SOURCE_UPSTREAM_OFF = "upstream_off"  # 上游能力不生效
SOURCE_MASTER_OFF = "master_off"  # 该角色总开关关着


@dataclass(frozen=True)
class CapState:
    """一项能力的解析结果。"""

    id: str
    enabled: bool
    source: str = SOURCE_ON
    blocked_by: tuple[str, ...] = ()

    def to_payload(self) -> JsonObject:
        out: JsonObject = {
            "id": self.id,
            "enabled": self.enabled,
            "source": self.source,
        }
        if self.blocked_by:
            out["blocked_by"] = list(self.blocked_by)
        return out


def evaluate_capabilities(
    root_enabled: bool,
    config_flags: dict[str, bool],
    overrides_off: set[str] | frozenset[str] = frozenset(),
    *,
    specs: dict[str, CapabilitySpec] | None = None,
) -> dict[str, CapState]:
    """按声明序解析全部能力的生效状态（纯函数，零 IO）。

    root_enabled: 该角色总开关（[tide].enabled / per-shard）
    config_flags: 能力 id → 其绑定配置键的布尔值（缺 key 按配置默认处理，
                  由调用方在组表时填默认值；这里只认已算好的布尔）
    overrides_off: 用户否决集（caps@<角色> ∪ caps@*），命中即强制 off
    """
    specs = specs if specs is not None else CAPABILITY_SPECS
    states: dict[str, CapState] = {}
    for cap_id, spec in specs.items():
        upstream_off = tuple(
            dep for dep in spec.depends if not states[dep].enabled
        )
        if upstream_off:
            # 上游挡住优先于自身配置展示：面板会说"先打开情绪引擎"
            states[cap_id] = CapState(cap_id, False, SOURCE_UPSTREAM_OFF, upstream_off)
            continue
        if not root_enabled:
            states[cap_id] = CapState(cap_id, False, SOURCE_MASTER_OFF)
            continue
        if cap_id in overrides_off:
            states[cap_id] = CapState(cap_id, False, SOURCE_USER_OFF)
            continue
        if not bool(config_flags.get(cap_id, True)):
            states[cap_id] = CapState(cap_id, False, SOURCE_CONFIG_OFF)
            continue
        states[cap_id] = CapState(cap_id, True)
    return states


def capability_tools_for(states: dict[str, CapState]) -> tuple[set[str], set[str]]:
    """按解析结果分组工具：(生效能力的工具并集, 可整体隐藏的工具集)。

    "可整体隐藏" = 该能力关闭且其工具没有同时被某个生效能力认领
    （当前声明表里工具与能力一对一对应，交集为空是常态，防御性照算）。
    """
    on_tools: set[str] = set()
    off_tools: set[str] = set()
    for cap_id, state in states.items():
        spec = CAPABILITY_SPECS.get(cap_id)
        if spec is None:
            continue
        if state.enabled:
            on_tools.update(spec.tools)
        else:
            off_tools.update(spec.tools)
    return on_tools, off_tools - on_tools
