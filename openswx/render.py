"""Headless software renderer (numpy z-buffer) producing PNG images of a part for agents."""
from __future__ import annotations

import struct
import zlib

import numpy as np

# camera direction (from target towards eye) and up vector, SOLIDWORKS standard views
VIEWS = {
    "front": ((0, 0, 1), (0, 1, 0)),
    "back": ((0, 0, -1), (0, 1, 0)),
    "top": ((0, 1, 0), (0, 0, -1)),
    "bottom": ((0, -1, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, 1, 0)),
    "isometric": ((1, 1, 1), (0, 1, 0)),
    "trimetric": ((0.8, 0.55, 1.0), (0, 1, 0)),
}
BODY = np.array([0.80, 0.82, 0.86])
SKETCH = {"fully_defined": (0, 0, 0), "under_defined": (0, 0, 220), "over_defined": (220, 0, 0)}


def _basis(view: str):
    d, up = VIEWS.get(view, VIEWS["isometric"])
    d = np.array(d, float); d /= np.linalg.norm(d)
    up = np.array(up, float)
    r = np.cross(up, d); r /= np.linalg.norm(r)
    u = np.cross(d, r)
    return r, u, d


def render_png(doc, view: str = "isometric", width: int = 800, height: int = 600, edges: bool = True,
               fit_points=None) -> bytes:
    """fit_points: optional (N, 3) array framing the view (keeps the camera still across animation frames)."""
    width, height = max(64, min(int(width), 2048)), max(64, min(int(height), 2048))
    mesh = doc.mesh()
    r, u, d = _basis(view)
    pts_all = []
    for f in mesh["faces"]:
        pts_all.append(np.array(f["v"], float).reshape(-1, 3))
    for s in mesh["sketches"]:
        if not s["hidden"]:
            for e in s["entities"]:
                pts_all.append(np.array(e["p"], float).reshape(-1, 3))
    # background: SOLIDWORKS-like vertical gradient
    top, bot = np.array([209, 219, 233], float), np.array([250, 250, 252], float)
    t = np.linspace(0, 1, height)[:, None, None]
    img = (top * (1 - t) + bot * t).repeat(width, axis=1)
    zbuf = np.full((height, width), -np.inf)
    if not pts_all:
        return _png(img.astype(np.uint8))
    P = np.vstack(pts_all) if fit_points is None else np.asarray(fit_points, float)
    sx, sy = P @ r, P @ u
    span = max(sx.max() - sx.min(), sy.max() - sy.min(), 1e-6)
    scale = 0.85 * min(width, height) / span
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2

    def project(V):
        X = (V @ r - cx) * scale + width / 2
        Y = height / 2 - (V @ u - cy) * scale
        return X, Y, V @ d

    light = d * 0.6 + u * 0.5 + r * 0.2
    light /= np.linalg.norm(light)
    for f in mesh["faces"]:
        V = np.array(f["v"], float).reshape(-1, 3)
        T = np.array(f["t"], int).reshape(-1, 3)
        X, Y, Z = project(V)
        for a, b, c in T:
            n = np.cross(V[b] - V[a], V[c] - V[a])
            nn = np.linalg.norm(n)
            if nn < 1e-12:
                continue
            n /= nn
            facing = n @ d
            shade = 0.35 + 0.65 * abs(n @ light)
            col = BODY * 255 * shade if facing >= 0 else BODY * 255 * shade * 0.8
            _tri(img, zbuf, (X[a], Y[a], Z[a]), (X[b], Y[b], Z[b]), (X[c], Y[c], Z[c]), col)
    bias = 1e-2 * span
    if edges:
        for e in mesh["edges"]:
            V = np.array(e["p"], float).reshape(-1, 3)
            _polyline(img, zbuf, *project(V), (20, 20, 20), bias)
    for s in mesh["sketches"]:
        if s["hidden"] or s["absorbed"]:
            continue
        for e in s["entities"]:
            V = np.array(e["p"], float).reshape(-1, 3)
            col = (120, 120, 120) if e.get("construction") else SKETCH.get(e.get("state"), (0, 0, 220))
            _polyline(img, zbuf, *project(V), col, span, always=True)
    return _png(np.clip(img, 0, 255).astype(np.uint8))


def _tri(img, zbuf, p0, p1, p2, col):
    h, w = zbuf.shape
    xs, ys = (p0[0], p1[0], p2[0]), (p0[1], p1[1], p2[1])
    x0, x1 = max(int(np.floor(min(xs))), 0), min(int(np.ceil(max(xs))), w - 1)
    y0, y1 = max(int(np.floor(min(ys))), 0), min(int(np.ceil(max(ys))), h - 1)
    if x0 > x1 or y0 > y1:
        return
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    den = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(den) < 1e-12:
        return
    l0 = ((p1[1] - p2[1]) * (gx - p2[0]) + (p2[0] - p1[0]) * (gy - p2[1])) / den
    l1 = ((p2[1] - p0[1]) * (gx - p2[0]) + (p0[0] - p2[0]) * (gy - p2[1])) / den
    l2 = 1 - l0 - l1
    inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    z = l0 * p0[2] + l1 * p1[2] + l2 * p2[2]
    sub = zbuf[y0:y1 + 1, x0:x1 + 1]
    m = inside & (z > sub)
    sub[m] = z[m]
    img[y0:y1 + 1, x0:x1 + 1][m] = col


def _polyline(img, zbuf, X, Y, Z, col, bias, always=False):
    h, w = zbuf.shape
    for i in range(len(X) - 1):
        n = int(max(abs(X[i + 1] - X[i]), abs(Y[i + 1] - Y[i]))) + 2
        t = np.linspace(0, 1, n)
        xs = np.round(X[i] + (X[i + 1] - X[i]) * t).astype(int)
        ys = np.round(Y[i] + (Y[i + 1] - Y[i]) * t).astype(int)
        zs = Z[i] + (Z[i + 1] - Z[i]) * t
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        xs, ys, zs = xs[ok], ys[ok], zs[ok]
        vis = np.ones_like(zs, bool) if always else zs + bias >= zbuf[ys, xs]
        img[ys[vis], xs[vis]] = col


def _png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_mjpeg_avi(path: str, jpegs: list[bytes], width: int, height: int, fps: int) -> None:
    """Minimal RIFF AVI with one MJPEG video stream (plays in Windows Media Player, VLC, browsers via tools)."""
    import struct

    def chunk(fourcc: bytes, data: bytes) -> bytes:
        return fourcc + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")

    def lst(kind: bytes, data: bytes) -> bytes:
        return b"LIST" + struct.pack("<I", len(data) + 4) + kind + data

    n, maxsz = len(jpegs), max(len(j) for j in jpegs)
    avih = struct.pack("<IIIIIIIIII4I", int(1e6 / fps), maxsz * fps, 0, 0x10, n, 0, 1, maxsz, width, height, 0, 0, 0, 0)
    strh = struct.pack("<4s4sIHHIIIIIIIIhhhh", b"vids", b"MJPG", 0, 0, 0, 0, 1, fps, 0, n, maxsz, 0xFFFFFFFF, 0,
                       0, 0, width, height)
    strf = struct.pack("<IiiHH4sIiiII", 40, width, height, 1, 24, b"MJPG", width * height * 3, 0, 0, 0, 0)
    hdrl = lst(b"hdrl", chunk(b"avih", avih) + lst(b"strl", chunk(b"strh", strh) + chunk(b"strf", strf)))
    movi_data, idx, off = b"", b"", 4
    for j in jpegs:
        c = chunk(b"00dc", j)
        idx += b"00dc" + struct.pack("<III", 0x10, off, len(j))
        movi_data += c
        off += len(c)
    body = b"AVI " + hdrl + lst(b"movi", movi_data) + chunk(b"idx1", idx)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)
