"""Configurations of SOLIDWORKS parts (import_sw_configurations).

No SOLIDWORKS file is in the repository, so this runs against the sample parts of a local SOLIDWORKS
installation and is skipped where there is none (set OPENSWX_SW_SAMPLES to the folder to point it elsewhere).
"""
import os
from pathlib import Path

import pytest

from openswx import session as S
from openswx.exchange.sldprt import configurations

SAMPLES = Path(os.environ.get("OPENSWX_SW_SAMPLES") or "C:/Users/Public/Documents/SOLIDWORKS")


def multi_config_parts(limit=3):
    if not SAMPLES.is_dir():
        return []
    out = []
    files = sorted((p for p in SAMPLES.rglob("*") if p.suffix.lower() == ".sldprt"), key=lambda p: p.stat().st_size)
    for p in files[:400]:  # (the small ones: quick to read)
        try:
            if len(configurations(str(p))) >= 2:
                out.append(p)
        except Exception:  # noqa: BLE001 - a file the container reader does not take is not this test's business
            continue
        if len(out) >= limit:
            break
    return out


PARTS = multi_config_parts()


@pytest.mark.skipif(not PARTS, reason="no SOLIDWORKS sample parts with several configurations on this machine")
@pytest.mark.parametrize("path", PARTS, ids=lambda p: p.name)
def test_other_configurations_come_in_as_bodies(path):
    cfgs = configurations(str(path))
    assert sum(1 for c in cfgs if c["active"]) == 1
    S.open_part(str(path))
    v0, n0 = S.get_mass_properties()["volume"], len(S.get_model()["features"])
    out = S.import_sw_configurations(str(path))
    assert len(out["imported"]) + len(out["failed"]) == len(cfgs) - 1 and out["imported"]
    assert S.get_mass_properties()["volume"] == pytest.approx(v0)  # the configuration the file was saved in is untouched
    for c in out["imported"]:
        S.activate_configuration(c["name"])
        assert S.get_mass_properties()["volume"] == pytest.approx(c["volume"], rel=1e-6)
        assert not [f for f in S.get_model()["features"] if f["status"] == "error"]
    S.activate_configuration("Default")
    assert S.get_mass_properties()["volume"] == pytest.approx(v0)
    S.undo()  # one step takes every imported configuration back
    assert len(S.get_model()["features"]) == n0 and list(S.APP.doc.configurations) == ["Default"]


def test_refusals(tmp_path):
    S.new_part("NotSw")
    with pytest.raises(S.CommandError, match="not a SOLIDWORKS part"):
        S.import_sw_configurations(str(tmp_path / "missing.sldprt"))
    other = tmp_path / "x.step"
    other.write_text("ISO-10303-21;")
    with pytest.raises(S.CommandError, match="not a SOLIDWORKS part"):
        S.import_sw_configurations(str(other))
