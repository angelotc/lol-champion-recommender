from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from paths import config_path
DEFAULT_LOCKFILE = r"C:\Riot Games\League of Legends\lockfile"


@dataclass
class Config:
    role: str = "support"
    champion_pool: list[str] = field(default_factory=list)
    tier: str = "master_plus"
    days: int = 14
    queue: str = "ranked"
    region: str = "all"
    k: float = 500
    tentative_factor: float = 0.5
    top_n: int = 8
    lockfile: str = DEFAULT_LOCKFILE
    weights: dict = field(default_factory=lambda: {"base": 1.0, "enemy": {}, "ally": {}})


def load_config(path: Path | str | None = None) -> Config:
    with open(path or config_path(), "rb") as f:
        raw = tomllib.load(f)
    return Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
