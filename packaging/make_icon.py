"""Generate packaging/openswx.ico (original brand mark: a red hexagonal cube) without extra dependencies."""
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openswx.shell.brand import hexagon  # noqa: E402  (the mark itself; the tray icon draws it too)


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


def main_icns(out: Path) -> None:
    """The macOS app icon: an ICNS container of PNG entries (ic07 128 px .. ic10 1024 px)."""
    body = b""
    for tag, s in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        im = png_rgba(hexagon(s))
        body += tag + struct.pack(">I", 8 + len(im)) + im
    out.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


if __name__ == "__main__":
    main(Path(__file__).with_name("openswx.ico"))
    main_icns(Path(__file__).with_name("openswx.icns"))
