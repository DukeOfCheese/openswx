import os

# tests read and rebuild files afresh: results cached on disk by earlier runs must not answer for them
import tempfile

# the user's own openswx folder (settings, token, custom materials, logs) is not the tests' to read or write
os.environ.setdefault("OPENSWX_HOME", tempfile.mkdtemp(prefix="openswx-tests-"))
os.environ.setdefault("OPENSWX_CACHE", "off")
os.environ.setdefault("OPENSWX_PLUGINS", "off")  # (plugins installed on this machine are not under test)
os.environ.setdefault("OPENSWX_AUTOSAVE", "off")
os.environ.setdefault("OPENSWX_LOG", "off")  # (test runs do not write to the user's application log)  # (no autosave thread or recovery files from test runs)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_application():
    """Every test starts with no open documents: a part left open by one test must not be picked up by name
    (insert_component("Plate")) in the next."""
    from openswx import session as S
    S.APP.documents.clear()
    S.APP.active = None
    S.APP.context = None
    S.APP.macro = None
    yield
