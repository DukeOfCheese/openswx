"""Simulation result probes and the study report (same registry as session.py, which re-exports them)."""
from __future__ import annotations

import base64
import datetime
import html
import math
from pathlib import Path

from .document import DocumentError
from .session import APP, Vec3, _sim_doc, command

__all__ = ["simulation_probe", "simulation_report"]


def _result():
    doc = _sim_doc()
    cached = getattr(APP, "sim_result", None)
    if not cached or cached[0] != doc.revision:
        raise DocumentError("run the static study first (simulation_run); the model changed since the last run")
    return doc, cached[1]


@command(mutates=False, category="simulation")
def simulation_probe(points: list[Vec3]) -> dict:
    """Probe the last static study at points in the model (mm): the von Mises stress (MPa), the displacement
    vector and its size (mm) at each, and the factor of safety against the material's yield strength there.
    Values are read from the solution's mesh at the point (inside the part; a point outside takes the nearest
    material)."""
    from .fea import map_to_mesh
    _doc, r = _result()
    if not points:
        raise DocumentError("give at least one point [x, y, z]")
    flat = [float(c) for p in points for c in p]
    if len(flat) != 3 * len(points):
        raise DocumentError("each point is [x, y, z]")
    out = map_to_mesh(r, {"faces": [{"id": "probe", "v": flat, "t": []}]})
    ys = r.get("yield_strength")
    rows = []
    for p, s, u in zip(points, out["stress"]["probe"], out["displacement"]["probe"]):
        rows.append({"point": [float(c) for c in p], "von_mises": s, "displacement": u,
                     "displacement_size": round(math.sqrt(sum(c * c for c in u)), 7),
                     "factor_of_safety": round(ys / s, 4) if ys and s > 1e-12 else None})
    return {"probes": rows, "units": {"stress": "MPa", "displacement": "mm"}}


def _table(rows: list[tuple]) -> str:
    return "<table>" + "".join(f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows) + "</table>"


def _num(v, digits: int = 4) -> str:
    return f"{v:.{digits}g}" if isinstance(v, (int, float)) else str(v)


@command(mutates=False, category="simulation")
def simulation_report(path: str, title: str | None = None, probes: list[Vec3] | None = None) -> dict:
    """Write the last static study up as a self-contained HTML report: the model (picture, material and its
    properties, mass), the study (fixtures and loads with where they act), the mesh, the results (maximum
    displacement and von Mises stress, factor of safety, reaction against applied load as a check) and the
    values at any `probes` points. The report says what kind of mesh the numbers come from."""
    from . import session as S
    doc, r = _result()
    mat = getattr(doc, "material", {}) or {}
    study = doc.study
    png = ""
    try:
        png = S.get_view_image.__wrapped__(view="isometric", width=900, height=600)["png_base64"]
        base64.b64decode(png[:16] + "=" * (-len(png[:16]) % 4))
    except Exception:  # noqa: BLE001 - a report without the picture is still a report
        png = ""

    def where(ref):
        return f"{ref.get('label') or ref.get('type', 'face')} at {[round(c, 3) for c in ref.get('point', [])]}"
    fixtures = [(f"Fixture {i + 1}", "fixed: " + where(f)) for i, f in enumerate(study.get("fixtures", []))]
    loads = []
    for i, ld in enumerate(study.get("loads", []), 1):
        what = ld.get("type", "load")
        value = ld.get("force") if ld.get("force") is not None else ld.get("magnitude", ld.get("pressure", ""))
        unit = "MPa" if what == "pressure" else "N"
        loads.append((f"Load {i}", f"{what} {value} {unit} on {where(ld.get('face', {}))}"))
    applied, reaction = r.get("applied") or [0, 0, 0], r.get("reaction") or [0, 0, 0]
    balance = math.sqrt(sum((a + b) ** 2 for a, b in zip(applied, reaction)))
    size = math.sqrt(sum(a * a for a in applied)) or 1.0
    fos = r.get("factor_of_safety")
    verdict = ("no yield strength for this material" if fos is None else
               f"factor of safety {fos:.3g}: " + ("below 1 - the part yields" if fos < 1 else "the part does not yield under this load"))
    parts = [f"<h1>{html.escape(title or doc.name + ' - static study')}</h1>",
             f"<p class='muted'>openswx {html.escape(__import__('openswx').__version__)}, {datetime.date.today().isoformat()}</p>"]
    if png:
        parts.append(f"<img alt='model' src='data:image/png;base64,{png}'>")
    parts += ["<h2>Model</h2>", _table([("Document", doc.name), ("Material", mat.get("name") or "(none)"),
                                        ("Elastic modulus", f"{_num(mat.get('elastic_modulus'))} MPa"),
                                        ("Poisson ratio", _num(mat.get("poisson_ratio"))),
                                        ("Yield strength", f"{_num(r.get('yield_strength'))} MPa"),
                                        ("Volume", f"{_num(r.get('mesh_volume'), 6)} mm3 (as meshed)")]),
              "<h2>Study</h2>", _table(fixtures + loads or [("", "no fixtures or loads recorded")]),
              "<h2>Mesh</h2>", _table([("Element size", f"{_num(r.get('element_size'))} mm"), ("Elements", r.get("elements")),
                                       ("Nodes", r.get("nodes")), ("Degrees of freedom", r.get("dofs"))]),
              "<p class='muted'>Voxel (finite-cell) hexahedra with incompatible modes: displacements and stresses in "
              "the bulk are reliable; peak stresses at curved boundaries and sharp corners are approximate. Halve the "
              "element size and compare before relying on a peak value.</p>",
              "<h2>Results</h2>", _table([("Maximum displacement", f"{_num(r.get('max_displacement'))} mm"),
                                          ("Maximum von Mises stress", f"{_num(r.get('max_von_mises'))} MPa"),
                                          ("Result", verdict),
                                          ("Applied load", f"{[round(a, 4) for a in applied]} N"),
                                          ("Reaction", f"{[round(a, 4) for a in reaction]} N"),
                                          ("Force balance", f"{100 * balance / size:.3g} % of the applied load unbalanced")])]
    probed = []
    if probes:
        probed = simulation_probe.__wrapped__(probes)["probes"]
        rows = "".join(f"<tr><td>{p['point']}</td><td>{_num(p['von_mises'])}</td><td>{_num(p['displacement_size'])}</td>"
                       f"<td>{_num(p['factor_of_safety']) if p['factor_of_safety'] else ''}</td></tr>" for p in probed)
        parts += ["<h2>Probes</h2>", "<table><tr><th>Point (mm)</th><th>von Mises (MPa)</th><th>Displacement (mm)</th>"
                                     f"<th>Factor of safety</th></tr>{rows}</table>"]
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>" + html.escape(title or doc.name + " - static study")
            + "</title><style>body{font:14px/1.5 system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em;color:#222}"
              "h1{font-size:22px}h2{font-size:16px;margin-top:1.6em;border-bottom:1px solid #ccc}table{border-collapse:collapse}"
              "th,td{text-align:left;padding:3px 14px 3px 0;vertical-align:top}th{font-weight:600}img{max-width:100%;border:1px solid #ddd}"
              ".muted{color:#666}</style><body>" + "".join(parts) + "</body></html>")
    p = Path(path)
    if p.suffix.lower() not in (".html", ".htm"):
        p = p.with_suffix(".html")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page, encoding="utf-8")
    return {"saved": str(p.resolve()), "factor_of_safety": fos, "force_balance_pct": round(100 * balance / size, 4), "probes": probed}
