"""Assemblies: components (part documents placed with 6-DOF transforms) and mates solved by least squares.

Mate types follow SOLIDWORKS standard mates: coincident, parallel, perpendicular, distance, angle,
concentric (cylindrical faces / circular edges), with aligned / anti-aligned options for planar mates.
The first inserted component is fixed, as in SOLIDWORKS.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
from build123d import Compound, Location, Vector
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from openswx.core.document import DocumentError, PartDocument
from openswx.core.features import mass_properties
from openswx.core.geom import face_normal, face_ref, resolve_face, tessellate

from openswx.system import snapshots  # noqa: E402

FORMAT = "openswx-assembly"
MATES = {"coincident", "parallel", "perpendicular", "distance", "angle", "concentric", "tangent", "lock", "width", "gear",
         "symmetric", "slot", "limit_distance", "limit_angle", "cam", "path", "rack_pinion", "screw", "hinge", "universal_joint", "linear_coupler",
         "connection"}
MOTION_MATES = {"rack_pinion", "screw", "universal_joint", "linear_coupler"}  # measured from the poses when the mate was added


class Component:
    def __init__(self, name: str, part: dict, link: str | None, x: list[float], fixed: bool, derived: dict | None = None):
        self.name, self.part, self.link, self.fixed = name, part, link, fixed
        self.envelope = False  # an envelope: shown, but not in mass properties, the BOM or interference
        self.speedpak = False  # SpeedPak: shown simplified (small holes and fillets removed)
        self.x = np.array(x, float)  # rotation vector (rad) + translation (mm)
        self.derived = derived  # {"feature", "seed", ...}: placed by a pattern / mirror, not by mates
        self._doc = None

    @property
    def is_subassembly(self) -> bool:
        return self.part.get("format") == FORMAT

    def doc(self, live_docs: dict | None = None):
        if live_docs and self.link in live_docs and getattr(live_docs[self.link], "body", None) is not None \
                and getattr(live_docs[self.link], "kind", "part") == ("assembly" if self.is_subassembly else "part"):
            return live_docs[self.link]  # follow the open document, like SOLIDWORKS updates assemblies
        if self._doc is None:
            if self.is_subassembly:  # subassembly: a rigid group of its components
                self._doc = AssemblyDocument(self.part.get("name", "SubAssem"))
                self._doc.live_docs = live_docs or {}
            else:
                self._doc = PartDocument(self.part.get("name", "Part"))
            self._doc.load_dict(self.part)
        return self._doc

    def matrix(self, x=None):
        x = self.x if x is None else x
        return Rotation.from_rotvec(x[:3]).as_matrix(), x[3:]

    def to_world(self, p, x=None):
        R, t = self.matrix(x)
        return R @ np.asarray(p, float) + t

    def dir_to_world(self, d, x=None):
        R, _ = self.matrix(x)
        return R @ np.asarray(d, float)

    def location(self) -> Location:
        R, t = self.matrix()
        rv = self.x[:3]
        ang = float(np.linalg.norm(rv))
        loc = Location(Vector(*t))
        if ang > 1e-12:
            axis = rv / ang
            loc = Location(Vector(*t)) * Location(Vector(0, 0, 0), Vector(*axis), math.degrees(ang))
        return loc


ASSEMBLY = "(assembly)"  # mate entities on the assembly's own reference planes name this component


class _AssemblyFrame(Component):
    """The assembly's own frame as a mate entity owner: fixed at the origin (its Front / Top / Right planes)."""

    def __init__(self):
        super().__init__(ASSEMBLY, {}, None, [0.0] * 6, True)


def plane_ref(name: str) -> dict | None:
    """A component's standard reference plane by name ('Front', 'Top Plane', ...) as a local point + normal."""
    n = {"front": (0, 0, 1), "top": (0, 1, 0), "right": (1, 0, 0)}.get(name.lower().replace(" plane", "").strip())
    return None if n is None else {"point": [0.0, 0.0, 0.0], "normal": list(n)}


class AssemblyDocument:
    kind = "assembly"

    def __init__(self, name: str = "Assem1"):
        self.name, self.path = name, None
        self.components: list[Component] = []
        self.mates: list[dict] = []
        self.status: dict = {}
        self.revision, self.dirty = 0, False
        self.live_docs: dict = {}
        self._undo: list = []  # (snapshots.Snapshot: long strings shared between steps)
        self._redo: list = []
        self.mate_counter = 0
        self.journal: list | None = []  # (see PartDocument.journal)
        # configurations: which components are suppressed and which mate values differ, per configuration.
        # Suppressed components (and their mates) are parked: out of self.components / self.mates, so nothing
        # that walks those lists (solving, mass, BOM, interference, drawings) sees them.
        self.configurations: dict = {"Default": {"suppressed": [], "mate_values": {}}}
        self.active_config = "Default"
        self._parked: list = []  # [(index in the full list, Component)]
        self._parked_mates: list = []  # [(index in the full list, mate)]
        self.display_states: dict = {}  # name -> {"hidden": [component names]}
        self.display_state: str | None = None
        self.features: list[dict] = []  # component patterns / mirror components
        self.explode: dict = {"steps": [], "active": False}
        self.drawing: dict = {}
        self.study: dict = {"fixtures": [], "loads": [], "element_size": None}

    def to_dict(self) -> dict:
        return {"format": FORMAT, "version": 1, "name": self.name, "mate_counter": self.mate_counter,
                "components": [{"name": c.name, "part": c.part, "link": c.link, "x": c.x.tolist(), "fixed": c.fixed,
                                **({"derived": c.derived} if c.derived else {}), **({"envelope": True} if c.envelope else {}),
                                **({"speedpak": True} if c.speedpak else {})} for c in self._all_components()],
                "mates": self._all_mates(base=True), "features": self.features,
                "configurations": self.configurations, "active_config": self.active_config,
                "display_states": self.display_states, "display_state": self.display_state, "explode": self.explode, "drawing": self.drawing, "study": self.study,
                "named_views": getattr(self, "named_views", {}),
                "alternate_positions": getattr(self, "alternate_positions", {}),
                "large_assembly_mode": getattr(self, "large_assembly_mode", False), "render": getattr(self, "render", {}),
                "electrical": getattr(self, "electrical", {}), "composer": getattr(self, "composer", {}),
                "requirements": getattr(self, "requirements", []),
                **({"journal": self.journal} if getattr(self, "journal", None) is not None else {}),
                **getattr(self, "_extra", {})}  # (keys of the file this version does not use: written back)

    def load_dict(self, d: dict) -> None:
        from openswx.exchange import migrate
        try:
            d = migrate.migrate(copy.deepcopy(d), FORMAT)
        except migrate.FormatError as exc:
            raise DocumentError(str(exc)) from exc
        self._extra = migrate.extras(d, {"format", "version", "name", "mate_counter", "components", "mates", "features",
                                         "explode", "drawing", "study", "named_views", "alternate_positions",
                                         "large_assembly_mode", "render", "electrical", "composer", "journal", "requirements",
                                         "configurations", "active_config", "display_states", "display_state"})
        self._parked, self._parked_mates = [], []
        self.configurations = d.get("configurations") or {"Default": {"suppressed": [], "mate_values": {}}}
        self.active_config = d.get("active_config", "Default") if d.get("active_config") in self.configurations else "Default"
        self.display_states = d.get("display_states", {})
        self.display_state = d.get("display_state") if d.get("display_state") in self.display_states else None
        self.requirements = d.get("requirements", [])
        self.journal = d.get("journal")
        self.name = d.get("name", self.name)
        self.mate_counter = d.get("mate_counter", len(d.get("mates", [])))
        self.components = [Component(c["name"], c["part"], c.get("link"), c["x"], c.get("fixed", False), c.get("derived"))
                           for c in d.get("components", [])]
        for c, cd in zip(self.components, d.get("components", [])):
            c.envelope, c.speedpak = bool(cd.get("envelope")), bool(cd.get("speedpak"))
        self.alternate_positions = d.get("alternate_positions", {})
        self.large_assembly_mode = bool(d.get("large_assembly_mode", False))
        self.mates = d.get("mates", [])
        self.features = d.get("features", [])
        self.explode = d.get("explode", {"steps": [], "active": False})
        self.drawing = d.get("drawing", {})
        self.study = d.get("study", {"fixtures": [], "loads": [], "element_size": None})
        self.named_views = d.get("named_views", {})
        self.render = d.get("render", {})
        self.electrical = d.get("electrical", {})
        self.composer = d.get("composer", {})
        self._park()
        self.solve()

    def save(self, path: str | None = None) -> str:
        path = path or self.path
        if not path:
            raise DocumentError("no file path given")
        p = Path(path)
        if p.suffix.lower() != ".oswasm":
            p = p.with_suffix(".oswasm")
        for c in self.components:  # embed the current state of linked parts
            if not (c.derived and "mirror" in c.derived):
                c.part = c.doc(self.live_docs).to_dict()
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        self.path, self.name, self.dirty = str(p), p.stem, False
        return str(p)

    def _all_components(self) -> list:
        out = list(self.components)
        for i, c in sorted(self._parked, key=lambda e: e[0]):
            out.insert(min(i, len(out)), c)
        return out

    def _all_mates(self, base: bool = False) -> list:
        out = list(self.mates)
        for i, m in sorted(self._parked_mates, key=lambda e: e[0]):
            out.insert(min(i, len(out)), m)
        if base:  # as saved: every mate with its Default value (the overrides are in the configurations)
            out = [({**{k: v for k, v in m.items() if k != "_base"}, "value": m["_base"]} if "_base" in m else m) for m in out]
        return out

    def _unpark(self) -> None:
        self.components, self.mates = self._all_components(), self._all_mates()
        self._parked, self._parked_mates = [], []
        for m in self.mates:
            if "_base" in m:
                m["value"] = m.pop("_base")

    def _park(self) -> None:
        """Put the active configuration in force: its suppressed components (with what is patterned from them,
        and every mate touching them) leave the working lists, and its mate values replace the Default ones."""
        self._unpark()
        cfg = self.configurations.setdefault(self.active_config, {"suppressed": [], "mate_values": {}})
        names = {c.name for c in self.components}
        cfg["suppressed"] = [n for n in cfg.get("suppressed", []) if n in names]  # (deleted since: forgotten)
        gone = set(cfg["suppressed"])
        gone |= {c.name for c in self.components if c.derived and c.derived.get("seed") in gone}
        if gone:
            self._parked = [(i, c) for i, c in enumerate(self.components) if c.name in gone]
            self.components = [c for c in self.components if c.name not in gone]
            hit = lambda m: m["a"]["component"] in gone or m["b"]["component"] in gone  # noqa: E731
            self._parked_mates = [(i, m) for i, m in enumerate(self.mates) if hit(m)]
            self.mates = [m for m in self.mates if not hit(m)]
        if self.active_config != "Default":
            for m in self.mates:
                v = cfg.get("mate_values", {}).get(m["name"])
                if v is not None:
                    m["_base"], m["value"] = m.get("value"), v

    def add_configuration(self, name: str, copy_from: str | None = None) -> None:
        if not name or name in self.configurations:
            raise DocumentError(f"configuration {name!r} already exists" if name else "give the configuration a name")
        src = self.configurations.get(copy_from or self.active_config)
        if src is None:
            raise DocumentError(f"no configuration {copy_from!r}")
        self._checkpoint()
        self.configurations[name] = copy.deepcopy(src)
        self.active_config = name
        self._park()
        self.solve()

    def activate_configuration(self, name: str) -> None:
        if name not in self.configurations:
            raise DocumentError(f"no configuration {name!r}; this assembly has {list(self.configurations)}")
        self._checkpoint()
        self.active_config = name
        self._park()
        self.solve()

    def delete_configuration(self, name: str) -> None:
        if name == "Default":
            raise DocumentError("the Default configuration cannot be deleted")
        if name not in self.configurations:
            raise DocumentError(f"no configuration {name!r}")
        self._checkpoint()
        del self.configurations[name]
        if self.active_config == name:
            self.active_config = "Default"
        self._park()
        self.solve()

    def suppress_component(self, name: str, suppressed: bool = True, configuration: str | None = None) -> None:
        cfg_name = configuration or self.active_config
        if cfg_name not in self.configurations:
            raise DocumentError(f"no configuration {cfg_name!r}")
        if name not in {c.name for c in self._all_components()}:
            raise DocumentError(f"no component {name!r}")
        self._checkpoint()
        cfg = self.configurations[cfg_name]
        cur = [n for n in cfg.get("suppressed", []) if n != name]
        if suppressed:
            cur.append(name)
        elif cfg_name == self.active_config:
            self._unpark()  # (so the name is known again when the list is pruned)
        cfg["suppressed"] = cur
        self._park()
        self.solve()

    def save_display_state(self, name: str, hidden: list[str]) -> None:
        if not name:
            raise DocumentError("give the display state a name")
        known = {c.name for c in self._all_components()}
        bad = [h for h in hidden if h not in known]
        if bad:
            raise DocumentError(f"no component(s) {bad}")
        self._checkpoint()
        self.display_states[name] = {"hidden": list(dict.fromkeys(hidden))}
        self.display_state = name
        self.revision += 1
        self.dirty = True

    def activate_display_state(self, name: str | None) -> None:
        if name is not None and name not in self.display_states:
            raise DocumentError(f"no display state {name!r}; this assembly has {list(self.display_states)}")
        self._checkpoint()
        self.display_state = name
        self.revision += 1
        self.dirty = True

    def delete_display_state(self, name: str) -> None:
        if name not in self.display_states:
            raise DocumentError(f"no display state {name!r}")
        self._checkpoint()
        del self.display_states[name]
        if self.display_state == name:
            self.display_state = None
        self.revision += 1
        self.dirty = True

    def _checkpoint(self):
        self._undo.append(snapshots.pack(self.to_dict()))
        self._undo = self._undo[-50:]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            raise DocumentError("nothing to undo")
        self._redo.append(snapshots.pack(self.to_dict()))
        self.load_dict(snapshots.unpack(self._undo.pop()))

    def redo(self):
        if not self._redo:
            raise DocumentError("nothing to redo")
        self._undo.append(snapshots.pack(self.to_dict()))
        self.load_dict(snapshots.unpack(self._redo.pop()))

    def component(self, name: str) -> Component:
        if name == ASSEMBLY:
            return self.__dict__.setdefault("_frame", _AssemblyFrame())
        for c in self.components:
            if c.name == name:
                return c
        raise DocumentError(f"no component {name!r}")

    def insert(self, part, link: str | None, position=(0, 0, 0), rotation=(0, 0, 0), fixed=None) -> Component:
        if part is self:
            raise DocumentError("an assembly cannot contain itself")
        if part.body is None:
            raise DocumentError(f"{part.name} has no solid body to insert")
        self._checkpoint()
        base = part.name
        n = self._instance_number(base)
        rv = Rotation.from_euler("xyz", rotation, degrees=True).as_rotvec()
        comp = Component(f"{base}-{n}", part.to_dict(), link, [*rv, *position],
                         fixed if fixed is not None else not self.components)
        self.components.append(comp)
        self.solve()
        return comp

    def move(self, name: str, position=None, rotation=None) -> None:
        c = self.component(name)
        self._checkpoint()
        if position is not None:
            c.x[3:] = position
        if rotation is not None:
            c.x[:3] = Rotation.from_euler("xyz", rotation, degrees=True).as_rotvec()
        if not c.fixed:  # the dragged part leads (driven mates follow it), then it settles if fully mated
            self.solve(hold_pose={name: c.x.copy()})
        self.solve()

    def set_fixed(self, name: str, fixed: bool) -> None:
        self._checkpoint()
        self.component(name).fixed = fixed
        self.solve()

    def replace_component(self, name: str, part, link: str | None, all_instances: bool = False) -> list[str]:
        """Replace Components: swap a component's part (keeping where it is); mates are re-resolved on the new
        part and any that no longer find their faces are reported failing. Returns the replaced names."""
        c = self.component(name)
        self._checkpoint()
        targets = [x for x in self.components if x.part is c.part] if all_instances else [c]
        data = part.to_dict() if hasattr(part, "to_dict") else part
        for x in targets:
            x.part, x.link, x._doc = data, link, None
        self.__dict__.pop("_ent_cache", None)
        self.__dict__.pop("_cut_cache", None)
        self.solve()
        return [x.name for x in targets]

    def delete_component(self, name: str) -> None:
        self.component(name)
        self._checkpoint()
        gone = {name} | {c.name for c in self.components if c.derived and c.derived.get("seed") == name}
        self.components = [c for c in self.components if c.name not in gone]
        self.mates = [m for m in self.mates if m["a"]["component"] not in gone and m["b"]["component"] not in gone]
        self.solve()

    def _instance_number(self, base: str) -> int:
        """The next instance number of a part: one past the highest in use (counting instances gave a deleted
        component's number to the next one inserted - or the number of one still there)."""
        used = [0]
        for c in self.components:
            head, _, tail = c.name.rpartition("-")
            if head == base and tail.isdigit():
                used.append(int(tail))
        return max(used) + 1

    def _feature_name(self, base: str) -> str:
        used = [0] + [int(f["name"][len(base):]) for f in self.features
                      if f["name"].startswith(base) and f["name"][len(base):].isdigit()]
        return f"{base}{max(used) + 1}"

    def _new_instance(self, seed: Component, feature: str, **extra) -> Component:
        base = seed.name.rsplit("-", 1)[0]
        n = self._instance_number(base)
        comp = Component(f"{base}-{n}", seed.part, seed.link, seed.x.tolist(), False,
                         {"feature": feature, "seed": seed.name, **extra})
        self.components.append(comp)
        return comp

    def add_component_pattern(self, kind: str, seeds: list[str], count: int, spacing: float = 0.0,
                              direction=(1, 0, 0), axis_origin=(0, 0, 0), axis_dir=(0, 0, 1),
                              angle: float = 360.0) -> dict:
        """Linear / circular component pattern; instances follow their seed when it moves."""
        if kind not in ("linear", "circular"):
            raise DocumentError("pattern kind must be linear or circular")
        if count < 2:
            raise DocumentError("count must be at least 2")
        for sname in seeds:
            if self.component(sname).derived:
                raise DocumentError(f"{sname} is itself a pattern/mirror instance")
        self._checkpoint()
        name = self._feature_name("LocalLPattern" if kind == "linear" else "LocalCirPattern")
        feat = {"name": name, "type": kind, "seeds": seeds, "count": int(count), "spacing": float(spacing),
                "direction": list(direction), "axis_origin": list(axis_origin), "axis_dir": list(axis_dir), "angle": float(angle)}
        self.features.append(feat)
        for sname in seeds:
            for k in range(1, count):
                self._new_instance(self.component(sname), name, index=k)
        self.solve()
        return feat

    def mirror_component(self, seeds: list[str], plane_origin=(0, 0, 0), plane_normal=(1, 0, 0)) -> dict:
        """Mirror Components (opposite hand): driven copies reflected through a world plane."""
        for sname in seeds:
            self.component(sname)
        if np.linalg.norm(plane_normal) < 1e-12:
            raise DocumentError("mirror plane normal is zero")
        self._checkpoint()
        name = self._feature_name("MirrorComponent")
        feat = {"name": name, "type": "mirror", "seeds": seeds, "origin": list(plane_origin), "normal": list(plane_normal)}
        self.features.append(feat)
        for sname in seeds:
            c = self._new_instance(self.component(sname), name, mirror=True)
            c.x = np.zeros(6)
        self.solve()
        return feat

    def delete_feature(self, name: str) -> None:
        if not any(f["name"] == name for f in self.features):
            raise DocumentError(f"no assembly feature {name!r}")
        self._checkpoint()
        gone = {c.name for c in self.components if c.derived and c.derived.get("feature") == name}
        self.features = [f for f in self.features if f["name"] != name]
        self.components = [c for c in self.components if c.name not in gone]
        self.mates = [m for m in self.mates if m["a"]["component"] not in gone and m["b"]["component"] not in gone]
        self.solve()

    def _update_derived(self) -> None:
        feats = {f["name"]: f for f in self.features}
        for c in self.components:
            if not c.derived:
                continue
            f = feats.get(c.derived["feature"])
            try:
                seed = self.component(c.derived["seed"])
            except DocumentError:
                continue
            if f is None or f["type"] == "mirror":
                continue
            k = c.derived.get("index", 1)
            Rs, ts = seed.matrix()
            if f["type"] == "linear":
                d = np.array(f["direction"], float)
                d = d / (np.linalg.norm(d) or 1.0)
                c.x = np.array([*seed.x[:3], *(ts + d * f["spacing"] * k)])
            else:
                ang = math.radians(f["angle"]) / (f["count"] if abs(f["angle"] - 360.0) < 1e-9 else f["count"] - 1)
                ax = np.array(f["axis_dir"], float)
                R = Rotation.from_rotvec(ax / np.linalg.norm(ax) * ang * k)
                o = np.array(f["axis_origin"], float)
                c.x = np.array([*(R * Rotation.from_matrix(Rs)).as_rotvec(), *(R.apply(ts - o) + o)])

    def _local_body(self, c: Component):
        """A component's geometry in its own frame (mirrored components: the reflected seed, placed at identity)."""
        if c.derived and c.derived.get("mirror"):
            f = next((f for f in self.features if f["name"] == c.derived["feature"]), None)
            seed = self.component(c.derived["seed"])
            body = seed.doc(self.live_docs).body
            if f is None or body is None:
                return None
            from build123d import Plane
            return self._apply_cuts(c, body.moved(seed.location()).mirror(Plane(origin=f["origin"], z_dir=f["normal"])))
        return self._apply_cuts(c, c.doc(self.live_docs).body)

    def _apply_cuts(self, c: Component, body):
        """Assembly cut features (Hole Series / assembly Extruded Cut) on a component, in its own frame: they
        belong to the assembly, the part file is untouched. Cached per pose and set of features."""
        cuts = [f for f in self.features if f["type"] == "cut" and (not f.get("components") or c.name in f["components"])]
        if body is None or not cuts:
            return body
        key = (c.name, id(body), tuple(np.round(c.x, 9)), json.dumps(cuts, sort_keys=True))
        cache = self.__dict__.setdefault("_cut_cache", {})
        hit = cache.get(key)
        if hit is not None and hit[0] is body:
            return hit[1]
        out = body
        inv = c.location().inverse()
        for f in cuts:
            res = out.cut(_cut_tool(f, self).moved(inv))
            out = Compound(list(res.solids())) if res is not None else out
        if len(cache) > 64:
            cache.clear()
        cache[key] = (body, out)
        return out

    def add_cut(self, kind: str, components: list[str] | None = None, **p) -> dict:
        """An assembly cut: kind 'hole' (position, direction, diameter, depth or through) or 'extrude' (a
        closed planar polygon `profile` of world points, cut along its normal - reverse flips - to depth or
        through). components: the ones it cuts (default: every component it meets)."""
        for name in components or []:
            self.component(name)
        self._checkpoint()
        feat = {"name": self._feature_name("Hole" if kind == "hole" else "Cut-Extrude"), "type": "cut", "kind": kind,
                "components": list(components or []), **p}
        _cut_tool(feat, self)  # validate
        self.features.append(feat)
        self.revision += 1
        self.dirty = True
        return feat

    def add_explode_step(self, components: list[str], direction, distance: float) -> dict:
        for n in components:
            self.component(n)
        d = np.array(direction, float)
        if np.linalg.norm(d) < 1e-12:
            raise DocumentError("explode direction is zero")
        self._checkpoint()
        step = {"name": f"Explode Step{len(self.explode['steps']) + 1}", "components": components,
                "direction": (d / np.linalg.norm(d)).tolist(), "distance": float(distance)}
        self.explode["steps"].append(step)
        self.explode["active"] = True
        self.revision += 1
        return step

    def set_exploded(self, on: bool, clear: bool = False) -> None:
        self._checkpoint()
        if clear:
            self.explode = {"steps": [], "active": False}
        else:
            self.explode["active"] = bool(on)
        self.revision += 1

    def _explode_offset(self, name: str) -> np.ndarray:
        off = np.zeros(3)
        if self.explode.get("active"):
            for st in self.explode["steps"]:
                if name in st["components"]:
                    off += np.array(st["direction"]) * st["distance"]
        return off

    def motion(self, mate: str, start: float, end: float, frames: int = 24) -> list[dict]:
        """Drive a distance/angle mate from start to end (a linear motor); returns each component pose per frame."""
        m = next((m for m in self.mates if m["name"] == mate), None)
        if m is None or m["type"] not in ("distance", "angle"):
            raise DocumentError("motion needs a distance or angle mate to drive")
        if frames < 2:
            raise DocumentError("frames must be at least 2")
        saved = (m["value"], [c.x.copy() for c in self.components], self.status, self.revision)
        out = []
        try:
            for k in range(frames):
                m["value"] = start + (end - start) * k / (frames - 1)
                st = self.solve()
                out.append({"value": m["value"], "ok": not st["failing"],
                            "poses": {c.name: c.x.tolist() for c in self.components}})
        finally:
            m["value"] = saved[0]
            for c, x in zip(self.components, saved[1]):
                c.x = x
            self.status, self.revision = saved[2], saved[3]
        return out

    def add_mate(self, mtype: str, a: dict, b: dict, value: float | None = None, aligned: bool = False,
                 a2: dict | None = None, b2: dict | None = None, value2: float | None = None) -> dict:
        if mtype not in MATES:
            raise DocumentError(f"unknown mate {mtype!r}; expected one of {sorted(MATES)}")
        if mtype in ("distance", "angle") and value is None:
            raise DocumentError(f"{mtype} mate needs a value")
        if mtype == "width" and (a2 is None or b2 is None):
            raise DocumentError("a width mate needs two faces on each component")
        if mtype in ("symmetric", "slot") and a2 is None:
            raise DocumentError("symmetric needs a symmetry plane (entity3); slot needs the second slot face (entity3)")
        if mtype == "hinge" and (a2 is None or b2 is None):
            raise DocumentError("a hinge mate needs the two cylindrical faces and two planar faces (entity3, entity4)")
        if mtype in ("rack_pinion", "screw") and not value:
            raise DocumentError("rack_pinion needs the pinion pitch diameter; screw needs the pitch (mm per turn)")
        if mtype in ("limit_distance", "limit_angle"):
            if value is None or value2 is None or value2 < value:
                raise DocumentError("limit mates need value (minimum) <= value2 (maximum)")
        self._checkpoint()
        self.mate_counter += 1
        label = {"coincident": "Coincident", "parallel": "Parallel", "perpendicular": "Perpendicular",
                 "distance": "Distance", "angle": "Angle", "concentric": "Concentric", "tangent": "Tangent",
                 "lock": "Lock", "width": "Width", "gear": "Gear", "symmetric": "Symmetric", "slot": "Slot",
                 "limit_distance": "LimitDistance", "limit_angle": "LimitAngle", "cam": "CamMateTangent", "path": "PathMate",
                 "rack_pinion": "RackPinion", "screw": "Screw", "hinge": "Hinge", "universal_joint": "UniversalJoint",
                 "linear_coupler": "LinearCoupler", "connection": "Magnetic"}[mtype]
        mate = {"name": f"{label}{self.mate_counter}", "type": mtype, "a": a, "b": b, "value": value, "aligned": aligned}
        if a2 is not None:
            mate.update(a2=a2, b2=b2)
        if value2 is not None:
            mate["value2"] = value2
        if mtype == "gear":  # remember both components' starting orientations (rotation is measured from here)
            if value is None or value == 0:
                raise DocumentError("a gear mate needs a non-zero ratio (teeth B / teeth A)")
            mate["init"] = [self.component(a["component"]).x[:3].tolist(), self.component(b["component"]).x[:3].tolist()]
        if mtype in MOTION_MATES:  # rotations and travel are measured from these poses
            mate["init6"] = [self.component(a["component"]).x.tolist(), self.component(b["component"]).x.tolist()]
        if mtype == "lock":  # remember the current relative pose
            ca, cb = self.component(a["component"]), self.component(b["component"])
            Ra, ta = ca.matrix()
            Rb, tb = cb.matrix()
            mate["rel"] = [*Rotation.from_matrix(Ra.T @ Rb).as_rotvec(), *(Ra.T @ (tb - ta))]
        self.mates.append(mate)
        st = self.solve()
        if mate["name"] in st["failing"]:
            self.mates.pop()
            self._undo.pop()
            self.mate_counter -= 1
            self.solve()
            raise DocumentError(f"{mate['name']} cannot be satisfied with the existing mates (over-defined)")
        return mate

    def edit_mate(self, name: str, value: float | None = None, aligned: bool | None = None) -> None:
        m = next((m for m in self.mates if m["name"] == name), None)
        if m is None:
            raise DocumentError(f"no mate {name!r}")
        self._checkpoint()
        if value is not None:
            if self.active_config != "Default":  # this configuration's own value; the others keep theirs
                m.setdefault("_base", m.get("value"))
                self.configurations[self.active_config].setdefault("mate_values", {})[name] = value
            m["value"] = value
        if aligned is not None:
            m["aligned"] = aligned
        self.solve()

    def delete_mate(self, name: str) -> None:
        self._checkpoint()
        self.mates = [m for m in self.mates if m["name"] != name]
        self.solve()

    def _local_entity(self, ref: dict):
        """Resolve a mate reference to local geometry: ('plane', p, n) or ('axis', p, d). Results are cached
        per component body and reference (resolving a face matches it against every face of the part)."""
        comp = self.component(ref["component"])
        body = self._local_body(comp) if ("face" in ref or "edge" in ref or "line" in ref) else None
        key = (comp.name, id(body), repr(sorted(ref.items())))
        cache = self.__dict__.setdefault("_ent_cache", {})
        hit = cache.get(key)
        if hit is not None and hit[0] is body:
            return (comp,) + hit[1]
        out = self._resolve_entity(comp, ref)
        if len(cache) > 512:
            cache.clear()
        cache[key] = (body, out[1:])
        return out

    def _resolve_entity(self, comp, ref: dict):
        if "edge" in ref:  # path mates: the local edge itself
            from openswx.core.geom import resolve_edge
            return comp, "edge", np.zeros(3), np.zeros(3), resolve_edge(self._local_body(comp), ref["edge"])
        if "line" in ref:  # a straight edge: a point on it + its direction
            from openswx.core.geom import resolve_edge
            e = resolve_edge(self._local_body(comp), ref["line"])
            p, d = e.position_at(0.5), e.tangent_at(0.5)
            return comp, "line", np.array([p.X, p.Y, p.Z]), np.array([d.X, d.Y, d.Z]), 0.0
        if "vertex" in ref:  # a vertex (part coordinates)
            return comp, "vertex", np.array(ref["vertex"], float), np.zeros(3), 0.0
        if "point" in ref:  # a connection point (magnetic mate): point + direction in the part
            return comp, "cpoint", np.array(ref["point"]["point"], float), np.array(ref["point"]["normal"], float), 0.0
        if "plane" in ref:  # a component reference plane (Front / Top / Right) given as point + normal
            return comp, "plane", np.array(ref["plane"]["point"], float), np.array(ref["plane"]["normal"], float), 0.0
        if "face" not in ref:  # whole component (lock mate)
            return comp, "component", np.zeros(3), np.zeros(3), 0.0
        body = self._local_body(comp)
        face = resolve_face(body, ref["face"])
        kind = face.geom_type.name.lower()
        if ref.get("as_shape"):  # cam mates: any face, queried by closest point
            return comp, "shape", np.zeros(3), np.zeros(3), face
        if kind == "plane":
            p = face.center()
            n = face_normal(face, p)
            return comp, "plane", np.array([p.X, p.Y, p.Z]), np.array([n.X, n.Y, n.Z]), 0.0
        for e in face.edges():
            if e.geom_type.name.lower() == "circle":
                c, d = e.arc_center, e.normal()
                return comp, "axis", np.array([c.X, c.Y, c.Z]), np.array([d.X, d.Y, d.Z]), float(e.radius)
        raise DocumentError("mate faces must be planar or cylindrical")

    def solve(self, hold: dict | None = None, hold_pose: dict | None = None) -> dict:
        """Solve the mates. hold: {component: rotvec} orientations kept while the rest solves (Rotate
        Component drives mechanical mates this way: the turned part may still slide); hold_pose:
        {component: x} whole poses kept (a dragged part leading)."""
        hold = hold or {}
        hold_pose = hold_pose or {}
        free = [c for c in self.components if not c.fixed and not c.derived]
        idx = {c.name: i for i, c in enumerate(free)}
        x0 = np.concatenate([c.x for c in free]) if free else np.zeros(0)
        ents = []
        for m in self.mates:
            try:
                extra = (self._local_entity(m["a2"]), self._local_entity(m["b2"]) if m.get("b2") else None) if m.get("a2") else None
                ents.append((m, self._local_entity(m["a"]), self._local_entity(m["b"]), extra))
            except Exception as exc:  # missing face after a part edit
                ents.append((m, None, str(exc), None))

        def comp_x(comp, X):
            return X[6 * idx[comp.name]: 6 * idx[comp.name] + 6] if comp.name in idx else comp.x

        def residuals(X):
            out, owners = [], []
            for m, ea, eb, extra in ents:
                if ea is None:
                    continue
                r = _mate_residual(m, ea, eb, comp_x(ea[0], X), comp_x(eb[0], X), extra,
                                   (comp_x(extra[0][0], X), comp_x(extra[1][0], X) if extra[1] else None) if extra else None)
                out += r
                owners += [m["name"]] * len(r)
            for name, rv in hold.items():
                if name in idx:
                    out += list((comp_x(self.component(name), X)[:3] - np.asarray(rv, float)) * 100.0)
                    owners += ["(held)"] * 3
            for name, xp in hold_pose.items():
                if name in idx:
                    out += list((comp_x(self.component(name), X) - np.asarray(xp, float)) * 100.0)
                    owners += ["(held)"] * 6
            return np.array(out, float), owners

        res0, owners = residuals(x0)

        def run(start):
            s = least_squares(lambda X: residuals(X)[0], start, method="trf", xtol=1e-12, ftol=1e-12, gtol=1e-12,
                              max_nfev=200 * (len(start) + 1))
            return s.x, float(np.abs(residuals(s.x)[0]).max(initial=0.0))

        if len(res0) and len(x0):
            X, worst = run(x0)
            if worst > 1e-6:
                # flipped mates start at a zero-gradient point: retry with each free component turned 180 degrees
                for c in free:
                    for axis in np.eye(3):
                        seed = X.copy()
                        k = 6 * idx[c.name]
                        r = Rotation.from_rotvec(axis * math.pi) * Rotation.from_rotvec(seed[k:k + 3])
                        seed[k:k + 3] = r.as_rotvec()
                        X2, w2 = run(seed)
                        if w2 < worst:
                            X, worst = X2, w2
                        if worst <= 1e-6:
                            break
                    if worst <= 1e-6:
                        break
        else:
            X = x0
        for m in self.mates:  # trial evaluations must not move the unwrapped angles
            m.pop("_pending", None)
        res, owners = residuals(X)
        for m in self.mates:  # commit them from the solution
            if "_pending" in m:
                m["unwrap"] = m.pop("_pending")
        for c in free:
            c.x = comp_x(c, X).copy()
        self._update_derived()
        # degrees of freedom per component (6 minus constrained directions)
        dof = {}
        if len(X):
            J = np.zeros((len(res), len(X)))
            for j in range(len(X)):
                Xp = X.copy(); Xp[j] += 1e-7
                J[:, j] = (residuals(Xp)[0] - res) / 1e-7
            if len(res):
                s = np.linalg.svd(J, compute_uv=False)
                rank = int((s > 1e-6 * max(1.0, s[0])).sum())
                _, _, vt = np.linalg.svd(J)
                null = vt[rank:]
            else:
                null = np.eye(len(X))
            for c in free:
                cols = slice(6 * idx[c.name], 6 * idx[c.name] + 6)
                dof[c.name] = int(np.linalg.matrix_rank(null[:, cols], tol=1e-6)) if null.size else 0
        failing = sorted({owners[i] for i, r in enumerate(res) if abs(r) > 1e-6 and owners[i] != "(held)"})
        failing += [m["name"] for m, ea, eb, _x in ents if ea is None]
        self.status = {"failing": failing, "dof": dof,
                       "state": "over_defined" if failing else ("fully_defined" if not any(dof.values()) else "under_defined")}
        self.revision += 1
        self.dirty = True
        return self.status

    def world_shapes(self):
        out = []
        for c in self.components:
            body = self._local_body(c)
            if body is None:
                continue
            shape = body.moved(c.location())
            off = self._explode_offset(c.name)
            if off.any():
                shape = shape.moved(Location(Vector(*off)))
            out.append((c, shape))
        return out

    @property
    def body(self):
        shapes = [s for c, s in self.world_shapes() if not c.envelope]  # (envelopes are reference only)
        return Compound([sol for s in shapes for sol in s.solids()]) if shapes else None

    def summary(self) -> dict:
        # mass properties integrate every component: reuse them while nothing has moved or changed
        key = (self.revision, tuple(tuple(np.round(c.x, 9)) for c in self.components),
               tuple(id(c.part) for c in self.components))
        cached = getattr(self, "_mp_cache", None)
        if cached and cached[0] == key:
            mp = cached[1]
        else:  # volume and body count do not depend on where components sit: sum the (cached) part values
            # a fast (fixed-order) volume for the summary; get_mass_properties integrates to full accuracy
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            vol, nb = 0.0, 0
            for c in self.components:
                body = self._local_body(c)
                if body is not None:
                    for sol in body.solids():
                        g = GProp_GProps()
                        BRepGProp.VolumeProperties_s(sol.wrapped, g)
                        vol, nb = vol + g.Mass(), nb + 1
            mp = {"volume": vol, "bodies": nb}
            self._mp_cache = (key, mp)
        return {"kind": "assembly", "name": self.name, "path": self.path, "revision": self.revision,
                "dirty": self.dirty, "can_undo": bool(self._undo), "can_redo": bool(self._redo),
                "assembly_features": self.features, "explode": self.explode, "drawing": self.drawing,
                "components": [{"name": c.name, "fixed": c.fixed, "link": c.link, "dof": self.status.get("dof", {}).get(c.name, 0),
                                "derived": c.derived, "subassembly": c.is_subassembly, "pose": c.x.tolist(),
                                "position": [round(v, 6) for v in c.x[3:]],
                                "rotation_deg": [round(v, 6) for v in Rotation.from_rotvec(c.x[:3]).as_euler("xyz", degrees=True)],
                                "envelope": c.envelope, "speedpak": c.speedpak}
                               for c in self.components],
                "alternate_positions": {k: None for k in getattr(self, "alternate_positions", {})},
                "large_assembly_mode": getattr(self, "large_assembly_mode", False),
                "mates": [{**m, "error": m["name"] in self.status.get("failing", [])} for m in self.mates],
                "state": self.status.get("state"), "volume_mm3": round(mp["volume"], 6), "bodies": mp["bodies"],
                "errors": {m: "mate not satisfied" for m in self.status.get("failing", [])},
                "features": [], "material": {"name": None, "density": 1000.0}, "units": "mm", "rollback": None,
                "configurations": list(self.configurations), "active_config": self.active_config,
                "suppressed_components": [c.name for _, c in self._parked],
                "display_states": list(self.display_states), "display_state": self.display_state,
                "hidden_components": list(self.display_states.get(self.display_state, {}).get("hidden", [])) if self.display_state else [], "named_views": getattr(self, "named_views", {}),
                "render": getattr(self, "render", {})}

    def topology(self) -> dict:
        faces, edges = [], []
        from openswx.core.geom import edge_info, face_info
        for c, shape in self.world_shapes():
            for i, f in enumerate(shape.faces()):
                faces.append({**face_info(i, f), "id": f"{c.name}/Face{i}"})
            for i, e in enumerate(shape.edges()):
                edges.append({**edge_info(i, e), "id": f"{c.name}/Edge{i}"})
        return {"faces": faces, "edges": edges}

    def edge_ref_for(self, sel: str) -> dict:
        """'Part1-1/Edge3' -> component-local edge reference (path mates)."""
        from openswx.core.geom import edge_ref
        cname, eid = sel.split("/", 1)
        comp = self.component(cname)
        body = self._local_body(comp)
        try:
            edge = body.edges()[int(eid.replace("Edge", ""))]
        except (ValueError, IndexError):
            raise DocumentError(f"unknown edge {sel!r}") from None
        return {"component": cname, "edge": edge_ref(edge, body)}

    def face_ref_for(self, sel: str) -> dict:
        """'Part1-1/Face3' (world topology id) -> component-local face reference for a mate."""
        if "/" not in sel:
            raise DocumentError("assembly faces are addressed as Component/Face#, see get_topology")
        cname, fid = sel.split("/", 1)
        comp = self.component(cname)
        std = plane_ref(fid)
        if std is not None:  # 'Part1-1/Front Plane': the component's own reference plane
            return {"component": cname, "plane": std}
        body = self._local_body(comp)
        try:
            face = body.faces()[int(fid.replace("Face", ""))]
        except (ValueError, IndexError):
            raise DocumentError(f"unknown face {sel!r}") from None
        return {"component": cname, "face": face_ref(face, body)}

    def mesh(self) -> dict:
        lam = getattr(self, "large_assembly_mode", False)
        out = {"faces": [], "edges": [], "sketches": [], "planes": [], "revision": self.revision, "components": [],
               "large_assembly_mode": lam}
        for c, shape in self.world_shapes():
            if c.speedpak:  # the simplified (defeatured) body stands in for the part on screen
                shape = self.speedpak_body(c).moved(c.location())
            # Large Assembly Mode: a coarser tessellation and no edges, for speed
            m = tessellate(shape, tol=0.5 if lam else 0.05, ang=0.6 if lam else 0.2)
            for f in m["faces"]:
                f["id"] = f"{c.name}/{f['id']}"
                f["component"] = c.name
                if c.envelope:
                    f["envelope"] = True
            for e in m["edges"]:
                e["id"] = f"{c.name}/{e['id']}"
            out["faces"] += m["faces"]
            if not lam:
                out["edges"] += m["edges"]
        return out

    def speedpak_body(self, c):
        from openswx.commands.more import defeature_shape
        body = self._local_body(c)
        cache = self.__dict__.setdefault("_speedpak", {})
        if cache.get(c.name, (None,))[0] is not body:
            cache[c.name] = (body, defeature_shape(body, max_hole=None, max_fillet=None))
        return cache[c.name][1]


def _mate_residual(m, ea, eb, xa, xb, extra=None, xextra=None) -> list[float]:
    ca, ka, pa, na, ra = ea
    cb, kb, pb, nb, rb = eb
    t, aligned = m["type"], m.get("aligned", False)
    if t == "lock":
        Ra, ta = ca.matrix(xa)
        Rb, tb = cb.matrix(xb)
        rel_r = Rotation.from_matrix(Ra.T @ Rb).as_rotvec()
        rel_t = Ra.T @ (tb - ta)
        want = np.array(m["rel"], float)
        return list((rel_r - want[:3]) * 100.0) + list(rel_t - want[3:])
    Pa, Na = ca.to_world(pa, xa), ca.dir_to_world(na, xa)
    Pb, Nb = cb.to_world(pb, xb), cb.dir_to_world(nb, xb)
    if t == "connection":  # magnetic mate: the connection points meet, their directions face each other
        return list(Pb - Pa) + list(np.cross(Na, Nb) * 10.0) + [float(np.dot(Na, Nb) + 1.0) * 10.0]
    if t == "gear":  # rotation of B about its axis = -(1/ratio) x rotation of A about its axis
        def spin(x, rv0, axis):
            rel = Rotation.from_rotvec(x[:3]) * Rotation.from_rotvec(rv0).inv()
            return float(np.dot(rel.as_rotvec(), axis))
        tha = spin(xa, np.array(m["init"][0]), Na)
        thb = spin(xb, np.array(m["init"][1]), Nb)
        return [(thb + tha / float(m["value"])) * 100.0]
    if t in MOTION_MATES or t == "hinge":
        return _mechanical_residual(m, ea, eb, xa, xb, extra, xextra)
    if t == "width":  # centre B's two faces between A's two faces
        (ca2, _, pa2, na2, _), (cb2, _, pb2, nb2, _) = extra
        Pa2 = ca2.to_world(pa2, xextra[0])
        Pb2 = cb2.to_world(pb2, xextra[1])
        mid_a, mid_b = (Pa + Pa2) / 2, (Pb + Pb2) / 2
        return list(np.cross(Na, Nb)) + [float(np.dot(mid_b - mid_a, Na))]
    if t in ("cam", "path"):
        from build123d import Vector
        from openswx.core.geom import closest_point, face_normal
        if kb != "axis":
            raise DocumentError(f"{t} mates need the follower's cylindrical face as entity2")
        Ra, ta = ca.matrix(xa)
        q = Ra.T @ (Pb - ta)  # follower axis point in A's local frame
        cp, d = closest_point(ra, Vector(*q))
        if t == "path":  # follower point rides on the path edge
            return list(q - np.array([cp.X, cp.Y, cp.Z]))
        n = face_normal(ra, cp)  # cam: roller tangent to the cam face, axis parallel to it
        nloc = np.array([n.X, n.Y, n.Z])
        side = 1.0 if np.dot(q - np.array([cp.X, cp.Y, cp.Z]), nloc) >= 0 else -1.0
        return [side * d - rb, float(np.dot(Ra.T @ Nb, nloc))]
    if t == "symmetric":  # B is the mirror image of A in the symmetry plane (extra[0])
        cs, ks, ps, ns, _ = extra[0]
        Ps, Ns = cs.to_world(ps, xextra[0]), cs.dir_to_world(ns, xextra[0])
        Ns = Ns / np.linalg.norm(Ns)
        if ka == "plane" and kb == "plane":
            refl_n = Na - 2 * np.dot(Na, Ns) * Ns
            refl_p = Pa - 2 * np.dot(Pa - Ps, Ns) * Ns
            return list(Nb - refl_n) + [float(np.dot(refl_p - Pb, Nb))]
        if ka == "axis" and kb == "axis":
            refl_d = Na - 2 * np.dot(Na, Ns) * Ns
            refl_p = Pa - 2 * np.dot(Pa - Ps, Ns) * Ns
            w = Pb - refl_p
            return list(np.cross(refl_d, Nb)) + list(w - refl_d * np.dot(w, refl_d))
        raise DocumentError("symmetric mates need two planar or two cylindrical faces")
    if t == "slot":  # pin (cylinder B) centred between the slot's parallel side faces A and extra[0], free along it
        if kb != "axis" or ka != "plane":
            raise DocumentError("slot mates need a slot side face (entity1), the pin's cylindrical face (entity2) and "
                                "the other slot side face (entity3)")
        c2, _, p2, _, _ = extra[0]
        P2 = c2.to_world(p2, xextra[0])
        mid = (Pa + P2) / 2
        return [float(np.dot(Nb, Na)), float(np.dot(Pb - mid, Na))]
    if t == "limit_distance":  # gap between planar faces kept within [value, value2]
        gap = float(np.dot(Pb - Pa, Na))
        lo, hi = float(m["value"]), float(m["value2"])
        return list(np.cross(Na, Nb)) + [min(0.0, gap - lo) + max(0.0, gap - hi)]
    if t == "limit_angle":
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(Na, Nb))))))
        lo, hi = float(m["value"]), float(m["value2"])
        return [(min(0.0, ang - lo) + max(0.0, ang - hi)) * math.pi / 180]
    if ka in ("line", "vertex") or kb in ("line", "vertex"):
        return _edge_point_residual(t, m, (ka, Pa, Na, ra), (kb, Pb, Nb, rb))
    if t == "tangent":  # planar face tangent to a cylindrical face
        if {ka, kb} != {"plane", "axis"}:
            raise DocumentError("tangent mates need one planar and one cylindrical face")
        (Pp, Np), (Pc, Dc, r) = ((Pa, Na), (Pb, Nb, rb)) if ka == "plane" else ((Pb, Nb), (Pa, Na, ra))
        return [float(np.dot(Dc, Np)), float(np.dot(Pc - Pp, Np)) - r]
    if t == "concentric":
        if ka != "axis" or kb != "axis":
            raise DocumentError("concentric mates need cylindrical faces")
        r = list(np.cross(Na, Nb))
        w = Pb - Pa
        r += list(w - Na * np.dot(w, Na))
        return r
    if ka == "axis" or kb == "axis":
        raise DocumentError(f"{t} mates need planar faces")
    if t == "parallel":
        return list(np.cross(Na, Nb))
    if t == "perpendicular":
        return [float(np.dot(Na, Nb))]
    if t == "angle":
        return [float(np.dot(Na, Nb)) - math.cos(math.radians(m["value"]))]
    target = Na if aligned else -Na  # coincident/distance: faces face each other unless aligned
    r = list(Nb - target)
    gap = float(np.dot(Pb - Pa, Na))
    want = 0.0 if t == "coincident" else float(m["value"]) * (-1.0 if aligned and False else 1.0)
    return r + [gap - want]


def _edge_point_residual(t: str, m: dict, a, b) -> list[float]:
    """Mates on straight edges ("line": point + direction) and vertices with planes, cylinder axes ("axis"),
    edges and vertices. coincident: the entities meet (a vertex on the plane / line, a line in the plane /
    along the line); distance: the gap is the value (signed along a plane's normal); parallel / perpendicular:
    of the directions."""
    order = {"plane": 0, "axis": 1, "line": 1, "vertex": 2}
    if a[0] not in order or b[0] not in order:
        raise DocumentError(f"{t} mates are not defined between a {a[0]} and a {b[0]}")
    (ka, Pa, Na, _), (kb, Pb, Nb, _) = sorted([a, b], key=lambda e: order[e[0]])
    ka = "line" if ka == "axis" else ka
    kb = "line" if kb == "axis" else kb
    want = float(m.get("value") or 0.0) if t == "distance" else 0.0
    w = Pb - Pa

    def off_line(v, d):  # the part of v across the line direction d
        d = d / np.linalg.norm(d)
        return v - d * np.dot(v, d)
    if t in ("coincident", "distance"):
        if ka == "plane" and kb == "vertex":
            return [float(np.dot(w, Na)) - want]
        if ka == "plane" and kb == "line":
            return [float(np.dot(Nb, Na)), float(np.dot(w, Na)) - want]
        if ka == "line" and kb == "line":
            o = off_line(w, Na)
            return list(np.cross(Na, Nb)) + (list(o) if t == "coincident" else [float(np.linalg.norm(o)) - want])
        if ka == "line" and kb == "vertex":
            o = off_line(w, Na)
            return list(o) if t == "coincident" else [float(np.linalg.norm(o)) - want]
        if ka == "vertex" and kb == "vertex":
            return list(w) if t == "coincident" else [float(np.linalg.norm(w)) - want]
    if t == "parallel" and "vertex" not in (ka, kb):
        return [float(np.dot(Na, Nb))] if ka == "plane" and kb == "line" else list(np.cross(Na, Nb))
    if t == "perpendicular" and "vertex" not in (ka, kb):
        return list(np.cross(Na, Nb)) if ka == "plane" and kb == "line" else [float(np.dot(Na, Nb))]
    raise DocumentError(f"{t} mates are not defined between a {ka} and a {kb}")


def _spin(x, x0, axis, m=None, key="") -> float:
    """A component's rotation (radians) about `axis` since pose x0; with a mate, unwrapped past half turns
    (the nearest to the mate's last value), so turning on keeps counting."""
    rel = Rotation.from_rotvec(np.asarray(x[:3], float)) * Rotation.from_rotvec(np.asarray(x0[:3], float)).inv()
    raw = float(np.dot(rel.as_rotvec(), axis))
    if m is None:
        return raw
    last = m.get("unwrap", {}).get(key, 0.0)  # committed by solve() from its final solution only
    th = raw + 2 * math.pi * round((last - raw) / (2 * math.pi))
    m.setdefault("_pending", {})[key] = th
    return th


def _mechanical_residual(m, ea, eb, xa, xb, extra, xextra) -> list[float]:
    """Mechanical mates. rack_pinion: A's linear edge (the rack) travels, relative to B, by B's turn x the
    pitch radius (value / 2). screw: concentric, and B advances along the axis by its turn relative to A x
    pitch / 2 pi. hinge: concentric + coincident. universal_joint: B's shaft turns with A's through a Cardan
    joint, tan(thB) cos(beta) = tan(thA), beta the angle between the shafts."""
    ca, ka, pa, na, ra = ea
    cb, kb, pb, nb, rb = eb
    t = m["type"]
    Pa, Na = ca.to_world(pa, xa), ca.dir_to_world(na, xa)
    Pb, Nb = cb.to_world(pb, xb), cb.dir_to_world(nb, xb)
    if t == "hinge":
        if ka != "axis" or kb != "axis":
            raise DocumentError("hinge mates need two cylindrical faces (entity1, entity2) and two planar faces")
        w = Pb - Pa
        conc = list(np.cross(Na, Nb)) + list(w - Na * np.dot(w, Na))
        (c3, _, p3, n3, _), (c4, _, p4, n4, _) = extra
        P3, N3 = c3.to_world(p3, xextra[0]), c3.dir_to_world(n3, xextra[0])
        P4, N4 = c4.to_world(p4, xextra[1]), c4.dir_to_world(n4, xextra[1])
        return conc + [float(np.dot(P4 - P3, N3))]  # the faces touch (they are square to the hinge axis)
    xa0, xb0 = (np.array(v, float) for v in m["init6"])
    if t == "linear_coupler":  # B travels along its direction `ratio` times A's travel along its own
        def direction(c, k, n, r, x):
            if k == "edge":
                tl = r.tangent_at(0)
                n = np.array([tl.X, tl.Y, tl.Z])
            elif k != "plane":
                raise DocumentError("linear coupler mates need planar faces or linear edges for the directions")
            w = c.dir_to_world(n, x)
            return w / np.linalg.norm(w)
        Da, Db = direction(ca, ka, na, ra, xa), direction(cb, kb, nb, rb, xb)
        travel_a = float(np.dot(np.asarray(xa[3:]) - xa0[3:], Da))
        travel_b = float(np.dot(np.asarray(xb[3:]) - xb0[3:], Db))
        return [travel_b - float(m["value"] if m.get("value") is not None else 1.0) * travel_a]
    if t == "rack_pinion":
        if ka != "edge" or kb != "axis":
            raise DocumentError("rack-and-pinion mates need the rack's linear edge (entity1, 'Comp/EdgeN') and the "
                                "pinion's cylindrical face (entity2)")
        d_loc = ra.tangent_at(0)
        D = ca.dir_to_world(np.array([d_loc.X, d_loc.Y, d_loc.Z]), xa)
        travel = float(np.dot((np.asarray(xa[3:]) - np.asarray(xb[3:])) - (xa0[3:] - xb0[3:]), D))
        turn = _spin(xb, xb0, Nb / np.linalg.norm(Nb), m, "b") - _spin(xa, xa0, Nb / np.linalg.norm(Nb), m, "a")
        return [travel - turn * float(m["value"]) / 2.0]
    if ka != "axis" or kb != "axis":
        raise DocumentError(f"{t.replace('_', ' ')} mates need two cylindrical faces")
    axis = Na / np.linalg.norm(Na)
    if t == "screw":
        w = Pb - Pa
        conc = list(np.cross(Na, Nb)) + list(w - Na * np.dot(w, Na))
        advance = float(np.dot((np.asarray(xb[3:]) - np.asarray(xa[3:])) - (xb0[3:] - xa0[3:]), axis))
        turn = _spin(xb, xb0, axis, m, "b") - _spin(xa, xa0, axis, m, "a")
        return conc + [advance - turn / (2 * math.pi) * float(m["value"])]
    # universal joint
    nb_ = Nb / np.linalg.norm(Nb)
    beta = math.acos(max(-1.0, min(1.0, abs(float(np.dot(axis, nb_))))))
    tha, thb = _spin(xa, xa0, axis, m, "a"), _spin(xb, xb0, nb_, m, "b")
    want = math.atan2(math.sin(tha), math.cos(tha) * math.cos(beta))  # tan(thB) cos(beta) = tan(thA)
    want += 2 * math.pi * round((tha - want) / (2 * math.pi))  # the branch that stays with the input shaft
    return [(thb - want) * 100.0]


def _cut_tool(f: dict, asm=None):
    """The solid an assembly cut removes, in world coordinates."""
    from build123d import Face, Plane, Solid, Wire
    reach = 10000.0  # 'through': far past any assembly
    if f["kind"] == "hole":
        d = np.asarray(f["direction"], float)
        if np.linalg.norm(d) < 1e-12 or float(f["diameter"]) <= 0:
            raise DocumentError("a hole needs a direction and a positive diameter")
        d = d / np.linalg.norm(d)
        length = float(f["depth"]) if f.get("depth") else reach
        return Solid.make_cylinder(float(f["diameter"]) / 2, length, Plane(origin=tuple(f["position"]), z_dir=tuple(d)))
    if f["kind"] == "extrude":
        pts = [Vector(*q) for q in f["profile"]]
        if len(pts) < 3:
            raise DocumentError("an assembly cut profile needs at least 3 points")
        face = Face(Wire.make_polygon(pts, close=True))
        n = face.normal_at(face.center()) * (-1.0 if f.get("reverse") else 1.0)
        length = float(f["depth"]) if f.get("depth") else reach
        return Solid.extrude(face, n * length)
    raise DocumentError(f"unknown assembly cut {f['kind']!r}")
