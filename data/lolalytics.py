"""Client for lolalytics' undocumented JSON endpoints (a1.lolalytics.com/mega/) and site pages.

Endpoints may change without notice; keep all knowledge of them in this module.
- ep=counter: champion `c` in `lane`; matchups vs enemies in `vslane`, plus stats.lanes
- ep=build-team: synergy with allies, keyed by ally lane (own lane omitted)
- ep=list: the tier list for `lane`, every champion with rank, pick rate and games
- ep=build-full: runes, spells, skills and items for `c` in `lane`
- matchup builds have no endpoint (vs= gives 404): the site's /lol/<c>/vs/<enemy>/build/ page is
  server-rendered and embeds the same build-full shape in its Qwik state, see `_qwik_build`.
`patch=<small int>` means a rolling window of that many days.
"""
from __future__ import annotations

import json
import re

import httpx

import paths
from data.cache import JsonCache
from data.fetch import Fetcher
from scoring import LANES, ChampStats, Pair

BASE = "https://a1.lolalytics.com/mega/"
SITE = "https://lolalytics.com/lol"
CACHE_DIR = paths.CACHE_DIR / "lolalytics"
TTL = 24 * 3600
LANE_RATE_TIER = "master_plus"  # enough games to tell which lane a champion plays (and who's viable)
BUILD_KEYS = ("header", "summary", "runes", "spells", "boots", "startSet", "item1")


def _games(stats: dict) -> int:
    """Estimated games in this lane: games analysed for the tier x pick rate (inferred meaning of fields)."""
    try:
        return round(float(stats.get("analysed") or 0) * float(stats.get("pr") or 0) / 100)
    except (TypeError, ValueError):
        return 0


def _qwik_build(html: str) -> dict | None:
    """The build-full-shaped object from a server-rendered build page.

    Qwik serializes state as {"objs": [...]} where container values are base-36 indexes into objs,
    and strings starting with a control character wrap a reference to another obj.
    """
    m = re.search(r'<script type="qwik/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        objs = json.loads(m.group(1))["objs"]
    except (ValueError, KeyError, TypeError):
        return None

    def ref(v):
        if not isinstance(v, str):
            return v
        try:
            return resolve(int(v, 36))
        except ValueError:
            return v

    def resolve(i: int, seen: frozenset = frozenset()):
        if i in seen or not 0 <= i < len(objs):
            return None
        o, seen = objs[i], seen | {i}
        if isinstance(o, str) and o and o[0] < " ":
            try:
                return resolve(int(o[1:], 36), seen)
            except ValueError:
                return o
        if isinstance(o, dict):
            return {k: (resolve(int(v, 36), seen) if isinstance(v, str) else v) for k, v in o.items()}
        if isinstance(o, list):
            return [(resolve(int(v, 36), seen) if isinstance(v, str) else v) for v in o]
        return o

    for i, o in enumerate(objs):
        if isinstance(o, dict) and all(k in o for k in BUILD_KEYS):
            return resolve(i)
    return None


class Lolalytics:
    def __init__(self, tier: str = "master_plus", days: int = 14, fetcher: Fetcher | None = None,
                 cache_dir=CACHE_DIR, ttl: float = TTL):
        self.common = {"p": "d", "v": "1", "patch": str(days), "tier": tier, "queue": "420", "region": "all"}
        self.fetch = fetcher or Fetcher()
        self.cache = JsonCache(cache_dir, ttl)

    @property
    def tier(self) -> str:
        return self.common["tier"]

    @tier.setter
    def tier(self, value: str) -> None:
        self.common["tier"] = value  # part of every cache key, so tiers cache separately

    @property
    def days(self) -> int:
        return int(self.common["patch"])

    def _get(self, **params) -> dict:
        q = {"ep": params.pop("ep"), **self.common, **params}
        key = "_".join(f"{v}" for v in q.values() if v)

        def fetch() -> dict | None:
            try:
                r = self.fetch.get(BASE, q)
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                return None
            # e.g. {"status":404} for unknown slug or unsupported filter
            return {} if not isinstance(data, dict) or "status" in data else data

        return self.fetch.once(key, lambda: self.cache.get(key, fetch, f"{q['ep']}_{q.get('c', '')}"))

    def counters(self, slug: str, lane: str, vslane: str, **extra) -> dict:
        return self._get(ep="counter", c=slug, lane=lane, vslane=vslane, **extra)

    def lane_rates(self, slug: str) -> dict[str, float]:
        # stats.lanes is lane-independent; reuse any cached lane query
        data = self.counters(slug, "middle", "middle", tier=LANE_RATE_TIER)
        return (data.get("stats") or {}).get("lanes") or {}

    def tier_list(self, lane: str, tier: str = LANE_RATE_TIER) -> dict[int, dict]:
        """Champion id -> tier list row (rank, tier, pr, wr, games, ...) for champions played in `lane`."""
        rows = self._get(ep="list", lane=lane, tier=tier).get("cid") or {}
        return {int(cid): row for cid, row in rows.items() if isinstance(row, dict)}

    def build_full(self, slug: str, lane: str) -> dict:
        """Raw build-full for `slug` in `lane` at the current tier (see data/builds.py to parse)."""
        return self._get(ep="build-full", c=slug, lane=lane)

    def matchup_build(self, slug: str, lane: str, vs_slug: str, vslane: str) -> dict:
        """build-full-shaped data for `slug` in `lane` only in games against `vs_slug` in `vslane`."""
        params = {"lane": lane, "vslane": vslane, "tier": self.tier, "patch": self.common["patch"]}
        url = f"{SITE}/{slug}/vs/{vs_slug}/build/"
        key = url + "?" + "&".join(f"{k}={v}" for k, v in params.items())

        def fetch() -> dict | None:
            try:
                r = self.fetch.get(url, params)
                r.raise_for_status()
            except httpx.HTTPError:
                return None
            return _qwik_build(r.text) or {}

        return self.fetch.once(key, lambda: self.cache.get(key, fetch, f"vsbuild_{slug}_{vs_slug}"))

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
        self.fetch.close()
