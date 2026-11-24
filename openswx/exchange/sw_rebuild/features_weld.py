"""Weldment structural-member rebuild helpers for the SOLIDWORKS history rebuild."""
from __future__ import annotations

import copy
import math

from openswx.core.document import DocumentError, PartDocument

from ._base import RebuildError, _Oracle, _check_time, _log, _plane, _try, _volume
from .sketches import _around_origin, _section_sketches

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
