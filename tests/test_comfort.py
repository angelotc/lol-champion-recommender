import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from data import lcu_data
from data.store import Store
from scoring import ChampStats, Comfort, comfort_terms, recommend

W = {"weight": 1.0, "k": 20, "history": 0.5, "mastery": 0.15, "mastery_cap": 1.0}


def test_comfort_shrinks_small_personal_samples():
    s = ChampStats(1, "support", 50.0, 10000)
    two = {c.label: c for c in comfort_terms(s, Comfort(2, 2), W)}["you"]
    forty = {c.label: c for c in comfort_terms(s, Comfort(40, 30), W)}["you"]
    assert two.delta == forty.delta + 25  # 100% vs 75%
    assert two.weighted < forty.weighted  # 2 wins in 2 games count for less than 30 in 40


def test_mastery_bonus_is_capped_and_optional():
    s = ChampStats(1, "support", 50.0, 10000)
    (m,) = comfort_terms(s, Comfort(mastery=10**9), W)
    assert m.label == "mastery" and m.weighted == pytest.approx(1.0)
    assert comfort_terms(s, Comfort(mastery=10**9), W | {"weight": 0}) == []
    assert comfort_terms(s, None, W) == []


def test_comfort_can_reorder_close_picks():
    a, b = ChampStats(1, "support", 52.0, 10000, avg_wr=50), ChampStats(2, "support", 51.8, 10000, avg_wr=50)
    w = {"base": 1.0, "comfort": W}
    assert [r.champion_id for r in recommend([a, b], {}, {}, w, 500)] == [1, 2]
    order = [r.champion_id for r in recommend([a, b], {}, {}, w, 500, comfort={2: Comfort(30, 20, 200000)})]
    assert order == [2, 1]


def test_config_comfort_override():
    cfg = Config(comfort={"weight": 0.5}, weights={"support": {"comfort": {"k": 50}}})
    w = cfg.weights_for("support")["comfort"]
    assert w["weight"] == 0.5 and w["k"] == 50 and w["mastery"] == W["mastery"]


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, games):
        self.games, self.calls = games, []

    def get(self, path):
        self.calls.append(path)
        if path.startswith("/lol-match-history/v1/products"):
            return FakeResponse({"games": {"games": self.games}})
        if path.startswith("/lol-match-history/v1/games/"):
            return FakeResponse({"full": True})
        return FakeResponse(None, 404)


def game(gid, champ, win, queue=420, created=None, lane="BOTTOM", role="SUPPORT"):
    return {"gameId": gid, "queueId": queue, "gameCreation": (created or time.time()) * 1000, "gameDuration": 1500,
            "participants": [{"participantId": 3, "championId": champ, "timeline": {"lane": lane, "role": role},
                              "stats": {"win": win}}]}


def test_sync_history_stores_new_ranked_games_and_links_drafts(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    now = time.time()
    did = st.start_draft("support", "master_plus")
    st.update_draft(did, my_champ=89, ended=now - 30)
    client = FakeClient([game(1, 89, True, created=now), game(2, 412, False, lane="NONE"), game(3, 89, True, queue=450)])
    assert lcu_data.sync_history(client, st) == 2  # ARAM (450) skipped
    assert lcu_data.sync_history(client, st) == 0  # already stored
    assert st.champ_record(role="support") == {89: (1, 1), 412: (1, 0)}
    (d,) = st.drafts()
    assert d["game_id"] == 1 and d["result"] == "win"


def test_role_from_timeline():
    assert lcu_data.role_from_timeline({"lane": "BOTTOM", "role": "SUPPORT"}) == "support"
    assert lcu_data.role_from_timeline({"lane": "BOTTOM", "role": "CARRY"}) == "bottom"
    assert lcu_data.role_from_timeline({"lane": "MIDDLE", "role": "SOLO"}) == "middle"
    assert lcu_data.role_from_timeline({"lane": "NONE", "role": "NONE"}) == ""
