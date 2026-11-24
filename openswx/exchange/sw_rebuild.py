"""Rebuild a SOLIDWORKS part's feature history in openswx, without SOLIDWORKS (docs/PLAN.md, milestone F5).

The feature tree, dimensions and end conditions come from sw_tree, the sketches from sw_sketch. What the
stream does not (yet) give away, such as the side an extrusion goes to or the edges a fillet rounds, is
settled against the exact Parasolid body stored in the same file: each candidate is scored by how well the
body it produces agrees with the stored one. The rebuilt part is only accepted when its volume and area
match the stored body.
"""
from __future__ import annotations

import copy
import itertools
import math
import re
import time

import numpy as np

from openswx.core.document import DocumentError, PartDocument

STD_PLANES = {"Front Plane", "Top Plane", "Right Plane", "Front", "Top", "Right"}
# features that add no geometry to the body
SKIP = {"moRefPlane_c", "moRefAxis_c", "moRefPoint_c", "moCoordSys_c", "moSensor_c", "moOriginProfileFeature_c",
        "moMateReferenceFeature_c", "moSketchBlockDef_c", "moAttribute_c", "moAttributeDef_c", "moScalerParameter_c",
        "moStringParameter_c", "moIntegerParameter_c", "moNoteData_c", "moDetailCabinet_c", "moLockFeature_c",
        "moDesignTableFeature_c", "mo3DProfileFeature_c", "moSheetMetal_c", "moWeldmentFeature_c", "moFlatPattern_c",
        "moCutListFolder_c"}
# features that change faces but not the solid (split lines): left out and reported
# base bodies that are not editable in SOLIDWORKS either: imported, or derived / mirrored from another part
FIXED_BASE = {"moBaseBody_c", "moStock_c", "moMirrorStock_c"}
NEUTRAL = {"moPLine_c", "moCosmeticThread_c", "moConnectionPoint_c", "moRoutePoint_c",  # (no geometry)
           "moSketchBitmap_c", "moWeldSymbol_c", "moCosmeticWeldBeadSymbol_c", "moPlaneSection_c",
           "moSMEnvTableFeat_c", "moTemplateSheetMetal_c"}
TOL = 1e-3  # mm, point on the stored body's boundary
MATCH = 1e-3  # relative volume / area agreement for accepting the rebuild


class RebuildError(Exception):
    pass


_PROGRESS = [None]  # (in a rebuild_isolated child) the pipe that reports the feature being tried
_AVOID = [set()]  # features whose trials crashed the kernel: taken from the stored body instead
_TRUST = [False]  # retrying with every built feature kept: a decoded profile that builds is not replaced
_REVOLVE_AXES: list = [None]  # standard axes a revolve without a decoded sketch may turn about
_DEADLINE = [math.inf]  # time.monotonic() when the current rebuild stops trying: later features are imported
_LUMP = [math.inf]  # time.monotonic() when the rest of the part comes in as one imported feature
_TAGGED: list = [None]  # stored faces the feature being rebuilt made (FACE_ID), when they can be compared
_MOVED_LATER: list = [False]  # a later feature scales / moves the body: the stored extents are not this feature's
_LATER_SCALE: list = [1.0]  # the product of later uniform scale factors (stored extents / this = this feature's)


def _log(msg: str) -> None:
    """Why a feature fell back to imported geometry (OPENSWX_REBUILD_LOG=1 prints it)."""
    import os
    if os.environ.get("OPENSWX_REBUILD_LOG"):
        print("[sw_rebuild]", msg, flush=True)


_BASE_TRIALS = [False]  # the base feature is being tried: its trials are not cut short (see rebuild_part)


def _check_time() -> None:
    import time
    if time.monotonic() > _DEADLINE[0] and not _BASE_TRIALS[0]:
        raise RebuildError("the history needs more trials than the time budget allows")


def _sketch_dict(sk: dict) -> tuple[dict, list[str]]:
    """An openswx sketch (params["sketch"]) from a decoded SOLIDWORKS sketch; also its centerline ids."""
    points, ents, n = {}, {}, 0
    pid = {}

    def p(i):
        nonlocal n
        if i not in pid:
            n += 1
            pid[i] = f"P{n}"
            points[pid[i]] = [float(v) for v in sk["points"][i]]
        return pid[i]

    degree: dict = {}
    for a, b in sk["lines"]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    ends = [(s, e) for _, s, e in sk["arcs"] if s != e] + [(sp["pts"][0], sp["pts"][-1]) for sp in sk.get("splines", [])]
    for s, e in ends:
        degree[s] = degree.get(s, 0) + 1
        degree[e] = degree.get(e, 0) + 1
    axes = []
    line_ids, arc_ids = {}, {}  # decoded line (a, b) / arc centre + ends -> entity id
    cons_used = set()  # a flagged pair marks one line: a centerline can coincide with a profile edge
    for a, b in sk["lines"]:
        n += 1
        eid = f"Line{n}"
        line_ids[frozenset((a, b))] = eid
        # a lone segment is taken for a centerline (revolve axis); construction lines are flagged in their records
        # (a flagged construction line that closes a chain, e.g. a centerline along a revolve profile, is
        # treated as profile geometry: SOLIDWORKS closes such profiles with it)
        closes = degree[a] == 2 or degree[b] == 2
        flagged = frozenset((a, b)) in sk.get("construction", ()) and frozenset((a, b)) not in cons_used
        if flagged:
            cons_used.add(frozenset((a, b)))
        open_end = (degree[a] == 1 and degree[b] == 1) or (flagged and not closes)
        if flagged and closes:
            axes.append(eid)  # still a revolve axis candidate
        ents[eid] = {"type": "line", "p1": p(a), "p2": p(b), "construction": open_end}
        if open_end:
            axes.append(eid)
    for c, s, e in sk["arcs"]:
        n += 1
        if s == e:
            r = math.dist(sk["points"][c], sk["points"][s])
            ents[f"Circle{n}"] = {"type": "circle", "c": p(c), "r": r, "construction": False}
            arc_ids[(c, frozenset((s, e)))] = f"Circle{n}"
        else:
            ents[f"Arc{n}"] = {"type": "arc", "c": p(c), "s": p(s), "e": p(e), "construction": False}
            arc_ids[(c, frozenset((s, e)))] = f"Arc{n}"
    for c, far, minor in sk.get("ellipses", []):
        n += 1
        ents[f"Ellipse{n}"] = {"type": "ellipse", "c": p(c), "m": p(far), "b": float(minor), "construction": False}
    for sp in sk.get("splines", []):
        n += 1
        ents[f"Spline{n}"] = {"type": "spline", "pts": [p(i) for i in sp["pts"]], "closed": False, "construction": False,
                              **({"bspline": sp["bspline"]} if "bspline" in sp else {})}
    # the sketch's own relations (decoded), then relations read off the geometry where none was decoded: lines
    # that are exactly horizontal / vertical (what SOLIDWORKS adds to rectangles and most sketched lines), so
    # edited dimensions move the geometry as expected
    rels = []
    for r in _decoded_relations(sk, pid, line_ids, arc_ids):
        n += 1
        rels.append({"id": f"R{n}", **r})
    for eid, e in ents.items():
        if e["type"] == "line" and not e["construction"]:
            (x1, y1), (x2, y2) = points[e["p1"]], points[e["p2"]]
            if abs(y1 - y2) <= 1e-9 * max(1.0, abs(x1 - x2)) and abs(x1 - x2) > 1e-9:
                n += 1
                rels.append({"id": f"R{n}", "type": "horizontal", "refs": [eid]})
            elif abs(x1 - x2) <= 1e-9 * max(1.0, abs(y1 - y2)) and abs(y1 - y2) > 1e-9:
                n += 1
                rels.append({"id": f"R{n}", "type": "vertical", "refs": [eid]})
    # a line ending on an arc along the arc's tangent there: tangent (fillets, slots)
    for lid, ln in ents.items():
        if ln["type"] != "line" or ln["construction"]:
            continue
        for aid, ar in ents.items():
            if ar["type"] != "arc" or ar["construction"]:
                continue
            for end in (ln["p1"], ln["p2"]):
                if end not in (ar["s"], ar["e"]):
                    continue
                other = ln["p2"] if end == ln["p1"] else ln["p1"]
                (ex, ey), (ox, oy), (cx, cy) = points[end], points[other], points[ar["c"]]
                dx, dy, rx, ry = ox - ex, oy - ey, ex - cx, ey - cy
                if abs(dx * rx + dy * ry) <= 1e-7 * max(1.0, math.hypot(dx, dy) * math.hypot(rx, ry)):
                    n += 1
                    rels.append({"id": f"R{n}", "type": "tangent", "refs": [lid, aid]})
                    break
    # relations that cannot repeat the ones above: perpendicular slanted lines meeting at a point, arcs meeting
    # along a common tangent, arcs / circles about the same centre written as separate points
    hv = {r["refs"][0] for r in rels if r["type"] in ("horizontal", "vertical")}
    lines = [(k, e) for k, e in ents.items() if e["type"] == "line" and not e["construction"] and k not in hv]
    for i, (la, a_) in enumerate(lines):
        for lb, b_ in lines[i + 1:]:
            common = {a_["p1"], a_["p2"]} & {b_["p1"], b_["p2"]}
            if len(common) != 1:
                continue
            (ax1, ay1), (ax2, ay2) = points[a_["p1"]], points[a_["p2"]]
            (bx1, by1), (bx2, by2) = points[b_["p1"]], points[b_["p2"]]
            ua, ub = (ax2 - ax1, ay2 - ay1), (bx2 - bx1, by2 - by1)
            if abs(ua[0] * ub[0] + ua[1] * ub[1]) <= 1e-9 * math.hypot(*ua) * math.hypot(*ub):
                n += 1
                rels.append({"id": f"R{n}", "type": "perpendicular", "refs": [la, lb]})
    round_ = [(k, e) for k, e in ents.items() if e["type"] in ("arc", "circle") and not e["construction"]]
    for i, (ka, a_) in enumerate(round_):
        for kb, b_ in round_[i + 1:]:
            ca, cb = points[a_["c"]], points[b_["c"]]
            if a_["c"] != b_["c"] and math.dist(ca, cb) <= 1e-9 * max(1.0, abs(ca[0]) + abs(ca[1])):
                n += 1
                rels.append({"id": f"R{n}", "type": "concentric", "refs": [ka, kb]})
                continue
            if a_["type"] != "arc" or b_["type"] != "arc":
                continue
            common = {a_["s"], a_["e"]} & {b_["s"], b_["e"]}
            if len(common) != 1 or a_["c"] == b_["c"]:
                continue
            q = points[next(iter(common))]
            ra, rb = (q[0] - ca[0], q[1] - ca[1]), (q[0] - cb[0], q[1] - cb[1])
            if abs(ra[0] * rb[1] - ra[1] * rb[0]) <= 1e-9 * math.hypot(*ra) * math.hypot(*rb):  # centres in line
                n += 1
                rels.append({"id": f"R{n}", "type": "tangent", "refs": [ka, kb]})
    seen, kept = set(), []  # (an inferred relation the sketch already has: once)
    for r in rels:
        key = (r["type"], frozenset(r["refs"]))
        if key not in seen:
            seen.add(key)
            kept.append(r)
    data = {"points": points, "entities": ents, "relations": kept, "dimensions": [], "counter": n, "status": {}}
    data["dimensions"] = _sketch_dimensions(sk, data, pid, line_ids, arc_ids)
    return data, axes


# swConstraintType_e codes of decoded relations -> openswx relation types (others are left to the geometry)
_REL_TYPES = {4: "horizontal", 5: "vertical", 6: "tangent", 7: "parallel", 8: "perpendicular", 10: "concentric",
              12: "midpoint", 14: "equal"}


def _decoded_relations(sk: dict, pid: dict, line_ids: dict, arc_ids: dict) -> list:
    """The sketch's decoded relations whose references resolve to rebuilt entities: [{"type", "refs"}]."""
    handles, order = sk.get("handles", {}), sk.get("point_order", [])
    out = []
    for r in sk.get("relations", []):
        kind = _REL_TYPES.get(r["type"])
        if kind is None:
            continue
        refs = []
        for cls, idx in r["refs"]:
            h = handles.get((cls, idx))
            if h and h[0] == "line" and frozenset(h[1]) in line_ids:
                refs.append(line_ids[frozenset(h[1])])
            elif h and h[0] == "arc" and (h[1][0], frozenset(h[1][1:])) in arc_ids:
                refs.append(arc_ids[(h[1][0], frozenset(h[1][1:]))])
            elif cls == "sgPointHandle" and idx < len(order) and order[idx] in pid:
                refs.append(pid[order[idx]])
            else:
                break
        # (only with as many references as the relation takes: a midpoint whose point is the sketch origin,
        # for one, lists only its line)
        need = 1 if kind in ("horizontal", "vertical") else 2
        # and only on the kinds of entity it applies to (a handle index can name another kind of segment)
        kinds = sorted("line" if x.startswith("Line") else "point" if x.startswith("P") else "round" for x in refs)
        allowed = {"horizontal": [["line"]], "vertical": [["line"]], "parallel": [["line", "line"]],
                   "perpendicular": [["line", "line"]], "tangent": [["line", "round"], ["round", "round"]],
                   "concentric": [["round", "round"]], "midpoint": [["line", "point"]],
                   "equal": [["line", "line"], ["round", "round"]]}[kind]
        if len(refs) == len(r["refs"]) == need and len(set(refs)) == len(refs) and kinds in allowed:
            out.append({"type": kind, "refs": refs})
    return out


def _sketch_dimensions(sk: dict, data: dict, pid: dict, line_ids: dict, arc_ids: dict) -> list:
    """openswx dimensions for the decoded SOLIDWORKS sketch dimensions whose references resolve and whose value
    the geometry already has (so they drive the sketch without moving it)."""
    from openswx.core.sketch import Sketch
    refmap, handles, order = sk.get("refmap", {}), sk.get("handles", {}), sk.get("point_order", [])
    probe = Sketch(data)
    out = []
    for dim in sk.get("dimensions", []):
        refs = []
        for word, idx in dim["refs"]:
            cls = refmap.get(word)
            h = handles.get((cls, idx)) if cls else None
            if h and h[0] == "line" and frozenset(h[1]) in line_ids:
                refs.append(("line", line_ids[frozenset(h[1])]))
            elif h and h[0] == "arc" and (h[1][0], frozenset(h[1][1:])) in arc_ids:
                refs.append(("arc", arc_ids[(h[1][0], frozenset(h[1][1:]))]))
            elif cls is None and idx < len(order) and order[idx] in pid:  # (a point of the sketch geometry)
                refs.append(("point", pid[order[idx]]))
            else:
                break
        if len(refs) != len(dim["refs"]):
            continue
        kinds = sorted(k for k, _ in refs)
        if dim["angle"]:
            types = ["angle"] if kinds == ["line", "line"] else []
            values = [math.degrees(v) for v in dim["values"]]
        else:
            types = ({1: {"line": ["length"], "arc": ["diameter", "radius"], "point": []}}.get(len(refs), {})
                     .get(kinds[0], []) if len(refs) == 1 else ["distance", "hdistance", "vdistance"])
            values = [v * 1000.0 for v in dim["values"]]
        for t in types:
            d = {"id": f"D{len(out) + 1}", "type": t, "refs": [r for _, r in refs], "driven": False}
            try:
                got = probe.measure(d)
            except Exception:
                continue
            match = next((v for v in values if abs(got - v) <= 1e-6 * max(1.0, abs(v))), None)
            if match is not None and not any(sorted(o["refs"]) == sorted(d["refs"]) for o in out):
                out.append({**d, "value": match})
                break
    return out


def _mesh_volume(shape, tol: float | None = None) -> float:
    """Volume from a fine triangulation (divergence theorem): OCC's surface integrals are unreliable on some
    freeform faces, a mesh is not. Default tolerance: 0.005 mm, coarser for large parts (5e-4 of the size)."""
    import numpy as np
    if all(f.geom_type.name in ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS") for f in shape.faces()):
        return shape.volume  # exact for analytic faces (and meshing a large part is slow)
    try:
        if tol is None:
            bb = shape.bounding_box()
            tol = max(5e-4 * (bb.max - bb.min).length, 0.005)
        vs, tris = shape.tessellate(tol, 0.05)
    except Exception:  # a face the mesher cannot triangulate
        return shape.volume
    v = np.array([[p.X, p.Y, p.Z] for p in vs])
    t = np.array(tris)
    return float(abs(np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum()) / 6.0)


def _triangles(shape, tol: float | None = None):
    """The shape's surface as an (n, 3, 3) array of triangles (by default 0.1 % of its size), or None."""
    import numpy as np
    try:
        if tol is None:
            bb = shape.bounding_box()
            tol = max(1e-3 * (bb.max - bb.min).length, 1e-3)
        vs, tris = shape.tessellate(tol, 0.2)
    except Exception:
        return None
    v = np.array([[p.X, p.Y, p.Z] for p in vs])
    return v[np.array(tris)] if len(tris) else None


def _inside_mesh(tri, pts):
    """Points inside a closed triangle mesh (ray parity along a skew direction), vectorised. Triangles are
    binned on a grid across the ray direction, so each point is tested only against those its ray can meet."""
    import numpy as np
    d = np.array([0.5773, 0.5779, 0.5767])
    d /= np.linalg.norm(d)
    v0, e1, e2 = tri[:, 0], tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    pv = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, pv)
    ok = np.abs(det) > 1e-14
    tri, v0, e1, e2, pv, inv = tri[ok], v0[ok], e1[ok], e2[ok], pv[ok], 1.0 / det[ok]
    out = np.zeros(len(pts), bool)
    if not len(tri):
        return out
    # the plane across the ray: axes a, b; each triangle's extent there, each point's position
    a = np.cross(d, [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(d, a)
    ta, tb = tri @ a, tri @ b  # (n, 3)
    lo_a, hi_a, lo_b, hi_b = ta.min(1), ta.max(1), tb.min(1), tb.max(1)
    pa, pb = pts @ a, pts @ b
    g = max(1, min(64, int(np.sqrt(len(tri) / 8))))
    a0, a1 = min(lo_a.min(), pa.min()), max(hi_a.max(), pa.max())
    b0, b1 = min(lo_b.min(), pb.min()), max(hi_b.max(), pb.max())
    sa, sb = max(a1 - a0, 1e-12) / g, max(b1 - b0, 1e-12) / g
    cell = lambda x, x0, s_: np.clip(((x - x0) / s_).astype(int), 0, g - 1)  # noqa: E731
    ia0, ia1, ib0, ib1 = cell(lo_a, a0, sa), cell(hi_a, a0, sa), cell(lo_b, b0, sb), cell(hi_b, b0, sb)
    # every (cell, triangle) pair, built with array operations and sorted by cell
    na, nb = ia1 - ia0 + 1, ib1 - ib0 + 1
    cnt = na * nb
    owner = np.repeat(np.arange(len(tri)), cnt)
    local = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    keys = (ia0[owner] + local // nb[owner]) * g + ib0[owner] + local % nb[owner]
    order = np.argsort(keys, kind="stable")
    keys, owner = keys[order], owner[order]
    pkey = cell(pa, a0, sa) * g + cell(pb, b0, sb)
    porder = np.argsort(pkey, kind="stable")
    ukeys, starts = np.unique(pkey[porder], return_index=True)
    ends = np.append(starts[1:], len(porder))
    for c, s0, s1 in zip(ukeys, starts, ends):
        lo, hi = np.searchsorted(keys, c, "left"), np.searchsorted(keys, c, "right")
        if lo == hi:
            continue
        cand = owner[lo:hi]
        idx = porder[s0:s1]
        V0, E1, E2, PV, INV = v0[cand], e1[cand], e2[cand], pv[cand], inv[cand]
        idx = np.array(idx)
        for k in range(0, len(idx), 64):
            p = pts[idx[k:k + 64]][:, None, :]
            tv = p - V0[None]
            u = np.einsum("mij,ij->mi", tv, PV) * INV
            qv = np.cross(tv, E1[None])
            w = np.einsum("j,mij->mi", d, qv) * INV
            t = np.einsum("ij,mij->mi", E2, qv) * INV
            hit = (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 0)
            out[idx[k:k + 64]] = hit.sum(axis=1) % 2 == 1
    return out


class _Oracle:
    """The stored body: score candidate bodies against it."""

    def __init__(self, shape):
        from build123d import Compound
        self.shape = shape
        self.skin = Compound(shape.faces())
        self.volume = _mesh_volume(shape) if shape.solids() else 0.0
        self.freeform = any(f.geom_type.name not in ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS")
                            for f in shape.faces())
        self.area = sum(f.area for f in shape.faces())

    def score_doc(self, doc: PartDocument) -> float:
        """score() of the part's solid body, or for a surface-only stored body, how close the surface bodies are."""
        return self.score_ctx(doc.ctx)

    def score_ctx(self, ctx) -> float:
        if self.shape.solids():
            return self.score(ctx.body)
        from build123d import Compound
        sheets = list(ctx.surfaces.values())
        return -self.deviation(Compound(sheets)) if sheets else -math.inf

    def score(self, body) -> float:
        """2 * (volume shared with the stored body) - volume: highest for the body that matches."""
        if body is None:
            return -math.inf
        vol = body.volume
        if not math.isfinite(vol) or vol > 1e15 or (vol < 1e-9 and self.volume > 0):
            return -math.inf  # degenerate geometry
        # (candidates often build the same body again, e.g. other planes or ways round that give one result:
        # the shared volume is reused for a body with the same volume, area, extent and centroid)
        try:
            bb, c = body.bounding_box(), body.center()
            key = tuple(round(v, 6) for v in (vol, sum(f.area for f in body.faces()), bb.min.X, bb.min.Y, bb.min.Z,
                                              bb.max.X, bb.max.Y, bb.max.Z, c.X, c.Y, c.Z))
        except Exception:
            key = None
        cache = self.__dict__.setdefault("_scores", {})
        if key is not None and key in cache:
            return cache[key]
        s = 2 * self._common(body, vol) - vol
        if key is not None:
            cache[key] = s
        return s

    def _common(self, body, vol: float) -> float:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from build123d import Shape
        analytic = ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS")
        freeform = any(f.geom_type.name not in analytic for f in body.faces()) or self.freeform
        # booleans against some stored bodies fail every time (seconds each, then sampled anyway): after a few
        # failures with no success, go straight to sampling
        fails = self.__dict__.setdefault("_bool", [0, 0, 0])  # [failures, successes, slow ones (> 0.5 s)]
        try:  # a fuzzy boolean copes with the faces the two bodies share (it can hang on freeform faces)
            if freeform or (fails[0] >= 3 and fails[1] == 0) or fails[2] >= 2:
                raise RuntimeError  # ... or they are slow on this geometry: rank by sampling (the match is checked apart)
            cap = min(vol, self.volume)
            t0 = time.monotonic()
            for fuzzy in (0.0, 1e-5):  # plain first (faster); fuzzy copes with faces the two bodies share
                op = BRepAlgoAPI_Common(body.wrapped, self.shape.wrapped)
                if fuzzy:
                    op.SetFuzzyValue(fuzzy)
                op.Build()
                common = Shape.cast(op.Shape()).volume if op.IsDone() else -1.0
                if -1e-9 <= common <= cap * 1.02 + 1e-9:  # a possible value (small ones are checked below)
                    break
            if time.monotonic() - t0 > 0.5:
                fails[2] += 1
            if common >= -1e-9:
                if -1e-9 <= common <= cap * 1.02 + 1e-9 and common >= 0.5 * cap:  # (OCC volumes of freeform
                    fails[1] += 1  # faces are off by ~1%; a failed boolean can return an operand or nothing)
                    return min(common, cap)
                if -1e-9 <= common <= cap * 1.02 + 1e-9:  # small: confirm by sampling (booleans fail silently)
                    sampled = self._sampled(body, vol)
                    ok = abs(sampled - common) <= 0.1 * cap
                    fails[1 if ok else 0] += 1
                    return min(common, cap) if ok else sampled
            fails[0] += 1
        except Exception:
            if not freeform:
                fails[0] += 1
        return self._sampled(body, vol)

    def _sampled(self, body, vol: float) -> float:
        """Common volume estimated from points sampled in the body (inside both triangle meshes)."""
        import numpy as np
        bb = body.bounding_box()
        span = [bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z]
        if not all(math.isfinite(v) and 0 <= v < 1e7 for v in span):
            return 0.0  # degenerate candidate geometry (unbounded): shares nothing
        rng = np.random.default_rng(0)
        n = 4000 if self.__dict__.get("_bool", [0, 0, 0])[2] >= 2 else 1200  # the ranking itself: more points
        pts = rng.uniform([bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z], (n, 3))
        mine = _triangles(body)
        if mine is None or self._tris() is None:
            return 0.0
        ins = pts[_inside_mesh(mine, pts)]
        if not len(ins):
            return 0.0
        return vol * float(_inside_mesh(self._tris(), ins).mean())

    def _tris(self):
        if not hasattr(self, "_mesh"):
            self._mesh = _triangles(self.shape)
        return self._mesh

    def on_boundary(self, pnt) -> bool:
        from build123d import Vertex
        if not self.area:  # (no stored body)
            return False
        return self.skin.distance_to(Vertex(pnt.X, pnt.Y, pnt.Z)) < TOL

    def deviation(self, body) -> float:
        """Largest distance between sample points (vertices, points along edges) of either body and the other
        body's boundary. Face centres are not used: a face's centroid can lie in a hole or outside a concave face."""
        from build123d import Compound, Vertex

        def samples(shape):
            return [e.position_at(t) for e in shape.edges() for t in (0.0, 0.25, 0.5, 0.75)]
        skin = Compound(body.faces())
        d1 = max((self.skin.distance_to(Vertex(p.X, p.Y, p.Z)) for p in samples(body)), default=0.0)
        d2 = max((skin.distance_to(Vertex(p.X, p.Y, p.Z)) for p in samples(self.shape)), default=0.0)
        return max(d1, d2)


def _try(doc: PartDocument, oracle: _Oracle, ftype: str, name: str, variants: list[dict]) -> dict:
    """Add the feature with each parameter variant and keep the one whose body agrees best with the stored body."""
    return _try_kinds(doc, oracle, [ftype], name, variants)


def _try_pattern(doc: PartDocument, oracle: "_Oracle", ftype: str, name: str, variants: list[dict]) -> dict:
    """A pattern's variants ranked by its copies alone: an added copy should lie inside the stored body, a cut
    one outside it. Each copy is a small check; building the patterned body for every variant is not (24 rib
    copies fused into a body took 15-90 s per variant). The best variant is then built."""
    best, best_fit = None, -math.inf
    for params in variants:
        _check_time()
        ctx = copy.copy(doc.ctx)
        for fld in ("tools", "info"):
            setattr(ctx, fld, dict(getattr(ctx, fld)))
        ctx.info["_tools_only"] = True
        try:
            from openswx.core.features import BUILDERS
            BUILDERS[ftype](ctx, params, name)
        except Exception:
            continue
        seeds = len(doc.ctx.tools.get(params["features"][0], [])) if params.get("features") else 0
        copies = ctx.tools.get(name, [])[seeds:]
        inside = total = 0.0
        for op, tool in copies:
            v = tool.volume
            if v <= 0:
                continue
            c = oracle._common(tool, v)
            inside += c if op == "add" else v - c
            total += v
        fit = inside / total if total > 0 else -math.inf
        _log(f"{name}: pattern variant fits {fit:.4f} ({len(copies)} copies)")
        if fit > best_fit + 1e-9:
            best, best_fit = params, fit
    if best is None:
        raise RebuildError(f"{name}: no variant builds")
    return doc.add_feature(ftype, best, name=name)


def _volume(doc: PartDocument) -> float:
    try:
        return doc.body.volume if doc.body is not None else 0.0
    except Exception:
        return 0.0


def _exact(oracle: "_Oracle", body, s0: float, v0: float, s: float, v: float) -> bool:
    """Did adding a feature change the body only where it agrees with the stored body? A matching boss adds
    material the stored body has and a matching cut removes material it lacks: the score gains |change|."""
    if not math.isfinite(s0):  # no body yet: an empty part scores 0
        s0 = 0.0
    dv = abs(v - v0)
    if not (oracle.shape.solids() and dv > 1e-9 and s - s0 >= dv * (1 - 1e-3) - 1e-6):
        return False
    # consistent with the stored body; complete too (a shorter boss is consistent) only if the faces SOLIDWORKS
    # tagged with this feature are all there
    return bool(_TAGGED[0]) and body is not None and _faces_on_body(body, _TAGGED[0], share=0.98)


def _tagged_ok(oracle: "_Oracle", body, s0: float, v0: float, s: float, v: float) -> bool:
    """Does a candidate have the faces SOLIDWORKS tagged with the feature (ranked before its score)? A cut must
    also remove only material the stored body lacks: a cut that goes too deep keeps its tagged walls (they
    run on past them). An addition need not be all there (later cuts carve it)."""
    if not (_TAGGED[0] and body is not None and body.solids()):
        return False
    if v < v0 - 1e-9:
        s0 = s0 if math.isfinite(s0) else 0.0
        if s - s0 < (v0 - v) * (1 - 1e-3) - 1e-6:
            return False
    return _faces_on_body(body, _TAGGED[0], share=0.98)


def _trial(doc: PartDocument, ftype: str, params: dict, name: str):
    """The build state after adding one feature to the end of the part, without touching the document."""
    from openswx.core.features import BUILDERS
    ctx = copy.copy(doc.ctx)
    for fld in ("surfaces", "planes", "sketches", "tools", "info", "axes", "points", "csys", "curves"):
        setattr(ctx, fld, dict(getattr(ctx, fld)))
    if ctx.sheet is not None:
        ctx.sheet = dict(ctx.sheet)
    BUILDERS[ftype](ctx, copy.deepcopy(params), name)
    return ctx


def _body_volume(body) -> float:
    try:
        return body.volume if body is not None else 0.0
    except Exception:
        return 0.0


def _try_kinds(doc: PartDocument, oracle: _Oracle, ftypes: list[str], name: str, variants: list[dict]) -> dict:
    # ranked by (has the faces SOLIDWORKS tagged with this feature, agreement with the stored body): later
    # features can add material where a wrong, larger candidate overlaps the stored body more than the right one
    best, best_score = None, (False, -math.inf)
    s0, v0 = oracle.score_doc(doc), _volume(doc)
    for ftype, params in itertools.product(ftypes, variants):
        _check_time()
        try:  # on a copy of the part's build state: no full regeneration per candidate
            ctx = _trial(doc, ftype, params, name)
        except Exception:
            continue
        s = oracle.score_ctx(ctx)
        exact = _exact(oracle, ctx.body, s0, v0, s, _body_volume(ctx.body))
        tagged = exact or _tagged_ok(oracle, ctx.body, s0, v0, s, _body_volume(ctx.body))
        if (tagged, s) > (best_score[0], best_score[1] + 1e-6):
            best, best_score = (ftype, params), (tagged, s)
        if exact:  # no need to look further
            break
    if best is None:
        raise RebuildError(f"{name}: no variant builds")
    return doc.add_feature(best[0], best[1], name=name)


def _orient_arcs(sk: dict, oracle: "_Oracle") -> None:
    """Pick each arc's sweep (start -> end counter-clockwise, or the complement) whose midpoint lies on the
    stored body's boundary; arcs where neither or both do keep the stream order (settled later)."""
    import numpy as np
    from build123d import Vector
    R, t = sk["R"], sk["t"]

    def mid(c, s, e):
        a0 = math.atan2(s[1] - c[1], s[0] - c[0])
        a1 = math.atan2(e[1] - c[1], e[0] - c[0])
        a = a0 + ((a1 - a0) % (2 * math.pi)) / 2
        r = math.dist(c, s)
        p = R @ np.array([c[0] + r * math.cos(a), c[1] + r * math.sin(a), 0.0]) + t
        return Vector(*map(float, p))

    out = []
    for c, s, e in sk["arcs"]:
        if s != e:
            P = sk["points"]
            here, there = oracle.on_boundary(mid(P[c], P[s], P[e])), oracle.on_boundary(mid(P[c], P[e], P[s]))
            if here and there:  # (both on a face lying in the sketch plane: the side the feature swept stays on
                # the boundary a little way off the plane)
                n = Vector(*map(float, R @ np.array([0.0, 0.0, 1.0])))

                def swept(q):
                    return any(oracle.on_boundary(q + n * d) for d in (0.05, -0.05))
                here, there = swept(mid(P[c], P[s], P[e])), swept(mid(P[c], P[e], P[s]))
            if there and not here:
                s, e = e, s
        out.append((c, s, e))
    sk["arcs"] = out


def _reach(oracle: "_Oracle", sk: dict) -> list[float]:
    """Blind depths that take a sketch's extrusion as far as the stored body goes along the sketch normal,
    either way (for end conditions whose target face is not decoded)."""
    if _MOVED_LATER[0]:
        return []
    import numpy as np
    n, o = sk["R"][:, 2], sk["t"]
    ds = [float(np.dot(np.array([v.X, v.Y, v.Z]) - o, n)) for v in oracle.shape.vertices()]
    return sorted({round(abs(d), 9) for d in (max(ds), min(ds)) if abs(d) > 1e-6})


def _loose_axes(sk: dict) -> list[dict]:
    """Axes through pairs of the sketch's points that no decoded entity uses (a centerline whose segment
    references were not decoded), in model coordinates."""
    import numpy as np
    used = {i for a, b in sk["lines"] for i in (a, b)} | {i for arc in sk["arcs"] for i in arc}
    used |= {i for sp in sk.get("splines", []) for i in sp["pts"]}
    loose = [i for i in sk["points"] if i not in used][:8]
    out = []
    for a, b in itertools.combinations(loose, 2):
        pa, pb = (sk["R"] @ np.array([*sk["points"][i], 0.0]) + sk["t"] for i in (a, b))
        if np.linalg.norm(pb - pa) > 1e-6:
            out.append({"origin": [float(v) for v in pa], "direction": [float(v) for v in (pb - pa) / np.linalg.norm(pb - pa)]})
    return out


def _revolve_angles(angles: list[float], lengths: list[float]) -> list[float]:
    """Candidate revolve angles (degrees). An untagged angle dimension can be taken for a length by its name,
    so without an angle a 'length' up to 2 pi (read in mm, i.e. radians * 1000) is offered as radians too."""
    if angles:
        out = [angles[0]]
    else:
        out = [math.degrees(v / 1000.0) for v in lengths[:1] if 0 < v / 1000.0 <= 2 * math.pi + 1e-9] + [360.0]
    return [360.0 if abs(g - 360.0) < 1e-6 else g for g in out]  # 2 pi comes back as 360.0000000000008


def _scale_bytes(buf: bytes, f: dict) -> float | None:
    """A uniform scale's factor as stored: three equal doubles (x, y, z factors) in the moScale_c object."""
    import struct
    for k in range(f["offset"], min(len(buf) - 24, f["offset"] + 1024)):
        x, y, z = struct.unpack_from("<3d", buf, k)
        if x == y == z and 1e-3 < x < 1e3 and x != 1.0:
            return x
    return None


def _scale_factor(buf: bytes, f: dict, doc: PartDocument, oracle: "_Oracle") -> float:
    """A uniform scale's factor: three equal doubles (x, y, z factors) in the moScale_c object, else the cube
    root of the volume ratio to the stored body."""
    import struct
    end = min(len(buf) - 24, f["offset"] + 1024)
    for k in range(f["offset"], end):
        x, y, z = struct.unpack_from("<3d", buf, k)
        if x == y == z and 1e-3 < x < 1e3 and x != 1.0:
            return x
    return (oracle.volume / max(_mesh_volume(doc.body), 1e-12)) ** (1 / 3)


def _edges_sketch(edges, R, t, closing: list | None = None) -> dict | None:
    """Section edges as an openswx sketch (lines, circles, arcs and exact (rational) B-splines) in the frame
    (R, t); `closing`: extra segments [(p, q)] in sketch coordinates. None when a curve cannot be converted."""
    import numpy as np
    from build123d import Shape, Vector
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.Geom import Geom_BSplineCurve
    pts, ents, ids = {}, {}, {}

    def loc(v):
        q = R.T @ (np.array([v.X, v.Y, v.Z]) - t)
        return [float(q[0]), float(q[1])]

    def pid(xy):
        key = (round(xy[0], 5), round(xy[1], 5))
        if key not in ids:
            ids[key] = f"P{len(ids) + 1}"
            pts[ids[key]] = list(xy)
        return ids[key]
    for k, e in enumerate(edges, 1):
        kind = e.geom_type.name
        if kind == "LINE":
            ents[f"Line{k}"] = {"type": "line", "p1": pid(loc(e.position_at(0))), "p2": pid(loc(e.position_at(1))),
                                "construction": False}
        elif kind == "CIRCLE":
            c = loc(e.arc_center)
            if e.is_closed:
                ents[f"Circle{k}"] = {"type": "circle", "c": pid(c), "r": float(e.radius), "construction": False}
            else:
                a, m, b = loc(e.position_at(0)), loc(e.position_at(0.5)), loc(e.position_at(1))
                ccw = (a[0] - c[0]) * (m[1] - c[1]) - (a[1] - c[1]) * (m[0] - c[0]) > 0
                s_, e_ = (a, b) if ccw else (b, a)
                ents[f"Arc{k}"] = {"type": "arc", "c": pid(c), "s": pid(s_), "e": pid(e_), "construction": False}
        else:  # any other curve, exactly, as a (rational) B-spline
            nurbs = Shape.cast(BRepBuilderAPI_NurbsConvert(e.wrapped, True).Shape()).edges()[0]
            u0, u1 = BRep_Tool.Range_s(nurbs.wrapped)
            curve = BRep_Tool.Curve_s(nurbs.wrapped, u0, u1)
            bs = curve.Copy() if isinstance(curve, Geom_BSplineCurve) else None  # (OCP returns the concrete type)
            if not isinstance(bs, Geom_BSplineCurve):
                return None
            if not bs.IsPeriodic():
                bs.Segment(u0, u1)
            poles = [loc(Vector(bs.Pole(i).X(), bs.Pole(i).Y(), bs.Pole(i).Z())) for i in range(1, bs.NbPoles() + 1)]
            knots = [bs.Knot(i) for i in range(1, bs.NbKnots() + 1) for _ in range(bs.Multiplicity(i))]
            weights = [bs.Weight(i) for i in range(1, bs.NbPoles() + 1)] if bs.IsRational() else None
            ends = [pid(loc(nurbs.position_at(0))), pid(loc(nurbs.position_at(1)))]
            ents[f"Spline{k}"] = {"type": "spline", "pts": ends, "closed": bool(e.is_closed), "construction": False,
                                  "bspline": {"degree": bs.Degree(), "knots": knots, "poles": poles,
                                              **({"weights": weights} if weights else {}),
                                              **({"periodic": True} if bs.IsPeriodic() else {})}}
    for k, (a, b) in enumerate(closing or [], 1):
        ents[f"Axis{k}"] = {"type": "line", "p1": pid(a), "p2": pid(b), "construction": False}
    return {"points": pts, "entities": ents, "relations": [], "dimensions": [], "counter": len(ents) + len(pts),
            "status": {}}


def _section_sketches(oracle: "_Oracle", sk: dict) -> list[dict]:
    """Sketches recovered from the stored body when a sketch's segments are not decoded: its section just
    above and just below the sketch plane, as lines, circles, arcs and (rational) B-splines in sketch
    coordinates. For an extrusion this is its profile (plus any later cuts through it). For a revolve
    (_REVOLVE_AXES set: standard axes through the origin) also the half-sections: the body cut by the half of
    the sketch plane on either side of an axis lying in it, closed along the axis - a revolved profile."""
    import numpy as np
    from build123d import Shape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    R, t = sk["R"], sk["t"]
    n = R[:, 2]
    out = []
    for d in (1e-3, -1e-3):
        o = t + n * d
        try:
            op = BRepAlgoAPI_Section(oracle.shape.wrapped, gp_Pln(gp_Pnt(*map(float, o)), gp_Dir(*map(float, n))), False)
            op.Approximation(True)  # (else freeform sections come as 1000-point polylines: slow, inexact booleans)
            op.Build()
            edges = Shape.cast(op.Shape()).edges() if op.IsDone() else []
        except Exception:
            edges = []
        if edges:
            got = _edges_sketch(edges, R, t)
            if got:
                out.append(got)
    if _REVOLVE_AXES[0] and abs(float(n @ t)) < 1e-6:  # (the plane holds the origin)
        out += _half_sections(oracle, R, t, _REVOLVE_AXES[0])
    return out


def _half_sections(oracle: "_Oracle", R, t, axes: list) -> list[dict]:
    """Per axis lying in the sketch plane and per side of it: the body's section by a half-plane bounded by the
    axis, closed along the axis. The half-plane is turned about the axis to where the section is largest
    (a revolved body is the same in every one, unless a later cut - a cross recess, a slot - runs through
    it) and the section turned back into the sketch plane."""
    import numpy as np
    from build123d import Axis, Face, Shape, Wire
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    n = R[:, 2]
    bb = oracle.shape.bounding_box()
    L = 2.0 * float(bb.diagonal) + 1.0
    out = []

    def half(a, b, side):
        corners = [-a * L, a * L, a * L + side * b * L, -a * L + side * b * L]
        try:
            face = Face(Wire.make_polygon([tuple(map(float, c)) for c in corners], close=True))
            op = BRepAlgoAPI_Section(oracle.shape.wrapped, face.wrapped, False)
            op.Approximation(True)
            op.Build()
            edges = Shape.cast(op.Shape()).edges() if op.IsDone() else []
        except Exception:
            return [], []
        on_axis = lambda e: all(np.linalg.norm(np.cross(np.array(tuple(e.position_at(u))), a)) < 1e-6  # noqa: E731
                                for u in (0.0, 0.5, 1.0))
        edges = [e for e in edges if not on_axis(e)]  # (where the body meets the axis: the closing line below)
        on = sorted({round(float(np.array(tuple(e.position_at(u))) @ a), 9) for e in edges for u in (0.0, 1.0)
                     if np.linalg.norm(np.cross(np.array(tuple(e.position_at(u))), a)) < 1e-6})
        spans = list(zip(on[0::2], on[1::2])) if len(on) >= 2 else []
        return edges, spans

    def area(edges, spans, a):
        from build123d import Edge
        try:
            ws = Wire.combine(list(edges) + [Edge.make_line(tuple(a * lo), tuple(a * hi)) for lo, hi in spans])
            return sum(Face(w).area for w in ws if w.is_closed)
        except Exception:
            return 0.0
    for a in axes:
        a = np.asarray(a, float)
        if abs(float(a @ n)) > 1e-9:
            continue  # not in the sketch plane
        b0 = np.cross(n, a)
        for side in (1.0, -1.0):
            best = None
            for deg in (0.0, 15.0, 30.0, 45.0, 60.0, 75.0):
                th = math.radians(deg) * side
                b = b0 * math.cos(th) + np.cross(a, b0) * math.sin(th)  # b0 turned about a
                edges, spans = half(a, b, side)
                if not edges:
                    continue
                ar = area(edges, spans, a)
                if best is None or ar > best[0] + 1e-9:
                    best = (ar, deg, edges, spans)
            if best is None:
                continue
            ar, deg, edges, spans = best
            if deg:  # back into the sketch plane
                edges = [e.rotate(Axis((0, 0, 0), tuple(map(float, a))), -deg * side) for e in edges]
            closing = [([float(v) for v in (R.T @ (a * lo - t))[:2]], [float(v) for v in (R.T @ (a * hi - t))[:2]])
                       for lo, hi in spans]
            got = _edges_sketch(edges, R, t, closing)
            if got:
                out.append(got)
    return out


def _consume(doc: PartDocument, oracle: "_Oracle", sketch: str, sk: dict, feature: str, build) -> None:
    """Build `feature` (`build` adds it) from `sketch`. The sketch's plane is its decoded frame unless that was
    not found (then its candidate planes are tried too); when the decoded segments do not build, the profile
    is taken from a section of the stored body instead. The combination that agrees best is kept."""
    frames = [(sk["R"], sk["t"])] + list(sk.get("frames", []))
    original = copy.deepcopy(doc.find(sketch)["params"]["sketch"])
    best, choice, done, fit = -math.inf, None, False, 0.0  # fit: the best candidate's gain / |volume change|
    best_tagged = False  # the best has the faces SOLIDWORKS tagged with the feature (ranks before the score)
    s0, v0 = oracle.score_doc(doc), _volume(doc)
    base = s0 if math.isfinite(s0) else 0.0
    room = max(oracle.volume - (base + v0) / 2, 1e-9)  # stored material the part does not have yet
    cover, covering = 0.0, None  # (retry) the decoded candidate adding most of that material

    def attempt(plane, data, alt=False):  # alt: another reading of the sketch, kept only when it matches exactly
        nonlocal best, choice, done, fit, cover, covering, best_tagged
        _check_time()
        try:
            doc.edit_feature(sketch, {"plane": plane, "sketch": data})
            build()
        except (DocumentError, RebuildError):
            return
        s, v = oracle.score_doc(doc), _volume(doc)
        done = _exact(oracle, doc.body, s0, v0, s, v)
        tagged = done or _tagged_ok(oracle, doc.body, s0, v0, s, v)
        doc.delete_feature(feature, with_children=False)
        _log(f"{feature}: candidate {'alt' if alt else 'decoded' if data is original else 'section'} scores {s:.6g} (volume {v:.6g}, from {v0:.6g}){' exact' if done else ''}")
        if (tagged, s) > (best_tagged, best + 1e-6) and (not alt or done or choice is None):
            best, choice, best_tagged = s, (plane, data), tagged
            fit = (s - base) / abs(v - v0) if abs(v - v0) > 1e-9 else 0.0
        added = ((s + v) - (base + v0)) / 2 / room
        if v > v0 and added > cover and data is not None and not decoded_done:
            cover, covering = added, (plane, data)
    decoded_done = False
    for R, t in frames:
        attempt(_plane(R, t), original)
        if done:
            break
    # other readings of the sketch: on the plane that fits best so far (each reading costs a full set of trials)
    planes = [fr for fr in frames if choice is not None and _plane(*fr) == choice[0]] or frames[:1]
    chords = _chords(sk)
    if not done and chords:  # lines across the profile not flagged as construction (a centre rectangle's diagonals)
        bare = _sketch_dict({**sk, "construction": set(sk.get("construction", ())) | chords})[0]
        for R, t in planes:
            attempt(_plane(R, t), bare, alt=True)
            if done:
                break
    for arcs in _closing_arcs(sk):  # an open chain whose closing arc was not decoded (a sector)
        if done:
            break
        closed = _sketch_dict({**sk, "arcs": arcs})[0]
        for R, t in planes:
            attempt(_plane(R, t), closed, alt=True)
            if done:
                break
    if not done and _TRUST[0] and sk.get("gap_lines"):  # the segments the point references miss, from their records
        filled = _sketch_dict({**sk, "lines": list(sk["lines"]) + list(sk["gap_lines"])})[0]
        for R, t in frames:
            attempt(_plane(R, t), filled)
            if done:
                break
    decoded_done = True
    if not done and _TRUST[0] and cover >= 0.9 and fit < 0.9:  # (retry) a decoded profile holding nearly all the
        # stored material, which later features carve down (one that already fits well is checked against sections)
        choice, done = covering, True  # left: later features (a shell, cuts) take the rest away
    # segments not decoded (or not closed), or no plane fits well: the profile from the stored body. (A decoded
    # profile that fits well is kept even when later features change the body: the section would absorb them.)
    if not done and fit < (0.999 if _TRUST[0] else 0.9) and not (_TRUST[0] and choice is not None and best > base
                                                                  and fit < 0.9):
        # (on the retry, a decoded profile that fits well but not exactly, e.g. a slot read as its construction
        # geometry, gives way to a section that matches exactly; on the first pass the section could absorb later
        # features, such as a fillet's rounds)
        loose = fit >= 0.9
        if choice is not None:  # the best plane so far first
            frames.sort(key=lambda fr: _plane(*fr) != choice[0])
        for R, t in frames[:1] if loose else frames:
            for data in _section_sketches(oracle, {**sk, "R": R, "t": t}):
                attempt(_plane(R, t), data, alt=loose)
                if done:
                    break
            if done:
                break
    if choice is None:
        raise RebuildError(f"{feature}: no variant builds")
    n0 = len(doc.body.solids()) if doc.body is not None else 0
    doc.edit_feature(sketch, {"plane": choice[0], "sketch": choice[1]})
    build()
    n1 = len(doc.body.solids()) if doc.body is not None else 0
    if n0 and n1 > n0 and abs(_volume(doc) - v0) <= 1e-6 * max(1.0, v0):  # it only splits off an empty sliver:
        for nm in (feature, f"{feature} (cut)"):  # not what SOLIDWORKS made (the import path finds it absent)
            if any(x["name"] == nm for x in doc.features):
                doc.delete_feature(nm, with_children=False)
        raise RebuildError(f"{feature}: it changes nothing but splits the body")


def _draft_variants(doc: PartDocument, oracle: "_Oracle", angles: list) -> list:
    """Draft (neutral plane): the faces to tilt are the body's faces that are off the stored boundary (drafted
    faces keep the tag of the feature that made them); the neutral plane is not decoded, so the body's planar
    faces square to those faces are tried (larger first), each way round."""
    from build123d import GeomType
    from openswx.core.geom import face_ref
    if doc.body is None or not angles:
        raise RebuildError("nothing to draft")
    pick = [fc for fc in doc.body.faces() if not oracle.on_boundary(fc.center())]
    if not pick:
        raise RebuildError("the drafted faces are not found")
    normals = [p.normal_at() for p in pick if p.geom_type == GeomType.PLANE]
    neutral = [fc for fc in doc.body.faces() if fc.geom_type == GeomType.PLANE and not any(fc.is_same(p) for p in pick)
               and all(abs(fc.normal_at().dot(n)) < 1e-3 for n in normals)]
    neutral.sort(key=lambda fc: -fc.area)
    faces = [face_ref(p) for p in pick]
    out = []
    for fc in neutral[:6]:
        c, n = fc.center(), fc.normal_at()
        x = (fc.edges()[0].position_at(1) - fc.edges()[0].position_at(0)).normalized()
        plane = {"origin": [c.X, c.Y, c.Z], "normal": [n.X, n.Y, n.Z], "x_dir": [x.X, x.Y, x.Z]}
        out += [{"neutral": plane, "faces": faces, "angle": angles[0], "reverse": r} for r in (False, True)]
    if not out:
        raise RebuildError("no neutral plane for the draft")
    return out


def _path_lines(doc: PartDocument, path: str, psk: dict) -> list:
    """A 2D path sketch's lines as (entity id, start, end) in model coordinates."""
    import numpy as np
    R, t0 = psk["R"], psk["t"]
    data = copy.deepcopy(doc.find(path)["params"]["sketch"])
    pts = data["points"]
    # path lines: the decoded lines not flagged as construction in their records (a lone line, which the
    # sketch otherwise takes for a centerline, is a path here)
    key = lambda xy: (round(xy[0], 6), round(xy[1], 6))  # noqa: E731
    path_lines = {frozenset((key(psk["points"][a]), key(psk["points"][b]))) for a, b in psk["lines"]
                  if frozenset((a, b)) not in psk.get("construction", set())}
    lines = []
    for eid, e in data["entities"].items():
        if e["type"] != "line" or frozenset((key(pts[e["p1"]]), key(pts[e["p2"]]))) not in path_lines:
            continue
        e["construction"] = False
        a3 = R @ np.array([*pts[e["p1"]], 0.0]) + t0
        b3 = R @ np.array([*pts[e["p2"]], 0.0]) + t0
        if np.linalg.norm(b3 - a3) > 1e-6:
            lines.append((eid, a3, b3))
    doc.edit_feature(path, {"sketch": data})
    return lines


def _weld_member(doc: PartDocument, oracle: "_Oracle", name: str, path: str, psk: dict, taken: set) -> None:
    """Weldment structural member: which path lines it uses, its profile and corner treatment are not decoded.
    The profile is the stored body's section across the middle of a path line (in the member's own frame:
    x along the path sketch's normal, z along the line); a line belongs to the member when the member along
    it alone adds only material the stored body has; then mitred and plain corners are tried."""
    import numpy as np
    if psk.get("is3d"):  # a 3D sketch: its lines are the openswx 3D sketch's edges, in order
        R = np.eye(3)
        lines = [(f"Edge{i + 1}", np.array(psk["points"][a]), np.array(psk["points"][b]))
                 for i, (a, b) in enumerate(psk["lines"])]
    else:
        R, lines = psk["R"], _path_lines(doc, path, psk)
    arcs_made = 0 if psk.get("is3d") else _weld_arcs(doc, oracle, name, psk, taken)
    if not lines:
        if arcs_made:
            return
        raise RebuildError(f"{name}: no path lines")
    prof = f"{name}-Profile"
    used, profile_made = [], False
    for eid, a3, b3 in lines:
        if eid in taken:  # (a member before this one runs along it)
            continue
        _check_time()
        d = (b3 - a3) / np.linalg.norm(b3 - a3)
        x = R[:, 2] if abs(float(d @ R[:, 2])) < 0.99 else R[:, 0]
        Rm = np.column_stack([x, np.cross(d, x), d])
        if not profile_made:
            secs = []  # (sections at a few places along the line: one can run along a member crossing it there)
            for u in (0.5, 0.3, 0.7, 0.15, 0.85):
                for x in _section_sketches(oracle, {"R": Rm, "t": a3 + u * (b3 - a3)}):
                    x = _around_origin(x)
                    if x["entities"]:
                        xs = [q[0] for q in x["points"].values()] + [q[1] for q in x["points"].values()]
                        secs.append((max(xs) - min(xs), x))
            if not secs:
                continue
            doc.add_feature("sketch", {"plane": _plane(Rm, a3), "sketch": min(secs, key=lambda q: q[0])[1]}, name=prof)
            profile_made = True
        s0, v0 = oracle.score_doc(doc), _volume(doc)
        s0 = s0 if math.isfinite(s0) else 0.0
        try:
            doc.add_feature("structural_member", {"sketch": path, "profile": "sketch", "profile_sketch": prof,
                                                  "segments": [eid], "corner": "none", "merge": True}, name=name)
        except DocumentError:
            continue
        gain, dv = oracle.score_doc(doc) - s0, _volume(doc) - v0
        own = sum(t_.volume for _, t_ in doc.ctx.tools.get(name, []))  # the member's own volume
        _log(f"{name}: path line {eid} gains {gain:.6g} for {dv:.6g} (member {own:.6g})")
        doc.delete_feature(name, with_children=False)
        # (and it is a member of its own, not a sliver along one already there)
        if dv > 0 and gain >= 0.9 * dv and dv >= 0.5 * own:
            used.append(eid)
    if not used:
        if profile_made:
            doc.delete_feature(prof, with_children=False)
        if arcs_made:
            return
        raise RebuildError(f"{name}: no path line gives a member of the stored body")
    _try(doc, oracle, "structural_member", name,
         [{"sketch": path, "profile": "sketch", "profile_sketch": prof, "segments": used, "corner": c, "merge": m}
          for c in ("miter", "none") for m in (False, True)])
    taken.update(used)


def _weld_arcs(doc: PartDocument, oracle: "_Oracle", name: str, psk: dict, taken: set) -> int:
    """Members along a 2D path sketch's arcs (a curved member): the stored body's section across the arc's
    middle, revolved about the arc's axis through its sweep (centred on the middle). Kept when it adds only
    material the stored body has. Returns how many were made."""
    import numpy as np
    R, t0, P = psk["R"], psk["t"], psk["points"]
    n = R[:, 2]
    made = 0
    for k, (c, s0_, e0_) in enumerate(psk.get("arcs", [])):
        # (the stream does not say reliably which way an arc runs: the complement is tried too)
        for s_, e_ in ((s0_, e0_), (e0_, s0_)) if s0_ != e0_ else ((s0_, e0_),):
            key = ("arc", c, s_, e_)
            if key in taken:
                continue
            _check_time()
            a0 = math.atan2(P[s_][1] - P[c][1], P[s_][0] - P[c][0])
            a1 = math.atan2(P[e_][1] - P[c][1], P[e_][0] - P[c][0])
            sweep = (a1 - a0) % (2 * math.pi) or 2 * math.pi
            r = math.dist(P[c], P[s_])
            am = a0 + sweep / 2
            c3 = R @ np.array([*P[c], 0.0]) + t0
            m3 = R @ np.array([P[c][0] + r * math.cos(am), P[c][1] + r * math.sin(am), 0.0]) + t0
            u = (m3 - c3) / np.linalg.norm(m3 - c3)
            Rm = np.column_stack([u, n, np.cross(u, n)])  # the section plane holds the arc's axis
            secs = [x for x in (_around_origin(x) for x in _section_sketches(oracle, {"R": Rm, "t": m3})) if x["entities"]]
            if not secs:
                continue
            nm = f"{name}-Arc{k + 1}"
            prof = f"{nm}-Profile"
            doc.add_feature("sketch", {"plane": _plane(Rm, m3), "sketch": secs[0]}, name=prof)
            s0, v0 = oracle.score_doc(doc), _volume(doc)
            s0 = s0 if math.isfinite(s0) else 0.0
            try:
                _try(doc, oracle, "revolve", nm, [{"sketch": prof, "axis": {"origin": [float(v) for v in c3],
                                                                             "direction": [float(v) for v in n]},
                                                   "angle": math.degrees(sweep), "type": "MidPlane", "merge": m}
                                                  for m in (False, True)])
            except RebuildError:
                doc.delete_feature(prof, with_children=False)
                continue
            gain, dv = oracle.score_doc(doc) - s0, _volume(doc) - v0
            _log(f"{name}: path arc {k + 1} gains {gain:.6g} for {dv:.6g}")
            if dv > 1e-9 and gain >= 0.9 * dv:
                taken.add(key)
                made += 1
                break
            for x in (nm, prof):
                doc.delete_feature(x, with_children=False)
    return made


def _around_origin(data: dict) -> dict:
    """The loops of a section sketch whose extent holds the origin (a member's section, not the members
    beside it that the same plane cuts)."""
    pts, ents = data["points"], data["entities"]
    parent = {k: k for k in ents}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    by_point: dict = {}
    for k, e in ents.items():
        for key in ("p1", "p2", "c", "s", "e"):
            if key in e and (e["type"] != "arc" or key != "c"):
                by_point.setdefault(e[key], []).append(k)
    for ks in by_point.values():
        for k in ks[1:]:
            parent[root(k)] = root(ks[0])
    groups: dict = {}
    for k in ents:
        groups.setdefault(root(k), []).append(k)

    def extent(ks):
        xs, ys = [], []
        for k in ks:
            e = ents[k]
            if e["type"] == "circle":
                c, r = pts[e["c"]], e["r"]
                xs += [c[0] - r, c[0] + r]
                ys += [c[1] - r, c[1] + r]
            for key in ("p1", "p2", "s", "e"):
                if key in e:
                    xs.append(pts[e[key]][0])
                    ys.append(pts[e[key]][1])
            for q in e.get("pts", []):
                xs.append(pts[q][0])
                ys.append(pts[q][1])
            for q in e.get("bspline", {}).get("poles", []):  # (a closed spline's one point says nothing)
                xs.append(q[0])
                ys.append(q[1])
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None
    keep = []
    for ks in groups.values():
        b = extent(ks)
        if b and b[0] - 1e-6 <= 0 <= b[2] + 1e-6 and b[1] - 1e-6 <= 0 <= b[3] + 1e-6:
            keep += ks
    return {**data, "entities": {k: ents[k] for k in ents if k in keep}}


def _fillet_radii(faces: list) -> list[float]:
    """Radii of the round faces a fillet made (cylinders; tori: the tube radius), most common first."""
    from collections import Counter
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
    radii: Counter = Counter()
    for fc in faces:
        srf = BRepAdaptor_Surface(fc.wrapped)
        if srf.GetType() == GeomAbs_Cylinder:
            radii[round(srf.Cylinder().Radius(), 6)] += 1
        elif srf.GetType() == GeomAbs_Torus:
            radii[round(srf.Torus().MinorRadius(), 6)] += 1
    return [r for r, _ in radii.most_common(3)]


def _closing_arcs(sk: dict) -> list:
    """For a sketch whose segments form one open chain: arc lists that close it with an arc between its two
    loose ends, about each point equidistant from them (a sector whose arc the stream did not give)."""
    degree: dict = {}
    for a, b in [*sk.get("lines", []), *[(s, e) for _, s, e in sk.get("arcs", []) if s != e]]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    ends = [q for q, n in degree.items() if n == 1]
    if len(ends) != 2:
        return []
    P = sk["points"]
    p, q = ends
    r = math.dist(P[p], P[q])
    if r < 1e-9:
        return []
    out = []
    for c, xy in P.items():
        if c in (p, q):
            continue
        rp, rq = math.dist(xy, P[p]), math.dist(xy, P[q])
        if rp > 1e-6 and abs(rp - rq) <= 1e-6 * max(rp, 1.0):
            out.append(list(sk.get("arcs", [])) + [(c, p, q)])
    return out[:3]


def _helix(doc: PartDocument, oracle: "_Oracle", name: str, sketch, sketches: dict, lengths: list) -> None:
    """Helix on a sketch circle: its height and pitch are the larger and the smaller length parameter; it
    starts at the circle's point. Its handedness and direction are not decoded: the variant whose curve runs
    inside the stored body (a swept spring holds its path) is kept."""
    import numpy as np
    from build123d import Axis, Edge, Plane, Vector
    sk = sketches.get(sketch) if sketch else None
    circ = [(c, s_) for c, s_, e_ in (sk or {}).get("arcs", []) if s_ == e_]
    if not circ or len(lengths) < 2:
        raise RebuildError(f"{name}: no circle or size for the helix")
    c, s_ = circ[0]
    R, t, P = sk["R"], sk["t"], sk["points"]
    o = R @ np.array([*P[c], 0.0]) + t
    q = R @ np.array([*P[s_], 0.0]) + t
    r = float(np.linalg.norm(q - o))
    h, pitch = max(lengths[:2]), min(lengths[:2])
    tris = oracle._tris()
    best, choice = -1.0, None
    for rev in (False, True):
        d = R[:, 2] * (-1 if rev else 1)
        for left in (False, True):
            w = Edge.make_helix(pitch, h, r, center=Vector(*o), normal=Vector(*d), lefthand=left)
            p0 = w.position_at(0)
            a0, a1 = np.array([p0.X, p0.Y, p0.Z]) - o, q - o
            ang = math.degrees(math.atan2(float(np.dot(np.cross(a0, a1), d)), float(np.dot(a0, a1))))
            w = w.rotate(Axis(Vector(*o), Vector(*d)), ang)
            pts = np.array([[v.X, v.Y, v.Z] for v in (w.position_at(i / 40) for i in range(41))])
            inside = float(_inside_mesh(tris, pts).mean()) if tris is not None else 0.0
            if inside > best:
                best, choice = inside, {"origin": [float(v) for v in o], "axis": {"origin": [float(v) for v in o],
                                        "direction": [float(v) for v in R[:, 2]]}, "radius": r, "pitch": pitch,
                                        "height": h, "reverse": rev, "counterclockwise": left, "start_angle": ang}
    _log(f"{name}: helix inside the stored body {best:.2f}")
    doc.add_feature("helix", choice, name=name)


def _chords(sk: dict) -> set:
    """Lines both of whose ends meet two or more other segments: chords across a profile, which SOLIDWORKS
    sketches as construction lines (a centre rectangle's diagonals) when the flag is not found."""
    degree: dict = {}
    lines = sk.get("lines", [])
    for a, b in [*lines, *[(s, e) for _, s, e in sk.get("arcs", []) if s != e]]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    return {frozenset((a, b)) for a, b in lines if a != b and degree[a] >= 3 and degree[b] >= 3}


def _faces_on_body(body, faces, tol: float = 1e-3, share: float = 0.98) -> bool:
    """Do the given stored faces lie on the body's boundary (nearly all points along each face's edges)?
    (A later feature can extend a face beyond this one, a coplanar boss keeping the earlier tag; a lower
    `share` lets such features pass, but over the sample parts it lets more wrong ones through.) Points beyond
    the body's bounding box are left out: a later mirror or pattern merges its copy into the same face (a pipe
    run mirrored to full length); each face still needs points within the box."""
    from build123d import Compound, Vertex
    skin = Compound(body.faces())
    bb = body.bounding_box()

    def near(p):
        return (bb.min.X - tol <= p.X <= bb.max.X + tol and bb.min.Y - tol <= p.Y <= bb.max.Y + tol
                and bb.min.Z - tol <= p.Z <= bb.max.Z + tol)
    for fc in faces:
        pts = [e.position_at(t) for e in fc.edges() for t in (0.0, 0.5)]
        if not pts:
            continue
        pts = [p for p in pts if near(p)]
        if not pts or sum(skin.distance_to(Vertex(p.X, p.Y, p.Z)) <= tol for p in pts) < share * len(pts):
            return False
    return True


def _tagged_faces(path: str, config, fid: int, oracle: "_Oracle", cache: dict, anywhere: bool = False) -> list:
    """Faces of the stored body that SOLIDWORKS tagged (FACE_ID_2001) as made by feature `fid` (`anywhere`:
    also faces a later feature changed, whose records name `fid` further down)."""
    from build123d import Vector
    from openswx.exchange.sldprt import face_geometry, part_face_ids
    if "entries" not in cache:
        try:
            cache["entries"] = part_face_ids(path, config)
        except Exception:
            cache["entries"] = []
    faces = oracle.shape.faces()
    out = []
    for e in cache["entries"]:
        if len(e["ids"]) < 2 or (e["ids"][1] != fid if not anywhere else fid not in e["ids"][1:]):
            continue  # the creating feature (later records name the faces it came from)
        g = face_geometry(e)
        if not g or "center" not in g:
            continue
        c = g["center"]
        best = min(faces, key=lambda fc: (fc.center() - Vector(*c)).length)
        out.append(best)
    return out


def _import_delta(doc: PartDocument, oracle: "_Oracle", path: str, config, name: str, fid: int, cache: dict,
                  whole: bool = False) -> str | None:
    """Add a feature that is not decoded as its contribution read off the stored body: the material the stored
    body has and the part lacks (or the reverse, for cuts) next to the faces tagged with the feature's id.
    "imported", "present" (nothing to add: an earlier recovered profile already holds it) or None."""
    from build123d import Vertex
    from openswx.core.features import _valid, encode_shape
    tagged = _tagged_faces(path, config, fid, oracle, cache)
    if not tagged and not whole:  # (an imported base body: all of the material the part lacks so far)
        return None
    cur = doc.body
    pieces = {"add": oracle.shape.solids() if cur is None else [], "cut": []}
    if cur is not None:
        try:
            pieces["add"] = _valid(oracle.shape - cur).solids()
            pieces["cut"] = _valid(cur - oracle.shape).solids()
        except Exception:
            return None

    def touches(sol):
        return any(sol.distance_to(Vertex(*fc.center())) < 1e-3 or sol.distance_to(fc) < 1e-3 for fc in tagged)
    from build123d import Compound
    real = {op: [s for s in pieces[op] if s.volume > 1e-9] for op in pieces}
    kept = {op: [s for s in real[op] if whole or touches(s)] for op in real}
    if cur is not None and any(kept.values()) and all(len(kept[op]) == len(real[op]) for op in real):
        # every difference is this feature's: the result is the stored body itself. It goes with the feature
        # (used while the body before it is unchanged), sparing a boolean that can take the kernel minutes
        # on pieces sharing many faces with the body
        tools = {op: (v[0] if len(v) == 1 else Compound(v)) for op, v in kept.items() if v}
        first = "add" if "add" in tools else "cut"
        params = {"brep": encode_shape(tools[first]), "op": first, "source": "stored body",
                  "result": encode_shape(oracle.shape), "base": _body_key(cur)}
        if first == "add" and "cut" in tools:
            params["cut_brep"] = encode_shape(tools["cut"])
        try:
            doc.add_feature("import", params, name=name)
        except DocumentError:
            return None
        return "imported"
    done = False
    for op in ("add", "cut"):
        keep = [s for s in pieces[op] if s.volume > 1e-9 and (whole or touches(s))]
        if not keep:
            continue
        from build123d import Compound
        shape = keep[0] if len(keep) == 1 else Compound(keep)
        nm = name if not done else f"{name} (cut)"
        try:
            doc.add_feature("import", {"brep": encode_shape(shape), "op": op, "source": "stored body"}, name=nm)
        except DocumentError:
            return "imported" if done else None
        done = True
    return "imported" if done else "present"


def _body_key(body) -> list[float]:
    from openswx.core.features import body_key
    return body_key(body)


def _lump(doc: PartDocument, oracle: "_Oracle", name: str) -> str | None:
    """The rest of the part at once: the material the part still lacks (and has too much of) as an imported
    feature named `name`, carrying the stored body as its result (so no fuse is needed now, and edits to the
    features before it still count)."""
    from build123d import Compound
    from openswx.core.features import _valid, encode_shape
    cur = doc.body
    try:
        pieces = {"add": oracle.shape.solids() if cur is None else _valid(oracle.shape - cur).solids(),
                  "cut": [] if cur is None else _valid(cur - oracle.shape).solids()}
    except Exception:
        pieces = {"add": [], "cut": []}
    tools = {op: [x for x in v if x.volume > 1e-9] for op, v in pieces.items()}
    tools = {op: (v[0] if len(v) == 1 else Compound(v)) for op, v in tools.items() if v}
    if not tools:
        return "present" if cur is not None else None
    first = "add" if "add" in tools else "cut"
    params = {"brep": encode_shape(tools[first]), "op": first, "source": "stored body", "result": encode_shape(oracle.shape)}
    if cur is not None:
        params["base"] = _body_key(cur)
    if first == "add" and "cut" in tools:
        params["cut_brep"] = encode_shape(tools["cut"])
    try:
        doc.add_feature("import", params, name=name)
    except DocumentError:
        return None
    return "imported"


def _hole_variants(tagged: list) -> list[list[dict]]:
    """openswx hole parameters read off the faces a Hole Wizard feature made, a list of variants per hole:
    coaxial cylinders (and a cone for a countersink, or a drill point) per hole; each hole is placed by a plane
    at one end of its faces, both ends offered (a drilled hole: the end away from its tip), blind or through."""
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
    groups = []  # [axis point, axis direction, [(kind, radius, half angle, t0, t1)]]
    for fc in tagged:
        srf = BRepAdaptor_Surface(fc.wrapped)
        if srf.GetType() == GeomAbs_Cylinder:
            g_ = srf.Cylinder()
            kind, r, ang = "cyl", g_.Radius(), 0.0
        elif srf.GetType() == GeomAbs_Cone:
            g_ = srf.Cone()
            kind, r, ang = "cone", g_.RefRadius(), abs(g_.SemiAngle())
        else:
            continue
        ax = g_.Axis()
        o = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        g = next((g for g in groups if abs(abs(np.dot(g[1], d)) - 1) < 1e-6
                  and np.linalg.norm(np.cross(o - g[0], g[1])) < 1e-4), None)
        if g is None:
            g = [o, d, []]
            groups.append(g)
        ts = [float(np.dot(np.array([v.X, v.Y, v.Z]) - g[0], g[1])) for v in fc.vertices()]
        if kind == "cone":  # the radius at each end of the cone face
            rs = [math.dist([v.X, v.Y, v.Z], g[0] + g[1] * np.dot(np.array([v.X, v.Y, v.Z]) - g[0], g[1]))
                  for v in fc.vertices()]
            r = max(rs) if rs else r
        g[2].append((kind, r, ang, min(ts, default=0.0), max(ts, default=0.0)))
    out = []
    for o, d, parts in groups:
        cyls = sorted({round(r, 6) for k, r, *_ in parts if k == "cyl"})
        if not cyls:
            continue
        out.append([])
        lo, hi = min(p[3] for p in parts), max(p[4] for p in parts)
        dia = 2 * cyls[0]
        # a cone as wide as the hole is its drill point (a blind hole's tip); a wider one is a countersink
        tips = [p for p in parts if p[0] == "cone" and abs(p[1] - cyls[0]) < 1e-4]
        parts = [p for p in parts if p not in tips]
        clo, chi = min(p[3] for p in parts), max(p[4] for p in parts)
        for sign, top in ((1.0, hi), (-1.0, lo)):  # the hole enters from either end
            if tips and (tips[0][3] + tips[0][4]) / 2 * sign > (clo + chi) / 2 * sign:
                continue  # (not from the tip's end)
            if tips:
                top = chi if sign > 0 else clo
            n = d * sign
            origin = o + d * top
            x = np.cross(n, [0, 0, 1]) if abs(n[2]) < 0.9 else np.cross(n, [1, 0, 0])
            plane = {"origin": [float(v) for v in origin], "normal": [float(v) for v in n],
                     "x_dir": [float(v) for v in x / np.linalg.norm(x)]}
            base = {"face": plane, "points": [[0.0, 0.0]], "diameter": dia, "depth": (chi - clo) if tips else hi - lo,
                    "type": "simple"}
            if tips:
                base["drill_point"] = True
            cone = next((p for p in parts if p[0] == "cone"), None)
            if cone is not None:
                base.update(type="countersink", csk_diameter=2 * cone[1], csk_angle=math.degrees(2 * cone[2]))
            elif len(cyls) > 1:
                big = max((p for p in parts if p[0] == "cyl"), key=lambda p: p[1])
                base.update(type="counterbore", cbore_diameter=2 * big[1], cbore_depth=big[4] - big[3])
            out[-1].append(base)
            out[-1].append({**base, "end_condition": "ThroughAll"})
    return out


def _plane(R, t) -> dict:
    """openswx plane spec of a sketch frame (model = R * sketch + t; columns: sketch x, y, normal)."""
    return {"origin": [float(v) for v in t], "normal": [float(v) for v in R[:, 2]], "x_dir": [float(v) for v in R[:, 0]]}


def _settle_arcs(doc: PartDocument, oracle: _Oracle, sketch: str) -> None:
    """The stream gives an arc's two end points but not reliably which one it starts from (counter-clockwise):
    flip each arc of `sketch` and keep the flip when the part (with the feature that consumes the sketch)
    then agrees better with the stored body."""
    feat = doc.find(sketch)
    arcs = [eid for eid, e in feat["params"]["sketch"]["entities"].items() if e["type"] == "arc"]
    if not arcs:
        return
    if _TAGGED[0] and doc.body is not None and _faces_on_body(doc.body, _TAGGED[0], share=0.98):
        return  # the feature already reproduces the faces SOLIDWORKS tagged with it
    best = oracle.score_doc(doc)
    for eid in arcs:
        data = copy.deepcopy(feat["params"]["sketch"])  # the rebuild writes solved points back into the params
        flipped = copy.deepcopy(data)
        e = flipped["entities"][eid]
        e["s"], e["e"] = e["e"], e["s"]
        try:
            doc.edit_feature(sketch, {"sketch": flipped})
        except DocumentError:
            continue
        s = oracle.score_doc(doc)
        if s > best + 1e-6:
            best = s
        else:
            try:
                doc.edit_feature(sketch, {"sketch": data})
            except DocumentError as exc:
                raise RebuildError(f"{sketch}: {exc}") from exc


def _off_boundary_edges(body, oracle: _Oracle, reach: float = math.inf) -> list:
    """Edges a fillet / chamfer of size `reach` rounded: off the stored body's boundary, but close to it (an edge
    that a later feature removed is usually farther away)."""
    from build123d import Vertex
    out = []
    for e in body.edges():
        ds = [oracle.skin.distance_to(Vertex(*e.position_at(t))) for t in (0.25, 0.5, 0.75)]
        if all(TOL <= d <= reach for d in ds):
            out.append(e)
    return out


def rebuild_part(path: str, doc: PartDocument, config: int | None = None, budget: float = 150.0) -> dict:
    """Recreate the part's features in `doc` (an empty part). Returns a report; raises RebuildError when a
    feature is not understood or the result does not match the stored body.

    A built feature whose tagged faces are not all on the body is normally replaced by its imported delta. A
    later feature can extend those faces though (a coplanar boss keeps the earlier tag), so when that leaves
    the base feature not editable, the history is rebuilt once more keeping every built feature, and the
    comparison of the finished body with the stored one decides."""
    import time
    start = time.monotonic()
    try:
        return _rebuild(path, doc, config, budget, trust_built=False)
    except RebuildError as exc:
        rest = budget - (time.monotonic() - start)
        if "differs from the stored one" in str(exc) and rest >= 5.0:
            # a trial accepted a reading that the finished body shows was wrong: rebuild once more with the base
            # feature tried and every later feature read off the stored body (what a run short of time does),
            # so the part opens exact with its base feature editable
            _log(f"retrying with the features after the base imported ({exc})")
            for x in reversed([x["name"] for x in doc.features]):
                if any(y["name"] == x for y in doc.features):
                    doc.delete_feature(x, with_children=False)
            try:
                return _rebuild(path, doc, config, rest, trust_built=False, import_after_base=True)
            except RebuildError as again:
                _log(f"retry failed too: {again}")
                raise exc from again
        if "base feature could not be rebuilt" not in str(exc) or rest < 5.0:
            raise
        _log(f"retrying, keeping built features ({exc})")
        for x in reversed([x["name"] for x in doc.features]):
            if any(y["name"] == x for y in doc.features):
                doc.delete_feature(x, with_children=False)
        try:
            return _rebuild(path, doc, config, rest, trust_built=True)
        except RebuildError as again:
            _log(f"retry failed too: {again}")
            if "base feature could not be rebuilt" in str(again):
                raise exc from again
            raise again from exc  # (the retry got past the base feature: its failure is the one to fix)


def _rebuild(path: str, doc: PartDocument, config: int | None, budget: float, trust_built: bool,
             import_after_base: bool = False) -> dict:
    from openswx.core.geom import edge_ref, face_ref
    from openswx.exchange.sldprt import read_sldprt
    from openswx.exchange.sw_sketch import read_sketches, read_sketches3d
    from openswx.exchange.sw_tree import _stream, read_tree

    import time
    # past 60 % of the budget the remaining features come in as their stored contribution, past 85 % all of
    # the material still missing comes in at once (the part still opens with its rebuilt features editable)
    _DEADLINE[0] = time.monotonic() + (0.0 if import_after_base else 0.6 * budget)
    _BASE_TRIALS[0] = False
    _LUMP[0] = time.monotonic() + 0.85 * budget
    lumped = went_late = False
    _TRUST[0] = trust_built
    tree = read_tree(path, config)
    stream = _stream(path, config)
    sketches = read_sketches(path, config)
    try:
        sketches3d = read_sketches3d(path, config)
    except Exception:
        sketches3d = {}
    try:
        shape, _ = read_sldprt(path, config)
    except ValueError as exc:  # a part of sketches and planes only has no body to compare with
        sketch_only = all(f.get("cls") in SKIP | NEUTRAL | {"moProfileFeature_c", "mo3DProfileFeature_c", None}
                          for f in tree)
        if not (sketch_only and re.fullmatch(r"no bodies found in \S+", str(exc))):
            raise
        from build123d import Compound
        shape = Compound([])
    oracle = oracle_full = _Oracle(shape)
    pre_scale: dict = {}  # later scale factor -> oracle of the stored body scaled back
    axes: dict[str, list[str]] = {}
    last_sketch = recent_sketch = None
    sketch_order, consumed = [], set()  # sketches in tree order; those a feature has used
    built, skipped, imported, face_cache = [], [], [], {}
    first_solid = None  # the feature that makes the first solid: it must be editable
    bend_done: set = set()
    weld_taken: dict = {}  # weldment path sketch -> its lines that members already run along
    for f in tree:
        name, cls = f["name"], f["cls"]
        # (a later scale, move or draft puts this feature's tagged faces elsewhere: drafted faces keep their tag)
        moved_later = any(g["cls"] in ("moScale_c", "moMoveCopyBody_c", "moDraft_c") for g in tree[tree.index(f) + 1:])
        later = tree[tree.index(f) + 1:]
        _MOVED_LATER[0] = any(g["cls"] == "moMoveCopyBody_c" for g in later)
        _LATER_SCALE[0] = math.prod(_scale_bytes(stream, g) or 1.0 for g in later if g["cls"] == "moScale_c")
        # features before a (uniform, about the origin) scale are compared with the stored body scaled back
        k = _LATER_SCALE[0]
        if abs(k - 1.0) > 1e-12:
            if k not in pre_scale:
                pre_scale[k] = _Oracle(oracle_full.shape.scale(1.0 / k))
            oracle = pre_scale[k]
        else:
            oracle = oracle_full
        _TAGGED[0] = (_tagged_faces(path, config, f["id"], oracle, face_cache)
                      if f.get("id") is not None and cls != "moProfileFeature_c" and not moved_later else None)
        lengths = [p["value"] * 1000.0 for p in f["params"] if p["kind"] == "length"]
        angles = [math.degrees(p["value"]) for p in f["params"] if p["kind"] == "angle"]
        if name in bend_done:  # (built with the base flange before it)
            continue
        if lumped:  # (its material came in with the lump)
            skipped.append(name)
            continue
        if cls == "mo3DProfileFeature_c" and sketches3d.get(name, {}).get("lines"):  # a 3D sketch of lines
            sk3 = sketches3d[name]
            P = sk3["points"]
            sk3["lines"] = [(a_, b_) for a_, b_ in sk3["lines"] if math.dist(P[a_], P[b_]) > 1e-9]
            doc.add_feature("sketch3d", {"segments": [{"points": [list(P[a_]), list(P[b_])], "kind": "line"}
                                                      for a_, b_ in sk3["lines"]]}, name=name)
            sketches[name] = {**sk3, "is3d": True}
            sketch_order.append(name)
            continue
        if cls is None or cls in SKIP or (cls == "moRefPlane_c" and name in STD_PLANES):
            # reference geometry, annotations, objects of unknown class (attributes, simulation data): sketches
            # carry their own (absolute) frames, and the final comparison catches any geometry left out
            continue
        if cls in NEUTRAL:
            skipped.append(name)
            continue
        if _PROGRESS[0]:  # (which feature is being tried: the parent learns it if the kernel crashes)
            _PROGRESS[0].send(("feature", name))
        # out of time for trials - except for the base feature: imported, it fails the whole part, so it is
        # always tried (the overall time limit still stops a rebuild that runs too long)
        late = time.monotonic() > _DEADLINE[0] and (first_solid is not None or cls in FIXED_BASE)
        _BASE_TRIALS[0] = first_solid is None and cls not in FIXED_BASE
        if (name in _AVOID[0] or late) and cls != "moProfileFeature_c" and f.get("id") is not None:
            # trying this feature crashed the geometry kernel before, or the time for trials is up: its
            # contribution read off the stored body
            if last_sketch is not None:
                consumed.add(last_sketch)
                last_sketch = None
            lumped = late and time.monotonic() > _LUMP[0] and first_solid is not None
            if late:
                _log(f"{name}: imported, out of time for trials" + (" (with the rest of the part)" if lumped else ""))
            got = (_lump(doc, oracle_full, name) if lumped else
                   _import_delta(doc, oracle, path, config, name, f["id"], face_cache, whole=cls in FIXED_BASE))
            if not got and late and first_solid is not None:  # (no faces of its own: it comes in with the rest)
                lumped = True
                got = _lump(doc, oracle_full, name)
            went_late = went_late or late
            if not got:
                raise RebuildError(f"{name}: " + ("out of time and it cannot be imported" if late else
                                                  "its trials crash the geometry kernel and it cannot be imported"))
            (imported if got == "imported" else skipped).append(name)
            built.append(name)
            if first_solid is None and doc.body is not None:
                first_solid = name
                if got == "imported" and cls not in FIXED_BASE:
                    raise RebuildError(f"{name}: the base feature could not be rebuilt as an editable feature")
            continue
        if cls == "moProfileFeature_c":
            sk = sketches.get(name)
            if sk is None:
                raise RebuildError(f"{name}: sketch not decoded")
            _orient_arcs(sk, oracle)
            data, axes[name] = _sketch_dict(sk)
            doc.add_feature("sketch", {"plane": _plane(sk["R"], sk["t"]), "sketch": data}, name=name)
            last_sketch = recent_sketch = name
            sketch_order.append(name)
        else:
            try:
                if cls in ("moExtrusion_c", "moICE_c", "moCut_c", "moBoss_c", "moExtruThin_c", "moBossThin_c", "moCutThin_c"):
                    last_sketch = last_sketch or recent_sketch  # a sketch shared with an earlier feature
                    if last_sketch is None:
                        raise RebuildError(f"{name}: no sketch to extrude")
                    # moICE_c is used for cuts and for some bosses: unless the name says, let the stored body decide
                    kinds = (["cut_extrude"] if name.startswith("Cut-") or cls in ("moCut_c", "moCutThin_c")
                             else ["extrude"] if cls in ("moExtrusion_c", "moBoss_c", "moExtruThin_c", "moBossThin_c")
                             else ["cut_extrude", "extrude"])
                    ec = f["end_condition"]
                    depth = lengths[0] if lengths else None
                    if ec in (2, 3, 4, 5, 7):  # up to next / vertex / surface / offset / body: the target is not decoded,
                        conds = [("Blind", d) for d in _reach(oracle, sketches[last_sketch])]  # so go as far as the body does
                    elif ec is None:  # end spec not found: the depth tells blind from through all; without one (the
                        # dimension can sit after a later object) try through all and blind to the stored extent
                        conds = ([("Blind", depth), ("MidPlane", depth)] if depth else [("ThroughAll", 10.0), ("ThroughAllBoth", 10.0)]
                                 + [("Blind", d) for d in _reach(oracle, sketches[last_sketch])])
                    elif ec in (0, 6):  # the depth dimension is not always the first length attached to the feature
                        cond = "Blind" if ec == 0 else "MidPlane"
                        ds = list(dict.fromkeys([d for d in lengths if d > 0] + _reach(oracle, sketches[last_sketch])))
                        if ec == 6:
                            ds = list(dict.fromkeys(ds + [2 * d for d in _reach(oracle, sketches[last_sketch])]))
                        conds = [(cond, d) for d in ds] or [(cond, 10.0)]
                    elif ec in (1, 9):
                        conds = [({1: "ThroughAll", 9: "ThroughAllBoth"}[ec], depth or 10.0)]
                    else:
                        raise RebuildError(f"{name}: end condition {ec} not supported yet")
                    draft = angles[0] if angles else 0.0
                    variants = [{"sketch": last_sketch, "depth": d, "end_condition": c, "draft": draft, "merge": True,
                                 "start_offset": 0.0, "reverse": r, "draft_outward": o}
                                for (c, d), r, o in itertools.product(conds, (False, True), (False, True)) if o is False or draft]
                    two = list(dict.fromkeys(d for d in lengths if d > 0))
                    if ec is None and len(two) >= 2 and "Thin" not in cls:  # (two directions: two end specs, two depths)
                        variants += [{"sketch": last_sketch, "depth": a, "end_condition": "Blind", "draft": 0.0, "merge": True,
                                      "start_offset": 0.0, "reverse": r, "direction2": {"depth": b}}
                                     for a, b in ((two[0], two[1]), (two[1], two[0])) for r in (False, True)]
                    if "Thin" in cls:  # thin feature: depth first, then the wall thickness
                        walls = list(dict.fromkeys(lengths[1:])) or [1.0]
                        variants = [{**v, "thin": {"thickness": t, "side": sd}} for v in variants
                                    for t in walls[:3] for sd in ("one", "other", "mid")]
                        # auto-fillet corners: a later length is the radius (tried after the sharp variants)
                        variants += [{**v, "thin": {**v["thin"], "fillet": r}} for r in walls[1:4] if r > 0
                                     for v in variants if v["thin"]["thickness"] != r]
                    if f.get("reverse") is not None:  # the decoded direction first (the other stays as a fallback)
                        variants.sort(key=lambda v: v["reverse"] != f["reverse"])
                    build = lambda: _try_kinds(doc, oracle, kinds, name, variants)  # noqa: E731
                    _consume(doc, oracle, last_sketch, sketches[last_sketch], name, build)
                    _settle_arcs(doc, oracle, last_sketch)
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls in ("moRevolution_c", "moRevCut_c"):
                    if last_sketch is None:
                        raise RebuildError(f"{name}: no sketch to revolve")
                    angs = _revolve_angles(angles, lengths)
                    cut = name.startswith("Cut-") or cls == "moRevCut_c"
                    # the sketch's lone lines (centerlines), then its other lines (a centerline sharing points with the
                    # profile), then (an axis feature or model edge) the standard axes
                    lines = [e for e, v in doc.find(last_sketch)["params"]["sketch"]["entities"].items() if v["type"] == "line"]
                    cands = (axes.get(last_sketch, []) + [ln for ln in lines if ln not in axes.get(last_sketch, [])]
                             + _loose_axes(sketches[last_sketch]) + ["X", "Y", "Z"])
                    variants = [{"sketch": last_sketch, "axis": a, "angle": g, "type": "OneDirection", "merge": True, "reverse": r}
                                for g in angs for a in cands for r in ((False,) if g == 360.0 else (False, True))]
                    build = lambda: _try(doc, oracle, "cut_revolve" if cut else "revolve", name, variants)  # noqa: E731
                    _REVOLVE_AXES[0] = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]  # (sections: half profiles too)
                    try:
                        _consume(doc, oracle, last_sketch, sketches[last_sketch], name, build)
                    finally:
                        _REVOLVE_AXES[0] = None
                    _settle_arcs(doc, oracle, last_sketch)
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls in ("Fillet_c", "moFillet_c", "moChamfer_c", "Chamfer_c"):
                    if not lengths and "Fillet" in cls and f.get("id") is not None:  # (no radius parameter in some
                        # older parts: the radius of the round faces it made, a later feature may have changed them)
                        lengths = _fillet_radii(_tagged_faces(path, config, f["id"], oracle, face_cache, anywhere=True))
                    if doc.body is None or not lengths:
                        raise RebuildError(f"{name}: nothing to round")
                    cand = _off_boundary_edges(doc.body, oracle, 1.5 * max(lengths))
                    if _TAGGED[0] and "Fillet" in cls:  # the edges next to the faces this fillet made (not a later
                        from build123d import Vertex  # fillet's: it rounds edges off the stored boundary too)
                        r_ = max(lengths)
                        mine = [e for e in cand if min(fc.distance_to(Vertex(e.position_at(0.5).X, e.position_at(0.5).Y, e.position_at(0.5).Z))
                                                       for fc in _TAGGED[0]) <= r_ * 1.01]
                        cand = mine or cand
                    edges = [edge_ref(e, doc.body) for e in cand]
                    if not edges and "Chamfer" in cls:  # a vertex chamfer: one corner cut off, its edges stay
                        corners = [v for v in doc.body.vertices() if not oracle.on_boundary(v.center())]
                        if len(corners) == 1:
                            c = corners[0].center()
                            d = (lengths + lengths[:1] * 3)[:3]
                            variants = [{"vertex": [c.X, c.Y, c.Z], "d1": a, "d2": b, "d3": e}
                                        for a, b, e in sorted(set(itertools.permutations(d)))]
                            _try(doc, oracle, "vertex_chamfer", name, variants)
                            built.append(name)
                            continue
                    if not edges:
                        raise RebuildError(f"{name}: could not find its edges")
                    if "Fillet" in cls:
                        kind, base = "fillet", {"radius": lengths[0], "faces": []}
                    else:
                        kind, base = "chamfer", {"distance": lengths[0], "type": "equal_distance", "angle": 45.0,
                                                 "distance2": lengths[0], "faces": []}
                    try:
                        try:
                            doc.add_feature(kind, {**base, "edges": edges}, name=name)
                        except DocumentError:
                            if kind != "fillet":
                                raise
                            # two fillets that meet across a face (a full round: radius = half the thickness) fail
                            # in the kernel; a radius a hair smaller leaves a sliver of the face, within tolerance
                            doc.add_feature(kind, {**base, "radius": lengths[0] * (1 - 1e-6), "edges": edges}, name=name)
                    except DocumentError:  # some edges were not this feature's: keep those that round on their own
                        ok = []
                        for e in edges:
                            try:
                                doc.add_feature(kind, {**base, "edges": [e]}, name=name)
                            except DocumentError:
                                continue
                            doc.delete_feature(name, with_children=False)
                            ok.append(e)
                        if not ok:
                            raise RebuildError(f"{name}: none of its edges can be rounded") from None
                        # (together they may not build: grow a set edge by edge, keeping each that builds and helps)
                        grown, best_s = [], oracle.score_doc(doc)
                        for e in ok:
                            _check_time()
                            try:
                                doc.add_feature(kind, {**base, "edges": grown + [e]}, name=name)
                            except DocumentError:
                                continue
                            s_ = oracle.score_doc(doc)
                            doc.delete_feature(name, with_children=False)
                            if s_ > best_s + 1e-6:
                                grown, best_s = grown + [e], s_
                        _try(doc, oracle, kind, name, ([{**base, "edges": grown}] if grown else []) + [{**base, "edges": ok}]
                             + [{**base, "edges": [e]} for e in ok][:8])
                elif cls in ("moLPattern_c", "moCirPattern_c"):
                    seeds = [b for b in built if not b.startswith(("Sketch", "3DSketch"))][-1:]
                    ints = [int(p["value"]) for p in f["params"] if p["kind"] == "integer"]
                    if not seeds or not ints:
                        raise RebuildError(f"{name}: pattern not understood")
                    dirs = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
                    if cls == "moLPattern_c":
                        base = {"features": seeds, "spacing": lengths[0] if lengths else 10.0, "count": ints[0]}
                        d2 = ({"spacing": lengths[1] if len(lengths) > 1 else 10.0, "count": ints[1]}
                              if len(ints) > 1 and ints[1] > 1 else None)
                        variants = []
                        for d, r in itertools.product(dirs, (False, True)):
                            v = {**base, "direction": d, "reverse": r}
                            if d2:
                                variants += [{**v, "direction2": {**d2, "direction": e, "reverse": r2}}
                                             for e, r2 in itertools.product(dirs, (False, True)) if e != d]
                            else:
                                variants.append(v)
                        _try_pattern(doc, oracle, "linear_pattern", name, variants)
                    else:
                        total = angles[0] if angles else 360.0
                        variants = [{"features": seeds, "axis": ax, "count": ints[0], "angle": total, "equal_spacing": True,
                                     "reverse": r} for ax, r in itertools.product(("X", "Y", "Z"), (False, True))]
                        _try_pattern(doc, oracle, "circular_pattern", name, variants)
                elif cls == "moSMBaseFlange_c":  # sheet metal from a closed sketch: a plate of the sheet thickness
                    last_sketch = last_sketch or recent_sketch
                    # (older parts: the thickness on the Sheet-Metal feature before it, the bend radius and depth on
                    # the base bend after it)
                    sheet = next((g for g in reversed(tree[:tree.index(f)]) if g["cls"] == "moSheetMetal_c"), None)
                    bend = later[0] if later and later[0]["cls"] == "moSketchBend_c" else None
                    own = lengths or [p["value"] * 1000.0 for p in (sheet or {}).get("params", []) if p["kind"] == "length"]
                    if last_sketch is None or not own:
                        raise RebuildError(f"{name}: no sketch or thickness")
                    sk_ = sketches[last_sketch]
                    if bend is not None:  # an open profile bent at its corners: a thin extrude
                        extra = [p["value"] * 1000.0 for p in (bend or {}).get("params", []) if p["kind"] == "length"]
                        t0 = own[0]
                        radii = list(dict.fromkeys([x for x in extra if 0 < x < 10 * t0][:2] + [t0]))
                        depths = list(dict.fromkeys([x for x in extra if x > 10 * t0][:2] + _reach(oracle, sk_)))
                        kinds_ = ["extrude"]
                        variants = [{"sketch": last_sketch, "depth": d, "end_condition": c, "draft": 0.0, "merge": True,
                                     "start_offset": 0.0, "reverse": r, "thin": {"thickness": t0, "side": sd, "fillet": rr}}
                                    for d in depths[:3] for c in ("Blind", "MidPlane") for r in (False, True)
                                    for sd in ("one", "other") for rr in radii]
                        if bend is not None:
                            skipped.append(bend["name"])
                            bend_done.add(bend["name"])
                        build = lambda: _try_kinds(doc, oracle, kinds_, name, variants)  # noqa: E731
                    else:
                        ts = list(dict.fromkeys([own[0]] + _reach(oracle, sk_)))  # (the plate or a block)
                        variants = [{"sketch": last_sketch, "thickness": t, "bend_radius": own[0], "reverse": r}
                                    for t in ts for r in (False, True)]
                        build = lambda: _try(doc, oracle, "base_flange", name, variants)  # noqa: E731
                    _consume(doc, oracle, last_sketch, sketches[last_sketch], name, build)
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls == "moHoleWzd_c":  # holes read off the faces it made (its own data is not decoded)
                    holes = _hole_variants(_TAGGED[0] or [])
                    if not holes:
                        raise RebuildError(f"{name}: no hole faces")
                    # one openswx hole per position (its own entry plane): its variants are its ends x blind / through
                    for i, variants in enumerate(holes):
                        _try(doc, oracle, "hole", name if i == 0 else f"{name}-{i + 1}", variants)
                elif cls == "moWeldMemberFeat_c":  # a structural member: the stored body's section across a path
                    # line (at its middle) is the profile, swept along the lines (of the path sketches not used yet)
                    # (members share path sketches: a path stays available, without the lines already used)
                    paths = [x for x in sketch_order if (x not in consumed or x in weld_taken)
                             and (sketches[x]["lines"] or sketches[x].get("arcs"))]
                    if not paths:
                        raise RebuildError(f"{name}: no path sketch")
                    made = 0
                    for k, path in enumerate(paths):
                        try:
                            _weld_member(doc, oracle, name if made == 0 else f"{name}-{made + 1}", path, sketches[path],
                                         weld_taken.setdefault(path, set()))
                            made += 1
                            consumed.add(path)
                        except RebuildError as exc:
                            _log(str(exc))
                    if not made:
                        raise RebuildError(f"{name}: no path line gives a member of the stored body")
                    last_sketch = None
                elif cls == "moScale_c":
                    if doc.body is None:
                        raise RebuildError(f"{name}: nothing to scale")
                    k = _scale_factor(_stream(path, config), f, doc, oracle)
                    c = doc.body.center()
                    variants = [{"op": "scale", "factor": k}, {"op": "scale", "factor": k, "about": [c.X, c.Y, c.Z]}]
                    _try(doc, oracle, "body_op", name, variants)
                elif cls in ("moSweep_c", "moSweepCut_c"):
                    free = [s for s in sketch_order if s not in consumed]
                    if len(free) < 2:
                        raise RebuildError(f"{name}: needs a profile and a path sketch")
                    kind = "cut_sweep" if cls == "moSweepCut_c" else "sweep"
                    # (a profile swept along a helix lies at the helix start, square to it: its plane is often not
                    # decoded, so a profile sketch with no plane of its own is put there)
                    for hx in [x for x in free[-3:] if (doc.find(x) or {}).get("type") == "helix"]:
                        info = doc.ctx.info.get(hx, {})
                        for prof in [x for x in free[-3:] if x != hx and doc.find(x)["type"] == "sketch"
                                     and not sketches.get(x, {}).get("is3d")
                                     and np.allclose(sketches[x]["R"], np.eye(3)) and not np.any(sketches[x]["t"])]:
                            if info.get("start") and info.get("start_tangent"):
                                o_, d_ = np.array(info["start"]), np.array(info["start_tangent"])
                                ax_ = np.array(doc.find(hx)["params"]["axis"]["origin"])
                                u_ = o_ - ax_ - d_ * float(np.dot(o_ - ax_, d_))
                                u_ = u_ / np.linalg.norm(u_) if np.linalg.norm(u_) > 1e-9 else np.cross(d_, [0, 0, 1])
                                doc.edit_feature(prof, {"plane": {"origin": list(o_), "normal": list(d_), "x_dir": list(u_)}})
                    variants = [{"sketch": a, "path": b, "frenet": fr, "merge": True}
                                for a, b in itertools.permutations(free[-3:], 2) for fr in (False, True)]
                    feat = _try(doc, oracle, kind, name, variants)
                    consumed.update((feat["params"]["sketch"], feat["params"]["path"]))
                    last_sketch = None
                elif cls in ("moBlend_c", "moBlendCut_c"):
                    free = [s for s in sketch_order if s not in consumed]
                    if len(free) < 2:
                        raise RebuildError(f"{name}: needs two or more profile sketches")
                    kind = "cut_loft" if cls == "moBlendCut_c" else "loft"
                    variants = [{"profiles": free[-k:], "merge": True} for k in range(len(free), 1, -1)]
                    feat = _try(doc, oracle, kind, name, variants)
                    consumed.update(feat["params"]["profiles"])
                    last_sketch = None
                elif cls == "moExtruRefSurface_c":
                    if last_sketch is None:
                        raise RebuildError(f"{name}: no sketch to extrude")
                    depths = [lengths[0]] if lengths else _reach(oracle, sketches[last_sketch])
                    _try(doc, oracle, "extruded_surface", name,
                         [{"sketch": last_sketch, "depth": d, "reverse": r} for d in depths for r in (False, True)])
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls == "moRevolvRefSurf_c":
                    if last_sketch is None:
                        raise RebuildError(f"{name}: no sketch to revolve")
                    angs = _revolve_angles(angles, lengths)
                    lines = [e for e, v in doc.find(last_sketch)["params"]["sketch"]["entities"].items() if v["type"] == "line"]
                    cands = [f"{last_sketch}/{a}" for a in (axes.get(last_sketch) or lines)] + ["X", "Y", "Z"]
                    _try(doc, oracle, "revolved_surface", name,
                         [{"sketch": last_sketch, "axis": a, "angle": g} for g in angs for a in cands])
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls == "moThicken_c":
                    surfs = [b for b in built if b in doc.ctx.surfaces]
                    if not surfs or not lengths:
                        raise RebuildError(f"{name}: no surface to thicken")
                    _try(doc, oracle, "thicken", name, [{"surface": surfs[-1], "thickness": lengths[0], "flip": fl, "merge": True}
                                                        for fl in (False, True)])
                elif cls in ("moMirrorSolid_c", "moMirrorPattern_c"):
                    if doc.body is None:
                        raise RebuildError(f"{name}: nothing to mirror")
                    planes = ["Front", "Top", "Right"] + [face_ref(fc) for fc in doc.body.faces() if fc.geom_type.name == "PLANE"]
                    seeds = [b for b in built if not b.startswith(("Sketch", "3DSketch"))][-1:]
                    p = ({"bodies": True, "merge": True} if cls == "moMirrorSolid_c" else {"features": seeds})
                    _try(doc, oracle, "mirror", name, [{**p, "plane": pl} for pl in planes])
                elif cls == "moPlanarSurface_c":
                    if last_sketch is None:
                        raise RebuildError(f"{name}: no sketch to fill")
                    if oracle.shape.solids():  # (a surface thickened later: the stored solid cannot score it)
                        doc.add_feature("planar_surface", {"sketch": last_sketch}, name=name)
                    else:
                        build = lambda: _try(doc, oracle, "planar_surface", name, [{"sketch": last_sketch}])  # noqa: E731
                        _consume(doc, oracle, last_sketch, sketches[last_sketch], name, build)
                    consumed.add(last_sketch)
                    last_sketch = None
                elif cls == "moShell_c":
                    if doc.body is None or not lengths:
                        raise RebuildError(f"{name}: nothing to shell")
                    faces = [face_ref(fc) for fc in doc.body.faces() if not oracle.on_boundary(fc.center())]
                    _try(doc, oracle, "shell", name, [{"thickness": lengths[0], "outward": o, "faces": faces} for o in (False, True)])
                elif cls == "moDeleteBody_c":  # which bodies is not decoded: those the stored part does not have
                    if doc.body is None or len(doc.body.solids()) < 2:  # (it deletes surface bodies: the solid
                        skipped.append(name)  # comparison does not see them)
                        continue
                    sols = list(doc.body.solids())
                    outside = [i for i, b_ in enumerate(sols) if oracle._common(b_, b_.volume) < 0.5 * b_.volume]
                    variants = ([{"op": "delete_body", "bodies": outside}] if outside and len(outside) < len(sols) else [])
                    variants += [{"op": "delete_body", "bodies": [i]} for i in range(len(sols)) if [i] != outside]
                    _try(doc, oracle, "body_op", name, variants)
                elif cls == "moHelix_c":  # a helix on the sketch circle before it (a sweep path)
                    _helix(doc, oracle, name, last_sketch or recent_sketch, sketches, lengths)
                    if last_sketch is not None:
                        consumed.add(last_sketch)
                        last_sketch = None
                    sketches[name] = {"points": {}, "lines": [], "arcs": [], "is3d": True}
                    sketch_order.append(name)
                elif cls == "moDraft_c":
                    # (the angle is written as a parameter of the length class: radians)
                    _try(doc, oracle, "draft", name, _draft_variants(doc, oracle, angles or [math.degrees(x / 1000.0) for x in lengths]))
                else:  # not decoded yet: its exact contribution, taken from the stored body, as an imported feature
                    got = f.get("id") is not None and _import_delta(doc, oracle, path, config, name, f["id"], face_cache,
                                                                    whole=cls in FIXED_BASE)
                    if not got:
                        raise RebuildError(f"{name}: feature type {cls or 'unknown'} is not supported yet")
                    if got != "imported":
                        skipped.append(name)
                        continue
                    imported.append(name)
            except (RebuildError, DocumentError) as exc:  # decoded but not rebuilt: its contribution, read off
                if f.get("id") is None:  # the stored body, as an imported feature (also when the time for trials ran out)
                    raise
                _log(f"{name}: {exc}")
                if any(x["name"] == name for x in doc.features):
                    doc.delete_feature(name, with_children=False)
                if last_sketch is not None:
                    consumed.add(last_sketch)
                    last_sketch = None
                got = _import_delta(doc, oracle, path, config, name, f["id"], face_cache)
                if got is None and time.monotonic() > _DEADLINE[0] and first_solid is not None:
                    # out of time and nothing of its own to import: the rest of the part comes in at once
                    lumped = went_late = True
                    got = _lump(doc, oracle_full, name)
                if got is None:
                    raise
                went_late = went_late or time.monotonic() > _DEADLINE[0]
                if got != "imported":
                    skipped.append(name)
                    continue
                imported.append(name)
            if (f.get("id") is not None and doc.body is not None and name not in imported and doc.body.solids()
                    and not moved_later and not (trust_built and first_solid is None)):  # (a later scale / move puts the
                # stored faces elsewhere; on the retry the base feature is kept, the final comparison decides)
                tagged = _tagged_faces(path, config, f["id"], oracle, face_cache)
                if tagged and not _faces_on_body(doc.body, tagged):  # built, but not what SOLIDWORKS made
                    _log(f"{name}: built, but its tagged faces are not on the body")
                    for nm in (name, f"{name} (cut)"):
                        if any(x["name"] == nm for x in doc.features):
                            doc.delete_feature(nm, with_children=False)
                    got = _import_delta(doc, oracle, path, config, name, f["id"], face_cache)
                    if got is None:
                        raise RebuildError(f"{name}: rebuilt differently from the stored body")
                    if got != "imported":
                        skipped.append(name)
                        continue
                    imported.append(name)
        built.append(name)
        if first_solid is None and doc.body is not None:
            first_solid = name
            if name in imported and cls not in FIXED_BASE:  # (an imported or derived base body is fixed in SOLIDWORKS too)
                raise RebuildError(f"{name}: the base feature could not be rebuilt as an editable feature")
    oracle = oracle_full  # the result is compared with the stored body itself
    body = doc.body
    if not shape.faces():  # no stored body (sketches only): the history must build none either
        if doc.body is not None or doc.ctx.surfaces:
            raise RebuildError("the history builds a body the stored part does not have")
        return {"features": built, "skipped": skipped, "imported": imported, "volume": 0.0, "stored_volume": 0.0,
                "area": 0.0, "stored_area": 0.0, "deviation": 0.0}
    if not shape.solids():  # surface bodies: compare the sheets (no volume)
        from build123d import Compound
        sheets = list(doc.ctx.surfaces.values())
        if not sheets:
            raise RebuildError("the history builds no surface body")
        body = Compound(sheets)
        area = sum(fc.area for fc in body.faces())
        dev = oracle.deviation(body)
        if abs(area - oracle.area) > MATCH * max(oracle.area, 1e-9) or dev > 10 * TOL:
            raise RebuildError(f"rebuilt surfaces differ from the stored ones (area {area:.4f} vs {oracle.area:.4f} mm2, "
                               f"deviation {dev:.4g} mm)")
        return {"features": built, "skipped": skipped, "imported": imported, "volume": 0.0, "stored_volume": 0.0, "area": area,
                "stored_area": oracle.area, "deviation": dev}
    if body is None:
        raise RebuildError("the history builds no body")
    from openswx.core.features import body_key
    if body_key(body)[0] <= 0:  # (the mesh volume below is unsigned: an inside-out body would pass it)
        raise RebuildError("the rebuilt body is inside out")
    if went_late and not lumped and first_solid is not None:
        # features imported for lack of time are read off one by one; whatever they missed comes in at the end
        vol, area = _mesh_volume(body), sum(fc.area for fc in body.faces())
        if abs(vol - oracle.volume) > MATCH * oracle.volume or abs(area - oracle.area) > MATCH * oracle.area:
            if _lump(doc, oracle, "Stored body (rest)") == "imported":
                imported.append("Stored body (rest)")
                built.append("Stored body (rest)")
                body = doc.body
    vol = _mesh_volume(body)
    area = sum(fc.area for fc in body.faces())
    dv = abs(vol - oracle.volume) / max(oracle.volume, 1e-9)
    da = abs(area - oracle.area) / max(oracle.area, 1e-9)
    dev = oracle.deviation(body) if dv <= 10 * MATCH and da <= 10 * MATCH else math.inf
    if not (dv <= MATCH and da <= MATCH) and dev > 10 * TOL:
        raise RebuildError(f"rebuilt body differs from the stored one (volume {vol:.4f} vs {oracle.volume:.4f} mm3, "
                           f"area {area:.4f} vs {oracle.area:.4f} mm2, deviation {dev:.4g} mm)")
    return {"features": built, "skipped": skipped, "imported": imported, "volume": vol, "stored_volume": oracle.volume, "area": area,
            "stored_area": oracle.area, "deviation": dev}


def _rebuild_worker(path: str, config: int | None, conn, avoid: frozenset = frozenset(), budget: float = 150.0) -> None:
    try:
        from pathlib import Path
        _PROGRESS[0], _AVOID[0] = conn, set(avoid)
        doc = PartDocument(Path(path).stem)
        report = rebuild_part(path, doc, config, budget)
        data = doc.to_dict()
        # the document as saved must regenerate to the same body (a sketch edited for a later feature can leave
        # an earlier one built on a state the saved history no longer gives)
        fresh = PartDocument(doc.name)
        fresh.load_dict(data)
        from openswx.core.features import body_key
        bad = [k for k, v in fresh.state.items() if v.get("status") == "error"]
        here = body_key(doc.body)[0] if doc.body is not None else 0.0
        again = body_key(fresh.body)[0] if fresh.body is not None else 0.0
        if bad or abs(here - again) > 1e-6 * max(1.0, abs(here)):
            raise RebuildError("the rebuilt history does not regenerate to the same body"
                               + (f" ({', '.join(bad[:3])})" if bad else ""))
        report["regenerated_volume"] = again
        conn.send(("ok", data, report))
    except Exception as exc:  # reported to the parent, which falls back to the stored body
        conn.send(("error", f"{type(exc).__name__}: {exc}", None))
    finally:
        conn.close()


def rebuild_isolated(path: str, config: int | None = None, timeout: float = 300.0) -> tuple[dict, dict]:
    """rebuild_part in a child process: trying candidate parameters can drive the geometry kernel into a crash,
    which must not take the application down. When the child crashes, the rebuild runs again with the feature
    it was trying taken from the stored body (up to 3 such features). Returns (part document dict, report);
    raises RebuildError when the history is not understood, the child keeps crashing or it runs out of time."""
    import multiprocessing as mp
    import time
    from multiprocessing.connection import wait
    ctx = mp.get_context("spawn")
    avoid: set = set()
    deadline = time.monotonic() + timeout
    while True:
        parent, child = ctx.Pipe(duplex=False)
        # (the child's own budget leaves time for its start-up, the final comparison and the result transfer)
        budget = max(5.0, 0.85 * (deadline - time.monotonic()) - 5.0)
        proc = ctx.Process(target=_rebuild_worker, args=(path, config, child, frozenset(avoid), budget), daemon=True)
        proc.start()
        # the child end stays open here until the child is done: on Windows the child copies the handle out of
        # this process after it starts, and closing it first can leave the child unconnected (it then hangs).
        # A crash is seen through the process sentinel instead of the end of the pipe.
        current, result = None, None
        try:
            while result is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise RebuildError(f"rebuilding the history took longer than {timeout:g} s")
                ready = wait([parent, proc.sentinel], left)
                if not ready:
                    raise RebuildError(f"rebuilding the history took longer than {timeout:g} s")
                if parent in ready or parent.poll():
                    msg = parent.recv()
                    if msg[0] == "feature":
                        current = msg[1]
                    else:
                        result = msg
                elif not proc.is_alive():
                    break  # the child crashed without a result
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join(5)
            child.close()
            parent.close()
        if result is not None:
            status, data, report = result
            if status != "ok":
                raise RebuildError(data)
            return data, report
        if current is None or current in avoid or len(avoid) >= 3:
            raise RebuildError("the geometry kernel crashed while rebuilding the history")
        avoid.add(current)
