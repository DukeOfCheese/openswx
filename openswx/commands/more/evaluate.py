"""Evaluate: costing and sustainability reports."""
from __future__ import annotations

from typing import Literal

from openswx.core.document import DocumentError
from openswx.session import APP, command


@command(mutates=False, category="evaluate")
def costing(method: Literal["machining", "sheet_metal", "additive", "casting"] = "machining", quantity: int = 1,
            rate_per_hour: float = 80.0, setup_hours: float = 1.0, price_per_kg: float | None = None) -> dict:
    """Costing: should-cost estimate of the active part (USD, generic rates - set the shop rate and material
    price per kg for your own): machining from stock, sheet metal (blank, cutting, bends), additive or
    casting, with a per-item breakdown and the setup / tooling shared over the quantity."""
    from openswx.analysis.analysis_more import cost_estimate
    try:
        return cost_estimate(APP.doc, method, quantity, rate_per_hour, setup_hours, price_per_kg)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="evaluate")
def sustainability_report(region: Literal["Asia", "Europe", "North America", "Oceania", "South America"] = "Oceania",
                          process: Literal["machining", "casting", "sheet_metal", "additive"] = "machining",
                          transport_km: float = 1000.0, recycled_fraction: float = 0.0) -> dict:
    """Sustainability: carbon footprint (kg CO2e), total energy (MJ) and water (L) of the active part by
    life-cycle phase - material, manufacturing (on the region's grid), transport, end of life - from generic
    published figures for its material class."""
    from openswx.analysis.analysis_more import sustainability
    try:
        return sustainability(APP.doc, region, process, transport_km, recycled_fraction)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


__all__ = ["costing", "sustainability_report"
]
