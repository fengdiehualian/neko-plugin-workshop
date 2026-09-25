"""Rules for activating the companion context outside the game-start edge."""

from __future__ import annotations


def should_activate_running_game_context(
    *, dry_run: bool, game_running: bool, context_active: bool
) -> bool:
    """Return whether live mode needs a one-time context catch-up."""
    return not dry_run and game_running and not context_active
