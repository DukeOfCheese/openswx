"""Release build: the PyInstaller app, then the Windows installer (Inno Setup, when installed) or else a
portable zip.

    .venv\\Scripts\\python packaging\\build.py [--skip-app] [--zip]

Outputs in dist-new\\: openswx\\ (the app folder), openswx-setup-<version>.exe and/or
openswx-<version>-win64.zip (unzip anywhere and run openswx.exe; `openswx-cli register-file-types` adds
the file associations the installer would).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist-new"
ISCC = [Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"), Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]  # (per-user install)


def version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-app", action="store_true", help="reuse dist-new\\openswx instead of rebuilding it")
    ap.add_argument("--zip", action="store_true", help="also make the portable zip when the installer is built")
    a = ap.parse_args()
    if not a.skip_app:
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "openswx.spec"), "--noconfirm",
                        "--distpath", str(DIST), "--workpath", str(ROOT / "build-new")], check=True, cwd=ROOT)
    app = DIST / "openswx"
    if not (app / "openswx.exe").exists():
        raise SystemExit(f"no app build at {app}")
    ver = version()
    iscc = next((p for p in ISCC if p.exists()), None)
    if iscc:
        subprocess.run([str(iscc), f"/DAppVersion={ver}", str(ROOT / "packaging" / "openswx.iss")], check=True, cwd=ROOT)
        print(f"installer: {DIST / f'openswx-setup-{ver}.exe'}")
    if a.zip or not iscc:
        base = DIST / f"openswx-{ver}-win64"
        out = shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir="openswx")
        print(f"portable zip: {out}" + ("" if iscc else "  (Inno Setup not installed: no installer built)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
