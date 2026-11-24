from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path


class FcstdError(ValueError):
    pass


def _open(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FcstdError(f"not a FreeCAD document: {exc}") from exc


def _read_member(z: zipfile.ZipFile, name: str) -> bytes:
    """A member's bytes: zipfile's own errors (bad CRC, corrupt stream, encrypted member) become FcstdError."""
    try:
        return z.read(name)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, zlib.error, RuntimeError) as exc:
        raise FcstdError(f"unreadable {name} in the archive: {exc}") from exc


def _bool_property(obj, name: str) -> bool | None:
    for prop in obj.iter("Property"):
        if prop.get("name") == name:
            b = prop.find("Bool")
            if b is not None:
                return b.get("value", "").lower() == "true"
    return None


def shape_files(path: str) -> list[tuple[str, str]]:
    """[(object name, B-rep file in the archive)] of the bodies to import."""
    with _open(path) as z:
        names = set(z.namelist())
        if "Document.xml" not in names:
            raise FcstdError("not a FreeCAD document (no Document.xml inside)")
        try:
            doc = ET.fromstring(_read_member(z, "Document.xml"))
        except ET.ParseError as exc:
            raise FcstdError(f"unreadable Document.xml: {exc}") from exc
        shapes, used = {}, set()
        data = doc.find("ObjectData")
        for obj in (data.findall("Object") if data is not None else []):
            name = obj.get("name")
            for prop in obj.findall("./Properties/Property"):
                part = prop.find("Part")
                # an object with no name cannot be reported: skip its shape, but keep the links it uses
                if name and prop.get("name") == "Shape" and part is not None and part.get("file") in names:
                    shapes[name] = part.get("file")
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
                gui = ET.fromstring(_read_member(z, "GuiDocument.xml"))
                visible = {vp.get("name") for vp in gui.iter("ViewProvider") if _bool_property(vp, "Visibility")}
            except (ET.ParseError, FcstdError):  # (no usable visibility list: fall back to the links)
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
    with _open(path) as z, tempfile.TemporaryDirectory(prefix="openswx_fc_") as tmp:
        for i, (name, member) in enumerate(picked):
            f = Path(tmp) / f"shape{i}.brp"  # (a name of our own: nothing from the archive is used as a path)
            try:
                f.write_bytes(_read_member(z, member))
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
