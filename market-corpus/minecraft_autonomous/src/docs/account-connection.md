# Human account and connection controls

The configuration panel distinguishes local vanilla and custom/public connections.
The optional Qiandeng button fills an unsaved draft with `micro.kangqiang.site:25702`,
Java `1.21.1`, and `ag_NEKO`. It preserves the selected authentication mode. Saving
does not start a game; the user must explicitly start the configured runtime.
Generic defaults remain unconfigured/local, offline, and stopped.

An offline nickname is not a Microsoft account. Microsoft mode uses the existing
native Mineflayer/prismarine-auth flow and its account-scoped private profiles
folder. No new OAuth provider, token cache, or access-token configuration is added.
The configured account identifier may be a cache alias; never enter an access
token, password or device code in the plugin settings.

To enable the human account page, set `[operator] port = 28986` (or another unused
unprivileged port; default `0` disables it), save, then explicitly start a matching
core. Embedded and standalone launches use the same contract. External observer
mode also needs this setting in the standalone configuration and the same data
directory/account identity. Java-client bodies use their own login UI and reject
this Mineflayer operator setting.

The native listener binds only to `127.0.0.1`. Its `operator.json` descriptor lives
in the selected account's private native data directory and contains only schema,
identity, instance ID and port. The plugin derives a bounded localhost link from
that file; it never fetches or proxies authentication state. A link is not a
liveness claim. The native page/API rejects a stale process instance and checks
Host, Origin and the process control token before actions. The descriptor is
removed on normal shutdown; stale links fail closed after a restart. Local OS
processes/files are the trust boundary; this is not remote account administration.

Open the page in a browser on the runtime machine. It shows requested versus
actual version, offline/Microsoft state, the official verification link, device
code and expiry, and the actual authorized profile when available. Connect is
explicit; cancel uses native disconnect. Cancel stops the current connection but
does not delete cached authorization or revoke the Microsoft account globally.
Use the plugin stop control to stop the whole autonomous runtime. Missing native
capability returns unavailable guidance rather than an invented login result.
Codes and control tokens stay in the native human page, outside Neko model tools,
reviews, metrics and public viewer streams.

The panel exposes the bundled appearance catalog path, Web default model and
the viewer's YUI slim, YSM and server-follow controls. Neko embedded presentation
defaults to original YUI YSM; a saved browser choice, including server-follow,
wins. Preferences use the unchanged opaque server/account identity and survive
reconnect/respawn. Old URL-only preferences remain stored and require one-time
reselection because their server provenance is unknown. Standalone defaults to
server-follow and owns its own external runtime settings. These affect Web presentation. They do
not set a global Microsoft skin or establish what another server/player sees.
Actual server appearance remains server/account-authoritative; unsupported server
skin control is reported as unavailable.

The 0.5.48 candidate pins reviewed autonomy .69/core .13 and shared viewer
567d95e6. Updating source or a package does not restart the runtime or prove real
Microsoft login, server-visible skins or the installed Web scene.
