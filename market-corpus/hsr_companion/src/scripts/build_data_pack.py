"""Build/install the external knowledge pack from pinned public data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog


def main() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry",
        type=Path,
        default=plugin_root / "resources" / "character_registry.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("target") / "hsr-companion-data-pack",
    )
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry, root=args.output_root)
    status = catalog.install_pinned()
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
