# N.E.K.O RimWorld v3.2 consultation architecture

The host sees `rimworld_consult(question)`. The plugin owns an independent,
read-only consultation with the configured model; it does not execute game orders.
Product metadata uses `3.2.0`; this describes the v3.2 source implementation,
not a published/deployed release or a content-quality certification. Protocol
versions and SDK support ranges retain their independent contracts.
The native query contract is in [DATA_PATH.md](DATA_PATH.md).

## Runtime and boundaries

`AgentRuntime` requires a tools provider returning a compilable Mod public-query
directory and its query handler. Every run creates `SemanticInteraction` directly;
there is no native-tools execution mode, alternate interaction policy or optional
adapter. A missing directory ends the consultation before model dispatch.

The runtime constructor accepts tools_provider, audit_registry, logger,
model_factory and report_sink. `run(question, settings, capabilities=())`
prepares a single request. `execute(session, settings)` accepts fresh caller-owned
context. Both use the same semantic reads, deadlines, audit and text delivery.
The caller owns persistent work; the execution owns model/tool calls and one result.

Native situation preparation has one owner in `game/situation.py`, held by the connection-scoped catalog cache. `ToolSetup.situation_provider` carries that internal source separately from tools and their directory. The shared runtime binds it once before the first dispatch, so standalone and caller-prepared persistent sessions use the same path. No model-facing `read_situation`, look subtype, refresh tool, database or background service is introduced.

`agent/context/situation_context.py` creates the single bounded background view. It preserves native ordering and scope and groups only direct Quest identities. Presentation text has explicit length bounds; an entry that cannot fit in full retains its identity and native priority/state/time cues with `details_omitted`. If even that cannot fit, the section stops rather than replacing it with a shorter lower-priority or older entry. Admission remains round-robin across business sections; entry and field omissions are explicit. The source map and tick describe the original sample even on cache reuse. The report's `situation_context` stores that exact content, token method/count, sample, cache check, observation reference and SHA-256 digests; `observations` retains the original envelope. Hashes use ordinary compact UTF-8 JSON for the raw observation and exact UTF-8 content bytes for the projection. Source-read failures are disclosed separately from empty sections. Existing tool projection, final delivery, claims and short-answer repair keep their current contracts.

The situation projection is bounded to 1,000 tokens, with a connection-owned
sample reused for 30,000 game ticks. Each consultation fixes its one sample;
this cache does not substitute for fresh, requested five-domain facts.

`look` and `count` are the base model vocabulary. Native capabilities add
survey_space, evaluate_activity, actions and threats; ready knowledge adds wiki.
The additive colony-state contract extends `look` for world locations, caravans,
factions, quests, trader stock, research and resource-source partitions. Existing
runtime dispatch also supplies `evaluate_journey` and `evaluate_trade`; no domain
router, separate agent or automatic five-domain scan is introduced. Resource
sources preserve physical/holder/seller distinctions without asserting ownership
or availability. Journey cargo scenarios retain current and changed-mass estimates
side by side. See [the capability guide](docs/COLONY-CAPABILITIES.md).
Internal public query and native path/state tools supply these semantic reads.
Caller-owned capabilities are composed explicitly and cannot shadow game tools.
Research closes near the deadline; the remaining interface contains only submission.

Persistent historical context and explicit saved-report questions use
`workspace_history`. Historical data stays marked as historical and does not
become a current observation. The runtime has no attached-report parameter or
separate report-evidence tool. Existing saved reports are read using full_text,
without reconstructing reference parts.

## Submission and complete delivery

`submit_response(answer, detail?, language?)` accepts answer as one text string.
The short answer must fit 180 tokens; answer plus detail must fit 12000 characters.
There are no answer parts, citation rendering, fact budgets or policy switches.
The deterministic claims checker always binds assertions to current observation
subjects and scope. A rejected claim does not enter committed text or failure output.

Valid draft fields survive rejection. Omitted detail stays saved; explicit null
clears it. A pure short-answer length repair sends answer alone using a small
request and preserves detail. Other claim/structure failures retain the research
context. Trailing tool-serialization fragments are removed only from rendered
authored text; saved input and native observations remain unchanged.

The full report remains separate from the short host summary. Report saving
records raw observations and samples. Oversized observations are omitted with
`omitted_size_limit`, retaining their count, IDs and sample metadata; the report
does not pretend all evidence was retained. Save failure remains explicit.

## Packages and dependencies

`agent/runtime` owns orchestration, session, registry, audit and prompts;
`agent/model` owns transport; `agent/context` owns ordinary-JSON pruning and
model-facing schema; `agent/world` owns semantic game reads; `agent/delivery`
owns drafts and handoff; `agent/reference` owns knowledge lookup.
`game/` owns transport and native directory negotiation; `workspace/` owns durable
work; `ui/` owns the Hosted panel. Shared serialization, tool contracts and model
message contracts have unique modules outside Agent. Importing game tools or
workspace history persistence does not initialize the model/runtime/world packages.

## Verification and preserved evidence

Input handling must retain actor/user, stage, origin/destination, scope, sample,
unknown/partial values and complete requested delivery. Raw observations remain
separate from pruned model messages. Cleanup must not add model or tool calls.
Software tests and fixed-evidence equivalence do not certify content quality.

Historical design and implementations are sealed outside production. Old
experiments reproduce their original source rather than selecting retired modes
inside the current runtime. The earlier selected A deployment and its historical evaluation, plus the v3.2
source and latest regression scope, remain described in [README.md](README.md).
