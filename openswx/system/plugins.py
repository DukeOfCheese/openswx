from __future__ import annotations

import os

GROUP = "openswx.plugins"
loaded: list[dict] = []


def load_all() -> list[dict]:
    """Import every installed plugin once; returns [{"name", "module", "ok", "commands", "error"}]."""
    if loaded or os.environ.get("OPENSWX_PLUGINS", "").strip().lower() in ("off", "0", "no", "false"):
        return loaded
    from importlib.metadata import entry_points

    from openswx.system import applog
    from openswx import session as S
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
