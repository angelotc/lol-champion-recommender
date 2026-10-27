from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import ssl
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx
import websockets

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE = r"C:\Riot Games\League of Legends\lockfile"
POSITIONS = {"utility": "support"}  # LCU name -> lane name
SESSION_EVENT = "OnJsonApiEvent_lol-champ-select_v1_session"
PHASE_EVENT = "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"


class LcuError(RuntimeError):
    pass


@dataclass
class LcuCreds:
    port: int
    password: str
    protocol: str = "https"
    pid: int = 0
    name: str = "LeagueClient"


SCAN_RETRY = 180.0  # seconds a "no client found" scan is trusted, so closed-client reconnects
# don't spawn PowerShell on every backoff retry; reset to None whenever a scan succeeds
_scan_failed_at: float | None = None


def _creds_from_process() -> LcuCreds:
    global _scan_failed_at
    if _scan_failed_at is not None and time.monotonic() - _scan_failed_at < SCAN_RETRY:
        raise LcuError("client not running (recent scan found none)")
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\" "
        "| Select-Object -ExpandProperty CommandLine"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,  # no console flash while the client is closed
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise LcuError(f"client not running ({e})") from e
    port = re.search(r"--app-port=(\d+)", out)
    token = re.search(r"--remoting-auth-token=([\w-]+)", out)
    if not (port and token):
        _scan_failed_at = time.monotonic()
        raise LcuError("client not running (no lockfile, no LeagueClientUx process)")
    _scan_failed_at = None
    return LcuCreds(int(port[1]), token[1])


def read_lockfile(path: str | Path | None = None) -> LcuCreds:
    p = Path(path or DEFAULT_LOCKFILE)
    try:
        text = p.read_text().strip()
    except OSError:
        return _creds_from_process()
    try:
        name, pid, port, password, protocol = text.split(":")[:5]
        return LcuCreds(int(port), password, protocol, int(pid), name)
    except ValueError:
        return _creds_from_process()


class LcuClient:
    def __init__(self, creds: LcuCreds):
        self.client = httpx.Client(
            base_url=f"https://127.0.0.1:{creds.port}",
            auth=("riot", creds.password),
            verify=False,  # localhost self-signed cert only
            timeout=5.0,
        )

    def get(self, path: str) -> httpx.Response:
        return self.client.get(path)

    def get_session(self) -> dict | None:
        r = self.get("/lol-champ-select/v1/session")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_pickable_ids(self) -> set[int]:
        r = self.get("/lol-champ-select/v1/pickable-champion-ids")
        if r.status_code == 404:
            return set()
        r.raise_for_status()
        return {int(i) for i in r.json()}

    def get_phase(self) -> str:
        """Gameflow phase: None, Lobby, Matchmaking, ReadyCheck, ChampSelect, InProgress, ..."""
        r = self.get("/lol-gameflow/v1/gameflow-phase")
        r.raise_for_status()
        return str(r.json())

    def get_lobby_positions(self) -> list[str]:
        """Your first and second position preference in the current lobby, as lane names
        (empty outside a lobby, or for fill / unselected)."""
        r = self.get("/lol-lobby/v2/lobby")
        if r.status_code != 200:
            return []
        me = (r.json() or {}).get("localMember") or {}
        out = []
        for key in ("firstPositionPreference", "secondPositionPreference"):
            pos = str(me.get(key) or "").lower()
            pos = POSITIONS.get(pos, pos)
            if pos in ("top", "jungle", "middle", "bottom", "support") and pos not in out:
                out.append(pos)
        return out

    def get_patch(self) -> str:
        r = self.get("/lol-patch/v1/game-version")
        r.raise_for_status()
        return ".".join(str(r.json()).strip('"').split(".")[:2])

    def close(self) -> None:
        self.client.close()


async def watch_session(
    creds: LcuCreds | Callable[[], LcuCreds],
    callback: Callable[[dict | None], Awaitable[None] | None],
    max_backoff: float = 15.0,
    on_phase: Callable[[str], Awaitable[None] | None] | None = None,
) -> None:
    """Push champ select session updates (None = no session / client gone) to callback,
    and gameflow phase changes to on_phase ("None" when the client goes away).

    Pass a callable for `creds` to re-resolve them on reconnect (client restarts change the port).
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # localhost self-signed cert only

    async def emit(data: dict | None, fn: Callable = callback) -> None:
        res = fn(data)
        if asyncio.iscoroutine(res):
            await res

    async def emit_phase(phase: str) -> None:
        if on_phase:
            await emit(phase, on_phase)

    delay = 1.0
    connected = False
    while True:
        try:
            c = creds() if callable(creds) else creds
            auth = base64.b64encode(f"riot:{c.password}".encode()).decode()
            async with websockets.connect(
                f"wss://127.0.0.1:{c.port}/", ssl=ctx,
                additional_headers={"Authorization": f"Basic {auth}"},
            ) as ws:
                await ws.send(json.dumps([5, SESSION_EVENT]))
                if on_phase:
                    await ws.send(json.dumps([5, PHASE_EVENT]))
                connected, delay = True, 1.0
                client = LcuClient(c)
                try:
                    if on_phase:
                        await emit_phase(await asyncio.to_thread(client.get_phase))
                    await emit(await asyncio.to_thread(client.get_session))
                except httpx.HTTPError:
                    pass
                finally:
                    client.close()
                async for msg in ws:
                    if not msg:
                        continue
                    try:
                        payload = json.loads(msg)
                    except ValueError:
                        continue
                    if not (isinstance(payload, list) and len(payload) >= 3 and payload[0] == 8):
                        continue
                    body = payload[2] or {}
                    if payload[1] == PHASE_EVENT:
                        await emit_phase(str(body.get("data")))
                        continue
                    await emit(None if body.get("eventType") == "Delete" else body.get("data"))
        except (OSError, LcuError, websockets.WebSocketException) as e:
            log.debug("lcu connection: %s", e)
        except Exception:  # bad data or a callback error: log it and reconnect, never stop watching
            log.exception("lcu watch")
        if connected:
            connected = False
            try:
                await emit_phase("None")
                await emit(None)
            except Exception:
                log.exception("lcu watch")
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_backoff)


@dataclass
class Slot:
    champion_id: int
    position: str
    tentative: bool
    is_me: bool


@dataclass
class DraftState:
    my_cell_id: int = -1
    my_position: str = ""
    allies: list[Slot] = field(default_factory=list)
    enemies: list[int] = field(default_factory=list)
    bans: set[int] = field(default_factory=set)
    picked: set[int] = field(default_factory=set)
    my_locked: int = 0  # champion the local player has locked in, 0 if not yet
    my_turn: bool = False  # your pick is in progress right now


def _id(v) -> int:
    return v if isinstance(v, int) else 0


def parse_session(session: dict) -> DraftState:
    session = session or {}
    my_cell = session.get("localPlayerCellId", -1)
    st = DraftState(my_cell_id=my_cell if isinstance(my_cell, int) else -1)
    for p in session.get("myTeam") or []:
        cid = _id(p.get("championId"))
        intent = _id(p.get("championPickIntent"))
        is_me = p.get("cellId") == st.my_cell_id
        pos = (p.get("assignedPosition") or "").lower()
        pos = POSITIONS.get(pos, pos)
        if is_me:
            st.my_position = pos
        st.allies.append(Slot(cid or intent, pos, cid == 0 and intent != 0, is_me))
    st.enemies = [c for p in session.get("theirTeam") or [] if (c := _id(p.get("championId")))]
    bans = session.get("bans") or {}
    for key in ("myTeamBans", "theirTeamBans"):
        st.bans |= {c for c in bans.get(key) or [] if _id(c) > 0}
    for group in session.get("actions") or []:
        for a in group if isinstance(group, list) else [group]:
            if not isinstance(a, dict):
                continue
            if a.get("type") == "pick" and a.get("isInProgress") and a.get("actorCellId") == st.my_cell_id:
                st.my_turn = True
            if not a.get("completed"):
                continue
            cid = _id(a.get("championId"))
            if cid <= 0:
                continue
            if a.get("type") == "ban":
                st.bans.add(cid)
            elif a.get("type") == "pick":
                st.picked.add(cid)
                if a.get("actorCellId") == st.my_cell_id:
                    st.my_locked = cid
    st.picked |= {s.champion_id for s in st.allies if s.champion_id and not s.tentative}
    st.picked |= set(st.enemies)
    return st
