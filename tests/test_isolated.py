"""Foreign geometry is read in a child process with a time limit (openswx/system/isolated.py)."""
import pytest

from openswx.system import isolated
from openswx import session as S


def xt_file(tmp_path):
    S.new_part("IsoSrc")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [30, 20])
    S.extrude(sk, 5)
    p = tmp_path / "iso.x_t"
    S.export_file(str(p))
    return str(p)


def test_isolated_read_gives_the_same_body(tmp_path, monkeypatch):
    path = xt_file(tmp_path)
    shape, notes = isolated.read_geometry(path, timeout=120)
    assert shape.volume == pytest.approx(3000) and isinstance(notes, list)
    monkeypatch.setenv("OPENSWX_ISOLATE", "on")  # as the application runs
    S.open_part(path)
    assert S.get_mass_properties()["volume"] == pytest.approx(3000)


def test_a_hanging_or_crashing_reader_costs_only_that_file(tmp_path):
    path = xt_file(tmp_path)
    with pytest.raises(isolated.IsolationError, match="took longer than 3 s"):
        isolated.read_geometry(path, timeout=3, _fn=isolated._test_hang)
    with pytest.raises(isolated.IsolationError, match="crashed while reading iso.x_t"):
        isolated.read_geometry(path, timeout=60, _fn=isolated._test_crash)
    bad = tmp_path / "bad.x_t"
    bad.write_text("this is not a Parasolid file")
    with pytest.raises(ValueError):  # the reader's own refusal comes back as it was
        isolated.read_geometry(str(bad), timeout=120)
    assert S.get_mass_properties()["volume"] == pytest.approx(3000)  # the application carries on
