from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from champions import Champions
from config import Config
from lcu import DraftState
from lolalytics import Lolalytics
from scoring import LANES, ChampStats, Recommendation, infer_enemy_lanes, recommend

log = logging.getLogger(__name__)


@dataclass
class Evaluation:
    draft: DraftState
    role: str
    recs: list[Recommendation]
    lanes: dict[int, str]  # champion id -> assigned or inferred lane, for allies and enemies


class Recommender:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.champs = Champions.load()
        self.lola = Lolalytics(cfg.tier, cfg.days)
        self.pools: dict[str, dict[int, ChampStats]] = {}
        self.progress: Callable[[str], None] = lambda _: None

    @staticmethod
    def role_for(draft: DraftState) -> str | None:
        """Assigned position, or None when the queue doesn't assign one (blind pick, ARAM)."""
        return draft.my_position if draft.my_position in LANES else None

    def pool(self, role: str) -> dict[int, ChampStats]:
        """Stats for the role's champion pool, loaded on first use (then served from cache)."""
        if role not in self.pools:
            pool = {}
            for slug in self.cfg.pools.get(role, []):
                self.progress(f"Loading {role} stats: {slug} ({self.cfg.tier}, {self.cfg.days} days)...")
                cid = self.champs.id_from_slug(slug)  # accepts names like "Lee Sin"
                s = self.lola.champ_stats(self.champs.slug(cid) if cid else slug, role)
                if s is None:
                    log.warning("no lolalytics data for %s in %s", slug, role)
                    continue
                pool[s.champion_id] = s
            self.pools[role] = pool
        return self.pools[role]

    def _lane_rates(self, ids: list[int]) -> dict[int, dict[str, float]]:
        return {c: self.lola.lane_rates(self.champs.slug(c)) for c in ids}

    def evaluate(self, draft: DraftState, pickable: set[int] | None = None) -> Evaluation:
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
        enemies = infer_enemy_lanes(draft.enemies, self._lane_rates(draft.enemies))
        recs = recommend(
            candidates, allies, enemies, self.cfg.weights_for(role), self.cfg.k,
            tentative_factor=self.cfg.tentative_factor,
        )
        lanes = {cid: lane for lane, (cid, _) in allies.items()} | {cid: lane for lane, cid in enemies.items()}
        return Evaluation(draft, role, recs[: self.cfg.top_n], lanes)
