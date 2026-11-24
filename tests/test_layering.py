"""Layering: core must not import commands, and command modules take helpers from commands.support."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "openswx"
COMMANDS = "openswx.commands"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package(path: Path) -> str:
    name = _module_name(path)
    return name if path.name == "__init__.py" else name.rpartition(".")[0]


def _imports(path: Path) -> list[str]:
    """Every module an import in `path` names, absolute (relative imports resolved against its package)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package(path).split(".")
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # (from ..core.geom import: relative to the module's package)
                base = ".".join(package[:len(package) - node.level + 1])
                out.append(".".join(p for p in (base, node.module) if p))
            elif node.module:
                out.append(node.module)
    return out


def _python_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts)


def test_core_does_not_import_the_command_layer():
    for path in _python_files(ROOT / "core"):
        for name in _imports(path):
            assert name != COMMANDS and not name.startswith(COMMANDS + "."), (
                f"{path.relative_to(ROOT.parent)} imports {name}: core must not depend on the command layer "
                "(move the helper down to core instead)")


def test_session_registers_the_command_package_only():
    for name in _imports(ROOT / "session.py"):
        assert not name.startswith(COMMANDS + "."), (
            f"session.py imports {name} by name: it registers the package (from openswx.commands import *) "
            "and must not pull a command module in directly")


def test_command_modules_take_helpers_from_commands_support():
    """Underscore helpers come from commands.support, not session (session's own _TL is the exception)."""
    for path in _python_files(ROOT / "commands"):
        if path.name == "support.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "openswx.session":
                stray = sorted(a.name for a in node.names if a.name.startswith("_") and a.name != "_TL")
                assert not stray, f"{path.name} imports {stray} from session; they belong in commands.support"
