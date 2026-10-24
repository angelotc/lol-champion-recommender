from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from paths import config_path
from scoring import LANES

DEFAULT_LOCKFILE = r"C:\Riot Games\League of Legends\lockfile"
DUO = {"bottom": "support", "support": "bottom"}


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
    return {"base": 1.0, "enemy": enemy, "ally": ally}


@dataclass
class Config:
    pools: dict[str, list[str]] = field(default_factory=dict)
    tier: str = "master_plus"
    days: int = 14
    queue: str = "ranked"
    region: str = "all"
    k: float = 500
    tentative_factor: float = 0.5
    top_n: int = 8
    lockfile: str = DEFAULT_LOCKFILE
    weights: dict[str, dict] = field(default_factory=dict)  # per-role overrides

    def weights_for(self, role: str) -> dict:
        w = default_weights(role)
        override = self.weights.get(role, {})
        return {
            "base": override.get("base", w["base"]),
            "enemy": w["enemy"] | override.get("enemy", {}),
            "ally": w["ally"] | override.get("ally", {}),
        }


def load_config(path: Path | str | None = None) -> Config:
    with open(path or config_path(), "rb") as f:
        raw = tomllib.load(f)
    return Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
