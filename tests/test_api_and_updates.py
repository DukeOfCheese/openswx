"""The object API for scripts (openswx/api.py) and the update check (openswx/system/updates.py)."""
import math
import urllib.error

import pytest

from openswx.system import updates
from openswx import session as S
from openswx.api import Error, Part


def test_object_api_builds_a_bracket(tmp_path):
    part = Part("ApiBracket")
    assert part.sketch("Top").rectangle([0, 0], [80, 40]).circle([20, 20], 5).extrude(10) is part
    v = 80 * 40 * 10 - math.pi * 25 * 10
    assert part.volume == pytest.approx(v)
    top = part.face(normal=[0, 1, 0])
    part.sketch(top).circle([60, 20], 8).cut()  # (the top face's sketch has the Top plane's axes and origin)
    v -= math.pi * 64 * 10
    assert part.volume == pytest.approx(v)
    part.fillet(2, [e for e in part.edges(type="line") if e["length"] == pytest.approx(10)])
    assert part.volume < v and part.features[-1].startswith("Fillet")
    sk = part.sketch("Front")
    assert sk.line([200, 0], [210, 0]).status == "under_defined" and sk.last["entity"].startswith("Line")
    part.undo().material("Plain Carbon Steel")
    assert part.mass == pytest.approx(part.volume * 7.8e-3, rel=0.02)
    assert part.image(width=120, height=90)[:4] == b"\x89PNG" and part._repr_png_()[:4] == b"\x89PNG"
    assert "S.fillet" in part.script() and repr(part).startswith("<Part ApiBracket")

    saved = part.save(tmp_path / "ApiBracket.oswprt")
    step = part.export(tmp_path / "ApiBracket.step")
    part.close()
    again = Part.open(saved)
    assert again.volume == pytest.approx(v, rel=0.01) and Part.open(step).volume == pytest.approx(again.volume)
    # a second part does not disturb the first: each call activates its own document
    other = Part("Other")
    other.sketch("Top").circle([0, 0], 3).extrude(2)
    assert again.volume > other.volume == pytest.approx(math.pi * 9 * 2)
    assert again.run("get_model")["name"] == "ApiBracket"
    with pytest.raises(Error):
        other.fillet(50, other.edges())
    with pytest.raises(Error):
        other.face(type="torus")


def test_update_check(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENSWX_UPDATE_REPO", raising=False)
    assert updates.repository() == "DukeOfCheese/openswx"  # (the project's own releases, by default)
    S.set_settings({"update_repository": ""})
    r = S.check_for_updates()
    assert r["configured"] is False and "update_repository" in r["message"]  # nowhere to ask: nothing is fetched

    asked = []

    def feed(tag):
        def fetch(url):
            asked.append(url)
            return {"tag_name": tag, "html_url": "https://github.com/acme/openswx/releases/tag/" + tag}
        return fetch
    monkeypatch.setenv("OPENSWX_UPDATE_REPO", "acme/openswx")
    newer = updates.check(feed("v9.1.0"))
    assert newer["update_available"] and newer["latest"] == "9.1.0" and asked == ["https://api.github.com/repos/acme/openswx/releases/latest"]
    assert updates.check(feed("v0.0.1"))["update_available"] is False

    def down(url):
        raise urllib.error.URLError("offline")
    assert "could not reach" in updates.check(down)["error"]
    monkeypatch.setenv("OPENSWX_UPDATE_REPO", "not a repository; rm -rf")
    assert updates.repository() is None and updates.check(feed("v9"))["configured"] is False
    assert updates.version_key("v1.2.10") > updates.version_key("1.2.9") > updates.version_key("1.2.9-rc1")
    S.set_settings({"update_repository": "acme/openswx"})
    monkeypatch.delenv("OPENSWX_UPDATE_REPO")
    assert updates.repository() == "acme/openswx"
