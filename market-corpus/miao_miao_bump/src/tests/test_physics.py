"""Run the actual browser physics engine in Node, without a DOM or server."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_physics_regressions() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the physics regression suite")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/physics.test.cjs", "tests/battle.test.cjs", "tests/maps.test.cjs", "tests/rounds.test.cjs", "tests/scoring.test.cjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
