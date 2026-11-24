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
