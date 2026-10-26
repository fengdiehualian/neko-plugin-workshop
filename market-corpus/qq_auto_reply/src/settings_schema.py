"""插件可调参数的**单一真相表**。

一个键要在六层里各写一遍（默认值 / 保存白名单 / 入口 JSON schema / dashboard 签名 /
dashboard 转发 / 钳制），全靠 ``tests/test_qq_config_save_keys.py`` 那几条看门狗盯着。
漏一层就是**静默失效** —— 界面上改得动、存得下、实际没人读。历史上踩过两次：
回复缓冲的两个开关（界面能点，值从没存进去过）和 ``retroactive_review_max_reply``
（能存能校验，逻辑里硬编码了"1-2 条"）。

本模块把每个键的**事实**集中在这里，并生成那六层里能量产的四层：

- ``default_config()`` 的默认值
- ``_CONFIG_SAVE_KEYS`` 白名单（仅 ``saveable``）
- 入口 ``input_schema`` 的 properties（仅 ``saveable``）
- dashboard 快照的逐键 cast 与默认值

**不生成** ``_save_settings_locked``：那里的键有一半带着不可归约的联动逻辑
（切连接模式要重置地址、两个概率键越界要抛异常而不是钳制、五个 consent 键要走
延迟发布、策略模式要联动强制开注意力），硬套通用循环只会把语义压平。所以那些键在
表里标 ``handler``，逻辑留在 ``settings_service``；``handler`` 为 ``None`` 的走通用路径。

新增一个键的标准动作：在 ``SETTINGS`` 里加一条，然后按 ``handler`` 决定要不要在
``settings_service`` 里写具名块，最后在业务代码里**真的读它** —— 后者由
``tests/test_qq_settings_schema.py`` 强制。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UIInput:
    """UI 输入框的元数据。``id`` 是 ``static/napcat.html`` 里的元素 id。

    目前只被测试用来断言"前端的 min/max 与后端钳制一致" —— 这两处过去是手工镜像
    （``settings_service`` 里那句"与前端 max=10 对齐"就是证据），会静默漂移。
    """

    id: str
    kind: str = "number"          # "number" | "checkbox" | "text" | "textarea" | "select"
    min: float | None = None
    max: float | None = None
    step: float | None = None
    label: str = ""               # i18n 键
    hint: str = ""                # i18n 键（.hint）


@dataclass(frozen=True)
class SettingSpec:
    """一个可调键的全部事实。"""

    key: str
    kind: str                      # int | float | bool | str | list | dict
    #: 默认值。可变类型（list/dict）在生成时会 deepcopy，调用方拿到的是独立对象。
    default: Any
    #: 是否进保存白名单与参数页。False = 只认配置文件（不进 UI 的细调项）。
    saveable: bool = False
    floor: float | None = None
    ceiling: float | None = None
    #: 上限取自另一个键的**当前值**（如发送线 ≤ 焦点线）。见 settings_service 的具名块。
    ceiling_key: str | None = None
    enum: tuple[str, ...] | None = None
    json_type: str | None = None   # 默认按 kind 推
    description: str = ""          # 入口 input_schema 的描述
    ui: UIInput | None = None
    #: 非 None = 在 settings_service 里有具名处理块（带联动/抛错/延迟发布），不走通用路径。
    handler: str | None = None
    #: 历史别名。保存时接受这些名字，落到 canonical key 上。
    aliases: tuple[str, ...] = ()
    #: 只读透传：既不归一也不改名（存量迁移源，见 config_store.load 的注释）。
    passthrough: bool = False
    #: 不写进 default_config()（历史遗留：靠 .get(key, "") 读）。
    not_in_defaults: bool = False


#: 情绪 → 注意力升/降速率倍率。原先硬编码在 ``attention_service._EMOTION_MULTIPLIER``。
#: 注意 ``attention_service`` 另有两个**按名字**匹配的集合（抢焦点 `arguing`/`proud`、
#: 让焦点 `sulking`/`embarrassed`/`bored`），它们不随这张表配置化 —— 用户往表里加新情绪时，
#: 新情绪不会自动获得抢/让焦点的行为。
#: 这张表与 ``attention_service._EMOTION_MULTIPLIER`` 必须逐键一致，由
#: ``tests/test_qq_emotion_vocabulary.py`` 强制。
DEFAULT_EMOTION_MULTIPLIERS: dict[str, float] = {
    "arguing": 1.2,      # 上头死磕，涨得快跌得慢
    "proud": 0.8,        # 赢了要炫耀，猛拉注意力
    "annoyed": 0.5,      # 不爽，比正常更专注
    "playful": 0.3,      # 玩闹中，微微挂住
    "curious": 0.2,      # 被勾起兴趣
    "calm": 0.0,         # 正常
    "sad": -0.4,         # 难过，不太想说话
    "embarrassed": -0.6,  # 尴尬想溜
    "bored": -0.7,       # 没兴趣的话题，主动掉注意力去找别的群
    "sulking": -0.9,     # 赌气——基本清零，主动让出焦点
}

#: 默认的关键词标签表。原先硬编码在 ``config_store.default_backlog_labels``。
DEFAULT_BACKLOG_LABELS: list[dict[str, Any]] = [
    {
        "id": "mention",
        "label": "点名",
        "keywords": [r"@全体成员"],
        "priority": 60,
    },
]

#: 注意力参数，按周期模型分组。
ATTENTION = (
    SettingSpec("attention_max_score", "float", 10.0, saveable=True,
                floor=1.0, ceiling=10.0, description="save：注意力分数上限"),
    # 下调焦点线时会把发送线一起收紧（见 settings_service），避免焦点群夺冠后
    # 被门控第 5 步一直拒之门外。
    SettingSpec("attention_focus_threshold", "float", 4.0, saveable=True,
                floor=0.1, description="save：焦点线（赢得焦点的资格线）",
                handler="attention_focus_threshold",
                ui=UIInput("cfg-att-focus-threshold", min=1, max=10, step=0.5,
                           label="ui.attention.focus_threshold", hint="ui.attention.focus_threshold.hint")),
    # 焦点群的「保持线」：低于焦点线、高于最低线。焦点线是夺冠资格线；发送门控若也用
    # 焦点线，焦点群回一条就跌破线被门控（见 attention_gate_service 门控第 5 步）。
    SettingSpec("attention_focus_hold_threshold", "float", 2.0, saveable=True,
                floor=0.0, ceiling_key="attention_focus_threshold",
                description="save：焦点发送门控线",
                handler="attention_focus_send_threshold",
                ui=UIInput("cfg-att-focus-send", min=0, max=10, step=0.5,
                           label="ui.attention.focus_send_threshold",
                           hint="ui.attention.focus_send_threshold.hint")),
    SettingSpec("attention_min_threshold", "float", 1.0, saveable=True,
                floor=0.0, description="save：最低阈值（全群低于此线 = 全局休眠）"),
    SettingSpec("attention_batch_message_gain", "float", 0.25, saveable=True,
                floor=0.0, description="save：批量消息计数时每条消息的注意力增益"),
    SettingSpec("attention_base_rise_rate", "float", 0.02, saveable=True,
                floor=0.0, description="save：热聊中每秒自然增长（0=禁用自然上升）"),
    SettingSpec("attention_message_boost", "float", 0.15, saveable=True,
                floor=0.0, description="save：单条消息的注意力加成"),
    SettingSpec("attention_keyword_boost_ratio", "float", 1.8, saveable=True,
                floor=0.0, description="save：关键词命中时的额外加成倍率"),
    # 2026-09-29 跨群取舍删除后，rise/fall 相位 + 蜜月 + 让位整套没了：
    # 热度只由「这个群最后一条消息离现在多远」决定（见 attention_service._heat_tier）。
    # `attention_honeymoon_seconds` / `attention_fall_seconds` /
    # `attention_fall_boost_attenuation` 三键退役（进 `_LEGACY_ZOMBIE_KEYS`）。
    SettingSpec("attention_heat_warm_gap_seconds", "int", 120, saveable=True,
                floor=0,
                description="save：本群静默多少秒算「凉下来了」（0=永不算凉）"),
    SettingSpec("attention_fall_rate", "float", 0.015, saveable=True,
                floor=0.0, description="save：凉下来之后每秒回落量"),
    # 每次回复消耗的注意力比例。语义是 `max_score × 比例` 的**绝对量**（减性、与当前
    # 分数解耦，见 `attention_service.update_on_reply`）。
    # 2026-09-29 使用者口径「满 10 的注意力发 6 条就踩线了」→ 0.10 降到 **0.05**：
    # 满格时 12 条才掉到焦点线、16 条才掉到「本群在聊的线」（旧值 6 条 / 8 条）。
    SettingSpec("attention_consume_ratio", "float", 0.05, saveable=True,
                floor=0.0, ceiling=1.0,
                description="save：每次回复消耗的注意力比例（满格 × 比例 = 每条扣多少分）"),
)

#: 本轮从硬编码提上来的注意力项。
ATTENTION_NEW = (
    # 锁：`@猫娘` / 唤醒词触发，期内该群独占焦点。
    # 与分数是两个独立信号——分数表达「没人叫我时我自己看哪」，锁表达
    # 「有人点名，我必须回头应对」。见 docs/attention-redesign-draft.md §2/§3。
    SettingSpec("attention_lock_seconds", "int", 90, saveable=True,
                floor=0,
                description="save：被 @/唤醒词后锁定该群专注的秒数（0=不锁，回到纯分数仲裁）",
                ui=UIInput("cfg-att-lock-seconds", min=0, max=1800, step=10,
                           label="ui.attention.lock_seconds",
                           hint="ui.attention.lock_seconds.hint")),
    SettingSpec("attention_at_bot_boost", "float", 3.0, saveable=True,
                floor=0.0, description="save：被 @ 时消息加成的倍率"),
    SettingSpec("attention_question_boost", "float", 1.5, saveable=True,
                floor=0.0, description="save：消息是提问时的加成倍率"),
    SettingSpec("attention_wake_boost_ratio", "float", 0.75, saveable=True,
                floor=0.0, ceiling=1.0,
                description="save：唤醒时把分数垫到「焦点线 × 此比例」"),
    SettingSpec("attention_decay_interval_seconds", "float", 5.0, saveable=True,
                floor=0.1, description="save：注意力衰减循环的 tick 间隔（秒）"),
    # ── 发言频率 → 自然增速 ──
    # 让 rise 相位的自然增长按**本群的消息节奏**缩放：热群涨得快、冷群几乎不涨。
    # 判据用「距上一条消息的间隔」（attention_service._frequency_scale），不引入
    # 滑动窗口或计数器 —— 无状态、可复现、也不需要额外的老化机制。
    #
    # 注意 ``_advance_phase`` 在 ``update_on_message`` 写新的 ``last_message_at``
    # **之前**执行，所以间隔天然就是「距上一条」而不是「距这一条」。
    #
    # 下限不为 0：冷群应当涨得慢，但不该完全冻结 —— 完全冻结会让一个曾夺冠、
    # 如今沉寂的群永远卡在 fall 里出不来（fall 相位的消息加成已被
    # ``attention_fall_boost_attenuation`` 压到 0.3，那里没有第二个回血来源）。
    SettingSpec("attention_frequency_target_gap", "float", 30.0, saveable=True,
                floor=1.0,
                description="save：发言频率的目标间隔（秒）——恰好这个节奏时增速为基准 1.0×",
                ui=UIInput("cfg-att-freq-target-gap", min=1, max=600, step=1,
                           label="ui.attention.freq_target_gap",
                           hint="ui.attention.freq_target_gap.hint")),
    SettingSpec("attention_frequency_min_multiplier", "float", 0.15, saveable=True,
                floor=0.0, ceiling=1.0,
                description="save：冷群的增速下限倍率（消息间隔远大于目标间隔时逼近它）",
                ui=UIInput("cfg-att-freq-min-mult", min=0, max=1, step=0.05,
                           label="ui.attention.freq_min_multiplier",
                           hint="ui.attention.freq_min_multiplier.hint")),
    SettingSpec("attention_frequency_max_multiplier", "float", 1.8, saveable=True,
                floor=1.0,
                description="save：热群的增速上限倍率（消息间隔远小于目标间隔时封顶到这里）",
                ui=UIInput("cfg-att-freq-max-mult", min=1, max=20, step=0.5,
                           label="ui.attention.freq_max_multiplier",
                           hint="ui.attention.freq_max_multiplier.hint")),
    # 9 项情绪 → 倍率表。做成 JSON 文本框而不是 9 个数字框：后者要在六层里各写九遍。
    SettingSpec("attention_emotion_multipliers", "dict", DEFAULT_EMOTION_MULTIPLIERS, saveable=True,
                description="save：情绪 → 升/降速率倍率表（JSON 对象）",
                handler="emotion_multipliers",
                ui=UIInput("cfg-att-emotion-multipliers", kind="textarea",
                           label="ui.attention.emotion_multipliers",
                           hint="ui.attention.emotion_multipliers.hint")),
    # ── 接话反馈闭环（ROUND1 方案 B5）──
    # 「她说完之后群里有没有人接」是**唯一**能把她的发言与群的反应连起来的信号：
    # 没人接 → 这个群/这个话题不值得继续占注意力；接起来了 → 值得多待一会儿。
    # 前置参考：Heartflow 会把这两句注入提示词（「上次回复后群里进行了热烈讨论」/
    # 「上次回复后无人接话」），MaiBot 的 retro review 是同一类做法的工具化版本。
    #
    # 为什么不进面板：D 档已经定了「面板只留 7 个用户能自己推理的行为旋钮」
    # （tests/test_qq_attention_panel_surface.py 钉住）。这五个是**内部量级**，
    # 按老规矩留在 schema 与配置文件里。
    SettingSpec("attention_feedback_enabled", "bool", True, saveable=True,
                description="save：接话反馈开关（她上次回复后群里有没有人接 → 影响注意力）"),
    SettingSpec("attention_feedback_window_seconds", "float", 90.0, saveable=True,
                floor=0.0,
                description="save：她发言后等多少秒才下结论（太短会把「还没打完字」误判成没人接）"),
    SettingSpec("attention_feedback_silent_penalty", "float", 0.4, saveable=True,
                floor=0.0, description="save：没人接话时扣多少注意力（回落加速）"),
    SettingSpec("attention_feedback_warm_bonus", "float", 0.4, saveable=True,
                floor=0.0, description="save：群友接起来时加多少注意力"),
    SettingSpec("attention_feedback_warm_count", "int", 3, saveable=True,
                floor=1, description="save：她发言后几条回应算「聊起来了」"),
)

#: 回复节奏与频率闸。缓冲只暴露标量 —— "按条数分桶"的那张等待表保持硬编码。
#:
#: 发送延迟的出厂值刻意压得很低（群聊中心 1.0s）：**生成本身已经花掉 2–3 秒，那就是
#: 拟人的停顿了**，再叠 6–10 秒等于重复计费，用户感觉是「猫娘反应很慢」。原先那套
#: 6±3 秒是按「生成很快、需要补一个停顿」设计的，前提不成立。
#:
#: 注意 ``reply_buffer_service`` 里还有一份同名类常量（DEFAULT_WAIT_SECONDS=6.0 等），
#: 那是**传了 settings 为空的调用方**（主要是测试）的最后兜底，生产路径永远走本表；
#: 两处不一致是有意的 —— 改类常量会碰掉 test_group_waits_longer_than_private 钉的
#: 「群聊明显等得比私聊久」那条设计不变量。
PACING = (
    SettingSpec("reply_burst_window_seconds", "int", 60, saveable=True,
                floor=1, description="save：频率软提示的统计窗口（秒）",
                ui=UIInput("cfg-burst-window", min=1, max=3600, step=1,
                           label="ui.pacing.burst_window", hint="ui.pacing.burst_window.hint")),
    # ⚠️ 这两个键的名字里还带着 "burst"，但**硬闸已删除**（2026-09-27 使用者口径：
    #    「不要这个，有注意力控制频率了」）。它们现在是 `pacing_hint` 的**参考频率**：
    #    窗口内她说得比这个密，就会在提示词里收到一句"你先少说两句"——不拦任何消息。
    #    名字不改：改名要走别名迁移，而键名对使用者不可见（界面上是中文标签）。
    SettingSpec("reply_burst_max_replies", "int", 3, saveable=True,
                floor=1, description="save：软提示的参考条数（说得比这密就提醒她收敛）",
                ui=UIInput("cfg-burst-max", min=1, max=100, step=1,
                           label="ui.pacing.burst_max", hint="ui.pacing.burst_max.hint")),
    # 发送停顿：**生成完成之后**、真正发出去之前的一小块停顿，只为了让回复别
    # 看起来是秒回的。总时长不靠它 —— 那由下面的收集窗口把控（见
    # reply_buffer_service._send_at 的下限语义），所以它常常"不参与决定"。
    #
    # 原本这里有四个旋钮（均值 / 标准差 / 下限 / 上限，按正态分布取样并夹尾巴）。
    # 改成下限语义后总时长归收集窗口管，正态尾巴与区间夹取都没有意义，四个并成一个。
    #
    # **键名里的 mean 是历史遗留**（原先它是正态分布的中心），现在就是那个停顿本身。
    # 刻意不改名：老配置里已经存了用户调好的值，改名等于把它们静默重置成出厂值。
    SettingSpec("buffer_delay_mean_seconds", "float", 1.0, saveable=True,
                floor=0.0, description="save：群聊发送停顿（秒）—— 生成完成后、发出前的小停顿",
                ui=UIInput("cfg-buffer-mean", min=0, max=60, step=0.5,
                           label="ui.pacing.buffer_mean", hint="ui.pacing.buffer_mean.hint")),
    SettingSpec("buffer_delay_private_seconds", "float", 0.6, saveable=True,
                floor=0.0, description="save：私聊发送停顿（秒）",
                ui=UIInput("cfg-buffer-mean-private", min=0, max=60, step=0.5,
                           label="ui.pacing.buffer_mean_private",
                           hint="ui.pacing.buffer_mean_private.hint")),
    # 收集窗口：从**消息到达**算起至少等这么久才发，是"后续消息并进同一批"的机会窗口。
    # 与上面那组 buffer_delay_* 的区别：那组是**生成完成之后**的发送停顿，这组锚在
    # 到达时刻、是下限（见 reply_buffer_service._send_at）。调小它 = 更难合并。
    SettingSpec("buffer_collect_window_seconds", "float", 3.0, saveable=True,
                floor=0.0,
                description="save：收集窗口（秒）—— 从消息到达算起至少等这么久，给后续消息合并的机会",
                ui=UIInput("cfg-buffer-collect", min=0, max=60, step=0.5,
                           label="ui.pacing.buffer_collect",
                           hint="ui.pacing.buffer_collect.hint")),
    SettingSpec("buffer_collect_window_private_seconds", "float", 1.0, saveable=True,
                floor=0.0, description="save：私聊收集窗口（秒）",
                ui=UIInput("cfg-buffer-collect-private", min=0, max=60, step=0.5,
                           label="ui.pacing.buffer_collect_private",
                           hint="ui.pacing.buffer_collect_private.hint")),
    SettingSpec("buffer_max_count", "int", 17, saveable=True,
                floor=1, description="save：缓冲多少条就强制总结（同时是「我在听」的上界）",
                ui=UIInput("cfg-buffer-max-count", min=1, max=200, step=1,
                           label="ui.pacing.buffer_max_count",
                           hint="ui.pacing.buffer_max_count.hint")),
    # 频率软提示：**硬闸已删除**（2026-09-27 使用者口径：「不要这个，有注意力控制频率了」，
    # 墓碑与三条理由见 `attention_gate_service`）。所以这一对键现在是**频率的唯一机制**：
    # 到参考条数的比例（默认 60%）→ 提醒她少说、说短；超过参考条数 → 换成更强的一档。
    # 窗口与参考条数复用上面的 reply_burst_*（单一真相，不新开一份窗口/上限）。
    SettingSpec("pacing_hint_enabled", "bool", True, saveable=True,
                description="save：频率软提示（说得比参考条数密时，在提示词里提醒她收敛）"),
    SettingSpec("pacing_hint_ratio", "float", 0.6, saveable=True,
                floor=0.0, ceiling=1.0,
                description="save：到参考条数的多少比例开始提醒（参考 3 条 + 0.6 → 第 2 条开始）"),
)

#: 出站守门（内容安全 + 重复过滤）。见 ``outbound_guard_service``。
#:
#: 都是**往回收**的闸，所以默认开、且不进面板（D 档：面板只留 7 个行为旋钮）。
#: 黑名单**复用关键词页里那份词表**（`backlog_labels` 里 priority<0 的条目）——
#: 同一个词表双向生效，用户不用维护第二份名单。
OUTBOUND = (
    SettingSpec("outbound_guard_enabled", "bool", True, saveable=True,
                description="save：出站守门总开关（内容安全 + 重复过滤）"),
    SettingSpec("outbound_blacklist_enabled", "bool", True, saveable=True,
                description="save：出站内容安全（命中黑名单词的文本不发出去）"),
    SettingSpec("outbound_dedup_enabled", "bool", True, saveable=True,
                description="save：出站重复过滤（同一个群窗口内不重发同一句）"),
    SettingSpec("outbound_dedup_window_seconds", "int", 1800, saveable=True,
                floor=0, description="save：重复过滤的窗口（秒）"),
    SettingSpec("outbound_dedup_min_chars", "int", 4, saveable=True,
                floor=1, description="save：短于此长度的文本不参与重复过滤（嗯嗯/？这类应答豁免）"),
)

#: 其余全部键。``saveable`` 决定是否进白名单/参数页；``handler`` 决定是否走通用路径。
MISC = (
    # ── 连接 ──
    SettingSpec("qq_connection_mode", "str", "napcat", saveable=True,
                enum=("napcat", "napcat_forward", "open_platform"),
                description="save：连接方式", handler="qq_connection_mode"),
    SettingSpec("onebot_url", "str", "ws://0.0.0.0:6199", saveable=True,
                description="save：反向 WS 监听地址 / 正向 WS 拨号目标",
                handler="onebot_url"),
    SettingSpec("token", "str", "", saveable=True, description="save：OneBot 鉴权 token",
                handler="token"),
    SettingSpec("napcat_directory", "str", "", saveable=True,
                description="save：NapCat 安装目录", handler="napcat_directory"),
    # 默认**后台**启动：自动化（一键部署 / 开机自启）不该弹一个控制台出来打断用户。
    # 藏了窗口就没有控制台了，所以启动隐藏窗口时会顺手打开 napcat.json 的 fileLog ——
    # 否则 NapCat 的日志哪儿都不会留，见 napcat_onebot_config.ensure_file_log。
    SettingSpec("show_napcat_window", "bool", False, saveable=True,
                description="save：启动 NapCat 时显示控制台窗口",
                ui=UIInput("cfg-show-napcat", kind="checkbox")),
    SettingSpec("local_stt_url", "str", "", saveable=True,
                description="save：本地 STT 地址", not_in_defaults=True),
    # ── QQ 开放平台 ──
    SettingSpec("qq_open_app_id", "str", "", saveable=True,
                description="save：开放平台 AppID", handler="qq_open_app_id"),
    SettingSpec("qq_open_client_secret", "str", "", saveable=True,
                description="save：开放平台密钥", handler="qq_open_client_secret"),
    SettingSpec("qq_open_sandbox_enabled", "bool", False, saveable=True,
                description="save：开放平台走沙箱域名", handler="qq_open_sandbox_enabled"),
    # 采集授权：打开后每条事件都把标识符落进**持久**日志，所以走延迟发布
    # （写盘成功后才对运行时可见），与记忆开关同族。
    SettingSpec("qq_open_identity_probe_enabled", "bool", False, saveable=True,
                description="save：身份作用域取证（往持久日志写标识符）",
                handler="consent_opt_in"),
    # 插件工具桥：``{plugin_id: "all"|"admin"}``。**只有开放平台会用它**（见
    # `plugin_tool_service` 的模块说明）。键 = 允许被猫娘调用的插件，值 = 谁可以用。
    # 只认"已经启动"的插件：没启动的那些挂不上工具，也不会出现在提示词里。
    SettingSpec("qq_open_plugin_tools", "dict", {}, saveable=True,
                description="save：开放平台上允许猫娘调用的插件及其分级（JSON 对象）"),
    # 异步插件任务的**结果回投**：她答应过"结果出来告诉你"时，由后台轮询到结果后
    # 主动回一条到同一个会话（见 `plugin_tool_followup_service`）。使用者 16:3x 要的
    # ——这是插件里唯一一条"她主动说话"的路径，所以要给一个能一键关掉的开关。
    SettingSpec("qq_open_plugin_followup_enabled", "bool", True, saveable=True,
                description="save：开放平台上是否允许她主动把异步插件的结果回投给对方"),
    # ── 名单与账本 ──
    SettingSpec("trusted_users", "list", [], description="可信用户名单",
                ui=UIInput("", kind="text")),
    SettingSpec("trusted_groups", "list", [], description="可信群名单"),
    SettingSpec("speaker_trust_profiles", "dict", {}, passthrough=True,
                description="存量 per-QQ 信赖度账本（只读迁移源，归一/截断等于改写它）"),
    # ── 转发与概率 ──
    # 越界抛 ValueError（不是钳制）—— 有测试钉着。
    # 「这句该不该接」的判定阈值：0 = 关闭这一关（回到「交给 LLM 自判」的老行为）。
    # 刻意**不 saveable、不给 UIInput**：saveable 键有硬契约（必须同时出现在 dashboard
    # 快照与 save_settings 参数里，两条看门狗盯着），而这是调参项不是日常开关 ——
    # 直接改 business_config.json 即可（与那些昼夜节律参数同类）。
    SettingSpec("reply_necessity_threshold", "float", 40.0,
                floor=0.0, ceiling=100.0,
                description="焦点群「这句该不该接」的判定阈值（0=关闭；改配置文件生效）"),
    # 「人对人」惩罚（2026-09-27 先只出数据）。
    # `human_pair_streak` = 连续多少条别人的消息既没 @ 她也没引用她。默认 **0.0 分**
    # —— 分数不动，但 `[Necessity]` 的依据里会出现 `人对人×N`，攒够真机数据再决定扣多少。
    # 学术上这是「谁在跟谁说话」那一维（结构性信号，净收益最大的非指称特征），
    # 而我们此前只有布尔量 `mentions_other_user`。
    SettingSpec("necessity_human_pair_penalty", "float", 0.0,
                floor=0.0, ceiling=100.0,
                description="连续多条「没在跟她说话」时扣多少分（0=只记数据不扣分）"),
    SettingSpec("necessity_human_pair_min_streak", "int", 3,
                floor=1, ceiling=100,
                description="连续多少条「没在跟她说话」才算「这群人在互相聊」"),
    # ── 「谁在跟谁说话」（addressee，见 `addressing.py`）──
    # 与上面两条同类：**调参项不是日常开关**，所以不 saveable、不给 UIInput
    # （saveable 键有硬契约：必须同时出现在 dashboard 快照与 save_settings 参数里，
    # 两条看门狗盯着）。改 `business_config.json` 生效。
    #
    # 减分**默认就生效**（30）—— 这点与 `necessity_human_pair_penalty` 的 0.0 相反，
    # 理由写在 `score_necessity` 的 docstring：人对人 streak 是要攒的结构量，而
    # "这条明确 @ 了别人 / 引用的是别人"是**逐条可判的事实**，拿事实减分不必等数据。
    # 取 30 而不是 40：阈值恰好是 40，罚满就等于硬门控（那是另一个开关），
    # 30 的语义是「要靠内容分或积压压力把这条捞回来」。
    SettingSpec("addressee_penalty", "float", 30.0,
                floor=0.0, ceiling=100.0,
                description="明确在跟别人说话（@别人 / 引用别人）时扣多少分（0=不扣）"),
    # AstrBot 的唤醒判据：群聊里**首段**是 At、但 @ 的不是她也不是全体 → 不唤醒。
    # 默认**关**：真机里"@ 了别人但话是问她的"并不罕见，硬拦会误伤；
    # 默认只走上面的减分，想复刻 AstrBot 就打开这个开关。
    SettingSpec("addressee_ignore_first_at_other", "bool", False,
                description="首段 @ 的是别人时直接不唤醒（默认只减分，不硬拦）"),
    # 群里怎么叫她。本体的名字（`get_character_data()[1]`）**自动生效**，
    # 不需要在这里抄一遍 —— 抄一遍就会随本体改名人设而漂移。这里只补
    # "群里怎么叫她"那部分（猫娘 / 喵喵 / 小名），本体不可能知道。
    SettingSpec("addressee_names", "list", [],
                description="她的别名/昵称（本体人设名字之外的叫法，命中即视为在叫她）"),
    SettingSpec("normal_relay_probability", "float", 0.1, saveable=True,
                floor=0.0, ceiling=1.0, description="save：转发给管理员的概率",
                handler="probability",
                ui=UIInput("cfg-normal-prob", min=0, max=1, step=0.05)),
    # ── 引导 ──
    #: 界面语言偏好。前端 `onLangChange` 一直在以 `action:'save'` 提交它，但它
    #: 不在真源里 → 被入口白名单整键丢弃（`_config_save` 的 payload 因此为空，
    #: 直接返回 Err，而前端 `catch(e){}` 把它吞了）；`settings_service` 里那段
    #: 写盘代码的上游 `dashboard_service.save_settings` 也没有这个参数，所以
    #: **永远不可达**；快照同样不返回它，而前端在 `s.locale` 读它。
    #: 净效果：语言只在浏览器 localStorage 里生效，换浏览器/清缓存就回到默认。
    #: 声明成 saveable 后三条链一次接通（白名单、写盘、快照回显）。
    SettingSpec("locale", "str", "", saveable=True,
                handler="locale",
                description="save：界面语言偏好（服务端只做持久化与回显）"),
    SettingSpec("show_onboarding", "bool", True, saveable=True,
                description="save：是否显示引导"),
    SettingSpec("guide_step_napcat_done", "bool", False, saveable=True,
                description="save：NapCat 步骤是否已完成"),
    SettingSpec("guide_step_config_done", "bool", False, saveable=True,
                description="init / save：配置步骤是否已完成"),
    SettingSpec("guide_step_runtime_done", "bool", False, saveable=True,
                description="save：运行时步骤是否已完成"),
    # ── 运行时 ──
    SettingSpec("max_concurrent_messages", "int", 3, floor=1,
                description="同时在处理的消息数上限"),
    SettingSpec("ai_connect_timeout_seconds", "float", 10.0, floor=0.0),
    SettingSpec("ai_turn_timeout_seconds", "float", 60.0, floor=0.0),
    SettingSpec("handler_shutdown_timeout_seconds", "float", 10.0, floor=0.0),
    SettingSpec("reply_mode", "str", "text", saveable=True,
                enum=("text", "voice", "both"), description="save：回复模式",
                handler="reply_mode", ui=UIInput("", kind="select")),
    # ── 积压 ──
    SettingSpec("backlog_retention_limit", "int", 200, floor=20,
                description="每个会话保留多少条积压消息"),
    SettingSpec("backlog_summary_threshold", "int", 10, floor=1,
                description="已废弃（解析了但从未被使用）"),
    SettingSpec("backlog_notify_cooldown_seconds", "int", 900, floor=60,
                description="积压通知冷却（秒）"),
    SettingSpec("backlog_issue_notify_threshold", "int", 1, floor=1,
                description="多少条非 chat 标签的消息触发通知"),
    SettingSpec("backlog_labels", "list", DEFAULT_BACKLOG_LABELS, saveable=True,
                description="save：关键词/优先级标签表", handler="backlog_labels"),
    # ── 策略 ──
    # 「回复策略」（strategy_mode）**已整个删除**（2026-09-27 使用者要求）。
    # 它在模式合并后只剩 "neko_dynamic" 一个取值，也就是一个界面上永远选不动、
    # 代码里永远恒真的下拉框 —— 与当初删掉的 `enable_group_attention` 同一类假旋钮。
    # 老配置里残留的 `strategy_mode` 键无害：不再被读出，也不会被写回，
    # 下次保存配置时自然消失。见 docs/SESSION-HANDOFF.md §24。
    SettingSpec("neko_dynamic_idle_timeout_seconds", "float", 10.0, floor=0.0,
                description="已废弃（注意力系统下不再使用）"),
    # 曾在这里的 `neko_dynamic_waking_users` / `neko_dynamic_waking_keywords` 已删除
    # （2026-09-27）：它们的描述一直写着「已废弃（改用 attention + backlog_labels）」，
    # 却留在表里当着两个"看起来能调、实际零消费方"的旋钮。昵称/别名那一半能力
    # 现在由 `addressee_names` + `addressing.py` 正式接管。
    # 老配置里的残留值由 `config_store._LEGACY_ZOMBIE_KEYS` 清掉。
    # ── 回溯补回 ──
    SettingSpec("retroactive_review_max_messages", "int", 30, saveable=True,
                floor=1, description="save：回溯最多取多少条被忽略消息",
                ui=UIInput("cfg-retro-max-msgs", min=1, max=500, step=1,
                           label="ui.napcat.config.retro_review_max_msgs")),
    # 此前是死键：提示词里硬编码了"1-2 条 / 不要超过两条"。
    SettingSpec("retroactive_review_max_reply", "int", 5, saveable=True,
                floor=1, description="save：回溯最多补回多少条",
                ui=UIInput("cfg-retro-max-reply", min=1, max=50, step=1,
                           label="ui.napcat.config.retro_review_max_reply")),
    # 回溯补回的**触发**从跨群焦点切换换成"本群凉转热"之后（2026-09-29），这两道闸
    # 必须存在：新触发比老触发频繁得多（任何群从低于保持线回到线上都算一次），
    # 而真去补一次要花一整次 LLM 调用。
    SettingSpec("retroactive_review_min_unreviewed", "int", 5, saveable=True,
                floor=0, description="save：至少攒够多少条没看过的消息才值得回溯补一次（0=不设门槛）",
                ui=UIInput("cfg-retro-min-unreviewed", min=0, max=100, step=1,
                           label="ui.napcat.config.retro_review_min_unreviewed")),
    SettingSpec("retroactive_review_cooldown_seconds", "int", 300, saveable=True,
                floor=0, description="save：同一个群两次回溯补回之间至少隔多少秒（0=不限）",
                ui=UIInput("cfg-retro-cooldown", min=0, max=3600, step=30,
                           label="ui.napcat.config.retro_review_cooldown")),
    # 按群维护循环的 tick 间隔：群记忆摘要按它推（游标幂等，多久推一次只影响及时性）。
    SettingSpec("attention_maintenance_interval_seconds", "float", 60.0, saveable=True,
                floor=5.0, description="save：按群维护循环（群记忆摘要）的间隔秒数",
                ui=UIInput("cfg-att-maintenance-interval", min=5, max=600, step=5,
                           label="ui.attention.maintenance_interval",
                           hint="ui.attention.maintenance_interval.hint")),
    SettingSpec("group_memory_digest_interval_seconds", "int", 300, saveable=True,
                floor=0, description="save：群记忆摘要推送间隔秒数（0=不按时间推）",
                ui=UIInput("cfg-gm-digest-interval", min=0, max=3600, step=30,
                           label="ui.attention.digest_interval",
                           hint="ui.attention.digest_interval.hint")),
    # ── 休眠（「这个群冷漠了」）──────────────────────────────────────
    # 使用者口径（2026-09-29）：「群聊如果冷漠了就休眠，大概就是半个小时没有任何发言」。
    # 睡下的效果 = **这个群只答点名**（@ / 引用她 / 关键词），其余消息这一轮不看不答
    # （但照常计分：分数涨回 `dormancy_wake_score` 就自己醒）；点名即醒。
    # 它与「主动破冰」是两件事，后者已删除（那个才是用时间判「该说话了」）。
    SettingSpec("dormancy_enabled", "bool", True, saveable=True,
                description="save：群冷漠了就休眠（只答点名）",
                ui=UIInput("cfg-dormancy-on", kind="checkbox",
                           label="ui.attention.dormancy_on")),
    SettingSpec("dormancy_idle_seconds", "int", 1800, saveable=True,
                floor=0, description="save：群静默多少秒算冷漠（0=关掉自动休眠）",
                ui=UIInput("cfg-dormancy-idle", min=0, max=86400, step=60,
                           label="ui.attention.dormancy_idle",
                           hint="ui.attention.dormancy_idle.hint")),
    SettingSpec("dormancy_wake_score", "float", 2.0, saveable=True,
                floor=0.0, description="save：群又热闹到多少分就苏醒（默认 2.0 = 在聊的线）",
                ui=UIInput("cfg-dormancy-wake-score", min=0, max=10, step=0.5,
                           label="ui.attention.dormancy_wake_score",
                           hint="ui.attention.dormancy_wake_score.hint")),
    # 这里曾有一个 `dormancy_auto_wake_seconds`（到点自动醒）。2026-09-29 使用者否掉
    # （「到点自动醒也不要」）：脚本内不再有任何「睡够多久就醒」的判据，键进僵尸名单。
    # 唤醒只剩两条 —— 点名，或群里又热闹到 `dormancy_wake_score`。
    # ── 主动破冰：整套删除（2026-09-29 使用者口径「干脆不要这个先」）──
    # 原触发判据是"这个群静默 1800 秒"（`icebreaker_idle_seconds`）—— 使用者明确不要
    # 用时间判断；给他的四个事件驱动备选（她说完没人接 / 有人喊冷场 / 群在聊但不带她 /
    # 话题收尾）他选了"先不要"。随之删除的还有它带出来的休眠
    # （`icebreaker_dormant_enabled` / `icebreaker_dormant_seconds`）与破冰话题表
    # （`proactive_topics`）—— 休眠的唯一触发源就是"她主动开口没人接"。
    # 五个键全部进 `config_store._LEGACY_ZOMBIE_KEYS`。见 docs/SESSION-HANDOFF.md §42。
    # ── 主动搭话：管理员私聊，**有由头**才开口（2026-10-02 使用者：「先做 A」）──
    # A = 她自己在私聊里说过要交代的事（"晚点告诉你"/"明天给你看"/"等我查一下"）到点了，
    # 把**她当时那句话**当由头交回给她，由她决定要不要主动说。判据是**内容**；
    # 时间只负责"到期"和"刹车"（静音时段 / 每日上限 / 最小间隔）。
    # 与上面已删除的主动破冰不是一回事：那套是拿"群静默 1800 秒"当理由。
    # 范围只到管理员私聊；群聊不走这条路。见 `proactive_initiative_service`。
    SettingSpec("proactive_initiative_enabled", "bool", False, saveable=True,
                description="save：允许她在管理员私聊里主动搭话（有由头才开口）"),
    SettingSpec("proactive_initiative_quiet_start_hour", "int", 23, saveable=True,
                floor=0, ceiling=23,
                description="save：主动搭话静音时段的起始小时（含；与结束小时相同=不静音）"),
    SettingSpec("proactive_initiative_quiet_end_hour", "int", 8, saveable=True,
                floor=0, ceiling=23,
                description="save：主动搭话静音时段的结束小时（不含）"),
    SettingSpec("proactive_initiative_max_per_day", "int", 2, saveable=True,
                floor=0, ceiling=12,
                description="save：主动搭话每天最多几次（0=不发）"),
    SettingSpec("proactive_initiative_min_gap_seconds", "int", 10800, saveable=True,
                floor=0, ceiling=86400,
                description="save：两次主动搭话之间至少隔多少秒"),
    # ── 回复缓冲开关 ──
    # 群聊与私聊**各自独立**开关，默认都开（与历史行为一致）。
    # 关掉的那一类不再排队等待，每条消息各自判定并立即投递。
    SettingSpec("group_buffer_enabled", "bool", True, saveable=True,
                description="save：群聊回复缓冲",
                ui=UIInput("cfg-buffer-group", kind="checkbox",
                           label="ui.napcat.config.buffer_group")),
    SettingSpec("private_buffer_enabled", "bool", True, saveable=True,
                description="save：私聊回复缓冲",
                ui=UIInput("cfg-buffer-private", kind="checkbox",
                           label="ui.napcat.config.buffer_private")),
    # 默认**关** —— NapCat 会为注入拉起 QQ（必要时杀掉正在运行的那个），
    # 不该由插件替用户决定。
    SettingSpec("auto_start_on_launch", "bool", False, saveable=True,
                description="save：自启（每次插件启动都拉起 NapCat 并接上自动回复）"),
    # ── 记忆（五个采集授权键走延迟发布）──
    # 群聊长期记忆显式 opt-in。成员记忆会增加按成员分桶的提取调用，
    # 因此独立开关且默认关闭。
    SettingSpec("group_memory_enabled", "bool", False, saveable=True,
                description="save：群聊长期记忆（显式 opt-in）", handler="consent_opt_in"),
    SettingSpec("group_member_memory_enabled", "bool", False, saveable=True,
                description="save：按成员分桶的群记忆（父开关关着时授权无效）",
                handler="consent_opt_in"),
    # 非管理员私聊的 participant 记忆：以对方为主体单独建档（participant scope），
    # 绝不进管理员的 legacy 私聊语料。同为显式 opt-in，默认关闭。
    SettingSpec("private_participant_memory_enabled", "bool", False, saveable=True,
                description="save：非管理员私聊的 participant 记忆",
                handler="consent_opt_in"),
    # 跨群实时话题不是长期记忆的一部分，默认严格隔离。
    SettingSpec("allow_cross_group_context", "bool", False, saveable=True,
                description="save：允许跨群实时话题上下文", handler="consent_opt_in"),
    # ── 提示词覆盖 ──
    # 提示词编辑器覆盖值（locale → layer_id → text）
    SettingSpec("prompt_overrides", "dict", {},
                description="提示词编辑器覆盖值（locale → layer_id → text）"),
    SettingSpec("group_prompts", "dict", {}, description="按群自定义提示词"),
)

SETTINGS: tuple[SettingSpec, ...] = ATTENTION + ATTENTION_NEW + PACING + OUTBOUND + MISC

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}

#: 白名单：``__init__.py`` 的 ``_CONFIG_SAVE_KEYS`` 由此生成（含别名）。
SAVEABLE_KEYS: frozenset[str] = frozenset(
    name for spec in SETTINGS if spec.saveable for name in (spec.key, *spec.aliases)
)

#: 有具名处理块的键 → 处理块名。用于看门狗断言"标了 handler 就真的有人管"。
HANDLED_KEYS: frozenset[str] = frozenset(
    name for spec in SETTINGS if spec.handler for name in (spec.key, *spec.aliases)
)


def _json_type(spec: SettingSpec) -> str:
    if spec.json_type:
        return spec.json_type
    return {
        "int": "integer", "float": "number", "bool": "boolean",
        "str": "string", "list": "array", "dict": "object",
    }[spec.kind]


def input_schema_properties() -> dict[str, dict[str, Any]]:
    """入口 ``config`` 的 JSON schema properties（仅 saveable，含历史别名）。

    **别名必须一起生成**：入口 schema 带 ``additionalProperties: False``，而前端发的
    是别名。漏掉别名不是"少一条文档"，是那次保存被整个拒掉。
    """
    props: dict[str, dict[str, Any]] = {}
    for spec in SETTINGS:
        if not spec.saveable:
            continue
        entry: dict[str, Any] = {"type": _json_type(spec)}
        if spec.enum:
            entry["enum"] = list(spec.enum)
        if spec.description:
            entry["description"] = spec.description
        props[spec.key] = entry
        for alias in spec.aliases:
            alias_entry = dict(entry)
            if spec.description:
                alias_entry["description"] = f"{spec.description}（历史别名，落到 {spec.key}）"
            props[alias] = alias_entry
    return props


def defaults() -> dict[str, Any]:
    """``default_config()`` 的默认值部分。可变类型每调用一次给一份新的。

    ``not_in_defaults`` 的键被排除：它们历史上就不在 ``default_config()`` 里
    （靠 ``.get(key, "")`` 读），凭空补一条会改动默认配置的形状。表里仍留着它们的
    声明，好让"表覆盖全部键"这条断言成立。
    """
    import copy

    return {
        spec.key: copy.deepcopy(spec.default)
        for spec in SETTINGS
        if not spec.not_in_defaults
    }


def snapshot_value(spec: SettingSpec, settings: dict[str, Any]) -> Any:
    """dashboard 快照里一个键的取值：按 kind 选 cast，缺值时退回默认。

    缺值路径**返回副本**：`attention_emotion_multipliers` 之类的默认值是模块级可变
    对象，按引用交出去等于把表本身暴露给调用方改。
    """
    import copy

    raw = settings.get(spec.key)
    if raw is None:
        fallback = spec.default
        if fallback is None:
            # 可变类型没写默认值 —— 交给调用方原样透传。
            return raw
        raw = copy.deepcopy(fallback)
    if spec.kind == "bool":
        return bool(raw)
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "float":
        return float(raw)
    return raw


__all__ = [
    "ATTENTION",
    "ATTENTION_NEW",
    "BY_KEY",
    "DEFAULT_EMOTION_MULTIPLIERS",
    "HANDLED_KEYS",
    "MISC",
    "PACING",
    "SAVEABLE_KEYS",
    "SETTINGS",
    "SettingSpec",
    "UIInput",
    "defaults",
    "input_schema_properties",
    "snapshot_value",
]
