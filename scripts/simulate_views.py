"""Render every panel view from a simulated draft, through the real controller and lolalytics data.

  uv run scripts/simulate_views.py              writes docs/views/{bans,picks,build,ingame}.png
  uv run scripts/simulate_views.py --out DIR

No League client needed: the draft, lock-in and in-game state are simulated.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from config import load_config  # noqa: E402
from controller import Controller  # noqa: E402
from data.live import LiveGame  # noqa: E402
from lcu import DraftState, Slot  # noqa: E402
from render_panel import render  # noqa: E402
from state import Stage  # noqa: E402

ROLE = "support"
ALLIES = [("ksante", "top", True), ("leesin", "jungle", False), ("ahri", "middle", False), ("jinx", "bottom", False)]
ENEMIES = ["caitlyn", "nautilus", "hwei", "viego"]
BANS = ["pyke", "blitzcrank", "yunara", "zed", "aatrox"]
LOCK = "leona"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "views")
    out_dir = ap.parse_args().out
    out_dir.mkdir(parents=True, exist_ok=True)
    states: list[dict] = []
    ctl = Controller(load_config(), states.append)
    ctl.cfg.show_ban_suggestions = True  # render the optional bans view too
    ctl.boot()
    ids = lambda *names: [ctl.rec.champs.id_from_slug(n) for n in names]
    shots: dict[str, tuple[dict, list[str], int, str]] = {}

    # ban phase
    allies = [Slot(c, lane, tent, False) for c, (_, lane, tent) in zip(ids(*(a[0] for a in ALLIES)), ALLIES)]
    me = Slot(0, ROLE, False, True)
    draft = DraftState(my_position=ROLE, allies=allies[:2] + [me], bans=set(ids(*BANS[:3])))
    ctl.session.phase, ctl.session.draft, ctl.session.banning = "ChampSelect", draft, True
    ctl.render()
    shots["bans"] = (states[-1], [".threat"], 900, "")

    # picks, with the draft win estimate (load the 10 champions' stats first, as the app does in the background)
    draft = DraftState(my_position=ROLE, allies=allies + [me], enemies=ids(*ENEMIES), bans=set(ids(*BANS)),
                       picked=set(ids(*(a[0] for a in ALLIES if not a[2]))) | set(ids(*ENEMIES)))
    ctl.session.draft, ctl.session.banning, ctl.tab = draft, False, None
    ctl.render()
    ev = ctl.last_eval
    ours, theirs = ctl.rec.odds_inputs(ev)
    for lane, (cid, _) in list(ours.items()) + list(theirs.items()):
        ctl.rec.lane_stats(cid, lane)
    ctl.render()
    shots["picks"] = (states[-1], [".rec"], 1000, "")

    # locked in: build view with matchup deltas
    leona = ids(LOCK)[0]
    locked = replace(draft, allies=allies + [Slot(leona, ROLE, False, True)], my_locked=leona, picked=draft.picked | {leona})
    ctl.session.draft, ctl.session.frozen = locked, replace(locked, picked=locked.picked - {leona})
    assert ctl.session.stage == Stage.LOCKED
    ctl.render()
    shots["build"] = (states[-1], [], 1600, "")

    # in game (Ctrl+X): Leona at level 9 with support item and boots
    ctl.session.draft = ctl.session.frozen = None
    ctl.session.phase, ctl.ingame_shown = "InProgress", True
    enemies = dict(zip(["bottom", "support", "middle", "jungle"], ["Caitlyn", "Nautilus", "Hwei", "Viego"]))
    ctl.live = LiveGame("Leona", ROLE, 9, 1320, [3869, 3047, 2055], 1095, {}, enemies)
    ctl.render()
    shots["ingame"] = (states[-1], [], 806, "ingame")

    for name, (state, clicks, height, mode) in shots.items():
        width = 333 if mode == "ingame" else 300
        scale = 1.333 if mode == "ingame" else 1.0
        path = render(out_dir / f"{name}.png", state, None, clicks, width, height, scale, mode)
        print(name, state.get("view"), path)


if __name__ == "__main__":
    main()
