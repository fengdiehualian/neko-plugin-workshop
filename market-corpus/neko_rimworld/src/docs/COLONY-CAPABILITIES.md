# v3.2 world, faction, quest, trade and research consultation

The v3.2 product source uses version `3.2.0` in plugin, Mod and bench metadata.
It also includes the selected situation background: one bounded projection of at
most 1,000 tokens, backed by a connection-owned 30,000-game-tick snapshot cache.
That sampled background is separate from the fresh requested facts below; it is
not a complete inventory, task, research or world-state response. Source version
alignment does not publish packages or deploy a game DLL.

These capabilities share the existing `rimworld_consult(question)` runtime,
ordinary JSON projection, observation audit, history and full report. They are
available when the connected Mod advertises `colony_state.v1`. A base connection
without this additive capability retains its current tools. No discovery round,
automatic five-domain background, separate agent or production switch is added.
Within the supported v1 contract, product selection, faction filters and cargo
changes are published only when the connected operation advertises those
parameters. An earlier Mod keeps its supported project/stock/current-cargo reads.

## Direct reads

Use `look(object, aspects, limit?, offset?, focus?)`. Names resolve in the same
read; a directory lookup is not a prerequisite. Existing physical object reads
keep their existing contract.
Returned instance IDs and definition names can be reused directly; see
[object references and focused reads](OBJECT-REFERENCES.md).

| object.kind | aspects |
|---|---|
| world | places |
| world_objects | identity, location |
| caravans | identity, location, route, supplies |
| factions | identity, relations, settlements |
| quests | identity, requirements, rewards, deadlines |
| traders | identity, stock |
| research | progress, requirements, unlocks |
| resources | quantity, distribution |

Selectors accept `id/ids/name/names`. World objects additionally accept category
and `near={kind:"map",map:"current"}` or a known object name/ID. Traders accept
map and `focus.items` (native item Def names or exact loaded labels). Resources
accept map and require `focus.items`; they do not silently scan all definitions.
Research accepts project Def names/labels, or `focus.items` for the desired
product's loaded recipe/workbench/research paths. Product and project selectors
are alternatives. World-object `focus.faction={id|name}` filters the existing
page by faction. Untargeted lists use public
objects; caravans are player-controlled, quests are offered/ongoing and research
defaults to selected projects. Explicit public historical quest lookup is allowed.

Examples:

```json
{"object":{"kind":"quests","names":["补给请求"]},"aspects":["requirements","rewards","deadlines"]}
{"object":{"kind":"research","names":["MicroelectronicsBasics"]},"aspects":["progress","requirements","unlocks"]}
{"object":{"kind":"traders","names":["商船"]},"aspects":["stock"],"focus":{"items":["ComponentIndustrial"]}}
{"object":{"kind":"resources","map":"current"},"aspects":["quantity","distribution"],"focus":{"items":["ComponentIndustrial"]}}
{"object":{"kind":"research"},"aspects":["requirements","unlocks"],"focus":{"items":["ComponentIndustrial"]}}
```

Top-level `total/page` describes the selected population. On a selected trader's
stock view, `offset/limit` page `stock.rows`; the selected trader is retained and
`stock.page.next_offset` continues its inventory. On an untargeted trader
directory they page partners, with each stock view starting at zero.
Faction `settlements` is a bounded preview with total/omitted and a directly
usable world-object continuation selection, rather than an unbounded nested list.

Resources partition observed units into spawned ground, player-pawn-held,
identified trader stock and other held contents. The selected map and exclusions
remain in scope. Ground is a physical location, not proof of player ownership or
availability. Seller stock is not player inventory; off-map caravan cargo and
unpaid quest rewards are separate reads. Existing `count` retains its original
population semantics. With `by`, count `detail` limits groups; it does not return
instance examples. Ungrouped count with detail returns instance examples.

Physical Thing measures carry one `quantity_basis` beside the numbers: the actual
held/fog flags and that this read does not establish availability. This does not
change count defaults or add eligibility checks. Resource `scope.selection`
identifies the requested exact definitions/labels and resolved definitions;
complete coverage of those definitions is not coverage of all food or equipment.
Resource reads exclude fog and include identified public seller stock, so their
buckets cannot silently replace a count with different population/visibility.
Trader stock carries `price_basis=market_value_reference_not_negotiated_quote`;
only a requested `evaluate_trade` supplies the bound contextual quote. These
headers are on the relevant result, with no extra read or automatic quotation.

## Direct calculations

`evaluate_journey` takes an existing player caravan and all requested world
destinations. It uses native walking-caravan path and time estimates. Optional
supplies retain current carrying facts and route-specific food estimates. It
does not form a party, issue a route or predict encounters.
Optional `cargo_add`/`cargo_remove` use the same item/quantity basket as trade.
They return a `cargo_plan` and per-destination `cargo_scenario` beside the current
estimate, using native movement/time calculation with the changed mass. Goods are
not moved, their accessibility is not inferred, and current food estimates retain
their explicit basis. A loaded route can differ even when mass stays below capacity.

```json
{"caravan":{"name":"采购队"},"destinations":[{"name":"采购据点"},{"name":"备用据点"}],"include":["supplies"]}
{"caravan":{"name":"采购队"},"destinations":[{"name":"采购据点"}],"cargo_add":[{"item":"WoodLog","quantity":40}],"include":["supplies"]}
```

`evaluate_trade` takes a trader, negotiator and explicit player buy/sell lists.
It resolves names, checks the supported channel and existing inventory, and
returns native contextual prices, aggregate currency rounding, available funds
and conditions. It neither substitutes goods nor performs a trade.
Funds retain the particular trader, negotiator and channel binding. A single-item
purchase can include an affordable quantity calculated using that same quote and
currency rounding; it is not a count of all colony wealth.

```json
{"trader":{"name":"商船"},"negotiator":{"name":"Lumi"},"buy":[{"item":"ComponentIndustrial","quantity":10}],"sell":[{"item":"WoodLog","quantity":40}]}
```

## Facts and scope

Planet locations retain layer and tile IDs. Faction identity is an instance ID,
not a FactionDef. Relations are relative to the player. Quest reward groups
remain alternatives; acceptance, delivery and public stage timers remain
distinct. Structured quest requirements cover supported native parts and retain
the public description for the rest. Reward lifecycle distinguishes an existing
offer object from an issued payment; offers are not proof of payment.

Research retains real/apparent progress, prerequisite chains, applied techprints,
bench/facility compatibility and known direct unlocks. Project selection is
separate from a researcher's actual work. Source definitions are not current
state. Existing settlement inventory is read without materializing missing stock;
market value is labeled separately from an actual quote.
Bench condition details retain matching existing buildings and the separate
required-facility presence/link/activity facts. An empty compatible combination
does not imply the research bench itself is absent. Product paths come from loaded
definitions, not a claim that every modded acquisition method was discovered.

All reads preserve sample/scope/unknowns and raw observations. Business status
such as an incomplete quote remains visible separately from transport success.
Native calculations are synchronous within the shared frame-operation lifetime;
these operations are not a hard-preemptible global planning service.

## Validation and remaining limits

Provider-free SDK pipelines verify native facts and wiring. The separate six
first-attempt real consultations delivered five answers; one procurement/research
question did not deliver after an empty response and a rejected recovery attempt.
Loaded-journey deadlines and resource-source partitions appeared in actual
answers, while other wording and evidence-scope issues remain. This is not five
quality passes or proof that all five domains are equally well exercised.

The latest original30 + standard45 regression compares the recent decision
improvements only: both arms already contain these capabilities and the selected
situation background. Public input fell 12.20%, with 74/75 versus 73/75 normal
completions, unchanged original30 major-issue count and fewer standard45 major
issues. Some long-tail inputs increased. It does not establish the cost of adding
the five domains from zero, exact provider costs, universal quality equivalence
or deployment. The full evidence and original failures remain in workspace
`.bench-results/consultation-decisions-20261016-v1/` and
`.bench-results/capability-regression75-20261016-v2/`.
