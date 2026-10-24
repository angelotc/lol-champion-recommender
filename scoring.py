from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

LANES = ("top", "jungle", "middle", "bottom", "support")


@dataclass
class Pair:
    wr: float
    games: int
    delta: float | None = None  # source-provided normalized delta; else wr - champ wr


@dataclass
class ChampStats:
    champion_id: int
    lane: str
    wr: float
    games: int
    avg_wr: float | None = None  # baseline for `base`; falls back to recommend(avg_wr=)
    matchups: dict[str, dict[int, Pair]] = field(default_factory=dict)
    synergies: dict[str, dict[int, Pair]] = field(default_factory=dict)


@dataclass
class Contribution:
    label: str  # "vs" or "with"
    champion_id: int
    lane: str
    delta: float
    games: int
    weighted: float
    tentative: bool = False


@dataclass
class Recommendation:
    champion_id: int
    score: float
    base: float
    contributions: list[Contribution]


def shrink(delta: float, games: int, k: float) -> float:
    return delta * games / (games + k) if games + k > 0 else 0.0


def recommend(
    candidates: list[ChampStats],
    allies: dict[str, tuple[int, bool]],
    enemies: dict[str, int],
    weights: dict,
    k: float,
    avg_wr: float = 50.0,
    tentative_factor: float = 0.5,
) -> list[Recommendation]:
    recs = []
    for s in candidates:
        base = shrink(s.wr - (avg_wr if s.avg_wr is None else s.avg_wr), s.games, k) * weights.get("base", 1)
        contribs: list[Contribution] = []
        for lane, cid in enemies.items():
            pair = s.matchups.get(lane, {}).get(cid)
            if pair is None:
                continue
            delta = pair.wr - s.wr if pair.delta is None else pair.delta
            w = shrink(delta, pair.games, k) * weights.get("enemy", {}).get(lane, 0)
            contribs.append(Contribution("vs", cid, lane, delta, pair.games, w, False))
        for lane, (cid, tent) in allies.items():
            pair = s.synergies.get(lane, {}).get(cid)
            if pair is None:
                continue
            delta = pair.wr - s.wr if pair.delta is None else pair.delta
            w = shrink(delta, pair.games, k) * weights.get("ally", {}).get(lane, 0)
            if tent:
                w *= tentative_factor
            contribs.append(Contribution("with", cid, lane, delta, pair.games, w, tent))
        score = base + sum(c.weighted for c in contribs)
        recs.append(Recommendation(s.champion_id, score, base, contribs))
    return sorted(recs, key=lambda r: r.score, reverse=True)


def infer_enemy_lanes(
    enemy_ids: list[int], lane_rates: dict[int, dict[str, float]], lanes: tuple[str, ...] = LANES
) -> dict[str, int]:
    ids = enemy_ids[: len(lanes)]
    best: tuple[float, tuple[str, ...]] | None = None
    for perm in permutations(lanes, len(ids)):
        total = sum(lane_rates.get(c, {}).get(l, 0.0) for c, l in zip(ids, perm))
        if best is None or total > best[0]:
            best = (total, perm)
    return {l: c for c, l in zip(ids, best[1])} if best else {}


def assign_enemy_lanes(
    enemy_ids: list[int], lane_rates: dict[int, dict[str, float]], fixed: dict[int, str] | None = None
) -> dict[str, int]:
    """Lanes set by hand (champion id -> lane) win; everyone else is inferred into the lanes left."""
    pinned: dict[str, int] = {}
    for cid, lane in (fixed or {}).items():
        if cid in enemy_ids and lane in LANES:
            pinned[lane] = cid
    rest = [c for c in enemy_ids if c not in pinned.values()]
    return pinned | infer_enemy_lanes(rest, lane_rates, tuple(l for l in LANES if l not in pinned))


@dataclass
class Threat:
    champion_id: int
    lane: str                 # the enemy lane where it hurts you most
    score: float              # expected harm: P(you face it) x (matchup harm + its own strength)
    pick_rate: float          # % of games it's picked, summed over the lanes considered
    strength: float           # its own shrunk win rate delta, in that lane
    against: list[Contribution] = field(default_factory=list)  # how it does vs each of your picks


def ban_threats(
    picks: list[ChampStats],
    enemy_rows: dict[str, dict[int, dict]],
    weights: dict,
    k: float,
    exclude: set[int] = frozenset(),
    min_pick_rate: float = 0.5,
) -> list[Threat]:
    """Rank enemy champions worth banning, given the picks you're likely to make.

    enemy_rows: lane -> {champion id: tier list row with pr (%), games and avgWrDelta} for lanes the
    enemy can play. For each lane a champion plays, its threat is pick rate (how likely you face it)
    times the lane-weighted harm it does: how much worse your picks do against it (matchup deltas,
    shrunk), plus how strong it is on its own. A champion's lanes are summed.
    """
    threats: dict[int, Threat] = {}
    best: dict[int, float] = {}
    for lane, rows in enemy_rows.items():
        lw = weights.get("enemy", {}).get(lane, 0)
        if lw <= 0:
            continue
        for cid, row in rows.items():
            pr = float(row.get("pr") or 0)
            if cid in exclude or pr < min_pick_rate:
                continue
            against = []
            for s in picks:
                pair = s.matchups.get(lane, {}).get(cid)
                if pair is None:
                    continue
                delta = pair.wr - s.wr if pair.delta is None else pair.delta
                against.append(Contribution("vs", s.champion_id, lane, delta, pair.games, shrink(delta, pair.games, k) * lw))
            harm = -sum(c.weighted for c in against) / len(picks) if picks else 0.0
            strength = shrink(float(row.get("avgWrDelta") or 0), int(row.get("games") or 0), k)
            lane_score = pr / 100 * (harm + strength * lw)
            t = threats.get(cid)
            if t is None:
                t = threats[cid] = Threat(cid, lane, 0.0, 0.0, strength, against)
                best[cid] = lane_score
            elif lane_score > best[cid]:
                t.lane, t.strength, t.against = lane, strength, against
                best[cid] = lane_score
            t.score += lane_score
            t.pick_rate += pr
    return sorted(threats.values(), key=lambda t: t.score, reverse=True)
