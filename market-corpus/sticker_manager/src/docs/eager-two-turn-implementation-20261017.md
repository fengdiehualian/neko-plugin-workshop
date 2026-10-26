# Eager Post-Reply Selection, Release 0.9.5

The maintainer selected `0.9.5` as the release version for the implementation
tested as `0.9.14+test.20261017`. Historical test observations below retain
the version installed at the time.

On October 18, 2026, the maintainer made the selection threshold configurable
from 0 to 10 with a default of 1. Zero means selection on every completed reply,
not disabling selection. The current behavior below reflects
that adjustment; October 17 validation and live observations remain historical.

The setting is saved from the eager panel slider through the existing host
configuration API. Changes cancel old selection work and rebaseline retained
history. Since a completed unillustrated reply already counts as one, values
0 and 1 have the same selection cadence under the existing-send guards.

October 18 validation: 162 focused Python regressions and 31 hosted UI
regressions passed, including every supported threshold, persistence, zero
selection, duplicate polls, no-match retry and cancellation on parameter edits.
Actual host-renderer browser checks passed at desktop, mobile, 320-pixel and
dark viewports; zero and ten were saved through the panel. SDK and Hosted TSX
checks passed. No real model calls or live dialogue acceptance were performed.

## Behavior

Natural and reserved retain their full delivered guidance. Eager retains the
0.9.13 complete natural guidance plus its existing extra preference.

Only eager schedules additional selection. A one-second timer observes the
public conversation bus, counts normal completed replies without a submitted
image, and starts a single separate selection after the configured gap
(`send.eager_attachment_turns`, default 1). Zero evaluates every completed reply;
existing submitted or pending images still prevent duplicate attachment. Further
unillustrated replies can trigger another attempt until a submission resets
the gap. Ineligible text-only/serious-task decisions reset the gap without
sending. The counter initially counts normal replies; eligibility is evaluated
by the selector at the threshold, not by keyword heuristics.

Bootstrap and session-change observations consume retained history without
sending for it. Replies older than 20 seconds are not counted for fallback.
Successful ordinary sends reset the gap; pending/uncertain sends prevent a
competing attempt without pretending a delivery succeeded. Failed sends do
not reset the gap.

The selector sees at most 24 fully described candidates, selected using
existing local intent retrieval plus everyday-expression coverage. Disabled,
inactive-zone, undescribed and recently used images are excluded. Candidate
data has a 14,000-character bound; captions are never clipped. Recent same-role
conversation context includes up to 12 records; oversized individual/context
text causes a skip rather than truncated output restrictions.

One nonstreaming request uses the declared optional host model usage
`eager_attachment`. It has a 12-second deadline and returns only a validated
candidate ID or a strict skip reason. Missing binding/service backs off for
60 seconds. No credentials, host model configuration or provider settings
are copied. The bound plugin model is not necessarily the conversation model.

The sender accepts the new internal `eager` source and keeps cooldown,
probability, per-role recent-image exclusion, current-zone and disabled-file
checks. Eager delivery uses image-only parts and `ai_behavior="blind"`; there
is no conversational response, caption, main-model continuation, or raw
dialogue persistence. Existing sender activity reservations arbitrate competing
sends, including an ordinary send occurring during selection.

## Stale Work

Before/during selection and before delivery, check current input, latest
reply, role, idle host response state, sender activity and configuration epoch.
A reply observed while the host is briefly busy can wait for readiness within
its age bound; claiming it before model selection prevents repeated model
requests for that reply.
A read-only Main-log ticket detects superseding inputs and session boundaries.
Log loss or unavailable health/bus data prevents automatic attachment.
New input cancels an outstanding model request, and configuration/shutdown
invalidation also cancels it across the host's event loops.

After image upload, check the log ticket and current configuration again.
The final synchronous submission shares the configuration lock, role
reservation and library lock. Revalidate the selected captions, description,
tags, group and visible text; changed candidate meaning cannot be submitted
using the old selection. Active-zone changes and expired replies also reject.

Session/input observation and transport are separate processes, so the
plugin cannot make input arrival and image consumption one atomic host
transaction. A transport submission already in progress cannot be recalled.
`submitted` means accepted by the plugin transport, not independently proven
UI rendering. Existing host tail submissions retain their stronger receipt
handling.

## Setup And Limits

After importing, bind a text model to the plugin's "eager image selection"
usage in host plugin model settings, choose eager and start a new session.
The feature adds model usage and image latency without holding up normal
speech. Unsupported hosts or missing bindings retain normal sticker tools.
The plugin settings surface reports missing/unavailable models and exposes
attempt/submission counts.

Pure-text constraints and suitability are model decisions based on supplied
context. The plugin cannot guarantee semantic classification, infer omitted
standing instructions, or produce suitable images from an unsuitable library.
This is a configurable reply selection threshold, not an unconditional image quota.
Live same-model acceptance remains required. The earlier dialogue-duplication
root cause remains open; this path avoids initiating tool continuation but
does not claim to repair existing host continuation behavior.

## Validation

Focused tests exercise the actual plugin timer, strict bus signatures, model
request, sender and image-only transport. Cases include retained history,
duplicate polls, all tiers, pending/successful ordinary sends, input/session
changes, cancellation of a hung selector, config/shutdown, candidate edits,
deletion, zone changes, upload-time interruption, existing send gates, invalid
or truncated output, timeout and binding backoff.

- Focused new selection/transport regression: 40 passed.
- Full isolated-host regression: 1,267 passed, 10 skipped. Network-dependent
  diagnostics are skipped by the isolation harness.
- Pinned Ruff 0.12.4 and 29 existing hosted UI regression cases passed.
- The 21 reply diagnostic tests also passed in the ordinary development
  environment, including real loopback HTTP/SSE forwarding.
- Isolated SDK check, official release check and Hosted TSX check passed.
  The build snapshot's CLI warns that `plugin.models` is unrecognized; its
  manifest allow-list lags the actual host configuration schema and model
  gateway, both of which support this declaration. The optional usage remains
  in the installed manifest and is read directly by the binding/gateway service.
  The second SDK warning concerns the disposable copy lacking its own Git repo.
- Actual host UI renderer browser checks passed at desktop, mobile, 320-pixel
  narrow and dark viewports, including missing-model and selecting states.
  Screenshots and preview are under the test build's `ui-preview` directory.
- The pre-package checks made no real model requests, binding changes,
  installation or host edits. The subsequent user-run sample is recorded below.

## Subsequent Live Observation

On October 17, 2026, the user installed `0.9.14+test.20261017` and subsequently
bound the selection model. Earlier `model_unbound` events at 16:30:55 and
16:31:57 precede the successful sample; they do not describe the later setup.
Times below are Asia/Shanghai.

The bounded eager window 16:36:53 through 16:43:50 contains 19 user inputs and
19 response-complete events in the Main log. The plugin reports five image
submissions: four from `source=eager` and one from `source=tool`. The four
fallback submissions occurred at 16:37:19, 16:38:33, 16:42:22 and 16:43:33.
Their selection-to-submission log intervals were approximately 3, 3, 4 and
6 seconds; normal dialogue completion did not await these submissions.

The same window contains 12 selection attempts: four `submitted`, five
`invalid_result` and three `superseded`. The logs do not retain raw selector
output, so the specific validation failures cannot be reconstructed from
this sample. Newer work or changed session/input state may supersede an
attempt. No Main or plugin warning/error entries were found in this window.
These observations demonstrate that the bound fallback can submit images,
but do not establish a stable one-image-per-two-replies guarantee.

The retained topic-signals sink contains 20 AI records timestamped inside
the window. The diagnostic checker found no exact full repeats, normalized
full repeats or repeated normalized heads in those records. Sink records
and Main completion events are distinct observations and are not a verified
one-to-one ledger. This limited sample does not resolve the earlier duplicate
dialogue root cause.
