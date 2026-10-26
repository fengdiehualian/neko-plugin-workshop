"""Character selfies: generate from a reference and submit an image to chat."""
import asyncio
import time

import httpx
from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, neko_plugin
from plugin.sdk.shared.i18n import tr

from . import _chat, _volcengine
from ._config import SelfieError, Settings
from ._media import load_reference

_PARAMETERS = {
    "type": "object",
    "properties": {
        "scene": {"type": "string", "maxLength": 4000, "description": tr("entry.scene")},
    },
    "required": ["scene"],
    "additionalProperties": False,
}
_IDENTITY_PROMPT = (
    "Generate one selfie of the character in the reference image. "
    "Preserve the character's identity, face, hair, and visual style. "
    "Use the following scene, pose, outfit, and expression details: "
)


@neko_plugin
class SelfiePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._busy = False
        self._last_started = float("-inf")
        self._locale = "en"

    async def _read_config(self):
        config = await self.config.dump(timeout=5.0)
        value = config.get("selfie", {}) if isinstance(config, dict) else {}
        if not isinstance(value, dict):
            raise SelfieError("config")
        locale = value.get("locale", "en")
        self._locale = locale if isinstance(locale, str) else "en"
        return value

    def _text(self, key):
        return self.i18n.t(key, locale=self._locale)

    async def _register_tool(self):
        config = await self._read_config()
        target = config.get("target_lanlan", "")
        if not isinstance(target, str):
            raise SelfieError("config")
        self.unregister_llm_tool("send_selfie")
        self.register_llm_tool(
            name="send_selfie",
            description=self._text("entry.description"),
            parameters=self.i18n.resolve(_PARAMETERS, locale=self._locale),
            handler=self.send_selfie_tool,
            timeout=270.0,
            role=target.strip() or None,
        )

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._register_tool()
        return Ok({"status": "ready"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        await self._register_tool()
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.unregister_llm_tool("send_selfie")
        return Ok({"status": "stopped"})

    def _failure(self, code):
        return {"output": {"summary": self._text("error." + code)},
                "is_error": True, "error": "SELFIE_" + code.upper()}

    async def _generate_and_push(self, scene):
        if not isinstance(scene, str) or not scene.strip() or len(scene) > 4000:
            return self._failure("scene")
        if self._busy:
            return self._failure("busy")
        # Set before the first await so overlapping calls cannot both spend money.
        self._busy = True
        try:
            async with asyncio.timeout(250):
                settings = Settings.from_config(await self._read_config())
                if time.monotonic() - self._last_started < settings.cooldown:
                    return self._failure("cooldown")
                async with asyncio.timeout(settings.timeout):
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
                        reference = await load_reference(client, settings.reference_image, self.plugin_dir)
                        # Failed paid requests also consume the cooldown. Never retry automatically.
                        self._last_started = time.monotonic()
                        backend = _volcengine if settings.provider == "volcengine" else _chat
                        data = await backend.generate(client, settings, _IDENTITY_PROMPT + scene.strip(), reference)
                    try:
                        part = await self.ctx.images.upload(data, timeout=10.0)
                    except (ValueError, TypeError):
                        raise SelfieError("image") from None
                    except RuntimeError:
                        raise SelfieError("upload") from None
                    receipt = self.push_message(
                        source=self.plugin_id,
                        target_lanlan=settings.target_lanlan.strip() or None,
                        visibility=["chat"],
                        ai_behavior="blind",
                        parts=[part],
                    )
                    if not isinstance(receipt, dict) or receipt.get("submitted") is not True:
                        return self._failure("delivery")
                # A receipt means local submission, not a browser-render acknowledgement.
                return {"submitted": True, "summary": self._text("result.submitted")}
        except SelfieError as error:
            return self._failure(str(error))
        except (TimeoutError, httpx.TimeoutException):
            return self._failure("timeout")
        except httpx.HTTPError:
            return self._failure("http")
        except Exception:
            # Provider bodies, API keys, prompts, and image URLs must not reach logs or the model.
            return self._failure("internal")
        finally:
            self._busy = False

    async def send_selfie_tool(self, scene: str, **_):
        return await self._generate_and_push(scene)
