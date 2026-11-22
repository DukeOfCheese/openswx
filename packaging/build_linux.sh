#!/usr/bin/env bash
# Linux build: a self-contained folder made by PyInstaller, packed as openswx-<version>-linux-<arch>.tar.gz.
# Unpack anywhere and run ./openswx/openswx (the window) or ./openswx/openswx-cli (mcp, serve, convert, ...).
#
#   bash packaging/build_linux.sh            # from the repository root, in a virtual environment
#
# Needs the system libraries of the geometry kernel and of a WebKit or Qt web view, e.g. on Debian / Ubuntu:
#   sudo apt-get install -y libgl1 libglu1-mesa libegl1 python3-gi gir1.2-webkit2-4.1
# (or `pip install pywebview[qt]` instead of the GTK packages). Without a web view the window does not open;
# `openswx-cli serve --open` still gives the full UI in a browser.
#
# STATUS: written from the Windows and macOS builds; not yet run on a Linux machine (see docs/GAPS.md, I1).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python}"
VERSION="$("$PY" - <<'EOF'
import re, pathlib
print(re.search(r'^version\s*=\s*"([^"]+)"', pathlib.Path("pyproject.toml").read_text(encoding="utf-8"), re.M).group(1))
EOF
)"
ARCH="$(uname -m)"
OUT="dist-new"

"$PY" -m pip install -e . "pyinstaller>=6" "pywebview>=6"
"$PY" packaging/make_icon.py || true   # (the icon is only used by the Windows and macOS builds)
"$PY" -m PyInstaller packaging/openswx.spec --noconfirm --distpath "$OUT" --workpath build-new

test -x "$OUT/openswx/openswx-cli"
cp LICENSE "$OUT/openswx/LICENSE.txt"
cp NOTICE "$OUT/openswx/NOTICE.txt"
# the build must at least answer from the command line before it is packed
"$OUT/openswx/openswx-cli" --help >/dev/null
"$OUT/openswx/openswx-cli" token >/dev/null

TARBALL="$OUT/openswx-$VERSION-linux-$ARCH.tar.gz"
tar -C "$OUT" -czf "$TARBALL" openswx
echo "$TARBALL"
