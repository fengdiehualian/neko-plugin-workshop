# Strategic review evidence and adoption repair

A body can keep repeating work even when the strategic model proposes a
correction. Two interface failures were reproduced without a model or game:

- A `continue` judgment with a nonempty `goal` used to acknowledge the review
  while leaving that goal undelivered. New judgments now receive
  `continue_cannot_deliver_new_goal` before speech, context publication or
  dispatch. A caller may explicitly correct to insert/revise once, within the
  original deadline. Recorded historical judgments are not replayed or changed.
- Structured reviews advertised immutable evidence references in their compact
  view but exposed only the final submission tool. Public game facts could also
  be abbreviated before their evidence source was stored. Complete bounded fact
  originals now have explicit query paths and JSON character lengths. The model
  can read up to four pages through the canonical read-only evidence port. A
  query cannot change its bound reference, refresh the scene, speak or dispatch
  work; final adoption still checks ownership, intent, lifecycle and expiry.

The original store admits at most 32 KiB per field and 64 KiB per inspection.
Oversized fields are explicitly unavailable rather than presented as complete
originals. Host persona and private memories are excluded. The game journal and
native source handles remain unchanged. Main-model free text has no voice route.

Validation includes failing replay against the installed old runtime, canonical
ReviewPort tests, bounded retrieval/correction tests using the real SDK with fake
ports, and isolated wheel consumption. It does not prove long-term project
completion, the quality of every model decision, or that server-side task
eligibility matches local planting receipts. No server-specific farming policy
or forced alternative project is added.

The adapter consumes autonomy 0.2.77. Its review and evidence modules are the only
runtime code changes from 0.2.76; the body controller, original queue, core and
model configuration are unchanged. They can be upgraded independently so that
review repairs need not discard an active body's context and native project table.
