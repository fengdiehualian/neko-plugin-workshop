"""`[sticker_manager]` 配置族的 dataclass 视图（默认值的第三来源）。

三处同源纪律（沿用 our_life 的教训）：本文件的 dataclass 默认值、
`plugin.toml` 的业务段、`config.example.toml` 必须逐键逐值一致，
由 `tests/test_config_docs_sync.py` 钉死。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# 上传单张的硬上限：8 MiB。宿主 images.upload 的解码上限是 32 MiB、
# 归一后上限是 8 MiB，表情包比它更严是合理的（表情包不该是相册）。
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# 配表情积极度（v0.14.0，主人拍板"档位而不是裸概率"）：三档，改的是**她有多想发**
# （存在感注入里的许可强度），不改发送层的任何闸。
EAGERNESS_LEVELS: tuple[str, ...] = ("reserved", "natural", "eager")
EAGERNESS_DEFAULT = "natural"

# 存在感注入的节奏档（v0.16.0）：由"用户开了新一轮"驱动，不再按挂钟打点。
# every_user_message = 每轮都注；interval_n = 攒够 N 条用户轮注一次（默认）。
# 语义与同门 forever_companion 的 tide.inject_mode 对齐；"关掉"由 awareness.enabled 承担。
INJECT_MODES: tuple[str, ...] = ("every_user_message", "interval_n")
INJECT_MODE_DEFAULT = "interval_n"


def _as_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """枚举读入：非字符串/不在册一律回默认。

    配置里写错档位不该让她的行为变野（也不该崩 startup）——这与 `_as_int`/`_as_number`
    同一纪律：读入层负责把垃圾收敛成合法值，下游永远只看到在册值。
    """
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    return default


def _as_int(value: Any, default: int) -> int:
    """整数读入的总 sanitiser：类型不对/非有限值/巨整数一律回默认。

    这里的每一层分支都是为了让后续 `int()` 转换**不可能抛异常**（from_config
    整条链在 try/except 之外跑，抬起来就是 startup 里冒火）：
    - bool 不是配置里的整数意图（True 不等于 1）；
    - TOML 允许 nan/inf 字面量：int(nan) ValueError、int(inf) OverflowError；
    - 巨整数值自己就是合法 int，直接返回，不经任何 float 中转
      （math.isfinite(10**400) 本身就会 OverflowError，不能拿它拦 int）。
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        # math.trunc 对 float 与 int() 行为完全一致（向零截断）；inf/nan 已由
        # isfinite 在上一行拦下，这里不存在可抛路径（非 try 包裹的 int() 会被
        # 静态门按词法误报）。
        return math.trunc(value)
    return default


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


@dataclass(frozen=True)
class SendSettings:
    """发送链路的行为参数。"""

    # 同一角色卡两次发送之间的最小间隔（秒）。防刷屏，也防模型连着甩图。
    cooldown_sec: float = 20.0
    # Only a host-issued text-reply capability enables ordered tail delivery.
    host_reply_tail: bool = True
    # 默认即时投递工具选图；显式打开才沿用日志驱动的回复后排队。
    defer_tool_sends: bool = False
    # 生成结束后给前端逐段冒字留的兼容缓冲；不是前端/TTS 完成信号。0 = 不额外等待。
    reply_tail_display_buffer_sec: float = 2.0
    # 静态图走内联（image data part）的上限。payload 整条限 512 KiB、
    # base64 膨胀 4/3，这里再留余量给可能的文字 part 与协议封装。
    # 超过这个尺寸的图改走 ctx.images.upload() 换 URL part。
    inline_max_bytes: int = 256 * 1024
    # GIF 和动画 WebP 刻意不走 upload：宿主会把任何输入归一成 JPEG，
    # 动画会被压平。动图只能内联，超过 inline_max_bytes 就如实拒绝。
    animated_via_upload: bool = False
    # 跨轮去重（轮 D①）：同一角色卡最近 N 张成功发过的图在"她自主选图"时不再出现
    # （query 候选剔除 / 显式 id 拦下）。0 = 关闭。数据源是**持久台账**（usage.json），
    # 与冷却（内存表，重启清零）刻意不同："最近发过什么"是事实记忆，不是节奏状态。
    recent_dedup_count: int = 5
    # 概率闸门（轮 D②）：她想发图时按此概率放行，未中则该轮回落纯文字（她可 force 绕行）。
    # 1.0 = 关闭——默认关（与冷却不同，闸门会拒绝她已下的决定，属于行为干预，
    # 主人显式打开才生效）。
    probability: float = 1.0
    # 同一角色卡一次掷骰的复用窗口（秒）：multi_candidates→拿 id 二次定夺是同一次
    # 意愿的延续，不许掷第二次骰（外部系统 p² 教训——判定环节只掷一次并缓存复用）。
    probability_reuse_sec: float = 60.0
    # 配表情积极度（v0.14.0）：只管**她想不想发**（存在感注入里的许可强度），
    # 与 probability（她决定发了之后放不放行）、cooldown（节奏）、去重是四把独立的尺——
    # 别拿档位去动闸门。natural 承接 0.9.10 的实测策略，reserved 保留 0.9.8。
    eagerness: str = EAGERNESS_DEFAULT
    # Eager-only selection after 0..10 unillustrated replies; 0 selects each reply.
    eager_attachment_turns: int = 1


@dataclass(frozen=True)
class StorageSettings:
    """表情库的容量参数。"""

    # 给模型看的目录最多列几条（按最近使用排序）。
    catalog_limit_for_model: int = 80
    # 使用台账保留多少条。
    usage_history_keep: int = 200


@dataclass(frozen=True)
class AwarenessSettings:
    """存在感注入（v0.2.0）的行为参数。

    与总开关的关系是**与**：`[sticker_manager].enabled=false` 时这里全不生效。

    v0.16.0 换了驱动源：注入不再按挂钟打点，而是**由用户开的新一轮触发**
    （`services/turns.py` 轮询 `bus.memory`）。实机 44 轮只覆盖 12 次提醒、
    3 次发表情——瓶颈从来是她没被提醒到，不是提醒得不够客气。
    """

    # 子开关：默认开（总开关才是那道 fail-closed 闸）。
    enabled: bool = True
    # 轮次节奏档：every_user_message（每轮都注）| interval_n（攒够 N 轮注一次）。
    inject_mode: str = INJECT_MODE_DEFAULT
    # interval_n 档下每 N 条用户轮注一次。<1 按 1 收（等价于每轮）。
    # 0 = 跟随档位：矜持固定 0.9.8 基线为 3 轮，自然 / 爱发均为 1 轮。
    # 尺在 `core/eagerness.effective_inject_interval_n`；这不是发图间隔。
    # 主人要自己定死就写个具体数字——这一键的存在就是"能自定义"那半句话的落点。
    inject_interval_n: int = 0
    # 相邻两次注入的最小间隔（秒，0..60）：0 关闭时间限制，轮数门槛仍生效。
    min_interval_sec: float = 60.0
    # v0.20.0 事件门控点名：这句用户话里有情绪反应时，不等轮次攒满就点名一次。
    # 判据是 `core/awareness.emotional_signal` 的本地词表——陷阱 30 不许为这件事去连模型。
    # 轮次节奏由此退居兜底；false = 完全回到"只按轮次计数点名"。
    # 上面那把地板对这一路照样生效，所以它不会变成每句都点名。
    event_gated: bool = True
    # 降级节奏：总线一次都没读通过时（宿主换了桶形状/总线断开）退回按这个挂钟
    # 打点，而不是让整条存在感链路静默死掉。默认一小时 = v0.15.0 之前的老行为。
    interval_sec: float = 3600.0


@dataclass(frozen=True)
class StickerManagerSettings:
    """插件总配置视图。"""

    # fail-closed 总开关：false = 不发图、不给模型目录，但库的增删改仍可用
    # （管理入口碰的是文件系统，不是行为链路）。
    enabled: bool = False
    send: SendSettings = field(default_factory=SendSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    awareness: AwarenessSettings = field(default_factory=AwarenessSettings)

    @classmethod
    def defaults(cls) -> "StickerManagerSettings":
        return cls(
            enabled=False,
            send=SendSettings(),
            storage=StorageSettings(),
            awareness=AwarenessSettings(),
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "StickerManagerSettings":
        section = config.get("sticker_manager") if isinstance(config, dict) else None
        if not isinstance(section, dict):
            return cls.defaults()
        send_raw = section.get("send") if isinstance(section.get("send"), dict) else {}
        storage_raw = section.get("storage") if isinstance(section.get("storage"), dict) else {}
        awareness_raw = section.get("awareness") if isinstance(section.get("awareness"), dict) else {}
        send_default = SendSettings()
        storage_default = StorageSettings()
        awareness_default = AwarenessSettings()
        return cls(
            enabled=_as_bool(section.get("enabled"), False),
            send=SendSettings(
                host_reply_tail=_as_bool(
                    send_raw.get("host_reply_tail"), send_default.host_reply_tail,
                ),
                defer_tool_sends=_as_bool(
                    send_raw.get("defer_tool_sends"), send_default.defer_tool_sends,
                ),
                cooldown_sec=_clamp_float(
                    _as_number(send_raw.get("cooldown_sec"), send_default.cooldown_sec),
                    0.0,
                    3600.0,
                ),
                reply_tail_display_buffer_sec=_clamp_float(
                    _as_number(
                        send_raw.get("reply_tail_display_buffer_sec"),
                        send_default.reply_tail_display_buffer_sec,
                    ),
                    0.0,
                    30.0,
                ),
                inline_max_bytes=_clamp_int(
                    _as_int(send_raw.get("inline_max_bytes"), send_default.inline_max_bytes),
                    16 * 1024,
                    360 * 1024,
                ),
                animated_via_upload=_as_bool(send_raw.get("animated_via_upload"), send_default.animated_via_upload),
                recent_dedup_count=_clamp_int(
                    _as_int(
                        send_raw.get("recent_dedup_count"),
                        send_default.recent_dedup_count,
                    ),
                    0,
                    50,
                ),
                probability=_clamp_float(
                    _as_number(send_raw.get("probability"), send_default.probability),
                    0.0,
                    1.0,
                ),
                probability_reuse_sec=_clamp_float(
                    _as_number(
                        send_raw.get("probability_reuse_sec"),
                        send_default.probability_reuse_sec,
                    ),
                    5.0,
                    3600.0,
                ),
                eagerness=_as_choice(
                    send_raw.get("eagerness"), EAGERNESS_LEVELS, send_default.eagerness
                ),
                eager_attachment_turns=_clamp_int(
                    _as_int(send_raw.get("eager_attachment_turns"), send_default.eager_attachment_turns),
                    0,
                    10,
                ),
            ),
            storage=StorageSettings(
                catalog_limit_for_model=_clamp_int(
                    _as_int(
                        storage_raw.get("catalog_limit_for_model"),
                        storage_default.catalog_limit_for_model,
                    ),
                    5,
                    500,
                ),
                usage_history_keep=_clamp_int(
                    _as_int(
                        storage_raw.get("usage_history_keep"),
                        storage_default.usage_history_keep,
                    ),
                    20,
                    2000,
                ),
            ),
            awareness=AwarenessSettings(
                enabled=_as_bool(awareness_raw.get("enabled"), awareness_default.enabled),
                inject_mode=_as_choice(
                    awareness_raw.get("inject_mode"),
                    INJECT_MODES,
                    awareness_default.inject_mode,
                ),
                # 0 = 跟随积极度档位（v0.19.0）；写正数就是主人自己定死的节奏。
                inject_interval_n=_clamp_int(
                    _as_int(
                        awareness_raw.get("inject_interval_n"),
                        awareness_default.inject_interval_n,
                    ),
                    0,
                    50,
                ),
                # 面板与配置共享 0..60 秒范围；0 关闭时间限制。
                min_interval_sec=_clamp_float(
                    _as_number(
                        awareness_raw.get("min_interval_sec"),
                        awareness_default.min_interval_sec,
                    ),
                    0.0,
                    60.0,
                ),
                # 非布尔一律回默认（与 enabled 同一把 `_as_bool` 尺）。
                event_gated=_as_bool(
                    awareness_raw.get("event_gated"), awareness_default.event_gated
                ),
                interval_sec=_clamp_float(
                    _as_number(awareness_raw.get("interval_sec"), awareness_default.interval_sec),
                    60.0,
                    86400.0,
                ),
            ),
        )


def _as_number(value: Any, default: float) -> float:
    """浮点读入的总 sanitiser：同 _as_int，但 int 分支刻意不做转换。

    PEP 484 的数值塔里 int 就是合法的 float 取值；下游 _clamp_float 只做比较，
    巨整数在整数域会被精确钳住（min(3600.0, 10**400) = 3600.0）——而
    float(10**400) 和 isfinite(10**400) 都会当场 OverflowError。
    不转换 = 不存在会抛的转换。
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return default


def _clamp_int(value: float | int, low: float | int, high: float | int) -> Any:
    return max(low, min(high, value))


def _clamp_float(value: float, low: float, high: float) -> float:
    """浮点钳位。调用方只准喂 _as_number 出来的有限 float——max/min 在实数域
    上不抬异常，这里刻意不再包 float()：多余的转换只会多一个可被质疑的调用点。"""
    return max(low, min(high, value))
