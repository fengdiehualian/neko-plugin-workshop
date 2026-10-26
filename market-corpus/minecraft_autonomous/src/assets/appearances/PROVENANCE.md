# Original bundled YUI appearances

These files reproduce the user's approved original Minecraft skin and YSM model from https://github.com/jcs130/mc-agent-neko (source checkout HEAD `9cb808b2ea65e65adc02ab4415abc9b7ef220658`). Skin source commit: `f7641acdee4794aeb4b0733168939a37e7eb3260`; YSM source commit: `cf004bd3fec21de4121e5dbf9fa3d1df27d61a99`.

jcs130 supplied character direction and commissioned the model; Codex authored the original geometry, numerical animation keyframes and packaging. The PNG was generated with imagegen and exported using the supplied source script. Source MIT text, READMEs, original resource manifest and generators are retained in `sources/`. The original `ysm.json` Custom license text is unchanged. This does not license the upstream YUI character design or any Live2D resources; none are bundled.

The original `models/ysm/pack-neko-yui.py` from YSM source commit `cf004bd3fec21de4121e5dbf9fa3d1df27d61a99` is retained as `sources/pack-neko-yui.py.txt`, a source reference rather than adapter Python code. Its 1,301 bytes are unchanged, SHA-256 `c3b211a7cd1a20bf5f941c6fb607ff1fd6371f15ae1bca601823acdebb9f601c`. Copy the reference to its original filename/layout when reproducing the upstream packaging; the bundled runtime assets do not execute it.

Runtime `catalog.json` records exact byte lengths and SHA-256 hashes for the slim PNG and seven unmodified YSM files. The included original ZIP has SHA-256 `dabb81be3a60a8a9927553e8ca53b975f8c5ab999bd5b71f8083d3e6e861b10e`. The skin and YSM default texture both have SHA-256 `f8ed6510dec8647895fb633dde7cad0d3338fe7b39f78e2566d389e4ec33709f`.

The shared viewer uses an explicit `plugin_bundled` source. It preserves original geometry, UVs, animation files and 0.9 width/height scale. Server appearance remains the default; explicit selection changes only this same player's Web body. No server skin upload, YSM installation, Freesia attachment or other client's state is claimed. Equipment, first-person YSM arms, extra-action triggers and complete Java animation/rendering parity remain unavailable or unverified. The original arm and extra resources are retained and integrity checked for future support.

Configure the host-neutral runtime's `[viewer].bundled_appearance_catalog_file` to this installed `catalog.json` path. No private runtime configuration or absolute development path is bundled.
