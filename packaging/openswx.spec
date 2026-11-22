# PyInstaller spec: builds dist/openswx/{openswx.exe (windowed app), openswx-cli.exe (console: mcp/sldinfo)}
# Build:  .venv\Scripts\pyinstaller packaging\openswx.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
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
hidden += ["openswx.desktop", "openswx.web_server", "openswx.mcp_server", "openswx.assembly", "openswx.drawing",
           "openswx.sldprt", "openswx.render", "openswx.equations", "openswx.sw_tree", "openswx.sw_sketch",
           "openswx.sw_rebuild", "openswx.drawing_sheets", "openswx.fileassoc", "openswx.sw_drawing",
           "openswx.advanced", "openswx.commands_more", "openswx.drawing_more", "openswx.fea_more",
           "openswx.analysis_more", "openswx.ecosystem", "openswx.piping", "openswx.cfd", "openswx.cam", "openswx.plastics", "openswx.inspection", "openswx.electrical", "openswx.commands_mfg",
           "openswx.naming", "openswx.cache", "openswx.security", "openswx.recovery", "openswx.commands_app", "openswx.migrate", "openswx.applog", "openswx.tolerance", "openswx.commands_tol", "openswx.commands_review", "openswx.cli", "openswx.snapshots", "openswx.jobs", "openswx.settings", "openswx.commands_settings", "openswx.plugins", "openswx.isolated", "openswx.commands_asm", "openswx.commands_blocks"]

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "pytest", "jedi", "parso"], noarchive=False)
# (build123d needs IPython; IPython uses jedi only for completion, when installed: 22 MB, and the deepest paths)
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / "openswx.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openswx", console=False, icon=icon)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openswx-cli", console=True, icon=icon)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="openswx")
