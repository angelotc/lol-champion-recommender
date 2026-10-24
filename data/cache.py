"""On-disk JSON cache with a TTL and a stale fallback, safe to use from several threads."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable


class JsonCache:
    def __init__(self, directory: Path, ttl: float):
        self.dir = directory
        self.ttl = ttl
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, key: str, label: str = "") -> Path:
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.dir / f"{digest}{'_' + label if label else ''}.json"

    def fresh(self, key: str, label: str = "") -> dict | None:
        p = self.path(key, label)
        if p.exists() and time.time() - p.stat().st_mtime < self.ttl:
            return self._read(p)
        return None

    def get(self, key: str, fetch: Callable[[], dict | None], label: str = "") -> dict:
        """Cached value if fresh; else fetch() and store it. If fetch() fails (returns None or
        raises), fall back to a stale copy, then to {}."""
        hit = self.fresh(key, label)
        if hit is not None:
            return hit
        p = self.path(key, label)
        try:
            data = fetch()
        except Exception:
            data = None
        if data is None:
            stale = self._read(p) if p.exists() else None
            return stale if stale is not None else {}
        self._write(p, data)
        return data

    @staticmethod
    def _read(p: Path) -> dict | None:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write(p: Path, data: dict) -> None:
        # write-then-rename so a reader on another thread never sees half a file
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
