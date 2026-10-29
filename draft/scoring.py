from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from math import exp, log1p

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
class Comfort:
    """Your own record on a champion in the role, and your mastery points on it."""
    games: int = 0
    wins: int = 0
    mastery: int = 0
    eff_games: float | None = None  # recency-weighted games/wins; used for the win rate when set
    eff_wins: float | None = None


@dataclass
class Contribution:
    label: str  # "vs", "with", "you" (your record) or "mastery"
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


def comfort_terms(s: ChampStats, c: Comfort | None, w: dict) -> list[Contribution]:
    """Your record vs everyone's on the champion (shrunk hard: personal samples are small), and a
    capped bonus for mastery points (familiarity), both weighted by w["weight"]."""
    if c is None or not w or not w.get("weight"):
        return []
    out = []
    eff = c.games if c.eff_games is None else c.eff_games
    if c.games and c.games >= w.get("min_games", 0) and eff > 0:
        wins = c.wins if c.eff_games is None else c.eff_wins or 0.0
        delta = 100 * wins / eff - s.wr
        out.append(Contribution("you", s.champion_id, s.lane, delta, c.games,
                                shrink(delta, eff, w.get("k", 20)) * w.get("history", 0.5) * w["weight"]))
    if c.mastery > 0 and w.get("mastery"):
        bonus = min(log1p(c.mastery / 1000) * w["mastery"], w.get("mastery_cap", 1.0))
        out.append(Contribution("mastery", s.champion_id, s.lane, float(c.mastery), 0, bonus * w["weight"]))
    return out


def recommend(
    candidates: list[ChampStats],
    allies: dict[str, tuple[int, bool]],
    enemies: dict[str, int],
    weights: dict,
    k: float,
    avg_wr: float = 50.0,
    tentative_factor: float = 0.5,
    comfort: dict[int, Comfort] | None = None,
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
        if comfort is not None:
            contribs += comfort_terms(s, comfort.get(s.champion_id), weights.get("comfort", {}))
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
class OddsTerm:
    kind: str        # "strength", "matchup" or "synergy"
    side: str        # "us" or "them": whose favour (a "them" term lowers your odds)
    champions: tuple[int, ...]
    lanes: tuple[str, ...]
    value: float     # percentage points in your favour (negative = theirs)


@dataclass
class DraftOdds:
    win: float                       # your estimated win %, 50 = even
    terms: list[OddsTerm]
    known: int                       # champions with stats counted, out of 10
    pending: int = 0                 # champions whose stats are still loading


DUO_LANES = {"bottom", "support"}
# cal_a/cal_b: logistic calibration of the term total, fit by scripts/backtest.py; None = the
# uncalibrated clamped total. sigmoid(cal_a * total + cal_b) becomes the win estimate.
DEFAULT_ODDS = {"strength": 0.5, "same_lane": 1.0, "duo": 0.7, "cross": 0.35, "synergy": 0.5,
                "clamp": 25.0, "cal_a": None, "cal_b": None}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + exp(-x))


def _pair_weight(a: str, b: str, w: dict) -> float:
    if a == b:
        return w["same_lane"]
    if a in DUO_LANES and b in DUO_LANES:
        return w["duo"]
    return w["cross"]


def draft_odds(
    ours: dict[str, tuple[ChampStats, bool]],
    theirs: dict[str, tuple[ChampStats, bool]],
    k: float,
    weights: dict | None = None,
    tentative_factor: float = 0.5,
) -> DraftOdds:
    """Estimated win % for your team from the champions picked so far (lane -> (stats, tentative)).

    Additive in percentage points: each side's champions' own strength (shrunk wr - avg, damped
    because summing five deltas overstates), every cross-team pair's matchup delta (taken from both
    champions' sides and averaged, weighted by how much the lanes interact), and synergy between
    teammates. Uncalibrated: scripts/backtest.py is what tunes these weights.
    """
    w = DEFAULT_ODDS | (weights or {})
    terms: list[OddsTerm] = []
    tf = lambda *tents: tentative_factor if any(tents) else 1.0

    for side, team, sign in (("us", ours, 1), ("them", theirs, -1)):
        for lane, (s, tent) in team.items():
            v = shrink(s.wr - (s.avg_wr if s.avg_wr is not None else 50.0), s.games, k) * w["strength"] * tf(tent) * sign
            terms.append(OddsTerm("strength", "us" if v >= 0 else "them", (s.champion_id,), (lane,), v))
        lanes = list(team.items())
        for i, (la, (a, ta)) in enumerate(lanes):
            for lb, (b, tb) in lanes[i + 1:]:
                vals = []
                for x, y, ly in ((a, b, lb), (b, a, la)):
                    pair = x.synergies.get(ly, {}).get(y.champion_id)
                    if pair is not None:
                        d = pair.wr - x.wr if pair.delta is None else pair.delta
                        vals.append(shrink(d, pair.games, k))
                if vals:
                    v = sum(vals) / len(vals) * w["synergy"] * tf(ta, tb) * sign
                    terms.append(OddsTerm("synergy", "us" if v >= 0 else "them", (a.champion_id, b.champion_id), (la, lb), v))

    for la, (a, ta) in ours.items():
        for lb, (b, tb) in theirs.items():
            vals = []
            pa = a.matchups.get(lb, {}).get(b.champion_id)  # your champion vs theirs
            if pa is not None:
                vals.append(shrink(pa.wr - a.wr if pa.delta is None else pa.delta, pa.games, k))
            pb = b.matchups.get(la, {}).get(a.champion_id)  # theirs vs yours, from their side
            if pb is not None:
                vals.append(-shrink(pb.wr - b.wr if pb.delta is None else pb.delta, pb.games, k))
            if vals:
                v = sum(vals) / len(vals) * _pair_weight(la, lb, w) * tf(ta, tb)
                terms.append(OddsTerm("matchup", "us" if v >= 0 else "them", (a.champion_id, b.champion_id), (la, lb), v))

    total = sum(t.value for t in terms)
    if w.get("cal_a") is not None:  # calibrated: a probability, no clamp needed
        win = 100 * sigmoid(w["cal_a"] * total + (w.get("cal_b") or 0.0))
    else:
        win = max(50 - w["clamp"], min(50 + w["clamp"], 50 + total))
    terms.sort(key=lambda t: abs(t.value), reverse=True)
    return DraftOdds(win, terms, len(ours) + len(theirs))


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
