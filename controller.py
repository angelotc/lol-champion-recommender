"""Turns phase/session updates into panel views, and records drafts to the store.

Sources (live LCU, replay, simulate) call on_phase / on_session; the panel calls set_tier,
set_enemy_lane and hide via the window API. All state changes go through one lock.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import views
from config import Config
from data import lcu_data
from data.store import Store
from engine import Evaluation, Recommender
from paths import DATA_DIR
from lcu import LcuClient
from scoring import LANES, Comfort
from state import DRAFT_STAGES, PRE_DRAFT_STAGES, Event, Session, Stage

SETTINGS = DATA_DIR / "settings.json"
SYNC_EVERY = 20.0  # seconds; history/mastery sync is cheap but not free
log = logging.getLogger("controller")


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> None:
    try:
        SETTINGS.write_text(json.dumps(load_settings() | changes), encoding="utf-8")
    except OSError:
        log.warning("could not save settings")


class Controller:
    def __init__(self, cfg: Config, push: Callable[[dict], None], store: Store | None = None):
        self.cfg = cfg
        self.push = push
        self.store = store
        self.rec: Recommender | None = None
        self.session = Session()
        self.lock = threading.RLock()  # updates arrive from the LCU thread and from panel clicks
        self.user_hidden = False
        self.idle_message = "Waiting for the League client..."
        self.notice: tuple[str, str] | None = None  # (message, kind) shown instead of the stage view
        self.draft_id: int | None = None
        self.last_eval: Evaluation | None = None
        self.tab: str | None = None        # "bans" | "picks" chosen by you during the ban phase
        self.lcu: Callable[[], LcuClient] | None = None  # client factory, set by the live source
        self.mastery: dict[int, lcu_data.Mastery] = {}
        self._synced_at = 0.0
        self.preload: str = ""             # progress line while preloading in lobby/queue
        self._preloaded: set[tuple[str, str]] = set()

    # -- setup ------------------------------------------------------------
    def boot(self) -> None:
        self.show_status("Loading champion data...")
        self.rec = Recommender(self.cfg)
        tier = load_settings().get("tier")
        if tier in views.TIERS:
            self.rec.tier = tier
        self.rec.progress = self.progress
        self.rec.comfort = self.comfort

    def comfort(self, role: str) -> dict[int, Comfort]:
        """Your record (from stored games) and mastery per champion, for scoring."""
        record = {}
        if self.store:
            try:
                record = self.store.champ_record(role=role)
            except Exception:
                log.exception("champ record")
        out = {cid: Comfort(mastery=m.points) for cid, m in self.mastery.items()}
        for cid, (games, wins) in record.items():
            out[cid] = Comfort(games, wins, out[cid].mastery if cid in out else 0)
        return out

    def lobby_roles(self) -> list[str]:
        if not self.lcu:
            return []
        client = self.lcu()
        try:
            return client.get_lobby_positions()
        finally:
            client.close()

    @property
    def tier(self) -> str:
        return self.rec.tier if self.rec else self.cfg.tier

    def header(self, role: str | None = None) -> dict:
        return views.header(role, self.tier, self.cfg.days)

    # -- output -----------------------------------------------------------
    def show_status(self, message: str, kind: str = "waiting") -> None:
        self.push(views.status(self.header(), message, kind))

    def progress(self, message: str) -> None:
        self.show_status(message)  # transient: the next render replaces it

    def render(self) -> None:
        """Show the view for the current stage, re-scoring the draft with current settings."""
        with self.lock:
            if self.notice:
                self.show_status(*self.notice)
                return
            s = self.session
            stage = s.stage
            if stage not in DRAFT_STAGES or not s.draft:
                msg = self.idle_message if stage == Stage.IDLE else views.STAGE_MESSAGES.get(stage, "")
                self.push(views.status(self.header(), msg) | {"detail": self.preload if stage in PRE_DRAFT_STAGES else ""})
                return
            if not self.rec or self.rec.role_for(s.draft) is None:
                self.show_status("No assigned role in this queue - recommendations need a position (draft or ranked).")
                return
            tabs = {"tabs": ["bans", "picks"], "tab": self.tab or "bans"} if stage == Stage.BAN else {}
            try:
                if tabs and tabs["tab"] == "bans":
                    bev = self.rec.bans(s.draft, s.pickable)
                    self.push(views.bans(self.header(bev.role), bev, self.rec.champs) | tabs)
                    return
                ev = self.rec.evaluate(s.draft, s.pickable, s.enemy_lanes)
                if s.frozen:  # teams stay live, recommendations stay as they were at lock-in
                    ev.recs = self.rec.evaluate(s.frozen, s.pickable, s.enemy_lanes).recs
                self.last_eval = ev
                self.push(views.picks(self.header(ev.role), ev, self.rec.champs, s.enemy_lanes) | tabs)
            except Exception:
                log.exception("evaluate failed")
                self.show_status("Error scoring draft (see app.log)", "error")

    # -- inputs -----------------------------------------------------------
    def on_phase(self, phase: str) -> None:
        with self.lock:
            if phase != self.session.phase:
                log.info("gameflow phase: %s", phase)
            self.handle(self.session.on_phase(phase))

    def on_session(self, session: dict | None, pickable: set[int] | None = None) -> None:
        with self.lock:
            events = self.session.on_session(session, pickable)
            if session and self.draft_id is not None:
                self._record(lambda: self.store.snapshot(self.draft_id, session))
            self.handle(events, session)

    def handle(self, events: list[Event], session: dict | None = None) -> None:
        if not events:
            return
        s = self.session
        if Event.PHASE in events and s.stage in PRE_DRAFT_STAGES:
            self.start_preload()
        if Event.PHASE in events and s.stage in (Stage.IDLE, Stage.LOBBY, Stage.POSTGAME):
            self.start_sync(delay=5.0 if s.stage == Stage.POSTGAME else 0.0)
        if Event.DRAFT_STARTED in events:
            self.user_hidden = False  # new draft: bring the panel back
            self.tab, self.preload = None, ""
            if self.store:
                role = self.rec.role_for(s.draft) if self.rec and s.draft else None
                self.draft_id = self._record(lambda: self.store.start_draft(role, self.tier))
                if session and self.draft_id is not None:
                    self._record(lambda: self.store.snapshot(self.draft_id, session))
        self.render()
        if Event.LOCKED_IN in events and self.draft_id is not None and self.last_eval:
            ev = self.last_eval
            self._record(lambda: self.store.update_draft(self.draft_id, my_champ=s.draft.my_locked, role=ev.role))
            self._record(lambda: self.store.save_recs(self.draft_id, "locked", views.record(ev)))
        if Event.DRAFT_ENDED in events and self.draft_id is not None:
            draft_id, ev = self.draft_id, self.last_eval
            if ev:
                self._record(lambda: self.store.save_recs(draft_id, "final", views.record(ev)))
            self._record(lambda: self.store.update_draft(draft_id, ended=time.time()))
            self.draft_id, self.last_eval = None, None

    def _record(self, fn: Callable):
        """Store writes never break the panel."""
        if not self.store:
            return None
        try:
            return fn()
        except Exception:
            log.exception("store write failed")
            return None

    # -- your games and mastery --------------------------------------------
    def start_sync(self, delay: float = 0.0) -> None:
        """Copy finished games into the store and refresh mastery, in the background."""
        if not (self.lcu and self.store) or time.time() - self._synced_at < SYNC_EVERY:
            return
        self._synced_at = time.time()
        threading.Thread(target=self._sync, args=(delay,), daemon=True, name="sync").start()

    def _sync(self, delay: float) -> None:
        time.sleep(delay)  # the client publishes a finished game a few seconds after it ends
        try:
            client = self.lcu()
            try:
                self.mastery = lcu_data.mastery(client) or self.mastery
                lcu_data.sync_history(client, self.store)
            finally:
                client.close()
        except Exception:
            log.exception("history sync")

    # -- preloading ------------------------------------------------------
    def start_preload(self) -> None:
        """Load stats for your lobby position preferences in the background, so the draft opens
        with recommendations instead of a loading bar."""
        if not self.rec:
            return
        try:
            roles = [r for r in self.lobby_roles() if (self.tier, r) not in self._preloaded]
        except Exception:
            log.exception("lobby positions")
            return
        if not roles:
            return
        self._preloaded |= {(self.tier, r) for r in roles}
        threading.Thread(target=self._preload, args=(roles, self.tier), daemon=True, name="preload").start()

    def _preload(self, roles: list[str], tier: str) -> None:
        for role in roles:
            if self.rec.has_pool(role, tier):
                continue

            def progress(msg: str) -> None:
                self.preload = "Preloading " + msg.removeprefix("Loading ")
                # never block: a draft render may hold the lock while waiting for this very pool
                if not self.lock.acquire(blocking=False):
                    return
                try:
                    if self.session.stage in PRE_DRAFT_STAGES and not self.notice:
                        self.render()
                finally:
                    self.lock.release()

            try:
                self.rec.pool(role, progress=progress, tier=tier)
            except Exception:
                log.exception("preload %s", role)
                self._preloaded.discard((tier, role))
        self.preload = "Stats ready: " + ", ".join(roles)
        with self.lock:
            if self.session.stage in PRE_DRAFT_STAGES:
                self.render()

    # -- panel actions ----------------------------------------------------
    def set_tab(self, tab: str) -> None:
        if tab in ("bans", "picks"):
            with self.lock:
                self.tab = tab
                self.render()

    def set_tier(self, tier: str) -> None:
        if tier not in views.TIERS or not self.rec:
            return
        with self.lock:
            self.rec.tier = tier
            save_settings(tier=tier)
            self.render()

    def set_enemy_lane(self, champion_id: int, lane: str) -> None:
        with self.lock:
            self.session.set_enemy_lane(champion_id, lane, LANES)
            self.render()

    def fail(self, message: str) -> None:
        with self.lock:
            self.notice = (message, "error")
            self.render()
