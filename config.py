from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from paths import config_path
from scoring import LANES

DEFAULT_LOCKFILE = r"C:\Riot Games\League of Legends\lockfile"
DUO = {"bottom": "support", "support": "bottom"}
# your own record and mastery: weight (0 = ignore), k (shrink: personal samples are small),
# history (how much your win rate counts), mastery (bonus per log-1000-points), mastery_cap,
# half_life (days: a game this old counts half in your win rate; 0 = no decay),
# min_games (ignore your record on a champion until you have this many games)
DEFAULT_COMFORT = {"weight": 1.0, "k": 40, "history": 0.5, "mastery": 0.15, "mastery_cap": 1.0,
                   "half_life": 90, "min_games": 3}


def default_weights(role: str) -> dict:
    """Lane opponent most; bot/sup also face the enemy duo and rely on their partner; jungle touches all lanes."""
    enemy, ally = {}, {}
    for lane in LANES:
        if lane == role or lane == DUO.get(role):
            enemy[lane] = 1.0
        elif lane == "jungle" or role == "jungle":
            enemy[lane] = 0.5
        else:
            enemy[lane] = 0.3
        if lane != role:
            ally[lane] = 1.0 if lane == DUO.get(role) else 0.5 if "jungle" in (lane, role) else 0.3
    return {"base": 1.0, "enemy": enemy, "ally": ally, "comfort": dict(DEFAULT_COMFORT)}


@dataclass
class Config:
    min_pick_rate: float = 1.0  # % of games in the role; who counts as a candidate
    tier: str = "master_plus"
    days: int = 14
    queue: str = "ranked"
    region: str = "all"
    k: float = 500
    tentative_factor: float = 0.5
    top_n: int = 8
    ban_count: int = 5       # ban suggestions shown
    ban_vs_top: int = 5      # measure bans against this many of your top picks
    show_ban_suggestions: bool = False  # ban phase: suggest bans (else no recommendations)
    build_k: float = 300      # shrink for matchup rune/item deltas
    build_min_games: int = 100  # hide matchup deltas with fewer games
    lockfile: str = DEFAULT_LOCKFILE
    weights: dict[str, dict] = field(default_factory=dict)  # per-role overrides
    comfort: dict = field(default_factory=dict)  # overrides for DEFAULT_COMFORT, all roles
    comfort_only: bool = False  # only recommend champions you've played or have mastery on
    odds: dict = field(default_factory=dict)  # draft win estimate weights, see scoring.DEFAULT_ODDS

    def weights_for(self, role: str) -> dict:
        w = default_weights(role)
        override = self.weights.get(role, {})
        return {
            "base": override.get("base", w["base"]),
            "enemy": w["enemy"] | override.get("enemy", {}),
            "ally": w["ally"] | override.get("ally", {}),
            "comfort": w["comfort"] | self.comfort | override.get("comfort", {}),
        }


def load_config(path: Path | str | None = None) -> Config:
    with open(path or config_path(), "rb") as f:
        raw = tomllib.load(f)
    return Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
