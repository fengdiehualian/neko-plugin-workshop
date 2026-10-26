"""Private file-backed videos with legacy Store compatibility and recoverable cleanup."""

from __future__ import annotations

import asyncio
import base64
import math
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from ..core import media_files, media_playback
from ..core.appearance import (
    clamp_appearance,
    gallery_find,
    gallery_img_key,
    gallery_next_id,
    gallery_normalize_index,
    gallery_remove_item,
)
from ..core.media import (
    GalleryMediaError,
    chunk_digest,
    decode_video_chunk,
    valid_digest,
    valid_storage_id,
    validate_video_signature,
    video_chunk_count,
    video_chunk_key,
    video_chunk_size,
    video_file_manifest_key,
    video_manifest_key,
    video_mime,
    video_poster,
    video_size,
)
from ..core.state import (
    _GALLERY_IMG_PREFIX,
    _GALLERY_MAX_ITEMS,
    _GALLERY_THUMB_MAX_CHARS,
    _GALLERY_VIDEO_CHUNK_BYTES,
    _GALLERY_VIDEO_CHUNK_PREFIX,
    _GALLERY_VIDEO_DEFAULT_MAX_BYTES,
    _GALLERY_VIDEO_DEFAULT_TOTAL_BYTES,
    _GALLERY_VIDEO_FILE_MANIFEST_PREFIX,
    _GALLERY_VIDEO_FILE_REGISTRY,
    _GALLERY_VIDEO_HARD_MAX_BYTES,
    _GALLERY_VIDEO_MAX_MAX_MIB,
    _GALLERY_VIDEO_MAX_TOTAL_MIB,
    _GALLERY_VIDEO_MIMES,
    _GALLERY_VIDEO_MIN_MAX_MIB,
    _GALLERY_VIDEO_MIN_TOTAL_MIB,
    _GALLERY_VIDEO_POSTER_MAX_CHARS,
    _GALLERY_VIDEO_PREFIX,
    _GALLERY_VIDEO_RESERVE_BYTES,
    _GALLERY_VIDEO_UPLOAD_LIMIT,
    _GALLERY_VIDEO_UPLOAD_TTL_SEC,
    _STORE_GALLERY_INDEX,
    _STORE_GALLERY_UPLOADS,
    _STORE_PANEL_APPEARANCE,
    _STORE_PANEL_BG,
    _now_utc,
)

JsonObject = dict[str, Any]
_MEDIA_PREFIXES = (
    _GALLERY_IMG_PREFIX, _GALLERY_VIDEO_PREFIX, _GALLERY_VIDEO_CHUNK_PREFIX,
    _GALLERY_VIDEO_FILE_MANIFEST_PREFIX,
)
_ROOT_LOCK_GUARDS: dict[str, threading.Lock] = {}
_ROOT_LOCK_GUARDS_LOCK = threading.Lock()
_MEDIA_LOG_OPERATIONS = frozenset({
    "image", "video_begin", "video_chunk", "video_status", "video_commit", "video_abort",
    "video_policy", "gallery_remove", "media_clear", "gallery_read",
})


class MediaGalleryMixin:
    def _init_media_gallery(self) -> None:
        self._gallery_token_lock = threading.Lock()
        self._gallery_busy = False
        self._gallery_owner = uuid.uuid4().hex
        self._gallery_cleanup_pending = False
        self._gallery_touched = False
        self._playback_registered = False
        self._playback_verified: dict = {}

    async def _clear_playback(self) -> None:
        await self._media_io(media_playback.clear, self, error="video_cleanup_incomplete")
        self._playback_verified.clear()

    async def _prepare_playback(self, manifest: JsonObject, summary: JsonObject | None = None) -> str:
        register = getattr(self, "register_static_ui", None)
        if manifest["storage"] != "file" or not callable(register):
            return ""
        started = time.monotonic()
        try:
            await self._ensure_media_mutation_allowed()
            if not self._playback_registered:
                directory = await self._media_io(media_playback.initialize, self)
                if not register(str(directory), cache_control=media_playback.CACHE_CONTROL):
                    return ""
                self._playback_registered = True
                self._playback_verified.clear()
            if summary is None:
                summary = await self._media_storage_summary()
            cache_budget = max(0, summary["total_limit_bytes"] - summary["occupied_bytes"] + summary["playback_cache_bytes"])
            name, reused = await self._media_io(
                media_playback.prepare, self, manifest, self._playback_verified,
                summary["pending_bytes"], cache_budget,
                error="video_read_failed",
            )
            self.logger.info(
                "gallery playback: status={} size={} elapsed_ms={}",
                "reused" if reused else "prepared", manifest["size"],
                round((time.monotonic() - started) * 1000),
            )
            return f"/plugin/forever_companion/ui/{name}"
        except Exception as exc:
            reason = exc.code if isinstance(exc, GalleryMediaError) else type(exc).__name__
            self.logger.info(
                "gallery playback: status=fallback reason={} elapsed_ms={}",
                reason, round((time.monotonic() - started) * 1000),
            )
            return ""

    async def _media_io(self, function, *args, error: str = "video_save_failed"):
        # Keep the guard held until a cancelled worker has finished its write.
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            except Exception:
                pass
            raise
        except media_files.MediaFileError as exc:
            raise GalleryMediaError(exc.code) from None
        except OSError:
            raise GalleryMediaError(error) from None

    async def _media_root(self):
        return await self._media_io(media_files.safe_root, self, error="video_path_unsafe")

    @asynccontextmanager
    async def _gallery_guard(self):
        deadline = time.monotonic() + 10
        while True:
            with self._gallery_token_lock:
                acquired = not self._gallery_busy
                if acquired:
                    self._gallery_busy = True
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise GalleryMediaError("gallery_busy")
            await asyncio.sleep(0.01)
        file_lock = None
        process_lock = None
        try:
            # Older SDK test/runtime shims may not expose `data_path`.  The
            # Store-backed image gallery remains usable there; file-backed
            # video operations fail closed when they actually need the root.
            if not callable(getattr(self, "data_path", None)):
                self._gallery_touched = True
                yield
                return
            try:
                root = await self._media_root()
            except GalleryMediaError as exc:
                # Store-backed image actions must remain available even when
                # an SDK data root is unavailable or unsafe.  Video/clear
                # paths call _media_root again and fail closed at their actual
                # file boundary.
                if exc.code != "video_path_unsafe":
                    raise
                self._gallery_touched = True
                yield
                return
            root_key = str(root.resolve())
            with _ROOT_LOCK_GUARDS_LOCK:
                process_lock = _ROOT_LOCK_GUARDS.setdefault(root_key, threading.Lock())
            if not await asyncio.to_thread(process_lock.acquire, True, 10):
                raise GalleryMediaError("gallery_busy")
            while file_lock is None:
                file_lock = await self._media_io(media_files.try_lock, root)
                if file_lock is not None:
                    break
                if time.monotonic() >= deadline:
                    raise GalleryMediaError("gallery_busy")
                await asyncio.sleep(0.01)
            self._gallery_touched = True
            yield
        finally:
            try:
                if file_lock is not None:
                    await self._media_io(media_files.unlock, file_lock)
            finally:
                if process_lock is not None and process_lock.locked():
                    process_lock.release()
                with self._gallery_token_lock:
                    self._gallery_busy = False

    async def _gallery_call(self, method, **kwargs: Any):
        method_name = str(getattr(method, "__name__", "gallery"))
        tracked = method_name in {
            "_gallery_add_image", "_video_operation", "_gallery_remove", "_media_clear",
            "_get_gallery_image",
        }
        started = time.monotonic()
        status = "ok"
        operation = kwargs.get("op")
        if not isinstance(operation, str) or not operation:
            if method_name == "_get_gallery_image":
                operation = "gallery_read"
            else:
                operation = "image" if method_name == "_gallery_add_image" else method_name.removeprefix("_")
        if operation not in _MEDIA_LOG_OPERATIONS:
            operation = "invalid"
        chunk_index = kwargs.get("chunk_index")
        if type(chunk_index) is not int:
            chunk_index = None
        requested_size = kwargs.get("size")
        if type(requested_size) is not int or requested_size < 0:
            requested_size = None
        encoded = kwargs.get("data_b64") if operation == "video_chunk" else None
        if operation == "image":
            data_url = kwargs.get("data_url")
            if isinstance(data_url, str):
                separator = data_url.find(",", 0, 128)
                if separator >= 0:
                    encoded = data_url[separator + 1:]
        if isinstance(encoded, str):
            requested_size = len(encoded) // 4 * 3 - (
                2 if encoded.endswith("==") else 1 if encoded.endswith("=") else 0
            )
            requested_size = max(0, requested_size)
        if tracked:
            self.logger.info(
                "gallery media start: op={} chunk_index={} size={}",
                operation,
                chunk_index,
                requested_size,
            )
        try:
            async with self._gallery_guard():
                result = await method(**kwargs)
                if isinstance(result, Err):
                    error = getattr(result, "error", None)
                    status = f"error:{getattr(error, 'code', None) or str(error or 'unknown')}"
                elif operation == "gallery_read" and isinstance(result, Ok) and isinstance(result.value, dict):
                    value = result.value
                    if type(value.get("size")) is int and value["size"] >= 0:
                        requested_size = value["size"]
                    returned_data = value.get("data_b64")
                    if returned_data is None and isinstance(value.get("data_url"), str):
                        header, separator, payload = value["data_url"].partition(",")
                        if separator and header.endswith(";base64"):
                            returned_data = payload
                    if isinstance(returned_data, str):
                        requested_size = max(0, len(returned_data) // 4 * 3 - (
                            2 if returned_data.endswith("==") else 1 if returned_data.endswith("=") else 0
                        ))
                    elif isinstance(value.get("chunks"), list):
                        total = 0
                        for chunk in value["chunks"]:
                            encoded_chunk = chunk.get("data_b64") if isinstance(chunk, dict) else None
                            if isinstance(encoded_chunk, str):
                                total += len(encoded_chunk) // 4 * 3 - (
                                    2 if encoded_chunk.endswith("==") else 1 if encoded_chunk.endswith("=") else 0
                                )
                        requested_size = total
                return result
        except GalleryMediaError as exc:
            status = f"error:{exc.code}"
            return Err(SdkError(exc.code))
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            status = f"exception:{type(exc).__name__}"
            raise
        finally:
            if tracked:
                elapsed_ms = round((time.monotonic() - started) * 1000)
                self.logger.info(
                    "gallery media op: op={} chunk_index={} size={} elapsed_ms={} status={}",
                    operation,
                    chunk_index,
                    requested_size,
                    elapsed_ms,
                    status,
                )

    async def _gallery_index_strict(self) -> JsonObject:
        result = await self._store_read(_STORE_GALLERY_INDEX)
        if not isinstance(result, Ok):
            raise GalleryMediaError("gallery_update_failed")
        raw = result.value
        if raw is not None and (
            not isinstance(raw, dict) or not isinstance(raw.get("items"), list)
        ):
            raise GalleryMediaError("gallery_update_failed")
        if isinstance(raw, dict):
            if raw.get("version", 1) != 1:
                raise GalleryMediaError("video_schema_unsupported")
            for item in raw["items"]:
                if not isinstance(item, dict):
                    raise GalleryMediaError("gallery_update_failed")
                if item.get("kind", "image") not in {"image", "video"}:
                    raise GalleryMediaError("video_schema_unsupported")
        await self._file_registry()
        return gallery_normalize_index(raw)

    def _video_require_store(self) -> None:
        if not self._store_ready:
            raise GalleryMediaError("video_store_not_ready")

    @staticmethod
    def _default_file_registry() -> JsonObject:
        return {
            "version": 1, "records": {}, "key_deletions": [], "clear": None,
            "policy": {
                "single_limit_mib": _GALLERY_VIDEO_DEFAULT_MAX_BYTES // (1024 * 1024),
                "total_limit_mib": _GALLERY_VIDEO_DEFAULT_TOTAL_BYTES // (1024 * 1024),
            },
            "storage_reclaim_pending": False,
        }

    @staticmethod
    def _valid_policy(policy: Any) -> bool:
        return (
            isinstance(policy, dict)
            and type(policy.get("single_limit_mib")) is int
            and _GALLERY_VIDEO_MIN_MAX_MIB <= policy["single_limit_mib"] <= _GALLERY_VIDEO_MAX_MAX_MIB
            and type(policy.get("total_limit_mib")) is int
            and _GALLERY_VIDEO_MIN_TOTAL_MIB <= policy["total_limit_mib"] <= _GALLERY_VIDEO_MAX_TOTAL_MIB
        )

    def _validated_records(self, raw: Any, *, file_backed: bool) -> JsonObject:
        if not isinstance(raw, dict) or len(raw) > _GALLERY_MAX_ITEMS + _GALLERY_VIDEO_UPLOAD_LIMIT:
            raise GalleryMediaError("video_read_failed")
        cleaned: JsonObject = {}
        for storage_id, record in raw.items():
            if not valid_storage_id(storage_id) or not isinstance(record, dict):
                raise GalleryMediaError("video_read_failed")
            try:
                size = video_size(record.get("size"))
                expires = float(record.get("expires_at"))
            except (GalleryMediaError, TypeError, ValueError, OverflowError):
                raise GalleryMediaError("video_read_failed") from None
            count = video_chunk_count(size)
            number, hashes = record.get("next_chunk"), record.get("hashes")
            if (
                record.get("mime") not in _GALLERY_VIDEO_MIMES
                or not valid_storage_id(record.get("owner"))
                or not math.isfinite(expires)
                or type(number) is not int or not 0 <= number <= count
                or not isinstance(hashes, list) or len(hashes) != number
                or not all(valid_digest(digest) for digest in hashes)
                or not isinstance(record.get("thumb"), str)
                or len(record["thumb"]) > _GALLERY_THUMB_MAX_CHARS
                or not isinstance(record.get("poster"), str)
                or len(record["poster"]) > _GALLERY_VIDEO_POSTER_MAX_CHARS
            ):
                raise GalleryMediaError("video_read_failed")
            item_id = record.get("item_id", "")
            if not isinstance(item_id, str) or len(item_id) > 64:
                raise GalleryMediaError("video_read_failed")
            if file_backed and record.get("status") not in {"upload", "published", "deleting"}:
                raise GalleryMediaError("video_schema_unsupported")
            cleaned[storage_id] = {**record, "hashes": list(hashes), "expires_at": expires}
        return cleaned

    async def _file_registry(self) -> JsonObject:
        result = await self._store_read(_GALLERY_VIDEO_FILE_REGISTRY)
        if not isinstance(result, Ok):
            raise GalleryMediaError("video_read_failed")
        if result.value is None:
            return self._default_file_registry()
        raw = result.value
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise GalleryMediaError("video_schema_unsupported")
        if not self._valid_policy(raw.get("policy")):
            raise GalleryMediaError("video_read_failed")
        records = self._validated_records(raw.get("records"), file_backed=True)
        deletions = raw.get("key_deletions", [])
        clear = raw.get("clear")
        if not isinstance(deletions, list) or not all(self._media_key(key) for key in deletions):
            raise GalleryMediaError("video_read_failed")
        if clear is not None and (
            not isinstance(clear, dict) or not isinstance(clear.get("keys"), list)
            or not all(self._media_key(key) for key in clear["keys"])
        ):
            raise GalleryMediaError("video_schema_unsupported")
        return {**raw, "records": records, "key_deletions": list(deletions)}

    async def _save_file_registry(self, registry: JsonObject) -> None:
        self._video_require_store()
        result = await self._store_write(_GALLERY_VIDEO_FILE_REGISTRY, registry, "wallpaper recovery registry")
        if isinstance(result, Err):
            raise GalleryMediaError("video_save_failed")

    async def _ensure_media_mutation_allowed(self) -> None:
        """Block new gallery writes while a destructive cleanup is resumable."""
        registry = await self._file_registry()
        if registry["clear"] is not None or registry["key_deletions"] or self._gallery_cleanup_pending:
            raise GalleryMediaError("video_cleanup_incomplete")

    async def _video_registry(self) -> JsonObject:
        result = await self._store_read(_STORE_GALLERY_UPLOADS)
        if not isinstance(result, Ok):
            raise GalleryMediaError("video_read_failed")
        if result.value is None:
            return {}
        raw = result.value
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise GalleryMediaError("video_schema_unsupported")
        return self._validated_records(raw.get("uploads"), file_backed=False)

    async def _video_save_registry(self, registry: JsonObject) -> None:
        self._video_require_store()
        result = await self._store_write(
            _STORE_GALLERY_UPLOADS, {"version": 1, "uploads": registry}, "legacy video recovery",
        )
        if isinstance(result, Err):
            raise GalleryMediaError("video_save_failed")

    async def _validate_manifest(self, item: JsonObject, raw: Any, *, file_backed: bool) -> JsonObject:
        if not isinstance(raw, dict):
            raise GalleryMediaError("video_read_failed")
        if file_backed and (raw.get("version") != 1 or raw.get("storage") != "file"):
            raise GalleryMediaError("video_schema_unsupported")
        if not file_backed and raw.get("version", 1) != 1:
            raise GalleryMediaError("video_schema_unsupported")
        try:
            size, mime = video_size(raw.get("size")), video_mime(raw.get("mime"))
        except GalleryMediaError:
            raise GalleryMediaError("video_read_failed") from None
        count = video_chunk_count(size)
        hashes = raw.get("hashes")
        if (
            not valid_storage_id(raw.get("storage_id"))
            or size != item["size"] or mime != item["mime"]
            or type(raw.get("chunk_count")) is not int or raw["chunk_count"] != count
            or not isinstance(hashes, list) or len(hashes) != count
            or not all(valid_digest(digest) for digest in hashes)
            or not isinstance(raw.get("poster"), str)
            or not 0 < len(raw["poster"]) <= _GALLERY_VIDEO_POSTER_MAX_CHARS
        ):
            raise GalleryMediaError("video_read_failed")
        return {**raw, "storage": "file" if file_backed else "store"}

    async def _video_manifest(self, item: JsonObject) -> JsonObject:
        self._video_require_store()
        await self._file_registry()
        result = await self._store_read(video_file_manifest_key(item["id"]))
        if not isinstance(result, Ok):
            raise GalleryMediaError("video_read_failed")
        if result.value is not None:
            return await self._validate_manifest(item, result.value, file_backed=True)
        result = await self._store_read(video_manifest_key(item["id"]))
        if not isinstance(result, Ok):
            raise GalleryMediaError("video_read_failed")
        return await self._validate_manifest(item, result.value, file_backed=False)

    async def _delete_key_checked(self, key: str) -> bool:
        result = await self._store_delete(key, "wallpaper asset cleanup")
        if isinstance(result, Err):
            return False
        checked = await self._store_read(key)
        return isinstance(checked, Ok) and checked.value is None

    async def _video_delete_chunks(self, storage_id: str, record: JsonObject) -> bool:
        successful = True
        for number in range(video_chunk_count(record["size"])):
            successful = await self._delete_key_checked(video_chunk_key(storage_id, number)) and successful
        item_id = record.get("item_id")
        if item_id:
            result = await self._store_read(video_manifest_key(item_id))
            if not isinstance(result, Ok):
                successful = False
            elif result.value is not None:
                raw = result.value
                if not isinstance(raw, dict) or raw.get("version", 1) != 1:
                    raise GalleryMediaError("video_schema_unsupported")
                if raw.get("storage_id") == storage_id:
                    successful = await self._delete_key_checked(video_manifest_key(item_id)) and successful
        return successful

    async def _delete_file_record(self, storage_id: str, record: JsonObject) -> bool:
        # Validate a durable manifest before touching the binary file.
        key = video_file_manifest_key(record["item_id"]) if record.get("item_id") else ""
        if key:
            result = await self._store_read(key)
            if not isinstance(result, Ok):
                return False
            if result.value is not None:
                raw = result.value
                if not isinstance(raw, dict) or raw.get("version") != 1:
                    raise GalleryMediaError("video_schema_unsupported")
                if raw.get("storage_id") != storage_id:
                    return False
        root = await self._media_root()
        try:
            successful = await self._media_io(
                media_files.delete_file, root, storage_id, error="video_cleanup_incomplete",
            )
        except GalleryMediaError:
            return False
        if key:
            successful = await self._delete_key_checked(key) and successful
        return successful

    async def _file_asset_present(self, manifest: JsonObject) -> bool:
        """Check the published file before treating its manifest as recoverable."""
        if manifest.get("storage") != "file":
            return False
        root = await self._media_root()
        return await self._media_io(
            lambda: (
                media_files.file_path(root, manifest["storage_id"], ".bin").is_file()
                and media_files.file_path(root, manifest["storage_id"], ".bin").stat().st_size
                == manifest["size"]
            ),
            error="video_read_failed",
        )

    async def _video_cleanup(self, *, reclaim_live: bool = False) -> set[str]:
        """Recover journaled mutations.

        ``reclaim_live`` is retained for compatibility with older internal callers.
        Ownership alone is never a reason to delete an upload: a second plugin
        instance may have opened the panel while the original instance is still
        uploading.  Only an expired record or a record whose owning PID is gone
        is reclaimable.
        """
        if not self._store_ready:
            return set()
        registry = await self._file_registry()
        legacy = await self._video_registry()
        index = await self._gallery_index_strict()
        if registry["clear"] is not None:
            try:
                await self._resume_media_clear(registry, index)
                self._gallery_cleanup_pending = False
            except GalleryMediaError as exc:
                if exc.code != "video_cleanup_incomplete":
                    raise
                self._gallery_cleanup_pending = True
            return set()
        changed = False
        failed = False
        expired: set[str] = set()
        for storage_id, record in list(registry["records"].items()):
            item = gallery_find(index, record.get("item_id", ""))
            if item is not None and item.get("kind") == "video":
                manifest = await self._video_manifest(item)
                if manifest["storage"] == "file" and manifest["storage_id"] == storage_id:
                    if not await self._file_asset_present(manifest):
                        raise GalleryMediaError("video_read_failed")
                    if record["status"] != "published":
                        record["status"] = "published"
                        changed = True
                    continue
                if manifest["storage"] == "file":
                    raise GalleryMediaError("video_read_failed")
            stale = record["status"] in {"published", "deleting"} or record["expires_at"] <= time.time()
            pid = record.get("pid")
            if record["status"] == "upload" and type(pid) is int and pid > 0:
                stale = stale or not await self._media_io(media_files.process_alive, pid)
            if not stale:
                continue
            if record["expires_at"] <= time.time():
                expired.add(storage_id)
            if await self._delete_file_record(storage_id, record):
                del registry["records"][storage_id]
                changed = True
            else:
                failed = True
        for key in list(registry["key_deletions"]):
            # Failed unpublication must not delete a still-visible image.
            if key.startswith(_GALLERY_IMG_PREFIX) and gallery_find(index, key[len(_GALLERY_IMG_PREFIX):]):
                registry["key_deletions"].remove(key)
                changed = True
                continue
            if await self._delete_key_checked(key):
                registry["key_deletions"].remove(key)
                changed = True
            else:
                failed = True
        if changed:
            await self._save_file_registry(registry)
        legacy_changed = False
        for storage_id, record in list(legacy.items()):
            item = gallery_find(index, record.get("item_id", ""))
            if item is not None and item.get("kind") == "video":
                manifest = await self._video_manifest(item)
                if manifest["storage"] == "store" and manifest["storage_id"] == storage_id:
                    del legacy[storage_id]
                    legacy_changed = True
                    continue
            if record["owner"] == self._gallery_owner and record["expires_at"] > time.time():
                continue
            if record["expires_at"] <= time.time():
                expired.add(storage_id)
            if await self._video_delete_chunks(storage_id, record):
                del legacy[storage_id]
                legacy_changed = True
            else:
                failed = True
        if legacy_changed:
            await self._video_save_registry(legacy)
            registry["storage_reclaim_pending"] = True
            await self._save_file_registry(registry)
        self._gallery_cleanup_pending = failed
        return expired

    async def _media_storage_summary(self, index: JsonObject | None = None) -> JsonObject:
        registry = await self._file_registry()
        legacy = await self._video_registry()
        if index is None:
            index = await self._gallery_index_strict()
        video_bytes = sum(item["size"] for item in index["items"] if item.get("kind") == "video")
        pending = 0
        reclaim = 0
        uploads = 0
        published = {item["id"] for item in index["items"] if item.get("kind") == "video"}
        for record in registry["records"].values():
            if record.get("item_id") in published:
                continue
            if record["status"] == "upload":
                pending += record["size"]
                uploads += 1
            else:
                reclaim += record["size"]
        for record in legacy.values():
            if record.get("item_id") not in published:
                pending += record["size"]
                uploads += 1
        def disk_summary():
            root = media_files.safe_root(self)
            files = media_files.inventory(root)
            actual = sum(path.stat().st_size for path in files)
            playback_bytes = media_playback.usage(self)
            free, _total = media_files.disk_usage(root)
            return actual, playback_bytes, free

        try:
            # One worker owns this read-only scan; repeated thread handoffs add
            # noticeable latency to short-lived Hosted entry event loops.
            actual, playback_bytes, free = await self._media_io(disk_summary)
            occupied = max(video_bytes + pending + reclaim, actual) + playback_bytes
            available: int | None = max(0, free - _GALLERY_VIDEO_RESERVE_BYTES)
        except GalleryMediaError as exc:
            if exc.code != "video_path_unsafe":
                raise
            # Keep Store-backed image/gallery compatibility on SDK shims that
            # predate `data_path`; production SDKs always take the file path.
            occupied = video_bytes + pending + reclaim
            playback_bytes = 0
            available = None
        return {
            "schema_version": 1, "backend": "files",
            "single_limit_bytes": registry["policy"]["single_limit_mib"] * 1024 * 1024,
            "total_limit_bytes": registry["policy"]["total_limit_mib"] * 1024 * 1024,
            "hard_single_limit_bytes": _GALLERY_VIDEO_HARD_MAX_BYTES,
            "chunk_bytes": _GALLERY_VIDEO_CHUNK_BYTES,
            "video_bytes": video_bytes, "pending_bytes": pending, "reclaim_bytes": reclaim,
            "occupied_bytes": occupied, "available_bytes": available,
            "playback_cache_bytes": playback_bytes,
            "reserve_bytes": _GALLERY_VIDEO_RESERVE_BYTES, "uploads": uploads,
            "storage_reclaim_pending": bool(registry.get("storage_reclaim_pending")),
            "cleanup_pending": bool(self._gallery_cleanup_pending or registry["clear"] or registry["key_deletions"]),
            "playback_mode": "direct_with_blob_fallback" if self._playback_registered else "chunked_blob",
        }

    async def _video_policy(self, single_limit_mib: Any, total_limit_mib: Any):
        self._video_require_store()
        await self._video_cleanup()
        await self._ensure_media_mutation_allowed()
        policy = {"single_limit_mib": single_limit_mib, "total_limit_mib": total_limit_mib}
        if not self._valid_policy(policy):
            raise GalleryMediaError("video_policy_invalid")
        registry = await self._file_registry()
        if registry["clear"] is not None:
            raise GalleryMediaError("video_cleanup_incomplete")
        await self._clear_playback()
        registry["policy"] = policy
        await self._save_file_registry(registry)
        return Ok({"media_storage": await self._media_storage_summary()})

    async def _video_operation(
        self, *, op: Any, name: Any = "", mime: Any = "", size: Any = 0,
        thumb: Any = "", poster: Any = "", upload_id: Any = "",
        chunk_index: Any = -1, data_b64: Any = "",
        single_limit_mib: Any = None, total_limit_mib: Any = None,
    ):
        self._video_require_store()
        if not isinstance(op, str) or op not in {
            "video_begin", "video_chunk", "video_status", "video_commit", "video_abort", "video_policy",
        }:
            raise GalleryMediaError("video_operation_invalid")
        if op == "video_status":
            return await self._video_status(upload_id, chunk_index)
        if op == "video_policy":
            return await self._video_policy(single_limit_mib, total_limit_mib)
        expired = await self._video_cleanup()
        if valid_storage_id(upload_id) and upload_id in expired and op != "video_abort":
            raise GalleryMediaError("video_upload_expired")
        registry = await self._file_registry()
        if registry["clear"] is not None or self._gallery_cleanup_pending:
            raise GalleryMediaError("video_cleanup_incomplete")
        if op == "video_begin":
            return await self._video_begin(registry, name=name, mime=mime, size=size, thumb=thumb, poster=poster)
        if not valid_storage_id(upload_id):
            raise GalleryMediaError("video_upload_not_found")
        record = registry["records"].get(upload_id)
        if record is not None and record["status"] == "published":
            if op == "video_commit":
                index = await self._gallery_index_strict()
                item = gallery_find(index, record["item_id"])
                if item is None:
                    raise GalleryMediaError("video_read_failed")
                manifest = await self._video_manifest(item)
                if manifest["storage"] == "file":
                    if manifest["storage_id"] != upload_id or not await self._file_asset_present(manifest):
                        raise GalleryMediaError("video_read_failed")
                return Ok({"id": record["item_id"], "items": index["items"]})
            record = None
        if op == "video_abort":
            if record is None:
                return Ok({"aborted": False})
            record["status"], record["expires_at"] = "deleting", 0
            await self._save_file_registry(registry)
            if not await self._delete_file_record(upload_id, record):
                raise GalleryMediaError("video_cleanup_incomplete")
            del registry["records"][upload_id]
            await self._save_file_registry(registry)
            return Ok({"aborted": True})
        if record is None or record["status"] != "upload":
            raise GalleryMediaError("video_upload_not_found")
        if op == "video_chunk":
            return await self._video_upload_chunk(registry, upload_id, record, chunk_index, data_b64)
        return await self._video_commit(registry, upload_id, record)

    async def _video_status(self, storage_id: Any, number: Any):
        if not valid_storage_id(storage_id):
            raise GalleryMediaError("video_upload_not_found")
        registry = await self._file_registry()
        if registry["clear"] is not None or self._gallery_cleanup_pending:
            raise GalleryMediaError("video_cleanup_incomplete")
        record = registry["records"].get(storage_id)
        if record is None or record["status"] != "upload":
            raise GalleryMediaError("video_upload_not_found")
        if record["expires_at"] <= time.time():
            raise GalleryMediaError("video_upload_expired")
        if type(number) is not int or not 0 <= number < video_chunk_count(record["size"]):
            raise GalleryMediaError("video_chunk_invalid")
        accepted = record["next_chunk"] > number and valid_digest(record["hashes"][number])
        return Ok({"chunk_index": number, "accepted": accepted})

    async def _video_begin(self, registry: JsonObject, *, name: Any, mime: Any, size: Any, thumb: Any, poster: Any):
        normalized_mime = video_mime(mime)
        maximum = registry["policy"]["single_limit_mib"] * 1024 * 1024
        normalized_size = video_size(size, maximum)
        normalized_thumb, normalized_poster = video_poster(thumb, thumbnail=True), video_poster(poster)
        index = await self._gallery_index_strict()
        await self._clear_playback()
        summary = await self._media_storage_summary(index)
        if summary["uploads"] >= _GALLERY_VIDEO_UPLOAD_LIMIT:
            raise GalleryMediaError("video_upload_limit")
        if len(index["items"]) + summary["uploads"] >= _GALLERY_MAX_ITEMS:
            raise GalleryMediaError("gallery_full")
        if summary["occupied_bytes"] + normalized_size > summary["total_limit_bytes"]:
            raise GalleryMediaError("video_storage_full")
        if summary["available_bytes"] < normalized_size + summary["pending_bytes"]:
            raise GalleryMediaError("video_disk_full")
        storage_id = uuid.uuid4().hex
        registry["records"][storage_id] = {
            "status": "upload", "owner": self._gallery_owner, "pid": os.getpid(),
            "name": str(name or "")[:80], "mime": normalized_mime, "size": normalized_size,
            "thumb": normalized_thumb, "poster": normalized_poster,
            "added_at": _now_utc().isoformat(timespec="seconds"),
            "expires_at": time.time() + _GALLERY_VIDEO_UPLOAD_TTL_SEC,
            "next_chunk": 0, "hashes": [],
        }
        await self._save_file_registry(registry)
        return Ok({
            "upload_id": storage_id, "chunk_bytes": _GALLERY_VIDEO_CHUNK_BYTES,
            "chunk_receipts": True,
        })

    async def _video_upload_chunk(
        self, registry: JsonObject, storage_id: str, record: JsonObject, number: Any, encoded: Any,
    ):
        count = video_chunk_count(record["size"])
        if type(number) is not int or not 0 <= number < count:
            raise GalleryMediaError("video_chunk_invalid")
        if number > record["next_chunk"]:
            raise GalleryMediaError("video_chunk_out_of_order")
        decoded = decode_video_chunk(encoded, video_chunk_size(record["size"], number))
        digest = chunk_digest(decoded)
        if number == 0:
            validate_video_signature(decoded, record["mime"], record["size"])
        if number < record["next_chunk"]:
            if record["hashes"][number] != digest:
                raise GalleryMediaError("video_chunk_invalid")
            return Ok({"chunk_index": number, "accepted": True})
        root = await self._media_root()
        free, _total = await self._media_io(media_files.disk_usage, root)
        if free < len(decoded) + _GALLERY_VIDEO_RESERVE_BYTES:
            raise GalleryMediaError("video_disk_full")
        await self._media_io(
            media_files.write_chunk, root, storage_id, number * _GALLERY_VIDEO_CHUNK_BYTES,
            decoded, record["size"],
        )
        record["next_chunk"] += 1
        record["hashes"].append(digest)
        record["expires_at"] = time.time() + _GALLERY_VIDEO_UPLOAD_TTL_SEC
        # An unacknowledged tail remains registered and can be retried or reclaimed.
        await self._save_file_registry(registry)
        return Ok({"chunk_index": number, "accepted": True})

    async def _video_commit(self, registry: JsonObject, storage_id: str, record: JsonObject):
        count = video_chunk_count(record["size"])
        if record["next_chunk"] != count:
            raise GalleryMediaError("video_upload_incomplete")
        index = await self._gallery_index_strict()
        if len(index["items"]) >= _GALLERY_MAX_ITEMS:
            raise GalleryMediaError("gallery_full")
        root = await self._media_root()
        published_file = await self._media_io(lambda: media_files.file_path(root, storage_id, ".bin").exists())
        for number in range(count):
            decoded = await self._media_io(
                lambda number=number: media_files.read_chunk(
                    root, storage_id, number * _GALLERY_VIDEO_CHUNK_BYTES,
                    video_chunk_size(record["size"], number), record["size"],
                    published=published_file,
                ),
                error="video_read_failed",
            )
            if chunk_digest(decoded) != record["hashes"][number]:
                self.logger.warning("gallery video integrity failed: chunk_index={}", number)
                raise GalleryMediaError("video_chunk_invalid")
            if number == 0:
                validate_video_signature(decoded, record["mime"], record["size"])
        item_id = record.get("item_id")
        if not item_id:
            item_id = gallery_next_id(index)
            record["item_id"] = item_id
            if not await self._gallery_save_index(index):
                raise GalleryMediaError("video_save_failed")
            await self._save_file_registry(registry)
        if not published_file:
            await self._media_io(media_files.commit_file, root, storage_id, record["size"])
        manifest = {
            "version": 1, "storage": "file", "storage_id": storage_id, "mime": record["mime"],
            "size": record["size"], "chunk_count": count, "poster": record["poster"],
            "hashes": list(record["hashes"]),
        }
        result = await self._store_write(video_file_manifest_key(item_id), manifest, "private video manifest")
        if isinstance(result, Err):
            raise GalleryMediaError("video_save_failed")
        index["items"].append({
            "id": item_id, "kind": "video", "name": record["name"], "mime": record["mime"],
            "size": record["size"], "added_at": record["added_at"], "thumb": record["thumb"],
        })
        if not await self._gallery_save_index(index):
            raise GalleryMediaError("video_save_failed")
        record["status"] = "published"
        try:
            await self._save_file_registry(registry)
        except GalleryMediaError:
            # A published index + manifest is authoritative after restart.
            pass
        self.logger.info("gallery video added: id={} mime={} bytes={}", item_id, record["mime"], record["size"])
        return Ok({"id": item_id, "items": index["items"]})

    async def _video_get(
        self, item: JsonObject, chunk_index: Any, chunk_count: Any = None,
        prefer_direct: bool = False, *, storage_summary: JsonObject | None = None,
    ):
        manifest = await self._video_manifest(item)
        if type(chunk_index) is not int:
            raise GalleryMediaError("video_chunk_invalid")
        if chunk_count is not None and (
            type(chunk_count) is not int or not 1 <= chunk_count <= 4
        ):
            raise GalleryMediaError("video_chunk_invalid")
        if chunk_index == -1:
            if chunk_count is not None:
                raise GalleryMediaError("video_chunk_invalid")
            metadata = {
                "kind": "video", "mime": manifest["mime"], "size": manifest["size"],
                "chunk_count": manifest["chunk_count"], "chunk_bytes": _GALLERY_VIDEO_CHUNK_BYTES,
                "poster": manifest["poster"], "name": item["name"],
                "storage": manifest["storage"], "schema_version": 1,
            }
            if prefer_direct is True:
                path = await self._prepare_playback(manifest, storage_summary)
                if path:
                    metadata["playback_path"] = path
            return Ok(metadata)
        if not 0 <= chunk_index < manifest["chunk_count"]:
            raise GalleryMediaError("video_chunk_invalid")

        async def read_chunk(index: int) -> JsonObject:
            if manifest["storage"] == "file":
                decoded = await self._media_io(
                    media_files.read_chunk, await self._media_root(), manifest["storage_id"],
                    index * _GALLERY_VIDEO_CHUNK_BYTES,
                    video_chunk_size(manifest["size"], index), manifest["size"],
                    error="video_read_failed",
                )
                encoded = base64.b64encode(decoded).decode("ascii")
            else:
                result = await self._store_read(video_chunk_key(manifest["storage_id"], index))
                if not isinstance(result, Ok):
                    raise GalleryMediaError("video_read_failed")
                try:
                    decoded = decode_video_chunk(result.value, video_chunk_size(manifest["size"], index))
                except GalleryMediaError:
                    raise GalleryMediaError("video_read_failed") from None
                encoded = result.value
            if chunk_digest(decoded) != manifest["hashes"][index]:
                raise GalleryMediaError("video_read_failed")
            return {"chunk_index": index, "data_b64": encoded}

        if chunk_count is None:
            return Ok(await read_chunk(chunk_index))
        end = min(manifest["chunk_count"], chunk_index + chunk_count)
        chunks = [await read_chunk(index) for index in range(chunk_index, end)]
        return Ok({"chunks": chunks})

    async def _video_remove(self, index: JsonObject, item: JsonObject) -> None:
        manifest = await self._video_manifest(item)
        await self._clear_playback()
        storage_id = manifest["storage_id"]
        record = {
            "status": "deleting", "owner": self._gallery_owner, "pid": os.getpid(),
            "name": item["name"], "mime": manifest["mime"], "size": manifest["size"],
            "thumb": item["thumb"], "poster": manifest["poster"], "added_at": item["added_at"],
            "expires_at": 0, "item_id": item["id"], "next_chunk": manifest["chunk_count"],
            "hashes": list(manifest["hashes"]),
        }
        files = await self._file_registry()
        if manifest["storage"] == "file":
            registry = files["records"]
            registry[storage_id] = record
            await self._save_file_registry(files)
        else:
            registry = await self._video_registry()
            registry[storage_id] = record
            files["storage_reclaim_pending"] = True
            await self._save_file_registry(files)
            await self._video_save_registry(registry)
        gallery_remove_item(index, item["id"])
        if not await self._gallery_save_index(index):
            raise GalleryMediaError("gallery_update_failed")
        successful = (
            await self._delete_file_record(storage_id, record)
            if manifest["storage"] == "file" else await self._video_delete_chunks(storage_id, record)
        )
        if not successful:
            self._gallery_cleanup_pending = True
            raise GalleryMediaError("video_cleanup_incomplete")
        del registry[storage_id]
        if manifest["storage"] == "file":
            await self._save_file_registry(files)
        else:
            await self._video_save_registry(registry)

    async def _media_remove_image(self, index: JsonObject, item: JsonObject) -> None:
        registry = await self._file_registry()
        key = gallery_img_key(item["id"])
        if key not in registry["key_deletions"]:
            registry["key_deletions"].append(key)
        registry["storage_reclaim_pending"] = True
        await self._save_file_registry(registry)
        gallery_remove_item(index, item["id"])
        if not await self._gallery_save_index(index):
            raise GalleryMediaError("gallery_update_failed")
        if not await self._delete_key_checked(key):
            self._gallery_cleanup_pending = True
            raise GalleryMediaError("video_cleanup_incomplete")
        registry["key_deletions"].remove(key)
        await self._save_file_registry(registry)

    @staticmethod
    def _media_key(key: Any) -> bool:
        return isinstance(key, str) and (
            key in {_STORE_PANEL_BG, _STORE_GALLERY_UPLOADS}
            or any(key.startswith(prefix) for prefix in _MEDIA_PREFIXES)
        )

    async def _media_clear_keys(self, index: JsonObject, files: JsonObject, legacy: JsonObject) -> list[str]:
        keys = {_STORE_PANEL_BG, _STORE_GALLERY_UPLOADS, *files["key_deletions"]}
        for item in index["items"]:
            keys.add(gallery_img_key(item["id"]))
            keys.add(video_file_manifest_key(item["id"]))
            keys.add(video_manifest_key(item["id"]))
            if item.get("kind") == "video":
                manifest = await self._video_manifest(item)
                if manifest["storage"] == "store":
                    keys.update(video_chunk_key(manifest["storage_id"], n) for n in range(manifest["chunk_count"]))
        for sid, record in legacy.items():
            keys.update(video_chunk_key(sid, n) for n in range(video_chunk_count(record["size"])))
            if record.get("item_id"):
                keys.add(video_manifest_key(record["item_id"]))
        for record in files["records"].values():
            if record.get("item_id"):
                keys.add(video_file_manifest_key(record["item_id"]))
        enumerate_keys = getattr(self.store, "keys", None)
        if callable(enumerate_keys):
            for prefix in _MEDIA_PREFIXES:
                result = await enumerate_keys(prefix)
                if not isinstance(result, Ok) or not isinstance(result.value, list):
                    raise GalleryMediaError("video_read_failed")
                if not all(isinstance(key, str) and key.startswith(prefix) for key in result.value):
                    raise GalleryMediaError("video_read_failed")
                keys.update(result.value)
        for key in sorted(keys):
            result = await self._store_read(key)
            if not isinstance(result, Ok):
                raise GalleryMediaError("video_read_failed")
            value = result.value
            if value is not None and key.startswith(_GALLERY_VIDEO_FILE_MANIFEST_PREFIX):
                if not isinstance(value, dict) or value.get("version") != 1:
                    raise GalleryMediaError("video_schema_unsupported")
            if value is not None and key.startswith(_GALLERY_VIDEO_PREFIX):
                if not isinstance(value, dict) or value.get("version", 1) != 1:
                    raise GalleryMediaError("video_schema_unsupported")
        return sorted(keys)

    async def _media_clear(self, confirm: Any):
        self._video_require_store()
        if confirm is not True:
            raise GalleryMediaError("video_clear_confirmation_required")
        index = await self._gallery_index_strict()
        files, legacy = await self._file_registry(), await self._video_registry()
        root = await self._media_root()
        await self._media_io(media_files.inventory, root, error="video_path_unsafe")
        appearance_result = await self._store_read(_STORE_PANEL_APPEARANCE)
        if not isinstance(appearance_result, Ok):
            raise GalleryMediaError("gallery_update_failed")
        if files["clear"] is None:
            keys = await self._media_clear_keys(index, files, legacy)
            files["clear"] = {"keys": keys}
            files["storage_reclaim_pending"] = files.get("storage_reclaim_pending", False) or any(
                key.startswith((_GALLERY_IMG_PREFIX, _GALLERY_VIDEO_CHUNK_PREFIX)) for key in keys
            )
            await self._save_file_registry(files)
        await self._resume_media_clear(files, index)
        self._gallery_cleanup_pending = False
        appearance = clamp_appearance(appearance_result.value)
        appearance["bg_id"] = ""
        summary = await self._media_storage_summary()
        return Ok({
            "cleared": True, "items": [], "appearance": appearance, "media_storage": summary,
            "storage_reclaim_pending": summary["storage_reclaim_pending"],
        })

    async def _resume_media_clear(self, files: JsonObject, index: JsonObject) -> None:
        self._video_require_store()
        await self._clear_playback()
        if not await self._gallery_save_index({"items": [], "next": index["next"]}):
            raise GalleryMediaError("video_cleanup_incomplete")
        result = await self._store_read(_STORE_PANEL_APPEARANCE)
        if not isinstance(result, Ok):
            raise GalleryMediaError("video_cleanup_incomplete")
        appearance = clamp_appearance(result.value)
        appearance["bg_id"] = ""
        result = await self._store_write(_STORE_PANEL_APPEARANCE, appearance, "wallpaper clear appearance")
        if isinstance(result, Err):
            raise GalleryMediaError("video_cleanup_incomplete")
        failed = False
        remaining = []
        for key in files["clear"]["keys"]:
            if not await self._delete_key_checked(key):
                remaining.append(key)
                failed = True
        try:
            clean_files = await self._media_io(
                media_files.clear_files, await self._media_root(), error="video_cleanup_incomplete",
            )
            failed = failed or not clean_files
        except GalleryMediaError:
            failed = True
        files["clear"]["keys"] = remaining
        if not failed:
            files["records"], files["key_deletions"], files["clear"] = {}, [], None
        await self._save_file_registry(files)
        if failed:
            self._gallery_cleanup_pending = True
            raise GalleryMediaError("video_cleanup_incomplete")

    async def _media_shutdown(self) -> None:
        """Graceful restart discards only this session's unfinished uploads."""
        if not self._gallery_touched:
            return
        try:
            async with self._gallery_guard():
                await self._clear_playback()
                if not self._store_ready:
                    return
                files = await self._file_registry()
                changed = False
                for record in files["records"].values():
                    if record["owner"] == self._gallery_owner and record["status"] == "upload":
                        record["expires_at"] = 0
                        changed = True
                if changed:
                    await self._save_file_registry(files)
                    await self._video_cleanup()
        except Exception:
            self.logger.warning("wallpaper shutdown cleanup deferred")
