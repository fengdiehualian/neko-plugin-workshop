# Reserved Baseline, 2026-10-17

## Observed Reference

The user designated the observed 0.9.8 eager behavior as the new reserved
baseline. The fresh session from 12:14:39 through 12:22:22 on October 17
contained 29 user-transcript events, 29 response completions, two direct-ID
send calls and two confirmed images. There were no lookups or send refusals.
The plugin submitted 12 reminders during that session window.

Both successful calls used the normal host reply-tail transport. Their tool
receipts took 251.3 ms and 81.4 ms; these durations are not whole model latency.
The shortcut description refreshed after each successful use. The startup
registry gap was reissued and later confirmed healthy.

The live profile used zero send cooldown, zero reminder minimum interval,
probability 1.0, recent deduplication of five images and tier-following reminder
cadence. Those are existing user settings, not defaults to impose on everyone.
The plugin's cumulative turn counter included 40 retained turns at restart;
the fresh sample has 29 turns, not 69.

## Mapping

Version 0.9.9 copies the 0.9.8 eager strategy into reserved:

- Reminder willingness, including the original soft frequency reference.
- Tool checkpoint, trigger criteria and final willingness note.
- Three-turn tier-following reminder cadence.
- The existing shared shortcuts, conservative lookup, send gates, transport,
  speech-first guidance and private nonrepetition receipts.

Only the model-facing name changes from eager to reserved. The original
strategy is frozen in `tests/fixtures/eager_098_baseline.json`. Later eager
experiments must not change this baseline by deriving reserved from eager.

The two-per-29 sample is an observed outcome, not a scheduling requirement.
The original prompt's one-per-two-or-three soft reference is retained to
preserve the tested strategy; it is not advertised as the measured rate.
Text-only requests and unsuitable candidates still take precedence.

Natural and eager are left at their previous strategies pending later tuning.
The transitional release does not claim they already produce higher frequency
than the new reserved baseline. Default tier selection remains natural.

Install the package and select reserved to use this baseline. No saved tier
selection, custom reminder override, cooldown, probability or collection data
is silently migrated. Existing user settings remain the way to reproduce the
same test conditions. No host source or live installation is edited by the
build. Tests preserve policy and delivery contracts; they cannot guarantee a
fixed frequency, latency or unique host speech.
