"""Hardware library (Toolbox-style): ISO metric and ANSI inch fasteners and pins generated from standard
dimension tables.

Geometry is simplified the way CAD fastener libraries usually are: nominal-diameter shank, no modelled
thread, plain hex/cylindrical heads. Screws point down -Z with the underside of the head on the XY plane
(countersunk screws: the top of the head); nuts, washers and pins sit on the XY plane centred on the Z axis.
"""
from __future__ import annotations

import math

from build123d import Cone, Cylinder, Location, Pos, RegularPolygon, Sphere, extrude

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
# size: (head diameter dk, head height k, hex socket s)             ISO 7380 button head screw
BUTTON_HEAD = {"M3": (5.7, 1.65, 2.0), "M4": (7.6, 2.2, 2.5), "M5": (9.5, 2.75, 3.0), "M6": (10.5, 3.3, 4.0),
               "M8": (14.0, 4.4, 5.0), "M10": (17.5, 5.5, 6.0), "M12": (21.0, 6.6, 8.0), "M16": (28.0, 8.8, 10.0)}
# size: (head diameter dk, hex socket s)                            ISO 10642 countersunk (90 deg) socket screw
COUNTERSUNK = {"M3": (6.72, 2.0), "M4": (8.96, 2.5), "M5": (11.2, 3.0), "M6": (13.44, 4.0), "M8": (17.92, 5.0),
               "M10": (22.4, 6.0), "M12": (26.88, 8.0), "M16": (33.6, 10.0), "M20": (40.32, 12.0)}
# size: hex socket s                                                ISO 4026 set screw, flat point
SET_SCREW = {"M3": 1.5, "M4": 2.0, "M5": 2.5, "M6": 3.0, "M8": 4.0, "M10": 5.0, "M12": 6.0, "M16": 8.0,
             "M20": 10.0, "M24": 12.0}
# size: nut height m                                               ISO 4032 hex nut (across flats as bolt)
HEX_NUT = {"M3": 2.4, "M4": 3.2, "M5": 4.7, "M6": 5.2, "M8": 6.8, "M10": 8.4, "M12": 10.8, "M16": 14.8,
           "M20": 18.0, "M24": 21.5}
# size: nut height m                                               ISO 4035 thin hex nut
THIN_NUT = {"M3": 1.8, "M4": 2.2, "M5": 2.7, "M6": 3.2, "M8": 4.0, "M10": 5.0, "M12": 6.0, "M16": 8.0,
            "M20": 10.0, "M24": 12.0}
# size: (inner d1, outer d2, thickness h)                          ISO 7089 plain washer
WASHER = {"M3": (3.2, 7.0, 0.5), "M4": (4.3, 9.0, 0.8), "M5": (5.3, 10.0, 1.0), "M6": (6.4, 12.0, 1.6),
          "M8": (8.4, 16.0, 1.6), "M10": (10.5, 20.0, 2.0), "M12": (13.0, 24.0, 2.5), "M16": (17.0, 30.0, 3.0),
          "M20": (21.0, 37.0, 3.0), "M24": (25.0, 44.0, 4.0)}
# size: (inner d1, outer d2, thickness s)                          DIN 127 B spring lock washer
LOCK_WASHER = {"M3": (3.1, 6.2, 0.8), "M4": (4.1, 7.6, 0.9), "M5": (5.1, 9.2, 1.2), "M6": (6.1, 11.8, 1.6),
               "M8": (8.2, 14.8, 2.0), "M10": (10.2, 18.1, 2.2), "M12": (12.2, 21.1, 2.5), "M16": (16.5, 27.4, 3.5),
               "M20": (20.5, 33.6, 4.0), "M24": (24.5, 40.0, 5.0)}
DOWEL_PIN = ["2", "3", "4", "5", "6", "8", "10", "12", "16", "20"]  # ISO 8734 nominal diameters (m6)

IN = 25.4
# ANSI inch sizes: nominal diameter (in)
ANSI_DIA = {"#4-40": 0.112, "#6-32": 0.138, "#8-32": 0.164, "#10-24": 0.190, "1/4-20": 0.25, "5/16-18": 0.3125,
            "3/8-16": 0.375, "7/16-14": 0.4375, "1/2-13": 0.5, "5/8-11": 0.625, "3/4-10": 0.75, "1-8": 1.0}
# size: (across flats F, head height H) in                          ASME B18.2.1 hex cap screw
ANSI_HEX_BOLT = {"1/4-20": (0.4375, 0.156), "5/16-18": (0.5, 0.203), "3/8-16": (0.5625, 0.234),
                 "7/16-14": (0.625, 0.281), "1/2-13": (0.75, 0.3125), "5/8-11": (0.9375, 0.391),
                 "3/4-10": (1.125, 0.469), "1-8": (1.5, 0.609)}
# size: (head diameter A, head height H, hex socket J) in            ASME B18.3 socket head cap screw
ANSI_SOCKET_CAP = {"#4-40": (0.183, 0.112, 0.09375), "#6-32": (0.226, 0.138, 0.109375), "#8-32": (0.270, 0.164, 0.140625),
                   "#10-24": (0.312, 0.190, 0.15625), "1/4-20": (0.375, 0.25, 0.1875), "5/16-18": (0.469, 0.3125, 0.25),
                   "3/8-16": (0.5625, 0.375, 0.3125), "1/2-13": (0.75, 0.5, 0.375), "5/8-11": (0.9375, 0.625, 0.5),
                   "3/4-10": (1.125, 0.75, 0.625)}
# size: (across flats F, height H) in                               ASME B18.2.2 hex nut
ANSI_HEX_NUT = {"1/4-20": (0.4375, 0.21875), "5/16-18": (0.5, 0.265625), "3/8-16": (0.5625, 0.328125),
                "7/16-14": (0.6875, 0.375), "1/2-13": (0.75, 0.4375), "5/8-11": (0.9375, 0.546875),
                "3/4-10": (1.125, 0.640625), "1-8": (1.5, 0.859375)}
# size: (inner, outer, thickness) in                                ANSI B18.22.1 type A plain washer (SAE)
ANSI_WASHER = {"1/4-20": (0.281, 0.625, 0.065), "5/16-18": (0.344, 0.688, 0.065), "3/8-16": (0.406, 0.812, 0.065),
               "7/16-14": (0.469, 0.922, 0.065), "1/2-13": (0.531, 1.062, 0.095), "5/8-11": (0.656, 1.312, 0.095),
               "3/4-10": (0.812, 1.469, 0.134), "1-8": (1.062, 2.0, 0.134)}

# kind: (standard, family, sizes, needs length)
KINDS = {
    "hex_bolt": ("ISO 4017", "ISO", list(HEX_BOLT), True),
    "socket_cap_screw": ("ISO 4762", "ISO", list(SOCKET_CAP), True),
    "button_head_screw": ("ISO 7380", "ISO", list(BUTTON_HEAD), True),
    "countersunk_screw": ("ISO 10642", "ISO", list(COUNTERSUNK), True),
    "set_screw": ("ISO 4026", "ISO", list(SET_SCREW), True),
    "hex_nut": ("ISO 4032", "ISO", list(HEX_NUT), False),
    "thin_nut": ("ISO 4035", "ISO", list(THIN_NUT), False),
    "washer": ("ISO 7089", "ISO", list(WASHER), False),
    "lock_washer": ("DIN 127 B", "ISO", list(LOCK_WASHER), False),
    "dowel_pin": ("ISO 8734", "ISO", DOWEL_PIN, True),
    "ansi_hex_bolt": ("ASME B18.2.1", "ANSI Inch", list(ANSI_HEX_BOLT), True),
    "ansi_socket_cap_screw": ("ASME B18.3", "ANSI Inch", list(ANSI_SOCKET_CAP), True),
    "ansi_hex_nut": ("ASME B18.2.2", "ANSI Inch", list(ANSI_HEX_NUT), False),
    "ansi_washer": ("ANSI B18.22.1", "ANSI Inch", list(ANSI_WASHER), False),
}
STANDARD = {k: v[0] for k, v in KINDS.items()}


def catalog() -> dict:
    return {k: {"standard": std, "family": fam, "sizes": sizes, "needs_length": nl,
                "length_unit": "in" if fam == "ANSI Inch" else "mm"} for k, (std, fam, sizes, nl) in KINDS.items()}


def _hex_prism(across_flats: float, height: float):
    return extrude(RegularPolygon(across_flats / 2 / math.cos(math.pi / 6), 6), height)


def _socket(s: float, top: float, depth: float):
    return Location((0, 0, top - depth)) * _hex_prism(s, depth + 0.01)


def _ring(d1: float, d2: float, h: float):
    return Pos(0, 0, h / 2) * Cylinder(d2 / 2, h) - Pos(0, 0, h / 2) * Cylinder(d1 / 2, h + 1)


def _shank(d: float, length: float):
    return Pos(0, 0, -length / 2) * Cylinder(d / 2, length)


def make_fastener(kind: str, size: str, length: float | None = None) -> PartDocument:
    """A fastener part. Metric lengths are in mm; ANSI lengths are in inches."""
    if kind not in KINDS:
        raise DocumentError(f"unknown fastener {kind!r}; expected one of {sorted(KINDS)}")
    std, fam, sizes, needs_length = KINDS[kind]
    size = size.upper() if fam == "ISO" and kind != "dowel_pin" else size
    if size not in sizes:
        raise DocumentError(f"{std} sizes: {', '.join(sizes)}")
    if needs_length and (not length or length <= 0):
        raise DocumentError("give the length (" + ("in" if fam == "ANSI Inch" else "mm") + ")")
    if fam == "ANSI Inch":
        d = ANSI_DIA[size] * IN
        L = (length or 0) * IN
        if kind == "ansi_hex_bolt":
            F, H = ANSI_HEX_BOLT[size]
            shape = _shank(d, L) + _hex_prism(F * IN, H * IN)
        elif kind == "ansi_socket_cap_screw":
            A, H, J = ANSI_SOCKET_CAP[size]
            shape = _shank(d, L) + Pos(0, 0, H * IN / 2) * Cylinder(A * IN / 2, H * IN) - _socket(J * IN, H * IN, 0.6 * H * IN)
        elif kind == "ansi_hex_nut":
            F, H = ANSI_HEX_NUT[size]
            shape = _hex_prism(F * IN, H * IN) - Pos(0, 0, H * IN / 2) * Cylinder(d / 2, H * IN + 1)
        else:
            a, b, t = ANSI_WASHER[size]
            shape = _ring(a * IN, b * IN, t * IN)
        title = f"{std} {size}" + (f"x{length:g}in" if needs_length else "")
    else:
        d = float(size[1:]) if size.startswith("M") else float(size)
        if kind == "hex_bolt":
            _, s, k = HEX_BOLT[size]
            shape = _shank(d, length) + _hex_prism(s, k)
        elif kind == "socket_cap_screw":
            dk, k, s = SOCKET_CAP[size]
            shape = _shank(d, length) + Pos(0, 0, k / 2) * Cylinder(dk / 2, k) - _socket(s, k, 0.6 * k)
        elif kind == "button_head_screw":
            dk, k, s = BUTTON_HEAD[size]
            # a spherical cap of height k on a dk circle
            R = (dk * dk / 4 + k * k) / (2 * k)
            dome = Pos(0, 0, k - R) * Sphere(R) & Pos(0, 0, k / 2) * Cylinder(dk / 2, k)
            shape = _shank(d, length) + dome - _socket(s, k, 0.6 * k)
        elif kind == "countersunk_screw":
            dk, s = COUNTERSUNK[size]
            k = (dk - d) / 2  # 90 deg head
            head = Pos(0, 0, -k / 2) * Cone(d / 2, dk / 2, k)
            shape = _shank(d, length - k).moved(Location((0, 0, -k))) + head - _socket(s, 0, 0.6 * k)
        elif kind == "set_screw":
            s = SET_SCREW[size]
            shape = _shank(d, length) - _socket(s, 0, min(0.5 * length, 1.2 * s))
        elif kind in ("hex_nut", "thin_nut"):
            m, s = (HEX_NUT if kind == "hex_nut" else THIN_NUT)[size], HEX_BOLT[size][1]
            shape = _hex_prism(s, m) - Pos(0, 0, m / 2) * Cylinder(d / 2, m + 1)
        elif kind in ("washer", "lock_washer"):
            shape = _ring(*(WASHER if kind == "washer" else LOCK_WASHER)[size])
        else:  # dowel pin, on the XY plane
            shape = Pos(0, 0, length / 2) * Cylinder(d / 2, length)
        title = f"{std} {size}" + (f"x{length:g}" if needs_length else "")
    doc = PartDocument(title.replace(" ", "_").replace("/", "-").replace("#", "No"))
    doc.add_feature("import", {"brep": encode_shape(shape), "merge": True}, name="Fastener")
    doc.set_material("Plain Carbon Steel")
    doc.properties = {"Description": title, "PartNo": title, "Standard": std}
    doc._undo.clear()
    return doc
