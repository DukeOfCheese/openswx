"""Plugins: installed Python packages that add commands and file formats to openswx.

A plugin is a package that declares an entry point in the group `openswx.plugins`:

    [project.entry-points."openswx.plugins"]
    gears = "openswx_gears"

Its module is imported when openswx starts. It can

- register commands with the same decorator the built-in ones use; they become REST commands and MCP tools
  (toolset "plugins") like any other:

      from openswx.session import APP, command

      @command(category="gears")
      def spur_gear(module: float, teeth: int, width: float) -> dict:
          '''A spur gear ...'''          # the docstring is the tool description
          ...

- register a file format with `register_format`: a reader makes the extension open (File > Open, the desktop
  app's dialogs, double-click, `openswx FILE`, open_part) and import (import_file); a writer makes it an export
  (export_file, File > Export, Save As, `openswx convert`):

      from openswx.system.plugins import register_format

      register_format(".ncx", "Neutral CAD eXchange", reader=read_ncx, writer=write_ncx)

See docs/plugins.md for the whole API and a minimal example. `OPENSWX_PLUGINS=off` starts without plugins. A
plugin that fails to import is skipped and reported by list_plugins; it cannot stop openswx starting.
"""

from __future__ import annotations

import os
from typing import Callable

GROUP = "openswx.plugins"
loaded: list[dict] = []
_loading: str | None = None  # the entry point being imported: what register_format attributes formats to


def register_format(extension: str | list[str] | tuple[str, ...], name: str, *, reader: Callable | None = None,
                    writer: Callable | None = None, id: str | None = None):
    """Add a file format to openswx; call it when the plugin module is imported.

    extension: '.ncx', or several ('.ncx', '.ncxz'); the first is used for new files.
    name: what dialogs and menus call it, e.g. 'Neutral CAD eXchange'.
    reader(path) -> shape or (shape, notes): read the file into a build123d (or OCP) shape with at least one solid.
        With a reader the extension opens as a new part and imports into the active part as an Imported feature.
    writer(shape, path) -> None: write the active document's body (a build123d Shape) to path. With a writer the
        extension is an export.
    id: the name export_file takes as `format` (default: the extension without the dot).

    A built-in extension cannot be taken over, and two plugins cannot claim the same one: that raises
    formats.FormatError, which list_plugins reports as the plugin's load error. Returns the FileFormat.
    """
    from openswx.exchange import formats
    return formats.register(extension, name, reader=reader, writer=writer, id=id, plugin=_loading or "plugin")


def load_all() -> list[dict]:
    """Import every installed plugin once; returns [{"name", "module", "ok", "commands", "formats", "error"}]."""
    global _loading
    if loaded or os.environ.get("OPENSWX_PLUGINS", "").strip().lower() in ("off", "0", "no", "false"):
        return loaded
    from importlib.metadata import entry_points

    from openswx.exchange import formats
    from openswx.system import applog
    from openswx import session as S
    try:
        eps = list(entry_points(group=GROUP))
    except Exception:  # noqa: BLE001 - broken package metadata must not stop the application
        eps = []
    for ep in eps:
        before, fmts_before = set(S.COMMANDS), len(formats.all_formats())
        entry = {"name": ep.name, "module": ep.value, "ok": True, "commands": [], "formats": [], "error": None}
        _loading = ep.name
        try:
            ep.load()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            applog.log.warning("plugin %s (%s) failed to load", ep.name, ep.value, exc_info=True)
        finally:
            _loading = None
        new = sorted(set(S.COMMANDS) - before)
        for n in new:
            S.COMMANDS[n]["plugin"] = ep.name
        entry["commands"] = new
        entry["formats"] = [f.describe() for f in formats.all_formats()[fmts_before:]]
        loaded.append(entry)
    return loaded
