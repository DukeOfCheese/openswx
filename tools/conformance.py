"""Conformance cases: a command sequence and what it must produce, as language-neutral JSON.

    tests/conformance/<name>.json = {"name", "description", "kind": "part", "commands": [{"name", "args"}],
                                     "expect": {"volume", "surface_area", "faces", "edges", "vertices",
                                                "bounding_box": {"min", "max"}, "bodies"},
                                     "tolerance": {"relative": 1e-6, "absolute": 1e-6}}

Any implementation of the openswx commands (this one, a port, or SOLIDWORKS through tools/difftest) can run
them: start a new part, run the commands in order, compare.

    python tools/conformance.py record FILE.oswprt [-o tests/conformance/NAME.json] [--description TEXT]
        make a case from a part built in openswx (its recorded history is the command sequence)
    python tools/conformance.py run [DIR]
        run every case and report (tests/test_conformance.py does the same under pytest)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "conformance"


def _session():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("OPENSWX_CACHE", "off")
    os.environ.setdefault("OPENSWX_AUTOSAVE", "off")
    os.environ.setdefault("OPENSWX_LOG", "off")
    from openswx import session as S
    return S


def measure(S) -> dict:
    mp = S.get_mass_properties()
    return {"volume": mp["volume"], "surface_area": mp["surface_area"], "faces": mp["faces"], "edges": mp["edges"],
            "vertices": mp["vertices"], "bodies": mp["bodies"], "bounding_box": mp["bounding_box"]}


def run_case(case: dict, S=None) -> list[str]:
    """Run one case; returns the differences (empty: it conforms)."""
    S = S or _session()
    S.new_part(case["name"])
    for i, c in enumerate(case["commands"]):
        try:
            S.call(c["name"], c.get("args") or {})
        except S.CommandError as exc:
            return [f"command {i + 1} ({c['name']}) failed: {exc}"]
    got, exp = measure(S), case["expect"]
    rel = case.get("tolerance", {}).get("relative", 1e-6)
    ab = case.get("tolerance", {}).get("absolute", 1e-6)

    def close(a, b):
        return abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))
    out = []
    for k in ("volume", "surface_area"):
        if k in exp and not close(got[k], exp[k]):
            out.append(f"{k}: {got[k]!r}, expected {exp[k]!r}")
    for k in ("faces", "edges", "vertices", "bodies"):
        if k in exp and got[k] != exp[k]:
            out.append(f"{k}: {got[k]}, expected {exp[k]}")
    if "bounding_box" in exp:
        for side in ("min", "max"):
            for a, b, axis in zip(got["bounding_box"][side], exp["bounding_box"][side], "xyz"):
                if not close(a, b):
                    out.append(f"bounding box {side} {axis}: {a!r}, expected {b!r}")
    return out


def record(path: str, out: str | None, description: str) -> Path:
    S = _session()
    S.open_part(str(Path(path).resolve()))
    doc = S.APP.doc
    if getattr(doc, "journal", None) is None:
        raise SystemExit(f"{path} has no recorded history (it was imported, or saved by an older openswx)")
    case = {"name": doc.name, "description": description, "kind": "part",
            "commands": [{"name": e["command"], "args": e["args"]} for e in doc.journal],
            "expect": measure(S), "tolerance": {"relative": 1e-6, "absolute": 1e-6}}
    # the sequence must reproduce itself before it is worth keeping
    problems = run_case(case, S)
    if problems:
        raise SystemExit("the recorded commands do not rebuild the part:\n  " + "\n  ".join(problems))
    target = Path(out) if out else CASES / f"{doc.name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(case, indent=1) + "\n", encoding="utf-8")
    return target


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("record", "run"):
        print(__doc__)
        return 2
    if argv[0] == "record":
        import argparse
        ap = argparse.ArgumentParser(prog="conformance.py record")
        ap.add_argument("file")
        ap.add_argument("-o", "--output")
        ap.add_argument("--description", default="")
        a = ap.parse_args(argv[1:])
        print(record(a.file, a.output, a.description))
        return 0
    folder = Path(argv[1]) if len(argv) > 1 else CASES
    S = _session()
    failed = 0
    for f in sorted(folder.glob("*.json")):
        problems = run_case(json.loads(f.read_text(encoding="utf-8")), S)
        print(("ok    " if not problems else "FAIL  ") + f.name + "".join(f"\n        {p}" for p in problems))
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
