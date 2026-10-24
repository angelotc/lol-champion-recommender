"""Panel payloads. Every payload has a `view` the panel routes on; builders here are pure."""
from __future__ import annotations

from data.champions import Champions
from engine import BanEvaluation, Evaluation
from scoring import LANES
from state import Stage

TIERS = {"master_plus": "Master+", "grandmaster_plus": "GM+", "challenger": "Challenger"}
LOW_DATA = 0.25  # below this share of possible matchups found, warn that the tier is thin
FLEX = 10.0  # an enemy whose second most played lane is at least this % is marked as a flex pick
STAGE_MESSAGES = {
    Stage.IDLE: "Waiting for champ select...",
    Stage.LOBBY: "In lobby - waiting for you to queue...",
    Stage.QUEUE: "In queue - waiting for a match...",
    Stage.READY: "Match found!",
    Stage.LOADING: "Game starting...",
    Stage.INGAME: "In game",
    Stage.POSTGAME: "Game over",
}


def header(role: str | None, tier: str, days: int) -> dict:
    return {
        "role": role or "", "tier": TIERS.get(tier, tier), "tier_id": tier, "days": days,
        "tiers": [{"id": k, "label": v} for k, v in TIERS.items()],
    }


def status(head: dict, message: str, kind: str = "waiting") -> dict:
    return head | {"view": "status", "status": kind, "message": message}


def picks(head: dict, ev: Evaluation, champs: Champions, enemy_lanes: dict[int, str]) -> dict:
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    d = ev.draft
    found = sum(1 for r in ev.recs for c in r.contributions if c.label == "vs")
    possible = len(ev.recs) * len(d.enemies)
    note = ""
    if possible and found / possible < LOW_DATA:
        note = "Few matchups at this tier: scores lean on overall win rate."

    def enemy(cid: int) -> dict:
        rates = ev.enemy_rates.get(cid, {})
        ranked = sorted(rates.values(), reverse=True)
        return champ(cid) | {
            "id": cid, "lane": ev.lanes.get(cid, ""), "manual": cid in enemy_lanes,
            "flex": len(ranked) > 1 and ranked[1] >= FLEX,
            "rates": {l: round(rates.get(l, 0)) for l in LANES},
        }

    return head | {
        "view": "picks",
        "status": "active",
        "message": "",
        "note": note,
        "locked": champ(d.my_locked) if d.my_locked else None,
        "allies": [
            champ(s.champion_id) | {"lane": s.position or ev.lanes.get(s.champion_id, ""),
                                    "tentative": s.tentative, "is_me": s.is_me}
            for s in d.allies if s.champion_id
        ],
        "enemies": [enemy(c) for c in d.enemies],
        "bans": [champ(c) for c in sorted(d.bans)],
        "recs": [
            champ(r.champion_id) | {
                "id": r.champion_id, "score": round(r.score, 2), "base": round(r.base, 2),
                "contributions": [
                    champ(c.champion_id) | {
                        "kind": c.label, "lane": c.lane, "delta": round(c.delta, 2),
                        "games": c.games, "weighted": round(c.weighted, 2), "tentative": c.tentative,
                    }
                    for c in sorted(r.contributions, key=lambda c: abs(c.weighted), reverse=True)
                ],
            }
            for r in ev.recs
        ],
    }


def bans(head: dict, bev: BanEvaluation, champs: Champions) -> dict:
    champ = lambda cid: {"name": champs.name(cid), "icon": champs.icon_url(cid)}
    d = bev.draft
    return head | {
        "view": "bans",
        "status": "active",
        "message": "",
        "allies": [
            champ(s.champion_id) | {"lane": s.position, "tentative": s.tentative, "is_me": s.is_me}
            for s in d.allies if s.champion_id
        ],
        "bans": [champ(c) for c in sorted(d.bans)],
        "picks": [champ(c) for c in bev.picks],
        "threats": [
            champ(t.champion_id) | {
                "id": t.champion_id, "lane": t.lane, "score": round(t.score, 3),
                "pick_rate": round(t.pick_rate, 1), "strength": round(t.strength, 2),
                "against": [
                    champ(c.champion_id) | {"delta": round(c.delta, 2), "games": c.games, "weighted": round(c.weighted, 2)}
                    for c in sorted(t.against, key=lambda c: c.weighted)
                ],
            }
            for t in bev.threats
        ],
    }


def record(ev: Evaluation) -> dict:
    """Compact form of an evaluation for the store (backtesting)."""
    return {
        "role": ev.role,
        "lanes": {str(c): l for c, l in ev.lanes.items()},
        "allies": [[s.champion_id, s.position, s.tentative] for s in ev.draft.allies if s.champion_id],
        "enemies": ev.draft.enemies,
        "recs": [
            {"id": r.champion_id, "score": r.score, "base": r.base,
             "c": [[c.label, c.champion_id, c.lane, c.delta, c.games, c.weighted] for c in r.contributions]}
            for r in ev.recs
        ],
    }
