"""The binary mesh document: /api/mesh.bin carries exactly the mesh of /api/mesh, with typed-array buffers."""
import json
import math
import struct

import pytest
from fastapi.testclient import TestClient

from openswx import session as S
from openswx.server.mesh_binary import MAGIC, VERSION, decode_mesh, encode_mesh
from openswx.server.web_server import create_app


def _part():
    """A part with fillets, a cut hole, sketches, reference geometry and a coloured face."""
    S.new_part("BinaryMesh")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [80, 50])
    S.extrude(sk, 20)
    S.fillet(6, edges=["Edge0"])
    hole = S.create_sketch("Front")["sketch"]
    S.sketch_circle(hole, [40, 10], 7)
    S.cut_extrude(hole, end_condition="ThroughAll")
    S.reference_axis(planes=["Front", "Top"])
    S.reference_point(coords=[5, 5, 5])
    S.coordinate_system(origin=[0, 0, 0], x_axis=[1, 0, 0], y_axis=[0, 1, 0])
    S.curve_through_points([[0, 0, 25], [20, 10, 25], [40, 0, 25]])
    S.set_appearance("#ff8800", "face", face="Face0")
    return S.get_mesh()


def _same(decoded, original, where="mesh"):
    if isinstance(original, list):
        assert isinstance(decoded, list), where
        assert len(decoded) == len(original), f"{where}: {len(decoded)} != {len(original)}"
        for i, (a, b) in enumerate(zip(decoded, original)):
            _same(a, b, f"{where}[{i}]")
    elif isinstance(original, dict):
        assert isinstance(decoded, dict) and set(decoded) == set(original), where
        for key in original:
            _same(decoded[key], original[key], f"{where}.{key}")
    elif isinstance(original, str) or original is None or isinstance(original, bool):
        assert decoded == original, where
    else:  # a number: positions are float32, indices stay exact
        assert isinstance(decoded, (int, float)), where
        assert math.isclose(decoded, original, rel_tol=1e-6, abs_tol=1e-4), f"{where}: {decoded} != {original}"


def test_binary_document_matches_get_mesh():
    mesh = _part()
    assert mesh["faces"] and mesh["edges"] and len(mesh["sketches"]) >= 2 and mesh["planes"]
    assert any(f.get("color") for f in mesh["faces"]) and any(r["kind"] == "curve" for r in mesh["refs"])

    data = encode_mesh(mesh)
    assert data[:8] == MAGIC and struct.unpack_from("<I", data, 8)[0] == VERSION
    back = decode_mesh(data)
    _same(back, mesh)

    header_length = struct.unpack_from("<I", data, 12)[0]
    header = json.loads(data[16:16 + header_length])
    assert header["buf"] and set(back) == set(mesh)


def test_a_damaged_document_is_rejected():
    with pytest.raises(ValueError):
        decode_mesh(b"this is not a mesh document, just some bytes")
    S.new_part("Empty")
    data = bytearray(encode_mesh(S.get_mesh()))
    data[8:12] = struct.pack("<I", 99)
    with pytest.raises(ValueError, match="version"):
        decode_mesh(bytes(data))


def test_mesh_bin_route_serves_the_same_mesh():
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as c:
        json_mesh = _part()
        r = c.get("/api/mesh.bin")
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
        assert r.content[:8] == MAGIC
        _same(decode_mesh(r.content), json_mesh)
        # the existing JSON route is unchanged, and both are gzipped on the wire
        assert c.get("/api/mesh").json()["faces"]
        assert r.headers.get("content-encoding") == "gzip"
        assert c.get("/api/mesh", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"


def test_mesh_bin_route_carries_assembly_faces():
    _part()
    part = S.get_model()["name"]
    S.new_assembly("BinaryAsm")
    S.insert_component(part)
    S.insert_component(part, position=[100, 0, 0])
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as c:
        back = decode_mesh(c.get("/api/mesh.bin").content)
        _same(back, c.get("/api/mesh").json())
        assert {f["component"] for f in back["faces"]} == {f"{part}-1", f"{part}-2"}
        assert all("/" in f["id"] for f in back["faces"])


def test_binary_payload_is_smaller_than_json():
    mesh = _part()
    raw = json.dumps(mesh, separators=(",", ":")).encode("utf-8")
    binary = encode_mesh(mesh)
    assert len(binary) < len(raw)


def test_only_triangle_data_travels_as_buffers():
    """Edge polylines and sketch geometry stay JSON number lists at full precision: the UI sends a picked vertex
    (edges[].p) back as a command argument and flattens sketch points (entities[].p) for the extrude handle, which
    a typed array breaks (it serialises as {"0": .., "1": ..} and does not flatten). Only the triangle data of
    faces, surfaces and context goes in buffers."""
    _part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [12.3, 4.56], 7.89)
    S.sketch_arc(sk, [0, 0], [10.1, 0], [0, 10.1]) if hasattr(S, "sketch_arc") else None
    mesh = S.get_mesh()
    assert any(len(e["p"]) >= 8 for e in mesh["edges"])
    assert any(len(e["p"]) >= 8 for s in mesh["sketches"] for e in s["entities"] if "p" in e)
    data = encode_mesh(mesh)
    header = json.loads(data[16:16 + struct.unpack_from("<I", data, 12)[0]])

    def buffers(value, path):
        if isinstance(value, dict):
            if set(value) == {"$buf"}:
                yield path
            for k, v in value.items():
                yield from buffers(v, f"{path}.{k}")
        elif isinstance(value, list):
            for v in value:
                yield from buffers(v, f"{path}[]")
    packed = set(buffers(header["mesh"], "mesh"))
    assert packed and packed <= {"mesh.faces[].v", "mesh.faces[].t", "mesh.surfaces[].v", "mesh.surfaces[].t",
                                 "mesh.context[].v", "mesh.context[].t"}, packed
    back = decode_mesh(data)
    assert back["edges"] == mesh["edges"] and back["sketches"] == mesh["sketches"]  # (exactly: no float32 rounding)
