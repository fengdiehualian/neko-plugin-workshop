"""Bounded image IO and response decoding shared by both selfie providers."""
import asyncio
import base64
import binascii
import ipaddress
import json
import re
import socket
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from ._config import SelfieError

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
_IMAGE_DATA = re.compile(r"data:image/(?:png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=]+)", re.I)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?(https://[^\s<>]+?)>?\s*\)", re.I)
_BARE_IMAGE = re.compile(r"https://[^\s<>\"']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>\"']*)?", re.I)


async def read_response(response, limit):
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise SelfieError("response")
    if not 200 <= response.status_code < 300:
        raise SelfieError("http")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > limit:
            raise SelfieError("too_large")
        body.extend(chunk)
    return bytes(body)


async def request_json(client, settings, payload):
    headers = {"Authorization": f"Bearer {settings.api_key}", "Accept-Encoding": "identity"}
    async with client.stream("POST", settings.endpoint, json=payload, headers=headers) as response:
        body = await read_response(response, MAX_RESPONSE_BYTES)
    try:
        result = await asyncio.to_thread(json.loads, body)
    except (ValueError, UnicodeError, RecursionError):
        raise SelfieError("response") from None
    if not isinstance(result, dict) or result.get("error"):
        raise SelfieError("response")
    return result


async def validate_download_url(url):
    try:
        parsed = urlsplit(url)
        parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError()
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM,
        )
        if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise SelfieError("image") from None


async def download_image(client, url):
    # Never forward the provider's Authorization header to an image CDN.
    for _ in range(4):
        await validate_download_url(url)
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise SelfieError("image")
                url = str(response.url.join(location))
                continue
            data = await read_response(response, MAX_IMAGE_BYTES)
            if not data:
                raise SelfieError("image")
            return data
    raise SelfieError("image")


def decode_base64(value):
    if not isinstance(value, str) or not value or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise SelfieError("image")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise SelfieError("image") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise SelfieError("too_large")
    return data


async def resolve_image(client, value):
    if not isinstance(value, str):
        raise SelfieError("image")
    match = _IMAGE_DATA.fullmatch(value)
    if match:
        return await asyncio.to_thread(decode_base64, match.group(1))
    return await download_image(client, value)


def normalize_reference(data):
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 16 * 1024 * 1024:
                raise SelfieError("too_large")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            output = BytesIO()
            rgb.save(output, "JPEG", quality=90)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise SelfieError("image") from None
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def read_local_image(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise SelfieError("reference") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise SelfieError("too_large")
    return data


async def load_reference(client, value, plugin_dir):
    if value.startswith(("https://", "data:")):
        data = await resolve_image(client, value)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = plugin_dir / path
        data = await asyncio.to_thread(read_local_image, path)
    return await asyncio.to_thread(normalize_reference, data)


def extract_chat_image(result):
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise SelfieError("response")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise SelfieError("response")
    candidates = []
    images = message.get("images")
    if isinstance(images, list):
        candidates.extend(images)
    content = message.get("content")
    if isinstance(content, list):
        candidates.extend(content)
    elif isinstance(content, str):
        candidates.append(content)
    for item in candidates:
        if isinstance(item, dict):
            encoded = item.get("b64_json")
            if isinstance(encoded, str) and encoded:
                return "data:image/png;base64," + encoded
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            if isinstance(image_url, str) and image_url:
                return image_url
            if item.get("type") in ("image", "image_url", "output_image") and isinstance(item.get("url"), str):
                return item["url"]
            item = item.get("text", "")
        if isinstance(item, str):
            match = _IMAGE_DATA.search(item)
            if match:
                return match.group(0)
            match = _MARKDOWN_IMAGE.search(item) or _BARE_IMAGE.search(item)
            if match:
                return match.group(1) if match.re is _MARKDOWN_IMAGE else match.group(0)
    raise SelfieError("no_image")
