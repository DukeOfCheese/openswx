"""Release build: the PyInstaller app, then the platform's installer.

    .venv\\Scripts\\python packaging\\build.py [--skip-app] [--zip]      (Windows)
    .venv/bin/python packaging/build.py [--skip-app] [--zip]            (macOS)

Outputs in dist\\:
  Windows: openswx\\ (the app folder), openswx-setup-<version>.exe (Inno Setup, when installed; installs two small
    launchers, the app folder itself goes to %LOCALAPPDATA%\\openswx\\runtime) and/or openswx-<version>-win64.zip
    (unzip anywhere and run openswx.exe; `openswx-cli register-file-types` adds the file associations the
    installer would).
  macOS: openswx.app (runnable as is), openswx-<version>-macos-<arch>.pkg (installs the app in /Applications and
    links /usr/local/bin/openswx-cli to it) and, with --zip, openswx-<version>-macos-<arch>.zip (the app alone).
    Unsigned (ad-hoc) unless OPENSWX_CODESIGN_IDENTITY (a "Developer ID Application" identity, for the app) and
    OPENSWX_INSTALLER_IDENTITY (a "Developer ID Installer" identity, for the .pkg) are set.
"""
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build-new"
ISCC = [Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"), Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]  # (per-user install)
BUNDLE_ID = "org.openswx.openswx"
POSTINSTALL = """#!/bin/sh
# openswx-cli on the PATH (what MCP clients run: openswx-cli mcp); $2 is the install location
mkdir -p /usr/local/bin
ln -sf "${2%/}/Applications/openswx.app/Contents/MacOS/openswx-cli" /usr/local/bin/openswx-cli
exit 0
"""


def version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def build_launchers(ver: str) -> None:
    """The installer's openswx.exe / openswx-cli.exe (packaging/launcher.cs): they start the runtime in AppData.
    Compiled with the C# compiler of the .NET Framework that ships with Windows; `ver` goes in their version
    resource."""
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    out = WORK / "launcher"
    out.mkdir(parents=True, exist_ok=True)
    src, ico = ROOT / "packaging" / "launcher.cs", ROOT / "packaging" / "openswx.ico"
    nums = (re.findall(r"\d+", ver) + ["0"] * 4)[:4]  # "0.4.1" -> 0.4.1.0 (Windows wants four numbers)
    info = out / "version.cs"
    info.write_text(f'[assembly: System.Reflection.AssemblyVersion("{".".join(nums)}")]\n'
                    f'[assembly: System.Reflection.AssemblyFileVersion("{".".join(nums)}")]\n'
                    f'[assembly: System.Reflection.AssemblyInformationalVersion("{ver}")]\n', encoding="utf-8")
    for name, opts in (("openswx.exe", ["/target:winexe", "/r:System.Windows.Forms.dll"]),
                       ("openswx-cli.exe", ["/target:exe", "/define:CLI"])):
        subprocess.run([str(csc), "/nologo", *opts, f"/win32icon:{ico}", f"/out:{out / name}", str(src), str(info)],
                       check=True)


def build_windows(ver: str, zip_too: bool) -> None:
    app = DIST / "openswx"
    if not (app / "openswx.exe").exists():
        raise SystemExit(f"no app build at {app}")
    iscc = next((p for p in ISCC if p.exists()), None)
    if iscc:
        build_launchers(ver)
        subprocess.run([str(iscc), f"/DAppVersion={ver}", str(ROOT / "packaging" / "openswx.iss")], check=True, cwd=ROOT)
        print(f"installer: {DIST / f'openswx-setup-{ver}.exe'}")
    if zip_too or not iscc:
        base = DIST / f"openswx-{ver}-win64"
        out = shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir="openswx")
        print(f"portable zip: {out}" + ("" if iscc else "  (Inno Setup not installed: no installer built)"))


def build_macos(ver: str, zip_too: bool) -> None:
    app = DIST / "openswx.app"
    if not (app / "Contents" / "MacOS" / "openswx").exists():
        raise SystemExit(f"no app build at {app}")
    arch = platform.machine()  # arm64 or x86_64: the build is for the machine it runs on
    for name in ("LICENSE", "NOTICE"):  # (inside the bundle, before it is signed)
        shutil.copy(ROOT / name, app / "Contents" / "Resources" / f"{name}.txt")
    ident = os.environ.get("OPENSWX_CODESIGN_IDENTITY")
    sign = ["--sign", ident, "--options", "runtime", "--timestamp"] if ident else ["--sign", "-"]
    subprocess.run(["codesign", "--force", "--deep", *sign, str(app)], check=True)

    pkg = WORK / "pkg"
    shutil.rmtree(pkg, ignore_errors=True)
    root, scripts = pkg / "root" / "Applications", pkg / "scripts"
    root.mkdir(parents=True)
    scripts.mkdir()
    subprocess.run(["ditto", str(app), str(root / "openswx.app")], check=True)  # (keeps the signature intact)
    (scripts / "postinstall").write_text(POSTINSTALL, encoding="utf-8")
    (scripts / "postinstall").chmod(0o755)
    # always /Applications: not relocated to wherever another copy of the bundle happens to be
    components = pkg / "components.plist"
    components.write_bytes(plistlib.dumps([{"RootRelativeBundlePath": "Applications/openswx.app",
                                            "BundleIsRelocatable": False, "BundleIsVersionChecked": False,
                                            "BundleHasStrictIdentifier": True, "BundleOverwriteAction": "upgrade"}]))
    component = pkg / "openswx-component.pkg"
    subprocess.run(["pkgbuild", "--root", str(pkg / "root"), "--install-location", "/",
                    "--component-plist", str(components), "--scripts", str(scripts),
                    "--identifier", BUNDLE_ID, "--version", ver, str(component)], check=True)
    out = DIST / f"openswx-{ver}-macos-{arch}.pkg"
    installer_ident = os.environ.get("OPENSWX_INSTALLER_IDENTITY")
    subprocess.run(["productbuild", "--package", str(component),
                    *(["--sign", installer_ident] if installer_ident else []), str(out)], check=True)
    print(f"app: {app}\ninstaller: {out}")
    if zip_too:
        z = DIST / f"openswx-{ver}-macos-{arch}.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(z)], check=True)
        print(f"portable zip: {z}")


def _running_from(folder: Path) -> bool:
    """Whether an openswx executable inside `folder` is running (Windows locks a running .exe)."""
    if sys.platform != "win32" or not folder.exists():
        return False
    exes = {p.name.lower() for p in folder.rglob("openswx*.exe")}
    if not exes:
        return False
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    return any(f'"{e}"' in out for e in exes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-app", action="store_true", help="reuse the app in dist instead of rebuilding it")
    ap.add_argument("--zip", action="store_true", help="also make the portable zip when the installer is built")
    a = ap.parse_args()
    if not a.skip_app and _running_from(DIST):
        print(f"openswx is running from {DIST}: close it first (Windows cannot replace a running program)", file=sys.stderr)
        return 1
    if not a.skip_app:
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "openswx.spec"), "--noconfirm",
                        "--distpath", str(DIST), "--workpath", str(WORK)], check=True, cwd=ROOT)
    if sys.platform == "darwin":
        build_macos(version(), a.zip)
    else:
        build_windows(version(), a.zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
