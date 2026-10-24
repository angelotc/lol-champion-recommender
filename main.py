"""Champ select pick recommender.

  uv run main.py                         live: follow champ select in the League client
  uv run main.py --record drafts/x.jsonl live, and save every session update
  uv run main.py --replay drafts/x.jsonl replay a recorded draft offline
  uv run main.py --simulate --allies jinx,leesin --enemies nautilus,caitlyn --bans pyke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live

from config import load_config
from engine import Recommender
from lcu import DraftState, LcuClient, LcuError, Slot, parse_session, read_lockfile, watch_session
from ui import render

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # cp1252 consoles can't print rich spinners
console = Console()


def show_eval(rec: Recommender, ev):
    return render(ev.draft, ev.recs, rec.champs, rec.cfg.top_n)


def run_live(rec: Recommender, record: Path | None) -> None:
    creds = read_lockfile(rec.cfg.lockfile)
    client = LcuClient(creds)
    pickable: set[int] = set()
    if record:
        record.parent.mkdir(parents=True, exist_ok=True)

    with Live(console=console, auto_refresh=False) as live:
        def show(session: dict | None) -> None:
            nonlocal pickable
            if record and session:
                with record.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": time.time(), "session": session}) + "\n")
            if not session:
                pickable = set()
                live.update("[dim]Waiting for champ select...", refresh=True)
                return
            if not pickable:
                try:
                    pickable = client.get_pickable_ids()
                except Exception:
                    pass
            live.update(show_eval(rec, rec.evaluate(parse_session(session), pickable)), refresh=True)

        asyncio.run(watch_session(lambda: read_lockfile(rec.cfg.lockfile), show))


def run_replay(rec: Recommender, path: Path, delay: float) -> None:
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    with Live(console=console, auto_refresh=False) as live:
        for i, entry in enumerate(lines, 1):
            view = show_eval(rec, rec.evaluate(parse_session(entry["session"])))
            live.update(view, refresh=True)
            if i < len(lines):
                time.sleep(delay)


def _ids(rec: Recommender, csv: str) -> list[int]:
    out = []
    for name in filter(None, (n.strip() for n in csv.split(","))):
        cid = rec.champs.id_from_slug(name)
        if cid is None:
            raise SystemExit(f"unknown champion: {name}")
        out.append(cid)
    return out


def run_simulate(rec: Recommender, args) -> None:
    allies = [Slot(c, "", False, False) for c in _ids(rec, args.allies)]
    enemies = _ids(rec, args.enemies)
    draft = DraftState(
        my_position=rec.cfg.role,
        allies=allies,
        enemies=enemies,
        bans=set(_ids(rec, args.bans)),
        picked={s.champion_id for s in allies} | set(enemies),
    )
    console.print(show_eval(rec, rec.evaluate(draft)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--record", type=Path, help="append each live session update to this .jsonl file")
    ap.add_argument("--replay", type=Path, help="replay a recorded .jsonl draft")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between replay steps")
    ap.add_argument("--simulate", action="store_true", help="score a hand-entered draft (no client)")
    ap.add_argument("--allies", default="")
    ap.add_argument("--enemies", default="")
    ap.add_argument("--bans", default="")
    args = ap.parse_args()

    rec = Recommender(load_config(args.config))
    with console.status("Loading lolalytics stats...") as status:
        for slug in rec.prefetch(status.update):
            console.print(f"[yellow]no lolalytics data for '{slug}' in {rec.cfg.role}; skipped")
    try:
        if args.simulate:
            run_simulate(rec, args)
        elif args.replay:
            run_replay(rec, args.replay, args.delay)
        else:
            run_live(rec, args.record)
    except LcuError as e:
        console.print(f"[red]{e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
