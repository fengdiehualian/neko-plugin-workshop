"""Non-streaming chat image-to-image API adapter."""
import asyncio

from ._media import extract_chat_image, request_json, resolve_image


async def generate(client, settings, prompt, reference):
    result = await request_json(client, settings, {
        "model": settings.model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": reference}},
        ]}],
        "stream": False,
    })
    image = await asyncio.to_thread(extract_chat_image, result)
    return await resolve_image(client, image)
