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
# Netgen (the optional tetrahedral mesher, fea_tet.py) is left out of the desktop builds for now: its own
# OpenCASCADE libraries have the same file names as OCP's (TKBRep.dll ...) and would overwrite them in the one
# bundle folder. Without it the app reports the mesher missing and meshes with voxels.
NOT_BUNDLED = ["netgen", "pyngcore"]
hidden += ["openswx.fea_tet", "openswx.desktop", "openswx.web_server", "openswx.mcp_server", "openswx.assembly", "openswx.drawing",
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
