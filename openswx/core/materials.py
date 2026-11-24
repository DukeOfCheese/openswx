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
    "AISI 1010 (hot rolled)": ("Steel", 7870, 200e3, 0.29, 180.0, 325.0, 65.2, "#8e9399", 450),
    "AISI 1018 (cold drawn)": ("Steel", 7870, 205e3, 0.29, 370.0, 440.0, 51.9, "#8d9298", 486),
    "AISI 1040 (cold drawn)": ("Steel", 7845, 200e3, 0.29, 490.0, 585.0, 50.7, "#8b9097", 486),
    "AISI 1095 (annealed)": ("Steel", 7850, 205e3, 0.29, 380.0, 655.0, 49.8, "#878c93", 461),
    "AISI 4130 (normalized)": ("Steel", 7850, 205e3, 0.29, 460.0, 670.0, 42.7, "#898e95", 477),
    "AISI 4140 (annealed)": ("Steel", 7850, 205e3, 0.29, 415.0, 655.0, 42.6, "#888d94", 473),
    "AISI 4140 (quenched and tempered)": ("Steel", 7850, 205e3, 0.29, 655.0, 1020.0, 42.6, "#868b92", 473),
    "ASTM A36": ("Steel", 7850, 200e3, 0.26, 250.0, 400.0, 51.9, "#8e9399", 486),
    "S235JR": ("Steel", 7850, 210e3, 0.30, 235.0, 360.0, 50.0, "#8e9399", 460),
    "S355JR": ("Steel", 7850, 210e3, 0.30, 355.0, 510.0, 50.0, "#8c9197", 460),
    "AISI O1 Tool Steel (annealed)": ("Steel", 7810, 214e3, 0.30, 345.0, 650.0, 32.0, "#7f848b", 460),
    "AISI H13 Tool Steel (annealed)": ("Steel", 7800, 210e3, 0.30, 370.0, 670.0, 24.3, "#7e838a", 460),
    "AISI 303": ("Stainless Steel", 8000, 193e3, 0.25, 240.0, 620.0, 16.2, "#b2b6bb", 500),
    "AISI 304L": ("Stainless Steel", 8000, 193e3, 0.29, 170.0, 485.0, 16.2, "#b4b8bd", 500),
    "AISI 316L": ("Stainless Steel", 8000, 193e3, 0.27, 170.0, 485.0, 16.3, "#b0b4ba", 500),
    "AISI 410 (annealed)": ("Stainless Steel", 7740, 200e3, 0.28, 275.0, 485.0, 24.9, "#a9adb3", 460),
    "AISI 420 (annealed)": ("Stainless Steel", 7800, 200e3, 0.28, 345.0, 655.0, 24.9, "#a7abb1", 460),
    "AISI 430": ("Stainless Steel", 7750, 200e3, 0.28, 205.0, 450.0, 26.1, "#adb1b7", 460),
    "17-4 PH (H900)": ("Stainless Steel", 7750, 196e3, 0.27, 1170.0, 1310.0, 18.3, "#a5a9af", 460),
    "Duplex 2205": ("Stainless Steel", 7800, 200e3, 0.30, 450.0, 655.0, 19.0, "#aeb2b8", 500),
    "1100-H14": ("Aluminium Alloys", 2710, 69e3, 0.33, 105.0, 125.0, 218.0, "#cacdd2", 904),
    "2014-T6": ("Aluminium Alloys", 2800, 72.4e3, 0.33, 414.0, 483.0, 154.0, "#c3c7cd", 880),
    "2024-T3": ("Aluminium Alloys", 2780, 73.1e3, 0.33, 345.0, 483.0, 121.0, "#c2c6cc", 875),
    "3003-H14": ("Aluminium Alloys", 2730, 69e3, 0.33, 145.0, 150.0, 159.0, "#c9ccd1", 893),
    "5052-H32": ("Aluminium Alloys", 2680, 70.3e3, 0.33, 193.0, 228.0, 138.0, "#c8ccd1", 880),
    "5083-H116": ("Aluminium Alloys", 2660, 71e3, 0.33, 215.0, 305.0, 117.0, "#c7cbd0", 900),
    "6063-T5": ("Aluminium Alloys", 2700, 68.9e3, 0.33, 145.0, 186.0, 209.0, "#c8ccd1", 900),
    "6082-T6": ("Aluminium Alloys", 2700, 70e3, 0.33, 260.0, 310.0, 180.0, "#c6cad0", 894),
    "A356-T6 (cast)": ("Aluminium Alloys", 2685, 72.4e3, 0.33, 186.0, 262.0, 151.0, "#bfc3c9", 963),
    "AlSi10Mg (additive, stress relieved)": ("Aluminium Alloys", 2670, 70e3, 0.33, 230.0, 345.0, 160.0, "#bcc0c6", 915),
    "Copper C110 (annealed)": ("Copper Alloys", 8890, 115e3, 0.31, 69.0, 220.0, 388.0, "#c17a4a", 385),
    "Brass C260 (half hard)": ("Copper Alloys", 8530, 110e3, 0.375, 360.0, 420.0, 120.0, "#cdab4c", 375),
    "Brass C360 (free cutting)": ("Copper Alloys", 8500, 97e3, 0.31, 310.0, 385.0, 115.0, "#c9a94a", 380),
    "Bearing Bronze C932": ("Copper Alloys", 8930, 100e3, 0.34, 125.0, 240.0, 58.0, "#a8783a", 380),
    "Phosphor Bronze C510 (hard)": ("Copper Alloys", 8860, 110e3, 0.34, 515.0, 560.0, 84.0, "#b0803f", 380),
    "Beryllium Copper C17200 (aged)": ("Copper Alloys", 8250, 128e3, 0.30, 1100.0, 1250.0, 105.0, "#c48a55", 420),
    "Titanium Grade 2 (CP)": ("Titanium Alloys", 4510, 105e3, 0.37, 275.0, 345.0, 16.4, "#9d9da2", 523),
    "Inconel 718 (aged)": ("Nickel Alloys", 8190, 200e3, 0.29, 1030.0, 1240.0, 11.4, "#9b9a95", 435),
    "Inconel 625 (annealed)": ("Nickel Alloys", 8440, 205.8e3, 0.31, 460.0, 880.0, 9.8, "#9c9b96", 410),
    "Monel 400 (annealed)": ("Nickel Alloys", 8800, 179e3, 0.32, 240.0, 550.0, 21.8, "#a09f99", 427),
    "Magnesium AZ31B-H24": ("Magnesium Alloys", 1770, 45e3, 0.35, 200.0, 260.0, 96.0, "#cfd2d4", 1000),
    "Magnesium AZ91D (cast)": ("Magnesium Alloys", 1810, 45e3, 0.35, 160.0, 230.0, 72.7, "#cbced0", 1000),
    "Zamak 3 (die cast zinc)": ("Zinc Alloys", 6600, 96e3, 0.27, 221.0, 283.0, 113.0, "#b9bcbf", 419),
    "Nylon 6 (PA6, dry)": ("Plastics", 1130, 2.8e3, 0.39, 70.0, 80.0, 0.25, "#ece7db", 1700),
    "Nylon 66 (PA66, dry)": ("Plastics", 1140, 3.0e3, 0.39, 80.0, 85.0, 0.25, "#ebe6d9", 1700),
    "Nylon 66 30% glass (PA66-GF30, dry)": ("Plastics", 1360, 9.5e3, 0.35, 0.0, 180.0, 0.30, "#e2ddd0", 1500),
    "Nylon 12 (PA12, SLS)": ("Plastics", 1010, 1.7e3, 0.40, 0.0, 48.0, 0.22, "#eeeae1", 1600),
    "PEEK": ("Plastics", 1300, 3.6e3, 0.40, 100.0, 100.0, 0.25, "#d8c7a4", 1340),
    "PEI (Ultem 1000)": ("Plastics", 1270, 3.2e3, 0.36, 105.0, 110.0, 0.22, "#d9a55b", 1460),
    "PPS": ("Plastics", 1350, 3.8e3, 0.37, 0.0, 85.0, 0.29, "#d5ccb9", 1100),
    "PSU (polysulfone)": ("Plastics", 1240, 2.5e3, 0.37, 70.0, 70.0, 0.26, "#e6d9b4", 1000),
    "Polypropylene (PP, homopolymer)": ("Plastics", 905, 1.5e3, 0.42, 32.0, 35.0, 0.22, "#f0efe8", 1920),
    "HDPE": ("Plastics", 955, 1.0e3, 0.42, 26.0, 30.0, 0.48, "#f2f2ee", 1900),
    "LDPE": ("Plastics", 920, 0.2e3, 0.45, 10.0, 12.0, 0.33, "#f4f4f1", 2300),
    "PVC (rigid)": ("Plastics", 1400, 3.0e3, 0.38, 45.0, 50.0, 0.16, "#dfe2e4", 1000),
    "PET": ("Plastics", 1380, 2.8e3, 0.40, 55.0, 60.0, 0.24, "#e3ecef", 1200),
    "PETG": ("Plastics", 1270, 2.1e3, 0.38, 50.0, 53.0, 0.20, "#dfeaee", 1200),
    "PBT": ("Plastics", 1310, 2.5e3, 0.40, 55.0, 55.0, 0.25, "#ebe8df", 1300),
    "ASA": ("Plastics", 1070, 2.3e3, 0.38, 45.0, 45.0, 0.17, "#e9e5da", 1300),
    "HIPS": ("Plastics", 1040, 1.9e3, 0.40, 25.0, 28.0, 0.17, "#f0eee8", 1300),
    "TPU (Shore 95A)": ("Rubber", 1200, 26.0, 0.48, 0.0, 40.0, 0.20, "#3a3a3a", 1800),
    "Silicone Rubber": ("Rubber", 1100, 5.0, 0.49, 0.0, 8.0, 0.20, "#d94a3a", 1300),
    "Neoprene": ("Rubber", 1230, 5.0, 0.49, 0.0, 15.0, 0.19, "#262626", 2000),
    "CFRP (quasi-isotropic laminate)": ("Composites", 1550, 50e3, 0.30, 0.0, 600.0, 5.0, "#2e3134", 1000),
    "GFRP (quasi-isotropic laminate)": ("Composites", 1900, 20e3, 0.30, 0.0, 300.0, 0.40, "#d8dcc8", 1100),
    "Pine (along the grain)": ("Woods", 500, 9e3, 0.35, 0.0, 80.0, 0.12, "#d8b47a", 1600),
    "Birch Plywood": ("Woods", 680, 9e3, 0.30, 0.0, 40.0, 0.15, "#d9b98a", 1700),
    "Balsa": ("Woods", 160, 3e3, 0.30, 0.0, 14.0, 0.05, "#e7d3a6", 2900),
    "MDF": ("Woods", 750, 3.5e3, 0.25, 0.0, 18.0, 0.12, "#b08a5c", 1700),
    "Concrete": ("Other", 2400, 30e3, 0.20, 0.0, 3.0, 1.7, "#a9a9a4", 880),
    "Borosilicate Glass": ("Other", 2230, 64e3, 0.20, 0.0, 40.0, 1.14, "#cfe4ea", 830),
    "Alumina (96%)": ("Ceramics", 3720, 300e3, 0.21, 0.0, 200.0, 25.0, "#f1efe9", 880),
    "Silicon Carbide": ("Ceramics", 3100, 410e3, 0.14, 0.0, 300.0, 120.0, "#3f4145", 750),
    "Tungsten Carbide (WC-Co)": ("Ceramics", 15000, 600e3, 0.22, 0.0, 1500.0, 85.0, "#6d7176", 200),
    "Lead": ("Other", 11340, 16e3, 0.44, 5.0, 17.0, 35.0, "#7a7f86", 128),
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
