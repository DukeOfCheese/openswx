"""Build the difftest cases in a licensed SOLIDWORKS (public COM API) and export each as Parasolid .x_t and
.x_b, recording SOLIDWORKS' own volume / area as ground truth for the clean-room XT reader.

    python tools/difftest/xt_samples.py OUT_DIR [CASE ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import difftest  # noqa: E402


# extra shapes for the reader only (freeform profiles: SOLIDWORKS and openswx splines need not agree exactly)
XT_CASES = difftest.CASES + [
    {"name": "spline_extrude", "expect": None,
     "ops": [("sketch", "Top"), ("spline", [(-40, 0), (-20, 25), (0, 10), (20, 30), (40, 0)]), ("line", 40, 0, -40, 0),
             ("extrude", 15, "Blind")]},
    {"name": "spline_drafted", "expect": None,  # a drafted spline side is a true B_SURFACE in XT
     "ops": [("sketch", "Top"), ("spline", [(-40, 0), (-20, 25), (0, 10), (20, 30), (40, 0)]), ("line", 40, 0, -40, 0),
             ("extrude", 15, "Blind", 5)]},
    {"name": "spline_fillet", "expect": None,  # rolling-ball fillet between a plane and a B-surface
     "ops": [("sketch", "Top"), ("spline", [(-40, 0), (-20, 25), (0, 10), (20, 30), (40, 0)]), ("line", 40, 0, -40, 0),
             ("extrude", 15, "Blind", 5), ("fillet_ray", 2, [((3.837, 15.0, -9.846), (0.0, -1.0, 0.0))])]},
    {"name": "spline_revolve", "expect": None,
     "ops": [("sketch", "Front"), ("centerline", 0, -5, 0, 50), ("line", 0, 0, 20, 0),
             ("spline", [(20, 0), (28, 15), (18, 30), (22, 40)]), ("line", 22, 40, 0, 40), ("line", 0, 40, 0, 0),
             ("revolve", 360)]},
]


class Exporter(difftest.SolidWorks):
    def __init__(self, out: Path):
        super().__init__()
        self.out = out
        self.truth = {}

    def before_close(self, m, case):  # export each part just before the difftest closes it
        saved = {}
        for ext in ("x_t", "x_b", "sldprt"):
            path = self.out / f"{case['name']}.{ext}"
            m.SaveAs(str(path))  # the extension selects the Parasolid text / binary exporter or the native format
            saved[ext] = str(path)
        self.truth[case["name"]] = {"files": saved}

    def run(self, case):
        res = super().run(case)
        self.truth[case["name"]].update(res)
        return res


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "xt_samples")
    only = set(sys.argv[2:])  # optional case names
    out.mkdir(parents=True, exist_ok=True)
    ex = Exporter(out)
    old = out / "truth.json"
    if only and old.exists():  # keep the cases that are not being re-exported
        ex.truth = json.loads(old.read_text(encoding="utf-8"))
    for case in XT_CASES:
        if only and case["name"] not in only:
            continue
        try:
            ex.run(case)
            print("exported", case["name"])
        except Exception as exc:
            print("FAILED", case["name"], exc)
    (out / "truth.json").write_text(json.dumps(ex.truth, indent=1), encoding="utf-8")
    print("truth:", out / "truth.json")


if __name__ == "__main__":
    main()
