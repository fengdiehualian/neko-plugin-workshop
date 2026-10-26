# Repository instructions

- This repository is a thin N.E.K.O adapter for the canonical autonomous Minecraft framework in minecraft-ai-friend/packages/agent-mc-autonomy. Game logic, review policy and native queue coordination must be changed and tested there, then consumed here as a pinned dependency. Never restore local implementation copies.
  Offline tests do not prove live gameplay, native-server support or viewer/body binding.
- Use Python 3.11+ and the public `plugin.sdk.plugin` API. Do not modify the host SDK.
- Keep plugin ID `minecraft_autonomous` and manifest/project versions consistent.
- Multiple agents may update related repositories. Fetch this repository's main before
  editing and again before pushing, inspect incoming changes, and preserve unrelated work.
  Push validated work to main promptly with an ordinary non-force push. Do not reset or
  rewrite shared branches; reconcile concurrent updates before retrying a rejected push.
- Check relevant upstream refs regularly during development, review scoped changes and
  record the checked SHA. A newer upstream commit does not automatically change the pinned
  backend. Keep core, plugin/frontend, viewer and server deployment changes in their own
  projects; private runtime state and world saves never belong in this repository.
- Keep the official managed Verify, Release and Ruff templates intact.
- Do not connect to games, start services, call models or send messages during tests.
  Use temporary storage and the real SDK from `NEKO_ROOT` or the CI host checkout.
- Keep automatic startup and autonomy disabled by default. Preserve explicit stop.
- Keep `autonomy/` as a compatibility namespace only, with no gameplay implementation. N.E.K.O supplies high-level intent and
  narration/voice; the optional planner refines actions, and the native executor owns
  the real work queue. External frontend shutdown must not stop the standalone runtime.
- Natural-language goals and feedback use the durable command mailbox. Preserve request
  IDs, distinguish insert/resume from explicit replacement, and never turn planner reports
  into verified goal success. Unknown native dispatch blocks replay.
- Keep maintenance resume evidence separate from the standing goal. Never append old
  project counts, method indexes or deployment reports to the launch goal; they become
  standing intent in both body and strategic review. Keep dated records in scoped game
  memory/original evidence and reacquire current native facts. A known maintainer-added
  appendix needs an explicit recorded correction, never silent rewriting of human text.
- Add only the dependencies needed for the selected backend; do not copy a legacy
  deployment, private config, credentials, weights, voice caches or user world saves.
- Before enabling gameplay, require a core whose source, build, default entry and
  public types are independent of Cortico. Optional host adapters depend on neutral
  contracts and the core; do not import Core/Persona/provider/Memory implementations.
  Preserve useful behavior from the user's fork through explicit contracts and tests.
- Use the pinned independent headless core from `core_distribution/source.json` and its checked build receipt.
  Do not replace it with the historical 0.3 distribution. Legacy speech discovery, server
  policies and additional model endpoints must be explicit optional integrations.
- Before committing, run pytest, Ruff and the official release/Market-structure check
  using the commands in `CONTRIBUTING.md`. Inspect package contents and staged changes.
- Do not create release tags, publish releases or submit to Market unless that work
  is explicitly requested and functionality has been verified.
