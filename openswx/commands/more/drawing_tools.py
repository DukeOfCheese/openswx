"""Drawing extras: surface finish, weld symbols, stacked balloons, layers, sheet formats and alternate positions."""
from __future__ import annotations

import json
from typing import Literal

from openswx.core.document import DocumentError
from openswx.session import Vec2, command


@command(category="drawing")
def drawing_add_surface_finish(x: float, y: float, ra: str | float | None = None,
                               process: Literal["any", "required", "prohibited"] = "required", lay: str | None = None,
                               leader: Vec2 | None = None, layer: str | None = None) -> dict:
    """Surface finish symbol (ISO 1302) with its tip at (x, y) sheet mm: process any / required (machining) /
    prohibited; ra the roughness (e.g. 1.6 or 'Rz 6.3'); lay symbol (=, X, M, C, R...); optional leader."""
    from openswx.commands.support import _drawing_edit, _next_ann

    def f(st):
        it = {"id": _next_ann(st, "Finish"), "x": x, "y": y, "ra": ra, "process": process, "lay": lay, "leader": leader,
              "layer": layer}
        st.setdefault("finishes", []).append(it)
        return it
    return {"finish": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_weld_symbol(x: float, y: float, leader: Vec2,
                            type: Literal["fillet", "v_groove", "square", "bevel", "plug", "spot", "u_groove", "j_groove"] = "fillet",
                            side: Literal["arrow", "other", "both"] = "arrow", size: str | float | None = None,
                            length: str | float | None = None, all_around: bool = False, field: bool = False,
                            process: str | None = None, layer: str | None = None) -> dict:
    """Weld symbol: reference line from (x, y) sheet mm with an arrow to the joint at `leader`; the weld's
    symbol on the arrow side, the other side or both; size and length; all-around and field-weld marks;
    process (e.g. 'MIG') in the tail."""
    from openswx.commands.support import _drawing_edit, _next_ann

    def f(st):
        it = {"id": _next_ann(st, "Weld"), "x": x, "y": y, "leader": leader, "type": type, "side": side, "size": size,
              "length": length, "all_around": all_around, "field": field, "process": process, "layer": layer}
        st.setdefault("welds", []).append(it)
        return it
    return {"weld": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_stacked_balloon(view: str, point: Vec2, items: list[str], x: float | None = None, y: float | None = None,
                                direction: Literal["down", "right"] = "down") -> dict:
    """Stacked balloon: one leader to the component at `point` and a stack of balloons - the component's item
    number first, then `items` (component names, which show their BOM numbers, or plain text) touching it."""
    from openswx.commands.support import _new_balloon, _views_edit
    from openswx.session import APP as _APP

    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        q = DS.sheet_to_view(st, _APP.active_doc, v, point)
        comp = DS.component_at(_APP.active_doc, st, v, q)
        b = _new_balloon(st, sh, view, q, x if x is not None else point[0] + 15, y if y is not None else point[1] - 15,
                         comp.name if comp else None, None if comp else (items[0] if items else "?"))
        b["stack"], b["direction"] = list(items if comp else items[1:]), direction
        return b
    return {"balloon": _views_edit(f)}


def _all_annotations(st):
    holders = [st] + list(st.get("sheets", []))
    for h in holders:
        for k in ("notes", "datums", "gdt", "finishes", "welds", "dims", "balloons"):
            yield from h.get(k, [])


@command(category="drawing")
def drawing_add_layer(name: str, color: str = "#000000", visible: bool = True) -> dict:
    """Drawing layer (or change one): a colour ('#rrggbb') and whether it shows; annotations and dimensions
    put on it (drawing_set_layer) take its colour and visibility."""
    from openswx.commands.support import _drawing_edit

    def f(st):
        st.setdefault("layers", {})[name] = {"color": color, "visible": visible}
        return st["layers"]
    return {"layers": _drawing_edit(f)}


@command(category="drawing")
def drawing_set_layer(items: list[str], layer: str | None) -> dict:
    """Put annotations / dimensions (their ids: Note1, Dim3, Weld1...) on a layer (None: no layer)."""
    from openswx.commands.support import _drawing_edit

    def f(st):
        if layer is not None and layer not in st.get("layers", {}):
            raise DocumentError(f"no layer {layer!r}: add it with drawing_add_layer")
        found = []
        for it in _all_annotations(st):
            if it.get("id") in items:
                it["layer"] = layer
                found.append(it["id"])
        missing = set(items) - set(found)
        if missing:
            raise DocumentError(f"no annotation {sorted(missing)[0]!r}")
        return found
    return {"moved": _drawing_edit(f)}


@command(mutates=False, category="drawing")
def list_sheet_formats() -> dict:
    """Sheet formats (title-block templates): the presets and the ones saved by the user."""
    from openswx.drawing.drawing_more import list_formats
    return list_formats()


@command(category="drawing")
def drawing_set_sheet_format(preset: str | None = None, format: dict | None = None) -> dict:
    """Use a sheet format for the drawing's title block: a preset / saved name ('ISO 7200', 'Minimal', ...),
    a format {name, width, height, cells: [{x, y, w, h, label, text, size, bold}]} (text can hold {title},
    {dwg_no}, {material}, {scale}, {sheet}, {date}, {drawn}, {rev}, {company}, {$PRP:"Name"}), or neither to go
    back to the standard block."""
    from openswx.drawing.drawing_more import check_format, list_formats
    from openswx.commands.support import _drawing_edit
    if preset is not None:
        allf = list_formats()
        fmt = allf["saved"].get(preset) or allf["presets"].get(preset)
        if fmt is None:
            raise DocumentError(f"no sheet format {preset!r}")
    else:
        fmt = format
    try:
        fmt = check_format(fmt) if fmt is not None else None
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc

    def f(st):
        if fmt is None:
            st.pop("format", None)
        else:
            st["format"] = fmt
        return fmt
    return {"format": _drawing_edit(f)}


@command(mutates=False, category="drawing")
def drawing_save_sheet_format(name: str) -> dict:
    """Save the drawing's current sheet format under a name, for other drawings."""
    from openswx.drawing.drawing_more import formats_dir
    from openswx.commands.support import _drawing_state
    fmt = _drawing_state().get("format")
    if not fmt:
        raise DocumentError("the drawing uses the standard title block: set a sheet format first")
    p = formats_dir() / f"{name}.json"
    p.write_text(json.dumps({**fmt, "name": name}, indent=1), encoding="utf-8")
    return {"saved": str(p)}


@command(category="drawing")
def drawing_view_alternate_position(view: str, position: str | None) -> dict:
    """Alternate Position View: show an assembly view's alternate position (save_alternate_position) over it in
    phantom lines; None removes it."""
    from openswx.commands.support import _views_edit
    from openswx.session import APP as _APP
    if position is not None and position not in (getattr(_APP.active_doc, "alternate_positions", {}) or {}):
        raise DocumentError(f"no alternate position {position!r}")

    def f(st, DS):
        v = DS.find_view(st, view)
        if position is None:
            v.pop("alt_position", None)
        else:
            v["alt_position"] = position
        return v
    return {"view": _views_edit(f)}


__all__ = ["drawing_add_surface_finish", "drawing_add_weld_symbol", "drawing_add_stacked_balloon",
           "drawing_add_layer", "drawing_set_layer", "drawing_set_sheet_format",
           "drawing_save_sheet_format", "list_sheet_formats", "drawing_view_alternate_position"]
