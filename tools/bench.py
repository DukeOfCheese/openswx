"""Benchmarks: how long the things users wait for take. Run before and after a change that should make
something faster (or might make it slower); keep the JSON with a release to track it over time.

    python tools/bench.py [-o bench.json] [--repeat 3] [--files a.sldprt b.step ...]

Each timing is the best of `--repeat` runs, in seconds. `import` is measured in a fresh interpreter.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k in ("OPENSWX_CACHE", "OPENSWX_AUTOSAVE", "OPENSWX_LOG", "OPENSWX_PLUGINS"):
    os.environ.setdefault(k, "off")


def best(fn, repeat: int) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return round(min(times), 4)


def cold_import() -> float:
    code = "import time; t = time.perf_counter(); from openswx import session; print(time.perf_counter() - t)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=dict(os.environ))
    return round(float(out.stdout.strip().splitlines()[-1]), 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--output")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--files", nargs="*", default=[], help="files to time opening (e.g. your own .sldprt / .step)")
    a = ap.parse_args()
    results = {"import_session_cold": cold_import()}
    from openswx import session as S
    from openswx.core.geom import tessellate

    def first_part():
        S.new_part("bench")
        sk = S.create_sketch("Top")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [100, 60])
        S.extrude(sk, 10)
    results["first_extrude"] = best(first_part, a.repeat)

    def plate(n=40):
        S.new_part("bench")
        sk = S.create_sketch("Top")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [10.0 * n, 60])
        S.extrude(sk, 10)
        for i in range(n):  # one hole feature per step: a long history
            h = S.create_sketch("Top")["sketch"]
            S.sketch_circle(h, [5 + 10 * i, 30], 3)
            S.cut_extrude(h, end_condition="ThroughAllBoth")
    t = time.perf_counter()
    plate()
    results["build_80_features"] = round(time.perf_counter() - t, 3)
    doc = S.APP.doc
    results["full_rebuild_80_features"] = best(lambda: S.rebuild(), a.repeat)
    results["edit_first_feature_80_features"] = best(lambda: S.edit_feature("Boss-Extrude1", {"depth": 10 + time.perf_counter() % 1}), a.repeat)
    results["edit_last_feature_80_features"] = best(lambda: S.edit_feature(doc.features[-1]["name"], {"reverse": False}), a.repeat)
    results["tessellate_plate"] = best(lambda: tessellate(doc.body), a.repeat)
    results["topology_with_names"] = best(lambda: S.get_topology(), a.repeat)
    results["undo_redo"] = best(lambda: (S.undo(), S.redo()), a.repeat)
    results["view_image_800x600"] = best(lambda: S.get_view_image(width=800, height=600), a.repeat)

    def sketch_solve():
        S.new_part("bench")
        sk = S.create_sketch("Top")["sketch"]
        prev = None
        for i in range(30):  # a chain of dimensioned lines: one solve per entity
            r = S.sketch_line(sk, prev or [0, 0], [10 * (i + 1), 5 * (i % 2)])["result"]
            S.sketch_dimension(sk, "length", [r["entity"]], 10.0 + i % 3)
            prev = r["points"][1]
    results["sketch_30_lines_30_dimensions"] = best(sketch_solve, 1)

    for f in a.files:
        p = Path(f)
        if p.exists():
            results[f"open {p.name}"] = best(lambda p=p: S.open_part(str(p)), 1)
    report = {"openswx": __import__("openswx").__version__, "python": platform.python_version(),
              "platform": platform.platform(), "repeat": a.repeat, "seconds": results}
    text = json.dumps(report, indent=1)
    print(text)
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
