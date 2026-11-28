"""The openswx brand mark (a red hexagonal cube) as pixels: the app icons (packaging/make_icon.py) and the tray
icon of `openswx mcp` (shell/tray.py) are drawn from it."""

from __future__ import annotations

import numpy as np


def hexagon(size: int) -> np.ndarray:
    """The mark as a size x size RGBA image (uint8), transparent outside the hexagon."""
    y, x = np.mgrid[0:size, 0:size].astype(float) + 0.5
    c, r = size / 2, size * 0.46
    dx, dy = np.abs(x - c), np.abs(y - c)
    inside = (dx <= r * 0.866) & (dy <= r - dx * 0.577)
    t = (x + y) / (2 * size)
    top = y < c - np.abs(x - c) * 0.577  # lighter top face reads as a cube
    left = x < c
    base = np.stack([200 - 60 * t, 20 + 10 * t, 46 + 0 * t], -1)
    shade = np.where(top[..., None], 1.15, np.where(left[..., None], 0.95, 0.75))
    img = np.zeros((size, size, 4), np.uint8)
    img[..., :3] = np.clip(base * shade, 0, 255).astype(np.uint8)
    img[..., 3] = np.where(inside, 255, 0)
    return img
