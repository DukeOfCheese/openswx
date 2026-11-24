"""openswx as a library: a small object API over the commands, for scripts and notebooks.

    from openswx.api import Part

    part = Part("Bracket")
    part.sketch("Top").rectangle([0, 0], [80, 40]).circle([20, 20], 5).extrude(10)
    part.sketch(part.face(normal=[0, 1, 0])).circle([60, 20], 8).cut()       # through all, on the top face
    part.fillet(2, part.edges(type="circle"))
    print(part.volume, part.mass)
    part.save("bracket.oswprt"); part.export("bracket.step")

Every method runs the command of the same name on this part (it is made the active document first), so
anything not wrapped here is one call away: `part.run("draft", ...)`, and the whole
command set is `openswx.session`. Errors are `openswx.api.Error` (the commands' CommandError). In a notebook a
Part displays as a picture.
"""

from __future__ import annotations

import base64

from openswx import session as _S

Error = _S.CommandError


class Sketch:
    """A sketch of a part. Drawing methods return the sketch, so they chain; `.last` is the last result."""

    def __init__(self, part: "Part", name: str):
        self.part, self.name, self.last = part, name, None

    def _add(self, command: str, **args) -> "Sketch":
        self.last = self.part.run(command, sketch=self.name, **args).get("result")
        return self

    def line(self, start, end) -> "Sketch":
        return self._add("sketch_line", start=start, end=end)

    def centerline(self, start, end) -> "Sketch":
        return self._add("sketch_centerline", start=start, end=end)

    def rectangle(self, corner1, corner2, centered: bool = False) -> "Sketch":
        return self._add("sketch_rectangle", corner1=corner1, corner2=corner2, centered=centered)

    def circle(self, center, radius: float) -> "Sketch":
        return self._add("sketch_circle", center=center, radius=radius)

    def arc(self, center, start, end) -> "Sketch":
        return self._add("sketch_arc", center=center, start=start, end=end)

    def polygon(self, center, radius: float, sides: int = 6) -> "Sketch":
        return self._add("sketch_polygon", center=center, radius=radius, sides=sides)

    def slot(self, center1, center2, width: float) -> "Sketch":
        return self._add("sketch_slot", center1=center1, center2=center2, width=width)

    def dimension(self, kind: str, refs: list[str], value: float) -> "Sketch":
        return self._add("sketch_dimension", kind=kind, refs=refs, value=value)

    def relation(self, kind: str, refs: list[str]) -> "Sketch":
        return self._add("sketch_relation", kind=kind, refs=refs)

    @property
    def status(self) -> str:
        """under_defined / fully_defined / over_defined."""
        return self.part.run("get_feature", name=self.name)["sketch"]["status"]["state"]

    # features made from this sketch (they return the part)
    def extrude(self, depth: float = 10.0, **options) -> "Part":
        self.part.run("extrude", sketch=self.name, depth=depth, **options)
        return self.part

    def cut(self, depth: float | None = None, **options) -> "Part":
        """Extruded cut: blind to `depth`, or through all in both directions when no depth is given."""
        if depth is None:
            options.setdefault("end_condition", "ThroughAllBoth")
            self.part.run("cut_extrude", sketch=self.name, **options)
        else:
            self.part.run("cut_extrude", sketch=self.name, depth=depth, **options)
        return self.part

    def revolve(self, angle: float = 360.0, **options) -> "Part":
        self.part.run("revolve", sketch=self.name, angle=angle, **options)
        return self.part

    def __repr__(self) -> str:
        return f"<Sketch {self.name} of {self.part.name}>"


class Part:
    """A part document. `Part("name")` makes a new one; `Part.open(path)` opens any file openswx reads."""

    def __init__(self, name: str | None = None, _existing: str | None = None):
        self.name = _existing or _S.new_part(name)["name"]

    @classmethod
    def open(cls, path: str, history: bool = True) -> "Part":
        _S.open_part(str(path), history=history)
        doc = _S.APP.active_doc
        if getattr(doc, "kind", "part") != "part":
            raise Error(f"{path} is an assembly; openswx.api.Part is for parts (use openswx.session for assemblies)")
        return cls(_existing=doc.name)

    def run(self, command: str, **args):
        """Run any openswx command on this part."""
        if _S.APP.active != self.name:
            _S.activate_document(self.name)
        result = _S.call(command, args)
        self.name = _S.APP.active if command == "save_part" else self.name  # (Save As renames the document)
        return result

    # sketches and reference geometry
    def sketch(self, plane="Front") -> Sketch:
        """A new sketch on 'Front' / 'Top' / 'Right', a reference plane, or a face (from face() / faces()). A
        sketch on a face parallel to a standard plane keeps that plane's axes and origin (the top of a block
        sketched on Top takes the same coordinates as the Top sketch did)."""
        plane = plane.get("name") or plane["id"] if isinstance(plane, dict) else plane
        return Sketch(self, self.run("create_sketch", plane=plane)["sketch"])

    def plane(self, reference: str = "Front", offset: float = 0.0, **options) -> str:
        return self.run("reference_plane", reference=reference, offset=offset, **options)["feature"]

    # selection: faces and edges as dictionaries with id, name, type, point, normal / length
    def faces(self, type: str | None = None, normal=None, tol: float = 1e-6) -> list[dict]:
        out = self.run("get_topology")["faces"]
        if type:
            out = [f for f in out if f["type"] == type]
        if normal is not None:
            n = sum(c * c for c in normal) ** 0.5
            out = [f for f in out if sum(a * b / n for a, b in zip(f["normal"], normal)) > 1 - tol]
        return out

    def face(self, **criteria) -> dict:
        """The one face matching the criteria (the largest, if several do)."""
        found = self.faces(**criteria)
        if not found:
            raise Error(f"no face matches {criteria}")
        return max(found, key=lambda f: f["area"])

    def edges(self, type: str | None = None) -> list[dict]:
        out = self.run("get_topology")["edges"]
        return [e for e in out if e["type"] == type] if type else out

    @staticmethod
    def _sel(items) -> list[str]:
        items = items if isinstance(items, (list, tuple)) else [items]
        return [(i.get("name") or i["id"]) if isinstance(i, dict) else i for i in items]

    # features
    def fillet(self, radius: float, edges) -> "Part":
        self.run("fillet", radius=radius, edges=self._sel(edges))
        return self

    def chamfer(self, distance: float, edges) -> "Part":
        self.run("chamfer", distance=distance, edges=self._sel(edges))
        return self

    def shell(self, thickness: float, faces) -> "Part":
        self.run("shell", thickness=thickness, faces_to_remove=self._sel(faces))
        return self

    def set(self, dimension: str, value: float) -> "Part":
        """Change a dimension ('D1@Sketch1') or a global variable and rebuild."""
        doc = _S.APP.documents[self.name]
        self.run("set_global_variable", name=dimension, expression=value) if dimension in doc.variables \
            else self.run("set_dimension", dimension=dimension, value=value)
        return self

    def material(self, name: str) -> "Part":
        self.run("set_material", name=name)
        return self

    def undo(self) -> "Part":
        self.run("undo")
        return self

    # results
    @property
    def features(self) -> list[str]:
        return [f["name"] for f in self.run("get_model")["features"]]

    @property
    def properties(self) -> dict:
        return self.run("get_mass_properties")

    @property
    def volume(self) -> float:
        return self.properties["volume"]

    @property
    def mass(self) -> float:
        return self.properties["mass"]

    @property
    def bounding_box(self) -> dict:
        return self.properties["bounding_box"]

    def image(self, view: str = "isometric", width: int = 800, height: int = 600) -> bytes:
        """A PNG picture of the part."""
        return base64.b64decode(self.run("get_view_image", view=view, width=width, height=height)["png_base64"])

    def _repr_png_(self) -> bytes:  # Jupyter shows the part
        return self.image()

    # files
    def save(self, path: str | None = None) -> str:
        return self.run("save_part", **({"path": str(path)} if path else {}))["saved"]

    def export(self, path: str, format: str | None = None) -> str:
        self.run("export_file", path=str(path), **({"format": format} if format else {}))
        return str(path)

    def script(self) -> str:
        """The commands that built this part, as Python (see export_script)."""
        return self.run("export_script")["script"]

    def close(self) -> None:
        _S.close_document(self.name)

    def __repr__(self) -> str:
        return f"<Part {self.name}: {len(self.features)} features>"
