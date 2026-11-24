"""Design Library: save and insert library features, and list, add or delete items."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from openswx.core.document import DocumentError
from openswx.session import APP, Vec2, command

from .common import openswx_home


def _library_dir() -> Path:
    d = openswx_home() / "library"
    (d / "parts").mkdir(parents=True, exist_ok=True)
    return d


def _has_model_refs(v) -> bool:
    """Does a parameter tree hold references to faces / edges of the model (those cannot travel)?"""
    if isinstance(v, dict):
        if v.get("kind") in ("face", "edge"):
            return True
        return any(_has_model_refs(x) for x in v.values())
    if isinstance(v, list):
        return any(_has_model_refs(x) for x in v)
    return False


@command(category="library")
def save_library_feature(name: str, features: list[str], description: str = "") -> dict:
    """Save features (and the sketches they use) as a library feature in the Design Library, to insert into
    other parts. Sketch-based features travel; features on model faces / edges (fillets...) cannot."""
    from openswx.core.document import consumed_sketches
    doc = APP.doc
    picked: list[str] = []
    for n in features:
        f = doc.find(n)
        for sk in consumed_sketches(f):
            if sk not in picked:
                picked.append(sk)
        if f["name"] not in picked:
            picked.append(f["name"])
    feats = [copy.deepcopy(f) for f in doc.features if f["name"] in picked]
    for f in feats:
        p = {k: v for k, v in f["params"].items() if k != "plane"}
        if _has_model_refs(p):
            raise DocumentError(f"{f['name']} uses faces or edges of this part; library features take sketch-based features")
    path = _library_dir() / f"{name}.oswlib"
    path.write_text(json.dumps({"format": "openswx-library-feature", "name": name, "description": description,
                                "units": doc.units, "features": feats}, indent=1), encoding="utf-8")
    return {"saved": str(path), "features": [f["name"] for f in feats]}


@command(mutates=False, category="library")
def list_design_library() -> dict:
    """The Design Library: library features (.oswlib) and parts (in its parts folder) with their paths."""
    d = _library_dir()
    feats = []
    for p in sorted(d.glob("*.oswlib")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        feats.append({"name": data.get("name", p.stem), "description": data.get("description", ""), "path": str(p),
                      "features": [f["name"] for f in data.get("features", [])]})
    parts = [{"name": p.stem, "path": str(p)} for p in sorted((d / "parts").glob("*"))
             if p.suffix.lower() in (".oswprt", ".step", ".stp", ".x_t", ".x_b", ".sldprt")]
    return {"folder": str(d), "features": feats, "parts": parts}


@command(category="library")
def add_part_to_design_library(path: str | None = None) -> dict:
    """Add a part file (default: the active part, saved under its name) to the Design Library's parts."""
    import shutil
    d = _library_dir() / "parts"
    if path:
        dst = d / Path(path).name
        shutil.copyfile(path, dst)
    else:
        doc = APP.doc
        dst = d / f"{doc.name}.oswprt"
        dst.write_text(json.dumps(doc.to_dict(), indent=1), encoding="utf-8")
    return {"added": str(dst)}


@command(category="library")
def delete_library_item(path: str) -> dict:
    """Remove a library feature or part from the Design Library."""
    p = Path(path).resolve()
    if _library_dir().resolve() not in p.parents:
        raise DocumentError("that file is not in the Design Library")
    p.unlink()
    return {"deleted": str(p)}


@command(category="library")
def insert_library_feature(name: str, plane: str | dict = "Top", offset: Vec2 | None = None) -> dict:
    """Insert a library feature into the active part: its sketches go on `plane` (a plane name or 'FaceN'),
    moved by offset [x, y] in sketch coordinates; features are renamed to fit this part."""
    from openswx.commands.support import _plane_spec
    path = Path(name) if name.endswith(".oswlib") else _library_dir() / f"{name}.oswlib"
    if not path.exists():
        raise DocumentError(f"no library feature {name!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = APP.doc
    target = _plane_spec(doc, plane)
    dx, dy = (float(v) for v in (offset or [0, 0]))
    rename = {f["name"]: doc._new_name(f["type"]) for f in data["features"]}

    def remap(v):
        if isinstance(v, str):
            head, sep, tail = v.partition("/")
            return rename[head] + sep + tail if head in rename else v
        if isinstance(v, dict):
            return {k: remap(x) for k, x in v.items()}
        if isinstance(v, list):
            return [remap(x) for x in v]
        return v
    added = []
    for f in data["features"]:
        p = remap(copy.deepcopy(f["params"]))
        if f["type"] == "sketch":
            p["plane"] = target
            for pt in p.get("sketch", {}).get("points", {}).values():
                pt[0] += dx
                pt[1] += dy
        doc.add_feature(f["type"], p, name=rename[f["name"]])
        added.append(rename[f["name"]])
    return {"inserted": added, "model": doc.summary()}


__all__ = ["save_library_feature", "list_design_library", "insert_library_feature",
           "delete_library_item", "add_part_to_design_library"]
