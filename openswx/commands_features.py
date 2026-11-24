"""Part feature, multibody, surface, sheet-metal and weldment commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from typing import Literal

from . import session as _S
from .session import (  # noqa: F401
    APP, DocumentError, Path, Vec2, Vec3, _axis_spec, _edge, _ext_params, _face, _feat, _is_edge_sel, _plane_spec,
    _point_spec, command,
)

__all__ = ["export_flat_pattern_dxf", "set_feature_visibility", "extrude", "cut_extrude", "revolve", "cut_revolve",
           "sweep", "cut_sweep", "loft", "cut_loft", "draft", "hole_wizard", "sketch_pattern", "curve_pattern",
           "table_pattern", "fill_pattern", "variable_fillet", "dome", "rib", "thread", "split_body",
           "cut_with_surface", "intersect", "delete_face", "move_face", "indent", "replace_face", "split_line",
           "projected_curve", "composite_curve", "curve_through_points", "combine_bodies", "delete_body",
           "move_copy_body", "scale_body", "structural_member", "weldment_cut_list", "extruded_surface", "thicken",
           "vertex_chamfer", "face_fillet", "full_round_fillet", "wrap", "hem", "sketched_bend", "jog",
           "corner_relief", "forming_tool_dimple", "miter_flange", "forming_tool_louver", "forming_tool_lance",
           "forming_tool_extruded_flange", "forming_tool_rib", "corner_relief_auto", "revolved_surface",
           "swept_surface", "lofted_surface", "boundary_surface", "planar_surface", "filled_surface",
           "offset_surface", "knit_surface", "trim_surface", "end_cap", "gusset", "base_flange", "edge_flange",
           "flatten", "sheet_metal_info", "rip", "vent", "unfold", "fold", "fillet", "chamfer", "shell", "hole", "linear_pattern", "circular_pattern",
           "mirror", "reference_plane", "reference_axis", "reference_point", "coordinate_system", "helix"]


@command(mutates=False, category="sheet_metal")
def export_flat_pattern_dxf(path: str) -> dict:
    """Sheet metal > Export flat pattern to DXF: the largest planar face of the flattened part (with its
    holes) as 2D geometry in millimetres, for laser / waterjet cutting, plus each bend's centreline (layer
    BEND), a bend note such as "UP 90° R2" seen from the exported face (layer NOTES) and the flat
    bounding box with its size (layer BBOX). Bends of miter flanges have no bend line yet. The part is
    returned to its formed state. Returns the path, the face area (mm2), its size and the notes."""
    from .dxf_io import export_face_outline
    doc = APP.doc
    if doc.ctx.sheet is None:
        raise DocumentError("the part is not sheet metal (no Base Flange)")
    was = doc.flattened
    try:
        if not was:
            doc.flattened = True
            doc.rebuild()
        faces = [f for f in doc.body.faces() if f.geom_type.name.lower() == "plane"]
        big = max(f.area for f in faces)
        from build123d import Vector
        up = Vector(*doc.ctx.sheet["normal"])
        # the largest face; of the two sides of the sheet, the one facing the base flange's normal (so a
        # flange folded up towards it reads UP)
        face = max((f for f in faces if f.area > big * (1 - 1e-6)), key=lambda f: f.normal_at(f.center()).dot(up))
        p = Path(path).with_suffix(".dxf")
        bends = [b for b in (doc.ctx.sheet or {}).get("bends", []) if b.get("flat_line")]
        notes = export_face_outline(face, str(p), bends=bends)
        bb = face.bounding_box()
        return {"path": str(p.resolve()), "area": face.area, "size": [bb.size.X, bb.size.Y, bb.size.Z], "notes": notes}
    finally:
        if not was:
            doc.flattened = False
            doc.rebuild()


@command(category="features")
def set_feature_visibility(name: str, hidden: bool) -> dict:
    """Show or hide a sketch, plane or reference geometry item (axis, point, coordinate system) in the graphics
    area; the standard planes (Front, Top, Right) too."""
    std = name.replace(" Plane", "").strip()
    if std in ("Front", "Top", "Right") and not any(f["name"] == name for f in APP.doc.features):
        doc = APP.doc
        doc._checkpoint()
        names = [n for n in doc.appearance.get("hidden", []) if n != std] + ([std] if hidden else [])
        doc.appearance = {**doc.appearance, "hidden": names}
        doc.revision += 1
        doc.dirty = True
        return doc.summary()
    APP.doc.edit_feature(name, {"hidden": hidden})
    return APP.doc.summary()


@command(category="features")
def extrude(sketch: str, depth: float = 10.0, end_condition: str | int = "Blind", reverse: bool = False,
            draft: float = 0.0, draft_outward: bool = False, direction2_depth: float | None = None,
            direction2_end_condition: str | int | None = None, merge: bool = True, start_offset: float = 0.0,
            up_to: str | Vec3 | None = None, up_to_offset: float = 0.0, reverse_offset: bool = False,
            thin_thickness: float | None = None, thin_side: Literal["one", "other", "mid"] = "one",
            thin_fillet: float | None = None) -> dict:
    """Extruded Boss/Base from a sketch's closed contours. end_condition: Blind | ThroughAll | MidPlane |
    ThroughAllBoth | UpToVertex | UpToSurface | OffsetFromSurface | UpToNext (or SOLIDWORKS swEndConditions_e
    ints 0/1/6/9/3/4/5/11). Depth in mm along the sketch normal (reverse flips it). For Up To Surface / Offset
    From Surface pass up_to='Face#' (+ up_to_offset); for Up To Vertex pass up_to=[x, y, z].
    Optional draft (deg) and a second direction. Thin feature: thin_thickness (mm) makes walls of the sketch
    curves (open or closed) on one side, the other or centred (thin_side); thin_fillet (mm) rounds the
    curves' corners first (auto-fillet corners)."""
    p = _ext_params(sketch, depth, end_condition, reverse, draft, draft_outward, direction2_depth,
                    direction2_end_condition, merge, start_offset, up_to, up_to_offset, reverse_offset)
    if thin_thickness:
        p["thin"] = {"thickness": thin_thickness, "side": thin_side, **({"fillet": thin_fillet} if thin_fillet else {})}
    feat = APP.doc.add_feature("extrude", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def cut_extrude(sketch: str, depth: float = 10.0, end_condition: str | int = "Blind", reverse: bool = False,
                draft: float = 0.0, draft_outward: bool = False, direction2_depth: float | None = None,
                direction2_end_condition: str | int | None = None, flip_side_to_cut: bool = False,
                start_offset: float = 0.0, up_to: str | Vec3 | None = None, up_to_offset: float = 0.0,
                reverse_offset: bool = False, thin_thickness: float | None = None,
                thin_side: Literal["one", "other", "mid"] = "one") -> dict:
    """Extruded Cut. Cuts into the material (opposite the sketch normal by default). Same end
    conditions as extrude (incl. UpToNext / UpToSurface / UpToVertex); flip_side_to_cut keeps only the
    material inside the profile; thin_thickness / thin_side cut a thin wall along the sketch curves."""
    p = _ext_params(sketch, depth, end_condition, reverse, draft, draft_outward, direction2_depth,
                    direction2_end_condition, True, start_offset, up_to, up_to_offset, reverse_offset)
    p["flip_side_to_cut"] = flip_side_to_cut
    if thin_thickness:
        p["thin"] = {"thickness": thin_thickness, "side": thin_side}
    feat = APP.doc.add_feature("cut_extrude", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def revolve(sketch: str, axis: str | None = None, angle: float = 360.0,
            type: Literal["OneDirection", "MidPlane", "TwoDirection"] = "OneDirection", angle2: float | None = None,
            reverse: bool = False, merge: bool = True) -> dict:
    """Revolved Boss/Base. axis: a line entity id in the sketch (default: its first centerline),
    'Sketch2/Line1', 'X'/'Y'/'Z' or an edge id."""
    p = {"sketch": sketch, "axis": _axis_spec(APP.doc, axis), "angle": angle, "type": type,
         "reverse": reverse, "merge": merge}
    if angle2 is not None:
        p["angle2"] = angle2
    feat = APP.doc.add_feature("revolve", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def cut_revolve(sketch: str, axis: str | None = None, angle: float = 360.0,
                type: Literal["OneDirection", "MidPlane", "TwoDirection"] = "OneDirection",
                angle2: float | None = None, reverse: bool = False) -> dict:
    """Revolved Cut (same axis rules as revolve)."""
    p = {"sketch": sketch, "axis": _axis_spec(APP.doc, axis), "angle": angle, "type": type, "reverse": reverse}
    if angle2 is not None:
        p["angle2"] = angle2
    feat = APP.doc.add_feature("cut_revolve", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def sweep(profile: str, path: str, merge: bool = True, frenet: bool = False, twist: float = 0.0,
          guide: str | None = None) -> dict:
    """Swept Boss/Base: sweep the closed contour(s) of the `profile` sketch along the connected curve in the
    `path` sketch (lines/arcs; the path may be open). Draw the profile on a plane crossing the path start.
    twist: degrees the profile turns along the path; guide: a sketch / curve the profile is scaled to follow."""
    p = {"sketch": profile, "path": path, "merge": merge, "frenet": frenet}
    if twist:
        p["twist"] = twist
    if guide:
        p["guide"] = guide
    feat = APP.doc.add_feature("sweep", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def cut_sweep(profile: str, path: str, frenet: bool = False) -> dict:
    """Swept Cut along a path sketch."""
    feat = APP.doc.add_feature("cut_sweep", {"sketch": profile, "path": path, "frenet": frenet})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def loft(profiles: list[str], ruled: bool = False, merge: bool = True, centerline: str | None = None,
         guides: list[str] | None = None) -> dict:
    """Lofted Boss/Base through two or more profile sketches (in order), each with one closed contour,
    e.g. on parallel reference planes. centerline: a sketch / curve through the profiles that the loft
    follows (Centerline Parameters). guides: two or more sketches / curves crossing every profile that the
    loft's side follows (Guide Curves)."""
    p = {"profiles": profiles, "ruled": ruled, "merge": merge}
    if centerline:
        p["centerline"] = centerline
    if guides:
        p["guides"] = list(guides)
    feat = APP.doc.add_feature("loft", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def cut_loft(profiles: list[str], ruled: bool = False) -> dict:
    """Lofted Cut through profile sketches."""
    feat = APP.doc.add_feature("cut_loft", {"profiles": profiles, "ruled": ruled})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def draft(neutral: str, faces: list[str], angle: float, reverse: bool = False) -> dict:
    """Draft (neutral plane): tilt `faces` (ids) by `angle` degrees about the neutral plane
    ('Top', a plane name or a planar face id)."""
    doc = APP.doc
    feat = doc.add_feature("draft", {"neutral": _plane_spec(doc, neutral), "faces": [_face(doc, f) for f in faces],
                                     "angle": angle, "reverse": reverse})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def hole_wizard(face: str, points: list[Vec2], size: str = "M6",
                hole_type: Literal["clearance", "tapped", "counterbore", "countersink"] = "clearance",
                depth: float = 10.0, end_condition: str | int = "ThroughAll") -> dict:
    """Hole Wizard with ISO metric standard sizes (M2..M24): clearance (normal fit), tapped (tap drill),
    counterbore (socket head cap screw) or countersink (90°). points as in `hole`."""
    from .features import hole_wizard_params
    doc = APP.doc
    p = {"face": _face(doc, face), "points": points, "depth": depth, "end_condition": end_condition,
         **hole_wizard_params(size, hole_type), "standard": "ISO", "size": size, "hole_type": hole_type}
    feat = doc.add_feature("hole", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def sketch_pattern(features: list[str], sketch: str, reference: Vec2 | None = None) -> dict:
    """Sketch Driven Pattern: copy features to every sketch point of `sketch` (offsets measured from
    `reference` [x, y] in that sketch, default its first point)."""
    feat = APP.doc.add_feature("sketch_pattern", {"features": features, "sketch": sketch, "reference": reference})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def curve_pattern(features: list[str], path: str, count: int) -> dict:
    """Curve Driven Pattern: `count` instances equally spaced along the curve in sketch `path`."""
    feat = APP.doc.add_feature("curve_pattern", {"features": features, "path": path, "count": count})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def table_pattern(features: list[str], offsets: list[Vec3], csys: str | None = None) -> dict:
    """Table Driven Pattern: one instance per [dx, dy, dz] offset from the seed features (in the axes of
    coordinate system `csys` when given, else the part's)."""
    feat = APP.doc.add_feature("table_pattern", {"features": features, "offsets": offsets, "csys": csys})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def fill_pattern(features: list[str], face: str, spacing: float = 10.0, margin: float = 0.0,
                 layout: str = "square", angle: float = 0.0) -> dict:
    """Fill Pattern: fill a planar `face` with copies of the seed features on a square or staggered
    grid (`spacing` apart, rotated `angle` degrees), keeping centres at least `margin` inside the boundary."""
    feat = APP.doc.add_feature("fill_pattern", {"features": features, "face": _face(APP.doc, face), "spacing": spacing,
                                                "margin": margin, "layout": layout, "angle": angle})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def variable_fillet(edges: list[str], r_start: float, r_end: float) -> dict:
    """Variable Size Fillet: radius changes linearly from r_start to r_end along each edge (ids 'Edge#')."""
    doc = APP.doc
    feat = doc.add_feature("variable_fillet", {"edges": [_edge(doc, e) for e in edges], "r_start": r_start, "r_end": r_end})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def dome(face: str, height: float, reverse: bool = False) -> dict:
    """Dome: spherical cap of `height` mm on a circular planar face (reverse makes a spherical dent)."""
    doc = APP.doc
    feat = doc.add_feature("dome", {"face": _face(doc, face), "height": height, "reverse": reverse})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def rib(sketch: str, thickness: float = 3.0, flip: bool = False) -> dict:
    """Rib from open sketch line(s): thickened symmetrically about the sketch plane and extended in the
    sketch plane until it meets the part (flip chooses the other side)."""
    feat = APP.doc.add_feature("rib", {"sketch": sketch, "thickness": thickness, "flip": flip})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def thread(face: str, pitch: float, length: float, internal: bool = False) -> dict:
    """Modelled ISO 60° thread on a cylindrical face (id 'Face#'): external threads cut into a shaft,
    internal threads cut into a hole wall. e.g. M8 shaft: pitch 1.25."""
    doc = APP.doc
    feat = doc.add_feature("thread", {"face": _face(doc, face), "pitch": pitch, "length": length, "internal": internal})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="bodies")
def split_body(tool: str, keep: Literal["all", "above", "below"] = "all") -> dict:
    """Split: cut the solid bodies with a plane ('Front', a plane name or a planar 'FaceN') or a surface
    body (its feature name). keep 'all' makes every piece a body; 'above' / 'below' keeps one side of the
    plane's (or surface's) normal."""
    doc = APP.doc
    feat = doc.add_feature("split", {"tool": _plane_spec(doc, tool), "keep": keep})
    return {"feature": feat["name"], "bodies": doc.ctx.info.get(feat["name"], {}).get("bodies"), "model": doc.summary()}


@command(category="features")
def cut_with_surface(surface: str, flip: bool = False) -> dict:
    """Cut With Surface: remove the material on the side of a surface body (or plane) its normal points
    to; flip removes the other side."""
    doc = APP.doc
    feat = doc.add_feature("cut_with_surface", {"tool": _plane_spec(doc, surface), "flip": flip})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def intersect(tools: list[str], exclude: list[int] | None = None, merge: bool = True) -> dict:
    """Intersect: split the solid bodies and the `tools` (surface bodies by feature name, planes, planar
    'FaceN') into the regions they enclose - volumes closed only by surfaces become solids too. Returns the
    regions (index, volume, centre); exclude drops regions by index; merge fuses the rest into one body.
    Call once to see the regions, then edit the feature (redefine_feature) to exclude some."""
    doc = APP.doc
    feat = doc.add_feature("intersect", {"tools": [_plane_spec(doc, t) for t in tools], "exclude": list(exclude or []),
                                         "merge": merge})
    return {"feature": feat["name"], **doc.ctx.info.get(feat["name"], {}), "model": doc.summary()}


@command(category="features")
def delete_face(faces: list[str], mode: Literal["patch", "delete"] = "patch") -> dict:
    """Delete Face: 'patch' removes the faces and heals the solid (deletes holes, fillets, bosses); 'delete'
    turns the solid into a surface body without those faces."""
    doc = APP.doc
    feat = doc.add_feature("delete_face", {"faces": [_face(doc, f) for f in faces], "mode": mode})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def move_face(faces: list[str], distance: float) -> dict:
    """Move Face (Offset): move faces along their normals by `distance` mm (positive: out of the material);
    the neighbouring faces extend or trim to meet them."""
    doc = APP.doc
    feat = doc.add_feature("move_face", {"faces": [_face(doc, f) for f in faces], "distance": distance})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def indent(target: int, tool: int, thickness: float = 1.0, clearance: float = 0.0, cut: bool = False,
           region_point: Vec3 | None = None) -> dict:
    """Indent (multibody part): the target body (index) takes the tool body's shape - the tool plus clearance
    is cut from it and, unless cut=True, a wall `thickness` thick wraps the part of the tool sticking out of
    the target (the piece nearest region_point when it sticks out on several sides). The tool body stays."""
    doc = APP.doc
    p = {"target": target, "tool": tool, "thickness": thickness, "clearance": clearance, "cut": cut}
    if region_point is not None:
        p["region_point"] = list(region_point)
    feat = doc.add_feature("indent", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def replace_face(faces: list[str], surface: str) -> dict:
    """Replace Face: make the solid take the shape of a surface body (its feature name) in place of `faces`;
    the neighbouring faces extend or trim to meet it. The surface must reach across the whole face."""
    doc = APP.doc
    feat = doc.add_feature("replace_face", {"faces": [_face(doc, f) for f in faces], "surface": surface})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def split_line(sketch: str, faces: list[str] | None = None, reverse: bool = False) -> dict:
    """Split Line (projection): project a sketch onto faces of the part and split them along it into
    separate faces (the solid is unchanged) - e.g. to draft or colour only part of a face."""
    doc = APP.doc
    p = {"sketch": sketch, "reverse": reverse}
    if faces:
        p["faces"] = [_face(doc, f) for f in faces]
    feat = doc.add_feature("split_line", p)
    return {"feature": feat["name"], **doc.ctx.info.get(feat["name"], {}), "model": doc.summary()}


@command(category="features")
def projected_curve(sketch: str, faces: list[str] | None = None, reverse: bool = False) -> dict:
    """Projected Curve (sketch onto faces): project a sketch's curves along its plane's normal onto faces
    of the part (default: the nearest faces met). The curve can be a sweep path or guide."""
    doc = APP.doc
    p = {"sketch": sketch, "reverse": reverse}
    if faces:
        p["faces"] = [_face(doc, f) for f in faces]
    feat = doc.add_feature("projected_curve", p)
    return {"feature": feat["name"], **doc.ctx.info.get(feat["name"], {})}


@command(category="features")
def composite_curve(items: list[str]) -> dict:
    """Composite Curve: join sketches, curves (3D sketches, helixes, projected curves) and model edges
    ('EdgeN') end to end into one curve."""
    doc = APP.doc
    feat = doc.add_feature("composite_curve", {"items": [_edge(doc, i) if _is_edge_sel(i) else i for i in items]})
    return {"feature": feat["name"], **doc.ctx.info.get(feat["name"], {})}


@command(category="features")
def curve_through_points(points: list[Vec3], closed: bool = False) -> dict:
    """Curve Through XYZ Points: a smooth curve through the points (mm), closed into a loop if asked."""
    feat = APP.doc.add_feature("xyz_curve", {"points": [list(map(float, q)) for q in points], "closed": closed})
    return {"feature": feat["name"], **APP.doc.ctx.info.get(feat["name"], {})}


@command(category="features")
def combine_bodies(main: int, tools: list[int], mode: Literal["add", "subtract", "common"] = "add") -> dict:
    """Combine: add/subtract/intersect tool bodies with the main body (body indices as in get_mass_properties)."""
    feat = APP.doc.add_feature("body_op", {"op": "combine", "main": main, "tools": tools, "mode": mode})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="bodies")
def delete_body(bodies: list[int]) -> dict:
    """Delete/Keep Body: remove the given bodies (by index)."""
    feat = APP.doc.add_feature("body_op", {"op": "delete_body", "bodies": bodies})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="bodies")
def move_copy_body(bodies: list[int], translate: Vec3 = (0, 0, 0), axis: Vec3 = (0, 0, 1), angle: float = 0.0,
                   copy: bool = False) -> dict:
    """Move/Copy Body: rotate about an axis through the origin by `angle` deg, then translate (mm); copy keeps
    the original."""
    feat = APP.doc.add_feature("body_op", {"op": "move_copy", "bodies": bodies, "translate": list(translate),
                                           "axis": list(axis), "angle": angle, "copy": copy})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="bodies")
def scale_body(factor: float) -> dict:
    """Scale all bodies uniformly about the origin."""
    feat = APP.doc.add_feature("body_op", {"op": "scale", "factor": factor})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="weldments")
def structural_member(sketch: str, profile: Literal["square_tube", "rect_tube", "pipe", "angle", "solid_bar", "sketch"] = "square_tube",
                      width: float = 20.0, height: float | None = None, thickness: float = 2.0, merge: bool = True,
                      corner: Literal["miter", "none"] = "miter", profile_sketch: str | None = None,
                      segments: list[str] | None = None) -> dict:
    """Weldments > Structural Member: a standard profile (tube, pipe, angle, bar; sizes in mm) along every
    straight segment of a 2D/3D layout sketch, e.g. a frame. corner='miter' trims members meeting at a corner
    on the bisector plane (end miter); 'none' leaves them butted at the sketch points. profile='sketch' uses
    the closed profile of `profile_sketch` instead (its x, y coordinates: x along the layout sketch's normal,
    the segment at the origin). `segments` limits the members to those sketch lines. merge=False keeps each
    member a body of its own."""
    p = {"sketch": sketch, "profile": profile, "width": width, "height": height if height is not None else width,
         "thickness": thickness, "merge": merge, "corner": corner}
    if profile == "sketch":
        if not profile_sketch:
            raise ValueError("profile='sketch' needs profile_sketch")
        p["profile_sketch"] = profile_sketch
    if segments:
        p["segments"] = list(segments)
    feat = APP.doc.add_feature("structural_member", p)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(mutates=False, category="weldments")
def weldment_cut_list() -> dict:
    """Weldment Cut List: every structural member grouped by profile description and length, with quantities."""
    from collections import Counter
    doc = APP.doc
    groups = Counter()
    for f in doc.features:
        st = doc.state.get(f["name"], {})
        for item in st.get("cut_list", []):
            groups[(st["description"], item["length"])] += 1
    items = [{"item": i + 1, "description": d, "length": L, "qty": q} for i, ((d, L), q) in enumerate(sorted(groups.items()))]
    return {"items": items, "total_members": sum(groups.values())}


@command(category="surfaces")
def extruded_surface(sketch: str, depth: float = 10.0, reverse: bool = False) -> dict:
    """Surfaces > Extruded Surface: extrude the sketch's curves (open profiles allowed) into a surface body."""
    feat = APP.doc.add_feature("extruded_surface", {"sketch": sketch, "depth": depth, "reverse": reverse})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="surfaces")
def thicken(surface: str, thickness: float, flip: bool = False, merge: bool = True) -> dict:
    """Surfaces > Thicken: make a solid from a surface feature (e.g. 'Surface-Extrude1') with a wall thickness."""
    feat = APP.doc.add_feature("thicken", {"surface": surface, "thickness": thickness, "flip": flip, "merge": merge})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def vertex_chamfer(vertex: Vec3, d1: float, d2: float | None = None, d3: float | None = None) -> dict:
    """Chamfer > Vertex: cut off the corner at `vertex` [x, y, z] by the plane through points d1, d2, d3 mm
    along its three edges (all d1 if d2/d3 omitted)."""
    p = {"vertex": vertex, "d1": d1}
    if d2 is not None:
        p.update(d2=d2, d3=d3 if d3 is not None else d2)
    return _feat("vertex_chamfer", p)


@command(category="features")
def face_fillet(faces1: list[str], faces2: list[str], radius: float) -> dict:
    """Fillet > Face Fillet: blend face set 1 with face set 2 ('FaceN' lists) along their common edges."""
    doc = APP.doc
    return _feat("face_fillet", {"faces1": [_face(doc, f) for f in faces1], "faces2": [_face(doc, f) for f in faces2],
                                 "radius": radius})


@command(category="features")
def full_round_fillet(side1: str, center: str, side2: str) -> dict:
    """Fillet > Full Round Fillet: replace planar `center` face between parallel `side1`/`side2` faces with a
    tangent half-round (radius = half their distance)."""
    doc = APP.doc
    return _feat("full_round_fillet", {"side1": _face(doc, side1), "center": _face(doc, center), "side2": _face(doc, side2)})


@command(category="features")
def wrap(sketch: str, face: str, type: Literal["emboss", "deboss", "scribe"] = "emboss", depth: float = 1.0) -> dict:
    """Wrap: wrap a sketch's closed profiles onto a cylindrical face ('FaceN'; sketch x = arc length around
    the axis, y = along it) and emboss/deboss by `depth` mm (scribe = 0.01 mm groove)."""
    return _feat("wrap", {"sketch": sketch, "face": _face(APP.doc, face), "type": type, "depth": depth})


@command(category="sheet_metal")
def hem(edge: str, type: Literal["closed", "open"] = "closed", length: float = 10.0, gap: float | None = None,
        flip: bool = False) -> dict:
    """Sheet Metal > Hem: fold the sheet back 180 deg along a straight edge ('Edge#'); closed = flat on the
    sheet, open = `gap` mm between the faces (default one thickness)."""
    p = {"edge": _edge(APP.doc, edge), "type": type, "length": length, "flip": flip}
    if gap is not None:
        p["gap"] = gap
    return _feat("hem", p)


@command(category="sheet_metal")
def sketched_bend(sketch: str, angle: float = 90.0, bend_radius: float | None = None, flip: bool = False,
                  fixed: Vec3 | None = None) -> dict:
    """Sheet Metal > Sketched Bend: bend the sheet about a single sketch line drawn on its face (the bend
    centreline). `fixed` is a point on the side that stays put; flip bends the other way."""
    p = {"sketch": sketch, "angle": angle, "flip": flip, "fixed": fixed}
    if bend_radius is not None:
        p["bend_radius"] = bend_radius
    return _feat("sketched_bend", p)


@command(category="sheet_metal")
def jog(sketch: str, distance: float, angle: float = 90.0, bend_radius: float | None = None, flip: bool = False,
        fixed: Vec3 | None = None) -> dict:
    """Sheet Metal > Jog: two opposite bends of `angle` along a single sketch line on the sheet face,
    `distance` mm apart in the flat (between bend centrelines); offsets the rest of the sheet. `fixed` is a
    point on the side that stays put; flip jogs the other way."""
    p = {"sketch": sketch, "distance": distance, "angle": angle, "flip": flip, "fixed": fixed}
    if bend_radius is not None:
        p["bend_radius"] = bend_radius
    return _feat("jog", p)


@command(category="sheet_metal")
def corner_relief(point: Vec3, size: float, type: Literal["rectangular", "circular"] = "rectangular") -> dict:
    """Sheet Metal > Corner Relief: rectangular (size x size) or circular (diameter size) relief cut through
    the sheet at a corner point [x, y, z], along the base sheet normal."""
    return _feat("corner_relief", {"point": point, "size": size, "type": type})


@command(category="sheet_metal")
def forming_tool_dimple(face: str, point: Vec3, diameter: float = 20.0, height: float = 5.0, reverse: bool = False) -> dict:
    """Sheet Metal > Forming Tools > Dimple: press a spherical cup (diameter, height) into the sheet at
    `point` on `face` ('FaceN'); the cup wall keeps the sheet thickness."""
    return _feat("dimple", {"face": _face(APP.doc, face), "point": point, "diameter": diameter, "height": height,
                            "reverse": reverse})


@command(category="sheet_metal")
def miter_flange(sketch: str, edge: str | None = None, flip: bool = False, edges: list[str] | None = None) -> dict:
    """Sheet Metal > Miter Flange along a straight edge ('EdgeN') or a connected chain of edges (`edges`):
    `sketch` holds an open profile of lines drawn on a plane perpendicular to the first edge at one of its
    ends (e.g. an L); the profile is thickened by the sheet thickness (flip = to the other side), carried
    around the chain and mitred at the corners."""
    names = edges or ([edge] if edge else [])
    if not names:
        raise DocumentError("give an edge or a list of edges")
    return _feat("miter_flange", {"sketch": sketch, "edges": [_edge(APP.doc, e) for e in names], "flip": flip})


@command(category="sheet_metal")
def forming_tool_louver(face: str, point: Vec3, length: float = 30.0, width: float = 6.0, height: float = 4.0,
                        angle: float = 0.0) -> dict:
    """Sheet Metal > Forming Tools > Louver at `point` on `face`: slot length x width with an open-front hood
    rising `height`; `angle` rotates it in the face plane (deg)."""
    return _feat("louver", {"face": _face(APP.doc, face), "point": point, "length": length, "width": width,
                            "height": height, "angle": angle})


@command(category="sheet_metal")
def forming_tool_lance(face: str, point: Vec3, length: float = 30.0, width: float = 6.0, height: float = 3.0,
                       ramp: float | None = None, angle: float = 0.0) -> dict:
    """Sheet Metal > Forming Tools > Bridge Lance at `point` on `face`: a length x width strip raised `height`
    with ramps (default length = height) at both ends."""
    return _feat("lance", {"face": _face(APP.doc, face), "point": point, "length": length, "width": width,
                           "height": height, "ramp": ramp if ramp is not None else height, "angle": angle})


@command(category="sheet_metal")
def forming_tool_extruded_flange(face: str, point: Vec3, diameter: float = 10.0, height: float = 5.0,
                                 reverse: bool = False) -> dict:
    """Sheet Metal > Forming Tools > Extruded Flange at `point` on `face`: pierce a hole of inner `diameter`
    and draw a collar of sheet thickness `height` out of the face (reverse = out of the opposite face)."""
    return _feat("extruded_flange", {"face": _face(APP.doc, face), "point": point, "diameter": diameter,
                                     "height": height, "reverse": reverse})


@command(category="sheet_metal")
def forming_tool_rib(face: str, point: Vec3, length: float = 40.0, width: float = 8.0, height: float = 2.5,
                     angle: float = 0.0, reverse: bool = False) -> dict:
    """Sheet Metal > Forming Tools > Rib at `point` on `face`: an embossed stiffening bead `length` x `width`
    (overall), raised `height` with a round cross-section and domed ends; `angle` rotates it in the face
    plane (deg), reverse presses it the other way."""
    return _feat("form_rib", {"face": _face(APP.doc, face), "point": point, "length": length, "width": width,
                         "height": height, "angle": angle, "reverse": reverse})


@command(category="sheet_metal")
def corner_relief_auto(size: float | None = None) -> dict:
    """Sheet Metal > Corner Relief > Collect all corners: a square relief (default 2 x thickness) at every
    corner where two flanges meet, through the flange height."""
    p = {} if size is None else {"size": size}
    return _feat("auto_corner_relief", p)


@command(category="surfaces")
def revolved_surface(sketch: str, axis: str, angle: float = 360.0) -> dict:
    """Surfaces > Revolved Surface: revolve the sketch curves about `axis` ('Sketch1/Line3', 'EdgeN', X/Y/Z)."""
    return _feat("revolved_surface", {"sketch": sketch, "axis": _axis_spec(APP.doc, axis), "angle": angle})


@command(category="surfaces")
def swept_surface(profile: str, path: str) -> dict:
    """Surfaces > Swept Surface: sweep an open or closed profile curve sketch along a path (sketch, 3D sketch
    or helix)."""
    return _feat("swept_surface", {"profile": profile, "path": path})


@command(category="surfaces")
def lofted_surface(profiles: list[str], ruled: bool = False) -> dict:
    """Surfaces > Lofted Surface: skin through two or more profile curves (sketches or 3D sketches), in order."""
    return _feat("lofted_surface", {"profiles": profiles, "ruled": ruled})


@command(category="surfaces")
def boundary_surface(direction1: list[str], direction2: list[str] | None = None) -> dict:
    """Surfaces > Boundary Surface: direction1/direction2 are curve sketches or 3D sketches. With both, the
    surface is bounded exactly by the four (or more) curves, which must meet in a closed loop; with only
    direction1, a smooth skin through those curves in order."""
    return _feat("boundary_surface", {"direction1": direction1, "direction2": direction2 or []})


@command(category="surfaces")
def planar_surface(sketch: str) -> dict:
    """Surfaces > Planar Surface: surface faces from the closed regions of a sketch."""
    return _feat("planar_surface", {"sketch": sketch})


@command(category="surfaces")
def filled_surface(boundary: str, points: list[Vec3] | None = None) -> dict:
    """Surfaces > Filled Surface: N-sided patch through a closed boundary (sketch / 3D sketch, may be
    non-planar), optionally through constraint points [x, y, z]."""
    return _feat("filled_surface", {"boundary": boundary, "points": points})


@command(category="surfaces")
def offset_surface(distance: float, surface: str | None = None, faces: list[str] | None = None) -> dict:
    """Surfaces > Offset Surface: offset a surface body, or model faces ('FaceN'), by `distance` mm along their
    normals (0 = copy surface)."""
    doc = APP.doc
    return _feat("offset_surface", {"distance": distance, "surface": surface,
                                    "faces": [_face(doc, f) for f in faces or []]})


@command(category="surfaces")
def knit_surface(surfaces: list[str], form_solid: bool = False, merge: bool = True) -> dict:
    """Surfaces > Knit Surface: sew surface bodies together; form_solid turns a watertight knit into a solid."""
    return _feat("knit_surface", {"surfaces": surfaces, "form_solid": form_solid, "merge": merge})


@command(category="surfaces")
def trim_surface(surface: str, tool: str, keep: Vec3 | None = None, flip: bool = False) -> dict:
    """Surfaces > Trim Surface: cut a surface body with a plane ('Front'/'Top'/'Right', 'Plane1', 'FaceN') and
    keep the side containing point `keep` (default: the side the plane normal points to); flip keeps the other."""
    return _feat("trim_surface", {"surface": surface, "tool": _plane_spec(APP.doc, tool), "keep": keep, "flip": flip})


@command(category="weldments")
def end_cap(member: str, thickness: float = 3.0) -> dict:
    """Weldments > End Cap: close the open ends of a structural member's tubes/pipes with plates."""
    return _feat("end_cap", {"member": member, "thickness": thickness})


@command(category="weldments")
def gusset(face1: str, face2: str, d1: float = 20.0, d2: float = 20.0, thickness: float = 3.0,
           position: float = 0.5) -> dict:
    """Weldments > Gusset: triangular plate in the inside corner between two perpendicular planar faces that
    share an edge; legs d1 (along face1) and d2 (along face2), at `position` (0..1) along the edge."""
    doc = APP.doc
    return _feat("gusset", {"face1": _face(doc, face1), "face2": _face(doc, face2), "d1": d1, "d2": d2,
                            "thickness": thickness, "position": position})


@command(category="sheet_metal")
def base_flange(sketch: str, thickness: float, bend_radius: float | None = None, k_factor: float = 0.5,
                reverse: bool = False) -> dict:
    """Sheet Metal > Base Flange/Tab from a closed sketch; sets thickness, default bend radius and K-factor."""
    feat = APP.doc.add_feature("base_flange", {"sketch": sketch, "thickness": thickness, "reverse": reverse,
                                               "bend_radius": bend_radius if bend_radius is not None else thickness,
                                               "k_factor": k_factor})
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="sheet_metal")
def edge_flange(edge: str, length: float, angle: float = 90.0, bend_radius: float | None = None,
                flip: bool = False) -> dict:
    """Sheet Metal > Edge Flange on a straight edge of the sheet's face (id 'Edge#'): bend through `angle`
    (deg) with inside `bend_radius` (default from the base flange), then a straight flange of `length` mm."""
    doc = APP.doc
    p = {"edge": _edge(doc, edge), "length": length, "angle": angle, "flip": flip}
    if bend_radius is not None:
        p["bend_radius"] = bend_radius
    feat = doc.add_feature("edge_flange", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="sheet_metal")
def flatten(flat: bool = True) -> dict:
    """Sheet Metal > Flatten: show the flat pattern (bends developed with the bend allowance) or fold back."""
    doc = APP.doc
    doc.flattened = flat
    doc.rebuild()
    return _S.sheet_metal_info.__wrapped__() | {"model": doc.summary()}


@command(category="sheet_metal")
def rip(edges: list[str], gap: float = 0.1) -> dict:
    """Sheet Metal > Rip: open a slot `gap` mm wide (default 0.1) along each straight outside corner edge
    ('Edge#') where two flat walls meet, through the wall thickness and centred on the corner, so a hollow
    part (for example a shelled box) can then be unfolded. Returns the feature name and the model."""
    return _feat("rip", {"edges": [_edge(APP.doc, e) for e in edges], "gap": gap})


@command(category="sheet_metal")
def vent(sketch: str, ribs: int = 0, rib_width: float = 2.0, rib_angle: float = 0.0,
         rib_spacing: float | None = None, depth: float | None = None) -> dict:
    """Sheet Metal > Vent: cut the closed outline(s) of `sketch` (drawn on the sheet face) through the sheet,
    leaving `ribs` straight ribs `rib_width` mm wide across the opening at `rib_angle` degrees from the
    sketch x axis, `rib_spacing` mm apart centre to centre (default: spread evenly across the opening).
    `depth` (mm) is only needed on a part without a Base Flange; otherwise the sheet thickness is used."""
    return _feat("vent", {"sketch": sketch, "ribs": ribs, "rib_width": rib_width, "rib_angle": rib_angle,
                          "rib_spacing": rib_spacing, "depth": depth})


@command(category="sheet_metal")
def unfold(bends: list[str]) -> dict:
    """Sheet Metal > Unfold: lay the listed bends flat (bend feature names made before this, e.g.
    ["Edge-Flange1", "Sketched Bend1"]; edge flanges, hems, sketched bends and jogs). Features added after it
    work on the flat sheet, typically a cut across a bend, until a Fold bends them back. Returns the feature."""
    return _feat("unfold", {"bends": list(bends)})


@command(category="sheet_metal")
def fold(bends: list[str] | None = None) -> dict:
    """Sheet Metal > Fold: bend back bends laid flat by an earlier Unfold (default: all of them). Holes and
    notches cut across a bend while it was flat are wrapped onto the bend (their walls run radially), so
    with K-factor 0.5 the folded part keeps the flat part's volume. Returns the feature."""
    return _feat("fold", {"bends": list(bends) if bends else []})


@command(mutates=False, category="sheet_metal")
def sheet_metal_info() -> dict:
    """Sheet metal parameters, bends (angle, radius, bend allowance BA = angle*(r + K*t)) and flat-pattern
    bounding box of the active part."""
    doc = APP.doc
    sheet = doc.ctx.sheet
    if sheet is None:
        raise DocumentError("this part has no sheet metal features")
    bb = doc.body.bounding_box() if doc.body is not None else None
    return {"flattened": doc.flattened, **sheet,
            "bounding_box": {"min": list(bb.min), "max": list(bb.max)} if bb else None}


@command(category="features")
def fillet(radius: float, edges: list[str] | None = None, faces: list[str] | None = None) -> dict:
    """Constant-radius fillet on edges (ids 'Edge4') and/or all edges of faces ('Face2')."""
    doc = APP.doc
    p = {"radius": radius, "edges": [_edge(doc, e) for e in edges or []], "faces": [_face(doc, f) for f in faces or []]}
    feat = doc.add_feature("fillet", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def chamfer(distance: float, edges: list[str] | None = None, faces: list[str] | None = None,
            type: Literal["angle_distance", "distance_distance", "equal_distance"] = "angle_distance",
            angle: float = 45.0, distance2: float | None = None) -> dict:
    """Chamfer edges/faces: angle-distance (default 45 deg), distance-distance or equal distance."""
    doc = APP.doc
    p = {"distance": distance, "type": type, "angle": angle, "distance2": distance2 if distance2 is not None else distance,
         "edges": [_edge(doc, e) for e in edges or []], "faces": [_face(doc, f) for f in faces or []]}
    feat = doc.add_feature("chamfer", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def shell(thickness: float, faces_to_remove: list[str] | None = None, outward: bool = False) -> dict:
    """Shell the body with a wall thickness, removing the selected faces (open faces)."""
    doc = APP.doc
    feat = doc.add_feature("shell", {"thickness": thickness, "outward": outward,
                                     "faces": [_face(doc, f) for f in faces_to_remove or []]})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def hole(face: str, points: list[Vec2], diameter: float, depth: float = 10.0, end_condition: str | int = "Blind",
         type: Literal["simple", "counterbore", "countersink"] = "simple", cbore_diameter: float | None = None,
         cbore_depth: float | None = None, csk_diameter: float | None = None, csk_angle: float = 90.0,
         drill_point: bool = False) -> dict:
    """Hole (Hole Wizard style) on a planar face. points are [x, y] positions in that face's sketch
    coordinates (same frame a sketch on the face would use)."""
    doc = APP.doc
    p = {"face": _face(doc, face), "points": points, "diameter": diameter, "depth": depth, "end_condition": end_condition,
         "type": type, "cbore_diameter": cbore_diameter, "cbore_depth": cbore_depth, "csk_diameter": csk_diameter,
         "csk_angle": csk_angle, "drill_point": drill_point}
    if type == "counterbore" and (cbore_diameter is None or cbore_depth is None):
        raise DocumentError("counterbore needs cbore_diameter and cbore_depth")
    if type == "countersink" and csk_diameter is None:
        raise DocumentError("countersink needs csk_diameter")
    feat = doc.add_feature("hole", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def linear_pattern(features: list[str], direction: str | Vec3, spacing: float, count: int, reverse: bool = False,
                   direction2: str | Vec3 | None = None, spacing2: float = 10.0, count2: int = 1,
                   reverse2: bool = False) -> dict:
    """Linear Pattern of features. direction: 'X'|'Y'|'Z', an edge id, 'Sketch1/Line2' or a vector."""
    doc = APP.doc
    p = {"features": features, "direction": _axis_spec(doc, direction), "spacing": spacing, "count": count, "reverse": reverse}
    if direction2 is not None and count2 > 1:
        p["direction2"] = {"direction": _axis_spec(doc, direction2), "spacing": spacing2, "count": count2, "reverse": reverse2}
    feat = doc.add_feature("linear_pattern", p)
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def circular_pattern(features: list[str], axis: str | dict, count: int, angle: float = 360.0,
                     equal_spacing: bool = True, reverse: bool = False) -> dict:
    """Circular Pattern of features about an axis ('X'|'Y'|'Z', a linear/circular edge id, a
    cylindrical face id, 'Sketch1/Line1' or {'origin':[..],'direction':[..]})."""
    doc = APP.doc
    feat = doc.add_feature("circular_pattern", {"features": features, "axis": _axis_spec(doc, axis), "count": count,
                                                "angle": angle, "equal_spacing": equal_spacing, "reverse": reverse})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def mirror(plane: str, features: list[str] | None = None, bodies: bool = False) -> dict:
    """Mirror features (or the whole body with bodies=True) about 'Front'|'Top'|'Right', a reference
    plane or a planar face id."""
    doc = APP.doc
    feat = doc.add_feature("mirror", {"plane": _plane_spec(doc, plane), "features": features or [], "bodies": bodies})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def reference_plane(reference: str = "Front", offset: float = 0.0, flip: bool = False, angle: float = 0.0,
                    angle_axis: str | None = None) -> dict:
    """Reference geometry > Plane: offset (mm) from a plane or planar face, optionally rotated by
    `angle` degrees about an axis/edge."""
    doc = APP.doc
    feat = doc.add_feature("plane", {"reference": _plane_spec(doc, reference), "offset": offset, "flip": flip,
                                     "angle": angle, "angle_axis": _axis_spec(doc, angle_axis) if angle_axis else None})
    return {"feature": feat["name"], "model": doc.summary()}


@command(category="features")
def reference_axis(reference: str | None = None, planes: list[str] | None = None,
                   points: list[str | Vec3] | None = None) -> dict:
    """Reference geometry > Axis: from one `reference` (linear/circular edge 'EdgeN', cylindrical face
    'FaceN', sketch line 'Sketch1/Line1', X/Y/Z), the intersection of two `planes`, or two `points`
    (each [x, y, z], a reference point name, 'EdgeN' or 'FaceN'). Use its name (Axis1) wherever an
    axis or direction is accepted."""
    doc = APP.doc
    if sum(x is not None for x in (reference, planes, points)) != 1:
        raise DocumentError("select an edge, cylindrical face or sketch line; or two planes; or two points")
    if (planes is not None and len(planes) != 2) or (points is not None and len(points) != 2):
        raise DocumentError("planes / points need exactly two entries")
    feat = doc.add_feature("ref_axis", {"reference": _axis_spec(doc, reference) if reference else None,
                                        "planes": [_plane_spec(doc, x) for x in planes] if planes else None,
                                        "points": [_point_spec(doc, x) for x in points] if points else None})
    return {"feature": feat["name"], "axis": doc.ctx.info.get(feat["name"]), "model": doc.summary()}


@command(category="features")
def reference_point(coords: Vec3 | None = None, reference: str | None = None, offset: Vec3 | None = None) -> dict:
    """Reference geometry > Point at `coords` [x, y, z], or at a `reference`: 'EdgeN' (arc centre or
    midpoint), 'FaceN' (centre), another point name or 'origin'; optional `offset` [dx, dy, dz]."""
    doc = APP.doc
    if (coords is None) == (reference is None):
        raise DocumentError("select an edge, face or point, or enter coordinates")
    feat = doc.add_feature("ref_point", {"coords": coords, "reference": _point_spec(doc, reference) if reference else None,
                                         "offset": offset})
    return {"feature": feat["name"], "point": doc.ctx.info.get(feat["name"]), "model": doc.summary()}


@command(category="features")
def coordinate_system(origin: str | Vec3 | None = None, x_axis: str | Vec3 | None = None,
                      y_axis: str | Vec3 | None = None) -> dict:
    """Reference geometry > Coordinate System: `origin` (point spec, default the part origin), X axis and
    Y axis directions ([x, y, z], axis name, 'EdgeN', X/Y/Z); Z = X x Y. Its name works as a sketch
    plane (its XY plane) and as the frame of a table pattern."""
    doc = APP.doc
    feat = doc.add_feature("csys", {"origin": _point_spec(doc, origin) if origin is not None else None,
                                    "x_axis": _axis_spec(doc, x_axis) if isinstance(x_axis, str) else x_axis,
                                    "y_axis": _axis_spec(doc, y_axis) if isinstance(y_axis, str) else y_axis})
    return {"feature": feat["name"], "csys": doc.ctx.info.get(feat["name"]), "model": doc.summary()}


@command(category="features")
def helix(pitch: float, radius: float, revolutions: float = 1.0, height: float | None = None, axis: str = "Z",
          origin: Vec3 | None = None, start_angle: float = 0.0, counterclockwise: bool = False,
          reverse: bool = False, taper: float = 0.0) -> dict:
    """Curves > Helix/Spiral: pitch (mm/rev), radius, revolutions (or height), about `axis` (X/Y/Z, axis
    name, 'EdgeN') through `origin`; taper (deg) gives a conical helix. Usable as a sweep path."""
    doc = APP.doc
    feat = doc.add_feature("helix", {"pitch": pitch, "radius": radius, "revolutions": revolutions, "height": height,
                                     "axis": _axis_spec(doc, axis), "origin": origin, "start_angle": start_angle,
                                     "counterclockwise": counterclockwise, "reverse": reverse, "taper": taper})
    return {"feature": feat["name"], "info": doc.ctx.info.get(feat["name"]), "model": doc.summary()}
