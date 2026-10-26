# Independent autonomy with optional planner adapters

Goal: run the same Minecraft Java 1.21.1 autonomy loop with or without N.E.K.O.
No game, service or model is started during this work. Explicit user stop remains in force.

## Upstream review

- [x] Compare pinned `7889ce261a2309d94400b739c6198468d8271d83` against freshly fetched main and the experiment branch; record source links and distinguish branch differences from regressions.
- [x] Adopt updates only if relevant and compatible; do not replace the verified execution core merely because a branch is newer.

## Shared runtime

User refinement: the native Minecraft execution queue is authoritative. N.E.K.O reads
progress to narrate/perform and submits new work; it must not own or mirror a second
execution queue. Add a local delivery inbox with original receipts, native append /
afterCheckpoint / now modes, cached observations, and an external frontend mode whose
shutdown does not stop the independently launched framework. Inference is optional for
executing already planned native tasks; generating fresh plans requires a chosen Planner.

- [x] Move bridge, controller, context and configuration into `autonomy/`, with no N.E.K.O imports. Preserve the existing `sessions/<identity>/host` ledger and all execution/unknown-outcome semantics.
- [x] Add a narrow async `Planner` contract and explicit OpenAI-compatible HTTP adapter; endpoint/model are required, keys come from a named environment variable, no discovery or automatic fallback.
- [x] Add a shared runner that validates configuration, holds an OS-backed session lock, owns connection/controller/model cleanup and writes status. Both frontends use this runner; do not duplicate the scheduler.
- [x] Reduce the N.E.K.O entry to configuration, model gateway adapter and start/status/stop frontend. Preserve entry-loop worker ownership and passive startup.
- [x] Add `standalone.py check|status|run` and a disabled example configuration. Check/status never connect. Foreground run is explicit and Ctrl+C stops its own session; no service installation or automatic recovery.

## Evidence and delivery

- [x] Run SDK-free subprocess imports and CLI checks with `plugin` imports forbidden; prove an autonomous fake observation -> planner -> action -> receipt cycle and cancellation without N.E.K.O.
- [x] Test shared ownership, unchanged ledger identity, unknown-outcome refusal, HTTP adapter validation/cancellation and no hidden fallback using temporary files and fake transports only.
- [x] Run the existing controller/plugin suite, Ruff and official Market/release structure check. Inspect packaged modules and ensure private configurations and data are excluded.
- [x] Document the architecture, standalone commands, model requirement, measured limits and unverified live gameplay. Prepare the coherent change on the current feature branch; no release, tag or Market publication.


## User refinement and verification — 2026-10-13

Neko remains the high-level main brain for strategy, feedback and narration/voice. A `Planner`
refines goals inside the independent runtime; it does not replace Neko's main conversation.
The same queue continues without new user input. Feedback supports insert/resume and explicit
replacement, with stale inference cancellation and original native receipts. Queue-only mode
cannot generate new natural-language plans. End-to-end Neko presentation is still unverified.

- `pytest tests -q`: 64 passed (including SDK-free subprocess, two consecutive native actions
  without user input, nested feedback restoration, and stopping a full command inbox).
- Ruff 0.12.4 with the managed repository configuration: passed.
- Official `check --release --market-release`: passed; version 0.3.0, payload hash verified,
  bundled tests passed. Warnings: this installed CLI does not recognize `plugin.models`, and
  no release tag was provided for tag/version alignment. No tag or publication was attempted.
- Package inspection: 35 own files matched their working sources; independent entry points
  included, old root modules absent, test/docs/private/runtime data excluded.
- Checked package SHA256: `59f588be7174c8347f2a3af76762bbb3729685999c60868c94d61b1c75a5ce98`.
- Disconnected MCP: 24 tools, Java 1.21.1, concurrent status/cancel, network denied,
  `game_connected=false`, no Cortico installed; stable context byte budget 18419.
- Derived core audit: 207 source files, 196 runtime files, 241 declarations; zero legacy runtime
  references, permission boundary passed, old procedure schema rejected, migration exports absent.
- Core source/patch pins are unchanged. No real model request, game connection, old service
  recovery, voice generation or public chat was used for verification.
