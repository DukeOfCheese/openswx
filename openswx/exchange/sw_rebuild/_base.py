"""Shared constants, state, body scoring and datum helpers for the SOLIDWORKS history rebuild."""
from __future__ import annotations

import copy
import itertools
import math
import time

from openswx.core.document import DocumentError, PartDocument

STD_PLANES = {"Front Plane", "Top Plane", "Right Plane", "Front", "Top", "Right"}
# features that add no geometry to the body
SKIP = {"moRefPlane_c", "moRefAxis_c", "moRefPoint_c", "moCoordSys_c", "moSensor_c", "moOriginProfileFeature_c",
        "moMateReferenceFeature_c", "moSketchBlockDef_c", "moAttribute_c", "moAttributeDef_c", "moScalerParameter_c",
        "moStringParameter_c", "moIntegerParameter_c", "moNoteData_c", "moDetailCabinet_c", "moLockFeature_c",
        "moDesignTableFeature_c", "mo3DProfileFeature_c", "moSheetMetal_c", "moWeldmentFeature_c", "moFlatPattern_c",
        "moTemplateFlatPattern_c", "moCutListFolder_c"}  # (flat patterns: the stored folded body catches a flattened save)
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

def _plane(R, t) -> dict:
    """openswx plane spec of a sketch frame (model = R * sketch + t; columns: sketch x, y, normal)."""
    return {"origin": [float(v) for v in t], "normal": [float(v) for v in R[:, 2]], "x_dir": [float(v) for v in R[:, 0]]}
