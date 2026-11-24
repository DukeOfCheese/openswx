"""Live collaboration: cloud documents that several people edit at once through an openswx-collab server.

Optional: it needs the `collab` extra (`pip install openswx[collab]`: the wire protocol package
`openswx-collab-protocol` and `websockets`). Without it, or while no document is shared, openswx works exactly
as it does alone: nothing here is imported until a collab_* command runs.

    auth.py              server address, sign-in (password, or Google / GitHub in the browser), the saved token
    client.py            one connection per cloud document: sends local changes, applies everyone else's
    backup.py            local .oswprt snapshots of every cloud document
    commands_collab.py   the collab_* commands
"""
from __future__ import annotations

MISSING = ("live collaboration needs the collab extra: pip install openswx[collab] (packages openswx-collab-protocol "
           "and websockets)")


def available() -> bool:
    """Are the protocol package and the WebSocket client installed?"""
    try:
        import openswx_collab_protocol  # noqa: F401
        import websockets.sync.client  # noqa: F401
    except ImportError:
        return False
    return True
