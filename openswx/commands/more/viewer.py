"""Single-file HTML viewer payload and eDrawings export."""
from __future__ import annotations

from pathlib import Path

from openswx.core.document import DocumentError
from openswx.session import APP, command


def viewer_payload(doc) -> dict:
    """The model for the single-file HTML viewer: faces / edges (per component in an assembly), properties, PMI."""
    from openswx.core.features import mass_properties
    if doc.body is None:
        raise DocumentError("the document has no body")
    mesh = doc.mesh()
    asm = getattr(doc, "kind", "part") == "assembly"
    colors = {}
    if asm:
        for c in doc.components:
            colors[c.name] = ((getattr(c.doc(doc.live_docs), "material", {}) or {}).get("color")) if not c.is_subassembly else None
    faces = []
    for f in mesh["faces"]:
        comp = f.get("component")
        faces.append({"v": f["v"], "t": f["t"], "color": f.get("color") or colors.get(comp), "component": comp,
                      "envelope": f.get("envelope", False)})
    edges = [{"p": e["p"], "component": e["id"].split("/")[0] if asm else None} for e in mesh["edges"]]
    mat = getattr(doc, "material", {}) or {}
    props = {"Name": doc.name, "Type": "Assembly" if asm else "Part"}
    if not asm:
        mp = mass_properties(doc.body, mat.get("density") or 1000.0)
        props.update({"Material": mat.get("name") or "-", "Mass": f"{mp['mass'] / 1000:.4g} kg",
                      "Volume": f"{mp['volume']:.6g} mm³"})
    props.update({k: str(v) for k, v in (getattr(doc, "properties", {}) or {}).items()})
    return {"name": doc.name, "units": getattr(doc, "units", "mm"), "color": mat.get("color"), "faces": faces,
            "edges": edges, "properties": props, "components": [c.name for c in doc.components] if asm else [],
            "pmi": [] if asm else list(getattr(doc, "pmi", []) or [])}


def write_viewer(path: str, title: str, payload: dict) -> Path:
    from openswx.manufacturing.ecosystem import viewer_html
    three = (Path(__file__).parents[2] / "web" / "vendor" / "three.module.min.js").read_text(encoding="utf-8")
    p = Path(path)
    if p.suffix.lower() not in (".html", ".htm"):
        p = p.with_suffix(".html")
    p.write_text(viewer_html(title, payload, three), encoding="utf-8")
    return p


@command(mutates=False, category="file")
def export_edrawing(path: str) -> dict:
    """Publish an eDrawings-style viewer: one self-contained .html file (no install needed; opens in any
    browser) with the model, its properties and PMI, rotate / pan / zoom, standard views, section, measure,
    component visibility and markup (pen and notes, saved as PNG / JSON)."""
    doc = APP.active_doc
    payload = viewer_payload(doc)
    p = write_viewer(path, doc.name, payload)
    return {"path": str(p.resolve()), "faces": len(payload["faces"]), "size_bytes": p.stat().st_size}


__all__ = ["export_edrawing"
]
