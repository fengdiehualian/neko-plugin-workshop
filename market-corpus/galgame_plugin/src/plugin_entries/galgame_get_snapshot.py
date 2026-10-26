from __future__ import annotations

from ._common import (
    Ok,
    SimpleNamespace,
    build_snapshot_payload,
    plugin_entry,
    tr,
)


class _GalgameGetSnapshotMixin:
    @plugin_entry(
        id="galgame_get_snapshot",
        name=tr("entries.galgame_get_snapshot.name", default='获取 galgame 快照'),
        description=tr("entries.galgame_get_snapshot.description", default='返回当前游戏快照和 stale 状态。'),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["snapshot"],
    )
    async def galgame_get_snapshot(self, **_):
        state_snapshot = self._snapshot_state()
        payload = build_snapshot_payload(SimpleNamespace(**state_snapshot))
        return Ok(payload)
