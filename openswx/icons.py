"""The UI's icon sprite: one <symbol> per SVG file in web/svgs/, inlined into index.html.

The pictograms stay individual, editable SVG files. The page is served with all of them in one hidden
<svg id="icons"> element, so an icon reference is a fragment (#icon-name): no request per pictogram and no
name list to keep in step — the sprite's symbols are the names.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
SVGS = Path(__file__).parent / "web" / "svgs"


def sprite(directory: Path = SVGS) -> str:
    """All the pictograms in `directory` as one sprite (cached by file name and modification time)."""
    try:
        files = sorted(directory.glob("*.svg"))
    except OSError:
        return ""
    return _sprite(tuple((str(f), f.stat().st_mtime_ns) for f in files))


@lru_cache(maxsize=4)
def _sprite(files: tuple[tuple[str, int], ...]) -> str:
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "id": "icons", "aria-hidden": "true",
        "style": "position:absolute;width:0;height:0;overflow:hidden",
    })
    for name, _ in files:
        icon = ET.parse(name).getroot()
        symbol = ET.SubElement(root, f"{{{SVG_NS}}}symbol", {
            "id": f"icon-{Path(name).stem}",
            "viewBox": icon.get("viewBox", "0 0 32 32"),
        })
        for attribute in ("fill", "stroke-linejoin", "stroke-linecap"):
            if icon.get(attribute):
                symbol.set(attribute, icon.get(attribute))
        symbol.extend(icon)
    return ET.tostring(root, encoding="unicode")
