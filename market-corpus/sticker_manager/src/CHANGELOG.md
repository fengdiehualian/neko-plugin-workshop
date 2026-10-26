# Changelog

## Unreleased (2026-10-18)

- 爱发档增加“补图触发轮数”面板滑块，可保存整数 0–10，默认 1；0 每轮帮选，1–10 连续相应轮数完整回复未配图后独立选图。
- 参数保存后立即生效并取消旧选图任务，重启后保留。同一回复最多判断一次，没有贴切候选时跳过，后续新回复可再次判断。
- 保留已有成功/待处理发送的避让、发送冷却、概率、最近去重、纯文字要求及过期取消；自然与矜持基线不变。

## 0.9.5

- 按维护者指定的正式版本号，汇总下方 `0.9.5+test.20261017` 至 `0.9.14+test.20261017` 的测试改动；历史测试版本与实测记录保留原编号。
- 发送工具预先展示完整说明的快捷候选，贴切时直接选 ID，减少查图往返；补充空候选原因与同轮回复指纹诊断，不记录聊天原文。
- 固定矜持与自然的实测策略基线；爱发保留自然的完整指引，并在连续两轮未配图后通过显式绑定的文字模型独立选图。
- 补图只提交图片，不续写猫娘正文；保留发送限制，并在新输入、会话、配置或图库变化时取消过期工作。
- 绑定后的实测窗口有 19 轮完整回复、5 次发图提交，其中 4 次来自独立补图。选图结果校验失败仍为已知限制，先前重复回复根因尚未确认。

## 0.9.14+test.20261017

- 爱发档保留自然档完整提示策略，并在连续两轮未配图后，对最新完整回复独立执行选图判断。通过宿主模型网关使用可选的 `eager_attachment` 文字模型绑定，不复制宿主凭据或自动换用其他模型。
- 补图只提交图片、使用 blind 通道，不续写或重发猫娘正文。已有工具发送或待发图时跳过；新输入、会话变化、切档、关闭插件、过期回复及图库变化会取消或拒绝提交。
- 只接受候选中的完整 ID；保留发送冷却、概率与最近去重，遵守纯文字要求和严肃任务，不为满足轮次随意选图。增加补图诊断及模型未绑定/不可用提示。
- 自然与矜持的完整提示基线保持不变。10 月 17 日绑定后的实测窗口有 19 轮完整回复、5 次发图提交，其中 4 次来自独立补图；仍有选图结果校验失败，尚不保证每两轮必发，也不代表先前重复回复根因已修复。

## 0.9.13+test.20261017

- 爱发档恢复自然档的完整策略，包括默认配图偏好、触发判据、快捷候选说明、日常问答示例、匹配规则与查图说明，只增加“上一轮发过，本轮有贴切的新图仍优先配一张”的偏好。
- 自然档和矜持档的完整基线保持不变；候选生成、提醒节奏、发送限制、正文顺序与工具回执不变，保留重复回复诊断。
- 基线回归验证爱发档除新增一句和档位名外与自然档一致，并覆盖自然→爱发→自然切换。此版需实机观察发图频率，不代表重复回复根因已修复。

## 0.9.12+test.20261017

- 增加工具进入/返回的回复标识指纹与配文长度日志，用于区分同轮重复调用和工具后续答；不记录宿主 capability token 或聊天原文。
- 增加独立、需显式接入的本地 HTTP 观察器与离线报告工具，用原始模型输出的指纹核对工具前后重说和历史丢失；不改写请求或回复内容，不自动更改宿主配置。
- 保留三档行为、原回复和发图流程。此版为诊断版，尚未声称修复重复对话；详见 `docs/reply-repetition-20261017.md`。

## 0.9.11+test.20261017

- Preserve the delivered 0.9.10 eager behavior as natural: both full tool
  descriptions, reminder wording, one-turn cadence, and everyday shortcuts.
  Generate an independent baseline fixture from the verified 0.9.10 package.
  Reserved remains at its 0.9.8 baseline; saved tier settings are not changed.
- Eager now prefers a fitting image on most casual replies. A previous
  illustrated turn is not itself a reason to skip a new fitting expression.
  Reduce repeated standing matching instructions without truncating captions
  or weakening recipient, action, stage and tone checks.
- Adapt the reference project's optional recent-dialogue quantity guidance and
  teaching examples to the existing direct-ID flow. No extra LLM call, random
  attachment, output-marker parser, ordinary-text push or host code edit.
- Keep speech-first order, silent receipts and all sender gates. The local
  0.9.10 sample was five confirmed images in 23 complete replies; this does not
  establish the live frequency of the new eager strategy.

## 0.9.10+test.20261017

- Make eager default to a fitting image during casual chat, including everyday
  questions, without waiting for strong emotion or a user image request. Keep
  text-only requests, unsuitable candidates and serious tasks as text-only exits.
- Change eager's tier-following reminder interval from three turns to one.
  Keep explicit overrides, minimum reminder interval, polling and send gates.
- Within the same 12-row/2,000-character shortcut budget, give eager broader
  everyday coverage and preserve matcher relevance among equally used images.
  Browse drowsiness as `犯困` rather than the ambiguous single character `困`.
  Reserved and natural keep their existing shortcut profile.
- Add an eager-only short reply-form example inspired by the local reference
  plugin's optional teaching examples. Keep explicit-ID selection, normal speech
  first, private receipts, host transport and the frozen reserved baseline.

## 0.9.9+test.20261017

- Preserve the 0.9.8 eager strategy as the new reserved baseline: reminder
  willingness, both tool descriptions, trigger criteria and three-turn reminder
  cadence. Change the model-visible tier name to reserved. A frozen 0.9.8
  fixture protects this baseline from later eager tuning.
- Natural and eager retain their existing strategies pending further tuning;
  this release does not claim a new ordering of their actual frequencies.
  Saved tier selection and explicit reminder overrides are preserved.
- Keep send gates, shortcut rotation, image transport and speech nonrepetition
  unchanged. The 29-reply/two-image sample is a reference, not a fixed schedule.
- Update settings help and examples to describe the transitional tier mapping.

## 0.9.8+test.20261017

- Remove repeated lookup-first guidance from eager tool descriptions. Prefer a
  fitting preloaded ID; browse only when more choices are needed. Keep speech
  first, single-image preference and private nonrepetition receipts.
- Shorten standing instructions and keep detailed matching examples on lookup
  results. Preserve full per-image captions/tags and all matcher/send checks.
- Rotate shared shortcuts past the latest N distinct successful images across
  roles, following the recent-dedup setting. Failed attempts do not occupy slots.
  Per-role lookup and send gates remain unchanged; old IDs still get rechecked.
- The 0.9.7 live window from 11:58:54 to 12:05:02 had 25 user/complete events,
  two direct-ID attempts, one recent-repeat refusal and one confirmed image.
  There were no lookup calls or empty candidate results in this window.

## 0.9.7+test.20261017

- Preload up to 12 fully described, distinct candidates from the active
  collection into the send tool, within a 2,000-character candidate budget.
  A fitting candidate can be sent by explicit ID without a preceding lookup.
  Keep lookup for other choices, normal speech first and default captions empty.
- Prefer less recently used representatives and rebuild through the existing
  library-change refresh. Disabled and unannotated images are excluded.
  Send-time availability, per-role recent filtering and all send gates still
  apply; this shortlist is not a contextual selection or an automatic sender.
- Recognize compact combinations of known positive actions and moods, such as
  `求摸摸舒服`, while retaining pending-event phrases, unknown text and negation.
  Preserve action, recipient, stage and unrequested-scene filters.
- Log candidate pool, token counts and rejection counts, including recent-use
  exclusion and unavailable group scope. Queries and dialogue are not logged.
  The previous ten-character live miss cannot be reconstructed from old logs.

## 0.9.6+test.20261017

- Give the eager tier a soft reference frequency of one image per two or three
  complete casual replies, prioritizing lookup after several replies without an
  image. Respect text-only requests, matching and the single-image preference.
  Inspired by the reference AstrBot plugin's optional frequency guidance.
- Apply tier guidance to candidate lookup as well as sending and reminders.
  Switching tiers refreshes both tools on current and legacy SDKs; failed lookup
  updates restore the previous description and reply context for a later retry.
- Retain speech-first ordering, conditional nonrepetition receipts and all send
  gates. This changes model guidance, not guaranteed image frequency. See
  `docs/eager-frequency-review-20261017.md` for the local log findings.

## 0.9.5+test.20261017

- Put normal speech before optional sticker lookup in every tier, the lookup
  tool description and reminder cues. Remove the instruction to select before
  speaking; preserve explicit candidate-ID selection and image-only replies.
- Tell the model to finish silently when the reply is already complete on all
  receipts, including empty candidates, refusals and duplicate registrations.
  Keep normal speech on the host channel and default sticker captions empty.
- Preserve host reply-tail delivery, blind images, accounting and send gates.
  This is a plugin guidance fix, not a host speech filter or a guarantee that
  every model will emit pre-tool text or avoid repeating itself.

## 0.9.4

- Release only the host-capability image transport changes. Preserve the 0.9.3
  panel, recall rules, explicit-ID selection and model guidance.
- Repeated speech and sticker-operation narration remain known limitations.
  This release does not filter host speech or include local diagnostic tooling.
- On compatible SDKs, request original-reply context for candidate lookup too,
  avoiding extra bus/current-role probes and retaining per-role recent filtering.
  Add elapsed-time logs for lookup, send and host tail registration. Model tool
  round trips remain; this does not promise pure-text response latency.
- Preserve original image bytes up to 1 MiB on the compatible host reply-tail
  HTTP channel instead of applying the ordinary 256 KiB message budget first.
  All 190 bundled GIF originals fit; immediate delivery keeps its WebP variants.
- Port host-capability reply-tail delivery onto the 0.9.3 mainline, retaining
  precise candidate recall, explicit-ID selection, the simplified panel and
  the original/animation-preserving official delivery assets.
- Register the selected image promptly and let the matching host place it after
  the original text queue and final presentation. Preserve immediate delivery
  on hosts without this capability; this does not wait for TTS completion.
- Revalidate image availability after uploads before captions or registrations.
  Reconcile uncertain receipts without immediate replay and count confirmed
  host submissions once, including cancellation and shutdown races.

## 0.9.3

- Prepare the 0.9.3 GitHub release on 2026-10-16. Do not notify the plugin
  Market; GitHub release assets are the delivery for this version.
- Replace empty-candidate narration with a private continuation instruction;
  explicitly keep lookup failures out of ordinary replies and roleplay actions.
  This is model guidance, not a host-output interception guarantee.
- Recall common equivalent mood words and fully recognized compact combinations
  without splitting unknown actions or negation. Preserve action, recipient,
  stage, scene and recent-use filters; never fill misses with unrelated pictures.
- Keep unrequested toasts and drinks out of broad mood results.
- Disable empty or invalid batch-tag actions, validate again before dispatch,
  and reject empty tag updates without reporting false successful edits.
- Simplify the panel into Library and Settings. Combine search, category filtering
  and import; move secondary management behind explicit menus and keep images
  visible. Use a compact collection selector without changing the active zone
  when browsing.
- Put eagerness and send cooldown first in Settings; collapse reminder controls
  and diagnostics. Shorten image-editing labels and preserve all existing actions,
  saved values, selection state and detail return behavior.
- Add an offline preview and browser layout checks using the actual host renderer
  and bundled sample images, with no installed-plugin or live-data writes.
- Distinguish waiting to receive a gift or meet a visitor from already holding,
  opening or greeting. Prefer explicit waiting evidence and preserve future
  wording in image descriptions instead of treating it as a completed event.
- Stop filling broad mood candidates with unrequested money, trophy, gift,
  greeting or other known scene cues; return fewer images or none after recent
  filtering. Preserve explicit scene queries, category browsing and exact-ID sends.
- Cover the latest waiting/unboxing and waiting/greeting mismatches with the
  shipped official pack and custom regression cases; keep all send settings.
- Add an independent send-cooldown input in the plugin panel, supporting
  0–3600 seconds with explicit save and immediate application; 0 disables it.
  Preserve the 20-second default, reminder settings and other send gates.
- Distinguish brewing, serving and drinking tea; reject explicit event-stage
  conflicts and rank simple expressions ahead of unrequested gift, trophy,
  dance, drink or food plots. Keep model review and exact-ID confirmation.
- Clarify that the panel's zero reminder interval does not disable the separate
  send cooldown. Show the configured send cooldown and log both effective values;
  preserve all existing defaults and live settings.
- Prefer reply-intent queries with a small vocabulary of common action phrases.
  Filter recognized actions before ranking, reject explicitly reversed roles,
  and prefer literal matches over equally annotated synonym expansions.
- Ignore explicit unsuitable-usage clauses during chat retrieval and avoid
  matching negated intent as positive intent. Keep full model descriptions and
  management search unchanged; category names do not supply per-image evidence.
- Emphasize core attitude and concrete action when selecting an image. Preserve
  explicit-ID sends, no-match exits, group boundaries and all frequency settings.
- Add regression coverage using custom examples and the existing official pack.

## 0.9.2

- Prepare the 0.9.2 stable release on 2026-10-15, including the fixes exercised
  in the unpublished 0.9.3 local test build. Publication is verified separately.
- Replace autonomous category random picks and unique keyword auto-sends with
  candidate descriptions followed by model selection of an explicit image ID.
  No match sends no image; invalid IDs never fall back to unrelated images.
- Match group and query together, exclude recent/disabled images from candidates,
  and prefer positive usage tags over caption text mentioning unsuitable moods.
- Review spaced animation frames and rewrite captions and usage tags for all
  190 bundled images in content version 3, distinguishing sender, recipient,
  tone, intensity, and unsuitable contexts. Keep all 380 media files unchanged.
- Keep sticker search, selection and sending out of spoken text and parenthesized
  actions. Apply the instruction to both tool descriptions, reminders and every
  model-facing result, including empty candidates, refusals and queued sends.
- Require image actions, targets, event stage and tone to fit the reply.
  Broad happiness or affection alone does not justify unmentioned unboxing or licking.
- After submitting an image, resume only unfinished text without repeating the
  prior reply or restarting the selection workflow. Include the selected ID in
  successful tool trace lines.
- Preserve existing eagerness, reminder cadence, sender gates and library data.
  Add selection, metadata-upgrade, private-instruction and no-delivery coverage.
- Review the 16:52-17:32 host run: 39 user turns, 11 candidate queries and nine
  successful ID-based submissions, with no selection narration in saved replies.
  Some action mismatches remain; this observation does not guarantee compliance
  or semantic correctness in all conversations.

## 0.9.1

- Prepare the 0.9.1 stable maintenance release on 2026-10-08 with four
  library/import/send fixes. Publication evidence is recorded separately.

- Serialize library mutations, persistence, rollback and snapshots across host
  threads, including usage records and upload sessions.
- Account for manifest entries beyond the 512-image import limit and retain
  partially imported inbox archives. Refuse exports that exceed that limit.
- Reject new pack images whose category belongs to another zone instead of
  silently importing into that zone. Preserve content-based duplicate handling.
- Revalidate current image existence, enablement and model-visible zone before
  submission after uploads. Keep explicit panel sends independent of the active zone.
- Add deterministic concurrency and import/send regression coverage.

## 0.9.0

- Publish the first stable Market release on 2026-10-08 after the initial
  repository review was approved. Release #1 and tag Verify #18 passed;
  Market version 0.9.0 is the verified stable latest. Keep immediate tool
  delivery and document its reply-tail limit.
- Use a relative test-helper import so the pinned Market Ruff policy passes
  from both the plugin root and the CI checkout parent. Remote Verify run #14
  passed on commit `7296e8b`; run #16 also passed on commit `a8af51d`.
- Refresh pre-release status and installation guidance; distinguish the earlier
  development 0.9.0 entry from this maintainer-selected version reset.

- Split the unchanged bundled archive into 64 MiB chunks with size and SHA256
  checks, avoiding GitHub's 100 MiB file limit without LFS or host changes.
  Read chunks directly with bounded seekable I/O; retain legacy ZIP compatibility.
  Preserve all original media, delivery variants, metadata and library behavior.

- Add an Apache-2.0 source license, copyright notice and separate media-scope
  statement, and ship them in both local and Market builds. Bundled-media
  redistribution rights are not granted or verified by the source license.
- Document first Market review, immutable revisions, standard Release evidence,
  stable/beta publication and the remaining installation and remote-CI checks.

- Prepare the first public-release candidate under a maintainer-selected
  version reset. 0.9.0 sorts below the historical 0.20.x development versions;
  existing installations need an explicitly confirmed manual replacement.
- Replace the release-history README with installation, first-use, configuration,
  privacy, compatibility and troubleshooting documentation. Preserve history here.
- Restore the local Git repository from the existing GitHub main history without
  replacing working files or rewriting remote commits.
- Keep sticker behavior, configuration defaults and user-library data unchanged.
  GitHub CI, first Market review and publication are separate release gates.

## 0.20.19

- Prepare a security-review build before the first stable release; this is not
  a Market publication or a declaration that 1.0 release gates have passed.
- Bound ZIP manifest decompression and keep loopback diagnostics off system
  proxies, with focused regression coverage.
- Run release checks in disposable host snapshots, never the original host
  checkout or installed application. Fix development-cache lint exclusions.
- Align Market packaging with the local runtime allow-list, reject linked
  build inputs, and regenerate packaged metadata from the staged payload.
- Remove stale generated metadata, generalize the installation-path example
  and expand ignore rules for secrets, logs, runtime data and build artifacts.
- Record remaining release blockers: Git history/remote CI are unavailable
  locally, and source licensing plus bundled-media redistribution rights
  require the maintainer's confirmation. No host or user data is modified.

## 0.20.18

- Restore exact original GIF bytes for official collections, details and exports.
  Add separate animated WebP send variants under the inline budget, preserving
  timing, loops and transparency without deliberate frame sampling. Variants are
  lossy when needed; identical decoded frames may merge without shortening time.
- Raise ZIP import capacity to 128MiB so original-quality exports can be reimported.
- Upgrade existing official images through explicit historical SHA256 aliases
  while preserving IDs, owner edits, disabled state, usage and active zone.
  Validate media releases before updating and roll back failed catalog writes.
  Deleted images are not automatically restored and custom-zone images stay put.
- Protect animated WebP from the host upload path that flattens animations.
- Replace the no-op market verification workflow and add the official release
  workflow. Actual GitHub CI and Market publishing remain external release gates.
- Add upgrade and media build regression tests plus release/update documentation.
  No host, installed plugin, user configuration or live library is modified.

## 0.20.17

- Make the library the default workspace, with separate sending settings and
  runtime status views, category navigation and compact management controls.
- Expand zone management and category descriptions on demand to keep the
  narrow-screen workspace focused on images.
- Keep batch actions and feedback available while browsing; distinguish hidden
  selections and process large selections in batches of at most 200 IDs.
- Freeze the target zone for category creation and imports, preserve failed
  drafts, prevent duplicate management requests and distinguish refresh errors
  from successful writes.
- Add explicit image retry and decode-error handling, keyboard-accessible image
  buttons and named selection controls, with focus restored on detail return.
- Preserve the v0.20.16 detail workflow, existing host protocol, sending rules,
  configuration defaults and stored library. No Steam import is performed.

## 0.20.16

- Open a clicked sticker in a dedicated detail workspace instead of inserting
  its details above the entire library. Keep the library view state and restore
  the browsing position when returning.
- Bound full-size previews to the available panel width, use explicit CSS
  length units in raw DOM styles, and keep the original GIF animation in details.
- Keep failed edits and deletes in their current workspace, reset cancelled
  drafts, and prevent duplicate submissions while an action is pending.
- Preserve the existing library, user configuration, main-chat tools, sending
  behavior, reminder settings and host boundaries. No Steam import is performed.

## 0.20.15

- Refine only the eager tier's main-chat decision, trigger and reminder wording.
  Name ordinary light conversation, celebration, affection, comfort, playful
  complaints and sleepy goodnights as opportunities for a fitting sticker.
- Prefer one matching category without waiting for an explicit image request
  or treating an already complete text reply as a reason to skip the image.
  Select the category for the speaker's intended response, not by copying the
  other person's mood. Keep text-only replies for serious tasks or when no
  category fits; do not require a sticker on every turn.
- Keep natural/reserved wording, default configuration, reminder eligibility
  and timing, send gates, tool schemas, immediate blind delivery, tool recovery
  and Agent isolation unchanged. No host or installed user files are modified.
- This is a prompt-only behavior trial, not a guaranteed increase in model
  tool-call frequency. Compare normal new conversations with stable API
  settings; do not count retained history or background tasks as main calls.

## 0.20.14

- Check main-chat tool registrations every 10 seconds, independently of reminder
  eligibility. Inspect each character separately so another character's healthy
  registry cannot mask missing `sticker_list` or `sticker_send`.
- Re-emit only missing SDK tool metadata through the existing registration path,
  with bounded retries and no unregister/register gap. Treat unreachable,
  unsuccessful or unrecognized responses as unknown, not as proof of absence.
- Keep recovery pending until a later registry query confirms the tools are
  present. Separate missing episodes, reissue attempts and confirmations in
  diagnostic logs; zero detected gaps is not proof of continuous availability.
- Before a reminder is submitted, check its target character and record the
  registry status, observation time and cache age without conversation content.
  Diagnostic failure does not suppress the existing reminder.
- Preserve eagerness, reminder cadence and wording, send gates, immediate blind
  main-tool delivery, user configuration, the library and Agent auto-routing
  isolation. This repairs a registration-recovery blind spot, not a guarantee
  that the model will choose to call a registered tool.
- The October 2 rollback test registered tools before API configuration saves
  rebuilt the host's character managers. Subsequent zero-call windows strongly
  suggest lost registrations, but existing logs do not expose the actual model
  request. No host code or Steam installation is changed.

## 0.20.13

- Mark all 28 panel and management entries with the supported
  `metadata.agent_auto=false` opt-out. The host's automatic Agent assessment
  no longer selects these entries on hosts honoring that metadata; UI actions
  and explicit API dispatch remain available.
- Keep `sticker_list` and `sticker_send` registered for the main conversation,
  with their existing names, schemas, group-first guidance and eagerness tiers.
  Do not misuse character-scoping `role` as a main/Agent audience switch.
- Steam logs confirmed background tasks dispatched `entry=send` even for light
  interaction, rather than a main-tool call internally handing off to Agent.
  The old generic send handler remains callable for compatibility, but is no
  longer offered as an automatic background fallback.
- Preserve immediate main-tool delivery and blind own-image display from
  0.20.9/0.20.12. The 0.20.3-to-0.20.8 package comparison found unchanged
  awareness timing and willingness tiers; forced deferred delivery was the
  confirmed lost-submission regression. Do not roll back the entire plugin,
  alter user configuration or inflate sticker counts with automatic random sends.
- Older hosts ignoring the Agent opt-out, or offering dynamic `__llm_tool__`
  entries to Agent, still need host-side support for that audience boundary.
  No host or Steam installation is modified. Actual main-tool frequency and
  absence of task HUD dispatch still require a fresh Steam conversation test.

## 0.20.12

- Submit main-tool stickers with `visibility=["chat"]` and `ai_behavior="blind"`
  for both immediate and deferred delivery. The selected sticker remains visible,
  and its id/description remain in the tool result; the plugin no longer feeds its
  own image and optional caption into the next model input.
- The October 2 Steam test showed a successful tool send followed by an own-plugin
  image input and a switch from `free-model` to `free-vision-model`. This removes
  that confirmed feedback path, not a demonstrated cause of all low tool-call frequency.
  Subsequent visual follow-ups rely on sticker metadata unless the user supplies an image.
- Keep panel and background Agent `read` delivery, passive reminder `read` delivery,
  immediate tool submission, separate caption/image messages, willingness prompts,
  reminder cadence, the 0-to-60-second setting and all send gates unchanged.
- Include the actual `ai_behavior` in successful-submission logs. This is a local
  transport receipt, not confirmation of host rendering or model consumption.
- Validate host bridge routing and add source-specific feedback regression coverage.
  Actual frequency improvement still requires a fresh Steam conversation and user testing.

## 0.20.11

- Recognize bounded, complete light-interaction utterances such as head pats and
  affectionate reactions in the local awareness gate. Do not match the same words
  embedded in editing/search requests. The time floor and event-gate switch still apply.
- Make the main `sticker_send` schema consistently group-first, use the actual
  `sticker_id` argument name, and shorten ordinary reply/optional-caption guidance.
  Natural and eager descriptions now include light conversation and the speaker's attitude.
- Remove contradictory, preemptive send-gate warnings from awareness reminders.
  Retry guidance remains in actual refusal results; cooldown, deduplication and
  probability enforcement are unchanged.
- Report `sticker_send_calls` and `agent_send_calls` independently in runtime snapshots
  and ledgers. These count entry invocations, not every upstream model decision.
- Preserve background Agent fallback, immediate main-tool delivery, caption/image
  separation and the persisted 0-to-60-second reminder setting. Agent-dispatched
  sends can still appear in the host task HUD.
- This repairs observed trigger gaps and prompt inconsistencies, not a demonstrated
  model-frequency regression. Steam tool payloads are not observable in the available
  logs; actual call-frequency improvement still requires user import and testing.

## 0.20.10

- Add a panel slider for the minimum reminder interval, with an explicit save action,
  a 0-to-60-second range and persisted configuration. Zero removes only the time limit;
  turn thresholds, emotional triggers and send gates remain unchanged.
- Keep the existing 60-second default and accept existing in-range overrides. Clamp
  configuration values above 60 seconds to the new maximum; reject invalid action values.
- Clarify the cadence readout as a reminder threshold and turns since the last reminder,
  rather than implying a sticker is sent every N turns.

## 0.20.9

- Restore immediate submission for model `sticker_send` tool calls.
- Keep reply-tail completion buffering for background Agent sends, where the host may
  already be generating a response. Explicit `send.defer_tool_sends=true` opts tool calls
  back into the old ordering and cancellation behavior; the default is false, including
  existing configurations without the key.
- Preserve cooldown, recent deduplication, probability, GIF bytes, independent caption/image
  delivery, and panel-send behavior.
- This avoids losing valid tool selections when a new user input arrives before the host's
  best-effort completion log marker; no host code or SDK contract is changed.
- Immediate tool sends retain `ai_behavior="read"` and may appear before remaining reply
  text. This fixes a proven lost-submission path, not the model's decision to call a tool.

## 0.20.8

- Clarify potentially ambiguous guidance after a zero-call sample on 0.20.7: a normal text reply can still call `sticker_send(group=category)` to add an independent sticker, without filling `text`.
- Reserve optional `text` for extra captions and avoid repeating those captions in the ordinary reply. Keep independent image delivery and caption compatibility unchanged.
- Preserve willingness tiers, decision checkpoints, trigger criteria, reminder cadence, send gates and the two-second default display buffer. This is a wording clarification, not a proven fix for model non-use or a guarantee of restored sticker frequency.

## 0.20.7

- Send stickers as standalone image messages. Keep the optional `text` argument compatible: a nonempty extra caption is submitted first as its own text message, followed by a separate image message; an empty caption still submits only one image.
- Keep ordinary assistant replies in the assistant's own response channel. Tool descriptions, awareness guidance and result notes treat `text` as optional extra caption content and discourage repeating it in the ordinary reply.
- Validate and construct the image before any caption submission, but do not claim a transaction across the two pushes. If the caption is accepted and the image fails, preserve the image failure reason and report `text_submitted=true` without resending the caption automatically.
- Count successful image submission, cooldown and deduplication once per sticker, not once per bubble. Partial failure remains one failed attempt with no image-success credit or cooldown advancement. Ledger and timing logs retain lengths and flags, not caption text.
- Preserve the two-second default compatibility buffer, GIF bytes, source labels, send gates, eager cadence and plugin-only host integration.

## 0.20.6

- Reduce the default `send.reply_tail_display_buffer_sec` from 7 to 2 seconds. The 0.20.5 seven-second wait exposed a continuous-input regression: all three observed tool selections were canceled as `turn_superseded` before transport submission, not hidden after a `blind` push.
- Keep explicit user overrides, including `7.0`, unchanged. Older configurations without this key inherit the shorter default without manual edits.
- Preserve cancellation on newer input, known response activity, subsequent completion markers and expiry. Do not carry old pending stickers into a later turn; the shorter default only increases the opportunity to send before the next input.
- Keep this a plugin-only display compatibility buffer, not a precise frontend or TTS completion signal. Longer responses may still display more text after the sticker, and rapid input can still cancel it. Probability, cooldown, deduplication and eagerness remain unchanged.

## 0.20.5

- Add a default 7-second display buffer after the Main log generation-end marker for the host's two-second, sentence-by-sentence chat display. Configure `send.reply_tail_display_buffer_sec` within `0..30`; `0` restores no extra buffer, and existing configurations inherit the new default automatically.
- Treat this delay as a compatibility estimate, not a frontend completion callback or a TTS playback guarantee. Do not send on elapsed time alone without the generation-end marker.
- Cancel pending stickers on same-character new user input or a later completion marker so stale images do not enter a new reply. Rapid consecutive chat may therefore cancel the previous turn's sticker.
- Display queued `sticker_send` tool images with `ai_behavior="blind"` instead of feeding them into a subsequent turn. Keep panel and background Agent `read` behavior unchanged, and scope the queued receipt's no-repeat instruction to the current turn only.
- Keep probability, submission-time cooldown, deduplication and eagerness unchanged. These changes do not promise a higher sticker frequency.

## 0.20.4

- Queue model-selected stickers until a newly appended, character-specific Main log completion marker. Use the existing read-only loopback health endpoint as a busy guard, not as a completion signal. No host changes or new SDK parameters.
- Keep panel test sends immediate. Background Agent sends defer while a reply is active or its state is unknown; confirmed idle sends retain their existing direct path.
- Retain GIF bytes, caption-before-image parts, gates and submission-time cooldown. Queued receipts do not count as successful sends; actual delivery or cancellation updates the ledger and run statistics once.
- Bound pending sends by role, count and lifetime. Cancel on shutdown, disable, missing/changed logs, ambiguous subsequent turns, deleted/disabled images or inactive zones. Follow size-based log rotation without holding Windows log files open.
- This is a Steam log compatibility adapter, not an official SDK callback or an audio-playback completion guarantee. Exact correlation during interruption remains limited by the host's lack of turn IDs in this signal.

## 0.20.3

- Let the background Agent use the send entry with a group, query, or no selector. An explicit generic request selects an available, non-recent image from the active zone; existing panel sends by id and all send gates remain unchanged.
- Generate package metadata using the official host probe on the actual filtered payload, and reject builds if the host cannot read it. Development-tree metadata is no longer copied into distribution packages.
- Poll retained turns without injecting new cues at shutdown and always emit a final run ledger. Agent sends are recorded separately from panel sends.
- Treat zero cooldown as disabled even when submission-time clock correction is slightly ahead of the wall clock.
- Add 22 regression cases for Agent selection and gates, payload metadata validation, final accounting, and disabled cooldown.

## 0.20.2

- Protect the catalog after read failures: retain the last good snapshot, reject writes and repair purges until a successful reload, and distinguish a fresh install from a missing index with remaining images.
- Reserve sends per character across awaits and host threads. Concurrent sends cannot bypass cooldown or deduplication; cancellation and failure release the reservation. Cooldown starts at submission, not before upload.
- Count all unseen retained user messages on each poll (up to the host's 500-record query limit), with independent role watermarks and equal-timestamp handling. Awareness processes only the latest message per role to avoid replaying stale cues.
- Add 25 regression cases covering recovery, concurrency, cancellation, message bursts, interleaved roles, and observation counts. Full suite: 480 tests.

The memory bus is still bounded by the host's retention and TTL. Messages expired before polling cannot be recovered by this plugin.

## 0.20.1

v0.20.1「把两个说不出口的读数补上」——0.20.0 在 Steam 实机跑出来的第一份账（19 轮 / 2 次自发调用 / 0 拒绝）
暴露了两处仪器哑口，本轮**纯观测，零行为变更**：

- **信号面**：`pointer=0/5` 讲不出病因。两种病处置完全相反——"这 19 句确实没情绪"（什么都不用改）
  vs "`turn.text` 在总线里读成空串"（事件门控恒假，得修读法）。现在每轮求值时记
  `turn_texts / turn_empty / signal_hits` 三个次数（**只记长度与次数，原话永不落盘**），
  注入日志行尾加 `turn_chars=`。命中数记在**判据求值那一瞬**、不等注入落地，
  否则被地板挡住的轮会把 `signal_hits` 压成假证。
- **工具面**：实机 23:55:17 心跳器补挂了 `sticker_list, sticker_send`——那之前她**根本没有**
  这个工具可调，而这条通道只在补挂那一刻吼一行，丢了多久、期间吃掉几轮全看不出来。
  现在 `ToolWatch` 记 `gaps / gap_seconds_max / unreachable`，账本并排打
  `tool_gaps=3(≤900s) unreachable=4`。**秒数是上界不是时长**：巡检 300s 一次，真实缺席起点不可知，
  字段名一律带 `_max`；补挂≠确认在场，基线清空后要到下一次 healthy 才重新计时；
  开机第一次巡检就发现缺席时**不编时长**（只加次数）。`unreachable` 单独一支——
  对面没答应是"状态未知"，既不算健康也不算缺席。
- 顺带记一条比 realtime 熔断更常见的洞：`tool_registry` 是 main_server 的内存属性，
  宿主侧重连/重启即清空（实机紧邻 23:50:49 的 `APIConnectionError`）。成文为**陷阱 35**。
- 发送层四把闸、措辞面、点门判据**一字未动**。测试 447 → 455。

## 0.20.0

v0.20.0「检查点 + 跟着这句话点名」——接着陷阱 33 没走完的那半步，并翻掉 v0.17.0 的一道尺：

- **常驻面从「选项表」改成「决策检查点」**：`sticker_send` 的描述**第一句**现在要求她
  「开口之前先做一次判断：这句配上图会不会比纯文字更准」（`core/eagerness.DECISION_CHECKPOINT`，
  三档三条）。依据是宿主里唯一被验证她真会自主调用的先例——`recall_memory` 靠的是系统提示里
  那句 「call the recall_memory tool **FIRST** … even when she feels she already remembers」
  （宿主 `config/prompts/prompts_chara.py:121`），不是它的工具描述。
- **翻尺**：旧门 `test_says_all_three_forms_are_allowed_without_an_imperative`（「描述里不许有祈使句」）
  删掉，换成两道新门——检查点必须排在目录之前、每一档都必须留「判断完是不配」的合法出口。
  要她**判断**，不是要她**发**。禁令族措辞那条旧尺一字未动。
- **点名换触发源**：由「每 N 轮」改成「**这句用户话里有情绪**就点名一次」（`core/awareness.emotional_signal`
  = 本地情绪词表 + 反应标点，不为这件事调用模型，陷阱 30 仍有效），轮次计数退居兜底，
  新键 `[sticker_manager.awareness].event_gated`（默认开）可整条关回旧节奏。
  载体仍是 `read`：同门 `forever_companion/services/emotion_sense.py:430-520` 那条真有效的提醒用的
  就是 `read`，差别在触发源是刚读完的那句，不在投递档——原本打算换 `respond`，被这份先例否掉。
- **读数补齐**：注入日志新增 `trigger=signal|count|wall_clock` 与累计 `trigger_counts`（面板快照同步），
  每 60s 心跳落一行 `run ledger:`（turns / calls / sent / refused / pointer 占比 / 常驻面形状）。
  为什么要并排打 `turns` 与 `calls`：宿主 realtime 有两个**插件不可见**的吞图洞
  （4 次/15s 工具熔断、抢话时在飞 tool task 整批取消，宿主 `omni_realtime_client/_tools.py:810-833,356-381`），
  它们一次都不会进本插件的计数，读数上就是「话轮在涨、调用为 0」。节流照陷阱 31 的尺来。
- **发送层四把闸一口没动**（冷却 / 最近不重复 / 概率 / 复用窗口，陷阱 26），本轮全在措辞面与观测面。
- 成文为**陷阱 34**，内含三条诚实边界：标记协议在本宿主做不出来、系统提示槽不对插件开放、
   realtime 两个洞插件测不到。测试 421 → 447。

## 0.19.0

v0.19.0「三面补齐 + 档位驱动节奏」（主人拍板：三面一起补，**且积极性要能自己调**）——先把一个错判记账清楚：

- **我上一版搬错了东西**：v0.17.0 我照参考那边把工具描述写成**许可式**（"纯文字、图文一起、只发图都行"），
  依据是"它整套提示词里找不到一句'你必须配图'"。依据对，搬法错——**它敢只给许可，
  是因为它的动作是回复里的一个 token、零额外请求**；我们这里她要停下手调一次工具
  （文字档 = 上游两次请求）。同一个措辞换了载体就不成立：前者的许可是"别拦我"，
  后者的许可变成"没事我就不做了"。主人这轮的原话就是证据：
  「**每次都是我提醒她才知道调用，我不提醒她好像都不喜欢发**」。
- **而日志证明她一旦决定就做对**：0.18.0 之后她两次自发调用都是 `gave=group+text`
  （分类名和配文都填对、两张不同的图、零拒绝）。⇒ 缺的不是"怎么做"，是"**什么时候该想到要做**"。
- **同门对照**（一方插件里她真会调的那批工具，一条都没落下）：每条都写「**触发判据**」+ 跨工具路由
  （`forever_companion/mixins/mood_actions.py:638,667,695`：*"触发判据：心情好、想亲昵…如果是委屈低落想被哄，
  用 mood_seek_harbor 而不是本工具"*），而且**没人只靠描述**：fc 是三面叠加——
  描述给判据 + 事件门控的点名提醒（`services/emotion_sense.py:492-495`，写明工具名）+
  **返回值指挥下一步**（`mood_actions.py:661`）。我们当时只有半面。成文为**陷阱 33**。
- **三面补齐**：① 工具描述加 `TRIGGER_CRITERIA[tier]`（判据排在许可句之前）；
  ② 注入正文从"去看 sticker_send 的说明"改成**点名**——"现在这句有情绪想配，就调 sticker_send：
  填 group（分类名）和 text（你想说的那句）"；③ 成功返回值加 `NEXT_STEP_NOTE` 指挥下一步
  （**不提任何闸**，闸仍在撞闸那一刻由 `_SEND_HINTS` 就地说，陷阱 29 的纪律挪到新面上）。
- **积极性可自定义（主人这轮明确要的那件）**：档位现在统一驱动**点名节奏**——
  矜持 12 / 自然 6 / 爱发 3 轮（爱发那档就是同门实机在跑的 3）。
  配置 `[sticker_manager.awareness].inject_interval_n` 语义改为 **0 = 跟随档位（默认）**、
  写正数 = 主人自己定死（档位不再插手）；面板不加新旋钮，沿用既有的三档 Select，
  但 `panel.awareness.eagerness.help` 三处（zh / en / TSX `defaultValue`）同步改口，
  因为它现在管三件事了——**说明文字不跟着改就是一处说谎的仪器**。
- **两条不能破的线，各钉一道门**：
  ① **发送层的四把尺一格都不许被档位带动**——`TestTierDoesNotTouchSendGates` 拿三档解析结果对拍，
  谁把 `cooldown/probability/recent_dedup/reuse` 挂到档位上就当众红（陷阱 26 的边界一字未松）；
  ② **dashboard 回的是生效值不是哨兵 0**——`test_dashboard_reports_the_effective_cadence_not_the_sentinel`
  反向验过：临时把快照改回原始配置值，这道门立刻红了才撤掉。
- 契约更迭（非放宽，逐条记档）：`inject_interval_n` 默认 8 → **0（跟随档位）**，
  读入下界 1 → 0（负数夹到 0 = 跟随），`test_turns` 那两道默认值/夹取门同步改；
  natural 档的注入正文与工具描述**都变长了**（判据与点名句），v0.14.0 那条"natural 意愿段与 v0.13.0
  一字不差"的钉的仍是**意愿段本身**，未受影响。
- 配置键 ±0（改语义不改形状）、错误码 ±0、盘形 ±0；测试 **411 → 421 passed**（新 12 条）。
- **待实机验收（本轮判据，样本仍按 ≥30 轮）**：**不提醒她**的情况下"她调用工具"这一格上不上来；
  切「矜持」→「爱发」时点名间隔应从 12 轮收到 3 轮（日志 `awareness injected` 的间隔 + 面板「节奏」）；
  而切档**不许**改变任何一次拒绝的理由。

## 0.18.0


v0.18.0「图文同条」（主人拍板：做 A 版、接受署插件名；`probability` 不动保持全放行）——把参考那边 `enable_mixed` 的可搬一半搬过来，动机是**动作成本**不是观感：

- **为什么这一条能提频**：查清了两边成本的不对称。参考那边发图是回复文本里的一个标记（`&&happy&&`），
  **零额外请求**；我们这边她每调一次工具 = 上游**两次**请求
  （宿主 `omni_offline_client/_tools.py:720-727`：`finish_reason="tool_calls"` 就收尾，
  宿主执行完把 `role="tool"` 结果追加进 messages **再发一次**让她把话讲完；
  封顶 `max_tool_iterations=3`，打满还要 forced-finalize 再要一次 `:925-941`）。
  叠加实机量的"她全天对所有插件的工具调用合计只有 0~5 次"，病灶是**动作本身贵**，
  不是目录不在场（0.17.0 已让目录每轮在场）、也不是被闸拦（0 次拦截）。
  所以这一版把"说话 + 另起念头配图"两段决定压成一次调用。
- **宿主侧核到底才动手**（两条结论，都进陷阱 32）：
  - 「同一条」**能做**：一次 push 的 `parts=[{text},{image}]` 走
    `_ordered_plugin_chat_blocks(include_text=True)`（`app/main_server/character_runtime.py:1154,1380`），
    同一个来源气泡、顺序保留、占位符照展开；聊天侧那份**不降分辨率**（宿主有意区分：
    给模型看的压到 1280×720，给人看的保原图）。
  - 「她的」**做不到**：`render_chat_blocks` 的 docstring 写死了理由——插件内容扮成助手或用户
    都是"读者无法核实的谎"（同文件 1355-1372、`turn.py:1804-1816`）；
    曾经能以她身份上屏的 `passthrough_to_chat_bubble` 仍在树里但**零生产调用方**，
    是被有意拆掉的（当年那些图既以助手面目出现又以 user 角色进模型）。**没去重开它**。
- **落地**：`sticker_send` 加 `text`（≤`SEND_TEXT_MAX_CHARS=500`，超长拒 `text_too_long`
  且**一次投递都不发生**；trim 在工具入口，sender 原样转发）；常驻面补一句
  "想跟着一句话说就填 text，图和它会一起出去"——**没有引入新禁令**（现有反向门照样过）。
  台账与日志只记 `text_len` **不记内容**（沿用"只放非隐私字段"的既有纪律）。
- **两条实现纪律各配一道反向可验的门**（新 `tests/test_mixed_send.py` 十一条）：
  ① **图文一体，一起出去或一起不出去**——图超预算时只把话投出去会造出一句没人应答的裸文本
  （`test_text_never_survives_a_lost_image`）；② 内联预算按**整条载荷**算，
  `text_len` 要先从 `inline_max_bytes` 扣掉（`test_caption_counts_against_the_inline_budget`，
  反向样本就是"忘了扣"）。另钉：不带 `text` 时 push 形状**一字不变**（老路径是实机验过 6 版的东西）。
- **已知且不可消的一处**：她调用**之前**流出的文本照常上屏（`_tools.py:803` 无条件 yield），
  所以可能出现「文字 / 图文 / 文字」。宿主给缓冲型调用方留了丢弃锚点（`_notify_tool_round_start`
  `:848-852`）但文字网页路径没注册 ⇒ **插件侧无解**，只能用常驻面的措辞去引导。
  写这条是为了下一轮别又把它当 bug 找一遍。
- 错误码 +1（`text_too_long`，只走工具面，无面板死键）；配置 ±0、盘形 ±0（台账多一个 `text_len` 字段）；
  测试 **400 → 411 passed**（新 `tests/test_mixed_send.py` 十一条）。
- **待实机验收**：她自发发图时**带不带话**、那条气泡署名是不是「表情包管理」、
  图放不下时有没有漏出裸文本；以及本轮真正的判据——**四把读数里的"她调用工具"是否上来了**
  （样本 ≥30 轮再比，历史基线 2~3% 话轮）。

## 0.17.2


v0.17.2「缩略图轮」——主人点的两件事（「日志刷屏」+「加载图片超时」）查下来是**同一个根因**，一轮一起修：

- **量的过程**：当天插件日志 363 行里 **313 行是 `preview`（86%）**，20:49:54~57 **四秒 180 次**；
  根因是 `preview_entry` 回的是**原图字节**分段（陷阱 14 那把尺留下的形状），
  而表情墙一格一图——官方区 190 张 ≈**34.7MB**（base64 后 ≈46MB）过控制通道，面板并发只 2 张。
  排在后面的格子等过 30s 默认预算 ⇒ 「超时」；每张一行宿主 SDK 的 `TRIGGER entry='preview'` ⇒ 「刷屏」。
  宿主侧 `timeout/500/refuse` 全 0（那两条"500"其实是进程号）：不是崩，是排队饿死。
- **修法**：服务端出缩略图。新 `core/thumbs.py`（256px / JPEG 质量 78 / 单张 ≤64KiB，
  超字节降一档 192/65 再试；解码前有像素总数守卫）+ `Library.thumb_for()` 按**内容指纹**落盘缓存
  （`data/library/thumbs/`）——按指纹命名所以**没有失效逻辑**：内容变了自然重算，整目录删掉也只是重做。
- **真 PIL 实测**（用宿主解释器跑真机那 190 张，Pillow 11.3.0）：**出图 190/190、降级 0；
  34.7MiB → 2.33MiB（6.7%）；单张均 12KiB；全量重做一遍 0.3 秒（均 1ms/张）；
  最大那张 249KiB 动图 → 12KiB / 240×240**。
- **三条尺各钉一道门**（新 `tests/test_thumbs.py` 十七条）：① **命中缓存时绝不读原图**——
  反向验法是"把原图文件删掉、缓存还在就该照样出图"（这条写反了本轮就白做）；
  ② 任何失败一律**透明降级回原图分段**，格子不许空白（性能优化不许做成功能回归），
  且降级日志**整轮只吼一次**（一墙 190 张各自降级 = 换个地方刷屏）；
  ③ `repair()` 只回收失主的缩略图，非我们命名的文件一律不碰（摆一个 `someone-else.txt` 钉住）。
- **契约面**：`preview` 加 `kind` 参数，**留空 = 老形状一字不变**（老调用点零改动，有门钉），
  `kind=thumb` 一次到底不分段；`additionalProperties=False` 所以 schema 必须同步加键。
  面板：墙上格子取 thumb、点开的聚焦卡仍取原图，缓存键按档位分开
  （同一张图的两种产物共用一键 = 看过格子的聚焦卡拿到糊图）。
- **取舍（主人拍板接受）**：动图在格子里只剩第一帧——格子是索引不是展品，动画在聚焦卡里看。
- **环境教训**：本仓 `.venv` 里没有 Pillow（宿主解释器有；插件子进程是宿主用 `sys.executable` 起的），
  所以 PIL 只能**函数内懒 import**（模块级 import 会当场打断插件导入链与测试收集）；
  真渲染**不能**写成"没 PIL 就 skip"的测试——那是一道永远不会变红的门（陷阱 5 的教训），
  改跑上面那次一次性实测并把数字留在本条里。测试改钉"PIL 缺席时诚实降级"这个真事实。
- 成文为**陷阱 31**。配置 ±0、错误码 ±0、盘形为纯新增目录（`thumbs/`，可随时删）；
  测试 **383 → 400 passed**。
- **待实机验收**：开一次官方区表情墙 → 滚动应明显变快、不再转圈；
  插件日志当天 `TRIGGER entry='preview'` 行数应从 313 掉到几十以内（一张缩略图仍是一行，
  但命中缓存后不再重复拉）；`data/library/thumbs/` 应出现约 190 个小文件、合计 ~2MB；
  点开除聚焦卡外动画仍在。

## 0.17.1


v0.17.1「观测轮」——**零行为变更**，只补读数。主人装完 0.17.0 反馈"还是很少发表情包"，我去量才发现手上没有能回答这个问题的数：

- **实机查账过程（Steam 版，日志在 `%LOCALAPPDATA%\N.E.K.O\logs\`）**：
  - 常驻目录**确实到场**——手读宿主 `GET /api/tools`：`sticker_send` 描述 **1095 字 / 22 类**，
    `YUI` 与 `ATLS` 两个角色位同一份，末句是爱发档许可句。判断这件事以前只能靠人现场问接口。
  - 今晚唯一那条注入**根本没投出去**：宿主 20:15:38 记 `enqueued callback (passive); next user turn will carry it`，
    之后每个 `turn_end` 都是 `agent_callback_turn=False`，两条 pending 到 20:16:53 热重置还挂着。
    **纯文本模式也会整条漏**（`core_api=free` + `OmniOfflineClient`，不是语音、不是 Gemini 方言）——
    陷阱 29 的载体判断被正面确认，但也说明它的严重程度超出我上轮的写法。
  - 工具通道本身活着：20:25:55 她真调了另一个插件的工具（宿主回调 POST → 200）。所以病灶是"她想不起来调"，
    不是"这个模型不会调工具"。
  - 真数字（历史基线，本轮第一次算出来）：**0918 75 轮→2 发 / 0919 65→2 / 0921 21→1 / 0922 9→0**，
    常年约 **2~3% 话轮**。今晚 9 轮判不了任何事。且今晚 9 轮里有 **2 轮压根没被回答**
    （`APIConnectionError` 重试到终止 + 一次 `400`）。
- **补的三处读数**（这就是本轮的全部内容）：
  1. **她每次工具调用留一痕**：`tool call: name=… gave=<参数形状> [group=…] [q_len=N] outcome=sent|refused reason=…|ok rows=N`。
     以前只有成功那一支有日志，"**她没调**"与"**她调了被闸拦下**"在日志里长得一模一样，而这两种病的处置完全相反。
     实现是薄壳（`tool_sticker_*` 只做留痕，判定原封不动搬进 `_sticker_*_body`）——**不改任何决策**。
  2. **常驻面重挂成功留一痕**：`sticker_send surface applied: chars=… categories=… tier=…`；描述未变时不记
     （记了就成噪声，而"重挂到底生没生效"正是本轮要看的那件事）。
  3. **面板加「本次运行」只读读数**：她的话轮 / 她调用工具 / 发出成功 / 被拦下 / 常驻目录 N 类 + 最近一次调用形状。
     新 `services/runstats.py`（内存态、重启归零），分母来自 `TurnWatcher.turns_seen`（新累加器，
     与"距上次注入攒了几轮"那把尺**分开**——合成一把的话注入一成功分母就归零，"40 轮里调了 1 次"就算不出来了）。
- **三条口径纪律**（反面就是"仪器坏了但读数正常"那个病）：面板标题写死「本次运行（重启插件归零）」，
  不许长得像"今日累计"；主人在面板点"发到聊天"**不进**她的计数（两把尺混了读数就失去意义）；
  `sticker_list` 那种"查了但没发"既不算发出也不算拦下。
- **隐私**：她的检索词只落长度，日志里出现原文即门红（哨兵门 `test_query_text_never_reaches_the_log`）。
  分类名是主人自己写的目录键，落原名——"她挑了哪个分类、挑空了几次"正是本轮要看的东西。
- **反向验过**（陷阱 5 的规矩：新检查必须先拿"已知应该红"的样本验一次）：临时把原文塞进日志行跑该门，
  **它当众报红了**（`q_len=哨兵原话…`）才撤掉。只会被动的绿不算绿。
- **归因作废一条**：`docs/sticker-system-study.md` §2 那行"VLM 逐图标注 ✅ 可搬（fc 槽位直连先例）"
  只在**主人自配付费 provider** 时成立——宿主免费路由的文字档只对主程序身份放行，
  插件子进程走它必拿 `400 STOP ABUSE THE API`（`N.E.K.O/tests/testbench/pipeline/chat_runner.py:201-252`）。
  成文为**陷阱 30**（量她的行为之前，先证明她这一轮真的被回答过、且读数存在）。
- 入口面 ±0、配置 ±0、盘形 ±0、发送/注入/闸的逻辑 ±0；i18n +8 键（zh-CN 与 en 各 8，两边键集门守着）。
  测试 **370 → 383 passed**（新 `tests/test_runstats.py` 十三条）。
- **下一轮怎么用它**：装上后正常用够 30 轮以上，读面板或日志出这四把数，再决定动文案、动闸还是动载体。
  样本小于 30 轮不下结论。

## 0.17.0


v0.17.0「常驻目录面」（主人拍板：方向照参考项目解决「不咋爱发表情，老得手动点注入她才发」；体量选"全表带说明"；注入"降成短提醒"）——本轮换的是**载体**，不是措辞也不是频率：

- **先量再改**（参考项目 29,642 行二次走查，一次纪要只吃了数据层与节奏层）：它频率高的前两条机制是
  ① `分类键 - 用途` 全表**每轮缝进常驻面**（`main.py:871-884` 拼表 + `_wrap/_strip_meme_prompt:692-719` 幂等重缝），
  ② 那张面上**只有许可没有闸**——`probability=100`、`max_memes_per_message=-1` 全是模型看不见的事后过滤
  （`_conf_schema.json:338-351`、`event_handlers.py:1077,1294-1299`），整套提示词里**找不到一句"你必须配图"**。
  对照我们：目录只活在存在感注入里，而 `sticker_send` 的工具描述**通篇是禁令**
  （"什么都没给会拒""别连试""只有主人点名才 force"）——那是教她别发。
- **载体为什么换成工具描述**：工具列表每轮随请求喂给模型，且重挂即推**活会话**
  （宿主 `main_logic/core/tool_calling.py:67` `register_tool_and_sync` → `_sync_tools_to_active_session`
  → realtime `session.update {tools}`；唯一例外 Gemini 方言不支持中途换表，`_tools.py:753`）。
  而 `ai_behavior="read"` 是**排干即弃的一次性 cue**：`drain_agent_callbacks_for_llm` 排干就清队，
  文字模式等下一个用户话轮、**语音模式要等下一次自然热切换**（宿主 `lifecycle.py:2749-2778` 明写
  "哪怕那隔好几个话轮"，且是 owner 决定）——把地图塞进一张会过期的便条，正是 v0.14~v0.16 三轮的隐含错误。
- **新纯函数层 `core/tool_surface.py`**：能力句（三种形态都摆成合法选项）+ 读表指引 + 分类全表 + 档位许可句。
  目录尺**复用 `format_group_overview`**（陷阱 21 自动成立：空分类/只有禁用图的分类不进描述），
  再摘掉「未分组」行（那是面板记账词，她填进 group 必回 `group_not_found`）；档位句仍只从
  `core/eagerness.py` 出一个来源（陷阱 26 没动）。真机官方区实测渲染 **1095 字 / 22 行**。
- **禁令从描述里清出去，闸一个都没拆**：冷却/去重/概率仍在 `services/sender.py` 硬拦，
  她撞闸当轮由 `_SEND_HINTS` 就地说明——常驻面上不再提。反向门钉住七个禁令词
  （`会拒/别连试/只有主人点名/force/挡下/最近不重复/冷却`）不许回到描述里。
- **库变了自动重挂，收口在写盘**：`Library(root, on_saved=...)` 在 `save()` 成功后回调
  （新增入口只要走 save() 就自动带上，不必各记一遍）；回调**只打标不发 IPC**——
  导官方包会连着 save() 190 次，那就是 190 个 `session.update` 打在一条线上。
  真正重挂在 10s 的 `turns` 拍上（与注入同拍、各兜各的异常，陷阱 16 同族）；
  脏标只在成功后清（失败留着，下拍重试）。开机/改配置/面板切档走 force。
- **注入降成短提醒**：正文抽掉套图概览与"最近常用"行，只剩"库有多大 + 去哪看目录 + 档位段"三句
  （新门钉 `len(text) < 300`，防它长回目录复读机）；`push_message` 补 **`coalesce_key`**
  （同卡共用一键，新 cue 收掉排队中的旧 cue——宿主为这件事专门造了尺 `proactive.py:3330-3340`，
  语音模式排队好几个话轮时尤其要吃）；默认 `inject_interval_n` **3 → 8**（同门那个 3 是
  "注入=目录载体"前提下的数，前提换了数就得换）。契约更迭非放宽：`test_turns` 那条默认值门同步改了。
- **顺带白拿**：按分类给目录就不再有"平铺只露最后 80 张"的缺口（陷阱 25 末尾那条已知约束
  对**她选图**这一面自然消解，`catalog_limit_for_model=80` 仍管着逐图清单）。
- **配置键 -1**：`[sticker_manager.awareness].max_recent_lines` 退场（"最近常用"不再进注入，
  留着是死键）；三处同源同步（`core/configuration.py` / `plugin.toml` / `config.example.toml`），
  `AwarenessSettings` 字段数 6 → 5，同步门的计数一起改。主人那份覆盖配置里没写过这键，无残留冲突。
- **测试桩补严**：`FakePush.push_message` 不再宽收 `**kwargs`——按 `SdkContext.push_message`
  的**真参数表**（`inspect.signature` 核过，18 个入参）做白名单，多一个键名就 TypeError。
  v0.16.1 那条教训（"桩宽容=盲区"）在这里的同族复现。
- **工程面一处**：`release_gate` 的复制排除表补 `.pytest-tmp`（手动跑 pytest 用的 basetemp 目录，
  实测被卷进暂存树后 release 门报"gif 压缩比 >200:1"——垃圾目录当弹药，红得毫无线索；
  `.gitignore` 同步）。门本身早就用自己的 `.tmpgate/pt`，这次是补手动那条腿。
- 入口面 ±0、盘形 ±0、i18n 面板文案 ±2（`panel.awareness.note` 两语改写）；测试 **357 → 370 passed**
  （新 `tests/test_tool_surface.py` 十五条）。
- **待实机验收**（并入 v0.13.0 那份未回账清单一起跑）：装 0.17.0 → 面板/日志确认她的工具描述里
  有 22 行分类 → **不点任何注入**、正常聊几句看是否自发配图 → 建一个新分类并收一张图，
  ≤10 秒内描述应出现该类（`sticker_send` 重挂，日志 `sticker_send` 相关无 warning）→
  换个区看目录是否整体跟着换 → 关掉注入子开关（`awareness.enabled=false`）只留常驻面，
  发图频率应当基本不掉——这一条是本轮论点成不成立的判据。

## 0.16.1


v0.16.1「轮次源在真机上根本没读到过」——主人装完 0.16.0 发现面板恒显示「每 3 轮 · 已攒 0 轮」，日志一句就定位了：

- **`SdkMemoryBus.get() got an unexpected keyword argument 'max_count'`**（WARNING，5 分钟节流一次）。
  我抄同门调用形状时顺手把 conversations 桶的 `max_count` 也塞给了 memory 桶。真机上**每一拍都抛**，
  被 `except` 吞成"总线没信号" → 降级时钟接管 → 提醒照旧每小时一次、计数永远 0。
  降级路径按设计接住了，所以表面看"插件还在正常工作"——这正是它最难发现的原因。
- **顺藤摸出一个更老的静默失效**：两个桶的入参**名字没有交集**
  （`SdkMemoryBus.get(*, bucket_id, limit, timeout)` vs `ConversationClient.get(*, conversation_id, max_count, since_ts, timeout)`，
  后者**根本不认 `bucket_id`**），而 v0.14.1 的 `LanlanResolver._from_bus` 给两边都塞了
  `{max_count, limit, bucket_id}` —— 也就是**目标解析的总线那一级从写下起就没通过**，
  全靠第 3 级宿主 HTTP 兜住。现在两个桶各按各的签名读，且过 `asyncio.to_thread` + 0.8s 超时
  （SDK 默认 5s 会吃满面板 context 的 5s 预算）。
- **测试桩是共犯**：`FakeBusNamespace.get(**kwargs)` 什么都收，所以 354 条测试全绿也看不见。
  换成按 SDK 逐字对齐的两个严格桩（`FakeConversationsNamespace` / `FakeMemoryNamespace`），
  传错参数名立刻红；`tests/test_lanlan.py` 补三条钉"各按各的签名""包装记录要 unwrap"。
- **`records_of` 不再丢对象序列**：宿主 `dump_records()` 给的是对象而非 dict 时，原来的
  `isinstance(Mapping)` 过滤会把它们全数丢掉（症状与传错 kwargs 一模一样）。改成统一过
  `unwrap_record`（与 `services/turns.py` 同一把尺，那个私有实现已并到 `lanlan.py` 导出）。
  memory 桶的时间戳键 `_ts` 也补进了 `_timestamp_of`（原来只认 `timestamp`）。
- **面板"已攒 N 轮"口径修好**：后端补一个标量 `turns_since`（优先最近注入的那张卡，
  没注过就取攒得最多的）。原来只回 dict，第一次注入之前 `target` 是空串 → 恒显示 0。
- 三处调用点已拿**真宿主/SDK 的签名**用 `inspect` 对过一遍（参数集合与必填项都核）。
- 测试 354 → 357。

## 0.16.0

v0.16.0「注入换轮次驱动」（主人拍板：只做这一层，面板先不加旋钮）——v0.15.0 那轮改错了地方：

- **实机账本**（同一晚，`%LOCALAPPDATA%/N.E.K.O/logs/`）：44 轮对话（`trigger=turn_end` 计数）
  / 12 次存在感注入 / 3 次 `sticker_send`，且 **3 次发送全部紧跟在注入后 6~30 秒**，
  闸门拒绝 0 次，她一次都没调过 `sticker_list`。措辞不是瓶颈，**提醒覆盖率 27% 才是**：
  `interval_sec` 默认 3600s，而活跃对话平均三分钟一轮——00:10:57 之后提醒彻底停，
  对话却跑到 00:16+ 十几轮。
- **换驱动源**：新增 `services/turns.py` 的 `TurnWatcher`，10 秒轮询 `bus.memory` 的
  `user_message`（宿主 SDK 不向插件派发"助手回复完成"，用户开新一轮是唯一可靠的回合边界代理）。
  记录解包、`_ts`/`lanlan` 键名差异、`asyncio.to_thread` + awaitable 兜底、warning 级失败日志
  全部照同门 `forever_companion/mixins/whisper.py` 的实机形态搬。详见新增陷阱 28。
- **新配置三键**（`[sticker_manager.awareness]`，默认值按同门定案）：
  `inject_mode = "interval_n"`（可选 `every_user_message`）、`inject_interval_n = 3`、
  `min_interval_sec = 60.0`。`interval_sec` 不删，语义收窄成**降级时钟**：总线一次都没读通过
  时退回老行为，好过整条链路静默失声。
- **两把尺分开**：水位（这条轮见没见过）与计数（距上次成功注过了几轮）。计数**只在注入成功后
  清零**，被地板或空库挡掉的轮不烧配额——否则"每 3 轮"会静默退化成"每 4、5 轮"。
- **拍子搬家**：存在感注入从 60s `watch` 拍拆到新的 10s `turns` 拍。60s 的粒度会把连发的几条
  用户轮并成一轮看见；两件事也各兜各的异常（timer 无 watchdog）。
- **面板只加读数**：`节奏` 与 `驱动` 两格（驱动显示"轮次"还是"降级时钟"是这轮唯一的自证证据）。
  旋钮刻意留在配置文件——主人拍板"先只改文件"。
- 测试 306 → 354（新增 `tests/test_turns.py`：解析 10 项 / 水位计数 10 项 / 节奏闸 8 项 /
  端到端 6 项 / 配置读入 8 项）。

## 0.15.0

v0.15.0「积极度进工具描述」（主人拍板：只做这一件，闸门类的一律不动）——实机日志把病灶指清楚了：

- **证据**（`%LOCALAPPDATA%/N.E.K.O/logs/plugin/N.E.K.O_Plugin_sticker_manager_20260918.log`）：
  装机 0.14.0、`set_eagerness` 写入成功（注入字数 1431→1445）、`awareness injected` 6 次、
  `sticker sent` 2 次且**两次都紧跟在注入后 30~60 秒内**、`cooldown/probability/not_enabled` 拒绝 **0 次**。
  → 不是被闸拦的，是**提醒沉底了**：注入默认 3600s/每角色卡一次，聊十几轮后那句话早就不在注意力里。
- **常驻面才是杠杆**：`sticker_send` 的**工具描述**每轮都随工具列表喂给模型。
  新增 `Library`-无关的 `_apply_send_tool_tier()`：先 `unregister_llm_tool` 再
  `register_llm_tool`（名字已在册时重注册会撞 EntryConflictError），把档位许可句拼在静态正文后面；
  三个调用点接上：`on_startup`、`on_config_change`（主人手改 toml 也算）、`set_eagerness`（面板切档当场生效）。
- **档位语义收进新模块 `core/eagerness.py`**：一个档位两个受众——`injection_guidance(tier)`（注入意愿段，
  可长）与 `send_tool_description(base, tier)`（工具描述追加句，必须短）。两处许可强度同向、不许各抄一份。
- **拆掉 eager 的自授退路**：v0.14.0 那句结尾写着"…或干脆不发"，等于给她一个合法的"那算了"；
  现在换成"拿不准用 group 让组内帮你选一张"。有门钉着：三档文案里都不许再出现"干脆不发"。
- **三条底线**：SDK 注册面缺席只记日志不动手；描述没变不折腾 IPC；**重注册失败先把旧描述装回去**
  （宁可她读到旧档位的句子，也不能因为换档没了工具），连回滚都失败也只是日志一条、交给 5 分钟工具心跳。
- **测试隔离补一处真漏洞**：解析链的 HTTP 兜底会打 `127.0.0.1:48911`——宿主与测试同机并存时，
  "没有目标"的用例竟然问到运行中的宿主拿到 `YUI` 而**误判通过**。新增 `lanlan.HTTP_ENABLED`
  与 conftest 的 autouse 夹具默认关掉，要验这一级的用例自己开并打桩。
- 入口面 ±0、配置 ±0、i18n ±0、盘形 ±0；测试 **297 → 306 passed**
  （新 `tests/test_eagerness_tool.py` 八条 + `test_eagerness.py` 三条挪过去 + 隔离夹具）。

## 0.14.1

v0.14.1「注入目标解析链」（实机回账修 bug：主人切完档位点「现在注一条」报 `awareness_no_target`）：

- **根因两条，都钉死在代码事实上**：① 宿主给面板动作派发的 `_ctx` 里**只有 `run_id`**
  （`plugin/server/application/plugins/ui_query_service.py:1757-1762`），所以面板上的
  「现在注一条」与「发到聊天」拿不到角色卡；② 旧实现退而读 `bus.conversations`，而
  **普通聊天话轮不写 conversations 存储**（只有主动离线轮次 publish，
  `main_logic/omni_offline_client/_lifecycle.py:94`），且只认 `lanlan_name` 这个键
  ——memory 桶里的角色键叫 `lanlan`。两条叠起来 = 面板按钮必然 no_target。
- **新增 `services/lanlan.py`**：四级解析链 `入口 _ctx` → `总线记录`（conversations 的
  `lanlan_name` + memory 的 `lanlan`，取时间戳最新）→ 宿主 `GET /api/characters/current_catgirl`
  → `ctx._current_lanlan` 粘滞值。整链 best-effort，任何一级炸只丢那一级。
- **两条刻意的不对称**：总线结果**每拍现读不缓存**（目标随对话漂，缓存它等于把她钉在上一张卡上，
  有门钉着）；HTTP 结果 15s TTL 且**失败也缓存**（宿主不可达时不该让每次面板刷新白等超时），
  超时 0.6s——面板 context 总预算 5s，兜底必须比它短。
- 四个调用点统一走 `_resolve_lanlan`：面板试发 `send`、面板注入 `awareness_now`、
  dashboard 快照的 `lanlan`（现在能显示"她在跟谁说话"了）、她的工具 `sticker_send`。
- `Awareness` 新增可选 `resolver=` 注入（不传则自建，老构造形状兼容）；
  `latest_lanlan`/`lanlan_of`/记录规范化从 `services/awareness.py` 搬进新模块（awareness 只管节奏与投递）。
- 测试 **286 → 297 passed**（新 `tests/test_lanlan.py` 十一条：链序、目标漂移、HTTP 缓存与失败缓存、
  总线读失败降级、纯函数三形状）。入口面 ±0、配置 ±0、i18n ±0、盘形 ±0。

## 0.14.0

v0.14.0「配表情积极度」（主人拍板：走档位而不是裸概率、控件用 Select）——回答的是"怎么让她真的开始配图"，不是"怎么拦她"：

- **调研结论先记账**：宿主今天**没有**"她刚回复完"这个插件可见时刻——普通聊天话轮只走 WS 帧 +
  `sync_message_queue`（`main_logic/core/turn.py:1449/1544/1568`），`lifecycle_bus` 明写无 main→plugin 通道；
  `@message` 注册了但没有派发点（`plugin/core/communication.py:496-506` 路由表不含它），
  `@hook/before_entry/after_entry/around_entry/replace_entry` 只挂元数据、`hook_executor` 是
  `NotImplementedError`。所以"跟着她的回复自动附带一张"在插件侧做不成确定事件——
  **不做替发，改她的意愿**。（图本来就是独立气泡：`services/sender.py:234` 走
  `visibility=["chat"]` 的 push，不是并进她那条。）
- **新配置一键**：`[sticker_manager.send].eagerness = "natural"`，三档 `reserved / natural / eager`。
  三处同源同步（dataclass / plugin.toml / config.example.toml），`SendSettings` 字段数 6 → 7。
  读入尺 `_as_choice`：非字符串/不在册/空白一律回默认档——配置写错不许让她的行为变野。
- **档位只管一件事**：存在感注入里的**意愿段**文案（`core/awareness.py` 的 `_WILL`）。
  默认 `natural` 的意愿段与 v0.13.0 那句**一字不差**（零静默行为变更，有门钉着）；
  `reserved` 加"多数时候纯文字就够了"；`eager` 换成"情绪对得上就配一张，别在心里过三遍才发"。
  **发送层的四把尺一律不吃档位**：`cooldown_sec` / `recent_dedup_count` / `probability` /
  `probability_reuse_sec` 各自独立——节奏段文案（"被拒了就正常用文字回，别重试"）三档共用，
  免得调档把闸门的说明也一起漂了（陷阱 26）。
- **入口面 +1**：`set_eagerness`（`@ui.action` + `@plugin_entry`，`input_schema` 带 `enum`）——
  与 `switch` 同模式：写配置走 `config.set`，面板**不声明 `config:write`**；
  非法值直接 `invalid_value` 拒（不许顺手兑成默认档，那是主人的决定被静默改掉），
  盘写不进去如实回 `config_unavailable`。
- **面板**：存在感卡里加 `Field` 包 `Select` 三档（选项文案是人话："矜持：没有正合适的就不发" /
  "自然：贴切就发（默认）" / "爱发：情绪对得上就配一张"），help 明写"冷却、最近不重复、
  概率闸不吃这一档"；当前档由 dashboard 快照新键 `eagerness` 回填（写完 `refresh()` 取服务端真值，
  不拿本地乐观值）。
- i18n +10 键（zh-CN/en 同键集，尾部纯插入）；测试 **275 → 286 passed**
  （新 `tests/test_eagerness.py` 十一条：读入收敛三门 + 注入文案四门 + 入口/快照四门）。
- **测试桩修一处真错**：`FakeConfig.set` 过去把点分路径写成平键（`sticker_manager.enabled` 只有一层
  所以看不出来），两层路径 `sticker_manager.send.eagerness` 就读不回来——改成与真宿主
  `_set_by_path` 同形的逐层嵌套。
- 实机验收追加一项：面板切到「爱发」→ 立刻点一次「现在注一条（调试）」→ 随便聊两句，
  她配图的频率应当肉眼可感地上去；切回「矜持」再点一次注入，应当明显收住。

## 0.13.0

v0.13.0「官方包打标 + 标签下发尺」（J-3，主人拍板：走内容轮、粒度"改开"、拿不准的按大致意思即可）——官方区从"190 个文件名"变成她看得懂的收藏：

- **官方包重打**：190 张全部**分好 22 类 + 逐张标了梗义(caption)/检索短词(desc)/同义词(tags)**，
  包带 `pack_version: 1`（34.8MiB，包 sha256 前缀 `23fc5cb0e82f`）。目录行正文实测从
  「173_一切都好 4」变成「一切都好（冒烟版）」这类人话；`sticker_list` 无词时的分类概览
  从 0 行变 22 行（每行都是"什么时候用这一类"）。
- **标签下发尺（本轮的真改动）**：J-2 留的洞——`import_pack` 对同指纹只计 duplicates 就跳过、
  `official_seeded` 又是一次性台账，所以**重打的官方包对老装机一张也不会变**。新尺三件：
  包带 `pack_version`、库记 `official_pack_version`（catalog 顶层）、
  `Library.refresh_official_labels()` 按**内容指纹**把官方区条目的文本刷成包里的版本。
  `seed_official` 的 `already` 分支照样走一次刷新（"只播一次"管的是进图，不管措辞）。
- **让位尺是字段级的**（`Sticker.owner_edited: [字段名]`，`Library.update` 顺手记账）：
  主人碰过 desc/caption 任一，**两者都归主人**（回落尺 caption>desc，只保一个等于没保），
  但分类与标签照刷——整条跳过会让这张永远拿不到新分类。分类说明仍只补缺不覆盖。
- **只动文本**：图片文件、id、启停、使用台账、区归属一律不碰；写盘失败不盖版本（下次启动自然重试）。
- **判断与 IO 分层**：新增 `core/labeling.py`（纯函数：配对、让位、同值不进补丁），
  `services/library.py` 只管读 zip 与落盘——大文件不再长新逻辑（拆分另立一轮，见 DESIGN 交接）。
- **盘形纯插入 + schema v2→v3**：顶层可选 `official_pack_version`（只认正整数）、条目可选
  `owner_edited`（只认白名单字段名，陌生名字 load 时丢）；两键都只在非空时写，旧库盘形一字不变。
- dashboard 快照 `official` 三键 → **四键**（+`pack_version`，排障用，面板不读）；
  `seed_official` 回包新增 `refresh` 子字典（status/version/refreshed/skipped_edited/unmatched/groups）。
- **入口面 ±0、i18n ±0、配置三处同源 ±0**：本轮不加新入口、不加新配置键——
  下发是启动时自动做的事，不该让主人多点一次按钮。
- 测试 **261 → 275 passed**（新 `tests/test_official_refresh.py` 十四条：下发尺七门 +
  宽松读两门 + 纯函数判定五门；改两处按设计该变的断言：`already` 回包形状、迁移后盘形版本号）。
- 真包对账（`sticker_pack_lab/tools/j3_verify_upgrade.py`，旧包从本仓 git HEAD 取）：
  装机态 190 张占位标签 → 换新包 → `refreshed=190 skipped_edited=1 unmatched=0 groups=22`，
  一张图不用重进、主人的话保住、版本落盘、同版本重放短路。
- 下一轮就位：v0.13.0 包实机验收（含 J-2 那份未回账清单：导真包→播种→实发一张贴尺 gif→拆官方区→恢复闭环）；
  分类改名轮（轮 I 欠账 3A）；`services/library.py`(1250 行)/`core/catalog.py`(524 行)/`__init__.py`(2007 行) 拆分轮。

## 0.12.0

v0.12.0「官方区内置」（J-2，主人拍板 P2A 完全内置 + P3 只播一次+恢复按钮）——她的收藏间开张即存货：**随包官方收藏 190 张，首启自动播种进「官方」区**：

- **官方包进 payload**：`official/official_pack.zip`（190 张压后 gif，manifest v3，34.8MiB）——
  甲方案压图尺在 `sticker_pack_lab`（每张 ≤250KiB，内联预算边缘，重刀零命中；
  gifsicle win 构建不认拼接帧选择/逗号列表，减帧走 explode→merge 三段路，已入该仓脚本档）。
  尺在 `core.catalog.OFFICIAL_PACK_RELPATH`（打包/播种/入口三处同源一把尺）。
- **播种尺（`Library.seed_official`）**：只播一次（catalog 顶层 `official_seeded` 台账，**只认布尔真**——
  脏值一律当未播，宁可重试不可假装封过）；台账只在包干净（rejected+failed=0）时盖，半截包下拍重试；
  入库走 `import_pack` 全套尺（魔数/指纹查重/原子写盘），重放不重入；**同名收编**：主人手建的「官方」区
  被补打 `builtin` 位就地收编，不造重名区；区的真身尺是 `builtin` 位不是名字（改名后靠位不靠名）。
- **不抓台**：只有播种前全库零图（新装态）才默认激活官方区；旧库升级官方区静默躺在 tab 里，
  她的世界不动（陷阱 23 的 `active_pool()` 尺天然接管：没激活就是没存货）。
- **入口面 +1**：`zone_restore_official`（服务端 120s 与 `LONG_CALL` 同尺，陷阱 19 对偶）；
  新码 `official_pack_missing`。`on_startup` 里播种炸了不拦启动（台账未盖下次自动重试）；
  startup 回包与日志带 `official_seed` 状态（absent/already/seeded/restored/io:码）。
- **面板**：区 tab 官方徽章（「 ·官方」）；官方区不在册且随包在——tab 尾出「恢复官方收藏」
  （dashboard 快照新增 `official:{pack,zone,seeded}` 三键当判据）；拆官方区同样 3 秒闸（J-1 现尺，无特例）。
- **盘形纯插入**：zones 条目可选 `builtin:true`、顶层可选 `official_seeded:true`——只在真时写键，
  未播种库的盘形一字不变（schema 仍 v2，不升号：旧 reader 遇新键自然忽略）。
- i18n +6 键（zh-CN/en 同键集，尾部纯插入）；plugin.description/short_description 双通道（toml + i18n）同改。
- 测试 **250 → 261 passed**（新 `tests/test_official_seed.py` 十一条：生命周期五门 + 宽松读两门 + 入口/快照/启动四门）。
- 下一轮就位：v0.12.0 包实机验收（首启播种 190 张 + 实发一张贴尺 gif = spike 乙补票 + 拆官方区→恢复闭环）；
  J-3 官方 190 张分区打标（`sticker_pack_lab`）。

## 0.11.0

v0.11.0「区」（J-1）——三层管理面的地基：**区 → 分类 → 图**，她只感知激活区（主人拍板 Q1b/Q3/Q4/P1）：

- **区是显式对象**（`catalog.json` 顶层 `zones:[{id,name,desc}]` + `active_zone` + `group_zone:{分类名:区id}`，
  schema v2）：区用**内部 id** 引用，所以**改名白送**（`zone_rename`）——分类改名欠的债（轮 I 拍板 3A）不新埋。
  分类名**全库唯一（跨区也算）**：她的世界按名字过活，一个名字不能有两个家。
- **她的世界 = 激活区**（P1 一把尺）：`sticker_list`/`sticker_send`（含显式 id，点进非激活区也算没找到）/
  query 检索/awareness 注入文案全部只从 `Library.active_pool()` 拿——非激活区的图与分类连名字都不露。
  新防回归门 `test_zones.py::TestInactiveZoneInvisibleToHer`（切区后镜像翻转）。
- **入口面 +5**：`zone_create`/`zone_rename`/`zone_set_desc`/`zone_activate`/`zone_remove`（连带拆区内
  全部分类与图，服务端 timeout=120s 与 `LONG_CALL` 同尺；最后一个区不许拆 `zone_last`；拆的是激活区
  则自动激活剩下第一个）。新码：`zone_required`/`zone_exists`/`zone_not_found`/`zone_last`。
  `add`/`group_create`/两个导入入口的 input_schema 补 `zone` 声明（不补会被 additionalProperties 拦）。
- **旧库宽松迁入**（v1→v2）：无 zones 键 = 建默认区（名「自制区」，这是数据不是文案）、
  全部图与分类入籍、**load 当场补写一次盘**（失败也只是下次再迁，幂等）；手改坏 zone 由 load 兼容
  与 `repair()` 区卫生收拾（不进体检三格计数）。
- **面板：区 tab 条**（`ui/components/zone_bar.tsx`，八块）：tab 带张数、「她在用」徽章、就地建区/改名/改说明；
  **拆区 3 秒防误删闸**：确认行先摊服务端张数（zone.total，陷阱 22 对偶），确认键 3 秒后才可点
  （纯前端纪律，服务端不装慢）。所有写入（建类/收图/直传导入）落**正在看的区**；
  收图目标区走 `viewZoneRef`（与目标分类同一条闭包坑）；聚焦卡/批量条的分类 Select 只列**图所在区**的分类。
  视图被删自动接回激活区，不留幽灵 tab。
- i18n +26 键（zh-CN/en 同键集，尾部纯插入）；文档三本账同步（DESIGN 能力面/陷阱区新 23/README）。
- 测试 **242 → 250 passed**（新 `test_zones.py` 八条：迁移两条、生命周期三条、她的视角三条）。
- 下一轮就位：J-2 官方区内置（spike：90MB 包导入 + 250KiB gif 实发），J-3 官方 190 张 GIF 分区打标。

## 0.10.3

v0.10.3「导入出口只留一个」——主人拍板的面板文案/操作轮（纯视图轮，**后端零变化**）：

- 库卡改名：「她的表情库」→**「管理表情包」**（`panel.card.library` zh/en 与 tsx defaultValue 同改）。
- 直传按钮改短：「选择套图包导入」→**「导入」**（busy 态「上传中…」不变）。
- **收件箱从面板彻底退场**：「导入收件箱 (N)」按钮与 `{path}` 路径提示行删除；
  `library_model.ts` 的 `importInbox` 闭包随之退场（不留死代码，同 guessDesc 先例）。
  服务端 `import_inbox` 入口、`panel.inbox.*` 键、陷阱 13 处置纪律全部保留——
  收件箱降为纯服务端旁路（放进目录的图仍能被入口整批收，只是面板不再给入口）。
- `shared.ts` 的 `State.inbox` 字段退场（后端仍发，注到 usage 同一条注释里）。
- 验证：五门全绿（pytest 242 passed 不增不减；release `payload_hash_verified=True`；hosted-tsx 过）。

## 0.10.2

v0.10.2「台账卡退场」——主人拍板：面板不再展示「她最近用过的」（纯视图轮，**后端零变化**）：

- `ui/components/usage_card.tsx` 删除；`ui/panel.tsx` 装配去位（台账卡与 Divider 间的空 Stack 一并收掉）；
  `ui/shared.ts` 退场 `UsageRow` 类型与 `State.usage` 字段（留注释说明后端仍发）。
- **后端不动**：`history` 入口、`state.usage`、`usage.json` 全部保留——台账的正职是跨轮去重
  （`recent_dedup_count`）与最近爱用排序（目录/注入文案），展示只是附带面；重启不失忆纪律（陷阱 9）不变。
- i18n：`panel.card.usage` / `panel.usage.*` 退场键按仓内先例留档不删；
  `plugin.description` 双通道（toml + zh-CN/en）同步改写（不再承诺「面板能看到最近爱用」）。
- 顺手：`Translate` 的 params 从 `Record<string, any>` 收紧为 `Record<string, unknown>`（hosted-tsx 门实测通过）。
- 验证：五门全绿（pytest 242 passed 不增不减；release `payload_hash_verified=True`；hosted-tsx 过）。

## 0.10.1

v0.10.1「面板拆分」——纯架构轮（主人选定方向：表情包管理类问题与面板整体管理的地基）：`ui/panel.tsx` 从 1924 行收敛为 273 行**装配骨架**，其余按 our_life v0.6.0 的拆分先例落位，**行为零变化、后端零变化、错误码零变化**：

- **文件落位**（house style 对齐 our_life：shared + components/**，只用具名导出，
  单绑定期声明形态——链接器拒 re-export/`export *`/导出列表）：
  - `ui/shared.ts`——类型（StickerRow/Section/State/Surface…）+ 常量（MAX_BATCH_FILES /
    MAX_STICKER_BYTES / LONG_CALL）+ 纯函数（extractCode/formatTime/readAsDataUrl/
    dataUrlToBase64/callAction/categoryOptions/buildSections）；
  - `ui/preview.ts`——预览缓存、并发尺限 2 的懒加载调度、IntersectionObserver 共用观察器、
    `useStickerPreview`（格子与聚焦卡仍共用一把尺，分段协议不变——陷阱 14）；
  - `ui/library_model.ts`——库卡动作模型 `useLibraryModel(surface)`：搜索/勾选/批量、
    收图 ref 纪律（轮 I）、直传分块、导入导出体检、建类/删类/组说明编辑全部搬入，
    不放任何呈现 JSX；
  - `ui/components/`——sticker_tile / focus_card / usage_card / awareness_card /
    batch_bar / category_section / library_toolbar 七块（awareness 的 ping 与调试反馈
    自治进卡内——与库卡动作没有对偶，不硬并进 model）。
- **i18n 契约门同步扩扫**：`tests/test_i18n_contract.py` 从 `ui/*.tsx` 顶层 glob 改为
  递归扫 `ui/**` 的 tsx+ts——拆分后键分散在新文件里，不扩面等于让新文件的键绕过门
  （our_life 同名门的 rglob 同款思路）。`categoryOptions` 的参数从 `translate` 归一为 `t`，
  让它的 `panel.group.none` 重新进门扫描面。
- **一处小形态修正**：`CategorySection` 的 props 声明补 `key?: string`（hosted-tsx 真跑
  类型检查，`key={section.key}` 要有槽位——本仓 StickerTile/FocusCard 同款先例）。
- 依赖账：运行时 11 文件 ≪ 32 文件 / 512 KiB 上限（hosted-tsx 门实测通过）；
  发行包 payload 含全部新文件（实测列包）。
- 验证：pytest **242 passed**（门数不变）；五门全绿
  `check -r` `payload_hash_verified=True`。

## 0.10.0

v0.10.0「分类优先」——轮 I：分类从「图的附带属性」升成**显式对象**（主人要求重设计分类系统；本轮动后端，不是纯视图轮）：

- **先立分类，再往分类里收图**：新入口 `group_create(name, desc)`（名字必填、说明可留空）与
  `group_remove(name)`；空分类从此存得住——`catalog.json.groups` 里 `{名: ""}` 的含义改成
  「在册但未写说明」，`load()` 不再丢空说明、`set_group_desc("")` 不再删键
  （**清空那句话≠拆掉这个分类**，拆它只用 `group_remove`）。在册名判定收敛到
  `Library.group_names()`（显式 ∪ 有图在住的隐式）一把尺，入口层与创建查重共用。
  新错误码 `group_exists`：重名（**含名字已被图住着**——那种情况主人该去「编辑说明」，
  否则会一个名字两张卡）；`group_not_found` 复用于删不存在的分类。
- **删分类 = 连带删它里的图**（主人拍板 1C）：`Library.remove_group` 先改内存、`save()` 成了再删文件，
  失败回滚内存（同 `remove()` 纪律，不留「目录没这张、盘上还在」的暗孤儿，孤儿收尾仍归 `repair()`）；
  服务端 `timeout=120` 与面板 `LONG_CALL` 同尺（陷阱 19）。**不做改名**（3A 拍板：牵动批量改写 +
  说明迁移 + 台账语义，单独立轮；现阶段改名 = 新建分类 + 批量移入）。
- **面板：「收一张新表情」整卡拆掉**（AddForm 249 行离场）——四个逐图字段与「收进表情库」按钮
  在这张卡里是主人用起来最累、收益最低的一段：逐图编辑聚焦卡早就有（v0.9.1），
  整批标签/分组批量条也早有（v0.7.0 `batch_update`）。台账卡因此升占整行。
- **收图入口下放到分类块头**：「收图进这一类」多选文件，目标分类就是这一块（全库共用**一个**隐藏
  `input[type=file]`，目标走 `collectTargetRef` 传递——点按钮→`click()`→`onChange` 三步里闭包
  可能抓到旧 state，ref 当场生效）。想逐图补什么，收完点开图在聚焦卡里改。
- **收图不再拿文件名当描述**（行为变化，如实记）：`add(desc="")`，目录正文按轮 F 那把尺回落到
  分类说明。为什么：外部包里哈希名一堆（`0FFD1AFA…jpg`），文件名描述会把自己压到主人写的分类说明
  头上。面板镜像函数 `guessDesc` 随之退场，陷阱 11 从「两份一起改」改成「只剩 Python 一份」。
- **逐图归类 Input → Select**：聚焦卡与批量条两处共用 `categoryOptions()`（选项 = 未分组 + 已有分类），
  不再能手打新分类名——自由输入会静默立一个没说明的隐式分类，把「先分类后收图」戳穿。
- **空分类显示给主人、对她隐形**（陷阱 21 成文；反转 v0.8.0「空区块不出现」）：面板区块与
  `state.groups` 带它出（否则建完就消失等于没建），而 `format_group_overview`、
  `sticker_list(group=)`、`sticker_send(group=)` 的候选组名全部只从**有图的**贴纸算——
  她选中一个空分类就是鬼打墙。新边界：本来就没图的分区块在，被搜索筛空的有图分区块仍不在。
  破坏性确认摊的是服务端张数 `section.total` 而不是搜索筛过的 `rows.length`（陷阱 22）。
- 面板术语统一叫「分类」（只动面板文案；模型面目录格式 `套图：G`／【套图分类】有测试钉着，本轮不动）。
- i18n +30 键（zh-CN/en 同键集，文本级族内插入）；若干值改写（术语统一、`group_set_desc` 的空串语义订正、
  `plugin.description` 双通道同步）；退场键按仓内先例留档不删（`panel.card.add`、`panel.add.*`、
  `panel.batch.label/busy/done`、`panel.group.label`、`panel.batch.group_ph`）。
- 测试 **235 → 242 passed**：`tests/test_groups.py` 新 `TestCategoryLifecycle` 八条门（空分类在册且
  活过重载、两类撞名、建完即可写说明、入口形状与 `state.groups` 的 `count: 0`、删空分类、
  删分类连带删图与文件——数目录也数磁盘文件、**空分类不进她的目录也发不出**的防回归门），
  另把两条旧语义门改正（`set_group_desc("")` 保留键、poisoned 段空说明保留键）。
- 五门全绿（pytest / ruff / check / release / hosted-tsx），`check --release` `payload_hash_verified=True`。

## 0.9.1（历史开发版本，重定号之前）

v0.9.1「墙不糊脸、弹窗不再被裁」——实机截图钉出的两只观感雷（纯视图轮，后端零变化）：

- **拉伸感真凶：小图被放大展示**。测试库里原图只有 160×160，v0.9.0 的 6 列墙在 2K 窗口
  里每格≈365px，浏览器硬放大 2.3 倍糊脸。改：格子 `object-fit: scale-down`（只缩不放，
  小图按原尺寸摆、四周留底色边）；墙改 `auto-fill minmax(128px, 176px)` 自适应方块轨道，
  不再随窗口宽度无限长大；聚焦卡大图同样不放大（封顶 420×380，小图原尺寸清晰）。
- **弹窗被关进卡片里（平台级坑，DESIGN 陷阱 20 成文）**：kit 的 .neko-page/.neko-card
  挂着 `animation: neko-fade-up ... both`，关键帧终帧 `transform: translateY(0)` 非 none、
  fill 永久生效→祖先成了 position:fixed 的包含块；再叠 .neko-card 的 overflow:hidden，
  实机表现为：遮罩只压暗表情库卡、弹窗底部被卡片边界裁没。**插件侧修不动平台 CSS，
  改用「聚焦视图」根治**：点格子→库卡顶部就地展开**聚焦卡**（大图+全属性+动作排，
  「编辑」同卡就地切表单，零 overlay 零 fixed，任何库长度免疫）；再点一次同一格或
  「返回墙」收起；被删/被筛掉自动收起不留幽灵卡。看做分离语义不变，只换承载。
- 取图逻辑抽成共用 hook `useStickerPreview`（格子=视口懒加载+排队，聚焦卡=直接排队；
  同缓存同并发尺；alive 护栏防卸载后 setState）；kit Modal/ImagePreview 退场（import 清干净）。
- i18n +2 键（panel.focus.title/back）。

## 0.9.0（历史开发版本，重定号之前）

v0.9.0「表情墙」——轮 G-2：格子去表单化，参数全部收进详情弹窗（纯视图轮，后端零变化）：

- **外面只剩图**：卡片从“横躺的表单”改成方形图块（6 列墙）：零文字零按钮；
  仅保留两样——左上角极小勾选框（批量刚需，主人拍板）、禁用态整块压暗+“已禁用”角标；
  选中态图块描蓝边。
- **id/发出次数/最近发出/描述/梗义/图内原文/分组/标签/入库时间 + 发到聊天/编辑/禁用/删除**
  全部搬进**点击弹出的详情弹窗**（看做分离：弹窗=大图+全属性+动作排；点「编辑」再开
  第二层编辑表单，那张表单本身没改）；弹窗内动作失败在弹窗里报，弹窗关着时才在图块下报。
- **预览改自动懒加载**（拍板 ③A）：IntersectionObserver 滚进视口（提前 240px）才拉图，
  叠一把**全局并发尺限同时 2 张**——单张预览是分段的串行调用，几百格同时开跑会踩挤
  插件子进程（原“点按钮才加载”就是为防这个，现在换了更精确的闸）；拿不到观察器环境
  直接排队兜底，宁多拉不漏图；预览缓存/失败记空串不重试风暴的既有纪律不变。
- i18n +10 键（zh-CN/en 尾部纯插入）；`panel.thumb.load`/`panel.batch.pick` 退场留档。

## 0.8.0

v0.8.0「收藏间有了楼层」——轮 G：面板分类分区视图（纯视图轮，后端零改动）：

- **浏览主形态从“chips 过滤 + 平铺大网格”改成“按组分区”**：一个分组一个区块
  （组名·张数 + 一句组说明 + 块内网格），一路滚下去就是她的收藏间目录；
  “未分组”也占一个区块沉底，天然暴露还有多少张没归家。
- **操作入口上到区块头**：全选本组（再点取消）、编辑说明（就地一行，同一时刻只开
  一个编辑态）；原 chips 排与 chips 下的说明输入框退场——分区后它们是冗余控件。
- **搜索语义升级**：跨区块过滤，组名/组说明命中→整组都在，否则只留命中的图；
  空区块不出现；有库但筛选无命中时给“换个词试试”的空态（与空库文案分开）。
- 区块内卡片、批量条、直传导入、台账、存在感卡等其余功能面**全部不动**；
  排序沿用现有尺（组间按目录同序字母、组内按库序），刻意不加折叠、不加“试发本组”
  （主人拍板：几百张内一路滚够用，分区头不另升发送入口）。
- i18n +8 键（zh-CN/en 同步，文本级尾部插入）；后端入口/配置/错误码零变化。
- 本轮占用“轮 G”序号：两步预检候选顺延为轮 H（study §5 同步改名）。

## 0.7.1

v0.7.1「大包装不再报假死」——面板超时尺对齐（迟到的审计抓到的一只真雷）：

- 宿主桥接客户端默认只等 30s（runtime.js 实测），而 import_inbox / export_pack /
  import_upload_finish 服务端都是 timeout=120——大包导入超 30s 时服务端在继续、
  面板先报失败，主人重试还可能撞进半成品会话。现在 `callAction` 支持 options，
  这三条长任务统一传 `{timeoutMs: 120000}`；短任务（含逐块上传，单块毫秒级）维持默认。

## 0.7.0

v0.7.0「打标不折腾」——轮 F：对齐参考系统的“分类=描述”心智，把逐图文本从门槛降为可选：

- **逐图 desc 不再必填**：空描述也能收——目录行正文回落链变成
  `caption > desc > 分组说明 > 「套图「G」里的一张」> 「未标注」`（一把尺，缺口如实标出来而不是造假描述）；
  超限仍拦（desc_too_long），update 传空串 = 清除。`desc_required` 自本版本退场（契约表已标）。
- **分组说明（catalog.json 顶层 `groups`）**：`group_set_desc` 入口/面板 chips 下一行编辑框——
  一句“什么时候用这一组”就是她看到的分类目录正文（≤300；只能给有图在用的组写，空组存不了图）。
- **两级目录**：`sticker_list` 无搜索词先回【套图分类】再回【条目】；`group=` 只看那一组。
  存在感注入同步：分组概览插在指南与常货之间，行形状与 sticker_list 同一把尺。
- **组内选图**：`sticker_send(group=)` ——她只拍板“这一组调性对”，哪张由系统定：
  候选池先过同一把近期去重尺再随机（剔到空→`recent_repeat`），撞多个组名回
  `group_candidates` 让她二决；id > group > query 优先级；节奏三闸（去重/概率/冷却）照常把关。
  随机尺 `_GROUP_PICK` 模块级可注入（对齐 _RNG 纪律）。新工具面拒因：`group_not_found` /
  `group_empty` / `group_candidates`。
- **批量整理**：`batch_update`（加/删标签、移组、启停，缺席不改）与 `batch_remove`；
  面板每张卡加勾选框，选中后弹批量条；删除前先把**精确张数**摊在确认里（参考系统的破坏性确认）。
  新码：`batch_empty` / `batch_noop`。
- **manifest v3**：顶层 `groups` 随包迁移；导入**只补缺不覆盖**——包里组话不能消音主人已写的；
  旧包（v1/v2）无此键自然回空，宽松兼容。导出面同步携带。
- 测试 218 → 235：回落链/分组概览形状/库层持久+毒数据/入口守卫/批量三面/组内选图五面
  （钉选、去重逐轮排池、force 放池、同名歧义、id 优先）/两级目录/manifest v3 随包+不消音。
- 顺手修掉本轮改造时 LSP 在既有代码上抓到的两处：awareness 时间戳的巨整数
  OverflowError（与轮 D 配置加固同纪，坏时间戳当 0 不炸整拍）、bus 回包鸭子形状的
  Any 标注（行为不变，静态跟得住）。

## 0.6.0

v0.6.0「导入不再找隐藏文件夹」——轮 E：面板选择文件直传套图包：

- **背景（真实踩坑）**：收件箱是唯一包入口，但运行时数据目录藏在 AppData 深处，
  用户把 zip 放在显眼的构建目录里点「导入收件箱」×4 全部空转——“导入”应该是
  选文件，不是“服务端读一个主人找不到的目录”。
- **新链路**：面板「选择套图包导入」→ 原生文件选择器（accept `.zip`）→ 分块直传。
  三个新入口 `import_upload_start`（开会话，回 session + 每块原始字节上限）→
  `import_upload_chunk`（按 seq 从 0 连续追加 base64 块）→ `import_upload_finish`
  （收尾走 import_pack 同一把尺，回四类计数）。面板进度/结果文案复用 inbox 同款报告形状。
- **为什么分块**：面板→entry 的 args 与预览回包同受 ZeroMQ 单帧上限 4,784,128 字节
  （陷阱 14 的反方向），几 MiB 的包整块 base64 会被直接拒；块大小由服务端 start
  回包定（3MiB 原始→≈4MiB base64，封套留余量），面板不硬编码。
- **会话纪律**：只存本进程内存 + `data/uploads/.sid.part`；重启即作废，重选文件即可
  （不做断点续传：不值得为短命交互引入复杂度）；seq 乱序/超限/写失败一律**作废会话**，
  不静默拼接；finish 后暂存体无论成败都删；新开会话前顺带回收过期死体（内存会话按 at、
  盘上残留按 mtime，24h）。名字带路径形状直接拒（`upload_not_zip`），不采纳
  safe_member_name 的“剥目录”宽容——面板传来的就该是裸文件名，诚实 > 宽容。
- **收件箱降为高级旁路**：入口保留（脚本化批量/无面板场景仍可用），面板提示文案
  改为首选选择器。导入的图片多选通道不变。
- 顺手修一个真雷（lens 在改动文件上抓到的既有缺口）：`Sticker.from_dict` 的
  时间戳/计数字段改用 `_lenient_float/_lenient_int`——手改坏的 `catalog.json` 过去会把
  整本库打成不可加载（load 的条目循环没有逐条 try，宽松承诺必须在 from_dict 内兑现）；
  nan/inf/巨整数/类型错全部回 0，好值不受牵连，钉 1 个专项测试。
- 新稳定码七个：`upload_not_zip` / `upload_session_unknown` / `upload_seq_gap` /
  `upload_chunk_bad` / `upload_too_large` / `upload_empty` / `upload_write_failed`（双语 i18n 同步）。
- 测试 207 → 218：会话规则十门（拒非 zip/manifest 包全链路小块多块/乱序作废/超限作废/
  空会话/假 zip 诚实计败/过期回收/入口往返/入口参数守卫/实例隔离）+ from_dict 毒数值一门。

## 0.5.0

v0.5.0「她的发送有节奏」——轮 D 前半：跨轮去重 + 概率闸门（学习 外部系统的节奏纪律，
机制见 docs/sticker-system-study.md；它没有的持久台账是我们能做得比它好的地方）：

- **跨轮去重（轮 D①，`[sticker_manager.send].recent_dedup_count`，默认 5，0=关）**：
  同一角色卡最近 N 张**成功发出**的图在"她自主选图"时不再出现——query 候选池先剔除再
  判定（剔到空但原池有命中→如实回 `recent_repeat`，和 `no_match` 分开：前者让她换一张，
  后者让她换词）；显式 id 在发送层被同一把尺拦下（tool 以外没有第二条能绕的通道）。
  数据源是**持久台账**："最近发过什么"是事实记忆，重启不该失忆——与冷却（内存表，
  陷阱 9）是两个维度。面板"试发"（source=panel）不拦：那是主人的直接动作。
- **概率闸门（轮 D②，`probability` 默认 1.0=关，`probability_reuse_sec` 默认 60）**：
  她想发图时按概率放行，未中则该轮回落纯文字（`probability_declined`，台账可见）。
  **掷一次存判定、复用窗口内不重掷**——multi_candidates→拿 id 二次定夺是同一次意愿的
  延续，不配第二次骰（外部系统的 p² 教训直接继承）；判定缓存是内存表，重启重掷。
  默认关：闸门会拒绝她已下的决定，属行为干预，主人显式打开才生效（与参考系统
  probability=100 默认同哲学；总开关仍是 fail-closed 那道）。
- **`force` 绕行（新工具参数）**：主人点名要再看某张时带 `force=true`，同时绕过去重与
  概率闸；**冷却不绕**——那是防刷屏不是表达问题。
- **软提示同层告知**：awareness 中段新增"刚发过的会被挡/被拒了就文字回、别重试"，
  尾段新增 force 通道告知；`sticker_send` 工具描述与参数说明同步；两个新拒因码带
  面向模型的二段指引 hint（同 multi_candidates 纪律，不进 i18n）。
- 新稳定码两个：`recent_repeat` / `probability_declined`（进台账，面板错误面可翻译，
  双语 i18n 已补；台账失败行列改为优先翻 `panel.error.<code>`、无键仍直出码）。
- 面板 dashboard 的 config 可观测面补三个新键；工具层失败尝试照旧进台账
  （"她想发但没发出去"多了两个节奏原因）。
- 配置加固（顺手钉的真雷）：`_as_int`/`_as_number` 现在对 TOML 合法的
  `nan`/`inf`/巨整数字面量全部钳位回默认不抬异常（int 域不做 float 中转）。
- 测试 194 → 207：概率闸五门（默认短路不掷/未中拦投递/窗口内不重掷/过窗重掷/
  force 只绕双闸不绕冷却）+ 去重四门（成功计数失败不计数/角色卡隔离+跨实例持久/
  0=关/query 候选剔除后严格最优直发）+ 工具面三门 + 文案门追加三句 + 三处同源键更新。

## 0.4.0

v0.4.0「表情是她挑的，不是系统拍板」——轮 C：按情境选图（学习 外部系统的选图纪律，
机制见 docs/sticker-system-study.md）：

- **`resolve_send_target` 判定器（core）**：query 选图不再"发匹配第一张"——
  最优分严格唯一→直发；头部并列→回 top-K 候选清单（`multi_candidates`，
  上限 5 与 外部系统 top_k 同量级）让她拿 id 发第二刀。**不替她拍板并列**。
- **检索一把尺重构**：`search_with_scores` 成为唯一实现，`search_stickers` /
  `resolve_send_target` 都是薄封装（打分/排序/目录行三处同源更牢）。
- **收紧空枪**：id 与 query 都不给→`id_or_query_required`（旧行为会"顺手"发常货——
  选图必须有依据，那是"发得准"的另一半）；点到的 id 失效仍如实回 `no_match`。
- **注入文案升级（学 外部系统提示词的 head 段 的"使用规则"段）**：中段新增——
  "分清你是在安慰对方还是在说自己，拿不准、不贴切就不发；一条回复配一张就够，
  宁缺毋滥"；尾段同步候选机制。**频控两层分离**：软提示进文案，硬闸在发送层冷却（既有）；
  "每条回复最多 N 张"的跨轮计数**刻意不做**——冷却已是硬闸，另立台账不值。
- 工具描述同步：`sticker_send` 的 query 参数明说"会按梗义筛、多候选回清单"。
- 本轮**不碰配置不碰面板**（三处同源不动；候选面只在工具链路，面板直发仍走 id）。
- 测试 190 → 194：并列回候选×2（含"一发都没出"与二刀直发）、严格最优不吃候选规则、
  空枪拒绝、注入文案使用规则门。

## 0.3.0

v0.3.0「她看得懂每张图在回复什么」——轮 A：语义元数据层（学习 外部表情包管理系统的数据层，
机制调研见 docs/sticker-system-study.md，代码全自写）：

- **梗义 caption（≤300）+ 图内原文 visible_text（≤200）**：`Sticker` 新增两可选字段。
  caption 是"这张图在回复什么、什么上一句会触发发它"（外部系统的标注提示词 的核心思想），
  区别于 desc 的"主人给的短标签"；visible_text 只进检索打分、**不上目录行**。
- **目录行一把尺 `Sticker.catalog_body()`**：caption 优先、空则回落 desc——
  `sticker_list` 与存在感注入共用的 `format_catalog_for_model` 单点改动，两处自动同步。
- **打分序升级**：desc(80) > caption 精确(78)/子串(76) > 套图名精确(75) > 标签(70/60) >
  套图名子串(58) > 图内原文(40) > 文件名(30)；文档钦定序"desc > caption > 套图 > 标签"成门。
- **入口面贯通**：add/update 收 caption/visible_text；新稳定错误码 `caption_too_long` /
  `visible_text_too_long`（校验发生在入库前，坏请求不留半张图）；update 的 caption 学 group 语义：
  **空串=清除标注，缺席=不改**。
- **灭掉整类"重建丢字段"雷**：`with_touch`/`with_sha256`/`Library.update` 改 `dataclasses.replace`——
  sha256 回归（v0.2.0 修）的真病根是手工枚举字段，现在加字段不再需要改四处。
- **套图包 manifest v2**：条目随包携带 caption/visible_text；导入侧从不按版本硬拒，
  旧 v1 包（无键）宽松回空；旧 catalog 分片无键同样宽松兼容，无迁移脚本。
- **面板**：编辑弹窗与单张收藏表单补"梗义/图内原文"输入；卡片正文下展示 caption；
  i18n +8 键（zh-CN/en 同步，文本级插入保 CRLF），4 处旧文案"选图唯一依据"改写
  （现在她看到的正文是 caption 优先）。
- 测试 170 → 190：test_catalog 新 TestSemanticFields/TestSearchSemanticFields（打分序四门、
  宽松兼容、拷贝携带新字段防回归、目录行隐 visible_text）；test_entries 新 TestCaptionFields
  （入库/超长拒/只改 desc 不丢 caption/空串清除）；test_pack 新 TestCaptionInPack（随包迁移、v1 旧包容宽松）。
- 本轮**不碰配置**（三处同源不动）；自动标注（VLM）是轮 B，本轮先把数据层地基打好。

## 0.2.0

v0.2.0「她得记得自己有表情」——存在感 + 套图分组 + 库可迁移（机制调研自 外部表情包管理系统，代码全自写）：

- **存在感注入（services/awareness.py）**：复用 60s watch 拍（不新增表），从 `bus.conversations`
  找"最近在跟她说话的角色卡"，把「收藏间里有 N 张 + 最近常用前 K 行」以
  `push_message(visibility=[], ai_behavior="read")` 静默注进她的上下文——治"她想不起来有表情"。
  按角色卡的内存时钟自节流（默认 3600s，配置 `[sticker_manager.awareness]`）；空库/没人说话/被拒
  都不推进时钟；永不炸拍（与 tool_watch 同纪律）。心跳=在场性不随业务冻结，注入=行为链路**随**
  `[].enabled` 冻结——两条相反的联动刻意在 docstring 里写死了。
- **调试入口 `awareness_now`**：绕节奏不绕开关，面板"现在注一条"；稳定码 awareness_disabled /
  awareness_no_target / awareness_empty_library。
- **套图分组（Sticker.group）**：一条表情至多一个组（可空=未分组）；检索按"desc 命中 > 套图名命中 >
  标签命中"打分；目录行形如 `[id] 描述（套图：G；标签：a/b）`；面板 chips 过滤 + 编辑框；
  老库无该键宽松兼容（from_dict 回空串）。
- **导出/导入套图包（core/pack.py + Library.export_pack/import_pack）**：`manifest.json + stickers/`
  打 zip 落 `data/library/exports/`；导入走收件箱通道认 `.zip`（有 manifest 吃元数据，裸图包按
  文件名清洗描述）；**zip-slip**：条目名只当"包内定位符"，落盘永远走 add() 服务端发号，
  `safe_member_name` 拒目录/上跳/盘符/隐藏形状；先查 ZipInfo.file_size 再读（zip bomb）；
  超 PACK_MAX_ENTRIES 截断计数。**刻意不兼容 外部系统的 memes_data.json**（用户拍板）。
- **修回归**：`Library.update()` 重建 Sticker 时曾丢 sha256（每编辑一次就得等下次查重 lazy 回填），
  现在指纹随文件本体走。
- 频控两层分离（参致 外部系统"软提示+硬闸"思想、实现自写）：core 只拼文案，节奏/目标/时钟在
  services；注入文案与 `sticker_list` 目录行同一把尺（format_catalog_for_model 复用）。
- i18n：+34 键（zh-CN/en 同步；文本级插入保 CRLF）；面板存在感卡（状态/注给/下次最快/调试按钮）
- 测试 121 → 170：test_awareness 21 / test_pack 16 / test_v020_entries 13 + 契约门词边界修正
  （writestr( 的尾巴会撞 tr( 前缀，加负向前查）+ config 三处同源并入 awareness 段

## 0.1.4

- **显示名改为「表情包管理」**（只动 `[plugin].name`、面板标题四处与文档题头；
id / entry / 类名 / 仓名不变，装机兼容）——在插件市场里比「表情包管理器」好听得多了
- **补 `plugin.name`/`plugin.description`/`plugin.short_description` 双语 i18n 键**：宿主已安装卡片
  优先读插件语言包的 `plugin.name`（回落才是 toml name），en 用户不再看到中文回落名；
  改显示名今后要两处一起改（已成文进 DESIGN Identity Lock 段）

- 工具注册心跳（移植 our_life v0.5.0 已验证方案，适配本插件两工具面）：

- **`services/tool_watch.py`（新）**：低频巡检器——每 5 分钟回环 `GET /api/tools`
  点名缺席的 `sticker_list` / `sticker_send`，只对"可达且真缺席"补挂（走基类
  `_notify_llm_tool_registered` IPC 重发，replace 幂等；`getattr` 取用，宿主改名
  时降级为日志不是崩溃）；不可达零动作（不盲挂、不每拍追打）；认不出的响应形状
  按"全场无工具"处理（幂等重发比漏挂安全）
- **病因**：`@llm_tool` 只在启动时发一次 IPC；main_server 晚起或重启后注册表全丢
  且不重试，她的发表情通道**静默失效**没人发现（宿主 docstring 自己写着
  "The plugin can re-register later"，官方 tool-calling 文档钦定周期巡检解法）
- **新 timer `on_watch`（60s 一拍，内部按 300s 自节流，首拍即查）**：本插件此前
  无后台拍；timer 无 watchdog，入口层双保险吞异常。心跳与 `[sticker_manager].enabled`
  无关：注册韧性是在场性，不随业务冻结而冻结
- 间隔不进配置：可靠性参数不是行为参数（与冷却只在内存同等待遇）
- 测试 108 → 121（新 `tests/test_tool_watch.py` 13 条：形状差集×4 / 间隔自节流×2 /
  不可达不盲挂 / 点名补挂 / 单名失败不连坐 / protected 面缺席降级 / fetch 异常吞掉 /
  间隔常量钉住 / timer 入口离线可跑）；DESIGN 风险区销账 + 陷阱 15

## 0.1.3

实机首测修复（Steam 宿主日志钉的坑）：

- **大图预览超时（真 bug）**：宿主 entry 回包走 ZeroMQ 控制通道，单帧硬上限
  4,784,128 字节；3.95MB 图的 dataUrl（base64 后 5.27MB）被传输层拒发，
  宿主干等到 15s 超时→面板只剩一句 500。preview 改为**分段协议**
  （offset / chunk_base64 / next_offset / done，宽 3MiB 且是 3 的倍数，
  base64 段无填充可直接串接），任意 ≤8MiB 的图都能拼回预览；面板逐段拉取
- **失败不再静默**：卡片上的发送/编辑/禁用/删除出错现在在卡内出 Alert，
  稳定码带中文文案（`panel.error.*` 十八码全盖）：send_cooldown 这类
  正常拦截终于能看懂“刚发过一张，让她缓一下”
- 新增 i18n：18 个 panel.error.* + fields.offset；preview 描述重写
- 测试 106 → 108（分段拼回/越界钳位/脏 offset 三道门）；
  DESIGN 陷阱 14 把控制通道 vs 媒体通道的区别成文

## 0.1.2

批量导入（思路参致 外部表情包管理系统，代码自写）：

- **面板多选批量收藏**：原生 `<input type="file" multiple>` + `FileReader` 逐张走
  已测的 `add` 通道（魔数/查重/体积限制全复用）；描述取自文件名清洗，
  单轮上限 64 张（超出如实报数），进度与四类计数（已收/重复/超限/失败）
- **收件箱目录**：新增 `data/library/inbox/`——把图片文件拖进去，面板点
  「导入收件箱」（`import_inbox` 入口）服务端逐张入库；成功/重复的源文件删掉，
  超限/坏图保留原地可重试；隐藏项不碰；面板按钮带待收计数，上下文露目录路径
- 文件名→描述清洗规则在两处各一份（`core.catalog.desc_from_filename` /
  面板 `guessDesc`）：跨运行时碰不到，改规则两边一起改（DESIGN 陷阱 11）
- 单张上限 8MiB 抽成 core 常量 `MAX_STICKER_BYTES`，入口与收件箱共用
- 测试 95 → 106（core 清洗 5 / 持久层收件箱 4 / 入口层 2）

## 0.1.1

基础完善轮（参考 外部表情包管理系统，只取纯管理面的小切口）：

- **重复入库拦截**：每张图入库时记 sha256 内容指纹，同图再传返回稳定码
  `duplicate_image`；旧版无指纹的条目在查重时 lazy 回填（三件里唯一碰数据的改动，
  幂等、宽松兼容，catalog schema 不变）
- **库体检与自修复**：新 `repair` 入口 + 面板按钮——清掉文件已丢的条目、
  删掉没人引用的孤儿文件、回填旧条目指纹，返回三项计数
- **面板收藏体验**：文件名预填描述（改过就不覆盖）；成功/失败（含 duplicate）
  都有 Alert 反馈，不再只进 console
- 测试 86 → 95（查重/回填/repair 各有入口层与持久层两道门）

## 0.1.0

首个版本：能收能发。

- 表情库持久化：`data/library/` 下 JSON 目录 + 图片文件 + 使用台账（写穿式、原子替换）
- 管理入口：add / update / remove / send / list / preview / history / switch（全部 `@ui.action` 暴露给面板）
- LLM 工具：`sticker_list` + `sticker_send`，她在对话里自主挑图发送
- 发送链路：≤256KiB 内联（gif 保动画）、大图换 URL、按角色卡冷却 20s、失败也进台账
- Hosted TSX 面板：收藏表单（ImageUpload）、网格浏览（懒加载预览）、编辑/禁用/删除、使用记录
- fail-closed 总开关 `[sticker_manager].enabled`（默认 false）
- i18n：zh-CN / en；三处同源门（configuration == plugin.toml == config.example.toml）
- 工程：五门 release_gate（pytest / ruff / check / release / hosted-tsx），本机 TEMP 对策已内置
