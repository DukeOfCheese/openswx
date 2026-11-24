"""Binary transport for the viewport mesh: a versioned JSON header plus little-endian typed-array buffers."""
from __future__ import annotations

import array
import json
import math
import struct
import sys

MAGIC = b"OSWXMESH"
VERSION = 1
BULK = 8  # a triangle list (PACKED_KEYS) this long or longer travels as a buffer
F32, U32 = 0, 1
_MARK = "$buf"
PACKED_GROUPS = ("faces", "surfaces", "context")  # the mesh's lists of triangle meshes
PACKED_KEYS = ("v", "t", "n")  # vertex positions, triangle indices, normals


def encode_mesh(mesh: dict) -> bytes:
    """Pack a get_mesh() result: magic, version, header length, JSON header with {"$buf": i} markers, then buffers."""
    buffers: list[bytes] = []
    table: list[list[int]] = []

    def pack(values: list) -> dict | None:
        if all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**32 for v in values):
            arr, kind = array.array("I", values), U32
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 3.4e38
                 for v in values):
            arr, kind = array.array("f", values), F32
        else:
            return None
        if arr.itemsize != 4:  # (no supported build has another size: keep the values as JSON)
            return None
        if sys.byteorder != "little":
            arr.byteswap()
        buffers.append(arr.tobytes())
        table.append([kind, len(values)])
        return {_MARK: len(buffers) - 1}

    def walk(value):
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    def triangles(item):
        """A face / surface / context item: its vertex and index lists go in buffers, everything else as JSON."""
        if not isinstance(item, dict):
            return walk(item)
        out = {}
        for k, v in item.items():
            packed = pack(v) if k in PACKED_KEYS and isinstance(v, list) and len(v) >= BULK else None
            out[k] = packed if packed is not None else walk(v)
        return out

    # Only the triangle data is packed. Everything else (edge polylines, sketch geometry, reference curves,
    # planes) stays JSON at full precision: the UI sends picked points back as command arguments and treats
    # them as plain arrays.
    doc = {k: ([triangles(i) for i in v] if k in PACKED_GROUPS and isinstance(v, list) else walk(v))
           for k, v in mesh.items()}
    header = json.dumps({"v": VERSION, "mesh": doc, "buf": table}, separators=(",", ":"),
                        ensure_ascii=False).encode("utf-8")
    header += b" " * (-len(header) % 4)  # (the buffers start 4-byte aligned, for Float32Array views)
    return b"".join([MAGIC, struct.pack("<II", VERSION, len(header)), header, *buffers])


def decode_mesh(data: bytes) -> dict:
    """The mesh object of an encode_mesh() document, with the buffers back as plain lists."""
    if len(data) < 16 or data[:8] != MAGIC:
        raise ValueError("not an openswx mesh document")
    version, header_length = struct.unpack_from("<II", data, 8)
    if version != VERSION:
        raise ValueError(f"mesh version {version} is not supported (this is version {VERSION})")
    header = json.loads(data[16:16 + header_length])
    offset, arrays = 16 + header_length, []
    for kind, count in header["buf"]:
        arr = array.array("f" if kind == F32 else "I")
        arr.frombytes(data[offset:offset + 4 * count])
        if sys.byteorder != "little":
            arr.byteswap()
        arrays.append(arr.tolist())
        offset += 4 * count

    def walk(value):
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            if set(value) == {_MARK}:
                return arrays[value[_MARK]]
            return {k: walk(v) for k, v in value.items()}
        return value

    return walk(header["mesh"])
