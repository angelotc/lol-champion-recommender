import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lcu import parse_session
from scoring import ChampStats, Pair, infer_enemy_lanes, recommend, shrink

W = {"base": 1.0, "enemy": {"support": 1.0, "bottom": 1.0}, "ally": {"bottom": 1.0}}


def test_shrink():
    assert shrink(10, 500, 500) == pytest.approx(5)
    assert shrink(10, 0, 500) == 0
    assert shrink(10, 10**9, 500) == pytest.approx(10, rel=1e-3)


def stats(cid, wr=50.0, games=10000, **kw):
    return ChampStats(cid, "support", wr, games, **kw)


def test_ordering_and_missing_data():
    a = stats(1, wr=52)
    b = stats(2, wr=50, matchups={"support": {99: Pair(55, 5000)}})
    c = stats(3, wr=51)
    recs = recommend([a, b, c], {}, {"support": 99}, W, k=500)
    assert [r.champion_id for r in recs] == [2, 1, 3]
    assert recs[1].contributions == []


def test_shrinkage_prefers_large_sample():
    tiny = stats(1, matchups={"support": {9: Pair(80, 5)}})
    big = stats(2, matchups={"support": {9: Pair(56, 5000)}})
    recs = recommend([tiny, big], {}, {"support": 9}, W, k=500)
    assert recs[0].champion_id == 2


def test_tentative_factor():
    s = stats(1, synergies={"bottom": {7: Pair(54, 2000)}})
    firm = recommend([s], {"bottom": (7, False)}, {}, W, k=500)[0]
    tent = recommend([s], {"bottom": (7, True)}, {}, W, k=500, tentative_factor=0.5)[0]
    assert tent.score - tent.base == pytest.approx((firm.score - firm.base) * 0.5)
    assert tent.contributions[0].tentative


def test_infer_enemy_lanes():
    rates = {
        1: {"top": 0.9, "middle": 0.1},
        2: {"top": 0.8, "jungle": 0.9},
        3: {"bottom": 0.95},
        4: {"support": 0.9, "bottom": 0.3},
    }
    res = infer_enemy_lanes([1, 2, 3, 4, 5], rates)
    assert res["top"] == 1 and res["jungle"] == 2 and res["bottom"] == 3 and res["support"] == 4
    assert res["middle"] == 5
    assert len(set(res.values())) == 5


def test_infer_unknown_gets_leftover():
    res = infer_enemy_lanes([1, 2], {1: {"top": 1.0}})
    assert res["top"] == 1 and sorted(res.values()) == [1, 2]


def test_parse_session():
    session = json.loads((Path(__file__).parent / "fixtures" / "session.json").read_text())
    d = parse_session(session)
    assert d.my_cell_id == 2
    assert d.my_position == "support"
    by_pos = {s.position: s for s in d.allies}
    assert by_pos["bottom"].champion_id == 222 and not by_pos["bottom"].tentative
    assert by_pos["jungle"].champion_id == 64 and by_pos["jungle"].tentative
    assert [s for s in d.allies if s.is_me][0].position == "support"
    assert sorted(d.enemies) == [51, 111]
    assert d.bans == {238, 777, 157, 103}
    assert {222, 111} <= d.picked


def test_parse_session_defensive():
    d = parse_session({})
    assert d.allies == [] and d.bans == set() and d.my_position == ""
