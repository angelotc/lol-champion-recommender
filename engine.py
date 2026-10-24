from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from data.champions import Champions
from config import Config
from lcu import DraftState
from data.fetch import Fetcher
from data.lolalytics import Lolalytics
from scoring import LANES, ChampStats, Recommendation, assign_enemy_lanes, infer_enemy_lanes, recommend

log = logging.getLogger(__name__)


@dataclass
class Evaluation:
    draft: DraftState
    role: str
    recs: list[Recommendation]
    lanes: dict[int, str]  # champion id -> assigned or inferred lane, for allies and enemies
    enemy_rates: dict[int, dict[str, float]] = field(default_factory=dict)  # lane play rates, %


class Recommender:
    def __init__(self, cfg: Config, fetcher: Fetcher | None = None):
        self.cfg = cfg
        self.champs = Champions.load()
        self.lola = Lolalytics(cfg.tier, cfg.days, fetcher)
        self.pools: dict[tuple[str, str], dict[int, ChampStats]] = {}  # (tier, role) -> pool
        self.progress: Callable[[str], None] = lambda _: None

    @staticmethod
    def role_for(draft: DraftState) -> str | None:
        """Assigned position, or None when the queue doesn't assign one (blind pick, ARAM)."""
        return draft.my_position if draft.my_position in LANES else None

    @property
    def tier(self) -> str:
        return self.lola.tier

    @tier.setter
    def tier(self, value: str) -> None:
        self.lola.tier = value

    def pool(self, role: str) -> dict[int, ChampStats]:
        """Stats at the current tier for every champion played enough in the role, loaded on first use."""
        key = (self.tier, role)
        if key not in self.pools:
            ids = self.candidates(role)
            pool: dict[int, ChampStats] = {}
            label = f"{role} stats ({self.tier}, {self.cfg.days} days)"
            self.progress(f"Loading {label}: 0/{len(ids)}...")
            jobs = self.lola.fetch.map(lambda c: (c, self.lola.champ_stats(self.champs.slug(c), role)), ids)
            for i, (cid, s) in enumerate(jobs, 1):
                self.progress(f"Loading {label}: {i}/{len(ids)}...")
                if s is None:
                    log.warning("no lolalytics data for %s in %s at %s", self.champs.slug(cid), role, self.tier)
                else:
                    pool[s.champion_id] = s
            self.pools[key] = pool
        return self.pools[key]

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
        candidates = [
            s for cid, s in self.pool(role).items()
            if cid not in taken and (not pickable or cid in pickable)
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
            tentative_factor=self.cfg.tentative_factor,
        )
        lanes = {cid: lane for lane, (cid, _) in allies.items()} | {cid: lane for lane, cid in enemies.items()}
        return Evaluation(draft, role, recs[: self.cfg.top_n], lanes, rates)
