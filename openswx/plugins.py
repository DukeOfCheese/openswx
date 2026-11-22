"""Plugins: installed Python packages that add commands to openswx.

A plugin is a package that declares an entry point in the group `openswx.plugins`:

    [project.entry-points."openswx.plugins"]
    gears = "openswx_gears"

Its module is imported when openswx starts; it registers commands with the same decorator the built-in ones
use, and they become REST commands and MCP tools (toolset "plugins") like any other:

    from openswx.session import APP, command

    @command(category="gears")
    def spur_gear(module: float, teeth: int, width: float) -> dict:
        '''A spur gear ...'''          # the docstring is the tool description
        ...

`OPENSWX_PLUGINS=off` starts without plugins. A plugin that fails to import is skipped and reported by
list_plugins; it cannot stop openswx starting.
"""
from __future__ import annotations

import os

GROUP = "openswx.plugins"
loaded: list[dict] = []


def load_all() -> list[dict]:
    """Import every installed plugin once; returns [{"name", "module", "ok", "commands", "error"}]."""
    if loaded or os.environ.get("OPENSWX_PLUGINS", "").strip().lower() in ("off", "0", "no", "false"):
        return loaded
    from importlib.metadata import entry_points

    from . import applog
    from . import session as S
    try:
        eps = list(entry_points(group=GROUP))
    except Exception:  # noqa: BLE001 - broken package metadata must not stop the application
        eps = []
    for ep in eps:
        before = set(S.COMMANDS)
        entry = {"name": ep.name, "module": ep.value, "ok": True, "commands": [], "error": None}
        try:
            ep.load()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            applog.log.warning("plugin %s (%s) failed to load", ep.name, ep.value, exc_info=True)
        new = sorted(set(S.COMMANDS) - before)
        for n in new:
            S.COMMANDS[n]["plugin"] = ep.name
        entry["commands"] = new
        loaded.append(entry)
    return loaded
