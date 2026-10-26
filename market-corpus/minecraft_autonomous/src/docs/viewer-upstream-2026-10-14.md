# Viewer upstream synchronization, 2026-10-14

The old renderer was `mc-visual-console@d878f21ca8b6d0ad2b5e43eb44ede32f2e390f06`. Main was fetched and inspected through `b382658d1d9e705864ac6d0e02d58936a9ef4716` (15 commits, 71 changed files). A final fetch at 19:43 Beijing confirmed the same head. Changes include YSM batching/poses, shoulder tracking, cutaways/culling, frame pacing, actual photo gallery handling and optional background music.

The old checkout's untracked server-content source/test and logs were preserved. Deployment uses a separate clean worktree at the checked main revision. Only the explicit `renderer_directory` changed; body source remains `2820afdb3d5ea0f5c995789ded24c7c27174a887`, framework 0.1.27 and adapter 0.5.12. No plugin version bump or Market publication is needed for this external input update.

## Active consumer and verification

Vanilla 1.21.1 uses the same-player native renderer and the existing exact `vanilla-1.21.1` catalog, not the optional 1.20.6 packaged viewer. It creates no observer account. All 8,453 original asset files passed byte/hash verification; registry SHA256 remains `55893d269459e1e05387903c77b2db877d7cece875ed31a5a6a2f965822ea546`.

Some upstream changes target the separate modern/socket renderer. The native route consumes shared page/CSS, appearance controls/previews and its own native scene/host. New modern sleep/tail/cutaway/gallery/music behavior is not claimed as fully ported to the native scene merely because the source was synchronized.

Renderer tests: 738 passed, zero failures, 12 conditional skips for additional original mod assets, metadata codec and 1.20.6 exports. The first run exposed a missing `minecraft-data` dependency for the shared-host serializer fixture; the successful run used the installed renderer dependencies through process-local `NODE_PATH`, without starting that legacy service. Offline native preparation created no game client or listening socket.

Adapter checks also passed: 53 Python tests, 4 Node tests, Ruff and the official release/Market structure check. The rebuilt 0.5.12 archive SHA256 is `31895033f9817fcdb96b9dac61580ffdc581d064d38f15bafadb7ba10c0a20e4`; its verified payload hash remains `a78042686bb81fc8d93bcc6a9ef4deea09be2215400cdb6a592cd8d91ae5279f`, unchanged from the earlier 0.5.12 build. The external renderer is an explicit deployment input, not private source copied into the plugin.

## Actual deployment

Bundles and host modules load when the body is attached. After preflight, the owned runtime normally stopped while idle, confirming stopped/disconnected with no pending/uncertain task. Original JSON/SQLite evidence and private configuration were preserved. It restarted at 19:38:06 Beijing with the same server/account/model/catalog. The original user food/safety goal and observer delivery were restored through public entries; no database edit or direct body test action was used.

Server/model/Neko main/plugin-service PIDs remained 40552/43604/39124/48016. Only the owned body/viewer changed. The old Cortico task stayed disabled and port 7788 absent.

Actual HTTP-served `index.js` and `client.js` bytes match preflight; `/build.json`, native status and the original player's UUID agree:

| Receipt | SHA256 |
| --- | --- |
| Renderer/host source | `4351a8fe686de9dd1a8d58520af2d5fb61dc450d2c4db7e3a6de6bd35bf79183` |
| Native world bundle | `9456bc2fcea721e9a71064e808ca6ede61e6d6e8d9d1ffe003d7335c8a31486b` |
| Native console bundle | `d9526a799d36b7bcf243bfca0a6ecfcc64e4eeb05ecda7572fe3c5351726ceaf` |

The real browser rendered the world, player label and actual inventory. Model settings still select `server` / “跟随服务器”; this vanilla pack currently exposes no additional YSM choices. Appearance was not changed. Agent HUD is still unavailable, some native item/health icons lack data and native audio controls remain disabled. These gaps are not called repaired by source synchronization or HTTP success. Gameplay resumed decisions; sustained survival is a separate unfinished acceptance.

Private proof: `local/live-runtime/viewer-upstream-20261014/`, excluded from Git and the plugin package. Rollback uses the preserved private renderer configuration and normal owned-runtime stop/start; server/world/model data need no rollback.
