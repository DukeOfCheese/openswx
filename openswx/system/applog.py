"""The application log: every command (name, who asked, how long it took) and every error with its traceback,
in `<home>/logs/openswx.log` (1 MB x 3 files). `OPENSWX_LOG=off` disables it; `OPENSWX_LOG=debug` adds the
arguments of each command."""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import zipfile
from pathlib import Path

log = logging.getLogger("openswx")
_ready = False


def log_dir() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "logs"


def setup() -> logging.Logger:
    """Attach the file handler (once). Never raises: a log that cannot be written is simply not kept."""
    global _ready
    if _ready:
        return log
    _ready = True
    mode = os.environ.get("OPENSWX_LOG", "info").strip().lower()
    log.propagate = False
    if mode in ("off", "0", "no", "false"):
        log.addHandler(logging.NullHandler())
        return log
    log.setLevel(logging.DEBUG if mode == "debug" else logging.INFO)
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(d / "openswx.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        from openswx import __version__
        log.info("openswx %s started (python %s, %s, pid %d)", __version__, platform.python_version(),
                 platform.platform(), os.getpid())
    except OSError:
        log.addHandler(logging.NullHandler())
    return log


def versions() -> dict:
    from importlib import metadata

    from openswx import __version__
    out = {"openswx": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
           "frozen": bool(getattr(sys, "frozen", False))}
    for pkg in ("build123d", "cadquery-ocp", "numpy", "scipy", "fastapi", "uvicorn", "mcp", "ezdxf", "pywebview"):
        try:
            out[pkg] = metadata.version(pkg)
        except Exception:  # noqa: BLE001 - not installed, or a frozen build without metadata
            pass
    return out


def write_report(path: str, extra: dict[str, str]) -> str:
    """A zip of the log files plus the given texts (name -> content)."""
    p = Path(path)
    if p.suffix.lower() != ".zip":
        p = p.with_suffix(".zip")
    p.parent.mkdir(parents=True, exist_ok=True)
    for h in log.handlers:
        h.flush()
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        d = log_dir()
        if d.is_dir():
            for f in sorted(d.glob("openswx.log*")):
                z.write(f, f"logs/{f.name}")
        for name, text in extra.items():
            z.writestr(name, text)
    return str(p.resolve())
