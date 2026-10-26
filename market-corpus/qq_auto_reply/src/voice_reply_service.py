from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import time
import wave
from pathlib import Path
from typing import Any

import websockets
from utils.api_config_loader import get_free_voices
from utils.config_manager import get_reserved

#: 宿主用 ``gsv:<raw>`` 标记 GPT-SoVITS 音色（见宿主的 ``config.GSV_VOICE_PREFIX``）。
#: 插件的语音管线**没有这一路** —— 它只有：本地 ws SoVITS/CosyVoice、MiMo、MiniMax、
#: Gemini native、免费音色、DashScope（百炼）。撞到这种 id 要当场说清楚，别拿它去问百炼
#: （那边只会回一个"音色不存在"，看起来像网络问题）。
GSV_VOICE_PREFIX = "gsv:"

try:
    from utils.tts.native_voice_registry import get_active_realtime_native_provider_for_ui
except (ImportError, ModuleNotFoundError):
    get_active_realtime_native_provider_for_ui = None
try:
    from utils.tts.providers.gemini import normalize_gemini_tts_voice
except (ImportError, ModuleNotFoundError):
    normalize_gemini_tts_voice = None
try:
    from utils.voice_clone import (
        MimoVoiceCloneClient,
        MimoVoiceCloneError,
        MinimaxVoiceCloneClient,
        MinimaxVoiceCloneError,
    )
except (ImportError, ModuleNotFoundError):
    MimoVoiceCloneClient = MinimaxVoiceCloneClient = None
    class _MissingVoiceCloneError(Exception):
        pass
    MimoVoiceCloneError = MinimaxVoiceCloneError = _MissingVoiceCloneError
try:
    from utils.voice_config import read_legacy_voice_id
except (ImportError, ModuleNotFoundError):
    read_legacy_voice_id = None


class QQVoiceReplyService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def get_voice_output_dir(self) -> Path:
        return self.plugin.data_path() / "voice_cache"

    async def cleanup_voice_output_dir(self, *, max_age_seconds: int = 1800) -> None:
        voice_dir = self.get_voice_output_dir()
        if not voice_dir.exists():
            return
        cutoff = time.time() - max_age_seconds
        for pattern in ("*.wav", "*.mp3"):
            for path in voice_dir.glob(pattern):
                try:
                    if path.stat().st_mtime < cutoff:
                        await asyncio.to_thread(path.unlink)
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    self.plugin.logger.warning(f"清理过期 QQ 语音缓存失败: {path} err={exc}")

    async def get_current_voice_id(self) -> str:
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            characters = await config_manager.aload_characters()
            if not isinstance(characters, dict):
                return ""
            current_name = str(characters.get("当前猫娘") or "").strip()
            catgirls = characters.get("猫娘") or {}
            current_character = catgirls.get(current_name) if isinstance(catgirls, dict) else None
            if not isinstance(current_character, dict):
                return ""
            return read_legacy_voice_id(get_reserved(current_character, "voice_id", default="", legacy_keys=("voice_id",))) if read_legacy_voice_id else ""
        except Exception as exc:
            self.plugin.logger.warning(f"读取当前猫娘 voice_id 失败: {exc}")
            return ""

    async def _synthesize_local_tts(self, text: str) -> tuple[bytes, str] | None:
        """尝试本地 SoVITS/CosyVoice WebSocket TTS，成功返回 (wav_bytes, mime)，失败返回 None。"""
        try:
            from utils.config_manager import get_config_manager
            cm = get_config_manager()
            tts_config = cm.get_model_api_config("tts_custom")
            base_url = str(tts_config.get("base_url", "")).strip()
            if not base_url or (not base_url.startswith("ws://") and not base_url.startswith("wss://")):
                return None

            voice_id = await self.get_current_voice_id() or "default"
            voice_name = str(tts_config.get("voice_name") or voice_id)

            import io
            import json as _json
            import wave

            import websockets
            ws_url = base_url.rstrip("/") + "/v1/audio/speech/stream"

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(_json.dumps({"voice": voice_name, "speed": 1.0}))
                await ws.send(_json.dumps({"text": text}))
                await ws.send(_json.dumps({"event": "end"}))

                chunks: list[bytes] = []
                async with asyncio.timeout(15.0):
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            chunks.append(msg)
                        elif isinstance(msg, str):
                            try:
                                data = _json.loads(msg)
                                if data.get("type") == "error":
                                    self.plugin.logger.warning(f"本地TTS错误: {data.get('message', '')}")
                                    return None
                            except Exception:
                                pass

                if not chunks:
                    return None

                combined = b"".join(chunks)
                out = io.BytesIO()
                with wave.open(out, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(22050)
                    wf.writeframes(combined)
                return out.getvalue(), "audio/wav"
        except Exception as e:
            self.plugin.logger.warning(f"本地TTS失败: {e}")
            return None

    async def synthesize_reply_voice_audio(self, text: str) -> tuple[bytes, str]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise RuntimeError("语音合成文本不能为空")
        try:
            from main_logic.tts_client.workers.free import (
                _adjust_free_tts_url,
                _build_step_tts_create_data,
            )
            from utils.config_manager import get_config_manager
            from utils.tts.providers.stepfun import STEPFUN_TTS_DEFAULT_VOICE

            config_manager = get_config_manager()

            # 这条合成路径会分两次读区域敏感配置：先按目录挑音色/provider，之后
            # _adjust_free_tts_url 再按区域拼 TTS 端点。判定若在两者之间落定，就会
            # 把大陆 free_voices 的音色 ID 发去 lanlan.app（或反过来），而两套目录不
            # 相交，这条语音回复直接失败。先落定，让整次合成用同一结论。已落定时零
            # 开销；自配 API 用户不会因此发起探测。fail-open：落定不了也继续按当前
            # 配置合成，最坏是这一条回复失败、下一条就好了。
            try:
                if not await config_manager.aensure_region_resolved():
                    self.plugin.logger.warning(
                        "[GeoIP] 语音回复合成开始时区域判定仍未落定；若结论恰在本次合成中途到达，"
                        "音色与线路可能不匹配导致这条语音失败"
                    )
            except Exception as e:
                self.plugin.logger.warning(f"[GeoIP] 语音回复区域落定失败，按当前配置继续: {e}")

            # 优先尝试本地 SoVITS/CosyVoice
            local_result = await self._synthesize_local_tts(normalized_text)
            if local_result:
                return local_result

            voices = config_manager.get_voices_for_current_api()
            voice_id = await self.get_current_voice_id()
            if voice_id.startswith(GSV_VOICE_PREFIX):
                # 走到这里说明本地那条 ws 线路没产出音频（没配端点或合成失败），而
                # GPT-SoVITS 只存在于宿主自己的 TTS 里 —— 插件这条链上没有它。
                raise RuntimeError(
                    f"当前音色是宿主的 GPT-SoVITS 音色（{GSV_VOICE_PREFIX}…），插件的语音管线没有这一路："
                    "要么把 tts_custom 指向 ws://… 用本地合成，要么在角色上换成插件支持的音色"
                    "（百炼 / MiniMax / MiMo / Gemini / 免费音色）"
                )
            if not voice_id and get_active_realtime_native_provider_for_ui:
                active_native = get_active_realtime_native_provider_for_ui(config_manager)
                if active_native:
                    voice_id = active_native
            voice_data = voices.get(voice_id) if isinstance(voices, dict) else None
            provider = (voice_data or {}).get("provider", "")
            text = normalized_text

            try:
                tts_custom_config = config_manager.get_model_api_config("tts_custom")
                audio_api_key = tts_custom_config.get("api_key", "")
            except Exception:
                audio_api_key = ""
            if not audio_api_key:
                core_config = await config_manager.aget_core_config()
                audio_api_key = core_config.get("AUDIO_API_KEY", "")
            preview_core_config = await config_manager.aget_core_config()

            logger = self.plugin.logger
            logger.info(f"正在为音色 {voice_id} 生成回复语音...")

            if provider == "mimo":
                sample_b64 = (voice_data or {}).get("clone_sample_b64") or ""
                if not sample_b64:
                    raise RuntimeError(f"MiMo 克隆音色缺少参考样本，无法生成回复语音: {voice_id}")
                mimo_api_key = config_manager.get_tts_api_key("mimo")
                if not mimo_api_key:
                    raise RuntimeError("MIMO_API_KEY_MISSING")
                if str(preview_core_config.get("assistApi") or "").strip().lower() == "mimo":
                    mimo_base_url = (preview_core_config.get("OPENROUTER_URL") or "").strip()
                else:
                    mimo_base_url = str((voice_data or {}).get("mimo_base_url") or "").strip()
                sample_bytes = base64.b64decode(sample_b64)
                if not MimoVoiceCloneClient:
                    raise RuntimeError("MIMO_VOICE_CLONE_UNAVAILABLE")
                mimo_client = MimoVoiceCloneClient(api_key=mimo_api_key, base_url=mimo_base_url or None)
                audio_data = await mimo_client.synthesize_preview(
                    sample_bytes,
                    (voice_data or {}).get("clone_sample_mime") or "audio/wav",
                    text=text,
                )
                return audio_data, "audio/wav"

            if provider in ("minimax", "minimax_intl"):
                minimax_api_key = config_manager.get_tts_api_key(provider)
                if not minimax_api_key:
                    raise RuntimeError("MINIMAX_API_KEY_MISSING")
                from utils.voice_clone import get_minimax_base_url
                minimax_base_url = (voice_data or {}).get("minimax_base_url") or get_minimax_base_url(provider)
                if not MinimaxVoiceCloneClient:
                    raise RuntimeError("MINIMAX_VOICE_CLONE_UNAVAILABLE")
                minimax_client = MinimaxVoiceCloneClient(api_key=minimax_api_key, base_url=minimax_base_url)
                audio_data = await minimax_client.synthesize_preview(voice_id=voice_id, text=text)
                return audio_data, "audio/mpeg"

            if not audio_api_key:
                raise RuntimeError("TTS_AUDIO_API_KEY_MISSING")

            active_native_provider = get_active_realtime_native_provider_for_ui(config_manager) if get_active_realtime_native_provider_for_ui else None
            if active_native_provider and normalize_gemini_tts_voice:
                native_voice_id, recognized = normalize_gemini_tts_voice(voice_id)
                if active_native_provider == "gemini" and recognized:
                    from main_routers.characters_router import _synthesize_gemini_native_voice_preview
                    core_config = await config_manager.aget_core_config()
                    native_audio_api_key = (core_config or {}).get("CORE_API_KEY", "")
                    if not native_audio_api_key:
                        raise RuntimeError("TTS_AUDIO_API_KEY_MISSING")
                    audio_data = await _synthesize_gemini_native_voice_preview(
                        voice_id=native_voice_id,
                        preview_line=text,
                        audio_api_key=native_audio_api_key,
                    )
                    return audio_data, "audio/wav"

            free_voice_ids = set((get_free_voices() or {}).values())
            if voice_id in free_voice_ids:
                tts_url = _adjust_free_tts_url("wss://www.lanlan.tech/tts")
                headers = {"Authorization": f"Bearer {audio_api_key or ''}"}
                lang_hint = None
                is_lanlan_app = "lanlan.app" in tts_url
                session_id = ""
                pcm_chunks: list[bytes] = []
                wav_meta: tuple[int, int, int] | None = None
                async with asyncio.timeout(20):
                    async with websockets.connect(tts_url, additional_headers=headers) as ws:
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            if isinstance(raw, bytes):
                                continue
                            event = json.loads(raw)
                            event_type = event.get("type")
                            if event_type == "tts.connection.done":
                                session_id = event.get("data", {}).get("session_id") or ""
                                break
                            if event_type == "tts.response.error":
                                raise RuntimeError(str(event.get("data") or event))
                        if not session_id:
                            raise RuntimeError("TTS 连接未返回 session_id")
                        create_data = _build_step_tts_create_data(session_id, voice_id or STEPFUN_TTS_DEFAULT_VOICE, lang_hint, is_lanlan_app)
                        await ws.send(json.dumps({"type": "tts.create", "data": create_data}))
                        await ws.send(json.dumps({"type": "tts.text.delta", "data": {"session_id": session_id, "text": text}}))
                        await ws.send(json.dumps({"type": "tts.text.done", "data": {"session_id": session_id}}))
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=12.0)
                            if isinstance(raw, bytes):
                                continue
                            event = json.loads(raw)
                            event_type = event.get("type")
                            if event_type == "tts.response.error":
                                raise RuntimeError(str(event.get("data") or event))
                            if event_type == "tts.response.audio.delta":
                                audio_b64 = event.get("data", {}).get("audio", "")
                                if audio_b64:
                                    with wave.open(io.BytesIO(base64.b64decode(audio_b64)), "rb") as wav_file:
                                        pcm_data = wav_file.readframes(wav_file.getnframes())
                                        channels = wav_file.getnchannels()
                                        sample_width = wav_file.getsampwidth()
                                        sample_rate = wav_file.getframerate()
                                    pcm_chunks.append(pcm_data)
                                    wav_meta = wav_meta or (channels, sample_width, sample_rate)
                            elif event_type in ("tts.response.done", "tts.response.audio.done"):
                                break
                if not pcm_chunks or wav_meta is None:
                    raise RuntimeError("TTS 未返回音频")
                channels, sample_width, sample_rate = wav_meta
                out = io.BytesIO()
                with wave.open(out, "wb") as wav_file:
                    wav_file.setnchannels(channels)
                    wav_file.setsampwidth(sample_width)
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(b"".join(pcm_chunks))
                return out.getvalue(), "audio/wav"

            cosyvoice_base_url = ""
            if provider in ("cosyvoice", "cosyvoice_intl"):
                cosyvoice_runtime = config_manager.get_cosyvoice_clone_runtime(provider)
                runtime_key = (cosyvoice_runtime.get("api_key") or "").strip()
                if runtime_key:
                    audio_api_key = runtime_key
                elif provider == "cosyvoice_intl":
                    raise RuntimeError("TTS_AUDIO_API_KEY_MISSING")
                cosyvoice_base_url = (voice_data or {}).get("dashscope_base_url") or cosyvoice_runtime.get("base_url", "")
            try:
                tts_api_config = config_manager.get_model_api_config("tts_custom")
            except Exception:
                tts_api_config = {}
            if not voice_id:
                raise RuntimeError(
                    "当前猫娘未配置 voice_id，而这条 TTS 线路需要音色"
                    "（本地 SoVITS/CosyVoice 已经先试过：没配 ws:// 端点，或者合成没成）"
                )
            preview_base_url = cosyvoice_base_url or tts_api_config.get("base_url", "")
            from utils.api_config_loader import get_cosyvoice_clone_model
            clone_model = (voice_data or {}).get("clone_model") or get_cosyvoice_clone_model(provider)

            def _do_synthesize():
                import dashscope
                from dashscope.audio.tts_v2 import SpeechSynthesizer
                from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls
                with DASHSCOPE_GLOBAL_LOCK:
                    dashscope.api_key = audio_api_key
                    try:
                        configure_dashscope_sdk_urls(dashscope, preview_base_url, websocket_path="inference")
                    except Exception:
                        configure_dashscope_sdk_urls(dashscope, "", websocket_path="inference")
                    synthesizer = SpeechSynthesizer(model=clone_model, voice=voice_id)
                    return synthesizer.call(text)

            audio_data = await asyncio.to_thread(_do_synthesize)
            if not audio_data:
                raise RuntimeError("语音合成未返回音频数据")
            return audio_data, "audio/mpeg"
        except (MimoVoiceCloneError, MinimaxVoiceCloneError) as e:
            raise RuntimeError(str(e)) from e

    async def synthesize_reply_voice_file(self, text: str) -> tuple[str, str]:
        """合成一条语音并落成文件，返回 ``(file:// uri, mime)``。

        **这里不再要求角色必须有 ``voice_id``**（2026-09-29 改）。以前第一件事就是
        `if not voice_id: raise`，而它挡住的是**本地那条路**：`_synthesize_local_tts`
        （自建 SoVITS/CosyVoice，音色取自 ``tts_custom.voice_name``，缺 voice_id 时用
        ``"default"``）本来就排在最前面、本来就不需要角色音色 —— GSV/自建 TTS 的用户因此
        永远发不出语音，日志里只留一句"当前猫娘未配置 voice_id"，像是配置错了。
        要求现在留在真正需要它的地方：云端各分支自己有守卫（DashScope 那条见下）。
        """
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise RuntimeError("语音合成文本不能为空")
        await self.cleanup_voice_output_dir()
        audio_bytes, mime_type = await self.synthesize_reply_voice_audio(normalized_text)
        if not audio_bytes:
            raise RuntimeError("语音合成未返回音频数据")
        voice_dir = self.get_voice_output_dir()
        await asyncio.to_thread(voice_dir.mkdir, parents=True, exist_ok=True)
        suffix = ".mp3" if "mpeg" in mime_type else ".wav"
        output_path = voice_dir / f"qq_reply_{int(time.time() * 1000)}_{random.randint(1000, 9999)}{suffix}"
        await asyncio.to_thread(output_path.write_bytes, audio_bytes)
        return output_path.resolve().as_uri(), mime_type

    def _client_supports_voice(self) -> bool:
        """当前通道能不能收语音。

        开放平台的 `supports_voice` 是 False，而这里以前**从不问它**：`voice` 模式下
        每次都先 `synthesize_reply_voice_file()`（真跑一次 TTS、落一个音频文件），
        然后 `send_*_record` 在那边是空桩、返回 None，于是判成"未确认"再回退文本 ——
        功能上没错，但每次白烧一次语音合成。

        宁可少发一句也不肯多烧一次 TTS，所以合成之前就问。
        拿不到这个属性的连接**按支持处理**（保持旧行为：只有明确说不支持才跳过）。
        """
        client = getattr(self.plugin, "qq_client", None)
        if client is None:
            return False
        return bool(getattr(client, "supports_voice", True))

    async def _private_text_fallback(
        self, target_qq: str, normalized_text: str, *, mode: str, fallback: bool,
    ) -> bool:
        """通道发不出语音时，私聊这条该怎么收场。

        * ``both``：文字本来就是这条回复的一部分（今天也是"先发文字、语音失败保留文字"），
          照发；
        * ``voice``：只有 ``fallback`` 允许时才拿文字顶上；不允许的调用方（转达 / 主动发言）
          要的是"语音没发出去就是没发出去"，这里返回 False 让它如实报未确认，
          而不是擅自补一条那几位调用方明确不要的文字。
        """
        if mode != "both" and not fallback:
            self.plugin.logger.info("当前通道不支持语音，且未允许回退文本，跳过私聊回复")
            return False
        if not normalized_text:
            return False
        return self._confirm_send(
            await self.plugin.qq_client.send_message(target_qq, normalized_text)
        )

    async def _group_text_fallback(
        self, group_id: str, text_segments: list[dict[str, Any]], normalized_text: str, *,
        mode: str, fallback: bool, keyboard: str = "",
    ) -> bool:
        """群聊版的同上（判据一致，只是发送接口不同：segments + keyboard）。"""
        if mode != "both" and not fallback:
            self.plugin.logger.info("当前通道不支持语音，且未允许回退文本，跳过群聊回复")
            return False
        if not normalized_text:
            return False
        return self._confirm_send(
            await self.plugin.qq_client.send_group_message_segments(
                group_id, text_segments, keyboard=keyboard,
            ),
        )

    @staticmethod
    def _confirm_send(result) -> bool:
        """Falsy result == the send was never confirmed.

        Both clients report a receipt for every sender now (Open Platform
        message id; NapCat echo round-trip), so there is no "no channel"
        case left to special-case — and no per-call flag to forget on a
        fallback path."""
        return bool(result)

    async def deliver_private_reply(self, target_qq: str, text: str, *, voice_text: str = "", fallback_to_text_on_voice_failure: bool) -> bool:
        normalized_text = self.plugin._validate_outbound_message(text)
        mode = self.plugin._get_reply_mode()
        if mode == "text":
            return self._confirm_send(
                await self.plugin.qq_client.send_message(target_qq, normalized_text)
            )
        if not self._client_supports_voice():
            # 这个通道发不出语音：直接按上面那套判据落文字，不做那次注定要丢的合成。
            return await self._private_text_fallback(
                target_qq, normalized_text, mode=mode, fallback=fallback_to_text_on_voice_failure,
            )
        # both 模式：LLM 自主决定 → 有 <record> 则语音，否则纯文字
        if mode == "both":
            voice_content = (voice_text or "").strip()
            if voice_content:
                # LLM 指定了语音内容
                text_ok = False
                if normalized_text:
                    text_ok = self._confirm_send(
                        await self.plugin.qq_client.send_message(target_qq, normalized_text)
                    )
                try:
                    file_uri, _ = await self.synthesize_reply_voice_file(voice_content)
                    return self._confirm_send(
                        await self.plugin.qq_client.send_private_record(target_qq, file_uri),
                    ) or text_ok
                except Exception:
                    self.plugin.logger.warning("QQ both-语音私聊发送失败，已保留文本", exc_info=True)
                    return text_ok
            else:
                # 无 <record> 标签 → 纯文字
                return self._confirm_send(
                    await self.plugin.qq_client.send_message(target_qq, normalized_text)
                )
        try:
            file_uri, _ = await self.synthesize_reply_voice_file(normalized_text)
            voice_ok = self._confirm_send(
                await self.plugin.qq_client.send_private_record(target_qq, file_uri),
            )
            if voice_ok:
                return True
            if mode == "voice" and fallback_to_text_on_voice_failure:
                # 开放平台失败吞异常返回 None（不 raise）：未确认同样要走
                # 文本回退，不能把"没发出去"当结论直接返回。
                self.plugin.logger.warning("QQ 纯语音私聊发送未确认，回退文本")
                return self._confirm_send(
                    await self.plugin.qq_client.send_message(target_qq, normalized_text)
                )
            return False
        except Exception:
            if mode == "voice" and fallback_to_text_on_voice_failure:
                self.plugin.logger.warning("QQ 纯语音私聊发送失败，回退文本", exc_info=True)
                return self._confirm_send(
                    await self.plugin.qq_client.send_message(target_qq, normalized_text)
                )
            if mode == "both" and normalized_text:
                self.plugin.logger.warning("QQ 复合私聊中的语音发送失败，已保留文本", exc_info=True)
                return False
            raise

    async def deliver_group_reply(self, group_id: str, text: str, *, reply_message_id: str = "", at_user_id: str = "", keyboard: str = "", voice_text: str = "", fallback_to_text_on_voice_failure: bool) -> bool:
        normalized_text = self.plugin._validate_outbound_message(text)
        mode = self.plugin._get_reply_mode()
        text_segments: list[dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            text_segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            text_segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        text_segments.append({"type": "text", "data": {"text": f" {normalized_text}" if at_user_id else normalized_text}})
        if mode == "text":
            return self._confirm_send(
                await self.plugin.qq_client.send_group_message_segments(group_id, text_segments, keyboard=keyboard),
            )
        if not self._client_supports_voice():
            # 与私聊那条同源：通道发不出语音时，别先合成一遍再判失败。
            return await self._group_text_fallback(
                group_id, text_segments, normalized_text,
                mode=mode, fallback=fallback_to_text_on_voice_failure, keyboard=keyboard,
            )
        # both 模式：LLM 自主决定 → 有 <record> 则语音，否则纯文字
        if mode == "both":
            voice_content = (voice_text or "").strip()
            if voice_content:
                text_ok = False
                if normalized_text:
                    text_ok = self._confirm_send(
                        await self.plugin.qq_client.send_group_message_segments(group_id, text_segments, keyboard=keyboard),
                    )
                try:
                    file_uri, _ = await self.synthesize_reply_voice_file(voice_content)
                    return self._confirm_send(
                        await self.plugin.qq_client.send_group_record(group_id, file_uri, reply_message_id=reply_message_id, at_user_id=at_user_id),
                    ) or text_ok
                except Exception:
                    self.plugin.logger.warning("QQ both-语音群聊发送失败，已保留文本", exc_info=True)
                    return text_ok
            else:
                return self._confirm_send(
                    await self.plugin.qq_client.send_group_message_segments(group_id, text_segments, keyboard=keyboard),
                )
        try:
            file_uri, _ = await self.synthesize_reply_voice_file(normalized_text)
            voice_ok = self._confirm_send(
                await self.plugin.qq_client.send_group_record(group_id, file_uri, reply_message_id=reply_message_id, at_user_id=at_user_id),
            )
            if voice_ok:
                return True
            if mode == "voice" and fallback_to_text_on_voice_failure:
                # 对偶私聊路径：未确认（开放平台吞异常返回 None）也回退文本。
                # 回退的这条同样要带 keyboard——否则语音一失败，用户拿到的
                # 是一句"想看哪个？"却一个按钮都没有。
                self.plugin.logger.warning("QQ 纯语音群聊发送未确认，回退文本")
                return self._confirm_send(
                    await self.plugin.qq_client.send_group_message_segments(
                        group_id, text_segments, keyboard=keyboard,
                    ),
                )
            return False
        except Exception:
            if mode == "voice" and fallback_to_text_on_voice_failure:
                self.plugin.logger.warning("QQ 纯语音群聊发送失败，回退文本", exc_info=True)
                return self._confirm_send(
                    await self.plugin.qq_client.send_group_message_segments(
                        group_id, text_segments, keyboard=keyboard,
                    ),
                )
            if mode == "both":
                self.plugin.logger.warning("QQ 复合群聊中的语音发送失败，已保留文本", exc_info=True)
                return False
            raise
