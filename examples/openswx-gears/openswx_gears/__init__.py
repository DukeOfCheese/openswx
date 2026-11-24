"""Example plugin: a spur gear command."""
from __future__ import annotations

import math

from openswx.session import APP, command, create_sketch, extrude, sketch_circle, sketch_spline

__all__ = ["spur_gear"]

SAMPLES = 6  # points per involute flank
TIP_SAMPLES = 2  # points along each tooth tip
ROOT_SAMPLES = 1  # points along each root arc


def _tooth_profile(module: float, teeth: int, pressure_angle: float) -> list[list[float]]:
    """Closed outline points (mm, sketch XY) for an external gear with involute flanks."""
    pitch_r = module * teeth / 2
    outer_r = pitch_r + module
    root_r = max(0.05, pitch_r - 1.25 * module)
    base_r = pitch_r * math.cos(math.radians(pressure_angle))
    inv_p = math.tan(math.radians(pressure_angle)) - math.radians(pressure_angle)

    def half_angle(r: float) -> float:
        """Angular half-thickness of a tooth at radius r, measured from the tooth's centre line."""
        a = math.acos(max(-1.0, min(1.0, base_r / r)))
        return math.pi / (2 * teeth) + inv_p - (math.tan(a) - a)

    if half_angle(outer_r) <= 0:
        raise ValueError("too few teeth for an involute gear at this pressure angle")

    start_r = max(root_r, base_r)
    points: list[list[float]] = []
    for k in range(teeth):
        base = 2 * math.pi * k / teeth
        # left flank, root to tip
        for i in range(SAMPLES + 1):
            r = start_r + (outer_r - start_r) * i / SAMPLES
            a = base - half_angle(r)
            points.append([r * math.cos(a), r * math.sin(a)])
        # tip arc, left to right
        for i in range(1, TIP_SAMPLES + 1):
            a = base - half_angle(outer_r) + 2 * half_angle(outer_r) * i / (TIP_SAMPLES + 1)
            points.append([outer_r * math.cos(a), outer_r * math.sin(a)])
        # right flank, tip to root
        for i in range(SAMPLES + 1):
            r = outer_r - (outer_r - start_r) * i / SAMPLES
            a = base + half_angle(r)
            points.append([r * math.cos(a), r * math.sin(a)])
        # root arc towards the next tooth
        first = base + half_angle(start_r)
        last = base + 2 * math.pi / teeth - half_angle(start_r)
        for i in range(1, ROOT_SAMPLES + 1):
            a = first + (last - first) * i / (ROOT_SAMPLES + 1)
            points.append([root_r * math.cos(a), root_r * math.sin(a)])
    return points


@command(category="gears")
def spur_gear(module: float, teeth: int, width: float, bore: float = 0.0,
              pressure_angle: float = 20.0) -> dict:
    """Build an external spur gear as a new part in the active document.

    module: gear module in mm (pitch diameter = module * teeth). teeth: number of teeth (6 or more). width:
    face width in mm. bore: central bore diameter in mm (0: no bore). pressure_angle: pressure angle in
    degrees (20 is standard). The involute flanks are sampled and interpolated with a closed spline, so the
    tooth tips are slightly rounded. Returns the feature name, the measured volume (mm3) and the pitch,
    outside and root diameters (mm).
    """
    module, width, bore, pressure_angle = float(module), float(width), float(bore), float(pressure_angle)
    teeth = int(teeth)
    if module <= 0:
        raise ValueError("module must be positive (mm)")
    if teeth < 6:
        raise ValueError("teeth must be at least 6")
    if width <= 0:
        raise ValueError("width must be positive (mm)")
    if bore < 0:
        raise ValueError("bore must be 0 or a positive diameter (mm)")
    if not 10.0 <= pressure_angle <= 35.0:
        raise ValueError("pressure_angle must be between 10 and 35 degrees")

    pitch_r = module * teeth / 2
    root_r = max(0.05, pitch_r - 1.25 * module)
    if bore and bore / 2 >= root_r:
        raise ValueError(f"a {bore:g} mm bore leaves no material inside a {2 * root_r:g} mm root circle")

    profile = _tooth_profile(module, teeth, pressure_angle)
    name = create_sketch("Top")["sketch"]
    sketch_spline(name, profile, closed=True)
    if bore:
        sketch_circle(name, [0.0, 0.0], bore / 2)
    feature = extrude(name, depth=width)["feature"]

    return {
        "feature": feature,
        "volume_mm3": float(APP.doc.body.volume),
        "module": module,
        "teeth": teeth,
        "width": width,
        "pitch_diameter": 2 * pitch_r,
        "outer_diameter": 2 * (pitch_r + module),
        "root_diameter": 2 * root_r,
        "bore_diameter": bore,
    }
