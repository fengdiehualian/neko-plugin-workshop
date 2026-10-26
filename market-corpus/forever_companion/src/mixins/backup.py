"""Panel-only backup transport and recoverable whole-dataset restoration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import tomllib
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui

from ..core import backup, backup_files
from ..core.appearance import legacy_to_gallery
from ..core.cycle import derive_cycle_params, parse_anchor_date, resolve_today, validate_cycle_params
from ..services.data_guard import DataGate

_TTL = 30 * 60
_JOURNAL = "restore-journal.json"


class BackupMixin:
    def _init_data_backup(self):
        self._data_gate = DataGate()
        self._backup_upload = None
        self._backup_preview = None
        self._backup_strict_store = False

    async def _store_read(self, key):
        result = await super()._store_read(key)
        self._backup_check_store(result)
        return result

    async def _store_write(self, key, value, label):
        result = await super()._store_write(key, value, label)
        self._backup_check_store(result)
        return result

    async def _store_delete(self, key, label):
        result = await super()._store_delete(key, label)
        self._backup_check_store(result)
        return result

    def _backup_check_store(self, result):
        if self._backup_strict_store and (not self._store_ready or not isinstance(result, Ok)):
            raise backup.BackupError("backup_restore_failed")

    async def _backup_io(self, function, *args):
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Do not release exclusivity while a worker still writes a file.
            try:
                await worker
            except Exception:
                pass
            raise

    async def _backup_snapshot(self) -> dict:
        if not self._state_trusted or not self._store_ready:
            raise backup.BackupError("backup_store_unavailable")
        enumerate_keys = getattr(self.store, "keys", None)
        if not callable(enumerate_keys):
            raise backup.BackupError("backup_store_unavailable")
        result = await enumerate_keys()
        if not isinstance(result, Ok):
            raise backup.BackupError("backup_store_unavailable")
        records = {}
        for key in result.value:
            if backup.managed_key(key):
                value = await self._store_read(key)
                if not isinstance(value, Ok):
                    raise backup.BackupError("backup_store_unavailable")
                records[key] = deepcopy(value.value)
        return records

    async def _backup_defaults(self) -> dict:
        def load():
            defaults = tomllib.loads((Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8"))
            defaults["fragments"].setdefault("daily_limit", 10)
            # Older panels persisted this option; keep their backups readable.
            defaults["capabilities"].setdefault("motion", "auto")
            for phase in defaults["tide"]["phases"].values():
                phase.setdefault("mood_note", "")
            return defaults
        return await self._backup_io(load)

    async def _backup_portable(self, source: dict) -> tuple[dict, int]:
        source = deepcopy(source)
        # Preserve custom TOML/runtime settings as overrides without pinning
        # every shipped default, and never read host model credentials.
        config = await self.config.dump(timeout=5.0)
        defaults = await self._backup_defaults()
        source["settings"] = backup.config_overrides(config, source.get("settings") or {}, defaults)
        if not isinstance(source.get("panel_appearance"), dict):
            migrated = legacy_to_gallery(source.get("panel_bg"))
            if migrated:
                gid, item, image, appearance = migrated
                index = source.setdefault("gallery_index", {"items": [], "next": 1})
                if not any(entry.get("id") == gid for entry in index["items"]):
                    index["items"].append(item)
                source[f"gallery_img/{gid}"] = image
                source["panel_appearance"] = appearance
        return backup.portable_records(source)

    async def _backup_save_export(self, root, source: dict, label: str) -> dict:
        portable, excluded = await self._backup_portable(source)
        defaults = await self._backup_defaults()
        content, manifest = await self._backup_io(backup.make_archive, portable, defaults["plugin"]["version"], excluded)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"forever-companion-{label}-{stamp}-{uuid.uuid4().hex[:8]}.fc-backup"
        path = await self._backup_io(backup_files.write_atomic, root, filename, content)
        return {"path": str(path), "directory": str(root), "bytes": len(content), "manifest": manifest}

    async def _backup_validate_settings(self, records: dict) -> None:
        defaults = await self._backup_defaults()
        settings = records.get("settings", {})

        def validate(values, schema):
            if not isinstance(values, dict):
                raise backup.BackupError("backup_invalid")
            for key, value in values.items():
                if key not in schema:
                    raise backup.BackupError("backup_version_unsupported")
                expected = schema[key]
                if isinstance(expected, dict):
                    validate(value, expected)
                elif type(expected) is float:
                    if type(value) not in (int, float):
                        raise backup.BackupError("backup_invalid")
                elif type(value) is not type(expected):
                    raise backup.BackupError("backup_invalid")
                elif isinstance(expected, list) and any(not isinstance(item, str) for item in value):
                    raise backup.BackupError("backup_invalid")

        validate(settings, {section: defaults[section] for section in backup.CONFIG_SECTIONS})
        tide = {**defaults["tide"], **settings.get("tide", {})}
        try:
            for name in ["", *backup.roles(records)]:
                cycle = records.get(f"cycle@{name}") or {}
                params = {**tide, **(cycle.get("params") or {})}
                anchor = parse_anchor_date(cycle.get("anchor_date") or tide.get("anchor_date") or "2026-01-01")
                numeric = {key: int(params[key]) for key in ("cycle_length", "period_length", "ovulation_day", "ovulation_window")}
                if params.get("auto_derive", True):
                    numeric = derive_cycle_params(numeric["cycle_length"])
                validate_cycle_params(anchor=anchor, **numeric)
                if not 0 <= int(cycle.get("advance_days") or 0) <= 1_000_000:
                    raise ValueError
            resolve_today(tide.get("timezone", "auto"))
        except (ValueError, TypeError, OverflowError):
            raise backup.BackupError("backup_invalid") from None

    def _backup_restore_plan(self, source: dict, current: dict) -> dict:
        plan = deepcopy(source)
        kept = []
        kept_images = {
            key: deepcopy(value) for key, value in current.items()
            if key.startswith("gallery_img/") and not backup.static_image(value)
        }
        if "panel_bg" in current and not backup.static_image(current["panel_bg"]):
            plan["panel_bg"] = deepcopy(current["panel_bg"])
        current_index = current.get("gallery_index") or {}
        for item in current_index.get("items") or []:
            if item.get("kind") == "video" or not backup.static_image(current.get(f"gallery_img/{item['id']}")):
                kept.append(deepcopy(item))
                image_key = f"gallery_img/{item['id']}"
                if image_key in current:
                    kept_images[image_key] = deepcopy(current[image_key])
        imported = plan["gallery_index"]["items"]
        if len(kept) + len(imported) > 24:
            raise backup.BackupError("backup_gallery_full")
        reserved = {item["id"] for item in kept} | {key.removeprefix("gallery_img/") for key in kept_images}
        for item in imported:
            gid = item["id"]
            if gid in reserved:
                new_id = f"backup-{uuid.uuid4().hex}"
                item["id"] = new_id
                plan[f"gallery_img/{new_id}"] = plan.pop(f"gallery_img/{gid}")
                if (plan.get("panel_appearance") or {}).get("bg_id") == gid:
                    plan["panel_appearance"]["bg_id"] = new_id
            reserved.add(item["id"])
        plan.update(kept_images)
        # Retain local excluded media and their registry; replace only static
        # images and portable business data. Never call store.clear().
        plan["gallery_index"]["items"] = kept + imported
        plan["gallery_index"]["next"] = max(
            int(current_index.get("next") or 1), int(plan["gallery_index"].get("next") or 1), len(kept) + len(imported) + 1,
        )
        return plan

    async def _backup_reload(self):
        self._shards = {}
        self._lanlan_index = []
        self._settings_override = {}
        self._caps_off = {}
        self._calendar_cache = ((), None)
        self._current_lanlan_cache = ("", -1000.0)
        self._known_catgirls_cache = (None, -1000.0)
        self._host_slot_cache = {}
        self._channel_break = {}
        self._review_writing.clear()
        self._journal_writing.clear()
        self._backup_strict_store = True
        try:
            settings = await self._store_read("settings")
            self._settings_override = deepcopy(settings.value or {})
            await self._refresh_config()
            await self._load_state()
            if not self._state_trusted or not self._store_ready:
                raise backup.BackupError("backup_restore_failed")
            await self._refresh_config()
            self._sync_tool_visibility()
            self._sync_debug_entries()
        finally:
            self._backup_strict_store = False

    async def _backup_write_records(self, records: dict, delete_keys: list[str]):
        for key, value in records.items():
            if not isinstance(await self._store_write(key, value, "backup restore"), Ok):
                raise backup.BackupError("backup_restore_failed")
        for key in delete_keys:
            if not isinstance(await self._store_delete(key, "backup restore"), Ok):
                raise backup.BackupError("backup_restore_failed")

    async def _backup_rollback(self, root, journal):
        if (
            not isinstance(journal, dict) or journal.get("version") != 1
            or not isinstance(journal.get("before"), dict) or not isinstance(journal.get("missing"), list)
            or any(not isinstance(key, str) or not backup.managed_key(key) for key in [*journal["before"], *journal["missing"]])
        ):
            raise backup.BackupError("backup_recovery_required")
        await self._backup_write_records(journal["before"], journal["missing"])

    async def _backup_recover(self):
        if not callable(getattr(self, "data_path", None)):
            return
        root = await self._backup_io(backup_files.root_for, self)
        path = backup_files.checked(root, _JOURNAL)
        if not path.exists():
            self._data_gate.blocked = False
            return
        self._data_gate.blocked = True
        if not await self._ensure_store_ready():
            raise backup.BackupError("backup_recovery_required")
        content = await self._backup_io(backup_files.read, root, _JOURNAL, backup.MAX_BYTES * 2)
        journal = await self._backup_io(backup._json_load, content)
        await self._backup_rollback(root, journal)
        await self._backup_reload()
        await self._backup_io(backup_files.remove, root, _JOURNAL)
        self._data_gate.blocked = False

    async def _backup_apply(self, root, source):
        current = await self._backup_snapshot()
        plan = self._backup_restore_plan(source, current)
        previous = await self._backup_save_export(root, current, "before-restore")
        # Include keys the reload/migration path may create, including the
        # current host role if it is absent from the incoming backup.
        affected = set(current) | set(plan)
        names = set(backup.roles(current)) | set(backup.roles(plan)) | {await self._resolve_current_lanlan()}
        for name in names:
            affected.update(f"{prefix}{name}" for prefix in backup.ROLE_PREFIXES)
        journal = {
            "version": 1, "before": current,
            "missing": sorted(affected - set(current)),
        }
        self._data_gate.blocked = True
        try:
            await self._backup_io(backup_files.write_atomic, root, _JOURNAL, backup.json_bytes(journal))
            await self._backup_write_records(plan, sorted(set(current) - set(plan)))
            await self._backup_reload()
            await self._backup_io(backup_files.remove, root, _JOURNAL)
        except BaseException:
            try:
                # A cancellation may arrive after the commit marker was
                # removed. Re-establish recovery before starting rollback.
                await self._backup_io(backup_files.write_atomic, root, _JOURNAL, backup.json_bytes(journal))
                await self._backup_rollback(root, journal)
                await self._backup_reload()
                await self._backup_io(backup_files.remove, root, _JOURNAL)
                self._data_gate.blocked = False
            except BaseException:
                raise backup.BackupError("backup_recovery_required") from None
            raise backup.BackupError("backup_restore_failed") from None
        self._data_gate.blocked = False
        # The current machine's proactive watermark is deliberately retained;
        # the existing supervisor reconciles it against restored mood state.
        return {"restored": True, "previous": previous, "summary": backup.summary(source)}

    @ui.action(label=tr("actions.dataBackup.label", default="备份与恢复"))
    @plugin_entry(
        id="data_backup",
        name=tr("actions.dataBackup.label", default="备份与恢复"),
        description="Private whole-plugin backup and restore for the hosted panel.",
        timeout=120,
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["export", "begin", "chunk", "preview", "apply", "abort", "recover"]},
                "upload_id": {"type": "string"}, "size": {"type": "integer"},
                "offset": {"type": "integer"}, "data_b64": {"type": "string"},
                "confirm": {"type": "boolean"}, "token": {"type": "string"},
            },
            "required": ["op"],
        },
        metadata={"agent_hidden": True},
    )
    async def data_backup(
        self, op: str = "", upload_id: str = "", size: int = 0, offset: int = 0,
        data_b64: str = "", confirm: bool = False, token: str = "", **_: Any,
    ):
        if not self._data_gate.enter(exclusive=True):
            return Err(SdkError("backup_busy"))
        try:
            if self._data_gate.blocked and op != "recover":
                raise backup.BackupError("backup_recovery_required")
            root = await self._backup_io(backup_files.root_for, self)
            if backup_files.checked(root, _JOURNAL).exists():
                self._data_gate.blocked = True
                if op != "recover":
                    raise backup.BackupError("backup_recovery_required")
            if op == "recover":
                await self._backup_recover()
                return Ok({"recovered": True})
            if op == "export":
                return Ok(await self._backup_save_export(root, await self._backup_snapshot(), "backup"))
            if op == "begin":
                if type(size) is not int or not 0 < size <= backup.MAX_BYTES:
                    raise backup.BackupError("backup_too_large")
                if self._backup_upload:
                    await self._backup_io(backup_files.remove, root, self._backup_upload["file"])
                await self._backup_io(backup_files.cleanup_imports, root)
                uid = uuid.uuid4().hex
                name = f"import-{uid}.part"
                await self._backup_io(backup_files.write_atomic, root, name, b"")
                self._backup_upload = {"id": uid, "file": name, "size": size, "received": 0, "at": time.time()}
                self._backup_preview = None
                return Ok({"upload_id": uid, "chunk_bytes": backup.CHUNK_BYTES})
            session = self._backup_upload
            if not session or session["id"] != upload_id or time.time() - session["at"] >= _TTL:
                raise backup.BackupError("backup_upload_invalid")
            if op == "abort":
                await self._backup_io(backup_files.remove, root, session["file"])
                self._backup_upload = self._backup_preview = None
                return Ok({"aborted": True})
            if op == "chunk":
                if type(offset) is not int or offset < 0 or offset % backup.CHUNK_BYTES or not isinstance(data_b64, str) or len(data_b64) > backup.CHUNK_BYTES * 4 // 3 + 4:
                    raise backup.BackupError("backup_upload_invalid")
                try:
                    content = base64.b64decode(data_b64, validate=True)
                except ValueError:
                    raise backup.BackupError("backup_upload_invalid") from None
                if len(content) != min(backup.CHUNK_BYTES, session["size"] - offset):
                    raise backup.BackupError("backup_upload_invalid")
                session["received"] = await self._backup_io(backup_files.append, root, session["file"], offset, content, session["size"])
                session["at"] = time.time()
                self._backup_preview = None
                return Ok({"received": session["received"]})
            if session["received"] != session["size"]:
                raise backup.BackupError("backup_upload_invalid")
            content = await self._backup_io(backup_files.read, root, session["file"])
            if len(content) != session["size"]:
                raise backup.BackupError("backup_upload_invalid")
            records, manifest = await self._backup_io(backup.read_archive, content)
            await self._backup_validate_settings(records)
            digest = hashlib.sha256(content).hexdigest()
            if op == "preview":
                current = await self._backup_snapshot()
                self._backup_restore_plan(records, current)
                self._backup_preview = {"id": upload_id, "token": digest}
                return Ok({"manifest": manifest, "current": backup.summary((await self._backup_portable(current))[0]), "token": digest})
            if op != "apply" or confirm is not True or self._backup_preview != {"id": upload_id, "token": token} or token != digest:
                raise backup.BackupError("backup_confirmation_required")
            result = await self._backup_apply(root, records)
            try:
                await self._backup_io(backup_files.remove, root, session["file"])
            except backup.BackupError:
                self.logger.warning("backup import staging cleanup pending")
            self._backup_upload = self._backup_preview = None
            return Ok(result)
        except backup.BackupError as exc:
            return Err(SdkError(exc.code))
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            return Err(SdkError("backup_invalid"))
        finally:
            self._data_gate.leave(exclusive=True)
