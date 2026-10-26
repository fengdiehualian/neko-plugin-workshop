# Eager Tuning, 2026-10-17

## Evidence And Scope

At review time the Electron test installation still contained 0.9.8, with no
new run of 0.9.9. The established fresh-session sample was 29 complete replies,
two direct-ID sends, zero lookups and zero send refusals. Later ledger updates
still showed two confirmed images. This change is not a measured improvement
on a live 0.9.10 session.

Reserved's 0.9.8 strategy remains frozen. Natural is unchanged. Only eager's
willingness, tier-following reminder cadence and shortcut presentation change.
No send gate, contextual matcher, host code or live installation is modified.

## Reference Project

Reviewed the local `astrbot_plugin_meme_manager-main`:

- `_conf_schema.json`, `generation.prompt.quantity_guidance`: permits images
  even when words already express the reply. Quantity guidance is optional and
  disabled by default; its existence does not establish observed frequency.
- `generation.prompt.reply_example` and `category_example`, plus `main.py`'s
  `_build_meme_prompt`: optional short demonstrations distinguish image/text,
  text-only and image-only replies. Eager adapts this idea with one short
  everyday-question/text-only example using real candidate IDs, not fake IDs.
- `main.py`, `_semantic_system_prompt` and `search_memes_tool`: search returns
  per-image descriptions and IDs; delivery uses response markers parsed by
  AstrBot hooks. Our tool-based transport keeps explicit-ID sending.
- `mixins/event_handlers.py`, `_resp_semantic_llm_impl` and
  `_build_emotion_semantic_query`: an optional auxiliary model generates query
  intent and chooses an ID. That adds model calls and depends on AstrBot's
  request/response context. It is not introduced into this plugin.

## Eager Policy

During casual chat, choosing one fitting image is the default preference.
Everyday questions about feelings, preferences or recent life are still casual
chat, rather than an automatic reason to skip images. A simple expression can
convey the assistant's attitude without reenacting every action in the prose.
It still cannot add an unmentioned event, reverse the recipient or alter the
reply's meaning. Text-only requests, no fitting image and serious tasks remain
valid exits. Normal speech precedes selection and is not repeated afterwards.

Eager's tier-following reminder interval changes from three observed turns to
one. The existing polling interval, minimum reminder interval and explicit
positive turn override remain effective. This does not promise a same-turn cue,
one model invocation per user event or an image after every reply.

## Shortcut Coverage

The global shortcut list is still not selected from the current conversation.
Eager browses a wider set of everyday intents while retaining the same limit
of 12 distinct rows and 2,000 characters. Full captions and tags are preserved.
Among equally used candidates it retains matcher relevance rather than sorting
by arbitrary ID first; ties are deterministic. Recent-use exclusions, active
collection and disabled/unannotated filters remain effective.

Eager uses `犯困` to avoid selecting a puzzled representative from a broad `困`
substring match. This only changes eager's browsing query, not the shared
matcher or the reserved baseline.

Offline on the 190 reviewed official captions, excluding IDs
`089,007,100,004,102`, the baseline list has nine rows while eager has twelve.
Eager includes approval (`005`), playful expression (`012`) and drowsiness
(`145`). These are label IDs, not actual installed runtime IDs. This demonstrates
coverage of this sample, not relevance to an unknown live conversation or a
guaranteed frequency increase.
