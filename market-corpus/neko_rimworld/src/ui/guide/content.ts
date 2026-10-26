export const GUIDE_LINKS = {
  host: "https://github.com/Project-N-E-K-O/N.E.K.O/releases",
  hostHelp: "https://github.com/Project-N-E-K-O/N.E.K.O#readme",
  harmony: "https://steamcommunity.com/sharedfiles/filedetails/?id=2009463077",
  mod: "https://github.com/wislap/NekoRimWorld/releases/download/v3.2.0/NekoRimWorld-3.2.0.zip",
  modRelease: "https://github.com/wislap/NekoRimWorld/releases/tag/v3.2.0",
  plugin: "https://github.com/wislap/n.e.k.o_plugin_neko_rimworld/releases",
  pluginSource: "https://github.com/wislap/n.e.k.o_plugin_neko_rimworld",
}

export const GUIDE_SECTIONS = ["prepare", "mod", "plugin", "pair", "model", "chat", "answers", "help"] as const

// Run inside a separate N.E.K.O source checkout, not the installed desktop app.
export const PLUGIN_BUILD_COMMANDS = [
  "git clone https://github.com/wislap/n.e.k.o_plugin_neko_rimworld.git plugin/plugins/neko_rimworld",
  "uv sync",
  "uv run neko-plugin sync neko_rimworld --clean",
  "uv run neko-plugin build neko_rimworld -o neko_rimworld.neko-plugin",
].join("\n")
