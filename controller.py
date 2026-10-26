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
from data.live import LiveClient, LiveGame
from data.store import Store
from engine import BuildEvaluation, Evaluation, Recommender
from paths import DATA_DIR
from lcu import LcuClient
from scoring import LANES, Comfort, DraftOdds, draft_odds
from state import DRAFT_STAGES, PRE_DRAFT_STAGES, Event, Session, Stage

SETTINGS = DATA_DIR / "settings.json"
SYNC_EVERY = 20.0  # seconds; history/mastery sync is cheap but not free
LIVE_POLL = 2.0  # seconds between game data reads while the in-game panel is shown
STAGE_TABS = {Stage.BAN: ["bans", "picks"], Stage.LOCKED: ["build", "picks"]}  # first is the default
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
        self.last_odds: DraftOdds | None = None
        self._stats_pending: set[tuple[int, str]] = set()
        self._build_cache: tuple[tuple, BuildEvaluation] | None = None
        self.tab: str | None = None        # "bans" | "picks" chosen by you during the ban phase
        self.lcu: Callable[[], LcuClient] | None = None  # client factory, set by the live source
        self.mastery: dict[int, lcu_data.Mastery] = {}
        self._synced_at = 0.0
        self.preload: str = ""             # progress line while preloading in lobby/queue
        self._preloaded: set[tuple[str, str]] = set()
        self.ingame_shown = False          # Ctrl+X in game; stays as you left it for the next game
        self.live: LiveGame | None = None
        self.last_role: str = ""           # role from your last draft (games without positions)
        self._live_client: LiveClient | None = None

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
                half_life = self.cfg.weights_for(role)["comfort"].get("half_life", 0)
                record = self.store.champ_record(role=role, half_life_days=half_life)
            except Exception:
                log.exception("champ record")
        out = {cid: Comfort(mastery=m.points) for cid, m in self.mastery.items()}
        for cid, (games, wins, eff_games, eff_wins) in record.items():
            out[cid] = Comfort(games, wins, out[cid].mastery if cid in out else 0, eff_games, eff_wins)
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
            if stage == Stage.INGAME and self.ingame_shown:
                self._render_ingame()
                return
            if stage not in DRAFT_STAGES or not s.draft:
                msg = self.idle_message if stage == Stage.IDLE else views.STAGE_MESSAGES.get(stage, "")
                self.push(views.status(self.header(), msg) | {"detail": self.preload if stage in PRE_DRAFT_STAGES else ""})
                return
            if not self.rec or self.rec.role_for(s.draft) is None:
                self.show_status("No assigned role in this queue - recommendations need a position (draft or ranked).")
                return
            if stage == Stage.BAN and not self.cfg.show_ban_suggestions:
                self.push(views.status(self.header(self.rec.role_for(s.draft)), views.BAN_MESSAGE))
                return
            if stage == Stage.PICK and not (s.draft.picked or s.draft.my_turn):
                # recommendations start with the first locked pick (or your turn, if you're first)
                self.push(views.status(self.header(self.rec.role_for(s.draft)), views.FIRST_PICK_MESSAGE))
                return
            tabs = {}
            if stage in STAGE_TABS:
                tabs = {"tabs": STAGE_TABS[stage], "tab": self.tab if self.tab in STAGE_TABS[stage] else STAGE_TABS[stage][0]}
            try:
                if tabs.get("tab") == "bans":
                    bev = self.rec.bans(s.draft, s.pickable)
                    self.push(views.bans(self.header(bev.role), bev, self.rec.champs) | tabs)
                    return
                ev = self.rec.evaluate(s.draft, s.pickable, s.enemy_lanes)
                if s.frozen:  # teams stay live, recommendations stay as they were at lock-in
                    ev.recs = self.rec.evaluate(s.frozen, s.pickable, s.enemy_lanes).recs
                self.last_eval = ev
                if tabs.get("tab") == "build":
                    self.push(views.build(self.header(ev.role), self._build(ev), self.rec.champs, self.rec.assets) | tabs)
                    return
                odds = self._odds(ev)
                self.last_odds = odds
                self.push(views.picks(self.header(ev.role), ev, self.rec.champs, s.enemy_lanes)
                          | tabs | {"odds": views.odds(odds, self.rec.champs)})
            except Exception:
                log.exception("evaluate failed")
                self.show_status("Error scoring draft (see app.log)", "error")

    def _odds(self, ev: Evaluation) -> DraftOdds | None:
        """Draft win estimate from the stats loaded so far; missing stats load in the background."""
        ours_ids, theirs_ids = self.rec.odds_inputs(ev)
        if not ours_ids and not theirs_ids:
            return None
        missing, sides = [], ({}, {})
        for side, team in zip(sides, (ours_ids, theirs_ids)):
            for lane, (cid, tent) in team.items():
                st = self.rec.cached_lane_stats(cid, lane)
                if st is False:
                    missing.append((cid, lane))
                elif st:
                    side[lane] = (st, tent)
        if missing:
            self._load_stats(missing)
        odds = draft_odds(sides[0], sides[1], self.cfg.k, self.cfg.odds, self.cfg.tentative_factor)
        odds.known = sum(map(len, sides))
        odds.pending = len(missing)
        return odds

    def _load_stats(self, need: list[tuple[int, str]]) -> None:
        need = [n for n in need if n not in self._stats_pending]
        if not need:
            return
        self._stats_pending |= set(need)

        def run() -> None:
            try:
                list(self.rec.lola.fetch.map(lambda n: self.rec.lane_stats(*n), need))
            except Exception:
                log.exception("draft odds stats")
            finally:
                self._stats_pending -= set(need)
            if self.lock.acquire(blocking=False):  # never wait: a render may be in progress
                try:
                    if self.session.stage in DRAFT_STAGES:
                        self.render()
                finally:
                    self.lock.release()
            else:
                threading.Timer(0.5, self._retry_render).start()

        threading.Thread(target=run, daemon=True, name="odds-stats").start()

    def _retry_render(self) -> None:
        if self.lock.acquire(blocking=False):
            try:
                if self.session.stage in DRAFT_STAGES:
                    self.render()
            finally:
                self.lock.release()

    def _render_ingame(self) -> None:
        g = self.live
        if not g or not self.rec:
            self.show_status("Waiting for game data...")
            return
        cid = self.rec.champs.id_from_slug(g.champion)
        role = g.role or self.last_role
        if not cid or not role:
            self.show_status("No build: champion or role unknown in this game.")
            return
        enemies = {lane: e for lane, name in g.enemies.items() if (e := self.rec.champs.id_from_slug(name))}
        try:
            bev = self._build_for(cid, role, enemies)
            self.push(views.ingame(self.header(role), g, bev, self.rec.champs, self.rec.assets))
        except Exception:
            log.exception("in-game build failed")
            self.show_status("Error loading build (see app.log)", "error")

    def _build(self, ev: Evaluation) -> BuildEvaluation:
        """Build for your locked champion vs the enemies' lanes, memoized per draft state."""
        enemies = {lane: cid for cid, lane in ev.lanes.items() if cid in ev.draft.enemies}
        return self._build_for(ev.draft.my_locked, ev.role, enemies)

    def _build_for(self, cid: int, role: str, enemies: dict[str, int]) -> BuildEvaluation:
        key = (cid, role, self.tier, tuple(sorted(enemies.items())))
        if self._build_cache and self._build_cache[0] == key:
            return self._build_cache[1]
        bev = self.rec.build(cid, role, enemies)
        self._build_cache = (key, bev)
        return bev

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
        if Event.LOCKED_IN in events:
            self.tab = None  # show the build first, whatever tab you had in the ban phase
        self.render()
        if Event.LOCKED_IN in events and self.last_eval:
            self.last_role = self.last_eval.role
        if Event.LOCKED_IN in events and self.draft_id is not None and self.last_eval:
            ev = self.last_eval
            self._record(lambda: self.store.update_draft(self.draft_id, my_champ=s.draft.my_locked, role=ev.role))
            self._record(lambda: self.store.save_recs(self.draft_id, "locked", views.record(ev, self.last_odds)))
        if Event.DRAFT_ENDED in events and self.draft_id is not None:
            draft_id, ev = self.draft_id, self.last_eval
            if ev:
                self._record(lambda: self.store.save_recs(draft_id, "final", views.record(ev, self.last_odds)))
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

    # -- in game -----------------------------------------------------------
    def toggle_ingame(self) -> None:
        with self.lock:
            self.ingame_shown = not self.ingame_shown
        log.info("in-game panel %s", "on" if self.ingame_shown else "off")
        if self.ingame_shown:
            try:
                log.info("game data: %s", "found" if self.poll_live() else "not available")
            except Exception:
                log.exception("in-game panel")

    def poll_live(self) -> bool:
        """Read the game's local data once; re-render if your state changed. True if a game is up."""
        if self._live_client is None:
            self._live_client = LiveClient()
        g = self._live_client.game()
        changed = g != self.live
        self.live = g
        if changed:
            self.render()
        return g is not None

    def live_loop(self) -> None:
        """Background: keep game data fresh while the in-game panel is shown."""
        while True:
            if self.ingame_shown and self.session.stage == Stage.INGAME:
                try:
                    self.poll_live()
                except Exception:
                    log.exception("live poll")
            time.sleep(LIVE_POLL)

    # -- panel actions ----------------------------------------------------
    def set_tab(self, tab: str) -> None:
        if any(tab in t for t in STAGE_TABS.values()):
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

    def reload_config(self, cfg: Config) -> None:
        """Settings saved: score with the new values from now on and redraw. (Tier, days and the
        lockfile path are fixed at startup.)"""
        with self.lock:
            self.cfg = cfg
            if self.rec:
                self.rec.cfg = cfg
            self.render()

    def fail(self, message: str) -> None:
        with self.lock:
            self.notice = (message, "error")
            self.render()
