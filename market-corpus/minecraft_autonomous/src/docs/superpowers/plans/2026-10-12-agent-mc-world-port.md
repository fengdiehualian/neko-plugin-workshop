# Agent MC World port implementation plan

> **For agentic workers:** Execute the tasks sequentially in this session using the existing user authorization. Preserve stopped services throughout implementation.

**Goal:** Adapt the independent Minecraft client to the public N.E.K.O SDK, with autonomous decisions, remote servers and the existing modern viewer.

**Architecture (revised after the fork and decoupling audit on 2026-10-13):** Reuse the useful Minecraft execution behavior and modern viewer through a core that builds without Cortico. A single plugin game controller owns actions; N.E.K.O Main handles user goals, takeover, stop and character communication. The plugin model gateway supplies inference but does not inherit Main's persona or memory. Define versioned neutral event, turn and cancellation contracts, then map both host adapters to them. Do not import the Cortico Core/Persona runtime. Current MCP transport and build boundaries are incomplete. See [integration research](../../research/neko-integration-architecture.md) and [fork audit](../../research/fork-improvements-and-decoupling-boundary.md).

**Tech stack:** Python 3.11+, public N.E.K.O SDK, official Python MCP client, Node.js 22+, Agent MC World 0.3.0-next.2, hosted TSX.

**Spec:** User requests in this conversation; upstream `docs/architecture.md`, `docs/agent-mcp.md` and `docs/minecraft-client.md` at `084234b24f816562d6493da9d57fe9f11b0a7874`.

## Constraints and review focus

- No old service starts, model requests, game connections or messages during verification.
- No Cortico installation/runtime, server-specific policy, credentials or deployment data in the plugin.
- Connect and disconnect receipts are distinct from actual game connection. Queue acceptance is distinct from execution or goal completion.
- Constructor, discovery and startup must be inert. Disabled configuration and persisted stop cannot resume work.
- Stop racing a model response must prevent subsequent actions. Login/transport uncertainty must not cause blind retries or a restart loop. A model failure stops new decisions; accepted actions and deterministic self-protection have their own lifecycle and must not be confused with model generation.
- Tool/event floods must fit a bounded request, retain raw evidence and provide a query path. Changing facts must not change the stable prefix.
- Multiple instances, server/account changes, cursor gaps and timed-out actions must not silently duplicate work.
- Modern viewer assets are Java 1.20.6; never claim other versions render correctly based on a successful connection.

## Task 1: Independent execution transport

Files: `backend/source.json`, `backend/package.json`, `backend/package-lock.json`, `backend/LICENSE-Agent-MC-World.txt`, `backend/install.py`, `bridge.py`, `configuration.py`, `tests/test_bridge.py`, `tests/test_configuration.py`.

- [x] Pin upstream release URL and SHA256; preserve license and engine/viewer provenance. Commit npm production dependency lock.
- [x] Implement an explicit installer accepting a local verified archive: verify bytes before npm, use `npm ci --ignore-scripts --omit=dev`, verify installed identity, never connect. Dependency installation still requires a package cache or registry access.
- [x] Implement immutable validated config, independent remote server/viewer addresses and private per-server/account storage.
- [x] Implement one task owning `stdio_client` and `ClientSession`. Forward bounded tool/resource requests through a queue; fail pending requests on EOF; close only the owned child.
- [x] Verify malformed configuration, missing runtime, MCP error receipts, startup/stop and cancellation without real I/O. Run real disconnected MCP discovery separately under a network-denial loader.

## Task 1b: Complete source, build and optional-adapter separation

Task 1 proved only disconnected transport. The pinned release remains a comparison
artifact until the [decoupling audit](../../research/agent-mc-world-decoupling-audit.md)
is resolved in a separately verified backend revision.

- [ ] Move the maintained Minecraft source into the independent core, preserving the user's fork improvements and licenses. A normal core build must not fetch a Cortico checkout, import its publisher, or reapply patches to its internal source paths.
- [ ] Give the default package entry and public types a host-neutral contract. Put Cortico-specific registration, types and console wiring behind an optional adapter dependency directed toward the core.
- [ ] Replace legacy speech port discovery with an explicitly configured optional adapter. Move server-specific commands/capabilities out of the generic core; changing a default to false is not source separation.
- [ ] Make host cognition/model access an explicit capability, disabled when absent. Do not transfer provider endpoints, Persona, private Memory paths or deployment lifecycle into the execution core.
- [ ] Record the fork-to-core/controller capability mapping, exact source and patch differences from the evaluated release, and data/procedure compatibility. No blind namespace replacement or license removal.
- [ ] Verify clean core build and public-type consumption without Cortico, same-bot viewer without legacy speech probes, and no unconfigured model calls using offline fixtures. Pin the resulting release only after these checks pass.

## Task 2a: Preserve behavior through a neutral host contract

Files: independent client SDK/MCP adapter and contract tests; plugin-side event/turn adapter. Keep upstream improvements generic and pin their verified source/package separately.

- [ ] Preserve event trigger modes and delivery/archival distinction; consume deferred samples at the intended boundary.
- [ ] Carry and enforce `barrierAfter`, `interruptible`, tool/result `endsTurn`, and a genuine model-turn end notification. An idle RPC queue is not the end of a model turn.
- [ ] Supply honest model modality/window information; mark absent optional cognition capabilities explicitly.
- [ ] Correlate acceptance, task terminal receipts and world effects; support run/cursor gaps and bounded raw archive lookup.
- [ ] Keep cancellation available while events or tools are waiting. Confirm persisted stop prevents engine restart or late action submission.
- [ ] Replay the same offline event/action scenarios across SDK and MCP. Use the user's fork as the behavior baseline, not as a runtime dependency. Do not advertise gameplay or host-contract parity based on tools/list.

## Task 2b: N.E.K.O autonomous driver

Files: `__init__.py`, `autonomy.py`, `context.py`, `evidence.py`, `policy.txt`, `plugin.toml`, `config.example.toml`, `pyproject.toml`, `tests/test_autonomy.py`, `tests/test_context.py`, `tests/test_plugin.py`.

- [ ] Replace unavailable scaffold entries with explicit connect/start/stop/status and tool forwarding.
- [ ] Declare the required `decision` model usage (`text`, `tool_calling`), use only `ctx.models.get_client()` and the official Chat Completions tool protocol.
- [ ] Give Main role-scoped goal/status/stop controls through public `llm_tool` APIs; implement an explicit character-task context and result handoff. Do not claim automatic Main memory inheritance.
- [ ] Consume cursor/run events and current status; wake on accepted goals or meaningful events. A bounded idle check reads state first and only requests inference when evidence warrants it. Never replace a useful running task just to raise model activity.
- [ ] Keep fixed system policy and schemas before history/current facts, reserve output tokens and bound serialized input. Save full results/events in private evidence with a bounded read entry.
- [ ] Use a short, stable default tool catalogue with progressive help; account for tool schemas in the input budget. Character count alone is not a token-window guarantee.
- [ ] Keep the transport and loop in one owned async lifetime compatible with the host SDK. Startup/timer/shutdown handlers must not share a session across unrelated event loops.
- [ ] Persist explicit stop, serialize lifecycle transitions, prevent stale responses after stop, and fail closed on uncertain action delivery instead of retrying.
- [ ] Test event wakeups, acceptance vs completion, run gaps, input bounds, deterministic prefix and stop races using fake execution/model ports with the real host SDK.

## Task 3: Viewer, documentation and verification

Files: `ui/panel.tsx`, `README.md`, `docs/design.md`, `docs/upstream-port.md`, `CONTRIBUTING.md`, `AGENTS.md`, `NOTICE`, `tests/test_smoke.py`, `.github/workflows/backend.yml`.

- [ ] Add hosted state and explicit start/stop/refresh actions; use actual backend links or explicitly configured viewer URL. Include `/dungeon/`, `/third/` and first person without a second login.
- [ ] Document remote/offline/Microsoft configuration, human device-code console, model-slot binding, independent installation, version limitations and first live acceptance steps.
- [ ] Preserve official managed workflow templates; add a separate disconnected backend verification job and exclude dependencies/private files from plugin package.
- [ ] Run full pytest, Ruff, official release/Market check, package inspection, disconnected real MCP discovery and exact-commit GitHub CI.
- [ ] Commit logical increments and push verified source; no release tag, Market submission or live deployment. Record source/package/offline checks separately from live game acceptance.

## Evidence checkpoint — 2026-10-13

Task 1 code exists and a disconnected discovery run found 23 tools while all networking and Cortico imports were denied. The complete compact tool JSON measured 47,794 characters, not tokens. Plugin entry points still report `backend_not_integrated`; Tasks 2a/2b and live acceptance are unfinished. Keep original Cortico gameplay as the later comparison baseline; the source inspection is not a performance benchmark.

Fresh local checks: 20 pytest cases passed; managed Ruff passed; official `check --release --market-release` passed. A second installation from the checked-in canonical npm lock installed 255 packages and passed the disconnected check, with no Cortico package installed. Package contents included the bridge, installer, provenance/license and Python MCP dependency, and excluded private state, node_modules and development docs/tests. The local check cannot compare a release tag because none is present; it does not publish or approve a Market submission.

Checked package SHA256: `10f37780aa33f4b931e89a77ea54d76f6501a72ae3414ae6225664d571cda8de`.
Verified payload hash: `52738fd29810a1ea7f5093f8287194255a490bd2e958182e396c037f96984d79`.
Exact-commit GitHub CI is still a separate pending check.
