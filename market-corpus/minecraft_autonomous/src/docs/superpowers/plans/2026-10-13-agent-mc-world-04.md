# Agent MC World 0.4 / Minecraft 1.21.1 Implementation Plan

> Execution: implement and verify in this session under the user's existing authorization.

**Goal:** Use the user's independent Minecraft execution plugin at `7889ce261a2309d94400b739c6198468d8271d83`, apply standalone cleanup, and expose an explicitly started N.E.K.O Minecraft 1.21.1 player.

**Architecture:** N.E.K.O owns the model and decision scheduler. A single worker owns the model client, MCP session and one Mineflayer body. `agent-mc/v1` carries turns, barriers, facts, events and receipts; current facts replace the request tail. The modern viewer is an explicitly configured external URL.

**Tech Stack:** Python 3.11+, public N.E.K.O SDK, MCP 2.1.1, Node 22, agent-mc-world 0.4.0.

**Spec:** User attachment `MY-AGENT-WORLD-AGENT-MC-WORLD.txt` and upstream `docs/protocol-v1.md` at the pinned commit. The native My Agent World adapter is present in this commit's server tree. Evaluate it as a separate provider; the user's follow-up permits using the independent core without that adapter.

## Global Constraints

- Minecraft protocol is exactly `1.21.1`; reject `auto` and mismatched versions.
- Default startup/autonomy remain disabled. No game connections, model requests or production restarts during verification.
- No Cortico imports/build inputs. No implicit server policies, model endpoints, extra accounts or performance discovery.
- Do not ship the core's 1.20.6 viewer as a 1.21.1 renderer. Prepare a clearly labelled headless distribution from the exact core source; preserve its public types and record hashes.
- Preserve existing per-account execution directories and uncertain receipts across upgrade/restart.
- The native adapter's `mc_do(operation,args,action_id)` must not be confused with generic `mc_do(steps)`.
- No tag, release or Market publication. Actual modded-server gameplay remains unverified until the separately distributed native adapter and explicit connection details are supplied.

## Review Focus

- A stop during a model/tool wait must prevent late output from issuing another action.
- An accepted/running receipt must not wake a new plan every polling interval or become goal success.
- An uncertain dispatch must block restart, not silently create a new ledger.
- Facts, schema and output reserve must all count toward context limits; originals remain queryable.
- Upgrade must not claim renderer or NeoForge/mod capabilities from vanilla protocol support.

### Task 1: Pinned headless core and protocol bridge

Files: `backend/source.json`, `backend/build-headless.mjs`, `backend/package*.json`, `backend/install.py`, `backend/check_disconnected.py`, `configuration.py`, `bridge.py`, `tests/test_configuration.py`, `tests/test_bridge.py`.

Interfaces: `Settings.client_config(data) -> dict` fixes 1.21.1, disables owned viewers/launchers; `MinecraftBridge.call(name,args,request_id,turn_id)` forwards `_meta`; `outcome(result)` retains structured receipts.

- [x] Pin the source revision and compile its own core/client/contracts/MCP source to the original export paths; generate public declarations with the upstream declaration builder. Label the artifact `neko-headless`, without substituting missing viewer assets.
- [x] Pack/install with scripts disabled and a checked archive hash. Installation never connects.
- [x] Add tests rejecting `auto`, foreign versions, enabled bundled viewer and preserving account data paths.
- [x] Test metadata, concurrent cancellation, failure and terminal receipt preservation; run real disconnected stdio with network and Cortico imports denied.

### Task 2: Explicit N.E.K.O autonomy and lifecycle

Files: `controller.py`, `context.py`, `worker.py`, `__init__.py`, `plugin.toml`, `config.example.toml`, `tests/test_controller.py`, `tests/test_plugin.py`.

Interfaces: controller `run(goal)` owns explicit turns and filtered event wakeups; thread worker `start/stop/snapshot` survives per-entry host event loops; model calls use `ctx.models.get_client()` and manifest usage `decision`.

- [x] Implement one model owner, bounded tool rounds and conservative context accounting. Stable rules/tool schemas precede history; latest facts and event references are last.
- [x] Consume control barriers and end-turn requests explicitly. Track accepted tasks until terminal events; exclude archive-only events from wakeups.
- [x] Persist stop/uncertain state; stop cancels the model wait and disconnects only the owned child. Never retry uncertain actions.
- [x] Test the complete scheduler using fake model/backend receipts: stop race, idle polling, running actions, barriers, context overflow and restart uncertainty.
- [x] Wire public SDK entries and explicit model binding; startup remains passive.

### Task 3: Verification and operator instructions

Files: `README.md`, `CONTRIBUTING.md`, `docs/design.md`, `AGENTS.md`, `docs/research/agent-mc-world-04-integration.md`.

- [x] Record exact source/build/hash and real offline evidence separately from gameplay claims.
- [x] Document external viewer ownership and native adapter dependency/behavior boundaries, with a concise forwarding request.
- [x] Run plugin pytest, managed Ruff, official release/Market structure validation and package inspection.

Commit/push handoff: inspect staged changes, then commit and push the existing feature branch without publishing. The implementation commit records this final step.

### Additional scope: complete legacy convention cleanup

- [x] Audit names, paths, globals, procedure schemas, chat identity, migration exports, optional modules and package inputs.
- [x] Apply a tracked, hash-pinned source patch at build time; identify the author as jcs130.
- [x] Rebuild, verify actual runtime/types/archive contents, run affected regression tests and repin the archive.
- [x] Repeat plugin/official package verification after the final changes.

Public SDK model methods were checked against official main ce008bb. Live game and viewer acceptance remain pending.

Final offline evidence: 43 plugin tests, 58 core regression tests, Ruff, core distribution audit and official release/Market structure check passed. Final package SHA256: f2b5b763fec9ea59cbef766e53620bd1755413ca7e381501937457c6f94fe696. Package source bytes and staged patch hash were verified; no live connection or publication.
