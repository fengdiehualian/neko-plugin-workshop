# Planner prefix continuity

Development adapter 0.6.79 pins host-neutral autonomy 0.2.75 at
`c8269ca1f7058e266981fd4956a191a35e8e71f4`. The game core and viewer pins are
unchanged. This is a dependency update; planning behavior remains in MC friend.

Full-mode temporary read deferral and ahead planning previously rewrote the
provider's leading function declarations. A replay through the real controller
and selected local Chat template/tokenizer reproduced almost total prefix
invalidation on the same retained history. The framework now keeps declarations
stable and sends current tool/append-only permissions in the final scene. The
same restricted validators still reject deferred reads, insertion/replacement
and forbidden mutations before native dispatch. Compact mode is unchanged.

The framework adds bounded request-context counts and hashes to successful,
cancelled and rejected decision journals. This helps distinguish cache loss,
history retirement and shared service pressure; it logs no prompt or private
memory contents in those diagnostics. Original receipts remain available.

The deadline stays bounded at the existing setting. CPU prefix replay and
offline tests establish this defect and repair; they do not establish a live
speedup, explain every timeout or prove long-term survival. This development
update does not publish a new GitHub or Market release, modify the host SDK or
change the selected model route.

The intermediate 0.6.77/0.2.73 live trial confirmed stable declarations across
ordinary/ahead planning and real cache reuse. It also exposed a separate
106,549-token deadline immediately after old history rotated, with unchanged
head/tool hashes. The existing 8K cache margin was smaller than large game
exchanges. The 0.2.74 follow-up measures the latest whole exchange only on actual
overflow, amortizes rotation within a quarter-window cap, and preserves the
latest fitting exchange plus fresh facts and raw journals. Its default remains
opt-in; no fitting history is removed early and the 128K capacity is unchanged.
Full framework regression passed 1,389 tests and 184 isolated-wheel checks;
the counter/template CPU replay proves prefix preservation, not live latency.

Later observation reopened a separate service-capacity issue: the finite
post-deployment window did not prevent subsequent deadlines at 115K+ input
tokens. At the first recurrence, the diagnostic head/tool/first-history hashes
were stable and no history was retired. The local engine's four admissions
share a pool whose published budget allows only one full 128K request; a full
game request and a small independent review can exceed that budget together.
The human requested actual resident capacity to be improved. A separately
journaled KVarN trial increased the local engine's cache budget and showed two
simultaneous requests, but five successful body replies and four deadlines
failed timeout acceptance. The trial was replaced through another normal,
drained operation with the same model's MTP predictor and FP8 cache. Its smaller
recurrent-state cost leaves more room for a long game request plus a small
independent review. Both retain the same weights, vision and 128K capacity;
neither holds two complete 128K requests on this card.

The MTP evaluation has naturally returned a 101,451-token prompt with 91,520
cached tokens in 47.938 seconds, while further observation continues. The cold
starts, changed inputs and cache states are not a controlled speed comparison.
This is a user-owned deployment choice: it does not change the public plugin's
model defaults, SDK, game configuration or dependency pins. Successful engine
initialization, model responses, actual native actions, sustained goals and
desktop audio playback remain separate acceptance checks.

The completed bounded MTP record contains 14 body replies and no body deadline,
with a 47.2895-second median; ten replies finished before an announced server
maintenance disconnect. A 116,224-token prompt reused 108,992 tokens and returned
in 37.343 seconds. The official review gateway still recorded one timeout and
one success. The subsequent history reset followed the connection/identity
transition, so it does not validate overflow retirement. The same executor
reconnected naturally. This remains finite deployment evidence, not long-term
survival, a controlled model comparison or a new release.


## Mixed-size replies and the next complete inspection

The later complete inspection found renewed deadlines in the same MTP profile;
the earlier fourteen-response window did not establish a permanent solution.
One independent source defect is reproduced at the real controller boundary:
a large tool reply followed by short speech/help caused the last-exchange
estimate to fall to a tiny value. The following large reply retired three more
history groups. Autonomy 0.2.75 takes the largest measured marginal growth from
at most four recent complete exchanges. It retains opt-in/actual-overflow-only
activation, the quarter-window bound, fitting history, fresh evidence and the
original journals. The source regression has 1,394 passing cases; the isolated
wheel has 189 targeted cases plus the SDK-free fake-body probe.

This source fix does not explain deadlines whose history/prefix never changed.
A separate local service trial reserves more GPU headroom with
`GPU_UTIL=0.85` on the same fixed HyperQwen MTP/FP8 image, same weights, vision,
128K per-request capacity, strict=function and two admissions. The engine now
reports 155,316 equivalent cache tokens, 204 usable blocks, and 173 blocks per
full 128K request. Two full 128K contexts still do not fit. This is one RTX 3090
WSL/desktop measurement, not a portable plugin default. The upstream
[WSL memory notes](https://github.com/syv-ai/HyperQwen/blob/main/docs/gotchas.md)
describe silent memory spill and the need for non-cache headroom; shared-memory
counters alone do not identify every allocation or establish paging as the sole
root cause.

The service comparison preserved the original body and ten retained history
groups. A 107,422-token cold request returned in 173.375 seconds; subsequent
warm requests reached 119,630 tokens with 109,824 cached, returning in 31.188
seconds. The first six body replies had no deadline, while a separate official
review still timed out. These finite samples precede deployment of the source
fix and must not be attributed to 0.2.75 or called long-term acceptance.
The old container's stop exhausted its 45-second grace period and exited 137;
GPU running/waiting counters being zero did not prove HTTP/pre-admission work
was fully drained. The trace and failed clean-exit assertion are retained.
Neko source/SDK, Java, driver, 250W power cap and private game configuration were
unchanged. Public release remains 0.6.70; no new tag or Market submission.

## Waiting reply after a completed task

The 0.2.75 live trial independently exposed a coordinator race: native task 4
completed at 22:50:06.720 UTC, during an ahead inference that began at
22:50:01.526. The reply arrived at 22:50:16.276 saying to wait for that task.
Because the body was now idle, the no-action branch imposed another 120-second
pause. Autonomy 0.2.76 exempts this existing, known ahead request from that
penalty and schedules the next ordinary boundary using the real terminal.
It does not manufacture actions or bypass failed-anchor cancellation, stops,
ordinary idle backoff or the native queue. The old behavior was reproduced
before the change; 1,395 package tests and 190 isolated-wheel cases plus the
SDK-free probe pass. This thin consumer only pins source `9f199d3d`.

Separately, the 0.2.75 context trial reached 116,507 input tokens, then performed
one real overflow retirement. The next two replies kept the retained prefix
without another retirement. Cold rotation still took 133.141 seconds; warm
responses then took 28.782 and 14.687 seconds. Main-brain reviews have also
produced real acknowledgements, while expired/corrected review attempts remain
separate failures. These finite records do not establish permanent timeout
resolution, complete farm progress or long-term survival acceptance.

The development consumer 0.6.80 was installed through official sync and
refresh/start after the standalone body's normal drain, owner exit and lease
release. Neko, the current model container and private game configuration stayed
unchanged during this source handover. Its finite natural window through 23:06
UTC has 11 successful body replies and no body deadline, with a 21.875-second
median; inputs and cache state differ from earlier trials. Real native
completed/blocked outcomes and one accepted strategic insert were recorded.
Another review exhausted its remaining 71.23-second lease, so the full chain is
not timeout-free. Six audio-completed receipts still do not verify desktop
playback. The identical waiting-reply race was not reobserved naturally during
this window; the targeted red/green test is the direct verification.

The official 3,155-file/18-entry candidate passed structure and hash checks,
tests, Ruff and 11 UI data tests. Its vendored source matches all 68 tested
runtime files in content; one Python file has only checkout CRLF differences,
not raw byte equality. This remains a development package, with public
GitHub/Market release still 0.6.70. No new tag or publication was made.
