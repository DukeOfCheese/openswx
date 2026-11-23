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
    survey.run(str(tmp_path / "corpus"), str(out), 115, limit=2, smallest_first=True, rebuild=fake(before))
    assert list(survey.load(str(out))) == ["a.SLDPRT", "c.sldprt"]  # the two smallest first
    survey.run(str(tmp_path / "corpus"), str(out), 115, limit=None, smallest_first=True, rebuild=fake(before))
    rows = survey.load(str(out))
    assert len(rows) == 4 and len(out.read_text().splitlines()) == 4  # the first two were not run again
    assert rows["a.SLDPRT"]["outcome"] == "exact" and rows["a.SLDPRT"]["parametric"] is True
    assert rows["b.sldprt"]["outcome"] == "exact" and rows["b.sldprt"]["parametric"] is False
    assert rows["c.sldprt"]["outcome"] == "inexact" and rows["d.sldprt"]["outcome"] == "timeout"
    assert survey.summary(rows) == {"parts": 4, "exact": 2, "fully_parametric": 1,
                                    "by_outcome": {"exact": 2, "inexact": 1, "timeout": 1}}

    after = {**before, "c.sldprt": exact, "a.SLDPRT": (None, "the child process crashed"), "b.sldprt": exact}
    out2 = tmp_path / "after.jsonl"
    survey.run(str(tmp_path / "corpus"), str(out2), 115, None, False, rebuild=fake(after), jobs=3)  # (in parallel)
    assert len(out2.read_text().splitlines()) == 4
    cmp_ = survey.compare(rows, survey.load(str(out2)))
    assert cmp_["gained"] == ["c.sldprt"] and cmp_["lost"] == ["a.SLDPRT"]
    assert cmp_["parametric_gained"] == ["b.sldprt", "c.sldprt"] and cmp_["parametric_lost"] == ["a.SLDPRT"]
    assert survey.load(str(out2))["a.SLDPRT"]["outcome"] == "crash"
    assert survey.main(["compare", str(out), str(out2)]) == 1  # something was lost: a failing exit status
    assert survey.main(["compare", str(out), str(out)]) == 0


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
