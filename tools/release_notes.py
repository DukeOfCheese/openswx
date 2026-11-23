"""Release notes for a version: its CHANGELOG.md section plus how to install the downloads.

    python tools/release_notes.py 0.1.0 > notes.md      (the release workflow does this for each v* tag)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INSTALL = """
## Install

| | Installer | Without installing |
|---|---|---|
| Windows 10/11 (x64) | `openswx-setup-{v}.exe` | `openswx-{v}-win64.zip`, then run `openswx.exe` |
| macOS, Apple silicon | `openswx-{v}-macos-arm64.pkg` | `openswx-{v}-macos-arm64.zip` |
| macOS, Intel | `openswx-{v}-macos-x86_64.pkg` | `openswx-{v}-macos-x86_64.zip` |
| Any platform, Python 3.11+ | from source: [README](https://github.com/DukeOfCheese/openswx#from-source-any-platform-browser-ui) | |

The builds are not code-signed. On Windows, SmartScreen asks once: *More info > Run anyway*. On macOS,
right-click `openswx.app` > *Open*, or run `xattr -dr com.apple.quarantine /Applications/openswx.app`.

To connect an AI agent (MCP), see [Agents: how they get access](https://github.com/DukeOfCheese/openswx#agents-how-they-get-access).
openswx is an independent open-source project, not affiliated with Dassault Systemes or Anthropic.
"""


def section(version: str, changelog: str) -> str:
    """The CHANGELOG section headed `## <version>` (up to the next `## `), without its heading."""
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip()


def notes(version: str) -> str:
    version = version.lstrip("v")
    body = section(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    return body + "\n" + INSTALL.format(v=version)


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))
