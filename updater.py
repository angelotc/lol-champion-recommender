"""Auto-updates from GitHub releases (Velopack).

States: "unsupported" (running from source or not installed via Setup.exe), "idle", "checking",
"downloading", "ready" (downloaded, applies on restart or quit), "error".

Everything here is best-effort: any failure (no network, not installed, bindings missing)
degrades to a state change, never an exception.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("updater")

REPO_URL = "https://github.com/angelotc/lol-companion"

try:
    import velopack
except Exception:  # bindings missing / unsupported platform
    velopack = None


def startup() -> None:
    """Call first thing in main(): handles Velopack install/update hooks (may exit the process)."""
    if velopack is None:
        return
    try:
        velopack.App().run()
    except Exception:
        log.exception("velopack startup failed")


def exe_path() -> str:
    r"""Stable path to launch the app from (for Start with Windows).

    Installed layout: <root>\Update.exe, <root>\LoLCompanion.exe (stub, survives updates),
    <root>\current\LoLCompanion.exe (replaced on each update). Prefer the root stub.
    """
    exe = Path(sys.executable)
    try:
        if getattr(sys, "frozen", False):
            root = exe.parent.parent
            stub = root / exe.name
            if exe.parent.name.lower() == "current" and (root / "Update.exe").exists() and stub.exists():
                return str(stub)
    except Exception:
        log.exception("exe_path failed")
    return str(exe)


class Updater:
    def __init__(self, on_change: Callable[[], None] = lambda: None):
        self.on_change = on_change
        self.state = "unsupported"
        self.version: str | None = None  # version of the downloaded update, when ready
        self._mgr = None
        self._info = None
        self._busy = threading.Lock()
        if velopack is None or not getattr(sys, "frozen", False):
            return
        try:
            self._mgr = velopack.UpdateManager(velopack.GithubSource(REPO_URL, None, False))
            self.state = "idle"
        except Exception as e:  # not installed by Velopack (e.g. unzipped portable build)
            log.info("updates unsupported: %s", e)
            self._mgr = None

    def _set(self, state: str, version: str | None = None) -> None:
        self.state = state
        if version is not None:
            self.version = version
        try:
            self.on_change()
        except Exception:
            log.exception("on_change failed")

    def check_async(self) -> None:
        """Check GitHub for a newer build and download it in the background; calls on_change."""
        if self._mgr is None or self.state in ("checking", "downloading", "ready"):
            return
        threading.Thread(target=self._check, name="updater", daemon=True).start()

    def _check(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            self._set("checking")
            info = self._mgr.check_for_updates()
            if not info:
                log.info("up to date")
                self._set("idle")
                return
            ver = str(info.TargetFullRelease.Version)
            log.info("update available: %s", ver)
            self._set("downloading", ver)
            self._mgr.download_updates(info)
            self._info = info
            self._set("ready", ver)
        except Exception as e:
            log.warning("update check failed: %s", e)
            self._set("error")
        finally:
            self._busy.release()

    def apply_and_restart(self) -> None:
        """Apply the downloaded update and restart the app (only when state == "ready")."""
        if self._mgr is None or self.state != "ready" or self._info is None:
            return
        try:
            self._mgr.apply_updates_and_restart(self._info)
        except Exception as e:
            log.warning("apply failed: %s", e)
            self._set("error")

    def apply_on_exit(self) -> None:
        """If an update is ready, apply it after this process exits (no restart)."""
        if self._mgr is None or self.state != "ready" or self._info is None:
            return
        try:
            self._mgr.wait_exit_then_apply_updates(self._info, True, False)
        except Exception as e:
            log.warning("apply-on-exit failed: %s", e)
