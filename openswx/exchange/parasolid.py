"""Clean-room Parasolid XT (.x_t text transmit file) reader -> OpenCASCADE B-rep.

Written from the published "Parasolid XT Format Reference" (UGS, 2006): file layout, node schema, embedded
schema edit scripts (V14+ writers describe their schema as edits against base schema 13006), geometry
definitions and topology. No Parasolid code is used. Lengths are converted from metres to millimetres.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------- base schema (13006)
# field spec: "name:code" ; code = d n u c l w f p v i b h ; fixed arrays "code[k]" ; variable "code[]"
_CURVE = "node_id:d attributes_groups:p owner:p next:p previous:p geometric_owner:p sense:c"
_SURF = _CURVE
BASE_SCHEMA = {
    10: "highest_node_id:d attributes_groups:p attribute_chains:p list:p surface:p curve:p point:p key:p res_size:f "
        "res_linear:f ref_instance:p next:p previous:p state:u owner:p type:u sub_instance:p",
    11: "node_id:d attributes_groups:p type:u part:p transform:p assembly:p next_in_part:p prev_in_part:p "
        "next_of_part:p prev_of_part:p",
    12: "highest_node_id:d attributes_groups:p attribute_chains:p surface:p curve:p point:p key:p res_size:f "
        "res_linear:f ref_instance:p next:p previous:p state:u owner:p body_type:u nom_geom_state:u shell:p "
        "boundary_surface:p boundary_curve:p boundary_point:p region:p edge:p vertex:p",
    13: "node_id:d attributes_groups:p body:p next:p face:p edge:p vertex:p region:p front_face:p",
    14: "node_id:d attributes_groups:p tolerance:f next:p previous:p loop:p shell:p surface:p sense:c "
        "next_on_surface:p previous_on_surface:p next_front:p previous_front:p front_shell:p",
    15: "node_id:d attributes_groups:p fin:p face:p next:p",
    16: "node_id:d attributes_groups:p tolerance:f fin:p previous:p next:p curve:p next_on_curve:p "
        "previous_on_curve:p owner:p",
    17: "attributes_groups:p loop:p forward:p backward:p vertex:p other:p edge:p curve:p next_at_vx:p sense:c",
    18: "node_id:d attributes_groups:p fin:p previous:p next:p point:p tolerance:f owner:p",
    19: "node_id:d attributes_groups:p body:p next:p previous:p shell:p type:c",
    29: "node_id:d attributes_groups:p owner:p next:p previous:p pvec:v",
    30: _CURVE + " pvec:v direction:v",
    31: _CURVE + " centre:v normal:v x_axis:v radius:f",
    32: _CURVE + " centre:v normal:v x_axis:v major_radius:f minor_radius:f",
    38: _CURVE + " surface:p[2] chart:p start:p end:p",
    40: "base_parameter:f base_scale:f chart_count:d chordal_error:f angular_error:f parameter_error:f[2] hvec:h[]",
    41: "type:c hvec:h[]",
    45: "vertices:f[]",
    50: _SURF + " pvec:v normal:v x_axis:v",
    51: _SURF + " pvec:v axis:v radius:f x_axis:v",
    52: _SURF + " pvec:v axis:v radius:f sin_half_angle:f cos_half_angle:f x_axis:v",
    53: _SURF + " centre:v radius:f axis:v x_axis:v",
    54: _SURF + " centre:v axis:v major_radius:f minor_radius:f x_axis:v",
    56: _SURF + " blend_type:c surface:p[2] spine:p range:f[2] thumb_weight:f[2] boundary:p[2] start:p end:p",
    59: _SURF + " boundary:n blend:p",
    60: _SURF + " check:c true_offset:l surface:p offset:f scale:f",
    67: _SURF + " section:p sweep:v scale:f",
    68: _SURF + " profile:p base:v axis:v start:v end:v start_param:f end_param:f x_axis:v scale:f",
    70: "node_id:d owner:p next:p previous:p list_type:d list_length:d block_length:d size_of_entry:d list_block:p "
        "finger_block:p finger_index:d notransmit:l",
    74: "n_entries:d next_block:p entries:p[]",
    79: "string:c[]",
    80: "next:p identifier:p type_id:d actions:u[8] field_names:p legal_owners:l[14] fields:u[]",
    81: "node_id:d definition:p owner:p next:p previous:p next_of_type:p previous_of_type:p fields:p[]",
    82: "values:d[]", 83: "values:f[]", 84: "values:c[]", 85: "values:v[]", 86: "values:v[]", 87: "values:v[]",
    88: "values:d[]", 89: "values:v[]", 98: "values:w[]",
    90: "node_id:d attributes_groups:p owner:p next:p previous:p type:u first_member:p",
    91: "dummy_node_id:d owning_group:p owner:p next:p previous:p next_member:p previous_member:p",
    99: "names:p[]",
    100: "node_id:d owner:p next:p previous:p rotation_matrix:f[9] translation_vector:v scale:f flag:d "
         "perspective_vector:v",
    101: "assembly:p attribute:p body:p transform:p surface:p curve:p point:p alive:l attrib_def:p highest_id:d "
         "current_id:d",
    102: "string:c[]",
    120: _SURF + " type:c data:p tf:p internal_geom:p[]",
    121: "geom_type:d real_array:p int_array:p",
    122: "key:p real_array:p int_array:p",
    124: _SURF + " nurbs:p data:p",
    125: "original_uint:i original_vint:i extended_uint:i extended_vint:i self_int:u original_u_start:c "
         "original_u_end:c original_v_start:c original_v_end:c extended_u_start:c extended_u_end:c "
         "extended_v_start:c extended_v_end:c analytic_form_type:c swept_form_type:c spun_form_type:c "
         "blend_form_type:c analytic_form:p swept_form:p spun_form:p blend_form:p",
    126: "u_periodic:l v_periodic:l u_degree:n v_degree:n n_u_vertices:d n_v_vertices:d u_knot_type:u v_knot_type:u "
         "n_u_knots:d n_v_knots:d rational:l u_closed:l v_closed:l surface_form:u vertex_dim:n bspline_vertices:p "
         "u_knot_mult:p v_knot_mult:p u_knots:p v_knots:p",
    127: "mult:n[]",
    128: "knots:f[]",
    130: _CURVE + " type:c data:p tf:p internal_geom:p[]",
    133: _CURVE + " basis_curve:p point_1:v point_2:v parm_1:f parm_2:f",
    134: _CURVE + " nurbs:p data:p",
    135: "self_int:u analytic_form:p",
    136: "degree:n n_vertices:d vertex_dim:n n_knots:d knot_type:u periodic:l closed:l rational:l curve_form:u "
         "bspline_vertices:p knot_mult:p knots:p",
    137: _CURVE + " surface:p b_curve:p original:p tolerance_to_original:f",
    141: "owner:p next:p previous:p shared_geometry:p",
    163: "axis_pt:v axis_dir:v hand:c turns:i pitch:f gap:f tol:f",  # surface helix form (163/184 swapped in the reference)
    184: "axis_pt:v axis_dir:v point:v hand:c turns:i pitch:f tol:f",  # curve helix form (163/184 swapped in the reference)
}
_WIDTH = {"v": 3, "h": 3, "i": 2, "b": 6}
_VISIBLE = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 29, 30, 31, 32, 38, 50, 51, 52, 53, 54, 56, 60, 67, 68, 70,
            80, 81, 90, 100, 120, 124, 130, 133, 134, 137}


@dataclass
class Field:
    name: str
    code: str  # scalar type letter
    n: int = 0  # 0 scalar, 1 variable-length, >1 fixed array
    xmt: bool = True


def _parse_spec(spec: str) -> list[Field]:
    out = []
    for tok in spec.split():
        name, code = tok.split(":")
        n = 0
        if code.endswith("[]"):
            code, n = code[:-2], 1
        elif "[" in code:
            code, k = code[:-1].split("[")
            n = int(k)
        out.append(Field(name, code, n))
    return out


class XTError(ValueError):
    pass


# ---------------------------------------------------------------------- streams
# Both encodings expose the same semantic reads, so one schema-driven parser serves text and binary files.
class _Text:
    def __init__(self, data: str):
        self.s = data.replace("\r", "").replace("\n", "")
        self.i = 0

    def char(self) -> str:
        c = self.s[self.i]
        self.i += 1
        return c

    def peek(self) -> str:
        return self.s[self.i]

    def word(self) -> str:
        j = self.s.index(" ", self.i)
        w = self.s[self.i:j]
        self.i = j + 1
        return w

    def int(self) -> int:
        return int(self.word())

    def real(self):
        if self.peek() == "?":
            self.i += 1
            return None
        return float(self.word())

    def chars(self, n: int) -> str:
        c = self.s[self.i:self.i + n]
        self.i += n
        return c

    def short_string(self) -> str:
        return self.chars(self.int())

    # semantic reads
    def prefix(self, embedded_probe):
        if self.char() != "T":
            raise XTError("not a text transmit file")
        version, schema = self.short_string(), self.short_string()
        maxtypes = self.int() if embedded_probe(schema) else None
        return version, schema, maxtypes, self.int()

    ntype = varcount = index = nfields = usfld = int
    op = char

    def edit_count(self):
        if self.s.startswith("255 ", self.i):
            self.word()
            return None
        return self.int()

    def field_desc(self) -> Field:
        name = self.short_string()
        ptr_class = self.int()
        n = self.int()
        code = "p" if ptr_class else self.short_string()
        xmt = self.char() in ("T", "1") if n == 1 else True
        return Field(name, code, n, xmt)

    def value(self, c):
        if c in ("c", "l"):
            return self.char()
        if c in ("d", "n", "u", "w", "p", "t"):
            if self.peek() == "?":
                self.i += 1
                return None
            return self.int()
        if c == "f":
            return self.real()
        if c in _WIDTH:
            if self.peek() == "?":  # a null vector / interval is written as a single '?'
                self.i += 1
                return None
            return tuple(self.real() for _ in range(_WIDTH[c]))
        raise XTError(f"unknown field type {c!r}")


_NULL_INT, _NULL_REAL = -32764, -3.14158e13


class _Binary:
    """Neutral binary (big-endian, flag 'PS\\0\\0') or bare binary (native order, flag 'B')."""

    def __init__(self, data: bytes, order: str):
        import struct
        self.d, self.i = data, 0
        self.S = {k: struct.Struct(order + k) for k in "hiBd"}

    def _get(self, k):
        st = self.S[k]
        v = st.unpack_from(self.d, self.i)[0]
        self.i += st.size
        return v

    def chars(self, n: int) -> str:
        c = self.d[self.i:self.i + n].decode("latin-1")
        self.i += n
        return c

    def short_string(self) -> str:
        return self.chars(self._get("B"))

    def index(self) -> int:  # pointer-index encoding (section 1.5)
        r, q = self._get("h"), 0
        if r < 0:
            q, r = self._get("h"), -r
        return q * 32767 + r - 1

    def prefix(self, embedded_probe):
        version = self.chars(self._get("h"))
        schema = self.chars(self._get("i"))
        maxtypes = self._get("h") if embedded_probe(schema) else None
        return version, schema, maxtypes, self._get("i")

    def ntype(self):
        return self._get("h")

    def varcount(self):
        return self._get("i")

    def usfld(self):
        return self._get("i")

    def nfields(self):
        return self._get("B")

    def op(self):
        return self.chars(1)

    def edit_count(self):
        n = self._get("B")
        return None if n == 255 else n

    def field_desc(self) -> Field:
        name = self.short_string()
        ptr_class = self._get("h")
        n = self.index()
        code = "p" if ptr_class else self.short_string()
        xmt = bool(self._get("B")) if n == 1 else True
        return Field(name, code, n, xmt)

    def _real(self):
        v = self._get("d")
        return None if abs(v - _NULL_REAL) < 1e3 else v

    def value(self, c):
        if c == "c":
            return self.chars(1)
        if c == "l":
            return "T" if self._get("B") else "F"
        if c == "u":
            return self._get("B")
        if c in ("n", "w"):
            return self._get("h")
        if c == "d":
            v = self._get("i")
            return None if v == _NULL_INT else v
        if c in ("p", "t"):
            return self.index()
        if c == "f":
            return self._real()
        if c in _WIDTH:
            v = tuple(self._real() for _ in range(_WIDTH[c]))
            return None if any(x is None for x in v) else v
        raise XTError(f"unknown field type {c!r}")


# ---------------------------------------------------------------------- file reader
@dataclass
class Model:
    nodes: dict = field(default_factory=dict)  # index -> (type, {field: value})
    version: str = ""
    schema: str = ""


def _read_value(t, f: Field, count):
    """Read one field (scalar, fixed array or variable array with `count` elements)."""
    if f.n == 0:
        return t.value(f.code)
    n = f.n if f.n > 1 else (count or 0)
    if f.code == "c":
        return t.chars(n)
    return [t.value(f.code) for _ in range(n)]


def _embedded(schema: str) -> bool:
    return len(schema.split("_")) >= 4


def _read_stream(t) -> Model:
    model = Model()
    model.version, model.schema, _, usfld = t.prefix(_embedded)
    embedded = _embedded(model.schema)
    base = {k: _parse_spec(v) for k, v in BASE_SCHEMA.items()}
    schema: dict[int, list[Field]] = {}
    while True:
        ntype = t.ntype()
        if ntype == 1:
            break
        if ntype not in schema:
            if not embedded:
                if ntype not in base:
                    raise XTError(f"unknown node type {ntype}")
                schema[ntype] = base[ntype]
            elif ntype in base:
                count = t.edit_count()
                if count is None:
                    schema[ntype] = base[ntype]
                else:
                    cur, b = [], list(base[ntype])
                    while True:
                        op = t.op()
                        if op == "C":
                            if not b:
                                raise XTError(f"node type {ntype}: schema edit copies past the base fields")
                            cur.append(b.pop(0))
                        elif op == "D":
                            if not b:
                                raise XTError(f"node type {ntype}: schema edit deletes past the base fields")
                            b.pop(0)
                        elif op in ("I", "A"):
                            cur.append(t.field_desc())
                        elif op == "Z":
                            break
                        else:
                            raise XTError(f"bad schema edit op {op!r} for node type {ntype}")
                    if len(cur) != count:  # base fields left after 'Z' are implicitly deleted
                        raise XTError(f"node type {ntype}: edit gives {len(cur)} fields, header says {count}")
                    schema[ntype] = cur
            else:  # node type unknown to the base schema: full description
                nf = t.nfields()
                t.short_string()  # name
                t.short_string()  # description
                schema[ntype] = [t.field_desc() for _ in range(nf)]
        fields = schema[ntype]
        variable = bool(fields) and fields[-1].n == 1
        count = t.varcount() if variable else None
        index = t.index()
        rec = {}
        for f in fields:
            if not f.xmt:
                continue
            rec[f.name] = _read_value(t, f, count if f.n == 1 else None)
        if usfld and ntype in _VISIBLE:
            for _ in range(usfld):
                t.usfld()
        model.nodes[index] = (ntype, rec)
    return model


def read_xt_text(text: str) -> Model:
    end = text.find("**END_OF_HEADER")
    if end < 0:
        raise XTError("not a Parasolid transmit file (no header)")
    return _read_stream(_Text(text[text.index("\n", end) + 1:]))


def read_xt_bytes(raw: bytes) -> Model:
    """Parse transmit data in any encoding: text (.x_t), neutral binary or bare binary (.x_b)."""
    start = 0
    if raw.startswith(b"**"):
        end = raw.find(b"**END_OF_HEADER")
        if end < 0:
            raise XTError("not a Parasolid transmit file (no header)")
        start = raw.index(b"\n", end) + 1
    body = raw[start:]
    if body[:1] == b"T":
        return _read_stream(_Text(body.decode("latin-1")))
    if body[:4] == b"PS\0\0":
        return _read_stream(_Binary(body[4:], ">"))
    if body[:1] == b"B":
        return _read_stream(_Binary(body[1:], "<"))
    raise XTError("unrecognised Parasolid transmit encoding")


def read_xt_file(path: str) -> Model:
    with open(path, "rb") as fh:
        return read_xt_bytes(fh.read())


# ---------------------------------------------------------------------- B-rep construction (OpenCASCADE)
SCALE = 1000.0  # XT lengths are metres; openswx works in millimetres


def _within_its_edges(face) -> bool:
    """A rebuilt face is the intended one only if it stays inside its boundary edges' box: the wrong turn of a
    periodic surface covers the rest of the revolution (seen on a sliver face, where it added material)."""
    from build123d import Compound, Face
    f = Face(face)
    fb = f.bounding_box(optimal=True)
    eb = Compound(f.edges()).bounding_box(optimal=True)
    tol = 1e-3 * max(eb.diagonal, 1e-6)
    return (fb.min.X >= eb.min.X - tol and fb.min.Y >= eb.min.Y - tol and fb.min.Z >= eb.min.Z - tol
            and fb.max.X <= eb.max.X + tol and fb.max.Y <= eb.max.Y + tol and fb.max.Z <= eb.max.Z + tol)


def _fix_negative_faces(shape):
    """Faces whose loops came out with a negative area on a periodic surface (a half cylinder whose boundary
    runs along the seam gets its pcurves on the wrong turn): rebuild them on the same surface with the seam
    turned half a revolution away, so the pcurves are worked out consistently."""
    import math
    from build123d import Face, Shape
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.Geom import Geom_ElementarySurface
    from OCP.gp import gp_Ax1
    from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shape
    from OCP.TopAbs import TopAbs_FORWARD, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Face
    re, changed = BRepTools_ReShape(), False
    for f in Shape.cast(shape).faces():
        try:
            # (an area of exactly zero is the same fault: the loop's two pcurves on the seam cancel out)
            bb = f.bounding_box()
            if f.area > 1e-9 * max(bb.diagonal, 1.0) ** 2:
                continue
        except Exception:
            continue
        surf = BRep_Tool.Surface_s(f.wrapped)
        if not isinstance(surf, Geom_ElementarySurface) or not surf.IsUPeriodic():
            continue
        g = surf.Copy()
        g.Rotate(gp_Ax1(surf.Position().Location(), surf.Position().Direction()), math.pi)
        b = BRep_Builder()
        nf = TopoDS_Face()
        b.MakeFace(nf, g, 1e-7)
        ex = TopExp_Explorer(f.wrapped.Oriented(TopAbs_FORWARD), TopAbs_WIRE)
        while ex.More():
            b.Add(nf, TopoDS.Wire(ex.Current()))
            ex.Next()
        if f.wrapped.Orientation() != TopAbs_FORWARD:
            nf.Reverse()
        fx = ShapeFix_Face(nf)
        fx.Perform()
        if Face(fx.Face()).area > 0 and _within_its_edges(fx.Face()):
            re.Replace(f.wrapped, fx.Face())
            changed = True
    if not changed:
        return shape
    sf = ShapeFix_Shape(re.Apply(shape))
    sf.Perform()
    return sf.Shape()


def _param_on(curve, pnt) -> float:
    """Parameter of the point of `curve` nearest to `pnt`. An orthogonal projection can find no solution when
    the point lies just past an end of a bounded curve: then the nearer end (or nearest sample) is used."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    pr = GeomAPI_ProjectPointOnCurve(pnt, curve)
    if pr.NbPoints():
        return pr.LowerDistanceParameter()
    t0, t1 = curve.FirstParameter(), curve.LastParameter()
    if abs(t0) > 1e50 or abs(t1) > 1e50:  # unbounded (a line): nothing better than its origin
        return 0.0
    ts = [t0 + (t1 - t0) * k / 400 for k in range(401)]
    return min(ts, key=lambda t: curve.Value(t).Distance(pnt))


def _dist_on(curve, pnt) -> float:
    """Distance from `pnt` to `curve` (including its ends)."""
    return curve.Value(_param_on(curve, pnt)).Distance(pnt)


def _along(c, pts):
    """The curve, reversed if needed so that it runs the way the ordered chart points do (an XT intersection
    curve runs in its chart order); judged by the tangent at the first point, which also works on closed
    curves. A closed B-spline is made periodic so that edges on it can wrap past its start."""
    from OCP.Geom import Geom_BSplineCurve
    from OCP.gp import gp_Pnt, gp_Vec
    if len(pts) >= 2:
        k = 1
        while k < len(pts) - 1 and pts[k].Distance(pts[0]) < 1e-6:
            k += 1
        p, v = gp_Pnt(), gp_Vec()
        c.D1(_param_on(c, pts[0]), p, v)
        if v.Dot(gp_Vec(pts[0], pts[k])) < 0:
            c = c.Reversed()
    if isinstance(c, Geom_BSplineCurve) and c.IsClosed() and not c.IsPeriodic():
        try:
            c = c.Copy()
            c.SetPeriodic()
        except Exception:
            pass
    return c


def _extend_natural(s, frac: float):
    """Copy of a (clamped, non-periodic) B-spline surface whose end spans are continued as the same
    polynomials by `frac` of the parameter range on all four sides: Parasolid's implicit extension
    (evaluation past the knot range). Each end span is made a Bezier patch (end knot raised to full
    multiplicity) and re-expressed over the longer interval by de Casteljau at lambda > 1, in homogeneous
    coordinates."""
    import numpy as np
    from OCP.gp import gp_Pnt
    if s.IsURational() or s.IsVRational():
        return s  # extrapolated weights can turn negative; rational surfaces are left as they are
    s = s.Copy()
    for in_u in (True, False):
        if (s.IsUPeriodic() if in_u else s.IsVPeriodic()):
            continue  # nothing to extend around a periodic direction
        for at_end in (True, False):
            deg = s.UDegree() if in_u else s.VDegree()
            nk = s.NbUKnots() if in_u else s.NbVKnots()
            knot = (s.UKnot if in_u else s.VKnot)
            t0, t1 = knot(1), knot(nk)
            inner = knot(nk - 1) if at_end else knot(2)
            (s.InsertUKnot if in_u else s.InsertVKnot)(inner, deg, 1e-12, False)  # end span -> Bezier patch
            npu, npv = s.NbUPoles(), s.NbVPoles()
            n_dir, n_oth = (npu, npv) if in_u else (npv, npu)
            idx = list(range(n_dir - deg - 1, n_dir)) if at_end else list(range(deg, -1, -1))
            span = (t1 - inner) if at_end else (inner - t0)
            lam = (span + frac * (t1 - t0)) / span
            for j in range(1, n_oth + 1):
                get = (lambda i: (s.Pole(i, j), s.Weight(i, j))) if in_u else (lambda i: (s.Pole(j, i), s.Weight(j, i)))
                Q = []
                for i in idx:
                    p, w = get(i + 1)
                    Q.append(np.array([p.X() * w, p.Y() * w, p.Z() * w, w]))
                # control points of the same polynomial on [a, a + lam (b - a)]: L_k = level-k de Casteljau point 0
                levels, cur = [Q[0]], Q
                for _ in range(deg):
                    cur = [(1 - lam) * cur[k] + lam * cur[k + 1] for k in range(len(cur) - 1)]
                    levels.append(cur[0])
                for i, h in zip(idx, levels):
                    pt, w = gp_Pnt(h[0] / h[3], h[1] / h[3], h[2] / h[3]), float(h[3])
                    if in_u:
                        s.SetPole(i + 1, j, pt, w)
                    else:
                        s.SetPole(j, i + 1, pt, w)
            k = (s.NbUKnots() if in_u else s.NbVKnots()) if at_end else 1
            newv = t1 + frac * (t1 - t0) if at_end else t0 - frac * (t1 - t0)
            (s.SetUKnot if in_u else s.SetVKnot)(k, newv)
    return s


class _Builder:
    def __init__(self, model: Model):
        self.nodes = model.nodes
        self.curves, self.surfaces, self.vertices, self.edges = {}, {}, {}, {}
        self.flipped: set = set()  # surfaces whose OCC natural normal is opposite to the XT one
        self.warnings: list[str] = []

    def node(self, idx):
        return self.nodes.get(idx, (None, None)) if idx else (None, None)

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    # ----------------------------------------------------------------- geometry
    @staticmethod
    def _pnt(v):
        from OCP.gp import gp_Pnt
        return gp_Pnt(v[0] * SCALE, v[1] * SCALE, v[2] * SCALE)

    @staticmethod
    def _dir(v):
        from OCP.gp import gp_Dir
        return gp_Dir(v[0], v[1], v[2])

    def _ax3(self, p, z, x):
        from OCP.gp import gp_Ax3
        return gp_Ax3(self._pnt(p), self._dir(z), self._dir(x))

    def _knots(self, set_idx, mult_idx):
        from OCP.collections import Array1_double as TColStd_Array1OfReal, Array1_int as TColStd_Array1OfInteger
        ks = list(self.node(set_idx)[1]["knots"])
        ms = self.node(mult_idx)[1]["mult"]
        if any(v is None for v in ks):  # null knot entries are padding: drop them with their multiplicities
            keep = [i for i, v in enumerate(ks) if v is not None]
            ks, ms = [ks[i] for i in keep], [ms[i] for i in keep if i < len(ms)]
        k, m = TColStd_Array1OfReal(1, len(ks)), TColStd_Array1OfInteger(1, len(ms))
        for i, (a, b) in enumerate(zip(ks, ms), 1):
            k.SetValue(i, a)
            m.SetValue(i, b)
        return k, m

    def _poles(self, nb, dim2=False):
        """Cartesian poles and weights of a NURBS_CURVE (homogeneous storage divided out)."""
        dim, n = nb["vertex_dim"], nb["n_vertices"]
        vs = self.node(nb["bspline_vertices"])[1]["vertices"]
        rational = nb["rational"] == "T"
        pts, wts = [], []
        for i in range(n):
            q = vs[i * dim:(i + 1) * dim]
            w = q[-1] if rational else 1.0
            pts.append([c / w for c in (q[:-1] if rational else q)])
            wts.append(w)
        return pts, wts, rational

    def curve(self, idx):
        """3D Geom_Curve for a curve node (None for an SP-curve, which is built on its surface)."""
        if idx in self.curves:
            return self.curves[idx]
        from OCP.Geom import Geom_BSplineCurve, Geom_Circle, Geom_Ellipse, Geom_Line
        from OCP.gp import gp_Ax2, gp_Circ, gp_Elips, gp_Lin
        from OCP.collections import Array1_gp_Pnt as TColgp_Array1OfPnt
        from OCP.collections import Array1_double as TColStd_Array1OfReal
        t, r = self.node(idx)
        c = None
        if t == 30:
            c = Geom_Line(gp_Lin(self._pnt(r["pvec"]), self._dir(r["direction"])))
        elif t in (31, 32):
            ax = gp_Ax2(self._pnt(r["centre"]), self._dir(r["normal"]), self._dir(r["x_axis"]))
            c = Geom_Circle(gp_Circ(ax, r["radius"] * SCALE)) if t == 31 else \
                Geom_Ellipse(gp_Elips(ax, r["major_radius"] * SCALE, r["minor_radius"] * SCALE))
        elif t == 134:
            nb = self.node(r["nurbs"])[1]
            pts, w, rational = self._poles(nb)
            poles, wts = TColgp_Array1OfPnt(1, len(pts)), TColStd_Array1OfReal(1, len(pts))
            for i, (p, wi) in enumerate(zip(pts, w), 1):
                poles.SetValue(i, self._pnt(p))
                wts.SetValue(i, wi)
            k, m = self._knots(nb["knots"], nb["knot_mult"])
            # XT stores the full unwrapped net of a periodic curve, which OCC accepts as a non-periodic spline
            c = Geom_BSplineCurve(poles, wts, k, m, nb["degree"]) if rational else \
                Geom_BSplineCurve(poles, k, m, nb["degree"])
        elif t == 133:
            c = self.curve(r["basis_curve"])
        elif t == 38:
            c = self._intersection(r)
        elif t != 137:
            self.warn(f"curve node type {t} is not supported")
        self.curves[idx] = c
        return c

    def _intersection(self, r):
        """INTERSECTION curve: intersect its two surfaces exactly and keep the branch through the chart points
        (the chart is only a polyline); interpolate the chart when that fails."""
        from OCP.GeomAPI import GeomAPI_IntSS, GeomAPI_Interpolate, GeomAPI_ProjectPointOnCurve
        from OCP.collections import HArray1_gp_Pnt
        pts = [self._pnt(h) for h in self.node(r["chart"])[1]["hvec"] if h]
        # an 'L' limit is an artificial end of an otherwise unbounded branch, far out on the (extended)
        # surfaces: it says nothing about the part, so it is left out of the branch test
        check = list(pts)
        if (self.node(r.get("end"))[1] or {}).get("type") == "L" and len(check) > 2:
            check = check[:-1]
        if (self.node(r.get("start"))[1] or {}).get("type") == "L" and len(check) > 2:
            check = check[1:]
        s1, s2 = (self.surface(i) for i in r["surface"])
        if s1 is not None and s2 is not None:
            # Parasolid may have extended a B-surface past its natural range (blend spines often run there),
            # so retry on extended copies when the plain intersection misses part of the chart
            for a, b in ((s1, s2), (self._extended(r["surface"][0]), self._extended(r["surface"][1]))):
                inter = GeomAPI_IntSS(a, b, 1e-7)
                best, err = None, None
                if inter.IsDone():
                    for k in range(1, inter.NbLines() + 1):
                        cand = inter.Line(k)
                        e = max(GeomAPI_ProjectPointOnCurve(p, cand).LowerDistance()
                                if GeomAPI_ProjectPointOnCurve(p, cand).NbPoints() else 1e9 for p in check)
                        if err is None or e < err:
                            best, err = cand, e
                if best is not None and err < 1e-4:
                    return _along(best, check)
        contact = self._contact_curve(r, check, pts)
        if contact is not None:
            return contact
        arr = HArray1_gp_Pnt(1, len(pts))
        for i, p in enumerate(pts, 1):
            arr.SetValue(i, p)
        it = GeomAPI_Interpolate(arr, False, 1e-9)
        it.Perform()
        self.warn("intersection curves interpolated through their chart points")
        return it.Curve()

    def _contact_curve(self, r, check, pts, samples: int = 800):
        """Blend boundary (an intersection with a BLEND_BOUND): the rolling ball touches the supporting surface
        at the foot of its centre, so the curve is the projection of the spine onto that surface, sampled
        densely and interpolated."""
        from OCP.GeomAPI import GeomAPI_Interpolate, GeomAPI_ProjectPointOnCurve, GeomAPI_ProjectPointOnSurf
        from OCP.collections import HArray1_gp_Pnt
        bb = next((si for si in r["surface"] if self.node(si)[0] == 59), None)
        if bb is None:
            return None
        bt, br = self.node(bb)
        blend = self.node(br["blend"])[1]
        support = self._extended(blend["surface"][1 - br["boundary"]])
        spine = self.curve(blend["spine"])
        if support is None or spine is None:
            return None
        # sample only the part of the spine that feeds this edge (with a margin), not its far extension
        pars = [_param_on(spine, p) for p in pts]
        a, b = min(pars), max(pars)
        pad = 0.1 * (b - a) + 1e-9
        a, b = max(spine.FirstParameter(), a - pad), min(spine.LastParameter(), b + pad)
        feet = []
        for k in range(samples + 1):
            pr = GeomAPI_ProjectPointOnSurf(spine.Value(a + (b - a) * k / samples), support)
            if pr.NbPoints():
                feet.append(pr.NearestPoint())
        if len(feet) < 4:
            return None
        arr = HArray1_gp_Pnt(1, len(feet))
        for i, p in enumerate(feet, 1):
            arr.SetValue(i, p)
        it = GeomAPI_Interpolate(arr, False, 1e-9)
        it.Perform()
        if not it.IsDone():
            return None
        c = it.Curve()
        if max(_dist_on(c, p) for p in check) > 1e-3:  # chart chords: ~6e-3
            return None
        return _along(c, check)

    def _seam_away(self, s, idx):
        """Move the u-seam of a periodic blend tube into the widest gap between the blend's boundary points
        (chart points of the intersection curves that lie on it), so the blend face does not straddle it."""
        import math
        from OCP.ShapeAnalysis import ShapeAnalysis_Surface
        if not s.IsUPeriodic():
            return s
        sas = ShapeAnalysis_Surface(s)  # (GeomAPI_ProjectPointOnSurf can take minutes on these fine pipe surfaces)
        us = []
        for t, r in self.nodes.values():
            if t != 38:
                continue
            on = any(si == idx or (self.node(si)[0] == 59 and self.node(si)[1].get("blend") == idx) for si in r["surface"])
            if on:  # (about 16 points per curve: enough to see where the boundary runs; projecting onto the
                # pipe surface is slow, and charts can hold hundreds of points)
                pts = [h for h in self.node(r["chart"])[1]["hvec"] if h]
                for h in pts[::max(1, len(pts) // 16)] + pts[-1:]:
                    if h:
                        uv = sas.ValueOfUV(self._pnt(h), 1e-4)
                        if sas.Gap() < 1e-3:
                            us.append(uv.X())
        if len(us) < 2:
            return s
        u0, per = s.Bounds()[0], s.UPeriod()
        us = sorted((u - u0) % per for u in us)
        gaps = [(us[(i + 1) % len(us)] - us[i]) % per or per for i in range(len(us))]
        k = max(range(len(gaps)), key=gaps.__getitem__)
        cut = u0 + (us[k] + gaps[k] / 2) % per
        s = s.Copy()
        s.InsertUKnot(cut, 1, 1e-9, True)
        ki = min(range(1, s.NbUKnots() + 1), key=lambda i: abs(s.UKnot(i) - cut))
        s.SetUOrigin(ki)
        del math
        return s

    def _extended(self, idx):
        """The surface, with B-splines (also under an offset) extended on all four sides by the part size:
        used only for intersections, never as face geometry (extension may re-parametrise)."""
        from OCP.Geom import Geom_BSplineSurface, Geom_OffsetSurface
        from OCP.GeomLib import GeomLib
        t, r = self.node(idx)
        s = self.surface(idx)
        if t == 60 and s is not None:
            base = self._extended(r["surface"])
            return Geom_OffsetSurface(base, s.Offset(), True) if base is not None else s
        if isinstance(s, Geom_BSplineSurface):
            try:
                return _extend_natural(s, 1.0)
            except Exception:  # knot insertion refused (degenerate spans): intersect the plain surface
                return s
        return s

    def surface(self, idx):
        if idx in self.surfaces:
            return self.surfaces[idx]
        import math
        from OCP.Geom import (Geom_ConicalSurface, Geom_CylindricalSurface, Geom_OffsetSurface, Geom_Plane,
                              Geom_SphericalSurface, Geom_SurfaceOfLinearExtrusion, Geom_SurfaceOfRevolution,
                              Geom_ToroidalSurface)
        from OCP.gp import gp_Ax1
        t, r = self.node(idx)
        s = None
        if t == 50:
            s = Geom_Plane(self._ax3(r["pvec"], r["normal"], r["x_axis"]))
        elif t == 51:
            s = Geom_CylindricalSurface(self._ax3(r["pvec"], r["axis"], r["x_axis"]), r["radius"] * SCALE)
        elif t == 52:
            # the radius grows along +A (the axis points away from the apex: verified on SOLIDWORKS exports)
            s = Geom_ConicalSurface(self._ax3(r["pvec"], r["axis"], r["x_axis"]),
                                    math.atan2(r["sin_half_angle"], r["cos_half_angle"]), r["radius"] * SCALE)
        elif t == 53:
            s = Geom_SphericalSurface(self._ax3(r["centre"], r["axis"], r["x_axis"]), r["radius"] * SCALE)
        elif t == 54:
            s = Geom_ToroidalSurface(self._ax3(r["centre"], r["axis"], r["x_axis"]), r["major_radius"] * SCALE,
                                     r["minor_radius"] * SCALE)
        elif t == 67:
            base = self.curve(r["section"])
            if base is not None:
                s = self._bspline_extrusion(base, r["sweep"], self.extent()) or Geom_SurfaceOfLinearExtrusion(base, self._dir(r["sweep"]))
        elif t == 68:  # OCC revolves with u = angle, v = profile: the reverse of XT, so the normal flips
            prof = self.curve(r["profile"])
            if prof is not None:
                s = Geom_SurfaceOfRevolution(prof, gp_Ax1(self._pnt(r["base"]), self._dir(r["axis"])))
                self.flipped.add(idx)
        elif t == 60:
            basis = self.surface(r["surface"])
            if basis is not None:
                bsense = self.node(r["surface"])[1].get("sense", "+")
                sign = (1 if bsense == "+" else -1) * (-1 if r["surface"] in self.flipped else 1)
                s = Geom_OffsetSurface(basis, r["offset"] * SCALE * sign, True)  # also C0 bases (Parasolid allows them)
                if r["surface"] in self.flipped:
                    self.flipped.add(idx)
        elif t == 124:
            s = self._bsurface(r)
        elif t == 56:  # rolling-ball blend: the tube of radius |range| swept by the ball along its spine
            spine = self.curve(r["spine"])
            rad = max(abs(x) for x in r["range"] if x is not None) * SCALE if r.get("range") else 0.0
            if spine is not None and rad > 0:
                from OCP.GeomFill import GeomFill_Pipe
                from OCP.GeomAbs import GeomAbs_C2
                pipe = GeomFill_Pipe(spine, rad)
                try:
                    pipe.Perform(1e-7, False, GeomAbs_C2, 11, 1000)  # many segments: the default 30 misses by ~0.2 mm
                except Exception:  # some spines defeat the approximation: fill from the boundary instead
                    pipe = None
                if pipe is not None and pipe.IsDone():
                    s = self._seam_away(pipe.Surface(), idx)
                    if pipe.ErrorOnSurf() > 1e-5:
                        self.warn(f"blend surface approximated to {pipe.ErrorOnSurf():.1e} mm")
            if s is None:
                self.warn("blend surface rebuilt as a filling surface through its boundary")
        else:
            if t != 59:  # BLEND_BOUND is construction-only: its curves are rebuilt from the blend (_contact_curve)
                self.warn(f"surface node type {t} rebuilt as a filling surface through its boundary")
        self.surfaces[idx] = s
        return s

    @staticmethod
    def _bspline_extrusion(base, sweep, reach):
        """C(u) + v*D for a B-spline section, as an exact degree-1-in-v B-spline surface over v in
        [-reach, reach] mm, reach covering the whole part (OCC integrates mass properties of these far more accurately than of
        Geom_SurfaceOfLinearExtrusion)."""
        from OCP.collections import Array1_double, Array1_int, Array2_double, Array2_gp_Pnt
        from OCP.Geom import Geom_BSplineCurve, Geom_BSplineSurface
        from OCP.gp import gp_Vec
        if not isinstance(base, Geom_BSplineCurve):
            return None
        d = gp_Vec(*sweep).Normalized()
        n = base.NbPoles()
        poles, wts = Array2_gp_Pnt(1, n, 1, 2), Array2_double(1, n, 1, 2)
        for i in range(1, n + 1):
            p = base.Pole(i)
            poles.SetValue(i, 1, p.Translated(d * -reach))
            poles.SetValue(i, 2, p.Translated(d * reach))
            wts.SetValue(i, 1, base.Weight(i))
            wts.SetValue(i, 2, base.Weight(i))
        uk, um = Array1_double(1, base.NbKnots()), Array1_int(1, base.NbKnots())
        for i in range(1, base.NbKnots() + 1):
            uk.SetValue(i, base.Knot(i))
            um.SetValue(i, base.Multiplicity(i))
        vk, vm = Array1_double(1, 2), Array1_int(1, 2)
        vk.SetValue(1, -reach)
        vk.SetValue(2, reach)
        vm.SetValue(1, 2)
        vm.SetValue(2, 2)
        return Geom_BSplineSurface(poles, wts, uk, vk, um, vm, base.Degree(), 1)

    def extent(self):
        """Twice the largest point coordinate in the file (mm): a length that spans the whole part. The points
        are the vertices, the edges' circles and ellipses (centre + radius: a full-circle edge has no vertex)
        and the poles of the 3D B-curves (a spline edge can bulge well past its vertices)."""
        if not hasattr(self, "_extent"):
            m = max((abs(c) for t, r in self.nodes.values() if t == 29 and r["pvec"] for c in r["pvec"]), default=1.0)
            on_edges = {r.get("curve") for t, r in self.nodes.values() if t == 16}  # (not construction circles)
            for i, (t, r) in self.nodes.items():
                if t in (31, 32) and i in on_edges and r.get("centre"):
                    m = max([m] + [abs(c) + abs(r.get("radius") or r.get("major_radius") or 0.0) for c in r["centre"]])
                if t != 134 or r.get("nurbs") not in self.nodes:
                    continue
                try:
                    pts = self._poles(self.node(r["nurbs"])[1])[0]
                except Exception:  # (incomplete data: the vertices alone)
                    continue
                m = max([m] + [abs(c) for q in pts if len(q) == 3 for c in q])
            self._extent = 2 * m * SCALE + 10.0
        return self._extent

    def _bsurface(self, r):
        from OCP.Geom import Geom_BSplineSurface
        from OCP.collections import Array2_gp_Pnt as TColgp_Array2OfPnt
        from OCP.collections import Array2_double as TColStd_Array2OfReal
        nb = self.node(r["nurbs"])[1]
        nu, nv, dim = nb["n_u_vertices"], nb["n_v_vertices"], nb["vertex_dim"]
        vs = self.node(nb["bspline_vertices"])[1]["vertices"]
        rational = nb["rational"] == "T"
        poles, wts = TColgp_Array2OfPnt(1, nu, 1, nv), TColStd_Array2OfReal(1, nu, 1, nv)
        for i in range(nu):
            for j in range(nv):
                k = (i * nv + j) * dim if self.v_fastest else (j * nu + i) * dim
                q = vs[k:k + dim]
                w = q[3] if rational else 1.0
                poles.SetValue(i + 1, j + 1, self._pnt([q[0] / w, q[1] / w, q[2] / w]))
                wts.SetValue(i + 1, j + 1, w)
        uk, um = self._knots(nb["u_knots"], nb["u_knot_mult"])
        vk, vm = self._knots(nb["v_knots"], nb["v_knot_mult"])
        if rational:
            s = Geom_BSplineSurface(poles, wts, uk, vk, um, vm, nb["u_degree"], nb["v_degree"])
        else:
            s = Geom_BSplineSurface(poles, uk, vk, um, vm, nb["u_degree"], nb["v_degree"])
        # XT stores a periodic surface unwrapped; faces bounded only by ring edges need OCC to know it is
        # periodic (so that it can add a seam)
        for flag, setter in (("u_periodic", s.SetUPeriodic), ("v_periodic", s.SetVPeriodic)):
            if nb.get(flag) == "T":
                try:
                    setter()
                except Exception:
                    pass  # not closed enough for OCC: leave it clamped
        return s

    v_fastest = True

    # ----------------------------------------------------------------- topology
    def vertex(self, idx):
        if idx not in self.vertices:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            r = self.node(idx)[1]
            self.vertices[idx] = BRepBuilderAPI_MakeVertex(self._pnt(self.node(r["point"])[1]["pvec"])).Vertex()
        return self.vertices[idx]

    def _edge_vertices(self, er):
        """(start, end) vertex indices: edge.fin is positive, so its vertex is the end, other's is the start."""
        f = self.node(er["fin"])[1]
        end = f["vertex"] if f else None
        o = self.node(f["other"])[1] if f else None
        start = o["vertex"] if o else None
        return start, end

    def edge(self, idx):
        if idx in self.edges:
            return self.edges[idx]
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
        er = self.node(idx)[1]
        cidx = er["curve"]
        if not cidx:  # tolerant edge: use the curve of one of its fins
            f, seen = er["fin"], 0
            while f and not self.node(f)[1]["curve"] and seen < 32:
                f, seen = self.node(f)[1]["other"], seen + 1
            cidx = self.node(f)[1]["curve"] if f else None
        ct, cr = self.node(cidx)
        sense = "+" if ct == 133 else (cr or {}).get("sense", "+")
        trim = None
        if ct == 133:
            bt, br = self.node(cr["basis_curve"])
            if bt == 137:
                trim = (cr.get("parm_1"), cr.get("parm_2"))
                ct, cr = bt, br
        s_idx, e_idx = self._edge_vertices(er)
        if ct == 137:
            e = self._sp_edge(cr, s_idx, e_idx, sense, trim)
            self.edges[idx] = e
            return e
        curve = self.curve(cidx)
        if curve is None:
            raise XTError(f"edge {idx}: curve type {ct} cannot be evaluated")
        if not s_idx:  # ring edge: the whole closed curve
            e = BRepBuilderAPI_MakeEdge(curve).Edge()
        else:
            vs, ve = self.vertex(s_idx), self.vertex(e_idx)
            if sense == "-":
                vs, ve = ve, vs

            def par(v):
                return _param_on(curve, BRep_Tool.Pnt_s(v))
            u1, u2 = par(vs), par(ve)
            if curve.IsPeriodic():
                per = curve.Period()
                while u2 <= u1 + 1e-9:
                    u2 += per
                while u2 - u1 > per + 1e-9:
                    u2 -= per
            for v in (vs, ve):  # a rebuilt (approximated) curve may pass a hair off a vertex: widen its tolerance
                d = _dist_on(curve, BRep_Tool.Pnt_s(v))
                if d > BRep_Tool.Tolerance_s(v):
                    from OCP.BRep import BRep_Builder
                    BRep_Builder().UpdateVertex(v, 1.5 * d)
            mk = BRepBuilderAPI_MakeEdge(curve, vs, ve, u1, u2)
            if not mk.IsDone():
                raise XTError(f"edge {idx}: could not trim its curve between the vertices")
            e = mk.Edge()
        if sense == "-":
            e.Reverse()
        self.edges[idx] = e
        return e

    @staticmethod
    def _trim_sp(c2, surf, va, vb, trim, sense="+"):
        """Edge on the 2D curve between vertices va and vb: parameters from the TRIMMED_CURVE when there is one,
        else from projecting the vertices onto the curve; each end takes the vertex nearest to it (tolerance
        widened to reach), falling back to the edge's own end vertices (sewn by the solid repair)."""
        from OCP.BRep import BRep_Builder, BRep_Tool
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.BRepLib import BRepLib
        free = BRepBuilderAPI_MakeEdge(c2, surf).Edge()
        BRepLib.BuildCurves3d_s(free)
        c3, f, l = BRep_Tool.Curve_s(free, 0.0, 0.0), c2.FirstParameter(), c2.LastParameter()
        pa, pb = BRep_Tool.Pnt_s(va), BRep_Tool.Pnt_s(vb)
        if trim and None not in trim:
            lo, hi = sorted(float(x) for x in trim)
        else:
            lo, hi = sorted((_param_on(c3, pa), _param_on(c3, pb)))
        if hi - lo < 1e-12:
            lo, hi = f, l
        p_lo = c3.Value(lo)
        v_lo, v_hi = (va, vb) if p_lo.Distance(pa) <= p_lo.Distance(pb) else (vb, va)
        for v, t in ((v_lo, lo), (v_hi, hi)):
            d = c3.Value(t).Distance(BRep_Tool.Pnt_s(v))
            if d > BRep_Tool.Tolerance_s(v):
                BRep_Builder().UpdateVertex(v, 1.5 * d + 1e-7)
        mk = BRepBuilderAPI_MakeEdge(c2, surf, v_lo, v_hi, lo, hi)
        e = mk.Edge() if mk.IsDone() else BRepBuilderAPI_MakeEdge(c2, surf, lo, hi).Edge()
        if va.IsSame(vb):  # a closed edge: the direction comes from the trim order (or the curve sense)
            if (trim and None not in trim and trim[1] < trim[0]) or (not trim and sense == "-"):
                e.Reverse()
        elif v_lo is not va:  # the XT edge runs from va (its start) to vb
            e.Reverse()
        return e

    def _sp_edge(self, cr, s_idx, e_idx, sense, trim=None):
        """Edge from an SP-curve: a 2D B-curve in a surface's parameter space; OCC rebuilds the 3D curve."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.BRepLib import BRepLib
        from OCP.Geom2d import Geom2d_BSplineCurve
        from OCP.gp import gp_Pnt2d
        from OCP.collections import Array1_gp_Pnt2d as TColgp_Array1OfPnt2d
        from OCP.collections import Array1_double as TColStd_Array1OfReal
        surf = self.surface(cr["surface"])
        if surf is None:
            raise XTError("SP-curve on a surface that cannot be evaluated")
        nb = self.node(self.node(cr["b_curve"])[1]["nurbs"])[1]
        pts, w, rational = self._poles(nb)
        poles, wts = TColgp_Array1OfPnt2d(1, len(pts)), TColStd_Array1OfReal(1, len(pts))
        for i, (p, wi) in enumerate(zip(pts, w), 1):
            poles.SetValue(i, gp_Pnt2d(p[0], p[1]))
            wts.SetValue(i, wi)
        k, m = self._knots(nb["knots"], nb["knot_mult"])
        c2 = Geom2d_BSplineCurve(poles, wts, k, m, nb["degree"]) if rational else \
            Geom2d_BSplineCurve(poles, k, m, nb["degree"])
        if s_idx:  # oriented start -> end by _trim_sp
            e = self._trim_sp(c2, surf, self.vertex(s_idx), self.vertex(e_idx), trim, sense)
            BRepLib.BuildCurves3d_s(e)
            return e
        e = BRepBuilderAPI_MakeEdge(c2, surf).Edge()
        BRepLib.BuildCurves3d_s(e)
        if sense == "-":
            e.Reverse()
        return e

    def face(self, idx):
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Wire
        b = BRep_Builder()
        fr = self.node(idx)[1]
        wires = []
        lp, guard = fr["loop"], 0
        while lp and guard < 100000:
            lr = self.node(lp)[1]
            w = TopoDS_Wire()
            b.MakeWire(w)
            f0 = f = lr["fin"]
            count = 0
            while f:
                finr = self.node(f)[1]
                if finr["edge"]:
                    e = self.edge(finr["edge"])
                    b.Add(w, TopoDS.Edge(e.Reversed()) if finr["sense"] == "-" else e)
                    count += 1
                f = finr["forward"]
                if f == f0 or count > 100000:
                    break
            if count:
                wires.append(w)
            lp, guard = lr["next"], guard + 1
        sidx = fr["surface"]
        surf = self.surface(sidx) if sidx else None
        if surf is None:
            return self._filled_face(wires)
        if any(self._ring(w) for w in wires):
            surf = self._periodic(surf)
        # XT loops keep the face on their left seen from the face normal; OCC reads wires against the
        # surface's natural normal, so build on the natural side and flip the whole face afterwards
        flip = (fr["sense"] != self.node(sidx)[1].get("sense", "+")) != (sidx in self.flipped)
        face = TopoDS_Face()
        b.MakeFace(face, surf, 1e-7)
        for w in wires:
            b.Add(face, TopoDS.Wire(w.Reversed()) if flip else w)
        if flip:
            face.Reverse()
        return face

    @staticmethod
    def _ring(wire) -> bool:
        """A loop of one closed edge with no vertex of its own (XT needs no seam on a closed surface)."""
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        ex, edges = TopExp_Explorer(wire, TopAbs_EDGE), []
        while ex.More():
            edges.append(TopoDS.Edge(ex.Current()))
            ex.Next()
        return len(edges) == 1 and BRep_Tool.IsClosed_s(edges[0])

    @staticmethod
    def _periodic(surf):
        """A B-spline surface closed but not periodic, made periodic: ShapeFix only adds the seam a band of
        ring loops needs on a periodic surface (else it drops a loop and leaves a zero-area face)."""
        from OCP.Geom import Geom_BSplineSurface
        if not isinstance(surf, Geom_BSplineSurface):
            return surf
        out = surf
        for closed, periodic, make in (("IsUClosed", "IsUPeriodic", "SetUPeriodic"),
                                       ("IsVClosed", "IsVPeriodic", "SetVPeriodic")):
            if getattr(out, closed)() and not getattr(out, periodic)():
                try:
                    cp = out.Copy()
                    getattr(cp, make)()
                    out = cp
                except Exception:  # (poles that only nearly meet: left as it is)
                    pass
        return out

    def _filled_face(self, wires):
        from OCP.BRepFill import BRepFill_Filling
        from OCP.GeomAbs import GeomAbs_C0
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        fill = BRepFill_Filling()
        exp = TopExp_Explorer(wires[0], TopAbs_EDGE)
        while exp.More():
            fill.Add(TopoDS.Edge(exp.Current()), GeomAbs_C0)
            exp.Next()
        fill.Build()
        return fill.Face()

    def _shell_body(self, sh):
        shr = self.node(sh)[1]
        if not shr:
            return None
        return shr.get("body") or (self.node(shr.get("region"))[1] or {}).get("body")

    def body_ids(self):
        return [i for i, (t, _) in self.nodes.items() if t == 12]

    def bodies(self):
        """Every BODY in the file as an OCC shape: a solid (outer shell plus any void shells) for solid bodies,
        else a compound of faces. Pcurves, seams and orientation are completed by one ShapeFix pass per body,
        so edges shared between faces stay shared."""
        from OCP.BRep import BRep_Builder
        from OCP.ShapeFix import ShapeFix_Shape
        from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell, TopoDS_Solid
        shells: dict = {}  # body -> {shell -> [faces]}; a face is filed under the shell it bounds as a back face
        for i, (t, r) in self.nodes.items():
            if t == 14:
                sh = r["shell"] or r["front_shell"]
                shells.setdefault(self._shell_body(sh), {}).setdefault(sh, []).append(i)
        out = []
        for idx in self.body_ids():
            r = self.nodes[idx][1]
            b = BRep_Builder()
            groups = shells.get(idx, {})
            if r["body_type"] == 1:
                shape = TopoDS_Solid()
                b.MakeSolid(shape)
                for faces in groups.values():
                    shell = TopoDS_Shell()
                    b.MakeShell(shell)
                    for f in faces:
                        b.Add(shell, self.face(f))
                    b.Add(shape, shell)
            else:
                shape = TopoDS_Compound()
                b.MakeCompound(shape)
                for faces in groups.values():
                    for f in faces:
                        b.Add(shape, self.face(f))
            fix = ShapeFix_Shape(shape)
            fix.Perform()
            fixed = _fix_negative_faces(fix.Shape())
            if r["body_type"] == 1:  # a closed solid inside out (negative volume) is turned the right way
                from OCP.BRepGProp import BRepGProp
                from OCP.GProp import GProp_GProps
                from OCP.TopAbs import TopAbs_SOLID
                from OCP.TopExp import TopExp_Explorer
                sols, flipped = [], False
                ex = TopExp_Explorer(fixed, TopAbs_SOLID)
                while ex.More():
                    so = ex.Current()
                    g = GProp_GProps()
                    BRepGProp.VolumeProperties_s(so, g)
                    if g.Mass() < 0:
                        so, flipped = so.Reversed(), True
                    sols.append(so)
                    ex.Next()
                if flipped:
                    if len(sols) == 1:
                        fixed = sols[0]
                    else:
                        comp = TopoDS_Compound()
                        b.MakeCompound(comp)
                        for so in sols:
                            b.Add(comp, so)
                        fixed = comp
            # sanity: a rebuilt body far outside the part's own points means geometry went wrong (e.g. an
            # offset of a C0 surface running away); fail rather than hand back nonsense
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            box = Bnd_Box()
            BRepBndLib.AddOptimal_s(fixed, box, False, False)  # surfaces too, not just edges
            if not box.IsVoid():
                lim = 2 * self.extent() + 10.0
                lo, hi = box.CornerMin(), box.CornerMax()
                if max(abs(lo.X()), abs(lo.Y()), abs(lo.Z()), abs(hi.X()), abs(hi.Y()), abs(hi.Z())) > lim:
                    raise XTError("a body could not be rebuilt (geometry runs far outside the part)")
            out.append(fixed)
        return out


def face_attributes(model: Model, name: str = "FACE_ID_2001") -> dict:
    """{FACE node index: integer values of its first attribute field} for attributes whose definition is
    `name` (SOLIDWORKS tags every face with FACE_ID_2001: records of (code, feature id, time stamp, 0,
    payload...), the generating record last)."""
    nodes = model.nodes
    defs = {i for i, (t, r) in nodes.items() if t == 80 and (nodes.get(r.get("identifier"), (0, {}))[1].get("string") == name)}
    out = {}
    for t, r in nodes.values():
        if t == 81 and r.get("definition") in defs and nodes.get(r.get("owner"), (0,))[0] == 14:
            vals = next((nodes[f][1].get("values") for f in r.get("fields", []) if f in nodes), None)
            if vals:
                out[r["owner"]] = [int(v) for v in vals]
    return out


def read_xt_shape(path: str):
    """Read a Parasolid .x_t / .x_b file. Returns (shape, warnings): a build123d Solid (or a Compound when the file
    holds several bodies) in millimetres, and notes about any geometry that had to be approximated."""
    from build123d import Compound, Shape
    b = _Builder(read_xt_file(path))
    shapes = [Shape.cast(s) for s in b.bodies()]
    if not shapes:
        raise XTError("the file contains no bodies")
    return (shapes[0] if len(shapes) == 1 else Compound(shapes)), b.warnings
