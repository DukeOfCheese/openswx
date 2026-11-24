"""Macro recording and replay."""
from __future__ import annotations

from pathlib import Path

from openswx.core.document import DocumentError
from openswx.session import APP, command

from .common import openswx_home


@command(mutates=False, category="tools")
def macro_start() -> dict:
    """Tools > Macro > Record: record the model-changing commands run from now on (UI or API)."""
    APP.macro = []
    return {"recording": True}


@command(mutates=False, category="tools")
def macro_status() -> dict:
    """Whether a macro is recording, and how many commands it holds."""
    return {"recording": APP.macro is not None, "commands": len(APP.macro or [])}


@command(mutates=False, category="tools")
def macro_stop(path: str | None = None) -> dict:
    """Tools > Macro > Stop: end recording and save the macro as a Python script (.py) replaying the commands
    through the openswx API (default name in the macros folder)."""
    import datetime
    from openswx.manufacturing.ecosystem import macro_source
    if APP.macro is None:
        raise DocumentError("no macro is recording")
    entries, APP.macro = APP.macro, None
    p = Path(path) if path else openswx_home() / "macros" / f"macro-{datetime.datetime.now():%Y%m%d-%H%M%S}.py"
    if p.suffix != ".py":
        p = p.with_suffix(".py")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(macro_source(entries), encoding="utf-8")
    return {"path": str(p.resolve()), "commands": len(entries)}


@command(mutates=False, category="tools", mcp=False)
def run_macro(path: str) -> dict:
    """Tools > Macro > Run: execute a macro (.py, Python using the openswx API as `S`). Macros run with your
    permissions: run only ones you trust."""
    import runpy
    from openswx import session
    p = Path(path)
    if not p.is_file() or p.suffix != ".py":
        raise DocumentError(f"no macro file {path!r}")
    before = len(APP.log)
    try:
        runpy.run_path(str(p), init_globals={"S": session}, run_name="__main__")
    except session.CommandError as exc:
        raise DocumentError(f"the macro stopped: {exc}") from exc
    return {"path": str(p.resolve()), "commands": len(APP.log) - before}


__all__ = ["macro_start", "macro_stop", "macro_status", "run_macro"
]
