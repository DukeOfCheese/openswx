"""The survey runner's bookkeeping (tools/survey.py): resumable runs, classification, comparison.
The rebuild itself is stood in for; no SOLIDWORKS files are needed or read."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import survey  # noqa: E402


def fake(results):
    def rebuild(path):
        return results[Path(path).name]
    return rebuild


def test_survey_run_is_resumable_and_compares(tmp_path):
    corpus = tmp_path / "corpus" / "sub"
    corpus.mkdir(parents=True)
    for n, size in (("a.SLDPRT", 10), ("b.sldprt", 30), ("c.sldprt", 20), ("d.sldprt", 40), ("notes.txt", 5)):
        (corpus / n).write_bytes(b"x" * size)
    exact = ({"volume": 100.0, "stored_volume": 100.0, "features": [1, 2], "imported": [], "skipped": []}, None)
    with_import = ({"volume": 50.0, "stored_volume": 50.0, "features": [1], "imported": ["Boss1"], "skipped": []}, None)
    off = ({"volume": 99.0, "stored_volume": 100.0, "features": [1], "imported": [], "skipped": ["Loft1"]}, None)
    before = {"a.SLDPRT": exact, "b.sldprt": with_import, "c.sldprt": off, "d.sldprt": (None, "the rebuild took longer than 115 s")}
    out = tmp_path / "before.jsonl"
    survey.run(str(corpus), str(out), 115, limit=2, smallest_first=True, rebuild=fake(before))
    assert list(survey.load(str(out))) == ["a.SLDPRT", "c.sldprt"]  # the two smallest first
    survey.run(str(corpus), str(out), 115, limit=None, smallest_first=True, rebuild=fake(before))
    rows = survey.load(str(out))
    assert len(rows) == 4 and len(out.read_text().splitlines()) == 4  # the first two were not run again
    assert rows["a.SLDPRT"]["outcome"] == "exact" and rows["a.SLDPRT"]["parametric"] is True
    assert rows["b.sldprt"]["outcome"] == "exact" and rows["b.sldprt"]["parametric"] is False
    assert rows["c.sldprt"]["outcome"] == "inexact" and rows["d.sldprt"]["outcome"] == "timeout"
    assert survey.summary(rows) == {"parts": 4, "exact": 2, "fully_parametric": 1,
                                    "by_outcome": {"exact": 2, "inexact": 1, "timeout": 1}}

    after = {**before, "c.sldprt": exact, "a.SLDPRT": (None, "the child process crashed"), "b.sldprt": exact}
    out2 = tmp_path / "after.jsonl"
    survey.run(str(corpus), str(out2), 115, None, False, rebuild=fake(after), jobs=3)  # (in parallel)
    assert len(out2.read_text().splitlines()) == 4
    cmp_ = survey.compare(rows, survey.load(str(out2)))
    assert cmp_["gained"] == ["c.sldprt"] and cmp_["lost"] == ["a.SLDPRT"]
    assert cmp_["parametric_gained"] == ["b.sldprt", "c.sldprt"] and cmp_["parametric_lost"] == ["a.SLDPRT"]
    assert survey.load(str(out2))["a.SLDPRT"]["outcome"] == "crash"
    assert survey.main(["compare", str(out), str(out2)]) == 1  # something was lost: a failing exit status
    assert survey.main(["compare", str(out), str(out)]) == 0


def test_parts_are_keyed_by_their_path_in_the_corpus(tmp_path):
    """Sample folders reuse file names (238 of SOLIDWORKS 2025's 919 parts): each file is its own row."""
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "part.sldprt").write_bytes(b"x")
    out = tmp_path / "r.jsonl"
    survey.run(str(tmp_path), str(out), 10, None, False, rebuild=lambda path: ({"volume": 1.0, "stored_volume": 1.0}, None))
    assert sorted(survey.load(str(out))) == ["a/part.sldprt", "b/part.sldprt"]


def test_a_rebuild_that_raises_does_not_end_the_survey(tmp_path):
    (tmp_path / "x.sldprt").write_bytes(b"x")
    (tmp_path / "y.sldprt").write_bytes(b"y")

    def rebuild(path):
        if path.endswith("x.sldprt"):
            raise RuntimeError("boom")
        return {"volume": 1.0, "stored_volume": 1.0}, None
    out = tmp_path / "r.jsonl"
    survey.run(str(tmp_path), str(out), 10, None, False, rebuild=rebuild)
    rows = survey.load(str(out))
    assert rows["x.sldprt"]["outcome"] == "error" and "boom" in rows["x.sldprt"]["reason"] and rows["y.sldprt"]["outcome"] == "exact"


def test_issue_forms_use_only_defined_labels():
    """tools/sync_labels.py reads .github/labels.yml, and every label an issue form applies is defined there."""
    import re

    import sync_labels
    defined = {lab["name"] for lab in sync_labels.load()}
    assert {"bug", "feature", "P0", "area:mcp", "good first issue"} <= defined
    root = Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE"
    forms = [p for p in root.glob("*.yml") if p.name != "config.yml"]
    assert len(forms) == 4
    for p in forms:
        used = re.search(r"^labels:\s*\[(.*)\]", p.read_text(encoding="utf-8"), re.M)[1]
        names = {s.strip().strip('"') for s in used.split(",")}
        assert names <= defined, (p.name, names - defined)


def test_release_metadata_agrees():
    """One version everywhere: pyproject.toml (PyPI, installers) and openswx.__version__ (About, update check)."""
    import re
    import tomllib

    import openswx
    root = Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["version"] == openswx.__version__
    assert re.search(rf'#define AppVersion "{re.escape(meta["version"])}"', (root / "packaging" / "openswx.iss").read_text(encoding="utf-8"))
    assert meta["urls"]["Homepage"].startswith("https://github.com/") and meta["readme"] == "README.md"
    assert re.search(rf'^version: {re.escape(meta["version"])}$', (root / "CITATION.cff").read_text(encoding="utf-8"), re.M)


def test_material_library_entries_are_plausible():
    """Every library material has the fields the studies need, in sane ranges and units (kg/m^3, MPa, W/(m K),
    J/(kg K)): a slip of units (GPa for MPa, g/cm^3 for kg/m^3) shows up as an out-of-range value here."""
    from openswx.core import materials
    lib = {n: m for n, m in materials.library().items() if m.get("category") != materials.CUSTOM_CATEGORY}
    assert len(lib) >= 90
    for name, m in lib.items():
        assert 100 <= m["density"] <= 20000, name
        assert 1 <= m["elastic_modulus"] <= 700e3, name
        assert 0.0 < m["poisson"] < 0.5, name
        assert m["tensile_strength"] > 0, name
        if m["category"] != "Plastics":  # (a plastic can yield above its strength at break)
            assert m["yield_strength"] <= m["tensile_strength"] * 1.01, name
        assert 0.01 <= m["thermal_conductivity"] <= 500 and 100 <= m["specific_heat"] <= 3000, name
        assert m["color"].startswith("#") and len(m["color"]) == 7, name
    assert materials.lookup("aisi 4140 (QUENCHED AND TEMPERED)")["yield_strength"] == 655.0


def test_release_notes_come_from_the_changelog():
    import release_notes
    text = release_notes.notes("v0.1.0")
    assert text.startswith("The first public version") and "## Install" in text and "openswx-setup-0.1.0.exe" in text
    assert "## 0.1.0" not in text and "## Unreleased" not in text  # its own section only, without the heading
    import pytest
    with pytest.raises(SystemExit):
        release_notes.notes("9.9.9")


def test_mcp_registry_entry_matches_the_package():
    """server.json (the MCP Registry listing) names this package and version, and the README carries the
    mcp-name marker the registry looks for on PyPI."""
    import json
    import tomllib
    root = Path(__file__).resolve().parent.parent
    entry = json.loads((root / "server.json").read_text(encoding="utf-8"))
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    pkg = entry["packages"][0]
    assert entry["version"] == pkg["version"] == version and pkg["identifier"] == "openswx"
    assert len(entry["description"]) <= 100
    assert f"mcp-name: {entry['name']}" in (root / "README.md").read_text(encoding="utf-8")

