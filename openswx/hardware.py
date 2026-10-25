"""Hardware library (Toolbox-style): ISO metric fasteners generated from standard dimension tables.

Geometry is simplified the way CAD fastener libraries usually are: nominal-diameter shank, no modelled
thread, plain hex/cylindrical heads. Bolts point down -Z with the underside of the head on the XY plane;
nuts and washers sit on the XY plane centred on the Z axis.
"""
from __future__ import annotations

import math

from build123d import Cylinder, Location, Pos, RegularPolygon, extrude

from .document import DocumentError, PartDocument
from .features import encode_shape

# size: (pitch, hex across flats s, hex head height k)            ISO 4017 hex head bolt
HEX_BOLT = {"M3": (0.5, 5.5, 2.0), "M4": (0.7, 7.0, 2.8), "M5": (0.8, 8.0, 3.5), "M6": (1.0, 10.0, 4.0),
            "M8": (1.25, 13.0, 5.3), "M10": (1.5, 16.0, 6.4), "M12": (1.75, 18.0, 7.5), "M16": (2.0, 24.0, 10.0),
            "M20": (2.5, 30.0, 12.5), "M24": (3.0, 36.0, 15.0)}
# size: (head diameter dk, head height k, hex socket s)             ISO 4762 socket head cap screw
SOCKET_CAP = {"M3": (5.5, 3.0, 2.5), "M4": (7.0, 4.0, 3.0), "M5": (8.5, 5.0, 4.0), "M6": (10.0, 6.0, 5.0),
              "M8": (13.0, 8.0, 6.0), "M10": (16.0, 10.0, 8.0), "M12": (18.0, 12.0, 10.0), "M16": (24.0, 16.0, 14.0),
              "M20": (30.0, 20.0, 17.0), "M24": (36.0, 24.0, 19.0)}
# size: nut height m                                               ISO 4032 hex nut (across flats as bolt)
HEX_NUT = {"M3": 2.4, "M4": 3.2, "M5": 4.7, "M6": 5.2, "M8": 6.8, "M10": 8.4, "M12": 10.8, "M16": 14.8,
           "M20": 18.0, "M24": 21.5}
# size: (inner d1, outer d2, thickness h)                          ISO 7089 plain washer
WASHER = {"M3": (3.2, 7.0, 0.5), "M4": (4.3, 9.0, 0.8), "M5": (5.3, 10.0, 1.0), "M6": (6.4, 12.0, 1.6),
          "M8": (8.4, 16.0, 1.6), "M10": (10.5, 20.0, 2.0), "M12": (13.0, 24.0, 2.5), "M16": (17.0, 30.0, 3.0),
          "M20": (21.0, 37.0, 3.0), "M24": (25.0, 44.0, 4.0)}
STANDARD = {"hex_bolt": "ISO 4017", "socket_cap_screw": "ISO 4762", "hex_nut": "ISO 4032", "washer": "ISO 7089"}


def catalog() -> dict:
    return {"hex_bolt": {"standard": "ISO 4017", "sizes": list(HEX_BOLT), "needs_length": True},
            "socket_cap_screw": {"standard": "ISO 4762", "sizes": list(SOCKET_CAP), "needs_length": True},
            "hex_nut": {"standard": "ISO 4032", "sizes": list(HEX_NUT), "needs_length": False},
            "washer": {"standard": "ISO 7089", "sizes": list(WASHER), "needs_length": False}}


def _hex_prism(across_flats: float, height: float):
    return extrude(RegularPolygon(across_flats / 2 / math.cos(math.pi / 6), 6), height)


def make_fastener(kind: str, size: str, length: float | None = None) -> PartDocument:
    size = size.upper()
    d = float(size[1:]) if size.startswith("M") else 0.0
    if kind in ("hex_bolt", "socket_cap_screw"):
        table = HEX_BOLT if kind == "hex_bolt" else SOCKET_CAP
        if size not in table:
            raise DocumentError(f"{STANDARD[kind]} sizes: {', '.join(table)}")
        if not length or length <= 0:
            raise DocumentError("give the screw length (mm, under the head)")
        shank = Pos(0, 0, -length / 2) * Cylinder(d / 2, length)
        if kind == "hex_bolt":
            _, s, k = HEX_BOLT[size]
            shape = shank + _hex_prism(s, k)
        else:
            dk, k, s = SOCKET_CAP[size]
            head = Pos(0, 0, k / 2) * Cylinder(dk / 2, k)
            socket = Location((0, 0, k - 0.6 * k)) * _hex_prism(s, 0.6 * k + 0.01)
            shape = shank + head - socket
        title = f"{STANDARD[kind]} {size}x{length:g}"
    elif kind == "hex_nut":
        if size not in HEX_NUT:
            raise DocumentError(f"ISO 4032 sizes: {', '.join(HEX_NUT)}")
        m, s = HEX_NUT[size], HEX_BOLT[size][1]
        shape = _hex_prism(s, m) - Pos(0, 0, m / 2) * Cylinder(d / 2, m + 1)
        title = f"ISO 4032 {size}"
    elif kind == "washer":
        if size not in WASHER:
            raise DocumentError(f"ISO 7089 sizes: {', '.join(WASHER)}")
        d1, d2, h = WASHER[size]
        shape = Pos(0, 0, h / 2) * Cylinder(d2 / 2, h) - Pos(0, 0, h / 2) * Cylinder(d1 / 2, h + 1)
        title = f"ISO 7089 {size}"
    else:
        raise DocumentError(f"unknown fastener {kind!r}; expected one of {sorted(STANDARD)}")
    doc = PartDocument(title.replace(" ", "_"))
    doc.add_feature("import", {"brep": encode_shape(shape), "merge": True}, name="Fastener")
    doc.set_material("Plain Carbon Steel")
    doc.properties = {"Description": title, "PartNo": title, "Standard": STANDARD[kind]}
    doc._undo.clear()
    return doc
