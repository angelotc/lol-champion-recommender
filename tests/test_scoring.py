import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.lcu import DraftState, parse_session
from draft.scoring import ChampStats, Pair, assign_enemy_lanes, infer_enemy_lanes, recommend, shrink

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


def test_default_weights_by_role():
    from config import default_weights

    sup = default_weights("support")
    assert sup["enemy"]["support"] == sup["enemy"]["bottom"] == 1.0
    assert sup["ally"]["bottom"] == 1.0 and "support" not in sup["ally"]
    mid = default_weights("middle")
    assert mid["enemy"]["middle"] == 1.0 and mid["enemy"]["jungle"] == 0.5 and mid["enemy"]["top"] == 0.3
    assert max(mid["ally"].values()) == 0.5  # no duo partner; jungle highest
    jg = default_weights("jungle")
    assert jg["enemy"]["jungle"] == 1.0 and set(jg["ally"].values()) == {0.5}


def test_weights_override_merges():
    from config import Config

    cfg = Config(weights={"middle": {"enemy": {"jungle": 0.9}}})
    w = cfg.weights_for("middle")
    assert w["enemy"]["jungle"] == 0.9 and w["enemy"]["middle"] == 1.0 and w["base"] == 1.0


def test_default_weights_by_role():
    from config import default_weights

    sup = default_weights("support")
    assert sup["enemy"]["support"] == sup["enemy"]["bottom"] == 1.0
    assert sup["ally"]["bottom"] == 1.0 and "support" not in sup["ally"]
    mid = default_weights("middle")
    assert mid["enemy"]["middle"] == 1.0 and mid["enemy"]["jungle"] == 0.5 and mid["enemy"]["top"] == 0.3
    assert max(mid["ally"].values()) == 0.5  # no duo partner; jungle highest
    jg = default_weights("jungle")
    assert jg["enemy"]["jungle"] == 1.0 and set(jg["ally"].values()) == {0.5}


def test_weights_override_merges():
    from config import Config

    cfg = Config(weights={"middle": {"enemy": {"jungle": 0.9}}})
    w = cfg.weights_for("middle")
    assert w["enemy"]["jungle"] == 0.9 and w["enemy"]["middle"] == 1.0 and w["base"] == 1.0


def test_role_comes_from_assignment_only():
    from draft.engine import Recommender

    assert Recommender.role_for(DraftState(my_position="middle")) == "middle"
    assert Recommender.role_for(DraftState(my_position="")) is None


def test_parse_session_my_lock():
    session = json.loads((Path(__file__).parent / "fixtures" / "session.json").read_text())
    assert parse_session(session).my_locked == 0
    for group in session["actions"]:
        for a in group:
            if a["type"] == "pick" and a["actorCellId"] == session["localPlayerCellId"]:
                a.update(completed=True, championId=412)
    assert parse_session(session).my_locked == 412


def test_manual_enemy_lane_overrides_inference():
    # Hwei (1) is mostly mid; Ziggs (2) plays mid and bot. Saying Hwei is bot moves Ziggs to mid.
    rates = {1: {"middle": 66, "bottom": 24}, 2: {"middle": 50, "bottom": 45}, 3: {"support": 90}}
    assert assign_enemy_lanes([1, 2, 3], rates) == {"middle": 1, "bottom": 2, "support": 3}
    res = assign_enemy_lanes([1, 2, 3], rates, {1: "bottom"})
    assert res == {"bottom": 1, "middle": 2, "support": 3}


def test_manual_enemy_lane_ignores_stale_and_bad_entries():
    rates = {1: {"middle": 60}, 2: {"bottom": 60}}
    assert assign_enemy_lanes([1, 2], rates, {9: "middle", 2: "nowhere"}) == {"middle": 1, "bottom": 2}


def test_ban_threats_weigh_likelihood_and_harm():
    from draft.scoring import ban_threats
    w = {"enemy": {"support": 1.0, "bottom": 1.0, "top": 0.3}}
    leona = ChampStats(1, "support", 50.0, 10000, matchups={
        "support": {10: Pair(46.0, 2000, -4.0), 11: Pair(46.0, 2000, -4.0)},
        "bottom": {20: Pair(52.0, 2000, 2.0)},
    })
    rows = {
        "support": {10: {"pr": 10, "games": 5000, "avgWrDelta": 0}, 11: {"pr": 1, "games": 500, "avgWrDelta": 0}},
        "bottom": {20: {"pr": 10, "games": 5000, "avgWrDelta": 0}},
        "top": {30: {"pr": 10, "games": 5000, "avgWrDelta": 3.0}},
    }
    t = ban_threats([leona], rows, w, k=500)
    order = [x.champion_id for x in t]
    assert order[0] == 10            # beats your pick and is picked often
    assert order.index(11) > order.index(10)  # same harm, rarely picked
    assert t[-1].champion_id == 20 and t[-1].score < 0  # you beat it: not worth a ban
    assert ban_threats([leona], rows, w, k=500, exclude={10})[0].champion_id != 10


def test_draft_odds_sums_both_sides():
    from draft.scoring import draft_odds
    k = 500
    # our bot laner is strong and beats their bot laner (measured from both sides)
    ours = ChampStats(1, "bottom", 53.0, 10**6, avg_wr=50.0, matchups={"bottom": {2: Pair(55.0, 10**6, 2.0)}})
    theirs = ChampStats(2, "bottom", 50.0, 10**6, avg_wr=50.0, matchups={"bottom": {1: Pair(48.0, 10**6, -2.0)}})
    o = draft_odds({"bottom": (ours, False)}, {"bottom": (theirs, False)}, k)
    # strength 3 x 0.5 + matchup mean(2, 2) x 1.0
    assert o.win == pytest.approx(50 + 1.5 + 2.0, abs=0.01)
    assert {t.kind for t in o.terms} == {"strength", "matchup"}
    flipped = draft_odds({"bottom": (theirs, False)}, {"bottom": (ours, False)}, k)
    assert flipped.win == pytest.approx(100 - o.win, abs=0.01)


def test_draft_odds_hovers_count_half_and_clamp():
    from draft.scoring import draft_odds
    strong = ChampStats(1, "top", 70.0, 10**6, avg_wr=50.0)
    assert draft_odds({"top": (strong, True)}, {}, 500).win == pytest.approx(55.0, abs=0.01)
    assert draft_odds({"top": (strong, False)}, {}, 500, {"strength": 5.0}).win == 75.0
    assert draft_odds({}, {}, 500).win == 50.0


def test_draft_odds_calibration_replaces_the_clamp():
    from draft.scoring import draft_odds, sigmoid
    strong = ChampStats(1, "top", 70.0, 10**6, avg_wr=50.0)
    w = {"cal_a": 0.1, "cal_b": -0.2}
    assert draft_odds({"top": (strong, False)}, {}, 500, w).win == pytest.approx(100 * sigmoid(0.8), abs=0.05)
    assert draft_odds({}, {}, 500, w).win == pytest.approx(100 * sigmoid(-0.2))  # empty draft ~ its own prior


def test_ban_threats_count_teammate_hovers():
    from draft.scoring import ban_threats
    w = {"enemy": {"support": 1.0, "top": 0.3}}
    leona = ChampStats(1, "support", 50.0, 10000, matchups={"support": {10: Pair(50.0, 2000, 0.0)}})
    # your teammate hovers a top laner that champion 30 counters hard
    darius = ChampStats(2, "top", 50.0, 10000, matchups={"top": {30: Pair(44.0, 3000, -6.0)}})
    top_w = {"enemy": {"top": 1.0}}
    rows = {
        "support": {10: {"pr": 10, "games": 5000, "avgWrDelta": 0.5}},
        "top": {30: {"pr": 10, "games": 5000, "avgWrDelta": 0}},
    }
    assert ban_threats([leona], rows, w, k=500)[0].champion_id == 10
    t = ban_threats([leona], rows, w, k=500, allies=[(darius, top_w)])
    assert t[0].champion_id == 30
    assert [c.champion_id for c in t[0].against] == [2]
