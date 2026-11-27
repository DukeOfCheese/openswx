"""Plugins that add a file format (plugins.register_format): a toy plugin registers a made-up extension, .toyx,
and the file must open and export through every path the built-in formats use."""
import importlib.metadata
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openswx import session as S
from openswx.exchange import formats
from openswx.system import fileassoc, plugins

L, W, H = 12.0, 7.0, 5.0  # the toy file's box: its volume is L*W*H, independent of openswx


class FakeEntryPoint:
    def __init__(self, name, value, load):
        self.name, self.value, self._load = name, value, load

    def load(self):
        return self._load()


def read_toy(path):
    """A .toyx file is one line, 'box L W H' (millimetres)."""
    from build123d import Box
    kind, *dims = Path(path).read_text().split()
    if kind != "box":
        raise ValueError(f"not a toy box: {kind}")
    return Box(*map(float, dims)), ["read by the toy plugin"]


def write_toy(shape, path):
    size = shape.bounding_box().size
    Path(path).write_text(f"box {size.X} {size.Y} {size.Z}\n")


@pytest.fixture
def toy(monkeypatch, tmp_path):
    """The toy plugin, installed and loaded the way an entry point is; returns a .toyx file to open."""
    monkeypatch.setattr(plugins, "loaded", [])
    monkeypatch.setattr(formats, "_plugin_formats", [])
    monkeypatch.delenv("OPENSWX_PLUGINS", raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: [FakeEntryPoint(
        "toy", "openswx_toy", lambda: plugins.register_format(".toyx", "Toy box", reader=read_toy, writer=write_toy))])
    plugins.load_all()
    f = tmp_path / "Box.toyx"
    f.write_text(f"box {L} {W} {H}\n")
    return f


def _volume():
    return S.call("get_mass_properties", {})["volume"]


def test_list_plugins_and_list_file_formats_show_the_format(toy):
    got = {p["name"]: p for p in S.call("list_plugins", {})["plugins"]}
    assert got["toy"]["ok"] and [f["id"] for f in got["toy"]["formats"]] == ["toyx"]
    fmts = S.call("list_file_formats", {})
    toyx = next(f for f in fmts["formats"] if f["id"] == "toyx")
    assert toyx == {"id": "toyx", "name": "Toy box", "extensions": [".toyx"], "open": True, "import": True,
                    "export": True, "native": False, "plugin": "toy"}
    assert ".toyx" in fmts["open"] and ".toyx" in fmts["import"] and ".toyx" in fmts["export"]
    assert ".step" in fmts["open"] and ".x_b" not in fmts["export"]  # (the built-in ones are in the same list)


def test_open_part_and_import_file(toy):
    r = S.call("open_part", {"path": str(toy)})
    assert r["name"] == "Box"
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)
    S.call("import_file", {"path": str(toy)})  # a second, overlapping box into the same part
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)
    feats = [f for f in S.call("get_model", {})["features"] if f["type"] == "import"]
    assert len(feats) == 2


def test_export_file_by_extension_and_by_format_id(toy, tmp_path):
    S.call("new_part", {})
    S.call("open_part", {"path": str(toy)})
    out = tmp_path / "out.toyx"
    assert S.call("export_file", {"path": str(out)})["format"] == "toyx"
    assert [float(x) for x in out.read_text().split()[1:]] == pytest.approx([L, W, H], rel=1e-6)
    other = tmp_path / "named.txt"
    assert S.call("export_file", {"path": str(other), "format": "toyx"})["format"] == "toyx"
    assert other.read_text().startswith("box ")
    S.call("open_part", {"path": str(out)})  # (round trip)
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)


def test_a_reader_error_names_the_plugin_and_leaves_no_empty_part(toy, tmp_path):
    bad = tmp_path / "Bad.toyx"
    bad.write_text("sphere 3\n")
    before = set(S.APP.documents)
    with pytest.raises(S.CommandError, match="Toy box reader \\(plugin toy\\) failed"):
        S.call("open_part", {"path": str(bad)})
    assert set(S.APP.documents) == before


def test_registration_rules(toy):
    with pytest.raises(formats.FormatError, match="built in"):
        plugins.register_format(".step", "My STEP", reader=read_toy)
    with pytest.raises(formats.FormatError, match="plugin toy"):
        plugins.register_format(".TOYX", "Another toy", writer=write_toy)
    with pytest.raises(formats.FormatError, match="reader"):
        plugins.register_format(".nothing", "Nothing")
    write_only = plugins.register_format([".wox"], "Write-only toy", writer=write_toy)
    assert not write_only.open and write_only.export
    wox = toy.with_suffix(".wox")
    wox.write_text("x")
    with pytest.raises(S.CommandError, match="exported but not opened"):
        S.call("open_part", {"path": str(wox)})
    S.call("new_part", {})
    with pytest.raises(S.CommandError, match="exported but not read"):
        S.call("import_file", {"path": str(wox)})


def test_web_upload_open_and_download_export(toy):
    from fastapi.testclient import TestClient
    from openswx.server.web_server import create_app
    client = TestClient(create_app(token=None, mount_mcp=False), base_url="http://127.0.0.1:8765")
    import base64
    r = client.post("/api/upload", json={"filename": "Box.toyx", "data": base64.b64encode(toy.read_bytes()).decode()})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)
    r = client.get("/api/download", params={"format": "toyx"})
    assert r.status_code == 200 and r.content.decode().startswith("box ")
    assert 'filename="Box.toyx"' in r.headers["content-disposition"]
    r = client.post("/api/command", json={"name": "list_file_formats", "args": {}})
    assert ".toyx" in r.json()["result"]["open"]


class _Dialogs:
    """Stands in for the native file dialog: records the filters it was shown and answers with `answer`."""

    def __init__(self, answer):
        self.answer, self.seen = answer, []

    def __call__(self, kind, name="", types=()):
        self.seen.append((kind, name, tuple(types)))
        return self.answer


def test_desktop_open_save_as_and_export_dialogs(toy, tmp_path, monkeypatch):
    from openswx.shell.desktop import DesktopApi
    api = DesktopApi()
    dlg = _Dialogs(str(toy))
    monkeypatch.setattr(api, "_dialog", dlg)
    r = api.open_file()
    assert r["ok"], r
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)
    kind, _, types = dlg.seen[-1]
    assert kind == "open" and "Toy box (*.toyx)" in types and "*.toyx" in types[0]  # (its own filter and in "all CAD")

    dlg.answer = str(tmp_path / "saved.toyx")
    r = api.save_as()
    assert r["ok"] and r["result"]["format"] == "toyx", r  # Save As with the plugin's extension exports
    assert "Toy box (*.toyx)" in dlg.seen[-1][2]
    assert (tmp_path / "saved.toyx").read_text().startswith("box ")

    dlg.answer = str(tmp_path / "exported.toyx")
    r = api.export("toyx")
    assert r["ok"] and dlg.seen[-1][1:] == ("Box.toyx", ("Toy box (*.toyx)",)), dlg.seen[-1]
    assert (tmp_path / "exported.toyx").exists()


def test_command_line_and_double_click_open_the_file(toy, monkeypatch):
    """Double-clicking a file (or `openswx FILE`) starts the desktop app with it; the app opens it with open_part."""
    from openswx import __main__ as entry
    from openswx.shell import desktop
    started = {}
    monkeypatch.setattr(desktop, "run", lambda port=8765, file=None: started.update(file=file) or 0)
    assert entry.main([str(toy)]) == 0
    assert started["file"] == str(toy)
    S.call("open_part", {"path": started["file"]}, "ui")  # (what the app does with it on start)
    assert _volume() == pytest.approx(L * W * H, rel=1e-6)


def test_cli_convert_reads_and_writes_the_format(toy, tmp_path):
    from openswx import __main__ as entry
    step = tmp_path / "box.step"
    assert entry.main(["convert", str(toy), str(step)]) == 0
    back = tmp_path / "back.toyx"
    assert entry.main(["convert", str(step), str(back)]) == 0
    assert [float(x) for x in back.read_text().split()[1:]] == pytest.approx([L, W, H], rel=1e-6)


def test_windows_open_with_offers_the_format(toy):
    keys = fileassoc.planned_keys(r"C:\openswx\openswx.exe")
    assert (rf"{fileassoc.APP_KEY}\SupportedTypes", ".toyx", "") in keys
    assert (r"Software\Classes\.toyx\OpenWithProgids", "openswx.File", "") in keys
    assert not any(k == r"Software\Classes\.toyx" and n is None for k, n, _ in keys)  # (not taken over)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_web_ui_open_filter_export_menu_and_save_as(toy, tmp_path):
    """The web UI reads list_file_formats: the Open dialog's filter, the Export menu and Save As follow it."""
    web = Path(__file__).resolve().parent.parent / "openswx" / "web" / "js"
    listing = S.call("list_file_formats", {})
    script = tmp_path / "t.mjs"
    script.write_text("""
const input = { accept: "" };
globalThis.document = { querySelector: (s) => (s === "#fileinput" ? input : {}), createElement: () => ({ style: {} }) };
const { installFiles } = await import(process.argv[2]);
class App {}
installFiles(App);
const app = new App();
app.api = async (path, body) => ({ ok: true, result: JSON.parse(process.argv[3]) });
await app.loadFileFormats();
console.log(JSON.stringify({ accept: input.accept, exports: app.pluginExports().map((f) => f.id),
  saveAs: app.exportFormatFor("C:\\\\work\\\\Part1.TOYX")?.id ?? null, native: app.exportFormatFor("/w/Part1.oswprt") }));
""")
    url = (web / "mixins" / "files.js").as_uri()
    r = subprocess.run(["node", str(script), url, json.dumps(listing)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert ".toyx" in got["accept"].split(",") and ".step" in got["accept"].split(",")
    assert got["exports"] == ["toyx"] and got["saveAs"] == "toyx" and got["native"] is None
    ui = (web / "mixins" / "ui.js").read_text(encoding="utf-8")
    assert "this.pluginExports().map((f) => [`Export > ${" in ui  # (the File menu lists the plugin's exports)
