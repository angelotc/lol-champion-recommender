"""System tray icon: show/hide the panel, update status, settings, start with Windows, quit."""
from __future__ import annotations

import logging
import os
import winreg
from typing import Callable

import pystray
from PIL import Image

import paths
import updater as upd

NAME = "LoL Companion"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LoLCompanion"
READY_MSG = "Update ready — it installs when you quit, or pick Restart to update."
# updater state -> (label, enabled); "unsupported" is hidden, anything else is "Check for updates"
BUSY = {"checking": "Checking for updates…", "downloading": "Downloading update…"}

log = logging.getLogger("tray")


def autostart_enabled(value: str = RUN_VALUE) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, value)
        return True
    except OSError:
        return False


def set_autostart(on: bool, value: str = RUN_VALUE, exe: str | None = None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, value, 0, winreg.REG_SZ, f'"{exe or upd.exe_path()}"')
        else:
            try:
                winreg.DeleteValue(k, value)
            except FileNotFoundError:
                pass


def update_item(state: str) -> tuple[str, bool] | None:
    """(label, enabled) of the update menu item for an updater state, None to hide it."""
    if state == "unsupported":
        return None
    if state == "ready":
        return "Restart to update", True
    if state in BUSY:
        return BUSY[state], False
    return "Check for updates", True


class Tray:
    def __init__(self, toggle: Callable[[], None], quit: Callable[[], None], updater: upd.Updater,
                 restart: Callable[[], None] | None = None,
                 settings: Callable[[], None] | None = None):
        self.updater, self._ready_shown = updater, False
        self.restart = restart or updater.apply_and_restart
        self.settings = settings or (lambda: os.startfile(paths.config_path()))
        self.icon = pystray.Icon(
            "LoLCompanion", Image.open(paths.RESOURCES / "assets" / "icon.png"), NAME,
            pystray.Menu(*self._items(toggle, quit)),
        )

    def _items(self, toggle, quit):
        u = self.updater
        item = pystray.MenuItem
        label = lambda _: (update_item(u.state) or ("", False))[0]
        yield item("Show / hide panel (Ctrl+X)", lambda: toggle(), default=True)
        yield item(
            label, lambda: self.restart() if u.state == "ready" else u.check_async(),
            enabled=lambda _: (update_item(u.state) or ("", False))[1],
            visible=lambda _: update_item(u.state) is not None,
        )
        yield item("Settings", lambda: self.settings())
        if paths.FROZEN:
            yield item("Start with Windows", lambda: set_autostart(not autostart_enabled()),
                       checked=lambda _: autostart_enabled())
        yield pystray.Menu.SEPARATOR
        yield item("Quit", lambda: quit())

    def on_update(self) -> None:
        """Updater.on_change: refresh the menu text; announce a ready update once."""
        try:
            self.icon.update_menu()
            if self.updater.state == "ready" and not self._ready_shown:
                self._ready_shown = True
                self.icon.notify(READY_MSG, NAME)
        except Exception:
            log.exception("tray update")  # icon not running yet / already stopped

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            log.exception("tray stop")
