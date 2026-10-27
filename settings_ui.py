"""Settings window backend: read the form values, validate, and save to config.toml (comments kept).

Rank tier is not here: it lives on the panel's pill. Everything else in config.toml (weights, odds,
days, ...) is left untouched by a save.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import tomlkit

from config import DEFAULT_COMFORT, Config, load_config

# form field -> (config.toml table or None for top-level, key)
FIELDS = {
    "top_n": (None, "top_n"),
    "min_pick_rate": (None, "min_pick_rate"),
    "show_ban_suggestions": (None, "show_ban_suggestions"),
    "comfort_only": (None, "comfort_only"),
    "comfort_weight": ("comfort", "weight"),
    "half_life": ("comfort", "half_life"),
    "min_games": ("comfort", "min_games"),
    "k": (None, "k"),
    "tentative_factor": (None, "tentative_factor"),
    "lockfile": (None, "lockfile"),
}
BOOLS = {"show_ban_suggestions", "comfort_only"}
INTS = {"top_n", "half_life", "min_games"}
# field -> (min, max, label for the error message)
RANGES = {
    "top_n": (3, 15, "Picks to show"),
    "min_pick_rate": (0, 5, "Minimum pick rate"),
    "comfort_weight": (0, 2, "Record and mastery weight"),
    "half_life": (0, 365, "Older games count less after"),
    "min_games": (1, 10, "Games before my record counts"),
    "k": (50, 2000, "k"),
    "tentative_factor": (0, 1, "tentative_factor"),
}


def defaults() -> dict:
    d, c = Config(), DEFAULT_COMFORT
    return {
        "top_n": d.top_n, "min_pick_rate": d.min_pick_rate,
        "show_ban_suggestions": d.show_ban_suggestions, "comfort_only": d.comfort_only,
        "comfort_weight": c["weight"], "half_life": c["half_life"], "min_games": c["min_games"],
        "k": d.k, "tentative_factor": d.tentative_factor, "lockfile": d.lockfile,
    }


def read_values(path: Path | str | None = None) -> dict:
    cfg = load_config(path)
    comfort = DEFAULT_COMFORT | cfg.comfort
    return {
        "top_n": cfg.top_n, "min_pick_rate": cfg.min_pick_rate,
        "show_ban_suggestions": cfg.show_ban_suggestions, "comfort_only": cfg.comfort_only,
        "comfort_weight": comfort["weight"], "half_life": comfort["half_life"],
        "min_games": comfort["min_games"],
        "k": cfg.k, "tentative_factor": cfg.tentative_factor, "lockfile": cfg.lockfile,
    }


def validate(values: dict) -> dict[str, str]:
    """field -> message for every field that is missing or out of range; empty = fine."""
    errors: dict[str, str] = {}
    for name in FIELDS:
        v = values.get(name)
        if name in BOOLS:
            if not isinstance(v, bool):
                errors[name] = "Must be on or off."
        elif name == "lockfile":
            if not isinstance(v, str) or not v.strip():
                errors[name] = "Enter a path."
        else:
            lo, hi, label = RANGES[name]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors[name] = "Enter a number."
            elif not lo <= v <= hi:
                errors[name] = f"Must be between {lo} and {hi}."
            elif name in INTS and v != int(v):
                errors[name] = "Must be a whole number."
    return errors


def _clean(name: str, v):
    if name in INTS:
        return int(v)
    if name in BOOLS:
        return v
    if name == "lockfile":
        return v.strip()
    return float(v) if name != "k" or v != int(v) else int(v)


def _string(s: str):
    return tomlkit.string(s, literal=True) if "'" not in s and "\n" not in s else s


def save(values: dict, path: Path | str) -> dict[str, str]:
    """Validate, then write only the form's keys into config.toml. Returns the errors (nothing written if any)."""
    errors = validate(values)
    if errors:
        return errors
    path = Path(path)
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    defaults_ = defaults()
    for name, (table, key) in FIELDS.items():
        v = _clean(name, values[name])
        if name == "lockfile":
            v = _string(v)
        if table is None:
            doc[key] = v
        elif table in doc:
            doc[table][key] = v
        elif v != defaults_[name]:  # no [comfort] yet: add it only once something differs
            doc[table] = tomlkit.table()
            doc[table][key] = v
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    try:
        load_config(tmp)  # the file we wrote must still load
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)  # atomic: the original is only replaced by a good file
    return {}


class SettingsApi:
    """Exposed to settings.html as window.pywebview.api."""

    def __init__(self, path: Path | str, on_saved: Callable[[Config], None] | None = None,
                 version: Callable[[], str] = lambda: "dev"):
        self._path, self._on_saved, self._version = Path(path), on_saved, version
        self.window = None  # set by the app once the window exists

    def get(self) -> dict:
        return {"values": read_values(self._path), "defaults": defaults(), "version": self._version()}

    def defaults(self) -> dict:
        return defaults()

    def save(self, values: dict) -> dict:
        errors = save(values, self._path)
        if not errors and self._on_saved:
            self._on_saved(load_config(self._path))
        return {"ok": not errors, "errors": errors}

    def open_file(self) -> None:
        os.startfile(self._path)

    def close(self) -> None:
        if self.window:
            self.window.destroy()
