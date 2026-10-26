"""Volcano Engine Ark image-to-image API adapter."""
import asyncio

from ._config import SelfieError
from ._media import decode_base64, request_json, resolve_image


async def generate(client, settings, prompt, reference):
    result = await request_json(client, settings, {
        "model": settings.model,
        "prompt": prompt,
        "image": reference,
        "size": settings.size,
        "response_format": "b64_json",
        "stream": False,
        "watermark": True,
    })
    images = result.get("data")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise SelfieError("no_image")
    image = images[0]
    if image.get("b64_json"):
        return await asyncio.to_thread(decode_base64, image["b64_json"])
    if image.get("url"):
        return await resolve_image(client, image["url"])
    raise SelfieError("no_image")
