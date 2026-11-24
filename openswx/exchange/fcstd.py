"""Read the geometry of a FreeCAD document (.FCStd).

A .FCStd file is a zip archive: `Document.xml` lists the objects and, for each with a shape, the name of a
B-rep file in the archive (OpenCASCADE's own .brp format); `GuiDocument.xml` says which objects are shown.
FreeCAD keeps the shape of every step of the model, so the bodies to import are the visible ones - the
results - not all of them. Without a GuiDocument.xml (a file saved without the GUI) the shapes no other
object is built from are taken.

Only the geometry comes across: FreeCAD's feature history is not rebuilt (the body arrives as an Imported
feature, like a STEP file). Written from the documented file layout and tested with archives made in that
layout; it has not been run against files saved by FreeCAD itself.
"""
from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


class FcstdError(ValueError):
    pass


def _bool_property(obj, name: str) -> bool | None:
    for prop in obj.iter("Property"):
        if prop.get("name") == name:
            b = prop.find("Bool")
            if b is not None:
                return b.get("value", "").lower() == "true"
    return None


def shape_files(path: str) -> list[tuple[str, str]]:
    """[(object name, B-rep file in the archive)] of the bodies to import."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FcstdError(f"not a FreeCAD document: {exc}") from exc
    with z:
        names = set(z.namelist())
        if "Document.xml" not in names:
            raise FcstdError("not a FreeCAD document (no Document.xml inside)")
        try:
            doc = ET.fromstring(z.read("Document.xml"))
        except ET.ParseError as exc:
            raise FcstdError(f"unreadable Document.xml: {exc}") from exc
        shapes, used = {}, set()
        data = doc.find("ObjectData")
        for obj in (data.findall("Object") if data is not None else []):
            for prop in obj.findall("./Properties/Property"):
                part = prop.find("Part")
                if prop.get("name") == "Shape" and part is not None and part.get("file") in names:
                    shapes[obj.get("name")] = part.get("file")
                for link in prop.iter("Link"):  # what this object is built from
                    if link.get("value"):
                        used.add(link.get("value"))
                for link in prop.iter("LinkSub"):
                    if link.get("value"):
                        used.add(link.get("value"))
        if not shapes:
            raise FcstdError("the document holds no shapes")
        visible = None
        if "GuiDocument.xml" in names:
            try:
                gui = ET.fromstring(z.read("GuiDocument.xml"))
                visible = {vp.get("name") for vp in gui.iter("ViewProvider") if _bool_property(vp, "Visibility")}
            except ET.ParseError:
                visible = None
        if visible:
            chosen = [n for n in shapes if n in visible]
        else:
            chosen = [n for n in shapes if n not in used]
        chosen = chosen or list(shapes)[-1:]
        return [(n, shapes[n]) for n in chosen]


def read_fcstd(path: str):
    """(shape, notes): the visible solid bodies of a FreeCAD document as one shape."""
    from build123d import Compound, import_brep
    picked = shape_files(path)
    solids, skipped = [], []
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory(prefix="openswx_fc_") as tmp:
        for i, (name, member) in enumerate(picked):
            f = Path(tmp) / f"shape{i}.brp"  # (a name of our own: nothing from the archive is used as a path)
            f.write_bytes(z.read(member))
            try:
                shape = import_brep(str(f))
            except Exception as exc:  # noqa: BLE001 - one unreadable shape does not lose the others
                skipped.append(f"{name}: {exc}")
                continue
            got = shape.solids()
            if got:
                solids += list(got)
            else:
                skipped.append(f"{name}: no solid (a sketch, surface or datum)")
    if not solids:
        raise FcstdError("no solid bodies among the document's visible shapes"
                         + (": " + "; ".join(skipped[:4]) if skipped else ""))
    notes = [f"FreeCAD document: {len(solids)} solid(s) from {', '.join(n for n, _ in picked)}; the feature history is not imported"]
    if skipped:
        notes.append("left out: " + "; ".join(skipped[:6]))
    return (solids[0] if len(solids) == 1 else Compound(solids)), notes
