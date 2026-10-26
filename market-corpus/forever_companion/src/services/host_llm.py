"""潮汐时刻 —— 宿主 LLM 管线复用（进程内走宿主自己的模型配置与客户端）。

要发自定义 prompt 的三个通道（碎片提取 / 我的日记成文 / 选了非默认槽位的语气分析）
历史上只有一条路：读宿主 core_config.json 自己拼 {model, api_key, base_url}，再用
stdlib urllib 直连（tone_slot.py）。这条路在"用户没在宿主里配付费服务商"时必然失败——
宿主免费路由的端点/模型名/key 来自 profile 默认值而非存盘字段，插件读盘永远看不见，
于是功能休眠，用户被迫额外配一套 API 才开箱。

本模块接管其中两条（碎片提取 / 我的日记成文）。语气分析不走这里：它的默认态（槽位
留空或 emotion）本来就短路到宿主 /api/emotion/analysis、开箱可用，没有要修的休眠问题，
选了别的槽位则仍留在直连通道上（见 README「模型通道」的边界说明）。

宿主自己的内置插件从不读盘拼端点：`plugin/plugins/qq_auto_reply` 直接
`get_config_manager().get_model_api_config(...)` + `create_chat_llm_async(...)` 跑
自定义 prompt。插件子进程的 sys.path 含宿主仓库根（宿主 plugin/core/host.py 注入），
所以本模块能用同一对入口。槽位解析、地域改写、免费路由端点与模型名、`neko/<版本>`
User-Agent、provider 协议判定全部留在宿主侧，插件不再复刻一遍它的解析逻辑。

零依赖与不抛异常是硬约束：两个入口都是调用点延迟 import（同 mixins/host_coord.py 的
`from config import MAIN_SERVER_PORT`）。宿主模块不可用（独立仓库跑测试、宿主改版
挪走内部模块）时一律返回 None，由调用方回落 tone_slot 的直连通道——本模块永远不是
功能能否工作的前提，只是"让它默认就能用"的那一层。
"""

from __future__ import annotations

from typing import Any

from ..core.state import _CHANNEL_TRANSPORT_HOST, _TONE_SLOT_PREFIXES
from .tone_slot import _exc_shape, _is_request_timeout

JsonObject = dict[str, Any]


def _warn(logger: Any, message: str) -> None:
    """唯一日志出口：logger 未注入（独立环境/测试桩）时静默，绝不反噬感知链路。"""
    if logger is None:
        return
    try:
        logger.warning(message)
    except Exception:  # noqa: BLE001 - 打日志失败不能把调用方带下水
        pass


async def resolve_host_slot(slot: str, logger: Any = None) -> JsonObject | None:
    """宿主某文本槽位当前生效的可调用配置；拿不到 → None（调用方回落直连）。

    返回值与 tone_slot 直连解析同形（model/api_key/base_url），多带
    provider_type 与 transport 标签供 chat_via_host 分派。只开放插件用得上的
    文本槽位（_TONE_SLOT_PREFIXES）：realtime/tts/image 是宿主音频栈的
    进程级单一身份约束槽，game_* 只是 conversation/summary 的委派，都不给。
    """
    if slot not in _TONE_SLOT_PREFIXES:
        return None
    try:
        from utils.config_manager import get_config_manager
    except Exception:  # noqa: BLE001 - 宿主模块不可用＝本模块整体不适用，静默降级
        return None
    try:
        cm = get_config_manager()
        # 免费路由 URL 按地域改写（lanlan.tech ↔ lanlan.app），而区域缓存是进程局部
        # 的：冷启动的插件子进程可能在宿主探测落定前读到 CN 入口。内置插件同样先等
        # 这一把（qq_auto_reply/reply_context_node.py）。代价说清楚：区域已定时它
        # 立即返回；免费路由且探测正在跑时它最多 join 1.5 秒（一次性，落定后恒真），
        # 调用方的 5 秒槽位缓存把它压在每槽每 5 秒至多一次。
        await cm.aensure_region_resolved()
        api_cfg = await cm.aget_model_api_config(slot)
    except Exception as exc:  # noqa: BLE001 - 配置解析失败回落直连，但要留痕
        _warn(logger, f"resolve host slot {slot} failed: {_exc_shape(exc)}")
        return None
    if not isinstance(api_cfg, dict):
        return None
    model = str(api_cfg.get("model") or "").strip()
    base_url = str(api_cfg.get("base_url") or "").strip()
    if not model or not base_url:
        return None
    return {
        "model": model,
        "api_key": str(api_cfg.get("api_key") or "").strip(),
        "base_url": base_url,
        "provider_type": api_cfg.get("provider_type"),
        "transport": _CHANNEL_TRANSPORT_HOST,
    }


async def chat_via_host(
    resolved: JsonObject,
    prompt: str,
    *,
    timeout_sec: float,
    max_completion_tokens: int,
    call_type: str = "",
    logger: Any = None,
    raise_timeout: bool = False,
) -> str | None:
    """用宿主客户端跑一次单轮补全；默认失败 → None（调用方按降级处理）。

    与直连的分工：本函数只在 resolve_host_slot 给出端点之后才被调用，端点与
    客户端身份都由宿主决定，所以这里的"失败"只可能是请求层面的（超时/上游
    5xx/空回复）——回落直连没有意义（同一个端点，插件那份 urllib 恰恰缺宿主
    身份），返回 None 记一次失败即可。
    失败日志沿用 tone_slot 的脱敏契约：只记异常类型/状态码，绝不记 str(exc)
    （自定义端点可能把凭据拼进 URL）。
    手动写作传 raise_timeout=True 时，明确超时改抛脱敏 TimeoutError；取消仍向上传播。
    """
    try:
        from utils.llm_client import create_chat_llm_async
    except Exception:  # noqa: BLE001 - 宿主客户端不可用，静默降级
        return None
    llm = None
    content: Any = None
    try:
        llm = await create_chat_llm_async(
            resolved.get("model") or "",
            resolved.get("base_url") or "",
            resolved.get("api_key") or "",
            provider_type=resolved.get("provider_type"),
            timeout=timeout_sec,
            max_retries=1,
            max_completion_tokens=max_completion_tokens,
        )
        _set_host_call_type(call_type)
        resp = await llm.ainvoke([{"role": "user", "content": prompt}])
        content = getattr(resp, "content", None)
    except Exception as exc:  # noqa: BLE001 - 降级但留痕
        _warn(logger, f"host pipeline chat failed: {_exc_shape(exc)}")
        if raise_timeout and _is_request_timeout(exc):
            raise TimeoutError() from None
        return None
    finally:
        if llm is not None:
            try:
                await llm.aclose()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响本次结果
                _warn(logger, f"host pipeline client close failed: {_exc_shape(exc)}")
    if isinstance(content, str) and content.strip():
        return content
    _warn(logger, "host pipeline returned no usable content")
    return None


def _set_host_call_type(call_type: str) -> None:
    """向宿主的 token 归集上下文声明这次调用的用途。现状是"写了但没人收"。

    宿主的用量记账挂在 openai 客户端的猴子补丁上（utils/token_tracker/hooks.py），
    而 install_hooks() 只在宿主三个服务进程启动时执行，插件子进程从不安装它——
    所以这一句目前没有消费者：走宿主管线的消耗既不进宿主用量面板、也不占宿主 agent
    日配额。custom 直连同理（那条路本来就在插件进程里），两条传输在记账上没有区别。
    仍然照内置插件的写法声明，是为了宿主哪天把补丁装进子进程、或给出官方 completion
    API 时这里已经是对的。归集不参与功能判定，失败一律忽略。
    """
    if not call_type:
        return
    try:
        from utils.token_tracker import set_call_type

        set_call_type(call_type)
    except Exception:  # noqa: BLE001 - 宿主统计模块缺失/改版时照常调用
        pass
