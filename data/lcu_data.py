"""Your own data from the League client (GET only): mastery and match history.

The client's match history list returns only your ~21 newest games, so finished games are copied
into the local store after every game; over time the store holds far more than the client shows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from data.store import Store
from lcu import LcuClient

log = logging.getLogger(__name__)
RANKED_QUEUES = {420, 440}  # solo/duo, flex
# timeline (lane, role) -> lane; the client's labels are noisy, so prefer the draft's assigned role
TIMELINE = {
    ("TOP", None): "top", ("JUNGLE", None): "jungle", ("MIDDLE", None): "middle", ("MID", None): "middle",
    ("BOTTOM", "SUPPORT"): "support", ("BOTTOM", "DUO_SUPPORT"): "support", ("NONE", "SUPPORT"): "support",
    ("BOTTOM", "CARRY"): "bottom", ("BOTTOM", "DUO_CARRY"): "bottom", ("BOTTOM", "SOLO"): "bottom",
    ("BOTTOM", "DUO"): "bottom",
}


@dataclass
class Mastery:
    points: int
    level: int
    last_play: float  # epoch seconds


def mastery(client: LcuClient) -> dict[int, Mastery]:
    r = client.get("/lol-champion-mastery/v1/local-player/champion-mastery")
    if r.status_code != 200:
        return {}
    return {
        int(m["championId"]): Mastery(int(m.get("championPoints") or 0), int(m.get("championLevel") or 0),
                                      (m.get("lastPlayTime") or 0) / 1000)
        for m in r.json() or [] if isinstance(m, dict) and m.get("championId")
    }


def role_from_timeline(timeline: dict) -> str:
    lane, role = str(timeline.get("lane") or "").upper(), str(timeline.get("role") or "").upper()
    return TIMELINE.get((lane, role)) or TIMELINE.get((lane, None)) or ""


def _mine(game: dict) -> dict | None:
    """Your participant entry: the list endpoint only includes you."""
    parts = game.get("participants") or []
    return parts[0] if len(parts) == 1 else None


def sync_history(client: LcuClient, store: Store, queues: set[int] = RANKED_QUEUES) -> int:
    """Copy new ranked games from the client's history into the store. Returns how many were added."""
    r = client.get("/lol-match-history/v1/products/lol/current-summoner/matches?begIndex=0&endIndex=20")
    if r.status_code != 200:
        return 0
    games = ((r.json() or {}).get("games") or {}).get("games") or []
    added = 0
    for g in games:
        gid, queue = g.get("gameId"), g.get("queueId")
        if not gid or queue not in queues or store.has_game(gid):
            continue
        me = _mine(g)
        if not me:
            continue
        detail = g
        full = client.get(f"/lol-match-history/v1/games/{gid}")
        if full.status_code == 200:
            detail = full.json() or g
        created = (g.get("gameCreation") or 0) / 1000
        champ = int(me.get("championId") or 0)
        role = store.draft_role_for(champ, created) or role_from_timeline(me.get("timeline") or {})
        win = bool((me.get("stats") or {}).get("win"))
        store.add_game(gid, created, queue, champ, role, win, int(g.get("gameDuration") or 0),
                       {"participantId": me.get("participantId"), "game": detail})
        store.link_draft(gid, champ, created, win)
        added += 1
    if added:
        log.info("stored %d new games", added)
    return added
