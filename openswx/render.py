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
    # every triangle of every face at once: corners in 3D (for the shading) and on screen
    tris3, scr = [], []
    for f in mesh["faces"]:
        V = np.array(f["v"], float).reshape(-1, 3)
        T = np.array(f["t"], int).reshape(-1, 3)
        if not len(T):
            continue
        X, Y, Z = project(V)
        tris3.append(V[T])
        scr.append(np.stack([X[T], Y[T], Z[T]], axis=-1))
    if tris3:
        V3, S3 = np.concatenate(tris3), np.concatenate(scr)
        n = np.cross(V3[:, 1] - V3[:, 0], V3[:, 2] - V3[:, 0])
        nn = np.linalg.norm(n, axis=1)
        keep = nn >= 1e-12
        n = n[keep] / nn[keep, None]
        shade = 0.35 + 0.65 * np.abs(n @ light)
        cols = BODY[None, :] * 255 * shade[:, None] * np.where(n @ d >= 0, 1.0, 0.8)[:, None]
        _tris(img, zbuf, S3[keep], cols)
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


def _tris(img, zbuf, S, cols, budget: int = 4_000_000):
    """Fill triangles S (n, 3 corners, x/y/z on screen) with colours cols (n, 3) into img with the z-buffer, in
    numpy passes over all triangles at once (at most `budget` pixels a pass) instead of one pass per triangle.
    Scanlines: each triangle's rows, each row's span between its edges, then the pixel centres in the span, so
    long thin triangles cost their area, not their bounding box. Nearer (larger z) wins."""
    h, w = zbuf.shape
    x, y, z = S[..., 0], S[..., 1], S[..., 2]
    den = (y[:, 1] - y[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (y[:, 0] - y[:, 2])
    ok = np.abs(den) >= 1e-12
    x, y, z, den, cols = x[ok], y[ok], z[ok], den[ok], cols[ok]
    if not len(x):
        return
    # depth as a plane over the screen: z = a x + b y + c, per triangle
    a = ((y[:, 1] - y[:, 2]) * (z[:, 0] - z[:, 2]) + (y[:, 2] - y[:, 0]) * (z[:, 1] - z[:, 2])) / den
    b = ((x[:, 2] - x[:, 1]) * (z[:, 0] - z[:, 2]) + (x[:, 0] - x[:, 2]) * (z[:, 1] - z[:, 2])) / den
    c = z[:, 2] - a * x[:, 2] - b * y[:, 2]
    # rows whose pixel centre (row + 0.5) the triangle spans
    r0 = np.clip(np.ceil(y.min(1) - 0.5), 0, h).astype(np.int64)
    r1 = np.clip(np.floor(y.max(1) - 0.5), -1, h - 1).astype(np.int64)
    nr = np.maximum(r1 - r0 + 1, 0)
    tri = np.repeat(np.arange(len(x)), nr)
    row = r0[tri] + (np.arange(nr.sum()) - np.repeat(np.cumsum(nr) - nr, nr))
    yc = row + 0.5
    lo = np.full(len(tri), np.inf)
    hi = np.full(len(tri), -np.inf)
    for i, j in ((0, 1), (1, 2), (2, 0)):  # where the row crosses each edge
        ya, yb, xa, xb = y[tri, i], y[tri, j], x[tri, i], x[tri, j]
        cross = ((ya <= yc) & (yc <= yb)) | ((yb <= yc) & (yc <= ya))
        dy = np.where(yb != ya, yb - ya, 1.0)
        xc = np.where(yb != ya, xa + (yc - ya) * (xb - xa) / dy, np.minimum(xa, xb))
        xc2 = np.where(yb != ya, xc, np.maximum(xa, xb))
        lo = np.where(cross, np.minimum(lo, xc), lo)
        hi = np.where(cross, np.maximum(hi, xc2), hi)
    c0 = np.clip(np.ceil(lo - 0.5 - 1e-9), 0, w).astype(np.int64)
    c1 = np.clip(np.floor(hi - 0.5 + 1e-9), -1, w - 1).astype(np.int64)
    span = np.maximum(c1 - c0 + 1, 0)
    zflat, iflat = zbuf.reshape(-1), img.reshape(-1, 3)
    start = 0
    while start < len(span):
        end = start + max(1, int(np.searchsorted(np.cumsum(span[start:]), budget, side="right")))
        cnt = span[start:end]
        rr = np.repeat(np.arange(start, end), cnt)
        px = c0[rr] + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        py = row[rr]
        t = tri[rr]
        zz = a[t] * (px + 0.5) + b[t] * (py + 0.5) + c[t]
        flat = py * w + px
        front = zz > zflat[flat]
        flat, zz, t = flat[front], zz[front], t[front]
        np.maximum.at(zflat, flat, zz)
        win = zz >= zflat[flat]  # (the nearest triangle at each pixel)
        iflat[flat[win]] = cols[t[win]]
        start = end


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
    """Draw the polyline through screen points X, Y (depth Z): all its segments sampled in one numpy pass."""
    h, w = zbuf.shape
    X, Y, Z = np.asarray(X, float), np.asarray(Y, float), np.asarray(Z, float)
    if len(X) < 2:
        return
    dx, dy, dz = np.diff(X), np.diff(Y), np.diff(Z)
    n = (np.maximum(np.abs(dx), np.abs(dy))).astype(np.int64) + 2  # samples per segment, ends included
    seg = np.repeat(np.arange(len(n)), n)
    k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    t = k / (n[seg] - 1)
    xs = np.round(X[seg] + dx[seg] * t).astype(int)
    ys = np.round(Y[seg] + dy[seg] * t).astype(int)
    zs = Z[seg] + dz[seg] * t
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
