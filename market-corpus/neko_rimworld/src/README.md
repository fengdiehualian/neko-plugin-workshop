# N.E.K.O RimWorld v3.2

Read-only colony consultation through the local RimWorld Mod and an independent model.

Start here: [中文使用指南](docs/QUICKSTART.zh-CN.md) · [English quick start](docs/QUICKSTART.en.md).

The plugin detail page's dedicated **教程 / Guide** tab walks through downloads,
separate Mod and plugin installation, pairing, model configuration and the first
question. The working panel keeps its four original pages. Download the released
[Bridge Mod v3.2.0 ZIP](https://github.com/wislap/NekoRimWorld/releases/download/v3.2.0/NekoRimWorld-3.2.0.zip)
for RimWorld; get the separate `.neko-plugin` package from the
[plugin releases page](https://github.com/wislap/n.e.k.o_plugin_neko_rimworld/releases) when available.
If no package is listed yet, the guides include optional source-build instructions.

The panel includes first-use guidance, saved-answer browsing and explicit settings
save receipts. A save can be confirmed, rejected or still unconfirmed; the panel
does not silently repeat a write after a lost reply. Model limits are under
advanced settings. English and Simplified Chinese follow the host language, with
English as the fallback. These local changes are not a published release.

## Current source: v3.2

The product version is `3.2.0` (displayed as **v3.2**), aligned with the Mod and
bench. It includes the selected situation background, world/faction/quest/trade/
research consultation, exact definition reads, resource-source separation and
loaded-journey comparisons. This is the current source version, not a claim that
a plugin v3.2 package has been published. The Mod is distributed separately at
[v3.2.0](https://github.com/wislap/NekoRimWorld/releases/tag/v3.2.0). Protocol versions and
SDK support ranges are independent and unchanged.

The existing `rimworld_consult(question)` entry and shared runtime remain the
only consultation path. Situation preparation below is distinct from the five
domains: their detailed state is read only when requested, without automatic
five-domain scans or a mandatory discovery round.

When the Mod advertises `situation_snapshot.v1`, consultation preparation supplies a native situation snapshot before the first model request. It covers ongoing conditions, visible hostile groups, native alerts, actionable quests and recent archive events. This is background data in a separate user message, not a model tool. The deterministic view uses at most 1,000 tokens; complete original observations remain in the report. Native section priority and recency order are retained, and directly linked Quest sources are grouped without inferring links.

One connection-owned in-memory provider reuses the original sampled snapshot until 30,000 game ticks have elapsed. A consultation fixes one snapshot for all its research requests. The displayed map is the map at sampling; global alert, quest and event scopes remain explicit. Persistent work does not automatically reattach old backgrounds through history, and pure answer-length repair excludes game background. Busy, unsupported or timed-out background reads are disclosed as unavailable while ordinary consultation can continue; world or connection changes still stop the run.
The host agent sees `rimworld_consult(question)`. No game orders or mutations are executed.

When the Mod advertises `colony_state.v1`, `look` also reads world locations,
existing caravans, factions, public quests, trader stock and research. Two direct
tools evaluate requested journeys and trade baskets. Names resolve in the same
call; no discovery step or automatic five-domain background is added. See the
[capability guide](docs/COLONY-CAPABILITIES.md) for arguments and evidence scope.

## Historical deployed baseline (2026-10-07)

This records the earlier deployed baseline; it does not describe a v3.2 deployment.
The selected baseline is `v3-subtraction30-20261007-v3`: semantic reads, ordinary-JSON request-scoped pruning, activity composition and deterministic Def-name recovery when supported. There are no deployment switches selecting old input or query strategies. Public live delivery uses text; the delivery critic is retired.

The original30 run delivered30/30 with84 requests and881,860 public input tokens. Its evidence adjudication was18 clean/12 minor/0 major/0 fatal. Selecting this default does not establish v2.4 quality parity or a new quality result. Fixed-evidence first-draft ablations are experiments, not deployed behavior.

The look/count interface is mandatory: missing or uncompiled public query directories stop consultation before model dispatch. Historical reports use the same interface. Completion requires the explicit `submit_response` tool; ordinary assistant text is progress only. The runtime no longer accepts `allow_text_completion` or `prepared_calls`. Caller-prepared sessions remain supported through `execute(session, settings)`. Legacy template/GraphQL/resource tool publication has been removed; the internal public query backend remains. Unavailable knowledge uses exact native names; supported world contracts retain their own state projection. Production never imports archived experiments.

## Available reads

With the required current public-query and native-world contracts, the model sees `look`, `count`, `survey_space`, `evaluate_activity`, `inspect_actions`, `inspect_threats`, available `wiki`, and `submit_response`. When the advertised colony-state contract supports them, `look` adds the five domains and resource-source reads, with `evaluate_journey` and `evaluate_trade` for direct calculations. Consultation admission requires `public_query.v2` and `world_observation.v2.3` with the complete native state contract. Older versions, missing state readers or incomplete state schemas fail before model dispatch. Optional behavior tools and knowledge still depend on their advertised contracts and ready indexes. The activity tool replaces overlapping public path tools while retaining their native readers internally.

- `look`: explicit aspects; physical `state` does not include Pawn health/skills/work/gear. `focus` selects skills/work/capacities/state facets; `stats` requests exact StatDefs; `include=[body_parts]` retains full anatomy detail. Pawn `identity` carries native gender where the schema has it, so pronouns are read rather than guessed from names.
- `count`: records or stack units, grouping and bounded examples. Holder membership is not ownership.
- `survey_space`: objects/environment/geometry selection and pagination. Omitted include retains full survey; geometric inclusion is not reachability. Area/zone intersections are always retained, so an unrequested `geometry` is not evidence that no zone covers the region.
- `evaluate_activity`: move, transfer or state, actors×destinations, optional approach, destination state, environment, objects and geometry. Only requested actor facts are read. actor travels/interacts; user receives; subject supplies the transfer origin. There is no separate multi-activity batch option.
- `inspect_actions` checks audited native player-order conditions without executing them. `inspect_threats` separates hostility, active classification and incoming attack checks.
- `wiki` combines local definitions and version-bound implementation lookup (`source:`). Source knowledge is not current game-state evidence.

Ordinary JSON retains sample, actor/user/stage/scope, unknown and partial states. Same-read identities and equal parent fields may inherit; environment_shared has local overrides and preserves path/corridor separation. Full native observations remain in the audit. The diagnostics include escape route is not supported.

Every supported game read uses the same context projection; `context_enabled` is no longer an accepted internal argument. Work-table summaries and their same-sample pagination have an independent owner, so context projection does not import the native state reader.

Look identities expose cells only through `location.position` and `location.held_position`. Activity object/source identities use the same projection when position facts are present. Unknown locations stay explicit in look; own and held cells keep their separate maps. Root/status position copies and cell `map_id` aliases are removed from the model-facing identity. Selection `map_id`, native `map_held`, all original observations and request scopes retain their distinct meanings.

## Configuration and migration

Keep model endpoint/name/key, deadlines, output length, temperature and pairing configuration in `config.example.toml`. The model is user-configured; the experiment model is not hard-coded.

Remove `NEKO_CONTEXT_PRUNING`, `NEKO_ACTIVITY`, `NEKO_DEF_NAME_RECOVERY`, `NEKO_PUBLIC_REFERENCES`, `NEKO_SEMANTIC_SURFACE`, `NEKO_EVIDENCE_COMPACT` and `NEKO_PUBLIC_INPUT_TOKEN_LIMIT`. Any present retired environment key is rejected, including an empty value or a former baseline value. Bench rejects nonempty `execution_flags` declarations; historical variants require their frozen source and runner. Bench retains explicitly declared per-case input budgets outside production.

Remove `review_before_delivery`, `allow_text_completion` and `prepared_calls` from the agent configuration. Any present retired key is rejected, including false, null or empty values and configurations without a model. Formal content review remains solely `bench.py evaluate review`, outside the consultation runtime.

`NEKO_RIMWORLD_KNOWLEDGE_DIR` and `NEKO_RIMWORLD_MECHANISMS_DIR` remain optional path overrides. Definition recovery requires a ready complete index and refuses ambiguous matches; native lookup determines existence. Definition knowledge accepts only the publisher's `ready.json` plus immutable `snapshot-*.json` payload. Full-file `current.json` imports, Def SQLite cache restoration, `cache_directory`, `allow_legacy_import` and path-based `WikiStore` construction are removed. A missing, replaced or invalid ready directory makes new consultations unavailable until refresh succeeds; already pinned immutable views remain separate. The version-bound mechanism source index (`source:`) and workspace SQLite storage remain current capabilities.

## Pair the game bridge

1. In RimWorld's N.E.K.O Mod Settings, generate a pairing token and copy it.
2. Start this plugin and open the **RimWorld Bridge** Hosted panel.
3. Under **Bridge setup**, paste the token and enter the same port used by the
   Mod, then choose **Save & restart bridge**.
4. Start or load a game and verify that the bridge is ready and the session is
   not stale.

The pairing-token field is write-only. Leaving it empty after initial setup
keeps the stored value.

## Configure the independent model

Under **Independent research agent**, configure:

- an OpenAI-compatible base URL, normally ending in `/v1`;
- the exact model name served by that endpoint;
- an API key, if the endpoint requires one.

The model adapter requests SSE streaming Chat Completions and assembles native tool calls before execution.
The API-key field is write-only and leaving it empty preserves an existing key.
An API key cannot be sent to a non-loopback plain-HTTP endpoint.

The model is owned by this plugin. It does not reuse the N.E.K.O character
model, prompt, conversation history, or memory.

## Consultation behavior

The outer N.E.K.O Agent calls:

```text
rimworld_consult(question)
```

Each call creates a new run with a new message list. The model may answer
directly, read the game through the semantic tools (`look`, `count`, `survey_space`,
`evaluate_activity` and advertised `inspect_actions` / `inspect_threats`,
`evaluate_journey` / `evaluate_trade`), look
up definitions through `wiki`, or finish a detailed response through the private
`submit_response` tool. The prompt recommends a small number of cycles
but the runtime does not impose a hard step count. Tool and submission errors
are returned to the model so it can recover and continue.

Network requests, cancellation, response sizes, and total wall time remain
bounded for process safety. If the model cannot finish normally, the entry
returns a structured `degraded` result instead of stopping the plugin.

## Work, delivery and audit

The panel opens on **本次建议** for the current colony. It shows the existing saved
short answer first; **查看完整回答** expands the complete authored text without
another model request. **历史记录** groups past discussions by caller and colony,
using readable questions and local timestamps. Saved answers remain explicitly
historical; changing the loaded colony does not rebind an older answer as current.

**调试** is always visible in the top navigation, including on the settings page.
Its **原始读取记录** tab retains the saved native observations, hashes, failures,
delivery metadata and execution audit. Record titles come from their original
operation/root metadata, with original JSON available on demand. The panel never
rewrites those observations or infers new game facts from a title. Reads use the
existing read-only workspace API; the database schema, model tools, prompts and
private settings are unchanged. A transport failure preserves available history
and supports an explicit refresh rather than silently substituting another answer.

Valid caller/colony identity binds persistent work; missing identity or retained work falls back to a standalone consultation. Turns for the same role and colony serialize. History is attached as historical evidence; workspace_history is published only for existing history or an explicit report ID. Reports and exchanges save automatically. No autonomous observation or learning is implied.

A failed submission retains valid draft fields. Authored report and short handoff revisions stay paired; invalid updates cannot mix a new summary with an older report. Natural-language claim checks are retained as audit diagnostics and do not reject the whole submission or require another model call. Delivery completion is a format/transport state, not a factual certification. Pure short-answer length repair sends answer only and preserves detail; structural repairs retain research evidence. Research closes near the deadline and only submission remains. Existing deadlines and tool/result size bounds remain.

Live text answer is limited to180 tokens and answer+detail to12000 characters. Full reports, raw evidence, failures, usage and incomplete status remain visible. Saved reports use their retained full_text; retrieval does not run a reference renderer or the old submission protocol.

## Verification scope

The latest original30 + standard45 regression compares only the recent exact-read,
resource-source and loaded-journey improvements. **Both arms already contain the
five domains and the selected situation background.** Public input changed from
4,310,708 to 3,784,675 tokens (-12.20%); normal completion changed from 74/75 to
73/75. Original30 major-issue counts were 2 in each arm; standard45 full-text and
SDK-summary major-issue counts each changed from 5 to 2. Some long-tail inputs
increased. Seven author attempts have unknown provider usage, so this is not an
exact cost comparison, proof of universal quality improvement or a comparison
against a version without the five domains/background.

The separate six-question first-attempt consultation check delivered five
answers and left one procurement/research question undelivered. Its failures
and remaining answer issues are retained. Software tests and native pipelines
verify wiring and facts, not complete model quality. See the
[capability guide](docs/COLONY-CAPABILITIES.md) for normal-use scope and limits.
Original evidence is retained outside this repository in workspace
`.bench-results/consultation-decisions-20261016-v1/` and
`.bench-results/capability-regression75-20261016-v2/`; historical results are not
rewritten for this version label.

## Development and preserved research

Run plugin pytest with UTF-8 in both parent and child processes. Software checks and fixed input equivalence do not prove content quality. Windows-only/platform failures are recorded separately.

Retired experiments are under `rimworld-bench-archive/code-20261013/`. Source versions preceding architecture removal are sealed under `.bench-results/architecture-removal-20261014-v1/`. Original experiments, questions, answers, failures and frozen runners stay unchanged. Reproduce old variants with their frozen source.


Production reads have one current native-state path. Historical no-state connections, the old public `inspect_space` interface and its ranking implementation are removed; activity comparisons retain the current native route/state readers and actor/user/stage scope bindings. Manual overview probes batch game/map facts in one public query. Existing historical data and frozen runners remain unchanged and require their original sealed implementation.

Query-backed selection, mood/profile, resources and population measures are separate from native physical state/Pawn facts. Each public tool is built by its business owner before generic schema serialization; context processing does not rewrite tool semantics. The mixed look adapter and obsolete query Pawn-fact planners are removed. Current catalogs advertising the retired `production` selection alias are rejected even if they retain the supported version numbers; use a matching rebuilt Mod with `bill_giver`. The physical `state.production` facet remains supported. This source cleanup does not certify or promote a new content-quality baseline, and does not deploy the game DLL.

Current tool setup carries the negotiated tool handlers, query directory and pinned knowledge view, plus the internal situation provider separately from the model tools. There is no catalog/wiki/mechanism prompt assembly in setup; actual tool descriptions remain the model-facing guidance. Caller-owned work capabilities enter only through `AgentRunSession.capabilities`; they are absent during finalization, and duplicate game/work tool names are rejected. Caller history and native actor/user context keep their original roles.

Workspace storage owns ordinary exchanges, original observations and delivery records. Current schemas and immutable terminal records are required; unsupported schemas fail without migration or reset. Working-note persistence and its history truncation, source digests, tool action and panel have been retired. Existing database files are never rewritten for this removal. Historical implementations remain sealed outside the current tree.

Session context projection is mandatory. A caller-supplied current `ContextPruner` is retained; `None` and invalid supplied instances reject. Semantic admission uses the actual interaction factory exclusively; the test-only boolean availability API is removed.

The current consultation prompt is authored only in `agent/runtime/prompt.py`. Session creation binds its research policy and work identity once; finalization changes only the phase policy. Prepared callers supply the question, ordinary context and work identity, with no alternate system-prompt option. Query discovery and the overlapping standalone spatial backend are retired; activity and native world reads retain their existing roles.

`preferred_cycles` has been retired: it did not control research or model calls. Configurations and settings writes containing that key are rejected; remove it explicitly. Research remains bounded by the existing deadlines, finalization reserve and tool/result limits. No cycle-count cutoff replaces it.
