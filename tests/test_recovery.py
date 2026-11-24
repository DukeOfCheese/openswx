"""Autosave and Document Recovery (openswx/system/recovery.py)."""
import json
import math
import os
import shutil

from openswx.system import recovery
from openswx import session as S


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    recovery.clean_exit()
    for name in list(S.APP.documents):
        S.close_document(name)


def test_autosave_and_recover_after_a_crash(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    S.new_part("Crashy")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 10)
    S.extrude(sk, 5)
    assert recovery.save_now(S.APP) == ["Crashy"]
    assert recovery.save_now(S.APP) == []  # unchanged: not written again
    assert S.list_recovered()["recovered"] == []  # this session is alive: nothing to recover from it
    d = recovery.session_dir()
    assert json.loads((d / "Crashy.oswprt").read_text())["features"]

    # the process dies: its folder stays, under a process id that is not running any more
    dead = d.with_name("999999999-1")
    shutil.copytree(d, dead)
    recovery.clean_exit()
    assert not d.exists()
    S.close_document("Crashy")
    found = S.list_recovered()["recovered"]
    assert [e["name"] for e in found] == ["Crashy"] and found[0]["path"] is None
    r = S.recover_document(found[0]["id"])
    assert r["recovered"] == "Crashy"
    doc = S.APP.active_doc
    assert doc.dirty and abs(doc.body.volume - math.pi * 100 * 5) < 1e-6
    assert S.list_recovered()["recovered"] == [] and not dead.exists()
    recovery.clean_exit()


def test_saved_documents_leave_no_recovery_copy_and_discard(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    S.new_part("Kept")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 4)
    S.extrude(sk, 2)
    recovery.save_now(S.APP)
    d = recovery.session_dir()
    assert (d / "Kept.oswprt").exists()
    S.save_part(str(tmp_path / "Kept.oswprt"))
    recovery.save_now(S.APP)
    assert not (d / "Kept.oswprt").exists()  # saved: nothing to recover

    assert not S.APP.active_doc.dirty
    S.set_material("Plain Carbon Steel")  # (sets a property only: still counts as an unsaved change)
    assert S.APP.active_doc.dirty
    recovery.save_now(S.APP)
    dead = d.with_name("999999998-1")
    shutil.copytree(d, dead)
    recovery.clean_exit()
    found = S.list_recovered()["recovered"]
    assert len(found) == 1 and found[0]["path"].endswith("Kept.oswprt")
    assert S.discard_recovered()["discarded"] == 1
    assert S.list_recovered()["recovered"] == [] and not dead.exists()


def test_a_running_session_is_not_offered(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    other = recovery.root() / f"{os.getpid()}-1"  # a folder of a live process (this one) with a fresh heartbeat
    other.mkdir(parents=True)
    (other / recovery.HEARTBEAT).write_text("x")
    (other / "P.json").write_text(json.dumps({"name": "P", "kind": "part", "path": None, "saved": 1.0}))
    (other / "P.oswprt").write_text("{}")
    assert recovery.recovered() == []
    old = other.stat().st_mtime - 10 * recovery.STALE
    os.utime(other / recovery.HEARTBEAT, (old, old))  # heartbeat long stale: the process id was reused
    assert [e["name"] for e in recovery.recovered()] == ["P"]
