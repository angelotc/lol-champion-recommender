"""Desktop panel: docks beside the League client during champ select.

  uv run app.py                          live
  uv run app.py --record drafts/x.jsonl  live, and save every session update for replay
  uv run app.py --replay drafts/x.jsonl  replay a recorded draft (panel always shown)
  uv run app.py --simulate --role middle --allies jinx --enemies syndra,leesin

Ctrl+Shift+L toggles the panel.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

import webview

import tracker
from config import load_config
from engine import Evaluation, Recommender
from lcu import DraftState, LcuClient, Slot, parse_session, read_lockfile, watch_session
from paths import DATA_DIR, RESOURCES
from scoring import LANES

TITLE = "League Pick Helper"
TIERS = {"master_plus": "Master+", "grandmaster_plus": "GM+", "challenger": "Challenger"}
SETTINGS = DATA_DIR / "settings.json"
LOW_DATA = 0.25  # below this share of possible matchups found, warn that the tier is thin
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x0312
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT = 0, 1, 2
HANDLER_ROUTINE = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)

log = logging.getLogger("app")


class Api:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self, app: "App"):
        self._app = app

    def hide(self) -> None:
        self._app.user_hidden = True

    def set_tier(self, tier: str) -> None:
        self._app.set_tier(tier)


class App:
    def __init__(self, args):
        self.args = args
        self.always_show = bool(args.replay or args.simulate)
        self.active = False  # in champ select
        self.user_hidden = False
        self.window: webview.Window | None = None
        self.rec: Recommender | None = None
        self.lock = threading.RLock()  # evaluation runs from the LCU thread and from pill clicks
        self.last_draft: tuple[DraftState, set[int] | None] | None = None
        self.last_status = ("Waiting for champ select...", "waiting")

    # -- page updates -----------------------------------------------------
    def js(self, code: str) -> None:
        if self.window:
            self.window.evaluate_js(code)

    def push(self, state: dict) -> None:
        self.js(f"window.updateState&&updateState({json.dumps(state)})")

    def status(self, message: str, status: str = "waiting", remember: bool = True) -> None:
        if remember:
            self.last_status = (message, status)
        self.push(self._header() | {"status": status, "message": message})

    def progress(self, message: str) -> None:
        self.status(message, remember=False)

    def _header(self, role: str | None = None) -> dict:
        cfg = self.rec.cfg if self.rec else load_config(self.args.config)
        tier = self.rec.tier if self.rec else cfg.tier
        return {
            "role": role or "", "tier": TIERS.get(tier, tier), "tier_id": tier, "days": cfg.days,
            "tiers": [{"id": k, "label": v} for k, v in TIERS.items()],
        }

    def set_tier(self, tier: str) -> None:
        if tier not in TIERS or not self.rec:
            return
        with self.lock:
            self.rec.tier = tier
            try:
                SETTINGS.write_text(json.dumps({"tier": tier}), encoding="utf-8")
            except OSError:
                log.warning("could not save settings")
            self.render()

    def render(self) -> None:
        """Re-score the last draft (or re-show the last status) with current settings."""
        with self.lock:
            if not (self.active and self.last_draft):
                self.status(*self.last_status)
                return
            draft, pickable = self.last_draft
            try:
                self.show_eval(self.rec.evaluate(draft, pickable))
            except Exception:
                log.exception("evaluate failed")
                self.status("Error scoring draft (see app.log)", "error")

    def show_eval(self, ev: Evaluation) -> None:
        ch = self.rec.champs
        champ = lambda cid: {"name": ch.name(cid), "icon": ch.icon_url(cid)}
        d = ev.draft
        found = sum(1 for r in ev.recs for c in r.contributions if c.label == "vs")
        possible = len(ev.recs) * len(d.enemies)
        note = ""
        if possible and found / possible < LOW_DATA:
            note = "Few matchups at this tier: scores lean on overall win rate."
        self.push(self._header(ev.role) | {
            "status": "active",
            "message": "",
            "note": note,
            "allies": [
                champ(s.champion_id) | {"lane": s.position or ev.lanes.get(s.champion_id, ""),
                                        "tentative": s.tentative, "is_me": s.is_me}
                for s in d.allies if s.champion_id
            ],
            "enemies": [champ(c) | {"lane": ev.lanes.get(c, "")} for c in d.enemies],
            "bans": [champ(c) for c in sorted(d.bans)],
            "recs": [
                champ(r.champion_id) | {
                    "score": round(r.score, 2), "base": round(r.base, 2),
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
        })

    # -- draft sources ----------------------------------------------------
    def on_session(self, session: dict | None, pickable: set[int] | None = None) -> None:
        with self.lock:
            if not session:
                self.active, self.last_draft = False, None
                self.status("Waiting for champ select...")
                return
            if not self.active:
                self.user_hidden = False  # new draft: bring the panel back
            self.active = True
            draft = parse_session(session)
            if self.rec.role_for(draft) is None:
                self.last_draft = None
                self.status("No assigned role in this queue - recommendations need a position (draft or ranked).")
                return
            self.last_draft = (draft, pickable)
            self.render()

    def run_live(self) -> None:
        pickable: set[int] = set()
        lockfile = self.rec.cfg.lockfile
        record: Path | None = self.args.record
        if record:
            record.parent.mkdir(parents=True, exist_ok=True)

        def handle(session: dict | None) -> None:
            nonlocal pickable
            if record and session:
                with record.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": time.time(), "session": session}) + "\n")
            if not session:
                pickable = set()
            elif not pickable:
                try:
                    client = LcuClient(read_lockfile(lockfile))
                    pickable = client.get_pickable_ids()
                    client.close()
                except Exception:
                    pass
            self.on_session(session, pickable)

        self.status("Waiting for the League client...")
        asyncio.run(watch_session(lambda: read_lockfile(lockfile), handle))

    def run_replay(self, path: Path) -> None:
        entries = [json.loads(l) for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        for e in entries:
            self.on_session(e["session"])
            time.sleep(self.args.delay)

    def run_simulate(self) -> None:
        ids = lambda csv: [c for n in csv.split(",") if n.strip() and (c := self.rec.champs.id_from_slug(n))]
        allies = [Slot(c, "", False, False) for c in ids(self.args.allies)]
        enemies = ids(self.args.enemies)
        draft = DraftState(
            my_position=self.args.role, allies=allies, enemies=enemies,
            bans=set(ids(self.args.bans)), picked={s.champion_id for s in allies} | set(enemies),
        )
        self.active = True
        self.last_draft = (draft, None)
        self.render()

    # -- window placement -------------------------------------------------
    def track(self) -> None:
        panel = 0
        shown, last = False, None
        while True:
            panel = panel or tracker.find_window(TITLE)
            client = tracker.find_client()
            placement, focused = None, False
            if client and not tracker.is_minimized(client):
                placement = tracker.place(tracker.window_rect(client), tracker.work_area(client))
                focused = self.always_show or tracker.foreground_root() in (client, panel)
            elif self.always_show:
                placement, focused = tracker.standalone(tracker.primary_work_area()), True
            visible = bool(panel and placement and focused and (self.active or self.always_show) and not self.user_hidden)
            if visible and (not shown or placement != last):
                if not last or (placement.mode, placement.scale) != (last.mode, last.scale):
                    self.js(f"setScale({placement.scale:.3f});setMode({json.dumps(placement.mode)})")
                if not shown:
                    self.window.show()  # via pywebview so WinForms/WebView2 know they're visible
                tracker.move(panel, placement)
                shown, last = True, placement
            elif not visible and shown:
                self.window.hide()
                shown = False
            time.sleep(0.25)

    def hotkey(self) -> None:
        if not ctypes.windll.user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("L")):
            log.warning("Ctrl+Shift+L is taken by another app; hotkey disabled")
            return
        msg = w.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.user_hidden = not self.user_hidden

    # -- startup ----------------------------------------------------------
    def boot(self) -> None:
        self.window.events.loaded.wait(15)
        for target in (self.track, self.hotkey):
            threading.Thread(target=target, daemon=True).start()
        try:
            self.status("Loading champion data...")
            self.rec = Recommender(load_config(self.args.config))
            self.rec.tier = self._saved_tier() or self.rec.tier
            self.rec.progress = self.progress
            if self.args.simulate:
                self.run_simulate()
            elif self.args.replay:
                self.run_replay(self.args.replay)
            else:
                self.run_live()
        except Exception:
            log.exception("fatal")
            self.status("Something went wrong (see app.log)", "error")

    @staticmethod
    def _saved_tier() -> str | None:
        try:
            tier = json.loads(SETTINGS.read_text(encoding="utf-8")).get("tier")
        except (OSError, ValueError):
            return None
        return tier if tier in TIERS else None

    def run(self) -> None:
        tracker.make_dpi_aware()
        self.window = webview.create_window(
            TITLE, url=str(RESOURCES / "panel" / "index.html"), js_api=Api(self),
            width=300, height=800, frameless=True, on_top=True, focus=False,
            resizable=False, hidden=True, background_color="#010a13",
        )
        self._install_quit_handler()
        webview.start(self.boot)
        os._exit(0)  # worker threads may be blocked in network/GetMessage calls

    def _install_quit_handler(self) -> None:
        """Ctrl+C / closing the terminal quits. Python's SIGINT handler can't run while the main
        thread sits in the native window loop, but Windows calls this on its own thread."""
        @HANDLER_ROUTINE
        def on_console_event(event: int) -> bool:
            if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
                self.window.destroy()
                return True
            return False

        self._quit_handler = on_console_event  # keep a reference so ctypes doesn't free it
        ctypes.windll.kernel32.SetConsoleCtrlHandler(on_console_event, True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--record", type=Path, help="append each live session update to this .jsonl file")
    ap.add_argument("--replay", type=Path, help="replay a recorded .jsonl draft")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--role", choices=LANES, help="simulate: your assigned role")
    ap.add_argument("--allies", default="")
    ap.add_argument("--enemies", default="")
    ap.add_argument("--bans", default="")
    args = ap.parse_args()
    if args.simulate and not args.role:
        ap.error("--simulate needs --role")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "app.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    App(args).run()


if __name__ == "__main__":
    main()
