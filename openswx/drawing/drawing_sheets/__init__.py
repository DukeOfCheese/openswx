"""Drawing documents: sheets holding views the user places, as in a SOLIDWORKS drawing.

State (kept in the model document's `drawing` dict, saved with it):
    {"sheets": [sheet, ...], "active": index}
    sheet = {"name", "size" ("A4".."A1"), "scale", "projection" ("third" | "first"), "views": [view, ...],
             "notes", "datums", "gdt" (annotations in sheet mm), "revisions": [row, ...], "tables": [table, ...]}
    view  = {"id", "kind", "x", "y" (sheet mm, centre of the view), "scale" (None: the sheet's),
             "hidden" ("visible" | "removed"), "dims" (show the model's sketch dimensions), "label", ...kind fields}
Kinds: "model" (orientation: front / back / top / bottom / left / right / isometric / dimetric / trimetric),
"projected" (parent, side: left / right / top / bottom; aligned to the parent as in SOLIDWORKS), "section"
(parent, cut line a-b in the parent's 2D model coordinates, flip), "detail" (parent, centre and radius in the
parent's 2D model coordinates, magnification), "auxiliary" (parent, direction in the parent's 2D coordinates:
the view looks square to an inclined edge).

A view's 2D model coordinates are (p . x, p . up) for its direction d (towards the viewer), up and
x = up x d: the kernel's hidden-line projection is taken with the camera looking at the world origin, which
gives exactly these coordinates, so cutting lines and detail circles map back to the model.
"""

from __future__ import annotations

from openswx.drawing import drawing as D
from openswx.drawing.drawing_sheets.annotations import _render_balloons
from openswx.drawing.drawing_sheets.dimensions import (
    _model_dims,
    _render_angle,
    _render_dims,
    _render_line_gap,
    _render_ordinate,
    dim_text,
    dim_value,
    match_dimension,
    parse_dim_text,
)
from openswx.drawing.drawing_sheets.helpers import (
    HID,
    THIN,
    VIS,
    DrawingError,
    _bands,
    _clip_hatch,
    _clip_polys,
    _corners,
    _fit_circle,
    _frame_of,
    _half,
    _kept,
    _polyline,
    _seg_keep,
    _unit,
    _warp,
    _wire_points,
    active_sheet,
    find_view,
    next_id,
    sheet_of,
    to2d,
    to3d,
    view_scale,
)
from openswx.drawing.drawing_sheets.sheets import (
    TITLE_FIELDS,
    _break_lines,
    _center_marks,
    _centerlines,
    _child_marks,
    _default_label,
    _render_view,
    _standard_layout,
    _title_block,
    default_position,
    new_drawing,
    next_letter,
    render_sheet,
    sheet_to_view,
)
from openswx.drawing.drawing_sheets.tables import (
    _bom_table,
    _hole_table,
    _letters,
    _revision_table,
    _sheet_tables,
    _table,
    bom_rows,
    item_numbers,
    view_holes,
)
from openswx.drawing.drawing_sheets.views import (
    BREAK_GAP,
    KINDS,
    LABELS,
    ORIENTATIONS,
    _FAR,
    _broken_out,
    _project,
    _section_shape,
    align,
    component_at,
    frame,
    half_size,
    line_gap,
    lines_angle,
    pick_circle,
    pick_line,
    placement,
    snap_vertex,
    view_circles,
    view_geometry,
)
