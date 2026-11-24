"""Assembly commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from typing import Literal

from . import session as _S
from .session import (  # noqa: F401
    APP, DocumentError, PartDocument, Path, Vec3, _BOLT_LENGTHS, _asm_ref, _import_file, command, json,
)

__all__ = ["new_assembly", "insert_component", "add_mate", "redefine_mate", "edit_mate", "delete_mate",
           "move_component", "rotate_component", "fix_component", "delete_component", "component_pattern",
           "mirror_components", "delete_assembly_feature", "explode_step", "set_exploded", "motion_study",
           "motion_export", "edit_in_context", "return_to_assembly", "replace_component", "assembly_hole",
           "assembly_cut_extrude", "assembly_bom", "list_fasteners", "insert_fastener", "smart_fasteners",
           "check_interference"]


@command(category="assembly")
def new_assembly(name: str | None = None) -> dict:
    """Create a new assembly document (File > New > Assembly) and make it active."""
    from .assembly import AssemblyDocument
    APP.drop_throwaway()
    APP._doc_counter += 1
    name = name or f"Assem{APP._doc_counter}"
    asm = AssemblyDocument(name)
    APP.documents[name] = asm
    APP.active = name
    return APP.active_doc.summary()


@command(category="assembly")
def insert_component(source: str, position: Vec3 | None = None, rotation_deg: Vec3 | None = None,
                     fixed: bool | None = None) -> dict:
    """Insert a part into the active assembly: `source` is an open part document name (stays linked: edits
    to the part update the assembly) or a file path (.oswprt/.oswasm/.step/.x_t/.x_b/.sldprt). The first
    component is fixed.
    position in mm, rotation_deg as XYZ Euler angles."""
    asm = APP.asm
    if source in APP.documents:  # an open part, or an open assembly inserted as a subassembly
        part, link = APP.documents[source], source
    else:
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(source)
        if p.suffix.lower() == ".oswasm":
            from .assembly import AssemblyDocument
            part = AssemblyDocument(p.stem)
            part.live_docs = APP.documents
            part.load_dict(json.loads(p.read_text(encoding="utf-8")))
        elif p.suffix.lower() == ".oswprt":
            part = PartDocument.open(str(p))
        else:
            part = PartDocument(p.stem)
            _import_file(part, str(p))
        link = None
    comp = asm.insert(part, link, position or [0, 0, 0], rotation_deg or [0, 0, 0], fixed)
    return {"component": comp.name, "assembly": asm.summary()}


@command(category="assembly")
def add_mate(type: Literal["coincident", "parallel", "perpendicular", "distance", "angle", "concentric", "tangent",
                           "lock", "width", "gear", "symmetric", "slot", "limit_distance", "limit_angle", "cam", "path",
                           "rack_pinion", "screw", "hinge", "universal_joint", "linear_coupler"],
             entity1: str | dict, entity2: str | dict, value: float | None = None, aligned: bool = False,
             entity3: str | dict | None = None, entity4: str | dict | None = None, value2: float | None = None) -> dict:
    """Add a mate between component faces given as 'Component/Face#' (from get_topology), or a component's
    own reference planes as 'Component/Front Plane' (Top Plane, Right Plane).
    coincident/distance put planar faces face-to-face (aligned=True keeps normals the same way);
    concentric takes cylindrical faces; tangent = planar face + cylindrical face; distance in mm, angle in deg.
    lock: entity1/entity2 are component names (keeps their current relative position).
    width: entity1+entity3 = the two slot faces of component A, entity2+entity4 = the tab faces of component B.
    gear: entity1/entity2 = cylindrical faces of the two gears, value = ratio (teeth B / teeth A); turning A
    by θ turns B by -θ/ratio (use rotate_component to drive it).
    symmetric: entity1/entity2 = two planar (or two cylindrical) faces, entity3 = the symmetry plane face.
    slot: entity1 + entity3 = the slot's two parallel side faces, entity2 = the pin's cylindrical face.
    limit_distance / limit_angle: planar faces kept between value (min) and value2 (max).
    cam: entity1 = cam face (any shape), entity2 = roller's cylindrical face (rolls tangent on the cam).
    path: entity1 = path edge 'Comp/EdgeN', entity2 = cylindrical face whose axis point follows the path.
    rack_pinion: entity1 = the rack's linear edge 'Comp/EdgeN' (its direction of travel), entity2 = the
    pinion's cylindrical face, value = pinion pitch diameter (travel = turn x diameter / 2).
    screw: entity1/entity2 = cylindrical faces, value = pitch (mm per turn): concentric, and B advances
    along the axis as it turns relative to A.
    hinge: entity1/entity2 = cylindrical faces (concentric), entity3/entity4 = planar faces that touch.
    universal_joint: entity1/entity2 = the two shafts' cylindrical faces; B turns with A through a
    Cardan joint (tan thB cos beta = tan thA). Turn a driving component with rotate_component.
    linear_coupler: entity1/entity2 = a planar face (its normal) or linear edge of each component giving its
    direction; B travels value (ratio, default 1; negative: opposite) times A's travel."""
    asm = APP.asm
    comp = lambda sel: sel["component"] if isinstance(sel, dict) else sel.split("/")[0]  # noqa: E731
    if type == "lock":
        a, b = {"component": comp(entity1)}, {"component": comp(entity2)}
        mate = asm.add_mate(type, a, b)
    else:
        def ref(sel, edge_ok):
            return asm.edge_ref_for(sel) if edge_ok and isinstance(sel, str) and "/Edge" in sel else _asm_ref(sel)
        e1 = (entity1 if isinstance(entity1, dict) else asm.edge_ref_for(entity1)) if type in ("path", "rack_pinion")             else ref(entity1, type == "linear_coupler")
        if type == "cam":
            e1 = {**e1, "as_shape": True}
        mate = asm.add_mate(type, e1, ref(entity2, type == "linear_coupler"), value, aligned,
                            _asm_ref(entity3) if entity3 else None, _asm_ref(entity4) if entity4 else None, value2)
    return {"mate": mate["name"], "assembly": asm.summary()}


@command(category="assembly")
def redefine_mate(name: str, type: str, entity1: str | dict, entity2: str | dict, value: float | None = None,
                  aligned: bool = False, entity3: str | dict | None = None, entity4: str | dict | None = None,
                  value2: float | None = None) -> dict:
    """Edit a mate in full (Edit Feature on a mate): new type, selections (as for add_mate; a mate's stored
    references are accepted as they are) and values. It keeps its name and place among the mates."""
    asm = APP.asm
    old = next((m for m in asm.mates if m["name"] == name), None)
    if old is None:
        raise DocumentError(f"no mate {name!r}")
    at, counter = asm.mates.index(old), asm.mate_counter
    asm._checkpoint()
    asm.mates.remove(old)
    try:
        _S.add_mate(type, entity1, entity2, value, aligned, entity3, entity4, value2)
    except Exception:
        asm.mates.insert(at, old)
        asm._undo.pop()
        asm.solve()
        raise
    asm._undo.pop()  # (one undo step: the checkpoint before the old mate came out)
    new = asm.mates.pop()
    new["name"], asm.mate_counter = name, counter
    asm.mates.insert(at, new)
    asm.solve()
    return {"mate": name, "assembly": asm.summary()}


@command(category="assembly")
def edit_mate(name: str, value: float | None = None, aligned: bool | None = None) -> dict:
    """Change a mate's value (distance/angle) or alignment."""
    APP.asm.edit_mate(name, value, aligned)
    return APP.asm.summary()


@command(category="assembly")
def delete_mate(name: str) -> dict:
    """Delete a mate by name (e.g. 'Coincident1'); the assembly is solved again without it."""
    APP.asm.delete_mate(name)
    return APP.asm.summary()


@command(category="assembly")
def move_component(name: str, position: Vec3 | None = None, rotation_deg: Vec3 | None = None) -> dict:
    """Move/rotate a component (then mates are re-solved; fully mated components snap back)."""
    APP.asm.move(name, position, rotation_deg)
    return APP.asm.summary()


@command(category="assembly")
def rotate_component(name: str, axis: Vec3, angle_deg: float) -> dict:
    """Rotate a component about a world axis direction through its own origin (Rotate Component); mates
    such as gear mates drive the connected components."""
    from scipy.spatial.transform import Rotation as _R
    asm = APP.asm
    c = asm.component(name)
    import numpy as _np
    ax = _np.array(axis, float) / _np.linalg.norm(axis)
    asm._checkpoint()
    steps = max(1, int(_np.ceil(abs(angle_deg) / 30.0)))  # turn in small steps so driven mates follow (stay on branch)
    for _ in range(steps):
        c.x[:3] = (_R.from_rotvec(ax * _np.radians(angle_deg / steps)) * _R.from_rotvec(c.x[:3])).as_rotvec()
        asm.solve(hold={name: c.x[:3].copy()})  # hold the driven angle; the part may still slide (screw, rack)
    return asm.summary()


@command(category="assembly")
def fix_component(name: str, fixed: bool = True) -> dict:
    """Fix or float a component."""
    APP.asm.set_fixed(name, fixed)
    return APP.asm.summary()


@command(category="assembly")
def delete_component(name: str) -> dict:
    """Remove a component and its mates."""
    APP.asm.delete_component(name)
    return APP.asm.summary()


@command(category="assembly")
def component_pattern(components: list[str], kind: Literal["linear", "circular"], count: int, spacing: float = 20.0,
                      direction: Vec3 | None = None, axis_origin: Vec3 | None = None, axis: Vec3 | None = None,
                      angle: float = 360.0) -> dict:
    """Component pattern: linear (`count` instances `spacing` mm along `direction`) or circular (`count` about
    the axis through `axis_origin` along `axis`, over `angle` deg). Instances follow the seed's mates."""
    asm = APP.asm
    f = asm.add_component_pattern(kind, components, count, spacing, direction or [1, 0, 0], axis_origin or [0, 0, 0],
                                  axis or [0, 0, 1], angle)
    return {"feature": f["name"], "assembly": asm.summary()}


@command(category="assembly")
def mirror_components(components: list[str], plane: Literal["Front", "Top", "Right"] | None = None,
                      plane_origin: Vec3 | None = None, plane_normal: Vec3 | None = None) -> dict:
    """Mirror Components: opposite-hand copies reflected through 'Front' (XY), 'Top' (XZ), 'Right' (YZ) or a
    plane given by origin + normal. The copies follow the seeds."""
    normal = {"Front": [0, 0, 1], "Top": [0, 1, 0], "Right": [1, 0, 0]}.get(plane or "", plane_normal)
    if normal is None:
        raise DocumentError("give a standard plane or plane_normal")
    asm = APP.asm
    f = asm.mirror_component(components, plane_origin or [0, 0, 0], normal)
    return {"feature": f["name"], "assembly": asm.summary()}


@command(category="assembly")
def delete_assembly_feature(name: str) -> dict:
    """Delete a component pattern / mirror feature and its instances."""
    APP.asm.delete_feature(name)
    return APP.asm.summary()


@command(category="assembly")
def explode_step(components: list[str], direction: Vec3, distance: float) -> dict:
    """Exploded View: add a step moving `components` `distance` mm along `direction` (display only; mates
    keep the assembled positions). The view is switched to exploded."""
    step = APP.asm.add_explode_step(components, direction, distance)
    return {"step": step, "assembly": APP.asm.summary()}


@command(category="assembly")
def set_exploded(exploded: bool, clear: bool = False) -> dict:
    """Explode / collapse the assembly view; clear=True deletes all explode steps."""
    APP.asm.set_exploded(exploded, clear)
    return APP.asm.summary()


@command(mutates=False, category="assembly")
def motion_study(mate: str, start: float, end: float, frames: int = 24) -> dict:
    """Basic motion: drive a distance/angle mate from `start` to `end` like a motor and return every
    component's pose per frame ([rotvec rad, translation mm]); the assembly is left unchanged."""
    return {"mate": mate, "frames": APP.asm.motion(mate, start, end, frames)}


@command(mutates=False, category="assembly")
def motion_export(mate: str, start: float, end: float, path: str, frames: int = 36,
                  view: Literal["isometric", "front", "top", "right", "trimetric"] = "isometric",
                  width: int = 640, height: int = 480, fps: int = 12) -> dict:
    """Motion study > Save as animation: drive a distance/angle mate from start to end and write the frames
    as a video (.avi, MJPEG) or an animated .gif (fixed camera). The assembly is unchanged."""
    import io
    import numpy as np
    from PIL import Image
    from .render import render_png
    asm = APP.asm
    fr = asm.motion(mate, start, end, frames)
    saved = [c.x.copy() for c in asm.components]
    pts = []
    try:
        for f in fr:  # camera framing: union of every frame's geometry
            for c in asm.components:
                c.x = np.array(f["poses"][c.name])
            for face in asm.mesh()["faces"]:
                pts.append(np.array(face["v"], float).reshape(-1, 3))
        fit = np.vstack(pts)
        lo, hi = fit.min(axis=0), fit.max(axis=0)
        box = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        images = []
        for f in fr:
            for c in asm.components:
                c.x = np.array(f["poses"][c.name])
            png = render_png(asm, view=view, width=width, height=height, fit_points=box)
            images.append(Image.open(io.BytesIO(png)).convert("RGB"))
    finally:
        for c, x in zip(asm.components, saved):
            c.x = x
    out = Path(path)
    if out.suffix.lower() == ".avi":
        from .render import write_mjpeg_avi
        jpegs = []
        for im in images:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            jpegs.append(buf.getvalue())
        write_mjpeg_avi(str(out), jpegs, images[0].width, images[0].height, max(int(fps), 1))
    else:
        out = out.with_suffix(".gif")
        pal = [im.convert("P", palette=Image.ADAPTIVE) for im in images]
        pal[0].save(out, save_all=True, append_images=pal[1:], duration=int(1000 / max(fps, 1)), loop=0)
    return {"path": str(out.resolve()), "frames": len(images), "bytes": out.stat().st_size}


@command(category="assembly")
def edit_in_context(component: str) -> dict:
    """Edit Part in the context of the assembly: make the component's part the active document, with the
    other components shown around it (ghosted, in the part's coordinates). Changes update the assembly.
    return_to_assembly goes back."""
    asm = APP.asm
    c = asm.component(component)
    if c.is_subassembly:
        raise DocumentError("edit a subassembly's parts from the subassembly itself")
    if not (c.link and c.link in APP.documents):  # inserted from a file: open its part as a linked document
        doc = c.doc(asm.live_docs)
        name = c.link or c.name.rsplit("-", 1)[0]
        while name in APP.documents and APP.documents[name] is not doc:
            name += "_1"
        APP.documents[name] = doc
        for other in asm.components:  # every instance of that part follows the open document
            if other.part is c.part:
                other.link = name
        c.link = name
    APP.context = {"assembly": APP.active, "component": component, "part": c.link}
    APP.active = c.link
    return {"editing": c.link, "component": component, "model": APP.active_doc.summary()}


@command(category="assembly")
def return_to_assembly() -> dict:
    """Leave Edit Part (in context) and go back to the assembly, rebuilt with the part's changes."""
    ctx = APP.context
    if not ctx or ctx["assembly"] not in APP.documents:
        raise DocumentError("not editing a part in an assembly's context")
    APP.context = None
    APP.active = ctx["assembly"]
    asm = APP.asm
    asm.revision += 1
    asm.solve()
    return asm.summary()


@command(category="assembly")
def replace_component(name: str, source: str, all_instances: bool = False) -> dict:
    """Replace Components: swap a component's part for another (an open part document name or a file), in
    the same place; all_instances replaces every instance of that part. Mates whose faces the new part no
    longer has show as failing."""
    asm = APP.asm
    if source in APP.documents:
        part, link = APP.documents[source], source
    else:
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(source)
        part, link = (PartDocument.open(str(p)) if p.suffix.lower() == ".oswprt" else None), None
        if part is None:
            part = PartDocument(p.stem)
            _import_file(part, str(p))
    done = asm.replace_component(name, part, link, all_instances)
    return {"replaced": done, "failing_mates": asm.status.get("failing", []), "assembly": asm.summary()}


@command(category="assembly")
def assembly_hole(position: Vec3, direction: Vec3, diameter: float, depth: float | None = None,
                  components: list[str] | None = None) -> dict:
    """Assembly Hole (Hole Series): drill a hole through the assembled components from `position` along
    `direction` (mm, world), to `depth` or through everything; components limits which ones it cuts. The
    cut belongs to the assembly - the part files are unchanged."""
    feat = APP.asm.add_cut("hole", components, position=list(position), direction=list(direction),
                           diameter=diameter, depth=depth)
    return {"feature": feat["name"], "assembly": APP.asm.summary()}


@command(category="assembly")
def assembly_cut_extrude(profile: list[Vec3], depth: float | None = None, reverse: bool = False,
                         components: list[str] | None = None) -> dict:
    """Assembly Extruded Cut: cut the closed planar polygon `profile` (world points, mm) along its normal
    (reverse: the other way) to `depth` or through all, in the assembly only (parts unchanged)."""
    feat = APP.asm.add_cut("extrude", components, profile=[list(q) for q in profile], depth=depth, reverse=reverse)
    return {"feature": feat["name"], "assembly": APP.asm.summary()}


@command(mutates=False, category="assembly")
def assembly_bom() -> dict:
    """Bill of materials of the active assembly: item, part number, description (the part's Description
    property) and quantity, parts grouped by source."""
    from .drawing_sheets import bom_rows
    asm = APP.asm
    rows = bom_rows(asm, {})
    for r in rows:
        if not r["description"]:
            comp = next(c for c in asm.components if (c.link or c.name.rsplit("-", 1)[0]) == r["part"])
            props = {k.lower(): v for k, v in getattr(comp.doc(asm.live_docs), "properties", {}).items()}
            r["description"] = props.get("description", "")
    return {"bom": rows}


@command(mutates=False, category="assembly")
def list_fasteners() -> dict:
    """Hardware library (Toolbox): fastener types, standards and sizes."""
    from .hardware import catalog
    return catalog()


@command(category="assembly")
def insert_fastener(kind: str, size: str, length: float | None = None, position: Vec3 | None = None,
                    rotation_deg: Vec3 | None = None) -> dict:
    """Insert a standard fastener into the active assembly (Toolbox; list_fasteners for kinds and sizes).
    ISO: hex_bolt (4017), socket_cap_screw (4762), button_head_screw (7380), countersunk_screw (10642),
    set_screw (4026), hex_nut (4032), thin_nut (4035), washer (7089), lock_washer (DIN 127), dowel_pin (8734,
    size = diameter); lengths in mm. ANSI inch: ansi_hex_bolt, ansi_socket_cap_screw, ansi_hex_nut,
    ansi_washer (sizes like '1/4-20', '#10-24'); lengths in inches. Screws point down -Z from the head
    underside at the origin; mate it concentric + coincident to place it."""
    from .hardware import make_fastener
    part = make_fastener(kind, size, length)
    comp = APP.asm.insert(part, None, position or [0, 0, 0], rotation_deg or [0, 0, 0], None)
    return {"component": comp.name, "assembly": APP.asm.summary()}


@command(category="assembly")
def smart_fasteners(component: str, kind: Literal["hex_bolt", "socket_cap_screw"] = "hex_bolt",
                    from_top: bool = True) -> dict:
    """Smart Fasteners: find the cylindrical holes of `component`, pick the ISO size whose clearance diameter
    matches each hole, pick a standard length (hole depth + one diameter), insert the screws and mate them
    concentric to the hole with the head seated on the entry face (top-most end unless from_top=False)."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    from .features import ISO_HOLES
    from .geom import face_normal, face_ref
    from .hardware import HEX_BOLT, make_fastener
    asm = APP.asm
    comp = asm.component(component)
    body = asm._local_body(comp)
    placed = []
    for face in body.faces():
        if face.geom_type.name.lower() != "cylinder":
            continue
        circles = [e for e in face.edges() if e.geom_type.name.lower() == "circle"]
        if len(circles) < 2:
            continue
        c = face.center()
        axis_pt = circles[0].arc_center
        radial = (c - axis_pt) - circles[0].normal() * (c - axis_pt).dot(circles[0].normal())
        if radial.length < 1e-9 or face_normal(face, c).dot(radial.normalized()) > 0:
            continue  # outward-facing cylinder: a boss, not a hole
        dia = 2 * circles[0].radius
        size = min((s for s in ISO_HOLES if s in HEX_BOLT), key=lambda s: abs(ISO_HOLES[s][0] - dia))
        if abs(ISO_HOLES[size][0] - dia) > 0.25 * dia:
            continue  # not a clearance hole for any ISO size
        ends = sorted(circles[:2], key=lambda e: comp.to_world([e.arc_center.X, e.arc_center.Y, e.arc_center.Z])[1],
                      reverse=from_top)
        entry, exit_ = ends[0].arc_center, ends[1].arc_center
        depth = (entry - exit_).length
        nominal = float(size[1:])
        length = next((L for L in _BOLT_LENGTHS if L >= depth + nominal), _BOLT_LENGTHS[-1])
        out_dir = (entry - exit_).normalized()  # pointing out of the hole at the entry side
        seat = next((f for f in body.faces() if f.geom_type.name.lower() == "plane"
                     and any(e.is_same(ends[0]) for e in f.edges())), None)
        if seat is None:
            continue
        P = comp.to_world([entry.X, entry.Y, entry.Z])
        n_w = comp.dir_to_world([out_dir.X, out_dir.Y, out_dir.Z])
        rot, _ = Rotation.align_vectors([n_w], [[0, 0, 1]])
        part = make_fastener(kind, size, length)
        bolt = asm.insert(part, None, list(P), list(rot.as_euler("xyz", degrees=True)), False)
        bbody = asm._local_body(bolt)
        shank = max((f for f in bbody.faces() if f.geom_type.name.lower() == "cylinder"
                     and abs(next(e for e in f.edges() if e.geom_type.name.lower() == "circle").radius - nominal / 2) < 1e-6),
                    key=lambda f: f.area)
        under = next(f for f in bbody.faces() if f.geom_type.name.lower() == "plane" and abs(f.center().Z) < 1e-6
                     and f.normal_at().Z < -0.99)
        asm.add_mate("concentric", {"component": component, "face": face_ref(face)}, {"component": bolt.name, "face": face_ref(shank)})
        asm.add_mate("coincident", {"component": component, "face": face_ref(seat)}, {"component": bolt.name, "face": face_ref(under)})
        placed.append({"component": bolt.name, "size": size, "length": length, "hole_diameter": round(dia, 4),
                       "position": [round(float(v), 6) for v in np.asarray(bolt.x[3:])]})
    if not placed:
        raise DocumentError(f"no clearance holes found in {component}")
    return {"fasteners": placed, "assembly": asm.summary()}


@command(mutates=False, category="assembly")
def check_interference() -> dict:
    """Interference Detection: volumes where components overlap (envelopes are left out)."""
    shapes = [x for x in APP.asm.world_shapes() if not getattr(x[0], "envelope", False)]
    hits = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            common = shapes[i][1].intersect(shapes[j][1])
            items = common if isinstance(common, (list, tuple)) else [common]
            v = sum(s.volume for it in items if it is not None for s in it.solids())
            if v > 1e-6:
                hits.append({"a": shapes[i][0].name, "b": shapes[j][0].name, "volume": v})
    return {"interferences": hits}
