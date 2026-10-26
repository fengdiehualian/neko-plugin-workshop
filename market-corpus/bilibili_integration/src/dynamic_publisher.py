"""Publish text and image dynamics using the plugin's existing Bilibili account."""

from __future__ import annotations

import asyncio
import base64
import binascii
from io import BytesIO
from urllib.parse import urlsplit

import httpx
from bilibili_api import Credential, dynamic
from bilibili_api.exceptions import ResponseCodeException
from bilibili_api.utils.picture import Picture
from bilibili_api.utils.utils import get_api
from PIL import Image

MAX_TEXT_LENGTH = 2000
MAX_IMAGES = 9
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PUBLISH_TIMEOUT_SECONDS = 45


class DynamicInputError(ValueError):
    """An input error safe to expose without including text, images or credentials."""


class DynamicPublishUncertainError(RuntimeError):
    """The creation request may have reached Bilibili; do not retry automatically."""


def validate_dynamic_input(text: str, images: list[str] | None) -> tuple[str, list[str]]:
    if not isinstance(text, str) or (images is not None and not isinstance(images, list)):
        raise DynamicInputError("DYNAMIC_INVALID_INPUT: Expected text and an image list")
    sources = [] if images is None else list(images)
    text = text.strip()
    if len(text) > MAX_TEXT_LENGTH or len(sources) > MAX_IMAGES or (not text and not sources):
        raise DynamicInputError("DYNAMIC_INVALID_INPUT: Provide text (up to 2000 characters) or up to 9 images")
    for source in sources:
        if not isinstance(source, str) or not source:
            raise DynamicInputError("DYNAMIC_INVALID_INPUT: Images must be HTTP(S) URLs or image data URLs")
        if source.startswith("data:image/"):
            if len(source) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 128:
                raise DynamicInputError("DYNAMIC_IMAGE_TOO_LARGE: Each image must be at most 10 MiB")
        else:
            try:
                url = urlsplit(source)
                valid = url.scheme in {"http", "https"} and bool(url.hostname) and not url.username and not url.password
            except ValueError:
                valid = False
            if not valid:
                raise DynamicInputError("DYNAMIC_INVALID_INPUT: Images must be HTTP(S) URLs or image data URLs")
    return text, sources


class _MemoryPicture(Picture):
    """Upload bytes asynchronously without the SDK's shared temporary image file."""

    async def upload(self, credential: Credential) -> Picture:
        api = get_api("dynamic")["send"]["upload_img"]
        mime = "image/jpeg" if self.imageType == "jpg" else f"image/{self.imageType}"
        async with httpx.AsyncClient(timeout=15.0, cookies=await credential.get_cookies()) as client:
            response = await client.post(
                api["url"],
                data={"biz": "draw", "category": "daily", "csrf": credential.bili_jct},
                files={"file_up": (f"image.{self.imageType}", self.content, mime)},
                headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("code") != 0:
            raise ResponseCodeException(code=payload.get("code", -1), msg="Dynamic image upload rejected")
        image_url = (payload.get("data") or {}).get("image_url")
        if not isinstance(image_url, str) or not image_url.startswith(("https://", "http://")):
            raise RuntimeError("Dynamic image upload returned no URL")
        self.url = image_url
        return self


def _picture_from_bytes(content: bytes) -> Picture:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise DynamicInputError("DYNAMIC_IMAGE_TOO_LARGE: Images must be nonempty and at most 10 MiB")
    try:
        with Image.open(BytesIO(content)) as image:
            image_type = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}.get(image.format)
            if image_type is None:
                raise ValueError("Unsupported image format")
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise DynamicInputError("DYNAMIC_INVALID_IMAGE: Use a valid PNG, JPEG, GIF or WebP image") from exc
    # Picture.from_content writes to a shared temporary filename in the pinned SDK.
    # Construct the documented data object directly to keep images in memory.
    return _MemoryPicture(
        content=content, imageType=image_type, width=width, height=height,
        size=len(content) / 1024, url="",
    )


def _picture_from_data_url(source: str) -> Picture:
    try:
        header, encoded = source.split(",", 1)
        if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/gif;base64", "data:image/webp;base64"}:
            raise ValueError("Unsupported data URL")
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DynamicInputError("DYNAMIC_INVALID_IMAGE: Use a valid base64 image data URL") from exc
    return _picture_from_bytes(content)


async def _load_picture(source: str) -> Picture:
    if source.startswith("data:"):
        return await asyncio.to_thread(_picture_from_data_url, source)
    # Never send account cookies to caller-supplied image hosts.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        async with client.stream("GET", source) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES:
                    raise DynamicInputError("DYNAMIC_IMAGE_TOO_LARGE: Each image must be at most 10 MiB")
    return await asyncio.to_thread(_picture_from_bytes, bytes(content))


async def publish_dynamic(credential: Credential, text: str = "", images: list[str] | None = None) -> dict[str, str]:
    text, sources = validate_dynamic_input(text, images)
    if not credential.sessdata or not credential.bili_jct:
        raise DynamicInputError("DYNAMIC_NOT_CONFIGURED: Save Bilibili login credentials first")
    attempted = False
    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_SECONDS):
            info = dynamic.BuildDynamic.empty().add_plain_text(text)
            # Validate every image before uploading any of them.
            for source in sources:
                info.add_image(await _load_picture(source))
            attempted = True
            result = await dynamic.send_dynamic(info=info, credential=credential)
    except ResponseCodeException:
        raise
    except Exception as exc:
        if attempted:
            raise DynamicPublishUncertainError("Check Bilibili before publishing again") from exc
        raise
    dynamic_id = str(result.get("dyn_id_str") or "") if isinstance(result, dict) else ""
    if not dynamic_id.isascii() or not dynamic_id.isdigit() or int(dynamic_id) <= 0:
        raise DynamicPublishUncertainError("Bilibili returned no valid dynamic ID; check before publishing again")
    return {"dynamic_id": dynamic_id, "url": f"https://t.bilibili.com/{dynamic_id}"}
