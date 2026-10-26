"""学习辅助猫娘 v0.1（作者：MENGYAOYUE）

一个会规划、会出题、会看卷子、还会做心理疏导的学习插件：

1. 规划     —— 按目标考试、目标分、剩余天数与每天可用时间生成长短期计划；
2. 联网     —— 连接本人学习平台账号同步真实进度，并检索免费公开课程资源；
3. 教学     —— 三段式（定义题 → 中等易错题 → 综合创新题），题型严格遵循命题规律；
4. 诊断     —— 从题目/试卷/地区/学校推断薄弱点，给出保守 / 最可能 / 理想三档分数预期；
5. 疏导     —— 按考试类型、临考阶段与情绪状态做个性化心理暗示与疏导。

设计约束：
- 零第三方依赖，网络用标准库 urllib；所有同步 IO 都经 ``asyncio.to_thread``；
- 平台连接只读取本人账号数据，遇到验证码交人工输入，不逆向加密参数；
- 凭据与学习数据只落在插件自己的 data/ 目录，绝不进 git。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional


def _dump_crash(stage: str) -> None:
    """把崩溃现场写到临时目录。

    宿主经常直接吞掉插件子进程的 stderr（日志里只剩一句 start_plugin failed），
    没有 traceback 就没法定位。这里主动落盘一份，排查完即可删掉本函数。
    """
    import traceback

    try:
        target = Path(tempfile.gettempdir()) / f"neko_study_copilot_crash_{stage}.txt"
        target.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


_PROBE_MODULES = (
    "urllib.robotparser",
    "http.cookiejar",
    "http.server",
    "http.client",
    "html",
    "html.parser",
    "gzip",
    "zlib",
    "concurrent.futures",
    "sqlite3",
    "socket",
    "ssl",
    "base64",
    "binascii",
    "hashlib",
    "tempfile",
    "traceback",
    "dataclasses",
    "contextlib",
    "importlib.util",
    "statistics",
    "random",
    "difflib",
    "textwrap",
    "zoneinfo",
    "secrets",
    "uuid",
    "logging",
    "argparse",
)


def _probe_host_modules() -> None:
    """记录宿主冻结环境里可用的标准库模块。

    宿主是 PyInstaller 冻结进程：标准库里只有被它自己（或依赖）引用过的模块
    才会被打包，所以"标准库一定有"这个假设在这里不成立——``urllib.robotparser``
    就是活生生的例子。这份清单写进临时目录，排查插件启动问题时先看它，
    能省掉一轮"改一行、重启一次"的盲试。
    """
    import importlib.util

    try:
        lines = []
        for name in _PROBE_MODULES:
            try:
                ok = importlib.util.find_spec(name) is not None
            except Exception:
                ok = False
            lines.append(f"{'OK     ' if ok else 'MISSING'} {name}")
        target = Path(tempfile.gettempdir()) / "neko_study_copilot_host_modules.txt"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


_probe_host_modules()

try:
    from plugin.sdk.plugin import (
        Err,
        NekoPluginBase,
        Ok,
        SdkError,
        lifecycle,
        llm_tool,
        message,
        neko_plugin,
        plugin_entry,
    )

    from . import _fmt as fmt
    from . import _games as minigames
    from . import _guard as guard
    from . import _persona as persona
    from ._connectors import ConnectorManager
    from ._crawl import Crawler, extract_json_object
    from ._diagnose import build_forecast, diagnose, format_diagnosis, format_forecast
    from ._memory import MemoryKeeper
    from ._panel import AsyncBridge, PanelServer, find_open_port, guess_mime
    from ._planner import build_plan, format_plan, resolve_exam_date, subject_label
    from ._profiles import (
        format_profile,
        get_profile,
        list_exam_types,
        resolve_exam_type,
        subject_keys,
    )
    from ._progress import ProgressEngine
    from ._psych import build_comfort_prompt, counsel, format_counsel
    from ._sources import (
        ResourceSearcher,
        build_links,
        build_query,
        format_resource_plan,
        format_resources,
        list_sources,
    )
    from ._store import StudyStore
    from ._syllabus import find_point, format_point, get_point, match_points, points_for, roi_ranking
    from ._tutor import (
        TeachContext,
        build_grade_prompt,
        build_quiz_prompt,
        build_teach_prompt,
        decide_stage,
        format_questions,
        quiz_intro,
        validate_question_plan,
    )
except Exception:  # pragma: no cover - 只在宿主环境里用于定位
    _dump_crash("import")
    raise

_PLUGIN_ID = "neko_study_copilot"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def _safe_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _parse_json_payload(text: str) -> Any:
    """从大模型输出里抠出 JSON（对象或数组都支持）。"""
    if not text:
        return None
    candidates = [match.group(1) for match in _JSON_BLOCK_RE.finditer(text)]
    candidates.append(text)
    for candidate in candidates:
        candidate = candidate.strip()
        for opener, closer in (("[", "]"), ("{", "}")):
            start = candidate.find(opener)
            end = candidate.rfind(closer)
            if start >= 0 and end > start:
                try:
                    return json.loads(candidate[start : end + 1])
                except Exception:
                    continue
    return None


def _extract_questions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("questions", "items", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


@neko_plugin
class StudyCopilotPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self.data_dir = Path(self.data_path())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir = Path(__file__).resolve().parent / "static"

        # ── 配置（异步 startup 中由 config.dump() 注入）────────────
        self.exam_type: str = "gaokao"
        self.region: str = ""
        self.school: str = ""
        self.grade: str = ""
        self.subjects: str = ""
        self.target_score: float = 0.0
        self.exam_date: str = ""
        self.credits: str = ""       # 学分配置，形如 "math_adv:5,english:3"
        self.daily_minutes: int = 180
        self.teach_style: str = "gentle"
        self.quiz_stage: str = "auto"
        self.quiz_count: int = 3
        self.quiz_with_traps: bool = True
        self.network_enabled: bool = True
        self.free_source_search: bool = True
        self.platform_sync_enabled: bool = True
        self.crawl_interval_ms: int = 1500
        self.crawl_respect_robots: bool = True
        self.crawl_max_pages: int = 6
        self.crawl_timeout: float = 15.0
        self.psychology_enabled: bool = True
        self.psychology_level: str = "normal"
        # 人设强度：full 猫娘人格 / light 轻度 / off 教学模式（见 _persona.py）
        self.persona_level: str = persona.DEFAULT_LEVEL
        self.vision_enabled: bool = True
        # 记忆：短期对话记忆保留天数、每次召回多少条
        self.memory_enabled: bool = True
        self.memory_short_days: float = 7.0
        self.memory_recall_turns: int = 8
        self.progress_enabled: bool = True
        self.game_enabled: bool = True
        # 最近一条用户聊天消息里捕获的文本与图片（识图诊断用）
        self.last_capture: dict[str, Any] = {"text": "", "images": [], "ts": 0.0}
        self.admin_password: str = ""
        self.panel_port: int = 15880
        self.llm_timeout: float = 60.0
        self.request_timeout: float = 20.0
        self.catgirl_name: str = "猫娘"
        self._config_loaded: bool = False

        # ── 运行时组件 ────────────────────────────────────────
        self.store = StudyStore(self.data_dir / "study.db")
        # 两级记忆：短期对话记忆（默认 7 天）+ 长期事实记忆（不自动删）
        self.memory = MemoryKeeper(self.store, logger=self.logger)
        # 修行等级：经验只来自真实学习行为（见 _progress.py）
        self.progress = ProgressEngine(self.store, logger=self.logger)
        # 小游戏：自成一套的积分/等级/记录/成就，**不给修行经验**（见 _games.py）
        self.games = minigames.GameService(self.store, logger=self.logger)
        # 面板偏好（字体/字号）存在插件自己的 data/ 里，纯本机
        self._prefs_path = self.data_dir / "panel_prefs.json"
        self._prefs: Optional[dict[str, str]] = None
        self.crawler: Optional[Crawler] = None
        self.connectors: Optional[ConnectorManager] = None
        self.searcher: Optional[ResourceSearcher] = None
        self._panel_server: Optional[PanelServer] = None
        self._bridge = AsyncBridge()
        try:
            self._bridge.set_notifier(lambda msg: self.logger.warning("%s", msg))
        except Exception:
            pass
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── 配置 ──────────────────────────────────────────────────
    async def _load_config(self) -> None:
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:
            self.logger.warning("[study_copilot] 读取配置失败，使用默认值: %s", exc)
            return
        section = cfg.get(_PLUGIN_ID)
        if not isinstance(section, dict):
            section = {}
        self.exam_type = resolve_exam_type(_safe_str(section.get("exam_type"), self.exam_type))
        self.region = _safe_str(section.get("region"), self.region)
        self.school = _safe_str(section.get("school"), self.school)
        self.grade = _safe_str(section.get("grade"), self.grade)
        self.subjects = _safe_str(section.get("subjects"), self.subjects)
        self.target_score = _safe_float(section.get("target_score"), self.target_score)
        self.exam_date = _safe_str(section.get("exam_date"), self.exam_date)
        self.credits = _safe_str(section.get("credits"), self.credits)
        self.daily_minutes = _safe_int(section.get("daily_minutes"), self.daily_minutes)
        self.teach_style = _safe_str(section.get("teach_style"), self.teach_style)
        self.quiz_stage = _safe_str(section.get("quiz_stage"), self.quiz_stage)
        self.quiz_count = _safe_int(section.get("quiz_count"), self.quiz_count)
        self.quiz_with_traps = _safe_bool(section.get("quiz_with_traps"), self.quiz_with_traps)
        self.network_enabled = _safe_bool(section.get("network_enabled"), self.network_enabled)
        self.free_source_search = _safe_bool(section.get("free_source_search"), self.free_source_search)
        self.platform_sync_enabled = _safe_bool(section.get("platform_sync_enabled"), self.platform_sync_enabled)
        self.crawl_interval_ms = _safe_int(section.get("crawl_interval_ms"), self.crawl_interval_ms)
        self.crawl_respect_robots = _safe_bool(section.get("crawl_respect_robots"), self.crawl_respect_robots)
        self.crawl_max_pages = _safe_int(section.get("crawl_max_pages"), self.crawl_max_pages)
        self.crawl_timeout = _safe_float(section.get("crawl_timeout"), self.crawl_timeout)
        self.psychology_enabled = _safe_bool(section.get("psychology_enabled"), self.psychology_enabled)
        self.psychology_level = _safe_str(section.get("psychology_level"), self.psychology_level)
        self.vision_enabled = _safe_bool(section.get("vision_enabled"), self.vision_enabled)
        self.memory_enabled = _safe_bool(section.get("memory_enabled"), self.memory_enabled)
        self.memory_short_days = _safe_float(section.get("memory_short_days"), self.memory_short_days)
        self.memory_recall_turns = _safe_int(section.get("memory_recall_turns"), self.memory_recall_turns)
        self.memory.enabled = self.memory_enabled
        self.memory.short_days = max(0.5, self.memory_short_days)
        self.memory.recall_turns = max(2, self.memory_recall_turns)
        self.progress_enabled = _safe_bool(section.get("progress_enabled"), self.progress_enabled)
        self.progress.enabled = self.progress_enabled
        self.game_enabled = _safe_bool(section.get("game_enabled"), self.game_enabled)
        # 人设：persona_level 是主开关；teaching_switch / teaching_mode 作为别名接受
        # （面板上的「教学模式」开关写的就是这个），true 等价于 persona_level="off"
        persona_raw = section.get("persona_level")
        if not persona_raw:
            # 配置里没有就退回面板偏好里存的那份（_set_persona 会同时写这里）
            try:
                persona_raw = self._load_prefs().get("persona_level")
            except Exception:
                persona_raw = ""
        self.persona_level = persona.normalize_level(persona_raw or self.persona_level)
        for alias in ("teaching_mode", "teaching_switch", "strict_teaching"):
            flag = section.get(alias)
            if isinstance(flag, bool):
                self.persona_level = "off" if flag else persona.DEFAULT_LEVEL
                break
        self.admin_password = _safe_str(section.get("admin_password"), self.admin_password)
        self.panel_port = _safe_int(section.get("panel_port"), self.panel_port)
        self.llm_timeout = _safe_float(section.get("llm_timeout"), self.llm_timeout)
        self.request_timeout = _safe_float(section.get("request_timeout"), self.request_timeout)
        self.catgirl_name = _safe_str(section.get("catgirl_name"), self.catgirl_name)
        self._config_loaded = True

    async def _ensure_ready(self) -> None:
        if not self._config_loaded:
            await self._load_config()
        # 过期短期记忆的清理放在这里：12 小时内只真跑一次，清理前会先压成长期事实
        if self.memory_enabled:
            await asyncio.to_thread(self.memory.purge)
        # 宿主可能在不同事件循环里调度 entry；只要原来那条已死就跟着当前循环走
        try:
            self._bridge.bind_if_dead(asyncio.get_running_loop())
        except RuntimeError:
            pass

    def _effective_profile(self) -> dict[str, Any]:
        """配置与数据库档案合并：数据库里填过的优先。"""
        stored = self.store.get_profile()
        profile = {
            "exam_type": self.exam_type,
            "region": self.region,
            "school": self.school,
            "grade": self.grade,
            "subjects": self.subjects,
            "target_score": self.target_score,
            "exam_date": self.exam_date,
            "daily_minutes": self.daily_minutes,
            "teach_style": self.teach_style,
            "quiz_stage": self.quiz_stage,
            "psychology_level": self.psychology_level,
            "credits": self.credits,
        }
        for key, value in stored.items():
            if value not in (None, "", 0):
                profile[key] = value
        if not profile.get("subjects"):
            profile["subjects"] = ",".join(subject_keys(profile["exam_type"], ""))
        return profile

    def _subjects_list(self, profile: dict[str, Any]) -> list[str]:
        raw = str(profile.get("subjects") or "")
        return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]

    def _days_left(self, profile: dict[str, Any]) -> int:
        _date, days = resolve_exam_date(str(profile.get("exam_date") or ""), profile["exam_type"])
        return days

    # ── 大模型 ────────────────────────────────────────────────
    def _model_config(self, kind: str = "conversation") -> dict[str, Any]:
        try:
            from utils.config_manager import get_config_manager

            cfg = get_config_manager().get_model_api_config(kind)
            return cfg if isinstance(cfg, dict) else {}
        except Exception as exc:
            self.logger.warning("[study_copilot] 读取模型配置失败: %s", exc)
            return {}

    def _vision_model_config(self) -> dict[str, Any]:
        """视觉模型配置：优先宿主的 vision 档，没有就退回 conversation 档。"""
        cfg = self._model_config("vision")
        if _safe_str(cfg.get("model")):
            return cfg
        return self._model_config("conversation")

    async def _llm_chat(
        self,
        system: str,
        user: str,
        timeout: Optional[float] = None,
        *,
        topic: str = "",
        keywords: str = "",
        ref_kind: str = "",
        record: bool = False,
    ) -> str:
        """带上记忆的模型调用。

        - 给了 ``topic`` / ``keywords`` 时，先把记忆（最近对话 + 相关长期事实）接到提示词前面；
        - ``record=True`` 时把这次回答写进短期记忆，供后续追问衔接。

        记忆是我们自己塞进去的上下文，所以这里额外提醒模型：无关的别硬扯、没有的别编。
        """
        block = ""
        if topic or keywords:
            block = await asyncio.to_thread(self.memory.build_context, topic=topic, keywords=keywords)
        text = await self._llm_chat_raw(system, f"{block}\n\n---\n\n{user}" if block else user, timeout)
        if record and text:
            await asyncio.to_thread(
                self.memory.remember_turn,
                "assistant",
                text,
                topic=topic,
                ref_kind=ref_kind or "chat",
            )
        return text

    async def _llm_chat_raw(self, system: str, user: str, timeout: Optional[float] = None) -> str:
        """调用宿主配置的大模型。优先走官方 llm_client，失败降级到直连。"""
        cfg = self._model_config()
        model = _safe_str(cfg.get("model"))
        base_url = _safe_str(cfg.get("base_url")).rstrip("/")
        api_key = _safe_str(cfg.get("api_key"))
        if not (model and base_url and api_key):
            raise SdkError("尚未配置可用的会话模型，请先在 N.E.K.O 的模型设置里配置。")
        limit = float(timeout or self.llm_timeout)
        provider_type = _safe_str(cfg.get("provider_type")) or None

        def _build(**extra: Any) -> Any:
            from utils.llm_client import create_chat_llm_async

            kwargs: dict[str, Any] = {
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "max_completion_tokens": 2048,
                "timeout": limit,
            }
            kwargs.update(extra)
            return create_chat_llm_async(**kwargs)

        try:
            llm = _build()
        except TypeError:
            llm = _build(provider_type=provider_type) if provider_type else _build()
        except ImportError:
            llm = None
        if llm is not None:
            try:
                result = await asyncio.wait_for(
                    llm.ainvoke(
                        [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ]
                    ),
                    timeout=limit,
                )
                text = getattr(result, "content", None) or _safe_str(result)
                if text:
                    return text
            except asyncio.TimeoutError:
                raise SdkError(f"模型响应超时（{int(limit)} 秒），请稍后再试。")
            except Exception as exc:
                self.logger.warning("[study_copilot] llm_client 调用失败，降级直连: %s", exc)
        return await asyncio.to_thread(self._llm_http, base_url, api_key, model, system, user, limit)

    def _llm_http(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system: str,
        user: str,
        timeout: float,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._llm_http_messages(base_url, api_key, model, messages, timeout)

    def _llm_http_messages(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        timeout: float,
    ) -> str:
        import urllib.error
        import urllib.request

        endpoint = f"{base_url}/chat/completions"
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_completion_tokens": 2048,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise SdkError(f"模型请求失败：HTTP {exc.code}")
        except Exception as exc:
            raise SdkError(f"模型请求失败：{exc}")
        choices = payload.get("choices") or []
        if not choices:
            raise SdkError("模型没有返回内容。")
        message = choices[0].get("message") or {}
        return _safe_str(message.get("content")).strip()

    async def _llm_json(self, system: str, user: str, timeout: Optional[float] = None) -> Any:
        text = await self._llm_chat(system, user, timeout=timeout)
        payload = _parse_json_payload(text)
        if payload is None:
            payload = extract_json_object(text) or None
        if payload is None:
            raise SdkError("模型返回的内容不是合法 JSON，请重试或换个说法。")
        return payload

    # ── 识图（聊天框发截图 → 转写题目 → 诊断/讲解）────────────
    def _capture_chat(self, payload: Any) -> None:
        """记录用户最近一条聊天消息里的文本与图片，供识图诊断使用。

        宿主的消息结构可能随版本变化，这里做防御式解析：能认出 text/image/image_url
        三类部件即可，认不出就原样存一份文本，绝不抛异常影响宿主。
        """
        try:
            data = payload if isinstance(payload, dict) else {}
            if isinstance(payload, str) and payload.strip():
                data = {"text": payload.strip()}
            for key in ("message", "event", "data", "payload", "record"):
                inner = data.get(key)
                if isinstance(inner, dict):
                    merged = dict(inner)
                    merged.update({k: v for k, v in data.items() if k not in merged})
                    data = merged
                    break
            parts = data.get("parts") or data.get("attachments") or data.get("content") or []
            if isinstance(parts, dict):
                parts = [parts]
            texts: list[str] = []
            images: list[dict[str, Any]] = []
            for part in parts:
                if not isinstance(part, dict):
                    if isinstance(part, str) and part.strip():
                        texts.append(part.strip())
                    continue
                kind = _safe_str(part.get("type")).lower()
                if kind in ("", "text"):
                    text = _safe_str(part.get("text") or part.get("content")).strip()
                    if text:
                        texts.append(text)
                elif kind in ("image", "image_url"):
                    item: dict[str, Any] = {"mime": _safe_str(part.get("mime")) or "image/png"}
                    if isinstance(part.get("data"), (bytes, bytearray)):
                        item["data"] = bytes(part["data"])
                    for locator in ("path", "file", "url"):
                        value = _safe_str(part.get(locator)).strip()
                        if value:
                            item[locator] = value
                            break
                    if ("data" in item) or ("path" in item) or ("url" in item):
                        images.append(item)
            if not texts and not images:
                text = _safe_str(data.get("text") or data.get("content")).strip()
                if text:
                    texts.append(text)
            if texts or images:
                self.last_capture = {
                    "text": "\n".join(texts)[:4000],
                    "images": images[-4:],
                    "ts": time.time(),
                }
        except Exception as exc:
            self.logger.warning("[study_copilot] 聊天捕获失败（忽略）: %s", exc)

    @message(id="study_capture", source="chat")
    def on_chat_message(self, *args, **kwargs):
        """被动监听聊天：记住用户最近发的文本与截图，识图诊断时直接用。

        同时把这一轮写进**短期记忆**——聊天是主要交互方式，如果只在调用入口时
        才记，用户在聊天框里说的话第二天就没了，追问也就接不上。
        """
        payload: Any = kwargs.get("payload") or kwargs
        if not isinstance(payload, dict) or not payload:
            payload = args[0] if args else {}
        self._capture_chat(payload)
        self._remember_capture()
        return Ok({"status": "captured"})

    def _remember_capture(self) -> None:
        """把刚捕获到的聊天内容写进短期记忆（同步方法：@message 处理器里用）。"""
        if not self.memory_enabled:
            return
        try:
            text = _safe_str(self.last_capture.get("text")).strip()
            if text:
                self.memory.remember_turn("user", text, ref_kind="chat", session="chat")
            if self._capture_image_count():
                self.memory.remember_turn(
                    "material",
                    f"（发来 {self._capture_image_count()} 张截图，尚未识图）",
                    ref_kind="chat-image",
                    session="chat",
                )
        except Exception as exc:
            self.logger.warning("[study_copilot] 聊天记忆写入失败（忽略）: %s", exc)

    def _capture_image_count(self) -> int:
        return len(self.last_capture.get("images") or [])

    async def _vision_transcribe(self, question: str) -> str:
        """把用户最近发的截图交给视觉模型转写成文字，并附上用户的提问。"""
        images = list(self.last_capture.get("images") or [])
        if not images:
            raise SdkError(
                "我还没收到你的截图喵。直接在聊天框把题目/试卷截图发给本喵，"
                "再说一句『识图诊断』或『讲讲这道题』就行，不用打字复制。"
            )
        item = images[-1]
        data_url = await asyncio.to_thread(self._image_to_data_url, item)
        prompt = (
            "你是学习辅助助手。这是用户发来的截图（可能是题目、试卷、作业、笔记或成绩单）。\n"
            "请完整转写图中的文字内容（保留题号、选项、分值、图注），"
            "若是试卷/成绩单请保留科目与分数；无法辨认的地方用〔无法辨认〕标注。\n"
            f"用户想问：{question or '（未说明，请按‘帮我看看这些题’处理）'}\n"
            "只输出转写文本，不要点评。"
        )
        cfg = self._vision_model_config()
        model = _safe_str(cfg.get("model"))
        base_url = _safe_str(cfg.get("base_url")).rstrip("/")
        api_key = _safe_str(cfg.get("api_key"))
        if not (model and base_url and api_key):
            raise SdkError("尚未配置可用的模型（含视觉模型），请先在 N.E.K.O 的模型设置里配置。")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        limit = min(self.llm_timeout, 90.0)
        try:
            from utils.llm_client import create_chat_llm_async

            llm = create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=2048,
                timeout=limit,
            )
            result = await asyncio.wait_for(llm.ainvoke(messages), timeout=limit)
            text = getattr(result, "content", None) or _safe_str(result)
            if text.strip():
                return text.strip()
        except SdkError:
            raise
        except Exception as exc:
            self.logger.warning("[study_copilot] 视觉模型 client 调用失败，降级直连: %s", exc)
        return await asyncio.to_thread(self._llm_http_messages, base_url, api_key, model, messages, limit)

    @staticmethod
    def _image_to_data_url(item: dict[str, Any]) -> str:
        """把捕获到的图片部件转成 data URL（支持内存字节 / 本地路径 / http 地址）。"""
        import base64
        import urllib.request

        mime = _safe_str(item.get("mime")) or "image/png"
        raw = item.get("data")
        if isinstance(raw, (bytes, bytearray)):
            return f"data:{mime};base64,{base64.b64encode(bytes(raw)).decode('ascii')}"
        locator = _safe_str(item.get("path") or item.get("file") or item.get("url"))
        if not locator:
            raise SdkError("这张截图的图片来源认不出来，麻烦重发一次喵。")
        if locator.startswith("http://") or locator.startswith("https://"):
            with urllib.request.urlopen(locator, timeout=15) as resp:  # noqa: S310 - 宿主内网地址
                raw = resp.read()
        else:
            raw = Path(locator).read_bytes()
        kind = "image/png" if locator.lower().endswith(".png") else mime
        return f"data:{kind};base64,{base64.b64encode(raw).decode('ascii')}"

    async def _vision_diagnose(self, question: str, subject: str = "") -> str:
        """识图 → 转写 → 走常规诊断链路。"""
        transcript = await self._vision_transcribe(question)
        profile = self._effective_profile()
        # 把截图转写存进短期记忆：追问「这张卷子第 3 题」时，模型能翻回来看
        await asyncio.to_thread(
            self.memory.remember_turn, "material", transcript, ref_kind="vision", session="vision"
        )
        if question:
            await asyncio.to_thread(
                self.memory.remember_turn, "user", question, ref_kind="vision", session="vision"
            )
        header = f"已读取你发的截图并转写如下：\n{transcript[:1200]}"
        await asyncio.to_thread(self.progress.award, "vision_diagnose", note="识图诊断")
        detail = await self._diagnose(transcript, subject)
        await asyncio.to_thread(
            self.store.add_session,
            "vision",
            f"识图诊断｜{_safe_str(profile.get('exam_type'))}｜题目字数 {len(transcript)}",
        )
        return fmt.join(header, fmt.section("诊断", detail))

    # ── 联网检索 ──────────────────────────────────────────────
    async def _search_resources(self, query: str, subject: str = "", point_id: str = "") -> str:
        if not self.network_enabled or not self.free_source_search or self.searcher is None:
            return ""
        items = await asyncio.to_thread(self.searcher.search, query, None, 4)
        if not items:
            return ""
        rows = [item.as_dict() for item in items]
        try:
            await asyncio.to_thread(self.store.save_resources, point_id, rows)
        except Exception:
            pass
        return format_resources(items, limit=6)

    async def _collect_resources(self, query: str, need: str = "", limit: int = 4) -> list[Any]:
        """检索 + 抓正文 + 按需求做内容分析。

        三步分开是有原因的：
        1. 检索只负责"找到候选"，靠关键词；
        2. 抓正文补上"这份材料到底讲了什么"，关键词是看不出来的；
        3. 才让模型按**用户的需求**判断哪份对得上、该怎么用——这一步不能省，
           否则给出的永远只是一串链接。
        """
        if self.searcher is None:
            return []
        items = await asyncio.to_thread(self.searcher.search_with_excerpt, query, None, limit)
        if not items:
            return []
        await self._analyze_resources(items, query, need)
        return items

    async def _analyze_resources(self, items: list[Any], query: str, need: str = "") -> None:
        """让模型读抓到的正文，逐条给出「讲了什么 / 和需求的关系 / 适合阶段 / 怎么用」。

        失败就静默跳过——没有分析也比报错好，前端会退化成链接列表。
        """
        with_text = [item for item in items if getattr(item, "excerpt", "")]
        if not with_text:
            return
        blocks = []
        for index, item in enumerate(with_text, start=1):
            blocks.append(
                f"[{index}] 平台：{item.source_name}｜标题：{item.title}\n"
                f"正文摘录：{item.excerpt[:1200]}"
            )
        system = (
            "你在帮学生筛选公开学习资料。下面是从免费平台抓到的候选材料正文摘录。\n"
            "请针对**学生当前的需求**逐条判断，并严格返回 JSON：\n"
            '{"items":[{"index":1,"covers":"这份材料讲了什么（30字内，具体到知识点）",'
            '"fit":"和学生需求的对应关系（30字内，对不上就直说对不上）",'
            '"level":"适合什么阶段（如 基础/一轮/冲刺/大学先修）",'
            '"how":"建议怎么用（30字内，如 只看某几节 / 当例题集用）"}],'
            '"pick":最推荐的一条 index,"pick_reason":"一句话理由"}\n'
            "要求：只依据摘录判断，不要脑补；摘录信息不足就在 covers 里写『信息不足』。"
        )
        user = f"学生需求：{need or query}\n\n" + "\n\n".join(blocks)
        try:
            payload = await self._llm_json(system, user, timeout=min(60.0, self.llm_timeout))
        except Exception as exc:
            self.logger.warning("[study_copilot] 资源内容分析失败（降级为链接列表）: %s", exc)
            return
        rows = (payload or {}).get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return
        by_index = {index: item for index, item in enumerate(with_text, start=1)}
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = by_index.get(_safe_int(row.get("index"), 0))
            if item is None:
                continue
            item.covers = _safe_str(row.get("covers"))[:120]
            item.fit = _safe_str(row.get("fit"))[:120]
            item.level = _safe_str(row.get("level"))[:60]
            item.how = _safe_str(row.get("how"))[:120]
        pick = _safe_int((payload or {}).get("pick"), 0) if isinstance(payload, dict) else 0
        reason = _safe_str((payload or {}).get("pick_reason"))[:160] if isinstance(payload, dict) else ""
        picked = by_index.get(pick)
        if picked is not None and reason:
            picked.fit = (picked.fit + f"　【推荐先看这个】{reason}").strip()

    async def _resolve_point(self, topic: str, subject: str = "") -> Any:
        """定位知识点：本地图谱优先，找不到就请大模型从描述里推断一个名字。"""
        profile = self._effective_profile()
        exam_type = profile["exam_type"]
        if not subject:
            subject = self._subjects_list(profile)[0] if self._subjects_list(profile) else ""
        point = find_point(topic, subject, exam_type)
        if point:
            return point
        candidates = match_points(topic, subject, exam_type, limit=3)
        if candidates:
            return candidates[0]
        # 让大模型把用户的说法翻译成图谱里的知识点名
        known = "、".join(item.name for item in points_for(subject, exam_type)[:40])
        system = (
            "你是考点索引助手。下面给出了该科目已知的考点名单，"
            "请从用户描述里挑出最接近的一个考点名称；如果都不合适，就返回最接近的通用章节名。\n"
            "只返回一个 JSON：{\"name\":\"考点名\"}"
        )
        user = f"用户描述：{topic}\n已知考点：{known}"
        try:
            payload = await self._llm_json(system, user, timeout=min(30.0, self.llm_timeout))
            name = _safe_str((payload or {}).get("name"))
            if name:
                found = find_point(name, subject, exam_type)
                if found:
                    return found
                hits = match_points(name, subject, exam_type, limit=1)
                if hits:
                    return hits[0]
        except Exception as exc:
            self.logger.warning("[study_copilot] 知识点推断失败: %s", exc)
        return None

    # ── 人设 / 教学模式 ────────────────────────────────────────
    @property
    def teaching_mode(self) -> bool:
        """教学模式是否开启（= 人设影响降到最低）。"""
        return persona.is_teaching(self.persona_level)

    def persona_state(self) -> dict[str, Any]:
        return {
            "level": self.persona_level,
            "label": persona.label(self.persona_level),
            "description": persona.describe(self.persona_level),
            "teaching_mode": self.teaching_mode,
        }

    def _out(self, text: Any) -> Any:
        """出口统一处理：教学模式下去掉我们自己模板里写死的口癖。

        只处理字符串；模型的输出靠提示词约束（见 ``_persona.directive``）。
        """
        if isinstance(text, str):
            return persona.soften(text, self.persona_level)
        return text

    async def _set_persona(self, level: str) -> dict[str, Any]:
        self.persona_level = persona.normalize_level(level)
        try:
            prefs = dict(self._load_prefs())
            prefs["persona_level"] = self.persona_level
            self._prefs = prefs
            self._write_prefs(prefs)
        except Exception as exc:
            self.logger.warning("[study_copilot] 人设档位写入本地偏好失败: %s", exc)
        try:
            await self.config.update({_PLUGIN_ID: {"persona_level": self.persona_level}})
        except Exception as exc:
            self.logger.warning("[study_copilot] 人设档位写回配置失败: %s", exc)
        state = self.persona_state()
        self.logger.info("[study_copilot] 人设档位 = %s（教学模式 %s）", self.persona_level, self.teaching_mode)
        return state

    def _api_persona(self, body: dict) -> dict:
        """面板顶栏开关：{enabled: true} → 教学模式；也可直接传 level。"""
        payload = body or {}
        level = _safe_str(payload.get("level")).strip()
        if not level and "enabled" in payload:
            level = "off" if _safe_bool(payload.get("enabled"), False) else persona.DEFAULT_LEVEL
        if not level:
            return {"ok": True, "persona": self.persona_state()}
        result = self._run_async(lambda: self._set_persona(level))
        # 统一成 {ok, persona}：前端只认这个结构，别再返回扁平 state
        state = result.get("persona") if isinstance(result, dict) else None
        if not isinstance(state, dict):
            state = result if isinstance(result, dict) and "level" in result else self.persona_state()
        return {
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "persona": state,
            "error": result.get("error", "") if isinstance(result, dict) else "",
        }

    def _progress_brief(self) -> dict[str, Any]:
        """给状态轮询用的精简进度：别把徽章明细塞进每次轮询的响应里。"""
        try:
            snap = self.progress.snapshot()
        except Exception:
            return {}
        return {
            "level": snap.level,
            "title": snap.title,
            "title_note": snap.title_note,
            "total_exp": snap.total_exp,
            "level_exp": snap.level_exp,
            "level_need": snap.level_need,
            "percent": round(snap.percent, 4),
            "streak": snap.streak,
            "today_exp": snap.today_exp,
            "badge_count": len(snap.badges),
            "badge_total": len(self.progress.badge_wall()["all"]),
        }

    def _api_game(self, body: dict) -> dict:
        """小游戏：取规则 / 取记录 / 交成绩。

        注意这里**完全不碰 ProgressEngine**——玩游戏不给修行经验，两条线分开。
        """
        if not self.game_enabled:
            return {"ok": False, "error": "小游戏当前是关闭的（配置里的 game_enabled）。"}
        payload = body or {}
        action = _safe_str(payload.get("action"), "state").strip() or "state"
        game = _safe_str(payload.get("game"), minigames.GAME_FRUIT).strip() or minigames.GAME_FRUIT
        if action == "config":
            config = self.games.config()
            config["local_audio"] = self.games.pad_audio(self.data_dir)
            config["local_audio_dir"] = f"data/{minigames.PAD_AUDIO_DIR}"
            return {"ok": True, "config": config, "state": self.games.state(game)}
        if action in ("import-audio", "import_audio"):
            files = payload.get("files")
            if not isinstance(files, list) or not files:
                return {"ok": False, "error": "没有收到音源文件。"}
            saved, skipped = self._import_pad_audio(files)
            return {
                "ok": True,
                "saved": saved,
                "skipped": skipped,
                "files": self.games.pad_audio(self.data_dir),
                "dir": f"data/{minigames.PAD_AUDIO_DIR}",
            }
        if action in ("pad-audio", "pad_audio"):
            names = self.games.pad_audio(self.data_dir)
            return {
                "ok": True,
                "files": names,
                "dir": f"data/{minigames.PAD_AUDIO_DIR}",
                "note": (
                    "把你自己有的音源（mp3/ogg/wav）放进插件 data/"
                    f"{minigames.PAD_AUDIO_DIR}/ 就会自动用上；这些文件不会被打进安装包。"
                ),
            }
        if action == "submit":
            run = payload.get("run")
            if not isinstance(run, dict):
                return {"ok": False, "error": "没有收到成绩数据。", "state": self.games.state(game)}
            return self.games.submit(run, game)
        return {"ok": True, "state": self.games.state(game), "config": self.games.config()}

    def _api_progress(self, _body: dict) -> dict:
        """修行等级：经验、头衔、徽章墙、经验规则（规则也要能查，别让人觉得经验来路不明）。"""
        snapshot = self.progress.snapshot()
        return {
            "ok": True,
            "progress": snapshot.as_dict(),
            "wall": self.progress.badge_wall(),
            "rules": self.progress.rules_text(),
            "enabled": self.progress_enabled,
        }

    def _api_background(self, body: dict) -> dict:
        """自定义背景：上传 / 切换模式 / 恢复默认。"""
        return self._save_background(body)

    # ── 记忆 ──────────────────────────────────────────────────
    def memory_report(self, query: str = "") -> str:
        """把记忆库整理成人话：短期剩几条、长期记了什么、要不要清理。"""
        snapshot = self.memory.snapshot(limit=8)
        stats = snapshot.get("stats") or {}
        blocks = [
            fmt.section(
                "记忆状态",
                fmt.bullets(
                    [
                        f"短期记忆（对话/材料）：**{stats.get('turns', 0)}** 条，"
                        f"保留 {self.memory_short_days:g} 天，到期自动清理",
                        f"待清理：{stats.get('expired', 0)} 条　长期记忆：**{stats.get('facts', 0)}** 条（不自动删）",
                        "存储位置：本机 `data/study.db`，不上传",
                    ]
                ),
            )
        ]
        if query:
            hits = self.store.search_facts(query, limit=6)
            rows = [f"[{row.get('kind')}] {row.get('text')}" for row in hits]
            blocks.append(
                fmt.section(
                    f"长期记忆里和「{query}」有关的",
                    fmt.bullets(rows) if rows else "没找到相关记录。",
                )
            )
        else:
            facts = snapshot.get("facts") or []
            rows = [f"[{row.get('kind')}] {row.get('text')}" for row in facts[:8]]
            blocks.append(
                fmt.section("长期记忆（重要的那几条）", fmt.bullets(rows) if rows else "还没攒下长期记忆。")
            )
        turns = snapshot.get("turns") or []
        rows = [f"[{row.get('ago')}] {row.get('text', '')[:80]}" for row in turns[-6:]]
        blocks.append(
            fmt.section("最近的短期记忆", fmt.bullets(rows) if rows else "最近没聊过什么。")
        )
        blocks.append(
            fmt.note("想清空可以说『忘掉刚才』（清短期）或『清空记忆』（短期+长期）。")
        )
        return fmt.join(*blocks)

    def _api_memory(self, body: dict) -> dict:
        payload = body or {}
        action = _safe_str(payload.get("action"), "show").strip() or "show"
        if action == "forget":
            scope = _safe_str(payload.get("scope"), "short").strip() or "short"
            fact_id = _safe_int(payload.get("fact_id"), 0)
            result = self.memory.forget(scope=scope, fact_id=fact_id)
            return {"ok": True, "memory": self.memory.snapshot(), "removed": result}
        if action == "purge":
            removed = self.memory.purge(force=True)
            return {"ok": True, "memory": self.memory.snapshot(), "removed": {"purged_turns": removed}}
        return {"ok": True, "memory": self.memory.snapshot(), "report": self.memory_report()}

    # ── 面板偏好（字体 / 字号 / 背景，纯前端体验，不影响教学逻辑）──────
    _PREFS_DEFAULT: dict[str, str] = {
        "ui_font": "system",
        "ui_font_size": "m",
        # 背景：default=插件自带那张插画（默认） / custom=用户上传 / plain=纯色渐变
        "bg_mode": "default",
        # 蒙层强度：自定义照片往往需要更厚的白蒙层才压得住文字
        "bg_dim": "medium",
        # 鼠标轨迹特效开关（on / off）
        "ui_trail": "on",
        # 人设档位的本地兜底副本（配置接口之外的保险）
        "persona_level": "full",
    }
    _BG_MAX_BYTES = 8 * 1024 * 1024
    _BG_MIME_EXT: dict[str, str] = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }

    # ── 自定义背景 ─────────────────────────────────────────────
    def _bg_dir(self) -> Path:
        path = self.data_dir / "backgrounds"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _bg_file(self) -> Optional[Path]:
        """当前自定义背景文件（按 prefs 里的 bg_file 找，找不到就扫目录）。"""
        prefs = self._load_prefs()
        name = _safe_str(prefs.get("bg_file")).strip()
        if name:
            candidate = (self._bg_dir() / Path(name).name).resolve()
            root = self._bg_dir().resolve()
            if candidate == root or root in candidate.parents:
                if candidate.is_file():
                    return candidate
        for suffix in (".png", ".jpg", ".webp", ".gif"):
            candidate = self._bg_dir() / f"custom{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def _background_state(self) -> dict[str, Any]:
        prefs = self._load_prefs()
        custom = self._bg_file()
        mode = _safe_str(prefs.get("bg_mode"), "default").strip() or "default"
        if mode not in ("default", "custom", "plain"):
            mode = "default"
        if mode == "custom" and custom is None:
            mode = "default"  # 图没了就退回默认，别把界面弄成一片空白
        return {
            "mode": mode,
            "dim": _safe_str(prefs.get("bg_dim"), "medium").strip() or "medium",
            "has_custom": custom is not None,
            "custom_bytes": custom.stat().st_size if custom is not None else 0,
            "custom_name": custom.name if custom is not None else "",
            # 面板用自己的端口取图；宿主托管时前端会换成插件端口
            "custom_path": "/bg/custom",
            "default_image": "bg.jpg",
        }

    def _bg_asset(self) -> Optional[tuple[bytes, str]]:
        """把用户上传的背景图发出去（供 CSS 直接引用）。"""
        path = self._bg_file()
        if path is None:
            return None
        try:
            return path.read_bytes(), guess_mime(path.name)
        except Exception as exc:
            self.logger.warning("[study_copilot] 读取背景图失败: %s", exc)
            return None

    def _import_pad_audio(self, files: list) -> tuple[int, int]:
        """把用户在面板里选的本机音源写进 data/mikutap_audio/。

        文件是**用户自己在自己机器上选的**，插件只负责存到自己的数据目录，
        发行包里不含任何音频。单文件上限 8MB、总数上限 120 个，避免误选整个音乐库。
        """
        target_dir = self.data_dir / minigames.PAD_AUDIO_DIR
        saved = 0
        skipped = 0
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.warning("[study_copilot] 音源目录创建失败: %s", exc)
            return 0, len(files)
        for item in files[:120]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            name = Path(_safe_str(item.get("name"))).name
            if not name or Path(name).suffix.lower() not in minigames.AUDIO_EXTS:
                skipped += 1
                continue
            raw = _safe_str(item.get("data") or item.get("data_base64")).strip()
            if not raw:
                skipped += 1
                continue
            _, _, encoded = raw.partition(",")
            try:
                blob = base64.b64decode(encoded or raw, validate=False)
            except Exception:
                skipped += 1
                continue
            if not blob or len(blob) > self._BG_MAX_BYTES:
                skipped += 1
                continue
            try:
                (target_dir / name).write_bytes(blob)
                saved += 1
            except Exception:
                skipped += 1
        self.logger.info("[study_copilot] 本地音源导入：成功 %d，跳过 %d", saved, skipped)
        return saved, skipped

    def _pad_audio_asset(self, name: str) -> Optional[tuple[bytes, str]]:
        """把用户自己放进 data/mikutap_audio/ 的音源发出去。

        这些文件**不进安装包**：Mikutap 不是开源许可（作者限定非盈利公共使用，
        音源还是初音未来的采样），商用分发不行。放在用户本机、就地读，
        个人非商业自用正好是作者条款允许的范围。
        """
        target = (self.data_dir / minigames.PAD_AUDIO_DIR / Path(name or "").name).resolve()
        try:
            base = (self.data_dir / minigames.PAD_AUDIO_DIR).resolve()
        except Exception:
            return None
        if base not in target.parents or not target.is_file():
            return None
        if target.suffix.lower() not in minigames.AUDIO_EXTS:
            return None
        try:
            return target.read_bytes(), guess_mime(target.name)
        except Exception as exc:
            self.logger.warning("[study_copilot] 读取本地音源失败 %s: %s", name, exc)
            return None

    def _save_background(self, body: dict) -> dict[str, Any]:
        payload = body or {}
        action = _safe_str(payload.get("action"), "mode").strip() or "mode"
        prefs = dict(self._load_prefs())

        if action == "upload":
            raw = _safe_str(payload.get("image_base64") or payload.get("image")).strip()
            if not raw:
                return {"ok": False, "error": "没有收到图片数据。", "background": self._background_state()}
            if raw.startswith("data:"):
                head, _, encoded = raw.partition(",")
                mime = head[5:].split(";")[0].strip().lower()
            else:
                encoded, mime = raw, "image/png"
            ext = self._BG_MIME_EXT.get(mime)
            if not ext:
                return {
                    "ok": False,
                    "error": f"不支持的图片格式：{mime or '未知'}（支持 PNG / JPG / WebP / GIF）",
                    "background": self._background_state(),
                }
            try:
                blob = base64.b64decode(encoded, validate=False)
            except Exception:
                return {"ok": False, "error": "图片数据解不开，可能上传中断了。", "background": self._background_state()}
            if not blob:
                return {"ok": False, "error": "图片是空的。", "background": self._background_state()}
            if len(blob) > self._BG_MAX_BYTES:
                return {
                    "ok": False,
                    "error": f"图片太大（{len(blob) / 1048576:.1f}MB），请压到 {self._BG_MAX_BYTES // 1048576}MB 以内。",
                    "background": self._background_state(),
                }
            # 先清掉旧图，避免 png/jpg 两份并存时取错
            for suffix in (".png", ".jpg", ".webp", ".gif"):
                stale = self._bg_dir() / f"custom{suffix}"
                if stale.is_file():
                    stale.unlink(missing_ok=True)
            target = self._bg_dir() / f"custom{ext}"
            target.write_bytes(blob)
            prefs["bg_mode"] = "custom"
            prefs["bg_file"] = target.name
            self._prefs = prefs
            self._write_prefs(prefs)
            self.logger.info("[study_copilot] 自定义背景已保存: %s（%d 字节）", target.name, len(blob))
            return {"ok": True, "message": "背景已换成你上传的图。", "background": self._background_state()}

        if action == "reset":
            # 只切回默认，不删文件——用户还能再切回来
            prefs["bg_mode"] = "default"
            self._prefs = prefs
            self._write_prefs(prefs)
            return {"ok": True, "message": "已恢复插件自带的背景图。", "background": self._background_state()}

        mode = _safe_str(payload.get("mode"), prefs.get("bg_mode", "default")).strip() or "default"
        if mode not in ("default", "custom", "plain"):
            return {"ok": False, "error": f"未知的背景模式：{mode}", "background": self._background_state()}
        if mode == "custom" and self._bg_file() is None:
            return {"ok": False, "error": "还没有上传过背景图。", "background": self._background_state()}
        prefs["bg_mode"] = mode
        dim = _safe_str(payload.get("dim")).strip()
        if dim in ("light", "medium", "strong"):
            prefs["bg_dim"] = dim
        self._prefs = prefs
        self._write_prefs(prefs)
        return {"ok": True, "background": self._background_state()}

    def _write_prefs(self, prefs: dict[str, str]) -> None:
        try:
            self._prefs_path.write_text(
                json.dumps(prefs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            self.logger.warning("[study_copilot] 面板偏好写入失败: %s", exc)

    def _load_prefs(self) -> dict[str, str]:
        cached = self._prefs
        if isinstance(cached, dict):
            return cached
        data = dict(self._PREFS_DEFAULT)
        try:
            if self._prefs_path.exists():
                raw = json.loads(self._prefs_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key in self._PREFS_DEFAULT:
                        value = _safe_str(raw.get(key)).strip()
                        if value:
                            data[key] = value[:32]
        except Exception as exc:
            self.logger.warning("[study_copilot] 面板偏好读取失败，用默认值: %s", exc)
        self._prefs = data
        return data

    def _api_prefs(self, body: dict) -> dict:
        payload = body or {}
        prefs = dict(self._load_prefs())
        for key in self._PREFS_DEFAULT:
            value = _safe_str(payload.get(key)).strip()
            if value:
                prefs[key] = value[:32]
        self._prefs = prefs
        self._write_prefs(prefs)
        return {"ok": True, "prefs": prefs, "background": self._background_state()}

    # ── 面板 ──────────────────────────────────────────────────
    def _panel_html(self) -> str:
        path = self.static_dir / "index.html"
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return "<html><body><p>面板页面缺失喵。</p></body></html>"

    def _static_asset(self, rel: str) -> Optional[tuple[bytes, str]]:
        """面板静态资源（背景图 / 字体 / 图标）。

        页面由宿主托管时这层用不上（宿主自己会发静态文件）；但页面由插件自己的
        端口托管时，``bg.jpg``、``fonts/*.woff2`` 都走这里，否则全是 404。
        只允许读 static/ 目录内的文件，防目录穿越。
        """
        rel = (rel or "").strip().lstrip("/").split("?", 1)[0]
        if rel in ("bg/custom", "bg/custom.jpg", "bg/custom.png"):  # 用户上传的背景图
            return self._bg_asset()
        if rel.startswith("pad-audio/"):                          # 用户自备的音源（非商业自用）
            return self._pad_audio_asset(rel.split("/", 1)[1])
        if not rel or rel.endswith("/"):
            rel = "index.html"
        try:
            root = self.static_dir.resolve()
            target = (root / rel).resolve()
        except Exception:
            return None
        if target != root and root not in target.parents:
            return None
        if not target.is_file():
            return None
        try:
            return target.read_bytes(), guess_mime(target.name)
        except Exception as exc:
            self.logger.warning("[study_copilot] 静态资源读取失败 %s: %s", rel, exc)
            return None

    def _start_panel(self) -> None:
        endpoints = {
            ("GET", "/api/status"): self._api_status,
            ("POST", "/api/prefs"): self._api_prefs,
            ("POST", "/api/persona"): self._api_persona,
            ("POST", "/api/background"): self._api_background,
            ("POST", "/api/game"): self._api_game,
            ("GET", "/api/game"): self._api_game,
            ("POST", "/api/progress"): self._api_progress,
            ("GET", "/api/progress"): self._api_progress,
            ("POST", "/api/memory"): self._api_memory,
            ("GET", "/api/memory"): self._api_memory,
            ("POST", "/api/profile"): self._api_profile,
            ("POST", "/api/plan"): self._api_plan,
            ("POST", "/api/quiz"): self._api_quiz,
            ("POST", "/api/teach"): self._api_teach,
            ("POST", "/api/diagnose"): self._api_diagnose,
            ("POST", "/api/vision"): self._api_vision,
            ("POST", "/api/forecast"): self._api_forecast,
            ("POST", "/api/comfort"): self._api_comfort,
            ("POST", "/api/search"): self._api_search,
            ("POST", "/api/resources"): self._api_resources,
            ("POST", "/api/platforms"): self._api_platforms,
            ("POST", "/api/connect"): self._api_connect,
            ("POST", "/api/disconnect"): self._api_disconnect,
            ("POST", "/api/sync"): self._api_sync,
        }
        port = find_open_port(self.panel_port)
        server = PanelServer(port, self._panel_html, endpoints, static_resolver=self._static_asset)
        if server.start():
            self._panel_server = server
            self.logger.info("[study_copilot] 面板已启动: http://127.0.0.1:{}", port)
            try:
                self.register_static_ui("static")
            except Exception as exc:
                self.logger.warning("[study_copilot] static UI 注册失败: %s", exc)
        else:
            self.logger.warning("[study_copilot] 面板启动失败（端口占用）")

    def _api_status(self, _body: dict) -> dict:
        profile = self._effective_profile()
        stored = self.store.get_profile()
        model = self._model_config()
        platforms = self.connectors.available() if self.connectors else []
        return {
            "ok": True,
            "port": self._panel_server.port if self._panel_server else self.panel_port,
            "profile": profile,
            "stored": bool(stored),
            "exam_types": list_exam_types(),
            "sources": list_sources(),
            "platforms": platforms,
            "overview": self.store.overview(),
            "latest_plan": self.store.latest_plan(),
            "latest_diagnosis": self.store.latest_diagnosis(),
            "network": {
                "enabled": self.network_enabled,
                "free_source_search": self.free_source_search,
                "platform_sync": self.platform_sync_enabled,
                "robots": self.crawl_respect_robots,
                "interval_ms": self.crawl_interval_ms,
            },
            "model": {
                "model": _safe_str(model.get("model")),
                "base_url": _safe_str(model.get("base_url")),
                "configured": bool(model.get("model") and model.get("base_url") and model.get("api_key")),
            },
            "psychology": {"enabled": self.psychology_enabled, "level": self.psychology_level},
            "persona": {"level": self.persona_level, "teaching_mode": self.teaching_mode},
            "progress": self._progress_brief(),
            "prefs": self._load_prefs(),
            "background": self._background_state(),
        }

    def _api_profile(self, body: dict) -> dict:
        payload = {key: value for key, value in (body or {}).items() if value not in (None, "")}
        if payload.get("exam_type"):
            payload["exam_type"] = resolve_exam_type(_safe_str(payload.get("exam_type")))
        saved = self.store.save_profile(payload)
        return {"ok": True, "profile": self._effective_profile(), "saved": saved}

    def _run_async(self, factory, timeout: float = 120.0) -> dict:
        """面板线程里安全地跑一段协程，统一成 dict 返回给前端。

        注意：这里**不能**再用 ``if not bridge.ready(): return "尚未就绪"`` 提前短路。
        宿主可能把 startup 跑在临时循环里，那种情况下 "没就绪" 是常态而不是错误，
        直接短路会让面板永远不可用；现在由 :class:`AsyncBridge` 自己兜底
        （宿主循环死了就用插件私有循环），真出不来才报错，并把诊断信息带出来。
        """
        try:
            result = self._bridge.call(factory, timeout=timeout)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "bridge": self._bridge.describe()}
        if isinstance(result, dict):
            return result
        return {"ok": True, "text": result}

    def _api_plan(self, body: dict) -> dict:
        horizon = _safe_str((body or {}).get("horizon"), "long")
        return self._run_async(lambda: self._plan(horizon))

    def _api_quiz(self, body: dict) -> dict:
        payload = body or {}
        topic = _safe_str(payload.get("topic"))
        subject = _safe_str(payload.get("subject"))
        stage = _safe_str(payload.get("stage"), "auto")
        count = _safe_int(payload.get("count"), self.quiz_count)
        return self._run_async(lambda: self._quiz(topic, subject, stage, count))

    def _api_teach(self, body: dict) -> dict:
        payload = body or {}
        topic = _safe_str(payload.get("topic"))
        subject = _safe_str(payload.get("subject"))
        stage = _safe_str(payload.get("stage"), "auto")
        return self._run_async(lambda: self._teach(topic, subject, stage))

    def _api_diagnose(self, body: dict) -> dict:
        payload = body or {}
        text = _safe_str(payload.get("text"))
        subject = _safe_str(payload.get("subject"))
        return self._run_async(lambda: self._diagnose(text, subject))

    def _api_vision(self, body: dict) -> dict:
        """面板上传截图（base64）→ 识图诊断；也支持用聊天里最近捕获的图。"""
        payload = body or {}
        subject = _safe_str(payload.get("subject"))
        question = _safe_str(payload.get("question"))
        image_b64 = _safe_str(payload.get("image_base64")).strip()
        if image_b64:
            import base64
            import binascii

            header, _, b64_data = image_b64.partition(",")
            mime = "image/png"
            if header.startswith("data:"):
                mime = header[5:].split(";", 1)[0] or mime
                b64_data = b64_data or header
            try:
                raw = base64.b64decode(b64_data, validate=False)
            except (binascii.Error, ValueError):
                return {"ok": False, "error": "图片数据不是合法的 base64，请重新选择文件。"}
            if len(raw) > 12 * 1024 * 1024:
                return {"ok": False, "error": "图片超过 12MB，先压一压再发喵。"}
            self.last_capture = {
                "text": _safe_str(self.last_capture.get("text")),
                "images": [{"data": raw, "mime": mime}],
                "ts": time.time(),
            }
        return self._run_async(lambda: self._vision_diagnose(question, subject))

    def _api_forecast(self, _body: dict) -> dict:
        return self._run_async(self._forecast)

    def _api_comfort(self, body: dict) -> dict:
        text = _safe_str((body or {}).get("text"))
        return self._run_async(lambda: self._comfort(text))

    def _api_search(self, body: dict) -> dict:
        """检索并做内容分析；顺带把平台直链一起返回，前端可以列成一排。"""
        payload = body or {}
        query = _safe_str(payload.get("query")).strip()
        subject = _safe_str(payload.get("subject")).strip()
        profile = self._effective_profile()
        full = build_query(query, subject, profile["exam_type"]) if query else ""
        result = self._run_async(lambda: self._search(query, subject), timeout=180.0)
        if full:
            result["links"] = [
                {"name": name, "url": url, "note": item_note} for name, url, item_note in build_links(full)
            ]
        return result

    def _api_resources(self, body: dict) -> dict:
        """免费资源清单：只给平台搜索直链，不抓取，所以必定可用且秒回。"""
        payload = body or {}
        keyword = _safe_str(payload.get("keyword")).strip()
        subject = _safe_str(payload.get("subject")).strip()
        profile = self._effective_profile()
        query = build_query(keyword, subject, profile["exam_type"]) if keyword else ""
        return {
            "ok": bool(query),
            "keyword": keyword,
            "text": format_resource_plan(query) if query else "",
            "links": [
                {"name": name, "url": url, "note": item_note}
                for name, url, item_note in build_links(query)
            ]
            if query
            else [],
        }


    def _api_platforms(self, _body: dict) -> dict:
        if self.connectors is None:
            return {"ok": False, "error": "平台组件未初始化"}
        return {"ok": True, "platforms": self.connectors.available()}

    def _api_connect(self, body: dict) -> dict:
        if not self.platform_sync_enabled:
            return {"ok": False, "error": "平台同步已在配置里关闭"}
        if self.connectors is None:
            return {"ok": False, "error": "平台组件未初始化"}
        payload = body or {}
        return self.connectors.connect(
            _safe_str(payload.get("platform")),
            _safe_str(payload.get("username")),
            _safe_str(payload.get("password")),
            _safe_str(payload.get("captcha")),
            _safe_str(payload.get("base_url")),
            remember=_safe_bool(payload.get("remember"), True),
        )

    def _api_disconnect(self, body: dict) -> dict:
        if self.connectors is None:
            return {"ok": False, "error": "平台组件未初始化"}
        return self.connectors.disconnect(_safe_str((body or {}).get("platform")))

    def _api_sync(self, body: dict) -> dict:
        if self.connectors is None:
            return {"ok": False, "error": "平台组件未初始化"}
        payload = body or {}
        platform = _safe_str(payload.get("platform"))
        base_url = _safe_str(payload.get("base_url"))
        return self._run_async(lambda: self._sync_platform(platform, base_url), timeout=150.0)

    # ── 内部业务 ──────────────────────────────────────────────
    async def _plan(self, horizon: str = "long") -> str:
        await self._ensure_ready()
        profile = self._effective_profile()
        mastery = await asyncio.to_thread(self.store.mastery_map)
        plan = build_plan(profile, mastery, horizon=horizon or "long")
        await asyncio.to_thread(
            self.store.save_plan,
            f"{get_profile(profile['exam_type']).name} · 剩余 {plan.days_left} 天",
            "",
            plan.exam_date,
            plan.as_dict(),
        )
        await asyncio.to_thread(self.progress.award, "plan", note="生成学习计划")
        return format_plan(plan)

    async def _diagnose(self, text: str, subject: str = "") -> str:
        await self._ensure_ready()
        profile = self._effective_profile()
        result = await asyncio.to_thread(diagnose, self.store, profile, text, subject, profile["exam_type"])
        base = format_diagnosis(result)
        if text:
            # 先把学生的原话记进短期记忆：之后追问「刚才那个第 3 题」要靠它
            await asyncio.to_thread(
                self.memory.remember_turn,
                "user",
                text,
                topic=(result.matched[0].name if result.matched else subject),
                ref_kind="diagnose",
            )
        if not text:
            await asyncio.to_thread(self.store.save_diagnosis, result.summary, result.as_dict())
            return base
        system = (
            "你是资深备考诊断师。结合学生给的材料、所在地区与学校情况，做一份务实的诊断。\n"
            f"{guard.FACT_RULES}\n"
            "输出要求：\n"
            "1. 先一句话回应他这次给的材料的核心事实（哪几道题错了、什么分数），再开始分析；\n"
            "2. 材料里暴露的具体问题，按知识点归类，不要泛泛而谈；\n"
            "3. 区分『会但做错』（步骤/习惯问题）与『真的不会』（知识漏洞）；\n"
            "4. 按提分性价比排出接下来该补的 3 个知识点，并说明为什么是它们；\n"
            "5. 地区/学校没填就按没填处理，不要替他假设所在省份的卷种与分数线。\n"
            f"{guard.layout_rule()}\n"
            "不要吹捧，也不要打击，说实话；材料没提到的，不要写。"
        )
        user = (
            f"考试：{get_profile(profile['exam_type']).name}｜地区：{profile.get('region') or '未填写'}"
            f"｜学校：{profile.get('school') or '未填写'}｜年级：{profile.get('grade') or '未填写'}\n"
            f"本地扫描结果：\n{base}\n\n"
            f"学生提供的材料（可能是题目、试卷、成绩或一段自述）：\n{text[:4000]}"
        )
        try:
            detail = await self._llm_chat(
                system,
                user,
                topic=(result.matched[0].name if result.matched else subject),
                keywords=subject or (result.matched[0].subject if result.matched else ""),
                ref_kind="diagnose",
                record=True,
            )
        except SdkError:
            detail = "（模型不可用，以上为本地扫描结果）"
        payload = result.as_dict()
        payload["detail"] = detail
        await asyncio.to_thread(self.store.save_diagnosis, result.summary, payload)
        await asyncio.to_thread(self.progress.award, "diagnose", note="诊断一次")
        # 把薄弱点沉淀成长期记忆：短期记忆会过期，但「哪个点反复错」应该一直记得
        for row in result.weak[:3]:
            await asyncio.to_thread(
                self.memory.remember_fact,
                "weakness",
                f"weak-{row.get('name')}",
                f"{row.get('name')}：{row.get('reason')}（{row.get('roi')}）",
                topic=_safe_str(row.get("subject")),
            )
        return fmt.join(base, fmt.section("深度分析", detail))

    async def _forecast(self) -> str:
        await self._ensure_ready()
        profile = self._effective_profile()
        mastery = await asyncio.to_thread(self.store.mastery_map)
        days = self._days_left(profile)
        forecast = build_forecast(profile, mastery, days)
        return format_forecast(forecast)

    async def _teach(self, topic: str, subject: str = "", stage: str = "auto") -> str:
        await self._ensure_ready()
        if not topic:
            raise SdkError("想让我讲什么？给个知识点或者贴一段题目喵。")
        profile = self._effective_profile()
        point = await self._resolve_point(topic, subject)
        if point is None:
            raise SdkError(f"没能定位到知识点『{topic}』，换个更具体的说法试试喵。")
        mastery_map = await asyncio.to_thread(self.store.mastery_map)
        level = float(mastery_map.get(point.id, 0.5))
        resolved_stage = decide_stage(level, stage or self.quiz_stage)
        resources = ""
        if self.network_enabled and self.free_source_search:
            query = build_query(point.name, _safe_str(subject), profile["exam_type"])
            scraped = await self._search_resources(query, point.subject, point.id)
            resources = self._resource_hints(query, scraped)
        context = TeachContext(
            point=point,
            stage=resolved_stage,
            mastery=level,
            style=self.teach_style,
            exam_name=get_profile(profile["exam_type"]).name,
            region=_safe_str(profile.get("region")),
        )
        system, user = build_teach_prompt(context, resources, persona=self.persona_level)
        text = await self._llm_chat(
            system,
            user,
            topic=point.name,
            keywords=point.subject,
            ref_kind="teach",
            record=True,
        )
        await asyncio.to_thread(self.store.add_session, "teach", f"{point.name}｜{resolved_stage}")
        await asyncio.to_thread(self.progress.award, "teach", note=point.name)
        return fmt.join(format_point(point), fmt.section("讲解", text))

    async def _quiz(self, topic: str, subject: str = "", stage: str = "auto", count: int = 0) -> str:
        await self._ensure_ready()
        if not topic:
            raise SdkError("要出哪个知识点的题？说个名字喵。")
        profile = self._effective_profile()
        point = await self._resolve_point(topic, subject)
        if point is None:
            raise SdkError(f"没能定位到知识点『{topic}』，换个更具体的说法试试喵。")
        mastery_map = await asyncio.to_thread(self.store.mastery_map)
        level = float(mastery_map.get(point.id, 0.5))
        resolved_stage = decide_stage(level, stage or self.quiz_stage)
        ok, warning = validate_question_plan(point, resolved_stage)
        if not ok:
            raise SdkError(warning)
        total = count or self.quiz_count
        resources = ""
        if self.network_enabled and self.free_source_search:
            query = build_query(point.name, point.subject, profile["exam_type"])
            scraped = await self._search_resources(query, point.subject, point.id)
            resources = self._resource_hints(query, scraped)
        system, user = build_quiz_prompt(
            point,
            resolved_stage,
            total,
            get_profile(profile["exam_type"]).name,
            _safe_str(profile.get("region")),
            self.quiz_with_traps,
            resources,
            persona=self.persona_level,
        )
        payload = await self._llm_json(system, user)
        questions = _extract_questions(payload)
        if not questions:
            raise SdkError("模型这次没吐出合法的题目，再试一次喵。")
        await asyncio.to_thread(self.store.add_session, "quiz", f"{point.name}｜{resolved_stage}｜{len(questions)} 题")
        head = quiz_intro(point, resolved_stage, len(questions))
        if warning:
            head += f"\n{warning}"
        body = f"{head}\n{format_questions(questions)}"
        # 记进短期记忆：他后面答错了、或问「第二题再讲讲」，需要知道出过什么题
        await asyncio.to_thread(
            self.memory.remember_turn, "user", f"要 {point.name} 的 {total} 道题（{resolved_stage}）",
            topic=point.name, ref_kind="quiz-request", session="quiz",
        )
        await asyncio.to_thread(
            self.memory.remember_turn, "assistant", body,
            topic=point.name, ref_kind="quiz", session="quiz",
        )
        await asyncio.to_thread(self.progress.award, "quiz", note=point.name)
        return body

    async def _grade(self, topic: str, question: str, answer: str) -> str:
        await self._ensure_ready()
        if not (question and answer):
            raise SdkError("把题目和你的答案都发给我喵。")
        profile = self._effective_profile()
        # 没给知识点名时，用题干去反查，别直接抛错
        point = await self._resolve_point(topic or question[:120], "")
        if point is None:
            raise SdkError(f"没能定位到知识点『{topic or '（未指定）'}』，告诉我是哪一章的喵。")
        system, user = build_grade_prompt(
            point,
            question,
            answer,
            get_profile(profile["exam_type"]).name,
            persona=self.persona_level,
        )
        payload = await self._llm_json(system, user)
        data = payload if isinstance(payload, dict) else {}
        correct = _safe_bool(data.get("correct"), False)
        before_level = float((await asyncio.to_thread(self.store.mastery_map)).get(point.id, 0.5))
        level = await asyncio.to_thread(self.store.update_mastery, point.id, point.subject, correct)
        # 经验：答对 10 / 答错 3；掌握度涨了另按幅度给（单次上限 20）
        await asyncio.to_thread(
            self.progress.award, "attempt_correct" if correct else "attempt_wrong", note=point.name
        )
        if correct:
            await asyncio.to_thread(self.progress.award_mastery_up, before_level, level, point.name)
        advice = _safe_str(data.get("advice"))
        reason = _safe_str(data.get("reason"))
        lines = [
            f"判定：{'对了' if correct else '错了'}｜{point.name} 掌握度更新为 {level:.0%}",
        ]
        if reason:
            lines.append(f"原因：{reason}")
        if advice:
            lines.append(f"建议：{advice}")
        verdict = lines[0]
        # 批改结果写进记忆：短期留原题与作答，答错的错因沉淀成长期记忆
        await asyncio.to_thread(
            self.memory.remember_turn, "user", f"作答〔{point.name}〕：{answer[:600]}",
            topic=point.name, ref_kind="grade", session="grade",
        )
        await asyncio.to_thread(
            self.memory.remember_turn, "assistant", "\n".join(lines),
            topic=point.name, ref_kind="grade", session="grade",
        )
        if not correct and reason:
            await asyncio.to_thread(
                self.memory.remember_fact,
                "mistake",
                f"mistake-{point.id}",
                f"{point.name} 答错：{reason}" + (f"；改进：{advice}" if advice else ""),
                topic=point.subject,
            )
        await asyncio.to_thread(self.store.add_session, "grade", verdict[:120])
        return "\n".join(lines)

    async def _search(self, query: str, subject: str = "") -> str:
        await self._ensure_ready()
        if not query:
            raise SdkError("想找什么资料？给个关键词喵。")
        if not (self.network_enabled and self.free_source_search):
            raise SdkError("免费资源检索当前是关闭的，可以在面板或配置里打开。")
        profile = self._effective_profile()
        full_query = build_query(query, subject, profile["exam_type"])
        head = fmt.section(
            "检索条件",
            "\n".join([fmt.kv("检索词", full_query), fmt.kv("你的需求", query)]),
        )
        items = await self._collect_resources(full_query, need=query, limit=4)
        if not items:
            # 抓不到不等于没有：把各平台的搜索直链给出来，用户点开就是结果页
            return fmt.join(
                head,
                fmt.note("这次没能抓取到可用条目（可能是网络或站点风控），先给你各平台的搜索直链。"),
                format_resource_plan(full_query),
            )
        try:
            rows = [item.as_dict() for item in items]
            await asyncio.to_thread(self.store.save_resources, "", rows)
        except Exception:
            pass
        return fmt.join(head, format_resources(items, limit=6))

    def _resource_hints(self, keyword: str, scraped: str = "") -> str:
        """给模型的「资源线索」：平台搜索直链（必定可用）+ 抓到的具体条目（有就带上）。

        为什么要给模型这些：它自己就能联网检索，缺的不是能力而是**准确的落点**——
        哪个平台适合找什么、关键词怎么拼。把落点交出去，模型就能去读、去核，
        而不是凭空回忆课程名。
        """
        links = build_links(keyword)
        if not links:
            return scraped
        rows = [f"- {name}：{url}" for name, url, _note in links]
        text = fmt.section("可以查的免费资源（搜索直链已带关键词）", "\n".join(rows))
        if scraped:
            text += "\n\n" + fmt.section("关键词检索到的条目", scraped)
        text += "\n\n" + fmt.note(
            "你具备联网检索能力，可以据此核实具体的课程名/视频名与知识点表述；"
            "但不要把没查到的东西当成事实说出来。"
        )
        return text

    async def _comfort(self, text: str = "") -> str:
        await self._ensure_ready()
        if not self.psychology_enabled:
            raise SdkError("心理疏导当前是关闭的。")
        profile = self._effective_profile()
        mastery = await asyncio.to_thread(self.store.mastery_map)
        overview = await asyncio.to_thread(self.store.overview)
        attempts = int((overview.get("attempts") or {}).get("total") or 0)
        average = sum(mastery.values()) / len(mastery) if mastery else 0.5
        # 掌握度只有真做过题才算数；否则 0.5 只是默认值，绝不能当事实讲给他听
        mastery_known = bool(mastery) and attempts > 0
        exam_date = _safe_str(profile.get("exam_date")).strip()
        result = counsel(
            profile["exam_type"],
            self._days_left(profile),
            average,
            mood="",
            text=text,
            level=self.psychology_level,
            mastery_known=mastery_known,
            attempts=attempts,
            days_estimated=not exam_date,
            exam_date=exam_date,
        )
        if result.risk:
            await asyncio.to_thread(self.store.add_session, "comfort-risk", "检测到风险信号")
            return format_counsel(result)
        # 疏导以情绪陪伴为主：教学模式只把人设降到「轻度」，不降到完全关闭——
        # 冷冰冰的疏导反而没用。这一点和讲解/批改不同，是有意为之。
        comfort_persona = "light" if self.teaching_mode else self.persona_level
        system, user = build_comfort_prompt(
            result, self.catgirl_name, self.psychology_level, persona=comfort_persona
        )
        if text:
            await asyncio.to_thread(
                self.memory.remember_turn, "user", text, ref_kind="comfort", session="comfort"
            )
        try:
            words = await self._llm_chat(
                system,
                user,
                timeout=min(45.0, self.llm_timeout),
                keywords=result.mood_label,
                ref_kind="comfort",
                record=True,
            )
        except SdkError:
            words = format_counsel(result)
        await asyncio.to_thread(self.store.add_session, "comfort", result.mood_label)
        return words

    async def _sync_platform(self, platform_id: str, base_url: str = "") -> dict:
        await self._ensure_ready()
        if not self.platform_sync_enabled:
            return {"ok": False, "error": "平台同步已在配置里关闭"}
        if self.connectors is None:
            return {"ok": False, "error": "平台组件未初始化"}
        if not platform_id:
            return {"ok": False, "error": "请指定要同步的平台"}
        raw = await asyncio.to_thread(self.connectors.fetch_raw, platform_id, base_url)
        if not raw.get("ok"):
            return raw
        pages = [page for page in raw.get("pages", []) if page.get("ok")]
        if not pages:
            return {"ok": False, "error": "登录后没有读到任何页面，可能是登录态过期或页面结构已变化"}
        joined = "\n\n".join(f"[{page['url']}]\n{page.get('text', '')[:2500]}" for page in pages[:3])
        system = (
            "你把学习平台的页面文本整理成结构化数据。只整理页面里真实出现的内容，不要编造。\n"
            "返回 JSON：{\"courses\":[{\"name\":\"课程名\",\"progress\":\"进度描述或百分比\","
            "\"pending\":\"待办数量或描述\"}],\"summary\":\"一句话总结当前学习状态\","
            "\"suggestions\":[\"两条最该先处理的事\"]}"
        )
        try:
            payload = await self._llm_json(system, joined)
        except SdkError as exc:
            return {"ok": False, "error": str(exc), "pages": len(pages)}
        data = payload if isinstance(payload, dict) else {}
        await asyncio.to_thread(self.store.add_session, "sync", f"{platform_id}｜{_safe_str(data.get('summary'))}")
        return {"ok": True, "platform": platform_id, "data": data, "pages": len(pages)}

    # ── 生命周期 ──────────────────────────────────────────────
    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        try:
            await self._startup_inner()
        except Exception as exc:  # 启动失败不要让整个插件进程被判死
            _dump_crash("startup")
            self.logger.warning("[study_copilot] 启动过程出错，已降级运行: %s", exc)

    async def _startup_inner(self) -> None:
        await self._load_config()
        self._loop = asyncio.get_running_loop()
        self._bridge.bind(self._loop)
        if self.network_enabled:
            self.crawler = Crawler(
                self.data_dir,
                interval_ms=self.crawl_interval_ms,
                respect_robots=self.crawl_respect_robots,
                timeout=self.crawl_timeout,
                max_pages=self.crawl_max_pages,
            )
            self.connectors = ConnectorManager(self.crawler, self.data_dir, self.admin_password)
            self.searcher = ResourceSearcher(self.crawler)
        self._start_panel()
        self.logger.info("[study_copilot] 事件循环桥：%s", self._bridge.describe())
        self.logger.info(
            "[study_copilot] 启动：考试=%s 地区=%s 科目=%s 目标=%s 每天=%d 分钟｜联网=%s",
            self.exam_type,
            self.region or "未填",
            self.subjects or "默认",
            self.target_score or "未设",
            self.daily_minutes,
            self.network_enabled,
        )

    @lifecycle(id="shutdown")
    async def on_shutdown(self) -> None:
        if self._panel_server:
            self._panel_server.stop()
        if self.crawler is not None:
            await asyncio.to_thread(self.crawler.save_cookies)
        self.logger.info("[study_copilot] 关闭")

    # ── 入口 ──────────────────────────────────────────────────
    @llm_tool(
        name="study_status",
        description="查看学习助手当前状态：考试档案、掌握度、平台连接、计划与诊断概览。",
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    @plugin_entry(
        id="study_status",
        name="学习助手状态",
        description="返回档案、平台连接、资源检索与计划进度概览。",
        input_schema={"type": "object", "properties": {}},
    )
    async def status_entry(self, **_):
        await self._ensure_ready()
        profile = self._effective_profile()
        overview = await asyncio.to_thread(self.store.overview)
        region = profile.get("region") or "未填写"
        school = profile.get("school") or "未填写"
        grade = profile.get("grade") or "未填写"
        exam_date = profile.get("exam_date") or "未填写"
        lines = [
            f"考试：{get_profile(profile['exam_type']).name}",
            f"地区：{region}｜学校：{school}｜年级：{grade}",
            f"科目：{'、'.join(subject_label(item) for item in self._subjects_list(profile))}",
            f"目标分：{profile.get('target_score') or '未设定'}｜考试日期：{exam_date}｜还剩 {self._days_left(profile)} 天",
            f"每日可用：{profile.get('daily_minutes')} 分钟",
            f"已跟踪知识点：{overview['tracked_points']} 个｜平均掌握度：{overview['average_mastery']:.0%}",
            f"作答记录：{overview['attempts']['total']} 次，正确率 {overview['attempts']['rate']:.0%}",
            f"待复习：{overview['due_reviews']} 项｜已保存计划：{'有' if overview['plan'] else '无'}",
        ]
        if self.connectors is not None:
            connected = [row["id"] for row in self.connectors.available() if row.get("connected")]
            lines.append(f"已连接平台：{'、'.join(connected) if connected else '无'}")
        return Ok("\n".join(lines))

    @llm_tool(
        name="study_profile",
        description="查看或修改学习档案：目标考试、地区、学校、年级、科目、目标分、考试日期、每日学习分钟数。",
        parameters={
            "type": "object",
            "properties": {
                "exam_type": {
                    "type": "string",
                    "description": "考试类型。可选：gaokao 高考 / zhongkao 中考 / cet4 四级 / cet6 六级 / "
                                   "kaoyan 考研 / final 校内通用 / university 大学期末(学分制,GPA) / "
                                   "cert_teacher 教资 / cert_soft 软考 / cert_cpa 注会 / cert_law 法考 / "
                                   "ielts 雅思 / toefl 托福 / ncse 计算机等级考试。也可直接写中文，"
                                   "如『大学期末』『绩点』『保研』『教资』『软考』『注会』『法考』『雅思』",
                },
                "credits": {
                    "type": "string",
                    "description": "大学学分制专用：科目与学分的对应关系，形如 'math_adv:5,english:3,politics:3'。"
                                   "填了它，时间分配与绩点预估才会按学分加权。",
                },
                "region": {"type": "string", "description": "省份或直辖市，例如 广东"},
                "school": {"type": "string", "description": "学校名称"},
                "grade": {"type": "string", "description": "年级，例如 高三"},
                "subjects": {"type": "string", "description": "科目，英文 key 逗号分隔，如 chinese,math,english"},
                "target_score": {"type": "number", "description": "目标总分"},
                "exam_date": {"type": "string", "description": "考试日期 YYYY-MM-DD"},
                "daily_minutes": {"type": "integer", "description": "每天可用于该考试的有效分钟数"},
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="study_profile",
        name="学习档案",
        description="查看或修改考试目标、地区、科目、目标分与可用时间。",
        input_schema={"type": "object", "properties": {}},
    )
    async def profile_entry(
        self,
        exam_type: str = "",
        region: str = "",
        school: str = "",
        grade: str = "",
        subjects: str = "",
        target_score: float = 0,
        exam_date: str = "",
        daily_minutes: int = 0,
        credits: str = "",
        **_,
    ):
        await self._ensure_ready()
        payload = {
            key: value
            for key, value in {
                "exam_type": resolve_exam_type(exam_type) if exam_type else "",
                "region": region,
                "school": school,
                "grade": grade,
                "subjects": subjects,
                "target_score": target_score,
                "exam_date": exam_date,
                "daily_minutes": daily_minutes,
                "credits": credits,
            }.items()
            if value not in ("", 0, None)
        }
        if payload:
            await asyncio.to_thread(self.store.save_profile, payload)
        profile = self._effective_profile()
        overview = format_profile(get_profile(profile["exam_type"]), self._subjects_list(profile))
        return Ok(
            "当前档案：\n"
            f"考试：{get_profile(profile['exam_type']).name}\n"
            f"地区：{profile.get('region') or '未填写'}｜学校：{profile.get('school') or '未填写'}"
            f"｜年级：{profile.get('grade') or '未填写'}\n"
            f"科目：{'、'.join(subject_label(item) for item in self._subjects_list(profile))}\n"
            f"目标分：{profile.get('target_score') or '未设定'}｜考试日期：{profile.get('exam_date') or '未填写'}"
            f"｜还剩 {self._days_left(profile)} 天\n"
            f"每日可用：{profile.get('daily_minutes')} 分钟\n"
            f"学分：{profile.get('credits') or '未填写（大学考试建议填，例：math_adv:5,english:3）'}\n\n"
            f"卷子结构：\n{overview}"
        )

    @llm_tool(
        name="study_plan",
        description="生成学习计划。用户说『帮我规划一下』『怎么安排接下来的复习』『这周学什么』时调用。",
        parameters={
            "type": "object",
            "properties": {
                "horizon": {"type": "string", "description": "long 长期（到考试）或 short 短期（本周）"},
            },
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="study_plan",
        name="生成学习计划",
        description="按目标考试、目标分、剩余天数和每天可用时间生成长期与短期学习计划。",
        input_schema={"type": "object", "properties": {"horizon": {"type": "string"}}},
    )
    async def plan_entry(self, horizon: str = "long", **_):
        try:
            return Ok(self._out(await self._plan(horizon)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_diagnose",
        description=(
            "诊断学习情况。用户提供文字版题目、试卷、成绩单，或问『我哪里薄弱』"
            "『这套卷子暴露了什么问题』时调用。注意：如果用户是发截图而不是打字，"
            "请改用 study_vision 识图诊断。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "题目、试卷、成绩或一段自述"},
                "subject": {"type": "string", "description": "科目 key，如 math；可留空自动判断"},
            },
        },
        timeout=90.0,
    )
    @plugin_entry(
        id="study_diagnose",
        name="学习诊断",
        description="根据题目、试卷、地区与学校推断薄弱知识点，输出诊断报告。",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    async def diagnose_entry(self, text: str = "", subject: str = "", **_):
        try:
            return Ok(self._out(await self._diagnose(text, subject)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_vision",
        description=(
            "识图学习辅助：用户在聊天里发过题目/试卷/作业/成绩单的截图，并让你讲题、"
            "批改、诊断薄弱点或估分时调用。会读取用户最近发的截图，用视觉模型转写成文字，"
            "再走常规诊断链路。用户没发过截图时不要调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户想对这张图问什么，例如『第 3 题怎么做』『这套卷子我哪里弱』"},
                "subject": {"type": "string", "description": "科目 key，如 math；可留空自动判断"},
            },
        },
        timeout=120.0,
    )
    @plugin_entry(
        id="study_vision",
        name="识图诊断",
        description="读取用户最近发的题目/试卷截图，转写后做薄弱点诊断与讲解。",
        input_schema={
            "type": "object",
            "properties": {"question": {"type": "string"}, "subject": {"type": "string"}},
        },
    )
    async def vision_entry(self, question: str = "", subject: str = "", **_):
        await self._ensure_ready()
        if not self.vision_enabled:
            raise SdkError("识图功能当前是关闭的，可以在配置里把 vision_enabled 打开。")
        try:
            return Ok(self._out(await self._vision_diagnose(question, subject)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_forecast",
        description="预估分数。用户问『我能考多少分』『能不能过』『大概什么水平』时调用，给出保守/最可能/理想三档。",
        parameters={"type": "object", "properties": {}},
        timeout=60.0,
    )
    @plugin_entry(
        id="study_forecast",
        name="分数预估",
        description="给出保守、最可能与理想三档分数预期及依据。",
        input_schema={"type": "object", "properties": {}},
    )
    async def forecast_entry(self, **_):
        try:
            return Ok(self._out(await self._forecast()))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_teach",
        description="讲解知识点并配套例题。用户说『讲一下 XX』『这个我不懂』时调用。",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "知识点名称，例如 导数及其应用"},
                "subject": {"type": "string", "description": "科目 key，如 math；可留空"},
                "stage": {"type": "string", "description": "basic/medium/hard/auto"},
            },
        },
        timeout=90.0,
    )
    @plugin_entry(
        id="study_teach",
        name="知识点讲解",
        description="讲解知识点并配套分阶段的例题，必要时先补前置知识。",
        input_schema={"type": "object", "properties": {"topic": {"type": "string"}}},
    )
    async def teach_entry(self, topic: str = "", subject: str = "", stage: str = "auto", **_):
        try:
            return Ok(self._out(await self._teach(topic, subject, stage)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_quiz",
        description="按知识点命题规律出题。用户说『出几道题』『考考我』『给我练练』时调用。",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "知识点名称"},
                "subject": {"type": "string", "description": "科目 key，可留空"},
                "stage": {"type": "string", "description": "basic/medium/hard/auto"},
                "count": {"type": "integer", "description": "题量，默认 3"},
            },
        },
        timeout=90.0,
    )
    @plugin_entry(
        id="study_quiz",
        name="出题测验",
        description="按知识点命题规律出基础、中等易错或综合创新题。",
        input_schema={"type": "object", "properties": {"topic": {"type": "string"}}},
    )
    async def quiz_entry(self, topic: str = "", subject: str = "", stage: str = "auto", count: int = 0, **_):
        try:
            return Ok(self._out(await self._quiz(topic, subject, stage, count)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_grade",
        description="批改学生的作答并更新掌握度。学生给出答案后调用。",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "知识点名称"},
                "question": {"type": "string", "description": "题目原文"},
                "answer": {"type": "string", "description": "学生的答案"},
            },
        },
        timeout=90.0,
    )
    @plugin_entry(
        id="study_grade",
        name="批改作答",
        description="批改一道题的作答，按真实给分标准判定并更新掌握度。",
        input_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )
    async def grade_entry(self, topic: str = "", question: str = "", answer: str = "", **_):
        try:
            return Ok(self._out(await self._grade(topic, question, answer)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_search",
        description="检索免费公开学习资源（国家智慧教育平台、中国大学MOOC、学堂在线、B站学习区等）。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，例如 导数 单调性"},
                "subject": {"type": "string", "description": "科目 key，可留空"},
            },
        },
        timeout=90.0,
    )
    @plugin_entry(
        id="study_search",
        name="检索学习资源",
        description="在免费公开课程与题库资源中检索匹配当前知识点的资料。",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    async def search_entry(self, query: str = "", subject: str = "", **_):
        try:
            return Ok(self._out(await self._search(query, subject)))
        except SdkError as exc:
            return Err(str(exc))

    @llm_tool(
        name="study_resources",
        description=(
            "按知识点/题型生成「去哪找资料」的清单：各免费平台的搜索直链 + 建议关键词。"
            "用户问『有没有推荐的课』『去哪找资料』『有什么视频能看』『给我几个链接』时调用。"
            "你自己也具备联网检索能力——先调用本工具拿到准确落点，再去核实具体课程名。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "知识点或题型，例如 导数 单调性"},
                "subject": {"type": "string", "description": "科目 key，可留空"},
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="study_resources",
        name="免费资源清单",
        description="按知识点给出各免费平台的搜索直链与用法建议。",
        input_schema={
            "type": "object",
            "properties": {"keyword": {"type": "string"}, "subject": {"type": "string"}},
        },
    )
    async def resources_entry(self, keyword: str = "", subject: str = "", **_):
        await self._ensure_ready()
        profile = self._effective_profile()
        text = (keyword or "").strip() or "（未指定）"
        query = build_query(text, subject, profile["exam_type"])
        return Ok(self._out(format_resource_plan(query)))

    @llm_tool(
        name="study_persona",
        description=(
            "切换人设强度 / 教学模式。用户说『开启教学模式』『别用猫娘腔』『正经讲课』"
            "『恢复正常』『可以卖萌了』『人设轻一点』时调用。"
            "教学模式会降低口癖与卖萌，让讲解更严谨结构化。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "description": "full 猫娘人格 / light 轻度人设 / off 教学模式",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "教学模式开关：true 等价于 level=off",
                },
            },
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="study_persona",
        name="切换教学模式",
        description="在猫娘人格与教学模式之间切换，教学模式会降低人设对教学输出的影响。",
        input_schema={
            "type": "object",
            "properties": {
                "level": {"type": "string", "description": "full / light / off"},
                "enabled": {"type": "boolean", "description": "教学模式开关"},
            },
        },
    )
    async def persona_entry(self, level: str = "", enabled: Any = None, **_):
        state = await self._set_persona(level or ("off" if _safe_bool(enabled, False) else "full"))
        return Ok(
            fmt.join(
                f"人设档位已切换为 **{state['label']}**。",
                fmt.note(state["description"]),
            )
        )

    @llm_tool(
        name="study_level",
        description=(
            "查看修行等级、头衔、经验与徽章。用户问『我几级了』『什么头衔』『经验怎么来的』"
            "『有什么成就』时调用。经验只来自真实学习行为（答题、诊断、讲解、复习），"
            "不存在打卡刷分，解释时不要承诺能靠聊天涨经验。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "part": {"type": "string", "description": "level 等级与经验 / badges 徽章 / rules 经验规则"},
            },
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="study_level",
        name="修行等级",
        description="查看当前等级、头衔、经验进度、连击天数与已获得的徽章。",
        input_schema={"type": "object", "properties": {"part": {"type": "string"}}},
    )
    async def level_entry(self, part: str = "level", **_):
        await self._ensure_ready()
        if not self.progress_enabled:
            return Ok("修行等级当前是关闭的（配置里的 progress_enabled）。")
        snapshot = await asyncio.to_thread(self.progress.snapshot)
        wall = await asyncio.to_thread(self.progress.badge_wall)
        want = (part or "level").strip().lower()
        if want in ("rules", "rule", "规则"):
            return Ok(
                fmt.join(
                    fmt.section("经验怎么来的", self.progress.rules_text()),
                    fmt.note("只统计真实学习行为——聊天、点按钮都不给经验。"),
                )
            )
        if want in ("badges", "badge", "徽章"):
            owned = [item for item in wall["all"] if item["owned"]]
            rest = [item for item in wall["all"] if not item["owned"]]
            blocks = [fmt.section(f"已获得（{len(owned)}/{len(wall['all'])}）", fmt.bullets(
                [f"**{item['title']}**　{item['note']}" for item in owned]
            ) or "还没有徽章，先答对一道题。")]
            if rest:
                blocks.append(fmt.section("还没拿到", fmt.bullets(
                    [f"{item['title']}　{item['note']}" for item in rest]
                )))
            return Ok(self._out(fmt.join(*blocks)))
        next_line = (
            f"距下一级还差 **{snapshot.level_need - snapshot.level_exp}** 点经验"
            if snapshot.level_need else "已经是最高级别。"
        )
        return Ok(
            self._out(
                fmt.join(
                    f"现在是 **Lv.{snapshot.level}　{snapshot.title}**（累计 {snapshot.total_exp} 点经验）",
                    fmt.section("升级进度", f"{fmt.bar(snapshot.percent)} {snapshot.level_exp}/{snapshot.level_need or '—'}\n{next_line}"),
                    fmt.section("状态", fmt.bullets([
                        f"连续学习：**{snapshot.streak}** 天",
                        f"今日经验：{snapshot.today_exp}",
                        f"徽章：{len(snapshot.badges)}/{len(wall['all'])}",
                    ])),
                    fmt.note(snapshot.title_note) if snapshot.title_note else "",
                )
            )
        )

    @llm_tool(
        name="study_game",
        description=(
            "查看内置小游戏的记录与成就（切水果）。用户问『我最高分多少』『游戏成就』"
            "『切水果打到几级』时调用。注意：玩游戏不给修行经验，两条线是分开的，"
            "不要暗示玩游戏能提升等级或掌握度。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "part": {"type": "string", "description": "records 记录 / badges 成就 / rules 规则"},
            },
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="study_game",
        name="小游戏记录",
        description="查看切水果的最高分、等级、连击、累计记录与游戏成就。",
        input_schema={"type": "object", "properties": {"part": {"type": "string"}}},
    )
    async def game_entry(self, part: str = "records", **_):
        await self._ensure_ready()
        if not self.game_enabled:
            return Ok("小游戏当前是关闭的（配置里的 game_enabled）。")
        state = await asyncio.to_thread(self.games.state)
        want = (part or "records").strip().lower()
        if want in ("badges", "badge", "成就"):
            owned = [item for item in state["badges"] if item["owned"]]
            rest = [item for item in state["badges"] if not item["owned"]]
            blocks = [
                fmt.section(
                    f"已解锁（{len(owned)}/{len(state['badges'])}）",
                    fmt.bullets([f"**{item['title']}**　{item['note']}" for item in owned]) or "还没解锁成就。",
                )
            ]
            if rest:
                blocks.append(fmt.section("还没解锁", fmt.bullets(
                    [f"{item['title']}　{item['note']}" for item in rest]
                )))
            return Ok(self._out(fmt.join(*blocks)))
        if want in ("rules", "rule", "规则"):
            lv = self.games.config()
            return Ok(
                self._out(
                    fmt.join(
                        fmt.section("玩法", fmt.bullets([
                            "水果从底往上飞，按住鼠标划过去切开它；切得越多分越高",
                            f"漏掉水果扣一条命，一共 {lv['lives']} 条；切到炸弹也扣命（等级越高炸弹越多）",
                            "同一次滑动里连切多个有连击奖励",
                        ])),
                        fmt.section("规则来源", fmt.bullets([
                            f"每 {lv['level_step']} 分升一级，最高 Lv.{lv['level_max']}",
                            "难度随等级上升：出水果更快、下落更快",
                            "**玩游戏不给修行经验**，也不影响掌握度——两条线分开",
                        ])),
                    )
                )
            )
        return Ok(
            self._out(
                fmt.join(
                    fmt.section("切水果 · 记录", self.games.report()),
                    fmt.note("想玩就去面板的「解压」页，切到「切水果」。"),
                )
            )
        )

    @llm_tool(
        name="study_memory",
        description=(
            "查看或清理学习记忆。用户问『你记得我什么』『还记得上次那张卷子吗』"
            "『我哪里反复错』时调用 action=show / search；"
            "说『忘掉刚才』『清空记忆』时调用 forget。"
            "记忆分两级：短期（对话与材料，7 天自动清理）与长期（进度、薄弱点、错因）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "show 查看 / search 搜索 / forget 清除 / purge 立即清理过期短期记忆",
                },
                "query": {"type": "string", "description": "search 时要找的关键词，如知识点名"},
                "scope": {"type": "string", "description": "forget 的范围：short 短期 / all 全部"},
            },
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="study_memory",
        name="学习记忆",
        description="查看、搜索或清理两级记忆（短期对话记忆 + 长期进度记忆）。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "query": {"type": "string"},
                "scope": {"type": "string"},
            },
        },
    )
    async def memory_entry(self, action: str = "show", query: str = "", scope: str = "short", **_):
        await self._ensure_ready()
        kind = (action or "show").strip().lower()
        if kind in ("forget", "clear", "清除", "忘记"):
            removed = await asyncio.to_thread(self.memory.forget, scope=scope or "short")
            label = "短期 + 长期" if (scope or "short") == "all" else "短期"
            return Ok(
                fmt.join(
                    f"已清除**{label}**记忆：{removed.get('turns', 0)} 条对话记录、"
                    f"{removed.get('facts', 0)} 条长期事实。",
                    fmt.note("掌握度与作答记录不在记忆库里，清记忆不会让你之前的练习白做。"),
                )
            )
        if kind in ("purge", "clean", "清理"):
            removed = await asyncio.to_thread(self.memory.purge, force=True)
            return Ok(f"已清理过期的短期记忆 {removed} 条（结论已并入长期记忆）。")
        return Ok(self._out(self.memory_report(query)))

    @llm_tool(
        name="study_platform_sync",
        description="同步本人学习平台的课程、进度与作业（只读）。用户说『同步一下学习通』『看看我还有多少课没看』时调用。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "平台 id：chaoxing/zhihuishu/icourse163/xuetangx/jwc/neea"},
            },
        },
        timeout=120.0,
    )
    @plugin_entry(
        id="study_platform_sync",
        name="同步学习平台",
        description="同步本人学习平台账号的课程、进度与作业数据（只读）。",
        input_schema={"type": "object", "properties": {"platform": {"type": "string"}}},
    )
    async def sync_entry(self, platform: str = "", **_):
        result = await self._sync_platform(platform)
        if not result.get("ok"):
            return Err(_safe_str(result.get("error"), "同步失败"))
        data = result.get("data") or {}
        courses = data.get("courses") or []
        lines = [f"已同步 {result.get('platform')}（读取了 {result.get('pages')} 个页面）"]
        if _safe_str(data.get("summary")):
            lines.append(_safe_str(data.get("summary")))
        for course in courses[:10]:
            lines.append(
                f"  · {_safe_str(course.get('name'), '未命名课程')}：进度 {_safe_str(course.get('progress'), '未知')}"
                f"｜待办 {_safe_str(course.get('pending'), '无')}"
            )
        for suggestion in (data.get("suggestions") or [])[:3]:
            lines.append(f"  → {suggestion}")
        return Ok("\n".join(lines))

    @llm_tool(
        name="study_comfort",
        description="心理疏导与学习心理暗示。用户表达焦虑、疲惫、迷茫、拖延、考前恐慌，或考砸了的时候调用。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "用户当前的情绪表达，原样传入"},
            },
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="study_comfort",
        name="心理疏导",
        description="按考试类型、临考阶段与情绪状态做个性化疏导和心理暗示。",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    async def comfort_entry(self, text: str = "", **_):
        try:
            return Ok(self._out(await self._comfort(text)))
        except SdkError as exc:
            return Err(str(exc))

    @plugin_entry(
        id="study_review_queue",
        name="到期复习队列",
        description="列出按艾宾浩斯间隔到期的知识点，供今日复习使用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def review_entry(self, **_):
        await self._ensure_ready()
        rows = await asyncio.to_thread(self.store.due_reviews, 10)
        if not rows:
            return Ok("今天没有到期的复习项喵，可以推进新内容。")
        lines = ["今天该复习这些："]
        for row in rows:
            point_id = _safe_str(row.get("point_id"))
            point = get_point(point_id)
            name = point.name if point else point_id
            tail = f"（{subject_label(point.subject)}）" if point else ""
            lines.append(f"  · {name}{tail}：当前掌握度 {float(row.get('level') or 0):.0%}")
        return Ok("\n".join(lines))

    @plugin_entry(
        id="study_roi",
        name="提分性价比排行",
        description="按『还差多少 × 分值权重 × 考频』排出最值得先补的知识点。",
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    )
    async def roi_entry(self, limit: int = 8, **_):
        await self._ensure_ready()
        profile = self._effective_profile()
        mastery = await asyncio.to_thread(self.store.mastery_map)
        rows = roi_ranking(profile["exam_type"], self._subjects_list(profile), mastery, limit or 8)
        if not rows:
            return Ok("还没有可排序的知识点喵。")
        lines = ["按提分性价比，先补这些："]
        for index, row in enumerate(rows, start=1):
            point = row["point"]
            lines.append(
                f"{index}. {point['name']}（{subject_label(point['subject'])}）性价比 {row['roi']}\n"
                f"   {row['reason']}\n"
                f"   只会考：{'、'.join(point['forms_label'])}"
            )
        return Ok("\n".join(lines))

    @plugin_entry(
        id="study_open_panel",
        name="打开学习面板",
        description="返回学习辅助面板的访问地址，用于设置档案、连接平台与检索资源。",
        input_schema={"type": "object", "properties": {}},
    )
    async def panel_entry(self, **_):
        port = self._panel_server.port if self._panel_server else self.panel_port
        return Ok(f"学习面板地址：http://127.0.0.1:{port} （本机可访问）")


__all__ = ["StudyCopilotPlugin"]
