# Eager Two-Turn Target: Local Evidence And Reference Review

Reviewed on 2026-10-17, Asia/Shanghai. This is an investigation and proposed
implementation contract, not a behavior change or a new test package.

## Live Evidence

Profile: `dist/desktop-test-20261017-c21391a/test-profile/AppData/Local/N.E.K.O`.
The installed manifest reports `0.9.13+test.20261017`.

Window: successful eager registration at 15:42:29 through 15:51:27.
The Main log contains 34 user inputs, 34 response completions and 34
character-specific turn-end records in this window.

| Reply number | Completion | Confirmed sticker delivery |
| --- | --- | --- |
| 4 | 15:43:33 | 15:43:33 |
| 19 | 15:46:55 | 15:46:55 |
| 34 | 15:51:16 | 15:51:16 |

There were three sticker_send calls, three confirmed images, no sticker_list
calls, and no plugin warnings/errors in the window. Two successive image
intervals were each 15 complete replies, including 14 text-only replies.
Observed image frequency was 3/34, about 8.8%.

The send cooldown was zero. Registry checks reported healthy, the session
snapshot included both tools, and all three attempted deliveries succeeded.
The long gaps consist of no tool invocation, not failed lookup or rejected
delivery. These records do not prove provider-side tool visibility on every
individual request or explain the model's internal decision.

Do not use the run ledger's 98 turns as this sample's denominator: startup
also observes retained historical user records. Use the bounded Main log.

The retained topic_signals sink covers 30 of these replies, starting at
15:44:00.177. The existing analyzer found no exact full copy, normalized full
copy or repeated normalized head in those 30 entries. This is incomplete
coverage and does not establish a fix for the earlier duplication.

The 10-second reminder poll can arrive after completion: input at 15:42:39,
completion at 15:42:41, reminder at 15:42:48. Passive reminders can influence
later requests; they are not synchronous per-request enforcement.

## Reference Implementation

Reference root:
`D:\neko kaifa3\不同项目的同类插件参考\astrbot_plugin_meme_manager-main`.

- `main.py:673`, `_semantic_system_prompt`: search when an image helps;
  optional image selection, at most one search per reply.
- `main.py:837`, `_apply_request_prompt`: select mutually exclusive tool or
  auxiliary-model mode. It does not set required tool choice for chat search.
- `_conf_schema.json`, generation.emotion.probability: its hint says to require
  search according to probability. That wording does not match the current
  chat implementation and is not evidence of a hard send guarantee.
- `backend/semantic/caption.py:566`: actual `tool_choice="required"` belongs
  to the visual caption/metadata task, not ordinary conversation attachment.
- `mixins/event_handlers.py:918`: auxiliary semantic mode derives a search
  query from the existing reply, retrieves candidates and asks a separate
  model to select a validated ID or an empty result. This can make two model
  calls; it still does not guarantee an image.
- `mixins/event_handlers.py:1039`: response processing claims the response
  before asynchronous work, ignoring duplicate hooks.
- `mixins/event_handlers.py:1283`: semantic selections are not discarded by
  a second random gate. Legacy selection also reuses its probability result
  instead of independently rolling twice.

The useful transferable idea is independently scheduling selection for an
already-written reply, with validated IDs and response-level idempotency.
There is no verified reference mechanism guaranteeing a delivery every two
chat replies.

## Proposed Plugin-Only Contract

Preserve natural/reserved behavior and eager's complete natural guidance.
For eager, count eligible completed casual replies since confirmed delivery.
At two eligible replies without an image, schedule one auxiliary selection
for the latest completed reply. An existing successful or pending tool send
for that reply must prevent a competing auxiliary send.

Use the published conversation identity, role, content and timestamps;
bootstrap a watermark instead of sending for retained historical replies.
Deduplicate before asynchronous selection and revalidate immediately before
delivery. A new input, role/session change, tier change, disable, shutdown,
timeout or stale identity must cancel the old selection. Failed or uncertain
delivery must not reset the successful-delivery counter.

Selection consumes the actual reply and fully described valid candidates.
It returns only a candidate ID or an explicit skip, including pure-text
requests, serious tasks and unsuitable candidates. Do not infer permission
to attach from keyword matches alone, invent IDs or select unrelated images
to satisfy a quota.

Run selection after the normal response without asking the main conversation
to continue. Send an image alone using blind delivery; do not replay the
reply, generate a caption or trigger another spoken turn. Keep delivery and
recent-image checks, pending-send reservations and diagnostic identities.

The actual host build snapshot exposes `bus.conversations.get` and
`ctx.models.get_client()`. Model calls use a declared plugin usage bound to
a host-managed model slot; availability and binding must be checked.
It is not automatically the current conversation provider. No credentials
should be copied from host config or a different model silently substituted.

This adds model latency and usage for selected fallback turns. It enforces a
selection attempt after a bounded gap, not unconditional delivery: suitable
candidates, model availability, request constraints and transport still
matter. Measure per-reply gaps, skip reasons and confirmed images in a new
same-model session before claiming the 1-2-turn target is met.

## Work Performed

Read-only log/source review and existing repetition analyzer; no live model
calls, settings changes, installation, host edits or plugin runtime changes.
The host checkout remained clean.
