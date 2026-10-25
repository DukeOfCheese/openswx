"""Material library: typical published engineering properties (SI: kg/m^3, MPa, W/(m K), J/(kg K)) plus a default
appearance colour. Values are generic handbook figures for common grades, adequate for mass properties and
first-pass linear static analysis; check a supplier datasheet for design-critical work."""
from __future__ import annotations

# name: (category, density, E [MPa], poisson, yield [MPa], tensile [MPa], conductivity, colour,
#        specific heat [J/(kg K)])
_TABLE = {
    "AISI 1020": ("Steel", 7900, 200e3, 0.29, 351.6, 420.5, 47.0, "#8e9399", 420),
    "Plain Carbon Steel": ("Steel", 7800, 210e3, 0.28, 220.6, 399.8, 43.0, "#8a8f96", 440),
    "AISI 1045": ("Steel", 7850, 205e3, 0.29, 530.0, 625.0, 49.8, "#8b9097", 486),
    "AISI 4340 Normalized": ("Steel", 7850, 205e3, 0.32, 710.0, 1110.0, 44.5, "#878c94", 475),
    "AISI 304": ("Stainless Steel", 8000, 190e3, 0.29, 206.8, 517.0, 16.0, "#b4b8bd", 500),
    "AISI 316": ("Stainless Steel", 8000, 193e3, 0.27, 205.0, 515.0, 16.3, "#b0b4ba", 500),
    "Gray Cast Iron": ("Iron", 7200, 66.2e3, 0.27, 0.0, 151.7, 45.0, "#6f7377", 510),
    "Ductile Iron": ("Iron", 7100, 120e3, 0.31, 275.0, 413.0, 75.0, "#73777c", 506),
    "1060 Alloy": ("Aluminium Alloys", 2700, 69e3, 0.33, 27.6, 68.9, 200.0, "#c9cdd2", 900),
    "6061-T6 (SS)": ("Aluminium Alloys", 2700, 69e3, 0.33, 275.0, 310.0, 167.0, "#c6cad0", 896),
    "6061 Alloy": ("Aluminium Alloys", 2700, 69e3, 0.33, 55.1, 124.1, 170.0, "#c6cad0", 896),
    "7075-T6 (SN)": ("Aluminium Alloys", 2810, 72e3, 0.33, 505.0, 570.0, 130.0, "#c2c6cc", 960),
    "Brass": ("Copper Alloys", 8500, 100e3, 0.33, 239.4, 478.4, 110.0, "#c9a94a", 390),
    "Copper": ("Copper Alloys", 8900, 110e3, 0.37, 258.6, 394.4, 390.0, "#c17a4a", 385),
    "Bronze": ("Copper Alloys", 8800, 110e3, 0.34, 125.0, 310.0, 50.0, "#a8783a", 370),
    "Ti-6Al-4V": ("Titanium Alloys", 4430, 114e3, 0.34, 880.0, 950.0, 6.7, "#9a9a9f", 526),
    "ABS": ("Plastics", 1020, 2.0e3, 0.394, 40.0, 30.0, 0.2256, "#e8e3d6", 1386),
    "PLA": ("Plastics", 1250, 3.5e3, 0.36, 60.0, 50.0, 0.13, "#f2efe6", 1800),
    "PC High Viscosity": ("Plastics", 1200, 2.32e3, 0.39, 62.0, 63.0, 0.2, "#dfe6ea", 1250),
    "Nylon 6/10": ("Plastics", 1400, 8.3e3, 0.28, 139.0, 142.0, 0.53, "#ebe6da", 1500),
    "Acetal (POM)": ("Plastics", 1420, 2.8e3, 0.35, 65.0, 70.0, 0.31, "#f4f4f0", 1460),
    "Acrylic (Medium-high impact)": ("Plastics", 1200, 3.0e3, 0.35, 45.0, 73.0, 0.21, "#cfe3ea", 1470),
    "PTFE": ("Plastics", 2200, 0.5e3, 0.46, 9.0, 25.0, 0.25, "#f7f7f7", 1050),
    "Natural Rubber": ("Rubber", 1000, 6.1, 0.49, 0.0, 13.8, 0.14, "#2b2b2b", 1800),
    "Oak": ("Woods", 700, 11e3, 0.35, 0.0, 90.0, 0.17, "#a77b46", 2000),
    "Glass": ("Other", 2500, 70e3, 0.22, 0.0, 33.0, 1.0, "#bcd9e0", 840),
}

FIELDS = ("category", "density", "elastic_modulus", "poisson", "yield_strength", "tensile_strength",
          "thermal_conductivity", "color", "specific_heat")


CUSTOM_CATEGORY = "Custom Materials"


def _custom_path():
    """The user's custom material library (as SOLIDWORKS keeps custom materials apart from its own library)."""
    import os
    from pathlib import Path
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "custom_materials.json"


def custom_materials() -> dict[str, dict]:
    import json
    try:
        data = json.loads(_custom_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {n: {**{f: None for f in FIELDS}, **m, "category": CUSTOM_CATEGORY} for n, m in data.items() if isinstance(m, dict)}


def save_custom_material(name: str, props: dict) -> dict:
    """Add or replace a material in the custom library; returns it."""
    import json
    name = name.strip()
    if not name:
        raise ValueError("a custom material needs a name")
    if name.lower() in {n.lower() for n in _TABLE}:
        raise ValueError(f"{name!r} is a library material: give the custom material another name")
    if not props.get("density") or float(props["density"]) <= 0:
        raise ValueError("a custom material needs a positive density")
    data = {n: {k: v for k, v in m.items() if k != "category"} for n, m in custom_materials().items()}
    data[name] = {f: props.get(f) for f in FIELDS if f != "category" and props.get(f) is not None}
    path = _custom_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return {**data[name], "category": CUSTOM_CATEGORY}


def delete_custom_material(name: str) -> None:
    import json
    data = {n: {k: v for k, v in m.items() if k != "category"} for n, m in custom_materials().items()}
    if name not in data:
        raise ValueError(f"no custom material {name!r}")
    del data[name]
    _custom_path().write_text(json.dumps(data, indent=1), encoding="utf-8")


def library() -> dict[str, dict]:
    return {**{n: dict(zip(FIELDS, row)) for n, row in _TABLE.items()}, **custom_materials()}


def lookup(name: str) -> dict | None:
    key = {n.lower(): n for n in _TABLE}.get(name.strip().lower())
    if key is not None:
        return {"name": key, **dict(zip(FIELDS, _TABLE[key]))}
    custom = {n.lower(): (n, m) for n, m in custom_materials().items()}.get(name.strip().lower())
    return None if custom is None else {"name": custom[0], **custom[1]}
