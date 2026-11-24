"""Windows file associations for openswx, per user (HKCU\\Software\\Classes - no administrator rights).

`openswx register-file-types` makes .oswprt / .oswasm open in openswx (double-click) with its icon, and adds
openswx to "Open with" for SOLIDWORKS and neutral CAD files; `openswx unregister-file-types` removes exactly
what was added. The installer (packaging/openswx.iss) registers the same keys machine-wide.
"""

from __future__ import annotations

import sys

PROGIDS = {  # extension -> (ProgID, description); openswx's own formats open in it by default
    ".oswprt": ("openswx.Part", "openswx Part"),
    ".oswasm": ("openswx.Assembly", "openswx Assembly"),
    ".oswdrw": ("openswx.Drawing", "openswx Drawing"),
}
OPEN_WITH = (".sldprt", ".sldasm", ".slddrw", ".step", ".stp", ".igs", ".iges", ".x_t", ".x_b")  # offered in "Open with", not taken over
APP_KEY = r"Software\Classes\Applications\openswx.exe"


def _command(exe: str) -> str:
    return f'"{exe}" "%1"'


def planned_keys(exe: str) -> list[tuple[str, str | None, str]]:
    """The (key, value name, data) entries registration writes, under HKEY_CURRENT_USER."""
    out = []
    for ext, (progid, desc) in PROGIDS.items():
        out += [(rf"Software\Classes\{ext}", None, progid),
                (rf"Software\Classes\{progid}", None, desc),
                (rf"Software\Classes\{progid}\DefaultIcon", None, f'"{exe}",0'),
                (rf"Software\Classes\{progid}\shell\open\command", None, _command(exe))]
    out += [(APP_KEY, "FriendlyAppName", "openswx"),
            (rf"{APP_KEY}\shell\open\command", None, _command(exe))]
    for ext in (*PROGIDS, *OPEN_WITH):
        out += [(rf"{APP_KEY}\SupportedTypes", ext, ""),
                (rf"Software\Classes\{ext}\OpenWithProgids", PROGIDS.get(ext, ("openswx.File",))[0], "")]
    out += [(r"Software\Classes\openswx.File", None, "CAD file (openswx)"),
            (r"Software\Classes\openswx.File\DefaultIcon", None, f'"{exe}",0'),
            (r"Software\Classes\openswx.File\shell\open\command", None, _command(exe))]
    return out


def _winreg():
    if sys.platform == "darwin":
        raise OSError("on macOS the file types come with openswx.app (its Info.plist): macOS registers them when "
                      "the app is in /Applications")
    if sys.platform != "win32":
        raise OSError("file associations are only registered on Windows")
    import winreg
    return winreg


def _notify() -> None:
    """Tell Explorer the associations changed (SHChangeNotify SHCNE_ASSOCCHANGED)."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def register(exe: str | None = None, reg=None) -> list[str]:
    """Write the associations for `exe` (default: this program). Returns the keys written."""
    reg = reg or _winreg()
    exe = exe or sys.executable
    keys = []
    for key, name, data in planned_keys(exe):
        with reg.CreateKey(reg.HKEY_CURRENT_USER, key) as k:
            reg.SetValueEx(k, name or "", 0, reg.REG_SZ, data)
        keys.append(key)
    _notify()
    return sorted(set(keys))


def unregister(reg=None) -> list[str]:
    """Remove what register() wrote (other programs' entries for the same extensions are left alone)."""
    reg = reg or _winreg()
    removed = []

    def delete_tree(root: str) -> None:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, root, 0, reg.KEY_ALL_ACCESS) as k:
                subs = []
                i = 0
                while True:
                    try:
                        subs.append(reg.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
            for s in subs:
                delete_tree(rf"{root}\{s}")
            reg.DeleteKey(reg.HKEY_CURRENT_USER, root)
            removed.append(root)
        except OSError:
            pass

    for progid, _ in PROGIDS.values():
        delete_tree(rf"Software\Classes\{progid}")
    delete_tree(r"Software\Classes\openswx.File")
    delete_tree(APP_KEY)
    for ext, (progid, _) in PROGIDS.items():
        try:  # the extension's default, only if it is still ours
            with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}", 0, reg.KEY_ALL_ACCESS) as k:
                if reg.QueryValueEx(k, "")[0] == progid:
                    reg.DeleteValue(k, "")
                    removed.append(rf"Software\Classes\{ext}")
        except OSError:
            pass
    for ext in (*PROGIDS, *OPEN_WITH):
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids", 0, reg.KEY_ALL_ACCESS) as k:
                reg.DeleteValue(k, PROGIDS.get(ext, ("openswx.File",))[0])
        except OSError:
            pass
    _notify()
    return removed
