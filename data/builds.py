"""Builds from lolalytics' build-full shape (lane-wide, or only games vs one enemy).

Win rates of later items are inflated (games that reach a 4th item are longer and more often
won), so options are only ever compared within the same section. A matchup delta compares an
option's edge in the matchup with its edge overall, both relative to that section's baseline:

    delta = (wr_option_vs - wr_vs) - (wr_option_all - wr_all)

which reads "how much better this option does against this enemy than it usually does".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from scoring import shrink

SECTIONS = ("spells", "start", "boots", "core", "item1", "item2", "item3", "item4", "item5", "skills", "skill_order")
MIN_SHARE = 1.0  # % pick rate below which options are hidden (noise, trolls)
HIDDEN = {"0", "9999", "1001"}  # none, unknown, plain Boots (mostly games that ended before upgrading)


@dataclass
class VsDelta:
    enemy: int       # enemy champion id
    lane: str        # its lane
    delta: float     # raw matchup delta (see module doc)
    games: int       # games with this option in the matchup
    weighted: float  # shrunk and lane-weighted


@dataclass
class Option:
    key: str                 # item id, "a_b_c" item path, "4_14" spell pair, "QWE", or a level order
    wr: float
    pr: float                # % of games in this section that took it
    games: int
    vs: list[VsDelta] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return [int(x) for x in str(self.key).split("_") if x.lstrip("-").isdigit()]

    @property
    def matchup(self) -> float:
        """Combined weighted delta across your lane opponents and the enemy jungler (0 when there's no matchup data)."""
        return sum(v.weighted for v in self.vs)


@dataclass
class RunePage:
    primary: list[int]       # keystone first, then 3
    secondary: list[int]     # 2
    shards: list[int]        # 3
    wr: float
    games: int


def shard_keys(shards: list[int]) -> list[str]:
    """Rune stat keys for a page's 3 shards (offense, flex, defense): the flex row's are "<id>f"."""
    return [f"{s}f" if i == 1 else str(s) for i, s in enumerate(shards)]


@dataclass
class Build:
    champion_id: int
    lane: str
    wr: float
    games: int
    pages: dict[str, RunePage]                          # "pick" (most picked), "win" (highest win rate)
    runes: dict[str, Option]  # each rune on its own, by id; flex-row shards are keyed "<id>f"
    sections: dict[str, list[Option]]
    vs: list[tuple[int, str, int, float]] = field(default_factory=list)  # (enemy, lane, games, wr)


def _options(rows, min_share: float = MIN_SHARE, relative: bool = False) -> list[Option]:
    """relative: rescale pick rates to a share of this section's games. Later item slots come as a
    share of all games, and few games reach a 4th or 5th item, so their options would all look rare."""
    rows = [r for r in rows or [] if isinstance(r, list) and len(r) >= 4]
    total = sum(float(r[2] or 0) for r in rows) if relative else 0.0
    scale = 100.0 / total if total > 0 else 1.0
    out = []
    for r in rows:
        key, wr, pr, games = r[0], float(r[1] or 0), float(r[2] or 0) * scale, int(r[3] or 0)
        if pr >= min_share and games > 0 and str(key) not in HIDDEN:
            out.append(Option(str(key), wr, pr, games))
    return out


def _page(p: dict | None) -> RunePage | None:
    if not p or not p.get("set"):
        return None
    s = p["set"]
    return RunePage(list(s.get("pri") or []), list(s.get("sec") or []), list(s.get("mod") or []),
                    float(p.get("wr") or 0), int(p.get("n") or 0))


def parse(raw: dict) -> Build | None:
    """A Build from build-full (or a matchup page's equivalent); None when there's no data."""
    h = (raw or {}).get("header") or {}
    if not h.get("cid") or not h.get("n"):
        return None
    summary = raw.get("summary") or {}
    pages = {k: pg for k in ("pick", "win") if (pg := _page((summary.get(k) or {}).get("runes")))}
    runes = {}
    for rid, entries in ((raw.get("runes") or {}).get("stats") or {}).items():
        best = max((e for e in entries if isinstance(e, list) and len(e) >= 3), key=lambda e: e[2], default=None)
        if best and best[2]:
            runes[str(rid)] = Option(str(rid), float(best[1]), float(best[0]), int(best[2]))
    sections = {
        "spells": _options(raw.get("spells")),
        "start": _options(raw.get("startSet")),
        "boots": _options(raw.get("boots")),
        "core": _options(raw.get("builtBootSet3"), 0.5),
        "skills": _options(raw.get("skillOrder")),
        "skill_order": _options(raw.get("skill15"), 0.5),
    }
    for i in range(1, 6):
        sections[f"item{i}"] = _options(raw.get(f"item{i}"), relative=True)
    return Build(int(h["cid"]), str(h.get("lane") or ""), float(h.get("wr") or 0), int(h["n"]), pages, runes, sections)


def add_matchup(base: Build, vs: Build, enemy: int, lane: str, lane_weight: float, k: float, min_games: int = 50) -> None:
    """Annotate base's options (and runes) with their delta in games against `enemy`."""
    base.vs.append((enemy, lane, vs.games, vs.wr))

    def annotate(mine: Option, theirs: Option | None) -> None:
        if theirs is None or theirs.games < min_games:
            return
        delta = (theirs.wr - vs.wr) - (mine.wr - base.wr)
        mine.vs.append(VsDelta(enemy, lane, delta, theirs.games, shrink(delta, theirs.games, k) * lane_weight))

    for name, opts in base.sections.items():
        theirs = {o.key: o for o in vs.sections.get(name, [])}
        for o in opts:
            annotate(o, theirs.get(o.key))
    for rid, o in base.runes.items():
        annotate(o, vs.runes.get(rid))


def best_vs(options: list[Option], min_edge: float = 0.3) -> Option | None:
    """The option that does best against this lane, if it clears the shrink by min_edge."""
    scored = [o for o in options if o.vs]
    if not scored:
        return None
    top = max(scored, key=lambda o: o.matchup)
    return top if top.matchup >= min_edge else None
