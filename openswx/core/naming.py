from __future__ import annotations

import weakref

_BODY_LABELS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # body Shape -> {face hash: name}


def _key(v: float, q: float) -> float:
    return round(v / q) * q + 0.0


def surface_key(face) -> tuple | None:
    """A face's underlying surface (with the face's side) as a hashable value; None for freeform surfaces."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus
    from OCP.TopAbs import TopAbs_REVERSED
    s = BRepAdaptor_Surface(face.wrapped)
    rev = face.wrapped.Orientation() == TopAbs_REVERSED
    t = s.GetType()

    def axis(ax):
        d, p = ax.Direction(), ax.Location()
        v = [d.X(), d.Y(), d.Z()]
        i = max(range(3), key=lambda k: abs(v[k]))
        if v[i] < 0:
            v = [-c for c in v]
        q = [p.X(), p.Y(), p.Z()]
        dot = sum(a * b for a, b in zip(q, v))
        foot = [a - dot * b for a, b in zip(q, v)]  # the axis line's point nearest the origin
        return tuple(_key(c, 1e-7) for c in v) + tuple(_key(c, 1e-5) for c in foot)
    if t == GeomAbs_Plane:
        pl = s.Plane()
        n, p = pl.Axis().Direction(), pl.Location()
        sgn = (-1.0 if rev else 1.0) * (1.0 if pl.Direct() else -1.0)  # (left-handed placement: normal = -Z)
        v = [sgn * n.X(), sgn * n.Y(), sgn * n.Z()]
        d = v[0] * p.X() + v[1] * p.Y() + v[2] * p.Z()
        return ("plane",) + tuple(_key(c, 1e-7) for c in v) + (_key(d, 1e-5),)
    if t == GeomAbs_Cylinder:
        c = s.Cylinder()
        return ("cyl", rev) + axis(c.Axis()) + (_key(c.Radius(), 1e-5),)
    if t == GeomAbs_Cone:
        c = s.Cone()
        a = c.Apex()
        return ("cone", rev) + axis(c.Axis()) + (_key(c.SemiAngle(), 1e-7),) + tuple(_key(x, 1e-5) for x in (a.X(), a.Y(), a.Z()))
    if t == GeomAbs_Sphere:
        c = s.Sphere()
        o = c.Location()
        return ("sph", rev, _key(o.X(), 1e-5), _key(o.Y(), 1e-5), _key(o.Z(), 1e-5), _key(c.Radius(), 1e-5))
    if t == GeomAbs_Torus:
        c = s.Torus()
        return ("tor", rev) + axis(c.Axis()) + (_key(c.MajorRadius(), 1e-5), _key(c.MinorRadius(), 1e-5))
    return None


def _on_surface(face, pts, tol: float = 1e-5) -> bool:
    """All points lie on the face's (untrimmed) surface."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.gp import gp_Pnt
    surf = BRep_Tool.Surface_s(face.wrapped)
    for p in pts:
        pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), surf)
        if pr.NbPoints() == 0 or pr.LowerDistance() > tol:
            return False
    return True


def _inner_point(face):
    from openswx.core.geom import closest_point
    q, _ = closest_point(face, face.center())
    return (q.X, q.Y, q.Z)


def _order_key(face) -> tuple:
    """A cheap fixed position of a face for ordering faces of one name: its lowest vertex (a face without
    vertices: a point on it)."""
    vs = face.vertices()
    if vs:
        return min((round(v.X, 3), round(v.Y, 3), round(v.Z, 3)) for v in vs)
    return tuple(round(c, 3) for c in _inner_point(face))


def _base(label: str) -> str:
    return label.split("#")[0]


def labels_of(body) -> dict | None:
    if body is None:
        return None
    try:
        return _BODY_LABELS.get(body)
    except TypeError:
        return None


def label_of_face(face, body=None) -> str | None:
    """The name of a face of `body` (or of any body named since it was made)."""
    h = hash(face)
    if body is not None:
        lab = labels_of(body)
        return lab.get(h) if lab else None
    for lab in list(_BODY_LABELS.values()):
        if h in lab:
            return lab[h]
    return None


def name_faces(body, before, feature: str, sketches: list | None = None) -> dict:
    """Name the faces of `body` (the body after `feature`; `before` the body before it, already named).
    `sketches` are (sketch name, Sketch, Plane) of the sketches the feature used. Returns {face hash: name}."""
    if body is None:
        return {}
    faces = list(body.faces())
    old = labels_of(before) or {}
    by_key: dict = {}
    old_free = []  # (face, name) of old faces on freeform surfaces
    if before is not None:
        for g in before.faces():
            nm = old.get(hash(g))
            if nm is None:
                continue
            k = surface_key(g)
            if k is None:
                old_free.append((g, _base(nm)))
            else:
                by_key.setdefault(k, _base(nm))
    names: dict[int, str] = {}
    new = []
    for i, f in enumerate(faces):
        h = hash(f)
        if h in old:  # the very same face
            names[i] = _base(old[h])
            continue
        k = surface_key(f)
        if k is not None and k in by_key:
            names[i] = by_key[k]
            continue
        if k is None and old_free:
            p = _inner_point(f)
            hit = next((nm for g, nm in old_free if _on_surface(g, [p], 1e-6)), None)
            if hit is not None:
                names[i] = hit
                continue
        new.append(i)
    # new faces of a sketch-based feature: the sketch entity they were made from, or a cap
    sk_edges = []
    for sname, sk, plane in sketches or []:
        try:
            sk_edges.append((sname, plane, sk.to_edges(plane)))
        except Exception:
            sk_edges.append((sname, plane, []))
    for i in list(new):
        f = faces[i]
        k = surface_key(f)
        for sname, plane, edges in sk_edges:
            n = plane.z_dir
            if k is not None and k[0] == "plane" and abs(abs(k[1] * n.X + k[2] * n.Y + k[3] * n.Z) - 1.0) < 1e-6:
                p = _inner_point(f)  # parallel to the sketch: a cap
                off = (p[0] - plane.origin.X) * n.X + (p[1] - plane.origin.Y) * n.Y + (p[2] - plane.origin.Z) * n.Z
                names[i] = f"{feature}:cap{'0' if abs(off) < 1e-6 else '+' if off > 0 else '-'}"
                new.remove(i)
                break
            hit = None
            for eid, e in edges:
                try:
                    pts = [tuple(e.position_at(u)) for u in (0.3, 0.7)]
                except Exception:
                    continue
                if _on_surface(f, pts):
                    hit = f"{feature}:{sname}.{eid}"
                    break
            if hit:
                names[i] = hit
                new.remove(i)
                break
    # the rest: after the named faces they meet
    if new:
        owners: dict[int, list[int]] = {}  # edge -> the faces it bounds (an edge's hash ignores its orientation)
        for i, f in enumerate(faces):
            for e in f.edges():
                owners.setdefault(hash(e), []).append(i)
        known = dict(names)  # (only names from before this pass: a name built from new names would nest them)
        for i in new:
            adj = {known[j] for e in faces[i].edges() for j in owners.get(hash(e), []) if j != i and j in known}
            names[i] = f"{feature}:~" + "+".join(sorted(adj)) if adj else f"{feature}:face"
    # faces sharing a name: numbered in a fixed spatial order
    groups: dict[str, list[int]] = {}
    for i, nm in names.items():
        groups.setdefault(nm, []).append(i)
    out = {}
    for nm, idx in groups.items():
        if len(idx) == 1:
            out[hash(faces[idx[0]])] = nm
            continue
        pts = {i: _order_key(faces[i]) for i in idx}
        for k, i in enumerate(sorted(idx, key=pts.get)):
            out[hash(faces[i])] = f"{nm}#{k}"
    return out


def register(body, labels: dict) -> None:
    if body is not None:
        try:
            _BODY_LABELS[body] = labels
        except TypeError:
            pass


def edge_faces(body, edge) -> list[str] | None:
    """The names of the faces on either side of an edge of `body` (sorted), or None if they are not named."""
    lab = labels_of(body)
    if not lab:
        return None
    from openswx.core.geom import faces_of_edge
    out = []
    for f in faces_of_edge(body, edge):
        nm = lab.get(hash(f))
        if nm is None:
            return None
        out.append(nm)
    return sorted(out) if len(out) == 2 else None


def find_faces(body, label: str) -> list:
    """The faces of `body` named `label` (the pieces of a split face, if that name has gone)."""
    lab = labels_of(body)
    if not lab or not label:
        return []
    faces = list(body.faces())
    hits = [f for f in faces if lab.get(hash(f)) == label]
    return hits or [f for f in faces if _base(lab.get(hash(f), "")) == _base(label)]


def find_face(body, label: str, near=None):
    """The face of `body` named `label` (several: the nearest to `near`), or None."""
    hits = find_faces(body, label)
    if not hits:
        return None
    if len(hits) == 1 or near is None:
        return hits[0]
    from openswx.core.geom import closest_point
    return min(hits, key=lambda f: closest_point(f, near)[1])


def find_edge(body, labels: list[str], near=None):
    """The edge of `body` between the faces named `labels` (several: the nearest to `near`), or None."""
    if not labels or len(labels) != 2:
        return None
    fa, fb = find_faces(body, labels[0]), find_faces(body, labels[1])
    if not fa or not fb:
        return None
    common = {hash(e) for a in fa for e in a.edges()} & {hash(e) for b in fb for e in b.edges()}
    hits = [e for e in body.edges() if hash(e) in common]  # (as the body has them: a face's copy may run backwards)
    if not hits:
        return None
    if len(hits) == 1 or near is None:
        return hits[0]
    from openswx.core.geom import closest_point
    return min(hits, key=lambda e: closest_point(e, near)[1])
