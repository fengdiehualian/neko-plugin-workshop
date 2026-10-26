# 0.5.52 public recent context and explicit-output admission

This internal candidate pins canonical autonomy **0.1.76** at
`bb538c7bc1e1b4bc33e8b38aceaf27079a6ab0df`. Its unchanged reviewed recent-context
API originated in .74 (`3f7095cd`). The checked engine is
**0.5.7-dev.20261016.39**, source `3ee62ca25b60693132f7b7b8ae6bac3df7ad452f`,
archive SHA256 `44c161edb81aad330aef57933716b31d329eb11506d6ba8e0b6c322f57285b56`.
The packaged external viewer default remains c799ae1a. An operator's configured
333d viewer and the previously running .73/.32/.51 are separate inputs; this
candidate does not activate or replace them.

The existing ten-second observer reads the public conversations snapshot once,
at most once per five seconds for the same complete binding. The public RPC has
a 0.35-second timeout and its owned async wrapper a 0.5-second total deadline.
Cancellation drains the wrapper, but the unmodified SDK uses run_in_executor:
an already-running bounded synchronous RPC worker can finish later. No thread
termination is claimed. No extra timer, model, private host prompt/history,
subscription, body wait or gameplay queue is introduced.

Eligible producers are main_logic.core user_message/master and
assistant_message|proactive_reply/cat, plus proactive/proactive_reply (role may
be omitted). Exact configured actor, provenance and source occurrence times are
checked; instructions, other actors and malformed/conflicting records are
discarded. Arrival is the query cursor; producer metadata.ts controls TTL/order.
Missing producer time is explicitly arrival-only. Composite delivery identity
uses actor/source/turn/role/occurrence/hash; conversation IDs are neither unique
message identities nor proof of paired turns or independent model generations.
The SDK may already have coerced scalar types; lost raw-type evidence cannot be
recovered. Empty public SDK results may also mean an absent underlying method,
so they remain empty_snapshot/coverage_unknown, not confirmed empty history.

The authoritative public status lacks an actual run-start timestamp. A validated
first-observed floor is keyed by runtime UUID, native run, Settings identity,
scope, actor and plugin generation. Query cutoff is max(floor, now-600). Initial
attachment/reload deliberately loses earlier same-run history; coverage is
partial and marked session_observed_from. No PID/file mtime/as_of is called a
run start. Scope/run/revision/death/config/stop checks bracket optional reads.
An as_of-only request change invalidates the query and cache without resetting
the same-run observation floor.

Only selected canonical records persist in private host-recent-context.json:
newest four messages with latest user protected, plus three owned hash-only
speech receipts. The canonical port enforces the 8192-byte total, message1600 /
speech600 caps, original hashes/truncation and 30-second lease. The controller
reads the file on every actual model invocation, including later tool rounds,
and alone mints body-queryable mc_original handles. Source IDs are descriptive.
Main inspect gets at most two distinct assistant excerpts in a 600-byte JSON
historical hint; its hash references are private-snapshot-only, not body handles.
Status/logging never receives raw recent text. Historical user text does not
submit goals; accepted/unknown output does not prove hearing or playback.

The same observer independently refreshes a configured public character when
missing or within120 seconds of expiry, at most one new GET per60 seconds with
its own0.35/0.5-second budget. Cached profile reads never publish a static lease.
Failure keeps only still-valid prior context. A new static publication can
recover an expired actor while waiting for the controller's next real request;
it never copies or renews current intent or selected memories.

Explicit sanitized speech has a 300-second exact-hash admission guard. A bounded
immutable timer/manual transport marker is correlated with the real review,
opportunity, baseline, revision, generation, runtime UUID/native run and scope.
Mixed/manual/unknown origins cannot be downgraded by automatic retries. Actual
review urgency/death and fresh typed commentary urgency bypass suppression.
Newly observed user-authority IDs conservatively bypass for300 seconds because
the public lineage has no submission timestamp. Missing provenance bypasses.

Same-opportunity durable receipt lookup happens first. Only accepted/queued or
reserved_unknown/unknown possible submissions in the same actor, Settings,
runtime UUID/run/scope count. Rejected/disabled/unavailable/unbound legacy rows
do not. A confirmed new duplicate persists repeat_suppressed with prior refs,
audio_queued=false and playback_verified=false in the bounded32-row ledger.
It schedules no send or substitute line and does not delay native goal dispatch.
Late output completion preserves the existing unknown/no-replay semantics.
Speech text retention remains disabled; legacy and new receipts are hash-only.

Coverage limits: the public main tool does not expose the model call's actual
human-versus-automatic cause. A human directly reusing an automatically offered
review ID cannot be uniquely identified. The marker proves transport origin,
not that cause. Ordinary generated host final answers are also outside this
explicit-output guard. No independent generation, audibility or live improvement
claim is made. Gameplay, survival, scene parity and natural voice acceptance
remain separate from these offline SDK/file/receipt/package checks.
