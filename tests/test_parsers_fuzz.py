"""Fuzz-style tests: mutated and truncated input must raise each reader's own error, not a crash."""
from __future__ import annotations

import copy
import io
import json
import random
import struct
import tracemalloc
import zipfile
import zlib
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="openswx.document builds geometry through the kernel")

from openswx.document import DocumentError, PartDocument  # noqa: E402
from openswx.parasolid import XTError, read_xt_bytes  # noqa: E402
from openswx.sldprt import RECORD, read_info, read_streams  # noqa: E402
from openswx.sw_drawing import read_drawing  # noqa: E402
from openswx.fcstd import FcstdError, shape_files  # noqa: E402

DATA = Path(__file__).parent / "data" / "xt"
SEED = 20261124  # (fixed: the case list, and so every assertion, is reproducible)

# Exceptions a damaged part document may raise: DocumentError is a ValueError, as are json's own errors.
DOC_ERRORS = (ValueError, KeyError, TypeError, IndexError)


def _rng(name: str) -> random.Random:
    return random.Random(f"{SEED}:{name}")


def _mutate(rng: random.Random, raw: bytes, edits: int) -> bytes:
    """Flip bits, drop and insert bytes, or truncate - the shapes a damaged download takes."""
    b = bytearray(raw)
    for _ in range(edits):
        op = rng.randrange(4)
        if op == 0 and b:
            b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
        elif op == 1 and len(b) > 4:
            i = rng.randrange(len(b))
            del b[i:min(len(b), i + rng.randrange(1, 40))]
        elif op == 2:
            i = rng.randrange(len(b) + 1)
            b[i:i] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 12)))
        elif b:
            del b[rng.randrange(len(b)):]
    return bytes(b)


def _sw_package(streams: dict[str, bytes]) -> bytes:
    """A minimal SOLIDWORKS 2015+ container: deflated records with nibble-swapped stream names."""
    out = b""
    for name, data in streams.items():
        nm = bytes(((c & 0x0F) << 4) | (c >> 4) for c in name.encode())
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = z.compress(data) + z.flush()
        out += (b"\0\0\0\0" + RECORD
                + struct.pack("<5I", 0, zlib.crc32(data), len(payload), len(data), len(nm))
                + nm + payload)
    return out


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _u16(text: str) -> bytes:
    return b"\xff\xfe" + text.encode("utf-16-le")


def _check_xt(model) -> None:
    assert isinstance(model.nodes, dict)
    assert len(model.nodes) < 50_000  # (a damaged header must not claim an enormous node table)


def _check_sldprt(info) -> None:
    assert isinstance(info, dict)
    if "streams" in info:  # read_info
        assert isinstance(info["streams"], dict)
        assert sum(info["streams"].values()) < 64 * 1024 * 1024
        assert len(info["parasolid"]) < 50
    elif "views" in info:  # read_drawing
        assert isinstance(info["views"], list) and len(info["views"]) < 1_000
        assert isinstance(info["models"], list)
        assert isinstance(info["sheets"], list) and info["sheets"]


@pytest.mark.parametrize("name", ["block.x_t", "block.x_b"])
def test_xt_reader_survives_mutated_transmit_data(name):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"sample {name} not installed")
    seed = path.read_bytes()
    rng = _rng(f"xt:{name}")
    parsed = 0
    for _ in range(120):
        data = _mutate(rng, seed, rng.randrange(1, 7))
        try:
            model = read_xt_bytes(data)
        except Exception as exc:  # noqa: BLE001 - the point is to classify what escapes
            assert isinstance(exc, XTError), f"{type(exc).__name__}: {exc}"
        else:
            parsed += 1
            _check_xt(model)
    assert parsed > 0  # some mutations leave the header intact: the deep parser is exercised too


def test_xt_reader_rejects_text_without_header():
    with pytest.raises(XTError):
        read_xt_bytes(b"**PART1;\nnot a transmit file")
    with pytest.raises(XTError):
        read_xt_bytes(b"neither text nor binary")


def _container_seed() -> bytes:
    xt = DATA / "drafted_cylinder_partition.xtp"
    partition = struct.pack("<I", 0) + bytes(16) + zlib.compress(xt.read_bytes() if xt.exists() else b"")
    return _sw_package({
        "Contents/Config-0-Partition": partition,
        "swXmlContents/Features": (
            b"<swSolidWorks swVersion='2025'><swFile swDocType='PART'/>"
            b"<swConfiguration swID='0' swName='Default' swMostRecentConfiguration='YES'/></swSolidWorks>"),
        "Contents/Definition": _u16("C:/models/part.sldprt\0Drawing View1\0*Front\0Sheet1\0"),
        "SheetPreviews/SheetNames": _u16("Sheet1\0"),
    })


def test_sldprt_readers_survive_damaged_containers(tmp_path):
    seed = _container_seed()
    target = tmp_path / "fuzz.sldprt"
    rng = _rng("sldprt")
    tracemalloc.start()
    try:
        for i in range(120):
            if i % 5 == 0:  # random bytes with the record signature sprinkled in: the scanner's worst case
                junk = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
                at = rng.randrange(len(junk) + 1)
                data = junk[:at] + RECORD + junk[at:]
            else:
                data = _mutate(rng, seed, rng.randrange(1, 6))
            target.write_bytes(data)
            for reader in (read_info, read_drawing):
                try:
                    out = reader(str(target))
                except ValueError:  # (includes the "not a container" error; read_streams' own contract)
                    pass
                else:
                    _check_sldprt(out)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024  # tiny inputs must not inflate to a large allocation


def test_sldprt_rejects_record_that_lies_about_its_size(tmp_path):
    raw = b"\0" * 64
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = z.compress(raw) + z.flush()
    name = bytes(((c & 0x0F) << 4) | (c >> 4) for c in b"Contents/Config-0-Partition")
    bomb = (RECORD + struct.pack("<5I", 0, zlib.crc32(raw), len(payload), 0x7FFFFFFF, len(name))
            + name + payload)
    target = tmp_path / "bomb.sldprt"
    target.write_bytes(bomb)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError):  # the declared 2 GiB does not match the 64 inflated bytes
            read_streams(str(target))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_sldprt_container_reads_back_a_sane_stream_table(tmp_path):
    target = tmp_path / "good.sldprt"
    target.write_bytes(_container_seed())
    info = read_info(str(target))
    assert info["streams"]["Contents/Config-0-Partition"] > 0
    assert info["parasolid"] and info["parasolid"][0]["schema"].startswith("SCH_")


_FC_DOCUMENT = b"""<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4"><ObjectData Count="1">
 <Object name="Box"><Properties Count="1">
  <Property name="Shape" type="Part::PropertyPartShape"><Part file="PartShape.brp"/></Property>
 </Properties></Object>
</ObjectData></Document>"""


def test_fcstd_loader_survives_malformed_archives(tmp_path):
    target = tmp_path / "fuzz.FCStd"
    rng = _rng("fcstd")
    checked = 0

    def read(data: bytes):
        nonlocal checked
        target.write_bytes(data)
        try:
            picked = shape_files(str(target))
        except Exception as exc:  # noqa: BLE001 - the point is to classify what escapes
            assert isinstance(exc, FcstdError), f"{type(exc).__name__}: {exc}"
        else:
            checked += 1
            assert isinstance(picked, list) and len(picked) < 100
            assert all(isinstance(n, str) and isinstance(m, str) for n, m in picked)

    good = _zip_bytes({"Document.xml": _FC_DOCUMENT, "PartShape.brp": b"not a B-rep"})
    read(good)
    assert checked == 1  # the undeformed archive is accepted, so the mutations are compared to a real read

    for _ in range(50):  # not a zip at all
        read(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 400))))
    for _ in range(50):  # damaged zip (central directory, CRCs, compressed members)
        read(_mutate(rng, good, rng.randrange(1, 6)))
    for _ in range(40):  # a readable zip whose XML is damaged
        xml = _mutate(rng, _FC_DOCUMENT, rng.randrange(1, 4))
        read(_zip_bytes({"Document.xml": xml, "PartShape.brp": b"not a B-rep"}))
    assert checked >= 1


def test_fcstd_rejects_non_zip_and_empty_zip(tmp_path):
    target = tmp_path / "bad.FCStd"
    target.write_bytes(b"not a zip")
    with pytest.raises(FcstdError):
        shape_files(str(target))
    target.write_bytes(_zip_bytes({"Other.xml": b"<x/>"}))
    with pytest.raises(FcstdError):
        shape_files(str(target))


def test_fcstd_rejects_corrupt_member_data(tmp_path):
    data = bytearray(_zip_bytes({"Document.xml": _FC_DOCUMENT}))
    data[data.index(b"PK\x01\x02") + 8] = 0x01  # the central directory's flag word: member encrypted
    target = tmp_path / "encrypted.FCStd"
    target.write_bytes(bytes(data))
    with pytest.raises(FcstdError):
        shape_files(str(target))


def test_fcstd_skips_an_object_without_a_name(tmp_path):
    xml = _FC_DOCUMENT.replace(b"</ObjectData>", b'<Object id="1"><Properties Count="1">'
                                b'<Property name="Shape"><Part file="Other.brp"/></Property>'
                                b"</Properties></Object></ObjectData>")
    target = tmp_path / "nameless.FCStd"
    target.write_bytes(_zip_bytes({"Document.xml": xml, "PartShape.brp": b"x", "Other.brp": b"y"}))
    assert shape_files(str(target)) == [("Box", "PartShape.brp")]


def _document_seed() -> dict:
    d = PartDocument("Seed").to_dict()
    d["features"] = [{"name": "Sketch1", "type": "sketch", "suppressed": False,
                      "params": {"plane": "XY", "sketch": {"points": {}, "entities": {}, "dimensions": []}}}]
    return d


def _check_doc(doc) -> None:
    assert isinstance(doc.features, list)
    assert isinstance(doc.state, dict)
    assert len(doc.features) < 1_000


def test_part_document_open_survives_mutated_json(tmp_path):
    text = json.dumps(_document_seed()).encode()
    target = tmp_path / "fuzz.oswprt"
    rng = _rng("document-json")
    opened = 0
    for _ in range(120):
        target.write_bytes(_mutate(rng, text, rng.randrange(1, 5)))
        try:
            doc = PartDocument.open(str(target))
        except Exception as exc:  # noqa: BLE001
            assert isinstance(exc, DOC_ERRORS), f"{type(exc).__name__}: {exc}"
            assert not isinstance(exc, (MemoryError, RecursionError))
        else:
            opened += 1
            _check_doc(doc)
    assert opened > 0


def test_part_document_load_dict_survives_structural_damage():
    base = _document_seed()
    keys = list(base)
    rng = _rng("document-dict")
    loaded = 0
    for _ in range(120):
        d = copy.deepcopy(base)
        op = rng.randrange(8)
        if op == 0:
            d.pop(rng.choice(keys), None)
        elif op == 1:
            d["features"] = []
        elif op == 2:
            d["version"] = rng.choice([None, 0, -1, 999, "1", 1.5, [], {}])
        elif op == 3:
            d["features"][0].pop(rng.choice(list(d["features"][0])), None)
        elif op == 4:
            d["features"][0]["type"] = rng.choice(["nope", "", None, 3])
        elif op == 5:
            d["rollback"] = rng.choice([0, 1, -1, 99, "x", None, 1.0])
        elif op == 6:
            d["variables"] = rng.choice([{}, {"a": "1+1"}, {"a": "b"}, {"a": 5}])
        else:
            d["active_config"] = rng.choice(["Default", "nope", "", None])
        doc = PartDocument("Fuzz")
        try:
            doc.load_dict(d)
        except Exception as exc:  # noqa: BLE001
            assert isinstance(exc, DocumentError), f"{type(exc).__name__}: {exc}"
        else:
            loaded += 1
            _check_doc(doc)
    assert loaded > 0
