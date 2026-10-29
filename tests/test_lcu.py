import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from data import lcu


def fake_scan(monkeypatch, stdout=""):
    """Stand in for the PowerShell scan; counts calls."""
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(lcu.subprocess, "run", run)
    return calls


@pytest.fixture(autouse=True)
def fresh_scan_state(monkeypatch):
    monkeypatch.setattr(lcu, "_scan_failed_at", None)


def test_creds_from_process_runs_hidden(monkeypatch):
    """The process scan runs while the client is closed: it must not flash a console window."""
    calls = fake_scan(monkeypatch)
    with pytest.raises(lcu.LcuError):  # no LeagueClientUx found: that's fine, just don't flash
        lcu._creds_from_process()
    assert calls[0]["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_failed_scan_is_not_repeated(monkeypatch):
    """The reconnect loop retries every few seconds while the client is closed: scan once, not
    on every retry."""
    calls = fake_scan(monkeypatch)
    for _ in range(5):
        with pytest.raises(lcu.LcuError):
            lcu._creds_from_process()
    assert len(calls) == 1


def test_successful_scan_resets_the_backoff(monkeypatch):
    """A scan that finds the client clears the failed-scan memory: the next closed-client call
    really scans again instead of trusting the stale 'not found'."""
    outputs = ["--app-port=123 --remoting-auth-token=tok", ""]
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=outputs.pop(0), stderr="")

    monkeypatch.setattr(lcu.subprocess, "run", run)
    lcu._creds_from_process()  # client up: scan succeeds
    with pytest.raises(lcu.LcuError):  # client closes: the next scan really runs, and finds none
        lcu._creds_from_process()
    with pytest.raises(lcu.LcuError):  # and that failure is then remembered
        lcu._creds_from_process()
    assert len(calls) == 2
