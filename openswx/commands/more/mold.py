"""Mold tools: parting line, parting surface, shut-off surfaces and tooling split."""
from __future__ import annotations

from openswx.commands.support import _feat
from openswx.session import Vec3, command


@command(category="mold")
def parting_line(direction: str | Vec3 = "Y", level: float | None = None) -> dict:
    """Mold Tools > Parting Line: the edges dividing the part between core and cavity for the pull direction."""
    return _feat("parting_line", {"direction": direction, "level": level})


@command(category="mold")
def parting_surface(direction: str | Vec3 = "Y", margin: float = 10.0, level: float | None = None) -> dict:
    """Mold Tools > Parting Surface: a flat surface on the parting plane out to `margin` beyond the part."""
    return _feat("parting_surface", {"direction": direction, "margin": margin, "level": level})


@command(category="mold")
def shut_off_surfaces(direction: str | Vec3 = "Y", level: float | None = None) -> dict:
    """Mold Tools > Shut-off Surfaces: patches over the openings through the part at the parting level."""
    return _feat("shut_off_surfaces", {"direction": direction, "level": level})


@command(category="mold")
def tooling_split(direction: str | Vec3 = "Y", margin: float = 10.0, level: float | None = None,
                  keep_part: bool = True) -> dict:
    """Mold Tools > Tooling Split: a block `margin` mm around the part split at the parting level into core
    and cavity bodies (the part kept as a third body unless keep_part is false). Scale the part first for
    shrinkage (Features > Scale)."""
    return _feat("tooling_split", {"direction": direction, "margin": margin, "level": level, "keep_part": keep_part})


__all__ = ["parting_line", "parting_surface", "shut_off_surfaces", "tooling_split"
]
