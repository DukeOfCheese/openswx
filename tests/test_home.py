import json

import pytest

from openswx.system import recent, settings
from openswx import session as S


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path / "home"))


def _part(tmp_path, name="Block"):
    S.new_part(name)
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, 5)
    p = tmp_path / f"{name}.oswprt"
    S.save_part(str(p))
    return p


# ---------------------------------------------------------------------------- recent files
def test_recent_newest_first_once_each(tmp_path):
    files = [tmp_path / f"f{i}.oswprt" for i in range(3)]
    for f in files:
        f.write_text("{}")
        recent.add(f)
    recent.add(files[0])  # again: moves to the top, not listed twice
    names = [e["name"] for e in recent.entries()]
    assert names == ["f0.oswprt", "f2.oswprt", "f1.oswprt"]
    assert all(e["exists"] and e["kind"] == "part" for e in recent.entries())


def test_recent_keeps_fifteen(tmp_path):
    for i in range(recent.LIMIT + 5):
        recent.add(tmp_path / f"f{i}.oswprt")
    e = recent.entries()
    assert len(e) == recent.LIMIT
    assert e[0]["name"] == f"f{recent.LIMIT + 4}.oswprt"


def test_recent_missing_files_and_kinds(tmp_path):
    a = tmp_path / "gone.oswasm"
    recent.add(a)
    (e,) = recent.entries()
    assert e["exists"] is False and e["kind"] == "assembly" and e["folder"] == str(tmp_path.resolve())


def test_recent_corrupt_file_is_an_empty_list(tmp_path):
    recent.path().parent.mkdir(parents=True)
    for bad in ("not json", '{"path": 1}', '[1, {"path": 5}, {"nopath": true}, {"path": "", "kind": "part"}]'):
        recent.path().write_text(bad, encoding="utf-8")
        assert recent.entries() == []
    recent.add(tmp_path / "x.oswprt")  # and it is written afresh
    assert [e["name"] for e in recent.entries()] == ["x.oswprt"]


def test_recent_skips_browser_uploads(tmp_path):
    import tempfile
    from pathlib import Path
    up = Path(tempfile.mkdtemp(prefix="openswx_")) / "sent.oswprt"
    recent.add(up)
    assert recent.entries() == []


def test_open_and_save_are_recorded_and_commands(tmp_path):
    p = _part(tmp_path)
    assert [e["path"] for e in S.recent_files()["recent"]] == [str(p.resolve())]
    other = _part(tmp_path, "Other")
    S.open_part(str(p))
    assert [e["name"] for e in S.recent_files()["recent"]] == ["Block.oswprt", "Other.oswprt"]
    assert [e["name"] for e in S.clear_recent_files(str(other))["recent"]] == ["Block.oswprt"]
    assert S.clear_recent_files()["recent"] == []
    json.dumps(S.recent_files())


def test_failed_open_is_not_recorded(tmp_path):
    with pytest.raises(S.CommandError):
        S.open_part(str(tmp_path / "missing.oswprt"))
    assert S.recent_files()["recent"] == []


# ---------------------------------------------------------------------------- settings
def test_show_home_setting_and_environment(monkeypatch):
    monkeypatch.delenv("OPENSWX_SHOW_HOME", raising=False)
    assert settings.get("show_home") is True
    assert settings.get("new_part_location") == "local"
    settings.update({"show_home": False, "new_part_location": "cloud"})
    assert settings.get("show_home") is False and settings.get("new_part_location") == "cloud"
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "1")
    assert settings.get("show_home") is True  # (the environment wins)
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "0")
    assert settings.get("show_home") is False
    with pytest.raises(settings.SettingsError):
        settings.update({"new_part_location": "mars"})


# ---------------------------------------------------------------------------- the part made on demand
def test_untouched_part_gives_way_to_an_opened_one(tmp_path):
    p = _part(tmp_path)
    S.close_document()
    assert S.list_documents()["documents"] == []
    m = S.get_model()  # (the window asks: a part is made on demand)
    assert m["throwaway"] is True and m["name"].startswith("Part")
    S.open_part(str(p))
    assert [d["name"] for d in S.list_documents()["documents"]] == ["Block"]


def test_untouched_part_gives_way_to_new_documents():
    S.get_model()
    S.new_part()
    assert len(S.list_documents()["documents"]) == 1
    assert "throwaway" not in S.get_model()  # (New Part is not made on demand)
    S.APP.documents.clear(); S.APP.active = None
    S.get_model()
    S.new_assembly()
    docs = S.list_documents()["documents"]
    assert len(docs) == 1 and docs[0]["name"].startswith("Assem")


def test_a_part_made_on_demand_is_kept_once_used(tmp_path):
    first = S.get_model()["name"]
    sk = S.create_sketch("Top")["sketch"]  # (an agent's first command works on it)
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, 5)
    assert "throwaway" not in S.get_model()
    S.new_part("Next")
    assert [d["name"] for d in S.list_documents()["documents"]] == [first, "Next"]


def test_new_part_is_kept_when_another_opens(tmp_path):
    p = _part(tmp_path)
    S.close_document()
    S.new_part("Mine")
    S.open_part(str(p))
    assert [d["name"] for d in S.list_documents()["documents"]] == ["Mine", "Block"]


# ---------------------------------------------------------------------------- the first paint
def test_home_at_start_decides_before_the_window_loads(tmp_path, monkeypatch):
    p = _part(tmp_path)
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "1")
    S.APP.documents.clear(); S.APP.active = None
    assert S.home_at_start() is True  # (nothing open yet: the window gets the part made on demand)
    S.get_model()
    assert S.home_at_start() is True  # (the untouched part made on demand)
    S.open_part(str(p))  # (as `openswx file.SLDPRT` does before the window loads)
    assert S.home_at_start() is False
    S.APP.documents.clear(); S.APP.active = None
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "0")
    assert S.home_at_start() is False


def test_index_page_starts_with_home_up_only_when_it_will_show(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from openswx.server.web_server import create_app
    p = _part(tmp_path)
    S.APP.documents.clear(); S.APP.active = None
    client = TestClient(create_app(token=None, mount_mcp=False), base_url="http://127.0.0.1:8765")
    home_div = lambda: next(l for l in client.get("/").text.splitlines() if 'id="home"' in l)  # noqa: E731
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "1")
    assert "hidden" not in home_div()
    S.open_part(str(p))
    assert "hidden" in home_div()
    S.APP.documents.clear(); S.APP.active = None
    monkeypatch.setenv("OPENSWX_SHOW_HOME", "0")
    assert "hidden" in home_div()
