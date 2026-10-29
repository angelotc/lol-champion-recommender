"""Desktop panel: docks beside the League client from lobby through champ select.

  uv run app.py                          live
  uv run app.py --record drafts/x.jsonl  live, and save every session update for replay
  uv run app.py --replay drafts/x.jsonl  replay a recorded draft (panel always shown)
  uv run app.py --simulate --role middle --allies jinx --enemies syndra,leesin

Ctrl+X (with League in front) toggles the panel; in game it toggles the in-game build panel.
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

import settings_ui
import tracker
import updater
from config import load_config
from controller import Controller
from data.store import Store
from lcu import DraftState, LcuClient, Slot, read_lockfile, watch_session
from paths import DATA_DIR, RESOURCES, config_path
from scoring import LANES
from state import DRAFT_STAGES, PRE_DRAFT_STAGES, Stage
from tray import Tray

TITLE = "LoL Companion"
SETTINGS_TITLE = "LoL Companion — Settings"
VISIBLE_STAGES = DRAFT_STAGES | PRE_DRAFT_STAGES  # never in game, loading or post-game
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x0312
HOTKEYS = {1: "L"}  # Ctrl+Shift+L panel, from anywhere (fallback; Ctrl+X is polled, see toggle_keys)
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
TOGGLE_KEY = "X"  # Ctrl+X while the client, the panel or the game is in front
KEY_POLL = 1 / 30  # seconds; in-game key check rate
UPDATE_EVERY = 20 * 60  # seconds between update checks
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT = 0, 1, 2
# one live copy at a time: a second launch signals SHOW_EVENT and exits
INSTANCE_MUTEX, SHOW_EVENT = "Local\\LoLCompanion", "Local\\LoLCompanion.Show"
ERROR_ALREADY_EXISTS, EVENT_MODIFY_STATE = 183, 0x0002
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

    def open_settings(self) -> None:
        self._app.open_settings()


class App:
    def __init__(self, args):
        self.args = args
        self._settings_lock = threading.Lock()
        self.always_show = bool(args.replay or args.simulate)
        self.window: webview.Window | None = None
        live = not self.always_show
        self.ctl = Controller(load_config(args.config), self.push, Store() if live else None)
        self.updater = updater.Updater(on_change=lambda: self.tray.on_update())
        self.settings_window: webview.Window | None = None
        self.tray = Tray(self.toggle_panel, self.quit, self.updater, self.restart, self.open_settings)

    def toggle_panel(self) -> None:
        self.ctl.user_hidden = not self.ctl.user_hidden

    def quit(self) -> None:
        self.tray.stop()  # before the window closes, so no ghost icon is left behind
        self.updater.apply_on_exit()
        self.window.destroy()  # webview.start returns and run() exits

    def restart(self) -> None:
        if self.updater.state != "ready":
            return
        self.tray.stop()  # the new version adds its own icon
        self.updater.apply_and_restart()
        if self.updater.state == "error":  # still running: the update failed, bring the tray back
            self.tray = Tray(self.toggle_panel, self.quit, self.updater, self.restart, self.open_settings)
            self.tray.start()

    def open_settings(self) -> None:
        """The settings window: one at a time, brought to front if already open."""
        with self._settings_lock:
            win = self.settings_window
            if win:
                try:
                    win.restore()
                    win.show()
                    win.on_top = True  # raise above other windows, then back to normal
                    win.on_top = False
                    return
                except Exception:
                    log.exception("raise settings")  # window is gone: make a new one
            api = settings_ui.SettingsApi(Path(self.args.config) if self.args.config else config_path(),
                                          self.ctl.reload_config, self.version_text)
            win = webview.create_window(
                SETTINGS_TITLE, url=str(RESOURCES / "panel" / "settings.html"), js_api=api,
                width=480, height=680, min_size=(420, 480), resizable=True, background_color="#010a13",
            )
            api.window = win
            win.events.closed += lambda: setattr(self, "settings_window", None)
            self.settings_window = win

    def version_text(self) -> str:
        """For the settings window: "Version 0.1.4", plus a downloaded update if there is one."""
        u = self.updater
        text = f"Version {u.current_version()}"
        if u.state == "ready" and u.version:
            text += f" · {u.version} ready (restart to update)"
        return text

    def wait_show_requests(self) -> None:
        """Another copy was started: show this copy's panel instead."""
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = w.HANDLE
        event = k32.CreateEventW(None, False, False, SHOW_EVENT)  # auto-reset
        while k32.WaitForSingleObject(event, 0xFFFFFFFF) == 0:
            log.info("second copy started: showing panel")
            self.ctl.user_hidden = False

    def check_updates(self) -> None:
        while True:
            self.updater.check_async()
            time.sleep(UPDATE_EVERY)

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

        ctl.lcu = lambda: LcuClient(read_lockfile(lockfile))

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
    def _target(self, panel: int) -> tuple[tracker.Placement | None, bool]:
        """Where the panel should be and whether it may show there."""
        if not self.always_show and self.ctl.session.stage == Stage.INGAME:
            game = tracker.find_game()  # in game: only after Ctrl+X, over the game's right edge
            if not (game and self.ctl.ingame_shown) or tracker.is_minimized(game):
                return None, False
            return tracker.place_ingame(tracker.window_rect(game), self.ctl.cfg.ingame_scale), tracker.foreground_root() in (game, panel)
        if self.ctl.user_hidden or not (self.always_show or self.ctl.session.stage in VISIBLE_STAGES):
            return None, False
        if not self.always_show and tracker.game_running():
            return None, False  # never beside the game
        client = tracker.find_client()
        if client and not tracker.is_minimized(client):
            placement = tracker.place(tracker.window_rect(client), tracker.work_area(client))
            return placement, self.always_show or tracker.foreground_root() in (client, panel)
        if self.always_show:
            return tracker.standalone(tracker.primary_work_area()), True
        return None, False

    def track(self) -> None:
        """Keep the panel placed and shown/hidden; never dies (errors are logged, loop continues)."""
        state = {"panel": 0, "shown": False, "last": None, "through": False}
        while True:
            try:
                self._track_once(state)
            except Exception:
                log.exception("track")
                time.sleep(1.0)
            time.sleep(0.25)

    def _track_once(self, st: dict) -> None:
        panel, shown, last, through = st["panel"], st["shown"], st["last"], st["through"]
        panel = panel or tracker.find_window(TITLE)
        placement, focused = self._target(panel) if panel else (None, False)
        visible = bool(placement and focused)
        ingame = bool(placement and placement.mode == "ingame")
        if panel and through != ingame:  # in game, clicks go to the game, never to the panel
            tracker.set_click_through(panel, ingame)
            through = ingame
        if visible and (not shown or placement != last):
            if not last or (placement.mode, placement.scale) != (last.mode, last.scale):
                self.js(f"setScale({placement.scale / tracker.dpi_scale(panel):.3f});setMode({json.dumps(placement.mode)})")
            if not shown:
                log.info("panel shown (%s)", placement.mode)
                self.window.show()  # via pywebview so WinForms/WebView2 know they're visible
            tracker.move(panel, placement)
            shown, last = True, placement
        elif not visible and shown:
            log.info("panel hidden")
            self.window.hide()
            shown = False
        st.update(panel=panel, shown=shown, last=last, through=through)

    def hotkey(self) -> None:
        for hid, key in HOTKEYS.items():
            if not ctypes.windll.user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord(key)):
                log.warning("Ctrl+Shift+%s is taken by another app (another copy running?); that hotkey is disabled", key)
            else:
                log.info("hotkey Ctrl+Shift+%s registered", key)
        msg = w.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != WM_HOTKEY:
                continue
            log.info("hotkey Ctrl+Shift+%s (stage %s)", HOTKEYS.get(msg.wParam, "?"), self.ctl.session.stage.value)
            if msg.wParam == 1:
                self.ctl.user_hidden = not self.ctl.user_hidden

    def toggle_keys(self) -> None:
        """Ctrl+X while League is in front: in game it toggles the in-game panel, otherwise the
        panel. The game reads the keyboard in a mode that turns off other programs' hotkeys, so
        RegisterHotKey never fires there; poll the key state instead. Polling doesn't consume the
        key, and it's only checked while the client, the panel or the game is the foreground
        window, so Ctrl+X stays cut everywhere else."""
        state = ctypes.windll.user32.GetAsyncKeyState
        down = lambda vk: bool(state(vk) & 0x8000)
        was = False
        while True:
            time.sleep(KEY_POLL)
            try:
                game, front = tracker.find_game(), tracker.foreground_root()
                if not front or front not in (game, tracker.find_client(), tracker.find_window(TITLE)):
                    was = False
                    continue
                now = down(VK_CONTROL) and not down(VK_SHIFT) and not down(VK_MENU) and down(ord(TOGGLE_KEY))
                if now and not was:
                    log.info("key Ctrl+%s (stage %s)", TOGGLE_KEY, self.ctl.session.stage.value)
                    if game:
                        threading.Thread(target=self.ctl.toggle_ingame, daemon=True).start()
                    else:
                        self.ctl.user_hidden = not self.ctl.user_hidden
                was = now
            except Exception:
                log.exception("in-game keys")
                time.sleep(1.0)

    # -- startup ----------------------------------------------------------
    def boot(self) -> None:
        self.window.events.loaded.wait(15)
        for target in (self.track, self.hotkey, self.toggle_keys, self.ctl.live_loop):
            threading.Thread(target=target, daemon=True).start()
        self.tray.start()
        if not self.always_show:
            threading.Thread(target=self.check_updates, daemon=True).start()
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
        # persistent storage so the settings page can remember things (e.g. the comfort weight)
        webview.start(self.boot, private_mode=False, storage_path=str(DATA_DIR / "webview"))
        os._exit(0)  # worker threads may be blocked in network/GetMessage calls

    def _install_quit_handler(self) -> None:
        """Ctrl+C / closing the terminal quits. Python's SIGINT handler can't run while the main
        thread sits in the native window loop, but Windows calls this on its own thread."""
        @HANDLER_ROUTINE
        def on_console_event(event: int) -> bool:
            if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
                self.tray.stop()
                self.window.destroy()
                return True
            return False

        self._quit_handler = on_console_event  # keep a reference so ctypes doesn't free it
        ctypes.windll.kernel32.SetConsoleCtrlHandler(on_console_event, True)


def main() -> None:
    updater.startup()  # Velopack hooks first; may exit the process
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
    live = not (args.replay or args.simulate)
    if live and not claim_single_instance():
        log.info("already running: showed the other copy's panel")
        return
    app = App(args)
    if live:
        threading.Thread(target=app.wait_show_requests, daemon=True).start()
    app.run()


def claim_single_instance() -> bool:
    """True if this is the only live copy. Otherwise ask the running copy to show its panel."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = k32.CreateEventW.restype = k32.OpenEventW.restype = w.HANDLE
    global _instance_mutex  # held until the process exits
    _instance_mutex = k32.CreateMutexW(None, False, INSTANCE_MUTEX)
    if k32.GetLastError() != ERROR_ALREADY_EXISTS:
        return True
    event = k32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT)
    if event:
        k32.SetEvent(event)
        k32.CloseHandle(event)
    return False


if __name__ == "__main__":
    main()
