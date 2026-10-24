"""Desktop panel: docks beside the League client from lobby through champ select.

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
from controller import Controller
from data.store import Store
from lcu import DraftState, LcuClient, Slot, read_lockfile, watch_session
from paths import DATA_DIR, RESOURCES
from scoring import LANES
from state import DRAFT_STAGES, PRE_DRAFT_STAGES

TITLE = "League Pick Helper"
VISIBLE_STAGES = DRAFT_STAGES | PRE_DRAFT_STAGES  # never in game, loading or post-game
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x0312
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT = 0, 1, 2
HANDLER_ROUTINE = ctypes.WINFUNCTYPE(w.BOOL, w.DWORD)

log = logging.getLogger("app")


class Api:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self, app: "App"):
        self._app = app

    def hide(self) -> None:
        self._app.ctl.user_hidden = True

    def set_tier(self, tier: str) -> None:
        self._app.ctl.set_tier(tier)

    def set_enemy_lane(self, champion_id: int, lane: str) -> None:
        self._app.ctl.set_enemy_lane(int(champion_id), lane)

    def set_tab(self, tab: str) -> None:
        self._app.ctl.set_tab(tab)


class App:
    def __init__(self, args):
        self.args = args
        self.always_show = bool(args.replay or args.simulate)
        self.window: webview.Window | None = None
        live = not self.always_show
        self.ctl = Controller(load_config(args.config), self.push, Store() if live else None)

    # -- page updates -----------------------------------------------------
    def js(self, code: str) -> None:
        if self.window:
            self.window.evaluate_js(code)

    def push(self, state: dict) -> None:
        self.js(f"window.updateState&&updateState({json.dumps(state)})")

    # -- draft sources ----------------------------------------------------
    def run_live(self) -> None:
        ctl = self.ctl
        pickable: set[int] = set()
        lockfile = ctl.cfg.lockfile
        record: Path | None = self.args.record
        if record:
            record.parent.mkdir(parents=True, exist_ok=True)

        def on_session(session: dict | None) -> None:
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
            ctl.on_session(session, pickable)

        def lobby_roles() -> list[str]:
            client = LcuClient(read_lockfile(lockfile))
            try:
                return client.get_lobby_positions()
            finally:
                client.close()

        ctl.lobby_roles = lobby_roles

        def on_phase(phase: str) -> None:
            ctl.idle_message = "Waiting for champ select..."  # connected (the panel is hidden when idle)
            ctl.on_phase(phase)

        ctl.render()
        asyncio.run(watch_session(lambda: read_lockfile(lockfile), on_session, on_phase=on_phase))

    def run_replay(self, path: Path) -> None:
        entries = [json.loads(l) for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        for e in entries:
            self.ctl.on_session(e["session"])
            time.sleep(self.args.delay)

    def run_simulate(self) -> None:
        ctl = self.ctl
        ids = lambda csv: [c for n in csv.split(",") if n.strip() and (c := ctl.rec.champs.id_from_slug(n))]
        allies = [Slot(c, "", False, False) for c in ids(self.args.allies)]
        enemies = ids(self.args.enemies)
        draft = DraftState(
            my_position=self.args.role, allies=allies, enemies=enemies,
            bans=set(ids(self.args.bans)), picked={s.champion_id for s in allies} | set(enemies),
        )
        with ctl.lock:
            ctl.session.draft = draft
            ctl.render()

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
            wanted = self.always_show or self.ctl.session.stage in VISIBLE_STAGES
            visible = bool(
                panel and placement and focused and wanted and not self.ctl.user_hidden
                and (self.always_show or not tracker.game_running())  # never over or beside the game
            )
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
                self.ctl.user_hidden = not self.ctl.user_hidden

    # -- startup ----------------------------------------------------------
    def boot(self) -> None:
        self.window.events.loaded.wait(15)
        for target in (self.track, self.hotkey):
            threading.Thread(target=target, daemon=True).start()
        try:
            self.ctl.boot()
            if self.args.simulate:
                self.run_simulate()
            elif self.args.replay:
                self.run_replay(self.args.replay)
            else:
                self.run_live()
        except Exception:
            log.exception("fatal")
            self.ctl.fail("Something went wrong (see app.log)")

    def run(self) -> None:
        tracker.make_dpi_aware()
        self.window = webview.create_window(
            TITLE, url=str(RESOURCES / "panel" / "index.html"), js_api=Api(self),
            width=300, height=800, frameless=True, on_top=True, focus=False,
            resizable=False, hidden=True, background_color="#010a13",
            easy_drag=False,  # position is owned by the tracker; pywebview's drag also crashes here
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
