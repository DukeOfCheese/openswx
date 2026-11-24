from __future__ import annotations

from typing import Literal

from openswx.core.document import DocumentError
from openswx.session import APP, command

__all__ = ["list_standard_parts", "standard_part"]

# deep-groove ball bearings, ISO 15 boundary dimensions: designation -> (bore d, outside D, width B) mm
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "608": (8, 22, 7),
            "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9), "6003": (17, 35, 10), "6004": (20, 42, 12),
            "6005": (25, 47, 12), "6006": (30, 55, 13), "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11),
            "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15), "6206": (30, 62, 16), "6300": (10, 35, 11),
            "6301": (12, 37, 12), "6302": (15, 42, 13), "6303": (17, 47, 14), "6304": (20, 52, 15)}
# parallel keys, DIN 6885 / ISO 773: shaft diameter over .. up to -> (width b, height h) mm
KEYS = [(6, 8, 2, 2), (8, 10, 3, 3), (10, 12, 4, 4), (12, 17, 5, 5), (17, 22, 6, 6), (22, 30, 8, 7), (30, 38, 10, 8),
        (38, 44, 12, 8), (44, 50, 14, 9), (50, 58, 16, 10), (58, 65, 18, 11), (65, 75, 20, 12)]
# T-slot extrusions (generic): size -> (side, slot width, slot depth, centre bore diameter) mm
EXTRUSIONS = {"2020": (20, 6, 6, 4.2), "3030": (30, 8, 9, 6.8), "4040": (40, 8, 12, 6.8), "4545": (45, 10, 13, 8.5)}
# O-ring cross-sections in common use, ISO 3601 (mm)
ORING_SECTIONS = (1.0, 1.5, 1.78, 2.0, 2.5, 2.62, 3.0, 3.53, 4.0, 5.0, 5.33, 7.0)


@command(mutates=False, category="library")
def list_standard_parts() -> dict:
    """The standard parts standard_part can make: deep-groove ball bearings (ISO 15 designations with bore,
    outside diameter and width), parallel keys (DIN 6885 sections by shaft diameter), O-ring cross-sections
    (ISO 3601) and generic T-slot extrusion sizes."""
    return {"ball_bearing": {k: {"bore": d, "outside_diameter": D, "width": B} for k, (d, D, B) in BEARINGS.items()},
            "parallel_key": [{"shaft_over": a, "shaft_to": b, "width": w, "height": h} for a, b, w, h in KEYS],
            "o_ring": {"cross_sections": list(ORING_SECTIONS)},
            "t_slot_extrusion": {k: {"side": s, "slot_width": w, "slot_depth": d, "bore": b} for k, (s, w, d, b) in EXTRUSIONS.items()}}


@command(category="library")
def standard_part(kind: Literal["ball_bearing", "parallel_key", "o_ring", "t_slot_extrusion"], designation: str | None = None,
                  length: float | None = None, shaft_diameter: float | None = None, inside_diameter: float | None = None,
                  cross_section: float | None = None, name: str | None = None) -> dict:
    """Make a standard part as a new part document (insert it into an assembly with insert_component).
    ball_bearing: designation '608', '6204', ... - a ring with the bearing's bore, outside diameter and width.
    parallel_key: shaft_diameter picks the DIN 6885 section (or designation 'b x h', e.g. '8x7'), with length.
    o_ring: inside_diameter and cross_section (mm). t_slot_extrusion: designation '2020' / '3030' / '4040' /
    '4545' with length - a square bar with a slot on each side and a centre bore (generic proportions: for
    layout and mass, not for the slot's exact shape). Custom properties carry the designation for the BOM."""
    from openswx import session as S
    props = {}
    if kind == "ball_bearing":
        if designation not in BEARINGS:
            raise DocumentError(f"no bearing {designation!r}; see list_standard_parts ({', '.join(list(BEARINGS)[:6])}, ...)")
        d, D, B = BEARINGS[designation]
        doc_name = name or f"Bearing {designation}"
        S.new_part.__wrapped__(doc_name)
        sk = S.create_sketch.__wrapped__("Front")["sketch"]
        S.sketch_circle.__wrapped__(sk, [0, 0], D / 2)
        S.sketch_circle.__wrapped__(sk, [0, 0], d / 2)
        S.extrude.__wrapped__(sk, B, end_condition="MidPlane")
        props = {"Description": f"Deep groove ball bearing {designation} ({d} x {D} x {B})", "PartNo": designation, "Standard": "ISO 15"}
        size = {"bore": d, "outside_diameter": D, "width": B}
    elif kind == "parallel_key":
        if not length or length <= 0:
            raise DocumentError("a key needs a length")
        if designation:
            try:
                w, h = (float(x) for x in designation.lower().replace(" ", "").split("x"))
            except ValueError:
                raise DocumentError("give the key section as 'b x h', e.g. '8x7'") from None
        elif shaft_diameter:
            row = next((r for r in KEYS if r[0] < shaft_diameter <= r[1]), None)
            if row is None:
                raise DocumentError(f"no DIN 6885 key section for a {shaft_diameter:g} mm shaft (table: over 6 to 75 mm)")
            w, h = float(row[2]), float(row[3])
        else:
            raise DocumentError("give shaft_diameter or designation ('8x7')")
        if length < w:
            raise DocumentError("the key is shorter than it is wide")
        doc_name = name or f"Key {w:g}x{h:g}x{length:g}"
        S.new_part.__wrapped__(doc_name)
        sk = S.create_sketch.__wrapped__("Top")["sketch"]
        S.sketch_slot.__wrapped__(sk, [0, 0], [length - w, 0], w)  # form A: round ends
        S.extrude.__wrapped__(sk, h)
        props = {"Description": f"Parallel key A {w:g} x {h:g} x {length:g}", "PartNo": f"DIN 6885 A {w:g}x{h:g}x{length:g}", "Standard": "DIN 6885"}
        size = {"width": w, "height": h, "length": length}
    elif kind == "o_ring":
        if not inside_diameter or not cross_section or inside_diameter <= 0 or cross_section <= 0:
            raise DocumentError("an O-ring needs inside_diameter and cross_section")
        doc_name = name or f"O-ring {inside_diameter:g}x{cross_section:g}"
        S.new_part.__wrapped__(doc_name)
        sk = S.create_sketch.__wrapped__("Front")["sketch"]
        S.sketch_centerline.__wrapped__(sk, [0, -cross_section], [0, cross_section])
        S.sketch_circle.__wrapped__(sk, [inside_diameter / 2 + cross_section / 2, 0], cross_section / 2)
        S.revolve.__wrapped__(sk)
        props = {"Description": f"O-ring {inside_diameter:g} x {cross_section:g}", "PartNo": f"{inside_diameter:g}x{cross_section:g}", "Standard": "ISO 3601"}
        size = {"inside_diameter": inside_diameter, "cross_section": cross_section}
    else:
        if designation not in EXTRUSIONS:
            raise DocumentError(f"no extrusion {designation!r}; sizes: {', '.join(EXTRUSIONS)}")
        if not length or length <= 0:
            raise DocumentError("an extrusion needs a length")
        a, w, dp, bore = EXTRUSIONS[designation]
        doc_name = name or f"Extrusion {designation} L{length:g}"
        S.new_part.__wrapped__(doc_name)
        sk = S.create_sketch.__wrapped__("Front")["sketch"]
        S.sketch_rectangle.__wrapped__(sk, [-a / 2, -a / 2], [a / 2, a / 2])
        S.extrude.__wrapped__(sk, length)
        cut = S.create_sketch.__wrapped__("Front")["sketch"]
        h = a / 2
        for x0, y0, x1, y1 in ((-w / 2, h - dp, w / 2, h + 1), (-w / 2, -h - 1, w / 2, -h + dp),
                               (h - dp, -w / 2, h + 1, w / 2), (-h - 1, -w / 2, -h + dp, w / 2)):
            S.sketch_rectangle.__wrapped__(cut, [x0, y0], [x1, y1])
        S.sketch_circle.__wrapped__(cut, [0, 0], bore / 2)
        S.cut_extrude.__wrapped__(cut, end_condition="ThroughAllBoth")
        props = {"Description": f"T-slot extrusion {designation}, {length:g} mm", "PartNo": f"{designation}-{length:g}", "Standard": "generic T-slot"}
        size = {"side": a, "slot_width": w, "slot_depth": dp, "bore": bore, "length": length}
    doc = APP.doc
    doc.properties.update(props)
    doc.journal = None  # (built by this one command, not by a recorded sequence: no script to export)
    return {"part": doc.name, "kind": kind, "size": size, "volume": doc.body.volume if doc.body is not None else 0.0,
            "properties": props, "model": doc.summary()}
