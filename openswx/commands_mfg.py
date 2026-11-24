"""Manufacturing and documentation commands (registered with session.py's @command registry, which
re-exports them): CAM toolpaths and G-code, the plastics fill analysis, Inspection (characteristics, results
and first-article reports), electrical harness routing and Composer-style animated instructions."""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Literal

import numpy as np

from .document import DocumentError
from .session import APP, Vec3, command, unlocked

__all__ = ["cam_generate", "cam_finish_3d", "cam_gcode", "cam_toolpaths", "plastics_materials", "plastics_fill", "plastics_gate_advisor", "plastics_results",
           "inspection_characteristics", "inspection_set", "inspection_settings", "inspection_report",
           "electrical_connector", "electrical_route_segment", "electrical_wires", "electrical_import_wires",
           "electrical_harness", "electrical_formboard", "electrical_clear", "electrical_info",
           "composer_auto", "composer_add_step", "composer_steps", "composer_clear", "composer_export"]


def _part():
    doc = APP.active_doc
    if getattr(doc, "kind", "part") == "assembly":
        raise DocumentError("this runs on a part: open the part")
    if doc.body is None:
        raise DocumentError("the part has no body")
    return doc


# ====================================================================== CAM
@command(mutates=False, category="cam")
def cam_generate(setup: Literal["+Z", "-Z", "+X", "-X", "+Y", "-Y"] = "+Z", tool_diameter: float = 6.0, flutes: int = 3,
                 stepdown: float | None = None, stepover: float | None = None, leave: float = 0.3,
                 stock_margin: float = 1.0, stock_top: float = 1.0,
                 material: Literal["aluminum", "brass", "plastic", "mild_steel", "stainless", "titanium", "cast_iron"] = "aluminum",
                 drill: bool = True, max_drill_diameter: float = 20.0, tolerance: float | None = None) -> dict:
    """CAM (3-axis milling): toolpaths for the active part seen from `setup` (the side the
    tool comes from). Stock = the part's box + stock_margin on the sides and stock_top on top. Z-level
    roughing with a flat end mill (stepdown default D/2, stepover 0.45 D) leaving `leave` mm, finishing of
    the walls and flat floors, and drilling of the holes along the tool axis (canned cycles; peck when deeper
    than 3 D). Feeds and speeds from the material (carbide, a starting point). A stock-removal check on the
    toolpath raster reports gouges and the material left (inside corners keep the tool radius).
    `tolerance` is the raster cell (precision, default D/30 within 0.05-0.25 mm). Then cam_gcode."""
    from .cam import generate
    doc = _part()
    try:
        with unlocked():
            r = generate(doc.body, setup, tool_diameter, flutes, stepdown, stepover, leave, stock_margin, stock_top,
                         material, drill, max_drill_diameter, tolerance)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.cam_result = (doc.name, doc.revision, r)
    return _cam_summary(r)


@command(mutates=False, category="plastics")
def plastics_gate_advisor(candidates: int = 6, points: list | None = None, material: str = "ABS",
                          fill_time: float | None = None, max_pressure: float = 150.0,
                          clamp_axis: Literal["X", "Y", "Z"] = "Z") -> dict:
    """Plastics: where to put the gate. Tries a single gate at each candidate - the middles of the part's
    `candidates` largest faces, plus any `points` [x, y, z] given - with a quick, coarse fill analysis each,
    and ranks them: lowest injection pressure first (the shortest, most balanced flow), then fewest weld-line
    cells and air traps. Returns the table and the best point; run plastics_fill on it for the full result.
    A short shot is ranked last whatever its pressure."""
    from .geom import closest_point
    from .plastics import fill, material as plastic
    doc = _part()
    if not 1 <= candidates <= 20:
        raise DocumentError("candidates must be between 1 and 20")
    faces = sorted(doc.body.faces(), key=lambda f: -f.area)[:candidates]
    spots = []
    for f in faces:
        q, _ = closest_point(f, f.center())
        spots.append(([round(float(q.X), 4), round(float(q.Y), 4), round(float(q.Z), 4)], "face centre"))
    for pt in points or []:
        spots.append(([float(v) for v in pt], "given"))
    seen, unique = set(), []
    for pt, why in spots:
        key = tuple(round(v, 2) for v in pt)
        if key not in seen:
            seen.add(key)
            unique.append((pt, why))
    try:
        mat = plastic(material, None, None)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    rows = []
    with unlocked():
        for pt, why in unique:
            try:
                r = fill(doc.body, [pt], mat, fill_time, max_pressure, None, max_cells=6000, clamp_axis=clamp_axis)
            except ValueError as exc:
                rows.append({"gate": pt, "from": why, "error": str(exc)})
                continue
            rows.append({"gate": pt, "from": why, "max_injection_pressure_MPa": r["max_injection_pressure_MPa"],
                         "filled_pct": r["filled_pct"], "short_shot": r["short_shot"], "weld_line_cells": r["weld_line_cells"],
                         "air_traps": len(r["air_traps"]), "clamp_force_tonne": r["clamp_force_tonne"],
                         "last_to_fill": r["last_to_fill"]})
    ok = [r for r in rows if "error" not in r]
    if not ok:
        raise DocumentError("no candidate gate could be analysed: " + "; ".join(r["error"] for r in rows[:3]))
    ok.sort(key=lambda r: (r["short_shot"], r["max_injection_pressure_MPa"], r["weld_line_cells"], r["air_traps"]))
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    return {"material": mat["name"], "best": ok[0]["gate"], "candidates": ok + [r for r in rows if "error" in r],
            "note": "single-gate, coarse-mesh comparison: confirm the best with plastics_fill"}


@command(mutates=False, category="cam")
def cam_finish_3d(tool_diameter: float = 6.0, tool: Literal["ball", "bull", "flat"] = "ball", corner_radius: float | None = None,
                  stepover: float | None = None, direction: Literal["x", "y"] = "x", leave: float = 0.0, flutes: int = 2) -> dict:
    """CAM: add a 3D parallel finishing operation to the last cam_generate - straight passes `stepover` apart
    (default 0.15 D for a ball mill, 0.4 D otherwise) along x or y, the tool following the part's surface:
    curved and sloped faces are finished smoothly instead of in Z-level steps. tool: ball, bull (bull nose,
    with corner_radius, default D/8) or flat. The path lies on the tool's drop-cutter surface, so it cannot cut
    into the part (to within the raster); for a ball mill the result gives the scallop height the stepover
    leaves. The operation joins the others in cam_gcode and cam_toolpaths."""
    from .cam import finish_3d
    _doc, r = _cam()
    try:
        with unlocked():
            op = finish_3d(r, tool_diameter, tool, corner_radius, stepover, direction, leave, flutes)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    return {"operation": {k: v for k, v in op.items() if k != "moves"}, "tool": r["tools"][op["tool"] - 1], **_cam_summary(r)}


def _cam_summary(r: dict) -> dict:
    ops = []
    for op in r["operations"]:
        o = {"name": op["name"], "kind": op["kind"], "tool": op["tool"], "length_mm": op["length_mm"], "time_min": op["time_min"]}
        if op["kind"] == "drill":
            o["holes"] = len(op["cycles"])
        ops.append(o)
    return {"setup": r["setup"], "material": r["material"], "stock": r["stock"], "tools": r["tools"], "holes": r["holes"],
            "levels": r["levels"], "operations": ops, "cycle_time_min": r["cycle_time_min"],
            "verification": r["verification"], "precision_mm": r["cell"], "warnings": r["warnings"]}


def _cam():
    cached = getattr(APP, "cam_result", None)
    doc = APP.active_doc
    if not cached or cached[0] != doc.name or cached[1] != doc.revision:
        raise DocumentError("generate the toolpaths first (cam_generate)")
    return doc, cached[2]


@command(mutates=False, category="cam")
def cam_gcode(post: Literal["fanuc", "linuxcnc", "grbl"] = "fanuc", path: str | None = None, program: int = 1000) -> dict:
    """Post-process the last cam_generate toolpaths to G-code: fanuc / linuxcnc (tool changes, G43, canned
    drilling cycles G81 / G83) or grbl (M0 pauses for tool changes, drilling written out). Writes `path` (.nc)
    when given; returns the program text (its first 200 lines when a long program was written to a file)."""
    from .cam import gcode
    doc, r = _cam()
    try:
        text = gcode(r, post, program, doc.name)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    out = {"post": post, "lines": text.count("\n"), "cycle_time_min": r["cycle_time_min"]}
    if path:
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".nc")
        p.write_text(text, encoding="ascii", errors="replace")
        out["path"] = str(p.resolve())
    out["gcode"] = text if len(text) <= 200_000 or not path else "\n".join(text.splitlines()[:200]) + "\n..."
    return out


@command(mutates=False, category="cam", mcp=False)
def cam_toolpaths() -> dict:
    """The last toolpaths as polylines in part coordinates, per operation (for the 3-D view)."""
    from .cam import toolpath_display
    _doc, r = _cam()
    return {**_cam_summary(r), "paths": toolpath_display(r)}


# ====================================================================== plastics
@command(mutates=False, category="plastics")
def plastics_materials() -> dict:
    """The plastics library: generic grades with melt / mold / ejection temperatures, viscosity (power law)."""
    from .plastics import MATERIALS
    return {"materials": [{"name": k, **{f: v[f] for f in ("melt", "mold", "eject", "eta", "n", "rho")}} for k, v in MATERIALS.items()]}


@command(mutates=False, category="plastics")
def plastics_fill(gates: list, material: str = "ABS", melt_temperature: float | None = None,
                  mold_temperature: float | None = None, fill_time: float | None = None, max_pressure: float = 150.0,
                  element_size: float | None = None, clamp_axis: Literal["X", "Y", "Z"] = "Z") -> dict:
    """Plastics fill analysis: inject the active part (the cavity) from `gates` -
    points [x, y, z] on the part or faces 'FaceN' - with a generic `material` (ABS, PC, PP, PA66, POM, HDPE,
    PS, PC/ABS) at a constant flow rate over `fill_time` s (default from the volume). Hele-Shaw flow with the
    local wall thickness and a shear-thinning melt; isothermal (freeze-off is checked, not simulated).
    Returns the fill time, injection pressure (short shot above max_pressure MPa), clamp force along
    clamp_axis, cooling time, weld lines, air traps, sink-mark risk and warnings; plot with plastics_results."""
    from .plastics import fill, material as plastic
    from .session import _measure_item
    doc = _part()
    if not gates:
        raise DocumentError("give at least one gate: a point [x, y, z] or a face 'FaceN'")
    gs = []
    for g in gates:
        if isinstance(g, str):
            shape = _measure_item(g)[0]
            if shape is None:
                raise DocumentError(f"no face {g!r}")
            gs.append(shape)
        else:
            gs.append([float(v) for v in g])
    try:
        mat = plastic(material, melt_temperature, mold_temperature)
        with unlocked():
            r = fill(doc.body, gs, mat, fill_time, max_pressure, element_size, clamp_axis=clamp_axis)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.plastics_result = (doc.name, doc.revision, r, gates)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="plastics", mcp=False)
def plastics_results(field: Literal["fill_time", "pressure", "thickness", "weld", "sink"] = "fill_time") -> dict:
    """The last fill analysis for the UI: per display-mesh vertex values of `field`, with the weld line,
    air trap, last-to-fill and gate markers."""
    from .plastics import surface_values
    doc = APP.active_doc
    cached = getattr(APP, "plastics_result", None)
    if not cached or cached[0] != doc.name or cached[1] != doc.revision:
        raise DocumentError("run the fill analysis first")
    r = cached[2]
    vals = surface_values(r, doc.mesh(), field)
    allv = [v for vs in vals.values() for v in vs if v is not None]
    return {"field": field, "values": vals, "min": min(allv, default=0.0), "max": max(allv, default=0.0),
            "weld_lines": r["weld_lines"], "air_traps": r["air_traps"], "last_to_fill": r["last_to_fill"],
            "gates": [g for g in cached[3] if not isinstance(g, str)], "fill_time_s": r["fill_time_s"]}


# ====================================================================== Inspection
def _insp_doc():
    from .session import _drawing_state
    doc = APP.active_doc
    if getattr(doc, "kind", "part") == "assembly":
        raise DocumentError("Inspection works on a part and its drawing: open the part")
    return doc, _drawing_state(doc)


@command(mutates=False, category="inspection")
def inspection_characteristics(include_pmi: bool = True) -> dict:
    """Inspection: the numbered characteristic list of the active part - every drawing dimension, geometric
    tolerance and surface finish, and the 3D PMI (DimXpert) - with its requirement (nominal and tolerance;
    ISO 2768 general tolerance where none is given), min / max, location (sheet, zone, view), measured values
    and pass / fail. Numbers stay with their characteristic as others are added or removed."""
    from . import inspection as I
    doc, st = _insp_doc()
    chars = I.characteristics(doc, st, include_pmi)
    return {"characteristics": chars, "summary": I.summary(chars), "general_tolerance": I.state(st)["general"],
            "balloons": I.state(st)["balloons"]}


def _char_source(doc, st, char) -> str:
    from . import inspection as I
    for c in I.characteristics(doc, st):
        if str(c["number"]) == str(char) or c["source"] == char:
            return c["source"]
    raise DocumentError(f"no characteristic {char!r}")


@command(category="inspection")
def inspection_set(characteristic: str | int, measured: list[float] | None = None, upper: float | None = None,
                   lower: float | None = None, note: str | None = None) -> dict:
    """Inspection: enter measured value(s) for a characteristic (its number or source id, e.g. 3 or 'Dim2'),
    and / or override its tolerance (upper / lower deviation from nominal, mm; lower negative) or add a note."""
    from . import inspection as I
    doc, st = _insp_doc()
    src = _char_source(doc, st, characteristic)
    doc._checkpoint()
    I.set_override(st, src, measured=[float(m) for m in measured] if measured is not None else None,
                   upper=upper, lower=lower, note=note)
    doc.revision += 1
    doc.dirty = True
    return {"characteristic": next(c for c in I.characteristics(doc, st) if c["source"] == src)}


@command(category="inspection")
def inspection_settings(general_tolerance: Literal["f", "m", "c", "v"] | None = None, balloons: bool | None = None,
                        part_number: str | None = None, part_name: str | None = None, revision: str | None = None,
                        serial: str | None = None, organization: str | None = None, inspector: str | None = None,
                        date: str | None = None) -> dict:
    """Inspection settings: the ISO 2768 class for untoleranced dimensions, inspection balloons on the drawing
    sheets (on / off) and the report header (part number, name, revision, serial number, organisation,
    inspector, date)."""
    from . import inspection as I
    doc, st = _insp_doc()
    doc._checkpoint()
    ins = I.state(st)
    if general_tolerance:
        ins["general"] = general_tolerance
    if balloons is not None:
        ins["balloons"] = bool(balloons)
    for k, v in (("part_number", part_number), ("part_name", part_name), ("revision", revision), ("serial", serial),
                 ("organization", organization), ("inspector", inspector), ("date", date)):
        if v is not None:
            ins["header"][k] = v
    I.characteristics(doc, st)  # number the items
    doc.revision += 1
    doc.dirty = True
    return {"general_tolerance": ins["general"], "balloons": ins["balloons"], "header": ins["header"]}


@command(mutates=False, category="inspection")
def inspection_report(path: str, format: Literal["html", "csv"] = "html", include_pmi: bool = True) -> dict:
    """Inspection report: a first-article inspection report (forms 1-3 in the AS9102 layout: part, materials,
    characteristic accountability with results) as HTML, or the characteristic table as CSV."""
    from . import inspection as I
    doc, st = _insp_doc()
    chars = I.characteristics(doc, st, include_pmi)
    p = Path(path)
    if p.suffix.lower() != f".{format}":
        p = p.with_suffix(f".{format}")
    p.write_text(I.to_html(doc, st, chars) if format == "html" else I.to_csv(chars), encoding="utf-8")
    return {"path": str(p.resolve()), **I.summary(chars)}


# ====================================================================== electrical
def _elec():
    asm = APP.asm
    e = getattr(asm, "electrical", None)
    if not e:
        e = asm.electrical = {}
    e.setdefault("connectors", {})
    e.setdefault("wires", [])
    e.setdefault("routes", [])
    return asm, e


def _changed(asm):
    asm._checkpoint()
    asm.revision += 1
    asm.dirty = True


@command(category="routing")
def electrical_connector(name: str, component: str, pins: int | None = None, point: Vec3 | None = None) -> dict:
    """Electrical: make a component a connector `name` (e.g. 'J1') with `pins` pins. Wires attach at `point`
    (assembly coordinates), else at the component's first connection point, else its centre."""
    from .commands_more import _component_infos
    asm, e = _elec()
    c = asm.component(component)
    if point is None:
        cps = _component_infos(c, "connection_point")
        if cps:
            point = c.to_world(np.array(cps[0][1]["point"], float)).tolist()
        else:
            shape = next((s for cc, s in asm.world_shapes() if cc.name == c.name), None)
            if shape is None:
                raise DocumentError(f"{component} has no geometry: give the point")
            bb = shape.bounding_box()
            point = [(bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (bb.min.Z + bb.max.Z) / 2]
    _changed(asm)
    e["connectors"][name] = {"component": component, "pins": pins, "point": [float(v) for v in point]}
    return {"connector": name, **e["connectors"][name]}


@command(category="routing")
def electrical_route_segment(points: list[Vec3]) -> dict:
    """Electrical: add a harness route (a polyline through points, assembly coordinates). Routes that share
    a point join, so a trunk with branches is several routes meeting at the branch points."""
    asm, e = _elec()
    if len(points) < 2:
        raise DocumentError("a route needs at least two points")
    _changed(asm)
    e["routes"].append([[float(v) for v in p] for p in points])
    return {"routes": len(e["routes"]), "length": round(sum(math.dist(a, b) for a, b in zip(points, points[1:])), 3)}


@command(category="routing")
def electrical_wires(wires: list[dict], replace: bool = False) -> dict:
    """Electrical from-to list: wires as {"id", "from": "J1-1", "to": "P2-3", "gauge": "22 AWG" or "0.5 mm2",
    "color": "RD"}; added to the list (replace: instead of it)."""
    from .electrical import gauge, parse_end
    asm, e = _elec()
    clean = []
    for k, w in enumerate(wires, 1):
        if "from" not in w or "to" not in w:
            raise DocumentError("each wire needs 'from' and 'to' (connector-pin, e.g. 'J1-1')")
        try:
            gauge(w.get("gauge", "22 AWG"))
        except ValueError as exc:
            raise DocumentError(str(exc)) from None
        for end in (w["from"], w["to"]):
            if parse_end(end)[0] not in e["connectors"]:
                raise DocumentError(f"no connector {parse_end(end)[0]!r} (add it with electrical_connector)")
        clean.append({"id": str(w.get("id") or f"W{(0 if replace else len(e['wires'])) + k}"), "from": str(w["from"]),
                      "to": str(w["to"]), "gauge": str(w.get("gauge", "22 AWG")), "color": str(w.get("color", ""))})
    _changed(asm)
    e["wires"] = clean if replace else e["wires"] + clean
    return {"wires": e["wires"]}


@command(category="routing")
def electrical_import_wires(path: str, replace: bool = True) -> dict:
    """Electrical: import a from-to list from CSV with the columns Wire (or ID), From, To, Gauge, Color."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            rows.append({"id": r.get("wire") or r.get("id") or "", "from": r.get("from", ""), "to": r.get("to", ""),
                         "gauge": r.get("gauge") or "22 AWG", "color": r.get("color") or r.get("colour") or ""})
    return electrical_wires(rows, replace)


@command(category="routing")
def electrical_harness(build: bool = True, slack: float = 0.05, end_allowance: float = 25.0, current: float = 1.0) -> dict:
    """Electrical: route every wire of the from-to list through the harness routes (shortest path), giving each
    wire's length (path x (1 + slack) + end_allowance per end), resistance and voltage drop at `current` A,
    the bundle diameter of every segment and a wire BOM. build: (re)make the harness as a virtual component
    ("Harness<n>") - one swept bundle per segment and a breakout to each connector."""
    from build123d import Compound as _C, Edge as _E, Vector as _V, Wire as _W
    from .commands_more import _sweep_circle, _virtual_component
    from .electrical import bundle_diameter, parse_end, public, route
    asm, e = _elec()
    if not e["wires"]:
        raise DocumentError("add wires first (electrical_wires)")
    try:
        r = route(e["connectors"], e["wires"], e["routes"], slack, end_allowance, current)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.electrical_result = (asm.name, r)
    out = public(r)
    if build and r["segments"]:
        old = e.get("component")
        if old and any(c.name == old for c in asm.components):
            asm.delete_component(old)
        solids = [_sweep_circle(_W([_E.make_line(_V(*s["points"][0]), _V(*s["points"][1]))]), s["diameter"]) for s in r["segments"]]
        net = r["_net"]
        od = {w["id"]: w["od"] for w in r["wires"]}
        for c in r["connectors"]:
            i, d = net.nearest(c["point"])
            if not c["breakout_diameter"] or d < 1e-3:
                continue
            ids = [w["id"] for w in r["wires"] if w["routed"] and c["name"] in (parse_end(w["from"])[0], parse_end(w["to"])[0])]
            solids.append(_sweep_circle(_W([_E.make_line(_V(*net.nodes[i]), _V(*c["point"]))]),
                                        bundle_diameter(od[k] for k in ids)))
        made = _virtual_component(_C(solids), "Harness", f"Wire harness, {len(r['wires'])} wires, {r['total_wire_m']:.2f} m",
                                  {"Wires": len(r["wires"]), "Wire length (m)": r["total_wire_m"]})
        e["component"] = made["component"]
        out["component"] = made["component"]
    return out


@command(mutates=False, category="routing")
def electrical_formboard(path: str) -> dict:
    """Electrical: the flattened harness drawing (formboard, SVG): the route laid out flat at true segment
    lengths, bundle diameters, connector breakouts, the wire list and the connector table. Run
    electrical_harness first."""
    from .electrical import formboard_svg
    cached = getattr(APP, "electrical_result", None)
    if not cached or cached[0] != APP.asm.name:
        raise DocumentError("route the harness first (electrical_harness)")
    p = Path(path)
    if p.suffix.lower() != ".svg":
        p = p.with_suffix(".svg")
    p.write_text(formboard_svg(cached[1], APP.asm.name), encoding="utf-8")
    return {"path": str(p.resolve()), "segments": len(cached[1]["segments"]), "wires": len(cached[1]["wires"])}


@command(category="routing")
def electrical_clear(what: Literal["all", "wires", "routes", "connectors"] = "all") -> dict:
    """Electrical: clear the from-to list, the routes, the connectors or all three."""
    asm, e = _elec()
    _changed(asm)
    for k in (("wires", "routes", "connectors") if what == "all" else (what,)):
        e[k] = {} if k == "connectors" else []
    return {"connectors": len(e["connectors"]), "wires": len(e["wires"]), "routes": len(e["routes"])}


@command(mutates=False, category="routing")
def electrical_info() -> dict:
    """Electrical: the connectors, harness routes and from-to wire list of the active assembly."""
    _asm, e = _elec()
    return {"connectors": e["connectors"], "routes": e["routes"], "wires": e["wires"], "component": e.get("component")}


# ====================================================================== Composer
def _comp():
    asm = APP.asm
    c = getattr(asm, "composer", None)
    if not c:
        c = asm.composer = {}
    c.setdefault("steps", [])
    return asm, c


def _explode_offsets(asm, upto: int) -> dict:
    off = {}
    for st in asm.explode.get("steps", [])[:upto]:
        for n in st["components"]:
            o = off.setdefault(n, [0.0, 0.0, 0.0])
            for i in range(3):
                o[i] += st["direction"][i] * st["distance"]
    return {k: [round(v, 4) for v in o] for k, o in off.items()}


@command(category="composer")
def composer_auto(order: Literal["assembly", "disassembly"] = "assembly", view: str = "iso") -> dict:
    """Composer: make the animation steps from the exploded view - disassembly (assembled, then each explode
    step in turn) or assembly instructions (all parts laid out, then the explode steps put back in reverse
    order). Each step's note names the parts it moves."""
    asm, c = _comp()
    steps_x = asm.explode.get("steps", [])
    if not steps_x:
        raise DocumentError("make an exploded view first (explode_step)")
    n = len(steps_x)
    if order == "disassembly":
        steps = [{"name": "Assembled", "offsets": {}, "note": "The complete assembly.", "view": view}]
        for k in range(1, n + 1):
            names = ", ".join(steps_x[k - 1]["components"])
            steps.append({"name": f"Remove {names}", "offsets": _explode_offsets(asm, k), "note": f"Remove {names}.", "view": view})
    else:
        steps = [{"name": "Parts", "offsets": _explode_offsets(asm, n), "note": "All parts laid out.", "view": view}]
        for k in range(n - 1, -1, -1):
            names = ", ".join(steps_x[k]["components"])
            steps.append({"name": f"Install {names}", "offsets": _explode_offsets(asm, k), "note": f"Install {names}.", "view": view})
    _changed(asm)
    c["steps"] = steps
    return {"steps": [s["name"] for s in steps]}


@command(category="composer")
def composer_add_step(name: str, explode_steps: int | None = None, offsets: dict | None = None,
                      hidden: list[str] | None = None, transparent: list[str] | None = None, view: str | None = None,
                      note: str = "") -> dict:
    """Composer: add a step - component positions from the first `explode_steps` explode steps and / or
    explicit offsets {component: [dx, dy, dz]} (mm from the assembled position), hidden and see-through
    components, a view (iso, front, top, right) and the instruction note."""
    asm, c = _comp()
    names = {x.name for x in asm.components}
    for n in list(hidden or []) + list(transparent or []) + list((offsets or {}).keys()):
        if n not in names:
            raise DocumentError(f"no component {n!r}")
    off = _explode_offsets(asm, explode_steps) if explode_steps else {}
    for k, v in (offsets or {}).items():
        off[k] = [float(x) for x in v]
    step = {"name": name, "offsets": off, "hidden": list(hidden or []), "transparent": list(transparent or []),
            "view": view, "note": note}
    _changed(asm)
    c["steps"].append(step)
    return {"step": len(c["steps"]), **step}


@command(mutates=False, category="composer")
def composer_steps() -> dict:
    """Composer: the animation steps."""
    return {"steps": _comp()[1]["steps"]}


@command(category="composer")
def composer_clear() -> dict:
    """Composer: delete all steps."""
    asm, c = _comp()
    _changed(asm)
    c["steps"] = []
    return {"steps": []}


@command(mutates=False, category="composer")
def composer_export(path: str) -> dict:
    """Composer: publish the steps as one self-contained interactive HTML file (opens in any browser): the
    3-D model with Play / previous / next, the parts moving between steps, the step list and notes, plus the
    viewer's section, measure and markup tools."""
    from .commands_more import viewer_payload, write_viewer
    asm, c = _comp()
    if not c["steps"]:
        raise DocumentError("add steps first (composer_auto or composer_add_step)")
    was = asm.explode.get("active", False)
    asm.explode["active"] = False  # the model goes out assembled; the steps move the parts
    asm.revision += 1
    try:
        payload = viewer_payload(asm)
    finally:
        asm.explode["active"] = was
        asm.revision += 1
    payload["steps"] = c["steps"]
    p = write_viewer(path, asm.name, payload)
    return {"path": str(p.resolve()), "steps": len(c["steps"]), "size_bytes": p.stat().st_size}
