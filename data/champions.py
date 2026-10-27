from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

DDRAGON = "https://ddragon.leagueoflegends.com"
from paths import CACHE_DIR
# Data Dragon `id` slug -> lolalytics slug (extend as mismatches are found)
SLUG_OVERRIDES = {"monkeyking": "wukong"}
# rough magic-damage share; ddragon ratings misjudge these (keyed by normalized ddragon id)
MAGIC_SHARE = {
    "amumu": 0.85, "alistar": 0.75, "malphite": 0.8, "sejuani": 0.75, "zac": 0.8, "maokai": 0.8,
    "rammus": 0.7, "nautilus": 0.7, "leona": 0.6, "braum": 0.6, "chogath": 0.8, "sion": 0.4,
    "ornn": 0.6, "kaisa": 0.6, "ezreal": 0.35, "corki": 0.6, "kayle": 0.65, "shyvana": 0.55,
    "belveth": 0.1, "varus": 0.35, "akali": 0.85, "ekko": 0.85, "diana": 0.9, "gragas": 0.85,
    "volibear": 0.5, "teemo": 0.85, "kennen": 0.85,
}


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

    def damage(self, cid: int) -> tuple[float, float]:
        """(physical, magic) damage shares summing to 1."""
        c = self._by_id.get(cid)
        if not c:
            return (0.5, 0.5)
        magic = MAGIC_SHARE.get(_norm(c["id"]))
        if magic is None:
            info = c.get("info", {})
            ad, ap = info.get("attack", 0), info.get("magic", 0)
            magic = ap / (ad + ap) if ad + ap else 0.5
        return (1 - magic, magic)

    def team_damage(self, cids) -> tuple[float, float] | None:
        """Average (physical, magic) split over known champions, None if there are none."""
        shares = [self.damage(c) for c in cids if c in self._by_id]
        if not shares:
            return None
        n = len(shares)
        return (sum(p for p, _ in shares) / n, sum(m for _, m in shares) / n)

    def all_ids(self) -> list[int]:
        return sorted(self._by_id)

    def tags(self, cid: int) -> list[str]:
        return list(self._by_id.get(cid, {}).get("tags", []))
