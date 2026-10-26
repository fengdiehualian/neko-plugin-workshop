# Selection Cost Review, 2026-10-17

## Observed 0.9.7 Run

The installed manifest was `0.9.7+test.20261017`. Startup at 11:58:32
confirmed eager descriptions of 3,498 characters for sending (including
1,102 candidate characters) and 930 for lookup. Registry checks stayed healthy.
Send and reminder minimum intervals were zero; probability was 1.0.

From the fresh session snapshot at 11:58:54 through completion at 12:05:02,
Main logged 25 user-transcript events and 25 response-complete events. The
plugin made two direct-ID send attempts without a lookup:

- 11:59:47: rejected as `recent_repeat` in 3.5 ms.
- 12:00:27: queued in 600.9 ms, including 290.6 ms of host registration;
  confirmed sent at 12:00:29.

There were no lookup calls, empty candidate results, cooldown or probability
refusals in this window. Successful direct-ID use demonstrates that the earlier
change removed a lookup round trip on these attempts, but sparse invocation and
a wasted repeat attempt remain. Completion events do not establish semantic
uniqueness of displayed speech or prove that every turn deserved an image.
The first refusal did not log its ID; it cannot be attributed to a specific
shortcut entry. Global shortcuts did not exclude recent-use IDs at all.

The plugin's turn counter started at 15 after restart because retained events
were observed again; its later value of 40 is not 40 fresh replies on 0.9.7.
Passive reminders can still be carried on the next user turn rather than
consumed immediately. No new dialogue or query text was collected.

## Plugin Changes

Eager wording consistently prefers a described shortcut ID and reserves lookup
for more choices. The reference of one image per two or three casual replies
remains a preference, never a quota. Standing instructions use compact matching
principles; detailed action/stage examples remain in lookup receipts. Full
per-image captions and tags remain intact in the shortcut list.

Shared metadata omits the latest N distinct successful images from the persisted
usage ledger, across roles, where N is the configured recent-dedup count.
Failures and duplicate success rows do not occupy extra slots. Setting N to zero
disables this omission. A singleton recently used image can therefore disappear
from shortcuts rather than being advertised and refused repeatedly. Startup
reads existing history and successful use marks the normal refresh dirty.

This shared presentation filter is not a per-role eligibility claim: another
role can still find and send a shortcut-excluded image through its ordinary
lookup. Likewise, stale shortcuts or older role-specific history may still
cause a repeat refusal. There is no bypass, retry loop or automatic replacement.
All existing candidate matchers and send checks remain in place.

Normal speech is still requested before tool calls; receipts tell the model
to finish if it has already spoken. The plugin adds no normal-text emission,
model request or host code change. Tests verify data flow and delivery contracts,
not model tool frequency, actual perceived latency or speech uniqueness.
