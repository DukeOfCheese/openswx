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
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from openswx.system import applog, jobs
from openswx.core.document import DocumentError, PartDocument
from openswx.core.features import FeatureError
from openswx.core.sketch import SketchError

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

    def is_throwaway(self, doc) -> bool:
        """The part `active_doc` made on demand, still untouched: no features, no unsaved changes, no file."""
        return (getattr(doc, "_auto", False) and getattr(doc, "kind", "part") == "part" and not doc.features
                and not doc.dirty and not doc.path)

    def drop_throwaway(self) -> None:
        """Close the active document if it is still the untouched part made on demand, so opening or creating
        a document (from the Home page, say) leaves no stray empty Part1 behind."""
        d = self.documents.get(self.active) if self.active else None
        if d is not None and self.is_throwaway(d):
            self.documents.pop(self.active, None)
            self.active = next(iter(self.documents), None)

    def new_document(self, name: str | None = None) -> PartDocument:
        self.drop_throwaway()
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
            self.new_document()._auto = True  # (made on demand: dropped by the next new or opened document while untouched)
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


def home_at_start() -> bool:
    """Whether a window that opens now starts on the Home page: show_home is on and nothing but the untouched
    part made on demand is open (or nothing at all yet: the window's first request makes that part). Decided
    when the page is served, so Home is the first thing it paints; a file opened at launch makes this False."""
    from openswx.system import settings
    if not settings.get("show_home"):
        return False
    if not APP.documents:
        return True
    return len(APP.documents) == 1 and APP.is_throwaway(APP.documents.get(APP.active))
COMMANDS: dict[str, dict] = {}
ERRORS = (DocumentError, FeatureError, SketchError, ValueError, KeyError, FileNotFoundError)
_TL = threading.local()  # .origin = "ui" for web requests; MCP/tests default to "agent"


class CommandError(RuntimeError):
    pass


# Command hooks: optional add-ons (live collaboration) that watch the outermost mutating commands. A hook is an
# object with
#     before(name, arguments) -> token    (called with the command lock held, before the command runs; may
#                                          raise to refuse the command)
#     after(token, ok)                    (after it: ok=False when it failed, and then it must not raise)
# and optionally redirect(name, document) -> result or None, which commands such as undo ask first (a hook
# may do them differently for some documents). With no hooks registered nothing here runs.
COMMAND_HOOKS: list = []


def add_command_hook(hook) -> None:
    if hook not in COMMAND_HOOKS:
        COMMAND_HOOKS.append(hook)


def remove_command_hook(hook) -> None:
    if hook in COMMAND_HOOKS:
        COMMAND_HOOKS.remove(hook)


def redirected(name: str, doc) -> Any:
    """What a hook does instead of command `name` on `doc`, or None: run the command as usual."""
    for h in list(COMMAND_HOOKS):
        fn = getattr(h, "redirect", None)
        out = fn(name, doc) if fn is not None else None
        if out is not None:
            return out
    return None


def _hooks_after(tokens: list, ok: bool) -> None:
    for h, tok in reversed(tokens):
        if ok:
            h.after(tok, True)
        else:
            try:
                h.after(tok, False)
            except Exception:  # noqa: BLE001 - the command's own error is the one to report
                _LOG.exception("command hook failed after a failed command")


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
                if watch is not None and getattr(getattr(watch, "_collab", None), "closed", True) is False:
                    watch = None  # (a cloud document: the server keeps it, it is never "unsaved")
                if watch is not None and not watch.dirty and getattr(watch, "_clean", None) is None:
                    watch._clean = _fingerprint(watch)
            t0 = time.perf_counter()
            _TL.depth = depth + 1
            hooked = None  # [(hook, token)] while the hooks wait for this command's outcome
            try:
                if COMMAND_HOOKS and mutates and outer is None and not getattr(_TL, "preview", False):
                    hooked = []
                    for h in list(COMMAND_HOOKS):
                        hooked.append((h, h.before(fn.__name__, bound.arguments)))
                result = fn(*bound.args, **bound.kwargs)  # (as bound: the workspace check may have resolved paths)
                if watch is not None:  # (before the hooks: a hook may say the change is kept elsewhere)
                    _mark_dirty(watch)
                if hooked is not None:
                    done, hooked = hooked, None
                    _hooks_after(done, True)
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
                if hooked:  # (the command failed: hooks hear so, and nothing of it goes anywhere)
                    _hooks_after(hooked, False)
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
                    from openswx.system import recovery
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
    "export_script": ("path",), "simulation_report": ("path",), "sketch_from_image": ("path",), "import_sw_configurations": ("path",), "export_handoff_pack": ("folder",), "compare_documents": ("a", "b"),
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
              "inspection_report", "composer_export", "electrical_formboard", "export_script", "export_handoff_pack",
              "standard_part", "simulation_report"}


def _journal(name: str, arguments) -> None:
    """Record a command in the active document's journal (the script that rebuilds it; see export_script).
    Undo and redo need no entry: the journal is part of the state they put back."""
    if name == "open_part":  # a foreign file opened as a new document: its script starts with the import
        doc = APP.documents.get(APP.active)
        if doc is not None and getattr(doc, "journal", None) == [] and getattr(doc, "features", None):
            doc.journal = [{"command": "import_file", "args": {"path": str(arguments.get("path"))}}]
        return
    if name in NO_JOURNAL or name.startswith(("pdm_", "macro_", "collab_")) or name == "run_macro":
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


def argument_problem(name: str, args: dict) -> str | None:
    """What is wrong with `args` for command `name` (unknown or missing arguments), in words; None if they
    fit its signature. Checked before the call so a caller never sees the interpreter's own error text."""
    import inspect
    if not isinstance(args, dict):
        return f"bad arguments for {name}: arguments must be an object of name: value"
    fn = COMMANDS[name]["fn"]
    sig = inspect.signature(getattr(fn, "__wrapped__", fn))
    params = sig.parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return None
    unknown = sorted(k for k in args if k not in params)
    missing = [p.name for p in params.values() if p.default is p.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
               and p.name not in args]
    if not unknown and not missing:
        return None
    parts = []
    if unknown:
        parts.append(f"unknown argument{'s' if len(unknown) > 1 else ''} {', '.join(unknown)}")
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    return f"bad arguments for {name}: {'; '.join(parts)} (it takes {', '.join(params) or 'no arguments'})"


def call(name: str, args: dict | None = None, origin: str | None = None) -> Any:
    if name not in COMMANDS:
        raise CommandError(f"unknown command {name!r}")
    problem = argument_problem(name, args or {})
    if problem:
        raise CommandError(problem)
    prev = getattr(_TL, "origin", None)
    if origin:
        _TL.origin = origin
    try:
        return COMMANDS[name]["fn"](**(args or {}))
    finally:
        if origin:
            _TL.origin = prev if prev is not None else "agent"


# What a preview may run: commands that make or change a model feature, nothing else. A preview runs whatever it
# is given on a copy of the document, and what it is given can come from someone else (a collaborator's draft):
# no command that reads or writes files, runs code, or opens, saves or closes documents.
FEATURE_CATEGORIES = ("features", "surfaces", "sheet_metal", "weldments", "mold", "bodies")
PREVIEW_EXTRA = ("edit_feature", "redefine_feature", "add_mate", "redefine_mate", "insert_component", "edit_sketch_plane",
                 "sketch_offset", "sketch_mirror", "sketch_linear_pattern", "sketch_circular_pattern",
                 "sketch_convert_entities", "sketch_transform")  # (the other panels the UI previews)


def feature_command(name: str) -> bool:
    """Does `name` make or change a model feature (and nothing else: no files)? What redefine_feature may run."""
    meta = COMMANDS.get(name)
    return (meta is not None and meta["mutates"] and meta["category"] in FEATURE_CATEGORIES and name not in FILE_ARGS
            and not name.startswith("collab_"))


def preview_refusal(name: str, args) -> str | None:
    """Why `name` with `args` may not run as a preview (None: it may)."""
    if not isinstance(name, str) or not isinstance(args, dict):
        return "a preview needs a command name and its arguments"
    if not (feature_command(name) or (name in PREVIEW_EXTRA and name in COMMANDS)):
        return f"{name!r} cannot be previewed (only commands that make or change a feature can)"
    if name == "insert_component" and args.get("source") not in APP.documents:
        return "a component preview takes an open document, not a file"
    if name == "redefine_feature" and not feature_command(str(args.get("command_name"))):
        return f"{args.get('command_name')!r} cannot be previewed (only commands that make or change a feature can)"
    return None


def preview(name: str, args: dict | None = None, wait: bool = True) -> dict:
    """Run a command that makes or changes a feature (see preview_refusal) on a throw-away copy of the active
    document and return its mesh. wait=False: give up at once ({"ok": False, "busy": True}) when another command
    is running, instead of waiting for it."""
    args = {} if args is None else args
    why = preview_refusal(name, args)
    if why:
        return {"ok": False, "error": why, "refused": True}
    if not APP.lock.acquire(blocking=wait, timeout=30 if wait else -1):
        return {"ok": False, "busy": True, "error": "openswx is busy with another command"}
    try:
        return _preview_locked(name, args)
    finally:
        APP.lock.release()


def _preview_locked(name: str, args: dict) -> dict:
    with APP.lock:
        key = APP.active
        real = APP.active_doc
        clone = type(real)(real.name)
        if getattr(real, "kind", "part") == "assembly":
            clone.live_docs = APP.documents
        if "_rb_cache" in real.__dict__:  # (the rebuild picks up at the feature being previewed, not from scratch;
            clone.__dict__["_rb_cache"] = real.__dict__["_rb_cache"]  # entries are keyed by content, copied on read)
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


_SECRET_ARGS = {"password", "token", "invite", "code"}  # (never written to a log: passwords, invites, login codes)


def _short(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k in _SECRET_ARGS and v is not None:
            out[k] = "***"
            continue
        s = repr(v)
        out[k] = v if len(s) < 200 else s[:200] + "..."
    return out


# Helpers moved to commands.support; old callers still reach them here (module __getattr__ below).
_MOVED_TO_SUPPORT = frozenset({
    "_face",
    "_edge",
    "_is_edge_sel",
    "_is_face_sel",
    "_plane_spec",
    "_axis_spec",
    "_dim_id",
    "_sw_properties",
    "_open_sldasm_native",
    "_import_sldasm_mates",
    "_recreate_drawing",
    "_origins_at",
    "_import_dimensions",
    "_read_iges",
    "MESH_FACE_LIMIT",
    "_read_mesh",
    "_read_foreign",
    "_import_file",
    "_eval_body",
    "_direction",
    "_sim_doc",
    "_sim_face",
    "_study_edit",
    "_resolve_study",
    "_thermal",
    "_context_meshes",
    "_measure_item",
    "_drawing_state",
    "_drawing_edit",
    "_next_ann",
    "_views_edit",
    "_beside",
    "_PICK_MM",
    "_line_dimension",
    "_hole_callout",
    "_new_balloon",
    "_asm_ref",
    "_BOLT_LENGTHS",
    "_sk",
    "_add_projected",
    "_up_to",
    "_ext_params",
    "_feat",
    "_point_spec",
})


def __getattr__(name: str):
    """A helper that moved to commands.support, looked up there on first use (also for `from` imports)."""
    if name in _MOVED_TO_SUPPORT:
        from openswx.commands import support
        return getattr(support, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# more commands (flex, mold tools, library features, ...) register with the same registry
from openswx.commands import *  # noqa: E402,F401,F403
from openswx.collab.commands_collab import *  # noqa: E402,F401,F403  (live collaboration: needs the collab extra to run)

from openswx.system import plugins as _plugins  # noqa: E402

_plugins.load_all()  # (installed packages that add commands: see plugins.py)
