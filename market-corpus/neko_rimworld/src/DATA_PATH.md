# Current RimWorld consultation data path

The host calls `rimworld_consult(question)`. The plugin creates a read-only
consultation, obtains current game evidence, and returns a short answer plus a
complete saved result. Execution ownership is described in [DESIGN.md](DESIGN.md).

## Connection and supported contracts

The authenticated bridge binds reads to a connection epoch, colony and loaded
world. The Mod owns the query catalog and executable field semantics. The plugin
requires `public_query.v2` and complete `world_observation.v2.3`, including the
current native-state schema, before dispatching the model. Invalid or incomplete
directories fail admission. A reconnect or loaded-world change requires a fresh
consultation; observations from different loaded worlds are never combined.

The world contract supplies `survey_space`, `inspect_route` and `inspect_state`.
An advertised `native_behavior.v1` contract supplies `inspect_actions` and
`inspect_threats`. The connection-bound setup is assembled in
[game/query_tools.py](game/query_tools.py).

## Model tools and native reads

The model uses `look`, `count`, `survey_space` and `evaluate_activity` for game
reads. `look` selects explicit object aspects; `count` counts records or sums
stack units. `evaluate_activity` resolves the requested actors, subjects and
destinations together and composes native state or route observations. Route and
state backends remain internal to these semantic operations. Native behavior
tools are exposed when their contract is present. Ready, version-bound knowledge
adds `wiki`; persistent history or an explicit report reference adds
`workspace_history`.

The semantic compiler maps these requests onto the Mod's public fields and
arguments. It preserves actor versus recipient, journey stage, origin,
destination, selected scope and native meaning. The model does not author raw
query field paths. `query_catalog` loads the directory; the Mod's `describe_query`
operation serves metadata diagnostics rather than consultation game evidence.

## Public-query semantics

A backend `query` accepts one `{from,args,where,select,...}` request or a
`requests:[...]` batch. A batch uses one observation sample. Its validation
completes before reads; a failed batch executes no requests. Roots, fields,
argument branches, notes and value domains come from the Mod's typed registry.
The internal typed getter schema also supports capture and software diagnostics.

Collection conditions apply before display paging. Whole-selection aggregates
cover matching objects before `limit` and `offset`; a display limit does not
reduce a complete aggregate. Grouped values must be shared within each group;
varying selected fields become unknown with their issue rather than arbitrary
representatives. Physical objects are deduplicated by native identity, while
independent query populations remain distinct.

`Pawn.inventory`, equipment, apparel and carried objects are direct Thing
collections. `Thing.contents` reads a container. Spawned map objects, storage-cell
membership, held contents and the map ResourceCounter represent different
populations and must not be added as disjoint totals. Storage-cell membership
alone does not establish acceptance or a runnable hauling job.

Each collection retains its effective scope, predicate, scan/field coverage,
issues and requested display window. Aggregate-only reads with `limit:0` omit
rows and paging because detail was not requested. A complete empty count or sum
is zero; empty min/max is null. Failed or incomplete membership is unknown.
Map selectors accepting `current` resolve to the actual loaded-map ID in scope.
Continuations are fresh observations, not an atomic snapshot with earlier pages.

## Native world and behavior evidence

Native state distinguishes physical/device state from Pawn health, skills,
work, gear, mood and profile. Actor and user contexts retain their separate
roles. Surveys and routes report their sampled footprint, environment, hazards,
coverage and unknowns. When incidental object rows are omitted, the footprint is
not thereby empty.

Route cost belongs to the selected actor and journey leg. Native weighted cost
is not elapsed travel time; native Danger is not a complete safety judgment.
Behavior inspections report audited current player-order conditions and threats;
eligibility is not job completion, autonomous work or a prediction of success.
Production reads run cooperatively on the game thread within their existing
read budgets; synchronous native work cannot be hard-preempted.

## Model input, history and complete delivery

Raw requests and native observations remain in the audit independently of the
model view. Ordinary JSON pruning retains scope, sample, actor/user/stage,
unknown and partial facts. Definitions and implementation knowledge remain
separate from live observations. Ordinary persisted exchanges and retrieved
reports are historical data and do not become fresh game facts.

The system policy is authored in [agent/runtime/prompt.py](agent/runtime/prompt.py).
The research deadline closes game tools and leaves submission available. Runtime
advisories describe elapsed time and remaining budget; time pressure does not
turn incomplete evidence into supported claims.

`submit_response(answer, detail?, language?)` accepts plain text. `answer` is the
standalone short conclusion with material conditions, bounded to 180 tokens;
`detail` holds the remaining requested findings. Together they fit 12000
characters. Omitted detail preserves a saved draft; null clears it. A draft is
complete only after successful explicit submission. Length-only repair shortens
the answer while preserving saved detail. Other rejected claims retain the
research evidence for correction. Submission ownership and validation are in
[agent/delivery/handoff.py](agent/delivery/handoff.py).

The saved complete result and the short host handoff are separate deliveries.
Report-size omissions and save failures are explicit. Existing deadlines,
request limits and complete raw evidence determine execution boundaries; software
checks and saved-input equivalence do not establish content quality.
