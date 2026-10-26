"""Backtest the draft odds model against your own finished drafts.

  uv run scripts/backtest.py           evaluate: how well did the estimated win % match results?
  uv run scripts/backtest.py --tune    also tune the odds weights (coordinate descent on log loss)

Reads every stored draft with a linked win/loss, rebuilds both teams from its last champ select
snapshot and recomputes the odds with current stats (lolalytics' cached numbers, not what was
true on the day - close enough at these sample sizes). Fits a logistic calibration on the raw
term total, which says whether "60%" actually means 60%. First run fetches stats for every
champion involved (cached afterwards).
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config, load_config
from data.store import Store
from engine import Recommender
from lcu import parse_session
from scoring import LANES, ChampStats, DEFAULT_ODDS, assign_enemy_lanes, draft_odds


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def fit_logistic(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares fit p = sigmoid(a*x + b) by Newton iterations (two parameters)."""
    a = b = 0.0
    for _ in range(100):
        ps = [sigmoid(a * x + b) for x in xs]
        ga = sum((p - y) * x for p, y, x in zip(ps, ys, xs))
        gb = sum(p - y for p, y in zip(ps, ys))
        if abs(ga) < 1e-10 and abs(gb) < 1e-10:
            break
        wa = [p * (1 - p) for p in ps]
        haa = sum(w * x * x for w, x in zip(wa, xs)) + 1e-6  # ridge: keeps the 2x2 solvable
        hab = sum(w * x for w, x in zip(wa, xs))
        hbb = sum(wa) + 1e-6                   # even when all x are equal
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        a -= (hbb * ga - hab * gb) / det
        b -= (haa * gb - hab * ga) / det
    return a, b


def log_loss(ps: list[float], ys: list[float]) -> float:
    return -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(ps, ys)) / len(ps)


def predict(total: float, w: dict) -> float:
    """The deployed predictor: the calibrated logit when cal_a/cal_b are set, else the clamped total."""
    if w.get("cal_a") is not None:
        return sigmoid(w["cal_a"] * total + (w.get("cal_b") or 0.0))
    return min(50 + w["clamp"], max(50 - w["clamp"], 50 + total)) / 100


@dataclass
class Sample:
    ours: dict[str, tuple[ChampStats, bool]]
    theirs: dict[str, tuple[ChampStats, bool]]
    win: float


def load_samples(rec: Recommender, store: Store, cfg: Config) -> tuple[list[Sample], int]:
    """Rebuild every finished draft from its last snapshot; returns (samples, skipped)."""
    samples: list[Sample] = []
    skipped = 0
    rows = [r for r in store.drafts() if r["result"]]
    for i, row in enumerate(rows, 1):
        print(f"\rdraft {i}/{len(rows)}", end="", flush=True)
        s = _sample(rec, store, row, cfg)
        if s is None:
            skipped += 1
        else:
            samples.append(s)
    print()
    return samples, skipped


def _sample(rec: Recommender, store: Store, row, cfg: Config) -> Sample | None:
    snaps = store.draft_snapshots(row["id"])
    if not snaps or not row["my_champ"] or row["role"] not in LANES:
        return None
    st = parse_session(snaps[-1][1])
    if not st.enemies:
        return None
    rec.tier = row["tier"] or rec.tier
    ours = {s.position: (s.champion_id, s.tentative) for s in st.allies
            if not s.is_me and s.champion_id and s.position in LANES}
    ours[row["role"]] = (row["my_champ"], False)
    rates = {c: rec.lola.lane_rates(rec.champs.slug(c)) for c in st.enemies}
    theirs = {lane: (cid, False) for lane, cid in assign_enemy_lanes(st.enemies, rates).items()}

    def team(side: dict) -> dict[str, tuple[ChampStats, bool]]:
        out = {}
        for lane, (cid, tent) in side.items():
            if s := rec.lane_stats(cid, lane):
                out[lane] = (s, tent)
        return out

    ours_s, theirs_s = team(ours), team(theirs)
    if not ours_s and not theirs_s:
        return None
    return Sample(ours_s, theirs_s, 1.0 if row["result"] == "win" else 0.0)


def totals(samples: list[Sample], k: float, weights: dict) -> list[float]:
    return [sum(t.value for t in draft_odds(s.ours, s.theirs, k, weights).terms) for s in samples]


def evaluate(samples: list[Sample], totals: list[float], weights: dict) -> dict:
    ps = [predict(t, weights) for t in totals]
    ys = [s.win for s in samples]
    right = sum((p >= 0.5) == (y >= 0.5) for p, y in zip(ps, ys))
    wins = [p for p, y in zip(ps, ys) if y >= 0.5]
    losses = [p for p, y in zip(ps, ys) if y < 0.5]
    return {
        "n": len(samples), "ps": ps, "ys": ys,
        "accuracy": right / len(samples),
        "log_loss": log_loss(ps, ys),
        "base_loss": log_loss([0.5] * len(ys), ys),
        "win_mean": 100 * sum(wins) / len(wins) if wins else float("nan"),
        "loss_mean": 100 * sum(losses) / len(losses) if losses else float("nan"),
    }


def calibration(ps: list[float], ys: list[float], edges=(40, 45, 55, 60)) -> list[tuple[str, int, float]]:
    """Bucket the estimates: what the model said vs what happened."""
    out = []
    lo = 0
    for hi in (*edges, 100):
        bucket = [(p, y) for p, y in zip(ps, ys) if lo < p * 100 <= hi]
        actual = sum(y for _, y in bucket) / len(bucket) * 100 if bucket else float("nan")
        out.append((f"<={hi}%" if lo == 0 else f"{lo}-{hi}%", len(bucket), actual))
        lo = hi
    return out


def tune(samples: list[Sample], cfg: Config, passes: int = 3) -> dict:
    """Coordinate descent on the odds weights: shrink/grow one weight at a time, keep what helps."""
    ys = [s.win for s in samples]

    def loss(weights: dict) -> float:
        return log_loss([predict(t, weights) for t in totals(samples, cfg.k, weights)], ys)

    weights = DEFAULT_ODDS | cfg.odds
    best = loss(weights)
    for _ in range(passes):
        moved = False
        for key in ("strength", "same_lane", "duo", "cross", "synergy"):
            for m in (0, 0.25, 0.5, 0.75, 1.5, 2.0, 3.0):
                if (l := loss(weights | {key: weights[key] * m})) < best - 1e-4:
                    weights, best, moved = weights | {key: weights[key] * m}, l, True
        if not moved:
            break
    return weights


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--tune", action="store_true", help="also tune the odds weights on these drafts")
    args = ap.parse_args()
    cfg = load_config(args.config)
    rec = Recommender(cfg)
    samples, skipped = load_samples(rec, Store(), cfg)
    if not samples:
        print("No finished drafts in the store yet (play ranked with the panel running).")
        return

    def report(weights: dict) -> None:
        w = DEFAULT_ODDS | weights
        e = evaluate(samples, totals(samples, cfg.k, w), w)
        print(f"\n{e['n']} drafts ({skipped} skipped: no snapshot or unknown champion/role)")
        print(f"accuracy {e['accuracy']:.0%}  log loss {e['log_loss']:.3f} (coin-flip baseline {e['base_loss']:.3f})")
        print(f"mean estimate: wins {e['win_mean']:.1f}% vs losses {e['loss_mean']:.1f}%")
        print("\ncalibration (estimate vs what happened):")
        for label, n, actual in calibration(e["ps"], e["ys"]):
            if n:
                print(f"  {label:>10}  n={n:<3} actual {actual:5.1f}%")
        return e

    report(cfg.odds)
    a, b = fit_logistic(totals(samples, cfg.k, cfg.odds), [s.win for s in samples])
    print("\ncalibration fit on these drafts, for config.toml (makes the panel's % mean what it says):")
    print("[odds]")
    print(f"cal_a = {a:.4f}")
    print(f"cal_b = {b:+.4f}")
    if args.tune:
        w = tune(samples, cfg)
        if len(samples) < 50:
            print(f"\nwarning: {len(samples)} drafts is thin - tuned weights will overfit; keep them only if they hold on the next batch")
        changed = {k: round(v, 3) for k, v in w.items() if v != DEFAULT_ODDS[k]}
        if not changed:
            print("\ntuning: the default weights are already the best found on these drafts")
        else:
            print("\ntuned weights for config.toml (default in the comment):")
            print("[odds]")
            for k, v in changed.items():
                print(f"{k} = {v}  # {DEFAULT_ODDS[k]}")
            report(w)


if __name__ == "__main__":
    main()
