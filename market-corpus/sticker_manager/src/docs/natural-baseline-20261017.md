# Natural Baseline And Eager Tuning, 2026-10-17

## Live Evidence

The installed manifest and tool surfaces now match the delivered 0.9.10 test
package. In the fresh 14:10:40-14:16:08 session, Main recorded 23 user
transcripts and 23 complete replies. The plugin recorded five direct-ID calls,
five confirmed images, no lookups and no refusals. Plugin and Main had no
warning or error entries in this window. The observed ratio is about 22%.

Calls took 276.4, 82.3, 50.9, 60.6 and 60.5 ms, including host tail
registration. These are tool durations, not first-word or perceived latency.
Twenty-two reminders were submitted, with passive Main callbacks carrying cues
on a later user turn. Submission is not proof of model consumption. The first
preflight reported busy; subsequent checks were healthy with no missing tools.
The fresh plugin ledger began at zero and later reached 23 turns.

This sample points primarily to turns without tool calls, rather than empty
lookup results or sender rejection. It does not prove why the model skipped
each turn, establish contextual relevance, or rule out duplicate speech.
No user dialogue, query text or credentials are copied into this report.

## Baselines

At the user's request, natural adopts 0.9.10 eager's willingness, checkpoint,
trigger criteria, tool note, both complete tool descriptions, reminder pointer,
reminder text, one-turn cadence and everyday shortcuts. Only the tier name
changes from eager to natural. Natural remains the configured default tier;
saved user configurations are not rewritten.

`tests/fixtures/eager_0910_baseline.json` is generated from the delivered
0.9.10 package after checking its pure modules against the reviewed source.
It includes full description cases, reminders and official shortcut samples
with and without recent exclusions. Tests cover the actual plugin shortcut
path and panel switching, as well as future eager policy changes.
Reserved's 0.9.8 fixture and complete standing surfaces remain unchanged.

Reminder intervals are now reserved 3 / natural 1 / eager 1 observed turns.
The existing minimum interval, polling and positive override remain effective.
The tier label is a model preference, not a frequency timer or quota.

## New Eager Preference

Prefer one fitting image on most complete casual replies. A previous image
does not itself prevent a different image for this turn's expression.
The text-only request, serious-task and missing-fitting-image exits remain.
Do not select by fixed turn counts or introduce random fallback attachments.

Shorten repeated standing matching prose, retaining full per-image
descriptions and checks for attitude, recipient, action, stage and tone.
A simple expression may convey the current attitude without reenacting every
action in the normal reply. It must not add an unmentioned event.
Detailed matching examples still appear in lookup receipts.

The local AstrBot reference's `_conf_schema.json` quantity guidance distinguishes
recent image use from a new expression need; `main.py` appends enabled quantity
guidance and examples. Those options are disabled by default in the reference.
We adapt the distinction and short example, not its numeric ratio, response
marker parser, random category delivery or optional auxiliary model calls.

The 12-row/2,000-character shortcuts are global everyday representatives from
the active collection, not candidates selected from the current conversation.
Recent exclusions and deterministic ordering remain the same as 0.9.10.

Read-only comparison against the currently installed 190-image library, with
five recent exclusions, retained identical 12-row shortcuts (1,072 characters).
Natural's complete send/list surfaces exactly matched old eager except for
the tier name. New eager's send description fell from 3,038 to 2,841 characters,
and its lookup description from 444 to 370. These are prompt character counts,
not token, latency or frequency measurements.

## Validation Limits

The isolated full suite passed 1,205 tests with eight skips. Ruff, SDK check,
official release check and Hosted TSX compilation passed. The SDK snapshot's
missing-own-Git-repository warning is expected for its isolated source copy.

Only plugin code is changed. Sender gates, host transport, image bytes, live
installation and saved profiles are untouched. Normal speech comes before
selection; receipts finish silently after completed speech and default text
is empty. Offline checks verify those contracts and baseline preservation.
They cannot demonstrate higher live model frequency, lower first-word latency
or absence of duplicate dialogue. A new eager session is needed for that.
