"""Filesystem locations that work both from source and from a PyInstaller build."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# read-only files shipped with the app (panel/, default config.toml)
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
# writable per-user data: cache, logs, and (when frozen) the user's config
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "league-analysis"
CACHE_DIR = DATA_DIR / "cache"


def config_path() -> Path:
    if not FROZEN:
        return RESOURCES / "config.toml"
    user = DATA_DIR / "config.toml"
    if not user.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(RESOURCES / "config.toml", user)
    return user
