from __future__ import annotations

import heapq
import math
from html import escape

import numpy as np

# conductor area (mm2), outside diameter over typical PVC / XLPE insulation (mm), copper resistance (ohm/km at 20 C)
WIRES = {
    "30 AWG": (0.051, 0.80, 338.0), "28 AWG": (0.081, 0.90, 213.0), "26 AWG": (0.129, 1.00, 134.0),
    "24 AWG": (0.205, 1.10, 84.2), "22 AWG": (0.326, 1.30, 52.9), "20 AWG": (0.518, 1.50, 33.3),
    "18 AWG": (0.823, 1.80, 20.9), "16 AWG": (1.31, 2.00, 13.2), "14 AWG": (2.08, 2.40, 8.28),
    "12 AWG": (3.31, 2.90, 5.21), "10 AWG": (5.26, 3.60, 3.28), "8 AWG": (8.37, 4.60, 2.06),
    "0.35 mm2": (0.35, 1.30, 52.0), "0.5 mm2": (0.5, 1.60, 37.1), "0.75 mm2": (0.75, 1.90, 24.7),
    "1 mm2": (1.0, 2.10, 18.5), "1.5 mm2": (1.5, 2.40, 12.2), "2.5 mm2": (2.5, 3.00, 7.41),
    "4 mm2": (4.0, 3.60, 4.61), "6 mm2": (6.0, 4.20, 3.08)}
COLORS = {"BK": "#111", "BN": "#7b4a12", "RD": "#d32f2f", "OG": "#f57c00", "YE": "#fbc02d", "GN": "#388e3c",
          "BU": "#1976d2", "VT": "#7b1fa2", "GY": "#757575", "WH": "#bbb", "PK": "#ec407a", "GNYE": "#9e9d24"}
PACKING = 1.15  # bundle diameter = PACKING * sqrt(sum of wire OD^2): ~75 % fill


def gauge(name: str) -> tuple[float, float, float]:
    key = {k.replace(" ", "").lower(): k for k in WIRES}.get(str(name).replace(" ", "").replace("²", "2").lower())
    if key is None:
        raise ValueError(f"unknown wire gauge {name!r}: one of {', '.join(WIRES)}")
    return WIRES[key]


def bundle_diameter(ods) -> float:
    ods = list(ods)
    if not ods:
        return 0.0
    if len(ods) == 1:
        return float(ods[0])
    return PACKING * math.sqrt(sum(d * d for d in ods))


def parse_end(end: str) -> tuple[str, str]:
    """'J1-3' -> ('J1', '3'); 'J1' -> ('J1', '')."""
    name, sep, pin = str(end).rpartition("-")
    return (name, pin) if sep and name else (str(end), "")


class Network:
    """The route segments as a graph: nodes are the polyline points (merged within `merge` mm, so routes that
    meet join), edges the straight pieces between them."""

    def __init__(self, routes: list, merge: float = 0.5):
        self.nodes: list[np.ndarray] = []
        self.adj: dict[int, list[tuple[int, float]]] = {}
        self.edges: dict[tuple[int, int], float] = {}
        for pts in routes:
            ids = [self._node(np.asarray(p, float), merge) for p in pts]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                k = (min(a, b), max(a, b))
                if k not in self.edges:
                    L = float(np.linalg.norm(self.nodes[a] - self.nodes[b]))
                    self.edges[k] = L
                    self.adj.setdefault(a, []).append((b, L))
                    self.adj.setdefault(b, []).append((a, L))

    def _node(self, p, merge):
        for i, q in enumerate(self.nodes):
            if np.linalg.norm(p - q) <= merge:
                return i
        self.nodes.append(p)
        return len(self.nodes) - 1

    def nearest(self, p) -> tuple[int, float]:
        d = [float(np.linalg.norm(np.asarray(p, float) - q)) for q in self.nodes]
        i = int(np.argmin(d))
        return i, d[i]

    def path(self, a: int, b: int) -> list[int] | None:
        dist, prev, todo = {a: 0.0}, {}, [(0.0, a)]
        while todo:
            d, u = heapq.heappop(todo)
            if u == b:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, L in self.adj.get(u, []):
                if d + L < dist.get(v, math.inf):
                    dist[v], prev[v] = d + L, u
                    heapq.heappush(todo, (d + L, v))
        if b not in dist:
            return None
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]


def _pin_key(p):
    return (0, int(p), "") if str(p).isdigit() else (1, 0, str(p))


def route(connectors: dict, wires: list[dict], routes: list, slack: float = 0.05, end_allowance: float = 25.0,
          current_A: float = 1.0) -> dict:
    """Route every wire: a breakout from each connector to the nearest route node, then the shortest path
    through the network. Length = (path + breakouts) x (1 + slack) + end_allowance per end (strip, crimp,
    service loop). Without a route joining its ends a wire runs straight and is flagged. Returns the wires,
    the segments with their bundle diameters, the connectors with pin use, a wire BOM and warnings."""
    net = Network(routes) if routes else None
    out_w, warnings = [], []
    seg_wires: dict[tuple[int, int], list[str]] = {}
    breakouts: dict[str, list[str]] = {}
    pins: dict[str, set] = {n: set() for n in connectors}
    for w in wires:
        (ca, pa), (cb, pb) = parse_end(w["from"]), parse_end(w["to"])
        for c, p in ((ca, pa), (cb, pb)):
            if c not in connectors:
                raise ValueError(f"wire {w['id']}: no connector {c!r}")
            npins = connectors[c].get("pins")
            if p and npins and p.isdigit() and not 1 <= int(p) <= int(npins):
                raise ValueError(f"wire {w['id']}: {c} has pins 1-{npins}, not {p}")
            if p:
                if p in pins[c]:
                    warnings.append(f"{c}-{p} has more than one wire")
                pins[c].add(p)
        _area, od, ohm_km = gauge(w.get("gauge", "22 AWG"))
        A, B = np.asarray(connectors[ca]["point"], float), np.asarray(connectors[cb]["point"], float)
        nodes = None
        if net and net.nodes:
            ia, da = net.nearest(A)
            ib, db = net.nearest(B)
            nodes = net.path(ia, ib)
        if nodes is not None:
            run = sum(net.edges[(min(a, b), max(a, b))] for a, b in zip(nodes, nodes[1:])) + da + db
            for a, b in zip(nodes, nodes[1:]):
                seg_wires.setdefault((min(a, b), max(a, b)), []).append(w["id"])
            breakouts.setdefault(ca, []).append(w["id"])
            breakouts.setdefault(cb, []).append(w["id"])
        else:
            run = float(np.linalg.norm(B - A))
            warnings.append(f"wire {w['id']} runs straight from {ca} to {cb} (no route joins them)")
        length = run * (1 + slack) + 2 * end_allowance
        out_w.append({"id": w["id"], "from": w["from"], "to": w["to"], "gauge": w.get("gauge", "22 AWG"),
                      "color": w.get("color", ""), "od": od, "length": round(length, 1), "routed": nodes is not None,
                      "resistance_ohm": round(ohm_km * length / 1e6, 5),
                      "voltage_drop_V": round(current_A * ohm_km * length / 1e6, 5), "_nodes": nodes})
    od_of = {w["id"]: w["od"] for w in out_w}
    segments = [{"a": a, "b": b, "length": round(net.edges[(a, b)], 2), "wires": ids,
                 "diameter": round(bundle_diameter(od_of[i] for i in ids), 3),
                 "points": [net.nodes[a].tolist(), net.nodes[b].tolist()]} for (a, b), ids in sorted(seg_wires.items())]
    conns = []
    for n, c in connectors.items():
        ids = breakouts.get(n, [])
        conns.append({"name": n, "component": c.get("component"), "pins": c.get("pins"),
                      "used_pins": sorted(pins[n], key=_pin_key), "point": [round(float(v), 3) for v in c["point"]],
                      "breakout_diameter": round(bundle_diameter(od_of[i] for i in ids), 3) if ids else 0.0})
    totals: dict[tuple[str, str], float] = {}
    for w in out_w:
        totals[(w["gauge"], w["color"])] = totals.get((w["gauge"], w["color"]), 0.0) + w["length"]
    bom = [{"gauge": g, "color": c, "length_m": round(L / 1000, 3)} for (g, c), L in sorted(totals.items())]
    return {"wires": out_w, "segments": segments, "connectors": conns, "bom": bom,
            "total_wire_m": round(sum(w["length"] for w in out_w) / 1000, 3), "warnings": warnings, "_net": net}


def public(r: dict) -> dict:
    return {**{k: v for k, v in r.items() if not k.startswith("_")},
            "wires": [{k: v for k, v in w.items() if not k.startswith("_")} for w in r["wires"]]}


def formboard_svg(r: dict, title: str = "Harness") -> str:
    """Flattened harness: the used route network laid out flat as a tree (true segment lengths at a standard
    scale), connector labels with their breakout lengths, segment lengths and bundle diameters, wire and
    connector tables."""
    net, segs = r["_net"], r["segments"]
    W, H, margin = 420.0, 297.0, 12.0
    pos: dict[int, tuple[float, float]] = {}
    if segs:
        adj: dict[int, list[tuple[int, float]]] = {}
        for s in segs:
            adj.setdefault(s["a"], []).append((s["b"], s["length"]))
            adj.setdefault(s["b"], []).append((s["a"], s["length"]))
        root = max(adj, key=lambda n: (len(adj[n]), -n))
        pos[root] = (0.0, 0.0)
        stack = [(root, None, 0.0)]
        while stack:  # depth first: the longest branch carries straight on, the others fan out at +/-45, +/-90 deg
            u, parent, ang = stack.pop()
            kids = [(v, L) for v, L in sorted(adj[u], key=lambda t: -t[1]) if v != parent and v not in pos]
            for k, (v, L) in enumerate(kids):
                if parent is None:
                    a = math.radians(180.0 * (k % 2) + 35.0 * (k // 2))
                else:
                    a = ang + math.radians((0.0, 45.0, -45.0, 90.0, -90.0)[k % 5])
                pos[v] = (pos[u][0] + L * math.cos(a), pos[u][1] + L * math.sin(a))
                stack.append((v, u, a))
    draw_w, draw_h = W - 2 * margin - 40, H * 0.5
    xs, ys = [p[0] for p in pos.values()] or [0.0], [p[1] for p in pos.values()] or [0.0]
    sx, sy = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
    fit = min(draw_w / sx, draw_h / sy)
    nice = next((s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if 1 / s <= fit), 2000)
    scale = 1 / nice
    ox = margin + 20 + (draw_w - sx * scale) / 2 - min(xs) * scale
    oy = margin + 22 + (draw_h - sy * scale) / 2 - min(ys) * scale

    def P(n):
        return ox + pos[n][0] * scale, oy + pos[n][1] * scale

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Arial, sans-serif"><rect width="{W}" height="{H}" fill="#fff"/>',
           f'<rect x="{margin}" y="{margin}" width="{W - 2 * margin}" height="{H - 2 * margin}" fill="none" stroke="#000" stroke-width="0.5"/>',
           f'<text x="{margin + 4}" y="{margin + 8}" font-size="5" font-weight="600">{escape(title)} - formboard (flattened, 1:{nice})</text>']
    for s in segs:
        (x1, y1), (x2, y2) = P(s["a"]), P(s["b"])
        out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="#444" '
                   f'stroke-width="{max(0.6, min(4.0, s["diameter"] * 0.4)):.2f}" stroke-linecap="round"/>')
        out.append(f'<text x="{(x1 + x2) / 2:.2f}" y="{(y1 + y2) / 2 - 2.5:.2f}" font-size="2.8" text-anchor="middle" '
                   f'fill="#1565c0">{s["length"]:.0f} mm · ⌀{s["diameter"]:.1f}</text>')
    if net is not None and net.nodes:
        for c in r["connectors"]:
            if not c["breakout_diameter"]:
                continue
            i, d = net.nearest(c["point"])
            if i not in pos:
                continue
            x, y = P(i)
            cx, cy = x + 8, y - 8
            out.append(f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{cx:.2f}" y2="{cy:.2f}" stroke="#444" stroke-width="0.8"/>'
                       f'<rect x="{cx - 0.5:.2f}" y="{cy - 4:.2f}" width="12" height="6" fill="#fff" stroke="#000" stroke-width="0.35"/>'
                       f'<text x="{cx + 5.5:.2f}" y="{cy:.2f}" font-size="3" text-anchor="middle" font-weight="600">{escape(c["name"])}</text>'
                       f'<text x="{cx + 13:.2f}" y="{cy:.2f}" font-size="2.4" fill="#555">+{d:.0f} mm</text>')
    y0 = H * 0.64 + margin
    cols = [("Wire", 18), ("From", 26), ("To", 26), ("Gauge", 22), ("Colour", 16), ("Length (mm)", 24), ("Drop V @1 A", 24)]
    x = margin + 6
    out.append(f'<text x="{x}" y="{y0 - 3}" font-size="3.5" font-weight="600">Wire list</text>')
    for k, (h, _w) in enumerate(cols):
        out.append(f'<text x="{x + sum(c[1] for c in cols[:k]):.1f}" y="{y0 + 3:.1f}" font-size="2.8" font-weight="600">{h}</text>')
    for i, w in enumerate(r["wires"][:22]):
        vals = [w["id"], w["from"], w["to"], w["gauge"], w["color"], f'{w["length"]:.0f}', f'{w["voltage_drop_V"]:.4f}']
        for k, v in enumerate(vals):
            fill = COLORS.get(str(w["color"]).upper(), "#000") if k == 4 else "#000"
            out.append(f'<text x="{x + sum(c[1] for c in cols[:k]):.1f}" y="{y0 + 7 + 3.6 * i:.1f}" font-size="2.6" '
                       f'fill="{fill}">{escape(str(v))}</text>')
    x2 = margin + 6 + sum(c[1] for c in cols) + 14
    out.append(f'<text x="{x2}" y="{y0 - 3}" font-size="3.5" font-weight="600">Connectors</text>')
    for i, c in enumerate(r["connectors"][:22]):
        used = f'{len(c["used_pins"])}/{c["pins"]}' if c.get("pins") else str(len(c["used_pins"]))
        out.append(f'<text x="{x2}" y="{y0 + 3 + 3.6 * i:.1f}" font-size="2.6">{escape(c["name"])} '
                   f'({escape(str(c.get("component") or "-"))}) - pins used {used}</text>')
    out.append(f'<text x="{x2}" y="{H - margin - 4}" font-size="2.8">Total wire {r["total_wire_m"]:.2f} m</text></svg>')
    return "\n".join(out)
