"""Application session and the command registry shared by the web UI, the MCP server and tests.

Every command is a plain typed function. Mutating commands run under a lock and bump the
application version so connected UIs refresh live (SSE).
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import inspect
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal

from . import applog, jobs
from .document import DocumentError, PartDocument
from .features import END_CONDITIONS, FeatureError, encode_shape, mass_properties
from .geom import edge_ref, face_ref
from .sketch import SketchError

SheetSize = Literal["A4", "A3", "A2", "A1", "A0", "A4 portrait", "ANSI A", "ANSI B", "ANSI C", "ANSI D", "ANSI E"]

Vec2 = list[float]
Vec3 = list[float]


class Application:
    def __init__(self):
        self.lock = threading.RLock()
        self.documents: dict[str, PartDocument] = {}
        self.active: str | None = None
        self.version = 0
        self.log: list[dict] = []
        self.capture_requests: dict[str, dict] = {}
        self._doc_counter = 0
        self.sim_result = None  # (document revision, fea result) of the last simulation_run
        self.thermal_result = None
        self.modal_result = None
        self.buckling_result = None
        self.context: dict | None = None  # editing a part in an assembly's context: {"assembly", "component", "part"}
        self.macro: list[dict] | None = None  # commands recorded since macro_start (None: not recording)
        self.running: dict[int, tuple] = {}  # thread id -> (command, start time) of the commands in progress

    @property
    def busy(self):
        """(command, start time) of the longest-running command, or None."""
        r = list(self.running.values())
        return min(r, key=lambda e: e[1]) if r else None

    def new_document(self, name: str | None = None) -> PartDocument:
        self._doc_counter += 1
        name = name or f"Part{self._doc_counter}"
        while name in self.documents:
            self._doc_counter += 1
            name = f"Part{self._doc_counter}"
        doc = PartDocument(name)
        doc.dirty = False
        self.documents[name] = doc
        self.active = name
        return doc

    @property
    def active_doc(self):
        if self.active is None or self.active not in self.documents:
            self.new_document()
        d = self.documents[self.active]
        if getattr(d, "kind", "part") == "assembly":
            d.live_docs = self.documents
        return d

    @property
    def doc(self) -> PartDocument:
        d = self.active_doc
        if getattr(d, "kind", "part") != "part":
            raise DocumentError(f"{d.name} is an assembly; activate a part document (activate_document) for part commands")
        return d

    @property
    def asm(self):
        d = self.active_doc
        if getattr(d, "kind", "part") != "assembly":
            raise DocumentError("the active document is not an assembly; use new_assembly or activate_document")
        return d

    def bump(self, command: str, args: dict, ok: bool, message: str = "", origin: str = "agent") -> None:
        self.version += 1
        self.log.append({"t": time.time(), "command": command, "args": _short(args), "ok": ok,
                         "message": message, "origin": origin})
        self.log = self.log[-200:]


APP = Application()
_LOG = applog.setup()
COMMANDS: dict[str, dict] = {}
ERRORS = (DocumentError, FeatureError, SketchError, ValueError, KeyError, FileNotFoundError)
_TL = threading.local()  # .origin = "ui" for web requests; MCP/tests default to "agent"


class CommandError(RuntimeError):
    pass


def command(mutates: bool = True, category: str = "general", mcp: bool = True):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = inspect.signature(fn).bind(*args, **kwargs)
            origin = getattr(_TL, "origin", "agent")
            depth = getattr(_TL, "depth", 0)  # (commands called by commands are not the agent's own requests)
            if origin == "agent" and depth == 0:
                _check_workspace(fn.__name__, bound.arguments)
            if not APP.lock.acquire(timeout=30):  # never hang a client forever behind a stuck command
                busy = getattr(APP, "busy", None)
                raise CommandError(f"openswx is busy with {busy[0] if busy else 'another command'}; try again")
            outer = None if depth == 0 else True  # (a command called by a command is not the outermost)
            if outer is None:
                APP.running[threading.get_ident()] = (fn.__name__, time.time())
            watch = None  # a document without unsaved changes: did this command change it? (see _mark_dirty)
            if mutates and outer is None and not getattr(_TL, "preview", False):
                watch = APP.documents.get(APP.active)
                if watch is not None and not watch.dirty and getattr(watch, "_clean", None) is None:
                    watch._clean = _fingerprint(watch)
            t0 = time.perf_counter()
            _TL.depth = depth + 1
            try:
                result = fn(*bound.args, **bound.kwargs)  # (as bound: the workspace check may have resolved paths)
                if watch is not None:
                    _mark_dirty(watch)
                if outer is None and not getattr(_TL, "preview", False) and (mutates or time.perf_counter() - t0 > 1.0):
                    _LOG.info("%s (%s) %.3f s", fn.__name__, origin, time.perf_counter() - t0)
                    _LOG.debug("%s args %s", fn.__name__, _short(dict(bound.arguments)))
            except ERRORS as exc:
                if mutates and not getattr(_TL, "preview", False):
                    APP.bump(fn.__name__, dict(bound.arguments), False, str(exc), origin)
                if outer is None and not getattr(_TL, "preview", False):
                    _LOG.warning("%s (%s) failed after %.3f s: %s", fn.__name__, origin, time.perf_counter() - t0, exc,
                                 exc_info=True)
                raise CommandError(str(exc)) from exc
            except CommandError:
                raise
            except jobs.Cancelled as exc:
                raise CommandError("cancelled") from exc
            except Exception:  # a defect, not a user error: the traceback is what a bug report needs
                _LOG.exception("%s (%s) crashed after %.3f s", fn.__name__, origin, time.perf_counter() - t0)
                raise
            finally:
                _TL.depth = depth
                if outer is None:
                    APP.running.pop(threading.get_ident(), None)
                APP.lock.release()
            if mutates and not getattr(_TL, "preview", False):
                with APP.lock:
                    APP.bump(fn.__name__, dict(bound.arguments), True, "", origin)
                    if APP.macro is not None and outer is None and not fn.__name__.startswith("macro_"):
                        APP.macro.append({"command": fn.__name__,
                                          "args": json.loads(json.dumps(dict(bound.arguments), default=str))})
                if depth == 0:
                    _journal(fn.__name__, bound.arguments)
                if outer is None:
                    from . import recovery
                    recovery.start(APP)  # (autosave thread: started by the first change, see recovery.py)
            return result
        COMMANDS[fn.__name__] = {"fn": wrapper, "mutates": mutates, "category": category, "mcp": mcp,
                                 "doc": inspect.getdoc(fn) or ""}
        return wrapper
    return deco


# Arguments that name files or folders, per command (everything else called `path` is a sketch or curve name).
FILE_ARGS = {
    "open_part": ("path",), "save_part": ("path",), "save_drawing_document": ("path",), "import_file": ("path",),
    "export_file": ("path",), "create_drawing": ("path",), "export_drawing": ("path",), "insert_component": ("source",),
    "replace_component": ("source",), "motion_export": ("path",), "export_bom": ("path",), "import_dxf": ("path",),
    "export_sketch_dxf": ("path",), "export_flat_pattern_dxf": ("path",), "insert_part": ("path",),
    "mirror_part": ("path",), "add_part_to_design_library": ("path",), "pack_and_go": ("folder", "zip_file"),
    "pdm_set_vault": ("path",), "pdm_check_in": ("path",), "export_edrawing": ("path",), "macro_stop": ("path",),
    "run_macro": ("path",), "cam_gcode": ("path",), "inspection_report": ("path",), "electrical_import_wires": ("path",),
    "electrical_formboard": ("path",), "composer_export": ("path",), "save_diagnostic_report": ("path",),
    "export_script": ("path",), "export_handoff_pack": ("folder",), "compare_documents": ("a", "b"),
}


def workspace() -> Path | None:
    """The folder agents are confined to (OPENSWX_WORKSPACE / --workspace), or None: no limit."""
    w = os.environ.get("OPENSWX_WORKSPACE", "").strip()
    return Path(w).resolve() if w else None


def _check_workspace(name: str, arguments) -> None:
    """With a workspace set, a command from an agent may only name files inside it. The user's own actions in
    the UI are not limited."""
    ws = workspace()
    if ws is None:
        return
    for arg in FILE_ARGS.get(name, ()):
        v = arguments.get(arg)
        if not isinstance(v, str) or not v:
            continue
        if arg in ("source", "a", "b") and v in APP.documents:  # (an open document's name, not a file)
            continue
        p = Path(v)
        target = (p if p.is_absolute() else ws / p).resolve()  # (resolve follows links: no way out through one)
        if target != ws and ws not in target.parents:
            raise CommandError(f"{name}: {v!r} is outside the agent workspace {str(ws)!r}; agents may only read and "
                               f"write files there (relative paths are taken from it)")
        arguments[arg] = str(target)


# Commands that are about files and the session, not about what the document is: never part of its script.
NO_JOURNAL = {"new_part", "new_assembly", "open_part", "save_part", "save_drawing_document", "activate_document",
              "close_document", "export_file", "export_bom", "export_drawing", "export_sketch_dxf",
              "export_flat_pattern_dxf", "export_edrawing", "pack_and_go", "motion_export", "undo", "redo",
              "recover_document", "discard_recovered", "save_custom_material", "delete_custom_material",
              "inspection_report", "composer_export", "electrical_formboard", "export_script", "export_handoff_pack"}


def _journal(name: str, arguments) -> None:
    """Record a command in the active document's journal (the script that rebuilds it; see export_script).
    Undo and redo need no entry: the journal is part of the state they put back."""
    if name == "open_part":  # a foreign file opened as a new document: its script starts with the import
        doc = APP.documents.get(APP.active)
        if doc is not None and getattr(doc, "journal", None) == [] and getattr(doc, "features", None):
            doc.journal = [{"command": "import_file", "args": {"path": str(arguments.get("path"))}}]
        return
    if name in NO_JOURNAL or name.startswith(("pdm_", "macro_")) or name == "run_macro":
        return
    doc = APP.documents.get(APP.active)
    if doc is None or getattr(doc, "journal", None) is None:
        return
    try:
        doc.journal.append({"command": name, "args": json.loads(json.dumps(dict(arguments), default=str))})
    except (TypeError, ValueError):
        doc.journal = None  # (an argument that cannot be written down: the script would be incomplete)


def _fingerprint(doc) -> str | None:
    try:
        return hashlib.sha1(json.dumps(doc.to_dict(), default=str).encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 - a document that cannot be written out is reported when it is saved
        return None


def _mark_dirty(doc) -> None:
    """Commands that set a property (material, units, appearance, ...) do not all flag the document as changed:
    a document that was clean before a mutating command is compared with what it was then. Costs one
    serialisation per command, and only while the document has no unsaved changes."""
    if doc.dirty:
        doc._clean = None
    elif getattr(doc, "_clean", None) is not None and _fingerprint(doc) != doc._clean:
        doc.dirty, doc._clean = True, None


@contextlib.contextmanager
def unlocked():
    """Let other commands run during a long computation. Use inside a command, around work on data it has
    already read (the body, the study): the command lock is released for the block and taken back after it,
    so the user can keep rotating, measuring - even editing - while a solver runs. Results must be stored
    after the block, against the revision read before it."""
    if getattr(_TL, "preview", False) or getattr(_TL, "depth", 0) != 1:
        yield  # (inside another command, or a preview on a throw-away copy: keep the lock)
        return
    state = APP.lock._release_save()
    try:
        yield
    finally:
        APP.lock._acquire_restore(state)


def call(name: str, args: dict | None = None, origin: str | None = None) -> Any:
    if name not in COMMANDS:
        raise CommandError(f"unknown command {name!r}")
    prev = getattr(_TL, "origin", None)
    if origin:
        _TL.origin = origin
    try:
        return COMMANDS[name]["fn"](**(args or {}))
    finally:
        if origin:
            _TL.origin = prev if prev is not None else "agent"


def preview(name: str, args: dict | None = None) -> dict:
    """Run a mutating command on a throw-away copy of the active document and return its mesh."""
    with APP.lock:
        key = APP.active
        real = APP.active_doc
        clone = type(real)(real.name)
        if getattr(real, "kind", "part") == "assembly":
            clone.live_docs = APP.documents
        clone.load_dict(real.to_dict())
        APP.documents[key] = clone
        _TL.preview = True
        try:
            call(name, args)
            mesh = clone.mesh()
            mesh["ok"] = True
            return mesh
        except CommandError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            _TL.preview = False
            APP.documents[key] = real


def _short(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        s = repr(v)
        out[k] = v if len(s) < 200 else s[:200] + "..."
    return out


# ---------------------------------------------------------------------- selection helpers
def _face(doc: PartDocument, sel) -> dict:
    """Accept 'Face3' (from get_topology) or a reference dict."""
    if isinstance(sel, dict):
        return sel
    if doc.body is None:
        raise DocumentError("the part has no body yet")
    faces = doc.body.faces()
    if not re.fullmatch(r"(Face)?\d+", str(sel)):  # a persistent name from get_topology ('Boss-Extrude1:cap+')
        from .naming import find_face
        hit = find_face(doc.body, str(sel))
        if hit is None:
            raise DocumentError(f"unknown face {sel!r}; call get_topology for current face ids and names")
        return face_ref(hit, doc.body)
    try:
        return face_ref(faces[int(str(sel).replace("Face", ""))], doc.body)
    except (ValueError, IndexError):
        raise DocumentError(f"unknown face {sel!r}; call get_topology for current face ids") from None


def _edge(doc: PartDocument, sel) -> dict:
    if isinstance(sel, dict):
        return sel
    if doc.body is None:
        raise DocumentError("the part has no body yet")
    edges = doc.body.edges()
    if "|" in str(sel):  # a persistent name from get_topology: the two faces the edge lies between
        from .naming import find_edge
        hit = find_edge(doc.body, str(sel).split("|"))
        if hit is None:
            raise DocumentError(f"unknown edge {sel!r}; call get_topology for current edge ids and names")
        return edge_ref(hit, doc.body)
    try:
        return edge_ref(edges[int(str(sel).replace("Edge", ""))], doc.body)
    except (ValueError, IndexError):
        raise DocumentError(f"unknown edge {sel!r}; call get_topology for current edge ids") from None


def _is_edge_sel(s) -> bool:
    """'Edge7', or an edge's persistent name ('faceA|faceB')."""
    return isinstance(s, str) and (s.startswith("Edge") or "|" in s)


def _is_face_sel(s) -> bool:
    """'Face3', or a face's persistent name ('Boss-Extrude1:cap+': feature, colon, what made the face)."""
    return isinstance(s, str) and not _is_edge_sel(s) and (s.startswith("Face") or ":" in s)


def _plane_spec(doc: PartDocument, plane):
    if _is_face_sel(plane):
        return _face(doc, plane)
    return plane


def _axis_spec(doc: PartDocument, axis):
    if _is_edge_sel(axis):
        return _edge(doc, axis)
    if _is_face_sel(axis):
        return _face(doc, axis)
    return axis


def _dim_id(dim: str) -> tuple[str | None, str]:
    if "@" in dim:
        d, sk = dim.split("@", 1)
        return sk, d
    return None, dim


# ====================================================================== document commands
@command(category="document")
def new_part(name: str | None = None) -> dict:
    """Create a new, empty part document (like File > New > Part) and make it active."""
    return APP.new_document(name).summary()


@command(category="document")
def open_part(path: str, history: bool = True) -> dict:
    """Open a document. .oswprt/.oswasm open natively; .step/.stp/.brep and Parasolid .x_t/.x_b are imported
    as a base feature. .sldprt: with history (default) the feature tree is rebuilt as editable openswx
    features when every feature is understood and the result matches the file's embedded Parasolid body;
    otherwise that body is imported as one feature. .sldasm: components (each part read natively) at their
    saved positions, with the common mates. Everything is read natively: openswx never uses SOLIDWORKS,
    installed or not."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    ext = p.suffix.lower()
    if ext == ".oswprt":
        doc = PartDocument.open(str(p))
        APP.documents[doc.name] = doc
        APP.active = doc.name
        return doc.summary()
    if ext in (".step", ".stp", ".brep", ".x_t", ".x_b", ".xmt_txt", ".xmt_bin", ".igs", ".iges", ".stl", ".3mf"):
        doc = APP.new_document(p.stem)
        _import_file(doc, str(p))
        return doc.summary()
    if ext == ".oswdrw":  # a drawing document: open (or reuse) its model, then show the drawing
        data = json.loads(p.read_text(encoding="utf-8"))
        from . import migrate
        try:
            data = migrate.migrate(data, "openswx-drawing")
        except migrate.FormatError as exc:
            raise DocumentError(f"{p.name}: {exc}") from exc
        ref = Path(data["model"])
        model = (ref if ref.is_absolute() else p.parent / ref).resolve()
        doc = next((d for d in APP.documents.values() if getattr(d, "path", None) and Path(d.path).resolve() == model), None)
        if doc is None:
            if not model.exists():
                raise DocumentError(f"the drawing's model {model} is missing")
            open_part(str(model))
            doc = APP.active_doc
        else:
            APP.active = next(n for n, d in APP.documents.items() if d is doc)
        doc.drawing = data.get("drawing") or {}
        doc.drawing_path = str(p.resolve())
        return {**doc.summary(), "open_drawing": True}
    if ext == ".oswasm":
        import json as _json
        from .assembly import AssemblyDocument
        asm = AssemblyDocument(p.stem)
        asm.live_docs = APP.documents
        asm.load_dict(_json.loads(p.read_text(encoding="utf-8")))
        asm.path, asm.dirty = str(p), False
        APP.documents[asm.name] = asm
        APP.active = asm.name
        return asm.summary()
    if ext == ".slddrw":  # a SOLIDWORKS drawing: open its model natively and recreate the sheets and views
        from .sw_drawing import find_model, read_drawing
        drw = read_drawing(str(p))
        model = find_model(str(p), drw["models"])
        if model is None:
            raise DocumentError(f"{p.name}: its model ({', '.join(Path(m.replace(chr(92), '/')).name for m in drw['models']) or 'none named'}) "
                                "is not beside the drawing; put the part / assembly file next to it")
        res = open_part(str(model), history)
        APP.active_doc.show_drawing = True  # the UI opens the drawing editor on the next refresh
        return {**_recreate_drawing(drw), "model_file": str(model), "open_drawing": True, "model": APP.active_doc.summary(),
                "note": "sheets and views recreated from the SOLIDWORKS drawing (sheet size, view orientations, positions and scales), "
                        "dimensions placed by value on what measures them; notes and other annotations are not imported yet", "model_open": {k: v for k, v in res.items() if k != "model"}}
    if ext in (".sldprt", ".sldasm", ".slddrw"):
        from .sldprt import read_info
        info = read_info(str(p))
        native_error = None
        if ext == ".sldasm":
            try:
                asm, notes = _open_sldasm_native(p)
            except Exception as exc:  # report what the container holds
                native_error = str(exc)
            else:
                return {"model": asm.summary(), "geometry": "read natively (component tree + embedded Parasolid data)",
                        "notes": notes, "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        history_error = None
        if ext == ".sldprt" and history:
            from . import cache
            from .sw_rebuild import RebuildError, rebuild_isolated
            doc = None
            ck = cache.key(str(p), "sldprt-history")
            try:  # in a child process: a kernel crash while trying candidates must not take openswx down
                hit = cache.get_json("history", ck)  # (rebuilt before: the result, or why it was not rebuilt)
                if hit is not None and not hit.get("ok"):
                    raise RebuildError(hit["error"])
                if hit is not None:
                    data, report = hit["data"], hit["report"]
                else:
                    try:
                        data, report = rebuild_isolated(str(p))
                    except RebuildError as exc:  # (not when it ran out of time or crashed: that can go otherwise)
                        if not re.search(r"took longer|crashed", str(exc)):
                            cache.put_json("history", ck, {"ok": False, "error": str(exc)})
                        raise
                    cache.put_json("history", ck, {"ok": True, "data": data, "report": report})
                doc = APP.new_document(p.stem)
                data["name"] = doc.name
                doc.load_dict(data)
                if any(v.get("status") == "error" for v in doc.state.values()):
                    raise DocumentError("the rebuilt history does not regenerate")
                if "regenerated_volume" in report and abs((doc.body.volume if doc.body is not None else 0.0)
                                                          - report["regenerated_volume"]) > 1e-6 * max(1.0, report["regenerated_volume"]):
                    raise DocumentError("the rebuilt history regenerates to a different body")
            except Exception as exc:  # history not understood: open the stored body instead
                if doc is not None:
                    APP.documents.pop(doc.name, None)
                    APP.active = next(iter(APP.documents), None)
                history_error = str(exc)
            else:
                doc._undo.clear()
                doc.path, doc.dirty = None, False
                return {"model": doc.summary(), "geometry": "feature history rebuilt from the file (editable)",
                        "feature_history": {"features": report["features"],
                                            "volume_mm3": round(report["volume"], 6),
                                            "stored_volume_mm3": round(report["stored_volume"], 6),
                                            "not_rebuilt": report["skipped"],
                                            "imported_geometry": report.get("imported", [])},
                        "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        if ext == ".sldprt":
            doc = APP.new_document(p.stem)
            try:
                feat = _import_file(doc, str(p))
            except Exception as exc:  # unsupported geometry: report it
                APP.documents.pop(doc.name, None)
                APP.active = next(iter(APP.documents), None)
                native_error = str(exc)
            else:
                return {"model": doc.summary(), "geometry": "read natively from the embedded Parasolid data",
                        "notes": feat["params"].get("notes", []) + ([f"feature history not rebuilt: {history_error}"]
                                                                    if history_error else []),
                        "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        return {"solidworks_file": info, **({"geometry": f"not imported: {native_error}"} if native_error else {})}
    raise DocumentError(f"unsupported file type {ext}")


@command(category="document")
def save_part(path: str | None = None) -> dict:
    """Save the active document (part .oswprt or assembly .oswasm), optionally to a new path (Save As)."""
    d = APP.active_doc
    saved = d.save(path)
    if APP.active != d.name:
        APP.documents[d.name] = APP.documents.pop(APP.active)
        APP.active = d.name
    return {"saved": saved}


@command(category="document")
def save_drawing_document(path: str | None = None) -> dict:
    """Save the drawing as its own document (.oswdrw, like a SOLIDWORKS .slddrw) that refers to its part /
    assembly file by a relative path (save the model first); opening it opens the model with this drawing."""
    import os
    d = APP.active_doc
    if not getattr(d, "drawing", None):
        raise DocumentError("this document has no drawing yet (start one with drawing_new)")
    if not getattr(d, "path", None):
        raise DocumentError("save the part / assembly first: the drawing refers to its file")
    model = Path(d.path).resolve()
    dp = Path(path or getattr(d, "drawing_path", None) or model.with_suffix(".oswdrw")).resolve()
    try:
        ref = os.path.relpath(model, dp.parent)
    except ValueError:  # another drive: keep the full path
        ref = str(model)
    dp.write_text(json.dumps({"format": "openswx-drawing", "version": 1, "model": ref, "drawing": d.drawing}, indent=1),
                  encoding="utf-8")
    d.drawing_path = str(dp)
    return {"saved": str(dp), "model": str(model)}


@command(mutates=False, category="document")
def list_documents() -> dict:
    """List open documents and which one is active."""
    return {"active": APP.active, "documents": [{"name": n, "path": d.path, "dirty": d.dirty} for n, d in APP.documents.items()]}


@command(category="document")
def activate_document(name: str) -> dict:
    """Switch the active document."""
    if name not in APP.documents:
        raise DocumentError(f"no open document {name!r}")
    APP.active = name
    return APP.active_doc.summary()


@command(category="document")
def close_document(name: str | None = None) -> dict:
    """Close a document (default: the active one). Unsaved changes are discarded."""
    name = name or APP.active
    APP.documents.pop(name, None)
    APP.active = next(iter(APP.documents), None)
    return list_documents.__wrapped__()


def _open_sldasm_native(p: Path):
    """A SOLIDWORKS assembly as an openswx assembly without SOLIDWORKS: the component tree from the file's
    XML summary, each part's B-rep from its embedded Parasolid data (the component's configuration), placed
    at its saved position. Mates are not decoded, so every component comes in fixed."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    from .assembly import AssemblyDocument
    from .sldprt import assembly_tree
    tree = assembly_tree(str(p))
    asm = AssemblyDocument(p.stem)
    asm.live_docs = APP.documents
    parts, notes, placed = {}, [], {}
    for c in tree["components"]:
        if c["suppressed"]:
            continue
        if c["type"] != "PART" or not c["path"]:
            notes.append(f"{c['name']}: {'file not found: ' + str(c['stored_path']) if not c['path'] else 'not a part'}")
            continue
        key = (c["path"], c["configuration_id"])
        if key not in parts:
            part = PartDocument(Path(c["path"]).stem)
            try:
                _import_file(part, c["path"], c["configuration_id"])
            except Exception as exc:
                notes.append(f"{c['name']}: {exc}")
                parts[key] = None
                continue
            parts[key] = part
        if parts[key] is None:
            continue
        R = np.array(c["rotation"])
        comp = asm.insert(parts[key], None, [float(v) for v in c["translation"]], [0, 0, 0], True)
        comp.x[:3] = Rotation.from_matrix(R).as_rotvec()  # direct: Euler angles lose information at gimbal lock
        comp.name = c["name"]  # keep the SOLIDWORKS component name (mates refer to it)
        placed[c["name"]] = (key, R, np.array(c["translation"]))
    if not asm.components:
        raise DocumentError("no component could be read" + (f" ({'; '.join(notes[:3])})" if notes else ""))
    notes += _import_sldasm_mates(asm, p, placed)
    APP.documents[asm.name] = asm
    APP.active = asm.name
    return asm, notes


def _import_sldasm_mates(asm, p: Path, placed: dict) -> list[str]:
    """Rebuild the assembly's mates. A mated entity is named in the file by a face (its SOLIDWORKS face id:
    FACE_ID_2001 attributes in the part's Parasolid data, matched to the component's face by position), a
    component reference plane / origin, or nothing (the assembly's own planes / origin). Mates on edges and
    vertices name a face too, so the face's straight edges and vertices are candidates as well; the saved
    positions satisfy every mate, so the first candidate pair (in the order the file's entity hint suggests)
    on which the mate holds there is taken. Distance / angle values are measured there; for edges and vertices
    the value must be one of the numbers in the mate's record (several edges of a face can hold a distance).
    Mated components are then freed (all but the first) unless solving would move anything."""
    import itertools
    import math
    import numpy as np
    from .assembly import ASSEMBLY, _mate_residual, plane_ref
    from .geom import edge_ref, face_ref
    from .sldprt import assembly_mates, face_geometry, match_face_ref, part_face_ids
    try:
        mates = assembly_mates(str(p))
    except Exception as exc:
        return [f"mates not read: {exc}"]
    notes, ids_cache, done = [], {}, 0
    frame = asm.component(ASSEMBLY)
    SUPPORTED = ("coincident", "concentric", "parallel", "perpendicular", "distance", "angle", "tangent")

    def local_face(ref):
        key, R, t = placed[ref["component"]]
        if key not in ids_cache:
            ids_cache[key] = part_face_ids(*key)
        hit = match_face_ref(ids_cache[key], ref["numbers"])
        hit = face_geometry(hit) if hit is not None else None
        if hit is None:
            return None
        c0 = np.array(hit["center"])
        best, bd = None, 1e-3
        for f in asm._local_body(asm.component(ref["component"])).faces():
            fc = f.center()
            d = float(np.linalg.norm(np.array([fc.X, fc.Y, fc.Z]) - c0))
            if d < bd:
                best, bd = f, d
        return best

    def origin(comp):
        return ({"component": comp.name, "vertex": [0.0, 0.0, 0.0]}, (comp, "vertex", np.zeros(3), np.zeros(3), 0.0))

    def std_plane(comp, nm):
        pr = plane_ref(nm)
        if pr is None:
            return None
        return ({"component": comp.name, "plane": pr}, (comp, "plane", np.array(pr["point"], float), np.array(pr["normal"], float), 0.0))

    def candidates(ref):
        """(mate entity reference, entity as the solver sees it) for what a SOLIDWORKS reference may name."""
        name = ref["component"]
        if name not in placed:
            return []
        comp = asm.component(name)
        if ref["kind"] == "refgeom":
            nm = ref.get("name", "")
            if nm.lower().startswith("origin"):
                return [origin(comp)]
            got = std_plane(comp, nm)
            return [got] if got else []
        face = local_face(ref)
        if face is None:
            return []
        body = asm._local_body(comp)
        out = {"face": [], "line": [], "point": []}
        fref = {"component": name, "face": face_ref(face, body)}
        try:
            out["face"].append((fref, asm._resolve_entity(comp, fref)))
        except Exception:  # (a face that is neither planar nor cylindrical)
            pass
        seen = []
        for e in face.edges():
            if e.geom_type.name.lower() == "line":
                q, d = e.position_at(0.5), e.tangent_at(0.5)
                out["line"].append(({"component": name, "line": edge_ref(e, body)},
                                    (comp, "line", np.array([q.X, q.Y, q.Z]), np.array([d.X, d.Y, d.Z]), 0.0)))
            for v in e.vertices():
                pt = np.array([v.X, v.Y, v.Z])
                if not any(np.allclose(pt, s, atol=1e-9) for s in seen):
                    seen.append(pt)
                    out["point"].append(({"component": name, "vertex": [float(x) for x in pt]}, (comp, "vertex", pt, np.zeros(3), 0.0)))
        hint = ref.get("entity", "face")
        return [c for k in [hint] + [k for k in ("face", "line", "point") if k != hint] for c in out[k]]

    assembly_side = [std_plane(frame, n) for n in ("Front", "Top", "Right")] + [origin(frame)]

    def listed(v, numbers, scale):  # a measured value among the record's numbers
        return any(abs(abs(v) * scale - abs(n)) <= 1e-9 * max(1.0, abs(n)) for n in numbers)

    def fit(typ, ca, cb, numbers):
        """(type, value, aligned, entity a, entity b, value confirmed) when the mate holds, else None."""
        (ra, ea), (rb, eb) = ca, cb
        Pa, Na = ea[0].to_world(ea[2]), ea[0].dir_to_world(ea[3])
        Pb, Nb = eb[0].to_world(eb[2]), eb[0].dir_to_world(eb[3])
        faces_only = {ea[1], eb[1]} <= {"plane", "axis"}
        value, aligned, sure = None, bool(np.dot(Na, Nb) > 0), True
        if faces_only:
            if typ == "coincident" and ea[1] == eb[1] == "axis":
                typ = "concentric"  # coincident cylindrical faces: same axis (and radius)
            if typ in ("coincident", "distance", "parallel") and ea[1] == eb[1] == "plane" and abs(float(np.dot(Na, Nb))) < 1 - 1e-6:
                return None  # (non-parallel faces: the mate is on an edge or vertex of them)
            if typ == "distance":
                if ea[1] != "plane" or eb[1] != "plane":
                    return None
                # openswx measures the signed gap along entity1's normal; an aligned pair is swapped to keep it positive
                value = float(np.dot(Pb - Pa, Na))
                if value < 0 and aligned:
                    (ca, ea, Pa, Na), (cb, eb, Pb, Nb) = (cb, eb, Pb, Nb), (ca, ea, Pa, Na)
                    value = float(np.dot(Pb - Pa, Na))
                sure = not numbers or listed(value, numbers, 1e-3)
            elif typ == "angle":  # openswx compares cos(angle) with n1 . n2 (signed)
                if ea[1] != "plane" or eb[1] != "plane":
                    return None
                value = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(Na, Nb))))))
                sure = (not numbers or listed(math.radians(value), numbers, 1.0)
                        or listed(math.pi - math.radians(value), numbers, 1.0))
        else:
            if typ not in ("coincident", "distance", "parallel", "perpendicular"):
                return None
            if typ == "distance":
                try:
                    r = _mate_residual({"type": "distance", "value": 0.0}, ea, eb, ea[0].x, eb[0].x)
                except Exception:
                    return None
                value = float(r[-1])
                if abs(value) < 1e-9 or not listed(value, numbers, 1e-3):
                    return None  # (which edge / vertex is meant shows only through the value)
        mate = {"type": typ, "value": value, "aligned": aligned}
        try:
            if max(abs(v) for v in _mate_residual(mate, ea, eb, ea[0].x, eb[0].x)) > 1e-6:
                return None
        except Exception:
            return None
        return typ, value, aligned, ca, cb, sure

    mated, anchored = set(), set()
    expanded = []
    for m in mates:  # a mate on more than two faces (coincident / parallel / concentric): each with the first
        if m["type"] in ("coincident", "parallel", "concentric") and len(m["refs"]) > 2:
            expanded += [{**m, "name": f"{m['name']}.{k}" if k > 1 else m["name"], "refs": [m["refs"][0], r]}
                         for k, r in enumerate(m["refs"][1:], 1)]
        else:
            expanded.append(m)
    for m in expanded:
        typ = m["type"]
        if typ not in SUPPORTED or not 1 <= len(m["refs"]) <= 2:
            notes.append(f"mate {m['name']} ({m['class']}) not imported")
            continue
        sides = [candidates(r) for r in m["refs"]]
        if len(sides) == 1:  # the other entity is the assembly's own plane / origin (origin to origin first)
            sides.append(assembly_side[::-1] if sides[0][0][1][1] == "vertex" else assembly_side)
        if not sides[0] or not sides[1]:
            notes.append(f"mate {m['name']}: a mated face could not be identified")
            continue
        best = None
        for ca, cb in itertools.islice(itertools.product(*sides), 20000):
            got = fit(typ, ca, cb, m.get("numbers", []))
            if got and got[5]:
                best = got
                break
            best = best or got
        if best is None:
            if len(m["refs"]) == 1:
                anchored.add(m["refs"][0]["component"])
                notes.append(f"mate {m['name']}: anchors {m['refs'][0]['component']} to assembly reference geometry (kept fixed)")
            else:
                notes.append(f"mate {m['name']}: no reading of its entities holds at the saved position (not imported)")
            continue
        mtype, value, aligned, (a, ea), (b, eb), _ = best
        try:
            mate = asm.add_mate(mtype, a, b, value, aligned)
        except Exception as exc:
            notes.append(f"mate {m['name']}: {exc}")
            continue
        # the saved pose satisfies every SOLIDWORKS mate: one that does not hold there was misread
        ea, eb = asm._local_entity(a), asm._local_entity(b)
        try:
            bad = max(abs(v) for v in _mate_residual(mate, ea, eb, ea[0].x, eb[0].x)) > 1e-6
        except Exception:
            bad = True
        if bad:
            asm.mates.remove(mate)
            notes.append(f"mate {m['name']}: does not hold at the saved position (not imported)")
            continue
        if m["name"] and all(x["name"] != m["name"] for x in asm.mates):
            mate["name"] = m["name"]  # keep the SOLIDWORKS mate name
        mated.update((a["component"], b["component"]))
        done += 1
    mated.discard(ASSEMBLY)
    if done:
        before = {c.name: c.x.copy() for c in asm.components}
        ground = asm.components[0].name
        for c in asm.components:
            if c.name in mated and c.name != ground and c.name not in anchored:
                c.fixed = False
        try:
            asm.solve()

            def pose_change(c):  # compare poses, not rotation vectors (equivalent vectors differ near 180 deg)
                R0, t0 = c.matrix(before[c.name])
                R1, t1 = c.matrix()
                return max(float(np.abs(R1 - R0).max()) * 100.0, float(np.abs(t1 - t0).max()))
            moved = max(pose_change(c) for c in asm.components)
        except Exception:
            moved = float("inf")
        if moved > 1e-4:  # (mm) the imported mates do not hold the saved positions: keep everything fixed
            for c in asm.components:
                c.x[:] = before[c.name]
                c.fixed = True
            notes.append("mates imported, components kept fixed (solving them would move parts)")
    notes.insert(0, f"{done} of {len(expanded)} mates imported")
    return notes


def _recreate_drawing(drw: dict) -> dict:
    """An openswx drawing of the active model with the SOLIDWORKS drawing's sheets and views: model views in
    their orientations, views without one projected from the model view before them (top, then right, as a
    standard three-view), at a standard scale that fits the model on an A3 sheet. Views whose sheet position
    and scale the file gives are placed there (on the smallest sheet that holds them), projected views on the
    side of their parent they sit on."""
    from .drawing import SHEETS
    doc = APP.active_doc
    body = doc.body
    size = max(tuple(body.bounding_box().max - body.bounding_box().min)) if body is not None else 100.0
    fit = next((s for s in (10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01) if size * s <= 75), 0.01)  # room for 3 views
    by_sheet: dict = {}
    for v in drw["views"]:
        by_sheet.setdefault(v["sheet"], []).append(v)
    sheets = [s for s in drw["sheets"] if s in by_sheet] or drw["sheets"][:1]
    made, scale, notes, gdt = [], fit, 0, 0
    for i, name in enumerate(sheets):
        views = by_sheet.get(name, [])
        placed = [v["position"] for v in views if v.get("position")]
        fmt = drw.get("sheet_size") or next((f for f in ("A4", "A3", "A2", "A1", "A0") if all(
            x < SHEETS[f][0] - 10 and y < SHEETS[f][1] - 10 for x, y in placed)), "A0")
        height = SHEETS[fmt][1]
        scales = [v["scale"] for v in views if v.get("scale")]
        scale = max(set(scales), key=scales.count) if scales else fit
        if i == 0:
            drawing_new(sheet=fmt, scale=scale, standard_views=False)
            drawing_sheet_properties(name=name)
        else:
            drawing_add_sheet(size=fmt, name=name, scale=scale)
        parent, parent_at, sides, model_views, at_origin = None, None, [], 0, []
        for v in views:
            orient = v["orientation"] or (None if parent else "front")
            pos = (v["position"][0], height - v["position"][1]) if v.get("position") else None  # y down
            if not orient and pos and parent_at and min(abs(pos[0] - parent_at[0]), abs(pos[1] - parent_at[1])) > 1:
                orient = "isometric"  # not in line with the view before it: not a projection of it
            if orient:  # a model view: the first left of centre, others (often the isometric) top right
                x, y = pos or ((140.0, 170.0) if model_views == 0 else (330.0, 90.0 + 70.0 * (model_views - 1)))
                own = v.get("scale") if v.get("scale") and v["scale"] != scale else None
                parent = drawing_add_view(orient, x, y, scale=own)["view"]["id"]
                if pos:
                    at_origin.append((parent, pos))
                parent_at = pos
                model_views += 1
                sides = ["top", "right", "bottom", "left"]
                made.append({"sw": v["name"], "view": parent, "orientation": orient})
            elif sides:
                if pos and parent_at:  # the side it sits on, from the stored positions
                    dx, dy = pos[0] - parent_at[0], pos[1] - parent_at[1]
                    side = ("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("bottom" if dy > 0 else "top")
                    side = side if side in sides else sides[0]
                else:
                    side = sides[0]
                sides.remove(side)
                kw = {"x": pos[0], "y": pos[1]} if pos else {}
                pv = drawing_projected_view(parent, side, **kw)["view"]["id"]
                if pos:
                    at_origin.append((pv, pos))
                made.append({"sw": v["name"], "view": pv, "projected_from": parent})
            if v.get("dimensions") and made and made[-1]["sw"] == v["name"]:
                made[-1]["dimensions"] = _import_dimensions(made[-1]["view"], v["dimensions"])
        _origins_at(at_origin)
        for n in drw.get("notes", []):  # the sheet's notes, where SOLIDWORKS has them
            if n["sheet"] == name:
                drawing_add_note(n["text"], n["position"][0], height - n["position"][1], size=n.get("size", 3.5))
                notes += 1
        for g in drw.get("gdt", []):  # feature control frames
            if g["sheet"] == name:
                drawing_add_gdt(g["characteristic"], g["tolerance"], g["position"][0], height - g["position"][1],
                                datums=g["datums"], diameter=g["diameter"])
                gdt += 1
    return {"sheets": sheets, "views": made, "scale": scale, "notes": notes, "gdt": gdt}


def _origins_at(placed: list) -> None:
    """Place a sheet's views [(view, pos)] so the model origin is at pos (sheet mm, y down) - where SOLIDWORKS
    records a view - when that keeps every one of them on the sheet (an assembly's origin here need not be
    SOLIDWORKS'); else they stay centred there."""
    def f(st, DS):
        moves = []
        for view, pos in placed:
            v = DS.find_view(st, view)
            b, s = DS.view_geometry(APP.active_doc, st, v)["box"], DS.view_scale(st, v)
            x, y = pos[0] + (b[0] + b[2]) / 2 * s, pos[1] - (b[1] + b[3]) / 2 * s
            W, H = DS.D.SHEETS.get(DS.sheet_of(st, view).get("size", "A3"), DS.D.SHEETS["A3"])
            hw, hh = (b[2] - b[0]) * s / 2, (b[3] - b[1]) * s / 2
            if not (hw <= x <= W - hw and hh <= y <= H - hh):
                return
            moves.append((v, x, y))
        for v, x, y in moves:
            v["x"], v["y"] = x, y
    if placed:
        _views_edit(f)


def _import_dimensions(view: str, texts: list[str]) -> dict:
    """The SOLIDWORKS view's dimensions, by value, on what measures them in the recreated view."""
    done, missed = 0, []
    for text in texts:
        try:
            drawing_dimension_value(view, text)
            done += 1
        except (DocumentError, CommandError):
            missed.append(text)
    return {"placed": done, "not_found": missed}


def _read_iges(path: str):
    """IGES: the solids in the file, or - for surface-only IGES (the usual case) - its faces sewn into a solid."""
    from build123d import Compound, Shape, Solid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    r = IGESControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        raise DocumentError(f"cannot read IGES file {path}")
    r.TransferRoots()
    shape = Shape.cast(r.OneShape())
    if shape.solids():
        return shape, []
    sew = BRepBuilderAPI_Sewing(1e-3)
    for f in shape.faces():
        sew.Add(f.wrapped)
    sew.Perform()
    solids = []
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    while ex.More():
        s = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(ex.Current())).Solid())
        if s.is_valid and s.volume < 0:  # inside-out shell
            s = Solid(s.wrapped.Reversed())
        if s.is_valid and abs(s.volume) > 1e-9:
            solids.append(s)
        ex.Next()
    if not solids:
        raise DocumentError("the IGES surfaces do not close into a solid (import the faces as a surface instead)")
    return Compound(solids), [f"sewed {len(shape.faces())} IGES faces into {len(solids)} solid(s)"]


MESH_FACE_LIMIT = 200_000


def _read_mesh(path: str):
    """A triangle mesh (.stl, .3mf) as solid bodies: every closed, consistently oriented mesh becomes a solid
    whose faces are its triangles, with coplanar neighbours merged into one planar face where that works."""
    from build123d import Compound, Mesher
    try:
        shapes = Mesher().read(path)
    except Exception as exc:  # noqa: BLE001 - lib3mf reports unreadable files in its own ways
        raise DocumentError(f"cannot read the mesh: {exc}") from exc
    solids = [s for s in shapes if s.solids()]
    if not solids:
        raise DocumentError("the mesh is not a closed volume (open edges or inconsistent facets): no solid to import")
    notes = []
    if len(solids) < len(shapes):
        notes.append(f"{len(shapes) - len(solids)} open mesh(es) were left out (not closed volumes)")
    n = sum(len(s.faces()) for s in solids)
    if n > MESH_FACE_LIMIT:
        raise DocumentError(f"the mesh has {n} facets; over {MESH_FACE_LIMIT} is too many to edit as a solid "
                            "(decimate it first)")
    out = []
    for s in solids:
        try:
            c = s.clean()
            out.append(c if c.is_valid and abs(c.volume - s.volume) <= 1e-6 * max(1.0, abs(s.volume)) else s)
        except Exception:  # noqa: BLE001 - merging facets is a nicety: keep the triangles
            out.append(s)
    merged = sum(len(s.faces()) for s in out)
    notes.append(f"mesh of {n} facets imported as {len(out)} solid(s)" + (f", {merged} faces after merging coplanar facets" if merged < n else "")
                 + "; curved surfaces stay faceted")
    return (out[0] if len(out) == 1 else Compound(out)), notes


def _import_file(doc: PartDocument, path: str, config: int | None = None) -> dict:
    from build123d import import_brep, import_step
    p = Path(path)
    ext, notes = p.suffix.lower(), []
    if ext in (".x_t", ".x_b", ".xmt_txt", ".xmt_bin"):
        from .parasolid import read_xt_shape
        shape, notes = read_xt_shape(str(p))
    elif ext == ".sldprt":
        from .sldprt import read_sldprt
        shape, notes = read_sldprt(str(p), config)
    elif ext in (".igs", ".iges"):
        shape, notes = _read_iges(str(p))
    elif ext in (".stl", ".3mf"):
        shape, notes = _read_mesh(str(p))
    else:
        shape = import_step(str(p)) if ext in (".step", ".stp") else import_brep(str(p))
    if not shape.solids():
        raise DocumentError("file contains no solid bodies")
    params = {"brep": encode_shape(shape), "source": p.name}
    if notes:
        params["notes"] = notes
    return doc.add_feature("import", params)


@command(category="document")
def import_file(path: str) -> dict:
    """Import a STEP (.step/.stp), IGES, BREP, Parasolid (.x_t/.x_b), SOLIDWORKS part (.sldprt, read natively)
    or a closed triangle mesh (.stl, .3mf: becomes a faceted solid) into the active part as an Imported feature."""
    feat = _import_file(APP.doc, path)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(mutates=False, category="document")
def export_file(path: str, format: Literal["step", "stl", "brep", "x_t", "iges", "3mf", "gltf", "glb"] | None = None) -> dict:
    """Export the active part's (or assembly's) bodies to STEP, IGES (solids), STL, 3MF (3D printing), glTF /
    GLB (web / visualisation), BREP or Parasolid text .x_t (opens in SOLIDWORKS and other Parasolid-based
    CAD); the format is inferred from the extension."""
    from build123d import export_brep, export_gltf, export_step, export_stl
    body = APP.active_doc.body
    if body is None:
        raise DocumentError("nothing to export: the part has no body")
    fmt = (format or Path(path).suffix.lstrip(".")).lower().replace("stp", "step").replace("igs", "iges")
    if fmt == "step":
        export_step(body, path)
    elif fmt == "iges":
        from OCP.IGESControl import IGESControl_Writer
        w = IGESControl_Writer("MM", 1)  # 1: BRep solids (faces, shells and solids kept)
        w.AddShape(body.wrapped)
        w.ComputeModel()
        if not w.Write(str(path)):
            raise DocumentError("IGES export failed")
    elif fmt == "3mf":
        from build123d import Mesher
        m = Mesher()
        m.add_shape(body, linear_deflection=0.01, angular_deflection=0.1)
        m.write(str(path))
    elif fmt in ("gltf", "glb"):
        export_gltf(body, str(path), binary=fmt == "glb", linear_deflection=0.01, angular_deflection=0.1)
    elif fmt == "stl":
        export_stl(body, path, tolerance=0.01, angular_tolerance=0.1)
    elif fmt == "brep":
        export_brep(body, path)
    elif fmt in ("x_t", "xt", "xmt_txt"):
        from .parasolid_write import XTWriteError, write_xt
        try:
            write_xt(body, path)
        except XTWriteError as exc:
            raise DocumentError(f"Parasolid export: {exc}") from exc
        fmt = "x_t"
    else:
        raise DocumentError(f"unsupported export format {fmt!r}")
    return {"exported": str(Path(path).resolve()), "format": fmt}


# ====================================================================== queries
@command(mutates=False, category="query")
def get_model() -> dict:
    """Summary of the active document. Parts: FeatureManager tree (with status/errors), rollback, bodies,
    volume, configurations. Assemblies (kind='assembly'): components with positions/DOF and mates."""
    out = APP.active_doc.summary()
    if getattr(APP.active_doc, "drawing_path", None):
        out["drawing_document"] = APP.active_doc.drawing_path
    if getattr(APP.active_doc, "show_drawing", False):  # once, after a SOLIDWORKS drawing was opened
        out["show_drawing"] = True
        APP.active_doc.show_drawing = False
    ctx = APP.context
    if ctx and ctx["part"] == APP.active and ctx["assembly"] in APP.documents:
        out["in_context"] = {"assembly": ctx["assembly"], "component": ctx["component"]}
    return out


@command(mutates=False, category="query")
def get_feature(name: str) -> dict:
    """Full definition of one feature (parameters, rebuild state; sketches include geometry,
    relations, dimensions and solve status)."""
    return APP.doc.feature_detail(name)


@command(mutates=False, category="query")
def get_topology() -> dict:
    """List current faces and edges with ids (Face0.., Edge0..), type, a point on each, normals,
    areas/lengths and radii. Use the ids for fillet, chamfer, shell, hole, sketch-on-face, etc.
    Ids are only valid until the next model change; `name`, where present, stays valid across edits
    (a face is named after the feature and sketch entity that made it, an edge 'faceA|faceB' after the two
    faces it lies between) and is accepted wherever an id is. In assemblies ids are 'Component/Face#'."""
    return APP.active_doc.topology()


@command(mutates=False, category="query")
def get_mass_properties() -> dict:
    """Mass properties of the active part or assembly (mm, g): volume, surface area, mass, centre of mass,
    inertia tensor at the centre of mass, bounding box and topology counts."""
    d = APP.active_doc
    return mass_properties(d.body, getattr(d, "material", {"density": 1000.0})["density"])


def _eval_body():
    body = APP.active_doc.body
    if body is None:
        raise DocumentError("nothing to evaluate: the document has no body")
    return body


@command(mutates=False, category="evaluate")
def draft_analysis(direction: str | Vec3 = "Y", angle: float = 1.0) -> dict:
    """Evaluate > Draft Analysis: classify each face against the pull direction ('X'/'Y'/'Z', [x, y, z] or a
    plane name = its normal): positive / negative draft (>= angle deg), requires draft, or straddle."""
    from build123d import Vector
    from .evaluate import draft_analysis as da
    if isinstance(direction, str):
        d = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(direction.upper())
        if d is None:
            from .features import resolve_plane
            d = tuple(resolve_plane(APP.doc.ctx, direction).z_dir)
    else:
        d = tuple(direction)
    return da(_eval_body(), Vector(*d), angle)


def _direction(direction) -> tuple:
    if isinstance(direction, str):
        d = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(direction.upper())
        if d is None:
            from .features import resolve_plane
            d = tuple(resolve_plane(APP.doc.ctx, direction).z_dir)
        return d
    return tuple(direction)


@command(mutates=False, category="evaluate")
def thickness_analysis(target: float = 1.0) -> dict:
    """Evaluate > Thickness Analysis: the wall thickness through the material from every face; faces
    thinner than `target` mm are listed (min_thickness per face, the part's minimum)."""
    from .evaluate import thickness_analysis as ta
    return ta(_eval_body(), target)


@command(mutates=False, category="evaluate")
def undercut_analysis(direction: str | Vec3 = "Y") -> dict:
    """Evaluate > Undercut Detection: for a mold pulled along +/- direction ('X'/'Y'/'Z', [x, y, z] or a
    plane name), each face is direction1 / direction2 / side (free) or undercut (blocked by the part)."""
    from build123d import Vector
    from .evaluate import undercut_analysis as ua
    return ua(_eval_body(), Vector(*_direction(direction)))


@command(mutates=False, category="evaluate")
def clearance_verification(min_clearance: float = 1.0, components: list[str] | None = None) -> dict:
    """Evaluate > Clearance Verification (assemblies): the gap between every pair of components (or those
    listed); pairs closer than min_clearance mm - touching or interfering included - are reported."""
    from .evaluate import clearance_verification as cv
    shapes = [(c.name, s) for c, s in APP.asm.world_shapes() if not components or c.name in components]
    return cv(shapes, min_clearance)


@command(mutates=False, category="evaluate")
def curvature_analysis() -> dict:
    """Evaluate > Curvature: maximum principal curvature (1/mm) per tessellation vertex of each face."""
    from .evaluate import curvature_analysis as ca
    return ca(_eval_body())


@command(mutates=False, category="evaluate", mcp=False)
def section_view(plane: str = "Front", offset: float = 0.0, flip: bool = False) -> dict:
    """Evaluate > Section View: cut plane ('Front'/'Top'/'Right', a reference plane or 'FaceN') offset along
    its normal; returns the plane and the cross-section cap mesh and area."""
    from .evaluate import section_view as sv
    from .features import resolve_plane
    from build123d import Plane
    doc = APP.active_doc
    ctx = getattr(doc, "ctx", None)
    if ctx is None:
        from .features import Ctx
        ctx = Ctx()
    pl = resolve_plane(ctx, _plane_spec(doc, plane) if getattr(doc, "kind", "part") == "part" else plane)
    pl = Plane(origin=pl.origin + pl.z_dir * offset, x_dir=pl.x_dir, z_dir=pl.z_dir)
    return sv(_eval_body(), pl, flip)


@command(mutates=False, category="evaluate")
def section_properties(plane: str = "Front", offset: float = 0.0) -> dict:
    """Area of the body's cross-section on a plane (Section Properties), e.g. for a cut through a part."""
    r = section_view(plane, offset)
    return {"area": r["area"], "origin": r["origin"], "normal": r["normal"]}


@command(mutates=False, category="evaluate")
def check_geometry() -> dict:
    """Tools > Check: B-rep validity, open shells, very short edges, minimum edge length and radius."""
    from .evaluate import check_geometry as cg
    return cg(_eval_body())


@command(mutates=False, category="evaluate")
def body_interference() -> dict:
    """Interference between the solid bodies of a multibody part (pairs and overlap volume)."""
    from .evaluate import body_interference as bi
    return bi(_eval_body())


# ====================================================================== simulation (linear static)
def _sim_doc():
    return APP.active_doc


def _sim_face(sel):
    """Part: 'FaceN' -> face ref; assembly: 'Comp/FaceN' -> {component, face}."""
    d = _sim_doc()
    if getattr(d, "kind", "part") == "assembly":
        return sel if isinstance(sel, dict) else d.face_ref_for(sel)
    return _face(d, sel)


def _study_edit(fn):
    doc = _sim_doc()
    doc._checkpoint()
    st = doc.study
    out = fn(st)
    doc.revision += 1
    doc.dirty = True
    APP.sim_result = None
    return {"study": out if out is not None else st}


@command(category="simulation")
def simulation_add_fixture(face: str) -> dict:
    """Simulation > Fixture (Fixed Geometry): clamp a face ('FaceN') of the active part."""
    ref = _sim_face(face)
    return _study_edit(lambda st: st["fixtures"].append(ref))


@command(category="simulation")
def simulation_add_force(face: str, force: Vec3 | None = None, magnitude: float | None = None) -> dict:
    """Simulation > Force on a face: a total force vector [Fx, Fy, Fz] in N, or `magnitude` N normal to the
    face pushing into it. Spread uniformly over the face."""
    if force is None and magnitude is None:
        raise DocumentError("give a force vector or a magnitude")
    ref = _sim_face(face)
    return _study_edit(lambda st: st["loads"].append({"type": "force", "face": ref, "force": force, "magnitude": magnitude}))


@command(category="simulation")
def simulation_add_pressure(face: str, pressure: float) -> dict:
    """Simulation > Pressure on a face in MPa (N/mm^2), acting into the face."""
    ref = _sim_face(face)
    return _study_edit(lambda st: st["loads"].append({"type": "pressure", "face": ref, "pressure": pressure}))


@command(category="simulation")
def simulation_add_spring(face1: str, face2: str, normal_stiffness: float, shear_stiffness: float | None = None) -> dict:
    """Simulation > Connectors > Spring between two faces of separate bodies/components: total stiffness in
    N/mm along the line joining the faces (normal) and across it (shear, default = normal)."""
    r1, r2 = _sim_face(face1), _sim_face(face2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append(
        {"type": "spring", "a": r1, "b": r2, "kn": normal_stiffness, "kt": shear_stiffness if shear_stiffness is not None else normal_stiffness}))


@command(category="simulation")
def simulation_add_contact(face1: str, face2: str) -> dict:
    """Simulation > Contact > No Penetration (frictionless) between facing faces of separate bodies or
    components: they may separate but not pass through each other (iterative, compression only)."""
    r1, r2 = _sim_face(face1), _sim_face(face2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append({"type": "contact", "a": r1, "b": r2}))


@command(category="simulation")
def simulation_add_bolt(hole1: str, hole2: str, diameter: float | None = None, preload: float = 0.0,
                        bolt_material: str = "AISI 1020") -> dict:
    """Simulation > Connectors > Bolt between two coaxial hole faces (cylindrical 'FaceN') of separate
    bodies/components: axial stiffness E A / L and shear G A / L of a bolt of `diameter` (default the hole
    diameter) over the grip length L between the holes, optional preload (N, clamping)."""
    from .materials import lookup
    m = lookup(bolt_material)
    if not m:
        raise DocumentError(f"unknown bolt material {bolt_material!r}")
    r1, r2 = _sim_face(hole1), _sim_face(hole2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append(
        {"type": "bolt", "a": r1, "b": r2, "diameter": diameter, "preload": preload, "E": m["elastic_modulus"],
         "nu": m["poisson"]}))


@command(category="simulation")
def simulation_clear(fixtures: bool = True, loads: bool = True) -> dict:
    """Remove the study's fixtures and/or loads."""
    def f(st):
        if fixtures:
            st["fixtures"] = []
        if loads:
            st["loads"] = []
            st["connectors"] = []
    return _study_edit(f)


def _resolve_study(doc):
    from .geom import face_normal, resolve_face
    body = doc.body
    if getattr(doc, "kind", "part") == "assembly":
        def rf(r):
            c = doc.component(r["component"])
            return resolve_face(doc._local_body(c), r["face"]).moved(c.location())
    else:
        rf = lambda r: resolve_face(body, r)  # noqa: E731
    fixtures = [rf(r) for r in doc.study["fixtures"]]
    loads = []
    for ld in doc.study["loads"]:
        face = rf(ld["face"])
        n = face_normal(face, face.center())
        if ld["type"] == "pressure":
            vecf = [-c * ld["pressure"] * face.area for c in n]
        elif ld.get("force") is not None:
            vecf = list(ld["force"])
        else:
            vecf = [-c * ld["magnitude"] for c in n]
        loads.append((face, vecf))
    connectors = []
    for c in doc.study.get("connectors", []):
        fa, fb = rf(c["a"]), rf(c["b"])
        if c["type"] == "contact":
            axis = face_normal(fa, fa.center())  # from face a towards face b
            if (fb.center() - fa.center()).dot(axis) < 0:
                axis = -axis
            connectors.append({"a": fa, "b": fb, "contact": True, "axis": list(axis)})
        elif c["type"] == "spring":
            axis = fb.center() - fa.center()
            connectors.append({"a": fa, "b": fb, "kn": c["kn"], "kt": c["kt"],
                               "axis": list(axis.normalized()) if axis.length > 1e-9 else None})
        else:  # bolt between coaxial holes
            circ = [e for e in fa.edges() if e.geom_type.name.lower() == "circle"]
            if not circ:
                raise DocumentError("bolt connectors need cylindrical hole faces")
            axis = circ[0].normal().normalized()
            L = abs((fb.center() - fa.center()).dot(axis)) or 1.0
            d = c.get("diameter") or 2 * circ[0].radius
            A = math.pi * d * d / 4
            G = c["E"] / (2 * (1 + c["nu"]))
            connectors.append({"a": fa, "b": fb, "kn": c["E"] * A / L, "kt": G * A / L, "axis": list(axis),
                               "preload": c.get("preload", 0.0)})
    return fixtures, loads, connectors


@command(mutates=False, category="simulation")
def simulation_run(element_size: float | None = None) -> dict:
    """Run the linear static study: voxel hex mesh (incompatible-mode elements), returns max displacement
    (mm), max von Mises stress (MPa), factor of safety (material yield / max stress), reactions (N). Smaller
    element_size = more accurate and slower (default: ~12k elements)."""
    from .fea import solve_static
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    fixtures, loads, connectors = _resolve_study(doc)
    material, regions = getattr(doc, "material", None), None
    if getattr(doc, "kind", "part") == "assembly":  # bonded components, each with its own part material
        mats = []
        for c, shape in doc.world_shapes():
            m = getattr(c.doc(doc.live_docs), "material", {}) or {}
            if not m.get("elastic_modulus"):
                raise DocumentError(f"assign a library material to {c.name}'s part first")
            mats.append((c, shape, m))
        material = dict(mats[0][2])
        material["yield_strength"] = min(m.get("yield_strength") or m.get("tensile_strength") or 1e18 for _, _, m in mats)
        regions = [(sol, m["elastic_modulus"] / material["elastic_modulus"]) for _, shape, m in mats for sol in shape.solids()]
    try:
        with unlocked():
            r = solve_static(doc.body, material, fixtures, loads, element_size or doc.study.get("element_size"),
                             regions=regions, connectors=connectors)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.sim_result = (doc.revision, r)
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    if regions:
        out["bonded_components"] = len(regions)
    return out


@command(mutates=False, category="simulation", mcp=False)
def simulation_results() -> dict:
    """Per-vertex von Mises stress and displacement on the display mesh for the last simulation_run (UI plot)."""
    from .fea import map_to_mesh
    cached = getattr(APP, "sim_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the study first (the model changed since the last run)")
    r = cached[1]
    from .fea import refine_mesh
    mesh = refine_mesh(_sim_doc().mesh(), 2 * r["element_size"])
    for f in mesh["faces"]:
        f["v"] = [round(c, 4) for c in f["v"]]
    out = map_to_mesh(r, mesh)
    out["faces"] = mesh["faces"]  # the refined display mesh the per-vertex values refer to
    out.update({k: v for k, v in r.items() if not k.startswith("_")})
    return out


@command(mutates=False, category="simulation")
def frequency_run(modes: int = 5, element_size: float | None = None) -> dict:
    """Frequency study: the first `modes` natural frequencies (Hz) of the part / bonded assembly with the
    study's fixtures (material density and modulus from the library)."""
    from .fea import solve_modal
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    fixtures, _loads, _con = _resolve_study(doc)
    material, regions = getattr(doc, "material", None), None
    if getattr(doc, "kind", "part") == "assembly":
        mats = [(shape, c.doc(doc.live_docs).material) for c, shape in doc.world_shapes()]
        material = dict(mats[0][1])
        regions = [(sol, m["elastic_modulus"] / material["elastic_modulus"], m["density"] / material["density"])
                   for shape, m in mats for sol in shape.solids()]
    try:
        r = solve_modal(doc.body, material, fixtures, modes, element_size or doc.study.get("element_size"), regions=regions)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.modal_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def frequency_mode_shape(mode: int = 1) -> dict:
    """Normalised mode shape (per display-mesh vertex displacement) of the last frequency_run (UI plot)."""
    from .fea import map_to_mesh, refine_mesh
    cached = getattr(APP, "modal_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the frequency study first")
    r = cached[1]
    if not 1 <= mode <= len(r["frequencies_hz"]):
        raise DocumentError(f"mode must be 1..{len(r['frequencies_hz'])}")
    g = dict(r["_grid"], U=r["_grid"]["shapes"][mode - 1])
    mesh = refine_mesh(_sim_doc().mesh(), 2 * r["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "displacement": out["displacement"], "frequency_hz": r["frequencies_hz"][mode - 1],
            "mode": mode}


@command(mutates=False, category="simulation")
def buckling_run(modes: int = 3, element_size: float | None = None) -> dict:
    """Buckling study (linear): load factors for the study's fixtures and loads; critical load = factor x the
    applied loads (a factor below 1 means the part buckles under them)."""
    from .fea import solve_buckling
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    fixtures, loads, _con = _resolve_study(doc)
    try:
        r = solve_buckling(doc.body, doc.material, fixtures, loads, modes, element_size or doc.study.get("element_size"))
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.buckling_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def buckling_mode_shape(mode: int = 1) -> dict:
    """Normalised buckling mode shape per display-mesh vertex of the last buckling_run (UI plot)."""
    from .fea import map_to_mesh, refine_mesh
    cached = getattr(APP, "buckling_result", None)
    if not cached or cached[0] != APP.doc.revision:
        raise DocumentError("run the buckling study first")
    r = cached[1]
    if not 1 <= mode <= len(r["load_factors"]):
        raise DocumentError(f"mode must be 1..{len(r['load_factors'])}")
    g = dict(r["_grid"], U=r["_grid"]["shapes"][mode - 1])
    mesh = refine_mesh(APP.doc.mesh(), 2 * r["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "displacement": out["displacement"], "load_factor": r["load_factors"][mode - 1],
            "mode": mode}


def _thermal(doc) -> dict:
    return doc.study.setdefault("thermal", {"temperatures": [], "heat": [], "convection": []})


@command(category="simulation")
def thermal_add_temperature(face: str, temperature: float) -> dict:
    """Thermal study: hold a face ('FaceN') at a fixed temperature (degC)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["temperatures"].append({"face": ref, "value": temperature}))


@command(category="simulation")
def thermal_add_heat_power(face: str, watts: float) -> dict:
    """Thermal study: heat power (W) entering the part through a face (negative = removed)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["heat"].append({"face": ref, "value": watts}))


@command(category="simulation")
def thermal_add_convection(face: str, coefficient: float, ambient: float = 20.0) -> dict:
    """Thermal study: convection on a face, film coefficient in W/(m^2 K) to an ambient temperature (degC)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["convection"].append({"face": ref, "h": coefficient, "ambient": ambient}))


@command(category="simulation")
def thermal_clear() -> dict:
    """Remove all thermal loads and boundary conditions."""
    return _study_edit(lambda st: st.__setitem__("thermal", {"temperatures": [], "heat": [], "convection": []}))


@command(mutates=False, category="simulation")
def thermal_run(element_size: float | None = None, transient: bool = False, duration: float = 60.0,
                steps: int = 50, initial_temperature: float = 20.0) -> dict:
    """Run the thermal study (finite-cell voxel mesh, material thermal conductivity). Steady state by default:
    returns min/max temperature (degC), the heat (W) into the part through each
    temperature boundary (heat_in_per_temperature_boundary, in the order added; negative = out) and their net sum.
    transient=True: the part starts at initial_temperature (degC) and is integrated over `duration` seconds in
    `steps` implicit steps using the material density and specific heat; returns the min/max/mean
    temperature history and the final field."""
    from .fea import solve_thermal, solve_thermal_transient
    from .geom import resolve_face
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    th = _thermal(doc)
    rf = lambda r: resolve_face(doc.body, r)  # noqa: E731
    loads = ([(rf(x["face"]), x["value"]) for x in th["temperatures"]],
             [(rf(x["face"]), x["value"]) for x in th["heat"]],
             [(rf(x["face"]), x["h"], x["ambient"]) for x in th["convection"]])
    try:
        if transient:
            from .materials import lookup
            mat = doc.material
            cp = mat.get("specific_heat") or (lookup(mat.get("name", "")) or {}).get("specific_heat")
            r = solve_thermal_transient(doc.body, mat.get("thermal_conductivity"), mat.get("density"), cp, *loads,
                                        initial=initial_temperature, duration=duration, steps=steps,
                                        element_size=element_size)
        else:
            r = solve_thermal(doc.body, doc.material.get("thermal_conductivity"), *loads, element_size)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.thermal_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def thermal_results() -> dict:
    """Temperature per display-mesh vertex for the last thermal_run (UI plot)."""
    from .fea import map_temperature, refine_mesh
    cached = getattr(APP, "thermal_result", None)
    if not cached or cached[0] != APP.doc.revision:
        raise DocumentError("run the thermal study first (the model changed since the last run)")
    r = cached[1]
    mesh = refine_mesh(APP.doc.mesh(), 2 * r["element_size"])
    out = map_temperature(r, mesh)
    out["faces"] = mesh["faces"]
    out.update({k: v for k, v in r.items() if not k.startswith("_")})
    return out


@command(mutates=False, category="query")
def list_end_conditions() -> dict:
    """End conditions accepted by extrude/cut/hole, with their SOLIDWORKS swEndConditions_e values."""
    from .features import SUPPORTED_END_CONDITIONS
    return {k: {"sw_value": v, "supported": k in SUPPORTED_END_CONDITIONS} for k, v in END_CONDITIONS.items()}


@command(mutates=False, category="query", mcp=False)
def get_mesh() -> dict:
    """The active document's display mesh for the viewport: triangles per face (with face ids), edge polylines,
    sketch, plane and reference-geometry overlays."""
    m = APP.active_doc.mesh()
    ctx = APP.context
    if ctx and ctx["part"] == APP.active and ctx["assembly"] in APP.documents:
        m["context"] = _context_meshes(APP.documents[ctx["assembly"]], ctx["component"])
    return m


def _context_meshes(asm, component: str) -> list[dict]:
    """The assembly's other components, placed in the edited component's frame (shown ghosted)."""
    from .geom import tessellate
    comp = asm.component(component)
    inv = comp.location().inverse()
    out = []
    for c, shape in asm.world_shapes():
        if c.name == component:
            continue
        for f in tessellate(shape.moved(inv))["faces"]:
            out.append({"v": f["v"], "t": f["t"], "component": c.name})
    return out


@command(mutates=False, category="query")
def get_activity(limit: int = 30) -> dict:
    """Recent command log (from the UI and from agents)."""
    return {"version": APP.version, "log": APP.log[-limit:]}


@command(mutates=False, category="query")
def measure(a: str, b: str) -> dict:
    """Minimum distance between two selections (Face#/Edge# ids)."""
    body = APP.doc.body
    if body is None:
        raise DocumentError("the part has no body")

    def pick(s):
        from .geom import resolve_edge, resolve_face
        if _is_edge_sel(s):
            return resolve_edge(body, _edge(APP.doc, s))
        if _is_face_sel(s):
            return resolve_face(body, _face(APP.doc, s))
        raise DocumentError(f"cannot measure {s!r}")
    sa, sb = pick(a), pick(b)
    return {"distance": sa.distance_to(sb), "a": a, "b": b}


def _measure_item(item):
    """A selection to measure: [x, y, z] (a vertex), Face#/Edge# of the part, or Component/Face# in an
    assembly. Returns (shape or None, info dict)."""
    from build123d import Vertex as _Vx
    from .geom import geom_name
    if isinstance(item, (list, tuple)):
        p = [float(v) for v in item]
        return _Vx(*p), {"kind": "vertex", "id": "Vertex", "point": p}
    doc = APP.active_doc
    if getattr(doc, "kind", "part") == "assembly" or "/" in item:
        comp, _, sub = item.partition("/")
        shapes = dict((c.name, sh) for c, sh in APP.asm.world_shapes())
        if comp not in shapes:
            raise DocumentError(f"no component {comp!r}")
        body, label = shapes[comp], item
    else:
        body, sub, label = doc.body, item, item
        if body is None:
            raise DocumentError("the part has no body")
    try:
        k = int(sub[4:])
        shape = body.faces()[k] if sub.startswith("Face") else body.edges()[k] if sub.startswith("Edge") else None
    except (ValueError, IndexError):
        shape = None
    if shape is None:
        raise DocumentError(f"cannot measure {item!r}")
    info = {"kind": "face" if sub.startswith("Face") else "edge", "id": label, "type": geom_name(shape)}
    if info["kind"] == "face":
        info["area"] = shape.area
        if info["type"] == "plane":
            info["normal"] = list(shape.normal_at())
        if info["type"] == "cylinder":
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            cyl = BRepAdaptor_Surface(shape.wrapped).Cylinder()
            ax = cyl.Axis()
            info["radius"] = cyl.Radius(); info["diameter"] = 2 * cyl.Radius()
            info["axis"] = [ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]
            info["center"] = [ax.Location().X(), ax.Location().Y(), ax.Location().Z()]
    else:
        info["length"] = shape.length
        if info["type"] == "circle":
            info["radius"] = shape.radius; info["diameter"] = 2 * shape.radius
            info["center"] = list(shape.arc_center)
        if info["type"] == "line":
            d = shape.position_at(1) - shape.position_at(0)
            info["direction"] = list(d.normalized())
        info["start"], info["end"] = list(shape.position_at(0)), list(shape.position_at(1))
    return shape, info


@command(category="view")
def save_named_view(name: str, quaternion: list[float], target: Vec3, height: float, perspective: bool = False) -> dict:
    """Orientation dialog New View: save the current view (camera orientation quaternion [x, y, z, w], the
    point it looks at and the height of the view in mm) under a name, kept with the document."""
    doc = APP.active_doc
    if not name.strip():
        raise DocumentError("give the view a name")
    if name.strip().lower() in ("front", "back", "left", "right", "top", "bottom", "isometric", "trimetric", "dimetric"):
        raise DocumentError(f"{name!r} is a standard view")
    doc._checkpoint()
    views = getattr(doc, "named_views", None)
    if views is None:
        views = doc.named_views = {}
    views[name.strip()] = {"q": [float(v) for v in quaternion], "t": [float(v) for v in target], "h": float(height),
                           "perspective": bool(perspective)}
    doc.dirty = True
    return {"named_views": views}


@command(category="view")
def delete_named_view(name: str) -> dict:
    """Delete a saved view."""
    doc = APP.active_doc
    views = getattr(doc, "named_views", {}) or {}
    if name not in views:
        raise DocumentError(f"no saved view {name!r}")
    doc._checkpoint()
    del views[name]
    doc.dirty = True
    return {"named_views": views}


@command(mutates=False, category="query")
def face_features() -> dict:
    """Which feature made each face of the part: {"Face3": "Boss-Extrude1", ...} (assemblies: empty)."""
    doc = APP.active_doc
    return {"faces": doc.face_features() if hasattr(doc, "face_features") else {}}


@command(mutates=False, category="query")
def measure_items(items: list[str | Vec3]) -> dict:
    """Measure, as SOLIDWORKS' Measure tool: one item gives its size (face area, radius/diameter, edge length,
    vertex XYZ); two give the minimum distance, the centre distance of circles/cylinders, dX/dY/dZ and the
    angle between planar faces or straight edges. Items: Face#/Edge# ids (Component/Face# in an assembly) or
    [x, y, z] vertices."""
    import numpy as _np
    got = [_measure_item(i) for i in items]
    out = {"items": [g[1] for g in got]}
    if len(got) == 2:
        (sa, ia), (sb, ib) = got
        out["distance"] = sa.distance_to(sb)
        ref = lambda i: i.get("center") or i.get("point")
        pa, pb = ref(ia), ref(ib)
        if pa and pb:
            d = _np.array(pb) - _np.array(pa)
            out["center_distance" if ("center" in ia or "center" in ib) else "point_distance"] = float(_np.linalg.norm(d))
            out["dx"], out["dy"], out["dz"] = (float(v) for v in d)
        da = ia.get("normal") or ia.get("direction") or ia.get("axis")
        db = ib.get("normal") or ib.get("direction") or ib.get("axis")
        if da and db:
            c = abs(float(_np.dot(da, db)) / (_np.linalg.norm(da) * _np.linalg.norm(db)))
            out["angle"] = math.degrees(math.acos(min(1.0, c)))
            if c > 1 - 1e-9 and "normal" in ia and "normal" in ib:  # parallel planes: their separation
                out["normal_distance"] = abs(float(_np.dot(_np.array(list(sb.center())) - _np.array(list(sa.center())), da)))
    return out


# ====================================================================== tree editing
@command(category="edit")
def undo() -> dict:
    """Undo the last change to the active document."""
    APP.active_doc.undo(); return APP.active_doc.summary()


@command(category="edit")
def redo() -> dict:
    """Redo the last undone change."""
    APP.active_doc.redo(); return APP.active_doc.summary()


@command(category="edit")
def rebuild() -> dict:
    """Force a full rebuild (Ctrl+Q)."""
    return {"rebuild": APP.doc.rebuild(), "model": APP.doc.summary()}


@command(category="edit")
def edit_feature(name: str, params: dict) -> dict:
    """Change parameters of an existing feature (merged into its current parameters), e.g.
    edit_feature('Boss-Extrude1', {'depth': 25}). The model rebuilds; invalid edits are rejected."""
    APP.doc.edit_feature(name, params)
    return APP.doc.summary()


@command(category="edit", mcp=False)
def redefine_feature(name: str, command_name: str, args: dict) -> dict:
    """Re-define a feature using the creation command's arguments (used by the UI's Edit Feature).
    The command runs on a scratch copy to translate selections into parameters."""
    doc = APP.doc
    target = doc.find(name)
    scratch = PartDocument(doc.name)
    scratch.load_dict(doc.to_dict())
    # selections resolve against the model as it was before this feature (e.g. Delete Face's faces are gone after it)
    idx = scratch.index(name)
    if scratch.rollback is None or scratch.rollback > idx:
        scratch.rollback = idx
        scratch.rebuild()
    key = APP.active
    APP.documents[key] = scratch
    _TL.preview = True
    try:
        COMMANDS[command_name]["fn"](**args)
        new_params = scratch.features[-1 if scratch.rollback is None else scratch.rollback - 1]["params"]
    finally:
        _TL.preview = False
        APP.documents[key] = doc
    if target["type"] == "sketch":
        raise DocumentError("use the sketch tools to edit a sketch")
    doc.edit_feature(name, new_params, replace=True)
    return doc.summary()


@command(category="edit")
def delete_feature(name: str, delete_absorbed_sketch: bool = True) -> dict:
    """Delete a feature (and its absorbed sketch, as SOLIDWORKS offers)."""
    removed = APP.doc.delete_feature(name, delete_absorbed_sketch)
    return {"removed": removed, "model": APP.doc.summary()}


@command(mutates=False, category="configurations")
def get_design_table() -> dict:
    """The part's design table: columns (dimension names such as 'D1@Sketch1' / 'Boss-Extrude1.depth', or
    '$STATE@Feature') and a row per configuration."""
    return APP.doc.get_design_table()


@command(category="configurations")
def apply_design_table(columns: list[str], rows: list[dict]) -> dict:
    """Design Table: rows [{'name': configuration, 'values': [cell per column]}] create or update
    configurations; '$STATE@Feature' cells are 'S' (suppressed) or 'U'; empty cells keep the base value."""
    made = APP.doc.apply_design_table(columns, rows)
    return {"created": made, "model": APP.doc.summary()}


@command(category="configurations")
def delete_design_table() -> dict:
    """Delete the design table; the configurations it made stay."""
    APP.doc.delete_design_table()
    return APP.doc.summary()


@command(category="edit")
def reorder_feature(name: str, after: str | None = None) -> dict:
    """Move a feature (with the sketches it consumes) in the FeatureManager to just after `after` (None: the
    top). Refused when the feature would come before something it references, or vice versa."""
    APP.doc.reorder(name, after)
    return APP.doc.summary()


@command(category="edit")
def add_folder(name: str, features: list[str]) -> dict:
    """FeatureManager Add to New Folder: group the given features under a folder (display only)."""
    APP.doc.add_folder(name, features)
    return APP.doc.summary()


@command(category="edit")
def remove_folder(name: str) -> dict:
    """Dissolve a FeatureManager folder; its features stay in the tree."""
    APP.doc.remove_folder(name)
    return APP.doc.summary()


@command(category="edit")
def rename_feature(name: str, new_name: str) -> dict:
    """Rename a feature; references from other features are updated."""
    APP.doc.rename(name, new_name)
    return APP.doc.summary()


@command(category="edit")
def suppress_feature(name: str, suppressed: bool = True, configuration: str | None = None) -> dict:
    """Suppress (or unsuppress) a feature in a configuration (default: the active one)."""
    APP.doc.set_suppressed(name, suppressed, configuration)
    return APP.doc.summary()


# ====================================================================== equations / configurations
@command(category="equations")
def set_global_variable(name: str, expression: str | float) -> dict:
    """Create or change a global variable (Equations > Global Variables), e.g. ('width', 120) or
    ('half', '"width"/2'). Use it in equations as "width"."""
    APP.doc.set_variable(name, expression)
    return APP.doc.equations()


@command(category="equations")
def delete_global_variable(name: str) -> dict:
    """Delete a global variable."""
    APP.doc.delete_variable(name)
    return APP.doc.equations()


@command(category="equations")
def set_equation(target: str, expression: str) -> dict:
    """Drive a dimension with an equation (SOLIDWORKS syntax): target 'D1@Sketch1' (sketch dimension) or
    'D1@Boss-Extrude1' (feature dimension: extrude D1=depth, revolve D1=angle, fillet D1=radius, shell
    D1=thickness, hole D1=diameter/D2=depth, pattern D1=spacing/D2=count, plane D1=offset). Expression may
    use global variables and other dimensions in quotes, + - * / ^, sin/cos/tan (degrees), sqrt, min, max."""
    APP.doc.set_equation(target, expression)
    return APP.doc.equations()


@command(category="equations")
def clear_equation(target: str) -> dict:
    """Remove the equation driving a dimension (it keeps its current value)."""
    APP.doc.clear_equation(target)
    return APP.doc.equations()


@command(mutates=False, category="equations")
def get_equations() -> dict:
    """List global variables, equations and the current value of every named dimension (D#@Feature)."""
    return APP.doc.equations()


@command(category="configurations")
def add_configuration(name: str, copy_from: str | None = None) -> dict:
    """Add a configuration (copying suppression states/overrides from `copy_from`, default the active one)
    and make it active."""
    APP.doc.add_configuration(name, copy_from)
    return APP.doc.summary()


@command(category="configurations")
def activate_configuration(name: str) -> dict:
    """Show a configuration (ConfigurationManager double-click)."""
    APP.doc.activate_configuration(name)
    return APP.doc.summary()


@command(category="configurations")
def delete_configuration(name: str) -> dict:
    """Delete a configuration (not Default)."""
    APP.doc.delete_configuration(name)
    return APP.doc.summary()


@command(category="configurations")
def set_configuration_value(configuration: str, target: str, value: float | str) -> dict:
    """Override a dimension in one configuration, e.g. ('Long', 'D1@Boss-Extrude1', 80)."""
    APP.doc.set_configuration_value(configuration, target, value)
    return APP.doc.summary()


# ====================================================================== drawings
@command(mutates=False, category="drawing")
def create_drawing(path: str | None = None, sheet: SheetSize = "A3",
                   projection: Literal["third", "first"] = "third", scale: float | None = None) -> dict:
    """Make Drawing from Part/Assembly: Front, Top, Right (third angle) or Left (first angle) and Isometric
    views with hidden lines, overall dimensions and a title block, as an SVG sheet. Writes the SVG to `path`
    if given; scale is chosen automatically unless given (e.g. 0.5 for 1:2)."""
    from .drawing import make_drawing
    d = make_drawing(APP.active_doc, sheet, projection, scale)
    if path:
        p = Path(path)
        if p.suffix.lower() != ".svg":
            p = p.with_suffix(".svg")
        p.write_text(d["svg"], encoding="utf-8")
        d["path"] = str(p.resolve())
    return {k: v for k, v in d.items() if k != "svg"} | ({"svg_length": len(d["svg"])} if path else {"svg": d["svg"]})


def _drawing_state(d=None) -> dict:
    d = d or APP.active_doc
    if not hasattr(d, "drawing") or d.drawing is None:
        d.drawing = {}
    return d.drawing


def _drawing_edit(fn, annotations: bool = False):
    """Run fn on the drawing state (annotations: on the active sheet's annotation lists when the drawing has
    sheets, else on the single automatic sheet)."""
    d = APP.active_doc
    d._checkpoint()
    st = _drawing_state(d)
    if annotations and st.get("sheets"):
        from .drawing_sheets import active_sheet
        st = active_sheet(st)
    try:
        out = fn(st)
    except ValueError as exc:  # (drawing errors: no such view, a cut that misses the part, ...)
        raise DocumentError(str(exc)) from exc
    d.revision += 1
    d.dirty = True
    return out


def _next_ann(st: dict, prefix: str) -> str:
    sheets = APP.active_doc.drawing.get("sheets") or [st]
    used = {a["id"] for sh in sheets for k in ("notes", "datums", "gdt", "finishes", "welds") for a in sh.get(k, [])}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


@command(category="drawing")
def drawing_settings(sheet: SheetSize | None = None, projection: Literal["third", "first"] | None = None,
                     scale: float | None = None, model_items: bool | None = None) -> dict:
    """Sheet properties for the active document's drawing: sheet size, projection, scale (e.g. 0.5 = 1:2;
    0 = automatic) and Model Items (show the driving sketch dimensions in the views)."""
    def f(st):
        if st.get("sheets"):  # sheet properties of the active sheet; Model Items on all of its views
            from .drawing_sheets import active_sheet
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
    their projection line, as in SOLIDWORKS."""
    def f(st):
        if st.get("sheets"):
            from .drawing_sheets import find_view
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


# ---- drawing documents: sheets and placed views (SOLIDWORKS drawing workflow) -------------------------
def _views_edit(fn):
    """fn(st, module) on a drawing that has sheets; returns its result."""
    from . import drawing_sheets as DS

    def g(st):
        if not st.get("sheets"):
            raise DocumentError("this drawing has no sheets yet: start one with drawing_new")
        return fn(st, DS)
    return _drawing_edit(g)


def _beside(DS, st, parent: dict, view: dict) -> str:
    """The side of the parent a derived view goes on: the way its arrows point, as SOLIDWORKS places it."""
    fr, pfr = DS.frame(st, view), DS.frame(st, parent)
    lx, ly = DS.to2d(-fr[0], pfr)
    return ("right" if lx > 0 else "left") if abs(lx) >= abs(ly) else ("top" if ly > 0 else "bottom")


@command(category="drawing")
def drawing_new(sheet: SheetSize = "A3", projection: Literal["third", "first"] = "third",
                scale: float | None = None, standard_views: bool = True) -> dict:
    """New Drawing of the active part or assembly: one sheet; with standard_views, Front, Top, the side view
    (Right in third angle, Left in first) and an Isometric, scaled to fit (scale e.g. 0.5 = 1:2). Replaces the
    document's automatic drawing; views are then added, moved and edited with the drawing_* commands."""
    from .drawing_sheets import new_drawing
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


_PICK_MM = 3.0  # how near (sheet mm) a pick must be to a vertex or circle


@command(mutates=False, category="drawing")
def drawing_pick(view: str, p: Vec2) -> dict:
    """What a click at p (sheet mm) in a view is on: the snapped vertex and / or the circle (centre, radius),
    in the view's 2D model coordinates and on the sheet (mm) - for Smart Dimension."""
    from . import drawing_sheets as DS
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


def _line_dimension(st, DS, v, sh, g, did, k, q1, p2, tol, offset, text, at) -> dict:
    """Angle between two straight edges, or the distance between two parallel ones."""
    import numpy as np
    if p2 is None:
        raise DocumentError("pick two straight edges")
    l1 = DS.pick_line(g, q1, tol)
    l2 = DS.pick_line(g, DS.sheet_to_view(st, APP.active_doc, v, p2), tol)
    if l1 is None or l2 is None:
        raise DocumentError("pick points on two straight edges of the view")
    if l1 == l2:
        raise DocumentError("pick two different edges")
    d = {"id": did, "view": v["id"], "kind": k, "l1": [list(l1[0]), list(l1[1])], "l2": [list(l2[0]), list(l2[1])], "offset": offset}
    crossing = DS.lines_angle(l1, l2, q1)
    if k == "angle":
        if crossing is None:
            raise DocumentError("the edges are parallel: dimension the distance between them (line_distance)")
        if at is not None:
            d["at"] = list(DS.sheet_to_view(st, APP.active_doc, v, at))
        else:  # the angle between the edges as picked: each ray from the crossing towards its pick
            apex = crossing[0]
            q2 = DS.sheet_to_view(st, APP.active_doc, v, p2)
            rays = []
            for line, q in ((l1, q1), (l2, q2)):
                u = np.asarray(line[1], float) - np.asarray(line[0], float)
                u = u / max(1e-12, float(np.linalg.norm(u)))
                rays.append(u if float((np.asarray(q, float) - apex) @ u) >= 0 else -u)
            mid = rays[0] + rays[1]
            mid = mid / max(1e-9, float(np.linalg.norm(mid)))
            d["at"] = [float(c) for c in apex + mid * (offset + 10) / DS.view_scale(st, v)]
    elif crossing is not None:
        raise DocumentError("the edges are not parallel: dimension the angle between them")
    if text:
        d["text"] = text
    sh.setdefault("dims", []).append(d)
    return {**d, "value": DS.dim_value(d)}


def _hole_callout(st, DS, v, sh, g, did, q1, tol, offset, text, angle) -> dict:
    """Hole Callout: the hole's diameter and THRU or its depth, on a leader."""
    c = DS.pick_circle(g, q1, tol)
    if c is None:
        raise DocumentError("no hole seen end-on near that point")
    holes = DS.view_holes(APP.active_doc, st, v)
    hole = min(holes, key=lambda h: math.dist(h["c"], c[0])) if holes else None
    if hole is not None and math.dist(hole["c"], c[0]) < 1e-3 and abs(hole["d"] - 2 * c[1]) < 1e-3:
        callout = "⌀" + DS.D._fmt(hole["d"]) + " " + ("THRU" if hole["depth"] is None else "↧" + DS.D._fmt(hole["depth"]))
    else:
        callout = "⌀" + DS.D._fmt(2 * c[1])
    ang = angle if angle is not None else math.degrees(math.atan2(q1[1] - c[0][1], q1[0] - c[0][0]))
    d = {"id": did, "view": v["id"], "kind": "callout", "c": list(c[0]), "r": c[1], "angle": ang, "offset": offset,
         "callout": callout}
    if text:
        d["text"] = text
    sh.setdefault("dims", []).append(d)
    return {**d, "value": DS.dim_value(d)}


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


def _new_balloon(st, sh, view, q, x, y, component, text=None) -> dict:
    used = {b["id"] for s in st["sheets"] for b in s.get("balloons", [])}
    bid = next(f"Balloon{i}" for i in range(1, 9999) if f"Balloon{i}" not in used)
    b = {"id": bid, "view": view, "p": [float(q[0]), float(q[1])], "x": float(x), "y": float(y), "component": component}
    if text:
        b["text"] = text
    sh.setdefault("balloons", []).append(b)
    return b


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
                        from . import tolerance as T
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
    from .drawing_sheets import bom_rows, view_scale

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
    from .drawing import make_drawing
    from .drawing_export import svg_to_dxf as _svg_to_dxf, svg_to_pdf

    def svg_to_dxf(svg, out):  # DXF, or DWG converted from it
        if fmt != "dwg":
            return _svg_to_dxf(svg, out)
        from .dxf_io import dxf_to_dwg
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
        from .drawing_export import svgs_to_pdf
        from .drawing_sheets import render_sheet
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


# ====================================================================== assemblies
def _asm_ref(sel):
    return sel if isinstance(sel, dict) else APP.asm.face_ref_for(sel)


@command(category="assembly")
def new_assembly(name: str | None = None) -> dict:
    """Create a new assembly document (File > New > Assembly) and make it active."""
    from .assembly import AssemblyDocument
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
        add_mate(type, entity1, entity2, value, aligned, entity3, entity4, value2)
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
    as a video (.avi, MJPEG, like SOLIDWORKS) or an animated .gif (fixed camera). The assembly is unchanged."""
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


@command(mutates=False, category="document")
def export_bom(path: str) -> dict:
    """Export the active assembly's bill of materials as CSV (item, part number, description, quantity)."""
    import csv
    rows = assembly_bom()["bom"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ITEM NO.", "PART NUMBER", "DESCRIPTION", "QTY."])
        for r in rows:
            w.writerow([r["item"], r["part_number"], r["description"], r["qty"]])
    return {"path": path, "rows": len(rows)}


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


_BOLT_LENGTHS = [6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120, 130, 140, 150]


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


@command(category="edit")
def rollback(to_feature: str | None = None) -> dict:
    """Move the rollback bar to just below `to_feature`; omit to roll forward to the end."""
    APP.doc.rollback_to(to_feature)
    return APP.doc.summary()


@command(category="edit")
def set_material(name: str, density_kg_m3: float | None = None, properties: dict | None = None) -> dict:
    """Assign a material. Library names (see list_materials, e.g. 'AISI 1020', '6061-T6 (SS)', 'ABS') bring
    their density, elastic modulus, Poisson ratio, strengths and colour; any other name needs a density.
    properties overrides any of: elastic_modulus, poisson, yield_strength, tensile_strength (MPa),
    thermal_conductivity, specific_heat, color."""
    APP.doc.set_material(name, density_kg_m3, properties)
    return APP.doc.summary()


@command(category="document")
def save_custom_material(name: str, properties: dict) -> dict:
    """Save a material to the user's Custom Materials library (kept across documents): density (kg/m^3)
    is required; elastic_modulus, poisson, yield_strength, tensile_strength, thermal_conductivity,
    specific_heat, color optional."""
    from .materials import save_custom_material as _save
    try:
        return {"material": _save(name, properties)}
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(category="document")
def delete_custom_material(name: str) -> dict:
    """Remove a material from the Custom Materials library."""
    from .materials import delete_custom_material as _del
    try:
        _del(name)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    return {"deleted": name}


@command(mutates=False, category="query")
def list_materials(category: str | None = None) -> dict:
    """The material library: density (kg/m^3), elastic modulus/yield/tensile strength (MPa), Poisson ratio,
    thermal conductivity (W/m K) and default colour, grouped by category."""
    from .materials import library
    lib = library()
    return {n: m for n, m in lib.items() if category is None or m["category"].lower() == category.lower()}


@command(category="edit")
def set_appearance(color: str | None, target: Literal["part", "feature", "face"] = "part",
                   feature: str | None = None, face: str | None = None) -> dict:
    """Appearance: colour ('#rrggbb', or null to clear) the whole part, a feature's faces (`feature`) or one
    face (`face` 'FaceN'). Face colours beat feature colours, which beat the part colour."""
    doc = APP.doc
    ref = feature if target == "feature" else _face(doc, face) if target == "face" and face else None
    if target != "part" and ref is None:
        raise DocumentError(f"give the {target} to colour")
    doc.set_appearance(color, target, ref)
    return doc.summary()


@command(category="edit")
def set_custom_property(name: str, value: str | None) -> dict:
    """File > Properties: set a custom property (e.g. 'Description', 'PartNo', 'Author'); value null deletes
    it. Drawings use Description/PartNo/Author in the title block and BOM."""
    doc = APP.doc
    doc._checkpoint()
    if value is None:
        doc.properties.pop(name, None)
    else:
        doc.properties[name] = str(value)
    doc.revision += 1
    return {"properties": doc.properties}


@command(category="edit")
def set_units(units: Literal["mm", "cm", "m", "in", "ft"]) -> dict:
    """Document units (display in the UI and drawings). The API always takes and returns millimetres."""
    doc = APP.doc
    doc._checkpoint()
    doc.units = units
    doc.revision += 1
    return doc.summary()


# ====================================================================== sketching
@command(category="sketch")
def create_sketch(plane: str | dict = "Front", name: str | None = None) -> dict:
    """Insert a new sketch on 'Front', 'Top', 'Right', a reference plane name (e.g. 'Plane1') or a
    planar face id ('Face5'). Returns the sketch name; add geometry with the sketch_* tools."""
    feat = APP.doc.add_feature("sketch", {"plane": _plane_spec(APP.doc, plane), "sketch": {}}, name=name)
    return {"sketch": feat["name"], "model": APP.doc.summary()}


def _sk(sketch: str, fn):
    return APP.doc.edit_sketch(sketch, fn)


@command(category="sketch")
def edit_sketch_plane(sketch: str, plane: str | dict) -> dict:
    """Edit Sketch Plane: move a sketch to another plane ('Front', 'Plane1') or planar face ('Face5'); its
    geometry keeps its sketch coordinates and the features built on it rebuild."""
    doc = APP.doc
    if doc.find(sketch)["type"] != "sketch":
        raise DocumentError(f"{sketch} is not a sketch")
    doc.edit_feature(sketch, {"plane": _plane_spec(doc, plane)})
    return doc.summary()


@command(mutates=False, category="query")
def parent_child(name: str) -> dict:
    """Parent/Child relationships of a feature: the features it references (parents) and the features that
    reference it (children), as in the SOLIDWORKS Parent/Child dialog."""
    doc = APP.doc
    feat = doc.find(name)
    names = {f["name"] for f in doc.features}

    def refs(v) -> set[str]:
        if isinstance(v, str):
            base = v.split("/")[0]
            return {base} if base in names else set()
        if isinstance(v, dict):
            return set().union(*(refs(x) for x in v.values())) if v else set()
        if isinstance(v, (list, tuple)):
            return set().union(*(refs(x) for x in v)) if v else set()
        return set()
    parents = sorted(refs(feat["params"]) - {feat["name"]}, key=doc.index)
    children = [f["name"] for f in doc.features if f is not feat and feat["name"] in refs(f["params"])]
    return {"feature": feat["name"], "parents": parents, "children": children}


@command(category="sketch")
def sketch_line(sketch: str, start: Vec2 | str, end: Vec2 | str, construction: bool = False) -> dict:
    """Add a line. Endpoints are [x, y] in sketch coordinates (mm) or existing point ids (e.g. 'P3',
    'ORIGIN') to connect to them. Returns the entity id and its point ids."""
    def f(sk):
        eid = sk.add_line(start, end, construction)
        return {"entity": eid, "points": [sk.entities[eid]["p1"], sk.entities[eid]["p2"]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_centerline(sketch: str, start: Vec2 | str, end: Vec2 | str) -> dict:
    """Add a construction centerline (used as the default revolve axis or for symmetry)."""
    def f(sk):
        eid = sk.add_centerline(start, end)
        return {"entity": eid, "points": [sk.entities[eid]["p1"], sk.entities[eid]["p2"]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_rectangle(sketch: str, corner1: Vec2, corner2: Vec2, centered: bool = False) -> dict:
    """Add a corner rectangle between two corners, or a center rectangle when centered=True
    (corner1 = centre, corner2 = a corner). Horizontal/vertical relations are added automatically."""
    def f(sk):
        lines = sk.add_center_rectangle(corner1, corner2) if centered else sk.add_rectangle(corner1, corner2)
        return {"entities": lines, "points": [sk.entities[line]["p1"] for line in lines[:4]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_circle(sketch: str, center: Vec2 | str, radius: float, construction: bool = False) -> dict:
    """Add a circle by centre ([x, y] or point id) and radius."""
    def f(sk):
        eid = sk.add_circle(center, radius, construction)
        return {"entity": eid, "center": sk.entities[eid]["c"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_arc(sketch: str, center: Vec2 | str, start: Vec2 | str, end: Vec2 | str) -> dict:
    """Add a centre-point arc, counter-clockwise from start to end."""
    def f(sk):
        eid = sk.add_arc(center, start, end)
        e = sk.entities[eid]
        return {"entity": eid, "points": {"center": e["c"], "start": e["s"], "end": e["e"]}}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_three_point_arc(sketch: str, start: Vec2 | str, end: Vec2 | str, mid: Vec2) -> dict:
    """Add an arc through start, a point on the arc (mid) and end."""
    def f(sk):
        eid = sk.add_three_point_arc(start, end, mid)
        e = sk.entities[eid]
        return {"entity": eid, "points": {"center": e["c"], "start": e["s"], "end": e["e"]}}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_polygon(sketch: str, center: Vec2, radius: float, sides: int = 6, angle: float = 90.0) -> dict:
    """Add a regular polygon inscribed in a construction circle (like the SOLIDWORKS Polygon tool)."""
    return _sk(sketch, lambda sk: {"entities": sk.add_polygon(center, radius, sides, angle)})


@command(category="sketch")
def sketch_slot(sketch: str, center1: Vec2, center2: Vec2, width: float) -> dict:
    """Add a straight slot between two arc centres with the given width."""
    return _sk(sketch, lambda sk: {"entities": sk.add_slot(center1, center2, width)})


@command(category="sketch")
def sketch_point(sketch: str, position: Vec2) -> dict:
    """Add a sketch point (e.g. hole centres)."""
    def f(sk):
        eid = sk.add_point(position)
        return {"entity": eid, "point": sk.entities[eid]["p"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_relation(sketch: str, relation: Literal["coincident", "horizontal", "vertical", "parallel", "perpendicular", "collinear", "equal", "tangent", "concentric", "midpoint", "fix", "symmetric"], refs: list[str]) -> dict:
    """Add a geometric relation between sketch items (entity ids like 'Line3', point ids like 'P2',
    or 'ORIGIN'). symmetric takes [point, point, line]."""
    return _sk(sketch, lambda sk: {"relation": sk.add_relation(relation, refs)})


@command(category="sketch")
def sketch_dimension(sketch: str, kind: Literal["distance", "hdistance", "vdistance", "length", "radius", "diameter", "angle"], refs: list[str], value: float | None = None,
                     position: Vec2 | None = None, supplement: bool = False) -> dict:
    """Add a driving dimension (Smart Dimension). Units mm/degrees. Omit value to keep the current
    size. If it would over-define the sketch it is added as a driven (reference) dimension.
    position: where the label is placed (sketch [x, y]); supplement: for an angle, dimension the other
    side of the lines (180 minus the angle between their directions)."""
    def f(sk):
        d = sk.add_dimension(kind, refs, value, position, supplement)
        return {"dimension": f"{d['id']}@{sketch}", "value": d["value"], "driven": d["driven"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_move_dimension(sketch: str, dimension: str, position: Vec2) -> dict:
    """Move a sketch dimension's label to position (sketch [x, y]); the geometry does not change."""
    _, did = _dim_id(dimension)
    return _sk(sketch, lambda sk: sk.move_dimension(did, position))


@command(category="sketch")
def set_dimension(dimension: str, value: float, sketch: str | None = None,
                  configurations: str | list[str] | None = None) -> dict:
    """Change a dimension value, e.g. set_dimension('D1@Sketch1', 42). Rebuilds the model. configurations
    (as the SOLIDWORKS Modify box): None or 'all' sets it in all configurations (the base value; overrides
    of it are removed), 'this' only in the active configuration, or a list of configuration names."""
    sk_name, did = _dim_id(dimension)
    sk_name = sk_name or sketch
    if not sk_name:
        raise DocumentError("give the dimension as 'D1@Sketch1' or pass sketch=")
    doc = APP.doc
    target = f"{did}@{sk_name}"
    if configurations in (None, "all"):
        out = _sk(sk_name, lambda sk: sk.set_dimension(did, value))
        key = doc._config_key(target)
        if any(key in c.get("values", {}) for c in doc.configurations.values()):
            for c in doc.configurations.values():
                c.get("values", {}).pop(key, None)
            doc.rebuild()
        return out
    names = [doc.active_config] if configurations == "this" else list(configurations)
    for n in names:
        doc.set_configuration_value(n, target, value)
    return {"configurations": names, "model": doc.summary()}


@command(category="sketch")
def sketch_delete(sketch: str, item: str) -> dict:
    """Delete a sketch entity, relation (R#) or dimension (D#)."""
    return _sk(sketch, lambda sk: sk.delete(item))


@command(category="sketch")
def sketch_ellipse(sketch: str, center: Vec2 | str, major_end: Vec2 | str, minor_radius: float,
                   construction: bool = False) -> dict:
    """Add an ellipse: centre, one end of the major axis ([x, y] or point ids) and the minor radius."""
    return _sk(sketch, lambda sk: {"entity": sk.add_ellipse(center, major_end, minor_radius, construction)})


@command(category="sketch")
def sketch_conic(sketch: str, start: Vec2 | str, end: Vec2 | str, apex: Vec2 | str, rho: float = 0.5,
                 construction: bool = False) -> dict:
    """Add a conic from start to end, tangent at both ends to the lines towards `apex` ([x, y] or point ids);
    rho 0..1 (0.5 parabola, less an ellipse arc, more a hyperbola)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_conic(start, end, apex, rho, construction)})


@command(category="sketch")
def sketch_spline(sketch: str, points: list[Vec2 | str], closed: bool = False, construction: bool = False) -> dict:
    """Add an interpolating spline through `points` ([x, y] or point ids); closed=True makes it periodic."""
    def f(sk):
        eid = sk.add_spline(points, closed, construction)
        return {"entity": eid, "points": sk.entities[eid]["pts"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_equation_curve(sketch: str, x: str, y: str, t_start: float = 0.0, t_end: float = 1.0, points: int = 25,
                          closed: bool = False, construction: bool = False) -> dict:
    """Equation Driven Curve (parametric): a spline through `points` samples of x(t), y(t) for t from t_start
    to t_end. The expressions use `t`, the part's global variables, + - * / ^ and the equation functions
    (sin, cos, tan take degrees, like every openswx equation; sqr, abs, min, max; pi). Examples: an involute
    x='10*(cos(t)+t*pi/180*sin(t))', y='10*(sin(t)-t*pi/180*cos(t))'; a parabola x='t', y='t^2/20' (the
    explicit form y = f(x) is x='t'). closed=True joins the end to the start."""
    from .equations import EquationError, Evaluator
    if points < 3 or points > 500:
        raise DocumentError("points must be between 3 and 500")
    if t_end == t_start:
        raise DocumentError("t_start and t_end must differ")
    ev = Evaluator(APP.doc.variables)
    pts = []
    n = points if not closed else points + 1
    for i in range(points):
        ev.globals["t"] = t_start + (t_end - t_start) * i / (n - 1)
        try:
            px, py = float(ev.eval(x)), float(ev.eval(y))
        except (EquationError, ZeroDivisionError, ValueError, OverflowError) as exc:
            raise DocumentError(f"cannot evaluate the curve at t = {ev.globals['t']:g}: {exc}") from exc
        if not (math.isfinite(px) and math.isfinite(py)):
            raise DocumentError(f"the curve is not finite at t = {ev.globals['t']:g}")
        if pts and math.dist(pts[-1], (px, py)) < 1e-9:
            raise DocumentError(f"the curve does not move at t = {ev.globals['t']:g} (two samples coincide)")
        pts.append([px, py])

    def f(sk):
        eid = sk.add_spline(pts, closed, construction)
        return {"entity": eid, "points": sk.entities[eid]["pts"], "samples": len(pts)}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_text(sketch: str, position: Vec2, text: str, height: float = 10.0, font: str = "Arial",
                angle: float = 0.0) -> dict:
    """Sketch Text: letter outlines at `position` (baseline start), cap height `height` mm, rotated `angle`
    degrees. The closed letter contours extrude/cut like any profile (counters become holes)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_text(position, text, height, font, angle)})


@command(category="sketch")
def sketch_linear_pattern(sketch: str, entities: list[str], spacing: float, count: int, angle: float = 0.0,
                          spacing2: float = 0.0, count2: int = 1, angle2: float = 90.0) -> dict:
    """Linear Sketch Pattern: `count` instances `spacing` apart along `angle` degrees (sketch X = 0), with
    an optional second direction (`spacing2`, `count2`, `angle2`)."""
    return _sk(sketch, lambda sk: {"entities": sk.linear_pattern_entities(entities, spacing, count, angle,
                                                                          spacing2, count2, angle2)})


@command(category="sketch")
def sketch_circular_pattern(sketch: str, entities: list[str], center: Vec2 | str, count: int,
                            angle: float = 360.0) -> dict:
    """Circular Sketch Pattern: `count` instances about `center` ([x, y] or point id) over `angle` degrees."""
    return _sk(sketch, lambda sk: {"entities": sk.circular_pattern_entities(entities, center, count, angle)})


def _add_projected(sk, plane, edges, body=None) -> list[str]:
    """Add 3D edges to a sketch, projected onto its plane, as fixed lines/circles/arcs/splines. With the body,
    each keeps a reference to its model edge ("ext"): if a change removes that edge the entity is dangling."""
    import math as _m
    pts: dict[tuple, str] = {}

    def loc(v):
        q = plane.to_local_coords(v)
        return [q.X, q.Y]

    def pid(v):
        x, y = loc(v)
        key = (round(x, 6), round(y, 6))
        if key not in pts:
            pts[key] = sk.point_id([x, y])
            sk.add_relation("fix", [pts[key]])
        return pts[key]

    created = []
    for e in edges:
        kind = e.geom_type.name.lower()
        normal_ok = kind == "circle" and abs(abs(e.normal().dot(plane.z_dir)) - 1) < 1e-6
        if kind == "line":
            a, b = pid(e.position_at(0)), pid(e.position_at(1))
            if a != b:
                created.append(sk.add_line(a, b))
        elif normal_ok and e.is_closed:
            c = sk.point_id(loc(e.arc_center))
            sk.add_relation("fix", [c])
            eid = sk.add_circle(c, e.radius)
            sk.add_relation("fix", [eid])
            created.append(eid)
        elif normal_ok:
            c = sk.point_id(loc(e.arc_center))
            sk.add_relation("fix", [c])
            s0, s1 = pid(e.position_at(0)), pid(e.position_at(1))
            # arcs are CCW about the sketch normal: swap ends if the edge runs clockwise
            mid = loc(e.position_at(0.5))
            cc, a0 = sk.coords(c), sk.coords(s0)
            a1 = sk.coords(s1)
            ang = lambda q: _m.atan2(q[1] - cc[1], q[0] - cc[0])  # noqa: E731
            ccw = (ang(mid) - ang(a0)) % (2 * _m.pi) < (ang(a1) - ang(a0)) % (2 * _m.pi)
            created.append(sk.add_arc(c, s0, s1) if ccw else sk.add_arc(c, s1, s0))
        else:  # ellipses, B-splines, out-of-plane circles: spline through samples
            n = 16
            samp = [pid(e.position_at(0))] + [sk.point_id(loc(e.position_at(k / n))) for k in range(1, n)] \
                + [pid(e.position_at(1))]
            closed = samp[0] == samp[-1]
            eid = sk.add_spline(samp[:-1] if closed else samp, closed=closed)
            for q in sk.entities[eid]["pts"]:
                sk.add_relation("fix", [q])
            created.append(eid)
        if body is not None and created and "ext" not in sk.entities[created[-1]]:
            sk.entities[created[-1]]["ext"] = edge_ref(e)
    return created


@command(category="sketch")
def sketch_convert_entities(sketch: str, faces: list[str] | None = None, edges: list[str] | None = None) -> dict:
    """Convert Entities: project model edges (`edges` 'EdgeN') and/or the boundary edges of `faces`
    ('FaceN') onto the sketch plane as fixed sketch geometry."""
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body to convert edges from")
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    plane = doc.ctx.sketches[sketch][1]
    from .geom import resolve_edge, resolve_face
    picked = []
    for f in faces or []:
        picked += resolve_face(doc.body, _face(doc, f)).edges()
    for e in edges or []:
        picked.append(resolve_edge(doc.body, _edge(doc, e)))
    if not picked:
        raise DocumentError("select faces or edges to convert")
    return _sk(sketch, lambda sk: {"entities": _add_projected(sk, plane, picked, doc.body)})


@command(category="sketch")
def sketch_intersection_curve(sketch: str) -> dict:
    """Intersection Curve: add the section of the part body with the sketch plane as fixed sketch geometry."""
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body to intersect")
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    plane = doc.ctx.sketches[sketch][1]
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from build123d import Compound as _C
    o, n = plane.origin, plane.z_dir
    sec = BRepAlgoAPI_Section(doc.body.wrapped, gp_Pln(gp_Pnt(o.X, o.Y, o.Z), gp_Dir(n.X, n.Y, n.Z)))
    sec.Build()
    edges = _C(sec.Shape()).edges()
    if not edges:
        raise DocumentError("the sketch plane does not cut the body")
    return _sk(sketch, lambda sk: {"entities": _add_projected(sk, plane, list(edges))})


@command(category="sketch")
def import_dxf(path: str, sketch: str | None = None, plane: str = "Front", scale: float = 1.0,
               offset: Vec2 | None = None) -> dict:
    """File > Open DXF into a sketch: LINE / CIRCLE / ARC / (LW)POLYLINE with bulges / SPLINE / ELLIPSE / POINT
    from model space, into `sketch` (or a new sketch on `plane`), scaled and offset (mm)."""
    from .dxf_io import dwg_to_dxf, import_into_sketch
    if not Path(path).exists():
        raise FileNotFoundError(path)
    if Path(path).suffix.lower() == ".dwg":  # closed format: convert through the ODA File Converter
        import tempfile
        path = dwg_to_dxf(path, tempfile.mkdtemp())
    name = sketch or create_sketch(plane)["sketch"]
    return _sk(name, lambda sk: {"sketch": name, "entities": import_into_sketch(sk, path, scale, tuple(offset or (0, 0)))})


@command(mutates=False, category="sketch")
def export_sketch_dxf(sketch: str, path: str, include_construction: bool = False) -> dict:
    """Save a sketch as DXF (local sketch XY, mm); a .dwg path converts through the ODA File Converter."""
    from .dxf_io import dxf_to_dwg, export_sketch
    doc = APP.doc
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    want_dwg = Path(path).suffix.lower() == ".dwg"
    p = Path(path).with_suffix(".dxf")
    export_sketch(doc.ctx.sketches[sketch][0], str(p), include_construction)
    if want_dwg:
        dwg = dxf_to_dwg(str(p), str(p.with_suffix(".dwg")))
        p.unlink(missing_ok=True)
        return {"path": str(Path(dwg).resolve())}
    return {"path": str(p.resolve())}


@command(mutates=False, category="sheet_metal")
def export_flat_pattern_dxf(path: str) -> dict:
    """Sheet metal > Export flat pattern to DXF: the largest planar face of the flattened part (with its
    holes) as 2D geometry, for laser / waterjet cutting. The part is returned to its formed state."""
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
        face = max(faces, key=lambda f: f.area)
        p = Path(path).with_suffix(".dxf")
        export_face_outline(face, str(p))
        bb = face.bounding_box()
        return {"path": str(p.resolve()), "area": face.area, "size": [bb.size.X, bb.size.Y, bb.size.Z]}
    finally:
        if not was:
            doc.flattened = False
            doc.rebuild()


@command(category="sketch")
def sketch_mirror(sketch: str, entities: list[str], about: str, copy: bool = True) -> dict:
    """Mirror Entities: copy entities across a sketch line/centerline (`about`), adding symmetric relations.
    copy=False removes the originals (SOLIDWORKS' Copy option off)."""
    def f(sk):
        new = sk.mirror_entities(entities, about)
        if not copy:
            sk.remove_entities(entities)
        return {"entities": new}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_offset(sketch: str, entities: list[str], distance: float, reverse: bool = False,
                  bidirectional: bool = False) -> dict:
    """Offset Entities: circles/arcs (concentric) or a connected chain of lines; positive distance offsets a
    closed loop outward, negative inward (reverse flips the side). Adds parallel/concentric relations and
    driving distances. bidirectional offsets to both sides."""
    d = -distance if reverse else distance
    def f(sk):
        out = sk.offset_entities(entities, d)
        if bidirectional:
            out += sk.offset_entities(entities, -d)
        return {"entities": out}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_fillet(sketch: str, point: str, radius: float) -> dict:
    """Sketch Fillet: round the corner at `point` (shared by two lines) with a tangent arc of `radius`."""
    return _sk(sketch, lambda sk: {"entity": sk.fillet_corner(point, radius)})


@command(category="sketch")
def sketch_circle_3pt(sketch: str, p1: Vec2 | str, p2: Vec2 | str, p3: Vec2 | str, construction: bool = False) -> dict:
    """Perimeter Circle: the circle through three points ([x, y] or point ids)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_circle_3pt(p1, p2, p3, construction)})


@command(category="sketch")
def sketch_tangent_arc(sketch: str, start: str, end: Vec2 | str, construction: bool = False) -> dict:
    """Tangent Arc: from `start` (a point id at the end of a line or arc) to `end`, leaving tangent to that
    line / arc (a tangent relation is added)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_tangent_arc(start, end, construction)})


@command(category="sketch")
def sketch_transform(sketch: str, entities: list[str], move: Vec2 | None = None, rotate: float = 0.0,
                     center: Vec2 | str | None = None, scale: float = 1.0, copy: bool = False) -> dict:
    """Move / Copy / Rotate / Scale Entities: scale by `scale` and turn by `rotate` degrees about `center`
    ([x, y] or a point id; default the origin), then shift by `move` [dx, dy]. copy=True keeps the originals
    and returns the new entities."""
    return _sk(sketch, lambda sk: {"entities": sk.transform_entities(entities, move or (0.0, 0.0), rotate,
                                                                      center if center is not None else (0.0, 0.0), scale, copy)})


@command(category="sketch")
def sketch_split(sketch: str, entity: str, at: Vec2) -> dict:
    """Split Entities: cut a line or arc in two at the point `at`."""
    return _sk(sketch, lambda sk: {"entities": sk.split_entity(entity, at)})


@command(category="sketch")
def sketch_chamfer(sketch: str, point: str, distance: float, distance2: float | None = None,
                   angle: float | None = None) -> dict:
    """Sketch Chamfer: cut the corner at `point` (shared by two lines) with a line `distance` back along each
    line (distance2: a different distance on the second; or angle: degrees from the first line)."""
    return _sk(sketch, lambda sk: {"entity": sk.chamfer_corner(point, distance, distance2, angle)})


@command(category="sketch")
def sketch_trim(sketch: str, entity: str, pick: Vec2) -> dict:
    """Trim Entities (power trim): remove the portion of `entity` around the point `pick` [x, y] up to the
    nearest intersections with other sketch entities (deletes it if nothing crosses it)."""
    return _sk(sketch, lambda sk: {"entities": sk.trim(entity, pick)})


@command(category="sketch")
def sketch_extend(sketch: str, entity: str, pick: Vec2) -> dict:
    """Extend Entities: lengthen the end of line `entity` nearest `pick` to the next entity it meets."""
    return _sk(sketch, lambda sk: {"entity": sk.extend(entity, pick)})


@command(category="sketch")
def sketch_set_construction(sketch: str, entities: list[str], construction: bool = True) -> dict:
    """Toggle 'For construction' on sketch entities (construction geometry is ignored by features)."""
    def f(sk):
        for eid in entities:
            if eid not in sk.entities:
                raise SketchError(f"unknown sketch entity {eid}")
            sk.entities[eid]["construction"] = construction
    return _sk(sketch, f)


@command(category="sketch", mcp=False)
def sketch_drag_entity(sketch: str, entity: str, frm: Vec2, to: Vec2) -> dict:
    """Drag a sketch entity (used by the UI): a circle or arc grabbed on its curve resizes, anything else
    moves by to - frm; the sketch is re-solved."""
    return _sk(sketch, lambda sk: sk.drag_entity(entity, frm, to))


@command(category="sketch", mcp=False)
def sketch_move_point(sketch: str, point: str, position: Vec2) -> dict:
    """Drag a sketch point to a new position and re-solve (used by the UI)."""
    def f(sk):
        if point not in sk.points:
            raise SketchError(f"unknown point {point}")
        sk.points[point] = [float(position[0]), float(position[1])]
        return None
    return _sk(sketch, f)


@command(category="sketch")
def set_sketch_visibility(sketch: str, hidden: bool) -> dict:
    """Show or hide a sketch in the graphics area."""
    APP.doc.edit_feature(sketch, {"hidden": hidden})
    return APP.doc.summary()


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


# ====================================================================== features
def _up_to(doc, up_to):
    """Up-to target: a face id ('Face3') / face reference, or a vertex position [x, y, z]."""
    if up_to is None:
        return None
    if isinstance(up_to, (list, tuple)):
        return [float(v) for v in up_to]
    return _face(doc, up_to)


def _ext_params(sketch, depth, end_condition, reverse, draft, draft_outward, direction2_depth,
                direction2_end_condition, merge, start_offset, up_to=None, up_to_offset=0.0, reverse_offset=False):
    p = {"sketch": sketch, "depth": depth, "end_condition": end_condition, "reverse": reverse,
         "draft": draft, "draft_outward": draft_outward, "merge": merge, "start_offset": start_offset}
    if up_to is not None:
        p.update(up_to=_up_to(APP.doc, up_to), up_to_offset=up_to_offset, reverse_offset=reverse_offset)
    if direction2_depth is not None or direction2_end_condition is not None:
        p["direction2"] = {"depth": direction2_depth or depth, "end_condition": direction2_end_condition or "Blind"}
    return p


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


def _feat(ftype: str, params: dict) -> dict:
    feat = APP.doc.add_feature(ftype, params)
    return {"feature": feat["name"], "info": APP.doc.ctx.info.get(feat["name"]), "model": APP.doc.summary()}


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
    return sheet_metal_info.__wrapped__() | {"model": doc.summary()}


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


def _point_spec(doc: PartDocument, pt):
    if _is_edge_sel(pt):
        return _edge(doc, pt)
    if _is_face_sel(pt):
        return _face(doc, pt)
    return pt


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


@command(category="sketch")
def create_3d_sketch(points: list[Vec3], kind: Literal["line", "spline"] = "line", closed: bool = False,
                     name: str | None = None) -> dict:
    """3D Sketch: a chain through [x, y, z] points as connected lines (kind 'line') or one spline. Use its
    name as a sweep path. Add more chains with add_3d_sketch_segment."""
    feat = APP.doc.add_feature("sketch3d", {"segments": [{"points": points, "kind": kind, "closed": closed}]}, name=name)
    return {"sketch": feat["name"], "info": APP.doc.ctx.info.get(feat["name"]), "model": APP.doc.summary()}


@command(category="sketch")
def add_3d_sketch_segment(sketch: str, points: list[Vec3], kind: Literal["line", "spline"] = "line",
                          closed: bool = False) -> dict:
    """Append a line chain or spline through [x, y, z] points to an existing 3D sketch."""
    doc = APP.doc
    f = doc.find(sketch)
    if f["type"] != "sketch3d":
        raise DocumentError(f"{sketch} is not a 3D sketch")
    doc.edit_feature(sketch, {"segments": f["params"]["segments"] + [{"points": points, "kind": kind, "closed": closed}]})
    return {"sketch": sketch, "info": doc.ctx.info.get(sketch), "model": doc.summary()}


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


# ====================================================================== view capture
@command(mutates=False, category="query")
def get_view_image(view: Literal["isometric", "front", "back", "top", "bottom", "left", "right", "trimetric"] = "isometric",
                   width: int = 800, height: int = 600, show_edges: bool = True) -> dict:
    """Render the active part to a PNG (base64) from a standard view, for visual checks."""
    from .render import render_png
    png = render_png(APP.doc, view=view, width=width, height=height, edges=show_edges)
    import base64
    return {"png_base64": base64.b64encode(png).decode(), "view": view, "width": width, "height": height}


# more commands (flex, mold tools, library features, ...) register with the same registry
from .commands_more import *  # noqa: E402,F401,F403
from .commands_mfg import *  # noqa: E402,F401,F403
from .commands_app import *  # noqa: E402,F401,F403
from .commands_tol import *  # noqa: E402,F401,F403
from .commands_review import *  # noqa: E402,F401,F403
