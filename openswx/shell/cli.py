"""Headless command-line tools: one-shot jobs on a file, for scripts and CI (no window, no server).

  openswx convert IN OUT              any format openswx opens -> .step .iges .stl .3mf .glb .brep .x_t .oswprt
  openswx render FILE -o out.png      a picture (--view isometric|front|top|..., --size 1600x1200)
  openswx massprops FILE              mass properties as JSON
  openswx check FILE                  the file's requirements and a rebuild check; exit status 1 if any fails
  openswx drawing FILE -o out.pdf     the file's drawing (or a standard three-view one) as .pdf .dxf .svg
  openswx script FILE [-o out.py]     the document as a Python script (when its history was recorded)
  openswx diff A B                    what changed between two .oswprt / .oswasm files
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

COMMANDS = ("convert", "render", "massprops", "check", "drawing", "script", "diff")
EXPORTS = {".step": "step", ".stp": "step", ".igs": "iges", ".iges": "iges", ".stl": "stl", ".3mf": "3mf",
           ".glb": "glb", ".gltf": "gltf", ".brep": "brep", ".x_t": "x_t"}


def add_parsers(sub) -> None:
    p = sub.add_parser("convert", help="convert a CAD file (headless)")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--no-history", action="store_true", help=".sldprt: read the stored body only (faster)")
    p = sub.add_parser("render", help="render a picture of a file (headless)")
    p.add_argument("file")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--view", default="isometric")
    p.add_argument("--size", default="1600x1200", help="WIDTHxHEIGHT in pixels")
    p = sub.add_parser("massprops", help="print a file's mass properties as JSON")
    p.add_argument("file")
    p = sub.add_parser("check", help="check a file's requirements and that it rebuilds (exit 1 on failure)")
    p.add_argument("file")
    p.add_argument("--review", action="store_true", help="also run the design review; its errors fail the check")
    p = sub.add_parser("drawing", help="export a file's drawing (headless)")
    p.add_argument("file")
    p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("script", help="print a document as a Python script")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p = sub.add_parser("diff", help="compare two openswx files")
    p.add_argument("a")
    p.add_argument("b")


def run(a) -> int:
    import os
    os.environ.setdefault("OPENSWX_AUTOSAVE", "off")  # (one-shot jobs: nothing to recover)
    from openswx import session as S

    def fail(msg: str) -> int:
        print(f"openswx: {msg}", file=sys.stderr)
        return 2

    def open_(path: str, history: bool = True):
        if not Path(path).exists():
            raise S.CommandError(f"no such file: {path}")
        S.call("open_part", {"path": str(Path(path).resolve()), **({} if history else {"history": False})}, "ui")

    try:
        if a.cmd == "convert":
            out = Path(a.output)
            ext = out.suffix.lower()
            open_(a.input, not a.no_history)
            if ext in (".oswprt", ".oswasm"):
                r = S.call("save_part", {"path": str(out.resolve())}, "ui")
                print(r["saved"])
                return 0
            if ext not in EXPORTS:
                return fail(f"cannot write {ext or 'a file without an extension'}; expected one of {sorted(EXPORTS)} or .oswprt")
            S.call("export_file", {"path": str(out.resolve()), "format": EXPORTS[ext]}, "ui")
            print(out.resolve())
            return 0
        if a.cmd == "render":
            try:
                w, h = (int(x) for x in a.size.lower().split("x"))
            except ValueError:
                return fail("--size must look like 1600x1200")
            open_(a.file)
            r = S.call("get_view_image", {"view": a.view, "width": w, "height": h}, "ui")
            Path(a.output).write_bytes(base64.b64decode(r["png_base64"]))
            print(Path(a.output).resolve())
            return 0
        if a.cmd == "massprops":
            open_(a.file)
            print(json.dumps(S.call("get_mass_properties", {}, "ui"), indent=1, default=str))
            return 0
        if a.cmd == "check":
            open_(a.file)
            model = S.call("get_model", {}, "ui")
            broken = [f["name"] for f in model.get("features", []) if f.get("status") == "error"]
            req = S.call("check_requirements", {}, "ui")
            report = {"file": str(Path(a.file).resolve()), "rebuild_errors": broken, "requirements": req["requirements"]}
            ok = req["ok"] and not broken
            if a.review and model.get("kind", "part") == "part":
                rev = S.call("review_model", {}, "ui")
                report["review"] = rev["findings"]
                ok = ok and rev["ok"]
            report["ok"] = ok
            print(json.dumps(report, indent=1, default=str))
            return 0 if ok else 1
        if a.cmd == "drawing":
            open_(a.file)
            doc = S.APP.active_doc
            if not (getattr(doc, "drawing", None) and doc.drawing.get("sheets")):
                S.call("drawing_new", {}, "ui")
            S.call("export_drawing", {"path": str(Path(a.output).resolve())}, "ui")
            print(Path(a.output).resolve())
            return 0
        if a.cmd == "script":
            open_(a.file)
            r = S.call("export_script", {"path": str(Path(a.output).resolve())} if a.output else {}, "ui")
            print(r["path"] if a.output else r["script"], end="" if not a.output else "\n")
            return 0
        if a.cmd == "diff":
            r = S.call("compare_documents", {"a": str(Path(a.a).resolve()), "b": str(Path(a.b).resolve())}, "ui")
            print(json.dumps(r, indent=1, default=str))
            return 0 if r["identical"] else 1
    except S.CommandError as exc:
        return fail(str(exc))
    return 2
