# Shared viewer and host boundary

The adapter uses only the public `plugin.sdk.plugin` API. It does not patch,
monkey-patch, import private modules from, or write source/configuration into the
Neko main program. Startup and status do not start gameplay; shutting down an
external observer leaves the independent framework running. An explicit stop is
still handled by its durable mailbox and native drain.

The framework owns game connection, queue, planner, facts and review state.
The shared `mc-visual-console` repository owns renderer/UI source. This adapter
only delivers goals, user feedback and bounded reviews through public APIs.
It does not open a second Minecraft account, implement a second body queue,
poll models for display, or restart the host, model or game server.

Default rendering is driven by ordinary Mineflayer data. Exact connection
version and registry select resources; optional server content requires actual
validated capability/state data and matching original assets. Hostnames, MOTD,
chat and model guesses do not enable a Qiandengji preset. No skills/mana data
means no skills/mana panel; loss/reset clears it. A vanilla test session does not
prove live Qiandengji capability negotiation.

The reference screenshot uses a different renderer entry, third-person camera,
player, resource pack and server abilities. The native entry now adopts the
shared docked toolbar, display settings, bounded chat module and gallery entry.
Native night vision and capture-history provider remain explicitly unavailable.
Complete original scene parity is still tracked separately in the shared viewer.

The 2026-10-15 deployment audit records every tracked Neko file hash and the
creation identity of Neko, its plugin manager, Qwen and the Minecraft server.
Only the exact owned executor is eligible for a normal stop/start handover.
This establishes the checked source/process boundary, not zero shared hardware
cost or complete autonomous survival acceptance.
