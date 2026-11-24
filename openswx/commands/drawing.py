"""Drawing commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from typing import Literal

from openswx import session as _S
from openswx.session import (
    APP,
    DocumentError,
    Path,
    SheetSize,
    Vec2,
    _PICK_MM,
    _beside,
    _drawing_edit,
    _drawing_state,
    _hole_callout,
    _line_dimension,
    _new_balloon,
    _next_ann,
    _views_edit,
    command,
    math,
)  # noqa: F401

__all__ = ["create_drawing", "drawing_settings", "drawing_move_view", "drawing_add_note", "drawing_add_datum",
           "drawing_add_gdt", "drawing_edit_annotation", "drawing_delete_annotation", "drawing_new",
           "drawing_add_sheet", "drawing_activate_sheet", "drawing_delete_sheet", "drawing_sheet_properties",
           "drawing_add_view", "drawing_projected_view", "drawing_section_view", "drawing_detail_view",
           "drawing_auxiliary_view", "drawing_view_properties", "drawing_crop_view", "drawing_break_view",
           "drawing_broken_out_section", "drawing_hole_table", "drawing_edit_bom", "drawing_title_block",
           "drawing_move_table", "drawing_delete_table", "drawing_remove_view_effect", "drawing_delete_view",
           "drawing_add_revision", "drawing_pick", "drawing_add_dimension", "drawing_dimension_value",
           "drawing_add_balloon", "drawing_auto_balloon", "drawing_edit_dimension", "drawing_delete_dimension",
           "drawing_info", "export_drawing"]


# ====================================================================== drawings
@command(mutates=False, category="drawing")
def create_drawing(path: str | None = None, sheet: SheetSize = "A3",
                   projection: Literal["third", "first"] = "third", scale: float | None = None) -> dict:
    """Make Drawing from Part/Assembly: Front, Top, Right (third angle) or Left (first angle) and Isometric
    views with hidden lines, overall dimensions and a title block, as an SVG sheet. Writes the SVG to `path`
    if given; scale is chosen automatically unless given (e.g. 0.5 for 1:2)."""
    from openswx.drawing.drawing import make_drawing
    d = make_drawing(APP.active_doc, sheet, projection, scale)
    if path:
        p = Path(path)
        if p.suffix.lower() != ".svg":
            p = p.with_suffix(".svg")
        p.write_text(d["svg"], encoding="utf-8")
        d["path"] = str(p.resolve())
    return {k: v for k, v in d.items() if k != "svg"} | ({"svg_length": len(d["svg"])} if path else {"svg": d["svg"]})


@command(category="drawing")
def drawing_settings(sheet: SheetSize | None = None, projection: Literal["third", "first"] | None = None,
                     scale: float | None = None, model_items: bool | None = None) -> dict:
    """Sheet properties for the active document's drawing: sheet size, projection, scale (e.g. 0.5 = 1:2;
    0 = automatic) and Model Items (show the driving sketch dimensions in the views)."""
    def f(st):
        if st.get("sheets"):  # sheet properties of the active sheet; Model Items on all of its views
            from openswx.drawing.drawing_sheets import active_sheet
            sh = active_sheet(st)
            for k, v in (("size", sheet), ("projection", projection)):
                if v is not None:
                    sh[k] = v
            if scale:
                sh["scale"] = scale
            if model_items is not None:
                for v in sh["views"]:
                    v["dims"] = model_items
            return {k: v for k, v in sh.items() if k not in ("views",)}
        for k, v in (("sheet", sheet), ("projection", projection), ("model_items", model_items)):
            if v is not None:
                st[k] = v
        if scale is not None:
            if scale > 0:
                st["scale"] = scale
            else:
                st.pop("scale", None)
        return dict(st)
    return {"drawing": _drawing_edit(f)}


@command(category="drawing")
def drawing_move_view(view: str, dx: float, dy: float) -> dict:
    """Move a drawing view by (dx, dy) sheet mm (y down): a view id (drawing with sheets) or front / top /
    right / left / isometric (automatic drawing). Projected views follow their parent and only slide along
    their projection line."""
    def f(st):
        if st.get("sheets"):
            from openswx.drawing.drawing_sheets import find_view
            v = find_view(st, view)
            v["x"], v["y"] = v["x"] + dx, v["y"] + dy
            moved = {view}
            for sh in st["sheets"]:  # children placed relative to the parent move with it
                for c in sh["views"]:
                    if c.get("parent") == view and c["kind"] != "detail":
                        c["x"], c["y"] = c["x"] + dx, c["y"] + dy
                        moved.add(c["id"])
            for sh in st["sheets"]:  # and so do their balloons
                for b in sh.get("balloons", []):
                    if b["view"] in moved:
                        b["x"], b["y"] = b["x"] + dx, b["y"] + dy
            return {"view": view, "x": v["x"], "y": v["y"]}
        o = st.setdefault("offsets", {})
        cur = o.get(view, [0.0, 0.0])
        o[view] = [cur[0] + dx, cur[1] + dy]
        return o
    return {"offsets": _drawing_edit(f)}


@command(category="drawing")
def drawing_add_note(text: str, x: float, y: float, size: float = 3.5, leader: Vec2 | None = None) -> dict:
    """Add a note at sheet position (x, y) mm (origin top-left); optional leader arrow to [x, y]. Use
    newlines for multi-line notes."""
    def f(st):
        n = {"id": _next_ann(st, "Note"), "text": text, "x": x, "y": y, "size": size, "leader": leader}
        st.setdefault("notes", []).append(n)
        return n
    return {"note": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_datum(label: str, x: float, y: float, leader: Vec2 | None = None) -> dict:
    """Datum Feature symbol (boxed letter, e.g. 'A') at (x, y) with a filled-triangle leader to a feature."""
    def f(st):
        d = {"id": _next_ann(st, "Datum"), "label": label.upper()[:2], "x": x, "y": y, "leader": leader}
        st.setdefault("datums", []).append(d)
        return d
    return {"datum": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_gdt(characteristic: Literal["straightness", "flatness", "circularity", "cylindricity", "profile_line",
                                            "profile_surface", "perpendicularity", "angularity", "parallelism",
                                            "position", "concentricity", "symmetry", "circular_runout", "total_runout"],
                    tolerance: float, x: float, y: float, datums: list[str] | None = None, diameter: bool = False,
                    modifier: Literal["MMC", "LMC"] | None = None, leader: Vec2 | None = None) -> dict:
    """Geometric Tolerance (feature control frame): characteristic symbol, tolerance (diameter zone if
    `diameter`), material modifier and up to three datum references, placed at (x, y) with an optional leader."""
    def f(st):
        g = {"id": _next_ann(st, "GTol"), "characteristic": characteristic, "tolerance": tolerance,
             "datums": [d.upper() for d in (datums or [])][:3], "diameter": diameter, "modifier": modifier,
             "x": x, "y": y, "leader": leader}
        st.setdefault("gdt", []).append(g)
        return g
    return {"gdt": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_edit_annotation(id: str, x: float | None = None, y: float | None = None, text: str | None = None) -> dict:
    """Move an annotation (note, datum, GD&T frame, surface finish or weld symbol, balloon) or change a note's /
    balloon's text."""
    def f(st):
        for k in ("notes", "datums", "gdt", "finishes", "welds", "balloons"):
            for a in st.get(k, []):
                if a["id"] == id:
                    if x is not None:
                        a["x"] = x
                    if y is not None:
                        a["y"] = y
                    if text is not None and k in ("notes", "balloons"):
                        a["text"] = text
                    return a
        raise DocumentError(f"no annotation {id!r}")
    return {"annotation": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_delete_annotation(id: str) -> dict:
    """Delete a note, datum, GD&T frame, surface finish or weld symbol, or balloon from the drawing."""
    def f(st):
        for k in ("notes", "datums", "gdt", "finishes", "welds", "balloons"):
            before = len(st.get(k, []))
            st[k] = [a for a in st.get(k, []) if a["id"] != id]
            if len(st[k]) != before:
                return id
        raise DocumentError(f"no annotation {id!r}")
    return {"deleted": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_new(sheet: SheetSize = "A3", projection: Literal["third", "first"] = "third",
                scale: float | None = None, standard_views: bool = True) -> dict:
    """New Drawing of the active part or assembly: one sheet; with standard_views, Front, Top, the side view
    (Right in third angle, Left in first) and an Isometric, scaled to fit (scale e.g. 0.5 = 1:2). Replaces the
    document's automatic drawing; views are then added, moved and edited with the drawing_* commands."""
    from openswx.drawing.drawing_sheets import new_drawing
    d = APP.active_doc
    try:
        st = new_drawing(d, sheet, projection, scale, standard_views)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc

    def f(old):
        old.clear()
        old.update(st)
        return st
    out = _drawing_edit(f)
    return {"sheets": [x["name"] for x in out["sheets"]], "views": [v["id"] for v in out["sheets"][0]["views"]],
            "scale": out["sheets"][0]["scale"]}


@command(category="drawing")
def drawing_add_sheet(size: SheetSize | None = None, name: str | None = None,
                      projection: Literal["third", "first"] | None = None, scale: float = 1.0) -> dict:
    """Add Sheet: a new empty sheet (size and projection default to the active sheet's); it becomes active."""
    def f(st, DS):
        cur = DS.active_sheet(st)
        names = {x["name"] for x in st["sheets"]}
        nm = name or next(f"Sheet{i}" for i in range(1, 999) if f"Sheet{i}" not in names)
        if nm in names:
            raise DocumentError(f"there is already a sheet {nm!r}")
        sh = {"name": nm, "size": size or cur.get("size", "A3"), "scale": scale,
              "projection": projection or cur.get("projection", "third"), "views": [], "notes": [], "datums": [],
              "gdt": [], "revisions": [], "tables": []}
        st["sheets"].append(sh)
        st["active"] = len(st["sheets"]) - 1
        return {"sheet": nm, "sheets": [x["name"] for x in st["sheets"]]}
    return _views_edit(f)


@command(category="drawing")
def drawing_activate_sheet(sheet: str) -> dict:
    """Make a sheet the active one (the one shown, exported as SVG / DXF and annotated)."""
    def f(st, DS):
        for i, x in enumerate(st["sheets"]):
            if x["name"] == sheet:
                st["active"] = i
                return {"sheet": sheet}
        raise DocumentError(f"no sheet {sheet!r}")
    return _views_edit(f)


@command(category="drawing")
def drawing_delete_sheet(sheet: str) -> dict:
    """Delete a sheet and its views (a drawing keeps at least one sheet)."""
    def f(st, DS):
        if len(st["sheets"]) < 2:
            raise DocumentError("a drawing keeps at least one sheet")
        idx = next((i for i, x in enumerate(st["sheets"]) if x["name"] == sheet), None)
        if idx is None:
            raise DocumentError(f"no sheet {sheet!r}")
        gone = {v["id"] for v in st["sheets"][idx]["views"]}
        if any(v.get("parent") in gone for i, x in enumerate(st["sheets"]) if i != idx for v in x["views"]):
            raise DocumentError("views on other sheets depend on this sheet's views")
        del st["sheets"][idx]
        st["active"] = min(int(st.get("active", 0)), len(st["sheets"]) - 1)
        return {"sheets": [x["name"] for x in st["sheets"]]}
    return _views_edit(f)


@command(category="drawing")
def drawing_sheet_properties(name: str | None = None, size: SheetSize | None = None,
                             scale: float | None = None, projection: Literal["third", "first"] | None = None) -> dict:
    """Sheet Properties of the active sheet: name, size, scale (views without their own scale follow it)
    and projection type."""
    def f(st, DS):
        sh = DS.active_sheet(st)
        if name:
            if any(x["name"] == name for x in st["sheets"] if x is not sh):
                raise DocumentError(f"there is already a sheet {name!r}")
            sh["name"] = name
        for k, v in (("size", size), ("projection", projection)):
            if v is not None:
                sh[k] = v
        if scale:
            sh["scale"] = scale
        return {k: v for k, v in sh.items() if k != "views"}
    return _views_edit(f)


@command(category="drawing")
def drawing_add_view(orientation: Literal["front", "back", "top", "bottom", "left", "right", "isometric", "dimetric",
                                          "trimetric"], x: float, y: float, scale: float | None = None) -> dict:
    """Model View: a view of the model from a standard orientation, centred at sheet (x, y) mm on the active
    sheet; scale None uses the sheet scale."""
    def f(st, DS):
        sh = DS.active_sheet(st)
        v = {"id": DS.next_id(st, "Drawing View"), "kind": "model", "orientation": orientation, "x": x, "y": y,
             "scale": scale}
        sh["views"].append(v)
        DS.view_geometry(APP.active_doc, st, v)
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_projected_view(parent: str, side: Literal["right", "left", "top", "bottom"], x: float | None = None,
                           y: float | None = None) -> dict:
    """Projected View from a parent view, placed on that side of it (third angle: placed right shows the
    right side; first angle: the left side). It stays aligned with the parent; x / y only move it along the
    projection line."""
    def f(st, DS):
        p = DS.find_view(st, parent)
        sh = DS.sheet_of(st, parent)
        v = {"id": DS.next_id(st, "Drawing View"), "kind": "projected", "parent": parent, "side": side,
             "x": p["x"], "y": p["y"]}
        sh["views"].append(v)
        v["x"], v["y"] = DS.default_position(APP.active_doc, st, p, side, v)
        if side in ("left", "right") and x is not None:
            v["x"] = x
        if side in ("top", "bottom") and y is not None:
            v["y"] = y
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_section_view(parent: str, start: Vec2, end: Vec2, x: float | None = None, y: float | None = None,
                         flip: bool = False, letter: str | None = None) -> dict:
    """Section View: a cutting line from start to end (sheet mm, drawn across the parent view); the section
    looks along the arrows at the material behind the line (flip reverses them), cut faces hatched. Placed
    beside the parent the way the arrows point unless x / y are given."""
    def f(st, DS):
        p = DS.find_view(st, parent)
        sh = DS.sheet_of(st, parent)
        a = DS.sheet_to_view(st, APP.active_doc, p, start)
        b = DS.sheet_to_view(st, APP.active_doc, p, end)
        if math.dist(a, b) < 1e-6:
            raise DocumentError("the section line needs two different points")
        v = {"id": DS.next_id(st, "Section View"), "kind": "section", "parent": parent, "a": list(a), "b": list(b),
             "flip": flip, "letter": (letter or DS.next_letter(st)).upper(), "x": p["x"], "y": p["y"]}
        sh["views"].append(v)
        try:
            DS.view_geometry(APP.active_doc, st, v)  # (fails here if the line misses the part)
        except ValueError:
            sh["views"].remove(v)
            raise
        dx, dy = DS.default_position(APP.active_doc, st, p, _beside(DS, st, p, v), v)
        v["x"], v["y"] = x if x is not None else dx, y if y is not None else dy
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_detail_view(parent: str, center: Vec2, radius: float, magnify: float = 2.0, x: float | None = None,
                        y: float | None = None, letter: str | None = None) -> dict:
    """Detail View: the part of the parent view inside a circle (centre and radius in sheet mm), magnified
    (2 = twice the parent's scale) and placed at (x, y) (default: to the right of the parent)."""
    def f(st, DS):
        if radius <= 0 or magnify <= 0:
            raise DocumentError("radius and magnification must be positive")
        p = DS.find_view(st, parent)
        sh = DS.sheet_of(st, parent)
        c = DS.sheet_to_view(st, APP.active_doc, p, center)
        v = {"id": DS.next_id(st, "Detail View"), "kind": "detail", "parent": parent, "center": list(c),
             "radius": radius / DS.view_scale(st, p), "magnify": magnify,
             "letter": (letter or DS.next_letter(st)).upper(), "x": p["x"], "y": p["y"]}
        sh["views"].append(v)
        dx, dy = DS.default_position(APP.active_doc, st, p, "right", v)
        v["x"], v["y"] = x if x is not None else dx, y if y is not None else dy
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_auxiliary_view(parent: str, edge_start: Vec2, edge_end: Vec2, x: float | None = None, y: float | None = None,
                           flip: bool = False, letter: str | None = None) -> dict:
    """Auxiliary View: looks square to an inclined edge picked in the parent view (its ends in sheet mm),
    showing the inclined face in its true shape; flip looks from the other side."""
    def f(st, DS):
        p = DS.find_view(st, parent)
        sh = DS.sheet_of(st, parent)
        a = DS.sheet_to_view(st, APP.active_doc, p, edge_start)
        b = DS.sheet_to_view(st, APP.active_doc, p, edge_end)
        if math.dist(a, b) < 1e-6:
            raise DocumentError("pick two different points along the edge")
        v = {"id": DS.next_id(st, "Drawing View"), "kind": "auxiliary", "parent": parent,
             "direction": [b[0] - a[0], b[1] - a[1]], "flip": flip, "letter": (letter or DS.next_letter(st)).upper(),
             "x": p["x"], "y": p["y"]}
        sh["views"].append(v)
        dx, dy = DS.default_position(APP.active_doc, st, p, _beside(DS, st, p, v), v)
        v["x"], v["y"] = x if x is not None else dx, y if y is not None else dy
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_view_properties(view: str, x: float | None = None, y: float | None = None, scale: float | None = None,
                            hidden: Literal["visible", "removed"] | None = None, dims: bool | None = None,
                            label: str | None = None, aligned: bool | None = None, center_marks: bool | None = None,
                            centerlines: bool | None = None) -> dict:
    """Drawing View properties: position (sheet mm), own scale (0 = use the sheet's / parent's), hidden lines
    shown dashed or removed, the model's dimensions shown, label text, (projected views) alignment with the
    parent, and automatic center marks on circles / centerlines on cylinders seen side-on."""
    def f(st, DS):
        v = DS.find_view(st, view)
        if x is not None:
            v["x"] = x
        if y is not None:
            v["y"] = y
        if scale is not None:
            v["scale"] = scale if scale > 0 else None
        for k, val in (("hidden", hidden), ("dims", dims), ("label", label), ("aligned", aligned),
                       ("center_marks", center_marks), ("centerlines", centerlines)):
            if val is not None:
                v[k] = val
        return v
    return {"view": _views_edit(f)}


@command(category="drawing")
def drawing_crop_view(view: str, corner1: Vec2, corner2: Vec2) -> dict:
    """Crop View: keep only the part of a view inside the rectangle corner1-corner2 (sheet mm)."""
    def f(st, DS):
        v = DS.find_view(st, view)
        if v["kind"] == "detail":
            raise DocumentError("a detail view is already cropped by its circle")
        a, b = (DS.sheet_to_view(st, APP.active_doc, v, p) for p in (corner1, corner2))
        crop = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
        if v.get("crop"):  # cropping again: within the current crop
            c = v["crop"]
            crop = [max(crop[0], c[0]), max(crop[1], c[1]), min(crop[2], c[2]), min(crop[3], c[3])]
        if crop[2] - crop[0] < 1e-6 or crop[3] - crop[1] < 1e-6:
            raise DocumentError("the crop rectangle is empty")
        v["crop"] = crop
        g = DS.view_geometry(APP.active_doc, st, v)
        if not g["vis"] + g["hid"]:
            v.pop("crop")
            raise DocumentError("the crop rectangle holds nothing of the view")
        return {"view": view, "crop": crop}
    return _views_edit(f)


@command(category="drawing")
def drawing_break_view(view: str, start: Vec2, end: Vec2, orientation: Literal["vertical", "horizontal"] = "vertical") -> dict:
    """Break View: remove the length of a long part between two break lines, placed through start and end
    (sheet mm); vertical break lines shorten the view left-right. Dimensions across the break keep the true
    length."""
    def f(st, DS):
        v = DS.find_view(st, view)
        if v["kind"] == "detail":
            raise DocumentError("a detail view cannot be broken")
        i = 0 if orientation == "vertical" else 1
        a, b = (DS.sheet_to_view(st, APP.active_doc, v, p)[i] for p in (start, end))
        lo, hi = min(a, b), max(a, b)
        gap = DS.BREAK_GAP / DS.view_scale(st, v)
        if hi - lo <= gap:
            raise DocumentError(f"place the break lines more than {DS.BREAK_GAP:g} mm apart")
        for x in v.get("breaks", []):
            if x["orientation"] == orientation and not (hi <= min(x["a"], x["b"]) or lo >= max(x["a"], x["b"])):
                raise DocumentError("the break overlaps an existing break")
        v.setdefault("breaks", []).append({"orientation": orientation, "a": lo, "b": hi})
        return {"view": view, "breaks": v["breaks"]}
    return _views_edit(f)


@command(category="drawing")
def drawing_broken_out_section(view: str, depth: float, profile: list[Vec2] | None = None, center: Vec2 | None = None,
                               radius: float | None = None) -> dict:
    """Broken-out Section: cut away the material inside a closed profile (points in sheet mm, or a circle:
    center + radius in sheet mm) from the viewer down to depth (model mm, from the part's nearest point),
    showing the inside hatched."""
    def f(st, DS):
        v = DS.find_view(st, view)
        if v["kind"] not in ("model", "projected", "auxiliary"):
            raise DocumentError("a broken-out section goes in a model, projected or auxiliary view")
        if depth <= 0:
            raise DocumentError("depth must be positive")
        if center is not None and radius:
            cut = {"center": list(DS.sheet_to_view(st, APP.active_doc, v, center)), "radius": radius / DS.view_scale(st, v)}
        elif profile and len(profile) >= 3:
            cut = {"profile": [list(DS.sheet_to_view(st, APP.active_doc, v, p)) for p in profile]}
        else:
            raise DocumentError("give a closed profile of at least 3 points, or a center and radius")
        cut["depth"] = depth
        v.setdefault("broken_out", []).append(cut)
        try:
            DS.view_geometry(APP.active_doc, st, v)
        except Exception as e:
            v["broken_out"].pop()
            if not v["broken_out"]:
                v.pop("broken_out")
            raise DocumentError(f"broken-out section failed: {e}") from None
        return {"view": view, "broken_out": v["broken_out"]}
    return _views_edit(f)


@command(category="drawing")
def drawing_hole_table(view: str, origin: Vec2, x: float | None = None, y: float | None = None) -> dict:
    """Hole Table: list the holes seen end-on in a view - tag, X / Y location from the origin (a corner picked
    in sheet mm, snapped to the view's vertices) and size (diameter, THRU or depth) - with the tags put on
    the view. x, y: the table's top-left corner (sheet mm)."""
    def f(st, DS):
        v = DS.find_view(st, view)
        g = DS.view_geometry(APP.active_doc, st, v)
        o = DS.snap_vertex(g, DS.sheet_to_view(st, APP.active_doc, v, origin), _PICK_MM / DS.view_scale(st, v))
        if o is None:
            raise DocumentError("pick the origin on a corner of the view")
        holes = DS.view_holes(APP.active_doc, st, v)
        if not holes:
            raise DocumentError(f"no holes seen end-on in {view}")
        sh = DS.sheet_of(st, view)
        used = {tb.get("id") for s in st["sheets"] for tb in s.get("tables", [])}
        tid = next(f"Hole Table{i}" for i in range(1, 999) if f"Hole Table{i}" not in used)
        tb = {"type": "hole", "id": tid, "view": view, "origin": list(o), "x": 16.0 if x is None else x, "y": 16.0 if y is None else y}
        sh.setdefault("tables", []).append(tb)
        return {"table": tid, "holes": [{"tag": h["tag"], "x": h["c"][0] - o[0], "y": h["c"][1] - o[1], "diameter": h["d"],
                                         "depth": h["depth"]} for h in holes]}
    return _views_edit(f)


@command(category="drawing")
def drawing_edit_bom(part: str, part_number: str | None = None, description: str | None = None, item: int | None = None) -> dict:
    """Edit the drawing's bill of materials row for a part (as listed by drawing_info's bom): its part number
    and description ('' = back to the default), or move it to item number `item`."""
    def f(st, DS):
        for sh in st["sheets"]:
            for tb in sh.get("tables", []):
                if tb["type"] != "bom":
                    continue
                rows = DS.bom_rows(APP.active_doc, tb)
                parts = [r["part"] for r in rows]
                if part not in parts:
                    raise DocumentError(f"no part {part!r} in the BOM (parts: {', '.join(parts)})")
                o = tb.setdefault("rows", {}).setdefault(part, {})
                for k, val in (("part_number", part_number), ("description", description)):
                    if val is not None:
                        if val:
                            o[k] = val
                        else:
                            o.pop(k, None)
                if item is not None:
                    parts.remove(part)
                    parts.insert(max(0, min(int(item) - 1, len(parts))), part)
                    tb["order"] = parts
                return {"bom": DS.bom_rows(APP.active_doc, tb)}
        raise DocumentError("this drawing has no bill of materials (drawings of assemblies have one)")
    return _views_edit(f)


@command(category="drawing")
def drawing_title_block(company: str | None = None, title: str | None = None, dwg_no: str | None = None,
                        material: str | None = None, drawn: str | None = None, drawn_date: str | None = None,
                        checked: str | None = None, approved: str | None = None) -> dict:
    """Sheet format: fill in the title block of every sheet ('' = back to what comes from the model)."""
    def f(st, DS):
        tf = st.setdefault("title", {})
        for k, val in (("company", company), ("title", title), ("dwg_no", dwg_no), ("material", material), ("drawn", drawn),
                       ("drawn_date", drawn_date), ("checked", checked), ("approved", approved)):
            if val is not None:
                if val:
                    tf[k] = val
                else:
                    tf.pop(k, None)
        return {"title_block": dict(tf)}
    return _views_edit(f)


@command(category="drawing")
def drawing_move_table(table: str, x: float, y: float) -> dict:
    """Move a table (\"BOM\" or a hole table's id) so its top-left corner is at x, y (sheet mm)."""
    def f(st, DS):
        for sh in st["sheets"]:
            for tb in sh.get("tables", []):
                if tb.get("id") == table or (table == "BOM" and tb["type"] == "bom"):
                    tb["x"], tb["y"] = x, y
                    return {"table": table, "x": x, "y": y}
        raise DocumentError(f"no table {table!r}")
    return _views_edit(f)


@command(category="drawing")
def drawing_delete_table(table: str) -> dict:
    """Delete a table: a hole table by id, \"BOM\" or \"Revision\"."""
    def f(st, DS):
        for sh in st["sheets"]:
            for tb in list(sh.get("tables", [])):
                if tb.get("id") == table or (table == "BOM" and tb["type"] == "bom") or (table == "Revision" and tb["type"] == "revision"):
                    sh["tables"].remove(tb)
                    return {"deleted": table}
        raise DocumentError(f"no table {table!r}")
    return _views_edit(f)


@command(category="drawing")
def drawing_remove_view_effect(view: str, effect: Literal["crop", "breaks", "broken_out"]) -> dict:
    """Remove Crop / Un-Break View / delete the broken-out sections of a view."""
    def f(st, DS):
        v = DS.find_view(st, view)
        if effect not in v:
            raise DocumentError(f"{view} has no {effect.replace('_', '-')}")
        v.pop(effect)
        return {"view": view, "removed": effect}
    return _views_edit(f)


@command(category="drawing")
def drawing_delete_view(view: str) -> dict:
    """Delete a drawing view and the views derived from it (projected, section, detail, auxiliary). On the
    automatic drawing (no sheets yet) the view (front / top / right / left / isometric) is left out."""
    if not _drawing_state(APP.active_doc).get("sheets"):
        def hide(st):
            if view not in ("front", "top", "right", "left", "isometric"):
                raise DocumentError(f"no view {view!r}")
            hv = st.setdefault("hidden_views", [])
            if view not in hv:
                hv.append(view)
            return [view]
        return {"deleted": _drawing_edit(hide)}

    def f(st, DS):
        DS.find_view(st, view)
        gone = {view}
        changed = True
        while changed:
            changed = False
            for sh in st["sheets"]:
                for v in sh["views"]:
                    if v.get("parent") in gone and v["id"] not in gone:
                        gone.add(v["id"])
                        changed = True
        for sh in st["sheets"]:
            sh["views"] = [v for v in sh["views"] if v["id"] not in gone]
            if "dims" in sh:
                sh["dims"] = [d for d in sh["dims"] if d["view"] not in gone]
            if "tables" in sh:
                sh["tables"] = [tb for tb in sh["tables"] if tb.get("view") not in gone]
        return sorted(gone)
    return {"deleted": _views_edit(f)}


@command(category="drawing")
def drawing_add_revision(rev: str, description: str, date: str | None = None, approved: str = "") -> dict:
    """Revision Table: add a revision row to the active sheet (shown top right; the latest revision also goes
    into the title block). date defaults to today (YYYY-MM-DD)."""
    import datetime as _dt

    def f(st, DS):
        sh = DS.active_sheet(st)
        row = {"rev": rev, "description": description, "date": date or _dt.date.today().isoformat(), "approved": approved}
        sh.setdefault("revisions", []).append(row)
        if not any(t["type"] == "revision" for t in sh.setdefault("tables", [])):
            sh["tables"].append({"type": "revision"})
        return row
    return {"revision": _views_edit(f)}


@command(mutates=False, category="drawing")
def drawing_pick(view: str, p: Vec2) -> dict:
    """What a click at p (sheet mm) in a view is on: the snapped vertex and / or the circle (centre, radius),
    in the view's 2D model coordinates and on the sheet (mm) - for Smart Dimension."""
    from openswx.drawing import drawing_sheets as DS
    st = getattr(APP.active_doc, "drawing", None) or {}
    try:
        v = DS.find_view(st, view)
    except ValueError as e:
        raise DocumentError(str(e)) from None
    g = DS.view_geometry(APP.active_doc, st, v)
    tol = _PICK_MM / DS.view_scale(st, v)
    q = DS.sheet_to_view(st, APP.active_doc, v, p)
    vx, c, ln = DS.snap_vertex(g, q, tol), DS.pick_circle(g, q, tol), DS.pick_line(g, q, tol)
    t, s = DS.placement(st, v, g), DS.view_scale(st, v)
    return {"vertex": list(vx) if vx else None, "vertex_sheet": list(t(*vx)) if vx else None,
            "circle": {"center": list(c[0]), "radius": c[1], "center_sheet": list(t(*c[0])), "radius_sheet": c[1] * s} if c else None,
            "line": {"ends": [list(ln[0]), list(ln[1])], "ends_sheet": [list(t(*ln[0])), list(t(*ln[1]))]} if ln else None}


@command(category="drawing")
def drawing_add_dimension(view: str, kind: Literal["auto", "linear", "horizontal", "vertical", "radius", "diameter", "angle",
                                                  "line_distance", "callout"], p1: Vec2,
                          p2: Vec2 | None = None, offset: float = 8.0, text: str | None = None, angle: float | None = None,
                          at: Vec2 | None = None) -> dict:
    """Smart Dimension in a drawing view: picks in sheet mm, snapped to the view's geometry. linear /
    horizontal / vertical: between two vertices (p1, p2) - the true model distance for edges in the view's
    plane; radius / diameter: p1 on a circle or arc seen face-on. offset: sheet mm from the measured
    geometry; text replaces the value (e.g. '2X R5'); angle: the radius / diameter leader's direction (degrees,
    default towards p1). angle: between the straight edges at p1 and p2, in the quadrant holding `at` (the
    arc goes through it); line_distance: between two parallel straight edges; callout (Hole Callout): p1 on a
    hole, its size and depth (THRU or the depth) as text. auto: a diameter with one point, else horizontal /
    vertical when the two vertices line up that way, otherwise the aligned distance. Picks snap within 3 mm."""
    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        g = DS.view_geometry(APP.active_doc, st, v)
        tol = _PICK_MM / DS.view_scale(st, v)
        q1 = DS.sheet_to_view(st, APP.active_doc, v, p1)
        used = {d["id"] for x in st["sheets"] for d in x.get("dims", [])}
        did = next(f"Dim{i}" for i in range(1, 9999) if f"Dim{i}" not in used)
        k = kind
        if k == "auto" and p2 is None:
            k = "diameter"
        if k == "auto" and DS.snap_vertex(g, q1, tol) is None and DS.pick_line(g, q1, tol) is not None:
            l1 = DS.pick_line(g, q1, tol)
            l2 = DS.pick_line(g, DS.sheet_to_view(st, APP.active_doc, v, p2), tol)
            if l2 is not None:
                k = "angle" if DS.lines_angle(l1, l2, q1) is not None else "line_distance"
        if k in ("angle", "line_distance"):
            return _line_dimension(st, DS, v, sh, g, did, k, q1, p2, tol, offset, text, at)
        if k == "callout":
            return _hole_callout(st, DS, v, sh, g, did, q1, tol, offset, text, angle)
        if k in ("radius", "diameter"):
            c = DS.pick_circle(g, q1, tol)
            if c is None:
                raise DocumentError("no circle or arc seen face-on near that point")
            ang = angle if angle is not None else math.degrees(math.atan2(q1[1] - c[0][1], q1[0] - c[0][0]))
            d = {"id": did, "view": view, "kind": k, "c": list(c[0]), "r": c[1], "angle": ang, "offset": offset}
        else:
            if p2 is None:
                raise DocumentError("a linear dimension needs two points")
            a = DS.snap_vertex(g, q1, tol)
            b = DS.snap_vertex(g, DS.sheet_to_view(st, APP.active_doc, v, p2), tol)
            if a is None or b is None:
                raise DocumentError("pick points on the view's corners or edge ends")
            if a == b:
                raise DocumentError("the two points snap to the same vertex")
            if k == "auto":
                dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
                k = "horizontal" if dy <= 1e-6 * max(dx, 1) else "vertical" if dx <= 1e-6 * max(dy, 1) else "linear"
            d = {"id": did, "view": view, "kind": k, "a": list(a), "b": list(b), "offset": offset}
        if text:
            d["text"] = text
        sh.setdefault("dims", []).append(d)
        return {**d, "value": DS.dim_value(d)}
    return {"dimension": _views_edit(f)}


@command(category="drawing")
def drawing_dimension_value(view: str, text: str, units: Literal["mm", "in", "auto"] = "auto") -> dict:
    """Dimension what in a view measures a given value: text as a drawing shows it ('100', '⌀16', '2X ⌀9',
    'R5'). A diameter / radius goes on the circle or arc of that size; a plain value between two vertices that
    far apart horizontally or vertically - a straight edge of that length first, then the pair on the view's
    outline. The value is in mm, or inches (units='in'; 'auto' tries mm, then inches). Text with a prefix
    ('2X') is kept as the dimension's text."""
    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        parsed = DS.parse_dim_text(text)
        if parsed is None:
            raise DocumentError(f"no number in {text!r}")
        value, decimals = parsed["value"], parsed["decimals"]
        g = DS.view_geometry(APP.active_doc, st, v)
        spec = None
        for factor in {"mm": (1.0,), "in": (25.4,), "auto": (1.0, 25.4)}[units]:  # within the shown rounding
            tol = max(0.5 * 10 ** -decimals, 1e-4) * factor if decimals else min(0.5, 0.01 * value + 1e-3) * factor
            spec = DS.match_dimension(g, parsed["kind"], value * factor, tol)
            if spec is None and parsed["kind"] == "diameter":  # a diameter seen side-on: the width across it
                spec = DS.match_dimension(g, "linear", value * factor, tol)
            if spec:
                break
        if spec is None:
            raise DocumentError(f"nothing in {view} measures {text!r}")
        used = {d["id"] for x in st["sheets"] for d in x.get("dims", [])}
        did = next(f"Dim{i}" for i in range(1, 9999) if f"Dim{i}" not in used)
        same = [d for d in sh.get("dims", []) if d["view"] == v["id"] and d["kind"] == spec["kind"]]
        d = {"id": did, "view": v["id"], **spec, "offset": 8.0 + 7.0 * len(same)}
        if parsed["prefix"] or (parsed["kind"] == "diameter") != (spec["kind"] == "diameter"):
            d["text"] = parsed["text"]
        sh.setdefault("dims", []).append(d)
        return {**d, "value": DS.dim_value(d)}
    return {"dimension": _views_edit(f)}


@command(category="drawing")
def drawing_add_balloon(view: str, point: Vec2, x: float | None = None, y: float | None = None, text: str | None = None) -> dict:
    """Balloon: the BOM item number of the assembly component at `point` (sheet mm, on the component in the
    view), in a circle at (x, y) (default: 15 mm up and right) with a leader to the component; text overrides
    the number."""
    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        q = DS.sheet_to_view(st, APP.active_doc, v, point)
        comp = DS.component_at(APP.active_doc, st, v, q)
        if comp is None and not text:
            raise DocumentError("no assembly component there (balloons label assembly components; give text otherwise)")
        b = _new_balloon(st, sh, view, q, x if x is not None else point[0] + 15, y if y is not None else point[1] - 15,
                         comp.name if comp else None, text)
        return {**b, "item": DS.item_numbers(APP.active_doc, sh).get(b.get("component"))}
    return {"balloon": _views_edit(f)}


@command(category="drawing")
def drawing_auto_balloon(view: str) -> dict:
    """Auto Balloon: one balloon per BOM item seen in the view, around the view with leaders to the parts."""
    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        doc = APP.active_doc
        if getattr(doc, "kind", "part") != "assembly":
            raise DocumentError("auto balloons label the components of an assembly drawing")
        g = DS.view_geometry(doc, st, v)
        t = DS.placement(st, v, g)
        fr = DS.frame(st, v)
        hw, hh = DS.half_size(st, v, g)
        items = DS.item_numbers(doc, sh)
        done = {items.get(b.get("component")) for b in sh.get("balloons", []) if b["view"] == view}
        placed = [(b["x"], b["y"]) for b in sh.get("balloons", []) if b["view"] == view]
        for c, shape in doc.world_shapes():
            if items.get(c.name) in done:
                continue
            anchor = None
            for p3 in [shape.center()] + list(shape.vertices())[:12]:  # a point of it the viewer sees
                q = DS.to2d((p3.X, p3.Y, p3.Z), fr)
                hit = DS.component_at(doc, st, v, q)
                if hit is not None and hit.name == c.name:
                    anchor = q
                    break
            if anchor is None:
                continue
            ax, ay = t(*anchor)
            dx, dy = ax - v["x"], ay - v["y"]
            n = math.hypot(dx, dy)
            ux, uy = (dx / n, dy / n) if n > 1e-6 else (0.7071, -0.7071)
            k = min(hw / max(abs(ux), 1e-9), hh / max(abs(uy), 1e-9)) + 12  # just outside the view
            bx, by = v["x"] + ux * k, v["y"] + uy * k
            if by > v["y"] + hh:  # below the view: clear of its label
                by += 9.0
            while any(math.hypot(bx - px, by - py) < 9.5 for px, py in placed):
                bx, by = bx + ux * 9.5, by + uy * 9.5
            placed.append((bx, by))
            _new_balloon(st, sh, view, anchor, bx, by, c.name)
            done.add(items.get(c.name))
        return [b for b in sh.get("balloons", []) if b["view"] == view]
    return {"balloons": _views_edit(f)}


@command(category="drawing")
def drawing_edit_dimension(id: str, offset: float | None = None, text: str | None = None, angle: float | None = None,
                           kind: Literal["radius", "diameter"] | None = None, tolerance: dict | None = None) -> dict:
    """Change a drawing dimension: its offset from the geometry (sheet mm), override text ('' = show the
    value) or, for radius / diameter, the leader angle (degrees) and which of the two it shows. tolerance:
    {"type": "symmetric", "plus": 0.1} | {"type": "bilateral", "plus": 0.1, "minus": 0.05} | {"type": "limit",
    "plus": .., "minus": ..} | {"type": "fit", "fit": "H7"} | {"type": "none"}; it is shown with the value and
    used by the inspection report instead of the general tolerance."""
    def f(st, DS):
        for sh in st["sheets"]:
            for d in sh.get("dims", []):
                if d["id"] == id:
                    if offset is not None:
                        d["offset"] = offset
                    if text is not None:
                        if text:
                            d["text"] = text
                        else:
                            d.pop("text", None)
                    if angle is not None:
                        d["angle"] = angle
                    if tolerance is not None:
                        from openswx.analysis import tolerance as T
                        try:
                            tol = T.normalize(tolerance)
                            if tol is not None:
                                T.deviations(DS.dim_value(d), tol)  # (a fit must exist at this size)
                        except T.ToleranceError as exc:
                            raise DocumentError(str(exc)) from exc
                        if tol is None:
                            d.pop("tol", None)
                        else:
                            d["tol"] = tol
                    if kind is not None:
                        if d["kind"] not in ("radius", "diameter"):
                            raise DocumentError(f"{id} is not a radius or diameter")
                        d["kind"] = kind
                    return {**d, "value": DS.dim_value(d)}
        raise DocumentError(f"no dimension {id!r}")
    return {"dimension": _views_edit(f)}


@command(category="drawing")
def drawing_delete_dimension(id: str) -> dict:
    """Delete a drawing dimension."""
    def f(st, DS):
        for sh in st["sheets"]:
            before = len(sh.get("dims", []))
            sh["dims"] = [d for d in sh.get("dims", []) if d["id"] != id]
            if len(sh["dims"]) != before:
                return id
        raise DocumentError(f"no dimension {id!r}")
    return {"deleted": _views_edit(f)}


@command(mutates=False, category="drawing")
def drawing_info() -> dict:
    """The drawing's sheets and views (ids, kinds, parents, positions, scales) or, for an automatic drawing,
    its settings."""
    st = _drawing_state(APP.active_doc)
    if not st.get("sheets"):
        return {"automatic": True, "settings": dict(st)}
    from openswx.drawing.drawing_sheets import bom_rows, view_scale

    def table(tb):
        if tb["type"] == "bom" and getattr(APP.active_doc, "kind", "part") == "assembly":
            return {**tb, "rows": bom_rows(APP.active_doc, tb)}
        return tb
    return {"active": st["sheets"][int(st.get("active", 0))]["name"], "title_block": st.get("title", {}),
            "sheets": [{"name": sh["name"], "size": sh.get("size"), "scale": sh.get("scale"), "projection": sh.get("projection"),
                        "views": [{**v, "effective_scale": view_scale(st, v)} for v in sh["views"]],
                        "dimensions": sh.get("dims", []), "tables": [table(tb) for tb in sh.get("tables", [])],
                        "revisions": sh.get("revisions", [])} for sh in st["sheets"]]}


@command(mutates=False, category="drawing")
def export_drawing(path: str, format: Literal["pdf", "dxf", "dwg", "svg"] | None = None) -> dict:
    """Export the active document's drawing to PDF (every sheet, one page each), DXF, DWG (through the free
    ODA File Converter, when installed) or SVG (one file per sheet), with its views, notes, GD&T and tables."""
    import tempfile
    from openswx.drawing.drawing import make_drawing
    from openswx.drawing.drawing_export import svg_to_dxf as _svg_to_dxf, svg_to_pdf

    def svg_to_dxf(svg, out):  # DXF, or DWG converted from it
        if fmt != "dwg":
            return _svg_to_dxf(svg, out)
        from openswx.exchange.dxf_io import dxf_to_dwg
        with tempfile.TemporaryDirectory() as tmp:
            dxf = str(Path(tmp) / (Path(out).stem + ".dxf"))
            _svg_to_dxf(svg, dxf)
            return dxf_to_dwg(dxf, out)
    p = Path(path)
    fmt = (format or p.suffix.lstrip(".") or "pdf").lower()
    if p.suffix.lower() != "." + fmt:
        p = p.with_suffix("." + fmt)
    st = _drawing_state(APP.active_doc)
    if st.get("sheets"):  # every sheet: one PDF page each; DXF / SVG: one file per sheet (name-Sheet2, ...)
        from openswx.drawing.drawing_export import svgs_to_pdf
        from openswx.drawing.drawing_sheets import render_sheet
        svgs = [(sh["name"], render_sheet(APP.active_doc, st, sh)) for sh in st["sheets"]]
        if fmt == "pdf":
            p.write_bytes(svgs_to_pdf([svg for _, svg in svgs]))
            return {"path": str(p.resolve()), "format": fmt, "bytes": p.stat().st_size, "pages": len(svgs)}
        paths = []
        for i, (name, svg) in enumerate(svgs):
            q = p if i == 0 else p.with_name(f"{p.stem}-{name}{p.suffix}")
            if fmt in ("dxf", "dwg"):
                svg_to_dxf(svg, str(q))
            else:
                q.write_text(svg, encoding="utf-8")
            paths.append(str(q.resolve()))
        return {"path": paths[0], "paths": paths, "format": fmt, "bytes": p.stat().st_size}
    svg = make_drawing(APP.active_doc)["svg"]
    if fmt == "pdf":
        p.write_bytes(svg_to_pdf(svg))
    elif fmt in ("dxf", "dwg"):
        svg_to_dxf(svg, str(p))
    else:
        p.write_text(svg, encoding="utf-8")
    return {"path": str(p.resolve()), "format": fmt, "bytes": p.stat().st_size}
