"""The application log and the diagnostic report (openswx/system/applog.py)."""
import logging
import zipfile

import pytest

from openswx.system import applog
from openswx import session as S


@pytest.fixture
def logfile(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSWX_LOG", "info")
    old, applog.log.handlers = applog.log.handlers, []
    monkeypatch.setattr(applog, "_ready", False)
    applog.setup()
    yield tmp_path / "logs" / "openswx.log"
    for h in applog.log.handlers:
        h.close()
    applog.log.handlers = old
    applog.log.setLevel(logging.NOTSET)


def test_commands_and_errors_are_logged_and_reported(logfile, tmp_path):
    S.new_part("Logged")
    with pytest.raises(S.CommandError):
        S.extrude("NoSuchSketch", 5)
    for h in applog.log.handlers:
        h.flush()
    text = logfile.read_text(encoding="utf-8")
    assert "new_part (agent)" in text
    assert "extrude (agent) failed" in text and "Traceback" in text and "unknown sketch" in text

    r = S.save_diagnostic_report(str(tmp_path / "report"))
    with zipfile.ZipFile(r["saved"]) as z:
        names = set(z.namelist())
        assert {"logs/openswx.log", "versions.json", "activity.json", "documents.json"} <= names
        assert not any(n.startswith("documents/") for n in names)  # design data only on request
    r = S.save_diagnostic_report(str(tmp_path / "full.zip"), include_documents=True)
    with zipfile.ZipFile(r["saved"]) as z:
        assert "documents/Logged.oswprt" in z.namelist()
