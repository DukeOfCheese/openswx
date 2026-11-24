import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "openswx" / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("src", sorted((WEB / "js").rglob("*.js")), ids=lambda p: p.name)
def test_web_module_syntax(src, tmp_path):
    dst = tmp_path / (src.stem + ".mjs")
    shutil.copyfile(src, dst)
    r = subprocess.run(["node", "--check", str(dst)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_icon_sprite_has_every_svg():
    import re
    from openswx.icons import sprite
    files = sorted(p.stem for p in (WEB / "svgs").glob("*.svg"))
    ids = re.findall(r'<symbol id="icon-([^"]+)"', sprite())
    assert ids, "the icon sprite is empty"
    assert len(ids) == len(set(ids)), "duplicate icon names"
    assert sorted(ids) == files, f"the sprite and web/svgs disagree: {sorted(set(ids) ^ set(files))}"
    assert "<!-- icon-sprite -->" in (WEB / "index.html").read_text(encoding="utf-8")
