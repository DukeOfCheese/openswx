"""Rebuild a SOLIDWORKS part's feature history in openswx from its decoded tree, sketches and stored body."""
from __future__ import annotations

import itertools
import math
import re

import numpy as np

from openswx.core.document import DocumentError, PartDocument

from ._base import (FIXED_BASE, MATCH, NEUTRAL, SKIP, STD_PLANES, TOL, RebuildError, _AVOID, _BASE_TRIALS,
                    _DEADLINE, _LATER_SCALE, _LUMP, _MOVED_LATER, _PROGRESS, _REVOLVE_AXES, _TAGGED, _TRUST,
                    _Oracle, _check_time, _faces_on_body, _import_delta, _log, _lump, _mesh_volume, _plane,
                    _tagged_faces, _try, _try_kinds, _volume)
from .features import (_draft_variants, _fillet_radii, _helix, _hole_variants, _off_boundary_edges, _scale_bytes,
                       _scale_factor)
from .features_weld import _weld_member
from .patterns import _try_pattern
from .sketches import _consume, _loose_axes, _orient_arcs, _reach, _revolve_angles, _settle_arcs, _sketch_dict

def _combine(doc: PartDocument, oracle: _Oracle, name: str) -> dict:
    """Combine bodies: the bodies and the mode are not decoded, so each main body with all the others (or with
    one other) is tried in each mode. Combine needs two or more bodies: when the part has one, an earlier boss
    was made as a body of its own ("merge result" off, not decoded either), so each earlier boss (the most
    recent first) is tried with merge off, and the combination that agrees best is kept."""

    def variants():
        n_ = len(doc.body.solids()) if doc.body is not None else 0
        if not 2 <= n_ <= 6:
            return []
        sets = []
        for m_ in range(n_):
            others = [j for j in range(n_) if j != m_]
            sets += [(m_, others)] + ([(m_, [j]) for j in others] if len(others) > 1 else [])
        return [{"op": "combine", "main": m_, "tools": t_, "mode": md}
                for md in ("add", "subtract", "common") for m_, t_ in sets]
    if variants():
        return _try(doc, oracle, "body_op", name, variants())
    best, best_s = None, -math.inf
    bosses = [f for f in doc.features[1:] if f["params"].get("merge") is True][::-1][:6]
    for b in bosses:
        _check_time()
        try:
            doc.edit_feature(b["name"], {"merge": False})
        except DocumentError:
            continue
        vs = variants()
        if vs:
            try:
                feat = _try(doc, oracle, "body_op", name, vs)
                s = oracle.score_doc(doc)
                if s > best_s + 1e-6:
                    best, best_s = (b["name"], feat["params"]), s
                doc.delete_feature(name, with_children=False)
            except RebuildError:
                pass
        doc.edit_feature(b["name"], {"merge": True})
    if best is None:
        raise RebuildError(f"{name}: needs two or more bodies")
    doc.edit_feature(best[0], {"merge": False})
    return doc.add_feature("body_op", best[1], name=name)


def _lines_as_curves(doc: PartDocument, name: str, sk: dict) -> None:
    """A sketch of lines only, all taken for construction lines (a lone line reads as a centerline): its lines
    not flagged as construction in their own records become ordinary lines, so the sketch can be a sweep's
    path or guide curve."""
    feat = doc.find(name)
    if not sk.get("lines") or feat is None or feat.get("type") != "sketch":
        return
    data = feat["params"]["sketch"]
    ents = data["entities"]
    if not ents or any(e["type"] != "line" or not e.get("construction") for e in ents.values()):
        return
    pts = data["points"]
    key = lambda xy: (round(xy[0], 6), round(xy[1], 6))  # noqa: E731
    flagged = {frozenset((key(sk["points"][a]), key(sk["points"][b]))) for a, b in sk["lines"]
               if frozenset((a, b)) in sk.get("construction", set())}
    new = {k: ({**e, "construction": False} if frozenset((key(pts[e["p1"]]), key(pts[e["p2"]]))) not in flagged else e)
           for k, e in ents.items()}
    if new != ents:
        doc.edit_feature(name, {"sketch": {**data, "entities": new}})


def _try_planes(doc: PartDocument, oracle: _Oracle, sketches: dict, kind: str, name: str, variants: list[dict],
                uses) -> dict:
    """_try for a feature built from several sketches (sweep, loft), also trying the candidate planes of each
    sketch whose plane was not decoded (a frame record that only says "a standard plane"). `uses(params)` names
    the sketches a variant uses. The combination that agrees best with the stored body is kept."""
    names = list(dict.fromkeys(n for v in variants for n in uses(v) if "R" in sketches.get(n, {})))  # (3D: none)
    options = {n: [(sketches[n]["R"], sketches[n]["t"])] + list(sketches[n].get("frames", []))
               + list(sketches[n].get("more_frames", [])) for n in names}
    open_ = [n for n in names if len(options[n]) > 1]
    if not open_:
        return _try(doc, oracle, kind, name, variants)
    # (the decoded planes first; up to 24 combinations, as each runs every variant)
    combos = list(itertools.islice(itertools.product(*[options[n] for n in open_]), 24))
    original = {n: doc.find(n)["params"]["plane"] for n in open_}
    s0, v0 = oracle.score_doc(doc), _volume(doc)
    s0 = s0 if math.isfinite(s0) else 0.0
    best, best_s = None, -math.inf
    for combo in combos:
        _check_time()
        try:
            for n, (R, t) in zip(open_, combo):
                doc.edit_feature(n, {"plane": _plane(R, t)})
            feat = _try(doc, oracle, kind, name, variants)
        except (DocumentError, RebuildError):
            continue
        s, params, v = oracle.score_doc(doc), feat["params"], _volume(doc)
        doc.delete_feature(name, with_children=False)
        if s > best_s + 1e-6:
            best, best_s = (combo, params), s
        # the feature changed the body only where it agrees with the stored body (all the change is gain):
        # no other plane can do better
        if oracle.volume > 0 and abs(v - v0) > 1e-9 and s - s0 >= abs(v - v0) * (1 - 1e-3) - 1e-6:
            break
    if best is None:
        for n, pl in original.items():
            doc.edit_feature(n, {"plane": pl})
        raise RebuildError(f"{name}: no variant builds")
    for n, (R, t) in zip(open_, best[0]):
        doc.edit_feature(n, {"plane": _plane(R, t)})
    return doc.add_feature(kind, best[1], name=name)


_RUN: dict = {}  # the stored body's oracle and face ids, kept between the passes of one rebuild_part call


def rebuild_part(path: str, doc: PartDocument, config: int | None = None, budget: float = 150.0) -> dict:
    """Recreate the part's features in `doc` (an empty part). Returns a report; raises RebuildError when a
    feature is not understood or the result does not match the stored body.

    A built feature whose tagged faces are not all on the body is normally replaced by its imported delta. A
    later feature can extend those faces though (a coplanar boss keeps the earlier tag), so when that leaves
    the base feature not editable, the history is rebuilt once more keeping every built feature, and the
    comparison of the finished body with the stored one decides."""
    import time
    start = time.monotonic()
    _RUN.clear()  # (what a second pass can reuse from the first: both read the same file)
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
    if _RUN.get("key") != (path, config):  # (the oracle keeps the scores of bodies tried already)
        _RUN.clear()
        _RUN.update(key=(path, config), oracle=_Oracle(shape), faces={})
    oracle = oracle_full = _RUN["oracle"]
    pre_scale: dict = {}  # later scale factor -> oracle of the stored body scaled back
    axes: dict[str, list[str]] = {}
    last_sketch = recent_sketch = None
    sketch_order, consumed = [], set()  # sketches in tree order; those a feature has used
    built, skipped, imported, face_cache = [], [], [], _RUN["faces"]
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
                    # (older parts: the thickness on the Sheet-Metal feature before it - moTemplateSheetMetal_c in
                    # files saved by SOLIDWORKS 2018 and later - the bend radius and depth on the base bend after it)
                    sheet = next((g for g in reversed(tree[:tree.index(f)])
                                  if g["cls"] in ("moSheetMetal_c", "moTemplateSheetMetal_c")), None)
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
                elif cls == "moSewRefSurface_c":  # Knit: the surface bodies so far, sewn. A planar surface whose
                    # class was not decoded (read as None, so not built) is made first from a sketch still unused,
                    # named after the undecoded features just before the knit
                    i_ = tree.index(f)
                    unnamed = [g["name"] for g in tree[:i_] if g["cls"] is None and g["name"] not in built
                               and not any(h["cls"] is not None and h["cls"] not in SKIP | NEUTRAL
                                           for h in tree[tree.index(g) + 1:i_])]
                    for x in [s for s in sketch_order if s not in consumed and not sketches[s].get("is3d")]:
                        nm = unnamed.pop(0) if unnamed else f"{name}-{x}"
                        try:
                            build = lambda x=x, nm=nm: _try(doc, oracle, "planar_surface", nm, [{"sketch": x}])  # noqa: E731
                            _consume(doc, oracle, x, sketches[x], nm, build)
                            consumed.add(x)
                            built.append(nm)
                        except RebuildError as exc:
                            _log(str(exc))
                    surfs = list(doc.ctx.surfaces)
                    if len(surfs) < 2:
                        raise RebuildError(f"{name}: needs two or more surface bodies")
                    solid_ = bool(oracle.shape.solids())
                    _try(doc, oracle, "knit_surface", name, [{"surfaces": surfs, "tolerance": t, "form_solid": solid_}
                                                             for t in (1e-4, 2.5e-3, 0.1)])
                elif cls == "moCombineBodies_c":  # Combine: which bodies and the mode are not decoded; each main
                    # body with all the others, or with one other, is tried in each mode
                    _combine(doc, oracle, name)
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
                    for x in free[-3:]:  # a lone line reads as a centerline: as a path or guide it is the curve
                        _lines_as_curves(doc, x, sketches.get(x, {}))
                    if kind == "sweep" and len(free) >= 3:  # (a guide curve the profile follows: a third sketch)
                        variants += [{"sketch": a, "path": b, "guide": g, "merge": True}
                                     for a, b, g in itertools.permutations(free[-3:], 3)
                                     if not sketches.get(a, {}).get("lines") and sketches.get(g, {}).get("lines")]
                    feat = _try_planes(doc, oracle, sketches, kind, name, variants,
                                       lambda v: (v["sketch"], v["path"]) + ((v["guide"],) if v.get("guide") else ()))
                    consumed.update((feat["params"]["sketch"], feat["params"]["path"]))
                    if feat["params"].get("guide"):
                        consumed.add(feat["params"]["guide"])
                    last_sketch = None
                elif cls in ("moBlend_c", "moBlendCut_c"):
                    free = [s for s in sketch_order if s not in consumed]
                    if len(free) < 2:
                        raise RebuildError(f"{name}: needs two or more profile sketches")
                    kind = "cut_loft" if cls == "moBlendCut_c" else "loft"
                    variants = [{"profiles": free[-k:], "merge": True} for k in range(len(free), 1, -1)]
                    feat = _try_planes(doc, oracle, sketches, kind, name, variants, lambda v: v["profiles"])
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
                    _lines_as_curves(doc, last_sketch, sketches[last_sketch])  # (a lone profile line, read as a centerline)
                    variants = [{"sketch": last_sketch, "axis": a, "angle": g} for g in angs for a in cands]
                    build = lambda: _try(doc, oracle, "revolved_surface", name, variants)  # noqa: E731
                    _consume(doc, oracle, last_sketch, sketches[last_sketch], name, build)  # (its candidate planes too)
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
