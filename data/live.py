"""The game's local Live Client Data API (https://127.0.0.1:2999, read-only, only while a game runs).

Only your own state (champion, items, gold, level) and every player's champion and position are
used. Enemy items and timers are deliberately ignored: in-game suggestions stay limited to the
build worked out before the game (Riot policy: no dictating decisions, no hidden information).

GameRecorder (off unless `record_games` is set) saves the full raw data to disk now and then, so
the fields and event names can be studied after a game. It changes nothing on screen.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

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

    def raw(self) -> dict | None:
        """The full allgamedata JSON, or None when no game is running."""
        try:
            r = self.http.get(URL)
            if r.status_code != 200:
                return None
            data = r.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    def game(self) -> LiveGame | None:
        data = self.raw()
        return parse(data) if data else None

    def close(self) -> None:
        self.http.close()


class GameRecorder:
    """Appends a raw snapshot every `every` seconds to <folder>/<game start>.jsonl.

    One line per snapshot: {"t": unix time, "data": allgamedata}. Events accumulate in the data,
    so the last line of a file holds every event of the game.
    """

    def __init__(self, folder: Path, every: float = 15.0):
        self.folder, self.every = folder, every
        self.path: Path | None = None
        self.last = 0.0

    def due(self) -> bool:
        return time.time() - self.last >= self.every

    def save(self, data: dict) -> None:
        now = time.time()
        if self.path is None:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path = self.folder / (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + ".jsonl")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(now, 1), "data": data}) + "\n")
        self.last = now

    def end(self) -> None:
        """The game is over: the next snapshot starts a new file."""
        self.path, self.last = None, 0.0
