# Eager Frequency Review, 2026-10-17

## Local Evidence

Source: the existing Electron test profile's October 17 plugin and Main logs.
The installed plugin manifest identifies `0.9.5+test.20261017`.
No messages were sent and no running installation or configuration was changed.

For the plugin run from 11:03:53 through 11:16:31:

| Observation | Count |
| --- | ---: |
| User-message events observed by the plugin | 36 |
| Main user-transcript events | 36 |
| Main response-complete events | 36 |
| Candidate lookup calls | 1 |
| Send calls / confirmed images | 1 / 1 |
| Send refusals | 0 |
| Signal / count reminders submitted | 5 / 8 |

Lookup took 194.2 ms. Sending returned in 286.7 ms, including 115.7 ms for
host-tail registration. The image was confirmed two seconds later. Send and
reminder minimum intervals were both zero. These observations support low
lookup invocation as the immediate bottleneck, not probability or cooldown
rejection. Response-complete counts do not prove that every displayed sentence
was unique or that each reminder reached the model.

One startup registry gap for `sticker_list` was reissued and subsequently
confirmed healthy. Memory-bus timeouts coincided with plugin shutdown.
Other host warnings concern the meme proxy rejecting `image/jpg`, a skipped
mini-game invitation, and Steam cloud shutdown failing to release a memory
handle with HTTP 503. They do not establish a sticker-send failure and are
outside this plugin-only adjustment.

## Reference Project

Reviewed the local `astrbot_plugin_meme_manager-main` source:

- `_conf_schema.json`, `generation.prompt.quantity_guidance`: optional guidance
  uses approximately one image per four complete assistant replies, encourages
  selection after a recent gap, and leaves room for text-only replies. Its
  enabling switch defaults to false; this is an available idea, not proof of
  the reference plugin's default runtime frequency.
- `main.py`, `_build_meme_quantity_prompt` and `_semantic_system_prompt`:
  share the optional guidance across selection modes and preserve reply intent,
  recipient and candidate-ID selection.
- `mixins/event_handlers.py`: output markers are converted into image requests;
  legacy probability checks are reused to avoid applying probability twice.

The host exposes different APIs. Copying AstrBot's response stream parser or
request hooks would require host integration. This change uses existing plugin
tool descriptions and silent reminder cues instead.

## Plugin Adjustment

The eager tier now suggests one image per two or three complete casual replies
without fixed turn counting, prioritizing candidate lookup after several replies
without images. The same preference appears before selection instructions on
both lookup and send tools and in the eager reminder. Lookup previously had
static guidance even when the send tool switched to eager.

Tier changes refresh both tools, preserve role/schema/timeouts and supported
original-reply context, avoid unchanged registrations, and restore the previous
lookup metadata after a failed update. No new dialogue push path is introduced.
Normal speech still precedes lookup; receipts finish silently if speech has
already completed. Matching, explicit-ID selection, send gates, reminder timing
and non-eager willingness remain unchanged.

Model frequency, actual speech latency and repeated dialogue still need testing
after importing this package and starting a fresh conversation. Unit tests can
verify guidance, registration and transport contracts, not model compliance.
