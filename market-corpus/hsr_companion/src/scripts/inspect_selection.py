"""Developer-only local HUD pixel measurements; never used as identity proof."""
from pathlib import Path

import numpy as np
from PIL import Image

for name in ("live-hud-before-switch.png", "live-hud.png"):
    image = Image.open(Path(__file__).resolve().parents[1] / "build/recognition-checks" / name).convert("RGB")
    pixels = np.asarray(image).astype(np.float32)
    delta = np.abs(pixels[1:] - pixels[:-1]).mean(axis=2)
    print(name)
    for i, center in enumerate((508, 632, 756, 880), 1):
        left = (delta[center + 15:center + 55, 310:420] > 12).mean(axis=1)
        right = (delta[center + 15:center + 55, 650:700] > 12).mean(axis=1)
        scores = np.minimum(left, right)
        index = int(scores.argmax())
        print(i, round(float(scores[index]), 3), int(center + 15 + index))
