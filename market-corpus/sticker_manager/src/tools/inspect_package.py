"""Use the host inspector against a package inside the read-only host sandbox."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from host_isolation import isolated_host

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--host-root", type=Path, default=ROOT.parent / "N.E.K.O")
    args = parser.parse_args()
    with isolated_host(args.host_root) as sandbox:
        code = (
            "import json,sys; from plugin.neko_plugin_cli.core.inspect import inspect_package; "
            "result=inspect_package(sys.argv[1]); assert result.payload_hash_verified; "
            "assert result.package_id=='sticker_manager'; "
            "print(json.dumps(result.model_dump(mode='json'),ensure_ascii=True,indent=2))"
        )
        return subprocess.run(
            [str(sandbox.python), "-c", code, str(args.package.resolve())],
            cwd=sandbox.snapshot, env=sandbox.env, check=False, timeout=120,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
