"""Syntax-check the web UI modules. `node --check file.js` silently accepts ES-module files with errors,
so each file is copied to .mjs (forcing module parsing) before checking."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "openswx" / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("src", sorted(WEB.glob("*.js")), ids=lambda p: p.name)
def test_web_module_syntax(src, tmp_path):
    dst = tmp_path / (src.stem + ".mjs")
    shutil.copyfile(src, dst)
    r = subprocess.run(["node", "--check", str(dst)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_icon_keys_unique():
    import collections
    import re
    keys = re.findall(r"^  ([a-z_0-9]+): S\(", (WEB / "icons.js").read_text(encoding="utf-8"), re.M)
    dup = [k for k, n in collections.Counter(keys).items() if n > 1]
    assert not dup, f"duplicate icon keys (the later one silently wins): {dup}"
