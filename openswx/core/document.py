"""Part document: ordered feature tree with parametric rebuild, rollback, suppression and undo."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import time
from pathlib import Path

from openswx.core.equations import FEATURE_DIMS, EquationError, Evaluator, feature_param_path, get_path, is_expr, parse_target, set_path
from openswx.core import naming
from openswx.core.features import BUILDERS, NAME_PREFIX, Ctx, FeatureError, mass_properties
from openswx.core.geom import edge_info, face_info, tessellate
from openswx.core.sketch import Sketch, SketchError

from openswx.system import snapshots  # noqa: E402

FORMAT = "openswx-part"
VERSION = 1  # (migrate.CURRENT[FORMAT]; see migrate.py before changing it)
_KNOWN_KEYS = {"format", "version", "name", "units", "material", "appearance", "properties", "drawing", "study", "features",
               "rollback", "counters", "next_id", "variables", "configurations", "active_config", "folders",
               "design_table", "named_views", "pmi", "render", "tolerances", "journal", "requirements", "blocks", "display_states", "display_state"}
# container types load_dict reads; a file that gives any of them another type is refused before it is used
_PART_TYPES = {"name": str, "units": str, "material": dict, "appearance": dict, "properties": dict,
               "drawing": dict, "study": dict, "features": list, "counters": dict, "variables": dict,
               "configurations": dict, "folders": list, "named_views": dict, "pmi": list, "render": dict,
               "tolerances": dict, "requirements": list, "blocks": dict, "display_states": dict}
_TYPE_WORDS = {str: "a string", bool: "true or false", list: "a list", dict: "an object"}
_FEATURE_TYPES = {"name": str, "type": str, "params": dict, "suppressed": bool}

DEFAULT_TREE = ["History", "Sensors", "Annotations", "Material", "Front Plane", "Top Plane", "Right Plane", "Origin"]
CONSUMES_SKETCH = {"extrude", "cut_extrude", "revolve", "cut_revolve", "sweep", "cut_sweep", "loft", "cut_loft",
                   "base_flange", "structural_member", "sketched_bend", "revolved_surface", "swept_surface",
                   "lofted_surface", "planar_surface", "filled_surface", "wrap", "jog", "miter_flange"}


def consumed_sketches(f: dict) -> list[str]:
    """Sketches absorbed by a feature (shown nested under it in the FeatureManager tree)."""
    if f["type"] not in CONSUMES_SKETCH:
        return []
    p = f["params"]
    profile = p.get("profile_sketch") if f["type"] == "structural_member" else p.get("profile")  # (there: the kind)
    return [s for s in [p.get("sketch"), p.get("path"), profile, p.get("boundary"), *p.get("profiles", [])] if s]


class DocumentError(ValueError):
    pass



def _ctx_copy(ctx: Ctx) -> Ctx:
    """A copy of a rebuild context that later features can add to without touching the original (shapes are
    immutable and shared; the dictionaries and lists holding them are copied)."""
    out = copy.copy(ctx)
    for fld in dataclasses.fields(ctx):
        v = getattr(ctx, fld.name)
        if isinstance(v, dict):
            setattr(out, fld.name, {k: (list(x) if isinstance(x, list) else dict(x) if isinstance(x, dict) else x)
                                    for k, x in v.items()})
        elif isinstance(v, list):
            setattr(out, fld.name, list(v))
    return out

def _keep_solved(f: dict, solved: dict, ev) -> None:
    """A sketch feature keeps its solved geometry (points, status, radii, driven dimensions); its expressions
    stay as written."""
    orig = f["params"].setdefault("sketch", {})
    orig["points"], orig["status"] = solved.get("points", {}), solved.get("status", {})
    for eid, e in solved.get("entities", {}).items():
        if "r" in e and eid in orig.get("entities", {}):
            orig["entities"][eid]["r"] = e["r"]
    for d_new, d_old in zip(solved.get("dimensions", []), orig.get("dimensions", [])):
        if d_old.get("driven") or not d_old.get("expr"):
            d_old["value"] = d_new["value"] if d_old.get("driven") else d_old["value"]
        ev.values[f"{d_new['id']}@{f['name']}"] = d_new["value"]


class PartDocument:
    def __init__(self, name: str = "Part1"):
        self.name = name
        self.path: str | None = None
        self.units = "mm"
        self.material = {"name": None, "density": 1000.0}
        self.appearance: dict = {"part": None, "features": {}, "faces": []}  # colours "#rrggbb"
        self.properties: dict[str, str] = {}  # custom properties (File > Properties)
        self.drawing: dict = {}  # drawing sheet state: offsets, notes, datums, gdt, model_items, sheet...
        self.study: dict = {"fixtures": [], "loads": [], "element_size": None}  # Simulation (static study)
        self.features: list[dict] = []
        self.rollback: int | None = None  # number of active features; None = all
        self.counters: dict[str, int] = {}
        self.next_id = 1
        self.state: dict[str, dict] = {}
        self.variables: dict[str, str] = {}  # global variables: name -> expression
        self.configurations: dict[str, dict] = {"Default": {"suppressed": {}, "values": {}}}
        self.active_config = "Default"
        self.flattened = False  # sheet metal Flatten
        self.folders: list[dict] = []  # FeatureManager folders: {"name", "features": [names]}
        self.design_table: dict | None = None  # {"columns": [...], "rows": [{"name", "values": [...]}]}
        self.dim_values: dict[str, float] = {}
        self.ctx = Ctx()
        self.revision = 0
        self.dirty = False
        self._undo: list = []  # (snapshots.Snapshot: long strings shared between steps)
        self._redo: list = []
        self.journal: list | None = []  # (a new document: everything done to it gets recorded)
        self.rebuild()

    def to_dict(self) -> dict:
        return {
            "format": FORMAT, "version": VERSION, "name": self.name, "units": self.units,
            "material": self.material, "appearance": self.appearance, "properties": self.properties, "drawing": self.drawing,
            "study": self.study, "features": self.features, "rollback": self.rollback,
            "counters": self.counters, "next_id": self.next_id,
            "variables": self.variables, "configurations": self.configurations, "active_config": self.active_config,
            "folders": self.folders, "design_table": self.design_table, "named_views": getattr(self, "named_views", {}),
            "pmi": getattr(self, "pmi", []), "render": getattr(self, "render", {}),
            "tolerances": getattr(self, "tolerances", {}),  # {"D1@Sketch1": {"type": ...}} (tolerance.py)
            "requirements": getattr(self, "requirements", []),
            "blocks": getattr(self, "blocks", {}),  # sketch blocks (commands_blocks.py)
            "display_states": getattr(self, "display_states", {}), "display_state": getattr(self, "display_state", None),
            # the commands that built the document, when all of them are known (export_script replays them)
            **({"journal": self.journal} if getattr(self, "journal", None) is not None else {}),
            **getattr(self, "_extra", {}),  # (keys of the file this version does not use: written back)
        }

    @staticmethod
    def _validate(d: dict) -> None:
        """Refuse the container types load_dict relies on, so a damaged file fails here and not halfway through."""
        for key, typ in _PART_TYPES.items():
            if key in d and not isinstance(d[key], typ):
                raise DocumentError(f"{key} must be {_TYPE_WORDS[typ]}, not {type(d[key]).__name__}")
        features = d.get("features") or []
        rollback = d.get("rollback")
        if rollback is not None:
            if isinstance(rollback, bool) or not isinstance(rollback, int):
                raise DocumentError(f"rollback must be an integer or null, not {type(rollback).__name__}")
            if not 0 <= rollback <= len(features):
                raise DocumentError(f"rollback {rollback} is outside the feature tree (0 to {len(features)})")
        if "next_id" in d and (isinstance(d["next_id"], bool) or not isinstance(d["next_id"], int)):
            raise DocumentError(f"next_id must be an integer, not {type(d['next_id']).__name__}")
        for key, typ in (("active_config", str), ("design_table", dict), ("display_state", str), ("journal", list)):
            if key in d and d[key] is not None and not isinstance(d[key], typ):
                raise DocumentError(f"{key} must be {_TYPE_WORDS[typ]} or null, not {type(d[key]).__name__}")
        for i, f in enumerate(features):
            if not isinstance(f, dict):
                raise DocumentError(f"feature {i} must be an object, not {type(f).__name__}")
            for key, typ in _FEATURE_TYPES.items():
                if not isinstance(f.get(key), typ):
                    raise DocumentError(f"feature {i} ({f.get('name') or 'unnamed'}): "
                                        f"{key} must be {_TYPE_WORDS[typ]}, not {type(f.get(key)).__name__}")
            if f.get("id") is not None and not isinstance(f["id"], str):
                raise DocumentError(f"feature {i} ({f['name']}): id must be a string, not {type(f['id']).__name__}")
        for name, cfg in (d.get("configurations") or {}).items():
            if not isinstance(cfg, dict):
                raise DocumentError(f"configuration {name!r} must be an object, not {type(cfg).__name__}")
            for key in ("suppressed", "values"):
                if key in cfg and not isinstance(cfg[key], dict):
                    raise DocumentError(f"configuration {name!r}: {key} must be an object, "
                                        f"not {type(cfg[key]).__name__}")
        for name, n in (d.get("counters") or {}).items():
            if not isinstance(n, int):
                raise DocumentError(f"counter {name!r} must be an integer, not {type(n).__name__}")

    def load_dict(self, d: dict) -> None:
        from openswx.exchange import migrate
        try:
            d = migrate.migrate(copy.deepcopy(d), FORMAT)  # (a copy: never share feature lists between documents)
        except migrate.FormatError as exc:
            raise DocumentError(str(exc)) from exc
        self._validate(d)
        self._extra = migrate.extras(d, _KNOWN_KEYS)
        self.name = d.get("name", self.name)
        self.units = d.get("units", "mm")
        self.material = d.get("material", self.material)
        self.appearance = d.get("appearance", {"part": None, "features": {}, "faces": []})
        self.properties = d.get("properties", {})
        self.drawing = d.get("drawing", {})
        self.study = d.get("study", {"fixtures": [], "loads": [], "element_size": None})
        self.features = d.get("features", [])
        self.rollback = d.get("rollback")
        self.counters = d.get("counters", {})
        self.next_id = d.get("next_id", len(self.features) + 1)
        self.variables = d.get("variables", {})
        self.configurations = d.get("configurations", {"Default": {"suppressed": {}, "values": {}}})
        self.active_config = d.get("active_config", "Default")
        self.folders = d.get("folders", [])
        self.design_table = d.get("design_table")
        self.named_views = d.get("named_views", {})
        self.pmi = d.get("pmi", [])
        self.render = d.get("render", {})
        self.tolerances = d.get("tolerances", {})
        self.requirements = d.get("requirements", [])
        self.blocks = d.get("blocks", {})
        self.display_states = d.get("display_states", {})
        self.display_state = d.get("display_state") if d.get("display_state") in self.display_states else None
        self.journal = d.get("journal")  # (None: the file does not say how it was built)
        self.rebuild()

    def save(self, path: str | None = None) -> str:
        path = path or self.path
        if not path:
            raise DocumentError("no file path given")
        p = Path(path)
        if p.suffix.lower() != ".oswprt":
            p = p.with_suffix(".oswprt")
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        self.path, self.name, self.dirty = str(p), p.stem, False
        return str(p)

    def save_copy(self, path: str) -> str:
        """Write the part to `path` (.oswprt) as a copy: the open document keeps its name, file and state."""
        p = Path(path)
        if p.suffix.lower() != ".oswprt":
            p = p.with_suffix(".oswprt")
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return str(p)

    @classmethod
    def open(cls, path: str) -> "PartDocument":
        doc = cls(Path(path).stem)
        doc.load_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        doc.path = str(path)
        doc.dirty = False
        return doc

    def _checkpoint(self) -> None:
        self._undo.append(snapshots.pack(self.to_dict()))
        self._undo = self._undo[-100:]
        self._redo.clear()

    def undo(self) -> None:
        if not self._undo:
            raise DocumentError("nothing to undo")
        self._redo.append(snapshots.pack(self.to_dict()))
        self.load_dict(snapshots.unpack(self._undo.pop()))
        self.dirty = True

    def redo(self) -> None:
        if not self._redo:
            raise DocumentError("nothing to redo")
        self._undo.append(snapshots.pack(self.to_dict()))
        self.load_dict(snapshots.unpack(self._redo.pop()))
        self.dirty = True

    def find(self, name: str) -> dict:
        for f in self.features:
            if f["name"] == name or f["id"] == name:
                return f
        raise DocumentError(f"no feature named {name!r}")

    def index(self, name: str) -> int:
        return self.features.index(self.find(name))

    # Who hands out feature ids and names when it is not this document: a cloud document's (live collaboration)
    # come from the server, through an object with take_id(doc) and take_name(doc, prefix).
    allocator = None

    def _new_id(self) -> str:
        if self.allocator is not None:
            return self.allocator.take_id(self)
        fid = f"f{self.next_id}"
        self.next_id += 1
        return fid

    def _new_name(self, ftype: str) -> str:
        prefix = NAME_PREFIX[ftype]
        if self.allocator is not None:
            return self.allocator.take_name(self, prefix)
        n = self.counters.get(prefix, 0) + 1
        names = {f["name"] for f in self.features}
        while f"{prefix}{n}" in names:
            n += 1
        self.counters[prefix] = n
        return f"{prefix}{n}"

    def add_feature(self, ftype: str, params: dict, name: str | None = None, rebuild: bool = True) -> dict:
        if ftype not in BUILDERS:
            raise DocumentError(f"unknown feature type {ftype!r}")
        self._checkpoint()
        feat = {"id": self._new_id(), "name": name or self._new_name(ftype), "type": ftype,
                "params": copy.deepcopy(params), "suppressed": False}
        at = len(self.features) if self.rollback is None else self.rollback
        self.features.insert(at, feat)
        if self.rollback is not None:
            self.rollback += 1
        if rebuild:
            self.rebuild()
            err = self.state.get(feat["name"], {}).get("error")
            if err:
                # keep SOLIDWORKS semantics simple for agents: refuse a feature that fails on creation
                self.features.remove(feat)
                if self.rollback is not None:
                    self.rollback -= 1
                self._undo.pop()
                self.rebuild()
                raise DocumentError(f"{feat['name']}: {err}")
        return feat

    def edit_feature(self, name: str, params: dict, replace: bool = False) -> dict:
        feat = self.find(name)
        self._checkpoint()
        old = copy.deepcopy(feat["params"])
        feat["params"] = copy.deepcopy(params) if replace else {**feat["params"], **copy.deepcopy(params)}
        self.rebuild()
        err = self.state.get(feat["name"], {}).get("error")
        if err:
            feat["params"] = old
            self._undo.pop()
            self.rebuild()
            raise DocumentError(f"{feat['name']}: {err}")
        return feat

    def edit_sketch(self, name: str, fn) -> dict:
        """Apply fn(Sketch) to a sketch feature and rebuild."""
        feat = self.find(name)
        if feat["type"] != "sketch":
            raise DocumentError(f"{name} is not a sketch")
        self._checkpoint()
        old = copy.deepcopy(feat["params"])
        sk = Sketch(feat["params"].get("sketch"))
        try:
            result = fn(sk)
            sk.solve()
        except (SketchError, KeyError) as exc:
            self._undo.pop()
            raise DocumentError(str(exc)) from exc
        feat["params"]["sketch"] = sk.to_dict()
        self.rebuild()
        err = self.state.get(feat["name"], {}).get("error")
        if err:
            feat["params"] = old
            self._undo.pop()
            self.rebuild()
            raise DocumentError(f"{name}: {err}")
        return {"result": result, "status": sk.status}

    def delete_feature(self, name: str, with_children: bool = True) -> list[str]:
        feat = self.find(name)
        self._checkpoint()
        removed = [feat["name"]]
        self.features.remove(feat)
        if with_children:
            for sk in consumed_sketches(feat):
                if not any(sk in consumed_sketches(f) for f in self.features):
                    try:
                        self.features.remove(self.find(sk)); removed.append(sk)
                    except DocumentError:
                        pass
        if self.rollback is not None:
            self.rollback = min(self.rollback, len(self.features))
        self.rebuild()
        return removed

    def reorder(self, name: str, after: str | None) -> None:
        """Drag a feature to another place in the FeatureManager: it (with the sketches it consumes) goes right
        after `after` (None: to the top). Refused, as in SOLIDWORKS, when it would come before something it
        depends on or something depending on it would come first (the rebuild gets new errors)."""
        feat = self.find(name)
        group = [self.find(sk) for sk in consumed_sketches(feat) if any(f["name"] == sk for f in self.features)] + [feat]
        if after is not None and any(after == g["name"] for g in group):
            return
        before_errors = {k for k, v in self.state.items() if v.get("error")}
        self._checkpoint()
        old = list(self.features)
        rest = [f for f in self.features if f not in group]
        at = 0 if after is None else rest.index(self.find(after)) + 1
        self.features = rest[:at] + group + rest[at:]
        self.rebuild()
        new_errors = {k for k, v in self.state.items() if v.get("error")} - before_errors
        if new_errors:
            self.features = old
            self._undo.pop()
            self.rebuild()
            raise DocumentError(f"cannot move {name} there: {', '.join(sorted(new_errors))} would fail "
                                "(a feature must come after what it references)")

    def add_folder(self, name: str, features: list[str]) -> None:
        """Add to New Folder: group features in the FeatureManager (display only)."""
        if any(fd["name"] == name for fd in self.folders):
            raise DocumentError(f"a folder named {name!r} already exists")
        names = [self.find(n)["name"] for n in features]
        self._checkpoint()
        for fd in self.folders:  # a feature lives in one folder
            fd["features"] = [n for n in fd["features"] if n not in names]
        self.folders.append({"name": name, "features": names})
        self.dirty = True

    def remove_folder(self, name: str) -> None:
        """Dissolve a folder (its features stay)."""
        if not any(fd["name"] == name for fd in self.folders):
            raise DocumentError(f"no folder named {name!r}")
        self._checkpoint()
        self.folders = [fd for fd in self.folders if fd["name"] != name]
        self.dirty = True

    def rename(self, name: str, new_name: str) -> None:
        if any(f["name"] == new_name for f in self.features):
            raise DocumentError(f"{new_name} already exists")
        feat = self.find(name)
        self._checkpoint()
        old = feat["name"]
        feat["name"] = new_name
        for fd in getattr(self, "folders", []):
            fd["features"] = [new_name if n == old else n for n in fd["features"]]
        for f in self.features:  # keep references working
            p = f["params"]
            for key in ("sketch", "path", "plane", "reference", "neutral", "axis", "direction", "csys", "origin"):
                if p.get(key) == old:
                    p[key] = new_name
            if "profiles" in p:
                p["profiles"] = [new_name if x == old else x for x in p["profiles"]]
            if "features" in p:
                p["features"] = [new_name if x == old else x for x in p["features"]]
            if isinstance(p.get("axis"), str) and p["axis"].startswith(old + "/"):
                p["axis"] = new_name + p["axis"][len(old):]
        self.rebuild()

    def set_suppressed(self, name: str, suppressed: bool, config: str | None = None) -> None:
        """Suppress in the given (default: active) configuration; Default writes the feature flag."""
        feat = self.find(name)
        self._checkpoint()
        config = config or self.active_config
        if config == "Default":
            feat["suppressed"] = suppressed
        else:
            self._config(config)["suppressed"][feat["name"]] = suppressed
        self.rebuild()

    def set_variable(self, name: str, expression) -> None:
        if not name.isidentifier():
            raise DocumentError("global variable names must be identifiers (letters, digits, _)")
        self._checkpoint()
        old = dict(self.variables)
        self.variables[name] = str(expression)
        try:
            Evaluator(self.variables)
        except EquationError as exc:
            self.variables = old
            self._undo.pop()
            raise DocumentError(str(exc)) from exc
        self.rebuild()

    def delete_variable(self, name: str) -> None:
        if name not in self.variables:
            raise DocumentError(f"no global variable {name!r}")
        self._checkpoint()
        del self.variables[name]
        self.rebuild()

    def _target_slot(self, target: str):
        owner, key = parse_target(target)
        feat = self.find(owner)
        if feat["type"] == "sketch":
            for d in feat["params"].get("sketch", {}).get("dimensions", []):
                if d["id"] == key:
                    return feat, ("dim", d)
            raise DocumentError(f"{owner} has no dimension {key}")
        return feat, ("param", feature_param_path(feat["type"], key))

    def set_equation(self, target: str, expression: str) -> None:
        """Drive a dimension/parameter by an equation, e.g. set_equation('D1@Sketch1', '"width"/2')."""
        feat, (kind, slot) = self._target_slot(target)
        expr = expression if str(expression).strip().startswith("=") else "=" + str(expression)
        self._checkpoint()
        old = copy.deepcopy(feat["params"])
        if kind == "dim":
            slot["expr"] = expr
        else:
            set_path(feat["params"], slot, expr)
        self.rebuild()
        err = self.state.get(feat["name"], {}).get("error")
        if err:
            feat["params"] = old
            self._undo.pop()
            self.rebuild()
            raise DocumentError(f"{feat['name']}: {err}")

    def clear_equation(self, target: str) -> None:
        feat, (kind, slot) = self._target_slot(target)
        self._checkpoint()
        if kind == "dim":
            slot.pop("expr", None)
        else:
            cur = self.dim_values.get(f"{target.split('@')[0]}@{feat['name']}") if "@" in target else None
            set_path(feat["params"], slot, cur if cur is not None else get_path(feat["params"], slot))
        self.rebuild()

    def equations(self) -> dict:
        ev_globals = {}
        try:
            ev_globals = Evaluator(self.variables).globals
        except EquationError:
            pass
        eqs = []
        for f in self.features:
            if f["type"] == "sketch":
                for d in f["params"].get("sketch", {}).get("dimensions", []):
                    if d.get("expr"):
                        eqs.append({"target": f"{d['id']}@{f['name']}", "expression": d["expr"],
                                    "value": self.dim_values.get(f"{d['id']}@{f['name']}")})
            else:
                for i, path in enumerate(FEATURE_DIMS.get(f["type"], []), 1):
                    v = get_path(f["params"], path)
                    if is_expr(v):
                        eqs.append({"target": f"D{i}@{f['name']}", "parameter": path, "expression": v,
                                    "value": self.dim_values.get(f"D{i}@{f['name']}")})
        return {"global_variables": [{"name": k, "expression": v, "value": ev_globals.get(k)} for k, v in self.variables.items()],
                "equations": eqs, "dimensions": self.dim_values}

    def _config(self, name: str) -> dict:
        if name not in self.configurations:
            raise DocumentError(f"no configuration {name!r}")
        return self.configurations[name]

    def _effective_config(self, name: str) -> dict:
        """What a configuration amounts to: its own suppression states and values over its parent's, and so on
        up the chain (a derived configuration follows its parent wherever it does not say otherwise)."""
        chain, seen = [], set()
        while name in self.configurations and name not in seen:
            seen.add(name)
            chain.append(self.configurations[name])
            name = self.configurations[name].get("parent")
        out = {"suppressed": {}, "values": {}}
        for cfg in reversed(chain):
            out["suppressed"].update(cfg.get("suppressed", {}))
            out["values"].update(cfg.get("values", {}))
        return out

    def add_configuration(self, name: str, copy_from: str | None = None, derived_from: str | None = None) -> None:
        if not name or name in self.configurations:
            raise DocumentError(f"configuration {name!r} already exists" if name else "give the configuration a name")
        if derived_from is not None:
            self._config(derived_from)
            self._checkpoint()
            self.configurations[name] = {"suppressed": {}, "values": {}, "parent": derived_from}
        else:
            self._checkpoint()
            src = self._config(copy_from or self.active_config)
            self.configurations[name] = copy.deepcopy(src)
        self.active_config = name
        self.rebuild()

    def activate_configuration(self, name: str) -> None:
        self._config(name)
        self.active_config = name
        self.rebuild()

    def delete_configuration(self, name: str) -> None:
        if name == "Default":
            raise DocumentError("the Default configuration cannot be deleted")
        self._config(name)
        children = [n for n, c in self.configurations.items() if c.get("parent") == name]
        if children:
            raise DocumentError(f"{name} has derived configurations ({', '.join(children)}): delete those first")
        self._checkpoint()
        del self.configurations[name]
        if self.active_config == name:
            self.active_config = "Default"
        self.rebuild()

    def save_display_state(self, name: str, hidden: list | None = None) -> None:
        if not name:
            raise DocumentError("give the display state a name")
        self._checkpoint()
        states = dict(getattr(self, "display_states", {}))
        states[name] = {"appearance": copy.deepcopy(self.appearance)}
        self.display_states, self.display_state = states, name
        self.dirty = True
        self.revision += 1

    def activate_display_state(self, name: str | None) -> None:
        states = getattr(self, "display_states", {})
        if name is not None and name not in states:
            raise DocumentError(f"no display state {name!r}; this part has {list(states)}")
        self._checkpoint()
        if name is not None:
            self.appearance = copy.deepcopy(states[name]["appearance"])
        self.display_state = name
        self.dirty = True
        self.revision += 1

    def delete_display_state(self, name: str) -> None:
        states = dict(getattr(self, "display_states", {}))
        if name not in states:
            raise DocumentError(f"no display state {name!r}")
        self._checkpoint()
        del states[name]
        self.display_states = states
        if getattr(self, "display_state", None) == name:
            self.display_state = None
        self.dirty = True
        self.revision += 1

    def _config_key(self, target: str) -> str:
        owner, key = parse_target(target)
        owner = self.find(owner)["name"]
        return f"{key}@{owner}" if key.startswith("D") and key[1:].isdigit() else f"{owner}.{key}"

    def set_configuration_value(self, config: str, target: str, value) -> None:
        """Override a dimension/parameter in one configuration (value or '=expression'); None removes the
        override (the configuration then uses the base value)."""
        self._config(config)
        k = self._config_key(target)
        self._checkpoint()
        if value is None:
            self.configurations[config]["values"].pop(k, None)
        else:
            self.configurations[config]["values"][k] = value
        self.rebuild()

    def get_design_table(self) -> dict:
        """The design table: the stored one, or one made from the configurations (a column per overridden
        value and per feature suppressed somewhere; $STATE@Feature cells are S / U)."""
        if self.design_table:
            return copy.deepcopy(self.design_table)
        cols: list[str] = []
        for c in self.configurations.values():
            for k in c.get("values", {}):
                if k not in cols:
                    cols.append(k)
            for n in c.get("suppressed", {}):
                if f"$STATE@{n}" not in cols:
                    cols.append(f"$STATE@{n}")
        rows = []
        for name, c in self.configurations.items():
            vals = []
            for col in cols:
                if col.startswith("$STATE@"):
                    feat = col[7:]
                    base = next((f["suppressed"] for f in self.features if f["name"] == feat), False)
                    vals.append("S" if c.get("suppressed", {}).get(feat, base) else "U")
                else:
                    v = c.get("values", {}).get(col)
                    vals.append("" if v is None else v)
            rows.append({"name": name, "values": vals})
        return {"columns": cols, "rows": rows}

    def apply_design_table(self, columns: list[str], rows: list[dict]) -> list[str]:
        """Design Table: each row is a configuration (made if missing), each column a dimension ("D1@Sketch1",
        "Boss-Extrude1.depth") or "$STATE@Feature" (S suppressed / U unsuppressed); an empty cell keeps the
        base value. The table is kept with the part."""
        keys = []
        for col in columns:
            if col.startswith("$STATE@"):
                keys.append(("state", self.find(col[7:])["name"]))
            else:
                keys.append(("value", self._config_key(col)))
        self._checkpoint()
        made = []
        for row in rows:
            name = str(row["name"]).strip()
            if not name:
                continue
            if name not in self.configurations:
                self.configurations[name] = {"suppressed": {}, "values": {}}
                made.append(name)
            cfg = self.configurations[name]
            for (kind, key), cell in zip(keys, row.get("values", [])):
                cell = "" if cell is None else cell
                if kind == "state":
                    if str(cell).strip():
                        cfg["suppressed"][key] = str(cell).strip().upper().startswith("S")
                    else:
                        cfg["suppressed"].pop(key, None)
                elif str(cell).strip() == "":
                    cfg["values"].pop(key, None)
                else:
                    cfg["values"][key] = cell if isinstance(cell, (int, float)) else (
                        str(cell).strip() if str(cell).strip().startswith("=") else float(cell))
        self.design_table = {"columns": list(columns), "rows": copy.deepcopy(rows)}
        self.rebuild()
        return made

    def delete_design_table(self) -> None:
        """Delete the design table (the configurations it made stay)."""
        self._checkpoint()
        self.design_table = None
        self.dirty = True

    def _effective_params(self, f: dict, ev: Evaluator, cfg: dict) -> dict:
        p = copy.deepcopy(f["params"])
        name, overrides = f["name"], cfg.get("values", {})
        if f["type"] == "sketch":
            for d in p.get("sketch", {}).get("dimensions", []):
                key = f"{d['id']}@{name}"
                val = overrides.get(key, d.get("expr"))
                if val is not None and not d.get("driven"):
                    d["value"] = ev.eval(val)
            return p
        for i, path in enumerate(FEATURE_DIMS.get(f["type"], []), 1):
            key = f"D{i}@{name}"
            val = overrides.get(key, overrides.get(f"{name}.{path}", get_path(p, path)))
            if val is None:
                continue
            if is_expr(val) or isinstance(val, (int, float)):
                v = ev.eval(val)
                set_path(p, path, int(v) if "count" in path else v)
                ev.values[key] = v
        for k, v in list(p.items()):  # any other parameter given as an expression or overridden
            if f"{name}.{k}" in overrides:
                v = overrides[f"{name}.{k}"]
            if is_expr(v):
                p[k] = ev.eval(v)
        return p

    def rollback_to(self, name: str | None) -> None:
        """Move the rollback bar to just after `name` (None = end of tree)."""
        self._checkpoint()
        self.rollback = None if name is None else self.index(name) + 1
        if self.rollback is not None and self.rollback >= len(self.features):
            self.rollback = None
        self.rebuild()

    def set_material(self, name: str, density: float | None = None, properties: dict | None = None) -> None:
        """Library (or custom library) material by name; density / properties override its values (elastic
        modulus, Poisson ratio, strengths, conductivity, specific heat, colour), as an edited copy does."""
        from openswx.core.materials import FIELDS, lookup
        lib = lookup(name) or {}
        props = {k: v for k, v in (properties or {}).items() if k in FIELDS and k != "category" and v is not None}
        if density is None and not lib and not props.get("density"):
            raise DocumentError(f"{name!r} is not in the material library; give its density")
        self._checkpoint()
        self.material = {**lib, **props, "name": lib.get("name", name),
                         "density": float(density if density is not None else props.get("density", lib.get("density")))}
        self.revision += 1

    def set_appearance(self, color: str | None, target: str = "part", ref=None) -> None:
        """Colour the whole part, one feature's faces, or a single face (ref = face reference dict)."""
        if color is not None and not (isinstance(color, str) and len(color) == 7 and color.startswith("#")):
            raise DocumentError("colour must be '#rrggbb' or null to clear")
        self._checkpoint()
        a = self.appearance
        if target == "part":
            a["part"] = color
        elif target == "feature":
            self.find(ref)
            if color is None:
                a["features"].pop(ref, None)
            else:
                a["features"][ref] = color
        elif target == "face":
            a["faces"] = [x for x in a["faces"] if x["ref"] != ref]
            if color is not None:
                a["faces"].append({"ref": ref, "color": color})
        else:
            raise DocumentError("target must be part, feature or face")
        self.revision += 1

    def face_colors(self) -> list:
        """Per-face display colour (None = default), in body.faces() order: face > feature > part."""
        from openswx.core.geom import resolve_face
        body = self.body
        if body is None:
            return []
        faces = body.faces()
        cols = [self.appearance.get("part") or None] * len(faces)
        named = [f["name"] for f in self.features if f["name"] in self.appearance.get("features", {})]
        if named:
            from openswx.core.geom import interior_point
            pts = [interior_point(f) for f in faces]
        for name in named:  # a face belongs to a feature when it lies on that feature's tool boundary
            tools = [tf for _, t in self.ctx.tools.get(name, []) for tf in t.faces()]
            for i, c in enumerate(pts):
                if any(tf.distance_to(c) < 1e-5 for tf in tools):
                    cols[i] = self.appearance["features"][name]
        for item in self.appearance.get("faces", []):
            try:
                target = resolve_face(body, item["ref"])
            except Exception:  # the face no longer exists after an edit
                continue
            for i, f in enumerate(faces):
                if f.is_same(target):
                    cols[i] = item["color"]
        return cols

    def rebuild(self) -> dict:
        t0 = time.perf_counter()
        ctx = Ctx(flat=getattr(self, "flattened", False))
        state: dict[str, dict] = {}
        active = self.features if self.rollback is None else self.features[: self.rollback]
        cfg = self._effective_config(self.active_config)
        # sheet metal Unfold: the bends it lists (those before it) are built flat from the start; its Fold bends
        # them back, wrapping onto the bends whatever was cut in between
        seen: set[str] = set()
        for f in active:
            if f["type"] == "unfold" and not cfg.get("suppressed", {}).get(f["name"], f["suppressed"]):
                for b in f["params"].get("bends") or []:
                    if b in seen and b not in ctx.unfold:
                        ctx.unfold[b] = f["name"]
            seen.add(f["name"])
        try:
            ev = Evaluator(self.variables)
        except EquationError as exc:
            ev = Evaluator({})
            state["__equations__"] = {"status": "error", "error": str(exc)}
        # incremental: the state after each feature is kept under a hash of everything up to it, so a rebuild
        # after an edit picks up from the first feature that changed (the history before it is not rebuilt)
        cache = self.__dict__.setdefault("_rb_cache", {})
        if len(cache) > 600:  # (the oldest half goes, instead of all of it)
            for k in list(cache)[:300]:
                cache.pop(k, None)
        chain = hashlib.sha1(repr((ctx.flat, sorted(ctx.unfold.items()))).encode())
        reuse = True
        bodies: list[tuple[str, object]] = []  # (feature, body after it): which feature made each face
        for f in self.features:
            if f not in active:
                state[f["name"]] = {"status": "rolled_back"}
                chain.update(b"\0rolled back " + f["name"].encode())
                continue
            if cfg.get("suppressed", {}).get(f["name"], f["suppressed"]):
                state[f["name"]] = {"status": "suppressed"}
                chain.update(b"\0suppressed " + f["name"].encode())
                continue
            key = None
            try:
                params = self._effective_params(f, ev, cfg)
                chain.update(json.dumps([f["name"], f["type"], params], sort_keys=True, default=str).encode())
                key = chain.hexdigest()
                hit = cache.get(key) if reuse else None
                if hit is not None:
                    ctx, ev.values = _ctx_copy(hit[0]), dict(hit[2])
                    state[f["name"]] = dict(hit[1])
                    if hit[3] is not None:  # (the solved sketch goes back into the feature, as when built)
                        _keep_solved(f, copy.deepcopy(hit[3]), ev)
                    bodies.append((f["name"], ctx.body))
                    continue
                reuse = False
                before = ctx.body
                BUILDERS[f["type"]](ctx, params, f["name"])
                if ctx.body is not None and ctx.body is not before:  # persistent face names (openswx/core/naming.py)
                    sks = [(s, *ctx.sketches[s]) for s in consumed_sketches(f) if s in ctx.sketches]
                    try:
                        naming.register(ctx.body, naming.name_faces(ctx.body, before, f["name"], sks))
                    except Exception:  # (unnamed faces are found by geometry, as before)
                        pass
                if f["type"] == "sketch":  # keep solved geometry, preserve expressions
                    _keep_solved(f, params["sketch"], ev)
                state[f["name"]] = {"status": "ok", **ctx.info.get(f["name"], {})}
            except (FeatureError, SketchError, KeyError, ValueError, EquationError) as exc:
                state[f["name"]] = {"status": "error", "error": str(exc)}
            except Exception as exc:  # kernel failures
                state[f["name"]] = {"status": "error", "error": f"geometry kernel error: {exc}"}
            if key is not None:
                solved = copy.deepcopy(params["sketch"]) if f["type"] == "sketch" and params else None
                cache[key] = (_ctx_copy(ctx), dict(state[f["name"]]), dict(ev.values), solved)
            bodies.append((f["name"], ctx.body))
        self.ctx, self.state, self.dim_values = ctx, state, dict(ev.values)
        self._feature_bodies, self._face_owner = bodies, None
        self.revision += 1
        self.dirty = True
        return {"seconds": round(time.perf_counter() - t0, 3), "errors": {k: v["error"] for k, v in state.items() if "error" in v}}

    @property
    def body(self):
        return self.ctx.body

    def tree(self) -> list[dict]:
        items = []
        for i, f in enumerate(self.features):
            st = self.state.get(f["name"], {})
            items.append({
                "id": f["id"], "name": f["name"], "type": f["type"], "suppressed": f["suppressed"],
                "status": st.get("status"), "error": st.get("error"), "sketch_state": st.get("sketch_state"),
                "absorbed_by": next((g["name"] for g in self.features if f["name"] in consumed_sketches(g)), None),
                "rolled_back": self.rollback is not None and i >= self.rollback,
            })
        return items

    def summary(self) -> dict:
        mp = mass_properties(self.body, self.material["density"])
        return {
            "name": self.name, "path": self.path, "units": self.units, "material": self.material,
            "appearance": self.appearance, "properties": self.properties, "drawing": self.drawing, "study": self.study,
            "revision": self.revision, "dirty": self.dirty, "rollback": self.rollback,
            "can_undo": bool(self._undo), "can_redo": bool(self._redo),
            "default_tree": DEFAULT_TREE, "features": self.tree(),
            "configurations": list(self.configurations), "active_config": self.active_config,
            "flattened": getattr(self, "flattened", False), "sheet_metal": self.ctx.sheet is not None,
            "global_variables": self.variables, "tolerances": getattr(self, "tolerances", {}),
            "requirements": getattr(self, "requirements", []), "folders": self.folders,
            "display_states": list(getattr(self, "display_states", {})), "display_state": getattr(self, "display_state", None),
            "derived_configurations": {n: c["parent"] for n, c in self.configurations.items() if c.get("parent")}, "design_table": self.design_table is not None,
            "named_views": getattr(self, "named_views", {}), "pmi": getattr(self, "pmi", []),
            "render": getattr(self, "render", {}),
            "bodies": mp["bodies"], "volume_mm3": round(mp["volume"], 6),
            "errors": {k: v["error"] for k, v in self.state.items() if v.get("error")},
        }

    def feature_detail(self, name: str) -> dict:
        f = self.find(name)
        out = copy.deepcopy(f)
        out["state"] = self.state.get(f["name"], {})
        if f["type"] == "sketch":
            out["sketch"] = Sketch(f["params"].get("sketch")).summary()
            out["sketch"]["status"] = f["params"].get("sketch", {}).get("status", {})
        return out

    def face_features(self) -> dict[str, str]:
        """Which feature made each face of the part ({"Face3": "Boss-Extrude1"}): the first feature after
        which a face of the same kind lies at the face's interior point. Used to highlight a feature's faces
        and a face's feature in the FeatureManager, as SOLIDWORKS does."""
        if getattr(self, "_face_owner", None) is not None:
            return self._face_owner
        from openswx.core.geom import geom_name, interior_point
        out: dict[str, str] = {}
        body = self.body
        if body is not None:
            import numpy as np
            steps, prev = [], None
            for name, b in getattr(self, "_feature_bodies", []):
                if b is not None and b is not prev:
                    faces = b.faces()
                    boxes = [g.bounding_box() for g in faces]
                    lo = np.array([[q.min.X, q.min.Y, q.min.Z] for q in boxes]) - 1e-4
                    hi = np.array([[q.max.X, q.max.Y, q.max.Z] for q in boxes]) + 1e-4
                    steps.append((name, faces, np.array([geom_name(g) for g in faces]), lo, hi))
                prev = b
            for i, f in enumerate(body.faces()):
                kind, p = geom_name(f), interior_point(f)
                q = np.array([p.X, p.Y, p.Z])
                for name, faces, kinds, lo, hi in steps:
                    cand = np.nonzero((kinds == kind) & (lo <= q).all(axis=1) & (q <= hi).all(axis=1))[0]
                    if any(faces[j].distance_to(p) < 1e-5 for j in cand):
                        out[f"Face{i}"] = name
                        break
        self._face_owner = out
        return out

    def topology(self) -> dict:
        body = self.body
        if body is None:
            return {"faces": [], "edges": []}
        from openswx.core.naming import labels_of
        body_faces, body_edges = body.faces(), body.edges()
        faces = [face_info(i, f) for i, f in enumerate(body_faces)]
        edges = [edge_info(i, e) for i, e in enumerate(body_edges)]
        labels = labels_of(body) or {}
        sides: dict[int, list] = {}  # edge hash -> [(edge, name of a face it bounds)]: one pass over the faces
        for info, f in zip(faces, body_faces):  # persistent names: valid after later edits, unlike the ids
            label = labels.get(hash(f))
            if label:
                info["name"] = label
            for e in f.edges():
                sides.setdefault(hash(e), []).append((e, label))
        for info, e in zip(edges, body_edges):
            names = [lab for other, lab in sides.get(hash(e), ()) if other.is_same(e)]
            if len(names) == 2 and all(names):
                info["name"] = "|".join(sorted(names))  # an edge is named by the two faces it lies between
        return {"faces": faces, "edges": edges}

    def mesh(self) -> dict:
        m = tessellate(self.body)
        for sname, surf in self.ctx.surfaces.items():  # surface bodies (shown, not pickable as solid faces)
            sm = tessellate(surf)
            m.setdefault("surfaces", []).extend({**f, "id": f"{sname}/{f['id']}"} for f in sm["faces"])
            m["edges"].extend({**e, "id": f"{sname}/{e['id']}"} for e in sm["edges"])
        for f, c in zip(m["faces"], self.face_colors()):
            if c:
                f["color"] = c
        m["material_color"] = self.material.get("color")
        m["sketches"] = self.sketch_overlays()
        m["planes"] = self.plane_overlays()
        m["refs"] = self.ref_overlays()
        m["revision"] = self.revision
        return m

    def sketch_overlays(self) -> list[dict]:
        out = []
        for name, (sk, plane) in self.ctx.sketches.items():
            st = sk.status.get("entities", {})
            ents = []
            for eid, e in sk.entities.items():
                if e["type"] == "point":
                    p = plane.from_local_coords(_v(sk.coords(e["p"])))
                    ents.append({"id": eid, "type": "point", "p": [p.X, p.Y, p.Z], "state": st.get(eid)})
                    continue
                from openswx.core.sketch import _entity_edges
                for edge in _entity_edges(sk, e, plane):
                    n = 2 if edge.geom_type.name.lower() == "line" else (12 if e["type"] == "text" else 48)
                    pts = [edge.position_at(t / (n - 1)) for t in range(n)]
                    ents.append({"id": eid, "type": e["type"], "construction": e.get("construction", False),
                                 "state": st.get(eid), "p": [c for q in pts for c in (q.X, q.Y, q.Z)]})
            feat = self.find(name)
            out.append({"name": name, "entities": ents, "state": sk.status.get("state"),
                        "origin": list(plane.origin), "x": list(plane.x_dir), "z": list(plane.z_dir),
                        "absorbed": any(name in consumed_sketches(g) and self.state.get(g["name"], {}).get("status") == "ok"
                                        for g in self.features),
                        "hidden": feat["params"].get("hidden", False)})
        return out

    def ref_overlays(self) -> list[dict]:
        """Reference axes, points and coordinate systems for the viewport (hidden ones flagged)."""
        out = []
        hidden = {f["name"] for f in self.features if f["params"].get("hidden")}
        for name, ax in self.ctx.axes.items():
            out.append({"kind": "axis", "name": name, "origin": list(ax.position), "dir": list(ax.direction), "hidden": name in hidden})
        for name, q in self.ctx.points.items():
            out.append({"kind": "point", "name": name, "p": list(q), "hidden": name in hidden})
        for name, edges in self.ctx.curves.items():
            pts = []
            for e in edges:
                n = 2 if e.geom_type.name.lower() == "line" else min(2000, max(16, int(e.length / 2)))
                pts.append([c for t in range(n) for c in e.position_at(t / (n - 1))])
            out.append({"kind": "curve", "name": name, "polylines": pts, "hidden": name in hidden})
        for name, pl in self.ctx.csys.items():
            out.append({"kind": "csys", "name": name, "origin": list(pl.origin), "x": list(pl.x_dir), "y": list(pl.y_dir),
                        "z": list(pl.z_dir), "hidden": name in hidden})
        return out

    def plane_overlays(self) -> list[dict]:
        """Planes for the viewport: shown (translucent) unless hidden - a plane feature's own flag, the standard
        planes' in appearance["hidden"]."""
        out = []
        hidden = set(self.appearance.get("hidden", [])) | {f["name"] for f in self.features if f["params"].get("hidden")}
        for name, pl in self.ctx.planes.items():
            out.append({"name": name, "origin": list(pl.origin), "x": list(pl.x_dir), "z": list(pl.z_dir),
                        "standard": name in ("Front", "Top", "Right"), "hidden": name in hidden})
        return out


def _v(xy):
    from build123d import Vector
    return Vector(xy[0], xy[1], 0)
