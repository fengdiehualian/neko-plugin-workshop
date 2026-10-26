"""N.E.K.O SDK v2 API preset management plugin."""

import asyncio
from datetime import datetime, timezone

import httpx
from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr, ui

from .connectivity import test_preset
from .domain import PresetError, build_payload, public_preset
from .host import NekoHost
from .storage import PresetRepository


@neko_plugin
class ApiPresetsPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = asyncio.Lock()
        self._stopping = False
        self._repository = PresetRepository(self.data_path("presets.json"))
        # One bounded public snapshot (<=13 groups), replaced each test/save;
        # no keys, background jobs, timers or per-user cache.
        self._last_test = None

    async def _disk(self, function, *args):
        # Finish atomic writes before releasing the operation lock on cancellation.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                # Every retry stays shielded: repeated cancellation must not
                # release the operation lock while the disk thread is running.
                if task.cancelled():
                    raise
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _host(self):
        cfg = await self.config.dump(timeout=5)
        section = cfg.get("apibook", {}) if isinstance(cfg, dict) else {}
        if not isinstance(section, dict):
            raise PresetError("invalid_port")
        port = section.get("main_server_port", 0)
        if port == 0:
            # Runtime port only; never load the main application's credential file.
            from config import MAIN_SERVER_PORT

            port = MAIN_SERVER_PORT
        return NekoHost(port)

    def _error(self, code):
        return Err(SdkError(self.i18n.t("errors." + code, default="Operation failed."), code=code))

    async def _run(self, operation):
        if self._stopping:
            return self._error("stopped")
        if self._lock.locked():
            return self._error("busy")
        async with self._lock:
            try:
                return Ok(await operation())
            except PresetError as exc:
                return self._error(str(exc))
            except (httpx.TimeoutException, TimeoutError):
                return self._error("host_timeout")
            except httpx.HTTPError:
                return self._error("host_unavailable")
            except (UnicodeError, ValueError):
                return self._error("invalid_storage")
            except OSError:
                return self._error("storage_failed")
            except Exception:
                return self._error("internal")

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._stopping = True
        async with self._lock:
            self._last_test = None
            return Ok({"stopped": True})

    @lifecycle(id="startup")
    async def startup(self, **_):
        self._stopping = False
        await self._disk(self._repository.migrate_legacy_id)
        return Ok({"ready": True})

    @ui.context(id="presets")
    async def context(self):
        async def read():
            data = await self._disk(self._repository.read)
            catalog, catalog_error = None, None
            try:
                catalog = await (await self._host()).catalog()
            except Exception:
                catalog_error = self.i18n.t("errors.host_unavailable", default="N.E.K.O is unavailable.")
            return {
                "presets": [item | {"preset": public_preset(item["preset"])} for item in data["presets"]],
                "last_application": data.get("last_application"), "catalog": catalog,
                "catalog_error": catalog_error, "max_presets": 100,
                "connectivity": getattr(self, "_last_test", None),
            }

        result = await self._run(read)
        if isinstance(result, Err):
            return {"presets": [], "error": str(result.error)}
        return result.value

    @ui.action(label=tr("actions.save", default="Save"), refresh_context=True)
    @plugin_entry(id="save_preset", persist=False, timeout=15, llm_result_fields=[])
    async def save_preset(self, preset: dict, preset_id: str = "", revision: int = 0, **_):
        async def save():
            item = await self._disk(self._repository.save, preset, preset_id, revision)
            self._last_test = None
            return item | {"preset": public_preset(item["preset"])}

        return await self._run(save)

    @ui.action(label=tr("actions.test", default="Test connectivity"), refresh_context=True)
    @plugin_entry(id="test_preset", persist=False, timeout=90, llm_result_fields=[])
    async def test_preset(self, preset_id: str, revision: int, **_):
        async def test():
            data = await self._disk(self._repository.read)
            item = self._repository.find(data, preset_id)
            if item["revision"] != revision:
                raise PresetError("conflict")
            self._last_test = None
            result = await asyncio.wait_for(test_preset(await self._host(), item["preset"]), timeout=80)
            self._last_test = result | {"preset_id": preset_id, "revision": revision}
            return self._last_test

        return await self._run(test)

    @ui.action(label=tr("actions.copy", default="Copy"), refresh_context=True)
    @plugin_entry(id="copy_preset", persist=False, timeout=15, llm_result_fields=[])
    async def copy_preset(self, preset_id: str, name: str, **_):
        async def copy():
            item = await self._disk(self._repository.copy, preset_id, name)
            return item | {"preset": public_preset(item["preset"])}

        return await self._run(copy)

    @ui.action(label=tr("actions.import", default="New from current API settings"))
    @plugin_entry(id="current_api_draft", persist=False, timeout=20, llm_result_fields=[])
    async def current_api_draft(self, **_):
        async def read():
            return await (await self._host()).current_draft()

        return await self._run(read)

    @ui.action(label=tr("actions.delete", default="Delete"), refresh_context=True)
    @plugin_entry(id="delete_preset", persist=False, timeout=15, llm_result_fields=[])
    async def delete_preset(self, preset_id: str, revision: int, **_):
        async def delete():
            await self._disk(self._repository.delete, preset_id, revision)
            if (getattr(self, "_last_test", None) or {}).get("preset_id") == preset_id:
                self._last_test = None
            return {"deleted": True}

        return await self._run(delete)

    @ui.action(label=tr("actions.apply", default="Apply to N.E.K.O"), refresh_context=True)
    @plugin_entry(id="apply_preset", persist=False, timeout=100, llm_result_fields=[])
    async def apply_preset(self, preset_id: str, revision: int, confirmed: bool = False, **_):
        async def apply():
            if confirmed is not True:
                raise PresetError("confirmation_required")
            data = await self._disk(self._repository.read)
            item = self._repository.find(data, preset_id)
            if item["revision"] != revision:
                raise PresetError("conflict")
            host = await self._host()
            payload = build_payload(item["preset"], await host.catalog())
            status = {
                "preset_id": preset_id, "revision": revision, "name": item["preset"]["name"],
                "time": datetime.now(timezone.utc).isoformat(), "status": "unknown",
            }
            await self._disk(self._repository.application, status)
            result = await asyncio.wait_for(host.apply(payload), timeout=80)
            status["status"] = "success"
            await self._disk(self._repository.application, status)
            return {"applied": True, **result}

        return await self._run(apply)
