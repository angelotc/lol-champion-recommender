from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

from data import builds
from data.champions import Champions
from data.ddragon import Assets
from config import Config
from lcu import DraftState
from data.fetch import Fetcher
from data.lolalytics import Lolalytics
from scoring import (
    LANES, ChampStats, Comfort, Recommendation, Threat, assign_enemy_lanes, ban_threats, infer_enemy_lanes, recommend,
)

log = logging.getLogger(__name__)


@dataclass
class Evaluation:
    draft: DraftState
    role: str
    recs: list[Recommendation]
    lanes: dict[int, str]  # champion id -> assigned or inferred lane, for allies and enemies
    enemy_rates: dict[int, dict[str, float]] = field(default_factory=dict)  # lane play rates, %


@dataclass
class BuildEvaluation:
    champion_id: int
    role: str
    build: builds.Build | None
    opponents: dict[str, int]  # lane -> enemy champion the matchup deltas are measured against


@dataclass
class BanEvaluation:
    draft: DraftState
    role: str
    picks: list[int]       # your likely picks the threats are measured against
    threats: list[Threat]


class Recommender:
    def __init__(self, cfg: Config, fetcher: Fetcher | None = None):
        self.cfg = cfg
        self.champs = Champions.load()
        self.lola = Lolalytics(cfg.tier, cfg.days, fetcher)
        self.pools: dict[tuple[str, str], dict[int, ChampStats]] = {}  # (tier, role) -> pool
        self.progress: Callable[[str], None] = lambda _: None
        # your record and mastery per champion for a role; set by the controller (live only)
        self.comfort: Callable[[str], dict[int, Comfort]] = lambda role: {}
        self._assets: Assets | None = None
        self._pool_locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_lock = threading.Lock()

    @staticmethod
    def role_for(draft: DraftState) -> str | None:
        """Assigned position, or None when the queue doesn't assign one (blind pick, ARAM)."""
        return draft.my_position if draft.my_position in LANES else None

    @property
    def assets(self) -> Assets:
        """Item, rune and summoner spell names/icons (loaded on first use)."""
        if self._assets is None:
            self._assets = Assets.load(self.champs.version)
        return self._assets

    @property
    def tier(self) -> str:
        return self.lola.tier

    @tier.setter
    def tier(self, value: str) -> None:
        self.lola.tier = value

    def has_pool(self, role: str, tier: str | None = None) -> bool:
        return (tier or self.tier, role) in self.pools

    def pool(self, role: str, progress: Callable[[str], None] | None = None,
             tier: str | None = None) -> dict[int, ChampStats]:
        """Stats at the tier (default: current) for every champion played enough in the role,
        loaded on first use. Concurrent callers for the same role and tier wait for one load."""
        key = (tier or self.tier, role)
        if key in self.pools:
            return self.pools[key]
        with self._locks_lock:
            lock = self._pool_locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self.pools:
                self.pools[key] = self._load_pool(role, key[0], progress or self.progress)
        return self.pools[key]

    def _load_pool(self, role: str, tier: str, progress: Callable[[str], None]) -> dict[int, ChampStats]:
        lola = self.lola if tier == self.tier else self.lola.at_tier(tier)
        ids = self.candidates(role)
        pool: dict[int, ChampStats] = {}
        label = f"{role} stats ({tier}, {self.cfg.days} days)"
        progress(f"Loading {label}: 0/{len(ids)}...")
        jobs = lola.fetch.map(lambda c: (c, lola.champ_stats(self.champs.slug(c), role)), ids)
        for i, (cid, s) in enumerate(jobs, 1):
            progress(f"Loading {label}: {i}/{len(ids)}...")
            if s is None:
                log.warning("no lolalytics data for %s in %s at %s", self.champs.slug(cid), role, tier)
            else:
                pool[s.champion_id] = s
        return pool

    def candidates(self, role: str) -> list[int]:
        """Champions with at least min_pick_rate % pick rate in the role on lolalytics' tier list."""
        rows, known = self.lola.tier_list(role), set(self.champs.all_ids())
        ids = [c for c, r in rows.items() if float(r.get("pr") or 0) >= self.cfg.min_pick_rate and c in known]
        return sorted(ids, key=lambda c: -float(rows[c].get("pr") or 0))

    def _lane_rates(self, ids: list[int]) -> dict[int, dict[str, float]]:
        return {c: self.lola.lane_rates(self.champs.slug(c)) for c in ids}

    def evaluate(
        self, draft: DraftState, pickable: set[int] | None = None, enemy_lanes: dict[int, str] | None = None,
    ) -> Evaluation:
        """enemy_lanes: lanes you've set by hand for enemy picks (flex picks); the rest are inferred."""
        role = self.role_for(draft)
        if role is None:
            raise ValueError("no assigned role")
        taken = draft.bans | draft.picked
        comfort = self.comfort(role)
        candidates = [
            s for cid, s in self.pool(role).items()
            if cid not in taken and (not pickable or cid in pickable)
            and (not self.cfg.comfort_only or cid in comfort)
        ]
        open_lanes = tuple(l for l in LANES if l != role)
        others = [s for s in draft.allies if s.champion_id and not s.is_me]
        allies = {s.position: (s.champion_id, s.tentative) for s in others if s.position in open_lanes}
        unplaced = [s for s in others if s.position not in open_lanes]
        if unplaced:  # blind pick: guess lanes for allies without an assigned position
            free = tuple(l for l in open_lanes if l not in allies)
            guess = infer_enemy_lanes([s.champion_id for s in unplaced], self._lane_rates([s.champion_id for s in unplaced]), free)
            tent = {s.champion_id: s.tentative for s in unplaced}
            allies |= {lane: (cid, tent[cid]) for lane, cid in guess.items()}
        rates = self._lane_rates(draft.enemies)
        enemies = assign_enemy_lanes(draft.enemies, rates, enemy_lanes)
        recs = recommend(
            candidates, allies, enemies, self.cfg.weights_for(role), self.cfg.k,
            tentative_factor=self.cfg.tentative_factor, comfort=comfort,
        )
        lanes = {cid: lane for lane, (cid, _) in allies.items()} | {cid: lane for lane, cid in enemies.items()}
        return Evaluation(draft, role, recs[: self.cfg.top_n], lanes, rates)

    def bans(self, draft: DraftState, pickable: set[int] | None = None) -> BanEvaluation:
        """Enemy champions worth banning, measured against your likely picks for this draft."""
        role = self.role_for(draft)
        if role is None:
            raise ValueError("no assigned role")
        ev = self.evaluate(draft, pickable)
        pool = self.pool(role)
        picks = [pool[r.champion_id] for r in ev.recs[: self.cfg.ban_vs_top]]
        mine = {s.champion_id for s in draft.allies if s.champion_id}  # your team's picks and hovers
        exclude = draft.bans | draft.picked | mine | {r.champion_id for r in ev.recs[:3]}
        rows = {lane: self.lola.tier_list(lane) for lane in LANES}
        known = set(self.champs.all_ids())
        threats = [
            t for t in ban_threats(picks, rows, self.cfg.weights_for(role), self.cfg.k, exclude)
            if t.champion_id in known
        ]
        return BanEvaluation(draft, role, [p.champion_id for p in picks], threats[: self.cfg.ban_count])

    def build(self, champion_id: int, role: str, enemies: dict[str, int]) -> BuildEvaluation:
        """Runes, spells, skills and items for your champion in the role, with matchup deltas vs
        your lane opponents (your lane, plus the enemy duo for bot and support)."""
        slug = self.champs.slug(champion_id)
        base = builds.parse(self.lola.build_full(slug, role))
        weights = self.cfg.weights_for(role)["enemy"]
        opponents = {lane: cid for lane, cid in enemies.items() if weights.get(lane, 0) >= 1.0}
        if base:
            jobs = {
                lane: self.lola.fetch.submit(self.lola.matchup_build, slug, role, self.champs.slug(cid), lane)
                for lane, cid in opponents.items()
            }
            for lane, job in jobs.items():
                try:
                    vs = builds.parse(job.result())
                except Exception:
                    log.exception("matchup build %s vs %s", slug, opponents[lane])
                    continue
                if vs:
                    builds.add_matchup(base, vs, opponents[lane], lane, weights[lane], self.cfg.build_k,
                                       self.cfg.build_min_games)
        return BuildEvaluation(champion_id, role, base, opponents)
