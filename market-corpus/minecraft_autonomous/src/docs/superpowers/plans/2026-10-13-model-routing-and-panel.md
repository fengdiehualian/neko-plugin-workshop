# Model routing, optional perception and operator panel

**Goal:** Use Neko's public model slots for embedded planning, optionally consume
real core image artifacts, support bounded fast selection with a rules fallback,
and expose a hosted settings/observability panel without changing the host or core.

**Boundaries:** The existing native executor retains its physical queue/reflexes.
External mode observes the standalone runtime and never takes its connection or
provider credentials. Opening the panel performs no model/game/network probe.
Jev/Decider use System One, not Chat Completions; do not copy the upstream Numen
body control or Cortico implementation. Defaults never launch a game or paid model.

1. Add neutral settings and fast selection contracts. Rules remain usable with no
   inference service; bounded System One and Neko slot adapters select advisory
   focus only. Check candidate distributions, confidence, age and errors. Reuse a
   client, no automatic retry, no model deployment, no arbitrary returned actions.
2. Preserve bounded PNG artifacts from the actual MCP connection, use only one
   fresh frame at the request tail, retain artifact references, and omit expired
   or unsupported frames. Bind optional `vision` to an image/tool-capable Neko slot;
   binding/capability/provider failure falls back to `decision` with text facts.
   Standalone image capability is explicit. Never infer capability from a name.
3. Add optional `fast` slot and sanitized route/usage/latency receipts. Missing
   decision slots keep native queue/reflex observation alive with a visible reason;
   cannot claim an unconfigured planner can fulfill natural-language goals.
4. Add `ui/panel.tsx` and public SDK context/actions. Show connection/queue/facts,
   bounded decision and tool receipt samples, model routing and context budgets.
   Allow validated plugin-owned settings and natural-language feedback. Do not show
   credentials, private provider configuration or fabricated thought processes.
5. Offline tests: routing with missing slots/capabilities/provider failure, latest
   image placement and expiration, rule fallback and Jev confidence semantics,
   dashboard read-only/bounded output, settings validation, SDK metadata. Then
   pytest, Ruff, official release/Market check and archive inspection.
6. Reload only the plugin development registration for hosted UI acceptance; keep
   the standalone game session running. Check actual UI, record what was tested
   versus unavailable, fetch upstream/main again and push the validated changes.

Evidence: Neko `docs/design/plugin-model-slots.md`, `docs/plugins/hosted-ui.md`,
`plugin/sdk/hosted-ui/index.d.ts`; user upstream `patches/decider/README.md`,
`world/survival/system_one.py`, `tool_focus.py`; https://docs.typesafe.ai/api and
https://docs.typesafe.ai/confidence. Core pin is unchanged.
