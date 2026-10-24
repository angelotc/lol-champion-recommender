from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from champions import Champions
from config import Config
from lcu import DraftState
from lolalytics import Lolalytics
from scoring import LANES, ChampStats, Recommendation, infer_enemy_lanes, recommend


@dataclass
class Evaluation:
    draft: DraftState
    recs: list[Recommendation]
    lanes: dict[int, str]  # champion id -> assigned or inferred lane, for allies and enemies


class Recommender:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.champs = Champions.load()
        self.lola = Lolalytics(cfg.tier, cfg.days)
        self.pool: dict[int, ChampStats] = {}

    def prefetch(self, progress: Callable[[str], None] = lambda _: None) -> list[str]:
        """Load stats for the champion pool; returns slugs that had no data."""
        missing = []
        for slug in self.cfg.champion_pool:
            progress(f"Loading {slug} ({self.cfg.tier}, {self.cfg.days} days)...")
            s = self.lola.champ_stats(slug, self.cfg.role)
            if s is None:
                missing.append(slug)
                continue
            self.pool[s.champion_id] = s
        return missing

    def _lane_rates(self, ids: list[int]) -> dict[int, dict[str, float]]:
        return {c: self.lola.lane_rates(self.champs.slug(c)) for c in ids}

    def evaluate(self, draft: DraftState, pickable: set[int] | None = None) -> Evaluation:
        taken = draft.bans | draft.picked
        candidates = [
            s for cid, s in self.pool.items()
            if cid not in taken and (not pickable or cid in pickable)
        ]
        open_lanes = tuple(l for l in LANES if l != self.cfg.role)
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
            candidates, allies, enemies, self.cfg.weights, self.cfg.k,
            tentative_factor=self.cfg.tentative_factor,
        )
        lanes = {cid: lane for lane, (cid, _) in allies.items()} | {cid: lane for lane, cid in enemies.items()}
        return Evaluation(draft, recs[: self.cfg.top_n], lanes)
