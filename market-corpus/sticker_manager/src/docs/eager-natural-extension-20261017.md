# Eager Extends Natural, 2026-10-17

## Change

Version `0.9.13+test.20261017` restores eager to the full natural strategy,
changing only the tier label and adding:

> 上一轮发过，本轮有贴切的新图仍优先配一张。

The extra sentence appears once in each send/list tool description and
reminder. Eager inherits natural's default fitting-image preference,
everyday-question trigger criteria, shortcut instructions, worked example,
matching rules, lookup instructions and text-only exits.

The shortened eager alternatives are removed from the shared surface builder.
The three other guidance mappings are retained as immutable strings derived
from natural where appropriate; future eager edits do not mutate natural.
No change to candidate generation, cadence, sender gates, image transport,
reply order, tool receipts, or diagnostic logging is included.

## Evidence And Tests

The preceding diagnostic run had 36 eager replies with no lookup/send calls.
Natural's middle window had three images in 21 replies, followed by seven
without images. Details and limits are in `tier-diagnostics-review-20261017.md`.
That observation motivates restoring the complete baseline; it does not
prove any particular instruction caused the inversion.

Regression checks compare all natural and reserved surfaces to their existing
delivered-package fixtures. Eager's full tool descriptions, reminders,
checkpoint and willingness must equal the verified 0.9.10 eager fixture after
removing only the new sentence. Both tiers use the same official shortcut
cases through the actual plugin path. A panel-switch test exercises
natural -> eager -> natural without extra chat messages or sends.

The reminder budget expands only by the added sentence for eager, retaining
the existing natural/reserved budget and full private-selection instructions.
This avoids imposing the previously shortened description as a test goal.

## Live Validation Limit

This is a model-guidance change, not a quota or automatic image sender.
Higher actual frequency requires a new same-model conversation sample.
The diagnostic build has not established the original repetition cause;
all existing diagnostics remain available and no hard continuation control
is claimed.

Only plugin sources and development tests/docs are changed. The package is
intended for manual test import; building it does not install it or alter
saved settings, host code or conversations.

## Validation

- Full isolated-host regression: 1,227 passed, 10 skipped. Isolation prohibits
  network connections; the 21 diagnostic tests also pass in the normal plugin
  development environment, including actual loopback HTTP/SSE forwarding.
- Pinned Ruff 0.12.4, SDK check, official release check and Hosted TSX check
  passed. The SDK warning about no Git repository belongs to the disposable
  source snapshot, not the user's checkout.
- Natural and reserved delivered-package baseline comparisons pass. Eager's
  full baseline plus one preference and panel tier-switch checks pass.
- No live model calls or installation were performed. These checks establish
  instruction preservation and package validity, not measured image frequency
  or a fix for repeated dialogue.
