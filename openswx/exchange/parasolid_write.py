"""Clean-room Parasolid XT writer: OpenCASCADE solids -> text transmit file (.x_t).

Written from the published "Parasolid XT Format Reference" (2006), like parasolid.py, and checked by reading
the output back with that reader and by importing it into SOLIDWORKS (tools/difftest/xt_roundtrip.py). The
file uses the plain base schema SCH_13006 (no embedded schema edits), which every Parasolid V13 or later
reads. Geometry: planes, cylinders, cones, spheres, tori and B-spline surfaces; lines, circles, ellipses and
B-spline curves (other kinds are converted to B-splines first). Lengths are written in metres.
"""

from __future__ import annotations

import math
import time

from openswx.exchange.parasolid import BASE_SCHEMA, SCALE, Model, _Builder, _parse_spec

_SCHEMA = {k: _parse_spec(v) for k, v in BASE_SCHEMA.items()}
_VERSION = ": TRANSMIT FILE created by modeller version 1300000"
_SCH = "SCH_1300000_13006"


class XTWriteError(ValueError):
    pass


def _num(v) -> str:
    if v is None:
        return "?"
    if isinstance(v, int):
        return f"{v} "
    v = float(v)
    if v == int(v) and abs(v) < 1e15:
        return f"{int(v)} "
    return f"{v:.17g} "


def _scalar(c, v) -> str:
    if c in ("c", "l"):
        return v if v is not None else "?"
    if c in ("v", "h", "i", "b"):
        return "?" if v is None else "".join(_num(x) for x in v)
    return _num(v)


def _field(f, v) -> str:
    if f.n == 1 and f.code == "c":
        return v or ""
    if f.n:
        vals = list(v) if v is not None else [None] * (f.n if f.n > 1 else 0)
        return "".join(_scalar(f.code, x) for x in vals)
    return _scalar(f.code, v)


def _records(nodes: dict) -> str:
    out = []
    for idx, (t, rec) in nodes.items():
        fields = _SCHEMA[t]
        var = fields[-1].n == 1
        head = f"{t} " + (f"{len(rec[fields[-1].name])} " if var else "") + f"{idx} "
        out.append(head + "".join(_field(f, rec.get(f.name)) for f in fields))
    out.append("1 0")
    return "".join(out)


def _wrap(stream: str, width: int = 80) -> str:
    """Records of about `width` characters; a line never ends with a space (readers may strip it)."""
    lines, i = [], 0
    while i < len(stream):
        j = min(i + width, len(stream))
        while j < len(stream) and j > i + 1 and stream[j - 1] == " ":
            j -= 1
        lines.append(stream[i:j])
        i = j
    return "\n".join(lines)


def _header(key: str, filename: str) -> str:
    stars = lambda s: s + "*" * max(0, 80 - len(s))  # noqa: E731
    part1 = ["MC=unknown;", "MC_MODEL=unknown;", "MC_ID=unknown;", "OS=unknown;", "OS_RELEASE=unknown;",
             "FRU=openswx;", "APPL=openswx;", "SITE=;", "USER=unknown;", "FORMAT=text;", "GUISE=transmit;",
             f"KEY={key};", f"FILE={filename};", f"DATE={time.strftime('%a %b %d %H:%M:%S %Y')};"]
    return "\n".join([stars("**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"),
                      stars("**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789"),
                      "**PART1;", *part1, "**PART2;", f"SCH={_SCH};", "USFLD_SIZE=0;", "**PART3;",
                      stars("**END_OF_HEADER")]) + "\n"


class _Out:
    def __init__(self):
        self.nodes: dict[int, tuple[int, dict]] = {}
        self.next_index = 1
        self.next_id = 1

    def add(self, t: int, rec: dict, node_id: bool = True) -> int:
        idx = self.next_index
        self.next_index += 1
        if node_id:
            rec.setdefault("node_id", self.next_id)
            self.next_id += 1
        self.nodes[idx] = (t, rec)
        return idx

    def rec(self, idx):
        return self.nodes[idx][1]


def _v(p):
    return (p.X(), p.Y(), p.Z())


_COMMON = {"attributes_groups": 0, "owner": 0, "next": 0, "previous": 0, "geometric_owner": 0, "sense": "+"}


def _nurbs_lists(out: _Out, knots, mults):
    return (out.add(127, {"mult": list(mults)}, node_id=False), out.add(128, {"knots": list(knots)}, node_id=False))


def _bcurve(out: _Out, c) -> int:
    """B_CURVE (134) + NURBS_CURVE (136) for a Geom_BSplineCurve (made non-periodic)."""
    c = c.Copy()
    if c.IsPeriodic():
        c.SetNotPeriodic()
    n, rational = c.NbPoles(), c.IsRational()
    verts = []
    for i in range(1, n + 1):
        p, w = c.Pole(i), (c.Weight(i) if rational else 1.0)
        verts += [p.X() * w, p.Y() * w, p.Z() * w] + ([w] if rational else [])
    bv = out.add(45, {"vertices": verts}, node_id=False)
    km, ks = _nurbs_lists(out, [c.Knot(i) for i in range(1, c.NbKnots() + 1)],
                          [c.Multiplicity(i) for i in range(1, c.NbKnots() + 1)])
    nb = out.add(136, {"degree": c.Degree(), "n_vertices": n, "vertex_dim": 4 if rational else 3,
                       "n_knots": c.NbKnots(), "knot_type": 1, "periodic": "F", "closed": "F",
                       "rational": "T" if rational else "F", "curve_form": 1, "bspline_vertices": bv,
                       "knot_mult": km, "knots": ks}, node_id=False)
    return out.add(134, {**_COMMON, "nurbs": nb, "data": 0})


def _bsurface(out: _Out, s) -> int:
    s = s.Copy()
    if s.IsUPeriodic():
        s.SetUNotPeriodic()
    if s.IsVPeriodic():
        s.SetVNotPeriodic()
    nu, nv = s.NbUPoles(), s.NbVPoles()
    rational = s.IsURational() or s.IsVRational()
    verts = []
    for i in range(1, nu + 1):
        for j in range(1, nv + 1):  # v index fastest
            p, w = s.Pole(i, j), (s.Weight(i, j) if rational else 1.0)
            verts += [p.X() * w, p.Y() * w, p.Z() * w] + ([w] if rational else [])
    bv = out.add(45, {"vertices": verts}, node_id=False)
    ukm, uks = _nurbs_lists(out, [s.UKnot(i) for i in range(1, s.NbUKnots() + 1)],
                            [s.UMultiplicity(i) for i in range(1, s.NbUKnots() + 1)])
    vkm, vks = _nurbs_lists(out, [s.VKnot(i) for i in range(1, s.NbVKnots() + 1)],
                            [s.VMultiplicity(i) for i in range(1, s.NbVKnots() + 1)])
    nb = out.add(126, {"u_periodic": "F", "v_periodic": "F", "u_degree": s.UDegree(), "v_degree": s.VDegree(),
                       "n_u_vertices": nu, "n_v_vertices": nv, "u_knot_type": 1, "v_knot_type": 1,
                       "n_u_knots": s.NbUKnots(), "n_v_knots": s.NbVKnots(), "rational": "T" if rational else "F",
                       "u_closed": "F", "v_closed": "F", "surface_form": 1, "vertex_dim": 4 if rational else 3,
                       "bspline_vertices": bv, "u_knot_mult": ukm, "v_knot_mult": vkm, "u_knots": uks,
                       "v_knots": vks}, node_id=False)
    return out.add(124, {**_COMMON, "nurbs": nb, "data": 0})


def _surface(out: _Out, face) -> int:
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.Geom import (Geom_BSplineSurface, Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane,
                          Geom_RectangularTrimmedSurface, Geom_SphericalSurface, Geom_ToroidalSurface)
    from OCP.GeomConvert import GeomConvert
    s = BRep_Tool.Surface_s(face)
    while isinstance(s, Geom_RectangularTrimmedSurface):
        s = s.BasisSurface()
    if isinstance(s, (Geom_Plane, Geom_CylindricalSurface)):
        a3 = s.Position()
        x, y = a3.XDirection(), a3.YDirection()
        z = x.Crossed(y)  # Z matching the (u, v) handedness, so XT's Y = Z x X equals OCC's Y
        if isinstance(s, Geom_Plane):
            return out.add(50, {**_COMMON, "pvec": _v(a3.Location()), "normal": _v(z), "x_axis": _v(x)})
        return out.add(51, {**_COMMON, "pvec": _v(a3.Location()), "axis": _v(z), "radius": s.Radius(), "x_axis": _v(x)})
    if isinstance(s, Geom_ConicalSurface):
        a3, a = s.Position(), s.SemiAngle()
        axis = _v(a3.Direction()) if a > 0 else tuple(-c for c in _v(a3.Direction()))  # radius grows along +axis
        return out.add(52, {**_COMMON, "pvec": _v(a3.Location()), "axis": axis, "radius": s.RefRadius(),
                            "sin_half_angle": math.sin(abs(a)), "cos_half_angle": math.cos(abs(a)),
                            "x_axis": _v(a3.XDirection())})
    if isinstance(s, Geom_SphericalSurface):
        a3 = s.Position()
        return out.add(53, {**_COMMON, "centre": _v(a3.Location()), "radius": s.Radius(), "axis": _v(a3.Direction()),
                            "x_axis": _v(a3.XDirection())})
    if isinstance(s, Geom_ToroidalSurface):
        a3 = s.Position()
        return out.add(54, {**_COMMON, "centre": _v(a3.Location()), "axis": _v(a3.Direction()),
                            "major_radius": s.MajorRadius(), "minor_radius": s.MinorRadius(),
                            "x_axis": _v(a3.XDirection())})
    if not isinstance(s, Geom_BSplineSurface):
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        s = GeomConvert.SurfaceToBSplineSurface_s(Geom_RectangularTrimmedSurface(s, u0, u1, v0, v1))
    return _bsurface(out, s)


def _curve(out: _Out, edge) -> int:
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_BSplineCurve, Geom_Circle, Geom_Ellipse, Geom_Line, Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    ad = BRepAdaptor_Curve(edge)
    f, l = ad.FirstParameter(), ad.LastParameter()
    c = BRep_Tool.Curve_s(edge, f, l)
    while isinstance(c, Geom_TrimmedCurve):
        c = c.BasisCurve()
    if isinstance(c, Geom_Line):
        ln = c.Lin()
        return out.add(30, {**_COMMON, "pvec": _v(ln.Location()), "direction": _v(ln.Direction())})
    if isinstance(c, (Geom_Circle, Geom_Ellipse)):
        a2 = c.Position()
        rec = {**_COMMON, "centre": _v(a2.Location()), "normal": _v(a2.Direction()), "x_axis": _v(a2.XDirection())}
        if isinstance(c, Geom_Circle):
            return out.add(31, {**rec, "radius": c.Radius()})
        return out.add(32, {**rec, "major_radius": c.MajorRadius(), "minor_radius": c.MinorRadius()})
    if not isinstance(c, Geom_BSplineCurve):
        c = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(c, f, l))
    return _bcurve(out, c)


def _loops(face):
    """Loops of a face as lists of oriented edges in XT order (face on the left seen from its normal), with
    OCC seam and degenerate edges removed: periodic XT faces need no seams, so a seamed loop falls apart into
    the closed runs that remain (a cylinder's [circle, seam, circle', seam'] -> [circle], [circle'])."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    loops = []
    wexp = TopExp_Explorer(face, TopAbs_WIRE)
    while wexp.More():
        w = TopoDS.Wire(wexp.Current())
        wexp.Next()
        seq = []
        it = BRepTools_WireExplorer(w, face)
        while it.More():
            e = it.Current()
            seq.append((e, BRep_Tool.Degenerated_s(e) or BRep_Tool.IsClosed_s(e, face)))
            it.Next()
        if not any(skip for _, skip in seq):
            loops.append([e for e, _ in seq])
            continue
        k = next(i for i, x in enumerate(seq) if x[1])
        seq = seq[k:] + seq[:k]  # start at a removed edge
        runs, cur = [], []
        for e, skip in seq:
            if skip:
                if cur:
                    runs.append(cur)
                cur = []
            else:
                cur.append(e)
        if cur:
            runs.append(cur)
        ends = lambda run: (TopExp.FirstVertex_s(run[0], True), TopExp.LastVertex_s(run[-1], True))  # noqa: E731
        while runs:
            run = runs.pop(0)
            while True:
                a, b = ends(run)
                if a.IsSame(b):
                    loops.append(run)
                    break
                nxt = next((r for r in runs if ends(r)[0].IsSame(b)), None)
                if nxt is None:
                    raise XTWriteError("a periodic face could not be split into closed loops")
                runs.remove(nxt)
                run = run + nxt
    return loops


def shape_to_model(shape) -> Model:
    """XT node graph {index: (type, record)} for the solids of an OCC / build123d shape given in mm."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepClass3d import BRepClass3d
    from OCP.gp import gp_Pnt, gp_Trsf
    from OCP.ShapeCustom import ShapeCustom
    from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    wrapped = getattr(shape, "wrapped", shape)
    tr = gp_Trsf()
    tr.SetScale(gp_Pnt(0, 0, 0), 1.0 / SCALE)  # mm -> m
    wrapped = BRepBuilderAPI_Transform(wrapped, tr, True).Shape()
    wrapped = ShapeCustom.ConvertToBSpline_s(wrapped, True, True, True, False)  # swept / spun / offset -> B-spline
    solids = []
    ex = TopExp_Explorer(wrapped, TopAbs_SOLID)
    while ex.More():
        solids.append(TopoDS.Solid(ex.Current()))
        ex.Next()
    if not solids:
        raise XTWriteError("only solid bodies can be written")
    out = _Out()
    body = out.add(12, {}, node_id=False)
    inf = out.add(19, {"attributes_groups": 0, "body": body, "type": "V"})  # the infinite void region comes first
    regions, shells_of = [inf], {inf: []}
    face_shells = []  # (face, back shell = solid side, front shell = void side)
    for so in solids:
        rs = out.add(19, {"attributes_groups": 0, "body": body, "type": "S"})
        regions.append(rs)
        shells_of[rs] = []
        outer = BRepClass3d.OuterShell_s(so)
        sx = TopExp_Explorer(so, TopAbs_SHELL)
        while sx.More():
            sh = TopoDS.Shell(sx.Current())
            sx.Next()
            void = inf
            if not sh.IsSame(outer):  # a cavity is its own void region
                void = out.add(19, {"attributes_groups": 0, "body": body, "type": "V"})
                regions.append(void)
                shells_of[void] = []
            s_back = out.add(13, {"attributes_groups": 0, "body": body, "edge": 0, "vertex": 0, "region": rs})
            s_front = out.add(13, {"attributes_groups": 0, "body": body, "edge": 0, "vertex": 0, "region": void})
            shells_of[rs].append(s_back)
            shells_of[void].append(s_front)
            fx = TopExp_Explorer(sh, TopAbs_FACE)
            while fx.More():
                face_shells.append((TopoDS.Face(fx.Current()), s_back, s_front))
                fx.Next()
    for i, r in enumerate(regions):
        rr = out.rec(r)
        rr["next"] = regions[i + 1] if i + 1 < len(regions) else 0
        rr["previous"] = regions[i - 1] if i else 0
        rr["shell"] = shells_of[r][0] if shells_of[r] else 0
        for j, s in enumerate(shells_of[r]):
            out.rec(s)["next"] = shells_of[r][j + 1] if j + 1 < len(shells_of[r]) else 0

    chains = {"surface": [], "curve": [], "point": [], "edge": [], "vertex": []}
    edges_xt, verts_xt, fins_of_edge, fins_at_vertex = [], [], {}, {}
    back_chain, front_chain = {}, {}

    def vertex(v):
        for vv, idx in verts_xt:
            if vv.IsSame(v):
                return idx
        pt = out.add(29, {"attributes_groups": 0, "owner": 0, "next": 0, "previous": 0, "pvec": _v(BRep_Tool.Pnt_s(v))})
        idx = out.add(18, {"attributes_groups": 0, "fin": 0, "previous": 0, "next": 0, "point": pt,
                           "tolerance": None, "owner": body})
        out.rec(pt)["owner"] = idx
        verts_xt.append((v, idx))
        chains["point"].append(pt)
        chains["vertex"].append(idx)
        return idx

    def edge(e):
        for ee, idx in edges_xt:
            if ee.IsSame(e):
                return idx, ee
        ef = TopoDS.Edge(e.Oriented(TopAbs_FORWARD))  # the XT edge runs the way its curve parameter increases
        cv = _curve(out, ef)
        idx = out.add(16, {"attributes_groups": 0, "tolerance": None, "fin": 0, "previous": 0, "next": 0, "curve": cv,
                           "next_on_curve": 0, "previous_on_curve": 0, "owner": body})
        out.rec(cv)["owner"] = idx
        chains["curve"].append(cv)
        chains["edge"].append(idx)
        edges_xt.append((ef, idx))
        fins_of_edge[idx] = []
        return idx, ef

    for face, s_back, s_front in face_shells:
        sf = _surface(out, face)
        chains["surface"].append(sf)
        fidx = out.add(14, {"attributes_groups": 0, "tolerance": None, "loop": 0, "shell": s_back, "surface": sf,
                            "sense": "+", "next_on_surface": 0, "previous_on_surface": 0, "front_shell": s_front,
                            "_occ": face})
        out.rec(sf)["owner"] = fidx
        back_chain.setdefault(s_back, []).append(fidx)
        front_chain.setdefault(s_front, []).append(fidx)
        loops = []
        for run in _loops(face):
            lp = out.add(15, {"attributes_groups": 0, "fin": 0, "face": fidx, "next": 0})
            fins = []
            for e in run:
                eidx, ef = edge(e)
                sense = "+" if e.Orientation() == ef.Orientation() else "-"
                v_start, v_end = vertex(TopExp.FirstVertex_s(ef)), vertex(TopExp.LastVertex_s(ef))
                fv = v_end if sense == "+" else v_start  # a fin's vertex is the one at its head
                fin = out.add(17, {"attributes_groups": 0, "loop": lp, "forward": 0, "backward": 0, "vertex": fv,
                                   "other": 0, "edge": eidx, "curve": 0, "next_at_vx": 0, "sense": sense}, node_id=False)
                fins.append(fin)
                fins_of_edge[eidx].append(fin)
                fins_at_vertex.setdefault(fv, []).append(fin)
            for i, fin in enumerate(fins):
                out.rec(fin)["forward"] = fins[(i + 1) % len(fins)]
                out.rec(fin)["backward"] = fins[i - 1]
            out.rec(lp)["fin"] = fins[0]
            loops.append(lp)
        for i, lp in enumerate(loops):
            out.rec(lp)["next"] = loops[i + 1] if i + 1 < len(loops) else 0
        out.rec(fidx)["loop"] = loops[0] if loops else 0
    for eidx, fins in fins_of_edge.items():  # a solid's edge has one positive and one negative fin
        senses = sorted(out.rec(f)["sense"] for f in fins)
        if senses != ["+", "-"]:
            raise XTWriteError(f"an edge has fins {senses}: the solid is not a closed manifold")
        pos = next(f for f in fins if out.rec(f)["sense"] == "+")
        neg = next(f for f in fins if out.rec(f)["sense"] == "-")
        out.rec(pos)["other"], out.rec(neg)["other"] = neg, pos
        out.rec(eidx)["fin"] = pos
    for v, fins in fins_at_vertex.items():
        out.rec(v)["fin"] = fins[0]
        for i, f in enumerate(fins):
            out.rec(f)["next_at_vx"] = fins[i + 1] if i + 1 < len(fins) else 0
    for chain, nxt, prv, head in ((back_chain, "next", "previous", "face"),
                                  (front_chain, "next_front", "previous_front", "front_face")):
        for s, faces in chain.items():
            out.rec(s)[head] = faces[0]
            for i, f in enumerate(faces):
                out.rec(f)[nxt] = faces[i + 1] if i + 1 < len(faces) else 0
                out.rec(f)[prv] = faces[i - 1] if i else 0
    for r in regions:
        for s in shells_of[r]:
            out.rec(s).setdefault("face", 0)
            out.rec(s).setdefault("front_face", 0)
    for chain in chains.values():
        for i, n in enumerate(chain):
            out.rec(n)["next"] = chain[i + 1] if i + 1 < len(chain) else 0
            out.rec(n)["previous"] = chain[i - 1] if i else 0
    head = lambda k: chains[k][0] if chains[k] else 0  # noqa: E731
    out.nodes[body] = (12, {"highest_node_id": out.next_id, "attributes_groups": 0, "attribute_chains": 0,
                            "surface": 0, "curve": 0, "point": 0, "key": 0, "res_size": 1000.0, "res_linear": 1e-8,
                            "ref_instance": 0, "next": 0, "previous": 0, "state": 1, "owner": 0, "body_type": 1,
                            "nom_geom_state": 1, "shell": shells_of[regions[1]][0], "boundary_surface": head("surface"),
                            "boundary_curve": head("curve"), "boundary_point": head("point"), "region": inf,
                            "edge": head("edge"), "vertex": head("vertex")})
    _set_face_senses(out)
    return Model(nodes=out.nodes, version=_VERSION, schema=_SCH)


def _set_face_senses(out: _Out) -> None:
    """face.sense '+' when the XT natural normal (evaluated through the reader's verified geometry mapping)
    agrees with the OCC face normal, which points out of the material."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.gp import gp_Pnt, gp_Vec
    faces = {k: r.pop("_occ") for k, (t, r) in out.nodes.items() if t == 14}
    b = _Builder(Model(nodes=out.nodes))
    for idx, face in faces.items():
        r = out.rec(idx)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        p, n = gp_Pnt(), gp_Vec()
        BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, p, n)  # includes the face orientation
        geo = b.surface(r["surface"])  # the XT surface as the reader rebuilds it (in mm)
        uu, vv = GeomAPI_ProjectPointOnSurf(gp_Pnt(p.X() * SCALE, p.Y() * SCALE, p.Z() * SCALE), geo).LowerDistanceParameters()
        nx = GeomLProp_SLProps(geo, uu, vv, 1, 1e-9).Normal()
        if r["surface"] in b.flipped:
            nx.Reverse()
        r["sense"] = "+" if nx.X() * n.X() + nx.Y() * n.Y() + nx.Z() * n.Z() > 0 else "-"


def write_xt(shape, path: str, key: str | None = None) -> dict:
    """Write the solids of `shape` (mm) to a Parasolid text transmit file (.x_t). Returns counts."""
    from pathlib import Path
    p = Path(path)
    model = shape_to_model(shape)
    stream = "T" + f"{len(_VERSION)} {_VERSION}" + f"{len(_SCH)} {_SCH}" + "0 " + _records(model.nodes)
    p.write_text(_header(key or p.stem, p.name) + _wrap(stream) + "\n", encoding="latin-1")
    count = lambda t: sum(1 for tt, _ in model.nodes.values() if tt == t)  # noqa: E731
    return {"path": str(p), "bodies": 1, "faces": count(14), "edges": count(16), "vertices": count(18)}
