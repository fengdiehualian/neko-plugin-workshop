# Bundled BrowserSkill CLI

- Component: `bsk` CLI
- Version: `0.1.10`
- Release: <https://github.com/Tencent/BrowserSkill/releases/tag/cli-v0.1.10>
- License: MIT; see `LICENSE.BrowserSkill`

The plugin selects the executable matching the current OS and architecture,
verifies its SHA-256, and executes it directly. No archive extraction or runtime
binary cache is involved.

The executable files are build inputs and are intentionally not stored in Git.
Materialize them from the pinned official release before testing or packaging:

```bash
python scripts/fetch_bsk.py
python scripts/fetch_bsk.py --check
```

The fetcher verifies both the upstream archive digest and the extracted
executable digest recorded in `manifest.json`. CI runs it before the N.E.K.O
release check, so the published `.neko-plugin` still contains all five binaries
and does not download anything at runtime.

| Platform | File | SHA-256 |
| --- | --- | --- |
| macOS ARM64 | `darwin-arm64/bsk` | `357452c2d9e15f3b24a088767eb4447dc56134ee0e32bf89c815e7b543ba987e` |
| macOS x64 | `darwin-x64/bsk` | `ce96809704657e9d18cb51a80d856bc49e41a22767cb3177f6d27e10a1ab275a` |
| Linux ARM64 | `linux-arm64/bsk` | `e4839a89b68ea49f96612da19f7869c2e298f5c7517f70d9dd85f57559325cbc` |
| Linux x64 | `linux-x64/bsk` | `7d94b5cabb82a5fc36d7af2032e7672cf41d6e625541dd4ce242ed80c5056f4d` |
| Windows x64 | `windows-x64/bsk.exe` | `e24090da00c9523eef484ef60ff932e8281183ab59b90ec95d6b22b9ee5a3e37` |

When upgrading, update `manifest.json`, the runtime checksum table, this table,
and the recorded version together. Do not commit the materialized executables.
