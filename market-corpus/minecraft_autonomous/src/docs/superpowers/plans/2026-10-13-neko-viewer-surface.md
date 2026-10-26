# Neko configuration and observation surfaces

**Goal:** Make configuration and real Minecraft runtime observations accessible from Neko, with an explicit entry to the user's separately deployed modern viewer.

**Architecture:** Keep the existing hosted TSX configuration panel. Add a declared static observation panel, because hosted TSX runs in an opaque-origin sandbox while the external 3D renderer needs its own browser origin. Both use the public hosted UI context; the observation panel never calls a model or dispatches game actions. Rendering assets and the body-to-viewer transport remain owned by their existing projects.

**Tech Stack:** Public Neko UI SDK, Python, HTML/CSS, native browser ES modules, Node test runner.

**Spec:** `docs/design.md` and the user's request to add configuration and visualization to Neko.

## Constraints and review focus

- Keep Java 1.21.1, existing core pin, explicit start/stop, external runtime ownership and private credentials unchanged.
- Saved status may be stale: display sample time and stale state instead of claiming a live body.
- A viewer page loading does not verify the observed player or render fidelity.
- Viewer URLs must reject credentials, scripts and token-bearing query strings; no runtime data or credentials are forwarded to the viewer.
- Hidden pages do not poll; requests cannot overlap; a failed read retains visibly stale observations.
- Missing viewer endpoint is an explicit empty state, never a fabricated game scene.

## Implementation

- [x] Declare `main` (hosted TSX) and `viewer` (static) in `plugin.toml`; suppress a duplicated legacy surface. Preserve `static/index.html` for older Neko's UI discovery.
- [x] Add `static/observer-data.mjs`, `static/observer.js`, `static/observer.css`, `static/index.html`. Read `/plugin/minecraft_autonomous/hosted-ui/context?kind=panel&id=viewer`; render bounded real status, vital values, native queue, facts and decision samples. Load `viewer.url` only on a user click, with a separate-window fallback.
- [x] Add official `FileDownload` navigation to `ui/panel.tsx` and label saved external settings accurately. Add safe viewer-only configuration updates while an embedded worker is active in `dashboard.py`.
- [x] Add `tests/observer-data.test.mjs` for absent/stale data, zero vital values, bounded samples and unsafe URLs; add a Python regression proving that viewer-only save does not restart a worker and runtime changes remain blocked.
- [x] Verify Node tests, all pytest, Ruff, SDK TypeScript check and official Market structure check. Inspect the archive before commit. Use the installed real Neko API and an isolated headless browser for web rendering.
- [x] Reload only the plugin and verify both surface/source/context routes. Leave the standalone runtime, server, model and desktop processes untouched. Update README / changelog and fetch main before preparing the commit for an ordinary main push; do not publish a release.

## Acceptance evidence and limits

- 97 Python tests and 4 Node tests passed; public SDK TypeScript diagnostics: 0; Ruff passed. Official `check --release --market-release` passed for 0.5.0. The older CLI warns about model declarations it does not recognize; the actual host supports the existing model slots. No release tag was used.
- The running Neko manager advertises exactly two available panels and discovers the static UI. HTML, module, script and stylesheet return the expected content types; the viewer context uses the existing read-only dashboard.
- A headless browser exercised the real Neko manager's `面板` tab and both sub-tabs, saved an unchanged empty viewer URL through the actual hosted action, and opened the observation page through the official SDK button. No page errors occurred. Hidden-panel polling paused; a 390 px layout did not overflow horizontally.
- An isolated viewer fixture verified explicit load/unload, iframe restrictions, no-referrer, invalid URL rejection, zero vitals, escaped observation text and visibly stale data after a failed read. This is UI verification, not real 3D/game fidelity verification.
- No matching viewer endpoint is configured. The pinned 1.21.1 headless core still needs a renderer transport from the separate visualization project. No historical viewer service was started.
- Desktop capture after the user unlocked remained black and activation failed. Native desktop clicks are not claimed as verified; actual plugin-manager rendering and interactions were verified in the browser above.
- During acceptance the external framework recorded a failed run with `brain_transport_error` before the plugin-only reload. The UI correctly shows failure and the last sample. This interface change does not claim to repair that separate runtime fault.

## References checked

- Plugin main: `873436e67cf285ffd9d9a71528ed675b12a61b50`.
- `minecraft-ai-friend` main: `40eb7eea951e5569a68864feb8febfa48052c996`; core pin unchanged.
- `mc-visual-console` main: `aaba46e7` (cinematic cameras and opt-in web controls). Read-only review; no copied deployment or mismatched 1.20.6 assets.
- Local Neko `HostedSurfaceFrame.vue`: static iframe permits scripts/same-origin; hosted TSX permits scripts only. Public UI manifest supports multiple static/hosted panels.
