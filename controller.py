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
from data.store import Store
from engine import Evaluation, Recommender
from paths import DATA_DIR
from scoring import LANES
from state import DRAFT_STAGES, Event, Session, Stage

SETTINGS = DATA_DIR / "settings.json"
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

    # -- setup ------------------------------------------------------------
    def boot(self) -> None:
        self.show_status("Loading champion data...")
        self.rec = Recommender(self.cfg)
        tier = load_settings().get("tier")
        if tier in views.TIERS:
            self.rec.tier = tier
        self.rec.progress = self.progress

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
                self.show_status(msg)
                return
            if not self.rec or self.rec.role_for(s.draft) is None:
                self.show_status("No assigned role in this queue - recommendations need a position (draft or ranked).")
                return
            try:
                ev = self.rec.evaluate(s.draft, s.pickable, s.enemy_lanes)
                if s.frozen:  # teams stay live, recommendations stay as they were at lock-in
                    ev.recs = self.rec.evaluate(s.frozen, s.pickable, s.enemy_lanes).recs
                self.last_eval = ev
                self.push(views.picks(self.header(ev.role), ev, self.rec.champs, s.enemy_lanes))
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
        if Event.DRAFT_STARTED in events:
            self.user_hidden = False  # new draft: bring the panel back
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

    # -- panel actions ----------------------------------------------------
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
