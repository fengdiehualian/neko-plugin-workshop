# Mental Omega AI Decision Reference

This reference records the native rule inputs used by `strategic_ai.py`. It is
derived from the shipped `rulesmo.ini` sections `[General]`, `[AI]`, `[IQ]`, and
`[GlobalControls]`, plus the native production and entity telemetry exposed by
the bridge.

## Native boundaries

- `CreditReserve=100` and `PowerSurplus=50` are emergency spending boundaries.
- `TeamDelays=200,1250,2500` and `AIHateDelays=10,10,10` prevent an attack decision from being retriggered every frame.
- `HarvestersPerRefinery=2,2,2`, `AIExtraRefineries=2,2,2`, and the refinery/barracks/war/defense ratios constrain base growth.
- `RefineryLimit=4`, `BarracksLimit=2`, `WarLimit=3`, `DefenseLimit=40`, `AALimit=15`, and `HelipadLimit=2` bound expansion.
- `MinimumAIDefensiveTeams=1,1,1`, `MaximumAIDefensiveTeams=3,3,3`, and `TotalAITeamCap=100,75,50` define defensive and total team budgets.
- `AIForcePredictionFudge=70,35,25` is treated as uncertainty. The companion requires a visible force margin instead of assuming hidden enemy strength.
- `MaximumBuildingPlacementFailures=18` is mirrored by bounded placement retries and host-specific upgrade placement.
- `AIUseTurbineUpgradeProbability=1.0` means allied power turbines are part of the expected power plan.
- `GlobalControls.AllowParallelAIQueues=no` is respected by the conservative scheduler; it does not assume that separate queues are available.

## Intelligence gates

`[IQ]` provides the native capability ceiling: production `4`, guard-area `1`,
repair/sell `1`, aircraft `3`, and harvester `1`. The strategic layer therefore
does not issue a feature that telemetry cannot verify, and it keeps dispatch,
progress, and completion as separate states.

## Strategic phases

`StrategicDirector` evaluates only current visible entities, economy telemetry,
production telemetry, and attack alerts:

1. `establish`: no owned construction foundation is visible.
2. `defend`: an under-attack alert or power emergency has priority.
3. `stabilize`: combat strength is below the defensive floor.
4. `assemble`: gather a verified force while prerequisites or income are pending.
5. `expand`: positive observed economy flow supports another refinery, power, or production building.
6. `probe`: a visible enemy structure exists and the force is adequate for a first contact.
7. `attack`: visible combat strength exceeds the visible enemy estimate by at least `1.25x`.

The layer never refreshes an enemy position from hidden telemetry and never treats
loss of visibility as proof of destruction. All experiments and phase transitions
are appended to `logs/combat_experiments.jsonl`.
