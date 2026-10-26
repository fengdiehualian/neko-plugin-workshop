# Death terminal receipts: source and deployment

Native task #16 was accepted during combat at 18:24:15 Beijing, then the player died. The body respawned with an empty queue, but the controller still waited on the accepted ID at 18:41 because death clearing emitted no terminal task report. This was a core executor bug, separate from the old Cortico console at port 7788.

The canonical core now emits one truthful cancellation per accepted current/frozen/queued task, retaining its ID and step outcomes. Late old-body completions remain fenced, and cancelled work is not replayed or called completed. The adapter contains no replacement queue or death-text heuristic.

- Native fix: `minecraft-ai-friend@2820afdb3d5ea0f5c995789ded24c7c27174a887`.
- Framework 0.1.27 pin: `minecraft-ai-friend@4806f7e009002aa04776a8d5ca1905554b406c88`.
- Actual engine-only archive SHA256: `59638493deb8a111a14836a3702829f89254c83e44d80febf90e15e2eb5f46da`.
- Plugin 0.5.12, normal development reload and explicit runtime restart at 19:10:54 Beijing. Server/model/Neko main/plugin-service PIDs remained 40552/43604/39124/48016.
- Official package check passed; 3,366 files, private runtime/backups excluded, exact framework/body pins confirmed. Package SHA256: `66d12fb29782073881a2111ffb7c1e784d24229ed6ee433b064e2b58ea95b892`. No tag, Release or Market publication.

Before deployment, original JSON state and read-only SQLite backups were preserved. The public stop request completed a normal disconnect: stopped true, no pending/inflight task, uncertainty false. No ledger/database record was manually deleted and the old task was not replayed. Only the explicit backend path changed in private runtime configuration; world, account, model and external renderer stayed the same. Observer delivery resumed through its public entry after the new connected session was observed.

Verification: native regression 5 passed; complete engine suite 690 passed with one optional packaged-viewer test skipped; independent clean build and actual packed consumer passed, including strict NodeNext/Bundler declarations and denied network/host imports. Framework 363 tests and isolated wheel consumer passed; adapter 53 Python and 4 Node tests, Ruff and official release/Market structure checks passed.

The 19:13:01–19:21:01 Beijing observation window contains 17 samples, all connected/running; decisions increased 6→22. Oak planks increased 4→29; sticks, birch logs and a wooden shovel were acquired, and the browser showed the actual player/world and changing inventory. Health ended at 20/20 and hunger decreased 20→16. Five distinct completed native task IDs appeared in sampled last-task receipts; the final ledger had no pending/uncertain work. The server log contains no new death after the 19:11 join, so live death-branch recovery was not exercised.

The original food/safety user goal had been cancelled by the normal maintenance stop. At 19:18:25 it was restored once through public `panel_intent`, with a fresh UUID and `origin=user`; no direct body action or database edit was used. This intervention is explicitly part of the window, not an uninterrupted natural-goal test. The later samples repeatedly observed the same position and no food reserve, eating or shelter was confirmed. **Sustained survival remains not passed**; game connectivity, model calls and task receipts are not a substitute.

Window SHA256: `1c2e979fb90fafc396dee1581ee00b8da1356b01f883c0be7d47e0a21f59ae59`. Viewer Agent HUD and some item/health textures still lack data; world rendering does not prove all viewer fields. No live death was intentionally triggered. Private runtime evidence is under `local/live-runtime/death-terminal-20261014/` and is excluded from source and the plugin package.

For the separately approved shutdown of the old Cortico instance and its five-minute Windows task, see [Cortico runtime audit](cortico-runtime-audit-2026-10-14.md).
