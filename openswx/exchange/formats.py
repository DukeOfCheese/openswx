"""The file formats openswx opens, imports and exports: one list that every file path reads.

open_part / import_file / export_file, the web UI's Open dialog and Export menu, the desktop app's native
dialogs, `openswx convert`, the browser download route and the Windows "Open with" registration all ask this
module which extensions exist, so a format added here (or by a plugin with `register_format`) shows up in all
of them at once.

A built-in format is read and written by code elsewhere in openswx; this list only says that it exists and what
it can do. A plugin format carries its own `reader` and/or `writer`:

    reader(path: str) -> shape | (shape, notes)      a build123d / OCP shape with at least one solid; notes is a
                                                     list of strings shown with the Imported feature
    writer(shape, path: str) -> None                 shape is the active document's body (a build123d Shape)

openswx turns what a reader returns into an Imported feature, so a plugin format opens as a new part
(File > Open, double-click, the command line) and imports into the active part (import_file) like STEP does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class FormatError(ValueError):
    """A format registration that cannot be accepted (bad extension, clash with another format)."""


@dataclass
class FileFormat:
    """One file format. `id` is the name export_file takes as `format` (e.g. "step"); `extensions` are the file
    extensions it is recognised by, lower case with the dot, the first one used for new files."""

    id: str
    name: str
    extensions: tuple[str, ...]
    open: bool = False  # open_part opens it (File > Open, double-click, command line)
    import_: bool = False  # import_file brings it into the active part
    export: bool = False  # export_file writes it (Export menu, Save As)
    native: bool = False  # an openswx document (.oswprt / .oswasm / .oswdrw): saved with save_part, not exported
    reader: Callable | None = field(default=None, repr=False)
    writer: Callable | None = field(default=None, repr=False)
    plugin: str | None = None  # the plugin that registered it (None: built in)

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "extensions": list(self.extensions), "open": self.open,
                "import": self.import_, "export": self.export, "native": self.native, "plugin": self.plugin}


def _f(id, name, exts, **kw) -> FileFormat:
    return FileFormat(id, name, tuple(exts), **kw)


BUILTIN: tuple[FileFormat, ...] = (
    _f("oswprt", "openswx part", [".oswprt"], open=True, native=True),
    _f("oswasm", "openswx assembly", [".oswasm"], open=True, native=True),
    _f("oswdrw", "openswx drawing", [".oswdrw"], open=True, native=True),
    _f("step", "STEP", [".step", ".stp"], open=True, import_=True, export=True),
    _f("iges", "IGES", [".igs", ".iges"], open=True, import_=True, export=True),
    _f("x_t", "Parasolid text", [".x_t", ".xmt_txt"], open=True, import_=True, export=True),
    _f("x_b", "Parasolid binary", [".x_b", ".xmt_bin"], open=True, import_=True),
    _f("brep", "OpenCASCADE BREP", [".brep"], open=True, import_=True, export=True),
    _f("stl", "STL mesh", [".stl"], open=True, import_=True, export=True),
    _f("3mf", "3MF (3D printing)", [".3mf"], open=True, import_=True, export=True),
    _f("gltf", "glTF", [".gltf"], export=True),
    _f("glb", "glTF binary (GLB)", [".glb"], export=True),
    _f("fcstd", "FreeCAD", [".fcstd"], open=True, import_=True),
    _f("sldprt", "SOLIDWORKS part", [".sldprt"], open=True, import_=True),
    _f("sldasm", "SOLIDWORKS assembly", [".sldasm"], open=True),
    _f("slddrw", "SOLIDWORKS drawing", [".slddrw"], open=True),
)

_plugin_formats: list[FileFormat] = []


def all_formats() -> list[FileFormat]:
    """Every format: the built-in ones, then the plugins' in the order they were registered."""
    return [*BUILTIN, *_plugin_formats]


def by_extension(ext: str) -> FileFormat | None:
    """The format a file extension ('.step', 'STEP' or 'step') belongs to, or None."""
    e = _ext(ext)
    return next((f for f in all_formats() if e in f.extensions), None)


def by_id(fmt: str) -> FileFormat | None:
    """The format export_file names `fmt` (its id, or any of its extensions without the dot: 'stp', 'igs')."""
    key = fmt.lower().lstrip(".")
    return next((f for f in all_formats() if f.id == key), None) or by_extension(key)


def extensions(*, open: bool = False, import_: bool = False, export: bool = False) -> list[str]:
    """Every extension of the formats that can do all of the asked things."""
    return [e for f in all_formats() if (f.open or not open) and (f.import_ or not import_)
            and (f.export or not export) for e in f.extensions]


def plugin_format(ext: str) -> FileFormat | None:
    """The plugin format for this extension, if a plugin registered one (built-in formats return None)."""
    f = by_extension(ext)
    return f if f is not None and f.plugin else None


def _ext(e: str) -> str:
    e = str(e).strip().lower()
    return e if e.startswith(".") else "." + e


def register(extension, name: str, *, reader: Callable | None = None, writer: Callable | None = None,
             id: str | None = None, plugin: str | None = None) -> FileFormat:
    """Add a format (see plugins.register_format, which is the public way in)."""
    exts = tuple(_ext(e) for e in ([extension] if isinstance(extension, str) else extension))
    if not exts or any(len(e) < 2 or not e[1:].replace("_", "").isalnum() for e in exts):
        raise FormatError(f"bad file extension in {exts!r}: give it like '.ncx'")
    if reader is None and writer is None:
        raise FormatError(f"{name}: give a reader (to open and import {exts[0]} files), a writer (to export them), or both")
    for f in all_formats():
        clash = set(exts) & set(f.extensions)
        if clash:
            raise FormatError(f"{', '.join(sorted(clash))} is already handled by {f.name}"
                              + (f" (plugin {f.plugin})" if f.plugin else " (built in)"))
    fid = (id or exts[0][1:]).lower()
    if by_id(fid) is not None:
        raise FormatError(f"format id {fid!r} is already taken")
    fmt = FileFormat(fid, name, exts, open=reader is not None, import_=reader is not None,
                     export=writer is not None, reader=reader, writer=writer, plugin=plugin or "plugin")
    _plugin_formats.append(fmt)
    return fmt


def unregister(fmt: FileFormat) -> None:
    """Remove a plugin format again (tests, and a plugin that is unloaded)."""
    if fmt in _plugin_formats:
        _plugin_formats.remove(fmt)


def dialog_filters(*, open: bool = False, export: bool = False) -> list[str]:
    """Native dialog filters ('STEP (*.step;*.stp)'), one per format, for the desktop app's file dialogs."""
    return [dialog_filter(f) for f in all_formats() if (not open or f.open) and (not export or f.export)]


def dialog_filter(f: FileFormat) -> str:
    """One format as a native dialog filter: 'STEP (*.step;*.stp)'."""
    return f"{f.name} (" + ";".join("*" + e for e in f.extensions) + ")"


def describe() -> dict:
    """What list_file_formats returns: every format and, for convenience, the extensions per use."""
    return {"formats": [f.describe() for f in all_formats()],
            "open": extensions(open=True), "import": extensions(import_=True), "export": extensions(export=True)}
