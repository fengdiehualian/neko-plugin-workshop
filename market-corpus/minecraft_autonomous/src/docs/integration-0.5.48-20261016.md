# 0.5.48 reviewed integration inputs

This internal candidate uses canonical autonomy 0.1.69 at immutable
`2b0db6308c93a5b155e17abe0ed5d4708424fe20`. Install/sync that declared Git
dependency with the official plugin CLI in an isolated `minecraft_autonomous`
candidate; local source imports and the host's older installed autonomy package
are not dependency substitutes. Automatic startup and autonomy stay disabled.
Saving configuration does not connect, start, stop or replace the running body.

The engine-only input is `agent-mc-world-0.5.7-dev.20261016.13.tgz`, SHA256
`28af85759431be38625489a4481f0e9d6ca35fc00e5f1fb35f9e0d2ddf110d7e`,
from core source `2dfad5f7968bf50a00f9b27e4c077eba41dcabd5`. Use the existing
hash-checking backend installer with an explicit new destination and that
archive. Native entry, source/runtime receipts and public declarations are
checked; it is not an npm publication or viewer bundle. The repository's newer
.21 observed-effect source is merged, but deliberately awaits separate final
packaging with the remaining entity work. It is not in this .13 artifact.

Reviewed shared viewer source is
`567d95e6a9d7a1124204819e4991311b9741d25d`. Its separate source archive
`modern-viewer-567d95e6-source.tar.gz` SHA256 is
`060b30c467d69b332d1489ef813db56d35b70373cd74995892339a37561bdc2b`;
portable renderer-plus-host source digest is
`974e3cb83179a935703fcf42f766635f1a3c98be0cf8aa0bbd68796ef1d9f47c`.
It contains renderer source and Mineflayer host adapters, with original assets
supplied separately. Actual generated browser bundles have their own hashes and
build receipts; source integrity does not prove the files a consumer serves.
Configure matching renderer/catalog inputs explicitly, use `/3dview/`, rebuild
with the original Minecraft version/preset, and verify the served build. Old
`/dungeon/` bookmarks remain compatible; arbitrary saved user URLs are retained.

Neko's embedded viewer fallback is `bundled:yui-ysm`, registered from the unchanged
bundled original YUI/neko_yui catalog. Choose YUI slim or server-follow in plugin
configuration for the next explicit start. A browser's explicit stored choice
wins over defaults. The shared host receives a bounded opaque Settings.identity
scope; the identity computation, server version and account are unchanged.
Choices survive same-identity reload/respawn and are isolated between servers.
Old manifest-URL preferences lack server provenance; preserve them and ask the
user to reselect once instead of guessing a migration. These choices change
only the Web model. External runtime appearance and renderer settings belong in
the standalone configuration; its neutral default remains `server`.

Review completion diagnostics record only a plugin-generated `call_id`, a
validated review ID, any returned valid command `request_id`, inspect/final phase,
strict acknowledgement, error category and evidence-reference presence. `call_id`
is not a host RPC request ID. No review body, persona, reason, goal, model text,
auth/server config or raw exception is logged. Results/exceptions and ordinary
dispatch are preserved. Diagnostics help distinguish a returned rejection from
a missing next invocation; they do not cure omitted final ACK or auto-confirm it.

Public speech receipts retain bounded `speech_id`, `audio_queued` and
`audio_completed` beside the existing hash/correlation fields. HTTP acceptance
or queue/completion flags are not independent `voice_play_start/end` proof.
Original speech scheduling stays asynchronous, single-delivery and non-replaying
when transport is unknown; the official configured host output remains the only
voice path. No model/audio fallback or speculative prompt repair is added.

Root performs reviewed normal deployment/handover and actual acceptance. Offline
SDK, packaging, source and original-asset checks do not prove natural lively
speech, main-brain final-decision compliance, swimming, all creature animations,
all 130 entities, complete scene fidelity or sustained autonomous survival.
Remaining Task2c/final integration and live gates block a complete release.
No release tag, GitHub Release or Market submission is authorized here.
