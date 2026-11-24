"""Development-time oracle for decoding SOLIDWORKS feature streams: build pairs of parts in a licensed
SOLIDWORKS (public COM API) that differ in exactly one feature parameter and save them as .SLDPRT, so the
bytes that encode that parameter can be located by diffing Contents/Config-<n>-ResolvedFeatures.
openswx itself never uses SOLIDWORKS; this only produces test data. The saved parts carry the Windows user
name in their properties: keep OUT_DIR outside the repository.

    python tools/difftest/feature_samples.py OUT_DIR [CASE ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import difftest  # noqa: E402

BLOCK = [("sketch", "Top"), ("rect", 0, 0, 100, 60)]
CASES = [
    # extrude depth
    {"name": "ext_d20", "ops": BLOCK + [("extrude", 20, "Blind")]},
    {"name": "ext_d25", "ops": BLOCK + [("extrude", 25, "Blind")]},
    # sketch size (rectangle width)
    {"name": "rect_w90", "ops": [("sketch", "Top"), ("rect", 0, 0, 90, 60), ("extrude", 20, "Blind")]},
    # direction (reverse)
    {"name": "ext_rev", "ops": BLOCK + [("extrude", 20, "Blind", 0, True)]},
    {"name": "ext_mid_rev", "ops": BLOCK + [("extrude", 20, "MidPlane", 0, True)]},
    # end condition
    {"name": "ext_mid", "ops": BLOCK + [("extrude", 20, "MidPlane")]},
    # draft
    {"name": "ext_draft5", "ops": BLOCK + [("extrude", 20, "Blind", 5)]},
    # fillet radius
    {"name": "fil_r5", "ops": BLOCK + [("extrude", 20, "Blind"),
                                       ("fillet", 5, [(50, 10, 30), (50, 10, -30), (-50, 10, 30), (-50, 10, -30)])]},
    {"name": "fil_r6", "ops": BLOCK + [("extrude", 20, "Blind"),
                                       ("fillet", 6, [(50, 10, 30), (50, 10, -30), (-50, 10, 30), (-50, 10, -30)])]},
    # cut through all vs blind
    {"name": "cut_thru", "ops": BLOCK + [("extrude", 20, "Blind"), ("sketch_face", (10, 20, 10)), ("circle", 0, 0, 10),
                                         ("cut", 0, "ThroughAll")]},
    {"name": "cut_blind8", "ops": BLOCK + [("extrude", 20, "Blind"), ("sketch_face", (10, 20, 10)), ("circle", 0, 0, 10),
                                           ("cut", 8, "Blind")]},
    # shell thickness
    {"name": "shell_t2", "ops": BLOCK + [("extrude", 20, "Blind"), ("shell", 2, (10, 20, 10))]},
    {"name": "shell_t3", "ops": BLOCK + [("extrude", 20, "Blind"), ("shell", 3, (10, 20, 10))]},
    # revolve
    {"name": "rev_360", "ops": [("sketch", "Front"), ("centerline", 0, -5, 0, 50), ("rect", 15, 20, 10, 40), ("revolve", 360)]},
    # minimal sketches (no feature): one entity each, to read the entity records
    {"name": "sk_line", "ops": [("sketch", "Top"), ("line", 7, 11, 31, 23)]},
    {"name": "sk_2lines", "ops": [("sketch", "Top"), ("line", 7, 11, 31, 23), ("line", 31, 23, 43, 5)]},
    {"name": "sk_circle", "ops": [("sketch", "Top"), ("circle", 13, 17, 9)]},
    {"name": "sk_arc", "ops": [("sketch", "Top"), ("arc", 13, 17, 22, 17, 13, 26)]},
    # the same arc's complement (start and end swapped, 270 degrees), to find how the sweep side is stored
    {"name": "sk_arc_rev", "ops": [("sketch", "Top"), ("arc", 13, 17, 13, 26, 22, 17)]},
    {"name": "arc_slot", "ops": [("sketch", "Top"), ("line", 0, 0, 40, 0), ("arc", 40, 10, 40, 0, 40, 20),
                                 ("line", 40, 20, 0, 20), ("arc", 0, 10, 0, 20, 0, 0), ("extrude", 5, "Blind")]},
    {"name": "sk_line_front", "ops": [("sketch", "Front"), ("line", 7, 11, 31, 23)]},
    {"name": "ell_ext", "ops": [("sketch", "Top"), ("ellipse", 5, 3, 25, 3, 5, 11), ("extrude", 10, "Blind")]},
    # sketch dimensions (driving): a 60 x 40 centre rectangle with its width and height dimensioned
    {"name": "dim_rect", "ops": [("sketch", "Front"), ("rect", 0, 0, 60, 40), ("dim", 0, 20, 0, 30), ("dim", 30, 0, 40, 0),
                                 ("extrude", 10, "Blind")]},
    # sketch relations: the same geometry with and without one relation (automatic relations off)
    {"name": "rel_none", "ops": [("sketch", "Front"), ("addtodb", True), ("line", 0, 0, 30, 0), ("addtodb", False)]},
    {"name": "rel_h", "ops": [("sketch", "Front"), ("addtodb", True), ("line", 0, 0, 30, 0), ("addtodb", False),
                              ("rel", "sgHORIZONTAL2D", [(15, 0)])]},
    {"name": "rel_fix", "ops": [("sketch", "Front"), ("addtodb", True), ("line", 0, 0, 30, 0), ("addtodb", False),
                                ("rel", "sgFIXED", [(15, 0)])]},
    {"name": "rel_perp", "ops": [("sketch", "Front"), ("addtodb", True), ("line", 0, 0, 30, 40), ("line", 30, 40, -10, 70),
                                 ("addtodb", False), ("rel", "sgPERPENDICULAR", [(15, 20), (10, 55)])]},
    {"name": "rel_par", "ops": [("sketch", "Front"), ("addtodb", True), ("line", 0, 0, 30, 40), ("line", 50, 0, 80, 40),
                                ("addtodb", False), ("rel", "sgPARALLEL", [(15, 20), (65, 20)])]},
    # a line and a centerline (construction): to find the construction flag
    {"name": "sk_constr", "ops": [("sketch", "Top"), ("line", 0, 0, 30, 0), ("centerline", 0, 10, 30, 10)]},
]


class Saver(difftest.SolidWorks):
    def __init__(self, out: Path):
        super().__init__()
        self.out = out

    def before_close(self, m, case):
        m.SaveAs(str(self.out / f"{case['name']}.SLDPRT"))


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "feature_samples")
    only = set(sys.argv[2:])
    out.mkdir(parents=True, exist_ok=True)
    sw = Saver(out)
    for case in CASES:
        if only and case["name"] not in only:
            continue
        try:
            r = sw.run(case)
            print("saved", case["name"], round(r["volume"], 3))
        except Exception as exc:
            print("FAILED", case["name"], exc)


if __name__ == "__main__":
    main()
