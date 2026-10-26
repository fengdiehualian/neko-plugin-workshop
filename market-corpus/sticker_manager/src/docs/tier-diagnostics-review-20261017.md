# Diagnostic Build Tier Review, 2026-10-17

## Live Evidence

Read-only review of the Electron test profile:

`D:\neko kaifa3\dist\desktop-test-20261017-c21391a\test-profile\AppData\Local\N.E.K.O`

The installed manifest is now `0.9.12+test.20261017`, and installed
`core/eagerness.py` matches the development checkout by SHA256. The last Main
log entry in this review is 15:19:37, Asia/Shanghai.

Windows start at successful tool-surface registration, rather than panel click
time. Main user-transcript and response-complete counts agree in each window.
These are total conversational turns, not a classification of image-eligible
casual replies. No dialogue or credentials are reproduced here.

| Tier | Window | Inputs / complete replies | Lookup calls | Send calls / confirmed images | Reminder submissions |
| --- | --- | --- | --- | --- | --- |
| Eager | 15:02:35 to 15:07:41 | 18 / 18 | 0 | 0 / 0 | 14 |
| Natural | 15:07:41 to 15:14:04 | 21 / 21 | 0 | 3 / 3 | 18 |
| Eager | 15:14:04 to 15:18:14 | 18 / 18 | 0 | 0 / 0 | 18 |
| Natural | 15:18:14 to last observed reply at 15:19:04 | 7 / 7 | 0 | 0 / 0 | 6 |

Both eager windows total 36 replies without a sticker tool call. Natural's
middle window has three images in 21 replies; its subsequent seven replies
have none. This supports the reported inversion in this sample, but neither
establishes natural's stable frequency nor proves a causal tier effect.
Sessions were reinitialized during the run, so this is not a controlled
same-context comparison.

The three successful sends entered at 15:08:09, 15:10:16 and 15:13:42.
Their context fingerprints differ, each has attempt zero and caption length
zero, and each has one matching return and confirmed image. Recorded tool
durations are 258.0, 63.7 and 41.8 ms, not first-word latency.

## Where Sending Stops

The plugin logs contain no warning/error entries in these windows. Main
records both sticker tools registering on every observed tier switch.
Registry health is confirmed during the second eager window; initial busy
preflight results during the first window are inconclusive, not proof of
missing tools. No tool-refusal fallback is logged in the reviewed interval.
This establishes registration evidence, not the exact outbound provider
request payload.

The catalog remains at 22 categories. Quick-candidate descriptions remain
present before and after each switch (1,073 characters at the first pair,
1,085 at the later pair). Natural and eager use the same candidate-selection
profile and the same sender gates. The effective send cooldown is zero.
No lookup or send was attempted in either eager window, so empty lookup
results, cooldown, recent-image exclusion at send time, probability refusal,
and image delivery failure do not explain those windows' zero sends.

The observed failure is upstream of plugin tool execution: no sticker tool
invocation arrives. The existing logs cannot distinguish a model decision to
skip from an upstream request/tool-visibility problem on every skipped turn.
Reminder submission alone does not prove same-turn model consumption.

## Policy Difference To Investigate

Natural retains the delivered 0.9.10 strategy. Eager's later revision both
changes willingness and shortens standing tool guidance:

- Natural: casual chat defaults to one fitting image.
- Eager: most complete casual replies preferably receive one fitting image.
- Eager replaces some of natural's trigger, matching, shortcut and example
  wording; it is not merely natural plus an additional eagerness instruction.

The revised wording could make skipping easier despite the stronger tier
name. This is a hypothesis, not an established provider-side explanation.
Reducing description length did not demonstrate improved tool use here.

The conservative next policy experiment is to preserve natural unchanged
and build eager from the proven natural surface with a narrow additional
preference. Compare matched fresh-session conversations on the same model
and settings; retain tool-call and repetition diagnostics. Do not reduce
sender safeguards or choose arbitrary fallback images to manufacture a rate.

## Repetition Status

The retained topic-signal window contains 31 AI entries, from 15:10:17.782
through 15:19:04.600. The existing offline analyzer found no exact full-reply
copies, normalized full-reply copies, or repeated normalized heads of six or
more characters. This limited sink does not retain all 64 replies counted
above and cannot reconstruct the entire session.

The diagnostic package adds logging and does not change continuation control.
No raw-provider observer JSONL exists in the diagnostic output directory.
Consequently this review cannot credit diagnosis with fixing repetition,
exclude paraphrased/middle-only repetitions, or establish the original cause.
Keep the issue open pending raw-stream evidence if it recurs.

## Scope

This review adds only this evidence note and an offline metadata report.
No plugin behavior, host code, installed files, API settings, tier settings
or chat history is modified. No build or live model call is required for
this read-only investigation.
