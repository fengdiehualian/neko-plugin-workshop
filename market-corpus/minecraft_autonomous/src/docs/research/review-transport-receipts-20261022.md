# Preserve structured review transport evidence

The latest `structured-review.json` is a UI snapshot. Previously a later timeout
overwrote an earlier output-budget failure, so provider-success accounting could
not establish whether a final review transaction had existed or been adopted.

Version 0.6.85 adds append-only `structured-review-attempts.jsonl` receipts. Each
actual gateway await has an opaque attempt ID, review ID, timestamps, wait/output
budgets, elapsed time, categorical outcome, whitelisted finish/tool labels and
available numeric usage. Content and private reasoning contribute only character
counts. The final delivery receipt links those attempts and records canonical
acknowledgement separately. Cancellation is retained before it propagates.

No model text, tool arguments, selected memories, persona, credentials or exception
messages enter the journal. Missing usage stays unknown; transport success remains
distinct from a valid final tool, review acknowledgement and native achievement.
Optional journal I/O failure cannot alter adoption or dispatch and is exposed in
the latest receipt. Original records are not replaced or replayed.

Regression coverage exercises the real heartbeat: a length-limited response
followed by a timeout, evidence reads plus one correction, caller cancellation,
untrusted provider labels and journal I/O failure. Existing output limits, original
opportunity deadline, automatic tool selection, bounded correction/evidence reads,
stop/ownership checks and the sole native body queue are unchanged.

Read-only inspection of the current local serving template found that omitted
reasoning effort injects `xhigh`; its CPU-counter copy matched the actual container
template hash. The public host gateway currently accepts no reasoning-effort or
template-kwargs extension, including through model-slot defaults. This is a
configuration difference from the standalone body, not proof that every prior
3072-token review was truncated. This change records actual finish reasons; it
does not change the host SDK, healthy model, reasoning configuration or inference
budgets, and makes no claim of repaired latency or autonomous project progress.

The dependency remains autonomy 0.2.80. This is an adapter-only development change;
the independent body, core and renderer need no replacement. Public release and
Market publication are outside this maintenance operation.

## Brief checkpoint guidance in 0.6.86

Autonomy 0.2.81 adds a host-neutral `reasoning_guidance` to the existing completion
contract. The adapter projects this public instruction and the existing short
output guidance before role/context material. It asks for brief deliberation and
an honest final decision rather than exhausting the output budget. All persona
and selected memory input remains available; decision alternatives and native
evidence/ownership checks are unchanged. This is advisory prompt guidance, not a
provider `reasoning_effort` setting or hard reasoning-token limit. No unsupported
gateway parameter is sent, no timeout is increased and no model is restarted.

The journal now records actual request entry before awaiting the gateway, and
marks whether each request carried this contract. A start without a terminal
receipt is incomplete evidence, not proof the request remains in flight. Final
transport receipts and delivery acknowledgement remain separate. Natural samples
must distinguish the earlier adapter from guided requests by their recorded flag
and deployment timestamps. A short successful response alone cannot prove lasting
project improvement, farming diversification or learned method reuse.

Only the adapter's review dependency changes to 0.2.81; the already running body
may remain 0.2.80. The fixed core/renderer and public 0.6.70 release are unchanged.

## Opt-in shared-model coordination in 0.6.87

Autonomy 0.2.82 owns a bounded review inference window and the matching body
admission check. `autonomy.review_model_pool` is empty by default. Only set the
same explicit identifier in body and adapter after verifying that their actual
bindings share an inference resource. The adapter begins the canonical window
immediately before its first real model await, keeps it over bounded evidence/
correction calls, and releases the same ID in `finally`. Coordination time is
charged to the original opportunity deadline. The journal links the actual
request to the window ID; no provider parameters or new model loop are added.

The body continues native polling, work and reflexes, while ordinary new slow
requests wait. Existing in-flight responses, user feedback, urgent native events,
death, stop, unknown barriers and original queue ownership retain their rules.
Expiry and scope/intent/session changes invalidate a hold. Optional coordination
errors are categorical and do not turn an otherwise valid review into a new game
action. A host crash leaves at most the original bounded window, not a permanent
pause. A late release cannot remove a newer window.

Both independent runtime and adapter must consume 0.2.82 for the admission check
to take effect. Installation alone is not evidence of shared-model pacing or
successful strategic review. Acceptance needs actual window/request timing,
continued native receipts and resumption after release; short fresh sessions
cannot establish a causal latency improvement or a finished long-term project.
