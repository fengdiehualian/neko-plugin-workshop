# mcbot_bridge — Expert Review Brief

**Artifact under review:** `n.e.k.o_plugin_mcbot_bridge` (branch `main`, review baseline `7c23cf5`;
working tree additionally carries an uncommitted fix batch — see §5).
**Written:** 2026-10-13. **Purpose:** external design review of a first-party N.E.K.O. plugin.

---

## 1. Background & Goals

Two independent systems are being joined:

- **mcbot** — a Fabric mod (MC 1.21.11, Java) that runs an LLM-driven **fake-player companion**.
  Its brain runs client-side (owner's machine, owner's API key); the *body*, tools and world
  mutation run server-side on the tick thread. It exposes a **task-level loopback bridge**
  (`HTTP + SSE + MCP` on `127.0.0.1:17897`) documented as a frozen **v1.0 contract**
  (`mcbot/docs/BRIDGE.md`). The bridge only listens **while the client is in a world**, and its
  bearer token (`<gameDir>/mcbot/bridge.token`) only exists after the first world join.
- **N.E.K.O.** — a desktop AI-companion host ("neko-chan"): a *dialog* LLM with persona, emotion,
  memory and voice, plus a plugin SDK (decorators, lifecycle, `push_message` cues, hosted UI
  panels, an analyzer agent). Plugins extend what the character can do.

**The product model that drives every semantic decision.** The in-world fake player **is the
character's own body**, not a third party. The mod-side LLM is only an **executor** (it needs
routing because it carries many tools) and has **no emotion or memory**. The character is strong at
dialogue/emotion/memory, plays along with the user, and **relays**. When execution is blocked the
executor raises a question whose real recipient is **the owner**; the character passes it on **in
her own voice**, the owner answers, and she relays the answer back so the task resumes. Therefore
every model-facing cue is written in the **first person**.

**Goal of this plugin:** let the character (a) dispatch in-game actions to the companion,
(b) relay the companion's reverse questions to the owner, and (c) narrate progress/completion in
real time — without the user polling for it.

**Non-goals / scope boundaries**

- Not a generic MCP client (deliberately; reasons in §3.9).
- Not an action executor: it only speaks the task-level bridge, never atomic game tools.
- Loopback, single-machine, single-Minecraft-client. The bridge is the mod's security boundary.

**Sibling:** `game_agent_minecraft` (same-shaped plugin for a *different* agent over a raw
WebSocket on 48909). This plugin is the "same-shaped sibling" for a different agent + transport,
and the two share the cue/decoration conventions. Differences are called out in §5.

---

## 2. Overall Structure

### 2.1 Topology

```
N.E.K.O. host ── plugin: mcbot_bridge ──┐
                                        │  HTTP REST + SSE (loopback, bearer token)
                              mcbot bridge (client-side, only while in a world)
                                        │
                              mcbot agent loop + tools (client)  ⇄  body + ticks (server)
```

### 2.2 Files and roles

| File | Role | Imports plugin SDK? |
|---|---|---|
| `__init__.py` | **Facade**: SDK glue — decorators, lifecycle, handlers, lazy/eager start. Holds **no** game-protocol state. | yes |
| `service.py` | **State & semantics**: task slot, SSE routing, cue emission, token reading, session reconciliation. | **no** (takes injected `push_message` + logger) |
| `client.py` | **Transport only**: 5 REST methods + one single-connection SSE pump. | **no** |
| `prompts.py` | Every LLM-visible string, keyed by 8 locales (`zh`, `zh-TW`, `en`, `ja`, `ko`, `ru`, `es`, `pt`). | no |
| `config.schema.json` | Settings-form contract (validated by the host's own schema test). | — |
| `surfaces/mcbot.tsx` | Hosted UI panel (status + "stop task"). | — |
| `i18n/*.json` | Only `plugin.name` / `plugin.description` (matches sibling convention). | — |
| `conftest.py` | Injects a minimal SDK stub so tests run **without the host**. | — |

The SDK-free split of `client.py`/`service.py` is what makes the whole state machine unit-testable
offline (see §4).

### 2.3 Three channels, two mutually-exclusive audiences

| Channel | Members | Caller | Why |
|---|---|---|---|
| `@llm_tool` | `mcbot_task`, `mcbot_ask`, `mcbot_answer`, `mcbot_cancel` | **dialog LLM** directly | "go chop wood" must be decided by the model mid-conversation, with typed args. |
| `@plugin_entry` (+`@ui.action`) | `mcbot_status`, `mcbot_stop`, `mcbot_reload_config`; `@ui.context(id="mcbot")` | analyzer / panel / CLI | Facts and settings don't deserve a dialog-model tool slot; a dispatched "status" call is the canonical self-loop trap. |
| Reverse (`push_message`) | cue stream from SSE | the character reacts unprompted | No polling needed; progress arrives on its own. |

Mutual exclusion is **structural**, not a convention: `@plugin_entry` never reaches the tool
registry (only `LLM_TOOL_REGISTER`, sent for `@llm_tool`, does), and the analyzer's plugin view has
every `__llm_tool__`-prefixed entry stripped out.

| SSE event | ai_behavior | priority | coalesce_key |
|---|---|---|---|
| `progress` | `read` | 4 | `mcbot_progress` |
| `done` | `respond` | 7 | `mcbot_done:<session>/<task_id>` |
| `question` | `respond` | 8 | `mcbot_question:<session>/<qid>` |
| `state` | `read` | 4 | `mcbot_state` |
| `timeout` (local) | `respond` | 7 | `mcbot_timeout:<session>/<task_id>` |
| body-change (local) | `read` | 4 | `mcbot_body` |

`visibility=[]` means the user never sees the cue text itself; the model absorbs it and, for
`respond` cues, says it in her own words. "The character narrating herself" **is** the product.

---

## 3. Key Design Decisions

**3.1 Transport vs. policy.** `client.pump_events()` makes **one** connection attempt and returns
when it ends; retry cadence and token re-acquisition live in the service loop. Because
`bridge.token` appears only on first world join and the HTTP server dies on disconnect, "no token"
and "not listening" are **routine, recoverable** states that must be re-evaluated every cycle.

**3.2 v1.0 contract compliance.** `session_id` is a *bridge-instance* id and every new bridge
restarts SSE ids at 1. On any disconnect the service **queries status first**: unchanged session →
reuse `Last-Event-ID`; changed session → **reset the cursor**, mark the in-flight task **unknown**
(neither success nor failure — a bridge that left cannot report), void open questions, and **never**
re-dispatch. Not resetting the cursor is a *silent* failure (the new session's `id > cursor` set
is empty).

**3.3 `done.status` semantics.** Only `completed` is success. `failed` narrates a failure.
`cancelled`/`superseded` produce **no cue at all** — the model caused them and knows; cueing them
triggers a re-dispatch storm (the same trap the sibling plugin documents).

**3.4 Fast `done` before `POST` returns.** The bridge takes its event cursor *before* submitting, so
a `done` can beat the HTTP response. Those frames are **parked** on the `PendingTask` and replayed
once the `task_id` is known, so they are never attributed to the wrong task.

**3.5 Cursor discipline & dedup.** The SSE cursor advances **only after a frame is fully handled**;
if a handler throws, the cursor stops for that connection (`cursor_blocked`) and the frame is
retried on reconnect. Frames are de-duplicated by `(session, event, id)` with a bounded window,
and terminal `done`s by `(session, task_id)`.

**3.6 One task slot, honestly accounted.** A single in-flight slot with an atomic
check-and-claim; a watchdog releases it if no terminal event arrives within
`task_wait_timeout_seconds` (default **900s**, raised from 300 after a composite task tripped it
early on real hardware). A watchdog timeout emits a **`CUE_TIMEOUT`** stating the outcome is
**unknown / possibly still running** — it never presents the timeout as success, and does not
re-dispatch.

**3.7 `overwrite` is a real promise.** `overwrite=true` **first** issues a cancel for the current
task and refuses the overwrite unless the bridge confirms it (`ok:true`); an unknown-id task can't
be targeted. A 2s anti-thrash floor stops a model from interrupting work it just started.

**3.8 Per-event coalesce keys.** Terminal cues are keyed per `session/task` and questions per
`session/question`; only replaceable snapshots (`progress`/`state`) share a key. This stops one
task's completion from clobbering another's in the host's "keep newest per key" queue.

**3.9 Why not MCP.** mcbot's MCP surface is config-only to wire up, but: (1) it has **no
server→client push** (progress/question/done live only on the REST SSE stream), so "the character
reacts on her own" is impossible; (2) MCP entries route through the analyzer (~10s decision
latency); (3) `mcbot_task` blocks up to `wait_s`; (4) the token would have to be copied into host
config, violating the mod's "token only in `bridge.token`" hygiene. MCP is kept only as a smoke
test / diagnostic fallback.

**3.10 Non-spoken identifiers.** The `question_id` the model needs to answer a reverse question
travels in cue **`metadata`**, not in the spoken text; the question text itself is **never
truncated**, because an authorization question must reach the owner at full scope or the owner
approves *narrower* than the bridge grants.

**3.11 First-person cues + `{identity}` bracket.** Cue bodies are the character's own words;
`service._push_cue` injects `（<body name>）` as `{identity}` (empty string when unknown), and a
shared "don't verbalize internals / don't read the bracket out" gag is appended to every cue. A
companion-name change or session change emits **one** first-person "I changed bodies" cue.

**3.12 No nudge/polling loop.** The sibling needs ~130 lines of `_system_prompt_loop` because
mc-agent never volunteers; mcbot **pushes**. Dropping that whole layer is the main structural win
here (and the main reason the two plugins are *not* feature-aligned).

**3.13 Startup on the long-lived loop.** The SDK runs the `startup` lifecycle inside a transient
`asyncio.run(...)` that cancels any task created beneath it, so the bridge is started from
`_on_command_loop_start` (eager) **and** a guarded lazy first-call path — starting it in `startup`
would silently produce a plugin that never streams.

**3.14 Fire-and-forget dispatch.** `wait_s=0` makes dispatch return in milliseconds, so
`mcbot_task` claims + dispatches synchronously in the handler; the completion arrives by SSE.
There is **no** detached-task handler (unlike the sibling).

**3.15 Sandboxed panel.** The hosted surface runs in an iframe, so the only path to the plugin is
`props.api.call` → the `mcbot_status` action; `@ui.context(id="mcbot")` must exist or the host
refuses every action call.

**3.16 Privacy.** Task text, answers and questions are **conversation content**: they are never
logged (logs carry shape/metadata only — ids, lengths, status codes, exception types) and never
returned raw to the model. Transport exceptions are rewritten into speakable lines
(`ConnectError` → "I can't find the companion right now…"), because **tool results persist in the
transcript** and a host name or port must not be written into it.

---

## 4. Testing Status

**Offline unit tests: 64 passed** (no host required).

```bash
uv run --no-project --python 3.11 --with pytest --with pytest-asyncio --with httpx \
  python -m pytest tests -q
```

`conftest.py` injects a minimal SDK stub and loads the entry module as `mcbot_bridge_plugin`
(the directory name is not a legal identifier and the repo root is the plugin module, so the
stub is what lets pytest collect without the host).

Coverage by area:

- **Config** — shipped defaults, hostile-value clamping, wrongly-typed values ignored, base-URL
  normalisation, identifier-guard heuristic.
- **Task slot** — claim/busy, anti-thrash floor, supersede after the floor, refusal when the old
  task can't be cancelled, stop releasing a waiter, watchdog release.
- **Dispatch** — task-id capture, release on unreachable, no-client friendly error.
- **Inbound events** — progress→`read`+throttle, done resolves the matching task, **another
  task's done leaves our slot alone**, question recorded + full text + `question_id` metadata,
  state→`read`+throttle, unknown/malformed ignored, companion bracket + one-time body-change cue,
  gag present.
- **Sessions** — cursor reset on change, in-flight task invalidated as `unknown`, open questions
  discarded, cross-session frames don't write old state, done-before-POST grouping,
  unattributable done doesn't clear the slot.
- **Outbound** — ask verbatim / silent fallback, answer ok / expired, cancel (no-client,
  id-0-is-cancel-all not a bare path, unknown id, other-task id, bridge-refused), error-code
  parsing and unknown-code degradation, completed-vs-failed framing, `cancelled`/`superseded`
  silence, error envelope carries no transport detail.

**Real-machine:** first joint run **2026-10-10** (MC 1.21.11 + Fabric 0.19.5 + mcbot 0.1.0 +
packaged N.E.K.O. host 0.9.0.1). Verified: 4 tools registered and actually called; SSE frames
match v1.0; **session change → cursor reset** observed; watchdog releases the slot. Raw SSE captures
are kept under `dist/e2e/`. mcbot maintains its own contract regression (`BridgeContractTest`,
`BridgeHttpContractTest`) and a joint acceptance checklist (`BRIDGE.md` §6).

**Not yet covered:** the `surfaces/mcbot.tsx` panel is a **new file type** for this project and has
only been exercised minimally; the fix batch below has **no** real-machine re-acceptance yet.

---

## 5. Known Issues & TODOs

**5.1 Uncommitted fix batch (main thing to review).** The working tree contains a large
uncommitted change set (7 modified files + new `surfaces/`) that implements a connector fix round
driven by a maintainer's independent source review (`PLAN-connector-fixes-2026-10-12.md`). It
fixes: (1) reverse-question id not reaching the model; (2) truncation of authorization text;
(3) cancel wiping other tasks; (4) `overwrite` promising a stop it never sent; (5) timeout framed
as success; (6) duplicate/missed events (dedup + cursor); (7) terminal cues sharing a merge key;
plus the first-person cue rewrite (8 locales), companion-body tracking, tool-description
repositioning, the stop-task panel, and `state` throttling. **Status: appears complete in the tree,
tests green, but not committed and not re-accepted on real hardware.**

**5.2 Cue flooding (highest-priority open item).** `progress` is throttled to 6s (~10/min) and
`state` is now throttled too, but `coalesce_key` collapse was **not observed** in the passive queue
during the first real run. Passive cues batch and then dump into context on the user's next turn —
suspected of **drowning the user's own request** (symptom: "I gave an order but nothing ran").

**5.3 `failed` wording re-verification.** In the first run the character once reported a *failed*
task as done. That run predated the `CUE_DONE_FAILED` fix, so it must be re-verified on the fixed
build.

**5.4 Watchdog budget re-verification.** Raised 300→900s after a legal composite task tripped the
watchdog mid-flight; needs a real run to confirm it no longer fires early.

**5.5 Panel & i18n.** `surfaces/mcbot.tsx` is new; its panel labels are hard-coded zh/en inside the
TSX (only `plugin.name`/`description` are in `i18n/`), matching the sibling — undecided whether to
internationalise.

**5.6 Known v1 boundaries (not bugs).** Only one Minecraft client per machine can hold the bridge
port; `ask` is blocking (mod-side cap 135s, ours 150s) so a "state question" hitches the mod-side
LLM; the bridge lifecycle is tied to the world; there is **no screenshot/vision** path and no nudge
loop (unlike the sibling).

**5.7 Housekeeping.** `PLAN-connector-fixes-2026-10-11.md` is superseded by the `-10-12` version
(kept as background); both are untracked.

---

## 6. Points We'd Like the Expert to Focus On

1. **Semantic layer correctness.** Is "first-person cue + non-spoken `metadata` for the
   `question_id`" the right split — and is there a residual risk the model *answers on the owner's
   behalf* or reads an identifier aloud?
2. **Context budget / cue flooding (§5.2).** Is a 6s throttle plus per-key coalescing enough, or
   should `read`-class cues be rarer / batched / dropped under load? How would you bound the
   passive-cue batch that lands on the user's next turn?
3. **Task-slot state machine under races.** Review `try_claim_pending` / `_cancel_superseded` /
   `_watch_pending` / `_apply_done` for interleavings that could double-narrate, wedge the slot, or
   leave two tasks running. Is refusing an overwrite whose cancel isn't confirmed the right call?
4. **Cursor & dedup after a mid-stream handler failure.** Once `cursor_blocked` is set, the cursor
   stops for the whole connection (conservative over-replay on reconnect). Is the `(session,
   event, id)` seen-window sized/ordered correctly to avoid a replay storm without missing a frame?
5. **Session-change = "unknown".** Is refusing to resume/re-dispatch right, or should the plugin
   offer a user-initiated resume?
6. **Error & privacy surface.** Is rewriting every transport error into speakable text (never
   leaking host/port, raw body, or bridge operator prose) *sufficient* — any path where
   conversational content or a credential could still reach a log or the transcript?
7. **Hosted panel contract.** Is the `props.api.call` + `@ui.context` + `@ui.action`/`@plugin_entry`
   dual-decoration pattern used correctly for a sandboxed surface? (`surfaces/mcbot.tsx`)
8. **Config knobs.** The 900s wait budget is an empirically-tuned constant; do you see a more
   principled way to bound it (e.g. deriving from `cap_ticks` as the mod side does)?
9. **Scope judgement.** Is deliberately dropping the MCP surface correct, and are the four stated
   reasons sound?
10. **Contract robustness.** Anything in the v1.0 contract we are *not* honouring that a strict
    reader of `mcbot/docs/BRIDGE.md` would flag?
