"""User settings (Tools > Options), kept in `<home>/settings.json`.

Each setting has a default, a type and a one-line description; unknown keys in the file are ignored and
kept. Environment variables win over the file where one exists (OPENSWX_AUTOSAVE, OPENSWX_TOOLSETS), so a
launch can still be configured from outside.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SETTINGS = {
    "units": {"default": "mm", "choices": ["mm", "cm", "m", "in", "ft"], "doc": "Units of new documents (display; commands always take mm)"},
    "autosave_seconds": {"default": 60, "min": 0, "max": 3600, "doc": "Autosave for recovery every N seconds (0: off)"},
    "mcp_toolsets": {"default": "core", "doc": "MCP tools listed when an agent connects: core, all, or e.g. core,assembly"},
    "confirm_close": {"default": True, "doc": "Ask before closing the window with unsaved changes"},
    "general_tolerance": {"default": "m", "choices": ["f", "m", "c", "v"], "doc": "ISO 2768 class for untoleranced dimensions in inspection reports"},
    "update_repository": {"default": "", "doc": "GitHub repository (owner/name) whose releases Help > Check for Updates looks at"},
    "image_width": {"default": 1600, "min": 100, "max": 8000, "doc": "Width in pixels of exported pictures"},
    "image_height": {"default": 1200, "min": 100, "max": 8000, "doc": "Height in pixels of exported pictures"},
}


class SettingsError(ValueError):
    pass


def path() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "settings.json"


def _file() -> dict:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _check(key: str, value):
    spec = SETTINGS.get(key)
    if spec is None:
        raise SettingsError(f"unknown setting {key!r}; expected one of {sorted(SETTINGS)}")
    default = spec["default"]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true or false")
        return value
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{key} must be a number")
        if not spec.get("min", value) <= value <= spec.get("max", value):
            raise SettingsError(f"{key} must be between {spec['min']} and {spec['max']}")
        return type(default)(value)
    if not isinstance(value, str):
        raise SettingsError(f"{key} must be text")
    if "choices" in spec and value not in spec["choices"]:
        raise SettingsError(f"{key} must be one of {spec['choices']}")
    return value


def get(key: str):
    """A setting's value: the file's if valid, else the default."""
    v = _file().get(key, SETTINGS[key]["default"])
    try:
        return _check(key, v)
    except SettingsError:
        return SETTINGS[key]["default"]


def all_settings() -> dict:
    return {k: get(k) for k in SETTINGS}


def update(values: dict) -> dict:
    """Check and save several settings; nothing is written if any is invalid."""
    checked = {k: _check(k, v) for k, v in values.items()}
    d = _file()
    d.update(checked)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return all_settings()


def reset() -> dict:
    d = {k: v for k, v in _file().items() if k not in SETTINGS}
    if d:
        path().write_text(json.dumps(d, indent=1), encoding="utf-8")
    else:
        path().unlink(missing_ok=True)
    return all_settings()
