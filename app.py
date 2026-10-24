"""Desktop panel: docks beside the League client during champ select.

  uv run app.py                          live
  uv run app.py --replay drafts/x.jsonl  replay a recorded draft (panel always shown)
  uv run app.py --simulate --allies jinx --enemies nautilus,caitlyn

Ctrl+Shift+L toggles the panel.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
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

TITLE = "League Pick Helper"
TIER_NAMES = {"master_plus": "Master+", "emerald_plus": "Emerald+", "platinum_plus": "Platinum+"}
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x0312

log = logging.getLogger("app")


class Api:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self, app: "App"):
        self._app = app

    def hide(self) -> None:
        self._app.user_hidden = True


class App:
    def __init__(self, args):
        self.args = args
        self.always_show = bool(args.replay or args.simulate)
        self.active = False  # in champ select
        self.user_hidden = False
        self.window: webview.Window | None = None
        self.rec: Recommender | None = None

    # -- page updates -----------------------------------------------------
    def js(self, code: str) -> None:
        if self.window:
            self.window.evaluate_js(code)

    def push(self, state: dict) -> None:
        self.js(f"window.updateState&&updateState({json.dumps(state)})")

    def status(self, message: str, status: str = "waiting") -> None:
        self.push(self._header() | {"status": status, "message": message})

    def _header(self) -> dict:
        cfg = self.rec.cfg if self.rec else load_config()
        return {"role": cfg.role, "tier": TIER_NAMES.get(cfg.tier, cfg.tier), "days": cfg.days}

    def show_eval(self, ev: Evaluation) -> None:
        ch = self.rec.champs
        champ = lambda cid: {"name": ch.name(cid), "icon": ch.icon_url(cid)}
        d = ev.draft
        self.push(self._header() | {
            "status": "active",
            "message": "",
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
        if not session:
            self.active = False
            self.status("Waiting for champ select...")
            return
        if not self.active:
            self.user_hidden = False  # new draft: bring the panel back
        self.active = True
        try:
            self.show_eval(self.rec.evaluate(parse_session(session), pickable))
        except Exception:
            log.exception("evaluate failed")
            self.status("Error scoring draft (see app.log)", "error")

    def run_live(self) -> None:
        pickable: set[int] = set()
        lockfile = self.rec.cfg.lockfile

        def handle(session: dict | None) -> None:
            nonlocal pickable
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
        entries = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for e in entries:
            self.on_session(e["session"])
            time.sleep(self.args.delay)

    def run_simulate(self) -> None:
        ids = lambda csv: [c for n in csv.split(",") if n.strip() and (c := self.rec.champs.id_from_slug(n))]
        allies = [Slot(c, "", False, False) for c in ids(self.args.allies)]
        enemies = ids(self.args.enemies)
        draft = DraftState(
            my_position=self.rec.cfg.role, allies=allies, enemies=enemies,
            bans=set(ids(self.args.bans)), picked={s.champion_id for s in allies} | set(enemies),
        )
        self.active = True
        self.show_eval(self.rec.evaluate(draft))

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
            missing = self.rec.prefetch(lambda m: self.status(m))
            if missing:
                log.warning("no lolalytics data for %s", missing)
            if self.args.simulate:
                self.run_simulate()
            elif self.args.replay:
                self.run_replay(self.args.replay)
            else:
                self.run_live()
        except Exception:
            log.exception("fatal")
            self.status("Something went wrong (see app.log)", "error")

    def run(self) -> None:
        tracker.make_dpi_aware()
        self.window = webview.create_window(
            TITLE, url=str(RESOURCES / "panel" / "index.html"), js_api=Api(self),
            width=300, height=800, frameless=True, on_top=True, focus=False,
            resizable=False, hidden=True, background_color="#010a13",
        )
        webview.start(self.boot)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--replay", type=Path)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--allies", default="")
    ap.add_argument("--enemies", default="")
    ap.add_argument("--bans", default="")
    args = ap.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "app.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    App(args).run()


if __name__ == "__main__":
    main()
