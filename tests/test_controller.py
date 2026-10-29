import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from draft.controller import Controller
from data.store import Store
from draft.scoring import DraftOdds, OddsTerm
from draft.state import Event


def controller(tmp_path, pushed):
    return Controller(Config(), lambda s: pushed.append(s), Store(tmp_path / "store.sqlite"))


def test_odds_timeline_recorded_on_each_draft_change(tmp_path):
    pushed = []
    ctl = controller(tmp_path, pushed)
    did = ctl.store.start_draft("support", "master_plus")
    ctl.draft_id = did
    ctl.last_odds = DraftOdds(57.0, [OddsTerm("matchup", "us", (1, 2), ("support", "bottom"), 3.0)], 8, 1)
    ctl.handle([Event.DRAFT_CHANGED])
    ctl.last_odds = DraftOdds(61.0, [OddsTerm("strength", "us", (1,), ("support",), 5.0)], 10)
    ctl.handle([Event.DRAFT_CHANGED])
    rows = ctl.store.db.execute(
        "SELECT payload FROM recommendations WHERE draft_id=? AND kind='odds' ORDER BY t", (did,)
    ).fetchall()
    import json
    first, last = json.loads(rows[0][0]), json.loads(rows[1][0])
    assert first["odds"] == 57.0 and first["terms"][0]["value"] == 3.0 and first["pending"] == 1
    assert last["odds"] == 61.0 and last["known"] == 10


def test_no_odds_row_without_an_estimate_or_draft(tmp_path):
    pushed = []
    ctl = controller(tmp_path, pushed)
    ctl.last_odds = DraftOdds(57.0, [], 8)  # odds but no draft yet
    ctl.handle([Event.DRAFT_CHANGED])
    ctl.draft_id = ctl.store.start_draft("support", "master_plus")
    ctl.last_odds = None  # draft but no odds (e.g. ban phase view)
    ctl.handle([Event.DRAFT_CHANGED])
    assert ctl.store.db.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 0
