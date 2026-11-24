"""PDM: vault setup, check-in/out, workflow states, history, search and where used."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from openswx.core.document import DocumentError
from openswx.session import APP, command

from .common import openswx_home


def _vault():
    from openswx.manufacturing.ecosystem import Vault
    cfg = openswx_home() / "pdm.json"
    root = None
    if cfg.is_file():
        try:
            root = json.loads(cfg.read_text(encoding="utf-8")).get("vault")
        except ValueError:
            root = None
    return Vault(Path(root) if root else openswx_home() / "vault")


def _pdm(fn):
    from openswx.manufacturing.ecosystem import VaultError
    try:
        return fn(_vault())
    except VaultError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="pdm")
def pdm_set_vault(path: str) -> dict:
    """PDM: use the vault folder at `path` (shared network folders work; created if missing)."""
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    openswx_home().mkdir(parents=True, exist_ok=True)
    (openswx_home() / "pdm.json").write_text(json.dumps({"vault": str(root)}), encoding="utf-8")
    return {"vault": str(root)}


@command(mutates=False, category="pdm")
def pdm_list() -> dict:
    """PDM: the vault's files with their latest version, revision, workflow state and who has them checked out."""
    return _pdm(lambda v: {"vault": str(v.root), "files": v.files()})


@command(mutates=False, category="pdm")
def pdm_check_in(path: str | None = None, comment: str = "", keep_checked_out: bool = False) -> dict:
    """PDM Check In: store the file (default the active document, which must be saved) as the next version in
    the vault with a comment; new files are added. The file must be checked out to you (except when new)."""
    if path is None:
        doc = APP.active_doc
        if not getattr(doc, "path", None):
            raise DocumentError("save the document first")
        if doc.dirty:
            raise DocumentError("the document has unsaved changes: save it first")
        path = doc.path
    return _pdm(lambda v: v.check_in(Path(path), comment, keep_checked_out))


@command(mutates=False, category="pdm")
def pdm_check_out(name: str, dest_dir: str | None = None, open: bool = True) -> dict:
    """PDM Check Out: lock a vault file for you and copy its latest version to dest_dir (default the local
    workspace); opens it unless open=False. Others can view but not check in until you check it back in."""
    dest = Path(dest_dir) if dest_dir else openswx_home() / "workspace"
    out = _pdm(lambda v: v.check_out(name, dest))
    if open and Path(out["path"]).suffix in (".oswprt", ".oswasm", ".oswdrw"):
        from openswx.session import open_part
        open_part(out["path"])
    return out


@command(mutates=False, category="pdm")
def pdm_undo_check_out(name: str) -> dict:
    """PDM Undo Check Out: release your lock without storing a new version."""
    return _pdm(lambda v: v.undo_check_out(name))


@command(mutates=False, category="pdm")
def pdm_get_version(name: str, version: int | None = None, dest_dir: str | None = None) -> dict:
    """PDM Get Version: copy a version (default the latest) of a vault file to dest_dir (read-only use; no lock)."""
    dest = Path(dest_dir) if dest_dir else openswx_home() / "workspace"
    return {"path": str(_pdm(lambda v: v.get(name, version, dest)))}


@command(mutates=False, category="pdm")
def pdm_change_state(name: str, state: Literal["Work in Progress", "Pending Approval", "Released"],
                     comment: str = "") -> dict:
    """PDM workflow: Work in Progress -> Pending Approval -> Released (or back to Work in Progress). Releasing
    gives the next revision letter (A, B, ...) and blocks changes until it goes back to Work in Progress."""
    return _pdm(lambda v: v.change_state(name, state, comment))


@command(mutates=False, category="pdm")
def pdm_history(name: str) -> dict:
    """PDM History: versions (comment, user, date, size), revisions and the check-in/out and state log."""
    return _pdm(lambda v: v.history(name))


@command(mutates=False, category="pdm")
def pdm_search(text: str) -> dict:
    """PDM Search: vault files whose name, check-in comments or custom properties contain the text."""
    return _pdm(lambda v: {"files": v.search(text)})


@command(mutates=False, category="pdm")
def pdm_where_used(name: str) -> dict:
    """PDM Where Used: vault assemblies that use the file."""
    return _pdm(lambda v: {"name": name, "used_in": v.where_used(name)})


__all__ = ["pdm_set_vault", "pdm_list", "pdm_check_in", "pdm_check_out", "pdm_undo_check_out",
           "pdm_get_version", "pdm_change_state", "pdm_history", "pdm_search", "pdm_where_used"]
