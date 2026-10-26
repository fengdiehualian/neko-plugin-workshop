# Qiandengji website onboarding implementation plan

Goal: expose the official invitation account flow in Neko without replacing its game body, model, persona or memories.

Architecture: keep the official MIT Node account helper byte-for-byte in an explicit optional server extension. A thin Python adapter accepts private file paths, validates scope, returns a strict public summary and prepares the existing core's private login configuration. Embedded startup checks imported identities against fresh API status before starting the existing body.

Specification: https://mc.kangqiang.site/join and https://mc.kangqiang.site/agent-api.html, Skill 1.1.0, retrieved 2026-10-23 Asia/Shanghai. Existing roles continue their original connection; the user supplied no invitation token.

Constraints: Java 1.21.1, original core rc.66 and autonomy 0.2.87, no second MCP/queue, no secret values in UI/config/logs, no automatic signup/start, no release/tag/Market submission. Preserve the online ag_NEKO identity and external runtime.

Review focus: pending/unknown never activates or connects; existing identity never registers again; wrong origin/account/address cannot import; secrets and arbitrary server response fields cannot enter public results; active/uncertain native sessions cannot be switched by onboarding.

- [x] Add account adapter tests using fake helper replies and temporary private files. Run the focused test to observe missing implementation.
- [x] Add `qiandengji_account.py`: private file checks, official helper integrity, bounded subprocess invocation, allowlisted summaries, ready gate and non-overwriting login material preparation.
- [x] Add `qiandengji_panel.py`: explicit status/register/activate/import services; preserve native session fences and save only paths/connection fields.
- [x] Integrate optional settings and fresh imported-account preflight into `dashboard.py` / `__init__.py`, leaving legacy accounts untouched.
- [x] Add the optional website panel, public links and private-path inputs in `ui/panel.tsx`; update source-install guidance and version to 0.6.93.
- [x] Test the pinned official helper with fake fetch for persist-before-request, pending/unknown and idempotent activation. Run complete plugin pytest, Node UI/account checks, Ruff and official release/Market structure checks; inspect built contents.
- [ ] Fetch main, inspect and ordinarily push the tested commit. Stop/sync/refresh/start only the adapter through fresh official lifecycle identities; confirm existing body/main/model/Java continuity and installed version.

Execution is native in this session, as authorized by the user's request to implement the plugin update. No additional approval or delegated agent work is needed.
