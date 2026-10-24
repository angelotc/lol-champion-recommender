"""Client for lolalytics' undocumented JSON endpoints (a1.lolalytics.com/mega/).

Endpoints may change without notice; keep all knowledge of them in this module.
- ep=counter: champion `c` in `lane`; matchups vs enemies in `vslane`, plus stats.lanes
- ep=build-team: synergy with allies, keyed by ally lane (own lane omitted)
`patch=<small int>` means a rolling window of that many days.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

import paths
from scoring import LANES, ChampStats, Pair

BASE = "https://a1.lolalytics.com/mega/"
CACHE_DIR = paths.CACHE_DIR / "lolalytics"
HEADERS = {"User-Agent": "Mozilla/5.0 (league-analysis pick helper)"}
TTL = 24 * 3600
LANE_RATE_TIER = "master_plus"  # enough games to tell which lane a champion plays


def _games(stats: dict) -> int:
    """Estimated games in this lane: games analysed for the tier x pick rate (inferred meaning of fields)."""
    try:
        return round(float(stats.get("analysed") or 0) * float(stats.get("pr") or 0) / 100)
    except (TypeError, ValueError):
        return 0


class Lolalytics:
    def __init__(self, tier: str = "master_plus", days: int = 14, cache_dir: Path = CACHE_DIR, ttl: float = TTL):
        self.common = {"p": "d", "v": "1", "patch": str(days), "tier": tier, "queue": "420", "region": "all"}
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.http = httpx.Client(timeout=10, headers=HEADERS)
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _get(self, **params) -> dict:
        q = {"ep": params.pop("ep"), **self.common, **params}
        key = "_".join(f"{v}" for v in q.values() if v)
        path = self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}_{q['ep']}_{q.get('c', '')}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            r = self.http.get(BASE, params=q)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError):
            if path.exists():  # stale beats nothing
                return json.loads(path.read_text(encoding="utf-8"))
            return {}
        if not isinstance(data, dict) or "status" in data:  # e.g. {"status":404} for unknown slug
            data = {}
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    @property
    def tier(self) -> str:
        return self.common["tier"]

    @tier.setter
    def tier(self, value: str) -> None:
        self.common["tier"] = value  # part of every cache key, so tiers cache separately

    def counters(self, slug: str, lane: str, vslane: str, **extra) -> dict:
        return self._get(ep="counter", c=slug, lane=lane, vslane=vslane, **extra)

    def lane_rates(self, slug: str) -> dict[str, float]:
        # stats.lanes is lane-independent; reuse any cached lane query
        data = self.counters(slug, "middle", "middle", tier=LANE_RATE_TIER)
        return (data.get("stats") or {}).get("lanes") or {}

    def champ_stats(self, slug: str, lane: str) -> ChampStats | None:
        matchups: dict[str, dict[int, Pair]] = {}
        stats = None
        for vs in LANES:
            data = self.counters(slug, lane, vs)
            stats = stats or data.get("stats")
            matchups[vs] = {
                row["cid"]: Pair(float(row["vsWr"]), int(row["n"]), float(row["d2"]))
                for row in data.get("counters") or []
            }
        if not stats:
            return None
        team = self._get(ep="build-team", c=slug, lane=lane)
        cols = team.get("team_h") or ["id", "wr", "d1", "d2", "pr", "n"]
        i_id, i_wr, i_d2, i_n = (cols.index(c) for c in ("id", "wr", "d2", "n"))
        synergies = {
            ally_lane: {int(r[i_id]): Pair(float(r[i_wr]), int(r[i_n]), float(r[i_d2])) for r in rows}
            for ally_lane, rows in (team.get("team") or {}).items()
        }
        own = matchups.get(lane, {})
        return ChampStats(
            champion_id=int(stats["cid"]),
            lane=lane,
            wr=float(stats["wr"]),
            games=_games(stats) or sum(p.games for p in own.values()) or 1,
            avg_wr=float(stats.get("avgWr") or 50),
            matchups=matchups,
            synergies=synergies,
        )

    def close(self) -> None:
        self.http.close()
