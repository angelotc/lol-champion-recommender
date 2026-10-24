"""Where you are, from the client's gameflow phase and champ select session.

Pure state: feed it phase and session updates, it tells you what changed (events) and where you
are (stage). No I/O here; the controller turns events into renders, storage and fetches.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from lcu import DraftState, parse_session


class Stage(str, Enum):
    IDLE = "idle"          # client open, not queued (or client closed)
    LOBBY = "lobby"
    QUEUE = "queue"
    READY = "ready"        # match found
    BAN = "ban"            # champ select, ban phase
    PICK = "pick"          # champ select, picking
    LOCKED = "locked"      # champ select, you've locked in
    LOADING = "loading"
    INGAME = "ingame"
    POSTGAME = "postgame"


GAMEFLOW = {
    "None": Stage.IDLE, "Lobby": Stage.LOBBY, "Matchmaking": Stage.QUEUE, "CheckedIntoTournament": Stage.QUEUE,
    "ReadyCheck": Stage.READY, "GameStart": Stage.LOADING, "InProgress": Stage.INGAME,
    "Reconnect": Stage.INGAME, "WaitingForStats": Stage.POSTGAME, "PreEndOfGame": Stage.POSTGAME,
    "EndOfGame": Stage.POSTGAME,
}
DRAFT_STAGES = {Stage.BAN, Stage.PICK, Stage.LOCKED}
PRE_DRAFT_STAGES = {Stage.LOBBY, Stage.QUEUE, Stage.READY}


class Event(str, Enum):
    PHASE = "phase"                  # gameflow phase changed
    DRAFT_STARTED = "draft_started"
    DRAFT_CHANGED = "draft_changed"
    LOCKED_IN = "locked_in"          # you locked a champion (once per draft)
    DRAFT_ENDED = "draft_ended"      # champ select over: game starting, dodge, or client gone


def in_ban_phase(session: dict) -> bool:
    """True while any ban action is in progress (the whole lobby bans at once in ranked)."""
    for group in session.get("actions") or []:
        for a in group if isinstance(group, list) else [group]:
            if isinstance(a, dict) and a.get("type") == "ban" and a.get("isInProgress") and not a.get("completed"):
                return True
    return False


@dataclass
class Session:
    phase: str | None = None                       # gameflow phase; None until known (replay, simulate)
    draft: DraftState | None = None
    pickable: set[int] | None = None
    banning: bool = False
    frozen: DraftState | None = None               # draft as it was when you locked in
    enemy_lanes: dict[int, str] = field(default_factory=dict)  # lanes you set by hand

    @property
    def stage(self) -> Stage:
        if self.draft is not None and self.phase in (None, "ChampSelect"):
            if self.draft.my_locked:
                return Stage.LOCKED
            return Stage.BAN if self.banning else Stage.PICK
        if self.phase == "ChampSelect":
            return Stage.PICK  # session not loaded yet
        return GAMEFLOW.get(self.phase or "None", Stage.IDLE)

    @property
    def in_draft(self) -> bool:
        return self.draft is not None

    def on_phase(self, phase: str) -> list[Event]:
        events: list[Event] = []
        if phase != self.phase:
            self.phase = phase
            events.append(Event.PHASE)
        if phase != "ChampSelect" and self.draft is not None:
            events += self.on_session(None)  # don't rely on the session's Delete event to end the draft
        return events

    def on_session(self, session: dict | None, pickable: set[int] | None = None) -> list[Event]:
        if not session:
            if self.draft is None:
                return []
            self.draft = self.frozen = None
            self.pickable, self.banning, self.enemy_lanes = None, False, {}
            return [Event.DRAFT_ENDED]
        events: list[Event] = []
        if self.draft is None:
            self.frozen, self.enemy_lanes = None, {}
            events.append(Event.DRAFT_STARTED)
        draft = parse_session(session)
        self.draft, self.banning = draft, in_ban_phase(session)
        if pickable:
            self.pickable = pickable
        if draft.my_locked and not self.frozen:
            # snapshot without your own pick excluded, so your champion stays in the list
            self.frozen = replace(draft, picked=draft.picked - {draft.my_locked}, bans=set(draft.bans))
            events.append(Event.LOCKED_IN)
        elif not draft.my_locked:
            self.frozen = None
        events.append(Event.DRAFT_CHANGED)
        return events

    def set_enemy_lane(self, champion_id: int, lane: str, lanes: tuple[str, ...]) -> None:
        """Pin an enemy to a lane ("" = back to the inferred lane). A lane holds one champion."""
        self.enemy_lanes = {c: l for c, l in self.enemy_lanes.items() if c != champion_id and l != lane}
        if lane in lanes:
            self.enemy_lanes[champion_id] = lane
