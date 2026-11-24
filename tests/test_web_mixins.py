import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "openswx" / "web"
JS = WEB / "js"
METHOD = re.compile(r"^  (?:async )?([A-Za-z_]\w*)\(", re.M)
MIXINS = sorted((JS / "mixins").glob("*.js")) + [JS / n for n in ("more.js", "mfg.js", "home.js", "collab.js")]


def test_mixins_do_not_shadow_app_methods():
    own = set(METHOD.findall((JS / "app.js").read_text(encoding="utf-8")))
    seen: dict[str, str] = {}
    for path in MIXINS:
        methods = set(METHOD.findall(path.read_text(encoding="utf-8")))
        assert not methods & own, f"{path.name} redefines App methods: {sorted(methods & own)}"
        for m in methods:
            assert m not in seen, f"{m} is defined in both {seen[m]} and {path.name}"
            seen[m] = path.name


def test_menu_entries_call_methods_that_exist():
    defined = set(METHOD.findall((JS / "app.js").read_text(encoding="utf-8")))
    for path in MIXINS + [JS / n for n in ("drawing.js", "sketcher.js", "propman.js", "viewport.js")]:
        defined |= set(METHOD.findall(path.read_text(encoding="utf-8")))
    app = (JS / "app.js").read_text(encoding="utf-8")
    called = set(re.findall(r'"\s*,\s*\(\)\s*=>\s*this\.([A-Za-z_]\w*)\(', app))
    missing = sorted(c for c in called if c not in defined)
    assert not missing, f"menu entries call undefined methods: {missing}"
