"""media_generator — 各媒体生成 API 的轻量封装。

当前支持的 provider：
- dashscope（通义万相）：文生图（异步任务 + 轮询）
- openai（OpenAI 兼容）：文生图（/images/generations）
- doubao（火山方舟 Seedance）：文生视频（异步任务 + 轮询）
- kling（可灵）：文生视频（JWT 签名 + 异步任务 + 轮询）

API Key 一律从环境变量读取（N.E.K.O 进程环境）：
- DASHSCOPE_API_KEY
- OPENAI_API_KEY / OPENAI_BASE_URL（可选，默认 https://api.openai.com/v1）/ OPENAI_IMAGE_MODEL（默认 dall-e-3）
- DOUBAO_ARK_API_KEY / DOUBAO_ARK_ENDPOINT（Seedance 模型 endpoint 或 model id，必填）/ DOUBAO_ARK_REGION（默认 cn-beijing）
- KLING_API_KEY / KLING_SECRET_KEY

设计原则：不依赖 N.E.K.O 内部模块，仅用 httpx + 标准库，插件可独立发布。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# ══════════ 配置读取 ══════════


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def dashscope_api_key() -> str:
    return _env("DASHSCOPE_API_KEY")


def openai_api_key() -> str:
    return _env("OPENAI_API_KEY")


def openai_base_url() -> str:
    return _env("OPENAI_BASE_URL", "OPENAI_API_BASE").rstrip("/") or "https://api.openai.com/v1"


def openai_image_model() -> str:
    return _env("OPENAI_IMAGE_MODEL") or "dall-e-3"


def dashscope_image_model() -> str:
    return _env("DASHSCOPE_IMAGE_MODEL") or "wanx2.1-t2i-turbo"


def doubao_ark_api_key() -> str:
    return _env("DOUBAO_ARK_API_KEY", "ARK_API_KEY")


def doubao_ark_endpoint() -> str:
    return _env("DOUBAO_ARK_ENDPOINT", "DOUBAO_SEEDANCE_MODEL")


def doubao_ark_region() -> str:
    return _env("DOUBAO_ARK_REGION") or "cn-beijing"


def kling_api_key() -> str:
    return _env("KLING_API_KEY")


def kling_secret_key() -> str:
    return _env("KLING_SECRET_KEY")


def list_configured_providers() -> list[str]:
    """返回已配置可用（有 key）的 provider 列表，供 auto 选择与提示。"""
    available: list[str] = []
    if dashscope_api_key():
        available.append("dashscope")
    if openai_api_key():
        available.append("openai")
    if doubao_ark_api_key() and doubao_ark_endpoint():
        available.append("doubao")
    if kling_api_key() and kling_secret_key():
        available.append("kling")
    return available


# ══════════ 小工具 ══════════

SIZE_ALLOWED = {"1024x1024", "768x1024", "1024x768", "512x512"}


def normalize_size(size: str) -> str:
    """校验并归一化图片尺寸，非法值回退默认并提示。"""
    s = (size or "").strip().lower().replace("×", "x").replace("*", "x")
    if s not in SIZE_ALLOWED:
        return "1024x1024"
    return s


def _fmt_url(url: str) -> str:
    return url.strip().strip('"').strip("'")


# ══════════ DashScope 文生图 ══════════


async def generate_image_dashscope(prompt: str, size: str = "1024x1024") -> dict:
    """通义万相文生图：提交异步任务并轮询，返回图片 URL 或带 file:// 的本地路径。"""
    key = dashscope_api_key()
    if not key:
        return {"is_error": True, "error": "NO_DASHSCOPE_KEY",
                "output": "未配置 DASHSCOPE_API_KEY 环境变量。请设置后重试，或改用 provider=openai。"}
    headers = {"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable", "Content-Type": "application/json"}
    payload = {
        "model": dashscope_image_model(),
        "input": {"prompt": prompt},
        "parameters": {"size": normalize_size(size), "n": 1},
    }
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
                headers=headers, json=payload,
            )
            if resp.status_code != 200:
                return {"is_error": True, "error": "DASHSCOPE_HTTP",
                        "output": f"DashScope 文生图请求失败（HTTP {resp.status_code}）：{resp.text[:300]}"}
            task_id = (resp.json().get("output") or {}).get("task_id", "")
            if not task_id:
                return {"is_error": True, "error": "DASHSCOPE_NO_TASK",
                        "output": f"DashScope 未返回 task_id：{resp.text[:300]}"}
            url = await _poll_dashscope_task(client, task_id)
        if url.startswith("http"):
            return {"is_error": False, "output": f"已生成图片（{dashscope_image_model()}）：\n{url}", "url": url}
        return {"is_error": False, "output": f"已生成图片并保存到本地：{url}", "url": url}
    except httpx.TimeoutException:
        return {"is_error": True, "error": "TIMEOUT", "output": "DashScope 请求超时（30s），请稍后重试。"}
    except Exception as exc:  # noqa: BLE001 - 统一转成对模型友好的错误
        return {"is_error": True, "error": "UNEXPECTED", "output": f"DashScope 文生图异常：{exc}"}


async def _poll_dashscope_task(client: httpx.AsyncClient, task_id: str) -> str:
    """轮询 DashScope 任务直到成功/失败，返回结果 URL。"""
    url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {dashscope_api_key()}"}
    for _ in range(40):  # 最多约 40 * 3s = 120s
        await asyncio_sleep(3.0)
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            continue
        out = resp.json().get("output") or {}
        status = out.get("task_status", "")
        if status == "SUCCEEDED":
            results = out.get("results") or []
            if results and results[0].get("url"):
                return _fmt_url(results[0]["url"])
            return "已生成，但服务端未返回图片 URL。"
        if status in ("FAILED", "CANCELED"):
            msg = (out.get("message") or {}).get("content", "未知失败原因")
            raise RuntimeError(f"DashScope 任务失败：{msg}")
    raise RuntimeError("DashScope 任务超时（120s），请稍后重试。")


def asyncio_sleep(seconds: float) -> asyncio.coroutines.Coroutine[asyncio.Any, asyncio.Any, None]:
    """延迟实现（兼容局部导入场景的统一入口）。"""
    return asyncio.sleep(seconds)


# ══════════ OpenAI 兼容文生图 ══════════


async def generate_image_openai(prompt: str, size: str = "1024x1024", save_dir: Path | None = None) -> dict:
    """OpenAI 兼容文生图：同步返回 URL；若端点只回 b64，保存到本地并返回文件路径。"""
    key = openai_api_key()
    if not key:
        return {"is_error": True, "error": "NO_OPENAI_KEY",
                "output": "未配置 OPENAI_API_KEY 环境变量。请设置后重试，或改用 provider=dashscope。"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": openai_image_model(), "prompt": prompt, "n": 1, "size": normalize_size(size)}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{openai_base_url()}/images/generations", headers=headers, json=payload)
            if resp.status_code != 200:
                return {"is_error": True, "error": "OPENAI_HTTP",
                        "output": f"OpenAI 兼容文生图失败（HTTP {resp.status_code}）：{resp.text[:300]}"}
            data = (resp.json().get("data") or [{}])[0]
            if data.get("url"):
                return {"is_error": False, "output": f"已生成图片（{openai_image_model()}）：\n{data['url']}", "url": data["url"]}
            b64 = data.get("b64_json", "")
            if b64 and save_dir is not None:
                save_dir.mkdir(parents=True, exist_ok=True)
                path = save_dir / f"media_gen_{uuid.uuid4().hex[:12]}.png"
                path.write_bytes(base64.b64decode(b64))
                return {"is_error": False, "output": f"已生成图片并保存到本地：\n{path}", "url": str(path)}
            return {"is_error": True, "error": "OPENAI_NO_DATA", "output": "OpenAI 返回中既无 URL 也无 b64 数据。"}
    except httpx.TimeoutException:
        return {"is_error": True, "error": "TIMEOUT", "output": "OpenAI 请求超时（30s），请稍后重试。"}
    except Exception as exc:  # noqa: BLE001
        return {"is_error": True, "error": "UNEXPECTED", "output": f"OpenAI 文生图异常：{exc}"}


# ══════════ 豆包（火山方舟 Seedance）文生视频 ══════════


async def generate_video_doubao(prompt: str, duration: int = 5) -> dict:
    """火山方舟 Seedance 文生视频：异步任务 + 轮询，返回视频 URL。"""
    key = doubao_ark_api_key()
    endpoint = doubao_ark_endpoint()
    if not key or not endpoint:
        return {"is_error": True, "error": "NO_DOUBAO_ARK",
                "output": "未配置 DOUBAO_ARK_API_KEY 和 DOUBAO_ARK_ENDPOINT 环境变量（Seedance 模型 endpoint 或 model id）。请设置后重试，或改用 provider=kling。"}
    dur = max(5, min(10, int(duration or 5)))
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    base = f"https://ark.{doubao_ark_region()}.volces.com/api/v3"
    payload = {"model": endpoint, "content": [{"type": "text", "text": prompt}], "duration": dur}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{base}/contents/generations/tasks", headers=headers, json=payload)
            if resp.status_code != 200:
                return {"is_error": True, "error": "ARK_HTTP",
                        "output": f"豆包视频生成请求失败（HTTP {resp.status_code}）：{resp.text[:300]}"}
            task_id = resp.json().get("id", "")
            if not task_id:
                return {"is_error": True, "error": "ARK_NO_TASK", "output": f"豆包未返回任务 id：{resp.text[:300]}"}
            video_url = await _poll_ark_task(client, headers, f"{base}/contents/generations/tasks/{task_id}")
        return {"is_error": False, "output": f"已生成视频（{endpoint}，{dur}s）：\n{video_url}", "url": video_url}
    except httpx.TimeoutException:
        return {"is_error": True, "error": "TIMEOUT", "output": "豆包视频生成请求超时（30s），请稍后重试。"}
    except Exception as exc:  # noqa: BLE001
        return {"is_error": True, "error": "UNEXPECTED", "output": f"豆包视频生成异常：{exc}"}


async def _poll_ark_task(client: httpx.AsyncClient, headers: dict, url: str) -> str:
    for _ in range(80):  # 视频生成较慢，最多约 80 * 3s = 240s
        await asyncio_sleep(3.0)
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            continue
        data = resp.json()
        status = data.get("status", "")
        if status == "success":
            content = data.get("content") or {}
            v = content.get("video_url") or content.get("url") or ""
            if v:
                return _fmt_url(v)
            return "已生成视频，但服务端未返回视频 URL。"
        if status in ("failed", "canceled"):
            err = data.get("error") or {}
            raise RuntimeError(f"豆包视频任务失败：{err.get('message', '未知原因')}")
    raise RuntimeError("豆包视频任务超时（240s），请稍后重试。")


# ══════════ 可灵文生视频 ══════════


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_kling_jwt(api_key: str, secret_key: str) -> str:
    """生成可灵 API 的 HS256 JWT（标准库实现，无外部依赖）。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": api_key, "exp": now + 1800, "nbf": now - 5}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}.{_b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    sig = hmac.new(secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def _kling_auth_header() -> str:
    return f"Bearer {build_kling_jwt(kling_api_key(), kling_secret_key())}"


async def generate_video_kling(prompt: str, duration: int = 5) -> dict:
    """可灵文生视频：JWT 鉴权 + 异步任务 + 轮询，返回视频 URL。"""
    ak, sk = kling_api_key(), kling_secret_key()
    if not ak or not sk:
        return {"is_error": True, "error": "NO_KLING_KEY",
                "output": "未配置 KLING_API_KEY / KLING_SECRET_KEY 环境变量。请设置后重试，或改用 provider=doubao。"}
    dur = "5" if int(duration or 5) <= 5 else "10"
    headers = {"Authorization": _kling_auth_header(), "Content-Type": "application/json"}
    payload = {"model": "kling-v1", "prompt": prompt, "duration": dur}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post("https://api.klingai.com/v1/videos/text2video", headers=headers, json=payload)
            if resp.status_code != 200:
                return {"is_error": True, "error": "KLING_HTTP",
                        "output": f"可灵视频生成请求失败（HTTP {resp.status_code}）：{resp.text[:300]}"}
            task_id = ((resp.json().get("data") or {}).get("task_id", ""))
            if not task_id:
                return {"is_error": True, "error": "KLING_NO_TASK", "output": f"可灵未返回 task_id：{resp.text[:300]}"}
            video_url = await _poll_kling_task(client, task_id)
        return {"is_error": False, "output": f"已生成视频（可灵 kling-v1，{dur}s）：\n{video_url}", "url": video_url}
    except httpx.TimeoutException:
        return {"is_error": True, "error": "TIMEOUT", "output": "可灵视频生成请求超时（30s），请稍后重试。"}
    except Exception as exc:  # noqa: BLE001
        return {"is_error": True, "error": "UNEXPECTED", "output": f"可灵视频生成异常：{exc}"}


async def _poll_kling_task(client: httpx.AsyncClient, task_id: str) -> str:
    url = f"https://api.klingai.com/v1/videos/text2video/{task_id}"
    headers = {"Authorization": _kling_auth_header()}
    for _ in range(80):  # 最多约 240s
        await asyncio_sleep(3.0)
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            continue
        data = (resp.json().get("data") or {})
        status = data.get("task_status", "")
        if status in ("succeed", "success"):
            videos = ((data.get("task_result") or {}).get("videos")) or []
            if videos and videos[0].get("url"):
                return _fmt_url(videos[0]["url"])
            return "已生成视频，但服务端未返回视频 URL。"
        if status in ("failed", "canceled"):
            raise RuntimeError(f"可灵视频任务失败：{data.get('task_status_msg', '未知原因')}")
    raise RuntimeError("可灵视频任务超时（240s），请稍后重试。")
