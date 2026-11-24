"""User settings (Tools > Options), kept in `<home>/settings.json`.

Each setting has a default, a type and a one-line description; unknown keys in the file are ignored and
kept. Environment variables win over the file where one exists (OPENSWX_AUTOSAVE, OPENSWX_TOOLSETS, and
OPENSWX_SHOW_HOME for show_home), so a launch can still be configured from outside.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# how the mouse moves the view (the web UI's viewport.js has the same table); Alt + left drag always rotates
MOUSE_PRESETS = {
    "solidworks": "rotate: middle drag; pan: Ctrl + middle drag or Ctrl + right drag; zoom: Shift + middle drag or wheel",
    "onshape": "rotate: right drag; pan: middle drag or Ctrl + right drag; zoom: wheel (no mouse gestures)",
    "fusion": "rotate: Shift + middle drag; pan: middle drag; zoom: wheel",
    "blender": "rotate: middle drag; pan: Shift + middle drag; zoom: Ctrl + middle drag or wheel",
}

SETTINGS = {
    "units": {"default": "mm", "choices": ["mm", "cm", "m", "in", "ft"], "doc": "Units of new documents (display; commands always take mm)"},
    "autosave_seconds": {"default": 60, "min": 0, "max": 3600, "doc": "Autosave for recovery every N seconds (0: off)"},
    "mcp_toolsets": {"default": "core", "doc": "MCP tools listed when an agent connects: core, all, or e.g. core,assembly"},
    "confirm_close": {"default": True, "doc": "Ask before closing the window with unsaved changes"},
    "general_tolerance": {"default": "m", "choices": ["f", "m", "c", "v"], "doc": "ISO 2768 class for untoleranced dimensions in inspection reports"},
    "update_repository": {"default": "DukeOfCheese/openswx", "doc": "GitHub repository (owner/name) whose releases Help > Check for Updates looks at"},
    "image_width": {"default": 1600, "min": 100, "max": 8000, "doc": "Width in pixels of exported pictures"},
    "image_height": {"default": 1200, "min": 100, "max": 8000, "doc": "Height in pixels of exported pictures"},
    # view colours (the graphics area's gradient and the model's default, highlight and selection colours)
    "view_background_top": {"default": "#cfdae8", "kind": "color", "doc": "Graphics area background: top colour (left at the default, the theme's own background is used)"},
    "view_background_bottom": {"default": "#ffffff", "kind": "color", "doc": "Graphics area background: bottom colour (left at the default, the theme's own background is used)"},
    "view_body_color": {"default": "#c4cad3", "kind": "color", "doc": "Colour of bodies with no appearance of their own"},
    "view_edge_color": {"default": "#101010", "kind": "color", "doc": "Colour of model edges"},
    "view_highlight_color": {"default": "#f08a00", "kind": "color", "doc": "Colour of what is under the pointer"},
    "view_selection_color": {"default": "#2f7be0", "kind": "color", "doc": "Colour of selected items"},
    # mouse
    "mouse_preset": {"default": "solidworks", "choices": list(MOUSE_PRESETS), "choice_docs": MOUSE_PRESETS,
                     "doc": "Mouse buttons for rotating, panning and zooming the view"},
    "mouse_zoom_reverse": {"default": False, "doc": "Reverse the wheel: rolling forward zooms in"},
    # keyboard: {action: "Ctrl+Shift+K", ...} overriding the defaults ("" turns a shortcut off); Tools > Customize Keyboard
    "shortcuts": {"default": {}, "doc": "Keyboard shortcuts that differ from the defaults (action -> keys)"},
    # start-up
    "show_home": {"default": True, "env": "OPENSWX_SHOW_HOME",
                  "doc": "Open on the Home page (new, open and recent files) when openswx starts without a file"},
    "new_part_location": {"default": "local", "choices": ["local", "cloud"],
                          "doc": "Where New Part creates a part when there is a choice (the last one picked)"},
}

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
# a key combination: modifiers in this order, then one key (a letter, digit, F1-F12 or a named key)
_COMBO = re.compile(r"(Ctrl\+)?(Alt\+)?(Shift\+)?([A-Z0-9]|F([1-9]|1[0-2])|Space|Enter|Escape|Delete|Backspace|Tab|Home|End|"
                    r"PageUp|PageDown|Insert|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|[-=\[\];',./`\\])")


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
    if isinstance(default, dict):  # shortcuts: action names to key combinations
        if not isinstance(value, dict):
            raise SettingsError(f"{key} must be an object of action: keys")
        for action, combo in value.items():
            if not isinstance(action, str) or not re.fullmatch(r"[a-z0-9_]+", action):
                raise SettingsError(f"{key}: {action!r} is not an action name")
            if not isinstance(combo, str) or (combo and not _COMBO.fullmatch(combo)):
                raise SettingsError(f"{key}: {combo!r} for {action} is not a key combination such as Ctrl+Shift+K")
        return dict(value)
    if spec.get("kind") == "color":
        if not isinstance(value, str) or not _COLOR.fullmatch(value):
            raise SettingsError(f"{key} must be a colour written as #rrggbb")
        return value.lower()
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
    """A setting's value: the environment variable's (for a boolean setting that has one), else the file's if
    valid, else the default."""
    env = os.environ.get(SETTINGS[key].get("env") or "")
    if env is not None and env.strip() and isinstance(SETTINGS[key]["default"], bool):
        return env.strip().lower() not in ("0", "off", "false", "no")
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
