"""Hosted settings receipts. Saving never occupies the UI request channel."""

from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict

from .game.state import utc_now


class SettingsSaveOperations:
    """One settings write at a time; receipts contain no submitted values."""

    def __init__(self):
        self.receipts = OrderedDict()
        self.task = None

    def start(self, ident, kind, execute):
        if ident in self.receipts:
            if self.receipts[ident]["kind"] != kind:
                raise ValueError("operation_conflict")
            return self.read(ident)
        if self.task is not None and not self.task.done():
            raise ValueError("settings_busy")
        while len(self.receipts) >= 20:
            self.receipts.popitem(last=False)
        self.receipts[ident] = {
            "operation_id": ident, "kind": kind, "state": "saving",
            "persisted": None, "applied": None, "started_at": utc_now(),
        }
        self.task = asyncio.create_task(self._run(ident, execute), name="rimworld-settings-save")
        return self.read(ident)

    async def _run(self, ident, execute):
        receipt = self.receipts[ident]
        try:
            result = await execute()
            if result.is_ok():
                receipt.update(state="saved", persisted=True, applied=True)
            else:
                code = result.error.code
                uncertain = code in {"configuration_update_failed", "configuration_status_unknown"}
                receipt.update(state="unconfirmed" if uncertain else "failed",
                               error_code=code, persisted=None if uncertain else False,
                               applied=None if uncertain else False)
        except asyncio.CancelledError:
            receipt.update(state="unconfirmed", error_code="save_interrupted")
            raise
        except Exception:
            # Never retain exceptions: a transport may echo submitted credentials.
            receipt.update(state="unconfirmed", error_code="save_unconfirmed")
        finally:
            receipt["finished_at"] = utc_now()

    def read(self, ident):
        if ident not in self.receipts:
            raise ValueError("settings_receipt_unavailable")
        return copy.deepcopy(self.receipts[ident])

    def snapshot(self):
        return copy.deepcopy(list(self.receipts.values()))

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def persist_settings(config, patch):
    """Accept a write acknowledgement or an authoritative matching readback.

    dump() may be an optimistic process cache after a timeout. The SDK's explicit
    effective-config read bypasses that cache and respects the active profile.
    Neither submitted values nor their digests leave this call.
    """
    try:
        await config.update(patch, timeout=5.0)
    except Exception:
        ctx = getattr(config, "ctx", None)
        readback = getattr(ctx, "get_own_effective_config", None)
        if readback is None:
            raise
        try:
            from plugin.sdk.shared.core.config import unwrap_config_payload

            response = await asyncio.wait_for(readback(timeout=3.0), 3.5)
            if not isinstance(response, dict) or response.get("success") is False:
                raise ValueError("configuration_read_not_confirmed")
            actual = unwrap_config_payload(response)
            if not _contains(actual, patch):
                raise ValueError("configuration_not_confirmed")
        except Exception:
            raise RuntimeError("Configuration persistence is unconfirmed") from None


def _contains(actual, expected):
    return isinstance(actual, dict) and all(
        key in actual and (_contains(actual[key], value) if isinstance(value, dict)
                           else type(actual[key]) is type(value) and actual[key] == value)
        for key, value in expected.items()
    )
