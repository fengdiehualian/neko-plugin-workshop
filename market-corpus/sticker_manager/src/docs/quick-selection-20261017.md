# Selection Cost And Empty Results, 2026-10-17

## Observed Run

The Electron test profile loaded `0.9.6+test.20261017` at 11:35:13. By 11:39:14,
Main had 15 user-transcript events and 15 response-complete events. The plugin
submitted six reminders, performed one lookup, and made no send calls or send
refusals. Registry checks remained healthy. The lookup at 11:37:56 returned zero
candidates in 202.5 ms for a ten-character query.

The query text was deliberately not logged, so its precise failure cannot be
reconstructed. This change does not claim to reproduce that specific request.
Main also confirms that reminders can arrive after normal speech and are queued
for a later user turn. Prompt submission is not a same-turn consumption receipt.

## Remove A Lookup When Candidates Are Already Known

The send tool now includes a compact candidate list before its longer browsing
instructions. The model reads full per-image descriptions and tags and can call
`sticker_send(sticker_id=...)` directly when a listed image fits. It still uses
the normal lookup path for additional choices. Groups or query-only sends remain
candidate requests; they do not send even when there is one result.

The list covers a small set of everyday browsing intents using the existing
conservative matcher. It is not chosen from the current user's message and does
not claim to be contextually appropriate. There are at most 12 distinct entries
and at most 2,000 characters. Full rows are included or omitted, never cut off,
so a qualification at the end of a caption is not lost. Per-image annotation is
required; group descriptions alone do not prove what a particular image depicts.

Disabled images and inactive collections are excluded. Less recently used
equivalents are preferred, and successful use marks the existing library surface
dirty for refresh. The list is global tool metadata, not a per-role recent-use
guarantee. A stale or recently used ID still passes through the existing
availability checks and per-role send gates. No new image queue, normal-text push
or model request is added.

The list adds bounded input context in exchange for avoiding an extra model/tool
round trip when one of its entries fits. It cannot guarantee that a model will
choose a tool or produce normal speech before it.

## Reproduced Recall Gap

The previous tokenizer recognized separated actions and a limited set of
concatenated mood words, but left action/mood combinations as a literal string.
Offline comparison against the 190 reviewed official captions and tags:

| Query | 0.9.6 candidates | Updated candidates |
| --- | ---: | ---: |
| `求摸摸舒服` | 0 | 3 |
| `自己被摸头开心` | 0 | 3 |
| `给对方泡茶开心` | 0 | 0 |
| `等待收到 开心` | 3 | 3 |
| Unknown complete phrase | 0 | 0 |

Only complete combinations of known positive action and mood words are split.
Recipient distinctions and action filters still apply. Pending-event compounds
are preserved before action splitting: interpreting `期待开箱` as the action
`开箱` must not admit images of an already opened gift. Unknown phrases and
negation do not fall back to fragments such as `开心`.

## Diagnostics For The Next Live Miss

The shared matcher optionally returns integer counters without changing results:

- `pool`, `disabled`: input scope and disabled images.
- `query_chars`, `query_clipped`: length and whether the 300-character cap applies.
- `terms`, `literal_only`, `actions`, `stages`: query structure; no token text.
- `action_filtered`, `stage_filtered`, `receipt_filtered`: explicit incompatibility.
- `no_word_match`, `scene_filtered`: no lexical evidence or an unrequested scene.
- `matched`: survivors before recent-use filtering.

Both tool lookup paths log `recent_filtered` and `selectable` afterwards.
Unavailable group scope has a separate `scope_unavailable` record. No query,
caption, user message or normal reply text is copied into these diagnostic lines.
This distinguishes a lexical miss from strict filtering or recent-use exclusion
on the next test without weakening selection merely to increase send counts.

Only plugin source and a new test package are changed. Host source, running
installation, user configuration and collection data are left untouched.
