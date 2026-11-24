"""Is there a newer release? Asked only when the user asks (Help > Check for Updates): nothing is sent or
fetched on its own, and nothing is downloaded or installed - the answer is a version number and a link.

The releases are those of the GitHub repository named by the `update_repository` setting ("owner/name") or
`OPENSWX_UPDATE_REPO`. With neither set there is nowhere to ask, and the check says so.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


def repository() -> str | None:
    repo = os.environ.get("OPENSWX_UPDATE_REPO", "").strip()
    if not repo:
        from openswx.system import settings
        repo = str(settings.get("update_repository")).strip()
    return repo if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) else None


def version_key(v: str) -> tuple:
    """'v1.2.10' -> (1, 2, 10): numeric parts compared as numbers; a pre-release suffix sorts before the release."""
    v = v.strip().lstrip("vV")
    main, _, pre = v.partition("-")
    nums = tuple(int(x) for x in re.findall(r"\d+", main))
    return (*nums, 0 if pre else 1, pre)


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "openswx-update-check"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 - a fixed https URL built from the setting
        return json.loads(r.read().decode("utf-8"))


def check(fetch=_fetch) -> dict:
    from openswx import __version__
    repo = repository()
    if repo is None:
        return {"configured": False, "current": __version__,
                "message": "no release feed is set: put the GitHub repository (owner/name) in User Settings > update_repository"}
    try:
        latest = fetch(f"https://api.github.com/repos/{repo}/releases/latest")
        tag = str(latest["tag_name"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
        return {"configured": True, "current": __version__, "error": f"could not reach the release feed of {repo}: {exc}"}
    newer = version_key(tag) > version_key(__version__)
    return {"configured": True, "current": __version__, "latest": tag.lstrip("vV"), "update_available": newer,
            "url": latest.get("html_url"), "published": latest.get("published_at"),
            "message": f"version {tag.lstrip('vV')} is available" if newer else "this is the latest release"}
