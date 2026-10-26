"""HUD probes: is a HUD on screen, and is it warning us?

We do **not** OCR health/ammo numbers.  The frozen host has no OCR engine, and
shipping screenshots of a game's HUD font as templates would be both brittle and
unnecessary: the dialog LLM can read the numbers from the frame it is given.

What the local policy needs is different and much cheaper:

* is a HUD present at all (a strong "we are in gameplay, not a menu/loading"
  signal);
* is the health area flashing red (Source turns the number red at low health);
* is the screen edge flashing red (taking damage);
* is there a subtitle band (the LLM can read it, we only report presence).

Temporal stability is the key trick: world pixels change while HUD pixels stay
put, so a per-pixel running variance over a short window separates them without
any template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

from .vision import FrameFeatures

__all__ = ["HudState", "HudTracker", "regions_for_style"]


#: Normalised regions (x, y, w, h).  Bottom corners are where every Source HUD
#: puts health/ammo; the exact numbers differ per game, so these are generous.
HUD_REGIONS: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "hl1": {
        "health": (0.02, 0.80, 0.22, 0.16),
        "ammo": (0.78, 0.80, 0.20, 0.16),
        "weapon": (0.40, 0.86, 0.20, 0.10),
    },
    "hl2": {
        "health": (0.02, 0.78, 0.24, 0.18),
        "ammo": (0.76, 0.78, 0.22, 0.18),
        "weapon": (0.40, 0.86, 0.20, 0.10),
    },
    "portal": {
        "health": (0.02, 0.80, 0.22, 0.16),
        "ammo": (0.78, 0.80, 0.20, 0.16),
        "weapon": (0.40, 0.86, 0.20, 0.10),
    },
    "portal2": {
        # Portal 2's persistent indicators sit in the top-right (health/portal
        # device) and bottom-right (portal dots); the old bottom-left box was a
        # Half-Life assumption that never matched a real Portal 2 frame.
        "health": (0.84, 0.02, 0.14, 0.13),
        "ammo": (0.72, 0.90, 0.26, 0.08),
        "weapon": (0.45, 0.90, 0.10, 0.08),
    },
    "vr": {
        "health": (0.02, 0.78, 0.24, 0.18),
        "ammo": (0.74, 0.80, 0.24, 0.16),
        "weapon": (0.40, 0.86, 0.20, 0.10),
    },
}


def regions_for_style(style: str) -> dict[str, tuple[float, float, float, float]]:
    return dict(HUD_REGIONS.get(style, HUD_REGIONS["hl2"]))


@dataclass
class HudState:
    present: bool = False
    stability: float = 0.0
    health_activity: float = 0.0
    ammo_activity: float = 0.0
    health_red: float = 0.0
    low_health_guess: bool = False
    damage_flash: bool = False
    subtitle: bool = False
    menu_overlay_guess: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "stability": round(self.stability, 3),
            "health_activity": round(self.health_activity, 4),
            "ammo_activity": round(self.ammo_activity, 4),
            "health_red": round(self.health_red, 3),
            "low_health_guess": self.low_health_guess,
            "damage_flash": self.damage_flash,
            "subtitle": self.subtitle,
            "menu_overlay_guess": self.menu_overlay_guess,
            "health_numbers": "not OCR'd locally; read them from the pushed frame",
        }


def _crop(image, region: tuple[float, float, float, float]):
    height, width = image.shape[:2]
    x, y, w, h = region
    x0 = max(0, min(width - 1, int(width * x)))
    y0 = max(0, min(height - 1, int(height * y)))
    x1 = max(x0 + 1, min(width, int(width * (x + w))))
    y1 = max(y0 + 1, min(height, int(height * (y + h))))
    return image[y0:y1, x0:x1]


class HudTracker:
    """Rolling HUD detector.  Cheap enough to run at 4-8 fps."""

    def __init__(self, *, style: str = "hl2", window: int = 6) -> None:
        if np is None:  # pragma: no cover
            raise RuntimeError("numpy is required for HUD tracking")
        self.regions = regions_for_style(style)
        self.window = max(3, int(window))
        self._history: dict[str, list[Any]] = {name: [] for name in self.regions}
        self._last_gray: dict[str, Any] = {}
        self.state = HudState()

    def set_style(self, style: str) -> None:
        if self.regions != regions_for_style(style):
            self.regions = regions_for_style(style)
            self.reset()

    def reset(self) -> None:
        self._history = {name: [] for name in self.regions}
        self._last_gray = {}
        self.state = HudState()

    @staticmethod
    def _gray(patch):
        return (patch[..., 0] * 0.299 + patch[..., 1] * 0.587
                + patch[..., 2] * 0.114).astype("float32")

    @staticmethod
    def _red_ratio(patch) -> float:
        flat = patch.reshape(-1, 3).astype("float32")
        if flat.size == 0:
            return 0.0
        r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
        bright = (r + g + b) > 150
        red = (r > 90) & (r > g * 1.35 + 12) & (r > b * 1.3 + 12)
        denom = max(1, int(bright.sum()))
        return float((red & bright).sum()) / denom

    def observe(self, image, features: FrameFeatures | None = None) -> HudState:
        if np is None:  # pragma: no cover
            return self.state
        array = np.asarray(image)
        if array.ndim != 3 or array.shape[0] < 32 or array.shape[1] < 32:
            return self.state

        stabilities: list[float] = []
        activities: dict[str, float] = {}
        reds: dict[str, float] = {}
        for name, region in self.regions.items():
            patch = _crop(array, region)
            if patch.size == 0:
                continue
            gray = self._gray(patch)
            previous = self._last_gray.get(name)
            activities[name] = float(np.abs(gray - previous).mean() / 255.0) \
                if previous is not None and previous.shape == gray.shape else 0.0
            self._last_gray[name] = gray
            history = self._history.setdefault(name, [])
            history.append(gray)
            if len(history) > self.window:
                del history[0]
            if len(history) >= 3:
                stack = np.stack(history, axis=0)
                std = stack.std(axis=0)
                mean = stack.mean(axis=0)
                stable = (std < 6.0) & (mean > 55.0)
                stabilities.append(float(stable.mean()))
            reds[name] = self._red_ratio(patch)

        stability = max(stabilities) if stabilities else 0.0
        health_activity = activities.get("health", 0.0)
        ammo_activity = activities.get("ammo", 0.0)
        health_red = reds.get("health", 0.0)
        global_motion = features.motion if features is not None else 0.0

        # A HUD is stable *while the world moves*.  When everything is frozen
        # (menu, loading) stability alone would be a false positive, so require
        # either some global motion or an unmistakable static-bright block.
        present = stability > 0.52 and (global_motion > 0.0015 or stability > 0.82)
        low_health = health_red > 0.28 and stability > 0.35
        damage = bool(features is not None and features.red_border > 0.015)
        subtitle = bool(features is not None and features.text_band > 0.28)

        menu_guess = bool(
            features is not None
            and features.frozen
            and features.mean_luma < 0.38
            and features.text_band > 0.24
        )

        self.state = HudState(
            present=present,
            stability=stability,
            health_activity=health_activity,
            ammo_activity=ammo_activity,
            health_red=health_red,
            low_health_guess=low_health,
            damage_flash=damage,
            subtitle=subtitle,
            menu_overlay_guess=menu_guess,
        )
        return self.state
