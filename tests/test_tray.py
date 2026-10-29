import winreg

import pytest

from desktop import tray

TEST_VALUE = "LoLCompanionTest"


@pytest.mark.parametrize("state,want", [
    ("unsupported", None),
    ("ready", ("Restart to update", True)),
    ("checking", ("Checking for updates…", False)),
    ("downloading", ("Downloading update…", False)),
    ("idle", ("Check for updates", True)),
    ("error", ("Check for updates", True)),
])
def test_update_item(state, want):
    assert tray.update_item(state) == want


def test_autostart_roundtrip():
    try:
        assert not tray.autostart_enabled(TEST_VALUE)
        tray.set_autostart(True, TEST_VALUE, r"C:\x y\app.exe")
        assert tray.autostart_enabled(TEST_VALUE)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, tray.RUN_KEY) as k:
            assert winreg.QueryValueEx(k, TEST_VALUE)[0] == r'"C:\x y\app.exe"'
        tray.set_autostart(False, TEST_VALUE)
        assert not tray.autostart_enabled(TEST_VALUE)
        tray.set_autostart(False, TEST_VALUE)  # already gone: no error
    finally:
        tray.set_autostart(False, TEST_VALUE)
