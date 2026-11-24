from __future__ import annotations

from typing import Callable

CURRENT = {"openswx-part": 1, "openswx-assembly": 1, "openswx-drawing": 1}

# (format, from_version) -> function(dict) -> dict of version from_version + 1
STEPS: dict[tuple[str, int], Callable[[dict], dict]] = {}


class FormatError(ValueError):
    pass


def migrate(d: dict, fmt: str) -> dict:
    """`d` (as read from a file) brought to the current version of `fmt`. Raises FormatError for another
    format, a newer version, or a version there is no step from."""
    if not isinstance(d, dict) or d.get("format") != fmt:
        raise FormatError(f"not an {fmt.replace('-', ' ')} file")
    try:
        v = int(d.get("version", 1))
    except (TypeError, ValueError):
        raise FormatError(f"unreadable file version {d.get('version')!r}") from None
    cur = CURRENT[fmt]
    if v > cur:
        raise FormatError(f"this file was saved by a newer openswx (file version {v}, this program reads up to "
                          f"{cur}); update openswx to open it")
    if v < 1:
        raise FormatError(f"unreadable file version {v}")
    while v < cur:
        step = STEPS.get((fmt, v))
        if step is None:
            raise FormatError(f"no way to read file version {v} of {fmt}")
        d = step(d)
        v += 1
        d["version"] = v
    return d


def extras(d: dict, known: set[str]) -> dict:
    """The keys of a file this version does not use (kept so that saving writes them back)."""
    return {k: v for k, v in d.items() if k not in known}
