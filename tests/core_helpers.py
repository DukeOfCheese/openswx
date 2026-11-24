import math
from pathlib import Path
import os

import pytest

from openswx import session as S
from openswx.core.geom import STD_PLANES
from openswx.core.sketch import Sketch

PI = math.pi


def vol():
    return S.get_mass_properties()["volume"]


def top_face(y=None):
    faces = [f for f in S.get_topology()["faces"] if f["normal"][1] > 0.99]
    if y is not None:
        faces = [f for f in faces if abs(f["point"][1] - y) < 1e-6]
    return max(faces, key=lambda f: f["point"][1])["id"]


def block(w=100, d=60, h=20):
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-w / 2, -d / 2], [w / 2, d / 2])
    S.extrude(sk, depth=h)
    return sk


# ---------------------------------------------------------------- phase 2 features
def face_at_y(y):
    return [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99 and abs(f["point"][1] - y) < 1e-6][0]


def _cyl_part(name, r, depth, box=None):
    S.new_part(name)
    sk = S.create_sketch("Front")["sketch"]
    if box:
        S.sketch_rectangle(sk, [-box, -box], [box, box])
    S.sketch_circle(sk, [0, 0], r)
    S.extrude(sk, depth=depth)


class _FakeReg:
    """An in-memory stand-in for winreg (HKEY_CURRENT_USER only), to test file associations safely."""
    HKEY_CURRENT_USER, REG_SZ, KEY_ALL_ACCESS = "HKCU", 1, 0xF003F

    def __init__(self):
        self.keys = {}  # path -> {value name: data}

    class _K:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, root, path):  # noqa: N802 (winreg names)
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        return self._K(self, path)

    def OpenKey(self, root, path, res=0, access=0):  # noqa: N802
        if path not in self.keys:
            raise OSError(path)
        return self._K(self, path)

    def SetValueEx(self, k, name, res, typ, data):  # noqa: N802
        self.keys[k.path][name] = data

    def QueryValueEx(self, k, name):  # noqa: N802
        if name not in self.keys[k.path]:
            raise OSError(name)
        return self.keys[k.path][name], self.REG_SZ

    def DeleteValue(self, k, name):  # noqa: N802
        if name not in self.keys[k.path]:
            raise OSError(name)
        del self.keys[k.path][name]

    def EnumKey(self, k, i):  # noqa: N802
        subs = sorted({p[len(k.path) + 1:].split("\\")[0] for p in self.keys if p.startswith(k.path + "\\")})
        if i >= len(subs):
            raise OSError("no more")
        return subs[i]

    def DeleteKey(self, root, path):  # noqa: N802
        if any(p.startswith(path + "\\") for p in self.keys):
            raise OSError("has subkeys")
        self.keys.pop(path)


def _poly_area(pts):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def _plate_with_hole():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    s2 = S.create_sketch("Top")["sketch"]
    S.sketch_circle(s2, [50, 30], 5)
    S.cut_extrude(s2, end_condition="ThroughAllBoth")


def _faces(kind=None, normal=None):
    out = []
    for f in S.get_topology()["faces"]:
        if kind and f["type"] != kind:
            continue
        if normal and not (f.get("normal") and all(abs(a - b) < 1e-6 for a, b in zip(f["normal"], normal))):
            continue
        out.append(f["id"])
    return out


SAMPLE = r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2025\samples\tutorial\advdrawings\98blade extension.sldprt"


XT_DATA = Path(__file__).parent / "data" / "xt"


def _sw_package(streams: dict) -> bytes:
    """A minimal SOLIDWORKS 2015+ style package: deflated records with nibble-swapped names."""
    import struct
    import zlib
    out = b""
    for name, data in streams.items():
        nm = bytes(((c & 0x0F) << 4) | (c >> 4) for c in name.encode())
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = z.compress(data) + z.flush()
        out += b"\0\0\0\0\x14\x00\x06\x00\x08\x00" + struct.pack("<5I", 0, zlib.crc32(data), len(payload), len(data), len(nm)) + nm + payload
    return out


SW_DATA = Path(__file__).parent / "data" / "sw"


def _sw_part(tmp_path, name: str) -> str:
    """A .sldprt holding a SOLIDWORKS 2025 part's feature-tree and Parasolid streams (tests/data/sw)."""
    f = tmp_path / f"{name}.SLDPRT"
    f.write_bytes(_sw_package({"Contents/Config-0-ResolvedFeatures": (SW_DATA / f"{name}.features").read_bytes(),
                               "Contents/Config-0-Partition": (SW_DATA / f"{name}.partition").read_bytes()}))
    return str(f)



def _base_and_pin():
    S.new_part("BaseM")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -30], [50, 30])
    S.extrude(sk, depth=20)
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    S.connection_point([0, 20, 0], "Y", name="Socket")
    hole = max((f for f in S.get_topology()["faces"] if f["type"] == "cylinder"), key=lambda f: f["area"])["id"]
    S.mate_reference(hole, "concentric", reference="PinFit")
    S.new_part("PinM")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=40)
    S.connection_point([0, 0, 0], "-Y", name="Plug")
    cyl = next(f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder")
    S.mate_reference(cyl, "concentric", reference="PinFit")
    S.new_assembly("AsmM")
    S.insert_component("BaseM")
    S.insert_component("PinM", position=[60, 50, 30])



# ---------------------------------------------------------------- analysis: nonlinear, dynamics, drop, fatigue, flow
def _cantilever():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.simulation_add_fixture(root)
    return root, tip


def _pocket_block():
    S.new_part("Block")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 40])
    S.extrude(sk, depth=20)
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [15, 10], [45, 30])
    S.cut_extrude(sk, depth=8, reverse=True)
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [6, 20], 3)
    S.sketch_circle(sk, [54, 20], 3)
    S.cut_extrude(sk, end_condition="Through All", reverse=True)


# every name here, private helpers included, is shared by the test files that `from core_helpers import *`
__all__ = [n for n in dir() if not n.startswith("__")]
