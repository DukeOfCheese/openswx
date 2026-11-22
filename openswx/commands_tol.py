"""Dimension tolerance commands (same registry as session.py, which re-exports them)."""
from __future__ import annotations

from typing import Literal

from . import tolerance as T
from .document import DocumentError
from .session import APP, command

__all__ = ["set_dimension_tolerance", "get_dimension_tolerances", "fit_calculator"]


def _entry(doc, name: str, tol: dict | None) -> dict:
    value = doc.dim_values.get(name)
    out = {"dimension": name, "value": value, "tolerance": tol or {"type": "none"}}
    if tol and value is not None:
        try:
            lo, hi = T.deviations(value, tol)
            out.update(lower=round(lo, 6), upper=round(hi, 6), min=round(value + lo, 6), max=round(value + hi, 6),
                       text=T.text(f"{value:g}", value, tol))
        except T.ToleranceError as exc:
            out["error"] = str(exc)
    return out


@command(category="edit")
def set_dimension_tolerance(dimension: str, type: Literal["none", "symmetric", "bilateral", "limit", "fit"] = "symmetric",
                            plus: float = 0.0, minus: float = 0.0, fit: str | None = None) -> dict:
    """Tolerance on a model dimension ('D1@Sketch1', 'D1@Boss-Extrude1'; names from get_equations).
    symmetric: +/- plus. bilateral: +plus / -minus. limit: shown as the two limits. fit: an ISO 286 class such
    as 'H7' (hole) or 'g6' (shaft), its deviations taken from the dimension's size. none: remove it.
    The tolerance is saved with the part, shown on the dimension in drawings (Model Items) and listed by
    get_dimension_tolerances with the resulting limits. Values in mm (degrees on an angle)."""
    doc = APP.doc
    if dimension not in doc.dim_values:
        near = [d for d in doc.dim_values if d.split("@")[-1] == dimension.split("@")[-1]][:8]
        raise DocumentError(f"no dimension {dimension!r}" + (f"; that feature has {near}" if near else "; see get_equations"))
    try:
        tol = T.normalize({"type": type, "plus": plus, "minus": minus, "fit": fit})
        if tol is not None:
            T.deviations(doc.dim_values[dimension], tol)  # (a fit must exist at this size)
    except T.ToleranceError as exc:
        raise DocumentError(str(exc)) from exc
    doc._checkpoint()
    tols = dict(getattr(doc, "tolerances", {}))
    if tol is None:
        tols.pop(dimension, None)
    else:
        tols[dimension] = tol
    doc.tolerances = tols
    doc.dirty = True
    doc.revision += 1
    return _entry(doc, dimension, tol)


@command(mutates=False, category="query")
def get_dimension_tolerances(all_dimensions: bool = False) -> dict:
    """The model dimensions that carry a tolerance, each with its value, deviations, limits and display text
    (all_dimensions=True also lists the untoleranced ones). A tolerance whose dimension no longer exists is
    reported under `orphaned`."""
    doc = APP.doc
    tols = getattr(doc, "tolerances", {})
    names = list(doc.dim_values) if all_dimensions else [n for n in doc.dim_values if n in tols]
    return {"dimensions": [_entry(doc, n, tols.get(n)) for n in names],
            "orphaned": sorted(n for n in tols if n not in doc.dim_values)}


@command(mutates=False, category="query")
def fit_calculator(size: float, hole: str = "H7", shaft: str = "g6") -> dict:
    """ISO 286 fit of a hole and a shaft at a nominal size (mm): both parts' deviations and limits, the
    minimum and maximum clearance (negative = interference) and the kind of fit (clearance / transition /
    interference). Classes: holes D E F G H JS K M N P, shafts d e f g h js k m n p, grades 5-12, up to 500 mm."""
    try:
        return T.fit_summary(float(size), hole, shaft)
    except T.ToleranceError as exc:
        raise DocumentError(str(exc)) from exc
