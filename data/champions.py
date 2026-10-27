from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

DDRAGON = "https://ddragon.leagueoflegends.com"
from paths import CACHE_DIR
# Data Dragon `id` slug -> lolalytics slug (extend as mismatches are found)
SLUG_OVERRIDES = {"monkeyking": "wukong"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Champions:
    def __init__(self, data: dict, version: str = ""):
        self.version = version
        self._by_id: dict[int, dict] = {int(c["key"]): c for c in data["data"].values()}
        self._slug_to_id = {self.slug(i): i for i in self._by_id}
        self._name_to_id = {_norm(c["name"]): i for i, c in self._by_id.items()}

    @classmethod
    def load(cls, cache_dir: Path = CACHE_DIR) -> "Champions":
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            return cls._fetch(cache_dir)
        except httpx.HTTPError:
            cached = sorted(cache_dir.glob("ddragon_[0-9]*.json"), key=lambda p: p.stat().st_mtime)
            if not cached:
                raise
            ver = cached[-1].stem.removeprefix("ddragon_")
            return cls(json.loads(cached[-1].read_text(encoding="utf-8")), ver)

    @classmethod
    def _fetch(cls, cache_dir: Path) -> "Champions":
        with httpx.Client(timeout=15) as c:
            ver = c.get(f"{DDRAGON}/api/versions.json").raise_for_status().json()[0]
            path = cache_dir / f"ddragon_{ver}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                data = c.get(f"{DDRAGON}/cdn/{ver}/data/en_US/champion.json").raise_for_status().json()
                path.write_text(json.dumps(data), encoding="utf-8")
        return cls(data, ver)

    def icon_url(self, cid: int) -> str:
        c = self._by_id.get(cid)
        return f"{DDRAGON}/cdn/{self.version}/img/champion/{c['image']['full']}" if c else ""

    def name(self, cid: int) -> str:
        c = self._by_id.get(cid)
        return c["name"] if c else f"#{cid}"

    def slug(self, cid: int) -> str:
        c = self._by_id.get(cid)
        if not c:
            return ""
        s = _norm(c["id"])
        return SLUG_OVERRIDES.get(s, s)

    def id_from_slug(self, slug: str) -> int | None:
        n = _norm(slug)
        return self._slug_to_id.get(n) or self._name_to_id.get(n)

    def all_ids(self) -> list[int]:
        return sorted(self._by_id)

    def tags(self, cid: int) -> list[str]:
        return list(self._by_id.get(cid, {}).get("tags", []))
