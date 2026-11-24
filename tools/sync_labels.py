"""Create or update the repository's issue labels from .github/labels.yml, with the GitHub CLI.

    python tools/sync_labels.py [--repo OWNER/NAME] [--dry-run]

Labels already on GitHub that are not in the file are left alone. Needs `gh` signed in with write access.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path = ROOT / ".github" / "labels.yml") -> list[dict]:
    """The labels in the file: one `- {name: ..., color: ..., description: "..."}` per line."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*-\s*\{name:\s*"?([^",]+?)"?,\s*color:\s*([0-9a-fA-F]{6}),\s*description:\s*"([^"]*)"\}\s*$', line)
        if m:
            out.append({"name": m[1].strip(), "color": m[2].lower(), "description": m[3]})
        elif line.strip().startswith("-"):
            raise ValueError(f"cannot read label line: {line!r}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    for lab in load():
        cmd = ["gh", "label", "create", lab["name"], "--color", lab["color"], "--description", lab["description"], "--force"]
        if a.repo:
            cmd += ["--repo", a.repo]
        print(" ".join(cmd) if a.dry_run else f"label {lab['name']}")
        if not a.dry_run:
            subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
