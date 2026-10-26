# Reusing observed objects

Physical reads return instance IDs; definition groups return `def_name`. Keep
those distinctions when making a requested follow-up read:

- Use an instance's returned `id` in `look`, `inspect_actions`, or the explicit
  actors/destinations of `evaluate_activity`. Preserve its map and each role.
  The operation still reads current facts; an ID does not freeze its state.
- Use a returned `def_name` for a definition or definition-filtered population.
  Grouped `count` returns groups, not instance candidates. Its `detail` controls
  group rows, so increasing it does not discover individual weapons.
- Names are exact instance display names, not fuzzy item categories. A returned
  `Thing_Gun_AssaultRifle50845` is more precise than `assault rifle`, which need
  not match the instance label `assault rifle (normal)`. Existing explicit IDs
  require no preliminary identity-discovery call. Ambiguous names remain
  ambiguous; no automatic candidate is selected.

For example, after an instance read has returned that weapon and its map:

```json
{"object":{"kind":"thing","id":"Thing_Gun_AssaultRifle50845","map":"Map_0"},"aspects":["state"],"focus":{"state":["condition","reservations"]}}
```

Read the candidate facts relevant to the question. A request about a few known
ground weapons does not require listing all Pawns or all weapon definitions.
Ground location, ownership and action eligibility remain distinct. State/path
observations keep their actor, target, sample and conditional meaning; conflicting
native reachability and path results can be reported as a route uncertainty.
Further diagnosis is useful when it changes the requested decision or is itself
requested, rather than an automatic prerequisite for a conditional answer.

A partially completed population read can enumerate a row without returning its
ID. That row retains its unavailable identity and unread state. Other rows with
usable IDs can still be read in the existing batch; no retry, wider population
lookup or new actor is silently substituted. Population enumeration completeness
therefore does not imply identity-field or state completeness.
