"""永远的陪伴 —— 语气感知（emotion_sense）服务：回复完成后的互动情绪分析。

A5 服务化：第 1 批迁入无 HTTP 的纯判定方法（配置读取 / 开关 / 生效阈值），
第 2 批迁入 HTTP 分析通道（CSRF token / 宿主情感槽分析 / core_config 加载 /
槽位直连），第 3 批迁入主链路（recent.json 水位 diff / 门控主入口 /
筛选与校正判定及提醒推送），均从主类 __init__.py 纯移动而来，逻辑一字不改；
主类保留同名薄委托，既有调用路径（含 tests 对私有方法的深耦合）全部不变。

三条硬约束（违反则测试红）：
1. 实例级 monkeypatch 兼容：测试会 p._proactive_http = ... 整体替换主类
   方法，主类能力一律经"调用时现取"的延迟解析回调注入，禁止构造期捕获
   BoundMethod 快照；
2. 配置整体替换兼容：测试会 p._emotion_sense_cfg = dict(...) 整体替换，
   主类 _refresh_config 同样整体替换——配置必须经 cfg_getter() 每次现读，
   禁止保存配置 dict 引用（服务构造时主类配置还是 {}）；
3. time/random 猴补丁兼容（同 state.py 头部约束）：测试用
   monkeypatch.setattr(forever_companion.time, "time", ...) 直接改 stdlib 模块
   对象的属性，因此本模块必须 ``import time`` 后调 ``time.time()``——运行时
   沿模块对象取属性，补丁对所有 import 它的模块同生效；不得
   ``from time import time``（定义期绑定函数对象本身，补丁失效）。
   random 同理。

TideConfigError 说明：_effective_tone_threshold 对阶段解析失败的兜底是
宽 ``except Exception``（配置异常时不加偏置），无需引用 TideConfigError，
故本模块不依赖 cycle.py，无循环导入风险。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..core.state import (
    _CORE_CONFIG_CACHE_TTL,
    _POSITIVE_ACTIONS,
    _PROACTIVE_PAUSE_ACTIONS,
    _TONE_COLD_LABELS,
    _TONE_DIRECT_PROMPT,
    _TONE_SCREEN_NUDGE_SEC,
    _TONE_WARM_LABELS,
    _LanlanShard,
)

JsonObject = dict[str, Any]


class EmotionSenseService:
    """语气感知服务：聚合 [emotion_sense] 功能块的判定与分析逻辑。

    构造参数全部是主类能力的延迟解析回调（调用时现取，见模块 docstring
    硬约束）。logger 同样是延迟解析回调：测试桩在插件构造后才注入
    p.logger，构造期取 self.logger 会 AttributeError。

    运行态缓存（_csrf_token / _last_tone_error_logged / _core_config_cache）
    随服务持有；主类仅留 _csrf_token 的 property 代理（tests 直读
    p._csrf_token），另两者无外部引用点、主类不再代理。shard 的
    last_turn_marker / last_recent_* / tone_window / last_tone_analysis_ts /
    last_screen_nudge_ts 等字段保持扁平，由本服务原地读写。
    """

    def __init__(
        self,
        *,
        http: Callable[..., Awaitable[Any]],
        # 宿主回环 origin：与 http 拼 URL 用的是同一个 base。宿主 CSRF 守卫
        # （_validate_local_mutation_request）要求 token ∧ Origin 同时成立，
        # 写死 48911 在自定义 MAIN_SERVER_PORT 下只能靠它的 hostname 降级分支
        # 过关（那是为 Docker 端口映射写的），一旦收紧就 403 静默休眠
        api_base: Callable[[], str],
        push: Callable[..., Any],
        cfg_getter: Callable[[], JsonObject],
        mood_enabled: Callable[..., bool],
        phase_state: Callable[..., Any],
        # save_mood 是主类的"语气感知收尾落盘"回调（签名保持 (lanlan, shard)→
        # awaitable 不变）；1.2.2 批次2 起它除 mood 外还补刷 stats/review 并回传
        # Result——本服务只是 await、按既定语义丢弃返回值，签名允许返回值透传即可
        save_mood: Callable[..., Awaitable[Any]],
        feed_affect: Callable[..., None],
        core_config_loader: Callable[[], JsonObject],
        core_config_path: Callable[[], Path | None],
        resolve_slot: Callable[..., JsonObject | None],
        post_chat: Callable[..., str | None],
        parse_result: Callable[..., tuple[str, float] | None],
        # 以下四个把服务内部跨方法调用回绕到主类薄委托（调用时现取），
        # 保证未来对主类这几个方法做实例级 monkeypatch 后经 _maybe_tone_sense
        # 间接触发也能命中 patch（不经主类则 patch 失效）
        poll_recent: Callable[..., Awaitable[Any]],
        analyze_tone: Callable[..., Awaitable[Any]],
        correction_check: Callable[..., bool],
        screen_check: Callable[..., bool],
        plugin_id: Callable[[], str],
        logger: Callable[[], Any],
        # 能力中心接线（1.2.7）：开关判定经主类 _cap_effective（含按角色否决集）；
        # 未注入时回落旧公式（情绪引擎 ∧ 配置），保留服务单独可测性
        cap_enabled: Callable[..., bool] | None = None,
    ) -> None:
        self._http = http
        self._api_base = api_base
        self._push = push
        self._cfg_getter = cfg_getter
        self._mood_enabled = mood_enabled
        self._phase_state = phase_state
        self._save_mood = save_mood
        self._feed_affect = feed_affect
        self._core_config_loader = core_config_loader
        self._core_config_path = core_config_path
        self._resolve_slot = resolve_slot
        self._post_chat = post_chat
        self._parse_result = parse_result
        self._poll_recent = poll_recent
        self._analyze_tone = analyze_tone
        self._correction_check = correction_check
        self._screen_check = screen_check
        # plugin_id 同为延迟解析回调：与其他回调保持同一纪律，杜绝构造期快照
        self._plugin_id = plugin_id
        self._logger = logger
        self._cap_enabled = cap_enabled
        # 宿主 CSRF token 缓存（GET /health 的 instance_id）；None = 未获取/已失效
        self._csrf_token: str | None = None
        # 分析端点 error 休眠的节流水位：哨兵 -1000.0 而非 0.0——防 time.monotonic()
        # 以 0 为纪元的平台把首条 warning 误判为已记录（同主类 _last_bus_error_logged 先例）
        self._last_tone_error_logged = -1000.0
        # recent 记忆拉取失败 / CSRF 获取失败的节流槽（同哨兵纪律）：此前这两处静默
        # return None，端点 400/不可达时语气感知全断且零日志（实测排障盲区）
        self._last_recent_error_logged = -1000.0
        self._last_csrf_error_logged = -1000.0
        # 宿主 core_config.json 的内存缓存（直连槽位解析用）：(时间戳, 内容)
        self._core_config_cache: tuple[float, JsonObject] = (-1000.0, {})

    # ---- 配置读取（每次经 cfg_getter() 现读，兼容配置整体替换）----

    def _tone_float_cfg(self, key: str, default: float) -> float:
        try:
            return float(self._cfg_getter().get(key, default))
        except (TypeError, ValueError):
            return default

    def _tone_int_cfg(self, key: str, default: int) -> int:
        try:
            return max(1, int(self._cfg_getter().get(key, default)))
        except (TypeError, ValueError):
            return default

    # ---- 开关与生效阈值 ----

    def _emotion_sense_enabled(self, shard: _LanlanShard | None = None) -> bool:
        # 1.2.7 能力中心：tone_sense 闸统一经主类判定（总开关 ∧ 情绪引擎 ∧
        # [emotion_sense].enabled ∧ 用户否决）；无回调时保持旧公式
        if self._cap_enabled is not None:
            return bool(self._cap_enabled("tone_sense", shard=shard))
        return bool(self._mood_enabled(shard) and self._cfg_getter().get("enabled", True))

    def _effective_tone_threshold(self, shard: _LanlanShard) -> float:
        """筛选模式的置信度阈值 × 阶段灵敏度：潮汐期/回升期/活跃期阈值下移（更激进）。

        这是"阶段×情绪"的机制层耦合形态：阶段不改触发概率，改筛选灵敏度。
        """
        base = self._tone_float_cfg("confidence_threshold", 0.6)
        if not bool(self._cfg_getter().get("phase_sensitivity_enabled", True)):
            return base
        try:
            phase = self._phase_state(shard).phase
        except Exception:  # noqa: BLE001 - 配置异常时不加偏置
            return base
        if phase not in ("menstrual", "follicular", "ovulatory"):
            return base
        bias = max(0.0, self._tone_float_cfg("phase_sensitivity", 0.15))
        return max(0.1, base - bias)

    # ---- HTTP 分析通道：CSRF token / 宿主情感槽 / core_config 加载 / 槽位直连 ----

    async def _get_csrf_token(self) -> str | None:
        """宿主 CSRF token = GET /health 的 instance_id（本机回环设计如此）；缓存复用。"""
        if self._csrf_token:
            return self._csrf_token
        payload = await self._http("GET", "/health")
        token = str(payload.get("instance_id") or "") if isinstance(payload, dict) else ""
        if token:
            self._csrf_token = token
            return token
        now_mono = time.monotonic()
        if now_mono - self._last_csrf_error_logged > 300:
            self._last_csrf_error_logged = now_mono
            self._logger().warning(
                "emotion sense: GET /health 未拿到 instance_id（CSRF token），语气分析暂停 (throttled 5min)"
            )
        return None

    async def _analyze_turn_tone(self, text: str, lanlan: str) -> tuple[str, float] | None:
        """分析文本情绪，返回 (label, confidence)；失败/降级返回 None。

        [emotion_sense].slot 为空或 "emotion"（默认）→ 走宿主 /api/emotion/analysis
        （宿主情感模型槽）；选其他文本槽位 → 读宿主本地 core_config.json 解析
        该槽位的 model/base_url/api_key 直连其端点（见 _analyze_turn_tone_direct）。
        宿主未配模型时端点返回 200+error 字段——检测到即休眠（节流 warning），不当异常处理。
        请求体只带 text、不带 lanlan_name：纯静默分析，不触发宿主把结果推给前端
        改头像表情的副作用（本插件只收集情绪信号，不接管表情）。
        """
        slot = str(self._cfg_getter().get("slot") or "").strip()
        if slot and slot != "emotion":
            return await self._analyze_turn_tone_direct(text, slot)
        token = await self._get_csrf_token()
        if not token:
            return None
        # 不带 lanlan_name：避免宿主把分析结果推给前端更新头像表情（副作用）
        body: JsonObject = {"text": text}
        # Origin 跟着实际请求 base 走（不写死端口）：自定义 MAIN_SERVER_PORT 下仍与
        # 宿主 allowed_origins 里的 request.base_url 逐项相等，不依赖 hostname 降级分支
        headers = {"X-CSRF-Token": token, "Origin": self._api_base()}
        payload = await self._http("POST", "/api/emotion/analysis", body, headers=headers)
        if not isinstance(payload, dict):
            # 403 等失败：token 可能失效，清缓存下趟重取
            self._csrf_token = None
            return None
        if payload.get("error"):
            now_mono = time.monotonic()
            if now_mono - self._last_tone_error_logged > 300:
                self._last_tone_error_logged = now_mono
                self._logger().warning(
                    "emotion sense dormant: analysis endpoint error: {}", payload.get("error")
                )
            return None
        label = str(payload.get("emotion") or "neutral")
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return label, confidence

    def _load_core_config(self) -> JsonObject:
        """读宿主 core_config.json（含明文 key），5 秒内存缓存；文件缺失/JSON 坏 → {}。"""
        cached_ts, cached_cfg = self._core_config_cache
        now_mono = time.monotonic()
        if cached_cfg and now_mono - cached_ts < _CORE_CONFIG_CACHE_TTL:
            return cached_cfg
        cfg: JsonObject = {}
        path = self._core_config_path()
        if path is not None and path.is_file():
            import json as _json

            try:
                raw = _json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = raw
            except Exception as exc:  # noqa: BLE001 - 坏配置按"无配置"降级，不炸分析链路
                self._logger().debug("load host core_config.json failed: {}", exc)
        self._core_config_cache = (now_mono, cfg)
        return cfg

    async def _aload_core_config(self) -> JsonObject:
        """`_load_core_config` 的 async 对偶：阻塞读盘丢回线程池。

        经注入的 `core_config_loader` 现取（不快照），所以测试在主类实例上
        monkeypatch `_load_core_config` 的链路照旧生效。与主类同名同语义，
        两层各有一个 `a*` 版而不是内联 `to_thread`——对偶性是硬要求。
        """
        return await asyncio.to_thread(self._core_config_loader)

    async def _analyze_turn_tone_direct(self, text: str, slot: str) -> tuple[str, float] | None:
        """直连所选槽位的端点做五分类分析；解析不出可用端点/请求失败/回复坏 → None 静默降级。"""
        resolved = self._resolve_slot(await self._aload_core_config(), slot)
        if resolved is None:
            self._logger().debug("tone slot {} unresolved in host core_config, skip analysis", slot)
            return None
        raw = await asyncio.to_thread(
            self._post_chat,
            resolved["base_url"], resolved["api_key"], resolved["model"],
            _TONE_DIRECT_PROMPT + text,
        )
        if not raw:
            return None
        return self._parse_result(raw)

    # ---- 主链路：recent.json 水位 diff → 门控 → 分析 → 筛选/校正判定 ----

    async def _poll_recent_turns(
        self, shard: _LanlanShard, *, lanlan: str, peek: bool = False, advance: bool = True
    ) -> tuple[str, str] | None:
        """diff 宿主 recent 记忆文件，返回最新一轮 (用户消息, 她的回复)；无新轮返回 None。

        端点 /api/memory/recent_file 的逻辑文件名必须是 recent_<角色名>.json
        （宿主 resolve_recent_file_path 的正则 ^recent_(.+)\\.json$，裸 recent.json
        会 400 静默失败——语气感知全断的实测根因），按 lanlan 定向到该角色的
        memory/<角色>/recent.json；角色名含非 ASCII 时 URL 编码。

        水位 = "轮数:末条回复hash"（per-shard）：recent.json 是滚动窗口，
        条目数不变时旧条目会被新回复挤掉，hash 兜底。
        - peek=True：只读最新一轮，完全不动水位（调试入口用）；
        - advance=False：判定是否新轮但不推进水位（门控未过的轮留给下趟，
          间隔节流不该吞掉这轮分析机会；概率抽查跳过的轮才推进水位——
          抽样语义就是"这轮不查"）；
        - 首趟只建基线不分析历史（插件启动时不该对旧对话补分析）。

        性能：宿主响应带 fingerprint 时，指纹未变 = 内容未变，跳过 json 解析与
        组对遍历直接复用上趟结果（peek 同样安全：返回的本就是同一轮，无副作用）；
        每趟算出的 marker/末轮缓存在 shard 上（last_recent_marker/last_recent_turn），
        供 _maybe_tone_sense 门控过后直接推进水位，不必再发起第二次 HTTP。
        """
        from urllib.parse import quote as _urlquote

        payload = await self._http(
            "GET", f"/api/memory/recent_file?filename={_urlquote(f'recent_{lanlan}.json')}"
        )
        if not isinstance(payload, dict):
            now_mono = time.monotonic()
            if now_mono - self._last_recent_error_logged > 300:
                self._last_recent_error_logged = now_mono
                self._logger().warning(
                    "recent memory poll failed (throttled 5min): lanlan={} —— 端点不可达/文件名不合法/返回非 JSON，语气感知暂停",
                    lanlan,
                )
            return None
        fingerprint = str(payload.get("fingerprint") or "")
        if fingerprint and fingerprint == shard.last_recent_fingerprint and shard.last_recent_turn is not None:
            # 指纹未变：跳过解析，复用上趟算出的 marker 与末轮，走同一套水位判定
            marker = shard.last_recent_marker
            latest = shard.last_recent_turn
        else:
            content = payload.get("content")
            if isinstance(content, str):
                # 部分版本 content 是 JSON 字符串而非已解析的 list
                import json as _json

                try:
                    content = _json.loads(content)
                except (ValueError, TypeError):
                    return None
            if not isinstance(content, list) or not content:
                return None
            turns: list[tuple[str, str]] = []
            last_user = ""
            for item in content:
                if not isinstance(item, dict):
                    continue
                data = item.get("data") if isinstance(item.get("data"), dict) else item
                text = str(data.get("content") or "")
                if item.get("type") == "human":
                    last_user = text
                elif item.get("type") == "ai" and text:
                    turns.append((last_user, text))
            if not turns:
                return None
            marker = f"{len(turns)}:{hash(turns[-1][1])}"
            latest = turns[-1]
            # 更新指纹缓存（peek 也更新：指纹相同 = 内容相同，无副作用）；
            # 宿主不带 fingerprint 时永不缓存，退回每趟全量解析
            shard.last_recent_marker = marker
            shard.last_recent_turn = latest
            if fingerprint:
                shard.last_recent_fingerprint = fingerprint
        if peek:
            return latest
        if not shard.last_turn_marker:
            # 基线是无条件水位初始化（不是分析机会），不受 advance 影响
            shard.last_turn_marker = marker
            shard.tone_window = []
            return None
        if marker == shard.last_turn_marker:
            return None
        if advance:
            shard.last_turn_marker = marker
        return latest

    async def _maybe_tone_sense(self, lanlan: str, shard: _LanlanShard) -> bool:
        """语气感知主入口（tick 驱动，只对当前角色 shard）：门控链 → 分析 → 分模式判定。"""
        if not self._emotion_sense_enabled(shard):
            return False
        now = time.time()
        min_interval = self._tone_int_cfg("min_interval_sec", 60)
        if now - shard.last_tone_analysis_ts < min_interval:
            # 间隔节流前移到 poll 之前：空闲期不再每 tick 白拉一次 HTTP；
            # 不推进水位（也尚未拉取），这轮留给节流过后再分析。
            # 基线尚未建立时 last_tone_analysis_ts 恒为 0.0，门控必然放行，首趟不受影响
            return False
        turn = await self._poll_recent(shard, lanlan=lanlan, advance=False)
        if turn is None:
            return False
        user_text, her_text = turn
        # 门控后的水位推进一律复用本次 poll 算出的 marker（存于 shard.last_recent_marker），
        # 不再发起第二次 HTTP；与原先"再 poll 一次推进"语义等价
        mood_active = self._mood_enabled(shard) and shard.mood.is_active()
        if not mood_active:
            # 筛选模式的概率抽查（校正模式不受此限：生效期间量小且不能漏关键轮）
            rate = min(1.0, max(0.0, self._tone_float_cfg("check_rate", 1.0)))
            if rate < 1.0 and random.random() >= rate:
                shard.last_turn_marker = shard.last_recent_marker  # 抽样跳过：推进水位，这轮不查
                return False
            if rate <= 0.0:
                shard.last_turn_marker = shard.last_recent_marker
                return False
        shard.last_turn_marker = shard.last_recent_marker  # 门控全过：推进水位
        shard.last_tone_analysis_ts = now
        # 她的回复单独分析（此前非校正模式把用户消息合并进同一段文本出单 label，
        # 用户侧没有独立权重，她的语气被误判时用户的话毫无制衡能力——0.6.8 拆开）
        result = await self._analyze_tone(her_text[:500], lanlan)
        if result is None:
            return False
        label, confidence = result
        # 语气信号喂入连续心情（两种模式都喂）：先惰性衰减再加、单轮限幅，随落盘
        self._feed_affect(shard, label, confidence, now=now)
        # 用户侧情绪同向传导（低权重，校正模式跳过）：互动氛围纳入考虑但占比更低
        if not mood_active:
            await self._maybe_feed_user_tone(lanlan, shard, user_text, now)
        await self._save_mood(lanlan, shard)
        # 感知活着的证据（每分钟最多一条，受 min_interval 兜底）：此前全链路静默，
        # "面板数值不变"类故障零线索可查
        self._logger().info(
            "tone fed affect: lanlan={} label={} conf={:.2f} -> valence={:.2f} arousal={:.2f}",
            lanlan, label, confidence, shard.mood.valence, shard.mood.arousal,
        )
        if mood_active:
            return self._correction_check(lanlan, shard, label)
        return self._screen_check(lanlan, shard, label, confidence)

    async def _maybe_feed_user_tone(self, lanlan: str, shard: _LanlanShard, user_text: str, now: float) -> None:
        """用户侧情绪同向传导（低权重）：分析用户消息并按 [emotion_sense].user_affect_weight
        （默认 0.25）缩放步长喂入连续心情——互动氛围纳入考虑，但她的情绪表达绝对主导
        （她的回复权重 1.0）；配 0 关闭。只喂心情，不参与筛选/校正判定；
        校正模式不调用（校正只看她的表现，不该被用户侧稀释）。
        """
        weight = max(0.0, min(1.0, self._tone_float_cfg("user_affect_weight", 0.25)))
        user_text = (user_text or "").strip()
        if weight <= 0.0 or not user_text:
            return
        result = await self._analyze_tone(user_text[:300], lanlan)
        if result is None:
            return  # 分析失败静默降级，不影响她的主链路（已喂完）
        label, confidence = result
        self._feed_affect(shard, label, confidence, now=now, weight=weight)
        self._logger().info(
            "user tone fed affect: lanlan={} label={} conf={:.2f} w={:.2f} -> valence={:.2f} arousal={:.2f}",
            lanlan, label, confidence, weight, shard.mood.valence, shard.mood.arousal,
        )

    def _tone_correction_check(self, lanlan: str, shard: _LanlanShard, label: str) -> bool:
        """校正模式：label 入趋势窗口，攒满 window_turns 判定一次（多数派），判定后清空。"""
        shard.tone_window.append(label)
        window = self._tone_int_cfg("window_turns", 2)
        if len(shard.tone_window) < window:
            return False
        labels, shard.tone_window = shard.tone_window[-window:], []
        action = shard.mood.action
        warm = sum(1 for item in labels if item in _TONE_WARM_LABELS)
        cold = sum(1 for item in labels if item in _TONE_COLD_LABELS)
        majority = window // 2 + 1
        if action in _PROACTIVE_PAUSE_ACTIONS and warm >= majority:
            text = (
                "（语气感知提醒）这几轮下来，你自己的语气其实已经软下来了。"
                "如果心里不气了，记得调用 mood_rising_tide 把状态切回心情转晴——"
                "嘴上和好但状态不切的话，系统会一直按当前情绪约束你。还想再缓缓就忽略。"
            )
        elif action in (*_POSITIVE_ACTIONS, "ripple", "rising_tide") and cold >= majority:
            text = (
                "（语气感知提醒）这几轮你的语气好像沉下来了，和当前的状态不太一致。"
                "如果心情真的变了，可以调用合适的情绪工具或 mood_drift_bottle 把感受记下来；"
                "只是随口一说的话，忽略这条就好。"
            )
        else:
            return False
        self._push(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            source=self._plugin_id(),
            target_lanlan=lanlan,
            coalesce_key=f"{self._plugin_id()}.tone_correction",
            metadata={
                "message_type": f"{self._plugin_id()}.tone_correction",
                "during_action": action,
                "window": labels,
            },
        )
        self._logger().info("tone correction nudge pushed for {} (action={}, window={})", lanlan, action, labels)
        return True

    def _tone_screen_check(
        self, lanlan: str, shard: _LanlanShard, label: str, confidence: float
    ) -> bool:
        """筛选模式：互动情绪浓度超阈值（敏感期下移）→ 中性提醒，10 分钟节流。"""
        if confidence < self._effective_tone_threshold(shard):
            return False
        now = time.time()
        if now - shard.last_screen_nudge_ts < _TONE_SCREEN_NUDGE_SEC:
            return False
        if label in _TONE_COLD_LABELS:
            # 文案保持中性：只陈述"有波动"，不诱导具体动作——敏感期阈值下移
            # 不能变成负面工具的放大器
            text = (
                "（语气感知提醒）刚才那轮互动里情绪有些波动。如果此刻心里确实有感受，"
                "可以调用情绪动作（比如 mood_ripple 心有涟漪）或 mood_drift_bottle 把它记下来；"
                "觉得没什么就忽略这条，不用勉强。"
            )
        elif label == "happy":
            text = (
                "（语气感知提醒）刚才那轮互动气氛很好。如果你想留住这份开心，"
                "可以调用 mood_spring_tide / mood_warm_current 或 mood_drift_bottle 记下来；"
                "觉得没什么就忽略这条。"
            )
        else:
            return False
        shard.last_screen_nudge_ts = now
        self._push(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            source=self._plugin_id(),
            target_lanlan=lanlan,
            coalesce_key=f"{self._plugin_id()}.tone_screen",
            metadata={
                "message_type": f"{self._plugin_id()}.tone_screen",
                "emotion": label,
                "confidence": confidence,
            },
        )
        self._logger().info("tone screen nudge pushed for {} ({}@{:.2f})", lanlan, label, confidence)
        return True
