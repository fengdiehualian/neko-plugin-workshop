# Protection receipts and viewer repair

Goal: retain real server denials in native execution, restore truthful HUD and inventory images without changing N.E.K.O or enabling server features on ordinary servers.

## 1. Native denial handling (minecraft-ai-friend)
- Add a bounded, host-neutral structured action denial parser beside `core/server-feedback.ts`. Accept explicit deny/allowed=false plus break/place, integral coordinates and a matching dimension; never infer whole regions or permission grants.
- Add Bridge admission memory for actual denied cells, connect direct server messages after player-chat provenance is resolved, and block only the matching native work. Keep full original message and terminal receipt; do not retry under a modified batch.
- Regression: direct system rejection, quoted player rejection, different dimension, malformed coordinates, repeated cells, changed block state, multi-step work.
- Run scoped Vitest first, then complete core suite, types and packed build. Commit core separately.

## 2. Shared viewer (mc-visual-console)
- Trace received max-health attributes versus actual health, preserving diagnostic source instead of inventing a value. Cover modifiers and genuine larger/smaller health limits.
- Fix the vanilla SNBT string producer (JSON escapes are not SNBT escapes). Add audited GUI support for plain armor and uncharged crossbows; retain unsupported visual modifiers explicitly.
- Extend the optional item appearance port for exact server resource assets; keep custom model selectors out of generic provider tables. Preserve source hashes and full stack identity.
- Regression: real captured stack shapes for armor, compass, book, custom model and head; missing/corrupt assets; foreign player input. Run renderer tests/build and inspect actual browser.

## 3. Optional Qiandengji presentation and resources (plugin pack)
- Preserve connection guidance through death while clearing stale body readings, and obtain bounded current state through the normal optional server mechanism. No extra planner/model or public test messages.
- Ship exact original appearance resources in the explicit extension with provenance and hashes; bind the observed model data and genuine head profile. Ordinary servers load none of these.
- Update checked dependency pins only after upstream builds pass. Run plugin pytest, Ruff and official release/Market structure validation.

## 4. Deployment and acceptance
- Fetch each main again before ordinary pushes, retain other agents' edits.
- Build verified packages into a new owned runtime, reconcile pending/unknown work and normally drain only this session. Leave N.E.K.O, SDK, Java and model processes running.
- Verify fresh same-player health/maximum, actual inventory icons and protection receipts in the browser and subsequent native actions. Record deployment and remaining limits in private maintenance logs; source tests alone do not prove autonomous survival.
