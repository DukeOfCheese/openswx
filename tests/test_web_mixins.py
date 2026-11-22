"""The UI's mixin files (more.js, mfg.js) add methods to the App class: none may silently replace one the
class already has (a second "optionsDialog" once took the place of the System Options dialog)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "openswx" / "web"
METHOD = re.compile(r"^  (?:async )?([A-Za-z_]\w*)\(", re.M)


def test_mixins_do_not_shadow_app_methods():
    own = set(METHOD.findall((WEB / "app.js").read_text(encoding="utf-8")))
    seen: dict[str, str] = {}
    for name in ("more.js", "mfg.js"):
        methods = set(METHOD.findall((WEB / name).read_text(encoding="utf-8")))
        assert not methods & own, f"{name} redefines App methods: {sorted(methods & own)}"
        for m in methods:
            assert m not in seen, f"{m} is defined in both {seen[m]} and {name}"
            seen[m] = name


def test_menu_entries_call_methods_that_exist():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    defined = set(METHOD.findall(app))
    for name in ("more.js", "mfg.js", "drawing.js", "sketcher.js", "propman.js", "viewport.js"):
        defined |= set(METHOD.findall((WEB / name).read_text(encoding="utf-8")))
    called = set(re.findall(r'"\s*,\s*\(\)\s*=>\s*this\.([A-Za-z_]\w*)\(', app))
    missing = sorted(c for c in called if c not in defined)
    assert not missing, f"menu entries call undefined methods: {missing}"
