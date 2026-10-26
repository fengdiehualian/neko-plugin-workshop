import json
import subprocess
import unittest
from pathlib import Path

from plugin.plugins.teach_sister_drive.intent import parse_actions, parse_command

HERE = Path(__file__).parent


class IntentTests(unittest.TestCase):
    def test_shared_control_examples(self):
        for case in json.loads((HERE / "intent_cases.json").read_text(encoding="utf-8")):
            with self.subTest(text=case["text"]):
                plan = parse_command(case["text"])
                self.assertEqual(plan.commands, case["commands"])
                self.assertEqual(bool(plan.note), bool(case.get("note")))
                self.assertEqual(bool(plan.error), not bool(case["commands"]))

    def test_original_text_cannot_be_overridden(self):
        self.assertFalse(parse_command("不要往前开", "forward").commands)
        self.assertEqual(parse_actions("往右", "left"), ["right"])
        self.assertEqual(parse_command("慢慢往前开", "forward", target_speed=60).commands[0]["target_speed"], 5)

    def test_explicit_parameters_are_finite_and_consistent(self):
        for fields in (
            {"target_speed": float("nan")},
            {"target_speed": True},
            {"target_speed": -1},
            {"duration_ms": 1},
            {"duration_ms": 500.5},
            {"steering": 2},
        ):
            with self.subTest(fields=fields):
                self.assertFalse(parse_command("", "forward", **fields).commands)
        self.assertFalse(parse_command("", "left", steering=0.2).commands)
        self.assertFalse(parse_command("", "start_engine", target_speed=5).commands)
        self.assertEqual(parse_command("", "forward").commands, [{"action": "forward"}])

    def test_browser_has_identical_results(self):
        # Compare the whole contract, including errors and notes, not just action
        # names. Node is already used by the repository; no packages are needed.
        script = """
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const {parseCommand} = await import(pathToFileURL(process.argv[1]).href);
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => parseCommand(c.text))));
"""
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                str(HERE.parent / "ui" / "intent.mjs"),
                str(HERE / "intent_cases.json"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        cases = json.loads((HERE / "intent_cases.json").read_text(encoding="utf-8"))
        for case, browser in zip(cases, json.loads(result.stdout), strict=True):
            with self.subTest(text=case["text"]):
                python = parse_command(case["text"])
                self.assertEqual(browser, {"commands": python.commands, "error": python.error, "note": python.note})


if __name__ == "__main__":
    unittest.main()
