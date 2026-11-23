# PyInstaller spec: builds dist/openswx/{openswx.exe (windowed app), openswx-cli.exe (console: mcp/sldinfo)};
# on macOS also dist/openswx.app (both executables in Contents/MacOS, the file types in its Info.plist)
# Build:  .venv\Scripts\pyinstaller packaging\openswx.spec --noconfirm
import importlib.util
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"

datas, binaries, hidden = [(str(ROOT / "openswx" / "web"), "openswx/web")], [], []
for pkg in ("build123d", "OCP", "webview", "uvicorn", "mcp", "ezdxf", "ocpsvg", "anytree", "lib3mf", "bd_materials",
            "ocp_gordon", "svgpathtools", "trianglesolver", "threejs_materials", "webcolors", "fontTools"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hidden += h
# Netgen, the optional tetrahedral mesher (fea_tet.py), when installed. Windows: its packages, the libraries
# its wheel keeps beside them (netgen_mesher.libs, found by its own __init__) and its OpenCASCADE build, which the
# netgen-occt wheel installs into <env>/bin. Those keep their plain names (TKBRep.dll ...), while OCP's are
# renamed with a hash, so they cannot collide; they go to their own folder, netgen_occt, which the meshing child
# adds to its library path before it imports netgen (fea_tet._mesh_worker), where it also hides netgen-occt's
# metadata from netgen (with it, netgen looks for the libraries at the wheel's own paths, outside the bundle).
# Checked 2026-11-23: a frozen Windows build meshes and solves a tetrahedral static study.
# macOS: netgen-occt installs its libraries into <env>/lib (libTK*.dylib); they go to netgen_occt too, and the
# meshing child loads them before netgen (macOS then reuses them by install name). The release workflow checks
# both platforms with packaging/smoke_study.py. Linux builds leave Netgen out for now.
NOT_BUNDLED = []
_ng = importlib.util.find_spec("netgen")
if _ng is not None and sys.platform in ("win32", "darwin"):
    for pkg in ("netgen", "pyngcore"):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    _site = Path(list(_ng.submodule_search_locations)[0]).parent
    for libs in ("netgen_mesher.libs", "pyngcore.libs"):
        if (_site / libs).is_dir():
            binaries += [(str(f), libs) for f in (_site / libs).iterdir() if f.is_file()]
    if sys.platform == "win32":
        binaries += [(str(f), "netgen_occt") for f in (Path(sys.prefix) / "bin").glob("TK*.dll")]
    else:
        binaries += [(str(f), "netgen_occt") for f in (Path(sys.prefix) / "lib").glob("libTK*.dylib")]
else:
    NOT_BUNDLED = ["netgen", "pyngcore"]
hidden += ["openswx.fea_tet", "openswx.mcp_bridge", "openswx.desktop", "openswx.web_server", "openswx.mcp_server", "openswx.assembly", "openswx.drawing",
           "openswx.sldprt", "openswx.render", "openswx.equations", "openswx.sw_tree", "openswx.sw_sketch",
           "openswx.sw_rebuild", "openswx.drawing_sheets", "openswx.fileassoc", "openswx.sw_drawing",
           "openswx.advanced", "openswx.commands_more", "openswx.drawing_more", "openswx.fea_more",
           "openswx.analysis_more", "openswx.ecosystem", "openswx.piping", "openswx.cfd", "openswx.cam", "openswx.plastics", "openswx.inspection", "openswx.electrical", "openswx.commands_mfg",
           "openswx.naming", "openswx.cache", "openswx.security", "openswx.recovery", "openswx.commands_app", "openswx.migrate", "openswx.applog", "openswx.tolerance", "openswx.commands_tol", "openswx.commands_review", "openswx.cli", "openswx.snapshots", "openswx.jobs", "openswx.settings", "openswx.commands_settings", "openswx.plugins", "openswx.isolated", "openswx.commands_asm", "openswx.commands_blocks", "openswx.commands_simreport", "openswx.commands_std", "openswx.fcstd", "openswx.updates", "openswx.api", "openswx.commands_document", "openswx.commands_sketch", "openswx.commands_features", "openswx.commands_assembly", "openswx.commands_drawing", "openswx.commands_simulation"]

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "pytest", "jedi", "parso", *NOT_BUNDLED], noarchive=False)
# (build123d needs IPython; IPython uses jedi only for completion, when installed: 22 MB, and the deepest paths)
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / ("openswx.icns" if IS_MAC else "openswx.ico"))
# (argv_emulation: a file opened from Finder arrives as an Apple event; this turns it into sys.argv, as on Windows)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openswx", console=False, icon=icon, argv_emulation=IS_MAC)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openswx-cli", console=True, icon=icon)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="openswx")

if IS_MAC:
    # the same file types as the Windows installer (loaded by path: the package __init__ installs import stubs)
    spec = importlib.util.spec_from_file_location("_fileassoc", ROOT / "openswx" / "fileassoc.py")
    fa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fa)
    version = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    own = [{"CFBundleTypeName": desc, "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner",
            "LSItemContentTypes": [f"org.openswx.{ext[1:]}"]} for ext, (_, desc) in fa.PROGIDS.items()]
    # SOLIDWORKS and neutral CAD files: offered in "Open With", their default program is left alone
    other = [{"CFBundleTypeName": "CAD file (openswx)", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
              "CFBundleTypeExtensions": [ext[1:] for ext in fa.OPEN_WITH]}]
    app = BUNDLE(coll, name="openswx.app", icon=icon, bundle_identifier="org.openswx.openswx", version=version,
                 info_plist={
                     "CFBundleName": "openswx", "CFBundleDisplayName": "openswx",
                     "CFBundleShortVersionString": version, "CFBundleVersion": version,
                     "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
                     "CFBundleDocumentTypes": own + other,
                     "UTExportedTypeDeclarations": [
                         {"UTTypeIdentifier": f"org.openswx.{ext[1:]}", "UTTypeDescription": desc,
                          "UTTypeConformsTo": ["public.data"],
                          "UTTypeTagSpecification": {"public.filename-extension": [ext[1:]]}}
                         for ext, (_, desc) in fa.PROGIDS.items()],
                 })
