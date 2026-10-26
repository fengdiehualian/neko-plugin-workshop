"""Opt-in PoC arm through the real resident/formal chain in isolated memory.

No installed class substitution: this process exits after the audit. The
ordinary runtime, guards, query budget and fake host/store remain unchanged.
"""
import argparse
import asyncio
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import sys
from types import SimpleNamespace


def main():
    from plugin.plugins.hsr_companion.adapters import local_vision
    from plugin.plugins.hsr_companion.experiments.regional_reader import RegionalReader
    from plugin.plugins.hsr_companion.scripts.audit_dynamic_handoff import audit
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("full", "regional"), required=True)
    parser.add_argument("--page", choices=("exploration_hud", "relic_detail"), required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--resident-seconds", type=float, default=60)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("Use a new plugin dist report path")
    captured = io.StringIO()
    class Tee:
        def write(self, value):
            captured.write(value)
            return sys.__stdout__.write(value)
        def flush(self):
            sys.__stdout__.flush()
    original = local_vision.LocalVisionAdapter
    class ExperimentalAdapter(original):
        @local_vision.serial_ocr(cooperative=True)
        def analyze(self, payload, *, cancelled=None, on_scene=None):
            return RegionalReader(self._get_backend(), self.catalog).analyze(payload, cancelled=cancelled, on_scene=on_scene)
        @local_vision.serial_ocr(cooperative=True)
        def analyze_scene(self, payload, *, cancelled=None, navigation=False):
            value = RegionalReader(self._get_backend(), self.catalog).analyze(payload, cancelled=cancelled)
            value["recognition"].update(scene_only_query=True, navigation_only_query=navigation)
            return value
    if args.arm == "regional":
        local_vision.LocalVisionAdapter = ExperimentalAdapter
    try:
        with redirect_stdout(Tee()):
            asyncio.run(audit(SimpleNamespace(assistant_url="http://127.0.0.1:8220", samples=2,
                resident_seconds=args.resident_seconds, sample_interval=5, resident_commits=1, setup_seconds=90,
                expected_page=args.page, interactive=False, compact=True, query=args.query, no_independent_reread=True)))
    finally:
        local_vision.LocalVisionAdapter = original
        rows = []
        for line in captured.getvalue().splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except ValueError:
                pass
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as output:
            json.dump({"experiment": "regional_poc_resident_v1", "arm": args.arm,
                       "real_model_real_capture": True, "fake_host_store_only": True,
                       "installed_plugin_changed": False, "results": rows}, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
