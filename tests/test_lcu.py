import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import lcu


def test_creds_from_process_runs_hidden(monkeypatch):
    """The process scan runs while the client is closed: it must not flash a console window."""
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(lcu.subprocess, "run", fake_run)
    with pytest.raises(lcu.LcuError):  # no LeagueClientUx found: that's fine, just don't flash
        lcu._creds_from_process()
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
