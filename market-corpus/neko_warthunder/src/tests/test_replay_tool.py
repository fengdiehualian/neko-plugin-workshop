"""Offline replay tool contract tests."""

from __future__ import annotations

import contextlib
import io


def test_synthetic_replay_covers_v16_kill_and_death_events():
    from neko_warthunder.tools import replay

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = replay.main([])

    text = output.getvalue()
    assert rc == 0
    assert "you_killed/warning" in text
    assert "you_died/critical" in text
    assert "is_my_kill" not in text
    assert "is_my_death" not in text
