"""Paths and document edits shared by the commands in this package."""
from __future__ import annotations

import os
from pathlib import Path


def openswx_home() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx")


def _doc_edit(doc, fn):
    doc._checkpoint()
    out = fn(doc)
    doc.revision += 1
    doc.dirty = True
    return out
