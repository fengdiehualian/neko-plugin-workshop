# Isolated Hosted UI QA

## 1.4.0 Card Surface Release Acceptance

The final settings surface reuses the plugin card border, radius, shadow and
single wallpaper material. The shared save bar stays outside the surface.
`../dist/qa-settings-card-border/report.json` passed 101 interactions and
78 screenshots across eight locales, desktop/narrow and light/dark.
`../dist/qa-settings-card-border-regression/report.json` passed 63 general,
gallery, transparency and compact-footer checks. Both reports have zero check,
render or layout failures. Hosted typecheck passed. These are isolated checks;
Steam installation and testing remain manual.

## 1.4.0 Settings Layout And Media Categories

Settings now opens on Appearance, with separate General and Data views.
Views stay mounted when hidden, retaining form drafts, active imports, directory
candidates and unsaved storage inputs. Debug mode is under Advanced. Reset and
orphan cleanup retain their confirmation flow and current-character protection.
Flat sections share one outer surface using the same border, radius, shadow
and wallpaper material as other plugin cards. The unified save bar remains
outside that surface and still writes only dirty settings and appearance.

The wallpaper library separates images and videos with counts. Missing legacy
`kind` means image. Filtering does not change the selected background or save it.
Successful imports reveal their category without selecting the new item.
Revert reveals the saved item's category. Import methods, adjustments and
storage are separate disclosures. Import errors, progress and cancellation stay
outside the import disclosure.

Final reports under `../dist`:

- `qa-settings-layout-final/report.json`: 101 interactions and 78 screenshots,
  covering all eight locales, 1280/390 widths, light/dark, and a 320px empty-video
  case. Keyboard tabs, drafts, shared save from Data, cancelling reset, import
  methods, cross-type revert, image import, visible errors after collapse and
  deleting the last video all passed.
- `qa-settings-layout-regression-final/report.json`: 63 general, gallery, compact
  savebar and diary-footer interaction checks passed.
- `qa-settings-layout-video-final/report.json`: 61 video interactions and 31
  screenshots passed, including real moving pixels, a video over 48 MiB,
  import/cancel/clear races, reopen, reduced motion and decoder failure.

There were no final check, render or layout failures. Desktop and narrow
screenshots were visually inspected. Old test selectors now navigate the
sections/disclosures. Cancellation checks use the recorded cancellation time,
so legitimate hedged reads begun before cancellation are not mistaken for new
reads after cancellation. The production transport was not changed.

The full Python suite passed 972 tests with 38 existing explicit skips.
Hosted typecheck and the 32-module link check passed.
The isolated official build passed strict validation, ZIP CRC, payload hash and
comparison of all 77 packaged source files.

Package: `../dist/forever_companion_1.4.0_settings_layout.neko-plugin`
(682,026 bytes). Version stays 1.4.0 for this local import test.
SHA-256:
`d23faa3f14656a96e9ecf2989f3a478aa847905c69a1c7a345fd4fe7b910037f`.
The host Git worktree remained clean; no live Steam installation was changed.
Actual Steam import and appearance remain user acceptance tests.

## 1.4.0 Version And Import Package

The 1.3.10 performance and recovery changes documented below are included in
1.4.0. The manifest, Python project, lockfile and current README version agree.
Historical QA reports and the earlier import-test package retain their names.

For 1.4.0, the full Python suite passed 972 tests with 38 existing explicit skips.
Hosted typecheck and the 32-module link check passed. The isolated official
build passed strict validation, ZIP CRC, payload hash verification and comparison
of all 77 packaged source files.

Package: `../dist/forever_companion_1.4.0.neko-plugin` (679,048 bytes).
SHA-256:
`37e59cc73ded21de5acb335b37adcf91672fcfd030815a4d12f5819a515da2d7`.
Reports: `../dist/pytest-release-140` and
`../dist/qa-release-140-typecheck/report.json`.
Steam import and actual GPU/window-switch behavior remain user tests.

## 1.3.10 Panel Loading And Video Recovery

The plugin combines the initial gallery snapshot with the selected wallpaper.
Video metadata can reuse that request's storage scan; the filesystem scan uses
one worker dispatch. Older gallery responses remain supported. Direct and Blob
playback use the displayed decoder, with the previous background retained until
the candidate presents its first frame. Invalid candidates retain the old
background or static cover. Hidden documents skip dashboard polling; slow polls
do not overlap, and visibility restores refreshes.

The actual player listens for focus/pageshow and detects missing presented
frames even when `paused` is false. It tries a small seek, then one reload of
the same URL. A fresh video-frame callback is armed after reload. Persistent
failure follows the existing direct-to-Blob fallback or displays the cover.
Long suspension uses a seek to refresh decoder output before showing motion.
This cannot identify all corruption while frames continue to arrive.

`../dist/qa-panel-perf-direct-1310-final/report.json` passed 24 checks, including
desktop/mobile reopen, focus/pageshow, a decoder stall with false paused state,
a missing-frame stall with advancing media time, a single same-source reload,
legacy metadata responses, HTTP Range, direct errors and reduced motion.
The installed 198,941,764-byte MP4 was tested read-only through a temporary
copy; playback and reopen passed. No live Store or Steam installation changed.

`../dist/qa-panel-perf-video-1310-final/report.json` passed 61 video interactions
and 31 screenshots, including damaged decode with/without an old background,
large videos, hidden parent iframe, upload cancellation, clearing, resource
release, all eight locales, desktop/mobile and light/dark. Render and layout
failures were zero. Desktop fallback and narrow moving-video screenshots were
visually inspected. Noise in the generated moving fixture is intentional.

The timing comparison uses the existing `forever_companion_1.3.9.neko-plugin`
archive as the old source, the same isolated Hosted runtime and a simulated
200ms delay per RPC. Three old first-frame samples were 661.7/598.8/600.1ms;
three new samples were 418.1/362.9/357.6ms. Medians were 600.1 and 362.9ms.
The old path created two video elements and made a second metadata RPC; the
new path created one and reused the initial response. Timing starts in the
child document and ends at the displayed player's first frame callback.
It excludes real Steam panel creation and production SDK storage latency.
Reports: `qa-panel-perf-measure-baseline-139` and
`qa-panel-perf-measure-1310-final`, both under `../dist`.

The import-test package is
`../dist/forever_companion_1.3.10_panel_performance.neko-plugin`.
Final Python regression passed 972 tests with 38 existing explicit skips.
`qa-panel-perf-review-1310-final/report.json` passed 45 additional interaction
checks and two screenshots on the final UI source, including ordinary settings,
gallery selection, cancellation and direct playback recovery. Hosted typecheck,
32-module linking and scoped Ruff passed. The official isolated build passed
strict validation, ZIP CRC, payload hash and comparison of 77 source files.
The package is 679,061 bytes with SHA-256
`cc727ffdf2491cc5c4004aa4690aa74733aecc89c71a50bd4203f35baa127505`.
The host Git worktree remained clean.

Steam import, real operating-system window switching, GPU artifacts and the
user's subjective opening latency still require a local test of this package.

## 1.3.9 Manual Diary Timeout Fix

This acceptance supersedes the initial 25-second manual deadline below.
Each manual model attempt can now wait up to 45 seconds, with at most two
attempts and a 90-second total deadline. Pending manual writes are consumed by
the independent SDK `diary_write_tick` timer. The host runs each interval timer
in its own thread and event loop, so slow manual generation does not hold up
the regular message and mood-analysis timer. Automatic My Diary defers while
manual writing is pending or active and keeps its existing thresholds afterward.

The 2026-10-13 live logs show two separate failures: HTTP 400 on `host|summary`
at 10:41:21 and 10:41:31, followed by circuit suspension; the personal manual
request accepted at 10:49:09 then failed at 10:49:37 without a transport exception
line. The latter is consistent with the previous outer deadline, but the old
logs cannot prove the precise timeout source. My Diary's success at 10:49:52
was an automatic threshold write, not a second button request.

Explicit transport or outer-deadline timeouts now return `request_timeout`
without incrementing rejection failures. Regression cases cover a full first
timeout followed by a successful second attempt, the total deadline, retained
material, main-timer responsiveness, wrapped urllib timeouts, host cancellation
cleanup and redacted diagnostics. Success logs identify manual/automatic writes;
failure logs report the route, attempt, elapsed time and reason without content.

The final Python suite passed 938 tests with 38 explicit skips. Ruff 0.12.4,
hosted linking (32 modules, zero remaining bare exports), the host-supported
typecheck and `git diff --check` passed.
`../dist/qa-manual-diary-timeout-139/report.json` recorded 85 passing interaction
checks and 56 screenshots with zero check, render or layout failures. Both
manual paths exercised the new timeout feedback in all eight locales at
1280px and 390px. Chinese desktop and narrow screenshots were inspected.

The build target is
`../dist/forever_companion_1.3.9_manual_diary_timeout.neko-plugin`.
This is a local import-test update: no host code, production Store or
installation is changed, and no release or tag is created. Production model
generation has not been retested with this fix.

```powershell
uv run --frozen python -m pytest tests -q -p no:cacheprovider
node tools/check_hosted_link.mjs .
node tools/hosted_ui_harness.mjs --diary-only --typecheck --out ../dist/qa-manual-diary-timeout-139
uv run --frozen python tools/build_import_package.py --host '../N.E.K.O' --output '../dist/forever_companion_1.3.9_manual_diary_timeout.neko-plugin'
```

## 1.3.9 Manual Diary Generation (Initial Acceptance)

Personal Journal's manual button now queues a first-person entry when a complete
recent exchange or fresh Time Diary material is available. My Diary still needs
at least ten interaction turns for manual generation. Neither manual path asks
the character to choose whether to write. Automatic journal invitations remain
voluntary. Manual attempts share a 25-second model deadline, with at most two
calls through the existing review channel, budget and circuit breaker.

On 2026-10-13, the complete Python suite passed 929 tests with 38 explicit skips.
Regression cases cover successful writes, retries, timeouts, retained material,
save rollback, character isolation, titles, archive/restart, duplicate requests,
tool contention, capability changes, budget exhaustion, circuit changes and
private-content-free exception logs. Ruff 0.12.4 and `git diff --check` passed.
Hosted linking reported 32 modules and zero remaining bare exports; the
host-supported typecheck passed.

`../dist/qa-manual-diary-139-final/report.json` recorded 85 passing interaction
checks and 56 screenshots, including all eight locales at 1280px and 390px for
both manual buttons. These cases exercise acceptance, disabled writing buttons,
persistent specific failures, completion feedback and existing diary navigation.
There were zero check, render or layout failures. Chinese desktop and narrow
screenshots were inspected. The shared-panel regression at
`../dist/qa-mood-manual-diary-regression-139/report.json` also passed all 16 mood
checks and screenshots, including typechecking.

The official strict validator and isolated build passed for
`../dist/forever_companion_1.3.9_manual_diary.neko-plugin`: ZIP CRC and payload
hash verified, with 77 packaged source files matching the plugin directory.
No host source, production Store, Steam install, release or tag was modified.
The preceding mood commit `7813adc` is pushed; these diary changes remain local
for import testing. These tests use isolated fixtures and do not verify the
availability of the user's live model endpoint.

```powershell
uv run --frozen python -m pytest tests -q -p no:cacheprovider
node tools/hosted_ui_harness.mjs --diary-only --typecheck --out ../dist/qa-manual-diary-139-final
node tools/hosted_ui_harness.mjs --emotion-only --typecheck --out ../dist/qa-mood-manual-diary-regression-139
```

## 1.3.9 Emotion Analysis Interval

The emotion tab's tone-sensing module exposes a 10..3600-second integer input
with a 20-second default. It uses the shared save bar and only submits changed
fields. Backend regressions cover bounds, rejected fractions/non-numbers,
validation before other writes, Store reloads, preservation of existing custom
intervals and immediate changes to runtime throttling.

On 2026-10-13, `../dist/qa-mood-interval-139/report.json` recorded 16 passing
browser interaction checks and 16 screenshots across all eight locales at
1280px and 390px, with zero render or layout failures. The checks use the real
hosted runtime in an isolated sandbox iframe; saving submits only the interval,
and the five-second refresh preserves an unsaved interval.

The initial typecheck in that report failed because the isolated tooling
installed TypeScript 7, which is incompatible with the host checker. After
aligning only the temporary tooling with the host's TypeScript 5.9 dependency,
`../dist/qa-mood-interval-139-typecheck/report.json` passed typechecking.
Hosted linking and scoped Ruff passed; Python regression passed 891 tests with
38 skips. No host files or production Store data were modified. These checks
do not replace a Steam import test.

```powershell
node tools/hosted_ui_harness.mjs --emotion-only --out ../dist/qa-mood-interval-139
node tools/hosted_ui_harness.mjs --typecheck --typecheck-only --out ../dist/qa-mood-interval-139-typecheck
```

## 1.3.9 Heatmap Readability

The 2026-10-12 plugin-only import test uses a light-blue activity scale matching
the plain panel. Dark mode and wallpaper mode use a separate blue scale; colors
remain opaque so wallpaper pixels do not change the level encoding.
Positive-day quartiles are computed only within the displayed year window.
Equal counts share a color, zero-count days remain separate, and a very busy
outlier does not flatten ordinary activity. This is a relative per-window scale,
not a fixed count scale for comparing years.

Cells remain square at 11..15px with 3px gaps. Month labels share the calendar's
actual week widths, including 53-week years. Short boundary labels are omitted
when their one-week slot would overlap the next label; the date range and day
tooltips remain available. Narrow panels scroll within the chart without clipping
end-of-year dates. The first week leaves pre-relationship and previous-year
positions empty.

`../dist/qa-heatmap-139-blue/report.json` passed host-supported typechecking,
13 interaction scenarios and 13 screenshots with zero render or layout failures.
Cases cover light/dark, wallpaper, desktop/narrow, ordinary/leap full years,
year navigation, a two-day window, an October/November boundary, uniform counts,
outliers, empty data, month alignment and unchanged cell size during hover.
The complete Python suite passed 812 tests with 38 explicit skips, including
four new calendar and quartile regressions.

```powershell
node tools/hosted_ui_harness.mjs --heatmap-only --typecheck --out ../dist/qa-heatmap-139-blue
```

The browser extension preview was attempted separately; its screenshot did not
capture the opaque iframe content. Visual acceptance uses the verified existing
Chromium harness screenshots above. No production Store, host source or Steam
installation was modified. This version is a local import test, not a new GitHub
release; Steam import remains the author's acceptance step.

## 1.3.8 Release Numbering

The official release consolidates the local 1.4.0 through 1.5.0 testing work
under version 1.3.8, following the repository's published 1.3.7 release.
Runtime and media formats are unchanged from the final 1.5.0 acceptance below;
the older version numbers and report paths are preserved as test history.
`plugin.toml`, `pyproject.toml`, `uv.lock` and current documentation use 1.3.8.

## 1.5.0 Direct Playback Acceptance

The 2026-10-10 plugin-only acceptance passed 808 Python tests with 38 explicit
skips (historical Store writer cases and unavailable filesystem symlink cases).
Ruff 0.12.4 and `git diff --check` passed. The unchanged host remains clean.

The existing Blob and lifecycle regression run at
`../dist/qa-playback-150-video/report.json` passed 61 checks and 31 screenshots.
The direct run at `../dist/qa-playback-150-direct-final/report.json` passed
15 checks and 2 screenshots. Both have zero check, render or layout failures.
Host-supported typechecking and the hosted linker passed. The source hash is
`9ce6f30bb92595c1dc7b2548b7726b94be03f918ad433b4654dff6d0371f4dd6`;
31 runtime dependencies total 500,529 bytes, below the host limits.

Run the new cases with:

```powershell
node tools/hosted_ui_harness.mjs --direct-only --typecheck --out ../dist/qa-direct
```

The direct probe extracts the host's unchanged `plugin_ui_file` and MIME
functions without initializing its application, configuration, Store or
lifecycle. Starlette FileResponse serves only a temporary QA directory through
a loopback test adapter; this adapter is excluded from the package and is never
started by the runtime plugin. Requests verify MIME, 206 Range, invalid-range
416, traversal denial and the exact revalidation cache policy.

A real 49-MiB moving WebM plays in the unchanged `sandbox="allow-scripts"`
iframe without Blob assembly or chunk actions. Desktop and narrow contexts
each destroy and recreate the actual iframe twice. Context refresh keeps the
playing node; runtime direct-resource failure recovers through Blob. Missing
endpoints and forbidden absolute paths fall back; reduced motion never
requests direct preparation or video chunks. HTTP video correctly taints
canvas in this sandbox, so moving-pixel checks compare browser screenshots
and playback time without relaxing the sandbox or CORS.

An optional `--actual-video <mp4-path>` case copies an explicitly supplied asset
into the temporary test directory and removes it at completion. The author's
198,941,764-byte / 3840x2160 MP4 passed direct playback and actual iframe
recreation without chunk actions. Initial loading plus pixel checks measured
1,836 ms; recreation plus pixel checks measured 1,203 ms. These measurements
include a deliberate 470-ms observation interval and screenshots, exclude
the production SDK registration/copy path, and are not Steam timings.
No screenshots of the actual user video are persisted.

`benchmark_playback.py --asset <path>` separately runs the real pure-core copy
helper against a temporary SDK root, never a real Store. For that video,
complete verification and publication measured 304.85 ms; four subsequent
signature-checked reuses measured 19.42..20.22 ms. This excludes metadata RPC,
browser decoding and Steam routing. Originals and production media remain
unchanged; there is no transcoding or automatic migration.

Backend cases additionally cover opt-in/legacy behavior, SDK failures,
corruption before publishing, changed originals/copies, two-entry and byte
limits, total library quota, pending upload reservations, disk exhaustion,
reparse rejection, cleanup failure/retry, upload/policy cleanup and shutdown.
The archive verifier excludes `wallpaper_playback` regardless of suffix.
The real Steam SDK, decoder and import remain the author's acceptance step.

## 1.4.9 Reopen Recovery Verification

The 2026-10-10 run at `../dist/qa-video-reopen-149-full/report.json` passed
61 interaction checks and 31 screenshots, with zero check, render or layout
failures. Host-supported typechecking and the 32-module Hosted linker passed.
The UI source hash is
`1c552ce69c9374b2604f74e9f9a13a044b82d87daa8d0ef994e2ddf547c1d12b`;
31 runtime dependencies total 499,315 bytes, within the host limits.

The new lifecycle test destroys and creates the opaque iframe document twice,
restores the saved video through the fixture backend, holds its first batched
reply, and verifies recovery and moving pixels before releasing the late
original reply. The playback test injects exactly one transient AbortError
into an already-decoded video's play request, then verifies actual Chromium
playback resumes without a permanent cover/error state. The injected
interruption is not a claim of reproducing an operating-system transition.
Existing real iframe CSS hiding, looping, 49-MiB downloads, resource cleanup,
decoder failure, reduced motion, directory imports and eight locales pass.
For only these two new checks, add `--video-recovery-only` to `--video-only`.

The complete Python suite passed 790 tests with 37 explicit skips. Five new
transport cases first failed on the previous implementation, then passed:
lost batch reply recovery at the one-second duplicate delay, and malformed
duplicates with wrong index, short bytes, excess chunks or an empty batch.
All 43 transport cases now pass. Ruff 0.12.4 and `git diff --check` pass.

A separate read-only probe of the author's saved 198,941,764-byte video used
an isolated headless Edge opaque iframe and a complete Blob, matching the
plugin's native decoder setup. It decoded at 3840x2160, duration 15 seconds,
and advanced playback time. The file and production Store were not modified;
no live host API, installation, settings, diaries or chat records were accessed.
This rules out a decoder failure for that file in the tested browser, not in
every Electron/Steam build. The real Steam import and routing test remains
the author's final acceptance step.

`hosted_ui_harness.mjs` renders the real plugin panel using the host's current
Hosted TSX linker and UI Kit. It does not replace the panel with React or a
mock component library. `hosted_ui_fixture.mjs` provides deterministic backend
responses and test-only image pixels.

The harness only reads the host checkout. It never installs dependencies into
the host, calls its backend, writes its plugin directory, or touches a Steam
installation. These development tools are excluded from `.neko-plugin` builds.
They are not an alternative to the final Steam import smoke test.

## Dependencies

Use Node.js with `playwright`, `sucrase`, and optional `typescript` dependencies
installed outside both the host and the plugin. The harness searches:

1. `--deps <node_modules>` or `NEKO_UI_TOOL_DEPS`.
2. `%TEMP%\neko-companion-ui-tooling\node_modules`.
3. The existing bundled Codex Node dependency directory on this development PC.

For example, isolated tooling can be installed with PowerShell:

```powershell
npm install --prefix "$env:TEMP\neko-companion-ui-tooling" --no-audit --no-fund sucrase typescript playwright
```

The harness launches Edge headless when installed. Use `--browser-executable`
or `NEKO_UI_BROWSER` to select another Chromium-compatible executable. It uses
a fresh Playwright context and no personal browser profile or login session.

## Runs

Run commands from the plugin root:

```powershell
# 192 screens: four widths, two themes, three wallpapers, eight tabs.
node tools\hosted_ui_harness.mjs --full --typecheck --out "$env:TEMP\neko-companion-ui-full"

# Interaction and reading/empty-state checks without the main screen matrix.
node tools\hosted_ui_harness.mjs --interactions-only --gallery --compact --details --widths 1280,390 --out "$env:TEMP\neko-companion-ui-interactions"

# Wallpaper palette linking, fallback, caching, and narrow swatch checks only.
node tools\hosted_ui_harness.mjs --theme-only --out "$env:TEMP\neko-companion-ui-theme"

# Eight-locale guide, real introductions, diary states and confirmations.
node tools\hosted_ui_harness.mjs --copy-only --out "$env:TEMP\neko-companion-ui-copy"

# Animated principle demos, eight-locale comparison matrix and lifecycle checks.
node tools\hosted_ui_harness.mjs --demo-only --typecheck --out "$env:TEMP\neko-companion-ui-demo"

# Motion/control checks without the comparison screenshot matrix.
node tools\hosted_ui_harness.mjs --demo-only --demo-dynamic-only --out "$env:TEMP\neko-companion-ui-demo-motion"

# Complete delivery run, including wallpaper theme regressions.
node tools\hosted_ui_harness.mjs --full --typecheck --interactions --gallery --compact --details --theme --calendar-diagnostics --copy --out "$env:TEMP\neko-companion-ui-delivery"

# Typecheck only, using a temporary copy of the host's supported scaffold.
node tools\hosted_ui_harness.mjs --typecheck --typecheck-only --out "$env:TEMP\neko-companion-ui-types"

# Read-only tooltip overflow diagnosis in two widths and two locales.
node tools\hosted_ui_harness.mjs --diagnostics-only --calendar-diagnostics --out "$env:TEMP\neko-companion-ui-calendar"
```

`--plugin-root` and `--host-root` override the sibling-checkout defaults.
Screenshot selection supports `--widths`, `--themes`, `--wallpapers`,
`--pages`, and `--locale`. Pages are `overview`, `calendar`, `diary`, `moment`,
`features`, `cycle`, `mood`, and `settings`. Wallpaper fixtures are `none`,
`bright`, and `dark`.

`--interactions` adds behavioral checks to a screen matrix. `--gallery` adds
gallery keyboard, preview/applied, upload, delete, zero-value styling, unified
save, delayed-image, and broken/failed-decode image tests. `--compact` adds
390x600 save-button and review-seal label-fit checks in all eight locales,
plus both books' footer/savebar non-overlap and pointer-hit checks across
1280/390 widths, 900/600 heights, and
light/dark themes. It also checks clearance and footer pointer hits after the
real five-second refresh and tab roundtrip, including observer cleanup on a
tab without a save bar. `--details` adds journal/review shelf and reading
screens, capability introduction screens, and empty diary screens.
`--compact-only` runs just the compact/footer checks without the main matrix
or general interaction suite.
`--theme` adds 34 theme checks. `--theme-only` runs these checks
without the main matrix. The suite uses olive/blue, rose/blue, gray, and white
pixel fixtures to verify linked primary/secondary CSS colors, default-blue
fallback, atomic image/theme transitions, cache reuse across polling and
filter edits, unchanged appearance-save fields, and deleted/reused image
request identity. It also measures colored-control contrast and keyboard
focus with zero mask/card opacity and checks swatch fit in all eight locales.
Four additional no-wallpaper checks measure actual composited text contrast
at rest and on hover, keyboard focus visibility/contrast, and the separation
of soft fill colors from readable ink in both themes and two widths.
`--calendar-diagnostics` measures calendar overflow with and without tooltip
boxes in the isolated DOM and captures the actual hovered tooltip.
`--copy` checks all six onboarding steps and all ten real introductions in
eight locales, two widths and both themes. It also checks diary empty states,
optional writing invitations, generated-review states and results, dependency
interpolation, master-switch confirmation/cancellation, destructive-operation
cancellation, and skipping the guide. `--copy-only` skips the base matrix;
`--copy-locales zh-CN,en` limits just this suite during development.
`--demo` adds the principle demo suite; `--demo-only` skips the base matrix.
The default demo matrix covers ten real capabilities, both comparison modes,
eight locales, two widths and both themes. Reduced-motion/manual-final-step
screenshots test reading and fit, not animation. Separate no-preference checks
verify visual motion, pause/resume/replay, steps, independent comparison modes,
journal choices, the privacy route, refresh persistence, runtime motion
preferences, real offscreen intersection and unmount cancellation.
`--demo-locales zh-CN,en` limits the screenshot locales; `--demo-static-only`
and `--demo-dynamic-only` select the respective phase. The explicitly named
synthetic child-document visibility test exercises event handling only; it
does not claim a real operating-system background transition.

Introduction fixtures come from the real `core/capabilities.py` and
`core/intros.py` declarations, loaded with Python's `runpy`, not placeholder
prose. The harness uses the plugin's development Python when present, or
`python` on PATH. This reads pure data modules only; it does not import or
start the plugin backend.

## Results And Limits

Every completed run writes `report.json` alongside its PNG screenshots. The
report includes the real source hash, runtime import graph and byte budget,
layout diagnostics, browser/runtime errors, interaction results, and optional
host-supported typecheck output. Failed interaction, typecheck, runtime, or
layout checks return a nonzero exit code. Content/document horizontal overflow
and clipped text are part of the layout gate.

The fixture uses the real sandboxed iframe and `postMessage` API transport, but
its backend responses are intentionally synthetic. Host file permissions,
Steam startup, persisted user settings, real LLM behavior, and production
image upload validation require separate integration or user smoke tests.
Layout diagnostics can flag an intentionally scrollable or off-screen tooltip;
review screenshots before classifying these as visible regressions.

## 1.3.5 Delivery Verification

The frozen delivery run completed on 2026-10-03 with 216 reported base/detail
screenshots, 85 passing interaction checks, four passing calendar diagnostics,
and a passing host-supported typecheck. Additional interaction/theme captures
bring the actual PNG count to 281. No render or interaction failures were
reported. The import graph contains 25 dependencies / 367,967 bytes, below
the host limits of 32 dependency files / 512 KiB.

UI source hash:
`c55405b35d5960a347c12e5a15ab2063f64f148a851a772f481993e9f7868efc`.
The plugin's separate Python regression suite passed all 522 tests.
These are isolated local checks, not a claim of Steam import or real-user-data
verification.

## 1.3.6 Delivery Verification

The 2026-10-03 delivery run used the new wallpaper defaults: blur 0, mask 0.4,
brightness/saturation/contrast 100, glass 0, card opacity 0, and text strength
100. It explicitly checked all eight slider values and the computed mask,
glass, and surface variables. The no-wallpaper checks verified that neutral
card backgrounds remain enabled while the theme stays blue. The gallery
roundtrip first saved nonzero glass/opacity, then verified returning both to
zero, so an already-zero default cannot make the save check vacuous.

The run passed 216 reported screenshots, 85 interaction checks, four calendar
diagnostics, and the host-supported typecheck, with 281 actual PNG captures.
The Python suite passed 527 tests. Dependencies total 25 files / 367,952 bytes.
UI source hash:
`6e8161bc78af2cfb21ca7133bbfe0df2bf7212b8b6cf4967208b68d2e541b482`.
These checks do not overwrite saved user appearance settings or replace a
Steam import smoke test.

## 1.3.7 Delivery Verification

The frozen 2026-10-03 delivery run passed 216 reported base/detail screenshots,
89 interaction checks, four calendar diagnostics, and the host-supported
typecheck, with 289 actual PNG captures. No render or interaction failures were
reported. All four new no-wallpaper light/dark and wide/narrow checks passed:
ordinary and hovered text contrast is at least 4.5, and keyboard focus contrast
is at least 3. The soft control fills remain separate from readable ink.

The existing wallpaper extraction, fallback, zero-opacity contrast, cache,
image-race, gallery, save, footer, and eight-locale checks remain passing.
Wallpaper defaults and extraction logic are unchanged from 1.3.6.
The Python suite passed all 530 tests. Dependencies total 25 files /
370,416 bytes, below the 32-file / 512-KiB limits.
UI source hash:
`8d09e385b03ade067006fe43dca0276643bb1552923c3a3c120c8cefec75665c`.

The official 1.3.7 package inspection verified the payload hash and ZIP CRC.
All 69 packaged source files match the plugin checkout byte-for-byte,
including 28 UI files and eight locales. No database, user-data directory,
test, tool, dependency directory, or cache is packaged. The host checkout is
clean, the isolated test processes have exited, and the previous 1.3.6 package
is unchanged. These isolated checks do not claim Steam import or production
user-data verification.

## Final 1.3.3 Release Number

At the author's request, the final public version is 1.3.3. The 1.3.4 through
1.3.7 headings above identify local development and import-package milestones,
not GitHub releases. Version metadata and the README were normalized without
changing UI source. The final pre-release run passed all 530 Python tests,
the CI-equivalent Ruff check, the official plugin check, the 26-module link
check, and the isolated host-supported typecheck. The frozen UI source hash
and full browser regression results above remain applicable.

## Unreleased Clear-Copy Verification (1.3.3)

The final 2026-10-03 run passed 528 reported screenshots, 201 interaction
checks, four calendar diagnostics, and the host-supported typecheck. Including
interaction captures, the output contains 601 PNG files. Check, render, and
layout failures are all zero. Content/document horizontal overflow and clipped
text are included in the layout gate.

The copy suite covers all six guide steps and ten real capability introductions
in eight locales, both themes and 1280/390 widths. It also covers diary empty
states, optional journal invitations, generated-review progress/results,
dependency interpolation, master-switch and destructive-operation cancellation,
and skipping onboarding. Narrow feature layouts were manually inspected after
fixing the grid's minimum width for longer Spanish, Portuguese and Russian text.
Wallpaper, gallery, compact reading/save bars, and existing theme checks remain
passing.

Dependencies total 25 files / 368,647 bytes, below the 32-file / 512-KiB limits.
UI source hash:
`39b6b7bbb5156ccef7b12a34449fba7572a53371e40d2e893a16ab9a084fbca4`.

The Python suite passed all 557 tests, including 27 copy-contract cases. Ruff,
the official plugin check, and the 26-module link check passed. The official
check's uncommitted-worktree warning is expected for this local test delivery.

Package: `forever_companion_1.3.3_clear_copy.neko-plugin`, 583,895 bytes /
72 entries. Official payload hash and ZIP CRC verification passed. All 67
packaged source files match the checkout byte-for-byte: 28 UI files, 23 Python
files, eight locales, and the remaining manifest/configuration/documentation
files. No user data, tests, tools, dependencies, or caches are included.
SHA-256:
`87ec474bc08472646407f2769804c57e79aee2b7f7e87067793623ad72e6edce`.

Runtime Python, manifest settings, permissions, tool descriptions and metadata
were separately checked for unchanged behavior. Guide version remains 2, so
existing users are not forced through onboarding again. The host checkout and
the original public 1.3.3 package remain unchanged. These are local fixture and
package checks, not Steam import, persisted-data, or real-model verification;
no commit, push, release, or market notification was performed.

## 1.3.4 Release Promotion

The author approved the clear-copy delivery for public version 1.3.4.
Only version metadata, the README's current-version note, and release records
change during promotion. The UI source is unchanged, so the frozen 528-screen /
201-interaction browser results and source hash above remain applicable.
The 1.3.3 clear-copy package remains a historical local test artifact and is not
overwritten; the public 1.3.4 package is rebuilt from the release commit.

The repository's reusable GitHub release workflow validates, tests, builds and
publishes a GitHub Release. Its market-release check enforces repository/tag
conventions and writes evidence only; no plugin-market registration, submission,
or notification endpoint is invoked.

After the version change, all 557 Python tests passed again, along with the
CI-equivalent pinned Ruff check, official strict plugin check, isolated
host-supported typecheck and 26-module link check. The UI source hash remains
`39b6b7bbb5156ccef7b12a34449fba7572a53371e40d2e893a16ab9a084fbca4`.

## Public 1.3.6 Feature-Introduction Release

The author approved the 2026-10-04 feature-introduction redesign for public
version 1.3.6. The older 1.3.6 wallpaper heading above describes a local
development milestone, not this public release. Promotion changes version
metadata and release documentation only; the accepted UI and copy stay frozen.

The completed isolated browser runs passed 508 screenshots and 1,257 checks
across eight locales, ten capability introductions, light/dark themes, wide and
narrow viewports, intermediate breakpoints, and short windows. There were no
render, clipping, overlap, horizontal-overflow, or interaction failures.
Demo animation and controls, privacy/journal choices, refresh preservation,
unmount cleanup, and absence of business mutations were also verified.

After the version change, all 586 Python tests passed again, along with the
CI-equivalent pinned Ruff check, official strict plugin check, isolated
host-supported typecheck, and 29-module link check. The frozen UI source hash is
`863553c29f42848c9aaa1fa3e250e41b5d4ad1d403b108680d2467f2698a17e2`.
The runtime graph contains 28 dependencies / 452,543 bytes, below the host's
32-file / 512-KiB limits. These fixture checks do not claim a Steam import or a
production-data/real-model test. Only the plugin repository is changed.

The reusable verify/release workflows and host checkout are pinned to the
audited host commit `9cdc4dfce7ae3203673ad4846a02ae1c7fbe8ad0`.
Their sync/check/build path does not call the CLI publish/market-notify path;
the market-release flag validates repository, tag and version conventions and
writes evidence only.

The prior `forever_companion_1.3.5_feature_intro.neko-plugin` remains an unchanged
local test artifact. A new 1.3.6 package is built from the release commit.
Publication is limited to GitHub; plugin-market submission and notification
remain the author's responsibility.

## Public 1.3.7 Appearance-Disclosure Release

The author approved the default-collapsed appearance adjustments for public
version 1.3.7. The gallery, import controls, theme swatches, save feedback and
revert control remain outside the native disclosure. All ten fields, eight
sliders, original ranges, disabled rules, draft callbacks and save schema are
preserved. Native open state survives ordinary draft updates and hosted refresh.

The accepted final UI passed 172 screenshots and 329 interaction checks:
the eight-locale matrix contributed 136 screenshots / 192 checks, the
390-by-600 short-window matrix contributed 36 screenshots / 48 checks, and
the gallery/theme/compact regression contributed 89 checks. There were no
render, clipping, overlap, horizontal-overflow or interaction failures.
The last slider's real hit test confirms it is not covered by the sticky
save bar after scrolling. A scoped scroll margin reuses the measured save-bar
clearance rather than changing shared layout.

After the version change, all 590 Python tests passed again, together with
CI-equivalent Ruff 0.12.4, official strict plugin check, isolated host-supported
typecheck and the 29-module link check. The frozen UI source hash remains
`32a97ac311460bf9a94d2dd014f6a280a3420e79d583fef1632672da3fb4251d`,
matching all three final browser reports and the release typecheck report.
The runtime graph has 28 dependencies / 453,624 bytes, below the host's
32-file / 512-KiB limits. These checks do not claim a Steam import or a
production-data/real-model test.

Version metadata, README and release records are synchronized to 1.3.7.
The previous 1.3.6 release and appearance test packages are preserved.
The existing audited GitHub workflows remain pinned to host commit
`9cdc4dfce7ae3203673ad4846a02ae1c7fbe8ad0`. Publication is limited to
GitHub: no plugin-market registration, submission or notification is invoked,
and no host source is changed.

## 1.4.0 Dynamic Wallpaper Import Package

This is a local import-test delivery, not a public release or Steam installation.
Only the standalone plugin is changed. The host and reference checkouts remain
read-only; fixture checks do not read or mutate production Store, diary or chat data.

The frozen source hash is
`abe416f2256f46edd4cd84add87cd29b4c5a22e503bd0772dfab8b0424ef5ca8`.
There are 30 runtime dependencies / 477,656 bytes, within the host's 32-file /
512-KiB budget. The 31-module linker, isolated Hosted TSX typecheck, Ruff,
official strict source check and all 638 Python tests passed.

The matching browser reports are `out/video-final/report.json` and
`out/wallpaper-regression-final/report.json`: 23 video screenshots / 38 checks,
plus 136 appearance report screenshots / 281 image/gallery/theme/compact and
eight-locale appearance checks, with no check, render or layout failures.
The latter run produces additional diagnostic PNG files beyond its report count.
The final `out/video-verified/report.json` repeats the video suite at the same
source hash and adds hard assertions for visible video opacity and coverage
of all four iframe viewport edges. Its 23 screenshots / 38 checks also pass.

Video QA uses Chromium canvas capture and MediaRecorder, not a fabricated
video element or encoded still. The 2,076,500-byte / 101-frame stress WebM
exercises three production-sized chunks and byte-level upload/download hashes.
A separate 44,817-byte / 101-frame landscape fixture provides clear screenshots.
Checks cover moving decoded pixels and playback time, looping, filters, save,
revert, remount, five-second refresh without resetting the video node,
document and real parent-iframe hiding, motion and reduced-motion changes,
malformed acknowledgments, cancelled uploads, authorized WE folder selection,
deletion and URL cleanup, actual decoder errors and poster/thumbnail fallback.
Generated fixtures and screenshots are ignored and excluded from the package.

Reproduce the isolated checks from the plugin directory:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q
ruff check .
node tools/check_hosted_link.mjs .
node tools/hosted_ui_harness.mjs --video-only --typecheck --out tools/out/video-final
node tools/hosted_ui_harness.mjs --interactions-only --gallery --compact --theme --appearance --out tools/out/wallpaper-regression-final
```

`tools/build_import_package.py` uses the host's official builder and archive
validators while routing runtime/config/log paths into a unique temporary tree.
Its build-only `sitecustomize` prevents Windows Documents probes and automatic
legacy migrations in both the parent and inherited metadata worker. It does not
modify host files or run plugin lifecycle hooks. Existing output is never overwritten.

The output is `../dist/forever_companion_1.4.0_dynamic_wallpaper.neko-plugin`
(643,572 bytes / 79 entries). ZIP CRC and official payload hash verification
passed, and all 74 packaged source files match the plugin workspace byte for byte.
The generated handler metadata is present. Tests, tools, caches, databases and
user media are absent.

Package SHA-256:
`a5ee40b19e4a9634c661e5f7f614b517804e70d72ab3e0a50486fbf7bd0f637c`.
Payload SHA-256:
`043fb2398c806bb7760fc12c66d6d7d69a3b1543974bb81a882730427c4094b7`.

Actual Steam import, codec support for the user's specific MP4/WebM files and
production persistence remain the author's manual acceptance step.

## Chunk Receipt Recovery Checks

The isolated video fixture advertises `chunk_receipts: true` and supports the
read-only `video_status` operation. New browser checks write the first real
768-KiB media chunk normally, then deliberately drop its ACK or deliver it
after 3.5 seconds. They require the real frontend to query its receipt within
three seconds, finish before the delayed ACK, cancel the primary waiting
request, write each chunk once, and publish exactly one gallery item.
Delivering the late ACK afterward must not advance or publish the upload again.

Separate cancel and clear races delay an already accepted status response.
After the real cancellation or clear confirmation, that late response must
not send another chunk, commit the upload, or resurrect gallery data. Both the
primary request and receipt probe must be cancelled. These checks preserve the
existing upload, playback, resource-release, and locale/layout cases.

This models a missing bridge reply, not its cause. It does not reproduce the
compiled Steam host's IPC stack or establish why a production ACK disappears.

## Video Playback And Local File Reuse Checks

The isolated browser suite checks that first selection of a newly imported
video performs zero `get_gallery_image` calls, including metadata: it must
play the retained original File with real decoded moving pixels. The real
49-MiB fixture is then saved and remounted, and must read every stored chunk,
preserving full download coverage rather than bypassing it with the cache.

For a saved video, the fixture holds the first read reply until the real
metadata poster, loading status, enabled import control and clear-dialog
cancellation have been inspected and captured. Releasing the reply must finish
moving playback and dismiss progress. Switching to an image before release
must neither resume old chunk requests nor revive the old background.

Resource probes check that exactly one unselected imported File URL is kept.
A second import must revoke its predecessor, while first selection of the
newest pending File still uses zero host reads. Another wallpaper selection,
clear, deletion and panel unmount must release owned URLs. Clear and unmount
also receive a held old background reply after cancellation and must not
resume its download or resurrect the background.

These are isolated fixture assertions, not evidence of a Steam run, a
production pass or codec support for every user's MP4/WebM. Browser report
results must be recorded separately after the frozen production source run.

## 1.4.6 Video Display Verification

The frozen-source run completed on 2026-10-10 at
`../dist/qa-media-146-verified/report.json`: 59 interaction checks and 29
screenshots passed, with zero check, render or layout failures. The
host-supported typecheck passed without changing the host. Python regression
passed 776 tests with 37 explicit skips; Ruff and hosted linking passed.

The six metadata, chunk-read and real decode failure cases require loading
progress to disappear. When a different usable old background exists, they
strictly require it to remain unchanged; without one, they require the known
poster or thumbnail. Native import selection uses zero metadata/chunk reads.
Pending File replacement, selection, clear and unmount release owned URLs;
held late reads cannot continue or revive the background after cancellation.

The source hash is
`1a4245d2efcbb5c11eb4df04712eb67f90e1d4614ea91c85e6d195a9403e9dc8`.
There are 31 dependencies / 495,652 bytes, within the host's 32-dependency /
512-KiB limits. These are isolated browser and filesystem checks, not a Steam
run. Reopening still assembles the complete video before playback; this is
not streaming and does not guarantee every user's codec support.

## 1.4.7 Video Read-Hedge Verification

The frozen-source run completed on 2026-10-10 at
`../dist/qa-video-reopen-147/report.json`: 59 interaction checks and 29
screenshots passed, with zero check, render or layout failures. The
host-supported typecheck passed and confirmed the host was untouched. The
source hash is
`33d5c4f68857e949b210bef9edc8f7019e18f6ef9d07f1100532c714ba5646f7`.
There are 31 dependencies / 498,045 bytes, below the host limit.

The Python regression suite passed 783 tests with 37 explicit skips; the
focused video transport and timeout contracts passed 37 tests. These checks
cover a delayed primary chunk response recovered by one same-chunk read after
one second, strict validation and loser cancellation, bounded timeout/retry
budgets, and cancellation races. The browser suite preserves the existing
poster/progress, full-download, cleanup, playback and locale checks.

This remains a bounded mitigation for a delayed Hosted response. It does not
keep a Blob across a destroyed iframe, add streaming or Range requests, change
the host, or guarantee every codec and production bridge condition.

## 1.4.8 Video Batch-Read Verification

The frozen-source run completed on 2026-10-10 at
`../dist/qa-video-reopen-148-batch2/report.json`: 59 interaction checks and 29
screenshots passed, with zero check, render or layout failures. The
host-supported typecheck and 32-module hosted linker check passed; the source
hash is
`ac1fddf6b32fcebc7422b5ca214bc208d2c42913f502adf59d225ee65409338d`.
There are 31 dependencies / 498,818 bytes, below the host limit.

Reopening a saved video now requests at most four consecutive chunks per
`get_gallery_image` call. The synthetic 49-MiB fixture verifies that all chunks
are covered in bounded batches (17 calls for 66 chunks), including the final
short batch, while the browser still reconstructs and decodes the complete
Blob. The fixture also exercises the legacy single-chunk reply path, poster and
loading state before a delayed reply, cancellation, cleanup, decoder failure,
moving pixels, looping and all eight locales.

The Python regression suite passed 785 tests with 37 explicit skips. This is
still a Hosted round-trip reduction, not HTTP Range streaming or persistent
cross-iframe caching; the first seconds after reopening may show the verified
poster while the full Blob is assembled. Steam import and codec support for
the author's files remain manual acceptance steps.

## 1.4.2 Persistent Media Storage Acceptance

This local import-test version moves only new videos to the plugin's SDK data
directory (`self.data_path("wallpapers")`). Neither a C-drive path nor the plugin
installation directory is a storage contract. Existing Store videos remain
readable without an automatic first-start migration. The host and Steam are
read-only throughout local acceptance.

Default limits are 256 MiB per video and 2 GiB per video library; configurable
ranges are 32..512 MiB and 64..16,384 MiB. Reads have a separate 512-MiB hard
limit and use 768-KiB chunks. Inventory, in-progress reservations, staging and
pending reclaim are accounted for, with 64 MiB reserved on the destination
volume. Lowering a budget must not evict existing media.

Playback still assembles a complete Blob through the existing Hosted actions.
This version does not expose a static directory, add a media server, relax the
opaque-origin sandbox or claim HTTP Range streaming. Large-video preparation
time and browser memory remain manual acceptance considerations.

Required isolated cases:

- Read pre-update Store video, preserve old indexes and appearance, then read
  and write file-backed video after repeated reloads without touching diaries.
- Interrupt upload, file promotion, manifest publication and deletion; recover
  only a complete published asset or a tracked, reclaimable unpublished file.
- Open the panel from a second plugin instance while an upload is still owned by
  a live process; the `.part` file and recovery record must remain available.
  Simulate a dead owner or expired TTL separately and verify that cleanup reclaims it.
- Remove a published `.bin` outside the plugin and verify startup fails closed with
  `video_read_failed` while preserving the index, manifest and recovery metadata.
- Reject invalid manifests, unknown future schema, path traversal and redirected
  media roots without rewriting valid media state or escaping the data root.
- Enforce actual policy after updates, malformed responses, disk exhaustion and
  quota reduction; refuse oversized reads rather than loading without bounds.
- Confirm and cancel full wallpaper cleanup. Confirmed cleanup removes gallery
  media, legacy `panel_bg`, covers, upload staging and browser media cache;
  other settings, diaries and relationship statistics remain unchanged.
- Retain and display `cleanup_pending` when logical/file deletion fails.
  Releasing a Windows file lock and retrying should finish cleanup.
  `storage_reclaim_pending` separately explains that the SQLite file may retain
  free pages after successful logical deletion; it must not falsely report an
  undeleted wallpaper or ask users to close an application to shrink SQLite.
  Do not claim free-page reclamation, OS-cache purge or secure physical erasure.
- Preserve GIF / SVG and animated WebP / APNG instead of flattening through
  Canvas. Run all eight locales with actual policy values and narrow layouts.
- Verify package CRC, official payload hash and byte-for-byte source match.
  Reject runtime `data/wallpapers/temp/staging/backup/published` paths as well as
  media/database suffixes, even when a runtime file has an innocuous extension.

## 1.4.2 Implementation Freeze

The final 2026-10-04 local acceptance run passed 717 Python tests with 37
explicit skips (one platform-specific filesystem case and historical
Store-writer tests superseded by the file-backed suite). Ruff, `git diff
--check`, the official strict source check, and the hosted-link check passed.

The final video run is `tools/out/video-final-1_4_1/report.json`: 24
screenshots and 45 interaction checks, including a real decodable WebM larger
than 48 MiB and more than 64 chunks, policy updates, confirmation/cancellation
cleanup, browser Blob URL release, animation-container preservation, and all
eight locales. The appearance regression run is
`tools/out/wallpaper-regression-final-1_4_1/report.json`: 136 screenshots and
281 interaction checks. Both reports have zero check, render, and layout
failures. The hosted UI source hash is
`c4c086f7903dd215e7543d8cc485e005e75f5edbcb33847ab367453d48afd9a9`.
The runtime graph contains 30 dependencies / 485,503 bytes, below the host
limit of 32 dependency files / 512 KiB.

The verified import package is
`../dist/forever_companion_1.4.2_stability_patch.neko-plugin`
(661,859 bytes / 80 entries). Official strict validation passed; ZIP CRC and
payload hash verification passed; all 75 packaged source files matched the
workspace byte-for-byte. Package SHA-256:
`9189903d8f8384dfbaec7a37bd24141a20f4fc80a049382cc29814e0d09dc725`.
Payload SHA-256:
`6845e6f43f5cc69516f9c13710cad113dc1c8fa2a5b226e79b7e761d707015c4`.
The archive contains no tests, tools, caches, databases, runtime media, or
private user data. These remain isolated local checks; Steam import, the
user's codec support, and production persistence are still the user's manual
acceptance step.
