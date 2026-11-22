"""Settings commands (same registry as session.py, which re-exports them)."""
from __future__ import annotations

from . import settings
from .document import DocumentError
from .session import command

__all__ = ["get_settings", "set_settings"]


@command(mutates=False, category="tools", mcp=False)
def get_settings() -> dict:
    """Tools > Options: the user's settings with, for each, its description, default and allowed values."""
    return {"settings": settings.all_settings(), "file": str(settings.path()),
            "about": {k: {kk: vv for kk, vv in v.items()} for k, v in settings.SETTINGS.items()}}


@command(mutates=False, category="tools", mcp=False)
def set_settings(values: dict | None = None, reset: bool = False) -> dict:
    """Tools > Options: change settings ({"units": "in", "autosave_seconds": 120, ...}); reset=True puts
    every setting back to its default. Saved for this user; nothing is saved if a value is not valid."""
    try:
        if reset:
            settings.reset()
        return {"settings": settings.update(values) if values else settings.all_settings()}
    except settings.SettingsError as exc:
        raise DocumentError(str(exc)) from exc
