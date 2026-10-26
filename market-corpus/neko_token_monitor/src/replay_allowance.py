"""Replay synthetic allowance observations without NEKO, credentials or networking."""

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path


def source_case_matches(case):
    root = Path(__file__).resolve().parent
    name = "_catfood_replay_sources"
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(root)]
        sys.modules[name] = package
    try:
        if case["adapter"] == "codex":
            module = importlib.import_module(name + ".codex_quota")
            facts = {"remaining": [w["remaining"] for w in module.normalize(case["payload"])["windows"]]}
        elif case["adapter"] == "deepseek":
            module = importlib.import_module(name + ".core")
            facts = {"balances": module.parse_balance(case["payload"])}
        elif case["adapter"] == "desktop":
            module = importlib.import_module(name + ".desktop_quota")
            result = module.parse_panels(case["app"], case["panels"])
            facts = {k: result[k] for k in ("remaining", "unit")}
        else:
            return False
    except ValueError:
        return case.get("error") is True
    return not case.get("error") and facts == case["expect"]


def main():
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("_allowance_replay", root / "allowance.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    fixture = json.loads((root / "replays/allowance-v1.json").read_text())
    if fixture.get("isolated") is not True or fixture.get("contains_real_account_data") is not False:
        raise ValueError("Only isolated synthetic fixtures may be replayed")
    failed = []
    for case in fixture["cases"]:
        scope = {"id": "isolated", "verified": False, "kind": "fixture", "label": "隔离合成样本"}
        observation = engine.normalize(case["state"], case["profile"], scope, now=case["now"])
        actual = engine.choose([observation], now=case["now"])
        if any(actual.get(key) != value for key, value in case["expect"].items()):
            failed.append(case["name"])
    for case in fixture.get("source_cases", []):
        if not source_case_matches(case):
            failed.append(case["name"])
    print(
        json.dumps(
            {
                "isolated": True,
                "cases": len(fixture["cases"]),
                "source_cases": len(fixture.get("source_cases", [])),
                "failed": failed,
                "network_used": False,
                "real_memory_written": False,
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
