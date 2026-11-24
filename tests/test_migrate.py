"""Native file versions (openswx/exchange/migrate.py): old files keep opening, newer ones are refused, unknown keys survive."""
import json
import math
from pathlib import Path

import pytest

from openswx.exchange import migrate
from openswx import session as S

NATIVE = Path(__file__).parent / "data" / "native"
# every file ever released must keep opening to the same model: name -> volume (mm3)
GOLDEN = {"v1_bracket.oswprt": 40 * 20 * 6 - math.pi * 9 * 6, "v1_pair.oswasm": 2 * (40 * 20 * 6 - math.pi * 9 * 6)}


def test_every_golden_file_is_listed():
    assert {p.name for p in NATIVE.iterdir()} == set(GOLDEN)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_files_open_to_the_same_model(name):
    S.open_part(str(NATIVE / name))
    assert abs(S.get_model()["volume_mm3"] - GOLDEN[name]) < 1e-4


def test_newer_and_foreign_files_are_refused(tmp_path):
    d = json.loads((NATIVE / "v1_bracket.oswprt").read_text())
    newer = tmp_path / "newer.oswprt"
    newer.write_text(json.dumps({**d, "version": 999}))
    with pytest.raises(S.CommandError, match="newer openswx"):
        S.open_part(str(newer))
    assert json.loads(newer.read_text())["version"] == 999  # refused, not rewritten
    wrong = tmp_path / "wrong.oswprt"
    wrong.write_text(json.dumps({**d, "format": "something-else"}))
    with pytest.raises(S.CommandError, match="not an openswx part"):
        S.open_part(str(wrong))


def test_unknown_keys_survive_a_round_trip(tmp_path):
    d = json.loads((NATIVE / "v1_bracket.oswprt").read_text())
    f = tmp_path / "extra.oswprt"
    f.write_text(json.dumps({**d, "from_the_future": {"keep": [1, 2, 3]}}))
    S.open_part(str(f))
    S.set_global_variable("h", 6)
    S.save_part(str(tmp_path / "extra2.oswprt"))
    out = json.loads((tmp_path / "extra2.oswprt").read_text())
    assert out["from_the_future"] == {"keep": [1, 2, 3]} and out["variables"]["h"]


def test_steps_run_in_order(monkeypatch):
    monkeypatch.setitem(migrate.CURRENT, "openswx-part", 3)
    monkeypatch.setitem(migrate.STEPS, ("openswx-part", 1), lambda d: {**d, "trail": ["1>2"]})
    monkeypatch.setitem(migrate.STEPS, ("openswx-part", 2), lambda d: {**d, "trail": d["trail"] + ["2>3"]})
    out = migrate.migrate({"format": "openswx-part", "version": 1}, "openswx-part")
    assert out["version"] == 3 and out["trail"] == ["1>2", "2>3"]
    monkeypatch.delitem(migrate.STEPS, ("openswx-part", 2))
    with pytest.raises(migrate.FormatError, match="no way to read"):
        migrate.migrate({"format": "openswx-part", "version": 1}, "openswx-part")
