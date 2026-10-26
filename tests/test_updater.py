import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater


def test_startup_from_source_does_not_raise():
    updater.startup()


def test_exe_path_from_source_is_sys_executable():
    assert updater.exe_path() == sys.executable


def test_unsupported_from_source_is_noop():
    calls = []
    u = updater.Updater(lambda: calls.append(1))
    assert u.state == "unsupported"
    u.check_async()
    u.apply_and_restart()
    u.apply_on_exit()
    assert u.state == "unsupported" and calls == []


class FakeMgr:
    def __init__(self, info=None, fail=False):
        self.info, self.fail = info, fail
        self.calls = []

    def check_for_updates(self):
        if self.fail:
            raise RuntimeError("offline")
        return self.info

    def download_updates(self, info):
        self.calls.append("download")

    def apply_updates_and_restart(self, info):
        self.calls.append("restart")

    def wait_exit_then_apply_updates(self, info, silent, restart):
        self.calls.append(("exit", silent, restart))


def make(mgr):
    states = []
    u = updater.Updater()
    u.on_change = lambda: states.append(u.state)
    u._mgr, u.state = mgr, "idle"
    return u, states


def info(ver="0.1.9"):
    return SimpleNamespace(TargetFullRelease=SimpleNamespace(Version=ver))


def test_update_flow_and_apply():
    mgr = FakeMgr(info())
    u, states = make(mgr)
    u._check()
    assert states == ["checking", "downloading", "ready"]
    assert u.version == "0.1.9"
    u.apply_on_exit()
    u.apply_and_restart()
    assert mgr.calls == ["download", ("exit", True, False), "restart"]


def test_no_update_goes_idle():
    u, states = make(FakeMgr(None))
    u._check()
    assert states == ["checking", "idle"]


def test_network_error_is_swallowed():
    u, states = make(FakeMgr(fail=True))
    u._check()
    assert states == ["checking", "error"]


def test_apply_noop_unless_ready():
    mgr = FakeMgr(info())
    u, _ = make(mgr)
    u.apply_and_restart()
    u.apply_on_exit()
    assert mgr.calls == []


def test_exe_path_finds_the_velopack_stub_by_any_name(tmp_path, monkeypatch):
    (tmp_path / "current").mkdir()
    for name in ("Update.exe", "LoL Companion.exe", "current/LoLCompanion.exe"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "current" / "LoLCompanion.exe"))
    assert updater.exe_path() == str(tmp_path / "LoL Companion.exe")
