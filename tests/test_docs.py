"""Generated documentation is current, and every command is documented and tested."""
import re
import subprocess
import sys
from pathlib import Path

from openswx import session as S

ROOT = Path(__file__).resolve().parent.parent

# Commands with no test naming them. This list may only shrink: a new command needs a test.
UNTESTED: set[str] = set()


def test_generated_docs_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_command_docs.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_command_has_a_description():
    short = [n for n, m in S.COMMANDS.items() if len((m["doc"] or "").strip()) < 15]
    assert not short, f"commands without a usable description (it is the MCP tool description): {short}"


def test_every_command_is_named_in_a_test():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("*.py") if p.name not in ("test_docs.py", "conftest.py"))
    missing = {n for n in S.COMMANDS if not re.search(rf"\b{re.escape(n)}\b", text)}
    assert missing - UNTESTED == set(), f"commands without a test: {sorted(missing - UNTESTED)}"
    assert UNTESTED - missing == set(), f"now tested, remove from UNTESTED: {sorted(UNTESTED - missing)}"
