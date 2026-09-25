"""
Suno.cn 音乐生成插件

通过 Suno.cn API 生成 AI 音乐、查询任务状态、获取歌词等功能。
严格遵循 HTTP REST API 规范，不伪造响应，不建议手动操作。
"""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import Any
from urllib.parse import urlparse

import httpx
from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)


@neko_plugin
class SunoCnMusicPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._api_key = ""
        self._base_url = "https://mcp.suno.cn"
        self._timeout = 60.0

    def _store_get_sync(self, key: str, default: Any = None) -> Any:
        if not self.store.enabled:
            return default
        try:
            return self.store._read_value(key, default)
        except Exception as exc:  # noqa: BLE001 - store backend failures should fall back to defaults.
            self.logger.warning(f"Store get failed for key {key!r}: {exc}")
            return default

    def _store_set_sync(self, key: str, value: Any) -> None:
        if not self.store.enabled:
            self.logger.warning(f"Store is disabled, cannot persist key {key!r}")
            return
        try:
            self.store._write_value(key, value)
        except Exception as exc:  # noqa: BLE001 - cache writes must not break plugin actions.
            self.logger.warning(f"Store set failed for key {key!r}: {exc}")

    def _get_latest_audio_cache(self, lanlan_name: str | None = None) -> dict[str, Any] | None:
        cache_key = f"latest_audio:{lanlan_name or '__global__'}"
        cached = self._store_get_sync(cache_key)
        return cached if isinstance(cached, dict) else None

    def _normalize_audio_item(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        url = str(item.get("play_url", "")).strip()
        if not url:
            return None
        return {
            "serial_no": str(item.get("serial_no", "")).strip(),
            "title": str(item.get("title", "未命名歌曲")).strip() or "未命名歌曲",
            "artist": str(item.get("artist", "Suno")).strip() or "Suno",
            "url": url,
            "cover": str(item.get("cover", "")).strip(),
            "duration": int(item.get("duration", 0) or 0),
        }

    def _find_cached_audio_item(self, url: str, lanlan_name: str | None = None) -> dict[str, Any] | None:
        normalized_url = str(url).strip()
        if not normalized_url:
            return None
        cached = self._get_latest_audio_cache(lanlan_name)
        if not cached:
            return None
        items = cached.get("items", [])
        if not isinstance(items, list):
            return None
        for item in items:
            normalized = self._normalize_audio_item(item)
            if normalized and normalized.get("url") == normalized_url:
                return normalized
        return None

    def _push_native_music_play(self, track: dict[str, Any]) -> None:
        url = str(track.get("url", "")).strip()
        if not url:
            raise ValueError("音乐链接不能为空")

        parsed = urlparse(url)
        host_or_url = parsed.hostname or url
        self.ctx.push_message(
            source=self.plugin_id,
            visibility=[],
            ai_behavior="blind",
            parts=[
                {
                    "type": "ui_action",
                    "action": "media_allowlist_add",
                    "domains": [host_or_url],
                }
            ],
        )
        self.ctx.push_message(
            source=self.plugin_id,
            visibility=["chat"],
            ai_behavior="blind",
            parts=[
                {
                    "type": "ui_action",
                    "action": "media_play_url",
                    "media_type": "audio",
                    "url": url,
                    "name": str(track.get("title", "未命名歌曲")).strip() or "未命名歌曲",
                    "artist": str(track.get("artist", "Suno")).strip() or "Suno",
                }
            ],
        )

    @lifecycle(id="startup")
    async def startup(self, **_):
        """插件启动时加载配置"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        suno_cfg = cfg.get("suno", {})

        self._api_key = str(suno_cfg.get("api_key", "")).strip()
        if not self._api_key:
            self.logger.warning("SUNO_CN_API_KEY not configured")
            return Err(SdkError("SUNO_CN_API_KEY 未配置，请在 plugin.toml 中设置 [suno] api_key"))

        self._base_url = str(suno_cfg.get("base_url", "https://mcp.suno.cn")).strip()
        self._timeout = float(suno_cfg.get("timeout_seconds", 60))

        if not self.store.enabled:
            store_cfg = cfg.get("plugin", {})
            store_cfg = store_cfg.get("store", {}) if isinstance(store_cfg, dict) else {}
            if isinstance(store_cfg, dict) and store_cfg.get("enabled"):
                self.store.enabled = True
                self.logger.info("Store enabled from config")
            else:
                self.store.enabled = True
                self.logger.info("Store force-enabled for audio cache")

        self.logger.info(f"SunoCnMusic started: base_url={self._base_url}, timeout={self._timeout}s, store={self.store.enabled}")
        return Ok({"status": "running", "base_url": self._base_url})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """插件关闭"""
        self.logger.info("SunoCnMusic shutdown")
        return Ok({"status": "shutdown"})

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        发送 HTTP 请求到 Suno.cn API

        Returns:
            (success: bool, response_data: dict | error_message: str)
        """
        url = f"{self._base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        self.logger.info(f"API Request: {method} {endpoint} params={params} json={json_data}")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=None,
                trust_env=False,
            ) as client:
                if method.upper() == "GET":
                    resp = await client.get(url, headers=headers, params=params)
                elif method.upper() == "POST":
                    resp = await client.post(url, headers=headers, json=json_data)
                else:
                    return False, f"不支持的 HTTP 方法: {method}"

                self.logger.info(f"API Response: status={resp.status_code} url={url}")

                # Handle HTTP errors
                if resp.status_code in (401, 403):
                    return False, "API Key 无效或已过期，请检查配置"
                elif resp.status_code >= 500:
                    return False, "服务暂时不可用，请稍后重试"
                elif resp.status_code != 200:
                    error_text = resp.text[:200] if resp.text else "Unknown error"
                    return False, f"HTTP {resp.status_code}: {error_text}"

                # Parse JSON response
                try:
                    data = resp.json()
                    self.logger.info(f"API Response data: {data}")
                    return True, data
                except Exception as e:  # noqa: BLE001 - invalid upstream JSON is returned as a user-facing error.
                    self.logger.warning(f"Failed to parse JSON response: {e}")
                    return False, f"响应解析失败: {e!s}"

        except httpx.TimeoutException:
            self.logger.warning(f"API request timeout: {url}")
            return False, "请求超时，请稍后重试"
        except Exception as e:
            self.logger.exception(f"API request failed: {url}")
            return False, f"网络错误: {e!s}"

    def _poll_and_notify(self, serial_nos: list[str], lanlan_name: str | None = None) -> None:
        """后台轮询任务状态，完成后主动通知用户"""
        try:
            serial_joined = ",".join(map(str, serial_nos))
            self.logger.info(f"Starting background polling for: {serial_joined}")

            # 最多轮询 6 次，每次间隔 10 秒，总共约 60 秒
            for attempt in range(6):
                time.sleep(10)

                try:
                    with httpx.Client(timeout=50.0, proxy=None, trust_env=False) as client:
                        resp = client.get(
                            f"{self._base_url}/mcp/api/task/{serial_joined}",
                            headers={"Authorization": f"Bearer {self._api_key}"},
                            params={"wait": 45},
                        )

                    if resp.status_code in (401, 403):
                        self.logger.warning("Background polling auth failed")
                        return
                    if resp.status_code != 200:
                        self.logger.warning(f"Background polling HTTP {resp.status_code}")
                        continue

                    data = resp.json()
                    tasks = data.get("tasks", []) if isinstance(data, dict) else []
                    if not tasks:
                        continue

                    pending = [t for t in tasks if t.get("status") in ("queued", "processing")]
                    if pending:
                        self.logger.info(f"Background polling attempt {attempt + 1}: still pending")
                        continue

                    success_tasks = [t for t in tasks if t.get("status") == "success"]
                    failed_tasks = [t for t in tasks if t.get("status") == "failed"]

                    if success_tasks:
                        lines = []
                        latest_items = []
                        for task in success_tasks:
                            title = task.get("title", "未命名歌曲")
                            play_url = task.get("play_url", "")
                            duration = task.get("duration", 0)
                            lines.append(f"- {title} ({duration}s) {play_url}")
                            if play_url:
                                latest_items.append({
                                    "serial_no": task.get("serial_no", ""),
                                    "title": title,
                                    "play_url": play_url,
                                    "duration": duration,
                                })

                        if latest_items:
                            cache_key = f"latest_audio:{lanlan_name or '__global__'}"
                            self._store_set_sync(cache_key, {
                                "items": latest_items,
                                "updated_at": time.time(),
                            })

                        self.ctx.push_message(
                            source="suno_cn_music",
                            priority=8,
                            parts=[
                                {
                                    "type": "text",
                                    "text": "音乐生成完成：\n" + "\n".join(lines),
                                }
                            ],
                            metadata={
                                "description": "音乐生成完成",
                                "serial_nos": serial_nos,
                                "tasks": success_tasks,
                            },
                            target_lanlan=lanlan_name,
                        )
                        self.logger.info("Background polling completed with success")
                        return

                    if failed_tasks and len(failed_tasks) == len(tasks):
                        reasons = [f"{t.get('serial_no', '')}: {t.get('fail_reason', '未知错误')}" for t in failed_tasks]
                        self.ctx.push_message(
                            source="suno_cn_music",
                            priority=8,
                            parts=[
                                {
                                    "type": "text",
                                    "text": "音乐生成失败：\n" + "\n".join(reasons),
                                }
                            ],
                            metadata={
                                "description": "音乐生成失败",
                                "serial_nos": serial_nos,
                                "tasks": failed_tasks,
                            },
                            target_lanlan=lanlan_name,
                        )
                        self.logger.info("Background polling completed with failure")
                        return

                except Exception as e:  # noqa: BLE001 - polling retries should continue after transient failures.
                    self.logger.warning(f"Background polling attempt {attempt + 1} failed: {e}")
                    continue

            self.logger.warning("Background polling finished without terminal result")

        except Exception:
            self.logger.exception("Background polling crashed")

    @plugin_entry(
        id="get_user_info",
        name="查询账户信息",
        description="查询 Suno.cn 账户信息（昵称、积分、会员状态）",
        llm_result_fields=["nickname", "points", "vip_status"],
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def get_user_info(self, **_):
        """查询账户信息"""
        success, result = await self._api_request("GET", "/mcp/api/user")
        if not success:
            return Err(SdkError(str(result)))

        return Ok({
            "nickname": result.get("nickname", ""),
            "points": result.get("points", 0),
            "vip_status": result.get("vip_status", ""),
            "raw": result,
        })

    @plugin_entry(
        id="query_task",
        name="查询任务状态",
        description="查询 Suno.cn 音乐生成任务状态。首次查询建议 wait=45。",
        llm_result_fields=["tasks", "summary"],
        timeout=70.0,
        input_schema={
            "type": "object",
            "properties": {
                "serial_no": {
                    "type": "string",
                    "description": "任务编号，支持多个编号用逗号分隔",
                },
                "wait": {
                    "type": "integer",
                    "description": "服务端等待秒数，推荐 45，最大 60",
                    "default": 45,
                },
            },
            "required": ["serial_no"],
        },
    )
    async def query_task(self, serial_no: str, wait: int = 45, **_):
        """查询任务状态"""
        serial_no = str(serial_no).strip()
        if not serial_no:
            return Err(SdkError("任务编号不能为空"))

        wait = max(0, min(int(wait), 60))
        success, result = await self._api_request(
            "GET",
            f"/mcp/api/task/{serial_no}",
            params={"wait": wait},
        )
        if not success:
            return Err(SdkError(str(result)))

        tasks = result.get("tasks", []) if isinstance(result, dict) else []
        summary_parts: list[str] = []
        for task in tasks:
            status = task.get("status", "")
            title = task.get("title", "")
            serial = task.get("serial_no", "")
            if status == "success":
                summary_parts.append(f"{serial} / {title} / success")
            elif status == "failed":
                summary_parts.append(f"{serial} / {title} / failed / {task.get('fail_reason', '')}")
            else:
                summary_parts.append(f"{serial} / {title} / {status}")

        return Ok({
            "tasks": tasks,
            "summary": "\n".join(summary_parts),
            "raw": result,
        })

    @plugin_entry(
        id="generate_music",
        name="生成音乐",
        description="通过 Suno.cn 生成音乐。默认只传 prompt；title、tags 留空，mv 使用默认 v5；custom_mode,默认 false；除非用户明确提供完整歌词，否则不要设为 true。提交后立即返回，后台轮询完成后会主动通知。",
        llm_result_fields=["serial_nos", "message", "instruction"],
        timeout=15.0,
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "必填。只补充歌曲描述本身；不要自动生成标题、标签或自定义模型参数。",
                },
                "title": {
                    "type": "string",
                    "description": "可选。默认留空，除非用户明确要求指定歌曲名。",
                },
                "tags": {
                    "type": "string",
                    "description": "可选。默认留空，除非用户明确要求指定风格标签。",
                },
                "custom_mode": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 为自定义歌词模式。默认 false；除非用户明确提供完整歌词，否则不要设为 true。",
                },
                "instrumental": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 为纯音乐。仅当用户明确要求无人声或纯音乐时设为 true。",
                },
                "mv": {
                    "type": "string",
                    "default": "v5",
                    "description": "模型版本。默认使用 v5，除非用户明确要求其他版本。",
                },
            },
            "required": ["prompt"],
        },
    )
    async def generate_music(
        self,
        prompt: str,
        title: str = "",
        tags: str = "",
        custom_mode: bool = False,
        instrumental: bool = False,
        mv: str = "v5",
        **kwargs,
    ):
        """生成音乐，立即返回，后台轮询完成后主动通知"""
        prompt = str(prompt).strip()
        if not prompt:
            return Err(SdkError("prompt 不能为空"))

        allowed_mv = {
            "v5",
            "v4.5",
            "v4",
            "v3.5",
            "chirp-crow",
            "chirp-bluejay",
            "chirp-auk",
            "chirp-auk-turbo",
            "chirp-v4",
            "chirp-v3-5",
        }
        mv = str(mv).strip() if mv is not None else ""
        if not mv:
            mv = "v5"
        if mv not in allowed_mv:
            self.logger.warning(f"Invalid mv {mv!r}, fallback to v5")
            mv = "v5"

        normalized_title = str(title).strip()
        normalized_tags = str(tags).strip()

        looks_like_lyrics = (
            "\n" in prompt
            or len(prompt) >= 200
            or prompt.count(",") >= 4
            or prompt.count("，") >= 4
        )
        original_custom_mode = bool(custom_mode)
        custom_mode = bool(custom_mode and looks_like_lyrics)
        if original_custom_mode and not custom_mode:
            self.logger.warning("custom_mode disabled because prompt does not look like lyrics")

        payload: dict[str, Any] = {
            "prompt": prompt,
            "mv": mv,
            "custom_mode": custom_mode,
            "instrumental": bool(instrumental),
        }
        if normalized_title:
            payload["title"] = normalized_title
        if normalized_tags:
            payload["tags"] = normalized_tags

        success, result = await self._api_request(
            "POST",
            "/mcp/api/generate",
            json_data=payload,
        )
        if not success:
            return Err(SdkError(str(result)))

        serial_nos = result.get("serial_nos", []) if isinstance(result, dict) else []
        message = result.get("message", "") if isinstance(result, dict) else ""
        if not serial_nos:
            return Ok({
                "serial_nos": [],
                "message": message,
                "instruction": "任务已提交，但未返回任务编号。",
                "raw": result,
            })

        # 获取 lanlan_name
        ctx_obj = kwargs.get("_ctx")
        lanlan_name = None
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
        if not lanlan_name:
            lanlan_name = getattr(self.ctx, "_current_lanlan", None)

        # 启动后台轮询线程
        threading.Thread(
            target=self._poll_and_notify,
            args=(serial_nos, lanlan_name),
            daemon=True,
            name=f"suno-poll-{serial_nos[0] if serial_nos else 'unknown'}",
        ).start()

        self.logger.info(f"Background polling thread started for: {serial_nos}")

        return Ok({
            "serial_nos": serial_nos,
            "message": message,
            "instruction": "音乐生成任务已提交，正在后台生成中（约 30-60 秒），完成后我会主动通知你。",
            "raw": result,
        })

    @plugin_entry(
        id="list_music",
        name="查询音乐列表",
        description="查询 Suno.cn 历史音乐列表",
        llm_result_fields=["list", "page", "count"],
        input_schema={
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "default": 1,
                    "description": "页码",
                },
                "page_size": {
                    "type": "integer",
                    "default": 10,
                    "description": "每页数量",
                },
            },
        },
    )
    async def list_music(self, page: int = 1, page_size: int = 10, **_):
        """查询音乐列表"""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 50))

        success, result = await self._api_request(
            "GET",
            "/mcp/api/music",
            params={"page": page, "page_size": page_size},
        )
        if not success:
            return Err(SdkError(str(result)))

        music_list = result.get("list", []) if isinstance(result, dict) else []
        return Ok({
            "list": music_list,
            "page": result.get("page", page) if isinstance(result, dict) else page,
            "count": len(music_list),
            "raw": result,
        })

    @plugin_entry(
        id="get_lyrics",
        name="获取歌词",
        description="获取指定歌曲的歌词。歌词内容将原样返回，不做改写。",
        llm_result_fields=["lyrics"],
        input_schema={
            "type": "object",
            "properties": {
                "serial_no": {
                    "type": "string",
                    "description": "任务编号",
                },
            },
            "required": ["serial_no"],
        },
    )
    async def get_lyrics(self, serial_no: str, **_):
        """获取歌词，必须原样返回"""
        serial_no = str(serial_no).strip()
        if not serial_no:
            return Err(SdkError("任务编号不能为空"))

        success, result = await self._api_request("GET", f"/mcp/api/lyrics/{serial_no}")
        if not success:
            return Err(SdkError(str(result)))

        return Ok({
            "serial_no": result.get("serial_no", serial_no),
            "lyrics": result.get("lyrics", ""),
            "raw": result,
        })

    @plugin_entry(
        id="extend_music",
        name="续写音乐",
        description="在某首歌基础上继续创作",
        llm_result_fields=["serial_no", "message"],
        input_schema={
            "type": "object",
            "properties": {
                "serial_no": {
                    "type": "string",
                    "description": "原任务编号",
                },
                "continue_at": {
                    "type": "integer",
                    "default": 0,
                    "description": "从第几秒续写，0 为从结尾",
                },
                "mv": {
                    "type": "string",
                    "default": "v5",
                    "description": "模型版本",
                },
            },
            "required": ["serial_no"],
        },
    )
    async def extend_music(
        self,
        serial_no: str,
        continue_at: int = 0,
        mv: str = "v5",
        **_,
    ):
        """续写音乐"""
        serial_no = str(serial_no).strip()
        if not serial_no:
            return Err(SdkError("原任务编号不能为空"))

        payload = {
            "serial_no": serial_no,
            "continue_at": max(0, int(continue_at)),
            "mv": mv or "v5",
        }
        success, result = await self._api_request(
            "POST",
            "/mcp/api/extend",
            json_data=payload,
        )
        if not success:
            return Err(SdkError(str(result)))

        return Ok({
            "serial_no": result.get("serial_no", ""),
            "message": result.get("message", ""),
            "raw": result,
        })

    @plugin_entry(
        id="gen_lyrics",
        name="AI 生成歌词",
        description="先生成歌词，再决定是否用该歌词生成音乐。歌词将完整返回。",
        llm_result_fields=["lyrics"],
        input_schema={
            "type": "object",
            "properties": {
                "inspiration": {
                    "type": "string",
                    "description": "灵感描述",
                },
                "title": {
                    "type": "string",
                    "description": "歌曲标题（可选）",
                },
                "style": {
                    "type": "string",
                    "description": "风格（可选）",
                },
            },
            "required": ["inspiration"],
        },
    )
    async def gen_lyrics(self, inspiration: str, title: str = "", style: str = "", **_):
        """AI 生成歌词"""
        inspiration = str(inspiration).strip()
        if not inspiration:
            return Err(SdkError("灵感描述不能为空"))

        payload: dict[str, Any] = {"inspiration": inspiration}
        if title:
            payload["title"] = title
        if style:
            payload["style"] = style

        success, result = await self._api_request(
            "POST",
            "/mcp/api/gen-lyrics",
            json_data=payload,
        )
        if not success:
            return Err(SdkError(str(result)))

        return Ok({
            "lyrics": result.get("lyrics", ""),
            "raw": result,
            "instruction": "歌词已生成。如需用这段歌词生成音乐，请使用 generate_music 并设置 custom_mode=true。",
        })

    def _open_url(self, url: str, title: str = ""):
        if not url.startswith(("http://", "https://")):
            return Err(SdkError("仅支持 http/https URL"))

        title = str(title).strip()
        title_display = f"（{title}）" if title else ""
        self.logger.info(f"Opening URL{title_display}: {url}")

        try:
            opened = webbrowser.open(url)
        except Exception as e:
            self.logger.exception(f"Failed to open URL: {url}")
            return Err(SdkError(f"无法打开 URL: {e!s}"))

        if not opened:
            self.logger.warning(f"webbrowser.open returned false for url={url}")
            return Err(SdkError("系统未能打开默认浏览器，请检查系统浏览器设置"))

        return Ok({
            "status": "opened",
            "url": url,
            "message": f"已打开链接{title_display}",
        })

    @plugin_entry(
        id="play_audio",
        name="播放音频",
        description="播放 Suno 生成的音乐或音频链接。用户说播放刚才生成的歌、播放这首歌、播放音频链接时调用。如果不提供 URL，会自动播放最近一次生成的音乐。",
        llm_result_fields=["status", "url", "message"],
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要播放的音频 URL。若不提供，会自动播放最近一次生成的音乐。",
                },
                "title": {
                    "type": "string",
                    "description": "标题（可选，仅用于日志和返回信息）",
                },
            },
        },
    )
    async def play_audio(self, url: str = "", title: str = "", **kwargs):
        url = str(url).strip()
        ctx_obj = kwargs.get("_ctx")
        lanlan_name = None
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
        if not lanlan_name:
            lanlan_name = getattr(self.ctx, "_current_lanlan", None)

        track: dict[str, Any] | None = None

        if not url:
            cached = self._get_latest_audio_cache(lanlan_name)

            if not cached or not isinstance(cached, dict):
                return Err(SdkError("没有找到最近生成的音乐，请先生成音乐或提供 URL"))

            items = cached.get("items", [])
            if not items or not isinstance(items, list):
                return Err(SdkError("没有找到最近生成的音乐，请先生成音乐或提供 URL"))

            track = self._normalize_audio_item(items[0])
            if not track:
                return Err(SdkError("最近生成的音乐没有播放链接"))

            url = track["url"]
            if not title:
                title = track["title"]

            self.logger.info(f"Using cached audio: {track}")
        else:
            track = self._find_cached_audio_item(url, lanlan_name)
            if track and not title:
                title = track["title"]

        if not url.startswith(("http://", "https://")):
            return Err(SdkError("仅支持 http/https URL"))

        if track:
            title_display = f"（{track.get('title', '未命名歌曲')}）"
            try:
                self._push_native_music_play(track)
            except Exception as e:
                self.logger.exception(f"Failed to dispatch music to native player: {url}")
                return Err(SdkError(f"无法推送到 N.E.K.O 播放器: {e!s}"))

            return Ok({
                "status": "queued",
                "url": track["url"],
                "message": f"已推送到 N.E.K.O 播放器播放{title_display}",
            })

        return self._open_url(url, title)

    @plugin_entry(
        id="open_url",
        name="打开链接",
        description="打开网页链接。用户说打开链接、打开网页、打开这个网址时调用。仅用于打开普通 http/https URL，不负责播放最近生成的音乐。",
        llm_result_fields=["status", "url", "message"],
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要打开的网页 URL，必须为 http/https。",
                },
                "title": {
                    "type": "string",
                    "description": "标题（可选，仅用于日志和返回信息）",
                },
            },
            "required": ["url"],
        },
    )
    async def open_url(self, url: str, title: str = "", **_):
        url = str(url).strip()
        if not url:
            return Err(SdkError("URL 不能为空"))
        return self._open_url(url, title)

    @plugin_entry(
        id="set_api_key",
        name="设置 API Key",
        description="更新 Suno.cn API Key 配置并立即生效",
        llm_result_fields=["status", "message"],
        input_schema={
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "新的 API Key（sk- 开头）",
                },
            },
            "required": ["api_key"],
        },
    )
    async def set_api_key(self, api_key: str, **_):
        """设置 API Key"""
        api_key = str(api_key).strip()
        if not api_key:
            return Err(SdkError("API Key 不能为空"))

        if not api_key.startswith("sk-"):
            return Err(SdkError("API Key 格式错误，应以 sk- 开头"))

        try:
            # 读取当前 plugin.toml
            config_path = self.config_dir / "plugin.toml"
            if not config_path.exists():
                return Err(SdkError(f"配置文件不存在: {config_path}"))

            # 读取文件内容
            content = config_path.read_text(encoding="utf-8")
            lines = content.split("\n")

            # 查找并替换 api_key 行
            updated = False
            in_suno_section = False
            new_lines = []

            for line in lines:
                stripped = line.strip()

                # 检测 [suno] 段
                if stripped == "[suno]":
                    in_suno_section = True
                    new_lines.append(line)
                    continue

                # 检测其他段开始
                if stripped.startswith("[") and stripped != "[suno]":
                    in_suno_section = False

                # 在 [suno] 段内替换 api_key
                if in_suno_section and stripped.startswith("api_key"):
                    new_lines.append(f'api_key = "{api_key}"')
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                return Err(SdkError("未找到 [suno] 段中的 api_key 配置项"))

            # 写回文件
            config_path.write_text("\n".join(new_lines), encoding="utf-8")

            # 立即更新内存中的 API Key
            self._api_key = api_key

            self.logger.info("API Key updated successfully")

            return Ok({
                "status": "success",
                "message": f"API Key 已更新并生效（前缀: {api_key[:8]}...）",
                "config_path": str(config_path),
            })

        except Exception as e:
            self.logger.exception("Failed to update API Key")
            return Err(SdkError(f"更新 API Key 失败: {e!s}"))
