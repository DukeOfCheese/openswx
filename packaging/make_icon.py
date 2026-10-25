"""Generate packaging/openswx.ico (original brand mark: a red hexagonal cube) without extra dependencies."""
import struct
import zlib
from pathlib import Path

import numpy as np


def hexagon(size: int) -> np.ndarray:
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


def png_rgba(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(out: Path) -> None:
    sizes = [256, 64, 48, 32, 16]
    images = [png_rgba(hexagon(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, im in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(im), offset + len(data))
        data += im
    out.write_bytes(header + entries + data)


if __name__ == "__main__":
    main(Path(__file__).with_name("openswx.ico"))
