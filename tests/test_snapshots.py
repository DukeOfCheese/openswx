"""Undo snapshots share what did not change (openswx/snapshots.py)."""
import gc
import json

from openswx import session as S
from openswx import snapshots


def test_pack_round_trip_and_release():
    gc.collect()
    base = snapshots.stored_bytes()
    big = "x" * 50_000
    d = {"a": 1, "features": [{"brep": big, "small": "y"}, {"nested": {"again": big}}], "t": (1, 2)}
    s1, s2 = snapshots.pack(d), snapshots.pack({**d, "a": 2})
    assert snapshots.unpack(s1) == json.loads(json.dumps(d))
    assert snapshots.unpack(s2)["a"] == 2
    assert snapshots.stored_bytes() - base == 50_000  # one copy for four uses
    assert len(s1) < 500
    del s1
    gc.collect()
    assert snapshots.stored_bytes() - base == 50_000
    del s2
    gc.collect()
    assert snapshots.stored_bytes() == base  # released with the last snapshot


def test_undo_of_a_part_with_an_imported_body_does_not_copy_the_body(tmp_path):
    S.new_part("Src")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.sketch_polygon(sk, [0, 0], 8, sides=12)
    S.extrude(sk, 30)
    S.export_file(str(tmp_path / "src.step"))
    S.close_document("Src")
    gc.collect()
    S.open_part(str(tmp_path / "src.step"))
    doc = S.APP.doc
    size = len(json.dumps(doc.to_dict()))
    assert size > 8_000  # the imported body is embedded in the document
    base = snapshots.stored_bytes()
    v0 = doc.body.volume
    for i in range(40):  # forty edits: forty undo steps
        S.set_custom_property("Rev", str(i))
    assert len(doc._undo) >= 40
    held = snapshots.stored_bytes() - base + sum(len(s) for s in doc._undo)
    naive = len(doc._undo) * size  # what whole copies per step cost
    assert held < naive / 4, f"undo holds {held} characters; whole copies would be {naive}"
    assert snapshots.stored_bytes() - base < 1.5 * size  # the body itself is held once
    for _ in range(40):
        S.undo()
    assert "Rev" not in doc.properties and abs(doc.body.volume - v0) < 1e-9
    S.redo()
    assert doc.properties["Rev"] == "0"
