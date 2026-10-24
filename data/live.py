"""The game's local Live Client Data API (https://127.0.0.1:2999, read-only, only while a game runs).

Only your own state (champion, items, gold, level) and every player's champion and position are
used. Enemy items and timers are deliberately ignored: in-game suggestions stay limited to the
build worked out before the game (Riot policy: no dictating decisions, no hidden information).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "middle", "BOTTOM": "bottom", "UTILITY": "support"}


@dataclass
class LiveGame:
    champion: str             # display name, e.g. "Dr. Mundo"
    role: str                 # lane name, "" if the game doesn't say (e.g. practice tool)
    level: int
    gold: float
    items: list[int]          # item ids you hold, slot order
    game_time: float          # seconds
    allies: dict[str, str] = field(default_factory=dict)   # lane -> champion display name (not you)
    enemies: dict[str, str] = field(default_factory=dict)  # lane -> champion display name


def parse(data: dict) -> LiveGame | None:
    ap = (data or {}).get("activePlayer") or {}
    players = data.get("allPlayers") or []
    me_id = ap.get("riotId") or ap.get("summonerName")
    me = next((p for p in players if (p.get("riotId") or p.get("summonerName")) == me_id), None)
    if not me:
        return None
    team = me.get("team")
    allies, enemies = {}, {}
    for p in players:
        lane = POSITIONS.get(str(p.get("position") or "").upper(), "")
        if p is me or not lane:
            continue
        (allies if p.get("team") == team else enemies)[lane] = p.get("championName", "")
    return LiveGame(
        champion=me.get("championName", ""), role=POSITIONS.get(str(me.get("position") or "").upper(), ""),
        level=int(ap.get("level") or me.get("level") or 0), gold=float(ap.get("currentGold") or 0),
        items=[int(i["itemID"]) for i in sorted(me.get("items") or [], key=lambda i: i.get("slot", 0)) if i.get("itemID")],
        game_time=float((data.get("gameData") or {}).get("gameTime") or 0), allies=allies, enemies=enemies,
    )


class LiveClient:
    def __init__(self, timeout: float = 1.5):
        self.http = httpx.Client(timeout=timeout, verify=False)  # localhost self-signed cert only

    def game(self) -> LiveGame | None:
        try:
            r = self.http.get(URL)
            if r.status_code != 200:
                return None
            return parse(r.json())
        except (httpx.HTTPError, ValueError):
            return None

    def close(self) -> None:
        self.http.close()
